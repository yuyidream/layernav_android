# layernav_android 框架 PRD

## §1. 设计原则

1. **框架只做校验** — 框架负责层级检测和跨层前后校验，**不负责点击**。点击由 Task handler 自主执行。
2. **每个层级一个 handler** — `_on_Lx(adb, scale_w, *, quick=False)` 是 Task 定义的函数，框架按当前层自动调用。
3. **原子 API** — 5 个原子操作可自由组合。

---
4. 整体架构（职责边界）

框架层 (base.py)
├─ detect / detect_layer          ← 层级检测
├─ back_one / home_one            ← 后退 / HOME
├─ back_recover                   ← 冷启动恢复
├─ poll_until_target_layer        ← 到达轮询
├─ _tap_to_layer(x, y, target)    ← tap + poll 闭环（v0.5.5 上提）
├─ _do_tap(x, y, jitter_x, jitter_y) ← 层间点击（默认 adb.tap）
└─ _call_on_layer(layer)          ← handler 路由

collector 业务层
├─ _do_tap → adb.click_xonly      ← mumdad 风控
├─ _tap_row                        ← 坐标计算（badge 感知）
├─ _on_L1 → _tap_row + _tap_to_layer(→ L2)
└─ _on_L2 → card.click + _tap_to_layer(→ L3)


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
    key: str             # "L0" | "L1" | ... | "L(N-1)"
    name: str            # 机器名
    label_cn: str        # 中文名
    detection: str       # 检测方法说明
    page_name: str = ""  # 可选子页面名（如 L1 的 "chat_list" / "contacts"）
    detection_extra: str = ""  # 检测补充说明（如可切换的子页面列表）
```

---

## §3. Task 子类契约

### §3.1 `detect(adb, scale_w) → str | None`

返回当前层级 key。**必须基于实时截屏**。

无法判定时返回 `None` — 调用方应将其视为"未知位置"并触发恢复流程（`back_recover`）。

### §3.2 `_on_Lx(adb, scale_w, *, quick=False) → str | None`

层级 handler（x = 0, 1, 2, 3, ...）。Task 按需覆盖。

| 参数 | 说明 |
|------|------|
| `adb` | ADB 客户端 |
| `scale_w` | 屏幕宽度缩放因子 |
| `quick` | `False`（默认）：完整业务逻辑；`True`：BACK 恢复时精简（第一个未读/第一个卡片） |

**返回值**：执行业务逻辑 + 点击动作后，返回**期望到达的下一层级 key**。
- 例如返回 `"L2"` → 框架立即截屏校验是否到达 L2（动态判断，不限于 L2）
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

### §3.3 `detect_layer(adb, scale_w, layer) → bool`（v0.5.0）

**目标感知检测**：回答"当前页面是否匹配指定的 *layer*？"与 `detect()`（"我在哪"）职责分离。导航 API（`poll_until_target_layer / back_recover`）使用 `detect_layer` 验证目标到达，`detect()` 仅用于"我在哪"查询。

**设计意图**：`detect()` 需要匹配多层的模板，来确定自己在哪一层。 `detect_layer()` 只需要匹配目标层这一层的模板，判断是否到达目标层即可。

```python
def detect_layer(self, adb, scale_w, layer: str) -> bool:
    """Check if the current screen matches *layer*."""
    screenshot = adb.screencap()
    if layer == "L0":
        return adb.foreground_package() != "com.tencent.mm"
    if layer == "L3":
        return detect_note_header(screenshot) is not None
    if layer == "L2":
        return detect_chat_chevron(screenshot) is not None
    if layer == "L1":
        # Subclass may add extra negation checks (e.g. reject L1 if L2 features present)
        return is_main_chrome(screenshot)
    return False
```



### §3.4 `detect_detail(adb, scale_w) → DetectResult`

**带子页面信息的层级检测**：返回 `DetectResult`（包含 `layer_key` 和 `page_name`）。默认实现调用 `detect()` 后从 `layers` 表中查找 `LayerDef.page_name`；子类可覆盖以**动态设置** `page_name`（如 L1 根据当前所在 Tab 返回 `"chat_list"` 或 `"contacts"`）。

```python
@dataclass
class DetectResult:
    layer_key: str   # "L0" | "L1" | "L2" | "L3"
    page_name: str = ""  # 从 LayerDef.page_name 查表或动态设置

def detect_detail(self, adb, scale_w) -> DetectResult:
    layer_key = self.detect(adb, scale_w)
    page_name = ""
    for ld in self.layers:
        if ld.key == layer_key:
            page_name = ld.page_name
            break
    return DetectResult(layer_key=layer_key, page_name=page_name)
```

### §3.5 `_recover_to_page(layer, page_name, adb, scale_w) → bool`

**到达目标层后导航到指定子页面**。由 `back_recover` 调用。默认实现：调用 `detect_detail()` 校验 `page_name` 是否匹配；子类可覆盖为具体导航动作（如 L1 切换到「微信」Tab）。

```python
def _recover_to_page(self, layer, page_name, adb, scale_w) -> bool:
    result = self.detect_detail(adb, scale_w)
    return result.page_name == page_name
```

### §3.6 `init(adb) → None`

**一次性初始化钩子**（可选覆盖）。框架在首次使用模型前调用，子类可在此完成 ADB 设备相关的预配置。默认空实现。

---

## §4. 框架 API —— 5 个原子操作 + 1 个目标感知检测

### §4.1 `back_one(adb, scale_w, *, max_retries=3) → str`

**退回到上一层**（KEYCODE_BACK，`poll_until_target_layer` + 冷启动兜底）。

.. versionchanged:: 0.5.5
    重构为 ``poll_until_target_layer`` 闭环：计算上一层 key → KEYCODE_BACK → poll 到达。
    不再使用 fixed-sleep(1s)+单次 detect。

```
back_one(*, max_retries):
    cur = detect()                         ← guard (pre-check)
    计算 target = 上一层 key（L3→L2, L2→L1, L1→L0,...）
    for attempt in range(max_retries):
        KEYCODE_BACK
        if poll_until_target_layer(target) ← validator (poll to target)
            notify_transition(cur, target) ← 触发 listener
            return target
    return back_recover(target)            ← 兜底：冷启动恢复
```

返回 BACK 后所在的层级 key。重试 *max_retries* 次仍无法退出时，自动走 :ref:`§4.4` 冷启动恢复。

---

### §4.2 `home_one(adb, scale_w) → str | None`

**从当前层级按 HOME 键回到手机主屏幕**（单步 KEYCODE_HOME）。

```
home_one():
    1. cur = detect()              ← ★ 先检查当前层级
    2. KEYCODE_HOME
    3. sleep(0.8)
    4. next = detect()             ← ★ HOME 后检查所在层级
    5. return next
```

返回 HOME 后所在的层级 key。**OOP 版本**（挂在 `BaseLayerModel` 上，触发 listener 通知）。

另有 **模块级函数** `layernav_android.home_one(adb)` — 仅执行 `key_event(KEYCODE_HOME) + sleep(0.8)`，不检测层级，用于无 model 实例的启动保活场景（如 `app_operations/start_app`）。

---

### §4.3 `poll_until_target_layer(adb, target_layer, scale_w, *, max_wait_s=8.0) → bool`

**自适应轮询检测目标层级**。调用方先执行 tap，再调用本方法等待目标层出现。

```
poll_until_target_layer(target_layer):
    1. cur = detect()
    2. if cur is not None and detect_layer(target_layer) → return True     ← 已在目标层
    3. 轮询 detect_layer(target_layer)，间隔 0.3s→0.6s→…→2.0s，最长 max_wait_s
    4. 命中 → return True
    5. 超时 → return False
```

自适应轮询引擎（0.3s 初始，步长 0.3s，上限 2.0s）。**不触发 listener 通知** — 通知职责由调用方自行处理。

---

### §4.4 `back_recover(adb, target_layer, scale_w, *, target_page=None) → bool`

**BACK 失败后恢复**：回到手机主屏幕 → 恢复到 BACK 的目标层级（v0.4.3: 冷启动 3 次重试 + `adb reboot` 兜底）。

```
back_recover(target_layer, *, target_page):
    1. KEYCODE_HOME → 冷启动到 L1（子类覆盖 _cold_start）
       ├─ 尝试 1: _cold_start(L1) — 失败 → HOME 重试
       ├─ 尝试 2: _cold_start(L1) — 失败 → HOME 重试
       ├─ 尝试 3: _cold_start(L1) — 失败 → HOME 进入 reboot 路径
       └─ 尝试 4: _cold_start(L1, allow_reboot=True) — 失败 → return False
    2. while not detect_layer(target_layer):                  ← 快速穿过中间层
         cur = detect()
         _call_on_layer(cur, adb, scale_w, quick=True)
    3. _on_L[target_layer](quick=False)                 ← 目标层正常恢复
    4. if target_page is not None:
         ok = _recover_to_page(target_layer, target_page, ...)  ← 导航到子页面
         if not ok: return False
    5. return detect_layer(target_layer)
```

| 参数 | 说明 | v0.4.3 新增 |
|------|------|------------|
| 冷启动重试 | 3 次常规重试 + 1 次 `adb reboot` 兜底 | ✅ |
| `_cold_start` 签名 | 新增 `*, allow_reboot: bool = False` 参数 | ✅ |
| `target_page` | 同 v0.3.0，恢复后自动定位到子页面 | — |

`allow_reboot=True` 时，`cold_start_app_from_launcher` 在 monkey / am start / Dock icon 三条路径全部失败后，执行 `adb reboot` → `_wait_for_boot_completed` → 重新 monkey 启动。重启耗时 60–120s，要求设备无锁屏。

---

### §4.5 `_do_tap(adb, click_x, click_y, *, jitter_x=0, jitter_y=0) → None`

因为项目有可能要使用防风控点击，点击功能必须单独拆分出来作为一个函数。

**层间点击**（可覆盖）。框架提供默认的 `adb.tap()` 实现，子类可覆盖加入防检测策略。

```
_do_tap(x, y, *, jitter_x, jitter_y):
    adb.tap(x, y)          ← 默认实现（框架层）
```

| 参数 | 说明 |
|------|------|
| `click_x`, `click_y` | 点击坐标 |
| `jitter_x`, `jitter_y` | 抖动范围（px），由调用方按场景传入。默认 0，子类内部策略自由替换 |

**设计意图**：层间跳转的 tap 由框架提供统一入口，防风控策略（如 `mumdad.click_xonly` 的 x 轴随机抖动）通过子类覆盖注入，不污染框架核心逻辑。

```python
# 框架默认（base.py）
def _do_tap(self, adb, click_x, click_y, jitter_x=0, jitter_y=0):
    adb.tap(click_x, click_y)

# 业务覆盖（collector_layer_model.py）
def _do_tap(self, adb, click_x, click_y, jitter_x=0, jitter_y=0):
    adb.click_xonly(click_x, click_y, jitter_x=jitter_x, jitter_y=jitter_y)
```

调用约定：handler 和 `_tap_to_layer` 通过 `_do_tap` 执行层间点击，`jitter` 参数由调用方按场景传入（如 L1→L2 宽抖动 20px），框架不预设任何防风控行为。

---

### §4.6 `_tap_to_layer(adb, scale_w, click_x, click_y, target_layer, *, jitter_x=0, jitter_y=0, max_attempts=3, max_wait_s=8.0) → bool`（v0.5.5）

**tap + poll 闭环**。.. versionadded:: 0.5.5 从 collector 业务层上提到框架层。

点击后轮询直到到达目标层，失败重试最多 ``max_attempts`` 次（重试间隔 1s）。

```
_tap_to_layer(x, y, target, *, jitter_x, jitter_y):
    for attempt in range(max_attempts):
        _do_tap(x, y, jitter_x=jitter_x, jitter_y=jitter_y)   ← 子类覆盖防检测
        if poll_until_target_layer(target, max_wait_s=max_wait_s):
            return True
        sleep(1.0)   ← 重试前等待
    return False
```

| 参数 | 说明 |
|------|------|
| `click_x`, `click_y` | 点击坐标 |
| `target_layer` | 目标层级 key（如 ``"L2"`` / ``"L3"``） |
| `jitter_x`, `jitter_y` | 抖动范围，透传给 `_do_tap` |
| `max_attempts` | 最大重试次数（默认 3） |
| `max_wait_s` | 单次 poll 超时（默认 8s） |

**设计意图**：对调用方屏蔽 tap + poll + retry 细节，只需提供坐标和目标层即可。子类覆盖 `_do_tap` 后自动继承防检测能力，不会出现"tap 完忘 poll" 的 bug。

.. note::
    框架同时提供 `_tap_to_layer`（tap + poll）和 `back_one`（KEYCODE_BACK + poll），
    前进和后退路径统一使用 ``poll_until_target_layer`` 验证到达目标层。

---

## §5. 完整示例

```
for group in scan_groups():
    _call_on_layer("L1") → _on_L1 → _tap_to_layer → 校验 L2
    for card in detect_cards():
        _call_on_layer("L2") → _on_L2 → _tap_to_layer → 校验 L3
        capture_note()
        back_one()                     → _on_L2(quick=False) 恢复
    back_one()                         → _on_L1(quick=False) 恢复
```

### BACK 失败恢复链

```
L3 → back_one() 失灵 → L0
  → back_recover("L2"):
    cold-start → L1
    _call_on_layer("L1", quick=True) → _on_L1(quick=True) → _tap_to_layer → L2
    _on_L2(quick=False) → 正常恢复
```

---

## §6. 职责边界

| | 框架 | Task |
|---|------|------|
| `detect()` | 定义签名 | 覆盖实现 |
| `detect_layer()` | 定义签名（v0.5.0） | 覆盖实现 |
| `detect_detail()` | 定义签名 + 默认实现 | 可选覆盖 |
| `DetectResult` | 定义类型 | — |
| `_on_Lx()` | 按层索引调用 | 覆盖：业务 + 点击 |
| `init()` | 调用钩子 | 可选覆盖 |
| `_recover_to_page()` | 调用钩子 | 可选覆盖 |
| `back_one()` | KEYCODE_BACK + poll_until_target_layer + back_recover 兜底（v0.5.5） | — |
| `home_one()` | KEYCODE_HOME + detect | — |
| `poll_until_target_layer()` | 自适应轮询检测目标层 | — |
| `back_recover()` | 冷启动 + 快速前进 + 子页面恢复 | — |
| `home_one(adb)` | 模块级函数 — KEYCODE_HOME + sleep | — |
| `_do_tap(x,y,jitter_x,jitter_y)` | 层间点击（默认 ``adb.tap``） | 可选覆盖：防检测策略 |
| `_tap_to_layer(x,y,target)` | tap + poll 重试闭环（v0.5.5 上提） | — |

### §6.1 有限状态与无限状态的分离（借鉴 Automat）

| 有限状态（状态图可见） | 无限状态（核心数据，不进入状态图） |
|---|---|
| L0 / L1 / L2 / L3 | 当前采集到第几个群 |
| | 已采集多少条笔记 |
| | 用户滚动位置 |
| | 重试计数 |

**设计决策**：`layers` 列表只描述页面层级结构（有限状态）。Handler 内部维护的业务状态（如 `_pick_first_unread` 的选行逻辑）属于"无限状态"，**不在层模型中体现**，由 Task 子类自行管理。这与 [Automat](https://github.com/glyph/automat) 的"Core Data"概念一脉相承——将无界数据从状态枚举中剥离，保持状态图简洁。

### §6.2 守卫（Guard）与验证器（Validator）语义分离（借鉴 python-statemachine）

| 概念 | 框架中的对应 | 说明 |
|------|------------|------|
| **guard**（守卫） | handler 自身的上下文校验 | 条件检查：当前层是否允许前进？（handler 返回 `None` 即拒绝） |
| **validator**（验证器） | `_tap_to_layer` 内部的 `poll_until_target_layer()` | 后置校验：页面是否真的跳到了目标层？ |
| **guard** | `back_one` 步骤 1 的 `detect()` | 条件检查：当前层是否允许后退？ |
| **validator** | `back_one` 内部的 `poll_until_target_layer()` | 后置校验：BACK 后是否真的到达了上一层？ |

guard 失败 → 拒绝操作（静默或日志告警）；validator 失败 → 触发恢复链（`back_recover`）。

---

## §7. 可观测性 —— LayerListener（借鉴 python-statemachine）

### §7.1 设计动机

借鉴 [`python-statemachine` 的 Listener 模式](https://github.com/fgmacedo/python-statemachine)，框架提供观察者接口，解耦状态变更与外部副作用（日志、指标、告警、截图收集）。

框架本身的 `LOG.debug/warning` 不面向程序消费；`LayerListener` 提供结构化事件回调。

### §7.2 接口

```python
class LayerListener(Protocol):
    def on_transition(self, from_layer: str, to_layer: str, method: str) -> None: ...
    def on_timeout(self, from_layer: str, target_layer: str, elapsed_s: float) -> None: ...
    def on_recovery(self, target_layer: str, ok: bool) -> None: ...
```

### §7.3 事件触发时机

| 事件 | 触发方法 | 触发时机 |
|------|---------|---------|
| `on_transition(cur, next, "back_one")` | `back_one` | KEYCODE_BACK 后检测到层变更 |
| `on_transition(cur, next, "home_one")` | `home_one` | KEYCODE_HOME 后检测到层变更 |
| `on_recovery(target, ok)` | `back_recover` | 恢复流程结束后（ok=True 成功 / ok=False 失败） |

### §7.4 使用示例

```python
from layernav_android import LayerListener

class MetricsListener:
    def on_transition(self, from_layer, to_layer, method):
        statsd.increment(f"layer.{method}", tags={"from": from_layer, "to": to_layer})

    def on_timeout(self, from_layer, target_layer, elapsed_s):
        LOG.error("layer transition timeout: %s→%s after %.1fs", from_layer, target_layer, elapsed_s)

    def on_recovery(self, target_layer, ok):
        if not ok:
            alert.send(f"back_recover to {target_layer} FAILED")

model.add_listener(MetricsListener())
```

### §7.5 状态图

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
        HOME → cold-start → _call_on_layer 循环 → target
```

> 注：`detect()` 返回 `None` 时，自动触发 `back_recover` 恢复链。

---

## §8. 通用冷启动 —— `cold_start_app_from_launcher`

### §8.1 设计动机

原 `BaseLayerModel._cold_start` 各子类自行实现（`am start -n` 或 `monkey`），缺乏统一的 Dock 图标兜底、session tab 点击、force-stop 控制等能力。抽取为独立通版函数，供所有 APP 模型和外部调用方使用。


说明：由于异常情况强制冷启动微信时，只能启动默认的主程序，所以安卓机能双开微信最好也只开一个（或者晚上切另一个账号）！！记录到文档


### §8.2 函数签名

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
    allow_reboot: bool = False,
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
| `allow_reboot` | 三条启动路径全部失败后是否执行 `adb reboot` 终极兜底（默认关闭） | `False` |

屏幕尺寸始终通过 `adb shell wm size` 自动获取，调用方无需传入。另有 **`APP_DEFAULTS`** 常量定义各 APP 的默认 `M`/`N`：

```python
APP_DEFAULTS = {
    "wechat": {"M": 4, "N": 3},
    "xhs":    {"M": 4, "N": 1},
}
```

### §8.3 冷启动路径

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

### §8.4 Dock 坐标公式

```python
def dock_app_icon_coords(
    screen_w, screen_h, scale_w, *, app_name="wechat", M=4, N=None,
) -> tuple[int, int]:
    """Dock M 等分，APP 在第 N 格（1‑indexed），返回该槽位近似中心。"""
    if N is None:
        N = {"wechat": 3, "xhs": 1}.get(app_name, 1)
    pad_x = max(12, screen_w // 8)                      # 左右留白，避免边缘误触
    dx = int(round(screen_w * (N - 0.5) / M))
    dx = max(pad_x, min(screen_w - pad_x, dx))          # 夹持在安全范围内
    dy = screen_h - max(48, int(round(52 * max(scale_w, 1e-6))))  # scale_w 防除零
    return dx, dy
```

### §8.5 使用示例

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

### §8.6 设计说明

- **使用普通 ADB tap**（非防风控触控）：冷启动是系统级操作（桌面 Dock 图标点击），不涉及 APP 内反爬检测，使用 `AdbProtocol.tap()` 即可，方便所有系统集成。
- **屏幕尺寸自动获取**：`screen_w` / `screen_h` / `scale_w` 不再作为参数，函数内部通过 `adb shell wm size` 自动获取。调用方只需传 `app_name` / `M` / `N` 三个核心参数即可计算出 Dock 图标坐标。
- **四条冷启动路径**：monkey（主路径）→ am start Intent（定制 ROM 备选）→ Dock 图标点击（兜底，含 0.5s 预等待 + 最多 2 次重试）→ adb reboot（终极兜底，`allow_reboot=True` 且前 3 条均失败后触发）。覆盖大部分设备和 ROM。
- `force_stop` 通过 `adb._run(["shell", "am", "force-stop", package])` 实现，无需额外接口。
- 返回值 `bool` 表示 `foreground_package() == package`，调用方需自行判断是否到达目标层级。

### §8.7 adb reboot 兜底

当 `allow_reboot=True` 且三条启动路径全部失败时，执行系统重启作为终极恢复：

1. 调用 `adb._run(["reboot"])` 重启设备
2. 每 3 s 轮询 `adb shell echo ok` 等待设备恢复连接（最长 90 s）
3. 每 3 s 轮询 `adb shell getprop sys.boot_completed` 等待系统启动完成（最长 60 s）
4. 重新执行 monkey 启动 + session tab 点击

> ⚠️ 重启总耗时 60–120 s，且要求设备无需手动解锁（无 PIN / 图案锁）。默认关闭。仅适用于无人值守的 7×24 自动化场景。

---
