"""CAD Parts dashboard: a tiny local server for the cad-parts folder.

Run:  uv run --python 3.12 python server.py [--open]
Then: http://127.0.0.1:8765

Standard library only. Binds to 127.0.0.1, so nothing outside this PC can reach it.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess

if os.name == "nt":  # helpers the server starts (Claude Code, PowerShell, uv…) never pop up a console window,
    _Popen_init = subprocess.Popen.__init__  # even when the server itself was started without a console.

    def _no_window_init(self, *args, creationflags=0, **kwargs):
        if not creationflags:
            creationflags = subprocess.CREATE_NO_WINDOW
        _Popen_init(self, *args, creationflags=creationflags, **kwargs)

    subprocess.Popen.__init__ = _no_window_init
import sys
import struct
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = 8765
APP_DIR = Path(__file__).resolve().parent  # the app's own code (never inside a parts folder)
ROOT: Path | None = None  # the active profile's parts folder; None until the first-run setup is done
META = ".cadparts"  # hidden folder inside each parts folder: settings, favorites/tags, new-part chat, pictures
APP_DATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "CAD Parts"
PROFILES_FILE = APP_DATA / "profiles.json"  # who uses this PC and where their parts are
SHOW_PS1 = APP_DIR / "blender" / "show.ps1"  # one reused Blender window (viewer.py watches a trigger file)
VIEWER_STATE = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "CAD Parts" / "blender"
LOCAL_BIN = Path.home() / ".local" / "bin"  # where the uv and Claude Code installers put their programs
if str(LOCAL_BIN).lower() not in os.environ.get("PATH", "").lower():  # freshly installed tools work without a re-login
    os.environ["PATH"] = str(LOCAL_BIN) + os.pathsep + os.environ.get("PATH", "")
PROGRAM_FILES = [Path(os.environ.get(k)) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432") if os.environ.get(k)]


# ---------------------------------------------------------------- tools this app relies on (found, not assumed)
def _first(paths):
    for x in paths:
        if x and Path(x).is_file():
            return str(x)
    return None


def find_claude() -> str | None:
    return shutil.which("claude") or _first([LOCAL_BIN / "claude.exe"])


def find_uv() -> str | None:
    return shutil.which("uv") or _first([LOCAL_BIN / "uv.exe"])


def find_git_bash() -> str | None:
    """Claude Code on Windows runs its shell commands in Git Bash (not WSL's bash.exe)."""
    git = shutil.which("git")
    cands = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")]
    if git:
        g = Path(git).resolve()
        cands += [g.parent.parent / "bin" / "bash.exe", g.parent / "bash.exe"]
    cands += [pf / "Git" / "bin" / "bash.exe" for pf in PROGRAM_FILES]
    cands.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe")
    return _first(c for c in cands if c and "system32" not in str(c).lower())


def _version_key(p: Path):
    return [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", p.parent.name)]


def find_blender() -> str | None:
    found = shutil.which("blender")
    if found:
        return found
    cands = []
    for pf in PROGRAM_FILES:
        cands += sorted((pf / "Blender Foundation").glob("Blender*/blender.exe"), key=_version_key, reverse=True)
        cands.append(pf / "Steam" / "steamapps" / "common" / "Blender" / "blender.exe")
    return _first(cands)


# ---------------------------------------------------------------- "Open with": slicers and 3D apps on this PC
# Known apps are found in the usual install folders; anything else can be added with "Choose another app…".
# Choices are per PC (program locations differ between PCs), so they live in %APPDATA%\CAD Parts\apps.json.
KNOWN_APPS = [  # id, name, kind, install-folder patterns (under Program Files / LocalAppData\Programs)
    ("bambu", "Bambu Studio", "slicer", ["Bambu Studio/bambu-studio.exe"]),
    ("orca", "OrcaSlicer", "slicer", ["OrcaSlicer/orca-slicer.exe"]),
    ("prusa", "PrusaSlicer", "slicer", ["Prusa3D/PrusaSlicer/prusa-slicer.exe", "PrusaSlicer*/prusa-slicer.exe"]),
    ("cura", "UltiMaker Cura", "slicer", ["UltiMaker Cura*/UltiMaker-Cura.exe", "Ultimaker Cura*/UltiMaker-Cura.exe",
                                         "Ultimaker Cura*/Cura.exe"]),
    ("creality", "Creality Print", "slicer", ["Creality/Creality Print*/CrealityPrint.exe", "Creality Print*/CrealityPrint.exe"]),
    ("lychee", "Lychee Slicer", "slicer", ["Lychee/LycheeSlicer.exe", "Mango3D/Lychee*/LycheeSlicer.exe", "Lychee*/LycheeSlicer.exe"]),
    ("chitubox", "CHITUBOX", "slicer", ["CHITUBOX/CHITUBOX.exe", "CHITUBOX*/CHITUBOX.exe"]),
    ("photon", "Photon Workshop", "slicer", ["ANYCUBIC/Photon Workshop*/PhotonWorkshop.exe", "Photon Workshop*/PhotonWorkshop.exe"]),
    ("blender", "Blender", "modeler", []),  # found by find_blender(); opens through the one-window viewer helper
    ("freecad", "FreeCAD", "modeler", ["FreeCAD*/bin/FreeCAD.exe"]),
    ("meshmixer", "Meshmixer", "modeler", ["Autodesk/Meshmixer/meshmixer.exe"]),
]
APPS_FILE = APP_DATA / "apps.json"


def _find_pattern(patterns) -> str | None:
    roots = PROGRAM_FILES + [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"]
    for pat in patterns:
        for root in roots:
            hits = sorted(root.glob(pat), key=_version_key, reverse=True) if root.is_dir() else []
            if hits:
                return str(hits[0])
    return None


def _apps_state() -> dict:
    try:
        st = json.loads(APPS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("defaults", {})
    st.setdefault("custom", [])
    return st


def _save_apps_state(st: dict) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    APPS_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def installed_apps() -> list[dict]:
    out = []
    for aid, name, kind, pats in KNOWN_APPS:
        exe = find_blender() if aid == "blender" else _find_pattern(pats)
        if exe:
            out.append({"id": aid, "name": name, "kind": kind, "exe": exe})
    for c in _apps_state()["custom"]:
        if Path(c.get("exe", "")).is_file():
            out.append({"id": c["id"], "name": c["name"], "kind": c.get("kind") or "other", "exe": c["exe"], "custom": True})
    return out


def apps_info() -> dict:
    apps, st = installed_apps(), _apps_state()
    ids = {a["id"] for a in apps}
    defaults = {}
    for kind, prefer in (("slicer", "bambu"), ("modeler", "blender")):
        want = st["defaults"].get(kind)
        if want == "none":
            defaults[kind] = ""
        elif want in ids:
            defaults[kind] = want
        else:  # nothing chosen yet (or it was uninstalled): the usual one if present, else the first found
            same = [a["id"] for a in apps if a["kind"] == kind]
            defaults[kind] = prefer if prefer in same else (same[0] if same else "")
    return {"apps": [{k: v for k, v in a.items() if k != "exe"} for a in apps], "defaults": defaults}


def set_default_apps(body: dict) -> dict:
    st = _apps_state()
    for kind in ("slicer", "modeler"):
        if kind in body:
            st["defaults"][kind] = str(body[kind] or "none")
    _save_apps_state(st)
    return {"ok": True, **apps_info()}


def open_with(app_id: str, rel: str) -> dict:
    if app_id == "explorer":
        return show_in_folder(rel)
    p = safe_path(rel)
    if not p or not p.is_file() or p.suffix.lower() not in MODEL_EXT:
        return {"ok": False, "error": "not a model in your parts folder"}
    app = next((a for a in installed_apps() if a["id"] == app_id), None)
    if not app:
        return {"ok": False, "error": "that app isn't installed any more"}
    if app_id == "blender":  # one reused Blender window (viewer.py watches a trigger file)
        if p.suffix.lower() != ".stl":
            return {"ok": False, "error": "Blender opens STL files"}
        VIEWER_STATE.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_PS1),
                            "-Path", str(p), "-Blender", app["exe"], "-State", str(VIEWER_STATE)],
                           capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0, "error": "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-200:]}
    # Bambu Studio: its own "single instance" setting decides whether an open window is reused.
    # Don't pass --single-instance: some builds exit with -2 on it.
    subprocess.Popen([app["exe"], str(p)], cwd=str(Path(app["exe"]).parent),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return {"ok": True, "name": app["name"]}


ICON_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "CAD Parts" / "icons"
EXPLORER = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe")


def app_icon(app_id: str) -> bytes | None:
    """The app's own icon (taken from its .exe), as a PNG; cached per PC."""
    if app_id == "explorer":
        exe = EXPLORER
    else:
        app = next((a for a in installed_apps() if a["id"] == app_id), None)
        exe = app and app["exe"]
    if not exe or not Path(exe).is_file():
        return None
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(Path(exe).stat().st_mtime)
    out = ICON_DIR / f"{re.sub(r'[^a-zA-Z0-9_-]', '_', app_id)}-{stamp}.png"
    if not out.is_file():
        ps = ("Add-Type -AssemblyName System.Drawing; "
              f"$i = [System.Drawing.Icon]::ExtractAssociatedIcon({_ps_lit(exe)}); "
              f"if ($i) {{ $i.ToBitmap().Save({_ps_lit(str(out))}, [System.Drawing.Imaging.ImageFormat]::Png) }}")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    try:
        return out.read_bytes()
    except OSError:
        return None


def show_in_folder(rel: str) -> dict:
    p = safe_path(rel)
    if not p or not p.exists():
        return {"ok": False, "error": "not found"}
    subprocess.Popen([EXPLORER, "/select,", str(p)] if p.is_file() else [EXPLORER, str(p)])
    return {"ok": True, "name": "File Explorer"}


def pick_app() -> dict:
    """"Choose another app…": Windows' file picker for a program, which is then kept in the Open with list."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.OpenFileDialog;"
          "$f.Title = 'Choose an app to open your parts with'; $f.Filter = 'Programs (*.exe)|*.exe';"
          "$f.InitialDirectory = $env:ProgramFiles;"
          "$o = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false};"
          "if ($f.ShowDialog($o) -eq 'OK') { $f.FileName; (Get-Item -LiteralPath $f.FileName).VersionInfo.ProductName }")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, timeout=600)
    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if not lines or not lines[0].lower().endswith(".exe"):
        return {"ok": False, "cancelled": True}
    exe = lines[0]
    name = (lines[1] if len(lines) > 1 else "") or Path(exe).stem
    st = _apps_state()
    known = next((c for c in st["custom"] if c["exe"].lower() == exe.lower()), None)
    if not known:
        known = {"id": f"c{int(time.time() * 1000):x}", "name": name[:40], "exe": exe, "kind": "other"}
        st["custom"].append(known)
        _save_apps_state(st)
    return {"ok": True, "id": known["id"], **apps_info()}


def find_bambu() -> str | None:
    return _first(pf / "Bambu Studio" / "bambu-studio.exe" for pf in PROGRAM_FILES)


_worker = {"state": "starting", "error": ""}  # the warm build123d worker (first run downloads build123d)


def warm_worker() -> None:
    _worker.update(state="starting", error="")
    uv = find_uv()
    if not uv:
        _worker.update(state="failed", error="uv isn't installed")
        return
    try:
        r = subprocess.run([uv, "run", "--python", "3.12", str(APP_DIR / "run.py"), "--warm"], cwd=str(APP_DIR),
                           capture_output=True, text=True, timeout=1200, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = r.returncode == 0
        _worker.update(state="ready" if ok else "failed", error="" if ok else ((r.stderr or r.stdout or "").strip()[-300:]))
    except subprocess.TimeoutExpired:
        _worker.update(state="failed", error="took too long (slow download?)")


def worker_up() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 8766), timeout=1) as so:
            so.sendall(b'{"ping": true}\n')
            return bool(so.makefile("rb").readline())
    except OSError:
        return False


_auth_cache = {"t": 0.0, "v": None}
_account = {"id": ""}  # fingerprint of the signed-in Claude account's email (see account_id); never sent to the page


def account_id() -> str:
    """Who is signed in to Claude on this PC, as a SHA-256 fingerprint of the account's email: enough to recognise
    the same person's parts folder on another PC, without writing the email itself into a (synced) folder."""
    if not _account["id"]:
        claude_auth(force=True)
    return _account["id"]


def claude_auth(force: bool = False) -> dict:
    """{'loggedIn': bool, 'plan': str} from `claude auth status --json` (cached briefly; it takes ~1 s)."""
    if not force and _auth_cache["v"] is not None and time.time() - _auth_cache["t"] < 20:
        return _auth_cache["v"]
    exe = find_claude()
    v = {"loggedIn": False, "plan": "", "error": ""}
    if exe:
        try:
            r = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            d = json.loads(r.stdout or "{}")
            v["loggedIn"] = bool(d.get("loggedIn"))
            email = str(d.get("email") or "").strip().lower() if v["loggedIn"] else ""
            import hashlib
            _account["id"] = hashlib.sha256(email.encode()).hexdigest() if email else ""
            if _account["id"]:
                stamp_owner()
            v["plan"] = str(d.get("subscriptionType") or d.get("planTier") or ("API key" if d.get("authMethod") == "api_key" else "") or "")
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            v["error"] = str(e)[:200]
    _auth_cache.update(t=time.time(), v=v)
    return v


def _version(exe: str) -> str:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "").strip().split(" (")[0][:30]
    except (OSError, subprocess.TimeoutExpired):
        return ""


def health(force: bool = False) -> dict:
    claude, bash, blender, bambu = find_claude(), find_git_bash(), find_blender(), find_bambu()
    auth = claude_auth(force) if claude else {"loggedIn": False, "plan": ""}
    if _worker["state"] != "ready" and worker_up():
        _worker.update(state="ready", error="")
    w = _worker["state"]
    checks = [
        {"id": "claude", "name": "Claude Code", "required": True, "ok": bool(claude),
         "detail": ("version " + _version(claude)) if claude else "Runs the chat that designs your parts", "action": "install"},
        {"id": "signin", "name": "Signed in to Claude", "required": True, "ok": bool(auth.get("loggedIn")),
         "detail": (f"{auth['plan'].capitalize()} plan" if auth.get("plan") else "Signed in") if auth.get("loggedIn")
         else ("Install Claude Code first" if not claude else "Use your own Claude account (Pro or Max) or an API key"),
         "action": "signin" if claude else ""},
        {"id": "git", "name": "Git for Windows", "required": True, "ok": bool(bash),
         "detail": "Found" if bash else "Claude Code needs it to run commands on Windows", "action": "install"},
        {"id": "build123d", "name": "CAD engine (Python + build123d)", "required": True, "ok": w == "ready",
         "detail": {"ready": "Ready", "starting": "Setting up: the first time downloads a few hundred MB, so it can take several minutes",
                    "failed": "Didn't start: " + (_worker["error"] or "unknown error")}[w], "action": "retry" if w == "failed" else "",
         "busy": w == "starting"},
        {"id": "blender", "name": "Blender", "required": False, "ok": bool(blender),
         "detail": "Found" if blender else "Optional: for the Open in Blender button", "action": "download"},
        {"id": "bambu", "name": "Bambu Studio", "required": False, "ok": bool(bambu),
         "detail": "Found" if bambu else "Optional: for the Open in Bambu Studio button", "action": "download"},
    ]
    return {"checks": checks, "ready": all(c["ok"] for c in checks if c["required"]),
            "tools": {"blender": bool(blender), "bambu": bool(bambu)}, "jobs": {k: dict(v) for k, v in _jobs.items()}}


# ---------------------------------------------------------------- version + update check (GitHub Releases)
def _version_file() -> dict:
    try:
        return json.loads((APP_DIR / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


APP_VERSION = str(_version_file().get("version") or "0.0.0")
UPDATE_REPO = str(_version_file().get("repo") or "")  # "owner/name" of the public GitHub repo; "" = no update check
_update = {"t": 0.0, "v": None}


def _vtuple(v: str):
    return tuple(int(n) for n in re.findall(r"\d+", v)[:3]) or (0,)


def update_info() -> dict:
    """{'version', 'latest', 'url', 'newer'}; asks GitHub at most every 6 hours (nothing about the user is sent)."""
    info = {"version": APP_VERSION, "latest": None, "url": None, "newer": False}
    if not UPDATE_REPO:
        return info
    if _update["v"] is None or time.time() - _update["t"] > 6 * 3600:
        try:
            rel = json.loads(http_get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", timeout=10))
            _update["v"] = {"latest": str(rel.get("tag_name", "")).lstrip("v"), "url": rel.get("html_url")}
        except Exception:  # noqa: BLE001 - offline or rate-limited: just don't show anything
            _update["v"] = {"latest": None, "url": None}
        _update["t"] = time.time()
    info.update(_update["v"])
    info["newer"] = bool(info["latest"]) and _vtuple(info["latest"]) > _vtuple(APP_VERSION)
    info["job"] = _jobs.get("update")
    return info


def _run_job(what: str, fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - shown in the app instead of crashing the thread
        _job(what, state="failed", error=str(e) or "failed")


def _install_update() -> None:
    """Get Update: download the newest installer from the GitHub release, check its SHA-256 (GitHub lists one per
    file), then hand over to a hidden PowerShell that closes the app window, runs the installer quietly and lets
    it reopen CAD Parts (installer.iss: /RELAUNCH). The installer stops this server itself."""
    _job("update", state="running", step="Checking the newest version…", pct=None, error="")
    rel = json.loads(http_get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"))
    asset = next((a for a in rel.get("assets", []) if re.fullmatch(r"CAD-Parts-Setup-[\d.]+\.exe", a.get("name", ""))), None)
    if not asset:
        raise OSError("this release has no installer")
    dest = APP_DATA / "updates" / asset["name"]
    download(asset["browser_download_url"], dest, int(asset.get("size") or 0), "update", "the update")
    want = str(asset.get("digest") or "")
    if not want.startswith("sha256:"):
        raise OSError("the release doesn't list a checksum, so the download can't be checked")
    if _sha256(dest) != want[len("sha256:"):].lower():
        dest.unlink(missing_ok=True)
        raise OSError("the download didn't match the release's checksum")
    _job("update", step="Installing… CAD Parts will reopen by itself", pct=1.0)
    ps = ("Start-Sleep -Seconds 2; "
          "Get-Process -Name 'CAD Parts' -ErrorAction SilentlyContinue | ForEach-Object { [void]$_.CloseMainWindow() }; "
          "$t = 0; while ((Get-Process -Name 'CAD Parts' -ErrorAction SilentlyContinue) -and $t -lt 30) { Start-Sleep -Milliseconds 500; $t++ }; "
          "Get-Process -Name 'CAD Parts' -ErrorAction SilentlyContinue | Stop-Process -Force; "
          f"Start-Process -FilePath {_ps_lit(str(dest))} -ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/RELAUNCH' -Wait")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:  # outlive this server, which the installer stops
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=flags | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], creationflags=flags)
    _job("update", state="done")


DOWNLOADS = {"blender": "https://www.blender.org/download/", "bambu": "https://bambulab.com/en/download/studio"}

# ---------------------------------------------------------------- setup jobs: installs and sign-in, all without windows
# Each runs in the background; the setup screen polls /api/health and shows progress from _jobs.
_jobs: dict[str, dict] = {}  # what -> {"state": running|done|failed, "step": str, "pct": float|None, "error": str, "url": str}
_job_procs: dict[str, subprocess.Popen] = {}


def _job(what: str, **kw) -> None:
    _jobs.setdefault(what, {"state": "running", "step": "", "pct": None, "error": "", "url": ""}).update(kw)


# Downloads go through Windows' own curl.exe: it checks certificates with Windows itself, which on a brand-new PC
# (or Windows Sandbox) also fetches missing root certificates. Python's own TLS can't, and failed there with
# "CERTIFICATE_VERIFY_FAILED".
CURL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe")


def http_get(url: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run([CURL, "-fsSL", "--max-time", str(timeout), "-A", "cad-parts-app", url],
                           capture_output=True, timeout=timeout + 10)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        err = str(e)
    raise OSError(err or "download failed")


def download(url: str, dest: Path, total: int, what: str, label: str) -> None:
    """Download with an accurate percentage: curl writes the file, we watch it grow against the known size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    proc = subprocess.Popen([CURL, "-fsSL", "-A", "cad-parts-app", "-o", str(dest), url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    while proc.poll() is None:
        got = dest.stat().st_size if dest.exists() else 0
        _job(what, step=f"Downloading {label}… {got / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"Downloading {label}…",
             pct=min(got / total, 0.999) if total else None)
        time.sleep(0.3)
    if proc.returncode != 0:
        raise OSError((proc.stderr.read() or b"").decode("utf-8", "replace").strip() or "download failed")
    _job(what, pct=1.0)


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


CLAUDE_RELEASES = "https://downloads.claude.ai/claude-code-releases"


def _install_claude() -> None:
    """The same steps as Anthropic's official installer (claude.ai/install.ps1): latest version → its manifest →
    download claude.exe → check its SHA-256 against the manifest → `claude.exe install` (puts it in ~/.local/bin).
    Done here so the download can show real progress and nothing opens a window."""
    _job("claude", state="running", step="Finding the latest Claude Code…", pct=0.0, error="")
    try:
        version = http_get(f"{CLAUDE_RELEASES}/latest").strip()
        if not re.match(r"^\d+\.\d+\.\d+", version):
            raise OSError("Claude Code's download service didn't answer (is it available in your region?)")
        plat = "win32-arm64" if os.environ.get("PROCESSOR_ARCHITECTURE") == "ARM64" else "win32-x64"
        info = json.loads(http_get(f"{CLAUDE_RELEASES}/{version}/manifest.json"))["platforms"][plat]
        dest = Path.home() / ".claude" / "downloads" / f"claude-{version}-{plat}.exe"
        download(f"{CLAUDE_RELEASES}/{version}/{plat}/claude.exe", dest, int(info.get("size") or 0), "claude", "Claude Code")
        _job("claude", step="Checking the download…")
        if _sha256(dest) != str(info["checksum"]).lower():
            dest.unlink()
            raise OSError("the download didn't match its checksum; try again")
        _job("claude", step="Installing Claude Code…")
        r = subprocess.run([str(dest), "install", "latest"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        time.sleep(1)
        try:
            dest.unlink()
        except OSError:
            pass
        if find_claude():
            _job("claude", state="done", step="Installed")
        else:
            _job("claude", state="failed", error=((r.stderr or r.stdout or "").strip().splitlines() or ["the installer stopped"])[-1][:200])
    except Exception as e:  # noqa: BLE001
        _job("claude", state="failed", error=str(e)[:200])


def _install_git() -> None:
    """Git for Windows' own installer from its GitHub release: downloaded with progress, installed silently for this
    Windows user only (no admin prompt), to %LOCALAPPDATA%/Programs/Git."""
    _job("git", state="running", step="Finding the latest Git for Windows…", pct=0.0, error="")
    try:
        rel = json.loads(http_get("https://api.github.com/repos/git-for-windows/git/releases/latest"))
        asset = next(a for a in rel.get("assets", []) if re.fullmatch(r"Git-[\d.]+-64-bit\.exe", a.get("name", "")))
        dest = Path(os.environ.get("TEMP", str(Path.home()))) / asset["name"]
        download(asset["browser_download_url"], dest, int(asset.get("size") or 0), "git", "Git for Windows")
        _job("git", step="Installing Git for Windows…")
        subprocess.run([str(dest), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/SP-", "/CURRENTUSER"],
                       timeout=1200)
        try:
            dest.unlink()
        except OSError:
            pass
        if find_git_bash():
            _job("git", state="done", step="Installed")
        else:
            _job("git", state="failed", error="the Git installer finished but Git wasn't found")
    except StopIteration:
        _job("git", state="failed", error="couldn't find the Git installer on GitHub")
    except Exception as e:  # noqa: BLE001
        _job("git", state="failed", error=str(e)[:200])


def _sign_in() -> None:
    """`claude auth login`, hidden: it opens the browser itself and waits there; we keep its link for "open it again"."""
    exe = find_claude()
    old = _job_procs.pop("signin", None)
    if old and old.poll() is None:
        old.kill()
    _job("signin", state="running", step="Finish signing in in your browser", pct=None, error="", url="")
    try:
        proc = subprocess.Popen([exe, "auth", "login"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        _job_procs["signin"] = proc
        why = ""
        for line in proc.stdout:
            m = re.search(r"https://\S+", line)
            if m and not _jobs["signin"].get("url"):
                _job("signin", url=m.group(0))
            if "Couldn't" in line or "couldn't" in line or "error" in line.lower():
                why = line.split("> ")[-1].strip()[:200]
        proc.wait(timeout=1800)
        _auth_cache["v"] = None
        if claude_auth(force=True).get("loggedIn"):
            _job("signin", state="done", step="Signed in")
        elif _job_procs.get("signin") is proc:  # not replaced by a newer attempt
            _job("signin", state="failed", error=why or "the sign-in didn't finish")
    except Exception as e:  # noqa: BLE001
        _job("signin", state="failed", error=str(e)[:200])


def _open_signin_page() -> dict:
    url = (_jobs.get("signin") or {}).get("url") or ""
    host = urlparse(url).hostname or ""
    if url.startswith("https://") and (host == "claude.ai" or host.endswith((".claude.ai", ".anthropic.com", ".claude.com"))
                                       or host in ("anthropic.com", "claude.com")):
        os.startfile(url)
        return {"ok": True}
    return {"ok": False, "error": "no sign-in page to open"}


def _send_signin_code(code: str) -> dict:
    """Some sign-ins end on a page that shows a code to paste into Claude Code: the app passes it on."""
    proc = _job_procs.get("signin")
    code = re.sub(r"\s+", "", code or "")
    if not proc or proc.poll() is not None:
        return {"ok": False, "error": "start the sign-in again"}
    if not code or len(code) > 2000:
        return {"ok": False, "error": "paste the code from your browser"}
    try:
        proc.stdin.write(code + "\n")
        proc.stdin.flush()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    _job("signin", step="Checking the code…")
    return {"ok": True}


def setup_action(what: str, arg: str = "") -> dict:
    """The setup screen's buttons. Everything runs in the background with no windows; progress shows in the app."""
    starters = {"claude": _install_claude, "git": _install_git, "signin": _sign_in}
    if what in starters:
        if what == "signin" and not find_claude():
            return {"ok": False, "error": "install Claude Code first"}
        if (_jobs.get(what) or {}).get("state") == "running" and what != "signin":
            return {"ok": True}  # already on it
        _jobs.pop(what, None)
        threading.Thread(target=starters[what], daemon=True).start()
    elif what == "signin-page":
        return _open_signin_page()
    elif what == "signin-code":
        return _send_signin_code(arg)
    elif what == "build123d":
        threading.Thread(target=warm_worker, daemon=True).start()
    elif what == "update":
        if not update_info().get("newer"):
            return {"ok": False, "error": "no update found"}
        if (_jobs.get("update") or {}).get("state") != "running":
            _jobs.pop("update", None)
            threading.Thread(target=_run_job, args=("update", _install_update), daemon=True).start()
    elif what in DOWNLOADS:
        os.startfile(DOWNLOADS[what])  # the user's normal browser
    else:
        return {"ok": False, "error": "unknown"}
    return {"ok": True}
TOKEN = secrets.token_urlsafe(24)  # new every time the server starts; see Handler.blocked
OPEN_GETS = {"/api/ping", "/api/appicon"}  # harmless, and used where no key can be sent (launcher, <img>)
SKIP_DIRS = {"ready-to-print", "_sync", "_app", "_to-review"}
VERSION_RE = re.compile(r"^(?P<series>.+?)_v(?P<n>\d+)(?:_(?P<desc>.*))?$")
MODEL_EXT = {".stl", ".3mf"}


def parse_model(p: Path) -> dict:
    stem = p.stem
    if p.name.lower().endswith(".gcode.3mf"):
        stem = p.name[: -len(".gcode.3mf")]
    m = VERSION_RE.match(stem)
    st = p.stat()
    return {
        "file": p.name,
        "ext": p.suffix.lower(),
        "series": m["series"] if m else stem,
        "version": int(m["n"]) if m else None,
        "desc": (m["desc"] or "").replace("-", " ") if m else "",
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def notes_fields(text: str) -> dict:
    """Pull a couple of headline fields out of notes.md, if present."""
    out = {}
    for line in text.splitlines():
        s = line.strip().lstrip("-*# ").strip()
        low = s.lower()
        if "printer" not in out and low.startswith("printer"):
            val = s.split(":", 1)[-1].strip(" *") if ":" in s else s
            val = re.split(r"[.(;]| - | — ", val)[0].strip()  # just the printer, not the rest of the line
            out["printer"] = val[:32] or None
    if "printer" not in out:
        t = text.lower()
        if "photon" in t or "resin" in t:
            out["printer"] = "Photon Mono 4"
        elif "x2d" in t:
            out["printer"] = "X2D"
    return out


RESIN_EXT = (".pm4n", ".lys", ".ctb", ".photon", ".pwmx", ".pwma", ".pmx2")


def printer_from_files(other: list[str], models: list[dict]) -> dict:
    """When notes.md doesn't name a printer, infer it from slicer files in the folder (never a guess beyond that)."""
    names = [n.lower() for n in other] + [m["file"].lower() for m in models]
    if any(n.endswith(RESIN_EXT) for n in names):
        return {"printer": "Photon Mono 4"}
    if any(n.endswith(".3mf") or "bambu" in n or "x2d" in n for n in names):
        return {"printer": "Bambu X2D"}
    return {}


CHAT_FILE = "chat.json"  # each part's dashboard conversation, saved (hidden) in its folder


def scan() -> dict:
    if ROOT is None:
        return {"setup": True}
    merge_sync_copies()
    st = load_state()
    complete, favs, tags, cats = set(st["complete"]), set(st["favorites"]), st["tags"], st["categories"]
    parts = []
    for d in sorted(ROOT.iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir() or d.name.startswith(".") or d.name in SKIP_DIRS:
            continue
        models, other, latest = [], [], 0.0
        notes = ""
        for f in d.iterdir():
            if not f.is_file():
                continue
            low = f.name.lower()
            if low == CHAT_FILE or low.endswith(".tmp"):  # chat saves don't count as work on the part (keeps date sorting honest)
                continue
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            latest = max(latest, mt)
            if low == "notes.md":
                try:
                    notes = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
            elif f.suffix.lower() in MODEL_EXT and not low.endswith(".gcode.3mf"):
                models.append(parse_model(f))
            elif f.suffix.lower() != ".py":
                other.append(f.name)
        series: dict[str, list] = {}
        for mdl in models:
            series.setdefault(mdl["series"], []).append(mdl)
        for lst in series.values():
            lst.sort(key=lambda x: (x["version"] if x["version"] is not None else -1, x["mtime"]))
        # newest = highest version of whichever piece was touched last (file dates alone can lie)
        tops = [lst[-1] for lst in series.values()]
        newest = max(tops, key=lambda x: x["mtime"]) if tops else None
        parts.append({
            "name": d.name,
            "latest": latest or d.stat().st_mtime,
            "series": [{"name": k, "files": v} for k, v in sorted(series.items())],
            "versions": len({(m["series"], m["version"]) for m in models}),
            "newest": newest,
            "complete": d.name in complete,
            "fav": d.name in favs,
            "tags": tags.get(d.name, []),
            "category": cats.get(d.name, ""),
            "other": sorted(other),
            "notes": notes,
            **(notes_fields(notes) or printer_from_files(other, models)),
        })
    sig = (str(sum(p["latest"] for p in parts)) + str(sum(p["versions"] for p in parts)) + str(len(parts))
           + "|" + ",".join(sorted(complete)) + "|" + ",".join(sorted(favs))
           + "|" + json.dumps(tags, sort_keys=True) + "|" + json.dumps(cats, sort_keys=True))
    names = {p["name"] for p in parts}
    categories = sorted({c for n, c in cats.items() if c and n in names}, key=str.lower)
    return {"root": str(ROOT), "parts": parts, "categories": categories, "sig": sig, "now": time.time()}


NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PNS = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
_preview_cache: dict[tuple, bytes] = {}


def _xf(s):
    return [float(x) for x in s.split()] if s else None


def _apply(t, pts):
    if not t:
        return pts
    return [(x * t[0] + y * t[3] + z * t[6] + t[9],
             x * t[1] + y * t[4] + z * t[7] + t[10],
             x * t[2] + y * t[5] + z * t[8] + t[11]) for x, y, z in pts]


def threemf_to_stl(path: Path) -> bytes:
    """Flatten a 3MF (including Bambu Studio's split-object files) into one binary STL, for preview only."""
    key = (str(path), path.stat().st_mtime)
    if key in _preview_cache:
        return _preview_cache[key]
    z = zipfile.ZipFile(path)
    names = {n.lower(): n for n in z.namelist()}
    docs: dict[str, tuple] = {}

    def doc(name: str):
        name = names.get(name.lstrip("/").lower(), name.lstrip("/"))
        if name not in docs:
            root = ET.fromstring(z.read(name))
            docs[name] = ({o.get("id"): o for o in root.iter(NS + "object")}, root, name)
        return docs[name]

    tris = []

    def emit(docname, oid, chain, depth=0):
        if depth > 8:
            return
        objs, _, dname = doc(docname)
        o = objs.get(oid)
        if o is None:
            return
        mesh = o.find(NS + "mesh")
        if mesh is not None:
            vs = [(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in mesh.find(NS + "vertices")]
            for t in chain:
                vs = _apply(t, vs)
            for t in mesh.find(NS + "triangles"):
                tris.append((vs[int(t.get("v1"))], vs[int(t.get("v2"))], vs[int(t.get("v3"))]))
        comps = o.find(NS + "components")
        if comps is not None:
            for c in comps:
                emit(c.get(PNS + "path") or dname, c.get("objectid"), [_xf(c.get("transform"))] + chain, depth + 1)

    main = names.get("3d/3dmodel.model") or next(n for n in z.namelist() if n.lower().endswith(".model"))
    _, root, _ = doc(main)
    for item in root.find(NS + "build"):
        emit(main, item.get("objectid"), [_xf(item.get("transform"))])

    out = bytearray(b"cad-parts preview".ljust(80, b"\0"))
    out += struct.pack("<I", len(tris))
    for a, b, c in tris:
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        out += struct.pack("<12fH", nx / ln, ny / ln, nz / ln, *a, *b, *c, 0)
    data = bytes(out)
    _preview_cache[key] = data
    return data


# ---------------------------------------------------------------- build chat
# Claude Code runs headless here, so it can't stop to ask for permission. It gets
# only what CAD work needs (agreed with the user 2026-11-25):
#   * acceptEdits mode: create/edit files inside cad-parts only
#   * read/search files, build123d MCP tools, and `uv run ... build123d` scripts
# Everything else is denied automatically. Housekeeping that would need more
# (hiding scripts/notes, recycling old versions) is done by this server instead.
UV_PREFIX = "uv run --python 3.12 --with build123d"
_run_py = f"{APP_DIR.as_posix()}/run.py"
FAST_RUN = f"uv run --python 3.12 {chr(34) + _run_py + chr(34) if ' ' in _run_py else _run_py}"  # warm build123d worker: ~0.4 s per run instead of ~11 s
# (quoted when the install path has a space, e.g. a Windows user name like "John Smith")
# Speed: no MCP servers or claude.ai connectors in dashboard chats (they cost ~2 s per message and
# bloat the prompt); scripts run on the warm worker instead of the build123d-mcp server.
MCP_NONE = APP_DIR / "mcp-none.json"
FRAMES_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CADPartsFrames"  # written by cadworker.py
CHAT_TOOLS = "Read,Write,Edit,Glob,Grep,Bash"  # Bash = Git Bash on Windows (checked on the setup checklist)
BUILD_MARK = "[[building]]"  # Claude writes this when it's done asking and starts modelling (stripped from the chat)
ALLOWED_TOOLS = [
    "Read", "Glob", "Grep",
    f"Bash({FAST_RUN}:*)", f"PowerShell({FAST_RUN}:*)",
    f"Bash({UV_PREFIX}:*)", f"PowerShell({UV_PREFIX}:*)",
]
CHAT_PROMPT_BASE = f"""You are running headless inside the user's CAD Parts dashboard, a local web app with a chat panel next to a 3D viewer.
- You cannot ask for tool permissions. Only these work: reading/searching files, creating and editing files inside the parts folder (your working directory), and shell commands that start exactly with `{FAST_RUN}` (preferred) or `{UV_PREFIX} python`. Run commands from the parts folder; no `cd`, no `&&`. Anything else is denied. If a task needs more, say so briefly and suggest a regular Claude Code session.
- Run part scripts with `{FAST_RUN} <part>/<part>_vN.py`. It behaves like `python script.py` but on a warm worker with build123d already loaded (~0.4 s instead of ~11 s), so iterate freely: run, check the printed numbers, fix, and re-run. Have scripts print key dimensions or volumes so you can verify them without extra tools.
- Follow the project's CLAUDE.md and memory as usual (ask which printer for a new part, one folder per part, notes.md, versioned file names `<part>_vN_<desc>.stl`, STL only).
- ONE PART = ONE VERSION HISTORY. Every version you save is ONE model file, `<part>_vN_<desc>.stl`, containing ALL the pieces of that part laid out side by side on the build plate (not overlapping, each in its print orientation). Never start a second file series inside a part folder (no `<part>_hook_vN`, `<part>_cup_vN`, …): the app shows each part's versions as one timeline.
- If the user asks to split a piece off, make something separately, or a piece clearly becomes its own design, create a NEW part folder (`<new-part>/` with `<new-part>_v1_<desc>.py/.stl` and its own notes.md) instead of a new series in the current folder. Name it after the original (e.g. `sanding-holder-cup`) and say its name in your reply; the app files it next to the original automatically.
- The dashboard does these for you after every reply, so don't do them yourself: show the newest export in its 3D viewer, hide .py and notes.md files, and move older versions to the Recycle Bin (it keeps the newest few, a user setting). Don't open Blender unless asked.
- Never edit, create or delete anything in the dashboard's own code ({APP_DIR.as_posix()}) or in the `{META}` folder (its settings). If the dashboard seems broken (e.g. a model doesn't appear in the viewer), check that the STL was written, then tell the user plainly what you saw so they can get it fixed in a regular Claude Code session.
- Each message may start with a [Dashboard: ...] line saying which part and file the user is looking at; "this", "it" etc. usually mean that part.
- A message may end with "[Attached pictures: ...]" listing full paths of photos or sketches the user added. Open every one with the Read tool before answering and use what they show (shapes, sizes written on a sketch, the object a part must fit).
- Keep replies short and plain: the user reads them in a narrow chat panel.
- The dashboard animates the model while you build. As soon as you know you will NOT need to ask the user anything this turn and are about to start modelling, write the exact marker {BUILD_MARK} on its own line before anything else (it is hidden from the user and starts the animation). Never write it when you're going to ask a question."""


def chat_prompt() -> str:
    st = load_state()["settings"]
    printers = st.get("printers") or []
    if printers:
        line = "- The user's printers: " + "; ".join(f"{x['name']} ({x.get('type') or 'FDM'})" for x in printers) + "."
        line += (f" Default for new parts: {st['printer']}." if st.get("printer") else " Ask which one a new part is for.")
    else:
        line = "- The user hasn't listed their printers yet: ask which printer a new part is for."
    return CHAT_PROMPT_BASE + "\n" + line

_chat_lock = threading.Lock()
_chat_proc: subprocess.Popen | None = None


def _tool_summary(name: str, inp: dict) -> str:
    def fname(p):
        return Path(str(p)).name if p else ""
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return f"{'Wrote' if name == 'Write' else 'Edited'} {fname(inp.get('file_path'))}"
    if name == "Read":
        return f"Read {fname(inp.get('file_path'))}"
    if name in ("Glob", "Grep"):
        return f"Searched for {inp.get('pattern', '')}"
    if name in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        scripts = [s for s in re.findall(r"([\w\-.]+\.py)", cmd) if s != "run.py"]  # the part script, not the runner
        if (cmd.startswith(UV_PREFIX) or cmd.startswith(FAST_RUN)) and scripts:
            return f"Ran {scripts[-1]}"
        return inp.get("description") or ("Ran " + cmd[:70])
    if name.startswith("mcp__build123d-mcp__"):
        return "build123d: " + name.split("__")[-1].replace("_", " ")
    if name == "TodoWrite":
        return "Updated plan"
    return name


def _set_hidden(p: Path):
    try:
        import ctypes
        k = ctypes.windll.kernel32
        a = k.GetFileAttributesW(str(p))
        if a != -1 and not a & 0x2:
            k.SetFileAttributesW(str(p), a | 0x2)
    except Exception:  # noqa: BLE001
        pass


def housekeeping() -> list[str]:
    """Hide scripts/notes/chat and recycle STL versions beyond the newest N (setting, default 5) per series."""
    if ROOT is None:
        return []
    keep = load_state()["settings"]["keep"]
    kept = set(load_state().get("kept", []))  # versions restored from the Recycle Bin stay
    to_recycle = []
    for d in ROOT.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name in SKIP_DIRS:
            continue
        series: dict[str, dict[int, list[Path]]] = {}
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() == ".py" or f.name.lower() in ("notes.md", CHAT_FILE):
                _set_hidden(f)
            elif f.suffix.lower() == ".stl":
                m = VERSION_RE.match(f.stem)
                if m:
                    series.setdefault(m["series"], {}).setdefault(int(m["n"]), []).append(f)
        for vers in series.values():
            for n in sorted(vers)[:-keep]:
                to_recycle += [f for f in vers[n] if f"{d.name}/{f.name}" not in kept]
    recycle(to_recycle)
    return [f.name for f in to_recycle if not f.exists()]


def recycle(paths) -> None:
    """Send files or folders to the Windows Recycle Bin (never a permanent delete)."""
    paths = [str(p) for p in paths if Path(p).exists()]
    if not paths:
        return
    # Paths are passed as single-quoted PowerShell literals inside the script itself: with -Command,
    # extra process arguments are NOT handed over as $args (they get glued onto the script text).
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; foreach ($f in @(" + quoted + ")) { "
          "if (Test-Path -LiteralPath $f -PathType Container) { "
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($f,'OnlyErrorDialogs','SendToRecycleBin') } else { "
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($f,'OnlyErrorDialogs','SendToRecycleBin') } }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)


# Dashboard state for one parts folder, kept in <parts>\.cadparts\state.json so it travels with the folder
# (OneDrive: the laptop sees the same favorites, tags and settings). "complete" is a retired label.
def state_file() -> Path:
    return ROOT / META / "state.json"


PRINTER_TYPES = ("FDM", "Resin")
_state_lock = threading.Lock()


def load_state() -> dict:
    try:
        st = json.loads(state_file().read_text(encoding="utf-8")) if ROOT else {}
    except (OSError, ValueError):
        st = {}
    st.setdefault("complete", [])
    st.setdefault("favorites", [])
    st.setdefault("tags", {})
    st.setdefault("categories", {})
    s = st.setdefault("settings", {})
    s.setdefault("model", st.get("model") or "")  # "" = Claude Code's default
    try:
        s["keep"] = max(1, min(50, int(s.get("keep") or 5)))
    except (TypeError, ValueError):
        s["keep"] = 5
    s.setdefault("printer", "")  # "" = ask each time
    s.setdefault("notify", True)
    s["printers"] = clean_printers(s.get("printers"))
    return st


def clean_printers(v) -> list[dict]:
    out = []
    for x in v if isinstance(v, list) else []:
        name = re.sub(r"\s+", " ", str((x or {}).get("name", ""))).strip()[:48] if isinstance(x, dict) else ""
        if name and name.lower() not in [o["name"].lower() for o in out]:
            out.append({"name": name, "type": x.get("type") if x.get("type") in PRINTER_TYPES else "FDM"})
    return out[:12]


def save_state(st: dict) -> None:
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def update_state(fn) -> dict:
    with _state_lock:
        st = load_state()
        fn(st)
        save_state(st)
        return st


def merge_sync_copies() -> list[str]:
    """Two PCs on one OneDrive folder: if both changed state.json before syncing, OneDrive keeps both and renames one
    (state-LAPTOP.json, state (1).json). Fold those copies back in (favorites, kept versions, categories, printers
    from either PC; this PC's settings win) and send the copy to the Recycle Bin. Returns the merged file names."""
    if ROOT is None:
        return []
    copies = [f for f in (ROOT / META).glob("state*.json") if f.name != "state.json"]
    if not copies:
        return []
    merged = []
    with _state_lock:
        st = load_state()
        for f in copies:
            try:
                other = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for key in ("favorites", "complete", "kept"):
                st[key] = sorted(set(st.get(key, [])) | set(other.get(key) or []))
            for key in ("categories", "tags"):
                for k, v in (other.get(key) or {}).items():
                    st.setdefault(key, {}).setdefault(k, v)
            names = {x["name"].lower() for x in st["settings"]["printers"]}
            extra = [x for x in clean_printers((other.get("settings") or {}).get("printers")) if x["name"].lower() not in names]
            st["settings"]["printers"] = (st["settings"]["printers"] + extra)[:12]
            merged.append(f)
        if merged:
            save_state(st)
    recycle(merged)
    return [f.name for f in merged]


def _ps_lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def trash_list() -> list[dict]:
    """Items in the Windows Recycle Bin that were deleted from cad-parts (newest first)."""
    ps = r"""
$root = %ROOT%
$sh = New-Object -ComObject Shell.Application
$out = foreach ($it in $sh.NameSpace(10).Items()) {
  $from = [string]$it.ExtendedProperty('System.Recycle.DeletedFrom')
  if (-not $from -or -not $from.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { continue }
  $name = $it.Name; $ext = [IO.Path]::GetExtension($it.Path)
  if (-not $it.IsFolder -and $ext -and -not $name.EndsWith($ext, [StringComparison]::OrdinalIgnoreCase)) { $name += $ext }
  $when = $it.ExtendedProperty('System.Recycle.DateDeleted')
  [pscustomobject]@{ name = $name; from = $from; folder = [bool]$it.IsFolder; id = $it.Path;
                     deleted = $(if ($when) { ([DateTime]$when).ToString('yyyy-MM-ddTHH:mm:ss') + 'Z' } else { '' }) }  # Windows gives this in UTC already
}
@($out) | ConvertTo-Json -Compress -Depth 3
""".replace("%ROOT%", _ps_lit(str(ROOT)))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    items = items if isinstance(items, list) else [items]
    for it in items:
        rel = os.path.relpath(it["from"], ROOT)
        it["part"] = None if rel == "." else rel.split(os.sep)[0]  # which part it belonged to (None = a whole part)
    return sorted(items, key=lambda x: x.get("deleted", ""), reverse=True)


def trash_restore(item_id: str) -> dict:
    """Move one Recycle Bin item back to where it was deleted from (only items that came from cad-parts)."""
    ps = r"""
$root = %ROOT%; $id = %ID%
$sh = New-Object -ComObject Shell.Application
$it = @($sh.NameSpace(10).Items() | Where-Object { $_.Path -eq $id })[0]
if (-not $it) { 'gone'; exit }
$from = [string]$it.ExtendedProperty('System.Recycle.DeletedFrom')
if (-not $from.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { 'outside'; exit }
$name = $it.Name; $ext = [IO.Path]::GetExtension($it.Path)
if (-not $it.IsFolder -and $ext -and -not $name.EndsWith($ext, [StringComparison]::OrdinalIgnoreCase)) { $name += $ext }
if (Test-Path -LiteralPath (Join-Path $from $name)) { 'exists'; exit }
New-Item -ItemType Directory -Force -Path $from | Out-Null
$sh.NameSpace($from).MoveHere($it, 0x14)
Start-Sleep -Milliseconds 400
if (Test-Path -LiteralPath (Join-Path $from $name)) { Join-Path $from $name; 'ok' } else { 'failed' }
""".replace("%ROOT%", _ps_lit(str(ROOT))).replace("%ID%", _ps_lit(item_id))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    lines = (r.stdout or "").strip().splitlines()
    res = lines[-1:] or ["failed"]
    if res[0] == "ok" and len(lines) >= 2 and ROOT:  # a version file brought back on purpose: housekeeping keeps it
        try:
            rel = Path(lines[-2]).resolve().relative_to(ROOT.resolve())
            if len(rel.parts) == 2 and rel.suffix.lower() in MODEL_EXT:
                update_state(lambda st: st.setdefault("kept", []).append(rel.as_posix()))
        except (ValueError, OSError):
            pass
    msg = {"ok": None, "gone": "it's no longer in the Recycle Bin", "outside": "not a cad-parts item",
           "exists": "something with that name is already back in its folder", "failed": "Windows couldn't restore it"}
    return {"ok": res[0] == "ok", "error": msg.get(res[0], "Windows couldn't restore it")}


def delete_item(part: str, file: str | None) -> dict:
    """Send a whole part folder, or one model file in it, to the Recycle Bin (never a permanent delete)."""
    if not part or "/" in part or "\\" in part or part.startswith((".", "_")) or part in SKIP_DIRS:
        return {"ok": False, "error": "not a part"}
    d = ROOT / part
    if not d.is_dir() or d.resolve().parent != ROOT.resolve():
        return {"ok": False, "error": "unknown part"}
    if file:
        f = d / file
        if "/" in file or "\\" in file or not f.is_file() or f.suffix.lower() not in MODEL_EXT:
            return {"ok": False, "error": "unknown file"}
        recycle([f])
        return {"ok": not f.exists()}
    recycle([d])
    if d.exists():
        return {"ok": False, "error": "couldn't move it to the Recycle Bin (is a file open somewhere?)"}
    # its favorite star and tags are kept in state.json, so a restore from the Recycle Bin brings them back
    return {"ok": True}


def set_complete(part: str, on: bool) -> dict:
    if not part or "/" in part or "\\" in part or not (ROOT / part).is_dir():
        return {"ok": False, "error": "unknown part"}
    with _state_lock:
        st = load_state()
        done = set(st["complete"])
        (done.add if on else done.discard)(part)
        st["complete"] = sorted(done)
        save_state(st)
    return {"ok": True, "sig": scan()["sig"]}


# ---------------------------------------------------------------- parts: rename / duplicate / meta
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


def clean_name(s: str) -> str:
    """'Caliper Holder (v2)' -> 'caliper-holder-v2' (the folder style used everywhere here)."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", str(s).lower())).strip("-")[:60]


def part_dir(name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name.startswith((".", "_")) or name in SKIP_DIRS:
        return None
    d = ROOT / name
    return d if d.is_dir() and d.resolve().parent == ROOT.resolve() else None


def _retag_files(d: Path, old: str, new: str, files=None) -> None:
    """Rename `<old>_...` files in d to `<new>_...`, and point scripts/notes at the new name."""
    for f in list(files if files is not None else d.iterdir()):
        if not f.is_file() or f.name.lower() == CHAT_FILE:
            continue
        target = f
        if f.name.startswith(old + "_") or f.stem == old:
            target = f.with_name(new + f.name[len(old):])
            f.rename(target)
        if target.suffix.lower() == ".py" or target.name.lower() == "notes.md":
            try:
                txt = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if old in txt:
                hidden = _is_hidden(target)
                if hidden:  # Windows won't open a hidden file for overwriting
                    _set_attr(target, hidden=False)
                target.write_text(txt.replace(old, new), encoding="utf-8")
                if hidden:
                    _set_hidden(target)


def _is_hidden(p: Path) -> bool:
    try:
        import ctypes
        a = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        return a != -1 and bool(a & 0x2)
    except Exception:  # noqa: BLE001
        return False


def _set_attr(p: Path, hidden: bool) -> None:
    try:
        import ctypes
        k = ctypes.windll.kernel32
        a = k.GetFileAttributesW(str(p))
        if a != -1:
            k.SetFileAttributesW(str(p), (a | 0x2) if hidden else (a & ~0x2))
    except Exception:  # noqa: BLE001
        pass


def _move_meta(old: str, new: str, copy: bool = False) -> None:
    def fn(st):
        if old in st["favorites"] and new not in st["favorites"]:
            st["favorites"].append(new)
        if old in st["tags"]:
            st["tags"][new] = list(st["tags"][old])
        if old in st["categories"]:
            st["categories"][new] = st["categories"][old]
        if not copy:
            st["favorites"] = [n for n in st["favorites"] if n != old]
            st["tags"].pop(old, None)
            st["categories"].pop(old, None)
            st["complete"] = [new if n == old else n for n in st["complete"]]
    update_state(fn)


def rename_part(old: str, to: str) -> dict:
    d, new = part_dir(old), clean_name(to)
    if not d:
        return {"ok": False, "error": "unknown part"}
    if not NAME_OK.match(new) or new in SKIP_DIRS:
        return {"ok": False, "error": "use letters, numbers and dashes"}
    if new == old:
        return {"ok": True, "name": new}
    if (ROOT / new).exists():
        return {"ok": False, "error": f"there's already a part called {new}"}
    if _chat_lock.locked():
        return {"ok": False, "error": "wait until Claude has finished"}
    try:
        d = d.rename(ROOT / new)
        _retag_files(d, old, new)
    except OSError as e:
        return {"ok": False, "error": f"Windows wouldn't rename it (is a file open somewhere?) {e.strerror or ''}".strip()}
    _move_meta(old, new)
    return {"ok": True, "name": new}


def duplicate_part(src: str) -> dict:
    """New part from an existing one: its newest version of each piece (+ the scripts that made them) and notes."""
    d = part_dir(src)
    if not d:
        return {"ok": False, "error": "unknown part"}
    base = clean_name(src + "-copy")
    new, i = base, 2
    while (ROOT / new).exists():
        new, i = f"{base}-{i}", i + 1
    newest: dict[str, tuple[int, str]] = {}
    for f in d.iterdir():
        m = VERSION_RE.match(f.stem) if f.is_file() and f.suffix.lower() in MODEL_EXT else None
        if m and int(m["n"]) >= newest.get(m["series"], (-1, ""))[0]:
            newest[m["series"]] = (int(m["n"]), f.name)
    keep = {name for _, name in newest.values()}
    stems = {f"{s}_v{n}" for s, (n, _) in newest.items()}
    nd = ROOT / new
    nd.mkdir()
    copied = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        m = VERSION_RE.match(f.stem)
        script = f.suffix.lower() == ".py" and m and f"{m['series']}_v{m['n']}" in stems
        if f.name in keep or script or f.name.lower() == "notes.md":
            copied.append(Path(shutil.copy2(f, nd / f.name)))
    _retag_files(nd, src, new, copied)
    _move_meta(src, new, copy=True)
    return {"ok": True, "name": new}


def clean_category(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:32]


def edit_category(body: dict) -> dict:
    """{"rename": old, "to": new} or {"remove": name}: applies to every part in that category."""
    def fn(st):
        cats = st["categories"]
        if body.get("rename"):
            old, new = clean_category(body["rename"]), clean_category(body.get("to"))
            if new:
                same = next((x for x in cats.values() if x.lower() == new.lower() and x.lower() != old.lower()), None)
                for k, v in list(cats.items()):
                    if v.lower() == old.lower():
                        cats[k] = same or new
        elif body.get("remove"):
            gone = clean_category(body["remove"]).lower()
            for k, v in list(cats.items()):
                if v.lower() == gone:
                    cats.pop(k)
    update_state(fn)
    return {"ok": True, "sig": scan()["sig"]}


def set_meta(name: str, body: dict) -> dict:
    if not part_dir(name):
        return {"ok": False, "error": "unknown part"}

    def fn(st):
        if "fav" in body:
            st["favorites"] = [n for n in st["favorites"] if n != name] + ([name] if body["fav"] else [])
        if "category" in body:
            c = clean_category(body["category"])
            if c:
                # reuse an existing category's spelling when only the capitalisation differs
                same = next((x for x in st["categories"].values() if x.lower() == c.lower()), None)
                st["categories"][name] = same or c
            else:
                st["categories"].pop(name, None)
        if "tags" in body:
            tags = []
            for t in body["tags"] or []:
                t = re.sub(r"\s+", " ", str(t)).strip()[:24]
                if t and t.lower() not in [x.lower() for x in tags]:
                    tags.append(t)
            if tags:
                st["tags"][name] = tags
            else:
                st["tags"].pop(name, None)
    update_state(fn)
    return {"ok": True, "sig": scan()["sig"]}


# ---------------------------------------------------------------- profiles (who uses this PC, where their parts are)
def load_profiles() -> dict:
    try:
        d = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("active", None)
    d.setdefault("profiles", [])
    return d


def save_profiles(d: dict) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    tmp = PROFILES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, PROFILES_FILE)


def count_parts(folder: Path) -> int:
    try:
        return sum(1 for d in folder.iterdir() if d.is_dir() and not d.name.startswith((".", "_")) and d.name not in SKIP_DIRS)
    except OSError:
        return 0


STARTER_CLAUDE_MD = """# CAD Parts

3D-printable parts made with build123d through the CAD Parts app. One folder per part.

## Printers
{printers}

## Conventions
- One folder per part, named in lowercase-with-dashes (`phone-stand/`).
- Versioned files: `<part>_vN_<short-desc>.py` makes `<part>_vN_<short-desc>.stl`. Never overwrite an old version; save the next number.
- One part = one version history: each version is ONE file with all of the part's pieces laid out side by side. Never start a second file series in a part folder.
- To split a piece off or make it separately, create a new part folder (named after the original) with its own versions.
- Keep `<part>/notes.md` up to date: printer, key dimensions, decisions and open questions. Read it when coming back to a part.
- Export STL only unless asked for something else.
- Don't model supports: the slicer adds them.
- Resin parts: thicken any feature too thin to print reliably.
"""


def prepare_folder(root: Path, printers: list[dict]) -> None:
    """Create the parts folder (and its hidden .cadparts), with a starter CLAUDE.md if it's a fresh folder."""
    root.mkdir(parents=True, exist_ok=True)
    meta = root / META
    meta.mkdir(exist_ok=True)
    _set_hidden(meta)
    if not (root / "CLAUDE.md").exists() and count_parts(root) == 0:
        lines = "\n".join(f"- {x['name']} ({x['type']})" for x in printers) or "- (none listed yet: ask which printer a part is for)"
        (root / "CLAUDE.md").write_text(STARTER_CLAUDE_MD.format(printers=lines), encoding="utf-8")


def activate_profile(pid: str | None) -> bool:
    global ROOT
    d = load_profiles()
    prof = next((x for x in d["profiles"] if x["id"] == pid), None)
    if not prof:
        ROOT = None
        return False
    folder = Path(prof["folder"])
    if not folder.is_dir():  # e.g. OneDrive not synced yet, or a drive that's unplugged: don't recreate it empty
        ROOT = None
        return False
    try:
        (folder / META).mkdir(exist_ok=True)
        _set_hidden(folder / META)
    except OSError:
        ROOT = None
        return False
    ROOT = folder
    if d["active"] != pid:
        d["active"] = pid
        save_profiles(d)
    stamp_owner(folder, prof.get("name", ""))
    return True


OWNER_FILE = "owner.json"  # in .cadparts: {"name", "account"} so a new PC can recognise its owner's folder


def read_owner(folder: Path) -> dict:
    try:
        o = json.loads((folder / META / OWNER_FILE).read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else {}
    except (OSError, ValueError):
        return {}


def stamp_owner(folder: Path | None = None, name: str = "") -> None:
    """Remember which Claude account (fingerprint) and name this parts folder belongs to. A folder that already
    belongs to another account is left alone."""
    folder = folder or ROOT
    acct = _account["id"]
    if not folder or not acct or not (folder / META).is_dir():
        return
    o = read_owner(folder)
    if o.get("account") and o["account"] != acct:
        return
    if not name:
        prof = next((x for x in load_profiles()["profiles"]
                     if os.path.realpath(x["folder"]).lower() == os.path.realpath(folder).lower()), None)
        name = (prof or {}).get("name") or o.get("name") or ""
    if o.get("account") == acct and o.get("name") == name:
        return
    try:
        (folder / META / OWNER_FILE).write_text(json.dumps({"name": name, "account": acct}, indent=2), encoding="utf-8")
    except OSError:
        pass


def sync_roots() -> list[Path]:
    """Where a parts folder can live: the sync services' folders on this PC (OneDrive, Dropbox, Google Drive,
    iCloud Drive, Box), plus Documents, Desktop and the home folder."""
    home, roots = Path.home(), []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    for info in (Path(os.environ.get("APPDATA", "")) / "Dropbox" / "info.json",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Dropbox" / "info.json"):
        try:
            roots += [Path(v["path"]) for v in json.loads(info.read_text(encoding="utf-8")).values() if v.get("path")]
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    roots += [home / "Dropbox", home / "Google Drive", home / "My Drive", home / "iCloudDrive", home / "Box"]
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":  # Google Drive for desktop shows up as a drive with "My Drive" in it
        roots.append(Path(f"{letter}:\\My Drive"))
    roots += [_known_folder(5) or home / "Documents", _known_folder(16) or home / "Desktop", home]
    out, seen = [], set()
    for r in roots:
        try:
            if r and r.is_dir():
                key = os.path.realpath(r).lower()
                if key not in seen:
                    seen.add(key); out.append(r)
        except OSError:
            pass
    return out


_found_cache = {"t": 0.0, "v": []}


def find_parts_folders(max_dirs: int = 600) -> list[dict]:
    """Every CAD Parts folder (one with a .cadparts folder) up to two levels inside sync_roots() (cached a minute)."""
    if time.time() - _found_cache["t"] < 60:
        return _found_cache["v"]
    found, seen = [], set()

    def look(d: Path):
        key = os.path.realpath(d).lower()
        if key in seen:
            return
        seen.add(key)
        if (d / META).is_dir():
            o = read_owner(d)
            found.append({"folder": str(d), "name": o.get("name", ""), "account": o.get("account", ""), "parts": count_parts(d)})

    for root in sync_roots():
        budget = max_dirs
        try:
            level1 = [x for x in root.iterdir() if x.is_dir() and not x.name.startswith((".", "$"))]
        except OSError:
            continue
        for d1 in level1[:budget]:
            look(d1)
            if (d1 / META).is_dir():
                continue
            try:
                kids = [x for x in d1.iterdir() if x.is_dir() and not x.name.startswith((".", "$"))]
            except OSError:
                continue
            budget -= len(kids)
            for d2 in kids[:max(0, budget)]:
                look(d2)
    _found_cache.update(t=time.time(), v=found)
    return found


def find_my_folder() -> dict | None:
    """After signing in on a new PC: the parts folder that belongs to this Claude account, if one is here."""
    acct = account_id()
    if not acct:
        return None
    taken = {os.path.realpath(x["folder"]).lower() for x in load_profiles()["profiles"]}
    mine = [f for f in find_parts_folders() if f["account"] == acct and os.path.realpath(f["folder"]).lower() not in taken]
    mine.sort(key=lambda f: -f["parts"])
    return {k: mine[0][k] for k in ("folder", "name", "parts")} if mine else None


# ---------------------------------------------------------------- Export My Parts / import a CAD Parts file
EXPORT_MANIFEST = "cadparts-export.json"
HIDE_ON_IMPORT = {".py", ".md", ".json"}  # scripts, notes and chats are kept hidden in part folders


def _export_members(root: Path) -> list[Path]:
    files = [f for f in root.iterdir() if f.is_file() and f.suffix.lower() == ".md"]  # CLAUDE.md and other notes
    for d in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if d.is_dir() and (d.name == META or (not d.name.startswith((".", "_")) and (d.name not in SKIP_DIRS or d.name == "ready-to-print"))):
            files += [f for f in d.rglob("*") if f.is_file() and not f.name.endswith(".tmp")]
    return files


def export_parts(dest: str, name: str) -> None:
    """One .zip with every part (all versions, scripts, notes, chats), ready-to-print, and the app's data for this
    folder (favorites, categories, settings, printers, pictures), plus a small manifest for importing."""
    _job("export", state="running", step="Packing your parts…", pct=0.0, error="", url="")
    root = ROOT
    files = _export_members(root)
    out = Path(dest)
    tmp = out.with_name(out.name + ".partial")
    st = load_state()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr(EXPORT_MANIFEST, json.dumps({"app": "CAD Parts", "version": APP_VERSION, "name": name,
                                                "parts": count_parts(root), "printers": st["settings"]["printers"],
                                                "printer": st["settings"]["printer"],
                                                "exported": time.strftime("%Y-%m-%d %H:%M")}, indent=2))
        for i, f in enumerate(files):
            z.write(f, f.relative_to(root).as_posix())
            if i % 20 == 0:
                _job("export", pct=i / max(1, len(files)))
    os.replace(tmp, out)
    _job("export", state="done", pct=1.0, url=str(out))


def peek_export(path: str) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            m = json.loads(z.read(EXPORT_MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {"ok": False, "error": "that isn't a CAD Parts file"}
    return {"ok": True, "name": str(m.get("name") or ""), "parts": int(m.get("parts") or 0),
            "printers": clean_printers(m.get("printers")), "printer": str(m.get("printer") or ""),
            "exported": str(m.get("exported") or "")}


def import_parts(path: str, folder: Path) -> dict:
    """Unpack a CAD Parts file into `folder`. Parts the folder already has are left alone (reported back)."""
    folder.mkdir(parents=True, exist_ok=True)
    have = {d.name.lower() for d in folder.iterdir() if d.is_dir()}
    skipped, n = set(), 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            parts = Path(name).parts
            if info.is_dir() or name == EXPORT_MANIFEST or not parts or Path(name).is_absolute() or ".." in parts or ":" in name:
                continue
            top = parts[0]
            if len(parts) > 1 and top != META and top.lower() in have:
                skipped.add(top); continue
            target = folder / Path(*parts)
            if top == META and target.exists():
                continue  # this folder's own app data wins
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            mt = time.mktime(info.date_time + (0, 0, -1))  # keep the parts' dates (the list is sorted by them)
            os.utime(target, (mt, mt))
            if len(parts) == 2 and top != META and (target.suffix.lower() in HIDE_ON_IMPORT):
                _set_hidden(target)
            n += 1
    if (folder / META).is_dir():
        _set_hidden(folder / META)
    return {"files": n, "skipped": sorted(skipped)}


def pick_file(start: str = "") -> str | None:
    """Windows' file picker for a CAD Parts file (when the app window can't show its own)."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.OpenFileDialog;"
          "$f.Title = 'Choose your CAD Parts file'; $f.Filter = 'CAD Parts file (*.zip)|*.zip';"
          "$f.InitialDirectory = " + _ps_lit(start or str(Path.home() / "Downloads")) + ";"
          "$o = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false};"
          "if ($f.ShowDialog($o) -eq 'OK') { $f.FileName }")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, timeout=600)
    out = (r.stdout or "").strip().splitlines()
    return out[-1] if out else None


def _known_folder(csidl: int) -> Path | None:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
            return Path(buf.value)
    except Exception:  # noqa: BLE001
        pass
    return None


def detect_folders() -> dict:
    """Suggestions for the setup screen: OneDrive, this-PC-only, and existing parts folders."""
    home = Path.home()
    od = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or os.environ.get("OneDriveCommercial")
    onedrive = Path(od) if od and Path(od).is_dir() else None
    docs = _known_folder(5) or home / "Documents"  # CSIDL_PERSONAL
    in_onedrive = onedrive is not None and str(docs).lower().startswith(str(onedrive).lower())
    local = (home if in_onedrive else docs) / "CAD Parts"  # Documents may itself be synced by OneDrive
    cands = []
    for base in [onedrive, docs, home]:
        if base:
            cands += [base / "cad-parts", base / "CAD Parts"]
    cands += [Path(f["folder"]) for f in find_parts_folders()]  # Dropbox, Google Drive, iCloud… too
    taken = {os.path.realpath(x["folder"]).lower() for x in load_profiles()["profiles"]}
    existing, seen = [], set()
    for c in cands:
        key = os.path.realpath(c).lower()  # a junction/shortcut to the same folder counts once
        if key in seen or not c.is_dir():
            continue
        seen.add(key)
        existing.append({"folder": str(c), "parts": count_parts(c), "used": key in taken})
    return {"onedrive": str(onedrive / "CAD Parts") if onedrive else None, "local": str(local), "existing": existing}


def folder_info(path: str) -> dict:
    f = Path(path)
    info = {"folder": str(f), "exists": f.is_dir(), "parts": count_parts(f) if f.is_dir() else 0, "printers": [], "printer": ""}
    try:
        st = json.loads((f / META / "state.json").read_text(encoding="utf-8")).get("settings", {})
        info["printers"], info["printer"] = clean_printers(st.get("printers")), st.get("printer") or ""
    except (OSError, ValueError):
        pass
    return info


def pick_folder(start: str = "") -> str | None:
    """Windows' own folder picker (works from the app window and from a browser)."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$f.Description = 'Choose the folder where CAD Parts keeps your parts'; $f.UseDescriptionForTitle = $true;"
          "$f.ShowNewFolderButton = $true; $f.SelectedPath = " + _ps_lit(start) + ";"
          "$o = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false};"
          "if ($f.ShowDialog($o) -eq 'OK') { $f.SelectedPath }")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, timeout=600)
    out = (r.stdout or "").strip().splitlines()
    return out[-1] if out else None


def save_profile(body: dict) -> dict:
    name = re.sub(r"\s+", " ", str(body.get("name", ""))).strip()[:40]
    folder = str(body.get("folder", "")).strip().strip('"')
    if not name:
        return {"ok": False, "error": "enter a name"}
    if not folder or not Path(folder).is_absolute():
        return {"ok": False, "error": "choose a folder"}
    if _chat_lock.locked():
        return {"ok": False, "error": "wait until Claude has finished"}
    d = load_profiles()
    pid = body.get("id") or None
    prof = next((x for x in d["profiles"] if x["id"] == pid), None)
    if any(os.path.realpath(x["folder"]).lower() == os.path.realpath(folder).lower() and x is not prof for x in d["profiles"]):
        return {"ok": False, "error": "another profile already uses that folder"}
    printers = clean_printers(body.get("printers"))
    imported = None
    if body.get("import"):
        if not peek_export(str(body["import"])).get("ok"):
            return {"ok": False, "error": "that isn't a CAD Parts file"}
        try:
            imported = import_parts(str(body["import"]), Path(folder))
        except (OSError, zipfile.BadZipFile) as e:
            return {"ok": False, "error": f"couldn't unpack it ({getattr(e, 'strerror', None) or e})"}
    try:
        prepare_folder(Path(folder), printers)
    except OSError as e:
        return {"ok": False, "error": f"couldn't use that folder ({e.strerror or e})"}
    if prof is None:
        prof = {"id": f"p{int(time.time() * 1000):x}"}
        d["profiles"].append(prof)
    prof.update(name=name, folder=folder)
    save_profiles(d)
    if not activate_profile(prof["id"]):
        return {"ok": False, "error": "couldn't open that folder"}
    default = str(body.get("printer", ""))

    def fn(st):
        if "printers" in body:
            st["settings"]["printers"] = printers
            st["settings"]["printer"] = default if default in [x["name"] for x in printers] else ""
    update_state(fn)
    return {"ok": True, "id": prof["id"], **({"imported": imported} if imported else {})}


def remove_profile(pid: str) -> dict:
    """Forget a profile (its parts folder and files are left exactly as they are)."""
    if _chat_lock.locked():
        return {"ok": False, "error": "wait until Claude has finished"}
    d = load_profiles()
    d["profiles"] = [x for x in d["profiles"] if x["id"] != pid]
    if d["active"] == pid:
        d["active"] = d["profiles"][0]["id"] if d["profiles"] else None
    save_profiles(d)
    activate_profile(d["active"])
    return {"ok": True}


# ---------------------------------------------------------------- chats saved on disk
def chat_path(key: str) -> Path | None:
    if ROOT is None:
        return None
    if key == "new":
        return ROOT / META / "chats" / "new.json"
    if key.startswith("part:"):
        d = part_dir(key[5:])
        return d / CHAT_FILE if d else None
    return None


def load_chats() -> dict:
    out = {}
    if ROOT is None:
        return out
    for d in ROOT.iterdir():
        f = d / CHAT_FILE
        if d.is_dir() and f.is_file():
            try:
                out["part:" + d.name] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    f = chat_path("new")
    if f.is_file():
        try:
            out["new"] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return out


def save_chat(key: str, chat: dict) -> dict:
    p = chat_path(key)
    if not p:
        return {"ok": False, "error": "no such part"}
    p.parent.mkdir(parents=True, exist_ok=True)
    if not chat.get("msgs") and not chat.get("session"):
        if p.exists():  # cleared: no file at all
            p.unlink()
        return {"ok": True}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
    if p.exists():
        _set_attr(p, hidden=False)  # os.replace can't overwrite a hidden file on Windows
    os.replace(tmp, p)
    if key != "new":
        _set_hidden(p)
    return {"ok": True}


# ---------------------------------------------------------------- photo / sketch attachments
def uploads_dir() -> Path:
    return ROOT / META / "uploads"  # inside the parts folder, so Claude (working there) can open them


IMG_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def save_upload(data: bytes, ctype: str) -> dict:
    ext = IMG_TYPES.get(ctype.split(";")[0].strip().lower())
    if not ext:
        return {"ok": False, "error": "only PNG, JPEG, WebP or GIF pictures"}
    if len(data) > 20 * 1024 * 1024:
        return {"ok": False, "error": "picture is over 20 MB"}
    if ROOT is None:
        return {"ok": False, "error": "finish the setup first"}
    up = uploads_dir()
    up.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}{ext}"
    (up / name).write_bytes(data)
    return {"ok": True, "name": name, "url": "/uploads/" + name, "path": str(up / name)}


# ---------------------------------------------------------------- "build finished" notification
def notify(title: str, body: str) -> None:
    """A Windows toast (shown even when the dashboard is in the background)."""
    xml = ("<toast><visual><binding template='ToastGeneric'><text>{}</text><text>{}</text></binding></visual>"
           "<audio src='ms-winsoundevent:Notification.Default'/></toast>")
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;")[:200]  # noqa: E731
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null;"
          "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml(" + _ps_lit(xml.format(esc(title), esc(body))) + ");"
          "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
          "[Windows.UI.Notifications.ToastNotification]::new($x))")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _file_new_parts(origin: str, new_parts: list[str]) -> str:
    """Parts split off during a chat about `origin` join origin's category (one named after it if it has none)."""
    if not origin or not new_parts or not part_dir(origin):
        return ""
    st = load_state()
    cat = st["categories"].get(origin) or " ".join(w.capitalize() for w in re.split(r"[-_\s]+", origin) if w)

    def fn(st):
        st["categories"].setdefault(origin, cat)
        for n in new_parts:
            st["categories"][n] = cat
    update_state(fn)
    return cat


def run_chat(handler, message: str, session: str | None, context: str, origin: str = ""):
    """Stream one Claude turn to the browser as newline-delimited JSON events."""
    global _chat_proc

    def emit(obj):
        handler.wfile.write((json.dumps(obj) + "\n").encode())
        handler.wfile.flush()

    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    if ROOT is None:
        emit({"t": "error", "text": "Finish the setup first."})
        return
    if not _chat_lock.acquire(blocking=False):
        emit({"t": "error", "text": "Claude is still working on the last message."})
        return
    try:
        claude = find_claude()
        if not claude:
            emit({"t": "error", "text": "Claude Code isn't installed. Open Settings → Check setup."})
            return
        args = [claude, "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS,
                "--strict-mcp-config", "--mcp-config", str(MCP_NONE),
                # Only the tools CAD work needs: a shorter prompt (faster, ~40% cheaper per message) and
                # Bash instead of the PowerShell tool, which took ~4 s to start each run (measured 2026-11-25)
                "--tools", CHAT_TOOLS,
                "--append-system-prompt", chat_prompt()]
        model = load_state()["settings"]["model"]  # Settings panel; empty = Claude Code's default
        if model:
            args += ["--model", model]
        if session:
            args += ["--resume", session]
        prompt = (f"[Dashboard: {context}]\n" if context else "") + message
        parts_before = {d.name for d in ROOT.iterdir() if d.is_dir()}
        bash = find_git_bash()  # Claude Code finds a per-user Git (not on PATH until the next sign-in) through this
        if bash:
            os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = bash
        try:
            proc = subprocess.Popen(args, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            emit({"t": "error", "text": "Couldn't find the claude command on this PC."})
            return
        _chat_proc = proc
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            typ = ev.get("type")
            if typ == "system" and ev.get("subtype") == "init":
                emit({"t": "session", "id": ev.get("session_id")})
            elif typ == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "text" and c.get("text", "").strip():
                        text = c["text"]
                        if BUILD_MARK in text:  # Claude has committed to modelling: start the viewer's build animation
                            emit({"t": "building"})
                            text = text.replace(BUILD_MARK, "").strip()
                        if text:
                            emit({"t": "text", "text": text})
                    elif c.get("type") == "tool_use":
                        emit({"t": "tool", "text": _tool_summary(c.get("name", ""), c.get("input") or {})})
            elif typ == "result":
                denied = [_tool_summary(d.get("tool_name", ""), d.get("tool_input") or {})
                          for d in ev.get("permission_denials") or []]
                if denied:
                    emit({"t": "denied", "items": denied})
                if ev.get("is_error"):
                    emit({"t": "error", "text": str(ev.get("result") or ev.get("subtype") or "Claude stopped with an error.")})
                emit({"t": "session", "id": ev.get("session_id")})
        proc.wait()
        if proc.returncode:
            err = (proc.stderr.read() or "").strip()[-400:]
            emit({"t": "error", "text": err or "Stopped."})
        made = sorted(d.name for d in ROOT.iterdir() if d.is_dir() and d.name not in parts_before
                      and not d.name.startswith((".", "_")) and d.name not in SKIP_DIRS)
        cat = _file_new_parts(origin, made)
        if cat:
            emit({"t": "note", "text": f"Filed {', '.join(made)} in the {cat} category, next to {origin}."})
        recycled = housekeeping()
        if recycled:
            emit({"t": "note", "text": f"Moved to Recycle Bin (keeping the newest {load_state()['settings']['keep']}): " + ", ".join(recycled)})
        emit({"t": "done"})
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        _chat_proc = None
        _chat_lock.release()


def safe_path(rel: str) -> Path | None:
    if ROOT is None:
        return None
    p = (ROOT / unquote(rel)).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return p


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the console quiet
        pass

    def send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj).encode(), "application/json")

    # Only this app's own page may use the server. A web page in your browser could otherwise send it requests
    # (delete parts, start a chat): the Host must be this server (stops DNS rebinding), a browser Origin must be
    # ours, and API calls carry TOKEN, which only the page we serve knows. /api/quit without an Origin is the
    # installer / Stop CAD Parts.cmd (local programs, not web pages).
    def blocked(self, post: bool) -> str | None:
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if (self.headers.get("Host") or "").lower() not in hosts:
            return "host"
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {"http://" + h for h in hosts}:
            return "origin"
        u = urlparse(self.path)
        needs = post or (u.path.startswith("/api/") and u.path not in OPEN_GETS)
        if post and u.path == "/api/quit" and origin is None:
            needs = False
        if needs:
            tok = self.headers.get("X-CAD-Token") or parse_qs(u.query).get("t", [""])[0]
            if not secrets.compare_digest(tok.encode(), TOKEN.encode()):
                return "token"
        return None

    def do_GET(self):
        if bad := self.blocked(False):
            return self.json({"ok": False, "error": "blocked: " + bad}, 403)
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            page = (APP_DIR / "index.html").read_bytes().replace(b"__CAD_TOKEN__", TOKEN.encode())
            return self.send(200, page, "text/html; charset=utf-8")
        if u.path in ("/app.js", "/app.css"):
            return self.send(200, (APP_DIR / u.path[1:]).read_bytes(),
                             ("text/javascript" if u.path.endswith(".js") else "text/css") + "; charset=utf-8")
        if u.path == "/api/ping":
            return self.json({"ok": True})
        if u.path == "/api/parts":
            return self.json(scan())
        if u.path == "/api/frames":  # newest recorded construction steps since `since` (optionally one part)
            q = parse_qs(u.query)
            since = float(q.get("since", ["0"])[0] or 0)
            want = q.get("part", [""])[0]
            best = None
            if FRAMES_ROOT.is_dir():
                for d in FRAMES_ROOT.iterdir():
                    if want and d.name != want:
                        continue
                    try:
                        m = json.loads((d / "latest.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if m.get("t", 0) > since and (best is None or m["t"] > best["t"]):
                        best = m
            return self.json({"manifest": best})
        if u.path.startswith("/frames/"):
            p = (FRAMES_ROOT / unquote(u.path[len("/frames/"):])).resolve()
            try:
                p.relative_to(FRAMES_ROOT.resolve())
            except ValueError:
                return self.send(404, b"not found", "text/plain")
            if not p.is_file() or p.suffix.lower() != ".stl":
                return self.send(404, b"not found", "text/plain")
            return self.send(200, p.read_bytes(), "application/octet-stream")
        if u.path == "/api/trash":
            return self.json({"items": trash_list(), "root": str(ROOT)})
        if u.path == "/api/profiles":
            d = load_profiles()
            for x in d["profiles"]:
                x["exists"] = Path(x["folder"]).is_dir()
            return self.json({**d, "root": str(ROOT) if ROOT else None, "detected": detect_folders()})
        if u.path == "/api/version":
            return self.json(update_info())
        if u.path == "/api/appicon":
            png = app_icon(parse_qs(u.query).get("id", [""])[0])
            if not png:
                return self.send(404, b"no icon", "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(png)
            return
        if u.path == "/api/apps":
            return self.json(apps_info())
        if u.path == "/api/health":
            return self.json(health(force="force" in u.query))
        if u.path == "/api/profile/find":  # first start on a new PC, after signing in
            return self.json({"match": find_my_folder()})
        if u.path == "/api/import/peek":
            return self.json(peek_export(parse_qs(u.query).get("file", [""])[0]))
        if u.path == "/api/job":
            return self.json(_jobs.get(parse_qs(u.query).get("what", [""])[0]) or {})
        if u.path == "/api/folderinfo":
            return self.json(folder_info(parse_qs(u.query).get("path", [""])[0]))
        if u.path == "/api/chats":
            return self.json(load_chats())
        if u.path == "/api/settings":
            return self.json(load_state()["settings"])
        if u.path.startswith("/uploads/"):
            if ROOT is None:
                return self.send(404, b"not found", "text/plain")
            p = (uploads_dir() / unquote(u.path[len("/uploads/"):])).resolve()
            if p.parent != uploads_dir().resolve() or not p.is_file() or p.suffix.lower() not in IMG_TYPES.values():
                return self.send(404, b"not found", "text/plain")
            ct = {v: k for k, v in IMG_TYPES.items()}[p.suffix.lower()]
            return self.send(200, p.read_bytes(), ct)
        if u.path.startswith("/vendor/"):  # three.js, saved locally so the viewer works without internet
            base = (APP_DIR / "vendor").resolve()
            p = (base / unquote(u.path[len("/vendor/"):])).resolve()
            try:
                p.relative_to(base)
            except ValueError:
                return self.send(404, b"not found", "text/plain")
            if not p.is_file() or p.suffix != ".js":
                return self.send(404, b"not found", "text/plain")
            body = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path.startswith("/files/"):
            p = safe_path(u.path[len("/files/"):])
            if not p or not p.is_file() or p.suffix.lower() not in MODEL_EXT:
                return self.send(404, b"not found", "text/plain")
            return self.send(200, p.read_bytes(), "application/octet-stream")
        if u.path.startswith("/preview/"):  # any model, always as STL
            p = safe_path(u.path[len("/preview/"):])
            if not p or not p.is_file() or p.suffix.lower() not in MODEL_EXT:
                return self.send(404, b"not found", "text/plain")
            if p.suffix.lower() == ".stl":
                return self.send(200, p.read_bytes(), "application/octet-stream")
            try:
                return self.send(200, threemf_to_stl(p), "application/octet-stream")
            except Exception as e:  # noqa: BLE001 - report, don't crash the server
                return self.send(422, f"could not read 3MF: {e}".encode(), "text/plain")
        self.send(404, b"not found", "text/plain")

    def do_POST(self):
        if bad := self.blocked(True):
            return self.json({"ok": False, "error": "blocked: " + bad}, 403)
        u = urlparse(self.path)
        q = parse_qs(u.query)
        rel = q.get("path", [""])[0]
        if u.path == "/api/open":
            return self.json(open_with(q.get("app", [""])[0], rel))
        if u.path == "/api/apps/pick":
            return self.json(pick_app())
        if u.path == "/api/apps/defaults":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.json({"ok": False}, 400)
            return self.json(set_default_apps(body))
        if u.path == "/api/blender":
            p = safe_path(rel)
            if not p or not p.is_file() or p.suffix.lower() != ".stl":
                return self.json({"ok": False, "error": "not an STL in cad-parts"}, 400)
            blender = find_blender()
            if not blender:
                return self.json({"ok": False, "error": "Blender isn't installed"})
            VIEWER_STATE.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_PS1),
                 "-Path", str(p), "-Blender", blender, "-State", str(VIEWER_STATE)],
                capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return self.json({"ok": r.returncode == 0, "out": r.stdout.strip() or r.stderr.strip()})
        if u.path == "/api/bambu":
            p = safe_path(rel)
            if not p or not p.is_file() or p.suffix.lower() not in MODEL_EXT:
                return self.json({"ok": False, "error": "not a model in cad-parts"}, 400)
            bambu = find_bambu()
            if not bambu:
                return self.json({"ok": False, "error": "Bambu Studio isn't installed"})
            # Bambu's own "single_instance" preference (turned on 2026-11-25 in BambuStudio.conf) hands the
            # file to an already-open window. Don't pass --single-instance: this build exits with -2 on it.
            subprocess.Popen([bambu, str(p)], cwd=str(Path(bambu).parent),
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return self.json({"ok": True})
        if u.path == "/api/restore":
            return self.json(trash_restore(q.get("id", [""])[0]))
        if u.path == "/api/delete":
            part_name, _, file = rel.strip("/").partition("/")
            return self.json(delete_item(part_name, file or None))
        if u.path == "/api/profile/save":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.json({"ok": False}, 400)
            return self.json(save_profile(body))
        if u.path == "/api/profile/switch":
            if _chat_lock.locked():
                return self.json({"ok": False, "error": "wait until Claude has finished"})
            return self.json({"ok": activate_profile(q.get("id", [""])[0])})
        if u.path == "/api/profile/remove":
            return self.json(remove_profile(q.get("id", [""])[0]))
        if u.path == "/api/setup":
            return self.json(setup_action(q.get("what", [""])[0], q.get("code", [""])[0]))
        if u.path == "/api/pickfile":
            return self.json({"path": pick_file(q.get("start", [""])[0])})
        if u.path == "/api/export":
            dest = q.get("dest", [""])[0]
            if not dest and q.get("name"):  # no save dialog (a browser, an older app window): Downloads
                dest = str(Path.home() / "Downloads" / re.sub(r'[<>:"/\\|?*]', "", q["name"][0])[:120])
            if ROOT is None or not dest or not Path(dest).is_absolute() or not dest.lower().endswith(".zip"):
                return self.json({"ok": False, "error": "choose where to save it"})
            if (_jobs.get("export") or {}).get("state") == "running":
                return self.json({"ok": True})
            name = next((x["name"] for x in load_profiles()["profiles"] if x["id"] == load_profiles()["active"]), "")
            _jobs.pop("export", None)
            threading.Thread(target=_run_job, args=("export", lambda: export_parts(dest, name)), daemon=True).start()
            return self.json({"ok": True})
        if u.path == "/api/pickfolder":
            return self.json({"path": pick_folder(q.get("start", [""])[0])})
        if ROOT is None and u.path not in ("/api/quit", "/api/chat/stop"):
            return self.json({"ok": False, "error": "finish the setup first"})
        if u.path == "/api/rename":
            return self.json(rename_part(rel.strip("/"), q.get("to", [""])[0]))
        if u.path == "/api/duplicate":
            return self.json(duplicate_part(rel.strip("/")))
        if u.path in ("/api/meta", "/api/settings", "/api/chat/save", "/api/notify", "/api/category"):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.json({"ok": False}, 400)
            if u.path == "/api/meta":
                return self.json(set_meta(rel.strip("/"), body))
            if u.path == "/api/category":
                return self.json(edit_category(body))
            if u.path == "/api/chat/save":
                return self.json(save_chat(q.get("key", [""])[0], body))
            if u.path == "/api/notify":
                if load_state()["settings"]["notify"]:
                    notify(body.get("title", "CAD Parts"), body.get("body", ""))
                return self.json({"ok": True})
            allowed = {"model": str, "keep": int, "printer": str, "notify": bool, "printers": clean_printers}

            def fn(st):
                for k, typ in allowed.items():
                    if k in body:
                        try:
                            st["settings"][k] = typ(body[k])
                        except (TypeError, ValueError):
                            pass
                st.pop("model", None)  # old top-level spot
            update_state(fn)
            return self.json({"ok": True, "settings": load_state()["settings"]})
        if u.path == "/api/upload":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20 * 1024 * 1024:
                return self.json({"ok": False, "error": "picture is over 20 MB"})
            return self.json(save_upload(self.rfile.read(n), self.headers.get("Content-Type", "")))
        if u.path == "/api/complete":
            return self.json(set_complete(rel.strip("/"), q.get("on", ["1"])[0] == "1"))
        if u.path == "/api/folder":
            p = safe_path(rel)
            if not p or not p.is_dir():
                return self.json({"ok": False}, 400)
            subprocess.Popen(["explorer", str(p)])
            return self.json({"ok": True})
        if u.path == "/api/chat":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.json({"ok": False}, 400)
            msg = str(body.get("message", "")).strip()
            if not msg:
                return self.json({"ok": False, "error": "empty message"}, 400)
            return run_chat(self, msg, body.get("session") or None, str(body.get("context", ""))[:300],
                            str(body.get("part") or "")[:80])
        if u.path == "/api/chat/stop":
            p = _chat_proc
            if p and p.poll() is None:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            return self.json({"ok": True})
        if u.path == "/api/quit":
            self.json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send(404, b"not found", "text/plain")


def main():
    url = f"http://127.0.0.1:{PORT}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running: just open it
        if "--open" in sys.argv:
            webbrowser.open(url)
        return
    activate_profile(load_profiles()["active"])
    print(f"CAD Parts dashboard on {url}  (parts: {ROOT or 'not set up yet'})")
    if not MCP_NONE.is_file():
        MCP_NONE.write_text('{ "mcpServers": {} }\n', encoding="utf-8")
    # warm the build123d worker in the background so the first build doesn't wait ~10 s for the import
    # (on a new PC this is also when build123d gets downloaded; the setup checklist shows its progress)
    threading.Thread(target=warm_worker, daemon=True).start()
    threading.Thread(target=claude_auth, kwargs={"force": True}, daemon=True).start()
    if "--open" in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
