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

import json
import os
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
#: 原始因果词（493.12-1 关总闸时回退用；含会怎样/假如/如果…会）
_CAUSAL_WORDS = ("为什么", "怎么才能", "怎么做才", "怎么实现", "如何做到",
                 "如何实现", "比较一下", "有什么区别", "差异", "打分", "评分",
                 "原理", "利弊", "优缺点", "会怎样", "假如", "如果…会",
                 "短板", "长处")
#: ★493.12-1：因果词拆两层
#   _PHILOSOPHY_CAUSAL：真哲学/主观判断词 → 仍 guard 跳过（不答不编）
#   _MODULE_CAUSAL：模块关系因果词 → 放行，交给关系路由/诚实降级（不再一棍子 skip）
#   注意：不放宽「会怎样」「如果…会」这种通用假设词（493.7 仍要求「如果…会怎样」跳过），
#        只放行带具体模块动作的词（挂了会怎样/应用到/影响/效果/协作/产生/诞生）。
_PHILOSOPHY_CAUSAL = ("为什么", "怎么才能", "怎么做才", "怎么实现", "如何做到",
                      "如何实现", "比较一下", "有什么区别", "差异", "打分", "评分",
                      "原理", "利弊", "优缺点", "短板", "长处",
                      "幸福", "意义", "人生", "应该", "好不好", "假如")
_MODULE_CAUSAL = ("挂了会怎样", "产生什么", "诞生",
                 "应用到", "影响", "效果", "协作")
_SELF_KW = ("我", "你", "曈曈", "自己", "器官")
_CODE_BLOCK = re.compile(
    r"```|^\s*def |^\s*class |import\s+\w+|from\s+\w+\s+import")
# 社交问候（493.7-5）：不是误答风险，闸门应对其豁免（不跳过，允许本地友好回）
_SOCIAL_RE = re.compile(
    r"^(你)?(最近|现在)?(怎么样|还好吗|在吗|在不在|你好|早安|早上好|中午好|"
    r"下午好|晚上好|晚安)\b|你最近(怎么样|好吗|在干嘛|如何)")


def _has_organ_ref(content) -> bool:
    """query 是否直接提到已知器官名（用于闸门 rule4 的放行判定，避免误拦『胃在哪』）。

    493.12-3：别名（皮层/大脑/管消化的…）也计入，避免「QICA 传给皮层」被误拦。
    """
    _c = str(content)
    for _name, _L in ORGAN_ALIAS_TABLE.items():
        if _name and _name in _c:
            return True
        if isinstance(_L, dict):
            for _al in (_L.get("name") or []):
                if _al and _al in _c:
                    return True
    return False


#: 493.12-1：已知模块名缓存（query 提到模块名也算自我查询，guard 不拦）。
_MODULE_NAMES_CACHE = None


def _has_module_ref(content) -> bool:
    """query 是否提到已知模块名（QICA/PulseCortex/InfoField…）→ 视为自我查询。

    避免「InfoField 和 PulseCortex 是什么关系」「QICA 怎么把话传给皮层」
    被 rule4 误拦。仅匹配末段类名（infofield/pulsecortex/qica…），不过度匹配
    nucleus/organs 等通用段，减少误放行。模块图首次调用时加载并缓存。
    """
    global _MODULE_NAMES_CACHE
    _c = str(content)
    if _MODULE_NAMES_CACHE is None:
        _MODULE_NAMES_CACHE = set()
        try:
            _mg = _load_module_graph_cached()
            for _n in (_mg or {}).get("nodes", []) or []:
                _last = str(_n.get("module") or "").split(".")[-1].lower()
                if len(_last) >= 3:
                    _MODULE_NAMES_CACHE.add(_last)
        except Exception as _e:
            silent_exc(_e, where="_has_module_ref", level="debug")
    for _tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _c):
        if _tok.lower() in _MODULE_NAMES_CACHE:
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
    # 2) 因果 / 判断 / 比较 / 原理类
    if _guard_layered_on():
        # ★493.12-1：模块关系因果词放行（交给关系路由/诚实降级），
        #   哲学/主观判断词仍跳过（不放宽到所有因果问法）。
        if any(_w in _s for _w in _MODULE_CAUSAL):
            return False
        if any(_w in _s for _w in _PHILOSOPHY_CAUSAL):
            return True
    else:
        if any(_w in _s for _w in _CAUSAL_WORDS):
            return True
    # 3) 代码块 / 时间戳 / URL / 文件路径
    if (_CODE_BLOCK.search(_s)
            or re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", _s)
            or re.search(r"https?://|www\.", _s)
            or re.search(r"[A-Za-z]:[\\/]|/\w+/\w+", _s)
            or re.search(r"\.\w{2,4}\b", _s)):
        return True
    # 4) 没有自我指称关键词、也不涉及已知器官/模块 → 不是自我查询
    if (not any(_k in _s for _k in _SELF_KW)
            and not _has_organ_ref(_s)
            and not (_guard_layered_on() and _has_module_ref(_s))):
        return True
    return False


#: 493.11-5：进程代（影子日志与 action_audit 各自独立，同进程内一致）
_SHADOW_PROC_GEN = None


def _shadow_proc_gen():
    global _SHADOW_PROC_GEN
    if _SHADOW_PROC_GEN is None:
        import time as _t
        _SHADOW_PROC_GEN = round(_t.time(), 3)
    return _SHADOW_PROC_GEN


def _guard_reason(content) -> str | None:
    """493.11-5：指出被哪条 guard 跳过（would_answer=null 时必须有原因）。"""
    _s = str(content or "").strip()
    if len(_s) > 80 or "\n" in _s:
        return "long_text_or_newline"
    if _guard_layered_on():
        if any(_w in _s for _w in _MODULE_CAUSAL):
            return "causal_on_module"      # 不跳过，放行给关系路由
        if any(_w in _s for _w in _PHILOSOPHY_CAUSAL):
            return "causal_or_judgment_word"  # 沿用旧字符串，兼容 493.7/493.11 测试
    else:
        if any(_w in _s for _w in _CAUSAL_WORDS):
            return "causal_or_judgment_word"
    if (_CODE_BLOCK.search(_s)
            or re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", _s)
            or re.search(r"https?://|www\.", _s)
            or re.search(r"[A-Za-z]:[\\/]|/\w+/\w+", _s)
            or re.search(r"\.\w{2,4}\b", _s)):
        return "code_or_url_or_timestamp"
    if (not any(_k in _s for _k in _SELF_KW)
            and not _has_organ_ref(_s)
            and not (_guard_layered_on() and _has_module_ref(_s))):
        return "no_self_reference"
    return None


def record_self_query_shadow(content, explain, *, session_id=None,
                             source_path="cortex", guard_reason=None,
                             error_reason=None) -> None:
    """493.7-4 影子日志：fastpath 照常计算但不答、不 emit，仅落盘 self_query_shadow.jsonl。

    493.11-5 新增字段：session_id / proc_gen / source_path / guard_reason /
    error_reason / expect_organ / labeled_correct（后两者留空供人工标注）。
    ``would_answer=null`` 的行**必须**带 guard_reason 或 error_reason，不准静默空。
    开关 ENABLE_M49311_SHADOW_FIELDS 关 → 逐字回 493.10（只写原有字段）。
    """
    try:
        import os
        import json
        import time as _t
        _path = os.path.join("data", "monitor", "self_query_shadow.jsonl")
        _ans = explain.get("answer")
        # 493.8-1：影子日志扩字段 label / self_ref（四类标注；开关关 → 不计算、字段留 None）
        _label = None
        _self_ref = None
        try:
            import config as _cfg
            if bool(getattr(_cfg, "ENABLE_M4938_SHADOW_LABEL", False)):
                from nucleus.self_index.alias_labeler import (
                    label_shadow_row, compute_self_ref)
                # ★493.14-3：label_shadow_row 读 would_answer，但 explain 只有 answer 键
                #   （schema 错位 → 标签误判）。构造带 would_answer 的影子行再判定。
                _label = label_shadow_row({
                    "would_answer": _ans,
                    "organ": explain.get("organ"),
                    "skipped_by_guard": explain.get("skipped_by_guard", False),
                    "query": str(content),
                })
                _self_ref = compute_self_ref(str(content))
        except Exception as _e:
            silent_exc(_e, where="record_self_query_shadow.label", level="debug")
        _row = {
            "ts": round(_t.time(), 3),
            "query": str(content),
            "would_answer": (str(_ans)[:200] if _ans else None),
            "action_id": explain.get("action_id"),
            "organ": explain.get("organ"),
            "confidence": explain.get("confidence"),
            "elapsed_ms": explain.get("elapsed_ms"),
            "skipped_by_guard": explain.get("skipped_by_guard", False),
            "label": _label,
            "self_ref": _self_ref,
        }
        try:
            import config as _cfg11
            _fields_on = bool(getattr(_cfg11, "ENABLE_M49311_SHADOW_FIELDS", False))
        except Exception as _e:
            silent_exc(_e, where="record_self_query_shadow.fields_cfg", level="debug")
            _fields_on = False
        if _fields_on:
            _gr = guard_reason
            if _gr is None and explain.get("skipped_by_guard"):
                _gr = _guard_reason(content) or "guard_skipped"
            _er = error_reason
            if not _ans and not _gr and not _er:
                # 不静默空：无答案且无 guard ⇒ 记为「fastpath 未命中」
                _er = "fastpath_no_match"
            _row.update({
                "session_id": session_id,
                "proc_gen": _shadow_proc_gen(),
                "source_path": source_path,
                "guard_reason": _gr,
                "error_reason": _er,
                "expect_organ": None,       # 人工标注留空
                "labeled_correct": None,    # 人工标注留空
            })
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        # ★493.12-4：写影子后触发 dry-run 候选抽取（每 100 条一次，只 append 候选文件，
        #   不写 overlay / 不 promote / 不改线上回答，受 ENABLE_M49312_DRYRUN_CANDIDATES 控）
        try:
            from nucleus.self_index.learned_alias import _maybe_trigger_dryrun
            _maybe_trigger_dryrun()
        except Exception as _e2:
            silent_exc(_e2, where="record_self_query_shadow.dryrun", level="debug")
    except Exception as _e:
        silent_exc(_e, where="record_self_query_shadow", level="debug")


def _organ_alias_match_on() -> bool:
    """493.9-1 独立开关：_find_organ 别名/中文名/类名三路匹配（关 → 回 493.8 行为）。"""
    try:
        import config as _cfg
        return (bool(getattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", False))
                and bool(getattr(_cfg, "ENABLE_M4939_FASTPATH_ORGAN_ALIAS", False)))
    except Exception as _e:
        silent_exc(_e, where="_organ_alias_match_on", level="debug")
        return False


def _guard_layered_on() -> bool:
    """493.12-1 独立开关：guard 因果词分层（关 → 逐字回 493.11 单 _CAUSAL_WORDS 一刀切）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_M49312_GUARD_LAYERED", False))
    except Exception as _e:
        silent_exc(_e, where="_guard_layered_on", level="debug")
        return False


def _find_organ(organs, kw):
    """在器官清单里模糊匹配关键词（493.9-1 三路：name/file 子串 + 别名表 name/func/sys + 消歧一致性）。

    第一路：name 包含 / file 包含（大小写不敏感）—— 覆盖中文名「胃」、类名「PulseStomach」、
            代码路径「organs/body/PulseStomach.py」。
    第二路：ORGAN_ALIAS_TABLE 的 name/func/sys 三层别名 —— 覆盖「管消化的」「看东西的」等功能指称。
    第三路：link_organ 唯一命中 → 按器官中文名回表 —— 保证「消歧命中了但查不到器官」不出现。
    """
    if not kw:
        return None
    _kw = kw.strip()
    # 第一路：name / file 子串
    for _o in organs:
        _n = str(_o.get("name") or "")
        _f = str(_o.get("file") or "")
        if _kw in _n or _kw.lower() in _f.lower():
            return _o
    if not _organ_alias_match_on():
        return None
    _k = _kw.lower()
    # 第二路：别名表三路（name / func / sys）
    try:
        _hit_names: list[str] = []
        for _name, _L in ORGAN_ALIAS_TABLE.items():
            if not isinstance(_L, dict):
                continue
            for _layer in ("name", "func", "sys"):
                for _a in (_L.get(_layer) or []):
                    _al = str(_a or "").lower()
                    if _al and (_k in _al or _al in _k):
                        _hit_names.append(_name)
                        break
                else:
                    continue
                break
        if len(_hit_names) == 1:
            for _o in organs:
                if str(_o.get("name") or "") == _hit_names[0]:
                    return _o
    except Exception as _e:
        silent_exc(_e, where="_find_organ.alias", level="debug")
    # 第三路：link_organ 唯一命中 → 回表（消歧一致性）
    try:
        from nucleus.self_index.exporters import link_organ as _lo
        _r = _lo(_kw)
        if _r and "organ" in _r:
            for _o in organs:
                if str(_o.get("name") or "") == _r["organ"]:
                    return _o
    except Exception as _e:
        silent_exc(_e, where="_find_organ.link", level="debug")
    return None


def _find_module(mg, kw):
    """在模块图节点里按模块名/文件路径匹配（代码指称回退）。

    ★493.12-3：优先精确匹配完整模块名/末段类名/文件名 stem
    （QICA→QICA.py，不误中同目录 IntentChannels）；仅当无精确命中才退回到
    子串/包名段。关 ENABLE_M49312_MODULE_EXACT → 逐字回 493.11 子串行为。
    """
    if not mg:
        return None
    _kw = str(kw).lower()
    if not _kw:
        return None
    if not _m49312_module_exact_on():
        for _n in mg.get("nodes", []) or []:
            _m = str(_n.get("module") or "").lower()
            _f = str(_n.get("file") or "").lower()
            if _kw in _m or _kw in _f:
                return _n
        return None
    _exact = None
    _fallback = None
    for _n in mg.get("nodes", []) or []:
        _m = str(_n.get("module") or "")
        _f = str(_n.get("file") or "")
        _ml = _m.lower()
        _short = _m.split(".")[-1].lower()
        _stem = _basename(_f)[:-3].lower() if _f.endswith(".py") else ""
        # 精确：完整模块名 / 末段类名 / 文件 stem（QICA 命中 QICA.py）
        if _kw == _ml or _kw == _short or _kw == _stem:
            _exact = _n
            break
        # 兜底：子串/包名段（仅当精确未命中时用，避免 qica 包误中 IntentChannels）
        if _fallback is None and (len(_kw) >= 4
                                  and (_kw in _ml or _kw in _f.lower())):
            _fallback = _n
    return _exact if _exact is not None else _fallback


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


# ══════════ 493.10-1/3：模块图真实消费 ══════════
#: 关系词（任务书 493.10-1）；命中即优先走模块图，不得被位置分支截走
#: 基础词（493.10）；扩展词（493.12-2：协作/影响/效果/产生/诞生/应用到/传给/…）
_REL_WORDS_RE_BASE = re.compile(
    r"怎么连|怎么关联|如何连|怎么接|怎么交互|怎么通信|关联|连接|关系|"
    r"调用|依赖|用到|被谁|谁调用|谁依赖|谁用到")
_REL_WORDS_RE_EXPAND = re.compile(
    r"怎么连|怎么关联|如何连|怎么接|怎么交互|怎么通信|关联|连接|关系|"
    r"调用|依赖|用到|被谁|谁调用|谁依赖|谁用到|"
    r"协作|影响|效果|产生|诞生|应用到|传给|交给|作用于|挂了会怎样|怎么协作|会做|"
    r"对.{0,8}有.{0,4}影响")


def _rel_words_re() -> "re.Pattern":
    """493.12-2 开关：选基础/扩展关系词表（关 → 逐字回 493.10）。"""
    try:
        import config as _cfg
        if bool(getattr(_cfg, "ENABLE_M49312_REL_EXPAND", False)):
            return _REL_WORDS_RE_EXPAND
    except Exception as _e:
        silent_exc(_e, where="_rel_words_re", level="debug")
    return _REL_WORDS_RE_BASE


#: 效果/副作用问法词（命中 → 回答尾部加诚实降级话术，不编运行后果）
_EFFECT_WORDS_RE = re.compile(r"影响|效果|产生|诞生|会怎样|会做|应用|作用|后果|副作用|挂了")


def _honest_effect_note(query, no_edge=False) -> str:
    """493.12-2：效果类问法诚实降级话术（无运行效果数据，不编）。"""
    if not _EFFECT_WORDS_RE.search(query or ""):
        return ""
    if no_edge:
        return ("模块图只记录结构调用关系，不记录运行效果/副作用数据，"
                "因此不能推断“挂了会怎样/产生什么效果”等运行后果。")
    return ("这些边说明模块间有调用/事件联系，但我现在没有“效果/副作用”数据，"
            "不能推断“应用到会诞生什么/会产生什么效果”。")


#: 关系问句里的非实体英文词（避免把「怎么」类词当模块名）
_REL_TOKEN_STOP = frozenset({
    "the", "and", "how", "what", "who", "which", "connect", "relation",
    "call", "import", "inject", "event", "module", "between"})


def _m49310_on() -> bool:
    """493.10 刀1-5 总闸（关 → 逐字回 493.9）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M49310_MODULE_GRAPH_CONSUME", False))
    except Exception as _e:
        silent_exc(_e, where="fastpath._m49310_on", level="debug")
        return False


def _short_module(m) -> str:
    """完整点分模块名 → 末段（organs.brain.PulseCortex → PulseCortex）。"""
    return str(m or "").split(".")[-1]


def _basename(path) -> str:
    return str(path or "").replace("\\", "/").split("/")[-1]


def _render_module_edge(e) -> str:
    """493.10-3：渲染单条边 —— kind + 事件名 + 起点文件:行 + 终点文件:行。

    `to_line` 为空（import/inject/call 共 4721 条）时明确写「目标行未解析」，
    绝不编造行号，也不留空。
    """
    _kind = str(e.get("kind") or "未知")
    _head = _kind + " 边"
    if e.get("event"):
        _head += f" {e.get('event')}"
    if e.get("via"):
        _head += f"（via {e.get('via')}）"
    _ff = _basename(e.get("from_file"))
    _fl = e.get("from_line")
    _src = f"{_ff}:{_fl}" if _ff and _fl is not None else \
           (f"{_ff}:起点行未解析" if _ff else "起点未解析")
    _tf = _basename(e.get("to_file"))
    _tl = e.get("to_line")
    if _tl is None or not _tf:
        _dst = "目标行未解析"
    else:
        _dst = f"{_tf}:{_tl}"
    return f"{_head}（{_src} → {_dst}）"


def _render_module_edges(label, edges, prefix="我查了模块图") -> str:
    """把一批边渲染成可读回答（最多 5 条，附真实总数）。"""
    _parts = [_render_module_edge(e) for e in edges[:5]]
    _more = f"（仅列前 5 条）" if len(edges) > 5 else ""
    return (f"{prefix}：{label} 之间有 {len(edges)} 条连接{_more}——"
            + "；".join(_parts) + "。")


def _resolve_module_terms(mg, organs, token) -> set:
    """把 query 实体 token 解析成模块图 from/to 可能出现的完整模块名集合。

    ★493.12-3：精确匹配（末段类名/文件名/完整模块）优先，模糊子串仅作兜底
    —— 否则「QICA」会同时命中同目录的 IntentChannels（qica 包段）。
    开关 ENABLE_M49312_MODULE_EXACT 关 → 逐字回 493.11（精确兜底混用）。
    """
    _terms: set = set()
    _t = str(token or "").lower()
    if not _t:
        return _terms
    _exact: set = set()
    _fuzzy: set = set()
    for _n in (mg or {}).get("nodes", []) or []:
        _m = str(_n.get("module") or "")
        _f = str(_n.get("file") or "")
        _ml = _m.lower()
        _short = _short_module(_m).lower()
        _stem = _basename(_f)[:-3].lower() if _f.endswith(".py") else ""
        if _t == _short or _t == _stem or _t == _ml:
            _exact.add(_m)          # 精确：末段类名 / 文件名 / 完整模块
        elif _t in _ml.split(".") or (len(_t) >= 4 and (_t in _ml or _t in _f.lower())):
            _fuzzy.add(_m)          # 兜底：段包含 / 子串（含包名段，仅无精确时回退）
    if _m49312_module_exact_on():
        _terms |= (_exact if _exact else _fuzzy)
    else:
        _terms |= (_exact | _fuzzy)
    # 中文器官名 → 代码点分路径（胃 → organs.body.PulseStomach）
    _om = _name_to_module(organs, token)
    if _om:
        _terms.add(_om)
    else:
        # ★493.12-3：中文别名（皮层 → 大脑皮层 → PulseCortex）反向解析
        try:
            from nucleus.self_index.exporters import ORGAN_ALIAS_TABLE, link_organ
            for _organ, _L in ORGAN_ALIAS_TABLE.items():
                if _t in [str(_x).lower() for _x in (_L.get("name") or [])]:
                    _om = _name_to_module(organs, _organ)
                    break
            if not _om:
                _r = link_organ(token)
                if _r and _r.get("organ"):
                    _om = _name_to_module(organs, _r["organ"])
        except Exception as _e:
            silent_exc(_e, where="_resolve_module_terms.alias", level="debug")
        if _om:
            _terms.add(_om)
    return _terms


def _edges_between(mg, terms_a, terms_b) -> list:
    _out = []
    for _e in (mg or {}).get("edges", []) or []:
        _f, _t = _e.get("from"), _e.get("to")
        if (_f in terms_a and _t in terms_b) or (_f in terms_b and _t in terms_a):
            _out.append(_e)
    return _out


def _edges_into(mg, terms) -> list:
    """「被谁调用/谁依赖」→ 入边（to 命中目标）。"""
    return [_e for _e in (mg or {}).get("edges", []) or []
            if _e.get("to") in terms]


def _edges_any(mg, terms) -> list:
    """「A 的相关连接」→ 双向边（from 或 to 命中目标）。"""
    return [_e for _e in (mg or {}).get("edges", []) or []
            if _e.get("from") in terms or _e.get("to") in terms]


def _m49312_module_exact_on() -> bool:
    """493.12-3 开关：模块名精确匹配优先（关 → 逐字回 493.11 子串匹配）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_M49312_MODULE_EXACT", False))
    except Exception as _e:
        silent_exc(_e, where="_m49312_module_exact_on", level="debug")
        return False


# ── 493.13-2 效果卡命中 ──
def _m49313_effect_cards_on() -> bool:
    """493.13-2 开关：关系路由命中效果卡（关 → 逐字回 493.12 纯结构边）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_M49313_EFFECT_CARDS", False))
    except Exception as _e:
        silent_exc(_e, where="_m49313_effect_cards_on", level="debug")
        return False


def _load_effect_cards() -> list:
    """读取 data/self_index/effects_cards.json（带 file:line 证据的效果卡）。"""
    _p = os.path.join("data", "self_index", "effects_cards.json")
    try:
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8", errors="replace") as _f:
                return json.load(_f)
    except Exception as _e:
        silent_exc(_e, where="_load_effect_cards", level="debug")
    return []


def _match_effect_card(cards, frm, to, event) -> dict | None:
    """按 (from,to,event) 精确，再退化 (from,to) 匹配效果卡。"""
    for _c in cards:
        if (_c.get("from") == frm and _c.get("to") == to
                and (_c.get("event") == event or not _c.get("event"))):
            return _c
    for _c in cards:
        if _c.get("from") == frm and _c.get("to") == to:
            return _c
    return None


def _is_effect_card_stale(card) -> bool:
    """generated_at + stale_after_days 判定是否过期（过期则只答结构边）。"""
    try:
        from datetime import datetime as _dt
        _g = card.get("generated_at")
        _d = int(card.get("stale_after_days", 90))
        _gd = _dt.strptime(str(_g), "%Y-%m-%d").date()
        return (_dt.today().date() - _gd).days > _d
    except Exception:
        return False


def _effect_section(found, query) -> str:
    """493.13-2：边命中效果卡 → 追加「效果：…（证据：…）」；有边无卡且问效果 → 诚实降级。

    过期卡（stale_after_days 超期）→ 跳过，只答结构边（不编旧效果）。
    """
    if not found or not _m49313_effect_cards_on():
        return ""
    _cards = _load_effect_cards()
    _lines = []
    for _e in found:
        _c = _match_effect_card(_cards, _e.get("from"), _e.get("to"),
                                _e.get("event") or _e.get("via"))
        if _c and not _is_effect_card_stale(_c):
            _lines.append(f"效果：{_c['effect']}（证据：{_c.get('evidence')}）")
    if _lines:
        return "\n" + "\n".join(_lines)
    if _EFFECT_WORDS_RE.search(query or ""):
        return ("\n这些边说明模块间有调用/事件联系，但我现在没有沉淀的「效果」数据"
                "（effects_cards 未登记该 from→to→event），不能推断运行后果。")
    return ""


def _answer_module_relation(query, mg, events, organs) -> str | None:
    """493.10-1：关系问法优先消费模块图（不得被位置分支截走）。

    返回答案字符串；未检测到关系问法 / 无法构成实体对 → None（交回原链路）。
    图里无连接边时如实说「未找到直接连接边」，不编造、不回退到位置话术。
    """
    if not _m49310_on() or not _rel_words_re().search(query):
        return None
    # ① 实体提取：英文/下划线模块 token + 中文器官名（去重、保序）
    _tokens: list = []
    for _t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query):
        _tl = _t.lower()
        if len(_t) >= 3 and _tl not in _REL_TOKEN_STOP and _t not in _tokens:
            _tokens.append(_t)
    for _o in organs or []:
        _nm = str(_o.get("name") or "")
        if _nm and _nm in query and _nm not in _tokens:
            _tokens.append(_nm)
    # ★493.12-3：中文别名（皮层/皮质等）也在 query 中 → 加别名 token，
    #   使「QICA 传给皮层」能解析出 PulseCortex（否则只匹配到全名「大脑皮层」）
    try:
        from nucleus.self_index.exporters import ORGAN_ALIAS_TABLE
        for _organ, _L in ORGAN_ALIAS_TABLE.items():
            for _al in (_L.get("name") or []):
                if _al and _al in query and _al not in _tokens:
                    _tokens.append(_al)
    except Exception as _e:
        silent_exc(_e, where="_answer_module_relation.alias", level="debug")
    if not _tokens:
        return None

    # ② 双实体：查双向边 → 事件订阅关系回退 → 如实说无连接
    if len(_tokens) >= 2:
        _a, _b = _tokens[0], _tokens[1]
        _ta = _resolve_module_terms(mg, organs, _a)
        _tb = _resolve_module_terms(mg, organs, _b)
        _found = _edges_between(mg, _ta, _tb)
        if _found:
            return (_render_module_edges(f"{_a} ↔ {_b}", _found)
                    + _honest_effect_note(query) + _effect_section(_found, query))
        _rel = _answer_relation(events, _a, _b)
        if _rel:
            return _rel + (_honest_effect_note(query, no_edge=True) or "")
        return (f"我查了模块图：{_a} 与 {_b} 之间未找到直接连接边"
                f"（事件订阅表里也没有共同事件）。" + _honest_effect_note(query, no_edge=True))

    # ③ 单实体 + 「被谁调用」→ 查入边
    if re.search(r"被谁|谁调用|谁依赖|谁用到", query):
        _a = _tokens[0]
        _ta = _resolve_module_terms(mg, organs, _a)
        _ins = _edges_into(mg, _ta)
        if _ins:
            _parts = [_render_module_edge(_e) for _e in _ins[:5]]
            _more = "（仅列前 5 条）" if len(_ins) > 5 else ""
            return (f"我查了模块图：{_a} 有 {len(_ins)} 条入边{_more}——"
                    + "；".join(_parts) + "。")
        return f"我查了模块图：未找到指向 {_a} 的调用/注入边。"
    # ③b 单实体 + 关系词（如「肝吸收知识后会做什么」）→ 查该实体全部连边（双向）
    if len(_tokens) == 1:
        _a = _tokens[0]
        _ta = _resolve_module_terms(mg, organs, _a)
        _all = _edges_any(mg, _ta)
        # 让 event/inject/call 排在 import 前（digest 等业务边优先展示，import 噪声后移）
        _all.sort(key=lambda _e: 0 if _e.get("kind") != "import" else 1)
        if _all:
            return (_render_module_edges(f"{_a} 的相关连接", _all)
                    + _honest_effect_note(query) + _effect_section(_all, query))
        return (f"我查了模块图：{_a} 暂未登记到与其它模块的连接边。"
                + _honest_effect_note(query, no_edge=True))
    return None


def _answer_module_call(mg, mod_a, mod_b) -> str | None:
    """493.5-5 / 493.10-3：查两模块间是否有边，渲染含事件名与行号。"""
    if not mg:
        return None
    _found = []
    for _e in mg.get("edges", []) or []:
        _f = _e.get("from")
        _t = _e.get("to")
        if (_f == mod_a and _t == mod_b) or (_f == mod_b and _t == mod_a):
            _found.append(_e)
    if _found:
        return _render_module_edges(f"{mod_a} 与 {mod_b}", _found)
    return None


def _action_result_usable(r) -> bool:
    """493.10-8：调用方取 result 前必须确认「验证通过」，不能只看裸 ok。

    新契约（ENABLE_M49310_HONEST_RETURN 开）：ok 已等于验证通过，verified.ok 一致；
    旧契约（关）：ok=True 可能掩盖 verified.ok=False —— 故仍显式校验 verified.ok，
    两代契约下都不会拿坏数据拼答案。
    """
    if not isinstance(r, dict) or not r.get("ok"):
        return False
    _v = r.get("verified")
    if _v is None:
        return True          # 无验证信息（如 blocked 之外路径）→ 沿用 ok
    if isinstance(_v, dict) and _v.get("ok") is False:
        return False
    return True


def _answer_repeat() -> str | None:
    """493.6-4：再说一遍刚才的话 → lung.dialog_history 取最后一条回复。"""
    try:
        from nucleus.self_index.action_executor import execute_action
        _r = execute_action("lung.dialog_history", {"n": 1})
        if _action_result_usable(_r):
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
        if _action_result_usable(_r):
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
    _or: dict = {}
    _organs: list = []
    _events: dict = {}
    _mg = None
    # 493.9-1 真 bug 修复：原实现把四个数据源装进**单一大 try**，任一源异常
    # （如重启后 events()/module_graph 瞬时不可用）就直接 return 空 explain，
    # 表现为影子日志「胃在哪 elapsed_ms=0 / organ=null / would_answer=null」。
    # 现逐项独立容错：单个源失败只降级该源，器官清单在就能答位置。
    try:
        import config as _cfg
        _defensive = (bool(getattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", False))
                      and bool(getattr(_cfg, "ENABLE_M4939_FASTPATH_DEFENSIVE", False)))
    except Exception as _e:
        silent_exc(_e, where="self_query_fastpath.defensive_cfg", level="debug")
        _defensive = False
    if not _defensive:
        # 关 → 逐字回退 493.8 行为（单一大 try）
        try:
            from nucleus.self_index.store import SelfIndexStore
            _store = SelfIndexStore()
            _or = _store.organs_runtime() or {}
            _organs = _or.get("organs", []) or []
            _ev = _store.events() or {}
            _events = _ev.get("events", {}) or {}
            _mg = _load_module_graph_cached()
        except Exception as _e:
            silent_exc(_e, where="self_query_fastpath", level="debug")
            return _explain
    else:
        try:
            from nucleus.self_index.store import SelfIndexStore
            _store = SelfIndexStore()
            _or = _store.organs_runtime() or {}
            _organs = _or.get("organs", []) or []
        except Exception as _e:
            silent_exc(_e, where="self_query_fastpath.organs_runtime", level="debug")
        try:
            from nucleus.self_index.store import SelfIndexStore
            _ev = SelfIndexStore().events() or {}
            _events = _ev.get("events", {}) or {}
        except Exception as _e:
            silent_exc(_e, where="self_query_fastpath.events", level="debug")
        _mg = _load_module_graph_cached()   # 内部已自带容错
        # 器官清单彻底为空 → 无法做任何本地索引应答，诚实落空（不编）
        if not _organs:
            _explain["elapsed_ms"] = (_time.time() - _t0) * 1000
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

    # 1.5) ★493.10-1：关系问法优先消费模块图 —— 必须在位置/描述分支之前，
    #      否则「A 和 B 怎么连」会被分支 3/4 截走，答成「模块在代码路径…」。
    #      关总闸 → 不执行，逐字回 493.9（含原 2) 关系对分支）。
    _rel_ans = _answer_module_relation(_q, _mg, _events, _organs)
    if _rel_ans:
        _set(_rel_ans, "module.relation", 0.85, intent="relation")
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
        # ★493.11-1：补 493.10-2 遗漏 —— 位置分支走 _find_organ（按文件路径匹配），
        #   模块名如 PulseCortex 会被绑成「大脑皮层」。先判模块 token，是则答模块位置，
        #   不走器官描述。复用 493.10 总闸（关 → 逐字回 493.9）。
        if _m49310_on():
            from nucleus.self_index.exporters import is_module_token
            if is_module_token(_kw):
                _mo = _find_module(_mg, _kw)
                if _mo:
                    _set(f"{_kw} 模块在代码路径 {_mo.get('file')}（模块 {_mo.get('module')}）。",
                         "module.location", 0.85, intent="location")
                    return _explain
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
        # 493.11-1：分支4 同理，模块 token 先答模块位置，不被器官化
        if _m49310_on():
            from nucleus.self_index.exporters import is_module_token
            if is_module_token(_w):
                _mo = _find_module(_mg, _w)
                if _mo:
                    _set(f"{_w} 模块在代码路径 {_mo.get('file')}（模块 {_mo.get('module')}）。",
                         "module.location", 0.85, intent="location")
                    return _explain
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
            # 493.10-4：删除指向未来批次的过期话术 —— 模块图已落盘
            # 468 节点 / 5282 边，直接给边结果，无连接时如实说没找到。
            _set(f"我查了模块图：{_o.get('name')} 未找到直接连接边。",
                 "relation.none", 0.6, organ=_o.get("name"), intent="relation")
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
        if re.search(r"忙|在途|任务|还在跑|有没有.*做|多少.*任务|并发数|并发|负载", _q):
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
                 f"未找到直接连接边。",
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
