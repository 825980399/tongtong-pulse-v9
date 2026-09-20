"""第82批 T-b：PeriodicTestScheduler._MANUAL_ONLY 登记 benchmark_hot_cold_faiss_kal.py。

背景：
  tools/benchmark_hot_cold_faiss_kal.py 是第81批冷热/FAISS/KAL 性能基准脚本，
  按 T7/T8 设计（CPU 计时比值 + 压测自扰）只应人工触发，不得进自动集。
  但 `_MANUAL_ONLY` 当前为空 → discover_scripts 每次把它列入
  "发现未登记的重型脚本(请人工评估后登记)" WARNING，长期噪音且暗示"待评估"。

本测试锁定：
  1. 静态登记：_MANUAL_ONLY 必须显式包含该脚本（已评估、仅人工）。
  2. 行为：登记后 discover_scripts 仍把它归 excluded（不自动跑，安全不变），
     且不再进 unregistered（WARNING 噪音消除）。
"""
from __future__ import annotations

import logging

from nucleus.evolution.PeriodicTestScheduler import PeriodicTestScheduler

BENCH = "benchmark_hot_cold_faiss_kal.py"


class TestManualRegistry:
    def test_manual_only_registers_benchmark(self):
        # 红：当前 _MANUAL_ONLY=()，断言必失败
        assert BENCH in PeriodicTestScheduler._MANUAL_ONLY, (
            f"{BENCH} 必须显式登记进 _MANUAL_ONLY（已评估为仅人工运行），"
            "否则每次 discover_scripts 都会打'未登记重型脚本' WARNING 噪音"
        )

    def test_discover_keeps_excluded_and_clears_unregistered(self, tmp_path, caplog):
        # 隔离 project_root：tools/ 下放一个空 benchmark 脚本
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / BENCH).write_text("# benchmark stub\n", encoding="utf-8")

        sched = PeriodicTestScheduler(str(tmp_path))
        caplog.set_level(logging.WARNING, logger="PeriodicTestScheduler")
        result = sched.discover_scripts()

        # 安全契约不变：benchmark 永不进 light/heavy 自动集
        assert BENCH not in result["light"]
        assert BENCH not in result["heavy"]
        # 仍归 excluded（不自动跑）
        assert BENCH in result["excluded"]
        # 登记后不再因它打"未登记重型脚本" WARNING
        assert BENCH not in result["unregistered"], (
            f"{BENCH} 已在 _MANUAL_ONLY 登记，不应再进 unregistered 噪音列表"
        )
        warn_text = " ".join(r.getMessage() for r in caplog.records)
        assert BENCH not in warn_text or "未登记" not in warn_text, (
            f"discover 不应再为 {BENCH} 打'未登记重型脚本' WARNING"
        )
