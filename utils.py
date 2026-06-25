import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


APP_NAME = "VBAudioBridge"
DEFAULT_CONFIG_PATH = "default_config.json"


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
    source_path = resource_path(DEFAULT_CONFIG_PATH)
    shutil.copy2(source_path, destination_path)
    return destination_path


def _load_default_config() -> dict[str, Any]:
    return json.loads(resource_path(DEFAULT_CONFIG_PATH).read_text())


def _merge_missing_keys(current_value: Any, default_value: Any) -> tuple[Any, bool]:
    if isinstance(default_value, dict):
        if not isinstance(current_value, dict):
            return default_value, True

        merged_value = dict(current_value)
        changed = False
        for key, default_item in default_value.items():
            if key in merged_value:
                merged_item, item_changed = _merge_missing_keys(merged_value[key], default_item)
                if item_changed:
                    merged_value[key] = merged_item
                    changed = True
            else:
                merged_value[key] = default_item
                changed = True

        return merged_value, changed

    return current_value, False


def load_config() -> dict:
    default_config = _load_default_config()
    config_file_path = ensure_config_file()

    try:
        current_config = json.loads(config_file_path.read_text())
    except json.JSONDecodeError:
        config_file_path.write_text(json.dumps(default_config, indent=4) + "\n")
        return default_config

    merged_config, changed = _merge_missing_keys(current_config, default_config)
    if changed:
        config_file_path.write_text(json.dumps(merged_config, indent=4) + "\n")
        return merged_config

    return current_config
