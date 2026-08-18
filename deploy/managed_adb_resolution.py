import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from module.device.managed_screenshot_crop import (
    ManagedScreenshotCropError,
    managed_screenshot_crop_from_environment,
)


_SIZE_PATTERN = re.compile(r"^[1-9]\d*x[1-9]\d*$")
_OVERRIDE_PATTERN = re.compile(
    r"^Override size:\s*([1-9]\d*x[1-9]\d*)\s*$", re.MULTILINE
)
_POWERED_PATTERN = re.compile(
    r"^\s*(?:AC|USB|Wireless) powered:\s*true\s*$", re.MULTILINE
)
_AWAKE_PATTERN = re.compile(r"\bmWakefulness=Awake\b")
_STAY_AWAKE_VALUE = "7"


class ManagedAdbResolutionError(RuntimeError):
    """Raised when an explicitly requested display-size change cannot be applied."""


@dataclass(frozen=True)
class _ManagedDeviceSnapshot:
    override: Optional[str]
    stay_on_while_plugged_in: Optional[str] = None


class ManagedAdbResolutionLease:
    def __init__(
        self,
        controller: "ManagedAdbResolution",
        generation: int,
    ) -> None:
        self._controller = controller
        self._generation = generation
        self._released = False
        self._lock = threading.Lock()

    def __enter__(self) -> "ManagedAdbResolutionLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.release()
        return False

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._controller._release(self._generation)
            self._released = True


class ManagedAdbResolution:
    """Reference-counted ADB display-size override for WebUI worker processes."""

    def __init__(
        self,
        serial: str,
        target: str,
        adb_binary: str = "adb",
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        keep_awake: bool = False,
    ) -> None:
        if not serial.strip():
            raise ManagedAdbResolutionError("SRC_ADB_SERIAL 不能为空")
        if not _SIZE_PATTERN.fullmatch(target):
            raise ManagedAdbResolutionError(
                "SRC_ADB_MANAGED_RESOLUTION 必须使用 WIDTHxHEIGHT 格式，例如 720x1280"
            )

        self.serial = serial.strip()
        self.target = target
        self.adb_binary = adb_binary
        self.keep_awake = keep_awake
        self._runner = runner
        self._lock = threading.Lock()
        self._leases = 0
        self._lease_generation = 0
        self._active_generation: Optional[int] = None
        self._snapshot: Optional[_ManagedDeviceSnapshot] = None

    def acquire(self) -> ManagedAdbResolutionLease:
        with self._lock:
            if self._leases == 0:
                # A previous setup may have changed the phone and then lost
                # ADB during rollback. Recover that original snapshot before
                # treating the current (possibly dirty) state as a new base.
                if self._snapshot is not None:
                    self._restore(self._snapshot)
                    self._snapshot = None
                    self._active_generation = None

                snapshot = self._read_snapshot()
                self._snapshot = snapshot
                try:
                    if self.keep_awake:
                        self._enable_keep_awake()
                    self._set_size(self.target)
                    self._verify_override(self.target)
                except Exception as setup_error:
                    try:
                        self._restore(snapshot)
                    except Exception as rollback_error:
                        raise ManagedAdbResolutionError(
                            "托管设备设置失败，且自动回滚暂时失败；"
                            f"下次启动将先重试恢复。设置错误：{setup_error}；"
                            f"回滚错误：{rollback_error}"
                        ) from setup_error
                    self._snapshot = None
                    raise
                self._lease_generation += 1
                self._active_generation = self._lease_generation
            self._leases += 1
            generation = self._active_generation
        return ManagedAdbResolutionLease(self, generation)

    def _release(self, generation: int) -> None:
        with self._lock:
            if generation != self._active_generation:
                return
            if self._leases == 0:
                if self._snapshot is not None:
                    self._restore(self._snapshot)
                    self._snapshot = None
                self._active_generation = None
                return
            if self._leases > 1:
                self._leases -= 1
                return

            snapshot = self._snapshot
            # Mark the active lease set closed before attempting I/O. If the
            # restore fails, any retry or new acquire must recover the pending
            # snapshot instead of sharing a half-restored phone state.
            self._leases = 0
            if snapshot is not None:
                self._restore(snapshot)
            self._snapshot = None
            self._active_generation = None

    def _adb(self, arguments: Sequence[str]) -> str:
        command = [self.adb_binary, "-s", self.serial, "shell", *arguments]
        try:
            result = self._runner(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            detail = getattr(error, "stderr", None) or str(error)
            raise ManagedAdbResolutionError(
                f"ADB 分辨率命令失败（设备 {self.serial}）：{detail.strip()}"
            ) from error
        return result.stdout

    def _read_size_override(self) -> Optional[str]:
        output = self._adb(["wm", "size"])
        match = _OVERRIDE_PATTERN.search(output)
        return match.group(1) if match else None

    def _read_stay_awake(self) -> Optional[str]:
        value = self._adb(
            ["settings", "get", "global", "stay_on_while_plugged_in"]
        ).strip()
        return None if value in {"", "null"} else value

    def _read_snapshot(self) -> _ManagedDeviceSnapshot:
        return _ManagedDeviceSnapshot(
            override=self._read_size_override(),
            stay_on_while_plugged_in=(
                self._read_stay_awake() if self.keep_awake else None
            ),
        )

    def _set_size(self, size: str) -> None:
        self._adb(["wm", "size", size])

    def _verify_override(self, expected: str) -> None:
        override = self._read_size_override()
        if override != expected:
            raise ManagedAdbResolutionError(
                f"设备未接受分辨率 {expected}，当前 override={override or '无'}"
            )

    def _restore_size(self, override: Optional[str]) -> None:
        if override is None:
            self._adb(["wm", "size", "reset"])
            restored = self._read_size_override()
            if restored is not None:
                raise ManagedAdbResolutionError(
                    f"分辨率 reset 后仍存在 override={restored}"
                )
        else:
            self._set_size(override)
            self._verify_override(override)

    def _enable_keep_awake(self) -> None:
        battery = self._adb(["dumpsys", "battery"])
        if _POWERED_PATTERN.search(battery) is None:
            raise ManagedAdbResolutionError(
                "托管手机未接通电源，无法在任务期间可靠保持唤醒"
            )

        self._adb(
            [
                "settings",
                "put",
                "global",
                "stay_on_while_plugged_in",
                _STAY_AWAKE_VALUE,
            ]
        )
        if self._read_stay_awake() != _STAY_AWAKE_VALUE:
            raise ManagedAdbResolutionError("设备未接受临时保持唤醒设置")

        self._adb(["input", "keyevent", "KEYCODE_WAKEUP"])
        self._adb(["wm", "dismiss-keyguard"])
        if _AWAKE_PATTERN.search(self._adb(["dumpsys", "power"])) is None:
            raise ManagedAdbResolutionError("设备唤醒失败，请先手动解锁手机")

    def _restore_keep_awake(self, value: Optional[str]) -> None:
        if value is None:
            self._adb(
                ["settings", "delete", "global", "stay_on_while_plugged_in"]
            )
        else:
            self._adb(
                [
                    "settings",
                    "put",
                    "global",
                    "stay_on_while_plugged_in",
                    value,
                ]
            )
        if self._read_stay_awake() != value:
            raise ManagedAdbResolutionError("设备保持唤醒设置恢复失败")

    def _restore(self, snapshot: _ManagedDeviceSnapshot) -> None:
        error = None
        try:
            self._restore_size(snapshot.override)
        except Exception as restore_error:
            error = restore_error
        finally:
            if self.keep_awake:
                try:
                    self._restore_keep_awake(
                        snapshot.stay_on_while_plugged_in
                    )
                except Exception as restore_error:
                    if error is None:
                        error = restore_error
        if error is not None:
            raise error


_CONTROLLERS = {}
_CONTROLLERS_LOCK = threading.Lock()


def managed_resolution_from_environment(
    environ: Mapping[str, str] = os.environ,
) -> Optional[ManagedAdbResolution]:
    target = environ.get("SRC_ADB_MANAGED_RESOLUTION", "").strip()
    try:
        crop = managed_screenshot_crop_from_environment(environ)
    except ManagedScreenshotCropError as error:
        raise ManagedAdbResolutionError(str(error)) from error
    if crop is not None and not target:
        raise ManagedAdbResolutionError(
            "SRC_ADB_MANAGED_SCREEN_CROP 必须搭配 SRC_ADB_MANAGED_RESOLUTION"
        )
    if not target:
        return None

    keep_awake_value = environ.get("SRC_ADB_MANAGED_KEEP_AWAKE", "").strip().lower()
    if keep_awake_value:
        if keep_awake_value not in {"0", "1", "false", "true", "no", "yes"}:
            raise ManagedAdbResolutionError(
                "SRC_ADB_MANAGED_KEEP_AWAKE 必须是 0/1、false/true 或 no/yes"
            )
        keep_awake = keep_awake_value in {"1", "true", "yes"}
    else:
        keep_awake = crop is not None

    if crop is not None:
        if not _SIZE_PATTERN.fullmatch(target):
            raise ManagedAdbResolutionError(
                "SRC_ADB_MANAGED_RESOLUTION 必须使用 WIDTHxHEIGHT 格式"
            )
        target_width, target_height = (int(value) for value in target.split("x"))
        source_width, source_height = crop.source_size
        accepted_sizes = {
            (source_width, source_height),
            (source_height, source_width),
        }
        if (target_width, target_height) not in accepted_sizes:
            raise ManagedAdbResolutionError(
                "SRC_ADB_MANAGED_RESOLUTION 与 SRC_ADB_MANAGED_SCREEN_CROP "
                "不匹配：裁剪前画布应为 "
                f"{source_width}x{source_height}（或其横竖互换）"
            )

    local_port = environ.get("SRC_TAILSCALE_ADB_LOCAL_PORT", "5555").strip() or "5555"
    serial = environ.get("SRC_ADB_SERIAL", "").strip() or f"127.0.0.1:{local_port}"
    adb_binary = environ.get("SRC_ADB_BINARY", "adb").strip() or "adb"
    key = (serial, target, adb_binary, keep_awake)
    with _CONTROLLERS_LOCK:
        controller = _CONTROLLERS.get(key)
        if controller is None:
            controller = ManagedAdbResolution(
                serial=serial,
                target=target,
                adb_binary=adb_binary,
                keep_awake=keep_awake,
            )
            _CONTROLLERS[key] = controller
        return controller
