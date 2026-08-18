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


class ManagedAdbResolutionError(RuntimeError):
    """Raised when an explicitly requested display-size change cannot be applied."""


@dataclass(frozen=True)
class _DisplaySizeSnapshot:
    override: Optional[str]


class ManagedAdbResolutionLease:
    def __init__(self, controller: "ManagedAdbResolution") -> None:
        self._controller = controller
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
            self._controller._release()
            self._released = True


class ManagedAdbResolution:
    """Reference-counted ADB display-size override for WebUI worker processes."""

    def __init__(
        self,
        serial: str,
        target: str,
        adb_binary: str = "adb",
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
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
        self._runner = runner
        self._lock = threading.Lock()
        self._leases = 0
        self._snapshot: Optional[_DisplaySizeSnapshot] = None

    def acquire(self) -> ManagedAdbResolutionLease:
        with self._lock:
            if self._leases == 0:
                snapshot = self._read_snapshot()
                self._set_size(self.target)
                try:
                    self._verify_override(self.target)
                except Exception:
                    self._restore(snapshot)
                    raise
                self._snapshot = snapshot
            self._leases += 1
        return ManagedAdbResolutionLease(self)

    def _release(self) -> None:
        with self._lock:
            if self._leases == 0:
                return
            if self._leases > 1:
                self._leases -= 1
                return

            snapshot = self._snapshot
            if snapshot is not None:
                self._restore(snapshot)
            self._leases = 0
            self._snapshot = None

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

    def _read_snapshot(self) -> _DisplaySizeSnapshot:
        output = self._adb(["wm", "size"])
        match = _OVERRIDE_PATTERN.search(output)
        return _DisplaySizeSnapshot(override=match.group(1) if match else None)

    def _set_size(self, size: str) -> None:
        self._adb(["wm", "size", size])

    def _verify_override(self, expected: str) -> None:
        snapshot = self._read_snapshot()
        if snapshot.override != expected:
            raise ManagedAdbResolutionError(
                f"设备未接受分辨率 {expected}，当前 override={snapshot.override or '无'}"
            )

    def _restore(self, snapshot: _DisplaySizeSnapshot) -> None:
        if snapshot.override is None:
            self._adb(["wm", "size", "reset"])
            restored = self._read_snapshot()
            if restored.override is not None:
                raise ManagedAdbResolutionError(
                    f"分辨率 reset 后仍存在 override={restored.override}"
                )
        else:
            self._set_size(snapshot.override)
            self._verify_override(snapshot.override)


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
    key = (serial, target, adb_binary)
    with _CONTROLLERS_LOCK:
        controller = _CONTROLLERS.get(key)
        if controller is None:
            controller = ManagedAdbResolution(
                serial=serial,
                target=target,
                adb_binary=adb_binary,
            )
            _CONTROLLERS[key] = controller
        return controller
