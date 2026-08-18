import subprocess
import unittest

from deploy.managed_adb_resolution import (
    ManagedAdbResolution,
    ManagedAdbResolutionError,
    managed_resolution_from_environment,
)


class FakeAdb:
    def __init__(self, override=None, reset_failures=0):
        self.override = override
        self.reset_failures = reset_failures
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        arguments = command[4:]
        if arguments == ["wm", "size"]:
            output = "Physical size: 1080x2460\n"
            if self.override is not None:
                output += f"Override size: {self.override}\n"
        elif arguments[:2] == ["wm", "size"] and len(arguments) == 3:
            if arguments[2] == "reset" and self.reset_failures:
                self.reset_failures -= 1
                raise subprocess.CalledProcessError(1, command, stderr="offline")
            self.override = None if arguments[2] == "reset" else arguments[2]
            output = ""
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


class ManagedAdbResolutionTest(unittest.TestCase):
    def test_disabled_when_environment_variable_is_absent(self):
        self.assertIsNone(managed_resolution_from_environment({}))

    def test_invalid_resolution_is_rejected(self):
        with self.assertRaises(ManagedAdbResolutionError):
            ManagedAdbResolution("127.0.0.1:5555", "1280*720")

    def test_crop_must_match_managed_resolution_before_acquire(self):
        with self.assertRaises(ManagedAdbResolutionError):
            managed_resolution_from_environment(
                {
                    "SRC_ADB_MANAGED_RESOLUTION": "720x1334",
                    "SRC_ADB_MANAGED_SCREEN_CROP": "0,0,52,0",
                }
            )

    def test_crop_requires_managed_resolution(self):
        with self.assertRaises(ManagedAdbResolutionError):
            managed_resolution_from_environment(
                {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"}
            )

    def test_crop_accepts_portrait_managed_resolution_pair(self):
        controller = managed_resolution_from_environment(
            {
                "SRC_ADB_MANAGED_RESOLUTION": "720x1334",
                "SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0",
            }
        )

        self.assertEqual(controller.target, "720x1334")

    def test_applies_override_and_resets_physical_size_on_release(self):
        adb = FakeAdb()
        controller = ManagedAdbResolution(
            "127.0.0.1:5555", "720x1280", runner=adb
        )

        lease = controller.acquire()
        self.assertEqual(adb.override, "720x1280")
        lease.release()

        self.assertIsNone(adb.override)
        self.assertIn(
            ["adb", "-s", "127.0.0.1:5555", "shell", "wm", "size", "reset"],
            adb.commands,
        )

    def test_restores_preexisting_override_and_release_is_idempotent(self):
        adb = FakeAdb(override="900x1600")
        controller = ManagedAdbResolution(
            "127.0.0.1:5555", "720x1280", runner=adb
        )

        lease = controller.acquire()
        lease.release()
        lease.release()

        self.assertEqual(adb.override, "900x1600")

    def test_shared_leases_restore_only_after_last_worker_stops(self):
        adb = FakeAdb()
        controller = ManagedAdbResolution(
            "127.0.0.1:5555", "720x1280", runner=adb
        )

        first = controller.acquire()
        second = controller.acquire()
        first.release()
        self.assertEqual(adb.override, "720x1280")
        second.release()

        self.assertIsNone(adb.override)

    def test_failed_restore_can_be_retried(self):
        adb = FakeAdb(reset_failures=1)
        controller = ManagedAdbResolution(
            "127.0.0.1:5555", "720x1280", runner=adb
        )

        lease = controller.acquire()
        with self.assertRaises(ManagedAdbResolutionError):
            lease.release()
        self.assertEqual(adb.override, "720x1280")

        lease.release()
        self.assertIsNone(adb.override)

    def test_context_manager_restores_after_worker_error(self):
        adb = FakeAdb()
        controller = ManagedAdbResolution(
            "127.0.0.1:5555", "720x1280", runner=adb
        )

        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            with controller.acquire():
                self.assertEqual(adb.override, "720x1280")
                raise RuntimeError("worker failed")

        self.assertIsNone(adb.override)


if __name__ == "__main__":
    unittest.main()
