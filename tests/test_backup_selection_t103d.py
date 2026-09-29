"""第103批 T-103d 回归测试：备份选优逻辑（_load_from_backup）。

Dxxx 选优逻辑已在第80批 T4 修复为「综合评分选优」（节点数 + L2/L3 分层
完整度 + checksum 有效 + 时间衰减），明确「不再固定取第一份 / >=4000 即停」。
本测试锁定该行为：当存在「更新但节点更少」与「较旧但节点更多」两份备份时，
必须选择节点更多（评分更高）的一份，而非简单取最新。
"""
import json
from unittest import mock

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


class _FakeNode:
    def __init__(self, nid, lvl):
        self.node_id = nid
        self.evol_level = lvl


def _write_backup(path, n_nodes, l2l3):
    nodes = [{"node_id": f"x{i}", "value": "v", "evol_level": "L1"}
             for i in range(n_nodes)]
    for j in range(l2l3):
        nodes.append({"node_id": f"l{j}", "value": "v", "evol_level": "L2"})
    data = {"version": "v", "node_list_checksum": "c",
            "node_count_at_save": n_nodes, "nodes": nodes}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_backup_selection_picks_best_score_not_newest(tmp_path):
    sp = tmp_path / "pulse_knowledge_snapshot.json"
    snap = PulseSnapshot(snapshot_path=str(sp))

    older = tmp_path / "pulse_knowledge_snapshot.json.20200101_000000.bak"
    newer = tmp_path / "pulse_knowledge_snapshot.json.20260922_000000.bak"
    _write_backup(older, 90, l2l3=10)   # 旧、100节点(90+10)、含 L2
    _write_backup(newer, 50, l2l3=0)     # 新、节点少

    with mock.patch.object(
        snap, "_restore_from_data",
        side_effect=lambda d, t: [_FakeNode(x.get("node_id"), x.get("evol_level"))
                                  for x in d.get("nodes", [])]
    ):
        best = snap._load_from_backup()

    assert len(best) == 100, f"应选节点多的旧备份(100), 实际 {len(best)}"


def test_backup_selection_prefers_l2l3_rich_when_node_counts_equal(tmp_path):
    sp = tmp_path / "pulse_knowledge_snapshot.json"
    snap = PulseSnapshot(snapshot_path=str(sp))

    a = tmp_path / "pulse_knowledge_snapshot.json.20200101_000000.bak"
    b = tmp_path / "pulse_knowledge_snapshot.json.20200102_000000.bak"
    _write_backup(a, 80, l2l3=0)    # 无 L2/L3
    _write_backup(b, 80, l2l3=20)   # 同节点数但更完整（L2/L3 加分）

    with mock.patch.object(
        snap, "_restore_from_data",
        side_effect=lambda d, t: [_FakeNode(x.get("node_id"), x.get("evol_level"))
                                  for x in d.get("nodes", [])]
    ):
        best = snap._load_from_backup()

    # b 含 20 个 L2 节点 => 评分更高 => 应选 b（80 节点且 L2/L3 丰富）
    l2 = sum(1 for n in best if n.evol_level == "L2")
    assert l2 == 20, f"应选 L2/L3 更完整的备份, 实际 L2 数 {l2}"
