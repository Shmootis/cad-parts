"""Run a build123d part script fast, using the warm worker (cadworker.py).

    uv run --python 3.12 <app folder>/run.py <part>/<part>_vN.py [args...]   (from the parts folder)

Behaves like `python script.py`: prints the script's output and exits non-zero on errors.
Starts the worker if it isn't running (the first run then waits ~10 s while build123d loads).
Standard library only, so this launcher itself starts in a blink.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PORT = 8766
HERE = Path(__file__).resolve().parent


def call(req: dict, timeout: float = 600) -> dict:
    with socket.create_connection(("127.0.0.1", PORT), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        f = s.makefile("rb")
        return json.loads(f.readline() or b"{}")


def ensure_worker() -> None:
    try:
        call({"ping": True}, timeout=2)
        return
    except OSError:
        pass
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hidden console (not detached: then its children would pop up windows)
    subprocess.Popen(["uv", "run", "--python", "3.12", "--with", "build123d", "python", str(HERE / "cadworker.py")],
                     cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            call({"ping": True}, timeout=2)
            return
        except OSError:
            continue
    sys.exit("cadworker did not start (try: uv run --python 3.12 --with build123d python cadworker.py, in the app folder)")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--warm":  # just make sure the worker is up (used by the dashboard at startup)
        ensure_worker()
        return
    script = str(Path(sys.argv[1]).resolve())
    if not os.path.isfile(script):
        sys.exit(f"no such script: {script}")
    ensure_worker()
    res = call({"script": script, "args": sys.argv[2:], "cwd": os.getcwd()})
    if res.get("out"):
        sys.stdout.write(res["out"])
    if res.get("err"):
        sys.stderr.write(res["err"])
    print(f"[ran in {res.get('secs', '?')} s on the warm build123d worker]", file=sys.stderr)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
