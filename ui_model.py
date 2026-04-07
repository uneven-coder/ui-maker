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


@dataclass
class UIComponentDefinition:
    # Hold one reusable component template as a serialized subtree payload.

    id: str
    name: str
    template: dict


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
    # Provide element-specific defaults needed for composite/labeled controls.

    if element_type in {"ButtonWithLabel", "InputWithLabel"}:
        return {"label_direction": "Top"}
    if element_type == "ToggleWithLabel":
        return {"label_direction": "Left"}
    return {}


class UIDocument:
    # Store hierarchy, layer order, and editable properties for the creator.

    CONTAINER_TYPES = {"Window", "ClosableWindow", "Container", "Box"}

    def __init__(self):
        self.elements: dict[str, UIElement] = {}
        self.roots: list[str] = []
        self.components: dict[str, UIComponentDefinition] = {}
        self.component_instances: dict[str, str] = {}
        self.file_path: Optional[Path] = None

    @staticmethod
    def _component_prop_key(name: str) -> str:
        # Namespace internal component metadata keys to avoid collisions with user props.

        return f"__component_{name}"

    def _set_component_metadata(self, node: UIElement, component_id: str, instance_root_id: str, node_path: str) -> None:
        # Stamp linkage metadata used for cross-instance synchronization.

        node.props[self._component_prop_key("id")] = component_id
        node.props[self._component_prop_key("instance_root")] = instance_root_id
        node.props[self._component_prop_key("path")] = node_path

    def _clear_component_metadata(self, node: UIElement) -> None:
        # Remove linkage metadata from one node.

        for key in (
            self._component_prop_key("id"),
            self._component_prop_key("instance_root"),
            self._component_prop_key("path"),
        ):
            node.props.pop(key, None)

    def _extract_component_metadata(self, node: UIElement) -> tuple[str, str, str] | None:
        # Read component linkage from a node when present.

        component_id = node.props.get(self._component_prop_key("id"))
        instance_root = node.props.get(self._component_prop_key("instance_root"))
        node_path = node.props.get(self._component_prop_key("path"))
        if not isinstance(component_id, str) or not isinstance(instance_root, str) or not isinstance(node_path, str):
            return None
        if component_id == "" or instance_root == "":
            return None
        return component_id, instance_root, node_path

    @staticmethod
    def _node_with_payload(node: UIElement, children_payload: list[dict]) -> dict:
        # Materialize one node payload matching the project JSON schema.

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
            "children": children_payload,
        }

    def _serialize_node_tree(self, node_id: str) -> dict:
        # Serialize one subtree into nested payload form.

        node = self.elements[node_id]
        children_payload = [self._serialize_node_tree(child_id) for child_id in node.children]
        return self._node_with_payload(node, children_payload)

    @staticmethod
    def _strip_component_ids(payload: dict) -> dict:
        # Remove runtime-only IDs so component templates can be instantiated with fresh identifiers.

        copied = dict(payload)
        copied.pop("id", None)
        props_value = copied.get("props", {})
        props_out: dict[str, object] = {}
        if isinstance(props_value, dict):
            for key, value in props_value.items():
                if isinstance(key, str) and key.startswith("__component_"):
                    continue
                props_out[str(key)] = value
        copied["props"] = props_out
        children_value = copied.get("children", [])
        children_out: list[dict] = []
        if isinstance(children_value, list):
            for child in children_value:
                if isinstance(child, dict):
                    children_out.append(UIDocument._strip_component_ids(child))
        copied["children"] = children_out
        return copied

    def _clone_payload_into_document(
        self,
        payload: dict,
        component_id: Optional[str],
        instance_root_id: Optional[str],
        path: str,
    ) -> str:
        # Clone one serialized payload into document nodes and return new root ID.

        element_type = str(payload.get("type", ""))
        if element_type == "":
            raise ValueError("Component template node is missing type.")

        new_id = str(uuid.uuid4())
        text_color = str(payload.get("text_color", "#ffffff"))
        text_color_override = bool(payload.get("text_color_override", False))
        background_color = str(payload.get("background_color", ""))
        background_color_override = bool(payload.get("background_color_override", False))
        legacy_padding = int(payload.get("padding", 12))

        element = UIElement(
            id=new_id,
            element_type=element_type,
            name=str(payload.get("name", element_type)),
            x=int(payload.get("x", 0)),
            y=int(payload.get("y", 0)),
            width=max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, int(payload.get("width", 220)))),
            height=max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, int(payload.get("height", 70)))),
            text=str(payload.get("text", "")),
            text_alignment=str(payload.get("text_alignment", "Left")),
            text_color_override=text_color_override,
            text_color=text_color,
            background_color_override=background_color_override,
            background_color=background_color,
            multiline=bool(payload.get("multiline", False)),
            border_color=str(payload.get("border_color", "")),
            layout=str(payload.get("layout", "Vertical")),
            child_alignment=str(payload.get("child_alignment", "UpperLeft")),
            spacing=max(SAFE_MIN_SPACING, min(SAFE_MAX_SPACING, int(payload.get("spacing", 12)))),
            padding_left=max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(payload.get("padding_left", legacy_padding)))),
            padding_right=max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(payload.get("padding_right", legacy_padding)))),
            padding_top=max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(payload.get("padding_top", legacy_padding)))),
            padding_bottom=max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(payload.get("padding_bottom", legacy_padding)))),
            width_mode=normalize_size_mode(payload.get("width_mode", legacy_full_flag_to_size_mode(payload.get("full_width", True))), "width"),
            height_mode=normalize_size_mode(payload.get("height_mode", legacy_full_flag_to_size_mode(payload.get("full_height", False))), "height"),
            scroll_vertical=bool(payload.get("scroll_vertical", False)),
            scroll_horizontal=bool(payload.get("scroll_horizontal", False)),
            props=dict(payload.get("props", {})) if isinstance(payload.get("props", {}), dict) else {},
        )
        self.elements[new_id] = element

        if component_id is not None and instance_root_id is not None:
            self._set_component_metadata(element, component_id, instance_root_id, path)

        children_value = payload.get("children", [])
        if isinstance(children_value, list):
            for index, child in enumerate(children_value):
                if not isinstance(child, dict):
                    continue
                child_path = str(index) if path == "" else f"{path}/{index}"
                child_id = self._clone_payload_into_document(child, component_id, instance_root_id, child_path)
                element.children.append(child_id)

        return new_id

    def list_components(self) -> list[UIComponentDefinition]:
        # List reusable components sorted by display name.

        return sorted(self.components.values(), key=lambda component: component.name.lower())

    def get_component_display_name(self, component_id: str) -> str:
        # Resolve display name for a known component id.

        component = self.components.get(component_id)
        if component is None:
            return "Unknown"
        return component.name

    def get_component_binding(self, node_id: str) -> tuple[str, str, bool] | None:
        # Return component linkage for a node as (component_id, component_name, is_instance_root).

        node = self.elements.get(node_id)
        if node is None:
            return None

        metadata = self._extract_component_metadata(node)
        if metadata is None:
            return None

        component_id, instance_root_id, _node_path = metadata
        if component_id not in self.components:
            return None

        return component_id, self.components[component_id].name, node_id == instance_root_id

    def rename_component(self, component_id: str, new_name: str) -> None:
        # Rename one component definition with strict validation.

        component = self.components.get(component_id)
        if component is None:
            raise ValueError("Component does not exist.")

        normalized_name = new_name.strip()
        if normalized_name == "":
            raise ValueError("Component name cannot be empty.")

        component.name = normalized_name

    def get_component_instance_roots(self, component_id: str) -> list[str]:
        # Return instance root ids for a component in stable root/sibling order.

        if component_id not in self.components:
            return []

        roots: list[str] = []
        for root_id in self.roots:
            if self.component_instances.get(root_id) == component_id:
                roots.append(root_id)

        for node_id, mapped_component_id in sorted(self.component_instances.items(), key=lambda pair: pair[0]):
            if mapped_component_id != component_id or node_id in roots:
                continue
            if node_id in self.elements:
                roots.append(node_id)

        return roots

    def convert_to_component(self, element_id: str, component_name: Optional[str] = None) -> str:
        # Turn an existing subtree into a reusable component and bind this subtree as its first instance.

        if element_id not in self.elements:
            raise ValueError("Element does not exist.")

        component_id = str(uuid.uuid4())
        root_node = self.elements[element_id]
        normalized_name = (component_name or root_node.name or "Component").strip()
        if normalized_name == "":
            normalized_name = "Component"

        template = self._strip_component_ids(self._serialize_node_tree(element_id))
        self.components[component_id] = UIComponentDefinition(id=component_id, name=normalized_name, template=template)
        self.component_instances[element_id] = component_id

        def annotate(node_id: str, path: str) -> None:
            node = self.elements[node_id]
            self._set_component_metadata(node, component_id, element_id, path)
            for index, child_id in enumerate(node.children):
                child_path = str(index) if path == "" else f"{path}/{index}"
                annotate(child_id, child_path)

        annotate(element_id, "")
        return component_id

    def instantiate_component_after(self, component_id: str, target_id: Optional[str]) -> str:
        # Insert a new instance of a component as sibling-after target or as root.

        if component_id not in self.components:
            raise ValueError("Component does not exist.")
        if target_id is not None and target_id not in self.elements:
            raise ValueError("Target element does not exist.")

        template = self.components[component_id].template
        new_root_id = self._clone_payload_into_document(template, None, None, "")
        self.component_instances[new_root_id] = component_id

        def annotate(node_id: str, path: str) -> None:
            node = self.elements[node_id]
            self._set_component_metadata(node, component_id, new_root_id, path)
            for index, child_id in enumerate(node.children):
                child_path = str(index) if path == "" else f"{path}/{index}"
                annotate(child_id, child_path)

        annotate(new_root_id, "")

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

    def sync_component_from_node(self, source_node_id: str) -> int:
        # Propagate one node edit to all instances of its component and update template payload.

        if source_node_id not in self.elements:
            return 0

        source = self.elements[source_node_id]
        metadata = self._extract_component_metadata(source)
        if metadata is None:
            return 0

        component_id, _instance_root, node_path = metadata
        component = self.components.get(component_id)
        if component is None:
            return 0

        template_node = component.template
        if node_path != "":
            parts = node_path.split("/")
            for part in parts:
                try:
                    index = int(part)
                except ValueError:
                    return 0
                children = template_node.get("children", [])
                if not isinstance(children, list) or index < 0 or index >= len(children):
                    return 0
                child = children[index]
                if not isinstance(child, dict):
                    return 0
                template_node = child

        template_node["type"] = source.element_type
        template_node["name"] = source.name
        template_node["x"] = source.x
        template_node["y"] = source.y
        template_node["width"] = source.width
        template_node["height"] = source.height
        template_node["text"] = source.text
        template_node["text_alignment"] = source.text_alignment
        template_node["text_color_override"] = source.text_color_override
        template_node["text_color"] = source.text_color
        template_node["background_color_override"] = source.background_color_override
        template_node["background_color"] = source.background_color
        template_node["multiline"] = source.multiline
        template_node["border_color"] = source.border_color
        template_node["layout"] = source.layout
        template_node["child_alignment"] = source.child_alignment
        template_node["spacing"] = source.spacing
        template_node["padding"] = source.padding_left
        template_node["padding_left"] = source.padding_left
        template_node["padding_right"] = source.padding_right
        template_node["padding_top"] = source.padding_top
        template_node["padding_bottom"] = source.padding_bottom
        template_node["width_mode"] = source.width_mode
        template_node["height_mode"] = source.height_mode
        template_node["scroll_vertical"] = source.scroll_vertical
        template_node["scroll_horizontal"] = source.scroll_horizontal
        props_copy: dict[str, object] = {}
        for key, value in source.props.items():
            if key.startswith("__component_"):
                continue
            props_copy[key] = value
        template_node["props"] = props_copy

        updated = 0

        def apply_to_instance(node: UIElement) -> None:
            nonlocal updated
            node.element_type = source.element_type
            node.name = source.name
            node.x = source.x
            node.y = source.y
            node.width = source.width
            node.height = source.height
            node.text = source.text
            node.text_alignment = source.text_alignment
            node.text_color_override = source.text_color_override
            node.text_color = source.text_color
            node.background_color_override = source.background_color_override
            node.background_color = source.background_color
            node.multiline = source.multiline
            node.border_color = source.border_color
            node.layout = source.layout
            node.child_alignment = source.child_alignment
            node.spacing = source.spacing
            node.padding_left = source.padding_left
            node.padding_right = source.padding_right
            node.padding_top = source.padding_top
            node.padding_bottom = source.padding_bottom
            node.width_mode = source.width_mode
            node.height_mode = source.height_mode
            node.scroll_vertical = source.scroll_vertical
            node.scroll_horizontal = source.scroll_horizontal
            preserved: dict[str, object] = {}
            for key, value in node.props.items():
                if key.startswith("__component_"):
                    preserved[key] = value
            node.props = dict(props_copy)
            node.props.update(preserved)
            updated += 1

        for node in self.elements.values():
            node_metadata = self._extract_component_metadata(node)
            if node_metadata is None:
                continue
            peer_component_id, _peer_instance_root, peer_path = node_metadata
            if peer_component_id != component_id or peer_path != node_path:
                continue
            if node.id == source_node_id:
                continue
            apply_to_instance(node)

        return updated

    def sync_component_structure_from_node(self, source_node_id: str) -> int:
        # Rebuild one component template from an edited instance root and mirror structure to peer roots.

        if source_node_id not in self.elements:
            return 0

        source_metadata = self._extract_component_metadata(self.elements[source_node_id])
        if source_metadata is None:
            return 0

        component_id, source_instance_root_id, _node_path = source_metadata
        if component_id not in self.components or source_instance_root_id not in self.elements:
            return 0

        template = self._strip_component_ids(self._serialize_node_tree(source_instance_root_id))
        self.components[component_id].template = template

        def annotate_instance(node_id: str, instance_root_id: str, path: str) -> None:
            if node_id not in self.elements:
                return

            node = self.elements[node_id]
            self._set_component_metadata(node, component_id, instance_root_id, path)
            for index, child_id in enumerate(node.children):
                child_path = str(index) if path == "" else f"{path}/{index}"
                annotate_instance(child_id, instance_root_id, child_path)

        annotate_instance(source_instance_root_id, source_instance_root_id, "")

        def delete_subtree(node_id: str) -> None:
            if node_id not in self.elements:
                return

            children = list(self.elements[node_id].children)
            for child_id in children:
                delete_subtree(child_id)

            self.component_instances.pop(node_id, None)
            del self.elements[node_id]

        def apply_template(node_id: str, template_node: dict, instance_root_id: str, path: str) -> None:
            if node_id not in self.elements:
                raise ValueError("Component sync target node does not exist.")

            element_type = str(template_node.get("type", ""))
            if element_type == "":
                raise ValueError("Component template node is missing type.")

            node = self.elements[node_id]
            legacy_padding = int(template_node.get("padding", 12))
            node.element_type = element_type
            node.name = str(template_node.get("name", element_type))
            node.x = int(template_node.get("x", 0))
            node.y = int(template_node.get("y", 0))
            node.width = max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, int(template_node.get("width", 220))))
            node.height = max(SAFE_MIN_SIZE, min(SAFE_MAX_SIZE, int(template_node.get("height", 70))))
            node.text = str(template_node.get("text", ""))
            node.text_alignment = str(template_node.get("text_alignment", "Left"))
            node.text_color_override = bool(template_node.get("text_color_override", False))
            node.text_color = str(template_node.get("text_color", "#ffffff"))
            node.background_color_override = bool(template_node.get("background_color_override", False))
            node.background_color = str(template_node.get("background_color", ""))
            node.multiline = bool(template_node.get("multiline", False))
            node.border_color = str(template_node.get("border_color", ""))
            node.layout = str(template_node.get("layout", "Vertical"))
            node.child_alignment = str(template_node.get("child_alignment", "UpperLeft"))
            node.spacing = max(SAFE_MIN_SPACING, min(SAFE_MAX_SPACING, int(template_node.get("spacing", 12))))
            node.padding_left = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(template_node.get("padding_left", legacy_padding))))
            node.padding_right = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(template_node.get("padding_right", legacy_padding))))
            node.padding_top = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(template_node.get("padding_top", legacy_padding))))
            node.padding_bottom = max(SAFE_MIN_PADDING, min(SAFE_MAX_PADDING, int(template_node.get("padding_bottom", legacy_padding))))
            node.width_mode = normalize_size_mode(
                template_node.get("width_mode", legacy_full_flag_to_size_mode(template_node.get("full_width", True))),
                "width",
            )
            node.height_mode = normalize_size_mode(
                template_node.get("height_mode", legacy_full_flag_to_size_mode(template_node.get("full_height", False))),
                "height",
            )
            node.scroll_vertical = bool(template_node.get("scroll_vertical", False))
            node.scroll_horizontal = bool(template_node.get("scroll_horizontal", False))

            props: dict[str, object] = {}
            props_value = template_node.get("props", {})
            if isinstance(props_value, dict):
                for key, value in props_value.items():
                    if isinstance(key, str) and key.startswith("__component_"):
                        continue
                    props[str(key)] = value
            node.props = props
            self._set_component_metadata(node, component_id, instance_root_id, path)

            template_children_raw = template_node.get("children", [])
            template_children: list[dict] = []
            if isinstance(template_children_raw, list):
                for item in template_children_raw:
                    if isinstance(item, dict):
                        template_children.append(item)

            existing_children = list(node.children)
            shared_count = min(len(existing_children), len(template_children))
            next_children: list[str] = []
            for index in range(shared_count):
                child_path = str(index) if path == "" else f"{path}/{index}"
                child_id = existing_children[index]
                apply_template(child_id, template_children[index], instance_root_id, child_path)
                next_children.append(child_id)

            for index in range(shared_count, len(existing_children)):
                delete_subtree(existing_children[index])

            for index in range(shared_count, len(template_children)):
                child_path = str(index) if path == "" else f"{path}/{index}"
                child_id = self._clone_payload_into_document(template_children[index], component_id, instance_root_id, child_path)
                next_children.append(child_id)

            node.children = next_children

        updated_roots = 0
        for instance_root_id in self.get_component_instance_roots(component_id):
            if instance_root_id == source_instance_root_id:
                continue
            if instance_root_id not in self.elements:
                continue

            apply_template(instance_root_id, template, instance_root_id, "")
            self.component_instances[instance_root_id] = component_id
            updated_roots += 1

        self._clear_stale_component_metadata()
        return updated_roots

    def _clear_stale_component_metadata(self) -> None:
        # Remove component metadata from nodes that are no longer inside their declared instance roots.

        instance_members: dict[str, set[str]] = {}
        for instance_root_id, component_id in self.component_instances.items():
            if component_id not in self.components or instance_root_id not in self.elements:
                continue
            instance_members[instance_root_id] = set(self._collect_subtree_ids(instance_root_id))

        for node in self.elements.values():
            metadata = self._extract_component_metadata(node)
            if metadata is None:
                continue

            component_id, instance_root_id, _path = metadata
            members = instance_members.get(instance_root_id)
            if members is None or node.id not in members or component_id not in self.components:
                self._clear_component_metadata(node)

    def add_element(self, element_type: str, parent_id: Optional[str]) -> UIElement:
        # Create and attach an element under a valid parent or the root scene.

        if parent_id is not None and parent_id not in self.elements:
            raise ValueError("Parent element does not exist.")

        if parent_id is not None and self.elements[parent_id].element_type not in self.CONTAINER_TYPES:
            raise ValueError("Only Window, Container, and Box can have children.")

        identifier = str(uuid.uuid4())
        label = f"{element_type}_{len(self.elements) + 1}"
        default_text = ""
        if element_type in {"Window", "Label", "Button", "ButtonWithLabel", "Toggle", "ToggleWithLabel", "TextInput", "InputWithLabel"}:
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

        removed_instance_roots: list[str] = []
        if element_id in self.component_instances:
            removed_instance_roots.append(element_id)

        subtree = self._collect_subtree_ids(element_id)
        for node_id in subtree:
            if node_id in self.component_instances and node_id not in removed_instance_roots:
                removed_instance_roots.append(node_id)
            del self.elements[node_id]

        for instance_root_id in removed_instance_roots:
            self.component_instances.pop(instance_root_id, None)

        self._prune_unreferenced_components()

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

        payload = {
            "schemaVersion": "1.0.0",
            "roots": [serialize(node_id) for node_id in self.roots],
        }
        if self.components:
            payload["components"] = [
                {
                    "id": component.id,
                    "name": component.name,
                    "template": component.template,
                }
                for component in self.list_components()
            ]
        if self.component_instances:
            payload["component_instances"] = [
                {
                    "root_id": root_id,
                    "component_id": component_id,
                }
                for root_id, component_id in sorted(self.component_instances.items(), key=lambda pair: pair[0])
            ]
        return payload

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
        self.components = {}
        self.component_instances = {}

        components_payload = payload.get("components", [])
        if isinstance(components_payload, list):
            for item in components_payload:
                if not isinstance(item, dict):
                    continue
                component_id = str(item.get("id", "")).strip()
                name = str(item.get("name", "")).strip()
                template = item.get("template", {})
                if component_id == "" or name == "" or not isinstance(template, dict):
                    continue
                self.components[component_id] = UIComponentDefinition(
                    id=component_id,
                    name=name,
                    template=self._strip_component_ids(template),
                )

        instances_payload = payload.get("component_instances", [])
        if isinstance(instances_payload, list):
            for item in instances_payload:
                if not isinstance(item, dict):
                    continue
                root_id = str(item.get("root_id", "")).strip()
                component_id = str(item.get("component_id", "")).strip()
                if root_id == "" or component_id == "":
                    continue
                if root_id not in self.elements or component_id not in self.components:
                    continue
                self.component_instances[root_id] = component_id

        for instance_root_id, component_id in list(self.component_instances.items()):
            if instance_root_id not in self.elements:
                self.component_instances.pop(instance_root_id, None)
                continue

            def annotate(node_id: str, path: str) -> None:
                if node_id not in self.elements:
                    return
                node = self.elements[node_id]
                self._set_component_metadata(node, component_id, instance_root_id, path)
                for index, child_id in enumerate(node.children):
                    child_path = str(index) if path == "" else f"{path}/{index}"
                    annotate(child_id, child_path)

            annotate(instance_root_id, "")

        self._prune_unreferenced_components()

    def _prune_unreferenced_components(self) -> None:
        # Keep component definitions even when uninstanced; only clean invalid instance bindings.

        stale_root_ids = [
            root_id
            for root_id, component_id in self.component_instances.items()
            if root_id not in self.elements or component_id not in self.components
        ]
        for root_id in stale_root_ids:
            self.component_instances.pop(root_id, None)

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
