import importlib
import os
import sys
import time
import types
import unittest
from unittest import mock


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Timer:
    def __init__(self, *_args, **_kwargs):
        pass

    def start(self):
        return self


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _load_device_class():
    script_error = type("ScriptError", (Exception,), {})
    exception_names = [
        "EmulatorNotRunningError",
        "GameNotRunningError",
        "GameStuckError",
        "GameTooManyClickError",
        "RequestHumanTakeover",
    ]
    exception_types = {
        name: type(name, (Exception,), {}) for name in exception_names
    }
    exception_types["ScriptError"] = script_error

    screenshot = type("Screenshot", (), {})
    control = type("Control", (), {})
    app_control = type("AppControl", (), {})
    etree = _module("lxml.etree")
    stubs = {
        "lxml": _module("lxml", etree=etree),
        "lxml.etree": etree,
        "module.base.timer": _module("module.base.timer", Timer=_Timer),
        "module.device.app_control": _module(
            "module.device.app_control", AppControl=app_control
        ),
        "module.device.control": _module("module.device.control", Control=control),
        "module.device.env": _module("module.device.env", IS_WINDOWS=False),
        "module.device.pkg_resources": _module(
            "module.device.pkg_resources", get_distribution=lambda *_args: None
        ),
        "module.device.screenshot": _module(
            "module.device.screenshot", Screenshot=screenshot
        ),
        "module.exception": _module("module.exception", **exception_types),
        "module.logger": _module("module.logger", logger=_Logger()),
    }

    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("module.device.device", None)
        module = importlib.import_module("module.device.device")
        return (
            module.Device,
            script_error,
            exception_types["RequestHumanTakeover"],
            screenshot,
        )


class _ControlHarness:
    def __init__(self, captured_at):
        self._managed_crop_landscape_frame_at = captured_at
        self._managed_crop_frame_generation = 1
        self.calls = []

    @property
    def maatouch_builder(self):
        raise AssertionError("control guard must not join MaaTouch init")

    def get_orientation(self):
        raise AssertionError("control guard must not query remote orientation")

    def stuck_record_clear(self):
        self.calls.append("clear")

    def click_record_add(self, button):
        self.calls.append(("add", button))

    def click_record_check(self):
        self.calls.append("check")


class ManagedControlGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (
            cls.Device,
            cls.ScriptError,
            cls.RequestHumanTakeover,
            cls.ScreenshotBase,
        ) = _load_device_class()

    def test_recent_landscape_frame_authorizes_without_orientation_query(self):
        harness = _ControlHarness(time.monotonic())

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.Device.handle_control_check(harness, "GUIDE")

        self.assertEqual(harness.calls, ["clear", ("add", "GUIDE"), "check"])

    def test_same_frame_cannot_authorize_a_second_distinct_control(self):
        harness = _ControlHarness(time.monotonic())

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.Device.handle_control_check(harness, "FIRST")
            with self.assertRaisesRegex(self.ScriptError, "new landscape"):
                self.Device.handle_control_check(harness, "SECOND")

        self.assertEqual(harness._managed_crop_touch_budget, 1)

    def test_stale_landscape_frame_rejects_touch(self):
        harness = _ControlHarness(time.monotonic() - 16)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaisesRegex(self.ScriptError, "recent landscape"):
                self.Device.handle_control_check(harness, "GUIDE")

        self.assertEqual(harness.calls, [])

    def test_managed_screenshot_rejection_does_not_run_fallback_benchmark(self):
        device = self.Device.__new__(self.Device)
        device.stuck_record_check = mock.Mock()
        device.run_simple_screenshot_benchmark = mock.Mock()
        device.ascreencap_available = False
        rejection = self.RequestHumanTakeover(
            "Managed phone did not provide a landscape screenshot"
        )

        with mock.patch.object(
            self.ScreenshotBase,
            "screenshot",
            side_effect=rejection,
            create=True,
        ), mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaises(self.RequestHumanTakeover) as raised:
                self.Device.screenshot(device)

        self.assertIs(raised.exception, rejection)
        device.run_simple_screenshot_benchmark.assert_not_called()

    def test_unmanaged_screenshot_keeps_existing_fallback_behavior(self):
        device = self.Device.__new__(self.Device)
        device.stuck_record_check = mock.Mock()
        device.run_simple_screenshot_benchmark = mock.Mock()
        device.ascreencap_available = False
        device.image = "fallback-image"
        calls = []

        def screenshot(_device):
            calls.append("capture")
            if len(calls) == 1:
                raise self.RequestHumanTakeover("ordinary screenshot failure")

        with mock.patch.object(
            self.ScreenshotBase,
            "screenshot",
            new=screenshot,
            create=True,
        ), mock.patch.dict(os.environ, {}, clear=True):
            result = self.Device.screenshot(device)

        self.assertEqual(result, "fallback-image")
        self.assertEqual(calls, ["capture", "capture"])
        device.run_simple_screenshot_benchmark.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
