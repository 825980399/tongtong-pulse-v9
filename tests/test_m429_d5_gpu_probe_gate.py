# -*- coding: utf-8 -*-
"""第429批 刀5：`device_router` 的 GPU 探测与 `GPU_PROBE_ENABLED` 对齐。

被守护的三条：
  1) 开关关闭时 `get_capability()` **不 import torch**（CUDA 栈不加载）；
  2) 开关关闭时仍给出可用的兜底画像（`has_gpu=False` + `_FALLBACK` 数值），
     而不是抛异常或返回半成品 —— `should_use_gpu` 要能照常给出「走 CPU」；
  3) 开关打开时行为与改造前一致（这里只验**判定分支**：不复用关闭态的缓存）。

★为什么这条重要（实测 2026-11-02，pid 33156）：
  关闭开关只拦住了 `GPUCore.probe_gpu()`，`device_router` 这条路径不受管 ⇒
  运行约 1 小时（首次向量检索）才 `import torch`，把 CUDA 栈装进进程并常驻：
  `image_mb` 566.6(15:03) → 4322.2(16:00) → 4322.2(18:00)，
  RSS 3901 → 6502 → 7093MB，而 `heap_mb` 反而 3334.8 → 2176.6（GC 有效，非泄漏）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus import device_router as dr


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


def _reset_router():
    """把路由器的「已探测」状态清掉，让下次调用重新走分支。"""
    with dr._ROUTER_LOCK:
        dr._STATE["cap"] = None
        dr._STATE["cap_probed"] = False
        dr._STATE.pop("m429_skip_logged", None)


class TestM429D5GpuProbeGate(unittest.TestCase):
    def setUp(self):
        _reset_router()

    def tearDown(self):
        _reset_router()

    def test_01_disabled_does_not_import_torch(self):
        """★核心：开关关闭 ⇒ 不 import torch（CUDA 栈不加载）。"""
        sys.modules.pop("torch", None)
        with _CfgSwitch(GPU_PROBE_ENABLED=False):
            cap = dr.get_capability()
        self.assertTrue(cap.get("probe_skipped"), "应标记 probe_skipped=True")
        self.assertEqual(cap.get("probe_error"), "GPU_PROBE_ENABLED=False")
        self.assertNotIn("torch", sys.modules,
                         "开关关闭时不该把 torch 装进进程（那是 3.7GB 的库镜像）")

    def test_02_disabled_still_usable_fallback(self):
        """关闭态必须仍能出决策 —— 走兜底数值，判「无可用 GPU」。"""
        with _CfgSwitch(GPU_PROBE_ENABLED=False):
            cap = dr.get_capability()
            self.assertFalse(cap["has_gpu"])
            self.assertGreater(cap["cpu_flops"], 0.0)
            self.assertGreater(cap["cpu_bytes_per_s"], 0.0)
            use_gpu, why, _g, _c = dr.should_use_gpu("vector_search", 10000, 256)
        self.assertFalse(use_gpu, "无 GPU 时不该选 GPU")
        self.assertTrue(why, "必须给出理由，不能空字符串")

    def test_03_enabled_branch_not_short_circuited(self):
        """开关打开时不走短路分支（只看标记：真实探测由 force=True 覆盖）。"""
        with _CfgSwitch(GPU_PROBE_ENABLED=True):
            self.assertTrue(dr._m429_gpu_probe_enabled(),
                            "开关为 True 时判定函数应为 True")
            # 关闭态下打的标记不该残留到打开态的判定上
            self.assertFalse(dr._STATE.get("m429_skip_logged", False))


if __name__ == "__main__":
    unittest.main()
