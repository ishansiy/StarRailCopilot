import importlib
import sys
import types
import unittest
from unittest import mock

import module.base.timer as timer_module
from module.exception import RequestHumanTakeover


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _ButtonWrapper:
    def __init__(self, name):
        self.name = name
        self.area = (0, 0, 1, 1)
        self.button = (0, 0, 1, 1)

    def load_search(self, _area):
        pass

    def __repr__(self):
        return self.name


def _load_combat_team():
    assets = _module(
        "tasks.combat.assets.assets_combat_team",
        ButtonWrapper=_ButtonWrapper,
        TEAM_SEARCH=_ButtonWrapper("TEAM_SEARCH"),
        TEAM_NEXT=_ButtonWrapper("TEAM_NEXT"),
        TEAM_PREV=_ButtonWrapper("TEAM_PREV"),
        COMBAT_TEAM_PREPARE=_ButtonWrapper("COMBAT_TEAM_PREPARE"),
    )
    for index in range(1, 13):
        setattr(assets, f"TEAM_{index}_CHECK", _ButtonWrapper(f"TEAM_{index}_CHECK"))

    stubs = {
        "module.logger": _module("module.logger", logger=_Logger()),
        "tasks.base.ui": _module("tasks.base.ui", UI=type("UI", (), {})),
        "tasks.combat.assets.assets_combat_team": assets,
    }
    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("tasks.combat.team", None)
        team_module = importlib.import_module("tasks.combat.team")
    return team_module.CombatTeam, assets


class _ReplayBudgetExceeded(Exception):
    pass


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _Device:
    def __init__(self, owner, clock, frame_seconds, max_screenshots):
        self.owner = owner
        self.clock = clock
        self.frame_seconds = frame_seconds
        self.max_screenshots = max_screenshots
        self.screenshots = 0
        self.actions = []
        self.image = object()

    def screenshot(self):
        self.screenshots += 1
        self.clock.advance(self.frame_seconds)
        if self.screenshots > self.max_screenshots:
            raise _ReplayBudgetExceeded("team navigation exceeded the replay frame budget")

    def multi_click(self, button, count, interval=None):
        self.actions.append((button.name, count))
        self.owner.on_multi_click(button.name, count)


def _make_harness(
    base,
    clock,
    current_team,
    frame_seconds,
    max_screenshots,
    on_multi_click=None,
):
    class Harness(base):
        def __init__(self):
            self.current_team = current_team
            self.device = _Device(
                self,
                clock,
                frame_seconds=frame_seconds,
                max_screenshots=max_screenshots,
            )

        def loop(self):
            skip_first = True
            while True:
                if skip_first:
                    skip_first = False
                else:
                    self.device.screenshot()
                yield self.device.image

        def _get_team(self):
            return self.current_team

        def on_multi_click(self, button_name, count):
            if on_multi_click is not None:
                on_multi_click(self, button_name, count)

    return Harness()


class CombatTeamSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.CombatTeam, cls.assets = _load_combat_team()

    def test_partial_batch_is_corrected_within_two_fresh_frames(self):
        clock = _Clock()

        def apply_partial_batch(harness, button_name, count):
            if button_name != "TEAM_NEXT":
                return
            if harness.current_team == 10 and count == 3:
                # The game accepts only two clicks from the first batch.
                harness.current_team = 12
            elif harness.current_team == 12 and count == 1:
                harness.current_team = 1

        harness = _make_harness(
            self.CombatTeam,
            clock,
            current_team=10,
            frame_seconds=34,
            max_screenshots=2,
            on_multi_click=apply_partial_batch,
        )
        with mock.patch.object(timer_module, "time", clock):
            clicked = harness.team_set(1)

        self.assertTrue(clicked)
        self.assertEqual(harness.current_team, 1)
        self.assertEqual(
            harness.device.actions,
            [("TEAM_NEXT", 3), ("TEAM_NEXT", 1)],
        )
        self.assertLessEqual(harness.device.screenshots, 2)

    def test_team_that_never_changes_fails_closed_within_total_budget(self):
        clock = _Clock()
        harness = _make_harness(
            self.CombatTeam,
            clock,
            current_team=12,
            frame_seconds=34,
            max_screenshots=4,
        )
        with mock.patch.object(timer_module, "time", clock):
            with self.assertRaises(RequestHumanTakeover):
                harness.team_set(1)

        self.assertEqual(harness.current_team, 12)
        self.assertGreater(clock.now - 100, 120)
        self.assertLessEqual(clock.now - 100, 136)

    def test_unknown_team_never_emits_navigation_touch(self):
        clock = _Clock()
        harness = _make_harness(
            self.CombatTeam,
            clock,
            current_team=0,
            frame_seconds=34,
            max_screenshots=4,
        )
        with mock.patch.object(timer_module, "time", clock):
            with self.assertRaises(RequestHumanTakeover):
                harness.team_set(1)

        self.assertEqual(harness.device.actions, [])
        self.assertGreater(clock.now - 100, 120)
        self.assertLessEqual(clock.now - 100, 136)

    def test_already_selected_team_returns_without_touch(self):
        clock = _Clock()
        harness = _make_harness(
            self.CombatTeam,
            clock,
            current_team=1,
            frame_seconds=1,
            max_screenshots=0,
        )
        with mock.patch.object(timer_module, "time", clock):
            clicked = harness.team_set(1)

        self.assertFalse(clicked)
        self.assertEqual(harness.device.actions, [])
        self.assertEqual(harness.device.screenshots, 0)

    def test_unknown_frames_after_a_valid_team_do_not_emit_blind_touches(self):
        clock = _Clock()

        def lose_team_number(harness, _button_name, _count):
            # After the first valid navigation action OCR temporarily loses
            # the team number and remains unknown for the rest of the replay.
            harness.current_team = 0

        harness = _make_harness(
            self.CombatTeam,
            clock,
            current_team=12,
            frame_seconds=34,
            max_screenshots=4,
            on_multi_click=lose_team_number,
        )
        with mock.patch.object(timer_module, "time", clock):
            with self.assertRaises(RequestHumanTakeover):
                harness.team_set(1)

        self.assertEqual(harness.device.actions, [("TEAM_NEXT", 1)])
        self.assertGreater(clock.now - 100, 120)
        self.assertLessEqual(clock.now - 100, 136)


if __name__ == "__main__":
    unittest.main()
