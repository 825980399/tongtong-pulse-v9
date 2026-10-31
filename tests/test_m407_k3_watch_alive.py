# -*- coding: utf-8 -*-
"""407 刀3 判据：`expired_pending` 不再算健康，观察位数量如实反映可派单数。

十组断言：
  A `_m407_watch_alive` 判据：expired → False；其他状态 → True；非 dict → False。
  B `alive_watch_ids`：只吐「在 pending 且未死」的位。
  C 健康判据：**同样一份 pending**，407 前报 healthy、407 后不再报 healthy
    —— 这是本刀的核心语义，A/B 组都只是它的零件。
  D 补位跳过 expired：3 条 expired + 2 条 eligible ⇒ 只补那 2 条。
  E `only_expired` 新状态：候选全是死状态时**不与** `no_candidate` 合并
    （一个该重新排发、一个该造补丁，合并会把人引到错方向）。
  F 数量如实：`n_active` 与 `n_eligible` 两个口径同时给，全死时 `n_eligible=0`。
  G 开关回退：`WATCH_HEAL_ALIVE_ONLY=False` ⇒ 补回 expired（407 前行为）。
  H 派单侧**未动**：`_m339_o3_dispatch_ready` 对 expired 仍返 `(False, "expired")`，
    且判据顺序未变（任务书明令「派单侧逻辑不动」）。
  I 结构（AST）：健康判据里出现 `_m407_watch_alive`；补位循环里
    `_skipped.append` 早于 `_picks.append`。
  J 死状态清单：`WATCH_DEAD_STATUSES == ("expired_pending",)` —— 不许顺手加别的。

★为什么 C 组要拿同一个 fixture 跑两个开关值：
  "expired 不算健康"这个结论只有在**同输入、只变开关**时才能归因到本刀；
  否则分不清是刀3 生效了，还是我造的 fixture 本来就不会命中健康分支。
"""
import ast
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ZB = os.path.join(_ROOT, "nucleus", "evolution", "zero_break_judge.py")
_DSP = os.path.join(_ROOT, "nucleus", "evolution", "patch_execution_dispatch.py")

_CACHE = {}


def _tree(path):
    if path not in _CACHE:
        _CACHE[path] = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    return _CACHE[path]


def _func(path, name):
    for n in ast.walk(_tree(path)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _row(pid, status, issue_type="dead_code"):
    return {"patch_id": pid, "issue_type": issue_type, "status": status}


#: 三条 expired + 两条活的 —— 全批最常用的 fixture
MIXED = [
    _row("o3_exp_a", "expired_pending"),
    _row("o3_exp_b", "expired_pending"),
    _row("o3_exp_c", "expired_pending", "dead_code_candidate"),
    _row("o3_live_a", "pending"),
    _row("o3_live_b", "runtime_verified"),
]
ONLY_DEAD = [r for r in MIXED if r["patch_id"].startswith("o3_exp")]
ONLY_LIVE = [r for r in MIXED if r["patch_id"].startswith("o3_live")]


class _Base(unittest.TestCase):
    def setUp(self):
        from nucleus.evolution import zero_break_judge as zb
        self.zb = zb
        zb.reset_watch_runtime()

    def tearDown(self):
        self.zb.reset_watch_runtime()


# ------------------------------------------------------------------ A 判据
class TestA_AliveJudge(_Base):
    def test_01_expired_is_dead(self):
        self.assertFalse(self.zb._m407_watch_alive(_row("x", "expired_pending")))

    def test_02_other_statuses_are_alive(self):
        for st in ("pending", "runtime_verified", "", "applied", "wont_fix"):
            self.assertTrue(self.zb._m407_watch_alive(_row("x", st)), st)

    def test_03_non_dict_is_dead(self):
        for bad in (None, [], "x", 7):
            self.assertFalse(self.zb._m407_watch_alive(bad))

    def test_04_status_case_sensitive(self):
        """大小写不同不算死 —— 判据要跟派单侧 `== "expired_pending"` 严格同口径。"""
        self.assertTrue(self.zb._m407_watch_alive(_row("x", "EXPIRED_PENDING")))


# ------------------------------------------------------------------ B 诊断口
class TestB_AliveIds(_Base):
    def test_10_only_alive_returned(self):
        self.zb._WATCH_RUNTIME.extend(["o3_exp_a", "o3_live_a"])
        got = self.zb.alive_watch_ids(MIXED)
        self.assertIn("o3_live_a", got)
        self.assertNotIn("o3_exp_a", got)

    def test_11_static_seed_absent_from_pending(self):
        """静态种子不在 pending ⇒ 不算 eligible（但仍在 active 里被观察）。"""
        self.assertEqual((), self.zb.alive_watch_ids([]))
        self.assertEqual(2, len(self.zb.active_watch_ids()))

    def test_12_superset_of_nothing(self):
        """alive ⊆ active —— 诊断口不许凭空多出位来。"""
        self.zb._WATCH_RUNTIME.extend(["o3_live_a", "o3_live_b"])
        self.assertTrue(set(self.zb.alive_watch_ids(MIXED)) <= set(self.zb.active_watch_ids()))


# ------------------------------------------------------------------ C 健康判据翻转
class TestC_HealthyFlips(_Base):
    def test_20_same_fixture_two_switch_values(self):
        """同一份 pending（静态种子全不在、只有 3 条 expired 候选）：
        开关关 ⇒ 407 前；开关开 ⇒ 不再报 healthy。"""
        # 让静态种子命中 pending，但状态是 expired —— 这是 406 的真实形态
        rows = [{"patch_id": self.zb.WATCH_IDS[0], "status": "expired_pending"}]
        with patch("config.WATCH_HEAL_ALIVE_ONLY", False, create=True):
            old = self.zb.self_heal_watch(rows, [])
        self.zb.reset_watch_runtime()
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            new = self.zb.self_heal_watch(rows, [])
        self.assertEqual("healthy", old["state"], "407 前：在文件里就算健康")
        self.assertNotEqual("healthy", new["state"], "407 后：expired 不算健康")
        self.assertEqual([], list(old.get("dead_active") or []),
                         "开关关时不该统计 dead_active（407 前没有这个概念）")
        self.assertIn(self.zb.WATCH_IDS[0], new.get("dead_active") or [])

    def test_21_alive_seed_still_healthy(self):
        """活的静态种子仍然算健康 —— 别把判据改成"永远不健康"。"""
        rows = [{"patch_id": self.zb.WATCH_IDS[0], "status": "pending"}]
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(rows, [])
        self.assertEqual("healthy", out["state"])
        self.assertIn("alive", out["why"])

    def test_22_not_in_pending_still_unhealthy(self):
        """压根不在 pending 的位照旧不算健康（刀3 没动这一半）。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch([_row("other", "pending")], [])
        self.assertNotEqual("healthy", out["state"])


# ------------------------------------------------------------------ D 补位跳过
class TestD_HealSkipsDead(_Base):
    def test_30_three_expired_two_live(self):
        """任务书验收原句：3 条 expired + 2 条 eligible ⇒ 只算 2 条。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(MIXED, [], capacity=5)
        self.assertEqual("healed", out["state"])
        self.assertEqual(2, out["n_eligible"])
        self.assertEqual({"o3_live_a", "o3_live_b"}, set(out["eligible"]))
        self.assertEqual({"o3_live_a", "o3_live_b"}, set(out["added_all"]))
        self.assertEqual({"o3_exp_a", "o3_exp_b", "o3_exp_c"}, set(out["skipped"]))

    def test_31_expired_never_enters_runtime(self):
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            self.zb.self_heal_watch(MIXED, [], capacity=5)
        for r in ONLY_DEAD:
            self.assertNotIn(r["patch_id"], self.zb._WATCH_RUNTIME)

    def test_32_live_still_healed(self):
        """全是活的候选 ⇒ 照常补满，刀3 不能把好路径也堵了。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(ONLY_LIVE, [], capacity=5)
        self.assertEqual("healed", out["state"])
        self.assertEqual(2, out["n_eligible"])
        self.assertEqual([], list(out["skipped"] or []))


# ------------------------------------------------------------------ E 新状态
class TestE_OnlyExpiredState(_Base):
    def test_40_only_dead_gives_only_expired(self):
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
        self.assertEqual("only_expired", out["state"])
        self.assertIn("expired_pending", out["why"])

    def test_41_distinct_from_no_candidate(self):
        """全死 ≠ 没候选 —— 两个状态不许合并。"""
        # ★注意 issue_type 必须**不是** O3 类型、且 id 不以 o3_ 开头，
        #   才是真正的「pending 里没有 O3 候选」；用默认 dead_code 会直接补位成功。
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            dead = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
            self.zb.reset_watch_runtime()
            none = self.zb.self_heal_watch(
                [_row("plain", "pending", issue_type="lint")], [], capacity=5)
        self.assertEqual("only_expired", dead["state"])
        self.assertEqual("no_candidate", none["state"])
        self.assertNotEqual(dead["why"], none["why"])

    def test_42_changed_dedup_per_state(self):
        """`changed` 去重按状态串 —— only_expired 不能与 no_candidate 互相顶掉。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            a = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
            b = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
        self.assertTrue(a.get("changed"))
        self.assertFalse(b.get("changed"), "同一状态第二次不该再报")


# ------------------------------------------------------------------ F 数量如实
class TestF_CountsHonest(_Base):
    def test_50_two_metrics_present(self):
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(MIXED, [], capacity=5)
        for k in ("n_active", "n_eligible", "eligible", "skipped", "dead_active",
                  "alive_check"):
            self.assertIn(k, out, "缺字段 %s" % k)

    def test_51_zero_not_five(self):
        """任务书原句：没有 eligible 补丁时，数量应显示 0 而不是 5。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
        self.assertEqual(0, out["n_eligible"])
        self.assertEqual([], list(out["eligible"]))
        self.assertEqual("", out["added"])

    def test_52_active_unchanged_by_design(self):
        """`n_active` 仍是"要观察几位"，不因刀3 缩小 —— 否则破零读数会消失。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
        self.assertEqual(len(self.zb.WATCH_IDS), out["n_active"])

    def test_53_eligible_recounted_after_heal(self):
        """补位后重算 eligible —— 不能报"补了 2 条所以有 2 条"的乐观数。"""
        with patch("config.WATCH_HEAL_ALIVE_ONLY", True, create=True):
            out = self.zb.self_heal_watch(ONLY_LIVE, [], capacity=5)
        self.assertEqual(len(out["eligible"]), out["n_eligible"])
        self.assertEqual(set(out["added_all"]), set(out["eligible"]))


# ------------------------------------------------------------------ G 开关回退
class TestG_SwitchRollback(_Base):
    def test_60_off_restores_expired_heal(self):
        with patch("config.WATCH_HEAL_ALIVE_ONLY", False, create=True):
            out = self.zb.self_heal_watch(ONLY_DEAD, [], capacity=5)
        self.assertEqual("healed", out["state"], "关掉开关应恢复到 407 前：expired 也补")
        self.assertEqual(set(r["patch_id"] for r in ONLY_DEAD), set(out["added_all"]))

    def test_61_off_says_old_why(self):
        """开关关时 why 用 407 前措辞（`at_least_one_in_pending`），不冒充新口径。"""
        rows = [{"patch_id": self.zb.WATCH_IDS[0], "status": "expired_pending"}]
        with patch("config.WATCH_HEAL_ALIVE_ONLY", False, create=True):
            out = self.zb.self_heal_watch(rows, [])
        self.assertEqual("at_least_one_in_pending", out["why"])

    def test_62_config_default_is_true(self):
        import config
        self.assertIs(True, getattr(config, "WATCH_HEAL_ALIVE_ONLY", False))


# ------------------------------------------------------------------ H 派单侧未动
class TestH_DispatchUntouched(unittest.TestCase):
    def test_70_dispatch_judge_still_rejects_expired(self):
        """任务书明令「派单侧不动」：它对 expired 必须仍返 (False, "expired")。"""
        from nucleus.evolution.patch_execution_dispatch import _m339_o3_dispatch_ready
        p = {"o3_dead_code": True, "o3_auto_dispatch_ready": True,
             "requires_confirmation": False, "status": "expired_pending"}
        self.assertEqual((False, "expired"), _m339_o3_dispatch_ready(p))

    def test_71_dispatch_judge_accepts_live(self):
        from nucleus.evolution.patch_execution_dispatch import _m339_o3_dispatch_ready
        p = {"o3_dead_code": True, "o3_auto_dispatch_ready": True,
             "requires_confirmation": False, "status": "runtime_verified"}
        self.assertEqual((True, "ok"), _m339_o3_dispatch_ready(p))

    def test_72_expired_check_still_last(self):
        """`status == expired_pending` 仍是**最后**一道判断（顺序未变）。

        ★为什么这在案：如果把 expired 提到 `pending_cleared` / `not_confirmed` 之前，
          那些条目就会改报 "expired"，把「被人撤单」的事实盖掉（铁律166：
          不同事实不同措辞）。刀3 只读它，不许动它。
        """
        fn = _func(_DSP, "_m339_o3_dispatch_ready")
        self.assertIsNotNone(fn)
        rets = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple):
                vals = [e.value for e in n.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if vals:
                    rets.append((n.lineno, vals[-1]))
        reasons = [r for _, r in sorted(rets)]
        self.assertIn("expired", reasons)
        # 最后一个是最终放行 `ok`；deadline 是**最后一道否定** ⇒ 倒数第二
        self.assertEqual("ok", reasons[-1], reasons)
        self.assertEqual("expired", reasons[-2],
                         "expired 不再是最后一道否定：%s" % reasons)
        self.assertLess(reasons.index("pending_cleared"), reasons.index("applied"),
                        "撤单残留应先于 applied 判定：%s" % reasons)


# ------------------------------------------------------------------ I 结构 AST
class TestI_Structural(unittest.TestCase):
    def test_80_health_judge_uses_alive(self):
        fn = _func(_ZB, "self_heal_watch")
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        self.assertIn("_m407_watch_alive", names, "健康判据没接上 _m407_watch_alive")

    def test_81_skip_precedes_pick(self):
        """补位循环里 `_skipped.append` 必须早于 `_picks.append`。"""
        fn = _func(_ZB, "self_heal_watch")
        loop = None
        for n in ast.walk(fn):
            if isinstance(n, ast.For):
                ids = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
                if "_picks" in ids and "_skipped" in ids:
                    loop = n
                    break
        self.assertIsNotNone(loop, "补位循环里没有 _skipped")
        sk = min(c.lineno for c in ast.walk(loop)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "append"
                 and isinstance(c.func.value, ast.Name) and c.func.value.id == "_skipped")
        pk = min(c.lineno for c in ast.walk(loop)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "append"
                 and isinstance(c.func.value, ast.Name) and c.func.value.id == "_picks")
        self.assertLess(sk, pk, "append 顺序反了 ⇒ 死状态会被补进去")

    def test_82_no_log_in_pure_module(self):
        """`self_heal_watch` 仍是**纯判据**：不打日志（日志由调用方打）。"""
        fn = _func(_ZB, "self_heal_watch")
        src = io.open(_ZB, encoding="utf-8", errors="replace").read()
        seg = ast.get_source_segment(src, fn) or ""
        for bad in ("logger", "logging", "_log(", "print("):
            self.assertNotIn(bad, seg, "纯判据里出现了 %r" % bad)


# ------------------------------------------------------------------ J 清单
class TestJ_DeadStatusList(unittest.TestCase):
    def test_90_only_expired_pending(self):
        """清单只许有 `expired_pending` —— 顺手加别的就是越界改派单判据。"""
        from nucleus.evolution import zero_break_judge as zb
        self.assertEqual(("expired_pending",), zb.WATCH_DEAD_STATUSES)

    def test_91_matches_dispatch_literal(self):
        """清单里的值必须与派单侧那个字面量**逐字相同**，否则两个模块又开始各说各话。"""
        from nucleus.evolution import zero_break_judge as zb
        src = io.open(_DSP, encoding="utf-8", errors="replace").read()
        for st in zb.WATCH_DEAD_STATUSES:
            self.assertIn('"%s"' % st, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
