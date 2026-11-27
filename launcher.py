"""CAD Parts desktop app: opens the dashboard in its own window.

Starts the dashboard server (server.py, same folder as this file / the .exe) if it isn't running,
then shows http://127.0.0.1:8765 in a native window (pywebview + WebView2).
Closing the window leaves the server running, so a chat that is still building keeps going.
The window remembers its size, position and maximized state between runs.
Build: see build-exe.cmd.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:8765"
TITLE = "CAD Parts"

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
if not (APP_DIR / "server.py").is_file():  # exe copied somewhere else: fall back to the project
    APP_DIR = Path.home() / "OneDrive" / "cad-parts-app"

LOCAL = Path.home() / "AppData" / "Local" / "CAD Parts"
STORAGE = LOCAL / "webview"         # the page's own storage (layout choices etc.)
WINDOW_FILE = LOCAL / "window.json"  # size / position / maximized, per PC


def server_up():
    try:
        with urllib.request.urlopen(URL + "/api/ping", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


LOCAL_BIN = Path.home() / ".local" / "bin"  # where uv's (and Claude Code's) official installers put their programs
if str(LOCAL_BIN).lower() not in os.environ.get("PATH", "").lower():  # so the server and everything it starts find them
    os.environ["PATH"] = str(LOCAL_BIN) + os.pathsep + os.environ.get("PATH", "")


def find_uv():
    return shutil.which("uv") or (str(LOCAL_BIN / "uv.exe") if (LOCAL_BIN / "uv.exe").is_file() else None)


def install_uv():
    """First run on a new PC: uv runs the app's Python. Astral's official installer, no admin rights needed."""
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    "irm https://astral.sh/uv/install.ps1 | iex"],
                   creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=600)
    return find_uv()


SERVER_LOG = Path.home() / "AppData" / "Local" / "CAD Parts" / "server.log"  # what the server printed (for "why won't it start")


def start_server():
    """Start the server in the background; returns the process so the caller can tell if it died."""
    uv = find_uv() or "uv"
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(SERVER_LOG, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen([uv, "run", "--python", "3.12", "python", "server.py"], cwd=str(APP_DIR),
                            creationflags=subprocess.CREATE_NO_WINDOW,  # hidden console: everything the server starts shares it (no pop-up windows)
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def log_tail(n=3):
    try:
        lines = [x.strip() for x in SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        return " · ".join(lines[-n:])[-300:]
    except OSError:
        return ""


def wait_for_server(seconds=60):
    for _ in range(seconds * 2):
        if server_up():
            return True
        time.sleep(0.5)
    return False


class Api:
    """Called from the page (window.pywebview.api)."""

    _win = None  # the app window (set in main); underscore: not exposed to the page

    def start_server(self):
        """When the server isn't answering."""
        if not server_up():
            start_server()
        return wait_for_server(60)

    def pick_folder(self, start=""):
        """Windows' folder picker, shown by the app window itself: opens at once and always in front of it."""
        import webview
        if not self._win:
            return None
        kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        start = start if start and os.path.isdir(start) else ""
        r = self._win.create_file_dialog(kind, directory=start)
        return r[0] if r else None


def focus_existing():
    """Second launch: bring the open window to the front instead of opening another one."""
    u = ctypes.windll.user32
    h = u.FindWindowW(None, TITLE)
    if h:
        u.ShowWindow(h, 9)  # SW_RESTORE
        u.SetForegroundWindow(h)


def load_geometry():
    try:
        g = json.loads(WINDOW_FILE.read_text(encoding="utf-8"))
        return {k: int(g[k]) for k in ("x", "y", "width", "height")} | {"maximized": bool(g.get("maximized"))}
    except Exception:
        return {"width": 1500, "height": 950, "maximized": True}


def on_screen(g):
    """Don't restore a window onto a monitor that's no longer there."""
    u = ctypes.windll.user32
    vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)  # virtual screen origin
    vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    return "x" in g and vx - 50 <= g["x"] <= vx + vw - 200 and vy - 50 <= g["y"] <= vy + vh - 200


LOADING = """<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;
background:#121211;color:#9a9a94;font:15px 'Segoe UI',sans-serif">
<div style="display:flex;flex-direction:column;align-items:center;gap:14px">
<div style="width:44px;height:44px;border-radius:12px;display:grid;place-items:center;
background:linear-gradient(145deg,#3987e5,#1c5cab)"><svg viewBox="0 0 24 24" width="26" height="26" fill="none"
stroke="#fff" stroke-width="1.8" stroke-linejoin="round"><path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4z"/>
<path d="M4 7.4 12 12l8-4.6M12 12v9.2" stroke-opacity=".75"/></svg></div>
<div id=m>Starting CAD Parts&hellip;</div></div></body></html>"""


def main():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\CADPartsWindow")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        focus_existing()
        return

    import webview

    LOCAL.mkdir(parents=True, exist_ok=True)
    g = load_geometry()
    pos = {"x": g["x"], "y": g["y"]} if on_screen(g) else {}
    state = dict(g)

    def say(text):
        win.evaluate_js("document.getElementById('m').textContent=" + json.dumps(text))

    def boot(win):
        if not server_up():
            if not find_uv():
                say("Installing uv (one time, about a minute)…")
                if not install_uv():
                    say("Couldn't install uv. Check your internet connection and open CAD Parts again.")
                    return
            time.sleep(1)  # an old server that's just been told to stop (e.g. by the installer) needs a moment
            proc, tries = start_server(), 1
            for i in range(600):  # the first start downloads Python, so allow a few minutes
                if server_up():
                    break
                if proc.poll() is not None and not server_up():  # it stopped instead of starting
                    if tries < 3:
                        say("Starting CAD Parts… (trying again)")
                        time.sleep(2)
                        proc, tries = start_server(), tries + 1
                        continue
                    say("CAD Parts couldn't start its server. " + (log_tail() or "No details.")
                        + " Close this window and open CAD Parts again; if it keeps happening, send this message.")
                    return
                if i == 16:
                    say("Starting CAD Parts… the first start downloads Python and can take a few minutes.")
                time.sleep(0.5)
            else:
                say("CAD Parts' server is taking very long to start. " + (log_tail() or "")
                    + " Check your internet connection, then close this window and open CAD Parts again.")
                return
        win.load_url(URL)

    api = Api()
    win = webview.create_window(TITLE, html=LOADING, width=g["width"], height=g["height"], **pos,
                                min_size=(1000, 650), maximized=g["maximized"], background_color="#121211",
                                js_api=api)
    api._win = win

    # remember the last normal (not maximized) size and position, and whether it was maximized
    def on_resized(w, h):
        if not state.get("maximized"):
            state["width"], state["height"] = w, h

    def on_moved(x, y):
        if not state.get("maximized"):
            state["x"], state["y"] = x, y

    def on_max():
        state["maximized"] = True

    def on_restored():
        state["maximized"] = False

    def on_closing():
        h = ctypes.windll.user32.FindWindowW(None, TITLE)
        if h:  # the real answer, whatever the events said (restoring from minimized also fires "restored")
            state["maximized"] = bool(ctypes.windll.user32.IsZoomed(h))
        try:
            WINDOW_FILE.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass

    win.events.resized += on_resized
    win.events.moved += on_moved
    win.events.maximized += on_max
    win.events.restored += on_restored
    win.events.closing += on_closing
    webview.start(boot, win, private_mode=False, storage_path=str(STORAGE),
                  icon=str(APP_DIR / "cadparts.ico"))
    del mutex


if __name__ == "__main__":
    main()
