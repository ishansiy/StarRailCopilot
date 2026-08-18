import functools
import importlib
import os
import sys
import types
import unittest
from unittest import mock

import module.device.managed_screenshot_crop  # Keep NumPy modules stable across import stubs.


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Config:
    @staticmethod
    def when(**_conditions):
        return lambda function: function


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _load_command_builder():
    adb_error = type("AdbError", (Exception,), {})
    adbutils_errors = _module("adbutils.errors", AdbError=adb_error)
    stubs = {
        "adbutils": _module("adbutils", errors=adbutils_errors),
        "adbutils.errors": adbutils_errors,
        "uiautomator2": _module(
            "uiautomator2",
            _Service=type("_Service", (), {}),
        ),
        "websockets": _module("websockets"),
        "module.base.decorator": _module(
            "module.base.decorator",
            Config=_Config,
            cached_property=functools.cached_property,
            del_cached_property=lambda *args, **kwargs: None,
            has_cached_property=lambda *args, **kwargs: False,
        ),
        "module.base.timer": _module(
            "module.base.timer",
            Timer=type("Timer", (), {}),
        ),
        "module.base.utils": _module("module.base.utils"),
        "module.device.connection": _module(
            "module.device.connection",
            Connection=type("Connection", (), {}),
        ),
        "module.device.method.utils": _module(
            "module.device.method.utils",
            RETRY_TRIES=3,
            handle_adb_error=lambda *args, **kwargs: False,
            handle_unknown_host_service=lambda *args, **kwargs: False,
            retry_sleep=lambda *args, **kwargs: 0,
        ),
        "module.exception": _module(
            "module.exception",
            RequestHumanTakeover=type("RequestHumanTakeover", (Exception,), {}),
            ScriptError=type("ScriptError", (Exception,), {}),
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
    }

    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("module.device.method.minitouch", None)
        module = importlib.import_module("module.device.method.minitouch")
        return module.CommandBuilder, stubs["module.exception"].ScriptError


class ManagedTouchCropTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.CommandBuilder, cls.ScriptError = _load_command_builder()

    def test_maatouch_command_preserves_asset_x_inside_right_cropped_canvas(self):
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            builder = self.CommandBuilder(
                device,
                handle_orientation=False,
            )
            builder.down(1200, 360)

        command = builder.commands[0]
        self.assertEqual((command.x, command.y), (1200, 360))
        self.assertEqual(builder.to_minitouch(), "d 0 1200 360 100\n")
        self.assertEqual((builder.max_x, builder.max_y), (1334, 720))

    def test_crop_rejects_http_device_before_emitting_touch_command(self):
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=True),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            builder = self.CommandBuilder(device, handle_orientation=False)
            with self.assertRaises(self.ScriptError):
                builder.down(1200, 360)

        self.assertEqual(builder.commands, [])

    def test_crop_rejects_minitouch_orientation_transform(self):
        device = types.SimpleNamespace(
            max_x=720,
            max_y=1334,
            orientation=1,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            builder = self.CommandBuilder(device, handle_orientation=True)
            with self.assertRaises(self.ScriptError):
                builder.down(1200, 360)

        self.assertEqual(builder.commands, [])

    def test_crop_rejects_stale_portrait_maatouch_axes(self):
        device = types.SimpleNamespace(
            max_x=720,
            max_y=1334,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            builder = self.CommandBuilder(device, handle_orientation=False)
            with self.assertRaises(self.ScriptError):
                builder.down(1200, 360)

        self.assertEqual(builder.commands, [])


if __name__ == "__main__":
    unittest.main()
