import atexit
import base64
import json
import os
import re
import signal
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import wx

from csharp_export import generate_csharp_export
from markdown_export import generate_markdown_layout_guide
from ui_model import (
    SAFE_MAX_PADDING,
    SAFE_MAX_POSITION,
    SAFE_MAX_SIZE,
    SAFE_MAX_SPACING,
    SAFE_MIN_PADDING,
    SAFE_MIN_POSITION,
    SAFE_MIN_SIZE,
    SAFE_MIN_SPACING,
    SIZE_MODE_CHOICES,
    SIZE_MODE_MANUAL,
    UIElement,
    UIDocument,
    normalize_size_mode,
)
from ui_canvas import DesignerCanvas
from ui_runtime import AppConfig, RealtimeBridge, SFSOrchestrator, SingleInstanceLock


class OrchestratorPanel(wx.Panel):
    # Host process orchestration controls and operational logs.

    def __init__(self, parent: wx.Window, orchestrator: SFSOrchestrator):
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._running = False
        self._build_ui()

    def _build_ui(self) -> None:
        # Build controls for build/launch/attach/shutdown operations.

        root = wx.BoxSizer(wx.VERTICAL)

        self.status_label = wx.StaticText(self, label="Status: Idle")
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_full = wx.Button(self, label="Build + Launch + Attach")
        self.btn_build = wx.Button(self, label="Build Only")
        self.btn_launch_attach = wx.Button(self, label="Launch + Attach")
        self.btn_stop = wx.Button(self, label="Stop SFS")

        for btn in (self.btn_full, self.btn_build, self.btn_launch_attach, self.btn_stop):
            buttons.Add(btn, 0, wx.ALL, 5)

        root.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.log_output = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        root.Add(self.log_output, 1, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(root)

        self.btn_full.Bind(wx.EVT_BUTTON, lambda _: self._start_async(self._orchestrator.full_startup))
        self.btn_build.Bind(wx.EVT_BUTTON, lambda _: self._start_async(self._orchestrator.build_mod))
        self.btn_launch_attach.Bind(wx.EVT_BUTTON, lambda _: self._start_async(self._orchestrator.launch_and_attach))
        self.btn_stop.Bind(wx.EVT_BUTTON, lambda _: self._start_async(self._orchestrator.shutdown_sfs))

    def append_log(self, message: str) -> None:
        # Append timestamped lifecycle logs to the panel output.

        timestamp = time.strftime("%H:%M:%S")
        self.log_output.AppendText(f"[{timestamp}] {message}\n")

    def update_status(self, message: str) -> None:
        # Update stage indicator text.

        self.status_label.SetLabel(f"Status: {message}")

    def _start_async(self, action: Callable[[], object]) -> None:
        # Execute long-running work in a background thread to keep UI responsive.

        if self._running:
            self.append_log("Action skipped: another operation is running.")
            return

        self._running = True
        self._set_buttons_enabled(False)

        def run() -> None:
            try:
                action()
            except Exception as ex:  # pragma: no cover
                wx.CallAfter(self.append_log, f"Unhandled error: {ex}")
                wx.CallAfter(self.update_status, "Error")
            finally:
                wx.CallAfter(self._finish_async)

        threading.Thread(target=run, daemon=True).start()

    def _finish_async(self) -> None:
        # Restore interactive controls after command completion.

        self._running = False
        self._set_buttons_enabled(True)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        # Toggle command availability during active operations.

        for control in (self.btn_full, self.btn_build, self.btn_launch_attach, self.btn_stop):
            control.Enable(enabled)


class DesignerPanel(wx.Panel):
    # Provide layered creator workspace plus realtime bridge visualizer controls.

    ELEMENT_TYPES = ["Window", "ClosableWindow", "Container", "Box", "Label", "Button", "ButtonWithLabel", "TextInput", "InputWithLabel", "Toggle", "ToggleWithLabel", "Slider", "Separator", "Space"]
    CONTAINER_TYPES = {"Window", "ClosableWindow", "Container", "Box"}
    TEXT_TYPES = {"Window", "ClosableWindow", "Label", "Button", "ButtonWithLabel", "TextInput", "InputWithLabel", "Toggle", "ToggleWithLabel"}
    TEXT_COLOR_OVERRIDE_SUPPORTED_TYPES = {"Window", "ClosableWindow", "Label", "Button", "ButtonWithLabel"}
    BACKGROUND_COLOR_OVERRIDE_SUPPORTED_TYPES = {"Window", "ClosableWindow", "Box", "TextInput", "InputWithLabel"}
    LABELED_TYPES = {"ButtonWithLabel", "InputWithLabel", "ToggleWithLabel"}
    LABELED_WITH_CONTROL_TEXT_TYPES = {"ButtonWithLabel", "InputWithLabel"}
    LABEL_DIRECTION_CHOICES = ["Top", "Bottom", "Left", "Right"]
    ALIGNMENT_CHOICES = [
        "UpperLeft",
        "UpperCenter",
        "UpperRight",
        "MiddleLeft",
        "MiddleCenter",
        "MiddleRight",
        "LowerLeft",
        "LowerCenter",
        "LowerRight",
    ]
    ANCHOR_PRESET_CHOICES = ALIGNMENT_CHOICES
    TEXT_ALIGNMENT_CHOICES = ["Left", "Center", "Right", "TopLeft", "Top", "TopRight", "BottomLeft", "Bottom", "BottomRight"]
    FONT_STYLE_CHOICES = ["Normal", "Bold", "Italic", "Underline", "Lowercase", "Uppercase", "Smallcaps"]
    SLIDER_TYPE_CHOICES = ["LeftToRight", "RightToLeft", "BottomToTop", "TopToBottom"]
    def __init__(
        self,
        parent: wx.Window,
        config: AppConfig,
        on_restart_generated_test: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._on_restart_generated_test = on_restart_generated_test
        self._document = UIDocument()
        self._selection: Optional[str] = None
        self._last_element_type = self.ELEMENT_TYPES[0]
        self._bridge = RealtimeBridge(self._log_threadsafe, self._set_bridge_state_threadsafe, self._on_bridge_json_threadsafe)
        self._tree_item_to_id: dict[wx.TreeItemId, str] = {}
        self._dynamic_prop_controls: dict[str, wx.Window] = {}
        self._is_syncing_fields = False
        self._applying_remote_snapshot = False
        self._last_preview_render_time = 0.0
        self._pending_sync: Optional[wx.CallLater] = None
        self._last_sent_snapshot_signature: str = ""
        self._dragging_tree_node_id: Optional[str] = None
        self._manual_refresh_waiting_for_connect = False
        self._copied_subtree_payload: Optional[dict] = None
        self._preview_connected = True
        self._bridge_state_text = "Disconnected"
        self._export_tab_active = False
        self._pending_generated_test_source: Optional[str] = None
        self._generated_test_restart_attempted = False
        self._pending_generated_retry_source: Optional[str] = None
        self._latest_layout_payload: Optional[dict] = None
        self._latest_preview_bitmap: Optional[wx.Bitmap] = None
        self._snapshot_sync_delay_ms = 120
        self._bridge_sync_enabled = True
        self._scope_root_ids: Optional[list[str]] = None
        self._scope_hide_window_nodes = False
        self._build_ui()
        self._refresh_project_status()
        self._set_creator_enabled(False)

    def shutdown(self) -> None:
        # Stop bridge session when the app is closing.

        self._bridge.disconnect()

    def get_document(self) -> UIDocument:
        # Expose backing document reference for panels that intentionally share the same editor state.

        return self._document

    def use_document(self, document: UIDocument, select_id: Optional[str] = None) -> None:
        # Rebind this editor instance to an external shared document.

        self._document = document
        self.canvas._document = document
        if select_id is None and document.roots:
            select_id = document.roots[0]
        self._refresh_all(select_id=select_id, sync_bridge=False)
        self._set_creator_enabled(True)

    def set_bridge_sync_enabled(self, enabled: bool) -> None:
        # Toggle websocket sync for this editor instance while keeping local editing active.

        self._bridge_sync_enabled = enabled
        if not enabled:
            self._bridge.disconnect()

    def set_scope(self, root_ids: Optional[list[str]], hide_window_nodes: bool = False, select_id: Optional[str] = None) -> None:
        # Limit tree/canvas to selected roots for focused component editing workflows.

        self._scope_root_ids = list(root_ids) if root_ids is not None else None
        self._scope_hide_window_nodes = hide_window_nodes
        self.canvas.set_scope(self._scope_root_ids, hide_window_nodes=hide_window_nodes)
        if select_id is None:
            select_id = self._first_visible_node_in_scope()
        self._refresh_all(select_id=select_id, sync_bridge=False)

    def _iter_scope_roots(self) -> list[str]:
        # Resolve active root ids for scoped tree rendering.

        if self._scope_root_ids is None:
            return [root_id for root_id in self._document.roots if root_id in self._document.elements]
        return [root_id for root_id in self._scope_root_ids if root_id in self._document.elements]

    def _first_visible_node_in_scope(self) -> Optional[str]:
        # Pick first node visible in current scope; skips window wrappers when hidden.

        def first_non_window(node_id: str) -> Optional[str]:
            if node_id not in self._document.elements:
                return None
            node = self._document.elements[node_id]
            if not (self._scope_hide_window_nodes and node.element_type in {"Window", "ClosableWindow"}):
                return node_id
            for child_id in node.children:
                found = first_non_window(child_id)
                if found is not None:
                    return found
            return None

        for root_id in self._iter_scope_roots():
            found = first_non_window(root_id)
            if found is not None:
                return found
        return None

    def _build_ui(self) -> None:
        # Construct creator and visualizer workspace sections.

        root = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.element_choice = wx.Choice(self, choices=self.ELEMENT_TYPES)
        initial_type_index = self.element_choice.FindString(self._last_element_type)
        self.element_choice.SetSelection(initial_type_index if initial_type_index != wx.NOT_FOUND else 0)
        self.btn_add_root = wx.Button(self, label="Add Root")
        self.btn_add_child = wx.Button(self, label="Add Child")
        self.btn_delete = wx.Button(self, label="Delete")

        for control in (
            self.element_choice,
            self.btn_add_root,
            self.btn_add_child,
            self.btn_delete,
        ):
            toolbar.Add(control, 0, wx.ALL, 4)

        root.Add(toolbar, 0, wx.ALL, 4)

        project_bar = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_new_project = wx.Button(self, label="New Project")
        self.btn_load_json = wx.Button(self, label="Load JSON")
        self.btn_save_json = wx.Button(self, label="Save JSON")
        self.project_status = wx.StaticText(self, label="Project: Unsaved")

        for control in (self.btn_new_project, self.btn_load_json, self.btn_save_json):
            project_bar.Add(control, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)

        project_bar.AddStretchSpacer()
        project_bar.Add(self.project_status, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        root.Add(project_bar, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 4)

        bridge_bar = wx.BoxSizer(wx.HORIZONTAL)
        self.bridge_url = wx.TextCtrl(self, value=self._config.websocket_url, size=wx.Size(320, -1))
        self.btn_refresh_snapshot = wx.Button(self, label="Refresh Snapshot")
        self.btn_toggle_preview = wx.Button(self, label="Disconnect Preview")
        self.bridge_state = wx.StaticText(self, label="Bridge: Disconnected")

        for control in (self.bridge_url, self.btn_refresh_snapshot, self.btn_toggle_preview, self.bridge_state):
            bridge_bar.Add(control, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)

        root.Add(bridge_bar, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        body = wx.BoxSizer(wx.HORIZONTAL)

        left_panel = wx.Panel(self)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        self.tree = wx.TreeCtrl(left_panel, style=wx.TR_DEFAULT_STYLE | wx.TR_SINGLE)
        left_sizer.Add(wx.StaticText(left_panel, label="Hierarchy"), 0, wx.ALL, 4)
        left_sizer.Add(self.tree, 3, wx.ALL | wx.EXPAND, 4)

        properties_panel = wx.ScrolledWindow(left_panel, style=wx.VSCROLL)
        properties_panel.SetScrollRate(0, 16)
        properties_sizer = wx.BoxSizer(wx.VERTICAL)
        properties_sizer.Add(wx.StaticText(properties_panel, label="Properties"), 0, wx.ALL, 4)

        self.field_name = wx.TextCtrl(properties_panel, style=wx.TE_PROCESS_ENTER)
        self.field_text = wx.TextCtrl(properties_panel, style=wx.TE_PROCESS_ENTER)
        self.field_label_text = wx.TextCtrl(properties_panel, style=wx.TE_PROCESS_ENTER)
        self.field_control_text = wx.TextCtrl(properties_panel, style=wx.TE_PROCESS_ENTER)
        self.field_text_alignment = wx.Choice(properties_panel, choices=self.TEXT_ALIGNMENT_CHOICES)
        self.field_text_alignment.SetSelection(0)
        self.field_text_color_override = wx.CheckBox(properties_panel, label="Enable Override")
        self.field_background_color_override = wx.CheckBox(properties_panel, label="Enable Override")
        self.text_color_row_panel = wx.Panel(properties_panel)
        self.field_text_color = wx.ColourPickerCtrl(self.text_color_row_panel)
        text_color_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        text_color_row_sizer.Add(wx.StaticText(self.text_color_row_panel, label="Color"), 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 4)
        text_color_row_sizer.Add(self.field_text_color, 0, wx.ALIGN_CENTER_VERTICAL, 0)
        self.text_color_row_panel.SetSizer(text_color_row_sizer)

        self.background_color_row_panel = wx.Panel(properties_panel)
        self.field_background_color = wx.ColourPickerCtrl(self.background_color_row_panel)
        background_color_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        background_color_row_sizer.Add(wx.StaticText(self.background_color_row_panel, label="Color"), 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 4)
        background_color_row_sizer.Add(self.field_background_color, 0, wx.ALIGN_CENTER_VERTICAL, 0)
        self.background_color_row_panel.SetSizer(background_color_row_sizer)
        self.field_x = wx.SpinCtrl(properties_panel, min=SAFE_MIN_POSITION, max=SAFE_MAX_POSITION, initial=0)
        self.field_y = wx.SpinCtrl(properties_panel, min=SAFE_MIN_POSITION, max=SAFE_MAX_POSITION, initial=0)
        self.field_w = wx.SpinCtrl(properties_panel, min=SAFE_MIN_SIZE, max=SAFE_MAX_SIZE, initial=100)
        self.field_h = wx.SpinCtrl(properties_panel, min=SAFE_MIN_SIZE, max=SAFE_MAX_SIZE, initial=40)
        self.field_layout = wx.Choice(properties_panel, choices=["Vertical", "Horizontal"])
        self.field_layout.SetSelection(0)
        self.field_child_alignment = wx.Choice(properties_panel, choices=self.ALIGNMENT_CHOICES)
        self.field_child_alignment.SetSelection(0)
        self.field_spacing = wx.SpinCtrl(properties_panel, min=SAFE_MIN_SPACING, max=SAFE_MAX_SPACING, initial=12)
        self.padding_row_panel = wx.Panel(properties_panel)
        self.field_padding_left = wx.SpinCtrl(self.padding_row_panel, min=SAFE_MIN_PADDING, max=SAFE_MAX_PADDING, initial=12)
        self.field_padding_right = wx.SpinCtrl(self.padding_row_panel, min=SAFE_MIN_PADDING, max=SAFE_MAX_PADDING, initial=12)
        self.field_padding_top = wx.SpinCtrl(self.padding_row_panel, min=SAFE_MIN_PADDING, max=SAFE_MAX_PADDING, initial=12)
        self.field_padding_bottom = wx.SpinCtrl(self.padding_row_panel, min=SAFE_MIN_PADDING, max=SAFE_MAX_PADDING, initial=12)
        padding_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        padding_row_sizer.Add(wx.StaticText(self.padding_row_panel, label="L"), 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 3)
        padding_row_sizer.Add(self.field_padding_left, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        padding_row_sizer.Add(wx.StaticText(self.padding_row_panel, label="R"), 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 3)
        padding_row_sizer.Add(self.field_padding_right, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        padding_row_sizer.Add(wx.StaticText(self.padding_row_panel, label="T"), 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 3)
        padding_row_sizer.Add(self.field_padding_top, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        padding_row_sizer.Add(wx.StaticText(self.padding_row_panel, label="B"), 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 3)
        padding_row_sizer.Add(self.field_padding_bottom, 0, wx.ALIGN_CENTER_VERTICAL, 0)
        self.padding_row_panel.SetSizer(padding_row_sizer)
        self.field_width_mode = wx.Choice(properties_panel, choices=SIZE_MODE_CHOICES)
        self.field_width_mode.SetSelection(0)
        self.field_height_mode = wx.Choice(properties_panel, choices=SIZE_MODE_CHOICES)
        self.field_height_mode.SetSelection(0)
        self.field_scroll_vertical = wx.CheckBox(properties_panel, label="Scroll Vertical")
        self.field_scroll_horizontal = wx.CheckBox(properties_panel, label="Scroll Horizontal")
        self.field_label_direction = wx.Choice(properties_panel, choices=self.LABEL_DIRECTION_CHOICES)
        self.field_label_direction.SetSelection(0)

        self._general_rows: dict[str, tuple[wx.StaticText, wx.Window]] = {}
        self._add_general_row(properties_panel, properties_sizer, "name", "Name", self.field_name)
        self._add_general_row(properties_panel, properties_sizer, "text", "Text", self.field_text)
        self._add_general_row(properties_panel, properties_sizer, "label_text", "Label Text", self.field_label_text)
        self._add_general_row(properties_panel, properties_sizer, "control_text", "Control Text", self.field_control_text)
        self._add_general_row(properties_panel, properties_sizer, "text_alignment", "Text Alignment", self.field_text_alignment)
        self._add_general_row(properties_panel, properties_sizer, "text_override", "Override Text Color", self.field_text_color_override)
        self._add_general_row(properties_panel, properties_sizer, "text_color", "Text Color", self.text_color_row_panel)
        self._add_general_row(properties_panel, properties_sizer, "background_override", "Override Background Color", self.field_background_color_override)
        self._add_general_row(properties_panel, properties_sizer, "background_color", "Background Color", self.background_color_row_panel)
        self.color_support_note = wx.StaticText(properties_panel, label="")
        self._add_general_row(properties_panel, properties_sizer, "color_note", "Color Support", self.color_support_note)
        self._add_general_row(properties_panel, properties_sizer, "x", "X", self.field_x)
        self._add_general_row(properties_panel, properties_sizer, "y", "Y", self.field_y)
        self._add_general_row(properties_panel, properties_sizer, "width", "Width", self.field_w)
        self._add_general_row(properties_panel, properties_sizer, "height", "Height", self.field_h)
        self._add_general_row(properties_panel, properties_sizer, "layout", "Layout Direction", self.field_layout)
        self._add_general_row(properties_panel, properties_sizer, "child_alignment", "Child Alignment", self.field_child_alignment)
        self._add_general_row(properties_panel, properties_sizer, "spacing", "Spacing", self.field_spacing)
        self._add_general_row(properties_panel, properties_sizer, "padding", "Padding (L/R/T/B)", self.padding_row_panel)
        self._add_general_row(properties_panel, properties_sizer, "width_mode", "Width Mode", self.field_width_mode)
        self._add_general_row(properties_panel, properties_sizer, "height_mode", "Height Mode", self.field_height_mode)
        self._add_general_row(properties_panel, properties_sizer, "scroll_vertical", "Scroll Vertical", self.field_scroll_vertical)
        self._add_general_row(properties_panel, properties_sizer, "scroll_horizontal", "Scroll Horizontal", self.field_scroll_horizontal)
        self._add_general_row(properties_panel, properties_sizer, "label_direction", "Label Direction", self.field_label_direction)
        properties_sizer.AddStretchSpacer()
        properties_panel.SetSizer(properties_sizer)

        left_sizer.Add(properties_panel, 2, wx.ALL | wx.EXPAND, 4)
        left_panel.SetSizer(left_sizer)

        right_panel = wx.Panel(self)
        right_sizer = wx.BoxSizer(wx.VERTICAL)

        canvas_panel = wx.Panel(right_panel)
        canvas_sizer = wx.BoxSizer(wx.VERTICAL)
        canvas_toolbar = wx.BoxSizer(wx.HORIZONTAL)
        canvas_toolbar.Add(wx.StaticText(canvas_panel, label="Layout + Live Preview"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.chk_show_layout = wx.CheckBox(canvas_panel, label="Show Layout Overlay")
        self.chk_show_layout.SetValue(False)
        canvas_toolbar.AddStretchSpacer()
        canvas_toolbar.Add(self.chk_show_layout, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        canvas_sizer.Add(canvas_toolbar, 0, wx.EXPAND)
        self.canvas = DesignerCanvas(canvas_panel, self._document, self._on_canvas_select)
        self.canvas.SetMinSize(wx.Size(420, 260))
        canvas_sizer.Add(self.canvas, 1, wx.ALL | wx.EXPAND, 4)
        canvas_panel.SetSizer(canvas_sizer)
        right_sizer.Add(canvas_panel, 3, wx.ALL | wx.EXPAND, 2)

        right_sizer.Add(wx.StaticText(right_panel, label="Bridge Log"), 0, wx.ALL, 4)
        self.log_box = wx.TextCtrl(right_panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        right_sizer.Add(self.log_box, 1, wx.ALL | wx.EXPAND, 4)
        right_panel.SetSizer(right_sizer)

        body.Add(left_panel, 1, wx.ALL | wx.EXPAND, 4)
        body.Add(right_panel, 2, wx.ALL | wx.EXPAND, 4)

        root.Add(body, 1, wx.ALL | wx.EXPAND, 4)
        self.SetSizer(root)

        self.btn_add_root.Bind(wx.EVT_BUTTON, self._on_add_root)
        self.btn_add_child.Bind(wx.EVT_BUTTON, self._on_add_child)
        self.btn_delete.Bind(wx.EVT_BUTTON, self._on_delete)
        self.btn_new_project.Bind(wx.EVT_BUTTON, self._on_new_project)
        self.btn_save_json.Bind(wx.EVT_BUTTON, self._on_save_document)
        self.btn_load_json.Bind(wx.EVT_BUTTON, self._on_load_document)
        self.btn_refresh_snapshot.Bind(wx.EVT_BUTTON, self._on_refresh_snapshot)
        self.btn_toggle_preview.Bind(wx.EVT_BUTTON, self._on_toggle_preview)

        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_tree_select)
        self.tree.Bind(wx.EVT_TREE_BEGIN_DRAG, self._on_tree_begin_drag)
        self.tree.Bind(wx.EVT_TREE_END_DRAG, self._on_tree_end_drag)
        self.tree.Bind(wx.EVT_TREE_ITEM_RIGHT_CLICK, self._on_tree_item_right_click)
        self.chk_show_layout.Bind(wx.EVT_CHECKBOX, lambda _: self.canvas.set_layout_visible(self.chk_show_layout.GetValue()))
        self.element_choice.Bind(wx.EVT_CHOICE, self._on_element_type_selected)

        self.field_name.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_text.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_label_text.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_control_text.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_text_alignment.Bind(wx.EVT_CHOICE, self._on_property_changed)
        self.field_text_color_override.Bind(wx.EVT_CHECKBOX, self._on_property_changed)
        self.field_background_color_override.Bind(wx.EVT_CHECKBOX, self._on_property_changed)
        self.field_text_color.Bind(wx.EVT_COLOURPICKER_CHANGED, self._on_property_changed)
        self.field_background_color.Bind(wx.EVT_COLOURPICKER_CHANGED, self._on_property_changed)
        self.field_name.Bind(wx.EVT_TEXT_ENTER, self._on_property_changed)
        self.field_text.Bind(wx.EVT_TEXT_ENTER, self._on_property_changed)
        self.field_label_text.Bind(wx.EVT_TEXT_ENTER, self._on_property_changed)
        self.field_control_text.Bind(wx.EVT_TEXT_ENTER, self._on_property_changed)
        self.field_name.Bind(wx.EVT_KILL_FOCUS, self._on_text_field_commit)
        self.field_text.Bind(wx.EVT_KILL_FOCUS, self._on_text_field_commit)
        self.field_label_text.Bind(wx.EVT_KILL_FOCUS, self._on_text_field_commit)
        self.field_control_text.Bind(wx.EVT_KILL_FOCUS, self._on_text_field_commit)
        self.field_x.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_y.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_w.Bind(wx.EVT_SPINCTRL, self._on_width_value_changed)
        self.field_h.Bind(wx.EVT_SPINCTRL, self._on_height_value_changed)
        self.field_x.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_y.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_w.Bind(wx.EVT_TEXT, self._on_width_value_changed)
        self.field_h.Bind(wx.EVT_TEXT, self._on_height_value_changed)
        self.field_layout.Bind(wx.EVT_CHOICE, self._on_property_changed)
        self.field_child_alignment.Bind(wx.EVT_CHOICE, self._on_property_changed)
        self.field_spacing.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_spacing.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_padding_left.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_padding_right.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_padding_top.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_padding_bottom.Bind(wx.EVT_SPINCTRL, self._on_property_changed)
        self.field_padding_left.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_padding_right.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_padding_top.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_padding_bottom.Bind(wx.EVT_TEXT, self._on_property_changed)
        self.field_width_mode.Bind(wx.EVT_CHOICE, self._on_property_changed)
        self.field_height_mode.Bind(wx.EVT_CHOICE, self._on_property_changed)
        self.field_scroll_vertical.Bind(wx.EVT_CHECKBOX, self._on_property_changed)
        self.field_scroll_horizontal.Bind(wx.EVT_CHECKBOX, self._on_property_changed)
        self.field_label_direction.Bind(wx.EVT_CHOICE, self._on_property_changed)

        self._creator_workspace_panels = (left_panel, right_panel)
        self._creator_controls = (
            self.element_choice,
            self.btn_add_root,
            self.btn_add_child,
            self.btn_delete,
            self.btn_save_json,
            self.btn_refresh_snapshot,
            self.btn_toggle_preview,
            self.chk_show_layout,
        )

    def _on_element_type_selected(self, _event: wx.CommandEvent) -> None:
        # Remember selected type so repeated insertions keep the most recent workflow choice.

        selected_type = self.element_choice.GetStringSelection()
        if selected_type != "":
            self._last_element_type = selected_type

    def _set_last_used_element_type(self, element_type: str) -> None:
        # Keep toolbar type selection synchronized with the last inserted element type.

        self._last_element_type = element_type
        index = self.element_choice.FindString(element_type)
        if index != wx.NOT_FOUND:
            self.element_choice.SetSelection(index)

    def _build_element_type_submenu(self, on_select: Callable[[str], None]) -> wx.Menu:
        # Build add-element submenu using the same type list shown in the toolbar dropdown.

        submenu = wx.Menu()
        for element_type in self.ELEMENT_TYPES:
            label = f"{element_type} (last)" if element_type == self._last_element_type else element_type
            item_id = wx.NewIdRef()
            submenu.Append(item_id, label)
            submenu.Bind(wx.EVT_MENU, lambda _evt, selected=element_type: on_select(selected), id=item_id)

        return submenu

    def _build_component_submenu(self, on_select: Callable[[str], None]) -> wx.Menu:
        # Build reusable component insertion menu from current document component definitions.

        submenu = wx.Menu()
        components = self._document.list_components()
        if not components:
            disabled_id = wx.NewIdRef()
            submenu.Append(disabled_id, "No Components")
            submenu.Enable(disabled_id, False)
            return submenu

        for component in components:
            item_id = wx.NewIdRef()
            submenu.Append(item_id, component.name)
            submenu.Bind(wx.EVT_MENU, lambda _evt, component_id=component.id: on_select(component_id), id=item_id)

        return submenu

    def list_component_choices(self) -> list[tuple[str, str]]:
        # Return stable component choices as (id, name) pairs for external UI panels.

        return [(component.id, component.name) for component in self._document.list_components()]

    def get_component_template(self, component_id: str) -> Optional[dict]:
        # Return component template payload for visual preview panels.

        component = self._document.components.get(component_id)
        if component is None:
            return None
        return dict(component.template)

    def rename_component(self, component_id: str, new_name: str) -> None:
        # Rename a reusable component definition.

        self._document.rename_component(component_id, new_name)

    def focus_component_for_edit(self, component_id: str) -> bool:
        # Select first instance root for a component so edits happen in the Creator inspector.

        roots = self._document.get_component_instance_roots(component_id)
        if not roots:
            return False

        target_id = roots[0]
        self._refresh_all(select_id=target_id)
        return True

    @staticmethod
    def _ensure_preview_ids(node: dict, path: str) -> dict:
        # Ensure preview payload nodes have stable IDs required by runtime snapshot parsing.

        copied = dict(node)
        node_id = str(copied.get("id", "")).strip()
        if node_id == "":
            copied["id"] = f"component_preview_{path}"

        children_value = copied.get("children", [])
        children_out: list[dict] = []
        if isinstance(children_value, list):
            for index, child in enumerate(children_value):
                if not isinstance(child, dict):
                    continue
                children_out.append(DesignerPanel._ensure_preview_ids(child, f"{path}_{index}"))
        copied["children"] = children_out
        return copied

    def preview_component_in_game(self, template: dict) -> bool:
        # Push selected component template as snapshot so bridge returns true runtime frame/layout preview.

        if not self._bridge.is_connected:
            self._ensure_bridge_connected_for_project()
            if not self._bridge.is_connected:
                return False

        payload = {
            "type": "snapshot",
            "source": "python-ui-maker-component-preview",
            "payload": {
                "schemaVersion": "1.0.0",
                "roots": [self._ensure_preview_ids(template, "root")],
            },
        }
        return self._bridge.send_json(payload)

    def get_latest_bridge_preview_data(self) -> tuple[Optional[wx.Bitmap], Optional[dict]]:
        # Expose latest runtime frame bitmap and authoritative layout payload for secondary preview surfaces.

        return self._latest_preview_bitmap, self._latest_layout_payload

    def force_snapshot_sync(self) -> None:
        # Reapply current document snapshot to runtime when returning from alternate preview views.

        self._sync_snapshot_to_bridge(force=True)

    def _add_element_by_type(self, element_type: str, parent_id: Optional[str]) -> None:
        # Add element directly from toolbar/context actions while preserving last-used type.

        self._set_last_used_element_type(element_type)
        try:
            element = self._document.add_element(element_type, parent_id)
        except ValueError as ex:
            self._error(str(ex))
            return

        self._refresh_all(select_id=element.id)

    def _add_sibling_by_type(self, element_type: str, target_id: str) -> None:
        # Add element at the same hierarchy depth as the target element.

        try:
            parent_id = self._document.get_parent_id(target_id)
        except ValueError as ex:
            self._error(str(ex))
            return

        self._add_element_by_type(element_type, parent_id)

    def _delete_element_by_id(self, element_id: str) -> None:
        # Delete specific node ID and clear selection when it is removed.

        try:
            self._document.remove_element(element_id)
        except ValueError as ex:
            self._error(str(ex))
            return

        self._refresh_all(select_id=None)

    def _on_tree_item_right_click(self, event: wx.TreeEvent) -> None:
        # Open hierarchy context menu for same-level add, child add, and delete actions.

        item = event.GetItem()
        target_id: Optional[str] = None
        if item.IsOk() and item in self._tree_item_to_id:
            self.tree.SelectItem(item)
            target_id = self._tree_item_to_id[item]

        menu = wx.Menu()
        if target_id is None:
            top_level_submenu = self._build_element_type_submenu(lambda element_type: self._add_element_by_type(element_type, None))
            menu.AppendSubMenu(top_level_submenu, "Add Top-Level")

            component_top_submenu = self._build_component_submenu(lambda component_id: self._insert_component_instance(component_id, None))
            menu.AppendSubMenu(component_top_submenu, "Add Component")

            if self._copied_subtree_payload is not None:
                menu.AppendSeparator()
                paste_root_id = wx.NewIdRef()
                menu.Append(paste_root_id, "Paste")
                menu.Bind(wx.EVT_MENU, lambda _evt: self._paste_subtree(None), id=paste_root_id)
        else:
            sibling_submenu = self._build_element_type_submenu(lambda element_type: self._add_sibling_by_type(element_type, target_id))
            menu.AppendSubMenu(sibling_submenu, "Add Sibling")

            child_submenu = self._build_element_type_submenu(lambda element_type: self._add_element_by_type(element_type, target_id))
            child_item = menu.AppendSubMenu(child_submenu, "Add Child")
            is_container = self._document.elements[target_id].element_type in self.CONTAINER_TYPES
            child_item.Enable(is_container)

            component_submenu = self._build_component_submenu(on_select=lambda component_id: self._insert_component_instance(component_id, target_id))
            menu.AppendSubMenu(component_submenu, "Add Component")

            menu.AppendSeparator()
            convert_id = wx.NewIdRef()
            menu.Append(convert_id, "Convert To Component")
            menu.Bind(wx.EVT_MENU, lambda _evt, node_id=target_id: self._convert_subtree_to_component(node_id), id=convert_id)

            menu.AppendSeparator()
            copy_id = wx.NewIdRef()
            menu.Append(copy_id, "Copy")
            menu.Bind(wx.EVT_MENU, lambda _evt, node_id=target_id: self._copy_subtree(node_id), id=copy_id)

            paste_id = wx.NewIdRef()
            menu.Append(paste_id, "Paste")
            menu.Bind(wx.EVT_MENU, lambda _evt, node_id=target_id: self._paste_subtree(node_id), id=paste_id)
            menu.Enable(paste_id, self._copied_subtree_payload is not None)

            delete_id = wx.NewIdRef()
            menu.Append(delete_id, "Delete")
            menu.Bind(wx.EVT_MENU, lambda _evt, node_id=target_id: self._delete_element_by_id(node_id), id=delete_id)

        self.PopupMenu(menu)
        menu.Destroy()

    def _convert_subtree_to_component(self, element_id: str) -> None:
        # Promote selected subtree to reusable component and keep it as the first live instance.

        if element_id not in self._document.elements:
            self._error("Selected element no longer exists.")
            return

        default_name = self._document.elements[element_id].name
        prompt = wx.TextEntryDialog(self, "Component name", "Convert To Component", default_name)
        if prompt.ShowModal() != wx.ID_OK:
            prompt.Destroy()
            return

        component_name = prompt.GetValue().strip()
        prompt.Destroy()
        if component_name == "":
            self._error("Component name cannot be empty.")
            return

        try:
            self._document.convert_to_component(element_id, component_name)
            self._log(f"Converted subtree to component: {component_name}")
            self._refresh_all(select_id=element_id)
        except Exception as ex:
            self._error(f"Convert to component failed: {ex}")

    def _insert_component_instance(self, component_id: str, target_id: Optional[str]) -> None:
        # Insert one reusable component instance as sibling-after target or at root.

        try:
            inserted_id = self._document.instantiate_component_after(component_id, target_id)
            self._refresh_all(select_id=inserted_id)
        except Exception as ex:
            self._error(f"Add component failed: {ex}")

    def _on_text_field_commit(self, event: wx.FocusEvent) -> None:
        # Apply text field edits on commit to avoid redraw churn while typing.

        self._on_property_changed(None)
        event.Skip()

    def _on_width_value_changed(self, _event: wx.CommandEvent) -> None:
        # Treat direct numeric width edits as manual intent to avoid hidden mode conflicts.

        if not self._is_syncing_fields:
            self.field_width_mode.SetStringSelection(SIZE_MODE_MANUAL)

        self._on_property_changed(_event)

    def _on_height_value_changed(self, _event: wx.CommandEvent) -> None:
        # Treat direct numeric height edits as manual intent to avoid hidden mode conflicts.

        if not self._is_syncing_fields:
            self.field_height_mode.SetStringSelection(SIZE_MODE_MANUAL)

        self._on_property_changed(_event)

    @staticmethod
    def _color_to_hex(color: wx.Colour) -> str:
        # Serialize wx color to #RRGGBB for snapshot/runtime/export consistency.

        return f"#{color.Red():02x}{color.Green():02x}{color.Blue():02x}"

    @staticmethod
    def _hex_to_color(value: str, fallback: wx.Colour) -> wx.Colour:
        # Parse #RRGGBB values from model fields into picker colors.

        text = value.strip().lower()
        if len(text) == 7 and text.startswith("#"):
            try:
                red = int(text[1:3], 16)
                green = int(text[3:5], 16)
                blue = int(text[5:7], 16)
                return wx.Colour(red, green, blue)
            except ValueError:
                return fallback

        return fallback

    def _copy_subtree(self, element_id: str) -> None:
        # Copy selected subtree payload for later paste operations.

        try:
            self._copied_subtree_payload = self._document.copy_subtree(element_id)
            self._log(f"Copied subtree: {self._document.elements[element_id].name}")
        except Exception as ex:
            self._error(f"Copy failed: {ex}")

    def _paste_subtree(self, target_id: Optional[str]) -> None:
        # Paste copied subtree as sibling-after selected target or as root when no target.

        if self._copied_subtree_payload is None:
            self._error("Nothing copied yet.")
            return

        try:
            new_id = self._document.paste_subtree_after(target_id, self._copied_subtree_payload)
            self._refresh_all(select_id=new_id)
        except Exception as ex:
            self._error(f"Paste failed: {ex}")

    def _add_general_row(self, panel: wx.Window, sizer: wx.BoxSizer, key: str, label: str, control: wx.Window) -> None:
        # Keep shared property rows addressable so visibility can follow selected element type.

        label_control = wx.StaticText(panel, label=label)
        sizer.Add(label_control, 0, wx.ALL, 2)
        sizer.Add(control, 0, wx.ALL | wx.EXPAND, 2)
        self._general_rows[key] = (label_control, control)

    def _refresh_project_status(self) -> None:
        # Show active project file so save behavior is always explicit.

        current_path = self._document.file_path
        if current_path is None:
            self.project_status.SetLabel("Project: Unsaved")
            return

        self.project_status.SetLabel(f"Project: {current_path}")

    def _set_creator_enabled(self, enabled: bool) -> None:
        # Gate creator access so editing requires an active project.

        for control in self._creator_controls:
            control.Enable(enabled)

        for panel in self._creator_workspace_panels:
            panel.Enable(enabled)

        if enabled:
            self._ensure_bridge_connected_for_project()
            return

        self._bridge.disconnect()

    def _ensure_bridge_connected_for_project(self) -> None:
        # Keep bridge connection automatic while a project is active.

        if not self._bridge_sync_enabled:
            return

        if self._bridge.is_connected:
            return

        url = self.bridge_url.GetValue().strip()
        if url == "":
            self._error("Bridge URL cannot be empty.")
            return

        self._bridge.connect(url)

    def _on_refresh_snapshot(self, _event: wx.CommandEvent) -> None:
        # Push a forced snapshot immediately and reconnect only when send path is unavailable.

        if self._bridge.is_connected:
            self._log("Manual refresh requested: forcing snapshot push.")
            if self._sync_snapshot_to_bridge(force=True):
                self._log("Manual refresh complete: snapshot sent.")
                return

            self._log("Manual refresh send failed; reconnecting bridge.")
        else:
            self._log("Manual refresh requested while disconnected; reconnecting bridge.")

        self._manual_refresh_waiting_for_connect = True
        self._bridge.disconnect()
        self._ensure_bridge_connected_for_project()

    def _on_toggle_preview(self, _event: wx.CommandEvent) -> None:
        # Toggle in-game preview visibility without disconnecting the websocket session.

        if not self._bridge.is_connected:
            self._error("Bridge is not connected.")
            return

        next_state = not self._preview_connected
        if self._send_preview_visibility(next_state):
            self._preview_connected = next_state
            self.btn_toggle_preview.SetLabel("Disconnect Preview" if next_state else "Connect Preview")
            self._log("Preview connected." if next_state else "Preview disconnected.")

    def set_export_tab_active(self, active: bool) -> None:
        # Hide realtime visualizer while Export tab is open so generated test windows stay isolated.

        if self._export_tab_active == active:
            return

        self._export_tab_active = active
        if not self._bridge.is_connected:
            return

        if active:
            if self._send_preview_visibility(False):
                self._log("Export tab active: visualizer hidden.")
            return

        if self._send_preview_visibility(self._preview_connected):
            self._log("Export tab inactive: visualizer restored.")

    def connect_bridge_for_export(self, reconnect: bool = False) -> None:
        # Expose explicit export-tab bridge connect/reconnect controls.

        if reconnect and self._bridge.is_connected:
            self._bridge.disconnect()

        self._ensure_bridge_connected_for_project()
        if self._bridge.is_connected and self._export_tab_active:
            self._send_preview_visibility(False)

    def get_bridge_state_text(self) -> str:
        # Report current bridge state for export-tab status indicators.

        return self._bridge_state_text

    def _send_preview_visibility(self, connected: bool) -> bool:
        # Send explicit preview visibility command to the runtime bridge.

        payload = {
            "type": "preview-visibility",
            "connected": connected,
        }
        return self._bridge.send_json(payload)

    def _set_general_property_visibility(self, node: UIElement) -> None:
        # Show only relevant base properties for the selected element type.

        relevant = {"name", "width", "height"}
        if node.element_type in self.TEXT_TYPES:
            relevant.update({"text_alignment", "text_override", "text_color"})
            if node.element_type in self.LABELED_TYPES:
                relevant.add("label_text")
                if node.element_type in self.LABELED_WITH_CONTROL_TEXT_TYPES:
                    relevant.add("control_text")
            else:
                relevant.add("text")
        if node.element_type != "Space":
            relevant.update({"background_override", "background_color", "color_note"})
        if node.element_type in self.CONTAINER_TYPES:
            relevant.update({"layout", "child_alignment", "spacing", "padding"})
        relevant.update({"width_mode", "height_mode"})
        if node.element_type in self.CONTAINER_TYPES:
            relevant.update({"scroll_vertical", "scroll_horizontal"})
        if node.element_type in self.LABELED_TYPES:
            relevant.add("label_direction")

        for key, (label, control) in self._general_rows.items():
            visible = key in relevant
            label.Show(visible)
            control.Show(visible)

    def _seed_document(self) -> None:
        # Initialize with one root window so the workspace is immediately usable.

        self._document.elements.clear()
        self._document.roots.clear()
        self._document.file_path = None

        window = self._document.add_element("Window", None)
        window.width = 360
        window.height = 240
        window.text = "Main Window"

        label = self._document.add_element("Label", window.id)
        label.x = 20
        label.y = 20
        label.width = 200
        label.height = 30
        label.text = "Designer Ready"

        self._refresh_all(select_id=window.id)
        self._refresh_project_status()

    def _on_new_project(self, _event: wx.CommandEvent) -> None:
        # Create a brand-new project JSON first, then start editing against that file.

        dialog = wx.FileDialog(
            self,
            "Create New UI Project",
            wildcard="JSON UI file (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dialog.ShowModal() != wx.ID_OK:
            return

        file_path = Path(dialog.GetPath())
        self._seed_document()
        try:
            self._document.save_to_file(file_path)
            self._log(f"New project created at {file_path}")
            self._refresh_project_status()
            self._set_creator_enabled(True)
            self._schedule_snapshot_sync()
        except Exception as ex:
            self._error(f"New project creation failed: {ex}")
            self._document.elements.clear()
            self._document.roots.clear()
            self._selection = None
            self._document.file_path = None
            self._refresh_project_status()
            self._refresh_all(select_id=None, sync_bridge=False)
            self._set_creator_enabled(False)

    def _on_add_root(self, _event: wx.CommandEvent) -> None:
        # Add a new top-level node to the scene hierarchy.

        element_type = self.element_choice.GetStringSelection()
        if element_type == "":
            self._error("Choose an element type before adding a root.")
            return

        self._add_element_by_type(element_type, None)

    def _on_add_child(self, _event: wx.CommandEvent) -> None:
        # Add a child under currently selected container-capable node.

        if self._selection is None:
            self._error("Select a parent before adding a child.")
            return

        element_type = self.element_choice.GetStringSelection()
        if element_type == "":
            self._error("Choose an element type before adding a child.")
            return

        self._add_element_by_type(element_type, self._selection)

    def _on_delete(self, _event: wx.CommandEvent) -> None:
        # Delete selected node and its subtree from the document.

        if self._selection is None:
            self._error("Select an element to delete.")
            return

        self._delete_element_by_id(self._selection)

    def _reorder_layer(self, direction: int) -> None:
        # Move selected node up or down among siblings.

        if self._selection is None:
            self._error("Select an element to reorder.")
            return

        moved = self._document.move_layer(self._selection, direction)
        if not moved:
            self._log("Layer move ignored: already at boundary.")
            return

        self._refresh_all(select_id=self._selection)

    def _on_save_document(self, _event: wx.CommandEvent) -> None:
        # Save the UI document to a JSON file; prompt for location if not previously saved.

        if self._document.file_path is not None:
            try:
                self._document.save_to_file(self._document.file_path)
                self._log(f"Document saved to {self._document.file_path}")
                self._refresh_project_status()
                return
            except Exception as ex:
                self._error(f"Save failed: {ex}")
                self._document.file_path = None
                self._refresh_project_status()

        dialog = wx.FileDialog(
            self,
            "Save UI Document",
            wildcard="JSON UI file (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dialog.ShowModal() != wx.ID_OK:
            return

        file_path = Path(dialog.GetPath())
        try:
            self._document.save_to_file(file_path)
            self._log(f"Document saved to {file_path}")
            self._refresh_project_status()
        except Exception as ex:
            self._error(f"Save failed: {ex}")

    def _on_load_document(self, _event: wx.CommandEvent) -> None:
        # Load a UI document from a JSON file and populate the editor.

        dialog = wx.FileDialog(
            self,
            "Load UI Document",
            wildcard="JSON UI file (*.json)|*.json",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dialog.ShowModal() != wx.ID_OK:
            return

        file_path = Path(dialog.GetPath())
        self._document.file_path = None
        self._refresh_project_status()
        try:
            self._document.load_from_file(file_path)
            self._log(f"Document loaded from {file_path}")
            self._refresh_project_status()
            self._set_creator_enabled(True)
            self._refresh_all(select_id=None, sync_bridge=True)
        except Exception as ex:
            self._error(f"Load failed: {ex}")
            self._document.elements.clear()
            self._document.roots.clear()
            self._selection = None
            self._document.file_path = None
            self._refresh_project_status()
            self._refresh_all(select_id=None, sync_bridge=False)
            self._set_creator_enabled(False)

    def _sync_snapshot_to_bridge(self, force: bool = False) -> bool:
        # Keep SFS renderer as the authoritative layout engine for live editing.

        if not self._bridge_sync_enabled or not self._bridge.is_connected or self._applying_remote_snapshot or self._export_tab_active:
            return False

        payload_obj = {
            "type": "snapshot",
            "source": "python-ui-maker",
            "payload": self._document.to_dict(),
        }
        signature = json.dumps(payload_obj["payload"], sort_keys=True, separators=(",", ":"))
        if not force and signature == self._last_sent_snapshot_signature:
            return False

        if self._bridge.send_json(payload_obj):
            self._last_sent_snapshot_signature = signature
            return True

        return False

    def _schedule_snapshot_sync(self) -> None:
        # Debounce edit bursts so preview sync stays responsive without flooding websocket sends.

        if self._export_tab_active:
            return

        if self._pending_sync is not None and self._pending_sync.IsRunning():
            self._pending_sync.Stop()

        self._pending_sync = wx.CallLater(self._snapshot_sync_delay_ms, self._flush_snapshot_sync)

    def _flush_snapshot_sync(self) -> None:
        # Flush one pending sync request after debounce delay.

        self._pending_sync = None
        self._sync_snapshot_to_bridge()

    def _on_tree_select(self, event: wx.TreeEvent) -> None:
        # Sync tree selection to canvas and property inspector.

        item = event.GetItem()
        if not item.IsOk():
            return

        if item in self._tree_item_to_id:
            self._selection = self._tree_item_to_id[item]
        else:
            self._selection = None

        self._refresh_selection_only()

    def _on_tree_begin_drag(self, event: wx.TreeEvent) -> None:
        # Begin drag-drop for hierarchy reorganization.

        item = event.GetItem()
        if not item.IsOk() or item not in self._tree_item_to_id:
            return

        self._dragging_tree_node_id = self._tree_item_to_id[item]
        event.Allow()

    def _on_tree_end_drag(self, event: wx.TreeEvent) -> None:
        # Reparent or reorder nodes based on precise drop zone (before/inside/after).

        dragging_id = self._dragging_tree_node_id
        self._dragging_tree_node_id = None
        if dragging_id is None:
            return

        target_item = event.GetItem()
        drop_point = event.GetPoint()

        if not isinstance(drop_point, wx.Point):
            drop_point = self.tree.ScreenToClient(wx.GetMousePosition())

        if not target_item.IsOk():
            moved = self._document.move_element(dragging_id, None)
            if moved:
                self._refresh_all(select_id=dragging_id)
            return

        target_id = self._tree_item_to_id.get(target_item)
        if target_id is None:
            moved = self._document.move_element(dragging_id, None)
            if moved:
                self._refresh_all(select_id=dragging_id)
            return

        if target_id == dragging_id:
            return

        rect = self.tree.GetBoundingRect(target_item, textOnly=False)
        if not rect.IsEmpty():
            relative_y = drop_point.y - rect.y
            top_zone = rect.height * 0.33
            bottom_zone = rect.height * 0.66
            if relative_y < top_zone:
                drop_mode = "before"
            elif relative_y > bottom_zone:
                drop_mode = "after"
            else:
                drop_mode = "inside"
        else:
            drop_mode = "inside"

        # Allow Ctrl key to force placing the dragged node inside the target (make child).
        try:
            if wx.GetKeyState(wx.WXK_CONTROL):
                drop_mode = "inside"
        except Exception:
            pass

        # If requested to place inside and the target can accept children, perform reparent.
        if drop_mode == "inside" and self._document.elements[target_id].element_type in self.CONTAINER_TYPES:
            moved = self._document.move_element(dragging_id, target_id)
            if moved:
                self._refresh_all(select_id=dragging_id)
            return

        # If inside was requested but the target is not a container, fall back to sibling insert after.
        if drop_mode == "inside" and self._document.elements[target_id].element_type not in self.CONTAINER_TYPES:
            drop_mode = "after"

        parent_id = self._document.get_parent_id(target_id)
        siblings = self._document.roots if parent_id is None else self._document.elements[parent_id].children
        target_index = siblings.index(target_id)
        insert_index = target_index if drop_mode == "before" else target_index + 1

        moved = self._document.move_element(dragging_id, parent_id, insert_index)
        if moved:
            self._refresh_all(select_id=dragging_id)

    def _on_canvas_select(self, element_id: Optional[str]) -> None:
        # Sync canvas click selection into tree and property inspector.

        self._selection = element_id
        self._refresh_selection_only()

    def _on_property_changed(self, _event) -> None:
        # Apply inspector edits directly into selected element properties.

        if self._selection is None or self._is_syncing_fields:
            return

        node = self._document.elements[self._selection]
        previous_state = (
            node.name,
            node.text,
            node.text_alignment,
            node.text_color_override,
            node.text_color,
            node.background_color_override,
            node.background_color,
            node.x,
            node.y,
            node.width,
            node.height,
            node.layout,
            node.child_alignment,
            node.spacing,
            node.padding_left,
            node.padding_right,
            node.padding_top,
            node.padding_bottom,
            node.width_mode,
            node.height_mode,
            node.scroll_vertical,
            node.scroll_horizontal,
            str(node.props.get("label_direction", "")),
            str(node.props.get("label_text", "")),
            str(node.props.get("control_text", "")),
        )
        old_name = node.name
        supports_text = node.element_type in self.TEXT_TYPES
        supports_background = node.element_type != "Space"
        supports_text_color_override = node.element_type in self.TEXT_COLOR_OVERRIDE_SUPPORTED_TYPES
        supports_background_color_override = node.element_type in self.BACKGROUND_COLOR_OVERRIDE_SUPPORTED_TYPES

        node.name = self.field_name.GetValue().strip() or node.name
        if supports_text:
            if node.element_type in self.LABELED_TYPES:
                label_text = self.field_label_text.GetValue()
                node.props["label_text"] = label_text
                node.text = label_text
                if node.element_type in self.LABELED_WITH_CONTROL_TEXT_TYPES:
                    node.props["control_text"] = self.field_control_text.GetValue()
                else:
                    node.props.pop("control_text", None)
            else:
                node.text = self.field_text.GetValue()
                node.props.pop("label_text", None)
                node.props.pop("control_text", None)
            node.text_alignment = self.field_text_alignment.GetStringSelection() or "Left"
            node.text_color_override = self.field_text_color_override.GetValue() if supports_text_color_override else False
            if node.text_color_override:
                node.text_color = self._color_to_hex(self.field_text_color.GetColour())

        if supports_background:
            node.background_color_override = self.field_background_color_override.GetValue() if supports_background_color_override else False
            if node.background_color_override:
                node.background_color = self._color_to_hex(self.field_background_color.GetColour())

        node.x = max(SAFE_MIN_POSITION, min(SAFE_MAX_POSITION, self.field_x.GetValue()))
        node.y = max(SAFE_MIN_POSITION, min(SAFE_MAX_POSITION, self.field_y.GetValue()))
        node.width = max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, self.field_w.GetValue()))
        node.height = max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, self.field_h.GetValue()))
        node.layout = self.field_layout.GetStringSelection() or "Vertical"
        node.child_alignment = self.field_child_alignment.GetStringSelection() or "UpperLeft"
        node.spacing = max(SAFE_MIN_SPACING, min(SAFE_MAX_SPACING, self.field_spacing.GetValue()))
        node.padding_left = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, self.field_padding_left.GetValue()))
        node.padding_right = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, self.field_padding_right.GetValue()))
        node.padding_top = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, self.field_padding_top.GetValue()))
        node.padding_bottom = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, self.field_padding_bottom.GetValue()))
        node.width_mode = normalize_size_mode(self.field_width_mode.GetStringSelection() or SIZE_MODE_MANUAL, "width")
        node.height_mode = normalize_size_mode(self.field_height_mode.GetStringSelection() or SIZE_MODE_MANUAL, "height")
        node.scroll_vertical = self.field_scroll_vertical.GetValue()
        node.scroll_horizontal = self.field_scroll_horizontal.GetValue()
        if node.element_type in self.LABELED_TYPES:
            node.props["label_direction"] = self.field_label_direction.GetStringSelection() or "Top"
        else:
            node.props.pop("label_direction", None)

        current_state = (
            node.name,
            node.text,
            node.text_alignment,
            node.text_color_override,
            node.text_color,
            node.background_color_override,
            node.background_color,
            node.x,
            node.y,
            node.width,
            node.height,
            node.layout,
            node.child_alignment,
            node.spacing,
            node.padding_left,
            node.padding_right,
            node.padding_top,
            node.padding_bottom,
            node.width_mode,
            node.height_mode,
            node.scroll_vertical,
            node.scroll_horizontal,
            str(node.props.get("label_direction", "")),
            str(node.props.get("label_text", "")),
            str(node.props.get("control_text", "")),
        )
        if current_state == previous_state:
            return

        synced_count = self._document.sync_component_from_node(self._selection)

        source_control: Optional[wx.Window]
        if _event is None:
            source_control = None
        else:
            try:
                source_control = _event.GetEventObject()
            except Exception:
                source_control = None

        if node.name != old_name:
            self._rebuild_tree(self._selection)
        elif synced_count > 0 and source_control is not None and source_control == self.field_name:
            self._rebuild_tree(self._selection)

        controls_requiring_ui_refresh = {
            self.field_width_mode,
            self.field_height_mode,
            self.field_text_color_override,
            self.field_background_color_override,
        }
        if source_control in controls_requiring_ui_refresh or _event is None:
            self._refresh_selection_only()

        self.canvas.Refresh()
        self._schedule_snapshot_sync()

        if _event is not None:
            style_controls = {
                self.field_text_color,
                self.field_text_color_override,
                self.field_background_color_override,
                self.field_background_color,
            }

            if source_control in style_controls:
                self._sync_snapshot_to_bridge(force=True)

    def _refresh_all(self, select_id: Optional[str], sync_bridge: bool = True) -> None:
        # Redraw tree, canvas, and inspector from document source of truth.

        self._rebuild_tree(select_id)
        self._selection = select_id
        self._refresh_selection_only()
        self.canvas.Refresh()
        if sync_bridge:
            self._schedule_snapshot_sync()

    def _refresh_selection_only(self) -> None:
        # Update only selection-dependent controls to avoid full rebuild overhead.

        self.canvas.set_selected(self._selection)

        self._is_syncing_fields = True
        try:
            if self._selection is None or self._selection not in self._document.elements:
                for field in (self.field_name, self.field_text):
                    field.SetValue("")
                for spin in (self.field_x, self.field_y, self.field_w, self.field_h):
                    spin.SetValue(0 if spin in (self.field_x, self.field_y) else 20)
                for _, (label, control) in self._general_rows.items():
                    label.Show(False)
                    control.Show(False)
                self.Layout()
                return

            node = self._document.elements[self._selection]
            self._set_general_property_visibility(node)
            self.field_name.SetValue(node.name)
            self.field_text.SetValue(node.text)
            label_text_value = str(node.props.get("label_text", node.text))
            control_text_value = str(node.props.get("control_text", node.text))
            self.field_label_text.SetValue(label_text_value)
            self.field_control_text.SetValue(control_text_value)
            text_alignment_index = self.field_text_alignment.FindString(node.text_alignment)
            self.field_text_alignment.SetSelection(text_alignment_index if text_alignment_index != wx.NOT_FOUND else 0)
            self.field_text_color_override.SetValue(node.text_color_override)
            self.field_background_color_override.SetValue(node.background_color_override)
            self.field_text_color.SetColour(self._hex_to_color(node.text_color, wx.Colour(255, 255, 255)))
            self.field_background_color.SetColour(self._hex_to_color(node.background_color, wx.Colour(40, 40, 40)))

            supports_text = node.element_type in self.TEXT_TYPES
            supports_text_color_override = node.element_type in self.TEXT_COLOR_OVERRIDE_SUPPORTED_TYPES
            allows_text_color = supports_text_color_override and node.text_color_override
            self.field_text_color_override.Enable(supports_text_color_override)
            self.field_text_color.Enable(allows_text_color)
            supports_background = node.element_type != "Space"
            supports_background_color_override = node.element_type in self.BACKGROUND_COLOR_OVERRIDE_SUPPORTED_TYPES
            allows_background_color = supports_background_color_override and node.background_color_override
            self.field_background_color_override.Enable(supports_background_color_override)
            self.field_background_color.Enable(allows_background_color)
            unsupported: list[str] = []
            if supports_text and not supports_text_color_override:
                unsupported.append("text")
            if supports_background and not supports_background_color_override:
                unsupported.append("background")
            if unsupported:
                channels = " and ".join(unsupported)
                self.color_support_note.SetLabel(f"Native ModGUI does not expose {channels} color override for {node.element_type}.")
            else:
                self.color_support_note.SetLabel("This element supports native color overrides.")
            self.color_support_note.Wrap(300)
            self.field_text_color.GetParent().Layout()
            self.field_background_color.GetParent().Layout()
            self.field_x.SetValue(node.x)
            self.field_y.SetValue(node.y)
            self.field_w.SetValue(node.width)
            self.field_h.SetValue(node.height)
            layout_index = self.field_layout.FindString(node.layout)
            self.field_layout.SetSelection(layout_index if layout_index != wx.NOT_FOUND else 0)
            alignment_index = self.field_child_alignment.FindString(node.child_alignment)
            self.field_child_alignment.SetSelection(alignment_index if alignment_index != wx.NOT_FOUND else 0)
            self.field_spacing.SetValue(node.spacing)
            self.field_padding_left.SetValue(node.padding_left)
            self.field_padding_right.SetValue(node.padding_right)
            self.field_padding_top.SetValue(node.padding_top)
            self.field_padding_bottom.SetValue(node.padding_bottom)
            width_mode_index = self.field_width_mode.FindString(node.width_mode)
            self.field_width_mode.SetSelection(width_mode_index if width_mode_index != wx.NOT_FOUND else 0)
            height_mode_index = self.field_height_mode.FindString(node.height_mode)
            self.field_height_mode.SetSelection(height_mode_index if height_mode_index != wx.NOT_FOUND else 0)
            self.field_scroll_vertical.SetValue(node.scroll_vertical)
            self.field_scroll_horizontal.SetValue(node.scroll_horizontal)
            default_label_direction = "Left" if node.element_type == "ToggleWithLabel" else "Top"
            label_direction = str(node.props.get("label_direction", default_label_direction))
            label_direction_index = self.field_label_direction.FindString(label_direction)
            self.field_label_direction.SetSelection(label_direction_index if label_direction_index != wx.NOT_FOUND else 0)

            width_is_manual = node.width_mode == SIZE_MODE_MANUAL
            height_is_manual = node.height_mode == SIZE_MODE_MANUAL
            width_label, width_control = self._general_rows["width"]
            height_label, height_control = self._general_rows["height"]
            width_label.SetLabel("Width (Manual px)" if width_is_manual else f"Width ({node.width_mode})")
            height_label.SetLabel("Height (Manual px)" if height_is_manual else f"Height ({node.height_mode})")
            width_label.Show(True)
            width_control.Show(True)
            height_label.Show(True)
            height_control.Show(True)
            width_control.Enable(width_is_manual)
            height_control.Enable(height_is_manual)
            self.Layout()
        finally:
            self._is_syncing_fields = False

    def _rebuild_tree(self, select_id: Optional[str]) -> None:
        # Recreate hierarchy tree based on current document model.

        self.tree.DeleteAllItems()
        self._tree_item_to_id = {}
        root_item = self.tree.AddRoot("Scene")

        def add_node(parent_item: wx.TreeItemId, node_id: str) -> None:
            node = self._document.elements[node_id]
            if self._scope_hide_window_nodes and node.element_type in {"Window", "ClosableWindow"}:
                for child_id in node.children:
                    if child_id in self._document.elements:
                        add_node(parent_item, child_id)
                return

            label = f"{node.name} [{node.element_type}]"
            component_binding = self._document.get_component_binding(node_id)
            if component_binding is not None:
                _component_id, component_name, is_instance_root = component_binding
                if is_instance_root:
                    label += f" [CR:{component_name}]"
                else:
                    label += " [♳]"

            item = self.tree.AppendItem(parent_item, label)
            self._tree_item_to_id[item] = node_id
            for child_id in node.children:
                add_node(item, child_id)

        for node_id in self._iter_scope_roots():
            add_node(root_item, node_id)

        self.tree.ExpandAll()

        if select_id is None:
            self.tree.SelectItem(root_item)
            return

        for item, node_id in self._tree_item_to_id.items():
            if node_id == select_id:
                self.tree.SelectItem(item)
                return

    def generate_export_code(self) -> str:
        # Generate one C# artifact with layout, bindings, and manifest metadata.

        return generate_csharp_export(self._document.to_dict(), self._document.file_path)

    def generate_markdown_guide(self) -> str:
        # Generate compact Markdown DSL guide for manual UI implementation.

        return generate_markdown_layout_guide(self._document.to_dict(), self._document.file_path)

    def validate_markdown_contract(self, markdown_contract: str) -> tuple[bool, str]:
        # Compare runtime-rendered tree payload against the current document tree contract.

        _ = markdown_contract

        if not self._bridge.is_connected:
            return False, "Bridge is not connected. Connect bridge and refresh preview before validating contract."

        if self._latest_layout_payload is None:
            return False, "No runtime layout payload available yet. Refresh snapshot and wait for layout frame."

        contract_rows = self._build_document_contract_rows()

        nodes = self._latest_layout_payload.get("nodes") if isinstance(self._latest_layout_payload, dict) else None
        if not isinstance(nodes, list):
            return False, "Runtime layout payload is missing nodes array."

        runtime_rows: dict[str, dict] = {}
        for item in nodes:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                runtime_rows[str(item["id"])] = item

        def to_int(value: object, default: int = 0) -> int:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(round(value))
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError:
                    return default
            return default

        mismatches: list[str] = []
        if len(runtime_rows) != len(contract_rows):
            mismatches.append(f"node count differs: runtime={len(runtime_rows)} contract={len(contract_rows)}")

        for node_id, row in contract_rows.items():
            runtime = runtime_rows.get(node_id)
            if runtime is None:
                mismatches.append(f"missing runtime node id={node_id}")
                continue

            parent_id = str(runtime.get("parent_id", ""))
            if parent_id != row["parent_id"]:
                mismatches.append(f"{node_id}: parent mismatch runtime={parent_id} contract={row['parent_id']}")

            path_key = str(runtime.get("path_key", ""))
            if path_key != row["path_key"]:
                mismatches.append(f"{node_id}: pathKey mismatch runtime={path_key} contract={row['path_key']}")

            runtime_width = int(round(float(runtime.get("expected_width", runtime.get("width", 0)))))
            runtime_height = int(round(float(runtime.get("expected_height", runtime.get("height", 0)))))
            contract_width = to_int(row["width"])
            contract_height = to_int(row["height"])
            width_mode = str(row["width_mode"])
            height_mode = str(row["height_mode"])
            if width_mode == SIZE_MODE_MANUAL:
                if runtime_width != contract_width:
                    mismatches.append(f"{node_id}: width mismatch runtime={runtime_width} contract={contract_width}")
            else:
                rendered_width = int(round(float(runtime.get("width", 0))))
                if rendered_width != runtime_width:
                    mismatches.append(f"{node_id}: width resolution mismatch rendered={rendered_width} resolved={runtime_width}")

            if height_mode == SIZE_MODE_MANUAL:
                if runtime_height != contract_height:
                    mismatches.append(f"{node_id}: height mismatch runtime={runtime_height} contract={contract_height}")
            else:
                rendered_height = int(round(float(runtime.get("height", 0))))
                if rendered_height != runtime_height:
                    mismatches.append(f"{node_id}: height resolution mismatch rendered={rendered_height} resolved={runtime_height}")

            layout = str(runtime.get("layout", "Vertical"))
            if layout != row["layout"]:
                mismatches.append(f"{node_id}: layout mismatch runtime={layout} contract={row['layout']}")

            alignment = str(runtime.get("child_alignment", "UpperLeft"))
            if alignment != row["child_alignment"]:
                mismatches.append(f"{node_id}: align mismatch runtime={alignment} contract={row['child_alignment']}")

            if str(runtime.get("width_mode", SIZE_MODE_MANUAL)) != width_mode:
                mismatches.append(f"{node_id}: widthMode mismatch")
            if str(runtime.get("height_mode", SIZE_MODE_MANUAL)) != height_mode:
                mismatches.append(f"{node_id}: heightMode mismatch")
            if bool(runtime.get("scroll_vertical", False)) != bool(row["scroll_vertical"]):
                mismatches.append(f"{node_id}: scrollV mismatch")
            if bool(runtime.get("scroll_horizontal", False)) != bool(row["scroll_horizontal"]):
                mismatches.append(f"{node_id}: scrollH mismatch")

            text_alignment = str(runtime.get("text_alignment", "Left"))
            if text_alignment != row["text_alignment"]:
                mismatches.append(f"{node_id}: textAlign mismatch runtime={text_alignment} contract={row['text_alignment']}")

        if not mismatches:
            return True, f"Contract validation passed for {len(contract_rows)} nodes."

        preview = "; ".join(mismatches[:10])
        suffix = "" if len(mismatches) <= 10 else f"; ... {len(mismatches) - 10} more"
        return False, f"Contract validation failed: {preview}{suffix}"

    def _build_document_contract_rows(self) -> dict[str, dict[str, object]]:
        # Build canonical contract rows from the current document tree, independent of markdown rendering format.

        rows: dict[str, dict[str, object]] = {}

        def sanitize(name: str) -> str:
            cleaned = (name or "").strip().replace("/", "_").replace("\\", "_")
            return cleaned if cleaned != "" else "Node"

        def walk(node_id: str, parent_id: str, parent_path: str, sibling_index: int) -> None:
            node = self._document.elements[node_id]
            segment = sanitize(node.name)
            path_key = f"{parent_path}/{segment}[{sibling_index}]" if parent_path != "" else f"{segment}[{sibling_index}]"

            rows[node_id] = {
                "parent_id": parent_id,
                "path_key": path_key,
                "width": node.width,
                "height": node.height,
                "width_mode": node.width_mode,
                "height_mode": node.height_mode,
                "layout": node.layout,
                "child_alignment": node.child_alignment,
                "scroll_vertical": node.scroll_vertical,
                "scroll_horizontal": node.scroll_horizontal,
                "text_alignment": node.text_alignment,
            }

            child_counts: dict[str, int] = {}
            for child_id in node.children:
                child = self._document.elements[child_id]
                child_segment = sanitize(child.name)
                index = child_counts.get(child_segment, 0)
                child_counts[child_segment] = index + 1
                walk(child_id, node_id, path_key, index)

        root_counts: dict[str, int] = {}
        for root_id in self._document.roots:
            root = self._document.elements[root_id]
            root_segment = sanitize(root.name)
            index = root_counts.get(root_segment, 0)
            root_counts[root_segment] = index + 1
            walk(root_id, "", "", index)

        return rows

    def run_generated_csharp_test(self, source_code: str) -> bool:
        # Request runtime-generated C# interpretation test through the local websocket bridge.

        if source_code.strip() == "":
            self._error("Generated C# output is empty.")
            return False

        if not self._bridge.is_connected:
            self._pending_generated_retry_source = source_code
            self._ensure_bridge_connected_for_project()
            self._log("Bridge not connected; queued generated window request for reconnect.")
            return True

        self._pending_generated_test_source = source_code
        self._generated_test_restart_attempted = False
        return self._send_generated_test_command(source_code)

    def _send_generated_test_command(self, source_code: str) -> bool:
        # Send one generated C# test command request over the bridge.

        # Generated-window tests require preview visibility to be enabled before execution.
        self._send_preview_visibility(True)

        payload = {
            "type": "test-generated-ui",
            "code": source_code,
            "entry_type": "GeneratedUI.GeneratedLayout",
            "request_id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }
        sent = self._bridge.send_json(payload)
        if sent:
            self._log("Generated C# test command sent to game runtime.")
        else:
            self._log("Generated C# test command failed to send.")
        return sent

    def queue_generated_test_after_restart(self, source_code: str) -> None:
        # Reconnect bridge after restart and retry the pending generated C# test command.

        self._pending_generated_test_source = source_code
        self._generated_test_restart_attempted = False
        self._pending_generated_retry_source = source_code
        self._bridge.disconnect()
        self._ensure_bridge_connected_for_project()

    def append_designer_log(self, message: str) -> None:
        # Expose designer log sink for sibling panels such as Export.

        self._log(message)

    def _log(self, message: str) -> None:
        # Add timestamped line to creator/visualizer log panel.

        timestamp = time.strftime("%H:%M:%S")
        self.log_box.AppendText(f"[{timestamp}] {message}\n")

    def _log_threadsafe(self, message: str) -> None:
        # Forward bridge logs safely to UI thread.

        wx.CallAfter(self._log, message)

    def _set_bridge_state_threadsafe(self, state: str) -> None:
        # Forward bridge state text safely to UI thread.

        def apply_state() -> None:
            self._bridge_state_text = state
            self.bridge_state.SetLabel(f"Bridge: {state}")
            if state == "Connected":
                if self._pending_generated_retry_source is not None:
                    retry_source = self._pending_generated_retry_source
                    self._pending_generated_retry_source = None
                    self._send_generated_test_command(retry_source)
                    return

                if self._export_tab_active:
                    if self._send_preview_visibility(False):
                        self._log("Reapplied export-tab visualizer hidden state after reconnect.")
                elif not self._preview_connected:
                    if self._send_preview_visibility(False):
                        self._log("Reapplied disconnected preview state after reconnect.")

                if self._manual_refresh_waiting_for_connect:
                    self._manual_refresh_waiting_for_connect = False
                    if not self._export_tab_active and self._sync_snapshot_to_bridge(force=True):
                        self._log("Manual refresh complete: snapshot sent after reconnect.")
                    else:
                        self._log("Manual refresh skipped after reconnect while Export tab is active.")
                else:
                    self._schedule_snapshot_sync()

        wx.CallAfter(apply_state)

    def _on_bridge_json_threadsafe(self, payload: dict) -> None:
        # Handle inbound JSON payloads from the bridge with explicit parsing rules.

        wx.CallAfter(self._on_bridge_json, payload)

    def _on_bridge_json(self, payload: dict) -> None:
        # Apply supported message types and log unsupported ones explicitly.

        if "type" not in payload:
            self._log("Ignored JSON message without type field.")
            return

        msg_type = payload["type"]
        if msg_type == "frame":
            image_data = payload.get("data")
            if isinstance(image_data, str) and image_data != "":
                self._render_live_preview(image_data)
            return

        if msg_type == "layout":
            self._latest_layout_payload = payload
            self.canvas.set_authoritative_layout(payload)
            return

        if msg_type == "snapshot" and isinstance(payload.get("payload"), dict):
            self._applying_remote_snapshot = True
            try:
                self._document.from_dict(payload["payload"])
                self._refresh_all(select_id=None, sync_bridge=False)
                self._log("Snapshot applied from game bridge.")
            finally:
                self._applying_remote_snapshot = False
            return

        if msg_type == "test-queued":
            self._log("Generated C# test queued in runtime.")
            return

        if msg_type == "test-result":
            status = str(payload.get("status", "unknown"))
            message = str(payload.get("message", ""))
            if message == "":
                self._log(f"Generated C# test result: {status}")
            else:
                self._log(f"Generated C# test result: {status} - {message}")

            if status == "restart-required":
                self._log("Generated C# restart-required status received, but restart flow is disabled in interpret mode.")
            elif status in {"success", "runtime-error", "compile-error", "invalid-request", "unsupported"}:
                self._pending_generated_test_source = None
            return

        self._log(f"Received message type: {msg_type}")

    def _render_live_preview(self, base64_png: str) -> None:
        # Decode incoming frame data and render scaled preview bitmap in the designer panel.

        if time.time() - self._last_preview_render_time < 0.25:
            return

        self._last_preview_render_time = time.time()

        try:
            raw = base64.b64decode(base64_png, validate=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(raw)
                temp_path = tmp.name

            image = wx.Image(temp_path, wx.BITMAP_TYPE_PNG)
            os.remove(temp_path)
        except Exception as ex:
            self._error(f"Frame decode failed: {ex}")
            return

        if not image.IsOk():
            self._error("Frame decode failed: invalid image payload.")
            return

        bitmap = wx.Bitmap(image)
        self._latest_preview_bitmap = bitmap
        self.canvas.set_preview_bitmap(bitmap)

    def _error(self, message: str) -> None:
        # Show explicit user-facing error without silent fallback behavior.

        wx.MessageBox(message, "UI Maker", wx.OK | wx.ICON_ERROR)


class ExportPanel(wx.Panel):
    # Provide one place to preview, copy, and save generated C# or Markdown exports.

    def __init__(
        self,
        parent: wx.Window,
        get_export_code: Callable[[], str],
        get_markdown_guide: Callable[[], str],
        on_validate_contract: Callable[[str], tuple[bool, str]],
        on_log: Callable[[str], None],
        on_test_generated: Callable[[str], bool],
        on_connect_bridge: Callable[[bool], None],
        get_bridge_state: Callable[[], str],
    ):
        super().__init__(parent)
        self._get_export_code = get_export_code
        self._get_markdown_guide = get_markdown_guide
        self._on_validate_contract = on_validate_contract
        self._on_log = on_log
        self._on_test_generated = on_test_generated
        self._on_connect_bridge = on_connect_bridge
        self._get_bridge_state = get_bridge_state
        self._mode = "C#"
        self._build_ui()

    def _build_ui(self) -> None:
        # Build export-focused controls and a read-only preview editor.

        root = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.mode_choice = wx.Choice(self, choices=["C#", "Markdown Guide"])
        self.mode_choice.SetSelection(0)
        self.btn_refresh_preview = wx.Button(self, label="Refresh Preview")
        self.btn_copy = wx.Button(self, label="Copy C#")
        self.btn_export = wx.Button(self, label="Export C#")
        self.btn_validate_contract = wx.Button(self, label="Validate Contract")
        self.btn_test_in_game = wx.Button(self, label="Show Generated Window")
        toolbar.Add(wx.StaticText(self, label="Format"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        toolbar.Add(self.mode_choice, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        for control in (self.btn_refresh_preview, self.btn_copy, self.btn_export, self.btn_validate_contract):
            toolbar.Add(control, 0, wx.ALL, 4)

        bridge_toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_connect_bridge = wx.Button(self, label="Connect Bridge")
        self.btn_reconnect_bridge = wx.Button(self, label="Reconnect Bridge")
        bridge_toolbar.Add(self.btn_connect_bridge, 0, wx.ALL, 4)
        bridge_toolbar.Add(self.btn_reconnect_bridge, 0, wx.ALL, 4)
        bridge_toolbar.Add(self.btn_test_in_game, 0, wx.ALL, 4)
        self.bridge_status = wx.StaticText(self, label="Bridge: Unknown")
        bridge_toolbar.AddStretchSpacer()
        bridge_toolbar.Add(self.bridge_status, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)

        root.Add(toolbar, 0, wx.ALL, 4)
        root.Add(bridge_toolbar, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 4)
        self.preview_label = wx.StaticText(self, label="Generated C# Output")
        root.Add(self.preview_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.preview = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        root.Add(self.preview, 1, wx.ALL | wx.EXPAND, 8)

        self.SetSizer(root)

        self.mode_choice.Bind(wx.EVT_CHOICE, self._on_mode_changed)
        self.btn_refresh_preview.Bind(wx.EVT_BUTTON, self._on_refresh_preview)
        self.btn_copy.Bind(wx.EVT_BUTTON, self._on_copy)
        self.btn_export.Bind(wx.EVT_BUTTON, self._on_export)
        self.btn_validate_contract.Bind(wx.EVT_BUTTON, self._on_validate_contract_clicked)
        self.btn_connect_bridge.Bind(wx.EVT_BUTTON, self._on_connect_bridge_clicked)
        self.btn_reconnect_bridge.Bind(wx.EVT_BUTTON, self._on_reconnect_bridge_clicked)
        self.btn_test_in_game.Bind(wx.EVT_BUTTON, self._on_test_in_game)
        self._refresh_bridge_status()
        self._refresh_mode_ui()

    def _is_markdown_mode(self) -> bool:
        # Check whether Export tab is currently showing Markdown guide output.

        return self._mode == "Markdown Guide"

    def _refresh_mode_ui(self) -> None:
        # Keep labels and action availability aligned with selected export format.

        if self._is_markdown_mode():
            self.preview_label.SetLabel("Generated Markdown Layout Guide")
            self.btn_copy.SetLabel("Copy Markdown")
            self.btn_export.SetLabel("Export Markdown")
            self.btn_test_in_game.Enable(True)
            return

        self.preview_label.SetLabel("Generated C# Output")
        self.btn_copy.SetLabel("Copy C#")
        self.btn_export.SetLabel("Export C#")
        self.btn_test_in_game.Enable(True)

    def _on_mode_changed(self, _event: wx.CommandEvent) -> None:
        # Switch export mode between runtime C# and Markdown layout guide.

        selected = self.mode_choice.GetStringSelection()
        self._mode = selected if selected in {"C#", "Markdown Guide"} else "C#"
        self._refresh_mode_ui()
        self._on_refresh_preview(_event)

    def _refresh_bridge_status(self) -> None:
        # Keep export-tab bridge state visible so interaction flow is explicit.

        state = self._get_bridge_state()
        self.bridge_status.SetLabel(f"Bridge: {state}")

    def _on_connect_bridge_clicked(self, _event: wx.CommandEvent) -> None:
        # Connect bridge directly from export workflow controls.

        self._on_connect_bridge(False)
        self._refresh_bridge_status()
        self._on_log("Export tab requested bridge connect.")

    def _on_reconnect_bridge_clicked(self, _event: wx.CommandEvent) -> None:
        # Reconnect bridge directly from export workflow controls.

        self._on_connect_bridge(True)
        self._refresh_bridge_status()
        self._on_log("Export tab requested bridge reconnect.")

    def _generate(self) -> str:
        # Request fresh export source from the shared designer document state.

        generated = self._get_markdown_guide() if self._is_markdown_mode() else self._get_export_code()
        self.preview.SetValue(generated)
        return generated

    def _on_refresh_preview(self, _event: wx.CommandEvent) -> None:
        # Rebuild the preview text from current designer state.

        try:
            self._generate()
            self._on_log("Markdown guide refreshed from Export tab." if self._is_markdown_mode() else "C# preview refreshed from Export tab.")
        except Exception as ex:
            wx.MessageBox(f"Export generation failed: {ex}", "UI Maker", wx.OK | wx.ICON_ERROR)

    def _on_copy(self, _event: wx.CommandEvent) -> None:
        # Copy generated source to clipboard for quick paste workflows.

        try:
            generated = self._generate()
        except Exception as ex:
            wx.MessageBox(f"Export generation failed: {ex}", "UI Maker", wx.OK | wx.ICON_ERROR)
            return

        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(generated))
            wx.TheClipboard.Close()
            self._on_log("Generated Markdown guide copied from Export tab." if self._is_markdown_mode() else "Generated C# copied from Export tab.")
            return

        wx.MessageBox("Could not open clipboard.", "UI Maker", wx.OK | wx.ICON_ERROR)

    def _on_export(self, _event: wx.CommandEvent) -> None:
        # Save generated source to a user-selected .cs file.

        try:
            generated = self._generate()
        except Exception as ex:
            wx.MessageBox(f"Export generation failed: {ex}", "UI Maker", wx.OK | wx.ICON_ERROR)
            return

        if self._is_markdown_mode():
            dialog = wx.FileDialog(
                self,
                "Save Generated Markdown Guide",
                wildcard="Markdown file (*.md)|*.md",
                style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            )
        else:
            dialog = wx.FileDialog(
                self,
                "Save Generated C#",
                wildcard="C# file (*.cs)|*.cs",
                style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            )
        if dialog.ShowModal() != wx.ID_OK:
            return

        output_path = Path(dialog.GetPath())
        output_path.write_text(generated, encoding="utf-8")
        self._on_log(f"Markdown guide exported to {output_path}" if self._is_markdown_mode() else f"C# exported to {output_path}")

    def _on_test_in_game(self, _event: wx.CommandEvent) -> None:
        # Send generated C# source to runtime test pipeline.

        if self._is_markdown_mode():
            self.mode_choice.SetSelection(0)
            self._mode = "C#"
            self._refresh_mode_ui()

        self._refresh_bridge_status()

        try:
            generated = self._generate()
        except Exception as ex:
            wx.MessageBox(f"Export generation failed: {ex}", "UI Maker", wx.OK | wx.ICON_ERROR)
            return

        sent = self._on_test_generated(generated)
        if sent:
            self._on_log("Requested generated window from Export tab.")
        else:
            self._on_log("Generated window request was not sent; check bridge state.")

        self._refresh_bridge_status()

    def _on_validate_contract_clicked(self, _event: wx.CommandEvent) -> None:
        # Validate canonical markdown contract against latest runtime tree payload.

        try:
            markdown_contract = self._get_markdown_guide()
            self.preview.SetValue(markdown_contract)
        except Exception as ex:
            wx.MessageBox(f"Markdown generation failed: {ex}", "UI Maker", wx.OK | wx.ICON_ERROR)
            return

        success, message = self._on_validate_contract(markdown_contract)
        self._on_log(message)
        if not success:
            wx.MessageBox(message, "Contract Validation", wx.OK | wx.ICON_ERROR)


class ComponentsPanel(wx.Panel):
    # Reuse the full Designer editor in this tab, scoped by a component selector.

    def __init__(
        self,
        parent: wx.Window,
        config: AppConfig,
        get_components: Callable[[], list[tuple[str, str]]],
        get_shared_document: Callable[[], UIDocument],
        get_component_template: Callable[[str], Optional[dict]],
        preview_component_in_game: Callable[[dict], bool],
        get_latest_bridge_preview_data: Callable[[], tuple[Optional[wx.Bitmap], Optional[dict]]],
    ):
        super().__init__(parent)
        self._config = config
        self._get_components = get_components
        self._get_shared_document = get_shared_document
        self._get_component_template = get_component_template
        self._preview_component_in_game = preview_component_in_game
        self._get_latest_bridge_preview_data = get_latest_bridge_preview_data
        self._choices: list[tuple[str, str]] = []
        self._active_component_id: Optional[str] = None
        self._is_active = False
        self._last_preview_signature = ""
        self._build_ui()

    def _build_ui(self) -> None:
        # Build component selector and embed a full Designer editor below it.

        root = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(wx.StaticText(self, label="Component"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        self.component_choice = wx.Choice(self)
        toolbar.Add(self.component_choice, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.refresh_button = wx.Button(self, label="Refresh Components")
        toolbar.Add(self.refresh_button, 0, wx.ALL, 4)
        toolbar.Add(wx.StaticText(self, label="Host W"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.host_width = wx.SpinCtrl(self, min=0, max=5000, initial=1200)
        toolbar.Add(self.host_width, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        toolbar.Add(wx.StaticText(self, label="Host H"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.host_height = wx.SpinCtrl(self, min=0, max=5000, initial=800)
        toolbar.Add(self.host_height, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        toolbar.Add(wx.StaticText(self, label="Host BG"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.host_background_mode = wx.Choice(self, choices=["Transparent", "Solid"])
        self.host_background_mode.SetSelection(0)
        toolbar.Add(self.host_background_mode, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.host_background_color = wx.ColourPickerCtrl(self, colour=wx.Colour(40, 40, 40))
        self.host_background_color.Enable(False)
        toolbar.Add(self.host_background_color, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        self.component_status = wx.StaticText(self, label="Components: 0")
        toolbar.Add(self.component_status, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        root.Add(toolbar, 0, wx.EXPAND)

        self.editor = DesignerPanel(self, self._config, on_restart_generated_test=None)
        self.editor.set_bridge_sync_enabled(False)
        self.editor.use_document(self._get_shared_document(), select_id=None)
        root.Add(self.editor, 1, wx.ALL | wx.EXPAND, 4)

        self.SetSizer(root)

        self.refresh_button.Bind(wx.EVT_BUTTON, lambda _evt: self.refresh())
        self.component_choice.Bind(wx.EVT_CHOICE, self._on_component_selected)
        self.host_width.Bind(wx.EVT_SPINCTRL, self._on_host_size_changed)
        self.host_width.Bind(wx.EVT_TEXT, self._on_host_size_changed)
        self.host_height.Bind(wx.EVT_SPINCTRL, self._on_host_size_changed)
        self.host_height.Bind(wx.EVT_TEXT, self._on_host_size_changed)
        self.host_background_mode.Bind(wx.EVT_CHOICE, self._on_host_background_mode_changed)
        self.host_background_color.Bind(wx.EVT_COLOURPICKER_CHANGED, self._on_host_size_changed)

        self._poll_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_poll_timer, self._poll_timer)
        self._poll_timer.Start(180)

    def refresh(self) -> None:
        # Reload selector entries and keep editor focused on selected component root.

        current_id: Optional[str] = None
        current_index = self.component_choice.GetSelection()
        if 0 <= current_index < len(self._choices):
            current_id = self._choices[current_index][0]

        self._choices = self._get_components()
        self.component_choice.Clear()
        for _component_id, component_name in self._choices:
            self.component_choice.Append(component_name)

        self.component_status.SetLabel(f"Components: {len(self._choices)}")
        if not self._choices:
            self._active_component_id = None
            self._last_preview_signature = ""
            self.editor.set_scope(None, hide_window_nodes=False)
            return

        restore_index = 0
        if current_id is not None:
            for index, (component_id, _component_name) in enumerate(self._choices):
                if component_id == current_id:
                    restore_index = index
                    break

        self.component_choice.SetSelection(restore_index)
        self._focus_selected_component()

    def set_active(self, active: bool) -> None:
        # Gate background preview sync to this tab's visibility so main editor state is not overridden.

        self._is_active = active
        if active:
            self.refresh()
            self._sync_active_component_preview()

    def _on_component_selected(self, _event: wx.CommandEvent) -> None:
        # Focus embedded editor to selected component instance root.

        self._focus_selected_component()

    def _focus_selected_component(self) -> None:
        # Select first instance root for chosen component in embedded full editor.

        index = self.component_choice.GetSelection()
        if index == wx.NOT_FOUND or index < 0 or index >= len(self._choices):
            return

        component_id, component_name = self._choices[index]
        if not self.editor.focus_component_for_edit(component_id):
            wx.MessageBox(f"No instance found for component: {component_name}", "UI Maker", wx.OK | wx.ICON_WARNING)
            self._active_component_id = None
            self._last_preview_signature = ""
            return

        roots = self.editor.get_document().get_component_instance_roots(component_id)
        if roots:
            self.editor.set_scope([roots[0]], hide_window_nodes=True)
        self._active_component_id = component_id
        self._last_preview_signature = ""

    def _on_host_size_changed(self, _event: wx.CommandEvent) -> None:
        # Force preview refresh when component editor host dimensions change.

        self._last_preview_signature = ""

    def _on_host_background_mode_changed(self, _event: wx.CommandEvent) -> None:
        # Toggle host background color control and force preview refresh.

        self.host_background_color.Enable(self.host_background_mode.GetStringSelection() == "Solid")
        self._last_preview_signature = ""

    @staticmethod
    def _hex_from_colour(colour: wx.Colour) -> str:
        # Convert wx color to #RRGGBB token for preview payload.

        return f"#{colour.Red():02x}{colour.Green():02x}{colour.Blue():02x}"

    @staticmethod
    def _resolve_host_axis(raw_value: int, root_mode: str, root_size: int, fallback: int, minimum: int) -> int:
        # Resolve host axis size where 0 means auto-from-component-root when root axis is manual.

        if raw_value > 0:
            return max(minimum, raw_value)
        if root_mode != "Auto":
            return max(minimum, root_size)
        return max(minimum, fallback)

    @staticmethod
    def _safe_int(value: object, fallback: int) -> int:
        # Parse integer-like values for preview sizing without raising.

        try:
            if isinstance(value, bool):
                return fallback
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(round(value))
            if isinstance(value, str):
                return int(value.strip())
        except Exception:
            return fallback
        return fallback

    def _build_component_preview_payload(self, template: dict) -> dict:
        # Wrap active component in a preview-only host container for accurate auto-size behavior.

        root_width = self._safe_int(template.get("width", 220), 220)
        root_height = self._safe_int(template.get("height", 70), 70)
        root_width_mode = str(template.get("width_mode", "Manual"))
        root_height_mode = str(template.get("height_mode", "Manual"))

        host_width = self._resolve_host_axis(int(self.host_width.GetValue()), root_width_mode, root_width, fallback=1200, minimum=200)
        host_height = self._resolve_host_axis(int(self.host_height.GetValue()), root_height_mode, root_height, fallback=800, minimum=120)
        wrapped_template = json.loads(json.dumps(template))

        background_mode = self.host_background_mode.GetStringSelection()
        background_solid = background_mode == "Solid"
        background_color = self._hex_from_colour(self.host_background_color.GetColour()) if background_solid else "#00000000"

        return {
            "id": "component_preview_host",
            "type": "Container",
            "name": "ComponentPreviewHost",
            "x": 0,
            "y": 0,
            "width": host_width,
            "height": host_height,
            "text": "",
            "text_alignment": "Left",
            "text_color_override": False,
            "text_color": "#ffffff",
            "background_color_override": background_solid,
            "background_color": background_color,
            "multiline": False,
            "border_color": "",
            "layout": "Vertical",
            "child_alignment": "UpperLeft",
            "spacing": 12,
            "padding": 12,
            "padding_left": 12,
            "padding_right": 12,
            "padding_top": 12,
            "padding_bottom": 12,
            "width_mode": "Manual",
            "height_mode": "Manual",
            "scroll_vertical": False,
            "scroll_horizontal": False,
            "props": {},
            "children": [wrapped_template],
        }

    def _sync_active_component_preview(self) -> None:
        # Re-push preview when component template or host size changes.

        if self._active_component_id is None:
            return

        template = self._get_component_template(self._active_component_id)
        if template is None:
            return

        payload = self._build_component_preview_payload(template)
        signature = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if signature == self._last_preview_signature:
            return

        if self._preview_component_in_game(payload):
            self._last_preview_signature = signature

    def _on_poll_timer(self, _event: wx.TimerEvent) -> None:
        # Mirror latest in-game preview frame/layout into the scoped component editor canvas.

        if not self._is_active:
            return

        self._sync_active_component_preview()

        bitmap, layout_payload = self._get_latest_bridge_preview_data()
        if bitmap is not None:
            self.editor.canvas.set_preview_bitmap(bitmap)
        if layout_payload is not None:
            self.editor.canvas.set_authoritative_layout(layout_payload)


class MainFrame(wx.Frame):
    # Combine orchestration and creator workflows in one responsive desktop shell.

    def __init__(self, config: AppConfig, orchestrator: SFSOrchestrator):
        super().__init__(None, title="UI Maker", size=wx.Size(1380, 900))
        self._orchestrator = orchestrator
        self._notebook: Optional[wx.Notebook] = None
        self._designer_panel: Optional[DesignerPanel] = None
        self._orchestrator_panel: Optional[OrchestratorPanel] = None
        self._export_panel: Optional[ExportPanel] = None
        self._components_panel: Optional[ComponentsPanel] = None
        self._generated_restart_in_progress = False
        self._build_ui(config)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _run_generated_test_full_cycle(self, source_code: str) -> bool:
        # Run generated-window test as one flow: auto-connect bridge then interpret generated C# in runtime.

        if source_code.strip() == "":
            if self._designer_panel is not None:
                self._designer_panel.append_designer_log("Generated C# output is empty.")
            return False

        if self._generated_restart_in_progress:
            if self._designer_panel is not None:
                self._designer_panel.append_designer_log("Generated window flow already in progress.")
            return False

        if self._designer_panel is not None:
            self._designer_panel.append_designer_log("Starting automatic generated window flow (connect, interpret, show).")

        if self._designer_panel is None:
            return False

        self._designer_panel.connect_bridge_for_export(reconnect=False)
        return self._designer_panel.run_generated_csharp_test(source_code)

    def append_orchestrator_log(self, message: str) -> None:
        # Relay orchestrator log messages to the orchestrator panel output.

        if self._orchestrator_panel is not None:
            self._orchestrator_panel.append_log(message)

    def update_orchestrator_status(self, message: str) -> None:
        # Relay orchestrator status updates to the orchestrator panel.

        if self._orchestrator_panel is not None:
            self._orchestrator_panel.update_status(message)

    def _show_creator_tab(self) -> None:
        # Switch notebook to Creator tab when another panel requests focused editing.

        if self._notebook is None or self._designer_panel is None:
            return

        index = self._notebook.FindPage(self._designer_panel)
        if index != wx.NOT_FOUND:
            self._notebook.SetSelection(index)

    def _build_ui(self, config: AppConfig) -> None:
        # Build tabbed shell with dedicated operations and design workspaces.

        self._notebook = wx.Notebook(self)
        self._orchestrator_panel = OrchestratorPanel(self._notebook, self._orchestrator)
        self._designer_panel = DesignerPanel(self._notebook, config, on_restart_generated_test=self._restart_generated_test)
        self._export_panel = ExportPanel(
            self._notebook,
            get_export_code=self._designer_panel.generate_export_code,
            get_markdown_guide=self._designer_panel.generate_markdown_guide,
            on_validate_contract=self._designer_panel.validate_markdown_contract,
            on_log=self._designer_panel.append_designer_log,
            on_test_generated=self._run_generated_test_full_cycle,
            on_connect_bridge=self._designer_panel.connect_bridge_for_export,
            get_bridge_state=self._designer_panel.get_bridge_state_text,
        )
        self._components_panel = ComponentsPanel(
            self._notebook,
            config=config,
            get_components=self._designer_panel.list_component_choices,
            get_shared_document=self._designer_panel.get_document,
            get_component_template=self._designer_panel.get_component_template,
            preview_component_in_game=self._designer_panel.preview_component_in_game,
            get_latest_bridge_preview_data=self._designer_panel.get_latest_bridge_preview_data,
        )
        self._notebook.AddPage(self._orchestrator_panel, "Build & Run")
        self._notebook.AddPage(self._designer_panel, "Creator & Visualizer")
        self._notebook.AddPage(self._export_panel, "Export")
        self._notebook.AddPage(self._components_panel, "Components")
        self._notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_tab_changed)

        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(self._notebook, 1, wx.EXPAND)
        self.SetSizer(root)

    def _on_tab_changed(self, event: wx.BookCtrlEvent) -> None:
        # Switch in-game preview behavior based on active tab.

        if self._notebook is not None and self._designer_panel is not None and self._export_panel is not None:
            selected_page = self._notebook.GetPage(event.GetSelection())
            self._designer_panel.set_export_tab_active(selected_page is self._export_panel)
            if self._components_panel is not None:
                components_active = selected_page is self._components_panel
                self._components_panel.set_active(components_active)
                if not components_active:
                    self._designer_panel.force_snapshot_sync()

        event.Skip()

    def _restart_generated_test(self, source_code: str) -> None:
        # Restart game automatically and retry generated C# test command once reconnect completes.

        if self._generated_restart_in_progress:
            if self._designer_panel is not None:
                self._designer_panel.append_designer_log("Generated test restart already in progress.")
            return

        self._generated_restart_in_progress = True
        if self._designer_panel is not None:
            self._designer_panel.append_designer_log("Restarting game for generated C# test...")

        def run_restart() -> None:
            ok = False
            try:
                self._orchestrator.shutdown_sfs()
                ok = self._orchestrator.full_startup()
            except Exception as ex:  # pragma: no cover
                if self._designer_panel is not None:
                    wx.CallAfter(self._designer_panel.append_designer_log, f"Generated test restart failed: {ex}")
                ok = False
            finally:
                wx.CallAfter(self._finish_generated_restart, ok, source_code)

        threading.Thread(target=run_restart, daemon=True).start()

    def _finish_generated_restart(self, ok: bool, source_code: str) -> None:
        # Complete restart flow and retry generated command after reconnect.

        self._generated_restart_in_progress = False
        if self._designer_panel is None:
            return

        if not ok:
            self._designer_panel.append_designer_log("Generated C# test restart did not complete successfully.")
            return

        self._designer_panel.append_designer_log("Game restart complete; retrying generated C# test.")
        self._designer_panel.queue_generated_test_after_restart(source_code)

    def _on_close(self, event: wx.CloseEvent) -> None:
        # Ensure controlled shutdown of bridge and game process ownership.

        if self._designer_panel is not None:
            self._designer_panel.shutdown()

        self.append_orchestrator_log("Closing UI Maker; shutting down attached SFS process.")
        self._orchestrator.shutdown_sfs()
        event.Skip()


def main() -> int:
    # Start app, wire orchestration callbacks, and enforce single-instance lifecycle.

    workspace_root = Path(__file__).resolve().parent
    config = AppConfig(
        workspace_root=workspace_root,
        csproj_path=workspace_root / "sfs-ui-cdn" / "sfs-ui-cdn.csproj",
    )

    instance_lock = SingleInstanceLock("ui_maker_orchestrator")
    if not instance_lock.acquire():
        wx.MessageBox("Another UI Maker instance is already running.", "UI Maker", wx.OK | wx.ICON_WARNING)
        return 1

    app = wx.App(False)
    frame_holder: dict[str, MainFrame] = {}

    def log(message: str) -> None:
        # Forward orchestration logs onto the wx UI thread safely.

        frame = frame_holder.get("frame")
        if frame is not None:
            wx.CallAfter(frame.append_orchestrator_log, message)

    def set_status(status: str) -> None:
        # Forward orchestration status onto the wx UI thread safely.

        frame = frame_holder.get("frame")
        if frame is not None:
            wx.CallAfter(frame.update_orchestrator_status, status)

    orchestrator = SFSOrchestrator(config, log=log, set_status=set_status)
    frame = MainFrame(config, orchestrator)
    frame_holder["frame"] = frame

    def cleanup() -> None:
        # Release external resources and owned process lifecycle hooks.

        orchestrator.shutdown_sfs()
        instance_lock.release()

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda *_: frame.Close())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: frame.Close())

    frame.Show()
    app.MainLoop()
    cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
