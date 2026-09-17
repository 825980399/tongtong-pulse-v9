"""P2-26 器官别名占位符污染节点清理工具（只标记不删除）

问题背景：
    代码学习曾把未替换的占位符字面量「[器官别名]」当作器官别名写入知识库，
    生成出形如 "[器官别名] [器官别名] 人格内核（PulsePersonalityKernel）是框架中的仿生器官..."
    的异常节点（见 2026-09-09 星轨任务书第四批 任务2）。

本工具：
    1. 流式扫描知识快照（复用 mark_duplicate_nodes 的 iter_snapshot_nodes，内存恒定，不整体加载 300MB+ 文件）；
    2. 识别 /自我理解/器官别名/ 路径下、value 仍含占位符「[器官别名]」的异常节点；
    3. 默认 dry-run 只读统计；--apply 时仅「标记」（quality_flag=placeholder_alias，trust_score 降为 0），
       绝不删除，并先自动备份快照。

安全约束（与 P2-8 一致）：
    落盘前若检测到框架仍在运行（快照近期被写入 / 存在主框架进程），则拒绝 --apply，
    必须先停止框架，否则标记会被下一次保存覆盖。可用 --force 强制（不推荐）。

用法：
    python tools/cleanup_alias_placeholder_nodes.py            # dry-run 只读报告
    python tools/cleanup_alias_placeholder_nodes.py --apply    # 标记异常节点
    python tools/cleanup_alias_placeholder_nodes.py --snapshot data/knowledge/pulse_l1_snapshot.json
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# 确保项目根与 tools/ 目录均在 sys.path，便于导入既有工具与 nucleus 包。
# 注意：mark_duplicate_nodes.py 位于 tools/ 目录，必须将该目录也加入 sys.path，
# 否则以 `python -m tools.cleanup_alias_placeholder_nodes` 或测试内 `from tools...` 方式
# 导入时会出现 ModuleNotFoundError: No module named 'mark_duplicate_nodes'。
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.dirname(os.path.abspath(__file__))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

# 复用既有流式扫描与「边读边写标记」工具（其 __main__ 守卫保证 import 安全）
from mark_duplicate_nodes import (  # noqa: E402
    iter_snapshot_nodes,
    iter_node_spans,
    mark_and_rewrite,
)

_PLACEHOLDER = "[器官别名]"
_ALIAS_PREFIX = "/自我理解/器官别名/"


def is_placeholder_alias_node(node: dict) -> bool:
    """判定一个节点是否为占位符污染节点。"""
    if not isinstance(node, dict):
        return False
    _path = str(node.get("space_path", "") or "")
    if not _path.startswith(_ALIAS_PREFIX):
        return False
    _val = str(node.get("value", "") or "")
    if _PLACEHOLDER in _val:
        return True
    # 路径末段本身就是占位符（如 /自我理解/器官别名/[器官别名]）
    _seg = _path[len(_ALIAS_PREFIX):].strip("/")
    return _seg == "[器官别名]"


def scan(snapshot: str) -> list[dict]:
    """流式扫描，返回所有占位符污染节点（含 node_id / space_path / value 预览）。"""
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
    """粗略判断框架是否仍在运行：快照近期被写入，或存在主框架 python 进程。"""
    try:
        if os.path.exists(snapshot):
            _age = time.time() - os.path.getmtime(snapshot)
            if _age < threshold_sec:
                return True
    except OSError:
        pass
    # 轮询常见主框架进程（按内存占用尺度粗判，约 >1GB）
    try:
        import psutil
        for _p in psutil.process_iter(["name", "memory_info"]):
            try:
                if _p.info.get("name", "").lower().startswith("python"):
                    _rss = (_p.info.get("memory_info") or psutil.Process().memory_info()).rss
                    if _rss > 1_000_000_000:
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def main() -> int:
    _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _proj not in sys.path:
        sys.path.insert(0, _proj)

    _ap = argparse.ArgumentParser(description="器官别名占位符污染节点清理（只标记不删除）")
    _ap.add_argument("--snapshot", default=os.path.join(_proj, "data", "knowledge",
                                                        "pulse_l1_snapshot.json"),
                     help="待扫描的知识快照路径（默认 pulse_l1_snapshot.json）")
    _ap.add_argument("--apply", action="store_true", help="标记异常节点（默认仅 dry-run 报告）")
    _ap.add_argument("--fix", action="store_true",
                     help="--apply 时改为「修复」：把 '[器官别名]' 前缀重写为真实别名（而非仅标记）")
    _ap.add_argument("--force", action="store_true", help="即使框架疑似运行也强制落盘（不推荐）")
    _args = _ap.parse_args()

    if not os.path.exists(_args.snapshot):
        print(f"[错误] 快照不存在: {_args.snapshot}")
        return 2

    print(f"[扫描] {_args.snapshot}")
    _hits = scan(_args.snapshot)
    print(f"[结果] 占位符污染节点共 {len(_hits)} 个")
    for _h in _hits[:50]:
        print(f"  - {_h['node_id']} | {_h['space_path']} | {_h['value_preview']}")
    if len(_hits) > 50:
        print(f"  ... 其余 {len(_hits) - 50} 个省略")

    if not _args.apply:
        print("\n[dry-run] 未做任何修改。加 --apply 进行标记（只标记不删除）。")
        return 0

    # ---- 落盘前安全校验 ----
    if _framework_looks_running(_args.snapshot) and not _args.force:
        print("\n[拒绝] 检测到框架疑似仍在运行（快照近期被写入或存在主框架进程）。")
        print("       请先停止框架，否则标记会被下一次保存覆盖。如需强制，加 --force。")
        return 3

    if _args.fix:
        # 修复：把 '[器官别名]' 前缀重写为真实别名（路径末段）
        _marks = {}
        for h in _hits:
            if not h["node_id"]:
                continue
            _alias = str(h["space_path"] or "").rsplit("/", 1)[-1].strip("/")
            if not _alias or _alias == "[器官别名]":
                continue
            _marks[str(h["node_id"])] = _alias
        # 用自定义重写逻辑（替换首个 [器官别名] 为 [alias]）
        _done = _rewrite_with_fix(_args.snapshot, _marks, backup_suffix=".bak_batch15")
        print(f"\n[完成] 已修复 {_done} 个占位符污染节点（前缀重写为真实别名）。"
              f"原快照已备份为 .bak_batch15。")
    else:
        # 标记：只标记不删除（默认，最安全）
        _marks = {str(h["node_id"]): {"quality_flag": "placeholder_alias", "trust_score": 0.0}
                  for h in _hits if h["node_id"]}
        _done = mark_and_rewrite(_args.snapshot, _marks, backup_suffix=".bak_batch15")
        print(f"\n[完成] 已标记 {_done} 个占位符污染节点（quality_flag=placeholder_alias）。"
              f"原快照已备份为 .bak_batch15。")
    return 0


def _rewrite_with_fix(path: str, marks: dict, backup_suffix: str = ".bak_batch15") -> int:
    """边读边写：把每个目标节点 value 中的首个 '[器官别名]' 重写为 '[alias]'。"""
    import json
    import shutil
    if not marks:
        return 0
    _bak = path + backup_suffix
    if not os.path.exists(_bak):
        shutil.copy2(path, _bak)
        print(f"[备份] 已生成 {_bak}")
    _tmp = path + ".tmp_batch15"
    _done = 0
    with open(path, "rb") as _src, open(_tmp, "wb") as _dst:
        _cursor = 0
        for _s, _e, _raw in iter_node_spans(path):
            try:
                _node = json.loads(_raw.decode("utf-8"))
            except Exception as e:
                print(f"[WARNING] cleanup_alias_placeholder_nodes.py:185: {type(e).__name__}: {e}")
                continue
            _alias = marks.get(str(_node.get("node_id", "")))
            if not _alias:
                continue
            _val = str(_node.get("value", "") or "")
            if "[器官别名]" in _val:
                # 替换全部出现（含双占位符 "[器官别名] [器官别名] ..." 的情况）
                _node["value"] = _val.replace("[器官别名]", f"[{_alias}]")
                _done += 1
            _dst.write(_src.read(_s - _cursor))
            _dst.write(json.dumps(_node, ensure_ascii=False).encode("utf-8"))
            _src.seek(_e)
            _cursor = _e
        _dst.write(_src.read())
    os.replace(_tmp, path)
    return _done


if __name__ == "__main__":
    raise SystemExit(main())
