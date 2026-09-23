# -*- coding: utf-8 -*-
"""第44批 T2/T3 门控测试：设计文档完整性 + 阈值校准脚本机制。

* T2 = `docs/设计文档/语义缓存升L2方案_v1.0.md`
* T3 = `docs/设计文档/内在模型L3调用精简器设计_v1.0.md`
* 校准脚本 = `tools/calibrate_semantic_threshold.py`（用**假编码器**测机制，不加载真模型）

★全部只读 / 注入隔离目录，**不写生产数据**。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DOC_T2 = os.path.join(_ROOT, "docs", "设计文档", "语义缓存升L2方案_v1.0.md")
_DOC_T3 = os.path.join(_ROOT, "docs", "设计文档", "内在模型L3调用精简器设计_v1.0.md")
_TOOL = os.path.join(_ROOT, "tools", "calibrate_semantic_threshold.py")


def _read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _load_tool():
    _spec = importlib.util.spec_from_file_location("m44_calib_tool", _TOOL)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


# ---------------------------------------------------------------------------
# 1) T2 设计文档完整性
# ---------------------------------------------------------------------------
class TestDesignDocL2(unittest.TestCase):
    REQUIRED = (
        "架构设计", "升级路径",           # 架构
        "阈值", "校准", "推荐",           # 阈值校准
        "TTL", "LRU", "容量",            # 缓存策略
        "置信度", "安全机制",             # 安全
        "灰度", "5%", "20%", "50%", "100%",  # 灰度
        "回滚",                          # 回滚
        "监控",                          # 监控
        "风险",                          # 风险
    )

    def setUp(self):
        self.assertTrue(os.path.isfile(_DOC_T2), "T2 设计文档缺失")
        self._t = _read(_DOC_T2)

    def test_01_doc_exists_and_sized(self):
        self.assertGreater(len(self._t), 5000)

    def test_02_required_sections_present(self):
        _miss = [k for k in self.REQUIRED if k not in self._t]
        self.assertEqual(_miss, [], "缺少章节关键词: %s" % _miss)

    def test_03_design_only_declared(self):
        """必须显式声明"仅设计不实施"（任务书验收标准）。"""
        self.assertIn("纯设计文档", self._t)
        self.assertIn("本批不实施", self._t)
        self.assertIn("未改动", self._t)

    def test_04_recommended_threshold_matches_calibration(self):
        """推荐阈值必须来自校准结论（0.85），而不是沿用 0.92。"""
        self.assertIn("0.85", self._t)
        self.assertIn("漏检 40%", self._t)

    def test_05_mentions_probe_set_and_tool(self):
        self.assertIn("tools/calibrate_semantic_threshold.py", self._t)
        self.assertIn("m44_阈值校准报告.json", self._t)

    def test_06_l2_bounded_by_t2_scope(self):
        """与 T3 的边界必须在文档中显式说明。"""
        self.assertIn("调用精简器", self._t)
        self.assertIn("互补", self._t)


# ---------------------------------------------------------------------------
# 2) T3 设计文档完整性
# ---------------------------------------------------------------------------
class TestDesignDocL3(unittest.TestCase):
    REQUIRED = (
        "L3", "决策协同",                # 定位
        "重复调用检测", "结果复用", "失败退避", "追问链合并",  # 4 功能
        "L1", "L2", "L4", "协作",        # 协作
        "输入", "输出", "反馈",           # 接口方向
        "幂等", "最大退避",               # 安全
        "观测级", "建议级", "决策级",      # 灰度
        "复用率", "退避次数", "合并次数", "节省调用数", "误判率",  # 监控
        "调用精简器", "语义缓存",          # 与 T2 关系
    )

    def setUp(self):
        self.assertTrue(os.path.isfile(_DOC_T3), "T3 设计文档缺失")
        self._t = _read(_DOC_T3)

    def test_10_doc_exists_and_sized(self):
        self.assertGreater(len(self._t), 5000)

    def test_11_required_sections_present(self):
        _miss = [k for k in self.REQUIRED if k not in self._t]
        self.assertEqual(_miss, [], "缺少章节关键词: %s" % _miss)

    def test_12_relation_with_t2_explicit(self):
        self.assertIn("架构位置", self._t)
        self.assertIn("无状态", self._t)
        self.assertIn("有状态", self._t)

    def test_13_design_only_declared(self):
        self.assertIn("纯设计文档", self._t)
        self.assertIn("本批不实施", self._t)

    def test_14_backoff_hard_cap_declared(self):
        """安全机制要求"失败退避必须有最大退避时间"。"""
        self.assertIn("900s", self._t)
        self.assertIn("硬上限", self._t)

    def test_15_four_layer_status_table(self):
        for _k in ("L1 数据感知", "L2 推理认知", "L3 决策协同", "L4 迭代进化"):
            self.assertIn(_k, self._t)


# ---------------------------------------------------------------------------
# 3) 校准脚本机制（假编码器）
# ---------------------------------------------------------------------------
class _FakeEncoder:
    """文本 → 固定向量（按字典查表），用于精确验证 precision/recall 机制。"""

    def __init__(self, table):
        self._t = table

    def encode_one(self, text):
        return self._t.get(text)


def _vec(cos):
    """构造与 ``[1,0]`` 夹角余弦恰为 ``cos`` 的二维向量。"""
    import math
    return [float(cos), math.sqrt(max(0.0, 1.0 - cos * cos))]


class TestCalibrationTool(unittest.TestCase):
    def setUp(self):
        self.mod = _load_tool()

    def test_20_cosine_basic(self):
        self.assertAlmostEqual(self.mod._cosine([1, 0], [1, 0]), 1.0, places=6)
        self.assertAlmostEqual(self.mod._cosine([1, 0], [0, 1]), 0.0, places=6)
        self.assertEqual(self.mod._cosine([0, 0], [1, 1]), 0.0)

    def test_21_histogram_buckets(self):
        _h = self.mod._histogram([0.71, 0.72, 0.95, 0.99])
        self.assertEqual(_h.get("0.7"), 2)
        self.assertEqual(_h.get("0.9"), 2)

    def test_22_probe_precision_recall_mechanics(self):
        """同义对 cos=0.9、异类对 cos=0.2 → 0.85 应 P=R=1.0；0.95 应 R=0。"""
        _enc = _FakeEncoder({"A": _vec(1.0), "A2": _vec(0.9),
                             "B": _vec(1.0), "B2": _vec(0.2)})
        _orig_s, _orig_d = self.mod.PROBE_SAME, self.mod.PROBE_DIFF
        try:
            self.mod.PROBE_SAME = (("A", "A2"),)
            self.mod.PROBE_DIFF = (("B", "B2"),)
            _r = self.mod.evaluate_probe(_enc)
            _rows = {round(x["threshold"], 2): x for x in _r["rows"]}
            self.assertEqual(_rows[0.85]["precision"], 1.0)
            self.assertEqual(_rows[0.85]["recall"], 1.0)
            self.assertEqual(_rows[0.95]["recall"], 0.0)
            # 异类最大 0.2 ≤ 任何候选阈值 → 永不误命中
            for _r2 in _r["rows"]:
                self.assertEqual(_r2["fp"], 0)
        finally:
            self.mod.PROBE_SAME, self.mod.PROBE_DIFF = _orig_s, _orig_d

    def test_23_probe_detects_false_positive(self):
        """异类对被判相似（cos=0.95 ≥ 0.92）→ precision 必须下降（抓得到误命中）。"""
        _enc = _FakeEncoder({"A": _vec(1.0), "A2": _vec(0.9),
                             "B": _vec(1.0), "B2": _vec(0.95)})
        _orig_s, _orig_d = self.mod.PROBE_SAME, self.mod.PROBE_DIFF
        try:
            self.mod.PROBE_SAME = (("A", "A2"),)
            self.mod.PROBE_DIFF = (("B", "B2"),)
            _r = self.mod.evaluate_probe(_enc)
            _rows = {round(x["threshold"], 2): x for x in _r["rows"]}
            self.assertEqual(_rows[0.92]["fp"], 1)
            self.assertLess(_rows[0.92]["precision"], 1.0)
            self.assertFalse(_r["safe_precision_0_95"])
        finally:
            self.mod.PROBE_SAME, self.mod.PROBE_DIFF = _orig_s, _orig_d

    def test_24_probe_reports_separation_band(self):
        _enc = _FakeEncoder({"A": _vec(1.0), "A2": _vec(0.9),
                             "B": _vec(1.0), "B2": _vec(0.2)})
        _orig_s, _orig_d = self.mod.PROBE_SAME, self.mod.PROBE_DIFF
        try:
            self.mod.PROBE_SAME = (("A", "A2"),)
            self.mod.PROBE_DIFF = (("B", "B2"),)
            _r = self.mod.evaluate_probe(_enc)
            self.assertEqual(_r["same_min"], 0.9)
            self.assertEqual(_r["diff_max"], 0.2)
        finally:
            self.mod.PROBE_SAME, self.mod.PROBE_DIFF = _orig_s, _orig_d

    def test_25_load_corpus_dedup_and_filter(self):
        _d = tempfile.mkdtemp(prefix="m44_calib_")
        try:
            _fp = os.path.join(_d, "calls_20260101.jsonl")
            with io.open(_fp, "w", encoding="utf-8") as f:
                for _p in ["深度学习原理", "深度学习原理", "ab", "注意力机制"]:
                    f.write(json.dumps({"prompt": _p}, ensure_ascii=False) + "\n")
            _got = self.mod.load_corpus(_d)
            # 去重（"深度学习原理" 只留 1 条）+ 过滤 <4 字符（"ab"）
            self.assertEqual(_got, ["深度学习原理", "注意力机制"])
        finally:
            shutil.rmtree(_d, ignore_errors=True)

    def test_26_corpus_stats_hit_rate_mechanics(self):
        _enc = _FakeEncoder({"p1": _vec(1.0), "p2": _vec(0.95), "p3": _vec(0.5)})
        _r = self.mod.corpus_stats(_enc, ["p1", "p2", "p3"],
                                   thresholds=(0.9, 0.99))
        self.assertEqual(_r["n"], 3)
        self.assertEqual(_r["nn_max"], 0.95)
        _rows = {x["threshold"]: x for x in _r["rows"]}
        # 三个点的最近邻：p1↔p2=0.95；p3 最近邻 = 0.5
        self.assertEqual(_rows[0.9]["hit"], 2)
        self.assertEqual(_rows[0.99]["hit"], 0)

    def test_27_run_raises_when_encoder_unavailable(self):
        """编码器不可用时必须明确报错，**不伪造数据**。"""
        _orig = self.mod.resolve_encoder
        try:
            self.mod.resolve_encoder = lambda wait_sec=0: None
            with self.assertRaises(RuntimeError):
                self.mod.run(None, with_corpus=False, wait_sec=0)
        finally:
            self.mod.resolve_encoder = _orig

    def test_28_sweep_covers_candidates(self):
        _enc = _FakeEncoder({"A": _vec(1.0), "A2": _vec(0.9),
                             "B": _vec(1.0), "B2": _vec(0.2)})
        _orig_s, _orig_d = self.mod.PROBE_SAME, self.mod.PROBE_DIFF
        try:
            self.mod.PROBE_SAME = (("A", "A2"),)
            self.mod.PROBE_DIFF = (("B", "B2"),)
            _rows = self.mod.sweep_probe(_enc)
            self.assertEqual(len(_rows), len(self.mod.SWEEP_THRESHOLDS))
            _best = max(_rows, key=lambda r: r["f1"])
            self.assertEqual(_best["f1"], 1.0)
        finally:
            self.mod.PROBE_SAME, self.mod.PROBE_DIFF = _orig_s, _orig_d


# ---------------------------------------------------------------------------
# 4) 交付边界：设计任务不得改动运行逻辑
# ---------------------------------------------------------------------------
class TestNoRuntimeChange(unittest.TestCase):
    def test_30_semantic_cache_threshold_calibrated(self):
        """★第45批 T1 反向同步：第44批"仅设计不实施"的守卫已随实施改为
        "必须等于校准值 0.85"（保持 `==` 而非放宽）。"""
        import config
        self.assertEqual(float(getattr(config, "SEMANTIC_CACHE_THRESHOLD")), 0.85)

    def test_31_no_l2_switch_introduced(self):
        import config
        for _k in ("ENABLE_SEMANTIC_CACHE_L2", "SEMANTIC_CACHE_L2_RATIO",
                   "ENABLE_L3_CALL_REDUCER_OBSERVE", "ENABLE_L3_REUSE"):
            self.assertFalse(hasattr(config, _k), "不应新增生产开关: %s" % _k)

    def test_32_no_call_reducer_module(self):
        for _p in (os.path.join(_ROOT, "nucleus", "llm", "call_reducer.py"),
                   os.path.join(_ROOT, "nucleus", "llm", "semantic_cache_l2.py")):
            self.assertFalse(os.path.exists(_p), "不应新建模块: %s" % _p)


if __name__ == "__main__":
    unittest.main()
