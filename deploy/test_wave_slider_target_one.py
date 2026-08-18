import importlib
import sys
import types
import unittest
from unittest import mock


def _module(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Asset:
    def __init__(self, name):
        self.name = name
        self.button = (0, 0, 1, 1)

    def load_search(self, *_args):
        pass

    def load_offset(self, *_args):
        pass

    def clear_offset(self):
        pass


class _FakeSlider:
    set_calls = []

    def __init__(self, *_args, **_kwargs):
        pass

    def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


def _load_combat_prepare():
    assets = {
        name: _Asset(name)
        for name in (
            "WAVE_CHECK",
            "WAVE_CHECK_SEARCH",
            "WAVE_SLIDER",
            "OCR_WAVE_COUNT",
            "WAVE_PLUS",
            "WAVE_MINUS",
        )
    }
    stubs = {
        "module.base.button": _module(
            "module.base.button",
            ClickButton=lambda button, name=None: _Asset(name or "click"),
        ),
        "module.base.timer": _module(
            "module.base.timer",
            Timer=type("Timer", (), {}),
        ),
        "module.logger": _module("module.logger", logger=_Logger()),
        "module.ocr.ocr": _module(
            "module.ocr.ocr",
            Digit=type("Digit", (), {"__init__": lambda self, *_args, **_kwargs: None}),
        ),
        "tasks.combat.assets.assets_combat_prepare": _module(
            "tasks.combat.assets.assets_combat_prepare",
            **assets,
        ),
        "tasks.combat.assets.assets_combat_relics": _module(
            "tasks.combat.assets.assets_combat_relics",
            COMBAT_RELIC_ENTER=_Asset("COMBAT_RELIC_ENTER"),
        ),
        "tasks.combat.stamina_status": _module(
            "tasks.combat.stamina_status",
            StaminaStatus=type("StaminaStatus", (), {}),
        ),
        "tasks.item.slider": _module(
            "tasks.item.slider",
            Slider=_FakeSlider,
        ),
    }
    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("tasks.combat.prepare", None)
        return importlib.import_module("tasks.combat.prepare"), assets


class WaveSliderTargetOneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepare_module, cls.assets = _load_combat_prepare()

    def setUp(self):
        _FakeSlider.set_calls.clear()

    def _prepare(self):
        prepare = self.prepare_module.CombatPrepare.__new__(
            self.prepare_module.CombatPrepare
        )
        prepare.match_template_luma = lambda *_args, **_kwargs: False
        prepare.ui_ensure_index = mock.Mock()
        return prepare

    def test_target_one_skips_stale_track_but_keeps_exact_ocr_check(self):
        prepare = self._prepare()

        prepare.combat_set_wave(count=1, total=24)

        self.assertEqual(_FakeSlider.set_calls, [])
        prepare.ui_ensure_index.assert_called_once()
        args, kwargs = prepare.ui_ensure_index.call_args
        self.assertEqual(args[0], 1)
        self.assertIs(kwargs["next_button"], self.assets["WAVE_PLUS"])
        self.assertIs(kwargs["prev_button"], self.assets["WAVE_MINUS"])

    def test_other_targets_still_use_slider_geometry(self):
        prepare = self._prepare()

        prepare.combat_set_wave(count=2, total=24)

        self.assertEqual(_FakeSlider.set_calls, [((2, 24), {})])
        prepare.ui_ensure_index.assert_called_once()


if __name__ == "__main__":
    unittest.main()
