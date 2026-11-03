# -*- coding: utf-8 -*-
"""384 刀1：快照节点定位器守卫（`mark_duplicate_nodes._locate_nodes_array`）。

## 这条守卫守的是什么

修前实现：

    _i = _buf.find(b'"nodes"')      # ← 会命中字符串值
    _j = _buf.find(b"[", _i + 7)
    → 窗口 1MB

真实快照里调用图有 `"args": "nodes"`（偏移 ~1.04MB），真字段 `"nodes":` 在 ~1.25MB，
于是定位到**错误的 `[`**，`iter_node_spans` 从那里开始扫，`json.loads` 全部失败
又被静默 `continue` ⇒ **扫出 0 个节点却一声不响**。清理工具因此在 720MB 快照上
报「占位符污染 0 个」—— 那个 0 是「没采到」，不是「真干净」。

## 合成夹具的形状（刻意复现污染）

    {"args": "nodes", "call_graph": [ {...} ], "nodes": [ {...}, {...} ]}
                 ↑ 字符串值                 ↑ 真数组

旧实现会定位到 `call_graph` 的 `[`（因为它排在 `"args": "nodes"` 之后），
新实现必须定位到 `"nodes":` 之后的 `[`。
"""
import ast
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(ROOT, "tools")
for _p in (ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mark_duplicate_nodes as m  # noqa: E402

REAL_SNAPSHOT = os.path.join(ROOT, "data", "knowledge",
                             "pulse_knowledge_snapshot.json")

#: 复现污染形态：字符串值 `"nodes"` 出现在真字段 `"nodes":` **之前**，
#: 且它后面紧跟**另一个数组**（`"called_methods": [...]`）—— 这才是真实快照的形态：
#: `"args": "nodes"}, "PulseLiver._detect_noise": {"called_methods": ["_...`
#: 旧实现会把这个 `called_methods` 的 `[` 当成节点数组起点。
SYNTH = (
    '{"version": "v9.5", '
    '"call_graph": {"PulseLiver.x": {"organ": "PulseLiver", "args": "nodes"}, '
    '"PulseLiver._detect_noise": {"called_methods": ["_a", "_b"]}}, '
    '"nodes": [{"node_id": "n1", "value": "a"}, '
    '{"node_id": "n2", "value": "b"}]}'
)


def _write_synth(tmp_path):
    p = tmp_path / "synth_snapshot.json"
    p.write_text(SYNTH, encoding="utf-8")
    return str(p)


# --------------------------------------------------- ① 新实现要定位到真数组
def test_m384_d1_locates_real_nodes_array(tmp_path):
    """★核心：必须定位到 `"nodes":` 之后，而不是 `"args": "nodes"` 之后。"""
    p = _write_synth(tmp_path)
    with io.open(p, "rb") as f:
        pos = m._locate_nodes_array(f)
    with io.open(p, "rb") as f:
        f.seek(pos)
        head = f.read(40)
    assert head.startswith(b'{"node_id"'), \
        "定位到了别处，切片开头是 %r" % head


def test_m384_d1_old_impl_would_misplace(tmp_path):
    """反向：复现改前实现，证明它**确实**会定位错（不是我们假想的风险）。"""
    p = _write_synth(tmp_path)
    with io.open(p, "rb") as f:
        buf = f.read(1 << 20)
    i = buf.find(b'"nodes"')            # 旧实现：不带冒号
    j = buf.find(b"[", i + 7)
    old_pos = j + 1
    with io.open(p, "rb") as f:
        new_pos = m._locate_nodes_array(f)
    assert old_pos != new_pos, "旧实现在本夹具上应与新实现不同（否则夹具没复现污染）"
    with io.open(p, "rb") as f:
        f.seek(old_pos)
        assert not f.read(20).startswith(b'{"node_id"'), "旧实现本应定位到非节点数组"


def test_m384_d1_iter_yields_all_nodes(tmp_path):
    """`iter_node_spans` 在夹具上应产出 2 个可解析节点。"""
    p = _write_synth(tmp_path)
    rows = [json.loads(raw.decode("utf-8")) for _s, _e, raw in m.iter_node_spans(p)]
    assert [r["node_id"] for r in rows] == ["n1", "n2"]


# ------------------------------------------------- ② 定位器不接受无 nodes 结构
def test_m384_d1_raises_when_no_nodes_field(tmp_path):
    """结构里没有 `"nodes":` ⇒ 必须报错，**不许**静默返回 0 让上层出假结论。"""
    p = tmp_path / "no_nodes.json"
    p.write_text('{"args": "nodes", "call_graph": [1, 2]}', encoding="utf-8")
    try:
        with io.open(str(p), "rb") as f:
            m._locate_nodes_array(f)
    except ValueError as _e:
        assert "nodes" in str(_e)
        return
    raise AssertionError("没有真 nodes 字段时必须 raise，不能返回一个偏移")


# --------------------------------------------- ③ 真实快照：偏移与首节点可解析
def test_m384_d1_real_snapshot_offset_past_callgraph():
    """真快照上：定位处**前 9 字节必须正好是 `"nodes":[`**。

    这条钉住「不许命中调用图里的字符串值 `"args": "nodes"`」。

    ★432 批附注（回滚点，2026-11-03）：原判据是 `pos > 1_200_000`（硬编码偏移门槛，
    代理「真字段在 freq_index 之后」）。今日真快照内部重排，`"nodes":` 落到
    1,135,426 ⇒ 门槛不成立，但**定位本身仍然正确**（前 9 字节实测 `b'"nodes":['`）。
    数据一变就红 = 假红，故把代理判据换成直判结构。判别力反而更强：
    旧实现（不带冒号 + 1MB 缓冲）在本日快照上会落到 852,033，
    前 12 字节为 `b'hods": ["_lo'`（命中 `called_methods` 数组），当场红。
    回滚：把本函数体换回 `assert pos > 1_200_000, ...` 一行即可（不建议，数据一变又假红）。
    """
    if not os.path.isfile(REAL_SNAPSHOT):
        return          # 无快照的机器上跳过（不制造假绿：本用例本就不适用）
    with io.open(REAL_SNAPSHOT, "rb") as f:
        pos = m._locate_nodes_array(f)
    with io.open(REAL_SNAPSHOT, "rb") as f:
        f.seek(pos - 9)
        _lead = f.read(9)
        f.seek(pos - 1)
        _brace = f.read(1)
        _raw = f.read(4096)
    assert _brace == b"[", "偏移前一个字节应是数组起始的 ["
    assert _lead == b'"nodes":[', (
        "定位偏移 %d 的前 9 字节是 %r，不是 b'\"nodes\":['  ⇒ 大概率又命中了字符串值"
        % (pos, _lead))
    assert b'"node_id"' in _raw, "定位后 4KB 内应出现 node_id"


def test_m384_d1_real_snapshot_first_span_parses():
    """真快照第一个 span 必须能 json 解析出 node_id（否则整次扫描都是空转）。"""
    if not os.path.isfile(REAL_SNAPSHOT):
        return
    for _s, _e, raw in m.iter_node_spans(REAL_SNAPSHOT):
        d = json.loads(raw.decode("utf-8"))
        assert str(d.get("node_id")).startswith("node:"), d.get("node_id")
        return
    raise AssertionError("一个 span 都没产出 ⇒ 定位器仍有问题")


# --------------------------------------- ④ 扫描件自身的守卫：0 节点必须报失败
def test_m384_d1_scan_refuses_zero_nodes():
    """★扫描件对「0 个节点」必须返回非 0，而不是打印一份「全干净」的报告。"""
    src = io.open(os.path.join(ROOT, "tools", "m384_kb_quality_scan.py"),
                  encoding="utf-8").read()
    assert 'if _r["total_nodes"] == 0:' in src
    assert "这不是「干净」，是解析失败" in src
    # 语法自检（防止拼补丁时把文件写坏）
    ast.parse(src)
