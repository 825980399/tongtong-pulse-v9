# -*- coding: utf-8 -*-
"""第54批 T2 门控测试：PulseCodeLearner 硬编码 risk<=1 消除（P2-375）。

背景
----
`PulseCodeLearner.py:3322` 原为硬编码 `if _auto_apply_enabled and isinstance(_risk, int) and _risk <= 1:`，
与 `config.EVOLUTION_CONFIG.auto_apply_max_risk`（当前 2）不一致 —— 这是全系统**第三处**补丁门槛。
本批改为运行时读配置，并用灰度开关 `ENABLE_PCL_RISK_FROM_CONFIG` 保障零回归。

覆盖
----
① 生产源码不再硬编码 `risk <= 1`（且已改用 `_risk_max`）
② `_risk_max` 确从 `EVOLUTION_CONFIG.auto_apply_max_risk` 读取
③ 灰度开关存在且默认 True
④ 同构复现：开关开 → 取配置值；开关关 → 回退 1（改造前行为）
⑤ 防漂移：改配置值，计算结果跟随（★断言写在 with 块内，铁律 49）
⑥ 全库扫描（用统一排除列表 C3）：生产代码中无其他 `risk <= N` 硬编码残留
"""
import contextlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io  # noqa: E402
import unittest  # noqa: E402

import config  # noqa: E402
from nucleus.data import exclude_dirs  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PCL_REL = os.path.join("organs", "brain", "PulseCodeLearner.py")


def _read(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace").read()


def _compute_risk_max(cfg_ev, ev_cfg):
    """★与 PulseCodeLearner 内 `_risk_max` 计算**同构**（改生产须同步改本函数）。"""
    risk_max = 1
    try:
        if getattr(cfg_ev, "ENABLE_PCL_RISK_FROM_CONFIG", True):
            risk_max = int(ev_cfg.get("auto_apply_max_risk", 1))
    except Exception:
        # ★铁律 10：禁止 except: pass；此处为测试复现，异常即回退默认值
        risk_max = 1
    return risk_max


@contextlib.contextmanager
def _cfg_switch(attr, value):
    """临时改写 config 属性，退出还原（★铁律 49：相关断言必须写在块内）。"""
    had = hasattr(config, attr)
    old = getattr(config, attr, None)
    setattr(config, attr, value)
    try:
        yield
    finally:
        if had:
            setattr(config, attr, old)
        else:
            delattr(config, attr)


class TestPclRiskThresholdM54(unittest.TestCase):
    """★第54批 T2：硬编码门槛统一。"""

    def test_01_no_hardcoded_risk_1(self):
        """生产源码不再硬编码 `risk <= 1`，且已改用 `_risk_max`。"""
        src = _read(_PCL_REL)
        self.assertNotIn("_risk <= 1", src, "仍残留硬编码 risk<=1（P2-375 未修复）")
        self.assertIn("_risk <= _risk_max", src, "未改用 _risk_max")

    def test_02_reads_from_config(self):
        """`_risk_max` 确从 EVOLUTION_CONFIG.auto_apply_max_risk 读取。"""
        src = _read(_PCL_REL)
        self.assertIn('_ev_cfg.get("auto_apply_max_risk", 1)', src,
                      "未从 auto_apply_max_risk 读取")
        self.assertIn("ENABLE_PCL_RISK_FROM_CONFIG", src, "缺少灰度开关判据")

    def test_03_switch_exists_and_default_true(self):
        """灰度开关存在且默认开（修复生效）。"""
        self.assertTrue(hasattr(config, "ENABLE_PCL_RISK_FROM_CONFIG"),
                        "config 缺少 ENABLE_PCL_RISK_FROM_CONFIG")
        self.assertIs(config.ENABLE_PCL_RISK_FROM_CONFIG, True)
        self.assertIn("auto_apply_max_risk", config.EVOLUTION_CONFIG)

    def test_04_isomorphic_switch_on_off(self):
        """开关开 → 取配置值；开关关 → 回退 1（改造前行为，零回归）。"""
        evo = config.EVOLUTION_CONFIG
        self.assertEqual(_compute_risk_max(config, evo),
                         int(evo["auto_apply_max_risk"]))
        with _cfg_switch("ENABLE_PCL_RISK_FROM_CONFIG", False):
            self.assertEqual(_compute_risk_max(config, evo), 1,
                             "关闭开关应回退到改造前的 1")

    def test_05_follows_config_value(self):
        """★防漂移：改配置值，计算结果必须跟随（断言在 with 块内）。"""
        fake = dict(config.EVOLUTION_CONFIG)
        fake["auto_apply_max_risk"] = 3
        with _cfg_switch("EVOLUTION_CONFIG", fake):
            self.assertEqual(_compute_risk_max(config, fake), 3)
            self.assertEqual(fake["auto_apply_max_risk"], 3)
        fake2 = dict(config.EVOLUTION_CONFIG)
        fake2["auto_apply_max_risk"] = 1
        with _cfg_switch("EVOLUTION_CONFIG", fake2):
            self.assertEqual(_compute_risk_max(config, fake2), 1)

    def test_06_no_other_hardcoded_risk_gate(self):
        """全库扫描（★C3 统一排除列表）：生产代码无其他 `risk <= N` 硬编码残留。"""
        pat = re.compile(r"risk\s*<=\s*\d")
        hits = []
        for path in exclude_dirs.iter_python_files(_ROOT):
            rel = os.path.relpath(path, _ROOT).replace("\\", "/")
            if rel.startswith(("tests/", "tmp/", ".bak")):
                continue
            try:
                txt = io.open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if pat.search(s):
                    hits.append("%s:%d: %s" % (rel, i, s[:80]))
        self.assertEqual(hits, [], "生产代码仍有 risk 门槛硬编码：\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
