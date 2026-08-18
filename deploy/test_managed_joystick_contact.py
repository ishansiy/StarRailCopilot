import functools
import importlib
import sys
import types
import unittest
from unittest import mock


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Timer:
    def __init__(self, *_args, **_kwargs):
        self.reset_calls = 0

    def start(self):
        return self

    def reset(self):
        self.reset_calls += 1
        return self

    def set(self, *_args, **_kwargs):
        return self

    def reached(self):
        return False

    def __str__(self):
        return "timer"


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Builder:
    _next_id = 0
    DEFAULT_DELAY = 0.05

    def __init__(self, device, contact=0, handle_orientation=False):
        type(self)._next_id += 1
        self.builder_id = type(self)._next_id
        self.device = device
        self.contact = contact
        self.commands = []

    def down(self, x, y, pressure=100):
        self.commands.append(f"d {self.contact} {x} {y} {pressure}\n")
        return self

    def move(self, x, y, pressure=100):
        self.commands.append(f"m {self.contact} {x} {y} {pressure}\n")
        return self

    def up(self):
        self.commands.append(f"u {self.contact}\n")
        return self

    def commit(self):
        self.commands.append("c\n")
        return self

    def wait(self, ms=10):
        self.commands.append(f"w {ms}\n")
        return self

    def clear(self):
        self.commands = []

    def to_minitouch(self):
        return "".join(self.commands)

    def send(self):
        return self.device.maatouch_send(self)


def _retry(function):
    @functools.wraps(function)
    def wrapped(receiver, *args, **kwargs):
        receiver.retry_receivers.append(receiver)
        error = None
        for _ in range(2):
            try:
                return function(receiver, *args, **kwargs)
            except Exception as exception:
                error = exception
        raise error

    return wrapped


def _load_joystick():
    script_error = type("ScriptError", (Exception,), {})
    joystick_asset = types.SimpleNamespace(area=(0, 0, 200, 200))
    stubs = {
        "cv2": _module(
            "cv2",
            INTER_CUBIC=0,
            remap=lambda *args, **kwargs: None,
        ),
        "module.base.timer": _module("module.base.timer", Timer=_Timer),
        "module.base.utils": _module(
            "module.base.utils",
            area_offset=lambda area, _offset: area,
        ),
        "module.device.method.maatouch": _module(
            "module.device.method.maatouch",
            MaatouchBuilder=_Builder,
            retry=_retry,
        ),
        "module.device.method.minitouch": _module(
            "module.device.method.minitouch",
            CommandBuilder=_Builder,
            insert_swipe=lambda p0, p3, speed=20: [p0, p3],
            random_normal_distribution=lambda a, _b, n=5: a,
            retry=_retry,
        ),
        "module.exception": _module(
            "module.exception",
            ScriptError=script_error,
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
        "tasks.base.ui": _module("tasks.base.ui", UI=object),
        "tasks.map.assets.assets_map_control": _module(
            "tasks.map.assets.assets_map_control",
            JOYSTICK=joystick_asset,
        ),
    }

    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("tasks.map.control.joystick", None)
        return importlib.import_module("tasks.map.control.joystick")


class _Device:
    def __init__(self):
        self.maatouch_builder = object()
        self.minitouch_builder = object()
        self.retry_receivers = []
        self.attempts = []

    def maatouch_send(self, builder):
        payload = builder.to_minitouch()
        self.attempts.append((builder.builder_id, payload))
        builder.clear()


class _Main:
    def __init__(self, device):
        self.device = device
        self.config = types.SimpleNamespace(Emulator_ControlMethod="MaaTouch")
        self.map_run_2x_timer = _Timer()
        self.joystick_lost_timer = _Timer()

    def joystick_speed(self):
        return "run"


class _FinalSendError(Exception):
    pass


class _GuardError(Exception):
    pass


class JoystickContactMaaTouchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.joystick = _load_joystick()
        cls.Contact = cls.joystick.JoystickContact

    def test_partial_down_final_failure_still_releases_contact(self):
        device = _Device()

        def send(builder):
            payload = builder.to_minitouch()
            device.attempts.append((builder.builder_id, payload))
            if any(row.startswith("d ") for row in payload.splitlines()):
                raise _FinalSendError("sendall delivered bytes, then failed")
            builder.clear()

        device.maatouch_send = send
        contact = self.Contact(_Main(device))

        with mock.patch.object(
            self.Contact,
            "direction2screen",
            return_value=(10, 20),
        ):
            with self.assertRaises(_FinalSendError):
                with contact:
                    contact.set(0)

        self.assertEqual(device.attempts[-1][1], "u 1\nc\n")
        self.assertFalse(contact.contact_may_be_down)

    def test_move_guard_failure_uses_fresh_builder_for_up(self):
        device = _Device()

        def send(builder):
            payload = builder.to_minitouch()
            device.attempts.append((builder.builder_id, payload))
            if any(row.startswith("m ") for row in payload.splitlines()):
                raise _GuardError("landscape frame expired")
            builder.clear()

        device.maatouch_send = send
        contact = self.Contact(_Main(device))
        contact.prev_point = (100, 100)
        contact.contact_may_be_down = True

        with mock.patch.object(
            self.Contact,
            "direction2screen",
            return_value=(200, 200),
        ), mock.patch.object(
            self.joystick,
            "insert_swipe",
            return_value=[(100, 100), (200, 200)],
        ):
            with self.assertRaises(_GuardError):
                with contact:
                    contact.set(0)

        move_builder_ids = [
            builder_id
            for builder_id, payload in device.attempts
            if "m " in payload
        ]
        cleanup_builder_id, cleanup_payload = device.attempts[-1]
        self.assertEqual(cleanup_payload, "u 1\nc\n")
        self.assertNotIn("m ", cleanup_payload)
        self.assertNotIn(cleanup_builder_id, move_builder_ids)

    def test_maatouch_retry_receiver_is_device(self):
        device = _Device()
        contact = self.Contact(_Main(device))
        callback_receivers = []

        contact.with_retry(lambda receiver: callback_receivers.append(receiver))

        self.assertEqual(device.retry_receivers, [device])
        self.assertEqual(callback_receivers, [contact])

    def test_stream_generation_change_redowns_instead_of_moving(self):
        device = _Device()
        device._maatouch_stream_generation = 1

        def send(builder):
            payload = builder.to_minitouch()
            device.attempts.append((builder.builder_id, payload))
            builder.clear()
            if payload.startswith("m "):
                device._maatouch_stream_generation = 2
                raise _GuardError("shared MaaTouch stream was rebuilt")

        device.maatouch_send = send
        contact = self.Contact(_Main(device))
        contact.prev_point = (100, 100)
        contact.contact_may_be_down = True
        contact.maatouch_stream_generation = 1

        with mock.patch.object(
            self.Contact,
            "direction2screen",
            return_value=(200, 200),
        ), mock.patch.object(
            self.joystick,
            "insert_swipe",
            return_value=[(100, 100), (200, 200)],
        ):
            contact.set(0)

        self.assertTrue(device.attempts[0][1].startswith("m 1 "))
        self.assertEqual(device.attempts[1][1], "d 1 200 200 100\nc\n")
        self.assertEqual(contact.maatouch_stream_generation, 2)
        self.assertEqual(contact.prev_point, (200, 200))


if __name__ == "__main__":
    unittest.main()
