from __future__ import annotations

from pathlib import Path
from typing import Optional


def generate_markdown_layout_guide(snapshot: dict, source_file: Optional[Path]) -> str:
    # Export a readable hierarchy-focused markdown guide for manual implementation.

    roots = snapshot.get("roots")
    if not isinstance(roots, list):
        raise ValueError("Snapshot is missing a roots list.")

    schema_version = str(snapshot.get("schemaVersion", "1.0.0"))
    source_label = str(source_file) if source_file is not None else "unsaved-document"
    alias_map = _build_alias_map(roots)

    lines: list[str] = []
    lines.append("# UI Layout Guide")
    lines.append("")
    lines.append(f"schema: {schema_version}")
    lines.append(f"source: {_escape_inline(source_label)}")
    lines.append("")

    lines.append("## Hierarchy Tree")
    lines.append("")
    tree_lines = _build_tree_lines(roots, alias_map)
    if tree_lines:
        lines.append("```text")
        lines.extend(tree_lines)
        lines.append("```")
    else:
        lines.append("_No elements._")
    lines.append("")

    lines.append("## Elements")
    lines.append("")
    for root in roots:
        _emit_node(lines, root, alias_map, depth=0)

    lines.append("## ID Map")
    lines.append("")
    alias_pairs = sorted(alias_map.items(), key=lambda pair: pair[1])
    for full_id, short_id in alias_pairs:
        lines.append(f"- {short_id} -> {full_id}")

    return "\n".join(lines).rstrip() + "\n"


def _emit_node(lines: list[str], node: dict, alias_map: dict[str, str], depth: int) -> None:
    # Emit one node in compact readable format and recurse into children.

    required = {"id", "type", "name", "x", "y", "width", "height", "children"}
    missing = required.difference(node.keys())
    if missing:
        raise ValueError(f"Snapshot node missing fields: {sorted(missing)}")

    node_id = str(node["id"])
    short_id = alias_map[node_id]
    node_name = str(node["name"])
    node_type = str(node["type"])

    x = int(node["x"])
    y = int(node["y"])
    width = int(node["width"])
    height = int(node["height"])

    layout = str(node.get("layout", "Vertical"))
    align = str(node.get("child_alignment", "UpperLeft"))
    spacing = int(node.get("spacing", 12))

    legacy_padding = int(node.get("padding", 12))
    padding_left = int(node.get("padding_left", legacy_padding))
    padding_right = int(node.get("padding_right", legacy_padding))
    padding_top = int(node.get("padding_top", legacy_padding))
    padding_bottom = int(node.get("padding_bottom", legacy_padding))

    text = str(node.get("text", ""))
    text_alignment = str(node.get("text_alignment", "Left"))
    text_color = str(node.get("text_color", "#ffffff"))
    text_color_override = bool(node.get("text_color_override", False))

    background_color = str(node.get("background_color", ""))
    background_color_override = bool(node.get("background_color_override", False))

    width_mode = str(node.get("width_mode", "Manual"))
    height_mode = str(node.get("height_mode", "Manual"))
    scroll_vertical = bool(node.get("scroll_vertical", False))
    scroll_horizontal = bool(node.get("scroll_horizontal", False))

    indent = "  " * depth
    child_indent = indent + "  "

    lines.append(f"{indent}- ({_escape_inline(short_id)}) {_escape_inline(node_name)} : {_escape_inline(node_type)}")

    layout_tokens = [
        f"pos({x},{y})",
        f"size({width},{height})",
    ]
    if node_type in {"Window", "Container", "Box"}:
        layout_tokens.extend(
            [
                f"layout={_escape_inline(layout)}",
                f"align={_escape_inline(align)}",
                f"spacing={spacing}",
                f"padding({padding_left},{padding_right},{padding_top},{padding_bottom})",
            ]
        )

    lines.append(f"{child_indent}layout: {' '.join(layout_tokens)}")

    prop_tokens: list[str] = []
    if text != "":
        prop_tokens.append(f"text=\"{_escape_quoted(text)}\"")

    if text_alignment.lower() != "left":
        prop_tokens.append(f"textAlign={_escape_inline(text_alignment)}")

    if text_color_override and text_color != "":
        prop_tokens.append(f"fg={_escape_inline(text_color)}")
    elif text_color_override:
        prop_tokens.append("textOverride=true")

    if background_color_override and background_color != "":
        prop_tokens.append(f"bg={_escape_inline(background_color)}")
    elif background_color_override:
        prop_tokens.append("bgOverride=true")

    if width_mode != "Manual":
        prop_tokens.append(f"widthMode={_escape_inline(width_mode)}")
    if height_mode != "Manual":
        prop_tokens.append(f"heightMode={_escape_inline(height_mode)}")
    if scroll_vertical or scroll_horizontal:
        prop_tokens.append(f"scroll(v={_bool_token(scroll_vertical)},h={_bool_token(scroll_horizontal)})")

    if not prop_tokens:
        prop_tokens.append("(none)")

    lines.append(f"{child_indent}props : {' '.join(prop_tokens)}")

    children = node.get("children", [])
    if not isinstance(children, list):
        raise ValueError(f"Node {node_id} children must be a list.")

    if children:
        lines.append("")
        for child in children:
            if not isinstance(child, dict):
                raise ValueError(f"Node {node_id} contains non-object child entry.")
            _emit_node(lines, child, alias_map, depth + 1)
        lines.append("")


def _build_tree_lines(roots: list[dict], alias_map: dict[str, str]) -> list[str]:
    # Build ASCII tree lines to visualize hierarchy quickly.

    lines: list[str] = []

    def walk(node: dict, prefix: str, is_last: bool) -> None:
        node_id = str(node.get("id", ""))
        short_id = alias_map.get(node_id, "el")
        name = _escape_inline(str(node.get("name", "Node")))
        node_type = _escape_inline(str(node.get("type", "Container")))

        connector = "└─ " if is_last else "├─ "
        lines.append(f"{prefix}{connector}{short_id} {name} [{node_type}]")

        children = node.get("children", [])
        if not isinstance(children, list) or not children:
            return

        next_prefix = f"{prefix}{'   ' if is_last else '│  '}"
        for index, child in enumerate(children):
            if isinstance(child, dict):
                walk(child, next_prefix, index == len(children) - 1)

    for index, root in enumerate(roots):
        if isinstance(root, dict):
            walk(root, "", index == len(roots) - 1)

    return lines


def _build_alias_map(roots: list[dict]) -> dict[str, str]:
    # Build short stable IDs per element type so guide stays readable.

    prefix_by_type = {
        "Window": "w",
        "Container": "c",
        "Box": "box",
        "Label": "lbl",
        "Button": "btn",
        "TextInput": "txt",
        "Toggle": "tog",
        "Slider": "sld",
        "Separator": "sep",
        "Space": "sp",
    }

    counters: dict[str, int] = {}
    alias_map: dict[str, str] = {}

    def walk(node: dict) -> None:
        node_id = str(node.get("id", ""))
        node_type = str(node.get("type", "Container"))
        prefix = prefix_by_type.get(node_type, "el")
        counters[prefix] = counters.get(prefix, 0) + 1
        alias_map[node_id] = f"{prefix}_{counters[prefix]}"

        children = node.get("children", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    walk(child)

    for root in roots:
        if isinstance(root, dict):
            walk(root)

    return alias_map


def _escape_inline(value: str) -> str:
    # Keep inline tokens readable without parser-breaking spacing.

    compact = value.replace("\n", " ").replace("\r", " ").strip()
    return compact if compact != "" else "_"


def _escape_quoted(value: str) -> str:
    # Escape text payload for quoted property values.

    return value.replace("\\", "\\\\").replace('"', '\\"')


def _bool_token(value: bool) -> str:
    # Emit lowercase booleans for consistency.

    return "true" if value else "false"
