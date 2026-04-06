from typing import Callable, Optional

import wx

from ui_model import SIZE_MODE_MANUAL, UIDocument


class DesignerCanvas(wx.Panel):
    # Render selection overlay using authoritative bounds received from SFS.

    COLORS = {
        "Window": wx.Colour(34, 51, 80),
        "Container": wx.Colour(60, 96, 120),
        "Box": wx.Colour(80, 60, 110),
        "Label": wx.Colour(160, 160, 80),
        "Button": wx.Colour(70, 120, 80),
        "TextInput": wx.Colour(100, 80, 130),
        "Toggle": wx.Colour(130, 90, 70),
        "Slider": wx.Colour(70, 110, 150),
        "Separator": wx.Colour(130, 130, 130),
        "Space": wx.Colour(90, 90, 90),
    }

    def __init__(self, parent: wx.Window, document: UIDocument, on_select: Callable[[Optional[str]], None]):
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self._document = document
        self._on_select = on_select
        self._selected_id: Optional[str] = None
        self._layout_overlay_visible = False
        self._last_layout_rects: list[tuple[str, wx.Rect]] = []
        self._authoritative_capture: Optional[tuple[float, float]] = None
        self._authoritative_nodes: dict[str, tuple[float, float, float, float]] = {}
        self._frame_bitmap: Optional[wx.Bitmap] = None
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)

    def set_selected(self, element_id: Optional[str]) -> None:
        # Track selected element so canvas can highlight current target.

        self._selected_id = element_id
        self.Refresh()

    def set_authoritative_layout(self, payload: dict) -> None:
        # Store SFS-resolved layout bounds as the source of truth for visualization and hit tests.

        capture = payload.get("capture")
        nodes = payload.get("nodes")
        if not isinstance(capture, dict) or not isinstance(nodes, list):
            return

        width = float(capture.get("width", 0.0))
        height = float(capture.get("height", 0.0))
        if width <= 0.0 or height <= 0.0:
            return

        authoritative_nodes: dict[str, tuple[float, float, float, float]] = {}
        for node in nodes:
            if not isinstance(node, dict) or "id" not in node:
                continue

            node_id = str(node["id"])
            try:
                x = float(node.get("x", 0.0))
                y = float(node.get("y", 0.0))
                node_w = float(node.get("width", 0.0))
                node_h = float(node.get("height", 0.0))
            except (TypeError, ValueError):
                continue

            if node_w <= 0.0 or node_h <= 0.0:
                continue

            authoritative_nodes[node_id] = (x, y, node_w, node_h)

        self._authoritative_capture = (width, height)
        self._authoritative_nodes = authoritative_nodes
        self.Refresh()

    def set_preview_bitmap(self, bitmap: wx.Bitmap) -> None:
        # Use latest SFS frame as canvas background so visualizer mirrors game output.

        self._frame_bitmap = bitmap
        self.Refresh()

    def set_layout_visible(self, visible: bool) -> None:
        # Toggle non-window layout overlay visibility.

        self._layout_overlay_visible = visible
        self.Refresh()

    def _on_paint(self, _event: wx.PaintEvent) -> None:
        # Draw live game frame and optional non-window layout overlay.

        dc = wx.AutoBufferedPaintDC(self)
        size = self.GetClientSize()
        dc.SetBrush(wx.Brush(wx.Colour(20, 24, 30)))
        dc.SetPen(wx.Pen(wx.Colour(20, 24, 30)))
        dc.DrawRectangle(0, 0, size.width, size.height)

        self._draw_frame_fit(dc, size)
        if not self._layout_overlay_visible:
            self._last_layout_rects = []
            return

        dc.SetPen(wx.Pen(wx.Colour(36, 42, 48), 1))
        step = 24
        for x in range(0, size.width, step):
            dc.DrawLine(x, 0, x, size.height)
        for y in range(0, size.height, step):
            dc.DrawLine(0, y, size.width, y)

        if self._authoritative_capture is not None and self._authoritative_nodes:
            self._draw_authoritative(dc, size)
            return

        self._last_layout_rects = []
        root_x = 20
        root_y = 20
        ordered_ids = self._iter_node_ids_in_order()
        order_map = {node_id: index for index, node_id in enumerate(ordered_ids)}
        depth_map = self._build_depth_map()
        for root_id in self._document.roots:
            consumed = self._draw_node(
                dc,
                root_id,
                x=root_x,
                y=root_y,
                available_width=max(200, size.width - 40),
                order_map=order_map,
                depth_map=depth_map,
            )
            root_y += consumed + 16

    def _draw_authoritative(self, dc: wx.AutoBufferedPaintDC, size: wx.Size) -> None:
        # Draw element rectangles from SFS-provided capture-space metrics.

        capture_width, capture_height = self._authoritative_capture if self._authoritative_capture is not None else (0.0, 0.0)
        if capture_width <= 0.0 or capture_height <= 0.0:
            return

        scale = min(size.width / capture_width, size.height / capture_height)
        scale = max(0.01, scale)
        draw_width = int(capture_width * scale)
        draw_height = int(capture_height * scale)
        offset_x = (size.width - draw_width) // 2
        offset_y = (size.height - draw_height) // 2

        self._last_layout_rects = []
        ordered_ids = self._iter_node_ids_in_order()
        order_map = {node_id: index for index, node_id in enumerate(ordered_ids)}
        depth_map = self._build_depth_map()
        for node_id in ordered_ids:
            metrics = self._authoritative_nodes.get(node_id)
            if metrics is None:
                continue

            node = self._document.elements.get(node_id)
            if node is not None and node.element_type in {"Window", "ClosableWindow"}:
                continue

            rel_x, rel_y, rel_w, rel_h = metrics
            px = int(offset_x + rel_x * scale)
            py = int(offset_y + (capture_height - (rel_y + rel_h)) * scale)
            pw = max(1, int(rel_w * scale))
            ph = max(1, int(rel_h * scale))
            rect = wx.Rect(px, py, pw, ph)
            self._last_layout_rects.append((node_id, rect))

            base_color = self.COLORS.get(node.element_type if node is not None else "", wx.Colour(100, 100, 100))
            color = self._overlay_variant_color(base_color, order_map.get(node_id, 0), depth_map.get(node_id, 0))
            dc.SetBrush(wx.Brush(wx.Colour(color.Red(), color.Green(), color.Blue(), 70)))
            if node_id == self._selected_id:
                dc.SetPen(wx.Pen(wx.Colour(255, 215, 0), 2))
            else:
                dc.SetPen(wx.Pen(wx.Colour(220, 220, 220), 1))

            dc.DrawRectangle(rect)

            if node is not None:
                label = node.name if node.text == "" else f"{node.name}: {node.text}"
                dc.SetTextForeground(wx.Colour(245, 245, 245))
                dc.DrawText(label, rect.x + 4, rect.y + 2)

    def _draw_frame_fit(self, dc: wx.AutoBufferedPaintDC, size: wx.Size) -> None:
        # Draw live game frame centered in canvas bounds while preserving aspect ratio.

        if self._frame_bitmap is None or not self._frame_bitmap.IsOk():
            return

        frame_w = self._frame_bitmap.GetWidth()
        frame_h = self._frame_bitmap.GetHeight()
        if frame_w <= 0 or frame_h <= 0:
            return

        scale = min(size.width / frame_w, size.height / frame_h)
        draw_w = max(1, int(frame_w * scale))
        draw_h = max(1, int(frame_h * scale))
        draw_x = (size.width - draw_w) // 2
        draw_y = (size.height - draw_h) // 2

        frame_image = self._frame_bitmap.ConvertToImage()
        scaled = frame_image.Scale(draw_w, draw_h, wx.IMAGE_QUALITY_HIGH)
        dc.DrawBitmap(wx.Bitmap(scaled), draw_x, draw_y)

    def _iter_node_ids_in_order(self) -> list[str]:
        # Preserve document hierarchy order for stable draw order and hit testing.

        ordered: list[str] = []

        def walk(node_id: str) -> None:
            ordered.append(node_id)
            node = self._document.elements.get(node_id)
            if node is None:
                return

            for child in node.children:
                walk(child)

        for root in self._document.roots:
            walk(root)

        return ordered

    def _draw_node(
        self,
        dc: wx.AutoBufferedPaintDC,
        node_id: str,
        x: int,
        y: int,
        available_width: int,
        order_map: dict[str, int],
        depth_map: dict[str, int],
    ) -> int:
        # Draw one node and recurse according to vertical/horizontal layout settings.

        node = self._document.elements[node_id]
        width = node.width if node.width_mode == SIZE_MODE_MANUAL else available_width
        width = max(20, width)
        rect = wx.Rect(x, y, width, node.height)
        if node.element_type not in {"Window", "ClosableWindow"}:
            self._last_layout_rects.append((node_id, rect))
            base_color = self.COLORS.get(node.element_type, wx.Colour(100, 100, 100))
            color = self._overlay_variant_color(base_color, order_map.get(node_id, 0), depth_map.get(node_id, 0))

            dc.SetBrush(wx.Brush(color))
            if node.id == self._selected_id:
                dc.SetPen(wx.Pen(wx.Colour(255, 215, 0), 2))
            else:
                dc.SetPen(wx.Pen(wx.Colour(16, 16, 16), 1))

            dc.DrawRectangle(rect)

            label = node.name if node.text == "" else f"{node.name}: {node.text}"
            dc.SetTextForeground(wx.Colour(238, 238, 238))
            dc.DrawText(label, x + 6, y + 6)

        if not node.children:
            return rect.height

        inner_x = x + node.padding_left
        inner_y = y + node.padding_top + 28
        inner_width = max(40, width - (node.padding_left + node.padding_right))

        consumed_height = rect.height
        layout = node.layout.lower()
        if layout == "horizontal":
            cursor_x = inner_x
            max_child_height = 0
            for child_id in node.children:
                child = self._document.elements[child_id]
                child_width = max(20, child.width)
                child_drawn_height = self._draw_node(dc, child_id, cursor_x, inner_y, child_width, order_map, depth_map)
                cursor_x += child_width + node.spacing
                max_child_height = max(max_child_height, child_drawn_height)

            consumed_height = max(consumed_height, (inner_y - y) + max_child_height + node.padding_bottom)
        else:
            cursor_y = inner_y
            for child_id in node.children:
                child_drawn_height = self._draw_node(dc, child_id, inner_x, cursor_y, inner_width, order_map, depth_map)
                cursor_y += child_drawn_height + node.spacing

            consumed_height = max(consumed_height, cursor_y - y + node.padding_bottom)

        return consumed_height

    def _build_depth_map(self) -> dict[str, int]:
        # Track hierarchy depth so stacked overlays get deterministic but varied tint levels.

        depth_map: dict[str, int] = {}

        def walk(node_id: str, depth: int) -> None:
            depth_map[node_id] = depth
            node = self._document.elements.get(node_id)
            if node is None:
                return

            for child_id in node.children:
                walk(child_id, depth + 1)

        for root_id in self._document.roots:
            walk(root_id, 0)

        return depth_map

    @staticmethod
    def _overlay_variant_color(base: wx.Colour, order_index: int, depth: int) -> wx.Colour:
        # Apply subtle deterministic brightness shifts to distinguish overlapping nodes.

        shift_bucket = (order_index + depth) % 4
        brightness = 0.88 + (shift_bucket * 0.07)
        red = max(0, min(255, int(base.Red() * brightness)))
        green = max(0, min(255, int(base.Green() * brightness)))
        blue = max(0, min(255, int(base.Blue() * brightness)))
        return wx.Colour(red, green, blue)

    def _on_left_down(self, event: wx.MouseEvent) -> None:
        # Select top-most element under cursor.

        hit = self._hit_test(event.GetPosition())
        self._on_select(hit)
        self._selected_id = hit

        self.Refresh()

    def _hit_test(self, position: wx.Point) -> Optional[str]:
        # Return top-most element ID at point using the latest layout-calculated rectangles.

        for node_id, rect in reversed(self._last_layout_rects):
            if rect.Contains(position):
                return node_id

        return None
