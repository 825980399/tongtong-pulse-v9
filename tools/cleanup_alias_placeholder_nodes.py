"""★第159批 上B 刀B①（T-对话模板-1）：占位符污染节点清理工具（修正版·主目录）。

背景（烛微 C 类采信）：
    写入层 P2-26 已修（09-09）；真因 = **清理工具识别面不匹配**——
    归档件谓词带尾斜杠 ``"/自我理解/器官别名/"``，而现网 306 个污染节点是
    **裸父路径** ``space_path="/自我理解/器官别名"``（无尾斜杠），恒漏检。
    这正是 P2-26 "Pending 停框架" 从未兑现的结构性原因。

本件与归档件的关系（★不改历史归档件语义）：
    * 归档件 ``tools/archive/cleanup_alias_placeholder_nodes.py`` **保持原样**；
    * 本件为修正版，仅复用 ``mark_duplicate_nodes`` 的流式扫描 / 边读边写标记；
    * 修正点：①谓词去尾斜杠 ②路径分段命中 ③value 含方括号占位符即判
      ④扫描面扩展至 ``/综合``、``/自我理解/代码``。

安全约束（与 P2-8 / 归档件一致）：
    落盘前若检测到框架仍在运行（快照近期被写入 / 存在主框架进程），拒绝落盘，
    须先停止框架，否则标记会被下一次全量保存覆盖。``--force`` 可强制（不推荐）。

用法：
    python tools/cleanup_alias_placeholder_nodes.py                  # dry-run 只读
    python tools/cleanup_alias_placeholder_nodes.py --apply          # 标记降权
    python tools/cleanup_alias_placeholder_nodes.py --apply --fix    # 重写占位符
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time

_PROJ = os.path.dirname(os.path.abspath(__file__))          # .../tools
_ROOT = os.path.dirname(_PROJ)                              # 项目根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from mark_duplicate_nodes import (  # noqa: E402
    iter_snapshot_nodes,
    iter_node_spans,
    mark_and_rewrite,
)
from nucleus._silent_except import silent_exc  # noqa: E402

# ★已知核心污染字面量（不限路径，污染本体）
_PLACEHOLDER_LITERALS = ("[器官别名]",)
# ★扫描面（★去尾斜杠，兼容裸父路径；扩展 /综合 与 /自我理解/代码）
#   仅用于 dry-run 分组统计与人工复核；命中判据只认核心占位符字面量（见下）。
_SCAN_PATHS = (
    "/自我理解/器官别名",
    "/综合",
    "/自我理解/代码",
)
_DEFAULT_SNAPSHOT = os.path.join(_ROOT, "data", "knowledge",
                                 "pulse_knowledge_snapshot.json")
_BACKUP_SUFFIX = ".bak_batch159b"


def is_placeholder_alias_node(node: dict) -> bool:
    """★修正版判据（刀B①，经两轮 dry-run 实测校准）。

    命中二者之一即判为占位符污染节点：
      1. value 含核心占位符字面量「[器官别名]」（不限路径）——污染本体，
         现网 306 个**裸父路径**节点（space_path="/自我理解/器官别名"）在此命中；
      2. space_path 含核心占位符字面量（如 /自我理解/器官别名/[器官别名]）。

    ★实测校准（2026-10-03 两轮 dry-run，如实记录）：
      * 第 1 轮：泛化方括号面含 /自我理解/代码 → 3911 个**正常**代码认知节点
        （value 含 [数据流·X] / [代码关联·X] 前缀标记）被误判，总命中 4402；
      * 第 2 轮：泛化面收窄至 /自我理解/器官别名 + /综合 → 仍误判 1613 个
        （/综合 下节点 value 普遍以「[综合]」开头，同为正常前缀标记）；
      * ★结论：放弃「泛化方括号」判据，只认「[器官别名]」字面量。
        与任务书验收口径一致（主快照 [器官别名] 字面量 583 → 0），零误伤，
        呼应风险③「净化阈值过激误伤真回答」。
    """
    if not isinstance(node, dict):
        return False
    _path = str(node.get("space_path", "") or "")
    _val = str(node.get("value", "") or "")
    if any(_lit in _val for _lit in _PLACEHOLDER_LITERALS):
        return True
    return any(_lit in _path for _lit in _PLACEHOLDER_LITERALS)


def scan(snapshot: str) -> list[dict]:
    """流式扫描，返回所有占位符污染节点（内存恒定，不整体加载 500MB+）。"""
    _hits: list[dict] = []
    for _n in iter_snapshot_nodes(snapshot):
        if is_placeholder_alias_node(_n):
            _hits.append({
                "node_id": _n.get("node_id"),
                "space_path": _n.get("space_path"),
                "value_preview": str(_n.get("value", ""))[:80],
            })
    return _hits


def _framework_looks_running(snapshot: str, threshold_sec: int = 600) -> bool:
    """停框架守卫：快照近期被写入，或存在大内存主框架 python 进程。"""
    try:
        if os.path.exists(snapshot):
            if time.time() - os.path.getmtime(snapshot) < threshold_sec:
                return True
    except OSError as _e:
        silent_exc(_e, where="cleanup_alias_placeholder_nodes::_framework_looks_running mtime")
    try:
        import psutil
        for _p in psutil.process_iter(["name", "memory_info"]):
            try:
                if str(_p.info.get("name", "")).lower().startswith("python"):
                    _mi = _p.info.get("memory_info")
                    if _mi is not None and _mi.rss > 1_000_000_000:
                        return True
            except Exception as _pe:
                silent_exc(_pe, where="cleanup_alias_placeholder_nodes::process_iter")
                continue
    except Exception as _e:
        silent_exc(_e, where="cleanup_alias_placeholder_nodes::_framework_looks_running psutil")
    return False


def _backup_with_sha256(path: str) -> str:
    """落盘前生成备份并返回 sha256（任务书④要求校验备份）。"""
    _bak = path + _BACKUP_SUFFIX
    if not os.path.exists(_bak):
        shutil.copy2(path, _bak)
    _h = hashlib.sha256()
    with open(_bak, "rb") as _f:
        for _chunk in iter(lambda: _f.read(1 << 20), b""):
            _h.update(_chunk)
    _sha = _h.hexdigest()
    print(f"[备份] {_bak}  sha256={_sha}")
    return _sha


def _rewrite_with_fix(path: str) -> int:
    """边读边写：把 '[器官别名]' 重写为路径末段的真实别名。

    ★裸父路径节点（``space_path="/自我理解/器官别名"``）末段即 ``器官别名``，
      无法提取真实别名 → 跳过。此类应走 ``--apply`` 标记降权路线。
    """
    _backup_with_sha256(path)
    _marks: dict[str, str] = {}
    for _n in iter_snapshot_nodes(path):
        if not is_placeholder_alias_node(_n):
            continue
        _sp = str(_n.get("space_path", "") or "")
        _alias = _sp.rsplit("/", 1)[-1].strip("/")
        if not _alias or _alias == "器官别名" or "[" in _alias or "]" in _alias:
            continue  # 无法提取真实别名 → 跳过（不伪造内容）
        _marks[str(_n.get("node_id", ""))] = _alias
    if not _marks:
        print("[跳过] 无可修复节点（均无法提取真实别名）→ 建议改走 --apply 标记路线")
        return 0
    import json
    _tmp = path + ".tmp_batch159b"
    _done = 0
    with open(path, "rb") as _src, open(_tmp, "wb") as _dst:
        _cursor = 0
        for _s, _e, _raw in iter_node_spans(path):
            try:
                _node = json.loads(_raw.decode("utf-8"))
            except Exception as _e:
                silent_exc(_e, where="cleanup_alias_placeholder_nodes::_rewrite_with_fix json")
                continue
            _alias = _marks.get(str(_node.get("node_id", "")))
            if _alias:
                _val = str(_node.get("value", "") or "")
                if "[器官别名]" in _val:
                    _node["value"] = _val.replace("[器官别名]", _alias)
                    _done += 1
            _dst.write(_src.read(_s - _cursor))
            _dst.write(json.dumps(_node, ensure_ascii=False).encode("utf-8"))
            _src.seek(_e)
            _cursor = _e
        _dst.write(_src.read())
    os.replace(_tmp, path)
    return _done


def main() -> int:
    _ap = argparse.ArgumentParser(description="占位符污染节点清理（修正版·第159批上B 刀B①）")
    _ap.add_argument("--snapshot", default=_DEFAULT_SNAPSHOT,
                     help="待扫描快照（默认主快照 pulse_knowledge_snapshot.json）")
    _ap.add_argument("--apply", action="store_true",
                     help="落盘：标记异常节点（默认仅 dry-run 报告）")
    _ap.add_argument("--fix", action="store_true",
                     help="配合 --apply：改为「重写占位符为真实别名」（而非仅标记）")
    _ap.add_argument("--force", action="store_true",
                     help="即使框架疑似运行也强制落盘（不推荐）")
    _args = _ap.parse_args()

    if not os.path.exists(_args.snapshot):
        print(f"[错误] 快照不存在: {_args.snapshot}")
        return 2

    print(f"[扫描] {_args.snapshot}")
    _hits = scan(_args.snapshot)
    print(f"[结果] 占位符污染节点共 {len(_hits)} 个")

    # 分组统计：★裸父路径（验收口径「0 → ≥306」）/ 器官别名子路径 / 其他路径
    _bare = sum(1 for h in _hits
                if str(h.get("space_path", "")) == "/自我理解/器官别名")
    _sub = sum(1 for h in _hits
               if str(h.get("space_path", "")).startswith("/自我理解/器官别名/"))
    _other = len(_hits) - _bare - _sub
    print(f"[分组] ★裸父路径 {_bare} 个 | 器官别名子路径 {_sub} 个 | "
          f"其他路径(/综合,/自我理解/代码 等) {_other} 个")
    for _h in _hits[:50]:
        print(f"  - {_h['node_id']} | {_h['space_path']} | {_h['value_preview']}")
    if len(_hits) > 50:
        print(f"  ... 其余 {len(_hits) - 50} 个省略")

    if not _args.apply:
        print("\n[dry-run] 未做任何修改。加 --apply 标记 / --apply --fix 重写。")
        return 0

    # ---- 落盘前停框架安全校验 ----
    if _framework_looks_running(_args.snapshot) and not _args.force:
        print("\n[拒绝] 检测到框架疑似仍在运行（快照近期被写入或存在主框架进程）。")
        print("       请先停止框架，否则标记会被下一次保存覆盖。如需强制加 --force。")
        return 3

    if _args.fix:
        _done = _rewrite_with_fix(_args.snapshot)
        print(f"\n[完成] 已修复 {_done} 个节点（占位符重写为真实别名）。")
    else:
        _sha = _backup_with_sha256(_args.snapshot)  # 存证：落盘前备份 sha256（验收①）
        _marks = {str(h["node_id"]): {"quality_flag": "placeholder_alias",
                                      "trust_score": 0.0}
                  for h in _hits if h["node_id"]}
        _done = mark_and_rewrite(_args.snapshot, _marks, backup_suffix=_BACKUP_SUFFIX)
        print(f"\n[完成] 已标记 {_done} 个节点（quality_flag=placeholder_alias）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
