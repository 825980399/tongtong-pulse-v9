# -*- coding: utf-8 -*-
"""432刀4：对话落盘断链 —— 入口定 cid、全链贯通、真的写文件。

断链全图（431 批只查出一半，本批补齐）：
  ① `chat_service._send_chat_message` 的 payload **没有 `correlation_id` 键**；
  ② `PulseCortex._on_chat_message` 于是自造 `ctx:<n>:<ms>` 存进 `_pending_messages`；
  ③ 但发 QICA 的 `SEMANTIC_CLASSIFY` / QICA 回传这条链上，cid 拿到的都是**空串**；
  ④ `_pending_messages.pop("")` 恒 None ⇒ 直接 return `no_pending_match`
     ⇒ `dialogue_process_trace.begin()` 一次都没被调到
     ⇒ `data/experience/dialogue_process_*.jsonl` **一个都不会生成**。

  所以 431 批「cid 没有生成点」的结论只对了一半：**有生成点，但生成在断链的下游**。
  真正缺的是「入口定 id」这一步。

四条判据：
  1. 入口真的生成 cid（唯一、非空、格式可辨来源）；
  2. payload 真的带上了它（不是只在局部变量里转了一圈）；
  3. 皮层源码里 cid 取值**先看 payload、后兜底自造**（顺序判据，防兜底盖掉入口）；
  4. 端到端：拿入口 cid 走真落盘模块，文件真的出现、内容对得上；
     且空 cid 必须**不**写任何东西（首闸没被顺手拆掉）。
"""

import json
import os
import re
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CHAT_SERVICE = os.path.join(ROOT, "functions", "chat", "chat_service.py")
CORTEX = os.path.join(ROOT, "organs", "brain", "PulseCortex.py")


class _FakeInfoField:
    def __init__(self):
        self.published = []

    def publish(self, pulse):
        self.published.append(pulse)
        return True


class _FakePulseCore:
    def __init__(self):
        self.emitted = []

    def emit(self, **kwargs):
        self.emitted.append(kwargs)
        return {"pulse_id": f"p{len(self.emitted)}", **kwargs}


def _service():
    """只带最小依赖的 ChatService（不跑全量 __init__ 装配）。"""
    from functions.chat.chat_service import ChatService
    _svc = ChatService.__new__(ChatService)
    _svc._thinking_lock = threading.Lock()
    _svc._thinking_tasks = {}
    _svc._current_user_name = "判据用户"
    _svc.info_field = _FakeInfoField()
    _svc.pulse_core = _FakePulseCore()
    return _svc


def _method_block(path: str, name: str) -> str:
    """截出 `def <name>(` 到下一个同级 `def ` 之间的源码（用于顺序判据）。"""
    with open(path, encoding="utf-8") as _f:
        _src = _f.read()
    _m = re.search(rf"^    def {re.escape(name)}\(", _src, re.MULTILINE)
    assert _m, f"{path} 里找不到方法 {name}"
    _rest = _src[_m.end():]
    _nxt = re.search(r"^    def ", _rest, re.MULTILINE)
    return _rest[: _nxt.start()] if _nxt else _rest


def test_m432_d4_entry_generates_unique_cid():
    """判据 1：入口生成 cid —— 非空、互不相同、前缀可辨来源。"""
    _svc = _service()
    _cids = [_svc._new_correlation_id() for _ in range(50)]
    assert all(_cids), f"生成了空 cid：{_cids}"
    assert len(set(_cids)) == 50, f"cid 有重复：{[c for c in _cids if _cids.count(c) > 1]}"
    assert all(c.startswith("dlg:") for c in _cids), (
        f"cid 前缀应是 `dlg:`（与皮层自造的 `ctx:` 区分开），实际：{_cids[:3]}")
    assert all(len(c.split(":")) == 3 for c in _cids), f"格式应为 dlg:<seq>:<ms>：{_cids[:3]}"


def test_m432_d4_payload_carries_cid():
    """判据 2：`_send_chat_message` 发出去的 payload **真的有** cid 键。

    这一条是断链的正中靶心：修复前这里没有这个键，下游全靠 `.get(..., "")` 拿空串。
    """
    _svc = _service()
    _svc._send_chat_message("你好", "你好", is_complex=False,
                            code_blocks=[], file_paths=[])
    assert _svc.pulse_core.emitted, "根本没发脉冲"
    _pl = _svc.pulse_core.emitted[-1]["payload"]

    assert "correlation_id" in _pl, (
        "payload 里没有 correlation_id 键 —— 断链未修，下游仍会拿到空串")
    assert _pl["correlation_id"], "correlation_id 是空串 —— 等于没生成"
    assert _pl["correlation_id"].startswith("dlg:"), (
        f"cid 来源不对（应是入口生成的 dlg: 前缀）：{_pl['correlation_id']!r}")

    # 同一轮对话不能两次拿到不同 id：连发两条应是两个不同 cid（每轮一个）
    _svc._send_chat_message("第二句", "第二句")
    _pl2 = _svc.pulse_core.emitted[-1]["payload"]
    assert _pl2["correlation_id"] != _pl["correlation_id"], (
        "两轮对话拿到了同一个 cid ⇒ 计数器没走，落盘会把两轮折叠成一条")


def test_m432_d4_cortex_prefers_entry_cid_over_self_made():
    """判据 3：皮层源码里「先看 payload、后兜底自造」—— 顺序不能反。

    为什么用顺序判据而不是跑一遍：`_on_chat_message` 依赖 qica/pending 等一大堆
    装配，跑起来成本高且容易测成"假通过"。而这里要钉的正是**取值顺序**这一条
    源码事实 —— 兜底分支跑到 `self._correlation_counter += 1` 之前，必须先查
    payload，否则入口给的 id 会被盖掉，断链以另一种方式复发。
    """
    _body = _method_block(CORTEX, "_on_chat_message")
    _i_payload = _body.find('payload.get("correlation_id"')
    _i_fallback = _body.find("self._correlation_counter += 1")

    assert _i_payload != -1, (
        "_on_chat_message 里没有读 payload 的 correlation_id ⇒ 皮层还在自造 cid，断链会复发")
    assert _i_fallback != -1, "兜底自造分支不见了（入口缺失时的兜底被删了？）"
    assert _i_payload < _i_fallback, (
        "payload 取值出现在兜底自造**之后** ⇒ 入口 cid 仍会被覆盖，等于没修")

    # 兜底必须在 else 里 —— 否则两个 id 都会被算出来，后写的赢，行为不确定
    _seg = _body[_i_payload:_i_fallback]
    assert "else" in _seg, f"payload 与兜底之间没有 else，兜底不是条件分支。片段：{_seg!r}"


def test_m432_d4_end_to_end_writes_jsonl_and_empty_cid_writes_nothing(monkeypatch, tmp_path):
    """判据 4：端到端 —— 入口 cid 走真落盘模块，文件真的出现；空 cid 一个字都不写。

    隔离：`M426_TRACE_ROOT` 指到 tmp_path（铁律 38：实例化后第一行先改路径）。
    """
    monkeypatch.setenv("M426_TRACE_ROOT", str(tmp_path))
    from nucleus import dialogue_process_trace as tr

    tr._pending.clear()
    _svc = _service()
    _svc._send_chat_message("落盘验证问题", "落盘验证问题")
    _cid = _svc.pulse_core.emitted[-1]["payload"]["correlation_id"]

    tr.begin(_cid, "落盘验证问题", "判据用户")
    tr.stamp(_cid, intent="知识检索", intent_type="一般对话",
             suggested_method="knowledge_retrieve")
    tr.finish(_cid, answer_preview="落盘验证答复", latency_ms=42.0)

    _path = tr.out_path()
    assert os.path.exists(_path), f"没有生成落盘文件：{_path}"
    with open(_path, encoding="utf-8") as _f:
        _lines = [ln for ln in _f.read().splitlines() if ln.strip()]
    assert len(_lines) == 1, f"应恰好 1 行，实际 {len(_lines)} 行"

    _rec = json.loads(_lines[0])
    assert _rec["correlation_id"] == _cid, (
        f"落盘里的 cid 与入口不一致：{_rec['correlation_id']!r} != {_cid!r}")
    assert _rec["user_question"] == "落盘验证问题"
    assert _rec["answer_preview"] == "落盘验证答复"
    assert _rec["latency_ms"] == 42.0
    assert _rec["stage"] == "finished"

    # 反证：空 cid 必须被首闸挡住（别为了"让文件出现"把闸拆了）
    _before = os.path.getsize(_path)
    tr.begin("", "空 cid 不该留下任何东西", "判据用户")
    tr.finish("", answer_preview="不该出现")
    assert os.path.getsize(_path) == _before, (
        "空 cid 也写了东西 ⇒ begin() 的首闸被拆了，493 批那种串号会回来")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
