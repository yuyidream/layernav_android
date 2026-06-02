"""Tests for cold_start.reboot fallback, reposition, and dhash helpers."""
from __future__ import annotations

from unittest import mock
from unittest.mock import call, MagicMock

import numpy as np
import pytest

from layernav_android._protocol import AdbProtocol
from layernav_android.cold_start import (
    _wait_for_device,
    _wait_for_boot_completed,
    cold_start_app_from_launcher,
)
from layernav_android.contrib.wechat import (
    _reposition_dhash64,
    _reposition_hamming,
    RepositionResult,
)


# ── dHash helper tests ────────────────────────────────────────────────────────

class TestRepositionDhash:
    """_reposition_dhash64 / _reposition_hamming pure-function tests."""

    @pytest.fixture
    def white(self) -> np.ndarray:
        return np.full((100, 100, 3), 255, dtype=np.uint8)

    @pytest.fixture
    def black(self) -> np.ndarray:
        return np.full((100, 100, 3), 0, dtype=np.uint8)

    def test_dhash_same_image(self, white):
        d1 = _reposition_dhash64(white)
        d2 = _reposition_dhash64(white)
        assert _reposition_hamming(d1, d2) == 0

    def test_dhash_black_vs_white(self, white, black):
        """dHash compares relative intensity patterns, not absolute values.
        Both all-black and all-white are featureless → identical hashes."""
        d1 = _reposition_dhash64(white)
        d2 = _reposition_dhash64(black)
        assert _reposition_hamming(d1, d2) == 0

    def test_dhash_different_images(self):
        img1 = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        img2 = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        d1 = _reposition_dhash64(img1)
        d2 = _reposition_dhash64(img2)
        assert 0 <= _reposition_hamming(d1, d2) <= 64

    def test_hamming_zero(self):
        a = np.array([True] * 64, dtype=bool)
        b = np.array([True] * 64, dtype=bool)
        assert _reposition_hamming(a, b) == 0

    def test_hamming_all_diff(self):
        a = np.array([True] * 64, dtype=bool)
        b = np.array([False] * 64, dtype=bool)
        assert _reposition_hamming(a, b) == 64


# ── RepositionResult tests ────────────────────────────────────────────────────

class TestRepositionResult:
    def test_defaults(self):
        r = RepositionResult(ok=False)
        assert r.ok is False
        assert r.reason == ""
        assert r.swipes_used == 0
        assert r.swipes_max == 0
        assert r.early_stop_triggered is False
        assert r.swipe_details == []

    def test_success(self):
        r = RepositionResult(
            ok=True, swipes_used=2, swipes_max=3,
            early_stop_triggered=True,
            swipe_details=[{"index": 1}, {"index": 2}],
        )
        assert r.ok is True
        assert r.swipes_used == 2
        assert r.early_stop_triggered is True

    def test_failure_with_reason(self):
        r = RepositionResult(ok=False, reason="step1_nav_exhausted")
        assert r.ok is False
        assert r.reason == "step1_nav_exhausted"


# ── cold_start allow_reboot tests ─────────────────────────────────────────────

class MockAdb:
    """Minimal mock implementing AdbProtocol subset for cold_start tests."""

    def __init__(self, foreground_pkg: str = "com.android.launcher"):
        self._foreground = foreground_pkg
        self._calls: list[str] = []
        self._run_args: list[list[str]] = []

    def _run(self, args: list[str]) -> str:
        self._run_args.append(list(args))
        return ""

    def screencap(self) -> bytes:
        return b""

    def key_event(self, code: int) -> None:
        pass

    def foreground_package(self) -> str:
        return self._foreground

    def tap(self, x: int, y: int) -> None:
        self._calls.append(f"tap({x},{y})")


class TestColdStartAllowReboot:
    def test_allow_reboot_default_false(self):
        """allow_reboot defaults to False in signature."""
        import inspect
        sig = inspect.signature(cold_start_app_from_launcher)
        assert "allow_reboot" in sig.parameters
        assert sig.parameters["allow_reboot"].default is False

    def test_cold_start_does_not_reboot_when_disabled(self):
        """
        When allow_reboot=False (default), fall through to False without reboot.
        All three paths fail → return False.
        """
        adb = MockAdb(foreground_pkg="com.android.launcher")

        # monkey fails
        with mock.patch("layernav_android.cold_start._try_monkey", return_value=False), \
             mock.patch("layernav_android.cold_start._try_am_start", return_value=False), \
             mock.patch("layernav_android.cold_start._check_foreground", return_value=False):

            result = cold_start_app_from_launcher(
                adb, "com.tencent.mm",
                app_name="wechat", force_stop_before=False,
                allow_reboot=False,
            )
            assert result is False
            # No reboot command should have been issued
            reboot_cmds = [a for a in adb._run_args if "reboot" in repr(a)]
            assert len(reboot_cmds) == 0

    def test_cold_start_allow_reboot_flag_accepted(self):
        """allow_reboot=True: reboots when all paths fail, then retries monkey.
        Mock reboot sub-steps to avoid real system calls."""
        adb = MockAdb(foreground_pkg="com.android.launcher")

        with mock.patch("layernav_android.cold_start._try_monkey", return_value=False), \
             mock.patch("layernav_android.cold_start._try_am_start", return_value=False), \
             mock.patch("layernav_android.cold_start._try_dock_tap_with_retry", return_value=False), \
             mock.patch("layernav_android.cold_start._check_foreground", return_value=False), \
             mock.patch("layernav_android.cold_start._wait_for_device"), \
             mock.patch("layernav_android.cold_start._wait_for_boot_completed"), \
             mock.patch("layernav_android.cold_start._resolve_screen_size", return_value=(1080, 1920)):

            result = cold_start_app_from_launcher(
                adb, "com.tencent.mm",
                app_name="wechat", force_stop_before=False,
                allow_reboot=True, deadline_s=5.0,
            )
            # Should fall through all paths (including reboot) and return False
            assert result is False
            # Verify reboot was attempted
            reboot_cmds = [a for a in adb._run_args if "reboot" in repr(a)]
            assert len(reboot_cmds) == 1


# ── _wait_for_device / _wait_for_boot_completed tests ──────────────────────────

class TestWaitHelpers:
    def test_wait_for_device_echo_ok(self):
        adb = MockAdb()
        # First call returns "ok"
        with mock.patch.object(adb, "_run", side_effect=["ok"]):
            _wait_for_device(adb, timeout_s=1)
            # Should have succeeded without error

    def test_wait_for_device_timeout(self):
        adb = MockAdb()
        with mock.patch.object(adb, "_run", side_effect=Exception("not ready")):
            _wait_for_device(adb, timeout_s=1)
            # Should not raise; just times out gracefully

    def test_wait_for_boot_completed(self):
        adb = MockAdb()
        with mock.patch.object(adb, "_run", side_effect=["0", "1"]):
            _wait_for_boot_completed(adb, timeout_s=2)
            # Should return after getting "1"


# ── swipe in AdbProtocol ──────────────────────────────────────────────────────

def test_adb_protocol_has_swipe():
    """AdbProtocol declares swipe method."""
    import inspect
    assert hasattr(AdbProtocol, "swipe")
    sig = inspect.signature(AdbProtocol.swipe)
    params = list(sig.parameters)
    assert "x1" in params
    assert "y1" in params
    assert "x2" in params
    assert "y2" in params
    assert "duration_ms" in params
