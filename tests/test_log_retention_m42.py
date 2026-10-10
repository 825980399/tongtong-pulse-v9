# -*- coding: utf-8 -*-
"""主线第42批 T1（P0-272）门控测试：日志留存治理。

覆盖：
  · 超期清理（只删超期 / 保留在写 / 保护名单 / 非日志 / 开关关闭 / 幂等）
  · 完整性自检（first_run / ok / truncated / missing / 留痕独立文件 / 开关关闭）
  · 轮转行为（备份生成 + backupCount 上限）
  · 接线（logger 调用治理 / config 开关 / 测试环境保护）

★ 全部使用隔离目录，**绝不写生产 logs/**。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
import logging.handlers
import tempfile
import time
import unittest

import config
from nucleus.logger import (
    _LOG_INTEGRITY_FILE,
    _LOG_STATE_FILE,
    _is_log_file,
    check_log_integrity,
    cleanup_old_logs,
    log_retention_days,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _pytest_tmp_root():
    """★主线第46批 T4（P2-301/307）：测试隔离目录改用【系统临时目录】。

    背景：旧实现 ``mkdtemp(prefix=..., dir=<项目>/tmp)`` 会在项目 ``tmp/`` 下
    持续留下隔离目录（``m41_t2_*`` / ``m43dq_*`` / ``_m27_*`` ...），tearDown
    清不干净时就变成"历史残留"，并与清理工具互相打架。

    现在改为系统临时目录下的 ``pulse_pytest/``：
      * 不污染项目 ``tmp/`` → 清理判据不再需要为它们开特例
      * 由操作系统回收 → 残留不再累积
      * 可用环境变量 ``PULSE_TEST_TMP_ROOT`` 覆盖（调试/隔离用）
    """
    _env = os.environ.get("PULSE_TEST_TMP_ROOT")
    if _env:
        _d = _env
    else:
        # ★与项目同盘：Windows 跨盘 shutil.move 会 copy+unlink →
        #   触发沙箱删除配额（m41 实测教训）。故不用 tempfile.gettempdir()，
        #   改用项目根下的 .pytest_tmp/（同盘 + 不污染 tmp/）。
        _d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp")
    os.makedirs(_d, exist_ok=True)
    return _d


_TMP_ROOT = _pytest_tmp_root()


def _safe_rmtree(path):
    """分批删除（≤50/批）+ 捕获 BaseException：被沙箱配额拦下时只记 warning。"""
    if not os.path.isdir(path):
        return
    for dp, dn, fn in os.walk(path, topdown=False):
        for i in range(0, len(fn), 50):
            for f in fn[i:i + 50]:
                try:
                    os.remove(os.path.join(dp, f))
                except BaseException as e:
                    print("cleanup warn: %s: %s" % (type(e).__name__, e))
        try:
            os.rmdir(dp)
        except BaseException as e:
            print("cleanup warn: %s: %s" % (type(e).__name__, e))


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m42t_", dir=_TMP_ROOT)

    def tearDown(self):
        _safe_rmtree(self.dir)

    def mk(self, name, days_ago=0.0, size=64):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x" * size)
        t = time.time() - days_ago * 86400.0
        os.utime(p, (t, t))
        return p


# ====================================================================== 清理
class TestCleanupOldLogs(_Base):
    def test_01_removes_expired(self):
        self.mk("pulse.log.1", 30)
        self.mk("pulse.log.2", 30)
        self.mk("svc.log", 30)
        removed = {os.path.basename(x) for x in cleanup_old_logs(log_dir=self.dir, days=7)}
        self.assertEqual(removed, {"pulse.log.1", "pulse.log.2", "svc.log"})

    def test_02_keeps_recent(self):
        self.mk("recent.log.1", 1)
        self.mk("today.log", 0)
        removed = cleanup_old_logs(log_dir=self.dir, days=7)
        self.assertEqual(removed, [])
        self.assertEqual(sorted(os.listdir(self.dir)), ["recent.log.1", "today.log"])

    def test_03_protect_list_wins(self):
        """pulse_crash.log 超期也永不删（保护名单）。"""
        self.mk("pulse_crash.log", 99)
        self.mk("other.log", 99)
        removed = {os.path.basename(x) for x in cleanup_old_logs(log_dir=self.dir, days=7)}
        self.assertIn("other.log", removed)
        self.assertNotIn("pulse_crash.log", removed)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "pulse_crash.log")))

    def test_04_skips_non_log_files(self):
        self.mk("notes.txt", 99)
        self.mk("param_report.html", 99)
        removed = cleanup_old_logs(log_dir=self.dir, days=7)
        self.assertEqual(removed, [])
        self.assertEqual(len(os.listdir(self.dir)), 2)

    def test_05_disabled_switch(self):
        self.mk("old.log", 99)
        _bak = config.ENABLE_LOG_RETENTION_CLEANUP
        try:
            config.ENABLE_LOG_RETENTION_CLEANUP = False
            self.assertEqual(log_retention_days(), 0)
            self.assertEqual(cleanup_old_logs(log_dir=self.dir), [])
            self.assertTrue(os.path.isfile(os.path.join(self.dir, "old.log")))
        finally:
            config.ENABLE_LOG_RETENTION_CLEANUP = _bak

    def test_06_days_zero_means_no_cleanup(self):
        self.mk("old.log", 999)
        self.assertEqual(cleanup_old_logs(log_dir=self.dir, days=0), [])
        self.assertEqual(cleanup_old_logs(log_dir=self.dir, days=-3), [])

    def test_07_missing_dir_is_safe(self):
        gone = os.path.join(self.dir, "not_exist")
        self.assertEqual(cleanup_old_logs(log_dir=gone, days=7), [])

    def test_08_idempotent(self):
        self.mk("a.log", 30)
        self.assertEqual(len(cleanup_old_logs(log_dir=self.dir, days=7)), 1)
        self.assertEqual(cleanup_old_logs(log_dir=self.dir, days=7), [])


class TestIsLogFile(unittest.TestCase):
    def test_10_patterns(self):
        for good in ("pulse.log", "pulse.log.1", "a.log.12", "x.log.999"):
            self.assertTrue(_is_log_file(good), good)
        for bad in ("notes.txt", "pulse.log.bak", "pulse_json", ".log_state.json",
                    "report.html", "log"):
            self.assertFalse(_is_log_file(bad), bad)


# ====================================================================== 完整性
class TestLogIntegrity(_Base):
    def _paths(self):
        return (os.path.join(self.dir, "pulse.log"),
                os.path.join(self.dir, _LOG_STATE_FILE))

    def test_20_first_run(self):
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 500)
        r = check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        self.assertEqual(r["status"], "first_run")
        self.assertTrue(os.path.isfile(sp))

    def test_21_growth_is_ok(self):
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 500)
        check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        with open(lp, "a", encoding="utf-8") as f:
            f.write("y" * 300)
        r = check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        self.assertEqual(r["status"], "ok")

    def test_22_truncated_records_event(self):
        """★核心：外部截断必须被识别并留痕。"""
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 5000)
        check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        with open(lp, "w", encoding="utf-8") as f:      # 模拟外部原地截断
            f.write("z" * 120)
        r = check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        self.assertEqual(r["status"], "truncated")
        ev = os.path.join(self.dir, _LOG_INTEGRITY_FILE)
        self.assertTrue(os.path.isfile(ev), "必须写留痕文件")
        rec = json.loads(open(ev, encoding="utf-8").read().strip().splitlines()[-1])
        self.assertEqual(rec["event"], "truncated")
        self.assertEqual(rec["prev_size"], 5000)
        self.assertEqual(rec["cur_size"], 120)

    def test_23_missing_file(self):
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 100)
        check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        os.remove(lp)
        r = check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        self.assertEqual(r["status"], "missing")

    def test_24_switch_off_skipped(self):
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 100)
        _bak = config.ENABLE_LOG_INTEGRITY_CHECK
        try:
            config.ENABLE_LOG_INTEGRITY_CHECK = False
            r = check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
            self.assertEqual(r["status"], "skipped")
            self.assertFalse(os.path.isfile(sp), "关闭后不应写状态")
        finally:
            config.ENABLE_LOG_INTEGRITY_CHECK = _bak

    def test_25_state_holds_size(self):
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 777)
        check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        st = json.loads(open(sp, encoding="utf-8").read())
        self.assertEqual(st["size"], 777)
        self.assertIn("ino", st)

    def test_26_event_is_separate_file(self):
        """留痕必须写独立文件（主日志可能正被清空）。"""
        lp, sp = self._paths()
        self.mk("pulse.log", 0, 3000)
        check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        with open(lp, "w", encoding="utf-8") as f:
            f.write("t" * 10)
        check_log_integrity(log_dir=self.dir, log_file=lp, state_path=sp)
        names = sorted(os.listdir(self.dir))
        self.assertIn(_LOG_INTEGRITY_FILE, names)
        self.assertNotEqual(_LOG_INTEGRITY_FILE, "pulse.log")
        self.assertNotIn("truncated", open(lp, encoding="utf-8").read())


# ====================================================================== 轮转
class TestRotation(_Base):
    def test_30_rotates_and_caps_backups(self):
        lp = os.path.join(self.dir, "pulse.log")
        lg = logging.getLogger("m42_test_rot")
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
        for h in list(lg.handlers):
            lg.removeHandler(h)
        h = logging.handlers.RotatingFileHandler(
            lp, maxBytes=2000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
        try:
            for i in range(40):
                lg.info("R%02d" % i + "x" * 496)
            h.flush()
        finally:
            h.close()
            lg.removeHandler(h)
        names = sorted(os.listdir(self.dir))
        baks = [n for n in names if n.startswith("pulse.log.")]
        self.assertTrue(baks, "应生成轮转备份")
        self.assertLessEqual(len(baks), 5, "备份数不得超过 backupCount")
        self.assertIn("pulse.log", names, "当前文件必须存在")
        # 备份按 .1 最新 ⇒ .1 存在且最大编号 ≤5
        self.assertEqual(baks[0], "pulse.log.1")
        self.assertLessEqual(int(baks[-1].split(".")[-1]), 5)


# ====================================================================== 接线
class TestWiring(unittest.TestCase):
    def test_40_logger_invokes_governance(self):
        src = open(os.path.join(_ROOT, "nucleus", "logger.py"), encoding="utf-8").read()
        self.assertIn("cleanup_old_logs()", src)
        self.assertIn("check_log_integrity()", src)
        # 必须由测试环境保护守卫
        self.assertIn("if not _log_in_test_env():", src)
        self.assertIn("_log_in_test_env", src)

    def test_41_config_switches_exist(self):
        self.assertTrue(hasattr(config, "ENABLE_LOG_RETENTION_CLEANUP"))
        self.assertTrue(hasattr(config, "LOG_RETENTION_DAYS"))
        self.assertTrue(hasattr(config, "LOG_RETENTION_PROTECT"))
        self.assertTrue(hasattr(config, "ENABLE_LOG_INTEGRITY_CHECK"))
        self.assertEqual(config.LOG_RETENTION_DAYS, 7)
        self.assertIn("pulse_crash.log", config.LOG_RETENTION_PROTECT)

    def test_42_logger_keeps_rotation_handler(self):
        """不得破坏既有轮转配置（RotatingFileHandler + maxBytes/backupCount）。"""
        src = open(os.path.join(_ROOT, "nucleus", "logger.py"), encoding="utf-8").read()
        self.assertIn("RotatingFileHandler", src)
        self.assertIn("maxBytes=max_bytes", src)
        self.assertIn("backupCount=backup_count", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
