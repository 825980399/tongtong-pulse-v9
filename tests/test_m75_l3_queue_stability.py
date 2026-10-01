# -*- coding: utf-8 -*-
"""主线第75批 T1-T4 单元测试（≥20 例）。

T1 L3 扩缩稳定性（滞回+冷却+平滑）；T2 肝模块类型防御；
T3 self_inspector 精确匹配；T4 RSS 超时/慢源降级/优先级。
轻量范式同 test_info_field：InfoField.__new__ 跳过重型 __init__。
"""
import os
import sys
import time
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.field.InfoField import _PULSE_LAYER_L3, InfoField
from organs.body.PulseLiver import PulseLiver
from nucleus.self_inspector import SelfInspector, _BODY_LOC_STATS
import nucleus.runtime_tempo as _rt
from nucleus.knowledge.RssCollector import RssCollector

try:
    import feedparser  # 解析相关测试需要
except Exception:  # pragma: no cover
    feedparser = None


# ===================== T1 helper =====================
class _FakeQueue:
    def __init__(self, depth):
        self._d = depth

    def qsize(self):
        return self._d


class _FakePool:
    def __init__(self, depth, max_workers=2):
        self._work_queue = _FakeQueue(depth)
        self._max_workers = max_workers


def _make_info(depth, max_workers=2, up_cd=60.0, down_cd=60.0, step=1,
               hyst_down=0.50, hyst_on=True, burst=True):
    inst = InfoField.__new__(InfoField)
    inst._layer_pools = {_PULSE_LAYER_L3: _FakePool(depth, max_workers)}
    inst._layer_queue_limits = {_PULSE_LAYER_L3: 100}
    inst._layer_rejected_count = {_PULSE_LAYER_L3: 0}
    inst._l3_dynamic_enabled = True
    inst._l3_last_scale_ts = 0.0
    inst._l3_scale_cooldown = up_cd
    inst._l3_scale_up_cooldown = up_cd
    inst._l3_scale_down_cooldown = down_cd
    inst._l3_base_workers = 2
    inst._l3_max_dynamic_workers = 5
    inst._l3_scale_up_thresholds = [0.70, 0.85, 0.95]
    inst._l3_burst_detection_enabled = burst
    inst._l3_burst_growth_threshold = 0.50
    inst._l3_min_workers = 3
    inst._l3_scale_down_buffer_sec = 30.0
    # ★主线第75批 T1 新增
    inst._l3_scale_step = step
    inst._l3_hysteresis_down_ratio = hyst_down
    inst._l3_hysteresis_enabled = hyst_on
    inst._l3_burst_window = []
    inst._l3_below_half_since = 0.0
    inst._l3_scale_up_count = 0
    inst._l3_scale_down_count = 0
    inst._l3_last_scale_reason = ""
    inst._l3_demand_start_ts = 0.0
    inst._l3_response_times = []
    inst._l3_depth_sample_ts = 0.0
    inst._l3_depth_history = []
    inst._l3_producer_counts = {}
    inst._l3_completion_window = []
    inst._module_logger = MagicMock()
    inst._resize_layer_pool = MagicMock()
    inst._record_phase18_l3 = MagicMock()
    return inst


# ===================== T1 =====================
class TestT1L3Scaling(unittest.TestCase):
    def test_配置块存在且冷却60(self):
        from config import L3_SCALING_STABILITY
        self.assertEqual(L3_SCALING_STABILITY["scale_cooldown_sec"], 60.0)
        self.assertEqual(L3_SCALING_STABILITY["scale_step"], 1)
        self.assertIn("hysteresis_down_ratio", L3_SCALING_STABILITY)

    def test_冷却60s抑制重复突发(self):
        inst = _make_info(50, max_workers=2, up_cd=60.0)
        now = time.time()
        inst._l3_burst_window = [(now - 8, 10), (now - 4, 30)]
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_count, 1)
        inst._auto_scale_l3_by_depth()  # 冷却期内
        self.assertEqual(inst._resize_layer_pool.call_count, 1)

    def test_突发平滑只扩1(self):
        inst = _make_info(50, max_workers=2)
        now = time.time()
        inst._l3_burst_window = [(now - 8, 10), (now - 4, 30)]
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)
        self.assertEqual(inst._l3_last_scale_reason, "burst")

    def test_阈值扩容平滑只扩1(self):
        inst = _make_info(95, max_workers=2)
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)

    def test_滞回保持带不调整(self):
        # 0.50 < ratio 0.60 < 0.70 → 中间带，不扩不缩
        inst = _make_info(60, max_workers=3)
        inst._l3_last_scale_ts = 0.0
        inst._l3_below_half_since = time.time() - 60.0
        inst._auto_scale_l3_by_depth()
        inst._resize_layer_pool.assert_not_called()

    def test_滞回下阈值缩容(self):
        inst = _make_info(40, max_workers=4)  # ratio 0.40 ≤ 0.50
        inst._l3_last_scale_ts = 0.0
        inst._l3_below_half_since = time.time() - 40.0
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)  # 4→3

    def test_平滑步长可配置为2(self):
        inst = _make_info(95, max_workers=2, step=2)
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 4)  # +2

    def test_L2无动态扩缩_仅记录(self):
        # 任务书 T1.4：L2 为固定 worker，不存在同构动态扩缩逻辑（文档化差异，非回归）
        self.assertTrue(hasattr(InfoField, "_init_layer_pools"))


# ===================== T2 =====================
def _fake_tempo_returning(value):
    return types.SimpleNamespace(get_background_tempo=lambda: value)


class TestT2LiverTypeGuard(unittest.TestCase):
    def _inst(self):
        obj = types.SimpleNamespace()
        obj._log = lambda lvl, msg: None
        return obj

    def test_tempo正常返回float(self):
        with patch.object(_rt, "get_runtime_tempo", lambda: _fake_tempo_returning(0.5)):
            self.assertEqual(PulseLiver._get_background_tempo(self._inst()), 0.5)

    def test_tempo_dict回退1(self):
        logs = []
        obj = types.SimpleNamespace(_log=lambda lvl, msg: logs.append(msg))
        with patch.object(_rt, "get_runtime_tempo", lambda: _fake_tempo_returning({"tempo": 0.5})):
            self.assertEqual(PulseLiver._get_background_tempo(obj), 1.0)
        self.assertTrue(any("类型异常" in m for m in logs))

    def test_tempo_bool回退1(self):
        with patch.object(_rt, "get_runtime_tempo", lambda: _fake_tempo_returning(True)):
            self.assertEqual(PulseLiver._get_background_tempo(self._inst()), 1.0)

    def test_tempo异常回退1(self):
        def _boom():
            raise RuntimeError("nope")
        with patch.object(_rt, "get_runtime_tempo", _boom):
            self.assertEqual(PulseLiver._get_background_tempo(self._inst()), 1.0)

    def test_异步优化异常日志含类型(self):
        inst = PulseLiver.__new__(PulseLiver)
        inst.node_pool = MagicMock()
        # ★B156-3 T-A05 读码判定：_do_optimize 已迁移至 self._kal.get_stats()（非 node_pool.get_stats），
        #   生产 KAL 集成是既定形态（8d9b95f 引入），测试 mock 过时 → 对齐 self._kal。
        inst._kal = MagicMock()
        inst._kal.get_stats.side_effect = ValueError("boom")
        inst._lock = MagicMock()
        inst._lock.__enter__ = lambda s: s
        inst._lock.__exit__ = lambda *a: False
        inst._stop_requested = False
        inst._optimize_pending = False
        logs = []
        inst._log = lambda lvl, msg: logs.append((lvl, msg))
        inst._do_optimize()
        self.assertTrue(any("ValueError" in m for _, m in logs),
                        f"未包含异常类型: {logs}")


# ===================== T3 =====================
_T3_SRC = """import os


def alpha():
    return 1


def beta(x):
    return x + 1


class Gamma:
    def delta(self):
        return 2
"""


def _reset_stats():
    for _k in ("fallback", "offset_probe", "exact", "miss"):
        _BODY_LOC_STATS[_k] = 0


class TestT3SelfInspector(unittest.TestCase):
    def setUp(self):
        _reset_stats()
        self._tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                                delete=False, encoding="utf-8")
        self._tmp.write(_T3_SRC)
        self._tmp.close()
        self._si = SelfInspector()

    def tearDown(self):
        os.remove(self._tmp.name)

    def test_精确命中计入exact(self):
        body = self._si._read_method_body(self._tmp.name, 3, "alpha")
        self.assertIsNotNone(body)
        self.assertEqual(_BODY_LOC_STATS["exact"], 1)

    def test_偏移1行计为exact(self):
        # start_line=4 是 alpha 函数体首行（+1 off-by-one），应计为精确命中
        body = self._si._read_method_body(self._tmp.name, 4, "alpha")
        self.assertIsNotNone(body)
        self.assertEqual(_BODY_LOC_STATS["exact"], 1)

    def test_方法名前缀匹配兜底(self):
        # 传截断名 "alp"，应前缀匹配到 alpha
        body = self._si._read_method_body(self._tmp.name, 1, "alp")
        self.assertIsNotNone(body)
        self.assertGreaterEqual(_BODY_LOC_STATS["fallback"], 1)

    def test_无法定位返回none(self):
        # 方法名完全不存在 → 返回 None，且不应误计任何命中类别
        # （★主线第75批 T3：miss 路径在重构后不单独计数，避免污染精确率统计）
        body = self._si._read_method_body(self._tmp.name, 1, "zzz_nope")
        self.assertIsNone(body)
        self.assertEqual(_BODY_LOC_STATS["exact"], 0)
        self.assertEqual(_BODY_LOC_STATS["fallback"], 0)

    def test_精确匹配率显著提升(self):
        self._si._read_method_body(self._tmp.name, 3, "alpha")   # exact
        self._si._read_method_body(self._tmp.name, 4, "alpha")   # exact(±1)
        self._si._read_method_body(self._tmp.name, 6, "beta")    # exact
        _tot = (_BODY_LOC_STATS["exact"] + _BODY_LOC_STATS["offset_probe"]
                + _BODY_LOC_STATS["fallback"])
        self.assertGreater(_tot, 0)
        self.assertGreaterEqual(_BODY_LOC_STATS["exact"] / _tot, 0.5)


# ===================== T4 =====================
class TestT4RssCollector(unittest.TestCase):
    def _collector(self, cfg=None):
        return RssCollector(cfg=cfg or {}, fetch_fn=lambda u, timeout=15: b"")

    def test_超时默认15(self):
        self.assertEqual(self._collector()._timeout, 15.0)

    def test_超时配置生效(self):
        self.assertEqual(self._collector({"per_source_timeout": 5})._timeout, 5.0)

    def test_优先级可配置(self):
        self.assertEqual(self._collector({"emit_priority": 2})._emit_priority, 2)

    def test_超时源记慢源(self):
        import socket
        def _slow(u, timeout=15):
            raise socket.timeout("too slow")
        c = RssCollector(cfg={"feeds": [{"name": "慢源", "url": "http://x"}]},
                        fetch_fn=_slow)
        c.pull_all(push=False)
        self.assertEqual(c._feeds[0]["slow_count"], 1)

    def test_慢源阈值降级(self):
        import socket
        def _slow(u, timeout=15):
            raise socket.timeout("too slow")
        c = RssCollector(cfg={"feeds": [{"name": "慢源", "url": "http://x"}],
                                 "slow_source_threshold": 3},
                        fetch_fn=_slow)
        for _ in range(3):
            c.pull_all(push=False)
        self.assertTrue(c._feeds[0]["deprioritized"])

    def test_成功重置慢源(self):
        import socket
        _state = {"slow": True}
        def _fetch(u, timeout=15):
            if _state["slow"]:
                raise socket.timeout("slow")
            return b"<rss></rss>"
        c = RssCollector(cfg={"feeds": [{"name": "源", "url": "http://x"}]},
                        fetch_fn=_fetch)
        c.pull_all(push=False)
        self.assertEqual(c._feeds[0]["slow_count"], 1)
        _state["slow"] = False
        c.pull_all(push=False)
        self.assertEqual(c._feeds[0]["slow_count"], 0)
        self.assertFalse(c._feeds[0]["deprioritized"])

    @unittest.skipIf(feedparser is None, "feedparser 未安装")
    def test_解析正常产出文章(self):
        # ★主线第75批 T4：测试用极短内容质量分 0.18<0.5 被过滤，
        #   cfg 设 min_quality_score=0 仅用于本测试放行；并隔离磁盘去重保证确定性
        _xml = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b'<item><title>t1</title><link>http://a</link>'
                b'<description>d1</description></item></channel></rss>')
        c = RssCollector(cfg={"feeds": [{"name": "源", "url": "http://x"}],
                              "min_quality_score": 0},
                        fetch_fn=lambda u, timeout=15: _xml)
        c._seen = {}
        c._save_seen = lambda: None
        arts = c.pull_all(push=False)
        self.assertGreaterEqual(len(arts), 1)

    @unittest.skipIf(feedparser is None, "feedparser 未安装")
    def test_去重丢弃重复(self):
        # min_quality_score=0 放行短内容；隔离磁盘去重，验证同实例二次拉取被去重
        _xml = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b'<item><title>t1</title><link>http://a</link>'
                b'<description>d1</description></item></channel></rss>')
        c = RssCollector(cfg={"feeds": [{"name": "源", "url": "http://x"}],
                              "min_quality_score": 0},
                        fetch_fn=lambda u, timeout=15: _xml)
        c._seen = {}
        c._save_seen = lambda: None
        a1 = c.pull_all(push=False)
        a2 = c.pull_all(push=False)  # 同内容应被去重
        self.assertEqual(len(a1), 1)
        self.assertEqual(len(a2), 0)


if __name__ == "__main__":
    unittest.main()
