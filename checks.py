"""Pre-release checks: run before every release.

    uv run --python 3.12 python checks.py

Starts the real server (server.py) on a free port with a throwaway parts folder and a throwaway profiles file,
then drives it over HTTP the way the app does: list, rename, duplicate, favorite, category, delete to the
Recycle Bin and restore, OneDrive sync-copy merge, previews, the page itself, and the security checks.
Your real parts and profiles are never touched. Prints PASS/FAIL per check; exits 1 if anything failed.
(Leaves one small throwaway file in the Recycle Bin: the merged OneDrive sync copy.)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

STL = b"solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid t\n"
results: list[tuple[bool, str]] = []


def check(ok, what: str, detail="") -> bool:
    results.append((bool(ok), what))
    print(("PASS  " if ok else "FAIL  ") + what + ("" if ok or not detail else f"   ({detail})"))
    return bool(ok)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cadparts-checks-"))
    root = tmp / "parts"
    for part, files in {"cube": ["cube_v1.stl", "cube_v2_rounded.stl"], "hook": ["hook_v1.stl"]}.items():
        (root / part).mkdir(parents=True)
        for f in files:
            (root / part / f).write_bytes(STL)
    (root / server.META).mkdir()
    server.APP_DATA = tmp / "appdata"
    server.PROFILES_FILE = server.APP_DATA / "profiles.json"
    server.ROOT = root

    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None, token=True, headers=None):
        h = {"X-CAD-Token": server.TOKEN} if token else {}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def parts():
        return {p["name"]: p for p in call("GET", "/api/parts")[1]["parts"]}

    try:
        # --- the page and its files
        code, page = call("GET", "/", token=False)
        check(code == 200 and server.TOKEN.encode() in page and b"__CAD_TOKEN__" not in page, "page is served with this run's key")
        check(call("GET", "/app.js", token=False)[0] == 200 and call("GET", "/app.css", token=False)[0] == 200, "app.js and app.css are served")

        # --- security: other web pages can't use the server
        check(call("POST", "/api/rename?path=cube&to=x", token=False)[0] == 403, "POST without the key is refused")
        check(call("GET", "/api/parts", token=False)[0] == 403, "reading parts without the key is refused")
        check(call("POST", "/api/rename?path=cube&to=x", headers={"Origin": "https://evil.example"})[0] == 403, "another website's Origin is refused")
        check(call("GET", "/api/ping", token=False, headers={"Host": "evil.example"})[0] == 403, "a foreign Host (DNS rebinding) is refused")
        check(call("GET", "/api/ping", token=False)[0] == 200, "ping works without the key (launcher)")
        check("cube" in parts(), "nothing was renamed by the refused requests")

        # --- listing
        p = parts()
        check(set(p) == {"cube", "hook"}, "parts are listed", list(p))
        cube_versions = [f["version"] for s in p["cube"]["series"] for f in s["files"]]
        check(cube_versions == [1, 2], "versions are read in order", cube_versions)

        # --- rename, duplicate, favorite, category
        code, r = call("POST", "/api/rename?path=cube&to=Block")
        check(r.get("ok") and (root / "block").is_dir() and not (root / "cube").exists(), "rename a part", r)
        code, r = call("POST", "/api/duplicate?path=block")
        dup = r.get("name", "")
        check(r.get("ok") and (root / dup).is_dir(), "duplicate a part", r)
        call("POST", "/api/meta?path=block", {"fav": True})
        call("POST", "/api/meta?path=block", {"category": "Test Shapes"})
        p = parts()
        check(p["block"]["fav"] and p["block"]["category"] == "Test Shapes", "favorite and category stick")

        # --- OneDrive sync copy (two PCs changed state.json at once) is merged back in
        (root / server.META / "state-LAPTOP.json").write_text(json.dumps({"favorites": ["hook"], "categories": {"hook": "From Laptop"}}))
        p = parts()
        check(p["hook"]["fav"] and p["hook"]["category"] == "From Laptop" and p["block"]["fav"], "sync copy merged (both PCs' changes kept)")
        check(not (root / server.META / "state-LAPTOP.json").exists(), "sync copy cleared away")

        # --- preview
        code, body = call("GET", "/preview/hook/hook_v1.stl", token=False)
        check(code == 200 and body == STL, "model preview is served")

        # --- delete a version to the Recycle Bin, then restore it
        target = root / "block" / "block_v2_rounded.stl"
        code, r = call("POST", "/api/delete?path=block/block_v2_rounded.stl")
        check(r.get("ok") and not target.exists(), "delete a version (to the Recycle Bin)", r)
        items = []
        for _ in range(10):  # the Recycle Bin can take a moment to list it
            items = [it for it in call("GET", "/api/trash")[1].get("items", []) if it.get("name") == target.name]
            if items:
                break
            time.sleep(0.5)
        check(items, "it shows in the Recycle Bin")
        if items:
            code, r = call("POST", "/api/restore?id=" + urllib.request.quote(items[0]["id"]))
            check(r.get("ok") and target.exists(), "restore it", r)

        # --- version info answers (offline is fine)
        code, v = call("GET", "/api/version")
        check(code == 200 and v.get("version") == server.APP_VERSION, "version info")

        # --- the installer's /api/quit (no Origin, no key) still stops the server
        check(call("POST", "/api/quit", token=False)[0] == 200, "quit works for the installer")
    finally:
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
