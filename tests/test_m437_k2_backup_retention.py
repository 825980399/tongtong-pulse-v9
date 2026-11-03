# -*- coding: utf-8 -*-
"""437 刀2 单测：备份保留策略（分类 / 代数 / 超龄 / 保护目录 / 干跑-真删）。

全部在 tmp_path 里造盘，不碰生产 data/。
"""
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import utils.backup_retention as br

_DAY = 86400.0


def _touch(path: str, size: int = 16, age_days: float = 0.0, now: float | None = None) -> str:
    """造一个指定大小与年龄的文件（mtime 显式指定，不靠 sleep）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as _f:
        _f.write(b"x" * size)
    _now = time.time() if now is None else now
    _t = _now - age_days * _DAY
    os.utime(path, (_t, _t))
    return path


# ---------------------------------------------------------------- ① 分类
@pytest.mark.parametrize("name,expect", [
    ("a.json.bak", ("a.json", 0)),
    ("a.json.bak.1", ("a.json", 1)),
    ("a.json.bak.12", ("a.json", 12)),
    ("p.jsonl.trim.bak", ("p.jsonl", 0)),
    ("s.json.20261103_064554.bak", ("s.json.20261103_064554", 0)),
    ("a.json", None),
    ("a.json.tmp", None),
    ("abak", None),
    ("", None),
])
def test_classify_backup(name, expect):
    assert br.classify_backup(name) == expect


def test_classify_prefers_rotation_over_plain():
    """`x.bak.1` 不以 ".bak" 结尾 —— 必须先判轮转位，否则整类多代备份扫不到。

    （施工时踩过：endswith(".bak") 前置 ⇒ 22 个备份只扫到 14 个。）
    """
    assert br.classify_backup("x.bak.1") == ("x", 1)
    tail = [n for n in ("x.bak", "x.bak.1", "x.bak.2")]
    assert len({br.classify_backup(n)[0] for n in tail}) == 1, "同源三代未归到同一 base"


# ---------------------------------------------------------------- ② 代数
def test_extra_generations_deleted(tmp_path):
    """有 gen0 时，gen>=keep 的一律删；gen0 保留。"""
    for _n in ("a.json.bak", "a.json.bak.1", "a.json.bak.2"):
        _touch(str(tmp_path / _n), size=100)
    _r = br.scan(str(tmp_path), max_age_days=7, keep_generations=1)
    _dels = sorted(x["rel"].replace("\\", "/") for x in _r["deletable"])
    _kept = [x["rel"].replace("\\", "/") for x in _r["kept"]]
    assert _dels == ["a.json.bak.1", "a.json.bak.2"], f"实际可删={_dels}"
    assert _kept == ["a.json.bak"], f"实际保留={_kept}"


def test_no_gen0_keeps_newest(tmp_path):
    """只剩 gen1 时必须留下（不能把最后一份备份也删了）。"""
    _touch(str(tmp_path / "b.json.bak.1"), size=100)
    _r = br.scan(str(tmp_path), max_age_days=7, keep_generations=1)
    assert _r["deletable"] == [], "没有 gen0 却把 gen1 判成可删"
    assert len(_r["kept"]) == 1


# ---------------------------------------------------------------- ③ 超龄
def test_age_rule(tmp_path):
    _touch(str(tmp_path / "old.json.bak"), size=10, age_days=8)
    _touch(str(tmp_path / "new.json.bak"), size=10, age_days=6)
    _r = br.scan(str(tmp_path), max_age_days=7)
    _dels = [x["rel"] for x in _r["deletable"]]
    assert _dels == ["old.json.bak"], f"超龄判定错: {_dels}"
    assert "new.json.bak" in "".join(x["rel"] for x in _r["kept"])


# ---------------------------------------------------------------- ④ 保护
@pytest.mark.parametrize("protected_dir", ["evidence_chain_freeze_20260923", "_archive"])
def test_protected_dirs_never_scanned(tmp_path, protected_dir):
    """证据链冻结仓 / 人工归档目录：整棵跳过，哪怕超龄也不许进可删清单。"""
    _touch(str(tmp_path / protected_dir / "keep.json.bak"), size=10, age_days=400)
    _touch(str(tmp_path / "normal.json.bak"), size=10, age_days=400)
    _r = br.scan(str(tmp_path), max_age_days=7)
    _rels = " ".join(x["rel"] for x in _r["deletable"])
    assert protected_dir not in _rels, f"保护目录被列进可删: {_rels}"
    assert "normal.json.bak" in _rels
    assert _r["scanned"] == 1, f"保护目录里的文件也被扫了（scanned={_r['scanned']}）"


# ---------------------------------------------------------------- ⑤ 干跑 vs 真删
def test_dry_run_does_not_delete(tmp_path):
    _p = _touch(str(tmp_path / "old.json.bak"), size=10, age_days=30)
    _r = br.sweep(str(tmp_path), max_age_days=7, dry_run=True)
    assert _r["dry_run"] is True
    assert len(_r["deletable"]) == 1
    assert os.path.exists(_p), "dry_run 把文件删了"
    assert _r["deleted"] == []


def test_apply_deletes_and_reports(tmp_path):
    _p = _touch(str(tmp_path / "old.json.bak"), size=1234, age_days=30)
    _r = br.sweep(str(tmp_path), max_age_days=7, dry_run=False)
    assert not os.path.exists(_p), "apply 没删掉"
    assert len(_r["deleted"]) == 1
    assert _r["deleted_bytes"] == 1234


def test_apply_only_touches_backups(tmp_path):
    """非备份后缀（活数据）在任何情况下都不许被删。"""
    _keep = _touch(str(tmp_path / "live.json"), size=10, age_days=400)
    _bak = _touch(str(tmp_path / "live.json.bak"), size=10, age_days=400)
    br.sweep(str(tmp_path), max_age_days=7, dry_run=False)
    assert os.path.exists(_keep), "活数据被删了"
    assert not os.path.exists(_bak)


# ---------------------------------------------------------------- ⑥ trim.bak
def test_trim_bak_treated_as_generation_zero(tmp_path):
    """`x.jsonl.trim.bak` 不是 `x.jsonl.bak` 的旧一代，各算各的 gen0。"""
    _touch(str(tmp_path / "x.jsonl.trim.bak"), size=10, age_days=100)
    _r = br.sweep(str(tmp_path), max_age_days=7, dry_run=False)
    assert len(_r["deleted"]) == 1, "trim.bak 未被超龄规则命中"
    assert os.path.exists(str(tmp_path / "x.jsonl")) is False  # 从未创建，仅确认不误判


# ---------------------------------------------------------------- ⑦ 边界
def test_missing_root_returns_empty(tmp_path):
    _r = br.scan(str(tmp_path / "nope"), max_age_days=7)
    assert _r["scanned"] == 0 and _r["deletable"] == []


def test_root_relative_paths_only(tmp_path):
    """返回的相对路径不得越出 root（防止把外部文件列进清理单）。"""
    _touch(str(tmp_path / "sub" / "deep.json.bak"), size=10, age_days=30)
    _r = br.scan(str(tmp_path), max_age_days=7)
    assert all(not os.path.isabs(x["rel"]) for x in _r["deletable"])
    assert all(".." not in x["rel"] for x in _r["deletable"])


def test_switch_off_skips_sweep(monkeypatch):
    """ENABLE_M437_BACKUP_RETENTION=False ⇒ maybe_sweep_once 不动作。"""
    import config
    monkeypatch.setattr(config, "ENABLE_M437_BACKUP_RETENTION", False, raising=True)
    _r = br.maybe_sweep_once("data")
    assert _r.get("skipped") is True
