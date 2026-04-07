from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "csharp"
UI_NODE_RUNTIME_TEMPLATE = TEMPLATES_DIR / "ui_node_runtime.cs.tpl"

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

TEXT_ANCHOR_TOKEN_BY_NAME = {
    "Left": "TextAnchor.MiddleLeft",
    "Center": "TextAnchor.MiddleCenter",
    "Right": "TextAnchor.MiddleRight",
    "TopLeft": "TextAnchor.UpperLeft",
    "Top": "TextAnchor.UpperCenter",
    "TopRight": "TextAnchor.UpperRight",
    "BottomLeft": "TextAnchor.LowerLeft",
    "Bottom": "TextAnchor.LowerCenter",
    "BottomRight": "TextAnchor.LowerRight",
    "UpperLeft": "TextAnchor.UpperLeft",
    "UpperCenter": "TextAnchor.UpperCenter",
    "UpperRight": "TextAnchor.UpperRight",
    "MiddleLeft": "TextAnchor.MiddleLeft",
    "MiddleCenter": "TextAnchor.MiddleCenter",
    "MiddleRight": "TextAnchor.MiddleRight",
    "LowerLeft": "TextAnchor.LowerLeft",
    "LowerCenter": "TextAnchor.LowerCenter",
    "LowerRight": "TextAnchor.LowerRight",
}

LAYOUT_TOKEN_BY_NAME = {
    "Vertical": "SFS.UI.ModGUI.Type.Vertical",
    "Horizontal": "SFS.UI.ModGUI.Type.Horizontal",
}


def generate_csharp_export(snapshot: dict, source_file: Optional[Path]) -> str:
    # Generate compact, executable C# that directly builds ModGUI/UITools elements.

    _ = source_file
    normalized_snapshot = _normalize_snapshot(snapshot)
    node_count = _count_nodes(normalized_snapshot.get("roots", []))

    component_definitions = _normalize_component_definitions(snapshot.get("components"))
    component_instance_by_root_id = _extract_component_instances(snapshot.get("component_instances"), component_definitions)

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
    component_method_names = _build_component_method_names(component_definitions)
    for root in normalized_snapshot["roots"]:
        lines.extend(
            _build_node_initializer_lines(
                root,
                export_id_map,
                indent=16,
                trailing_comma=True,
                sibling_count=root_sibling_count,
                component_instance_by_root_id=component_instance_by_root_id,
                component_method_names=component_method_names,
            )
        )
    lines.append("            };")
    lines.append("        }")
    lines.append("")
    component_lines = _emit_component_method_lines(component_definitions, component_method_names)
    if component_lines:
        lines.extend(component_lines)
        lines.append("")
    lines.extend(_emit_ui_node_class_lines())
    lines.append("    }")
    lines.append("}")

    return "\n".join(lines).rstrip() + "\n"


def _emit_ui_node_class_lines() -> list[str]:
    # Load the full UiNode runtime renderer from a reusable C# template file.

    if not UI_NODE_RUNTIME_TEMPLATE.exists():
        raise FileNotFoundError(f"Missing C# runtime template: {UI_NODE_RUNTIME_TEMPLATE}")

    content = UI_NODE_RUNTIME_TEMPLATE.read_text(encoding="utf-8")
    return content.splitlines()


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
    component_instance_by_root_id: Optional[dict[str, str]] = None,
    component_method_names: Optional[dict[str, str]] = None,
) -> list[str]:
    # Emit one fluent monadic node expression with chainable configuration.

    node_id = str(node.get("id", ""))
    export_id = _escape_csharp_string(export_id_map[node_id])

    if component_instance_by_root_id is not None and component_method_names is not None:
        component_id = component_instance_by_root_id.get(node_id)
        if component_id is not None:
            method_name = component_method_names.get(component_id)
            if method_name is not None:
                suffix = "," if trailing_comma else ""
                return [f'{" " * indent}{method_name}("{export_id}"){suffix}']

    lines: list[str] = []
    pad = " " * indent
    child_pad = " " * (indent + 4)

    node_type_enum = _to_node_type_enum(str(node.get("type", "Container")))
    name_text = _escape_csharp_string(_normalize_export_string(str(node.get("name", "Node"))))
    raw_text = _normalize_export_string(str(node.get("text", "")))
    text_text = _escape_csharp_string(raw_text)
    text_alignment = _text_anchor_token(str(node.get("text_alignment", "Left")))
    text_color = _escape_csharp_string(str(node.get("text_color", "#ffffff")))
    background_color = _escape_csharp_string(str(node.get("background_color", "")))
    border_color = _escape_csharp_string(str(node.get("border_color", "")))
    layout = _layout_token(str(node.get("layout", "Vertical")))
    child_alignment = _text_anchor_token(str(node.get("child_alignment", "UpperLeft")))
    props_value = node.get("props", {}) if isinstance(node.get("props", {}), dict) else {}
    props_json = _escape_csharp_string(json.dumps(props_value, separators=(",", ":"), ensure_ascii=True))
    label_direction_raw = props_value.get("label_direction", props_value.get("labelDirection", "Top"))
    toggle_default_direction = "Left" if node_type_enum == "ToggleWithLabel" else "Top"
    label_direction = _normalize_label_direction(label_direction_raw, toggle_default_direction)
    label_text_raw = _normalize_export_string(str(props_value.get("label_text", raw_text)))
    control_text_raw = _normalize_export_string(str(props_value.get("control_text", raw_text)))

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
    width_mode = _size_mode_token(_normalize_size_mode(node.get("width_mode", _legacy_full_flag_to_mode(node.get("full_width", True))), "width_mode"))
    height_mode = _size_mode_token(_normalize_size_mode(node.get("height_mode", _legacy_full_flag_to_mode(node.get("full_height", False))), "height_mode"))
    scroll_vertical = _bool_token(_as_bool(node.get("scroll_vertical", False), "scroll_vertical"))
    scroll_horizontal = _bool_token(_as_bool(node.get("scroll_horizontal", False), "scroll_horizontal"))

    lines.append(f'{pad}Node("{export_id}", UiNodeType.{node_type_enum}, "{name_text}", {width}, {height})')

    if x != 0 or y != 0:
        lines.append(f"{child_pad}.At({x}, {y})")
    if text_text != "":
        lines.append(f'{child_pad}.WithText("{text_text}")')

    has_visual_change = (
        text_alignment != "TextAnchor.MiddleLeft"
        or multiline != "false"
        or text_color_override != "false"
        or text_color != "#ffffff"
        or background_color_override != "false"
        or background_color != ""
        or border_color != ""
    )
    if has_visual_change:
        lines.append(
            f'{child_pad}.Visual({text_alignment}, {multiline}, {text_color_override}, "{text_color}", {background_color_override}, "{background_color}", "{border_color}")'
        )

    has_layout_change = (
        layout != "SFS.UI.ModGUI.Type.Vertical"
        or child_alignment != "TextAnchor.UpperLeft"
        or spacing != 12
        or padding_left != 12
        or padding_right != 12
        or padding_top != 12
        or padding_bottom != 12
    )
    if has_layout_change:
        lines.append(
            f"{child_pad}.LayoutConfig({layout}, {child_alignment}, {spacing}, {padding_left}, {padding_right}, {padding_top}, {padding_bottom})"
        )

    if width_mode != "UiSizeMode.Manual" or height_mode != "UiSizeMode.Manual" or sibling_count > 1:
        lines.append(f"{child_pad}.Sizing({width_mode}, {height_mode}, {sibling_count})")

    if scroll_vertical != "false" or scroll_horizontal != "false":
        lines.append(f"{child_pad}.Scroll({scroll_vertical}, {scroll_horizontal})")
    if node_type_enum in {"ButtonWithLabel", "InputWithLabel", "ToggleWithLabel"}:
        direction_literal = _escape_csharp_string(label_direction)
        lines.append(f'{child_pad}.LabelPlacement("{direction_literal}")')
        label_literal = _escape_csharp_string(label_text_raw)
        control_literal = _escape_csharp_string(control_text_raw)
        lines.append(f'{child_pad}.LabeledTexts("{label_literal}", "{control_literal}")')
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
            child_lines = _build_node_initializer_lines(
                child,
                export_id_map,
                indent + 8,
                trailing_comma=False,
                sibling_count=child_sibling_count,
                component_instance_by_root_id=component_instance_by_root_id,
                component_method_names=component_method_names,
            )
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
        "ButtonWithLabel",
        "TextInput",
        "InputWithLabel",
        "Toggle",
        "ToggleWithLabel",
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


def _text_anchor_token(value: str) -> str:
    # Map alignment strings to generated TextAnchor enum tokens.

    normalized = value.strip()
    token = TEXT_ANCHOR_TOKEN_BY_NAME.get(normalized)
    if token is None:
        return "TextAnchor.MiddleLeft"
    return token


def _layout_token(value: str) -> str:
    # Map layout strings to generated layout enum tokens.

    normalized = value.strip()
    token = LAYOUT_TOKEN_BY_NAME.get(normalized)
    if token is None:
        return "SFS.UI.ModGUI.Type.Vertical"
    return token


def _size_mode_token(value: str) -> str:
    # Convert normalized size mode names into generated UiSizeMode tokens.

    if value == SIZE_MODE_AUTO:
        return "UiSizeMode.Auto"
    return "UiSizeMode.Manual"


def _normalize_export_string(value: str) -> str:
    # Unwrap accidental JSON-encoded string literals so export text stays human-readable.

    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, str):
                return parsed
        except json.JSONDecodeError:
            return value
    return value


def _normalize_label_direction(value: Any, fallback: str) -> str:
    # Canonicalize label direction tokens so generated C# always emits valid placement values.

    raw = _normalize_export_string(str(value)).strip().lower()
    if raw in {"top", "bottom", "left", "right"}:
        return raw.capitalize()
    return fallback


def _normalize_component_definitions(value: Any) -> dict[str, dict[str, Any]]:
    # Normalize component definitions from project payload for export method generation.

    definitions: dict[str, dict[str, Any]] = {}
    if not isinstance(value, list):
        return definitions

    for item in value:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("id", "")).strip()
        component_name = str(item.get("name", "")).strip()
        template = item.get("template")
        if component_id == "" or component_name == "" or not isinstance(template, dict):
            continue
        prepared_template = _template_with_generated_ids(template, component_id, "0")
        definitions[component_id] = {
            "id": component_id,
            "name": component_name,
            "template": _normalize_node(prepared_template, depth=0),
        }

    return definitions


def _extract_component_instances(value: Any, definitions: dict[str, dict[str, Any]]) -> dict[str, str]:
    # Build root-id to component-id lookup for top-level method call emission.

    instances: dict[str, str] = {}
    if not isinstance(value, list):
        return instances

    for item in value:
        if not isinstance(item, dict):
            continue
        root_id = str(item.get("root_id", "")).strip()
        component_id = str(item.get("component_id", "")).strip()
        if root_id == "" or component_id == "" or component_id not in definitions:
            continue
        instances[root_id] = component_id

    return instances


def _template_with_generated_ids(node: dict[str, Any], component_id: str, path: str) -> dict[str, Any]:
    # Ensure component template nodes have stable IDs required by snapshot normalization.

    prepared = dict(node)
    node_id = str(prepared.get("id", "")).strip()
    if node_id == "":
        prepared["id"] = f"component_{component_id}_{path}"

    children_value = prepared.get("children", [])
    children_out: list[dict[str, Any]] = []
    if isinstance(children_value, list):
        for index, child in enumerate(children_value):
            if not isinstance(child, dict):
                continue
            child_path = f"{path}_{index}"
            children_out.append(_template_with_generated_ids(child, component_id, child_path))
    prepared["children"] = children_out
    return prepared


def _build_component_method_names(definitions: dict[str, dict[str, Any]]) -> dict[str, str]:
    # Produce deterministic unique C# method names for component functions.

    names: dict[str, str] = {}
    used: set[str] = set()
    for component_id in sorted(definitions):
        item = definitions[component_id]
        raw_name = _sanitize_csharp_identifier(str(item["name"]))
        if raw_name == "":
            raw_name = "Component"
        method_name = f"Create{raw_name}"
        suffix = 2
        while method_name in used:
            method_name = f"Create{raw_name}{suffix}"
            suffix += 1
        used.add(method_name)
        names[component_id] = method_name
    return names


def _sanitize_csharp_identifier(value: str) -> str:
    # Keep generated method names valid and stable.

    chars: list[str] = []
    for ch in value:
        if ch.isalnum() or ch == "_":
            chars.append(ch)
    if not chars:
        return ""
    identifier = "".join(chars)
    if identifier[0].isdigit():
        return f"Component{identifier}"
    return identifier


def _emit_component_method_lines(definitions: dict[str, dict[str, Any]], method_names: dict[str, str]) -> list[str]:
    # Emit reusable component builder functions referenced by Define().

    lines: list[str] = []
    for component_id in sorted(definitions):
        method_name = method_names.get(component_id)
        if method_name is None:
            continue

        root = definitions[component_id]["template"]
        lines.append(f"        private static UiNode {method_name}(string id)")
        lines.append("        {")
        lines.append("            // Build component instance subtree.")
        component_expression = _build_component_node_initializer_lines(root, indent=12, trailing_comma=False, sibling_count=1, node_path="0")
        if component_expression:
            component_expression[0] = component_expression[0].replace("Node(", "return Node(", 1)
            component_expression[-1] = component_expression[-1] + ";"
            lines.extend(component_expression)
        else:
            lines.append("            throw new InvalidOperationException(\"Component template is empty.\");")
        lines.append("        }")
        lines.append("")

    return lines


def _build_component_node_initializer_lines(
    node: dict[str, Any],
    indent: int,
    trailing_comma: bool,
    sibling_count: int,
    node_path: str,
) -> list[str]:
    # Emit node fluent expression for reusable component methods with per-instance deterministic IDs.

    lines: list[str] = []
    pad = " " * indent
    child_pad = " " * (indent + 4)

    node_type_enum = _to_node_type_enum(str(node.get("type", "Container")))
    name_text = _escape_csharp_string(_normalize_export_string(str(node.get("name", "Node"))))
    text_text = _escape_csharp_string(_normalize_export_string(str(node.get("text", ""))))
    text_alignment = _text_anchor_token(str(node.get("text_alignment", "Left")))
    text_color = _escape_csharp_string(str(node.get("text_color", "#ffffff")))
    background_color = _escape_csharp_string(str(node.get("background_color", "")))
    border_color = _escape_csharp_string(str(node.get("border_color", "")))
    layout = _layout_token(str(node.get("layout", "Vertical")))
    child_alignment = _text_anchor_token(str(node.get("child_alignment", "UpperLeft")))
    props_value = node.get("props", {}) if isinstance(node.get("props", {}), dict) else {}
    props_json = _escape_csharp_string(json.dumps(props_value, separators=(",", ":"), ensure_ascii=True))
    raw_text = _normalize_export_string(str(node.get("text", "")))
    label_direction_raw = props_value.get("label_direction", props_value.get("labelDirection", "Top"))
    toggle_default_direction = "Left" if node_type_enum == "ToggleWithLabel" else "Top"
    label_direction = _normalize_label_direction(label_direction_raw, toggle_default_direction)
    label_text_raw = _normalize_export_string(str(props_value.get("label_text", raw_text)))
    control_text_raw = _normalize_export_string(str(props_value.get("control_text", raw_text)))

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
    width_mode = _size_mode_token(_normalize_size_mode(node.get("width_mode", _legacy_full_flag_to_mode(node.get("full_width", True))), "width_mode"))
    height_mode = _size_mode_token(_normalize_size_mode(node.get("height_mode", _legacy_full_flag_to_mode(node.get("full_height", False))), "height_mode"))
    scroll_vertical = _bool_token(_as_bool(node.get("scroll_vertical", False), "scroll_vertical"))
    scroll_horizontal = _bool_token(_as_bool(node.get("scroll_horizontal", False), "scroll_horizontal"))

    lines.append(f'{pad}Node($"{{id}}/{node_path}", UiNodeType.{node_type_enum}, "{name_text}", {width}, {height})')
    if x != 0 or y != 0:
        lines.append(f"{child_pad}.At({x}, {y})")
    if text_text != "":
        lines.append(f'{child_pad}.WithText("{text_text}")')

    has_visual_change = (
        text_alignment != "TextAnchor.MiddleLeft"
        or multiline != "false"
        or text_color_override != "false"
        or text_color != "#ffffff"
        or background_color_override != "false"
        or background_color != ""
        or border_color != ""
    )
    if has_visual_change:
        lines.append(f'{child_pad}.Visual({text_alignment}, {multiline}, {text_color_override}, "{text_color}", {background_color_override}, "{background_color}", "{border_color}")')

    has_layout_change = (
        layout != "SFS.UI.ModGUI.Type.Vertical"
        or child_alignment != "TextAnchor.UpperLeft"
        or spacing != 12
        or padding_left != 12
        or padding_right != 12
        or padding_top != 12
        or padding_bottom != 12
    )
    if has_layout_change:
        lines.append(f"{child_pad}.LayoutConfig({layout}, {child_alignment}, {spacing}, {padding_left}, {padding_right}, {padding_top}, {padding_bottom})")

    if width_mode != "UiSizeMode.Manual" or height_mode != "UiSizeMode.Manual" or sibling_count > 1:
        lines.append(f"{child_pad}.Sizing({width_mode}, {height_mode}, {sibling_count})")

    if scroll_vertical != "false" or scroll_horizontal != "false":
        lines.append(f"{child_pad}.Scroll({scroll_vertical}, {scroll_horizontal})")
    if node_type_enum in {"ButtonWithLabel", "InputWithLabel", "ToggleWithLabel"}:
        direction_literal = _escape_csharp_string(label_direction)
        lines.append(f'{child_pad}.LabelPlacement("{direction_literal}")')
        label_literal = _escape_csharp_string(label_text_raw)
        control_literal = _escape_csharp_string(control_text_raw)
        lines.append(f'{child_pad}.LabeledTexts("{label_literal}", "{control_literal}")')
    if props_json != "{}":
        lines.append(f'{child_pad}.Props("{props_json}")')

    children = node.get("children", [])
    if isinstance(children, list) and len(children) > 0:
        child_sibling_count = len(children)
        lines.append(f"{child_pad}.AddChildren(")
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            child_path = f"{node_path}/{index}"
            suffix = "," if index < len(children) - 1 else ""
            child_lines = _build_component_node_initializer_lines(
                child,
                indent + 8,
                trailing_comma=False,
                sibling_count=child_sibling_count,
                node_path=child_path,
            )
            if suffix and child_lines:
                child_lines[-1] = child_lines[-1] + suffix
            lines.extend(child_lines)
        lines.append(f"{child_pad})")

    if trailing_comma and lines:
        lines[-1] = lines[-1] + ","

    return lines
