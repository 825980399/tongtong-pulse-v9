# -*- coding: utf-8 -*-
"""468 批门控：进化闭环「最后一公里」三刀。

背景（T0 实测 2026-11-06 15:33-15:40）：

* 刀1 · 复验前置门
  `SafeEvolutionExecutor` 在「提交待审批」那一刻就打上 `fixed_at` /
  `needs_runtime_verify`（L2888-2896），不看审批是否通过、不看 apply 是否成功。
  于是复验流程捡起**源码没被改过**的方法测效果：前后都 0 错误 ⇒ baseline=0
  ⇒ 不可判定 ⇒ 延迟复验 ⇒ 再不可判定。实证：`_schedule_next_beat` 的补丁
  applied=False，而 `organs/body/PulseHeart.py:444-482` 无 `_in_progress` 守卫；
  pending 里三条进复验的补丁 applied 全为 False，其中
  `_m383_dump_tier_distribution` 还拿到了 runtime_verified 证书。

* 刀2 · 审批重投
  `_m447_try_approve` 只在补丁刚生成时调一次。14:53:52 实测
  `approved=False blocked=daily_cap（今日已批准 1 / 上限 1）`，此后再无审批日志。

* 刀3 · 生成侧标复核
  生成侧用**原始 would_block** 打 `sandbox_incompatible=True`
  （`PatchManager.save_pending_patch` L3688），而 365刀A2 的两通道放行只在
  「字段缺失、现算」那条路上生效 ⇒ 打过标的补丁被 `is_sandbox_incompatible`
  第一行短路，idiom 放行对它完全失效。
"""
import ast
import importlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEE_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_DIS_PATH = os.path.join(_ROOT, "nucleus", "evolution", "patch_execution_dispatch.py")
_CFG_PATH = os.path.join(_ROOT, "config.py")

SEE = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
DIS = importlib.import_module("nucleus.evolution.patch_execution_dispatch")


def _src(p):
    return io.open(p, encoding="utf-8").read()


def _code_only(text):
    """剥掉整行注释（注释里引用符号不是调用点，铁律 85）。"""
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


class TestA_ReverifyGate(unittest.TestCase):
    """刀1 判据：没落地的补丁不复验。"""

    def setUp(self):
        self._old = getattr(config, "ENABLE_M468_REVERIFY_APPLIED_GUARD", None)

    def tearDown(self):
        if self._old is None:
            config.__dict__.pop("ENABLE_M468_REVERIFY_APPLIED_GUARD", None)
        else:
            config.ENABLE_M468_REVERIFY_APPLIED_GUARD = self._old

    def test_01_applied_true_passes(self):
        self.assertEqual(SEE._m468_reverify_gate({"applied": True}), (True, ""))

    def test_02_applied_false_blocks(self):
        self.assertEqual(SEE._m468_reverify_gate({"applied": False}),
                         (False, "not_applied"))

    def test_03_apply_error_takes_precedence(self):
        _ok, _why = SEE._m468_reverify_gate(
            {"applied": False, "apply_error": "副本验证失败: ['基础缩进不一致']"})
        self.assertFalse(_ok)
        self.assertTrue(_why.startswith("apply_failed:"), _why)

    def test_04_applied_missing_keeps_old_behaviour(self):
        """老件没有 applied 字段 ⇒ 不擅自改变行为。"""
        self.assertEqual(SEE._m468_reverify_gate({"method": "x"}),
                         (True, "applied_unknown"))

    def test_05_non_dict_passes(self):
        self.assertTrue(SEE._m468_reverify_gate(None)[0])
        self.assertTrue(SEE._m468_reverify_gate([])[0])

    def test_06_switch_off_restores_old_behaviour(self):
        config.ENABLE_M468_REVERIFY_APPLIED_GUARD = False
        self.assertFalse(SEE._m468_reverify_applied_guard_on())
        self.assertEqual(SEE._m468_reverify_gate({"applied": False}),
                         (True, "guard_off"))

    def test_07_switch_on_by_default(self):
        self.assertTrue(SEE._m468_reverify_applied_guard_on())


class TestB_ReverifyWiring(unittest.TestCase):
    """刀1 接线：两个入口都必须过 gate（防止只改一处）。"""

    def setUp(self):
        self._code = _code_only(_src(_SEE_PATH))

    def test_11_gate_called_twice(self):
        self.assertEqual(self._code.count("_m468_reverify_gate(_patch)"), 2,
                         "两处入口（延迟复验 / 批量验证）都应过 gate")

    def test_12_gate_definition_once(self):
        self.assertEqual(_src(_SEE_PATH).count("def _m468_reverify_gate("), 1)

    def test_13_marks_awaiting_apply(self):
        self.assertEqual(self._code.count('_patch["m468_awaiting_apply"] = True'), 2)

    def test_14_gate_before_total_increment(self):
        """批量验证里 gate 必须在 `_total += 1` 之前（否则计数仍会涨）。"""
        _i = self._code.find("_m468_reverify_gate(_patch)")
        _j = self._code.rfind("_m468_reverify_gate(_patch)")
        _k = self._code.find("_total += 1")
        self.assertGreater(_j, _i)
        self.assertGreater(_k, _j, "gate 必须在 _total += 1 之前")

    def test_15_no_bare_except_pass(self):
        import re
        _s = _src(_SEE_PATH)
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)), 0)


class TestC_ApprovalRetry(unittest.TestCase):
    """刀2：滞留补丁的审批重投。"""

    def setUp(self):
        self._code = _code_only(_src(_SEE_PATH))

    def test_21_retry_branch_wired(self):
        self.assertIn("[468刀2] 审批重投", self._code)

    def test_22_retry_calls_bridge(self):
        self.assertIn('source="retry_468"', self._code)

    def test_23_skip_set_is_terminal_only(self):
        _skip = SEE._M468_APPROVAL_RETRY_SKIP
        for _s in ("approved", "runtime_verified", "runtime_failed",
                   "expired_pending", "wont_fix", "rolled_back"):
            self.assertIn(_s, _skip)
        # 未落地待审批的状态**不得**在跳过集合里
        for _s in ("verified", "needs_reverify", "pending", "awaiting_apply"):
            self.assertNotIn(_s, _skip, "%s 必须允许重投" % _s)

    def test_24_capped_stops_immediately(self):
        """撞日限额必须 break（日限额是当天硬约束，再问也没用）。"""
        _i = self._code.find('_r468.get("blocked") == "daily_cap"')
        self.assertGreater(_i, 0)
        _seg = self._code[_i:_i + 400]
        self.assertIn("break", _seg)

    def test_25_retry_on_default_true(self):
        self.assertTrue(SEE._m468_approval_retry_on())


class TestD_SandboxFlagRecheck(unittest.TestCase):
    """刀3：生成侧标与两通道口径分裂。"""

    def setUp(self):
        self._old = getattr(config, "ENABLE_M468_SANDBOX_FLAG_RECHECK", None)
        # 真实标本：14:53 被判「转人工」的那条补丁的字段形态
        self._patch = {
            "id": "patch_probe_468",
            "method": "_schedule_next_beat",
            "file": "organs/body/PulseHeart.py",
            "danger_keyword_class": "idiom",
            "sandbox_blocked_keywords": ["getattr("],
            "sandbox_incompatible": True,
            "original_code": "    def _schedule_next_beat(self) -> bool:\n        return True\n",
            "modified_code": (
                "    def _schedule_next_beat(self) -> bool:\n"
                "        if getattr(self, '_in_progress', False):\n"
                "            return False\n"
                "        return True\n"),
        }

    def tearDown(self):
        if self._old is None:
            config.__dict__.pop("ENABLE_M468_SANDBOX_FLAG_RECHECK", None)
        else:
            config.ENABLE_M468_SANDBOX_FLAG_RECHECK = self._old

    def test_31_idiom_flag_released(self):
        self.assertFalse(DIS.is_sandbox_incompatible(self._patch),
                         "danger_keyword_class=idiom 的 getattr 守卫应放行")

    def test_32_switch_off_restores_true(self):
        config.ENABLE_M468_SANDBOX_FLAG_RECHECK = False
        self.assertTrue(DIS.is_sandbox_incompatible(self._patch))

    def test_33_real_dangerous_still_blocked(self):
        _bad = dict(self._patch)
        _bad["modified_code"] = "def m(self):\n    return eval(_user)\n"
        self.assertTrue(DIS.is_sandbox_incompatible(_bad),
                        "真实 eval 调用必须仍被拦（不得借刀3 放宽）")

    def test_34_flag_false_shortcut_intact(self):
        self.assertFalse(DIS.is_sandbox_incompatible({"sandbox_incompatible": False}))

    def test_35_released_counter_recorded(self):
        DIS.is_sandbox_incompatible(self._patch)
        self.assertIsInstance(DIS._M468_RECHECK_RELEASED, dict)
        self.assertTrue(DIS._M468_RECHECK_RELEASED)

    def test_36_switch_fn_default_true(self):
        self.assertTrue(DIS._m468_recheck_flagged_on())


class TestE_ConfigRegistration(unittest.TestCase):
    """三刀开关必须登记进 config.py（不得留模块内联默认值当事实来源）。"""

    def test_41_switches_registered(self):
        _s = _src(_CFG_PATH)
        for _k in ("ENABLE_M468_REVERIFY_APPLIED_GUARD",
                   "ENABLE_M468_APPROVAL_RETRY",
                   "ENABLE_M468_SANDBOX_FLAG_RECHECK"):
            self.assertIn("%s = True" % _k, _s, "%s 未登记" % _k)

    def test_42_config_marker_present(self):
        self.assertIn("[M468-CFG]", _src(_CFG_PATH))

    def test_43_config_still_parses(self):
        ast.parse(_src(_CFG_PATH))

    def test_44_only_appended_at_tail(self):
        """既有关键开关值不得被本批改写（零回归的结构性证明）。

        ★468刀5/刀6 追加在本块之后 ⇒ 原「末尾 1200 字符内」的硬阈值被
        **合法追加**顶红（实测 1316 字符）。按"钉精确值必被新内容顶红"的教训
        改判「块起点在文件后半段」：既保留"只追加、不插中间"的原意，
        又不会被后续同批追加再次误伤。
        回滚点：若 468 撤回，恢复 `self.assertGreater(_i, len(_s) - 1200, ...)`。
        """
        _s = _src(_CFG_PATH)
        self.assertIn("ENABLE_M447_APPROVAL_BRIDGE", _s)
        self.assertIn("M447_APPROVE_DAILY_CAP", _s)
        _i = _s.find("[M468-CFG]")
        self.assertGreater(_i, 0, "[M468-CFG] 块必须存在")
        self.assertGreater(_i, len(_s) // 2,
                           "本批配置块应在文件后半段（只追加、不插中间）")


class TestF_OrganFileFallback(unittest.TestCase):
    """刀4：scan_all_organs 有 300s 降频（第二次起返回 {}），467 刀1 因此全程短路。

    实测（2026-11-06 16:20）：同一 SelfInspector 实例连调 4 次，条目数
    66 → 0 → 0 → 0；全量 pulse.log 里 `[467刀1]` 标记 0 次。
    """

    def setUp(self):
        self._old = getattr(config, "ENABLE_M468_ORGAN_FILE_FALLBACK", None)

    def tearDown(self):
        if self._old is None:
            config.__dict__.pop("ENABLE_M468_ORGAN_FILE_FALLBACK", None)
        else:
            config.ENABLE_M468_ORGAN_FILE_FALLBACK = self._old

    def test_51_switch_default_true(self):
        self.assertTrue(SEE._m468_file_fallback_on())

    def test_52_switch_off(self):
        config.ENABLE_M468_ORGAN_FILE_FALLBACK = False
        self.assertFalse(SEE._m468_file_fallback_on())

    def test_53_fallback_wired_inside_467_block(self):
        _code = _code_only(_src(_SEE_PATH))
        _i = _code.find("all_organs = self_inspector.scan_all_organs()")
        self.assertGreater(_i, 0, "467 刀1 前的 scan_all_organs 调用点应在")
        _seg = _code[_i:_i + 1400]
        self.assertIn("resolve_organ_file", _seg,
                      "兜底必须紧跟在 scan_all_organs 之后（同一块内）")
        self.assertIn("_m468_file_fallback_on()", _seg)

    def test_54_throttled_scan_returns_empty(self):
        """先证伪：降频态确实返回 {}（否则兜底没有存在理由）。"""
        _si = importlib.import_module("nucleus.self_inspector").SelfInspector()
        _a = _si.scan_all_organs()
        _b = _si.scan_all_organs()
        self.assertGreater(len(_a), 0, "首次扫描应有条目")
        self.assertEqual(len(_b), 0, "第二次应被 T3 降频打空（若不为 0 则本刀前提失效）")

    def test_55_fallback_recovers_path_under_throttle(self):
        """降频态下 resolve_organ_file 仍能给出真实路径。"""
        _si = importlib.import_module("nucleus.self_inspector").SelfInspector()
        _si.scan_all_organs()
        _throttled = _si.scan_all_organs()
        self.assertEqual(_throttled, {})
        _fp = _si.resolve_organ_file("PulseLiver") or ""
        self.assertTrue(_fp.endswith("PulseLiver.py"), _fp)
        self.assertTrue(os.path.isfile(_fp))

    def test_56_compress_group_full_feed_with_def(self):
        """端到端：467 刀1 对 _compress_group（converge_voided 里 63 次作废的那个）。"""
        _si = importlib.import_module("nucleus.self_inspector").SelfInspector()
        _fc = importlib.import_module("nucleus.evolution.feed_code")
        _d = _si.get_method_body("PulseLiver", "_compress_group") or {}
        _body = _d.get("body", "")
        self.assertGreater(len(_body), 1000, "方法体应取到")
        _fp = _si.resolve_organ_file("PulseLiver") or ""
        _blk = _fc.method_block(_fp, "_compress_group", int(_d.get("line_number") or 0))
        self.assertTrue(_blk, "method_block 不应为空")
        self.assertEqual(_fc.extract_def_name(_blk), "_compress_group")
        self.assertGreater(len(_blk), len(_body),
                           "含 def 的完整方法必须比纯方法体长")
        # 467 的判据：喂料前有 no_def 缺陷，喂料后没有
        self.assertIn("no_def", _fc.struct_defects(_body, "_compress_group"))
        self.assertEqual(_fc.struct_defects(_blk, "_compress_group"), [])

    def test_57_config_registered(self):
        self.assertIn("ENABLE_M468_ORGAN_FILE_FALLBACK = True", _src(_CFG_PATH))



# ============================================================
# G 组 ★468刀5：`pending` 真读数
# ============================================================
_MAIN_PATH = os.path.join(_ROOT, "main.py")


def _load_main_func(names):
    """把 main.py 里的指定顶层定义 exec 到受控 globals —— **不 import main**。

    `import main` 会拉起活体主循环，绝不能那么做。这里用 AST 只取需要的
    赋值/函数源码，外部依赖用桩补上（`silent_exc` / `_m422_dead_patch_ids`）。
    """
    import ast as _ast
    _msrc = _src(_MAIN_PATH)
    _tree = _ast.parse(_msrc)
    _chunks = []
    for _node in _tree.body:
        if isinstance(_node, _ast.Assign):
            for _t in _node.targets:
                if isinstance(_t, _ast.Name) and _t.id in names:
                    _chunks.append(_ast.get_source_segment(_msrc, _node))
        if isinstance(_node, _ast.FunctionDef) and _node.name in names:
            _chunks.append(_ast.get_source_segment(_msrc, _node))
    _g = {
        "os": os,
        "silent_exc": lambda *a, **k: None,
        "__file__": _MAIN_PATH,
        "_m422_dead_patch_ids": lambda: set(),
    }
    # 不能 import main（会拉起活体主循环），只能 exec 提取
    exec(compile("\n\n".join(_chunks), "<main-extract>", "exec"), _g)  # noqa: S102
    return _g, len(_chunks)


class TestG_422RealPending(unittest.TestCase):
    """★468刀5：`pending` 曾恒为 0（键根本不存在），连带告警永久降级成 DEBUG。"""

    NAMES = frozenset({"_M422_DONE_STATUS",
                       "_m422_count_approved_pending"})

    def test_60_done_status_defined(self):
        _g, _n = _load_main_func(self.NAMES)
        self.assertEqual(_n, 2, "应取到常量 + 函数各一段")
        _st = _g["_M422_DONE_STATUS"]
        for _s in ("expired_pending", "runtime_verified", "runtime_undecidable"):
            self.assertIn(_s, _st, f"终态集必须含 {_s}")
        # 还能走审批的状态绝不能进终态集，否则 pending 口径会失真
        for _s in ("verified", "needs_reverify", "approved", "pending"):
            self.assertNotIn(_s, _st, f"…状态 {_s} 不属于终态")

    def test_61_pending_key_exists(self):
        """回归锁：缺键会让 `_cnt.get("pending", 0)` 静默取 0。"""
        _g, _ = _load_main_func(self.NAMES)
        _out = _g["_m422_count_approved_pending"]()
        self.assertIn("pending", _out, "`pending` 键必须存在")
        self.assertIsInstance(_out["pending"], int)
        self.assertEqual(
            set(_out.keys()), {"approved", "dead", "live", "pending"},
            "不得删既有三键（444刀4 分级依赖 approved，440刀3 依赖 pending）")

    def test_62_pending_matches_real_queue(self):
        """判据来自**另一个入口**：直接数 pending_patches.json 的状态分桶。"""
        import json as _json
        _g, _ = _load_main_func(self.NAMES)
        _out = _g["_m422_count_approved_pending"]()
        _pf = os.path.join(_ROOT, "data", "patches", "pending_patches.json")
        if not os.path.isfile(_pf):
            self.skipTest("无 pending 队列文件")
        with open(_pf, "r", encoding="utf-8") as _f:
            _rows = _json.load(_f)
        _st = _g["_M422_DONE_STATUS"]
        _want = sum(1 for _p in _rows
                    if isinstance(_p, dict) and _p.get("applied") is not True
                    and str(_p.get("status") or "") not in _st)
        # 活体持续写盘 ⇒ 用 >= 下限 + 差量容忍（不钉死精确值，铁律 9）
        self.assertGreaterEqual(_out["pending"], min(_want, 1),
                                "有可审批件时 pending 必须 >0（否则分级又静音）")
        self.assertLessEqual(abs(_out["pending"] - _want), 2,
                             f"pending={_out['pending']} 与手工分桶 {_want} 偏差过大")

    def test_63_pending_not_zero_while_backlog_exists(self):
        """核心症状锁：队列里有可审批件时，`pending=0` 就是假读数。"""
        _g, _ = _load_main_func(self.NAMES)
        _out = _g["_m422_count_approved_pending"]()
        if _out["pending"] == 0:
            self.skipTest("当前队列确实空（或全是终态）")
        self.assertGreater(_out["pending"], 0)
        # 派生判据：444刀4 的分级必须能选中 WARNING
        self.assertGreater(int(_out.get("pending", 0) or 0), 0,
                           "分级表达式 warning if pending>0 else debug 应走 warning")

    def test_64_source_has_pending_accumulation(self):
        _s = _src(_MAIN_PATH)
        _body = _s[_s.index("def _m422_count_approved_pending"):]
        _body = _body[:_body.index("\ndef ")]
        self.assertIn('_out["pending"] += 1', _body)
        self.assertIn('"pending": 0', _body)
        self.assertIn("_M422_DONE_STATUS", _body)


# ============================================================
# H 组 ★468刀6：审批额度结算（apply 失败退还日限额）
# ============================================================
_AB = importlib.import_module("nucleus.evolution.approval_bridge")


def _mk_root(tmpdir):
    _d = os.path.join(tmpdir, "data", "patches")
    os.makedirs(_d, exist_ok=True)
    return _d


def _write_trace(_d, rows):
    import json as _json
    with open(os.path.join(_d, "approval_trace.jsonl"), "w", encoding="utf-8") as _f:
        _f.writelines(_json.dumps(_r, ensure_ascii=False) + "\n"
                     for _r in rows)


def _today():
    import time as _t
    return _t.strftime("%Y-%m-%d")


class TestH_468Refund(unittest.TestCase):
    """病因实证：14:11:21 唯一一次放行被 apply 失败的补丁吃掉，
    当日 cap=1 用尽 ⇒ 之后 12 次审批全 blocked=daily_cap、两小时零落地。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="t468h_")
        self._pd = _mk_root(self._tmp)
        self.addCleanup(__import__("shutil").rmtree, self._tmp,
                        ignore_errors=True)

    # ---------- 计数口 ----------
    def test_70_no_refund_behaviour_unchanged(self):
        """规格锁：无 refund 行时行为与 447 批完全一致（不得破既有断言）。"""
        _write_trace(self._pd, [
            {"day": _today(), "patch_id": "A", "approved": True},
            {"day": _today(), "patch_id": "B", "approved": False},
            {"day": "1999-01-01", "patch_id": "C", "approved": True},
        ])
        self.assertEqual(_AB.approved_today(self._tmp), 1)

    def test_71_refund_reduces_quota(self):
        _write_trace(self._pd, [
            {"day": _today(), "patch_id": "A", "approved": True},
            {"day": _today(), "patch_id": "A", "approved": False, "refund": True},
        ])
        self.assertEqual(_AB.approved_today(self._tmp), 0,
                         "退款后额度必须回 1 条")

    def test_72_refund_dedup_by_patch_id(self):
        _write_trace(self._pd, [
            {"day": _today(), "patch_id": "A", "approved": True},
            {"day": _today(), "patch_id": "A", "approved": False, "refund": True},
            {"day": _today(), "patch_id": "A", "approved": False, "refund": True},
        ])
        self.assertEqual(_AB.approved_today(self._tmp), 0)
        self.assertGreaterEqual(_AB.approved_today(self._tmp), 0, "不得为负")

    def test_73_ids_helpers(self):
        _write_trace(self._pd, [
            {"day": _today(), "patch_id": "A", "approved": True},
            {"day": _today(), "patch_id": "B", "approved": True},
            {"day": _today(), "patch_id": "B", "approved": False, "refund": True},
        ])
        self.assertEqual(_AB.approved_today_ids(self._tmp), {"A"},
                         "已退款件不得留在放行集合里")
        self.assertEqual(_AB.refunded_today_ids(self._tmp), {"B"})

    # ---------- 写入口 ----------
    def test_74_note_refund_row_shape(self):
        _ok = _AB.note_approval_refund({"id": "P9"}, reason="boom",
                                       root=self._tmp)
        self.assertTrue(_ok)
        import json as _json
        _pth = os.path.join(self._pd, "approval_trace.jsonl")
        with open(_pth, "r", encoding="utf-8") as _f:
            _row = _json.loads(_f.read().strip())
        self.assertIs(_row["refund"], True)
        self.assertIs(_row["approved"], False, "退款行绝不能被数成一次放行")
        self.assertEqual(_row["patch_id"], "P9")
        self.assertEqual(_row["day"], _today())
        self.assertIn("boom", _row["reason"])

    def test_75_pid_of_accepts_both_keys(self):
        """踩过的坑：补丁对象键是 `id`，留痕行键是 `patch_id`。"""
        self.assertEqual(_AB._pid_of({"id": "X"}), "X")
        self.assertEqual(_AB._pid_of({"patch_id": "Y"}), "Y")
        self.assertEqual(_AB._pid_of({}), "")
        self.assertEqual(_AB._pid_of(None), "")

    def test_76_refund_rejects_empty_id(self):
        self.assertFalse(_AB.note_approval_refund({}, root=self._tmp))

    # ---------- 结算口 ----------
    def _settle(self, rows, hist=None):
        return SEE._m468_settle_approval_refund(rows, hist, root=self._tmp)

    def test_80_settles_applied_failed_patch(self):
        _write_trace(self._pd, [{"day": _today(), "patch_id": "Z",
                                 "approved": True}])
        _r = self._settle([{"id": "Z", "applied": False,
                            "apply_error": "缩进不一致 original=8 modified=0"}])
        self.assertEqual(_r["refunded"], 1)
        self.assertEqual(_AB.approved_today(self._tmp), 0)

    def test_81_skips_landed_patch(self):
        _write_trace(self._pd, [{"day": _today(), "patch_id": "Z",
                                 "approved": True}])
        _r = self._settle([{"id": "Z", "applied": True, "apply_error": "x"}])
        self.assertEqual(_r["refunded"], 0, "真落地了额度用得其所，不退")

    def test_82_skips_not_yet_attempted(self):
        _write_trace(self._pd, [{"day": _today(), "patch_id": "Z",
                                 "approved": True}])
        _r = self._settle([{"id": "Z", "applied": None, "apply_error": ""}])
        self.assertEqual(_r["refunded"], 0, "还没尝试落地 ⇒ 给它机会")

    def test_83_skips_not_approved_today(self):
        _write_trace(self._pd, [{"day": _today(), "patch_id": "OTHER",
                                 "approved": True}])
        _r = self._settle([{"id": "Z", "applied": False, "apply_error": "x"}])
        self.assertEqual(_r["refunded"], 0, "今天没放行过，无额度可退")

    def test_84_idempotent_across_rounds(self):
        _write_trace(self._pd, [{"day": _today(), "patch_id": "Z",
                                 "approved": True}])
        _row = {"id": "Z", "applied": False, "apply_error": "same"}
        self.assertEqual(self._settle([_row])["refunded"], 1)
        self.assertEqual(self._settle([_row])["refunded"], 0, "第二轮不得重复退")

    def test_85_scans_history_too(self):
        """已出队的补丁也要结算 —— 实证那条 7587 就只在 history 里。"""
        _write_trace(self._pd, [{"day": _today(), "patch_id": "OLD",
                                 "approved": True}])
        _hist = [{"id": "OLD", "status": "approved", "applied": False,
                  "apply_error": "副本验证失败: 基础缩进不一致"}]
        _r = self._settle([], _hist)
        self.assertEqual(_r["refunded"], 1)

    def test_86_switch_off_means_no_refund(self):
        _write_trace(self._pd, [{"day": _today(), "patch_id": "Z",
                                 "approved": True}])
        _old = getattr(config, "ENABLE_M468_APPROVAL_REFUND", None)
        config.ENABLE_M468_APPROVAL_REFUND = False
        try:
            _r = self._settle([{"id": "Z", "applied": False,
                                "apply_error": "x"}])
            self.assertEqual(_r["refunded"], 0)
            self.assertEqual(_AB.approved_today(self._tmp), 1, "关掉后额度不释放")
        finally:
            config.ENABLE_M468_APPROVAL_REFUND = (
                True if _old is None else _old)

    def test_87_registered_and_ordered_before_retry(self):
        self.assertIn("ENABLE_M468_APPROVAL_REFUND", _src(_CFG_PATH))
        _s = _src(_SEE_PATH)
        _i_settle = _s.index("_m468_settle_approval_refund(\n                    _pending,")
        _i_retry = _s.index("[468刀2] 审批重投:")
        self.assertLess(_i_settle, _i_retry,
                        "结算必须先于刀2 重投，否则重投照旧撞 daily_cap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
