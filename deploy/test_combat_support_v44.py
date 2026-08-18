import unittest
from pathlib import Path

import numpy as np

if __package__:
    from .test_guide_ui_assets import load_rgb, match_asset
else:
    from test_guide_ui_assets import load_rgb, match_asset


ROOT = Path(__file__).resolve().parents[1]


def non_black_bbox(path: Path) -> tuple[int, int, int, int]:
    image = load_rgb(path)
    mask = np.any(image != 0, axis=2)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise AssertionError(f"empty asset: {path}")
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def support_asset_spec(base: str):
    asset = ROOT / base
    area = non_black_bbox(asset)
    search_asset = asset.with_name(f"{asset.stem}.SEARCH.png")
    if search_asset.exists():
        search = non_black_bbox(search_asset)
    else:
        x1, y1, x2, y2 = area
        search = max(0, x1 - 20), max(0, y1 - 20), min(1280, x2 + 20), min(720, y2 + 20)
    return base, area, search


class CombatSupportV44Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / "deploy/fixtures"
        cls.support = load_rgb(fixtures / "combat-support-v44.png")
        cls.not_support = load_rgb(fixtures / "combat-support-negative-v44.png")

    def test_v44_support_page_matches_language_neutral_marker_pair(self):
        cases = (
            "assets/share/combat/support/COMBAT_SUPPORT_LIST.png",
            "assets/share/combat/support_dev/LIST_REFRESH.png",
        )
        for asset in cases:
            score, offset = match_asset(
                *support_asset_spec(asset), self.support, luma=True
            )
            self.assertGreater(score, 0.99, asset)
            self.assertEqual(offset, (54, 0), asset)

    def test_v44_selected_support_tab_uses_same_text_free_variant_for_all_languages(self):
        cn_path = ROOT / "assets/cn/combat/support_tab/SUPPORT_CHECK.2.png"
        en_path = ROOT / "assets/en/combat/support_tab/SUPPORT_CHECK.2.png"
        self.assertTrue(cn_path.exists())
        self.assertTrue(en_path.exists())
        self.assertTrue(np.array_equal(load_rgb(cn_path), load_rgb(en_path)))

        for path in (cn_path, en_path):
            asset = path.relative_to(ROOT).as_posix()
            score, offset = match_asset(
                *support_asset_spec(asset), self.support, luma=True
            )
            self.assertGreater(score, 0.99, asset)
            self.assertEqual(offset, (0, 0), asset)

    def test_widened_search_keeps_legacy_marker_positions(self):
        cases = (
            "assets/share/combat/support/COMBAT_SUPPORT_LIST.png",
            "assets/share/combat/support_dev/LIST_REFRESH.png",
        )
        for asset in cases:
            screenshot = load_rgb(ROOT / asset)
            score, offset = match_asset(
                *support_asset_spec(asset), screenshot, luma=True
            )
            self.assertGreater(score, 0.99, asset)
            self.assertEqual(offset, (0, 0), asset)

    def test_marker_combinations_reject_unrelated_team_page(self):
        marker_scores = []
        for asset in (
            "assets/share/combat/support/COMBAT_SUPPORT_LIST.png",
            "assets/share/combat/support_dev/LIST_REFRESH.png",
        ):
            score, _ = match_asset(
                *support_asset_spec(asset), self.not_support, luma=True
            )
            marker_scores.append(score)
        self.assertFalse(
            all(score > 0.85 for score in marker_scores), marker_scores
        )

        tab_asset = "assets/cn/combat/support_tab/SUPPORT_CHECK.2.png"
        tab_score, _ = match_asset(
            *support_asset_spec(tab_asset), self.not_support, luma=True
        )
        self.assertFalse(
            marker_scores[0] > 0.85 and tab_score > 0.85,
            (marker_scores[0], tab_score),
        )

    def test_fixture_and_assets_exclude_personal_screen_content(self):
        allowed = np.zeros((720, 1280), dtype=bool)
        allowed[643:683, 32:126] = True
        allowed[629:688, 290:401] = True
        allowed[23:89, 480:538] = True
        for fixture in (self.support, self.not_support):
            self.assertFalse(np.any(fixture[~allowed]))

        for asset, allowed_area in (
            (
                "assets/share/combat/support/COMBAT_SUPPORT_LIST.png",
                (39, 645, 65, 669),
            ),
            (
                "assets/share/combat/support_dev/LIST_REFRESH.png",
                (310, 649, 327, 667),
            ),
        ):
            image = load_rgb(ROOT / asset)
            asset_allowed = np.zeros((720, 1280), dtype=bool)
            x1, y1, x2, y2 = allowed_area
            asset_allowed[y1:y2, x1:x2] = True
            self.assertFalse(np.any(image[~asset_allowed]), asset)

        for asset in (
            "assets/cn/combat/support_tab/SUPPORT_CHECK.2.png",
            "assets/en/combat/support_tab/SUPPORT_CHECK.2.png",
        ):
            image = load_rgb(ROOT / asset)
            asset_allowed = np.zeros((720, 1280), dtype=bool)
            asset_allowed[43:69, 500:518] = True
            self.assertFalse(np.any(image[~asset_allowed]), asset)


if __name__ == "__main__":
    unittest.main()
