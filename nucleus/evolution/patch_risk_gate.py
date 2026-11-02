# -*- coding: utf-8 -*-
"""★406刀4（P1 · 高风险面）：低风险补丁的「三条件前置闸」+ diff 窄闸 + 留痕。

## 为什么需要它（破零卡在哪）

406 批实测（`tmp/m406_k4_probe_t0.py`，零模型调用）：

* 观察位**已经是 5 条**（385刀1 多值化 + 387刀4 补满容量），任务书「只有 1 条」的前提已过时；
* `data/patches/pending_patches.json` 现有 11 条：8 条 `patch_llm_*` 都走到了
  `status=runtime_verified` + `verified=True`，却**全部 `applied=False`**；
* 3 条 `o3_*` 是 `expired_pending`；pending 里 `applied=True` 的**一条也没有**；
* 风险标签**早已存在**（`risk_level` 中文低/中/高 + `risk_level_basis`），
  但它是按 `issue_type` 推断的（`_m280_risk_by_issue_type`），
  **不是**任务书对 low 的定义「只改注释/文档/测试/配置默认值 False」。

⇒ 真正缺的是：**按 diff 内容**判「这次改动到底动了什么」，再加上任务书那三条，才敢自动应用。

## 本模块的定位（能力边界，写在结论前面）

本模块只做**判定与留痕**，不写源码、不碰审批状态机。
真正的写盘仍走既有通路（`PatchManager.apply_all_pending` ← `_m101` / `_m85` 放行）。
它给那条通路**再收一道窄闸**：只有

    diff ∈ {comment_only, doc_only, test_only}
    ∧ 单测全绿 ∧ 不跨器官 ∧ 不碰审批池/状态机

才允许自动应用 —— 语义上等价于任务书对 low 的定义（「只改注释/文档/测试/配置默认值 False」）。

★`judge()` 返回的 `low` **不等于"补丁是对的"**，只等于"它动的面足够窄、够格走免签"。
★`PATCH_M406_NARROW_GATE=False` 时 `gate_allows()` 恒返回 True ⇒ 完全回退 406 前行为（零回归）。
"""
from __future__ import annotations

import ast
import json
import os
import re
import textwrap
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PatchRiskGate")

#: 判定结论的 schema 版本（写进每条 pending 的 `m406_risk`）
SCHEMA = "patch_risk_gate/406.v1"

#: 任务书对 low 的 diff 定义 —— **只**动这几类才可能是 low
DIFF_NARROW_KINDS = ("comment_only", "doc_only", "test_only")

#: ★412刀4：`delete_only`（只删不增）**能识别，但默认不开闸**。
#:   为什么不放进 DIFF_NARROW_KINDS：放进去就等于**立刻真放行删代码**，
#:   而任务书/红线都写明"本批只 dry-run 不真 apply"。所以判据先具备、
#:   开闸走独立开关 `config.PATCH_M412_DELETE_ONLY_GATE`（默认 False ⇒ 零回归）。
DELETE_ONLY_KIND = "delete_only"


def _delete_only_enabled() -> bool:
    """★412刀4：是否把 `delete_only` 计入窄闸（默认 False = 不开闸）。"""
    try:
        import config as _c412
        return bool(getattr(_c412, "PATCH_M412_DELETE_ONLY_GATE", False))
    except Exception as _e:
        silent_exc(_e, where="patch_risk_gate._delete_only_enabled", level="debug")
        return False


def narrow_kinds() -> tuple:
    """当前生效的窄闸清单（含灰度开关控制的 delete_only）。"""
    if _delete_only_enabled():
        return tuple(DIFF_NARROW_KINDS) + (DELETE_ONLY_KIND,)
    return tuple(DIFF_NARROW_KINDS)

#: 台账里算「已验证」的状态集合（与 `zero_break_judge.VERIFIED_STATUSES` 同源）
VERIFIED_STATUSES = ("runtime_verified", "verified", "applied_verified")

#: 审批池 / 状态机 / 审计池 —— 碰这些**一律 high**（不论 diff 多窄）。
#: 依据：任务书 high 的定义「跨器官 / 改状态机 / 改审批池」，这三类是同一个面。
APPROVAL_POOL_FILES = frozenset((
    "config.py",
    "main.py",
    "nucleus/reasoning/PatchManager.py",
    "nucleus/reasoning/SafeEvolutionExecutor.py",
    "nucleus/evolution/PatchAutoApprover.py",
    "nucleus/evolution/patch_lifecycle.py",
    "nucleus/evolution/patch_execution_dispatch.py",
    "nucleus/evolution/pending_write.py",
    "nucleus/evolution/patch_verification_split.py",
    "nucleus/evolution/verify_feedback.py",
    "nucleus/evolution/rollback_audit.py",
    "nucleus/evolution/zero_break_judge.py",
    "nucleus/evolution/patch_risk_gate.py",
))

#: 器官根目录（`organs/<name>/`）—— 用于「不跨器官」
ORGAN_ROOT = "organs"

#: `from organs.xxx` / `import organs.xxx` —— 只匹配**器官包**，不误伤 `organs` 本体
_RE_ORGAN_IMPORT = re.compile(r"(?:from|import)\s+organs\.([A-Za-z_][A-Za-z0-9_]*)")

#: 留痕目录（`data/` 整目录 gitignore ⇒ 只落盘不进 git）
_MONITOR_REL = os.path.join("data", "monitor")
_AUDIT_PREFIX = "patch_risk_audit_"
_SEEN_REL = os.path.join("data", "monitor", "patch_risk_seen.json")
_SEEN_MAX = 2000


def _project_root(project_root: str | None = None) -> str:
    """项目根（`project_root` 显式优先；不传按本文件位置推三层）。"""
    if project_root:
        return str(project_root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _norm_rel(path: Any) -> str:
    """路径归一：反斜杠转正斜杠、去 `./` 前缀（判据只认结构，不认大小写之外的平台差异）。"""
    _p = str(path or "").strip().replace("\\", "/")
    while _p.startswith("./"):
        _p = _p[2:]
    return _p


# ============ 一、diff 内容分类（任务书对 low 的定义） ============

def _skeleton(src: str) -> str:
    """源码去掉**全部 docstring**后的 AST 骨架（注释本来就不进 AST）。

    两侧骨架相同 ⇒ 差异只可能落在注释或 docstring 上 ⇒ `comment_only`。
    ★为什么不用「逐行剥 `#` 再比」：docstring 不以 `#` 开头，剥不掉；
      而 docstring 改动恰恰是**最常见也最安全**的一类补丁。
    """
    _tree = ast.parse(textwrap.dedent(src))
    for _node in ast.walk(_tree):
        if isinstance(_node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _body = getattr(_node, "body", None)
            if _body and isinstance(_body[0], ast.Expr):
                _v = _body[0].value
                if isinstance(_v, ast.Constant) and isinstance(_v.value, str):
                    _node.body = _body[1:] or [ast.Pass()]
    return ast.dump(_tree)


def _is_delete_only(orig: str, new: str) -> bool:
    """是否"只删不增"：**无任何新增有效行** 且 **有效行数变少**。

    ★为什么不用 difflib 精确 diff：这里的用途是"这条补丁有没有新增代码"，
      新增行判定用 `_added_lines`（与 import 增量判定同一口径）就够，
      精确 diff 反而会把"移动了几行"判成既增又删 —— 那不是 delete_only。
    """
    _added = _added_lines(orig, new)
    if _added:
        return False
    def _n(txt: str) -> int:
        return len([_l for _l in txt.splitlines() if _l.strip()])
    return _n(new) < _n(orig)


def diff_kind(record: Any) -> str:
    """这次补丁**动了什么**：`comment_only` / `doc_only` / `test_only` / `logic` / `unknown`。

    判定顺序（先便宜后贵，先结构后解析）：
      ① 目标在 `tests/` → `test_only`
      ② 目标是 `.md` 或 `docs/` → `doc_only`
      ③ 前后代码 AST 骨架相同（去 docstring）→ `comment_only`
      ④ 其余 → `logic`（**不可**自动应用）
      ⑤ 缺字段 / 解析失败 → `unknown`（同样不可自动应用）
    """
    if not isinstance(record, dict):
        return "unknown"
    _rel = _norm_rel(record.get("file"))
    if not _rel:
        return "unknown"
    if _rel.startswith("tests/") or "/tests/" in _rel:
        return "test_only"
    if _rel.endswith(".md") or _rel.startswith("docs/"):
        return "doc_only"
    _orig = str(record.get("original_code") or "")
    _new = str(record.get("modified_code") or "")
    if not _orig.strip() or not _new.strip():
        return "unknown"
    if _orig.strip() == _new.strip():
        # 前后一模一样 ⇒ 这不是"只改注释"，是**没改**；按 logic 处理（不放行）
        return "logic"
    try:
        if _skeleton(_orig) == _skeleton(_new):
            return "comment_only"
    except (SyntaxError, ValueError) as _e:
        silent_exc(_e, where="patch_risk_gate.diff_kind 解析前后片段", level="debug")
        return "unknown"
    # ★412刀4：只删不增 ⇒ `delete_only`（注意：**识别** 与 **放行** 是两件事）
    if _is_delete_only(_orig, _new):
        return DELETE_ONLY_KIND
    return "logic"


# ============ 二、任务书三条件 ============

def check_unit_tests_green(record: Any) -> dict:
    """条件①「单测全绿」—— 以**验证产物**为证，不看自我声明。

    四项同时成立才算绿：
      * `verification.passed is True`（有验证跑过且过）
      * `no_regression is True`（有回归判定且无回归）
      * `post_apply_errors ∈ {0, None}`（None = 还没应用，属"不反对"）
      * `verified is True` 或 `status ∈ VERIFIED_STATUSES`（台账认过）
    """
    if not isinstance(record, dict):
        return {"ok": False, "why": "not_dict"}
    _v = record.get("verification")
    _passed = isinstance(_v, dict) and _v.get("passed") is True
    _no_reg = record.get("no_regression") is True
    _pae = record.get("post_apply_errors")
    _zero_err = _pae in (0, None)
    _st = str(record.get("status") or "").strip().lower()
    _verified = (record.get("verified") is True) or (_st in VERIFIED_STATUSES)
    _ok = bool(_passed and _no_reg and _zero_err and _verified)
    return {"ok": _ok, "passed": _passed, "no_regression": _no_reg,
            "post_apply_errors": _pae, "verified": _verified}


def _added_lines(orig: str, new: str) -> list:
    """new 里有、orig 里没有的行（保序去重）。粗但够用于 import 增量判定。"""
    _o = {_l.strip() for _l in orig.splitlines()}
    _out = []
    for _l in new.splitlines():
        _s = _l.strip()
        if _s and _s not in _o and _s not in _out:
            _out.append(_s)
    return _out


def check_single_organ(record: Any) -> dict:
    """条件②「不跨器官」。

    `organs/` 下的补丁：目标必须落在**单个**器官目录内，且新增行里不得出现
    对其他器官包的 import（`from organs.other…` / `import organs.other…`）。
    非 `organs/` 下的补丁（`nucleus/`、`utils/`…）本条件视为满足 ——
    它们不属任何器官，"跨器官"无从谈起；而它们是不是审批池由条件③管。
    """
    if not isinstance(record, dict):
        return {"ok": False, "why": "not_dict"}
    _rel = _norm_rel(record.get("file"))
    _parts = _rel.split("/")
    _organ = _parts[1] if (len(_parts) >= 3 and _parts[0] == ORGAN_ROOT) else ""
    if not _organ:
        return {"ok": True, "organ": "", "crossed": [], "why": "not_an_organ_file"}
    _crossed = []
    for _line in _added_lines(str(record.get("original_code") or ""),
                              str(record.get("modified_code") or "")):
        for _other in _RE_ORGAN_IMPORT.findall(_line):
            if _other != _organ and _other not in _crossed:
                _crossed.append(_other)
    return {"ok": not _crossed, "organ": _organ, "crossed": _crossed[:5],
            "why": "" if not _crossed else "cross_organ_import"}


def check_not_approval_pool(record: Any) -> dict:
    """条件③「不碰审批池 / 状态机 / 审计池」—— 单个真源清单 `APPROVAL_POOL_FILES`。"""
    if not isinstance(record, dict):
        return {"ok": False, "why": "not_dict"}
    _rel = _norm_rel(record.get("file"))
    _hit = _rel in APPROVAL_POOL_FILES
    return {"ok": not _hit, "file": _rel,
            "why": "approval_pool" if _hit else ""}


# ============ 三、综合判定 ============

def judge(record: Any, project_root: str | None = None) -> dict:
    """综合判定：`low` / `medium` / `high` + `eligible`（能否走免签自动应用）。

    分级规则（与任务书对齐）：
      * 碰审批池/状态机（条件③不满足）→ **high**（「改审批池」）
      * 跨器官（条件②不满足）→ **high**（「跨器官」）
      * diff 窄 ∧ 单测全绿 → **low**（唯一允许自动应用的一档）
      * 其余 → **medium**（走人工审批，本模块不干预）

    ★`eligible` 只在 `low` 时为真。medium/high 的行为在 406 前后**完全不变**。
    """
    _kind = diff_kind(record)
    _c1 = check_unit_tests_green(record)
    _c2 = check_single_organ(record)
    _c3 = check_not_approval_pool(record)
    if not _c3["ok"] or not _c2["ok"]:
        _level = "high"
    elif _kind in narrow_kinds() and _c1["ok"]:
        _level = "low"
    else:
        _level = "medium"
    return {
        "schema": SCHEMA,
        "level": _level,
        "diff_kind": _kind,
        "eligible": _level == "low",
        "checks": {
            "unit_tests_green": _c1,
            "single_organ": _c2,
            "not_approval_pool": _c3,
        },
        "why": _explain(_level, _kind, _c1, _c2, _c3),
    }


def _explain(level: str, kind: str, c1: dict, c2: dict, c3: dict) -> str:
    """一句可读的结论（日志/留痕共用，避免两处措辞漂移）。"""
    if not c3["ok"]:
        return "目标属审批池/状态机/审计池 → high"
    if not c2["ok"]:
        return "跨器官（新增对 organs.%s 的引用）→ high" % ",".join(c2.get("crossed") or [])
    if level == "low":
        return "diff=%s ∧ 三条件全绿 → low（可免签）" % kind
    if kind == "unknown":
        return "diff 无法判定（缺 original_code/modified_code 或解析失败）→ medium"
    if kind == "logic":
        return "diff=logic（动了逻辑行）→ medium"
    return "diff=%s 但单测未全绿 → medium" % kind


# ============ 四、落 pending 时打标签（挂进唯一写口） ============

def _ok_of(v: Any) -> bool:
    """`checks` 的两种形态都要吃得下：`judge()` 给 `{ok: bool, ...}`，
    写进 pending 的是压平后的 `bool`。只认 `ok`，不认"非空即真"。"""
    if isinstance(v, dict):
        return v.get("ok") is True
    return v is True


def _brief(entry: Any) -> tuple:
    """判定摘要（用于幂等比较：结论没变就不重写，避免每拍刷 mtime）。"""
    if not isinstance(entry, dict):
        return ("", "", False, ())
    _ck = entry.get("checks") if isinstance(entry.get("checks"), dict) else {}
    return (str(entry.get("level") or ""),
            str(entry.get("diff_kind") or ""),
            entry.get("eligible") is True,
            tuple(_ok_of(_ck.get(_k)) for _k in
                  ("unit_tests_green", "single_organ", "not_approval_pool")))


def annotate_rows(rows: Any, project_root: str | None = None) -> dict:
    """给每条 pending 补 `m406_risk`（**原地**）。返回 `{changed, total, verdicts}`。

    幂等：已有 `m406_risk` 且结论摘要不变 ⇒ 不重写（连 `at` 都不动）。
    ★任何单条异常都只跳过该条，绝不让「打标签」打断「写 pending」主流程。
    """
    if not isinstance(rows, list):
        return {"changed": 0, "total": 0, "verdicts": []}
    _changed = 0
    _verdicts = []
    _now = int(time.time())
    for _r in rows:
        if not isinstance(_r, dict):
            continue
        try:
            _j = judge(_r, project_root=project_root)
        except Exception as _e:
            silent_exc(_e, where="patch_risk_gate.annotate_rows.judge", level="debug")
            continue
        _verdicts.append(_j)
        _new = {
            "schema": SCHEMA,
            "level": _j["level"],
            "diff_kind": _j["diff_kind"],
            "eligible": _j["eligible"],
            "why": _j["why"],
            "checks": {_k: bool((_v or {}).get("ok")) for _k, _v in _j["checks"].items()},
            "at": _now,
        }
        if _brief(_r.get("m406_risk")) == _brief(_new):
            continue
        _r["m406_risk"] = _new
        _changed += 1
    return {"changed": _changed, "total": len(rows), "verdicts": _verdicts}


# ============ 五、留痕（自动应用的每一步都可追溯） ============

def _audit_path(root: str) -> str:
    return os.path.join(root, _MONITOR_REL,
                        "%s%s.jsonl" % (_AUDIT_PREFIX, time.strftime("%Y%m%d")))


def _load_seen(root: str) -> dict:
    _p = os.path.join(root, _SEEN_REL)
    if not os.path.isfile(_p):
        return {}
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, dict) else {}
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="patch_risk_gate._load_seen", level="debug")
        return {}


def _save_seen(root: str, seen: dict) -> None:
    _p = os.path.join(root, _SEEN_REL)
    try:
        _dir = os.path.dirname(_p)
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        if len(seen) > _SEEN_MAX:
            _keep = sorted(seen.items(), key=lambda kv: str(kv[1]))[-_SEEN_MAX:]
            seen = dict(_keep)
        _tmp = _p + ".406.tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(seen, _f, ensure_ascii=False)
        os.replace(_tmp, _p)
    except (OSError, TypeError, ValueError) as _e:
        silent_exc(_e, where="patch_risk_gate._save_seen", level="debug")


def record_audit(rows: Any, *, project_root: str | None = None,
                 writer: str = "") -> dict:
    """把**新出现 / 结论变化**的判定追加进 `data/monitor/patch_risk_audit_YYYYMMDD.jsonl`。

    只写变化 —— 否则每写一次 pending 就会把同一批结论重抄一遍，留痕变成噪音。
    返回 `{written, skipped, path}`；任何失败都返回 0 而不抛（留痕不得打断主流程）。
    """
    if not isinstance(rows, list):
        return {"written": 0, "skipped": 0, "path": ""}
    _root = _project_root(project_root)
    _seen = _load_seen(_root)
    _written = 0
    _skipped = 0
    _lines = []
    _now = time.time()
    for _r in rows:
        if not isinstance(_r, dict):
            continue
        _m = _r.get("m406_risk")
        if not isinstance(_m, dict):
            continue
        _pid = str(_r.get("id") or _r.get("patch_id") or "")
        if not _pid:
            continue
        _sig = "%s|%s|%s|%s" % (_m.get("level"), _m.get("diff_kind"),
                                int(bool(_m.get("eligible"))), _m.get("at"))
        if _seen.get(_pid) == _sig:
            _skipped += 1
            continue
        _seen[_pid] = _sig
        _lines.append({
            "ts": round(_now, 3),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(_now)),
            "patch_id": _pid,
            "file": _norm_rel(_r.get("file")),
            "method": str(_r.get("method") or ""),
            "source": str(_r.get("source") or ""),
            "level": _m.get("level"),
            "diff_kind": _m.get("diff_kind"),
            "eligible": bool(_m.get("eligible")),
            "why": _m.get("why"),
            "checks": _m.get("checks"),
            "writer": writer,
        })
        _written += 1
    _path = _audit_path(_root)
    if _lines:
        try:
            _dir = os.path.dirname(_path)
            if _dir and not os.path.isdir(_dir):
                os.makedirs(_dir, exist_ok=True)
            with open(_path, "a", encoding="utf-8") as _f:
                for _ln in _lines:
                    _f.write(json.dumps(_ln, ensure_ascii=False) + "\n")
        except (OSError, TypeError) as _e:
            silent_exc(_e, where="patch_risk_gate.record_audit 追加", level="warning")
            return {"written": 0, "skipped": _skipped, "path": _path}
        _save_seen(_root, _seen)
    return {"written": _written, "skipped": _skipped, "path": _path}


# ============ 六、给审批通路用的窄闸 ============

def gate_allows(record: Any, project_root: str | None = None) -> tuple:
    """免签通路的**再收一道窄闸**：返回 `(allowed, why)`。

    * 开关 `config.PATCH_M406_NARROW_GATE` 为 False ⇒ 恒 `(True, "gate_off")`，完全回退 406 前行为；
    * 开关为 True ⇒ 只有 `judge()["eligible"]`（即 low）才放行，其余一律拦。

    ★只收窄、不放宽：它加在原有判据**之后**，原本被拒的补丁不会因为本闸而变成放行。
    """
    try:
        import config as _cfg
        _on = bool(getattr(_cfg, "PATCH_M406_NARROW_GATE", True))
    except Exception as _e:
        # 读不到开关 ⇒ 按**默认（启用）**继续判定，而不是直接放行 ——
        # 否则一次 import 抖动就把整道闸静默关掉。
        silent_exc(_e, where="patch_risk_gate.gate_allows 读开关", level="debug")
        _on = True
    if not _on:
        return True, "gate_off"
    try:
        _j = judge(record, project_root=project_root)
    except Exception as _e:
        silent_exc(_e, where="patch_risk_gate.gate_allows judge", level="debug")
        return False, "judge_failed"
    if _j.get("eligible"):
        return True, "low:%s" % _j.get("diff_kind")
    return False, "%s:%s" % (_j.get("level"), _j.get("diff_kind"))


def gate_status() -> dict:
    """诊断口：当前闸的开关与清单规模（供自省/报告读，不参与判定）。"""
    try:
        import config as _cfg
        _on = bool(getattr(_cfg, "PATCH_M406_NARROW_GATE", True))
    except Exception as _e:
        silent_exc(_e, where="patch_risk_gate.gate_status 读开关", level="debug")
        _on = True
    return {"schema": SCHEMA, "gate_on": _on,
            "narrow_kinds": list(narrow_kinds()),
            "delete_only_enabled": _delete_only_enabled(),
            "approval_pool_files": len(APPROVAL_POOL_FILES)}
