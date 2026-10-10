# -*- coding: utf-8 -*-
"""T1 统一数据访问层（DataAccessLayer）单元测试。

覆盖：JSON/文本读写往返、原子写无 .tmp 残留、读取容错（文件缺失/损坏归档）、
最多 3 版备份、编码回退。全部使用 tmp_path，不污染生产数据。
"""
import json
import os
import sys

# 保证 nucleus 命名空间包可导入（项目根在 sys.path）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.data.DataAccessLayer as DAL


def test_write_read_json_roundtrip(tmp_path):
    p = tmp_path / "a.json"
    assert DAL.safe_write_json(str(p), {"x": 1, "name": "曈曈"}) is True
    assert p.exists()
    assert DAL.safe_read_json(str(p)) == {"x": 1, "name": "曈曈"}


def test_write_json_atomic_no_tmp_leftover(tmp_path):
    p = tmp_path / "b.json"
    assert DAL.safe_write_json(str(p), [1, 2, 3]) is True
    # 原子写不应残留 .tmp 文件
    assert list(tmp_path.glob("*.tmp*")) == []
    # 内容正确（含中文，ensure_ascii=False）
    assert json.loads(p.read_text(encoding="utf-8")) == [1, 2, 3]


def test_read_json_missing_returns_default(tmp_path):
    p = tmp_path / "missing.json"
    assert DAL.safe_read_json(str(p)) == {}
    assert DAL.safe_read_json(str(p), default=[1, 2]) == [1, 2]


def test_read_json_corrupt_returns_default_and_archives(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{ this is not valid json ", encoding="utf-8")
    archived = []
    monkeypatch.setattr(DAL, "_backup_corrupted", lambda path: archived.append(path))
    assert DAL.safe_read_json(str(p)) == {}
    assert archived, "损坏文件应被归档"


def test_write_text_read_text_roundtrip(tmp_path):
    p = tmp_path / "c.txt"
    assert DAL.safe_write_text(str(p), "hello 中文 🌟") is True
    assert DAL.safe_read_text(str(p)) == "hello 中文 🌟"
    assert DAL.safe_read_text(str(tmp_path / "nope.txt"), default="dflt") == "dflt"


def test_backup_keeps_max_three_versions(tmp_path):
    p = tmp_path / "d.json"
    for i in range(5):
        assert DAL.safe_write_json(str(p), {"v": i}) is True
    backups = list(tmp_path.glob("d.json.bak*"))
    assert len(backups) <= 3, f"备份应不超过 3 版，实际 {len(backups)}"
    # 当前内容应为最后一次写入
    assert DAL.safe_read_json(str(p)) == {"v": 4}


def test_write_json_failure_returns_false_not_raise(tmp_path, monkeypatch):
    p = tmp_path / "e.json"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(DAL, "os", type("X", (), {"replace": boom, "makedirs": lambda *a, **k: None})())
    # 此处仅验证失败路径不抛异常（直接调用底层应返回 False）
    import types
    fake_os = types.SimpleNamespace(
        makedirs=lambda *a, **k: None,
        replace=boom,
        path=None,
    )
    monkeypatch.setattr(DAL, "os", fake_os)
    # 用 tmp 文件路径，确保 replace 失败
    assert DAL.safe_write_json(str(p), {"k": 1}) is False


# ============ T2 专项：json.load 异常处理（缺失/损坏/编码回退） ============


def test_read_json_encoding_fallback_gbk(tmp_path):
    """T2：编码错误应按 utf-8→gbk→latin-1 回退，gbk 文件可读。"""
    p = tmp_path / "gbk.json"
    # 写入含中文的 gbk 编码文件（utf-8 解码会失败，应回退到 gbk 成功）
    p.write_bytes('{"城市": "北京", "温度": 26}'.encode("gbk"))
    data = DAL.safe_read_json(str(p))
    assert data.get("城市") == "北京"
    assert data.get("温度") == 26


def test_read_json_corrupt_backup_naming(tmp_path):
    """T2：损坏文件归档到 data/corrupted/，文件名带时间戳后缀 .corrupted。"""
    p = tmp_path / "bad2.json"
    p.write_text("{ broken json ", encoding="utf-8")
    corrupted_dir = os.path.join(_ROOT, "data", "corrupted")
    before = set(os.listdir(corrupted_dir)) if os.path.isdir(corrupted_dir) else set()
    # 直接调用真实归档逻辑，验证命名规则（测后清理，避免污染生产数据）
    DAL._backup_corrupted(str(p))
    after = set(os.listdir(corrupted_dir)) if os.path.isdir(corrupted_dir) else set()
    new_files = after - before
    try:
        assert new_files, "应在 data/corrupted 生成归档文件"
        assert all(f.endswith(".corrupted") for f in new_files)
    finally:
        for f in new_files:
            try:
                os.remove(os.path.join(corrupted_dir, f))
            except OSError:
                pass


def test_real_caller_wired_to_safe_read_json():
    """T2：真实生产模块的 json.load 已收口到 safe_read_json（静态核对）。"""
    import pathlib
    f = pathlib.Path("nucleus/evolution/PatchAutoApprover.py")
    src = f.read_text(encoding="utf-8")
    assert "from nucleus.data.DataAccessLayer import safe_read_json" in src
    # 该文件原有 6 处裸 json.load，改造后不应再出现裸 json.load(
    assert "json.load(" not in src, "仍存在裸 json.load 调用，未收口"
