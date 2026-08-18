import functools
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


def _managed_screenshot_crop_from_environment():
    raw = os.environ.get("SRC_ADB_MANAGED_SCREEN_CROP", "").strip()
    if raw == "invalid":
        raise ValueError("invalid managed screenshot crop")
    return object() if raw else None


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Config:
    functions = {}

    @classmethod
    def when(cls, **conditions):
        def decorator(function):
            records = cls.functions.setdefault(function.__name__, [])
            records.append((conditions, function))

            @functools.wraps(function)
            def wrapper(self, *args, **kwargs):
                for options, candidate in cls.functions[function.__name__]:
                    if all(
                        getattr(self.config, key) == value
                        for key, value in options.items()
                    ):
                        return candidate(self, *args, **kwargs)
                return function(self, *args, **kwargs)

            return wrapper

        return decorator


def _load_adb_module():
    adb_error = type("AdbError", (Exception,), {})
    stubs = {
        "cv2": _module(
            "cv2",
            COLOR_BGRA2BGR=0,
            COLOR_BGR2RGB=1,
            IMREAD_COLOR=2,
            cvtColor=lambda image, *_args, **_kwargs: image,
            imdecode=lambda image, *_args, **_kwargs: image,
        ),
        "adbutils.errors": _module("adbutils.errors", AdbError=adb_error),
        "lxml": _module("lxml", etree=types.SimpleNamespace()),
        "module.base.decorator": _module(
            "module.base.decorator",
            Config=_Config,
        ),
        "module.config.server": _module(
            "module.config.server",
            DICT_PACKAGE_TO_ACTIVITY={},
        ),
        "module.device.connection": _module(
            "module.device.connection",
            Connection=object,
        ),
        "module.device.managed_screenshot_crop": _module(
            "module.device.managed_screenshot_crop",
            managed_screenshot_crop_from_environment=(
                _managed_screenshot_crop_from_environment
            ),
        ),
        "module.device.method.remove_warning": _module(
            "module.device.method.remove_warning",
            remove_screenshot_warning=lambda data: data,
        ),
        "module.device.method.utils": _module(
            "module.device.method.utils",
            ImageTruncated=type("ImageTruncated", (Exception,), {}),
            PackageNotInstalled=type("PackageNotInstalled", (Exception,), {}),
            RETRY_TRIES=5,
            handle_adb_error=lambda error: "timeout" in str(error).lower(),
            handle_unknown_host_service=lambda _error: False,
            retry_sleep=lambda _trial: 0,
        ),
        "module.exception": _module(
            "module.exception",
            RequestHumanTakeover=type("RequestHumanTakeover", (Exception,), {}),
            ScriptError=type("ScriptError", (Exception,), {}),
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
    }

    _Config.functions = {}
    name = "test_managed_screenshot_timeout_adb"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "module" / "device" / "method" / "adb.py",
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class ManagedScreenshotTimeoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adb_module = _load_adb_module()
        cls.Adb = cls.adb_module.Adb

    def test_managed_crop_screencap_gets_45_second_timeout(self):
        calls = []

        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

            def adb_shell(self, command, **kwargs):
                calls.append((command, kwargs))
                return b"x" * 500

            def _Adb__process_screenshot(self, data):
                return data

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            Device().screenshot_adb()

        self.assertEqual(
            calls,
            [(["screencap", "-p"], {"stream": True, "timeout": 45})],
        )

    def test_managed_crop_screencap_fails_closed_within_two_capture_deadlines(self):
        timeouts = []
        reconnects = []
        adb_module = self.adb_module

        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

            def adb_shell(self, _command, **kwargs):
                timeout = kwargs["timeout"]
                timeouts.append(timeout)
                raise adb_module.AdbError("adb total read timeout")

            def adb_reconnect(self):
                reconnects.append(1)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ), mock.patch.object(adb_module.time, "sleep", return_value=None):
            with self.assertRaises(adb_module.RequestHumanTakeover):
                Device().screenshot_adb()

        self.assertEqual(timeouts, [45, 45])
        self.assertLessEqual(sum(timeouts), 90)
        self.assertEqual(len(reconnects), 1)

    def test_regular_screencap_keeps_five_ten_second_attempts(self):
        timeouts = []
        reconnects = []
        adb_module = self.adb_module

        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

            def adb_shell(self, _command, **kwargs):
                timeout = kwargs["timeout"]
                timeouts.append(timeout)
                raise adb_module.AdbError("adb total read timeout")

            def adb_reconnect(self):
                reconnects.append(1)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": ""},
        ), mock.patch.object(adb_module.time, "sleep", return_value=None):
            with self.assertRaises(adb_module.RequestHumanTakeover):
                Device().screenshot_adb()

        self.assertEqual(timeouts, [10, 10, 10, 10, 10])
        self.assertEqual(len(reconnects), 4)

    def test_regular_screencap_keeps_10_second_timeout(self):
        calls = []

        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

            def adb_shell(self, command, **kwargs):
                calls.append((command, kwargs))
                return b"x" * 500

            def _Adb__process_screenshot(self, data):
                return data

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SRC_ADB_MANAGED_SCREEN_CROP", None)
            Device().screenshot_adb()

        self.assertEqual(
            calls,
            [(["screencap", "-p"], {"stream": True, "timeout": 10})],
        )

    def test_http_screencap_keeps_five_regular_attempts_in_managed_environment(self):
        calls = []
        reconnects = []
        adb_module = self.adb_module

        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=True)

            def adb_shell(self, command, **kwargs):
                calls.append((command, kwargs.get("timeout", 10)))
                raise adb_module.AdbError("adb total read timeout")

            def adb_reconnect(self):
                reconnects.append(1)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ), mock.patch.object(adb_module.time, "sleep", return_value=None):
            with self.assertRaises(adb_module.RequestHumanTakeover):
                Device().screenshot_adb()

        self.assertEqual(calls, [(["screencap"], 10)] * 5)
        self.assertEqual(len(reconnects), 4)

    def test_managed_environment_does_not_reduce_click_retries(self):
        calls = []
        reconnects = []
        adb_module = self.adb_module

        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

            def adb_shell(self, command, **_kwargs):
                calls.append(command)
                raise adb_module.AdbError("adb total read timeout")

            def adb_reconnect(self):
                reconnects.append(1)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ), mock.patch.object(adb_module.time, "sleep", return_value=None):
            with self.assertRaises(adb_module.RequestHumanTakeover):
                Device().click_adb(100, 200)

        self.assertEqual(calls, [["input", "tap", 100, 200]] * 5)
        self.assertEqual(len(reconnects), 4)

    def test_invalid_managed_crop_still_fails_through_screenshot_retry_contract(self):
        class Device(self.Adb):
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "invalid"},
        ), mock.patch.object(self.adb_module.time, "sleep", return_value=None):
            with self.assertRaises(self.adb_module.RequestHumanTakeover):
                Device().screenshot_adb()


if __name__ == "__main__":
    unittest.main()
