# -*- coding: utf-8 -*-
"""
IdentityKnowledgeManager.py —— 身份知识管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 身份相关知识的专门管理
机制: 基于IdentityKnowledgeManager类实现，包含10个核心方法
定位: 记忆身份层
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


try:
    from nucleus.logger import get_module_logger
    _logger = get_module_logger("IdentityKnowledgeManager")
except Exception:  # pragma: no cover - 日志不可用时静默降级
    _logger = None


def _log(level: str, msg: str) -> None:
    if _logger is None:
        return
    try:
        getattr(_logger, level)(msg)
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.IdentityKnowledgeManager::_log L37")


# ==================== 关系代数 ====================

# 关系 → 反向关系（A 是 B 的父亲 ⇒ B 是 A 的儿子/女儿；性别未知时用「孩子」）
RELATION_INVERSE: dict[str, str] = {
    "父亲": "孩子", "母亲": "孩子", "爸爸": "孩子", "妈妈": "孩子",
    "儿子": "父亲或母亲", "女儿": "父亲或母亲", "孩子": "父亲或母亲",
    "祖父": "孙子或孙女", "奶奶": "孙子或孙女", "爷爷": "孙子或孙女",
    "外婆": "外孙或外孙女", "外公": "外孙或外孙女",
    "孙子": "祖父或祖母", "孙女": "祖父或祖母",
    "哥哥": "弟弟或妹妹", "姐姐": "弟弟或妹妹",
    "弟弟": "哥哥或姐姐", "妹妹": "哥哥或姐姐",
    "丈夫": "妻子", "妻子": "丈夫",
    "老师": "学生", "学生": "老师",
    "朋友": "朋友", "同事": "同事", "创造者": "被创造者", "被创造者": "创造者",
}

# 关系组合：(A 对 B 的关系, B 对 C 的关系) → A 对 C 的关系
RELATION_COMPOSE: dict[tuple[str, str], str] = {
    ("父亲", "父亲"): "祖父",
    ("父亲", "母亲"): "祖母",
    ("母亲", "父亲"): "外祖父",
    ("母亲", "母亲"): "外祖母",
    ("父亲", "儿子"): "自己或兄弟",  # 保守标注，不做强推理
    ("父亲", "女儿"): "自己或姐妹",
    ("创造者", "孩子"): "创造者",
}

# 关系词表（用于抽取），按长度降序匹配，避免「父亲」被「父」抢先
RELATION_WORDS: list[str] = sorted(
    RELATION_INVERSE.keys(), key=lambda w: -len(w)
)

# 第一/二人称归一：都指向框架自己（「我」「你」在用户口中都指框架）
SELF_ALIASES = {"我", "你", "曈曈", "瞳瞳", "通通", "tongtong", "框架", "你这家伙"}


def _is_self(name: str) -> bool:
    _n = str(name or "").strip()
    return _n in SELF_ALIASES or _n == "自己"


# ==================== 抽取规则 ====================

# 规则1（别名链）：「小林就是<CREATOR>也就是你的父亲」「A即B」
_RE_ALIAS_SPLIT = re.compile(r"(?:就是|也就是|即|亦即|aka)")

# 规则2（正向关系）：「小林是我的父亲」「<CREATOR>是我父亲」
_RE_REL_FORWARD = re.compile(
    r"([\u4e00-\u9fff\w·]{1,12}?)\s*(?:是|为|就是|乃)\s*"
    r"([\u4e00-\u9fff\w·]{1,12}?)\s*(?:的)?\s*"
    r"(" + "|".join(RELATION_WORDS) + r")"
)

# 规则3（反向关系）：「我的父亲是小林」「你父亲叫<CREATOR>」
_RE_REL_BACKWARD = re.compile(
    r"([\u4e00-\u9fff\w·]{1,12}?)\s*(?:的)?\s*"
    r"(" + "|".join(RELATION_WORDS) + r")\s*"
    r"(?:是|为|叫|名叫|叫做|就是)\s*([\u4e00-\u9fff\w·]{1,12})"
)

# 人名清洗：去掉常见虚词与标点
_RE_NAME_CLEAN = re.compile(r"^[的是了和与跟给把被就也就\s，。！？、,.!?]*(.*?)[的是了\s，。！？、,.!?]*$")


def _clean_name(raw: str) -> str:
    """清洗抽取到的人名/关系目标。"""
    _n = str(raw or "").strip()
    _n = _n.strip("的了和与跟给把被就也，。！？、,.!?· ")
    # 去掉前置称谓修饰（"我的"、"你的"）
    _n = re.sub(r"^[我你他她它其]+的", "", _n)
    return _n.strip()


def _clean_target(raw: str) -> str:
    """清洗「关系目标」。

    「小林就是<CREATOR>也就是你的父亲」里，正则会把目标抓成
    「<CREATOR>也就是你」——这是别名链的中间段被误当成人名。
    处理：先按别名连接词取最后一段，再去掉关系词，最后归一自称。
    """
    _n = str(raw or "").strip()
    # 1) 别名链残留：<CREATOR>也就是你 → 你
    for _sep in ("也就是", "就是", "即", "亦即"):
        if _sep in _n:
            _n = _n.split(_sep)[-1]
    # 2) 去掉关系词及其前后的「的」（你的父亲 → 你）
    _n = re.sub(r"[的]?(" + "|".join(RELATION_WORDS) + r")[的]?", "", _n)
    _n = _clean_name(_n)
    # 3) 自称归一：我/你/曈曈… 都指向框架自己
    if (not _n) or _is_self(_n):
        return "自己"
    return _n


# ★第九批：测试隔离用默认目录（tmp/test_isolation.redirect_all 会重定向到 tmp/test_data）
_ISO_BASE_DIR: str | None = None


class IdentityKnowledgeManager:
    """人物身份知识库（单例使用，见 get_identity_manager）。"""

    # 高置信声明的阈值：达到即可视为「稳定知识」（写 L3、直接用于回答）
    CONFIRMED_THRESHOLD = 0.75
    # 声明来源的默认置信度
    DEFAULT_CLAIM_CONFIDENCE = 0.8
    # 每次提到同一关系，置信度的小幅累加上限
    MAX_CONFIDENCE = 0.98

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.RLock()
        # ★第十一批 批次B 修复：_ISO_BASE_DIR 必须**优先于**默认目录生效。
        #   原写法先赋默认目录，再判 `base_dir is None and _ISO_BASE_DIR`（恒假），
        #   导致测试隔离失效、测试把身份知识写到生产 data/identity_knowledge.json。
        if base_dir is None and _ISO_BASE_DIR:
            base_dir = _ISO_BASE_DIR
        if base_dir is None:
            # ★第十一批 批次C 修复：本文件位于 <root>/nucleus/mnemosyne/，需上溯
            #   **3 层**才是项目根；原写法只上溯 2 层，落到 <root>/nucleus/data，
            #   与框架约定的 <root>/data 不一致（实测 _save_path 偏到 nucleus/data）。
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data")
        self._base_dir = base_dir
        self._save_path = os.path.join(base_dir, "identity_knowledge.json")
        # {name: {"aliases": [...], "relations": [ {...} ], "updated": ts}}
        self._people: dict[str, dict[str, Any]] = {}
        # 冲突待确认队列：[{...claim, "conflict_with": {...}}]
        self._pending: list[dict[str, Any]] = []
        self._load()

    # ==================== 抽取 ====================

    @staticmethod
    def split_alias_chain(text: str) -> list[str]:
        """把「小林就是<CREATOR>也就是你的父亲」切成候选片段。

        返回按出现顺序的片段列表（含尾部关系短语），供 extract_claims 继续解析。
        """
        if not text:
            return []
        _parts = [p.strip() for p in _RE_ALIAS_SPLIT.split(text) if p and p.strip()]
        return _parts

    def extract_claims(self, text: str, source: str = "对话") -> list[dict[str, Any]]:
        """从自然语言文本中抽取身份声明。

        支持三种说法：
          ① 别名链：小林就是<CREATOR>（= 同一个人）
          ② 正向：小林是我的父亲
          ③ 反向：我的父亲是小林

        Returns: [{"person":..., "relation"/"alias":..., "target":..., "raw":...}]
        """
        if not text or not isinstance(text, str):
            return []
        _claims: list[dict[str, Any]] = []
        _text = text.strip().replace("，", ",").replace("。", ",")

        # ---- ① 别名链：A就是B也就是C（优先处理，因为它决定了主名）----
        #    「小林就是<CREATOR>也就是你的父亲」切成 ['小林','<CREATOR>','你的父亲']：
        #     人名部分互认别名，末尾的关系短语则生成一条「主名 → 关系」声明。
        _parts = self.split_alias_chain(_text)
        _chain_names: list[str] = []
        if len(_parts) >= 2:
            for _p in _parts:
                _cand = _clean_name(_p)
                if not _cand:
                    continue
                if any(_w in _cand for _w in RELATION_WORDS):
                    continue  # 是关系短语，不是人名
                if len(_cand) > 12:
                    continue
                _chain_names.append(_cand)
            for _i in range(len(_chain_names) - 1):
                _claims.append({
                    "kind": "alias",
                    "person": _chain_names[_i],
                    "alias": _chain_names[_i + 1],
                    "raw": "就是".join(_chain_names[_i:_i + 2]),
                    "source": source,
                })
            # 末段是关系短语（"你的父亲"）→ 主名对该关系
            _last = _parts[-1]
            if _chain_names and any(_w in _last for _w in RELATION_WORDS):
                _rel_match = re.search("|".join(RELATION_WORDS), _last)
                if _rel_match:
                    _claims.append({
                        "kind": "relation",
                        "person": _chain_names[0],
                        "relation": _rel_match.group(0),
                        "target": _clean_target(_last),
                        "raw": _text,
                        "source": source,
                    })

        # ---- ② 正向关系：A 是 B 的 Z ----
        for _m in _RE_REL_FORWARD.finditer(_text):
            _person = _clean_name(_m.group(1))
            _target = _clean_target(_m.group(2))
            _rel = _m.group(3)
            if not _person or not _rel:
                continue
            if _is_self(_person):
                continue  # "我是你父亲" → 说话人是父亲，需反向处理（见下）
            _claims.append({
                "kind": "relation",
                "person": _person,
                "relation": _rel,
                "target": _target,
                "raw": _m.group(0),
                "source": source,
            })

        # ---- ③ 反向关系：B 的 Z 是 A（"我的父亲是小林"）----
        for _m in _RE_REL_BACKWARD.finditer(_text):
            _target = _clean_target(_m.group(1))
            _rel = _m.group(2)
            _person = _clean_name(_m.group(3))
            if not _person or not _rel:
                continue
            _claims.append({
                "kind": "relation",
                "person": _person,
                "relation": _rel,
                "target": _target,
                "raw": _m.group(0),
                "source": source,
            })

        # ---- 去重：同一 (类型, 人物, 关系, 目标) 只留一条 ----
        _seen: set[tuple] = set()
        _uniq: list[dict[str, Any]] = []
        for _c in _claims:
            _key = (_c.get("kind"), _c.get("person"),
                    _c.get("relation") or _c.get("alias"), _c.get("target", ""))
            if _key in _seen:
                continue
            _seen.add(_key)
            _uniq.append(_c)
        return _uniq

    # ==================== 写入（含冲突检测） ====================

    def add_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        """写入一条声明，返回处理结果 {"status": ..., ...}。

        status:
          added      —— 新知识，已写入
          reinforced  —— 与已有知识一致，提升置信度
          pending     —— 与已有知识冲突，进入待确认队列（不覆盖）
          ignored     —— 无效声明（字段缺失/自指）
        """
        if not claim or not isinstance(claim, dict):
            return {"status": "ignored", "reason": "空声明"}
        _kind = claim.get("kind")
        _person = _clean_name(claim.get("person", ""))
        if not _person:
            return {"status": "ignored", "reason": "缺少人物名"}

        with self._lock:
            _entry = self._people.setdefault(
                _person, {"aliases": [], "relations": [], "updated": time.time()})
            _now = time.time()

            # ---------- 别名 ----------
            if _kind == "alias":
                _alias = _clean_name(claim.get("alias", ""))
                if not _alias or _alias == _person:
                    return {"status": "ignored", "reason": "无效别名"}
                if _alias not in _entry["aliases"]:
                    _entry["aliases"].append(_alias)
                    _entry["updated"] = _now
                    # 别名双向可见：<CREATOR> 也能查到 小林
                    _a_entry = self._people.setdefault(
                        _alias, {"aliases": [], "relations": [], "updated": _now})
                    if _person not in _a_entry["aliases"]:
                        _a_entry["aliases"].append(_person)
                    _a_entry["updated"] = _now
                    self.save()
                    _log("info", f"[身份知识] 新增别名: {_person} = {_alias}")
                    return {"status": "added", "person": _person, "alias": _alias}
                return {"status": "reinforced", "person": _person, "alias": _alias}

            # ---------- 关系 ----------
            if _kind != "relation":
                return {"status": "ignored", "reason": f"未知声明类型: {_kind}"}
            _rel = str(claim.get("relation") or "").strip()
            _target = _clean_name(claim.get("target", "")) or "自己"
            if not _rel:
                return {"status": "ignored", "reason": "缺少关系"}
            _conf = float(claim.get("confidence", self.DEFAULT_CLAIM_CONFIDENCE) or 0.0)
            _conf = max(0.0, min(self.MAX_CONFIDENCE, _conf))

            for _r in _entry["relations"]:
                if _r.get("relation") == _rel and _r.get("target") == _target:
                    # 一致 → 提升置信度（多次确认趋近上限）
                    _r["confidence"] = min(self.MAX_CONFIDENCE,
                                           float(_r.get("confidence", 0.0)) + 0.05)
                    _r["last_confirmed"] = _now
                    _r["hits"] = int(_r.get("hits", 1)) + 1
                    _entry["updated"] = _now
                    self.save()
                    _log("debug", f"[身份知识] 关系再次确认: {_person} 是 {_target} 的 {_rel}"
                                  f"(置信→{_r['confidence']:.2f})")
                    return {"status": "reinforced", "person": _person,
                            "relation": _rel, "confidence": _r["confidence"]}
                if _r.get("target") == _target and _r.get("relation") != _rel:
                    # 冲突 → 待确认，绝不覆盖
                    _pend = {
                        "person": _person, "relation": _rel, "target": _target,
                        "confidence": _conf, "source": claim.get("source", "对话"),
                        "raw": claim.get("raw", ""), "detected_at": _now,
                        "conflict_with": dict(_r),
                    }
                    self._pending.append(_pend)
                    self._pending = self._pending[-100:]
                    self.save()
                    _log("warning",
                         f"[身份知识] 冲突待确认: 「{_person} 是 {_target} 的 {_rel}」"
                         f" 与已有「{_person} 是 {_target} 的 {_r.get('relation')}」矛盾，"
                         f"已挂起未覆盖")
                    return {"status": "pending", "person": _person,
                            "relation": _rel, "conflict_with": dict(_r)}

            _entry["relations"].append({
                "relation": _rel, "target": _target, "confidence": _conf,
                "source": claim.get("source", "对话"), "raw": claim.get("raw", ""),
                "first_seen": _now, "last_confirmed": _now, "hits": 1,
            })
            _entry["updated"] = _now
            self.save()
            _log("info", f"[身份知识] 新增关系: {_person} 是 {_target} 的 {_rel}"
                         f"(置信{_conf:.2f}, 来源={claim.get('source', '对话')})")
            return {"status": "added", "person": _person, "relation": _rel,
                    "target": _target, "confidence": _conf}

    def ingest_text(self, text: str, source: str = "对话") -> list[dict[str, Any]]:
        """从一段文本抽取并写入所有身份声明，返回逐条处理结果。"""
        _claims = self.extract_claims(text, source=source)
        return [self.add_claim(_c) for _c in _claims]

    # ==================== 查询 ====================

    def get_person(self, name: str) -> dict[str, Any] | None:
        """取人物条目（支持别名命中）。"""
        if not name:
            return None
        _key = self.resolve_name(name)
        with self._lock:
            if _key not in self._people:
                return None
            return json.loads(json.dumps(self._people[_key], ensure_ascii=False))

    def resolve_name(self, name: str) -> str:
        """把别名解析到主名（未收录则原样返回）。"""
        _n = _clean_name(name)
        with self._lock:
            if _n in self._people:
                return _n
            for _k, _v in self._people.items():
                if _n in (_v.get("aliases") or []):
                    return _k
        return _n

    def get_relation(self, person: str, target: str = "自己") -> dict[str, Any] | None:
        """查 A 对 B 的关系；无直接关系时做一次关系推理。"""
        _p = self.resolve_name(person)
        _t = self.resolve_name(target)
        with self._lock:
            _entry = self._people.get(_p)
            if not _entry:
                return None
            for _r in _entry.get("relations", []):
                if _r.get("target") in (_t, target, "自己") and _r.get("target") == _t:
                    return dict(_r)
            # 目标归一：别名/自己
            for _r in _entry.get("relations", []):
                if self.resolve_name(_r.get("target", "")) == _t:
                    return dict(_r)
            return self._infer_relation_locked(_p, _t)

    def _infer_relation_locked(self, person: str, target: str) -> dict[str, Any] | None:
        """关系推理：A→B→C 两段关系合成一段。"""
        _mid = None
        _first = None
        for _r in self._people.get(person, {}).get("relations", []):
            _b = self.resolve_name(_r.get("target", ""))
            if _b == target:
                continue
            for _r2 in self._people.get(_b, {}).get("relations", []):
                if self.resolve_name(_r2.get("target", "")) == target:
                    _composed = RELATION_COMPOSE.get(
                        (_r.get("relation"), _r2.get("relation")))
                    if _composed:
                        _mid, _first = _r2, _r
                        break
            if _mid:
                break
        if not _mid:
            return None
        return {
            "relation": RELATION_COMPOSE.get((_first.get("relation"),
                                              _mid.get("relation")), "亲属"),
            "target": target,
            "confidence": round(min(float(_first.get("confidence", 0.5)),
                                    float(_mid.get("confidence", 0.5))) * 0.9, 2),
            "inferred": True,
            "via": f"{person}→{_first.get('target')}→{target}",
        }

    def describe(self, name: str) -> str:
        """生成一句自然人话描述，供回答直接引用（无知识时返回空串）。"""
        _key = self.resolve_name(name)
        with self._lock:
            _entry = self._people.get(_key)
            if not _entry:
                return ""
            _alias = [a for a in (_entry.get("aliases") or []) if a != _key]
            _name_part = f"{_key}（{'、'.join(_alias)}）" if _alias else _key
            _rels = sorted((_entry.get("relations") or []),
                           key=lambda r: -float(r.get("confidence", 0)))
            if not _rels:
                return f"{_name_part}——我知道这个名字，但还没弄清我们的关系。"
            _r = _rels[0]
            _t = _r.get("target", "自己")
            _t_name = "我" if _t in ("自己", "我", "你") else _t
            _suffix = "（这是推出来的）" if _r.get("inferred") else ""
            _rel = _r.get("relation")
            # ★第161批 刀9（T-对话模板拼接断裂-1）：relation 缺失/非法时兜底为完整句。
            #   根因：原实现直接插值 _r.get('relation')，脏值/缺失时输出
            #   「小林是我的您」这类断裂句（9.2 禁输出）。
            #   有效性判据**复用本模块已有的 RELATION_WORDS**（唯一真相源，不另立词表）。
            try:
                import config as _cfg_k9
                _fb_on = bool(getattr(_cfg_k9, "IDENTITY_RELATION_FALLBACK_COMPLETE", True))
            except Exception as _k9_e:
                silent_exc(_k9_e, where="IdentityKnowledgeManager.describe 开关读取")
                _fb_on = True
            _rel_text = str(_rel or "").strip()
            if _fb_on and (_rel_text not in RELATION_WORDS):
                # 缺失或不在关系词表内 → 用泛称兜底，保证输出是完整句
                _log("warning",
                     f"身份关系词非法（person={_key} relation={_rel_text!r}），"
                     f"已兜底为泛称；脏值不入库请复核抽取来源")
                _rel_text = "家人"
            return f"{_name_part}是{_t_name}的{_rel_text}{_suffix}。"

    # ==================== 路径与导出 ====================

    @staticmethod
    def person_path(name: str) -> str:
        """统一人物知识路径：`/人物/{人名}`（QICA 建议路径即按此生成）。"""
        return f"/人物/{_clean_name(name)}"

    def export_nodes(self) -> list[dict[str, Any]]:
        """导出为可写入知识树的节点结构（由调用方决定写不写、写哪一层）。

        只导出「高置信 + 已确认」的关系，避免把待确认的猜测写进长期记忆。
        """
        _out: list[dict[str, Any]] = []
        with self._lock:
            for _name, _entry in self._people.items():
                _rels = [r for r in (_entry.get("relations") or [])
                         if float(r.get("confidence", 0)) >= self.CONFIRMED_THRESHOLD]
                if not _rels:
                    continue
                _out.append({
                    "space_path": self.person_path(_name),
                    "value": self.describe(_name),
                    "keywords": [_name, *list(_entry.get("aliases") or [])],
                    "confidence": max(float(r.get("confidence", 0)) for r in _rels),
                    "suggested_level": "L3",
                })
        return _out

    def get_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._pending, ensure_ascii=False))

    def resolve_pending(self, index: int, accept_new: bool) -> dict[str, Any]:
        """人工裁决待确认冲突：accept_new=True 采用新声明，否则保留旧的。"""
        with self._lock:
            if not (0 <= index < len(self._pending)):
                return {"ok": False, "reason": "索引越界"}
            _p = self._pending.pop(index)
            if accept_new:
                _entry = self._people.setdefault(
                    _p["person"], {"aliases": [], "relations": [], "updated": time.time()})
                # 先移除旧关系，再写入新关系
                _entry["relations"] = [
                    r for r in _entry.get("relations", [])
                    if not (r.get("target") == _p["target"]
                            and r.get("relation") == _p["conflict_with"].get("relation"))
                ]
                _entry["relations"].append({
                    "relation": _p["relation"], "target": _p["target"],
                    "confidence": _p.get("confidence", 0.8),
                    "source": f"{_p.get('source', '对话')}+人工裁决",
                    "raw": _p.get("raw", ""), "first_seen": time.time(),
                    "last_confirmed": time.time(), "hits": 1,
                })
                _entry["updated"] = time.time()
            self.save()
            return {"ok": True, "accepted_new": accept_new, "person": _p["person"]}

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "people": len(self._people),
                "relations": sum(len(v.get("relations", [])) for v in self._people.values()),
                "pending": len(self._pending),
                "names": list(self._people.keys())[:20],
            }

    # ==================== 持久化 ====================

    def _load(self) -> None:
        try:
            if os.path.exists(self._save_path):
                _raw = safe_read_json(self._save_path, default={})
                self._people = (_raw or {}).get("people", {}) or {}
                self._pending = (_raw or {}).get("pending", []) or []
        except Exception as _e:
            _log("warning", f"[身份知识] 加载失败，以空库启动: {_e}")
            self._people, self._pending = {}, []

    def save(self) -> None:
        try:
            os.makedirs(self._base_dir, exist_ok=True)
            with self._lock:
                _dump = {"version": 1, "updated": time.time(),
                         "people": self._people, "pending": self._pending}
            _tmp = self._save_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(_dump, f, ensure_ascii=False, indent=1)
            os.replace(_tmp, self._save_path)
        except Exception as _e:
            _log("warning", f"[身份知识] 落盘失败（内存数据未丢）: {_e}")

    def reset(self) -> None:
        """仅供测试：清空内存与磁盘。"""
        with self._lock:
            self._people, self._pending = {}, []
        try:
            if os.path.exists(self._save_path):
                os.remove(self._save_path)
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.IdentityKnowledgeManager::reset L569")


# ==================== 单例 ====================

_manager: IdentityKnowledgeManager | None = None
_manager_lock = threading.Lock()


def get_identity_manager(base_dir: str | None = None) -> IdentityKnowledgeManager:
    """获取 IdentityKnowledgeManager 单例。"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = IdentityKnowledgeManager(base_dir=base_dir)
    return _manager


