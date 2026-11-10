# -*- coding: utf-8 -*-
"""493批刀2/3/4：Self-Index 三个导出器（只读，数据全部动态读取，不硬编码）。

  · export_event_subscribers()  -> 事件↔订阅者关系（493-2）
  · export_organs_runtime()     -> 器官运行态（493-3）
  · export_intent_map()         -> 意图→方法映射（493-4）

设计原则（任务书 §纪律 + 烛微前置分析 §11.8）：
  * 纯只读，不修改任何现有器官逻辑 / 应答路径；
  * 所有字段从运行态 / 代码 / 静态表动态读取，不在此硬编码实体；
  * 每个导出都带 `generated_at` 时间戳字段（纪律 §2「加时间戳」）；
  * 任一数据源不可用时降级为空 / None，**用 silent_exc 合规记录**，保证下游可解析。
"""
from __future__ import annotations

import json
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.self_index.schema import SELF_INDEX_VERSION, _now_str

# 本地处理 vs 需调大模型：rule_reason/health_check 为纯本地；其余走 LLM / 混合
_LOCAL_METHODS = frozenset({"rule_reason", "health_check"})


def _self_index_on() -> bool:
    """493-1 总开关（fail-closed：读不到 config 时默认开）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M493_SELF_INDEX", True))
    except Exception as _e:
        silent_exc(_e, where="exporters._self_index_on", level="debug")
        return True


def _read_health_snapshot_raw() -> dict:
    """读 health_snapshot.json 原始 dict（用于取 event_fanout 等透传字段）。"""
    _p = os.path.join("data", "monitor", "health_snapshot.json")
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            return json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="exporters._read_health_snapshot_raw", level="debug")
        return {}


def _parse_ts(ts) -> float | None:
    """解析 intent_samples 的 `YYYY-MM-DD HH:MM:SS` 时间戳为 epoch。"""
    if not ts or not isinstance(ts, str):
        return None
    for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(ts, _fmt))
        except (ValueError, OverflowError) as _e:
            silent_exc(_e, where="exporters._parse_ts", level="debug")
            continue
    return None


# ───────────────────────── 493-2 事件↔订阅者关系 ─────────────────────────
def export_event_subscribers() -> dict:
    """从 InfoField 条件表 + 事件账本导出每个事件的完整订阅者列表。

    返回：
        {"generated_at", "version", "event_count",
         "events": {event_name: [{"organ","handler","avg_latency_ms"}]},
         "fanout": {event_name: 当前扇出(current)}}
    InfoField 单例不可用时降级为 {'events': {}, 'fanout': {}}。
    """
    _events: dict[str, list[dict]] = {}
    _fanout: dict[str, float] = {}
    try:
        _raw = _read_health_snapshot_raw()
        _ef = (_raw.get("info_field") or {}).get("event_fanout") or {}
        for _ev_name, _v in _ef.items():
            if isinstance(_v, dict):
                _fanout[_ev_name] = _v.get("current")
            else:
                _fanout[_ev_name] = _v
        from nucleus.field.InfoField import get_info_field
        _field = get_info_field()
        if _field is not None:
            _conds = getattr(_field, "_conditions", {}) or {}
            for _cid, _c in _conds.items():
                if not isinstance(_c, dict):
                    continue
                _cond = _c.get("condition", {}) or {}
                _ets = _cond.get("event_types") or []
                _organ = _cond.get("organ_name", "unknown")
                _handler = _c.get("handler")
                _handler_name = (getattr(_handler, "__name__", None)
                                 or (str(_handler) if _handler else None))
                for _et in _ets:
                    if not _et:
                        continue
                    _events.setdefault(_et, []).append({
                        "organ": _organ,
                        "handler": _handler_name,
                        # 事件账本无延迟埋点，诚实标 null（待 493-8 调用图补齐）
                        "avg_latency_ms": None,
                    })
    except Exception as _e:
        silent_exc(_e, where="exporters.export_event_subscribers", level="debug")
    return {
        "generated_at": _now_str(),
        "version": SELF_INDEX_VERSION,
        "event_count": len(_events),
        "events": _events,
        "fanout": _fanout,
    }


# ───────────────────────── 493-3 器官运行态 ─────────────────────────
def export_organs_runtime() -> dict:
    """从 organ_inventory + health_snapshot 导出每个器官的实时状态。

    493.5-4 增强：开关 ENABLE_M4935_RUNTIME_FILL 开时，按器官补全
    inflight（InfoField.get_organ_inflight）/ success_rate+avg_latency_ms
    （InfoField.get_organ_handler_stats 聚合 489 数据）/ 顶层 last_channel
    （InfoField.get_last_channel）；关时诚实标 null（符合「查不到就标 null」）。
    """
    import config as _cfg
    _fill = bool(getattr(_cfg, "ENABLE_M4935_RUNTIME_FILL", True))
    _organs_out: list[dict] = []
    try:
        from nucleus.cognitive.organ_inventory import export_organ_inventory
        _inv = export_organ_inventory() or {}
    except Exception as _e:
        silent_exc(_e, where="exporters.export_organs_runtime:inventory", level="debug")
        _inv = {}
    _items = (_inv.get("organs") or []) if isinstance(_inv, dict) else []

    # 运行态指标：内存按器官、队列深度按层
    _mem_by_organ: dict[str, float] = {}
    _layer_qdepth: dict[str, int] = {}
    try:
        from nucleus.world_model.external_heartbeat import read_health_snapshot
        _snap = read_health_snapshot() or {}
        _mp = _snap.get("memory_pressure") or {}
        _mbo = _mp.get("memory_by_organ") or {}
        for _o in (_mbo.get("organs") or []):
            if isinstance(_o, dict) and _o.get("organ"):
                _mem_by_organ[_o["organ"]] = _o.get("est_mb")
        _bp = _snap.get("backpressure") or {}
        if isinstance(_bp, dict):
            for _layer, _v in _bp.items():
                if isinstance(_v, dict):
                    _layer_qdepth[_layer] = _v.get("queue_depth")
    except Exception as _e:
        silent_exc(_e, where="exporters.export_organs_runtime:snapshot", level="debug")

    # 493.5-4：运行态埋点数据源（InfoField 单例；未运行态/无单例时降级）
    _if = None
    if _fill:
        try:
            from nucleus.field.InfoField import get_info_field
            _if = get_info_field()
        except Exception as _e:
            silent_exc(_e, where="exporters.export_organs_runtime:field", level="debug")

    for _it in _items:
        if not isinstance(_it, dict):
            continue
        _name = _it.get("name")
        _is_brain = bool(_it.get("is_brain_organ"))
        _layer = "L3" if _is_brain else "L2"
        # 493.5-4：按器官埋点（仅在开关开 + 单例就绪时填充）
        _inflight = None
        _srate = None
        _lat = None
        if _if is not None:
            try:
                _inflight = _if.get_organ_inflight(_name)
                _hs = _if.get_organ_handler_stats(_name)
                _srate = _hs.get("success_rate")
                _lat = _hs.get("avg_latency_ms")
            except Exception as _e:
                silent_exc(_e, where="exporters.export_organs_runtime:fill", level="debug")
        _organs_out.append({
            "name": _name,
            "file": _it.get("file_path"),          # 代码路径（行号待 493-8 CallGraphAnalyzer 补齐）
            "type": _it.get("type"),
            "is_core": _it.get("is_core"),
            "is_brain_organ": _it.get("is_brain_organ"),
            "health": _it.get("health"),
            "method_count": _it.get("method_count"),
            "memory_est_mb": _mem_by_organ.get(_name),
            "queue_depth": _layer_qdepth.get(_layer),
            "thread_count": None,                   # 全局线程数非按器官，诚实标 null
            "inflight": _inflight,                  # 493.5-4
            "success_rate": _srate,                 # 493.5-4（关时 null）
            "avg_latency_ms": _lat,                 # 493.5-4（关时 null）
        })
    # 493.5-4：顶层最近应答渠道
    _last_ch = None
    if _if is not None:
        try:
            _last_ch = _if.get_last_channel()
        except Exception as _e:
            silent_exc(_e, where="exporters.export_organs_runtime:last", level="debug")
    return {
        "generated_at": _now_str(),
        "version": SELF_INDEX_VERSION,
        "organ_count": len(_organs_out),
        "last_channel": _last_ch,                   # 493.5-4
        "organs": _organs_out,
    }


# ───────────────────────── 493-4 意图→方法映射 ─────────────────────────
def export_intent_map() -> dict:
    """从 QICA INTENT_TO_METHOD + intent_samples.jsonl 导出意图映射。

    返回：
        {"generated_at","version","intent_count",
         "intents":[{intent,method,local,sample_count_7d,
                     sample_count_all,examples,avg_latency_ms}]}
    注：样本无延迟字段，avg_latency_ms 诚实标 null；local=True 表示纯本地处理。
    """
    try:
        from nucleus.qica.IntentChannels import INTENT_TO_METHOD
        _im = dict(INTENT_TO_METHOD)
    except Exception as _e:
        silent_exc(_e, where="exporters.export_intent_map:import", level="debug")
        _im = {}

    # 样本统计：按 qica_intent_type 分组，统计近7天量 + 取示例问句
    _by_intent: dict[str, dict] = {}
    _cut = time.time() - 7 * 86400
    try:
        _p = os.path.join("data", "eval", "intent_samples.jsonl")
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _r = json.loads(_line)
                    except (ValueError, TypeError) as _e:
                        silent_exc(_e, where="exporters.export_intent_map:json", level="debug")
                        continue
                    _int = _r.get("qica_intent_type") or ""
                    _rec = _by_intent.setdefault(
                        _int, {"count_7d": 0, "count_all": 0, "examples": []})
                    _rec["count_all"] += 1
                    _t = _parse_ts(_r.get("ts"))
                    if _t is not None and _t >= _cut:
                        _rec["count_7d"] += 1
                    if len(_rec["examples"]) < 3 and _r.get("query"):
                        _rec["examples"].append(_r["query"])
    except Exception as _e:
        silent_exc(_e, where="exporters.export_intent_map:samples", level="debug")

    _intents_out = []
    for _intent, _method in _im.items():
        _s = _by_intent.get(_intent, {})
        _intents_out.append({
            "intent": _intent,
            "method": _method,
            "local": _method in _LOCAL_METHODS,
            "sample_count_7d": _s.get("count_7d", 0),
            "sample_count_all": _s.get("count_all", 0),
            "examples": _s.get("examples", []),
            "avg_latency_ms": None,               # 样本无延迟字段，诚实标 null
        })
    return {
        "generated_at": _now_str(),
        "version": SELF_INDEX_VERSION,
        "intent_count": len(_intents_out),
        "intents": _intents_out,
    }


# ───────────────────────── 连接关系推导（body_relations） ─────────────────────────
def derive_body_relations(event_subscribers: dict) -> list[dict]:
    """从事件订阅者反推「谁订阅谁」连接关系（493-1 schema 的 body_relations）。

    「谁调用谁」需调用图（CallGraphAnalyzer，493-8 范围），本批暂无数据源 ⇒ 留空。
    返回 [{from(订阅器官), relation:"subscribes", event}]。
    """
    _rel: list[dict] = []
    if not isinstance(event_subscribers, dict):
        return _rel
    _events = event_subscribers.get("events", {}) or {}
    for _ev, _subs in _events.items():
        if not isinstance(_subs, list):
            continue
        for _s in _subs:
            if isinstance(_s, dict) and _s.get("organ"):
                _rel.append({
                    "from": _s["organ"],
                    "relation": "subscribes",
                    "event": _ev,
                })
    return _rel


# ───────────────────────── 493.5-2 指称链接（自然语言 → 器官） ─────────────────────────
# 57 器官别名表（来源：烛微 493 第二阶段前置分析 §2.2 全量逐行核对；一个不漏）。
# 性质：策展静态表（非代码动态推导）。任务书纪律明令禁止「硬猜别名映射」，
# 故直接采用烛微核实后的清单；后续由样本增长补充，不在本批自动生成。
ORGAN_ALIAS_TABLE: dict[str, list[str]] = {
    "血管": ["血管", "血液循环", "血流", "循环监测"],
    "心脏": ["心脏", "心", "心跳", "心率", "起搏", "heart"],
    "肾": ["肾", "肾脏", "过滤", "排泄", "清理"],
    "肝": ["肝", "肝脏", "代谢", "融合", "知识代谢"],
    "肺": ["肺", "肺部", "呼吸", "语言模型", "外脑", "渠道"],
    "胃": ["胃", "消化", "消化器官", "吃进", "知识消化"],
    "代码学习": ["代码学习", "学代码", "读代码", "代码理解"],
    "大脑皮层": ["皮层", "大脑", "思考", "对话主链", "cortex"],
    "主动交互": ["主动", "主动说话", "发起对话", "initiative"],
    "内在世界": ["内在世界", "内心", "沉思", "推理", "思考"],
    "兴趣模型": ["兴趣", "爱好", "好奇"],
    "前额叶": ["前额叶", "反思", "复盘"],
    "风险感知": ["风险", "危险", "谨慎"],
    "语义理解器": ["语义", "理解", "意图", "QICA"],
    "精神核心": ["精神", "核心", "意志"],
    "潜意识": ["潜意识", "直觉", "灵感", "取种"],
    "设备管理器": ["设备", "硬件管理"],
    "紧急处理": ["紧急", "急救", "告警处理"],
    "能量代谢": ["能量", "代谢", "体力"],
    "全局学习器": ["全局学习", "学习"],
    "硬件启动器": ["启动", "开机"],
    "健康监控": ["健康", "体检", "监控"],
    "推理引擎": ["推理引擎", "推理"],
    "指标采集器": ["指标", "采集", "metrics", "监控数据"],
    "动机循环": ["动机", "驱动"],
    "本体感知": ["本体", "身体感知", "proprioception"],
    "脊髓": ["脊髓", "反射", "巡检"],
    "应激轴": ["应激", "压力", "应急"],
    "系统管理器": ["系统管理", "框架管理", "总控"],
    "激素": ["激素", "情绪", "心情", "emotion"],
    "神经递质": ["神经递质", "神经", "信号"],
    "情感羁绊": ["羁绊", "亲密", "关系"],
    "共同决策": ["共同决策", "协商", "同意"],
    "DNA修复": ["DNA", "修复", "自愈"],
    "进化": ["进化", "演化", "升级"],
    "养育": ["养育", "培养"],
    "生育伦理": ["生育", "伦理审查"],
    "伦理": ["伦理", "道德", "价值观"],
    "成长": ["成长", "进步"],
    "叙事自我": ["叙事", "故事", "自传", "记忆线"],
    "人格内核": ["人格", "性格", "人设"],
    "自我认知": ["自我认知", "自我意识", "自省"],
    "宪法守护": ["宪法", "规则", "底线", "守护"],
    "骨髓": ["骨髓", "造血"],
    "皮肤": ["皮肤", "屏障", "边界"],
    "胸腺": ["胸腺", "免疫训练"],
    "白细胞": ["白细胞", "免疫", "杀毒", "异常检测"],
    "代码沙箱": ["沙箱", "代码执行", "安全执行"],
    "控制器": ["控制器", "动作", "执行", "手"],
    "文件消化器": ["文件", "文档", "解析"],
    "双手": ["双手", "手", "操作"],
    "双腿": ["双腿", "腿", "探索"],
    "嘴巴": ["嘴", "嘴巴", "说话", "输出", "TTS"],
    "耳朵": ["耳朵", "听", "语音输入"],
    "眼睛": ["眼睛", "看", "视觉", "摄像头"],
    "触觉": ["触觉", "硬件", "传感器"],
    "视觉皮层": ["视觉皮层", "识图", "OCR"],
}

# 归一化去除的语气 / 指代词（保留语义字）
_STOP = set("的了我那个这各个里是叫哪谁你他它她管负责地方有在怎么什么哪吗呢吧啊呀哦")


def _normalize(q: str) -> str:
    import re
    q = (q or "").strip().lower()
    q = re.sub(r"[\s\W]+", "", q)          # 去空白与标点（中文属 \w 保留）
    return "".join(ch for ch in q if ch not in _STOP)


def _char_ngrams(s: str, n: int = 2) -> dict:
    """字符 unigram + bigram 向量（无依赖、<1ms 级）。"""
    s = s.lower()
    grams: dict[str, int] = {}
    for i in range(len(s)):
        grams[s[i]] = grams.get(s[i], 0) + 1
        if i + 1 < len(s):
            grams[s[i:i + 2]] = grams.get(s[i:i + 2], 0) + 1
    return grams


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    for k, v in a.items():
        if k in b:
            dot += v * b[k]
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def link_organ(query: str) -> dict | None:
    """自然语言指称 → 器官（493.5-2 核心）。

    返回：
      {"organ": name, "score": float, "match": "exact"|"fuzzy"}  命中
      {"candidates": [{"organ":name,"score":float}, ...]}           歧义（需澄清）
      None                                                                无候选（走大模型）
    算法（<3ms，本地无依赖）：① 精确别名子串命中；② 字符 n-gram 余弦模糊
      （top1≥0.72 且 top1-top2≥0.10 才返回；分差不足→澄清；都低→None）。
    """
    q = _normalize(query)
    if not q:
        return None
    # ① 精确别名命中
    hits: list[str] = []
    for _organ, _als in ORGAN_ALIAS_TABLE.items():
        for _al in _als:
            if _al and (_al.lower() in q or q in _al.lower()):
                hits.append(_organ)
                break
    if len(hits) == 1:
        return {"organ": hits[0], "score": 1.0, "match": "exact"}
    if len(hits) > 1:
        return {"candidates": [{"organ": _h, "score": 1.0} for _h in hits[:3]]}
    # ② 模糊 n-gram 余弦
    _qv = _char_ngrams(q)
    _scored: list[tuple[str, float]] = []
    for _organ, _als in ORGAN_ALIAS_TABLE.items():
        _best = 0.0
        for _al in _als:
            _sc = _cosine(_qv, _char_ngrams(_al))
            if _sc > _best:
                _best = _sc
        if _best > 0:
            _scored.append((_organ, _best))
    _scored.sort(key=lambda x: -x[1])
    if not _scored:
        return None
    _t1, _s1 = _scored[0]
    _t2, _s2 = _scored[1] if len(_scored) > 1 else (None, 0.0)
    if _s1 >= 0.72 and (_s1 - _s2) >= 0.10:
        return {"organ": _t1, "score": round(_s1, 3), "match": "fuzzy"}
    if _s1 < 0.72:
        return None
    return {"candidates": [{"organ": _o, "score": round(_s, 3)} for _o, _s in _scored[:3]]}


def export_organ_aliases() -> dict:
    """导出指称链接表（organ_aliases.json，含 generated_at 时间戳）。"""
    return {
        "generated_at": _now_str(),
        "version": "493.5.alias.v1",
        "organ_count": len(ORGAN_ALIAS_TABLE),
        "aliases": ORGAN_ALIAS_TABLE,
    }


# ───────────────────────── 493.5-3 自我动作库白名单 ─────────────────────────
# 来源：烛微 493 第二阶段前置分析 §3.4（策展白名单，**非**自动生成 594 个 public）。
# 纪律：写类（patch.apply/config.set/file.write）本阶段只登记不执行 → status="blocked"。
# 行号以实际文件为准（修正烛微表笔误：channel.stats 1408→1840、lung.dialog_history 1449→1882）。
ACTION_WHITELIST: list[dict] = [
    # ── 只读（可直接本地执行） ──
    {"action_id": "self.count_organs", "organ": "Self-Index", "kind": "read",
     "impl": {"type": "self_index", "target": "SelfIndexStore.build", "file": "nucleus/self_index/store.py", "line": 0},
     "params": {}, "preconditions": ["self_index_on"], "risk": 0, "idempotent": True,
     "verify": {"type": "int_gt", "key": "organ_count", "gt": 0}, "source": "curated", "status": "ready"},
    {"action_id": "organ.locate", "organ": "Self-Index", "kind": "read",
     "impl": {"type": "self_index", "target": "export_organ_aliases", "file": "nucleus/self_index/exporters.py", "line": 0},
     "params": {"name": "器官名/别名"}, "preconditions": ["self_index_on"], "risk": 0, "idempotent": True,
     "verify": {"type": "has_key", "key": "file"}, "source": "curated", "status": "ready"},
    {"action_id": "organ.stats", "organ": "通用", "kind": "read",
     "impl": {"type": "method", "target": "PulseX.get_stats", "file": "organs/*/PulseX.py", "line": 0},
     "params": {"organ": "器官名"}, "preconditions": ["organ_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "has_key", "key": "organ"}, "source": "curated", "status": "ready"},
    {"action_id": "organ.inflight", "organ": "信息场", "kind": "read",
     "impl": {"type": "method", "target": "InfoField.get_organ_inflight", "file": "nucleus/field/InfoField.py", "line": 0},
     "params": {"organ": "器官名"}, "preconditions": ["field_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "int_ge", "key": "inflight", "ge": 0}, "source": "curated", "status": "ready"},
    {"action_id": "layer.backpressure", "organ": "信息场", "kind": "read",
     "impl": {"type": "method", "target": "InfoField.get_backpressure_stats", "file": "nucleus/field/InfoField.py", "line": 2690},
     "params": {"layer": "L2"}, "preconditions": ["field_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "has_keys", "keys": ["depth", "limit"]}, "source": "curated", "status": "ready"},
    {"action_id": "event.subscribers", "organ": "Self-Index", "kind": "read",
     "impl": {"type": "self_index", "target": "export_event_subscribers", "file": "nucleus/self_index/exporters.py", "line": 0},
     "params": {"event": "事件名"}, "preconditions": ["self_index_on"], "risk": 0, "idempotent": True,
     "verify": {"type": "has_key", "key": "subscribers"}, "source": "curated", "status": "ready"},
    {"action_id": "module.imports", "organ": "Self-Index", "kind": "read",
     "impl": {"type": "module_graph", "target": "build_module_graph", "file": "nucleus/self_index/module_graph.py", "line": 0},
     "params": {"module": "模块名"}, "preconditions": ["graph_on"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_list"}, "source": "curated", "status": "ready"},
    {"action_id": "module.calls", "organ": "Self-Index", "kind": "read",
     "impl": {"type": "module_graph", "target": "build_module_graph", "file": "nucleus/self_index/module_graph.py", "line": 0},
     "params": {"module_a": "模块", "module_b": "模块"}, "preconditions": ["graph_on"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_list"}, "source": "curated", "status": "ready"},
    {"action_id": "module.events", "organ": "Self-Index", "kind": "read",
     "impl": {"type": "module_graph", "target": "build_module_graph", "file": "nucleus/self_index/module_graph.py", "line": 0},
     "params": {"module": "模块名"}, "preconditions": ["graph_on"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_list"}, "source": "curated", "status": "ready"},
    {"action_id": "channel.stats", "organ": "肺", "kind": "read",
     "impl": {"type": "method", "target": "PulseLung.get_channel_concurrency_stats", "file": "organs/body/PulseLung.py", "line": 1840},
     "params": {}, "preconditions": ["lung_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "channel.last", "organ": "肺", "kind": "read",
     "impl": {"type": "method", "target": "PulseLung.get_last_channel", "file": "organs/body/PulseLung.py", "line": 0},
     "params": {}, "preconditions": ["lung_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_str"}, "source": "curated", "status": "ready"},
    {"action_id": "eyes.stats", "organ": "眼睛", "kind": "read",
     "impl": {"type": "method", "target": "PulseEyes.get_stats", "file": "organs/sense/PulseEyes.py", "line": 588},
     "params": {}, "preconditions": ["eyes_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "mouth.stats", "organ": "嘴巴", "kind": "read",
     "impl": {"type": "method", "target": "PulseMouth.get_stats", "file": "organs/output/PulseMouth.py", "line": 440},
     "params": {}, "preconditions": ["mouth_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "cortex.tools", "organ": "大脑皮层", "kind": "read",
     "impl": {"type": "method", "target": "PulseCortex.get_tool_registry", "file": "organs/brain/PulseCortex.py", "line": 3229},
     "params": {}, "preconditions": ["cortex_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_list"}, "source": "curated", "status": "ready"},
    {"action_id": "cortex.stats", "organ": "大脑皮层", "kind": "read",
     "impl": {"type": "method", "target": "PulseCortex.get_stats", "file": "organs/brain/PulseCortex.py", "line": 4608},
     "params": {}, "preconditions": ["cortex_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "cortex.parallelism", "organ": "大脑皮层", "kind": "read",
     "impl": {"type": "method", "target": "PulseCortex.get_parallelism", "file": "organs/brain/PulseCortex.py", "line": 3789},
     "params": {}, "preconditions": ["cortex_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "int_ge", "key": "parallelism", "ge": 0}, "source": "curated", "status": "ready"},
    {"action_id": "infofield.history", "organ": "信息场", "kind": "read",
     "impl": {"type": "method", "target": "InfoField.get_history", "file": "nucleus/field/InfoField.py", "line": 3365},
     "params": {"limit": 10}, "preconditions": ["field_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_list"}, "source": "curated", "status": "ready"},
    {"action_id": "infofield.deadline", "organ": "信息场", "kind": "read",
     "impl": {"type": "method", "target": "InfoField.get_deadline_stats", "file": "nucleus/field/InfoField.py", "line": 2590},
     "params": {}, "preconditions": ["field_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "stomach.stats", "organ": "胃", "kind": "read",
     "impl": {"type": "method", "target": "PulseStomach.get_stats", "file": "organs/body/PulseStomach.py", "line": 1408},
     "params": {}, "preconditions": ["stomach_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "stomach.json_stats", "organ": "胃", "kind": "read",
     "impl": {"type": "method", "target": "PulseStomach.get_json_parse_stats", "file": "organs/body/PulseStomach.py", "line": 2310},
     "params": {}, "preconditions": ["stomach_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "lung.dialog_history", "organ": "肺", "kind": "read",
     "impl": {"type": "method", "target": "PulseLung.get_dialog_history", "file": "organs/body/PulseLung.py", "line": 1882},
     "params": {"n": 10}, "preconditions": ["lung_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_list"}, "source": "curated", "status": "ready"},
    {"action_id": "innerworld.global_state", "organ": "内在世界", "kind": "read",
     "impl": {"type": "method", "target": "PulseInnerWorld.get_global_state", "file": "organs/brain/PulseInnerWorld.py", "line": 721},
     "params": {}, "preconditions": ["iw_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "liver.stats", "organ": "肝", "kind": "read",
     "impl": {"type": "method", "target": "PulseLiver.get_stats", "file": "organs/body/PulseLiver.py", "line": 0},
     "params": {}, "preconditions": ["liver_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "heart.stats", "organ": "心脏", "kind": "read",
     "impl": {"type": "method", "target": "PulseHeart.get_stats", "file": "organs/body/PulseHeart.py", "line": 0},
     "params": {}, "preconditions": ["heart_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "kidney.stats", "organ": "肾", "kind": "read",
     "impl": {"type": "method", "target": "PulseKidney.get_stats", "file": "organs/body/PulseKidney.py", "line": 0},
     "params": {}, "preconditions": ["kidney_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "hormones.stats", "organ": "激素", "kind": "read",
     "impl": {"type": "method", "target": "PulseHormones.get_stats", "file": "organs/body/PulseHormones.py", "line": 0},
     "params": {}, "preconditions": ["hormones_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "narrative.stats", "organ": "叙事自我", "kind": "read",
     "impl": {"type": "method", "target": "PulseNarrativeSelf.get_stats", "file": "organs/identity/PulseNarrativeSelf.py", "line": 0},
     "params": {}, "preconditions": ["narrative_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    {"action_id": "file_digest.stats", "organ": "文件消化器", "kind": "read",
     "impl": {"type": "method", "target": "PulseFileDigest.get_stats", "file": "organs/motor/PulseFileDigest.py", "line": 0},
     "params": {}, "preconditions": ["file_digest_online"], "risk": 0, "idempotent": True,
     "verify": {"type": "is_dict"}, "source": "curated", "status": "ready"},
    # ── 控制类（必须闸门 + 审计）──
    {"action_id": "digest_submit", "organ": "胃", "kind": "control",
     "impl": {"type": "emit", "target": "DigestEvent" + ".KNOWLEDGE", "file": "organs/body/PulseStomach.py", "line": 0},
     "params": {"payload": "知识片段"}, "preconditions": ["gate_open", "audit"], "risk": 1, "idempotent": False,
     "verify": {"type": "event_enqueued"}, "source": "curated", "status": "ready"},
    {"action_id": "mouth_speak", "organ": "嘴巴", "kind": "control",
     "impl": {"type": "emit", "target": "MouthEvent" + ".SPEAK", "file": "organs/output/PulseMouth.py", "line": 0},
     "params": {"content": "文本"}, "preconditions": ["gate_open", "audit"], "risk": 1, "idempotent": False,
     "verify": {"type": "event_emitted"}, "source": "curated", "status": "ready"},
    {"action_id": "channel_rotate", "organ": "肺", "kind": "control",
     "impl": {"type": "config", "target": "channel.rotate", "file": "organs/body/PulseLung.py", "line": 0},
     "params": {"to_channel": "渠道名"}, "preconditions": ["gate_open", "audit"], "risk": 2, "idempotent": False,
     "verify": {"type": "next_call_switched"}, "source": "curated", "status": "ready"},
    {"action_id": "organ_restart", "organ": "系统管理器", "kind": "control",
     "impl": {"type": "method", "target": "PulseSystemManager.restart_organ", "file": "organs/core/PulseSystemManager.py", "line": 0},
     "params": {"organ": "器官名"}, "preconditions": ["gate_open", "audit", "high_privilege"], "risk": 3, "idempotent": False,
     "verify": {"type": "health_restored"}, "source": "curated", "status": "ready"},
    # ── 写类（本阶段只登记，不执行）──
    {"action_id": "patch.apply", "organ": "补丁闸", "kind": "write",
     "impl": {"type": "patch", "target": "patch.apply", "file": None, "line": 0},
     "params": {"patch": "补丁内容"}, "preconditions": ["blocked_stage"], "risk": 3, "idempotent": False,
     "verify": None, "source": "curated", "status": "blocked"},
    {"action_id": "config.set", "organ": "配置", "kind": "write",
     "impl": {"type": "config", "target": "config.set", "file": None, "line": 0},
     "params": {"key": "键", "value": "值"}, "preconditions": ["blocked_stage"], "risk": 3, "idempotent": False,
     "verify": None, "source": "curated", "status": "blocked"},
    {"action_id": "file.write", "organ": "文件系统", "kind": "write",
     "impl": {"type": "fs", "target": "file.write", "file": None, "line": 0},
     "params": {"path": "路径", "content": "内容"}, "preconditions": ["blocked_stage"], "risk": 3, "idempotent": False,
     "verify": None, "source": "curated", "status": "blocked"},
]


def export_actions() -> dict:
    """导出自我动作库白名单（actions.json，含 generated_at 时间戳）。"""
    return {
        "generated_at": _now_str(),
        "version": "493.5.actions.v1",
        "action_count": len(ACTION_WHITELIST),
        "actions": ACTION_WHITELIST,
    }
