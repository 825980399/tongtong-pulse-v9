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
import json
import os
import time
from typing import Any, Callable

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PatchGenerator")

#: 生成器版本（写进每份草稿，便于按版本统计合格率；改 prompt/解析规则必须升版）
GENERATOR_VERSION = "349.d1.v1"

# ---- ★三态（生成侧唯一口径）----
STATE_DRAFT_READY = "draft_ready"
STATE_GATE_REJECTED = "gate_rejected"
STATE_GENERATION_FAILED = "generation_failed"

STATES = (STATE_DRAFT_READY, STATE_GATE_REJECTED, STATE_GENERATION_FAILED)

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


def build_prompt(entry: dict, source: dict) -> str:
    """构造生成提示词（**确定性**，同一条目同一次输入 ⇒ 同一 prompt）。

    确定性很重要：合格率要能按版本比较，prompt 里不能塞时间戳/随机数。
    """
    _desc = str(entry.get("desc") or "")[:_DESC_MAX_CHARS]
    return (
        "你是 Python 代码修复助手。给定一条静态审计问题与相关源码，输出**最小修复补丁**。\n"
        "只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码围栏。\n"
        'JSON 字段：{"anchor": "...", "replacement": "...", "reason": "..."}\n'
        "约束：\n"
        "1. anchor = 源码片段里**原样的一行**（逐字复制，含原有缩进，不要改任何字符）。\n"
        "2. replacement = 用来替换 anchor 的那 1-3 行（含缩进，缩进量必须与 anchor 相同）。\n"
        "3. reason 说明改了什么、为什么，中文，不超过 120 字。\n"
        "4. 若这个问题无法通过改代码解决（协议接口/环境缺陷/需人工决策），"
        "把 anchor 与 replacement 都输出为空字符串，reason 里写明原因。\n"
        "问题：issue_type=%s severity=%s klass=%s\n"
        "位置：file=%s method=%s line=%s\n"
        "描述：%s\n"
        "源码片段（%s，行 %s-%s）：\n%s\n"
    ) % (
        entry.get("issue_type"), entry.get("severity"), entry.get("klass"),
        entry.get("file"), entry.get("method"), entry.get("line"),
        _desc,
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
    try:
        import ast as _ast
        _tree = _ast.parse(_code)
    except SyntaxError as _se:
        _module_logger.info("[350生成] 整方法契约 AST 不通过: %s", _se)
        return {"ok": False, "mode": "method", "why": "syntax_error:%s" % _se,
                "defs": [], "changed_lines": None}
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
            "changed_lines": _n_changed}


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
    _src = extract_source_snippet(_root, entry.get("file"), entry.get("method"),
                                  entry.get("line"))
    _out["source_ok"] = bool(_src.get("ok"))
    _out["source_why"] = _src.get("why")
    if not _src.get("ok"):
        _out["error"] = "source_unavailable:%s" % _src.get("why")
        _module_logger.warning("[349生成] 取不到源码片段，不猜测: key=%s file=%s why=%s",
                               _out["entry_key"], entry.get("file"), _src.get("why"))
        return _out
    _prompt = build_prompt(entry, _src)
    _out["prompt_chars"] = len(_prompt)
    _tr = _tp(_prompt, model=_model, base_url=_base, timeout=_to)
    _out["elapsed_sec"] = round(time.perf_counter() - _t0, 3)
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
        _out["entry_key"], _model, _out["elapsed_sec"] or 0.0, len(_prompt),
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
        "requires_confirmation": True,
        "auto_apply_eligible": False,
        "o3_dead_code": False,
        "risk_level": "中",
        "status": "pending",
    }
    _out["patch"] = _rec
    if not save:
        _out["ok"] = True
        _out["why"] = "dry_run"
        return _out
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
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
                "requires_confirmation=True（不自动应用，不进 O3 判死池）",
                _pid, _rec["file"], _rec["method"])
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
