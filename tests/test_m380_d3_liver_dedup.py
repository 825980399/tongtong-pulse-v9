# -*- coding: utf-8 -*-
"""★380 刀3 · 肝吸收去重 key（P2）

票面：`[第98批T-98b] digest.knowledge` 同一条正文重复进肝 70 次/日，去重 key 未生效。

T0 结论（票面偏轻）：`PulseLiver._on_digest_knowledge` **根本没有去重**
（全文件 grep `dedup|sha256|hashlib` 命中 0），不是"key 没生效"。
实测当日 1,605 条吸收 / 680 个不同正文，最高一条重复 70 次。

本件验：同正文喂 10 次只进肝 1 次；不同正文各自进；指纹按 sha256 而非前缀。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.body.PulseLiver import PulseLiver  # noqa: E402


class _Kal:
    def __init__(self):
        self.added = []

    def add_node(self, node):
        self.added.append(node)


class _Stub(PulseLiver):
    """最小宿主：**继承 PulseLiver**（这样 `self._m380_digest_seen` 能解析到），
    但**不调 `super().__init__()`** —— 不拉起器官线程/节点池，只装判定要用的三个属性。
    """

    organ_name = "肝"
    node_pool = object()

    def __init__(self):
        self._kal = _Kal()
        self.logs = []

    def _log(self, level, msg):
        self.logs.append(msg)


def _call(stub, content):
    PulseLiver._on_digest_knowledge(stub, {"content": content,
                                           "source_organ": "代码学习"})


def test_m380_d3_same_body_absorbed_once():
    """★核心：同一条正文喂 10 次，肝里只存 1 条。"""
    _s = _Stub()
    for _ in range(10):
        _call(_s, "【种子记忆】[自我理解·X] 同一条正文")
    assert len(_s._kal.added) == 1, "add_node 被调了 %d 次" % len(_s._kal.added)


def test_m380_d3_different_bodies_all_absorbed():
    _s = _Stub()
    for _i in range(5):
        _call(_s, "第 %d 条不同正文" % _i)
    assert len(_s._kal.added) == 5


def test_m380_d3_fingerprint_is_not_prefix():
    """反控：前 50 字相同、后文不同的两条**不许**被当成重复。

    这正是"按前 N 字去重"会踩的坑（种子记忆的前缀高度重复）。
    """
    _prefix = "【种子记忆】[自我理解·PulseInnerWorld] " + "填" * 30
    _s = _Stub()
    _call(_s, _prefix + "AAA")
    _call(_s, _prefix + "BBB")
    assert len(_s._kal.added) == 2


def test_m380_d3_empty_content_ignored():
    _s = _Stub()
    _call(_s, "")
    _call(_s, None)
    assert len(_s._kal.added) == 0


def test_m380_d3_seen_set_is_bounded():
    """去重表要有上限（防内存无限涨），且淘汰后不影响正确性判定。"""
    _s = _Stub()
    _cap = PulseLiver._M380_DIGEST_SEEN_MAX
    _seen = _s.__dict__.setdefault("_m380_digest_seen_set", set())
    _order = _s.__dict__.setdefault("_m380_digest_seen_order", [])
    for _i in range(_cap + 5):
        PulseLiver._m380_digest_seen(_s, "body-%d" % _i)
    assert len(_seen) <= _cap and len(_order) <= _cap
