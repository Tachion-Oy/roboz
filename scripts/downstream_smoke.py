"""Installed RoboSprawl HTTP run/reply/completion contract; no service credentials."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request


def main() -> None:
    import robosprawl
    import roboz
    import roboz_openai
    import roboz_shed

    for module in (robosprawl, roboz, roboz_shed, roboz_openai):
        assert (
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    def request(path: str, data: dict | None = None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            base + path, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)

    with Path("backend.log").open("w") as log:
        backend = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "uvicorn",
                "robosprawl.api.app:mock_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        stream = None
        try:
            deadline = time.monotonic() + 30
            while True:
                assert backend.poll() is None, "Backend exited; inspect backend.log"
                try:
                    request("/ready")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Backend readiness")
                    time.sleep(0.05)
            request("/projects", {"name": "installed-contract"})
            run_id = request("/run/create", {"project": "installed-contract"})["run_id"]
            frames = []
            errors = []

            def consume():
                try:
                    with urllib.request.urlopen(
                        base + f"/run/{run_id}/stream", timeout=30
                    ) as response:
                        for line in response:
                            frames.append(line.decode())
                except Exception as error:
                    errors.append(error)

            stream = threading.Thread(target=consume, daemon=True)
            stream.start()
            answered = set()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = request(f"/run/{run_id}")
                assert state["status"] not in {"failed", "cancelled"}, state
                if state["status"] == "completed":
                    break
                if state["status"] == "awaiting_user_input":
                    prompt = state["current_prompt_id"]
                    if prompt not in answered:
                        assert request(
                            f"/run/{run_id}/reply", {"content": "Continue and finish."}
                        ) == {"ok": True}
                        answered.add(prompt)
                time.sleep(0.05)
            else:
                raise TimeoutError(f"Run did not complete: {state}")
            stream.join(timeout=5)
            assert not stream.is_alive() and not errors, errors
            assert len(answered) == 2, answered
            assert "Hello, World!" in "".join(frames)
            assert "completed" in "".join(frames)
            assert list(Path("hub_data/projects/installed-contract").rglob("*.json"))
            print(
                "PASS installed imports, HTTP create/stream/reply, specialist, completion and persistence"
            )
        finally:
            backend.terminate()
            try:
                backend.wait(timeout=8)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait(timeout=5)
            if stream:
                stream.join(timeout=5)


if __name__ == "__main__":
    main()
