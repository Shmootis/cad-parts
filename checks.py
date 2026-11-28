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
    server.KEY_FILE = tmp / "local" / "server.key"  # never the real launch key
    server.init_key()
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
        check(server.KEY_FILE.read_text().strip() == server.KEY, "the launch key is written for the launcher")
        code, page = call("GET", "/", token=False)
        check(code == 403 and server.TOKEN.encode() not in page, "page without the launch key is refused")
        check(call("GET", "/?k=wrong", token=False)[0] == 403, "page with a wrong launch key is refused")
        req = urllib.request.Request(base + "/?k=" + urllib.request.quote(server.KEY))
        with urllib.request.urlopen(req, timeout=30) as r:
            page, set_cookie = r.read(), r.headers.get("Set-Cookie", "")
        check(server.TOKEN.encode() in page and b"__CAD_TOKEN__" not in page, "page is served with the launch key")
        check(f"cadk={server.KEY}" in set_cookie and "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie,
              "…and remembers it in a cookie", set_cookie)
        code, page = call("GET", "/", token=False, headers={"Cookie": f"cadk={server.KEY}"})
        check(code == 200 and server.TOKEN.encode() in page, "the cookie opens the page (reload)")
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
        (root / "cube" / "notes.md").write_text("cube_v2 is rounded; cubes, cube-holder and cupboard stay", encoding="utf-8")
        server.update_state(lambda st: st.update(kept=["cube/cube_v1.stl", "hook/hook_v1.stl"]))
        code, r = call("POST", "/api/rename?path=cube&to=Block")
        check(r.get("ok") and (root / "block").is_dir() and not (root / "cube").exists(), "rename a part", r)
        notes = (root / "block" / "notes.md").read_text(encoding="utf-8")
        check(notes == "block_v2 is rounded; cubes, cube-holder and cupboard stay", "rename changes whole names only", notes)
        check(server.load_state()["kept"] == ["block/block_v1.stl", "hook/hook_v1.stl"], "kept versions follow the rename",
              server.load_state()["kept"])
        (root / "Mixed").mkdir()
        (root / "Mixed" / "Mixed_v1.stl").write_bytes(STL)
        code, r = call("POST", "/api/rename?path=Mixed&to=mixed")
        check(r.get("ok") and [d.name for d in root.iterdir() if d.name.lower() == "mixed"] == ["mixed"]
              and (root / "mixed" / "mixed_v1.stl").is_file(), "a capitals-only rename works", r)
        shutil.rmtree(root / "mixed")
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

        # --- PowerShell quoting: Windows' curly quotes end a '...' string too
        tricky = "it’s ‘odd’ ‚x‛ 'y' $z"
        out = server._ps("Write-Output " + server._ps_lit(tricky)) or ""
        check(out.strip() == tricky, "PowerShell quoting is safe (’ ‘ ‚ ‛ quotes)", out)
        check(server.safe_path("hook/a%41.stl").name == "a%41.stl", "a % in a file name isn't decoded twice")
        quirky = root / "o’brien"
        quirky.mkdir()
        (quirky / "o’brien_v1.stl").write_bytes(STL)
        code, r = call("POST", "/api/delete?path=" + urllib.request.quote("o’brien/o’brien_v1.stl"))
        check(r.get("ok") and not (quirky / "o’brien_v1.stl").exists(), "delete a file whose folder has a ’ in its name", r)
        items = []
        for _ in range(10):
            items = [it for it in call("GET", "/api/trash")[1].get("items", []) if it.get("name") == "o’brien_v1.stl"]
            if items:
                break
            time.sleep(0.5)
        check(items and items[0].get("part") == "o’brien", "…it shows in the Recycle Bin", items)
        if items:
            code, r = call("POST", "/api/restore?id=" + urllib.request.quote(items[0]["id"]))
            check(r.get("ok") and (quirky / "o’brien_v1.stl").exists(), "…and comes back", r)
        shutil.rmtree(quirky)
        # the merged sync copy (in the Recycle Bin, from <tmp>\parts\.cadparts) isn't listed for a folder <tmp>\part
        listed = [it for it in server.trash_list() if it.get("name") == "state-LAPTOP.json"]
        server.ROOT = Path(str(root)[:-1])
        sibling = [it for it in server.trash_list() if it.get("name") == "state-LAPTOP.json"]
        server.ROOT = root
        check(listed and not sibling, "a folder whose name only starts the same doesn't share the Recycle Bin list")

        # --- version info answers (offline is fine)
        code, v = call("GET", "/api/version")
        check(code == 200 and v.get("version") == server.APP_VERSION, "version info")
        keys = {"version", "latest", "url", "newer", "job", "ready", "downloading", "download_failed"}
        real_repo = server.UPDATE_REPO
        server.UPDATE_REPO = ""
        check(set(server.update_info()) == keys and set(v) == keys, "version info always has the same fields", sorted(v))
        server.UPDATE_REPO = real_repo or "example/none"

        # --- your Claude account owns this folder; a new PC finds it after signing in
        server._account["id"] = "test-account"
        server.stamp_owner(root, "Tester")
        check(server.read_owner(root) == {"name": "Tester", "account": "test-account"}, "folder remembers its owner")
        server.sync_roots = lambda: [tmp]
        server._found_cache["t"] = 0
        m = server.find_my_folder()
        check(m and Path(m["folder"]) == root and m["name"] == "Tester", "a new PC finds the owner's folder", m)
        server._account["id"] = "someone-else"
        server._found_cache["t"] = 0
        check(server.find_my_folder() is None, "someone else's folder isn't picked up")
        server.stamp_owner(root, "Intruder")
        check(server.read_owner(root)["name"] == "Tester", "another account doesn't take over the folder")
        server._account["id"] = "test-account"

        # --- first start on a new PC: yours / "are these yours?" / start fresh in a synced folder
        fake_od = tmp / "FakeOneDrive"
        (fake_od / "Old Parts" / server.META).mkdir(parents=True)
        (fake_od / "Old Parts" / "knob").mkdir()
        (fake_od / "Old Parts" / "knob" / "knob_v1.stl").write_bytes(STL)
        real_services = server.sync_services
        server.sync_services = lambda: [("OneDrive", fake_od)]
        server.sync_roots = lambda: [tmp, fake_od]
        st = server.first_start(force=True)
        check(st["match"] and Path(st["match"]["folder"]) == root, "first start: your folder opens straight away", st["match"])
        check(st["candidate"] and st["candidate"]["folder"].endswith("Old Parts"), "first start: an untagged parts folder is offered", st["candidate"])
        check(st["suggest"]["synced"] == "OneDrive" and st["suggest"]["label"] == "OneDrive\\CAD Parts", "first start: new parts go in the synced folder", st["suggest"])
        # C: a second person (same OneDrive) is never sent into the first person's folder
        theirs = fake_od / "CAD Parts"
        (theirs / server.META).mkdir(parents=True)
        (theirs / server.META / server.OWNER_FILE).write_text(json.dumps({"name": "Other", "account": "someone-else"}))
        check(server.suggest_new_folder()["folder"].endswith("CAD Parts 2"), "Start Fresh skips someone else's folder")
        server.sync_services = lambda: []
        check(st and server.suggest_new_folder()["synced"] == "" and server.suggest_new_folder()["label"].endswith("\\CAD Parts"), "no sync service: Documents")
        server.sync_services = real_services

        # B: a folder from another Claude account is only taken over when you say so
        info = server.folder_info(str(theirs))
        check(info["othersAccount"] and info["owner"] == "Other", "another account's folder is flagged")
        server.stamp_owner(theirs, "Tester")
        check(server.read_owner(theirs)["account"] == "someone-else", "…and not taken over by itself")
        server.stamp_owner(theirs, "Tester", claim=True)
        check(server.read_owner(theirs) == {"name": "Tester", "account": "test-account"}, "Make Them Mine takes it over")

        # A: started fresh (empty folder) and then your parts turned up somewhere else
        empty = tmp / "fresh"
        (empty / server.META).mkdir(parents=True)
        was_root = server.ROOT
        server.ROOT = empty
        server.sync_roots = lambda: [tmp]
        found = server.parts_elsewhere()
        check(found and Path(found["folder"]) == root, "your parts turning up later are offered", found)
        server.ROOT = was_root
        check(server.parts_elsewhere() is None, "…but not when your folder already has parts")

        # --- Export My Parts, then import it on "another PC"
        zip_path = tmp / "export.zip"
        r = call("POST", "/api/export?dest=" + urllib.request.quote(str(zip_path)))[1]
        job = {}
        for _ in range(60):
            job = call("GET", "/api/job?what=export")[1]
            if job.get("state") in ("done", "failed"):
                break
            time.sleep(0.25)
        check(r.get("ok") and job.get("state") == "done" and zip_path.is_file(), "export makes one file", job)
        peek = call("GET", "/api/import/peek?file=" + urllib.request.quote(str(zip_path)))[1]
        check(peek.get("ok") and peek.get("parts") == 3, "the file says what's in it", peek)
        other = tmp / "other-pc"
        (other / "hook").mkdir(parents=True)  # the other PC already has a "hook": it must be left alone
        res = server.import_parts(str(zip_path), other)
        check((other / "block" / "block_v1.stl").read_bytes() == STL and res["skipped"] == ["hook"]
              and not any((other / "hook").iterdir()), "import brings parts over, leaves existing ones alone", res)
        check(json.loads((other / server.META / "state.json").read_text())["favorites"], "favorites come along")
        check(server.read_owner(other).get("account") == "test-account", "the owner comes along")
        old = (root / "block" / "block_v1.stl").stat().st_mtime
        check(abs((other / "block" / "block_v1.stl").stat().st_mtime - old) < 3, "file dates are kept (date sort)")
        evil = tmp / "evil.zip"
        import zipfile
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr(server.EXPORT_MANIFEST, "{}")
            z.writestr("../escaped.txt", "x")
        target_dir = tmp / "evil-target"
        target_dir.mkdir()
        (target_dir / "CLAUDE.md").write_text("mine")
        absolute = "/" + str(tmp / "abs-escaped.txt")[3:].replace("\\", "/")  # C:\…\abs-escaped.txt without "C:"
        with zipfile.ZipFile(evil, "a") as z:
            z.writestr(absolute, "x")
            z.writestr(".claude/settings.json", "{}")
            z.writestr("CLAUDE.md", "theirs")
            z.writestr("README.md", "new notes")
        server.import_parts(str(evil), target_dir)
        check(not (tmp / "escaped.txt").exists(), "a file can't write outside the folder")
        check(not (tmp / "abs-escaped.txt").exists() and not (target_dir / "abs-escaped.txt").exists(),
              "an absolute path in the file is ignored")
        check(not (target_dir / ".claude").exists(), "nothing is written into .claude")
        check((target_dir / "CLAUDE.md").read_text() == "mine" and (target_dir / "README.md").read_text() == "new notes",
              "an existing CLAUDE.md isn't overwritten (new notes are added)")

        # --- updates: a newer release downloads quietly, is checked, and is then "ready" (nothing is installed here)
        import hashlib
        fake_setup = tmp / "CAD-Parts-Setup-9.9.9.exe"
        fake_setup.write_bytes(b"pretend installer")
        digest = hashlib.sha256(fake_setup.read_bytes()).hexdigest()
        release = {"tag_name": "v9.9.9", "html_url": "https://example.invalid",
                   "assets": [{"name": fake_setup.name, "size": fake_setup.stat().st_size,
                               "browser_download_url": fake_setup.as_uri(), "digest": "sha256:" + digest}]}
        real_get = server.http_get
        # the app asked a minute ago (no update then); the release goes up; the next fresh check must see it
        server._update.update(t=time.time() - 60, v={"latest": server.APP_VERSION, "url": None})
        server.http_get = lambda url, timeout=20: json.dumps(release)
        v = call("GET", "/api/version?fresh=1")[1]
        check(v["newer"] and v["latest"] == "9.9.9" and not v["ready"], "a new release is noticed straight away", v)
        call("POST", "/api/setup?what=update-download")
        for _ in range(40):
            v = call("GET", "/api/version")[1]
            if v.get("ready") or (server._jobs.get("update-dl") or {}).get("state") == "failed":
                break
            time.sleep(0.25)
        check(v.get("ready"), "it downloads quietly and is ready", server._jobs.get("update-dl"))
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        server._ready.update(version=None, path=None)
        try:
            server._download_update("update-dl")
            bad = False
        except OSError:
            bad = True
        check(bad and not (server.APP_DATA / "updates" / fake_setup.name).exists(), "a download that doesn't match is thrown away")
        server.http_get = real_get
        server._update.update(t=0, v=None)

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
