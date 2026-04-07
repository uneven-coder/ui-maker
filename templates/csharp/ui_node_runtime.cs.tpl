        private static GameObject? _activeHolder;
        private static Window? _activeWindow;
        private static RenderOptions? _activeOptions;

        public sealed class RenderOptions
        {
            public Builder.SceneToAttach SceneToAttach { get; set; } = Builder.SceneToAttach.CurrentScene;
            public string HolderName { get; set; } = "GeneratedUIHolder";
            public bool WindowDraggable { get; set; }
            public WindowRenderMode WindowMode { get; set; } = WindowRenderMode.AsDefined;
            public int? Width { get; set; }
            public int? Height { get; set; }
            public bool StartHidden { get; set; }
            public bool RemoveExistingBeforeRender { get; set; } = true;
        }

        public enum WindowRenderMode
        {
            AsDefined,
            ForceNormal,
            ForceClosable,
        }

        public enum UiNodeType
        {
            Window,
            ClosableWindow,
            Container,
            Box,
            Label,
            Button,
            ButtonWithLabel,
            TextInput,
            InputWithLabel,
            Toggle,
            ToggleWithLabel,
            Slider,
            Separator,
            Space,
            NumberInput,
        }

        public enum UiSizeMode
        {
            Manual,
            Auto,
        }

        public static Window? Render(RenderOptions? options = null)
        {
            // Render full UI tree into a managed holder.

            options ??= new RenderOptions();
            if (options.RemoveExistingBeforeRender)
                Remove();
            _activeOptions = options;

            _activeHolder = Builder.CreateHolder(options.SceneToAttach, options.HolderName);
            Transform parent = _activeHolder.transform;
            _activeWindow = null;

            foreach (var node in Define())
                node.Build(parent);

            if (options.StartHidden && _activeHolder != null)
                _activeHolder.SetActive(false);

            return _activeWindow;
        }

        public static void Render(Window window)
        {
            // Keep compatibility with existing call sites that pass a Window.

            _ = window;
            Render((RenderOptions?)null);
        }

        public static void Render(Transform parent)
        {
            // Render definitions directly under an existing parent transform.

            _activeOptions = null;
            foreach (var node in Define())
                node.Build(parent);
        }

        public static void Hide()
        {
            // Hide generated holder without destroying it.

            if (_activeHolder != null)
                _activeHolder.SetActive(false);
        }

        public static void Show()
        {
            // Show generated holder if it exists.

            if (_activeHolder != null)
                _activeHolder.SetActive(true);
        }

        public static void Remove()
        {
            // Destroy generated holder and clear static render state.

            if (_activeHolder != null)
            {
                UnityEngine.Object.Destroy(_activeHolder);
                _activeHolder = null;
            }
            _activeWindow = null;
            _activeOptions = null;
        }

        private static bool ResolveClosableWindowMode(UiNodeType nodeType)
        {
            // Resolve whether a window node should render as ClosableWindow.

            var mode = _activeOptions?.WindowMode ?? WindowRenderMode.AsDefined;
            switch (mode)
            {
                case WindowRenderMode.ForceClosable:
                    return nodeType == UiNodeType.Window || nodeType == UiNodeType.ClosableWindow;
                case WindowRenderMode.ForceNormal:
                    return false;
                default:
                    return nodeType == UiNodeType.ClosableWindow;
            }
        }

        private static UiNode Node(string id, UiNodeType type, string name, int width, int height)
        {
            // Create one fluent node with required identity and dimensions.

            return new UiNode
            {
                Id = id,
                Type = type,
                Name = name,
                Width = width,
                Height = height,
            };
        }

        public sealed class UiNode
        {
            public string Id { get; set; } = string.Empty;
            public UiNodeType Type { get; set; } = UiNodeType.Container;
            public string Name { get; set; } = string.Empty;
            public int X { get; set; }
            public int Y { get; set; }
            public int Width { get; set; }
            public int Height { get; set; }
            public string Text { get; set; } = string.Empty;
            public TextAnchor TextAlignment { get; set; } = TextAnchor.MiddleLeft;
            public bool TextColorOverride { get; set; }
            public string TextColor { get; set; } = "#ffffff";
            public bool BackgroundColorOverride { get; set; }
            public string BackgroundColor { get; set; } = string.Empty;
            public string BorderColor { get; set; } = string.Empty;
            public bool Multiline { get; set; }
            public SFS.UI.ModGUI.Type Layout { get; set; } = SFS.UI.ModGUI.Type.Vertical;
            public TextAnchor ChildAlignment { get; set; } = TextAnchor.UpperLeft;
            public int Spacing { get; set; } = 12;
            public int PaddingLeft { get; set; } = 12;
            public int PaddingRight { get; set; } = 12;
            public int PaddingTop { get; set; } = 12;
            public int PaddingBottom { get; set; } = 12;
            public UiSizeMode WidthMode { get; set; } = UiSizeMode.Manual;
            public UiSizeMode HeightMode { get; set; } = UiSizeMode.Manual;
            public int SiblingCount { get; set; } = 1;
            public bool ScrollVertical { get; set; }
            public bool ScrollHorizontal { get; set; }
            public string PropsJson { get; set; } = "{}";
            public string LabelDirection { get; set; } = "Top";
            public string LabelText { get; set; } = string.Empty;
            public string ControlText { get; set; } = string.Empty;
            public List<UiNode> Children { get; set; } = new List<UiNode>();
            private int? _allocatedWidth;
            private int? _allocatedHeight;

            public UiNode At(int x, int y)
            {
                X = x;
                Y = y;
                return this;
            }

            public UiNode WithText(string text)
            {
                Text = text;
                return this;
            }

            public UiNode Visual(TextAnchor textAlignment, bool multiline, bool textColorOverride, string textColor, bool backgroundColorOverride, string backgroundColor, string borderColor)
            {
                TextAlignment = textAlignment;
                Multiline = multiline;
                TextColorOverride = textColorOverride;
                TextColor = textColor;
                BackgroundColorOverride = backgroundColorOverride;
                BackgroundColor = backgroundColor;
                BorderColor = borderColor;
                return this;
            }

            public UiNode LayoutConfig(SFS.UI.ModGUI.Type layout, TextAnchor childAlignment, int spacing, int paddingLeft, int paddingRight, int paddingTop, int paddingBottom)
            {
                Layout = layout;
                ChildAlignment = childAlignment;
                Spacing = spacing;
                PaddingLeft = paddingLeft;
                PaddingRight = paddingRight;
                PaddingTop = paddingTop;
                PaddingBottom = paddingBottom;
                return this;
            }

            public UiNode Sizing(UiSizeMode widthMode, UiSizeMode heightMode, int siblingCount)
            {
                WidthMode = widthMode;
                HeightMode = heightMode;
                SiblingCount = Math.Max(1, siblingCount);
                return this;
            }

            public UiNode Scroll(bool vertical, bool horizontal)
            {
                ScrollVertical = vertical;
                ScrollHorizontal = horizontal;
                return this;
            }

            public UiNode Props(string propsJson)
            {
                PropsJson = propsJson;
                return this;
            }

            public UiNode LabelPlacement(string direction)
            {
                LabelDirection = direction;
                return this;
            }

            public UiNode LabeledTexts(string labelText, string controlText)
            {
                LabelText = labelText;
                ControlText = controlText;
                return this;
            }

            public UiNode AddChildren(params UiNode[] children)
            {
                if (children == null || children.Length == 0)
                    return this;
                Children.AddRange(children);
                return this;
            }

            public Transform Build(Transform parent)
            {
                var resolvedWidth = ResolveWidth(parent);
                var resolvedHeight = ResolveHeight(parent);
                var draggable = GeneratedLayout._activeOptions != null && GeneratedLayout._activeOptions.WindowDraggable;
                object element;
                Transform transform;
                Transform childParent;

                switch (Type)
                {
                    case UiNodeType.Window:
                    case UiNodeType.ClosableWindow:
                    {
                        if (GeneratedLayout.ResolveClosableWindowMode(Type))
                        {
                            var closable = UIToolsBuilder.CreateClosableWindow(parent, DeterministicId(Id), resolvedWidth, resolvedHeight, X, Y, draggable: draggable, savePosition: false, titleText: string.IsNullOrWhiteSpace(Text) ? Name : Text, minimized: false);
                            if (closable.rectTransform != null)
                                ClampRectTransformToScreen(closable.rectTransform);
                            ConfigureLayout(closable);
                            if (ScrollVertical)
                                closable.EnableScrolling(SFS.UI.ModGUI.Type.Vertical);
                            if (ScrollHorizontal)
                                closable.EnableScrolling(SFS.UI.ModGUI.Type.Horizontal);
                            element = closable;
                            transform = closable.gameObject.transform;
                            childParent = closable.ChildrenHolder;
                        }
                        else
                        {
                            var window = Builder.CreateWindow(parent, DeterministicId(Id), resolvedWidth, resolvedHeight, X, Y, draggable: draggable, savePosition: false, titleText: string.IsNullOrWhiteSpace(Text) ? Name : Text);
                            if (window.rectTransform != null)
                                ClampRectTransformToScreen(window.rectTransform);
                            ConfigureLayout(window);
                            if (ScrollVertical)
                                window.EnableScrolling(SFS.UI.ModGUI.Type.Vertical);
                            if (ScrollHorizontal)
                                window.EnableScrolling(SFS.UI.ModGUI.Type.Horizontal);
                            element = window;
                            transform = window.gameObject.transform;
                            childParent = window.ChildrenHolder;
                        }
                        break;
                    }
                    case UiNodeType.Container:
                    {
                        var container = Builder.CreateContainer(parent, X, Y);
                        container.Size = new Vector2(resolvedWidth, resolvedHeight);
                        DisableContentSizeFitter(container.gameObject.transform);
                        Transform contentParent = container.gameObject.transform;
                        if (ScrollVertical || ScrollHorizontal)
                            contentParent = EnsureScrollableContentHost(container.gameObject.transform, ScrollVertical, ScrollHorizontal);
                        ConfigureLayout(contentParent);
                        ApplyScrolling(container, container.gameObject.transform, ScrollVertical, ScrollHorizontal);
                        element = container;
                        transform = container.gameObject.transform;
                        childParent = contentParent;
                        break;
                    }
                    case UiNodeType.Box:
                    {
                        var box = Builder.CreateBox(parent, resolvedWidth, resolvedHeight, X, Y, opacity: 0.35f);
                        DisableContentSizeFitter(box.gameObject.transform);
                        Transform contentParent = box.gameObject.transform;
                        if (ScrollVertical || ScrollHorizontal)
                            contentParent = EnsureScrollableContentHost(box.gameObject.transform, ScrollVertical, ScrollHorizontal);
                        ConfigureLayout(contentParent);
                        ApplyScrolling(box, box.gameObject.transform, ScrollVertical, ScrollHorizontal);
                        element = box;
                        transform = box.gameObject.transform;
                        childParent = contentParent;
                        break;
                    }
                    case UiNodeType.Label:
                    {
                        var label = Builder.CreateLabel(parent, resolvedWidth, resolvedHeight, X, Y, Text);
                        element = label;
                        transform = label.gameObject.transform;
                        childParent = label.gameObject.transform;
                        break;
                    }
                    case UiNodeType.Button:
                    {
                        var button = Builder.CreateButton(parent, resolvedWidth, resolvedHeight, X, Y, null, Text);
                        element = button;
                        transform = button.gameObject.transform;
                        childParent = button.gameObject.transform;
                        break;
                    }
                    case UiNodeType.ButtonWithLabel:
                    {
                        var holder = Builder.CreateContainer(parent, X, Y);
                        holder.Size = new Vector2(resolvedWidth, resolvedHeight);
                        var labelText = string.IsNullOrWhiteSpace(LabelText) ? Text : LabelText;
                        var controlText = string.IsNullOrWhiteSpace(ControlText) ? Text : ControlText;
                        var labelDirection = NormalizeLabelDirection(LabelDirection, "Top");
                        var horizontal = labelDirection == "Left" || labelDirection == "Right";
                        var reverse = labelDirection == "Right" || labelDirection == "Bottom";
                        holder.CreateLayoutGroup(horizontal ? SFS.UI.ModGUI.Type.Horizontal : SFS.UI.ModGUI.Type.Vertical, horizontal ? TextAnchor.MiddleLeft : TextAnchor.UpperLeft, Math.Max(0, Spacing), new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);
                        var labelHeight = Math.Clamp(resolvedHeight / 3, 18, resolvedHeight);
                        var buttonHeight = Math.Max(18, resolvedHeight - labelHeight - Math.Max(0, Spacing));
                        if (horizontal)
                        {
                            var labelWidth = Math.Clamp(resolvedWidth / 2, 20, Math.Max(20, resolvedWidth));
                            var controlWidth = Math.Max(20, resolvedWidth - labelWidth - Math.Max(0, Spacing));
                            if (reverse)
                            {
                                Builder.CreateButton(holder.gameObject.transform, controlWidth, Math.Max(20, resolvedHeight), 0, 0, null, controlText);
                                Builder.CreateLabel(holder.gameObject.transform, labelWidth, Math.Max(20, resolvedHeight), 0, 0, labelText);
                            }
                            else
                            {
                                Builder.CreateLabel(holder.gameObject.transform, labelWidth, Math.Max(20, resolvedHeight), 0, 0, labelText);
                                Builder.CreateButton(holder.gameObject.transform, controlWidth, Math.Max(20, resolvedHeight), 0, 0, null, controlText);
                            }
                        }
                        else if (reverse)
                        {
                            Builder.CreateButton(holder.gameObject.transform, resolvedWidth, buttonHeight, 0, 0, null, controlText);
                            Builder.CreateLabel(holder.gameObject.transform, resolvedWidth, labelHeight, 0, 0, labelText);
                        }
                        else
                        {
                            Builder.CreateLabel(holder.gameObject.transform, resolvedWidth, labelHeight, 0, 0, labelText);
                            Builder.CreateButton(holder.gameObject.transform, resolvedWidth, buttonHeight, 0, 0, null, controlText);
                        }
                        element = holder;
                        transform = holder.gameObject.transform;
                        childParent = holder.gameObject.transform;
                        break;
                    }
                    case UiNodeType.TextInput:
                    {
                        var input = Builder.CreateTextInput(parent, resolvedWidth, resolvedHeight, X, Y, Text, null);
                        element = input;
                        transform = input.gameObject.transform;
                        childParent = input.gameObject.transform;
                        break;
                    }
                    case UiNodeType.InputWithLabel:
                    {
                        var holder = Builder.CreateContainer(parent, X, Y);
                        holder.Size = new Vector2(resolvedWidth, resolvedHeight);
                        var labelText = string.IsNullOrWhiteSpace(LabelText) ? Text : LabelText;
                        var controlText = string.IsNullOrWhiteSpace(ControlText) ? Text : ControlText;
                        var labelDirection = NormalizeLabelDirection(LabelDirection, "Top");
                        var horizontal = labelDirection == "Left" || labelDirection == "Right";
                        var reverse = labelDirection == "Right" || labelDirection == "Bottom";
                        holder.CreateLayoutGroup(horizontal ? SFS.UI.ModGUI.Type.Horizontal : SFS.UI.ModGUI.Type.Vertical, horizontal ? TextAnchor.MiddleLeft : TextAnchor.UpperLeft, Math.Max(0, Spacing), new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);
                        var labelHeight = Math.Clamp(resolvedHeight / 3, 18, resolvedHeight);
                        var inputHeight = Math.Max(18, resolvedHeight - labelHeight - Math.Max(0, Spacing));
                        if (horizontal)
                        {
                            var labelWidth = Math.Clamp(resolvedWidth / 2, 20, Math.Max(20, resolvedWidth));
                            var controlWidth = Math.Max(20, resolvedWidth - labelWidth - Math.Max(0, Spacing));
                            if (reverse)
                            {
                                Builder.CreateTextInput(holder.gameObject.transform, controlWidth, Math.Max(20, resolvedHeight), 0, 0, controlText, null);
                                Builder.CreateLabel(holder.gameObject.transform, labelWidth, Math.Max(20, resolvedHeight), 0, 0, labelText);
                            }
                            else
                            {
                                Builder.CreateLabel(holder.gameObject.transform, labelWidth, Math.Max(20, resolvedHeight), 0, 0, labelText);
                                Builder.CreateTextInput(holder.gameObject.transform, controlWidth, Math.Max(20, resolvedHeight), 0, 0, controlText, null);
                            }
                        }
                        else if (reverse)
                        {
                            Builder.CreateTextInput(holder.gameObject.transform, resolvedWidth, inputHeight, 0, 0, controlText, null);
                            Builder.CreateLabel(holder.gameObject.transform, resolvedWidth, labelHeight, 0, 0, labelText);
                        }
                        else
                        {
                            Builder.CreateLabel(holder.gameObject.transform, resolvedWidth, labelHeight, 0, 0, labelText);
                            Builder.CreateTextInput(holder.gameObject.transform, resolvedWidth, inputHeight, 0, 0, controlText, null);
                        }
                        element = holder;
                        transform = holder.gameObject.transform;
                        childParent = holder.gameObject.transform;
                        break;
                    }
                    case UiNodeType.Toggle:
                    {
                        var state = false;
                        var toggle = Builder.CreateToggle(parent, () => state, X, Y, () => state = !state);
                        element = toggle;
                        transform = toggle.gameObject.transform;
                        childParent = toggle.gameObject.transform;
                        break;
                    }
                    case UiNodeType.ToggleWithLabel:
                    {
                        var holder = Builder.CreateContainer(parent, X, Y);
                        holder.Size = new Vector2(resolvedWidth, resolvedHeight);
                        var labelText = string.IsNullOrWhiteSpace(LabelText) ? Text : LabelText;
                        var labelDirection = NormalizeLabelDirection(LabelDirection, "Left");
                        var horizontal = labelDirection == "Left" || labelDirection == "Right";
                        var reverse = labelDirection == "Right" || labelDirection == "Bottom";
                        holder.CreateLayoutGroup(horizontal ? SFS.UI.ModGUI.Type.Horizontal : SFS.UI.ModGUI.Type.Vertical, horizontal ? TextAnchor.MiddleLeft : TextAnchor.UpperLeft, Math.Max(0, Spacing), new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);
                        var state = false;
                        if (horizontal)
                        {
                            var labelWidth = Math.Clamp(resolvedWidth / 2, 20, Math.Max(20, resolvedWidth));
                            if (reverse)
                            {
                                Builder.CreateToggle(holder.gameObject.transform, () => state, 0, 0, () => state = !state);
                                Builder.CreateLabel(holder.gameObject.transform, labelWidth, Math.Max(20, resolvedHeight), 0, 0, labelText);
                            }
                            else
                            {
                                Builder.CreateLabel(holder.gameObject.transform, labelWidth, Math.Max(20, resolvedHeight), 0, 0, labelText);
                                Builder.CreateToggle(holder.gameObject.transform, () => state, 0, 0, () => state = !state);
                            }
                        }
                        else if (reverse)
                        {
                            Builder.CreateToggle(holder.gameObject.transform, () => state, 0, 0, () => state = !state);
                            Builder.CreateLabel(holder.gameObject.transform, resolvedWidth, Math.Clamp(resolvedHeight / 3, 18, resolvedHeight), 0, 0, labelText);
                        }
                        else
                        {
                            Builder.CreateLabel(holder.gameObject.transform, resolvedWidth, Math.Clamp(resolvedHeight / 3, 18, resolvedHeight), 0, 0, labelText);
                            Builder.CreateToggle(holder.gameObject.transform, () => state, 0, 0, () => state = !state);
                        }
                        element = holder;
                        transform = holder.gameObject.transform;
                        childParent = holder.gameObject.transform;
                        break;
                    }
                    case UiNodeType.Slider:
                    {
                        var slider = Builder.CreateSlider(parent, resolvedWidth, 0f, (0f, 1f), false, null, null);
                        element = slider;
                        transform = slider.gameObject.transform;
                        childParent = slider.gameObject.transform;
                        break;
                    }
                    case UiNodeType.Separator:
                    {
                        var separator = Builder.CreateSeparator(parent, resolvedWidth, X, Y);
                        element = separator;
                        transform = separator.gameObject.transform;
                        childParent = separator.gameObject.transform;
                        break;
                    }
                    case UiNodeType.Space:
                    {
                        var space = Builder.CreateSpace(parent, resolvedWidth, resolvedHeight);
                        element = space;
                        transform = space.gameObject.transform;
                        childParent = space.gameObject.transform;
                        break;
                    }
                    case UiNodeType.NumberInput:
                    {
                        var number = UIToolsBuilder.CreateNumberInput(parent, resolvedWidth, resolvedHeight, 0f, 1f, X, Y);
                        element = number;
                        transform = number.gameObject.transform;
                        childParent = number.gameObject.transform;
                        break;
                    }
                    default:
                        throw new InvalidOperationException($"Unsupported UiNode type: {Type}");
                }

                ApplyVisualStyle(element);
                ApplyChildAutoSizing(resolvedWidth, resolvedHeight);

                foreach (var child in Children)
                    child.Build(childParent);

                return transform;
            }

            private int ResolveWidth(Transform parent)
            {
                if (_allocatedWidth != null)
                    return Math.Max(1, _allocatedWidth.Value);
                if (Type == UiNodeType.Window || Type == UiNodeType.ClosableWindow)
                {
                    var options = GeneratedLayout._activeOptions;
                    if (options?.Width != null)
                        return Math.Max(1, options.Width.Value);
                }

                return ResolveAxisSize(parent, true, WidthMode, Width);
            }

            private int ResolveHeight(Transform parent)
            {
                if (_allocatedHeight != null)
                    return Math.Max(1, _allocatedHeight.Value);
                if (Type == UiNodeType.Window || Type == UiNodeType.ClosableWindow)
                {
                    var options = GeneratedLayout._activeOptions;
                    if (options?.Height != null)
                        return Math.Max(1, options.Height.Value);
                }

                return ResolveAxisSize(parent, false, HeightMode, Height);
            }

            private int ResolveAxisSize(Transform parent, bool isWidthAxis, UiSizeMode mode, int manualSize)
            {
                var clampedManual = Math.Max(1, manualSize);
                if (mode != UiSizeMode.Auto)
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
                    available = Math.Max(1f, available - axisPadding);

                    var siblingCount = Math.Max(1, SiblingCount);
                    var totalSpacing = layoutGroup.spacing * Math.Max(0, siblingCount - 1);
                    var isPrimaryAxis = (isWidthAxis && layoutGroup is HorizontalLayoutGroup)
                        || (!isWidthAxis && layoutGroup is VerticalLayoutGroup);
                    if (isPrimaryAxis)
                        available = Math.Max(1f, (available - totalSpacing) / siblingCount);
                }

                return Math.Max(1, (int)Math.Round(available));
            }

            private void ApplyChildAutoSizing(int parentWidth, int parentHeight)
            {
                if (Children.Count == 0)
                    return;

                var innerWidth = Math.Max(1, parentWidth - PaddingLeft - PaddingRight);
                var innerHeight = Math.Max(1, parentHeight - PaddingTop - PaddingBottom);
                var horizontal = Layout == SFS.UI.ModGUI.Type.Horizontal;
                var spacingTotal = Math.Max(0, Children.Count - 1) * Math.Max(0, Spacing);

                if (horizontal)
                {
                    var primaryAvailable = Math.Max(1, innerWidth - spacingTotal);
                    var manualTotal = 0;
                    var autoCount = 0;
                    for (var i = 0; i < Children.Count; i++)
                    {
                        var child = Children[i];
                        if (child.WidthMode == UiSizeMode.Auto)
                            autoCount += 1;
                        else
                            manualTotal += Math.Max(1, child.Width);
                    }

                    var remaining = Math.Max(1, primaryAvailable - manualTotal);
                    var autoPrimary = autoCount > 0 ? Math.Max(1, remaining / autoCount) : 0;
                    for (var i = 0; i < Children.Count; i++)
                    {
                        var child = Children[i];
                        child._allocatedWidth = child.WidthMode == UiSizeMode.Auto ? autoPrimary : null;
                        child._allocatedHeight = child.HeightMode == UiSizeMode.Auto ? innerHeight : null;
                    }
                    return;
                }

                var primaryHeightAvailable = Math.Max(1, innerHeight - spacingTotal);
                var manualHeightTotal = 0;
                var autoHeightCount = 0;
                for (var i = 0; i < Children.Count; i++)
                {
                    var child = Children[i];
                    if (child.HeightMode == UiSizeMode.Auto)
                        autoHeightCount += 1;
                    else
                        manualHeightTotal += Math.Max(1, child.Height);
                }

                var remainingHeight = Math.Max(1, primaryHeightAvailable - manualHeightTotal);
                var autoHeight = autoHeightCount > 0 ? Math.Max(1, remainingHeight / autoHeightCount) : 0;
                for (var i = 0; i < Children.Count; i++)
                {
                    var child = Children[i];
                    child._allocatedWidth = child.WidthMode == UiSizeMode.Auto ? innerWidth : null;
                    child._allocatedHeight = child.HeightMode == UiSizeMode.Auto ? autoHeight : null;
                }
            }

            private void ConfigureLayout(Window window)
            {
                window.CreateLayoutGroup(Layout, ChildAlignment, Spacing, new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);
            }

            private void ConfigureLayout(Container container)
            {
                container.CreateLayoutGroup(Layout, ChildAlignment, Spacing, new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);
            }

            private void ConfigureLayout(Box box)
            {
                box.CreateLayoutGroup(Layout, ChildAlignment, Spacing, new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom), true);
            }

            private void ConfigureLayout(Transform target)
            {
                HorizontalOrVerticalLayoutGroup layoutGroup;
                if (Layout == SFS.UI.ModGUI.Type.Horizontal)
                {
                    layoutGroup = target.GetComponent<HorizontalLayoutGroup>();
                    if (layoutGroup == null)
                        layoutGroup = target.gameObject.AddComponent<HorizontalLayoutGroup>();
                }
                else
                {
                    layoutGroup = target.GetComponent<VerticalLayoutGroup>();
                    if (layoutGroup == null)
                        layoutGroup = target.gameObject.AddComponent<VerticalLayoutGroup>();
                }

                layoutGroup.childAlignment = ChildAlignment;
                layoutGroup.spacing = Spacing;
                layoutGroup.padding = new RectOffset(PaddingLeft, PaddingRight, PaddingTop, PaddingBottom);
                layoutGroup.childControlWidth = false;
                layoutGroup.childControlHeight = false;
                layoutGroup.childForceExpandWidth = false;
                layoutGroup.childForceExpandHeight = false;
            }

            private static Transform EnsureScrollableContentHost(Transform host, bool vertical, bool horizontal)
            {
                var hostRect = host as RectTransform ?? host.gameObject.GetComponent<RectTransform>();
                if (hostRect == null)
                    throw new InvalidOperationException("Scrollable host must have a RectTransform.");

                var viewport = host.Find("__ui_maker_viewport") as RectTransform;
                if (viewport == null)
                {
                    var viewportObject = new GameObject("__ui_maker_viewport", typeof(RectTransform));
                    viewport = viewportObject.GetComponent<RectTransform>();
                    viewport.SetParent(host, false);
                }

                viewport.anchorMin = Vector2.zero;
                viewport.anchorMax = Vector2.one;
                viewport.pivot = new Vector2(0.5f, 0.5f);
                viewport.anchoredPosition = Vector2.zero;
                viewport.offsetMin = Vector2.zero;
                viewport.offsetMax = Vector2.zero;

                var viewportMask = viewport.GetComponent<RectMask2D>();
                if (viewportMask == null)
                    viewportMask = viewport.gameObject.AddComponent<RectMask2D>();
                viewportMask.enabled = vertical || horizontal;

                var viewportImage = viewport.GetComponent<Image>();
                if (viewportImage == null)
                    viewportImage = viewport.gameObject.AddComponent<Image>();
                viewportImage.color = new Color(0f, 0f, 0f, 0f);
                viewportImage.raycastTarget = true;

                var content = viewport.Find("__ui_maker_content") as RectTransform;
                if (content == null)
                {
                    var contentObject = new GameObject("__ui_maker_content", typeof(RectTransform));
                    content = contentObject.GetComponent<RectTransform>();
                    content.SetParent(viewport, false);
                }

                content.localScale = Vector3.one;
                content.anchoredPosition = Vector2.zero;

                if (vertical && !horizontal)
                {
                    content.anchorMin = new Vector2(0f, 1f);
                    content.anchorMax = new Vector2(1f, 1f);
                    content.pivot = new Vector2(0.5f, 1f);
                    content.sizeDelta = Vector2.zero;
                }
                else if (horizontal && !vertical)
                {
                    content.anchorMin = new Vector2(0f, 0f);
                    content.anchorMax = new Vector2(0f, 1f);
                    content.pivot = new Vector2(0f, 0.5f);
                    content.sizeDelta = Vector2.zero;
                }
                else
                {
                    content.anchorMin = new Vector2(0f, 1f);
                    content.anchorMax = new Vector2(0f, 1f);
                    content.pivot = new Vector2(0f, 1f);
                    content.sizeDelta = Vector2.zero;
                }

                var contentFitter = content.GetComponent<ContentSizeFitter>();
                if (contentFitter == null)
                    contentFitter = content.gameObject.AddComponent<ContentSizeFitter>();
                contentFitter.horizontalFit = horizontal ? ContentSizeFitter.FitMode.PreferredSize : ContentSizeFitter.FitMode.Unconstrained;
                contentFitter.verticalFit = vertical ? ContentSizeFitter.FitMode.PreferredSize : ContentSizeFitter.FitMode.Unconstrained;

                var scrollRect = host.gameObject.GetComponent<ScrollRect>();
                if (scrollRect == null)
                    scrollRect = host.gameObject.AddComponent<ScrollRect>();
                scrollRect.viewport = viewport;
                scrollRect.content = content;
                scrollRect.vertical = vertical;
                scrollRect.horizontal = horizontal;
                scrollRect.scrollSensitivity = 25f;
                scrollRect.movementType = ScrollRect.MovementType.Clamped;
                scrollRect.inertia = true;
                scrollRect.enabled = vertical || horizontal;
                scrollRect.horizontalNormalizedPosition = 0f;
                scrollRect.verticalNormalizedPosition = 1f;

                return content;
            }

            private static void DisableContentSizeFitter(Transform transform)
            {
                var fitter = transform.GetComponent<ContentSizeFitter>();
                if (fitter == null)
                    return;

                fitter.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
                fitter.verticalFit = ContentSizeFitter.FitMode.Unconstrained;
                fitter.enabled = false;
            }

            private static void ApplyScrolling(object element, Transform transform, bool vertical, bool horizontal)
            {
                TryInvokeEnableScrolling(element, vertical, horizontal);

                if (element != null)
                {
                    TrySetBoolMember(element, "scrollVertical", vertical);
                    TrySetBoolMember(element, "scrollHorizontal", horizontal);
                    TrySetBoolMember(element, "ScrollVertical", vertical);
                    TrySetBoolMember(element, "ScrollHorizontal", horizontal);
                    TrySetBoolMember(element, "vertical", vertical);
                    TrySetBoolMember(element, "horizontal", horizontal);
                    TrySetBoolMember(element, "Vertical", vertical);
                    TrySetBoolMember(element, "Horizontal", horizontal);
                }

                if (transform == null)
                    return;

                var scrollRect = transform.GetComponent<ScrollRect>() ?? transform.GetComponentInChildren<ScrollRect>(true);
                if (scrollRect != null)
                {
                    scrollRect.vertical = vertical;
                    scrollRect.horizontal = horizontal;
                    scrollRect.scrollSensitivity = 25f;
                    scrollRect.inertia = true;
                    scrollRect.movementType = ScrollRect.MovementType.Clamped;
                    scrollRect.enabled = vertical || horizontal;
                }

                var rectMask = transform.GetComponentInChildren<RectMask2D>(true);
                if (rectMask != null)
                    rectMask.enabled = vertical || horizontal;
            }

            private static void TryInvokeEnableScrolling(object element, bool vertical, bool horizontal)
            {
                if (element == null)
                    return;

                var method = element.GetType().GetMethod("EnableScrolling", new[] { typeof(SFS.UI.ModGUI.Type) });
                if (method == null)
                    return;

                if (vertical)
                    method.Invoke(element, new object[] { SFS.UI.ModGUI.Type.Vertical });
                if (horizontal)
                    method.Invoke(element, new object[] { SFS.UI.ModGUI.Type.Horizontal });
            }

            private static void TrySetBoolMember(object target, string memberName, bool value)
            {
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


            private void ApplyVisualStyle(object element)
            {
                if (TextColorOverride && TryParseColor(TextColor, out var textColor))
                {
                    TrySetColorProperty(element, "TextColor", textColor);
                    TrySetColorProperty(element, "TitleColor", textColor);
                }

                if (BackgroundColorOverride && TryParseColor(BackgroundColor, out var backgroundColor))
                {
                    TrySetColorProperty(element, "WindowColor", backgroundColor);
                    TrySetColorProperty(element, "FieldColor", backgroundColor);
                    TrySetColorProperty(element, "Color", backgroundColor);
                }

                TrySetTextAlignmentProperty(element, "TextAlignment", TextAlignment);
                if (element is Component rootComponent)
                    ApplyTextAlignmentToChildren(rootComponent.transform, TextAlignment);
            }

            private static string NormalizeLabelDirection(string value, string fallback)
            {
                var normalized = (value ?? string.Empty).Trim();
                if (string.Equals(normalized, "Top", StringComparison.OrdinalIgnoreCase))
                    return "Top";
                if (string.Equals(normalized, "Bottom", StringComparison.OrdinalIgnoreCase))
                    return "Bottom";
                if (string.Equals(normalized, "Left", StringComparison.OrdinalIgnoreCase))
                    return "Left";
                if (string.Equals(normalized, "Right", StringComparison.OrdinalIgnoreCase))
                    return "Right";

                return fallback;
            }

            private static bool TryParseColor(string value, out Color color)
            {
                color = default;
                return !string.IsNullOrWhiteSpace(value) && ColorUtility.TryParseHtmlString(value, out color);
            }

            private static void TrySetColorProperty(object target, string propertyName, Color color)
            {
                var property = target.GetType().GetProperty(propertyName);
                if (property != null && property.CanWrite && property.PropertyType == typeof(Color))
                    property.SetValue(target, color, null);
            }

            private static void TrySetTextAlignmentProperty(object target, string propertyName, TextAnchor value)
            {
                var property = target.GetType().GetProperty(propertyName);
                if (property == null || !property.CanWrite || !property.PropertyType.IsEnum)
                    return;
                try
                {
                    var mappedName = MapTextAlignmentName(property.PropertyType, value);
                    var enumValue = Enum.Parse(property.PropertyType, mappedName, true);
                    property.SetValue(target, enumValue, null);
                }
                catch
                {
                }
            }

            private static string MapTextAlignmentName(System.Type enumType, TextAnchor value)
            {
                var fullName = enumType.FullName ?? enumType.Name;
                if (fullName.IndexOf("TMPro.TextAlignmentOptions", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return value switch
                    {
                        TextAnchor.UpperLeft => "TopLeft",
                        TextAnchor.UpperCenter => "Top",
                        TextAnchor.UpperRight => "TopRight",
                        TextAnchor.MiddleLeft => "Left",
                        TextAnchor.MiddleCenter => "Center",
                        TextAnchor.MiddleRight => "Right",
                        TextAnchor.LowerLeft => "BottomLeft",
                        TextAnchor.LowerCenter => "Bottom",
                        TextAnchor.LowerRight => "BottomRight",
                        _ => "Center",
                    };
                }

                return value.ToString();
            }

            private static void ApplyTextAlignmentToChildren(Transform root, TextAnchor value)
            {
                foreach (var component in root.GetComponentsInChildren<Component>(true))
                {
                    if (component == null)
                        continue;

                    var typeName = component.GetType().Name;
                    if (!typeName.Contains("Text", StringComparison.OrdinalIgnoreCase) && !typeName.Contains("Label", StringComparison.OrdinalIgnoreCase))
                        continue;

                    TrySetTextAlignmentProperty(component, "alignment", value);
                    TrySetTextAlignmentProperty(component, "Alignment", value);
                    TrySetTextAlignmentProperty(component, "TextAlignment", value);
                }
            }

            private static void ClampRectTransformToScreen(RectTransform rectTransform)
            {
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

            private static int DeterministicId(string id)
            {
                unchecked
                {
                    var hash = 23;
                    for (var i = 0; i < id.Length; i++)
                        hash = hash * 31 + id[i];
                    return Math.Abs(hash);
                }
            }
        }
