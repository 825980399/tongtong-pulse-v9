# -*- coding: utf-8 -*-
"""
ExperienceTransfer.py —— 经验迁移

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 跨任务经验迁移与复用
机制: 基于ExperienceTransfer类实现，包含10个核心方法
定位: 进化学习层
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from typing import Any
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


__all__ = ["ExperienceTransfer", "get_experience_transfer"]

_DEFAULT_STORAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "evolution", "experience_transfer.json")

# 中文分词兜底：2-gram（无需第三方分词库）
_RE_CJK = re.compile(r'[\u4e00-\u9fff]{2,}')
_RE_ASCII = re.compile(r'[A-Za-z_][A-Za-z0-9_\.]{1,}')
_RE_NUM = re.compile(r'\d+')


def _tokenize(text: str) -> set[str]:
    """问题特征抽取：中文 2-gram + ASCII 标识符 + 数字（小写归一）。"""
    _t = str(text or "")
    _out: set[str] = set()
    for _seg in _RE_CJK.findall(_t):
        for _i in range(len(_seg) - 1):
            _out.add(_seg[_i:_i + 2])
    for _w in _RE_ASCII.findall(_t):
        _out.add(_w.lower())
    for _n in _RE_NUM.findall(_t):
        _out.add(f"#{_n}")
    return _out


class ExperienceTransfer:
    """问题解决经验迁移（线程安全，落盘 JSON）。"""

    def __init__(self, config: dict[str, Any] | None = None,
                 storage_path: str | None = None, log_fn=None):
        _cfg = config or {}
        self._max_experiences = int(_cfg.get("max_experiences", 500))
        self._match_threshold = float(_cfg.get("match_threshold", 0.60))
        self._decay_per_day = float(_cfg.get("decay_per_day", 0.02))
        self._min_weight = float(_cfg.get("min_weight", 0.1))
        self._success_increment = float(_cfg.get("success_increment", 0.15))
        self._storage = storage_path or _cfg.get("storage_path") or _DEFAULT_STORAGE
        self._log_fn = log_fn
        self._lock = threading.RLock()
        self._experiences: dict[str, dict[str, Any]] = {}
        # 统计
        self._total_recorded = 0
        self._total_matched = 0
        self._total_applied = 0
        self._total_success = 0
        self._total_fail = 0
        self._total_decayed = 0
        self._loaded = False

    # ========== 生命周期 ==========

    def load(self) -> int:
        """从磁盘恢复经验库（不存在/损坏返回 0，绝不抛异常）。"""
        with self._lock:
            if self._loaded:
                return len(self._experiences)
            self._loaded = True
            try:
                if not os.path.exists(self._storage):
                    return 0
                _data = safe_read_json(self._storage, default={})
                _list = _data.get("experiences", []) if isinstance(_data, dict) else []
                for _e in _list:
                    _eid = _e.get("id")
                    if _eid:
                        self._experiences[_eid] = _e
                self._log("INFO", f"[经验迁移] 恢复经验库: {len(self._experiences)}条 "
                                  f"（{self._storage}）")
            except Exception as _e:
                self._log("DEBUG", f"[经验迁移] 经验库恢复失败(空库启动): {_e}")
            return len(self._experiences)

    def save(self) -> bool:
        """落盘（原子写：临时文件 + 替换）。"""
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self._storage), exist_ok=True)
                _tmp = f"{self._storage}.tmp"
                _payload = {
                    "version": 1,
                    "updated_at": time.time(),
                    "total_recorded": self._total_recorded,
                    "experiences": list(self._experiences.values()),
                }
                with open(_tmp, "w", encoding="utf-8") as _f:
                    json.dump(_payload, _f, ensure_ascii=False, indent=2)
                os.replace(_tmp, self._storage)
                return True
            except Exception as _e:
                self._log("DEBUG", f"[经验迁移] 落盘失败(不影响主流程): {_e}")
                return False

    # ========== 经验沉淀 ==========

    def record(self, problem: Any, strategy: str, domain: str = "",
               source: str = "code_learner", success: bool = True,
               persist: bool = True) -> str:
        """沉淀一条「问题模式 → 修复策略」经验。

        Args:
            problem: 问题文本 或 含 description/organ/method/type 的 dict
            strategy: 修复策略文本（建议要做的事）
            domain: 领域（器官/模块名）
            source: 来源（code_learner / evolution / manual）
            success: 该策略本次是否确认有效
            persist: 是否立即落盘（批量录入可置 False，最后统一 save）

        Returns:
            经验ID；未启用/异常返回 ""
        """
        if not strategy:
            return ""
        _text, _domain, _extra = self._normalize_problem(problem)
        _domain = domain or _domain
        _features = sorted(_tokenize(_text + " " + _extra))
        if not _features:
            return ""

        with self._lock:
            self.load()
            _now = time.time()
            # 同源同问题的经验合并（命中即强化，避免重复膨胀）
            _sig = "|".join(_features[:24])
            for _e in self._experiences.values():
                if _e.get("signature") == _sig and _e.get("strategy") == strategy:
                    _e["hit_count"] = int(_e.get("hit_count", 0)) + 1
                    if success:
                        _e["success_count"] = int(_e.get("success_count", 0)) + 1
                        _e["weight"] = min(1.0, float(_e.get("weight", 0.5)) + self._success_increment)
                    _e["last_used_at"] = _now
                    _eid = str(_e["id"])
                    self._log("INFO", f"[经验迁移] 经验强化: {_eid} "
                                      f"命中{_e['hit_count']}次 权重={_e['weight']:.2f}")
                    if persist:
                        self.save()
                    return _eid

            _eid = f"exp_{int(_now)}_{random.randint(1000, 9999)}"
            self._experiences[_eid] = {
                "id": _eid,
                "domain": _domain,
                "problem_text": _text[:300],
                "features": _features,
                "signature": _sig,
                "strategy": strategy[:1000],
                "source": source,
                "created_at": _now,
                "last_hit_at": _now,
                "last_used_at": _now,
                "hit_count": 1 if success else 0,
                "success_count": 1 if success else 0,
                "fail_count": 0,
                "weight": 0.6 if success else 0.4,
            }
            self._total_recorded += 1
            self._prune_if_needed()
            self._log("INFO", f"[经验迁移] 新经验入库: {_eid} 领域={_domain} "
                              f"来源={source} 特征{len(_features)}个 "
                              f"策略={strategy[:40]}")
            if persist:
                self.save()
            return _eid

    # ========== 经验复用 ==========

    def match(self, problem: Any, domain: str = "", top_k: int = 3,
              cross_domain: bool = True) -> list[dict[str, Any]]:
        """匹配可复用经验（跨领域优先：同领域加权，异领域不排除）。

        Returns:
            [{经验字段..., "match_score": float}]，按匹配度降序；
            低于阈值的经验不返回。
        """
        with self._lock:
            self.load()
            if not self._experiences:
                return []
            _text, _pdomain, _extra = self._normalize_problem(problem)
            _domain = domain or _pdomain
            _qf = _tokenize(_text + " " + _extra)
            if not _qf:
                return []

            _now = time.time()
            _scored: list[tuple[float, dict]] = []
            for _e in self._experiences.values():
                _ef = set(_e.get("features") or [])
                if not _ef:
                    continue
                # 重叠系数（overlap coefficient）：交集 / min(查询集, 经验集)
                #   ★为何不用 Jaccard：问题文本长短差异大，Jaccard 被经验里的
                #     长尾特征稀释，短问题几乎永远匹配不上；重叠系数更稳健，
                #     且天然满足"经验比问题更详细"的真实场景。
                #   下限保护：交集 < 2 视为偶然撞词，不命中。
                _inter = len(_qf & _ef)
                if _inter < 2:
                    continue
                _sim = _inter / max(1, min(len(_qf), len(_ef)))
                # 领域加成：同领域 ×1.15，跨领域不惩罚（举一反三的核心）
                if _domain and _e.get("domain") and _e.get("domain") == _domain:
                    _sim *= 1.15
                elif not cross_domain:
                    continue
                # 权重加成：历史成功率高的经验优先
                _w = float(_e.get("weight", 0.5))
                _hc = int(_e.get("hit_count", 0))
                _reliability = _w * (1.0 + min(0.3, 0.05 * _hc))
                _score = min(1.0, _sim * 0.75 + _reliability * 0.25)
                if _score < self._match_threshold:
                    continue
                _scored.append((_score, _e))

            _scored.sort(key=lambda x: x[0], reverse=True)
            _out = []
            for _s, _e in _scored[:top_k]:
                _item = dict(_e)
                _item["match_score"] = round(_s, 3)
                _out.append(_item)
                _e["last_hit_at"] = _now
                _e["hit_count"] = int(_e.get("hit_count", 0)) + 1
                self._total_matched += 1
                self._log("INFO", f"[经验迁移] 经验命中: {_e.get('id')} "
                                  f"匹配度={_s:.2f} 领域={_e.get('domain')} "
                                  f"策略={str(_e.get('strategy',''))[:40]}")
            return _out

    def get_strategy(self, problem: Any, domain: str = "") -> str:
        """最简接入：返回最佳复用策略文本（无命中返回 ""）。"""
        _hits = self.match(problem, domain=domain, top_k=1)
        return str(_hits[0].get("strategy", "")) if _hits else ""

    def feedback(self, experience_id: str, success: bool) -> bool:
        """复用结果反馈：成功加权、失败降权（可追溯闭环）。"""
        with self._lock:
            _e = self._experiences.get(experience_id)
            if not _e:
                return False
            _w = float(_e.get("weight", 0.5))
            if success:
                _e["success_count"] = int(_e.get("success_count", 0)) + 1
                _e["weight"] = min(1.0, _w + self._success_increment)
                self._total_success += 1
            else:
                _e["fail_count"] = int(_e.get("fail_count", 0)) + 1
                _e["weight"] = max(self._min_weight, _w - self._success_increment * 1.5)
                self._total_fail += 1
            _e["last_used_at"] = time.time()
            self._log("INFO", f"[经验迁移] 复用反馈: {experience_id} "
                              f"{'成功' if success else '失败'} 权重{_w:.2f}→{_e['weight']:.2f}")
            if _e["weight"] <= self._min_weight and int(_e.get("fail_count", 0)) >= 3:
                self._experiences.pop(experience_id, None)
                self._log("INFO", f"[经验迁移] 低质经验淘汰: {experience_id}")
            else:
                self.save()
            return True

    # ========== 衰减与淘汰 ==========

    def decay(self, now: float | None = None) -> int:
        """长期未命中的经验自动降权（按天），低于阈值淘汰。

        Returns:
            本次淘汰数量
        """
        with self._lock:
            self.load()
            _now = now or time.time()
            _removed = 0
            for _eid, _e in list(self._experiences.items()):
                _last = float(_e.get("last_hit_at", _e.get("created_at", _now)))
                _days = max(0.0, (_now - _last) / 86400.0)
                if _days < 1.0:
                    continue
                _new_w = float(_e.get("weight", 0.5)) - self._decay_per_day * _days
                if _new_w <= self._min_weight:
                    self._experiences.pop(_eid, None)
                    _removed += 1
                    self._total_decayed += 1
                else:
                    _e["weight"] = round(_new_w, 4)
            if _removed:
                self._log("INFO", f"[经验迁移] 经验衰减: 淘汰{_removed}条过期经验")
                self.save()
            return _removed

    # ========== 统计 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            _domains: dict[str, int] = {}
            for _e in self._experiences.values():
                _d = str(_e.get("domain", "unknown"))
                _domains[_d] = _domains.get(_d, 0) + 1
            return {
                "total_experiences": len(self._experiences),
                "total_recorded": self._total_recorded,
                "total_matched": self._total_matched,
                "total_applied": self._total_applied,
                "feedback_success": self._total_success,
                "feedback_fail": self._total_fail,
                "total_decayed": self._total_decayed,
                "domains": _domains,
                "match_threshold": self._match_threshold,
                "storage": self._storage,
            }

    def list_experiences(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            self.load()
            _all = sorted(self._experiences.values(),
                          key=lambda e: float(e.get("weight", 0)), reverse=True)
            return [dict(e) for e in _all[:limit]]

    # ========== 内部 ==========

    @staticmethod
    def _normalize_problem(problem: Any) -> tuple[str, str, str]:
        """把任意问题输入归一为 (文本, 领域, 附加特征串)。"""
        if isinstance(problem, dict):
            _text = " ".join(str(problem.get(k, "")) for k in
                             ("description", "problem", "summary", "message", "title")
                             if problem.get(k))
            _domain = str(problem.get("organ") or problem.get("domain")
                          or problem.get("module") or "")
            _extra = " ".join(str(problem.get(k, "")) for k in
                              ("method", "type", "issue_type", "category")
                              if problem.get(k))
            if not _text:
                _text = json.dumps(problem, ensure_ascii=False)[:300]
            return _text, _domain, _extra
        return str(problem or ""), "", ""

    def _prune_if_needed(self):
        """超出容量时剪掉权重最低的经验（保留高质量）。"""
        if len(self._experiences) <= self._max_experiences:
            return
        _sorted = sorted(self._experiences.values(),
                         key=lambda e: (float(e.get("weight", 0)),
                                        float(e.get("last_hit_at", 0))))
        _to_remove = len(self._experiences) - self._max_experiences
        for _e in _sorted[:_to_remove]:
            self._experiences.pop(str(_e.get("id")), None)

    def _log(self, level: str, msg: str):
        if self._log_fn is not None:
            try:
                self._log_fn(level, msg)
            except Exception as e:
                silent_exc(e, where="nucleus.evolution.ExperienceTransfer::_log L375")


# ========== 模块级共享实例 ==========
_transfer: ExperienceTransfer | None = None
_transfer_lock = threading.Lock()


def get_experience_transfer(config: dict[str, Any] | None = None,
                            storage_path: str | None = None,
                            log_fn=None) -> ExperienceTransfer:
    """获取共享经验迁移库（单例；首用自动 load）。"""
    global _transfer
    if _transfer is None:
        with _transfer_lock:
            if _transfer is None:
                _transfer = ExperienceTransfer(config=config,
                                               storage_path=storage_path,
                                               log_fn=log_fn)
                _transfer.load()
    return _transfer


def reset_experience_transfer():
    global _transfer
    _transfer = None


# ========== 自测 ==========
if __name__ == "__main__":
    import tempfile

    print("=== ExperienceTransfer 自测 ===\n")
    _tmpdir = tempfile.mkdtemp(prefix="exptransfer_test_")
    _store = os.path.join(_tmpdir, "experience_transfer.json")
    _et = ExperienceTransfer(storage_path=_store,
                             log_fn=lambda l, m: print(f"  [{l}] {m}"))

    # 1. 沉淀经验（领域A）
    _id1 = _et.record(
        {"organ": "双腿", "method": "_fetch_url", "description": "请求超时导致抓取失败"},
        "增加超时重试与指数退避，超时时间从5秒提升到15秒",
        source="code_learner", success=True)
    print(f"1. 经验入库: {_id1}")
    assert _id1, "经验入库失败"

    # 2. 跨领域匹配（领域B 遇到同类问题）
    _hits = _et.match({"organ": "控制器", "method": "fetch",
                       "description": "网页请求超时抓取失败"}, top_k=3)
    print(f"2. 跨领域匹配命中: {len(_hits)}条, "
          f"最佳匹配度={_hits[0]['match_score'] if _hits else 0}")
    assert _hits, "跨领域应能匹配到超时经验"
    assert "超时" in _hits[0]["strategy"] or "重试" in _hits[0]["strategy"]

    # 3. 不相关问题不应命中
    _miss = _et.match({"organ": "胃", "description": "关键词提取纯度偏低需要优化"}, top_k=3)
    print(f"3. 不相关问题命中: {len(_miss)}条（应为0）")
    assert len(_miss) == 0, "不相关问题不应命中"

    # 4. 反馈闭环
    _et.feedback(_id1, success=True)
    _w_after = _et.get_stats()
    print(f"4. 反馈后统计: 经验{_w_after['total_experiences']}条, "
          f"成功反馈={_w_after['feedback_success']}")

    # 5. 持久化 + 重载
    assert _et.save()
    _et2 = ExperienceTransfer(storage_path=_store, log_fn=lambda l, m: None)
    _n = _et2.load()
    print(f"5. 重载恢复: {_n}条")
    assert _n == 1, "持久化恢复失败"

    # 6. 衰减淘汰（把 last_hit_at 推到 60 天前）
    _raw = safe_read_json(_store, default={})
    for _e in _raw["experiences"]:
        _e["last_hit_at"] = time.time() - 60 * 86400
        _e["weight"] = 0.15
    safe_write_json(_store, _raw)
    _et3 = ExperienceTransfer(storage_path=_store, log_fn=lambda l, m: None)
    _et3.load()
    _removed = _et3.decay()
    print(f"6. 衰减淘汰: {_removed}条（应为1）")
    assert _removed == 1, "过期经验应被淘汰"

    print("\n✅ 自测全部通过（6/6）")
