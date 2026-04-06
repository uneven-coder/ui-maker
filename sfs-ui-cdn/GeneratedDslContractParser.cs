using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json.Linq;

namespace cdnui
{
    internal static class GeneratedDslContractParser
    {
        // Parse generated-source contract markers and rebuild snapshot payload used by runtime rendering.

        public static bool TryBuildSnapshot(string sourceCode, string startMarker, string endMarker, out JObject snapshot, out string message)
        {
            snapshot = new JObject();
            message = string.Empty;

            if (!TryExtractContract(sourceCode, startMarker, endMarker, out var contractText, out message))
                return false;

            return TryParseContract(contractText, out snapshot, out message);
        }

        private static bool TryExtractContract(string sourceCode, string startMarker, string endMarker, out string contractText, out string message)
        {
            contractText = string.Empty;
            message = string.Empty;

            var startIndex = sourceCode.IndexOf(startMarker, StringComparison.Ordinal);
            if (startIndex < 0)
            {
                message = "Generated source is contractless and cannot be interpreted by this legacy runtime test parser. Use the generated file directly in a mod project and call GeneratedLayout.Render(...).";
                return false;
            }

            startIndex += startMarker.Length;
            var endIndex = sourceCode.IndexOf(endMarker, startIndex, StringComparison.Ordinal);
            if (endIndex < 0)
            {
                message = $"Generated source is missing marker: {endMarker}.";
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

        private static bool TryParseContract(string contractText, out JObject snapshot, out string message)
        {
            snapshot = new JObject();
            message = string.Empty;

            var entries = new List<ContractNodeEntry>();
            var schemaVersion = "1.0.0";
            var versionSeen = false;

            var lines = contractText.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);
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
                props = JObject.Parse(propsJson);
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

        private sealed class ContractNodeEntry
        {
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
    }
}
