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

- `build-exe.cmd`: builds `CAD Parts.exe` (the app window) with PyInstaller (pinned pywebview / PyInstaller versions).
- `build-release.ps1`: runs `checks.py` (stops if anything fails), then builds the app window and the installer (`dist\`), using [Inno Setup 6](https://jrsoftware.org/isinfo.php).
- The version number lives in `version.json`.

### Checks

`uv run --python 3.12 python checks.py` starts the real server on a free port with a throwaway parts folder and profiles file, drives it over HTTP like the app does, and prints PASS/FAIL per check (exit code 1 on any failure). Run it before every release; `build-release.ps1` does this for you.

### Without the app window

- `Start CAD Parts.cmd`: starts the server in the background (if it isn't running) and opens the dashboard in your browser, at `http://127.0.0.1:8765/?k=<key>` with the launch key the server writes to `%LOCALAPPDATA%\CAD Parts\server.key`.
- `Stop CAD Parts.cmd`: stops the server (port 8765) and the build123d worker (port 8766).

### Running part scripts fast

`uv run --python 3.12 <app folder>/run.py <part>/<part>_vN.py [args]` runs a build123d script like `python script.py`, but on a warm worker (`cadworker.py`, port 8766) that has build123d already loaded: about 0.4 s instead of about 11 s. The first run starts the worker. Runs are handled one at a time; a run started during a long build waits for it.

Bundled: [three.js](https://threejs.org) 0.170 (MIT) in `vendor/three`.
