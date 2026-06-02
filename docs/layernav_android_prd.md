# layernav_android 框架 PRD

> 原文件：`collector_phone_android/docs/adr/layer_model_framework_prd.md`
> 迁移日期：2026-05-31

---

## §1. 设计原则

1. **框架只做校验** — 框架负责层级检测和跨层前后校验，**不负责点击**。点击由 Task handler 自主执行。
2. **每个层级一个 handler** — `_on_Lx(adb, scale_w, *, quick=False)` 是 Task 定义的函数，框架按当前层自动调用。
3. **原子 API** — 4 个原子操作可自由组合，高层 `back()` / `advance()` / `restore()` 基于它们构建。

---

## §2. 层级定义

```
L(N-1)   最深层
  ...
L2       内容页
L1       App 主界面
L0       手机主屏幕（非 App 前台）
```

**传递方式**：`layers` 是 `BaseLayerModel` 上的 Python 类属性（`list[LayerDef]`），子类通过**类属性覆盖**定义自己的层级。不通过 YAML / JSON / 接口——因为层级检测是截图+模板匹配的函数调用（`detect_wechat_note_header(arr, scale_w)`），YAML 无法表达，`layers`、`detect()`、`_on_Lx()` 三者紧耦合适合用 Python。

```python
# 框架定义槽位
class BaseLayerModel:
    layers: list[LayerDef] = []

# Task 覆盖
class WeChatGroupLayerModel(BaseLayerModel):
    layers = [
        LayerDef("L0", "home", "手机主屏幕", "foreground ≠ com.tencent.mm"),
        LayerDef("L1", "main_list", "微信主会话列表", "is_main_list_chrome()"),
        LayerDef("L2", "chat", "群聊天界面", "WeChat FG + no tabs4 + no notes"),
        LayerDef("L3", "notes", "微信笔记", "detect_note_header()"),
    ]
```

```python
@dataclass
class LayerDef:
    key: str         # "L0" | "L1" | ... | "L(N-1)"
    name: str        # 机器名
    label_cn: str    # 中文名
    detection: str   # 检测方法说明
```

---

## §3. Task 子类契约

### §3.1 `detect(adb, scale_w) → str`

返回当前层级 key。**必须基于实时截屏**。

### §3.2 `_on_Lx(adb, scale_w, *, quick=False) → str | None`

层级 handler（x = 0, 1, 2, 3, ...）。Task 按需覆盖。

| 参数 | 说明 |
|------|------|
| `adb` | ADB 客户端 |
| `scale_w` | 屏幕宽度缩放因子 |
| `quick` | `False`（默认）：完整业务逻辑；`True`：BACK 恢复时精简（第一个未读/第一个卡片） |

**返回值**：执行业务逻辑 + 点击动作后，返回**期望到达的下一层级 key**。
- 返回 `"L2"` → 框架立即截屏校验是否到达 L2
- 返回 `None` 或当前层 key → 无需前进

**handler 自己负责所有动作**（`adb.tap`、`adb.swipe` 等）。

```python
def _on_L1(self, adb, scale_w, *, quick=False) -> str | None:
    if quick:
        row = self._pick_first_unread(adb, scale_w)
    else:
        row = self._scan_and_select(adb, scale_w)
    if row is None:
        return None
    adb.tap(row.tap_cx, row.tap_cy)
    return "L2"
```

---

## §4. 框架 API —— 4 个原子操作

### §4.1 `detect(adb, scale_w) → str`

**查询当前所在层级**。Task 覆盖。

---

### §4.2 `enter_next(adb, scale_w, *, quick=False) → bool`

**从当前层级进入下一个层级**（单步）。
**必须先检查当前层级，进入后立即检查目标层级。**

```
enter_next(*, quick):
    1. cur = detect()                                    ← ★ 先检查当前层级
    2. result = _on_L[cur](adb, scale_w, quick=quick)   ← 调 handler（handler 执行业务+点击）
    3. if result is None or result == cur:
         return True                                     ← handler 说无需前进
    4. 轮询 detect()，间隔 0.3s→0.6s→0.9s→1.2s→1.5s→2.0s，最长 8s  ← ★ 纯自适应轮询，无固定预等待
    5. 命中目标 → return True
    6. 超时未到达 → return False
```

`quick` 参数透传给 handler。

---

### §4.3 `back_one(adb, scale_w) → str`

**从当前层级返回上一个层级**（单步 KEYCODE_BACK）。
**必须先检查当前层级，BACK 后立即检查所在层级。**

```
back_one():
    1. cur = detect()              ← ★ 先检查当前层级
    2. KEYCODE_BACK
    3. sleep(1.0)
    4. next = detect()             ← ★ BACK 后立即检查所在层级
    5. return next
```

返回 BACK 后所在的层级 key。不验证方向——由调用方决定是否继续。

---

### §4.4 `back_recover(adb, target_layer, scale_w) → bool`

**BACK 失败后恢复**：回到手机主屏幕 → 恢复到 BACK 的目标层级。

```
back_recover(target_layer):
    1. KEYCODE_HOME → 冷启动到 L1（子类覆盖 _cold_start）  ← 冷启动
    2. 循环 enter_next(quick=True) 直到 target_layer  ← 快速穿过中间层
    3. _on_L[target_layer](quick=False)                 ← 正常恢复业务（advance 内部完成）
    4. return detect() == target_layer
```

---

## §5. 框架 API —— 组合操作

以下基于 4 个原子操作构建。

### §5.1 `back(adb, to_layer, scale_w) → bool`

从任意当前层后退到 `to_layer`。

```
back(to_layer):
    for i in 0..2:
        cur = detect()
        if cur == to_layer:
            _on_L[to_layer](quick=False)          ← 到达 → 正常恢复
            return True
        if cur == "L0":
            break                                 ← 到桌面 → 走恢复
        back_one()                                ← §4.3
    return back_recover(to_layer)                 ← §4.4
```

### §5.2 `advance(adb, target_layer, scale_w, *, quick=False) → bool`

从当前层逐层前进到 `target_layer`。

```
advance(target, *, quick):
    while True:
        cur = detect()                            ← §4.1
        if cur == target:
            _on_L[target](quick=False)            ← 到达 → 正常执行
            return True
        ok = enter_next(quick=quick)              ← §4.2
        if not ok:
            return False
```

### §5.3 `restore(adb, target_layer, scale_w) → bool`

从任意位置恢复到 `target_layer`（自动判断方向）。

```
restore(target):
    cur = detect()
    if cur == target:
        return True
    if layer_index(cur) > layer_index(target):
        return back(target)                       ← 在上面 → 退
    else:
        return advance(target, quick=True)        ← 在下面 → 快速进
```

---

## §6. 完整示例

```
restore("L1")                          → _on_L1(quick=False) 正常

for group in scan_groups():
    advance("L2")                      → enter_next → _on_L1 → tap → 校验 L2
    for card in detect_cards():
        advance("L3")                  → enter_next → _on_L2 → tap → 校验 L3
        capture_note()
        back("L2")                     → back_one → _on_L2(quick=False) 恢复
    back("L1")                         → back_one → _on_L1(quick=False) 恢复
```

### BACK 失败恢复链

```
L3 → back("L2") → back_one 失灵 → L0
  → back_recover("L2"):
    cold-start → L1
    enter_next(quick=True) → _on_L1(quick=True) → tap → L2
    _on_L2(quick=False) → 正常恢复
```

---

## §7. 职责边界

| | 框架 | Task |
|---|------|------|
| `detect()` | 定义签名 | 覆盖实现 |
| `_on_Lx()` | 按层索引调用 | 覆盖：业务 + 点击 |
| `enter_next()` | 调 handler + 校验 | — |
| `back_one()` | KEYCODE_BACK + detect | — |
| `back_recover()` | 冷启动 + 快速前进 + 正常恢复 | — |
| `back()` | 循环 back_one + back_recover | — |
| `advance()` | 循环 enter_next | — |
| `restore()` | 方向判断 + 调 back/advance | — |
| 点击动作 | — | handler 内 `adb.tap()` |

### §7.1 有限状态与无限状态的分离（借鉴 Automat）

| 有限状态（状态图可见） | 无限状态（核心数据，不进入状态图） |
|---|---|
| L0 / L1 / L2 / L3 | 当前采集到第几个群 |
| | 已采集多少条笔记 |
| | 用户滚动位置 |
| | 重试计数 |

**设计决策**：`layers` 列表只描述页面层级结构（有限状态）。Handler 内部维护的业务状态（如 `_pick_first_unread` 的选行逻辑）属于"无限状态"，**不在层模型中体现**，由 Task 子类自行管理。这与 [Automat](https://github.com/glyph/automat) 的"Core Data"概念一脉相承——将无界数据从状态枚举中剥离，保持状态图简洁。

### §7.2 守卫（Guard）与验证器（Validator）语义分离（借鉴 python-statemachine）

| 概念 | 框架中的对应 | 说明 |
|------|------------|------|
| **guard**（守卫） | `enter_next` 步骤 1 的 `detect()` | 条件检查：当前层是否允许前进？ |
| **validator**（验证器） | `enter_next` 步骤 4-5 的轮询 `detect()` | 后置校验：页面是否真的跳到了目标层？ |
| **guard** | `back_one` 步骤 1 的 `detect()` | 条件检查：当前层是否允许后退？ |
| **validator** | `back_one` 步骤 3 的 `detect()` | 后置校验：BACK 后实际在哪个层？ |

guard 失败 → 拒绝操作（静默或日志告警）；validator 失败 → 触发恢复链（`back_recover`）。

---

## §8. 可观测性 —— LayerListener（借鉴 python-statemachine）

### §8.1 设计动机

借鉴 [`python-statemachine` 的 Listener 模式](https://github.com/fgmacedo/python-statemachine)，框架提供观察者接口，解耦状态变更与外部副作用（日志、指标、告警、截图收集）。

框架本身的 `LOG.debug/warning` 不面向程序消费；`LayerListener` 提供结构化事件回调。

### §8.2 接口

```python
class LayerListener(Protocol):
    def on_transition(self, from_layer: str, to_layer: str, method: str) -> None: ...
    def on_timeout(self, from_layer: str, target_layer: str, elapsed_s: float) -> None: ...
    def on_recovery(self, target_layer: str, ok: bool) -> None: ...
```

### §8.3 事件触发时机

| 事件 | 触发方法 | 触发时机 |
|------|---------|---------|
| `on_transition(cur, next, "enter_next")` | `enter_next` | 轮询验证通过，确认到达目标层 |
| `on_timeout(cur, target, elapsed)` | `enter_next` | 轮询超时，未到达目标层 |
| `on_transition(cur, next, "back_one")` | `back_one` | KEYCODE_BACK 后检测到层变更 |
| `on_recovery(target, ok)` | `back_recover` | 恢复流程结束后（ok=True 成功 / ok=False 失败） |

### §8.4 使用示例

```python
from layernav_android import LayerListener

class MetricsListener:
    def on_transition(self, from_layer, to_layer, method):
        statsd.increment(f"layer.{method}", tags={"from": from_layer, "to": to_layer})

    def on_timeout(self, from_layer, target_layer, elapsed_s):
        LOG.error("enter_next timeout: %s→%s after %.1fs", from_layer, target_layer, elapsed_s)

    def on_recovery(self, target_layer, ok):
        if not ok:
            alert.send(f"back_recover to {target_layer} FAILED")

model.add_listener(MetricsListener())
```

### §8.5 状态图

```
┌──────┐  _on_L0      ┌──────┐  _on_L1      ┌──────┐  _on_L2      ┌──────┐
│  L0  │─────────────→│  L1  │─────────────→│  L2  │─────────────→│  L3  │
│ 手机  │←─────────────│ 微信  │←─────────────│ 群聊  │←─────────────│ 笔记  │
│ 主屏  │  back_one   │ 主界面│  back_one   │ 界面  │  back_one   │ 页面  │
└──────┘              └──────┘              └──────┘              └──────┘
    ↑                     │
    │   cold-start        │
    └─────────────────────┘
        back_recover:
        HOME → cold-start → advance(quick=True)
```

> 注：`back_one` 连续 3 次未到达目标层时，自动触发 `back_recover` 恢复链。`advance` / `restore` / `back` 是上述原子操作的组合。

---

## §9. 通用冷启动 —— `cold_start_app_from_launcher`

### §9.1 设计动机

原 `BaseLayerModel._cold_start` 各子类自行实现（`am start -n` 或 `monkey`），缺乏统一的 Dock 图标兜底、session tab 点击、force-stop 控制等能力。抽取为独立通版函数，供所有 APP 模型和外部调用方使用。

### §9.2 函数签名

```python
from layernav_android.cold_start import cold_start_app_from_launcher, dock_app_icon_coords

def cold_start_app_from_launcher(
    adb: AdbProtocol,
    package: str,
    *,
    app_name: str = "wechat",
    M: int = 4,
    N: int | None = None,
    session_tab_x: int | None = None,
    session_tab_y: int | None = None,
    force_stop_before: bool = True,
    deadline_s: float = 25.0,
) -> bool:
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `adb` | ADB 客户端（`AdbProtocol`，使用普通 `tap`，非防风控触控） | — |
| `package` | Android 包名 | `"com.tencent.mm"` |
| `app_name` | APP 名称，驱动默认 M/N | `"wechat"` |
| `M` | Dock 槽位总数 | `4` |
| `N` | APP 图标所在槽位（1‑indexed） | `wechat→3`, `xhs→1` |
| `session_tab_x`, `session_tab_y` | 启动后需点击的 APP 内底栏 Tab 坐标（如微信「微信」Tab），不传则跳过 | `None` |
| `force_stop_before` | 冷启动前是否 `am force-stop` | `True` |
| `deadline_s` | 总超时 | `25.0` |

屏幕尺寸始终通过 `adb shell wm size` 自动获取，调用方无需传入。

### §9.3 冷启动路径

```
1. [可选] am force-stop <package>
2. monkey -p <package> -c LAUNCHER 1          ← 主路径
3. [可选] tap session_tab                       ← 进入APP后点击底栏Tab
4. ↓ 如果 foreground != package:
5. am start -a MAIN -c LAUNCHER <package>     ← 备选（定制 ROM 兼容）
6. [可选] tap session_tab
7. ↓ 如果仍然失败:
8. dock_app_icon_coords(app_name, M, N) → tap  ← Dock 图标兜底（0.5s 预等 + 最多重试 2 次）
9. [可选] tap session_tab
10. ↓ 如果 allow_reboot=True 且上面全部失败:
11. adb reboot → 等待设备上线 → 等待 boot_completed  ← 终极兜底（默认关闭）
12. → 重新执行 monkey 启动 + tap session_tab
```

### §9.4 Dock 坐标公式

```python
def dock_app_icon_coords(
    screen_w, screen_h, scale_w, *, app_name="wechat", M=4, N=None,
) -> tuple[int, int]:
    """Dock M 等分，APP 在第 N 格（1‑indexed），返回该槽位近似中心。"""
    if N is None:
        N = {"wechat": 3, "xhs": 1}.get(app_name, 1)
    dx = int(round(screen_w * (N - 0.5) / M))
    dy = screen_h - max(48, int(round(52 * scale_w)))
    return dx, dy
```

### §9.5 使用示例

```python
# 微信 — 最简调用（尺寸自动获取，无 session tab）
cold_start_app_from_launcher(
    adb, "com.tencent.mm",
    app_name="wechat", M=4, N=3,
)

# 微信 — 完整调用（含 session tab 定位到「微信」主列表）
cold_start_app_from_launcher(
    adb, "com.tencent.mm",
    app_name="wechat", M=4, N=3,
    session_tab_x=108, session_tab_y=2192,
)

# 小红书
cold_start_app_from_launcher(
    adb, "com.xingin.xhs",
    app_name="xhs", M=4, N=1,
)
```

### §9.6 设计说明

- **使用普通 ADB tap**（非防风控触控）：冷启动是系统级操作（桌面 Dock 图标点击），不涉及 APP 内反爬检测，使用 `AdbProtocol.tap()` 即可，方便所有系统集成。
- **屏幕尺寸自动获取**：`screen_w` / `screen_h` / `scale_w` 不再作为参数，函数内部通过 `adb shell wm size` 自动获取。调用方只需传 `app_name` / `M` / `N` 三个核心参数即可计算出 Dock 图标坐标。
- **四条冷启动路径**：monkey（主路径）→ am start Intent（定制 ROM 备选）→ Dock 图标点击（兜底，含 0.5s 预等待 + 最多 2 次重试）→ adb reboot（终极兜底，`allow_reboot=True` 且前 3 条均失败后触发）。覆盖大部分设备和 ROM。
- `force_stop` 通过 `adb._run(["shell", "am", "force-stop", package])` 实现，无需额外接口。
- 返回值 `bool` 表示 `foreground_package() == package`，调用方需自行判断是否到达目标层级。

### §9.7 adb reboot 兜底

当 `allow_reboot=True` 且三条启动路径全部失败时，执行系统重启作为终极恢复：

1. 调用 `adb._run(["reboot"])` 重启设备
2. 每 3 s 轮询 `adb shell echo ok` 等待设备恢复连接（最长 90 s）
3. 每 3 s 轮询 `adb shell getprop sys.boot_completed` 等待系统启动完成（最长 60 s）
4. 重新执行 monkey 启动 + session tab 点击

> ⚠️ 重启总耗时 60–120 s，且要求设备无需手动解锁（无 PIN / 图案锁）。默认关闭。仅适用于无人值守的 7×24 自动化场景。

---

## §10. 微信主界面会话列表归位 —— `reposition_wechat_to_list_top`

### §10.1 设计动机

微信采集自动化中，需要在进入群聊前将微信前台归位到**主界面会话列表最顶端**。此操作包含导航（L2/L0→L1）和锚点下拉（触发「最近」页面），是从 `collector_phone_android` 抽取到框架层的完整归位功能。

### §10.2 函数签名

```python
from layernav_android.contrib.wechat import (
    reposition_wechat_to_list_top, RepositionResult,
)

def reposition_wechat_to_list_top(
    adb: AdbProtocol,
    *,
    scale_w: float,
    screen_w: int,
    screen_h: int,
    deadline_s: float = 60.0,
    require_visible_pinned_row: bool = False,
) -> RepositionResult:
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `adb` | ADB 客户端（需实现 `swipe`） | — |
| `scale_w` | `screen_w / 1080.0` | — |
| `screen_w`, `screen_h` | 设备分辨率 | — |
| `deadline_s` | 总超时 | `60.0` |
| `require_visible_pinned_row` | 终检是否要求置顶行可见 | `False` |

### §10.3 归位流程

```
1. detect_detail → 获取当前 layer + page
2. L2 → KEYCODE_BACK (单次)
   L0/其他 → restore(adb, "L1", target_page="chat_list")  (冷启动)
   L1 → 已在主列表，跳过
3. 下拉锚点循环：
   a. 随机采样: from∈[13%,25%], L∈[30%,42%], to=min(99%,from+L)
   b. adb.swipe(x_mid, y_s, x_mid, y_e, duration=380ms)
   c. 截图 → _recent_pull_top_heading_likely (单门早停)
   d. 失败 → 重试（同范围重新采样，最多 3 次）
   e. 连续 2 次失败 → dHash64 交叉校验信号可信度
   f. 3 次仍失败 → 冷启动兜底 (restore→L1 chat_list)
4. 点击底部「微信」Tab → 回到主列表顶端
5. is_wechat_main_conversation_list_chrome() 终检
```

### §10.4 手势参数

三设备统一 `from × L` 模型，2026-06-02 真机 10 轮验证 100%：

| 参数 | 范围 | 说明 |
|---|---|---|
| `_REPOSITION_FROM_LO/HI` | 13% / 25% | 起点在屏高范围内随机 |
| `_REPOSITION_L_LO/HI` | 30% / 42% | 直线长度在范围内随机 |
| `_REPOSITION_MAX_RETRIES` | 3 | 单轮最大重试次数 |

- 手势方式：仅 `adb shell input swipe` 直线滑动（贝塞尔曲线 / sendevent 已禁用）
- 抗风控：随机采样
- 依赖：`collector_phone_android.vision.template_matcher`（lazy import）
