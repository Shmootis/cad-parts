# CAD Parts

A Windows app for designing 3D-printable parts by describing them. You type what you need ("a wall hook for a 30 mm broom handle, Skadis pegboard"), Claude writes and runs a [build123d](https://github.com/gumyr/build123d) model, and the part appears in a 3D viewer. Every change is saved as a new version, ready to open in your slicer.

## Download

Get **CAD-Parts-Setup-x.y.z.exe** from the [latest release](https://github.com/Shmootis/cad-parts/releases/latest) and run it. It installs for your Windows user only (no admin rights needed).

Windows may warn that the installer is from an unknown publisher, because it isn't code-signed yet. Click **More info → Run anyway**.

## What you need

- Windows 10 or 11 (64-bit)
- **Your own Claude account** (Pro or Max plan, or an Anthropic API key). The app uses Claude Code on your PC, signed in to your account.

On first start, CAD Parts checks for the free tools it uses and helps install anything missing:

| Tool | Why |
|---|---|
| Claude Code | designs and edits the parts |
| Git for Windows | Claude Code uses it to run commands on Windows |
| uv + Python + build123d | the CAD engine (downloaded automatically, a few hundred MB the first time) |
| Blender, Bambu Studio *(optional)* | the "Open in…" buttons |

## Your parts

You choose where parts are kept: a OneDrive folder (synced across your PCs), a folder on this PC only, or any folder you like. Each part gets its own folder with versioned STL files and notes. Uninstalling the app never touches your parts.

## Building from source

- `build-exe.cmd`: builds `CAD Parts.exe` (the app window) with PyInstaller.
- `build-release.ps1`: builds the app window and then the installer (`dist\`), using [Inno Setup 6](https://jrsoftware.org/isinfo.php).
- The version number lives in `version.json`.

Bundled: [three.js](https://threejs.org) 0.170 (MIT) in `vendor/three`.
