# -*- coding: utf-8 -*-
"""自主进化闭环测试（≥5 例）：审批字段桥接、组合判定、积压清理、可观测统计。

隔离：所有文件操作走临时 base_dir，不触碰真实 data/patches。
"""
import json
import os
import tempfile
import time

import pytest

from nucleus.evolution.PatchAutoApprover import (
    DECISION_APPROVE,
    DECISION_HUMAN,
    PatchAutoApprover,
)

# ★第53批 T1 同步：阈值 30 → 40（本常量必须与 config.py 实际文本一致）
_REAL_SNIPPET = "PATCH_AUTO_APPROVE_TRUST_THRESHOLD = 40"  # 真实存在于 config.py


def _mk_patch(**kw):
    base = {
        "id": kw.get("id", "p1"),
        "file": kw.get("file", "config.py"),
        "risk_level": kw.get("risk_level", "低"),
        "confidence": kw.get("confidence", "high"),
        "trust_score": kw.get("trust_score", 40),   # ★第53批 T1：门槛统一为 40
        "verified": kw.get("verified", None),
        "runtime_verified": kw.get("runtime_verified", True),
        "status": kw.get("status", "runtime_verified"),
        "generated_at": kw.get("generated_at", time.time() - 48 * 3600.0),
        "original_code": kw.get("original_code", _REAL_SNIPPET),
        "modified_code": kw.get("modified_code", _REAL_SNIPPET + "  # x"),
        "applied": kw.get("applied", False),
    }
    return base


@pytest.fixture
def appr():
    return PatchAutoApprover(base_dir=tempfile.mkdtemp())


class TestVerifiedBridge:
    def test_runtime_verified_桥接(self, appr):
        assert appr._verified(_mk_patch(verified=None, runtime_verified=True)) is True

    def test_status_runtime_verified_桥接(self, appr):
        p = _mk_patch(verified=None, runtime_verified=False, status="runtime_verified")
        assert appr._verified(p) is True

    def test_显式verified仍有效(self, appr):
        assert appr._verified(_mk_patch(verified=True, runtime_verified=False)) is True

    def test_无字段返回未验证(self, appr):
        assert appr._verified(_mk_patch(verified=None, runtime_verified=False, status="x")) is False


class TestClassify:
    def test_真实积压形态自动放行(self, appr):
        p = _mk_patch(trust_score=40, confidence="high", risk_level="低",
                     runtime_verified=True, status="runtime_verified")
        dec, _ = appr.classify(p)
        assert dec == DECISION_APPROVE

    def test_等待不足转人工(self, appr):
        p = _mk_patch(generated_at=time.time())
        dec, reason = appr.classify(p)
        assert dec == DECISION_HUMAN
        assert "等待" in reason

    def test_低信任仍拒绝(self, appr):
        p = _mk_patch(trust_score=10, verified=None, runtime_verified=True)
        dec, _ = appr.classify(p)
        assert dec == "reject_low_trust"


class TestPruneAndStats:
    def _write_pending(self, tmp, patches):
        path = os.path.join(tmp, "pending_patches.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(patches, f, ensure_ascii=False)

    def test_prune移除obsolete与已应用(self):
        tmp = tempfile.mkdtemp()
        obsolete = _mk_patch(id="o1", file="nonexistent_x.py", original_code="xxx")
        applied = _mk_patch(id="a1", applied=True)
        keep = _mk_patch(id="n1", trust_score=10, verified=None, runtime_verified=True)
        self._write_pending(tmp, [obsolete, applied, keep])
        a = PatchAutoApprover(base_dir=tmp)
        res = a.prune_pending()
        assert res["pruned"] == 2
        assert res["remaining"] == 1
        with open(os.path.join(tmp, "pending_patches.json"), encoding="utf-8") as f:
            kept = json.load(f)
        assert len(kept) == 1 and kept[0]["id"] == "n1"

    def test_prune_dryrun不写盘(self):
        tmp = tempfile.mkdtemp()
        obsolete = _mk_patch(id="o1", file="nonexistent_x.py", original_code="xxx")
        self._write_pending(tmp, [obsolete])
        a = PatchAutoApprover(base_dir=tmp)
        res = a.prune_pending(dry_run=True)
        assert res["dry_run"] is True
        with open(os.path.join(tmp, "pending_patches.json"), encoding="utf-8") as f:
            assert len(json.load(f)) == 1

    def test_stats分布(self):
        tmp = tempfile.mkdtemp()
        patches = [
            _mk_patch(id="a"),
            _mk_patch(id="b", trust_score=10, verified=None, runtime_verified=True),
        ]
        self._write_pending(tmp, patches)
        a = PatchAutoApprover(base_dir=tmp)
        stats = a.get_pending_stats()
        assert stats["total"] == 2
        assert DECISION_APPROVE in stats["by_decision"]
        assert "reject_low_trust" in stats["by_decision"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
