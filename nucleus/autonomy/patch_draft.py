# -*- coding: utf-8 -*-
"""475 批第一期：**曈曈主笔草稿的影子旁路**（角色翻转的第一步）。

目标（用户定义）
--------------
把「外包 LLM 生成 modified_code」逐步变成「曈曈自己处理、LLM 辅助」。
本模块做的是**第一步：建立曈曈自己出草稿的通道 + 与 LLM 产物的 A/B 对照**，
不替换任何东西。

为什么叫「影子」
--------------
`PatchManager.save_pending_patch()` 在保存前旁路调 `note_patch_draft_shadow(patch)`：

  * **只读** patch，深拷贝后在内存里跑自有算子；绝不改 patch 的任何字段；
  * 产出只写自有台账 `data/autonomy/patch_draft_shadow.jsonl`（/data/ 已 ignore）；
  * 不改状态、不碰八闸/审批/apply/自重启/日限额/冷却等任何判据；
  * 任何异常一律 `silent_exc` 吞掉，绝不阻断入池。
  * `ENABLE_M475_PATCH_DRAFT_SHADOW = False` ⇒ 逐字回滚刀1 之前的行为。

诚实的边界（重要）
----------------
第一批 3 个算子只会修**结构性缺陷**（缩进层级、docstring/类型注解、裸静默 except），
不会实现业务逻辑。所以：

  * 算子命中并真的改了代码 ⇒ `covered=True`，这一题曈曈**能自己出草稿**；
  * 没命中 ⇒ `covered=False / external_needed=True`，明确登记「这一题仍需 LLM」，
    **绝不假装会写**。这正是「按 error_type 分桶收敛」的度量基础。

基准独立（铁律 30）
------------------
`_code_metrics()` 直接用 `ast.parse` + 文本解析，对曈曈草稿与 LLM 产物用
**同一把尺**；绝不拿算子的 `verify()` 结论当基准（那是循环论证）。

铁律 304
--------
本模块不 import 任何 HTTP / 模型客户端，覆盖不了只是 `external_needed=True`，
内核自己不求助外部。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import sys
import textwrap
import time
from typing import Any

from nucleus._silent_except import silent_exc

from .kernel import CapabilityContext
from .operators import DOMAIN_CODE_REPAIR, build_default_kernel

SCHEMA = "autonomy_patch_draft/475.v1"
SHADOW_LEDGER_REL = "data/autonomy/patch_draft_shadow.jsonl"


def _cfg_on(name: str, default: bool) -> bool:
    """★只有 None 才算缺省；0/False 是显式关，不能被 or default 吞掉。"""
    try:
        import config as _c
        _v = getattr(_c, name, None)
        return default if _v is None else bool(_v)
    except Exception as _e:
        silent_exc(_e, where="autonomy.patch_draft::_cfg_on", level="debug")
        return default


def _body_indent(code: str) -> int | None:
    """def 之后第一个非空、非注释行的缩进宽度（不猜，直接按文本算）。"""
    _lines = str(code or "").split("\n")
    _seen_def = False
    for _l in _lines:
        if not _seen_def:
            if _l.lstrip().startswith(("def ", "async def ")):
                _seen_def = True
            continue
        if not _l.strip() or _l.strip().startswith("#"):
            continue
        return len(_l) - len(_l.lstrip(" "))
    return None


def _def_indent(code: str) -> int | None:
    for _l in str(code or "").split("\n"):
        if _l.lstrip().startswith(("def ", "async def ")):
            return len(_l) - len(_l.lstrip(" "))
    return None


def _code_metrics(code: str) -> dict:
    """对一份方法片段做**独立**结构度量。两边用同一把尺，不依赖算子结论。

    补丁里的 `modified_code` 是带类缩进的方法片段，直接 `ast.parse` 会报
    `unexpected indent`（473 批实测踩过），所以先 dedent 再解析。
    """
    _raw = str(code or "")
    _ded = textwrap.dedent(_raw)
    _out: dict = {
        "lines": len(_raw.split("\n")),
        "bytes": len(_raw.encode("utf-8")),
        "sha256": hashlib.sha256(_ded.encode("utf-8")).hexdigest()[:16],
        "def_indent": _def_indent(_raw),
        "body_indent": _body_indent(_raw),
        "parse_ok": False,
        "parse_error": "",
        "has_docstring": False,
        "bare_except_pass": 0,
        "func_count": 0,
    }
    try:
        _tree = ast.parse(_ded)
        _out["parse_ok"] = True
    except (SyntaxError, ValueError) as _e:
        silent_exc(_e, where="autonomy.patch_draft::_code_metrics parse", level="debug")
        _out["parse_error"] = f"{type(_e).__name__}: {str(_e)[:120]}"
        return _out
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _out["func_count"] += 1
            if not _out["has_docstring"] and ast.get_docstring(_n):
                _out["has_docstring"] = True
        if isinstance(_n, ast.ExceptHandler):
            _body = list(_n.body)
            if len(_body) == 1 and isinstance(_body[0], ast.Pass):
                _out["bare_except_pass"] += 1
    return _out


def _run_ops(code: str, issue_type: str, file: str,
             kernel: Any = None) -> tuple:
    """在**一份给定代码**上跑自有算子，返回 (产出代码, 命中算子, 变更列表)。

    不写盘、不改入参；kernel 可注入（单测用）。
    """
    _ctx = CapabilityContext(
        domain=DOMAIN_CODE_REPAIR,
        artifact=copy.deepcopy({"original_code": code, "modified_code": code}),
        evidence={"issue_type": issue_type},
        metadata={"file": file},
    )
    _k = kernel if kernel is not None else build_default_kernel(
        enforce=True, ledger_enabled=False)
    _res = _k.run(_ctx, enforce=True)
    _out = str((_res.get("artifact") or {}).get("modified_code") or code)
    _records = _res.get("records") or []
    _hits = [_r.get("capability_id") for _r in _records
             if isinstance(_r, dict) and _r.get("status") == "applied"]
    _changes = [str(_c) for _r in _records if isinstance(_r, dict)
                for _c in (_r.get("changes") or [])]
    return _out, _hits, _changes


def draft_from_patch(patch: dict, kernel: Any = None) -> dict:
    """曈曈出**两份**产物，与 LLM 产物做 A/B 对照。**纯旁路**。

    两份产物的语义必须分清（这是角色翻转的两个不同维度）：
      * ``self_draft``    : 从 ``original_code`` 出发主笔 ⇒ 量「曈曈能不能自己写」；
      * ``self_revision`` : 以 LLM 的 ``modified_code`` 为输入做修订 ⇒
        量「LLM 产物里有多少需要曈曈把关」，即「LLM 辅助、曈曈处理」的实证。

    475 刀1 真实样本冒烟：4 条补丁主笔都覆盖不了（业务逻辑修复，诚实登记
    ``external_needed=True``），但 **4/4 的 LLM 产物都被算子检出结构性缺陷**
    （3 条方法体多缩 4 格、1 条裸 ``except: pass``）——正是 473 批刀22b/c
    我两次手工收口的问题。所以修订维度先有了真实价值。

    ``kernel`` 可注入（单测用），生产不传 ⇒ 用装好第一批算子的默认内核。
    """
    _p = patch if isinstance(patch, dict) else {}
    _orig = str(_p.get("original_code") or "")
    _llm = str(_p.get("modified_code") or "")
    _issue = str(_p.get("issue_type") or "unknown")
    _file = str(_p.get("file") or "")
    if not _orig:
        return {"status": "no_original", "covered": False,
                "external_needed": True}
    try:
        # ① 主笔：从原文出发，不看 LLM 产物（否则仍然是擦屁股）
        _draft, _hits, _changes = _run_ops(_orig, _issue, _file, kernel)
        # ② 修订：把 LLM 产物交给曈曈把关
        _revision, _rev_hits, _rev_changes = _run_ops(_llm, _issue, _file, kernel)
    except Exception as _e:
        silent_exc(_e, where="autonomy.patch_draft::draft_from_patch kernel", level="debug")
        return {"status": "kernel_error", "covered": False,
                "external_needed": True, "error_type": type(_e).__name__}

    # ★covered 必须「算子真的改了代码」才算，光 detect 到不算（判别力）
    _covered = bool(_hits) and (_draft != _orig)
    _rev_covered = bool(_rev_hits) and (_revision != _llm)

    _self_m = _code_metrics(_draft)
    _llm_m = _code_metrics(_llm)
    _orig_m = _code_metrics(_orig)
    _rev_m = _code_metrics(_revision)
    if not _covered:
        _verdict = "uncovered_needs_external"
    elif _draft == _llm:
        _verdict = "identical_to_llm"
    else:
        _verdict = "self_covered"

    return {
        "status": "ok",
        "schema": SCHEMA,
        "patch_id": str(_p.get("id") or ""),
        "file": _file,
        "method": str(_p.get("method") or ""),
        "issue_type": _issue,
        "covered": _covered,
        "external_needed": not _covered,
        "verdict": _verdict,
        "operators": _hits,
        "changes": _changes[:20],
        "draft_sha256": _self_m["sha256"],
        "draft_preview": _draft[:160],
        "revision_covered": _rev_covered,
        "llm_defects": _rev_hits,
        "revision_changes": _rev_changes[:20],
        "self": _self_m,
        "llm": _llm_m,
        "origin": _orig_m,
        "revision": _rev_m,
        # ★477：修订后的**代码本体**（此前只回指标，调用方拿不到可用产物）。
        #   仅当 revision_covered=True 且修订后语法干净时才可被下游采用。
        "revision_code": _revision,
        "quality": {
            "self_parse_ok": _self_m["parse_ok"],
            "llm_parse_ok": _llm_m["parse_ok"],
            "revision_parse_ok": _rev_m["parse_ok"],
            "self_bare_except_pass": _self_m["bare_except_pass"],
            "llm_bare_except_pass": _llm_m["bare_except_pass"],
            "revision_bare_except_pass": _rev_m["bare_except_pass"],
            "self_has_docstring": _self_m["has_docstring"],
            "llm_has_docstring": _llm_m["has_docstring"],
            "self_body_indent": _self_m["body_indent"],
            "llm_body_indent": _llm_m["body_indent"],
            "revision_body_indent": _rev_m["body_indent"],
        },
    }


def _note(rec: dict, ledger_path: str | None = None) -> None:
    """append-only 台账。★pytest 走默认生产路径时不写（铁律：别灌假经验）。"""
    _path = ledger_path or SHADOW_LEDGER_REL
    _is_default = ledger_path is None
    if _is_default and "pytest" in sys.modules:
        return
    try:
        _dir = os.path.dirname(os.path.abspath(_path))
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _rec = dict(rec)
        _rec.update({"schema": SCHEMA, "at": time.time()})
        with open(_path, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="autonomy.patch_draft::_note", level="debug")


def note_patch_draft_shadow(patch: dict, ledger_path: str | None = None) -> dict:
    """生产挂点：`save_pending_patch()` 保存前调用。**任何异常都不外溢**。"""
    if not _cfg_on("ENABLE_M475_PATCH_DRAFT_SHADOW", True):
        return {"status": "disabled"}
    try:
        _rec = draft_from_patch(patch)
        # ★只有成功产出对照才留痕：no_original / kernel_error 不写台账，
        #   否则一次异常输入就往 A/B 表里灌一条无对照价值的脏数据。
        if _rec.get("status") == "ok":
            _note(_rec, ledger_path=ledger_path)
        return _rec
    except Exception as _e:
        silent_exc(_e, where="autonomy.patch_draft::note_patch_draft_shadow", level="debug")
        return {"status": "error", "error_type": type(_e).__name__}


def consume_existing_patches(max_patches: int = 200,
                             ledger_path: str | None = None,
                             root: str | None = None) -> dict:
    """★475刀27：**补上消费端** —— 离线消费存量补丁，产出 A/B 对照。

    475 刀1 的影子旁路挂在 `save_pending_patch()`（链路最末端），而实盘
    「自检视 462 条 → 当日入池 0/1 → LLM 通道熔断」导致**没有新补丁入池**
    ⇒ 旁路空转、台账一直是空的。这正是用户指出的「有产出但消费端薄弱」。

    本函数把 `pending_patches.json` + `patch_history.json` 里 91 条真实补丁
    批量转成 A/B 对照，去重后写台账；不依赖新产出、不改任何生产判据。

    ★pytest 走默认路径时直接跳过（与 kernel `_note` 同口径）：
      既避免灌假数据，也避免单测里空跑 91 次算子。
    """
    _ledger = ledger_path or SHADOW_LEDGER_REL
    if ledger_path is None and "pytest" in sys.modules:
        return {"status": "skipped_test"}
    _root = root or os.getcwd()
    _seen: set = set()
    try:
        with open(_ledger, encoding="utf-8") as _f:
            for _ln in _f:
                if not _ln.strip():
                    continue
                try:
                    _r = json.loads(_ln)
                    if isinstance(_r, dict) and _r.get("patch_id"):
                        _seen.add(str(_r["patch_id"]))
                except (ValueError, TypeError) as _e:
                    silent_exc(_e, where="autonomy.patch_draft::backfill row", level="debug")
    except OSError as _e:
        silent_exc(_e, where="autonomy.patch_draft::backfill ledger open", level="debug")

    _rows: list = []
    for _name in ("pending_patches.json", "patch_history.json"):
        _p = os.path.join(_root, "data", "patches", _name)
        try:
            with open(_p, encoding="utf-8") as _f:
                _d = json.load(_f)
            _part = _d if isinstance(_d, list) else (
                _d.get("patches") or _d.get("history") or [])
            _rows.extend(_r for _r in _part if isinstance(_r, dict))
        except (OSError, ValueError, TypeError) as _e:
            silent_exc(_e, where="autonomy.patch_draft::backfill read " + _name,
                       level="debug")

    _new = 0
    _skipped = 0
    _scanned = 0
    for _r in _rows:
        _pid = str(_r.get("id") or "")
        if (not _pid or _pid in _seen or not _r.get("original_code")
                or not _r.get("modified_code")):
            _skipped += 1
            continue
        if _scanned >= max(1, int(max_patches)):
            break
        _scanned += 1
        _rec = draft_from_patch(_r)
        if _rec.get("status") != "ok":
            continue
        _rec["source"] = "offline_backfill"   # 与运行时旁路留痕区分
        _note(_rec, ledger_path=_ledger)
        _seen.add(_pid)
        _new += 1
    return {"status": "ok", "scanned": _scanned, "new": _new,
            "skipped_dup": _skipped, "stats": shadow_stats(_ledger)}


def shadow_stats(ledger_path: str | None = None) -> dict:
    """读台账产出 A/B 对照表：按 issue_type 分桶，看哪类题曈曈已能主笔。"""
    _path = ledger_path or SHADOW_LEDGER_REL
    _rows: list = []
    try:
        with open(_path, encoding="utf-8") as _f:
            for _ln in _f:
                if not _ln.strip():
                    continue
                try:
                    _rows.append(json.loads(_ln))
                except (ValueError, TypeError) as _e:
                    silent_exc(_e, where="autonomy.patch_draft::shadow_stats row", level="debug")
    except OSError as _e:
        silent_exc(_e, where="autonomy.patch_draft::shadow_stats open", level="debug")
        return {"total": 0, "by_issue_type": {}, "operator_hits": {},
                "coverage_rate": None}
    _by: dict = {}
    _ops: dict = {}
    _defects: dict = {}
    for _r in _rows:
        _t = str(_r.get("issue_type") or "unknown")
        _b = _by.setdefault(_t, {"total": 0, "covered": 0,
                                 "revision_covered": 0,
                                 "self_parse_ok": 0, "llm_parse_ok": 0})
        _b["total"] += 1
        _b["covered"] += int(bool(_r.get("covered")))
        _b["revision_covered"] += int(bool(_r.get("revision_covered")))
        _q = _r.get("quality") or {}
        _b["self_parse_ok"] += int(bool(_q.get("self_parse_ok")))
        _b["llm_parse_ok"] += int(bool(_q.get("llm_parse_ok")))
        for _op in _r.get("operators") or []:
            _ops[str(_op)] = _ops.get(str(_op), 0) + 1
        for _d in _r.get("llm_defects") or []:
            _defects[str(_d)] = _defects.get(str(_d), 0) + 1
    _n = len(_rows) or 1
    return {
        "total": len(_rows),
        "covered": sum(1 for _r in _rows if _r.get("covered")),
        "coverage_rate": round(sum(1 for _r in _rows if _r.get("covered")) / _n, 4),
        "revision_covered": sum(1 for _r in _rows if _r.get("revision_covered")),
        "revision_rate": round(
            sum(1 for _r in _rows if _r.get("revision_covered")) / _n, 4),
        "by_issue_type": _by,
        "operator_hits": _ops,
        "llm_defect_hits": _defects,
        "self_parse_ok": sum(1 for _r in _rows
                             if (_r.get("quality") or {}).get("self_parse_ok")),
        "llm_parse_ok": sum(1 for _r in _rows
                            if (_r.get("quality") or {}).get("llm_parse_ok")),
    }
