# layernav_android

Multi-layer task-stack navigation framework for Android ADB automation.

Define N layers (e.g. L0=home → L1=app → L2=detail → L3=sub),
register per-layer handlers, and let the framework handle BACK recovery,
cold-start, and cross-layer verification.

## Install

```bash
pip install layernav_android
```

For WeChat contrib:

```bash
pip install layernav_android[wechat]
```

## Quick start

```python
from layernav_android import BaseLayerModel, LayerDef

class MyAppModel(BaseLayerModel):
    layers = [
        LayerDef("L0", "home",    "手机主屏幕",     "foreground check"),
        LayerDef("L1", "main",    "APP 主界面",      "template A"),
        LayerDef("L2", "detail",  "内容详情页",      "template B"),
        LayerDef("L3", "sub",     "子页面",          "template C"),
    ]

    def detect(self, adb, scale_w) -> str:
        ...  # implement screenshot-based detection

    def _on_L0(self, adb, scale_w, *, quick=False):
        self._cold_start(adb, "L1", scale_w)
        return "L1"

    def _on_L1(self, adb, scale_w, *, quick=False):
        ...  # business logic + adb.tap()
        return "L2"

    def _on_L2(self, adb, scale_w, *, quick=False):
        ...  # business logic + adb.tap()
        return "L3"

    def _on_L3(self, adb, scale_w, *, quick=False):
        return None  # deepest layer, no further advance
```

## API

### Atomic

| Method | Description |
|--------|-------------|
| `detect(adb, scale_w) → str` | Screenshot-based layer detection |
| `enter_next(adb, scale_w, *, quick, max_wait_s) → bool` | Advance one layer with polling verification |
| `back_one(adb, scale_w) → str` | Send KEYCODE_BACK, return new layer |
| `back_recover(adb, target, scale_w) → bool` | HOME → cold-start → fast-forward → normal resume |

### Combined

| Method | Description |
|--------|-------------|
| `back(adb, to_layer, scale_w) → bool` | Retreat to target via repeated BACK |
| `advance(adb, target, scale_w, *, quick) → bool` | Layer-by-layer forward |
| `restore(adb, target, scale_w) → bool` | Restore to target from any position |

### Observability

```python
from layernav_android import LayerListener

class MetricsListener:
    def on_transition(self, from_layer, to_layer, method):
        print(f"{from_layer} → {to_layer} via {method}")

    def on_timeout(self, from_layer, target_layer, elapsed_s):
        print(f"Timeout: {from_layer} → {target_layer}")

    def on_recovery(self, target_layer, ok):
        print(f"Recovery {'OK' if ok else 'FAILED'}: {target_layer}")

model.add_listener(MetricsListener())
```

## License

MIT
