# -*- coding: utf-8 -*-
"""171批刀4 · 加载路径 state 枚举校验 门控单测。

覆盖 任务书判据：PulseNode.from_dict 加载路径须校验 state 取值。
① 合法三类（active / dormant / locked）经 from_dict 往返保留；
② 非法 state（如 "frozen"）经 from_dict 加载 → 记 WARNING 并回退 active（不中断加载）。

落点：PulseNode.from_dict（第171批 刀4 新增加载路径校验）。
与 162批刀6 的 入库拒绝闸门 互补（入库拒绝 / 加载回退）。
"""
from nucleus.mnemosyne.PulseNode import PulseNode
import nucleus.mnemosyne.PulseNode as PN


def _capture_warning():
    _cap = []
    _ow = PN._module_logger.warning
    PN._module_logger.warning = lambda m, *a, **k: _cap.append((m % a) if a else m)

    def _restore():
        PN._module_logger.warning = _ow

    return _cap, _restore


def test_load_valid_state_preserved():
    """判据①：合法三类经 from_dict 往返保留原 state。"""
    for _st in ("active", "dormant", "locked"):
        _d = {
            "node_id": f"k4_valid_{_st}",
            "value": "v",
            "evol_level": PulseNode.EVOL_L1,
            "state": _st,
        }
        _r = PulseNode.from_dict(_d)
        assert _r.state == _st, f"from_dict 应保留合法 state={_st}（实际={_r.state!r}）"


def test_load_invalid_state_warns_and_coerces():
    """判据②：非法 state 经 from_dict → 记 WARNING（含『刀4状态校验』）并回退 active。"""
    _d = {
        "node_id": "k4_invalid_frozen",
        "value": "v",
        "evol_level": PulseNode.EVOL_L1,
        "state": "frozen",  # 非法取值，不在 VALID_STATES
    }
    _cap, _restore = _capture_warning()
    try:
        _r = PulseNode.from_dict(_d)
    finally:
        _restore()
    assert _r.state == "active", f"非法 state 应回退 active（实际={_r.state!r}）"
    _joined = " ".join(str(x) for x in _cap)
    assert "刀4状态校验" in _joined, "应记录加载非法 state 的 WARNING"
