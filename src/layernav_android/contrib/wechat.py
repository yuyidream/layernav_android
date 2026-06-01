"""WeChat 4-layer model for group screenshot collection.

Requires ``pip install layernav_android[wechat]`` and an existing vision backend
(typically ``collector_phone_android.vision.template_matcher``).

.. code-block:: python

    from layernav_android.contrib.wechat import WeChatGroupLayerModel

    model = WeChatGroupLayerModel()
    model.restore(adb, "L1", scale_w)
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from layernav_android._protocol import AdbProtocol
from layernav_android.base import KEYCODE_BACK, KEYCODE_HOME, BaseLayerModel, LayerDef
from layernav_android.cold_start import cold_start_app_from_launcher

LOG = logging.getLogger("layernav.wechat")

WECHAT_PACKAGE = "com.tencent.mm"


def _decode_png(data: bytes) -> np.ndarray:
    buf = np.frombuffer(data, dtype=np.uint8)
    return __import__("cv2").imdecode(buf, __import__("cv2").IMREAD_COLOR)


def _calc_wechat_session_tab(screen_w: int, screen_h: int, scale_w: float) -> tuple[int, int]:
    tab_x = max(24, min(screen_w - 24, int(round(screen_w * 0.10))))
    tab_y = max(screen_h // 2, screen_h - int(round(56 * max(scale_w, 1e-6))))
    return tab_x, tab_y


class WeChatGroupLayerModel(BaseLayerModel):
    """4-layer model for WeChat group screenshot collection.

    Layer stack::

        L3  微信笔记       detect_wechat_note_header() → score > 0
        L2  群聊天界面      WeChat FG + bottom-4-tab absent + no note-header
        L1  微信主界面      is_wechat_main_conversation_list_chrome()
        L0  手机主屏幕      foreground_package() ≠ com.tencent.mm
    """

    layers = [
        LayerDef("L0", "home", "手机主屏幕", "foreground ≠ com.tencent.mm"),
        LayerDef("L1", "main_list", "微信主会话列表", "is_main_list_chrome()",
                 page_name="chat_list"),
        LayerDef("L2", "chat", "群聊天界面", "WeChat FG + no tabs4 + no notes",
                 page_name="chat_view"),
        LayerDef("L3", "notes", "微信笔记", "detect_note_header()",
                 page_name="note_detail"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._device_id: str = "unknown"

    def init(self, adb: AdbProtocol) -> None:
        self._device_id: str = getattr(adb, "_serial", "unknown")

    def _ensure_vision(self):
        try:
            from collector_phone_android.vision.template_matcher import (
                detect_wechat_main_bottom_tab_bar_four_columns,
                detect_wechat_note_header,
                is_wechat_main_conversation_list_chrome,
            )
            return (
                detect_wechat_note_header,
                is_wechat_main_conversation_list_chrome,
                detect_wechat_main_bottom_tab_bar_four_columns,
            )
        except ImportError:
            raise ImportError(
                "WeChatGroupLayerModel.detect() requires "
                "collector_phone_android.vision.template_matcher. "
                "Install with: pip install collector_phone_android"
            )

    # ── detect ────────────────────────────────────────────────────────────────

    def detect(self, adb: AdbProtocol, scale_w: float) -> str:
        fg = adb.foreground_package()
        if fg != WECHAT_PACKAGE:
            return "L0"
        (
            detect_wechat_note_header,
            is_wechat_main_conversation_list_chrome,
            detect_wechat_main_bottom_tab_bar_four_columns,
        ) = self._ensure_vision()
        png = adb.screencap()
        arr = _decode_png(png)
        if detect_wechat_note_header(arr, scale_w) is not None:
            return "L3"
        if is_wechat_main_conversation_list_chrome(
            arr, scale_w, require_visible_pinned_row=False,
        ):
            return "L1"
        if detect_wechat_main_bottom_tab_bar_four_columns(arr, scale_w):
            return "L1"
        return "L2"

    def detect_from_png(self, png: bytes, scale_w: float, fg: str) -> str:
        """Detect from already-captured PNG (no extra screencap)."""
        if fg != WECHAT_PACKAGE:
            return "L0"
        (
            detect_wechat_note_header,
            is_wechat_main_conversation_list_chrome,
            detect_wechat_main_bottom_tab_bar_four_columns,
        ) = self._ensure_vision()
        arr = _decode_png(png)
        if detect_wechat_note_header(arr, scale_w) is not None:
            return "L3"
        if is_wechat_main_conversation_list_chrome(
            arr, scale_w, require_visible_pinned_row=False,
        ):
            return "L1"
        if detect_wechat_main_bottom_tab_bar_four_columns(arr, scale_w):
            return "L1"
        return "L2"

    # ── Layer handlers ────────────────────────────────────────────────────────

    def _on_L0(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        self._cold_start(adb, "L1", scale_w)
        return "L1"

    def _on_L1(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        if quick:
            row = self._pick_first_unread(adb, scale_w)
        else:
            row = self._scan_and_select(adb, scale_w)
        if row is None:
            return None
        self._tap_row(row, adb)
        return "L2"

    def _on_L2(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        if quick:
            card = self._pick_first_card(adb, scale_w)
        else:
            card = self._scan_and_select_card(adb, scale_w)
        if card is None:
            return None
        adb.tap(card.click_x, card.click_y)
        return "L3"

    def _on_L3(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _tap_row(self, row: Any, adb: AdbProtocol) -> None:
        x1, y1, x2, y2 = row.bbox
        if getattr(row, "unread_dots", None):
            badge = row.unread_dots[0]
            row_h = max(1, y2 - y1)
            badge_cx = badge.x + badge.w // 2
            badge_cy = badge.y + badge.h // 2
            cy = badge_cy + int(row_h * 0.35)
            cy = max(y1 + 10, min(y2 - 10, cy))
            cx = badge_cx
        else:
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
        adb.tap(cx, cy)

    def _pick_first_unread(self, adb: AdbProtocol, scale_w: float) -> Any:
        return None  # TODO: wire real scan from driver

    def _scan_and_select(self, adb: AdbProtocol, scale_w: float) -> Any:
        return None  # TODO: wire real scan — caller holds the logic

    def _pick_first_card(self, adb: AdbProtocol, scale_w: float) -> Any:
        return None

    def _scan_and_select_card(self, adb: AdbProtocol, scale_w: float) -> Any:
        return None

    # ── Cold-start ────────────────────────────────────────────────────────────

    def _cold_start(
        self,
        adb: AdbProtocol,
        target_layer: str,
        scale_w: float,
        deadline_s: float = 20.0,
    ) -> None:
        LOG.info("_cold_start: HOME → WeChat → poll %s", target_layer)

        png = adb.screencap()
        arr = _decode_png(png)
        h, w = arr.shape[:2]

        tab_x, tab_y = _calc_wechat_session_tab(w, h, scale_w)

        adb.key_event(KEYCODE_HOME)
        time.sleep(0.8)

        cold_start_app_from_launcher(
            adb, WECHAT_PACKAGE,
            app_name="wechat", M=4, N=3,
            session_tab_x=tab_x, session_tab_y=tab_y,
            force_stop_before=True,
            deadline_s=deadline_s,
        )

        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            if self.detect(adb, scale_w) == target_layer:
                LOG.info("_cold_start: reached %s", target_layer)
                return
            time.sleep(1.0)
        raise TimeoutError(
            f"cold-start WeChat: did not reach {target_layer} within {deadline_s}s"
        )

    # ── Page recovery ─────────────────────────────────────────────────────────

    _TAB_INDEX_MAP: dict[str, int] = {
        # WeChat bottom 4-tab bar (0=微信, 1=通讯录, 2=发现, 3=我)
        "chat_list": 0,
        "contacts":   1,
        "discover":   2,
        "profile":    3,
    }

    def _recover_to_page(
        self, layer: str, page_name: str, adb: AdbProtocol, scale_w: float,
    ) -> bool:
        """Navigate to a specific sub-page within *layer* after recovery.

        For L1 (WeChat main tabs):
            - ``chat_list`` → tap WeChat tab (0)
            - ``contacts``  → tap Contacts tab (1)
            - ``discover``  → tap Discover tab (2)
            - ``profile``   → tap Me tab (3)

        Other layers / unknown pages fall back to default
        :meth:`BaseLayerModel._recover_to_page`.
        """
        if layer != "L1":
            return super()._recover_to_page(layer, page_name, adb, scale_w)
        if page_name not in self._TAB_INDEX_MAP:
            return super()._recover_to_page(layer, page_name, adb, scale_w)

        png = adb.screencap()
        arr = _decode_png(png)
        h, w = arr.shape[:2]
        sw = max(scale_w, 1e-6)

        # Calculate tab centers — 4 evenly-spaced bottom tabs
        tab_y = max(h // 2, h - int(round(56 * sw)))
        tab_width = w // 4
        tab_idx = self._TAB_INDEX_MAP[page_name]
        tab_x = tab_width // 2 + tab_idx * tab_width

        LOG.info("_recover_to_page: L1 → %s tap (%d, %d)", page_name, tab_x, tab_y)
        adb.tap(tab_x, tab_y)
        time.sleep(0.5)
        return self.detect_detail(adb, scale_w).page_name == page_name
