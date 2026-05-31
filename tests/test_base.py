"""Tests for the layernav_android base framework."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from layernav_android import (
    AdbProtocol,
    BaseLayerModel,
    KEYCODE_BACK,
    KEYCODE_HOME,
    LayerDef,
    LayerListener,
    POST_TRANSITION_SLEEP,
)


# ── Mock ADB ──────────────────────────────────────────────────────────────────


class MockAdb:
    def __init__(self) -> None:
        self._events: list[int] = []
        self._shell_commands: list[list[str]] = []

    def key_event(self, code: int) -> None:
        self._events.append(code)

    def foreground_package(self) -> str:
        return "com.tencent.mm"

    def screencap(self) -> bytes:
        return b"\x89PNG"

    def tap(self, x: int, y: int) -> None:
        pass

    def _run(self, args: list[str]) -> str:
        self._shell_commands.append(args)
        return ""


# ── Minimal subclass for testing ──────────────────────────────────────────────


class _TestModel(BaseLayerModel):
    layers = [
        LayerDef("L0", "home", "手机主屏幕", "foreground check"),
        LayerDef("L1", "main", "主界面", "template A"),
        LayerDef("L2", "detail", "详情页", "template B"),
        LayerDef("L3", "sub", "子页面", "template C"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._detect_returns: list[str] = ["L0"]

    def detect(self, adb, scale_w) -> str:
        if self._detect_returns:
            return self._detect_returns.pop(0)
        return "L0"

    def _on_L0(self, adb, scale_w, *, quick=False) -> str | None:
        return "L1"

    def _on_L1(self, adb, scale_w, *, quick=False) -> str | None:
        return "L2"

    def _on_L2(self, adb, scale_w, *, quick=False) -> str | None:
        return "L3"

    def _on_L3(self, adb, scale_w, *, quick=False) -> str | None:
        return None


class _RecordListener:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str, str]] = []
        self.timeouts: list[tuple[str, str, float]] = []
        self.recoveries: list[tuple[str, bool]] = []

    def on_transition(self, from_layer: str, to_layer: str, method: str) -> None:
        self.transitions.append((from_layer, to_layer, method))

    def on_timeout(self, from_layer: str, target_layer: str, elapsed_s: float) -> None:
        self.timeouts.append((from_layer, target_layer, elapsed_s))

    def on_recovery(self, target_layer: str, ok: bool) -> None:
        self.recoveries.append((target_layer, ok))


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestLayerDef:
    def test_layer_def_construction(self):
        ld = LayerDef("L1", "main", "主界面", "detect method desc")
        assert ld.key == "L1"
        assert ld.name == "main"
        assert ld.label_cn == "主界面"
        assert ld.detection == "detect method desc"


class TestConstants:
    def test_keycode_constants(self):
        assert KEYCODE_BACK == 4
        assert KEYCODE_HOME == 3


class TestDetect:
    def test_detect_calls_subclass(self):
        m = _TestModel()
        m._detect_returns = ["L1"]
        result = m.detect(MagicMock(), 1.0)
        assert result == "L1"


class TestBackOne:
    def test_back_one_sends_keyevent_and_returns_new_layer(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L2", "L1"]
        result = m.back_one(adb, 1.0)
        assert adb._events == [KEYCODE_BACK]
        assert result == "L1"

    def test_back_one_notifies_listener(self):
        m = _TestModel()
        lst = _RecordListener()
        m.add_listener(lst)
        adb = MockAdb()
        m._detect_returns = ["L2", "L1"]
        m.back_one(adb, 1.0)
        assert len(lst.transitions) == 1
        assert lst.transitions[0] == ("L2", "L1", "back_one")


class TestEnterNext:
    def test_enter_next_calls_handler_and_verifies(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L0", "L1"]
        ok = m.enter_next(adb, 1.0)
        assert ok is True

    def test_enter_next_returns_true_when_handler_returns_none(self):
        m = _TestModel()
        adb = MockAdb()

        def _on_L0_none(*args, **kwargs):
            return None
        m._on_L0 = _on_L0_none
        m._detect_returns = ["L0"]
        ok = m.enter_next(adb, 1.0)
        assert ok is True

    def test_enter_next_polls_until_target_reached(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L1", "L1", "L2"]

        def _on_L1_poll(*args, **kwargs):
            return "L2"
        m._on_L1 = _on_L1_poll

        ok = m.enter_next(adb, 1.0)
        assert ok is True

    def test_enter_next_returns_false_on_timeout(self):
        m = _TestModel()
        adb = MockAdb()

        def _on_L1_stuck(*args, **kwargs):
            return "L2"
        m._on_L1 = _on_L1_stuck
        m._detect_returns = ["L1", "L1", "L1", "L1", "L1"]

        ok = m.enter_next(adb, 1.0, max_wait_s=1.0)
        assert ok is False

    def test_enter_next_notifies_on_success(self):
        m = _TestModel()
        lst = _RecordListener()
        m.add_listener(lst)
        adb = MockAdb()
        m._detect_returns = ["L0", "L1"]
        m.enter_next(adb, 1.0)
        assert len(lst.transitions) == 1
        assert lst.transitions[0] == ("L0", "L1", "enter_next")

    def test_enter_next_notifies_on_timeout(self):
        m = _TestModel()
        lst = _RecordListener()
        m.add_listener(lst)
        adb = MockAdb()

        def _on_L1_stuck(*args, **kwargs):
            return "L2"
        m._on_L1 = _on_L1_stuck
        m._detect_returns = ["L1", "L1", "L1", "L1", "L1"]

        m.enter_next(adb, 1.0, max_wait_s=1.0)
        assert len(lst.timeouts) == 1
        assert lst.timeouts[0][0] == "L1"
        assert lst.timeouts[0][1] == "L2"


class TestBack:
    def test_back_stops_when_at_target(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L1"]
        ok = m.back(adb, "L1", 1.0)
        assert ok is True

    def test_back_uses_back_one_then_stops(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L3", "L2", "L1", "L1"]
        ok = m.back(adb, "L1", 1.0)
        assert ok is True
        assert adb._events == [KEYCODE_BACK]


class TestAdvance:
    def test_advance_steps_until_target(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = [
            "L1", "L1", "L2", "L2", "L2", "L3", "L3",
        ]
        ok = m.advance(adb, "L3", 1.0)
        assert ok is True

    def test_advance_calls_target_handler_with_quick_false(self):
        m = _TestModel()
        adb = MockAdb()
        calls = []

        def _on_L3_track(*args, **kwargs):
            calls.append(kwargs.get("quick"))
            return None
        m._on_L3 = _on_L3_track
        m._detect_returns = ["L3"]

        m.advance(adb, "L3", 1.0, quick=True)
        assert calls == [False]

    def test_advance_stops_on_enter_next_failure(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L1", "L1", "L1", "L1"]

        ok = m.advance(adb, "L2", 1.0, max_wait_s=1.0)
        assert ok is False


class TestRestore:
    def test_restore_backs_when_above(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L3", "L2", "L1", "L1", "L1"]
        ok = m.restore(adb, "L1", 1.0)
        assert ok is True

    def test_restore_advances_when_below(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L0", "L0", "L1", "L1", "L1", "L2", "L2"]
        ok = m.restore(adb, "L2", 1.0)
        assert ok is True

    def test_restore_returns_true_when_at_target(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L2"]
        ok = m.restore(adb, "L2", 1.0)
        assert ok is True


class TestListener:
    def test_add_listener_and_notify(self):
        m = _TestModel()
        lst1 = _RecordListener()
        lst2 = _RecordListener()
        m.add_listener(lst1)
        m.add_listener(lst2)
        adb = MockAdb()
        m._detect_returns = ["L2", "L1"]
        m.back_one(adb, 1.0)
        assert len(lst1.transitions) == 1
        assert len(lst2.transitions) == 1

    def test_notify_recovery(self):
        m = _TestModel()
        lst = _RecordListener()
        m.add_listener(lst)
        adb = MockAdb()

        m._detect_returns = [
            "L2", "L2", "L2",
            "L2", "L2", "L2",
            "L2", "L2", "L0",
            "L0",
        ]
        ok = m.back(adb, "L1", 1.0)
        assert ok is False
        assert len(lst.recoveries) == 1
        assert lst.recoveries[0] == ("L1", False)
