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
        L2  聊天界面        WeChat FG + bottom-4-tab absent + no note-header
        L1  微信主界面      is_wechat_main_conversation_list_chrome()
        L0  手机主屏幕      foreground_package() ≠ com.tencent.mm

    Sub-page definitions (per-layer):

        L1 → chat_list / contacts / discover / profile  (4-tab switch via tap)
        L2 → group_chat / personal_chat                 (verify via detect_detail)
        L3 → wechat_note                                (verify via detect_detail)
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

            LOG.info("_recover_to_page: L1 → %s tap (%d, %d)", page_name, tab_x, tab_y)
            adb.tap(tab_x, tab_y)
            time.sleep(0.5)
            return self.detect_detail(adb, scale_w).page_name == page_name

        return super()._recover_to_page(layer, page_name, adb, scale_w)


# ── 微信主界面会话列表归位 ─────────────────────────────────────────────
# 自 v0.5 起迁移至 mum.android.wechat.reposition，此处保留重导出以兼容旧代码。
# 新代码请直接：
#   from mum.android.wechat.reposition import (
#       reposition_wechat_to_list_top, RepositionResult,
#       _REPOSITION_FROM_LO, _REPOSITION_FROM_HI,
#       _REPOSITION_L_LO, _REPOSITION_L_HI, _REPOSITION_MAX_RETRIES,
#   )
from mum.android.wechat.reposition import (  # noqa: F401 E402
    _reposition_dhash64,
    _reposition_hamming,
    RepositionResult,
    reposition_wechat_to_list_top,
    _REPOSITION_FROM_LO,
    _REPOSITION_FROM_HI,
    _REPOSITION_L_LO,
    _REPOSITION_L_HI,
    _REPOSITION_MAX_RETRIES,
)

import random as _random
from typing import Any as _Any

# 会话列表 scroll_down / scroll_up 手势参数 (from×L 模型，设备自适应)
# from 固定值（原随机区间中点），L 按 Android 版本选择：
#   Android >= 13 → L ∈ [51%, 53%]  (D2/D3 高分/低分屏)
#   Android < 13  → L ∈ [66%, 68%]  (D1 MI 8 UD, 早期系统手势模型)
# to = max(14%, from − L)  /  to = min(93%, from + L)
#
# 关键约束：滑动速率 ≤ 1.0 px/ms（防 Android fling 惯性）
#   duration = L × screen_h / VELOCITY_MAX
_SESSION_LIST_SCROLL_DOWN_FROM = 0.86  # 固定起点（原 [84%, 88%] 中点）
_SESSION_LIST_SCROLL_UP_FROM   = 0.165  # 固定起点（原 [14%, 19%] 中点）

_SESSION_LIST_SCROLL_DOWN_L_LO_OLD = 0.66  # Android < 13 (D1 高分屏)
_SESSION_LIST_SCROLL_DOWN_L_HI_OLD = 0.68
_SESSION_LIST_SCROLL_DOWN_L_LO_NEW = 0.51  # Android >= 13 (D2/D3)
_SESSION_LIST_SCROLL_DOWN_L_HI_NEW = 0.53

_SESSION_LIST_SCROLL_DOWN_TO_MIN  = 0.14
_SESSION_LIST_SCROLL_DOWN_TO_MAX  = 0.93
_SESSION_LIST_SCROLL_DOWN_VELOCITY_MAX = 1.0  # px/ms 上限，防 fling 惯性


def _get_android_version(adb: AdbProtocol) -> int:
    """Query Android release version via adb (e.g. 10, 13, 16)."""
    try:
        raw = adb._run(["shell", "getprop", "ro.build.version.release"])
        return int(raw.strip().split(".", 1)[0])
    except Exception:
        LOG.warning("Failed to query Android version, assuming < 13")
        return 0


def session_list_content_scroll_down(
    adb: AdbProtocol,
    *,
    screen_w: int,
    screen_h: int,
    duration_ms: int = 0,
) -> dict[str, _Any]:
    """会话列表上滑翻页 — finger up → content scrolls down → 露出下一页（PRD §2.(2)C）。

    Gesture (from×L model, device-adaptive, velocity ≤ 1.0 px/ms):
        from = 86% (fixed)
        L ∈ [66%, 68%] (Android < 13) / L ∈ [51%, 53%] (Android ≥ 13)
        to = max(14%, from − L)
        duration = L × screen_h / VELOCITY_MAX  (防 Android fling 惯性)

    Returns:
        dict with ``from_ratio``, ``to_ratio``, ``L_ratio``.
    """
    android_ver = _get_android_version(adb)
    if android_ver >= 13:
        L_lo, L_hi = _SESSION_LIST_SCROLL_DOWN_L_LO_NEW, _SESSION_LIST_SCROLL_DOWN_L_HI_NEW
    else:
        L_lo, L_hi = _SESSION_LIST_SCROLL_DOWN_L_LO_OLD, _SESSION_LIST_SCROLL_DOWN_L_HI_OLD

    from_ratio = _SESSION_LIST_SCROLL_DOWN_FROM
    L_ratio    = _random.uniform(L_lo, L_hi)
    to_ratio   = max(_SESSION_LIST_SCROLL_DOWN_TO_MIN, from_ratio - L_ratio)
    actual_L   = from_ratio - to_ratio

    x_mid = screen_w // 2
    y_s = int(round(screen_h * from_ratio))
    y_e = int(round(screen_h * to_ratio))

    if duration_ms <= 0:
        duration_ms = max(100, int(round(actual_L * screen_h / _SESSION_LIST_SCROLL_DOWN_VELOCITY_MAX)))

    LOG.info(
        "session_list_content_scroll_down: swipe (%d,%d)->(%d,%d)"
        " from=%.1f%% to=%.1f%% L=%.1f%% dur=%dms v=%.1fpx/ms android=%d",
        x_mid, y_s, x_mid, y_e,
        from_ratio * 100, to_ratio * 100, actual_L * 100,
        duration_ms, actual_L * screen_h / duration_ms,
        android_ver,
    )
    adb.swipe(x_mid, y_s, x_mid, y_e, duration_ms=duration_ms)

    return {
        "from_ratio": round(from_ratio, 4),
        "to_ratio": round(to_ratio, 4),
        "L_ratio": round(actual_L, 4),
    }


def session_list_content_scroll_up(
    adb: AdbProtocol,
    *,
    screen_w: int,
    screen_h: int,
    duration_ms: int = 0,
) -> dict[str, _Any]:
    """会话列表下拉回翻 — finger down → content scrolls up → 回到上一页（PRD §3.(2)）。

    scroll_down 的镜像：from 在内容带顶部，L 相同，to = from + L。
    与归位下拉 ``reposition_to_list_top`` 独立（后者有早停 / 双门检测 / 冷启动）。

    Gesture (from×L model, device-adaptive, velocity ≤ 1.0 px/ms):
        from = 16.5% (fixed)
        L ∈ [66%, 68%] (Android < 13) / L ∈ [51%, 53%] (Android ≥ 13)
        to = min(93%, from + L)

    Returns:
        dict with ``from_ratio``, ``to_ratio``, ``L_ratio``.
    """
    android_ver = _get_android_version(adb)
    if android_ver >= 13:
        L_lo, L_hi = _SESSION_LIST_SCROLL_DOWN_L_LO_NEW, _SESSION_LIST_SCROLL_DOWN_L_HI_NEW
    else:
        L_lo, L_hi = _SESSION_LIST_SCROLL_DOWN_L_LO_OLD, _SESSION_LIST_SCROLL_DOWN_L_HI_OLD

    from_ratio = _SESSION_LIST_SCROLL_UP_FROM
    L_ratio    = _random.uniform(L_lo, L_hi)
    to_ratio   = min(_SESSION_LIST_SCROLL_DOWN_TO_MAX, from_ratio + L_ratio)
    actual_L   = to_ratio - from_ratio

    x_mid = screen_w // 2
    y_s = int(round(screen_h * from_ratio))
    y_e = int(round(screen_h * to_ratio))

    if duration_ms <= 0:
        duration_ms = max(100, int(round(actual_L * screen_h / _SESSION_LIST_SCROLL_DOWN_VELOCITY_MAX)))

    LOG.info(
        "session_list_content_scroll_up: swipe (%d,%d)->(%d,%d)"
        " from=%.1f%% to=%.1f%% L=%.1f%% dur=%dms v=%.1fpx/ms android=%d",
        x_mid, y_s, x_mid, y_e,
        from_ratio * 100, to_ratio * 100, actual_L * 100,
        duration_ms, actual_L * screen_h / duration_ms,
        android_ver,
    )
    adb.swipe(x_mid, y_s, x_mid, y_e, duration_ms=duration_ms)

    return {
        "from_ratio": round(from_ratio, 4),
        "to_ratio": round(to_ratio, 4),
        "L_ratio": round(actual_L, 4),
    }
