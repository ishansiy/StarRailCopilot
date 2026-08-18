import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

import numpy as np


_CROP_PATTERN = re.compile(r"^(0|[1-9]\d*),(0|[1-9]\d*),(0|[1-9]\d*),(0|[1-9]\d*)$")
_ASSET_WIDTH = 1280
_ASSET_HEIGHT = 720


class ManagedScreenshotCropError(ValueError):
    """Raised when an explicitly requested screenshot crop is invalid."""


@dataclass(frozen=True)
class ManagedScreenshotCrop:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def source_size(self):
        return (
            _ASSET_WIDTH + self.left + self.right,
            _ASSET_HEIGHT + self.top + self.bottom,
        )

    @property
    def portrait_source_size(self):
        width, height = self.source_size
        return height, width

    def asset_point_to_source(self, x: int, y: int):
        return x + self.left, y + self.top

    def convert_touch_point(
        self,
        x: int,
        y: int,
        *,
        max_x: int,
        max_y: int,
    ):
        """Map SRC asset coordinates onto MaaTouch's uncropped canvas."""
        x, y = self.asset_point_to_source(x, y)
        source_width, source_height = self.source_size
        x = x * max_x // source_width
        y = y * max_y // source_height
        return x, y, max_x, max_y


def managed_screenshot_crop_from_environment(
    environ: Mapping[str, str] = os.environ,
) -> Optional[ManagedScreenshotCrop]:
    raw = environ.get("SRC_ADB_MANAGED_SCREEN_CROP", "").strip()
    if not raw:
        return None
    if not _CROP_PATTERN.fullmatch(raw):
        raise ManagedScreenshotCropError(
            "SRC_ADB_MANAGED_SCREEN_CROP 必须使用 LEFT,TOP,RIGHT,BOTTOM 格式，"
            "例如 0,0,54,0"
        )

    left, top, right, bottom = (int(value) for value in raw.split(","))
    if left + right == 0 and top + bottom == 0:
        raise ManagedScreenshotCropError(
            "SRC_ADB_MANAGED_SCREEN_CROP 至少需要裁剪一个边缘"
        )
    return ManagedScreenshotCrop(left, top, right, bottom)


def apply_managed_screenshot_crop(
    image: np.ndarray,
    crop: Optional[ManagedScreenshotCrop],
) -> np.ndarray:
    """Crop a known phone safe-area frame to SRC's 1280x720 asset canvas.

    Frames with any other dimensions are left untouched. This is important while
    Android is still on its portrait launcher during app startup.
    """
    if crop is None:
        return image

    height, width = image.shape[:2]
    if (width, height) != crop.source_size:
        return image

    x_end = width - crop.right if crop.right else width
    y_end = height - crop.bottom if crop.bottom else height
    normalized = image[crop.top:y_end, crop.left:x_end]
    normalized_height, normalized_width = normalized.shape[:2]
    if (normalized_width, normalized_height) != (_ASSET_WIDTH, _ASSET_HEIGHT):
        raise ManagedScreenshotCropError(
            "托管截图裁剪结果不是 1280x720："
            f"{normalized_width}x{normalized_height}"
        )
    return np.ascontiguousarray(normalized)
