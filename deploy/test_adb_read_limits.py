import functools
import importlib.util
import socket
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Initer:
    pass


class _AdbConnection:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def close(self):
        self.closed = True
        self.conn.close()


class _Config:
    functions = {}

    @classmethod
    def when(cls, **conditions):
        def decorator(function):
            enabled = conditions.get("DEVICE_OVER_HTTP")
            if enabled is False:
                cls.functions[function.__name__] = function
                return function
            if enabled is True:
                return cls.functions.get(function.__name__, function)
            return function

        return decorator


def _load_utils():
    adb_timeout = type("AdbTimeout", (Exception,), {})
    adb_error = type("AdbError", (Exception,), {})
    adbutils_errors = _module("adbutils.errors", AdbError=adb_error)
    u2_init = types.SimpleNamespace(Initer=_Initer, GITHUB_BASEURL="")
    u2_logger = _Logger()
    stubs = {
        "adbutils": _module(
            "adbutils",
            AdbTimeout=adb_timeout,
            _AdbStreamConnection=_AdbConnection,
            errors=adbutils_errors,
        ),
        "adbutils.errors": adbutils_errors,
        "uiautomator2": _module(
            "uiautomator2",
            Device=type("Device", (), {}),
            init=u2_init,
            logger=u2_logger,
            setup_logger=lambda *args, **kwargs: u2_logger,
        ),
        "uiautomator2cache": _module(
            "uiautomator2cache",
            __file__=str(ROOT / "deploy" / "test_adb_read_limits.py"),
        ),
        "lxml": _module(
            "lxml",
            etree=types.SimpleNamespace(_Element=type("_Element", (), {})),
        ),
        "module.base.decorator": _module(
            "module.base.decorator",
            cached_property=functools.cached_property,
        ),
        "module.device.method.remove_warning": _module(
            "module.device.method.remove_warning",
            remove_shell_warning=lambda data: data,
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
    }

    name = "test_adb_read_limits_utils"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "module" / "device" / "method" / "utils.py",
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module, adb_timeout, adb_error


def _load_connection(utils, adb_timeout, adb_error):
    class AdbDevice:
        def __init__(self, client=None, serial=""):
            self._client = client
            self.serial = serial

    class SelectedGrids(list):
        def select(self, **_kwargs):
            return SelectedGrids()

        def delete(self, _other):
            return SelectedGrids()

    connection_utils = _module(
        "module.device.method.utils",
        PackageNotInstalled=type("PackageNotInstalled", (Exception,), {}),
        RETRY_TRIES=1,
        get_serial_pair=lambda _serial: (None, None),
        handle_adb_error=lambda _error: False,
        handle_unknown_host_service=lambda _error: False,
        possible_reasons=lambda *_args: None,
        random_port=lambda _port_range: 12345,
        recv_all=utils.recv_all,
        retry_sleep=lambda _trial: 0,
    )
    adbutils_errors = _module("adbutils.errors", AdbError=adb_error)
    stubs = {
        "adbutils": _module(
            "adbutils",
            AdbClient=type("AdbClient", (), {}),
            AdbDevice=AdbDevice,
            AdbTimeout=adb_timeout,
            ForwardItem=type("ForwardItem", (), {}),
            ReverseItem=type("ReverseItem", (), {}),
        ),
        "adbutils.errors": adbutils_errors,
        "uiautomator2": _module("uiautomator2", init=types.SimpleNamespace()),
        "module.config.server": _module(
            "module.config.server",
            to_package=lambda package, **_kwargs: package,
        ),
        "module.base.decorator": _module(
            "module.base.decorator",
            Config=_Config,
            cached_property=functools.cached_property,
            del_cached_property=lambda *_args, **_kwargs: None,
            run_once=lambda function: function,
        ),
        "module.base.timer": _module(
            "module.base.timer",
            Timer=type("Timer", (), {}),
        ),
        "module.base.utils": _module(
            "module.base.utils",
            SelectedGrids=SelectedGrids,
            ensure_time=lambda value: value,
        ),
        "module.config.deep": _module(
            "module.config.deep",
            deep_get=lambda *_args, **_kwargs: None,
        ),
        "module.device.connection_attr": _module(
            "module.device.connection_attr",
            ConnectionAttr=type("ConnectionAttr", (), {}),
        ),
        "module.device.env": _module(
            "module.device.env",
            IS_LINUX=True,
            IS_MACINTOSH=False,
            IS_WINDOWS=False,
        ),
        "module.device.method.pool": _module(
            "module.device.method.pool",
            WORKER_POOL=types.SimpleNamespace(),
        ),
        "module.device.method.remove_warning": _module(
            "module.device.method.remove_warning",
            remove_shell_warning=lambda data: data,
        ),
        "module.device.method.utils": connection_utils,
        "module.exception": _module(
            "module.exception",
            EmulatorNotRunningError=type("EmulatorNotRunningError", (Exception,), {}),
            RequestHumanTakeover=type("RequestHumanTakeover", (Exception,), {}),
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
    }
    stubs.update({
        "module.base": _module("module.base", __path__=[]),
        "module.config": _module("module.config", __path__=[]),
        "module.device": _module("module.device", __path__=[]),
        "module.device.method": _module("module.device.method", __path__=[]),
    })

    _Config.functions = {}
    name = "test_adb_read_limits_connection"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "module" / "device" / "connection.py",
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class AdbReadLimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.utils, cls.AdbTimeout, cls.AdbError = _load_utils()
        cls.connection = _load_connection(
            cls.utils,
            cls.AdbTimeout,
            cls.AdbError,
        )

    def test_recv_all_enforces_total_timeout_while_peer_trickles(self):
        reader, writer = socket.socketpair()
        transport = _AdbConnection(reader)
        stop = threading.Event()

        def trickle():
            try:
                while not stop.is_set():
                    writer.sendall(b"x")
                    time.sleep(0.01)
            except OSError:
                pass

        producer = threading.Thread(target=trickle, daemon=True)
        producer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(self.AdbTimeout):
                self.utils.recv_all(
                    transport,
                    chunk_size=1,
                    total_timeout=0.1,
                )
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(transport.closed)
            self.assertEqual(reader.fileno(), -1)
        finally:
            stop.set()
            reader.close()
            writer.close()
            producer.join(timeout=1)

    def test_recv_all_rejects_oversized_output_and_closes_transport(self):
        reader, writer = socket.socketpair()
        writer.sendall(b"12345")
        try:
            with self.assertRaises(self.AdbError):
                self.utils.recv_all(
                    reader,
                    total_timeout=0.5,
                    max_bytes=4,
                )
            self.assertEqual(reader.fileno(), -1)
        finally:
            reader.close()
            writer.close()

    def test_recv_all_without_one_shot_bounds_keeps_long_stream_behavior(self):
        reader, writer = socket.socketpair()
        payload = b"x" * (16 * 1024 * 1024 + 1)

        def produce():
            try:
                writer.sendall(payload)
            finally:
                writer.close()

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        try:
            result = self.utils.recv_all(reader, chunk_size=64 * 1024)
            self.assertEqual(len(result), len(payload))
        finally:
            reader.close()
            writer.close()
            producer.join(timeout=1)

    def test_adb_shell_recvall_uses_the_callers_total_timeout(self):
        reader, writer = socket.socketpair()
        transport = _AdbConnection(reader)
        stop = threading.Event()

        class FakeAdb:
            @staticmethod
            def shell(_cmd, stream=False, timeout=None, rstrip=True):
                return transport

        device = types.SimpleNamespace(adb=FakeAdb())

        def trickle():
            try:
                deadline = time.monotonic() + 0.6
                while not stop.is_set() and time.monotonic() < deadline:
                    writer.sendall(b"x")
                    time.sleep(0.01)
            except OSError:
                pass
            finally:
                writer.close()

        producer = threading.Thread(target=trickle, daemon=True)
        producer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(self.AdbTimeout):
                self.connection.Connection.adb_shell(
                    device,
                    ["screencap", "-p"],
                    stream=True,
                    recvall=True,
                    timeout=0.1,
                )
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(transport.closed)
            self.assertEqual(reader.fileno(), -1)
        finally:
            stop.set()
            reader.close()
            writer.close()
            producer.join(timeout=1)

    def test_adb_shell_recvall_shares_timeout_with_transport_open(self):
        transport = object()

        class FakeAdb:
            @staticmethod
            def shell(_cmd, stream=False, timeout=None, rstrip=True):
                self.assertEqual(timeout, 10)
                return transport

        device = types.SimpleNamespace(adb=FakeAdb())
        clock = types.SimpleNamespace(
            monotonic=mock.Mock(side_effect=[100.0, 103.0]),
        )

        with mock.patch.object(self.connection, "time", clock):
            with mock.patch.object(
                self.connection,
                "recv_all",
                return_value=b"screenshot",
            ) as recv_all:
                result = self.connection.Connection.adb_shell(
                    device,
                    ["screencap", "-p"],
                    stream=True,
                    recvall=True,
                    timeout=10,
                )

        self.assertEqual(result, b"screenshot")
        recv_all.assert_called_once_with(
            transport,
            total_timeout=7,
            max_bytes=self.connection.ADB_SHELL_MAX_OUTPUT_BYTES,
        )

    def test_adb_shell_recvall_preserves_unbounded_timeout_none(self):
        transport = object()

        class FakeAdb:
            @staticmethod
            def shell(_cmd, stream=False, timeout=None, rstrip=True):
                self.assertIsNone(timeout)
                return transport

        device = types.SimpleNamespace(adb=FakeAdb())
        with mock.patch.object(
            self.connection,
            "recv_all",
            return_value=b"stream",
        ) as recv_all:
            result = self.connection.Connection.adb_shell(
                device,
                ["long-read"],
                stream=True,
                recvall=True,
                timeout=None,
            )

        self.assertEqual(result, b"stream")
        recv_all.assert_called_once_with(
            transport,
            total_timeout=None,
            max_bytes=self.connection.ADB_SHELL_MAX_OUTPUT_BYTES,
        )

    def test_adb_shell_recvall_rejects_output_above_one_shot_limit(self):
        reader, writer = socket.socketpair()
        transport = _AdbConnection(reader)
        payload = b"x" * (16 * 1024 * 1024 + 1)

        class FakeAdb:
            @staticmethod
            def shell(_cmd, stream=False, timeout=None, rstrip=True):
                return transport

        device = types.SimpleNamespace(adb=FakeAdb())

        def produce():
            try:
                writer.sendall(payload)
            except OSError:
                pass
            finally:
                writer.close()

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        try:
            with self.assertRaises(self.AdbError):
                self.connection.Connection.adb_shell(
                    device,
                    ["screencap", "-p"],
                    stream=True,
                    recvall=True,
                    timeout=2,
                )
            self.assertTrue(transport.closed)
            self.assertEqual(reader.fileno(), -1)
        finally:
            reader.close()
            writer.close()
            producer.join(timeout=1)

    def test_adb_shell_long_lived_stream_is_returned_without_one_shot_reading(self):
        sentinel = object()

        class FakeAdb:
            @staticmethod
            def shell(_cmd, stream=False, timeout=None, rstrip=True):
                return sentinel

        device = types.SimpleNamespace(adb=FakeAdb())
        result = self.connection.Connection.adb_shell(
            device,
            ["long-lived-server"],
            stream=True,
            recvall=False,
            timeout=0.1,
        )

        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
