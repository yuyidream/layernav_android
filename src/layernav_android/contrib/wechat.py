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


# ── 微信主界面会话列表归位（PRD §步骤1(2)） ──────────────────────────────────
# 从任意 WeChat 状态 → 导航到 L1 chat_list → 下拉触发「最近」→ 点击「微信」→ 列表顶端
# from×L 随机采样 + dHash 重试 + 冷启动兜底，三设备 10 轮 100% 验证

import random as _random
from dataclasses import dataclass as _dataclass, field as _field
from typing import Any as _Any

# 手势参数（三设备统一, 2026-06-02 真机验证）
_REPOSITION_FROM_LO = 0.13
_REPOSITION_FROM_HI = 0.25
_REPOSITION_L_LO    = 0.30
_REPOSITION_L_HI    = 0.42
_REPOSITION_MAX_RETRIES = 3


def _reposition_dhash64(bgr: np.ndarray) -> np.ndarray:
    """64-bit difference hash for post-failure frame comparison."""
    gray = __import__("cv2").cvtColor(bgr, __import__("cv2").COLOR_BGR2GRAY)
    r = __import__("cv2").resize(gray, (9, 8), interpolation=__import__("cv2").INTER_AREA)
    return (r[:, 1:] > r[:, :-1]).flatten()


def _reposition_hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int((a != b).sum())


@_dataclass
class RepositionResult:
    """归位全链路结果：导航 + 锚点下拉。"""
    ok: bool
    reason: str = ""                       # 空串=成功；否则为失败码
    swipes_used: int = 0                   # 实际下拉次数
    swipes_max: int = 0                    # 最大允许下拉次数
    early_stop_triggered: bool = False     # 是否因 heading 早停
    swipe_details: list[dict[str, _Any]] = _field(default_factory=list)


def reposition_wechat_to_list_top(
    adb: AdbProtocol,
    *,
    scale_w: float,
    screen_w: int,
    screen_h: int,
    deadline_s: float = 60.0,
    require_visible_pinned_row: bool = False,
) -> RepositionResult:
    """归位到微信主界面会话列表最顶端（PRD §步骤1）。

    Two phases:
    (1) Screenshot + layer detect → navigate to L1 chat_list (A/B/C)
    (2) Pull-down to trigger 「最近」→ tap bottom 「微信」→ back to list top

    Gesture params (3-device unified, from×L model):
        from ∈ [13%, 25%]   L ∈ [30%, 42%]   to = min(99%, from+L)

    Retry: re-sample within same ranges up to 3 times; dHash cross-check
    after 2 consecutive failures.  Cold-start fallback on triple failure.

    Args:
        adb: :class:`AdbProtocol` client.
        scale_w: ``screen_w / 1080.0``.
        screen_w, screen_h: device resolution in pixels.
        deadline_s: total time budget.
        require_visible_pinned_row: passed to ``is_wechat_main_conversation_list_chrome``
            for the final verification (default False).

    Returns:
        :class:`RepositionResult` with ``ok=True`` on success.
    """
    try:
        from collector_phone_android.vision.template_matcher import (
            _recent_pull_top_heading_likely,
            detect_wechat_main_bottom_tab_bar_four_columns,
            is_wechat_main_conversation_list_chrome,
        )
    except ImportError:
        LOG.error("reposition: missing collector_phone_android.vision.template_matcher")
        return RepositionResult(ok=False, reason="import_error")

    classify_deadline = time.monotonic() + deadline_s
    navigated_ok = False

    LOG.info(
        "reposition_to_list_top: deadline=%.0fs screen=%dx%d",
        deadline_s, screen_w, screen_h,
    )

    # (1) Navigate to L1 chat_list — PRD §步骤1(1) A/B/C
    model = WeChatGroupLayerModel()
    model.init(adb)

    dr = model.detect_detail(adb, scale_w)
    LOG.info("reposition_to_list_top: detected layer=%s page=%s", dr.layer_key, dr.page_name)

    if time.monotonic() >= classify_deadline:
        return RepositionResult(ok=False, reason="step1_deadline_nav")

    if dr.layer_key == "L1":
        navigated_ok = True
        LOG.info("reposition_to_list_top: already on L1")
    elif dr.layer_key == "L2":
        cur = model.back_one(adb, scale_w)
        navigated_ok = (cur == "L1")
        LOG.info("reposition_to_list_top: L2 → back_one → %s (ok=%s)", cur, navigated_ok)
    else:
        LOG.info("reposition_to_list_top: %s → restore(L1, target_page=chat_list)", dr.layer_key)
        navigated_ok = model.restore(adb, "L1", scale_w, target_page="chat_list")

    if not navigated_ok:
        LOG.error("reposition_to_list_top: navigation to L1 failed")
        return RepositionResult(ok=False, reason="step1_nav_exhausted")
    if time.monotonic() >= classify_deadline:
        return RepositionResult(ok=False, reason="step1_deadline_before_anchor")

    # (2) Pull-down anchor — from×L random + dHash retry + cold-start fallback
    tab_x, tab_y = _calc_wechat_session_tab(screen_w, screen_h, scale_w)
    x_mid = screen_w // 2

    swipe_details: list[dict[str, _Any]] = []
    early_stop_triggered = False
    swipes_used = 0
    cold_start_retry_used = False
    fail_frames: list[np.ndarray] = []
    total_swipes = 0
    MAX_COLD_START_BONUS = 1

    while total_swipes < _REPOSITION_MAX_RETRIES + MAX_COLD_START_BONUS:
        if time.monotonic() >= classify_deadline:
            return RepositionResult(
                ok=False, reason="anchor_deadline", swipes_used=swipes_used,
                swipes_max=_REPOSITION_MAX_RETRIES, swipe_details=swipe_details,
            )

        from_ratio = _random.uniform(_REPOSITION_FROM_LO, _REPOSITION_FROM_HI)
        L_ratio    = _random.uniform(_REPOSITION_L_LO, _REPOSITION_L_HI)
        to_ratio   = min(0.99, from_ratio + L_ratio)
        y_s = int(round(screen_h * from_ratio))
        y_e = int(round(screen_h * to_ratio))

        idx = total_swipes + 1
        detail: dict[str, _Any] = {
            "index": idx, "from_y_ratio": round(from_ratio, 4),
            "to_y_ratio": round(to_ratio, 4),
        }

        adb.swipe(x_mid, y_s, x_mid, y_e, duration_ms=380)
        time.sleep(0.32)

        arr_probe = _decode_png(adb.screencap())
        heading = _recent_pull_top_heading_likely(arr_probe, scale_w)
        detail["heading_likely"] = heading

        if heading:
            bottom = detect_wechat_main_bottom_tab_bar_four_columns(arr_probe, scale_w)
            detail["bottom_tab_four_columns"] = bottom
            if not bottom:
                early_stop_triggered = True
                swipes_used = idx
                LOG.info("reposition_to_list_top: early stop swipe %d from=%.1f%% to=%.1f%% L=%.1f%%",
                         idx, from_ratio * 100, to_ratio * 100, (to_ratio - from_ratio) * 100)
                break

        fail_frames.append(arr_probe.copy())
        swipe_details.append(detail)
        total_swipes += 1

        if len(fail_frames) >= 2:
            dh1 = _reposition_dhash64(fail_frames[-2])
            dh2 = _reposition_dhash64(fail_frames[-1])
            ham = _reposition_hamming(dh1, dh2)
            LOG.info("reposition_to_list_top: 2 consecutive fails dHash ham=%d", ham)

        if total_swipes >= _REPOSITION_MAX_RETRIES and not cold_start_retry_used:
            cold_start_retry_used = True
            fail_frames.clear()
            LOG.warning("reposition_to_list_top: %d swipes failed, cold start retry", _REPOSITION_MAX_RETRIES)
            model2 = WeChatGroupLayerModel()
            model2.init(adb)
            navigated_ok = model2.restore(adb, "L1", scale_w, target_page="chat_list")
            if not navigated_ok:
                LOG.error("reposition_to_list_top: cold start retry navigation failed")
                break
            continue

    if not early_stop_triggered:
        swipes_used = total_swipes

    if time.monotonic() >= classify_deadline:
        return RepositionResult(
            ok=False, reason="anchor_deadline", swipes_used=swipes_used,
            swipes_max=_REPOSITION_MAX_RETRIES, swipe_details=swipe_details,
        )

    adb.tap(tab_x, tab_y)
    time.sleep(0.55)

    if time.monotonic() >= classify_deadline:
        return RepositionResult(
            ok=False, reason="anchor_deadline", swipes_used=swipes_used,
            swipes_max=_REPOSITION_MAX_RETRIES, swipe_details=swipe_details,
        )

    arr = _decode_png(adb.screencap())
    chrome_ok = is_wechat_main_conversation_list_chrome(
        arr, scale_w, require_visible_pinned_row=require_visible_pinned_row,
    )
    LOG.info("reposition_to_list_top: chrome_ok=%s", chrome_ok)

    return RepositionResult(
        ok=chrome_ok,
        reason="" if chrome_ok else "step1_chrome_failed",
        swipes_used=swipes_used,
        swipes_max=_REPOSITION_MAX_RETRIES,
        early_stop_triggered=early_stop_triggered,
        swipe_details=swipe_details,
    )
