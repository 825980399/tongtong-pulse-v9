# -*- coding: utf-8 -*-
"""第422批 刀4 门控单测：叙事启动补跑 + 每 6h 槽位 flush + append-only 审计账。

被测对象：`nucleus/observability/narrative_flush.py`
  · `maybe_catchup_on_boot` / `flush_tick`
  · `_m422_k4_digest_day` / `_m422_k4_slot` / `_m422_k4_day_landed`
  · `_m422_k4_load_state` / `_m422_k4_save_state` / `_m422_k4_append_ledger`
  · `get_m422_flush_state`

隔离约束（每条都独立）：
  · 每个用例 `mkdtemp` 独立根，`tearDown` 自行 rmtree（**不留给 pytest 一次性清**，
    否则跨用例累积会撞 safe-delete 批量阈值 ⇒ 整批假红，421 已栽过一次）；
  · 凡会触发落盘的地方**一律注入假 runner** —— 真 runner 走
    `narrative_store.persist_from_organ()`，它读的是模块全局 `_DATA_DIR`
    （不是本文件传的 `root=`），不注入就会写进**生产** `data/narrative/`；
  · 唯一测真链路的 `TestF::test_23` 显式 `narrative_store.set_data_dir(tmp)`
    并在 `tearDown` 还原；
  · 时间一律显式传 `now=`，**不 sleep**。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.observability import narrative_digest as ND
from nucleus.observability import narrative_flush as NF
from nucleus.observability import narrative_store as NS

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts(y, mo, d, h, mi=0):
    """本地时间构造 epoch（与 `time.localtime` 同口径，避免 UTC 偏移干扰）。"""
    return time.mktime(time.strptime(f"{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:00",
                                     "%Y-%m-%d %H:%M:%S"))


class _Base(unittest.TestCase):
    """公共夹具：独立根 + 两个路径 + 计数器式假 runner。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m422k4_")
        self.root = self._td
        self.sp = os.path.join(self._td, "state", "flush_state.json")
        self.lp = os.path.join(self._td, "narrative", "flush_history.jsonl")
        self.calls = []          # 每次 runner 被调时记下 now
        self.results = []        # 依次弹出作为 runner 的返回

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    # ---- 假 runner：不碰真器官、不碰生产目录
    def _runner(self, now=None):
        self.calls.append(now)
        if self.results:
            return self.results.pop(0)
        return {"ok": True, "day": NF._m422_k4_digest_day(now), "rows": 1,
                "replaced": 0, "path": os.path.join(self._td, "fake.jsonl")}

    def _seed_day(self, day, extra=None, row_date=None):
        """在隔离根里造一份 `narrative_<day>.jsonl`。

        `row_date` 用来造**文件名与内容日期不一致**的畸形文件（见 test_11）——
        这种文件正常写不出来，但历史版本/手工改动可能是这样，
        判定函数必须按**内容**而不是文件名认账。
        """
        p = os.path.join(self._td, "data", "narrative", f"narrative_{day}.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        rows = [{"date": row_date or day, "version": "412.2", "headline": "seed"}]
        rows.extend(extra or [])
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        return p

    def _ledger_rows(self):
        if not os.path.exists(self.lp):
            return []
        with open(self.lp, encoding="utf-8") as f:
            return [json.loads(s) for s in f if s.strip()]


# ====================================================================== A
class TestA_CatchupDecision(_Base):
    """启动补跑的四态判定 + 幂等 + 失败可重试。"""

    def test_01_missing_before_noon_runs(self):
        """摘要日无文件 + 09:00 ⇒ 真补一次（runner 恰好 1 次）。"""
        _now = _ts(2026, 11, 2, 9, 0)
        _r = NF.maybe_catchup_on_boot(now=_now, root=self.root, state_path=self.sp,
                                      ledger_path=self.lp, runner=self._runner)
        self.assertEqual(_r["status"], "catchup_ran")
        self.assertEqual(_r["digest_day"], "20261102")
        self.assertEqual(len(self.calls), 1, "补跑必须真调一次落盘")
        self.assertTrue(_r["run"]["ok"])

    def test_02_landed_skips_runner(self):
        """该落的那天已有行 ⇒ already_landed，**runner 0 次**（不能白跑）。"""
        self._seed_day("20261102")
        _r = NF.maybe_catchup_on_boot(now=_ts(2026, 11, 2, 9, 0), root=self.root,
                                      state_path=self.sp, ledger_path=self.lp,
                                      runner=self._runner)
        self.assertEqual(_r["status"], "already_landed")
        self.assertEqual(_r["reason"], "row_ok")
        self.assertEqual(len(self.calls), 0, "已落盘还调 runner = 白烧一次全量摘要")

    def test_03_after_noon_too_late(self):
        """≥12:00 ⇒ too_late，runner 0 次（任务书限定只补上午）。"""
        _r = NF.maybe_catchup_on_boot(now=_ts(2026, 11, 2, 13, 0), root=self.root,
                                      state_path=self.sp, ledger_path=self.lp,
                                      runner=self._runner)
        self.assertEqual(_r["status"], "too_late")
        self.assertIn("past_12h", _r["reason"])
        self.assertEqual(len(self.calls), 0)

    def test_04_idempotent_same_day(self):
        """同一天补成功过 ⇒ 再调 catchup_done，runner 累计仍 1 次。"""
        _now = _ts(2026, 11, 2, 9, 0)
        _r1 = NF.maybe_catchup_on_boot(now=_now, root=self.root, state_path=self.sp,
                                       ledger_path=self.lp, runner=self._runner)
        _r2 = NF.maybe_catchup_on_boot(now=_now + 60, root=self.root, state_path=self.sp,
                                       ledger_path=self.lp, runner=self._runner)
        self.assertEqual(_r1["status"], "catchup_ran")
        self.assertEqual(_r2["status"], "catchup_done")
        self.assertEqual(len(self.calls), 1, "同一天补过的不能重复补")

    def test_05_failed_can_retry(self):
        """补失败 → 下次启动**仍要试**（catchup_ok=False 不锁死）。"""
        self.results = [{"ok": False, "reason": "organ_missing"}, {"ok": True, "day": "20261102"}]
        _r1 = NF.maybe_catchup_on_boot(now=_ts(2026, 11, 2, 9, 0), root=self.root,
                                       state_path=self.sp, ledger_path=self.lp,
                                       runner=self._runner)
        _r2 = NF.maybe_catchup_on_boot(now=_ts(2026, 11, 2, 9, 5), root=self.root,
                                       state_path=self.sp, ledger_path=self.lp,
                                       runner=self._runner)
        self.assertEqual(_r1["status"], "catchup_failed")
        self.assertEqual(_r2["status"], "catchup_ran", "上次失败不该把今天锁死")
        self.assertEqual(len(self.calls), 2)


# ====================================================================== B
class TestB_DigestDay(_Base):
    """摘要日口径 —— 本刀最容易错的地方（错一天 ⇒ 补跑了还是没有今天的文件）。"""

    def test_06_settle_boundary(self):
        """<6 点回退一天，>=6 点算当天。"""
        self.assertEqual(NF._m422_k4_digest_day(_ts(2026, 11, 2, 5, 59)), "20261101")
        self.assertEqual(NF._m422_k4_digest_day(_ts(2026, 11, 2, 6, 0)), "20261102")
        self.assertEqual(NF._m422_k4_digest_day(_ts(2026, 11, 2, 4, 0)), "20261101")

    def test_07_matches_narrative_digest_full_day(self):
        """全天 1440 个时刻与 `narrative_digest.digest_day` **逐点相等**。

        ★这条是防漂移的主断言：两处各写一份「摘要日」迟早错开一天，
          而错开的表现是「补跑了、但今天还是没文件」，比不补更难查。
        """
        _bad = []
        for _h in range(24):
            for _m in range(60):
                _t = _ts(2026, 11, 2, _h, _m)
                _a = NF._m422_k4_digest_day(_t)
                _b = ND.digest_day(_t)
                if _a != _b:
                    _bad.append((_h, _m, _a, _b))
        self.assertEqual(_bad, [], "摘要日与 narrative_digest 不一致")

    def test_08_settle_hour_is_single_source(self):
        """结算小时必须**读** `narrative_digest.DIGEST_SETTLE_HOUR`，不是本地常量。"""
        self.assertEqual(NF._m422_k4_settle_hour(), int(ND.DIGEST_SETTLE_HOUR))

    def test_09_midnight_rolls_back(self):
        """00:00 也回退一天（凌晨都属于"前一天的收尾"）。"""
        self.assertEqual(NF._m422_k4_digest_day(_ts(2026, 11, 2, 0, 0)), "20261101")
        self.assertEqual(NF._m422_k4_digest_day(_ts(2026, 11, 2, 23, 59)), "20261102")


# ====================================================================== C
class TestC_DayLanded(_Base):
    """落盘判定 —— 「文件在」不等于「这天落了」，只看 exists 会永久不补。"""

    def test_10_file_missing(self):
        self.assertEqual(NF._m422_k4_day_landed("20261102", root=self.root),
                         (False, "file_missing"))

    def test_11_file_exists_but_other_date(self):
        """★文件存在但里面是**别的日期** ⇒ 必须判未落盘。

        造法：文件名 `narrative_20261102.jsonl`，里面那行 `date=20261101`。
        `persist_daily` 的 `date_mismatch` 校验正常写不出这种文件，但历史版本 /
        手工改动 / 未来的口径变更都可能产生 —— 判定必须按**内容**认账，
        只看文件名会把「里面其实是昨天」误判成「今天已落盘」⇒ 永不补跑。
        """
        self._seed_day("20261102", row_date="20261101")
        _landed, _why = NF._m422_k4_day_landed("20261102", root=self.root)
        self.assertFalse(_landed, "文件名对但内容日期不对，不能算今天已落盘")
        self.assertEqual(_why, "row_missing")

    def test_12_row_ok(self):
        self._seed_day("20261102")
        self.assertEqual(NF._m422_k4_day_landed("20261102", root=self.root),
                         (True, "row_ok"))

    def test_13_badline_tolerated(self):
        """坏行跳过、好行照认（坏行不许把整个文件判成"没落盘"）。"""
        p = os.path.join(self._td, "data", "narrative", "narrative_20261102.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("{这不是 json\n")
            f.write(json.dumps({"date": "20261102"}, ensure_ascii=False) + "\n")
        self.assertEqual(NF._m422_k4_day_landed("20261102", root=self.root),
                         (True, "row_ok"))


# ====================================================================== D
class TestD_SlotFlush(_Base):
    """槽位 flush：10/16/22 三槽，同槽当天只跑一次，漏槽不追。"""

    def test_14_before_ten_no_slot(self):
        """09:59 ⇒ no_slot，runner 0 次（不建状态文件、不落盘）。"""
        _r = NF.flush_tick(now=_ts(2026, 11, 2, 9, 59), root=self.root, state_path=self.sp,
                           ledger_path=self.lp, runner=self._runner)
        self.assertEqual(_r["status"], "no_slot")
        self.assertEqual(len(self.calls), 0)
        self.assertFalse(os.path.exists(self.sp), "no_slot 不该写状态文件")

    def test_15_ten_oclock_flushes(self):
        _r = NF.flush_tick(now=_ts(2026, 11, 2, 10, 0), root=self.root, state_path=self.sp,
                           ledger_path=self.lp, runner=self._runner)
        self.assertEqual(_r["status"], "flushed")
        self.assertEqual(_r["slot"], "20261102T10")
        self.assertEqual(len(self.calls), 1)

    def test_16_same_slot_not_repeated(self):
        """同一槽再调 ⇒ already_flushed，runner 调用次数**不增**。"""
        _now = _ts(2026, 11, 2, 10, 0)
        for _i in range(3):
            NF.flush_tick(now=_now + _i * 300, root=self.root, state_path=self.sp,
                          ledger_path=self.lp, runner=self._runner)
        _r = NF.flush_tick(now=_now + 900, root=self.root, state_path=self.sp,
                           ledger_path=self.lp, runner=self._runner)
        self.assertEqual(_r["status"], "already_flushed")
        self.assertEqual(len(self.calls), 1, "同槽一天只能跑一次")

    def test_17_three_slots_run_three_times(self):
        """10:00 / 16:00 / 22:00 各跑一次 ⇒ runner 累计 3 次（+04:00 夜间编排 = 4 拍/天）。"""
        for _h in (10, 16, 22):
            _r = NF.flush_tick(now=_ts(2026, 11, 2, _h, 0), root=self.root,
                               state_path=self.sp, ledger_path=self.lp, runner=self._runner)
            self.assertEqual(_r["status"], "flushed")
            self.assertEqual(_r["slot"], f"20261102T{_h:02d}")
        self.assertEqual(len(self.calls), 3)

    def test_18_late_night_stays_t22(self):
        """23:59 仍归 22:00 那一槽（不产生第 4 个槽）。"""
        self.assertEqual(NF._m422_k4_slot(_ts(2026, 11, 2, 23, 59)), "20261102T22")
        self.assertEqual(NF._m422_k4_slot(_ts(2026, 11, 2, 15, 59)), "20261102T10")
        self.assertEqual(NF._m422_k4_slot(_ts(2026, 11, 2, 16, 0)), "20261102T16")


# ====================================================================== E
class TestE_RetryCap(_Base):
    """失败重试上限：一直失败不许每拍重烧。"""

    def test_19_three_failures_then_give_up(self):
        self.results = [{"ok": False, "reason": "boom"}] * 6
        _now = _ts(2026, 11, 2, 16, 0)
        _st = []
        for _i in range(4):
            _st.append(NF.flush_tick(now=_now + _i * 60, root=self.root, state_path=self.sp,
                                     ledger_path=self.lp, runner=self._runner)["status"])
        self.assertEqual(_st, ["flush_failed"] * 3 + ["give_up"])
        self.assertEqual(len(self.calls), NF.MAX_ATTEMPTS,
                         f"最多试 {NF.MAX_ATTEMPTS} 次，实得 {len(self.calls)}")

    def test_20_success_clears_attempts(self):
        """失败 2 次后成功 ⇒ attempts 清空（否则下一槽会带着旧欠账）。"""
        self.results = [{"ok": False}, {"ok": False}, {"ok": True, "day": "20261102"}]
        _now = _ts(2026, 11, 2, 16, 0)
        for _i in range(3):
            NF.flush_tick(now=_now + _i * 60, root=self.root, state_path=self.sp,
                          ledger_path=self.lp, runner=self._runner)
        _s = NF._m422_k4_load_state(self.sp)
        self.assertEqual(_s["attempts"], {}, "成功后 attempts 必须清空")
        self.assertEqual(_s["last_slot"], "20261102T16")

    def test_21_give_up_does_not_call_runner(self):
        """give_up 之后 runner 一次都不许再被调。"""
        self.results = [{"ok": False}] * 3
        _now = _ts(2026, 11, 2, 22, 0)
        for _i in range(3):
            NF.flush_tick(now=_now + _i * 60, root=self.root, state_path=self.sp,
                          ledger_path=self.lp, runner=self._runner)
        _n_before = len(self.calls)
        _r = NF.flush_tick(now=_now + 600, root=self.root, state_path=self.sp,
                           ledger_path=self.lp, runner=self._runner)
        self.assertEqual(_r["status"], "give_up")
        self.assertEqual(len(self.calls), _n_before)


# ====================================================================== F
class TestF_LedgerAndContract(_Base):
    """审计账 + **摘要文件契约不许变**（一天一行）。"""

    def test_22_ledger_one_record_per_run(self):
        NF.flush_tick(now=_ts(2026, 11, 2, 10, 0), root=self.root, state_path=self.sp,
                      ledger_path=self.lp, runner=self._runner)
        _rows = self._ledger_rows()
        self.assertEqual(len(_rows), 1)
        self.assertEqual(_rows[0]["trigger"], "slot")
        self.assertEqual(_rows[0]["slot"], "20261102T10")
        self.assertEqual(_rows[0]["digest_day"], "20261102")
        self.assertTrue(_rows[0]["ok"])

    def test_23_two_slots_one_summary_row(self):
        """★任务书验证项的真实答案：一天两拍 ⇒ **账 2 条、摘要文件仍 1 行**。

        为什么不是"文件里多出两行"：`persist_daily` 的语义是
        `_kept = [r for r in _old if r.get("date") != _day]` ⇒ **替换当天那行**。
        改它等于推翻 `prev_row`/`summary`/`list_days`/`continuous_days` 的全部假设，
        所以本刀另建 append-only 账来体现「多个时间点」，摘要文件契约原样不动。
        """
        _saved_dir = NS._DATA_DIR
        try:
            NS.set_data_dir(self._td)

            def _real_runner(now=None):
                return NS.persist_daily(NS.row_of("20261102", generated_at=now), now=now)

            for _h in (10, 16):
                _r = NF.flush_tick(now=_ts(2026, 11, 2, _h, 0), root=self.root,
                                   state_path=self.sp, ledger_path=self.lp,
                                   runner=_real_runner)
                self.assertEqual(_r["status"], "flushed")
        finally:
            NS.set_data_dir(_saved_dir)

        _day_file = os.path.join(self._td, "data", "narrative", "narrative_20261102.jsonl")
        with open(_day_file, encoding="utf-8") as f:
            _lines = [s for s in f if s.strip()]
        self.assertEqual(len(_lines), 1, "摘要文件必须仍是一天一行（替换语义）")
        _row = json.loads(_lines[0])
        self.assertEqual(_row["date"], "20261102")
        self.assertEqual(len(self._ledger_rows()), 2, "两次 flush ⇒ 账里必须两条")
        self.assertEqual([r["slot"] for r in self._ledger_rows()],
                         ["20261102T10", "20261102T16"])

    def test_24_ledger_is_appendable_jsonl(self):
        for _h in (10, 16, 22):
            NF.flush_tick(now=_ts(2026, 11, 2, _h, 0), root=self.root, state_path=self.sp,
                          ledger_path=self.lp, runner=self._runner)
        with open(self.lp, encoding="utf-8") as f:
            for _i, _ln in enumerate(f, 1):
                self.assertTrue(_ln.endswith("\n"))
                self.assertIsInstance(json.loads(_ln), dict, f"第 {_i} 行不是 dict")

    def test_25_disabled_writes_nothing(self):
        """两个开关都关 ⇒ 两个入口都 disabled，且**不建任何文件**。"""
        _saved = (NF.CATCHUP_SWITCH, NF.FLUSH_SWITCH)
        _orig = NF._m422_k4_enabled
        try:
            NF._m422_k4_enabled = lambda _sw: False
            _r1 = NF.maybe_catchup_on_boot(now=_ts(2026, 11, 2, 9, 0), root=self.root,
                                           state_path=self.sp, ledger_path=self.lp,
                                           runner=self._runner)
            _r2 = NF.flush_tick(now=_ts(2026, 11, 2, 16, 0), root=self.root,
                                state_path=self.sp, ledger_path=self.lp, runner=self._runner)
        finally:
            NF._m422_k4_enabled = _orig
        self.assertEqual((_r1["status"], _r2["status"]), ("disabled", "disabled"))
        self.assertEqual(len(self.calls), 0)
        self.assertFalse(os.path.exists(self.sp))
        self.assertFalse(os.path.exists(self.lp))
        self.assertEqual(_saved, (NF.CATCHUP_SWITCH, NF.FLUSH_SWITCH))


# ====================================================================== G
class TestG_Wiring(_Base):
    """接线：main.py 两处 + config.py 两个开关（少一处就整刀空转）。"""

    def _read(self, rel):
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            return f.read()

    def test_26_main_calls_both_entries(self):
        _src = self._read("main.py")
        self.assertIn("maybe_catchup_on_boot(", _src)
        self.assertIn("flush_tick(", _src)
        self.assertIn("narrative_flush", _src)

    def test_27_catchup_runs_before_main_loop(self):
        """补跑必须在**真正那个**主循环之前（启动时，不是循环里）。

        ★`main.py` 里有 **两处** `while not exit_requested:`（4825 / 4954），
          用 `find` 会撞上前一处 ⇒ 假红。这里取 `rfind`（最后一处 = 主循环），
          并用「节流变量初始化」作为中间锚点钉住三者的先后。
        """
        _src = self._read("main.py")
        _i_call = _src.find("maybe_catchup_on_boot(")
        _i_init = _src.find("_m422_fl_last_ts = 0.0")
        _i_loop = _src.rfind("while not exit_requested:")
        self.assertGreater(_i_call, 0)
        self.assertGreater(_i_init, 0)
        self.assertGreater(_i_loop, 0)
        self.assertLess(_i_call, _i_init, "补跑必须先于节流变量初始化")
        self.assertLess(_i_init, _i_loop, "节流变量初始化必须在主循环之前")

    def test_28_config_switches_exist(self):
        import config as _cfg
        self.assertTrue(getattr(_cfg, NF.CATCHUP_SWITCH, None))
        self.assertTrue(getattr(_cfg, NF.FLUSH_SWITCH, None))


if __name__ == "__main__":
    unittest.main()
