"""Load the native UI asset dependencies shipped with a platform-specific zipapp."""
import hashlib
import io
import json
from pathlib import Path
import os
import shutil
import sys
import sysconfig
import tempfile
from zipfile import ZipFile

from deadlock_tools import config


def load_asset_tools():
    if config.ZIPAPP:
        with ZipFile(config.ZIPAPP) as app:
            if "ui-assets.json" in app.namelist():
                manifest = json.loads(app.read("ui-assets.json"))
                if (manifest["python"], manifest["platform"]) != (sys.implementation.cache_tag, sysconfig.get_platform()):
                    raise ImportError("The bundled UnityPy files require Python %s on %s."
                                      % (manifest["python"], manifest["platform"]))
                cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "deadlock-tools"
                target = cache / ("ui-assets-" + manifest["sha256"])
                if not target.is_dir():
                    data = app.read("ui-assets.zip")
                    if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                        raise ImportError("The bundled UnityPy archive failed its checksum.")
                    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
                    staging = Path(tempfile.mkdtemp(prefix=".ui-assets-", dir=cache))
                    try:
                        with ZipFile(io.BytesIO(data)) as vendor:
                            vendor.extractall(staging)
                        try:
                            staging.rename(target)
                        except OSError:
                            if not target.is_dir():
                                raise
                    finally:
                        shutil.rmtree(staging, ignore_errors=True)
                if str(target) not in sys.path:
                    sys.path.insert(0, str(target))

    import UnityPy
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

    return UnityPy, TypeTreeGenerator
