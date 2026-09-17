# -*- coding: utf-8 -*-
"""第52批 T1（P2-366）门控测试：ReportBus 落盘携带消费标记。

背景：`publish()` 原顺序为 `_write()` → dispatch，
      导致 `consumed_by` 落盘时恒为空（第51批实测磁盘 137 份全部为空）
      → 重启后恢复的报告全显示"未消费" → 消费率统计失真。

覆盖：
* 落盘 `consumed_by` 正确性（认领型 / 旁观型 / 混合 / 异常隔离）
* 灰度开关零回归（关闭 → 退回旧顺序）
* `load_from_disk` 跳过归档目录 + 历史报告计数
* `get_stats` 新字段
* 源码接线（dispatch 在 `_write` 之前）
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.reporting.report_bus import ReportBus  # noqa: E402

_SWITCH = "ENABLE_REPORT_DISPATCH_BEFORE_WRITE"


def _consumer_yes(env):
    return True


def _consumer_no(env):
    return False


def _consumer_boom(env):
    raise RuntimeError("boom")


def _named(name):
    """构造具名消费者（便于断言 consumed_by 内容）。"""
    def _f(env):
        return True
    _f.__name__ = name
    return _f


class _BusBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m52_t1_")
        self._base = os.path.join(self._tmp, "reports")
        self._orig = getattr(config, _SWITCH, True)

    def tearDown(self):
        setattr(config, _SWITCH, self._orig)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _bus(self, persist=True):
        return ReportBus(base_dir=self._base, persist=persist)

    def _disk(self, report_id, rtype="health"):
        _p = os.path.join(self._base, rtype, "%s.json" % report_id)
        if not os.path.isfile(_p):
            return None
        return json.load(io.open(_p, encoding="utf-8"))


class TestConsumedByOnDisk(_BusBase):
    def test_01_claimer_marks_disk(self):
        """★核心：认领型消费者 → 落盘 consumed_by 非空。"""
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut", content={"i": 1})
        _j = self._disk(_r["report_id"])
        self.assertIsNotNone(_j, "报告必须落盘")
        self.assertEqual(_j["consumed_by"], ["_consumer_yes"])

    def test_02_observer_leaves_empty(self):
        """旁观型消费者（返回假值）→ 落盘为空（未认领）。"""
        _b = self._bus()
        _b.subscribe("health", _consumer_no)
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"], [])

    def test_03_mixed_only_claimer_recorded(self):
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _b.subscribe("health", _consumer_no)
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"],
                         ["_consumer_yes"])

    def test_04_wildcard_subscriber_marks(self):
        _b = self._bus()
        _b.subscribe("*", _consumer_yes)
        _r = _b.publish_simple("runtime", "ut")
        self.assertEqual(self._disk(_r["report_id"], "runtime")["consumed_by"],
                         ["_consumer_yes"])

    def test_05_consumer_exception_isolated(self):
        """单个消费者异常 → 隔离 + 进 errors（★不得静默）。"""
        _b = self._bus()
        _b.subscribe("health", _consumer_boom)
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut")
        self.assertTrue(_r["errors"], "异常应进 errors")
        self.assertIn("_consumer_boom", _r["errors"][0])
        # 另一个消费者仍生效
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"],
                         ["_consumer_yes"])

    def test_06_duplicate_consumer_recorded_once(self):
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"],
                         ["_consumer_yes"])

    def test_07_no_subscriber_empty(self):
        _b = self._bus()
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"], [])
        self.assertEqual(_r["consumers"], [])

    def test_08_named_consumers_ordered(self):
        _b = self._bus()
        _b.subscribe("health", _named("c1"))
        _b.subscribe("health", _named("c2"))
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"], ["c1", "c2"])

    def test_09_dispatch_false_skips(self):
        """dispatch=False → 不分发、落盘为空。"""
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish(_r0 := __import__(
            "nucleus.reporting.report_envelope", fromlist=["make_envelope"]
        ).make_envelope("health", "ut"), dispatch=False)
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"], [])
        self.assertFalse(_r["dispatched"])

    def test_10_persist_false_no_disk(self):
        _b = self._bus(persist=False)
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut")
        self.assertIsNone(self._disk(_r["report_id"]))
        self.assertFalse(_r["persisted"])


class TestSwitchZeroRegression(_BusBase):
    def test_20_switch_off_restores_old_order(self):
        """★零回归：开关关闭 → 落盘无消费标记（旧行为）。"""
        setattr(config, _SWITCH, False)
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"], [])
        # 但返回值仍正确报告消费者（行为不变）
        self.assertEqual(_r["consumers"], ["_consumer_yes"])
        # 内存中的 envelope 仍有标记
        self.assertEqual(_b.get(_r["report_id"]).consumed_by, ["_consumer_yes"])

    def test_21_switch_on_marks_disk(self):
        setattr(config, _SWITCH, True)
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut")
        self.assertEqual(self._disk(_r["report_id"])["consumed_by"],
                         ["_consumer_yes"])

    def test_22_default_is_true(self):
        self.assertTrue(getattr(config, _SWITCH, True))
        self.assertTrue(self._bus()._dispatch_before_write())


class TestLoadFromDisk(_BusBase):
    def _write_json(self, rtype, name, rid, consumed):
        _d = os.path.join(self._base, rtype)
        os.makedirs(_d, exist_ok=True)
        io.open(os.path.join(_d, name), "w", encoding="utf-8").write(
            json.dumps({"report_id": rid, "report_type": rtype,
                        "generator": "ut", "consumed_by": consumed,
                        "anomalies": [], "content": {},
                        "generated_at": 1}, ensure_ascii=False))

    def test_30_skips_archive_dirs(self):
        """★归档目录（``_`` 前缀）不得被载入。"""
        self._write_json("health", "a.json", "a", ["x"])
        self._write_json("_archive_x/health", "b.json", "b", ["y"])
        _n = self._bus(persist=False).load_from_disk()
        self.assertEqual(_n, 1)
        self.assertIsNone(self._bus(persist=False).get("b"))

    def test_31_counts_legacy_without_consumed(self):
        """★历史报告（consumed_by 空）单独计数。"""
        self._write_json("health", "a.json", "a", ["x"])
        self._write_json("health", "b.json", "b", [])
        self._write_json("health", "c.json", "c", [])
        _b = self._bus(persist=False)
        self.assertEqual(_b.load_from_disk(), 3)
        self.assertEqual(_b.get_stats()["legacy_without_consumed"], 2)

    def test_32_legacy_reset_between_loads(self):
        self._write_json("health", "a.json", "a", [])
        _b = self._bus(persist=False)
        _b.load_from_disk()
        _b.load_from_disk()
        self.assertEqual(_b.get_stats()["legacy_without_consumed"], 1)

    def test_33_roundtrip_new_report_marked(self):
        """★端到端：落盘 → 重新载入 → 消费状态**保留**。"""
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _r = _b.publish_simple("health", "ut")
        _b2 = self._bus(persist=False)
        _b2.load_from_disk()
        _e = _b2.get(_r["report_id"])
        self.assertIsNotNone(_e)
        self.assertEqual(_e.consumed_by, ["_consumer_yes"])
        self.assertEqual(_b2.get_stats()["legacy_without_consumed"], 0,
                         "新报告不应被计为 legacy")

    def test_34_missing_dir_returns_zero(self):
        _b = ReportBus(base_dir=os.path.join(self._tmp, "nope"), persist=False)
        self.assertEqual(_b.load_from_disk(), 0)

    def test_35_corrupt_json_skipped(self):
        _d = os.path.join(self._base, "health")
        os.makedirs(_d, exist_ok=True)
        io.open(os.path.join(_d, "bad.json"), "w", encoding="utf-8").write("{")
        self.assertEqual(self._bus(persist=False).load_from_disk(), 0)


class TestStats(_BusBase):
    def test_40_new_fields_present(self):
        _st = self._bus().get_stats()
        self.assertIn("dispatch_before_write", _st)
        self.assertIn("legacy_without_consumed", _st)

    def test_41_consumption_rate_reflects_disk(self):
        _b = self._bus()
        _b.subscribe("health", _consumer_yes)
        _b.subscribe("runtime", _consumer_yes)
        for _i in range(3):
            _b.publish_simple("health", "ut", content={"i": _i})
        _b.publish_simple("runtime", "ut")
        _st = _b.get_stats()
        self.assertEqual(_st["total"], 4)
        self.assertEqual(_st["consumed"], 4)
        self.assertEqual(_st["consumption_rate"], 1.0)


class TestSourceWiring(_BusBase):
    def test_50_dispatch_before_write_in_source(self):
        """★源码接线：`_dispatch` 调用应出现在 `_write` 之前。"""
        _s = io.open(os.path.join(_ROOT, "nucleus/reporting/report_bus.py"),
                     encoding="utf-8").read()
        _i = _s.find("def publish(")
        _seg = _s[_i:_i + 2600]
        _i_disp = _seg.find("self._dispatch(envelope, _consumers, _errors)")
        _i_write = _seg.find("_persisted = self._write(envelope)")
        self.assertGreater(_i_disp, 0, "未找到 _dispatch 调用")
        self.assertGreater(_i_write, 0, "未找到 _write 调用")
        self.assertLess(_i_disp, _i_write,
                        "★dispatch 必须在 _write 之前（P2-366）")

    def test_51_has_switch_helper(self):
        _s = io.open(os.path.join(_ROOT, "nucleus/reporting/report_bus.py"),
                     encoding="utf-8").read()
        self.assertIn("def _dispatch_before_write", _s)
        self.assertIn("ENABLE_REPORT_DISPATCH_BEFORE_WRITE", _s)

    def test_52_config_has_switch(self):
        _c = io.open(os.path.join(_ROOT, "config.py"), encoding="utf-8").read()
        self.assertIn(_SWITCH, _c)

    def test_53_publish_ast_structure(self):
        """AST 复核：publish 内 if dispatch 分支存在且 _write 调用存在。"""
        _tree = ast.parse(io.open(
            os.path.join(_ROOT, "nucleus/reporting/report_bus.py"),
            encoding="utf-8").read())
        _pub = None
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.FunctionDef) and _n.name == "publish":
                _pub = _n
                break
        self.assertIsNotNone(_pub)
        _calls = [n.func.attr if isinstance(n.func, ast.Attribute)
                  else getattr(n.func, "id", "")
                  for n in ast.walk(_pub) if isinstance(n, ast.Call)]
        self.assertIn("_write", _calls)
        self.assertIn("_dispatch", _calls)

    def test_54_no_bare_except_pass(self):
        import re
        _s = io.open(os.path.join(_ROOT, "nucleus/reporting/report_bus.py"),
                     encoding="utf-8").read()
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)), 0)


if __name__ == "__main__":
    unittest.main()
