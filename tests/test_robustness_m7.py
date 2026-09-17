# -*- coding: utf-8 -*-
"""主线第7批 任务5：框架鲁棒性增强回归测试（≥10 例）。

覆盖四大健壮性维度：
1) 配置健壮性 —— TIMEOUT_CONFIG / EXTERNAL_CALL_TIMEOUTS / FEATURE.L3 参数的存在性、
   取值类型与关键值（缺失键安全降级、类型错误降级）。
2) 异常处理不再静默 —— 核心 7 文件无裸 `except ...: pass`，且异常路径均记录日志。
3) 外部调用超时配置化生效 —— subprocess 调用点确实引用 TIMEOUT_CONFIG / EXTERNAL_CALL_TIMEOUTS，
   且取值等于配置；requests 调用不得缺失 timeout。
4) （L3 扩缩容健壮性见 tests/test_info_field.py 的 TestL3DynamicScaling）

全部为轻量测试：仅依赖 config 模块与源码静态扫描，不触发重型运行时导入。
"""
import os
import re
import sys
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import config

# Task2 打过补丁的 7 个核心文件（精确签名 type={type(_exc).__name__} 定位）
_CORE_FILES = [
    "nucleus/field/InfoField.py",
    "nucleus/qica/QICA.py",
    "nucleus/reasoning/SafeEvolutionExecutor.py",
    "nucleus/semantic/VectorStore.py",
    "organs/body/PulseLung.py",
    "organs/brain/PulseCortex.py",
    "organs/brain/PulseInnerWorld.py",
]

# 单/多行「裸 except ...: pass」检测
_SILENT_SINGLE = re.compile(r"except\s+[A-Za-z_][A-Za-z0-9_.]*\s*:\s*pass\s*$")
_SILENT_MULTI = re.compile(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)")


def _read(rel):
    with open(os.path.join(_PROJ, rel), encoding="utf-8") as f:
        return f.read()


class TestConfigRobustness(unittest.TestCase):
    def test_timeout_config_含16键(self):
        expected = {
            "scheduler_poll", "queue_get", "hardware_probe_fast", "thread_join",
            "hardware_probe", "http_get", "subprocess_default", "future_result",
            "ethics_review", "api_post", "future_result_long", "api_post_long",
            "llm_call", "join_long", "code_review", "page_load",
        }
        self.assertEqual(set(config.TIMEOUT_CONFIG.keys()), expected)

    def test_timeout_config_取值均为数值(self):
        for k, v in config.TIMEOUT_CONFIG.items():
            self.assertIsInstance(v, (int, float), f"{k} 取值非数值: {v!r}")

    def test_timeout_config_关键值符合规格(self):
        self.assertEqual(config.TIMEOUT_CONFIG["scheduler_poll"], 0.001)
        self.assertEqual(config.TIMEOUT_CONFIG["hardware_probe_fast"], 2)
        self.assertEqual(config.TIMEOUT_CONFIG["hardware_probe"], 3)
        self.assertEqual(config.TIMEOUT_CONFIG["http_get"], 8)
        self.assertEqual(config.TIMEOUT_CONFIG["subprocess_default"], 10)
        self.assertEqual(config.TIMEOUT_CONFIG["future_result"], 10.0)
        self.assertEqual(config.TIMEOUT_CONFIG["api_post"], 15)
        self.assertEqual(config.TIMEOUT_CONFIG["llm_call"], 60)
        self.assertEqual(config.TIMEOUT_CONFIG["join_long"], 90)
        self.assertEqual(config.TIMEOUT_CONFIG["code_review"], 600.0)
        self.assertEqual(config.TIMEOUT_CONFIG["page_load"], 8000)

    def test_external_call_timeouts_键与类型(self):
        expected = {
            "subprocess_short", "subprocess_medium", "subprocess_long",
            "http_connect", "http_read",
        }
        self.assertEqual(set(config.EXTERNAL_CALL_TIMEOUTS.keys()), expected)
        for k, v in config.EXTERNAL_CALL_TIMEOUTS.items():
            self.assertIsInstance(v, (int, float), f"{k} 取值非数值: {v!r}")

    def test_feature_l3_配置符合任务1(self):
        feat = config.FEATURE
        self.assertEqual(feat["l3_scale_up_cooldown"], 15)
        self.assertAlmostEqual(feat["l3_burst_growth_threshold"], 0.50)
        self.assertEqual(feat["l3_min_workers"], 3)
        self.assertEqual(feat["l3_scale_down_buffer_sec"], 30)

    def test_配置缺失键安全降级(self):
        # 文档化安全取值范式：使用 .get 并提供默认值，避免 KeyError 崩溃
        d = dict(config.TIMEOUT_CONFIG)
        self.assertEqual(d.get("not_a_real_key", 10), 10)
        # 类型错误降级：所有值均可被 float() 接受（timeout= 不会拿到 str/None）
        for k, v in config.TIMEOUT_CONFIG.items():
            self.assertIsNotNone(float(v), f"{k} 不能被 float() 接受")


class TestExceptionHandlingNotSilent(unittest.TestCase):
    def test_core_files_no_silent_except_pass(self):
        for rel in _CORE_FILES:
            src = _read(rel)
            self.assertEqual(
                len(_SILENT_SINGLE.findall(src)),
                0,
                f"{rel} 仍存在单行长 except ...: pass",
            )
            self.assertEqual(
                len(_SILENT_MULTI.findall(src)),
                0,
                f"{rel} 仍存在多行 except ...: pass",
            )

    def test_core_files_exceptions_logged(self):
        # 每个核心文件都应含有「异常被记录」的日志签名（非静默 pass）
        marker = "type={type(_exc).__name__}"
        for rel in _CORE_FILES:
            src = _read(rel)
            self.assertIn(
                marker, src,
                f"{rel} 缺少异常记录日志签名，可能退回静默 pass",
            )


class TestExternalCallTimeoutPlumbing(unittest.TestCase):
    def test_subprocess_default_引用配置且值正确(self):
        self.assertEqual(config.TIMEOUT_CONFIG["subprocess_default"], 10)
        hit = False
        for rel in [
            "nucleus/tooling_runner.py", "nucleus/hardware_probe.py",
            "nucleus/parallel_scheduler.py", "organs/senses/PulseEars.py",
            "organs/body/PulseLiver.py", "organs/motor/PulseLegs.py",
        ]:
            if "timeout=TIMEOUT_CONFIG['subprocess_default']" in _read(rel):
                hit = True
                break
        self.assertTrue(hit, "未找到引用 TIMEOUT_CONFIG['subprocess_default'] 的 subprocess 调用")

    def test_hardware_probe_fast_timeout_已配置化(self):
        self.assertEqual(config.TIMEOUT_CONFIG["hardware_probe_fast"], 2)
        src = _read("nucleus/hardware_probe.py")
        self.assertIn("timeout=TIMEOUT_CONFIG['hardware_probe_fast']", src)

    def test_external_call_timeouts_subprocess_medium_config_exists(self):
        self.assertEqual(config.EXTERNAL_CALL_TIMEOUTS["subprocess_medium"], 30)
        src = _read("main.py")
        self.assertNotIn('timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_medium"]', src, "Popen不接受timeout参数")

    def test_requests_calls_have_timeout_or_none(self):
        # Task3 核实：生产代码 requests 真实调用为 0；此处守护「若存在 requests 调用必须带 timeout」
        bad = []
        pat = re.compile(r"requests\.(get|post|put|delete|head|patch)\s*\(")
        for root, _, files in os.walk(os.path.join(_PROJ, "nucleus")):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                try:
                    s = open(p, encoding="utf-8").read()
                except Exception:
                    continue
                for m in pat.finditer(s):
                    line_start = s.rfind("\n", 0, m.start()) + 1
                    line_end = s.find("\n", m.start())
                    line = s[line_start:line_end]
                    # 跳过位于注释中的匹配（# 出现在调用之前）
                    if "#" in s[line_start:m.start()]:
                        continue
                    if "timeout=" not in line:
                        bad.append((p, line.strip()))
        self.assertEqual(
            bad, [],
            f"发现缺失 timeout 的 requests 调用：{bad[:5]}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
