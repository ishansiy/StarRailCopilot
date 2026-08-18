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
        image = np.arange(720 * 1334, dtype=np.int32).reshape(720, 1334)
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"}
        )

        normalized = apply_managed_screenshot_crop(image, crop)

        self.assertEqual(normalized.shape, (720, 1280))
        np.testing.assert_array_equal(normalized, image[:, :1280])
        self.assertTrue(normalized.flags.c_contiguous)

    def test_maps_asset_coordinates_into_source_frame(self):
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "12,8,42,2"}
        )

        self.assertEqual(crop.asset_point_to_source(100, 200), (112, 208))

    def test_exposes_portrait_source_size_for_orientation_recheck(self):
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"}
        )

        self.assertEqual(crop.portrait_source_size, (720, 1334))

    def test_maatouch_mapping_does_not_scale_into_cropped_inset(self):
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"}
        )

        self.assertEqual(
            crop.convert_touch_point(
                1200,
                300,
                max_x=1334,
                max_y=720,
            ),
            (1200, 300, 1334, 720),
        )

    def test_leaves_portrait_launcher_frame_unchanged(self):
        image = np.zeros((1334, 720, 3), dtype=np.uint8)
        crop = managed_screenshot_crop_from_environment(
            {"SRC_ADB_MANAGED_SCREEN_CROP": "0,0,54,0"}
        )

        self.assertIs(apply_managed_screenshot_crop(image, crop), image)


if __name__ == "__main__":
    unittest.main()
