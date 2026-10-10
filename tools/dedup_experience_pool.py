"""dedup_experience_pool —— 经验池历史重复数据清理与降权

第五批 任务1（P0-2）：经验库写入去重的历史数据清理配套工具。

★根因（见 docs/经验库污染清理报告_20260909.md）：经验池被重复灌水，
1501 条仅 41 种不同 summary，最高频单句出现 843 次。写入端已在
ExperiencePool.record_experience 增加 summary 指纹去重（未来重复写入只累加
activation_count），本工具负责**历史数据**的标记降权：同一 summary 仅保留一条
作为有效经验（权重 1.0、activation_count=出现总次数），其余完全重复条目标记
polluted 且权重 ×0.0（×0 而非 ×0.1，彻底消除排序扭曲）。

★红线（与 ExperiencePollutionGuard 一致）：
  1. 只标记不删除：绝不物理删除任何条目；
  2. 降权消费：重复条目 quality_weight = 0.0；
  3. 高风险操作默认 dry-run，须 --apply 才落盘；框架运行时拒绝落盘。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

# 确保项目根在 sys.path，便于复用既有工具
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.data.DataAccessLayer import safe_read_json  # noqa: E402
from nucleus.logger import get_module_logger  # noqa: E402

# ★第五批：框架运行探测统一复用 tools._framework_probe，避免重复实现
from tools._framework_probe import _framework_looks_running  # noqa: E402

_logger = get_module_logger("dedup_experience_pool")

# 经验池主文件（相对项目根）
DEFAULT_POOL = os.path.join("data", "experience", "experience_pool.json")

# 去重聚类阈值：仅对足够长的 summary 做聚类，避免空/短摘要的体验互相误判
MIN_SUMMARY_LEN = 8

# 重复条目权重（×0.0：彻底不参与排序，区别于 ExperiencePollutionGuard 的 ×0.1）
WEIGHT_DUPLICATE = 0.0

# 备份后缀（第五批统一批次标记）
BAK_SUFFIX = ".bak_batch16"


def _project_root() -> str:
    return _PROJ


def plan(pool_path: str) -> dict:
    """分析经验池，返回去重方案与统计（不写盘）。

    返回字典包含：
      total / unique_nonempty_summary / polluted_count / clean_count /
      top_duplicates / by_summary(每种 summary 出现次数)
    """
    if not os.path.exists(pool_path):
        return {"exists": False, "path": pool_path}
    _d = safe_read_json(pool_path, default={})
    _items = _d.get("experiences", []) if isinstance(_d, dict) else _d

    _seen: dict[str, int] = {}          # summary -> 首次出现 index（代表）
    _count: dict[str, int] = {}         # summary -> 出现次数
    for _i, _it in enumerate(_items):
        if not isinstance(_it, dict):
            continue
        _s = (_it.get("summary") or "").strip()
        if len(_s) < MIN_SUMMARY_LEN:
            continue
        _count[_s] = _count.get(_s, 0) + 1
        if _s not in _seen:
            _seen[_s] = _i

    _nonempty_unique = len(_count)
    _nonempty_total = sum(_count.values())
    _polluted = max(0, _nonempty_total - _nonempty_unique)

    _top = sorted(_count.items(), key=lambda kv: kv[1], reverse=True)[:6]

    return {
        "exists": True,
        "path": pool_path,
        "total": len(_items),
        "nonempty_summary_total": _nonempty_total,
        "unique_nonempty_summary": _nonempty_unique,
        "polluted_count": _polluted,
        "clean_count": len(_items) - _polluted,
        "top_duplicates": [(s[:40], c) for s, c in _top],
    }


def apply_dedup(pool_path: str, backup: bool = True) -> dict:
    """执行去重标记（写盘）。

    每个唯一非空 summary 保留第一条为「代表」（权重 1.0、activation_count=总次数、
    去除 polluted 标记）；其余同 summary 条目标记 polluted 且权重 0.0。
    返回统计字典；框架运行时直接拒绝。
    """
    if _framework_looks_running():
        _logger.warning("[经验池去重] 检测到框架正在运行，拒绝落盘（请先停框架）")
        return {"applied": False, "reason": "framework_running"}

    _d = safe_read_json(pool_path, default={})
    _items = _d.get("experiences", []) if isinstance(_d, dict) else _d

    # 第一遍：统计每种 summary 的出现次数与代表 index
    _count: dict[str, int] = {}
    _rep: dict[str, int] = {}
    for _i, _it in enumerate(_items):
        if not isinstance(_it, dict):
            continue
        _s = (_it.get("summary") or "").strip()
        if len(_s) < MIN_SUMMARY_LEN:
            continue
        _count[_s] = _count.get(_s, 0) + 1
        if _s not in _rep:
            _rep[_s] = _i

    # 第二遍：标记
    _polluted = 0
    _clean = 0
    for _i, _it in enumerate(_items):
        if not isinstance(_it, dict):
            continue
        _s = (_it.get("summary") or "").strip()
        if len(_s) < MIN_SUMMARY_LEN:
            # 空/短摘要：保持原样（视为不同体验，不污染）
            _clean += 1
            continue
        if _rep.get(_s) == _i:
            # 代表条目：有效经验
            _it.pop("polluted", None)
            _it.pop("quality_flag", None)
            _it.pop("pollution_reason", None)
            _it["quality_weight"] = 1.0
            _it["activation_count"] = int(_count[_s])
            _clean += 1
        else:
            # 重复条目：标记 polluted + 权重 ×0.0
            _it["polluted"] = True
            _it["quality_flag"] = "polluted"
            _it["pollution_reason"] = "内容重复(历史灌水)"
            _it["quality_weight"] = WEIGHT_DUPLICATE
            _polluted += 1

    if backup and not os.path.exists(pool_path + BAK_SUFFIX):
        shutil.copy2(pool_path, pool_path + BAK_SUFFIX)

    if isinstance(_d, dict):
        _d["experiences"] = _items
        _tmp = pool_path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_d, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, pool_path)

    _logger.info(
        f"[经验池去重] 完成：共 {len(_items)} 条，标记 polluted {_polluted} 条，"
        f"有效经验 {_clean} 条（唯一 summary {len(_rep)} 种）")
    return {
        "applied": True,
        "total": len(_items),
        "polluted": _polluted,
        "clean": _clean,
        "unique_summary": len(_rep),
        "backup": pool_path + BAK_SUFFIX if backup else None,
    }


def main() -> int:
    import argparse
    _p = argparse.ArgumentParser(description="经验池历史重复数据清理与降权（第五批任务1）")
    _p.add_argument("--pool", default=DEFAULT_POOL, help="经验池 json 路径")
    _p.add_argument("--apply", action="store_true", help="真正落盘（默认 dry-run 仅预览）")
    _args = _p.parse_args()

    _pool = os.path.join(_project_root(), _args.pool) \
        if not os.path.isabs(_args.pool) else _args.pool

    if not os.path.exists(_pool):
        print(f"[错误] 经验池文件不存在: {_pool}")
        return 2

    _plan = plan(_pool)
    print("=== 经验池去重方案预览 ===")
    print(f"文件路径: {_plan.get('path')}")
    print(f"总条目:   {_plan.get('total')}")
    print(f"非空 summary 条目: {_plan.get('nonempty_summary_total')}")
    print(f"唯一内容(非空 summary 种类): {_plan.get('unique_nonempty_summary')}")
    print(f"将标记 polluted(权重×0.0) 条数: {_plan.get('polluted_count')}")
    print(f"保留有效经验条数: {_plan.get('clean_count')}")
    print("最高频重复 summary(前6):")
    for _s, _c in _plan.get("top_duplicates", []):
        print(f"   {_c:>4}次 | {_s!r}")

    if not _args.apply:
        print("\n[dry-run] 未修改任何文件。加 --apply 执行落盘（框架运行时会被拒绝）。")
        return 0

    _res = apply_dedup(_pool, backup=True)
    if not _res.get("applied"):
        print(f"[未执行] {_res.get('reason')}")
        return 1
    print("\n[apply] 已落盘：")
    print(f"  polluted={_res['polluted']}  clean={_res['clean']}  "
          f"唯一summary={_res['unique_summary']}")
    print(f"  备份: {_res['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
