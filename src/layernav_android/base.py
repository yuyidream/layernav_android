"""Multi-layer Android task-stack navigation framework.

Framework—Task contract:
    Task subclass overrides:
        - ``layers`` — list of :class:`LayerDef`
        - ``detect`` — screenshot-based layer detection
        - ``_on_Lx`` — per-layer handler (business logic + tap)

Framework provides:
    Atomic:   ``detect``  ``enter_next``  ``back_one``  ``back_recover``
    Combined: ``back``    ``advance``     ``restore``
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from layernav_android._protocol import AdbProtocol

LOG = logging.getLogger("layernav")

POST_TRANSITION_SLEEP = 1.5

KEYCODE_BACK = 4
KEYCODE_HOME = 3

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
        self, adb: AdbProtocol, target_layer: str, scale_w: float
    ) -> None:
        """Cold-start the target app (override in subclass)."""
        pass

    # ── Atomic API ────────────────────────────────────────────────────────────

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
        4. wait POST_TRANSITION_SLEEP, then detect  ← **validator** (post-check)
        5. if not yet on target, poll with increasing intervals up to max_wait_s

        This handles variable transition times (network loading, animations).
        """
        cur = self.detect(adb, scale_w)
        target = self._call_on_layer(cur, adb, scale_w, quick=quick)
        if target is None or target == cur:
            return True

        time.sleep(POST_TRANSITION_SLEEP)
        next_cur = self.detect(adb, scale_w)
        if next_cur == target:
            self._notify_transition(cur, next_cur, "enter_next")
            return True

        poll_start = time.monotonic()
        deadline = poll_start + max_wait_s
        interval = 0.5
        while time.monotonic() < deadline:
            time.sleep(interval)
            next_cur = self.detect(adb, scale_w)
            if next_cur == target:
                self._notify_transition(cur, next_cur, "enter_next")
                return True
            interval = min(interval + 0.5, 2.0)

        elapsed = time.monotonic() - poll_start
        LOG.warning(
            "enter_next: %s→%s timeout after %.1fs — still on %s",
            cur, target, max_wait_s, next_cur,
        )
        self._notify_timeout(cur, target, elapsed)
        return False

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

    def back_recover(
        self, adb: AdbProtocol, target_layer: str, scale_w: float
    ) -> bool:
        """Recover after BACK exhaustion: cold-start → fast-forward → normal
        resume."""
        LOG.warning("back_recover: cold-start → fast-forward → %s", target_layer)
        adb.key_event(KEYCODE_HOME)
        time.sleep(0.8)
        self._cold_start(adb, "L1", scale_w)

        ok = self.advance(adb, target_layer, scale_w, quick=True)
        if not ok:
            self._notify_recovery(target_layer, False)
            return False

        result = self.detect(adb, scale_w) == target_layer
        self._notify_recovery(target_layer, result)
        return result

    # ── Combined API ──────────────────────────────────────────────────────────

    def back(self, adb: AdbProtocol, to_layer: str, scale_w: float) -> bool:
        """Retreat to *to_layer* via repeated BACK."""
        for _ in range(3):
            cur = self.detect(adb, scale_w)
            if cur == to_layer:
                self._call_on_layer(to_layer, adb, scale_w, quick=False)
                return True
            if cur == "L0":
                break
            self.back_one(adb, scale_w)
        return self.back_recover(adb, to_layer, scale_w)

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
            ok = self.enter_next(adb, scale_w, quick=quick, max_wait_s=max_wait_s)
            if not ok:
                return False

    def restore(
        self, adb: AdbProtocol, target_layer: str, scale_w: float
    ) -> bool:
        """Restore to *target_layer* from any position."""
        cur = self.detect(adb, scale_w)
        if cur == target_layer:
            return True
        ci = self._layer_index(cur)
        ti = self._layer_index(target_layer)
        if ci > ti:
            return self.back(adb, target_layer, scale_w)
        else:
            return self.advance(adb, target_layer, scale_w, quick=True)
