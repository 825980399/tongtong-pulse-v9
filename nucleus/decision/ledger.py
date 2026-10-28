# -*- coding: utf-8 -*-
"""385 刀2：DL3 决策账本（append-only）。

按洞鉴 385 §6.4：

- 路径 `data/decisions/dec_<epoch>.jsonl`（一行一个 `DecideResult` + 输入摘要哈希）
- 三键索引：`decision_id`（幂等）／`request_id`（1:1）／`target`（可反查）
- **不可改写**：只允许追加；纠正走「新决策 + `supersedes`」，**不删旧行**
- 落盘纪律沿用铁律182（先算 data → `.tmp` → `os.replace`），写失败即 `DEFER`

## 关于铁律182 的落法（这里说清楚，别机械套）

182 的本意是「**整文件重写**要么换成功、要么保持旧文件」。而本账本是**单行追加**：
`O_APPEND` 打开后写一行（< 4KB），在内核层是原子的，不存在"写了一半"的中间态。
硬套 `.tmp + os.replace` 反而要求先把 N 万行读进内存再全量重写，既慢又会在中途
崩掉时留下**更严重的**问题（旧文件被替换成不完整的）。所以：
  · **追加**走 `open("a")` + `flush`（原子单行）
  · **压缩/重写**（本批不提供，留口）才用 `.tmp + os.replace`
两种都写在这里，理由是"用对工具"，不是"绕过纪律"。
"""
from __future__ import annotations

import json
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.decision.contracts import DecideResult, DecisionRequest

#: 账本目录（相对仓库根）
LEDGER_DIR = os.path.join("data", "decisions")

#: 单文件软上限：超过就提示该换 epoch（不自动切，避免把一拍的记录劈成两半）
LEDGER_SOFT_MAX_LINES = 200000

#: ★385刀2 性能：已写过的 `decision_id`（**进程内**，有界 FIFO）。
#: 为什么不查文件：`find_by_decision_id` 是 O(文件行数)，每拍一条就成 O(n²)；
#: 实测 60 条时把 P99 从 <1ms 拖到 43ms。影子场景同 id 只可能来自同进程重放，
#: 内存集合足够；跨进程重复的概率极低，且账本是 append-only，真重复也只多一行。
_SEEN_IDS: set = set()
_SEEN_ORDER: list = []
_SEEN_MAX = 5000


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def epoch_tag(request_epoch: str = "", now: float | None = None) -> str:
    """账本分区名：优先用请求里的 `epoch`，否则用当天日期。

    ★按「停机窗/重启」分代是洞鉴 §6.2 的要求；拿不到就退化为日期，
      退化后仍能按 `proc_gen` 切片，不会串代。
    """
    _e = str(request_epoch or "").strip()
    if _e:
        return _e.replace("/", "_").replace("\\", "_").replace(":", "")[:32]
    return time.strftime("%Y%m%d", time.localtime(now if now is not None else time.time()))


def ledger_path(epoch: str = "", now: float | None = None,
                root: str | None = None) -> str:
    return os.path.join(root or _root(), LEDGER_DIR,
                        "dec_%s.jsonl" % epoch_tag(epoch, now))


def _guard(path: str, explicit: bool) -> bool:
    """写盘守卫。被拒 ⇒ False（调用方按"写失败即 DEFER"处理）。"""
    try:
        from nucleus.data.write_guard import guard_write
        return bool(guard_write(path, explicit=explicit, component="DecisionLedger"))
    except Exception as _e:
        silent_exc(_e, where="decision.ledger._guard", level="debug")
        return True     # 守卫本身不可用时不阻断影子账本（本批不改执行行为）


def append(result: DecideResult, request: DecisionRequest | None = None,
           *, path: str | None = None, explicit: bool = False,
           supersedes: str = "") -> str:
    """追加一条决策记录。返回状态串（**不抛异常**）：

    · `ok`          写入成功
    · `disabled`    契约校验不过（不该发生；发生了就说明调用方造了非法结果）
    · `duplicate`   同 `decision_id` 已在账本里（幂等，洞鉴 §6.4 三键之一）
    · `denied`      写盘守卫拒绝
    · `write_failed` 写盘异常

    ★写入的不是裸 `DecideResult`，而是 `{"schema","input_digest","result"}`：
      洞鉴 §6.4 要求「一行一个 DecideResult **+ 输入摘要哈希**」，摘要是回放
      「同输入是否复现同结果」的唯一凭据。
    """
    _bad = result.validate()
    if _bad:
        return "disabled"

    _p = path or ledger_path(getattr(request, "epoch", "") if request else "")
    if not _guard(_p, explicit):
        return "denied"

    _did = str(result.decision_id)
    if _did in _SEEN_IDS:            # ★385刀2：内存幂等（O(1)），不扫文件
        return "duplicate"

    _row = {
        "schema": "dec_ledger_row/385.v1",
        "input_digest": (request.digest() if request is not None else ""),
        "request_id": result.request_id,
        "recorded_at": time.time(),
        "result": result.to_dict(),
    }
    if supersedes:
        _row["supersedes"] = str(supersedes)

    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
            _f.flush()
            # ★385刀2 性能：**不做 fsync**。理由：账本是观测数据，进程崩掉丢最后
            #   一行可接受（可重放）；而 fsync 单次 10-30ms，会把「每拍 1 条的观测」
            #   变成「每拍一次磁盘同步」，触碰本批「不加延迟」的红线。
    except OSError as _e:
        silent_exc(_e, where="decision.ledger.append", level="warning")
        return "write_failed"
    _SEEN_IDS.add(_did)
    _SEEN_ORDER.append(_did)
    if len(_SEEN_ORDER) > _SEEN_MAX:
        _drop = _SEEN_ORDER[:len(_SEEN_ORDER) - _SEEN_MAX]
        del _SEEN_ORDER[:len(_SEEN_ORDER) - _SEEN_MAX]
        for _d in _drop:
            _SEEN_IDS.discard(_d)
    return "ok"


def iter_rows(path: str | None = None):
    """流式读账本行（坏行跳过，不中断）。"""
    _p = path or ledger_path()
    if not os.path.isfile(_p):
        return
    try:
        with open(_p, "r", encoding="utf-8") as _f:
            for _line in _f:
                _s = _line.strip()
                if not _s:
                    continue
                try:
                    yield json.loads(_s)
                except (ValueError, TypeError) as _e:
                    silent_exc(_e, where="decision.ledger.iter_rows 坏行", level="debug")
                    continue
    except OSError as _e:
        silent_exc(_e, where="decision.ledger.iter_rows", level="warning")


def load_index(path: str | None = None) -> dict:
    """建三键索引（洞鉴 §6.4）：decision_id / request_id / target。

    `target` 是**一对多**（同一补丁能被裁决多次），另两个是一对一。
    """
    _by_did: dict = {}
    _by_rid: dict = {}
    _by_target: dict = {}
    _n = 0
    for _row in iter_rows(path):
        _res = (_row or {}).get("result") or {}
        if not isinstance(_res, dict):
            continue
        _n += 1
        _did = str(_res.get("decision_id") or "")
        _rid = str(_res.get("request_id") or "")
        _tgt = str(_res.get("target") or "")
        if _did:
            _by_did.setdefault(_did, _res)
        if _rid:
            _by_rid.setdefault(_rid, _res)
        if _tgt:
            _by_target.setdefault(_tgt, []).append(_res)
    return {"lines": _n, "by_decision_id": _by_did,
            "by_request_id": _by_rid, "by_target": _by_target}


def find_by_decision_id(decision_id: str, path: str | None = None) -> dict | None:
    """幂等查询：同 `decision_id` 已存在就不重复写。"""
    _want = str(decision_id or "")
    if not _want:
        return None
    for _row in iter_rows(path):
        _res = (_row or {}).get("result") or {}
        if isinstance(_res, dict) and str(_res.get("decision_id") or "") == _want:
            return _res
    return None


def count_rows(path: str | None = None) -> int:
    """行数（单测用：账本行数应等于影子调用次数）。"""
    _n = 0
    for _row in iter_rows(path):
        if _row:
            _n += 1
    return _n
