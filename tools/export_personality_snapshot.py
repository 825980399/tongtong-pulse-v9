# -*- coding: utf-8 -*-
"""★185批刀2：人格快照落盘 + 启动哈希比对告警（tools 导出面）。

守 data/ 铁律：快照固定写入 data/personality_snapshot.json（可用环境变量
PULSE_PERSONALITY_SNAPSHOT_PATH 覆盖，便于测试隔离），经
nucleus.data.write_guard.guard_write 守卫；守卫拒写则放行失败、绝不自行绕过。

仅提供函数，不在导入时执行；由 PulsePersonalityKernel.__init__ 于启动时调用：
  - write_personality_snapshot(kernel)：落盘当前 export_personality_snapshot() 产物。
  - read_personality_snapshot_hash()：读落盘快照中的 l3_anchors_hash
    （不存在/损坏返回 None）。
  - compare_personality_snapshot_at_startup(kernel)：落盘哈希 vs 当前构造哈希，返回
    (disk_hash, current_hash, mismatch)。mismatch=True 表示落盘基线存在但与当前构造
    不一致（人格同一性漂移），需人工裁决；本模块绝不自动回滚、绝不覆盖已有基线。

不做：状态持久化（除首次基线落盘外）/ 演化写入 / 自动回滚。
"""
import json
import os

from nucleus.data.write_guard import guard_write
from nucleus.logger import get_module_logger

_LOGGER = get_module_logger("PersonalitySnapshot")


def _snapshot_path():
    """落盘固定路径：默认 data/personality_snapshot.json，可用环境变量覆盖。"""
    return os.environ.get(
        "PULSE_PERSONALITY_SNAPSHOT_PATH",
        os.path.join("data", "personality_snapshot.json"),
    )


def write_personality_snapshot(kernel, path=None):
    """将当前人格快照落盘到固定路径。成功返回 True，否则 False（不阻断启动）。"""
    _path = path or _snapshot_path()
    if not guard_write(_path, explicit=True, component="PersonalitySnapshot"):
        _LOGGER.warning("人格快照落盘被写盘守卫拒写，跳过: %s", _path)
        return False
    try:
        _snap = kernel.export_personality_snapshot(as_json=True)
        _data = json.loads(_snap) if isinstance(_snap, str) else _snap
        with open(_path, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _LOGGER.warning("人格快照落盘失败(已忽略): %s: %s", type(e).__name__, e)
        return False


def read_personality_snapshot_hash(path=None):
    """读落盘快照中的 l3_anchors_hash；文件不存在/损坏返回 None。"""
    _path = path or _snapshot_path()
    if not os.path.isfile(_path):
        return None
    try:
        with open(_path, "r", encoding="utf-8") as f:
            return json.load(f).get("l3_anchors_hash")
    except Exception as e:
        _LOGGER.debug("快照读取失败(忽略): %s: %s", type(e).__name__, e)
        return None


def compare_personality_snapshot_at_startup(kernel, path=None):
    """启动比对：落盘哈希 vs 当前构造哈希。返回 (disk_hash, current_hash, mismatch)。

    mismatch=True 表示落盘基线存在但与当前构造不一致（漂移），需人工裁决；
    本函数不回滚、不重写。
    """
    _current = kernel._compute_l3_anchors_hash()
    _disk = read_personality_snapshot_hash(path)
    return _disk, _current, (_disk is not None and _disk != _current)
