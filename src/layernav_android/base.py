"""Multi-layer Android task-stack navigation framework.

Framework—Task contract:
    Task subclass overrides:
        - ``layers`` — list of :class:`LayerDef`
        - ``detect`` — screenshot-based layer detection
        - ``_on_Lx`` — per-layer handler (business logic + tap)

Framework provides:
    Atomic:   ``detect``  ``detect_layer``  ``back_one``  ``home_one``  ``back_recover``
             ``poll_until_target_layer``  (adaptive-poll after caller tap)
    Tap:      ``_do_tap`` — 层间点击，子类覆盖加入防检测策略
             ``_tap_to_layer`` — tap + poll 闭环，点击后轮询直到到达目标层
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from layernav_android._protocol import AdbProtocol
from layernav_android.logging import get_logger

logger = get_logger(__name__)

KEYCODE_BACK = 4
KEYCODE_HOME = 3

# Adaptive polling intervals (0.3s → 0.6s → … → 2.0s)
_ENTER_NEXT_POLL_INITIAL = 0.3
_ENTER_NEXT_POLL_STEP   = 0.3
_ENTER_NEXT_POLL_MAX    = 2.0

_HOME_SETTLE_S = 0.8


def home_one(adb: AdbProtocol) -> None:
    """Standalone: press HOME key and wait for launcher to settle.

    A thin wrapper around ``adb.key_event(KEYCODE_HOME)`` + 0.8s settle.
    Intended for cold-start preambles where no layer model instance exists
    (e.g. task scheduler's ``start_app``).  For in‑navigation HOME use
    :meth:`BaseLayerModel.home_one` which additionally detects and notifies
    layer transitions.
    """
    adb.key_event(KEYCODE_HOME)
    time.sleep(_HOME_SETTLE_S)


# ── Layer definition ──────────────────────────────────────────────────────────


@dataclass
class LayerDef:
    """Description of one layer in the Android task stack."""

    key: str
    """Layer key: ``"L0"`` | ``"L1"`` | ``"L2"`` | ``"L3"``."""

    name: str
    """Machine-readable name."""

    label_cn: str
    """Human-readable Chinese label."""

    detection: str
    """How this layer is detected (human-readable)."""

    page_name: str = ""
    """Optional custom page name. Callers can set this per-layer to
    distinguish sub-states within a layer (e.g. main_list vs recent_page for L1).
    Default empty string means no sub-page distinction."""

    detection_extra: str = ""
    """Optional detail about detection (human-readable, complementary to
    *detection*). Callers can append custom context strings."""


# ── Observer / Listener (inspired by python-statemachine's Listener pattern) ───


class LayerListener(Protocol):
    """Observer interface for layer model lifecycle events.

    All methods are optional — implement only what you need.
    Inspired by `python-statemachine
    <https://github.com/fgmacedo/python-statemachine>`_'s Listener pattern.
    """

    def on_transition(
        self, from_layer: str, to_layer: str, method: str,
    ) -> None:
        """Called after an atomic layer transition completes.

        *method* is one of ``"back_one"`` or ``"home_one"``.
        """
        ...

    def on_timeout(
        self, from_layer: str, target_layer: str, elapsed_s: float,
    ) -> None:
        """Called when a layer transition polling times out."""
        ...

    def on_recovery(self, target_layer: str, ok: bool) -> None:
        """Called after :meth:`BaseLayerModel.back_recover` completes.

        *ok* indicates whether the recovery succeeded.
        """
        ...


# ── Layer detection result ──────────────────────────────────────────────────────


@dataclass
class DetectResult:
    """Result of :meth:`BaseLayerModel.detect_detail`.

    Combines layer key with optional page_name from :class:`LayerDef`.
    """

    layer_key: str
    """Layer key: ``"L0"`` | ``"L1"`` | ``"L2"`` | ``"L3"``."""

    page_name: str = ""
    """Custom page name from :attr:`LayerDef.page_name`, or ``""``."""


# ── Abstract base ─────────────────────────────────────────────────────────────


class BaseLayerModel:
    """Abstract Android task-stack layer model.

    Subclass contract:
        - Override :attr:`layers`.
        - Override :meth:`detect`.
        - Override :meth:`detect_layer`.
        - Override ``_on_L0`` / ``_on_L1`` / ``_on_L2`` / ``_on_L3``.
        - Optionally override :meth:`_cold_start`.
    """

    layers: list[LayerDef] = []
    _ON_METHODS: tuple[str, ...] = ("_on_L0", "_on_L1", "_on_L2", "_on_L3")

    def __init__(self) -> None:
        self._listeners: list[LayerListener] = []
        self._idle_watch: Any | None = None

    def add_listener(self, listener: LayerListener) -> None:
        """Register a :class:`LayerListener` to observe lifecycle events."""
        self._listeners.append(listener)

    def bind_idle_watch(self, watch: Any | None) -> None:
        """Inject an idle watchdog instance (duck-typed: ``touch()`` / ``is_violated()``).

        When bound, :meth:`back_recover` will:
        - call ``touch()`` on entry to reset the idle baseline
        - check ``is_violated()`` at each fast-forward iteration

        Pass ``None`` to unbind.  Default is ``None`` (no idle protection).
        """
        self._idle_watch = watch

    def _notify_transition(
        self, from_layer: str, to_layer: str, method: str,
    ) -> None:
        for lst in self._listeners:
            lst.on_transition(from_layer, to_layer, method)

    def _notify_timeout(
        self, from_layer: str, target_layer: str, elapsed_s: float,
    ) -> None:
        for lst in self._listeners:
            lst.on_timeout(from_layer, target_layer, elapsed_s)

    def _notify_recovery(self, target_layer: str, ok: bool) -> None:
        for lst in self._listeners:
            lst.on_recovery(target_layer, ok)

    # ── Overridable tap ─────────────────────────────────────────────────────

    def _do_tap(
        self, adb: AdbProtocol,
        click_x: int, click_y: int,
        jitter_x: int = 0, jitter_y: int = 0,
    ) -> None:
        """层间点击。默认 ``adb.tap``，子类覆盖加入防检测策略。

        *jitter_x* / *jitter_y* 由调用方按场景传入（如 L1→L2 宽抖动 20px），
        子类内部策略自由替换（如 mumdad ``click_xonly``）。
        """
        adb.tap(click_x, click_y)

    def _tap_to_layer(
        self, adb: AdbProtocol, scale_w: float,
        click_x: int, click_y: int, target_layer: str,
        jitter_x: int = 0, jitter_y: int = 0,
        max_attempts: int = 3,
        max_wait_s: float = 8.0,
    ) -> bool:
        """Tap + poll 闭环：点击后轮询直到到达目标层。

        Uses :meth:`_do_tap`（子类可覆盖防检测）和
        :meth:`poll_until_target_layer` 验证到达。失败时最多重试
        ``max_attempts`` 次，重试间隔 1s。
        """
        for attempt in range(max_attempts):
            self._do_tap(adb, click_x, click_y, jitter_x=jitter_x, jitter_y=jitter_y)
            if self.poll_until_target_layer(adb, target_layer, scale_w, max_wait_s=max_wait_s):
                return True
            if attempt < max_attempts - 1:
                time.sleep(1.0)
        return False

    # ── Subclass overrides ────────────────────────────────────────────────────

    def detect(self, adb: AdbProtocol, scale_w: float) -> str | None:
        """Return current layer key.  Task MUST override.

        Returns ``None`` when the layer cannot be determined — callers
        should treat this as "unknown position" and initiate recovery.
        """
        raise NotImplementedError("subclass must override detect()")

    def detect_layer(self, adb: AdbProtocol, scale_w: float, layer: str) -> bool:
        """Check if the current screen matches *layer* (target-aware detection).

        Unlike :meth:`detect` ("where am I"), this answers "have I reached
        *layer*?" — used by navigation methods to verify destination arrival.
        Subclass MUST override.
        """
        raise NotImplementedError("subclass must override detect_layer()")

    def detect_detail(self, adb: AdbProtocol, scale_w: float) -> DetectResult:
        """Return current layer key + custom page_name.

        Default implementation calls :meth:`detect` and looks up
        :attr:`LayerDef.page_name` from :attr:`layers`.  Subclasses
        may override to set page_name dynamically.
        """
        layer_key = self.detect(adb, scale_w)
        page_name = ""
        for ld in self.layers:
            if ld.key == layer_key:
                page_name = ld.page_name
                break
        return DetectResult(layer_key=layer_key, page_name=page_name)

    def _recover_to_page(
        self,
        layer: str,
        page_name: str,
        adb: AdbProtocol,
        scale_w: float,
    ) -> bool:
        """Navigate to a specific sub-page within *layer* after recovery.

        Called by :meth:`back_recover` after reaching the correct layer.  Override
        to handle sub-page navigation (e.g. switching tabs within L1).

        Default: verify current page via :meth:`detect_detail` — returns
        ``True`` if ``detect_detail().page_name == page_name``.
        """
        result = self.detect_detail(adb, scale_w)
        return result.page_name == page_name

    def _on_L0(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        """L0 handler: home screen → cold-start App."""
        raise NotImplementedError("subclass must override _on_L0")

    def _on_L1(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        """L1 handler: App main screen → pick content, tap."""
        raise NotImplementedError("subclass must override _on_L1")

    def _on_L2(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        """L2 handler: content page → pick sub-content, tap."""
        raise NotImplementedError("subclass must override _on_L2")

    def _on_L3(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        """L3 handler: deepest layer — typically no further advance."""
        raise NotImplementedError("subclass must override _on_L3")

    def _call_on_layer(
        self, layer_key: str, adb: AdbProtocol, scale_w: float, *, quick: bool
    ) -> str | None:
        i = self._layer_index(layer_key)
        if i < 0:
            logger.error("_call_on_layer: unknown layer %s", layer_key)
            return None
        method = getattr(self, self._ON_METHODS[i])
        return method(adb, scale_w, quick=quick)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _layer_index(self, layer_key: str) -> int:
        for i, ld in enumerate(self.layers):
            if ld.key == layer_key:
                return i
        return -1

    def init(self, adb: AdbProtocol) -> None:
        """One-time initialisation (optional)."""
        pass

    def _cold_start(
        self, adb: AdbProtocol, target_layer: str, scale_w: float,
        *, allow_reboot: bool = False,
    ) -> None:
        """Cold-start the target app (override in subclass)."""
        pass

    # ── Atomic API ────────────────────────────────────────────────────────────

    def poll_until_target_layer(
        self,
        adb: AdbProtocol,
        target_layer: str,
        scale_w: float,
        *,
        max_wait_s: float = 8.0,
    ) -> bool:
        """Adaptive poll: detect → sleep → detect until *target_layer* is reached.

        Caller performs a tap (or any navigation action) **before** calling
        this method, then polls here for the target layer transition.

        Adaptive intervals: 0.3s → 0.6s → … → 2.0s, capped at *max_wait_s*.
        Does **not** fire listener notifications.
        """
        cur = self.detect(adb, scale_w)
        if cur is not None and self.detect_layer(adb, scale_w, target_layer):
            return True
        poll_start = time.monotonic()
        deadline = poll_start + max_wait_s
        interval = _ENTER_NEXT_POLL_INITIAL
        while time.monotonic() < deadline:
            time.sleep(interval)
            if self.detect_layer(adb, scale_w, target_layer):
                return True
            interval = min(interval + _ENTER_NEXT_POLL_STEP, _ENTER_NEXT_POLL_MAX)

        elapsed = time.monotonic() - poll_start
        current = self.detect(adb, scale_w)
        logger.warning(
            "poll_until_target_layer: %s→%s timeout after %.1fs (still on %s)",
            cur, target_layer, elapsed, current,
        )
        return False

    def back_one(self, adb: AdbProtocol, scale_w: float, *, max_retries: int = 3) -> str:
        """退回到上一层（KEYCODE_BACK，poll_until_target_layer + 冷启动兜底）。

        1. detect() → 计算上一层 key
        2. KEYCODE_BACK → poll_until_target_layer(上一层)
        3. 失败重试，最多 *max_retries* 次
        4. 全部失败 → :meth:`back_recover` 冷启动兜底
        """
        cur = self.detect(adb, scale_w)
        logger.debug("back_one: from %s", cur)

        ci = self._layer_index(cur) if cur is not None else -1
        if ci > 0:
            target = self.layers[ci - 1].key
        else:
            target = "L1"

        for attempt in range(max_retries):
            adb.key_event(KEYCODE_BACK)
            if self.poll_until_target_layer(adb, target, scale_w):
                if cur is not None and target is not None:
                    self._notify_transition(cur, target, "back_one")
                return target
            logger.debug(
                "back_one: attempt %d/%d poll %s timeout",
                attempt + 1, max_retries, target,
            )

        logger.warning(
            "back_one: %d attempts failed (stuck at %s) — falling back to back_recover(%s)",
            max_retries, cur, target,
        )
        self.back_recover(adb, target, scale_w)
        return target

    def home_one(
        self, adb: AdbProtocol, scale_w: float,
    ) -> str | None:
        """Press HOME key once, detect layer transition.

        Returns the *new* layer after returning to launcher home screen.
        Intended for cold-start preambles and navigation-stack reset.
        """
        cur = self.detect(adb, scale_w)
        logger.debug("home_one: from %s", cur)
        adb.key_event(KEYCODE_HOME)
        time.sleep(0.8)
        next_cur = self.detect(adb, scale_w)
        logger.debug("home_one: %s → %s", cur, next_cur)
        if cur != next_cur:
            self._notify_transition(cur, next_cur, "home_one")
        return next_cur

    def back_recover(
        self,
        adb: AdbProtocol,
        target_layer: str,
        scale_w: float,
        *,
        target_page: str | None = None,
        max_nav_steps: int = 3,
    ) -> bool:
        """Recover after BACK exhaustion: cold-start → fast-forward → page.

        Cold-start is retried up to 3 times; if all fail, one final
        attempt is made with ``allow_reboot=True`` (``adb reboot`` +
        wait‑for‑boot + cold‑start).  If *target_page* is given, calls
        :meth:`_recover_to_page` after reaching *target_layer*.

        *max_nav_steps* bounds the fast-forward loop (layer-navigation
        steps).  Default 3 is sufficient for the 4-layer WeChat model
        (L0→L1→L2→L3→L0).  Consumers with more layers should pass a
        higher value explicitly.
        """
        logger.warning("back_recover: cold-start → fast-forward → %s (page=%s)",
                     target_layer, target_page)

        # Touch idle watchdog on entry (covers cold-start + reboot wait)
        if self._idle_watch is not None:
            self._idle_watch.touch()

        self.home_one(adb, scale_w)

        # ── cold-start with retries (3× normal, 1× reboot) ──
        for attempt in range(1, 5):  # 1,2,3 normal; 4 = reboot
            try:
                if attempt < 4:
                    self._cold_start(adb, "L1", scale_w)
                else:
                    logger.error("back_recover: 3 attempts failed — rebooting device")
                    self.home_one(adb, scale_w)
                    self._cold_start(adb, "L1", scale_w, allow_reboot=True)
                break  # success — exit retry loop
            except TimeoutError:
                if attempt < 3:
                    logger.warning(
                        "back_recover: cold-start attempt %d/3 timed out "
                        "— retrying", attempt,
                    )
                    self.home_one(adb, scale_w)
                elif attempt == 3:
                    continue  # → reboot attempt
                else:
                    logger.error("back_recover: cold-start timed out after reboot")
                    self._notify_recovery(target_layer, False)
                    return False
            except Exception:
                if attempt < 3:
                    logger.warning(
                        "back_recover: cold-start attempt %d/3 exception "
                        "— retrying", attempt, exc_info=True,
                    )
                    self.home_one(adb, scale_w)
                elif attempt == 3:
                    continue  # → reboot attempt
                else:
                    logger.error(
                        "back_recover: cold-start exception after reboot",
                        exc_info=True,
                    )
                    self._notify_recovery(target_layer, False)
                    return False

        # ── fast-forward to target_layer (PRD §4.7 step 2) ──
        nav_steps = 0
        while not self.detect_layer(adb, scale_w, target_layer):
            # Idle watchdog: abort if violated (e.g. device stuck)
            if self._idle_watch is not None and self._idle_watch.is_violated():
                logger.error(
                    "back_recover: idle watchdog violated at nav_step=%d",
                    nav_steps,
                )
                self._notify_recovery(target_layer, False)
                return False

            nav_steps += 1
            if nav_steps > max_nav_steps:
                logger.error(
                    "back_recover: exceeded max_nav_steps=%d "
                    "(fast-forward bounded abort)", max_nav_steps,
                )
                self._notify_recovery(target_layer, False)
                return False

            cur = self.detect(adb, scale_w)
            if cur is None or self._layer_index(cur) < 0:
                # 冷启动后可能截到短暂动画/加载帧（detect() 返回 None）。
                # 重试 2 次（每 0.5s），过渡帧通常在 1s 内稳定。
                for _i in range(2):
                    time.sleep(0.5)
                    cur = self.detect(adb, scale_w)
                    if cur is not None and self._layer_index(cur) >= 0:
                        break
                else:
                    logger.error(
                        "back_recover: lost after cold-start (cur=%s)", cur,
                    )
                    self._notify_recovery(target_layer, False)
                    return False

            try:
                self._call_on_layer(cur, adb, scale_w, quick=True)
            except Exception:
                logger.error(
                    "back_recover: _call_on_layer(%s) raised exception",
                    cur, exc_info=True,
                )
                self._notify_recovery(target_layer, False)
                return False

        if target_page is not None:
            page_ok = self._recover_to_page(
                target_layer, target_page, adb, scale_w,
            )
            if not page_ok:
                logger.error(
                    "back_recover: reached L%s but page=%s recovery failed",
                    target_layer, target_page,
                )
                self._notify_recovery(target_layer, False)
                return False

        result = self.detect_layer(adb, scale_w, target_layer)
        self._notify_recovery(target_layer, result)
        return result

    # ── internal helpers ────────────────────────────────────────────────────────
