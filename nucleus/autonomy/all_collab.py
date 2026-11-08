"""★479批：ALL 协作 —— 多渠道并行出草稿，曈曈用自有判据择优。

## 为什么需要它

477 批把「曈曈修订」接进了补丁构造链（修 LLM 的结构缺陷），但修复率仍是 0。
根因不是「模型不够强」，而是**单模型一次命中「粒度对齐」的概率太低**：

  · 469/470 的判据要求 `original` 必须逐字等于目标方法体、
    `modified` 的 def 行方法名必须等于补丁 method；
  · 单模型经常返回「方法体片段」「改了方法名」「多缩 4 格」。

换成更贵的模型也没用（约束是**格式契约**，不是智力）。真正有效的是
**多模型并行 + 用判据选**：一个模型命中概率 p，N 个并行命中概率 1-(1-p)^N。

## 分工（这就是「曈曈为主、ALL 为辅」）

  · **ALL（外部渠道）**：只负责「出草稿」，不参与评判；
  · **曈曈（本模块的 score_code）**：用自己的 AST + 相似度判据打分、择优，
    全程不问任何模型「哪个好」。

## 判据为什么这么定（踩过的坑）

补丁里的 `original_code` / `modified_code` 是**带 8 格缩进的方法片段**
（不是完整模块），所以：
  · 直接 `ast.parse` 必撞 `IndentationError` ⇒ 必须 flex 解析（原样→dedent→包 class）；
  · 「方法体与原文完全相等」是**补丁格式校验**（470 批的活），不是修复质量判据 ——
    修复必然改变方法体。修复质量该看「**最小改动**」：与原文高度相似但确有差异。
    相似度 ≥0.98 判为「没真改」，<0.5 判为「重写」（风险高），中间段才是好补丁。

## 安全边界

  · 纯旁路：只读补丁、只写自有台账（`data/autonomy/all_collab.jsonl`）；
  · 本模块**不做**任何代码替换 —— 替换决策在调用方（默认关）；
  · 任何异常都吞掉并 `silent_exc` 留痕，绝不影响主链路的补丁生成；
  · 不打印密钥（只打渠道名与模型名）。

开关（config.py）：
  · `ENABLE_M479_ALL_COLLAB`      总开关，默认 False
  · `M479_ALL_COLLAB_CHANNELS`    并行渠道数，默认 3
  · `ENABLE_M479_ALL_COLLAB_PICK` 是否用择优结果替换，默认 False（纯影子）

回滚：把 `ENABLE_M479_ALL_COLLAB` 置 False 即逐字回到 477 行为。
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import re
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nucleus._silent_except import silent_exc

SCHEMA = "479.all_collab.v1"
_LEDGER_NAME = "all_collab.jsonl"
_DEFAULT_LEDGER_DIR = os.path.join("data", "autonomy")
_LOCK = threading.Lock()

# _parse_flex 的「同类错误只留痕一次」去重集合（预期控制流，防刷屏）
_SEEN_PARSE_MISS: set[str] = set()

# 代码块 ```python ... ``` 提取
_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)

# 判据权重（合计 100）
W_PARSE = 35       # 语法可解析
W_FULL_METHOD = 20  # 返回的是完整方法（含 def 行），不是片段
W_MINIMAL = 25     # 最小改动（相似度在合理区间）
W_NO_BARE = 10     # 无裸 except（铁律 4）
W_CHANGED = 10     # 确有改动

SIM_TOO_SIMILAR = 0.98   # ≥ 此值视为「没真改」
SIM_TOO_DIFF = 0.50      # < 此值视为「重写」，风险高


# ────────────────────────── flex 解析（片段也能解析） ──────────────────────────

def _parse_flex(code: str) -> tuple[ast.AST | None, str]:
    """把「可能带缩进的方法片段」解析成 AST。

    依次尝试：① 原样 ② 整体 dedent ③ 包一个临时 class（含 self 的完整方法体）。
    返回 (tree|None, 失败原因)。三种都失败才算语法不通。

    ★这里「解析失败」是**预期控制流**（每次打分都要试 3 种写法，片段必然前两次失败），
    不是系统异常。但 cw2 门禁要求 handler 体直调 silent_exc ⇒ 折中为
    「每种错误类型**只留痕一次**」：既满足可见化、又不刷屏（477c 的教训）。
    """
    _c = str(code or "")
    if not _c.strip():
        return None, "空代码"
    _errs: list[str] = []
    for _tag, _cand in (
        ("raw", _c),
        ("dedent", textwrap.dedent(_c)),
        ("wrapped", "class _M479Wrap:\n" + textwrap.indent(textwrap.dedent(_c), "    ")),
    ):
        try:
            return ast.parse(_cand), ""
        except (SyntaxError, ValueError) as _e:
            _kind = f"{_tag}:{type(_e).__name__}"
            _errs.append(f"{_kind}: {str(_e)[:60]}")
            if _kind not in _SEEN_PARSE_MISS:
                _SEEN_PARSE_MISS.add(_kind)
                silent_exc(_e, where=f"autonomy.all_collab::_parse_flex 变体未命中({_tag},仅首次)",
                           level="debug")
    return None, " | ".join(_errs[:2])


def _find_funcs(code: str) -> list[ast.AST]:
    _tree, _ = _parse_flex(code)
    if _tree is None:
        return []
    return [n for n in ast.walk(_tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _norm_lines(text: str) -> str:
    """统一换行、去行尾空白、去空行、去掉公共缩进。"""
    _rows = [(r.rstrip()) for r in str(text or "").replace("\r\n", "\n").split("\n")]
    _rows = [r for r in _rows if r.strip()]
    if not _rows:
        return ""
    _base = min((len(r) - len(r.lstrip()) for r in _rows), default=0)
    return "\n".join(r[_base:] if len(r) >= _base else r for r in _rows)


def _method_body(code: str, method: str) -> str | None:
    """取指定方法的方法体规范文本。找不到返回 None。

    ★不跳过 docstring：补丁里的 original 往往是「从方法体开头截的片段」，
    可能正好含 docstring。若这里跳、那边不跳，相似度会被无端拉低。
    两边口径一致才可比。
    """
    _m = str(method or "")
    if not _m:
        return None
    for _f in _find_funcs(code):
        if getattr(_f, "name", "") != _m:
            continue
        _body = list(getattr(_f, "body", []) or [])
        if not _body:
            return ""
        _segs: list[str] = []
        for _st in _body:
            try:
                _seg = ast.get_source_segment(code, _st) or ast.unparse(_st)
            except (ValueError, AttributeError, TypeError) as _e:
                silent_exc(_e, where="autonomy.all_collab::_method_body seg", level="debug")
                _seg = ""
            if _seg:
                _segs.append(_seg)
        return _norm_lines("\n".join(_segs))
    return None


def _payload_text(code: str, method: str) -> str:
    """取出「用于比对的内容主体」：有完整方法就取方法体，否则取整体规范化文本。"""
    _b = _method_body(code, method)
    if _b is not None:
        return _b
    return _norm_lines(code)


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def _has_bare_except(code: str) -> bool:
    """有没有 `except:`（本项目铁律 4 明令禁止）。"""
    _tree, _ = _parse_flex(code)
    if _tree is None:
        return False
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.ExceptHandler) and _node.type is None:
            return True
    return False


def extract_code(raw: str) -> str:
    """从 LLM 回复里取出代码。优先 ```python 围栏，其次整段原文。"""
    _s = str(raw or "")
    _m = _FENCE.search(_s)
    if _m:
        return _m.group(1).strip("\n")
    return _s.strip("\n")


def score_code(code: str, original: str = "", method: str = "") -> dict:
    """曈曈判据打分（0~100）。**完全不问模型**。

    分项：
      parse_ok     35  能 flex 解析（片段/完整方法都算过）
      full_method  20  含 `def <method>`（不是裸片段）—— 470 判据的硬要求
      minimal      25  与原文相似度落在 [0.50, 0.98)：改了但没重写
      no_bare      10  无裸 except
      changed      10  与原文确有差异
    """
    _code = str(code or "")
    _m = str(method or "")
    _out: dict[str, Any] = {
        "score": 0, "parse_ok": False, "full_method": False,
        "similarity": None, "minimal": False, "no_bare_except": True,
        "changed": False, "parse_error": "", "reason": "",
    }
    if not _code.strip():
        _out["reason"] = "空代码"
        return _out

    _tree, _err = _parse_flex(_code)
    _out["parse_ok"] = _tree is not None
    _out["parse_error"] = _err
    if _tree is None:
        _out["reason"] = f"语法不通（{_err[:60]}）"
        return _out
    _score = W_PARSE
    _reasons: list[str] = []

    # full_method：必须能定位到 def <method>
    if _m:
        _has = any(getattr(f, "name", "") == _m for f in _find_funcs(_code))
        _out["full_method"] = _has
        if _has:
            _score += W_FULL_METHOD
        else:
            _reasons.append(f"不是完整方法（找不到 def {_m}）")
    else:
        # 未指定 method ⇒ 该项不判（等价给分，避免类别误伤）
        _out["full_method"] = True
        _score += W_FULL_METHOD

    # minimal：最小改动
    if original:
        _a = _payload_text(original, _m)
        _b = _payload_text(_code, _m)
        _sim = _similarity(_a, _b)
        _out["similarity"] = _sim
        if SIM_TOO_DIFF <= _sim < SIM_TOO_SIMILAR:
            _out["minimal"] = True
            _score += W_MINIMAL
        elif _sim >= SIM_TOO_SIMILAR:
            # 相似度过高 ⇒ 基本没改（除 issue 本身外），风险：空补丁
            _score += W_MINIMAL // 5
            _reasons.append(f"与原文几乎一致（相似度 {_sim}），可能未真正修复")
        else:
            _score += int(W_MINIMAL * max(0.0, _sim / SIM_TOO_DIFF))
            _reasons.append(f"改动过大/疑似重写（相似度 {_sim}）")
    else:
        _score += W_MINIMAL

    if not _has_bare_except(_code):
        _score += W_NO_BARE
    else:
        _out["no_bare_except"] = False
        _reasons.append("引入裸 except（违反铁律 4）")

    _chg = (not original) or (_payload_text(_code, _m) != _payload_text(original, _m))
    _out["changed"] = _chg
    if not _chg:
        _reasons.append("与原文无差异（空改动）")
    else:
        _score += W_CHANGED

    _out["score"] = int(max(0, min(100, _score)))
    # ★多因聚合：一个补丁常同时踩多个坑（改动过大 + 裸 except）。
    #   只留首个原因会让运维以为改一处就够，实际还得再改。
    _out["reason"] = "；".join(_reasons)
    return _out


# ────────────────────────── 渠道挑选与并发生成 ──────────────────────────

def pick_channels(n: int = 3, exclude: str = "") -> list[dict]:
    """从活跃渠道池里挑 n 条「有 key」的渠道；exclude 为要跳过的渠道名。"""
    try:
        import config as _cfg
    except ImportError as _e:
        silent_exc(_e, where="autonomy.all_collab::pick_channels config", level="debug")
        return []
    try:
        _chs = list(_cfg.get_active_channels() or [])
    except (AttributeError, TypeError, ValueError) as _e:
        silent_exc(_e, where="autonomy.all_collab::pick_channels active", level="debug")
        return []

    try:
        from nucleus.reasoning.channel_client import resolve_api_key as _rak
    except ImportError as _e:
        silent_exc(_e, where="autonomy.all_collab::pick_channels import", level="debug")
        return []

    _out: list[dict] = []
    _ex = str(exclude or "")
    for _c in _chs:
        if not isinstance(_c, dict):
            continue
        if _ex and str(_c.get("name") or "") == _ex:
            continue
        try:
            if not _rak(_c):
                continue
        except (KeyError, TypeError, ValueError, AttributeError) as _e:
            silent_exc(_e, where="autonomy.all_collab::pick_channels key", level="debug")
            continue
        _out.append(_c)
        if len(_out) >= max(1, int(n)):
            break
    return _out


def generate_one(channel: dict, prompt: str, *, system: str = "",
                 timeout: float = 60.0, max_tokens: int = 2048) -> dict:
    """单渠道出一份草稿（异常一律吞掉，返回带 error 的结构）。"""
    _t0 = time.time()
    _rec: dict[str, Any] = {
        "channel": str((channel or {}).get("name") or "?"),
        "model": str((channel or {}).get("model") or ""),
        "ok": False, "code": "", "raw_len": 0, "elapsed": 0.0, "error": None,
    }
    try:
        from nucleus.reasoning.channel_client import call_channel
        _r = call_channel(channel, prompt, timeout=timeout,
                          max_tokens=max_tokens, system=system)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError, KeyError) as _e:
        silent_exc(_e, where="autonomy.all_collab::generate_one", level="debug")
        _rec["error"] = f"{type(_e).__name__}: {str(_e)[:120]}"
        _rec["elapsed"] = round(time.time() - _t0, 2)
        return _rec

    _rec["elapsed"] = round(time.time() - _t0, 2)
    if not isinstance(_r, dict):
        _rec["error"] = "call_channel 返回非 dict"
        return _rec
    _rec["error"] = _r.get("error")
    _txt = str(_r.get("text") or "")
    _rec["raw_len"] = len(_txt)
    _rec["ok"] = bool(_r.get("ok")) and bool(_txt.strip())
    if _rec["ok"]:
        _rec["code"] = extract_code(_txt)
    return _rec


def generate_candidates(prompt: str, *, original: str = "", method: str = "",
                        n: int = 3, exclude: str = "", system: str = "",
                        timeout: float = 60.0, max_tokens: int = 2048) -> dict:
    """并行调 n 条渠道出草稿，逐条打分。返回按分数降序的候选表。"""
    _t0 = time.time()
    _chs = pick_channels(n, exclude=exclude)
    if not _chs:
        return {"ok": False, "error": "无可用渠道（池为空或缺 key）",
                "candidates": [], "best": None, "elapsed": 0.0,
                "channels_tried": []}

    _rows: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=min(len(_chs), 6)) as _ex:
            _futs = [_ex.submit(generate_one, _c, prompt, system=system,
                                timeout=timeout, max_tokens=max_tokens)
                     for _c in _chs]
            for _f in _futs:
                try:
                    _rows.append(_f.result())
                except (RuntimeError, ValueError, TypeError) as _e:
                    silent_exc(_e, where="autonomy.all_collab::future", level="debug")
    except (RuntimeError, OSError, ValueError) as _e:
        silent_exc(_e, where="autonomy.all_collab::generate_candidates", level="debug")

    for _r in _rows:
        _sc = score_code(_r.get("code") or "", original=original, method=method)
        _r["score"] = _sc["score"]
        _r["score_detail"] = _sc

    _rows.sort(key=lambda x: (-int(x.get("score") or 0), float(x.get("elapsed") or 0)))
    # 达标线 65：parse(35)+full_method(20)+no_bare(10) 即已 65 ——
    #   即「语法通 + 是完整方法 + 无裸 except」，这是能进补丁链的**最低门槛**。
    _best = None
    for _r in _rows:
        if int(_r.get("score") or 0) >= 65:
            _best = _r
            break
    return {
        "ok": bool(_rows),
        "candidates": _rows,
        "best": _best,
        "elapsed": round(time.time() - _t0, 2),
        "channels_tried": [r.get("channel") for r in _rows],
    }


# ────────────────────────── 台账 ──────────────────────────

def _ledger_path() -> str:
    try:
        import config as _cfg
        _d = str(getattr(_cfg, "M479_ALL_COLLAB_LEDGER_DIR", "") or _DEFAULT_LEDGER_DIR)
    except (ImportError, AttributeError, TypeError) as _e:
        silent_exc(_e, where="autonomy.all_collab::_ledger_path", level="debug")
        _d = _DEFAULT_LEDGER_DIR
    return os.path.join(_d, _LEDGER_NAME)


def _is_default_prod_path(path: str) -> bool:
    return os.path.abspath(path) == os.path.abspath(
        os.path.join(_DEFAULT_LEDGER_DIR, _LEDGER_NAME))


def note_round(record: dict, ledger_path: str | None = None) -> dict:
    """写一行台账（append-only）。pytest 下不写默认生产路径（防测试污染活体）。"""
    _p = ledger_path or _ledger_path()
    if "pytest" in sys.modules and _is_default_prod_path(_p):
        return {"ok": True, "skipped": "pytest_guard", "path": _p}
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _line = json.dumps(record, ensure_ascii=False, default=str)
        with _LOCK, open(_p, "a", encoding="utf-8") as _f:
            _f.write(_line + "\n")
        return {"ok": True, "path": _p}
    except (OSError, TypeError, ValueError) as _e:
        silent_exc(_e, where="autonomy.all_collab::note_round", level="debug")
        return {"ok": False, "error": f"{type(_e).__name__}: {str(_e)[:120]}"}


def ledger_stats(ledger_path: str | None = None) -> dict:
    """台账汇总：总轮次 / 有候选 / 有达标 / 各渠道命中分布。"""
    _p = ledger_path or _ledger_path()
    _rows: list[dict] = []
    try:
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8", errors="replace") as _f:
                for _ln in _f:
                    _ln = _ln.strip()
                    if not _ln:
                        continue
                    try:
                        _rows.append(json.loads(_ln))
                    except (json.JSONDecodeError, ValueError) as _e:
                        silent_exc(_e, where="autonomy.all_collab::ledger parse",
                                   level="debug")
    except OSError as _e:
        silent_exc(_e, where="autonomy.all_collab::ledger_stats", level="debug")
        return {"total": 0, "reason": type(_e).__name__}

    _by_ch: dict[str, dict] = {}
    _hit = 0
    _with_cand = 0
    for _r in _rows:
        _cands = _r.get("candidates") or []
        if _cands:
            _with_cand += 1
        if _r.get("best_channel"):
            _hit += 1
        for _c in _cands:
            _n = str(_c.get("channel") or "?")
            _b = _by_ch.setdefault(_n, {"tried": 0, "ok": 0, "score_sum": 0,
                                        "best": 0, "picked": 0})
            _b["tried"] += 1
            _b["ok"] += int(bool(_c.get("ok")))
            _b["score_sum"] += int(_c.get("score") or 0)
            _b["best"] = max(_b["best"], int(_c.get("score") or 0))
            _b["picked"] += int(bool(_c.get("picked")))
    for _b in _by_ch.values():
        _b["avg_score"] = round(_b["score_sum"] / max(1, _b["tried"]), 1)
        _b.pop("score_sum", None)
    _n_total = len(_rows) or 1
    return {
        "schema": SCHEMA,
        "total": len(_rows),
        "with_candidates": _with_cand,
        "best_found": _hit,
        "hit_rate": round(_hit / _n_total, 4),
        "by_channel": _by_ch,
    }
