# -*- coding: utf-8 -*-
"""第434批刀1 单测：代码审视「全库扫描 + 补丁生成」整体下沉到专用低优先级后台线程。

覆盖：
  * 消阻塞：_review_own_code_issues 提交后台线程后立即返回，不阻塞调用线程
  * 单飞：后台线程在跑时不重复启动（绝不两轮审视叠着抢 CPU）
  * 冷却：冷却期内不启动后台线程
  * 降优先级：_code_review_worker 先降当前线程优先级再扫描
  * 闭环保留：_code_review_worker 仍调用 detect_code_issues（扫描没丢）
"""
import threading
import time

from organs.brain.PulseCodeLearner import PulseCodeLearner


def _make_learner():
    """造一个不打完整 __init__ 的裸实例，只给刀1 相关属性。"""
    learner = object.__new__(PulseCodeLearner)
    learner._last_code_review_at = 0.0
    learner._code_review_worker_thread = None
    learner._self_inspector = None
    learner._log = lambda level, msg: None
    return learner


def test_review_offloads_to_background_thread():
    learner = _make_learner()
    started = threading.Event()

    def fake_worker():
        started.set()
        time.sleep(0.3)

    learner._code_review_worker = fake_worker
    t0 = time.time()
    learner._review_own_code_issues()
    elapsed = time.time() - t0
    assert elapsed < 0.15, f"调用线程被阻塞 {elapsed:.3f}s（应快速返回，不跑扫描）"
    assert started.wait(2.0), "后台线程没启动"
    th = learner._code_review_worker_thread
    assert th is not None and th.is_alive()
    th.join(2.0)


def test_review_single_flight():
    learner = _make_learner()
    started = threading.Event()
    release = threading.Event()

    def fake_worker():
        started.set()
        release.wait(2.0)

    learner._code_review_worker = fake_worker
    learner._review_own_code_issues()
    started.wait(2.0)
    th1 = learner._code_review_worker_thread
    assert th1 is not None and th1.is_alive()
    # 重置冷却再调一次，应跳过（单飞），不得启动第二个线程
    learner._last_code_review_at = 0.0
    learner._review_own_code_issues()
    assert learner._code_review_worker_thread is th1, "单飞失败，启动了第二个线程"
    release.set()
    th1.join(2.0)


def test_review_cooling_window_skips():
    learner = _make_learner()
    learner._last_code_review_at = time.time()  # 刚审视过，20 分钟冷却期内
    called = []
    learner._code_review_worker = lambda: called.append(1)
    learner._review_own_code_issues()
    assert called == [], "冷却期内不该启动后台线程"


def test_code_review_worker_lowers_priority_before_scan():
    learner = _make_learner()

    def boom():
        raise RuntimeError("stop here")

    learner._get_inspector = boom
    learner._code_review_worker()
    backend = getattr(learner, "_code_review_prio_backend", "")
    assert backend in ("win32_lowest", "win32_failed", "posix_nice19", "unavailable"), \
        f"降优先级后端未记录: {backend!r}"


def test_code_review_worker_still_scans():
    learner = _make_learner()
    scanned = []

    class FakeInspector:
        def submit_deferred_scan(self, reason="", **kw):
            return {"started": False, "why": "outside_startup_window",
                    "delay_sec": 30.0}

        def detect_code_issues(self):
            scanned.append(True)
            raise RuntimeError("scan done, stop here")

    learner._get_inspector = lambda: FakeInspector()
    learner._code_review_worker()
    assert scanned == [True], "detect_code_issues 没被调用，扫描闭环被破坏"
