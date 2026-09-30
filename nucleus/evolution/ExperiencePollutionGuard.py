# -*- coding: utf-8 -*-
"""
ExperiencePollutionGuard.py —— 经验污染防护

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 防止低质量经验污染经验池
机制: 函数式模块，包含6个工具函数
定位: 进化治理层
"""

import os
import shutil
from collections import Counter
from typing import Any

from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.logger import get_module_logger

_logger = get_module_logger("ExperiencePollutionGuard")

# 测试隔离
_ISO_BASE_DIR: str | None = None

# 权重（检索消费时按此降权）
WEIGHT_POLLUTED = 0.1
WEIGHT_SUSPECT = 0.5
WEIGHT_NORMAL = 1.0

# 污染判定参数
MIN_CONTENT_LEN = 2
LOW_CONFIDENCE = 0.3
POLLUTION_RATE_WARN = 0.05   # 污染率 >5% 告警

_TEST_TOKENS = ("test", "tmp", "mock", "demo", "模拟", "测试", "placeholder",
                "占位", "xxxx", "todo", "asdf", "123456")


def _text_of(entry: dict[str, Any]) -> str:
    """取条目的主体文本（兼容两种经验库的不同字段名）。"""
    for _k in ("summary", "example_question", "content", "question", "text"):
        _v = entry.get(_k)
        if isinstance(_v, str) and _v.strip():
            return _v.strip()
    return ""


def detect(entry: Any) -> tuple[str, str]:
    """检测单条经验，返回 (flag, reason)。

    flag: "polluted"（确定污染，权重0.1）/ "suspect"（可疑，权重0.5）/ "ok"
    """
    if not isinstance(entry, dict):
        return "polluted", "条目格式非法（非 dict）"

    _txt = _text_of(entry)

    # 1) 空内容 / 无意义
    if not _txt or len(_txt) < MIN_CONTENT_LEN:
        return "polluted", "内容为空或过短（无意义）"

    # 2) 测试残留
    _low = _txt.lower()
    for _t in _TEST_TOKENS:
        if _t in _low:
            return "polluted", f"含测试/占位残留关键词「{_t}」"

    # 3) 低置信度（reasoning_experience 有 confidence 字段）
    _conf = entry.get("confidence")
    if isinstance(_conf, (int, float)) and float(_conf) < LOW_CONFIDENCE:
        return "polluted", f"置信度 {_conf} < {LOW_CONFIDENCE}"

    # 4) 可疑：缺少时间戳等元信息（不完整，可能来源异常）
    if not entry.get("timestamp") and not entry.get("id"):
        return "suspect", "缺少时间戳与 id 元信息"

    return "ok", ""


def _project_root() -> str:
    # <root>/nucleus/evolution/ExperiencePollutionGuard.py → 上溯 3 层
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def scan_library(rel_path: str, mark: bool = True,
                 backup: bool = True) -> dict[str, Any]:
    """扫描单个经验库文件并标记污染。

    rel_path: 相对项目根的路径，如 "data/context/reasoning_experience.json"
    mark=True 时写回文件（只加标记字段，不删条目）。
    """
    _p = os.path.join(_project_root(), rel_path)
    _res: dict[str, Any] = {"path": rel_path, "exists": os.path.exists(_p)}
    if not os.path.exists(_p):
        return _res
    try:
        _d = safe_read_json(_p, default={})
    except (ValueError, OSError) as e:
        print(f"[WARNING] ExperiencePollutionGuard.py:106: {type(e).__name__}: {e}")
        _res["error"] = f"{type(e).__name__}: {e}"
        return _res

    _items = None
    if isinstance(_d, dict):
        for _k in ("experiences", "items", "nodes", "records", "data"):
            if isinstance(_d.get(_k), list):
                _items = _d[_k]
                break
    elif isinstance(_d, list):
        _items = _d
    if _items is None:
        _res["error"] = "未找到经验列表"
        return _res

    _flags = Counter()
    _reasons: Counter = Counter()
    _seen: dict[str, str] = {}   # 文本 → 首次出现的 key 指纹（查重复与矛盾）

    for _i, _it in enumerate(_items):
        if not isinstance(_it, dict):
            _flags["polluted"] += 1
            continue
        _flag, _reason = detect(_it)
        # 重复 / 矛盾检测（同文本二次出现 → 重复；同问题不同结论 → 可疑）
        _txt = _text_of(_it)
        if _txt:
            _finger = _txt[:120]
            if _finger in _seen:
                _prev_idx = _seen[_finger]
                _prev = _items[int(_prev_idx)] if str(_prev_idx).isdigit() else None
                if isinstance(_prev, dict) and _prev is not _it:
                    _same = (_prev.get("summary") == _it.get("summary")
                             or _prev.get("example_question") == _it.get("example_question"))
                    _flag = "polluted" if _same else "suspect"
                    _reason = "重复经验（内容完全相同）" if _same else "同问题存在不同结论（疑似矛盾）"
            else:
                _seen[_finger] = str(_i)
        _flags[_flag] += 1
        if _flag != "ok":
            _reasons[_reason] += 1
        if _flag == "ok":
            _it.pop("polluted", None)
            _it.pop("quality_flag", None)
            _it.pop("pollution_reason", None)
        else:
            _it["polluted"] = True
            _it["quality_flag"] = _flag
            _it["pollution_reason"] = _reason
            _it["quality_weight"] = WEIGHT_POLLUTED if _flag == "polluted" else WEIGHT_SUSPECT

    _total = len(_items)
    _bad = _flags["polluted"] + _flags["suspect"]
    _res.update({
        "total": _total,
        "polluted": _flags["polluted"],
        "suspect": _flags["suspect"],
        "clean": _flags["ok"],
        "pollution_rate": round(_bad / _total, 4) if _total else 0.0,
        "top_reasons": _reasons.most_common(6),
    })

    if mark:
        try:
            if backup and not os.path.exists(_p + ".bak_batch11cd"):
                shutil.copy2(_p, _p + ".bak_batch11cd")
            # 经安全写通道：路径锁 + 退避重试 + 唯一 tmp + 失败清理
            # backup=False 避免与既有 .bak_batch11cd 重复备份
            if safe_write_json(_p, _d, backup=False, indent=2):
                _res["written"] = True
            else:
                _res["written"] = False
                _res["write_error"] = "safe_write_json 返回 False"
        except Exception as e:
            _res["written"] = False
            _res["write_error"] = f"{type(e).__name__}: {e}"
            _logger.warning(f"经验库标记写回失败（需关注）: {type(e).__name__}: {e}")

    if _res.get("pollution_rate", 0) > POLLUTION_RATE_WARN:
        _logger.warning(
            f"[经验库污染] {rel_path} 污染率 {_res['pollution_rate']:.1%} "
            f"> {POLLUTION_RATE_WARN:.0%}（污染 {_flags['polluted']} / 可疑 {_flags['suspect']} / 共 {_total}）")
    else:
        _logger.info(
            f"[经验库污染] {rel_path} 扫描完成：共 {_total}，污染 {_flags['polluted']}，"
            f"可疑 {_flags['suspect']}，污染率 {_res.get('pollution_rate', 0):.1%}")
    return _res


def scan_all(rels: tuple[str, ...] = (
        "data/context/reasoning_experience.json",
        "data/experience/experience_pool.json"), mark: bool = True) -> list[dict[str, Any]]:
    """扫描全部经验库，返回各库统计。"""
    return [scan_library(r, mark=mark) for r in rels]


