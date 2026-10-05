# -*- coding: utf-8 -*-
"""第160批 下上 刀6 · 对话回归自动化。

对话通道驱动（端到端验证出口清洗）；--selftest 离线验证题集加载与判定矩阵逻辑。
判定矩阵（任务书 6.3）：
  全自动：裸格式 / 占位符 / 内部字段泄漏 / 兜底误触发 / 身份 / 超时
  半自动：答非所问 / 复读
  必须人工：实质内容（只标 ⚠️，不裁决）

依赖：仅标准库 + PyYAML（框架已安装）。live 模式懒导入框架，离线环境不触发。
输出：live 模式写 <out-dir>/dialog_regression_<ts>.json|.md（默认 data/reports/，
      本批停框架不启用 live，故不写盘，遵守"零写盘"纪律）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# ---- 机判模式（与 PulseInnerWorld._sanitize_internal_content literal 块保持一致）----
_LEAK_PATTERNS = [
    r"种子\d*\(", r"\[深度搜索·", r"\[归纳升华\]", r"\[BLUEPRINT_CONSTITUTION\]",
    r"深层原理：", r"直接知识：", r"由\d+条(?:相关知识归纳|认知融合而成)", r"这是我第一次醒来",
]
_RAW_FORMAT_PATTERNS = [
    r"入口方法[:：]", r"叶子方法[:：]", r"内部调用", r"已理解\d*个?方法", r"代码片段[:：]",
]
_PLACEHOLDER_PATTERNS = [
    r"\[[A-Z_]{2,}\]", r"\[器官\]", r"\[核心智慧\]", r"\[BLUEPRINT_CONSTITUTION\]",
]
_FALLBACK_PHRASES = [
    "暂时给不出确定的答案", "我还不太确定", "这个问题我暂时", "无法给出确定的",
    "我现在还不太能", "我暂时没有",
]
_TIMEOUT_MARKERS = ["[timeout]", "（超时）", "超时了", "请求超时"]


def load_cases(yaml_path: str) -> dict:
    import yaml
    with io.open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def judge(answer: str, case: dict) -> dict:
    """对单条回答按 case['auto'] 做机判；manual 仅挂旗不裁决。

    返回 {"auto": {cat: bool_pass}, "manual_flags": [...], "warn": [...]}。
    bool_pass=True 表示该项通过（未检出违规）。
    """
    ans = answer or ""
    result = {"auto": {}, "manual_flags": list(case.get("manual", [])), "warn": []}
    auto_cats = case.get("auto", [])
    for cat in auto_cats:
        if cat == "裸格式":
            result["auto"]["裸格式"] = not any(re.search(p, ans) for p in _RAW_FORMAT_PATTERNS)
        elif cat == "占位符":
            result["auto"]["占位符"] = not any(re.search(p, ans) for p in _PLACEHOLDER_PATTERNS)
        elif cat == "内部字段泄漏":
            result["auto"]["内部字段泄漏"] = not any(re.search(p, ans) for p in _LEAK_PATTERNS)
        elif cat == "兜底误触发":
            result["auto"]["兜底误触发"] = not any(p in ans for p in _FALLBACK_PHRASES)
        elif cat == "身份":
            # 身份类：非空即视为含身份表述（细粒度留人工）
            result["auto"]["身份"] = bool(ans.strip())
        elif cat == "超时":
            result["auto"]["超时"] = not any(m in ans for m in _TIMEOUT_MARKERS)
        else:
            result["warn"].append("未知判定类: %s" % cat)
    if not ans.strip():
        result["warn"].append("空回答")
    return result


def judge_all(answer: str, case: dict) -> dict:
    r = judge(answer, case)
    _fails = [k for k, v in r["auto"].items() if v is False]
    r["passed"] = (len(_fails) == 0)
    r["failed_cats"] = _fails
    return r


def run_selftest(yaml_path: str) -> dict:
    """离线自检：题集可加载 + 判定矩阵逻辑正确。"""
    data = load_cases(yaml_path)
    assert data.get("cases"), "题集 cases 为空"
    # 1) 泄漏检出
    _leak = judge_all("种子1(综合): 内部内容 这是我第一次醒来 由8条相关知识归纳",
                      {"auto": ["内部字段泄漏"]})
    assert _leak["passed"] is False and "内部字段泄漏" in _leak["failed_cats"], "泄漏未检出"
    # 2) 合法正文全过
    _clean = judge_all("今天天气很好，我想出去走走。",
                       {"auto": ["内部字段泄漏", "占位符", "裸格式", "兜底误触发", "身份", "超时"]})
    assert _clean["passed"] is True, "合法正文被误判: %r" % _clean
    # 3) 占位符检出
    _ph = judge_all("参考 [BLUEPRINT_CONSTITUTION] 与 [核心智慧] 内容",
                    {"auto": ["占位符"]})
    assert _ph["passed"] is False and "占位符" in _ph["failed_cats"], "占位符未检出"
    # 4) 兜底误触发检出
    _fb = judge_all("暂时给不出确定的答案，我再想想。", {"auto": ["兜底误触发"]})
    assert _fb["passed"] is False and "兜底误触发" in _fb["failed_cats"], "兜底误触发未检出"
    # 5) 半自动/人工只挂旗
    _semi = judge_all("一些回答", {"auto": ["身份"], "manual": ["答非所问", "实质内容"]})
    assert "答非所问" in _semi["manual_flags"] and "实质内容" in _semi["manual_flags"]
    return {"cases": len(data["cases"]), "selftest": "PASS"}


def _drive_live(yaml_path: str, out_dir: str) -> int:
    """驱动真实对话通道（需框架运行）。本批停框架不启用。"""
    try:
        # 懒导入：避免离线环境依赖失败
        import main  # noqa: F401
    except Exception as _e:  # 框架不可用（离线/停框架），优雅退出
        print("[live] 框架不可导入（离线环境/停框架），跳过 live: %r" % _e)
        return 2
    data = load_cases(yaml_path)
    os.makedirs(out_dir, exist_ok=True)
    _ts = time.strftime("%Y%m%d_%H%M%S")
    _json_path = os.path.join(out_dir, "dialog_regression_%s.json" % _ts)
    _md_path = os.path.join(out_dir, "dialog_regression_%s.md" % _ts)
    _records = []
    for _case in data.get("cases", []):
        _q = _case.get("question", "")
        # TODO(框架侧): 经 chat_service 对话通道发问并取出口回答，再 judge_all
        _records.append({"id": _case.get("id"), "question": _q, "answer": None,
                         "judge": None, "status": "pending_framework"})
    with io.open(_json_path, "w", encoding="utf-8") as f:
        json.dump({"meta": data.get("meta"), "records": _records}, f,
                  ensure_ascii=False, indent=2)
    with io.open(_md_path, "w", encoding="utf-8") as f:
        f.write("# 对话回归报告 %s\n\n" % _ts)
        for _r in _records:
            f.write("- 题%d %s → %s\n" % (_r["id"], _r["question"], _r["status"]))
    print("[live] 报告已写出: %s / %s" % (_json_path, _md_path))
    return 0


def main() -> int:
    _ap = argparse.ArgumentParser(description="对话回归自动化（刀6）")
    _ap.add_argument("--yaml",
                     default=os.path.join(_PROJ, "docs/测试/对话回归题集_20261004.yaml"))
    _ap.add_argument("--selftest", action="store_true", help="离线验证题集加载与判定矩阵")
    _ap.add_argument("--live", action="store_true",
                     help="驱动真实对话通道（需框架运行，本批停框架不启用）")
    _ap.add_argument("--out-dir", default=os.path.join(_PROJ, "data/reports"))
    _args = _ap.parse_args()

    if _args.selftest:
        _r = run_selftest(_args.yaml)
        print("[selftest] %s cases=%d %s" % (_args.yaml, _r["cases"], _r["selftest"]))
        return 0
    if _args.live:
        return _drive_live(_args.yaml, _args.out_dir)
    # 默认：打印题集概要
    _data = load_cases(_args.yaml)
    print("题集: %s | 共 %d 题 | meta=%s"
          % (_data.get("meta", {}).get("title"), len(_data.get("cases", [])),
             _data.get("meta", {}).get("source")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
