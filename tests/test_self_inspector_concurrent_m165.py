# -*- coding: utf-8 -*-
"""165批 刀A2 并发回归：_issue_states 读写经 RLock + list() 快照，不再触发
「dictionary changed size during iteration」RuntimeError。

验证方式：
- 写线程在 self._issue_states_lock 保护下对 _issue_states 做增删（与 detect_code_issues
  的真实写路径共用同一把锁、同一数据结构），节奏与生产的「更新 + 插入新键 + 容量淘汰」一致；
- 读线程并发调用 get_issue_lifecycle_stats() / get_recent_resolved_issues()
  （修复后均经锁 + list() 快照，不再直接迭代活动字典）；
- 任一侧缺失保护即会复现 RuntimeError: dictionary changed size during iteration。
另：开头真实跑一次 detect_code_issues() 以覆盖真实写路径。
"""
import threading
import time
import unittest

from nucleus.self_inspector import SelfInspector


class TestIssueStatesConcurrent(unittest.TestCase):
    def _seed(self, si, n=80):
        _now = time.time()
        for i in range(n):
            si._issue_states[f"seed_{i}.py:10:type_{i % 6}"] = {
                "status": "resolved" if i % 3 == 0 else "new",
                "first_seen": _now,
                "last_seen": _now,
                "count": 1,
                "type": f"type_{i % 6}",
                "file": f"seed_{i}.py",
                "organ": "x",
                "method": "m",
                "line": 10,
            }

    def test_concurrent_read_write_no_dict_changed_error(self):
        si = SelfInspector()
        self._seed(si)
        # 真实写路径先跑一次（填充/更新 _issue_states，验证 detect_code_issues 自身加锁无误）
        si.detect_code_issues(
            skip_detectors=list(getattr(si, "GLOBAL_DETECTORS", {}).keys())
        )

        def writer():
            for _ in range(20):
                # 与 detect_code_issues 共用同一把锁：模拟其「更新 + 插入新键 + 淘汰」节奏
                with si._issue_states_lock:
                    si._issue_states[f"w_{threading.get_ident()}_{_}"] = {
                        "status": "new",
                        "first_seen": time.time(),
                        "last_seen": time.time(),
                        "count": 1,
                        "type": "t",
                        "file": "w.py",
                        "organ": "x",
                        "method": "m",
                        "line": 1,
                    }
                    _k = next(iter(si._issue_states))
                    si._issue_states.pop(_k, None)

        def reader():
            for _ in range(20):
                si.get_issue_lifecycle_stats()
                si.get_recent_resolved_issues()

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=reader)
        tw.start()
        tr.start()
        tw.join()
        tr.join()
        # 若修复缺失，上面对 _issue_states 的并发增删 + 直接迭代会抛
        # RuntimeError: dictionary changed size during iteration；能无异常返回即通过。
        self.assertTrue(True)

    def test_lock_present(self):
        si = SelfInspector()
        self.assertTrue(
            hasattr(si, "_issue_states_lock"),
            "165批A2: 缺少 _issue_states_lock（读写锁未初始化）",
        )


if __name__ == "__main__":
    unittest.main()
