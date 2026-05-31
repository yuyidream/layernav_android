# LayerNav_Android

> A stable page layer navigation framework for Android ADB automation.
> 基于 ADB 的安卓页面层级导航框架，主打**强校验、自动容错、故障恢复**，适用于 APP 数据采集、移动端 RPA、UI 自动化测试场景。
>
> **Python 3.12+** · 零外部依赖 · MIT License

[![PyPI](https://badge.fury.io/py/layernav_android.svg)](https://badge.fury.io/py/layernav_android)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/yuyidream/layernav_android/actions/workflows/ci.yml/badge.svg)](https://github.com/yuyidream/layernav_android/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 一、项目介绍

市面上主流 ADB / UI 自动化库仅提供点击、滑动、返回等基础原子能力，**缺少页面状态校验、层级管理、异常恢复**，脚本极易因页面跳转失败、APP 卡死、返回失灵而中断。

本框架基于 **L0~Ln 页面层级模型** 设计，将手机桌面、APP 主页、内容页、详情页抽象为标准化层级，内置「动作执行 → 截屏校验 → 自动重试 → 冷启动恢复」全链路能力，大幅提升自动化脚本稳定性与开发效率。

### 核心定位

- 不是通用 UI 自动化库，而是 **导航调度 + 容错引擎**
- 框架负责：层级检测、跳转校验、后退/前进/恢复逻辑、异常兜底
- 业务脚本负责：控件点击、数据采集、业务逻辑（职责彻底分离）

---

## 二、核心特性

✅ **标准化层级模型**
统一抽象 `L0(手机桌面) / L1(APP主页) / L2(内容页) / L3(详情页) ... Ln`，一套模型适配绝大多数 APP。

✅ **闭环跳转校验**
执行操作后自动截屏检测页面，**拒绝盲操作**，跳转失败即时感知。guard（前置校验）+ validator（后置轮询）语义分离。

✅ **完整导航原子 API**
内置 `detect / enter_next / back_one / back_recover` 四原子操作 + `advance / back / restore` 三组合操作，一行代码完成跨层级跳转。

✅ **故障自动恢复**
返回键失效、页面卡死、意外退回桌面时，自动执行 `back_recover`：HOME → 冷启动 APP → 快速前进至目标层级 → 正常恢复业务。

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

### 2. 职责划分

| 模块 | 框架能力 | Task 能力 |
|------|---------|----------|
| 状态检测 | 调用 `detect()`、校验结果 | 实现截图/识别逻辑 |
| 页面动作 | 流程调度、等待、重试 | 实现 `_on_Lx` 点击/滑动等业务动作 |
| 导航逻辑 | `advance` / `back` / `restore` / 恢复 | 无 |
| 点击 | **不负责** — 框架不 `tap` | handler 内 `adb.tap()` |

### 3. 核心流程

```
1. detect() 实时识别当前页面层级
2. 调用对应层级 _on_Lx handler 执行业务操作
3. 二次校验页面是否到达目标层级（截屏 + 轮询）
4. 跳转失败 → 自动重试 → 重试失败 → 冷启动恢复
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
pip install layernav_android[wechat]
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
        LayerDef(key="L1", name="app_home",  label_cn="APP 主页", detection="OCR 识别主页文字"),
        LayerDef(key="L2", name="content",   label_cn="内容列表页", detection="图像特征匹配"),
        LayerDef(key="L3", name="detail",    label_cn="详情页",   detection="模板匹配"),
    ]

    def detect(self, adb, scale_w: float) -> str:
        screenshot = adb.screencap()
        if is_desktop(screenshot):
            return "L0"
        elif is_app_home(screenshot):
            return "L1"
        elif is_content_list(screenshot):
            return "L2"
        return "L3"

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

# 智能恢复到 L1
model.restore(adb, target_layer="L1", scale_w=1.0)
# 逐层前进到 L3
model.advance(adb, target_layer="L3", scale_w=1.0)
# 后退回 L1
model.back(adb, to_layer="L1", scale_w=1.0)
```

### 3. 核心 API

**原子操作**

| 方法 | 说明 |
|------|------|
| `detect(adb, scale_w) → str` | 检测当前所在层级（Task 覆盖实现） |
| `enter_next(adb, scale_w, *, quick, max_wait_s) → bool` | 单步进入下一层 ← guard + validator + 轮询 |
| `back_one(adb, scale_w) → str` | 单步 `KEYCODE_BACK`，返回新层级 |
| `back_recover(adb, target, scale_w) → bool` | 故障恢复：HOME → 冷启动 → 快速前进 → 正常恢复 |

**组合操作**

| 方法 | 说明 |
|------|------|
| `back(adb, to_layer, scale_w) → bool` | 逐层后退至目标（3 次重试 → 恢复） |
| `advance(adb, target, scale_w, *, quick, max_wait_s) → bool` | 逐层前进至目标（目标层 always `quick=False`） |
| `restore(adb, target, scale_w) → bool` | 智能判断方向，从任意位置恢复至目标 |

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

## 五、适用场景

- **APP 合规数据采集** — 内容抓取、列表遍历、批量浏览
- **移动端 RPA 自动化** — 账号矩阵、批量操作、运营工具
- **Android UI 自动化测试** — 提升脚本稳定性，减少维护成本
- **APP 流程逆向 / 行为模拟** — 稳定进入深层页面

---

## 六、优势对比

| 能力 | 本框架 | Appium / uiautomator2 / Airtest |
|------|--------|---------------------------------|
| 标准化页面层级 | ✅ 内置模型 | ❌ 无统一抽象 |
| 操作后页面校验 | ✅ 闭环 guard + validator | ❌ 仅执行动作，不校验结果 |
| 自动后退恢复 | ✅ 3 次重试 + 冷启动兜底 | ❌ 需手动编写重试逻辑 |
| 层级穿越 API | ✅ `advance` / `back` / `restore` | ❌ 仅基础点击/返回 |
| 可观测监听器 | ✅ `LayerListener` 事件回调 | ❌ 需自行埋点 |
| ADB 解耦 | ✅ `AdbProtocol` 接口抽象 | ⚠️ 部分耦合 |

---

## 七、拓展建议

- **页面检测能力**：可接入 PaddleOCR / EasyOCR / OpenCV 图像匹配
- **ADB 加固**：对接自定义风控 ADB 客户端，模拟真人操作轨迹
- **状态机拓展**：可结合 `python-statemachine` 优化状态管理（本框架的 `LayerListener` 即借鉴其设计）
- **多设备并行**：每设备独立 `BaseLayerModel` 实例即可天然支持多设备

---

## 八、目录结构

```
layernav_android/
├── src/layernav_android/
│   ├── __init__.py          # 公开导出
│   ├── _protocol.py         # AdbProtocol 接口
│   ├── base.py              # LayerDef, LayerListener, BaseLayerModel
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

## 九、参与贡献

欢迎提交 Issue、PR，共建安卓自动化导航生态：

- **Bug 反馈、功能建议** → [Issues](https://github.com/yuyidream/layernav_android/issues)
- **代码优化、新增示例** → Pull Request

---

## 十、开源协议

本项目基于 [MIT License](LICENSE) 开源，可自由用于个人、商业项目。
