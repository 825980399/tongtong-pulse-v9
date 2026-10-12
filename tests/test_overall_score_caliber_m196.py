# -*- coding: utf-8 -*-
"""196刀3 overall_score 口径统一三层案：文档存在 + 三层结构断言（隔离）。"""
import os

_CALIBER_PATH = os.path.join("docs", "分析报告", "overall_score口径统一三层案_20261012.md")


def test_caliber_doc_exists():
    assert os.path.isfile(_CALIBER_PATH), "口径声明文档缺失: %s" % _CALIBER_PATH


def test_caliber_three_layers():
    _txt = open(_CALIBER_PATH, encoding="utf-8").read()
    assert "分数口径" in _txt, "缺少①分数口径"
    assert "分级带口径" in _txt, "缺少②分级带口径"
    assert "断代标注" in _txt, "缺少③断代标注"
    # v2 为主 + engine 降级维度级诊断
    assert "v2" in _txt and "维度级诊断" in _txt
