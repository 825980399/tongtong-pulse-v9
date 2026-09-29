# -*- coding: utf-8 -*-
"""★往期批次 T146-7：JSON 读路径 BOM 兼容回归测试。

背景：带 UTF-8 BOM 的 JSON 用 strict `utf-8` 能解码成功，但 `json.loads` 会抛
"Unexpected UTF-8 BOM"，表现为持续 ERROR / WARNING 且一律回落默认值
（补丁列表、指标采集静默失真）。

修复口径（本批）：
  · `PatchManager._load_json` 改 `encoding="utf-8-sig"`；
  · `PulseMetricsCollector` 的 pending 统计改走 `DataAccessLayer.safe_read_json`
    （其编码回退链首位即 utf-8-sig）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _write_with_bom(path: str, obj: dict) -> None:
    """以带 BOM 的 UTF-8 写入 JSON（复现 Windows 工具/编辑器产出的脏文件）。"""
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(json.dumps(obj, ensure_ascii=False))


def test_safe_read_json_handles_bom(tmp_path):
    """DAL.safe_read_json 必须透明吃掉 BOM。"""
    from nucleus.data.DataAccessLayer import safe_read_json

    p = tmp_path / "bom.json"
    _write_with_bom(str(p), {"patches": [{"id": "D1", "needs_repair": True}]})
    with open(p, "rb") as f:
        head = f.read(3)
    assert head == b"\xef\xbb\xbf", "夹具未真正写出 BOM：%r" % head

    data = safe_read_json(str(p), {})
    assert isinstance(data, dict), "BOM 文件应解析为 dict，实际=%r" % type(data)
    assert data.get("patches"), "BOM 文件应读出内容，实际=%r" % data


def test_patch_manager_load_json_handles_bom(tmp_path):
    """PatchManager._load_json 读到 BOM 文件必须返回真实内容，而非回落默认值。"""
    from nucleus.reasoning.PatchManager import PatchManager

    pm = PatchManager(str(tmp_path / "patches"))
    p = tmp_path / "bom_pm.json"
    _write_with_bom(str(p), {"total": 7, "note": "带BOM"})

    sentinel = {"__default__": True}
    got = pm._load_json(str(p), sentinel)
    assert got is not sentinel, "BOM 文件不应回落默认值（说明仍报 Unexpected BOM）"
    assert got.get("total") == 7, "读出内容不符：%r" % got
    assert got.get("note") == "带BOM"


def test_patch_manager_load_json_handles_plain_utf8(tmp_path):
    """回归保障：无 BOM 的普通 utf-8 行为不变。"""
    from nucleus.reasoning.PatchManager import PatchManager

    pm = PatchManager(str(tmp_path / "patches2"))
    p = tmp_path / "plain.json"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"total": 3}, ensure_ascii=False))

    assert pm._load_json(str(p), {}) == {"total": 3}


def test_patch_manager_empty_and_missing_file(tmp_path):
    """既有语义保持：空文件与不存在的文件都回落默认值，不抛异常。"""
    from nucleus.reasoning.PatchManager import PatchManager

    pm = PatchManager(str(tmp_path / "patches3"))
    sentinel = {"__d__": 1}
    empty = tmp_path / "empty.json"
    empty.write_text("   \n", encoding="utf-8")
    assert pm._load_json(str(empty), sentinel) is sentinel
    assert pm._load_json(str(tmp_path / "nope.json"), sentinel) is sentinel


def test_metrics_collector_uses_bom_safe_reader():
    """PulseMetricsCollector 的 pending 读取须改走 BOM 安全的 DAL 通道。

    该读取 inline 在快照构造里、无独立函数可调用，故此处以**源码契约**锁定：
    不得再出现裸 open(encoding="utf-8") 读 pending_patches.json。
    """
    src_path = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    assert "pending_patches.json" in src, "采集点已迁移，请同步更新本断言"
    assert 'open(_pp, encoding="utf-8")' not in src, \
        "仍存在不兼容 BOM 的裸读取：open(_pp, encoding=\"utf-8\")"
    assert "safe_read_json" in src, \
        "应改走 DataAccessLayer.safe_read_json（其编码链首位 utf-8-sig）"
