# -*- coding: utf-8 -*-
"""374 刀8 单测：d5 判据改「主路径唯一写路径裸写」口径（三级兜底层豁免）。

票 `T-371-d5判据主路径口径-1`（P2 · 374 批 C 组判据改造）。

【票面】356 d5 的判据是**字面扫**「文件里不许出现 `open(log_path, 'w'`」。
但 370 刀1 给视觉流日志补了**三级兜底**：主路径原子写（tmp+fsync+replace）→
rename 被拒时复用 tmp 再 replace → 再被拒才退化为截断写，且该兜底**必须**保留
（不回退就会丢整批条目）。字面判据分不清「主路径裸写」（真缺陷）与「兜底层裸写」
（有意为之），于是这条判据自 370 起**持续红**。

【新判据（本刀）】按 **AST + 位置** 判，不看字面：
  · 主路径（不在任何 `except` 体内）出现裸 `open(<log_path>, 'w')` ⇒ **红**
  · 裸写只允许在 `except` 体内的兜底分支，且该 handler **必须留痕**
    （WARNING/ERROR 级日志）——否则等于把「兜底」变成无声许可
  · 主路径必须仍是原子写三要素：临时文件（mkstemp/tempfile）+ `os.replace`

验六件事：① 主路径裸写必红；② 兜底层裸写（带留痕）必绿；③ 兜底裸写无留痕必红；
④ 两个真文件按新判据通过；⑤ 真文件里兜底裸写**确实存在**（证明不是"没货所以绿"）；
⑥ m356 的老字面判据已换成新口径（不留"两套尺子"）。
"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ 判据本体
def _iter_with_ancestors(node, anc=()):
    yield node, anc
    for _ch in ast.iter_child_nodes(node):
        yield from _iter_with_ancestors(_ch, anc + (node,))


def _is_log_path_arg(node) -> bool:
    """目标参数名里含 `log_path`（`log_path` / `self._log_path` 都算）。

    不做 except 兜底：`ast.unparse` 对合法节点无失败路径，写 try/except 只会
    变成一个静默 handler（本仓 cw2 门禁对它零容忍，测试辅助件也不例外）。
    """
    return "log_path" in ast.unparse(node)


def bare_truncating_writes(src):
    """返回 `[(lineno, in_fallback)]`：裸截断写的位置与是否在 `except` 兜底层内。"""
    _tree = ast.parse(src)
    _out = []
    for _n, _anc in _iter_with_ancestors(_tree):
        if not isinstance(_n, ast.Call):
            continue
        _f = _n.func
        if not (isinstance(_f, ast.Name) and _f.id == "open"):
            continue
        if not _n.args or not _is_log_path_arg(_n.args[0]):
            continue
        _mode = None
        if len(_n.args) > 1:
            _mode = _n.args[1]
        for _kw in _n.keywords:
            if _kw.arg == "mode":
                _mode = _kw.value
        if not (isinstance(_mode, ast.Constant) and isinstance(_mode.value, str)
                and _mode.value.startswith("w")):
            continue
        _in_except = any(isinstance(a, ast.ExceptHandler) for a in _anc)
        _out.append((int(_n.lineno), _in_except))
    return _out


_LOGGY = ("_log", "warning", "error", "exception", "critical", "silent_exc")


def handler_has_trace(src, lineno):
    """裸写所在的那个 `except` handler 体内是否有留痕调用。"""
    _tree = ast.parse(src)
    for _n, _ in _iter_with_ancestors(_tree):
        if not isinstance(_n, ast.ExceptHandler):
            continue
        _lines = [getattr(x, "lineno", 0) for x in _n.body]
        if not any(a <= lineno <= (getattr(_n, "end_lineno", 0) or 0) for a in _lines):
            continue
        for _c in ast.walk(_n):
            if isinstance(_c, ast.Call):
                _f = _c.func
                _nm = (_f.attr if isinstance(_f, ast.Attribute)
                       else (_f.id if isinstance(_f, ast.Name) else ""))
                if _nm in _LOGGY:
                    return True
    return False


def has_atomic_write(src) -> bool:
    """主路径原子写三要素：临时文件 + `os.replace`。"""
    _tree = ast.parse(src)
    _txt = ast.unparse(_tree)
    _tmp = any(k in _txt for k in ("mkstemp", "NamedTemporaryFile", "tempfile"))
    return _tmp and "os.replace" in _txt


def assert_no_primary_bare_write(rel_path, root=None):
    """主路径不许裸写；兜底层裸写必须带留痕。不满足即 AssertionError。"""
    _p = os.path.join(root or _ROOT, rel_path)
    with open(_p, encoding="utf-8", errors="replace") as _f:
        _src = _f.read()
    _bare = bare_truncating_writes(_src)
    _primary = [ln for ln, fb in _bare if not fb]
    assert not _primary, (
        f"{rel_path} 主路径仍有裸截断写（第 {_primary} 行）——"
        "原子写被开了口子")
    for _ln, _fb in _bare:
        if _fb:
            assert handler_has_trace(_src, _ln), (
                f"{rel_path}:{_ln} 兜底层裸写没有留痕——"
                "「兜底」不能变成无声许可")
    return _bare


# --------------------------------------------------- ① / ② / ③ 判别力自测
_PRIMARY_BARE = '''import json
def _write_learn_log(self, log_data, log_path):
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f)
'''

_FALLBACK_OK = '''import json, os, tempfile
def _write_learn_log(self, log_data, log_path):
    _fd, _tmp = tempfile.mkstemp(dir=os.path.dirname(log_path))
    with os.fdopen(_fd, 'w', encoding='utf-8') as f:
        json.dump(log_data, f)
    try:
        os.replace(_tmp, log_path)
    except OSError as _e:
        try:
            with open(log_path, 'w', encoding='utf-8') as fb:
                json.dump(log_data, fb)
            self._log("WARN", "已回退为整体重写")
        except Exception as _e2:
            self._log("ERROR", f"回退也失败: {_e2}")
'''

_FALLBACK_NO_TRACE = '''import json, os, tempfile
def _write_learn_log(self, log_data, log_path):
    _fd, _tmp = tempfile.mkstemp(dir=os.path.dirname(log_path))
    with os.fdopen(_fd, 'w', encoding='utf-8') as f:
        json.dump(log_data, f)
    try:
        os.replace(_tmp, log_path)
    except OSError:
        with open(log_path, 'w', encoding='utf-8') as fb:
            json.dump(log_data, fb)
'''


def _mk(tmp_path, name, src):
    _p = tmp_path / name
    _p.write_text(src, encoding="utf-8")
    return str(_p)


def test_01_primary_bare_write_is_red(tmp_path):
    _p = _mk(tmp_path, "PulseLegs.py", _PRIMARY_BARE)
    assert bare_truncating_writes(_PRIMARY_BARE) == [(3, False)]
    try:
        assert_no_primary_bare_write("PulseLegs.py", root=str(tmp_path))
    except AssertionError as _e:
        assert "主路径仍有裸截断写" in str(_e)
    else:
        raise AssertionError("主路径裸写竟然通过了判据")
    assert os.path.isfile(_p)


def test_02_fallback_bare_write_is_green(tmp_path):
    _mk(tmp_path, "PulseVisualCortex.py", _FALLBACK_OK)
    _bare = assert_no_primary_bare_write("PulseVisualCortex.py", root=str(tmp_path))
    assert _bare == [(10, True)], _bare


def test_03_fallback_without_trace_is_red(tmp_path):
    _mk(tmp_path, "Bad.py", _FALLBACK_NO_TRACE)
    try:
        assert_no_primary_bare_write("Bad.py", root=str(tmp_path))
    except AssertionError as _e:
        assert "没有留痕" in str(_e)
    else:
        raise AssertionError("兜底裸写无留痕竟然通过了判据")


def test_04_atomic_write_required(tmp_path):
    assert has_atomic_write(_FALLBACK_OK) is True
    assert has_atomic_write(_PRIMARY_BARE) is False


# ------------------------------------------------ ④ / ⑤ 真文件验收
def test_05_real_files_pass_new_criteria():
    for _rel in ("organs/motor/PulseLegs.py", "organs/senses/PulseVisualCortex.py"):
        _bare = assert_no_primary_bare_write(_rel)
        with open(os.path.join(_ROOT, _rel), encoding="utf-8") as _f:
            assert has_atomic_write(_f.read()), _rel
        assert _bare is not None


def test_06_real_fallback_write_still_there():
    """⑤ 证明判据不是「因为没货所以绿」：视觉流的兜底裸写必须真在，且只在兜底层。"""
    _p = os.path.join(_ROOT, "organs/senses/PulseVisualCortex.py")
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    _bare = bare_truncating_writes(_src)
    assert len(_bare) >= 1, "兜底裸写消失了？先确认 370 刀1 的回退分支还在"
    assert all(fb for _, fb in _bare), f"有裸写跑到了主路径：{_bare}"


def test_07_m356_judgement_replaced():
    """⑥ 旧的字面判据不许留着当第二套尺子。"""
    with open(os.path.join(_ROOT, "tests", "test_m356_batch.py"),
              encoding="utf-8") as _f:
        _t = _f.read()
    assert "assert \"open(log_path, 'w'\" not in _t" not in _t
    assert "assert_no_primary_bare_write" in _t
