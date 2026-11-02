# -*- coding: utf-8 -*-
"""audit_problem_pool.py —— 自发现问题池落盘（第345批 刀1/刀2）。

## 为什么需要本模块

洞鉴第三季的原始判定是「662 问题池零落盘＝纯内存、12min 重算、重启清零」。
**实测复核后修正**：`data/reports/code_review/run_<ts>.json` 从 303 批起每拍落盘
（per-item 明细，含 type/severity/file/line/method/dedup_key），滚动保留 7 份。
洞鉴的搜索关键词是 audit/problem/issue，而目录名是 `code_review`、文件名是 `run_*`
⇒ **关键词零命中被读成了「零落盘」**（铁律168：零命中不等于没有）。

真实缺口是四条，本模块只补这四条：
1. **无读回路径**：`run_*.json` 只写不读，`grep code_review` 无任何消费者 ⇒ 重启确实清零。
2. **无状态机**：每条 item 只有 `verdict=None` / `verdict_status="UNSET"`，
   没有 open/claimed/fixed/verified/wontfix，问题"发现之后"没有任何推进语义。
3. **无 first_seen**：每份是当拍快照，看不出某条问题已经存在了多久。
4. **滚动 7 份＝84 分钟**（12min/拍），而裁决①要求「7 天 + 未结案」。

## 与 `code_review/run_*.json` 的分工（不是两套真值）

- `code_review/run_<ts>.json`：**每拍明细**（既有，不动），回答"这一拍看到了什么"。
- `audit/audit_<YYYYMMDD>.json`：**池累积态**（本模块），回答"现在池里有哪些问题、
  各处在什么状态、首见是什么时候"。日内滚动更新同一文件，跨天新建 ⇒ 7 天＝7 个文件。

## 字段口径（烛微345落盘预演为最终口径）

`id / issue_type / severity / file / line / method / desc / dedup_key / klass /
 criteria_version / code_version / first_seen_ts / last_seen_ts / proc_gen_first /
 status / closed_reason`

- `dedup_key = sha1(file|method|issue_type)[:16]` —— **弃行号**（烛微345实测：
  现 `file:line:type` 拼接，行号一漂移就变成新问题；335/336 已踩，344 批又踩一次
  O3 条目入池记 122、实际已在 123）。行号只作为位置信息留在 `line` 字段。
- `first_seen_ts / last_seen_ts`：烛微用 `_ts` 后缀（本批按新口径，不用 `_at`）。
- `proc_gen_first`：首见时的进程代次（`proc_gen_str()`，取不到记 `unknown`，**不填 0**）。
- `klass`：问题大类（见 `classify_klass`）。★这一项烛微预演只给了字段名，
  语义按「刀2 三类出口的族」落地，**与烛微待核2 的最终裁定可能有差异，见报告移交项**。
- `closed_reason`：进入终态（verified/wontfix）时的原因文本。

## severity 值域（烛微345挂账59）

只认 `{low, medium, high}`。实测生产里出现过 `ERROR`（1 条）这类非枚举值。
处置：**不吞不弃**——原值留在 `severity_raw`、`severity` 归 `low`、
打 `severity_invalid=True`、留痕 `[345池] severity非法值: <id>=<值>`，
并**强制走清洗队列**（刀2 route），由人工看，不允许它混在主池里影响统计。

## 隔离纪律（铁律158/172）

所有函数都接受 `root` / `audit_dir_path` 注入，另支持环境变量
`PULSE_AUDIT_POOL_DIR`（给隔离脚本用）。生产不设环境变量 ⇒ 落 `data/reports/audit/`。
"""

import functools
import hashlib
import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.drop_tracker import note_drop

from nucleus.logger import get_module_logger

_module_logger = get_module_logger(__name__)

#: 池积存目录（相对项目根）。
#:
#: ★346刀6：先给一个**正斜杠字面量**常量，再由它派生 OS 路径。
#: 原因：`os.path.join("data","reports","audit")` 在源码里被拆成三段，
#: `grep -rn '"data/reports'` 命中 0 处 ⇒ 烛微346 判"落盘路径未见源码字符串常量"。
#: 结论不是"没有常量"（`AUDIT_DIR_REL` 一直都在），而是**常量不可 grep**——
#: 对审计者来说不可 grep 就等于不存在（铁律168：零命中不等于没有，但会误导）。
#: 现在两种写法都能命中：字面量在这行，派生值仍是同一个路径（下方有断言自测）。
AUDIT_DIR_LITERAL = "data/reports/audit"

#: 池积存目录（相对项目根，OS 分隔符形态；由上面字面量派生，不另写一份）
AUDIT_DIR_REL = os.path.join(*AUDIT_DIR_LITERAL.split("/"))

#: 滚动保留天数（裁决①：最近 7 天 + 未结案全留）
POOL_RETAIN_DAYS = 7

#: 快照文件名前缀/后缀（文件名含日期 ⇒ 字典序＝时间序）
SNAPSHOT_PREFIX = "audit_"
SNAPSHOT_SUFFIX = ".json"

#: 状态机（刀2）：open → claimed → fixed → verified；wontfix 为"不再修"的终态
STATUS_OPEN = "open"
STATUS_CLAIMED = "claimed"
STATUS_FIXED = "fixed"
STATUS_VERIFIED = "verified"
STATUS_WONTFIX = "wontfix"
#: ★352刀1：目标漂移（file/method 在当前工作副本上已不存在/改名）。
#   与 wontfix 的区别：wontfix=“不修了”（人的决定）；target_drifted=“还要修，
#   但要先重新定位”（机器的事实）。混用会把 27 条待重定位的真问题
#   当成“已结案”丢掉。
STATUS_TARGET_DRIFTED = "target_drifted"
#: ★383刀4：O3 判死条目 OPEN 超时后转此态 —— **「不推进」不是「已结案」**。
#   刻意**不进** TERMINAL_STATUSES：否则同时踩两个坑
#     ① 结案率口径被污染（烛微口径：status≠verified/wontfix 即未结案）；
#     ② POOL_RETAIN_DAYS 同为 7 天，转 stale 当天就被 prune 当结案删掉。
STATUS_STALE = "stale"

ALL_STATUSES = (STATUS_OPEN, STATUS_CLAIMED, STATUS_FIXED, STATUS_VERIFIED,
                STATUS_WONTFIX, STATUS_TARGET_DRIFTED, STATUS_STALE)

#: 终态：进入后视为"结案"，可被超期 prune（烛微口径：status≠verified/wontfix 即未结案）
TERMINAL_STATUSES = (STATUS_VERIFIED, STATUS_WONTFIX)

#: 合法流转（非法流转只告警不写，不静默纠正）
_LEGAL_TRANSITIONS = {
    STATUS_OPEN: (STATUS_CLAIMED, STATUS_FIXED, STATUS_VERIFIED, STATUS_WONTFIX,
                  STATUS_TARGET_DRIFTED, STATUS_STALE),
    STATUS_CLAIMED: (STATUS_FIXED, STATUS_OPEN, STATUS_WONTFIX, STATUS_TARGET_DRIFTED),
    STATUS_FIXED: (STATUS_VERIFIED, STATUS_CLAIMED, STATUS_WONTFIX, STATUS_TARGET_DRIFTED),
    STATUS_VERIFIED: (STATUS_OPEN, STATUS_WONTFIX),
    STATUS_WONTFIX: (STATUS_OPEN,),
    # ★352刀1：漂移**不是结案**。必须能回 open（重新定位后继续），
    #   也允许直接判 wontfix（确认该问题已随代码消失）。
    STATUS_TARGET_DRIFTED: (STATUS_OPEN, STATUS_WONTFIX),
    # ★383刀4：stale 留两条回升通道 —— 回 open（有人要处理了）/ 直接 wontfix
    #   （确认该问题已随代码消失）。不给 fixed：它本来就没人在修。
    STATUS_STALE: (STATUS_OPEN, STATUS_WONTFIX),
}

#: severity 值域（烛微345挂账59）
SEVERITY_ALLOWED = ("low", "medium", "high")

#: severity 别名归一（只归一**语义相同**的写法；`ERROR` 这类**不归一**，
#: 它是非法值，要按非法值处置并留痕——归一掉就等于把问题抹了）
_SEVERITY_ALIASES = {"warning": "medium", "info": "low", "critical": "high"}

#: 单快照条目上限（防御：池无上限会把落盘本身变成新的内存/磁盘泄漏源）
MAX_POOL_ENTRIES = 5000

#: ★367 后续（T-死代码放大器修复-1 第三件事）：**低危条目**入池上限。
#:
#: 为什么需要单独限额（实测数据，非估算）：
#:   · `SelfInspector._detect_dead_code()` 单次产 **646 条**，severity 归一后**恒为 low**；
#:   · 占 `MAX_POOL_ENTRIES`（5000）的 **12.9%**，且每拍随 issues 重新生成、只增不减；
#:   · 它们 `review_only=True`，源码语义边界明写「待复核 ≠ 死代码，不得据此删码」
#:     ⇒ 与真正可修的 high/medium 条目抢容量与排序位，却不构成任何可执行修复任务。
#:
#: 语义边界：
#:   · 只作用于 low；high/medium **全量保留**（限额绝不挤掉可修项）；
#:   · **不是丢弃**：超限条数进 `low_severity_capped` 计数并打 warning（不吞不弃）；
#:   · 截下的只是「本拍快照视图」，下一拍 issues 仍会重新送来（生成侧幂等）。
MAX_LOW_ENTRIES = 600

#: 池 schema 版本
SCHEMA = "audit_problem_pool/345.v2"

#: ★348刀8：池指纹台账（防再丢自证链）。
#   345 的教训是"写进去的东西会悄悄不见"（滚动仅 84min；产物被复制走而池一无所知）。
#   快照**无法自证"我还在"**——能证明它的只有池外的第三方台账。故指纹不落快照内，
#   落同目录独立文件 `_pool_ledger.json`。
LEDGER_FILENAME = "_pool_ledger.json"
LEDGER_SCHEMA = "audit_pool_ledger/348.v1"

#: ★356刀3：池写**串行化**锁。
#:   现状：`persist_pool` / `set_status` / `note_verify_feedback` 都是
#:   「load_pool → 改内存 → _atomic_write_json」，三者互不知情 ⇒ 两个写手
#:   并发时后写者用**旧快照**覆盖前者（lost update）。星轨 356 前置把它列成
#:   "竞态两候选（persist 断链重建 vs 双写）"，本刀先给出**确定的串行化**，
#:   把"到底是断链重建还是双写"的判定留到重启后一拍实测（有锁之后若仍丢，
#:   就排除并发写、直指断链重建）。
#:   RLock：`set_status` 内部可能经 `note_pool_fingerprint` 再次进池，需可重入。
_M356_POOL_LOCK = threading.RLock()


def _m356_serialized(fn):
    """★356刀3：给池写入口加串行化（装饰器形态，零侵入函数体）。"""
    @functools.wraps(fn)
    def _wrap(*args, **kwargs):
        with _M356_POOL_LOCK:
            return fn(*args, **kwargs)
    return _wrap
LEDGER_HISTORY_MAX = 120


#: ★352刀1：批量预检计划 schema
DRIFT_SCHEMA = "audit_pool_target_drift/352.v1"


# ★370刀3 死方法监测标记：定义1 / 生产调用0 / 测试调用0 / 属性读0（全仓扫描实测）。
#   保留不删：实现完整，去留另立裁决轮；review_only 语义不代签删除，人工确认闸保留不绕。
#   引用扫描留痕见 docs/路灯与星轨对话/交付报告/证据件/370/O3两方法死方法监测扫描_20261026.md
def plan_target_drift(entries: list, project_root: str | None = None) -> dict:
    """对池条目批量跑目标存在性预检，**只读不写**，返回流转计划。

    为什么不直接改状态：`set_status` 是**唯一**流转口（它负责合法校验、
    追 history、落盘、留痕）。这里开一条“直接改字段”的旁路等于把那些
    保障全绕过去（铁律205：跨态推进必须过状态机）。故本函数只产计划，
    真正落盘由调用方逐条 `set_status(key, STATUS_TARGET_DRIFTED)`。

    返回：`{schema, total, in_place, drifted, already, other,
             drifted_keys, rows, boundary}`。
    """
    try:
        from nucleus.evolution.patch_generator import (
            TARGET_DRIFTED_VERDICTS as _DR,
            TARGET_IN_PLACE as _IN,
            precheck_batch as _pb,
        )
    except Exception as _e:                                    # noqa: BLE001
        from nucleus._silent_except import silent_exc as _se

        _se(_e, where="audit_problem_pool.plan_target_drift import")
        return {"schema": DRIFT_SCHEMA, "state": "import_failed",
                "error": "%s: %s" % (type(_e).__name__, str(_e)[:200]),
                "total": 0, "in_place": 0, "drifted": 0, "already": 0,
                "other": 0, "drifted_keys": [], "rows": []}
    _rows_in = [e for e in (entries or []) if isinstance(e, dict)]
    _batch = _pb(project_root, _rows_in)
    _by_id = {str(r.get("id") or ""): r for r in _batch["rows"]}
    _drifted_keys: list = []
    _already: list = []
    _in_place: list = []
    _other: list = []
    _detail: list = []
    for _e in _rows_in:
        _k = str(_e.get("dedup_key") or _e.get("id") or "")
        _st = str(_e.get("status") or STATUS_OPEN)
        _r = _by_id.get(_k) or {}
        _v = str(_r.get("verdict") or "")
        _rec = {"dedup_key": _k, "status": _st, "verdict": _v,
                "file": _r.get("file"), "method": _r.get("method"),
                "why": _r.get("why")}
        if _st == STATUS_TARGET_DRIFTED:
            # 幂等：已经标过的不重复计入 drifted（铁律199：标记选“改动后才出现”的状态）
            _already.append(_k)
            _rec["action"] = "already_drifted"
        elif _v in _DR:
            if _st != STATUS_OPEN:
                # 非 open 态要先回 open 才能进 target_drifted（状态机不许跳档）
                _rec["action"] = "need_reopen_first"
                _other.append(_k)
            else:
                _rec["action"] = "mark_drifted"
                _drifted_keys.append(_k)
        elif _v == _IN:
            _in_place.append(_k)
            _rec["action"] = "keep"
        else:
            _other.append(_k)
            _rec["action"] = "unjudged:%s" % _v
        _detail.append(_rec)
    return {
        "schema": DRIFT_SCHEMA,
        "state": "ok",
        "total": len(_rows_in),
        "in_place": len(_in_place),
        "drifted": len(_drifted_keys),
        "already": len(_already),
        "other": len(_other),
        "drifted_keys": _drifted_keys,
        "batch_tally": _batch["tally"],
        "rows": _detail,
        "boundary": "判词只认 file_absent/method_absent 为漂移；"
                    "parse_failed/unreadable 归 other（不能借漂移名义剔掉）；"
                    "同名多类仍计 in_place 但标 ambiguous",
    }


def _project_root() -> str:
    """项目根（★346刀6 修一个 345 遗留的**静默错位**）。

    原实现固定上溯三级。本模块在 `nucleus/` 下（只两级就是项目根），
    上溯三级得到的是**仓库的父目录** ⇒ 默认池路径变成
    `D:\\xinrenlei\\data\\reports\\audit`，**写到仓库外面去了**。

    为什么一直没暴露：345 改码要等下一次重启窗才在活体上生效，此前这段
    在隔离脚本里都走 `root=` / `PULSE_AUDIT_POOL_DIR` 注入，从没走过默认分支。
    346 刀6 的域对齐验收第一次实跑默认路径就把它打出来了
    （日志里目标路径是 `D:\\xinrenlei\\data\\...` 而不是 `...\\tongtong-pulse-v9\\data\\...`）。

    修法：**不再硬编码层级**，按 `main.py` + `config.py` 这两个根标记向上找（有界）。
    找不到就退回两级并告警——宁可给出一个可疑值并留痕，也不要静默错位。
    """
    _cur = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _first = _cur
    for _ in range(4):
        if (os.path.isfile(os.path.join(_cur, "main.py"))
                and os.path.isfile(os.path.join(_cur, "config.py"))):
            return _cur
        _parent = os.path.dirname(_cur)
        if _parent == _cur:
            break
        _cur = _parent
    _module_logger.warning("[346池] 项目根定位失败（无 main.py/config.py），退回 %s", _first)
    return _first


def audit_dir(root: str | None = None, audit_dir_path: str | None = None) -> str:
    """定位池目录。

    优先级：`audit_dir_path`（整目录注入）> 环境变量 `PULSE_AUDIT_POOL_DIR`
    > `root` + 相对路径 > 项目根 + 相对路径。

    环境变量这一层是给**隔离验证**用的：生产不设它就走默认
    `data/reports/audit/`；隔离脚本设了它就一条都不落生产盘（铁律158）。
    """
    if audit_dir_path:
        return str(audit_dir_path)
    _env = os.environ.get("PULSE_AUDIT_POOL_DIR", "").strip()
    if _env:
        return _env
    _root = str(root) if root else _project_root()
    return os.path.join(_root, AUDIT_DIR_REL)


def day_stamp(now: float | None = None) -> str:
    """日期戳 `YYYYMMDD`（日内滚动更新同一快照文件）。"""
    return time.strftime("%Y%m%d", time.localtime(float(now if now is not None else time.time())))


def snapshot_path(now: float | None = None, root: str | None = None,
                  audit_dir_path: str | None = None, day: str | None = None) -> str:
    _day = day or day_stamp(now)
    return os.path.join(audit_dir(root, audit_dir_path),
                        "%s%s%s" % (SNAPSHOT_PREFIX, _day, SNAPSHOT_SUFFIX))


def list_snapshots(root: str | None = None, audit_dir_path: str | None = None) -> list[str]:
    """按日期升序列出全部快照绝对路径。"""
    _dir = audit_dir(root, audit_dir_path)
    try:
        _names = [n for n in os.listdir(_dir)
                  if n.startswith(SNAPSHOT_PREFIX) and n.endswith(SNAPSHOT_SUFFIX)]
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool.list_snapshots 目录不可读", level="debug")
        return []
    return [os.path.join(_dir, n) for n in sorted(_names)]


def make_dedup_key(file: str, method: str, issue_type: str) -> str:
    """池判重键：`sha1(file|method|issue_type)[:16]`。

    ★**弃行号**（烛微345）：既有 per-item 的 `dedup_key` 是 `file:line:type`，
    行号随编辑漂移 ⇒ "同一个问题"每漂一次就算新条目（335/336 踩过，344 又一次）。

    用 sha1 而非内置 `hash()`：后者受 `PYTHONHASHSEED` 影响，跨进程算出的键不同
    ⇒ 重启后同一问题被判新条目（343 批已在 O3 id 上踩过）。

    ★356刀2：**先归一文件路径**再算 sha1。此前 audit 侧原样用 `file` 串（可能带
    反斜杠/绝对路径），而 generator 侧先 `\\`→`/` 再算 ⇒ 同一条问题两侧算出
    **不同键**（烛微356「键双错位」）：回写落空、merge 断配、池里同问题两条。
    归一函数复用 349 刀10② 的 `norm_file_field`（相对项目根的 posix 路径），
    不另造一份口径。
    """
    _norm = norm_file_field(file)
    _raw = "%s|%s|%s" % (_norm, str(method or "").strip(),
                         str(issue_type or "").strip())
    return hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]


# ========== severity 值域 ==========


def normalize_severity(raw: Any) -> tuple[str, bool, str]:
    """返回 `(归一后的值, 是否合法, 原始值字符串)`。

    合法值：low/medium/high（大小写不敏感）+ 语义别名（warning/medium、info/low、
    critical/high）。其余一律判**非法**：值归 `low`、`valid=False`、原值原样带回。
    """
    _raw = str(raw if raw is not None else "").strip()
    if not _raw:
        return "low", True, ""
    _low = _raw.lower()
    if _low in SEVERITY_ALLOWED:
        return _low, True, _raw
    if _low in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[_low], True, _raw
    return "low", False, _raw


# ========== klass（问题大类） ==========

KLASS_CODE_DEFECT = "code_defect"
KLASS_POLLUTION = "pollution"
KLASS_EXPERIENCE = "experience"
KLASS_UNKNOWN = "unknown"
#: ★367 后续（T-死代码放大器修复-1 第二件事）：**观测/趋势类**。
#: 与 code_defect 的区别：趋势条目**不可修**（它们是「指标在涨」这件事本身），
#: 只该进看板被人看，不该进 patch_pool 抢可修项的容量与排序位。
KLASS_TREND = "trend"

#: issue_type → klass 族表。
#:
#: ★这张表不是拍脑袋写的：`data/reports/code_review/run_20261022_205339.json`
#: 实测 665 条只有 10 种 type ——
#:   dead_code 595 / cross_module_singleton_call 23 / periodic_task_no_reentry 11 /
#:   config_disabled 11 / long_method 8 / print_debug 7 / non_atomic_write 6 /
#:   lock_with_emit 2 / resource_no_close 1 / unknown 1
#: 全部是代码面问题（当前审查器不产 pollution / experience 类）。
#: 漏一个类型就等于让该类型整批掉进兜底桶（本批已知教训：`silent_except`
#: 写成 `silent_exception` 就漏了一批）。
_KLASS_TABLE = {
    "code_defect": ("dead_code", "cross_module_singleton_call", "periodic_task_no_reentry",
                    "config_disabled", "long_method", "print_debug", "non_atomic_write",
                    "lock_with_emit", "resource_no_close",
                    "bare_except", "silent_exception", "silent_except", "high_complexity",
                    "hardcoded_secret", "sql_injection", "unsafe_eval", "mutable_default",
                    "unused_import", "undefined_name", "syntax_error", "type_error",
                    "broken_chain", "legacy_assembly"),
    "pollution": ("pollution", "meta_description", "low_quality", "duplicate_node",
                  "shallow_node", "noise"),
    "experience": ("experience", "reflection", "insight", "growth"),
    # ★367 后续：趋势类**显式**收录。
    #   此前这三个靠「族表未收录 ⇒ unknown ⇒ 兜底 optimize_list」才进到看板侧，
    #   是巧合不是设计（见本文件 split_routes 上方「掉进兜底桶」的注释）。
    #   显式归类后：谁把它们挪进 code_defect，就会立刻被本表的意图挡住。
    "trend": ("cc_ge50_new", "code_loc_weekly_growth", "silent_except_weekly_rise"),
}


def classify_klass(issue_type: Any) -> str:
    """把一个 issue_type 归到问题大类。先查字面量，再查族表，都不中 ⇒ unknown。"""
    _t = str(issue_type or "").strip().lower()
    if _t in (KLASS_CODE_DEFECT, KLASS_POLLUTION, KLASS_EXPERIENCE, KLASS_TREND):
        return _t
    for _klass, _members in _KLASS_TABLE.items():
        if _t in _members:
            return _klass
    return KLASS_UNKNOWN


def _version_stamps() -> dict:
    """取 criteria/code 版本戳（取不到返回 None，**不编**）。"""
    try:
        from nucleus.exploration_audit import _m338_version_stamps
        _s = _m338_version_stamps()
        return {"criteria_version": _s.get("criteria_version"),
                "code_version": _s.get("code_version")}
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool._version_stamps", level="debug")
        return {"criteria_version": None, "code_version": None}


def _proc_gen() -> str:
    """进程代次（唯一口 `proc_gen.proc_gen_str()`；取不到记 unknown，不填 0）。"""
    try:
        from nucleus.proc_gen import proc_gen_str
        return str(proc_gen_str() or "unknown")
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool._proc_gen", level="debug")
        return "unknown"



def _m349_gate_field() -> str:
    """★349刀2：池条目的 `gate` 字段值（只读落盘结论）。

    单独抽函数是为了**可测**：测试可以直接断言"读不到 ⇒ ABSENT，不是 OPEN"。
    """
    try:
        from nucleus.evolution.official_verdict_read import gate_field as _gf

        return str(_gf())
    except Exception as _e:  # noqa: BLE001
        silent_exc(_e, "audit_problem_pool._m349_gate_field", level="debug")
        return "UNAVAILABLE"



#: ★349刀10②：项目根目录名（用于把绝对路径切成相对路径）。
#   ★为什么不按"上溯 N 级"定位：那在 worktree/子目录下必错（铁律187）。
#     这里只做**文本切片**（去掉盘符 + 到本目录名之前的部分），不解析文件系统。
_PROJ_DIRNAME = "tongtong-pulse-v9"


def norm_file_field(file: Any) -> str:
    """把 `file` 归一到「相对项目根的 posix 路径」。

    三种输入统一成一种输出：
      `D:\\xinrenlei\\tongtong-pulse-v9\\organs\\body\\PulseLiver.py` → `organs/body/PulseLiver.py`
      `./organs/body/PulseLiver.py`                                  → `organs/body/PulseLiver.py`
      `organs/body/PulseLiver.py`                                    → 原样
    不属于本项目的绝对路径（别的盘/别的目录）**原样保留**（不硬切，切开就没意义了）。
    """
    if not isinstance(file, str) or not file:
        return str(file or "")
    _f = file.replace("\\", "/").strip()
    # ★349刀10②：项目名做**大小写不敏感**匹配（Windows 路径大小写不敏感，
    #   `D:\\XinRenLei\\TongTong-Pulse-V9\\...` 与 `D:\\xinrenlei\\tongtong-pulse-v9\\...`
    #   是同一个文件；逐字比较会让同一文件派生两个 dedup_key —— 正是本刀要消灭的缺陷）。
    #   切完**保留尾部原始大小写**，不改被审计文件名的写法。
    _low = _f.lower()
    _mark = "/%s/" % _PROJ_DIRNAME.lower()
    _i = _low.find(_mark)
    if _i >= 0:
        _f = _f[_i + len(_mark):]
    elif _low.startswith(_PROJ_DIRNAME.lower() + "/"):
        _f = _f[len(_PROJ_DIRNAME) + 1:]
    while _f.startswith("./"):
        _f = _f[2:]
    return _f.lstrip("/")


def _m349_norm_enabled() -> bool:
    """归一闪开关（默认 True；关 ⇒ 逐字回到改前行为）。"""
    try:
        import config as _c

        return bool(getattr(_c, "ENABLE_FILE_FIELD_NORMALIZE", True))
    except Exception as _e:  # noqa: BLE001
        silent_exc(_e, "audit_problem_pool._m349_norm_enabled", level="debug")
        return True


# ========== 条目构造 ==========


# ===== ★350刀6②：解析兜底桶修复（兜底行补齐归因）=====

#: 兜底原因标识（逐条可计数，不是一个粘贴字符串）
ATTR_MISSING_MESSAGE = "missing_message"
ATTR_UNKNOWN_TYPE = "unknown_issue_type"
ATTR_LINE_ZERO = "line_zero_or_missing"
ATTR_MISSING_FILE = "missing_file"


def _m350_desc_of(issue: dict) -> str:
    """★350刀6①：入池 desc 过一道动态读数归一（与生成器同判）。"""
    _raw = str(issue.get("message", "") or issue.get("description", "") or "")
    try:
        from nucleus.evolution.desc_norm import normalize_desc
        return str(normalize_desc(_raw)["desc"])[:400]
    except Exception as _e:                                      # noqa: BLE001
        silent_exc(_e, where="audit_problem_pool::_m350_desc_of", level="debug")
        return _raw[:400]


def _m350_attribution(issue: dict) -> dict:
    """★350刀6②：兜底行补齐归因（源行 + 原因）。

    病因（349 刀6 副产物）：`nucleus/field/InfoField.py:0`、
    `nucleus/aibot_logger.py:0` 这类条目以 `type=unknown / severity=ERROR /
    line=0 / desc=""` 进池 —— 它们没有身份，既无法对差也无法处置，
    只能堆在 `unknown` 兜底桶里。现在至少能回答三件事：缺什么、
    源行是什么、能不能定位。
    """
    _why: list = []
    _msg = str(issue.get("message", "") or issue.get("description", "") or "")
    if not _msg.strip():
        _why.append(ATTR_MISSING_MESSAGE)
    _it = issue.get("issue_type") or issue.get("type")
    if not _it or str(_it).strip().lower() in ("", "unknown", "none"):
        _why.append(ATTR_UNKNOWN_TYPE)
    _ln = issue.get("line")
    if _ln is None or str(_ln).strip() in ("", "0"):
        _why.append(ATTR_LINE_ZERO)
    if not str(issue.get("file") or "").strip():
        _why.append(ATTR_MISSING_FILE)
    _src = ""
    for _k in ("raw", "source_line", "line_text", "evidence", "raw_line"):
        _v = issue.get(_k)
        if isinstance(_v, str) and _v.strip():
            _src = _v.strip()[:300]
            break
    return {"schema": "audit_pool_attribution/350.v1",
            "fallback": bool(_why), "reasons": _why,
            "source_line": _src, "source_line_state": ("ok" if _src else "absent"),
            "locatable": bool(str(issue.get("file") or "").strip()
                              and _ln is not None and str(_ln) != "0")}



# ===== ★351刀4：历史兜底条目追溯（attribution 补齐）=====
#   350 刀6 的归因只挂在 `entry_from_issue` 上 ⇒ **只对新入池条目生效**；
#   存量条目（实测快照 1356 条里 677 条无 attribution 字段）一直是空白的。
#   追溯点选在 `persist_pool`（唯一"全量 entries 都在手里"的地方），
#   判据与归因器**与 350 完全同一套**（`_m350_attribution`），不留第二口径。
BACKFILL_SCHEMA = "audit_pool_attribution_backfill/351.v1"


def backfill_attribution(entries: list, *, now: float | None = None) -> dict:
    """给缺 `attribution` 的历史条目补齐归因（幂等）。

    ★为什么不新写一套判据：归因口径一旦分叉，两份 attribution 会互相打脸
    （350 刀6 已有的 `fallback=false` 与新写的不一致，读的人不知道信谁）。
    这里只复用 `_m350_attribution`，额外加三个"这是追溯来的"标记字段。
    """
    _t = float(now if now is not None else time.time())
    _n_missing = _n_filled = _n_fallback = _n_locatable = 0
    _already = 0
    for _e in (entries or []):
        if not isinstance(_e, dict):
            continue
        _old = _e.get("attribution")
        if isinstance(_old, dict) and _old.get("schema"):
            _already += 1
            continue
        _n_missing += 1
        # 池条目的 desc 就是 350 归因器要的 message（字段名不同，语义同一个）
        _probe = {
            "message": _e.get("desc") or "",
            "issue_type": _e.get("issue_type"),
            "line": _e.get("line"),
            "file": _e.get("file"),
        }
        _att = _m350_attribution(_probe)
        _att["backfilled"] = True
        _att["backfilled_at"] = _t
        _att["backfill_schema"] = BACKFILL_SCHEMA
        _e["attribution"] = _att
        _n_filled += 1
        if _att.get("fallback"):
            _n_fallback += 1
        if _att.get("locatable"):
            _n_locatable += 1
    return {
        "schema": BACKFILL_SCHEMA,
        "already": _already,
        "missing": _n_missing,
        "filled": _n_filled,
        "fallback": _n_fallback,
        "locatable": _n_locatable,
        "idempotent_second_call_filled": 0 if _n_missing == 0 else None,
        "boundary": ("只补「缺 attribution 字段」的条目；已有的**不覆盖**"
                     "（覆盖会把 350 的原值改成追溯值，等于篡改历史）"),
    }


def entry_from_issue(issue: dict, now: float | None = None,
                     stamps: dict | None = None, proc_gen: str | None = None) -> dict | None:
    """把一条 issue 转成池条目（16 字段口径）。非 dict 返回 None（不猜）。"""
    if not isinstance(issue, dict):
        return None
    _ts = float(now if now is not None else time.time())
    _st = stamps if isinstance(stamps, dict) else _version_stamps()
    _file_raw_in = str(issue.get("file", "") or "")
    # ★349刀10②：file 归一到相对 posix 路径（幂等：已归一的原样返回）
    _file = norm_file_field(_file_raw_in) if _m349_norm_enabled() else _file_raw_in
    _method = str(issue.get("method", "") or "")
    _itype = str(issue.get("type", "") or issue.get("issue_type", "") or "unknown")
    _key = make_dedup_key(_file, _method, _itype)
    _sev, _sev_ok, _sev_raw = normalize_severity(issue.get("severity"))
    _line_raw = issue.get("line", 0)
    try:
        _line = max(0, int(_line_raw))
    except (TypeError, ValueError) as _le:
        # 行号非数值 ⇒ 按 0 处理（dedup_key 已弃行号，不影响身份）
        silent_exc(_le, "audit_problem_pool.entry_from_issue line 非数值按 0", level="debug")
        _line = 0
    _entry = {
        "id": _key,
        "issue_type": _itype,
        "severity": _sev,
        "file": _file,
        "line": _line,
        "method": _method,
        "desc": _m350_desc_of(issue),
        "attribution": _m350_attribution(issue),
        "dedup_key": _key,
        "klass": classify_klass(_itype),
        "criteria_version": _st.get("criteria_version"),
        "code_version": _st.get("code_version"),
        "first_seen_ts": _ts,
        "last_seen_ts": _ts,
        "proc_gen_first": str(proc_gen or _proc_gen()),
        "status": STATUS_OPEN,
        "closed_reason": "",
        "status_history": [{"status": STATUS_OPEN, "at": _ts}],
        # ★349刀2 读点③：官方结论的 `gate` 字段（供自知柱面周报按 gate 分组）。
        #   **零新采集**：只读已落盘的判定结果，绝不在这里再跑一次判定
        #   （`o3_open_gate_report` 是重活，池构造是每日热路径）。
        #   读不到就是 `ABSENT`/`UNAVAILABLE`，**不猜**、更不写成 OPEN。
        "gate": _m349_gate_field(),
    }
    if _file != _file_raw_in:
        _entry["file_raw"] = _file_raw_in
    if not _sev_ok:
        # 不吞不弃：原值留证，条目照入池但强制走清洗队列
        _entry["severity_raw"] = _sev_raw
        _entry["severity_invalid"] = True
    # 刀2：分流结果随条目持久化。优先用接线层已打好的 route；
    #   没打（例如直接调 persist_pool 的调用方）就按 klass 现算——
    #   不留"池里有条目却没有出口"的中间态（本批实测踩过：s10f 直接读 route 落空）。
    _route = issue.get("route")
    _entry["route"] = str(_route) if _route else classify_route(_itype, bool(_entry.get("severity_invalid")))
    return _entry


def read_snapshot(path: str) -> dict:
    """读一份快照。返回 `{state, entries, generated_at, reason}`。"""
    if not os.path.isfile(path):
        return {"state": "missing", "entries": [], "generated_at": None, "reason": "file_not_found"}
    try:
        with open(path, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, "audit_problem_pool.read_snapshot 快照不可读", level="debug")
        return {"state": "unavailable", "entries": [], "generated_at": None,
                "reason": "%s: %s" % (type(_e).__name__, _e)}
    if not isinstance(_d, dict) or not isinstance(_d.get("entries"), list):
        return {"state": "unavailable", "entries": [], "generated_at": None,
                "reason": "bad_structure"}
    _entries = [e for e in _d["entries"] if isinstance(e, dict) and e.get("dedup_key")]
    return {"state": "ok", "entries": _entries,
            "generated_at": _d.get("generated_at"), "reason": ""}


def load_pool(root: str | None = None, audit_dir_path: str | None = None,
              now: float | None = None, exclude_current: bool = False) -> dict:
    """读回池：从**最新**快照往最旧找。

    返回 `{state, entries, source, generated_at, reason}`：
    - `state="ok"`：至少读到一份快照
    - `state="empty"`：目录/快照一个都没有（首次运行，合法空池）
    - `state="unavailable"`：目录存在但所有快照都读不出（**不猜 0**）
    """
    _paths = list_snapshots(root, audit_dir_path)
    _today = snapshot_path(now, root, audit_dir_path)
    if exclude_current:
        _paths = [p for p in _paths if os.path.abspath(p) != os.path.abspath(_today)]
    # ★从**最新**往最旧找：文件名含日期 ⇒ 字典序＝时间序。
    #   绝不能"把 today 提到最前再 reversed"——reversed 会把最旧的翻到最前，
    #   导致 set_status 每次从旧快照读、往新快照写，状态永远回退（本批实测踩过）。
    for _p in reversed(_paths):
        _r = read_snapshot(_p)
        if _r["state"] == "ok":
            return {"state": "ok", "entries": _r["entries"],
                    "source": os.path.basename(_p), "generated_at": _r["generated_at"],
                    "reason": ""}
        if _r["state"] == "unavailable":
            _module_logger.warning("[345池] 快照不可读，跳过: %s (%s)",
                                   os.path.basename(_p), _r["reason"])
    if _paths:
        return {"state": "unavailable", "entries": [], "source": "", "generated_at": None,
                "reason": "all_snapshots_unreadable"}
    return {"state": "empty", "entries": [], "source": "", "generated_at": None,
            "reason": "no_snapshot"}


def _is_open(entry: dict) -> bool:
    return str(entry.get("status", STATUS_OPEN)) not in TERMINAL_STATUSES


def _m383_is_actionable(entry: dict) -> bool:
    """★383刀4：「可推进」判据 —— 与 `_is_open`（未结案）**不是一回事**。

    为什么要分开：`_is_open` 被 `prune` 用来保护「超期但未结案也留」，
    那是**留存**语义；限额与全局截断排序要的是「谁该占观察位」，是**优先级**
    语义。混用会让 stale 条目要么被删（错）、要么继续占位（也错）。
    """
    if not _is_open(entry):
        return False
    return str(entry.get("status", STATUS_OPEN)) != STATUS_STALE


def _transition_verdict(old: str, new: str) -> str:
    """★383刀4：流转合法性判定的**唯一真源**（`set_status` 与批量转换共用）。

    返回 "ok" / "bad_status" / "illegal_transition"。
    抽这一层是为了让批量转 stale 复用同一张 `_LEGAL_TRANSITIONS`，
    而不是在别处再写一份 if 判断 —— 两份规则必然分叉。
    """
    if new not in ALL_STATUSES:
        return "bad_status"
    if new not in _LEGAL_TRANSITIONS.get(old, ()):
        return "illegal_transition"
    return "ok"


#: ★383刀4：O3 判死族。与 `patch_execution_dispatch._M339_O3_ISSUE_TYPES` 同集，
#: 有守卫测试断言两者一致（这里不直接 import 是为了避免 evolution ↔ audit 循环依赖）。
_M383_O3_DEAD_TYPES = ("dead_code", "dead_code_candidate")

#: ★383刀4：OPEN 超时天数兜底值（票面默认 7 天；config.O3_STALE_TTL_DAYS 优先）。
_M383_O3_STALE_TTL_DAYS = 7


def _m383_o3_stale_on() -> bool:
    """★383刀4：O3 stale 计时总开关（读 config，**默认关** ⇒ 零回归）。"""
    try:
        import config as _c383
        return bool(getattr(_c383, "ENABLE_O3_STALE_TIMEOUT", False))
    except Exception as _e383:
        silent_exc(_e383, where="audit_problem_pool::_m383_o3_stale_on",
                   level="debug")
        return False


def _m383_o3_stale_ttl_days() -> float:
    """★383刀4：TTL 天数（config.O3_STALE_TTL_DAYS 优先，兜底 7 天）。"""
    try:
        import config as _c383
        return float(getattr(_c383, "O3_STALE_TTL_DAYS", _M383_O3_STALE_TTL_DAYS))
    except Exception as _e383:
        silent_exc(_e383, where="audit_problem_pool::_m383_o3_stale_ttl_days",
                   level="debug")
        return float(_M383_O3_STALE_TTL_DAYS)


def stale_o3_open_entries(entries: list, now: float | None = None,
                          ttl_days: float | None = None) -> tuple:
    """★383刀4：O3 判死条目 OPEN 超时 ⇒ 转 stale（票面「不再占观察位」）。

    **纯函数**：不读盘、不落盘、不改全局状态；只改传入的 dict（与 `prune` /
    `cap_low_severity` 同风格），返回 `(entries, changed)`。

    只吃同时满足三条的条目：
      1. `issue_type` ∈ O3 判死族（dead_code / dead_code_candidate）
      2. `status == "open"`（claimed/fixed 有人在推进，不动）
      3. `now - first_seen_ts > ttl_days * 86400`

    按 `_transition_verdict` 校验后再改 —— 走的是与 `set_status` 同一张
    `_LEGAL_TRANSITIONS`，不另写一份规则。
    """
    _now = float(now if now is not None else time.time())
    _ttl = _m383_o3_stale_ttl_days() if ttl_days is None else float(ttl_days)
    _limit = _now - _ttl * 86400.0
    _changed = 0
    for _e in (entries if isinstance(entries, list) else []):
        if not isinstance(_e, dict):
            continue
        if str(_e.get("issue_type") or "") not in _M383_O3_DEAD_TYPES:
            continue
        if str(_e.get("status", STATUS_OPEN)) != STATUS_OPEN:
            continue
        _f = _first_ts(_e)
        if _f is None or float(_f) > _limit:
            continue
        if _transition_verdict(STATUS_OPEN, STATUS_STALE) != "ok":
            continue
        _e["status"] = STATUS_STALE
        _hist = _e.get("status_history")
        if not isinstance(_hist, list):
            _hist = []
        _hist.append({"status": STATUS_STALE, "at": _now,
                      "note": "O3 判死超 %g 天未裁决（383刀4）" % _ttl})
        _e["status_history"] = _hist
        _changed += 1
    return entries, _changed


def cap_low_severity(entries: list, cap: int | None = None) -> tuple:
    """★367 后续：低危（severity=low）条目入池限额。纯函数，可单测。

    规则（顺序即优先级）：
      · `medium` / `high` **全量保留** —— 限额只砍 low，绝不挤掉可修项；
      · `low` 按「open 优先 → 首次时间新优先」保留前 `cap` 条
        （与全局截断 `MAX_POOL_ENTRIES` 的排序键一致，避免两处口径分叉）；
      · 未超限时**原样返回**（连顺序都不动），保证无超限时零影响。

    ★为什么返回 dropped 而不是就地删：调用方必须留痕（铁律 156 不静默降级）。
      被截下的条目**不是被判定为无价值**，只是本拍快照不收录。

    Returns:
        (kept, dropped)：kept 保持入参相对顺序；dropped 为被截下的 low 条目（升序按同键）
    """
    _cap = MAX_LOW_ENTRIES if cap is None else int(cap)
    if _cap < 0:
        _cap = 0

    def _is_low(_e: dict) -> bool:
        return str(_e.get("severity") or "").strip().lower() == "low"

    _low = [_e for _e in entries if _is_low(_e)]
    if len(_low) <= _cap:
        return list(entries), []

    # ★383刀4：排序用「可推进」而非「未结案」—— stale 条目要让位，但不结案。
    _low_sorted = sorted(_low, key=lambda _e: (0 if _m383_is_actionable(_e) else 1,
                                               -(_first_ts(_e) or 0)))
    _keep_low_ids = {id(_e) for _e in _low_sorted[:_cap]}
    _dropped = _low_sorted[_cap:]
    # 保序重建：原来的 entries 顺序里筛，避免重排影响下游按序消费
    _kept = [_e for _e in entries
             if (not _is_low(_e)) or id(_e) in _keep_low_ids]
    return _kept, _dropped


def _first_ts(entry: dict) -> float:
    """兼容 `first_seen_ts`（新口径）与 `first_seen_at`（旧快照）。"""
    for _k in ("first_seen_ts", "first_seen_at"):
        _v = entry.get(_k)
        if _v is not None:
            try:
                return float(_v)
            except (TypeError, ValueError) as _m360e:
                # ★360刀3：first_seen 转 float 失败 ⇒ 该条目被跳过，池统计失真
                note_drop("audit_problem_pool._first_ts", "cast_failed", "%s" % _m360e)
                continue
    return 0.0


def prune(entries: list, now: float | None = None,
          retain_days: int = POOL_RETAIN_DAYS) -> tuple[list, int]:
    """滚动保留：`retain_days` 内全留；超期但**未结案**也留；超期且结案剔除。"""
    _now = float(now if now is not None else time.time())
    _cutoff = _now - max(0, int(retain_days)) * 86400.0
    _keep: list = []
    _dropped = 0
    for _e in entries:
        if not isinstance(_e, dict):
            _dropped += 1
            continue
        _f = _first_ts(_e) or _now  # 无时间戳视为新条目，保留
        if _f >= _cutoff or _is_open(_e):
            _keep.append(_e)
        else:
            _dropped += 1
    return _keep, _dropped


def _merge(old_entries: list, new_entries: list, now: float) -> tuple[list, int, int]:
    """合并：同 dedup_key 继承 first_seen_ts / status / proc_gen_first。"""
    _by_key: dict[str, dict] = {}
    for _e in old_entries:
        _k = str(_e.get("dedup_key") or "")
        if _k:
            _by_key[_k] = _e
    _added = 0
    _updated = 0
    for _n in new_entries:
        _k = _n["dedup_key"]
        _old = _by_key.get(_k)
        if _old is None:
            _by_key[_k] = _n
            _added += 1
            continue
        _old["last_seen_ts"] = now
        # 首见时间/首见代次跨拍继承（不可被本拍覆盖）
        _old["first_seen_ts"] = _old.get("first_seen_ts") or _old.get("first_seen_at") or _n["first_seen_ts"]
        _old.pop("first_seen_at", None)
        _old["proc_gen_first"] = _old.get("proc_gen_first") or _n.get("proc_gen_first")
        # 位置/描述/级别允许刷新（同一问题的位置会漂、描述会更精确）
        for _f in ("line", "severity", "issue_type", "klass", "desc", "route",
                   "criteria_version", "code_version"):
            if _n.get(_f) not in (None, ""):
                _old[_f] = _n[_f]
        if _n.get("severity_invalid"):
            _old["severity_invalid"] = True
            _old["severity_raw"] = _n.get("severity_raw", "")
        _updated += 1
    return list(_by_key.values()), _added, _updated


def _atomic_write_json(path: str, payload: dict) -> bool:
    """原子写（tmp + os.replace）。写盘经 guard_write 守卫（项目约定）。"""
    try:
        # ★346刀6（A-17 第三类出口对齐）：隔离进程**不得写生产池**。
        #   判据是结构性的（不猜）：存在显式隔离声明 且 目标就是生产池目录 ⇒ 拒写。
        #   **不改道**：`PULSE_AUDIT_POOL_DIR` 已经在 `audit_dir()` 那层把目录换掉了，
        #   这里再改道会二次搬移（声明的池目录叫 audit ⇒ 被当成生产目录再挪一层），
        #   隔离证据会跑到意料之外的路径上。拒写比改道更容易解释，也更安全。
        #
        #   ★顺序很重要：守卫必须在 `os.makedirs` **之前**。否则"拒写"虽然成立，
        #   目录已经被建出来了 —— 隔离进程仍然在生产目录树上留了痕（本批 d6 首轮
        #   就是这么发现问题的：文件没写，空目录出现了）。
        try:
            from nucleus import domain_guard as _m346_dg
            _decl = _m346_dg.isolation_declared()
            if _decl:
                _prod = os.path.abspath(os.path.join(_project_root(), AUDIT_DIR_REL))
                if os.path.abspath(os.path.dirname(path)) == _prod:
                    _module_logger.warning(
                        "[346池] 隔离声明在位（%s），拒绝写生产池: %s", _decl, path)
                    return False
        except Exception as _dge:
            silent_exc(_dge, "audit_problem_pool._atomic_write_json 346刀6 域过滤", level="debug")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            from nucleus.data.write_guard import guard_write
            if not guard_write(path, explicit=True):
                _module_logger.warning("[345池] 写盘守卫拒绝，本次不落盘: %s", path)
                return False
        except Exception as _ge:
            silent_exc(_ge, "audit_problem_pool._atomic_write_json guard_write", level="debug")
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(payload, _f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(_tmp, path)
        return True
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool._atomic_write_json 落盘失败", level="warning")
        return False


def _status_counts(entries: list) -> dict:
    _out = {s: 0 for s in ALL_STATUSES}
    for _e in entries:
        _s = str(_e.get("status", STATUS_OPEN))
        if _s in _out:
            _out[_s] += 1
        else:
            _out[STATUS_OPEN] += 1
    return _out


@_m356_serialized
def persist_pool(report: dict, root: str | None = None, audit_dir_path: str | None = None,
                 now: float | None = None, retain_days: int = POOL_RETAIN_DAYS) -> dict:
    """主入口：审查报告 → 合并进池 → prune → 原子落盘 → 留痕。

    Returns: `{state, path, total, added, updated, pruned, severity_rejected, ...}`
    `state="skipped"` 表示无可落盘内容（非 dict / 无 issues），**不写空快照**。
    """
    _now = float(now if now is not None else time.time())
    if not isinstance(report, dict):
        return {"state": "skipped", "reason": "report_not_dict", "total": 0}
    _issues = report.get("issues")
    if not isinstance(_issues, list):
        return {"state": "skipped", "reason": "no_issues_field", "total": 0}

    _prev = load_pool(root, audit_dir_path, _now)
    if _prev["state"] == "unavailable":
        # 读不到旧池时**不静默重建**：会让全部 first_seen_ts 重置。
        # 但本拍仍落盘（否则永远修不好），并在 payload 标明这是"断链重建"。
        _module_logger.warning("[345池] 旧池不可读，本拍按断链重建（first_seen_ts 会重置）: %s",
                               _prev.get("reason", ""))
    _old = _prev["entries"] if _prev["state"] == "ok" else []

    _stamps = _version_stamps()
    _pg = _proc_gen()
    _new: list = []
    _rejected: list = []
    for _it in _issues:
        _e = entry_from_issue(_it, _now, stamps=_stamps, proc_gen=_pg)
        if _e is None:
            continue
        if _e.get("severity_invalid"):
            _rejected.append({"id": _e["id"], "severity_raw": _e.get("severity_raw", "")})
        _new.append(_e)

    _merged, _added, _updated = _merge(_old, _new, _now)
    # ★351刀4：历史兜底条目追溯（每拍都可安全调用：已有 attribution 的跳过）。
    _bf = backfill_attribution(_merged, now=_now)
    if _bf["filled"]:
        _module_logger.info(
            "[351归因] 历史条目追溯: filled=%d already=%d fallback=%d locatable=%d "
            "proc_gen=%s", _bf["filled"], _bf["already"], _bf["fallback"],
            _bf["locatable"], _pg)
    # ★383刀4：O3 判死条目 OPEN 超时 ⇒ 转 stale（开关默认关）。
    #   位置：prune 之前、cap 之前 —— prune 只按 first_seen 判留存、不看 status
    #   （stale 不进 TERMINAL_STATUSES ⇒ 仍受「未结案也留」保护，不会被删）；
    #   而 cap 与全局截断要看到 stale 才能把它排到后面。
    _stale_n = 0
    if _m383_o3_stale_on():
        _merged, _stale_n = stale_o3_open_entries(_merged, now=_now)
        if _stale_n:
            _module_logger.info(
                "[383刀4] O3 判死超 %g 天未裁决 ⇒ 转 stale: %d 条（不再占观察位）",
                _m383_o3_stale_ttl_days(), _stale_n)
    _kept, _pruned = prune(_merged, _now, retain_days)
    # ★367 后续：低危限额（必须在全局截断**之前**：否则全局截断先砍，限额就形同虚设）
    _kept, _low_dropped = cap_low_severity(_kept)
    if _low_dropped:
        _module_logger.warning(
            "[367低危限额] 低危条目超限：保留 %d 条，本快照未收录 %d 条"
            "（review_only 类，非丢弃：生成侧幂等，下拍仍会送来）",
            MAX_LOW_ENTRIES, len(_low_dropped))
    _truncated = 0
    if len(_kept) > MAX_POOL_ENTRIES:
        # ★383刀4：同上，stale 排后（它不该占截断名额，但仍算未结案）。
        _kept.sort(key=lambda e: (0 if _m383_is_actionable(e) else 1,
                                  -(_first_ts(e) or 0)))
        _truncated = len(_kept) - MAX_POOL_ENTRIES
        _kept = _kept[:MAX_POOL_ENTRIES]

    _path = snapshot_path(_now, root, audit_dir_path)
    _payload = {
        "schema": SCHEMA,
        "snapshot_day": day_stamp(_now),
        "generated_at": _now,
        "criteria_version": _stamps.get("criteria_version"),
        "code_version": _stamps.get("code_version"),
        "proc_gen": _pg,
        "retain_days": int(retain_days),
        "total_entries": len(_kept),
        "status_counts": _status_counts(_kept),
        "severity_invalid_count": len(_rejected),
        "low_severity_capped": len(_low_dropped),
        "prev_source": _prev.get("source", ""),
        "prev_state": _prev["state"],
        "entries": _kept,
    }
    if _truncated:
        _module_logger.warning("[345池] 条目超上限 %d，截断 %d 条", MAX_POOL_ENTRIES, _truncated)
    if not _atomic_write_json(_path, _payload):
        return {"state": "write_failed", "reason": "guard_or_os", "total": len(_kept)}

    # severity 非法值：不吞不弃，逐条留痕（按 id 去重后打，避免每拍刷屏）
    for _r in _rejected[:20]:
        _module_logger.warning("[345池] severity非法值: %s=%s", _r["id"], _r["severity_raw"])
    if len(_rejected) > 20:
        _module_logger.warning("[345池] severity非法值: 另有 %d 条未逐条打印",
                               len(_rejected) - 20)
    if _pruned:
        _module_logger.info("[345池] prune: %d 条", _pruned)
    _module_logger.info(
        "[345池] persist: total=%d new=%d updated=%d pruned=%d stale=%d sev_invalid=%d file=%s proc_gen=%s",
        len(_kept), _added, _updated, _pruned, _stale_n, len(_rejected),
        os.path.basename(_path), _pg)
    # ★348刀8：**先记账再查删**（顺序不可倒：反了会把本拍刚写的快照报成"指纹缺失"）。
    _m348_fp = note_pool_fingerprint(_path, _payload, _now, root, audit_dir_path)
    _m348_loss = detect_pool_loss(root, audit_dir_path, _now, current_path=_path)
    return {"state": "ok", "path": _path, "total": len(_kept), "added": _added,
            "updated": _updated, "pruned": _pruned, "truncated": _truncated,
            "stale_converted": _stale_n,
            "severity_rejected": len(_rejected),
            "snapshot_day": day_stamp(_now), "prev_state": _prev["state"],
            "proc_gen": _pg,
            "fingerprint_ok": bool(_m348_fp.get("ok")),
            "pool_loss": {"state": _m348_loss.get("state"),
                          "alarms": _m348_loss.get("alarms"),
                          "unregistered": _m348_loss.get("unregistered"),
                          "missing": _m348_loss.get("missing")}}


# ★370刀3 死方法监测标记：定义1 / **生产调用0** / **测试调用2** / 属性读0。
#   ★核真伪：票面记为「调用0」，实测 tests/test_m345_batch.py:101/108 有 2 处真实调用
#   （p.restore_pool(...)，覆盖 ok/total/empty 三形态）⇒ 它**不是无调用方的死方法**，
#   而是「生产面暂无调用方、测试面在用」。去留另立裁决轮，本刀只出监测基线。
#   另：全仓第 2 处同名定义位于 .bak_batch350_ap.py:680（历史备份副本，非生产面，不计）。
#   引用扫描留痕见 docs/路灯与星轨对话/交付报告/证据件/370/O3两方法死方法监测扫描_20261026.md
def restore_pool(root: str | None = None, audit_dir_path: str | None = None,
                 now: float | None = None) -> dict:
    """重启恢复：读最近快照，恢复池（不再清零）。

    与 `load_pool` 的区别：这是**重启入口**，多留一行 `[345池] restore:` 痕迹，
    并对 `unavailable` / `empty` 分别给出明确状态（"没恢复"与"池本来就是空的"不能混）。
    """
    _r = load_pool(root, audit_dir_path, now)
    if _r["state"] == "ok":
        _module_logger.info("[345池] restore: %d 条 from %s",
                            len(_r["entries"]), _r.get("source", ""))
        return {"state": "ok", "total": len(_r["entries"]), "source": _r["source"],
                "status_counts": _status_counts(_r["entries"]),
                "entries": _r["entries"]}
    if _r["state"] == "empty":
        _module_logger.info("[345池] restore: 0 条（无历史快照，属首次运行）")
        return {"state": "empty", "total": 0, "source": "", "entries": [],
                "status_counts": _status_counts([])}
    _module_logger.warning("[345池] restore: unavailable（全部快照不可读，不猜 0）")
    return {"state": "unavailable", "total": None, "source": "", "entries": [],
            "status_counts": {}, "reason": _r.get("reason", "")}


@_m356_serialized
def set_status(dedup_key: str, new_status: str, root: str | None = None,
               audit_dir_path: str | None = None, now: float | None = None,
               note: str = "") -> dict:
    """状态机流转（刀2）：校验合法流转 → 改状态 → 追加 history → 落盘 → 留痕。

    `verified` / `wontfix` 为终态，同时写 `closed_reason`。
    非法流转只告警并返回 `state="illegal_transition"`，**不写**（不静默纠正）。
    """
    _now = float(now if now is not None else time.time())
    if new_status not in ALL_STATUSES:
        _module_logger.warning("[345流] 非法状态值: %r（合法值 %s）", new_status, list(ALL_STATUSES))
        return {"state": "bad_status", "changed": False}
    _pool = load_pool(root, audit_dir_path, _now)
    if _pool["state"] != "ok":
        _module_logger.warning("[345流] 池不可用，状态变更失败: key=%s state=%s",
                               dedup_key, _pool["state"])
        return {"state": _pool["state"], "changed": False}
    _entries = _pool["entries"]
    _hit = None
    for _e in _entries:
        if str(_e.get("dedup_key")) == str(dedup_key):
            _hit = _e
            break
    if _hit is None:
        _module_logger.warning("[345流] 未找到条目: key=%s", dedup_key)
        return {"state": "not_found", "changed": False}
    _old = str(_hit.get("status", STATUS_OPEN))
    if _old == new_status:
        return {"state": "noop", "changed": False, "status": _old}
    if new_status not in _LEGAL_TRANSITIONS.get(_old, ()):
        _module_logger.warning("[345流] 非法流转被拒: key=%s %s → %s", dedup_key, _old, new_status)
        return {"state": "illegal_transition", "changed": False, "from": _old}
    _hit["status"] = new_status
    _hist = _hit.get("status_history")
    if not isinstance(_hist, list):
        _hist = []
    _hist.append({"status": new_status, "at": _now, "note": str(note)[:200]})
    _hit["status_history"] = _hist
    if new_status in TERMINAL_STATUSES:
        _hit["closed_reason"] = str(note or "")[:200]
    _module_logger.info("[345流] %s → %s", dedup_key, new_status)

    _path = snapshot_path(_now, root, audit_dir_path)
    _payload = {
        "schema": SCHEMA,
        "snapshot_day": day_stamp(_now),
        "generated_at": _now,
        "retain_days": POOL_RETAIN_DAYS,
        "proc_gen": _proc_gen(),
        "total_entries": len(_entries),
        "status_counts": _status_counts(_entries),
        "prev_source": _pool.get("source", ""),
        "prev_state": "ok",
        "entries": _entries,
    }
    if not _atomic_write_json(_path, _payload):
        return {"state": "write_failed", "changed": False}
    return {"state": "ok", "changed": True, "from": _old, "to": new_status}

# ===== ★350刀1：验证回灌面（effect_verify → 池条目）=====

#: 回灌台账 schema（字段只增不改名）
VERIFY_FEEDBACK_SCHEMA = "audit_pool_verify_feedback/350.v1"


def _status_path(old: str, new: str, max_hops: int = 3):
    """求 `old→new` 的最短合法转移路径（BFS，直接用 `_LEGAL_TRANSITIONS`）。

    为什么要求路径而不是直接赋值：`claimed→verified` 在状态机上**不合法**
    （必须经 `fixed`）。直接改字段就绕过了状态机，等于把一个有校验的入口
    降级成不校验的写入口。BFS 也保证了"路径在就能走、路径不在就报
    unreachable"，不靠硬编码两条路。
    """
    if old == new:
        return []
    _seen = {old}
    _q = [(old, [])]
    while _q:
        _cur, _p = _q.pop(0)
        if len(_p) >= max_hops:
            continue
        for _nxt in _LEGAL_TRANSITIONS.get(_cur, ()):
            if _nxt == new:
                return _p + [_nxt]
            if _nxt not in _seen:
                _seen.add(_nxt)
                _q.append((_nxt, _p + [_nxt]))
    return None


@_m356_serialized
def note_verify_feedback(dedup_key: str, *, patch_id: str = "", passed=None,
                         verified_at=None, dims=None, reason: str = "",
                         advance: bool = True, root: str | None = None,
                         audit_dir_path: str | None = None,
                         now: float | None = None) -> dict:
    """★350刀1：把一条验证 verdict **写回池条目**（单次原子写）。

    写三件事：
      ① `verify_feedback` 字段（应用态/验证态/时间戳/dims）；
      ② 状态翻转（`passed=True → verified`；`False → open`；`None → **不动**）；
      ③ `status_history` 逐跳追加（跳过中间态也要留痕，否则看不出"它是怎么到这里的"）。

    `passed is None` 不改状态是有意的：三项都没测时「不可判定」，
    把它当 verified 写下去就是白送一个修复率。
    """
    _now = float(now if now is not None else time.time())
    _pool = load_pool(root, audit_dir_path, _now)
    if _pool["state"] != "ok":
        _module_logger.warning("[350回灌] 池不可用 state=%s key=%s",
                               _pool["state"], dedup_key)
        return {"state": _pool["state"], "changed": False}
    _entries = _pool["entries"]
    _hit = None
    for _e in _entries:
        if str(_e.get("dedup_key")) == str(dedup_key):
            _hit = _e
            break
    if _hit is None:
        return {"state": "not_found", "changed": False}
    _old = str(_hit.get("status", STATUS_OPEN))
    _want = (STATUS_VERIFIED if passed is True
             else (STATUS_OPEN if passed is False else None))
    _hops: list = []
    if advance and _want and _want != _old:
        _path = _status_path(_old, _want)
        if _path is None:
            _module_logger.warning("[350回灌] 状态不可达 key=%s %s→%s",
                                   dedup_key, _old, _want)
            return {"state": "unreachable", "changed": False, "from": _old}
        _hops = list(_path)
    _hist = _hit.get("status_history")
    if not isinstance(_hist, list):
        _hist = []
    for _st in _hops:
        _hist.append({"status": _st, "at": _now,
                      "note": "[350回灌] patch=%s passed=%r ⚡%s"
                              % (str(patch_id)[:40], passed, str(reason)[:120])})
    if _hops:
        _hit["status"] = _hops[-1]
        if _hops[-1] in TERMINAL_STATUSES:
            _hit["closed_reason"] = ("[350回灌] 效果验证通过 patch=%s"
                                     % str(patch_id)[:60])
    _hit["status_history"] = _hist
    # ★356刀1：验证通过 ⇒ `problem_fixed=verified` 与状态 VERIFIED **同事件双写**。
    #   此前只翻状态、不写 problem_fixed ⇒ 破零指标恒为 None（六段闭环第 5 段断点）。
    #   反过来 passed=False ⇒ problem_fixed=failed（失败回滚留证据，不静默）。
    if _want == STATUS_VERIFIED and str(_hit.get("status")) == STATUS_VERIFIED:
        _hit["problem_fixed"] = "verified"
        _hit["problem_fixed_reason"] = "356_effect_verify_passed"
        _hit["problem_fixed_partial"] = False
    elif _want == STATUS_OPEN and passed is False:
        _hit["problem_fixed"] = "failed"
        _hit["problem_fixed_reason"] = "356_effect_verify_failed"
    _hit["verify_feedback"] = {
        "schema": VERIFY_FEEDBACK_SCHEMA,
        "patch_id": str(patch_id or ""),
        "passed": passed,
        "verified_at": verified_at,
        "applied_at": _now,
        "dims": dims if isinstance(dims, dict) else {},
        "reason": str(reason or "")[:300],
        "status_from": _old,
        "status_to": _hit.get("status"),
    }
    _changed = bool(_hops) or True          # verify_feedback 本身也是内容变更
    _path_out = snapshot_path(_now, root, audit_dir_path)
    _payload = {
        "schema": SCHEMA,
        "snapshot_day": day_stamp(_now),
        "generated_at": _now,
        "retain_days": POOL_RETAIN_DAYS,
        "proc_gen": _proc_gen(),
        "total_entries": len(_entries),
        "status_counts": _status_counts(_entries),
        "prev_source": _pool.get("source", ""),
        "prev_state": "ok",
        "entries": _entries,
    }
    if not _atomic_write_json(_path_out, _payload):
        _module_logger.warning("[350回灌] 落盘失败 key=%s（内存已改、文件未写）",
                               dedup_key)
        return {"state": "write_failed", "changed": False, "from": _old}
    _module_logger.info("[350回灌] 条目 %s %s→%s patch=%s passed=%r",
                        dedup_key, _old, _hit.get("status"), str(patch_id)[:40], passed)
    return {"state": "noop" if not _hops else "ok", "changed": _changed,
            "from": _old, "to": _hit.get("status"), "hops": _hops,
            "persisted": True}



def pool_summary(root: str | None = None, audit_dir_path: str | None = None,
                 now: float | None = None) -> dict:
    """给报告/心跳用的只读摘要（不落盘）。"""
    _r = load_pool(root, audit_dir_path, now)
    if _r["state"] != "ok":
        return {"state": _r["state"], "total": 0 if _r["state"] == "empty" else None,
                "status_counts": {}, "source": ""}
    _types: dict[str, int] = {}
    _klasses: dict[str, int] = {}
    for _e in _r["entries"]:
        _t = str(_e.get("issue_type", "unknown"))
        _types[_t] = _types.get(_t, 0) + 1
        _k = str(_e.get("klass") or classify_klass(_t))
        _klasses[_k] = _klasses.get(_k, 0) + 1
    return {"state": "ok", "total": len(_r["entries"]),
            "status_counts": _status_counts(_r["entries"]),
            "issue_type_counts": _types, "klass_counts": _klasses,
            "source": _r.get("source", "")}


# ========== 刀2：回流分流（klass → 三类出口） ==========

ROUTE_PATCH_POOL = "patch_pool"
ROUTE_OPTIMIZE_LIST = "optimize_list"
ROUTE_CLEANUP_QUEUE = "cleanup_queue"

#: klass → 出口
_ROUTE_BY_KLASS = {
    KLASS_CODE_DEFECT: ROUTE_PATCH_POOL,
    KLASS_EXPERIENCE: ROUTE_OPTIMIZE_LIST,
    KLASS_POLLUTION: ROUTE_CLEANUP_QUEUE,
    # ★367 后续：趋势类**显式**进看板侧（原来靠 unknown 兜底，见 KLASS_TREND 注释）
    KLASS_TREND: ROUTE_OPTIMIZE_LIST,
    # klass 不明 ⇒ 人工看（不丢，也不假装知道该去哪）
    KLASS_UNKNOWN: ROUTE_OPTIMIZE_LIST,
}


def classify_route(issue_type: Any, severity_invalid: bool = False) -> str:
    """把一个 `issue_type` 分到三类出口之一（纯函数，可单测）。

    ★severity 非法值的条目**强制走清洗队列**（烛微345挂账59 的"进清洗队列"）：
      这类条目的统计口径本身不可信，混在补丁池里会误导"高=0 是否判据偏松"。
    """
    if severity_invalid:
        return ROUTE_CLEANUP_QUEUE
    return _ROUTE_BY_KLASS.get(classify_klass(issue_type), ROUTE_OPTIMIZE_LIST)


def split_routes(entries: list) -> dict:
    """把条目按三类出口分桶（刀2 接线函数）。

    ★入参可能是**审查报告的原生 issue**（字段名 `type`），也可能是**池条目**
    （字段名 `issue_type`）。两者都要认——只读 `issue_type` 会让全部 issue
    掉进兜底桶（本批实测踩过：5 条全判 optimize_list，route 全丢）。

    Returns: `{patch_pool: [...], optimize_list: [...], cleanup_queue: [...]}`，
    并给每条打 `route` 字段（原地）。非 dict 条目跳过（不猜）。
    """
    _out: dict[str, list] = {ROUTE_PATCH_POOL: [], ROUTE_OPTIMIZE_LIST: [],
                             ROUTE_CLEANUP_QUEUE: []}
    for _e in entries:
        if not isinstance(_e, dict):
            continue
        _t = _e.get("issue_type") or _e.get("type") or ""
        _route = classify_route(_t, bool(_e.get("severity_invalid")))
        _e["route"] = _route
        _out[_route].append(_e)
    return _out


#: 三类出口的落盘文件名（整体重写，可 grep）
_ROUTE_FILENAMES = {
    ROUTE_PATCH_POOL: "patch_pool.jsonl",
    ROUTE_OPTIMIZE_LIST: "optimize_list.jsonl",
    ROUTE_CLEANUP_QUEUE: "cleanup_queue.jsonl",
}


# ========== 刀2：hub 条目级数据源（烛微345待核2 裁定） ==========


def hub_entries(hub: Any = None) -> dict:
    """读 `verification_learning_hub` 的**条目级**记录（`_m335_strategies` 里的 strategies）。

    ★为什么不用"回流条数"：烛微345 实测回流是**每轮重复投递 15 条**（6×15=90），
      hub 在规则层按 issue_type 去重。把"投递成功 15 条"当成"新增 15 个问题"，
      是 15×每轮 的虚增。条目级记录带 `first_seen`，只有它才能回答"真正新发现多少"。

    Returns: `{state, entries, source, reason}`——
    `ok` 读到；`unavailable` hub 不可用；`empty` hub 在但没有任何条目（合法空）。
    """
    _hub = hub
    if _hub is None:
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            _hub = get_verification_learning_hub()
        except Exception as _e:
            silent_exc(_e, "audit_problem_pool.hub_entries 取 hub", level="debug")
            return {"state": "unavailable", "entries": {}, "source": "", "reason": "hub_import"}
    try:
        _d = _hub.get_m335_strategies()
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool.hub_entries 读盘账", level="warning")
        return {"state": "unavailable", "entries": {}, "source": "", "reason": "read_failed"}
    if not isinstance(_d, dict):
        return {"state": "unavailable", "entries": {}, "source": "", "reason": "bad_snapshot"}
    _str = _d.get("strategies")
    if not isinstance(_str, dict):
        return {"state": "empty", "entries": {}, "source": "", "reason": "no_strategies"}
    _entries = {k: v for k, v in _str.items() if isinstance(v, dict)}
    return {"state": "ok" if _entries else "empty", "entries": _entries,
            "source": str(_d.get("path", "") or ""), "reason": ""}


def hub_discovery_stats(since_ts: float, hub: Any = None,
                        rounds: int | None = None, reflow_per_round: int = 15) -> dict:
    """统计"窗口内真正新增的问题数"（= hub 条目里 `first_seen >= since_ts` 的条数）。

    同时给出**虚增对照**：若按"回流条数"计，同样这些轮次会得出
    `rounds × reflow_per_round` 条——两者差多少，就是烛微指出的虚增倍数。

    `rounds` 为 None 时不做虚增对照（不猜轮数）。
    """
    _p = hub_entries(hub)
    if _p["state"] == "unavailable":
        return {"state": "unavailable", "reason": _p.get("reason", ""),
                "new_by_first_seen": None, "reflow_equivalent": None}
    _all = _p["entries"]
    _new = 0
    _seen = 0
    for _v in _all.values():
        try:
            _fs = float(_v.get("first_seen") or 0)
        except (TypeError, ValueError) as _m360e:
            # ★360刀3：同上，另一处口径（新条目统计）
            note_drop("audit_problem_pool.new_since", "cast_failed", "%s" % _m360e)
            continue
        _seen += 1
        if _fs >= float(since_ts):
            _new += 1
    _reflow = (max(0, int(rounds)) * max(0, int(reflow_per_round))
               if rounds is not None else None)
    return {"state": _p["state"], "hub_entries_total": _seen,
            "new_by_first_seen": _new,
            "reflow_equivalent": _reflow,
            "inflation_ratio": (round(_reflow / _new, 2) if _reflow and _new else None),
            "source": _p.get("source", ""), "reason": _p.get("reason", "")}


def routes_from_hub(hub: Any = None) -> dict:
    """按 hub **条目级** issue_type 做三类分流（刀2 的数据源口径）。

    Returns: `{state, buckets: {route: [...]}, counts: {route: n}, source}`。
    hub 不可用 ⇒ `state="unavailable"`，**不拿空桶冒充"三类全 0"**（铁律156）。
    """
    _p = hub_entries(hub)
    if _p["state"] == "unavailable":
        return {"state": "unavailable", "buckets": {}, "counts": {}, "reason": _p.get("reason", "")}
    _buckets: dict[str, list] = {ROUTE_PATCH_POOL: [], ROUTE_OPTIMIZE_LIST: [],
                                 ROUTE_CLEANUP_QUEUE: []}
    for _k, _v in _p["entries"].items():
        _t = _v.get("issue_type") or _v.get("type") or ""
        _item = {
            "hub_key": _k,
            "issue_type": str(_t or ""),
            "rule": _v.get("rule"),
            "organ": _v.get("organ"),
            "tag": _v.get("tag"),
            "strategy": _v.get("strategy"),
            "first_seen": _v.get("first_seen"),
            "last_seen": _v.get("last_seen"),
            "hit_count": _v.get("hit_count"),
            "applied_total": _v.get("applied_total"),
            "route": classify_route(_t),
        }
        _buckets[_item["route"]].append(_item)
    return {"state": _p["state"], "buckets": _buckets,
            "counts": {k: len(v) for k, v in _buckets.items()},
            "source": _p.get("source", "")}


def export_hub_routes(hub: Any = None, root: str | None = None,
                      audit_dir_path: str | None = None) -> dict:
    """把 hub 条目级分流结果落成 `routes/hub_<name>.jsonl`（可 grep 的三类出口）。"""
    _r = routes_from_hub(hub)
    if _r["state"] == "unavailable":
        return {"state": "unavailable", "counts": {}, "reason": _r.get("reason", "")}
    _dir = os.path.join(audit_dir(root, audit_dir_path), "routes")
    _out: dict[str, dict] = {}
    try:
        os.makedirs(_dir, exist_ok=True)
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool.export_hub_routes 建目录", level="warning")
        return {"state": "write_failed", "counts": {}}
    for _route, _items in _r["buckets"].items():
        _path = os.path.join(_dir, "hub_" + _ROUTE_FILENAMES[_route])
        try:
            _tmp = _path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                for _it in _items:
                    _f.write(json.dumps(_it, ensure_ascii=False) + "\n")
            os.replace(_tmp, _path)
        except OSError as _e:
            silent_exc(_e, "audit_problem_pool.export_hub_routes 写", level="warning")
            continue
        _out[_route] = {"path": _path, "count": len(_items)}
    _module_logger.info("[345流] hub 条目级出口: 补丁池=%d 优化清单=%d 清洗队列=%d",
                        _r["counts"].get(ROUTE_PATCH_POOL, 0),
                        _r["counts"].get(ROUTE_OPTIMIZE_LIST, 0),
                        _r["counts"].get(ROUTE_CLEANUP_QUEUE, 0))
    return {"state": _r["state"], "routes": _out, "counts": _r["counts"]}


#: 三种趋势的 issue_type（看板固定观察面；与 _KLASS_TABLE["trend"] 同源，
#: 在此另列一份是为了**只读出口不依赖族表可变结构**——族表将来加成员不该
#: 悄悄改变看板的口径）
TREND_ISSUE_TYPES = ("cc_ge50_new", "code_loc_weekly_growth", "silent_except_weekly_rise")


def trend_board_snapshot(root: str | None = None,
                         audit_dir_path: str | None = None) -> dict:
    """趋势三种的看板只读视图（T-死代码放大器修复-1 第二件事的读口）。

    数据源：`<audit_dir>/routes/optimize_list.jsonl`（趋势类显式路由到此）。

    ★三态不伪装（铁律 166：不同事实不同措辞）：
      · 文件不存在 ⇒ `state="absent"`、`total=None`；
      · 文件读不了 ⇒ `state="unreadable"`、`total=None`；
      · 读到了但一条都没有 ⇒ `state="empty"`、`total=0`。
      「没有这个文件」与「文件里 0 条」是两件不同的事，绝不合并成 0。

    Returns:
        {state, path, total, trends: {issue_type: {count, latest_ts, open}},
         others, truncated_lines}
    """
    _p = os.path.join(audit_dir(root, audit_dir_path), "routes",
                      _ROUTE_FILENAMES[ROUTE_OPTIMIZE_LIST])
    _zero = {_t: {"count": None, "latest_ts": None, "open": None}
             for _t in TREND_ISSUE_TYPES}
    if not os.path.isfile(_p):
        return {"state": "absent", "path": _p, "total": None,
                "trends": _zero, "others": None, "truncated_lines": None}
    _rows = []
    _bad = 0
    try:
        with open(_p, encoding="utf-8") as _fh:
            for _ln in _fh:
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _rows.append(json.loads(_ln))
                except (ValueError, TypeError) as _je:
                    _bad += 1
                    if _bad == 1:
                        # ★只报首个坏行：看板出口每拍都读同一份文件，逐行报会刷屏；
                        #   坏行总数照常进返回值的 truncated_lines（不吞不弃）。
                        silent_exc(_je, where="audit_problem_pool.trend_board_snapshot 坏行",
                                   level="warning")
    except OSError as _e:
        silent_exc(_e, where="audit_problem_pool.trend_board_snapshot 读", level="warning")
        return {"state": "unreadable", "path": _p, "total": None,
                "trends": _zero, "others": None, "truncated_lines": None}

    _trends = {_t: {"count": 0, "latest_ts": None, "open": 0} for _t in TREND_ISSUE_TYPES}
    _others = 0
    for _r in _rows:
        _t = str(_r.get("issue_type") or _r.get("type") or "").strip()
        if _t not in _trends:
            _others += 1
            continue
        _cell = _trends[_t]
        _cell["count"] += 1
        if _is_open(_r):
            _cell["open"] += 1
        _ts = _first_ts(_r)
        if _ts is not None and (_cell["latest_ts"] is None or _ts > _cell["latest_ts"]):
            _cell["latest_ts"] = _ts
    return {"state": "ok" if _rows else "empty", "path": _p,
            "total": len(_rows), "trends": _trends, "others": _others,
            "truncated_lines": _bad}


def export_routes(entries: list, root: str | None = None,
                  audit_dir_path: str | None = None, now: float | None = None) -> dict:
    """把三类出口各自落成 `routes/<name>.jsonl`（整体重写）。

    **这是"出口实体"，不是"下游实现"**：具体接补丁池 / 优化清单 / 清洗队列的哪套
    代码，按任务书要以烛微 345 待核2 裁定为准，裁定前不做两套假设。
    本函数只保证「同一个池、三个可 grep 的出口」这件事成立。

    Returns: `{route: {"path":..., "count":...}}`；某类 0 条也写空文件
    （"出口存在但暂无条目"与"出口没接"必须可区分，铁律166）。
    """
    _bucket = split_routes(entries)
    _dir = os.path.join(audit_dir(root, audit_dir_path), "routes")
    _out: dict[str, dict] = {}
    try:
        os.makedirs(_dir, exist_ok=True)
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool.export_routes 建目录", level="warning")
        return {}
    for _route, _items in _bucket.items():
        _path = os.path.join(_dir, _ROUTE_FILENAMES[_route])
        try:
            _tmp = _path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                for _it in _items:
                    _f.write(json.dumps(_it, ensure_ascii=False) + "\n")
            os.replace(_tmp, _path)
        except OSError as _e:
            silent_exc(_e, "audit_problem_pool.export_routes 写出口", level="warning")
            continue
        _out[_route] = {"path": _path, "count": len(_items)}
    _module_logger.info("[345流] 出口导出: 补丁池=%d 优化清单=%d 清洗队列=%d",
                        _out.get(ROUTE_PATCH_POOL, {}).get("count", 0),
                        _out.get(ROUTE_OPTIMIZE_LIST, {}).get("count", 0),
                        _out.get(ROUTE_CLEANUP_QUEUE, {}).get("count", 0))
    return _out


# ===== ★348刀8：池指纹留痕（防再丢自证链） =====
def ledger_path(root: str | None = None, audit_dir_path: str | None = None) -> str:
    """台账路径（与快照同目录、不同文件——台账必须能"站在池外"看池）。"""
    return os.path.join(audit_dir(root, audit_dir_path), LEDGER_FILENAME)


def _fingerprint_of(path: str, payload: dict, now: float) -> dict:
    """算一条快照指纹。**纯函数**（不读盘）：读回校验由调用方负责，避免双读口径分叉。"""
    _entries = payload.get("entries") or []
    try:
        _raw = json.dumps(_entries, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as _je:
        # cw2：纯赋值也是静默（铁律176）——序列化不了说明条目里有不可 JSON 化的对象，
        #   指纹会退化成"只按条数算"，必须留痕（否则两条不同内容同条数 = 同指纹）
        silent_exc(_je, "audit_problem_pool._fingerprint_of json", level="debug")
        _raw = repr(len(_entries))
    return {
        "snapshot": os.path.basename(path),
        "entries_sha1_16": hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16],
        "total_entries": int(payload.get("total_entries") or 0),
        "snapshot_day": str(payload.get("snapshot_day") or ""),
        "written_at": float(now),
        "written_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(now))),
        "proc_gen": str(payload.get("proc_gen") or "unknown"),
        "code_version": str(payload.get("code_version") or "unknown"),
        "schema": str(payload.get("schema") or ""),
        # ★356刀3：台账补 `status_counts`——此前**根本没这个键**，读方 `.get()` 恒 None，
        #   "池里各状态各有多少"在台账层面完全不可见（星轨 356 前置列的原始现象）。
        "status_counts": _status_counts(_entries),
    }


def read_ledger(root: str | None = None, audit_dir_path: str | None = None) -> dict:
    """读台账。

    `state` 三态**必须分开**（铁律166）：
      - `absent`：台账从来不存在 ⇒ 首次运行，只做基线登记，**不告警**。
      - `unreadable`：台账存在但读不出来 ⇒ 这是**故障**，要告警（不可当 absent 处理，
        否则"台账被写坏"会被读成"第一次跑"，删除检测随之失效）。
      - `ok`：正常。
    """
    _p = ledger_path(root, audit_dir_path)
    if not os.path.isfile(_p):
        return {"state": "absent", "path": _p, "last_known_snapshot": None, "seen": {}}
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, "audit_problem_pool.read_ledger 台账不可读", level="warning")
        return {"state": "unreadable", "path": _p, "last_known_snapshot": None, "seen": {}}
    if not isinstance(_d, dict):
        return {"state": "unreadable", "path": _p, "last_known_snapshot": None, "seen": {}}
    if not isinstance(_d.get("seen"), dict):
        _d["seen"] = {}
    _d["state"] = "ok"
    _d["path"] = _p
    return _d


def note_pool_fingerprint(path: str, payload: dict, now: float | None = None,
                          root: str | None = None,
                          audit_dir_path: str | None = None) -> dict:
    """★348刀8①：persist 成功后把指纹写进独立台账。

    `last_known_snapshot` = 本拍指纹（最近一次"我知道池长什么样"）；
    `seen` = 按快照文件名的登记簿（首见/末见/写次/proc_gen），删除检测的分母；
    `history` = 近 `LEDGER_HISTORY_MAX` 条指纹流水。

    **首建基线**：台账 absent 时，先把磁盘上既有快照全部以 `baseline=True` 登记，
    否则第一次跑会把历史快照全报成"指纹缺失"（假告警，铁律179 同型）。
    """
    _now = float(now if now is not None else time.time())
    _fp = _fingerprint_of(path, payload, _now)
    _led = read_ledger(root, audit_dir_path)
    _first = _led["state"] == "absent"
    _seen = dict(_led.get("seen") or {})
    if _first:
        for _p in list_snapshots(root, audit_dir_path):
            _b = os.path.basename(_p)
            if _b != _fp["snapshot"] and _b not in _seen:
                try:
                    _sz = os.path.getsize(_p)
                except OSError as _se:
                    # cw2：纯赋值也是静默（铁律176）——基线快照量不到要留痕
                    silent_exc(_se, "audit_problem_pool.note_pool_fingerprint baseline size",
                               level="debug")
                    _sz = 0
                _seen[_b] = {"baseline": True, "bytes": int(_sz), "writes": 0,
                             "first_at": _now, "last_at": _now}
    _old = _seen.get(_fp["snapshot"]) or {}
    _seen[_fp["snapshot"]] = {
        "total_entries": _fp["total_entries"],
        "entries_sha1_16": _fp["entries_sha1_16"],
        "first_at": float(_old.get("first_at") or _fp["written_at"]),
        "last_at": _fp["written_at"],
        "last_proc_gen": _fp["proc_gen"],
        "writes": int(_old.get("writes") or 0) + 1,
    }
    _hist = list(_led.get("history") or [])
    _hist.append(_fp)
    if len(_hist) > LEDGER_HISTORY_MAX:
        _hist = _hist[-LEDGER_HISTORY_MAX:]
    _out = {
        "schema": LEDGER_SCHEMA,
        "updated_at": _now,
        "updated_at_str": _fp["written_at_str"],
        "last_known_snapshot": _fp,
        # ★356刀3：顶层镜像，方便"一眼看当前池分布"而不必钻指纹历史
        "status_counts": (_fp.get("status_counts") or {}),
        "seen": _seen,
        "history": _hist,
    }
    _ok = _atomic_write_json(ledger_path(root, audit_dir_path), _out)
    if not _ok:
        _module_logger.warning("[348池] 指纹台账写失败（本次不留痕，池仍已落盘）: %s",
                               _fp["snapshot"])
    return {"ok": _ok, "fingerprint": _fp, "seen_count": len(_seen),
            "baseline_initialized": bool(_first)}


def detect_pool_loss(root: str | None = None, audit_dir_path: str | None = None,
                     now: float | None = None,
                     current_path: str | None = None) -> dict:
    """★348刀8②：删除检测。两个方向**分开报**，不合并成一个"异常"。

    - `unregistered`：磁盘上有快照，台账里没有指纹 ⇒ 任务书原话「快照存在但指纹缺失」
      （= 台账被清 / 快照被外部写入 / 池曾以别的口径落盘）。
    - `missing`：台账里有指纹，磁盘上快照消失 ⇒ 真·被删除（这才是"再丢"）。

    两个方向的处置不同：前者说明**记账链断了**，后者说明**数据没了**。
    合并成一个数字，等于把两类事故读成同一类。

    调用纪律：必须在 `note_pool_fingerprint()` **之后**调（否则本拍快照会被自己报成
    未登记）。台账 `unreadable` 时**不猜正常**：直接以 `state="ledger_unreadable"` 报出，
    并给出 missing/unregistered 的"不可判"字样，不做 0/0 的假清白。
    """
    _now = float(now if now is not None else time.time())
    _led = read_ledger(root, audit_dir_path)
    _disk = set(os.path.basename(_p) for _p in list_snapshots(root, audit_dir_path))
    if current_path:
        _disk.add(os.path.basename(current_path))
    if _led["state"] == "unreadable":
        _module_logger.warning("[348池] 台账不可读，删除检测**不可判**（不当 0 处理）: %s",
                               _led.get("path", ""))
        return {"state": "ledger_unreadable", "alarms": 1, "unregistered": None,
                "missing": None, "checked_at": _now}
    _reg = set((_led.get("seen") or {}).keys())
    _unregistered = sorted(_disk - _reg)
    _missing = sorted(_reg - _disk)
    if _unregistered:
        _module_logger.warning(
            "[348池] 快照存在但指纹缺失 %d 个（记账链断裂/外部写入）: %s",
            len(_unregistered), ",".join(_unregistered[:5]))
    if _missing:
        _module_logger.warning(
            "[348池] 台账有指纹但快照已消失 %d 个（被删除/被清理）: %s",
            len(_missing), ",".join(_missing[:5]))
    return {"state": "ok", "alarms": len(_unregistered) + len(_missing),
            "unregistered": _unregistered, "missing": _missing,
            "registered": len(_reg), "on_disk": len(_disk), "checked_at": _now}
