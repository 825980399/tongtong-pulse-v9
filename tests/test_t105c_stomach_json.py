# -*- coding: utf-8 -*-
"""★第105批 T-105c（P1）：胃 JSON 解析失败修复 —— 复原验收。

任务书 T-105c（P1）：
    * 旧序：策略4（部分解析）先于策略5（容错解析）运行，会把 587 字原文抽成
      35 字标点串（纯 :,[ ] 组合）入库 → _parsed 非 None → 策略5 永不被触发（被遮蔽）。
    * 改法：①策略5 与 4 互换顺序（容错解析优先）；②策略5 输出补「列表repr需json化」
      （list/dict 值统一 json.dumps 为合法 JSON）；③策略4 加值有效性闸
      ``if v and not set(v) <= set(':,[ ]')``（纯标点值不入库）；④消化完成日志改打
      cleaned_content 长度。

本文件固化四类证据（真实源码，不复刻逻辑）：
  A. 静态接线：_do_digest 内「策略5」块必须位于「策略4」块之前（防顺序回退）。
  B. 值有效性闸（★核心回归）：纯标点值被 _extract_code_analysis_partial 拒绝（返回 None）。
  C. 正常抽取不受闸影响（真实值仍被抽取）。
  D. 策略5 容错解析能力：能消化「数组里放键值对」的真错误（parse_json_fault_tolerant）。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.parsing.json_fault_tolerant import (  # noqa: E402
    parse_json_fault_tolerant as _ft_parse,
)
from organs.body.PulseStomach import PulseStomach  # noqa: E402

_STOMACH = os.path.join(ROOT, "organs", "body", "PulseStomach.py")


def _read_src():
    with io.open(_STOMACH, encoding="utf-8", errors="ignore") as f:
        return f.read().replace("\r\n", "\n")


class TestT105cStomachJson(unittest.TestCase):
    # ---------- A. 静态接线（策略5 先于 策略4） ----------
    def test_A_strategy5_block_before_strategy4(self):
        _src = _read_src()
        _i5 = _src.find("策略5 容错解析")
        _i4 = _src.find("策略4 部分解析兜底")
        self.assertGreater(_i5, 0, "★未找到策略5 标记")
        self.assertGreater(_i4, 0, "★未找到策略4 标记")
        self.assertLess(_i5, _i4,
                        "★策略5(容错解析)必须位于策略4(部分解析)之前（交换顺序防回退）")

    # ---------- B. 值有效性闸（核心回归） ----------
    def test_B_partial_rejects_pure_punctuation(self):
        # 纯标点值：策略4 不应入库（否则遮蔽策略5）
        _r = PulseStomach._extract_code_analysis_partial('功能: :,[ ]')
        self.assertIsNone(_r, "★纯标点值必须被值有效性闸拒绝（返回 None）")

        # 混合：真实字段保留，纯标点字段丢弃
        _mixed = '功能: "缓存用户会话数据", 关键步骤: :,[ ]'
        _out = PulseStomach._extract_code_analysis_partial(_mixed)
        self.assertIsNotNone(_out)
        self.assertEqual(_out.get("功能"), "缓存用户会话数据")
        self.assertNotIn("关键步骤", _out, "★纯标点字段必须被闸丢弃")

    # ---------- C. 正常抽取不受影响 ----------
    def test_C_partial_keeps_real_value(self):
        _out = PulseStomach._extract_code_analysis_partial(
            '功能: "加载并校验用户配置"')
        self.assertEqual(_out.get("功能"), "加载并校验用户配置")

    # ---------- D. 策略5 容错解析能力 ----------
    def test_D_fault_tolerant_parses_array_with_keyvalue(self):
        # 典型真错误：数组里放键值对
        _bad = ('{"功能": "加载并校验用户配置",'
                ' "关键步骤": ["读取文件", "校验格式"],'
                ' "依赖的外部数据": [ "_a": "一个用于缓存会话的模块",'
                ' "_b": "配置解析器" ]}')
        _ok, _data, _st = _ft_parse(_bad)
        self.assertTrue(_ok, "★策略5 必须能消化数组里放键值对的真错误")
        self.assertIsInstance(_data, dict)
        self.assertIn("功能", _data)
        self.assertIn("依赖的外部数据", _data)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
