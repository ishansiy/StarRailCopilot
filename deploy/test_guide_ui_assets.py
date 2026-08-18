import unittest
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def template_match(template: np.ndarray, target: np.ndarray):
    if template.ndim == 2:
        template = template[:, :, None]
        target = target[:, :, None]

    template = template.astype(np.float64).transpose(2, 0, 1)
    target = target.astype(np.float64)
    height, width = template.shape[1:]
    windows = np.lib.stride_tricks.sliding_window_view(
        target, (height, width), axis=(0, 1)
    )

    centered_template = template - template.mean(axis=(1, 2), keepdims=True)
    numerator = np.einsum(
        "ijchw,chw->ij", windows, centered_template, optimize=True
    )
    sums = windows.sum(axis=(-2, -1))
    square_sums = np.einsum("ijchw,ijchw->ijc", windows, windows, optimize=True)
    target_energy = (square_sums - sums * sums / (height * width)).sum(axis=2)
    template_energy = np.square(centered_template).sum()
    denominator = np.sqrt(template_energy * np.maximum(target_energy, 0))
    scores = np.divide(
        numerator,
        denominator,
        out=np.full_like(numerator, -1.0),
        where=denominator > 0,
    )
    point_y, point_x = np.unravel_index(np.argmax(scores), scores.shape)
    return float(scores[point_y, point_x]), (int(point_x), int(point_y))


def match_asset(asset, area, search, screenshot, *, luma=False):
    template = load_rgb(ROOT / asset)
    x1, y1, x2, y2 = area
    sx1, sy1, sx2, sy2 = search
    template = template[y1:y2, x1:x2]
    target = screenshot[sy1:sy2, sx1:sx2]
    if luma:
        weights = np.array((0.299, 0.587, 0.114))
        template = np.rint(template @ weights).astype(np.uint8)
        target = np.rint(target @ weights).astype(np.uint8)
    score, point = template_match(template, target)
    offset = (point[0] + sx1 - x1, point[1] + sy1 - y1)
    return score, offset


class GuideUiAssetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / "deploy/fixtures"
        cls.daily = load_rgb(fixtures / "guide-daily-v44.png")
        cls.survival = load_rgb(fixtures / "guide-survival-v44.png")
        cls.main = load_rgb(fixtures / "main-header-v44.png")

    def test_new_guide_badge_matches_across_tabs(self):
        spec = (
            "assets/share/base/page/GUIDE_CHECK.2.png",
            (98, 20, 127, 57),
            (78, 0, 147, 77),
        )
        for screenshot in (self.daily, self.survival):
            score, offset = match_asset(*spec, screenshot)
            self.assertGreater(score, 0.99)
            self.assertEqual(offset, (0, 0))

    def test_new_guide_badge_rejects_main_page(self):
        score, _ = match_asset(
            "assets/share/base/page/GUIDE_CHECK.2.png",
            (98, 20, 127, 57),
            (78, 0, 147, 77),
            self.main,
        )
        self.assertLess(score, 0.85)

    def test_legacy_badge_does_not_mask_the_v44_regression(self):
        score, _ = match_asset(
            "assets/share/base/page/GUIDE_CHECK.png",
            (64, 297, 83, 326),
            (44, 277, 103, 346),
            self.daily,
        )
        self.assertLess(score, 0.85)

    def test_existing_tab_assets_follow_the_shifted_v44_bar(self):
        tab_search = (139, 84, 599, 144)
        cases = (
            (
                "assets/share/dungeon/tab/TAB_DAILY_TRAINING_CHECK.png",
                (241, 114, 281, 134),
                self.daily,
            ),
            (
                "assets/share/dungeon/tab/TAB_SURVIVAL_INDEX_CLICK.png",
                (332, 113, 370, 133),
                self.daily,
            ),
            (
                "assets/share/dungeon/tab/TAB_SURVIVAL_INDEX_CHECK.png",
                (332, 113, 370, 133),
                self.survival,
            ),
        )
        for asset, area, screenshot in cases:
            score, offset = match_asset(
                asset, area, tab_search, screenshot, luma=True
            )
            self.assertGreater(score, 0.95)
            self.assertEqual(offset, (-45, 0))

    def test_fixtures_and_asset_exclude_personal_screen_content(self):
        allowed = np.zeros((720, 1280), dtype=bool)
        allowed[0:77, 78:147] = True
        allowed[84:144, 139:599] = True
        allowed[277:346, 44:103] = True
        for screenshot in (self.daily, self.survival):
            self.assertFalse(np.any(screenshot[~allowed]))

        allowed_main = np.zeros((720, 1280), dtype=bool)
        allowed_main[0:77, 78:147] = True
        self.assertFalse(np.any(self.main[~allowed_main]))

        asset = load_rgb(ROOT / "assets/share/base/page/GUIDE_CHECK.2.png")
        allowed_asset = np.zeros((720, 1280), dtype=bool)
        allowed_asset[20:57, 98:127] = True
        self.assertFalse(np.any(asset[~allowed_asset]))


if __name__ == "__main__":
    unittest.main()
