import os
import threading
from typing import Any
import pystray
import voicemeeterlib
from PIL import Image
from comtypes import COMObject
from ctypes import POINTER, cast
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioEndpointVolumeCallback

import utils

config = utils.load_config()

KIND_ID = config['vm']['KIND_ID']
STRIPS_TO_UPDATE: list[int] = config['vm']['DEFAULT_STRIPS_TO_UPDATE']
BUSES_TO_UPDATE: list[int] = config['vm']['DEFAULT_BUSES_TO_UPDATE']
MAX_VOLUME_DB: float = config['vm']['MAX_VOLUME_DB']
MIN_VOLUME_DB: float = config['vm']['MIN_VOLUME_DB']
MATCH_MUTE_STATE: bool = config['vm']['MATCH_MUTE_STATE']
DISABLE_MULTI_SELECT_STRIPS: bool = config['vm'].get('DISABLE_MULTI_SELECT_STRIPS', False)
DISABLE_MULTI_SELECT_BUSES: bool = config['vm'].get('DISABLE_MULTI_SELECT_BUSES', False)
AVAILABLE_STRIPS = config['vm']['AVAILABLE_STRIPS']
AVAILABLE_BUSES = config['vm']['AVAILABLE_BUSES']
ROUND_TO_INTEGER = config.get('ROUND_TO_INTEGER', False)
shutdown_event = threading.Event()
current_vm: Any = None
current_volume_controller: Any = None
initial_selected_targets: list[tuple[str, int]] = [
    *[("strip", strip) for strip in STRIPS_TO_UPDATE],
    *[("bus", bus) for bus in BUSES_TO_UPDATE],
]

def normalize_selected_targets(targets: list[tuple[str, int]]) -> set[tuple[str, int]]:
    normalized_targets: set[tuple[str, int]] = set()
    selected_strip = False
    selected_bus = False

    for target_type, index in targets:
        if target_type == 'strip':
            if DISABLE_MULTI_SELECT_STRIPS:
                if selected_strip:
                    continue
                selected_strip = True
            normalized_targets.add((target_type, index))
        elif target_type == 'bus':
            if DISABLE_MULTI_SELECT_BUSES:
                if selected_bus:
                    continue
                selected_bus = True
            normalized_targets.add((target_type, index))

    return normalized_targets

selected_targets: set[tuple[str, int]] = normalize_selected_targets(initial_selected_targets)


class TrayController:
    def create_icon_image(self) -> Image.Image:
        icon_path = utils.resource_path("app.ico")
        with Image.open(icon_path) as image:
            return image.convert("RGBA").copy()

    @staticmethod
    def target_key(target_type: str, index: int) -> tuple[str, int]:
        return target_type, index

    def is_target_selected(self, target_type: str, index: int) -> bool:
        return self.target_key(target_type, index) in selected_targets

    def target_label(self, target_type: str, index: int) -> str:
        if current_vm is None:
            return f"{target_type.title()} {index}"

        if target_type == 'strip':
            label = current_vm.strip[index].label
        elif target_type == 'bus' and (
            (KIND_ID == "potato" and index < 5) or
            (KIND_ID == "banana" and index < 3)
        ):
            label = current_vm.bus[index].device.name
        else:
            label = current_vm.bus[index].label

        return f"{target_type.title()} {index}" if not label else f"{target_type.title()} {index} - {label}"

    def sync_windows_volume_to_target(self, target_type: str, index: int) -> None:
        if current_volume_controller is None:
            return

        gain_db = target_gain(target_type, index)
        scalar = (gain_db - MIN_VOLUME_DB) / (MAX_VOLUME_DB - MIN_VOLUME_DB)
        current_volume_controller.SetMasterVolumeLevelScalar(max(0.0, min(1.0, scalar)), None)

    def toggle_target(self, icon, target_type: str, index: int) -> None:
        key = self.target_key(target_type, index)

        if key in selected_targets:
            selected_targets.remove(key)
        else:
            if target_type == 'strip' and DISABLE_MULTI_SELECT_STRIPS:
                selected_targets.difference_update({target for target in selected_targets if target[0] == 'strip'})
            if target_type == 'bus' and DISABLE_MULTI_SELECT_BUSES:
                selected_targets.difference_update({target for target in selected_targets if target[0] == 'bus'})
            selected_targets.add(key)
            self.sync_windows_volume_to_target(target_type, index)

        icon.update_menu()

    def tray_menu_item(self, target_type: str, index: int) -> pystray.MenuItem:
        return pystray.MenuItem(
            self.target_label(target_type, index),
            lambda icon, _item: self.toggle_target(icon, target_type, index),
            checked=lambda _item, target_type=target_type, index=index: self.is_target_selected(target_type, index),
        )

    def build_tray_menu(self):
        yield pystray.MenuItem("Open config file", lambda _icon, _item: os.startfile(utils.config_path()))
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Strips", None, enabled=False)
        yield from (self.tray_menu_item('strip', strip) for strip in AVAILABLE_STRIPS)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Buses", None, enabled=False)
        yield from (self.tray_menu_item('bus', bus) for bus in AVAILABLE_BUSES)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Exit VBAB", lambda icon, _item: self.exit_app(icon))

    @staticmethod
    def exit_app(icon) -> None:
        shutdown_event.set()
        icon.stop()


tray_controller = TrayController()


def apply_volume_to_target(target_type: str, index: int, volume_db: float, muted: bool) -> None:
    if current_vm is None:
        return

    target = current_vm.strip[index] if target_type == 'strip' else current_vm.bus[index]
    target.gain = volume_db
    if MATCH_MUTE_STATE:
        target.mute = muted


def target_gain(target_type: str, index: int) -> float:
    if current_vm is None:
        return 0.0

    target = current_vm.strip[index] if target_type == 'strip' else current_vm.bus[index]
    return float(target.gain)


def sync_windows_volume_to_target(target_type: str, index: int) -> None:
    tray_controller.sync_windows_volume_to_target(target_type, index)


class VolumeCallback(COMObject):
    _com_interfaces_ = [IAudioEndpointVolumeCallback]

    def __init__(self, vm):
        super().__init__()
        self._vm = vm
        self._last_gain = None
        self._last_mute_state = None
        
    def OnNotify(self, pNotify):
        if pNotify:
            notification_data = pNotify.contents

            normalized_volume = max(0.0, min(1.0, float(notification_data.fMasterVolume)))
            volume_db = round(MIN_VOLUME_DB + normalized_volume * (MAX_VOLUME_DB - MIN_VOLUME_DB), 0 if ROUND_TO_INTEGER else 1)
            muted = bool(notification_data.bMuted)

            if selected_targets and (
                self._last_gain != volume_db or self._last_mute_state != muted
            ):
                for target_type, index in selected_targets:
                    apply_volume_to_target(target_type, index, volume_db, muted)

                self._last_gain = volume_db
                self._last_mute_state = muted
        return 0


def main():
    global current_vm, current_volume_controller

    device = AudioUtilities.GetSpeakers()
    volume_controller = cast(device.EndpointVolume, POINTER(IAudioEndpointVolume)) # type: ignore

    with voicemeeterlib.api(KIND_ID) as vm:
        current_vm = vm
        current_volume_controller = volume_controller
        callback_instance = VolumeCallback(vm)
        tray_icon = pystray.Icon("VBAudioBridge", tray_controller.create_icon_image(), "VBAudioBridge", pystray.Menu(tray_controller.build_tray_menu))
        volume_controller.RegisterControlChangeNotify(callback_instance) # type: ignore

        try:
            tray_icon.run()
        finally:
            volume_controller.UnregisterControlChangeNotify(callback_instance) # type: ignore
            shutdown_event.set()


if __name__ == '__main__':
    main()
