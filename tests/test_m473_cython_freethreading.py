"""473 刀4：free-threading 下 6 个 Cython 热路径模块的并发正确性回归。

为什么需要这个测试
------------------
473 批给 6 个 `.pyx` 补了 `# cython: freethreading_compatible=True`，
使它们在 Python 3.14t（free-threaded，GIL 默认关闭）下**不会把 GIL 重新启回来**。
代价是这些模块从此在多线程里**真并发**执行——原本被 GIL 顺带保护的东西不再受保护。

这些模块的设计前提是「纯数值、不触碰 Python 对象、nogil 块内只用 C 类型」
（各 `.pyx` 头部注释自述），因此本测试要证明的是：
**同一输入在 8 线程 × 200 次并发下，结果与单线程基准逐位相同，且不抛异常。**

三层判据
--------
1. 静态：每个 `.pyx` 首行必须带指令（防止后续有人删掉，静默让 GIL 复活）；
2. 行为：6 个模块各自的并发结果 == 单线程基准（浮点比较用 repr 精确相等）；
3. 环境：free-threaded 构建下，导入全部 6 个模块后 `sys._is_gil_enabled()` 仍为 False。

在带 GIL 的解释器（如 3.12）上，第 3 条自动跳过，前两条照常执行。
"""

from __future__ import annotations

import concurrent.futures as _cf
import importlib
import sys
import unittest
from pathlib import Path

from nucleus._silent_except import silent_exc

_ROOT = Path(__file__).resolve().parent.parent

#: (模块路径, pyx 相对路径)
_MODULES: list[tuple[str, str]] = [
    ("nucleus.pulse._frequency_codec_cy", "nucleus/pulse/_frequency_codec_cy.pyx"),
    ("nucleus.synapsys._resonance_cy", "nucleus/synapsys/_resonance_cy.pyx"),
    ("nucleus.field._oscillon_cy", "nucleus/field/_oscillon_cy.pyx"),
    ("nucleus.gpu._cosine_cpu_cy", "nucleus/gpu/_cosine_cpu_cy.pyx"),
    ("nucleus.reasoning._inner_world_math_cy", "nucleus/reasoning/_inner_world_math_cy.pyx"),
    ("nucleus.reasoning._topk_retrieve_cy", "nucleus/reasoning/_topk_retrieve_cy.pyx"),
]

_THREADS = 8
_ROUNDS = 200


def _try_import(name: str):
    """导入扩展模块；未编译（纯 Python 回退环境）时返回 None，由用例跳过。"""
    try:
        return importlib.import_module(name)
    except ImportError as _e:
        silent_exc(_e, where="test_m473_cython_freethreading::_try_import " + name, level="debug")
        return None


def _call(module, fn: str, *args):
    return getattr(module, fn)(*args)


class TestM473FreeThreading(unittest.TestCase):
    """并发正确性 + 指令静态守恒 + GIL 状态。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._mods = {name: _try_import(name) for name, _ in _MODULES}
        cls._missing = [n for n, m in cls._mods.items() if m is None]

    # ---------- 判据 1：静态守恒 ----------

    def test_01_all_pyx_declare_freethreading(self):
        """6 个 .pyx 首行必须是 freethreading 指令，否则 free-threaded 下会重开 GIL。"""
        _bad = []
        for _, rel in _MODULES:
            _p = _ROOT / rel
            self.assertTrue(_p.exists(), f"{rel} 不存在")
            _first = _p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            if "freethreading_compatible" not in _first:
                _bad.append(f"{rel}: {_first!r}")
        self.assertEqual(_bad, [], f"以下 .pyx 缺 free-threading 指令: {_bad}")

    def test_02_directive_not_shadowed_by_rebuild(self):
        """指令必须在**首个注释块**（第 1 行），放到代码之后 Cython 不认。"""
        for _, rel in _MODULES:
            _lines = (_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
            self.assertIn("freethreading_compatible", _lines[0],
                          f"{rel} 的指令不在第 1 行，Cython 可能忽略")

    # ---------- 判据 2：并发结果 == 单线程基准 ----------

    def _concurrent_matches_reference(self, fn: str, *args):
        """同一调用在 8 线程 × 200 次下，全部结果与单线程基准 repr 相同。"""
        _mod = self._mods.get(self._current_module)
        if _mod is None:
            self.skipTest(f"{self._current_module} 未编译（纯 Python 回退环境）")

        _ref = repr(_call(_mod, fn, *args))

        def _worker(_i: int) -> str:
            return repr(_call(_mod, fn, *args))

        _outs: list[str] = []
        with _cf.ThreadPoolExecutor(max_workers=_THREADS) as _ex:
            _futs = [_ex.submit(_worker, i) for i in range(_THREADS * _ROUNDS)]
            for _f in _futs:
                _outs.append(_f.result())

        _diff = [o for o in _outs if o != _ref]
        self.assertEqual(
            _diff[:3], [],
            f"{self._current_module}.{fn} 并发结果与基准不一致（{len(_diff)}/{len(_outs)} 条）",
        )

    def test_03_frequency_codec_concurrent(self):
        self._current_module = "nucleus.pulse._frequency_codec_cy"
        self._concurrent_matches_reference("encode_cy", "测试关键词")
        self._concurrent_matches_reference("_md5_int", b"abc")

    def test_04_oscillon_concurrent(self):
        self._current_module = "nucleus.field._oscillon_cy"
        self._concurrent_matches_reference("calculate_resonance_cy", 1.0, 1.0, 0.5)
        self._concurrent_matches_reference("try_lock_frequency_cy", 1.0, 1.0, 0.1)

    def test_05_cosine_concurrent(self):
        self._current_module = "nucleus.gpu._cosine_cpu_cy"
        self._concurrent_matches_reference(
            "batch_cosine_cy", [1.0, 0.0], [[1.0, 0.0], [0.0, 1.0], [3.0, 4.0]])

    def test_06_resonance_concurrent(self):
        self._current_module = "nucleus.synapsys._resonance_cy"
        self._concurrent_matches_reference("calc_memory_dim_cy", 1.0, 1.0, 0.5, 3)
        self._concurrent_matches_reference("calc_time_dim_cy", 10.0)
        self._concurrent_matches_reference("calc_state_dim_cy", 2)

    def test_07_inner_world_math_concurrent(self):
        self._current_module = "nucleus.reasoning._inner_world_math_cy"
        self._concurrent_matches_reference("cosine_similarity_cy", [1.0, 2.0], [1.0, 2.0])
        self._concurrent_matches_reference("calc_trust_boost", 0.5, 0.1)
        self._concurrent_matches_reference("normalize_scores", [1.0, 2.0, 3.0])

    def test_08_topk_concurrent(self):
        self._current_module = "nucleus.reasoning._topk_retrieve_cy"
        self._concurrent_matches_reference(
            "topk_retrieve_cy", [1.0, 0.0], [[1.0, 0.0], [0.0, 1.0], [3.0, 4.0]], 2)

    # ---------- 判据 3：GIL 仍关闭 ----------

    def test_09_gil_stays_disabled_on_free_threaded_build(self):
        """free-threaded 构建下，导入全部 6 个模块后 GIL 必须仍为关闭。"""
        if not hasattr(sys, "_is_gil_enabled"):
            self.skipTest("非 free-threaded 构建，无 _is_gil_enabled")
        if self._missing:
            self.skipTest(f"扩展未编译: {self._missing}")
        self.assertFalse(
            sys._is_gil_enabled(),
            "导入 Cython 扩展后 GIL 被重新启用 ⇒ freethreading_compatible 指令未生效",
        )


if __name__ == "__main__":
    unittest.main()
