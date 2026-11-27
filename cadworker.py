"""Warm build123d worker: imports build123d once, then runs part scripts on request.

Importing build123d costs ~10 s on this PC (mostly loading the OpenCascade DLLs); running a
typical part script takes well under a second. This process pays the import once and keeps
running, so each script run is fast. Started by run.py (or the dashboard server) as:

    uv run --python 3.12 --with build123d python cadworker.py

Protocol: one JSON line per request on 127.0.0.1:8766 -> {"script", "args", "cwd"};
reply is one JSON line {"ok", "out", "err", "secs"}. Requests are handled one at a time.
Each script runs as __main__ in a fresh namespace, exactly like `python script.py`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import socket
import sys
import time
import traceback

PORT = 8766

t0 = time.perf_counter()
import build123d  # noqa: E402,F401  (the slow part, done once)

LOAD_SECS = time.perf_counter() - t0

# ----------------------------------------------------------------------------------------------
# Construction-step recording, for the dashboard's live "watch it being built" animation.
# While a part script runs we keep references to the solids produced by each modelling operation
# (+, -, &, fillet, extrude, ... and BuildPart steps). Only AFTER the reply has been sent to run.py
# are they tessellated (coarsely) and written to a temp folder outside OneDrive, so scripts don't
# run any slower. Nested operations (e.g. a fillet calling booleans internally) are not recorded.
# ----------------------------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent            # ...\OneDrive\cad-parts
FRAMES_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CADPartsFrames"
MAX_FRAMES = 14
_rec: list[tuple[str, object]] = []   # (label, shape) in the order they were made
_exported: list[object] = []          # shapes passed to export_stl/export_step: the final part(s)
_depth = 0
_recording = False
_ORIG: dict = {}                      # unwrapped build123d functions


def _volume(s) -> float:
    try:
        return float(s.volume)
    except Exception:  # noqa: BLE001
        return 0.0


def _record(label: str, shape) -> None:
    if _recording and _depth == 0 and shape is not None and _volume(shape) > 1e-6:
        _rec.append((label, shape))
        del _rec[:-80]  # bounded


def _wrap_fn(name: str, fn, label=None):
    def wrapper(*a, **k):
        global _depth
        _depth += 1
        try:
            res = fn(*a, **k)
        finally:
            _depth -= 1
        _record(label or name, res)
        return res
    wrapper.__name__, wrapper.__doc__, wrapper.__wrapped__ = fn.__name__, fn.__doc__, fn
    return wrapper


def _wrap_op(cls, dunder: str, sym: str) -> None:
    orig = cls.__dict__.get(dunder)
    if orig is None:
        return

    def op(self, other, _orig=orig):
        global _depth
        # the block an operation starts from (e.g. the base Box before "+ pyramid"), if it isn't recorded yet
        if _recording and _depth == 0 and not any(s is self for _, s in _rec):
            _record(type(self).__name__, self)
        _depth += 1
        try:
            res = _orig(self, other)
        finally:
            _depth -= 1
        others = other if isinstance(other, (list, tuple)) else [other]
        kinds = ", ".join(sorted({type(o).__name__ for o in others}))
        _record(f"{sym} {kinds}", res)
        return res
    setattr(cls, dunder, op)


def _install_hooks() -> None:
    import build123d as bd
    for cls in (bd.Shape, bd.Compound):
        _wrap_op(cls, "__add__", "+")
        _wrap_op(cls, "__sub__", "−")
        _wrap_op(cls, "__and__", "∩")
    for fn in ("fillet", "chamfer", "extrude", "revolve", "loft", "sweep", "offset", "mirror", "split", "thicken"):
        if hasattr(bd, fn):
            setattr(bd, fn, _wrap_fn(fn, getattr(bd, fn)))
    for fn in ("export_stl", "export_step"):
        if hasattr(bd, fn):
            orig = _ORIG[fn] = getattr(bd, fn)

            def exp(to_export, *a, _orig=orig, **k):
                _exported.append(to_export)
                return _orig(to_export, *a, **k)
            exp.__name__ = fn
            setattr(bd, fn, exp)
    orig_add = bd.BuildPart._add_to_context

    def add_to_context(self, *a, **k):
        global _depth
        _depth += 1
        try:
            res = orig_add(self, *a, **k)
        finally:
            _depth -= 1
        mode = k.get("mode")
        _record("build " + (getattr(mode, "name", "").lower() or "add"), getattr(self, "part", None))
        return res
    bd.BuildPart._add_to_context = add_to_context


try:
    _install_hooks()
except Exception:  # noqa: BLE001 - recording is a nice-to-have; never break part scripts
    traceback.print_exc()


def _bbox(s):
    b = s.bounding_box()
    return (b.min.X, b.min.Y, b.min.Z), (b.max.X, b.max.Y, b.max.Z)


def _pick_frames(final) -> list[tuple[str, object]]:
    """Keep the steps that look like stages of the final part (not unrelated helper shapes)."""
    (fx0, fy0, fz0), (fx1, fy1, fz1) = _bbox(final)
    size = max(fx1 - fx0, fy1 - fy0, fz1 - fz0, 1e-6)
    pad = size * 0.15
    fv = _volume(final)
    keep = []
    for label, s in _rec:
        try:
            (x0, y0, z0), (x1, y1, z1) = _bbox(s)
        except Exception:  # noqa: BLE001
            continue
        inside = x0 >= fx0 - pad and y0 >= fy0 - pad and z0 >= fz0 - pad and x1 <= fx1 + pad and y1 <= fy1 + pad and z1 <= fz1 + pad
        big = max(x1 - x0, y1 - y0, z1 - z0) >= size * 0.25
        v = _volume(s)
        if inside and big and 0.02 * fv <= v <= 3 * fv:
            if keep and abs(_volume(keep[-1][1]) - v) <= 1e-6 * max(v, 1):
                keep[-1] = (label, s)  # same shape again: keep the later label
            else:
                keep.append((label, s))
    if not keep or abs(_volume(keep[-1][1]) - fv) > 1e-6 * max(fv, 1):
        keep.append(("final", final))
    if len(keep) > MAX_FRAMES:  # spread the kept steps evenly, always ending on the final shape
        idx = sorted({round(i * (len(keep) - 1) / (MAX_FRAMES - 1)) for i in range(MAX_FRAMES)})
        keep = [keep[i] for i in idx]
    return keep


def _save_frames(script: str, ok: bool) -> None:
    """Runs after the reply has been sent: write coarse STL frames + a manifest for the dashboard."""
    try:
        sp = Path(script).resolve()
        if not ok or sp.parent.parent != ROOT or not _exported:
            return
        part = sp.parent.name
        final = _exported[-1]
        frames = _pick_frames(final)  # always ends with the final shape, so even one-operation parts get a build
        if not frames:
            return
        export = _ORIG["export_stl"]  # the unwrapped exporter, so frames aren't recorded as exports
        run = f"{int(time.time() * 1000)}"
        out = FRAMES_ROOT / part / run
        out.mkdir(parents=True, exist_ok=True)
        (fx0, fy0, fz0), (fx1, fy1, fz1) = _bbox(final)
        tol = max(0.05, max(fx1 - fx0, fy1 - fy0, fz1 - fz0) / 250)
        listed = []
        for i, (label, s) in enumerate(frames):
            name = f"{i:02d}.stl"
            export(s, str(out / name), tolerance=tol, angular_tolerance=0.5)
            listed.append({"file": name, "label": label})
        manifest = {"part": part, "script": sp.name, "run": run, "t": time.time(), "frames": listed}
        (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (FRAMES_ROOT / part / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for old in sorted((p for p in (FRAMES_ROOT / part).iterdir() if p.is_dir()), key=lambda p: p.name)[:-3]:
            for f in old.iterdir():
                f.unlink(missing_ok=True)
            old.rmdir()
    except Exception:  # noqa: BLE001 - never let the animation break anything
        traceback.print_exc()


def run_script(script: str, args: list[str], cwd: str) -> dict:
    global _recording
    out, err = io.StringIO(), io.StringIO()
    old_argv, old_cwd, old_path = sys.argv[:], os.getcwd(), sys.path[:]
    ok, t = True, time.perf_counter()
    _rec.clear(); _exported.clear(); _recording = True
    try:
        os.chdir(cwd or os.path.dirname(os.path.abspath(script)))
        sys.argv = [script, *args]
        sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runpy.run_path(script, run_name="__main__")
            except SystemExit as e:
                ok = e.code in (None, 0)
    except BaseException:  # noqa: BLE001 - report everything back to the caller
        ok = False
        err.write(traceback.format_exc())
    finally:
        _recording = False
        sys.argv, sys.path = old_argv, old_path
        os.chdir(old_cwd)
    return {"ok": ok, "out": out.getvalue(), "err": err.getvalue(), "secs": round(time.perf_counter() - t, 3)}


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1) if hasattr(socket, "SO_EXCLUSIVEADDRUSE") else None
    try:
        srv.bind(("127.0.0.1", PORT))
    except OSError:
        return  # another worker is already running
    srv.listen(4)
    print(f"cadworker ready (build123d loaded in {LOAD_SECS:.1f}s) on {PORT}", flush=True)
    while True:
        conn, _ = srv.accept()
        with conn:
            f = conn.makefile("rwb")
            line = f.readline()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                continue
            if req.get("ping"):
                res = {"ok": True, "pong": True, "load_secs": round(LOAD_SECS, 1)}
            else:
                res = run_script(req["script"], req.get("args") or [], req.get("cwd") or "")
            f.write((json.dumps(res) + "\n").encode())
            f.flush()
        # the caller already has its answer; now save the construction steps for the dashboard
        if not req.get("ping"):
            pass  # step frames are no longer saved: the dashboard shows the wisps effect instead (2026-11-25)
        _rec.clear(); _exported.clear()  # drop references to this run's shapes


if __name__ == "__main__":
    main()
