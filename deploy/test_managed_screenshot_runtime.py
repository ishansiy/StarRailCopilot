import functools
import importlib
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

import module.device.managed_screenshot_crop  # Keep the real crop parser loaded.


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Timer:
    def __init__(self, *_args, **_kwargs):
        self.limit = 0

    def wait(self):
        pass

    def reset(self):
        pass


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _load_screenshot_class():
    bases = {
        name: type(name, (), {})
        for name in (
            "Adb",
            "WSA",
            "DroidCast",
            "AScreenCap",
            "Scrcpy",
            "NemuIpc",
            "LDOpenGL",
        )
    }
    cv2 = _module(
        "cv2",
        ROTATE_90_COUNTERCLOCKWISE=0,
        ROTATE_180=1,
        ROTATE_90_CLOCKWISE=2,
        rotate=lambda image, _mode: image,
    )
    request_human_takeover = type("RequestHumanTakeover", (Exception,), {})
    stubs = {
        "cv2": cv2,
        "module.base.decorator": _module(
            "module.base.decorator",
            cached_property=functools.cached_property,
        ),
        "module.base.timer": _module("module.base.timer", Timer=_Timer),
        "module.base.utils": _module(
            "module.base.utils",
            get_color=lambda *_args, **_kwargs: (1, 1, 1),
            image_size=lambda image: (image.shape[1], image.shape[0]),
            limit_in=lambda value, *_args: value,
            save_image=lambda *_args, **_kwargs: None,
        ),
        "module.device.method.adb": _module(
            "module.device.method.adb", Adb=bases["Adb"]
        ),
        "module.device.method.ascreencap": _module(
            "module.device.method.ascreencap", AScreenCap=bases["AScreenCap"]
        ),
        "module.device.method.droidcast": _module(
            "module.device.method.droidcast", DroidCast=bases["DroidCast"]
        ),
        "module.device.method.ldopengl": _module(
            "module.device.method.ldopengl", LDOpenGL=bases["LDOpenGL"]
        ),
        "module.device.method.nemu_ipc": _module(
            "module.device.method.nemu_ipc", NemuIpc=bases["NemuIpc"]
        ),
        "module.device.method.scrcpy": _module(
            "module.device.method.scrcpy", Scrcpy=bases["Scrcpy"]
        ),
        "module.device.method.wsa": _module(
            "module.device.method.wsa", WSA=bases["WSA"]
        ),
        "module.exception": _module(
            "module.exception",
            RequestHumanTakeover=request_human_takeover,
            ScriptError=type("ScriptError", (Exception,), {}),
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
    }

    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("module.device.screenshot", None)
        screenshot = importlib.import_module("module.device.screenshot").Screenshot
        return screenshot, request_human_takeover


class ManagedScreenshotRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Screenshot, cls.RequestHumanTakeover = _load_screenshot_class()

    def test_portrait_crop_frame_is_not_rotated_by_stale_orientation(self):
        image = np.zeros((1334, 720, 3), dtype=np.uint8)
        device = types.SimpleNamespace(orientation=1)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            normalized = self.Screenshot._handle_orientated_image(device, image)

        self.assertIs(normalized, image)
        self.assertEqual(normalized.shape, (1334, 720, 3))

    def test_cached_landscape_check_does_not_admit_later_portrait_frame(self):
        device = types.SimpleNamespace(
            _screen_size_checked=True,
            image=np.zeros((1334, 720, 3), dtype=np.uint8),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            ready = self.Screenshot.check_screen_size(device)

        self.assertFalse(ready)

    def test_repeated_portrait_frames_fail_closed_before_template_matching(self):
        calls = []
        interval = _Timer()
        portrait = np.zeros((1334, 720, 3), dtype=np.uint8)
        device = types.SimpleNamespace(
            _screenshot_interval=interval,
            screenshot_method_override="",
            screenshot_methods={"ADB": lambda: calls.append(1) or portrait},
            screenshot_adb=lambda: calls.append(1) or portrait,
            config=types.SimpleNamespace(
                Emulator_ScreenshotMethod="ADB",
                Error_SaveError=False,
            ),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaisesRegex(
                self.RequestHumanTakeover,
                "landscape screenshot",
            ):
                self.Screenshot.screenshot(device)

        self.assertEqual(len(calls), 5)
        self.assertIsNone(device._managed_crop_landscape_frame_at)


if __name__ == "__main__":
    unittest.main()
