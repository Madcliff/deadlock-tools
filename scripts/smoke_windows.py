"""Exercise the Windows distribution from a clean folder without game files."""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import mkdtemp
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler


def remove_tree(directory, seconds=30):
    """Delete the test folder. Windows can keep the copied .exe locked for a moment after the editor is killed
    (the one-file launcher's child exiting, or Defender scanning it), so retry before giving up; a folder left in
    the runner's temp directory is not a test failure."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            shutil.rmtree(directory)
            return
        except FileNotFoundError:
            return
        except PermissionError as error:
            if time.monotonic() >= deadline:
                print(f"warning: could not remove {directory}: {error}")
                return
            time.sleep(1)


def main():
    executable = Path(sys.argv[1]).resolve()
    directory = Path(mkdtemp(prefix="Deadlock Tools test "))
    try:
        app = directory / executable.name
        shutil.copy2(executable, app)
        subprocess.run([str(app), "weapons", "-h"], cwd=directory, check=True, timeout=60)
        subprocess.run([str(app), "config", "--work", str(directory / "mod work")],
                       cwd=directory, check=True, timeout=60)
        settings = json.loads((directory / "deadlock.json").read_text())
        assert Path(settings["work"]) == directory / "mod work"

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with subprocess.Popen([str(app), "gui", "--no-browser", "--port", str(port)],
                              cwd=directory, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP) as gui:
            try:
                opener = build_opener(ProxyHandler({}))
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        with opener.open(url + "/", timeout=2) as page:
                            assert b"Deadlock Ship Editor" in page.read()
                        with opener.open(url + "/api/config", timeout=2) as response:
                            assert Path(json.load(response)["work"]) == directory / "mod work"
                        break
                    except URLError:
                        if gui.poll() is not None:
                            raise RuntimeError(f"editor exited with code {gui.returncode}") from None
                        time.sleep(0.5)
                else:
                    raise RuntimeError("the standalone editor did not respond")
            finally:
                # The one-file launcher has an extraction parent and an app child.
                subprocess.run(["taskkill", "/PID", str(gui.pid), "/T", "/F"],
                               capture_output=True, check=False, timeout=15)
                gui.wait(timeout=15)
    finally:
        remove_tree(directory)


if __name__ == "__main__":
    main()
