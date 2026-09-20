# -*- coding: utf-8 -*-
"""
verification_learning_hub.py —— 验证学习中心

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 验证结果的学习与反馈枢纽
机制: 基于VerificationLearningHub类实现，包含10个核心方法
定位: 记忆学习层
"""

import json
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_read_json

# ★第87批 T-87c：验证决策校验日志的采样间隔（每 N 条不一致打 1 条 DEBUG）。
#   原实现「每次不一致都打一条」，实测单次进化轮次可产出上百条
#   （09-18~09-20 累计 1633 条），把同文件的 INFO/WARNING 淹没在 DEBUG 里。
#   改为采样后噪声下降约 99%，可观测性由「首条必打 + 累计计数」保留。
#   ≤0 表示禁用采样（回退为改造前的全量打印）。
_VL_MISMATCH_LOG_EVERY = 100



class VerificationLearningHub:
    """全框架终身学习引擎统一骨架"""

    def __init__(self, file_path: str = "data/verification_learning.json",
                 # ★P1: 原阈值 2000 在正常运营中几乎不可达，导致蒸馏永不触发、规则永不回灌。
                 #   降为 200 使累计对比记录能周期性蒸馏并经 applier 真正作用于 QICA（闭合回灌链）。
                 #   distill_rules 仍保留「单器官≥3条 + 同概念≥2次」护栏，不会因阈值降低产生噪声规则。
                 distill_threshold: int = 200):
        self._file_path = file_path
        self._distill_threshold = max(5, distill_threshold)  # 保留下限校验，删掉冗余的第二行
        # ★第87批 T-87c：验证决策校验日志采样计数（纯计数，不参与任何判定）。
        self._vl_mismatch_total = 0
        self._vl_mismatch_logged = 0
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._organ_appliers: dict[str, Callable] = {}
    
        self._load()
        # ★FIX: 不在构造期自动蒸馏（此时 applier 尚未注册，规则会产出即丢失），
        #   改为首个 applier 注册后延迟触发，见 register_applier

    def _log(self, level: LogLevel, message: str, exc_info: bool = False):
        """统一日志输出，使用框架logging系统（写入后台日志文件）"""
        try:
            from nucleus.logger import get_module_logger
            _logger = get_module_logger("verification_learning_hub")
            _level_map = {
                LogLevel.ERROR: 40,
                LogLevel.WARNING: 30,
                LogLevel.INFO: 20,
                LogLevel.DEBUG: 10,
            }
            _py_level = _level_map.get(level, 20)
            _logger.log(_py_level, f"[验证学习枢纽] {message}", exc_info=exc_info)
        except Exception:
            # 降级：框架日志不可用时用print
            prefix_map = {
                LogLevel.ERROR: "[ERROR]",
                LogLevel.WARNING: "[WARN]",
                LogLevel.INFO: "[INFO]",
                LogLevel.DEBUG: "[DEBUG]",
            }
            prefix = prefix_map.get(level, "[INFO]")
            print(f"{prefix} [验证学习枢纽] {message}")
            if exc_info:
                import traceback
                traceback.print_exc()


    def _load(self):
        try:
            if os.path.exists(self._file_path):
                data = safe_read_json(self._file_path, default={})
                self._entries = data.get("entries", [])
        except Exception as e:
            print(f"[WARNING] verification_learning_hub.py:71: {type(e).__name__}: {e}")
            self._entries = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            snapshot = {
                "version": "v1.0",
                "updated_at": time.time(),
                "entries": self._entries,
            }
            tmp_file = self._file_path + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, self._file_path)
        except Exception as e:
            self._log(LogLevel.WARNING, f"[VerificationLearningHub] 保存失败: {e}")

    def register_applier(self, organ_name: str, callback: Callable):
        """
        注册器官的规则应用回调。
        当蒸馏出该器官的规则时，自动调用回调并传入规则列表。
        """
        with self._lock:
            self._organ_appliers[organ_name] = callback

    def run_startup_distill(self):
        """在全部器官 applier 注册完成后调用，处理历史存量蒸馏（避免首个注册即蒸馏的竞态）。"""
        if getattr(self, "_startup_distill_done", False):
            return
        self._startup_distill_done = True
        try:
            self._auto_distill_on_startup()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _auto_distill_on_startup(self):
        """启动时自动检查并触发蒸馏（处理历史存量条目）"""
        try:
            if len(self._entries) >= self._distill_threshold:
                self._log(LogLevel.INFO,f"[终身学习引擎] 启动检测到 {len(self._entries)} 条存量记录，自动触发蒸馏...")
                result = self.process_distill()
                if result.get("status") == "distilled":
                    self._log(LogLevel.INFO,
                        f"[终身学习引擎] 启动自动蒸馏完成: "
                        f"{result.get('distilled_entries', 0)}条→"
                        f"{result.get('rules_generated', 0)}条规则→"
                        f"应用{result.get('rules_applied', 0)}条"
                    )
                else:
                    self._log(LogLevel.INFO,f"[终身学习引擎] 启动蒸馏结果: {result}")
        except Exception as e:
            self._log(LogLevel.WARNING,f"[终身学习引擎] 启动自动蒸馏异常: {e}", exc_info=True)
    def record(self,
               organ: str,
               task_type: str,
               input_summary: str,
               local_result: dict,
               confidence: float,
               relevance_score: float,
               needs_verification: bool,
               verification_result: dict | None = None,
               api_better: bool = False,
               lesson: str = "") -> str:
        """
        记录一次验证-学习事件。
        返回条目ID。
        """
        with self._lock:
            entry_id = f"vl_{int(time.time()*1000)}_{len(self._entries)}"
            entry = {
                "id": entry_id,
                "organ": organ,
                "task_type": task_type,
                "input_summary": input_summary[:200],
                "local_result": local_result,
                "confidence": round(max(0.0, min(1.0, confidence)), 2),
                "relevance_score": round(max(0.0, min(1.0, relevance_score)), 2),
                "needs_verification": needs_verification,
                "verification_result": verification_result or {},
                "api_better": api_better,
                "lesson": lesson[:300],
                "timestamp": time.time(),
            }
            self._entries.append(entry)
            # ★主线C(C2)：接线 should_verify（统一验证决策，此前从未被调用的死代码）。
            #   用其判定结果与实际传入的 needs_verification 做交叉校验，发现
            #   「阈值判定规则 vs 调用方实际决策」的漂移——当高置信度却仍标记
            #   需要验证（过度验证）或低置信度却未标记（漏验证）时，记录诊断。
            try:
                _should = self.should_verify(organ, task_type, confidence, relevance_score)
                if _should != bool(needs_verification):
                    # ★第87批 T-87c：采样打印（判定逻辑与写入流程零改动）。
                    #   首条必打（保证"机制在工作"始终可见）+ 每 _VL_MISMATCH_LOG_EVERY
                    #   条打一条，并在每条采样日志里带上累计值，避免完全静默。
                    self._vl_mismatch_total = getattr(
                        self, "_vl_mismatch_total", 0) + 1
                    _every87 = int(_VL_MISMATCH_LOG_EVERY)
                    _should_log87 = (
                        self._vl_mismatch_total == 1
                        or (_every87 > 0 and self._vl_mismatch_total % _every87 == 0))
                    if _should_log87:
                        self._vl_mismatch_logged = getattr(
                            self, "_vl_mismatch_logged", 0) + 1
                        self._log(LogLevel.DEBUG,
                                  f"[验证决策校验] {organ}/{task_type} 置信度={confidence:.2f} "
                                  f"匹配度={relevance_score:.2f}，规则判定={_should}，"
                                  f"实际={bool(needs_verification)}（不一致，供阈值调优参考；"
                                  f"累计不一致={self._vl_mismatch_total} 条，"
                                  f"已采样打印={self._vl_mismatch_logged} 条）")
            except Exception as _vl87_err:
                self._log(LogLevel.DEBUG,
                          f"[验证决策校验] 校验失败（已忽略）: "
                          f"{type(_vl87_err).__name__}: {_vl87_err}")
            # 防止意外膨胀
            if len(self._entries) > 2000:
                self._entries = self._entries[-2000:]
            # 进度日志
            if len(self._entries) % 10 == 0:
                self._log(LogLevel.INFO,
                    f"[终身学习引擎] 当前累计 {len(self._entries)} 条对比记录，"
                    f"距离下次蒸馏还需 {max(0, self._distill_threshold - len(self._entries))} 条"
                )
            self._save()

        # ★修复：自动蒸馏必须在锁外调用，防止死锁
        if len(self._entries) >= self._distill_threshold:
            try:
                distill_result = self.process_distill()
                if distill_result.get("status") == "distilled":
                    self._log(LogLevel.INFO,
                        f"[终身学习引擎] 自动蒸馏完成: "
                        f"{distill_result.get('distilled_entries', 0)}条→"
                        f"{distill_result.get('rules_generated', 0)}条规则→"
                        f"应用{distill_result.get('rules_applied', 0)}条"
                    )
            except Exception as _distill_e:
                self._log(LogLevel.INFO,f"[终身学习引擎] 自动蒸馏异常: {_distill_e}", exc_info=True)

        return entry_id

    def get_stats(self) -> dict:
        """
        ★v25.1: 获取验证学习统计，供参数优化审计使用。
        返回: 总记录数、补救率、常见失败原因等
        """
        try:
            _total = len(self._entries)
            _remediation = sum(1 for e in self._entries if e.get("remediation_triggered", False))
            _rate = _remediation / _total if _total > 0 else 0.0
            # 统计常见失败原因
            _reasons = {}
            for e in self._entries:
                _r = e.get("failure_reason", "")
                if _r:
                    _reasons[_r] = _reasons.get(_r, 0) + 1
            _top_reasons = sorted(_reasons.items(), key=lambda x: x[1], reverse=True)[:3]
            return {
                "total": _total,
                "remediation_count": _remediation,
                "remediation_rate": round(_rate, 3),
                "top_failure_reasons": _top_reasons,
                "distill_threshold": self._distill_threshold,
            }
        except Exception as e:
            return {"total": 0, "remediation_count": 0, "remediation_rate": 0.0, "top_failure_reasons": []}

    def should_verify(self, organ: str, task_type: str,
                      confidence: float, relevance_score: float) -> bool:
        """
        统一判断是否需要外部验证。

        规则：
        - 置信度 < 0.6 → 需要验证
        - 话题匹配度 < 0.5 → 需要验证
        - 两者都低 → 必须验证
        - 置信度 >= 0.8 且匹配度 >= 0.7 → 不需要
        """
        if confidence < 0.6 or relevance_score < 0.5:
            return True
        return bool(confidence < 0.8 and relevance_score < 0.7)

    def check_and_get_distillable(self, organ: str | None = None) -> list[dict[str, Any]]:
        """
        检查是否达到蒸馏阈值。
        达到则返回待蒸馏的条目（并清空这些条目），否则返回空列表。
        """
        with self._lock:
            target = [e for e in self._entries if organ is None or e.get("organ") == organ]
            if len(target) >= self._distill_threshold:
                distillable = target[:]
                # 从主列表中移除已蒸馏的条目
                distill_ids = {e["id"] for e in distillable}
                self._entries = [e for e in self._entries if e["id"] not in distill_ids]
                # 保留最新10条作为缓冲
                self._save()
                return distillable
            return []

    def distill_rules(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        从一组验证-学习条目中蒸馏规则。

        当前实现：提取"API更优"且具有明确纠正模式的条目，
        生成概念→正确结果 的映射规则。

        Returns:
            规则列表，每条规则包含:
            {
                "type": "concept_correction",
                "organ": str,
                "concept": str,
                "correct_intent": str,
                "correct_path": str,
                "confidence": float,
                "occurrence": int,
            }
        """
        if not entries:
            return []

        # v25.1诊断: 统计各过滤层的条目数，定位0规则根因
        _total = len(entries)
        _api_better_count = sum(1 for e in entries if e.get("api_better", False))
        _with_verify = sum(1 for e in entries if isinstance(e.get("verification_result"), dict)
                          and (e["verification_result"].get("intent") or e["verification_result"].get("path")))
        _with_llm_suggestion = sum(1 for e in entries if isinstance(e.get("verification_result"), dict)
                                   and (e["verification_result"].get("llm_suggestion")
                                        or e["verification_result"].get("llm_analysis")))
        # 按器官统计
        _organ_stats = {}
        for e in entries:
            if e.get("api_better", False):
                _org = e.get("organ", "unknown")
                _organ_stats[_org] = _organ_stats.get(_org, 0) + 1
        _organ_detail = ", ".join(f"{k}={v}" for k, v in sorted(_organ_stats.items()))
        self._log(LogLevel.INFO,
                 f"[蒸馏诊断] 总条目={_total}, api_better={_api_better_count}, "
                 f"含intent/path={_with_verify}, 含llm_suggestion={_with_llm_suggestion}, "
                 f"器官分布[{_organ_detail}]")

        # 按器官分组统计
        organ_entries: dict[str, list] = {}
        for e in entries:
            if e.get("api_better", False):
                organ = e.get("organ", "unknown")
                organ_entries.setdefault(organ, []).append(e)

        if not organ_entries:
            self._log(LogLevel.INFO, "[蒸馏诊断] 无api_better=True条目，无法蒸馏规则")
            return []

        rules = []
        for organ, org_entries in organ_entries.items():
            if len(org_entries) < 3:
                self._log(LogLevel.DEBUG, f"蒸馏跳过: 器官 {organ} 记录不足3条(实际{len(org_entries)}条)")
                continue

            correction_map: dict[str, dict[str, Any]] = {}

            for entry in org_entries:
                # 提取本地结果中的内容（如果是语义分类，包含 content）
                local = entry.get("local_result", {})
                content = local.get("content", "") if isinstance(local, dict) else ""
                if not content:
                    content = entry.get("input_summary", "")

                # 提取正确意图/路径（从验证结果中）
                verify = entry.get("verification_result", {})
                correct_intent = verify.get("intent", "")
                correct_path = verify.get("path", "") or (verify.get("paths", [""])[0] if verify.get("paths") else "")

                if not correct_intent and not correct_path:
                    continue

                # 提取内容中的中文关键词
                keywords = re.findall(r'[\u4e00-\u9fff]{2,4}', content)
                noise = {"什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个", "一个", "一种", "帮我", "请"}
                keywords = [k for k in keywords if k not in noise]
                if not keywords:
                    continue

                concept = keywords[0]
                key = f"{concept}:{correct_intent}"
                if key not in correction_map:
                    correction_map[key] = {
                        "concept": concept,
                        "correct_intent": correct_intent,
                        "correct_path": correct_path,
                        "count": 0,
                    }
                correction_map[key]["count"] += 1

            for data in correction_map.values():
                count = data["count"]
                if count >= 2:
                    confidence = min(0.95, 0.5 + count / max(1, len(org_entries)))
                    rules.append({
                        "type": "concept_correction",
                        "organ": organ,
                        "concept": data["concept"],
                        "correct_intent": data["correct_intent"],
                        "correct_path": data["correct_path"],
                        "confidence": round(confidence, 2),
                        "occurrence": count,
                    })
                    # ★FIX(问题14): 存在正确路径时同时产出 domain_knowledge 规则，
                    #   使 QICA 的 domain_knowledge 消费分支不再死代码
                    if data.get("correct_path"):
                        rules.append({
                            "type": "domain_knowledge",
                            "organ": organ,
                            "concept": data["concept"],
                            "path": data["correct_path"],
                            "confidence": round(confidence, 2),
                            "occurrence": count,
                        })

        # ★P1-5修复：code 类记录（organ=code_learner）原先因无 intent/path 被上面的
        # silent continue 跳过，永远无法蒸馏。此处单独蒸馏为 code_issue_lesson 规则
        # （issue_type → 修复策略），供 self_inspector 后续注册 applier 后消费（P3）。
        # ★增强：按issue_type去重计数，同一类型只生成一条规则
        _code_issue_map = {}
        _code_issue_count = 0
        for entry in entries:
            if not entry.get("api_better", False):
                continue
            if entry.get("organ") != "code_learner":
                continue
            verify = entry.get("verification_result", {})
            if not isinstance(verify, dict):
                continue
            _suggestion = verify.get("llm_suggestion") or verify.get("llm_analysis")
            if not _suggestion:
                continue
            _summary = entry.get("input_summary", "")
            # 优先从verification_result获取issue_type，其次从input_summary解析
            _issue_type = verify.get("issue_type", "") or _summary.split(":", 1)[0].strip() if _summary else "unknown"
            if not _issue_type:
                _issue_type = "unknown"
            _code_issue_count += 1
            if _issue_type not in _code_issue_map:
                _code_issue_map[_issue_type] = {
                    "suggestion": _suggestion[:500],
                    "confidence": entry.get("confidence", 0.7),
                    "count": 0,
                }
            _code_issue_map[_issue_type]["count"] += 1
            # 保留置信度最高的建议
            if entry.get("confidence", 0.7) > _code_issue_map[_issue_type]["confidence"]:
                _code_issue_map[_issue_type]["suggestion"] = _suggestion[:500]
                _code_issue_map[_issue_type]["confidence"] = entry.get("confidence", 0.7)

        for _itype, _data in _code_issue_map.items():
            rules.append({
                "type": "code_issue_lesson",
                "organ": "code_learner",
                "issue_type": _itype,
                "fix_strategy": _data["suggestion"],
                "confidence": round(_data["confidence"], 2),
                "occurrence": _data["count"],
            })

        # 蒸馏结果诊断
        _concept_rules = sum(1 for r in rules if r.get("type") == "concept_correction")
        _domain_rules = sum(1 for r in rules if r.get("type") == "domain_knowledge")
        _code_rules = sum(1 for r in rules if r.get("type") == "code_issue_lesson")
        self._log(LogLevel.INFO,
                 f"[蒸馏诊断] 生成规则={len(rules)} "
                 f"(concept_correction={_concept_rules}, domain_knowledge={_domain_rules}, "
                 f"code_issue_lesson={_code_rules}), code_learner候选={_code_issue_count}")

        return rules

    def apply_rules(self, organ: str, rules: list[dict[str, Any]]) -> int:
        """
        将蒸馏规则应用到指定器官。
        如果该器官注册了回调，则调用回调。
        返回成功应用的规则数。
        """
        callback = self._organ_appliers.get(organ)
        if callback:
            try:
                return callback(rules)
            except Exception as e:
                self._log(LogLevel.INFO,f"[VerificationLearningHub] 应用规则失败({organ}): {e}")
                return 0
        # ★FIX(L1): 无回调时显式告警，避免规则静默丢弃
        self._log(LogLevel.WARNING,
                 f"[VerificationLearningHub] 器官 {organ} 未注册规则回调，{len(rules)} 条规则被跳过")
        return 0

    def process_distill(self, organ: str | None = None) -> dict[str, Any]:
        """
        统一的蒸馏处理入口：检查阈值 → 蒸馏 → 应用规则。
        返回处理结果摘要。
        """
        distillable = self.check_and_get_distillable(organ)
        if not distillable:
            return {"status": "below_threshold", "entry_count": len(self._entries)}

        rules = self.distill_rules(distillable)

        # 按器官分组应用规则
        applied_total = 0
        organ_rule_groups: dict[str, list] = {}
        for rule in rules:
            organ_rule_groups.setdefault(rule.get("organ", "unknown"), []).append(rule)

        for organ_name, organ_rules in organ_rule_groups.items():
            applied = self.apply_rules(organ_name, organ_rules)
            applied_total += applied

        return {
            "status": "distilled",
            "distilled_entries": len(distillable),
            "rules_generated": len(rules),
            "rules_applied": applied_total,
        }

    def get_stats_detailed(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_entries": len(self._entries),
                "file_exists": os.path.exists(self._file_path),
                "distill_threshold": self._distill_threshold,
                "registered_organs": list(self._organ_appliers.keys()),
            }


# 模块级单例
_hub: VerificationLearningHub | None = None


def get_verification_learning_hub() -> VerificationLearningHub:
    global _hub
    if _hub is None:
        _hub = VerificationLearningHub()
    return _hub


def shutdown_verification_learning_hub() -> None:
    """★P0批次3：复位 VerificationLearningHub 单例，满足器官零状态（规则4）。

    原停机流程未清理该全局单例（_organ_appliers 等注册表残留），重启时会复用带
    残留注册信息的旧实例。此处显式置空，使下次获取重建全新零状态实例。
    """
    global _hub
    _inst = _hub
    _hub = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception:
                pass