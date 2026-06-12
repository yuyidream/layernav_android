"""Multi-layer Android task-stack navigation framework.

Framework—Task contract:
    Task subclass overrides:
        - ``layers`` — list of :class:`LayerDef`
        - ``detect`` — screenshot-based layer detection
        - ``_on_Lx`` — per-layer handler (business logic + tap)

Framework provides:
    Atomic:   ``detect``  ``enter_next``  ``back_one``  ``home_one``  ``back_recover``
             ``poll_until_target_layer``  (adaptive-poll after caller tap)
    Combined: ``back``    ``advance``     ``restore``
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from layernav_android._protocol import AdbProtocol

LOG = logging.getLogger("layernav")

KEYCODE_BACK = 4
KEYCODE_HOME = 3

# enter_next polling intervals (no fixed pre-wait — pure adaptive poll)
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

        *method* is one of ``"enter_next"`` or ``"back_one"``.
        """
        ...

    def on_timeout(
        self, from_layer: str, target_layer: str, elapsed_s: float,
    ) -> None:
        """Called when :meth:`BaseLayerModel.enter_next` polling times out."""
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
        - Override ``_on_L0`` / ``_on_L1`` / ``_on_L2`` / ``_on_L3``.
        - Optionally override :meth:`_cold_start`.
    """

    layers: list[LayerDef] = []
    _ON_METHODS: tuple[str, ...] = ("_on_L0", "_on_L1", "_on_L2", "_on_L3")

    def __init__(self) -> None:
        self._listeners: list[LayerListener] = []

    def add_listener(self, listener: LayerListener) -> None:
        """Register a :class:`LayerListener` to observe lifecycle events."""
        self._listeners.append(listener)

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

    # ── Subclass overrides ────────────────────────────────────────────────────

    def detect(self, adb: AdbProtocol, scale_w: float) -> str:
        """Return current layer key.  Task MUST override."""
        raise NotImplementedError("subclass must override detect()")

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

        Called by :meth:`back_recover` (and :meth:`restore` when already
        on the target layer) after reaching the correct layer.  Override
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
            LOG.error("_call_on_layer: unknown layer %s", layer_key)
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

        Same 0.3s→2.0s adaptive engine as :meth:`enter_next`.
        Does **not** fire listener notifications — callers that need them
        (e.g. :meth:`enter_next`) handle that separately.
        """
        cur = self.detect(adb, scale_w)
        if cur == target_layer:
            return True
        poll_start = time.monotonic()
        deadline = poll_start + max_wait_s
        interval = _ENTER_NEXT_POLL_INITIAL
        while time.monotonic() < deadline:
            time.sleep(interval)
            next_cur = self.detect(adb, scale_w)
            if next_cur == target_layer:
                return True
            interval = min(interval + _ENTER_NEXT_POLL_STEP, _ENTER_NEXT_POLL_MAX)

        elapsed = time.monotonic() - poll_start
        current = self.detect(adb, scale_w)
        LOG.warning(
            "poll_until_target_layer: %s→%s timeout after %.1fs (still on %s)",
            cur, target_layer, elapsed, current,
        )
        return False

    def enter_next(
        self,
        adb: AdbProtocol,
        scale_w: float,
        *,
        quick: bool = False,
        max_wait_s: float = 8.0,
    ) -> bool:
        """Advance ONE layer from current position.

        1. detect current layer  ← **guard** (pre-check)
        2. call _on_L[cur](quick) — handler does business + tap
        3. if handler returns None or same layer → stop (success)
        4. poll detect() at adaptive intervals (0.3s→0.6s→…→2.0s)
           until target layer reached or *max_wait_s* elapsed
           ← **validator** (post-check, no fixed pre-wait)

        This handles variable transition times (network loading, animations)
        without a single fixed wait — fast transitions hit on the first or
        second poll, slow ones are covered by the growing interval up to
        *max_wait_s*.
        """
        cur = self.detect(adb, scale_w)
        if self._layer_index(cur) < 0:
            LOG.error("enter_next: unknown layer %s, cannot advance", cur)
            return False
        target = self._call_on_layer(cur, adb, scale_w, quick=quick)
        if target is None or target == cur:
            return True

        ok = self.poll_until_target_layer(
            adb, target, scale_w, max_wait_s=max_wait_s,
        )
        if ok:
            self._notify_transition(cur, target, "enter_next")
        else:
            self._notify_timeout(cur, target, max_wait_s)
        return ok

    def back_one(self, adb: AdbProtocol, scale_w: float) -> str:
        """Send KEYCODE_BACK once, return new layer.

        1. detect current layer  ← **guard** (pre-check)
        2. KEYCODE_BACK
        3. sleep, detect new layer  ← **validator** (post-check)
        """
        cur = self.detect(adb, scale_w)
        LOG.debug("back_one: from %s", cur)
        adb.key_event(KEYCODE_BACK)
        time.sleep(1.0)
        next_cur = self.detect(adb, scale_w)
        LOG.debug("back_one: %s → %s", cur, next_cur)
        self._notify_transition(cur, next_cur, "back_one")
        return next_cur

    def home_one(
        self, adb: AdbProtocol, scale_w: float,
    ) -> str | None:
        """Press HOME key once, detect layer transition.

        Returns the *new* layer after returning to launcher home screen.
        Intended for cold-start preambles and navigation-stack reset.
        """
        cur = self.detect(adb, scale_w)
        LOG.debug("home_one: from %s", cur)
        adb.key_event(KEYCODE_HOME)
        time.sleep(0.8)
        next_cur = self.detect(adb, scale_w)
        LOG.debug("home_one: %s → %s", cur, next_cur)
        self._notify_transition(cur, next_cur, "home_one")
        return next_cur

    def back_recover(
        self,
        adb: AdbProtocol,
        target_layer: str,
        scale_w: float,
        *,
        target_page: str | None = None,
    ) -> bool:
        """Recover after BACK exhaustion: cold-start → fast-forward → page.

        Cold-start is retried up to 3 times; if all fail, one final
        attempt is made with ``allow_reboot=True`` (``adb reboot`` +
        wait‑for‑boot + cold‑start).  If *target_page* is given, calls
        :meth:`_recover_to_page` after reaching *target_layer*.
        """
        LOG.warning("back_recover: cold-start → fast-forward → %s (page=%s)",
                     target_layer, target_page)
        self.home_one(adb, scale_w)

        # ── cold-start with retries (3× normal, 1× reboot) ──
        for attempt in range(1, 5):  # 1,2,3 normal; 4 = reboot
            try:
                if attempt < 4:
                    self._cold_start(adb, "L1", scale_w)
                else:
                    LOG.error("back_recover: 3 attempts failed — rebooting device")
                    self.home_one(adb, scale_w)
                    self._cold_start(adb, "L1", scale_w, allow_reboot=True)
                break  # success — exit retry loop
            except TimeoutError:
                if attempt < 3:
                    LOG.warning(
                        "back_recover: cold-start attempt %d/3 timed out "
                        "— retrying", attempt,
                    )
                    self.home_one(adb, scale_w)
                elif attempt == 3:
                    continue  # → reboot attempt
                else:
                    LOG.error("back_recover: cold-start timed out after reboot")
                    self._notify_recovery(target_layer, False)
                    return False
            except Exception:
                if attempt < 3:
                    LOG.warning(
                        "back_recover: cold-start attempt %d/3 exception "
                        "— retrying", attempt, exc_info=True,
                    )
                    self.home_one(adb, scale_w)
                elif attempt == 3:
                    continue  # → reboot attempt
                else:
                    LOG.error(
                        "back_recover: cold-start exception after reboot",
                        exc_info=True,
                    )
                    self._notify_recovery(target_layer, False)
                    return False

        ok = self.advance(adb, target_layer, scale_w, quick=True)
        if not ok:
            self._notify_recovery(target_layer, False)
            return False

        if target_page is not None:
            page_ok = self._recover_to_page(
                target_layer, target_page, adb, scale_w,
            )
            if not page_ok:
                LOG.error(
                    "back_recover: reached L%s but page=%s recovery failed",
                    target_layer, target_page,
                )
                self._notify_recovery(target_layer, False)
                return False

        result = self.detect(adb, scale_w) == target_layer
        self._notify_recovery(target_layer, result)
        return result

    # ── Combined API ──────────────────────────────────────────────────────────

    def back(
        self, adb: AdbProtocol, to_layer: str, scale_w: float, *,
        target_page: str | None = None,
    ) -> bool:
        """Retreat to *to_layer* via repeated BACK."""
        for _ in range(3):
            cur = self.detect(adb, scale_w)
            if cur == to_layer:
                self._call_on_layer(to_layer, adb, scale_w, quick=False)
                if target_page is not None:
                    return self._recover_to_page(to_layer, target_page, adb, scale_w)
                return True
            if cur == "L0":
                break
            self.back_one(adb, scale_w)
        return self.back_recover(adb, to_layer, scale_w, target_page=target_page)

    def advance(
        self, adb: AdbProtocol, target_layer: str, scale_w: float, *,
        quick: bool = False,
        max_wait_s: float = 8.0,
    ) -> bool:
        """Advance layer-by-layer to *target_layer*.

        Uses :meth:`enter_next` for each step.  *quick* is forwarded to
        intermediate layers' handlers.  At the target layer, handler is
        always called with ``quick=False``.
        """
        while True:
            cur = self.detect(adb, scale_w)
            if cur == target_layer:
                self._call_on_layer(target_layer, adb, scale_w, quick=False)
                return True
            if self._layer_index(cur) < 0:
                LOG.warning(
                    "advance: unknown layer %s, cold-starting → %s",
                    cur, target_layer,
                )
                self._cold_start(adb, target_layer, scale_w)
                continue
            ok = self.enter_next(adb, scale_w, quick=quick, max_wait_s=max_wait_s)
            if not ok:
                return False

    def restore(
        self, adb: AdbProtocol, target_layer: str, scale_w: float, *,
        target_page: str | None = None,
    ) -> bool:
        """Restore to *target_layer* (and optionally *target_page*) from any position.

        If already on *target_layer* but page mismatch, calls
        :meth:`_recover_to_page` without cold-start.
        """
        cur = self.detect(adb, scale_w)
        if cur == target_layer:
            if target_page is not None:
                return self._recover_to_page(target_layer, target_page, adb, scale_w)
            return True
        ci = self._layer_index(cur)
        ti = self._layer_index(target_layer)
        if ci > ti:
            return self.back(adb, target_layer, scale_w, target_page=target_page)
        else:
            ok = self.advance(adb, target_layer, scale_w, quick=True)
            if not ok:
                return False
            if target_page is not None:
                return self._recover_to_page(target_layer, target_page, adb, scale_w)
            return True
