using System.Collections.Generic;
using System.Collections.Concurrent;
using System.Collections;
using System;
using System.IO;
using System.Reflection;
using System.Reflection.Emit;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using ModLoader;
using Newtonsoft.Json.Linq;
using SFS.UI.ModGUI;
using UnityEngine;
using UnityEngine.UI;

namespace cdnui
{
    public class Main : Mod
    {
        // Host a local WebSocket endpoint so the Python designer can attach in realtime.

        private readonly LocalWebSocketServer _server = new LocalWebSocketServer("127.0.0.1", 18650);
        private readonly ConcurrentQueue<string> _pendingSnapshotPayloads = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<bool> _pendingPreviewVisibilityCommands = new ConcurrentQueue<bool>();
        private readonly ConcurrentQueue<GeneratedUiTestRequest> _pendingGeneratedUiTests = new ConcurrentQueue<GeneratedUiTestRequest>();
        private readonly Dictionary<string, RectTransform> _resolvedNodeRects = new Dictionary<string, RectTransform>();
        private readonly Dictionary<string, RenderedNodeContract> _resolvedNodeContracts = new Dictionary<string, RenderedNodeContract>();
        private readonly List<RectTransform> _windowRects = new List<RectTransform>();
        private GameObject? _previewHolder;
        private string? _lastSnapshotPayload;
        private bool _previewConnected = true;
        private BridgeRuntime? _runtime;
        private float _nextPreviewFrameTime;
        private const float PreviewFrameIntervalSeconds = 0.35f;
        private const string GeneratedDslContractStartMarker = "UI-MAKER-DSL-CONTRACT-START";
        private const string GeneratedDslContractEndMarker = "UI-MAKER-DSL-CONTRACT-END";

        public override string ModNameID => "sfs-ui-cdn";
        public override string DisplayName => "sfs-ui-cdn";
        public override string Author => "Cratior";
        public override string MinimumGameVersionNecessary => "1.5.10";
        public override string ModVersion => "0.0.1";
        public override string Description => "Used for ui creator";

        public override Dictionary<string, string> Dependencies => new Dictionary<string, string>
        {

        };

        public override void Early_Load()
        {
            base.Early_Load();
            _runtime = BridgeRuntime.Create(this);
            _server.Start(
                payload => _pendingSnapshotPayloads.Enqueue(payload),
                connected => _pendingPreviewVisibilityCommands.Enqueue(connected),
                request => _pendingGeneratedUiTests.Enqueue(request)
            );
        }

        public override void Load()
        {
            base.Load();
        }

        private void ProcessPendingSnapshots()
        {
            // Apply queued snapshot updates on Unity's main thread.

            while (_pendingPreviewVisibilityCommands.TryDequeue(out var connected))
            {
                try
                {
                    SetPreviewConnected(connected);
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[sfs-ui-cdn] Preview visibility update failed: {ex.Message}");
                }
            }

            while (_pendingSnapshotPayloads.TryDequeue(out var payload))
            {
                try
                {
                    if (_previewConnected)
                        RenderSnapshot(payload);
                    else
                        _lastSnapshotPayload = payload;
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[sfs-ui-cdn] Snapshot render failed: {ex.Message}");
                }
            }

            while (_pendingGeneratedUiTests.TryDequeue(out var request))
            {
                try
                {
                    ExecuteGeneratedUiTest(request);
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[sfs-ui-cdn] Generated UI test failed: {ex.Message}");
                    SendGeneratedTestResult(request.RequestId, "runtime-error", ex.Message);
                }
            }
        }

        private void ExecuteGeneratedUiTest(GeneratedUiTestRequest request)
        {
            // Execute generated C# by interpreting exporter contract payload without reload.

            if (string.IsNullOrWhiteSpace(request.Code))
            {
                SendGeneratedTestResult(request.RequestId, "invalid-request", "Generated C# code payload is empty.");
                return;
            }

            if (TryRenderGeneratedSourceSnapshot(request.Code, request.EntryType, out var interpretedMessage))
            {
                SendGeneratedTestResult(request.RequestId, "success", interpretedMessage);
                return;
            }

            SendGeneratedTestResult(
                request.RequestId,
                "unsupported",
                interpretedMessage
            );
        }

        private bool TryRenderGeneratedSourceSnapshot(string sourceCode, string entryTypeName, out string message)
        {
            // Interpret generated source by extracting the embedded DSL line contract.

            message = string.Empty;

            if (!TryExtractGeneratedDslContract(sourceCode, out var contractText, out message))
                return false;

            if (!TryParseGeneratedDslContract(contractText, out var snapshot, out message))
                return false;

            if (snapshot["roots"] is not JArray roots)
            {
                message = "Generated DSL contract did not produce a roots array.";
                return false;
            }

            RenderSnapshot(snapshot.ToString(Newtonsoft.Json.Formatting.None));
            message = $"Rendered generated layout from DSL contract for {CountSnapshotNodes(roots)} node(s).";
            return true;
        }

        private static bool TryExtractGeneratedDslContract(string sourceCode, out string contractText, out string message)
        {
            // Pull DSL contract lines between explicit exporter markers so manual edits stay parse-safe.

            contractText = string.Empty;
            message = string.Empty;

            var startIndex = sourceCode.IndexOf(GeneratedDslContractStartMarker, StringComparison.Ordinal);
            if (startIndex < 0)
            {
                message = "Generated source is contractless and cannot be interpreted by this legacy runtime test parser. Use the generated file directly in a mod project and call GeneratedLayout.Render(...).";
                return false;
            }

            startIndex += GeneratedDslContractStartMarker.Length;
            var endIndex = sourceCode.IndexOf(GeneratedDslContractEndMarker, startIndex, StringComparison.Ordinal);
            if (endIndex < 0)
            {
                message = $"Generated source is missing marker: {GeneratedDslContractEndMarker}.";
                return false;
            }

            contractText = sourceCode.Substring(startIndex, endIndex - startIndex).Trim();
            if (contractText.Length == 0)
            {
                message = "Generated DSL contract payload is empty.";
                return false;
            }

            return true;
        }

        private static bool TryParseGeneratedDslContract(string contractText, out JObject snapshot, out string message)
        {
            // Parse strict contract lines and rebuild snapshot payload used by RenderSnapshot.

            snapshot = new JObject();
            message = string.Empty;

            var entries = new List<ContractNodeEntry>();
            var schemaVersion = "1.0.0";
            var versionSeen = false;

            var lines = contractText
                .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);

            for (var i = 0; i < lines.Length; i++)
            {
                var line = lines[i].Trim();
                if (line.Length == 0)
                    continue;

                if (line.StartsWith("VERSION|", StringComparison.Ordinal))
                {
                    var parts = line.Split('|');
                    if (parts.Length != 2)
                    {
                        message = "Invalid VERSION line in DSL contract.";
                        return false;
                    }

                    if (!string.Equals(parts[1], "1", StringComparison.Ordinal))
                    {
                        message = $"Unsupported DSL contract version: {parts[1]}.";
                        return false;
                    }

                    versionSeen = true;
                    continue;
                }

                if (line.StartsWith("SCHEMA|", StringComparison.Ordinal))
                {
                    var parts = line.Split('|');
                    if (parts.Length != 2)
                    {
                        message = "Invalid SCHEMA line in DSL contract.";
                        return false;
                    }

                    if (!TryDecodeBase64(parts[1], out schemaVersion))
                    {
                        message = "SCHEMA line contains invalid base64 payload.";
                        return false;
                    }

                    continue;
                }

                if (line.StartsWith("NODE|", StringComparison.Ordinal))
                {
                    if (!TryParseContractNodeLine(line, out var entry, out message))
                        return false;

                    entries.Add(entry);
                    continue;
                }

                message = $"Unsupported DSL contract line: {line}";
                return false;
            }

            if (!versionSeen)
            {
                message = "DSL contract is missing VERSION line.";
                return false;
            }

            if (entries.Count == 0)
            {
                message = "DSL contract does not contain any NODE entries.";
                return false;
            }

            var byId = new Dictionary<string, JObject>(StringComparer.Ordinal);
            var byParent = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            var rootIds = new List<string>();

            for (var i = 0; i < entries.Count; i++)
            {
                var entry = entries[i];
                if (byId.ContainsKey(entry.Id))
                {
                    message = $"DSL contract contains duplicate node id: {entry.Id}.";
                    return false;
                }

                byId[entry.Id] = new JObject
                {
                    ["id"] = entry.Id,
                    ["type"] = entry.Type,
                    ["name"] = entry.Name,
                    ["x"] = entry.X,
                    ["y"] = entry.Y,
                    ["width"] = entry.Width,
                    ["height"] = entry.Height,
                    ["text"] = entry.Text,
                    ["text_alignment"] = entry.TextAlignment,
                    ["text_color_override"] = entry.TextColorOverride,
                    ["text_color"] = entry.TextColor,
                    ["background_color_override"] = entry.BackgroundColorOverride,
                    ["background_color"] = entry.BackgroundColor,
                    ["border_color"] = entry.BorderColor,
                    ["multiline"] = entry.Multiline,
                    ["layout"] = entry.Layout,
                    ["child_alignment"] = entry.ChildAlignment,
                    ["spacing"] = entry.Spacing,
                    ["padding"] = entry.PaddingLeft,
                    ["padding_left"] = entry.PaddingLeft,
                    ["padding_right"] = entry.PaddingRight,
                    ["padding_top"] = entry.PaddingTop,
                    ["padding_bottom"] = entry.PaddingBottom,
                    ["width_mode"] = entry.WidthMode,
                    ["height_mode"] = entry.HeightMode,
                    ["scroll_vertical"] = entry.ScrollVertical,
                    ["scroll_horizontal"] = entry.ScrollHorizontal,
                    ["props"] = entry.Props,
                    ["children"] = new JArray(),
                };

                if (entry.ParentId.Length == 0)
                    rootIds.Add(entry.Id);
                else
                {
                    if (!byParent.TryGetValue(entry.ParentId, out var children))
                    {
                        children = new List<string>();
                        byParent[entry.ParentId] = children;
                    }

                    children.Add(entry.Id);
                }
            }

            foreach (var pair in byParent)
            {
                if (!byId.TryGetValue(pair.Key, out var parentNode))
                {
                    message = $"DSL contract references missing parent id: {pair.Key}.";
                    return false;
                }

                var childArray = parentNode["children"] as JArray;
                if (childArray == null)
                {
                    message = $"Parent node missing children array: {pair.Key}.";
                    return false;
                }

                for (var i = 0; i < pair.Value.Count; i++)
                {
                    var childId = pair.Value[i];
                    if (!byId.TryGetValue(childId, out var childNode))
                    {
                        message = $"DSL contract references missing child id: {childId}.";
                        return false;
                    }

                    childArray.Add(childNode);
                }
            }

            var roots = new JArray();
            for (var i = 0; i < rootIds.Count; i++)
            {
                var rootId = rootIds[i];
                if (!byId.TryGetValue(rootId, out var rootNode))
                {
                    message = $"DSL contract references missing root id: {rootId}.";
                    return false;
                }

                roots.Add(rootNode);
            }

            snapshot = new JObject
            {
                ["schemaVersion"] = schemaVersion,
                ["roots"] = roots,
            };
            return true;
        }

        private static bool TryParseContractNodeLine(string line, out ContractNodeEntry entry, out string message)
        {
            // Parse one strict NODE line from the contract payload.

            entry = new ContractNodeEntry();
            message = string.Empty;

            var parts = line.Split('|');
            if (parts.Length != 29)
            {
                message = $"Invalid NODE line field count ({parts.Length}); expected 29.";
                return false;
            }

            if (!TryDecodeBase64(parts[1], out var id) || id.Length == 0)
            {
                message = "NODE line has invalid id field.";
                return false;
            }

            if (!TryDecodeBase64(parts[2], out var parentId))
            {
                message = "NODE line has invalid parent id field.";
                return false;
            }

            if (!TryDecodeBase64(parts[3], out var type) || type.Length == 0)
            {
                message = "NODE line has invalid type field.";
                return false;
            }

            if (!TryDecodeBase64(parts[4], out var name) || name.Length == 0)
            {
                message = "NODE line has invalid name field.";
                return false;
            }

            if (!int.TryParse(parts[5], out var x)
                || !int.TryParse(parts[6], out var y)
                || !int.TryParse(parts[7], out var width)
                || !int.TryParse(parts[8], out var height)
                || !int.TryParse(parts[19], out var spacing)
                || !int.TryParse(parts[20], out var paddingLeft)
                || !int.TryParse(parts[21], out var paddingRight)
                || !int.TryParse(parts[22], out var paddingTop)
                || !int.TryParse(parts[23], out var paddingBottom))
            {
                message = "NODE line contains invalid numeric fields.";
                return false;
            }

            if (!TryDecodeBase64(parts[9], out var text)
                || !TryDecodeBase64(parts[10], out var textAlignment)
                || !TryDecodeBase64(parts[12], out var textColor)
                || !TryDecodeBase64(parts[14], out var backgroundColor)
                || !TryDecodeBase64(parts[15], out var borderColor)
                || !TryDecodeBase64(parts[17], out var layout)
                || !TryDecodeBase64(parts[18], out var childAlignment)
                || !TryDecodeBase64(parts[28], out var propsJson))
            {
                message = "NODE line contains invalid base64 text fields.";
                return false;
            }

            if (!bool.TryParse(parts[11], out var textColorOverride)
                || !bool.TryParse(parts[13], out var backgroundColorOverride)
                || !bool.TryParse(parts[16], out var multiline)
                || !bool.TryParse(parts[26], out var scrollVertical)
                || !bool.TryParse(parts[27], out var scrollHorizontal))
            {
                message = "NODE line contains invalid boolean fields.";
                return false;
            }

            if (!TryDecodeBase64(parts[24], out var widthMode) || widthMode.Length == 0)
            {
                message = "NODE line has invalid width mode field.";
                return false;
            }

            if (!TryDecodeBase64(parts[25], out var heightMode) || heightMode.Length == 0)
            {
                message = "NODE line has invalid height mode field.";
                return false;
            }

            JObject props;
            try
            {
                var parsed = JObject.Parse(propsJson);
                props = parsed;
            }
            catch (Exception ex)
            {
                message = $"NODE line has invalid props JSON: {ex.Message}";
                return false;
            }

            entry = new ContractNodeEntry
            {
                Id = id,
                ParentId = parentId,
                Type = type,
                Name = name,
                X = x,
                Y = y,
                Width = width,
                Height = height,
                Text = text,
                TextAlignment = textAlignment,
                TextColorOverride = textColorOverride,
                TextColor = textColor,
                BackgroundColorOverride = backgroundColorOverride,
                BackgroundColor = backgroundColor,
                BorderColor = borderColor,
                Multiline = multiline,
                Layout = layout,
                ChildAlignment = childAlignment,
                Spacing = spacing,
                PaddingLeft = paddingLeft,
                PaddingRight = paddingRight,
                PaddingTop = paddingTop,
                PaddingBottom = paddingBottom,
                WidthMode = widthMode,
                HeightMode = heightMode,
                ScrollVertical = scrollVertical,
                ScrollHorizontal = scrollHorizontal,
                Props = props,
            };
            return true;
        }

        private static bool TryDecodeBase64(string input, out string value)
        {
            // Decode base64 contract segments and fail explicitly for malformed values.

            value = string.Empty;
            try
            {
                var bytes = Convert.FromBase64String(input);
                value = Encoding.UTF8.GetString(bytes);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static int CountSnapshotNodes(JArray roots)
        {
            // Count rendered nodes for explicit generated-test feedback.

            var total = 0;

            void Walk(JToken token)
            {
                if (token is not JObject node)
                    return;

                total += 1;
                if (node["children"] is not JArray children)
                    return;

                for (var i = 0; i < children.Count; i++)
                    Walk(children[i]);
            }

            for (var i = 0; i < roots.Count; i++)
                Walk(roots[i]);

            return total;
        }

        private static System.Type? FindLoadedType(string fullTypeName)
        {
            // Locate a generated type from currently loaded assemblies.

            var assemblies = AppDomain.CurrentDomain.GetAssemblies();
            for (var i = 0; i < assemblies.Length; i++)
            {
                var candidate = assemblies[i].GetType(fullTypeName, throwOnError: false, ignoreCase: false);
                if (candidate != null)
                    return candidate;
            }

            return null;
        }

        private static object CreateNoOpBindings(System.Type interfaceType)
        {
            // Emit a runtime interface implementation that returns default values for generated callbacks.

            var assemblyName = new AssemblyName($"GeneratedUiBindings_{Guid.NewGuid():N}");
            var assemblyBuilder = AssemblyBuilder.DefineDynamicAssembly(assemblyName, AssemblyBuilderAccess.Run);
            var moduleBuilder = assemblyBuilder.DefineDynamicModule("Main");
            var typeBuilder = moduleBuilder.DefineType(
                $"NoOpBindings_{Guid.NewGuid():N}",
                TypeAttributes.Public | TypeAttributes.Class | TypeAttributes.Sealed
            );
            typeBuilder.AddInterfaceImplementation(interfaceType);
            typeBuilder.DefineDefaultConstructor(MethodAttributes.Public);

            var methods = interfaceType.GetMethods();
            for (var i = 0; i < methods.Length; i++)
            {
                var interfaceMethod = methods[i];
                var parameterTypes = interfaceMethod.GetParameters();
                var signature = new System.Type[parameterTypes.Length];
                for (var p = 0; p < parameterTypes.Length; p++)
                    signature[p] = parameterTypes[p].ParameterType;

                var methodBuilder = typeBuilder.DefineMethod(
                    interfaceMethod.Name,
                    MethodAttributes.Public | MethodAttributes.Virtual | MethodAttributes.Final | MethodAttributes.HideBySig | MethodAttributes.NewSlot,
                    interfaceMethod.ReturnType,
                    signature
                );

                var il = methodBuilder.GetILGenerator();
                if (interfaceMethod.ReturnType == typeof(void))
                {
                    il.Emit(OpCodes.Ret);
                }
                else if (interfaceMethod.ReturnType.IsValueType)
                {
                    var local = il.DeclareLocal(interfaceMethod.ReturnType);
                    il.Emit(OpCodes.Ldloca_S, local);
                    il.Emit(OpCodes.Initobj, interfaceMethod.ReturnType);
                    il.Emit(OpCodes.Ldloc_0);
                    il.Emit(OpCodes.Ret);
                }
                else
                {
                    il.Emit(OpCodes.Ldnull);
                    il.Emit(OpCodes.Ret);
                }

                typeBuilder.DefineMethodOverride(methodBuilder, interfaceMethod);
            }

            var concreteType = typeBuilder.CreateType();
            return Activator.CreateInstance(concreteType)!;
        }

        private void SendGeneratedTestResult(string requestId, string status, string message)
        {
            // Publish deterministic generated C# test status updates to the Python client.

            var envelope = new JObject
            {
                ["type"] = "test-result",
                ["request_id"] = requestId,
                ["status"] = status,
                ["message"] = message,
                ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };
            _server.SendJson(envelope.ToString(Newtonsoft.Json.Formatting.None));
        }

        private void TrySendPreviewFrame()
        {
            // Send preview frames only when preview exists and client is connected.

            if (!_previewConnected || _previewHolder == null || !_server.HasActiveClient || Time.unscaledTime < _nextPreviewFrameTime)
                return;

            ClampPreviewWindowsToScreen();
            _nextPreviewFrameTime = Time.unscaledTime + PreviewFrameIntervalSeconds;
            SendPreviewFrame();
            SendResolvedLayoutPayload();
        }

        private void SetPreviewConnected(bool connected)
        {
            // Toggle runtime preview visibility while preserving the last known snapshot state.

            if (_previewConnected == connected)
                return;

            _previewConnected = connected;
            if (!_previewConnected)
            {
                ClearPreview();
                Debug.Log("[sfs-ui-cdn] Preview disconnected.");
                return;
            }

            Debug.Log("[sfs-ui-cdn] Preview connected.");
            if (!string.IsNullOrWhiteSpace(_lastSnapshotPayload))
                RenderSnapshot(_lastSnapshotPayload);
        }

        private void RenderSnapshot(string payloadJson)
        {
            // Rebuild live preview from the latest full snapshot payload.

            _lastSnapshotPayload = payloadJson;
            if (!_previewConnected)
                return;

            var payload = JObject.Parse(payloadJson);
            var roots = payload["roots"] as JArray ?? throw new InvalidOperationException("Snapshot payload is missing roots array.");

            ClearPreview();
            _resolvedNodeRects.Clear();
            _resolvedNodeContracts.Clear();
            _windowRects.Clear();
            _previewHolder = CreatePreviewHolder();

            var rootNameCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (var token in roots)
            {
                var node = token as JObject ?? throw new InvalidOperationException("Snapshot root node must be an object.");
                var rootName = SanitizePathSegment(GetRequiredString(node, "name"));
                var rootIndex = rootNameCounts.TryGetValue(rootName, out var existingRootIndex) ? existingRootIndex : 0;
                rootNameCounts[rootName] = rootIndex + 1;
                CreateNodeRecursive(node, _previewHolder.transform, parentId: string.Empty, parentPath: string.Empty, siblingIndex: rootIndex, siblingCount: roots.Count);
            }

            Debug.Log($"[sfs-ui-cdn] Applied snapshot with {roots.Count} root node(s).");
        }

        private void SendResolvedLayoutPayload()
        {
            // Publish authoritative post-layout element rectangles so Python can mirror exact SFS sizing.

            if (!_server.HasActiveClient || _previewHolder == null || !TryGetPreviewCaptureRect(out var captureRect))
                return;

            var nodes = new JArray();
            var skippedCount = 0;
            foreach (var pair in _resolvedNodeRects)
            {
                if (pair.Value == null || !TryGetScreenRect(pair.Value, out var screenRect))
                {
                    skippedCount++;
                    continue;
                }

                var relativeX = screenRect.x - captureRect.x;
                var relativeY = screenRect.y - captureRect.y;
                if (!_resolvedNodeContracts.TryGetValue(pair.Key, out var contract))
                    continue;

                nodes.Add(new JObject
                {
                    ["id"] = pair.Key,
                    ["parent_id"] = contract.ParentId,
                    ["path_key"] = contract.PathKey,
                    ["x"] = relativeX,
                    ["y"] = relativeY,
                    ["width"] = screenRect.width,
                    ["height"] = screenRect.height,
                    ["expected_width"] = contract.ExpectedWidth,
                    ["expected_height"] = contract.ExpectedHeight,
                    ["layout"] = contract.Layout,
                    ["child_alignment"] = contract.ChildAlignment,
                    ["spacing"] = contract.Spacing,
                    ["width_mode"] = contract.WidthMode,
                    ["height_mode"] = contract.HeightMode,
                    ["scroll_vertical"] = contract.ScrollVertical,
                    ["scroll_horizontal"] = contract.ScrollHorizontal,
                    ["text_alignment"] = contract.TextAlignment,
                });
            }
            if (skippedCount > 0)
                Debug.LogWarning($"[sfs-ui-cdn] Layout payload: skipped {skippedCount} node(s) with unavailable rects");

            var envelope = new JObject
            {
                ["type"] = "layout",
                ["capture"] = new JObject
                {
                    ["x"] = captureRect.x,
                    ["y"] = captureRect.y,
                    ["width"] = captureRect.width,
                    ["height"] = captureRect.height,
                },
                ["nodes"] = nodes,
                ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };

            _server.SendJson(envelope.ToString(Newtonsoft.Json.Formatting.None));
        }

        private static bool TryGetScreenRect(RectTransform rectTransform, out Rect rect)
        {
            // Convert a rect transform to screen-space bounds.
            // Keep bounds clamped to the screen but always return true for contract completeness.

            rect = default;
            var corners = new Vector3[4];
            rectTransform.GetWorldCorners(corners);

            var minX = Mathf.Min(corners[0].x, corners[2].x);
            var minY = Mathf.Min(corners[0].y, corners[2].y);
            var maxX = Mathf.Max(corners[0].x, corners[2].x);
            var maxY = Mathf.Max(corners[0].y, corners[2].y);

            var x = Mathf.Clamp(minX, 0f, Screen.width - 1f);
            var y = Mathf.Clamp(minY, 0f, Screen.height - 1f);
            var width = Mathf.Clamp(maxX - x, 1f, Screen.width - x);
            var height = Mathf.Clamp(maxY - y, 1f, Screen.height - y);

            rect = new Rect(x, y, width, height);
            return true;
        }

        private GameObject CreatePreviewHolder()
        {
            // Create canvas-attached holder for rendered preview content.

            var holder = Builder.CreateHolder(Builder.SceneToAttach.CurrentScene, "ui-maker-live-preview");
            if (holder.transform.parent != null)
                return holder;

            UnityEngine.Object.Destroy(holder);

            holder = Builder.CreateHolder(Builder.SceneToAttach.BaseScene, "ui-maker-live-preview");
            if (holder.transform.parent != null)
                return holder;

            UnityEngine.Object.Destroy(holder);
            throw new InvalidOperationException("Could not attach preview holder to game UI canvas.");
        }

        private void SendPreviewFrame()
        {
            // Capture only the rendered preview window region and push PNG preview to Python.

            if (!TryGetPreviewCaptureRect(out var captureRect))
            {
                Debug.LogWarning("[sfs-ui-cdn] Preview frame skipped: capture rect unavailable");
                return;
            }

            var texture = new Texture2D((int)captureRect.width, (int)captureRect.height, TextureFormat.RGB24, false);

            try
            {
                texture.ReadPixels(captureRect, 0, 0, false);
                texture.Apply(false, false);
                var png = texture.EncodeToPNG();
                var envelope = new JObject
                {
                    ["type"] = "frame",
                    ["mime"] = "image/png",
                    ["data"] = Convert.ToBase64String(png),
                    ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                };
                _server.SendJson(envelope.ToString(Newtonsoft.Json.Formatting.None));
            }
            catch (Exception ex)
            {
                Debug.LogError($"[sfs-ui-cdn] Preview frame encode failed: {ex.Message}");
            }
            finally
            {
                UnityEngine.Object.Destroy(texture);
            }
        }

        private bool TryGetPreviewCaptureRect(out Rect rect)
        {
            // Resolve screen-space capture rect from the first rendered preview window.

            rect = default;
            if (_previewHolder == null || _previewHolder.transform.childCount == 0)
                return false;

            var firstChild = _previewHolder.transform.GetChild(0);
            var rectTransform = firstChild as RectTransform;
            if (rectTransform == null)
                return false;

            var corners = new Vector3[4];
            rectTransform.GetWorldCorners(corners);

            var minX = corners[0].x;
            var minY = corners[0].y;
            var maxX = corners[2].x;
            var maxY = corners[2].y;

            var x = Mathf.Clamp(minX, 0f, Screen.width - 1f);
            var y = Mathf.Clamp(minY, 0f, Screen.height - 1f);
            var width = Mathf.Clamp(maxX - x, 1f, Screen.width - x);
            var height = Mathf.Clamp(maxY - y, 1f, Screen.height - y);

            rect = new Rect(x, y, width, height);
            return true;
        }

        private void ClearPreview()
        {
            // Destroy previous preview holder and all rendered descendants.

            if (_previewHolder == null)
                return;

            UnityEngine.Object.Destroy(_previewHolder);
            _previewHolder = null;
            _resolvedNodeRects.Clear();
            _resolvedNodeContracts.Clear();
            _windowRects.Clear();
        }

        private void CreateNodeRecursive(JObject node, Transform parent, string parentId, string parentPath, int siblingIndex, int siblingCount)
        {
            // Render one node and recurse with layout-group-based positioning rules.

            var id = GetRequiredString(node, "id");
            var type = GetRequiredString(node, "type");
            var x = GetOptionalInt(node, "x", 0);
            var y = GetOptionalInt(node, "y", 0);
            var width = Mathf.Clamp(GetRequiredInt(node, "width"), 1, 50000);
            var height = Mathf.Clamp(GetRequiredInt(node, "height"), 1, 50000);
            var text = GetRequiredString(node, "text");
            var nodeName = GetRequiredString(node, "name");
            var renderedText = string.IsNullOrWhiteSpace(text) ? nodeName : text;
            var pathSegment = SanitizePathSegment(nodeName);
            var pathKey = string.IsNullOrWhiteSpace(parentPath)
                ? $"{pathSegment}[{Mathf.Max(0, siblingIndex)}]"
                : $"{parentPath}/{pathSegment}[{Mathf.Max(0, siblingIndex)}]";
            var layoutDirection = GetOptionalString(node, "layout", "Vertical");
            var childAlignment = GetOptionalString(node, "child_alignment", "UpperLeft");
            var spacing = GetOptionalInt(node, "spacing", 12);
            var legacyPadding = GetOptionalInt(node, "padding", 12);
            var paddingLeft = GetOptionalInt(node, "padding_left", legacyPadding);
            var paddingRight = GetOptionalInt(node, "padding_right", legacyPadding);
            var paddingTop = GetOptionalInt(node, "padding_top", legacyPadding);
            var paddingBottom = GetOptionalInt(node, "padding_bottom", legacyPadding);
            var widthMode = NormalizeSizeMode(GetOptionalString(node, "width_mode", LegacySizeModeFromFullFlag(GetOptionalBool(node, "full_width", true))), "width");
            var heightMode = NormalizeSizeMode(GetOptionalString(node, "height_mode", LegacySizeModeFromFullFlag(GetOptionalBool(node, "full_height", false))), "height");
            var scrollVertical = GetOptionalBool(node, "scroll_vertical", false);
            var scrollHorizontal = GetOptionalBool(node, "scroll_horizontal", false);
            var textAlignment = GetOptionalString(node, "text_alignment", "Left");
            var multiline = GetOptionalBool(node, "multiline", false);
            var textColor = GetOptionalString(node, "text_color", string.Empty);
            var textColorOverride = node["text_color_override"] == null
                ? !string.IsNullOrWhiteSpace(textColor) && !string.Equals(textColor, "#ffffff", StringComparison.OrdinalIgnoreCase)
                : GetOptionalBool(node, "text_color_override", false);
            var backgroundColor = GetOptionalString(node, "background_color", string.Empty);
            var backgroundColorOverride = node["background_color_override"] == null
                ? !string.IsNullOrWhiteSpace(backgroundColor) && !string.Equals(GetOptionalString(node, "background_style", "Image"), "Image", StringComparison.OrdinalIgnoreCase)
                : GetOptionalBool(node, "background_color_override", false);
            var borderColor = GetOptionalString(node, "border_color", string.Empty);
            var children = node["children"] as JArray ?? throw new InvalidOperationException($"Node {id} is missing children array.");
            var resolvedWidth = ResolveNodeAxisSize(parent, type, isWidthAxis: true, widthMode, width, siblingCount);
            var resolvedHeight = ResolveNodeAxisSize(parent, type, isWidthAxis: false, heightMode, height, siblingCount);

            Transform childParent;
            Transform renderedElement;
            object renderedInstance;

            switch (type)
            {
                case "Window":
                {
                    var window = Builder.CreateWindow(parent, DeterministicId(id), resolvedWidth, resolvedHeight, x, y, draggable: false, savePosition: false, titleText: renderedText);

                    var layoutType = ParseLayoutDirection(layoutDirection);
                    window.CreateLayoutGroup(layoutType, childAlignment: ParseTextAnchor(childAlignment), spacing: spacing, padding: new RectOffset(paddingLeft, paddingRight, paddingTop, paddingBottom), disableChildSizeControl: true);
                    DisableContentSizeFitter(window.ChildrenHolder);
                    ApplyScrolling(window.ChildrenHolder, scrollVertical, scrollHorizontal);
                    childParent = window;
                    renderedElement = window.gameObject.transform;
                    renderedInstance = window;
                    if (window.rectTransform != null)
                        _windowRects.Add(window.rectTransform);
                    break;
                }
                case "Container":
                {
                    var container = Builder.CreateContainer(parent, x, y);
                    container.Size = new Vector2(resolvedWidth, resolvedHeight);
                    DisableContentSizeFitter(container.gameObject.transform);
                    container.CreateLayoutGroup(ParseLayoutDirection(layoutDirection), ParseTextAnchor(childAlignment), spacing, new RectOffset(paddingLeft, paddingRight, paddingTop, paddingBottom), true);
                    ApplyScrolling(container.gameObject.transform, scrollVertical, scrollHorizontal);
                    childParent = container.gameObject.transform;
                    renderedElement = container.gameObject.transform;
                    renderedInstance = container;
                    break;
                }
                case "Box":
                {
                    var box = Builder.CreateBox(parent, resolvedWidth, resolvedHeight, x, y, opacity: 0.35f);
                    DisableContentSizeFitter(box.gameObject.transform);
                    box.CreateLayoutGroup(ParseLayoutDirection(layoutDirection), ParseTextAnchor(childAlignment), spacing, new RectOffset(paddingLeft, paddingRight, paddingTop, paddingBottom), true);
                    ApplyScrolling(box.gameObject.transform, scrollVertical, scrollHorizontal);
                    childParent = box.gameObject.transform;
                    renderedElement = box.gameObject.transform;
                    renderedInstance = box;
                    break;
                }
                case "Label":
                {
                    var label = Builder.CreateLabel(parent, resolvedWidth, resolvedHeight, x, y, renderedText);
                    childParent = label.gameObject.transform;
                    renderedElement = label.gameObject.transform;
                    renderedInstance = label;
                    break;
                }
                case "Button":
                {
                    var button = Builder.CreateButton(parent, resolvedWidth, resolvedHeight, x, y, null, renderedText);
                    childParent = button.gameObject.transform;
                    renderedElement = button.gameObject.transform;
                    renderedInstance = button;
                    break;
                }
                case "TextInput":
                {
                    var input = Builder.CreateTextInput(parent, resolvedWidth, resolvedHeight, x, y, renderedText, null);
                    childParent = input.gameObject.transform;
                    renderedElement = input.gameObject.transform;
                    renderedInstance = input;
                    break;
                }
                case "Toggle":
                {
                    var localState = false;
                    var toggle = Builder.CreateToggle(parent, () => localState, x, y, () => localState = !localState);
                    childParent = toggle.gameObject.transform;
                    renderedElement = toggle.gameObject.transform;
                    renderedInstance = toggle;
                    break;
                }
                case "Slider":
                {
                    var minValue = 0f;
                    var maxValue = 1f;
                    var value = minValue;
                    var wholeNumbers = false;

                    var slider = Builder.CreateSlider(parent, resolvedWidth, value, (minValue, maxValue), wholeNumbers: wholeNumbers);
                    slider.SliderType = UnityEngine.UI.Slider.Direction.LeftToRight;
                    childParent = slider.gameObject.transform;
                    renderedElement = slider.gameObject.transform;
                    renderedInstance = slider;
                    break;
                }
                case "Separator":
                {
                    var separator = Builder.CreateSeparator(parent, resolvedWidth, x, y);
                    childParent = separator.gameObject.transform;
                    renderedElement = separator.gameObject.transform;
                    renderedInstance = separator;
                    break;
                }
                case "Space":
                {
                    var space = Builder.CreateSpace(parent, resolvedWidth, resolvedHeight);
                    childParent = space.gameObject.transform;
                    renderedElement = space.gameObject.transform;
                    renderedInstance = space;
                    break;
                }
                default:
                    throw new InvalidOperationException($"Unsupported node type in snapshot: {type}");
            }

            ApplyVisualStyle(renderedInstance, renderedElement, type, textAlignment, multiline, textColorOverride, textColor, backgroundColorOverride, backgroundColor, borderColor);

            if (renderedElement is RectTransform elementRect)
                _resolvedNodeRects[id] = elementRect;

            _resolvedNodeContracts[id] = new RenderedNodeContract
            {
                ParentId = parentId,
                PathKey = pathKey,
                ExpectedWidth = width,
                ExpectedHeight = height,
                Layout = layoutDirection,
                ChildAlignment = childAlignment,
                Spacing = spacing,
                WidthMode = widthMode,
                HeightMode = heightMode,
                ScrollVertical = scrollVertical,
                ScrollHorizontal = scrollHorizontal,
                TextAlignment = textAlignment,
            };

            var childNameCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var childSiblingCount = children.Count;
            foreach (var childToken in children)
            {
                var childNode = childToken as JObject ?? throw new InvalidOperationException($"Node {id} contains non-object child entry.");
                var childName = SanitizePathSegment(GetRequiredString(childNode, "name"));
                var childIndex = childNameCounts.TryGetValue(childName, out var existingChildIndex) ? existingChildIndex : 0;
                childNameCounts[childName] = childIndex + 1;
                CreateNodeRecursive(childNode, childParent, parentId: id, parentPath: pathKey, siblingIndex: childIndex, siblingCount: childSiblingCount);
            }
        }

        private static string SanitizePathSegment(string value)
        {
            // Normalize names into stable path segments used in markdown/runtime contract keys.

            var trimmed = (value ?? string.Empty).Trim();
            if (trimmed.Length == 0)
                return "Node";

            var builder = new StringBuilder(trimmed.Length);
            for (var i = 0; i < trimmed.Length; i++)
            {
                var ch = trimmed[i];
                if (char.IsLetterOrDigit(ch) || ch == '_' || ch == '-')
                {
                    builder.Append(ch);
                    continue;
                }

                if (char.IsWhiteSpace(ch) || ch == '/' || ch == '\\')
                    builder.Append('_');
                else
                    builder.Append('_');
            }

            var normalized = builder.ToString().Trim('_');
            return normalized.Length == 0 ? "Node" : normalized;
        }

        private static string LegacySizeModeFromFullFlag(bool fullFlag)
        {
            // Keep legacy full-size booleans compatible with explicit axis sizing modes.

            return fullFlag ? "Auto" : "Manual";
        }

        private static string NormalizeSizeMode(string value, string axisLabel)
        {
            // Validate incoming mode names so malformed payloads fail clearly.

            var normalized = (value ?? string.Empty).Trim();
            if (string.Equals(normalized, "NativeAuto", StringComparison.Ordinal)
                || string.Equals(normalized, "UseParentSize", StringComparison.Ordinal))
                return "Auto";

            if (string.Equals(normalized, "Manual", StringComparison.Ordinal)
                || string.Equals(normalized, "Auto", StringComparison.Ordinal))
                return normalized;

            throw new InvalidOperationException($"Unsupported {axisLabel} sizing mode: {value}");
        }

        private static int ResolveNodeAxisSize(Transform parent, string elementType, bool isWidthAxis, string axisMode, int manualSize, int siblingCount)
        {
            // Resolve concrete axis dimensions using manual or auto layout strategy.

            var clampedManual = Mathf.Clamp(manualSize, 1, 50000);
            if (string.Equals(axisMode, "Manual", StringComparison.Ordinal))
                return clampedManual;

            var parentRect = parent as RectTransform;
            if (parentRect == null)
                return clampedManual;

            var available = isWidthAxis ? parentRect.rect.width : parentRect.rect.height;
            var layoutGroup = parent.GetComponent<HorizontalOrVerticalLayoutGroup>();
            if (layoutGroup != null)
            {
                var axisPadding = isWidthAxis
                    ? layoutGroup.padding.left + layoutGroup.padding.right
                    : layoutGroup.padding.top + layoutGroup.padding.bottom;
                available = Mathf.Max(1f, available - axisPadding);

                var effectiveSiblingCount = Mathf.Max(1, siblingCount);
                var totalSpacing = layoutGroup.spacing * Mathf.Max(0, siblingCount - 1);
                var isPrimaryAxis = (isWidthAxis && layoutGroup is HorizontalLayoutGroup)
                    || (!isWidthAxis && layoutGroup is VerticalLayoutGroup);
                if (isPrimaryAxis)
                    available = Mathf.Max(1f, (available - totalSpacing) / effectiveSiblingCount);
            }

            return Mathf.Clamp(Mathf.RoundToInt(available), 1, 50000);
        }

        private void ClampPreviewWindowsToScreen()
        {
            // Keep preview windows visible without changing gameplay cameras or systems.

            if (!_previewConnected)
                return;

            for (var i = 0; i < _windowRects.Count; i++)
            {
                var rect = _windowRects[i];
                if (rect == null)
                    continue;

                ClampRectTransformToScreen(rect);
            }
        }

        private static void ClampRectTransformToScreen(RectTransform rectTransform)
        {
            // Clamp window world position into visible screen bounds.

            var corners = new Vector3[4];
            rectTransform.GetWorldCorners(corners);

            var minX = Mathf.Min(corners[0].x, corners[2].x);
            var minY = Mathf.Min(corners[0].y, corners[2].y);
            var maxX = Mathf.Max(corners[0].x, corners[2].x);
            var maxY = Mathf.Max(corners[0].y, corners[2].y);

            var deltaX = 0f;
            var deltaY = 0f;
            if (minX < 0f)
                deltaX = -minX;
            else if (maxX > Screen.width)
                deltaX = Screen.width - maxX;

            if (minY < 0f)
                deltaY = -minY;
            else if (maxY > Screen.height)
                deltaY = Screen.height - maxY;

            if (Mathf.Abs(deltaX) < 0.01f && Mathf.Abs(deltaY) < 0.01f)
                return;

            rectTransform.position += new Vector3(deltaX, deltaY, 0f);
        }

        private static SFS.UI.ModGUI.Type ParseLayoutDirection(string value)
        {
            // Parse layout direction with strict known values.

            return string.Equals(value, "Horizontal", StringComparison.OrdinalIgnoreCase)
                ? SFS.UI.ModGUI.Type.Horizontal
                : SFS.UI.ModGUI.Type.Vertical;
        }

        private static int GetOptionalInt(JObject node, string field, int defaultValue)
        {
            // Read optional integer fields while preserving explicit default behavior.

            var token = node[field];
            if (token == null)
                return defaultValue;

            if (token.Type == JTokenType.Integer)
                return token.Value<int>();

            return int.TryParse(token.ToString(), out var parsed) ? parsed : defaultValue;
        }

        private static bool GetOptionalBool(JObject node, string field, bool defaultValue)
        {
            // Read optional boolean fields while preserving explicit defaults.

            var token = node[field];
            if (token == null)
                return defaultValue;

            if (token.Type == JTokenType.Boolean)
                return token.Value<bool>();

            return bool.TryParse(token.ToString(), out var parsed) ? parsed : defaultValue;
        }

        private static float GetOptionalFloat(JObject node, string field, float defaultValue)
        {
            // Read optional floating-point fields while preserving explicit defaults.

            var token = node[field];
            if (token == null)
                return defaultValue;

            if (token.Type == JTokenType.Float || token.Type == JTokenType.Integer)
                return token.Value<float>();

            return float.TryParse(token.ToString(), out var parsed) ? parsed : defaultValue;
        }

        private static string GetOptionalString(JObject node, string field, string defaultValue)
        {
            // Read optional string fields with safe default fallback.

            var token = node[field];
            if (token == null)
                return defaultValue;

            return token.Type == JTokenType.String ? token.Value<string>() ?? defaultValue : token.ToString();
        }

        private static void DisableContentSizeFitter(Transform transform)
        {
            // Prevent fitters from collapsing dynamic full-size layout elements.

            var fitter = transform.GetComponent<ContentSizeFitter>();
            if (fitter == null)
                return;

            fitter.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            fitter.verticalFit = ContentSizeFitter.FitMode.Unconstrained;
            fitter.enabled = false;
        }

        private static void ApplyScrolling(Transform transform, bool vertical, bool horizontal)
        {
            // Enable scrolling when the target supports SFS scroll behavior.

            var scrollElement = transform.GetComponent("ScrollElement");
            if (scrollElement != null)
            {
                TrySetBoolMember(scrollElement, "vertical", vertical);
                TrySetBoolMember(scrollElement, "horizontal", horizontal);
                TrySetBoolMember(scrollElement, "Vertical", vertical);
                TrySetBoolMember(scrollElement, "Horizontal", horizontal);
                TrySetBoolMember(scrollElement, "scrollVertical", vertical);
                TrySetBoolMember(scrollElement, "scrollHorizontal", horizontal);
            }

            var scrollRect = transform.GetComponent<ScrollRect>() ?? transform.GetComponentInChildren<ScrollRect>(true);
            if (scrollRect != null)
            {
                scrollRect.vertical = vertical;
                scrollRect.horizontal = horizontal;
                scrollRect.inertia = true;
                scrollRect.movementType = ScrollRect.MovementType.Clamped;
                scrollRect.enabled = vertical || horizontal;
            }

            if (vertical || horizontal)
            {
                var mask = transform.GetComponentInChildren<Mask>(true);
                if (mask != null)
                    mask.enabled = true;

                var rectMask = transform.GetComponentInChildren<RectMask2D>(true);
                if (rectMask != null)
                    rectMask.enabled = true;
            }
        }

        private static void TrySetBoolMember(object target, string memberName, bool value)
        {
            // Set bool fields/properties case-insensitively to support runtime API shape differences.

            var type = target.GetType();
            var field = type.GetField(memberName, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.IgnoreCase);
            if (field != null && field.FieldType == typeof(bool))
            {
                field.SetValue(target, value);
                return;
            }

            var property = type.GetProperty(memberName, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.IgnoreCase);
            if (property != null && property.CanWrite && property.PropertyType == typeof(bool))
                property.SetValue(target, value, null);
        }

        private static TextAnchor ParseTextAnchor(string value)
        {
            // Parse layout alignment names used by the Python editor.

            return NormalizeAlignmentName(value) switch
            {
                "UpperCenter" => TextAnchor.UpperCenter,
                "UpperRight" => TextAnchor.UpperRight,
                "MiddleLeft" => TextAnchor.MiddleLeft,
                "MiddleCenter" => TextAnchor.MiddleCenter,
                "MiddleRight" => TextAnchor.MiddleRight,
                "LowerLeft" => TextAnchor.LowerLeft,
                "LowerCenter" => TextAnchor.LowerCenter,
                "LowerRight" => TextAnchor.LowerRight,
                _ => TextAnchor.UpperLeft,
            };
        }

        private static string NormalizeAlignmentName(string value)
        {
            // Accept common alias labels from the editor and map to canonical names.

            var trimmed = (value ?? string.Empty).Trim();
            if (trimmed.Length == 0)
                return "UpperLeft";

            var compact = trimmed.Replace("-", string.Empty).Replace("_", string.Empty).Replace(" ", string.Empty).ToLowerInvariant();
            return compact switch
            {
                "topleft" => "UpperLeft",
                "topcenter" => "UpperCenter",
                "topright" => "UpperRight",
                "middleleft" => "MiddleLeft",
                "centerleft" => "MiddleLeft",
                "middle" => "MiddleCenter",
                "center" => "MiddleCenter",
                "middlecenter" => "MiddleCenter",
                "centerright" => "MiddleRight",
                "middleright" => "MiddleRight",
                "bottomleft" => "LowerLeft",
                "bottomcenter" => "LowerCenter",
                "bottomright" => "LowerRight",
                _ => trimmed,
            };
        }

        private static void TrySetEnumProperty(object target, string propertyName, string value)
        {
            // Set enum properties by name without hard-linking optional assemblies.

            try
            {
                var property = target.GetType().GetProperty(propertyName);
                if (property == null || !property.CanWrite || !property.PropertyType.IsEnum)
                    return;

                var enumValue = Enum.Parse(property.PropertyType, value, ignoreCase: true);
                property.SetValue(target, enumValue, null);
            }
            catch
            {
                Debug.LogWarning($"[sfs-ui-cdn] Invalid enum value '{value}' for property '{propertyName}'.");
            }
        }

        private static UnityEngine.UI.Slider.Direction ParseSliderDirection(string value)
        {
            // Parse slider direction values from editor properties.

            var compact = (value ?? string.Empty).Trim().Replace("-", string.Empty).Replace("_", string.Empty).Replace(" ", string.Empty).ToLowerInvariant();
            return compact switch
            {
                "righttoleft" => UnityEngine.UI.Slider.Direction.RightToLeft,
                "bottomtotop" => UnityEngine.UI.Slider.Direction.BottomToTop,
                "toptobottom" => UnityEngine.UI.Slider.Direction.TopToBottom,
                _ => UnityEngine.UI.Slider.Direction.LeftToRight,
            };
        }

        private static void ApplyVisualStyle(object element, Transform renderedElement, string elementType, string textAlignment, bool multiline, bool textColorOverride, string textColorHex, bool backgroundColorOverride, string backgroundColorHex, string borderColorHex)
        {
            // Apply lightweight styling and text settings from snapshot fields.

            if (textColorOverride && TryParseColor(textColorHex, out var textColor))
            {
                TrySetColorProperty(element, "TextColor", textColor);
                ApplyTextColorToChildren(renderedElement, textColor);
            }

            if (backgroundColorOverride && TryParseColor(backgroundColorHex, out var backgroundColor))
                ApplyBackgroundColor(element, renderedElement, elementType, backgroundColor);

            TrySetEnumProperty(element, "TextAlignment", textAlignment);
        }

        private static void ApplyBackgroundColor(object element, Transform renderedElement, string elementType, Color backgroundColor)
        {
            // Route background color to element-specific channels so text colors stay independent.

            ApplyElementBackgroundChannels(element, elementType, backgroundColor);
            ApplyBackgroundColorToChildren(renderedElement, backgroundColor);
        }

        private static void ApplyElementBackgroundChannels(object element, string elementType, Color backgroundColor)
        {
            // Apply direct element background channels used by common ModGUI controls.

            var normalizedType = (elementType ?? string.Empty).Trim().ToLowerInvariant();
            if (normalizedType == "window")
                TrySetColorProperty(element, "WindowColor", backgroundColor);
            else if (normalizedType == "textinput")
                TrySetColorProperty(element, "FieldColor", backgroundColor);
            else if (normalizedType == "button")
                TrySetColorProperty(element, "ButtonColor", backgroundColor);
            else if (normalizedType == "box" || normalizedType == "container")
                TrySetColorProperty(element, "Color", backgroundColor);
        }

        private static void ApplyTextColorToChildren(Transform renderedElement, Color textColor)
        {
            // Apply color to likely text components in children when exposed on color property.

            foreach (var component in renderedElement.GetComponentsInChildren<Component>(true))
            {
                if (component == null)
                    continue;

                var typeName = component.GetType().Name;
                if (!typeName.Contains("Text", StringComparison.OrdinalIgnoreCase) && !typeName.Contains("Label", StringComparison.OrdinalIgnoreCase))
                    continue;

                TrySetColorProperty(component, "color", textColor);
                TrySetColorProperty(component, "Color", textColor);
            }
        }

        private static void ApplyBackgroundColorToChildren(Transform renderedElement, Color backgroundColor)
        {
            // Apply background color to non-text render components so background edits do not repaint labels.

            foreach (var component in renderedElement.GetComponentsInChildren<Component>(true))
            {
                if (component == null)
                    continue;

                var typeName = component.GetType().Name;
                if (typeName.Contains("Text", StringComparison.OrdinalIgnoreCase) || typeName.Contains("Label", StringComparison.OrdinalIgnoreCase))
                    continue;

                if (!typeName.Contains("Image", StringComparison.OrdinalIgnoreCase)
                    && !typeName.Contains("Graphic", StringComparison.OrdinalIgnoreCase)
                    && !typeName.Contains("Renderer", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                TrySetColorProperty(component, "color", backgroundColor);
                TrySetColorProperty(component, "Color", backgroundColor);
            }
        }

        private static void TrySetColorProperty(object target, string propertyName, Color color)
        {
            // Best-effort reflection color setter for compatible style properties.

            var property = target.GetType().GetProperty(propertyName);
            if (property == null || !property.CanWrite || property.PropertyType != typeof(Color))
                return;

            property.SetValue(target, color, null);
        }

        private static bool TryParseColor(string value, out Color color)
        {
            // Parse color from #RRGGBB, #RRGGBBAA, or comma-separated float channels.

            color = default;
            if (string.IsNullOrWhiteSpace(value))
                return false;

            var trimmed = value.Trim();
            if (trimmed.StartsWith("#") && ColorUtility.TryParseHtmlString(trimmed, out color))
                return true;

            var parts = trimmed.Split(',');
            if (parts.Length < 3 || parts.Length > 4)
                return false;

            if (!float.TryParse(parts[0].Trim(), out var r) || !float.TryParse(parts[1].Trim(), out var g) || !float.TryParse(parts[2].Trim(), out var b))
                return false;

            var a = 1f;
            if (parts.Length == 4 && !float.TryParse(parts[3].Trim(), out a))
                return false;

            color = new Color(Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b), Mathf.Clamp01(a));
            return true;
        }

        private static string GetRequiredString(JObject node, string field)
        {
            // Read required string fields with explicit failures for malformed payloads.

            var token = node[field] ?? throw new InvalidOperationException($"Node is missing required field '{field}'.");
            return token.Type == JTokenType.String ? token.Value<string>() ?? string.Empty : token.ToString();
        }

        private static int GetRequiredInt(JObject node, string field)
        {
            // Read required integer fields and fail when non-numeric content is supplied.

            var token = node[field] ?? throw new InvalidOperationException($"Node is missing required field '{field}'.");
            if (token.Type == JTokenType.Integer)
                return token.Value<int>();

            if (int.TryParse(token.ToString(), out var parsed))
                return parsed;

            throw new InvalidOperationException($"Field '{field}' must be an integer.");
        }

        private static int DeterministicId(string id)
        {
            // Convert external node ID to stable positive integer for ModGUI window IDs.

            unchecked
            {
                var hash = 23;
                for (var i = 0; i < id.Length; i++)
                    hash = hash * 31 + id[i];

                return Math.Abs(hash);
            }
        }

        private sealed class BridgeRuntime : MonoBehaviour
        {
            // Pump queued websocket snapshots into the renderer during Unity updates.

            private Main? _owner;

            public static BridgeRuntime Create(Main owner)
            {
                var go = new GameObject("sfs-ui-cdn-runtime");
                UnityEngine.Object.DontDestroyOnLoad(go);
                var runtime = go.AddComponent<BridgeRuntime>();
                runtime._owner = owner;
                return runtime;
            }

            private IEnumerator Start()
            {
                // Capture preview frames at end-of-frame so ReadPixels is valid.

                while (true)
                {
                    yield return new WaitForEndOfFrame();
                    _owner?.TrySendPreviewFrame();
                }
            }

            private void Update()
            {
                _owner?.ProcessPendingSnapshots();
            }
        }

        private sealed class GeneratedUiTestRequest
        {
            // Carry generated source test request details from websocket to main thread execution.

            public string RequestId { get; set; } = string.Empty;
            public string Code { get; set; } = string.Empty;
            public string EntryType { get; set; } = "GeneratedUI.GeneratedLayout";
        }

        private sealed class ContractNodeEntry
        {
            // Hold one parsed DSL contract node row before snapshot object reconstruction.

            public string Id { get; set; } = string.Empty;
            public string ParentId { get; set; } = string.Empty;
            public string Type { get; set; } = string.Empty;
            public string Name { get; set; } = string.Empty;
            public int X { get; set; }
            public int Y { get; set; }
            public int Width { get; set; }
            public int Height { get; set; }
            public string Text { get; set; } = string.Empty;
            public string TextAlignment { get; set; } = "Left";
            public bool TextColorOverride { get; set; }
            public string TextColor { get; set; } = "#ffffff";
            public bool BackgroundColorOverride { get; set; }
            public string BackgroundColor { get; set; } = string.Empty;
            public string BorderColor { get; set; } = string.Empty;
            public bool Multiline { get; set; }
            public string Layout { get; set; } = "Vertical";
            public string ChildAlignment { get; set; } = "UpperLeft";
            public int Spacing { get; set; } = 12;
            public int PaddingLeft { get; set; } = 12;
            public int PaddingRight { get; set; } = 12;
            public int PaddingTop { get; set; } = 12;
            public int PaddingBottom { get; set; } = 12;
            public string WidthMode { get; set; } = "Manual";
            public string HeightMode { get; set; } = "Manual";
            public bool ScrollVertical { get; set; }
            public bool ScrollHorizontal { get; set; }
            public JObject Props { get; set; } = new JObject();
        }

        private sealed class RenderedNodeContract
        {
            // Capture canonical contract fields for each rendered node and include them in layout payloads.

            public string ParentId { get; set; } = string.Empty;
            public string PathKey { get; set; } = string.Empty;
            public int ExpectedWidth { get; set; }
            public int ExpectedHeight { get; set; }
            public string Layout { get; set; } = "Vertical";
            public string ChildAlignment { get; set; } = "UpperLeft";
            public int Spacing { get; set; } = 12;
            public string WidthMode { get; set; } = "Manual";
            public string HeightMode { get; set; } = "Manual";
            public bool ScrollVertical { get; set; }
            public bool ScrollHorizontal { get; set; }
            public string TextAlignment { get; set; } = "Left";
        }

        private sealed class LocalWebSocketServer
        {
            // Run a Mono-compatible websocket server using raw TCP and RFC6455 framing.

            private readonly string _host;
            private readonly int _port;
            private readonly TcpListener _listener;
            private readonly CancellationTokenSource _cts;
            private Action<string>? _onSnapshotPayload;
            private Action<bool>? _onPreviewConnected;
            private Action<GeneratedUiTestRequest>? _onGeneratedUiTest;
            private Task? _acceptLoopTask;
            private const string WebSocketMagic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
            private readonly SemaphoreSlim _sendLock = new SemaphoreSlim(1, 1);
            private volatile bool _hasActiveClient;
            private NetworkStream? _activeStream;

            public bool HasActiveClient => _hasActiveClient;

            public LocalWebSocketServer(string host, int port)
            {
                _host = host;
                _port = port;
                _listener = new TcpListener(System.Net.IPAddress.Parse(host), port);
                _cts = new CancellationTokenSource();
            }

            public void Start(
                Action<string> onSnapshotPayload,
                Action<bool> onPreviewConnected,
                Action<GeneratedUiTestRequest> onGeneratedUiTest
            )
            {
                // Start listener and enter async accept loop for TCP websocket clients.

                try
                {
                    _onSnapshotPayload = onSnapshotPayload;
                    _onPreviewConnected = onPreviewConnected;
                    _onGeneratedUiTest = onGeneratedUiTest;
                    _listener.Start();
                    _acceptLoopTask = Task.Run(() => AcceptLoop(_cts.Token));
                    Debug.Log($"[sfs-ui-cdn] WebSocket TCP server listening on ws://{_host}:{_port}/ws");
                }
                catch (SocketException ex)
                {
                    Debug.LogError($"[sfs-ui-cdn] Failed to start WebSocket server: {ex.Message}");
                }
            }

            public void SendJson(string payload)
            {
                // Send server-initiated websocket JSON frame when a client is connected.

                var stream = _activeStream;
                if (!_hasActiveClient || stream == null)
                    return;

                _ = Task.Run(async () =>
                {
                    try
                    {
                        await SendText(stream, payload, CancellationToken.None, _sendLock);
                    }
                    catch
                    {
                        // Connection failures are surfaced by the receive loop lifecycle.
                    }
                });
            }

            private async Task AcceptLoop(CancellationToken cancellationToken)
            {
                // Accept incoming TCP clients and process websocket handshake/frames.

                while (!cancellationToken.IsCancellationRequested)
                {
                    TcpClient client;
                    try
                    {
                        client = await _listener.AcceptTcpClientAsync();
                    }
                    catch (ObjectDisposedException)
                    {
                        return;
                    }
                    catch (SocketException)
                    {
                        if (cancellationToken.IsCancellationRequested)
                            return;
                        continue;
                    }

                    _ = Task.Run(() => HandleClient(client, cancellationToken), cancellationToken);
                }
            }

            private async Task HandleClient(TcpClient client, CancellationToken cancellationToken)
            {
                // Validate HTTP upgrade request and switch to websocket frame mode.

                using (client)
                using (var stream = client.GetStream())
                {
                    HttpRequestData? request;
                    try
                    {
                        request = await ReadHttpRequest(stream, cancellationToken);
                    }
                    catch (InvalidOperationException ex) when (ex.Message.Contains("header"))
                    {
                        Debug.LogError($"[sfs-ui-cdn] {ex.Message}");
                        await WriteHttpResponse(stream, 413, "Request header too large", cancellationToken);
                        return;
                    }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[sfs-ui-cdn] Failed reading HTTP request: {ex.Message}");
                        return;
                    }

                    if (request == null)
                        return;

                    var path = NormalizePath(request.Path);

                    if (path == "/health")
                    {
                        await WriteHttpResponse(stream, 200, "ok", cancellationToken);
                        return;
                    }

                    if (path != "/ws")
                    {
                        await WriteHttpResponse(stream, 400, $"Expected route /ws or /ws/ (received: {request.Path})", cancellationToken);
                        return;
                    }

                    if (!request.Headers.TryGetValue("sec-websocket-key", out var secWebSocketKey) || string.IsNullOrWhiteSpace(secWebSocketKey))
                    {
                        await WriteHttpResponse(stream, 400, "Missing Sec-WebSocket-Key header", cancellationToken);
                        return;
                    }

                    if (!request.Headers.TryGetValue("upgrade", out var upgrade) || !string.Equals(upgrade, "websocket", StringComparison.OrdinalIgnoreCase))
                    {
                        await WriteHttpResponse(stream, 400, "Missing Upgrade: websocket header", cancellationToken);
                        return;
                    }

                    var accept = ComputeWebSocketAccept(secWebSocketKey);
                    var response =
                        "HTTP/1.1 101 Switching Protocols\r\n" +
                        "Upgrade: websocket\r\n" +
                        "Connection: Upgrade\r\n" +
                        $"Sec-WebSocket-Accept: {accept}\r\n\r\n";

                    var responseBytes = Encoding.ASCII.GetBytes(response);
                    await stream.WriteAsync(responseBytes, 0, responseBytes.Length, cancellationToken);
                    await stream.FlushAsync(cancellationToken);

                    _activeStream = stream;
                    _hasActiveClient = true;
                    Debug.Log("[sfs-ui-cdn] Python client connected.");
                    await ReceiveLoop(stream, cancellationToken);
                    _hasActiveClient = false;
                    _activeStream = null;
                    Debug.Log("[sfs-ui-cdn] Python client disconnected.");
                }
            }

            private static async Task<HttpRequestData?> ReadHttpRequest(NetworkStream stream, CancellationToken cancellationToken)
            {
                // Read and parse HTTP request headers until CRLF-CRLF terminator.

                var buffer = new byte[4096];
                var requestBytes = new MemoryStream();

                while (true)
                {
                    var read = await stream.ReadAsync(buffer, 0, buffer.Length, cancellationToken);
                    if (read <= 0)
                        return null;

                    requestBytes.Write(buffer, 0, read);
                    if (EndsWithHeaderTerminator(requestBytes))
                        break;

                    if (requestBytes.Length > 64 * 1024)
                        throw new InvalidOperationException("HTTP header too large.");
                }

                var raw = Encoding.ASCII.GetString(requestBytes.ToArray());
                var lines = raw.Split(new[] { "\r\n" }, StringSplitOptions.None);
                if (lines.Length == 0 || string.IsNullOrWhiteSpace(lines[0]))
                    throw new InvalidOperationException("Missing request line.");

                var requestLineParts = lines[0].Split(' ');
                if (requestLineParts.Length < 2)
                    throw new InvalidOperationException("Invalid request line.");

                var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                for (var i = 1; i < lines.Length; i++)
                {
                    var line = lines[i];
                    if (string.IsNullOrEmpty(line))
                        break;

                    var separator = line.IndexOf(':');
                    if (separator <= 0)
                        continue;

                    var key = line.Substring(0, separator).Trim();
                    var value = line.Substring(separator + 1).Trim();
                    headers[key] = value;
                }

                return new HttpRequestData
                {
                    Method = requestLineParts[0],
                    Path = requestLineParts[1],
                    Headers = headers,
                };
            }

            private static bool EndsWithHeaderTerminator(MemoryStream stream)
            {
                // Detect CRLF-CRLF end marker in the buffered HTTP header.

                if (stream.Length < 4)
                    return false;

                var bytes = stream.GetBuffer();
                var n = (int)stream.Length;
                return bytes[n - 4] == 13 && bytes[n - 3] == 10 && bytes[n - 2] == 13 && bytes[n - 1] == 10;
            }

            private async Task ReceiveLoop(NetworkStream stream, CancellationToken cancellationToken)
            {
                // Handle websocket frames and send explicit responses for known commands.

                while (!cancellationToken.IsCancellationRequested)
                {
                    var header = new byte[2];
                    if (!await ReadExactly(stream, header, 0, 2, cancellationToken))
                        return;

                    var fin = (header[0] & 0x80) != 0;
                    var opcode = header[0] & 0x0F;
                    var masked = (header[1] & 0x80) != 0;
                    var payloadLenCode = (ulong)(header[1] & 0x7F);

                    if (!fin)
                    {
                        await SendCloseFrame(stream, cancellationToken, _sendLock);
                        return;
                    }

                    ulong payloadLen = payloadLenCode;
                    if (payloadLenCode == 126)
                    {
                        var ext = new byte[2];
                        if (!await ReadExactly(stream, ext, 0, 2, cancellationToken))
                            return;

                        if (BitConverter.IsLittleEndian)
                            Array.Reverse(ext);
                        payloadLen = BitConverter.ToUInt16(ext, 0);
                    }
                    else if (payloadLenCode == 127)
                    {
                        var ext = new byte[8];
                        if (!await ReadExactly(stream, ext, 0, 8, cancellationToken))
                            return;

                        if (BitConverter.IsLittleEndian)
                            Array.Reverse(ext);
                        payloadLen = BitConverter.ToUInt64(ext, 0);
                    }

                    if (payloadLen > int.MaxValue)
                    {
                        await SendCloseFrame(stream, cancellationToken, _sendLock);
                        return;
                    }

                    byte[]? mask = null;
                    if (masked)
                    {
                        mask = new byte[4];
                        if (!await ReadExactly(stream, mask, 0, 4, cancellationToken))
                            return;
                    }

                    var payload = new byte[(int)payloadLen];
                    if (payload.Length > 0 && !await ReadExactly(stream, payload, 0, payload.Length, cancellationToken))
                        return;

                    if (masked && mask != null)
                    {
                        for (var i = 0; i < payload.Length; i++)
                            payload[i] = (byte)(payload[i] ^ mask[i % 4]);
                    }

                    if (opcode == 0x8)
                    {
                        await SendCloseFrame(stream, cancellationToken, _sendLock);
                        return;
                    }

                    if (opcode == 0x9)
                    {
                        await SendFrame(stream, 0xA, payload, cancellationToken, _sendLock);
                        continue;
                    }

                    if (opcode != 0x1)
                        continue;

                    var message = Encoding.UTF8.GetString(payload).Trim();
                    if (message.Length == 0)
                    {
                        await SendText(stream, "{\"type\":\"error\",\"message\":\"empty-message\"}", cancellationToken, _sendLock);
                        continue;
                    }

                    if (string.Equals(message, "ping", StringComparison.OrdinalIgnoreCase))
                    {
                        await SendText(stream, "{\"type\":\"pong\",\"source\":\"sfs-ui-cdn\"}", cancellationToken, _sendLock);
                        continue;
                    }

                    if (string.Equals(message, "hello", StringComparison.OrdinalIgnoreCase))
                    {
                        await SendText(stream, "{\"type\":\"hello\",\"mod\":\"sfs-ui-cdn\",\"version\":\"0.0.1\"}", cancellationToken, _sendLock);
                        continue;
                    }

                    if (TryExtractSnapshotPayload(message, out var payloadJson, out var error))
                    {
                        if (payloadJson == null)
                        {
                            var errorMsg = error ?? "invalid-snapshot";
                            Debug.LogWarning($"[sfs-ui-cdn] Snapshot extraction failed: {errorMsg}");
                            await SendText(stream, "{\"type\":\"error\",\"message:\":\"" + EscapeJson(errorMsg) + "\"}", cancellationToken, _sendLock);
                            continue;
                        }

                        _onSnapshotPayload?.Invoke(payloadJson);
                        await SendText(stream, "{\"type\":\"snapshot-queued\"}", cancellationToken, _sendLock);
                        continue;
                    }

                    if (TryExtractPreviewVisibility(message, out var connected, out var visibilityError))
                    {
                        if (connected == null)
                        {
                            var errorMsg = visibilityError ?? "invalid-preview-visibility";
                            await SendText(stream, "{\"type\":\"error\",\"message\":\"" + EscapeJson(errorMsg) + "\"}", cancellationToken, _sendLock);
                            continue;
                        }

                        _onPreviewConnected?.Invoke(connected.Value);
                        await SendText(stream, "{\"type\":\"preview-visibility\",\"connected\":" + (connected.Value ? "true" : "false") + "}", cancellationToken, _sendLock);
                        continue;
                    }

                    if (TryExtractGeneratedUiTest(message, out var request, out var requestError))
                    {
                        if (request == null)
                        {
                            var errorMsg = requestError ?? "invalid-generated-ui-test";
                            await SendText(stream, "{\"type\":\"error\",\"message\":\"" + EscapeJson(errorMsg) + "\"}", cancellationToken, _sendLock);
                            continue;
                        }

                        _onGeneratedUiTest?.Invoke(request);
                        var queuedMessage = "{\"type\":\"test-queued\",\"request_id\":\"" + EscapeJson(request.RequestId) + "\"}";
                        await SendText(stream, queuedMessage, cancellationToken, _sendLock);
                        continue;
                    }

                    await SendText(stream, "{\"type\":\"echo\",\"payload\":\"" + EscapeJson(message) + "\"}", cancellationToken, _sendLock);
                }
            }

            private static bool TryExtractPreviewVisibility(string message, out bool? connected, out string? error)
            {
                // Parse preview visibility command from JSON envelope.

                connected = null;
                error = null;

                JObject envelope;
                try
                {
                    envelope = JObject.Parse(message);
                }
                catch
                {
                    return false;
                }

                var messageType = envelope["type"]?.Value<string>();
                if (!string.Equals(messageType, "preview-visibility", StringComparison.OrdinalIgnoreCase))
                    return false;

                var token = envelope["connected"];
                if (token == null)
                {
                    error = "preview-visibility message requires connected field";
                    return true;
                }

                if (token.Type == JTokenType.Boolean)
                {
                    connected = token.Value<bool>();
                    return true;
                }

                if (bool.TryParse(token.ToString(), out var parsed))
                {
                    connected = parsed;
                    return true;
                }

                error = "preview-visibility connected must be boolean";
                return true;
            }

            private static bool TryExtractSnapshotPayload(string message, out string? payloadJson, out string? error)
            {
                // Parse JSON message and extract payload when message type is snapshot.

                payloadJson = null;
                error = null;

                JObject envelope;
                try
                {
                    envelope = JObject.Parse(message);
                }
                catch (Exception ex)
                {
                    error = $"JSON parse error: {ex.Message}";
                    return true; // Signal that we tried to extract snapshot but failed on parse
                }

                var messageType = envelope["type"]?.Value<string>();
                if (!string.Equals(messageType, "snapshot", StringComparison.OrdinalIgnoreCase))
                    return false; // Not a snapshot message, let caller handle as non-snapshot

                var payloadToken = envelope["payload"];
                if (payloadToken == null || payloadToken.Type != JTokenType.Object)
                {
                    error = "snapshot payload must be an object";
                    return true; // Signal snapshot envelope but invalid payload
                }

                payloadJson = payloadToken.ToString(Newtonsoft.Json.Formatting.None);
                return true; // Successfully extracted valid snapshot
            }

            private static bool TryExtractGeneratedUiTest(string message, out GeneratedUiTestRequest? request, out string? error)
            {
                // Parse generated C# test command payload from JSON envelope.

                request = null;
                error = null;

                JObject envelope;
                try
                {
                    envelope = JObject.Parse(message);
                }
                catch
                {
                    return false;
                }

                var messageType = envelope["type"]?.Value<string>();
                if (!string.Equals(messageType, "test-generated-ui", StringComparison.OrdinalIgnoreCase))
                    return false;

                var code = envelope["code"]?.Value<string>() ?? string.Empty;
                if (string.IsNullOrWhiteSpace(code))
                {
                    error = "test-generated-ui requires non-empty code field";
                    return true;
                }

                var requestId = envelope["request_id"]?.Value<string>();
                if (string.IsNullOrWhiteSpace(requestId))
                    requestId = Guid.NewGuid().ToString("N");

                var entryType = envelope["entry_type"]?.Value<string>() ?? "GeneratedUI.GeneratedLayout";
                request = new GeneratedUiTestRequest
                {
                    RequestId = requestId,
                    Code = code,
                    EntryType = entryType,
                };
                return true;
            }

            private static async Task<bool> ReadExactly(NetworkStream stream, byte[] buffer, int offset, int count, CancellationToken cancellationToken)
            {
                // Read exactly N bytes from the socket unless the client disconnects.

                var readTotal = 0;
                while (readTotal < count)
                {
                    var read = await stream.ReadAsync(buffer, offset + readTotal, count - readTotal, cancellationToken);
                    if (read <= 0)
                        return false;
                    readTotal += read;
                }

                return true;
            }

            private static async Task SendText(NetworkStream stream, string payload, CancellationToken cancellationToken, SemaphoreSlim sendLock)
            {
                // Send UTF-8 JSON text frame to websocket client.

                await SendFrame(stream, 0x1, Encoding.UTF8.GetBytes(payload), cancellationToken, sendLock);
            }

            private static async Task SendCloseFrame(NetworkStream stream, CancellationToken cancellationToken, SemaphoreSlim sendLock)
            {
                // Send close opcode to terminate websocket session cleanly.

                await SendFrame(stream, 0x8, Array.Empty<byte>(), cancellationToken, sendLock);
            }

            private static async Task SendFrame(NetworkStream stream, byte opcode, byte[] payload, CancellationToken cancellationToken, SemaphoreSlim sendLock)
            {
                // Build and send unmasked server websocket frames.

                using var ms = new MemoryStream();
                ms.WriteByte((byte)(0x80 | (opcode & 0x0F)));

                if (payload.Length <= 125)
                {
                    ms.WriteByte((byte)payload.Length);
                }
                else if (payload.Length <= ushort.MaxValue)
                {
                    ms.WriteByte(126);
                    var len = BitConverter.GetBytes((ushort)payload.Length);
                    if (BitConverter.IsLittleEndian)
                        Array.Reverse(len);
                    ms.Write(len, 0, len.Length);
                }
                else
                {
                    ms.WriteByte(127);
                    var len = BitConverter.GetBytes((ulong)payload.Length);
                    if (BitConverter.IsLittleEndian)
                        Array.Reverse(len);
                    ms.Write(len, 0, len.Length);
                }

                if (payload.Length > 0)
                    ms.Write(payload, 0, payload.Length);

                var bytes = ms.ToArray();
                await sendLock.WaitAsync(cancellationToken);
                try
                {
                    await stream.WriteAsync(bytes, 0, bytes.Length, cancellationToken);
                    await stream.FlushAsync(cancellationToken);
                }
                finally
                {
                    sendLock.Release();
                }
            }

            private static async Task WriteHttpResponse(NetworkStream stream, int statusCode, string message, CancellationToken cancellationToken)
            {
                // Emit a plain HTTP response for health checks and handshake failures.

                var statusText = statusCode == 200 ? "OK" : "Bad Request";
                var body = Encoding.UTF8.GetBytes(message);
                var header =
                    $"HTTP/1.1 {statusCode} {statusText}\r\n" +
                    "Content-Type: text/plain; charset=utf-8\r\n" +
                    $"Content-Length: {body.Length}\r\n" +
                    "Connection: close\r\n\r\n";

                var headerBytes = Encoding.ASCII.GetBytes(header);
                await stream.WriteAsync(headerBytes, 0, headerBytes.Length, cancellationToken);
                await stream.WriteAsync(body, 0, body.Length, cancellationToken);
                await stream.FlushAsync(cancellationToken);
            }

            private static string ComputeWebSocketAccept(string secWebSocketKey)
            {
                // Compute RFC6455 Sec-WebSocket-Accept response token.

                using var sha1 = SHA1.Create();
                var input = Encoding.ASCII.GetBytes(secWebSocketKey.Trim() + WebSocketMagic);
                var hash = sha1.ComputeHash(input);
                return Convert.ToBase64String(hash);
            }

            private static string EscapeJson(string value)
            {
                // Escape characters that would break minimal JSON string payload responses.

                return value
                    .Replace("\\", "\\\\")
                    .Replace("\"", "\\\"")
                    .Replace("\r", "\\r")
                    .Replace("\n", "\\n");
            }

            private static string NormalizePath(string value)
            {
                // Normalize trailing slash behavior so /ws and /ws/ resolve identically.

                if (string.IsNullOrWhiteSpace(value))
                    return "/";

                var trimmed = value.Trim();
                if (trimmed.Length > 1 && trimmed.EndsWith("/"))
                    return trimmed.TrimEnd('/');

                return trimmed;
            }

            private sealed class HttpRequestData
            {
                // Carry parsed request line and headers into handshake logic.

                public string Method { get; set; } = string.Empty;
                public string Path { get; set; } = string.Empty;
                public Dictionary<string, string> Headers { get; set; } = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            }


        }
    }
}