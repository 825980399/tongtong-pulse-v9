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

_DEAD_LOGGED: set = set()
_LOWRATE_LOGGED: set = set()


def _channel_stats(window: int = 200) -> dict:
    """从台账最近 window 轮统计各渠道 [tried, ok]（死渠道与低效渠道共用一份口径）。"""
    _stat: dict = {}
    _p = _ledger_path()
    if not os.path.isfile(_p):
        return _stat
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _lines = [x for x in _f if x.strip()]
        for _ln in _lines[-max(1, int(window)):]:
            try:
                _r = json.loads(_ln)
            except (json.JSONDecodeError, ValueError) as _e:
                silent_exc(_e, where="autonomy.all_collab::_channel_stats row",
                           level="debug")
                continue
            for _c in (_r.get("candidates") or []):
                if not isinstance(_c, dict):
                    continue
                _n = str(_c.get("channel") or "")
                if not _n:
                    continue
                _b = _stat.setdefault(_n, [0, 0])
                _b[0] += 1
                _b[1] += int(bool(_c.get("ok")))
    except OSError as _e:
        silent_exc(_e, where="autonomy.all_collab::_channel_stats open", level="debug")
    return _stat


def _low_rate_channels(window: int = 200, min_tries: int = 10,
                       min_rate: float = 0.20) -> set:
    """★479 收尾①：成功率过低的渠道（近 WINDOW 轮 tried >= MIN 且 ok/tried < MIN_RATE）。

    实测依据（44 轮）：zhipu 4.1%（429×36）、qwen37-plus 5.6%（timeout×17）、
    modelscope 0%（timeout×10）—— 28 次读超时里 27 次来自后两者（96%）。
    这类渠道占了并行名额却几乎不产出，**降路数会把好渠道一起砍掉**（见模块 docstring），
    所以正确做法是按成功率**换掉坏名额**，而不是减少名额。

    只看最近窗口 ⇒ 额度恢复/换模型后自动复活（与死渠道同一机制，不会永久拉黑）。
    """
    _out: set = set()
    try:
        import config as _cfg
        if not getattr(_cfg, "M479_ALL_COLLAB_SKIP_LOW_RATE", True):
            return _out
        _win = int(getattr(_cfg, "M479_ALL_COLLAB_RATE_WINDOW", window) or window)
        _min = int(getattr(_cfg, "M479_ALL_COLLAB_RATE_MIN_TRIES", min_tries) or min_tries)
        _rate = float(getattr(_cfg, "M479_ALL_COLLAB_MIN_OK_RATE", min_rate) or min_rate)
    except (ImportError, AttributeError, TypeError, ValueError) as _e:
        silent_exc(_e, where="autonomy.all_collab::_low_rate_channels config",
                   level="debug")
        _win, _min, _rate = window, min_tries, min_rate

    for _n, (_tried, _ok) in _channel_stats(_win).items():
        if _tried < _min:
            continue
        if (_ok / float(_tried)) >= _rate:
            continue
        _out.add(_n)
        if _n not in _LOWRATE_LOGGED:
            _LOWRATE_LOGGED.add(_n)
            try:
                from nucleus.logger import get_logger as _gl
                _gl("autonomy.all_collab").info(
                    "[479ALL协作] 跳过低成功率渠道 %s（近 %d 轮 %d 次成功 %d 次 = %.1f%%，门槛 %.0f%%）",
                    _n, _win, _tried, _ok, 100.0 * _ok / max(1, _tried), _rate * 100)
            except (ImportError, AttributeError, TypeError, ValueError) as _e:
                silent_exc(_e, where="autonomy.all_collab::_low_rate_channels log",
                           level="debug")
    return _out


def _dead_channels(window: int = 200, min_tries: int = 10) -> set:
    """从台账最近 window 轮里找出「零产出」渠道（数据驱动，不硬编码名单）。

    判据刻意保守：**tried >= min_tries 且 ok == 0** 才判死。
    理由：只要有过一次成功就说明渠道是活的（可能只是限流/慢），不能剔；
    而 21 次全空说明是「模型档位选错」（如推理模型把 token 花在思考上、
    content 恒空），继续调用纯属烧配额。

    只看最近 window 轮 ⇒ 换模型/修好后旧数据滑出窗口，自动复活，不会永久拉黑。
    """
    _out: set = set()
    try:
        import config as _cfg
        if not getattr(_cfg, "M479_ALL_COLLAB_SKIP_DEAD", True):
            return _out
        _win = int(getattr(_cfg, "M479_ALL_COLLAB_DEAD_WINDOW", window) or window)
        _min = int(getattr(_cfg, "M479_ALL_COLLAB_DEAD_MIN_TRIES", min_tries) or min_tries)
    except (ImportError, AttributeError, TypeError, ValueError) as _e:
        silent_exc(_e, where="autonomy.all_collab::_dead_channels config", level="debug")
        _win, _min = window, min_tries

    # ★479 收尾①：统计逻辑抽到 _channel_stats（与低效渠道过滤共用一份口径）
    _stat = _channel_stats(_win)
    if not _stat:
        return _out

    for _n, (_tried, _ok) in _stat.items():
        if _tried >= _min and _ok == 0:
            _out.add(_n)
            if _n not in _DEAD_LOGGED:
                _DEAD_LOGGED.add(_n)
                try:
                    from nucleus.logger import get_logger as _gl
                    _gl("autonomy.all_collab").info(
                        "[479ALL协作] 跳过零产出渠道 %s（近 %d 轮 %d 次调用 0 次成功）",
                        _n, _win, _tried)
                except (ImportError, AttributeError, TypeError, ValueError) as _e:
                    silent_exc(_e, where="autonomy.all_collab::_dead_channels log", level="debug")
    return _out


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

    _ex = str(exclude or "")
    _dead = _dead_channels()
    _lowrate = _low_rate_channels()

    def _collect(_skip: set) -> list[dict]:
        _acc: list[dict] = []
        for _c in _chs:
            if not isinstance(_c, dict):
                continue
            if _ex and str(_c.get("name") or "") == _ex:
                continue
            if _skip and str(_c.get("name") or "") in _skip:
                continue
            try:
                if not _rak(_c):
                    continue
            except (KeyError, TypeError, ValueError, AttributeError) as _e:
                silent_exc(_e, where="autonomy.all_collab::pick_channels key",
                           level="debug")
                continue
            _acc.append(_c)
            if len(_acc) >= max(1, int(n)):
                break
        return _acc

    _out = _collect(_dead | _lowrate)
    if _out:
        return _out
    # ★安全回落：若「零产出 + 低成功率」一起筛之后一个都不剩，退回只排除零产出。
    #   理由：统计口径不能把渠道池全关死（那会让 ALL 协作永久失效，比低效更糟）。
    _out = _collect(_dead)
    if _out:
        try:
            from nucleus.logger import get_logger as _gl
            _gl("autonomy.all_collab").warning(
                "[479ALL协作] 低成功率过滤后无渠道可用 ⇒ 回落为只排除零产出渠道（%d 条）",
                len(_out))
        except (ImportError, AttributeError, TypeError, ValueError) as _e:
            silent_exc(_e, where="autonomy.all_collab::pick_channels 回落日志",
                       level="debug")
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
    # ★479 收尾：best 必须同时满足「分数 >= 65」与「**真的是完整方法**」。
    #   原实现只判分数，而 65 分可由 parse(35)+no_bare(10)+changed(10)+minimal(10)
    #   凑出 —— 不含 full_method(20)。实测 22 轮里有大量 78 分但 full_method=false
    #   的候选被选为 best；这类候选进 469 粒度对齐必被拦（470 要求 original==body
    #   且 modified 的 def 行方法名匹配），替换等于白烧 40s 且污染日志。
    #   现在 full_method=false 的候选**不进 best**，只在台账里留档供分析。
    _best = None
    for _r in _rows:
        _d = _r.get("score_detail") or {}
        if int(_r.get("score") or 0) >= 65 and _d.get("full_method") is True:
            _best = _r
            break
    _usable = sum(1 for _r in _rows
                  if int(_r.get("score") or 0) >= 65
                  and (_r.get("score_detail") or {}).get("full_method") is True)
    return {
        "ok": bool(_rows),
        "candidates": _rows,
        "best": _best,
        "usable": _usable,
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
    # ★479 收尾：把「分数够」与「真能用（完整方法）」分开统计。
    #   只看 best_score 会高估协作价值（78 分但无 def 行的候选进不了下游）。
    _cand_total = 0
    _cand_usable = 0
    _rounds_with_usable = 0
    for _r in _rows:
        _cands = _r.get("candidates") or []
        if _cands:
            _with_cand += 1
        if _r.get("best_channel"):
            _hit += 1
        _r_usable = 0
        for _c in _cands:
            _cand_total += 1
            _cd = _c.get("detail") or {}
            if (int(_c.get("score") or 0) >= 65
                    and _cd.get("full_method") is True):
                _cand_usable += 1
                _r_usable += 1
        if _r_usable:
            _rounds_with_usable += 1
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
        "candidates_total": _cand_total,
        "candidates_usable": _cand_usable,
        "usable_rate": round(_cand_usable / max(1, _cand_total), 4),
        "rounds_with_usable": _rounds_with_usable,
        "rounds_usable_rate": round(_rounds_with_usable / _n_total, 4),
        "by_channel": _by_ch,
    }
