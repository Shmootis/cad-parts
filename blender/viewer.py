"""Single-instance STL viewer for Blender.

Started by show.ps1 (CAD Parts app): blender --python viewer.py
Then write an STL path into load.txt (in CADPARTS_VIEWER_DIR); the model is
reloaded in this same Blender window. Reloading the same file keeps the
camera; a different file re-frames the view.
"""
import os
import time
import bpy

# state files (trigger, pid, log) live where show.ps1 says (CADPARTS_VIEWER_DIR), not next to the code
BASE = os.environ.get("CADPARTS_VIEWER_DIR") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE, exist_ok=True)
TRIGGER = os.path.join(BASE, "load.txt")
PIDFILE = os.path.join(BASE, "viewer.pid")
LOGFILE = os.path.join(BASE, "viewer.log")

state = {"mtime": None, "path": None}


def log(msg):
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} {msg}\n")


def setup_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    units = bpy.context.scene.unit_settings
    units.system = 'METRIC'
    units.scale_length = 0.001          # 1 Blender unit = 1 mm
    units.length_unit = 'MILLIMETERS'


def view3d_areas():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                region = next(r for r in area.regions if r.type == 'WINDOW')
                yield window, area, region


def load(path):
    # remove whatever the viewer loaded last time; leave the user's own objects
    for obj in list(bpy.data.objects):
        if obj.get("viewer"):
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)

    before = set(bpy.data.objects)
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.wm.stl_import(filepath=path)
    for obj in set(bpy.data.objects) - before:
        obj["viewer"] = True

    reframe = path != state["path"]
    for window, area, region in view3d_areas():
        space = area.spaces.active
        space.clip_end = 10000
        space.overlay.grid_scale = 0.001
        if reframe:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.view_all()
        area.tag_redraw()
    state["path"] = path
    log(f"loaded {path} (reframed={reframe})")


def poll():
    try:
        if os.path.exists(TRIGGER):
            mtime = os.path.getmtime(TRIGGER)
            if mtime != state["mtime"]:
                state["mtime"] = mtime
                with open(TRIGGER, encoding="utf-8-sig") as f:
                    path = f.read().strip()
                if path and os.path.exists(path):
                    load(path)
                elif path:
                    log(f"missing file {path}")
    except Exception as ex:  # keep the watcher alive
        log(f"error {ex!r}")
    return 0.5


def start():
    setup_scene()
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    log("viewer started")
    bpy.app.timers.register(poll, first_interval=1.0, persistent=True)


start()
