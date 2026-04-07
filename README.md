# UI Maker (SFS)

Desktop UI designer for Spaceflight Simulator with live in-game preview, JSON based saving and loading, and C# export generation.

## Warning

- Do not use Build + Launch SFS unless you have a reliable post-build step that copies the mod DLL into the SFS Mods folder.
- The editor and game bridge are still under active iteration; some preview/runtime edge cases may still occur.
- Always save your JSON project before major preview actions (refresh, reconnect, disconnect, or restart).
- **The SFS process is tied to the Python session, so closing the app can end or detach the preview workflow.**

## What This Tool Does

- Uses websocket communication to bridge live UI snapshots from the editor to SFS for real-time in-game preview.
- Build and edit UI layouts visually in a desktop editor.
- Push live snapshots to SFS so layout/styling changes can be previewed in-game.
- Export generated C# code from the current UI document.
- Keep project state in JSON for loading/saving and iteration.

Ive followed some core principles in the design and implementation:
I like uis that are layout drived meaning they follow full width/height rules and rely on layout groups to position and size elements rather than manually setting positions/sizes for every element.
But due to comprimised in the editor to allow for the live preview and c# generation some features that would be expected in a ui editor had to be removed or simplified, such as element-specific default props, and some layout options. But overall the core layout and styling features are still there and the live preview allows for quick iteration to achive the desired results.
Although the generated code is mostly hardcoded replica but should match the ui but responsiveness and interaction is still expected to be implemented manually.

## Core Features

- Visual hierarchy editor
	- Add root/child elements
	- Reorder and reparent by drag/drop
	- Context menu actions for copy/paste/delete subtree
	- Convert subtree to reusable component
	- Add reusable component instances
	- Compact hierarchy indicators for component roots/children

- Property inspector
	- Position and size
	- Text and alignment
	- Labeled controls with separate Label Text and Control Text fields
	- Label direction for labeled controls (Top/Bottom/Left/Right)
	- Container layout settings (direction, spacing, padding)
	- Full-width/full-height layout behavior
	- Scroll toggles (vertical/horizontal)
	- Background style modes
		- Image
		- Color
		- Image + Color
	- Tint strength slider for image tint blending

- Live bridge to SFS
	- WebSocket snapshot sync
	- Manual refresh snapshot
	- Connect/disconnect preview visibility
	- Realtime frame/layout feedback in the editor
	- Automatic snapshot restore when switching between Components and main editor tabs

- Runtime preview behavior
	- Authoritative layout overlay fallback when runtime IDs do not overlap scoped view
	- In-memory snapshot restore after reconnect
	- Window clamping to screen bounds
	- Safer connect/disconnect command handling on main thread
	- Improved scroll handling for runtime preview components
	- Component host preview options (transparent/solid background, host W/H auto when set to 0)

- Components workflow
	- Dedicated Components tab with embedded full editor
	- Scoped editing for selected component instance roots
	- Optional hide-window scope while editing components
	- In-game preview mirroring from the primary bridge feed
    - customisable Host preview options for component instances (background mode with transparent or solid color for easy viewing, host W/H auto when set to 0 allwoing it to adapt to the component size)
	- Global propagation of shared component template edits across all instances
    - *Issue:* The layout preview may be broken and so its recomended to be hidden

- Elements
	- Window, ClosableWindow, Container, Box
	- Label, Button, TextInput, Toggle, Slider, Separator, Space
	- ButtonWithLabel, InputWithLabel, ToggleWithLabel

- C# export pipeline
	- Dedicated export panel with preview/copy/save
	- Single-file generated output
    - Entireley Standalone, requiring single line within your mod to show the generated UI (See ![# How to use](#how-to-use))
	- Strongly-typed generated node runtime with component method reuse
	- Exported style fields (including text alignment and background overrides)
	- Labeled control export with split label/control text and directional placement
	- Runtime renderer block loaded from external C# template (`templates/csharp/ui_node_runtime.cs.tpl`) instead of huge inline Python append chains

## Project Workflow

1. Create a new JSON project or load an existing one.
2. Build layout and styling in the editor.
3. Build reusable components as needed and validate in the Components tab.
4. Use live preview to validate behavior in SFS.
5. Save project JSON.
6. Export generated C# when ready.

## Operational Notes

- App startup can start/attach SFS depending on your workflow and button usage.
- Do not use Build + Launch SFS unless your environment is set up to copy the built mod DLL to the SFS Mods folder.
- The SFS process lifecycle is tied to the Python app session in normal use.
	- Closing the Python app can close or detach from SFS depending on current state.
	- Save work before disconnect/close.
- Generated C# remains production-usable but can still require project-specific changes for interaction logic.

## Project Goals and Limits

### What This Aims To Be

- A practical UI layout and preview for SFS mod development.
- Starting point for generating production UI code.
    - although the generated code may be hard to use, ive tried to use a structured monad system and made reusable component methods to make it as easy to use as possible

### What It May Not Fully Achieve Yet

- Perfect 1:1 parity with every custom or modded in-game UI component behavior.
    - it was hard enough to get it anywhere close at first, as the way the ui editor builds the ui is very different from how the game builds it, as the preview is done in runtime it has to interpret commands while exported code is done by the game causing some differences.
- Guaranteed stability for every preview reconnect timing scenario across all game startup states.
- Exact visual matching for every image/tint/material setup used by all UI assets.
- Complete replacement for UI code and UI interaction.
- This tool is designed to be a tool for inspiration and allows for direct usage. generated C# export is useful but may still require manual refinement for use.


## Install
I dont have plans to maintain a latest version, you may have to build your own version of the mod to use

Download the latest release from the Releases page and extract it to a folder. 
1. Copy the `sfs-ui-cdn.dll` to your SFS Mods folder.
2. Run `main.py` to start the editor.

or if you would like the latet version / build your own:
1. Clone the repo and open `sfs-ui-cdn.sln`
    - its recomended to create a post-build step to copy the built `sfs-ui-cdn.dll` to your SFS Mods folder for easier testing.
3. Run `main.py` to start the editor.

4. start sfs
    - If you have a post build command:
        - in the `build` tab click `Build + Launch + Attach` this builds the mod and launces SFS through steam using `steam://rungameid/appid`
    - If you dont have a post build command:
        - Click the `Build only` button in the `build` tab, copy the built `sfs-ui-cdn.dll` to your SFS Mods folder, then click `Launch + Attach` to start SFS through steam using `steam://rungameid/appid`

#### notes
- the editor should detect sfs and show the preview when connected but if sfs **Is running** and no preview is showing, click `Refresh Snapshot`
- you can hide the preview in game by clicking `Disconnect Preview` and reconnect it with `Connect Preview` without needing to restart sfs, but be sure to save your work before disconnecting or closing the editor as the preview state is tied to the session and may be lost on unexpected exits.
- Dont close the python app while sfs is running as the process is tied to the python session.

## How To Use
This is how ive activated the ui for testing. The generated C# is designed to be entirely standalone allowing you to easily integrate it into your mod.
(
    Keep in mind the generated code is meant to be a strong starting point but may still require manual refinement for complex interaction logic or edge cases.
    E.G. use the generated code as the layout and styling, then implement your own code/logic for handling dynamic data, interactions, and advanced behaviors as needed.
)

```c#
public override void Load()
{
    base.Load();
    GeneratedUI.GeneratedLayout.Render(new GeneratedUI.GeneratedLayout.RenderOptions
    {
        SceneToAttach = Builder.SceneToAttach.CurrentScene,
        HolderName = "GeneratedUIHolder",
        WindowDraggable = true,
    });
}
```

with options like:
```c#
    SceneToAttach = Builder.SceneToAttach.CurrentScene,
    HolderName = "GeneratedUIHolder",                   // The name of the GameObject that will hold the generated UI
    WindowDraggable = true,                             // Self explanatory
    WindowMode = WindowRenderMode.AsDefined,            // Whether Overides with AsDefined, ForceNormal, ForceClosable. I havent fully tested but its easy to change the window type in the generated code
    Width = null,                                       // Optional width overide for the root window/node overiding what was set in the editor
    Height = null,                                      // Optional height overide for the root window/node overiding what was set in the editor
    StartHidden = false,                                // Whether to start with the generated UI hidden (can be shown later through code by calling Show() on the root node)
    RemoveExistingBeforeRender = true,                  // Whether to remove any existing rendered instance of this layout before rendering a new one (useful to prevent duplicates during iterative development)
```

Components that were defined in the editor will be generated as reusable methods in the generated C# code, allowing you to easily reuse them in your mod code as well.
Like this shader dependency element i made:
```c#
private static UiNode CreateDependancy(string id)
{
    // Build component instance subtree.
    return Node($"{id}/0", UiNodeType.Box, "Dependancy", 220, 100)
        .At(30, 30)
        .Visual(TextAnchor.MiddleLeft, false, false, "#ffffff", true, "#9ab68d", "")
        .LayoutConfig(SFS.UI.ModGUI.Type.Horizontal, TextAnchor.UpperLeft, 12, 12, 12, 12, 12)
        .Sizing(UiSizeMode.Auto, UiSizeMode.Manual, 1)
        .AddChildren(
            Node($"{id}/0/0", UiNodeType.Container, "Container_62", 220, 70)
                .LayoutConfig(SFS.UI.ModGUI.Type.Vertical, TextAnchor.UpperLeft, 8, 0, 0, 0, 0)
                .Sizing(UiSizeMode.Auto, UiSizeMode.Auto, 2)
                .AddChildren(
                    Node($"{id}/0/0/0", UiNodeType.Label, "Name", 600, 70)
                        .WithText("Shader Name")
                        .Sizing(UiSizeMode.Auto, UiSizeMode.Auto, 2),
                    Node($"{id}/0/0/1", UiNodeType.Box, "Box_61", 400, 30)
                        .Visual(TextAnchor.MiddleLeft, false, false, "#ffffff", true, "#555555", "")
                        .LayoutConfig(SFS.UI.ModGUI.Type.Vertical, TextAnchor.UpperLeft, 0, 0, 0, 0, 0)
                        .Sizing(UiSizeMode.Manual, UiSizeMode.Manual, 2)
                        .AddChildren(
                            Node($"{id}/0/0/1/0", UiNodeType.Label, "Shader_Path", 220, 70)
                                .WithText("Hidden/ShaderPath")
                                .Visual(TextAnchor.MiddleCenter, false, false, "#ffffff", false, "#f9f9f9", "")
                                .Sizing(UiSizeMode.Auto, UiSizeMode.Auto, 1)
                        )
                ),
            Node($"{id}/0/1", UiNodeType.Container, "Container_62", 220, 70)
                .LayoutConfig(SFS.UI.ModGUI.Type.Vertical, TextAnchor.UpperLeft, 0, 0, 0, 0, 0)
                .Sizing(UiSizeMode.Auto, UiSizeMode.Auto, 2)
                .AddChildren(
                    Node($"{id}/0/1/0", UiNodeType.Label, "Shader Status", 220, 70)
                        .WithText("Shader Status")
                        .Visual(TextAnchor.MiddleRight, false, false, "#ffffff", false, "", "")
                        .Sizing(UiSizeMode.Auto, UiSizeMode.Auto, 2),
                    Node($"{id}/0/1/1", UiNodeType.Label, "Shader Type", 220, 70)
                        .WithText("Shader Type")
                        .Visual(TextAnchor.MiddleRight, false, false, "#ffffff", false, "", "")
                        .Sizing(UiSizeMode.Auto, UiSizeMode.Auto, 2)
                )
        );
}
```

The generated c# code will include all the helper functions it needs like Types and responsive sizing helpers.


### Recommended Expectation

- Treat this as a strong acceleration tool for layout, structure, and preview-driven iteration.
- Expect to do final manual polish/validation inside your mod code for advanced edge cases.
- I have designed it to be responsive but ive had to comprimise some features to achive preview and c# generation so responsiveness is only expected during ui design not in export.
