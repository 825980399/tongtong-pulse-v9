# -*- coding: utf-8 -*-
"""第49批 T3 门控测试：P2-331 / P2-332 / P2-333

T3-1 情绪衰减死代码接入（check_decay → _trim_capacity）
T3-2 眼睛推流统计日志级别（INFO → DEBUG + 低频摘要）
T3-3 日志轮转 PermissionError 静默处理（自定义 handler）
"""
import io
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import nucleus.logger as _lg  # noqa: E402
from nucleus.mnemosyne.experience_pool import ExperiencePool  # noqa: E402

_EXP = io.open(os.path.join(_ROOT, "nucleus/mnemosyne/experience_pool.py"),
               encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
_EYES = io.open(os.path.join(_ROOT, "organs/senses/PulseEyes.py"),
                encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
_LOG = io.open(os.path.join(_ROOT, "nucleus/logger.py"),
               encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw, self._old = kw, {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


# ============================================================ T3-1
class TestDecayWired(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m49_t31_")
        self.pool = ExperiencePool(base_dir=self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _seed(self, age_hours, intensity, decay_rate):
        self.pool._experiences.append({
            "id": "e1", "timestamp": time.time() - age_hours * 3600,
            "emotion_intensity": intensity, "decay_rate": decay_rate,
            "is_summarized": False, "motivation": "测试动机" * 6,
            "summary": "原始摘要内容", "content": "内容" * 10,
            "field_frequency_snapshot": {"a": 1},
        })

    def test_10_low_intensity_decayed_and_summarized(self):
        """★任务书核心：情绪强度 <0.05 的完整体验应被自动压缩。"""
        self._seed(48.0, 0.06, 0.05)          # 48h × 0.05/h = 2.4 → 归零
        self.pool._last_decay_check = 0.0
        self.pool._trim_capacity()            # ★应触发 check_decay
        e = self.pool._experiences[0]
        self.assertLess(e["emotion_intensity"], 0.05)
        self.assertTrue(e["is_summarized"], "衰减到极低后应被压缩")
        self.assertGreaterEqual(self.pool._total_decayed, 1)

    def test_11_high_intensity_untouched(self):
        self._seed(48.0, 0.9, 0.0)
        self.pool._last_decay_check = 0.0
        self.pool._trim_capacity()
        e = self.pool._experiences[0]
        self.assertEqual(e["emotion_intensity"], 0.9)
        self.assertFalse(e["is_summarized"])

    def test_12_throttle_respected(self):
        self._seed(48.0, 0.06, 0.05)
        self.pool._last_decay_check = time.time()   # 刚检查过
        self.pool._trim_capacity()
        e = self.pool._experiences[0]
        self.assertEqual(e["emotion_intensity"], 0.06, "节流窗口内不得衰减")

    def test_13_switch_off_restores_old_behavior(self):
        self._seed(48.0, 0.06, 0.05)
        self.pool._last_decay_check = 0.0
        with _CfgSwitch(ENABLE_EXPERIENCE_DECAY_ON_TRIM=False):
            self.pool._trim_capacity()
        self.assertEqual(self.pool._experiences[0]["emotion_intensity"], 0.06)

    def test_14_record_experience_triggers_chain(self):
        """端到端：写入一条经验即走 `_trim_capacity` → `check_decay`（不死锁）。"""
        self.pool._experiences.append({
            "id": "old", "timestamp": time.time() - 48 * 3600,
            "emotion_intensity": 0.06, "decay_rate": 0.05,
            "is_summarized": False, "motivation": "旧动机" * 5,
            "summary": "旧摘要", "content": "旧内容" * 8,
        })
        self.pool._last_decay_check = 0.0
        _id = self.pool.record_experience(
            motivation="新动机" * 5, content="一段足够长的新的经验内容描述文字用于写入")
        self.assertTrue(_id or _id == "")
        self.assertGreaterEqual(self.pool._total_decayed, 1, "写入链路应触发衰减")

    def test_15_rlock_no_deadlock(self):
        """锁可重入性声明：`_trim_capacity` 在持锁时调用 `check_decay`。"""
        import threading
        self.assertIsInstance(self.pool._lock, type(threading.RLock()))

    def test_16_source_wiring(self):
        self.assertIn("if self._m49_decay_on_trim_on():", _EXP)
        self.assertIn("self.check_decay()", _EXP)


# ============================================================ T3-2
class TestEyesStreamStats(unittest.TestCase):
    def test_20_default_is_debug(self):
        """★默认降为 DEBUG（INFO 级不再刷屏）。"""
        self.assertIs(getattr(config, "ENABLE_EYES_STREAM_STATS_INFO", None), False)
        i = _EYES.find("_m49_level = LogLevel.DEBUG")
        self.assertGreater(i, 0, "缺省分支应使用 LogLevel.DEBUG")

    def test_21_low_frequency_info_summary_kept(self):
        """保留低频 INFO 摘要作为存活信号（默认 30 分钟）。"""
        self.assertEqual(config.EYES_STREAM_STATS_INFO_INTERVAL, 1800)
        self.assertIn("EYES_STREAM_STATS_INFO_INTERVAL", _EYES)
        self.assertIn("_m49_summary_interval", _EYES)

    def test_22_switch_on_restores_info_30s(self):
        i = _EYES.find("if _m49_stats_info:")
        self.assertGreater(i, 0)
        block = _EYES[i:i + 260]
        self.assertIn("_m49_interval = 30.0", block)
        self.assertIn("_m49_level = LogLevel.INFO", block)

    def test_23_interval_selection_is_config_driven(self):
        self.assertIn('getattr(\n                    _m49_ecfg, "ENABLE_EYES_STREAM_STATS_INFO", False)',
                      _EYES)
        self.assertIn('getattr(\n                    _m49_ecfg, "EYES_STREAM_STATS_INFO_INTERVAL", 1800.0)',
                      _EYES)


# ============================================================ T3-3
class TestSafeRollover(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m49_t33_")
        self.log = os.path.join(self.d, "t.log")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _handler(self):
        return _lg.SafeRotatingFileHandler(self.log, maxBytes=200,
                                           backupCount=2, encoding="utf-8")

    def test_30_handler_is_subclass(self):
        self.assertTrue(issubclass(_lg.SafeRotatingFileHandler,
                                   logging.handlers.RotatingFileHandler))

    def test_31_permission_error_silenced(self):
        """★核心：PermissionError 不得冒泡、不得打印调用栈。"""
        h = self._handler()
        _orig = logging.handlers.RotatingFileHandler.doRollover
        calls = {"n": 0}

        def _boom(self):
            calls["n"] += 1
            raise PermissionError(32, "另一个程序正在使用此文件")

        logging.handlers.RotatingFileHandler.doRollover = _boom
        try:
            with _CfgSwitch(LOG_ROLLOVER_RETRY=3, LOG_ROLLOVER_RETRY_WAIT=0.01):
                h.doRollover()          # ★不得抛异常
        finally:
            logging.handlers.RotatingFileHandler.doRollover = _orig
            h.close()
        self.assertEqual(calls["n"], 3, "应重试 3 次")

    def test_32_warning_recorded_once(self):
        # ★第59批 T1：rename 失败「且」方案B(复制+截断)也失败时，
        #   才记录冷却 WARNING（方案B 成功即已真正轮转，无需告警）。
        _lg._last_rollover_warn_ts = 0.0
        h = self._handler()
        _orig_roll = logging.handlers.RotatingFileHandler.doRollover
        _orig_copy = _lg.SafeRotatingFileHandler._copy_truncate_rollover

        def _boom(self):
            raise PermissionError(32, "占用")

        def _copy_fail(self):
            raise OSError(13, "拒绝访问")

        logging.handlers.RotatingFileHandler.doRollover = _boom
        _lg.SafeRotatingFileHandler._copy_truncate_rollover = _copy_fail
        try:
            with self.assertLogs("nucleus.logger", level="WARNING") as cm:
                with _CfgSwitch(LOG_ROLLOVER_RETRY=1, LOG_ROLLOVER_RETRY_WAIT=0.01):
                    h.doRollover()
        finally:
            logging.handlers.RotatingFileHandler.doRollover = _orig_roll
            _lg.SafeRotatingFileHandler._copy_truncate_rollover = _orig_copy
            h.close()
        self.assertTrue(any("日志轮转失败" in m for m in cm.output),
                        "彻底降级应记录单行 WARNING")

    def test_33_no_traceback_printed(self):
        """不得调用 logging 的异常打印（`handleError`/`print_exception`）。"""
        h = self._handler()
        printed = []

        def _spy(_self, *a, **kw):
            printed.append(a)

        _orig_print = logging.Handler.handleError
        _orig_roll = logging.handlers.RotatingFileHandler.doRollover

        def _boom(self):
            raise PermissionError(32, "占用")

        logging.Handler.handleError = _spy
        logging.handlers.RotatingFileHandler.doRollover = _boom
        try:
            with _CfgSwitch(LOG_ROLLOVER_RETRY=1, LOG_ROLLOVER_RETRY_WAIT=0.01):
                h.doRollover()
        finally:
            logging.Handler.handleError = _orig_print
            logging.handlers.RotatingFileHandler.doRollover = _orig_roll
            h.close()
        self.assertEqual(printed, [], "不得把异常交给 logging 打印调用栈")

    def test_34_other_oserror_silenced(self):
        h = self._handler()
        _orig = logging.handlers.RotatingFileHandler.doRollover

        def _boom(self):
            raise OSError(13, "拒绝访问")

        logging.handlers.RotatingFileHandler.doRollover = _boom
        try:
            with _CfgSwitch(LOG_ROLLOVER_RETRY=2, LOG_ROLLOVER_RETRY_WAIT=0.01):
                h.doRollover()
        finally:
            logging.handlers.RotatingFileHandler.doRollover = _orig
            h.close()

    def test_35_normal_rollover_still_works(self):
        """正常路径不得被破坏：写满后应真的轮转出 .1。"""
        h = self._handler()
        h.setFormatter(logging.Formatter("%(message)s"))
        lg = logging.getLogger("m49_t33_norm")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(h)
        try:
            for i in range(60):
                lg.info("X" * 40)
        finally:
            lg.removeHandler(h)
            h.close()
        self.assertTrue(os.path.exists(self.log), "当前日志应存在")
        self.assertTrue(os.path.exists(self.log + ".1"),
                        "应轮转出 .1 备份文件")

    def test_36_wired_into_init(self):
        self.assertIn("file_handler = SafeRotatingFileHandler(", _LOG)
        self.assertNotIn("file_handler = logging.handlers.RotatingFileHandler(", _LOG)

    def test_37_config_retry_keys(self):
        self.assertEqual(config.LOG_ROLLOVER_RETRY, 3)
        self.assertEqual(config.LOG_ROLLOVER_RETRY_WAIT, 0.2)


    def test_38_copy_truncate_fallback_rotates(self):
        # ★第59批 T1：rename 失败时方案B 复制+截断应真的轮转出 .1 且清空当前文件。
        h = self._handler()
        h.setFormatter(logging.Formatter("%(message)s"))
        lg = logging.getLogger("m59_t1_copy")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(h)
        try:
            lg.info("HELLO_BEFORE_ROLLOVER")
            h.stream.flush()
            _orig = logging.handlers.RotatingFileHandler.doRollover

            def _boom(self):
                raise PermissionError(32, "占用")

            logging.handlers.RotatingFileHandler.doRollover = _boom
            try:
                with _CfgSwitch(LOG_ROLLOVER_RETRY=1, LOG_ROLLOVER_RETRY_WAIT=0.01):
                    h.doRollover()
            finally:
                logging.handlers.RotatingFileHandler.doRollover = _orig
            self.assertTrue(os.path.exists(self.log + ".1"), "应轮转出 .1 备份")
            _cur = io.open(self.log, encoding="utf-8", errors="replace").read()
            self.assertNotIn("HELLO_BEFORE_ROLLOVER", _cur, "当前文件应已清空")
            _bk = io.open(self.log + ".1", encoding="utf-8", errors="replace").read()
            self.assertIn("HELLO_BEFORE_ROLLOVER", _bk, "旧内容应在 .1 备份中")
        finally:
            lg.removeHandler(h)
            h.close()

    def test_39_warn_cooldown_suppresses_repeat(self):
        # ★第59批 T1：冷却期内重复降级只报一次 WARNING（不刷屏）。
        _lg._last_rollover_warn_ts = 0.0
        _records = []
        _cap = logging.Handler()

        def _handle(record):
            _records.append(record)

        _cap.handle = _handle
        _logger = logging.getLogger("nucleus.logger")
        _logger.addHandler(_cap)
        try:
            h = self._handler()
            _orig_roll = logging.handlers.RotatingFileHandler.doRollover
            _orig_copy = _lg.SafeRotatingFileHandler._copy_truncate_rollover

            def _boom(self):
                raise PermissionError(32, "占用")

            def _copy_fail(self):
                raise OSError(13, "拒绝访问")

            logging.handlers.RotatingFileHandler.doRollover = _boom
            _lg.SafeRotatingFileHandler._copy_truncate_rollover = _copy_fail
            try:
                with _CfgSwitch(LOG_ROLLOVER_RETRY=1, LOG_ROLLOVER_RETRY_WAIT=0.01,
                                LOG_ROLLOVER_WARN_COOLDOWN_SEC=300):
                    h.doRollover()
                    h.doRollover()
            finally:
                logging.handlers.RotatingFileHandler.doRollover = _orig_roll
                _lg.SafeRotatingFileHandler._copy_truncate_rollover = _orig_copy
                h.close()
        finally:
            _logger.removeHandler(_cap)
        _warns = [r.getMessage() for r in _records
                  if r.levelno == logging.WARNING and "日志轮转失败" in r.getMessage()]
        self.assertEqual(len(_warns), 1, "冷却期内应只报 1 次 WARNING")

if __name__ == "__main__":
    unittest.main(verbosity=2)
