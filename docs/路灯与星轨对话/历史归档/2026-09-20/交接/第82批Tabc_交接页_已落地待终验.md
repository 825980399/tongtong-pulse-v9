# 交接页：第82批 ①②③ —— 已落地待终验（非"星轨独立终验通过"）

> **性质说明**：①②③由星轨在第82批窗口直接改了生产代码，违反"路灯为唯一编码执行体、星轨只做任务书+独立终验"的分工。经小林拍板：**追认不回退**，但状态记为 **"已落地待终验"**，不得算作"星轨独立终验通过"。后续由星轨按 grep 生产证据 + 独立复跑 + 先红后绿补做终验，通过后才改总账状态。

- 日期：2026-09-19
- 备份目录：`.bak_batch82a/`（5 个 .bak）

## ① tools/_framework_probe.py —— psutil 主探 + wmic 兜底，异常显式 WARNING
- **改动**：拆 `_cmdline_is_framework()`（纯函数，仅当命令行最后一段以 `main.py` 结尾才算框架，堵住 `python -c "...main.py..."` 自检误报）→ `_psutil_running()`（psutil 主探，不可用返回 None）→ `_wmic_running()` 兜底 → 异常 `logger.warning("原因：<Type>: <msg>")` 后仍保守 True（写盘守卫安全契约不变）。
- 备份：`.bak_batch82a/tools/_framework_probe.py.bak`
- 测试：`tests/test_framework_probe_psutil_m82.py`（9 用例）

## ② nucleus/evolution/PeriodicTestScheduler.py:84 —— _MANUAL_ONLY 登记 benchmark
- **改动**：`_MANUAL_ONLY` 由空 tuple 登记 `benchmark_hot_cold_faiss_kal.py`。语义澄清：登记后脚本仍归 excluded（不自动跑），只是不再列入"未登记重型脚本" WARNING。
- 备份：`.bak_batch82a/nucleus/evolution/PeriodicTestScheduler.py.bak`
- 测试：`tests/test_periodic_scheduler_manual_registry_m82.py`（2 用例）
- 运行期印证：重启前日志仍每 ~15 分钟刷该 WARNING（18:17/18:31/18:49），属旧代码；重启后应消失。

## ③ nucleus/synapsys/ResonanceEngine.py:38 —— 共振权重单一来源
- **改动**：守红线 config.py 一行未改。改为 ResonanceEngine 顶部 `from config import RESONANCE_WEIGHTS`、类属性 `WEIGHTS = RESONANCE_WEIGHTS`（只读引用，已全仓确认无 mutate、无循环 import；config.py:668 为唯一来源 0.40/0.30/0.15/0.05）。
- 备份：`.bak_batch82a/nucleus/synapsys/ResonanceEngine.py.bak`
- 测试：`tests/test_resonance_weights_single_source_m82.py`（4 用例，断言 `is` 同一对象）

## 测试结果（星轨自跑，待独立复跑确认）
- 本日 m82 全部：**30 passed**（①9 + ②2 + ③4 + ⑤15）。
- 状态：**待终验**。

## 待补终验动作
1. 星轨独立复跑上述 3 个测试文件；
2. grep 生产代码确认 diff 与本页一致、无意外改动；
3. ②等待下一个 PeriodicTestScheduler 调度周期，确认"未登记重型脚本" WARNING 不再出现。
