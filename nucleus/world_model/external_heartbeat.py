# -*- coding: utf-8 -*-
"""W1 · 外部心跳真值哨（world model 最小件 · 328批刀3 落地）。

## 它要解决的问题

A1—A6 六条断言全部建立在**调用图自身的内部一致性**上：图说方法被调了就算"活着"。
但"图说活着"和"活体真的在发信号"是两件事 —— 图可以因为解析规则变宽松而变得更好看，
而进程可能已经僵死。周期性的**外部可观测产物**是唯一能反驳"内部自证"的东西。

W1 就用现成的两件外部产物做真值判定：
  * `data/monitor/health_snapshot.json` —— 由 `PulseMetricsCollector` 在**每次心跳**
    落盘（`_snapshot_interval = 1`，心跳约 10—13s 一次），带 `timestamp`（快照生成时刻）
    与 `beat_count`（累计心跳数）。**文件字节在变 + beat_count 在涨** = 活体在发信号。
  * `data/reports/alerts.jsonl` —— 事件流，带 `ts`。**只作辅助**：一片安静是健康的表现，
    不能拿"没有新告警"判死（这是最容易写错的一条，故写进判据注释里）。

## 判据（三态，绝不返回 0 冒充读数）

  * `alive`       —— 快照年龄 ≤ `max_age_sec`，且（若给了上一次采样）`beat_count` 严格增长；
  * `stale`       —— 快照存在但年龄超线（进程可能卡住/落盘路径被占）；
  * `unavailable` —— 文件不存在或不可解析。**没有读数 ≠ 读数为 0**：
                     返回 `unavailable` 并给出所需数据源，绝不填 0 让人误以为"活着但没动静"。

## 范围守界（小林/任务书）

不新建器官、不扩世界模型大工程、不排 W2/W3；本模块**纯读**，不写任何文件、
不发起脉冲、不注册钩子 —— 调用方拿结论自己用。
"""
from __future__ import annotations

import json
import os
import time
from collections import deque

from nucleus._silent_except import silent_exc

#: 口径版本（换判据必须换版本号，避免历史读数被静默重解释）
W1_CRITERIA_VERSION = "328.w1.v1"

_SNAPSHOT_REL = os.path.join("data", "monitor", "health_snapshot.json")
_ALERTS_REL = os.path.join("data", "reports", "alerts.jsonl")

#: 快照最大可接受年龄（秒）。心跳 ~10—13s 一次、`snapshot_interval=1`（每拍都落），
#: 取 90s ≈ 连丢 7 拍才判 stale —— 宁可漏报也不误报（误报会让 W1 变成噪声源）。
DEFAULT_MAX_AGE_SEC = 90.0
#: 辅助告警流的扫描深度（只看尾部，避免把 5MB 历史全读进内存）
_ALERT_TAIL_LINES = 4000


def _root(root: str | None = None) -> str:
    if root:
        return os.path.abspath(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8", errors="replace") as _f:
            return json.load(_f)
    except (OSError, ValueError) as e:
        silent_exc(e, where="nucleus.world_model.external_heartbeat::_read_json")
        return None


def read_health_snapshot(root: str | None = None) -> dict:
    """读健康快照并算年龄。**只读**；失败返回 `available=False` + 原因，不抛。"""
    _p = os.path.join(_root(root), _SNAPSHOT_REL)
    _d = _read_json(_p)
    if not isinstance(_d, dict):
        return {"available": False, "path": _p,
                "reason": "文件不存在或不可解析（不是'没有心跳'，是'读不到'）"}
    try:
        _mt = os.path.getmtime(_p)
    except OSError as e:
        silent_exc(e, where="nucleus.world_model.external_heartbeat::mtime")
        _mt = 0.0
    _ts = _d.get("timestamp")
    try:
        _ts = float(_ts) if _ts is not None else 0.0
    except (TypeError, ValueError) as e:
        silent_exc(e, where="nucleus.world_model.external_heartbeat::timestamp")
        _ts = 0.0
    _now = time.time()
    # 年龄取「文件 mtime」与「快照自报 timestamp」中**更保守**的那个：
    # 前者防"写盘被卡住但内容旧"，后者防"文件被 touch 过"。
    _age_file = (_now - _mt) if _mt else None
    _age_ts = (_now - _ts) if _ts else None
    _age = max([_a for _a in (_age_file, _age_ts) if _a is not None], default=None)
    return {
        "available": True,
        "path": _p,
        "mtime": _mt,
        "snapshot_ts": _ts,
        "age_sec": round(float(_age), 1) if _age is not None else None,
        "age_from_file_sec": round(_age_file, 1) if _age_file is not None else None,
        "age_from_ts_sec": round(_age_ts, 1) if _age_ts is not None else None,
        "beat_count": _d.get("beat_count"),
        "heartbeat": _d.get("heartbeat"),
        "pulse": _d.get("pulse"),
        "organs": _d.get("organs"),
        "memory_pressure": _d.get("memory_pressure"),
    }


def read_alert_tail(root: str | None = None, max_lines: int = _ALERT_TAIL_LINES) -> dict:
    """读告警流尾部（辅助证据）。

    ★口径警告：**安静 ≠ 死了**。告警少只说明"没出事"，不能反过来证明"活着"。
    故本函数结果只进 `evidence`，**不参与 verdict 判定**。
    """
    _p = os.path.join(_root(root), _ALERTS_REL)
    if not os.path.isfile(_p):
        return {"available": False, "path": _p, "reason": "告警流不存在"}
    # ★328批刀3 首版踩坑：写成"读满 max_lines 就 break" ⇒ 读的是**文件开头**
    #   （最老的 4000 行），算出的"末条年龄"是 9.7 天 —— 比真实值差三个数量级，
    #   而且看上去完全正常。改用 `deque(maxlen=...)` 滚一遍，只留**最后** N 行。
    _tail: deque[str] = deque(maxlen=int(max_lines))
    _n = 0
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            for _line in _f:
                _s = _line.strip()
                if not _s:
                    continue
                _n += 1
                _tail.append(_s)
    except OSError as e:
        silent_exc(e, where="nucleus.world_model.external_heartbeat::alerts")
        return {"available": False, "path": _p, "reason": "读失败: %s" % type(e).__name__}
    _last = _tail[-1] if _tail else None
    _ts = None
    if _last:
        try:
            _ts = float(json.loads(_last).get("ts") or 0) or None
        except (TypeError, ValueError) as e:
            silent_exc(e, where="nucleus.world_model.external_heartbeat::alerts ts")
    return {
        "available": True,
        "path": _p,
        "scanned_lines": _n,
        "tail_lines_kept": len(_tail),
        "last_ts": _ts,
        "last_age_sec": round(time.time() - _ts, 1) if _ts else None,
    }


def external_heartbeat_verdict(root: str | None = None,
                               max_age_sec: float | None = None,
                               prev_sample: dict | None = None,
                               now: float | None = None) -> dict:
    """★W1 真值判定：活体是否**真的在发信号**（外部可观测，不靠内部自证）。

    参数：
      prev_sample —— 上一次调用的 `sample` 返回值。给了就额外判 `beat_count` 是否严格增长；
                     不给（首次调用）只按年龄判，并在 `basis` 里写明"未做增量判定"。
      max_age_sec —— 覆盖默认 90s（测试/压测可调）。

    返回：`verdict ∈ {alive, stale, unavailable}` + `evidence` + `basis`（判定依据逐条列出）
          + `sample`（下次调用回传即可做增量判定）+ `criteria_version`。
    """
    _now = float(now if now is not None else time.time())
    _max = float(max_age_sec if max_age_sec is not None else DEFAULT_MAX_AGE_SEC)
    _snap = read_health_snapshot(root)
    _alerts = read_alert_tail(root)
    _basis: list[str] = []

    if not _snap.get("available"):
        return {
            "verdict": "unavailable",
            "criteria_version": W1_CRITERIA_VERSION,
            "reason": _snap.get("reason") or "快照不可读",
            "required_sources": ["data/monitor/health_snapshot.json（每次心跳落盘）"],
            "evidence": {"snapshot": _snap, "alerts": _alerts},
            "basis": ["快照不可读 ⇒ 没有外部证据，判 unavailable（**不填 0**）"],
            "sample": None,
        }

    _age = _snap.get("age_sec")
    if _age is None:
        return {
            "verdict": "unavailable",
            "criteria_version": W1_CRITERIA_VERSION,
            "reason": "快照无可解析时间戳与 mtime",
            "evidence": {"snapshot": _snap, "alerts": _alerts},
            "basis": ["拿不到年龄 ⇒ 无法判活，判 unavailable"],
            "sample": None,
        }

    _beat = _snap.get("beat_count")
    _sample = {"snapshot_mtime": _snap.get("mtime"), "beat_count": _beat,
               "snapshot_ts": _snap.get("snapshot_ts"), "taken_at": _now}

    _verdict = "alive"
    if _age > _max:
        _verdict = "stale"
        _basis.append("快照年龄 %.1fs > 阈值 %.0fs ⇒ stale（进程可能卡住或落盘被占）"
                      % (_age, _max))
    else:
        _basis.append("快照年龄 %.1fs ≤ 阈值 %.0fs ⇒ 外部信号新鲜" % (_age, _max))

    _delta = None
    if isinstance(prev_sample, dict) and prev_sample.get("beat_count") is not None \
            and _beat is not None:
        try:
            _delta = int(_beat) - int(prev_sample.get("beat_count"))
        except (TypeError, ValueError) as e:
            silent_exc(e, where="nucleus.world_model.external_heartbeat::delta")
            _delta = None
        if _delta is not None:
            if _delta > 0:
                _basis.append("beat_count %s→%s（+%d）⇒ 活体在推进"
                              % (prev_sample.get("beat_count"), _beat, _delta))
            elif _verdict == "alive":
                # 年龄新鲜但心跳不动：落盘还在写、心脏没跳 —— 这是"半死"，必须报 stale
                _verdict = "stale"
                _basis.append("快照新鲜但 beat_count 未增长（%s→%s）⇒ 心脏未推进，判 stale"
                              % (prev_sample.get("beat_count"), _beat))
    else:
        _basis.append("未提供上一次采样 ⇒ 只按年龄判，未做增量判定")

    # 告警流只写进证据，不参与判定（安静 ≠ 死了）
    if _alerts.get("available"):
        _basis.append("告警流仅作辅助：末条 %.1fs 前（**不参与判定**：没告警不等于没活着）"
                      % (_alerts.get("last_age_sec") or 0))

    return {
        "verdict": _verdict,
        "criteria_version": W1_CRITERIA_VERSION,
        "max_age_sec": _max,
        "age_sec": _age,
        "beat_delta": _delta,
        "evidence": {"snapshot": _snap, "alerts": _alerts},
        "basis": _basis,
        "sample": _sample,
    }


def w1_consumers() -> dict:
    """W1 的消费方接入点（结构化，供报告/日报直接引用，不靠文字描述）。"""
    return {
        "criteria_version": W1_CRITERIA_VERSION,
        "consumers": [
            {"name": "A6 外部证据面",
             "how": "A6 判聚合与逐边一致性；W1 提供的 alive/stale 作为**图外**证据，"
                    "当 A6 与 W1 冲突（图说好、外部说卡）时以 W1 为准先查图新鲜度",
             "landed": False,
             "reason": "需 A6 消费方接入，属 330 批（开闸批）范围"},
            {"name": "O3 抽验 A",
             "how": "抽验前先过 W1：`stale/unavailable` ⇒ 抽样结论记 `provisional`，"
                    "不给开闸结论（避免在旧信号上做判据手术）",
             "landed": False,
             "reason": "O3 开闸批（330）接入"},
            {"name": "开闸三证第四证",
             "how": "开闸需 ①图 ②判据 ③A 抽验 ④**外部心跳**；第四证 = W1 verdict == alive "
                    "且（重启后）beat_count 已推进",
             "landed": False,
             "reason": "随开闸批编排；本批只提供读口与判定"},
        ],
    }
