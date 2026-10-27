# -*- coding: utf-8 -*-
"""K1 产出卸载器 · ★374 刀3（票 `T-K1主闭环卸载-1`）

## 问题（口径来自实测，不是推测）

并行柱 K1（`SafeEvolutionExecutor` 的子进程 review 拍）一直有产出，但产出**没有去处**：

* 子进程回来只做两件事——计数（`_OUTPUT_STATS`）与打日志（`[317刀3][产出率]`）；
* `stats["report"]` 里那条条 `{pulse_type, file, method, severity/analysis}` 读完就扔；
* 审核池（`audit_problem_pool`）与它的消费方（`audit_problem_pool_consumer`）从未见过它们。

⇒ 「K1 达成」只说明子代理跑得不错，曈曈本身一点没受益（仍是隔离验证态）。

## 择路（任务书要求「写盘/信号/消费口择路」，这里选**写盘**）

理由逐条：

1. **消费面是现成的**：`audit_problem_pool.persist_pool()` 已经是审查报告的唯一落盘口
   （合并 + 归因兜底 + prune + 限额 + 原子写），下游 `load_pool()` / consumer /
   看板三处都在读它。新开一条"信号"或"新消费口"等于再造一套入口。
2. **幂等**：池条目的身份是 `sha1(file|method|issue_type)[:16]`，同一条 anomaly 重复
   卸载只会更新 `last_seen_ts`，不会膨胀。
3. **不新开写口**：仍然走池的唯一落盘函数（守 356 单写者语义）。
4. **不改判据**：不碰 K1 三态（`_K1_MIN_RUNS` / `_K1_MIN_PRODUCTIVE`）与
   `_OUTPUT_STATS` 口径，卸载是**产出侧的第二次使用**，不是判定。

## 语义边界（★最容易搞错的一条）

K1 的产出**不是缺陷判定**，是「这些异常我评审过了」。所以：

* issue_type 固定 `k1_task_face_anomaly` ⇒ `classify_klass` 判 `unknown`
  ⇒ `classify_route` 兜底 `optimize_list`（看板/待优化清单）；
* **绝不进 `patch_pool`**：那里是"可修的缺陷"，塞评审记录会挤掉真可修项的容量与排序位
  （367 低危限额那条教训的同型）。

要让它进补丁链，得先有真正的缺陷判据（K2 的活），不是本刀。
"""
from __future__ import annotations

import time
from typing import Any

import config
from nucleus import audit_problem_pool as _pool
from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("K1Offload")

#: 卸载进池时使用的 issue_type（固定值，见模块头"语义边界"）
K1_ISSUE_TYPE = "k1_task_face_anomaly"

#: K1 产出的 `severity`/`level` 是日志级（ERROR/CRITICAL/P0…），池只认 low/medium/high。
#: 这里做**显式**映射而不是改 `normalize_severity` —— 池那边"ERROR 不归一"是刻意的
#: （非法值要按非法值处置并留痕），不能为了本刀把那条口径撬松。
_SEV_MAP = {
    "FATAL": "high", "CRITICAL": "high", "P0": "high", "ERROR": "high",
    "WARN": "medium", "WARNING": "medium",
    "INFO": "low", "DEBUG": "low",
}

#: 两次卸载之间的最小间隔（秒）。K1 每拍都会回一份 report，但池是**日快照**且要
#: 合并+prune+原子写；不设闸的话同一条 anomaly 会以分钟级频率反复触发落盘。
#: 这是**节流常量**，不是 config 开关（任务书：本批不新增开关）。
MIN_OFFLOAD_INTERVAL_SEC = 60.0

_LAST_OFFLOAD_TS = 0.0


def _offload_enabled() -> bool:
    """卸载总开关（复用既有 `ENABLE_AUDIT_POOL_CONSUME`，**不新增键**）。

    为什么复用消费侧开关：卸载的意义就是"给消费方喂料"，消费都关了还往里写
    只会让池变成只写不读的垃圾场。
    """
    try:
        return bool(getattr(config, "ENABLE_AUDIT_POOL_CONSUME", True))
    except Exception as _e:
        silent_exc(_e, where="k1_offload::_offload_enabled", level="debug")
        return True


def _sev_of(item: dict[str, Any]) -> str:
    _raw = str(item.get("severity") or item.get("level") or "").strip().upper()
    if not _raw:
        return "low"
    return _SEV_MAP.get(_raw, "low")


def report_to_issues(report: list | None) -> list[dict]:
    """把 K1 的 `report` 列表转成池能吃的 issue 列表（**纯函数**）。

    不可定位条目（既无 file 也无 method）直接丢弃并计数：池条目的 dedup_key 靠
    `file|method|issue_type`，三者都缺就只剩类型 ⇒ 会把所有不可定位项合并成 1 条，
    那不是"收敛"，是把 N 个问题糊成 1 个假条目。
    """
    _out: list[dict] = []
    for _it in (report or []):
        if not isinstance(_it, dict):
            continue
        _file = str(_it.get("file") or "").strip()
        _method = str(_it.get("method") or "").strip()
        if not (_file or _method):
            continue
        _ptype = str(_it.get("pulse_type") or "?")
        _detail = str(_it.get("analysis") or "").strip()
        if not _detail:
            _snip = _it.get("snippet_len")
            _detail = (f"K1 评审：pulse_type={_ptype}"
                       + (f"，代码片段 {_snip} 字符" if _snip is not None else ""))
        _out.append({
            "file": _file,
            "method": _method,
            "type": K1_ISSUE_TYPE,
            "severity": _sev_of(_it),
            "line": 0,
            "desc": _detail[:300],
            # 归因机器字段（`_m350_attribution` 认这里）
            "attribution": {
                "source": "k1_task_face_review",
                "pulse_type": _ptype,
                "reviewed_at": _it.get("ts") or "",
            },
        })
    return _out


def offload_report(report: list | None, *, root: str | None = None,
                   audit_dir_path: str | None = None, now: float | None = None,
                   dry_run: bool = False, force: bool = False) -> dict:
    """把一份 K1 `report` 卸载进审计池（写盘路径；失败不抛，只留痕）。

    Returns: `{state, fed, issues, offloaded, added, updated, reason, ...}`
      `state="skipped"` 表示没写（`disabled` / `empty` / `throttled` / `dry_run`），
      **不写空快照**（与 `persist_pool` 的 `state="skipped"` 同口径）。

    为什么节流放在这里而不是调用方：调用点在生产热路径上，节流是**卸载自身的语义**
    （同一份快照一天写 N 次没有意义），写在调用方就会有两个调用方各写一套。
    """
    global _LAST_OFFLOAD_TS
    _now = float(now if now is not None else time.time())
    _rep: dict[str, Any] = {"state": "skipped", "fed": 0, "issues": 0,
                            "offloaded": 0, "added": 0, "updated": 0,
                            "reason": "", "proc_gen": ""}
    _issues = report_to_issues(report)
    _rep["fed"] = len(report or [])
    _rep["issues"] = len(_issues)
    if not _issues:
        _rep["reason"] = "empty"
        return _rep
    if not _offload_enabled():
        _rep["reason"] = "disabled"
        return _rep
    if dry_run:
        _rep["reason"] = "dry_run"
        return _rep
    if not force and _LAST_OFFLOAD_TS > 0 and (_now - _LAST_OFFLOAD_TS) < MIN_OFFLOAD_INTERVAL_SEC:
        _rep["reason"] = "throttled"
        return _rep
    try:
        _r = _pool.persist_pool({"issues": _issues}, root=root,
                                audit_dir_path=audit_dir_path, now=_now)
        _rep["state"] = _r.get("state")
        _rep["added"] = int(_r.get("added", 0) or 0)
        _rep["updated"] = int(_r.get("updated", 0) or 0)
        _rep["offloaded"] = len(_issues)
        _rep["proc_gen"] = str(_r.get("proc_gen", "") or "")
        _LAST_OFFLOAD_TS = _now
        _module_logger.info(
            "[374刀3][K1卸载] fed=%d issues=%d added=%d updated=%d path=%s proc_gen=%s",
            _rep["fed"], _rep["issues"], _rep["added"], _rep["updated"],
            _r.get("path", ""), _rep["proc_gen"])
    except Exception as _e:
        _rep["state"] = "error"
        _rep["reason"] = f"{type(_e).__name__}: {_e}"
        silent_exc(_e, where="k1_offload::offload_report", level="warning")
    return _rep


def reset_offload_throttle() -> None:
    """★测试专用：清节流时间戳（不改任何业务状态）。"""
    global _LAST_OFFLOAD_TS
    _LAST_OFFLOAD_TS = 0.0
