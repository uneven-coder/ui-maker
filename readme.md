# UI Maker (SFS)

Desktop UI designer for Spaceflight Simulator with live in-game preview, JSON project workflow, and C# export generation.

## Warning

- Do not use Build + Launch SFS unless you have a reliable post-build step that copies the mod DLL into the SFS Mods folder.
- The editor and game bridge are still under active iteration; some preview/runtime edge cases may still occur.
- Always save your JSON project before major preview actions (refresh, reconnect, disconnect, or restart).
- **The SFS process is tied to the Python session, so closing the app can end or detach the preview workflow.**

## What This Tool Does

- Build and edit UI layouts visually in a desktop editor.
- Push live snapshots to SFS so layout/styling changes can be previewed in-game.
- Export generated C# code from the current UI document.
- Keep project state in JSON for loading/saving and iteration.

## Core Features

- Visual hierarchy editor
	- Add root/child elements
	- Reorder and reparent by drag/drop
	- Context menu actions for copy/paste/delete subtree

- Property inspector
	- Position and size
	- Text and alignment
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

- Runtime preview behavior
	- In-memory snapshot restore after reconnect
	- Window clamping to screen bounds
	- Safer connect/disconnect command handling on main thread
	- Improved scroll handling for runtime preview components

- C# export pipeline
	- Dedicated export panel with preview/copy/save
	- Single-file generated output
	- Generated manifest + binding interface + layout build method
	- Exported style fields (including background mode and tint strength)

## Project Workflow

1. Create a new JSON project or load an existing one.
2. Build layout and styling in the editor.
3. Use live preview to validate behavior in SFS.
4. Save project JSON.
5. Export generated C# when ready.

## Operational Notes

- App startup can start/attach SFS depending on your workflow and button usage.
- Do not use Build + Launch SFS unless your environment is set up to copy the built mod DLL to the SFS Mods folder.
- The SFS process lifecycle is tied to the Python app session in normal use.
	- Closing the Python app can close or detach from SFS depending on current state.
	- Save work before disconnect/close.

## Project Goals and Limits

### What This Aims To Be

- A practical UI layout and preview workflow for SFS mod development.
- A fast iteration loop between editor changes and in-game visual feedback.
- A useful code-generation starting point (not just mock data), with exportable C# scaffolding.

### What It May Not Fully Achieve Yet

- Perfect 1:1 parity with every custom or modded in-game UI component behavior.
- Guaranteed stability for every preview reconnect timing scenario across all game startup states.
- Exact visual matching for every image/tint/material setup used by all UI assets.
- Complete replacement for hand-tuned production UI code in complex interaction-heavy screens.
- This tool is designed to be a tool for inspiration and allows for direct usage. generated C# export is useful but may still require manual refinement for use.

### Recommended Expectation

- Treat this as a strong acceleration tool for layout, structure, and preview-driven iteration.
- Expect to do final manual polish/validation inside your mod code for advanced edge cases.

## Suggested GIF Placements

Use short, focused GIFs (5 to 12 seconds each) in these sections:

- Hero section (top of README)
	- Show: create element -> change property -> see live update in SFS.

- Hierarchy editing
	- Show: drag/drop reparenting + copy/paste subtree.

- Styling modes
	- Show: Image vs Color vs Image + Color on one element.
	- Show tint slider moving from 0 to 100.

- Scrolling preview
	- Show: enable vertical scroll in editor, then interact with scroll in-game preview.

- Bridge controls
	- Show: disconnect preview, reconnect preview, state restored.

- Export workflow
	- Show: open Export tab -> refresh preview -> copy/save C# output.
