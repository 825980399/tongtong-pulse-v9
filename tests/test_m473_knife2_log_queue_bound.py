# -*- coding: utf-8 -*-
"""第473批 刀2 门控单测：日志队列无界 → 有界 + 满则丢（台账 T4）。

根因
----
`nucleus/logger.py` 用 `queue.Queue(-1)` 建日志队列，容量无上限。
消费方是 `QueueListener` 单线程写盘，一旦消费变慢：

    磁盘慢 / 日志轮转卡住 / 全进程被 OS 换出
        ⇒ 生产速度 > 消费速度
        ⇒ 队列无限增长
        ⇒ 每条 LogRecord 含 args 约 1~3KB，一小时几万条就是几十 MB
        ⇒ 长时间挂死直接吃掉进程内存

修复口径（三条硬约束）
----------------------
1. 队列**有界**，容量取 `config.LOG_QUEUE_MAXSIZE`（默认 20000）。
2. 满时**丢弃新日志**，绝不阻塞业务线程 —— 阻塞式入队会让所有打日志的线程
   排队等磁盘，那正是 434 刀2 之前那条死路。
3. 丢弃要**可观测**：累计计数 + 限流告警（≤1 条/60s），且告警**不经过 logging**
   （否则在已满的队列上递归）。
"""

import logging
import os
import queue
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.logger as nl

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rec(msg="x", level=logging.INFO):
    return logging.LogRecord("t473", level, __file__, 1, msg, None, None)


class TestA_HandlerNeverBlocks(unittest.TestCase):
    """判据：队列满时必须立即返回，且丢弃可计数。"""

    def setUp(self):
        self._saved_dropped = nl._M473_QUEUE_DROPPED
        self._saved_warn_at = nl._M473_QUEUE_DROP_WARN_AT
        nl._M473_QUEUE_DROPPED = 0
        nl._M473_QUEUE_DROP_WARN_AT = 0.0

    def tearDown(self):
        nl._M473_QUEUE_DROPPED = self._saved_dropped
        nl._M473_QUEUE_DROP_WARN_AT = self._saved_warn_at

    def test_01_full_queue_does_not_block(self):
        q = queue.Queue(maxsize=3)
        h = nl._M473NonBlockingQueueHandler(q)
        for _ in range(3):
            h.enqueue(_rec())
        self.assertEqual(q.qsize(), 3, "前 3 条应正常入队")

        _t0 = time.time()
        for _ in range(50):          # 超容量 50 条
            h.enqueue(_rec())
        _cost = time.time() - _t0
        self.assertLess(_cost, 1.0,
                        f"队列满后入队耗时 {_cost:.3f}s —— 说明在阻塞业务线程")
        self.assertEqual(q.qsize(), 3, "满队列不应再增长")
        self.assertEqual(nl._M473_QUEUE_DROPPED, 50,
                         f"应记 50 条丢弃，实得 {nl._M473_QUEUE_DROPPED}")

    def test_02_queue_full_not_raising(self):
        """queue.Full 不得冒泡成异常（否则 handleError 会打整段 traceback 刷屏）。"""
        q = queue.Queue(maxsize=1)
        h = nl._M473NonBlockingQueueHandler(q)
        h.enqueue(_rec())
        try:
            h.enqueue(_rec())
        except Exception as e:
            self.fail(f"满队列入队抛了异常：{type(e).__name__}: {e}")

    def test_03_drop_warn_is_throttled(self):
        """首次丢弃要告警，随后 60s 内必须静默（否则每丢一条刷一行）。"""
        import io

        q = queue.Queue(maxsize=1)
        h = nl._M473NonBlockingQueueHandler(q)
        h.enqueue(_rec())

        _buf = io.StringIO()
        _old = sys.stderr
        sys.stderr = _buf
        try:
            h.enqueue(_rec())      # 第 1 次丢弃 ⇒ 告警
            h.enqueue(_rec())      # 第 2 次丢弃 ⇒ 静默（限流窗口内）
            h.enqueue(_rec())
        finally:
            sys.stderr = _old
        _out = _buf.getvalue()
        self.assertEqual(_out.count("[473刀1]"), 1,
                         f"限流窗口内应只告警 1 次，实得 {_out.count('[473刀1]')} 次：{_out!r}")


class TestB_BoundedQueueWiring(unittest.TestCase):
    """判据：创建处必须用 config 里的容量，且挂的是非阻塞 handler。"""

    def test_01_config_default(self):
        self.assertEqual(getattr(config, "LOG_QUEUE_MAXSIZE", None), 20000,
                         "config.LOG_QUEUE_MAXSIZE 默认值应为 20000")

    def test_02_source_uses_maxsize(self):
        with open(os.path.join(_ROOT, "nucleus", "logger.py"), encoding="utf-8") as _fh:
            src = _fh.read()
        self.assertIn("queue.Queue(maxsize=_m473_qmax)", src,
                      "队列创建处没改成本地 maxsize")
        self.assertIn('getattr(config, "LOG_QUEUE_MAXSIZE", 20000)', src,
                      "容量没有从 config 读，回滚/调参无处可下手")

    def test_03_no_unbounded_queue_in_code(self):
        """源码级：`queue.Queue(-1)` 不得再出现在**代码行**（注释里说明根因允许）。"""
        p = os.path.join(_ROOT, "nucleus", "logger.py")
        with open(p, encoding="utf-8") as _fh:
            _lines = _fh.readlines()
        for _i, _line in enumerate(_lines, 1):
            _s = _line.strip()
            if _s.startswith("#"):
                continue
            self.assertNotIn("queue.Queue(-1)", _s,
                             f"logger.py:{_i} 仍有代码级无界队列：{_s}")

    def test_04_handler_is_nonblocking_class(self):
        with open(os.path.join(_ROOT, "nucleus", "logger.py"), encoding="utf-8") as _fh:
            src = _fh.read()
        self.assertIn("_queue_handler = _M473NonBlockingQueueHandler(_log_queue)", src,
                      "创建处仍挂标准 QueueHandler（Full 会冒到 handleError）")


class TestC_StatsReadable(unittest.TestCase):
    """判据：必须能查到积压/丢弃，否则出事时看不出来。"""

    def setUp(self):
        self._saved = nl._QUEUE_HANDLER
        self._saved_dropped = nl._M473_QUEUE_DROPPED
        nl._M473_QUEUE_DROPPED = 0

    def tearDown(self):
        nl._QUEUE_HANDLER = self._saved
        nl._M473_QUEUE_DROPPED = self._saved_dropped

    def test_01_not_initialized(self):
        nl._QUEUE_HANDLER = None
        _s = nl.get_log_queue_stats()
        self.assertIs(_s["enabled"], False)
        self.assertEqual(_s["size"], 0)

    def test_02_initialized_reports_fill(self):
        q = queue.Queue(maxsize=10)
        nl._QUEUE_HANDLER = nl._M473NonBlockingQueueHandler(q)
        for _ in range(4):
            q.put_nowait(_rec())
        _s = nl.get_log_queue_stats()
        self.assertIs(_s["enabled"], True)
        self.assertEqual(_s["size"], 4)
        self.assertEqual(_s["maxsize"], 10)
        self.assertAlmostEqual(_s["fill_ratio"], 0.4, places=4)

    def test_03_dropped_surfaced(self):
        q = queue.Queue(maxsize=1)
        nl._QUEUE_HANDLER = nl._M473NonBlockingQueueHandler(q)
        q.put_nowait(_rec())
        nl._QUEUE_HANDLER.enqueue(_rec())   # 触发 1 次丢弃
        self.assertEqual(nl.get_log_queue_stats()["dropped"], 1,
                         "丢弃计数没有出现在 stats 里 —— 现场无法自证")


if __name__ == "__main__":
    unittest.main(verbosity=2)
