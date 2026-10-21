# -*- coding: utf-8 -*-
"""★317批刀4（长期13 · 世界模型最小版）+ ★318批刀5（扩源）：expectation_ledger。

「预期 → 实测 → 差」台账，每行一条断言：

    {assertion_id, statement, source,
     predicted{field, expect},
     measured{field, actual, observed_at, obs_source},
     verdict ∈ {met, violated, unverifiable},
     gap_reason, criteria_version, graph_file}

**红线（任务书 §六）**：
  * `violated` **不得影响入队 / 动机 / 分诊**——本模块只写台账，
    不 import 任何入队、规划、动机接口（可 grep 自证）；
  * 不做物理世界建模；**不新建调度器**——由 04:00
    `nucleus/evolution/night_orchestration.py::generate_night_digest` 复用调用。

★318批刀5 扩源（周产能 ≥112 条，断言源 ≥7 类）：

| 源 | 目标条数 | obs_source |
|---|---|---|
| A1 类簇（零入边方法占比） | 40 | call_graph_cluster_a1 |
| A4 覆盖（图 vs 源文件 def） | 16 | call_graph_ast_manual |
| 说明书入口（器官入口存在性） | 16+1 | call_graph_handbook_rule |
| health_snapshot 字段 | 10 | health_snapshot |
| alerts 分布 | 8 | alerts.jsonl |
| pulse_pairs（近 7 天 ×2） | 14 | pulse_pairs_<day>.jsonl |
| 调用图 stats | 8 | call_graph_graph_stats |

`gap_reason` 把 unverifiable 的成因分开（**"取不到实测" ≠ "世界与预期不符"**）：
`field_not_exposed` / `no_measurement_source` / `predicate_not_computable`。

`assertion_id` 可加圈次前缀 `loop.<N>.<id>`（压测每圈外部裁判一一对应）。
"""
from __future__ import annotations

import ast
import json
import logging
import os
import time

_logger = logging.getLogger(__name__)
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ExpectationLedger")

#: 与 CallGraphAnalyzer 的 A4 口径保持同一版本号（337 批起：文件口径，不含 _inner_world_math.py）
#: ★337刀6/7 同批升版：317.a4.v1（cls 单名过滤）→ 337.a4.v2（按 files_included 过滤）
#:   **跨版本禁止比较**（A-14）：存量 ledger 行的 measured.actual 是旧口径值。
_CRITERIA_VERSION = "337.a4.v2"
#: A4 说明书侧基线（5 文件 def 总数，317 批实测）
_A4_MANUAL_BASELINE = 455

_LEDGER_REL = os.path.join("data", "world_model", "expectation_ledger.jsonl")

#: 各类断言的目标条数（周产能预算，可审计）
_SOURCE_BUDGET = {
    "call_graph_cluster_a1": 40,
    "call_graph_ast_manual": 16,
    "call_graph_handbook_rule": 17,
    "health_snapshot": 10,
    "alerts": 8,
    "pulse_pairs": 14,
    "call_graph_graph_stats": 8,
}


def ledger_path(project_root: str | None = None) -> str:
    """台账文件绝对路径。

    ★320批刀3（验证产物隔离硬规·代码侧支撑）：环境变量 ``TT_LEDGER_PATH``
    优先——施工/压测的**验证运行**一律指向隔离根（``tmp/p3xx_root/...``），
    不得写生产 ``data/``（烛微320§0-2 已数到第四例）。
    """
    _env = os.environ.get("TT_LEDGER_PATH")
    if _env:
        return _env
    return os.path.join(project_root or os.getcwd(), _LEDGER_REL)


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._read_json")
        return default


def _read_jsonl(path: str) -> list[dict]:
    _rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for _line in f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError as _e:
                    silent_exc(_e, where="world_model.expectation_ledger._read_jsonl")
                    continue
                if isinstance(_d, dict):
                    _rows.append(_d)
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._read_jsonl")
    return _rows


def _latest_graph(project_root: str) -> tuple[str, dict]:
    """取最近一次调用图快照（文件名按时间戳倒序）。"""
    _dir = os.path.join(project_root, "data", "call_graph")
    _cands = []
    try:
        _cands = [f for f in os.listdir(_dir)
                  if f.startswith("graph_") and f.endswith(".json")]
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._latest_graph")
        return "", {}
    if not _cands:
        return "", {}
    _cands.sort(reverse=True)
    _p = os.path.join(_dir, _cands[0])
    return os.path.join("data", "call_graph", _cands[0]), (_read_json(_p, {}) or {})


# ★320批刀1：gap_reason 枚举（**violated 也必须带**——烛微320§0-3 指出 25 条
# violated 里 24 条 gap_reason 为空，导致无法区分"世界不符"与"口径差"）。
#   field_not_exposed / no_measurement_source / predicate_not_computable /
#   criteria_gap（口径差，**不计入裁判分母**）/ real_violation（真违反，计入分母）
_GAP_REASONS = frozenset({
    "field_not_exposed",
    "no_measurement_source",
    "predicate_not_computable",
    "criteria_gap",
    "real_violation",
})

#: 这些 assertion_id 前缀属「说明书口径 vs 图口径」的结构性差值，不是世界不符
#: 注意含 `a4.`（`a4.coverage.<Cls>` 是实测 id 形态）与 `a4_`（旧写法）两种
_CRITERIA_GAP_PREFIXES = ("mastery.", "handbook.", "a4.", "a4_", "manual.")

#: ★321批刀3：断言命名**可识别前缀**（与 320批刀1 的 `_infer_gap_reason` 同源）。
#: 前缀不是风格问题——它是**判据的输入**：缺前缀的断言会被归成
#: real_violation，直接压进裁判分母、把通过率压低。新写断言必须命中其一，
#: 或在 `_mk(...)` 里**显式带 gap_reason**。
_ASSERT_ID_PREFIXES = ("mastery.", "a4.", "a4_", "handbook.", "manual.", "loop.")
#: 已告警过的断言 id（同一条只告一次，避刷屏）
_PREFIX_MISS_SEEN: set = set()


def _m321_check_prefix(assertion_id: str, gap_reason: str = "") -> bool:
    """★321批刀3：断言命名前缀校验（**只告警，不改 verdict / gap_reason**）。

    为什么要管这个：320批刀1 的 `_infer_gap_reason()` **按前缀推断**口径差——
    `mastery./a4./handbook./manual.` 归 criteria_gap（不进裁判分母），其余归
    real_violation（进分母、扣分）。所以命名前缀直接决定裁判口径。
    本函数不推断、不抦圆：只对“对不上前缀且没现成 gap_reason”的断言告一次警。
    """
    _aid = str(assertion_id or "")
    _ok = any(_aid.startswith(_p) for _p in _ASSERT_ID_PREFIXES) or bool(gap_reason)
    if not _ok and _aid not in _PREFIX_MISS_SEEN:
        _PREFIX_MISS_SEEN.add(_aid)
        _logger.warning(
            "[321刀3][assert_prefix] 断言缺可识别前缀: %r"
            "（新断言须以 mastery./a4./handbook./manual./loop. 开头，"
            "或显式带 gap_reason；否则会被判成 real_violation）", _aid)
    return _ok


def prefix_hit_rate(rows: list[dict]) -> dict:
    """★321批刀3：当前断言集的前缀命中率（只读统计，供验收与回归）。"""
    _total = _hit = 0
    _miss: list = []
    for _r in (rows or []):
        if not isinstance(_r, dict):
            continue
        _aid = str(_r.get("assertion_id") or "")
        if not _aid:
            continue
        _total += 1
        if any(_aid.startswith(_p) for _p in _ASSERT_ID_PREFIXES) or _r.get("gap_reason"):
            _hit += 1
        elif len(_miss) < 20:
            _miss.append(_aid)
    return {"total": _total, "hit": _hit, "miss": len(_miss),
            "rate": round(_hit / _total, 4) if _total else None,
            "miss_samples": _miss}


def _row_source(row: dict) -> str:
    """★320批刀0：取一条记录的**来源进程**（新字段优先，旧数据回退）。

    旧台账（320 前）没有 `source_process`；其 `source` 是"断言来源模块"（不是进程），
    不能拿来当进程标记 ⇒ 一律按 `live`（历史数据视为活体时期产生）处理。
    """
    _sp = row.get("source_process")
    if _sp:
        return _norm_source(_sp)
    _s = str(row.get("source") or "")
    return _norm_source(_s) if _s in ("live", "verification") else "live"


def _infer_gap_reason(assertion_id: str, verdict: str, gap_reason: str) -> str:
    """★320批刀1：gap_reason 归一——violated 必带；口径差单列 criteria_gap。"""
    if gap_reason:
        return gap_reason if gap_reason in _GAP_REASONS else "predicate_not_computable"
    if verdict == "unverifiable":
        return "no_measurement_source"
    if verdict == "violated":
        _aid = str(assertion_id or "")
        if any(_aid.startswith(_p) for _p in _CRITERIA_GAP_PREFIXES):
            return "criteria_gap"
        return "real_violation"
    return ""


# ===== ★324批刀3：gap_reason 显式标注（裁判口径 · 防 8/25 误判）=====
# 烛微324§0-5 实测：120 条里仅 1 条 gap_reason 非空，其余全靠 `_infer_gap_reason()`
# 按前缀猜；8/25 条 violated **没有 criteria_gap 前缀** ⇒ 被记成 real_violation。
# 本批口径：**显式 gap_reason 为唯一首选**，缺则 WARNING 留痕一次（每个 assertion_id 一次）；
# 推断只作兜底，且 `gap_reason_origin` 分列 explicit / inferred / none，判罚一律不改。
_GAP_EXPLICIT_SEEN: set = set()
_GAP_MISS_TOTAL = {"miss": 0}


def _m324_warn_missing_gap(assertion_id: str, verdict: str, inferred: str) -> bool:
    """缺显式 gap_reason ⇒ 每个 assertion_id 只告警一次（防刷屏）。返回是否首次。"""
    _aid = str(assertion_id or "-")
    if _aid in _GAP_EXPLICIT_SEEN:
        return False
    _GAP_EXPLICIT_SEEN.add(_aid)
    try:
        _logger.warning(
            "[324刀3] 断言未显式给 gap_reason ⇒ 按兜底推断落账: id=%s verdict=%s "
            "inferred=%s（只留痕，不改判罚）", _aid, verdict, inferred or "-")
    except Exception as e:
        silent_exc(e, where="expectation_ledger._m324_warn_missing_gap")
    return True


def gap_reason_origin_stats(rows: list | None = None) -> dict:
    """★324批刀3：显式 / 兜底推断 分列统计（喂验收与报表，不改任何既有读数）。

    历史行（324 前）没有 `gap_reason_origin` ⇒ 记 `legacy`，并按 gap_reason 是否为空
    再分 `legacy_explicit` / `legacy_inferred`，避免把旧账混进新口径。
    """
    _c = {"explicit": 0, "inferred": 0, "none": 0, "legacy_explicit": 0,
          "legacy_inferred": 0, "unknown": 0}
    _reasons: dict = {}
    for _r in (rows or []):
        if not isinstance(_r, dict):
            continue
        _o = str(_r.get("gap_reason_origin") or "")
        _g = str(_r.get("gap_reason") or "")
        if not _o:
            _o = "legacy_explicit" if _g else "legacy_inferred"
        if _o not in _c:
            _o = "unknown"
        _c[_o] += 1
        if _g:
            _reasons[_g] = int(_reasons.get(_g, 0)) + 1
    _new = _c["explicit"] + _c["inferred"] + _c["none"]
    return {
        "counts": _c,
        "new_rows": _new,
        "explicit_rate_on_new": (round(_c["explicit"] / float(_new), 4) if _new else None),
        "gap_reason_dist": dict(sorted(_reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "warn_missing_total": int(_GAP_MISS_TOTAL.get("miss", 0)),
        "note": "显式=调用方传入；inferred=按前缀兜底推断；none=不可推断（unverifiable 以外）",
    }


def audit_violated_gap_ownership(rows: list | None = None,
                                 sample: int = 30) -> dict:
    """★324批刀3：8/25 风险面复核——**violated 且靠推断记 real_violation** 的条目点名。

    只读：不写盘、不改台账。返回 `risky` 计数与抽样 id，供人工复核归属
    （真违规 / 口径差 / 断言写错）。
    """
    _risky: list = []
    _stat = {"violated_total": 0, "violated_explicit": 0, "violated_inferred": 0,
             "violated_real_inferred": 0, "violated_criteria_gap_inferred": 0,
             "violated_gap_empty": 0}
    for _r in (rows or []):
        if not isinstance(_r, dict) or str(_r.get("verdict") or "") != "violated":
            continue
        _stat["violated_total"] += 1
        _o = str(_r.get("gap_reason_origin") or "")
        _g = str(_r.get("gap_reason") or "")
        if _o == "explicit":
            _stat["violated_explicit"] += 1
            continue
        _stat["violated_inferred"] += 1
        if not _g:
            # ★324批刀3：**gap_reason 为空**的 violated 才是 8/25 风险面本体——
            #   既无显式口径也无前缀可推 ⇒ "真违规"与"口径差"在数据上不可区分。
            #   旧实现只看 real_violation 会把这一类漏掉（实测 25/25 全落这里）。
            _stat["violated_gap_empty"] += 1
            if len(_risky) < max(0, int(sample)):
                _risky.append({"assertion_id": str(_r.get("assertion_id") or ""),
                               "gap_reason": "", "origin": _o or "legacy",
                               "why": "gap_reason_empty：真违规/口径差不可区分",
                               "criteria_version": str(_r.get("criteria_version") or "")})
        elif _g == "real_violation":
            _stat["violated_real_inferred"] += 1
            if len(_risky) < max(0, int(sample)):
                _risky.append({"assertion_id": str(_r.get("assertion_id") or ""),
                               "gap_reason": _g, "origin": _o or "legacy",
                               "criteria_version": str(_r.get("criteria_version") or "")})
        elif _g == "criteria_gap":
            _stat["violated_criteria_gap_inferred"] += 1
    _risky_n = _stat["violated_real_inferred"] + _stat["violated_gap_empty"]
    return {
        "stat": _stat,
        "risky_sample": _risky,
        "risky_count": _risky_n,
        "verdict": ("需要人工复核归属" if _risky_n else "无风险面"),
        "rule": "violated 且 origin!=explicit 且 gap_reason==real_violation ⇒ 记可疑，不直接判真违规",
    }


# ===== ★331批刀3（小林拍板①）：判定线改「violated 100% 带归属」=====
# 为什么换线：烛微331§1 报「120 行 gap_reason 非空仅 27（22.5%），与 90% 验收线不符」。
#   实测根因是**口径错配**，不是留痕缺失：120 行里 **93 行是 `met`（达成）**——
#   达成本就没有 gap 要解释，把它算进分母，非空率怎么算都是 22.5%。
#   正确口径只看 violated：实测 **25/25 = 100%** 已带归属
#   （criteria_gap 17 / real_violation 8），unverifiable 2/2 同样 100%。
# ⇒ 90% 线作废（它量的是错误分母），判定线改为「violated 100% 带归属」。
_M331_VIOLATED_OWNERSHIP_RULE = (
    "violated 100% 带归属（90% 全量线作废：其分母含 93 行 met，量的是错误口径）")
_M331_VIOLATED_OWNERSHIP_TARGET = 1.0


def violated_gap_ownership_check(rows: list | None = None,
                                 sample: int = 30) -> dict:
    """★331批刀3：判定线「violated 100% 带归属」——只读校验，不写盘、不改台账。

    口径写死（可复算）：
      - 分母 = `verdict == "violated"` 的行数（**不含 met**：达成没有 gap 要解释）
      - 分子 = 其中 `gap_reason` 非空的行数
      - 通过线 = 覆盖率 == 1.0（**不是 90%**）

    另记 `explicit_ratio`（显式占比）作**质量参考，不进通过线**：
    "推断来的归属"也是归属（能区分真违规与口径差），但它不如显式的可信，
    故分列展示而不混进判定 —— 混进去会把"能归属"与"归属得好"两件事搅在一起。

    边界：一条 violated 都没有 ⇒ `coverage=None`、`verdict=UNAVAILABLE`
    （**不填 0**：0 会被读成"全部没带归属"）。
    """
    _rows = rows if rows is not None else _read_jsonl(ledger_path())
    _total = 0
    _owned = 0
    _explicit = 0
    _missing: list = []
    for _r in (_rows or []):
        if not isinstance(_r, dict) or str(_r.get("verdict") or "") != "violated":
            continue
        _total += 1
        _g = str(_r.get("gap_reason") or "").strip()
        if _g:
            _owned += 1
            if str(_r.get("gap_reason_origin") or "").endswith("explicit"):
                _explicit += 1
        elif len(_missing) < max(0, int(sample)):
            _missing.append({"assertion_id": str(_r.get("assertion_id") or ""),
                             "criteria_version": str(_r.get("criteria_version") or "")})
    _cov = (_owned / _total) if _total else None
    return {
        "rule": _M331_VIOLATED_OWNERSHIP_RULE,
        "target": _M331_VIOLATED_OWNERSHIP_TARGET,
        "violated_total": _total,
        "violated_owned": _owned,
        "violated_missing": _total - _owned,
        "coverage": None if _cov is None else round(_cov, 4),
        "explicit_count": _explicit,
        "explicit_ratio": None if not _total else round(_explicit / _total, 4),
        "missing_sample": _missing,
        "verdict": ("UNAVAILABLE" if _cov is None
                    else ("PASS" if _cov >= _M331_VIOLATED_OWNERSHIP_TARGET else "FAIL")),
    }


# ===== ★325批刀3：25 条 violated 复核清单（只出清单 + 建议，**不代判**）=====
# 324刀3 已把"violated 且 gap_reason 为空"点成风险面（25/25）；本批要能逐条复核。
# 分类规则逐条可核（不做黑箱打分）：
#   R1 gap_reason 显式且 == criteria_gap            ⇒ 疑似口径差
#   R2 gap_reason 显式且 ∈ {real_violation, ...}    ⇒ 可判真违规候选
#   R3 前缀 a4./a1.cluster./mastery. 且 gap 空      ⇒ 疑似口径差（附具体理由）
#   R4 前缀 alerts. 且 gap 空                       ⇒ 可判真违规候选（事实层）
#   R5 其余                                         ⇒ 无法判
_O3_ATTR_REAL = "可判真违规候选"
_O3_ATTR_GAP = "疑似口径差"
_O3_ATTR_UNKNOWN = "无法判"
# ★326批刀3（小林裁决②）：`a1.` **暂不入** `_CRITERIA_GAP_PREFIXES`——
#   入列会改动裁判分母（当前裁判比 0.9208），而 a1.cluster 的阈值 0.5 从未标定，
#   属于"口径未定"而不是"已确认口径差"。所以给它**第三档**：`口径差_待标定`。
#
# ★两件事必须分清（本批实测订正，别混成一句"不参与任何计数"）：
#   ① **归因/复核清单计数**：这 7 条**不计入**"可判真违规候选"、也不计入
#      "疑似口径差"——`counts` 里单列 `口径差_待标定`，条目标 `counted=False`；
#   ② **裁判比分母**：325刀6 回填已把这 7 条的 `gap_reason` 推断成 `real_violation`
#      （legacy_inferred）。裁决②要求裁判比**稳定在 0.9208**，所以本批
#      **不改 gap_reason**（改了分母就动）⇒ 它们在裁判比里**仍然算 violated**。
#      这不是"漏改"，是"口径稳定优先"的显式取舍，故每条都带
#      `judge_denominator_effect` 字段，谁读谁看得见。
#   待掌握度判据修订时统一标定（单独立项，本批不做）。
_O3_ATTR_UNCALIBRATED = "口径差_待标定"
_A1_UNCALIBRATED_PREFIX = "a1.cluster."
_A1_CRITERIA_VERSION = "326.a1.uncalibrated"
_A1_DECISION = ("小林裁决②：a1. 暂不入 _CRITERIA_GAP_PREFIXES；"
                "a1.cluster.* 挂「口径差_待标定」，不进裁判分母、不参与任何计数")

#: 前缀 ⇒ (归属, 依据)。**依据必须写清"为什么这个前缀算口径差"**，否则等于没说。
_M325_PREFIX_BASIS = {
    "a4.": (_O3_ATTR_GAP,
            "A4 口径 317.a4.v1：图节点数 vs 说明书方法数本身存在已知映射差"
            "（322 实测 +math 7 个模块级 def 需排除；本批 116 条差额同源）"),
    "a1.cluster.": (_O3_ATTR_GAP,
                    "类簇零入边率阈值 0.5 为设定值、从未标定；"
                    "ratio<0.95 时属阈值口径问题（ratio≥0.95 另标真违规候选=整簇孤立/图残缺）"),
    "mastery.": (_O3_ATTR_GAP,
                 "mastery 生产者缺失（323/324 V3 实测 no_producer）："
                 "断言值本身取不到，violated 反映的是管道未接通，不是行为违规"),
    "alerts.": (_O3_ATTR_REAL,
                "alerts 是事实层（有 ERROR 记录即事实），不依赖口径推断"),
    "a1.": (_O3_ATTR_GAP, "A1 图覆盖率类断言，阈值未标定"),
    "loop.": (_O3_ATTR_UNKNOWN, "压测圈次包装断言，需按内层断言回落判定"),
}


def _m325_prefix_of(assertion_id: str) -> str:
    """取断言 id 的可识别前缀（两段优先，如 a1.cluster.），否则一段。"""
    _a = str(assertion_id or "")
    if not _a:
        return ""
    if _a.startswith("a1.cluster."):
        return "a1.cluster."
    for _p in _ASSERT_ID_PREFIXES + ("a1.", "alerts.", "loop."):
        if _a.startswith(_p):
            return _p
    return _a.split(".")[0] + "." if "." in _a else ""


def build_violated_review_list(rows=None, project_root=None) -> dict:
    """★325批刀3：给出 violated 逐条复核清单（含归属**建议**与依据），只读不写盘。

    "不代判"的落地方式：输出里主字段叫 `attribution_suggestion`，
    并在 `verdict_owner` 里写明"真违规计数由星轨+小林核后定"。
    """
    _rows = rows
    if _rows is None:
        _rows = _read_jsonl(ledger_path(project_root))
    _out = []
    _cnt = {_O3_ATTR_REAL: 0, _O3_ATTR_GAP: 0, _O3_ATTR_UNKNOWN: 0,
            # ★326批刀3：第三档单独计数（**不进裁判分母**，也不与上面三档相加成"违规数"）
            _O3_ATTR_UNCALIBRATED: 0}
    for _r in (_rows or []):
        if not isinstance(_r, dict) or str(_r.get("verdict") or "") != "violated":
            continue
        _aid = str(_r.get("assertion_id") or "")
        _pfx = _m325_prefix_of(_aid)
        _g = str(_r.get("gap_reason") or "")
        _o = str(_r.get("gap_reason_origin") or "")
        _meas = (_r.get("measured") or {}) if isinstance(_r.get("measured"), dict) else {}
        _actual = _meas.get("actual")
        if _g == "criteria_gap":
            _attr, _basis, _rule = _O3_ATTR_GAP, "显式 gap_reason=criteria_gap", "R1"
        elif _pfx == _A1_UNCALIBRATED_PREFIX:
            # ★326批刀3：**先于**通用前缀规则命中（R1/R2 之前）——a1.cluster 一律
            #   走"待标定"，不再落 `可判真违规候选`/`疑似口径差`。
            #   注意：**不看 gap_reason**——325 回填已把它推断成 real_violation，
            #   若按 gap 分流就永远走不到这一档（实测第一版正是 0 条命中）。
            _attr = _O3_ATTR_UNCALIBRATED
            _basis = ("阈值 0.5 为设定值、从未标定（小林裁决②：a1. 暂不入口径差前缀）；"
                      "归因计数不含本条，待掌握度判据修订时统一标定")
            if _g:
                _basis += "｜当前 gap_reason=%s(%s) 系 325 回填推断，未改（裁判比稳定优先）" % (
                    _g, _o or "legacy")
            _rule = "R3u"
        elif _g:
            _attr, _basis, _rule = _O3_ATTR_REAL, "显式 gap_reason=%s" % _g, "R2"
        elif _pfx in _M325_PREFIX_BASIS:
            _attr, _basis = _M325_PREFIX_BASIS[_pfx]
            _rule = "R4" if _pfx == "alerts." else "R3"
            if _pfx == "a1.cluster." and isinstance(_actual, (int, float)) and _actual >= 0.95:
                _basis += "；本行 actual=%s ≥0.95 ⇒ 另可判真违规候选（整簇孤立）" % _actual
        else:
            _attr, _basis, _rule = _O3_ATTR_UNKNOWN, "前缀无规则、gap_reason 亦缺失", "R5"
        _cnt[_attr] = _cnt.get(_attr, 0) + 1
        _out.append({
            "assertion_id": _aid,
            "prefix": _pfx,
            "gap_reason_now": _g,
            "gap_reason_origin_now": _o or "legacy(缺失)",
            "criteria_gap_prefix_inferable": _pfx in _CRITERIA_GAP_PREFIXES,
            "statement": str(_r.get("statement") or "")[:160],
            "expect": str((_r.get("predicted") or {}).get("expect") or ""),
            "actual": _actual,
            "attribution_suggestion": _attr,
            "basis": _basis,
            "rule": _rule,
            # ★326批刀3：待标定条目标 counted=False（机器读这个，不读自然语言）
            "counted": (_attr != _O3_ATTR_UNCALIBRATED),
            "uncalibrated": (_attr == _O3_ATTR_UNCALIBRATED),
            # 待标定条目在**裁判比**里的真实地位（不改 gap_reason ⇒ 仍进分母）
            "judge_denominator_effect": (
                ("仍计入裁判比分母（gap_reason=%s 未改；裁决①要求裁判比稳定 0.9208）"
                 % (_g or "空")) if _attr == _O3_ATTR_UNCALIBRATED else "按原 gap_reason 计"),
        })
    return {"total": len(_out), "counts": _cnt, "items": _out,
            # ★326批刀3：裁定留痕（复算裁判比时先看这两行）
            "criteria_version": _A1_CRITERIA_VERSION,
            "a1_decision": _A1_DECISION,
            "a1_threshold_uncalibrated": int(_cnt.get(_O3_ATTR_UNCALIBRATED, 0)),
            "verdict_owner": "星轨+小林（本清单只给建议，真违规计数不由本模块定）",
            "rule_doc": "R1/R2/R3/R4/R5 见 nucleus/world_model/expectation_ledger.py 325刀3 注释"}


# ===== ★325批刀6：legacy 存量 gap_reason_origin 回填 =====
# 存量 120 行全部缺 `gap_reason_origin`（324刀3 的显式标注只对新写入生效）。
# 回填规则：**只补标记与可推的 gap_reason，绝不改 verdict / 不删行**。
_GAP_BACKFILL_VERSION = "325.1"


def backfill_legacy_origin(path=None, dry_run: bool = True,
                           project_root=None) -> dict:
    """给存量行补 `gap_reason_origin`（legacy_explicit / legacy_inferred / legacy_none）。

    Args:
        path: 台账路径；缺省取 ledger_path(project_root)。
        dry_run: True 只统计不写盘（默认，安全）。

    返回：`origin_added` / `gap_filled` / `verdict_changed`（**必须为 0**）/ `rows`。
    非 dry_run 时先备份到 `<path>.bak_325` 再原子写。
    """
    _p = path or ledger_path(project_root)
    _rows = _read_jsonl(_p)
    _stat = {"rows": len(_rows), "origin_added": 0, "gap_filled": 0,
             "legacy_explicit": 0, "legacy_inferred": 0, "legacy_none": 0,
             "verdict_changed": 0, "dry_run": bool(dry_run), "path": _p,
             "version": _GAP_BACKFILL_VERSION}
    _now = round(time.time(), 3)
    _out = []
    for _r in _rows:
        if not isinstance(_r, dict):
            continue
        _row = dict(_r)
        _v_before = str(_row.get("verdict") or "")
        if not str(_row.get("gap_reason_origin") or ""):
            _g = str(_row.get("gap_reason") or "")
            if _g:
                _row["gap_reason_origin"] = "legacy_explicit"
                _stat["legacy_explicit"] += 1
            else:
                _inferred = _infer_gap_reason(_row.get("assertion_id"),
                                              _v_before, "")
                if _inferred:
                    _row["gap_reason"] = _inferred
                    _row["gap_reason_origin"] = "legacy_inferred"
                    _stat["legacy_inferred"] += 1
                    _stat["gap_filled"] += 1
                else:
                    _row["gap_reason_origin"] = "legacy_none"
                    _stat["legacy_none"] += 1
            _row["legacy_backfill_version"] = _GAP_BACKFILL_VERSION
            _row["legacy_backfill_at"] = _now
            _stat["origin_added"] += 1
        if str(_row.get("verdict") or "") != _v_before:
            _stat["verdict_changed"] += 1     # 必须恒为 0
        _out.append(_row)
    if not dry_run and _stat["origin_added"]:
        import shutil as _sh
        _sh.copy2(_p, _p + ".bak_325")
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            for _row in _out:
                _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        os.replace(_tmp, _p)
        _stat["written"] = True
    else:
        _stat["written"] = False
    _stat["verdict_safe"] = (_stat["verdict_changed"] == 0)
    return _stat


def _norm_source(source: str | None) -> str:
    """★320批刀0/刀3：来源标记归一（``live`` 活体 / ``verification`` 验证运行）。"""
    _s = str(source or "live").strip().lower()
    return "verification" if _s.startswith("verif") else "live"


def _mk(assertion_id: str, statement: str, source: str, pred_field: str, expect: str,
        meas_field: str, actual: Any, verdict: str, obs_source: str,
        gap_reason: str = "", graph_file: str = "",
        criteria_version: str = _CRITERIA_VERSION,
        observed_at: float | None = None) -> dict:
    """构造一条 ledger 记录（统一 schema）。

    ★331批刀3（小林拍板①）：本函数**不做 gap_reason 强校验**——
    判定线已改为「violated 100% 带归属」（见 `violated_gap_ownership_check`），
    由 `_infer_gap_reason()` 兜底保证 violated 必带归属；在构造入口强校验会
    逼着改存量写法，收益不抵风险。要验覆盖请用上面的只读校验函数。
    """
    # ★321批刀3：前缀校验留痕（缺前缀⇒ WARNING；**不改 verdict / gap_reason**）
    _m321_check_prefix(assertion_id, gap_reason)
    # ★324批刀3：显式 gap_reason 优先；缺则兜底推断 + 单独计数（判罚一律不改）
    _g_origin = "explicit" if str(gap_reason or "").strip() else "none"
    _g_final = _infer_gap_reason(assertion_id, verdict, gap_reason)
    if _g_origin == "none":
        _GAP_MISS_TOTAL["miss"] = int(_GAP_MISS_TOTAL.get("miss", 0)) + 1
        _g_origin = "inferred" if _g_final else "none"
        _m324_warn_missing_gap(assertion_id, verdict, _g_final)
    return {
        "assertion_id": assertion_id,
        "statement": statement,
        "source": source,
        "predicted": {"field": pred_field, "expect": expect},
        "measured": {"field": meas_field, "actual": actual,
                     "observed_at": round(float(observed_at or time.time()), 3),
                     "obs_source": obs_source},
        "verdict": verdict,
        # ★320批刀1：原实现 `gap_reason if verdict == "unverifiable" else ""` 把
        # violated 的 gap_reason **直接清空**（烛微320§0-3 的 24/25 空值即此因）。
        "gap_reason": _g_final,
        # ★324批刀3：口径来源分列（explicit / inferred / none）——报表与验收可直接读
        "gap_reason_origin": _g_origin,
        "criteria_version": criteria_version,
        "graph_file": graph_file,
    }


def _count_defs_in_file(path: str) -> int | None:
    """AST 统计单文件 def 数（含方法）；不可读返回 None。"""
    try:
        with open(path, encoding="utf-8") as _f:
            _tree = ast.parse(_f.read())
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._count_defs_in_file")
        return None
    return sum(1 for _n in ast.walk(_tree)
               if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _clusters(graph: dict) -> dict[str, list[dict]]:
    """按 cls 分组节点。"""
    _out: dict[str, list[dict]] = {}
    for _n in (graph.get("nodes") or []):
        if not isinstance(_n, dict):
            continue
        _c = str(_n.get("cls") or "")
        if _c:
            _out.setdefault(_c, []).append(_n)
    return _out


def collect_assertions(project_root: str | None = None,
                       source: str = "live") -> list[dict]:
    """按 ≥7 类现成实测源产出断言（预算 112 条/周；三态 verdict 均可出现）。"""
    _root = project_root or os.getcwd()
    _now = time.time()
    _graph_rel, _graph = _latest_graph(_root)
    _gstats = (_graph or {}).get("stats") or {}
    _nodes = (_graph or {}).get("nodes") or []
    _edges = (_graph or {}).get("edges") or []
    _out: list[dict] = []

    # ---------- 源1：A1 类簇（零入边方法占比）40 条 ----------
    _in_deg: dict[str, int] = {}
    for _e in _edges:
        if isinstance(_e, dict) and _e.get("callee"):
            _in_deg[_e["callee"]] = _in_deg.get(_e["callee"], 0) + int(_e.get("count", 1) or 1)
    _cl = _clusters(_graph)
    _top = sorted(_cl.items(), key=lambda kv: -len(kv[1]))
    for _cname, _ns in _top[:_SOURCE_BUDGET["call_graph_cluster_a1"]]:
        _ids = [str(_n.get("id")) for _n in _ns]
        _zero = sum(1 for _i in _ids if _in_deg.get(_i, 0) == 0)
        _ratio = round(_zero / float(len(_ids)), 4) if _ids else None
        _out.append(_mk(
            "a1.cluster.%s.zero_in_degree_ratio" % _cname,
            "类簇 %s 的零入边方法占比应 ≤50%%（类内自洽可接受，整簇孤立则图残缺）" % _cname,
            "CallGraphAnalyzer.cluster_view", "zero_in_degree_ratio", "<=0.5",
            "zero_in_degree_ratio", _ratio,
            "met" if (_ratio is not None and _ratio <= 0.5) else "violated",
            "call_graph_cluster_a1",
            "no_measurement_source" if _ratio is None else "",
            _graph_rel, observed_at=_now))

    # ---------- 源2：A4 覆盖（图类簇 vs 源文件 def）16 条 ----------
    for _cname, _ns in _top[:_SOURCE_BUDGET["call_graph_ast_manual"]]:
        _files = {str(_n.get("file") or "") for _n in _ns if _n.get("file")}
        _manual = 0
        _missing = True
        for _f in _files:
            _abs = os.path.join(_root, str(_f).replace("\\", "/"))
            _c = _count_defs_in_file(_abs)
            if _c is None:
                continue
            _missing = False
            _manual += _c
        if _missing:
            _out.append(_mk(
                "a4.coverage.%s" % _cname,
                "A4 覆盖：类 %s 的图节点数应等于其源文件 def 总数" % _cname,
                "CallGraphAnalyzer.mastery_assertion_check", "a4_gap", "==0",
                "a4_gap", None, "unverifiable", "call_graph_ast_manual",
                "no_measurement_source", _graph_rel, observed_at=_now))
        else:
            _gap = abs(_manual - len(_ns))
            _out.append(_mk(
                "a4.coverage.%s" % _cname,
                "A4 覆盖：类 %s 的图节点数应等于其源文件 def 总数（口径 317.a4.v1）" % _cname,
                "CallGraphAnalyzer.mastery_assertion_check", "a4_gap", "==0",
                "a4_gap", _gap, "met" if _gap == 0 else "violated",
                "call_graph_ast_manual", "", _graph_rel, observed_at=_now))

    # ---------- 源3：说明书入口 16+1 条 ----------
    _organ_cls = [_c for _c, _ns in _top
                  if _c.startswith("Pulse")][:_SOURCE_BUDGET["call_graph_handbook_rule"] - 1]
    for _cname in _organ_cls:
        _ns = _cl.get(_cname) or []
        _ids = [str(_n.get("id")) for _n in _ns]
        _with_in = sum(1 for _i in _ids if _in_deg.get(_i, 0) > 0)
        _out.append(_mk(
            "handbook.entry.%s" % _cname,
            "说明书入口判据：类 %s 应至少有 1 个有入边的方法（可作为入口）" % _cname,
            "PulseCodeLearner._generate_organ_handbook", "entry_with_in_degree", ">=1",
            "entry_with_in_degree", _with_in, "met" if _with_in >= 1 else "violated",
            "call_graph_handbook_rule", "", _graph_rel, observed_at=_now))
    # 说明书入口判据全局断言（318刀6 复核项：旧启发式文案是否已消失）
    _pcl_txt = ""
    try:
        with open(os.path.join(_root, "organs", "brain", "PulseCodeLearner.py"),
                  encoding="utf-8") as _f:
            _pcl_txt = _f.read()
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.handbook_rule")
    if not _pcl_txt:
        _out.append(_mk("handbook.entry_rule.graph_judged",
                        "说明书入口判据应统一为图判定（旧启发式文案已消失）",
                        "PulseCodeLearner", "old_heuristic_present", "==False",
                        "old_heuristic_present", None, "unverifiable",
                        "call_graph_handbook_rule", "no_measurement_source",
                        _graph_rel, observed_at=_now))
    else:
        _old = '_entry_str = "、".join(_entry_methods[:3]) if _entry_methods else "脉冲响应入口"'
        _bad = _old in _pcl_txt
        _out.append(_mk("handbook.entry_rule.graph_judged",
                        "说明书入口判据应统一为图判定（旧启发式文案已消失）",
                        "PulseCodeLearner", "old_heuristic_present", "==False",
                        "old_heuristic_present", _bad,
                        "violated" if _bad else "met",
                        "call_graph_handbook_rule", "", _graph_rel, observed_at=_now))

    # ---------- 源4：health_snapshot 10 条 ----------
    _snap = _read_json(os.path.join(_root, "data", "monitor", "health_snapshot.json"), None)
    for _fname in ["beat_count", "nodes", "pulse", "info_field", "memory_pressure",
                   "knowledge_evolution", "organs", "heartbeat", "dream",
                   "reflection"][:_SOURCE_BUDGET["health_snapshot"]]:
        if not isinstance(_snap, dict) or _fname not in _snap:
            _out.append(_mk("health.%s.present" % _fname,
                            "health_snapshot 应暴露字段 %s" % _fname,
                            "health_snapshot", _fname, "not None", _fname, None,
                            "unverifiable", "health_snapshot",
                            "field_not_exposed" if isinstance(_snap, dict)
                            else "no_measurement_source", _graph_rel, observed_at=_now))
        else:
            _v = _snap.get(_fname)
            _ok = _v not in (None, "", [], {})
            _out.append(_mk("health.%s.present" % _fname,
                            "health_snapshot 字段 %s 应有非空读数" % _fname,
                            "health_snapshot", _fname, "not empty", _fname,
                            (str(_v)[:80] if _v is not None else None),
                            "met" if _ok else "violated", "health_snapshot", "",
                            _graph_rel, observed_at=_now))

    # ★318批刀5：一条**真实取不到**的断言（health_snapshot 不暴露检索 P95 延迟）——
    #   它存在的意义是让「三态」不缺 unverifiable：没有它，"全 met" 会掩盖"测不到"这一类。
    _p95 = _snap.get("search_latency_p95_ms") if isinstance(_snap, dict) else None
    _out.append(_mk("health.search_latency.p95",
                    "检索 P95 延迟应 ≤200ms", "health_snapshot",
                    "search_latency_p95_ms", "<=200", "search_latency_p95_ms", _p95,
                    ("met" if (_p95 is not None and _p95 <= 200)
                     else ("violated" if _p95 is not None else "unverifiable")),
                    "health_snapshot",
                    "" if _p95 is not None else "field_not_exposed",
                    _graph_rel, observed_at=_now))

    # ---------- 源5：alerts 8 条 ----------
    _alerts = _read_jsonl(os.path.join(_root, "data", "reports", "alerts.jsonl"))
    _sev: dict[str, int] = {}
    _needs_human = 0
    for _r in _alerts:
        _s = str(_r.get("severity") or _r.get("level") or "").upper()
        if _s:
            _sev[_s] = _sev.get(_s, 0) + 1
        if _r.get("needs_human"):
            _needs_human += 1
    for _k in ["P0", "P1", "ERROR", "CRITICAL", "WARNING"]:
        _n = _sev.get(_k, 0)
        _out.append(_mk("alerts.severity.%s.count" % _k,
                        "告警严重度 %s 计数应可统计（CRITICAL 应 =0）" % _k,
                        "alerts", "%s_count" % _k,
                        "==0" if _k == "CRITICAL" else ">=0", "%s_count" % _k, _n,
                        (("met" if _n == 0 else "violated") if _k == "CRITICAL" else "met"),
                        "alerts.jsonl", "", _graph_rel, observed_at=_now))
    _out.append(_mk("alerts.total.count", "告警总量应有记录（>0）", "alerts",
                    "total", ">0", "total", len(_alerts),
                    "met" if _alerts else "violated", "alerts.jsonl", "",
                    _graph_rel, observed_at=_now))
    _out.append(_mk("alerts.needs_human.count", "需人工介入条目应可统计", "alerts",
                    "needs_human", ">=0", "needs_human", _needs_human, "met",
                    "alerts.jsonl", "", _graph_rel, observed_at=_now))
    _bad_n = _sev.get("ERROR", 0) + _sev.get("CRITICAL", 0)
    _ratio_a = round(_bad_n / float(len(_alerts)), 4) if _alerts else None
    _out.append(_mk("alerts.error_ratio.window",
                    "ERROR+CRITICAL 占全部告警比例应 ≤5%", "alerts",
                    "error_ratio", "<=0.05", "error_ratio", _ratio_a,
                    ("met" if (_ratio_a is not None and _ratio_a <= 0.05)
                     else ("violated" if _ratio_a is not None else "unverifiable")),
                    "alerts.jsonl", "" if _ratio_a is not None else "no_measurement_source",
                    _graph_rel, observed_at=_now))

    # ---------- 源6：pulse_pairs 近 7 天 ×2 = 14 条 ----------
    for _d in range(7):
        _day = time.strftime("%Y%m%d", time.localtime(_now - _d * 86400.0))
        _pp = os.path.join(_root, "data", "monitor", "pulse_pairs_%s.jsonl" % _day)
        _exists = os.path.exists(_pp)
        _rows = _read_jsonl(_pp) if _exists else []
        _out.append(_mk("pulse_pairs.%s.present" % _day,
                        "当日脉冲对文件应存在", "pulse_pairs", "file_exists", "==True",
                        "file_exists", _exists, "met" if _exists else "violated",
                        "pulse_pairs_%s.jsonl" % _day, "", _graph_rel, observed_at=_now))
        _out.append(_mk("pulse_pairs.%s.rows" % _day,
                        "当日脉冲对应有落盘记录（≥1 条）", "pulse_pairs", "rows", ">=1",
                        "rows", len(_rows) if _exists else None,
                        ("met" if _rows else ("violated" if _exists else "unverifiable")),
                        "pulse_pairs_%s.jsonl" % _day,
                        "" if _exists else "no_measurement_source", _graph_rel,
                        observed_at=_now))

    # ---------- 源7：调用图 stats 8 条 ----------
    _pe = _gstats.get("parse_errors")
    _out.append(_mk("graph.parse_errors.zero", "调用图解析错误应为 0（图可信才能当判据）",
                    "call_graph.stats", "parse_errors", "==0", "parse_errors", _pe,
                    "met" if _pe == 0 else ("violated" if _pe is not None else "unverifiable"),
                    "call_graph_graph_stats",
                    "no_measurement_source" if _pe is None else "", _graph_rel,
                    observed_at=_now))
    _out.append(_mk("graph.nodes.nonzero", "调用图节点数应 >0", "call_graph.stats",
                    "nodes", ">0", "nodes", len(_nodes),
                    "met" if _nodes else "violated", "call_graph_graph_stats", "",
                    _graph_rel, observed_at=_now))
    _out.append(_mk("graph.edges.nonzero", "调用图边数应 >0", "call_graph.stats",
                    "edges", ">0", "edges", len(_edges),
                    "met" if _edges else "violated", "call_graph_graph_stats", "",
                    _graph_rel, observed_at=_now))
    _tc = _gstats.get("total_calls")
    _out.append(_mk("graph.total_calls.nonzero", "调用图总调用数应 >0", "call_graph.stats",
                    "total_calls", ">0", "total_calls", _tc,
                    "met" if (isinstance(_tc, int) and _tc > 0) else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    _ur = _gstats.get("unresolved_calls")
    if isinstance(_ur, int) and isinstance(_tc, int) and _tc:
        _ur_r = round(_ur / float(_tc), 4)
        _out.append(_mk("graph.unresolved_ratio.window",
                        "未解析调用占总调用比例应 <60%（R1 攻坚：先降到 50% 以下）",
                        "call_graph.stats", "unresolved_ratio", "<0.6",
                        "unresolved_ratio", _ur_r,
                        "met" if _ur_r < 0.6 else "violated",
                        "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    else:
        _out.append(_mk("graph.unresolved_ratio.window",
                        "未解析调用占总调用比例应 <60%", "call_graph.stats",
                        "unresolved_ratio", "<0.6", "unresolved_ratio", None,
                        "unverifiable", "call_graph_graph_stats",
                        "no_measurement_source", _graph_rel, observed_at=_now))
    _ex = _gstats.get("external_calls")
    _out.append(_mk("graph.external_calls.present", "外部调用计数应可统计（≥0）",
                    "call_graph.stats", "external_calls", ">=0", "external_calls", _ex,
                    "met" if isinstance(_ex, int) else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    _cf = _gstats.get("cross_file_calls")
    _out.append(_mk("graph.cross_file.present", "跨文件调用计数应可统计（≥0）",
                    "call_graph.stats", "cross_file_calls", ">=0",
                    "cross_file_calls", _cf,
                    "met" if _cf is not None else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    _out.append(_mk("graph.file.present", "应能定位到本轮使用的图文件（同图同版本可比）",
                    "call_graph.stats", "graph_file", "not empty", "graph_file",
                    _graph_rel or None, "met" if _graph_rel else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    # ★320批刀0/刀3：**每条**打来源标记（一次循环，不改 25 处 _mk 调用）。
    #   live=活体自然产出 ｜ verification=施工/压测验证运行（可一键剔除，见
    #   tools/stress/purge_verification_data.py）。
    # ★注意：`source` 字段是 _mk 的第 3 个位置参数，语义是「**断言来源模块**」
    #   （如 `CallGraphAnalyzer.mastery_assertion_check`）——**不得覆盖**。
    #   来源进程标记独立用 `source_process`（施工自查发现的字段语义冲突，已修正）。
    _src_tag = _norm_source(source)
    for _r in _out:
        _r["source_process"] = _src_tag
    return _out



def sc3_consistency(project_root: str | None = None,
                    class_names: list | None = None) -> dict:
    """★319批刀1（SC-3 一致性自检）：断言（二阶）与 ledger（世界模型）**对表**。

    同一条断言在 `mastery_assertion_check` 与 `expectation_ledger.verdict` 两处结论
    是否一致——不一致即"尺子坏"，**优先于任何结论**（验收线：不一致率 ≤5%）。
    比对项：A4 覆盖的 `gap`（断言侧） vs ledger 的 `measured.actual` ＋ `verdict`。

    红线：只产计数与结论，不因不一致去改任一侧（不自动"修"数据）。
    """
    _root = project_root or os.getcwd()
    try:
        from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.sc3_consistency.import")
        return {"error": type(_e).__name__, "total": 0, "mismatch": 0}
    _graph_rel, _graph = _latest_graph(_root)
    _a = CallGraphAnalyzer()
    _a._graph_data = _graph
    _idx: dict[str, dict] = {}
    for _r in _read_jsonl(ledger_path(_root)):
        _aid = str(_r.get("assertion_id") or "")
        _idx[_aid] = _r
        if _aid.startswith("loop."):
            _idx[_aid.split(".", 2)[-1]] = _r
    _names = class_names or [c for c, _ns in
                             sorted(_clusters(_graph).items(), key=lambda kv: -len(kv[1]))[:10]]
    _total = 0
    _mismatch = 0
    # ★337批刀7：跨版本跳过计数（**不静默**：跳过了几条必须报出来）
    _legacy = 0
    _details: list[dict] = []
    _cur_a4v = ""
    try:
        _cur_a4v = str(getattr(CallGraphAnalyzer, "_A4_CRITERIA_VERSION", "") or "")
    except Exception as _ve:
        silent_exc(_ve, where="expectation_ledger.sc3_consistency.a4_version")
    for _c in _names:
        # ★修正：断言侧必须拿到「说明书侧方法数」才有 gap——否则 a4_coverage 恒为
        #   unverifiable，对表会**空转（total=0）**，看起来"一致"其实是没比。
        _ns = _clusters(_graph).get(_c) or []
        _files = {str(_n.get("file") or "") for _n in _ns if _n.get("file")}
        _manual = 0
        _missing = True
        for _f in _files:
            _cnt = _count_defs_in_file(os.path.join(_root, str(_f).replace("\\", "/")))
            if _cnt is None:
                continue
            _missing = False
            _manual += _cnt
        _res = _a.mastery_assertion_check(
            _c, manual_method_count=(None if _missing else _manual))
        _gap = (_res.get("a4_coverage") or {}).get("gap")
        if _gap is None:
            continue
        _lr = _idx.get("a4.coverage.%s" % _c)
        if _lr is None:
            _total += 1
            _mismatch += 1
            _details.append({"class": _c, "reason": "ledger 无对应断言"})
            continue
        # ★337批刀7（A-14）：ledger 行口径与当前 A4 版本不一致 ⇒ **禁止比较**。
        #   不比而不是"比了算 mismatch"：口径改版本就是"尺子换了"，
        #   拿旧尺子的读数去判新尺子坏，是判据常识错误。
        _lvert = str(_lr.get("criteria_version") or "")
        if _cur_a4v and _lvert and _lvert != _cur_a4v:
            _legacy += 1
            _details.append({"class": _c, "reason": "跨版本跳过（禁止比较）",
                             "ledger_criteria": _lvert,
                             "current_criteria": _cur_a4v})
            continue
        # ★337刀7：**分母只计真正比过的项**（铁律160：分母与被测对象同域）。
        #   跨版本跳过的不进 total——否则 mismatch=0 时会算出 rate=0=PASS，
        #   把"没比"读成"一致"。
        _total += 1
        _lactual = (_lr.get("measured") or {}).get("actual")
        _lverdict = _lr.get("verdict")
        _exp = "met" if _gap == 0 else "violated"
        if _lverdict != _exp or _lactual != _gap:
            _mismatch += 1
            _details.append({"class": _c, "assert_gap": _gap,
                             "ledger_actual": _lactual, "ledger_verdict": _lverdict,
                             "expected_verdict": _exp})
    # ★337批刀7：**空转 ≠ 通过** —— total==0 记 UNAVAILABLE，绝不记 rate=0.0
    #   （原实现 `rate=_mismatch/_total if _total else 0.0`，空转时 rate=0
    #    会被下游读成"不一致率 0% = 尺子没坏"，这正是 322 遗留至今的假绿。）
    _pg7 = ""
    try:
        from nucleus.proc_gen import proc_gen_str as _pgs
        _pg7 = _pgs()
    except Exception as _pe:
        silent_exc(_pe, where="expectation_ledger.sc3_consistency.proc_gen")
    if _total <= 0:
        _module_logger.warning(
            "[337刀7][SC-3] total=0 ⇒ **UNAVAILABLE**（空转不得记 rate=0=通过）"
            " legacy_skipped=%d graph=%s proc_gen=%s",
            _legacy, _graph_rel, _pg7)
        return {"total": 0, "mismatch": 0, "rate": None, "status": "UNAVAILABLE",
                "threshold": 0.05, "ruler_broken": None,
                "legacy_skipped": _legacy,
                "reason": "无可对表项（跨版本跳过 %d 条／断言侧拿不到 gap）⇒ 未实测"
                          % _legacy,
                "criteria_version": _cur_a4v,
                "graph_file": _graph_rel, "details": _details[:20]}
    _rate = _mismatch / float(_total)
    _status = "PASS" if _rate <= 0.05 else "RULER_BROKEN"
    _module_logger.info(
        "[337刀7][SC-3] total=%d mismatch=%d rate=%.4f legacy_skipped=%d"
        " status=%s criteria=%s graph=%s proc_gen=%s"
        "（>0.05 即判尺子坏；只产计数与结论，不入 issues/趋势门禁）",
        _total, _mismatch, _rate, _legacy, _status, _cur_a4v, _graph_rel, _pg7)
    return {"total": _total, "mismatch": _mismatch, "rate": round(_rate, 4),
            "threshold": 0.05, "ruler_broken": _rate > 0.05, "status": _status,
            "legacy_skipped": _legacy, "criteria_version": _cur_a4v,
            "graph_file": _graph_rel, "details": _details[:20]}

def judge_ratio(rows: list[dict]) -> dict:
    """★320批刀1：压测外部裁判比 —— `met / (met + violated - criteria_gap)`。

    口径差（``criteria_gap``，如 a4_gap 116：说明书口径 vs 图类簇口径）**不进分母**，
    否则每一圈都会被结构性口径差压成失败（烛微320§0-3：25/118≈21%）。
    ``real_violation`` 仍进分母——真违反就是要扣分。
    """
    _met = _viol = _cg = _real = 0
    for _r in rows or []:
        _v = str(_r.get("verdict") or "")
        _g = str(_r.get("gap_reason") or "")
        if _v == "met":
            _met += 1
        elif _v == "violated":
            if _g == "criteria_gap":
                _cg += 1
            else:
                _viol += 1
                if _g == "real_violation":
                    _real += 1
    _den = _met + _viol
    _ratio = round(_met / _den, 4) if _den else None
    return {
        "met": _met, "violated": _viol, "criteria_gap": _cg,
        "real_violation": _real, "denominator": _den,
        "ratio": _ratio, "threshold": 0.8,
        "pass": bool(_den and _ratio is not None and _ratio >= 0.8),
        "denominator_excludes": "criteria_gap",
    }


def ledger_judge_ratio(project_root: str | None = None) -> dict:
    """★320批刀1：按当前台账算裁判比（只读，不写盘）。"""
    return judge_ratio(_read_jsonl(ledger_path(project_root)))


def a1_uncalibrated_summary(rows: list | None = None) -> dict:
    """★326批刀3：a1.cluster.* 复核条目单列（**不进归因计数**）。只读。

    为什么必须有这个口：裁决②把 7 条 a1.cluster 从归因里拿出来挂"待标定"，
    如果报表仍按 `counts[疑似口径差]` / `counts[可判真违规候选]` 出数，
    这 7 条会被静默算进去——所以**待标定档单独出数**。

    ★口径边界（必须同时读）：`excluded_from_any_count` 指的是**归因计数**；
    裁判比分母是另一件事——325 回填已把这 7 条 gap_reason 推断成 real_violation，
    裁决①要求裁判比稳定在 0.9208，所以本批**不改 gap_reason**，它们在裁判比里
    仍算 violated（每条都带 `judge_denominator_effect`，看得见、不藏着）。
    """
    _rows = rows
    if _rows is None:
        _rows = _read_jsonl(ledger_path())
    _items = []
    for _r in (_rows or []):
        if not isinstance(_r, dict) or str(_r.get("verdict") or "") != "violated":
            continue
        _aid = str(_r.get("assertion_id") or "")
        if not _aid.startswith(_A1_UNCALIBRATED_PREFIX):
            continue
        _meas = _r.get("measured") if isinstance(_r.get("measured"), dict) else {}
        _items.append({"assertion_id": _aid,
                       "actual": _meas.get("actual"),
                       "gap_reason_now": str(_r.get("gap_reason") or ""),
                       "gap_reason_origin_now": (str(_r.get("gap_reason_origin") or "")
                                                 or "legacy(缺失)"),
                       "attribution_suggestion": _O3_ATTR_UNCALIBRATED,
                       "counted": False,
                       "judge_denominator_effect": "仍计入裁判比分母（gap_reason 未改）"})
    return {"criteria_version": _A1_CRITERIA_VERSION,
            "decision": _A1_DECISION,
            "prefix": _A1_UNCALIBRATED_PREFIX,
            "count": len(_items),
            "a1_threshold_uncalibrated": len(_items),
            "excluded_from_any_count": True,
            "attribution_suggestion": _O3_ATTR_UNCALIBRATED,
            "items": _items}


def judge_ratio_guard(rows: list | None = None, expect: float | None = 0.9208) -> dict:
    """★326批刀3：裁判比**零变化**自证（只读）。裁定②的前提就是"不动裁判口径"。

    判据：`judge_ratio` 的 ratio 必须与 `expect` 相等（四舍五入到 4 位）。
    不相等 ⇒ `changed=True` + 归因字段（met/violated/criteria_gap 三计数），
    供星轨查是不是有人静默改了判罚。
    """
    _rows = rows
    if _rows is None:
        _rows = _read_jsonl(ledger_path())
    _jr = judge_ratio(_rows)
    _ok = (expect is None) or (_jr.get("ratio") == expect)
    return {"ratio": _jr.get("ratio"), "expect": expect, "unchanged": bool(_ok),
            "denominator": _jr.get("denominator"), "met": _jr.get("met"),
            "violated": _jr.get("violated"), "criteria_gap": _jr.get("criteria_gap"),
            "real_violation": _jr.get("real_violation"),
            "note": ("裁判比分母仍排除 criteria_gap；a1.cluster 未入 "
                     "_CRITERIA_GAP_PREFIXES，故 7 条待标定条目未改变任何计数")}


def sweep(project_root: str | None = None, loop: int | None = None,
          source: str = "live") -> dict:
    """跑一次台账（幂等：同一 assertion_id 同日只记首次），返回统计。

    Args:
        loop: 压测圈次。给定时 assertion_id 加前缀 `loop.<N>.`，供每圈外部裁判一一对应。
    """
    _root = project_root or os.getcwd()
    _p = ledger_path(_root)
    _src = _norm_source(source)
    _rows = collect_assertions(_root, source=_src)
    if loop is not None:
        for _r in _rows:
            _r["assertion_id"] = "loop.%d.%s" % (int(loop), _r["assertion_id"])

    # ★320批刀0：幂等键由 (assertion_id, 当天) 改为 **(assertion_id, 当天, 来源)**。
    #   归因：烛微320§0-2-① 活体 04:00 sweep `added=0`，01—08 时 0 新增——真因是
    #   00:0x 有**验证运行**先写了当日 114 条，把「当日幂等位」占满，04:00 活体
    #   sweep 逐条命中 `(id, today)` ⇒ 全部 skip ⇒ 0 产出（不是版本行为）。
    #   加来源维度后：验证运行只占 verification 位，活体 live 位仍空 ⇒ 能自然产出。
    _existing: set[tuple[str, str, str]] = set()
    _total = 0
    for _r in _read_jsonl(_p):
        _total += 1
        _day = time.strftime("%Y-%m-%d", time.localtime(
            float((_r.get("measured") or {}).get("observed_at") or 0)))
        _existing.add((str(_r.get("assertion_id")), _day,
                       _row_source(_r)))

    _today = time.strftime("%Y-%m-%d", time.localtime())
    _added = 0
    _skipped = 0
    # ★333批刀5：新写入的 violated 缺 gap_reason 计数（**只告警，不阻断、不伪造**）
    _vio_no_gap = 0
    _verdicts: dict[str, int] = {}
    _gaps: dict[str, int] = {}
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            for _r in _rows:
                _verdicts[_r["verdict"]] = _verdicts.get(_r["verdict"], 0) + 1
                if _r.get("gap_reason"):
                    _gaps[_r["gap_reason"]] = _gaps.get(_r["gap_reason"], 0) + 1
                _key = (str(_r.get("assertion_id")), _today, _src)
                if _key in _existing:
                    _skipped += 1
                    continue
                # ★333批刀5（烛微332建议·弱化版）：**新写入**的 violated 若没有归属就喊一声。
                #   为什么只告警不拦截：331刀3 已裁定不做 `_mk` 强校验（会逼着改存量写法），
                #   但"100% 带归属"若全靠历史回填兜着，就是巧合而不是机制。
                #   本检查**不改 verdict、不伪造 gap_reason、不跳过写入**。
                if (str(_r.get("verdict") or "") == "violated"
                        and not str(_r.get("gap_reason") or "").strip()):
                    _vio_no_gap += 1
                    _module_logger.warning(
                        "[333刀5][gap归属] 新写入 violated **缺 gap_reason**："
                        "assertion_id=%s source=%s gap_origin=%s criteria_version=%s"
                        " ⇒ 已照写（不伪造），但该条无法归因"
                        "（本圈累计 %d 条）",
                        _r.get("assertion_id"), _r.get("source"),
                        _r.get("gap_reason_origin"),
                        _r.get("criteria_version"), _vio_no_gap)
                _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
                _existing.add(_key)
                _added += 1
                _total += 1
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.sweep")
        _module_logger.info(
            "[320刀0][ledger_sweep] triggered=True generated=0 skipped=%d "
            "reason=write_error:%s source=%s", _skipped, type(_e).__name__, _src)
        return {"added": 0, "total": _total, "verdicts": _verdicts,
                "error": type(_e).__name__, "source": _src,
                "triggered": True, "skipped": _skipped,
                "reason": "write_error"}

    # ★320批刀0：显式四段留痕（triggered/generated/skipped/reason），
    #   使「钩子未触发」与「触发了但被幂等挡住」在日志里可区分——
    #   317 批只打一条 [318刀5][ledger] 看不出 skipped，才让 0 产出悬了一夜。
    _reason = ("all_idempotent_skipped" if (_added == 0 and _skipped > 0)
               else ("no_assertions" if not _rows else "ok"))
    _module_logger.info(
        "[320刀0][ledger_sweep] triggered=True generated=%d skipped=%d reason=%s "
        "source=%s total=%d verdict=%s gap_reason=%s（只观测不决策）",
        _added, _skipped, _reason, _src, _total, _verdicts, _gaps)
    if _vio_no_gap:
        # ★333批刀5：单独一行汇总——警告逐条打之外，还要一个"本圈总计"可被 grep
        _module_logger.warning(
            "[333刀5][gap归属] 本圈新写入 violated 缺归属 **%d/%d** 条"
            "（写入口告警，不阻断不伪造；存量 25 条不动）",
            _vio_no_gap, _added)
    _module_logger.info(
        "[318刀5][ledger] 新增 %d 条 / 累计 %d 条 verdict=%s gap_reason=%s（只观测不决策）",
        _added, _total, _verdicts, _gaps)
    return {"added": _added, "total": _total, "verdicts": _verdicts,
            "gap_reasons": _gaps, "path": _p, "source": _src,
            "triggered": True, "skipped": _skipped, "reason": _reason,
            # ★333批刀5：本圈新写入的 violated 缺归属条数（0 是合法读数）
            "violated_missing_gap": _vio_no_gap}
