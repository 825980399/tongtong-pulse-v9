# -*- coding: utf-8 -*-
"""第435批刀1 单测：备份链按文件大小分级（小文件 1 代 / 大文件 0 代 / 关灰度回退 3 代）。

守三件事（都跟「写放大」直接相关）：
  ① 小文件（<= 阈值）只 copy2 **1 次**、只留 1 代 `.bak`，更早的 `.bak.1/.bak.2` 被清；
  ② 大文件（> 阈值）**0 次 copy2**、不留 `.bak`（写放大 1×）；
  ③ `ENABLE_M435_BACKUP_TIER=False` 时逐字回到原 3 代滚动。
"""
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.data.DataAccessLayer as DAL


def _count_copy2(monkeypatch):
    """统计 _keep_backup 期间 shutil.copy2 的调用次数（写放大的直接判据）。"""
    _calls = []
    _orig = shutil.copy2

    def _spy(src, dst, *a, **kw):
        _calls.append((str(src), str(dst)))
        return _orig(src, dst, *a, **kw)

    monkeypatch.setattr(DAL.shutil, "copy2", _spy)
    return _calls


def test_small_file_keeps_one_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(DAL, "_m435_backup_tier_on", lambda: True)
    _p = tmp_path / "small.json"
    _p.write_text('{"a":1}', encoding="utf-8")
    (tmp_path / "small.json.bak.1").write_text("old1", encoding="utf-8")
    (tmp_path / "small.json.bak.2").write_text("old2", encoding="utf-8")

    _calls = _count_copy2(monkeypatch)
    DAL._keep_backup(str(_p))

    assert len(_calls) == 1, f"小文件 copy2 次数={len(_calls)}（应 1 次，写放大 2×）"
    assert (tmp_path / "small.json.bak").exists()
    assert not (tmp_path / "small.json.bak.1").exists(), "更早的 .bak.1 没清"
    assert not (tmp_path / "small.json.bak.2").exists(), "更早的 .bak.2 没清"


def test_large_file_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(DAL, "_m435_backup_tier_on", lambda: True)
    _p = tmp_path / "big.json"
    _p.write_text("x" * (1024 * 1024 + 100), encoding="utf-8")

    _calls = _count_copy2(monkeypatch)
    DAL._keep_backup(str(_p))

    assert len(_calls) == 0, f"大文件 copy2 次数={len(_calls)}（应 0 次，写放大 1×）"
    assert not (tmp_path / "big.json.bak").exists(), "大文件不该留 .bak"


def test_switch_off_restores_three_generations(tmp_path, monkeypatch):
    monkeypatch.setattr(DAL, "_m435_backup_tier_on", lambda: False)
    _p = tmp_path / "small.json"
    _p.write_text('{"a":1}', encoding="utf-8")
    (tmp_path / "small.json.bak").write_text("gen1", encoding="utf-8")

    _calls = _count_copy2(monkeypatch)
    DAL._keep_backup(str(_p))

    # 回退路径：.bak → .bak.1 → .bak.2（1 次）+ path → .bak（1 次）= 2 次
    assert len(_calls) == 2, f"回退路径 copy2 次数={len(_calls)}（原语义应 2 次：滚 .bak + 写新 .bak）"
    assert (tmp_path / "small.json.bak").exists()
    assert (tmp_path / "small.json.bak.1").exists(), "回退路径应保留 .bak.1"


def test_threshold_configurable(tmp_path, monkeypatch):
    """阈值可降到很小 ⇒ 原本「小文件」也走大文件分支（0 备份）。"""
    monkeypatch.setattr(DAL, "_m435_backup_tier_on", lambda: True)
    monkeypatch.setattr(DAL, "_m435_backup_small_max_bytes", lambda: 10)
    _p = tmp_path / "mid.json"
    _p.write_text('{"a":"' + "y" * 100 + '"}', encoding="utf-8")

    _calls = _count_copy2(monkeypatch)
    DAL._keep_backup(str(_p))

    assert len(_calls) == 0, f"阈值 10B 时 copy2={len(_calls)}（应 0 次）"
    assert not (tmp_path / "mid.json.bak").exists()
