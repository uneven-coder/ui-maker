from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

CONTAINER_TYPES = {"Window", "ClosableWindow", "Container", "Box"}
KNOWN_NODE_ORDER = [
    "id",
    "type",
    "name",
    "x",
    "y",
    "width",
    "height",
    "text",
    "text_alignment",
    "text_color_override",
    "text_color",
    "background_color_override",
    "background_color",
    "border_color",
    "multiline",
    "layout",
    "child_alignment",
    "spacing",
    "padding",
    "padding_left",
    "padding_right",
    "padding_top",
    "padding_bottom",
    "width_mode",
    "height_mode",
    "scroll_vertical",
    "scroll_horizontal",
    "props",
    "children",
]

SIZE_MODE_MANUAL = "Manual"
SIZE_MODE_AUTO = "Auto"


def generate_csharp_export(snapshot: dict, source_file: Optional[Path]) -> str:
    # Generate compact, executable C# that directly builds ModGUI/UITools elements.

    _ = source_file
    normalized_snapshot = _normalize_snapshot(snapshot)
    node_count = _count_nodes(normalized_snapshot.get("roots", []))

    ordered_nodes = _collect_nodes_preorder(normalized_snapshot["roots"])
    export_id_map = _build_export_id_map(ordered_nodes)
    hierarchy_lines = _build_hierarchy_lines(normalized_snapshot["roots"], export_id_map)
    hierarchy_comment = "\n".join(f"// {line}" for line in hierarchy_lines) if hierarchy_lines else "// (no elements)"

    lines: list[str] = []
    lines.append("using System;")
    lines.append("using System.Collections.Generic;")
    lines.append("using SFS.UI.ModGUI;")
    lines.append("using UITools;")
    lines.append("using UnityEngine;")
    lines.append("using UnityEngine.UI;")
    lines.append("")
    lines.append("namespace GeneratedUI")
    lines.append("{")
    lines.append("    public static class GeneratedLayout")
    lines.append("    {")
    # lines.append(f"        // nodeCount: {node_count}")
    # lines.append("        // hierarchy:")
    # lines.extend(f"        {line}" for line in hierarchy_comment.splitlines())
    lines.append("")
    lines.append("        public static IReadOnlyList<UiNode> Define()")
    lines.append("        {")
    lines.append("            // Build deterministic node definitions for runtime rendering.")
    lines.append("")
    lines.append("            return new List<UiNode>")
    lines.append("            {")
    root_sibling_count = len(normalized_snapshot["roots"])
    for root in normalized_snapshot["roots"]:
        lines.extend(_build_node_initializer_lines(root, export_id_map, indent=16, trailing_comma=True, sibling_count=root_sibling_count))
    lines.append("            };")
    lines.append("        }")
    lines.append("")
    lines.extend(_emit_ui_node_class_lines())
    lines.append("    }")
    lines.append("}")

    return "\n".join(lines).rstrip() + "\n"


def _emit_ui_node_class_lines() -> list[str]:
    # Emit a full UiNode runtime renderer that instantiates ModGUI/UITools elements.

    lines: list[str] = []
    lines.append("        private static GameObject? _activeHolder;")
    lines.append("        private static Window? _activeWindow;")
    lines.append("        private static RenderOptions? _activeOptions;")
    lines.append("")
    lines.append("        public sealed class RenderOptions")
    lines.append("        {")
    lines.append("            public Builder.SceneToAttach SceneToAttach { get; set; } = Builder.SceneToAttach.CurrentScene;")
    lines.append("            public string HolderName { get; set; } = \"GeneratedUIHolder\";")
    lines.append("            public bool WindowDraggable { get; set; }")
    lines.append("            public WindowRenderMode WindowMode { get; set; } = WindowRenderMode.AsDefined;")
    lines.append("            public int? Width { get; set; }")
    lines.append("            public int? Height { get; set; }")
    lines.append("            public bool StartHidden { get; set; }")
    lines.append("            public bool RemoveExistingBeforeRender { get; set; } = true;")
    lines.append("        }")
    lines.append("")
    lines.append("        public enum WindowRenderMode")
    lines.append("        {")
    lines.append("            AsDefined,")
    lines.append("            ForceNormal,")
    lines.append("            ForceClosable,")
    lines.append("        }")
    lines.append("")
    lines.append("        public enum UiNodeType")
    lines.append("        {")
    lines.append("            Window,")
    lines.append("            ClosableWindow,")
    lines.append("            Container,")
    lines.append("            Box,")
    lines.append("            Label,")
    lines.append("            Button,")
    lines.append("            TextInput,")
    lines.append("            Toggle,")
    lines.append("            Slider,")
    lines.append("            Separator,")
    lines.append("            Space,")
    lines.append("            NumberInput,")
    lines.append("        }")
    lines.append("")
    lines.append("        public static Window? Render(RenderOptions? options = null)")
    lines.append("        {")
    lines.append("            // Render full UI tree into a managed holder.")
    lines.append("")
    lines.append("            options ??= new RenderOptions();")
    lines.append("            if (options.RemoveExistingBeforeRender)")
    lines.append("                Remove();")
    lines.append("            _activeOptions = options;")
    lines.append("")
    lines.append("            _activeHolder = Builder.CreateHolder(options.SceneToAttach, options.HolderName);")
    lines.append("            Transform parent = _activeHolder.transform;")
    lines.append("            _activeWindow = null;")
    lines.append("")
    lines.append("            foreach (var node in Define())")
    lines.append("                node.Build(parent);")
    lines.append("")
    lines.append("            if (options.StartHidden && _activeHolder != null)")
    lines.append("                _activeHolder.SetActive(false);")
    lines.append("")
    lines.append("            return _activeWindow;")
    lines.append("        }")
    lines.append("")
    lines.append("        public static void Render(Window window)")
    lines.append("        {")
    lines.append("            // Keep compatibility with existing call sites that pass a Window.")
    lines.append("")
    lines.append("            _ = window;")
    lines.append("            Render((RenderOptions?)null);")
    lines.append("        }")
    lines.append("")
    lines.append("        public static void Render(Transform parent)")
    lines.append("        {")
    lines.append("            // Render definitions directly under an existing parent transform.")
    lines.append("")
    lines.append("            _activeOptions = null;")
    lines.append("            foreach (var node in Define())")
    lines.append("                node.Build(parent);")
    lines.append("        }")
    lines.append("")
    lines.append("        public static void Hide()")
    lines.append("        {")
    lines.append("            // Hide generated holder without destroying it.")
    lines.append("")
    lines.append("            if (_activeHolder != null)")
    lines.append("                _activeHolder.SetActive(false);")
    lines.append("        }")
    lines.append("")
    lines.append("        public static void Show()")
    lines.append("        {")
    lines.append("            // Show generated holder if it exists.")
    lines.append("")
    lines.append("            if (_activeHolder != null)")
    lines.append("                _activeHolder.SetActive(true);")
    lines.append("        }")
    lines.append("")
    lines.append("        public static void Remove()")
    lines.append("        {")
    lines.append("            // Destroy generated holder and clear static render state.")
    lines.append("")
    lines.append("            if (_activeHolder != null)")
    lines.append("            {")
    lines.append("                UnityEngine.Object.Destroy(_activeHolder);")
    lines.append("                _activeHolder = null;")
    lines.append("            }")
    lines.append("            _activeWindow = null;")
    lines.append("            _activeOptions = null;")
    lines.append("        }")
    lines.append("")
    lines.append("        private static bool ResolveClosableWindowMode(UiNodeType nodeType)")
    lines.append("        {")
    lines.append("            // Resolve whether a window node should render as ClosableWindow.")
    lines.append("")
    lines.append("            var mode = _activeOptions?.WindowMode ?? WindowRenderMode.AsDefined;")
    lines.append("            switch (mode)")
    lines.append("            {")
    lines.append("                case WindowRenderMode.ForceClosable:")
    lines.append("                    return nodeType == UiNodeType.Window || nodeType == UiNodeType.ClosableWindow;")
    lines.append("                case WindowRenderMode.ForceNormal:")
    lines.append("                    return false;")
    lines.append("                default:")
    lines.append("                    return nodeType == UiNodeType.ClosableWindow;")
    lines.append("            }")
    lines.append("        }")
    lines.append("")
    lines.append("        private static UiNode Node(string id, UiNodeType type, string name, int width, int height)")
    lines.append("        {")
    lines.append("            // Create one fluent node with required identity and dimensions.")
    lines.append("")
    lines.append("            return new UiNode")
    lines.append("            {")
    lines.append("                Id = id,")
    lines.append("                Type = type,")
    lines.append("                Name = name,")
    lines.append("                Width = width,")
    lines.append("                Height = height,")
    lines.append("            };")
    lines.append("        }")
    lines.append("")
    lines.append("        public sealed class UiNode")
    lines.append("        {")
    lines.append("            public string Id { get; set; } = string.Empty;")
    lines.append("            public UiNodeType Type { get; set; } = UiNodeType.Container;")
    lines.append("            public string Name { get; set; } = string.Empty;")
    lines.append("            public int X { get; set; }")
    lines.append("            public int Y { get; set; }")
    lines.append("            public int Width { get; set; }")
    lines.append("            public int Height { get; set; }")
    lines.append("            public string Text { get; set; } = string.Empty;")
    lines.append("            public string TextAlignment { get; set; } = \"Left\";")
    lines.append("            public bool TextColorOverride { get; set; }")
    lines.append("            public string TextColor { get; set; } = \"#ffffff\";")
    lines.append("            public bool BackgroundColorOverride { get; set; }")
    lines.append("            public string BackgroundColor { get; set; } = string.Empty;")
    lines.append("            public string BorderColor { get; set; } = string.Empty;")
    lines.append("            public bool Multiline { get; set; }")
    lines.append("            public string Layout { get; set; } = \"Vertical\";")
    lines.append("            public string ChildAlignment { get; set; } = \"UpperLeft\";")
    lines.append("            public int Spacing { get; set; } = 12;")
    lines.append("            public int PaddingLeft { get; set; } = 12;")
    lines.append("            public int PaddingRight { get; set; } = 12;")
    lines.append("            public int PaddingTop { get; set; } = 12;")
    lines.append("            public int PaddingBottom { get; set; } = 12;")
    lines.append("            public string WidthMode { get; set; } = \"Manual\";")
    lines.append("            public string HeightMode { get; set; } = \"Manual\";")
    lines.append("            public int SiblingCount { get; set; } = 1;")
    lines.append("            public bool ScrollVertical { get; set; }")
    lines.append("            public bool ScrollHorizontal { get; set; }")
    lines.append("            public string PropsJson { get; set; } = \"{}\";")
    lines.append("            public List<UiNode> Children { get; set; } = new List<UiNode>();")
    lines.append("            private int? _allocatedWidth;")
    lines.append("            private int? _allocatedHeight;")
    lines.append("")
    lines.append("            public UiNode At(int x, int y)")
    lines.append("            {")
    lines.append("                X = x;")
    lines.append("                Y = y;")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode WithText(string text)")
    lines.append("            {")
    lines.append("                Text = text;")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode Visual(string textAlignment, bool multiline, bool textColorOverride, string textColor, bool backgroundColorOverride, string backgroundColor, string borderColor)")
    lines.append("            {")
    lines.append("                TextAlignment = textAlignment;")
    lines.append("                Multiline = multiline;")
    lines.append("                TextColorOverride = textColorOverride;")
    lines.append("                TextColor = textColor;")
    lines.append("                BackgroundColorOverride = backgroundColorOverride;")
    lines.append("                BackgroundColor = backgroundColor;")
    lines.append("                BorderColor = borderColor;")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode LayoutConfig(string layout, string childAlignment, int spacing, int paddingLeft, int paddingRight, int paddingTop, int paddingBottom)")
    lines.append("            {")
    lines.append("                Layout = layout;")
    lines.append("                ChildAlignment = childAlignment;")
    lines.append("                Spacing = spacing;")
    lines.append("                PaddingLeft = paddingLeft;")
    lines.append("                PaddingRight = paddingRight;")
    lines.append("                PaddingTop = paddingTop;")
    lines.append("                PaddingBottom = paddingBottom;")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode Sizing(string widthMode, string heightMode, int siblingCount)")
    lines.append("            {")
    lines.append("                WidthMode = widthMode;")
    lines.append("                HeightMode = heightMode;")
    lines.append("                SiblingCount = Math.Max(1, siblingCount);")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode Scroll(bool vertical, bool horizontal)")
    lines.append("            {")
    lines.append("                ScrollVertical = vertical;")
    lines.append("                ScrollHorizontal = horizontal;")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode Props(string propsJson)")
    lines.append("            {")
    lines.append("                PropsJson = propsJson;")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public UiNode AddChildren(params UiNode[] children)")
    lines.append("            {")
    lines.append("                if (children == null || children.Length == 0)")
    lines.append("                    return this;")
    lines.append("                Children.AddRange(children);")
    lines.append("                return this;")
    lines.append("            }")
    lines.append("")
    lines.append("            public Transform Build(Transform parent)")
    lines.append("            {")
    lines.append("                var resolvedWidth = ResolveWidth(parent);")
    lines.append("                var resolvedHeight = ResolveHeight(parent);")
    lines.append("                var draggable = GeneratedLayout._activeOptions != null && GeneratedLayout._activeOptions.WindowDraggable;")
    lines.append("                object element;")
    lines.append("                Transform transform;")
    lines.append("                Transform childParent;")
    lines.append("")
    lines.append("                switch (Type)")
    lines.append("                {")
    lines.append("                    case UiNodeType.Window:")
    lines.append("                    case UiNodeType.ClosableWindow:")
    lines.append("                    {")
    lines.append("                        if (GeneratedLayout.ResolveClosableWindowMode(Type))")
    lines.append("                        {")
    lines.append("                            var closable = UIToolsBuilder.CreateClosableWindow(parent, DeterministicId(Id), resolvedWidth, resolvedHeight, X, Y, draggable: draggable, savePosition: false, titleText: string.IsNullOrWhiteSpace(Text) ? Name : Text, minimized: false);")
    lines.append("                            if (closable.rectTransform != null)")
    lines.append("                                ClampRectTransformToScreen(closable.rectTransform);")
    lines.append("                            ConfigureLayout(closable);")
    lines.append("                            if (ScrollVertical)")
    lines.append("                                closable.EnableScrolling(SFS.UI.ModGUI.Type.Vertical);")
    lines.append("                            if (ScrollHorizontal)")
    lines.append("                                closable.EnableScrolling(SFS.UI.ModGUI.Type.Horizontal);")
    lines.append("                            element = closable;")
    lines.append("                            transform = closable.gameObject.transform;")
    lines.append("                            childParent = closable.ChildrenHolder;")
    lines.append("                        }")
    lines.append("                        else")
    lines.append("                        {")
    lines.append("                            var window = Builder.CreateWindow(parent, DeterministicId(Id), resolvedWidth, resolvedHeight, X, Y, draggable: draggable, savePosition: false, titleText: string.IsNullOrWhiteSpace(Text) ? Name : Text);")
    lines.append("                            if (window.rectTransform != null)")
    lines.append("                                ClampRectTransformToScreen(window.rectTransform);")
    lines.append("                            ConfigureLayout(window);")
    lines.append("                            if (ScrollVertical)")
    lines.append("                                window.EnableScrolling(SFS.UI.ModGUI.Type.Vertical);")
    lines.append("                            if (ScrollHorizontal)")
    lines.append("                                window.EnableScrolling(SFS.UI.ModGUI.Type.Horizontal);")
    lines.append("                            element = window;")
    lines.append("                            transform = window.gameObject.transform;")
    lines.append("                            childParent = window.ChildrenHolder;")
    lines.append("                        }")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Container:")
    lines.append("                    {")
    lines.append("                        var container = Builder.CreateContainer(parent, X, Y);")
    lines.append("                        container.Size = new Vector2(resolvedWidth, resolvedHeight);")
    lines.append("                        ConfigureLayout(container);")
    lines.append("                        element = container;")
    lines.append("                        transform = container.gameObject.transform;")
    lines.append("                        childParent = container.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Box:")
    lines.append("                    {")
    lines.append("                        var box = Builder.CreateBox(parent, resolvedWidth, resolvedHeight, X, Y, opacity: 0.35f);")
    lines.append("                        ConfigureLayout(box);")
    lines.append("                        element = box;")
    lines.append("                        transform = box.gameObject.transform;")
    lines.append("                        childParent = box.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Label:")
    lines.append("                    {")
    lines.append("                        var label = Builder.CreateLabel(parent, resolvedWidth, resolvedHeight, X, Y, Text);")
    lines.append("                        element = label;")
    lines.append("                        transform = label.gameObject.transform;")
    lines.append("                        childParent = label.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Button:")
    lines.append("                    {")
    lines.append("                        var button = Builder.CreateButton(parent, resolvedWidth, resolvedHeight, X, Y, null, Text);")
    lines.append("                        element = button;")
    lines.append("                        transform = button.gameObject.transform;")
    lines.append("                        childParent = button.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.TextInput:")
    lines.append("                    {")
    lines.append("                        var input = Builder.CreateTextInput(parent, resolvedWidth, resolvedHeight, X, Y, Text, null);")
    lines.append("                        element = input;")
    lines.append("                        transform = input.gameObject.transform;")
    lines.append("                        childParent = input.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Toggle:")
    lines.append("                    {")
    lines.append("                        var state = false;")
    lines.append("                        var toggle = Builder.CreateToggle(parent, () => state, X, Y, () => state = !state);")
    lines.append("                        element = toggle;")
    lines.append("                        transform = toggle.gameObject.transform;")
    lines.append("                        childParent = toggle.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Slider:")
    lines.append("                    {")
    lines.append("                        var slider = Builder.CreateSlider(parent, resolvedWidth, 0f, (0f, 1f), false, null, null);")
    lines.append("                        element = slider;")
    lines.append("                        transform = slider.gameObject.transform;")
    lines.append("                        childParent = slider.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Separator:")
    lines.append("                    {")
    lines.append("                        var separator = Builder.CreateSeparator(parent, resolvedWidth, X, Y);")
    lines.append("                        element = separator;")
    lines.append("                        transform = separator.gameObject.transform;")
    lines.append("                        childParent = separator.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.Space:")
    lines.append("                    {")
    lines.append("                        var space = Builder.CreateSpace(parent, resolvedWidth, resolvedHeight);")
    lines.append("                        element = space;")
    lines.append("                        transform = space.gameObject.transform;")
    lines.append("                        childParent = space.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    case UiNodeType.NumberInput:")
    lines.append("                    {")
    lines.append("                        var number = UIToolsBuilder.CreateNumberInput(parent, resolvedWidth, resolvedHeight, 0f, 1f, X, Y);")
    lines.append("                        element = number;")
    lines.append("                        transform = number.gameObject.transform;")
    lines.append("                        childParent = number.gameObject.transform;")
    lines.append("                        break;")
    lines.append("                    }")
    lines.append("                    default:")
    lines.append("                        throw new InvalidOperationException($\"Unsupported UiNode type: {Type}\");")
    lines.append("                }")
    lines.append("")
    lines.append("                ApplyVisualStyle(element);")
    lines.append("                ApplyChildAutoSizing(resolvedWidth, resolvedHeight);")
    lines.append("")
    lines.append("                foreach (var child in Children)")
    lines.append("                    child.Build(childParent);")
    lines.append("")
    lines.append("                return transform;")
    lines.append("            }")
    lines.append("")
    lines.append("            private int ResolveWidth(Transform parent)")
    lines.append("            {")
    lines.append("                if (_allocatedWidth != null)")
    lines.append("                    return Math.Max(1, _allocatedWidth.Value);")
    lines.append("                if (Type == UiNodeType.Window || Type == UiNodeType.ClosableWindow)")
    lines.append("                {")
    lines.append("                    var options = GeneratedLayout._activeOptions;")
    lines.append("                    if (options?.Width != null)")
    lines.append("                        return Math.Max(1, options.Width.Value);")
    lines.append("                }")
    lines.append("")
    lines.append("                return ResolveAxisSize(parent, true, WidthMode, Width);")
    lines.append("            }")
    lines.append("")
    lines.append("            private int ResolveHeight(Transform parent)")
    lines.append("            {")
    lines.append("                if (_allocatedHeight != null)")
    lines.append("                    return Math.Max(1, _allocatedHeight.Value);")
    lines.append("                if (Type == UiNodeType.Window || Type == UiNodeType.ClosableWindow)")
    lines.append("                {")
    lines.append("                    var options = GeneratedLayout._activeOptions;")
    lines.append("                    if (options?.Height != null)")
    lines.append("                        return Math.Max(1, options.Height.Value);")
    lines.append("                }")
    lines.append("")
    lines.append("                return ResolveAxisSize(parent, false, HeightMode, Height);")
    lines.append("            }")
    lines.append("")
    lines.append("            private int ResolveAxisSize(Transform parent, bool isWidthAxis, string mode, int manualSize)")
    lines.append("            {")
    lines.append("                var clampedManual = Math.Max(1, manualSize);")
    lines.append("                if (!string.Equals(mode, \"Auto\", StringComparison.Ordinal))")
    lines.append("                    return clampedManual;")
    lines.append("")
    lines.append("                var parentRect = parent as RectTransform;")
    lines.append("                if (parentRect == null)")
    lines.append("                    return clampedManual;")
    lines.append("")
    lines.append("                var available = isWidthAxis ? parentRect.rect.width : parentRect.rect.height;")
    lines.append("                var layoutGroup = parent.GetComponent<HorizontalOrVerticalLayoutGroup>();")
    lines.append("                if (layoutGroup != null)")
    lines.append("                {")
    lines.append("                    var axisPadding = isWidthAxis")
    lines.append("                        ? layoutGroup.padding.left + layoutGroup.padding.right")
    lines.append("                        : layoutGroup.padding.top + layoutGroup.padding.bottom;")
    lines.append("                    available = Math.Max(1f, available - axisPadding);")
    lines.append("")
    lines.append("                    var siblingCount = Math.Max(1, SiblingCount);")
    lines.append("                    var totalSpacing = layoutGroup.spacing * Math.Max(0, siblingCount - 1);")
    lines.append("                    var isPrimaryAxis = (isWidthAxis && layoutGroup is HorizontalLayoutGroup)")
    lines.append("                        || (!isWidthAxis && layoutGroup is VerticalLayoutGroup);")
    lines.append("                    if (isPrimaryAxis)")
    lines.append("                        available = Math.Max(1f, (available - totalSpacing) / siblingCount);")
    lines.append("                }")
    lines.append("")
    lines.append("                return Math.Max(1, (int)Math.Round(available));")
    lines.append("            }")
    lines.append("")
    lines.append("            private void ApplyChildAutoSizing(int parentWidth, int parentHeight)")
    lines.append("            {")
    lines.append("                if (Children.Count == 0)")
    lines.append("                    return;")
    lines.append("")
    lines.append("                var innerWidth = Math.Max(1, parentWidth - PaddingLeft - PaddingRight);")
    lines.append("                var innerHeight = Math.Max(1, parentHeight - PaddingTop - PaddingBottom);")
    lines.append("                var horizontal = string.Equals(Layout, \"Horizontal\", StringComparison.OrdinalIgnoreCase);")
    lines.append("                var spacingTotal = Math.Max(0, Children.Count - 1) * Math.Max(0, Spacing);")
    lines.append("")
    lines.append("                if (horizontal)")
    lines.append("                {")
    lines.append("                    var primaryAvailable = Math.Max(1, innerWidth - spacingTotal);")
    lines.append("                    var manualTotal = 0;")
    lines.append("                    var autoCount = 0;")
    lines.append("                    for (var i = 0; i < Children.Count; i++)")
    lines.append("                    {")
    lines.append("                        var child = Children[i];")
    lines.append("                        if (string.Equals(child.WidthMode, \"Auto\", StringComparison.Ordinal))")
    lines.append("                            autoCount += 1;")
    lines.append("                        else")
    lines.append("                            manualTotal += Math.Max(1, child.Width);")
    lines.append("                    }")
    lines.append("")
    lines.append("                    var remaining = Math.Max(1, primaryAvailable - manualTotal);")
    lines.append("                    var autoPrimary = autoCount > 0 ? Math.Max(1, remaining / autoCount) : 0;")
    lines.append("                    for (var i = 0; i < Children.Count; i++)")
    lines.append("                    {")
    lines.append("                        var child = Children[i];")
    lines.append("                        child._allocatedWidth = string.Equals(child.WidthMode, \"Auto\", StringComparison.Ordinal) ? autoPrimary : null;")
    lines.append("                        child._allocatedHeight = string.Equals(child.HeightMode, \"Auto\", StringComparison.Ordinal) ? innerHeight : null;")
    lines.append("                    }")
    lines.append("                    return;")
    lines.append("                }")
    lines.append("")
    lines.append("                var primaryHeightAvailable = Math.Max(1, innerHeight - spacingTotal);")
    lines.append("                var manualHeightTotal = 0;")
    lines.append("                var autoHeightCount = 0;")
    lines.append("                for (var i = 0; i < Children.Count; i++)")
    lines.append("                {")
    lines.append("                    var child = Children[i];")
    lines.append("                    if (string.Equals(child.HeightMode, \"Auto\", StringComparison.Ordinal))")
    lines.append("                        autoHeightCount += 1;")
    lines.append("                    else")
    lines.append("                        manualHeightTotal += Math.Max(1, child.Height);")
    lines.append("                }")
    lines.append("")
    lines.append("                var remainingHeight = Math.Max(1, primaryHeightAvailable - manualHeightTotal);")
    lines.append("                var autoHeight = autoHeightCount > 0 ? Math.Max(1, remainingHeight / autoHeightCount) : 0;")
    lines.append("                for (var i = 0; i < Children.Count; i++)")
    lines.append("                {")
    lines.append("                    var child = Children[i];")
    lines.append("                    child._allocatedWidth = string.Equals(child.WidthMode, \"Auto\", StringComparison.Ordinal) ? innerWidth : null;")
    lines.append("                    child._allocatedHeight = string.Equals(child.HeightMode, \"Auto\", StringComparison.Ordinal) ? autoHeight : null;")
    lines.append("                }")
    lines.append("            }")
    lines.append("")
    lines.append("            private void ConfigureLayout(Window window)")
    lines.append("            {")
    lines.append("                window.CreateLayoutGroup(ParseLayoutType(Layout), ParseTextAnchor(ChildAlignment), Spacing, new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);")
    lines.append("            }")
    lines.append("")
    lines.append("            private void ConfigureLayout(Container container)")
    lines.append("            {")
    lines.append("                container.CreateLayoutGroup(ParseLayoutType(Layout), ParseTextAnchor(ChildAlignment), Spacing, new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);")
    lines.append("            }")
    lines.append("")
    lines.append("            private void ConfigureLayout(Box box)")
    lines.append("            {")
    lines.append("                box.CreateLayoutGroup(ParseLayoutType(Layout), ParseTextAnchor(ChildAlignment), Spacing, new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);")
    lines.append("            }")
    lines.append("")
    lines.append("")
    lines.append("            private void ApplyVisualStyle(object element)")
    lines.append("            {")
    lines.append("                if (TextColorOverride && TryParseColor(TextColor, out var textColor))")
    lines.append("                {")
    lines.append("                    TrySetColorProperty(element, \"TextColor\", textColor);")
    lines.append("                    TrySetColorProperty(element, \"TitleColor\", textColor);")
    lines.append("                }")
    lines.append("")
    lines.append("                if (BackgroundColorOverride && TryParseColor(BackgroundColor, out var backgroundColor))")
    lines.append("                {")
    lines.append("                    TrySetColorProperty(element, \"WindowColor\", backgroundColor);")
    lines.append("                    TrySetColorProperty(element, \"FieldColor\", backgroundColor);")
    lines.append("                    TrySetColorProperty(element, \"Color\", backgroundColor);")
    lines.append("                }")
    lines.append("")
    lines.append("                TrySetEnumProperty(element, \"TextAlignment\", TextAlignment);")
    lines.append("            }")
    lines.append("")
    lines.append("            private static bool TryParseColor(string value, out Color color)")
    lines.append("            {")
    lines.append("                color = default;")
    lines.append("                return !string.IsNullOrWhiteSpace(value) && ColorUtility.TryParseHtmlString(value, out color);")
    lines.append("            }")
    lines.append("")
    lines.append("            private static void TrySetColorProperty(object target, string propertyName, Color color)")
    lines.append("            {")
    lines.append("                var property = target.GetType().GetProperty(propertyName);")
    lines.append("                if (property != null && property.CanWrite && property.PropertyType == typeof(Color))")
    lines.append("                    property.SetValue(target, color, null);")
    lines.append("            }")
    lines.append("")
    lines.append("            private static void TrySetEnumProperty(object target, string propertyName, string value)")
    lines.append("            {")
    lines.append("                var property = target.GetType().GetProperty(propertyName);")
    lines.append("                if (property == null || !property.CanWrite || !property.PropertyType.IsEnum)")
    lines.append("                    return;")
    lines.append("                try")
    lines.append("                {")
    lines.append("                    var enumValue = Enum.Parse(property.PropertyType, value, true);")
    lines.append("                    property.SetValue(target, enumValue, null);")
    lines.append("                }")
    lines.append("                catch")
    lines.append("                {")
    lines.append("                }")
    lines.append("            }")
    lines.append("")
    lines.append("            private static void ClampRectTransformToScreen(RectTransform rectTransform)")
    lines.append("            {")
    lines.append("                var corners = new Vector3[4];")
    lines.append("                rectTransform.GetWorldCorners(corners);")
    lines.append("")
    lines.append("                var minX = Mathf.Min(corners[0].x, corners[2].x);")
    lines.append("                var minY = Mathf.Min(corners[0].y, corners[2].y);")
    lines.append("                var maxX = Mathf.Max(corners[0].x, corners[2].x);")
    lines.append("                var maxY = Mathf.Max(corners[0].y, corners[2].y);")
    lines.append("")
    lines.append("                var deltaX = 0f;")
    lines.append("                var deltaY = 0f;")
    lines.append("                if (minX < 0f)")
    lines.append("                    deltaX = -minX;")
    lines.append("                else if (maxX > Screen.width)")
    lines.append("                    deltaX = Screen.width - maxX;")
    lines.append("")
    lines.append("                if (minY < 0f)")
    lines.append("                    deltaY = -minY;")
    lines.append("                else if (maxY > Screen.height)")
    lines.append("                    deltaY = Screen.height - maxY;")
    lines.append("")
    lines.append("                if (Mathf.Abs(deltaX) < 0.01f && Mathf.Abs(deltaY) < 0.01f)")
    lines.append("                    return;")
    lines.append("")
    lines.append("                rectTransform.position += new Vector3(deltaX, deltaY, 0f);")
    lines.append("            }")
    lines.append("")
    lines.append("            private static SFS.UI.ModGUI.Type ParseLayoutType(string value)")
    lines.append("            {")
    lines.append("                // Parse layout direction with explicit default behavior.")
    lines.append("")
    lines.append("                if (string.Equals(value, \"Horizontal\", StringComparison.OrdinalIgnoreCase))")
    lines.append("                    return SFS.UI.ModGUI.Type.Horizontal;")
    lines.append("")
    lines.append("                return SFS.UI.ModGUI.Type.Vertical;")
    lines.append("            }")
    lines.append("")
    lines.append("            private static TextAnchor ParseTextAnchor(string value)")
    lines.append("            {")
    lines.append("                // Parse text anchor names into Unity TextAnchor values.")
    lines.append("")
    lines.append("                switch (value)")
    lines.append("                {")
    lines.append("                    case \"UpperCenter\":")
    lines.append("                        return TextAnchor.UpperCenter;")
    lines.append("                    case \"UpperRight\":")
    lines.append("                        return TextAnchor.UpperRight;")
    lines.append("                    case \"MiddleLeft\":")
    lines.append("                        return TextAnchor.MiddleLeft;")
    lines.append("                    case \"MiddleCenter\":")
    lines.append("                        return TextAnchor.MiddleCenter;")
    lines.append("                    case \"MiddleRight\":")
    lines.append("                        return TextAnchor.MiddleRight;")
    lines.append("                    case \"LowerLeft\":")
    lines.append("                        return TextAnchor.LowerLeft;")
    lines.append("                    case \"LowerCenter\":")
    lines.append("                        return TextAnchor.LowerCenter;")
    lines.append("                    case \"LowerRight\":")
    lines.append("                        return TextAnchor.LowerRight;")
    lines.append("                    default:")
    lines.append("                        return TextAnchor.UpperLeft;")
    lines.append("                }")
    lines.append("            }")
    lines.append("")
    lines.append("            private static int DeterministicId(string id)")
    lines.append("            {")
    lines.append("                unchecked")
    lines.append("                {")
    lines.append("                    var hash = 23;")
    lines.append("                    for (var i = 0; i < id.Length; i++)")
    lines.append("                        hash = hash * 31 + id[i];")
    lines.append("                    return Math.Abs(hash);")
    lines.append("                }")
    lines.append("            }")
    lines.append("        }")
    return lines


def _collect_nodes_preorder(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Collect nodes in stable parent-before-child traversal order.

    ordered_nodes: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        ordered_nodes.append(node)
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child)

    for root in roots:
        walk(root)

    return ordered_nodes


def _build_export_id_map(ordered_nodes: list[dict[str, Any]]) -> dict[str, str]:
    # Map internal IDs to compact element IDs for readable C# export.

    export_id_map: dict[str, str] = {}
    for index, node in enumerate(ordered_nodes, start=1):
        node_id = str(node.get("id", ""))
        export_id_map[node_id] = f"element_{index}"
    return export_id_map


def _build_node_initializer_lines(
    node: dict[str, Any],
    export_id_map: dict[str, str],
    indent: int,
    trailing_comma: bool,
    sibling_count: int,
) -> list[str]:
    # Emit one fluent monadic node expression with chainable configuration.

    node_id = str(node.get("id", ""))
    export_id = _escape_csharp_string(export_id_map[node_id])

    lines: list[str] = []
    pad = " " * indent
    child_pad = " " * (indent + 4)

    node_type_enum = _to_node_type_enum(str(node.get("type", "Container")))
    name_text = _escape_csharp_string(str(node.get("name", "Node")))
    text_text = _escape_csharp_string(str(node.get("text", "")))
    text_alignment = _escape_csharp_string(str(node.get("text_alignment", "Left")))
    text_color = _escape_csharp_string(str(node.get("text_color", "#ffffff")))
    background_color = _escape_csharp_string(str(node.get("background_color", "")))
    border_color = _escape_csharp_string(str(node.get("border_color", "")))
    layout = _escape_csharp_string(str(node.get("layout", "Vertical")))
    child_alignment = _escape_csharp_string(str(node.get("child_alignment", "UpperLeft")))
    props_json = _escape_csharp_string(json.dumps(node.get("props", {}), separators=(",", ":"), ensure_ascii=True))

    x = _as_int(node.get("x", 0), "x")
    y = _as_int(node.get("y", 0), "y")
    width = _as_int(node.get("width", 0), "width")
    height = _as_int(node.get("height", 0), "height")
    spacing = _as_int(node.get("spacing", 12), "spacing")
    padding_left = _as_int(node.get("padding_left", 12), "padding_left")
    padding_right = _as_int(node.get("padding_right", 12), "padding_right")
    padding_top = _as_int(node.get("padding_top", 12), "padding_top")
    padding_bottom = _as_int(node.get("padding_bottom", 12), "padding_bottom")

    text_color_override = _bool_token(_as_bool(node.get("text_color_override", False), "text_color_override"))
    background_color_override = _bool_token(_as_bool(node.get("background_color_override", False), "background_color_override"))
    multiline = _bool_token(_as_bool(node.get("multiline", False), "multiline"))
    width_mode = _escape_csharp_string(_normalize_size_mode(node.get("width_mode", _legacy_full_flag_to_mode(node.get("full_width", True))), "width_mode"))
    height_mode = _escape_csharp_string(_normalize_size_mode(node.get("height_mode", _legacy_full_flag_to_mode(node.get("full_height", False))), "height_mode"))
    scroll_vertical = _bool_token(_as_bool(node.get("scroll_vertical", False), "scroll_vertical"))
    scroll_horizontal = _bool_token(_as_bool(node.get("scroll_horizontal", False), "scroll_horizontal"))

    lines.append(f'{pad}Node("{export_id}", UiNodeType.{node_type_enum}, "{name_text}", {width}, {height})')

    if x != 0 or y != 0:
        lines.append(f"{child_pad}.At({x}, {y})")
    if text_text != "":
        lines.append(f'{child_pad}.WithText("{text_text}")')

    has_visual_change = (
        text_alignment != "Left"
        or multiline != "false"
        or text_color_override != "false"
        or text_color != "#ffffff"
        or background_color_override != "false"
        or background_color != ""
        or border_color != ""
    )
    if has_visual_change:
        lines.append(
            f'{child_pad}.Visual("{text_alignment}", {multiline}, {text_color_override}, "{text_color}", {background_color_override}, "{background_color}", "{border_color}")'
        )

    has_layout_change = (
        layout != "Vertical"
        or child_alignment != "UpperLeft"
        or spacing != 12
        or padding_left != 12
        or padding_right != 12
        or padding_top != 12
        or padding_bottom != 12
    )
    if has_layout_change:
        lines.append(
            f'{child_pad}.LayoutConfig("{layout}", "{child_alignment}", {spacing}, {padding_left}, {padding_right}, {padding_top}, {padding_bottom})'
        )

    if width_mode != "Manual" or height_mode != "Manual" or sibling_count > 1:
        lines.append(f'{child_pad}.Sizing("{width_mode}", "{height_mode}", {sibling_count})')

    if scroll_vertical != "false" or scroll_horizontal != "false":
        lines.append(f"{child_pad}.Scroll({scroll_vertical}, {scroll_horizontal})")
    if props_json != "{}":
        lines.append(f'{child_pad}.Props("{props_json}")')

    children = node.get("children", [])
    if isinstance(children, list) and len(children) > 0:
        child_sibling_count = len(children)
        lines.append(f"{child_pad}.AddChildren(")
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            suffix = "," if index < len(children) - 1 else ""
            child_lines = _build_node_initializer_lines(child, export_id_map, indent + 8, trailing_comma=False, sibling_count=child_sibling_count)
            if suffix and len(child_lines) > 0:
                child_lines[-1] = child_lines[-1] + suffix
            lines.extend(child_lines)
        lines.append(f"{child_pad})")

    if trailing_comma and len(lines) > 0:
        lines[-1] = lines[-1] + ","

    return lines


def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Normalize and validate the snapshot so generated output remains deterministic.

    roots_value = snapshot.get("roots")
    if not isinstance(roots_value, list):
        raise ValueError("Snapshot is missing a roots list.")

    roots = [_normalize_node(root, depth=0) for root in roots_value]

    return {
        "schemaVersion": "1.0.0",
        "roots": roots,
    }


def _normalize_node(node: Any, depth: int) -> dict[str, Any]:
    # Normalize one node, preserving unknown keys and explicit behavior flags.

    if not isinstance(node, dict):
        raise ValueError("Snapshot contains a non-object node.")

    required = {"id", "type", "name", "width", "height", "children"}
    missing = [name for name in sorted(required) if name not in node]
    if missing:
        raise ValueError(f"Snapshot node missing fields: {missing}")

    node_type = str(node["type"])
    if node_type == "":
        raise ValueError("Snapshot node type cannot be empty.")

    is_root = depth == 0
    is_window = node_type == "Window"
    x_value = _as_int(node.get("x", 0), "x")
    y_value = _as_int(node.get("y", 0), "y")
    x = x_value if is_root or is_window else 0
    y = y_value if is_root or is_window else 0

    width = _as_int(node["width"], "width")
    height = _as_int(node["height"], "height")

    legacy_padding = _as_int(node.get("padding", 12), "padding")
    padding_left = _as_int(node.get("padding_left", legacy_padding), "padding_left")
    padding_right = _as_int(node.get("padding_right", legacy_padding), "padding_right")
    padding_top = _as_int(node.get("padding_top", legacy_padding), "padding_top")
    padding_bottom = _as_int(node.get("padding_bottom", legacy_padding), "padding_bottom")

    props_value = node.get("props", {})
    if not isinstance(props_value, dict):
        raise ValueError(f"Node {node.get('id', '?')} props must be an object.")

    children_value = node.get("children")
    if not isinstance(children_value, list):
        raise ValueError(f"Node {node.get('id', '?')} children must be a list.")

    children = [_normalize_node(child, depth + 1) for child in children_value]

    normalized: dict[str, Any] = {
        "id": str(node["id"]),
        "type": node_type,
        "name": str(node["name"]),
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "text": str(node.get("text", "")),
        "text_alignment": str(node.get("text_alignment", "Left")),
        "text_color_override": _as_bool(node.get("text_color_override", False), "text_color_override"),
        "text_color": str(node.get("text_color", "#ffffff")),
        "background_color_override": _as_bool(node.get("background_color_override", False), "background_color_override"),
        "background_color": str(node.get("background_color", "")),
        "border_color": str(node.get("border_color", "")),
        "multiline": _as_bool(node.get("multiline", False), "multiline"),
        "layout": str(node.get("layout", "Vertical")),
        "child_alignment": str(node.get("child_alignment", "UpperLeft")),
        "spacing": _as_int(node.get("spacing", 12), "spacing"),
        "padding": padding_left,
        "padding_left": padding_left,
        "padding_right": padding_right,
        "padding_top": padding_top,
        "padding_bottom": padding_bottom,
        "width_mode": _normalize_size_mode(node.get("width_mode", _legacy_full_flag_to_mode(node.get("full_width", True))), "width_mode"),
        "height_mode": _normalize_size_mode(node.get("height_mode", _legacy_full_flag_to_mode(node.get("full_height", False))), "height_mode"),
        "scroll_vertical": _as_bool(node.get("scroll_vertical", False), "scroll_vertical"),
        "scroll_horizontal": _as_bool(node.get("scroll_horizontal", False), "scroll_horizontal"),
        "props": _normalize_props(props_value),
        "children": children,
    }

    extras = {
        key: value
        for key, value in node.items()
        if key not in KNOWN_NODE_ORDER and key not in {"schemaVersion", "roots"}
    }

    for key in sorted(extras):
        normalized[key] = extras[key]

    if node_type not in CONTAINER_TYPES:
        normalized["layout"] = str(node.get("layout", "Vertical"))
        normalized["child_alignment"] = str(node.get("child_alignment", "UpperLeft"))

    return normalized


def _normalize_props(props: dict[str, Any]) -> dict[str, Any]:
    # Keep props deterministic while preserving unknown/custom values.

    normalized: dict[str, Any] = {}
    for key in sorted(props):
        normalized[str(key)] = props[key]
    return normalized


def _materialize_export_sizes(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Resolve auto sizing to concrete widths/heights using parent size, padding, spacing, and sibling count.

    def node_with_resolved_modes(
        node: dict[str, Any],
        parent_size: Optional[tuple[int, int]],
        parent_layout: Optional[str],
        parent_spacing: int,
        parent_padding: Optional[tuple[int, int, int, int]],
        sibling_count: int,
    ) -> dict[str, Any]:
        resolved = dict(node)
        width = int(resolved.get("width", 1))
        height = int(resolved.get("height", 1))
        width_mode = str(resolved.get("width_mode", SIZE_MODE_MANUAL))
        height_mode = str(resolved.get("height_mode", SIZE_MODE_MANUAL))

        if parent_size is not None and parent_padding is not None and sibling_count > 0:
            parent_width, parent_height = parent_size
            pad_left, pad_right, pad_top, pad_bottom = parent_padding
            available_width = max(1, parent_width - pad_left - pad_right)
            available_height = max(1, parent_height - pad_top - pad_bottom)
            layout_name = (parent_layout or "Vertical").strip().lower()

            primary_width = available_width
            primary_height = available_height
            if layout_name == "horizontal":
                spacing_total = max(0, sibling_count - 1) * max(0, parent_spacing)
                primary_width = max(1, int(round((available_width - spacing_total) / sibling_count)))
            else:
                spacing_total = max(0, sibling_count - 1) * max(0, parent_spacing)
                primary_height = max(1, int(round((available_height - spacing_total) / sibling_count)))

            if width_mode == SIZE_MODE_AUTO:
                width = primary_width if layout_name == "horizontal" else available_width
                width_mode = SIZE_MODE_MANUAL

            if height_mode == SIZE_MODE_AUTO:
                height = primary_height if layout_name != "horizontal" else available_height
                height_mode = SIZE_MODE_MANUAL

        resolved["width"] = max(1, width)
        resolved["height"] = max(1, height)
        resolved["width_mode"] = width_mode
        resolved["height_mode"] = height_mode

        children_in = resolved.get("children", [])
        children_out: list[dict[str, Any]] = []
        if isinstance(children_in, list):
            child_count = len(children_in)
            child_parent_layout = str(resolved.get("layout", "Vertical"))
            child_parent_spacing = int(resolved.get("spacing", 12))
            child_parent_padding = (
                int(resolved.get("padding_left", 12)),
                int(resolved.get("padding_right", 12)),
                int(resolved.get("padding_top", 12)),
                int(resolved.get("padding_bottom", 12)),
            )
            child_parent_size = (resolved["width"], resolved["height"])

            for child in children_in:
                if not isinstance(child, dict):
                    continue
                child_out = node_with_resolved_modes(
                    dict(child),
                    child_parent_size,
                    child_parent_layout,
                    child_parent_spacing,
                    child_parent_padding,
                    child_count,
                )
                children_out.append(child_out)

        resolved["children"] = children_out
        return resolved

    output: list[dict[str, Any]] = []
    for root in roots:
        if isinstance(root, dict):
            output.append(node_with_resolved_modes(root, None, None, 0, None, 0))

    return output


def _legacy_full_flag_to_mode(value: Any) -> str:
    # Translate legacy full-size booleans into explicit axis sizing modes.

    return SIZE_MODE_AUTO if bool(value) else SIZE_MODE_MANUAL


def _normalize_size_mode(value: Any, field_name: str) -> str:
    # Validate exported size mode values.

    mode = str(value).strip()
    if mode in {"NativeAuto", "UseParentSize"}:
        return SIZE_MODE_AUTO

    if mode in {SIZE_MODE_MANUAL, SIZE_MODE_AUTO}:
        return mode

    raise ValueError(f"Field {field_name} must be one of: Manual, Auto.")


def _build_hierarchy_lines(roots: list[dict[str, Any]], export_id_map: dict[str, str]) -> list[str]:
    # Render a deterministic hierarchy preview with compact exported element IDs.

    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        indent = "  " * depth
        node_type = str(node.get("type", "Container"))
        node_name = str(node.get("name", "Node"))
        node_id = str(node.get("id", ""))
        export_id = export_id_map.get(node_id, node_id)
        lines.append(f"{indent}- {node_type}: {node_name} ({export_id})")
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child, depth + 1)

    for root in roots:
        walk(root, depth=0)

    return lines


def _to_node_type_enum(value: str) -> str:
    # Convert serialized node type tokens to generated UiNodeType enum members.

    normalized = value.strip()
    allowed = {
        "Window",
        "ClosableWindow",
        "Container",
        "Box",
        "Label",
        "Button",
        "TextInput",
        "Toggle",
        "Slider",
        "Separator",
        "Space",
        "NumberInput",
    }
    if normalized in allowed:
        return normalized

    raise ValueError(f"Unsupported node type for export: {value}")


def _escape_csharp_string(value: str) -> str:
    # Escape C# literal content for generated object initializers.

    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _bool_token(value: bool) -> str:
    # Emit lowercase bool literals for generated C# code.

    return "true" if value else "false"


def _as_int(value: Any, field_name: str) -> int:
    # Convert numeric values deterministically and fail fast for invalid types.

    if isinstance(value, bool):
        raise ValueError(f"Field {field_name} cannot be bool.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as ex:
            raise ValueError(f"Field {field_name} must be an integer value.") from ex
    raise ValueError(f"Field {field_name} must be an integer value.")


def _as_bool(value: Any, field_name: str) -> bool:
    # Convert booleans explicitly without relying on truthy fallbacks.

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
    raise ValueError(f"Field {field_name} must be a boolean value.")


def _count_nodes(roots: list[dict[str, Any]]) -> int:
    # Count nodes deterministically for export metadata and verification.

    total = 0

    def walk(node: dict[str, Any]) -> None:
        nonlocal total
        total += 1
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child)

    for root in roots:
        walk(root)

    return total
