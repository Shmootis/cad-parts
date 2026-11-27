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


def call(req: dict, timeout: float | None = None) -> dict:
    """One request. No timeout by default: the worker answers in order, so a request queued behind a
    long build simply waits for it (the worker binds its port before loading, so it is never "missing")."""
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(req) + "\n").encode())
        f = s.makefile("rb")
        return json.loads(f.readline() or b"{}")


def port_taken() -> bool:
    """A worker owns the port (loading build123d, busy with a build, or idle). Plain TCP connect, no request."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2):
            return True
    except OSError:
        return False


def ensure_worker() -> None:
    if port_taken():
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hidden console (not detached: then its children would pop up windows)
    subprocess.Popen(["uv", "run", "--python", "3.12", "--with", "build123d", "python", str(HERE / "cadworker.py")],
                     cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    deadline = time.time() + 900  # the first start may download Python and build123d before the worker can bind
    while time.time() < deadline:
        time.sleep(0.5)
        if port_taken():
            return
    sys.exit("cadworker did not start (try: uv run --python 3.12 --with build123d python cadworker.py, in the app folder)")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--warm":  # make sure the worker is up and build123d is loaded (used by the dashboard at startup)
        ensure_worker()
        try:
            res = call({"ping": True})  # answered once build123d is loaded (and any queued build is done)
        except OSError as e:
            sys.exit(f"cadworker stopped while starting: {e}")
        sys.exit(0 if res.get("pong") else "cadworker did not answer")
    script = str(Path(sys.argv[1]).resolve())
    if not os.path.isfile(script):
        sys.exit(f"no such script: {script}")
    ensure_worker()
    try:
        res = call({"script": script, "args": sys.argv[2:], "cwd": os.getcwd()})
    except (OSError, ValueError) as e:
        sys.exit(f"the build123d worker stopped before answering ({e}); run the script again")
    if res.get("out"):
        sys.stdout.write(res["out"])
    if res.get("err"):
        sys.stderr.write(res["err"])
    print(f"[ran in {res.get('secs', '?')} s on the warm build123d worker]", file=sys.stderr)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
