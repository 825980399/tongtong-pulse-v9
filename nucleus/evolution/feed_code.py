# -*- coding: utf-8 -*-
"""★467批：补丁生成侧的「喂料」真源（纯函数，不依赖任何业务模块）。

病因（T0 实测 2026-11-06，全部是真实读数，不是推测）：

1. `SafeEvolutionExecutor._generate_llm_patch` 把
   `self_inspector.get_method_body(...)["body"]` 直接当 `original_code` 喂给 LLM。
   对 `PulseLiver._compress_group` 实测：body **12514 字符**，首行是
   `        if len(nodes) > 40:`（8 空格缩进），**不含 def 行**；
   而同一次调用返回的 `args` 是 `prefix: str, nodes: list[PulseNode]`
   —— **既无 `self`、也无 `-> bool`**，所以**不能**凭 args 拼 def 行，
   必须回源文件取原文。
2. 下游 prompt 还有两处硬上限：单文件 `code_snippet[:2000]`（只给到 16%，
   且是从中间硬切）、多文件 `_m428_self_contained_prefix(original_code, 3000)`
   （24%）。模型于是「照着输入的形状」回一段方法体。
3. `PatchManager._m280_converge_pending` 用 `_m280_extract_def_name(modified_code)`
   抽首个 def 名，抽不到即 `no_def` ⇒ 作废。`converge_voided.jsonl` 里
   `PulseLiver.py::_compress_group` 连续 **63 次**作废，**100% 来自
   `patch_multi_*`**（即本模块要修的那条多文件生成路径）。

本模块只提供四个纯口：
* `method_block()`      —— 从源文件取**含 def 行的完整方法原文**（保缩进/行尾）
* `struct_defects()`    —— 纯 AST 判定结构缺陷（毫秒级，不跑测试、不写文件）
* `extract_def_name()`  —— 与 `PatchManager._m280_extract_def_name` 同算法的首个 def 名
* `FULL_DEF_REQUIREMENT`—— 445 硬性要求的**唯一真源**
  （此前只有 `SafeEvolutionExecutor` 一份，而多文件生成走
  `LLMEvolutionEngine`，永远读不到 ⇒ 445 的开关实际只罩住了单文件路径）
"""
from __future__ import annotations

import ast
import re
import textwrap

from nucleus._silent_except import silent_exc

#: ★467：445 硬性要求的**唯一真源**。
#:   文本逐字沿用 445 原句（`tests/test_m445_k2_feedback.py` 断言它出现在 prompt 里），
#:   只是把存放位置从 `SafeEvolutionExecutor` 挪到这里，供单文件与多文件两条路径共用。
FULL_DEF_REQUIREMENT = (
    "【硬性要求 · 445】必须输出**完整方法定义**："
    "第一行就是 `def <方法名>(self, ...):`"
    "（含 self、完整参数列表与类型标注），不要只给方法体。"
    "上一版因缺少 def 签名被判结构缺陷作废。"
)

#: 与 `PatchManager._m280_extract_def_name` **逐字同一正则**。
#:   两处若漂移，就会出现「生成侧自检说合格、入队侧判 no_def」的再次分叉。
#:   单测 `test_m467_feed_code.py::test_def_name_parity_with_patch_manager`
#:   直接拿 PatchManager 的真函数做基准对拍（铁律 30：基准来自另一个入口）。
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)

#: 467 起 prompt 里代码段的默认上限（字符）。
#:   16000 是按实测标本定的：`_compress_group` 完整方法约 12.7KB，
#:   16000 能一次装下；再大则对 `glm-4-flash` 这类渠道的首字延迟不划算。
_DEFAULT_PROMPT_CODE_LIMIT = 16000


def _cfg_int(name: str, default: int) -> int:
    """读 config 整数项。**只有缺失/None/非法才算缺省**（0 是合法值）。"""
    try:
        import config as _c
        _v = getattr(_c, name, None)
        if _v is None:
            return int(default)
        return int(_v)
    except Exception as _e:
        silent_exc(_e, where="feed_code::_cfg_int(%s)" % name, level="debug")
        return int(default)


def _cfg_on(name: str, default: bool = True) -> bool:
    """读 config 布尔灰度开关；缺失按 `default`。"""
    try:
        import config as _c
        _v = getattr(_c, name, None)
        if _v is None:
            return bool(default)
        return bool(_v)
    except Exception as _e:
        silent_exc(_e, where="feed_code::_cfg_on(%s)" % name, level="debug")
        return bool(default)


def prompt_code_limit() -> int:
    """prompt 里代码段的字符上限。467 前是 2000（单文件）/3000（多文件）。"""
    _v = _cfg_int("M467_PROMPT_CODE_LIMIT", _DEFAULT_PROMPT_CODE_LIMIT)
    return _v if _v > 0 else _DEFAULT_PROMPT_CODE_LIMIT


def feed_with_def_on() -> bool:
    """刀1 开关：喂给 LLM 的 original_code 是否升级为「含 def 的完整方法」。"""
    return _cfg_on("ENABLE_M467_FEED_WITH_DEF", True)


def struct_gate_on() -> bool:
    """刀3 开关：生成后是否做 AST 结构自检 + 有界重生成。"""
    return _cfg_on("ENABLE_M467_STRUCT_GATE", True)


def max_regen() -> int:
    """刀3：结构不过时最多回灌重生成几次（0 = 只判定不重生成）。"""
    return max(0, _cfg_int("M467_STRUCT_REGEN_MAX", 2))


def unfixable_streak() -> int:
    """刀5：同一 (file,method) 连续几次结构缺陷即判「不可修」并退出。"""
    return max(1, _cfg_int("M467_UNFIXABLE_STREAK", 5))


def extract_def_name(code: str) -> str:
    """取代码里**第一个** def 名；无 def 返回空串。

    与 `PatchManager._m280_extract_def_name` 同算法同正则（对拍见单测）。
    """
    if not code or not isinstance(code, str):
        return ""
    try:
        _m = _DEF_RE.search(code)
        return _m.group(1) if _m else ""
    except Exception as _e:
        silent_exc(_e, where="feed_code::extract_def_name", level="debug")
        return ""


def method_block(file_path: str, method_name: str, line_number: int = 0) -> str:
    """返回**含 def 行的完整方法原文**；取不到返回空串。

    `line_number` 沿用 `get_method_body` 的 **0-based** 语义，仅在
    「同名方法出现多次」时用于就近消歧，不是唯一定位依据（同文件里
    `def foo` 可能出现在多个类中）。

    返回值与文件内容**逐字一致**（含原缩进、原行尾、装饰器行），
    因此可以直接作为 `full_content.replace(original_code, modified_code)`
    的锚点 —— 换成「按 args 拼 def 行」则做不到：实测 `args` 丢了
    `self` 与 `-> bool`，拼出来的文本在文件里永远定位不到。
    """
    if not file_path or not method_name:
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as _f:
            _text = _f.read()
    except OSError as _e:
        silent_exc(_e, where="feed_code::method_block 读取 %s" % file_path,
                   level="debug")
        return ""
    if not _text.strip():
        return ""
    try:
        _tree = ast.parse(_text)
    except SyntaxError as _e:
        silent_exc(_e, where="feed_code::method_block 解析 %s" % file_path,
                   level="debug")
        return ""
    _want = str(method_name)
    _hits = [
        _n for _n in ast.walk(_tree)
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == _want
    ]
    if not _hits:
        return ""
    if len(_hits) > 1:
        _target = int(line_number or 0) + 1
        _hits.sort(key=lambda _n: abs(_n.lineno - _target))
    _node = _hits[0]
    _lines = _text.split("\n")
    _start = max(0, int(_node.lineno) - 1)
    for _d in getattr(_node, "decorator_list", None) or []:
        _start = min(_start, max(0, int(_d.lineno) - 1))
    _end = getattr(_node, "end_lineno", None) or len(_lines)
    if _end <= _start:
        return ""
    return "\n".join(_lines[_start:_end])


def _parses(code: str) -> bool:
    """这段代码能否独立成为合法 Python（纯判定，不改内容、不写文件）。

    ★为什么必须先归一化行尾：`textwrap.dedent` 用 `^[ \t]+$` 认「纯空白行」，
      而 **CRLF 文件的空行是 `\r`** —— 它匹配不上，于是前导空白被算成 0，
      dedent 一字不剥。实测 `PulseLiver.py`（4079 CRLF / 0 裸 LF）的
      `_compress_group` 就这样被判成 `unexpected indent@1`。
      这不是假设，是本批 T0 撞到的真失败。

    ★为什么失败后要包一层 class：生成片段可能是「def 行 4 空格 + 方法体 8 空格」，
      dedent 之后本应顶格；但若模型给的是「def 行 0 空格 + 方法体 4 空格」，
      包进类体同样合法。两条路都试，避免把「缩进约定不同」误判成语法错。
    """
    _n = str(code or "").replace("\r\n", "\n").replace("\r", "\n")
    if not _n.strip():
        return False
    for _cand in (textwrap.dedent(_n), _n, "class _M467Holder:\n" + _n):
        try:
            ast.parse(_cand)
            return True
        except SyntaxError as _e:
            silent_exc(_e, where="feed_code::_parses 候选形态", level="debug")
    return False


def struct_defects(code: str, method_name: str = "") -> list[str]:
    """纯 AST 判定一段生成代码的**结构缺陷**；空列表 = 结构合格。

    为什么不用「跑一遍测试」来判：`no_def` / 语法错 / def 名错配这三类
    都能在毫秒内判定，而跑副本验证要几十秒到分钟级。把便宜判据放前面，
    才可能在一次生成里回灌重试 2 次而不显著拖慢一轮进化。

    判据与入队侧 `_m280_converge_pending` **对齐**（错配 / 无 def），
    另外多查一条 `syntax_error` —— 入队侧对语法错是靠后续 `ast.parse` 拦的，
    这里提前拦能少走一趟完整验证。
    """
    _s = str(code or "")
    if not _s.strip():
        return ["empty"]
    _out: list[str] = []
    _first = extract_def_name(_s)
    _method = str(method_name or "").strip()
    if _method and not _first:
        _out.append("no_def")
    elif _method and _first != _method:
        _out.append("def_name_mismatch:%s" % _first)
    if not _parses(_s):
        _out.append("syntax_error")
    return _out


def defect_hint(defects: list[str], method_name: str = "") -> str:
    """把结构缺陷清单转成给 LLM 看的**可执行**回灌说明。"""
    _items = [str(_d) for _d in (defects or []) if _d]
    if not _items:
        return ""
    _lines = ["【上一版结构缺陷 · 必须修正】生成的代码未通过结构自检："]
    for _d in _items:
        if _d == "empty":
            _lines.append("- 输出为空：必须给出完整代码，不要只给解释。")
        elif _d == "no_def":
            _lines.append(
                "- 缺少 def 签名：`modified_code` 的第一行必须是与目标同名的"
                " `def %s(self, ...):`，不要只给方法体。" % (method_name or "<方法名>"))
        elif _d.startswith("def_name_mismatch:"):
            _lines.append(
                "- def 名错配：生成的是 `%s`，而目标方法是 `%s`；"
                "必须沿用目标方法名。" % (_d.split(":", 1)[1], method_name or "?"))
        elif _d.startswith("syntax_error:"):
            _lines.append(
                "- 语法错误：%s。注意中文标点、未闭合的 try/if/def 块都会被判语法错。"
                % _d.split(":", 1)[1])
        else:
            _lines.append("- %s" % _d)
    _lines.append(
        "请**只输出修正后的完整 JSON**，`original_code` 与 `modified_code` "
        "都必须是语法自洽的完整语句块。")
    return "\n".join(_lines)
