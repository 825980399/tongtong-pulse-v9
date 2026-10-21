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

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.proc_gen import proc_gen_str as _m335_pg

# ★第87批 T-87c：验证决策校验日志的采样间隔（每 N 条不一致打 1 条 DEBUG）。
#   原实现「每次不一致都打一条」，实测单次进化轮次可产出上百条
#   （09-18~09-20 累计 1633 条），把同文件的 INFO/WARNING 淹没在 DEBUG 里。
#   改为采样后噪声下降约 99%，可观测性由「首条必打 + 累计计数」保留。
#   ≤0 表示禁用采样（回退为改造前的全量打印）。
_VL_MISMATCH_LOG_EVERY = 100



# ===== ★335批刀5（小林拍板②）：absorb 结果**持久化** =====
# 问题：吸收效果只活在内存 dict 里，重启即失（实测 applied 0→2 重启归零）。
#   "复利"如果跨不过一次重启，它就不是复利，只是一次性副作用。
# 设计：**只搬结果、不动判定** ——
#   * 落盘 `data/evolution/lesson_strategies.json`（原子写 tmp + os.replace）；
#   * 键 = `organ|tag|rule`（rule = `type::issue_type`）：同一规则在不同吸收方
#     名下**分开记账**（谁吸收的、吸收了几次，必须分得清）；
#   * `applied_total` **累加不重置** —— 跨重启续算，这是本刀的核心判据；
#   * 边界：类别白名单不扩（已裁）、absorb 四步逻辑不动、不新增调度面。
# ⚠ 口径如实说明：回调只返回**整批** applied，无法拆分到单条规则，
#   故同批的每条规则都记同一批次数 —— `applied_total` 是"批次口径累计"，
#   读它时请配合 `hit_count`（被派发次数）一起看，别当"单条规则被应用了几次"。
_M335_STRATEGY_REL = os.path.join("data", "evolution", "lesson_strategies.json")
_M335_STRATEGY_VERSION = "335.1"


def _m335_strategy_key(rule: dict) -> str:
    """规则的稳定键：`type::issue_type`（无 issue_type 时退 `type::name`）。"""
    _t = str(rule.get("type") or "unknown")
    _k = str(rule.get("issue_type") or rule.get("rule")
             or rule.get("name") or "")
    return ("%s::%s" % (_t, _k)) if _k else _t


class VerificationLearningHub:
    """全框架终身学习引擎统一骨架"""

    def __init__(self, file_path: str = "data/verification_learning.json",
                 # ★P1: 原阈值 2000 在正常运营中几乎不可达，导致蒸馏永不触发、规则永不回灌。
                 #   降为 200 使累计对比记录能周期性蒸馏并经 applier 真正作用于 QICA（闭合回灌链）。
                 #   distill_rules 仍保留「单器官≥3条 + 同概念≥2次」护栏，不会因阈值降低产生噪声规则。
                 distill_threshold: int = 200,
                 strategies_path: str | None = None):
        self._file_path = file_path
        # ★335批刀5：absorb 策略盘账路径（`strategies_path` 仅供隔离验证注入，
        #   生产调用方不传 ⇒ 走项目根下的默认相对路径。铁律158）
        self._m335_strategy_path: str | None = strategies_path
        self._m335_strategies: dict = {}
        self._distill_threshold = max(5, distill_threshold)  # 保留下限校验，删掉冗余的第二行
        # ★第87批 T-87c：验证决策校验日志采样计数（纯计数，不参与任何判定）。
        self._vl_mismatch_total = 0
        self._vl_mismatch_logged = 0
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        # ★326批刀0：一 organ **多通道**回调。结构 {organ: [(tag, callback), ...]}
        #   旧实现是单值 `dict[organ] = callback` ⇒ 后注册者静默胜出、前通道规则丢失
        #   （烛微326 §0-4 实测 WARNING×2：stomach 被两处注册，后者顶掉前者）。
        #   改列表后语义：**同 organ 同 tag = 更新（幂等）**、**不同 tag = 并存（追加）**。
        #   tag 由注册方声明（如 qica_growth / self_inspector_absorb），是"谁吸收了规则"的账名。
        self._organ_appliers: dict[str, list[tuple[str, Callable]]] = {}
        #: 分发留痕序号（每次 apply_rules +1，供日志/报表复算）
        self._applier_dispatch_seq = 0
        # ★330批刀4：**上一次跨阈消费的时刻**（= 上一轮 check_and_get_distillable 真放料的时刻）。
        #   需求源入队侧要拿它算"料龄"——料龄超过跨阈节奏（≈33 分钟）就说明这一拍读到的
        #   本就是上一轮残留，标 stale 而不是当成"刚产出的料"。
        self._last_consume_ts = 0.0
        #: 分发留痕最近一条（只读快照，供压测报告/日报取数）
        self._applier_last_dispatch: dict = {}

        self._load()
        # ★335批刀5：启动即加载策略盘账（跨重启续算的前提）。
        #   加载失败不阻断构造：宁可"这轮从空账起步"，也不让一次读盘失败
        #   把整个学习中枢拖死（失败已在 `_m335_load_strategies` 里留痕）。
        self._m335_strategies = self._m335_load_strategies()
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

    #: ★326批刀0：未声明 tag 时的默认通道名（保持旧调用点可用）
    APPLIER_TAG_DEFAULT = "default"

    def register_applier(self, organ_name: str, callback: Callable,
                         tag: str | None = None) -> str:
        """注册器官某**通道**的规则应用回调（★326批刀0：一 organ 多回调）。

        语义（写死，勿回退为单值）：
          * **同 organ 同 tag** ⇒ 更新该通道回调（幂等：同对象重复注册零动作）；
            **不再**打「applier 被覆盖」WARNING —— 覆盖式语义已不存在。
          * **同 organ 不同 tag** ⇒ 追加并存，分发时逐个调用（谁都不顶掉谁）。
          * tag 是"谁吸收了规则"的账名，缺省用 `APPLIER_TAG_DEFAULT`。

        Returns: 实际落入的 tag（供调用方留痕/断言）。
        """
        _tag = str(tag or self.APPLIER_TAG_DEFAULT)
        _cb_name = (getattr(callback, "__qualname__", None)
                    or repr(callback)[:40])
        with self._lock:
            _slot = self._organ_appliers.setdefault(organ_name, [])
            for _i, (_t, _cb) in enumerate(_slot):
                if _t != _tag:
                    continue
                if _cb is callback:
                    return _tag          # 完全幂等：同 tag 同对象，零动作
                _slot[_i] = (_tag, callback)
                self._log(LogLevel.DEBUG,
                          f"[326刀0][applier] 同 tag 更新 organ={organ_name} tag={_tag} "
                          f"new={_cb_name}（同 tag 覆盖属幂等更新，非丢规则）")
                return _tag
            _slot.append((_tag, callback))
            self._log(LogLevel.INFO,
                      f"[326刀0][applier] 新通道注册 organ={organ_name} tag={_tag} "
                      f"cb={_cb_name} 该器官通道数={len(_slot)}")
        return _tag

    def _appliers_for(self, organ_name: str) -> list[tuple[str, Callable]]:
        """取某 organ 的全部 (tag, callback)；缺失返回 []（只读，不改注册表）。

        兼容旧结构（万一拿到单值 Callable 的实例）：按默认 tag 包装，绝不 AttributeError。
        """
        _slot = self._organ_appliers.get(organ_name)
        if not _slot:
            return []
        if callable(_slot):
            return [(self.APPLIER_TAG_DEFAULT, _slot)]
        _out = []
        for _item in _slot:
            if isinstance(_item, tuple) and len(_item) == 2:
                _out.append((str(_item[0]), _item[1]))
            elif callable(_item):
                _out.append((self.APPLIER_TAG_DEFAULT, _item))
        return _out

    def applier_registry_snapshot(self) -> dict:
        """★326批刀0：注册表快照（organ → [tag...]），供验收/报表核"几个通道"。只读。"""
        return {_o: [t for t, _ in self._appliers_for(_o)]
                for _o in list(self._organ_appliers.keys())}

    def register_applier_alias(self, primary: str, alias: str):
        """★315批刀2-B：声明别名路由——当规则派发给 `alias` 器官时，
        同时派发给 `primary` 器官的回调（让一份料被多个吸收方接收，互不顶掉）。

        例：register_applier_alias("self_inspector", "code_learner")
           => code_learner 蒸馏出的 code_issue_lesson 规则也会回流到 self_inspector，
             self_inspector 的 absorb_growth_rules 因此被调用（闭环「注册了但收不到料」）。
        """
        with self._lock:
            if not hasattr(self, "_applier_aliases"):
                self._applier_aliases = {}
            if alias in self._applier_aliases and self._applier_aliases[alias] != primary:
                self._log(LogLevel.WARNING,
                          f"[VerificationLearningHub] 别名 {alias} 已指向 "
                          f"{self._applier_aliases[alias]}，忽略新的 {primary} 映射")
                return
            self._applier_aliases[alias] = primary

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
               lesson: str = "", remediation_triggered: bool = False,
               origin: str = "") -> str:
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
                "remediation_triggered": remediation_triggered,
                "origin": (origin or "")[:64],
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

    #: ★330批刀4：消费后**至少保留**的 growth 料条数（供下一拍 intake 读取）
    _M330_GROWTH_RETAIN_N = 2

    @staticmethod
    def _m330_is_growth(e) -> bool:
        """与 `EvolutionLoop._m316_collect_growth_intake` 的取料条件**同源**。

        两处若各写一份判据，早晚会漂移成"消费侧保留了、采集侧照样找不到"——
        所以这里不改用新口径，直接照抄采集侧那两个字段。
        """
        if not isinstance(e, dict):
            return False
        return (str(e.get("task_type", "")) == "growth_hypothesis"
                or str(e.get("origin", "")) == "growth")

    def check_and_get_distillable(self, organ: str | None = None) -> list[dict[str, Any]]:
        """
        检查是否达到蒸馏阈值。
        达到则返回待蒸馏的条目（并清空这些条目），否则返回空列表。

        ★330批刀4（326 挂账⑧/327 挂账⑨）：**growth 料最小保留**。
          旧实现 `distillable = target[:]` 把命中器官的条目**整批**摘走，注释写的
          "保留最新10条作为缓冲"从未实现过 —— 于是 growth 料被一轮跨阈消费清空后，
          下一拍 `_m316_collect_growth_intake` 只能读到 `no_material`，需求源结构性断料。
          现在：每轮消费时把**最近 N 条** growth 料留在 `_entries` 里，只让它被保留**一轮**
          （`_m330_retained_once` 标记，下一轮必放行），既不断料也不永久屏蔽蒸馏。
        """
        import time as _t
        with self._lock:
            # ---- 先释放上一轮的保留标记：保留只维持 1 轮，第 2 轮必进蒸馏 ----
            for _e in self._entries:
                if isinstance(_e, dict) and _e.pop("_m330_retained", None):
                    _e["_m330_retained_once"] = True
            target = [e for e in self._entries if organ is None or e.get("organ") == organ]
            if len(target) >= self._distill_threshold:
                distillable = target[:]
                # ---- ★330批刀4：挑最近的 N 条 growth 料留下（已保留过一次的不再保留）----
                _g = [e for e in distillable
                      if self._m330_is_growth(e) and not e.get("_m330_retained_once")]
                _keep_ids = {id(e) for e in _g[-self._M330_GROWTH_RETAIN_N:]} if _g else set()
                for _e in distillable:
                    if id(_e) in _keep_ids:
                        _e["_m330_retained"] = True
                if _keep_ids:
                    distillable = [e for e in distillable if id(e) not in _keep_ids]
                    # 留下的是"料"，不是"待蒸馏"——本轮的返回值里不能含它们
                    self._last_consume_ts = _t.time()
                # 从主列表中移除已蒸馏的条目
                distill_ids = {e["id"] for e in distillable if isinstance(e, dict) and "id" in e}
                # 移除时要容错：缺 id 的脏条目不能 KeyError 掉整轮（保留原有"不清它"的语义）
                self._entries = [e for e in self._entries
                                 if not (isinstance(e, dict) and e.get("id") in distill_ids)]
                self._save()
                return distillable
            return []

    # ★317批刀6（G-4 · 星轨+小林裁定A）：蒸馏来源白名单。
    # 原判据只看 `organ=="code_learner"`（所谓"工牌"），导致 self_inspector 名义料
    # （origin=code_review，organ=self_inspector）永远进不了蒸馏——315刀2 审查归位
    # 因此只成一半。裁定：改按 **origin/规则类型** 分流，同时保留白名单防噪声混入。
    _DISTILL_ORIGIN_WHITELIST = frozenset({"code_review"})

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
            # ★317批刀6：origin 分流——organ 工牌命中 **或** origin 在可信白名单内即放行
            #   （白名单外来源仍被拦，不放开；code_learner 老路径行为不变）
            _src_organ = str(entry.get("organ") or "") or "code_learner"
            _src_origin = str(entry.get("origin") or "")
            if _src_organ != "code_learner" and _src_origin not in self._DISTILL_ORIGIN_WHITELIST:
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
            _grp_key = (_src_organ, _issue_type)
            if _grp_key not in _code_issue_map:
                _code_issue_map[_grp_key] = {
                    "suggestion": _suggestion[:500],
                    "confidence": entry.get("confidence", 0.7),
                    "count": 0,
                    "origin": _src_origin,
                }
            _code_issue_map[_grp_key]["count"] += 1
            # 保留置信度最高的建议
            if entry.get("confidence", 0.7) > _code_issue_map[_grp_key]["confidence"]:
                _code_issue_map[_grp_key]["suggestion"] = _suggestion[:500]
                _code_issue_map[_grp_key]["confidence"] = entry.get("confidence", 0.7)

        for (_r_organ, _itype), _data in _code_issue_map.items():
            rules.append({
                "type": "code_issue_lesson",
                "organ": _r_organ,
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
                 f"code_issue_lesson={_code_rules}), 候选={_code_issue_count} "
                 f"来源器官={sorted({r.get('organ') for r in rules if r.get('type') == 'code_issue_lesson'})} "
                 f"白名单={sorted(self._DISTILL_ORIGIN_WHITELIST)}")

        return rules

    # ===== ★335批刀5：absorb 盘账三件套（路径 → 读 → 原子写）=====
    def _m335_strategy_file(self) -> str:
        """策略盘账路径：注入优先，否则落项目根 `data/evolution/lesson_strategies.json`。"""
        if getattr(self, "_m335_strategy_path", None):
            return self._m335_strategy_path
        _root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(_root, _M335_STRATEGY_REL)

    def _m335_load_strategies(self, path: str | None = None) -> dict:
        """读盘账；缺失/损坏 ⇒ 返回空账结构（**不抛**，但必须留痕）。"""
        _p = path or self._m335_strategy_file()
        _empty = {"version": _M335_STRATEGY_VERSION, "updated_at": 0.0,
                  "strategies": {}}
        try:
            if not os.path.isfile(_p):
                return _empty
            with open(_p, encoding="utf-8") as _f:
                _d = json.load(_f)
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="VerificationLearningHub::_m335_load_strategies",
                       level="warning")
            self._log(LogLevel.WARNING,
                      f"[335刀5][absorb盘账] 读取失败 {_p}：{type(_e).__name__}: {_e}"
                      f" ⇒ 本轮从空账起步（不覆盖原文件）")
            return _empty
        if not isinstance(_d, dict) or not isinstance(_d.get("strategies"), dict):
            self._log(LogLevel.WARNING,
                      f"[335刀5][absorb盘账] 结构不符（{type(_d).__name__}）"
                      f" ⇒ 本轮从空账起步（不覆盖原文件）")
            return _empty
        _d.setdefault("version", _M335_STRATEGY_VERSION)
        _d.setdefault("updated_at", 0.0)
        return _d

    def _m335_save_strategies(self, data: dict, path: str | None = None) -> str:
        """原子写：同目录 tmp 文件 + `os.replace`（半个 JSON 比不写更糟）。"""
        _p = path or self._m335_strategy_file()
        _dir = os.path.dirname(os.path.abspath(_p))
        if not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        _tmp = _p + ".tmp335"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _p)
        return _p

    def _m335_persist_batch(self, organ: str, tag: str, rules: list,
                            applied: int) -> dict:
        """把**本批规则**的吸收结果 upsert 进盘账（`applied_total` 累加不重置）。

        Returns:
            ``{"ok": bool, "total": n, "cumulative": m, "path": ...}``；
            `ok=False` 时带 `reason`（`no_rule` / `write_failed`），**不吞**。
        """
        if not isinstance(getattr(self, "_m335_strategies", None), dict) \
                or not self._m335_strategies:
            self._m335_strategies = self._m335_load_strategies()
        _d = self._m335_strategies
        _bk = _d.setdefault("strategies", {})
        _now = time.time()
        _n = 0
        for _r in (rules or []):
            if not isinstance(_r, dict):
                continue
            _key = _m335_strategy_key(_r)
            _full = "%s|%s|%s" % (organ, tag, _key)
            _e = _bk.get(_full) if isinstance(_bk.get(_full), dict) else {}
            _e.update({
                "rule": _key,
                "type": _r.get("type"),
                "issue_type": _r.get("issue_type"),
                "organ": organ,
                "tag": tag,
                "strategy": str(_r.get("fix_strategy")
                                or _r.get("suggestion") or "")[:1000],
                # ★核心：累加不重置（跨重启续算）
                "applied_total": int(_e.get("applied_total") or 0) + int(applied or 0),
                "applied_last": int(applied or 0),
                "hit_count": int(_e.get("hit_count") or 0) + 1,
                "first_seen": float(_e.get("first_seen") or _now),
                "last_seen": _now,
            })
            _bk[_full] = _e
            _n += 1
        if not _n:
            return {"ok": False, "total": 0, "reason": "no_rule"}
        _d["version"] = _M335_STRATEGY_VERSION
        _d["updated_at"] = _now
        try:
            _p = self._m335_save_strategies(_d)
        except OSError as _e:
            silent_exc(_e, where="VerificationLearningHub::_m335_persist_batch",
                       level="warning")
            self._log(LogLevel.WARNING,
                      f"[335刀5][absorb盘账] 写盘失败 {type(_e).__name__}: {_e}"
                      f" ⇒ 本轮吸收结果只在内存（下次成功写盘时补齐）")
            return {"ok": False, "total": _n, "reason": "write_failed"}
        _cum = sum(int(v.get("applied_total") or 0)
                   for v in _bk.values() if isinstance(v, dict))
        return {"ok": True, "total": _n, "cumulative": _cum, "path": _p}

    def get_m335_strategies(self) -> dict:
        """★335批刀5：盘账快照（纯读，供报表/压测取数）。"""
        _d = getattr(self, "_m335_strategies", None)
        if not isinstance(_d, dict) or not _d:
            _d = self._m335_load_strategies()
        return {"version": _d.get("version"),
                "updated_at": _d.get("updated_at"),
                "count": len(_d.get("strategies") or {}),
                "strategies": dict(_d.get("strategies") or {})}

    def apply_rules(self, organ: str, rules: list[dict[str, Any]]) -> int:
        """
        将蒸馏规则应用到指定器官。
        如果该器官注册了回调，则调用回调；并通过别名路由把同一批规则
        同时派发给别名目标器官（★315批刀2-B：一份料可被多个吸收方接收）。
        返回成功应用的规则数。
        """
        # ★326批刀0：一 organ 多通道全部派发（不再"后注册者胜出"）
        _targets = [(organ, _tag, _cb) for _tag, _cb in self._appliers_for(organ)]
        # ★315批刀2-B：别名路由——把 `alias` 的规则也派给其 primary 的各通道
        _primary = getattr(self, "_applier_aliases", {}).get(organ)
        if _primary:
            _targets.extend((_primary, _tag, _cb)
                            for _tag, _cb in self._appliers_for(_primary))
        if not _targets:
            # ★FIX(L1): 无回调时显式告警，避免规则静默丢弃
            self._log(LogLevel.WARNING,
                     f"[VerificationLearningHub] 器官 {organ} 未注册规则回调，{len(rules)} 条规则被跳过")
            return 0
        _delivered = 0
        _tags = []
        _per = []
        for _name, _tag, _c in _targets:
            # ★326批刀5：absorb 计数留痕——**class/organ/tag/rules/applied/tier 六段固定**
            #   回调若是绑定方法，`__self__` 就是吸收方实例 ⇒ 类名可直接取出（无需调用方传参）。
            _owner = getattr(_c, "__self__", None)
            _cls = (type(_owner).__name__ if _owner is not None
                    else (getattr(_c, "__qualname__", "") or "callable"))
            _applied = 0
            try:
                _applied = int(_c(rules) or 0)
                _delivered += _applied
            except Exception as e:
                self._log(LogLevel.INFO,
                          f"[VerificationLearningHub] 应用规则失败({_name}/{_tag}): {e}")
            _tags.append(f"{_name}:{_tag}")
            # ★331批刀1：把吸收方的四步计数并进留痕（读回调 owner 的实例统计，
            #   **不 import self_inspector** —— 那边会 import 本模块，循环依赖）。
            #   读不到记 None（**不填 0**：没统计 ≠ 统计为 0）。
            # ★335批刀5：本通道吸收结果**即时落盘**（applied 累加不重置）。
            #   必须放在 `_per` 组装之前 —— 留痕里要带 `persisted=`，
            #   否则"到底落没落盘"又要靠人猜（这正是本刀要消灭的东西）。
            _persisted = self._m335_persist_batch(_name, _tag, rules, _applied)
            _abs = getattr(_owner, "_m331_absorb_stats", None) if _owner is not None else None
            _reason = dict(_abs["reason"]) if isinstance(_abs, dict) else None
            _per.append({"organ": _name, "tag": _tag, "class": _cls,
                         "rules": len(rules), "applied": _applied,
                         "tier": _m326_rule_tier(rules),
                         "persisted": _persisted.get("ok"),
                         "persisted_total": _persisted.get("total"),
                         "absorb_reason": _reason,
                         "absorb_steps": ({
                             "delivered": _abs.get("delivered"),
                             "categorized": _abs.get("categorized"),
                             "tier_checked": _abs.get("tier_checked"),
                             "applied": _abs.get("applied"),
                         } if isinstance(_abs, dict) else None)})
            self._log(LogLevel.INFO,
                      f"[326刀5][absorb] class={_cls} organ={_name} tag={_tag} "
                      f"rules={len(rules)} applied={_applied} tier={_m326_rule_tier(rules)} "
                      f"reason={_reason} "
                      f"persisted={_persisted.get('ok')}:{_persisted.get('total')}"
                      f":cum={_persisted.get('cumulative')} "
                      f"proc_gen={_m335_pg()}")
        self._applier_dispatch_seq = int(getattr(self, "_applier_dispatch_seq", 0)) + 1
        self._applier_last_dispatch = {
            "seq": self._applier_dispatch_seq, "organ": organ,
            "tags": list(_tags), "rules": len(rules), "delivered": int(_delivered),
            "per_channel": _per, "at": time.time(),
        }
        # ★326批刀0：分发留痕（固定一行、可 grep、可复算"谁吸收了规则"）
        self._log(LogLevel.INFO,
                  f"[326刀0][applier_dispatch] organ={organ} tags=[{','.join(_tags)}] "
                  f"rules={len(rules)} delivered={int(_delivered)} "
                  f"channels={len(_targets)} seq={self._applier_dispatch_seq}")
        return _delivered

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

        # ★316批刀2：跨阈三段留痕（料数/蒸馏/消费方），固定一行、可 grep。
        self._log(
            LogLevel.INFO,
            f"[316刀2][蒸馏跨阈] status=distilled entries={len(self._entries)} "
            f"distilled={len(distillable)} rules={len(rules)} "
            f"applied={applied_total} applied_organs={list(organ_rule_groups.keys())}"
        )

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
                # ★326批刀0：通道视图（organ → [tag...]），旧字段保持原样不动
                "registered_appliers": self.applier_registry_snapshot(),
            }


def _m326_rule_tier(rules: list) -> str:
    """★326批刀5：给一批规则的"力度档"命名（只用于留痕，不参与任何判罚）。

    口径写死：取批内**最高置信度**分档 —— high >=0.8 / mid >=0.6 / low 其余；
    空批记 `empty`。为什么按 max 而不是均值：一批规则里只要有一条高置信，
    吸收方的行为就已经被它拉动，均值会把这件事抹平。
    """
    _cs = []
    for _r in (rules or []):
        if isinstance(_r, dict):
            try:
                _cs.append(float(_r.get("confidence", 0) or 0))
            except (TypeError, ValueError):
                continue
    if not _cs:
        return "empty"
    _mx = max(_cs)
    if _mx >= 0.8:
        return "high"
    if _mx >= 0.6:
        return "mid"
    return "low"


def get_applier_dispatch_summary() -> dict:
    """★326批刀0：最近一次分发留痕（只读拷贝），供压测/日报取数。"""
    _h = _hub
    if _h is None:
        return {}
    return dict(getattr(_h, "_applier_last_dispatch", {}) or {})


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
            except Exception as e:
                silent_exc(e, where="nucleus.mnemosyne.verification_learning_hub::shutdown_verification_learning_hub L543")
