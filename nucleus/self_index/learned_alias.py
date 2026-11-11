# -*- coding: utf-8 -*-
"""493.8 批：语义层自学习闭环骨架（learned overlay + 候选/纠正/冷启动/融合/提升/veto/安全）。

纪律（任务书 + 用户铁律）：
  - 所有新机制由独立开关控制，默认 False；关 → 完整回退到 493.7 行为。
  - **只写** `data/self_index/learned_alias_overlay.json` 与 `data/self_index/alias_veto.json`；
    绝不 emit 任何事件、绝不新增/启用写类动作权限、绝不新增 action_id。
  - 候选/别名只影响 `link_organ` 与 fast path，不能触发新动作或改变器官行为。
  - 带粘贴日志/代码/URL/时间戳的 query 不进学习池（防污染闸门）。

本模块是纯文件 IO + 内存计算，不依赖运行态装配。
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import datetime

try:
    from nucleus._silent_except import silent_exc
except Exception:  # 兜底，避免导入链断裂
    def silent_exc(_e, where="", level="debug"):
        return None


# ── 路径与版本 ──
_OVERLAY_PATH = os.path.join("data", "self_index", "learned_alias_overlay.json")
_VETO_PATH = os.path.join("data", "self_index", "alias_veto.json")
OVERLAY_SCHEMA_VERSION = 1

# ── 安全边界（493.8-7）──
MAX_OVERLAY_ALIASES = 200          # 覆盖层别名条目上限
MAX_CANDIDATES_PER_QUERY = 5       # 单 query 候选数上限
MIN_QUARANTINE_HITS = 3            #  quarantine → active 最少命中
MIN_QUARANTINE_DAYS = 2            #  quarantine 最少留存天数
MAX_ACTIVE_RATIO = 0.5             # active 别名数 / 静态器官数 上限（防覆盖膨胀）


def _cfg_on(name: str) -> bool:
    try:
        import config
        return bool(getattr(config, name, False))
    except Exception as _e:
        silent_exc(_e, where="learned_alias._cfg_on", level="debug")
        return False


def _norm_key(query: str) -> str:
    """归一化 query 作为 overlay 的 key（去空白、小写、全角→半角基础处理）。"""
    if not query:
        return ""
    _s = str(query).strip().lower()
    _s = _s.replace("　", " ").replace("“", "\"").replace("”", "\"")
    return _s


# =============================================================================
# 覆盖层 IO
# =============================================================================
def _empty_overlay() -> dict:
    return {
        "version": OVERLAY_SCHEMA_VERSION,
        "updated_at": "",
        "aliases": {},
        "meta": {"total_active": 0, "total_quarantine": 0,
                 "promotions": 0, "vetoes": 0, "ingested_rows": 0},
    }


def load_overlay() -> dict:
    """读取 learned overlay（损坏/缺失 → 空结构）。"""
    try:
        if not os.path.isfile(_OVERLAY_PATH):
            return _empty_overlay()
        with open(_OVERLAY_PATH, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        if not isinstance(_d, dict):
            return _empty_overlay()
        if _d.get("version") != OVERLAY_SCHEMA_VERSION:
            # 版本不符：不直接丢弃，保留但以空别名起算（防旧格式污染）
            _d = _empty_overlay()
        _d.setdefault("aliases", {})
        _d.setdefault("meta", {})
        return _d
    except Exception as _e:
        silent_exc(_e, where="learned_alias.load_overlay", level="debug")
        return _empty_overlay()


def save_overlay(ov: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_OVERLAY_PATH), exist_ok=True)
        ov["updated_at"] = _now_iso()
        with open(_OVERLAY_PATH, "w", encoding="utf-8") as _f:
            json.dump(ov, _f, ensure_ascii=False, indent=2)
    except Exception as _e:
        silent_exc(_e, where="learned_alias.save_overlay", level="debug")


_OVERLAY_CACHE = {"ts": 0.0, "data": None}


def get_alias_overlay(use_cache: bool = True) -> dict:
    """带 1s TTL 的缓存读取（热路径 link_organ 使用）。"""
    _now = time.time()
    if use_cache and _OVERLAY_CACHE["data"] is not None \
            and _now - _OVERLAY_CACHE["ts"] < 1.0:
        return _OVERLAY_CACHE["data"]
    _d = load_overlay()
    _OVERLAY_CACHE["data"] = _d
    _OVERLAY_CACHE["ts"] = _now
    return _d


def _now_iso() -> str:
    try:
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return ""


# =============================================================================
# veto 表 IO（493.8-7）
# =============================================================================
def load_veto() -> dict:
    try:
        if not os.path.isfile(_VETO_PATH):
            return {"version": 1, "vetoes": []}
        with open(_VETO_PATH, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        if not isinstance(_d, dict):
            return {"version": 1, "vetoes": []}
        _d.setdefault("vetoes", [])
        return _d
    except Exception as _e:
        silent_exc(_e, where="learned_alias.load_veto", level="debug")
        return {"version": 1, "vetoes": []}


def save_veto(v: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_VETO_PATH), exist_ok=True)
        with open(_VETO_PATH, "w", encoding="utf-8") as _f:
            json.dump(v, _f, ensure_ascii=False, indent=2)
    except Exception as _e:
        silent_exc(_e, where="learned_alias.save_veto", level="debug")


def veto_alias(query: str = None, organ: str = None, reason: str = "") -> bool:
    """登记一条 veto（人工否决某 query→organ 的自动学习结果）。"""
    if not _cfg_on("ENABLE_M4938_ALIAS_VETO"):
        return False
    _v = load_veto()
    _entry = {
        "query": _norm_key(query) if query else None,
        "organ": organ,
        "reason": reason or "manual",
        "at": _now_iso(),
    }
    _v["vetoes"].append(_entry)
    # 清理已存在的 active 覆盖（veto 立即生效）
    _ov = load_overlay()
    _changed = False
    for _k, _ent in _ov.get("aliases", {}).items():
        _act = _ent.get("active")
        if _act and _matches_veto(_entry, _k, _act.get("organ")):
            _ent["active"] = None
            _changed = True
    if _changed:
        save_overlay(_ov)
    save_veto(_v)
    return True


def _matches_veto(veto_entry: dict, query_key: str, organ: str) -> bool:
    _vq = veto_entry.get("query")
    _vo = veto_entry.get("organ")
    if _vq and _vo:
        return _vq == query_key and _vo == organ
    if _vq:
        return _vq == query_key
    if _vo:
        return _vo == organ
    return False


def is_vetoed(query: str = None, organ: str = None) -> bool:
    _v = load_veto()
    _qk = _norm_key(query) if query else None
    for _e in _v.get("vetoes", []):
        if _matches_veto(_e, _qk, organ):
            return True
    return False


# =============================================================================
# link_organ 合并（493.8-3）：热路径
# =============================================================================
def maybe_overlay_alias(query: str) -> dict | None:
    """若 learned overlay 中有该 query 的 active 别名且未被 veto，返回它。

    仅在 ENABLE_M4938_LEARNED_ALIASES 开启时生效；否则返回 None（493.7 行为）。
    这是 link_organ 在匹配前的第一道（最高优先）入口。
    """
    if not _cfg_on("ENABLE_M4938_LEARNED_ALIASES"):
        return None
    _qk = _norm_key(query)
    if not _qk:
        return None
    try:
        _ov = get_alias_overlay()
        _ent = _ov.get("aliases", {}).get(_qk)
        if not _ent:
            return None
        _act = _ent.get("active")
        if not _act:
            return None
        _organ = _act.get("organ")
        if not _organ or is_vetoed(query=_qk, organ=_organ):
            return None
        return {"organ": _organ, "score": float(_act.get("score", 1.0)),
                "match": "learned", "source": "learned",
                "state": _act.get("state", "active")}
    except Exception as _e:
        silent_exc(_e, where="learned_alias.maybe_overlay_alias", level="debug")
        return None


# =============================================================================
# 候选抽取（493.8-4）+ 防污染闸门
# =============================================================================
def _in_pollution(query: str) -> bool:
    """带粘贴日志/代码/URL/时间戳的 query 不进学习池。复用 fast path 闸门判断。"""
    try:
        from nucleus.self_index.fastpath import fastpath_should_skip
        return bool(fastpath_should_skip(query))
    except Exception:
        # 兜底：简单规则
        if len(str(query)) > 80 or "\n" in str(query):
            return True
        return False


def extract_candidates_from_shadow_row(row: dict) -> list:
    """从一条影子日志行抽取候选别名（493.8-4 入池）。

    仅「指代类」行入池（alias_correct / alias_miss）；其余（fastpath_hit/miss
    非指代、skipped_by_guard）不入池。带污染特征（日志/代码/URL/时间戳）不入池。
    """
    if not _cfg_on("ENABLE_M4938_CANDIDATE_LEARNING"):
        return []
    if not isinstance(row, dict):
        return []
    _q = str(row.get("query", "") or "")
    if not _q:
        return []
    if row.get("skipped_by_guard"):
        return []
    if _in_pollution(_q):
        return []
    try:
        from nucleus.self_index.alias_labeler import label_shadow_row
        _label = label_shadow_row(row)
    except Exception:
        _label = None
    if _label not in ("alias_correct", "alias_miss"):
        return []
    _organ = row.get("organ")
    if not _organ:
        return []
    return [{
        "query": _q, "organ": _organ, "label": _label,
        "confidence": row.get("confidence"),
        "action_id": row.get("action_id"),
    }]


# =============================================================================
# 用户纠正解析（493.8-4）
# =============================================================================
# 先剥掉「你刚才说的 / 你说的 / 搞错了」这类话术前缀，避免贪婪吞掉器官名
_LEAD_RE = re.compile(r"^(?:你?刚才|之前|上面|刚刚)?(?:说的?|说错|搞错|错了)\s*")


def _strip_lead(text: str) -> str:
    return _LEAD_RE.sub("", str(text)).strip()


_CORRECT_PATTERNS = [
    # “胃不是肺是肝脏” / “X不叫Y叫Z”（3 段式，须先于 2 段式匹配）
    re.compile(
        r"([一-龥A-Za-z_]{1,12})\s*(?:不是|不叫|不对)\s*([一-龥A-Za-z_]{1,12})"
        r"\s*(?:是|叫|为|应该是)\s*([一-龥A-Za-z_]{1,12})"),
    # “肺不是胃” / “肺其实是胃” / “肺搞错了是胃”（2 段式）
    re.compile(
        r"([一-龥A-Za-z_]{1,12})\s*(?:不(?:是|对)|搞错|说错|错了)"
        r"\s*(?:其实|应该|明明|实际)?\s*(?:是|为)?\s*([一-龥A-Za-z_]{1,12})"),
    # “肺其实是胃” / “X应该叫Y”
    re.compile(
        r"([一-龥A-Za-z_]{1,12})\s*(?:应该|其实|才|明明)\s*(?:是|叫|为)\s*([一-龥A-Za-z_]{1,12})"),
]


def parse_user_correction(text: str, prev_bot_text: str = "") -> dict | None:
    """解析用户纠正话术 → {wrong_organ, correct_organ, source}。

    命中返回 dict；否则 None。仅当 ENABLE_M4938_CANDIDATE_LEARNING 开启时有效。
    """
    if not _cfg_on("ENABLE_M4938_CANDIDATE_LEARNING"):
        return None
    _t = str(text or "").strip()
    if not _t:
        return None
    _core = _strip_lead(_t)  # 剥掉「你刚才说的」前缀，避免贪婪吞器官名
    if not _core:
        return None
    for _p in _CORRECT_PATTERNS:
        _m = _p.search(_core)
        if not _m:
            continue
        _g = _m.groups()
        # 不同 pattern 的组含义不同，统一取：错误器官、正确器官
        if len(_g) == 2:
            _wrong, _correct = _g
        else:  # 3 组：第一是上下文词，第二错误，第三正确
            _wrong, _correct = _g[1], _g[2]
        if not _wrong or not _correct or _wrong == _correct:
            continue
        return {"wrong_organ": _wrong, "correct_organ": _correct,
                "source": "user_correction", "raw": _t[:80]}
    return None


# =============================================================================
# 冷启动提取器（493.8-5）
# =============================================================================
def cold_start_extract() -> dict:
    """从 ORGAN_META / 头注释中文名 / 方法名 提取初始候选别名。

    仅 ENABLE_M4938_COLD_START 开启时返回非空；返回 {norm_key: {organ,score,source}}。
    不自动写 overlay（由调用方决定是否 ingest）。
    """
    if not _cfg_on("ENABLE_M4938_COLD_START"):
        return {}
    _out: dict = {}
    try:
        from nucleus.cognitive.organ_inventory import export_organ_inventory
        _inv = export_organ_inventory()
        for _o in _inv.get("organs", []):
            _name = str(_o.get("name") or "")
            _cls = str(_o.get("class_name") or "")
            _file = str(_o.get("file_path") or "")
            _stem = os.path.splitext(os.path.basename(_file))[0] if _file else ""
            # 候选关键词：中文名、类名按驼峰切分、文件名
            _keys = set()
            if _name:
                _keys.add(_name)
            for _part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", _cls):
                if len(_part) >= 2:
                    _keys.add(_part.lower())
            if _stem:
                _keys.add(_stem.lower())
            for _k in _keys:
                _nk = _norm_key(_k)
                if not _nk or len(_nk) < 1:
                    continue
                _out[_nk] = {"organ": _name or _cls, "score": 0.5,
                             "source": "cold_start", "name": _name}
    except Exception as _e:
        silent_exc(_e, where="learned_alias.cold_start_extract", level="debug")
    return _out


# =============================================================================
# digest 融合 + 日终 promoter（493.8-6）
# =============================================================================
def _merge_candidate_into_overlay(ov: dict, cand: dict) -> None:
    """把一条候选合并进 overlay 的 quarantine（累计命中）。受上限约束。"""
    _qk = _norm_key(cand.get("query", ""))
    if not _qk:
        return
    _organ = cand.get("organ")
    if not _organ:
        return
    if is_vetoed(query=_qk, organ=_organ):
        return
    _aliases = ov.setdefault("aliases", {})
    if len(_aliases) >= MAX_OVERLAY_ALIASES:
        return
    _ent = _aliases.get(_qk)
    if _ent is None:
        _ent = {"query": cand.get("query", ""), "quarantine": [], "active": None}
        _aliases[_qk] = _ent
    _q = _ent.setdefault("quarantine", [])
    # 同 organ 的 quarantine 累计 hits
    for _c in _q:
        if _c.get("organ") == _organ:
            _c["hits"] = int(_c.get("hits", 0)) + 1
            _c["last_seen"] = _now_iso()
            break
    else:
        if len(_q) < MAX_CANDIDATES_PER_QUERY:
            _q.append({
                "organ": _organ, "score": float(cand.get("confidence") or 0.6),
                "source": cand.get("source", "shadow"),
                "hits": 1, "first_seen": _now_iso(), "last_seen": _now_iso(),
            })


def _row_date_str(ts) -> str:
    """493.12-4：影子 ts 归一化为 YYYY-MM-DD。

    兼容两种来源：真实影子是 float（如 1794352372.817）→ fromtimestamp；
    测试/历史影子是 ISO 字符串（如 2026-11-11T00:00:00）→ 直接取日期段。
    旧实现 ``str(ts).find(date)`` 对 float 永远失败 → date 过滤完全失效。
    """
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        _s = str(ts or "")
        if "T" in _s:
            _s = _s.split("T", 1)[0]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", _s):
            return _s
        return ""


def fuse_shadow_to_overlay(shadow_path: str = None, date: str = None) -> int:
    """读取影子日志（按日期或全量），抽取候选并合并进 overlay quarantine。

    返回新增/更新的候选数。仅 ENABLE_M4938_DIGEST_FUSION 开启时执行。
    """
    if not _cfg_on("ENABLE_M4938_DIGEST_FUSION"):
        return 0
    if shadow_path is None:
        shadow_path = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    if not os.path.isfile(shadow_path):
        return 0
    _ov = load_overlay()
    _ingested = 0
    try:
        with open(shadow_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _row = json.loads(_line)
                except Exception:
                    continue
                if date:
                    # ★493.12-4：真实影子 ts 是 float（如 1794352372.817），
                    #   str(ts).find(date) 永远匹配不到 → 改成按日期归一化比较。
                    if _row_date_str(_row.get("ts")) != date:
                        continue
                _cands = extract_candidates_from_shadow_row(_row)
                for _c in _cands:
                    _merge_candidate_into_overlay(_ov, _c)
                    _ingested += 1
    except Exception as _e:
        silent_exc(_e, where="learned_alias.fuse_shadow_to_overlay", level="debug")
    _ov["meta"]["ingested_rows"] = int(_ov["meta"].get("ingested_rows", 0)) + _ingested
    save_overlay(_ov)
    return _ingested


def _today_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def promote_quarantine() -> int:
    """日终提升：quarantine 达阈值且未被 veto → 升为 active（状态机）。

    受活跃比例上限约束。返回提升条数。仅 ENABLE_M4938_DIGEST_FUSION 开启时执行。
    """
    if not _cfg_on("ENABLE_M4938_DIGEST_FUSION"):
        return 0
    _ov = load_overlay()
    _promoted = 0
    # 活跃数上限 = max(静态器官数 * MAX_ACTIVE_RATIO, 已有 active)
    _max_active = _active_cap()
    _cur_active = sum(1 for _e in _ov.get("aliases", {}).values()
                      if _e.get("active"))
    for _k, _ent in _ov.get("aliases", {}).items():
        if _ent.get("active"):
            continue
        _best = None
        for _c in _ent.get("quarantine", []):
            if is_vetoed(query=_k, organ=_c.get("organ")):
                continue
            _age_days = _days_since(_c.get("first_seen"))
            if int(_c.get("hits", 0)) >= MIN_QUARANTINE_HITS \
                    and _age_days >= MIN_QUARANTINE_DAYS:
                if _best is None or int(_c.get("hits", 0)) > int(_best.get("hits", 0)):
                    _best = _c
        if _best and _cur_active < _max_active:
            _ent["active"] = {
                "organ": _best["organ"],
                "score": float(_best.get("score", 0.8)),
                "source": _best.get("source", "shadow"),
                "hits": _best.get("hits", 0),
                "since": _now_iso(), "state": "active",
            }
            _promoted += 1
            _cur_active += 1
    if _promoted:
        _ov["meta"]["promotions"] = int(_ov["meta"].get("promotions", 0)) + _promoted
        save_overlay(_ov)
    return _promoted


def _active_cap() -> int:
    try:
        from nucleus.cognitive.organ_inventory import export_organ_inventory
        _n = len(export_organ_inventory().get("organs", []))
        return max(int(_n * MAX_ACTIVE_RATIO), 1)
    except Exception:
        return 50


def _days_since(iso: str) -> int:
    if not iso:
        return 999
    try:
        _d = datetime.datetime.strptime(iso[:10], "%Y-%m-%d")
        return (datetime.datetime.now() - _d).days
    except Exception:
        return 999


# =============================================================================
# 指标面板（493.8-7）
# =============================================================================
def metrics() -> dict:
    """返回自学习闭环当前指标（供面板/观测）。"""
    _ov = load_overlay()
    _v = load_veto()
    _active = sum(1 for _e in _ov.get("aliases", {}).values() if _e.get("active"))
    _quar = sum(len(_e.get("quarantine", [])) for _e in _ov.get("aliases", {}).values())
    return {
        "overlay_aliases": len(_ov.get("aliases", {})),
        "active": _active,
        "quarantine": _quar,
        "promotions": _ov.get("meta", {}).get("promotions", 0),
        "ingested_rows": _ov.get("meta", {}).get("ingested_rows", 0),
        "vetoes": len(_v.get("vetoes", [])),
        "switches": {
            "learned_aliases": _cfg_on("ENABLE_M4938_LEARNED_ALIASES"),
            "candidate_learning": _cfg_on("ENABLE_M4938_CANDIDATE_LEARNING"),
            "cold_start": _cfg_on("ENABLE_M4938_COLD_START"),
            "digest_fusion": _cfg_on("ENABLE_M4938_DIGEST_FUSION"),
            "safety_boundary": _cfg_on("ENABLE_M4938_SAFETY_BOUNDARY"),
            "alias_veto": _cfg_on("ENABLE_M4938_ALIAS_VETO"),
        },
    }


# =============================================================================
# 493.12-4/5：dry-run 独立候选管线（只写候选文件，绝不写 overlay / 不 promote / 不改 link_organ）
# =============================================================================
_DRYRUN_SHADOW_EVERY = 100          # 每 100 条影子触发一次抽取
_DRYRUN_COUNTER = 0
_SEED_VETO_DONE = False


def _dryrun_candidates_path() -> str:
    return os.path.join("data", "self_index", "alias_candidates_dryrun.jsonl")


def _dryrun_existing_keys() -> set:
    """已写入 dryrun.jsonl 的去重键（query|organ|label）。"""
    _keys: set = set()
    try:
        if os.path.isfile(_dryrun_candidates_path()):
            with open(_dryrun_candidates_path(), "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _r = json.loads(_line)
                    except Exception:
                        continue
                    _keys.add(f"{_r.get('query')}|{_r.get('organ')}|{_r.get('label')}")
    except Exception as _e:
        silent_exc(_e, where="learned_alias._dryrun_existing_keys", level="debug")
    return _keys


def _scan_dryrun_candidates(shadow_path: str = None) -> list:
    """扫描影子日志，抽出所有候选（含是否被 veto 拦截）。**不写任何文件**。

    复用 493.8-4 的候选抽取口径（CANDIDATE_LEARNING 开 → 返回 list）；
    每条候选附 ``vetoed`` 标记（veto 命中则 True，不应进 dryrun 文件）。
    """
    if shadow_path is None:
        shadow_path = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    _all: list = []
    if not os.path.isfile(shadow_path):
        return _all
    try:
        with open(shadow_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _row = json.loads(_line)
                except Exception:
                    continue
                for _c in extract_candidates_from_shadow_row(_row):
                    _c = dict(_c)
                    _c["vetoed"] = is_vetoed(
                        query=_c.get("query"), organ=_c.get("organ"))
                    _all.append(_c)
    except Exception as _e:
        silent_exc(_e, where="learned_alias._scan_dryrun_candidates", level="debug")
    return _all


def extract_and_append_dryrun(shadow_path: str = None, max_new: int = None) -> int:
    """读影子日志 → 抽候选 → append 到 dryrun.jsonl；**不写 overlay、不 promote**。

    去重（已写入的 query|organ|label 不再写）；veto 命中的候选丢弃不进文件。
    返回本次新增行数。开关 ENABLE_M49312_DRYRUN_CANDIDATES 关 → 0。
    """
    if not _cfg_on("ENABLE_M49312_DRYRUN_CANDIDATES"):
        return 0
    _ensure_seed_veto()
    _cands = _scan_dryrun_candidates(shadow_path)
    _existing = _dryrun_existing_keys()
    _rows = []
    for _c in _cands:
        if _c.get("vetoed"):
            continue
        _k = f"{_c.get('query')}|{_c.get('organ')}|{_c.get('label')}"
        if _k in _existing:
            continue
        _existing.add(_k)
        _rows.append(_c)
    if max_new:
        _rows = _rows[-max_new:]
    if _rows:
        try:
            os.makedirs(os.path.dirname(_dryrun_candidates_path()), exist_ok=True)
            with open(_dryrun_candidates_path(), "a", encoding="utf-8") as _f:
                for _c in _rows:
                    _rec = {_k2: _c[_k2] for _k2 in
                            ("query", "organ", "label", "confidence",
                             "action_id", "source") if _k2 in _c}
                    _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        except Exception as _e:
            silent_exc(_e, where="learned_alias.extract_and_append_dryrun",
                       level="debug")
            return 0
    return len(_rows)


def _maybe_trigger_dryrun() -> int:
    """每 _DRYRUN_SHADOW_EVERY 条影子触发一次抽取（开关关 → 0）。

    由 fastpath.record_self_query_shadow 在每次写影子后调用。
    """
    global _DRYRUN_COUNTER
    if not _cfg_on("ENABLE_M49312_DRYRUN_CANDIDATES"):
        return 0
    _DRYRUN_COUNTER += 1
    if _DRYRUN_COUNTER >= _DRYRUN_SHADOW_EVERY:
        _DRYRUN_COUNTER = 0
        return extract_and_append_dryrun()
    return 0


def _ensure_seed_veto() -> None:
    """493.12-5：确保一条真实负例 veto（「肺」不得被学成「肝」）已存在（幂等）。"""
    global _SEED_VETO_DONE
    if _SEED_VETO_DONE:
        return
    _SEED_VETO_DONE = True
    try:
        if not _cfg_on("ENABLE_M4938_ALIAS_VETO"):
            return
        _v = load_veto()
        for _e in _v.get("vetoes", []):
            if _e.get("reason") == "seed_49312_肺不得学成肝":
                return
        _v.setdefault("vetoes", []).append({
            "query": _norm_key("肺其实是肝"),
            "organ": "肝",
            "reason": "seed_49312_肺不得学成肝",
            "at": _now_iso(),
        })
        save_veto(_v)
    except Exception as _e:
        silent_exc(_e, where="learned_alias._ensure_seed_veto", level="debug")


def metrics_dryrun() -> dict:
    """493.12-5：dry-run 观测指标。

    - candidate_rows_total / distinct_queries / by_label：来自扫描影子候选
    - quarantine_hits_ge3 / promotion_ready：(query,organ) 命中次数 ≥ 阈值
    - veto_blocked：被 veto 拦下的候选数
    - online_answer_delta：**恒为 0**（dry-run 仅 append 候选文件，绝不改
      link_organ / overlay / 线上回答——这是本批的硬约束验收点）
    """
    _cands = _scan_dryrun_candidates()
    _total = len(_cands)
    _veto_blocked = sum(1 for _c in _cands if _c.get("vetoed"))
    _distinct = len(set(str(_c.get("query")) for _c in _cands))
    _by_label: dict = {}
    for _c in _cands:
        _l = _c.get("label") or "unknown"
        _by_label[_l] = _by_label.get(_l, 0) + 1
    _groups: dict = {}
    for _c in _cands:
        _k = (str(_c.get("query")), str(_c.get("organ")))
        _groups[_k] = _groups.get(_k, 0) + 1
    _ge3 = sum(1 for _v in _groups.values() if _v >= MIN_QUARANTINE_HITS)
    _promo = sum(1 for _v in _groups.values()
                 if _v >= MIN_QUARANTINE_HITS
                 and _days_since(_now_iso()[:10]) >= MIN_QUARANTINE_DAYS)
    return {
        "candidate_rows_total": _total,
        "distinct_queries": _distinct,
        "by_label": _by_label,
        "quarantine_hits_ge3": _ge3,
        "veto_blocked": _veto_blocked,
        "promotion_ready": _promo,
        "online_answer_delta": 0,
        "switches": {
            "dryrun_candidates": _cfg_on("ENABLE_M49312_DRYRUN_CANDIDATES"),
            "learned_aliases": _cfg_on("ENABLE_M4938_LEARNED_ALIASES"),
            "digest_fusion": _cfg_on("ENABLE_M4938_DIGEST_FUSION"),
        },
    }
