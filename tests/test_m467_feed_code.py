# -*- coding: utf-8 -*-
"""★467批：补丁生成侧「喂料」修复的单测。

覆盖五刀：
  刀1 喂料补全（method_block 含 def，与源文件逐字一致）
  刀2 上限放宽 + prior_error / 445 要求接进**多文件**路径
  刀3 生成后 AST 结构自检 + 有界回灌重生成
  刀4 prior_error 跨轮（走 criteria_window 的 7 天台账）
  刀5 连续 N 次结构缺陷 → 判不可修并退出（append-only 台账）

判据全部与被测函数**非同源**：
  · def 名以 `PatchManager._m280_extract_def_name`（入队侧真函数）为基准对拍；
  · 结构门的输入是**真实标本**（PulseLiver._compress_group 的方法体/完整方法）；
  · 「不可修」用 `should_stop` 与 `streak` 两个口互相校验。
"""
import importlib
import json

import pytest

import config
from nucleus.evolution import feed_code as FC
from nucleus.evolution import struct_streak as SS
from nucleus.evolution.LLMEvolutionEngine import LLMEvolutionEngine, _m467_prior_notice
from nucleus.reasoning.PatchManager import PatchManager

TARGET = "organs/body/PulseLiver.py"
METHOD = "_compress_group"

#: 真实方法体（不含 def）—— 旧喂料。首行缩进 8 空格，与 `get_method_body` 一致。
OLD_BODY = (
    "        if len(nodes) > 40:\n"
    "            _any_ok = False\n"
    "            for _i in range(0, len(nodes), 40):\n"
    "                return False\n"
    "        return True"
)

#: 真实完整方法（含 def）—— 新喂料。def 行 4 空格，方法体 8 空格。
FULL_DEF = (
    "    def _compress_group(self, prefix: str, nodes: list[PulseNode]) -> bool:\n"
    "        if len(nodes) > 40:\n"
    "            _any_ok = False\n"
    "            for _i in range(0, len(nodes), 40):\n"
    "                return False\n"
    "        return True"
)


# ---------------------------------------------------------------- 刀1
@pytest.mark.parametrize("code,expected", [
    (OLD_BODY, ["no_def"]),
    (FULL_DEF, []),
    ("", ["empty"]),
    ("def foo():\n    pass", ["def_name_mismatch:foo"]),
])
def test_01_struct_defects_real_specimens(code, expected):
    """真实喂料的两种形态：方法体判 no_def，含 def 的完整方法判合格。"""
    assert FC.struct_defects(code, METHOD) == expected


def test_02_method_block_matches_source_byte_for_byte():
    """`method_block` 必须返回**源文件原文**（含缩进/行尾），否则
    `full_content.replace(original_code, modified_code)` 会静默不替换。"""
    import io
    _raw = io.open(TARGET, "r", encoding="utf-8", newline="").read()
    from nucleus.self_inspector import SelfInspector
    _d = SelfInspector().get_method_body("PulseLiver", METHOD)
    assert _d, "取不到真实标本，测试前提失效"
    _blk = FC.method_block(TARGET, METHOD, _d.get("line_number") or 0)
    assert _blk, "method_block 返回空"
    assert _blk in _raw, "返回的不是源文件原文"
    assert _blk.split("\n")[0].lstrip().startswith("def " + METHOD + "("), \
        "首行必须是 def 行"
    assert len(_blk) > len(_d.get("body") or ""), "完整方法应比方法体长（多了 def 行）"


def test_03_method_block_keeps_def_line_shape():
    """def 行必须带 `self` 与返回类型 —— 凭 `args` 拼是拼不出来的。

    实测 `args` 字段 = `prefix: str, nodes: list[PulseNode]`，
    **既无 self 也无 -> bool**，所以只能回源文件取原文。
    """
    from nucleus.self_inspector import SelfInspector
    _si = SelfInspector()
    _d = _si.get_method_body("PulseLiver", METHOD)
    _blk = FC.method_block(TARGET, METHOD, _d.get("line_number") or 0)
    _def_line = _blk.split("\n")[0]
    assert "self" in _def_line, "def 行丢了 self"
    assert "-> bool" in _def_line, "def 行丢了返回类型"
    assert "self" not in str(_d.get("args") or ""), \
        "前提校验：args 字段本来就不含 self（若将来变了，本用例可简化）"


def test_04_def_name_parity_with_patch_manager():
    """以入队侧真函数为基准对拍 —— 两处若漂移，就会出现
    「生成侧自检说合格、入队侧判 no_def」的再次分叉。"""
    _samples = [
        OLD_BODY, FULL_DEF, "def foo():\n    pass", "x=1", "",
        "async def bar():\n    pass", "    def baz(self):\n        pass",
        "try:\n  pass", "def foo_suffix( ", "class A:\n    def m(self):\n        pass",
    ]
    for _s in _samples:
        assert FC.extract_def_name(_s) == PatchManager._m280_extract_def_name(_s), \
            "def 名抽取与 PatchManager 漂移: %r" % (_s[:40],)


def test_05_crlf_blank_lines_do_not_break_syntax_check():
    """回归本批 T0 撞到的真失败：CRLF 文件的空行是 `\\r`，
    `textwrap.dedent` 认不出「纯空白行」⇒ 公共前缀算成 0 ⇒ 一字不剥
    ⇒ 完整方法被判 `unexpected indent@1`（PulseLiver.py 4079 CRLF / 0 裸 LF）。"""
    _crlf = FULL_DEF.replace("\n", "\r\n")
    assert FC.struct_defects(_crlf, METHOD) == [], "CRLF 版本必须同样判合格"
    _crlf_bad = OLD_BODY.replace("\n", "\r\n")
    assert FC.struct_defects(_crlf_bad, METHOD) == ["no_def"], \
        "CRLF 的方法体仍应判 no_def（不是被语法错掩盖）"


def test_06_struct_defects_has_discriminating_power():
    """同一段合格代码，改一处就该变 —— 否则断言没有判别力。"""
    assert FC.struct_defects(FULL_DEF, METHOD) == []
    assert FC.struct_defects(
        FULL_DEF.replace("def _compress_group(", "def _compress_groupx(", 1),
        METHOD) == ["def_name_mismatch:_compress_groupx"]
    assert FC.struct_defects(
        FULL_DEF.replace("if len(nodes) > 40:", "if len(nodes) > 40", 1),
        METHOD) == ["syntax_error"]


# ---------------------------------------------------------------- 刀2
def test_07_prompt_code_limit_widened():
    """467 前是 2000（单文件）/ 3000（多文件）；实测完整方法 12903 字符，
    必须能被一次装进 prompt，否则模型永远只看得到前 16%。

    ★这里必须 `importlib.import_module`：`from nucleus.evolution import
      LLMEvolutionEngine as X` 拿到的是**类**（包 `__init__` 同名导出覆盖），
      而要读的是**模块级**函数。
    """
    assert FC.prompt_code_limit() >= 13000
    _mod = importlib.import_module("nucleus.evolution.LLMEvolutionEngine")
    assert _mod._m467_prompt_code_limit() == FC.prompt_code_limit()


def test_08_prior_notice_carries_previous_failure():
    """prior_error 跨轮进 prompt —— 此前只在同一轮多步规划内部填充，跨轮恒空。"""
    _txt = _m467_prior_notice({"prior_error": "上一版只给了方法体"})
    assert "上一版只给了方法体" in _txt
    assert _m467_prior_notice({}) == "", "无 prior_error 时不得凭空加料"


def test_09_prior_notice_carries_445_requirement():
    _txt = _m467_prior_notice({"m445_require_full_def": True})
    assert FC.FULL_DEF_REQUIREMENT in _txt
    assert "def " in _txt


# ---------------------------------------------------------------- 刀3
def _engine_with(replies):
    """构造一个只走本地解析的引擎：`_call_llm` 按序吐 replies。"""
    _calls = []

    def _fake(system=None, prompt=None, model=None, max_tokens=None):
        _calls.append(len(str(prompt or "")))
        return replies.pop(0) if replies else ""

    _e = LLMEvolutionEngine(".")
    _e._call_llm = _fake
    return _e, _calls


def _payload(modified, original=FULL_DEF, method=METHOD, file=TARGET):
    return json.dumps({"patches": [{
        "file": file, "method": method,
        "original_code": original, "modified_code": modified,
    }]})


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """台账隔离：**绝不**写生产 `data/evolution/struct_defect_ledger.jsonl`。"""
    _p = str(tmp_path / "struct_defect_ledger.jsonl")
    monkeypatch.setattr(SS, "ledger_path", lambda path=None: _p)
    return _p


def test_10_gate_regenerates_and_keeps_good_patch(ledger):
    """坏补丁 → 回灌 → 好补丁。上限 2 次，本用例第 2 次才修好（真实场景）。"""
    _iss = {"type": "optimize", "description": "d"}
    _e, _calls = _engine_with([_payload(OLD_BODY), _payload(FULL_DEF)])
    _in = _e._parse_multi_file_patch(_payload(OLD_BODY), _iss)
    assert len(_in) == 1, "前提：首发补丁能被解析出来"
    _out = _e._m467_struct_gate(_in, _iss, "BASE", "SYS", METHOD, TARGET)
    assert len(_out) == 1, "应留下 1 条结构合格的补丁"
    assert "def _compress_group(" in _out[0]["modified_code"]
    assert "m467_struct_defects" not in _out[0]
    assert len(_calls) == 2, "应回灌 2 次（第 1 次仍坏，第 2 次修好）"


def test_11_gate_is_bounded_and_drops_when_unfixable(ledger):
    """模型一直修不好时必须**停在上限**，且不把注定被作废的补丁放回队列。"""
    _iss = {"type": "optimize", "description": "d"}
    _limit = FC.max_regen()
    assert _limit == 2, "前提：默认上限 2 次（若配置改了请同步本断言）"
    _e, _calls = _engine_with([_payload(OLD_BODY) for _ in range(5)])
    _in = _e._parse_multi_file_patch(_payload(OLD_BODY), _iss)
    _out = _e._m467_struct_gate(_in, _iss, "BASE", "SYS", METHOD, TARGET)
    assert _out == [], "结构缺陷未修好 ⇒ 不得返回"
    assert len(_calls) == _limit, "回灌次数必须等于上限，不能无限重试"


def test_12_gate_writes_defect_ledger_when_dropped(ledger):
    _iss = {"type": "optimize", "description": "d"}
    _e, _ = _engine_with([_payload(OLD_BODY) for _ in range(5)])
    _e._m467_struct_gate(
        _e._parse_multi_file_patch(_payload(OLD_BODY), _iss),
        _iss, "BASE", "SYS", METHOD, TARGET)
    _rows = SS._read(ledger)
    assert _rows, "丢弃后必须落台账（否则刀5 永远拿不到数据）"
    assert _rows[-1]["event"] == SS.EV_DEFECT
    assert _rows[-1]["file"] == "PulseLiver.py"
    assert "no_def" in _rows[-1]["defects"]


def test_13_gate_does_not_kill_caller_migration_patches(ledger):
    """跨文件迁移的调用方补丁，def 名当然不等于目标方法名 —— 不能误杀。"""
    _iss = {"type": "optimize", "description": "d"}
    _e, _ = _engine_with([])
    _pl = _payload(
        "def caller_fn(self):\n    result = liver._compress_group(prefix, nodes)",
        original="result = liver._compress_group(prefix, nodes)",
        method="caller_fn", file="nucleus/other.py")
    _good, _bad = LLMEvolutionEngine._m467_split(
        _e._parse_multi_file_patch(_pl, _iss), METHOD, FC)
    assert len(_good) == 1 and not _bad, "调用方补丁被误判为结构缺陷"


def test_14_gate_off_returns_input_unchanged(ledger, monkeypatch):
    _iss = {"type": "optimize", "description": "d"}
    _e, _calls = _engine_with([])
    _in = _e._parse_multi_file_patch(_payload(OLD_BODY), _iss)
    monkeypatch.setattr(FC, "struct_gate_on", lambda: False)
    _out = _e._m467_struct_gate(_in, _iss, "BASE", "SYS", METHOD, TARGET)
    assert len(_out) == 1, "关闭时必须原样返回（灰度可回退）"
    assert not _calls, "关闭时不得再调 LLM"


# ---------------------------------------------------------------- 刀5
def test_20_should_stop_after_n_defects(ledger, monkeypatch):
    monkeypatch.setattr(config, "M467_UNFIXABLE_STREAK", 3)
    for _i in range(3):
        SS.note(TARGET, METHOD, defects=["no_def"], patch_id="p%d" % _i)
    _r = SS.should_stop(TARGET, METHOD)
    assert _r["stop"] is True
    assert _r["streak"] == 3 and _r["limit"] == 3


def test_21_ok_event_resets_streak(ledger):
    """没有恢复点，只要历史上攒够 N 次，即使后来修好了也永远停在那里。"""
    for _i in range(5):
        SS.note(TARGET, METHOD, defects=["no_def"])
    assert SS.should_stop(TARGET, METHOD)["stop"] is True
    SS.note(TARGET, METHOD, event=SS.EV_OK)
    assert SS.should_stop(TARGET, METHOD)["stop"] is False
    assert SS.streak(TARGET, METHOD)["streak"] == 0


def test_22_targets_do_not_interfere(ledger):
    """多目标轮转不能把彼此的计数冲掉。"""
    for _i in range(5):
        SS.note("organs/body/A.py", "m1", defects=["no_def"])
    assert SS.should_stop("organs/body/A.py", "m1")["stop"] is True
    assert SS.should_stop("organs/body/B.py", "m2")["stop"] is False


def test_23_same_basename_from_different_paths_are_same_key(ledger):
    """现场可能一边给绝对路径、一边给相对路径；按 basename 归一。"""
    SS.note("D:/repo/organs/body/PulseLiver.py", METHOD, defects=["no_def"])
    assert SS.streak("organs/body/PulseLiver.py", METHOD)["streak"] == 1


def test_24_ledger_is_append_only(ledger):
    SS.note(TARGET, METHOD, defects=["no_def"])
    SS.note(TARGET, METHOD, event=SS.EV_OK)
    _rows = SS._read(ledger)
    assert [r["event"] for r in _rows] == [SS.EV_DEFECT, SS.EV_OK]
