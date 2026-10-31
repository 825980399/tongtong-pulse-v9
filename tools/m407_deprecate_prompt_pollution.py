# -*- coding: utf-8 -*-
"""407 刀1：把「代码学习模板 prompt」从推理经验库里**标记剔除**（不删数据、可回滚）。

背景（407 T0 实测）
-------------------
`data/context/reasoning_experience.json` 共 128 条，其中 114 条是
`remote_api_confirmed` 型污染，**97 条的 `example_question` 就是代码学习用的那条模板
prompt**（`请分析以下Python方法，并返回一个JSON格式的结果…`）。源头是
`organs/body/PulseLung.py` 的「回复质量高 ⇒ 记一条经验」分支：它把**我们自己发出去的
指令**当成了"用户问题"记进库。这些条目恒定抢占 top1，命中后又被
`PulseInnerWorld._SKIP_TYPES` 整类砍掉 ⇒ 经验路由永远 0 有效产出。

★策略：**标记，不删除**
    复用项目既有清洗口径（`nucleus/data/experience_cleanup.py` 那一套字段名），
    搜索侧由 `ReasoningExperience.search()` 的污染闸按 `polluted is True` /
    `is_cleaned is False` 拦下 ⇒ 它们不再参与打分，但原始文本一条不少，
    `--restore` 可按批次号精确回滚。

用法
----
    python tools/m407_deprecate_prompt_pollution.py                # 只报告（dry-run，默认）
    python tools/m407_deprecate_prompt_pollution.py --apply        # 落盘（先自动备份）
    python tools/m407_deprecate_prompt_pollution.py --restore      # 按批次 407 回滚
    python tools/m407_deprecate_prompt_pollution.py --root <dir>   # 隔离根（测试用）

退出码：0 正常；1 环境错误（文件读不到 / 写盘失败）；2 参数错误。
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402
from nucleus.mnemosyne.ReasoningExperience import is_code_learning_prompt  # noqa: E402

DEFAULT_REL = os.path.join("data", "context", "reasoning_experience.json")
BATCH = 407
F_IS_CLEANED = "is_cleaned"


def _pool_path(root: str | None = None) -> str:
    _root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(_root, DEFAULT_REL)


def _rows_of(doc) -> list:
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for _k in ("experiences", "items", "rows"):
            if isinstance(doc.get(_k), list):
                return doc[_k]
    return []


def scan(records) -> list:
    """返回**命中的索引列表**（纯函数，不写盘）。"""
    out = []
    for i, e in enumerate(records or []):
        if not isinstance(e, dict):
            continue
        if is_code_learning_prompt(e.get("example_question")):
            out.append(i)
    return out


def mark(records, batch: int = BATCH, now: float | None = None) -> dict:
    """给命中条打标（**原地**，不删除）。返回统计。

    ★只动 `example_question` 命中模板指纹的记录；其余记录一个字段都不碰
      （零回归的结构性保证）。
    ★已打过同批次标记的跳过（幂等）。
    """
    _ts = time.time() if now is None else now
    _at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(_ts))
    _hit = _already = 0
    for i in scan(records):
        e = records[i]
        if e.get(F_IS_CLEANED) is False and e.get("cleanup_batch") == batch:
            _already += 1
            continue
        e["polluted"] = True
        e["quality_flag"] = "polluted"
        e["quality_weight"] = 0.1
        e["pollution_reason"] = str(e.get("pollution_reason") or "code_learning_prompt")
        e[F_IS_CLEANED] = False
        e["pollution_risk"] = "high"
        e["cleanup_reason"] = "code_learning_prompt"
        e["cleanup_batch"] = batch
        e["cleanup_at"] = _at
        _hit += 1
    return {"batch": batch, "marked": _hit, "already": _already,
            "at": _at, "total": len(records or [])}


def restore(records, batch: int = BATCH) -> dict:
    """按批次号回滚标记（只清本批写过的字段，不动别人的 `polluted`）。"""
    _n = 0
    for e in records or []:
        if not isinstance(e, dict):
            continue
        if e.get("cleanup_batch") != batch:
            continue
        for _k in ("pollution_risk", "cleanup_reason", "cleanup_batch", "cleanup_at"):
            e.pop(_k, None)
        # ★只回滚**本批**打的那个 polluted：本批新增的标记一律撤掉；
        #   若 `pollution_reason` 不是本批写的（历史批次打的），保留原状。
        if e.get("cleanup_reason") is None:
            e.pop(F_IS_CLEANED, None)
            if e.get("pollution_reason") == "code_learning_prompt":
                e.pop("polluted", None)
                e.pop("quality_flag", None)
                e.pop("quality_weight", None)
                e.pop("pollution_reason", None)
        _n += 1
    return {"batch": batch, "restored": _n}


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path: str, doc) -> bool:
    _tmp = path + ".tmp"
    try:
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, path)
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"[407刀1] 写盘失败: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except OSError as _e:
            # 清理临时文件失败不该掩盖原始写盘错误 —— 只留痕，仍返回 False
            silent_exc(_e, where="tools/m407_deprecate_prompt_pollution.py::_atomic_write")
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="407刀1：标记剔除代码学习 prompt 型经验")
    ap.add_argument("--root", default="", help="项目根（隔离测试用）")
    ap.add_argument("--apply", action="store_true", help="落盘（默认只报告）")
    ap.add_argument("--restore", action="store_true", help="按批次 407 回滚")
    args = ap.parse_args(argv)

    path = _pool_path(args.root or None)
    if not os.path.isfile(path):
        print(f"[407刀1] 经验库不存在: {path}", file=sys.stderr)
        return 1
    doc = _load(path)
    rows = _rows_of(doc)
    if not rows:
        print(f"[407刀1] 经验库结构不可判定（无 experiences 列表）: {path}", file=sys.stderr)
        return 1

    if args.restore:
        r = restore(rows)
        if not args.apply:
            print(f"[dry-run] 将回滚 {r['restored']} 条（加 --apply 落盘）")
            return 0
        _bk = f"{path}.bak_m407_restore_{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(path, _bk)
        if not _atomic_write(path, doc):
            return 1
        print(f"[407刀1] 已回滚 {r['restored']} 条；备份={_bk}")
        return 0

    _idx = scan(rows)
    print(f"[407刀1] 经验库: {path}")
    print(f"[407刀1] 总条数={len(rows)}  命中代码学习 prompt={len(_idx)}")
    for i in _idx[:5]:
        print(f"    #{i} {str(rows[i].get('example_question'))[:56]!r}")
    if len(_idx) > 5:
        print(f"    ... 其余 {len(_idx) - 5} 条同类")

    if not args.apply:
        print("[dry-run] 加 --apply 落盘（会先自动备份）")
        return 0
    if not _idx:
        print("[407刀1] 无需处理（可能已标记过）")
        return 0

    _bk = f"{path}.bak_m407_{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, _bk)
    r = mark(rows)
    if not _atomic_write(path, doc):
        print(f"[407刀1] 写盘失败，原文件未改；备份={_bk}", file=sys.stderr)
        return 1
    print(f"[407刀1] 已标记 {r['marked']} 条（已标记过 {r['already']} 条）；备份={_bk}")
    _back = _load(path)
    _back_n = sum(1 for e in _rows_of(_back)
                  if isinstance(e, dict) and e.get(F_IS_CLEANED) is False
                  and e.get("cleanup_batch") == BATCH)
    print(f"[407刀1] 读回校验: 本批标记数={_back_n}（应 = {r['marked'] + r['already']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
