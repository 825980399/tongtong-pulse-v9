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

import hashlib
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger(__name__)

#: 池积存目录（相对项目根）
AUDIT_DIR_REL = os.path.join("data", "reports", "audit")

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

ALL_STATUSES = (STATUS_OPEN, STATUS_CLAIMED, STATUS_FIXED, STATUS_VERIFIED, STATUS_WONTFIX)

#: 终态：进入后视为"结案"，可被超期 prune（烛微口径：status≠verified/wontfix 即未结案）
TERMINAL_STATUSES = (STATUS_VERIFIED, STATUS_WONTFIX)

#: 合法流转（非法流转只告警不写，不静默纠正）
_LEGAL_TRANSITIONS = {
    STATUS_OPEN: (STATUS_CLAIMED, STATUS_FIXED, STATUS_VERIFIED, STATUS_WONTFIX),
    STATUS_CLAIMED: (STATUS_FIXED, STATUS_OPEN, STATUS_WONTFIX),
    STATUS_FIXED: (STATUS_VERIFIED, STATUS_CLAIMED, STATUS_WONTFIX),
    STATUS_VERIFIED: (STATUS_OPEN, STATUS_WONTFIX),
    STATUS_WONTFIX: (STATUS_OPEN,),
}

#: severity 值域（烛微345挂账59）
SEVERITY_ALLOWED = ("low", "medium", "high")

#: severity 别名归一（只归一**语义相同**的写法；`ERROR` 这类**不归一**，
#: 它是非法值，要按非法值处置并留痕——归一掉就等于把问题抹了）
_SEVERITY_ALIASES = {"warning": "medium", "info": "low", "critical": "high"}

#: 单快照条目上限（防御：池无上限会把落盘本身变成新的内存/磁盘泄漏源）
MAX_POOL_ENTRIES = 5000

#: 池 schema 版本
SCHEMA = "audit_problem_pool/345.v2"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    """
    _raw = "%s|%s|%s" % (str(file or "").strip(), str(method or "").strip(),
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
}


def classify_klass(issue_type: Any) -> str:
    """把一个 issue_type 归到问题大类。先查字面量，再查族表，都不中 ⇒ unknown。"""
    _t = str(issue_type or "").strip().lower()
    if _t in (KLASS_CODE_DEFECT, KLASS_POLLUTION, KLASS_EXPERIENCE):
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
        silent_exc(_e, "audit_problem_pool._version_stamps")
        return {"criteria_version": None, "code_version": None}


def _proc_gen() -> str:
    """进程代次（唯一口 `proc_gen.proc_gen_str()`；取不到记 unknown，不填 0）。"""
    try:
        from nucleus.proc_gen import proc_gen_str
        return str(proc_gen_str() or "unknown")
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool._proc_gen")
        return "unknown"


# ========== 条目构造 ==========


def entry_from_issue(issue: dict, now: float | None = None,
                     stamps: dict | None = None, proc_gen: str | None = None) -> dict | None:
    """把一条 issue 转成池条目（16 字段口径）。非 dict 返回 None（不猜）。"""
    if not isinstance(issue, dict):
        return None
    _ts = float(now if now is not None else time.time())
    _st = stamps if isinstance(stamps, dict) else _version_stamps()
    _file = str(issue.get("file", "") or "")
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
        "desc": str(issue.get("message", "") or issue.get("description", "") or "")[:400],
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
    }
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


def _first_ts(entry: dict) -> float:
    """兼容 `first_seen_ts`（新口径）与 `first_seen_at`（旧快照）。"""
    for _k in ("first_seen_ts", "first_seen_at"):
        _v = entry.get(_k)
        if _v is not None:
            try:
                return float(_v)
            except (TypeError, ValueError):
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
    _kept, _pruned = prune(_merged, _now, retain_days)
    _truncated = 0
    if len(_kept) > MAX_POOL_ENTRIES:
        _kept.sort(key=lambda e: (0 if _is_open(e) else 1, -(_first_ts(e) or 0)))
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
        "[345池] persist: total=%d new=%d updated=%d pruned=%d sev_invalid=%d file=%s proc_gen=%s",
        len(_kept), _added, _updated, _pruned, len(_rejected),
        os.path.basename(_path), _pg)
    return {"state": "ok", "path": _path, "total": len(_kept), "added": _added,
            "updated": _updated, "pruned": _pruned, "truncated": _truncated,
            "severity_rejected": len(_rejected),
            "snapshot_day": day_stamp(_now), "prev_state": _prev["state"],
            "proc_gen": _pg}


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
            silent_exc(_e, "audit_problem_pool.hub_entries 取 hub")
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
        except (TypeError, ValueError):
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
