"""Warm build123d worker: imports build123d once, then runs part scripts on request.

Importing build123d costs ~10 s on this PC (mostly loading the OpenCascade DLLs); running a
typical part script takes well under a second. This process pays the import once and keeps
running, so each script run is fast. Started by run.py (or the dashboard server) as:

    uv run --python 3.12 --with build123d python cadworker.py

Protocol: one JSON line per request on 127.0.0.1:8766 -> {"script", "args", "cwd"} or {"ping": true};
reply is one JSON line {"ok", "out", "err", "secs"}. Requests are handled one at a time; others wait
in the listen queue. The port is bound BEFORE build123d is imported, so "port in use" always means
"a worker is running (loading or busy)", never "start another one".
Each script runs as __main__ in a fresh namespace, exactly like `python script.py`; modules it
imported from its own folders are dropped afterwards, so an edited helper module is re-read next run.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import socket
import sys
import sysconfig
import time
import traceback

PORT = 8766


def _bind():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        srv.bind(("127.0.0.1", PORT))
    except OSError:
        return None  # another worker is already running
    srv.listen(16)
    return srv


def _lib_roots() -> list[str]:
    """Folders of the interpreter's own and installed modules: never unloaded between runs."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    for k in ("stdlib", "platstdlib", "purelib", "platlib"):
        with contextlib.suppress(Exception):
            roots.add(sysconfig.get_paths()[k])
    # `uv run --with build123d` adds an overlay environment through a .pth file: its site-packages too
    roots.update(p for p in sys.path if p and "site-packages" in p.lower())
    roots.add(os.path.dirname(os.path.abspath(__file__)))  # this worker's own folder
    return [os.path.normcase(os.path.abspath(r)) + os.sep for r in roots if r]


def _is_under(path: str, roots: list[str]) -> bool:
    return any(path.startswith(r) for r in roots)


def _drop_script_modules(script_dirs: list[str], lib_roots: list[str]) -> None:
    """Forget modules a part script imported from its own folders (never stdlib/site-packages ones:
    dropping half of a library would break class identity)."""
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if name == "__main__" or not isinstance(f, str):
            continue
        p = os.path.normcase(os.path.abspath(f))
        if _is_under(p, script_dirs) and not _is_under(p, lib_roots):
            sys.modules.pop(name, None)


def run_script(script: str, args: list[str], cwd: str, lib_roots: list[str]) -> dict:
    out, err = io.StringIO(), io.StringIO()
    old_argv, old_cwd, old_path = sys.argv[:], os.getcwd(), sys.path[:]
    here = os.path.dirname(os.path.abspath(script))
    dirs = {here, os.path.dirname(here)}  # the part folder and the parts folder above it
    if cwd:
        dirs.add(os.path.abspath(cwd))
    script_dirs = [os.path.normcase(d) + os.sep for d in dirs]
    ok, t = True, time.perf_counter()
    try:
        os.chdir(cwd or here)
        sys.argv = [script, *args]
        sys.path.insert(0, here)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runpy.run_path(script, run_name="__main__")
            except SystemExit as e:
                ok = e.code in (None, 0)
    except BaseException:  # noqa: BLE001 - report everything back to the caller
        ok = False
        err.write(traceback.format_exc())
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path
        with contextlib.suppress(OSError):
            os.chdir(old_cwd)
        _drop_script_modules(script_dirs, lib_roots)
    return {"ok": ok, "out": out.getvalue(), "err": err.getvalue(), "secs": round(time.perf_counter() - t, 3)}


def handle(conn: socket.socket, load_secs: float, lib_roots: list[str]) -> None:
    conn.settimeout(30)  # a client that connects and never sends a line can't block the worker
    f = conn.makefile("rwb")
    line = f.readline()
    if not line:
        return  # e.g. run.py checking that the port is taken
    conn.settimeout(None)
    try:
        req = json.loads(line)
        if not isinstance(req, dict):
            raise ValueError("request is not a JSON object")
    except ValueError as e:
        res = {"ok": False, "out": "", "err": f"bad request: {e}\n", "secs": 0}
    else:
        if req.get("ping"):
            res = {"ok": True, "pong": True, "load_secs": round(load_secs, 1)}
        elif not isinstance(req.get("script"), str):
            res = {"ok": False, "out": "", "err": "bad request: no \"script\"\n", "secs": 0}
        else:
            res = run_script(req["script"], list(req.get("args") or []), req.get("cwd") or "", lib_roots)
    f.write((json.dumps(res) + "\n").encode())
    f.flush()


def main():
    srv = _bind()
    if srv is None:
        return
    t0 = time.perf_counter()
    import build123d  # noqa: F401  (the slow part, done once; queued requests wait for it)
    load_secs = time.perf_counter() - t0
    lib_roots = _lib_roots()
    print(f"cadworker ready (build123d loaded in {load_secs:.1f}s) on {PORT}", flush=True)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        try:
            with conn:
                handle(conn, load_secs, lib_roots)
        except Exception:  # noqa: BLE001 - one bad request or a vanished client must not stop the worker
            traceback.print_exc()


if __name__ == "__main__":
    main()
