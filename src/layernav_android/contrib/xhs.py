"""Xiaohongshu placeholder layer model.

.. code-block:: python

    from layernav_android.contrib.xhs import XhsLayerModel

    model = XhsLayerModel()
"""

from __future__ import annotations

import logging
import time

from layernav_android._protocol import AdbProtocol
from layernav_android.base import KEYCODE_BACK, KEYCODE_HOME, BaseLayerModel, LayerDef

LOG = logging.getLogger("layernav.xhs")


class XhsLayerModel(BaseLayerModel):
    """Placeholder layer model for Xiaohongshu feed collection."""

    layers = [
        LayerDef("L0", "home", "手机主屏幕", "foreground ≠ com.xingin.xhs"),
        LayerDef("L1", "feed", "小红书首页推荐流", "TODO"),
        LayerDef("L2", "note_detail", "笔记详情页", "TODO"),
        LayerDef("L3", "comments", "评论浮层", "TODO"),
    ]

    def detect(self, adb: AdbProtocol, scale_w: float) -> str:
        return "L0"

    def _on_L0(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        self._cold_start(adb, "L1", scale_w)
        return "L1"

    def _on_L1(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        return None

    def _on_L2(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        return None

    def _on_L3(self, adb: AdbProtocol, scale_w: float, *, quick: bool = False) -> str | None:
        return None

    def _cold_start(self, adb: AdbProtocol, target_layer: str, scale_w: float) -> None:
        adb.key_event(KEYCODE_HOME)
        time.sleep(0.8)
        adb._run(["shell", "am", "start", "-n", "com.xingin.xhs/.activity.SplashActivity"])
        time.sleep(3.0)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.detect(adb, scale_w) == target_layer:
                return
            time.sleep(1.0)
