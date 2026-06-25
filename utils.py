import json
import os
import shutil
import sys
from pathlib import Path


APP_NAME = "VBAudioBridge"


def resource_path(name: str) -> Path:
    if getattr(sys, 'frozen', False):
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base_path = Path(__file__).resolve().parent

    return base_path / name


def appdata_path() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata)

    return Path.home() / "AppData" / "Local"


def config_path() -> Path:
    return appdata_path() / APP_NAME / "config.json"


def ensure_config_file() -> Path:
    destination_path = config_path()
    if destination_path.exists():
        return destination_path

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_path = resource_path("default_config.json")
    shutil.copy2(source_path, destination_path)
    return destination_path


def load_config() -> dict:
    return json.loads(ensure_config_file().read_text())
