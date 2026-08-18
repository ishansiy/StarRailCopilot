import functools
import importlib
import sys
import types
import unittest


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _load_alas_module():
    inflection = types.ModuleType("inflection")
    inflection.underscore = lambda value: value.lower()
    sys.modules["inflection"] = inflection

    cached_property = types.ModuleType("cached_property")
    cached_property.cached_property = functools.cached_property
    sys.modules["cached_property"] = cached_property

    decorator = types.ModuleType("module.base.decorator")
    decorator.del_cached_property = lambda *args, **kwargs: None
    sys.modules["module.base.decorator"] = decorator

    config = types.ModuleType("module.config.config")
    config.AzurLaneConfig = type("AzurLaneConfig", (), {})
    config.TaskEnd = type("TaskEnd", (Exception,), {})
    sys.modules["module.config.config"] = config

    deep = types.ModuleType("module.config.deep")
    deep.deep_get = lambda *args, **kwargs: kwargs.get("default")
    deep.deep_set = lambda *args, **kwargs: None
    sys.modules["module.config.deep"] = deep

    exceptions = types.ModuleType("module.exception")
    for name in (
        "RequestHumanTakeover",
        "GameNotRunningError",
        "GameStuckError",
        "GameTooManyClickError",
        "GameBugError",
        "GamePageUnknownError",
        "HandledError",
        "ScriptError",
    ):
        setattr(exceptions, name, type(name, (Exception,), {}))
    exceptions.__all__ = [
        name for name in vars(exceptions) if not name.startswith("_")
    ]
    sys.modules["module.exception"] = exceptions

    logger = types.ModuleType("module.logger")
    logger.logger = _Logger()
    logger.save_error_log = lambda *args, **kwargs: None
    sys.modules["module.logger"] = logger

    notify = types.ModuleType("module.notify")
    notify.handle_notify = lambda *args, **kwargs: None
    sys.modules["module.notify"] = notify

    sys.modules.pop("module.alas", None)
    return importlib.import_module("module.alas")


class _Device:
    def __init__(self, running):
        self.running = running
        self.checks = 0

    def app_is_running(self):
        self.checks += 1
        return self.running


class _Scheduler:
    def __init__(self, first_task, running):
        self.is_first_task = first_task
        self.device = _Device(running)
        self.commands = []

    def run(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return True


class _ScreenshotTracking:
    def clear(self):
        pass


class _RunDevice:
    def __init__(self):
        self.screenshots = 0
        self.screenshot_tracking = _ScreenshotTracking()

    def screenshot(self):
        self.screenshots += 1


class _RunHarness:
    def __init__(self):
        self.device = _RunDevice()
        self.started = 0

    def start(self):
        self.started += 1


class SchedulerStartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alas = _load_alas_module()

    def test_first_scheduler_iteration_starts_a_stopped_game(self):
        scheduler = _Scheduler(first_task=True, running=False)

        result = self.alas.AzurLaneAutoScript.ensure_app_running_at_scheduler_start(
            scheduler
        )

        self.assertTrue(result)
        self.assertEqual(
            scheduler.commands,
            [("start", {"skip_initial_screenshot": True})],
        )

    def test_first_scheduler_iteration_does_not_restart_a_running_game(self):
        scheduler = _Scheduler(first_task=True, running=True)

        result = self.alas.AzurLaneAutoScript.ensure_app_running_at_scheduler_start(
            scheduler
        )

        self.assertTrue(result)
        self.assertEqual(scheduler.commands, [])

    def test_later_scheduler_iterations_do_not_repeat_start_check(self):
        scheduler = _Scheduler(first_task=False, running=False)

        result = self.alas.AzurLaneAutoScript.ensure_app_running_at_scheduler_start(
            scheduler
        )

        self.assertTrue(result)
        self.assertEqual(scheduler.device.checks, 0)
        self.assertEqual(scheduler.commands, [])

    def test_app_start_can_skip_the_screenshot_that_requires_a_running_game(self):
        scheduler = _RunHarness()

        result = self.alas.AzurLaneAutoScript.run(
            scheduler, "start", skip_initial_screenshot=True
        )

        self.assertTrue(result)
        self.assertEqual(scheduler.started, 1)
        self.assertEqual(scheduler.device.screenshots, 0)


if __name__ == "__main__":
    unittest.main()
