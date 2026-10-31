# -*- coding: utf-8 -*-
"""406 刀3 判据：经验路由阈值埋点 + 纯知识拦截放开。

五组断言：
  A 埋点格式：任务书要求的 `[406刀3] experience_route score=… query=… top_hit=…`
    真的被拼出来，且带了 `hit/thr/n/type/src` 五个可归类的字段。
  B 埋点健壮性：诊断属性缺失 / reasoning_exp 为 None 时**只打日志不抛** ——
    埋点绝不能成为路由的新失败面。
  C 纯知识拦截放开：命中≥3 跳过、命中<3 放行、池不可用也放行（探针故障绝不误拦）。
  D 阈值未改 + 诊断快照：`_min_confidence` 仍 0.5；`best_score` 是**未截断**的真实值。
  E 结构：埋点在 `search()` 之后、`_SKIP_TYPES` 判断之前（AST 定位，不用文本包含）——
    否则「命中了但被类型闸挡掉」在分布里就看不见，3 天数据白跑。

★为什么 E 组要用 AST：
  `search` 这个词在本函数里出现 13 次（含注释、docstring、别的调用），
  文本序比较必然误判；只有 AST 能定位到"真正那次 search 调用"的行号。
"""
import ast
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IW = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")
_RX = os.path.join(_ROOT, "nucleus", "mnemosyne", "ReasoningExperience.py")
_CFG = os.path.join(_ROOT, "config.py")


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


# ★缓存 AST：PulseInnerWorld.py 有 1.5 万行 / 940 KB，每个用例重读重解析要 3~5 秒，
#   9 个用例就白烧 30 多秒。判据要跑得动才有人跑。
_TREE_CACHE = {}


_SRC_CACHE = {}


def _src(path):
    if path not in _SRC_CACHE:
        _SRC_CACHE[path] = _read(path)
    return _SRC_CACHE[path]


def _tree(path):
    if path not in _TREE_CACHE:
        _TREE_CACHE[path] = ast.parse(_src(path))
    return _TREE_CACHE[path]


def _func_node(path, name):
    for n in ast.walk(_tree(path)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


class _N:
    """假节点：只要 value/keywords 两个字段（探针就是这么读的）。"""

    def __init__(self, v, kws):
        self.value = v
        self.keywords = kws


class _Pool:
    def __init__(self, nodes):
        self._nodes = nodes
        self.calls = 0

    def query(self, **_kw):
        self.calls += 1
        return self._nodes


class _Ctx:
    def __init__(self, q):
        self.question = q
        self.payload = {}
        self.user_name = "u"
        self.correlation_id = "c1"


def _mk_iw(pool, logs):
    """绕过 __init__ 造一个只带本次所需状态的内在世界实例。"""
    from organs.brain.PulseInnerWorld import PulseInnerWorld

    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.node_pool = pool
    iw._log = lambda lvl, msg: logs.append((lvl, msg))
    return iw


class TestA_TraceFormat(unittest.TestCase):
    """埋点行必须带齐可归类的字段 —— 少一个字段，3 天后就回答不了"为什么 0 命中"。"""

    def setUp(self):
        self.logs = []
        self.iw = _mk_iw(None, self.logs)

        class _RX:
            last_search_diagnostics = {
                "best_score": 0.8875, "threshold": 0.5, "n_experiences": 128, "hit": True,
            }

        self.rx = _RX()
        self.match = {
            "derivation_type": "remote_api_confirmed",
            "source": "quality_feedback",
            "confidence": 0.89,
            "matched_example": '请分析以下Python方法，并返回"JSON"',
        }
        self.iw._m406_trace_experience_route("什么是量子纠缠", self.match, self.rx)
        self.line = self.logs[-1][1] if self.logs else ""

    def test_01_prefix_and_marker(self):
        self.assertTrue(self.line.startswith("[406刀3] experience_route "),
                        "埋点前缀不符，实际：%r" % self.line[:60])

    def test_02_level_is_debug(self):
        from nucleus.const import LogLevel

        self.assertEqual(LogLevel.DEBUG, self.logs[-1][0],
                         "埋点必须是 DEBUG（LOG_LEVEL_FILE=DEBUG 才落盘，且不该刷 WARNING）")

    def test_03_score_from_diagnostics(self):
        self.assertIn("score=0.89", self.line,
                      "score 应取诊断快照的未截断值（0.8875 → 0.89）")

    def test_04_hit_flag(self):
        self.assertIn("hit=1", self.line)

    def test_05_threshold_snapshot(self):
        self.assertIn("thr=0.50", self.line,
                      "必须带阈值快照，否则 3 天后不知道当时阈值是多少")

    def test_06_experience_count(self):
        self.assertIn("n=128", self.line, "必须带经验库条数，才能看出分母")

    def test_07_type_field(self):
        self.assertIn("type=remote_api_confirmed", self.line,
                      "type 缺失就看不出「命中了却被类型闸挡掉」这一整类")

    def test_08_src_field(self):
        self.assertIn("src=quality_feedback", self.line)

    def test_09_query_quoted(self):
        self.assertIn('query="什么是量子纠缠"', self.line)

    def test_10_top_hit_quoted_and_sanitized(self):
        self.assertIn("top_hit=", self.line)
        self.assertIn("'JSON'", self.line, "matched_example 里的双引号应被换成单引号（防日志行断裂）")
        self.assertNotIn('"JSON"', self.line, "双引号未被替换会截断日志字段")


class TestB_TraceRobustness(unittest.TestCase):
    """埋点不得成为路由的新失败面：拿不到数据就退化成 0，而不是抛。"""

    def _run(self, match, rx):
        logs = []
        iw = _mk_iw(None, logs)
        iw._m406_trace_experience_route("什么是意识", match, rx)
        return logs

    def test_01_empty_diagnostics(self):
        class _RX:
            last_search_diagnostics = {}

        logs = self._run(None, _RX())
        self.assertEqual(1, len(logs))
        self.assertIn("score=0.00 hit=0", logs[0][1])

    def test_02_object_without_attribute(self):
        class _RX:
            pass

        self.assertEqual(1, len(self._run(None, _RX())), "无属性对象也要出一行（不能静默丢样本）")

    def test_03_reasoning_exp_none(self):
        self.assertEqual(1, len(self._run(None, None)))

    def test_04_weird_types_do_not_raise(self):
        class _RX:
            last_search_diagnostics = {
                "best_score": "0.42", "threshold": None, "n_experiences": "7", "hit": 0,
            }

        logs = self._run({"matched_example": None, "derivation_type": None, "source": None}, _RX())
        self.assertEqual(1, len(logs), "脏字段应被兜成 '-' 而不是抛异常")

    def test_05_trace_switch_reads_config(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld as P

        self.assertTrue(P._m406_route_trace_on(), "默认应为开启（EXPERIENCE_ROUTE_TRACE 缺省 True）")
        with patch("config.EXPERIENCE_ROUTE_TRACE", False):
            self.assertFalse(P._m406_route_trace_on())


class TestC_KnowledgeGuard(unittest.TestCase):
    """拦截放开：本地真答得了才跳，答不了就放行给经验匹配。"""

    def setUp(self):
        import nucleus.mnemosyne.ReasoningExperience as _rxmod

        self._rxmod = _rxmod
        self._calls = {"n": 0}
        self._rec = []

        outer = self

        class _RXMod:
            last_search_diagnostics = {
                "best_score": 0.0, "threshold": 0.5, "n_experiences": 128, "hit": False,
            }

            def search(self, q, exclude_types=None):
                # ★407刀2：签名跟上 `ReasoningExperience.search(question, exclude_types=None)`。
                #   不改的话本 mock 会 TypeError，被路由外层 `except Exception` 吞掉，
                #   `_calls["n"]` 不增 ⇒ 4 个用例假红（测出来的是 mock 过时，不是代码坏了）。
                #   顺手把实参记下来，供 test_04b 断言"路由侧确实注入了 _SKIP_TYPES"。
                outer._calls["n"] += 1
                outer._calls.setdefault("exclude_types", []).append(exclude_types)
                return None  # 放行后不命中 ⇒ 本函数仍走 return None（被测路径终结在此）

        self._p1 = patch.object(_rxmod, "get_reasoning_experience", lambda: _RXMod())
        self._p1.start()
        import organs.brain.PulseInnerWorld as _m

        self._p2 = patch.object(_m, "record_local_inference", lambda k: self._rec.append(k))
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()

    def _guard_lines(self, logs):
        return [m for _, m in logs if "knowledge_guard" in m]

    def test_01_skip_when_hits_ge_min(self):
        pool = _Pool([_N("量子纠缠的定义", ["量子", "纠缠"]),
                      _N("纠缠态与量子比特", ["量子"]),
                      _N("量子计算入门", ["量子"]),
                      _N("无关", ["天气"])])
        logs = []
        iw = _mk_iw(pool, logs)
        self.assertIsNone(iw._detect_experience_route(_Ctx("什么是量子纠缠")))
        self.assertTrue(any("skip=1" in m for m in self._guard_lines(logs)),
                        "命中 3 条应跳过，实际日志：%r" % self._guard_lines(logs))

    def test_02_skip_does_not_call_search(self):
        pool = _Pool([_N("量子", ["量子"]), _N("量子纠缠", ["量子"]), _N("纠缠", ["纠缠"])])
        iw = _mk_iw(pool, [])
        iw._detect_experience_route(_Ctx("什么是量子纠缠"))
        self.assertEqual(0, self._calls["n"], "既然本地答得了，就不该再跑一次经验检索")

    def test_03_pass_when_hits_lt_min(self):
        pool = _Pool([_N("无关", ["天气"]), _N("无关2", ["饮食"])])
        logs = []
        iw = _mk_iw(pool, logs)
        iw._detect_experience_route(_Ctx("什么是量子纠缠"))
        self.assertTrue(any("skip=0" in m for m in self._guard_lines(logs)),
                        "命中不足应放行，实际日志：%r" % self._guard_lines(logs))

    def test_04_pass_calls_search(self):
        pool = _Pool([_N("无关", ["天气"])])
        iw = _mk_iw(pool, [])
        iw._detect_experience_route(_Ctx("什么是量子纠缠"))
        self.assertEqual(1, self._calls["n"], "放行后必须真的去查经验库")

    def test_04b_search_called_with_skip_types(self):
        """★407刀2：放行后的检索必须**带上 `_SKIP_TYPES`** —— 否则又回到"命中后再砍"。"""
        pool = _Pool([_N("无关", ["天气"])])
        iw = _mk_iw(pool, [])
        iw._detect_experience_route(_Ctx("什么是量子纠缠"))
        _seen = self._calls.get("exclude_types") or []
        self.assertTrue(_seen, "search 一次都没被调用")
        self.assertEqual(("remote_api_confirmed", "remote_api"), tuple(_seen[0]),
                         "路由侧没有把 _SKIP_TYPES 注入检索层")

    def test_05_no_pool_passes_not_blocks(self):
        iw = _mk_iw(None, [])
        iw._detect_experience_route(_Ctx("什么是量子纠缠"))
        self.assertEqual(1, self._calls["n"], "池不可用时探针返回 0 ⇒ 放行；绝不能因探针故障反而拦掉")

    def test_06_non_knowledge_question_untouched(self):
        logs = []
        iw = _mk_iw(_Pool([_N("量子", ["量子"])]), logs)
        iw._detect_experience_route(_Ctx("帮我查看今天的日程安排"))
        self.assertEqual([], self._guard_lines(logs), "非纯知识问题不该走这道闸")
        self.assertEqual(1, self._calls["n"], "非纯知识问题的原有路径不变（仍然查经验库）")

    def test_07_strong_signal_excluded(self):
        logs = []
        iw = _mk_iw(_Pool([_N("量子", ["量子"])]), logs)
        iw._detect_experience_route(_Ctx("什么是矛盾"))
        self.assertEqual([], self._guard_lines(logs),
                         "含『矛盾』等强信号的问题仍走原排除逻辑（不进纯知识分支）")

    def test_08_local_inference_count_symmetric(self):
        iw = _mk_iw(_Pool([_N("无关", ["天气"])]), [])
        iw._detect_experience_route(_Ctx("什么是量子纠缠"))
        self.assertEqual(1, len(self._rec),
                         "每次纯知识提问记 1 次 KIND_RULE —— 与 406 前频次一致（放行也不许丢计数）")

    def test_09_hit_count_probe_is_pure(self):
        pool = _Pool([_N("量子纠缠", ["量子", "纠缠"])])
        iw = _mk_iw(pool, [])
        self.assertEqual(1, iw._m406_knowledge_hit_count("什么是量子纠缠"))
        self.assertEqual(1, pool.calls, "探针只做一次有界采样，不得多次查询")

    def test_10_hit_count_zero_without_terms(self):
        iw = _mk_iw(_Pool([_N("量子", ["量子"])]), [])
        self.assertEqual(0, iw._m406_knowledge_hit_count("   "), "提不出核心词应返回 0 而不是抛")

    def test_11_min_hits_reads_config(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld as P

        self.assertEqual(3, P._m406_knowledge_guard_params()[0])
        with patch("config.EXPERIENCE_KNOWLEDGE_GUARD_MIN_HITS", 5):
            self.assertEqual(5, P._m406_knowledge_guard_params()[0])

    def test_12_scan_limit_floor(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld as P

        with patch("config.EXPERIENCE_KNOWLEDGE_GUARD_SCAN_LIMIT", 0):
            self.assertGreaterEqual(P._m406_knowledge_guard_params()[1], 50,
                                    "采样上限必须设下限，否则配成 0 会退化成不采样")


class TestD_ThresholdAndDiagnostics(unittest.TestCase):
    """阈值不许动；诊断快照必须留下**未截断**的真实分数。"""

    def test_01_threshold_attribute_read(self):
        from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience

        self.assertEqual(0.5, get_reasoning_experience()._min_confidence,
                         "运行时阈值必须仍是 0.5（任务书：本刀不改阈值）")

    def test_02_source_hardcodes_point_five(self):
        src = _read(_RX)
        self.assertIn("self._min_confidence = 0.5", src,
                      "任务书要求本刀**不改**阈值：源码里应仍是 0.5")
        self.assertNotIn("self._min_confidence = 0.75", src)

    def test_03_diagnostics_fields(self):
        from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience

        rx = get_reasoning_experience()
        rx.search("什么是量子纠缠")
        d = getattr(rx, "last_search_diagnostics", None)
        self.assertIsInstance(d, dict)
        self.assertGreaterEqual(set(d), {"best_score", "threshold", "n_experiences", "hit"})

    def test_04_best_score_not_truncated(self):
        from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience

        rx = get_reasoning_experience()
        rx.search("光速是什么")
        d = rx.last_search_diagnostics
        self.assertIsInstance(d["best_score"], float)
        self.assertGreaterEqual(d["best_score"], 0.0)
        self.assertEqual(0.5, d["threshold"])

    def test_05_hit_flag_matches_return(self):
        from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience

        rx = get_reasoning_experience()
        hit = rx.search("什么是量子纠缠")
        self.assertEqual(hit is not None, bool(rx.last_search_diagnostics["hit"]),
                         "hit 标记必须与返回值一致")

    def test_06_search_contract_unchanged(self):
        from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience

        rx = get_reasoning_experience()
        hit = rx.search("什么是量子纠缠")
        if hit is None:
            self.skipTest("当前经验库无 >=0.5 命中，跳过返回契约校验")
        self.assertIn("confidence", hit, "返回契约不变：调用方仍靠 confidence 判阈值")
        self.assertEqual(round(hit["confidence"], 2), hit["confidence"])


class TestE_Structure(unittest.TestCase):
    """结构判据：埋点位置错了，3 天数据就等于白跑。"""

    def setUp(self):
        self.fn = _func_node(_IW, "_detect_experience_route")
        self.assertIsNotNone(self.fn, "PulseInnerWorld 缺 _detect_experience_route")

    def _call_lines(self, method):
        out = []
        for x in ast.walk(self.fn):
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == method:
                out.append(x.lineno)
        return sorted(out)

    def test_01_trace_after_search(self):
        s = self._call_lines("search")
        t = self._call_lines("_m406_trace_experience_route")
        self.assertTrue(s, "函数里应有一次 search 调用")
        self.assertTrue(t, "函数里应有一次埋点调用")
        # search 调用有多次（默认算子/回退），埋点必须在**所有** search 之后
        self.assertGreaterEqual(t[0], min(s))
        # 精确定位：埋点应紧跟 |_exp_match = ...search(...)| 那一次
        # ★407刀2：原断言按字符串匹配 `_reasoning_exp.search(ctx.question)`，
        #   刀2 加了 `exclude_types=_SKIP_TYPES` 实参后必然红 —— 那是**钉措辞**，
        #   不是本例要守的语义（本例守的是"埋点紧跟真的那次检索"）。
        #   改成 AST：只要求存在对 `_reasoning_exp.search` 的调用，不锁实参写法；
        #   实参正确性由 test_m407_k2_type_gate::test_51 单独守。
        _found = None
        for _n in ast.walk(self.fn):
            if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                    and _n.func.attr == "search"
                    and isinstance(_n.func.value, ast.Name)
                    and _n.func.value.id == "_reasoning_exp"):
                _found = _n
                break
        self.assertIsNotNone(_found, "找不到对 _reasoning_exp.search 的调用")
        self.assertLessEqual(_found.lineno, t[0], "埋点早于检索 ⇒ 埋的是上一轮的读数")

    def test_02_trace_before_skip_types(self):
        t = self._call_lines("_m406_trace_experience_route")
        skip = [x.lineno for x in ast.walk(self.fn)
                if isinstance(x, ast.Compare)
                and any(isinstance(o, ast.In) for o in x.ops)
                and any(isinstance(c, ast.Name) and c.id == "_SKIP_TYPES" for c in x.comparators)]
        self.assertTrue(skip, "类型闸判断应仍在位（本刀不动 _SKIP_TYPES）")
        self.assertLess(t[0], skip[0],
                        "埋点必须在类型闸之前 —— 否则「命中却被类型挡」整类样本在分布里消失")

    def test_03_trace_after_hit_threshold(self):
        t = self._call_lines("_m406_trace_experience_route")
        conf = [x.lineno for x in ast.walk(self.fn)
                if isinstance(x, ast.Compare)
                and any(isinstance(c, ast.Constant) and c.value == 0.5 for c in x.comparators)]
        if conf:
            self.assertLess(t[0], min(conf),
                            "埋点应在阈值判断之前 —— 未命中也要留样本，否则低分段全丢")

    def test_04_skip_types_still_remote(self):
        seg = ast.get_source_segment(_src(_IW), self.fn) or ""
        self.assertIn('_SKIP_TYPES = ("remote_api_confirmed", "remote_api")', seg,
                      "本刀不动类型闸：远程经验仍不在本地执行")

    def test_05_guard_calls_config_params(self):
        self.assertTrue(self._call_lines("_m406_knowledge_guard_params"),
                        "守卫必须读配置参数，不能硬编码 3")

    def test_06_no_hardcoded_min_hits(self):
        seg = ast.get_source_segment(_src(_IW), self.fn) or ""
        self.assertNotIn("_m406_hits >= 3", seg, "命中下限不得写死 3")
        self.assertIn("_m406_hits >= _m406_min_hits", seg)

    def test_07_config_constants_present(self):
        cfg = _read(_CFG)
        for k in ("EXPERIENCE_ROUTE_TRACE",
                  "EXPERIENCE_KNOWLEDGE_GUARD_MIN_HITS",
                  "EXPERIENCE_KNOWLEDGE_GUARD_SCAN_LIMIT"):
            self.assertIn(k, cfg, "config.py 缺常量：%s" % k)
        self.assertIn("EXPERIENCE_KNOWLEDGE_GUARD_MIN_HITS = 3", cfg)

    def test_08_methods_exist(self):
        for m in ("_m406_route_trace_on", "_m406_knowledge_guard_params",
                  "_m406_knowledge_hit_count", "_m406_trace_experience_route"):
            self.assertIsNotNone(_func_node(_IW, m), "缺方法：%s" % m)

    def test_09_no_silent_except_added(self):
        """新增的 except 必须带 silent_exc（cw2 门禁口径的手工复核）。"""
        for m in ("_m406_route_trace_on", "_m406_knowledge_guard_params",
                  "_m406_knowledge_hit_count", "_m406_trace_experience_route"):
            node = _func_node(_IW, m)
            for x in ast.walk(node):
                if isinstance(x, ast.ExceptHandler):
                    body = ast.dump(ast.Module(body=x.body, type_ignores=[]))
                    self.assertIn("silent_exc", body, "%s 的 except 未走 silent_exc" % m)


if __name__ == "__main__":
    unittest.main()
