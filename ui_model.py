import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SAFE_MIN_POSITION = -100000
SAFE_MAX_POSITION = 100000
SAFE_MIN_SIZE = 1
SAFE_MAX_SIZE = 50000
SAFE_MIN_SPACING = 0
SAFE_MAX_SPACING = 10000
SAFE_MIN_PADDING = 0
SAFE_MAX_PADDING = 10000
SIZE_MODE_MANUAL = "Manual"
SIZE_MODE_AUTO = "Auto"
SIZE_MODE_CHOICES = [SIZE_MODE_MANUAL, SIZE_MODE_AUTO]


@dataclass
class UIElement:
    # Represent one editable UI node in the designer tree.

    id: str
    element_type: str
    name: str
    x: int = 30
    y: int = 30
    width: int = 220
    height: int = 70
    text: str = ""
    text_alignment: str = "Left"
    text_color_override: bool = False
    text_color: str = "#ffffff"
    background_color_override: bool = False
    background_color: str = ""
    multiline: bool = False
    border_color: str = ""
    layout: str = "Vertical"
    child_alignment: str = "UpperLeft"
    spacing: int = 12
    padding_left: int = 12
    padding_right: int = 12
    padding_top: int = 12
    padding_bottom: int = 12
    width_mode: str = SIZE_MODE_MANUAL
    height_mode: str = SIZE_MODE_MANUAL
    scroll_vertical: bool = False
    scroll_horizontal: bool = False
    props: dict[str, object] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)


def normalize_size_mode(value: object, axis: str) -> str:
    # Validate and normalize a serialized size mode token.

    normalized = str(value).strip()
    if normalized in {"NativeAuto", "UseParentSize"}:
        return SIZE_MODE_AUTO

    if normalized in SIZE_MODE_CHOICES:
        return normalized

    raise ValueError(f"Invalid {axis} size mode: {value}")


def legacy_full_flag_to_size_mode(value: object) -> str:
    # Map legacy full width/height booleans to explicit size mode values.

    return SIZE_MODE_AUTO if bool(value) else SIZE_MODE_MANUAL


def default_props_for_type(element_type: str) -> dict[str, object]:
    # Element-specific props were removed from the editor to avoid conflicting values.

    return {}


class UIDocument:
    # Store hierarchy, layer order, and editable properties for the creator.

    CONTAINER_TYPES = {"Window", "ClosableWindow", "Container", "Box"}

    def __init__(self):
        self.elements: dict[str, UIElement] = {}
        self.roots: list[str] = []
        self.file_path: Optional[Path] = None

    def add_element(self, element_type: str, parent_id: Optional[str]) -> UIElement:
        # Create and attach an element under a valid parent or the root scene.

        if parent_id is not None and parent_id not in self.elements:
            raise ValueError("Parent element does not exist.")

        if parent_id is not None and self.elements[parent_id].element_type not in self.CONTAINER_TYPES:
            raise ValueError("Only Window, Container, and Box can have children.")

        identifier = str(uuid.uuid4())
        label = f"{element_type}_{len(self.elements) + 1}"
        default_text = ""
        if element_type in {"Window", "Label", "Button", "Toggle", "TextInput"}:
            default_text = label

        element = UIElement(
            id=identifier,
            element_type=element_type,
            name=label,
            text=default_text,
            props=default_props_for_type(element_type),
        )
        self.elements[identifier] = element

        if parent_id is None:
            self.roots.append(identifier)
        else:
            self.elements[parent_id].children.append(identifier)

        return element

    def remove_element(self, element_id: str) -> None:
        # Delete an element subtree and detach it cleanly from parents.

        if element_id not in self.elements:
            raise ValueError("Element does not exist.")

        for parent in self.elements.values():
            if element_id in parent.children:
                parent.children.remove(element_id)

        if element_id in self.roots:
            self.roots.remove(element_id)

        subtree = self._collect_subtree_ids(element_id)
        for node_id in subtree:
            del self.elements[node_id]

    def move_layer(self, element_id: str, direction: int) -> bool:
        # Reorder an element among siblings to control draw/order layering.

        sibling_list = self.roots
        for element in self.elements.values():
            if element_id in element.children:
                sibling_list = element.children
                break

        index = sibling_list.index(element_id)
        new_index = index + direction
        if new_index < 0 or new_index >= len(sibling_list):
            return False

        sibling_list[index], sibling_list[new_index] = sibling_list[new_index], sibling_list[index]
        return True

    def get_parent_id(self, element_id: str) -> Optional[str]:
        # Resolve parent ID for a node; None means root-level element.

        if element_id not in self.elements:
            raise ValueError("Element does not exist.")

        for parent_id, element in self.elements.items():
            if element_id in element.children:
                return parent_id

        if element_id in self.roots:
            return None

        raise ValueError("Element is detached from document hierarchy.")

    def can_reparent(self, element_id: str, new_parent_id: Optional[str]) -> bool:
        # Validate whether a node can move to a new parent while preserving tree integrity.

        if element_id not in self.elements:
            return False

        if new_parent_id is None:
            return True

        if new_parent_id not in self.elements:
            return False

        if new_parent_id == element_id:
            return False

        if self.elements[new_parent_id].element_type not in self.CONTAINER_TYPES:
            return False

        subtree_ids = set(self._collect_subtree_ids(element_id))
        if new_parent_id in subtree_ids:
            return False

        return True

    def move_element(self, element_id: str, new_parent_id: Optional[str], insert_index: Optional[int] = None) -> bool:
        # Move an existing node under a new parent at a specific sibling index.

        if not self.can_reparent(element_id, new_parent_id):
            return False

        current_parent_id = self.get_parent_id(element_id)
        if current_parent_id is None:
            current_siblings = self.roots
        else:
            current_siblings = self.elements[current_parent_id].children

        old_index = current_siblings.index(element_id)
        current_siblings.pop(old_index)

        if new_parent_id is None:
            target_siblings = self.roots
        else:
            target_siblings = self.elements[new_parent_id].children

        if insert_index is None:
            insert_index = len(target_siblings)

        insert_index = max(0, min(insert_index, len(target_siblings)))
        if target_siblings is current_siblings and insert_index > old_index:
            insert_index -= 1

        target_siblings.insert(insert_index, element_id)
        return True

    def to_dict(self) -> dict:
        # Produce serializable snapshot for export and realtime sync.

        def serialize(node_id: str) -> dict:
            element = self.elements[node_id]
            return {
                "id": element.id,
                "type": element.element_type,
                "name": element.name,
                "x": element.x,
                "y": element.y,
                "width": element.width,
                "height": element.height,
                "text": element.text,
                "text_alignment": element.text_alignment,
                "text_color_override": element.text_color_override,
                "text_color": element.text_color,
                "background_color_override": element.background_color_override,
                "background_color": element.background_color,
                "multiline": element.multiline,
                "border_color": element.border_color,
                "layout": element.layout,
                "child_alignment": element.child_alignment,
                "spacing": element.spacing,
                "padding": element.padding_left,
                "padding_left": element.padding_left,
                "padding_right": element.padding_right,
                "padding_top": element.padding_top,
                "padding_bottom": element.padding_bottom,
                "width_mode": element.width_mode,
                "height_mode": element.height_mode,
                "scroll_vertical": element.scroll_vertical,
                "scroll_horizontal": element.scroll_horizontal,
                "props": element.props,
                "children": [serialize(child) for child in element.children],
            }

        return {
            "schemaVersion": "1.0.0",
            "roots": [serialize(node_id) for node_id in self.roots],
        }

    def from_dict(self, payload: dict) -> None:
        # Load external snapshot and rebuild a validated local tree.

        if "roots" not in payload or not isinstance(payload["roots"], list):
            raise ValueError("Invalid snapshot: missing roots array.")

        new_elements: dict[str, UIElement] = {}
        new_roots: list[str] = []

        def read_node(node: dict, parent_id: Optional[str]) -> str:
            required = {"id", "type", "name", "x", "y", "width", "height", "text", "children"}
            missing = required.difference(node.keys())
            if missing:
                raise ValueError(f"Invalid snapshot node missing fields: {sorted(missing)}")

            identifier = str(node["id"])
            if identifier in new_elements:
                raise ValueError(f"Duplicate node id in snapshot: {identifier}")

            existing = self.elements.get(identifier)
            default_text_alignment = existing.text_alignment if existing is not None else "Left"
            default_multiline = existing.multiline if existing is not None else False
            default_text_color_override = existing.text_color_override if existing is not None else False
            default_text_color = existing.text_color if existing is not None else "#ffffff"
            default_background_color_override = existing.background_color_override if existing is not None else False
            default_background_color = existing.background_color if existing is not None else ""
            default_border_color = existing.border_color if existing is not None else ""
            default_props = dict(existing.props) if existing is not None else {}
            default_padding_left = existing.padding_left if existing is not None else 12
            default_padding_right = existing.padding_right if existing is not None else 12
            default_padding_top = existing.padding_top if existing is not None else 12
            default_padding_bottom = existing.padding_bottom if existing is not None else 12
            default_width_mode = existing.width_mode if existing is not None else SIZE_MODE_MANUAL
            default_height_mode = existing.height_mode if existing is not None else SIZE_MODE_MANUAL

            original_x = int(node["x"])
            original_y = int(node["y"])
            original_width = int(node["width"])
            original_height = int(node["height"])

            clamped_x = max(SAFE_MIN_POSITION, min(SAFE_MAX_POSITION, original_x))
            clamped_y = max(SAFE_MIN_POSITION, min(SAFE_MAX_POSITION, original_y))
            clamped_width = max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, original_width))
            clamped_height = max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, original_height))

            if original_x != clamped_x:
                print(f"[Load] Clamped position X: {original_x} -> {clamped_x}")
            if original_y != clamped_y:
                print(f"[Load] Clamped position Y: {original_y} -> {clamped_y}")
            if original_width != clamped_width:
                print(f"[Load] Clamped width: {original_width} -> {clamped_width}")
            if original_height != clamped_height:
                print(f"[Load] Clamped height: {original_height} -> {clamped_height}")

            text_color_value = str(node.get("text_color", default_text_color))
            if "text_color_override" in node:
                text_color_override_value = bool(node.get("text_color_override", default_text_color_override))
            else:
                text_color_override_value = text_color_value.strip() != "" and text_color_value.strip().lower() != "#ffffff"

            background_color_value = str(node.get("background_color", default_background_color))
            if "background_color_override" in node:
                background_color_override_value = bool(node.get("background_color_override", default_background_color_override))
            else:
                legacy_style = str(node.get("background_style", "Image")).strip().lower()
                background_color_override_value = background_color_value.strip() != "" and legacy_style != "image"

            legacy_padding_value = int(node.get("padding", default_padding_left))
            padding_left_value = int(node.get("padding_left", legacy_padding_value))
            padding_right_value = int(node.get("padding_right", legacy_padding_value))
            padding_top_value = int(node.get("padding_top", legacy_padding_value))
            padding_bottom_value = int(node.get("padding_bottom", legacy_padding_value))
            if "padding_left" not in node:
                padding_left_value = default_padding_left if existing is not None else legacy_padding_value
            if "padding_right" not in node:
                padding_right_value = default_padding_right if existing is not None else legacy_padding_value
            if "padding_top" not in node:
                padding_top_value = default_padding_top if existing is not None else legacy_padding_value
            if "padding_bottom" not in node:
                padding_bottom_value = default_padding_bottom if existing is not None else legacy_padding_value
            padding_left_value = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, padding_left_value))
            padding_right_value = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, padding_right_value))
            padding_top_value = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, padding_top_value))
            padding_bottom_value = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, padding_bottom_value))

            width_mode_raw = node.get("width_mode")
            if width_mode_raw is None:
                width_mode_value = legacy_full_flag_to_size_mode(node.get("full_width", True))
            else:
                width_mode_value = normalize_size_mode(width_mode_raw, "width")

            height_mode_raw = node.get("height_mode")
            if height_mode_raw is None:
                height_mode_value = legacy_full_flag_to_size_mode(node.get("full_height", False))
            else:
                height_mode_value = normalize_size_mode(height_mode_raw, "height")

            if existing is not None:
                width_mode_value = normalize_size_mode(width_mode_value or default_width_mode, "width")
                height_mode_value = normalize_size_mode(height_mode_value or default_height_mode, "height")

            element = UIElement(
                id=identifier,
                element_type=str(node["type"]),
                name=str(node["name"]),
                x=clamped_x,
                y=clamped_y,
                width=clamped_width,
                height=clamped_height,
                text=str(node["text"]),
                text_alignment=str(node.get("text_alignment", default_text_alignment)),
                text_color_override=text_color_override_value,
                text_color=text_color_value,
                background_color_override=background_color_override_value,
                background_color=background_color_value,
                multiline=bool(node.get("multiline", default_multiline)),
                border_color=str(node.get("border_color", default_border_color)),
                layout=str(node.get("layout", "Vertical")),
                child_alignment=str(node.get("child_alignment", "UpperLeft")),
                spacing=max(SAFE_MIN_SPACING, min(SAFE_MAX_SPACING, int(node.get("spacing", 12)))),
                padding_left=padding_left_value,
                padding_right=padding_right_value,
                padding_top=padding_top_value,
                padding_bottom=padding_bottom_value,
                width_mode=width_mode_value,
                height_mode=height_mode_value,
                scroll_vertical=bool(node.get("scroll_vertical", False)),
                scroll_horizontal=bool(node.get("scroll_horizontal", False)),
                props=dict(node.get("props", default_props)) if isinstance(node.get("props", default_props), dict) else {},
            )
            new_elements[identifier] = element

            if parent_id is None:
                new_roots.append(identifier)
            else:
                new_elements[parent_id].children.append(identifier)

            for child in node["children"]:
                read_node(child, identifier)

            return identifier

        for root in payload["roots"]:
            read_node(root, None)

        self.elements = new_elements
        self.roots = new_roots

    def save_to_file(self, file_path: Path) -> None:
        # Serialize document to JSON file and track path for future saves.

        file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        with open(file_path, "w") as f:
            json.dump(payload, f, indent=2)
        self.file_path = file_path

    def load_from_file(self, file_path: Path) -> None:
        # Load document from JSON file and populate hierarchy.

        if not file_path.exists():
            raise FileNotFoundError(f"Document file not found: {file_path}")

        with open(file_path, "r") as f:
            payload = json.load(f)

        self.from_dict(payload)
        self.file_path = file_path

    def _collect_subtree_ids(self, element_id: str) -> list[str]:
        # Enumerate all descendants for recursive delete operations.

        stack = [element_id]
        ordered: list[str] = []
        while stack:
            current = stack.pop()
            ordered.append(current)
            stack.extend(self.elements[current].children)
        return ordered

    def copy_subtree(self, element_id: str) -> dict:
        # Capture an element subtree payload for paste operations.

        if element_id not in self.elements:
            raise ValueError("Element does not exist.")

        def serialize(node_id: str) -> dict:
            node = self.elements[node_id]
            return {
                "id": node.id,
                "type": node.element_type,
                "name": node.name,
                "x": node.x,
                "y": node.y,
                "width": node.width,
                "height": node.height,
                "text": node.text,
                "text_alignment": node.text_alignment,
                "text_color_override": node.text_color_override,
                "text_color": node.text_color,
                "background_color_override": node.background_color_override,
                "background_color": node.background_color,
                "multiline": node.multiline,
                "border_color": node.border_color,
                "layout": node.layout,
                "child_alignment": node.child_alignment,
                "spacing": node.spacing,
                "padding": node.padding_left,
                "padding_left": node.padding_left,
                "padding_right": node.padding_right,
                "padding_top": node.padding_top,
                "padding_bottom": node.padding_bottom,
                "width_mode": node.width_mode,
                "height_mode": node.height_mode,
                "scroll_vertical": node.scroll_vertical,
                "scroll_horizontal": node.scroll_horizontal,
                "props": dict(node.props),
                "children": [serialize(child) for child in node.children],
            }

        return serialize(element_id)

    def paste_subtree_after(self, target_id: Optional[str], subtree_payload: dict) -> str:
        # Paste a copied subtree as sibling-after target, or root if target is None.

        if target_id is not None and target_id not in self.elements:
            raise ValueError("Paste target does not exist.")

        def clone(node: dict) -> str:
            element_type = str(node.get("type", ""))
            if element_type == "":
                raise ValueError("Invalid copied subtree: missing type.")

            new_id = str(uuid.uuid4())
            cloned_text_color = str(node.get("text_color", "#ffffff"))
            if "text_color_override" in node:
                cloned_text_color_override = bool(node.get("text_color_override", False))
            else:
                cloned_text_color_override = cloned_text_color.strip() != "" and cloned_text_color.strip().lower() != "#ffffff"

            cloned_background_color = str(node.get("background_color", ""))
            if "background_color_override" in node:
                cloned_background_color_override = bool(node.get("background_color_override", False))
            else:
                legacy_style = str(node.get("background_style", "Image")).strip().lower()
                cloned_background_color_override = cloned_background_color.strip() != "" and legacy_style != "image"

            cloned_legacy_padding = int(node.get("padding", 12))
            cloned_padding_left = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(node.get("padding_left", cloned_legacy_padding))))
            cloned_padding_right = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(node.get("padding_right", cloned_legacy_padding))))
            cloned_padding_top = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(node.get("padding_top", cloned_legacy_padding))))
            cloned_padding_bottom = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(node.get("padding_bottom", cloned_legacy_padding))))
            cloned_width_mode = normalize_size_mode(
                node.get("width_mode", legacy_full_flag_to_size_mode(node.get("full_width", True))),
                "width",
            )
            cloned_height_mode = normalize_size_mode(
                node.get("height_mode", legacy_full_flag_to_size_mode(node.get("full_height", False))),
                "height",
            )

            element = UIElement(
                id=new_id,
                element_type=element_type,
                name=str(node.get("name", element_type)),
                x=int(node.get("x", 0)),
                y=int(node.get("y", 0)),
                width=max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, int(node.get("width", 220)))),
                height=max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, int(node.get("height", 70)))),
                text=str(node.get("text", "")),
                text_alignment=str(node.get("text_alignment", "Left")),
                text_color_override=cloned_text_color_override,
                text_color=cloned_text_color,
                background_color_override=cloned_background_color_override,
                background_color=cloned_background_color,
                multiline=bool(node.get("multiline", False)),
                border_color=str(node.get("border_color", "")),
                layout=str(node.get("layout", "Vertical")),
                child_alignment=str(node.get("child_alignment", "UpperLeft")),
                spacing=max(SAFE_MIN_SPACING, min(SAFE_MAX_SPACING, int(node.get("spacing", 12)))),
                padding_left=cloned_padding_left,
                padding_right=cloned_padding_right,
                padding_top=cloned_padding_top,
                padding_bottom=cloned_padding_bottom,
                width_mode=cloned_width_mode,
                height_mode=cloned_height_mode,
                scroll_vertical=bool(node.get("scroll_vertical", False)),
                scroll_horizontal=bool(node.get("scroll_horizontal", False)),
                props=dict(node.get("props", {})) if isinstance(node.get("props", {}), dict) else {},
            )
            self.elements[new_id] = element

            for child in node.get("children", []):
                child_id = clone(child)
                element.children.append(child_id)

            return new_id

        new_root_id = clone(subtree_payload)
        parent_id = self.get_parent_id(target_id) if target_id is not None else None

        if parent_id is None:
            siblings = self.roots
            insert_index = len(siblings)
            if target_id is not None:
                insert_index = siblings.index(target_id) + 1
            siblings.insert(insert_index, new_root_id)
            return new_root_id

        siblings = self.elements[parent_id].children
        target_index = siblings.index(target_id) if target_id is not None else len(siblings) - 1
        siblings.insert(target_index + 1, new_root_id)
        return new_root_id
