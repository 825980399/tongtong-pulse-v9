# -*- coding: utf-8 -*-
"""B156-9 风格批 · 幻影技术债证伪门控测试。

任务书断言风格面仅「380/182 except_pass」一处真实面；「裸 except 35」「裸 noqa 21」
为幻影（已证伪为 0）。本测试以 `tools/scan_style_phantoms.py` 复扫项目源码
（排除 tmp / .aionclaw-tmp / .bak_batch* 等 scratch 目录），断言：

  - 裸 `except:`（catch-all 无类型）数量 = 0
  - 裸 `# noqa`（无错误码）数量 = 0

锁定「幻影已证伪」，防止后续误把 scratch 探针脚本计入项目技术债。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_scanner():
    _p = os.path.join(ROOT, "tools", "scan_style_phantoms.py")
    _spec = importlib.util.spec_from_file_location("b156_9_style_phantom", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


def test_no_bare_except_in_project_source():
    _res = _load_scanner().scan()
    assert _res["bare_except_count"] == 0, (
        f"项目源码仍存在裸 except:（应为 0，scratch 已排除）：{_res['bare_except']}"
    )


def test_no_bare_noqa_in_project_source():
    _res = _load_scanner().scan()
    assert _res["bare_noqa_count"] == 0, (
        f"项目源码仍存在裸 # noqa（应为 0）：{_res['bare_noqa']}"
    )
