import unittest

import numpy as np

from module.device.managed_screenshot_crop import (
    ManagedScreenshotCropError,
    apply_managed_screenshot_crop,
    managed_screenshot_crop_from_environment,
)


class ManagedScreenshotCropTest(unittest.TestCase):
    def test_disabled_when_environment_variable_is_absent(self):
        self.assertIsNone(managed_screenshot_crop_from_environment({}))

    def test_invalid_crop_is_rejected(self):
        with self.assertRaises(ManagedScreenshotCropError):
            managed_screenshot_crop_from_environment(
                {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,-52,0"}
            )

    def test_crops_hardware_safe_inset_to_asset_resolution(self):
        image = np.arange(720 * 1332, dtype=np.int32).reshape(720, 1332)
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,52,0"}
        )

        normalized = apply_managed_screenshot_crop(image, crop)

        self.assertEqual(normalized.shape, (720, 1280))
        np.testing.assert_array_equal(normalized, image[:, :1280])

    def test_leaves_portrait_launcher_frame_unchanged(self):
        image = np.zeros((1332, 720, 3), dtype=np.uint8)
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,52,0"}
        )

        self.assertIs(apply_managed_screenshot_crop(image, crop), image)


if __name__ == "__main__":
    unittest.main()
