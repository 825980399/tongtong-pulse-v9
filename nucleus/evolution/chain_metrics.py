"""进化全链六段指标化（352 刀7）。

发现 → 生成 → 审批 → 应用 → 验证 → 回灌。

设计约束（为什么不是一个大函数算到底）：
  每一段的**分母必须与被测对象同域**（铁律160）。“发现 677 条、生成 14 条”不能
  直接相除得出“生成率 2%”——那两个数可能根本不是同一批条目。所以每段都自带
  `numerator / denominator / source / note`，分母不明就写 `None`，**不拿 None 当 0** 也不自动补一个看起来
  合理的分母。

★读不到的一律如实报 `state`，不用“0”冒充“没有”（铁律156）。

★本模块**不自己挂心跳**：352 是代码提交批（不重启），新增一个未经重启验证的
写盘钩子比不加更坏。调用方（心跳/周报任务）在重启窗接线。

均为只读。唯一写盘函数 `write_chain_metrics` 路径必须显式传入（铁律158）。
"""

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ChainMetrics")

#: 六段名（顺序即链路顺序，不要乱排——排序会让读的人以为上下游关系变了）
STAGE_DISCOVER = "discover"
STAGE_GENERATE = "generate"
STAGE_APPROVE = "approve"
STAGE_APPLY = "apply"
STAGE_VERIFY = "verify"
STAGE_FEEDBACK = "feedback"
STAGES = (STAGE_DISCOVER, STAGE_GENERATE, STAGE_APPROVE,
          STAGE_APPLY, STAGE_VERIFY, STAGE_FEEDBACK)

#: 段名中文（报告与日志用；代码里一律用英文常量）
STAGE_ZH = {STAGE_DISCOVER: "发现", STAGE_GENERATE: "生成", STAGE_APPROVE: "审批",
            STAGE_APPLY: "应用", STAGE_VERIFY: "验证", STAGE_FEEDBACK: "回灌"}

CHAIN_SCHEMA = "evolution_chain_metrics/352.v1"
WEEKLY_TAG = "[352周报]"

#: 生成器草稿池（与 patch_generator.DRAFTS_REL 同一份，不另开一个源）
DRAFTS_REL = os.path.join("data", "patches", "generator_drafts.json")
PENDING_REL = os.path.join("data", "patches", "pending_patches.json")
HISTORY_REL = os.path.join("data", "patches", "patch_history.json")
EFFECT_VERIFY_REL = os.path.join("data", "evolution", "effect_verify.json")
CHAIN_OUT_REL = os.path.join("data", "evolution", "chain_metrics.json")


def _root_of(root: str | None) -> str:
    """项目根。★不用“固定上溯 N 级”（铁律187），按标记文件有界上溯。"""
    if root:
        return str(root)
    _d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isfile(os.path.join(_d, "config.py")) and os.path.isdir(
                os.path.join(_d, "nucleus")):
            return _d
        _p = os.path.dirname(_d)
        if _p == _d:
            break
        _d = _p
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_json(path: str) -> dict:
    """读 JSON，返回 `{state, data, path}`；state 不假装 ok。"""
    if not os.path.isfile(path):
        return {"state": "absent", "data": None, "path": path}
    try:
        with open(path, encoding="utf-8") as _f:
            _d = json.load(_f)
    except Exception as _e:                                     # noqa: BLE001
        silent_exc(_e, where="chain_metrics::_load_json", level="debug")
        return {"state": "unreadable", "data": None, "path": path}
    return {"state": "ok", "data": _d, "path": path}


def _rows_of(d: Any) -> list:
    """从一份 JSON 里取出行列表（兼容 list 与 {rows:[...]}）。"""
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        for _k in ("rows", "patches", "pending", "entries", "items"):
            _v = d.get(_k)
            if isinstance(_v, list):
                return [x for x in _v if isinstance(x, dict)]
    return []


def _stage(name: str, num, den, source: str, note: str = "") -> dict:
    """组一段。`rate` 只在两个数都真存在且分母>0 时算，否则 None。"""
    _rate = None
    if isinstance(num, int) and isinstance(den, int) and den > 0:
        _rate = round(num / den, 4)
    return {"stage": name, "stage_zh": STAGE_ZH[name],
            "numerator": num, "denominator": den, "rate": _rate,
            "source": source, "note": note}


def compute_chain_metrics(root: str | None = None, *,
                          now: float | None = None) -> dict:
    """算六段转化率。只读。返回 `{schema, root, computed_at, stages, raw, notes}`。

    每段口径（写在 `note` 里，让读数的人不用猜）：
      发现 —— 池条目数（无上游，分母 None）
      生成 —— 草稿池行数 / 池里 open 条目数（可生成面）
      审批 —— 草稿里 requires_confirmation=False 的数 / 草稿总数
      应用 —— history 里 applied=True 的数 / history 总数
      验证 —— verdicts 里 passed 非 None 的数 / verdicts 总数
      回灌 —— verdicts 里真实文件条目数 / verdicts 总数
    """
    _r = _root_of(root)
    _now = float(now if now is not None else time.time())
    _out: dict = {"schema": CHAIN_SCHEMA, "root": _r, "computed_at": _now,
                  "stages": [], "raw": {}, "notes": []}

    # ── 发现：池条目 ──
    _pool_rows: list = []
    _pool_state = "unknown"
    try:
        from nucleus import audit_problem_pool as _ap
        _p = _ap.load_pool(_r)
        _pool_state = _p.get("state")
        if _pool_state == "ok":
            _pool_rows = list(_p.get("entries") or [])
    except Exception as _e:                                     # noqa: BLE001
        silent_exc(_e, where="chain_metrics::pool", level="debug")
        _pool_state = "import_failed"
    _open_n = sum(1 for x in _pool_rows if str(x.get("status") or "open") == "open")
    _out["raw"]["pool"] = {"state": _pool_state, "rows": len(_pool_rows),
                           "open": _open_n}
    _out["stages"].append(_stage(
        STAGE_DISCOVER, len(_pool_rows) if _pool_state == "ok" else None, None,
        "audit_problem_pool.load_pool",
        "池条目总数（无上游分母）；open=%d" % _open_n))
    if _pool_state != "ok":
        _out["notes"].append("发现段池不可用（state=%s）" % _pool_state)

    # ── 生成：草稿 / 可生成面 ──
    _dr = _load_json(os.path.join(_r, DRAFTS_REL))
    _draft_rows = _rows_of(_dr["data"]) if _dr["state"] == "ok" else []
    _out["raw"]["drafts"] = {"state": _dr["state"], "rows": len(_draft_rows)}
    _out["stages"].append(_stage(
        STAGE_GENERATE, len(_draft_rows) if _dr["state"] == "ok" else None,
        _open_n if _pool_state == "ok" else None,
        "generator_drafts.json / 池 open",
        "草稿池行数 / 池里 open 条目数；不是“生成器合格率”"))
    if _dr["state"] != "ok":
        _out["notes"].append("生成段草稿池不可用（state=%s）" % _dr["state"])

    # ── 审批：免人工 / 草稿总数 ──
    _auto_n = sum(1 for x in _draft_rows if x.get("requires_confirmation") is False)
    _out["raw"]["approval"] = {"drafts": len(_draft_rows), "auto_pass": _auto_n}
    _out["stages"].append(_stage(
        STAGE_APPROVE, _auto_n if _dr["state"] == "ok" else None,
        len(_draft_rows) if _dr["state"] == "ok" else None,
        "generator_drafts.json[requires_confirmation=False]",
        "免人工审核占比（刀4 分档后才有意义；本批前恒 True⇒恒 0）"))

    # ── 应用：history 里 applied ──
    _hi = _load_json(os.path.join(_r, HISTORY_REL))
    _hist_rows = _rows_of(_hi["data"]) if _hi["state"] == "ok" else []
    _applied_n = sum(1 for x in _hist_rows if x.get("applied") is True)
    _out["raw"]["apply"] = {"state": _hi["state"], "rows": len(_hist_rows),
                            "applied": _applied_n}
    _out["stages"].append(_stage(
        STAGE_APPLY, _applied_n if _hi["state"] == "ok" else None,
        len(_hist_rows) if _hi["state"] == "ok" else None,
        "patch_history.json[applied is True]",
        "历史已应用 / 历史总数（分母是历史面，不是池面）"))

    # ── 验证 / 回灌：verdicts ──
    _ev = _load_json(os.path.join(_r, EFFECT_VERIFY_REL))
    _verdicts: dict = {}
    if _ev["state"] == "ok" and isinstance(_ev["data"], dict):
        _v = _ev["data"].get("verdicts")
        if isinstance(_v, dict):
            _verdicts = _v
    _v_rows = [x for x in _verdicts.values() if isinstance(x, dict)]
    _decided = sum(1 for x in _v_rows if x.get("passed") is not None)
    _out["raw"]["verify"] = {"state": _ev["state"], "verdicts": len(_v_rows),
                             "decided": _decided}
    _out["stages"].append(_stage(
        STAGE_VERIFY, _decided if _ev["state"] == "ok" else None,
        len(_v_rows) if _ev["state"] == "ok" else None,
        "effect_verify.json[verdicts.passed is not None]",
        "已判定 / 全部验证记录（None=三项未测，不能当“未通过”）"))

    _real_v = 0
    for x in _v_rows:
        _f = str(x.get("file") or "").replace("\\", "/").strip().lstrip("./")
        if _f and "/" in _f and not _f.startswith(("tmp/", "tests/")):
            _real_v += 1
    _out["raw"]["feedback"] = {"verdicts": len(_v_rows), "real_file": _real_v}
    _out["stages"].append(_stage(
        STAGE_FEEDBACK, _real_v if _ev["state"] == "ok" else None,
        len(_v_rows) if _ev["state"] == "ok" else None,
        "effect_verify.json[真实文件条目]",
        "可回灌样本 / 验证记录（注 5 只用真实文件件）"))

    _out["notes"].append("分母不同域的段**不可相乘**：每段转化率只对自己的分母负责")
    return _out


def weekly_report_line(m: dict | None = None, *, root: str | None = None) -> str:
    """一行可 grep 的周报。不传 `m` 就现算。

    格式固定以 `[352周报]` 开头，后面全是 `key=value`——便于日志被抽取成表。
    缺数一律写 `NA`，**不写 0**（写 0 会被当成“真的是零”）。
    """
    _m = m if isinstance(m, dict) else compute_chain_metrics(root)
    _by = {s["stage"]: s for s in _m.get("stages") or []}

    def _kv(k: str) -> str:
        # 只出「值(分子/分母)」，标签由外层给——免得拼出「生成=generate=NA」这种双标签
        _s = _by.get(k) or {}
        if _s.get("rate") is None:
            return "NA(%s/%s)" % (_s.get("numerator"), _s.get("denominator"))
        return "%.2f%%(%s/%s)" % (_s["rate"] * 100.0,
                                  _s.get("numerator"), _s.get("denominator"))

    _raw = _m.get("raw") or {}
    _pool = _raw.get("pool") or {}
    return "%s 池规模=%s open=%s 生成=%s 审批免人工=%s " \
           "应用=%s 验证已判=%s 回灌可用=%s" % (
               WEEKLY_TAG,
               _pool.get("rows") if _pool.get("state") == "ok" else "NA",
               _pool.get("open") if _pool.get("state") == "ok" else "NA",
               _kv(STAGE_GENERATE), _kv(STAGE_APPROVE), _kv(STAGE_APPLY),
               _kv(STAGE_VERIFY), _kv(STAGE_FEEDBACK))


def write_chain_metrics(path: str, m: dict, *, now: float | None = None) -> dict:
    """原子写入。★`path` **必须显式传入**（铁律158）；不推导生产路径。

    走「先算 data → .tmp → os.replace → 读回校验」（铁律182）。
    """
    _out = {"written": False, "path": path, "why": ""}
    if not str(path or "").strip():
        _out["why"] = "empty_path"
        return _out
    _payload = dict(m or {})
    _payload["written_at"] = float(now if now is not None else time.time())
    _text = json.dumps(_payload, ensure_ascii=False, indent=1)
    try:
        _d = os.path.dirname(path)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _f.write(_text)
        os.replace(_tmp, path)
        with open(path, encoding="utf-8") as _f:
            _back = json.load(_f)
        _out["written"] = isinstance(_back, dict) and _back.get("schema") == CHAIN_SCHEMA
        _out["why"] = "saved" if _out["written"] else "readback_mismatch"
    except Exception as _e:                                     # noqa: BLE001
        silent_exc(_e, where="chain_metrics::write_chain_metrics", level="debug")
        _out["why"] = "%s: %s" % (type(_e).__name__, str(_e)[:200])
    if _out["written"]:
        _module_logger.info("%s", weekly_report_line(_payload))
    return _out
