# -*- coding: utf-8 -*-
"""351 批隔离单测（刀1..刀5 核心不变量）。

纪律（与 350 同）：
  · 全部走**路径注入**（root / tmp_path / env），不读写生产 `data/`；
  · 不依赖现场数据 —— 真值与用例现造（铁律179）；
  · 每条"能过"的断言旁边都有**反向控制**（铁律168：对拍先造判别力）。
"""
import io
import json
import os

import pytest

from nucleus import audit_problem_pool as ap
from nucleus import audit_problem_pool_consumer as ac
from nucleus.evolution import zero_break_judge as zb


# ------------------------------------------------------------------ 夹具
@pytest.fixture()
def iso(tmp_path):
    """隔离根：`tmp/` 子目录（guard_pending_path 只认 `<root>/tmp/` 结构）。"""
    r = tmp_path / "proj"
    (r / "tmp").mkdir(parents=True)
    return r


def _write_collide(root):
    """造同名碰撞三件套：Alpha 被限定调用 / Beta 没有。"""
    with io.open(root / "mod_a.py", "w", encoding="utf-8") as f:
        f.write("class Alpha:\n    def shared_name(self):\n        return 1\n")
    with io.open(root / "mod_b.py", "w", encoding="utf-8") as f:
        f.write("class Beta:\n    def shared_name(self):\n        return 2\n")
    with io.open(root / "caller.py", "w", encoding="utf-8") as f:
        f.write("from mod_a import Alpha\n\n\ndef go():\n"
                "    return Alpha().shared_name()\n")


# ------------------------------------------------------- 刀1 口径 / 校准
def test_刀1_scoped_消解同名碰撞(iso):
    _write_collide(iso)
    items = [{"file": "mod_a.py", "method": "shared_name"},
             {"file": "mod_b.py", "method": "shared_name"}]
    sc = zb.reference_truth_scoped(str(iso), items)
    assert sc[0]["cls"] == "Alpha" and sc[1]["cls"] == "Beta"
    # 正例：被 `Alpha().shared_name()` 调用 ⇒ 活
    assert sc[0]["dead"] is False
    # 负控：Beta 的同名方法**不能**被 Alpha 的调用救活
    assert sc[1]["dead"] is True
    # 判别力：裸名口径下 Beta 会被"救活"（这正是 350 的 70% 上界成因）
    bare = zb.reference_truth_batch(str(iso), items)
    assert bare[1]["dead"] is False


def test_刀1_测试引用与生产引用分列(tmp_path):
    with io.open(tmp_path / "m.py", "w", encoding="utf-8") as f:
        f.write("class C:\n    def only_in_test(self):\n        return 1\n")
    (tmp_path / "tests").mkdir()
    with io.open(tmp_path / "tests" / "test_x.py", "w", encoding="utf-8") as f:
        f.write("from m import C\n\n\ndef test_a():\n    return C().only_in_test()\n")
    r = zb.reference_truth_scoped(str(tmp_path),
                                  [{"file": "m.py", "method": "only_in_test"}])[0]
    assert r["refs_test"] == 1 and r["refs_prod"] == 0
    assert r["dead"] is False            # 有引用 ⇒ 不算死
    assert r["dead_prod"] is True        # 但生产不可达（分列，不折算）


@pytest.mark.parametrize("rate,band,action", [
    (0.3158, "over_20", "candidate_only"),
    (0.20, "10_20", "split_by_klass"),          # 边界：>0.20 才算 over
    (0.15, "10_20", "split_by_klass"),
    (0.10, "under_10", "keep"),                 # 边界：>0.10 才算 split
    (0.05, "under_10", "keep"),
])
def test_刀1_校准三档(rate, band, action):
    d = zb.calibration_decision(rate, sample_size=76, source="t")
    assert d["band"] == band and d["action"] == action
    assert d["schema"] == zb.CALIBRATION_SCHEMA


def test_刀1_校准无样本不冒充keep():
    d = zb.calibration_decision(None, sample_size=0)
    assert d["action"] == "unknown" and d["rate"] is None
    assert d["band"] == "no_sample"


def test_刀1_校准台账幂等(tmp_path):
    p = str(tmp_path / "c.jsonl")
    d = zb.calibration_decision(0.25, sample_size=20)
    first = zb.write_calibration_ledger(p, d)
    second = zb.write_calibration_ledger(p, d)
    assert first["appended"] == 1 and first["persisted"] is True
    assert second["appended"] == 0 and second["skipped_dup"] == 1
    assert zb.read_calibration(p)["state"] == "ok"
    assert zb.read_calibration(str(tmp_path / "nope.jsonl"))["state"] == "absent"


# ------------------------------------------------- 刀2 注入路径 / 守卫
def test_刀2_守卫四例(iso):
    prod = str(iso / "data" / "patches" / "pending_patches.json")
    assert ac.guard_pending_path(prod, str(iso)) == (False, "is_production_path")
    assert ac.guard_pending_path(str(iso / "data" / "x.json"), str(iso))[1] \
        == "outside_isolation_root"
    ok = ac.guard_pending_path(str(iso / "tmp" / "p.json"), str(iso))
    assert ok == (True, "ok")
    # 名字欺骗：目录名带 tmp 但不在 `<root>/tmp/` 结构下 ⇒ 仍拒
    trick = ac.guard_pending_path(
        str(iso / "tmp_not_really" / "pending_patches.json"), str(iso))
    assert trick[0] is False


def test_刀2_注入落盘_不碰生产(iso, monkeypatch):
    """消费段真落注入路径；生产 pending 文件不存在/不变。"""
    iso_audit = iso / "tmp" / "audit"
    iso_audit.mkdir(parents=True)
    pend = iso / "tmp" / "pending_test.json"
    now = 1792700000.0
    entry = {
        "dedup_key": ap.make_dedup_key("m.py", "probe_fn", "dead_code"),
        "id": "x1", "file": "m.py", "method": "probe_fn",
        "issue_type": "dead_code", "severity": "low", "line": 3,
        "status": ap.STATUS_OPEN, "desc": "t",
        "original_code": "def probe_fn(self):\n    return 1\n",
        "modified_code": "def probe_fn(self):\n    return 2\n",
    }
    snap = ap.snapshot_path(now, root=str(iso), audit_dir_path=str(iso_audit))
    os.makedirs(os.path.dirname(snap), exist_ok=True)
    with io.open(snap, "w", encoding="utf-8") as f:
        json.dump({"schema": ap.SCHEMA, "entries": [entry],
                   "generated_at": now, "snapshot_day": ap.day_stamp(now)}, f)
    # 校准台账 injected = keep（放行）
    calib = iso / "tmp" / "calib.jsonl"
    with io.open(calib, "w", encoding="utf-8") as f:
        f.write(json.dumps({"key": "k", "action": "keep", "rate": 0.05}) + "\n")
    monkeypatch.setenv("PULSE_DEATH_CALIB_LEDGER", str(calib))
    monkeypatch.setenv("PULSE_AUDIT_POOL_DIR", str(iso_audit))
    monkeypatch.setenv("PULSE_FRAMEWORK", "1")   # 允许写（但只写隔离根）
    ac.consume_pool(limit=2, root=str(iso), audit_dir_path=str(iso_audit),
                    now=now, enable=True, pending_path=str(pend))
    assert pend.is_file(), "注入路径必须真落盘"
    rows = json.load(io.open(pend, encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("patches", [])
    assert any(str(r.get("id", "")).startswith("pool_") for r in rows)
    # 生产路径零写
    assert not (iso / "data" / "patches" / "pending_patches.json").exists()


def test_刀2_校准candidate_only拦下(iso, monkeypatch):
    calib = iso / "tmp" / "calib2.jsonl"
    with io.open(calib, "w", encoding="utf-8") as f:
        f.write(json.dumps({"key": "k", "action": "candidate_only",
                            "rate": 0.3158}) + "\n")
    monkeypatch.setenv("PULSE_DEATH_CALIB_LEDGER", str(calib))
    entry = {"file": "m.py", "method": "f", "issue_type": "dead_code",
             "original_code": "def f(self):\n    return 1\n",
             "modified_code": "def f(self):\n    return 2\n"}
    rec = ac._route_patch_candidate(entry, str(iso), 1.0)
    assert rec["calibration_blocked"] is True
    assert rec["approval_written"] is False
    assert rec["requires"] == ["calibration_passed"]
    # 负控：非判死族不受影响
    entry2 = dict(entry, issue_type="non_atomic_write")
    rec2 = ac._route_patch_candidate(entry2, str(iso), 1.0)
    assert rec2.get("calibration_blocked") is None


def test_刀2_无台账走内置默认(iso, monkeypatch):
    monkeypatch.setenv("PULSE_DEATH_CALIB_LEDGER",
                       str(iso / "tmp" / "absent.jsonl"))
    entry = {"file": "m.py", "method": "f", "issue_type": "dead_code",
             "original_code": "def f(self):\n    return 1\n",
             "modified_code": "def f(self):\n    return 2\n"}
    rec = ac._route_patch_candidate(entry, str(iso), 1.0)
    assert rec["calibration_blocked"] is True
    assert "builtin_default" in str(rec["calibration"]["source"])


# --------------------------------------------------------- 刀3 破零观察
_WID = "o3_selftest_351"


def test_刀3_四条件正例与负控():
    full = [{"patch_id": _WID, "success": True, "verified": True}]
    hist_ok = [{"id": _WID, "status": "applied_verified", "verified": True,
                "executed": 1, "applied": True, "file": "nucleus/x.py"}]
    w = zb.watch_candidate(_WID, pending_rows=[{"id": _WID}],
                           history_rows=hist_ok, results=full)
    assert w["zero_broken"] is True and w["selected"] == 1

    # 负控①：只在隔离根 ⇒ 不算破零
    hist_iso = [dict(hist_ok[0], file="tmp/p351_root/x.py")]
    w2 = zb.watch_candidate(_WID, pending_rows=[{"id": _WID}],
                            history_rows=hist_iso, results=full)
    assert w2["zero_broken"] is False
    assert "not_isolation_path" in w2["missing"]

    # 负控②：applied=False ⇒ 半通
    w3 = zb.watch_candidate(_WID, pending_rows=[{"id": _WID}],
                            history_rows=[{"id": _WID}],
                            results=[{"patch_id": _WID, "success": False}])
    assert w3["selected"] == 1 and w3["zero_broken"] is False


def test_刀3_日志行可grep():
    w = zb.watch_candidate(_WID, pending_rows=[{"id": _WID, "file": "a.py"}],
                           history_rows=[], results=None)
    ln = zb.watch_line(w)
    assert _WID in ln and "selected=0" in ln and "four_ok=N" in ln
    assert "in_pending=Y" in ln


def test_刀3_pending的file是目标而非落地():
    """★351 自证抓到的坑：history 的 file 覆盖 pending 的 file（否则假破零）。"""
    pend = [{"id": _WID, "file": "production_target.py"}]
    hist = [{"id": _WID, "status": "applied_verified", "verified": True,
             "executed": 1, "applied": True, "file": "tmp/iso_artifact.py"}]
    w = zb.watch_candidate(_WID, pending_rows=pend, history_rows=hist,
                           results=[{"patch_id": _WID, "success": True}])
    assert w["file"] == "tmp/iso_artifact.py"
    assert w["zero_broken"] is False


# ------------------------------------------------------- 刀4 归因追溯
def test_刀4_补齐与幂等():
    es = [{"dedup_key": "a", "desc": "x", "issue_type": "dead_code",
           "line": 5, "file": "m.py"},
          {"dedup_key": "b", "desc": "", "issue_type": "unknown",
           "line": 0, "file": ""}]
    r1 = ap.backfill_attribution(es, now=100.0)
    assert r1["filled"] == 2 and r1["already"] == 0
    assert es[1]["attribution"]["fallback"] is True
    assert es[1]["attribution"]["locatable"] is False
    assert es[0]["attribution"]["fallback"] is False
    assert es[0]["attribution"]["locatable"] is True
    r2 = ap.backfill_attribution(es, now=200.0)
    assert r2["filled"] == 0 and r2["already"] == 2


def test_刀4_不覆盖已有归因():
    old = {"schema": "audit_pool_attribution/350.v1", "fallback": False,
           "reasons": [], "locatable": True, "source_line": "",
           "source_line_state": "absent"}
    e = {"dedup_key": "a", "desc": "", "issue_type": "unknown", "line": 0,
         "file": "", "attribution": dict(old)}
    ap.backfill_attribution([e], now=1.0)
    assert e["attribution"] == old          # 一字不改
    assert "backfilled" not in e["attribution"]


# ------------------------------------------------------- 刀5 节拍可配
def test_刀5_默认节拍不变():
    import config
    assert getattr(config, "POOL_CONSUME_EVERY_TICKS", None) == 300


def test_刀5_回灌dry_run不写生产(tmp_path):
    from nucleus.evolution import verify_feedback as vf
    iso = tmp_path / "iso"
    (iso / "audit").mkdir(parents=True)
    r = vf.feedback_once(base_dir=str(tmp_path / "evo"),
                         entries=[], pool=ap, now=time_now(),
                         dry_run=True, root=str(iso),
                         audit_dir_path=str(iso / "audit"))
    # `absent` = effect_verify.json 不存在（合法：这里 base_dir 是空目录）
    assert r.get("state") in ("absent", "no_verdicts", "ok", "unavailable",
                              "no_pool")
    assert not (tmp_path / "data").exists()


def time_now():
    import time
    return time.time()
