# -*- coding: utf-8 -*-
"""★349刀1（P0 · 裁决②）：本地补丁**生成器**（烛微349审计1 方案A）。

## 为什么单独成模块

`PatchManager` 是**补丁生命周期的账房**（入队/审批/应用/回滚），它不该知道
"代码是怎么被想出来的"。生成器是**另一个职责**：把一条静态审计问题
（`file/method/desc/severity/klass`）变成一份**待审草稿**。混在一起会让
"生成质量"与"账房纪律"互相掩盖——349 之前池里 86 条候选零 applied 就是这个味道：
没人能回答"这条草稿是谁、凭什么生成的"。

## 三条纪律（本模块立身之本）

1. **三态可区分，不静默**（铁律166）：
     · `generation_failed` —— 模型层失败（连不上/超时/回复不可解析）
     · `gate_rejected`     —— 证据门或审核链标准拦下（**模型没错，是产物不合格**）
     · `draft_ready`       —— 六字段齐、AST 过、diff 最小、不触上帝文件
   "模型没回"与"回了但不合格"必须能分开统计，否则合格率是假的。

2. **草稿只入审批分道，不进 O3 判死池**（裁决② + 348刀10）：
   草稿一律 `requires_confirmation=True` + `o3_dead_code` 不置真 +
   `source="patch_generator"`（不是 `o3` / `o3_open_gate`）⇒
   `_m339_is_o3` 判否 ⇒ 不会被派单链认领。

3. **路径必须可注入**（铁律158/189）：`project_root`、`out_dir`、`transport`
   全部显式参数，None 才回退默认。隔离验证不许碰生产 `data/`。

## 与既有证据门的关系

`patch_execution_dispatch._m338_evidence_ok` 管的是**派单侧三要素**
（risk/trust/blast 可复算）。本模块管的是**生成侧六字段**
（file/method/original_code/modified_code/reason/dedup_key）。
348 已经铺了前四个（`_m90_patch_field_contract` + `audit_problem_pool_consumer`），
本批补的是 `reason` 与 `dedup_key` —— 这两个恰好是"凭什么"和"是不是同一条"。
两层各管一段，**不合并**：合并会再造一个"生成侧填了、派单侧不认"的分叉。
"""
from __future__ import annotations

import ast
import hashlib
import re
import textwrap
import io
import json
import os
import time
from typing import Any, Callable

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PatchGenerator")

#: ★352刀3：模板档识别 `except <exc> as <var>:`。只匹配“有 as”的形态（裸 except
#   没有异常对象可传，猜一个就不确定了）。
_RE_EXCEPT_AS_PASS = re.compile(
    r"^(?P<ind>[ \t]*)except\s+(?P<exc>[A-Za-z_][\w.\t ,()]*)\s+as\s+(?P<var>[A-Za-z_]\w*)\s*:\s*$")

#: 生成器版本（写进每份草稿，便于按版本统计合格率；改 prompt/解析规则必须升版）
#: ★352刀6：349.d1.v1 → 352.d6.v1。升版理由：提示模板从“锚点三元组”改为
#:   “整方法四步”（与 `METHOD_LEVEL_CONTRACT=True` 对齐），合格率不可跨版本直接比。
GENERATOR_VERSION = "352.d6.v1"

#: ★352刀6：提示模板版本（独立于生成器版本——同一版生成器可能试多套模板）
PROMPT_TEMPLATE_VERSION = "352.d6.v1"

# ---- ★三态（生成侧唯一口径）----
STATE_DRAFT_READY = "draft_ready"
STATE_GATE_REJECTED = "gate_rejected"
STATE_GENERATION_FAILED = "generation_failed"
#: ★352刀1：第四态——**目标漂移**。
#   为什么不能并进 generation_failed：两者是**不同事实**（铁律166）。
#     generation_failed = 模型/传输/解析没给出可用产物（重试或换模型有意义）；
#     target_drifted   = 要改的东西在 HEAD 上已不存在/改名（重试一百次也没用，
#                        必须重新定位或剔除）。
#   混为一谈的代价：69.2% 的无效生成被记成“模型不行”——烛微352 诊断的正是这一条。
STATE_TARGET_DRIFTED = "target_drifted"
#: ★352刀3：第五态——**转人工**。
#   与 generation_failed 不同（铁律166）：failed 是“这次没成功，可以重试”；
#   manual_required 是“**不应该让模型生成**，必须人写”——重试、换模型、加强 prompt
#   都是错的方向。把两者混一谈，这批条目会永远在重试队列里转。
STATE_MANUAL_REQUIRED = "manual_required"

STATES = (STATE_DRAFT_READY, STATE_GATE_REJECTED, STATE_GENERATION_FAILED,
          STATE_TARGET_DRIFTED, STATE_MANUAL_REQUIRED)

#: ★349刀1②：证据门**六字段**（348 已铺前四个，本批补 reason / dedup_key）
REQUIRED_PATCH_FIELDS = ("file", "method", "original_code", "modified_code",
                         "reason", "dedup_key")

#: ★349刀1③：生成器草稿池（**独立于** `pending_patches.json`）。
#   为什么不并进主队列：主队列的入队口有「modified_code 首个 def 名 == method」硬约束，
#   而草稿是行级片段（锚点替换 1-3 行），天然不含 def ⇒ 会被静默拒收。
#   两条通道的补丁形态不同，硬塞只会让 def 对齐形同虚设。
DRAFTS_REL = os.path.join("data", "patches", "generator_drafts.json")

#: 本地自托管模型（config.EVOLUTION_LLM_PRICE_TABLE 中单价 0.0 ⇒ 零成本）
DEFAULT_MODEL = "qwen2:7b-instruct-q4_K_M"
DEFAULT_TIMEOUT_SEC = 60.0
DEFAULT_BASE_URL = "http://localhost:11434"

#: 送给模型的源码片段上限（行数）；超出只取方法头若干行 + 尾部
_SRC_MAX_LINES = 90
#: desc 截断（问题描述可能很长，模型上下文吃不下）
_DESC_MAX_CHARS = 1200


# ========== 配置 ==========


def _m349_cfg(name: str, default: Any) -> Any:
    """读配置（读不到回退默认，不抛）。

    ★本函数是**观测/生成**用途，不是门禁：回退默认不会改变任何放行语义。
    """
    try:
        import config as _cfg

        return getattr(_cfg, name, default)
    except Exception as _e:
        silent_exc(_e, where="patch_generator::_m349_cfg %s" % name)
        return default


def generator_enabled() -> bool:
    """生成器总开关（默认**关**）。

    为什么默认关：生成器会真的拉起本地 7B 模型（单次几秒到几十秒）。
    默认开等于给主循环加一个未知耗时的旁路——349 只交付**能力**，
    开闸由重启窗与压测决定（与 shadow→live 同纪律）。
    """
    return bool(_m349_cfg("ENABLE_PATCH_GENERATOR", False))


# ========== 身份键（与池同源，不重造）==========


def make_dedup_key(file: str, method: str, issue_type: str) -> str:
    """★349刀1②：`dedup_key = sha1(file|method|issue_type)[:16]`（**弃行号**）。

    为什么把池里的算法再抄一遍而不是 import：`audit_problem_pool` 是 LF 的
    池模块，import 它会把整条池依赖（含 pyarrow 邻域）拉进生成器。
    这里用**逐字同构**的实现，并在下面 `dedup_key_matches_pool()` 里
    与池正本对拍——口径一致性由**测试**保证，不靠"同一份代码"这种口头承诺。
    """
    _norm = str(file or "").replace("\\", "/").strip()
    _raw = "%s|%s|%s" % (_norm, str(method or "").strip(),
                         str(issue_type or "").strip())
    return hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]


def dedup_key_matches_pool(file: str, method: str, issue_type: str) -> bool:
    """与池正本 `audit_problem_pool.make_dedup_key` 对拍（不一致 ⇒ False）。"""
    try:
        from nucleus.audit_problem_pool import make_dedup_key as _ref

        return _ref(file, method, issue_type) == make_dedup_key(file, method, issue_type)
    except Exception as _e:
        silent_exc(_e, where="patch_generator::dedup_key_matches_pool")
        return False


# ========== ★352刀1：生成前目标存在性预检 ==========
#   烛微352 实测：40 条 pending 里目标漂移 27 条（69.2%），主根因是“生成器缺生成前预检”。
#   另一半原因：原有 `extract_source_snippet` 虽然也会 file_not_found，
#   但它归 `generation_failed`（词不达意），**且不回写池状态**，
#   于是同一条漂移条目每一拍都重新失败一次。预检把这两件
#   事都收了：给**独立判词**，且能被池側用来置 target_drifted。

PRECHECK_SCHEMA = "target_precheck/352.v1"

TARGET_IN_PLACE = "in_place"
TARGET_FILE_ABSENT = "file_absent"
TARGET_METHOD_ABSENT = "method_absent"
TARGET_PARSE_FAILED = "parse_failed"
TARGET_UNREADABLE = "unreadable"

TARGET_VERDICTS = (TARGET_IN_PLACE, TARGET_FILE_ABSENT, TARGET_METHOD_ABSENT,
                   TARGET_PARSE_FAILED, TARGET_UNREADABLE)

#: 视为「漂移」（不该进生成/判死池）的判词。
#   ★只收 file_absent 与 method_absent。parse_failed / unreadable 是**暂时不可判**，
#   不能借“漂移”把它们一起剔掉（那会把真问题连同一个不可读文件一起打掉）
TARGET_DRIFTED_VERDICTS = (TARGET_FILE_ABSENT, TARGET_METHOD_ABSENT)

#: 预检缓存：key=(abs_path, mtime_ns, size) ⇒ 索引。
#   为什么缓存：40 条存量里 `PulseLegs.py` 占 6 条、`PulseHormones.py` 占 4 条，
#   同一文件反复 AST 解析在心跳里是白费的开销（铁律208：批量一次遍历）。
_PRECHECK_CACHE: dict = {}
_PRECHECK_CACHE_MAX = 512


def _precheck_index(file_abs: str) -> tuple:
    """返回 `(index, state)`；index = {module:set, by_class:dict, n_class:int}。"""
    try:
        _st = os.stat(file_abs)
    except OSError as _e:
        silent_exc(_e, where="patch_generator::_precheck_index stat %s" % file_abs)
        return None, "%s: %s" % (type(_e).__name__, _e)
    _key = (os.path.abspath(file_abs), int(_st.st_mtime_ns), int(_st.st_size))
    _hit = _PRECHECK_CACHE.get(_key)
    if _hit is not None:
        return _hit
    try:
        with open(file_abs, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _tree = ast.parse(_src)
    except SyntaxError as _se:
        silent_exc(_se, where="patch_generator::_precheck_index parse %s" % file_abs)
        _res = (None, "SyntaxError: %s" % _se)
    except OSError as _oe:
        silent_exc(_oe, where="patch_generator::_precheck_index read %s" % file_abs)
        _res = (None, "%s: %s" % (type(_oe).__name__, _oe))
    else:
        _mod = set()
        _by_class: dict = {}
        _n_cls = 0
        for _node in _tree.body:
            if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _mod.add(_node.name)
            elif isinstance(_node, ast.ClassDef):
                _n_cls += 1
                for _sub in _node.body:
                    if isinstance(_sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        _by_class.setdefault(_sub.name, []).append(_node.name)
        _res = ({"module": _mod, "by_class": _by_class, "n_class": _n_cls}, "ok")
    if len(_PRECHECK_CACHE) >= _PRECHECK_CACHE_MAX:
        _PRECHECK_CACHE.clear()
    _PRECHECK_CACHE[_key] = _res
    return _res


def precheck_target(project_root: str | None, file: str, method: str) -> dict:
    """对**当前工作副本**校验「file 存在 ∧ method 有 def」。只读。

    为什么用 AST 不用子串搜索：实测发现文本搜 `def __init__(` 在 1500 行的
    器官文件里**必命中**（任何类都有 `__init__`），会把真漂移报成在位。
    按语义单元（模块级 def / 类内 def）判才对得上账（铁律162）。

    返回：`{schema, verdict, file, method, why, cls, module_level, ambiguous, n_class}`。
    `cls` 列出命中的类；多个类同名 ⇒ `ambiguous=True`（**仍计在位**，
    但如实标出，不假装确定性）。
    """
    _rel = _m349_norm_rel(file)
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _out = {
        "schema": PRECHECK_SCHEMA,
        "file": _rel,
        "method": str(method or "").strip(),
        "verdict": TARGET_METHOD_ABSENT,
        "why": "",
        "cls": [],
        "module_level": False,
        "ambiguous": False,
        "n_class": 0,
    }
    if not _rel:
        _out["verdict"] = TARGET_FILE_ABSENT
        _out["why"] = "empty_file_field"
        return _out
    if not _out["method"]:
        # 没方法名 ⇒ 无法判“方法在不在”；这属于**条目声明不完整**，不当漂移
        _out["verdict"] = TARGET_METHOD_ABSENT
        _out["why"] = "empty_method_field"
        return _out
    _p = os.path.join(str(_root), _rel)
    if not os.path.isfile(_p):
        _out["verdict"] = TARGET_FILE_ABSENT
        _out["why"] = "file_not_found"
        return _out
    _idx, _state = _precheck_index(_p)
    if _idx is None:
        _out["verdict"] = (TARGET_UNREADABLE if _state.startswith(("OSError", "FileNotFound"))
                           else TARGET_PARSE_FAILED)
        _out["why"] = _state[:200]
        return _out
    _out["n_class"] = _idx["n_class"]
    if _out["method"] in _idx["module"]:
        _out["verdict"] = TARGET_IN_PLACE
        _out["module_level"] = True
        _out["why"] = "module_level_def"
        return _out
    _cls = list(_idx["by_class"].get(_out["method"], []))
    if _cls:
        _out["verdict"] = TARGET_IN_PLACE
        _out["cls"] = _cls
        _out["ambiguous"] = len(_cls) > 1
        _out["why"] = "class_def:%s%s" % (
            ",".join(_cls), "(同名 %d 类)" % len(_cls) if len(_cls) > 1 else "")
        return _out
    _out["verdict"] = TARGET_METHOD_ABSENT
    _out["why"] = "no_def_in_%d_classes_or_module" % _idx["n_class"]
    return _out


def precheck_target_v2(project_root: str | None, file: str, method: str,
                       method_before: str = "", original_code: str = "") -> dict:
    """★354刀6修正（星轨 v1.1）：三锚或判的目标存在性预检。

    为什么不能只用 field 名：pending 40 条里 **35 条**的 `method` 字段是
    **生成器猜的名**，与代码里的真实方法名对不上；`m323_truth.method_before`
    才是补丁真正改的那个方法。拿 field 名去 AST 查 ⇒ 查不到 ⇒ 被判"目标漂移"
    ⇒ 35 条真实存在的补丁被误杀。这就是 352 审计"27/39 漂移"的真相：
    **是名字漂移，不是位置漂移**（星轨 pending 深析，39/39 用真实名全部命中）。

    三锚（任一命中即"存在"）：
      A. field 名 AST 命中           —— 名对得上的老条目
      B. `method_before` AST 命中    —— 真实方法名（主锚）
      C. `original_code` 归一化全文匹配 —— 前两锚都查不到时的兜底（改名/跨类）

    Returns:
        同 `precheck_target` 的结构，另加 `anchor`（field/method_before/
        original_code/none）与 `anchors_tried`（逐个锚的结果，便于追溯）。
    """
    import re as _re

    def _norm(_s: str) -> str:
        return _re.sub(r"\s+", "", str(_s or ""))

    _tried: list = []
    _a = precheck_target(project_root, file, method)
    _tried.append({"anchor": "field", "method": method, "verdict": _a["verdict"]})
    if _a["verdict"] == TARGET_IN_PLACE:
        _a["anchor"] = "field"
        _a["anchors_tried"] = _tried
        return _a

    _mb = str(method_before or "").strip()
    if _mb and _mb != str(method or "").strip():
        _b = precheck_target(project_root, file, _mb)
        _tried.append({"anchor": "method_before", "method": _mb, "verdict": _b["verdict"]})
        if _b["verdict"] == TARGET_IN_PLACE:
            _b["anchor"] = "method_before"
            _b["field_method"] = method
            _b["anchors_tried"] = _tried
            return _b

    _oc = str(original_code or "").strip()
    if _oc:
        _root = project_root or os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        _rel = _m349_norm_rel(file)
        _src = ""
        try:
            with io.open(os.path.join(str(_root), _rel), encoding="utf-8",
                         errors="ignore") as _f:
                _src = _f.read()
        except Exception as _oe:
            silent_exc(_oe, where="patch_generator::precheck_target_v2 read_source")
            _src = ""
        _hit = bool(_src) and _norm(_oc) and (_norm(_oc) in _norm(_src))
        _tried.append({"anchor": "original_code", "method": "-",
                       "verdict": TARGET_IN_PLACE if _hit else TARGET_METHOD_ABSENT})
        if _hit:
            return {"schema": PRECHECK_SCHEMA + "/v2", "file": _rel,
                    "method": _mb or str(method or "").strip(),
                    "verdict": TARGET_IN_PLACE, "why": "original_code_norm_match",
                    "cls": [], "module_level": False, "ambiguous": False,
                    "n_class": 0, "anchor": "original_code",
                    "field_method": method, "anchors_tried": _tried}

    _a["anchor"] = "none"
    _a["anchors_tried"] = _tried
    _a["why"] = "three_anchors_all_missed:%s" % ";".join(
        "%s=%s" % (t["anchor"], t["verdict"]) for t in _tried)
    return _a


#: ★354刀6增量：方法体完整性判定线（星轨 2026-10-24 建议）
BODY_INTEGRITY_MIN_RATIO = 0.85


def check_body_integrity(project_root: str | None, file: str, method: str,
                         modified_code: str,
                         min_ratio: float = BODY_INTEGRITY_MIN_RATIO) -> dict:
    """方法体完整性：改后行数是否撑得住原方法体（识别模型输出截断）。

    Args:
        modified_code: 模型给出的**完整替换方法**（含 def 行）。
        min_ratio: 判定线，默认 0.85（星轨建议值）。

    Returns:
        {verdict: "ok"|"truncated"|"undecidable"|"empty_output",
         orig_lines, new_lines, ratio, min_ratio, why}
        ★`undecidable` 与 `ok` 必须分得开（铁律166）：取不到原方法体时
         **不得**算作合格，也不得算作截断 —— 那是"判据不可用"。
    """
    import ast as _ast

    def _orig_lines() -> int | None:
        _root = project_root or os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        _rel = _m349_norm_rel(file)
        if not _rel:
            return None
        try:
            with io.open(os.path.join(str(_root), _rel), encoding="utf-8",
                         errors="ignore") as _f:
                _src = _f.read()
            _tree = _ast.parse(_src)
        except Exception as _pe:
            silent_exc(_pe, where="patch_generator::check_body_integrity parse")
            return None
        for _n in _ast.walk(_tree):
            if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                    and _n.name == str(method or "").strip():
                return int(getattr(_n, "end_lineno", _n.lineno) or _n.lineno) \
                    - int(_n.lineno) + 1
        return None

    _new = len([_l for _l in str(modified_code or "").splitlines() if _l.strip()])
    if _new == 0:
        return {"verdict": "empty_output", "orig_lines": None, "new_lines": 0,
                "ratio": 0.0, "min_ratio": min_ratio,
                "why": "modified_code 为空 ⇒ 无产物可判完整性"}
    _ol = _orig_lines()
    if not _ol:
        return {"verdict": "undecidable", "orig_lines": None, "new_lines": _new,
                "ratio": None, "min_ratio": min_ratio,
                "why": "取不到原方法体行数（文件/方法不可读）⇒ 判据不可用，不判不合格"}
    _ratio = _new / float(_ol)
    _ok = _ratio >= min_ratio
    return {"verdict": "ok" if _ok else "truncated",
            "orig_lines": _ol, "new_lines": _new,
            "ratio": round(_ratio, 3), "min_ratio": min_ratio,
            "why": ("改后 %d 行 / 原方法体 %d 行 = %.2f %s %.2f"
                    % (_new, _ol, _ratio, ">=" if _ok else "<", min_ratio))}


#: ★355刀2：尾部覆盖判定线（任务书刀2 明定：保留 ≥80%）
TAIL_COVERAGE_MIN = 0.80
#: 取原方法体尾部多少条**非空语句**做覆盖比对
TAIL_SAMPLE_LINES = 10


def check_tail_coverage(project_root: str | None, file: str, method: str,
                        modified_code: str,
                        min_ratio: float = TAIL_COVERAGE_MIN,
                        sample_lines: int = TAIL_SAMPLE_LINES) -> dict:
    """尾部覆盖判据：原方法体**尾部 N 条非空语句**是否还留在改后方法体里。

    为什么必须补这条：行数比只能抓"整体变短"，抓不到"**长度够但尾段被改写/丢失**"。
    实证 94e9（PulseInnerWorld._run_periodic_tasks）：原 91 行 → 改后 96 行，
    行数比 **1.05 过关**，但尾止于 tempo 分支 ⇒ **诊断建议发射在尾部丢失**
    —— 行数比完全看不见这种截断。

    Returns:
        {verdict: "ok"|"truncated"|"undecidable",
         tail_total, tail_hit, ratio, min_ratio, missing}
        `missing` 列出**没被引进来的尾行**（可追溯到"丢了哪句"）。
        `undecidable` = 取不到原方法体（不判不合格，铁律166）。
    """
    import ast as _ast

    def _orig_body() -> str | None:
        _root = project_root or os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        _rel = _m349_norm_rel(file)
        if not _rel:
            return None
        try:
            with io.open(os.path.join(str(_root), _rel), encoding="utf-8",
                         errors="ignore") as _f:
                _lines = _f.read().split("\n")
            _src = "\n".join(_lines)
            _tree = _ast.parse(_src)
        except Exception as _pe:
            silent_exc(_pe, where="patch_generator::check_tail_coverage parse")
            return None
        for _n in _ast.walk(_tree):
            if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                    and _n.name == str(method or "").strip():
                _a = int(_n.lineno)
                _b = int(getattr(_n, "end_lineno", _n.lineno) or _n.lineno)
                # 去掉装饰器行：从 def 所在行开始
                return "\n".join(_lines[max(0, _a - 1):_b])
        return None

    def _norm(_s: str) -> str:
        import re as _re
        return _re.sub(r"\s+", "", str(_s or ""))

    _ob = _orig_body()
    if not _ob:
        return {"verdict": "undecidable", "tail_total": 0, "tail_hit": 0,
                "ratio": None, "min_ratio": min_ratio, "missing": [],
                "why": "取不到原方法体 ⇒ 判据不可用，不判不合格"}
    _tail = [_l for _l in _ob.split("\n") if _l.strip()][-int(sample_lines):]
    _new_n = _norm(modified_code)
    _hit, _missing = 0, []
    for _l in _tail:
        if _norm(_l) and _norm(_l) in _new_n:
            _hit += 1
        else:
            _missing.append(_l.strip()[:80])
    _ratio = _hit / float(len(_tail)) if _tail else 0.0
    return {"verdict": "ok" if _ratio >= min_ratio else "truncated",
            "tail_total": len(_tail), "tail_hit": _hit,
            "ratio": round(_ratio, 3), "min_ratio": min_ratio,
            "missing": _missing,
            "why": "尾部%d条命中%d条=%.2f %s %.2f"
                   % (len(_tail), _hit, _ratio,
                      ">=" if _ratio >= min_ratio else "<", min_ratio)}


def _m355_normalize_method(entry: dict) -> dict:
    """★355刀2①：把条目的 `method` 归一为**真实方法名**（`m323_truth.method_before`）。

    m323_truth 39/39 `action=method_backfilled`：def_name（=field 名）≠ method_before，
    生成器拿 field 名去猜 ⇒ 31/39 把 def 名改成了猜的名字，应用即"改错方法"。
    归一后：生成/契约/应用**全部锚真实名**，rompt 里也不会再出现猜的名。

    Returns:
        归一后的条目（原地修改并返回），另写 `method_field_original` 保留原名，
        便于追溯"这条原本叫什么"。
    """
    if not isinstance(entry, dict):
        return entry
    _truth = entry.get("m323_truth") or {}
    _real = str(_truth.get("method_before") or "").strip()
    _cur = str(entry.get("method") or "").strip()
    if _real and _real != _cur:
        entry["method_field_original"] = _cur
        entry["method"] = _real
    return entry


def precheck_batch(project_root: str | None, entries: list) -> dict:
    """批量预检（一次遍历，铁律208）。返回 `{schema, total, tally, in_place,
    drifted, other, rows}`。用于 40 条存量对账与池侧批量标记。
    """
    _rows = []
    _tally: dict = {}
    _anchor_tally: dict = {}
    for _e in (entries or []):
        if not isinstance(_e, dict):
            continue
        # ★354刀6修正：O3 破零观察位（无 m323_truth 且无 original_code，如
        #   o3_b31b31fee790d6e9）属**形态预期**，星轨明写不参与预检/去重，单独标记
        #   ⇒ 跳过并计入 excluded_o3，不进 rejected 分母（否则会假报 1 条拒收）。
        if not (_e.get("m323_truth") or {}).get("method_before")                 and not str(_e.get("original_code") or "").strip():
            _anchor_tally["excluded_o3"] = _anchor_tally.get("excluded_o3", 0) + 1
            _rows.append({"id": str(_e.get("id") or ""), "file": _e.get("file"),
                          "method": _e.get("method"), "verdict": "excluded_o3",
                          "why": "o3_observation_slot", "cls": [],
                          "ambiguous": False, "anchor": "excluded_o3",
                          "field_method": _e.get("method")})
            continue
        # ★354刀6修正：改用三锚或判（field ∨ method_before ∨ original_code）
        _mb = str((_e.get("m323_truth") or {}).get("method_before") or "").strip()
        _pc = precheck_target_v2(project_root, _e.get("file"), _e.get("method"),
                                 method_before=_mb,
                                 original_code=_e.get("original_code") or "")
        _tally[_pc["verdict"]] = _tally.get(_pc["verdict"], 0) + 1
        _anchor = str(_pc.get("anchor") or "none")
        _anchor_tally[_anchor] = _anchor_tally.get(_anchor, 0) + 1
        _rows.append({
            "id": str(_e.get("dedup_key") or _e.get("id") or ""),
            "file": _pc["file"],
            "method": _pc["method"],
            "verdict": _pc["verdict"],
            "why": _pc["why"],
            "cls": _pc["cls"],
            "ambiguous": _pc["ambiguous"],
            "anchor": _anchor,
            "field_method": _e.get("method"),
        })
    _in = _tally.get(TARGET_IN_PLACE, 0)
    _dr = sum(_tally.get(_v, 0) for _v in TARGET_DRIFTED_VERDICTS)
    # ★354刀6：批量汇总留痕。单条预检的 [352预检] 在 generate_patch_draft 里已有，
    #   但**批量对账口径**（40 条存量 drifting 多少）此前无任何留痕 ⇒ 每批都要重算一遍
    #   才能回答"拦截了多少"。这里一次遍历顺带打，不重复实现预检逻辑。
    _module_logger.info(
        "[354预检] rejected=%d/total=%d in_place=%d 锚点=%s 归因=%s",
        _dr, len(_rows), _in, dict(_anchor_tally), dict(_tally))
    return {
        "schema": PRECHECK_SCHEMA + "/batch/v2",
        "anchor_tally": dict(_anchor_tally),
        "total": len(_rows),
        "tally": _tally,
        "in_place": _in,
        "drifted": _dr,
        "other": len(_rows) - _in - _dr,
        "rate_drifted": (round(_dr / len(_rows), 4) if _rows else None),
        "rows": _rows,
    }


# ========== ★352刀3：分层生成路由 ==========
#   烛微352：简单=规则模板（零模型零误报）｜中等=qwen2:7b 整方法改写｜复杂=人工。
#   为什么要分：现状是**不管难易全扛给 7B 小模型**，而 7B 在跨方法/跨文件上
#   几乎必错（铁律203：60s 只能出 ≈350 tokens，契约只能锚三元组）。

LANE_TEMPLATE = "template"
LANE_LOCAL_MODEL = "local_model"
LANE_MANUAL = "manual"
LANE_SCHEMA = "gen_lane/352.v1"
LANES = (LANE_TEMPLATE, LANE_LOCAL_MODEL, LANE_MANUAL)

#: 模板档只接**能确定性枚举**的 issue_type。
#   ★收得越窄越好：每多一类就多一分“模板以为自己会写”的风险。
TEMPLATE_ISSUE_TYPES = ("silent_exception",)

#: 复杂档：跨文件/跨模块/语义重——机器不该猜
MANUAL_ISSUE_TYPES = ("cross_module_singleton_call", "cross_file_refactor",
                      "multi_file_change", "architecture")

#: 上帝文件行数阈值（超过则改动影响面难以由小模型把握）
GOD_FILE_LINES = 3000


def route_generation_lane(entry: dict, project_root: str | None = None) -> dict:
    """判定一条池条目应走哪个生成档。只读、确定性。

    返回：`{schema, lane, score, reasons, signals}`。
      · `score` 是“复杂度”分（越高越复杂），`reasons` 逐条列出为何加分——
        留这个是为了事后能回答“它当初为什么没走模板”，而不是只报一个档位。
      · `signals` 是原始阈值读数（行数/是否核心/是否判死）。
    """
    _e = entry if isinstance(entry, dict) else {}
    _it = str(_e.get("issue_type") or "")
    _f = _m349_norm_rel(_e.get("file"))
    _o: dict = {"schema": LANE_SCHEMA, "lane": LANE_LOCAL_MODEL, "score": 0,
                "reasons": [], "signals": {}}
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _p = os.path.join(str(_root), _f) if _f else ""
    _nlines = 0
    if _p and os.path.isfile(_p):
        try:
            with open(_p, encoding="utf-8", errors="replace") as _fh:
                _nlines = sum(1 for _ in _fh)
        except OSError as _oe:
            silent_exc(_oe, where="patch_generator::route_generation_lane lines")
    _core = _m349_is_core_file(_f) if _f else True
    _o["signals"] = {"issue_type": _it, "file": _f, "file_lines": _nlines,
                     "is_core": bool(_core),
                     "o3_dead_code": bool(_e.get("o3_dead_code"))}
    # —— 复杂信号（只升档不降档）——
    if _e.get("o3_dead_code"):
        _o["score"] += 4
        _o["reasons"].append("判死候选(o3_dead_code)⇒走AST删除路径，不应走生成器")
    if _it in MANUAL_ISSUE_TYPES:
        _o["score"] += 4
        _o["reasons"].append("issue_type=%s 属跨文件/语义重族" % _it)
    if _core:
        _o["score"] += 3
        _o["reasons"].append("核心文件（改错代价高）")
    if _nlines >= GOD_FILE_LINES:
        _o["score"] += 2
        _o["reasons"].append("上帝文件 %d 行 ≥ %d" % (_nlines, GOD_FILE_LINES))
    # —— 简单信号（仅当无复杂信号时才能进模板档）——
    _simple = (_it in TEMPLATE_ISSUE_TYPES and not _e.get("o3_dead_code")
               and _nlines and _nlines < GOD_FILE_LINES)
    if _o["score"] >= 3:
        _o["lane"] = LANE_MANUAL
    elif _simple:
        _o["lane"] = LANE_TEMPLATE
        _o["reasons"].append("issue_type=%s 在模板可枚举集合内" % _it)
    else:
        _o["lane"] = LANE_LOCAL_MODEL
        if _it not in TEMPLATE_ISSUE_TYPES:
            _o["reasons"].append("issue_type=%s 不在模板可枚举集合内" % _it)
        if not _nlines:
            _o["reasons"].append("取不到行数（文件缺失）")
    return _o


# ── 模板档实现：只做**能确定性枚举**的改写 ──

def _tpl_file_imports(file_path: str, symbol: str) -> bool:
    """目标文件是否已导入某符号（模板产物要用它，没导入就不能改）。"""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
    except OSError as _e:
        silent_exc(_e, where="patch_generator::_tpl_file_imports %s" % file_path)
        return False
    for _line in _src.split("\n"):
        _s = _line.strip()
        if _s.startswith(("import ", "from ")) and symbol in _s:
            return True
    return False


def _tpl_silent_exception(project_root: str, entry: dict) -> dict:
    """模板：`except X as e: pass` ⇒ `silent_exc(e, where="file::method")`。

    三道确定性前提（少一条就**不生成**，降级给模型）：
      ① 目标文件已导入 `silent_exc`（否则模板产物会 NameError）；
      ② 方法体内存在 `except ... as <var>:` 紧跟 `pass` 的块；
      ③ 该块是**单独一个 pass**（多语句时不拆，拆了就是改行为）。
    裸 `except:`（无 as）不接——没有异常对象可传，猜一个就是不确定。
    """
    _rel = _m349_norm_rel(entry.get("file"))
    _meth = str(entry.get("method") or "").strip()
    _p = os.path.join(str(project_root), _rel)
    _base = {"ok": False, "data": None, "why": ""}
    if not _tpl_file_imports(_p, "silent_exc"):
        _base["why"] = "no_silent_exc_import"
        return _base
    _span = _m349_method_span(project_root, _rel, _meth)
    if not _span.get("ok"):
        _base["why"] = "span:%s" % _span.get("why")
        return _base
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _lines = _f.read().replace("\r\n", "\n").split("\n")
    except OSError as _e:
        silent_exc(_e, where="patch_generator::_tpl_silent_exception read")
        _base["why"] = "read_error"
        return _base
    _orig = "\n".join(_lines[_span["start"]:_span["end"]])
    _new = []
    _hits = 0
    _var_name = "e"
    _i = _span["start"]
    _rx = _RE_EXCEPT_AS_PASS
    while _i < _span["end"]:
        _cur = _lines[_i]
        _m = _rx.match(_cur)
        if _m and _i + 1 < _span["end"]:
            _nxt = _lines[_i + 1]
            if _nxt.strip() == "pass" and (
                    len(_nxt) - len(_nxt.lstrip())) > (len(_cur) - len(_cur.lstrip())):
                _ind = _m.group("ind")
                _var = _m.group("var")
                _new.append("%sexcept %s as %s:" % (_ind, _m.group("exc"), _var)
                            if _m.group("exc") else "%sexcept:" % _ind)
                _new.append('%s    silent_exc(%s, where="%s::%s")  '
                            '# 352模板档：静默异常留痕'
                            % (_ind, _var, _rel, _meth))
                _var_name = _var
                _hits += 1
                _i += 2
                continue
        _new.append(_cur)
        _i += 1
    if _hits != 1:
        _base["why"] = "except_pass_hits=%d（需恰好 1）" % _hits
        return _base
    _mod = "\n".join(_new)
    if _mod == _orig:
        _base["why"] = "no_change"
        return _base
    # ★352刀3：reason 必须过「三段式」证据链门（触发行/现象/依据）——
    #   模板档不能因为“是自动生成的”就不交代凭什么改（否则证据门对
    #   模板档形同虚设）。三段内容都是**从源码实测得来**，不是套话。
    _reason = ("触发行：%s 的 except 块（第 %d 行起）只有 pass。"
               "现象：异常被静默吞掉，运行时无任何留痕。"
               "依据：本文件已导入 silent_exc，按项目约定改为 "
               "silent_exc(%s, where=\042%s::%s\042)，不改异常类与控制流。"
               "（模板档，零模型）") % (
                   _meth, _span["start"] + 1, _var_name, _rel, _meth)
    _d = {"original_code": _orig, "modified_code": _mod,
          "reason": _reason,
          "splice_line": _span["start"] + 1}
    return {"ok": True, "data": _d, "why": "template_ok", "hits": _hits}


_TEMPLATE_RULES = {"silent_exception": _tpl_silent_exception}


def generate_from_template(project_root: str, entry: dict) -> dict:
    """模板档生成（**零模型**）。返回 `{ok, data, why}`，与 `parse_llm_reply` 同形。"""
    _it = str((entry or {}).get("issue_type") or "")
    _fn = _TEMPLATE_RULES.get(_it)
    if _fn is None:
        return {"ok": False, "data": None, "why": "no_template_for:%s" % _it}
    return _fn(project_root, entry or {})


# ========== ★352刀4：审批链分层 ==========
#   烛微352：低危草稿自动过、高危人工。接㔉裁决②审批分道。
#   ★不变量：`auto_apply_eligible` 恒 False。本刀只把“要不要人签字”分档，
#   **不**把“要不要自动改源码”一并放开——免签与免审是两件事，
#   混在一起就是把审批链当成自动落地链（铁律165：一开关控两语义须拆）。

APPROVAL_TIER_SCHEMA = "approval_tier/352.v1"
TIER_LOW = "low"
TIER_HIGH = "high"

#: 低危档的改动行上限（超过则不再“看上去小”）
APPROVAL_LOW_MAX_CHANGED_LINES = 6
#: 评分到达该值即高危（只升不降）
APPROVAL_HIGH_SCORE = 3


def score_approval_tier(draft: dict, project_root: str | None = None,
                        *, lane: str | None = None) -> dict:
    """给一份草稿算审批分档。只读、确定性。

    返回：`{schema, tier, requires_confirmation, auto_apply_eligible,
             score, reasons, changed_lines, signals}`。

    高危信号（只升不降，与 `_m344_merge_risk` 同一哲学）：
      判死候选 +5｜核心文件 +4｜跨文件族 +4｜影响面标记 +3
    低危信号（只降不升，且**永远抵不过**高危信号）：
      来自模板档 -2｜改动≤ 6 行 -1
    """
    _d = draft if isinstance(draft, dict) else {}
    _o: dict = {"schema": APPROVAL_TIER_SCHEMA, "tier": TIER_HIGH,
                "requires_confirmation": True, "auto_apply_eligible": False,
                "score": 0, "reasons": [], "changed_lines": None, "signals": {}}
    _f = _m349_norm_rel(_d.get("file"))
    _it = str(_d.get("issue_type") or "")
    _core = _m349_is_core_file(_f) if _f else True
    _o3 = bool(_d.get("o3_dead_code"))
    _multi = ("," in _f) or bool(_d.get("multi_file"))
    _dm = _m349_diff_is_minimal(_d.get("original_code") or "",
                                _d.get("modified_code") or "",
                                max_changed_lines=10 ** 6)
    _chg = _dm.get("changed")
    _o["changed_lines"] = _chg
    _o["signals"] = {"file": _f, "issue_type": _it, "is_core": bool(_core),
                     "o3_dead_code": _o3, "multi_file": _multi,
                     "lane": lane, "changed_lines": _chg,
                     "risk_level": _d.get("risk_level")}
    if _o3:
        _o["score"] += 5
        _o["reasons"].append("判死候选(o3_dead_code)")
    if _core:
        _o["score"] += 4
        _o["reasons"].append("核心文件")
    if _it in MANUAL_ISSUE_TYPES:
        _o["score"] += 4
        _o["reasons"].append("issue_type=%s 属跨文件/语义重族" % _it)
    if _multi:
        _o["score"] += 3
        _o["reasons"].append("多文件改动")
    if str(_d.get("risk_level") or "") in ("高", "中等", "high", "medium"):
        _o["score"] += 2
        _o["reasons"].append("risk_level=%s" % _d.get("risk_level"))
    # —— 低危信号（高危信号已累计时不再减，避免“降到低危”）——
    if _o["score"] == 0:
        if lane == LANE_TEMPLATE:
            _o["score"] -= 2
            _o["reasons"].append("来自模板档（确定性改写）")
        if isinstance(_chg, int) and _chg <= APPROVAL_LOW_MAX_CHANGED_LINES:
            _o["score"] -= 1
            _o["reasons"].append("改动 %d 行 ≤ %d" % (_chg, APPROVAL_LOW_MAX_CHANGED_LINES))
    _o["tier"] = TIER_HIGH if _o["score"] >= APPROVAL_HIGH_SCORE else TIER_LOW
    _o["requires_confirmation"] = (_o["tier"] == TIER_HIGH)
    # ★不变量：永远 False。免审 ≠ 免征。
    _o["auto_apply_eligible"] = False
    return _o


# ========== 源码片段 ==========


def _m349_norm_rel(file: str) -> str:
    return str(file or "").replace("\\", "/").strip().lstrip("./")


def extract_source_snippet(project_root: str, file: str, method: str,
                           line: int | None = None) -> dict:
    """从项目里取 `method` 的源码片段（**只读**）。

    返回 `{ok, code, start_line, end_line, why}`。
    找不到方法时退化为"按 line 取上下文窗口"，再不行 `ok=False`。
    """
    _rel = _m349_norm_rel(file)
    _p = os.path.join(project_root, _rel)
    if not os.path.isfile(_p):
        return {"ok": False, "code": "", "start_line": 0, "end_line": 0,
                "why": "file_not_found:%s" % _rel}
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _lines = _f.read().splitlines()
    except OSError as _e:
        silent_exc(_e, where="patch_generator::extract_source_snippet read")
        return {"ok": False, "code": "", "start_line": 0, "end_line": 0,
                "why": "read_error:%s" % type(_e).__name__}
    _m = str(method or "").strip()
    if _m:
        _start = -1
        for _i, _s in enumerate(_lines):
            _t = _s.strip()
            if _t.startswith("def %s(" % _m) or _t.startswith("async def %s(" % _m):
                _start = _i
                break
        if _start >= 0:
            _indent = len(_lines[_start]) - len(_lines[_start].lstrip())
            _end = _start + 1
            while _end < len(_lines):
                _s = _lines[_end]
                if _s.strip():
                    _cur = len(_s) - len(_s.lstrip())
                    if _cur <= _indent and not _s.lstrip().startswith(("#", "@")):
                        break
                _end += 1
            _seg = _lines[_start:_end]
            if len(_seg) > _SRC_MAX_LINES:
                _seg = (_seg[:_SRC_MAX_LINES // 2]
                        + ["    # ...（中间省略 %d 行）..." % (len(_seg) - _SRC_MAX_LINES)]
                        + _seg[-(_SRC_MAX_LINES // 2):])
            return {"ok": True, "code": "\n".join(_seg),
                    "start_line": _start + 1, "end_line": _end,
                    "why": "def_found"}
    # 退化：按 line 取上下文窗口
    try:
        _ln = int(line or 0)
    except (TypeError, ValueError) as _le:
        _module_logger.debug("[349生成] line 非数值，按 0 处理: %s: %s",
                             type(_le).__name__, _le)
        _ln = 0
    if _ln > 0:
        _a = max(0, _ln - 8)
        _b = min(len(_lines), _ln + 24)
        return {"ok": bool(_lines[_a:_b]), "code": "\n".join(_lines[_a:_b]),
                "start_line": _a + 1, "end_line": _b,
                "why": "line_window" if _lines[_a:_b] else "line_window_empty"}
    return {"ok": False, "code": "", "start_line": 0, "end_line": 0,
            "why": "method_not_found_and_no_line"}


# ========== 提示词与解析 ==========


# ========== ★352刀5：质量回灌（verdict → prompt） ==========
#   350刀1 已把「验证结果 → 问题池状态」闭环；本刀把同一份 verdict 再往
#   **生成侧**引：让模型看到“上几次同类改动实际效果如何”，而不是每次从零开始猜。

FEEDBACK_SCHEMA = "gen_feedback_context/352.v1"
#: 注入几条样本（越多越吃 token；7B 小模型吃不下太多）
FEEDBACK_TOP_N = 3
#: 样本 reason 截断（prompt 里不能被一条长理由撞没）
FEEDBACK_REASON_MAX = 90

#: 测试注入件的文件名形态（实测 effect_verify.json 里 50 条 verdict 有相当一部分
#: 是 `a.py`/`b.py` 这种自证造的假文件）。把它们当 few-shot 会把
#: 模型往“编一个不存在的文件”上带（铁律179：测试真值不得作为生产样本）。
FEEDBACK_FAKE_FILE_EXACT = ("a.py", "b.py", "c.py", "x.py", "test.py", "foo.py")
FEEDBACK_FAKE_PREFIX = ("tmp/", "tests/", "./a.py")


def _feedback_is_real(row: dict) -> bool:
    """判一条 verdict 是否值得当样本。只认**能定位到真实源码**的。"""
    _f = _m349_norm_rel(row.get("file"))
    if not _f:
        return False
    if _f in FEEDBACK_FAKE_FILE_EXACT:
        return False
    for _p in FEEDBACK_FAKE_PREFIX:
        if _f.startswith(_p):
            return False
    # 真实条目应带目录层次（organs/ 或 nucleus/），光杆文件名不算
    return "/" in _f


def load_feedback_examples(base_dir: str | None = None,
                          n: int = FEEDBACK_TOP_N) -> dict:
    """从 `effect_verify.json` 取最近 n 条**真实**验证结果作 few-shot。

    返回：`{schema, state, total, real, used, examples, path}`。
    `state` 如实回报 `absent/unreadable/no_verdicts/ok`——
    **读不到不等于“没有可回灌的东西”**（铁律156）。
    """
    _o = {"schema": FEEDBACK_SCHEMA, "state": "absent", "total": 0, "real": 0,
          "used": 0, "examples": [], "path": ""}
    try:
        from nucleus.evolution.verify_feedback import read_verdicts as _rv
    except Exception as _e:                                    # noqa: BLE001
        silent_exc(_e, where="patch_generator::load_feedback_examples import")
        _o["state"] = "import_failed"
        return _o
    _v = _rv(base_dir)
    _o["state"] = _v.get("state")
    _o["path"] = _v.get("path") or ""
    _rows = list((_v.get("verdicts") or {}).values())
    _o["total"] = len(_rows)
    _real = [r for r in _rows if isinstance(r, dict) and _feedback_is_real(r)]
    _o["real"] = len(_real)
    # 确定性排序：verified_at 倒序，同时间戳用 patch_id 做 tiebreak
    _real.sort(key=lambda r: (-(r.get("verified_at") or 0),
                              str(r.get("patch_id") or "")))
    _ex = []
    for r in _real[:max(0, int(n))]:
        _ex.append({
            "patch_id": str(r.get("patch_id") or "")[:24],
            "file": _m349_norm_rel(r.get("file")),
            "issue_type": str(r.get("issue_type") or "")[:40],
            "passed": r.get("passed"),
            "reason": str(r.get("reason") or "")[:FEEDBACK_REASON_MAX],
        })
    _o["examples"] = _ex
    _o["used"] = len(_ex)
    return _o


def render_feedback_block(examples: list) -> str:
    """把样本渲成 prompt 里的一段（无样本返回空串，不留一个空标题）。"""
    if not examples:
        return ""
    _lines = ["参考（同仓库最近已应用补丁的**实测**验证结果："
              "标「通过」的做法可信，标「未通过」的路子不要重走）："]
    for _e in examples:
        _tag = ("通过" if _e.get("passed") is True
                else ("未通过" if _e.get("passed") is False else "未判定"))
        _lines.append("- [%s] %s :: %s —— %s" % (
            _tag, _e.get("file"), _e.get("issue_type") or "?", _e.get("reason") or ""))
    return "\n".join(_lines) + "\n"


def build_prompt(entry: dict, source: dict, examples: list | None = None) -> str:
    """构造生成提示词（**确定性**，同一条目同一次输入 ⇒ 同一 prompt）。

    确定性很重要：合格率要能按版本比较，prompt 里不能塞时间戳/随机数。
    ★352刀5：`examples`（回灌样本）也要满足确定性——取样规则固定（见
    `load_feedback_examples`），同一份 `effect_verify.json` 必得同一个 prompt。
    """
    _desc = str(entry.get("desc") or "")[:_DESC_MAX_CHARS]
    return (
        # ★352刀6：版本号写*字面量*而不插变量——这是隐式字符串拼接，
        #   中间插一个名字会把拼接打断（实测直接 SyntaxError）。
        #   两处一致由 `tests/test_m352_batch.py::test_刀6_版本与模板一致` 断言保证。
        "你是一个严谨的 Python 代码修复助手。请按下面五步作业（本提示模板版本 352.d6.v1）。\n"
        "\n"
        "① 预检：先核对下面给出的 method 在源码片段里确实存在（能看到 "
        "def <method>）。看不到就说明“目标已漂移”，**不要凭空写代码**。\n"
        "② 定位：只改该 method 内部，不要动旁边的方法；不要改导包、"
        "不要新增依赖。\n"
        "③ 理由必须写成**三段**，段名就用这三个词：\n"
        "    「触发行：……」（定位到具体哪一行/哪个语句）\n"
        "    「现象：……」（运行时会发生什么，如异常被吞掉）\n"
        "    「依据：……」（凭什么判定这样改对）\n"
        "④ 交付形式：给**完整方法**（从 def 行到方法结束），不要只给片段、"
        "不要给 diff 补丁。`modified_code` 里必须**只有一个 def**，且名字等于 method。\n"
        "⑤ 自检：交之前心里过一遍——把 `modified_code` 当作一个完整模块能不能 parse？"
        "缩进是否与原文一致？能不能被本文件已有的导包满足？\n"
        "\n"
        "只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码围栏。\n"
        'JSON 字段：{"original_code": "...", "modified_code": "...", "reason": "..."}\n'
        "  · original_code = 源码片段里 **该方法的原文**（逐字复制，含原有缩进）。\n"
        "  · modified_code = 修复后的**整个方法**（含 def 行，缩进与原文一致，不超 120 行改动）。\n"
        "  · reason = 上面③的三段式。\n"
        "  · 若这个问题无法通过改代码解决（协议接口/环境缺陷/需人工决策），"
        "把 original_code 与 modified_code 都输出为空字符串，reason 里写明原因。\n"
        "问题：issue_type=%s severity=%s klass=%s\n"
        "位置（②定位用）：file=%s method=%s line=%s\n"
        "描述：%s\n%s"
        "源码片段（%s，行 %s-%s）：\n%s\n"
    ) % (
        entry.get("issue_type"), entry.get("severity"), entry.get("klass"),
        entry.get("file"), entry.get("method"), entry.get("line"),
        _desc, render_feedback_block(examples or []),
        source.get("why"), source.get("start_line"), source.get("end_line"),
        source.get("code") or "",
    )


def _m349_strip_fence(text: str) -> str:
    """去掉模型习惯性包上的 ```json ... ``` 围栏。"""
    _t = str(text or "").strip()
    if _t.startswith("```"):
        _t = _t.split("\n", 1)[1] if "\n" in _t else _t
        if _t.rstrip().endswith("```"):
            _t = _t.rstrip()[:-3]
    return _t.strip()


def parse_llm_reply(text: str) -> dict:
    """解析模型回复 → `{ok, data, why}`。

    三级降级：整段 JSON → 截取首个 `{` 到末个 `}` → 失败。
    **不做"猜字段"式的容错**：解析不出来就是 `ok=False`（喂给上层判 generation_failed）。
    """
    _t = _m349_strip_fence(text)
    _cand = [_t]
    _i, _j = _t.find("{"), _t.rfind("}")
    if 0 <= _i < _j:
        _cand.append(_t[_i:_j + 1])
    for _c in _cand:
        try:
            _d = json.loads(_c)
        except (ValueError, TypeError) as _je:
            _module_logger.debug("[349生成] JSON 解析未过（继续降级）: %s: %s",
                                 type(_je).__name__, _je)
            continue
        if isinstance(_d, dict):
            return {"ok": True, "data": _d, "why": "parsed"}
    return {"ok": False, "data": None, "why": "json_unparseable"}


# ========== 证据门（六字段）与审核链标准 ==========


def check_evidence_six(draft: dict) -> dict:
    """★349刀1②：六字段强制。

    返回 `{ok, missing, why}`。`missing` 逐字段列出——**不许只说"不合格"**。
    注意 `reason` 与 `dedup_key` 是本批新增的两项，也是历史上最容易缺的两项：
      · 缺 `reason` ⇒ 无法回答"凭什么改"（337刀1 的 fcef 病灶同源）
      · 缺 `dedup_key` ⇒ 无法回答"这条和池里那是不是同一条"
    """
    _miss: list = []
    for _k in REQUIRED_PATCH_FIELDS:
        _v = draft.get(_k)
        if _v is None or not str(_v).strip():
            _miss.append(_k)
    if _miss:
        return {"ok": False, "missing": _miss, "why": "缺 %s" % ",".join(_miss)}
    # dedup_key 还要求**可复算自洽**：填了个对不上的键比不填更糟
    _expect = make_dedup_key(draft.get("file"), draft.get("method"),
                             draft.get("issue_type"))
    _got = str(draft.get("dedup_key") or "")
    if _got != _expect:
        return {"ok": False, "missing": ["dedup_key"],
                "why": "dedup_key 不自洽（got=%s expect=%s）" % (_got, _expect)}
    return {"ok": True, "missing": [], "why": "ok"}


def _m349_ast_ok(code: str, method: str) -> dict:
    """AST 校验：片段能否 `ast.parse`，且（给了 method 时）能解出同名 def。

    为什么降级到"包一层 def"再 parse：模型给的常常是**方法体片段**
    （缩进 8 空格），直接 parse 会 `IndentationError` —— 那是形态问题，
    不是语法问题。先原样 parse，失败再 dedent 包函数重试。
    """
    _c = str(code or "")
    try:
        _tree = ast.parse(_c)
        _names = [n.name for n in ast.walk(_tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return {"ok": True, "why": "parse_ok", "defs": _names[:8]}
    except SyntaxError as _se:
        _module_logger.debug("[349生成] 片段原样 parse 失败，尝试包 def 重试: %s", _se)
    _m = str(method or "generated").strip() or "generated"
    try:
        _wrapped = "def %s():\n%s" % (
            _m, "\n".join("    " + _ln for _ln in _c.splitlines()))
        _tree2 = ast.parse(_wrapped)
        _names2 = [n.name for n in ast.walk(_tree2)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return {"ok": True, "why": "parse_ok_wrapped", "defs": _names2[:8]}
    except SyntaxError as _se2:
        _module_logger.debug("[349生成] 包 def 重试仍 parse 失败: %s", _se2)
        return {"ok": False, "why": "syntax_error:%s" % _se2, "defs": []}


def _m349_is_core_file(file: str) -> bool:
    """委派 `SafeEvolutionExecutor._is_core_file`（**单一口径**，不重写规则）。

    340 之后的教训：核心文件清单散成两份，早晚不一致。
    """
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor as _SEE

        return bool(_SEE._is_core_file(str(file or "")))
    except Exception as _e:
        silent_exc(_e, where="patch_generator::_m349_is_core_file")
        return True  # 判不出来 ⇒ 当核心处理（宁可转人工）


def _m349_diff_is_minimal(original: str, modified: str, max_changed_lines: int = 40) -> dict:
    """diff 最小集：改动行数不得超过上限，且不得"整段重写"。

    判定用**行级集合差**（不看字符 diff）：`|A-B| + |B-A|` 即增删行数。
    另外一条硬约束：`modified` **不得比 `original` 短到 1/3 以下**
    （历史事故：LLM 把整个方法体换成一个 `pass`）。
    """
    _a = [x.rstrip() for x in str(original or "").splitlines() if x.strip()]
    _b = [x.rstrip() for x in str(modified or "").splitlines() if x.strip()]
    if not _a or not _b:
        return {"ok": False, "why": "empty_side", "changed": 0}
    if len(_b) * 3 < len(_a):
        return {"ok": False, "why": "shrunk_to_under_1_3(%d->%d)" % (len(_a), len(_b)),
                "changed": abs(len(_a) - len(_b))}
    _sa, _sb = set(_a), set(_b)
    _changed = len(_sa - _sb) + len(_sb - _sa)
    if _changed > int(max_changed_lines):
        return {"ok": False, "why": "changed_lines_%d_gt_%d" % (_changed, max_changed_lines),
                "changed": _changed}
    return {"ok": True, "why": "minimal", "changed": _changed}


def _m349_indent_shift(original: str, modified: str) -> str:
    """把 `modified` 的公共缩进对齐到 `original`（模型常把 8 空格写成 4）。

    只调**公共前缀缩进**（每行都减/加同一个量），不动相对缩进 ——
    相对缩进一改就是"重写"，那不是最小改动。
    """
    def _common(text: str) -> int:
        _v = [len(x) - len(x.lstrip()) for x in text.splitlines() if x.strip()]
        return min(_v) if _v else 0

    _a, _b = _common(original), _common(modified)
    if _a == _b:
        return modified
    if _b > _a:
        _cut = _b - _a
        _out = []
        for _x in modified.splitlines():
            _k = len(_x) - len(_x.lstrip())
            _out.append(_x[min(_cut, _k):] if _x.strip() else _x)
        return "\n".join(_out)
    return "\n".join((" " * (_a - _b)) + _x if _x.strip() else _x
                      for _x in modified.splitlines())


def _m349_method_span(project_root: str, file: str, method: str) -> dict:
    """取方法在文件里的**绝对行区间** `[start, end)`（0-based 半开）。

    与 `extract_source_snippet` 同一把尺子（同一 `def` 扫描 + 同一缩进收尾规则），
    只是这里只要行号。取不到 ⇒ `ok=False`。
    """
    _rel = str(file or "").replace("\\", "/").strip().lstrip("./")
    _p = os.path.join(project_root, _rel)
    _m = str(method or "").strip()
    if not _m or not os.path.isfile(_p):
        return {"ok": False, "start": 0, "end": 0, "why": "file_or_method_missing"}
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _lines = _f.read().replace("\r\n", "\n").split("\n")
    except OSError as _e:
        silent_exc(_e, where="patch_generator::_m349_method_span read")
        return {"ok": False, "start": 0, "end": 0, "why": "read_error"}
    for _i, _s in enumerate(_lines):
        _t = _s.strip()
        if _t.startswith("def %s(" % _m) or _t.startswith("async def %s(" % _m):
            _indent = len(_s) - len(_s.lstrip())
            _j = _i + 1
            while _j < len(_lines):
                _cur_s = _lines[_j]
                if _cur_s.strip():
                    _cur = len(_cur_s) - len(_cur_s.lstrip())
                    if _cur <= _indent and not _cur_s.lstrip().startswith(("#", "@")):
                        break
                _j += 1
            return {"ok": True, "start": _i, "end": _j, "why": "def_found"}
    return {"ok": False, "start": 0, "end": 0, "why": "method_not_found"}


def check_splice_parses(project_root: str, file: str, original: str,
                        modified: str, line_no: int | None = None,
                        method: str | None = None) -> dict:
    """★349刀1②④：把 `original_code` 替换成 `modified_code`，再对**整个文件** `ast.parse`。

    定位优先级（**先确定，再猜**）：
      1. 给了 `line_no` —— 直接按绝对行号替换，并断言该行 strip 后等于
         `original`（不等 ⇒ `anchor_drifted`，不替换）；
      2. 没给行号 —— 全文逐字唯一匹配（`occurrences==1`）；
      3. 全文多处匹配但给了 `method` —— 在方法区间内替换。

    失败档必须分得开（铁律166）：`line_drifted` / `original_not_found` /
    `original_ambiguous:N` / `syntax_error:...`。
    """
    _rel = str(file or "").replace("\\", "/").strip().lstrip("./")
    _p = os.path.join(project_root, _rel)
    if not os.path.isfile(_p):
        return {"ok": False, "why": "file_not_found:%s" % _rel,
                "occurrences": 0, "mode": "none"}
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _lines = _f.read().replace("\r\n", "\n").split("\n")
    except OSError as _e:
        silent_exc(_e, where="patch_generator::check_splice_parses read")
        return {"ok": False, "why": "read_error:%s" % type(_e).__name__,
                "occurrences": 0, "mode": "none"}
    _whole = "\n".join(_lines)
    _o = str(original or "").replace("\r\n", "\n").strip("\n")
    _m = str(modified or "").replace("\r\n", "\n").strip("\n")
    if not _o or not _m:
        return {"ok": False, "why": "empty_side", "occurrences": 0, "mode": "none"}
    _tries = [(False, _m), (True, _m349_indent_shift(_o, _m))]
    # ---- 1) 行号定位 ----
    if line_no is not None:
        try:
            _ln = int(line_no)
        except (TypeError, ValueError) as _le:
            _module_logger.debug("[349生成] line_no 非数值: %s: %s",
                                 type(_le).__name__, _le)
            _ln = 0
        _idx = _ln - 1
        if 0 <= _idx < len(_lines):
            if _lines[_idx].strip() == _o.strip():
                _last = ""
                for _realigned, _cand in _tries:
                    _new_lines = list(_lines)
                    _new_lines[_idx] = _cand
                    try:
                        ast.parse("\n".join(_new_lines), filename=_rel)
                        return {"ok": True, "why": "parse_ok_after_splice",
                                "occurrences": 1, "mode": "line_no",
                                "realigned": _realigned, "line_no": _ln}
                    except SyntaxError as _se:
                        silent_exc(_se, where="patch_generator.py::line472")
                        _last = "syntax_error:%s(line %s)" % (_se.msg, _se.lineno)
                return {"ok": False, "why": _last, "occurrences": 1,
                        "mode": "line_no", "line_no": _ln}
            return {"ok": False, "why": "anchor_drifted(line %d)" % _ln,
                    "occurrences": 0, "mode": "line_no", "line_no": _ln}
        return {"ok": False, "why": "line_out_of_range:%s" % _ln,
                "occurrences": 0, "mode": "line_no"}
    # ---- 2) 全文唯一 ----
    _n = _whole.count(_o)
    if _n > 1:
        # ---- 3) 方法区间内替换 ----
        _sp = _m349_method_span(project_root, file, method or "")
        if _sp.get("ok"):
            _seg = "\n".join(_lines[_sp["start"]:_sp["end"]])
            if _seg.count(_o) == 1:
                _last = ""
                for _realigned, _cand in _tries:
                    _new_seg = _seg.replace(_o, _cand, 1)
                    _new = ("\n".join(_lines[:_sp["start"]]) + "\n" + _new_seg
                            + "\n" + "\n".join(_lines[_sp["end"]:]))
                    try:
                        ast.parse(_new, filename=_rel)
                        return {"ok": True, "why": "parse_ok_after_splice",
                                "occurrences": _n, "mode": "method_scope",
                                "realigned": _realigned}
                    except SyntaxError as _se:
                        silent_exc(_se, where="patch_generator.py::line498")
                        _last = "syntax_error:%s(line %s)" % (_se.msg, _se.lineno)
                return {"ok": False, "why": _last, "occurrences": _n,
                        "mode": "method_scope"}
        return {"ok": False, "why": "original_ambiguous:%d" % _n,
                "occurrences": _n, "mode": "global"}
    if _n == 0:
        _o_stripped = "\n".join(x.strip() for x in _o.splitlines() if x.strip())
        _w_stripped = "\n".join(x.strip() for x in _lines if x.strip())
        if _o_stripped and _o_stripped in _w_stripped:
            return {"ok": False, "why": "original_indent_drifted",
                    "occurrences": 0, "mode": "global"}
        return {"ok": False, "why": "original_not_found",
                "occurrences": 0, "mode": "global"}
    _last = ""
    for _realigned, _cand in _tries:
        _new = _whole.replace(_o, _cand, 1)
        try:
            ast.parse(_new, filename=_rel)
            return {"ok": True, "why": "parse_ok_after_splice", "occurrences": _n,
                    "mode": "global", "realigned": _realigned}
        except SyntaxError as _se:
            silent_exc(_se, where="patch_generator.py::line519")
            _last = "syntax_error:%s(line %s)" % (_se.msg, _se.lineno)
    return {"ok": False, "why": _last, "occurrences": _n, "mode": "global"}


def _m349_compose_from_anchor(project_root: str, file: str, anchor: str,
                              replacement: str, method: str | None = None) -> dict:
    """把 `anchor`→`replacement` 组装成 `original_code`/`modified_code` + 落点行号。

    ★这一步是"把抄原文的活从模型手里拿回来"：`original_code` 直接从磁盘取，
    模型只负责"改哪一行、改成什么"。

    定位语义（本批订正）：anchor 的唯一性只在**目标方法范围内**要求 ——
    它的语义是"这个方法里的一行"，不是"全文件只出现一次的行"。
    实测 6 条里有 2 条因为 `try:`／`return None` 这类高频行被判
    `anchor_ambiguous:45/80`，那是**判据错位**，不是模型错。
    """
    _rel = str(file or "").replace("\\", "/").strip().lstrip("./")
    _p = os.path.join(project_root, _rel)
    _a = str(anchor or "").strip("\n")
    if not _a.strip():
        return {"ok": False, "why": "anchor_empty"}
    if not os.path.isfile(_p):
        return {"ok": False, "why": "file_not_found:%s" % _rel}
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _lines = _f.read().replace("\r\n", "\n").split("\n")
    except OSError as _e:
        silent_exc(_e, where="patch_generator::_m349_compose_from_anchor read")
        return {"ok": False, "why": "read_error:%s" % type(_e).__name__}
    _hits = [_i for _i, _s in enumerate(_lines) if _s.strip() == _a.strip()]
    _scope = ""
    if method:
        _sp = _m349_method_span(project_root, file, method)
        if _sp.get("ok"):
            _in = [_i for _i in _hits if _sp["start"] <= _i < _sp["end"]]
            if len(_in) == 1:
                _hits = _in
                _scope = "method"
            elif len(_in) > 1:
                return {"ok": False, "why": "anchor_ambiguous_in_method:%d" % len(_in)}
    if not _hits:
        if method:
            return {"ok": False, "why": "anchor_not_in_method"}
        return {"ok": False, "why": "anchor_not_found"}
    if len(_hits) > 1:
        return {"ok": False, "why": "anchor_ambiguous:%d" % len(_hits)}
    _idx = _hits[0]
    _rep = str(replacement or "").strip("\n")
    if _rep:
        _rep = _m349_indent_shift(_lines[_idx], _rep)
    return {"ok": True, "why": "composed:%s" % (_scope or "global_unique"),
            "original_code": _lines[_idx].strip("\n"),
            "modified_code": _rep, "occurrences": 1,
            "line_no": _idx + 1}

def judge_draft(draft: dict, project_root: str | None = None) -> dict:
    """审核链通过标准（六字段 / AST / diff 最小 / 不触上帝文件）。

    返回 `{state, checks, gate_reasons}`。`state` ∈ 三态
    （`draft_ready` / `gate_rejected`；`generation_failed` 由调用方判）。
    """
    _checks: dict = {}
    _reasons: list = []
    if project_root and not draft.get("_project_root"):
        draft["_project_root"] = project_root
    _six = check_evidence_six(draft)
    _checks["six_fields"] = _six
    if not _six["ok"]:
        _reasons.append("six_fields:%s" % _six["why"])
    # ★349刀1②：AST 判据从「片段自洽」改为「替换回原文件后整文件 parse」。
    #   原判据把合法片段误杀（改动落在 try 块内 ⇒ 裸 parse 报
    #   `expected 'except' or 'finally' block`）。判别对象错了：
    #   我们要验的是**改完的代码还对不对**，不是"片段能不能单独跑"。
    _sp = check_splice_parses(str(draft.get("_project_root") or ""),
                              draft.get("file"),
                              draft.get("original_code") or "",
                              draft.get("modified_code") or "",
                              line_no=draft.get("splice_line"),
                              method=draft.get("method"))
    _checks["ast_after_splice"] = _sp
    if not _sp["ok"]:
        _reasons.append("ast_splice:%s" % _sp["why"])
    # ★350刀6①：desc 禁嵌动态读数（防349刀6假漂移复发）
    _dd = check_desc_dynamic(draft)
    _checks["desc_dynamic"] = _dd
    if not _dd["ok"]:
        _reasons.append("desc_dynamic:%s" % _dd["why"])
    # ★350刀6③：reason 证据链（触发行/现象/依据）
    _rc = check_reason_chain(draft)
    _checks["reason_chain"] = _rc
    if not _rc["ok"]:
        _reasons.append("reason_chain:%s" % _rc["why"])
    # ★350刀6③②：整方法改写契约（限上用方法级，而非锚点级 40 行）
    _mc = check_method_contract(draft, draft.get("_project_root"))
    _checks["method_contract"] = _mc
    if not _mc["ok"]:
        _reasons.append("method_contract:%s" % _mc["why"])
    _dm_limit = (METHOD_CONTRACT_MAX_LINES
                 if _mc.get("mode") == "method" else 40)
    _dm = _m349_diff_is_minimal(draft.get("original_code") or "",
                                draft.get("modified_code") or "",
                                max_changed_lines=_dm_limit)
    _checks["diff_minimal"] = _dm
    if not _dm["ok"]:
        _reasons.append("diff:%s" % _dm["why"])
    _core = _m349_is_core_file(draft.get("file"))
    _checks["touches_core_file"] = _core
    if _core:
        # ★不是"不合格"，是"必须人审"——但按任务书的通过标准，它不进 auto 通道
        _reasons.append("touches_core_file")
    # ★354刀6增量：方法体完整性（星轨建议：改后行数 ≥ 原方法体 ×0.85）。
    #   `ast.parse` 只验语法不验长度 ⇒ 被 max_tokens 截断的草稿可能"语法合法但内容残缺"，
    #   单靠语法校验会把它当合格品放行。
    _bi = check_body_integrity(draft.get("_project_root"), draft.get("file"),
                               draft.get("method"),
                               draft.get("modified_code") or "")
    _checks["body_integrity"] = _bi
    # ★355刀2：双判据。判据A（行数比）抓"整体变短"，判据B（尾部覆盖）抓
    #   "长度够但尾段丢失" —— 94e9 行数比 1.05 过关、尾部却断在 tempo 分支，
    #   只有 B 能抓住。任一不过即不进 auto 通道。
    _tc = check_tail_coverage(draft.get("_project_root"), draft.get("file"),
                              draft.get("method"),
                              draft.get("modified_code") or "")
    _checks["tail_coverage"] = _tc
    if _bi["verdict"] == "truncated":
        _reasons.append("body_truncated:%.2f" % _bi["ratio"])
        _module_logger.warning(
            "[355生成器] 判据A行数比=truncated 改后%d行/原%d行=%.2f<%s file=%s method=%s",
            _bi["new_lines"], _bi["orig_lines"], _bi["ratio"],
            _bi["min_ratio"], draft.get("file"), draft.get("method"))
    if _tc["verdict"] == "truncated":
        _reasons.append("tail_coverage:%.2f" % _tc["ratio"])
        _module_logger.warning(
            "[355生成器] 判据B尾部覆盖=truncated 命中%d/%d=%.2f<%s file=%s method=%s 丢失尾行=%s",
            _tc["tail_hit"], _tc["tail_total"], _tc["ratio"], _tc["min_ratio"],
            draft.get("file"), draft.get("method"), _tc["missing"][:3])
    elif _bi["verdict"] == "empty_output":
        _reasons.append("body_empty_output")
    # undecidable ⇒ 不追加理由（判据不可用不等于不合格，铁律166）
    _state = STATE_DRAFT_READY if not _reasons else STATE_GATE_REJECTED
    return {"state": _state, "checks": _checks, "gate_reasons": _reasons}


# ===== ★350刀6：证据门强化（desc 动态读数 + reason 证据链 + 整方法改写契约）=====

#: 契约切换：**整方法改写**（350 刀6③②·小林 2026-10-23 裁定）。
#: 为什么从"锚点三元组"改成"整方法"：锚点级只能表达 3 行内的替换，模型给出的
#: 往往是"这个方法该怎么写"，硬压成锚点会丢掉上下文 ⇒ 合格率 14%（349 刀1）。
#: 翻回锚点级只需把此常量置 False（保留回退路径，不靠改代码结构）。
METHOD_LEVEL_CONTRACT = True

#: 整方法改写的改动行上限（比锚点级的 40 行宽——整方法本来就长）
METHOD_CONTRACT_MAX_LINES = 120

#: reason 证据链三段（触发行 / 现象 / 判定依据）——缺一段即"薄理由"
REASON_CHAIN_KEYS = ("触发行", "现象", "依据")

#: 理由薄的最低长度（过短的 reason 基本等于没写）
REASON_MIN_CHARS = 24


def check_desc_dynamic(draft: dict) -> dict:
    """★350刀6①：desc 含**动态读数**即拒收。

    病根（349 刀6 真凶）：`desc` 里嵌了 `硬=59.3%/全=66.2%` 这类覆盖率读数，
    而覆盖率每份图算一次 ⇒ 同一不变量的文本**每份都不同** ⇒ 按原文对差
    假漂移 1217 条。判别力来自"这个 token 会不会随时间自己变"，不是"它像不像数字"。
    """
    from nucleus.evolution.desc_norm import desc_dynamic_hits
    _hits = desc_dynamic_hits(draft.get("desc"))
    return {"ok": not _hits, "hits": _hits[:8],
            "why": "" if not _hits
                   else "desc 含动态读数 %s（会随每份快照漂移，禁止进身份/对差）"
                        % ",".join(h[:24] for h in _hits[:4])}


def check_reason_chain(draft: dict) -> dict:
    """★350刀6③：reason 必须有证据链（触发行/现象/依据三段落其一不可缺）。

    口径：三段的**顺序**不强制（模型常写成"依据…现象…"），但**存在性**必须齐。
    另外给一条长度下限，防"补 bug"这种等于没写的一句话理由。
    """
    _r = str(draft.get("reason") or "")
    _miss = [k for k in REASON_CHAIN_KEYS if k not in _r]
    _short = len(_r.strip()) < REASON_MIN_CHARS
    _ok = (not _miss) and (not _short)
    if _ok:
        return {"ok": True, "missing": [], "why": ""}
    _why = []
    if _miss:
        _why.append("缺段落 %s" % ",".join(_miss))
    if _short:
        _why.append("理由过短(%d<%d)" % (len(_r.strip()), REASON_MIN_CHARS))
    return {"ok": False, "missing": _miss, "why": "；".join(_why)}


def check_method_contract(draft: dict, project_root: str | None = None) -> dict:
    """★350刀6③②：整方法改写契约（方法级 diff / AST 方法体）。

    三问：
      ① `modified_code` 能不能 parse；
      ② 有没有且只有**一个**顶层 def，名字等于 `method`；
      ③ 改动行数在**方法级**上限之内（`METHOD_CONTRACT_MAX_LINES`）。
    契约关闭时返回 `{"ok": True, "mode": "anchor", ...}`（不做方法级约束）。
    """
    if not METHOD_LEVEL_CONTRACT:
        return {"ok": True, "mode": "anchor", "why": "", "defs": [],
                "changed_lines": None}
    _code = str(draft.get("modified_code") or "")
    _m = str(draft.get("method") or "").strip()
    import ast as _ast

    # ★352刀3：**缩进形态不是语法错误**。
    #   类内方法的 `modified_code` 天然带类体缩进（4 空格），直接 `ast.parse`
    #   必报 `unexpected indent`——那是**形态**问题，不是代码坏了（铁律162：
    #   判据按语义单元）。先原样 parse，失败再 dedent 重试——
    #   与 `_m349_ast_ok` 已有做法同源（那里已经这么做了，本门当初漏了）。
    _tree = None
    _dedented = False
    try:
        _tree = _ast.parse(_code)
    except SyntaxError as _se:
        _ded = textwrap.dedent(_code)
        if _ded != _code:
            try:
                _tree = _ast.parse(_ded)
                _dedented = True
            except SyntaxError as _dse:
                silent_exc(_dse, where="patch_generator.check_method_contract dedent")
                _tree = None
        if _tree is None:
            _module_logger.info("[350生成] 整方法契约 AST 不通过: %s", _se)
            return {"ok": False, "mode": "method", "why": "syntax_error:%s" % _se,
                    "defs": [], "changed_lines": None, "dedented": False}
    _defs = [n.name for n in _tree.body
             if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))]
    if len(_defs) != 1:
        return {"ok": False, "mode": "method",
                "why": "顶层 def 数=%d（须=1：整方法改写）" % len(_defs),
                "defs": _defs[:6], "changed_lines": None}
    if _m and _defs[0] != _m:
        return {"ok": False, "mode": "method",
                "why": "def 名 %s ≠ method %s" % (_defs[0], _m),
                "defs": _defs[:6], "changed_lines": None}
    _o = str(draft.get("original_code") or "")
    _n_changed = None
    if _o:
        try:
            import difflib as _dl
            _diff = list(_dl.unified_diff(_o.splitlines(), _code.splitlines(), n=0))
            _n_changed = sum(1 for ln in _diff
                             if (ln.startswith("+") or ln.startswith("-"))
                             and not ln.startswith("+++") and not ln.startswith("---"))
        except Exception as _e:                                # noqa: BLE001
            silent_exc(_e, where="patch_generator.check_method_contract diff")
            _n_changed = None
    if _n_changed is not None and _n_changed > METHOD_CONTRACT_MAX_LINES:
        return {"ok": False, "mode": "method",
                "why": "方法级改动 %d 行 > 上限 %d" % (_n_changed, METHOD_CONTRACT_MAX_LINES),
                "defs": _defs[:6], "changed_lines": _n_changed}
    return {"ok": True, "mode": "method", "why": "", "defs": _defs[:6],
            "changed_lines": _n_changed, "dedented": _dedented}


# ========== 传输层（可注入）==========


def ollama_transport(prompt: str, *, model: str, base_url: str,
                     timeout: float) -> dict:
    """默认传输：Ollama `/api/generate`（`stream=False`）。

    返回 `{ok, text, err}`。**任何失败都返回 ok=False + err**，不抛、不静默。
    """
    try:
        from nucleus.ssrf_guard import safe_http_json

        _url = str(base_url).rstrip("/") + "/api/generate"
        _payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            # ★349刀1 实测调参：512 够写完一个最小补丁（30 行内改动 200-350 tokens）
            # ★实测：本机 qwen2:7b 约 7 tok/s，512 打满要 73s ⇒ 必撞 60s 线；
            #   320 实测 29s 完成，而一条最小补丁实测只用 159-198 tokens。
            "options": {"temperature": 0.2, "num_predict": 320},
        }, ensure_ascii=False).encode("utf-8")
        _ok, _data = safe_http_json(
            _url, method="POST", data=_payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=int(max(1.0, float(timeout))),
        )
        if not _ok:
            return {"ok": False, "text": "", "err": str(_data)[:300]}
        if not isinstance(_data, dict):
            return {"ok": False, "text": "", "err": "non_dict_response"}
        _txt = str(_data.get("response") or "").strip()
        if not _txt:
            return {"ok": False, "text": "", "err": "empty_response"}
        return {"ok": True, "text": _txt, "err": ""}
    except Exception as _e:
        silent_exc(_e, where="patch_generator::ollama_transport")
        return {"ok": False, "text": "",
                "err": "%s: %s" % (type(_e).__name__, str(_e)[:200])}


# ========== 主入口 ==========


def generate_patch_draft(entry: dict,
                         project_root: str | None = None,
                         *,
                         model: str | None = None,
                         base_url: str | None = None,
                         timeout: float | None = None,
                         transport: Callable[..., dict] | None = None,
                         now: float | None = None) -> dict:
    """把一条问题池条目生成**待审草稿**（三态之一）。

    Args:
        entry: 问题池条目（至少 `file/method/issue_type/desc/severity/klass`）。
        project_root: 项目根（**必须可注入**，铁律158）；None 才回退本文件上溯。
        transport: 传输函数（测试注入用；签名见 `ollama_transport`）。
        now: 时间戳注入（测试用）。

    返回 dict：
        `state` ∈ 三态；
        `draft`（`draft_ready` 时六字段齐）；
        `error` / `gate_reasons`（按态填其一）；
        `prompt_chars` / `elapsed_sec` / `model` / `generator_version` / `proc_gen`。
    """
    _t0 = time.perf_counter()
    _now = float(now if now is not None else time.time())
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _model = model or str(_m349_cfg("PATCH_GENERATOR_MODEL", DEFAULT_MODEL))
    # ★逐级回退必须**每一步都判空**：config 里 PATCH_GENERATOR_BASE_URL 的默认值
    #   是空串（表示"没配"）， 会把空串当有效值，导致 URL 变成
    #   "/api/generate" ⇒ SSRF 报"不支持的协议" ⇒ 全量 generation_failed。
    #   这是自证时现场抓到的：三态是对的（它如实报失败），但**失败原因被伪装成模型问题**。
    _base = (base_url
             or str(_m349_cfg("PATCH_GENERATOR_BASE_URL", "") or "")
             or str(_m349_cfg("OLLAMA_BASE_URL", "") or "")
             or DEFAULT_BASE_URL)
    _to = float(timeout if timeout is not None
                else _m349_cfg("PATCH_GENERATOR_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC))
    _tp = transport or ollama_transport
    _out: dict = {
        "state": STATE_GENERATION_FAILED,
        "generator_version": GENERATOR_VERSION,
        "model": _model,
        "issued_at": _now,
        "entry_key": str(entry.get("dedup_key") or "") or make_dedup_key(
            entry.get("file"), entry.get("method"), entry.get("issue_type")),
        "draft": None, "error": "", "gate_reasons": [],
        "prompt_chars": 0, "elapsed_sec": None, "proc_gen": "unknown",
    }
    try:
        from nucleus.proc_gen import proc_gen_str as _pgs

        _out["proc_gen"] = str(_pgs())
    except Exception as _e:
        silent_exc(_e, where="patch_generator::generate_patch_draft proc_gen")
    if not isinstance(entry, dict) or not entry:
        _out["error"] = "empty_entry"
        return _out
    if not str(entry.get("file") or "").strip() or not str(entry.get("method") or "").strip():
        # 缺定位 ⇒ 连 prompt 都构不出来。这是**上游问题**，不是模型失败，
        #   但也不能算 gate_rejected（没有产物可判）⇒ 归 generation_failed + 明确 error。
        _out["error"] = "entry_missing_file_or_method"
        _module_logger.warning("[349生成] 条目缺 file/method，无法生成: key=%s",
                               _out["entry_key"])
        return _out
    # ★355刀2①：先把方法名归一为真实名（`m323_truth.method_before`），
    #   再走预检与生成 —— 否则 prompt 里带着猜的名，生成的 def 名也是猜的。
    entry = _m355_normalize_method(entry)
    # ★352刀1①：生成前目标存在性预检。
    #   为何放在取源码**之前**：目标已不存在时读文件本就是白费，
    #   而且旧路径会把它记成 generation_failed（词不达意）。
    # ★354刀6修正：生成前预检改用三锚或判（星轨 v1.1）。
    #   仅按 field 名判会误杀 35 条真实补丁（它们的真实名在 m323_truth.method_before）。
    _pc = precheck_target_v2(
        _root, entry.get("file"), entry.get("method"),
        method_before=str((entry.get("m323_truth") or {}).get("method_before") or "").strip(),
        original_code=entry.get("original_code") or "")
    _out["precheck"] = _pc
    if _pc["verdict"] in TARGET_DRIFTED_VERDICTS:
        _out["state"] = STATE_TARGET_DRIFTED
        _out["error"] = "target_drifted:%s" % _pc["verdict"]
        # 留证：告诉读人“改前它会被归到哪一档”，否则无法相信这是修复
        _out["would_be_state_before_352"] = STATE_GENERATION_FAILED
        _module_logger.warning(
            "[352预检] target_drifted key=%s file=%s method=%s verdict=%s why=%s "
            "→ 不进生成（改前会被误记为 %s，重试无效）",
            _out["entry_key"], entry.get("file"), entry.get("method"),
            _pc["verdict"], _pc["why"], STATE_GENERATION_FAILED)
        return _out
    # ★352刀3：分层路由。路由在**取源码之前**做：manual 档根本不需要源码，取了也是白取。
    _lane = route_generation_lane(entry, _root)
    _out["lane"] = _lane
    if _lane["lane"] == LANE_MANUAL:
        _out["state"] = STATE_MANUAL_REQUIRED
        _out["error"] = "lane_manual:%s" % ";".join(_lane["reasons"])[:200]
        _module_logger.warning(
            "[352路由] manual_required key=%s file=%s method=%s 复杂度=%d 原因=%s",
            _out["entry_key"], entry.get("file"), entry.get("method"),
            _lane["score"], ";".join(_lane["reasons"])[:200])
        return _out
    _src = extract_source_snippet(_root, entry.get("file"), entry.get("method"),
                                  entry.get("line"))
    _out["source_ok"] = bool(_src.get("ok"))
    _out["source_why"] = _src.get("why")
    if not _src.get("ok"):
        _out["error"] = "source_unavailable:%s" % _src.get("why")
        _module_logger.warning("[349生成] 取不到源码片段，不猜测: key=%s file=%s why=%s",
                               _out["entry_key"], entry.get("file"), _src.get("why"))
        return _out
    _pr = None
    if _lane["lane"] == LANE_TEMPLATE:
        # ★模板档零模型。生成失败**不是错误**，是“模板覆盖不到”⇒ 降级给模型。
        _tpl = generate_from_template(_root, entry)
        _out["template"] = {_k: _tpl.get(_k) for _k in ("ok", "why", "hits")}
        if _tpl.get("ok"):
            _pr = _tpl
            _out["lane_effective"] = LANE_TEMPLATE
            _out["prompt_chars"] = 0
            _out["elapsed_sec"] = round(time.perf_counter() - _t0, 3)
            _module_logger.info(
                "[352路由] template key=%s file=%s method=%s hits=%s 零模型",
                _out["entry_key"], entry.get("file"), entry.get("method"),
                _tpl.get("hits"))
        else:
            # ★降级必须留痕。静默降级会把“模板覆盖不到”这件事藏掉（铁律156）
            _out["lane_effective"] = LANE_LOCAL_MODEL
            _out["lane_downgraded_from"] = LANE_TEMPLATE
            _out["lane_downgrade_why"] = _tpl.get("why")
            _module_logger.info(
                "[352路由] 模板不适用，降级 local_model key=%s why=%s",
                _out["entry_key"], _tpl.get("why"))
    if _pr is None:
        # ★352刀5：模型档才回灌（模板档零模型，读 verdict 是白读）
        _fb = load_feedback_examples(os.path.join(str(_root), "data", "evolution"))
        _out["feedback"] = {_k: _fb.get(_k) for _k in ("schema", "state", "total",
                                                       "real", "used")}
        _out["feedback_examples"] = _fb.get("examples") or []
        if _fb.get("used"):
            _module_logger.info(
                "[352回灌] 注入 %d 条验证样本 key=%s（真实=%d/%d）",
                _fb["used"], _out["entry_key"], _fb["real"], _fb["total"])
        elif _fb.get("state") == "ok" and _fb.get("total"):
            # 有 verdict 但全是测试件 ⇒ 如实标“无可用样本”，不假装没事
            _module_logger.info(
                "[352回灌] 无可用样本 key=%s（verdict %d 条，真实 0 条）",
                _out["entry_key"], _fb["total"])
        _prompt = build_prompt(entry, _src, _fb.get("examples"))
        _out["prompt_chars"] = len(_prompt)
        _tr = _tp(_prompt, model=_model, base_url=_base, timeout=_to)
        _out["elapsed_sec"] = round(time.perf_counter() - _t0, 3)
        _out["lane_effective"] = LANE_LOCAL_MODEL
        if not isinstance(_tr, dict) or not _tr.get("ok"):
            _out["error"] = "transport:%s" % ((_tr or {}).get("err") or "unknown")
            _module_logger.warning("[349生成] generation_failed（模型层）key=%s err=%s",
                                   _out["entry_key"], _out["error"][:200])
            return _out
        _pr = parse_llm_reply(_tr.get("text") or "")
        if not _pr.get("ok"):
            _out["error"] = "parse:%s" % _pr.get("why")
            _module_logger.warning("[349生成] generation_failed（回复不可解析）key=%s why=%s",
                                   _out["entry_key"], _pr.get("why"))
            return _out
    _d = _pr["data"]
    # ★349刀1③：两种输出格式都接（锚点优先，老格式兼容）
    _orig = str(_d.get("original_code") or "")
    _mod = str(_d.get("modified_code") or "")
    _anchor = str(_d.get("anchor") or "")
    if not _orig and _anchor:
        _comp = _m349_compose_from_anchor(_root, entry.get("file"), _anchor,
                                          _d.get("replacement") or "",
                                          method=entry.get("method"))
        _out["anchor_compose"] = {k: _comp.get(k) for k in ("ok", "why", "occurrences")}
        if not _comp.get("ok"):
            # 锚点对不上 ⇒ 这不是"模型没回"，是"回了但对不上原文" ⇒ 交审核链判
            _out["state"] = STATE_GATE_REJECTED
            _out["gate_reasons"] = ["anchor:%s" % _comp.get("why")]
            _out["draft"] = {
                "file": _m349_norm_rel(entry.get("file")),
                "method": str(entry.get("method") or "").strip(),
                "issue_type": entry.get("issue_type"),
                "severity": entry.get("severity"),
                "klass": entry.get("klass"),
                "original_code": "", "modified_code": "",
                "reason": str(_d.get("reason") or "")[:300],
                "dedup_key": make_dedup_key(entry.get("file"), entry.get("method"),
                                            entry.get("issue_type")),
                "raw_anchor": _anchor[:300],
                "source": "patch_generator",
                "generator_version": GENERATOR_VERSION,
                "generated_at": _now,
                "requires_confirmation": True,
                "auto_apply_eligible": False,
                "o3_dead_code": False,
                "gate": "",
            }
            _module_logger.info("[349生成] gate_rejected key=%s 原因=%s",
                                _out["entry_key"], _out["gate_reasons"][0])
            return _out
        _orig = _comp.get("original_code") or ""
        _mod = _comp.get("modified_code") or ""
        _splice_line = _comp.get("line_no")
    _draft = {
        "file": _m349_norm_rel(entry.get("file")),
        "method": str(entry.get("method") or "").strip(),
        "issue_type": entry.get("issue_type"),
        "severity": entry.get("severity"),
        "klass": entry.get("klass"),
        "original_code": _orig,
        "modified_code": _mod,
        # 行号落点（仅锚点路径有；老格式为空 ⇒ splice 退回文本唯一匹配）
        "splice_line": locals().get("_splice_line"),
        "reason": str(_d.get("reason") or "").strip()[:_DESC_MAX_CHARS],
        "dedup_key": make_dedup_key(entry.get("file"), entry.get("method"),
                                    entry.get("issue_type")),
        "source": "patch_generator",
        "generator_version": GENERATOR_VERSION,
        "generated_at": _now,
        "requires_confirmation": True,
        "auto_apply_eligible": False,
        # ★不进 O3 判死池：显式置 False（`_m339_is_o3` 另由 source 判否，双保险）
        "o3_dead_code": False,
        "gate": "",
    }
    _jd = judge_draft(_draft, project_root=_root)
    _draft.pop("_project_root", None)
    _out["gate_checks"] = _jd["checks"]
    if _jd["state"] != STATE_DRAFT_READY:
        _out["state"] = STATE_GATE_REJECTED
        _out["gate_reasons"] = _jd["gate_reasons"]
        _out["draft"] = _draft
        _module_logger.info("[349生成] gate_rejected key=%s 原因=%s",
                            _out["entry_key"], ";".join(_jd["gate_reasons"])[:300])
        return _out
    _draft["gate"] = "generator_evidence_gate:PASS"
    _out["state"] = STATE_DRAFT_READY
    _out["draft"] = _draft
    _module_logger.info(
        "[349生成] draft_ready key=%s model=%s 耗时=%.2fs prompt=%d字 "
        "changed_lines=%s proc_gen=%s",
        _out["entry_key"], _model, _out["elapsed_sec"] or 0.0, _out["prompt_chars"],
        (_jd["checks"].get("diff_minimal") or {}).get("changed"), _out["proc_gen"])
    return _out


def route_draft_to_approval(draft: dict, project_root: str | None = None,
                            *, save: bool = True, also_queue: bool = False) -> dict:
    """把 `draft_ready` 草稿送进**审批分道**（不进 O3 判死池）。

    与 `audit_problem_pool_consumer._route_patch_candidate` 的区别：
    那条路要求条目**自带** `original_code/modified_code`（可能是人写的），
    这条路明确标注 `source="patch_generator"` + `generator_version`，
    使审批人在台账里一眼看出"这是模型草稿"。

    ★`save=False` 只回结构不写盘（隔离验证用）。
    ★落盘走 `PatchManager.save_pending_patch`，其内部有 `guard_write`；
      写不进去时**返回值必须上报**（铁律198）。
    """
    _out: dict = {"ok": False, "written": False, "patch_id": "", "why": ""}
    if not isinstance(draft, dict):
        _out["why"] = "draft_not_dict"
        return _out
    _six = check_evidence_six(draft)
    if not _six["ok"]:
        _out["why"] = "six_fields:%s" % _six["why"]
        return _out
    # ★352刀1②（审批链联动）：漂移候选**不得**进审批分道。
    #   生成器侧已拦一道，这里是第二道——因为 `route_draft_to_approval` 也接**人写/
    #   旧版生成器**产出的 draft，那些不经过 generate_patch_draft。
    #   铁律172：隔离/守卫须守两端（输入侧 + 输出侧）。
    _root_pc = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _pc = precheck_target(_root_pc, draft.get("file"), draft.get("method"))
    _out["precheck"] = _pc
    if _pc["verdict"] in TARGET_DRIFTED_VERDICTS:
        _out["ok"] = False
        _out["why"] = "draft_target_drifted:%s" % _pc["verdict"]
        _module_logger.warning(
            "[352预检] 草稿目标已漂移，拒收不入审批分道: file=%s method=%s why=%s",
            draft.get("file"), draft.get("method"), _pc["why"])
        return _out
    # ★352刀4：审批分档（低危自动过审 / 高危人工）
    _tier = score_approval_tier(draft, _root_pc, lane=draft.get("lane"))
    _out["approval_tier"] = _tier
    _pid = "gen_%s" % str(draft.get("dedup_key") or "")[:16]
    _out["patch_id"] = _pid
    _rec = {
        "id": _pid,
        "file": draft.get("file"),
        "method": draft.get("method"),
        "issue_type": draft.get("issue_type"),
        "severity": draft.get("severity"),
        "klass": draft.get("klass"),
        "original_code": draft.get("original_code"),
        "modified_code": draft.get("modified_code"),
        "reason": draft.get("reason"),
        "dedup_key": draft.get("dedup_key"),
        "description": "本地生成器草稿（349刀1）: %s" % str(draft.get("reason") or "")[:300],
        "source": "patch_generator",
        "entry_source": "patch_generator",
        "generator_version": draft.get("generator_version"),
        "route": "approval_lane",
        # ★352刀4：分档结果覆盖硬编码的 True
        "requires_confirmation": bool(_tier["requires_confirmation"]),
        "auto_apply_eligible": False,
        "o3_dead_code": False,
        # ★352刀4：风险档由分层结果决定（不再恒“中”）
        "risk_level": ("低" if _tier["tier"] == TIER_LOW else "中"),
        "approval_tier": _tier["tier"],
        "approval_tier_score": _tier["score"],
        "approval_tier_reasons": list(_tier["reasons"]),
        "status": "pending",
    }
    _out["patch"] = _rec
    if not save:
        _out["ok"] = True
        _out["why"] = "dry_run"
        return _out
    _root = _root_pc  # 同一次上溯，不重复算、不两份口径
    _path = os.path.join(str(_root), DRAFTS_REL)
    if also_queue:
        # 可选：同时试一次方法级入队口（它的 def 对齐会拒片段，**拒了不算错**，
        #   但必须如实上报，不能因为"另存成功了"就把这次拒绝藏起来）
        try:
            from nucleus.reasoning.PatchManager import PatchManager

            _pm = PatchManager(str(_root))
            _pm.save_pending_patch(_rec)
            _hist = list(getattr(_pm, "patch_history", []) or [])
            _out["queued_method_level"] = any(
                isinstance(x, dict) and x.get("id") == _pid for x in _hist)
        except Exception as _qe:
            silent_exc(_qe, where="patch_generator::route_draft_to_approval also_queue")
            _out["queued_method_level"] = False
    try:
        _rows: list = []
        if os.path.isfile(_path):
            try:
                with open(_path, encoding="utf-8") as _f:
                    _raw = json.load(_f)
                if isinstance(_raw, list):
                    _rows = [x for x in _raw if isinstance(x, dict)]
            except (ValueError, OSError) as _re:
                # 读不出来**不能当空表覆盖**（那会静默丢历史草稿）
                silent_exc(_re, where="patch_generator::route_draft_to_approval read")
                _out["ok"] = False
                _out["why"] = "drafts_unreadable:%s" % type(_re).__name__
                return _out
        _rows = [x for x in _rows if x.get("dedup_key") != _rec["dedup_key"]]
        _rows.append(dict(_rec, drafted_at=time.time(),
                          lane="approval", status="pending_review"))
        _payload = json.dumps({"schema": "patch_generator_drafts/349.v1",
                               "updated_at": time.time(),
                               "rows": _rows},
                              ensure_ascii=False, indent=1)
        _ok_w = True
        try:
            from nucleus.data.write_guard import guard_write

            # ★订正：`guard_write` 只回答"允不允许写"，**它不写盘**。
            #   原先写成 `guard_write(_path, _payload)` —— 第二个位置参数是
            #   `explicit`（不是数据），于是"允许写"被当成"已写"。
            _ok_w = bool(guard_write(_path, component="PatchGenerator"))
        except ImportError as _ie:
            silent_exc(_ie, where="patch_generator::route_draft_to_approval guard")
            _ok_w = True
        if not _ok_w:
            # 守卫拒绝写（生产 data/ 默认只读）⇒ 如实上报，**不当成功**
            _out["ok"] = False
            _out["written"] = False
            _out["why"] = "guard_write_refused"
            _module_logger.warning(
                "[349生成] 草稿**未落盘**（写盘守卫拒绝）path=%s id=%s",
                os.path.relpath(_path, _root), _pid)
            return _out
        # 真正的落盘（守卫只判定，写在这里）
        _dir_p = os.path.dirname(_path)
        if _dir_p:
            os.makedirs(_dir_p, exist_ok=True)
        _tmp = _path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _f.write(_payload)
        os.replace(_tmp, _path)
        with open(_path, encoding="utf-8") as _f:
            _back = json.load(_f)
        _ids = [x.get("id") for x in (_back.get("rows") or []) if isinstance(x, dict)]
        _out["written"] = _pid in _ids
        _out["ok"] = _out["written"]
        _out["why"] = "saved" if _out["written"] else "readback_missing"
        _out["drafts_path"] = os.path.relpath(_path, _root)
        _out["drafts_count"] = len(_ids)
        if _out["written"]:
            _module_logger.warning(
                "[349生成] 草稿入审批分道 id=%s file=%s method=%s "
                "requires_confirmation=%s（不自动应用，不进 O3 判死池）",
                _pid, _rec["file"], _rec["method"], _rec["requires_confirmation"])
            # ★352刀4：分档一行可 grep（不则“为什么这条免了人工”永远要翻代码）
            _module_logger.info(
                "[352审批] tier=%s score=%d changed=%s 原因=%s id=%s",
                _tier["tier"], _tier["score"], _tier["changed_lines"],
                ";".join(_tier["reasons"])[:200] or "无", _pid)
    except Exception as _e:
        silent_exc(_e, where="patch_generator::route_draft_to_approval")
        _out["ok"] = False
        _out["written"] = False
        _out["why"] = "%s: %s" % (type(_e).__name__, str(_e)[:200])
    return _out


def generation_stats(rows: list) -> dict:
    """按三态统计（合格率 / 误报分母都在这里算，**不散落在报告里手算**）。

    合格率 = `draft_ready / (draft_ready + gate_rejected)`
      —— 分母**只含"真产出了候选"的次数**：`generation_failed` 是模型/环境问题，
         把它算进分母会让"模型没跑起来"看起来像"生成质量差"（铁律155/160）。
    另有 `end_to_end_rate = draft_ready / 全部请求` 单独报，两个口径都要有。
    """
    _c = {STATE_DRAFT_READY: 0, STATE_GATE_REJECTED: 0, STATE_GENERATION_FAILED: 0}
    for _r in rows or []:
        _s = str((_r or {}).get("state") or "")
        if _s in _c:
            _c[_s] += 1
    _tot = sum(_c.values())
    _den = _c[STATE_DRAFT_READY] + _c[STATE_GATE_REJECTED]
    return {
        "counts": dict(_c),
        "total": _tot,
        "qualified_rate": round(_c[STATE_DRAFT_READY] / _den, 4) if _den else None,
        "qualified_rate_denominator": _den,
        "end_to_end_rate": round(_c[STATE_DRAFT_READY] / _tot, 4) if _tot else None,
        "note": "qualified_rate 分母不含 generation_failed（模型层失败不是生成质量）",
    }
