# -*- coding: utf-8 -*-
"""453R 刀2：请求级状态改 ContextVar，并发不串味。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import evidence_injection as EI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")


def _src():
    with open(LUNG, encoding="utf-8") as f:
        return f.read()


def test_两次fresh_state是独立字典():
    a = EI.fresh_state(origin="user_query")
    b = EI.fresh_state(origin="user_query")
    assert a is not b
    assert a["request_id"] != b["request_id"]
    assert a["budget"] is None and b["budget"] is None
    EI.end_request_state()


def test_同一context读写同一份state():
    st = EI.fresh_state(request_id="rid-A")
    assert EI.get_request_state() is st
    EI.update_request_state(budget="BUDGET-A", stage=2, injected=True)
    assert st["budget"] == "BUDGET-A" and st["stage"] == 2
    assert EI.is_retry_in_flight() is False
    EI.update_request_state(retrying=True)
    assert EI.is_retry_in_flight() is True
    EI.end_request_state()
    assert EI.get_request_state() is None


def test_并发两线程request_id与budget不交叉():
    barrier = threading.Barrier(2)
    out = {}

    def worker(name):
        st = EI.fresh_state(origin="user_query")
        st["request_id"] = "rid-" + name
        st["budget"] = "budget-" + name
        barrier.wait(timeout=5)       # 两边都设完再互相读
        out[name] = (EI.get_request_state()["request_id"],
                     EI.get_request_state()["budget"])
        EI.end_request_state()

    ts = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert out == {"A": ("rid-A", "budget-A"), "B": ("rid-B", "budget-B")}, out


def test_线程池复用不会继承上一次的retrying():
    # 上次请求若留下 retrying=True，新请求入口的 fresh_state 必须覆盖
    EI.fresh_state(origin="user_query")
    EI.update_request_state(retrying=True, in_flight=True)
    assert EI.is_retry_in_flight() is True
    EI.fresh_state(origin="user_query")     # 模拟下一个请求
    assert EI.is_retry_in_flight() is False
    EI.end_request_state()


def test_生产不再把状态挂在self上():
    src = _src()
    for token in ("self._m453_budget = ", "self._m453_stage = ",
                  "self._m453_skip_l2 = ", "self._m453_hits = ",
                  "self._m453_request_id = ", "self._m453_retrying = "):
        assert token not in src, token


def test_入口无条件fresh_state覆盖残留():
    src = _src()
    assert "_ei453.fresh_state(origin=_m40_origin)" in src
    assert "_ei453.is_retry_in_flight()" in src


def test_call_channel从contextvar读budget():
    src = _src()
    # ★不按固定字符窗口切：_call_channel 很长，clamp 段可能超出窗口
    assert "_st453cl.get(\"budget\")" in src
    assert "_m453_stage453" in src


def test_影子sink从contextvar读request_id():
    src = _src()
    assert 'request_id=_st453.get("request_id")' in src
    assert '_m453_cached = _st453.get("hits")' in src
