"""WeChat 4-layer model — skeleton with cold-start support.

``detect()`` is currently a stub; wire your own vision backend to implement
actual screenshot-based layer classification.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from layernav_android._protocol import AdbProtocol
from layernav_android.base import KEYCODE_BACK, BaseLayerModel, LayerDef
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
        L2  聊天界面        WeChat FG + bottom-4-tab absent + no note-header
        L1  微信主界面      is_wechat_main_conversation_list_chrome()
        L0  手机主屏幕      foreground_package() ≠ com.tencent.mm

    Sub-page definitions (per-layer):

        L1 → chat_list / contacts / discover / profile  (4-tab switch via tap)
        L2 → group_chat / personal_chat                 (verify via detect_detail)
        L3 → wechat_note                                (verify via detect_detail)

    .. note::

        ``detect()`` is a stub; subclass and override with your own
        screenshot-based vision backend.
    """

    layers = [
        LayerDef("L0", "home", "手机主屏幕", "foreground ≠ com.tencent.mm"),
        LayerDef("L1", "main_list", "微信主会话列表", "is_main_list_chrome()",
                 page_name="chat_list",
                 detection_extra="子页面: chat_list(会话列表)/contacts(通讯录)/discover(发现)/profile(我)"),
        LayerDef("L2", "chat", "聊天界面", "WeChat FG + no tabs4 + no notes",
                 page_name="group_chat",
                 detection_extra="子页面: group_chat(群聊天)/personal_chat(个人聊天)"),
        LayerDef("L3", "notes", "微信笔记", "detect_note_header()",
                 page_name="wechat_note",
                 detection_extra="子页面: wechat_note(群里发的笔记)"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._device_id: str = "unknown"

    def init(self, adb: AdbProtocol) -> None:
        self._device_id: str = getattr(adb, "_serial", "unknown")

    # ── detect ────────────────────────────────────────────────────────────────

    def detect(self, adb: AdbProtocol, scale_w: float) -> str:
        """Stub — override with your own screenshot-based layer classifier."""
        fg = adb.foreground_package()
        if fg != WECHAT_PACKAGE:
            return "L0"
        raise NotImplementedError(
            "WeChatGroupLayerModel.detect() is a stub. "
            "Override with your own vision backend."
        )

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
        *,
        allow_reboot: bool = False,
    ) -> None:
        LOG.info("_cold_start: HOME → WeChat → poll %s (allow_reboot=%s)",
                 target_layer, allow_reboot)

        png = adb.screencap()
        arr = _decode_png(png)
        h, w = arr.shape[:2]

        tab_x, tab_y = _calc_wechat_session_tab(w, h, scale_w)

        self.home_one(adb, scale_w)

        cold_start_app_from_launcher(
            adb, WECHAT_PACKAGE,
            app_name="wechat", M=4, N=3,
            session_tab_x=tab_x, session_tab_y=tab_y,
            force_stop_before=True,
            deadline_s=deadline_s,
            allow_reboot=allow_reboot,
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

    _NON_TAB_PAGES: dict[str, list[str]] = {
        # Pages that can only be verified via detect_detail (no tab tap)
        "L2": ["group_chat", "personal_chat"],
        "L3": ["wechat_note"],
    }

    def _recover_to_page(
        self, layer: str, page_name: str, adb: AdbProtocol, scale_w: float,
    ) -> bool:
        """Navigate to a specific sub-page within *layer* after recovery.

        L1 (4-tab switch via tap):
            - ``chat_list`` → tap WeChat tab (0)
            - ``contacts``  → tap Contacts tab (1)
            - ``discover``  → tap Discover tab (2)
            - ``profile``   → tap Me tab (3)

        L2 / L3 (no direct navigation — verify-only):
            - ``group_chat`` / ``personal_chat`` → verify via detect_detail
            - ``wechat_note`` → verify via detect_detail

        Unknown layers / pages fall back to default
        :meth:`BaseLayerModel._recover_to_page`.
        """
        non_tab = self._NON_TAB_PAGES.get(layer, [])
        if page_name in non_tab:
            LOG.info("_recover_to_page: %s → %s (verify-only)", layer, page_name)
            result = self.detect_detail(adb, scale_w)
            return result.layer_key == layer and result.page_name == page_name

        if layer == "L1" and page_name in self._TAB_INDEX_MAP:
            png = adb.screencap()
            arr = _decode_png(png)
            h, w = arr.shape[:2]
            sw = max(scale_w, 1e-6)

            tab_y = max(h // 2, h - int(round(56 * sw)))
            tab_width = w // 4
            tab_idx = self._TAB_INDEX_MAP[page_name]
            tab_x = tab_width // 2 + tab_idx * tab_width

            for attempt in range(3):
                LOG.info("_recover_to_page: L1 → %s tap (%d, %d) attempt=%d/3",
                         page_name, tab_x, tab_y, attempt + 1)
                adb.tap(tab_x, tab_y)
                time.sleep(0.55)
                result = self.detect_detail(adb, scale_w)
                if result.page_name == page_name:
                    return True
                if attempt < 2:
                    LOG.info("_recover_to_page: page=%s (expected=%s), retrying",
                             result.page_name, page_name)

            LOG.warning("_recover_to_page: 3 attempts exhausted, page=%s",
                        result.page_name)
            return False

        return super()._recover_to_page(layer, page_name, adb, scale_w)
