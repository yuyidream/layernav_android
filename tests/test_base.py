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
        self._detect_returns: list[str | None] = ["L0"]

    def detect(self, adb, scale_w) -> str | None:
        if self._detect_returns:
            return self._detect_returns.pop(0)
        return "L0"

    def detect_layer(self, adb, scale_w, layer) -> bool:
        return self.detect(adb, scale_w) == layer

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

    def test_back_one_retries_when_layer_unchanged(self):
        m = _TestModel()
        adb = MockAdb()
        # 前 2 次 BACK 后仍在 L2 → 重试, 第 3 次回到 L1
        m._detect_returns = ["L2", "L2", "L2", "L1"]
        result = m.back_one(adb, 1.0)
        assert result == "L1"
        assert adb._events == [KEYCODE_BACK, KEYCODE_BACK, KEYCODE_BACK]

    def test_back_one_falls_to_back_recover_after_retries(self):
        m = _TestModel()
        adb = MockAdb()

        def _cold_start(adb, target, scale):
            pass

        m._cold_start = _cold_start
        m._detect_returns = [
            "L2",        # detect() → cur
            "L2",        # attempt 1 → next (same)
            "L2",        # attempt 2 → next (same)
            "L2",        # attempt 3 → next (same) → 走 back_recover
            # ── back_recover ──
            "L1",        # home_one post-detect
            "L1",        # advance → detect("L1")
            "L1",        # advance → detect_layer("L1") call handler
            "L1",        # back_recover final detect_layer("L1")
        ]
        result = m.back_one(adb, 1.0, max_retries=3)
        assert result == "L1"
        assert KEYCODE_HOME in adb._events  # back_recover 发了 HOME
        assert adb._events[:3] == [KEYCODE_BACK, KEYCODE_BACK, KEYCODE_BACK]


class TestHomeOne:
    def test_home_one_sends_keyevent_and_returns_new_layer(self):
        m = _TestModel()
        adb = MockAdb()
        m._detect_returns = ["L2", "L0"]
        result = m.home_one(adb, 1.0)
        assert adb._events == [KEYCODE_HOME]
        assert result == "L0"

    def test_home_one_notifies_listener(self):
        m = _TestModel()
        lst = _RecordListener()
        m.add_listener(lst)
        adb = MockAdb()
        m._detect_returns = ["L2", "L0"]
        m.home_one(adb, 1.0)
        assert len(lst.transitions) == 1
        assert lst.transitions[0] == ("L2", "L0", "home_one")


class TestBackRecover:
    def test_back_recover_succeeds_after_cold_start(self):
        m = _TestModel()
        adb = MockAdb()

        def _cold_start(adb, target, scale):
            m._detect_returns = ["L1", "L1"]

        m._cold_start = _cold_start

        m._detect_returns = [
            "L1",  # detect_layer("L1") → True, skip fast-forward loop
            "L1",  # final detect_layer("L1") return check
        ]
        ok = m.back_recover(adb, "L1", 1.0)
        assert ok is True
        assert adb._events == [KEYCODE_HOME]

    def test_back_recover_fails_when_cold_start_fails(self):
        m = _TestModel()
        adb = MockAdb()
        lst = _RecordListener()
        m.add_listener(lst)

        def _cold_start(adb, target, scale):
            raise TimeoutError("mock fail")

        m._cold_start = _cold_start

        ok = m.back_recover(adb, "L1", 1.0)
        assert ok is False
        assert lst.recoveries == [("L1", False)]


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

        # back_one(): 3 retries stuck at L2 → fallback to back_recover("L1")
        # back_recover fast-forward: detect None → notify_recovery(False)
        m._detect_returns = [
            "L2",       # back_one → detect() (cur)
            "L2",       # attempt 1 → next (same → retry)
            "L2",       # attempt 2 → next (same → retry)
            "L2",       # attempt 3 → next (same → fallback)
            # back_recover → home_one
            "L0",       # home_one cur detect (before KEYCODE_HOME)
            "L0",       # home_one next detect (after KEYCODE_HOME)
            # fast-forward loop
            "L0",       # detect_layer("L1") → detect → L0 → not L1 → enter loop
            None,       # detect() in loop → None → retry 1
            None,       # detect() retry 1 → None
            None,       # detect() retry 2 → None → for-else fail
        ]
        ok = m.back_one(adb, 1.0)
        assert ok == "L1"
        assert len(lst.recoveries) == 1
        assert lst.recoveries[0] == ("L1", False)
