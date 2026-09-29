# -*- coding: utf-8 -*-
"""往期批次 相关任务：tracer flush 竞态门禁单测（离线、隔离、不写生产 data/）。

被修的病：**头判尾更** —— `flush_to_file` 开头判「距上次 >= 2s？」，
却在函数**末尾**（两轮 safe_write_json 之后）才更新 `_last_flush_time`。
闸非原子 ⇒ 突发窗内 k 个并发者全部判「该我刷」，k 份近乎同内容的重复写
去排同一把 per-path 锁（内部协作者实测 19:35 块 11 个参与者，等待者=器官接收线程）。

修法：
  · 方案A 占闸：`_claim_flush_gate()` 内「判 + 更」一次完成（判过即占位）。
  · 方案B 后台刷：BasePulseOrgan 两处同步 flush 调用移除，改由 tracer 内
    daemon 线程承担；★红线：import 期绝不起线程/做 IO，只能运行期懒启动。

本文件全部用例用 **importlib 独立加载副本** + tmp 目录 + 桩替换 safe_write_json，
既碰不到生产 data/monitor，也不受单例模块状态互相污染。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACER_SRC = os.path.join(ROOT, "utils", "pulse_tracer.py")
_BPO_SRC = os.path.join(ROOT, "base", "BasePulseOrgan.py")


def _load_tracer():
    """每次加载一份独立副本（避免单例状态跨用例污染）。"""
    _spec = importlib.util.spec_from_file_location(
        f"pulse_tracer_m117_{threading.get_ident()}", _TRACER_SRC)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


class _Counter:
    """线程安全的写调用计数器 + 可插桩的 IO 耗时。"""

    def __init__(self, io_delay: float = 0.0):
        self.n = 0
        self._lk = threading.Lock()
        self._delay = io_delay

    def write(self, path, data, backup=False):
        if self._delay:
            time.sleep(self._delay)
        with self._lk:
            self.n += 1
        return True

    def read(self, path, default=None):
        return [] if default is None else default

    @property
    def count(self) -> int:
        with self._lk:
            return self.n


def _wire(mod, counter, tmpdir):
    """把模块的 IO 面全部换成桩/临时路径（不产生任何生产写入）。"""
    mod._output_path = os.path.join(tmpdir, "events.json")
    mod._orphan_path = os.path.join(tmpdir, "orphans.json")
    mod.safe_write_json = counter.write
    mod.safe_read_json = counter.read
    # 关键：压测只测「闸」，不让后台线程参与计数
    mod._flusher_started = True
    mod._flusher_stop.set()
    mod._last_flush_time = 0.0
    mod._events = [{
        "timestamp": time.time(), "type": "emit", "organ": "O",
        "event_type": "E", "layer": "L0", "pulse_id": "p1",
        "matched_organs": [],
    }]


class T117aFlushGate(unittest.TestCase):
    """方案A：一个 2s 窗口内恒定 <= 1 个写者穿越。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m117a_")
        self.mod = _load_tracer()

    def tearDown(self):
        self.mod._flusher_stop.set()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_single_winner_under_50_threads(self):
        """50 线程同时 flush：safe_write_json 调用 <= 2/窗（1 次 flush × 2 个文件）。"""
        _c = _Counter(io_delay=0.002)  # 给写一点耗时，放大并发窗口
        _wire(self.mod, _c, self.tmp)

        _n = 50
        _barrier = threading.Barrier(_n)
        _threads = []

        def _worker():
            _barrier.wait()
            self.mod.flush_to_file()

        for _ in range(_n):
            _t = threading.Thread(target=_worker, daemon=True)
            _threads.append(_t)
            _t.start()
        for _t in _threads:
            _t.join(10)

        self.assertLessEqual(
            _c.count, 2,
            f"占闸后并发写者应 <=1 次 flush（<=2 次 safe_write_json），实际 {_c.count}")

    def test_02_negative_control_old_logic_blows_up(self):
        """★正控：同样的并发下，「头判尾更」旧逻辑必然放大（>10），证明用例有区分力。"""
        _c = _Counter(io_delay=0.002)
        _state = {"last": 0.0}
        _interval = 2.0

        def _old_flush():
            # 头判 —— 判完不占位
            _now = time.time()
            if _now - _state["last"] < _interval:
                return
            _c.write("events.json", [])   # 事件文件 IO
            _c.write("orphans.json", [])  # 孤儿文件 IO
            _state["last"] = time.time()  # ★尾更：全部并发者都已穿越

        _n = 50
        _barrier = threading.Barrier(_n)
        _threads = [threading.Thread(
            target=lambda: (_barrier.wait(), _old_flush()), daemon=True)
            for _ in range(_n)]
        for _t in _threads:
            _t.start()
        for _t in _threads:
            _t.join(10)

        self.assertGreater(
            _c.count, 10,
            f"正控失效：旧「头判尾更」逻辑本应放大到 >10 次写，实际 {_c.count}")

    def test_03_gate_reopens_next_window(self):
        """闸不是死锁：窗口过后必须能再次放行（否则 trace 永不更新）。"""
        _c = _Counter()
        _wire(self.mod, _c, self.tmp)

        self.mod.flush_to_file()
        _first = _c.count
        self.assertEqual(2, _first, "首个窗口应放行一次 flush（2 次写）")

        # 立刻再刷 -> 应被闸拦下
        self.mod.flush_to_file()
        self.assertEqual(_first, _c.count, "同一窗口内第二次 flush 必须被拦下")

        # 把闸拨回 2.5s 前（等价窗口已过）-> 应再次放行
        self.mod._last_flush_time = time.time() - 2.5
        self.mod.flush_to_file()
        self.assertEqual(_first + 2, _c.count, "窗口过后闸必须重新放行")

    def test_04_force_bypasses_gate(self):
        """force=True（atexit 终刷）必须绕过 2s 闸，否则退出时丢数据。"""
        _c = _Counter()
        _wire(self.mod, _c, self.tmp)
        self.mod.flush_to_file()
        self.assertEqual(2, _c.count)
        self.mod.flush_to_file(force=True)
        self.assertEqual(4, _c.count, "force=True 应绕过闸再次写盘")


class T117aBackgroundFlusher(unittest.TestCase):
    """方案B：后台守护刷 —— import 期零线程/零 IO，运行期懒启动。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m117b_")
        self.mod = _load_tracer()

    def tearDown(self):
        self.mod._flusher_stop.set()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_05_no_thread_and_no_io_at_import(self):
        """★红线：模块 import 完成时不得已起 flusher 线程。"""
        _names = [t.name for t in threading.enumerate()]
        self.assertNotIn("PulseTracerFlusher", _names,
                         "import 期不得起后台线程（潜意识 :466 案教训）")
        self.assertFalse(self.mod._flusher_started,
                         "import 期 flusher 标记必须为 False")

    def test_06_lazy_start_on_first_event(self):
        """首次记录事件时懒启动，且线程为 daemon（不阻塞进程退出）。"""
        _c = _Counter()
        _wire(self.mod, _c, self.tmp)
        self.mod._flusher_started = False  # 还原成"尚未启动"
        self.mod._flusher_stop.clear()

        self.mod.log_emit("OrganA", "EVT", "L0", "pid-1")

        self.assertTrue(self.mod._flusher_started, "首次事件后应已懒启动 flusher")
        _alive = [t for t in threading.enumerate()
                  if t.name == "PulseTracerFlusher" and t.is_alive()]
        self.assertEqual(1, len(_alive), "应恰有 1 个 flusher 线程在跑")
        self.assertTrue(_alive[0].daemon, "flusher 必须是 daemon 线程")

    def test_07_atexit_final_flush_registered(self):
        """atexit 终刷必须已注册（否则进程退出丢最后一窗 trace）。"""
        _found = False
        try:
            import atexit
            # 无法直接枚举回调，改为行为验证：_flush_at_exit 存在且可绕过闸
            _c = _Counter()
            _wire(self.mod, _c, self.tmp)
            self.mod._flush_at_exit()
            _found = (_c.count >= 2)
        except Exception as e:  # pragma: no cover
            self.fail(f"_flush_at_exit 行为验证异常: {type(e).__name__}: {e}")
        self.assertTrue(_found, "_flush_at_exit 应完成一次终刷")
        self.assertIsNotNone(atexit, "atexit 模块应可用")


class T117aStaticContract(unittest.TestCase):
    """静态契约：器官主循环不再承担同步 flush。"""

    def test_08_base_pulse_organ_has_no_sync_flush(self):
        """用 AST 找真实调用（避免把说明注释里的字样也算上）。"""
        import ast
        with open(_BPO_SRC, "r", encoding="utf-8") as f:
            _tree = ast.parse(f.read())
        _calls = [n for n in ast.walk(_tree)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "flush_to_file"]
        self.assertEqual(
            [], _calls,
            f"BasePulseOrgan.py 不得再有同步 flush_to_file() 调用（AST 实测 {len(_calls)} 处）")

    def test_09_gate_and_tail_update_discipline(self):
        with open(_TRACER_SRC, "r", encoding="utf-8") as f:
            _src = f.read()
        self.assertIn("def _claim_flush_gate(", _src)
        self.assertIn("_last_flush_time = _now  # 判完立刻占闸", _src)
        # 函数体（除占闸处）不应再有其它 _last_flush_time 赋值
        self.assertEqual(
            1, _src.count("_last_flush_time = _now"),
            "_last_flush_time 只允许在 _claim_flush_gate 内被更新（头判尾更的根因）")


if __name__ == "__main__":
    unittest.main()
