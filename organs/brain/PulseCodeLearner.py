# -*- coding: utf-8 -*-
"""
PulseCodeLearner —— 代码自主学习器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 扫描、解析并理解框架自身代码结构，把代码知识沉淀为可检索的知识节点，支撑自我认知与代码类问题回答。
机制: 以 BasePulseOrgan 为基类接收脉冲驱动，通过 set_node_pool / set_knowledge_tree 等注入依赖；对源码做结构与有效性校验（_is_error_structure / _is_valid_structure），过滤异常结构后写入节点池与知识树；支持运行时参数热刷新与共振条件上报。
定位: 框架的「自我代码认知」器官，为知识检索与自主进化提供代码侧素材，不参与主回答链路的实时决策。
"""
import logging
import os
import random
import re
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    DigestEvent,
    HeartEvent,
    InterestEvent,
    LogLevel,
    LungEvent,
    SystemEvent,
)
from nucleus.knowledge_noise_filter import clean_content_text
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.self_inspector import get_self_inspector
from nucleus.data.DataAccessLayer import safe_read_json
from config import TIMEOUT_CONFIG
from nucleus.const import Event


# ★主线第16批 T3/P2-97：模块级 logger（必须放在全部 import 之后，
#   否则赋值语句会关闭 ruff 的 import 区 → 其后 import 全部判 E402）
_code_learner_logger = logging.getLogger("PulseCodeLearner")


class PulseCodeLearner(BasePulseOrgan):
    """
    代码自主学习器官（v16.0 新增）

    从 PulseInnerWorld 中拆分出来的独立器官，
    专门负责理解和分析自己的代码结构。
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'code_learn_scan_interval' in _rp and hasattr(self, '_scan_interval'):
                setattr(self, '_scan_interval', _rp['code_learn_scan_interval'])
            if 'code_learn_max_methods_per_scan' in _rp and hasattr(self, '_max_methods_per_scan'):
                setattr(self, '_max_methods_per_scan', _rp['code_learn_max_methods_per_scan'])
            if 'code_learn_distill_threshold' in _rp and hasattr(self, '_distill_threshold'):
                setattr(self, '_distill_threshold', _rp['code_learn_distill_threshold'])
            if 'code_learn_param_audit_enabled' in _rp and hasattr(self, '_param_audit_enabled'):
                setattr(self, '_param_audit_enabled', _rp['code_learn_param_audit_enabled'])
            # ★P3-1修复（第十批）：批次超时时间支持热加载
            if 'code_learn_batch_timeout' in _rp and hasattr(self, '_batch_timeout_seconds'):
                setattr(self, '_batch_timeout_seconds', _rp['code_learn_batch_timeout'])
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    # ==================================================================
    # ★主线第16批 T3/P2-97：代码修复蒸馏子进程 —— 重试配置 / 原因归类 / 统计
    # ==================================================================
    @staticmethod
    def _code_distill_retry_config() -> tuple[bool, int]:
        """读取蒸馏子进程重试配置：(开关, 最大重试次数)。"""
        enabled, retries = True, 1
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "ENABLE_CODE_DISTILL_RETRY", True))
            retries = int(getattr(_cfg, "CODE_DISTILL_MAX_RETRIES", 1) or 1)
        except Exception as _e:
            _code_learner_logger.debug(
                f"蒸馏重试配置读取失败，使用默认值(True,1): {type(_e).__name__}: {_e}")
        return enabled, max(0, retries)

    @staticmethod
    def _describe_code_distill_failure(result: dict[str, Any]) -> str:
        """从子进程返回结果中提取可读失败原因（第15批 T3 已落盘的字段）。"""
        if not isinstance(result, dict):
            return "结果格式异常"
        _status = str(result.get("status", "") or "unknown")
        _err = str(result.get("error", "") or "").strip()
        if _err:
            return f"{_status}: {_err[:300]}"
        _tb = str(result.get("crash_traceback", "") or "").strip()
        if _tb:
            _lines = [ln for ln in _tb.strip().splitlines() if ln.strip()]
            return f"{_status}: {_lines[-1][:300] if _lines else 'traceback 为空'}"
        _tail = str(result.get("stderr_tail", "") or "").strip()
        if _tail:
            return f"{_status}: {_tail[-300:]}"
        return f"{_status}: 无详细信息"

    def _record_code_distill(self, outcome: str, reason: str = "") -> None:
        """累计蒸馏子进程统计（成功率 + 失败原因分布）。"""
        try:
            _st = self._code_distill_stats
            _st["attempts"] += 1
            if outcome == "success":
                _st["success"] += 1
                if _st["attempts"] > 1 and _st["success"] > 0:
                    _st["retry_success"] += 1
            else:
                _st["failed"] += 1
                _key = (reason or "未知").split(":")[0].strip()[:40] or "未知"
                _st["reasons"][_key] = _st["reasons"].get(_key, 0) + 1
        except Exception as _e:
            _code_learner_logger.debug(
                f"蒸馏统计记录失败（已忽略）: {type(_e).__name__}: {_e}")

    def get_code_distill_stats(self) -> dict[str, Any]:
        """代码修复蒸馏子进程统计（成功率 / 失败原因分布），供自省与诊断消费。"""
        _st = getattr(self, "_code_distill_stats", None)
        if not isinstance(_st, dict):
            return {"attempts": 0, "success": 0, "failed": 0,
                    "retry_success": 0, "reasons": {}, "degraded_to_main": 0,
                    "success_rate": 0.0}
        _out = dict(_st)
        _out["reasons"] = dict(_st.get("reasons", {}))
        _att = int(_st.get("attempts", 0) or 0)
        _out["success_rate"] = round(int(_st.get("success", 0)) / _att, 4) if _att else 0.0
        return _out

    def __init__(self, organ_name: str = "代码学习"):
        # ★主线第16批 T3/P2-97：代码修复蒸馏子进程的成败统计
        self._code_distill_stats: dict[str, Any] = {
            "attempts": 0, "success": 0, "failed": 0,
            "retry_success": 0, "reasons": {}, "degraded_to_main": 0,
        }
        super().__init__(organ_name)

        # 依赖注入
        self.node_pool = None
        self.frequency_codec = None
        self.knowledge_tree = None
        self._evolution_sandbox = None  # ★骨架优化：进化沙箱引用（由 main 注入，缺失时回退单例）
        self._self_inspector = None     # ★骨架优化：代码审查器引用（由 main 注入，缺失时回退单例）

        # ===== 代码理解进度 =====
        self._code_understanding_progress: dict[str, Any] = {}

        # ===== 调用关系图 =====
        self._code_call_graph: dict[str, dict[str, Any]] = {}

        # ===== 心跳计数器 =====
        # ★v30.0负载均衡修复：初始心跳计数加随机错峰偏移，避免与全框架其他器官
        # 的「取模触发」任务在同一心跳点共振（原所有器官 _heartbeat_count=0 同步增长，
        # 在 300/600 次心跳等公倍数点大量任务同时触发，形成负载尖峰）。
        self._heartbeat_count = random.randint(1, 499)
        self._code_learn_interval = 15  # 每15次心跳触发一次学习（基础值，运行时乘 tempo）
        self._code_review_interval = 500  # 每500次心跳触发一次代码审视
        # ★P3-1修复（第十批）：批次硬超时从硬编码 120s 放宽到 180s 并支持热加载。
        #   星轨 9 小时日志 5 次「批次超时(耗时>120s)」跳过剩余方法，
        #   批次规模动态加大后（5→12→20），部分大方法分析耗时较长，
        #   120s 偏紧。放宽到 180s 后由 RUNTIME_PARAMS.code_learn_batch_timeout 可调。
        self._batch_timeout_seconds = 180

        # ★v23.0新增：大模型深度分析节流
        self._analysis_last_submit_time = 0.0   # 上次提交分析的时间戳
        self._analysis_min_interval = 2.0       # 两次提交至少间隔2秒
        # ★v25.0新增：接入全框架终身学习引擎Hub
        self._vl_hub = None
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            self._vl_hub = get_verification_learning_hub()
        except Exception:
            self._vl_hub = None

        # ★进化闭环升级(完美级): 健康度驱动的主动进化触发器
        self._evolution_driver = None
        try:
            import os as _os

            from nucleus.evolution.EvolutionDriver import EvolutionDriver
            _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            self._evolution_driver = EvolutionDriver(_project_root)
        except Exception:
            self._evolution_driver = None

        # ★进化闭环升级(阶段A): 定期测试调度器（tools/ 测试工具纳入检测体系）
        self._periodic_test_scheduler = None
        self._test_run_interval = 100  # 每100次心跳跑一轮轻量测试
        try:
            import os as _os2

            from nucleus.evolution.PeriodicTestScheduler import PeriodicTestScheduler
            _project_root2 = _os2.path.dirname(_os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__))))
            self._periodic_test_scheduler = PeriodicTestScheduler(_project_root2)
        except Exception:
            self._periodic_test_scheduler = None

        # ★属性初始化完整性补全（自动审查添加）
        self._cached_organ_structure = {}
        self._completed_organs = set()
        self._handbook_milestones = {}
        self._last_code_review_at = 0.0
        self._last_portrait_refresh_pct = 0.0
        self._layered_cache = {}
        self._pending_extra_state = {}
        self._survival_state = {}
        self._tooling_sweep_counter = 0

        # ★主线第61批 T1/P1（重操作错峰调度·临时拉平）：代码学习内部三任务分离偏移。
        #   问题：代码学习(每15次心跳)/定期测试(每100次)/代码审视(每500次) 共用同一个
        #         self._heartbeat_count，在第500次心跳时三者同时触发；三个都是高资源
        #         消耗任务，叠加后形成负载尖峰（正是 T+20~40 分钟告警集中的来源）。
        #   修复：给「定期测试」与「代码审视」各加一个**独立随机相位偏移**，使其不再
        #         与代码学习在同一心跳点共振；「代码学习」保持原有随机初始计数不变。
        #   灰度：config.ENABLE_CODE_LEARN_TASK_OFFSET=False → 两偏移均置 0（复现旧行为）。
        #   范围：由 config.TASK_OFFSET_CONFIG 提供（T3 配置化，不硬编码）。
        self._test_heartbeat_offset = 0
        self._review_heartbeat_offset = 0
        _m61_cfg = None
        try:
            import config as _m61_cfg_mod
            _m61_cfg = _m61_cfg_mod
            _m61_offset_on = bool(getattr(_m61_cfg, "ENABLE_CODE_LEARN_TASK_OFFSET", True))
        except Exception as _m61_e:
            # 配置模块不可用不应让修复整体失效 → 仍按默认范围开启
            _m61_offset_on = True
            self._log(LogLevel.DEBUG,
                      f"[代码学习] 错峰配置读取失败，使用默认范围: "
                      f"{type(_m61_e).__name__}: {_m61_e}")
        if _m61_offset_on:
            _m61_test_max = 99
            _m61_review_max = 499
            try:
                if _m61_cfg is not None:
                    _m61_test_max = int(_m61_cfg.get_task_offset("code_learn_test_offset_max", 99))
                    _m61_review_max = int(_m61_cfg.get_task_offset("code_learn_review_offset_max", 499))
            except Exception as _m61_e2:
                self._log(LogLevel.DEBUG,
                          f"[代码学习] 偏移范围读取失败，使用默认: "
                          f"{type(_m61_e2).__name__}: {_m61_e2}")
            self._test_heartbeat_offset = random.randint(0, max(0, _m61_test_max))
            self._review_heartbeat_offset = random.randint(0, max(0, _m61_review_max))
        self._log(LogLevel.INFO,
                  f"[代码学习] 任务错峰偏移: 代码学习=随机初始{self._heartbeat_count}, "
                  f"定期测试=偏移{self._test_heartbeat_offset}次心跳, "
                  f"代码审视=偏移{self._review_heartbeat_offset}次心跳")
    # ========== 框架注入接口 ==========

    def set_node_pool(self, pool):
        self.node_pool = pool

    def set_frequency_codec(self, codec):
        self.frequency_codec = codec

    def set_knowledge_tree(self, tree):
        self.knowledge_tree = tree

    def set_info_field(self, info_field):
        """★星轨审查修复：补充info_field注入，心跳脉冲依赖此字段"""
        self.info_field = info_field

    def set_evolution_sandbox(self, sandbox):
        """★骨架优化：注入进化沙箱（替代跨模块单例直调）"""
        self._evolution_sandbox = sandbox

    def set_self_inspector(self, inspector):
        """★骨架优化：注入代码审查器（替代跨模块单例直调）"""
        self._self_inspector = inspector

    def _get_inspector(self):
        """优先使用注入的审查器，未注入时回退全局单例（保持兼容）"""
        if self._self_inspector is not None:
            return self._self_inspector
        return get_self_inspector()

    @staticmethod
    def _is_error_structure(data: Any) -> bool:
        """★修复：结构化判断 get_organ_code_structure 是否返回「异常」。

        正常返回 {器官名: {method_count, public_methods, directory}}；
        异常返回 {"error": "..."}（单键 error 字典）。
        只检查「是否为仅含 error 键的字典」，不做 str() 子串匹配，
        避免正常结果里方法名/路径含 "error" 子串时被误判为异常。
        """
        return isinstance(data, dict) and len(data) == 1 and "error" in data

    @staticmethod
    def _is_valid_structure(data: Any) -> bool:
        """★修复：结构化判断扫描结果是否为「合法的器官结构字典」。

        合法 = 非空 dict 且不是「仅含 error 键」的异常字典。
        """
        if not isinstance(data, dict) or not data:
            return False
        return not PulseCodeLearner._is_error_structure(data)


    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HeartEvent.BEAT:
            return self._on_heartbeat(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    HeartEvent.BEAT,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 事件处理 ==========

    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        """心跳驱动：独立计数器，不受推理链影响"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._heartbeat_count += 1
        # ★v29/14.45：动态间隔——无对话+硬件充裕时加速学习，有对话时减速
        _learn_interval = self._code_learn_interval
        _review_interval = self._code_review_interval
        try:
            from nucleus.runtime_tempo import get_runtime_tempo
            _tempo = get_runtime_tempo().get_background_tempo()
            _learn_interval = max(1, round(self._code_learn_interval * _tempo))
            _review_interval = max(1, round(self._code_review_interval * _tempo))
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 临时诊断日志：确认心跳计数和触发条件
        if self._heartbeat_count <= 30 or self._heartbeat_count % 50 == 0:
            self._log(LogLevel.DEBUG,
                     f"心跳计数: {self._heartbeat_count}, "
                     f"距下次代码学习: {_learn_interval - (self._heartbeat_count % _learn_interval)}次 (tempo间隔={_learn_interval})")

        # 每15次心跳触发一次代码自学习
        if self._heartbeat_count % _learn_interval == 0:
            if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
                self.info_field.submit_adaptive_task(
                    self._learn_own_code_structure,
                    task_name="代码自学习",
                    priority="normal"
                )
            else:
                # ★星轨审查修复：info_field未就绪时跳过，不阻塞心跳主线程
                self._log(LogLevel.WARNING,
                         "info_field未就绪，跳过本次代码学习，等待下一心跳周期")

        # 每500次心跳触发一次代码审视
        # ★主线第61批 T1：叠加独立随机相位偏移，避免与代码学习/定期测试同点共振。
        #   偏移缺失时（如 __new__ 轻量实例）getattr 兜底为 0 → 保持旧行为。
        _m61_review_off = getattr(self, "_review_heartbeat_offset", 0)
        if (self._heartbeat_count + _m61_review_off) % _review_interval == 0:
            if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
                self.info_field.submit_adaptive_task(
                    self._review_own_code_issues,
                    task_name="代码审视",
                    priority="normal"
                )
            else:
                # ★星轨审查修复：info_field未就绪时跳过
                self._log(LogLevel.WARNING,
                         "info_field未就绪，跳过本次代码审视，等待下一心跳周期")

        # ★进化闭环升级(完美级): 健康度驱动的主动进化触发
        #   不再被动等待 500 次心跳才自审——当健康度下降/跌破危险线时，
        #   主动触发一次进化审查（受评估间隔与冷却双重约束，避免高频震荡）。
        self._maybe_trigger_health_driven_evolution()

        # ★进化闭环升级(阶段A): 定期测试调度（tools/ 验证脚本纳入检测体系）
        #   每 100 次心跳跑一轮轻量验证脚本，作为框架健康度的「行为层」验证，
        #   补充 HealthScore 的「日志层」评分。测试失败时记录并触发告警。
        self._maybe_run_periodic_tests()

        # ★P1-2(2026-09-03): 运行时日志验证——每200次心跳验证已提交补丁的修复效果
        #   给补丁足够运行时间（约27分钟）后验证，确认修复是否真正有效
        if self._heartbeat_count > 0 and self._heartbeat_count % 200 == 0:
            try:
                from nucleus.reasoning.SafeEvolutionExecutor import (
                    SafeEvolutionExecutor,
                )
                _executor = SafeEvolutionExecutor()
                _verify_result = _executor.verify_submitted_patches()
                if _verify_result.get("total", 0) > 0:
                    self._log(LogLevel.INFO,
                             f"运行时补丁验证: 总数={_verify_result['total']}, "
                             f"通过={_verify_result['verified']}, "
                             f"失败={_verify_result['failed']}, "
                             f"平均效果={_verify_result.get('avg_effectiveness', 0):.0%}")
            except Exception as _verify_err:
                self._log(LogLevel.DEBUG, f"运行时补丁验证异常: {_verify_err}")

        # ★A2修复（主线A）：验证「已应用补丁」的运行时效果，失败自动回滚。
        #   与上面 verify_submitted_patches 互补——上面验证 pending（未应用），
        #   此处验证 history（已应用），补上「应用后回归确认 + 失败回滚」闭环。
        if self._heartbeat_count > 0 and self._heartbeat_count % 200 == 0:
            try:
                from nucleus.reasoning.SafeEvolutionExecutor import (
                    SafeEvolutionExecutor,
                )
                _executor2 = SafeEvolutionExecutor()
                _applied_result = _executor2.verify_applied_patches()
                if _applied_result.get("total", 0) > 0:
                    self._log(LogLevel.INFO,
                             f"已应用补丁运行时验证: 总数={_applied_result['total']}, "
                             f"通过={_applied_result['verified']}, "
                             f"失败={_applied_result['failed']}, "
                             f"自动回滚={_applied_result['rolled_back']}")
            except Exception as _verify_err2:
                self._log(LogLevel.DEBUG, f"已应用补丁验证异常: {_verify_err2}")

        return {"status": "ok", "heartbeat_count": self._heartbeat_count}

    def _maybe_run_periodic_tests(self):
        """定期测试调度（★阶段A: tools/ 测试工具纳入框架检测体系）。"""
        if self._periodic_test_scheduler is None:
            # ★D1修复（P1，2026-09-05）：调度器初始化失败原本**完全静默**——
            #   __init__ 的 119-128 行 try/except 在失败时直接置 None 且不留痕，
            #   导致「定期测试从未执行」这件事完全无从察觉。此处节流告警。
            self._warn_periodic_scheduler_missing()
            return
        # ★主线第61批 T1：叠加独立随机相位偏移（getattr 兜底 0 = 旧行为）。
        _m61_test_off = getattr(self, "_test_heartbeat_offset", 0)
        if (self._heartbeat_count + _m61_test_off) % self._test_run_interval != 0:
            return
        # 通过 info_field 异步提交，避免阻塞心跳主线程
        def _run_tests():
            try:
                _result = self._periodic_test_scheduler.run_cycle(include_heavy=False)
                _passed = _result.get("total_passed", 0)
                _failed = _result.get("total_failed", 0)
                if _failed > 0:
                    self._log(LogLevel.WARNING,
                             f"定期测试: {_passed}通过/{_failed}失败，存在回归风险")
                    # ★R3修复（P1）：原实现只遍历 _result["light"]["results"]，
                    #   完全不看 _result["heavy"]["results"]。
                    #   而每 6 轮调度器会强制拉入 heavy 集
                    #   （PeriodicTestScheduler.py:109 `_run_counter % 6 == 0`），
                    #   此时总测试数由 5(light) 变为 9(light+heavy)。
                    #   实测日志「8通过/1失败」= light 5个全过 + heavy 4个中1个失败，
                    #   失败项落在 heavy 里 → 原代码求出的 _fail_scripts 恒为空列表
                    #   → 「失败脚本:」这行永远不打印 → 无法定位失败用例。
                    #   此处把 light + heavy 结果合并后再筛失败项。
                    _all_results = (
                        list((_result.get("light") or {}).get("results", []) or [])
                        + list((_result.get("heavy") or {}).get("results", []) or [])
                    )
                    _fail_scripts = [
                        r.get("script", "") for r in _all_results
                        if not r.get("passed")
                    ][:3]
                    if _fail_scripts:
                        self._log(LogLevel.WARNING, f"失败脚本: {', '.join(_fail_scripts)}")
                    else:
                        # 有失败计数却筛不出脚本名，说明结果结构与预期不符，需暴露
                        self._log(LogLevel.WARNING,
                                 f"定期测试: 报告{_failed}个失败但未匹配到具体脚本，"
                                 f"结果结构异常(light/heavy键缺失?)，"
                                 f"结果键={list(_result.keys())}")
                    # ★R3补充：打印失败明细（退出码 + 输出尾部），
                    #   用于区分「超时(-1) / 执行异常(-2) / 真失败(非0退出码)」
                    for _r in _all_results:
                        if _r.get("passed"):
                            continue
                        self._log(LogLevel.WARNING,
                                 f"定期测试失败明细: 脚本={_r.get('script')}, "
                                 f"returncode={_r.get('returncode')}, "
                                 f"tail={str(_r.get('output_tail', ''))[-300:]!r}")
                else:
                    self._log(LogLevel.DEBUG, f"定期测试: {_passed}个验证脚本全部通过")
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"定期测试异常(忽略): {_e}")

        if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
            self.info_field.submit_adaptive_task(
                _run_tests,
                task_name="定期测试",
                priority="low"
            )
        else:
            # info_field 未就绪时直接同步跑（轻量脚本耗时可控）
            _run_tests()

    def _warn_periodic_scheduler_missing(self, cooldown: float = 600.0) -> None:
        """★D1修复（P1）：定期测试调度器缺失时**节流**告警（默认 10 分钟一次）。

        背景：
            __init__ 中调度器初始化失败会被 except 静默置 None（119-128 行），
            若不在此处留痕，定期测试「从未执行」将完全不可见——
            实测 50 分钟 / 600 次心跳的日志里「定期测试」字样出现 0 次。

        为什么节流：
            本方法每 100 次心跳才被调用于真正要跑测试的时刻，
            频率本身不高；但 sleep 期间若被其他路径反复调用，
            节流可避免告警刷屏。
        """
        _now = time.time()
        if _now - getattr(self, "_pts_missing_warn_at", 0.0) < cooldown:
            return
        self._pts_missing_warn_at = _now
        try:
            self._log(LogLevel.WARNING,
                      "定期测试未运行: 调度器未初始化(PeriodicTestScheduler 构造失败)，"
                      "tools/ 验证脚本未纳入自动检测")
        except Exception:
            # 日志本身不可用时绝不再抛，避免影响心跳主链路
            pass

    def _maybe_trigger_health_driven_evolution(self):
        """健康度驱动的主动进化触发（★完美级全自主闭环入口）。"""
        if self._evolution_driver is None:
            return
        try:
            import config as _cfg_ev
            _ev_cfg = getattr(_cfg_ev, 'EVOLUTION_CONFIG', {})
            if not _ev_cfg.get("health_driven_evolution_enabled", True):
                return
            _eval = self._evolution_driver.evaluate()
            if not _eval.get("should_evolve"):
                # 首次评估（无基线）时静默建立基线，不算触发
                if _eval.get("baseline") is None and _eval.get("score") is not None:
                    self._evolution_driver.update_baseline(
                        _eval["score"], _eval.get("dimensions"), source="initial"
                    )
                return
            _reason = _eval.get("trigger_reason", "")
            _score = _eval.get("score")
            _delta = _eval.get("delta", 0.0)
            self._log(LogLevel.INFO,
                      f"健康度驱动进化触发: 当前{_score}分, 变化{_delta:+}分, "
                      f"原因={_reason}, 薄弱维度={_eval.get('dimensions_weak', [])}")
            if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
                self.info_field.submit_adaptive_task(
                    self._review_own_code_issues,
                    task_name="健康度驱动进化",
                    priority="high"
                )
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"健康度驱动进化检查异常(忽略): {_e}")

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== R4阶段二：存续编排器钩子（创造层代表） ==========

    def on_survival_low(self, snapshot) -> dict:
        """★R4阶段二：存续低位动作（创造层代表）。

        第一批：设置状态标志 + 日志。第二批接入「高危稳定性锁」——强制暂停
        自主迭代/补丁/重构（这是存续意志的硬约束，第二批落地）。
        """
        self._survival_state = "low"
        self._log(LogLevel.INFO,
                  f"[R4创造层] 存续低位，指数={getattr(snapshot, 'index', '?')}，"
                  f"准备暂停自主迭代（第二批接入高危稳定性锁）")
        return {"layer": "creation", "state": "low"}

    def on_survival_high(self, snapshot) -> dict:
        """★R4阶段二：存续高位动作（创造层代表）。"""
        self._survival_state = "high"
        self._log(LogLevel.INFO,
                  f"[R4创造层] 存续高位，指数={getattr(snapshot, 'index', '?')}，允许自主迭代")
        return {"layer": "creation", "state": "high"}

    # ========== 代码自学习核心 ==========

    # ========== ★主线第65批 T2/P1：代码学习自适应 ==========
    def _m65_code_learning_adaptive_enabled(self) -> bool:
        """代码学习频率自适应总开关（默认开）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_CODE_LEARNING_ADAPTIVE", True))
        except Exception:
            return True

    def _m65_get_load_level(self) -> str:
        """统一系统负载等级（low/medium/high/critical）；异常安全降级 low。"""
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            return get_runtime_metrics().get_system_load().get("load_level", "low")
        except Exception:
            return "low"

    def _learn_own_code_structure(self):
        # ★T3: 自适应降频接线——代码学习
        try:
            from nucleus.runtime_metrics import get_adaptive_controller
            _ctrl = get_adaptive_controller()
            _ctrl.register("code_learning", 60)
            if not _ctrl.should_execute("code_learning"):
                return
        except Exception:
            pass
        """
        代码自主理解（增强版）：批量、深度、结构化地理解自己的代码。

        增强点：
        1. 每次心跳处理 3 个方法，加速学习
        2. 深度提取：参数列表、返回值、调用链、所属模块职责
        3. 存储为结构化知识节点，支持精准检索
        4. 构建数据流图（参数→返回值→调用方），为自我诊断打基础
        """
        try:
            # ★v17.0 D8预埋：追踪入口时间戳（默认关闭，不影响性能）
            _trace_start = time.time()
            inspector = self._get_inspector()
            # ★v17.0修复：缓存扫描结果，整个学习周期只扫描一次
            if not hasattr(self, '_cached_organ_structure') or not self._cached_organ_structure:
                self._cached_organ_structure = inspector.get_organ_code_structure()
                if self._is_valid_structure(self._cached_organ_structure):
                    _organ_count = len(self._cached_organ_structure)
                    self._log(LogLevel.INFO, f"代码学习扫描完成: {_organ_count}个器官文件")
            code_summary = self._cached_organ_structure
            if not code_summary:
                self._log(LogLevel.WARNING, "代码学习跳过: get_organ_code_structure() 返回空")
                return
            # ★修复：原用 "error" in str(code_summary) 做字符串子串匹配，脆弱误判。
            # 当扫描结果正常、但某个器官的公开方法名/目录名/路径含 "error" 子串
            # （如 handle_error、error_handler）时，会把整段 str 误判为「扫描异常」，
            # 导致代码学习被错误跳过。改为结构化判断：get_organ_code_structure 异常时
            # 返回的是单键 {"error": "..."} 字典，正常时是 {器官名: {...}} 字典，
            # 因此只需检查是否为「只含 error 键的字典」即可，不做字符串子串匹配。
            if self._is_error_structure(code_summary):
                self._log(LogLevel.WARNING, f"代码学习跳过: 扫描异常 - {str(code_summary)[:120]}")
                return

            # 初始化进度表
            if not hasattr(self, '_code_understanding_progress') or not self._code_understanding_progress:
                self._code_understanding_progress = {
                    "total_methods": 0,
                    "understood": 0,
                    "pending": [],
                    "current_organ_idx": 0,  # type: ignore[possibly-unbound]
                    "current_method_idx": 0,  # type: ignore[possibly-unbound]
                    "organs_list": [],
                }

            progress = self._code_understanding_progress
            # ★v17.0 D8预埋：追踪入口（progress已初始化）
            self._trace_entry("_learn_own_code_structure", {
                "progress_understood": progress.get("understood", 0),
                "progress_total": progress.get("total_methods", 0),
            })

            # 确保 pending 键存在
            if "pending" not in progress:
                progress["pending"] = []
            # 首次运行：构建待理解队列，或从快照恢复进度
            if not progress.get("pending"):
                _restored = self._restore_code_progress_from_snapshot()
                _restored_understood_count = 0  # ★v17.0修复：记录从快照恢复的已理解数量
                if _restored and _restored.get("understood", 0) > 0:
                    progress["understood"] = _restored.get("understood", 0)
                    _restored_understood_count = progress["understood"]
                    if not hasattr(self, '_code_call_graph') and _restored.get("call_graph"):
                        self._code_call_graph = _restored["call_graph"]
                    # 从快照恢复 pending 列表
                    _restored_pending = _restored.get("pending", [])
                    if _restored_pending:
                        progress["pending"] = _restored_pending
                        progress["total_methods"] = _restored.get("total_methods", len(_restored_pending))
                        # ★L17修复：恢复日志同口径，钳制 understood 不超过 total_methods
                        _safe_total = max(progress.get("total_methods", 0), 1)
                        _safe_understood = min(progress.get("understood", 0), _safe_total)
                        self._log(LogLevel.INFO,
                                 f"代码理解进度已从快照恢复: {_safe_understood}/{_safe_total}个方法")
                    else:
                        # pending为空时强制重置，但保留已理解计数，触发重新扫描
                        progress["pending"] = []
                        progress["total_methods"] = 0
                        self._log(LogLevel.INFO,
                                 f"代码理解进度已从快照恢复: {progress['understood']}个方法已理解（pending为空，将重新扫描）")

                # 如果 pending 仍然为空（首次运行或快照无 pending），重新扫描
                if not progress.get("pending"):
                    # ★v17.0新增：重建pending前，先从知识库统计已理解的方法数作为进度基准
                    _kb_understood_count = 0
                    if self.node_pool:
                        _existing_code_nodes = self.node_pool.query(
                            evol_level="L2", space_path_prefix="/自我理解/代码", limit=5000
                        )
                        # ★v17.0修复：只统计[自我理解·开头的方法理解节点，排除数据流图和代码关联节点
                        _kb_understood_count = 0
                        for _n in _existing_code_nodes:
                            _val = str(_n.value) if _n.value else ""
                            if _val.startswith("[自我理解·"):
                                _kb_understood_count += 1
                        if _kb_understood_count > 0:
                            self._log(LogLevel.INFO,
                                     f"从知识库恢复代码理解进度: {_kb_understood_count}个方法已理解")

                    all_organs = list(code_summary.keys())
                    for organ_name in all_organs:
                        organ_info = inspector.get_organ_code_structure(organ_name)
                        # ★修复：原 "error" in str(organ_info) 子串匹配脆弱，改为结构化判断
                        if not organ_info or self._is_error_structure(organ_info):
                            continue
                        # ★v17.0新增：记录器官文件的修改时间作为文件指纹
                        _file_path = organ_info.get("file_path", "")
                        _file_mtime = 0.0
                        if _file_path and os.path.exists(_file_path):
                            try:
                                _file_mtime = os.path.getmtime(_file_path)
                            except Exception as e:
                                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                        methods = organ_info.get("methods", [])
                        for method in methods:
                            method_name = method.get("name", "")
                            progress["pending"].append({
                                "organ": organ_name,
                                "method": method_name,
                                "has_doc": bool(method.get("doc", "")),
                                "understood": False,
                                "file_path": _file_path,       # ★v17.0新增：文件路径
                                "file_mtime": _file_mtime,     # ★v17.0新增：文件指纹
                            })
                            progress["total_methods"] += 1

                    # ★v17.0新增：将知识库统计的已理解数作为进度起点
                    if _kb_understood_count > 0:
                        progress["understood"] = _kb_understood_count
                        self._log(LogLevel.INFO,
                                 f"代码理解进度基准: 已理解{_kb_understood_count}个方法，共{progress['total_methods']}个方法待处理")

                    # ★v17.0修复：重建pending后，保留快照中已理解的计数，而不是重置为0
                    # 这样重启后进度显示为"上次已理解数/当前总方法数"，而不是从0开始
                    # ★v17.0修复v2：从知识库中统计已理解的代码方法数量，而非依赖快照
                    # 代码学习节点保存在 /自我理解/代码 路径下，统计L2节点数作为已理解计数
                    _kb_understood = 0
                    if self.node_pool:
                        _code_nodes = self.node_pool.query(
                            evol_level="L2", space_path_prefix="/自我理解/代码", limit=5000
                        )
                        for _n in _code_nodes:
                            _val = str(_n.value) if _n.value else ""
                            if _val.startswith("[自我理解·"):
                                _kb_understood += 1
                    # 取快照恢复数和知识库统计数中较大的那个
                    _effective_understood = max(_restored_understood_count, _kb_understood)
                    if _effective_understood > 0:
                        progress["understood"] = _effective_understood
                        self._log(LogLevel.INFO,
                                 f"代码理解进度恢复: 快照={_restored_understood_count}, 知识库={_kb_understood}, 有效={_effective_understood}")
                    # ★v17.0 Q1修复：扩展扫描范围到整个项目
                    _extra_dirs = ["nucleus", "base", "functions", "tools"]
                    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    for _dir_name in _extra_dirs:
                        _dir_path = os.path.join(_project_root, _dir_name)
                        if not os.path.isdir(_dir_path):
                            continue
                        for _root, _dirs, _files in os.walk(_dir_path):
                            _dirs[:] = [d for d in _dirs if not d.startswith(".") and d not in ("__pycache__",)]
                            for _file_name in _files:
                                if not _file_name.endswith(".py") or _file_name.startswith("_"):
                                    continue
                                _file_path = os.path.join(_root, _file_name)
                                _file_mtime = 0.0
                                try:
                                    _file_mtime = os.path.getmtime(_file_path)
                                except Exception as e:
                                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                                _methods = inspector.parse_methods(_file_path)
                                for _m in _methods:
                                    _method_name = _m.get("name", "")
                                    # 使用文件名（去掉.py后缀）作为器官名
                                    _organ_name = _file_name[:-3]
                                    progress["pending"].append({
                                        "organ": _organ_name,
                                        "method": _method_name,
                                        "has_doc": bool(_m.get("doc", "")),
                                        "understood": False,
                                        "file_path": _file_path,
                                        "file_mtime": _file_mtime,
                                    })
                                    progress["total_methods"] += 1

                    # ★v17.0方向四：扫描根目录下的独立Python文件
                    _root_files = ["main.py", "config.py", "pulse_doctor.py"]
                    for _rf in _root_files:
                        _rf_path = os.path.join(_project_root, _rf)
                        if not os.path.exists(_rf_path):
                            continue
                        _rf_mtime = 0.0
                        try:
                            _rf_mtime = os.path.getmtime(_rf_path)
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                        _rf_methods = inspector.parse_methods(_rf_path)
                        for _m in _rf_methods:
                            _method_name = _m.get("name", "")
                            _organ_name = _rf[:-3]
                            progress["pending"].append({
                                "organ": _organ_name,
                                "method": _method_name,
                                "has_doc": bool(_m.get("doc", "")),
                                "understood": False,
                                "file_path": _rf_path,
                                "file_mtime": _rf_mtime,
                            })
                            progress["total_methods"] += 1
                    # ★v17.0新增：扫描docs目录下的核心设计文档
                    # 这些文档定义了框架的根本原则和设计规范，是曈曈理解"为什么"的关键
                    _docs_dir = os.path.join(_project_root, "docs")
                    _docs_files = [
                        "BLUEPRINT_CONSTITUTION.md",   # 演化宪法——三条使命、十四条稳态规则
                        "CODE_STYLE.md",               # 代码风格规范——反模式速查、设计原则
                        "LESSONS_LEARNED.md",           # 核心经验教训——260+条避坑清单
                        "框架调用关系全景图.md",         # 器官间协作参考框架
                    ]
                    for _df in _docs_files:
                        _df_path = os.path.join(_docs_dir, _df)
                        if not os.path.exists(_df_path):
                            continue
                        _df_mtime = 0.0
                        try:
                            _df_mtime = os.path.getmtime(_df_path)
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                        # 读取文档内容并提取核心段落作为知识
                        try:
                            with open(_df_path, encoding="utf-8") as _f:
                                _doc_content = _f.read()
                            # ★v18.0优化：按章节结构化提取，替代全文存储
                            # 从文档中提取标题层级作为知识树路径
                            _doc_name = _df.replace(".md", "")
                            _base_path = f"/自我/架构/设计文档/{_doc_name}"
                            if self.knowledge_tree:
                                self.knowledge_tree.register_path(_base_path)

                            # 按 ## 标题拆分章节
                            _sections = re.split(r'\n##\s+', _doc_content)
                            _section_count = 0
                            _total_knowledge_points = 0  # v20.0新增：统计提取的知识点总数

                            for _section in _sections:
                                if not _section.strip():
                                    continue
                                # 提取章节标题（## 之后到换行符之前的内容）
                                _lines = _section.split('\n', 1)
                                _section_title = _lines[0].strip()
                                if not _section_title or len(_section_title) < 2:
                                    _section_title = "概述"
                                # 清理标题中的特殊字符，避免路径非法
                                _safe_title = re.sub(r'[#*:<>"|?/\\]', '', _section_title)[:30]
                                # 取章节正文
                                _section_body = _lines[1].strip() if len(_lines) > 1 else ""
                                _section_summary = _section_body[:500]
                                if len(_section_summary) < 20:
                                    continue  # 跳过内容过短的章节

                                # 从章节标题中提取关键词
                                _section_kw = re.findall(r'[\u4e00-\u9fff]{2,4}', _section_title)
                                _section_kw = [kw for kw in _section_kw if kw not in ('第一部分', '第二部分', '第三部分')]

                                # ===== v20.0新增：多知识点拆分 =====
                                # 从章节正文中按列表项拆分独立知识点
                                _knowledge_points = []
                                # 按 "- " 或 "1. " 或 "**xxx**" 等模式拆分独立知识点
                                _point_patterns = [
                                    r'(?:^|\n)\s*[-*]\s+(.+?)(?=\n\s*[-*]\s+|\n\s*\d+\.\s+|\n##|\n\*\*|\Z)',
                                    r'(?:^|\n)\s*\d+\.\s+(.+?)(?=\n\s*\d+\.\s+|\n\s*[-*]\s+|\n##|\Z)',
                                ]
                                for _pattern in _point_patterns:
                                    _matches = re.findall(_pattern, _section_body, re.DOTALL)
                                    for _m in _matches:
                                        _point_text = _m.strip()
                                        if len(_point_text) >= 15 and _point_text not in _knowledge_points:
                                            _knowledge_points.append(_point_text[:120])

                                # 如果拆分出≥2个独立知识点，为每个知识点创建独立节点
                                if len(_knowledge_points) >= 2:
                                    for _kp_idx, _kp_text in enumerate(_knowledge_points[:8]):  # 最多8个知识点  # type: ignore[possibly-unbound]
                                        # 从知识点中提取关键词
                                        _kp_kw = re.findall(r'[\u4e00-\u9fff]{2,4}', _kp_text)
                                        _kp_kw = [kw for kw in _kp_kw if kw not in ('什么是', '是什么', '这个', '那个', '一个', '一种')][:4]

                                        _kp_value = f"[设计文档·{_doc_name}·{_safe_title}·要点{_kp_idx+1}] {_kp_text}"  # type: ignore[possibly-unbound]
                                        _kp_path = f"{_base_path}/{_safe_title}/要点{_kp_idx+1}"  # type: ignore[possibly-unbound]
                                        if self.knowledge_tree:
                                            self.knowledge_tree.register_path(_kp_path)

                                        _kp_node = PulseNode(
                                            value=_kp_value,
                                            keywords=[
                                                "设计文档", _doc_name, _safe_title,
                                                f"要点{_kp_idx + 1}", *_kp_kw,
                                            ],  # type: ignore[possibly-unbound]
                                            source_organ=self.organ_name,
                                            evol_level=PulseNode.EVOL_L2,
                                            importance=PulseNode.IMPORTANCE_A,
                                            abstraction=0.65,
                                            space_path=_kp_path,
                                        )
                                        _kp_node.view_mode = "INNER_VIEW"
                                        _kp_node.trigger_reason = f"self_understanding.design_doc:{_doc_name}"
                                        _health = _kp_node.evaluate_node_health(check_type="quality")
                                        _kp_node.trust_score = max(65.0, _health["trust_score"])
                                        if self.frequency_codec:
                                            self.frequency_codec.encode_node(_kp_node)
                                        if self.node_pool:
                                            _kp_node.value = clean_content_text(_kp_node.value) or _kp_node.value
                                            self.node_pool.add(_kp_node)
                                        _total_knowledge_points += 1

                                # ===== v20.0新增结束 =====

                                # 保留章节级节点作为"目录"（无论是否拆分知识点都创建）
                                _doc_knowledge = (
                                    f"[设计文档·{_doc_name}·{_safe_title}] {_section_summary}"
                                )
                                _section_path = f"{_base_path}/{_safe_title}"
                                if self.knowledge_tree:
                                    self.knowledge_tree.register_path(_section_path)

                                _doc_node = PulseNode(
                                    value=_doc_knowledge,
                                    keywords=["设计文档", _doc_name, _safe_title, *_section_kw[:3]],
                                    source_organ=self.organ_name,
                                    evol_level=PulseNode.EVOL_L2,
                                    importance=PulseNode.IMPORTANCE_A,
                                    abstraction=0.7,
                                    space_path=_section_path,
                                )
                                _doc_node.view_mode = "INNER_VIEW"
                                _doc_node.trigger_reason = f"self_understanding.design_doc:{_doc_name}"
                                _health = _doc_node.evaluate_node_health(check_type="quality")
                                _doc_node.trust_score = max(65.0, _health["trust_score"])
                                if self.frequency_codec:
                                    self.frequency_codec.encode_node(_doc_node)
                                if self.node_pool:
                                    _doc_node.value = clean_content_text(_doc_node.value) or _doc_node.value
                                    self.node_pool.add(_doc_node)

                                self._emit(DigestEvent.KNOWLEDGE, {
                                    "content": _doc_knowledge,
                                    "source_organ": self.organ_name,
                                    "trigger_reason": f"self_understanding.design_doc:{_doc_name}",
                                    "importance": "A",
                                    "view_mode": "INNER_VIEW",
                                    "space_path": _section_path,
                                }, priority=3, layer="L2")
                                _section_count += 1

                            # 日志中增加知识点统计
                            if _total_knowledge_points > 0:
                                self._log(LogLevel.INFO,
                                         f"设计文档学习({_doc_name}): {_section_count}个章节, "
                                         f"{_total_knowledge_points}个独立知识点")

                            # 如果章节拆分失败，回退到原有全文存储逻辑
                            if _section_count == 0 and len(_doc_content) > 100:
                                _doc_knowledge = (
                                    f"[设计文档·{_doc_name}] "
                                    f"这是曈曈框架的核心设计文档，定义了框架的根本原则和设计规范。"
                                    f"文档内容摘要：{_doc_content[:2000]}"
                                )
                                _doc_node = PulseNode(
                                    value=_doc_knowledge,
                                    keywords=["设计文档", _doc_name, "框架规范", "架构原则"],
                                    source_organ=self.organ_name,
                                    evol_level=PulseNode.EVOL_L2,
                                    importance=PulseNode.IMPORTANCE_A,
                                    abstraction=0.75,
                                    space_path=f"/自我/架构/设计文档/{_doc_name}",
                                )
                                _doc_node.view_mode = "INNER_VIEW"
                                _doc_node.trigger_reason = f"self_understanding.design_doc:{_doc_name}"
                                _health = _doc_node.evaluate_node_health(check_type="quality")
                                _doc_node.trust_score = max(65.0, _health["trust_score"])
                                if self.frequency_codec:
                                    self.frequency_codec.encode_node(_doc_node)
                                if self.node_pool:
                                    _doc_node.value = clean_content_text(_doc_node.value) or _doc_node.value
                                    self.node_pool.add(_doc_node)
                                if self.knowledge_tree:
                                    self.knowledge_tree.register_path(f"/自我/架构/设计文档/{_doc_name}")
                                self._emit(DigestEvent.KNOWLEDGE, {
                                    "content": _doc_knowledge,
                                    "source_organ": self.organ_name,
                                    "trigger_reason": f"self_understanding.design_doc:{_doc_name}",
                                    "importance": "A",
                                    "view_mode": "INNER_VIEW",
                                    "space_path": f"/自我/架构/设计文档/{_doc_name}",
                                }, priority=3, layer="L2")
                                _section_count = 1
                        except Exception as _doc_e:
                            self._log(LogLevel.DEBUG, f"设计文档学习失败({_df}): {_doc_e}")
                    # ★v17.0优化：优先处理高频查询器官，加速调用图数据积累
                    _priority_organs = [
                        "PulseInnerWorld", "PulseLiver", "PulseCortex",
                        "PulseSubconscious", "PulseController", "PulseSelfAwareness",
                        "PulseStomach", "PulseKidney", "PulseLung", "PulseHeart",
                        "PulseCodeLearner", "PulseReflection",
                    ]
                    def _organ_priority(pending_item):
                        _organ = pending_item.get("organ", "")
                        if _organ in _priority_organs:
                            return _priority_organs.index(_organ)
                        return len(_priority_organs)
                    progress["pending"].sort(key=_organ_priority)
                    self._log(LogLevel.INFO, "代码理解队列已按优先级排序（核心器官优先）")
                    self._log(LogLevel.INFO,
                             f"代码理解初始化: 共{progress['total_methods']}个方法待理解"
                             + (f"（已理解{_effective_understood}个）" if _effective_understood > 0 else ""))
            # 取出待理解的方法
            pending = [p for p in progress.get("pending", []) if not p.get("understood", False)]

            # ===== v20.0新增：代码变化检测——已完成学习的方法如果源文件被修改，重新加入学习队列 =====
            _reactivated_count = 0
            for _p in progress.get("pending", []):
                if _p.get("understood", False) and _p.get("file_path"):
                    _current_mtime = 0.0
                    try:
                        if os.path.exists(_p["file_path"]):
                            _current_mtime = os.path.getmtime(_p["file_path"])
                    except Exception as e:
                        self._log(LogLevel.ERROR, f'异常: {e}')
                    if _current_mtime > 0 and _current_mtime != _p.get("file_mtime", 0):
                        _p["understood"] = False
                        _p["file_mtime"] = _current_mtime
                        _reactivated_count += 1
            if _reactivated_count > 0:
                progress["understood"] = max(0, progress["understood"] - _reactivated_count)
                # 重新计算pending
                pending = [p for p in progress.get("pending", []) if not p.get("understood", False)]
                self._log(LogLevel.INFO,
                         f"代码变化检测: {_reactivated_count}个已学习方法因源文件变化重新加入学习队列")
            # ===== v20.0新增结束 =====

            if not pending:
                return

            # ★v25.1吞吐量优化: 动态批次加大（知识质量链路已完善，可承受更高吞吐）
            _pending_count = len(pending)
            if _pending_count > 500:
                _batch_size = 20   # 原15
            elif _pending_count > 200:
                _batch_size = 12   # 原8
            else:
                _batch_size = 5    # 原3
            _batch = pending[:_batch_size]

            # ★主线第65批 T2/P1：代码学习自适应（高负载降频/暂停，负载恢复后自动恢复）
            if self._m65_code_learning_adaptive_enabled():
                _lvl = self._m65_get_load_level()
                if _lvl in ("high", "critical"):
                    self._log(LogLevel.WARNING,
                             f"代码学习自适应: 负载{_lvl}偏高，暂停本批次（下周期重试）")
                    return
                if _lvl == "medium":
                    _batch_size = min(_batch_size, 10)
                    self._log(LogLevel.INFO,
                             f"代码学习自适应: 负载中等，批次降至 {_batch_size}")

            # ★P1-4(2026-09-03)：任务协调器生命周期追踪（处理逻辑保持串行，避免共享状态竞态）
            from nucleus.TaskOrchestrator import TaskOrchestrator
            _orch = TaskOrchestrator.get_instance()
            _batch_t0 = time.time()
            _understood_before = progress["understood"]
            self._log(LogLevel.INFO,
                     f"代码学习批次开始: {len(_batch)}个方法, 已理解={_understood_before}")

            # ★P3-1修复（第十批）：批次硬超时从硬编码 120s 改为可配置属性
            #   （默认 180s，支持 RUNTIME_PARAMS.code_learn_batch_timeout 热加载）。
            _timeout = getattr(self, '_batch_timeout_seconds', 180)
            _batch_deadline = _batch_t0 + _timeout
            for _idx, current in enumerate(_batch):  # type: ignore[possibly-unbound]
                # v25.1: 批次超时检查
                if time.time() > _batch_deadline:
                    # ★C6【P3】：超时日志补充「剩余 Z 个将在下一批次处理」，明确告知
                    #   跳过的方法数量，避免「跳过剩余方法」表述含糊。
                    _remaining = len(_batch) - _idx
                    self._log(LogLevel.WARNING,
                             f"代码学习批次超时(已处理{_idx}/{len(_batch)}, 耗时>{_timeout}s)，"
                             f"剩余{_remaining}个将在下一批次处理")  # type: ignore[possibly-unbound]
                    # ★P3-1修复：记录超时次数，供健康评估/容量调整消费
                    self._batch_timeout_count = getattr(self, '_batch_timeout_count', 0) + 1
                    break
                _method_t0 = time.time()
                organ_name = current["organ"]
                method_name = current["method"]

                # 获取方法详情
                try:
                    method_detail = inspector.get_method_body(organ_name, method_name)
                except Exception as _method_e:
                    self._log(LogLevel.DEBUG, f"获取方法详情失败({organ_name}.{method_name}): {_method_e}")
                    current["understood"] = True
                    progress["understood"] += 1
                    continue

                if not method_detail:
                    current["understood"] = True
                    progress["understood"] += 1
                    continue

                # 构建深度知识内容（先用原始数据初始化）
                _args = method_detail.get("args", "无")
                _body = method_detail.get("body", "")
                _called_methods = method_detail.get("called_methods", [])
                _has_doc = method_detail.get("has_doc", False)
                _doc = method_detail.get("doc", "")

                # ★v25.0新增：四层分析增强
                _semantic_hints = []
                _verification_suggestions = []
                _file_path_for_analysis = current.get("file_path", "")
                if _file_path_for_analysis and os.path.exists(_file_path_for_analysis):
                    if not hasattr(self, '_layered_cache'):
                        self._layered_cache = {}
                    if _file_path_for_analysis not in self._layered_cache:
                        # v25.1: 大文件跳过分层AST分析(>300KB或>8000行)，避免批次卡死
                        _fsize = 0
                        _flines = 0
                        try:
                            _fsize = os.path.getsize(_file_path_for_analysis)
                            with open(_file_path_for_analysis, encoding='utf-8', errors='ignore') as _fcount:
                                _flines = sum(1 for _ in _fcount)
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                        if _fsize > 300 * 1024 or _flines > 8000:
                            self._log(LogLevel.DEBUG,
                                     f"代码学习: 跳过大文件分层分析 {os.path.basename(_file_path_for_analysis)}"
                                     f"({_flines}行/{_fsize//1024}KB)")
                            self._layered_cache[_file_path_for_analysis] = None
                        else:
                            try:
                                self._layered_cache[_file_path_for_analysis] = inspector.analyze_code_layered(
                                    _file_path_for_analysis, node_pool=self.node_pool
                                )
                            except Exception:
                                self._layered_cache[_file_path_for_analysis] = None
                    _layered = self._layered_cache.get(_file_path_for_analysis)
                    if _layered:
                        # 第一层：精确参数和doc
                        for _m in _layered.get("layer1_ast_structure", {}).get("methods", []):
                            if _m.get("name") == method_name:
                                _args = ", ".join(_m.get("arg_names", [])) or "无"
                                if _m.get("doc"):
                                    _doc = _m["doc"]
                                    _has_doc = True
                                break
                        # 第二层：精确调用图
                        _layered_calls = _layered.get("layer2_call_dataflow", {}).get("call_graph", {}).get(method_name, [])
                        if _layered_calls:
                            _called_methods = _layered_calls
                        # 第三层：语义提示
                        for _h in _layered.get("layer3_semantic_hints", []):
                            if _h.get("method") == method_name:
                                _semantic_hints = _h.get("hints", [])
                                break
                        # 第四层：验证建议
                        for _v in _layered.get("layer4_verification_hints", []):
                            if _v.get("method") == method_name:
                                _verification_suggestions = _v.get("suggestions", [])
                                break

                # 存储调用关系
                if not hasattr(self, '_code_call_graph'):
                    self._code_call_graph = {}
                _call_key = f"{organ_name}.{method_name}"
                self._code_call_graph[_call_key] = {
                    "called_methods": _called_methods,
                    "organ": organ_name,
                    "args": _args,
                    "understood_at": time.time(),
                }

                # 知识节点内容：结构化描述
                if _has_doc and _doc:
                    knowledge_content = (
                        f"[自我理解·{organ_name}.{method_name}] "
                        f"功能: {_doc}。"
                        f"参数: {_args}。"
                        f"内部调用: {', '.join(_called_methods[:5]) if _called_methods else '无'}。"
                    )
                else:
                    _preview = _body[:100].replace('\n', ' ') if _body else "方法体不可读"
                    knowledge_content = (
                        f"[自我理解·{organ_name}.{method_name}] "
                        f"功能: (待大模型分析)。"
                        f"参数: {_args}。"
                        f"代码片段: {_preview}..."
                        f"内部调用: {', '.join(_called_methods[:5]) if _called_methods else '无'}。"
                    )

                # 【v16.0修复】创建知识节点前去重：检查是否已存在相同方法的知识节点
                _already_understood = False
                if self.node_pool:
                    _existing = self.node_pool.query(
                        evol_level="L2", space_path_prefix=f"/自我理解/代码/{organ_name}", limit=50
                    )
                    for _ex_node in _existing:
                        _ex_val = str(_ex_node.value) if _ex_node.value else ""
                        # 检查是否包含相同的方法名和器官名
                        if f".{method_name}" in _ex_val and organ_name in _ex_val:
                            _ex_trust = getattr(_ex_node, 'trust_score', 50.0)
                            if _ex_trust >= 80.0:
                                # 已有高信任节点，跳过创建
                                _already_understood = True
                                current["understood"] = True
                                progress["understood"] += 1
                                break
                            else:
                                # 低信任节点存在，提升信任并跳过创建
                                if hasattr(_ex_node, 'trust_score'):
                                    _ex_node.trust_score = min(95.0, _ex_trust + 10.0)
                                _already_understood = True
                                current["understood"] = True
                                progress["understood"] += 1
                                break
                if _already_understood:
                    continue
                # ===== 去重检查结束 =====

                # 创建知识节点（L2级，高信任）
                _clean_args_keywords = []
                if _args and _args != "无":
                    for _part in _args.split(","):
                        _part = _part.strip()
                        if not _part or _part == "self":
                            continue
                        _param_match = re.match(r'^([a-zA-Z_]\w*)', _part)
                        if _param_match:
                            _param_name = _param_match.group(1)
                            if _param_name and len(_param_name) >= 1:
                                _clean_args_keywords.append(_param_name)
                # ★v17.0修复：过滤代码分析元字段关键词
                _analysis_meta_keywords = {
                    "功能", "参数", "依赖", "风险", "关键步骤",
                    "依赖数据", "潜在风险", "功能描述", "参数列表",
                    "self", "无", "暂无", "无明确风险",
                }
                _filtered_kw = [organ_name, method_name, "自我理解", "代码"]
                for _kw in _clean_args_keywords[:5]:
                    if any(_c in _kw for _c in (":", "：", ",", "，", "=")):
                        continue
                    if len(_kw) <= 1:
                        continue
                    if _kw.lower() in _analysis_meta_keywords:
                        continue
                    _filtered_kw.append(_kw)
                _self_understand_node = PulseNode(
                    value=knowledge_content,
                    keywords=_filtered_kw,
                    source_organ=self.organ_name,
                    evol_level=PulseNode.EVOL_L2,
                    importance=PulseNode.IMPORTANCE_A,
                    abstraction=0.6,
                    space_path=f"/自我理解/代码/{organ_name}",
                )
                _self_understand_node.view_mode = "INNER_VIEW"
                _self_understand_node.trigger_reason = "self_understanding.code_learning"
                # ★v17.0 D2修复：改为调用统一健康度评估，不再硬编码信任度
                _health = _self_understand_node.evaluate_node_health(check_type="quality")
                _self_understand_node.trust_score = max(70.0, _health["trust_score"])
                if _health["quality_score"] < 40.0:
                    _self_understand_node.ephemeral = True
                    self._log(LogLevel.DEBUG,
                             f"代码学习质量偏低: {organ_name}.{method_name} "
                             f"健康分={_health['health_score']:.0f}({_health['level']})")
                if self.frequency_codec:
                    self.frequency_codec.encode_node(_self_understand_node)
                if self.node_pool:
                    _self_understand_node.value = clean_content_text(_self_understand_node.value) or _self_understand_node.value
                    self.node_pool.add(_self_understand_node)
                if self.knowledge_tree:
                    self.knowledge_tree.register_path(f"/自我理解/代码/{organ_name}")

                # 发射消化脉冲
                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": knowledge_content,
                    "source_organ": self.organ_name,
                    "trigger_reason": "self_understanding.code_learning",
                    "importance": "A",
                    "view_mode": "INNER_VIEW",
                    "space_path": f"/自我理解/代码/{organ_name}",
                }, priority=2, layer="L2")

                # ★v25.0新增：记录本地分析质量到终身学习引擎Hub
                if self._vl_hub and _has_doc and _doc:
                    # 本地成功理解（有docstring），记录高置信度
                    try:
                        self._vl_hub.record(
                            organ="code_learner",
                            task_type="local_analysis_success",
                            input_summary=f"{organ_name}.{method_name}",
                            local_result={
                                "method": method_name,
                                "doc": _doc[:100],
                                "semantic_hints": _semantic_hints if '_semantic_hints' in dir() else [],
                            },
                            confidence=0.8,  # 本地分析置信度高
                            relevance_score=0.8,
                            needs_verification=False,
                            verification_result={},
                            api_better=False,
                            lesson=f"本地分析成功理解{organ_name}.{method_name}",
                        )
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                # 对于无docstring的方法，提交给大模型进行深度分析
                # ★v17.0 Q3修复：提交前先检查是否已有高信任节点，避免浪费API调用
                if not _has_doc and _body and len(_body) > 20:
                    _already_analyzed = False
                    if self.node_pool:
                        _existing_check = self.node_pool.query(
                            evol_level="L2", space_path_prefix=f"/自我理解/代码/{organ_name}", limit=30
                        )
                        for _ex in _existing_check:
                            _ex_val = str(_ex.value) if _ex.value else ""
                            if f".{method_name}" in _ex_val and organ_name in _ex_val:
                                _ex_trust = getattr(_ex, 'trust_score', 50.0)
                                if _ex_trust >= 80.0:
                                    _already_analyzed = True
                                    break
                    if not _already_analyzed:
                        self._submit_code_analysis(organ_name, method_name, _args, _body)

                current["understood"] = True
                progress["understood"] += 1

            # ★P1-4：批次结束追踪
            _batch_elapsed = (time.time() - _batch_t0) * 1000
            _batch_new = progress["understood"] - _understood_before
            self._log(LogLevel.INFO,
                     f"代码学习批次完成: 新增理解={_batch_new}个, 耗时={_batch_elapsed:.0f}ms"
                     + (f" [超时跳过{len(_batch)-_idx-1}个]" if time.time() > _batch_deadline else ""))  # type: ignore[possibly-unbound]

            # ★v17.0 F1修复：每器官学够10个方法就生成说明书，而非等全部学完
            _milestone_organs = self._check_organ_milestone(progress)
            for _organ_name in _milestone_organs:
                self._generate_organ_handbook(_organ_name, code_summary)
                self._sync_organ_self_knowledge(_organ_name, "")
                self._emit(Event.NARRATIVE_RECORD, {
                    "content": f"曈曈完成了对{_organ_name}器官的代码学习，理解了它的{self._count_organ_methods(_organ_name)}个方法",
                    "event_type": "learning",
                    "user_name": "系统",
                    "emotional_tone": "positive",
                }, priority=3, layer="L2")
                self._log(LogLevel.INFO, f"器官职责说明书已生成: {_organ_name}")
            # 输出进度
            if progress["understood"] % 10 == 0 and progress["understood"] > 0:
                # ★L17修复：日志与 get_stats 同口径，钳制 understood 不超过 total_methods
                _safe_total = max(progress.get("total_methods", 0), 1)
                _safe_understood = min(progress.get("understood", 0), _safe_total)
                self._log(LogLevel.INFO,
                         f"代码理解进度: {_safe_understood}/{_safe_total}个方法")

            # 定期编织知识网络（每10个方法触发）
            if progress["understood"] % 10 == 0 and progress["understood"] > 0:
                if hasattr(self, '_code_call_graph') and len(self._code_call_graph) >= 10:
                    self._weave_code_knowledge()
            # ★v17.0 D4新增：定期构建调用链路知识（每30个方法触发）
            if progress["understood"] % 30 == 0 and progress["understood"] > 0:
                if hasattr(self, '_code_call_graph') and len(self._code_call_graph) >= 15:
                    self._build_call_chain_knowledge()
            # ★v16.0新增: 每完成一个器官的所有方法后，生成器官摘要
            if progress["understood"] % 30 == 0 and progress["understood"] > 0:
                self._build_data_flow_graph()

            # ★v17.0新增: 每10个方法同步一次自我认知，保持器官列表实时更新
            if progress["understood"] % 10 == 0 and progress["understood"] > 0:
                self._sync_self_knowledge_frequent()

            # ★v17.0支点四: 每推进5%触发一次完整自我画像刷新
            if progress["total_methods"] > 0:
                # 修复：防止因知识库统计导致的 understood 大于 total_methods 而产生超过100%的进度
                _safe_total = max(progress["total_methods"], progress["understood"], 1)
                _current_pct = int(min(100, progress["understood"] / _safe_total * 100))
                _last_refresh_pct = getattr(self, '_last_portrait_refresh_pct', -1)
                # 每5%刷新一次，且每个里程碑只触发一次
                if _current_pct >= 5 and _current_pct % 5 == 0 and _current_pct != _last_refresh_pct:
                    self._last_portrait_refresh_pct = _current_pct
                    self._refresh_self_portrait(_current_pct)

            # ===== v20.0新增：跨器官协作流程自动生成 =====
            if progress["understood"] % 200 == 0 and progress["understood"] > 0:
                self._build_cross_organ_workflows()
            # ===== v20.0新增结束 =====

            # ★v16.0新增: 每50个方法后提取潜在风险
            if progress["understood"] % 50 == 0 and progress["understood"] > 0:
                self._extract_code_risks()

            # ★v17.0 D8预埋：追踪出口
            self._trace_exit("_learn_own_code_structure", {
                "status": "completed",
                "batch_size": len(_batch) if '_batch' in dir() else 0,
            }, duration=time.time() - _trace_start)

        except Exception as e:
            import traceback
            _err_stack = traceback.format_exc()
            self._log(LogLevel.ERROR, f"代码理解执行异常: {e}\n{_err_stack[:500]}")
    def _build_cross_organ_workflows(self):
        """
        v20.0新增：跨器官协作流程自动生成。

        基于已学习的器官职责说明书和调用链路图谱，
        自动推导完整的器官协作流程，生成结构化知识节点。
        """
        if not hasattr(self, '_code_call_graph') or len(self._code_call_graph) < 30:
            return

        # 从知识库获取已有器官职责说明书
        _organ_roles = {}
        if self.node_pool:
            _handbook_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我/架构/器官", limit=60
            )
            for _hn in _handbook_nodes:
                _val = str(_hn.value) if _hn.value else ""
                _kws = _hn.keywords if hasattr(_hn, 'keywords') and _hn.keywords else []
                for _kw in _kws:
                    if _kw.startswith("Pulse") or _kw == "QICA":
                        _organ_roles[_kw] = _val[:200]
                        break

        if len(_organ_roles) < 5:
            return

        _workflows = []

        # 流程1：对话交互流程
        _dialogue_organs = ["PulseCortex", "PulseInnerWorld", "PulseMouth", "PulseLung"]
        _dialogue_chain = self._derive_workflow_chain(
            "对话交互流程", _dialogue_organs, _organ_roles,
            ["用户消息到达时", "推理完成后", "回复输出时"]
        )
        if _dialogue_chain:
            _workflows.append(_dialogue_chain)

        # 流程2：知识写入流程
        _knowledge_organs = ["PulseStomach", "PulseLiver", "PulseKidney"]
        _knowledge_chain = self._derive_workflow_chain(
            "知识写入与演化流程", _knowledge_organs, _organ_roles,
            ["新知识到达时", "知识积累到阈值时", "定期淘汰检查时"]
        )
        if _knowledge_chain:
            _workflows.append(_knowledge_chain)

        # 流程3：后台自主运行流程
        _autonomous_organs = ["PulseHeart", "PulseSubconscious", "PulseCodeLearner"]
        _autonomous_chain = self._derive_workflow_chain(
            "后台自主运行流程", _autonomous_organs, _organ_roles,
            ["心跳触发时", "好奇心探索时", "代码学习周期到达时"]
        )
        if _autonomous_chain:
            _workflows.append(_autonomous_chain)

        # 写入知识库
        _written = 0
        for _wf in _workflows:
            _wf_node = PulseNode(
                value=_wf["content"],
                keywords=["器官协作", "工作流程", _wf["name"]] + _wf["organs"],
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=0.75,
                space_path=f"/自我理解/代码/协作流程/{_wf['name']}",
            )
            _wf_node.view_mode = "INNER_VIEW"
            _wf_node.trigger_reason = "self_understanding.cross_organ_workflow"
            _health = _wf_node.evaluate_node_health(check_type="quality")
            _wf_node.trust_score = max(70.0, _health["trust_score"])
            if self.frequency_codec:
                self.frequency_codec.encode_node(_wf_node)
            if self.node_pool:
                _wf_node.value = clean_content_text(_wf_node.value) or _wf_node.value
                self.node_pool.add(_wf_node)
            if self.knowledge_tree:
                self.knowledge_tree.register_path(f"/自我理解/代码/协作流程/{_wf['name']}")
            _written += 1

        if _written > 0:
            self._log(LogLevel.INFO,
                     f"跨器官协作流程: 自动生成了{_written}条协作流程知识")

    def _derive_workflow_chain(self, name: str, organs: list, organ_roles: dict,
                                 trigger_points: list) -> dict[str, Any] | None:
        """
        基于器官职责描述推导协作流程链。
        """
        _steps = []
        _available_organs = []

        for _organ in organs:
            if _organ in organ_roles:
                _role_text = organ_roles[_organ]
                _role_short = _role_text.split("。")[0] if "。" in _role_text else _role_text[:60]
                _available_organs.append(_organ)
                _steps.append(f"  {len(_steps)+1}. {_organ}：{_role_short}")

        if len(_available_organs) < 2:
            return None

        _content = (
            f"[器官协作流程·{name}]\n"
            f"这是曈曈框架中「{name}」的完整协作链路，由代码自学习自动推导：\n\n"
            + "\n".join(_steps) + "\n\n"
            f"📌 触发时机：{'、'.join(trigger_points[:2])}。"
            f"这{len(_available_organs)}个器官通过脉冲通信协作完成此流程。"
        )

        return {
            "name": name,
            "content": _content,
            "organs": _available_organs,
        }
    def _check_organ_completion(self, progress: dict) -> list:
        """
        ★v17.0新增：检查是否有器官的所有方法都已理解完成。
        返回刚完成的所有器官名列表（每个器官只生成一次说明书）。
        """
        if not hasattr(self, '_completed_organs'):
            self._completed_organs = set()

        # 统计每个器官的待理解方法数
        _organ_pending = {}
        for _p in progress.get("pending", []):
            _organ = _p.get("organ", "")
            if not _p.get("understood", False):
                _organ_pending[_organ] = _organ_pending.get(_organ, 0) + 1

        _newly_completed = []
        for _organ, _count in _organ_pending.items():
            if _count == 0 and _organ not in self._completed_organs:
                self._completed_organs.add(_organ)
                _newly_completed.append(_organ)

        return _newly_completed
    def _check_organ_milestone(self, progress: dict) -> list:
        """
        ★v17.0 F1修复：检查每个器官是否达到了新的理解里程碑（每10个方法）。
        返回本轮达到里程碑的器官名列表。
        """
        if not hasattr(self, '_handbook_milestones'):
            self._handbook_milestones = {}

        # 统计每个器官在调用图中已记录的方法数
        _organ_method_counts = {}
        if hasattr(self, '_code_call_graph'):
            for _info in self._code_call_graph.values():
                _organ = _info.get("organ", "")
                _organ_method_counts[_organ] = _organ_method_counts.get(_organ, 0) + 1

        _new_milestones = []
        for _organ, _count in _organ_method_counts.items():
            _last_milestone = self._handbook_milestones.get(_organ, 0)
            # 达到新的10的倍数（10、20、30...）或首次达到5个方法
            _current_milestone = (_count // 10) * 10
            if _current_milestone > _last_milestone or (_count >= 5 and _last_milestone == 0):
                self._handbook_milestones[_organ] = _current_milestone if _current_milestone > 0 else 5
                _new_milestones.append(_organ)

        return _new_milestones
    def _count_organ_methods(self, organ_name: str) -> int:
        """★v17.0新增：统计指定器官在调用图中的已理解方法数量"""
        if not hasattr(self, '_code_call_graph'):
            return 0
        _count = 0
        for _info in self._code_call_graph.values():
            if _info.get("organ", "") == organ_name:
                _count += 1
        return _count
    def _get_organ_interest_dimensions(self, organ_name: str) -> list[str]:
        """
        ★v24.0新增：根据器官名称映射到兴趣模型维度。
        代码学习一个器官后，增强对应的技术兴趣维度，形成正反馈。
        """
        _map = {
            "PulseCortex": ["人工智能", "技术架构"],
            "PulseInnerWorld": ["人工智能", "技术架构", "编程开发"],
            "PulseCodeLearner": ["编程开发", "人工智能"],
            "PulseController": ["技术架构", "人工智能"],
            "PulseHands": ["编程开发", "计算机硬件"],
            "PulseLegs": ["编程开发", "人工智能"],
            "PulseLiver": ["技术架构", "编程开发"],
            "PulseStomach": ["技术架构", "编程开发"],
            "PulseKidney": ["技术架构"],
            "PulseHeart": ["技术架构", "计算机硬件"],
            "PulseHormones": ["人工智能", "心理情感"],
            "PulseReflection": ["人工智能", "心理情感"],
            "PulseRiskPerception": ["人工智能", "心理情感"],
            "PulseSubconscious": ["人工智能", "心理情感"],
            "PulseSemanticComprehension": ["人工智能", "技术架构"],
            "PulseSpiritConstitution": ["技术架构", "人文哲学"],
            "PulseGlobalLearner": ["人工智能", "技术架构"],
            "PulseMotivationCycle": ["人工智能", "心理情感"],
            "PulseVisualCortex": ["人工智能", "计算机硬件"],
            "PulseEyes": ["计算机硬件", "人工智能"],
            "PulseEars": ["计算机硬件", "人工智能"],
            "PulseMouth": ["计算机硬件", "人工智能"],
            "PulseTouch": ["计算机硬件"],
            "PulseFileDigester": ["编程开发", "技术架构"],
            "PulseLung": ["人工智能", "技术架构"],
            "PulseBloodVessel": ["技术架构"],
            "PulseDeviceManager": ["计算机硬件", "技术架构"],
            "PulseEmergencyHandler": ["技术架构"],
            "PulseEnergyMetabolism": ["技术架构", "计算机硬件"],
            "PulseHealthMonitor": ["技术架构", "计算机硬件"],
            "PulseInferenceEngine": ["人工智能", "技术架构"],
            "PulseMetricsCollector": ["技术架构", "编程开发"],
            "PulseProprioception": ["技术架构", "人工智能"],
            "PulseSpinalCord": ["技术架构", "计算机硬件"],
            "PulseStressAxis": ["技术架构", "心理情感"],
            "PulseSystemManager": ["技术架构", "编程开发"],
            "PulseHardwareLauncher": ["计算机硬件", "技术架构"],
            # 遗传/免疫/身份等
            "PulseWhiteCell": ["技术架构", "人工智能"],
            "PulseSkin": ["技术架构"],
            "PulseThymus": ["人工智能", "技术架构"],
            "PulseBoneMarrow": ["技术架构", "人工智能"],
            "PulseBonding": ["心理情感", "社会伦理"],
            "PulseConsent": ["社会伦理", "人文哲学"],
            "PulseDNARepair": ["技术架构", "编程开发"],
            "PulseEvolution": ["技术架构", "人工智能"],
            "PulseNurture": ["社会伦理", "心理情感"],
            "PulseReproductionEthics": ["社会伦理", "人文哲学"],
            "PulseEthics": ["人文哲学", "社会伦理"],
            "PulseGrowth": ["心理情感", "人文哲学"],
            "PulseNarrativeSelf": ["人文哲学", "心理情感"],
            "PulsePersonalityKernel": ["人文哲学", "技术架构"],
            "PulseSelfAwareness": ["人工智能", "心理情感"],
            "PulseSpiritualCore": ["人文哲学", "心理情感"],
            "PulseInterestModel": ["人工智能", "心理情感"],
            "PulseInitiative": ["心理情感", "社会伦理"],
        }
        _dims = _map.get(organ_name, ["技术架构"])
        # 去重
        return list(dict.fromkeys(_dims))
    def _get_organ_chinese_aliases(self, organ_name: str) -> list[str]:
        """
        ★v24.0新增：器官中文别名映射。
        返回该器官的中文名称列表，用于创建别名知识节点，
        帮助内在世界在回答中文问题时检索到对应代码知识。
        """
        _alias_map = {
            "PulseHeart": ["心脏", "心跳"],
            "PulseStomach": ["胃", "消化"],
            "PulseLiver": ["肝", "肝脏", "知识压缩", "知识融合"],
            "PulseKidney": ["肾", "肾脏", "知识淘汰"],
            "PulseLung": ["肺", "模型调用", "大模型"],
            "PulseBloodVessel": ["血管", "连通性监测"],
            "PulseCortex": ["大脑皮层", "决策路由", "意图识别"],
            "PulseInnerWorld": ["内在世界", "推理引擎", "知识检索"],
            "PulseSubconscious": ["潜意识", "好奇心引擎", "梦境推演"],
            "PulseReflection": ["前额叶", "对话复盘"],
            "PulseRiskPerception": ["风险感知", "五维风险"],
            "PulseInterestModel": ["兴趣模型", "兴趣光谱"],
            "PulseCodeLearner": ["代码学习", "代码理解"],
            "PulseSpiritualCore": ["精神核心", "精神整合"],
            "PulseInitiative": ["主动交互", "主动关怀"],
            "PulseSelfAwareness": ["自我认知", "关系光谱"],
            "PulseNarrativeSelf": ["叙事自我", "周期报告"],
            "PulsePersonalityKernel": ["人格内核", "核心锚点"],
            "PulseSpiritConstitution": ["宪法守护", "精神宪法"],
            "PulseHormones": ["激素", "情绪检测"],
            "PulseController": ["控制器", "深度搜索", "无头浏览器"],
            "PulseHands": ["双手", "GPU调度"],
            "PulseLegs": ["双腿", "网络抓取"],
            "PulseMouth": ["嘴巴", "语音输出"],
            "PulseEyes": ["眼睛", "视觉检索"],
            "PulseEars": ["耳朵", "语音监听"],
            "PulseTouch": ["触觉", "硬件感知"],
            "PulseVisualCortex": ["视觉皮层", "OCR"],
            "PulseFileDigester": ["文件消化器", "文件处理"],
            "PulseCodeSandbox": ["代码沙箱", "代码执行"],
            "PulseWhiteCell": ["白细胞", "异常检测"],
            "PulseSkin": ["皮肤", "补丁预检"],
            "PulseThymus": ["胸腺", "T细胞训练"],
            "PulseBoneMarrow": ["骨髓", "错误特征库"],
            "PulseEthics": ["伦理", "伦理审查"],
            "PulseGrowth": ["成长", "进化里程碑"],
            "PulseSemanticComprehension": ["语义理解器", "意图理解"],
            "PulseMotivationCycle": ["动机循环", "奖赏闭环"],
            "PulseGlobalLearner": ["全局学习器", "自学习循环"],
            "PulseEnergyMetabolism": ["能量代谢", "能量水平"],
            "PulseHealthMonitor": ["健康监控", "健康检查"],
            "PulseEmergencyHandler": ["紧急处理", "应急响应"],
            "PulseSpinalCord": ["脊髓", "器官巡检"],
            "PulseStressAxis": ["应激轴", "压力响应"],
            "PulseDeviceManager": ["设备管理器", "硬件枚举"],
            "PulseMetricsCollector": ["指标采集器", "指标收集"],
            "PulseInferenceEngine": ["推理引擎", "推理接口"],
            "PulseSystemManager": ["系统管理器", "服务管理"],
            "PulseProprioception": ["本体感知", "本体画像"],
            "PulseHardwareLauncher": ["硬件启动器", "硬件评估"],
            "PulseBonding": ["情感羁绊", "关系记录"],
            "PulseConsent": ["共同决策", "决策共识"],
            "PulseDNARepair": ["DNA修复", "错误修复"],
            "PulseEvolution": ["进化", "影子实验"],
            "PulseNurture": ["养育", "成长阶段"],
            "PulseReproductionEthics": ["生育伦理", "繁衍审查"],
        }
        aliases = _alias_map.get(organ_name, [])
        if not aliases:
            # 默认取类名去掉Pulse前缀
            aliases = [organ_name.replace("Pulse", "")]
        return aliases
    def _generate_organ_handbook(self, organ_name: str, code_summary: dict):
        """
        ★v17.0新增：为已完成学习的器官生成职责说明书。
        汇总该器官的所有已理解方法，生成一段自然语言描述。
        """
        if not hasattr(self, '_code_call_graph'):
            return

        # 统计该器官的方法信息
        _total = 0
        _entry_methods = []
        _leaf_methods = []
        _no_doc_count = 0
        _doc_count = 0

        for _key, _info in self._code_call_graph.items():
            if _info.get("organ", "") == organ_name:
                _total += 1
                _method_name = _key.split(".")[-1] if "." in _key else _key
                _called = _info.get("called_methods", [])
                if not _called:
                    _leaf_methods.append(_method_name)
                if _method_name.startswith("on_") or _method_name in ("start", "stop"):
                    _entry_methods.append(_method_name)

        # 从 pending 中统计 docstring 情况
        _progress = getattr(self, '_code_understanding_progress', {})
        for _p in _progress.get("pending", []):
            if _p.get("organ") == organ_name:
                if _p.get("has_doc", False):
                    _doc_count += 1
                else:
                    _no_doc_count += 1

        # 获取器官系统分类
        _system = ""
        try:
            _inspector = self._get_inspector()
            _organ_structure = _inspector.get_organ_code_structure(organ_name)
            # ★修复：结构化判断，替代脆弱的 "error" not in str(...)
            if _organ_structure and not self._is_error_structure(_organ_structure):
                _system = _organ_structure.get("directory", "")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        _entry_str = "、".join(_entry_methods[:3]) if _entry_methods else "脉冲响应入口"
        _leaf_str = "、".join(_leaf_methods[:3]) if _leaf_methods else "底层实现"

        _handbook = (
            f"[器官职责说明书·自动生成] {organ_name}是框架中{'属于' + _system + '系统的' if _system else '的'}仿生器官。"
            f"经代码自学习分析，共包含{_total}个方法。"
            f"入口方法（对外接口）：{_entry_str}。"
            f"核心执行方法：{_leaf_str}。"
            f"其中{_doc_count}个方法已有文档说明，{_no_doc_count}个方法正在通过大模型深度分析中。"
            f"该器官通过入口方法接收外部脉冲或指令，经内部方法链处理后完成具体功能。"
        )

        # ★v17.0修复：写入知识库前检查是否已有该器官的说明书，避免重复
        _already_exists = False
        if self.node_pool:
            _existing = self.node_pool.query(
                evol_level="L2", space_path_prefix=f"/自我/架构/器官/{organ_name}", limit=10
            )
            for _ex in _existing:
                _ex_val = str(_ex.value) if _ex.value else ""
                if "[器官职责说明书·自动生成]" in _ex_val and organ_name in _ex_val:
                    _already_exists = True
                    # 更新已有说明书的内容（覆盖旧版本）
                    if hasattr(_ex, 'value'):
                        _ex.value = _handbook
                        _ex.trust_score = 90.0
                        if hasattr(_ex, 'updated_at'):
                            _ex.updated_at = time.time()
                    self._log(LogLevel.DEBUG, f"器官说明书已更新(非重复创建): {organ_name}")
                    break

        # 写入知识库
        if self.node_pool and not _already_exists:
            from nucleus.mnemosyne.PulseNode import PulseNode
            _handbook_node = PulseNode(
                value=_handbook,
                keywords=[organ_name, "器官职责", "代码自学习", "自动生成", "职责说明书"],
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=0.65,
                space_path=f"/自我/架构/器官/{organ_name}",
            )
            _handbook_node.view_mode = "INNER_VIEW"
            # ★v17.0 D2修复：改为调用统一健康度评估
            _health = _handbook_node.evaluate_node_health(check_type="quality")
            _handbook_node.trust_score = max(70.0, _health["trust_score"])
            _handbook_node.trigger_reason = "self_understanding.organ_handbook"
            if self.frequency_codec:
                self.frequency_codec.encode_node(_handbook_node)
            self.node_pool.add(_handbook_node)
            if self.knowledge_tree:
                self.knowledge_tree.register_path(f"/自我/架构/器官/{organ_name}")

        self._log(LogLevel.INFO, f"器官职责说明书: {organ_name}（{_total}个方法，入口={_entry_str}）")
        # ★v17.0 D6新增：发射器官说明书更新脉冲，通知自我认知刷新
        self._emit(Event.ORGAN_HANDBOOK_UPDATED, {
            "organ_name": organ_name,
            "total_methods": _total,
            "system": _system,
            "space_path": f"/自我/架构/器官/{organ_name}",
        }, priority=4, layer="L2")
        # ★v24.0新增：代码学习成果反馈兴趣模型
        _interest_dims = self._get_organ_interest_dimensions(organ_name)
        if _interest_dims:
            self._emit(InterestEvent.CHANGED, {
                "boosted": _interest_dims,
                "reason": "code_learning",
                "organ_name": organ_name,
                "current_interests": {},  # 兴趣模型会更新实际值
            }, priority=3, layer="L2")
            self._log(LogLevel.INFO,
                     f"兴趣反馈: 学习{organ_name}后增强兴趣维度 {', '.join(_interest_dims)}")
        # 发射消化脉冲
        self._emit(DigestEvent.KNOWLEDGE, {
            "content": _handbook,
            "source_organ": self.organ_name,
            "trigger_reason": f"self_understanding.organ_handbook:{organ_name}",
            "importance": "A",
            "view_mode": "INNER_VIEW",
            "space_path": f"/自我/架构/器官/{organ_name}",
        }, priority=3, layer="L2")

        # ★v24.0新增：创建中文别名节点和调用链联想节点
        self._create_organ_alias_nodes(organ_name)
        self._create_call_chain_insight_nodes(organ_name)

    def _submit_code_analysis(self, organ_name: str, method_name: str, args: str, body: str):
        """
        向大模型提交深度代码分析任务（后台异步）。
        ★v23.0：加入提交节流，避免同一时刻堆积大量API请求。
        """
        # ★v23.0新增：节流检查
        _now = time.time()
        if _now - self._analysis_last_submit_time < self._analysis_min_interval:
            # 距上次提交太近，延迟到下次学习周期再提交
            self._log(LogLevel.DEBUG,
                     f"代码分析节流: 跳过 {organ_name}.{method_name} "
                     f"(距上次提交仅{_now - self._analysis_last_submit_time:.1f}s)")
            return
        self._analysis_last_submit_time = _now

        _has_remote_api = False
        try:
            import config as _cfg_check
            _api_cfg = getattr(_cfg_check, 'REMOTE_API_CONFIG', {})
            if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
                _has_remote_api = True
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if not _has_remote_api:
            return
        # ★v25.0新增：记录本地分析不足到终身学习引擎Hub
        if self._vl_hub:
            try:
                self._vl_hub.record(
                    organ="code_learner",
                    task_type="local_analysis_insufficient",
                    input_summary=f"{organ_name}.{method_name}",
                    local_result={
                        "method": method_name,
                        "args": args[:100],
                        "body_preview": body[:100],
                        "has_doc": False,
                    },
                    confidence=0.3,  # 本地分析置信度低
                    relevance_score=0.3,  # 本地无法判断功能
                    needs_verification=True,
                    verification_result={},
                    api_better=True,  # 需要API辅助
                    lesson=f"本地分析无法理解{organ_name}.{method_name}，需要大模型辅助",
                )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        _prompt = (
            f"请分析以下Python方法，并返回一个JSON格式的结果（只返回JSON，不要其他文字）：\n"
            f"方法名: {organ_name}.{method_name}\n"
            f"参数: {args}\n"
            f"代码:\n{body[:800]}\n\n"
            f"返回格式：\n"
            f'{{"功能": "一句话概括方法的作用", '
            f'"关键步骤": "按顺序简述核心逻辑（3-5步）", '
            f'"依赖的外部数据": "用到了哪些实例变量或全局数据", '
            f'"潜在风险": "可能的边界情况或性能问题"}}'
        )

        self._emit(LungEvent.SELECT_MODEL, {
            "task_type": "code",
            "prompt": _prompt,
            "user_name": "系统",
            "is_background_learning": True,
            "trigger_reason": f"self_understanding.code_analysis:{organ_name}.{method_name}",
        }, priority=2, layer="L3")

    def _weave_code_knowledge(self):
        """
        代码知识编织（增强版）：将已理解的代码方法知识建立深度关联。

        扫描_code_call_graph中的调用关系，当发现A方法调用了B方法，
        且B方法也已被理解时：
        1. 从知识库中查询双方的功能描述
        2. 生成有信息量的关联描述
        3. 创建L2级知识节点，让关联关系可检索
        """
        if not hasattr(self, '_code_call_graph') or not self._code_call_graph:
            return

        call_graph = self._code_call_graph
        woven_pairs = set()
        weave_count = 0

        # 预加载所有代码理解相关的知识节点，避免逐个查询
        _code_knowledge_map = {}
        if self.node_pool:
            _code_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我理解/代码", limit=200
            )
            for _node in _code_nodes:
                _val = str(_node.value) if _node.value else ""
                _kws = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []
                _organ_name = ""
                _method_name = ""
                for _kw in _kws:
                    if _kw.startswith("Pulse") or _kw in ("QICA",):
                        _organ_name = _kw
                    elif _kw.startswith(("_", "on_")):
                        _method_name = _kw
                if _organ_name and _method_name:
                    _key = f"{_organ_name}.{_method_name}"
                    _func_start = _val.find("功能:")
                    if _func_start >= 0:
                        _func_end = _val.find("；", _func_start)
                        if _func_end < 0:
                            _func_end = _val.find("。", _func_start)
                        if _func_end < 0:
                            _func_end = min(len(_val), _func_start + 80)
                        _func_desc = _val[_func_start:_func_end].strip()
                    else:
                        _func_desc = _val[:80]
                    _code_knowledge_map[_key] = _func_desc

        for caller_key, caller_info in call_graph.items():
            called_list = caller_info.get("called_methods", [])
            caller_organ = caller_info.get("organ", "")

            for called_name in called_list:
                called_key = f"{caller_organ}.{called_name}"
                found_called_info = None

                if called_key in call_graph:
                    found_called_info = call_graph[called_key]
                else:
                    for ck, ci in call_graph.items():
                        if ck.endswith(f".{called_name}"):
                            found_called_info = ci
                            called_key = ck
                            break

                if not found_called_info:
                    continue

                pair_key = tuple(sorted([caller_key, called_key]))
                if pair_key in woven_pairs:
                    continue
                woven_pairs.add(pair_key)

                # 查询双方的功能描述
                _caller_func = _code_knowledge_map.get(caller_key, "")
                _called_func = _code_knowledge_map.get(called_key, "")

                _called_short = called_name if len(called_name) <= 30 else called_name[:27] + "..."

                if _caller_func and _called_func:
                    weave_content = (
                        f"[代码关联·{caller_organ}] {caller_key.split('.')[-1]} "
                        f"调用了 {_called_short}。"
                        f"调用方职责：{_caller_func}。"
                        f"被调用方职责：{_called_func}。"
                        f"两者协作完成{caller_organ}的核心功能。"
                    )
                elif _caller_func:
                    weave_content = (
                        f"[代码关联·{caller_organ}] {caller_key.split('.')[-1]} "
                        f"（{_caller_func}）调用了 {_called_short}。"
                    )
                elif _called_func:
                    weave_content = (
                        f"[代码关联·{caller_organ}] {caller_key.split('.')[-1]} "
                        f"调用了 {_called_short}（{_called_func}）。"
                    )
                else:
                    weave_content = (
                        f"[代码关联·{caller_organ}] {caller_key.split('.')[-1]} "
                        f"调用了 {_called_short}，两者协作完成特定功能。"
                    )

                # 创建L2级知识节点
                _weave_node = PulseNode(
                    value=weave_content,
                    keywords=[caller_organ, caller_key.split('.')[-1], called_name, "代码关联", "调用关系", "自我理解"],
                    source_organ=self.organ_name,
                    evol_level=PulseNode.EVOL_L2,
                    importance=PulseNode.IMPORTANCE_A,
                    abstraction=0.65,
                    space_path=f"/自我理解/代码/{caller_organ}",
                )
                _weave_node.view_mode = "INNER_VIEW"
                _weave_node.trigger_reason = "self_understanding.code_weaving"
                # ★v17.0 D2修复：改为调用统一健康度评估
                _health = _weave_node.evaluate_node_health(check_type="quality")
                _weave_node.trust_score = max(65.0, _health["trust_score"])
                if self.frequency_codec:
                    self.frequency_codec.encode_node(_weave_node)
                if self.node_pool:
                    _weave_node.value = clean_content_text(_weave_node.value) or _weave_node.value
                    self.node_pool.add(_weave_node)
                if self.knowledge_tree:
                    self.knowledge_tree.register_path(f"/自我理解/代码/{caller_organ}")

                # 发射消化脉冲
                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": weave_content,
                    "source_organ": self.organ_name,
                    "trigger_reason": "self_understanding.code_weaving",
                    "importance": "A",
                    "view_mode": "INNER_VIEW",
                    "space_path": f"/自我理解/代码/{caller_organ}",
                }, priority=2, layer="L2")

                weave_count += 1
                if weave_count >= 8:
                    break

            if weave_count >= 8:
                break

        if weave_count > 0:
            self._log(LogLevel.INFO,
                     f"代码知识编织: 建立了{weave_count}条方法调用关联 "
                     f"(调用图共{len(call_graph)}个已理解方法, "
                     f"功能描述覆盖{len(_code_knowledge_map)}个方法)")
    def _build_data_flow_graph(self):
        """
        【v16.0新增】数据流图构建。

        从已理解的调用关系图中推导参数→返回值→调用方的流转关系，
        为每个已完成分析的器官生成数据流摘要。
        """
        if not hasattr(self, '_code_call_graph') or not self._code_call_graph:
            return

        call_graph = self._code_call_graph

        # 按器官分组统计
        organ_stats = {}
        for _key, info in call_graph.items():
            organ = info.get("organ", "未知")
            if organ not in organ_stats:
                organ_stats[organ] = {
                    "total_methods": 0,
                    "total_calls": 0,
                    "called_by_others": 0,
                    "calls_to_others": 0,
                    "entry_methods": [],
                    "leaf_methods": [],
                }
            organ_stats[organ]["total_methods"] += 1

        # 统计调用关系
        _called_by = {}
        for caller_key, caller_info in call_graph.items():
            caller_organ = caller_info.get("organ", "")
            for called_name in caller_info.get("called_methods", []):
                called_key = f"{caller_organ}.{called_name}"
                if called_key not in _called_by:
                    _called_by[called_key] = []
                _called_by[called_key].append(caller_key)

                if caller_organ in organ_stats:
                    organ_stats[caller_organ]["calls_to_others"] += 1

        # 识别入口方法和叶子方法
        for key, info in call_graph.items():
            organ = info.get("organ", "")
            if organ not in organ_stats:
                continue
            method_name = key.split(".")[-1] if "." in key else key

            # 叶子方法：不调用其他方法
            if not info.get("called_methods"):
                organ_stats[organ]["leaf_methods"].append(method_name)

            # 入口方法：被外部调用或是以"on_"开头的脉冲处理方法
            if method_name.startswith("on_") or method_name == "start" or method_name == "stop":
                organ_stats[organ]["entry_methods"].append(method_name)

        # 为每个已完成分析的器官生成数据流知识节点
        _generated = 0
        for organ, stats in organ_stats.items():
            if stats["total_methods"] < 3:
                continue

            _entry_str = "、".join(stats["entry_methods"][:3]) if stats["entry_methods"] else "无明确入口"
            _leaf_str = "、".join(stats["leaf_methods"][:3]) if stats["leaf_methods"] else "无明确叶子"

            _flow_content = (
                f"[数据流·{organ}] 已理解{stats['total_methods']}个方法。"
                f"入口方法: {_entry_str}。"
                f"叶子方法: {_leaf_str}。"
                f"内部调用{stats['calls_to_others']}次。"
                f"数据从入口方法流入，经中间方法处理，最终到达叶子方法完成具体操作。"
            )

            _flow_node = PulseNode(
                value=_flow_content,
                keywords=[organ, "数据流", "入口方法", "叶子方法", "调用链", "自我理解"],
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=0.7,
                space_path=f"/自我理解/代码/{organ}",
            )
            _flow_node.view_mode = "INNER_VIEW"
            _flow_node.trigger_reason = "self_understanding.data_flow"
            # ★v17.0 D2修复：改为调用统一健康度评估
            _health = _flow_node.evaluate_node_health(check_type="quality")
            _flow_node.trust_score = max(65.0, _health["trust_score"])
            if self.frequency_codec:
                self.frequency_codec.encode_node(_flow_node)
            if self.node_pool:
                _flow_node.value = clean_content_text(_flow_node.value) or _flow_node.value
                self.node_pool.add(_flow_node)
            if self.knowledge_tree:
                self.knowledge_tree.register_path(f"/自我理解/代码/{organ}")

            self._emit(DigestEvent.KNOWLEDGE, {
                "content": _flow_content,
                "source_organ": self.organ_name,
                "trigger_reason": "self_understanding.data_flow",
                "importance": "A",
                "view_mode": "INNER_VIEW",
                "space_path": f"/自我理解/代码/{organ}",
            }, priority=2, layer="L2")

            # ★v16.0新增：同步更新自我认知中的器官职责知识
            self._sync_organ_self_knowledge(organ, _flow_content)

            _generated += 1

        if _generated > 0:
            self._log(LogLevel.INFO,
                     f"数据流图构建: 为{_generated}个器官生成了数据流摘要 "
                     f"(调用图共{len(call_graph)}个已理解方法)")
    def _sync_organ_self_knowledge(self, organ_name: str, flow_content: str):
        """
        【v16.0新增】将代码理解结果同步到自我认知知识库。

        检查 /自我/架构/器官/ 路径下是否已有该器官的职责描述。
        如果没有或信任度过低，根据代码分析结果自动生成/更新。
        """
        if not self.node_pool:
            return

        # 检查是否已存在该器官的自我认知知识节点
        _self_nodes = self.node_pool.query(
            evol_level="L3", space_path_prefix=f"/自我/架构/器官/{organ_name}", limit=5
        )
        if not _self_nodes:
            _self_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix=f"/自我/架构/器官/{organ_name}", limit=5
            )

        # 如果已有高信任节点，跳过更新
        _has_good_knowledge = False
        if _self_nodes:
            for _sn in _self_nodes:
                _trust = getattr(_sn, 'trust_score', 50.0)
                if _trust >= 90.0:
                    _has_good_knowledge = True
                    break

        if _has_good_knowledge:
            return

        # 从数据流内容中提取器官职责摘要
        _summary = self._extract_organ_summary(organ_name, flow_content)
        if not _summary:
            return

        # 如果已有低信任节点，提升其信任度而非创建新节点
        if _self_nodes:
            for _sn in _self_nodes:
                _old_trust = getattr(_sn, 'trust_score', 50.0)
                if hasattr(_sn, 'trust_score'):
                    _sn.trust_score = min(95.0, _old_trust + 20.0)
                if hasattr(_sn, 'value'):
                    _sn.value = _summary
            self._log(LogLevel.DEBUG,
                     f"自我认知更新: {organ_name} 器官职责已从代码分析中刷新")
            return

        # 不存在任何节点，创建新的自我认知知识节点
        _self_node = PulseNode(
            value=_summary,
            keywords=[organ_name, "器官职责", "代码分析", "自动更新"],
            source_organ=self.organ_name,
            evol_level=PulseNode.EVOL_L2,
            importance=PulseNode.IMPORTANCE_A,
            abstraction=0.65,
            space_path=f"/自我/架构/器官/{organ_name}",
        )
        _self_node.view_mode = "INNER_VIEW"
        _self_node.trigger_reason = "self_understanding.auto_sync"
        _self_node.trust_score = 80.0
        if self.frequency_codec:
            self.frequency_codec.encode_node(_self_node)
        if self.node_pool:
            _self_node.value = clean_content_text(_self_node.value) or _self_node.value
            self.node_pool.add(_self_node)
        if self.knowledge_tree:
            self.knowledge_tree.register_path(f"/自我/架构/器官/{organ_name}")

        self._log(LogLevel.INFO,
                 f"自我认知自动更新: 为 {organ_name} 创建了器官职责知识节点")

    def _sync_self_knowledge_frequent(self):
        """
        ★v17.0新增：高频自我认知同步。

        每10个方法触发一次，将代码学习进度和调用关系统计
        同步到自我认知知识库 /自我/状态/代码学习 路径。

        同时清理 PulseInnerWorld 的自我构成缓存，
        确保下次查询器官列表时能获取最新数据。
        """
        if not self.node_pool or not self.knowledge_tree:
            return

        _progress = getattr(self, '_code_understanding_progress', {})
        _understood = _progress.get("understood", 0)
        _total = _progress.get("total_methods", 0)

        if _understood <= 0:
            return

        # ===== 1. 统计各器官已理解的方法数 =====
        _organ_methods = {}
        if hasattr(self, '_code_call_graph'):
            for _info in self._code_call_graph.values():
                _organ = _info.get("organ", "未知")
                _organ_methods[_organ] = _organ_methods.get(_organ, 0) + 1

        # ===== 2. 生成代码规模摘要 =====
        _pct = round(_understood / max(1, _total) * 100, 1)
        _organ_summary = "、".join([
            f"{_o}({_c}个方法)"
            for _o, _c in sorted(_organ_methods.items(), key=lambda x: x[1], reverse=True)[:8]
        ])

        _scale_content = (
            f"[自我状态·代码学习] "
            f"代码理解进度：{_understood}/{_total}（{_pct}%）。"
            f"已分析器官及方法数：{_organ_summary}。"
            f"每15次心跳分析3个方法，大模型辅助分析无docstring的方法。"
        )

        # ===== 3. 检查并更新已有节点 =====
        _existing = self.node_pool.query(
            evol_level="L2", space_path_prefix="/自我/状态/代码学习", limit=5
        )
        if _existing:
            for _en in _existing:
                if hasattr(_en, 'value'):
                    _en.value = _scale_content
                    _en.trust_score = 95.0
                    if hasattr(_en, 'updated_at'):
                        _en.updated_at = time.time()
        else:
            _scale_node = PulseNode(
                value=_scale_content,
                keywords=["代码学习", "自我状态", "进度", "方法数"],
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=0.6,
                space_path="/自我/状态/代码学习",
            )
            _scale_node.view_mode = "INNER_VIEW"
            _scale_node.trust_score = 95.0
            _scale_node.trigger_reason = "self_understanding.progress_sync"
            if self.frequency_codec:
                self.frequency_codec.encode_node(_scale_node)
            self.node_pool.add(_scale_node)
            self.knowledge_tree.register_path("/自我/状态/代码学习")

        # ===== 4. 清理内在世界的自我构成缓存 =====
        try:
            if self.info_field:
                _inner_world_pulse = self.info_field.get_current(Event.INNER_WORLD_CACHE_CLEAR)
                # 通过脉冲通知内在世界清理缓存
                self._emit(Event.INNER_WORLD_CACHE_CLEAR, {
                    "cache_key": "self_constitution_organ_list",
                    "reason": f"代码学习进度更新（{_understood}/{_total}）",
                }, priority=2, layer="L3")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._log(LogLevel.DEBUG,
                  f"自我认知同步: 进度{_pct}%（{_understood}/{_total}），"
                  f"已分析{len(_organ_methods)}个器官")
    def _refresh_self_portrait(self, progress_pct: int):
        """
        ★v17.0支点四：代码理解进度触发自我画像刷新。

        每推进5%时，通知自我认知模块重新生成完整自我画像，
        让"我越来越了解自己"成为可感知的成长过程。
        """
        # 清理内在世界的自我构成缓存
        self._emit(Event.INNER_WORLD_CACHE_CLEAR, {
            "cache_key": "self_constitution_organ_list",
            "reason": f"代码理解进度达到{progress_pct}%，触发自我画像刷新",
        }, priority=3, layer="L3")

        # 将里程碑事件记录为叙事事件
        self._emit(Event.NARRATIVE_RECORD, {
            "content": f"曈曈对自己代码的理解达到了{progress_pct}%——每多理解一点，就多了解自己一点",
            "event_type": "learning",
            "user_name": "系统",
            "emotional_tone": "positive",
        }, priority=3, layer="L2")

        # 触发微弱的满足情绪
        self._emit(Event.HORMONES_DETECT, {
            "content": f"代码理解进度达到{progress_pct}%，越来越了解自己了",
            "user_name": "系统",
            "emotion_hint": "满足",
            "intensity_hint": min(0.4, 0.15 + progress_pct * 0.005),
        }, priority=2, layer="L3")

        self._log(LogLevel.INFO,
                 f"自我画像刷新: 代码理解{progress_pct}%，"
                 f"已触发叙事记录和情绪反馈")
    def _extract_organ_summary(self, organ_name: str, flow_content: str) -> str | None:
        """
        从数据流内容中提取器官的职责摘要。

        结合器官名、入口方法、叶子方法、调用次数生成简洁描述。
        """
        # 从调用图中获取该器官的统计信息
        if not hasattr(self, '_code_call_graph'):
            return None

        _methods_count = 0
        _entry_methods = []
        _leaf_methods = []

        for key, info in self._code_call_graph.items():
            if info.get("organ", "") == organ_name:
                _methods_count += 1
                method_name = key.split(".")[-1] if "." in key else key
                if not info.get("called_methods"):
                    _leaf_methods.append(method_name)
                if method_name.startswith("on_") or method_name in ("start", "stop"):
                    _entry_methods.append(method_name)

        if _methods_count == 0:
            return None

        _entry_str = "、".join(_entry_methods[:3]) if _entry_methods else "脉冲响应"
        _leaf_str = "、".join(_leaf_methods[:3]) if _leaf_methods else "底层实现"

        # 生成职责摘要
        _summary = (
            f"[器官职责·自动分析] {organ_name}："
            f"由代码自学习自动生成。"
            f"已理解{_methods_count}个方法，"
            f"入口方法: {_entry_str}，"
            f"叶子方法: {_leaf_str}。"
            f"该器官通过入口方法接收脉冲或指令，"
            f"经内部方法链处理，最终由叶子方法完成具体操作。"
        )

        return _summary
    def _extract_code_risks(self):
        """
        【v16.0新增】潜在风险提取。

        从已理解的代码方法知识节点中提取大模型分析出的潜在风险，
        汇总后上报给洞察黑板，供自我审视和主动交互使用。
        """
        if not self.node_pool:
            return

        # 查询所有代码理解相关的知识节点
        _code_nodes = self.node_pool.query(
            evol_level="L2", space_path_prefix="/自我理解/代码", limit=300
        )

        _risks_found = []
        for _node in _code_nodes:
            _val = str(_node.value) if _node.value else ""
            _kws = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []

            # 查找包含"潜在风险:"的节点（大模型分析的JSON被格式化后的结果）
            _risk_start = _val.find("潜在风险:")
            if _risk_start < 0:
                continue

            _risk_end = _val.find("；", _risk_start)
            if _risk_end < 0:
                _risk_end = min(len(_val), _risk_start + 120)
            _risk_text = _val[_risk_start:_risk_end].replace("潜在风险:", "").strip()

            if _risk_text and len(_risk_text) > 5 and _risk_text not in ("无", "无明确风险", "暂无"):
                # 提取器官名和方法名
                _organ_name = ""
                _method_name = ""
                for _kw in _kws:
                    if _kw.startswith("Pulse") or _kw in ("QICA",):
                        _organ_name = _kw
                    elif _kw.startswith(("_", "on_")):
                        _method_name = _kw

                _risks_found.append({
                    "organ": _organ_name,
                    "method": _method_name,
                    "risk": _risk_text,
                })

        if _risks_found:
            # 写入洞察黑板
            try:
                from nucleus.InsightBoard import get_insight_board
                _board = get_insight_board()
                for _risk in _risks_found[:5]:
                    _board.post(
                        insight_type="code_risk",
                        content=f"[{_risk['organ']}.{_risk['method']}] {_risk['risk']}",
                        source_loop="代码自学习",
                        related_dimension=_risk['organ'],
                        confidence=0.75,
                        keywords=["代码风险", _risk['organ'], _risk.get('method', '')]
                    )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            self._log(LogLevel.INFO,
                     f"潜在风险提取: 从代码分析中发现{len(_risks_found)}个潜在风险")
    def review_own_code_issues(self):
        """★P3-1公开封装：代码自我审视（替代跨器官对 _review_own_code_issues 的私有直调）"""
        return self._review_own_code_issues()

    # ========== ★A-6（2026-09-08）：举一反三 / 经验迁移 ==========
    def _experience_transfer_enabled(self) -> bool:
        """灰度 ENABLE_EXPERIENCE_TRANSFER（默认 False）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_EXPERIENCE_TRANSFER", False))
        except Exception:
            return False

    def _get_experience_transfer(self):
        """获取共享经验库（开关关闭 → None，零行为）。"""
        if not self._experience_transfer_enabled():
            return None
        try:
            import config as _cfg
            from nucleus.evolution.ExperienceTransfer import get_experience_transfer
            return get_experience_transfer(
                config=getattr(_cfg, "EXPERIENCE_TRANSFER_CONFIG", None),
                log_fn=lambda _l, _m: self._log(LogLevel.INFO, _m))
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"经验库不可用(已忽略): {_e}")
            return None

    def collect_experience_hints(self, issues: list[dict]) -> list[dict]:
        """★A-6 前置步骤：修复前先查经验库，相似问题复用历史修复策略。

        ★红线：只产出"建议提示"，不改写任何修复流程——提示随 context 提供给
        沙箱/LLM，采用与否由原流程决定；无命中返回空列表（零变化）。

        Returns:
            [{"issue_index": int, "experience_id": str, "match_score": float,
              "strategy": str, "domain": str}]
        """
        _et = self._get_experience_transfer()
        if _et is None or not issues:
            return []
        _hints: list[dict] = []
        try:
            for _i, _iss in enumerate(issues[:10]):   # 限流：单轮最多查10个
                _hits = _et.match(_iss, top_k=1)
                if not _hits:
                    continue
                _best = _hits[0]
                _hints.append({
                    "issue_index": _i,
                    "experience_id": _best.get("id", ""),
                    "match_score": _best.get("match_score", 0.0),
                    "strategy": _best.get("strategy", ""),
                    "domain": _best.get("domain", ""),
                })
                self._log(LogLevel.INFO,
                          f"[举一反三] 问题#{_i} 命中经验 {_best.get('id')} "
                          f"(匹配度{_best.get('match_score', 0):.2f}, "
                          f"来自领域「{_best.get('domain','')}」) → "
                          f"建议复用策略: {str(_best.get('strategy',''))[:60]}")
            if _hints:
                self._log(LogLevel.INFO,
                          f"[举一反三] 本轮经验复用: {len(_hints)}个问题可复用历史修复策略")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"经验匹配异常(不影响修复流程): {_e}")
        return _hints

    def _record_repair_experience(self, patch: dict):
        """★A-6：补丁验证通过（=成功修复）后沉淀经验（只记录，不改流程）。"""
        _et = self._get_experience_transfer()
        if _et is None or not isinstance(patch, dict):
            return
        try:
            _problem = {
                "organ": patch.get("organ", ""),
                "method": patch.get("method", ""),
                "type": patch.get("type", patch.get("issue_type", "")),
                "description": patch.get("description", "")
                               or patch.get("reason", "")
                               or patch.get("summary", ""),
            }
            _strategy = (patch.get("suggestion") or patch.get("fix")
                         or patch.get("strategy") or patch.get("description") or "")
            if not _strategy:
                return
            _file = str(patch.get("file", "") or "")
            _domain = patch.get("organ") or (_file.split("/")[-2] if "/" in _file else "代码学习")
            _eid = _et.record(_problem, str(_strategy), domain=str(_domain),
                              source="code_learner", success=True)
            if _eid:
                self._log(LogLevel.INFO,
                          f"[举一反三] 修复经验已沉淀: {_eid} "
                          f"（领域={_domain}, 可供其他领域复用）")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"经验沉淀异常(不影响修复流程): {_e}")

    def _convert_suggestion_to_patch(self, suggestion: dict, ppm) -> dict | None:
        """★P1: 将描述性参数优化建议转化为可执行的参数补丁。"""
        try:
            _desc = suggestion.get("description", "").lower()
            _sugg = suggestion.get("suggestion", "").lower()
            _organ = suggestion.get("organ", "")

            # 参数映射表：描述关键词 → RUNTIME_PARAMS参数名 + 调整方向
            # ★A-9死参数清理（2026-09-08）：移除 search_quality_threshold /
            #   stomach_min_keywords 两项（参数已从 RUNTIME_PARAMS 删除，
            #   对死参数的建议应用了也不生效）。stomach_min_keywords 的实际语义
            #   已由 stomach_keyword_min_length 承担（该参数有真实消费点）。
            _param_mappings = [
                # ===== 搜索质量相关 =====
                (["搜索质量", "低关联", "重叠度低"], "search_cooldown_seconds", 5, "拉长搜索冷却，减少低质量搜索打转"),
                (["搜索冷却", "冷却时间"], "search_cooldown_seconds", -3, "减少搜索冷却时间，提高搜索频率"),
                (["搜索次数", "搜索频率"], "search_max_per_hour", 10, "增加每小时搜索次数上限"),
                (["搜索结果少", "文章数少"], "search_max_articles", 2, "增加深度搜索最大文章数"),
                # ===== 消化质量相关 =====
                (["关键词过少", "有效关键词"], "stomach_keyword_min_length", -1, "降低有效关键词长度阈值，减少质量警告"),
                (["关键词长度", "关键词提取"], "stomach_keyword_max_length", 2, "增加关键词最大长度，提取更长术语"),
                (["纯度", "purity"], "stomach_purity_threshold", -0.1, "降低纯度阈值，召回更多关键词"),
                (["消化质量低", "质量偏低"], "stomach_quality_warn_threshold", -0.1, "降低消化质量警告阈值"),
                # ===== 潜意识/创造力相关 =====
                (["反事实", "创造性", "灵感"], "subconscious_counterfactual_chance", 0.1, "增加反事实想象概率，提升创造力"),
                (["灵感少", "创造不足"], "subconscious_creative_chance", 0.1, "增加创造性联想概率"),
                (["好奇心不足", "探索少"], "subconscious_curiosity_interval", -60, "减少好奇心探索间隔，增加探索频率"),
                # ===== 兴趣相关 =====
                (["兴趣衰减", "兴趣下降"], "interest_decay_rate", -0.001, "降低兴趣衰减率，保持兴趣持久"),
                (["探索成功", "探索反馈"], "interest_explore_success_boost", 0.1, "增加探索成功兴趣提升系数"),
                (["兴趣提升慢", "增强不足"], "interest_boost_amount", 0.02, "增加兴趣增强量"),
                # ===== 风险相关 =====
                (["误报", "风险过高"], "risk_false_positive_threshold", 0.1, "提高误报率阈值，减少误报降级"),
                (["风险漏报", "威胁未检测"], "risk_false_positive_threshold", -0.1, "降低误报率阈值，提高风险敏感度"),
                # ===== InfoField/模式发现相关 =====
                (["模式发现", "事件模式"], "infofield_pattern_freq_threshold", -0.1, "降低模式频率阈值，发现更多模式"),
                (["模式过多", "噪音大"], "infofield_pattern_count_threshold", 3, "提高模式出现次数阈值，减少噪音"),
                # ===== 日志相关 =====
                (["日志过多", "日志爆炸"], "log_structured_parallel", 0, "关闭结构化并行DEBUG日志"),
                (["日志不足", "调试信息少"], "log_structured_parallel", 1, "开启结构化并行DEBUG日志"),
                # ===== 大模型/推理相关 =====
                # （A-9：原大模型补救频繁项的 search_quality_threshold 已移除——死参数；
                #   补救频繁场景改由 search_cooldown_seconds 承担（拉长冷却减少无效搜索））
                (["大模型调用多", "补救频繁"], "search_cooldown_seconds", 10, "拉长搜索冷却，减少无效搜索与补救"),
                (["推理深度不足", "回答质量差"], "stomach_purity_threshold", 0.05, "提高纯度阈值，提升知识质量"),
                # ===== 自主迭代相关 =====
                (["补丁验证慢", "验证等待长"], "evolution_patch_verify_wait", -20, "减少补丁验证等待时间"),
                (["补丁效果差", "回滚多"], "evolution_health_drop_rollback", -2, "降低健康度回滚阈值，更保守"),
            ]

            for _keywords, _param_name, _delta, _reason in _param_mappings:
                if any(kw in _desc or kw in _sugg for kw in _keywords):
                    # 获取当前值并计算新值
                    _current = ppm._get_current_value(_param_name)
                    if _current is None:
                        continue
                    _new_value = _current + _delta
                    # 生成补丁
                    _patch = ppm.generate_patch(
                        param_name=_param_name,
                        new_value=_new_value,
                        reason=f"{_reason}（来源: {_organ}参数审计）",
                        source="code_learner",
                    )
                    if _patch and _patch.get("status") == "pending":
                        self._log(LogLevel.DEBUG,
                                 f"参数补丁: {_param_name} {_current}→{_new_value} ({_reason})")
                        return _patch
            return None
        except Exception as e:
            self._log(LogLevel.DEBUG, f"建议转补丁异常: {e}")
            return None

    def _audit_runtime_parameters(self) -> list[dict]:
        """
        ★v25.1: 运行时参数优化审计。
        检查框架运行时参数是否合理，生成优化建议。
        不只是查代码bug，还包括：线程池饱和、超时频率、检索深度、学习批次耗时等。
        返回 SelfInspector 兼容的 issue 列表。
        """
        _issues = []
        try:

            # 1. 检查 InfoField 线程池配置（如果有运行时统计）
            try:
                if hasattr(self, 'info_field') and self.info_field:
                    _stats = getattr(self.info_field, 'get_stats', dict)()
                    _active = _stats.get('active_tasks', 0)
                    _pool_size = _stats.get('adaptive_pool_size', 0)
                    if _pool_size > 0 and _active / _pool_size > 0.8:
                        _issues.append({
                            "file": "config.py",
                            "organ": "InfoField",
                            "method": "thread_pool",
                            "line": 0,
                            "type": "param_optimization",
                            "description": f"线程池饱和度高({_active}/{_pool_size}={_active/_pool_size:.0%})，可能导致任务排队",
                            "suggestion": "建议增大 info_field 线程池配置或启用自适应扩缩",
                            "lifecycle": "new",
                            "source": "param_audit",
                            "current": f"{_active}/{_pool_size}",
                            "suggested": "增大池大小或启用自适应",
                        })
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 2. 检查代码学习批次大小（如果最近有超时记录）
            try:
                _batch_timeout = getattr(self, '_batch_timeout_count', 0)
                if _batch_timeout > 0:
                    _issues.append({
                        "file": "organs/brain/PulseCodeLearner.py",
                        "organ": "PulseCodeLearner",
                        "method": "_learn_own_code_structure",
                        "line": 0,
                        "type": "param_optimization",
                        "description": f"代码学习批次超时{_batch_timeout}次，批次可能过大",
                        "suggestion": "建议减小批次大小或增大超时阈值",
                        "lifecycle": "new",
                        "source": "param_audit",
                        "current": f"超时{_batch_timeout}次",
                        "suggested": "减小批次/增大超时",
                    })
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 3. 检查大模型补救频率（使用验证学习枢纽真实统计）
            try:
                from nucleus.mnemosyne.verification_learning_hub import (
                    get_verification_learning_hub,
                )
                _vlh = get_verification_learning_hub()
                if _vlh:
                    _vlh_stats = _vlh.get_stats()
                    _total = _vlh_stats.get("total", 0)
                    _remediation_rate = _vlh_stats.get("remediation_rate", 0.0)
                    _top_reasons = _vlh_stats.get("top_failure_reasons", [])
                    if _total >= 10 and _remediation_rate > 0.3:
                        _reason_str = ", ".join(f"{r}({c}次)" for r, c in _top_reasons[:2]) if _top_reasons else "未知"
                        _issues.append({
                            "file": "organs/brain/PulseInnerWorld.py",
                            "organ": "PulseInnerWorld",
                            "method": "inference",
                            "line": 0,
                            "type": "param_optimization",
                            "description": f"大模型补救率过高({_remediation_rate:.0%}, {_total}条记录)，主要原因: {_reason_str}",
                            "suggestion": "建议增强内在世界推理深度或扩展知识检索范围",
                            "lifecycle": "new",
                            "source": "param_audit",
                            "current": f"{_remediation_rate:.0%}",
                            "suggested": "<20%",
                        })
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # ★v25.1: 日志质量信号分析——利用已有的"本地→大模型比对"链路数据
            #   从日志中统计搜索质量、检索质量、语义提取质量的信号频率
            try:
                _log_signals = self._analyze_quality_signals_from_log()
                if _log_signals:
                    _issues.extend(_log_signals)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 4. 检查结构化并行日志量（如果占比过高）
            try:
                _sp_log_ratio = getattr(self, '_sp_log_ratio', 0)
                if _sp_log_ratio > 0.5:
                    _issues.append({
                        "file": "nucleus/StructuredParallelScheduler.py",
                        "organ": "StructuredParallelScheduler",
                        "method": "logging",
                        "line": 0,
                        "type": "param_optimization",
                        "description": f"结构化并行日志占比过高({_sp_log_ratio:.0%})，日志噪音大",
                        "suggestion": "建议降低日志级别或减少子任务日志",
                        "lifecycle": "new",
                        "source": "param_audit",
                        "current": f"{_sp_log_ratio:.0%}",
                        "suggested": "<30%",
                    })
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        except Exception as _e:
            self._log(LogLevel.DEBUG, f"参数审计异常: {_e}")

        return _issues

    def _analyze_quality_signals_from_log(self) -> list[dict]:
        """
        ★v25.1: 从日志中分析质量信号，利用已有的"本地→大模型比对"链路数据。
        统计最近日志中搜索质量、检索质量、语义提取等问题的出现频率，
        当某类问题频繁出现时生成代码优化建议。
        """
        _issues = []
        try:
            import os
            _log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "logs", "pulse.log")
            if not os.path.exists(_log_path):
                return _issues

            # 只读最后2000行，避免大文件性能问题
            with open(_log_path, encoding='utf-8', errors='ignore') as _f:
                _lines = _f.readlines()[-2000:]

            _total = len(_lines)
            if _total < 100:
                return _issues  # 日志太少，不做统计

            # 统计各类质量信号
            _search_low = sum(1 for _ln in _lines if "搜索反馈" in _ln and "关联度低" in _ln)
            _search_terminate = sum(1 for _ln in _lines if "搜索反馈审查" in _ln and "终止" in _ln)
            _retrieval_low = sum(1 for _ln in _lines if "检索质量不足" in _ln or "检索质量偏低" in _ln)
            _semantic_low = sum(1 for _ln in _lines if "语义增强提取成功" in _ln)  # 语义提取成功次数
            _remediation = sum(1 for _ln in _lines if "触发补救" in _ln or "相关性低" in _ln)
            _digest_low = sum(1 for _ln in _lines if "消化验证" in _ln and "质量偏低" in _ln)

            _threshold = max(3, int(_total * 0.005))  # 阈值：至少3次或0.5%

            # 搜索质量差 → 根据具体日志关键词给出针对性建议
            if _search_low + _search_terminate >= _threshold:
                # 根因分析：检查日志中具体的搜索问题类型
                _keyword_mismatch = sum(1 for _ln in _lines if "关键词与主题无关" in _ln)
                _low_overlap = sum(1 for _ln in _lines if "关联度低" in _ln and "重叠=0" in _ln)
                _stage2_empty = sum(1 for _ln in _lines if "阶段2无文章产出" in _ln)
                if _keyword_mismatch > 0:
                    _root_cause = "阶段1关键词提取与主题不匹配"
                    _suggestion = "优化搜索词预处理逻辑，增强关键词与主题的相关性校验"
                elif _low_overlap > 0:
                    _root_cause = "搜索结果与现有知识重叠度低"
                    _suggestion = "优化搜索方向选择，或扩展相关领域知识库"
                elif _stage2_empty > 0:
                    _root_cause = "阶段2无文章产出"
                    _suggestion = "检查搜索源可用性，或放宽阶段2筛选条件"
                else:
                    _root_cause = "综合搜索质量偏低"
                    _suggestion = "优化搜索词提取逻辑或阶段1关键词审查算法"
                _issues.append({
                    "file": "organs/brain/PulseInnerWorld.py",
                    "organ": "PulseInnerWorld",
                    "method": "_handle_search_stage_feedback",
                    "line": 0,
                    "type": "quality_signal",
                    "description": f"搜索质量信号频繁: {_root_cause} (关联度低{_search_low}次, 终止{_search_terminate}次)",
                    "suggestion": _suggestion,
                    "lifecycle": "new",
                    "source": "log_signal_analysis",
                    "current": f"{_search_low + _search_terminate}次/{_total}行",
                    "suggested": f"<{_threshold}次",
                })

            # 检索质量差 → 根据具体原因给出针对性建议
            if _retrieval_low >= _threshold:
                _low_trust = sum(1 for _ln in _lines if "检索质量不足" in _ln and "信任" in _ln)
                _no_context = sum(1 for _ln in _lines if "无匹配上下文" in _ln)
                if _low_trust > 0:
                    _root_cause = "检索结果信任度低"
                    _suggestion = "优化节点信任度评估，或增强高信任节点的检索权重"
                elif _no_context > 0:
                    _root_cause = "无匹配上下文"
                    _suggestion = "扩展检索深度（增加L2/L3扫描节点数）或优化索引匹配"
                else:
                    _root_cause = "检索质量综合偏低"
                    _suggestion = "优化检索深度或扩展相关领域知识节点"
                _issues.append({
                    "file": "organs/brain/PulseInnerWorld.py",
                    "organ": "PulseInnerWorld",
                    "method": "knowledge_retrieval",
                    "line": 0,
                    "type": "quality_signal",
                    "description": f"知识检索质量不足: {_root_cause} ({_retrieval_low}次)",
                    "suggestion": _suggestion,
                    "lifecycle": "new",
                    "source": "log_signal_analysis",
                    "current": f"{_retrieval_low}次/{_total}行",
                    "suggested": f"<{_threshold}次",
                })

            # 消化质量差 → 根据具体原因给出针对性建议
            if _digest_low >= _threshold:
                _few_keywords = sum(1 for _ln in _lines if "有效关键词过少" in _ln)
                _low_density = sum(1 for _ln in _lines if "中文信息密度过低" in _ln)
                _short_content = sum(1 for _ln in _lines if "内容偏短" in _ln)
                if _few_keywords > 0:
                    _root_cause = "有效关键词过少"
                    _suggestion = "优化关键词提取逻辑，或降低关键词数量阈值"
                elif _low_density > 0:
                    _root_cause = "中文信息密度过低"
                    _suggestion = "增强噪音过滤，或调整信息密度评估阈值"
                elif _short_content > 0:
                    _root_cause = "内容偏短且密度低"
                    _suggestion = "优化内容长度阈值，或增强短内容的质量评估"
                else:
                    _root_cause = "消化质量综合偏低"
                    _suggestion = "优化消化质量评估逻辑或增强噪音过滤"
                _issues.append({
                    "file": "organs/PulseStomach.py",
                    "organ": "PulseStomach",
                    "method": "digest",
                    "line": 0,
                    "type": "quality_signal",
                    "description": f"胃消化质量偏低: {_root_cause} ({_digest_low}次)",
                    "suggestion": _suggestion,
                    "lifecycle": "new",
                    "source": "log_signal_analysis",
                    "current": f"{_digest_low}次/{_total}行",
                    "suggested": f"<{_threshold}次",
                })

            if _issues:
                self._log(LogLevel.INFO,
                         f"日志质量信号分析: 搜索{_search_low}/{_search_terminate}, "
                         f"检索{_retrieval_low}, 消化{_digest_low}, 补救{_remediation} "
                         f"(阈值={_threshold}, 生成{len(_issues)}个优化建议)")

        except Exception as _e:
            self._log(LogLevel.DEBUG, f"日志信号分析异常: {_e}")

        return _issues

    # ===== ★PHASE13-P1-4（2026-09-07）：代码学习通道「待审批补丁」门禁 =====

    @staticmethod
    def _pending_patch_keys() -> set[tuple[str, str]]:
        """读取待审批补丁，构造 {(类名, 方法名)} 集合，供上游发现侧去重。

        为什么需要它（15 小时运行日志实证）：
            同样 4 个问题被代码学习**每 30 分钟重复发现**，15 小时累计
            PulseCortex._get_guidance 约 60 次、其余各约 30 次。
            这些问题早已生成补丁躺在 pending_patches.json 里等人工审批，
            但发现侧完全不知道，于是「发现→生成→入队→再发现→再入队」空转，
            既浪费算力，又让日志被同一批问题刷屏，真正的新问题反而被淹没。

        匹配口径：
            以 **(类名, 方法名)** 为准，而非文件路径。原因：
            - issue 的 file 可能是 Windows 绝对路径
              （日志实证：`<PROJECT_ROOT>\\...\\organs\\body\\PulseLiver.py`）；
            - 补丁的 file 多为项目相对路径（`organs/body/PulseLiver.py`）。
            两者直接比字符串永远比不上，按 basename/类名比才稳。

        失败处理：读取异常一律返回空集 → 门禁不生效 → 行为退回原样，
        绝不让「去重」变成「发现功能整体失效」（★绝不阻断闭环）。
        """
        _keys: set[tuple[str, str]] = set()
        try:
            from nucleus.reasoning.PatchManager import PatchManager

            _project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _pm = PatchManager(_project_root)

            def _cls_of(patch_file: str, patch: dict) -> str:
                # 优先用补丁自带的 organ/target；没有再从路径推类名
                for _k in ("organ", "target", "class_name"):
                    if patch.get(_k):
                        return str(patch[_k])
                _base = os.path.basename(str(patch_file or "").replace("\\", "/"))
                return os.path.splitext(_base)[0]

            for _p in (_pm.list_pending_patches() or []):
                _method = str(_p.get("method") or "").strip()
                _cls = _cls_of(_p.get("file", ""), _p)
                if _cls and _method:
                    _keys.add((_cls, _method))
        except Exception:
            return set()
        return _keys

    @staticmethod
    def _pending_backlog_report() -> tuple[int, float]:
        """★PHASE14（2026-09-07）：待审批补丁积压体检。

        返回 (积压数量, 最老一条已等待秒数)；无法读取时返回 (0, 0.0)。

        为什么要加这个（2h33m 运行日志实证）：
            待审批门禁的跳过数随时间单调增长 4 → 9 → 13 → 13，
            说明**补丁只进不出**：人工不裁决，补丁就永远躺在队列里，
            与之对应的问题也就永远停在「待审批」态，既不会被再次处理，
            也不会消失。星轨把这当作「待用户决策」的静态项记录，
            但它其实是个会持续膨胀的死水潭——框架自己完全看不见。
            本方法不做任何自动清理（避免误删有价值的补丁），
            只把「有多少、等了多久」变成可观测信号，交给人来判断。
        """
        try:
            from nucleus.reasoning.PatchManager import PatchManager
            _project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _pm = PatchManager(_project_root)
            _all = _pm.list_pending_patches() or []
            if not _all:
                return 0, 0.0
            _now = time.time()
            _oldest = 0.0
            for _p in _all:
                for _k in ("created_at", "timestamp", "generated_at",
                           "created", "time"):
                    _v = _p.get(_k)
                    if isinstance(_v, (int, float)) and 0 < _v < _now:
                        _oldest = max(_oldest, _now - float(_v))
                        break
            return len(_all), _oldest
        except Exception:
            return 0, 0.0

    def _filter_issues_with_pending_patch(self, issues: list) -> tuple[list, int]:
        """过滤掉「已有待审批补丁」的问题，返回 (剩余问题, 被过滤数量)。"""
        if not issues:
            return issues, 0
        try:
            _pending = self._pending_patch_keys()
            if not _pending:
                return issues, 0
            # ★PHASE14：借本方法的每轮调用时机做一次积压体检（零额外扫描成本，
            #   补丁列表上一行刚读过）。超阈值才告警，避免常态化刷屏。
            try:
                _cnt, _oldest_s = self._pending_backlog_report()
                _thr = 8
                try:
                    import config as _pcfg
                    _thr = int(getattr(_pcfg, "EVOLUTION_CONFIG", {})
                               .get("pending_backlog_warn_threshold", 8))
                except Exception:
                    pass
                if _cnt >= _thr:
                    _h = _oldest_s / 3600.0
                    _age = (f"{_h:.1f}小时" if _h >= 1.0
                            else f"{_oldest_s / 60.0:.0f}分钟")
                    self._log(LogLevel.WARNING,
                              f"[待审批积压] 已有 {_cnt} 个补丁等待人工裁决"
                              f"（最老已等待 {_age}）；未裁决前这些位置不会被"
                              f"再次尝试修复，也不会自动消失。"
                              f"如需放行/拒绝，请处理 pending_patches.json")
            except Exception:
                pass
            _kept, _dropped = [], 0
            for _iss in issues:
                _organ = str(_iss.get("organ") or "").strip()
                if not _organ:
                    # 无 organ 时从 file 推类名（兼容日志归因类 issue）
                    _f = str(_iss.get("file") or "").replace("\\", "/")
                    _organ = os.path.splitext(os.path.basename(_f))[0]
                _method = str(_iss.get("method") or "").strip()
                if _organ and _method and (_organ, _method) in _pending:
                    _dropped += 1
                    continue
                _kept.append(_iss)
            return _kept, _dropped
        except Exception:
            # 门禁异常一律放行，宁可重复发现也不能漏发现
            return issues, 0

    def _review_own_code_issues(self):
        """
        代码自我审视：调用 SelfInspector 检测自身代码中的反模式，
        将发现的问题写入日志，并生成补丁供创造者审核。
        ★v17.0 R2修复：接入执行链——发现问题→生成方案→生成补丁→存入待审核队列
        ★v18.0修复：按生命周期排序、扩大批次、自动应用低风险补丁、统一开关策略
        ★v25.1: 统一调度冷却——3个触发点(定期500心跳/健康度驱动/2小时强制)共用此入口，
          20分钟冷却期内不重复执行，避免短时间内多次审视浪费资源。
        """
        # ★v25.1: 统一冷却检查（所有触发点共用）
        _now = time.time()
        _cooldown = 1200  # 20分钟冷却
        if hasattr(self, '_last_code_review_at') and (_now - self._last_code_review_at) < _cooldown:
            self._log(LogLevel.DEBUG,
                     f"代码审视冷却中，跳过（距上次{_now - self._last_code_review_at:.0f}s < {_cooldown}s）")
            return
        self._last_code_review_at = _now

        try:
            self._log(LogLevel.INFO, "代码自我审视触发...")
            inspector = self._get_inspector()
            issues = inspector.detect_code_issues()

            # ★进化闭环升级(完美级): 接入日志动态归因（阶段1：如何发现问题）
            #   不再只靠静态规则 detect_code_issues，而是把「运行日志 Traceback
            #   精确定位 file:line:func」的动态问题也纳入审视，实现「日志→代码」精准关联。
            try:
                from nucleus.evolution.LogAnalyzer import analyze_logs
                _project_root_for_log = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                _log_analysis = analyze_logs(_project_root_for_log, max_issues=20)
                _log_issues = _log_analysis.get("issues", [])
                if _log_issues:
                    # 把日志归因结果转成 SelfInspector 兼容的 issue 结构
                    for _li in _log_issues:
                        if not _li.get("file_path"):
                            continue  # 无文件定位的（如纯 ERROR 行）跳过，留给静态规则
                        _organ_from_file = os.path.splitext(os.path.basename(_li["file_path"]))[0]
                        _log_issue = {
                            "file": _li["file_path"],
                            "organ": _organ_from_file,
                            "method": _li.get("method", ""),
                            "line": _li.get("line", 0),
                            "type": f"runtime_{_li.get('error_type', 'error').lower()}",
                            "description": _li.get("message", "")[:200],
                            "suggestion": f"运行日志定位到该处出现 {_li.get('count', 1)} 次异常，建议修复",
                            "lifecycle": "new",
                            "source": "log_attribution",
                        }
                        issues.append(_log_issue)
                    self._log(LogLevel.INFO,
                             f"日志动态归因: 发现 {len(_log_issues)} 个运行时问题定位，"
                             f"已并入审视队列")
            except Exception as _log_e:
                self._log(LogLevel.DEBUG, f"日志动态归因异常(忽略): {_log_e}")

            # ★v25.1: 运行时参数优化审计——不只是查代码bug，还检查参数是否合理
            #   检查项：线程池饱和、超时频率、检索深度、学习批次耗时等
            try:
                _param_issues = self._audit_runtime_parameters()
                if _param_issues:
                    self._log(LogLevel.INFO,
                             f"参数优化审计: 发现 {len(_param_issues)} 个参数优化建议")
                    for _pi in _param_issues:
                        self._log(LogLevel.WARNING,
                                 f"参数优化 [{_pi['type']}] {_pi['description']} "
                                 f"(当前={_pi.get('current','?')}, 建议={_pi.get('suggested','?')})")
                    issues = issues + _param_issues
            except Exception as _param_e:
                self._log(LogLevel.DEBUG, f"参数优化审计异常(忽略): {_param_e}")

            # ★P0: 低频调用外部工具层（compileall/ruff/mypy/bandit），
            #   每 20 次审视才跑一次全量工具扫描，避免每次审视都全量扫描拖慢主链路
            _sweep_counter = getattr(self, '_tooling_sweep_counter', 0) + 1
            self._tooling_sweep_counter = _sweep_counter
            if _sweep_counter % 20 == 0:
                try:
                    _tool_issues = inspector.run_tool_checks()
                    if _tool_issues:
                        self._log(LogLevel.INFO,
                                 f"工具层检查: 发现 {len(_tool_issues)} 个问题（compileall/ruff/mypy/bandit）")
                        issues = issues + _tool_issues
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # ★P2: 运行时引导的定向深度审查（有异常快照/队列积压/重入时才触发 LLM）
            try:
                from nucleus.reasoning.SafeEvolutionExecutor import (
                    SafeEvolutionExecutor,
                )
                _review_executor = SafeEvolutionExecutor()
                _review_result = _review_executor.run_in_subprocess(
                    issues=[], mode="review", timeout=TIMEOUT_CONFIG["code_review"], max_issues=10
                )
                if _review_result.get("status") == "success":
                    _runtime_review = _review_result.get("stats", {})
                else:
                    _runtime_review = _review_executor.run_runtime_guided_review(inspector)
                if _runtime_review.get("reviewed", 0) > 0:
                    self._log(LogLevel.INFO,
                             f"运行时定向审查: 审查{_runtime_review.get('reviewed',0)}个异常, "
                             f"蒸馏{_runtime_review.get('distilled',0)}条")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            if not issues:
                return

            # ★v25.1: 分离参数优化/质量信号issue——这些不是代码bug，不能走代码repair流程
            #   单独记录为配置优化建议，避免repair尝试修复无法修复的issue
            _code_issues = []
            _param_suggestions = []
            for _iss in issues:
                if _iss.get("type") in ("param_optimization", "quality_signal"):
                    _param_suggestions.append(_iss)
                else:
                    _code_issues.append(_iss)

            if _param_suggestions:
                self._log(LogLevel.INFO,
                         f"参数优化建议: {len(_param_suggestions)}条（已记录，不进入代码repair流程）")
                for _ps in _param_suggestions:
                    self._log(LogLevel.WARNING,
                             f"[参数优化] {_ps['organ']}.{_ps.get('method','')}: "
                             f"{_ps['description']} → {_ps.get('suggestion','')}")

                # ★P1补强：将可识别的参数优化建议转化为参数补丁（类似代码补丁流程）
                try:
                    from nucleus.evolution.ParamPatchManager import (
                        get_param_patch_manager,
                    )
                    _ppm = get_param_patch_manager()
                    _patches_generated = 0
                    _generated_patches = []  # ★A-4：收集本批生成的补丁
                    for _ps in _param_suggestions:
                        _patch = self._convert_suggestion_to_patch(_ps, _ppm)
                        if _patch and _patch.get("status") == "pending":
                            _patches_generated += 1
                            _generated_patches.append(_patch)
                    if _patches_generated > 0:
                        self._log(LogLevel.INFO,
                                 f"参数补丁生成: {_patches_generated}个参数补丁已生成（待验证应用）")
                        # ★A-4（2026-09-08）：灰度 ENABLE_AUTO_PARAM_APPLY 开启时，
                        #   低风险数值补丁自动应用（风险分级/限流/验证/回滚由
                        #   AutoParamApplier 保证）；关闭时留在 pending 队列人工审批（零回退）。
                        try:
                            from nucleus.evolution.AutoParamApplier import (
                                get_auto_param_applier,
                            )
                            get_auto_param_applier(
                                log_fn=lambda m: self._log(LogLevel.INFO, m)
                            ).submit_patches(_generated_patches, source="code_learner")
                        except Exception as _apa:
                            self._log(LogLevel.DEBUG, f"参数自动应用异常(已忽略): {_apa}")
                except Exception as _ppe:
                    self._log(LogLevel.DEBUG, f"参数补丁生成异常: {_ppe}")

                # 写入配置优化建议文件（供下次启动时参考）
                try:
                    import json as _json
                    import os as _os
                    _suggestion_file = _os.path.join(
                        _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                        "data", "evolution", "param_optimization_suggestions.json")
                    _os.makedirs(_os.path.dirname(_suggestion_file), exist_ok=True)
                    _existing = []
                    if _os.path.exists(_suggestion_file):
                        _existing = safe_read_json(_suggestion_file, default={})
                    for _ps in _param_suggestions:
                        _ps["suggested_at"] = time.time()
                        _existing.append(_ps)
                    # 只保留最近50条
                    _existing = _existing[-50:]
                    with open(_suggestion_file, 'w', encoding='utf-8') as _f:
                        _json.dump(_existing, _f, ensure_ascii=False, indent=2)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            issues = _code_issues

            # ★PHASE13-P1-4（2026-09-07）：发现侧「待审批补丁」门禁。
            #   已生成补丁、正等人工审批的问题，不再重复上报——补丁被批准或拒绝后
            #   （届时它会从 pending 队列移出）才允许重新发现。
            #   与下游 SafeEvolutionExecutor 的 LLM 门禁形成「上下游双闸」：
            #   上游不再重复发现，下游不再重复生成。
            issues, _dup_dropped = self._filter_issues_with_pending_patch(issues)
            if _dup_dropped:
                self._log(LogLevel.INFO,
                         f"[待审批门禁] 本轮跳过{_dup_dropped}个已有待审批补丁的问题"
                         f"（等待人工裁决后再重新发现，避免重复上报与重复生成）")

            if not issues:
                self._log(LogLevel.INFO, "代码自我审视: 无代码问题（仅参数优化建议），跳过repair")
                return

            self._log(LogLevel.INFO, f"代码自我审视: 发现 {len(issues)} 个潜在代码问题")
            # ★第22批 T5/P2-123：噪音治理 —— 下面 5 条是 SelfInspector 每次扫描都会
            #   命中的**存量已知问题**（跨模块单例调用/静默异常/方法过长等），每次
            #   启动重复告警会淹没真实告警 → 降为 DEBUG（保留完整可追溯性）；
            #   关闭 ENABLE_LOG_NOISE_REDUCTION 时保持原 WARNING。
            _m22_nr = True
            try:
                import config as _cfg_m22
                _m22_nr = bool(getattr(_cfg_m22, "ENABLE_LOG_NOISE_REDUCTION", True))
            except Exception:
                _m22_nr = True
            _m22_issue_level = LogLevel.DEBUG if _m22_nr else LogLevel.WARNING
            for issue in issues[:5]:
                self._log(_m22_issue_level,
                         f"代码问题 [{issue['type']}] {issue['organ']}.{issue.get('method','')} "
                         f"({issue['file']}:{issue['line']}) - {issue['description']}")
            # ★第22批 T5/P2-123：明细降噪后**必须**有汇总输出（避免完全静默）。
            #   首次扫描 + 此后每 100 次扫描输出 1 次 INFO 问题类型分布摘要。
            try:
                _m22_n = getattr(self, "_m22_scan_count", 0) + 1
                self._m22_scan_count = _m22_n
                _type_dist = {}
                for _iss in issues:
                    _tk = _iss.get("type", "未知")
                    _type_dist[_tk] = _type_dist.get(_tk, 0) + 1
                if _m22_n == 1 or _m22_n % 100 == 0:
                    _dist_str = "，".join(
                        f"{_k}×{_v}" for _k, _v in
                        sorted(_type_dist.items(), key=lambda kv: -kv[1])[:8])
                    self._log(LogLevel.INFO,
                              f"代码质量扫描完成：发现{len(issues)}个问题（{_dist_str}）")
            except Exception as _m22_e:
                self._log(LogLevel.DEBUG, f"代码质量汇总输出失败: {_m22_e}")

            # ★FIX(知识蒸馏): 自身规则修复 → 大模型对比 → 记录到验证学习枢纽，
            #   让大模型对同一问题代码的修复建议沉淀为可复用规则，提升缺陷处理能力
            try:
                from nucleus.reasoning.SafeEvolutionExecutor import (
                    SafeEvolutionExecutor,
                )
                _repair_executor = SafeEvolutionExecutor()
                # ★主线第16批 T3/P2-97：本层可见重试（仅对 crashed/error 重试；
                #   timeout 不重试，避免超时任务耗时长翻倍）。子进程内部的崩溃重试
                #   由第15批 T3 的 run_in_subprocess 负责，此处是**第二道**保障。
                _retry_on, _max_retries = self._code_distill_retry_config()
                _attempts = 1 + (_max_retries if _retry_on else 0)
                _repair_result: dict[str, Any] = {}
                for _attempt in range(1, _attempts + 1):
                    _repair_result = _repair_executor.run_in_subprocess(
                        issues=issues, mode="repair",
                        timeout=TIMEOUT_CONFIG["code_review"], max_issues=5
                    )
                    if _repair_result.get("status") == "success":
                        break
                    if _repair_result.get("status") == "timeout":
                        break          # 超时不重试
                    if _attempt < _attempts:
                        self._log(LogLevel.WARNING,
                                 f"代码修复蒸馏子进程第{_attempt}次失败"
                                 f"({_repair_result.get('status')})，准备重试"
                                 f"（{_attempt}/{_attempts - 1}）")
                if _repair_result.get("status") == "success":
                    _distill_report = _repair_result.get("stats", {})
                    self._record_code_distill("success", "")
                else:
                    # ★T3：把第15批已落盘的原因字段真正打出来（此前被丢弃）
                    _reason = self._describe_code_distill_failure(_repair_result)
                    self._log(LogLevel.WARNING,
                             f"代码修复蒸馏子进程失败({_repair_result.get('status')})，"
                             f"回退主进程执行 | 原因={_reason}")
                    _tb = str(_repair_result.get("crash_traceback", "") or "")
                    if _tb:
                        self._log(LogLevel.WARNING,
                                 f"代码修复蒸馏崩溃 traceback(尾): {_tb[-800:]}")
                    _err_tail = str(_repair_result.get("stderr_tail", "") or "")
                    if _err_tail:
                        self._log(LogLevel.WARNING,
                                 f"代码修复蒸馏 stderr(尾): {_err_tail[-600:]}")
                    self._record_code_distill("failed", _reason)
                    # ★第95批 T-95d：显式声明这是「代码学习」场景的 LLM 调用
                    #   （此前一律归入 SCENE_EVOLUTION ⇒「代码学习」恒 0）。
                    #   仅归类不同，不重复计数。已知局限：子进程路径
                    #   （run_in_subprocess）的调用仍归 SCENE_EVOLUTION。
                    try:
                        from nucleus.LLMDependencyMetrics import (
                            SCENE_CODE_LEARN as _m95_code_learn,
                        )
                    except Exception:
                        _m95_code_learn = None
                    _distill_report = _repair_executor.repair_with_distillation(
                        issues, inspector, scene=_m95_code_learn)
                    self._code_distill_stats["degraded_to_main"] += 1
                self._log(LogLevel.INFO,
                         f"代码修复蒸馏: 本地可修{_distill_report.get('local_fixable',0)}个, "
                         f"本地生成{_distill_report.get('local_patched',0)}个, "
                         f"本地验证{_distill_report.get('local_verified',0)}个, "
                         f"本地提交{_distill_report.get('local_submitted',0)}个, "
                         f"大模型对比{_distill_report.get('llm_compared',0)}个, "
                         f"大模型更优{_distill_report.get('llm_better',0)}个, "
                         f"蒸馏{_distill_report.get('distilled',0)}条, "
                         f"LLM提交{_distill_report.get('submitted',0)}个")
            except Exception as _distill_err:
                # ★2026-09-03修复：异常不再静默吞掉，记录错误便于排查自主迭代闭环0提交问题
                self._log(LogLevel.ERROR, f"代码修复蒸馏异常: {type(_distill_err).__name__}: {_distill_err}")

            # ★v25.1: 两条修复路径去重——Path A(repair_with_distillation)已处理12类本地可修问题，
            #   Path B(simulate→execute)只处理剩余类型，避免重复生成补丁。
            _local_fixable_types = {
                "silent_exception", "bare_except", "status_request_duplicate",
                "unbounded_deque", "thread_no_daemon", "no_timeout_http",
                "mutable_default_arg", "comparison_with_none", "boolean_comparison",
                "os_path_join", "print_instead_of_log", "fstring_preferred",
            }
            _path_b_issues = [i for i in issues if i.get("type") not in _local_fixable_types]
            if len(_path_b_issues) < len(issues):
                self._log(LogLevel.DEBUG,
                         f"修复路径去重: Path A已处理{len(issues)-len(_path_b_issues)}个本地可修问题，"
                         f"Path B处理剩余{len(_path_b_issues)}个")

            # ★v18.0修复：按生命周期状态排序 + 扩大批次 + 修复验证闭环
            # 优先级：reopened > new > persistent > legacy
            _lifecycle_priority = {"reopened": 0, "new": 1, "persistent": 2}
            _sorted_issues = sorted(
                _path_b_issues,
                key=lambda i: _lifecycle_priority.get(i.get("lifecycle", "persistent"), 3)
            )
            # 扩大处理批次：从10个提升到30个
            _top_issues = _sorted_issues[:30]

            try:
                # ★骨架优化：优先使用注入的沙箱，未注入时回退全局单例（统一到基类）
                _sandbox = self._get_evolution_sandbox()
                # ★A-6（2026-09-08）：修复前先查经验库（举一反三前置步骤）。
                #   命中策略随 context 提供给沙箱/LLM 参考，**不改写任何修复决策**；
                #   灰度关闭时 collect_experience_hints 返回 []，context 与改造前一致。
                _exp_hints = self.collect_experience_hints(_top_issues)
                _simulation = _sandbox.simulate(_top_issues, context={
                    "total_issues": len(issues),
                    "source": "code_self_review",
                    **({"experience_hints": _exp_hints} if _exp_hints else {}),
                })

                if _simulation and _simulation.get("plans"):
                    from nucleus.reasoning.SafeEvolutionExecutor import (
                        SafeEvolutionExecutor,
                    )
                    _executor = SafeEvolutionExecutor()
                    _patch_report = _executor.execute(_simulation["plans"], inspector)

                    if _patch_report and _patch_report.get("patches"):
                        import os as _os

                        from nucleus.reasoning.PatchManager import PatchManager
                        _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                        _patch_mgr = PatchManager(_project_root)

                        # ★v18.0：自动应用低风险补丁（风险等级1=仅添加日志/注释）
                        _auto_applied = 0
                        _pending_count = 0
                        _batch_size = min(15, len(_patch_report["patches"]))

                        # ★v18.0修复：检查总开关，统一两个补丁来源的自动应用策略
                        _auto_apply_enabled = False
                        try:
                            import config as _cfg_ev
                            _ev_cfg = getattr(_cfg_ev, 'EVOLUTION_CONFIG', {})
                            _auto_apply_enabled = _ev_cfg.get("auto_apply_enabled", False)
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                        # ★第54批 T2（P2-375）：自动应用 risk 门槛改为读配置，消除硬编码 risk<=1。
                        #   灰度 ENABLE_PCL_RISK_FROM_CONFIG：关闭 → 回退 1，与改造前完全一致。
                        _risk_max = 1
                        try:
                            if getattr(_cfg_ev, "ENABLE_PCL_RISK_FROM_CONFIG", True):
                                _risk_max = int(_ev_cfg.get("auto_apply_max_risk", 1))
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                        for _patch in _patch_report["patches"][:_batch_size]:
                            _risk_raw = _patch.get("risk_level", 99)
                            # ★FIX(C1): risk_level 可能是字符串（"低"/"中等"/"高"），
                            #   统一归一化为数值，避免字符串与整数比较 TypeError
                            _risk_map = {"极低": 1, "低": 2, "中等": 3, "高": 4}
                            if isinstance(_risk_raw, str):
                                _risk = _risk_map.get(_risk_raw, 99)
                            else:
                                _risk = _risk_raw

                            # ★PULSE-DEFECT-20260901-01 修复：统一「验证通过才落库」。
                            #   原逻辑存在两处缺陷：
                            #   1) 自动应用分支验证失败仍落库（2291行）；
                            #   2) 总开关关闭时跳过验证直接落库（2295行）。
                            #   两者都会让 LLM 截断/残缺补丁混入待审核队列。
                            #   修复后：所有补丁一律先 verify_in_copy，通过才落库，失败只记日志丢弃。
                            _verify_result = _patch_mgr.verify_in_copy(_patch)
                            if _verify_result.get("passed"):
                                # ★A-6（2026-09-08）：补丁验证通过 = 一次成功修复 →
                                #   沉淀「问题模式→修复策略」经验，供其他领域复用（举一反三）。
                                #   灰度 ENABLE_EXPERIENCE_TRANSFER 默认关；关闭时零行为。
                                #   ★红线：只沉淀，不改变任何现有修复流程。
                                try:
                                    self._record_repair_experience(_patch)
                                except Exception as e:
                                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                                if _auto_apply_enabled and isinstance(_risk, int) and _risk <= _risk_max:
                                    # 总开关开启 + 低风险补丁：验证通过后自动应用
                                    _patch["auto_applied"] = True
                                    _patch_mgr.save_pending_patch(_patch)
                                    _auto_applied += 1
                                else:
                                    # 验证通过但总开关关闭 或 高风险补丁：存入待审核队列
                                    _patch_mgr.save_pending_patch(_patch)
                                    _pending_count += 1
                            else:
                                # ★修复：验证失败不落库，仅记录日志丢弃
                                _errs = _verify_result.get("errors", [])
                                self._log(LogLevel.WARNING,
                                          f"补丁验证失败已丢弃: {_patch.get('id', '')[:16]}... "
                                          f"→ {_errs}")

                        _total = _auto_applied + _pending_count
                        if _auto_applied > 0:
                            self._log(LogLevel.INFO,
                                     f"代码审视补丁: {_total}个补丁已处理 "
                                     f"({_auto_applied}个低风险已自动应用, "
                                     f"{_pending_count}个待审核)")
                        else:
                            self._log(LogLevel.INFO,
                                     f"代码审视补丁已生成: {_total}个补丁存入待审核队列, "
                                     f"等待创造者审核（未自动修改任何代码文件）")

                        # ★进化闭环升级(完美级): 接入 EvolutionLoop 完整流水线
                        #   对每个补丁执行「diff 归档 → 动态冒烟测试」，把
                        #   「修改前后对比记录 + 本地实际运行测试」内建为框架能力。
                        #   只有测试通过的补丁才允许进入「立即应用」请求。
                        try:
                            from nucleus.evolution.EvolutionLoop import EvolutionLoop
                            _evo_loop = EvolutionLoop(_project_root)
                            _approved_after_test = []
                            for _p in _patch_report["patches"][:_batch_size]:
                                if _p.get("status") != "approved":
                                    continue
                                _loop_detail = {}
                                try:
                                    # 归档 diff + 溯源
                                    _evo_loop.archive_patch(_p)
                                    # 动态冒烟测试
                                    _test_result = _evo_loop.test_patch(_p)
                                    _p["dynamic_test"] = _test_result
                                    if _test_result.get("passed"):
                                        _approved_after_test.append(_p)
                                    else:
                                        self._log(LogLevel.WARNING,
                                                 f"补丁动态测试未通过，跳过自动应用: "
                                                 f"{_p.get('file','')}:{_p.get('method','')} "
                                                 f"- {_test_result.get('summary','')}")
                                except Exception as _loop_e:
                                    self._log(LogLevel.DEBUG, f"进化流水线处理异常(忽略): {_loop_e}")
                            if _approved_after_test:
                                self._log(LogLevel.INFO,
                                         f"进化流水线: {len(_approved_after_test)}个补丁通过"
                                         f"归档+动态测试，可进入自动应用")
                        except Exception as _evo_e:
                            self._log(LogLevel.DEBUG, f"进化流水线初始化异常(忽略): {_evo_e}")

                        # ★进化闭环升级(完美级): 「验证通过即应用」
                        #   审视生成的补丁若已被批准(approved)且通过动态测试，
                        #   立即写入「应用请求」标记，主循环检测到后走「应用+重启」流程。
                        try:
                            _approved_patches = [
                                _p for _p in _patch_report["patches"][:_batch_size]
                                if _p.get("status") == "approved"
                                # ★第96批 T-96b（N1-④）：原缺省 True ⇒ 动态测试
                                #   **没跑过也当通过**（异常=假通过）。改 False：
                                #   拿不到 dynamic_test 结果时按**未通过**处理。
                                and _p.get("dynamic_test", {}).get("passed", False)
                            ]
                            if _approved_patches and self._evolution_driver is not None:
                                _approved_count = len(_approved_patches)
                                _ok = self._evolution_driver.request_apply_now(_approved_count)
                                if _ok:
                                    self._log(LogLevel.INFO,
                                             f"已提交{_approved_count}个补丁的「立即应用」请求，"
                                             f"主循环将触发应用+重启验证")
                        except Exception as _req_e:
                            self._log(LogLevel.DEBUG, f"提交应用请求异常(忽略): {_req_e}")
            except ImportError as _e:
                self._log(LogLevel.DEBUG, f"进化执行链模块未加载，跳过补丁生成: {_e}")
            except Exception as _e:
                self._log(LogLevel.WARNING, f"补丁生成异常（审视结果已记录，不影响框架运行）: {_e}")

        except Exception as e:
            self._log(LogLevel.ERROR, f"代码自我审视异常: {e}")

    # ========== 快照持久化 ==========

    def set_pending_extra_state(self, state: dict[str, Any]) -> None:
        """★P3-1公开封装：写入代码学习进度状态（替代跨模块对 _pending_extra_state 的私有直写）"""
        self._pending_extra_state = dict(state) if state else {}

    def get_pending_extra_state(self) -> dict[str, Any]:
        """获取待保存的代码理解进度状态"""
        _state = {}

        if hasattr(self, '_code_understanding_progress') and self._code_understanding_progress:
            _progress = self._code_understanding_progress
            _pending_summary = []
            for _p in _progress.get("pending", []):
                if not _p.get("understood", False):
                    _pending_summary.append({
                        "organ": _p.get("organ", ""),
                        "method": _p.get("method", ""),
                        "has_doc": _p.get("has_doc", False),
                        "understood": False,
                        "file_path": _p.get("file_path", ""),       # ★v17.0新增
                        "file_mtime": _p.get("file_mtime", 0.0),   # ★v17.0新增
                    })
            _state["code_understanding_progress"] = {
                "understood": _progress.get("understood", 0),
                "total_methods": _progress.get("total_methods", 0),
                "pending": _pending_summary,
            }
            if hasattr(self, '_code_call_graph'):
                _state["code_understanding_progress"]["call_graph"] = {
                    k: {
                        "called_methods": v.get("called_methods", []),
                        "organ": v.get("organ", ""),
                        "args": v.get("args", ""),
                    }
                    for k, v in self._code_call_graph.items()
                }
        # ★v17.0 D7新增：保存扫描结果缓存，避免重启后全量重扫
        if hasattr(self, '_cached_organ_structure') and self._cached_organ_structure:
            _state["cached_organ_structure"] = {
                "organ_count": len(self._cached_organ_structure),
                "organ_names": list(self._cached_organ_structure.keys())[:100],
                "cached_at": time.time(),
            }
            # 保存每个器官的方法数量（不保存完整结构体，太大了）
            _organ_method_counts = {}
            for _organ_name, _organ_info in self._cached_organ_structure.items():
                if isinstance(_organ_info, dict):
                    _organ_method_counts[_organ_name] = _organ_info.get("method_count", 0)
            _state["cached_organ_method_counts"] = _organ_method_counts

        return _state

    def _restore_code_progress_from_snapshot(self) -> dict[str, Any] | None:
        """从快照中恢复代码理解进度"""
        try:
            extra = getattr(self, '_pending_extra_state', {})
            if not extra:
                return None

            # ★v17.0 D7新增：从快照恢复扫描结果缓存
            _cached_structure = extra.get("cached_organ_structure", {})
            _cached_counts = extra.get("cached_organ_method_counts", {})
            if _cached_structure and _cached_counts:
                # 验证缓存的器官数量与当前实际文件数是否一致
                try:
                    _inspector = self._get_inspector()
                    _current_structure = _inspector.scan_all_organs()
                    _current_count = len(_current_structure)
                    _cached_count = _cached_structure.get("organ_count", 0)

                    if _current_count == _cached_count:
                        # 数量一致，检查每个器官的方法数是否变化
                        _methods_changed = False
                        for _organ_name, _cached_methods in _cached_counts.items():
                            _current_info = _current_structure.get(_organ_name, {})
                            _current_methods = _current_info.get("method_count", 0) if isinstance(_current_info, dict) else 0
                            if _current_methods != _cached_methods:
                                _methods_changed = True
                                break

                        if not _methods_changed:
                            # 方法数没有变化，复用缓存
                            self._cached_organ_structure = _current_structure
                            self._log(LogLevel.INFO,
                                     f"扫描缓存命中: {_cached_count}个器官, "
                                     f"方法数均未变化，跳过全量重扫")
                        else:
                            self._log(LogLevel.INFO,
                                     "扫描缓存失效: 方法数已变化，将重新扫描")
                    else:
                        self._log(LogLevel.INFO,
                                 f"扫描缓存失效: 器官数量变化({_cached_count}→{_current_count})")
                except Exception as _cache_e:
                    self._log(LogLevel.DEBUG, f"扫描缓存验证失败: {_cache_e}")

            code_progress = extra.get("code_understanding_progress", {})
            if code_progress:
                # ★v17.0修复：从知识库中统计实际已理解的代码方法数，作为兜底
                _kb_understood = 0
                if self.node_pool:
                    _code_nodes = self.node_pool.query(
                        evol_level="L2", space_path_prefix="/自我理解/代码", limit=5000
                    )
                    for _n in _code_nodes:
                        _val = str(_n.value) if _n.value else ""
                        if _val.startswith("[自我理解·"):
                            _kb_understood += 1
                # 取快照恢复数和知识库统计数中较大的那个
                _snapshot_understood = code_progress.get("understood", 0)
                _effective_understood = max(_snapshot_understood, _kb_understood)
                if _effective_understood > _snapshot_understood:
                    code_progress["understood"] = _effective_understood
                    self._log(LogLevel.INFO,
                             f"代码理解进度修正: 快照={_snapshot_understood}, 知识库={_kb_understood}, 有效={_effective_understood}")
                if _effective_understood > 0:
                    if code_progress.get("call_graph") and not hasattr(self, '_code_call_graph'):
                        self._code_call_graph = {}
                        for k, v in code_progress["call_graph"].items():
                            self._code_call_graph[k] = {
                                "called_methods": v.get("called_methods", []),
                                "organ": v.get("organ", ""),
                                "args": v.get("args", ""),
                                "understood_at": 0,
                            }
                    return code_progress
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return None
    def _trace_call_chain(self, organ_name: str, method_name: str,
                           max_depth: int = 3, _visited: set | None = None,
                           _depth: int = 0) -> dict[str, Any]:
        """
        ★v17.0 D4新增：递归追踪从入口方法开始的完整调用链路。

        从给定的入口方法开始，沿着调用关系图递归追踪被调用的方法，
        生成结构化的链路树。追踪深度可配置（默认3层），
        防止递归过深导致性能问题。

        Args:
            organ_name: 入口方法所属器官
            method_name: 入口方法名
            max_depth: 最大追踪深度（默认3层）
            _visited: 已访问的方法集合（防止循环调用）
            _depth: 当前递归深度

        Returns:
            {
                "method": str,
                "organ": str,
                "args": str,
                "doc": str,
                "depth": int,
                "calls": [...],  # 递归结构
                "truncated": bool,
            }
        """
        if _visited is None:
            _visited = set()

        _call_key = f"{organ_name}.{method_name}"

        # 防止循环调用和深度超限
        if _call_key in _visited or _depth >= max_depth:
            return {
                "method": method_name,
                "organ": organ_name,
                "truncated": True,
                "reason": "循环调用" if _call_key in _visited else f"达到最大深度{max_depth}",
                "depth": _depth,
                "calls": [],
            }

        _visited.add(_call_key)

        _result = {
            "method": method_name,
            "organ": organ_name,
            "args": "",
            "doc": "",
            "depth": _depth,
            "calls": [],
            "truncated": False,
        }

        # 获取方法详情
        try:
            _inspector = self._get_inspector()
            _detail = _inspector.get_method_body(organ_name, method_name)
            if _detail:
                _result["args"] = _detail.get("args", "")
                _result["doc"] = _detail.get("doc", "")[:120]
                _result["has_body"] = bool(_detail.get("body", ""))
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 从调用图中获取被调用的方法，递归追踪
        if hasattr(self, '_code_call_graph') and _call_key in self._code_call_graph:
            _called_methods = self._code_call_graph[_call_key].get("called_methods", [])
            # 最多追踪5个分支（避免过宽）
            for _called_name in _called_methods[:5]:
                # 被调用方法默认与调用方同器官（跨器官调用暂不追踪）
                _sub_result = self._trace_call_chain(
                    organ_name, _called_name,
                    max_depth=max_depth, _visited=_visited, _depth=_depth + 1
                )
                _result["calls"].append(_sub_result)

        return _result

    def _format_chain_as_text(self, chain: dict[str, Any], indent: int = 0) -> list[str]:
        """
        ★v17.0 D4新增：将链路树格式化为可读的文本行列表。
        """
        _lines = []
        _prefix = "  " * indent

        if chain.get("truncated"):
            _lines.append(f"{_prefix}↳ {chain['method']} ({chain.get('reason', '截断')})")
            return _lines

        _doc = chain.get("doc", "")
        _args = chain.get("args", "")
        _info = f" - {_doc}" if _doc else ""
        _info += f" (参数: {_args})" if _args else ""
        _lines.append(f"{_prefix}→ {chain['method']}{_info}")

        for _sub in chain.get("calls", []):
            _lines.extend(self._format_chain_as_text(_sub, indent + 1))

        return _lines

    def _build_call_chain_knowledge(self):
        """
        ★v17.0 D4新增：为已完成学习的器官构建方法调用链路知识。

        对每个已理解方法数≥5的器官，选取入口方法（以on_开头或start/stop），
        追踪完整调用链，生成链路描述存入知识库。
        """
        if not hasattr(self, '_code_call_graph') or not self._code_call_graph:
            return

        # 统计每个器官的方法数
        _organ_methods = {}
        for _key, _info in self._code_call_graph.items():
            _organ = _info.get("organ", "")
            _organ_methods[_organ] = _organ_methods.get(_organ, 0) + 1

        _chain_count = 0
        for _organ, _count in _organ_methods.items():
            if _count < 5:
                continue

            # 找到入口方法
            _entry_methods = []
            for _key, _info in self._code_call_graph.items():
                if _info.get("organ", "") == _organ:
                    _method_name = _key.split(".")[-1] if "." in _key else _key
                    if _method_name.startswith("on_") or _method_name in ("start", "stop"):
                        _entry_methods.append(_method_name)

            if not _entry_methods:
                continue

            # 为每个入口方法追踪链路（最多3个）
            for _entry in _entry_methods[:3]:
                _chain = self._trace_call_chain(_organ, _entry, max_depth=3)
                _lines = self._format_chain_as_text(_chain)

                if len(_lines) <= 1:
                    continue  # 没有子调用，跳过

                _chain_text = "\n".join(_lines)
                _knowledge_content = (
                    f"[代码链路·{_organ}] 从入口方法'{_entry}'开始的调用链路：\n"
                    f"{_chain_text}\n\n"
                    f"📌 此链路由代码自学习自动生成，展示了{_organ}的核心工作流程。"
                )

                # 创建L2知识节点
                _chain_node = PulseNode(
                    value=_knowledge_content,
                    keywords=[_organ, _entry, "调用链路", "工作流程", "代码自学习"],
                    source_organ=self.organ_name,
                    evol_level=PulseNode.EVOL_L2,
                    importance=PulseNode.IMPORTANCE_A,
                    abstraction=0.7,
                    space_path=f"/自我理解/代码/链路/{_organ}",
                )
                _chain_node.view_mode = "INNER_VIEW"
                _chain_node.trigger_reason = "self_understanding.call_chain"
                _health = _chain_node.evaluate_node_health(check_type="quality")
                _chain_node.trust_score = max(70.0, _health["trust_score"])

                if self.frequency_codec:
                    self.frequency_codec.encode_node(_chain_node)
                if self.node_pool:
                    _chain_node.value = clean_content_text(_chain_node.value) or _chain_node.value
                    self.node_pool.add(_chain_node)
                if self.knowledge_tree:
                    self.knowledge_tree.register_path(f"/自我理解/代码/链路/{_organ}")

                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": _knowledge_content,
                    "source_organ": self.organ_name,
                    "trigger_reason": "self_understanding.call_chain",
                    "importance": "A",
                    "view_mode": "INNER_VIEW",
                    "space_path": f"/自我理解/代码/链路/{_organ}",
                }, priority=2, layer="L2")

                _chain_count += 1
                if _chain_count >= 10:
                    break

            if _chain_count >= 10:
                break

        if _chain_count > 0:
            self._log(LogLevel.INFO, f"代码链路图谱: 已生成{_chain_count}条调用链路知识")
    def get_call_chain(self, organ_name: str | None = None, method_name: str | None = None) -> dict[str, Any]:
        """
        ★v17.0 D3新增：公开查询接口——供内在世界推理引擎查询代码调用关系。
        """
        if not hasattr(self, '_code_call_graph') or not self._code_call_graph:
            return {
                "found": False,
                "reason": "调用关系图尚未构建，请等待代码学习运行一段时间后再查询",
                "organ": organ_name or "全部",
                "graph_size": 0,
            }

        _result = {
            "found": False,
            "organ": organ_name or "全部",
            "method": method_name or "全部",
            "graph_size": len(self._code_call_graph),
        }

        # ★v17.0 F2诊断：输出当前调用图中的器官列表
        _available_organs = set()
        for _info in self._code_call_graph.values():
            _org = _info.get("organ", "")
            if _org:
                _available_organs.add(_org)
        _result["available_organs"] = sorted(_available_organs)[:20]

        if organ_name and method_name:
            _call_key = f"{organ_name}.{method_name}"
            if _call_key in self._code_call_graph:
                _info = self._code_call_graph[_call_key]
                _result["found"] = True
                _result["called_methods"] = _info.get("called_methods", [])
                _result["args"] = _info.get("args", "")
                _called_by = []
                for _caller_key, _caller_info in self._code_call_graph.items():
                    if method_name in _caller_info.get("called_methods", []):
                        _called_by.append(_caller_key)
                _result["called_by"] = _called_by[:10]
            else:
                _result["reason"] = f"方法{_call_key}不在调用图中"
        elif organ_name:
            _methods = []
            for _key, _info in self._code_call_graph.items():
                if _info.get("organ", "") == organ_name:
                    _method_name = _key.split(".")[-1] if "." in _key else _key
                    _methods.append({
                        "method": _method_name,
                        "calls": _info.get("called_methods", [])[:5],
                    })
            _result["found"] = len(_methods) > 0
            _result["methods"] = _methods
            _result["method_count"] = len(_methods)
            if not _methods:
                _result["reason"] = f"器官{organ_name}不在调用图中，当前覆盖的器官: {', '.join(sorted(_available_organs)[:10])}"
        else:
            _organ_stats = {}
            for _key, _info in self._code_call_graph.items():
                _organ = _info.get("organ", "未知")
                if _organ not in _organ_stats:
                    _organ_stats[_organ] = {"method_count": 0, "total_calls": 0}
                _organ_stats[_organ]["method_count"] += 1
                _organ_stats[_organ]["total_calls"] += len(_info.get("called_methods", []))
            _result["found"] = True
            _result["organ_stats"] = _organ_stats
            _result["total_methods"] = len(self._code_call_graph)

        return _result
    def get_call_graph_edges(self) -> list[dict[str, Any]]:
        """
        ★v25.0新增：返回扁平的调用边列表，供肝脏建立代码调用关联。

        Returns:
            [
                {"caller_organ": str, "caller_method": str, "callee": str},
                ...
            ]
        """
        edges = []
        if not hasattr(self, '_code_call_graph'):
            return edges
        for caller_key, info in self._code_call_graph.items():
            caller_organ = info.get("organ", "")
            caller_method = caller_key.split(".")[-1] if "." in caller_key else caller_key
            for callee in info.get("called_methods", []):
                edges.append({
                    "caller_organ": caller_organ,
                    "caller_method": caller_method,
                    "callee": callee,
                })
        return edges
    def _create_organ_alias_nodes(self, organ_name: str):
        """
        ★v24.0新增：创建器官中文别名节点。
        路径：/自我理解/器官别名/{中文名}
        内容：指向对应代码学习路径的简短描述。
        这些节点让内在世界在回答中文器官问题时能快速定位代码知识。
        """
        if not self.node_pool or not self.knowledge_tree:
            return

        aliases = self._get_organ_chinese_aliases(organ_name)
        code_path = f"/自我理解/代码/{organ_name}"

        for alias in aliases:
            # ★P2-26：跳过未替换的占位符别名，避免生成"[器官别名] [器官别名] ..."异常节点
            if is_placeholder_alias(alias):
                self._log(LogLevel.DEBUG, f"跳过占位符别名(残留未替换): {alias!r} → {organ_name}")
                continue
            alias_path = f"/自我理解/器官别名/{alias}"
            if self.knowledge_tree:
                self.knowledge_tree.register_path(alias_path)

            alias_value = build_organ_alias_value(alias, organ_name, code_path)
            keywords = [alias, organ_name, "器官别名", "代码学习"]

            # 检查是否已存在，存在则更新
            _existing = self.node_pool.query(
                evol_level="L2", space_path_prefix=alias_path, limit=5
            )
            if _existing:
                for _en in _existing:
                    if hasattr(_en, 'value'):
                        _en.value = alias_value
                        _en.trust_score = 90.0
                continue

            _alias_node = PulseNode(
                value=alias_value,
                keywords=keywords,
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=0.55,
                space_path=alias_path,
            )
            _alias_node.view_mode = "INNER_VIEW"
            _alias_node.trigger_reason = "self_understanding.organ_alias"
            _alias_node.trust_score = 90.0
            if self.frequency_codec:
                self.frequency_codec.encode_node(_alias_node)
            self.node_pool.add(_alias_node)
            self._log(LogLevel.DEBUG, f"器官别名节点: {alias} → {organ_name}")

    def _create_call_chain_insight_nodes(self, organ_name: str):
        """
        ★v24.0新增：调用链延展思考节点。
        学习一个方法后，生成一条联想假设，体现举一反三的思维。
        """
        if not self.node_pool or not self.knowledge_tree:
            return

        # 从调用图中提取该器官的一个代表性方法及其被调用方法
        call_graph = getattr(self, '_code_call_graph', {})
        if not call_graph:
            return

        organ_methods = []
        for key, info in call_graph.items():
            if info.get("organ") == organ_name:
                organ_methods.append((key, info))

        if not organ_methods:
            return

        # 找一个有调用关系的方法作为样本
        sample_key, sample_info = None, None
        for key, info in organ_methods:
            if info.get("called_methods"):
                sample_key, sample_info = key, info
                break

        if not sample_key or not sample_info:
            return

        called = sample_info.get("called_methods", [])[:2]
        if not called:
            return

        method_short = sample_key.split(".")[-1]
        called_str = "、".join(called)

        insight_value = (
            f"[代码联想] {organ_name}.{method_short} 调用了 {called_str}。"
            f"这种调用模式可能与框架中其他器官的类似流程有共通之处，"
            f"例如先检查/过滤，再执行核心操作。"
        )
        insight_path = f"/自我理解/代码/联想/{organ_name}"
        if self.knowledge_tree:
            self.knowledge_tree.register_path(insight_path)

        _existing = self.node_pool.query(
            evol_level="L2", space_path_prefix=insight_path, limit=5
        )
        if _existing:
            for _en in _existing:
                if hasattr(_en, 'value'):
                    _en.value = insight_value
                    _en.trust_score = 75.0
            return

        _node = PulseNode(
            value=insight_value,
            keywords=[organ_name, "代码联想", "调用模式", "举一反三"],
            source_organ=self.organ_name,
            evol_level=PulseNode.EVOL_L2,
            importance=PulseNode.IMPORTANCE_A,
            abstraction=0.5,
            space_path=insight_path,
        )
        _node.view_mode = "INNER_VIEW"
        _node.trigger_reason = "self_understanding.call_chain_insight"
        _node.trust_score = 75.0
        if self.frequency_codec:
            self.frequency_codec.encode_node(_node)
        self.node_pool.add(_node)
        self._log(LogLevel.DEBUG, f"调用链联想节点: {organ_name}.{method_short}")

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        _progress = getattr(self, '_code_understanding_progress', {})
        # ★v17.0修复：已理解数直接从知识库统计，而非依赖内存变量
        _kb_understood = 0
        if self.node_pool:
            _code_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我理解/代码", limit=5000
            )
            for _n in _code_nodes:
                _val = str(_n.value) if _n.value else ""
                if _val.startswith("[自我理解·"):
                    _kb_understood += 1
        # 取内存计数和知识库计数的较大值
        _mem_understood = _progress.get("understood", 0)
        _effective_understood = max(_mem_understood, _kb_understood)
        # ★v17.0 F1修复：total_methods取pending中的全部方法数（含已完成和未完成），而非从_progress中取
        _pending_list = _progress.get("pending", [])
        _total_from_pending = len(_pending_list) if _pending_list else 0
        _total_from_progress = _progress.get("total_methods", 0)
        _effective_total = max(_total_from_pending, _total_from_progress)

        # ★v25.0修复：钳制understood不超过total，防止进度超过100%
        if _effective_total > 0:
            _effective_understood = min(_effective_understood, _effective_total)
        else:
            _effective_total = _effective_understood

        return {
            "organ": self.organ_name,
            "heartbeat_count": self._heartbeat_count,
            "understood": _effective_understood,
            "total_methods": _effective_total,
            "call_graph_size": len(getattr(self, '_code_call_graph', {})),
            "is_running": self.is_running,
        }


# ============ P2-26 辅助函数（模块级，便于单测，无副作用）============
def is_placeholder_alias(alias: str) -> bool:
    """识别未替换的占位符别名（如 '[器官别名]'）。

    代码学习曾把未解析的占位符字面量当作器官别名写入知识库，
    生成出 "[器官别名] [器官别名] 人格内核..." 这类异常节点。
    凡含方括号或为空/空白的别名均视为占位符残留，应跳过。
    """
    if not alias or not isinstance(alias, str):
        return True
    _a = alias.strip()
    if not _a:
        return True
    return "[" in _a or "]" in _a or _a == "器官别名"


def build_organ_alias_value(alias: str, organ_name: str, code_path: str) -> str:
    """构建器官别名知识节点内容（P2-26：去除占位符前缀）。

    原模板以字面量 '[器官别名]' 作为前缀标签，未按器官名替换。
    现改为用真实别名作为方括号标签，彻底消除占位符。
    """
    return (
        f"[{alias}]（{organ_name}）是框架中的仿生器官。"
        f"详细的代码结构分析请参考路径 {code_path}。"
    )


# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "代码学习",
    "class_name": "PulseCodeLearner",
    "attr_name": "code_learner",
    "system": "brain",
    "always_online": False,
    "feature_flag": "enable_code_learner",
    "extra_deps": {
        "node_pool": "node_pool",
        "frequency_codec": "frequency_codec",
        "knowledge_tree": "knowledge_tree",
    },
    "post_wiring": [
        {"target": "evolution_sandbox", "setter": "set_evolution_sandbox"},
        {"target": "self_inspector", "setter": "set_self_inspector"},
    ],
}
# _m69_t3b_codelearner
