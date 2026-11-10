# -*- coding: utf-8 -*-
"""493批刀5：自我查询 fast path（含自我技术指称路由）。

策略（任务书 493-5）：
  * 用户问句出现自我指称（「你有多少个器官」「XX器官在哪」「XX和YY怎么连」）或
    代码自我指称（「你的pulse_XXX」「你的InfoField」「你的XX模块」）时，
    直接查 Self-Index 本地回答，**不调大模型**；
  * 查不到真实模块 / 器官信息 ⇒ 返回 None，由调用方走原大模型路径
    （不硬答、不套八股模板）；
  * 开关 ENABLE_M493_SELF_QUERY_FASTPATH 关闭 ⇒ 直接返回 None（逐字回退原链路）。

接入点由调用方（PulseCortex._on_chat_message）负责：命中即 emit SPEAK 并 early-return。
本模块只做「查询 → 答案字符串 | None」，纯函数、零副作用。
"""
from __future__ import annotations

import re

from nucleus._silent_except import silent_exc
from nucleus.self_index.exporters import ORGAN_ALIAS_TABLE

#: 仅 ASCII 字母数字下划线（避免 \w 吞掉中文，例如「InfoField在哪」被整段匹配）
_TOKEN = r"[A-Za-z_][A-Za-z0-9_]*"


def _fastpath_on() -> bool:
    try:
        import config
        return bool(getattr(config, "ENABLE_M493_SELF_QUERY_FASTPATH", True))
    except Exception as _e:
        silent_exc(_e, where="_fastpath_on", level="debug")
        return True


# ── 493.7-1 误答前置排除闸门 ──
_CAUSAL_WORDS = ("为什么", "怎么才能", "怎么做才", "怎么实现", "如何做到",
                 "如何实现", "比较一下", "有什么区别", "差异", "打分", "评分",
                 "原理", "利弊", "优缺点", "会怎样", "假如", "如果…会",
                 "短板", "长处")
_SELF_KW = ("我", "你", "曈曈", "自己", "器官")
_CODE_BLOCK = re.compile(
    r"```|^\s*def |^\s*class |import\s+\w+|from\s+\w+\s+import")
# 社交问候（493.7-5）：不是误答风险，闸门应对其豁免（不跳过，允许本地友好回）
_SOCIAL_RE = re.compile(
    r"^(你)?(最近|现在)?(怎么样|还好吗|在吗|在不在|你好|早安|早上好|中午好|"
    r"下午好|晚上好|晚安)\b|你最近(怎么样|好吗|在干嘛|如何)")


def _has_organ_ref(content) -> bool:
    """query 是否直接提到已知器官名（用于闸门 rule4 的放行判定，避免误拦『胃在哪』）。"""
    _c = str(content)
    for _name in ORGAN_ALIAS_TABLE:
        if _name and len(_name) >= 1 and _name in _c:
            return True
    return False


def fastpath_should_skip(content) -> bool:
    """493.7-1 误答前置排除闸门：命中任一条 → 跳过 fast path（走原链路）。

    规则：①长文本/含换行 ②因果/判断/比较/原理类 ③代码块/时间戳/URL/文件路径
          ④无自我指称关键词且不涉及已知器官（非自我查询）。
    """
    if not content or not str(content).strip():
        return True
    _s = str(content).strip()
    # 社交问候豁免：非误答风险，允许本地友好回（493.7-5）
    if _SOCIAL_RE.search(_s):
        return False
    # 1) 长文本 / 含换行
    if len(_s) > 80 or "\n" in _s:
        return True
    # 2) 因果 / 判断 / 比较 / 原理类（7 条误答多为这类）
    if any(_w in _s for _w in _CAUSAL_WORDS):
        return True
    # 3) 代码块 / 时间戳 / URL / 文件路径
    if (_CODE_BLOCK.search(_s)
            or re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", _s)
            or re.search(r"https?://|www\.", _s)
            or re.search(r"[A-Za-z]:[\\/]|/\w+/\w+", _s)
            or re.search(r"\.\w{2,4}\b", _s)):
        return True
    # 4) 没有自我指称关键词、也不涉及已知器官 → 不是自我查询
    if not any(_k in _s for _k in _SELF_KW) and not _has_organ_ref(_s):
        return True
    return False


def record_self_query_shadow(content, explain) -> None:
    """493.7-4 影子日志：fastpath 照常计算但不答、不 emit，仅落盘 self_query_shadow.jsonl。

    explain 为 self_query_fastpath_explain 的返回 dict（含 would_answer / action_id /
    organ / confidence / elapsed_ms / skipped_by_guard）。
    """
    try:
        import os
        import json
        import time as _t
        _path = os.path.join("data", "monitor", "self_query_shadow.jsonl")
        _ans = explain.get("answer")
        _row = {
            "ts": round(_t.time(), 3),
            "query": str(content),
            "would_answer": (str(_ans)[:200] if _ans else None),
            "action_id": explain.get("action_id"),
            "organ": explain.get("organ"),
            "confidence": explain.get("confidence"),
            "elapsed_ms": explain.get("elapsed_ms"),
            "skipped_by_guard": explain.get("skipped_by_guard", False),
        }
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="record_self_query_shadow", level="debug")


def _find_organ(organs, kw):
    """在器官清单里模糊匹配关键词（name 包含 / file 包含，大小写不敏感）。"""
    if not kw:
        return None
    _kw = kw.strip()
    for _o in organs:
        _n = str(_o.get("name") or "")
        _f = str(_o.get("file") or "")
        if _kw in _n or _kw.lower() in _f.lower():
            return _o
    return None


def _find_module(mg, kw):
    """在模块图节点里按模块名/文件路径模糊匹配（代码指称回退）。"""
    if not mg:
        return None
    _kw = str(kw).lower()
    for _n in mg.get("nodes", []) or []:
        _m = str(_n.get("module") or "").lower()
        _f = str(_n.get("file") or "").lower()
        if _kw in _m or _kw in _f:
            return _n
    return None


def _describe_organ(organ) -> str:
    _t = organ.get("type") or "未知"
    _hc = "核心" if organ.get("is_core") else "非核心"
    return f"{_t}类、{_hc}器官"


def _answer_organ_count(organs) -> str:
    _n = len(organs)
    _core = sum(1 for o in organs if o.get("is_core"))
    _brain = sum(1 for o in organs if o.get("is_brain_organ"))
    _sample = "、".join(str(o.get("name") or "?") for o in organs[:8])
    return (f"我目前有 {_n} 个已注册器官（核心 {_core} 个、大脑 {_brain} 个）。"
            f"例如：{_sample}。")


def _answer_organ_location(organ) -> str:
    _name = organ.get("name")
    _file = organ.get("file")
    _desc = _describe_organ(organ)
    if _file:
        return f"{_name} 在代码路径 {_file}，主要负责 {_desc}。"
    return f"{_name} 主要负责 {_desc}（代码路径暂未索引）。"


def _answer_relation(events, kw_a, kw_b) -> str | None:
    """从事件订阅者查 A、B 是否通过某事件共同连接。"""
    _matched_a, _matched_b, _common = set(), set(), []
    for _ev, _subs in events.items():
        if not isinstance(_subs, list):
            continue
        _organs_in = [s.get("organ") for s in _subs if isinstance(s, dict)]
        _a_hit = [o for o in _organs_in if o and kw_a in o]
        _b_hit = [o for o in _organs_in if o and kw_b in o]
        if _a_hit:
            _matched_a.add(_ev)
        if _b_hit:
            _matched_b.add(_ev)
        if _a_hit and _b_hit:
            _common.append((_ev, _a_hit, _b_hit))
    if _common:
        _parts = []
        for _ev, _ah, _bh in _common[:5]:
            _parts.append(f"事件「{_ev}」（{', '.join(_ah)} 与 {', '.join(_bh)} 共同订阅）")
        return (f"我查了 Self-Index：{kw_a} 和 {kw_b} 通过以下事件连接——"
                + "；".join(_parts) + "。")
    if _matched_a or _matched_b:
        _msg = []
        if _matched_a:
            _msg.append(f"{kw_a} 订阅：{', '.join(sorted(_matched_a)[:8])}")
        if _matched_b:
            _msg.append(f"{kw_b} 订阅：{', '.join(sorted(_matched_b)[:8])}")
        return (f"我在 Self-Index 里没找到 {kw_a} 与 {kw_b} 的直接共同订阅事件；"
                + "；".join(_msg) + "。")
    return None


def _load_module_graph_cached() -> dict | None:
    """493.5-5：只读已落盘的模块图（不触发重建，避免实时路径 10s 重建）。"""
    try:
        import json
        import os
        _p = os.path.join("data", "self_index", "module_graph.json")
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8", errors="replace") as _f:
                return json.load(_f)
    except Exception as _e:
        silent_exc(_e, where="_load_module_graph_cached", level="debug")
    return None


def _name_to_module(organs, name):
    for _o in organs:
        if str(_o.get("name") or "") == name:
            _f = _o.get("file") or ""
            if _f.endswith(".py"):
                return _f[:-3].replace("/", ".")
    return None


def _answer_alias(query, organs) -> str | None:
    """493.5-2/5/493.6-1：自然语言指称 → 器官（link_organ 三层消歧；开关关回 legacy）。"""
    try:
        import config as _cfg
        from nucleus.self_index.exporters import link_organ, _link_organ_legacy
        _fn = link_organ if bool(getattr(_cfg, "ENABLE_M4936_DISAMBIGUATION", True)) \
            else _link_organ_legacy
        _r = _fn(query)
    except Exception as _e:
        silent_exc(_e, where="_answer_alias", level="debug")
        return None
    if _r is None:
        return None
    if "candidates" in _r:
        _c = "、".join(c["organ"] for c in _r["candidates"][:3])
        return (f"你指的可能是这几个器官：{_c}？说得更具体点"
                f"（比如「管消化的器官」「看东西的器官」）。")
    _organ = _r["organ"]
    _o = _find_organ(organs, _organ)
    if _o:
        return _answer_organ_location(_o)
    return f"你说的是「{_organ}」这个器官（位置暂未索引）。"


def _answer_module_call(mg, mod_a, mod_b) -> str | None:
    """493.5-5：查两模块间是否有边。"""
    if not mg:
        return None
    _edges = mg.get("edges", []) or []
    _found = []
    for _e in _edges:
        _f = _e.get("from")
        _t = _e.get("to")
        if (_f == mod_a and _t == mod_b) or (_f == mod_b and _t == mod_a):
            _found.append(_e)
    if _found:
        _parts = [f"{_e.get('kind')} 边（{_e.get('from')} → {_e.get('to')}）"
                  for _e in _found[:5]]
        return (f"我查了模块图：{mod_a} 与 {mod_b} 之间有 {len(_found)} 条连接——"
                + "；".join(_parts) + "。")
    return None


def _answer_repeat() -> str | None:
    """493.6-4：再说一遍刚才的话 → lung.dialog_history 取最后一条回复。"""
    try:
        from nucleus.self_index.action_executor import execute_action
        _r = execute_action("lung.dialog_history", {"n": 1})
        if _r.get("ok"):
            _hist = _r.get("result") or []
            if isinstance(_hist, list) and _hist:
                _last = _hist[-1]
                _txt = _last.get("reply") or _last.get("content") or _last.get("text") or ""
                if _txt:
                    return f"刚才我说的是：「{_txt}」"
    except Exception as _e:
        silent_exc(_e, where="_answer_repeat", level="debug")
    return None


def _answer_backpressure(query) -> str | None:
    """493.6-4/493.7-2.3：L2 队列多深 → layer.backpressure（真实返回 {L0..L3:{queue_depth,limit}}）。"""
    _m = re.search(r"L([1-7])", query)
    _layer = _m.group(1) if _m else "L2"
    try:
        from nucleus.self_index.action_executor import execute_action
        _r = execute_action("layer.backpressure", {"layer": _layer})
        if _r.get("ok"):
            _s = _r.get("result") or {}
            if isinstance(_s, dict):
                # 493.7-2.3：get_backpressure_stats 返回按层分桶，取对应层
                _lv = _s.get("L" + _layer) or {}
                if isinstance(_lv, dict) and "queue_depth" in _lv:
                    return (f"L{_layer} 当前队列深度 {_lv.get('queue_depth')}，"
                            f"上限 {_lv.get('limit')}（背压比 {_lv.get('ratio')}）。")
    except Exception as _e:
        silent_exc(_e, where="_answer_backpressure", level="debug")
    return None


def _inflight_of(organ_name, organs):
    """在 organs_runtime 列表里按名取在途任务数。"""
    for _o in (organs or []):
        if str(_o.get("name") or "") == organ_name:
            _v = _o.get("inflight")
            return _v
    return None


def _answer_alias_state(query, organs) -> str | None:
    """493.6-4：指称 + 状态（「管消化的忙不忙」）→ 消歧 + inflight。"""
    try:
        import config as _cfg
        from nucleus.self_index.exporters import link_organ, _link_organ_legacy
        _fn = link_organ if bool(getattr(_cfg, "ENABLE_M4936_DISAMBIGUATION", True)) \
            else _link_organ_legacy
        _r = _fn(query)
    except Exception as _e:
        silent_exc(_e, where="_answer_alias_state", level="debug")
        return None
    if not _r or "organ" not in _r:
        return None
    _name = _r["organ"]
    _inf = _inflight_of(_name, organs)
    if _inf is not None:
        return f"{_name} 当前在途任务数 {_inf}。"
    return f"我暂时拿不到 {_name} 的在途任务数（埋点未就绪）。"


def self_query_fastpath_explain(query) -> dict:
    """自我查询 fast path（解释版）：返回 dict，含 answer/action_id/organ/confidence/intent/elapsed_ms。

    命中且查到真实信息 → answer 非空；未命中/查不到/开关关 → answer 为 None。
    纯查询、零副作用；不调大模型。影子模式与正式回答共用此函数（493.7-4）。
    """
    import time as _time
    _explain = {"answer": None, "action_id": None, "organ": None,
                "confidence": None, "intent": None, "elapsed_ms": 0}
    if not query or not str(query).strip():
        return _explain
    _q = str(query).strip()
    _t0 = _time.time()
    try:
        from nucleus.self_index.store import SelfIndexStore
        _store = SelfIndexStore()
        _or = _store.organs_runtime() or {}
        _organs = _or.get("organs", []) or []
        _ev = _store.events() or {}
        _events = _ev.get("events", {}) or {}
        _mg = _load_module_graph_cached()   # 493.5-5：读已落盘模块图（不触发重建）
    except Exception as _e:
        silent_exc(_e, where="self_query_fastpath", level="debug")
        return _explain

    def _set(answer, action_id, confidence, organ=None, intent=None):
        _explain.update(answer=answer, action_id=action_id,
                        confidence=confidence, organ=organ, intent=intent)
        _explain["elapsed_ms"] = (_time.time() - _t0) * 1000

    # 0) 复读：再说一遍刚才的话（严格本地，不调大模型）
    if re.search(r"再说一遍|重复.*(刚才|上面|之前|的话)|刚才.*(说|讲|回)|刚刚.*(说|讲|回)", _q):
        _rep = _answer_repeat()
        if _rep:
            _set(_rep, "repeat", 0.9, intent="repeat")
            return _explain

    # 0b) 背压：L2 队列多深（因果「为什么满」不命中，走 fallback）
    if (re.search(r"L[1-7].*(队列|积压|背压|深度|多深|多长)|队列.*(多深|多长|深度)|(背压|积压).*L[1-7]", _q)
            and not re.search(r"为什么|怎么|原因|会满|会爆|会崩", _q)):
        _bp = _answer_backpressure(_q)
        if _bp:
            _set(_bp, "layer.backpressure", 0.8, intent="backpressure")
            return _explain

    # 社交模板（493.7-5：你最近怎么样/晚上好/中午好/在吗…本地友好回，不调大模型；非误答）
    if re.search(r"^(你)?(最近|现在)?(怎么样|还好吗|在吗|在不在|你好|早安|早上好|中午好|下午好|晚上好|晚安)\b", _q) \
            or re.search(r"你最近(怎么样|好吗|在干嘛|如何)", _q):
        _set("我一直在，随时可以聊～你呢？有什么想说的？", "social.greeting", 0.95, intent="social")
        return _explain

    # 1) 器官计数
    if re.search(r"器官.*(多少|几个|几只)|(多少|几个|几只).*器官", _q) \
            or "有多少个器官" in _q or "几个器官" in _q:
        _set(_answer_organ_count(_organs), "organ.count", 0.95, intent="count")
        return _explain

    # 2) 关系对「A 和/与 B 怎么连/关系/关联/连接」
    _rel = re.search(
        r"([一-龥A-Za-z_]{1,12})\s*(?:和|与|跟|及)\s*([一-龥A-Za-z_]{1,12})"
        r"\s*(怎么连|怎么关联|的关系|如何连|连接|关联|怎么接|用到|用到会怎样)", _q)
    if _rel:
        _ans = _answer_relation(_events, _rel.group(1), _rel.group(2))
        if _ans:
            _set(_ans, "relation", 0.8, intent="relation")
            return _explain

    # 3) 单器官 / 模块位置（493.7-5：器官名/模块名 + 在哪/文件 即可，不必带「器官」关键词）
    _m = re.search(
        rf"(?:你的|我的)?({_TOKEN})|([A-Za-z0-9_]+\.py)|"
        rf"(?:你的|我的)?([一-龥]{{1,8}}?)\s*(?:器官|模块)?\s*(?:在|的|于)?\s*"
        r"(?:哪|什么位置|哪里|文件|路径|代码)", _q)
    if _m:
        _kw = _m.group(1) or _m.group(2) or _m.group(3)
        _o = _find_organ(_organs, _kw)
        if _o is None:
            _mo = _find_module(_mg, _kw)
            if _mo:
                _set(f"{_kw} 模块在代码路径 {_mo.get('file')}（模块 {_mo.get('module')}）。",
                     "module.location", 0.85, intent="location")
                return _explain
        if _o:
            _set(_answer_organ_location(_o), "organ.location", 0.9,
                 organ=_o.get("name"), intent="location")
            return _explain
        # 明确器官/模块均未解析（如「管消化的器官」「看东西的器官」这类描述式指称）
        # ⇒ 不硬答、落空，交后续别名/消歧分支处理。

    # 4) 裸英文 / 模块词命中（覆盖「pulse_XXX」「InfoField」这类指称，无论是否带「你的」）
    for _w in re.findall(rf"{_TOKEN}", _q):
        if len(_w) < 3:
            continue
        _o = _find_organ(_organs, _w)
        if not _o:
            _mo = _find_module(_mg, _w)
            if _mo:
                _set(f"{_w} 模块在代码路径 {_mo.get('file')}（模块 {_mo.get('module')}）。",
                     "module.location", 0.85, intent="location")
                return _explain
            continue
        if re.search(r"怎么连|关系|关联|连接|用到|用到会怎样", _q):
            # 关系型：尝试找另一模块共同事件
            _others = [w for w in re.findall(rf"{_TOKEN}", _q)
                       if len(w) >= 3 and w != _w]
            for _w2 in _others:
                _o2 = _find_organ(_organs, _w2)
                if _o2:
                    _ans = _answer_relation(_events, _w, _w2)
                    if _ans:
                        _set(_ans, "relation", 0.8, organ=_o.get("name"), intent="relation")
                        return _explain
            _set((f"我在 Self-Index 里查到 {_o.get('name')}（{_o.get('file')}），"
                  f"但本批尚未索引模块级调用关系（待 493-8 调用图补齐），"
                  f"暂时说不清它的调用链路。"),
                 "relation.unknown", 0.6, organ=_o.get("name"), intent="relation")
            return _explain
        _set(_answer_organ_location(_o), "organ.location", 0.85,
             organ=_o.get("name"), intent="location")
        return _explain

    # 5) 单器官指称（无位置词，仅问「你的XX器官」）
    _m3 = re.search(r"你的([A-Za-z_][A-Za-z0-9_]*)|([一-龥]{1,4})器官", _q)
    if _m3:
        _kw = _m3.group(1) or _m3.group(2)
        _o = _find_organ(_organs, _kw)
        if _o:
            _loc = f"，代码路径 {_o.get('file')}" if _o.get("file") else ""
            _set(f"{_o.get('name')} 是{_describe_organ(_o)}{_loc}。",
                 "organ.describe", 0.9, organ=_o.get("name"), intent="describe")
            return _explain

    # 6a) 指称 + 状态（「管消化的忙不忙」）→ 消歧到器官并查在途
    if (re.search(r"管[一-龥]{1,6}的|负责[一-龥]{1,6}的|的器官", _q)
            and re.search(r"忙|在途|处理多少|在跑|有多少.*任务|任务.*多少|忙不忙", _q)):
        _st = _answer_alias_state(_q, _organs)
        if _st:
            _set(_st, "organ.state", 0.85, intent="state")
            return _explain

    # 6) 指称别名（「管消化的」「管说话的」「看东西的」「负责X的器官」）
    if re.search(r"管[一-龥]{1,6}的|负责[一-龥]{1,6}的|的器官|哪个器官|什么器官", _q):
        _ans = _answer_alias(_q, _organs)
        if _ans:
            _set(_ans, "organ.alias", 0.8, intent="alias")
            return _explain

    # 7) 运行态：在途 / 队列 / 渠道（遍历器官名，避免中文贪婪吃掉修饰词）
    for _o in _organs:
        _nm = str(_o.get("name") or "")
        if not _nm or _nm not in _q:
            continue
        if re.search(r"忙|在途|任务|还在跑|有没有.*做|多少.*任务", _q):
            _v = _o.get("inflight")
            if _v is not None:
                _set(f"{_nm} 当前在途任务数 {_v}。", "organ.inflight", 0.85,
                     organ=_nm, intent="inflight")
                return _explain
            _set(f"我暂时拿不到 {_nm} 的在途任务数（埋点未就绪）。",
                 "organ.inflight.missing", 0.7, organ=_nm, intent="inflight")
            return _explain
        if re.search(r"队列|积压|排队", _q):
            _v = _o.get("queue_depth")
            if _v is not None:
                _set(f"{_nm} 当前队列深度 {_v}。", "organ.queue", 0.8, organ=_nm, intent="queue")
                return _explain
            _set(f"我暂时拿不到 {_nm} 的队列深度。", "organ.queue.missing", 0.6, organ=_nm, intent="queue")
            return _explain
    if re.search(r"哪个渠道|什么渠道|最近.*渠道|现在.*回答.*渠道|用的.*渠道|在.*渠道|哪个模型|什么模型|用的.*模型|刚才.*模型|最近.*模型", _q):
        _lc = _or.get("last_channel")
        if _lc:
            _set(f"最近一次回答用的是「{_lc}」渠道。", "channel.last", 0.85, intent="channel")
            return _explain
        _set("我暂时没记录到最近使用的应答渠道。", "channel.last.missing", 0.7, intent="channel")
        return _explain

    # 8) 模块调用 / 依赖（「XX调用YY吗」「XX依赖什么」）
    _mc = re.search(
        r"([一-龥A-Za-z_]{1,12})\s*(?:调用|依赖|用到|和|与)\s*([一-龥A-Za-z_]{1,12})"
        r"\s*(?:吗|？|\?|怎么|关系|调用|依赖|连接)", _q)
    if _mc:
        _ma = _name_to_module(_organs, _mc.group(1))
        _mb = _name_to_module(_organs, _mc.group(2))
        if _ma and _mb:
            _ans = _answer_module_call(_mg, _ma, _mb)
            if _ans:
                _set(_ans, "module.call", 0.7, intent="module_call")
                return _explain
            _set(f"我查了模块图：{_mc.group(1)} 与 {_mc.group(2)} 之间"
                 f"暂时没有直接登记的调用/依赖边。",
                 "module.call.none", 0.6, intent="module_call")
            return _explain
        if _ma and re.search(r"依赖|用到|调用", _q):
            _outs = [e for e in (_mg or {}).get("edges", []) if e.get("from") == _ma]
            if _outs:
                _tos = "、".join(sorted({e.get("to") for e in _outs[:6]}))
                _set(f"{_mc.group(1)} 直接连接（调用/注入/事件）的模块有：{_tos}。",
                     "module.deps", 0.7, intent="module_deps")
                return _explain

    # 9) 眼睛 / 视觉状态
    if re.search(r"眼睛|视觉", _q) and re.search(r"状态|在干嘛|现在|如何|情况", _q):
        _o = _find_organ(_organs, "眼睛")
        if _o:
            _set((f"{_o.get('name')}（视觉感官器官）负责采集视觉输入，"
                  f"代码路径 {_o.get('file')}；实时画面我看不到原始像素，"
                  f"这是我的感官层职责。"), "organ.eyes", 0.8, organ=_o.get("name"), intent="eyes")
            return _explain

    _explain["elapsed_ms"] = (_time.time() - _t0) * 1000
    return _explain


def self_query_fastpath(query) -> str | None:
    """自我查询 fast path 入口（答案字符串版）。

    薄封装：self_query_fastpath_explain(...).get("answer")。逻辑本身不受
    ENABLE_M493_SELF_QUERY_FASTPATH 约束——开关只决定 PulseCortex 是否正式 emit 回答，
    影子模式直接用 explain 照常计算。逐字未命中/查不到 → None。
    """
    return self_query_fastpath_explain(query).get("answer")
