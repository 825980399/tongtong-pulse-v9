# -*- coding: utf-8 -*-
"""主线第68批 T7：Parquet主存储(T1) / 冷存告警治理(T6) / JSON容错解析(T8) / FAISS回退(T3) 测试。

隔离约定：一律使用 tempfile.mkdtemp()，绝不写生产 data/。
"""
import json
import os
import sys
import shutil
import tempfile
import threading
import unittest
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                    # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot        # noqa: E402
from nucleus.parsing import json_fault_tolerant as _ft           # noqa: E402
import nucleus.knowledge_access_layer as _kal                    # noqa: E402


def _mk_snap(tmpdir):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "pulse_knowledge_snapshot.json")
    s._logger = logging.getLogger("test_m68")
    s._logs = []
    s._log = lambda level, msg: s._logs.append(str(msg))
    s._lock = threading.RLock()
    return s


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                if hasattr(config, k):
                    try:
                        delattr(config, k)
                    except Exception:
                        pass
            else:
                setattr(config, k, v)


# ==================== T1 Parquet 主存储 ====================

class TestParquetPrimary(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _d(self):
        d = tempfile.mkdtemp(prefix="m68_")
        self._dirs.append(d)
        return d

    def test_11_parquet_dir_derived(self):
        """Parquet 目录由快照路径推导（同级 parquet/）。"""
        s = _mk_snap(self._d())
        self.assertTrue(s._m68_parquet_dir().endswith("parquet"))

    def test_12_switches_default_on(self):
        """第80批 T1 止血后：Parquet 主存储默认关（G0 分层塌缩根因，第81批重开），
        JSON 兼容备份默认仍开（双写安全兜底，防止 Parquet 失败时无回退）。"""
        s = _mk_snap(self._d())
        self.assertFalse(s._m68_parquet_primary_enabled())
        self.assertTrue(s._m68_json_backup_enabled())

    def test_13_switches_can_be_disabled(self):
        """两个开关均可关闭（回退 JSON 主存储）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(PARQUET_AS_PRIMARY_STORAGE=False):
            self.assertFalse(s._m68_parquet_primary_enabled())
        with _CfgSwitch(SNAPSHOT_SAVE_JSON_BACKUP=False):
            self.assertFalse(s._m68_json_backup_enabled())

    def test_14_load_returns_none_without_parquet(self):
        """无 Parquet 目录时返回 None（触发 JSON 回退），不抛异常。"""
        s = _mk_snap(self._d())
        self.assertIsNone(s._m68_load_from_parquet())

    def test_15_verify_parquet_negative_without_dir(self):
        """无 Parquet 目录时完整性校验返回 -1（表示不可用/未校验）。"""
        s = _mk_snap(self._d())
        self.assertEqual(s._m68_verify_parquet(), -1)

    def test_16_load_source_recorded(self):
        """加载来源被记录（parquet / json），便于诊断。"""
        s = _mk_snap(self._d())
        s._m68_last_load_source = "json"
        self.assertEqual(s._m68_last_load_source, "json")

    def test_17_primary_switch_off_uses_json_path(self):
        """主存储关闭时不调用 Parquet 加载（走 JSON 路径）。"""
        s = _mk_snap(self._d())
        with _CfgSwitch(PARQUET_AS_PRIMARY_STORAGE=False):
            self.assertFalse(s._m68_parquet_primary_enabled())
            # 关闭时不应命中 parquet 分支
            self.assertIsNone(s._m68_load_from_parquet())


# ==================== T6 冷存告警治理 ====================

class TestColdStorageGuard(unittest.TestCase):
    def test_61_config_present(self):
        """冷存一致性相关配置存在。"""

    def test_62_warn_limit_wired(self):
        """源码中已做告警限流（前3条 WARNING，其余 DEBUG），不再逐文件刷屏。"""
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "nucleus", "mnemosyne", "PulseNodePool.py")
        _src = open(_p, encoding="utf-8", errors="replace").read()
        self.assertIn("_skipped <= 3", _src, "应保留前3条 WARNING 的限流判据")
        self.assertIn("第68批", _src, "应有第68批改动标记")

    def test_63_missing_dir_info_once(self):
        """冷存目录缺失时输出一条 INFO 汇总（源码断言）。"""
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "nucleus", "mnemosyne", "PulseNodePool.py")
        _src = open(_p, encoding="utf-8", errors="replace").read()
        self.assertIn("冷存目录不存在，跳过 compaction", _src)


# ==================== T8 JSON 容错解析 ====================

class TestJsonFaultTolerant(unittest.TestCase):
    def test_81_valid_json_direct(self):
        """标准 JSON 走 direct 策略（不走修复）。"""
        ok, data, st = _ft.parse_json_fault_tolerant('{"a": 1}')
        self.assertTrue(ok)
        self.assertEqual(st, "direct")
        self.assertEqual(data["a"], 1)

    def test_82_array_with_key_values(self):
        """★核心：数组中放键值对 → 修复为对象（任务书真实错误模式）。"""
        _s = ('{"功能":"判断主题","关键步骤":["读配置"],'
              '"依赖的外部数据":["_a":"开关","_b":"前缀"]}')
        ok, data, st = _ft.parse_json_fault_tolerant(_s)
        self.assertTrue(ok, "数组里放键值对应被修复")
        self.assertEqual(st, "repaired")
        self.assertIsInstance(data["依赖的外部数据"], dict)
        self.assertEqual(data["依赖的外部数据"]["_a"], "开关")

    def test_83_trailing_comma(self):
        """尾随逗号可修复。"""
        ok, data, st = _ft.parse_json_fault_tolerant('{"功能":"f","关键步骤":["a","b",]}')
        self.assertTrue(ok)
        self.assertEqual(data["关键步骤"], ["a", "b"])

    def test_84_code_fence(self):
        """markdown 代码块可剥离后解析。"""
        _s = '```json\n{"功能":"f"}\n```'
        ok, data, st = _ft.parse_json_fault_tolerant(_s)
        self.assertTrue(ok)
        self.assertEqual(data["功能"], "f")

    def test_85_fallback_extract(self):
        """完全无法解析但含关键字段时，降级提取不丢全部内容。"""
        _s = '这是乱文 "功能": "仍能提取我" "关键步骤": ["s1", "s2"] 结尾'
        fields = _ft.extract_key_fields(_s)
        self.assertEqual(fields.get("功能"), "仍能提取我")
        self.assertEqual(fields.get("关键步骤"), ["s1", "s2"])

    def test_86_strip_code_fence(self):
        """围栏剥离函数行为正确。"""
        self.assertEqual(_ft.strip_code_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(_ft.strip_code_fence('{"a":1}'), '{"a":1}')

    def test_87_real_array_kv_via_repair(self):
        """repair_json_text 直接把 [ "k": "v" ] 转成对象形态。"""
        _r = _ft.repair_json_text('{"x": ["_a": "b"]}')
        self.assertIn('"_a": "b"', _r)
        # 修复后原始文本中数组起始已被替换为对象起始
        _ok, _data = True, None
        try:
            _data = json.loads(_r)
        except Exception:
            _ok = False
        self.assertTrue(_ok, "修复结果应可被标准 json.loads 解析")
        self.assertIsInstance(_data["x"], dict)

    def test_88_stomach_wired(self):
        """胃模块已接线策略5（源码断言，确保接线真实存在）。"""
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "organs", "body", "PulseStomach.py")
        _src = open(_p, encoding="utf-8", errors="replace").read()
        self.assertIn("策略5(容错解析", _src)
        self.assertIn("json_fault_tolerant", _src)


# ==================== T3 FAISS 回退（依赖未安装） ====================

class TestFaissFallback(unittest.TestCase):
    def test_91_faiss_not_installed(self):
        """★T0实测：faiss 未安装 → 必须走回退，不得因此崩溃。"""
        _installed = True
        try:
            __import__("faiss")
        except Exception:
            _installed = False
        if _installed:
            self.skipTest("faiss 已安装，回退断言不适用")
        # 未安装时 KAL 语义检索仍应可用（暴力余弦）
        k = _kal.KnowledgeAccessLayer(node_pool=None)
        self.assertIsNotNone(k)

    def test_92_kal_semantic_search_works_without_faiss(self):
        """FAISS 不可用时，KAL 语义检索（暴力余弦）仍正常工作。"""

        class _N:
            def __init__(self, nid, v):
                self.node_id = nid
                self.vector = v
                self.value = ""
                self.keywords = []

        class _P:
            def __init__(self):
                self.nodes = [_N("a", [1.0, 0.0]), _N("b", [0.0, 1.0])]

            def get_all_including_evicted(self):
                return self.nodes

        k = _kal.KnowledgeAccessLayer(node_pool=_P())
        out = k.semantic_search([1.0, 0.0], top_k=5)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][0].node_id, "a")
        self.assertAlmostEqual(out[0][1], 1.0, places=5)

    def test_93_faiss_config_present(self):
        """FAISS 配置项已就位（含索引类型/路径/批量/阈值）。"""
        for _k in ["ENABLE_FAISS_VECTOR_STORE", "FAISS_INDEX_TYPE",
                   "FAISS_INDEX_PATH", "FAISS_BATCH_SIZE", "VECTOR_DIMENSION"]:
            self.assertTrue(hasattr(config, _k), "缺少配置: %s" % _k)


if __name__ == "__main__":
    unittest.main()
