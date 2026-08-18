import functools
import importlib
import os
import sys
import time
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


class _TouchTimer:
    def __init__(self, *_args, **_kwargs):
        pass

    def start(self):
        return self

    def reached(self):
        return False


class _TouchConnection:
    def get_orientation(self):
        self.orientation = getattr(self, "orientation", 0)
        return self.orientation


def _load_touch_classes():
    adb_error = type("AdbError", (Exception,), {})
    adbutils_errors = _module("adbutils.errors", AdbError=adb_error)
    stubs = {
        "adbutils": _module("adbutils", errors=adbutils_errors),
        "adbutils.errors": adbutils_errors,
        "uiautomator2": _module(
            "uiautomator2",
            _Service=type("_Service", (), {}),
        ),
        "websockets": _module(
            "websockets",
            WebSocketClientProtocol=type("WebSocketClientProtocol", (), {}),
        ),
        "module.base.decorator": _module(
            "module.base.decorator",
            Config=_Config,
            cached_property=functools.cached_property,
            del_cached_property=lambda *args, **kwargs: None,
            has_cached_property=lambda *args, **kwargs: False,
        ),
        "module.base.timer": _module(
            "module.base.timer",
            Timer=_TouchTimer,
        ),
        "module.base.utils": _module("module.base.utils"),
        "module.device.connection": _module(
            "module.device.connection",
            Connection=_TouchConnection,
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
        sys.modules.pop("module.device.method.maatouch", None)
        module = importlib.import_module("module.device.method.minitouch")
        maatouch = importlib.import_module("module.device.method.maatouch")
        # Python 3.14 defers annotation evaluation; force the Python 3.10
        # import-time contract so this test catches incomplete dependency stubs.
        _ = module.Minitouch.__annotations__
        return (
            module.CommandBuilder,
            maatouch.MaatouchBuilder,
            maatouch.MaaTouch,
            stubs["module.exception"].ScriptError,
            maatouch,
        )


class ManagedTouchCropTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (
            cls.CommandBuilder,
            cls.MaatouchBuilder,
            cls.MaaTouch,
            cls.ScriptError,
            cls.maatouch_module,
        ) = _load_touch_classes()

    def test_maatouch_click_holds_contact_before_release(self):
        payloads = []
        captured_at = time.monotonic()
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
            _managed_crop_landscape_frame_at=captured_at,
            _managed_crop_frame_generation=1,
            _managed_crop_touch_authorized_at=captured_at,
            _managed_crop_touch_authorized_generation=1,
            _managed_crop_touch_budget=1,
        )
        device.maatouch_send = lambda builder: payloads.append(
            builder.to_minitouch()
        )
        device.maatouch_builder = self.MaatouchBuilder(device)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.MaaTouch.click_maatouch.__wrapped__(device, 1013, 52)

        self.assertEqual(
            payloads,
            ["d 0 1013 52 100\nc\nw 50\nu 0\nc\n"],
        )
        self.assertEqual(device._managed_crop_touch_budget, 0)

    def test_expired_authorization_cannot_send_after_maatouch_init_delay(self):
        payloads = []
        captured_at = time.monotonic() - 16
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
            _managed_crop_landscape_frame_at=captured_at,
            _managed_crop_frame_generation=1,
            _managed_crop_touch_authorized_at=captured_at,
            _managed_crop_touch_authorized_generation=1,
            _managed_crop_touch_budget=1,
        )
        device.maatouch_send = lambda builder: payloads.append(
            builder.to_minitouch()
        )
        device.maatouch_builder = self.MaatouchBuilder(device)

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaises(self.ScriptError):
                self.MaaTouch.click_maatouch.__wrapped__(device, 1013, 52)

        self.assertEqual(payloads, [])
        self.assertEqual(device._managed_crop_touch_budget, 1)

    def test_direct_builder_cannot_bypass_stale_landscape_frame_guard(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        builder = types.SimpleNamespace(
            DEFAULT_DELAY=0,
            contact=1,
            delay=0,
            to_minitouch=lambda: "d 1 640 600 100\nc\n",
            clear=lambda: None,
        )
        device = types.SimpleNamespace(
            _managed_crop_landscape_frame_at=time.monotonic() - 16,
            _maatouch_stream=stream,
            maatouch_builder=types.SimpleNamespace(delay=0),
            sleep=lambda _seconds: None,
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaises(self.ScriptError):
                self.MaaTouch.maatouch_send(device, builder)

        self.assertEqual(sent, [])

    def test_stale_frame_still_allows_touch_release_cleanup(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        builder = types.SimpleNamespace(
            DEFAULT_DELAY=0,
            contact=1,
            delay=0,
            to_minitouch=lambda: "u 1\nc\n",
            clear=lambda: None,
        )
        device = types.SimpleNamespace(
            _managed_crop_landscape_frame_at=None,
            _maatouch_stream=stream,
            maatouch_builder=types.SimpleNamespace(delay=0),
            sleep=lambda _seconds: None,
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.MaaTouch.maatouch_send(device, builder)

        self.assertEqual(sent, [b"u 1\nc\n"])

    def test_stale_frame_rejects_standalone_commit(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        builder = types.SimpleNamespace(
            DEFAULT_DELAY=0,
            contact=1,
            delay=0,
            to_minitouch=lambda: "c\n",
            clear=lambda: None,
        )
        device = types.SimpleNamespace(
            _managed_crop_landscape_frame_at=None,
            _maatouch_stream=stream,
            maatouch_builder=types.SimpleNamespace(delay=0),
            sleep=lambda _seconds: None,
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaises(self.ScriptError):
                self.MaaTouch.maatouch_send(device, builder)

        self.assertEqual(sent, [])

    def test_stale_frame_rejects_unknown_builder_command(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
            _managed_crop_landscape_frame_at=None,
            _maatouch_stream=stream,
            sleep=lambda _seconds: None,
        )
        builder = self.MaatouchBuilder(device)
        builder.commands.append(
            types.SimpleNamespace(
                operation="unknown",
                to_minitouch=lambda: "",
            )
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaises(self.ScriptError):
                self.MaaTouch.maatouch_send(device, builder)

        self.assertEqual(sent, [])
        self.assertEqual(builder.commands, [])

    def test_partial_wire_failure_clears_builder_and_poisons_stream(self):
        close = mock.Mock()
        stream = types.SimpleNamespace(
            sendall=mock.Mock(side_effect=BrokenPipeError("partial send")),
            recv=lambda _size: b"",
            close=close,
        )
        captured_at = time.monotonic()
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
            _managed_crop_landscape_frame_at=captured_at,
            _maatouch_stream=stream,
            _maatouch_stream_storage=object(),
            _maatouch_active_contacts={0},
            sleep=lambda _seconds: None,
        )
        device._maatouch_poison_stream = types.MethodType(
            self.MaaTouch._maatouch_poison_stream,
            device,
        )
        builder = self.MaatouchBuilder(device, contact=1)
        builder.down(100, 100).commit()

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaises(BrokenPipeError):
                self.MaaTouch.maatouch_send(device, builder)

        self.assertEqual(builder.commands, [])
        self.assertIsNone(device._maatouch_stream)
        self.assertIsNone(device._maatouch_stream_storage)
        self.assertEqual(device._maatouch_pending_release_contacts, {0, 1})
        self.assertEqual(device._maatouch_active_contacts, set())
        close.assert_called_once_with()

    def test_new_stream_releases_pending_contacts_before_reuse(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        device = types.SimpleNamespace(
            _maatouch_pending_release_contacts={1, 0},
        )

        self.MaaTouch._maatouch_release_pending_contacts(device, stream)

        self.assertEqual(sent, [b"u 0\nc\nu 1\nc\n"])
        self.assertEqual(device._maatouch_pending_release_contacts, set())

    def test_successful_payloads_track_active_contacts(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
            _managed_crop_landscape_frame_at=time.monotonic(),
            _maatouch_stream=stream,
            _maatouch_active_contacts=set(),
            sleep=lambda _seconds: None,
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            down = self.MaatouchBuilder(device, contact=1)
            down.down(100, 100).commit()
            self.MaaTouch.maatouch_send(device, down)
            self.assertEqual(device._maatouch_active_contacts, {1})

            device._managed_crop_landscape_frame_at = None
            up = self.MaatouchBuilder(device, contact=1)
            up.up().commit()
            self.MaaTouch.maatouch_send(device, up)

        self.assertEqual(device._maatouch_active_contacts, set())
        self.assertEqual(
            sent,
            [b"d 1 100 100 100\nc\n", b"u 1\nc\n"],
        )

    def test_portrait_stream_releases_pending_contact_before_axis_rejection(self):
        sent = []
        close = mock.Mock()
        lines = iter(["^ 2 720 1334 50\n", "$ 123\n"])
        socket_out = types.SimpleNamespace(readline=lambda: next(lines))
        stream = types.SimpleNamespace(
            settimeout=lambda _timeout: None,
            makefile=lambda: socket_out,
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
            close=close,
        )
        storage = types.SimpleNamespace(conn=stream)
        device = self.MaaTouch.__new__(self.MaaTouch)
        device._maatouch_stream = None
        device._maatouch_stream_storage = None
        device._maatouch_pending_release_contacts = {1}
        device._maatouch_active_contacts = set()
        device.orientation = 0
        device.adb_shell = lambda *_args, **_kwargs: storage
        device.sleep = lambda _seconds: None

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            with self.assertRaisesRegex(self.ScriptError, "landscape MaaTouch"):
                self.MaaTouch.maatouch_init(device)

        self.assertEqual(sent, [b"u 1\nc\n"])
        self.assertEqual(device._maatouch_pending_release_contacts, set())
        self.assertIsNone(device._maatouch_stream)
        self.assertIsNone(device._maatouch_stream_storage)
        close.assert_called_once_with()

    def test_swipe_releases_contact_when_frame_expires_mid_gesture(self):
        sent = []
        captured_at = time.monotonic()
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
            _managed_crop_landscape_frame_at=captured_at,
            _managed_crop_frame_generation=1,
            _managed_crop_touch_authorized_at=captured_at,
            _managed_crop_touch_authorized_generation=1,
            _managed_crop_touch_budget=1,
            sleep=lambda _seconds: None,
        )
        builder = self.MaatouchBuilder(device)
        device.maatouch_builder = builder

        def sendall(payload):
            sent.append(payload)
            if payload.startswith(b"d "):
                device._managed_crop_landscape_frame_at = None

        device._maatouch_stream = types.SimpleNamespace(
            sendall=sendall,
            recv=lambda _size: b"",
        )
        device.maatouch_send = types.MethodType(self.MaaTouch.maatouch_send, device)
        device._maatouch_release_contact_after_failure = types.MethodType(
            self.MaaTouch._maatouch_release_contact_after_failure,
            device,
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ), mock.patch.object(
            self.maatouch_module,
            "insert_swipe",
            return_value=[(100, 100), (200, 200)],
        ):
            with self.assertRaises(self.ScriptError):
                self.MaaTouch.swipe_maatouch.__wrapped__(
                    device,
                    (100, 100),
                    (200, 200),
                )

        self.assertEqual(
            sent,
            [b"d 0 100 100 100\nc\nw 10\n", b"u 0\nc\n"],
        )
        self.assertEqual(device._managed_crop_touch_budget, 1)

    def test_direct_builder_sends_with_fresh_landscape_frame(self):
        sent = []
        stream = types.SimpleNamespace(
            sendall=lambda payload: sent.append(payload),
            recv=lambda _size: b"",
        )
        builder = types.SimpleNamespace(
            DEFAULT_DELAY=0,
            contact=1,
            delay=0,
            to_minitouch=lambda: "d 1 640 600 100\nc\n",
            clear=lambda: None,
        )
        device = types.SimpleNamespace(
            _managed_crop_landscape_frame_at=time.monotonic(),
            _maatouch_stream=stream,
            maatouch_builder=types.SimpleNamespace(delay=0),
            sleep=lambda _seconds: None,
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.MaaTouch.maatouch_send(device, builder)

        self.assertEqual(sent, [b"d 1 640 600 100\nc\n"])

    def test_transport_retry_consumes_budget_only_after_success(self):
        builder_class = self.MaatouchBuilder

        class Device:
            max_x = 1334
            max_y = 720
            orientation = 0
            config = types.SimpleNamespace(DEVICE_OVER_HTTP=False)

            def __init__(self):
                captured_at = time.monotonic()
                self._managed_crop_landscape_frame_at = captured_at
                self._managed_crop_frame_generation = 1
                self._managed_crop_touch_authorized_at = captured_at
                self._managed_crop_touch_authorized_generation = 1
                self._managed_crop_touch_budget = 1
                self.attempts = 0
                self.payloads = []

            @property
            def maatouch_builder(self):
                return builder_class(self)

            def maatouch_send(self, builder):
                self.attempts += 1
                if self.attempts == 1:
                    raise BrokenPipeError("temporary transport failure")
                self.payloads.append(builder.to_minitouch())
                builder.clear()

        device = Device()
        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.MaaTouch.click_maatouch(device, 1013, 52)

        self.assertEqual(device.attempts, 2)
        self.assertEqual(
            device.payloads,
            ["d 0 1013 52 100\nc\nw 50\nu 0\nc\n"],
        )
        self.assertEqual(device._managed_crop_touch_budget, 0)

    def test_regular_maatouch_click_keeps_existing_zero_hold_behavior(self):
        payloads = []
        device = types.SimpleNamespace(
            max_x=1280,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
        )
        device.maatouch_send = lambda builder: payloads.append(
            builder.to_minitouch()
        )
        device.maatouch_builder = self.MaatouchBuilder(device)

        with mock.patch.dict(os.environ, {}, clear=True):
            self.MaaTouch.click_maatouch.__wrapped__(device, 1013, 360)

        self.assertEqual(
            payloads,
            ["d 0 1013 360 100\nc\nu 0\nc\n"],
        )

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

    def test_maatouch_command_offsets_asset_x_inside_left_cropped_canvas(self):
        device = types.SimpleNamespace(
            max_x=1334,
            max_y=720,
            orientation=0,
            config=types.SimpleNamespace(DEVICE_OVER_HTTP=False),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "54,0,0,0"},
        ):
            builder = self.CommandBuilder(
                device,
                handle_orientation=False,
            )
            builder.down(0, 360)
            builder.down(1200, 360)

        first, second = builder.commands
        self.assertEqual((first.x, first.y), (54, 360))
        self.assertEqual((second.x, second.y), (1254, 360))
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

    def test_crop_keeps_verified_landscape_builder_on_transient_portrait(self):
        reinitializations = []
        device = types.SimpleNamespace(
            _maatouch_orientation=1,
            orientation=0,
            early_maatouch_init=lambda: reinitializations.append("reinit"),
        )

        with mock.patch.dict(
            os.environ,
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"},
        ):
            self.MaaTouch.on_orientation_change_maatouch(device)

        self.assertEqual(reinitializations, [])

    def test_regular_maatouch_still_reinitializes_after_orientation_change(self):
        reinitializations = []
        device = types.SimpleNamespace(
            _maatouch_orientation=1,
            orientation=0,
            early_maatouch_init=lambda: reinitializations.append("reinit"),
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            self.MaaTouch.on_orientation_change_maatouch(device)

        self.assertEqual(reinitializations, ["reinit"])


if __name__ == "__main__":
    unittest.main()
