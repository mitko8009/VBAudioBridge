import json
import copy
import os
import subprocess
import shutil
import psutil
import sys
import threading
import logging
import logging.config
from pathlib import Path
from typing import Any
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager


APP_NAME = "VBAudioBridge"
DEFAULT_CONFIG_PATH = "default_config.json"
VB_PROCESS_NAMES = {
    "voicemeeter.exe",
    "voicemeeter_x64.exe",
    "voicemeeter8.exe",
    "voicemeeter8x64.exe",
    "voicemeeterpro.exe",
    "voicemeeterpro_x64.exe",
}


async def control_media(play: bool):
    manager = await MediaManager.request_async()
    sessions = manager.get_sessions()
    
    if not sessions:
        logging.info("No active media sessions found.")
        return

    session = sessions[0]
    status = session.get_playback_info().playback_status

    if play and status == 5:
        await session.try_play_async()
    elif not play and status == 4:
        await session.try_pause_async()


async def play_next_track():
    manager = await MediaManager.request_async()
    sessions = manager.get_sessions()
    
    if not sessions:
        logging.info("No active media sessions found.")
        return

    session = sessions[0]
    await session.try_skip_next_async()


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


def is_voicemeeter_running() -> bool:
    for process in psutil.process_iter(['name']):
        try: 
            if process.info['name'] in VB_PROCESS_NAMES:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        
    return False


def setup_logger(config: dict) -> logging.Logger:
    logging_config = copy.deepcopy(config)

    log_file_path = resolve_log_file_path(logging_config)
    file_handler = logging_config.get("handlers", {}).get("file")
    if isinstance(file_handler, dict):
        file_handler["filename"] = str(log_file_path)
        file_handler["mode"] = "w"

    logging.config.dictConfig(logging_config)
    logger = logging.getLogger(APP_NAME)

    def log_uncaught_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    def log_thread_exception(args):
        logger.error(
            "Uncaught thread exception in %s",
            args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_uncaught_exception
    threading.excepthook = log_thread_exception

    return logger


def resolve_log_file_path(logging_config: dict) -> Path:
    file_handler = logging_config.get("handlers", {}).get("file")
    log_file_name = "app.log"

    if isinstance(file_handler, dict):
        log_file_name = file_handler.get("filename", log_file_name)

    log_file_path = config_path().parent / log_file_name
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    return log_file_path


def open_console(log_file_path: Path) -> None:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    log_file_path.touch(exist_ok=True)

    command = (
        f"Get-Content -Path '{str(log_file_path).replace("'", "''")}' "
        "-Tail 50 -Wait"
    )

    subprocess.Popen(
        ["powershell", "-NoExit", "-Command", command],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
