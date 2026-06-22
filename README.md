# LayerNav_Android

> A stable page layer navigation framework for Android ADB automation.
> 基于 ADB 的安卓页面层级导航框架，主打**强校验、自动容错、故障恢复**，适用于 APP 数据采集、移动端 RPA、UI 自动化测试场景。
>
> **Python 3.12+** · 轻量依赖（仅 loguru） · MIT License

[![PyPI](https://badge.fury.io/py/layernav_android.svg)](https://badge.fury.io/py/layernav_android)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/yuyidream/layernav_android/actions/workflows/ci.yml/badge.svg)](https://github.com/yuyidream/layernav_android/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/yuyidream/layernav_android/branch/main/graph/badge.svg)](https://codecov.io/gh/yuyidream/layernav_android)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 一、项目介绍

市面上主流 ADB / UI 自动化库仅提供点击、滑动、返回等基础原子能力，**缺少页面状态校验、层级管理、异常恢复**。本框架基于 **L0~Ln 页面层级模型** 设计，将桌面、APP主页、内容页、详情页抽象为标准化层级，内置「动作执行 → 截屏校验 → 自动重试 → 冷启动恢复」全链路能力。

### 核心定位

- 导航调度 + 容错引擎
- 框架负责：层级检测、跳转校验、后退/前进/恢复、**子页面导航**
- 业务脚本负责：控件点击、数据采集、业务逻辑

---

## 二、核心特性

✅ **标准化层级模型**
统一抽象 `L0(手机桌面) / L1(APP主页) / L2(内容页) / L3(详情页) ... Ln`，一套模型适配绝大多数 APP。

✅ **闭环跳转校验**
执行操作后自动截屏检测页面，**拒绝盲操作**，跳转失败即时感知。guard（前置校验）+ validator（后置轮询）语义分离。

✅ **目标感知检测（v0.5.0）**
`detect()` 负责"我在哪"（返回 `str | None`），`detect_layer(target)` 负责"到达目标了吗"（返回 `bool`）。导航 API 全部使用 `detect_layer` 做目标验证，`detect()` 无法判定时（返回 `None`）自动回退到 `back_recover`。

✅ **同层多页面导航（v0.3.0）**
`detect_detail()` 一次截图返回层级 + 子页面；`back_recover` 支持 `target_page` 参数，恢复后自动精确定位到指定子页面。

✅ **完整导航原子 API**
内置 `detect / detect_layer / _do_tap / _tap_to_layer / back_one / back_recover` 原子操作，一行代码完成跨层级跳转（v0.5.5：`_tap_to_layer` 从业务层上提为框架 API，tap + poll 闭环）。

✅ **防检测点击（v0.5.3）**
`_do_tap` 提供可覆盖的层间点击入口，子类注入防风控策略（如 `mumdad.click_xonly` 的 x 轴随机抖动），框架只提供默认 `adb.tap`，不预设防护行为。

✅ **故障自动恢复（v0.5.5 强化）**
`detect()` 无法判定层级时 → `back_one()` 用 `poll_until_target_layer` 验证 BACK 到达上一层，重试 3 次失败后走 `back_recover`（HOME → 冷启动 → 前进恢复）。返回键失效、页面卡死、意外退回桌面时同样自动恢复。

✅ **Quick 快速模式**
专为恢复场景设计，handlers 收到 `quick=True` 时可精简业务逻辑（如选第一个未读），提升导航速度。

✅ **可观测监听器**
内置 `LayerListener` 观察者接口，零侵入监控层切换、超时、恢复事件，方便接入指标采集与告警。

✅ **解耦设计**
- 页面检测 `detect` 接口可自由接入：OCR / 图像匹配 / UI 控件解析
- ADB 客户端通过 `AdbProtocol` 完全抽象，原生 ADB / 风控加固 ADB 均可无缝接入
- 分层 Handler 机制（`_on_Lx`），业务代码与框架逻辑完全隔离

---

## 三、架构设计

### 1. 层级模型

```
L0     手机主屏幕（非 APP 前台）
L1     APP 主界面
L2     二级内容页
L3     三级详情页
...
Ln     最深业务层级
```

#### 同层多页面支持（v0.3.0）

同一个层级可包含多个子页面（如 L1 的会话列表/通讯录/发现/我）。`LayerDef` 通过 `page_name` 和 `detection_extra` 字段声明：

```python
layers = [
    LayerDef("L1", "main_list", "微信主界面", "is_main_list_chrome()",
             page_name="chat_list",
             detection_extra="子页面: chat_list/contacts/discover/profile"),
]
```

| 能力 | 方法 | 说明 |
|------|------|------|
| 子页面检测 | `detect_detail() → DetectResult` | 返回 `(layer_key, page_name)` |
| Tab 切换（L1） | `WeChatGroupLayerModel._recover_to_page` | 框架自动计算底部 tab 坐标并点击 |
| 校验型（L2/L3） | `detect_detail` 验证 | 无需额外操作，校验 page_name 即可 |

### 2. 职责划分

| 模块 | 框架能力 | Task 能力 |
|------|---------|----------|
| 状态检测 | 调用 `detect()` / `detect_detail()`、校验结果 | 实现截图/识别逻辑 |
| 页面动作 | 流程调度、等待、重试 | 实现 `_on_Lx` 点击/滑动等业务动作 |
| 导航逻辑 | `back_one` + `back_recover` / 恢复 | 无 |
| 子页面导航 | `detect_detail` / `_recover_to_page` | 无（框架提供 `target_page` 路由） |
| 点击 | 框架提供 `_tap_to_layer`（tap + poll 闭环） | handler 可选直接 `adb.tap()` |

### 3. 核心流程

```
1. detect() 实时识别当前页面层级（无法识别时返回 None）
2. 调用对应层级 _on_Lx handler 执行业务操作
3. detect_layer() 二次校验页面是否到达目标层级（截屏 + 轮询）
4. detect() 返回 None 或跳转失败 → 直接 back_recover 冷启动恢复
```

---

## 四、快速上手

### 1. 安装

**方式一：pip 安装（推荐）**

```bash
pip install layernav_android
```

如需使用 WeChat contrib 模块，需额外安装 `opencv-python` 和 `numpy`：

```bash
pip install opencv-python numpy
```

**方式二：从源码安装**

```bash
git clone https://github.com/yuyidream/layernav_android.git
cd layernav_android
pip install -e .
```

如需运行测试：

```bash
pip install -e ".[dev]"
pytest tests/ -v --tb=short
```

### 2. 基础使用

继承 `BaseLayerModel`，实现层级检测与页面处理器，即可使用全套导航能力：

```python
from layernav_android import BaseLayerModel, LayerDef

class DemoAppModel(BaseLayerModel):
    layers = [
        LayerDef(key="L0", name="desktop",   label_cn="手机桌面", detection="截屏识别桌面图标"),
        LayerDef(key="L1", name="app_home",  label_cn="APP 主页", detection="OCR 识别主页文字",
                 page_name="home", detection_extra="子页面: home/search"),
        LayerDef(key="L2", name="content",   label_cn="内容列表页", detection="图像特征匹配"),
        LayerDef(key="L3", name="detail",    label_cn="详情页",   detection="模板匹配"),
    ]

    def detect(self, adb, scale_w: float) -> str | None:
        screenshot = adb.screencap()
        if is_desktop(screenshot):
            return "L0"
        elif is_app_home(screenshot):
            return "L1"
        elif is_content_list(screenshot):
            return "L2"
        elif is_detail(screenshot):
            return "L3"
        return None  # 无法判定 → 框架走 back_recover

    def detect_layer(self, adb, scale_w: float, layer: str) -> bool:
        screenshot = adb.screencap()
        if layer == "L0":
            return is_desktop(screenshot)
        elif layer == "L1":
            return is_app_home(screenshot) and not is_content_list(screenshot)
        elif layer == "L2":
            return is_content_list(screenshot)
        elif layer == "L3":
            return is_detail(screenshot)
        return False

    def _on_L0(self, adb, scale_w, *, quick=False):
        self._cold_start(adb, "L1", scale_w)
        return "L1"

    def _on_L1(self, adb, scale_w, *, quick=False) -> str | None:
        if quick:
            row = self._pick_first_row(adb, scale_w)
        else:
            row = self._scan_and_select(adb, scale_w)
        if row is None:
            return None
        adb.tap(row.x, row.y)
        return "L2"

    def _on_L2(self, adb, scale_w, *, quick=False) -> str | None:
        item = self._pick_item(adb, scale_w, quick=quick)
        if item is None:
            return None
        adb.tap(item.x, item.y)
        return "L3"

    def _on_L3(self, adb, scale_w, *, quick=False) -> str | None:
        return None  # 最深层，不再前进


# 执行导航流程
model = DemoAppModel()
adb = get_adb_client()

# 检测层级 + 子页面
dr = model.detect_detail(adb, scale_w=1.0)
print(f"当前: {dr.layer_key} / {dr.page_name}")

# 前置：确保已在 L1
# 逐层前进到 L3（中间层 quick 模式）
while not model.detect_layer(adb, scale_w, "L3"):
    cur = model.detect(adb, scale_w)
    model._call_on_layer(cur, adb, scale_w, quick=True)
# 目标层到达，可开始业务操作
# 后退回 L1 的 search 子页面
model.back_one(adb, scale_w=1.0)
```

### 3. 核心 API

**原子操作**

| 方法 | 说明 |
|------|------|
| `detect(adb, scale_w) → str \| None` | 检测当前所在层级（Task 覆盖实现）。无法判定时返回 `None`，框架自动走恢复 |
| `detect_layer(adb, scale_w, layer) → bool` | 目标感知检测：当前屏幕是否匹配指定层级（v0.5.0，Task 覆盖实现） |
| `detect_detail(adb, scale_w) → DetectResult` | 检测层级 + 子页面名称（v0.3.0，默认调用 `detect` + `LayerDef.page_name`） |
| `back_one(adb, scale_w, *, max_retries=3) → str` | 退回到上一层：KEYCODE_BACK + poll_until_target_layer 验证，失败走 `back_recover` 冷启动兜底（v0.5.5） |
| `_tap_to_layer(adb, scale_w, x, y, target, *, jitter_x, jitter_y, max_attempts=3) → bool` | **v0.5.5 新增**：tap + poll 闭环，点击后轮询直到到达目标层。调用方只需提供坐标 + 目标层，内部处理重试 |
| `_do_tap(adb, x, y, *, jitter_x, jitter_y) → None` | 层间点击（默认 ``adb.tap``），子类覆盖加入防检测策略 |
| `back_recover(adb, target, scale_w, *, target_page=None) → bool` | 故障恢复：HOME → 冷启动 → 快速前进 → 子页面（v0.4.3: 冷启动 3 次重试 + `adb reboot` 兜底） |

**`_do_tap` 使用示例**

```python
# 框架默认（base.py）— 普通 ADB tap
def _do_tap(self, adb, click_x, click_y, jitter_x=0, jitter_y=0):
    adb.tap(click_x, click_y)

# 业务覆盖（子类）— mumdad 风控点击
def _do_tap(self, adb, click_x, click_y, jitter_x=0, jitter_y=0):
    adb.click_xonly(click_x, click_y, jitter_x=jitter_x, jitter_y=jitter_y)
```

`jitter_x` / `jitter_y` 由调用方按场景传入（如 L1→L2 宽抖动 20px），子类内部策略自由替换。

**可观测**

```python
from layernav_android import LayerListener

class MetricsListener:
    def on_transition(self, from_layer, to_layer, method):
        print(f"{from_layer} → {to_layer} via {method}")

    def on_timeout(self, from_layer, target_layer, elapsed_s):
        print(f"Timeout {from_layer}→{target_layer} after {elapsed_s:.1f}s")

    def on_recovery(self, target_layer, ok):
        print(f"Recovery to {target_layer}: {'OK' if ok else 'FAILED'}")

model.add_listener(MetricsListener())
```

---

## 五、通用冷启动工具

`cold_start_app_from_launcher` 提供统一的 APP 冷启动能力，支持 monkey 主路径 + Dock 图标兜底 + session tab 点击：

```python
from layernav_android.cold_start import cold_start_app_from_launcher

# 微信 — 最简调用（尺寸自动获取）
ok = cold_start_app_from_launcher(
    adb, "com.tencent.mm",
    app_name="wechat", M=4, N=3,
)

# 微信 — 含 session tab
ok = cold_start_app_from_launcher(
    adb, "com.tencent.mm",
    app_name="wechat", M=4, N=3,
    session_tab_x=108, session_tab_y=2192,
)

# 小红书
ok = cold_start_app_from_launcher(
    adb, "com.xingin.xhs",
    app_name="xhs", M=4, N=1,
)
```

**关键设计**：使用普通 ADB tap（非防风控触控），因为是系统级操作（桌面 Dock 图标点击），不涉及 APP 内反爬检测，方便所有系统集成。

**最后一搏 — adb reboot 兜底**（`allow_reboot=True`，默认关闭）：

当 monkey、am start、Dock icon tap 三条路径全部失败时，可选执行 `adb reboot` 作为终极恢复手段。重启后等待设备上线 + boot 完成，然后重新尝试 monkey 启动。

> ⚠️ 重启耗时 60–120 s，且要求设备无需手动解锁（无 PIN/图案锁）。适用于无人值守的 7×24 自动化。


## 六、适用场景


- **移动端 RPA 自动化** — 加速开发
- **Android UI 自动化测试** — 提升脚本稳定性，减少维护成本
- **APP 流程逆向 / 行为模拟** — 稳定进入深层页面

---

## 七、优势对比

| 能力 | 本框架 | Appium / uiautomator2 / Airtest |
|------|--------|---------------------------------|
| 标准化页面层级 | ✅ 内置模型 | ❌ 无统一抽象 |
| 同层多页面导航 | ✅ `detect_detail` + `_recover_to_page` | ❌ 需手动分支 |
| 操作后页面校验 | ✅ 闭环 guard + validator | ❌ 仅执行动作，不校验结果 |
| 自动后退恢复 | ✅ 3 次重试 + 冷启动兜底 | ❌ 需手动编写重试逻辑 |
| 层级穿越 API | ✅ `back_one` + `back_recover` | ❌ 仅基础点击/返回 |
| 可观测监听器 | ✅ `LayerListener` 事件回调 | ❌ 需自行埋点 |
| ADB 解耦 | ✅ `AdbProtocol` 接口抽象 | ⚠️ 部分耦合 |

---

## 八、拓展建议

- **页面检测能力**：可接入 PaddleOCR / EasyOCR / OpenCV 图像匹配
- **状态机拓展**：可结合 `python-statemachine` 优化状态管理（本框架的 `LayerListener` 即借鉴其设计）
- **多设备并行**：每设备独立 `BaseLayerModel` 实例即可天然支持多设备

---

## 九、目录结构

```
layernav_android/
├── src/layernav_android/
│   ├── __init__.py          # 公开导出
│   ├── _protocol.py         # AdbProtocol 接口
│   ├── base.py              # LayerDef, LayerListener, BaseLayerModel (v0.5.0 + detect_layer)
│   ├── cold_start.py        # 通用冷启动工具（含 adb reboot 兜底）
│   └── contrib/
│       ├── __init__.py
│       ├── wechat.py        # WeChatGroupLayerModel（微信示例）
│       └── xhs.py           # XhsLayerModel（小红书占位）
├── tests/
│   └── test_base.py         # 23 个单元测试
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## 十、参与贡献

欢迎提交 Issue、PR，共建安卓自动化导航生态：

- **Bug 反馈、功能建议** → [Issues](https://github.com/yuyidream/layernav_android/issues)
- **代码优化、新增示例** → Pull Request

---

## 十一、开源协议

本项目基于 [MIT License](LICENSE) 开源，可自由用于个人、商业项目。
