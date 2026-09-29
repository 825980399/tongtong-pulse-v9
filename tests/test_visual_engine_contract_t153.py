"""T153-6 视觉引擎契约（P1 层参数化文件）

覆盖 4 个视觉引擎：ocr_engine / pdf_engine（函数式 process 接口）+ haar_engine /
mediapipe_engine（HaarEngine / MediaPipeEngine 类 detect 接口）。

⚠️ 任务书 T153-6④ 原文称"4个视觉引擎共用 process(file_path) 五断言"，但实测接口分化：
- ocr_engine / pdf_engine 是模块级 process(file_path, ...) 函数（OCR / PDF 文字提取）
- haar_engine / mediapipe_engine 是 detect(frame) 类引擎（人脸检测），无 process 属性
本文件按真实接口分两类契约，不强行统一。此偏差如实记录，交星轨知悉。

纪律：每器官<=1文件；本文件为 P1 视觉引擎共用契约文件（任务书允许）。
"""
import importlib
import os
import tempfile

import numpy as np
import pytest

FUNC_ENGINES = [
    ("ocr_engine", "ocr_engine"),
    ("pdf_engine", "pdf_engine"),
]
CLASS_ENGINES = [
    ("haar_engine", "HaarEngine"),
    ("mediapipe_engine", "MediaPipeEngine"),
]


@pytest.mark.parametrize("modname,src", FUNC_ENGINES)
def test_func_engine_process_contract(modname, src):
    m = importlib.import_module(f"organs.senses.visual_engines.{modname}")
    # A1 不存在文件调用不抛，返回结构化 dict
    r = m.process("__nonexistent_t153_xyz.tmp")
    assert isinstance(r, dict), "process 必须返回 dict"
    # A2 约定键集稳定
    for k in ("text", "confidence", "source", "error"):
        assert k in r, f"process 返回缺约定键 {k}"
    # A3 source 标识正确
    assert r["source"] == src
    # A4 文件不存在：error 非 None、text 为空串
    assert r["error"] is not None
    assert r["text"] == ""
    # A5 真实极小文件：结构化返回不抛（依赖缺失时走 error 分支而非抛）
    fd, p = tempfile.mkstemp(suffix=".tmp")
    os.close(fd)
    try:
        r2 = m.process(p)
        assert isinstance(r2, dict) and "error" in r2
    finally:
        os.remove(p)


@pytest.mark.parametrize("modname,cls", CLASS_ENGINES)
def test_class_engine_detect_contract(modname, cls):
    m = pytest.importorskip(f"organs.senses.visual_engines.{modname}")
    C = getattr(m, cls)
    inst = C()  # 实例化不抛
    assert isinstance(inst.is_available(), bool)
    fake = np.zeros((20, 20, 3), dtype=np.uint8)
    for frame in (None, fake):
        r = inst.detect(frame)  # 无效/假帧均不抛
        assert isinstance(r, tuple) and len(r) == 2
        assert isinstance(r[0], bool) and isinstance(r[1], (int, float))
