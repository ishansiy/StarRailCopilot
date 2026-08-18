import importlib
import queue
import sys
import types
import unittest
from unittest import mock


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _load_process_manager_module():
    state = types.SimpleNamespace(
        manager=types.SimpleNamespace(Queue=queue.Queue),
        electron=False,
    )
    stubs = {
        "inflection": _module("inflection"),
        "rich.console": _module(
            "rich.console",
            Console=type("Console", (), {}),
            ConsoleRenderable=object,
        ),
        "module.logger": _module(
            "module.logger",
            logger=_Logger(),
            set_file_logger=lambda *args, **kwargs: None,
            set_func_logger=lambda *args, **kwargs: None,
        ),
        "module.webui.fake": _module(
            "module.webui.fake",
            get_config_mod=lambda *args, **kwargs: None,
            mod_instance=lambda *args, **kwargs: None,
        ),
        "module.webui.setting": _module("module.webui.setting", State=state),
        "module.webui.submodule.utils": _module(
            "module.webui.submodule.utils",
            get_available_func=lambda *args, **kwargs: None,
        ),
    }

    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("module.webui.process_manager", None)
        return importlib.import_module("module.webui.process_manager")


class ProcessManagerResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process_manager_module = _load_process_manager_module()

    def test_invalid_managed_resolution_is_reported_without_starting_worker(self):
        manager = self.process_manager_module.ProcessManager("phone")
        error = RuntimeError("裁剪配置不匹配")

        with mock.patch.object(
            self.process_manager_module,
            "managed_resolution_from_environment",
            side_effect=error,
        ):
            manager.start(lambda: None)

        self.assertIsNone(manager._process)
        self.assertIsNone(manager._managed_resolution_lease)
        self.assertIn("启动失败：无法托管设备分辨率", manager.renderables[-1])
        self.assertIn("裁剪配置不匹配", manager.renderables[-1])

    def test_log_handler_start_failure_stops_worker_and_releases_lease(self):
        manager = self.process_manager_module.ProcessManager("phone")
        lease = mock.Mock()
        controller = types.SimpleNamespace(
            target="720x1334",
            keep_awake=True,
            acquire=lambda: lease,
        )

        class FakeProcess:
            def __init__(self, **_kwargs):
                self.running = False
                self.killed = False
                self.joined = False

            def start(self):
                self.running = True

            def is_alive(self):
                return self.running

            def kill(self):
                self.killed = True
                self.running = False

            def join(self, timeout=None):
                self.joined = timeout

        with (
            mock.patch.object(
                self.process_manager_module,
                "managed_resolution_from_environment",
                return_value=controller,
            ),
            mock.patch.object(
                self.process_manager_module,
                "Process",
                FakeProcess,
            ),
            mock.patch.object(
                manager,
                "start_log_queue_handler",
                side_effect=RuntimeError("thread start failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                manager.start(lambda: None)

        self.assertTrue(manager._process.killed)
        self.assertEqual(manager._process.joined, 1)
        lease.release.assert_called_once_with()
        self.assertIsNone(manager._managed_resolution_lease)


if __name__ == "__main__":
    unittest.main()
