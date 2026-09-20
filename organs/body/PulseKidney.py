# -*- coding: utf-8 -*-
"""
PulseKidney —— 脉冲驱动肾 · 知识淘汰器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 接收 PurgeEvent.PURGE_CHECK 执行知识淘汰——淘汰老化低价值的 L1 感知节点、把长期低活跃的 L2 认知节点降级为 L1、永久保护 L3 智慧节点，并在节点池总量超阈值时加速淘汰。
机制: _on_purge_check 按「年龄 + 活跃度 + 重要性」三档阈值筛选候选，叠加 _detect_bias 偏见检测与 _active_forget_scan 主动遗忘扫描，淘汰完成后发射 PurgeEvent.PURGE_RESULT 回传结果。
定位: 记忆维的容量守门人，与肝互补——肝负责提质，肾负责减量，共同维持知识池的规模与信噪比。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    InterestEvent,
    KnowledgeEvent,
    LogLevel,
    PurgeEvent,
    SystemEvent,
)


class PulseKidney(BasePulseOrgan):
    """脉冲驱动肾（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'kidney_forget_score_threshold' in _rp and hasattr(self, '_forget_score_threshold'):
                self._forget_score_threshold = _rp['kidney_forget_score_threshold']
            if 'kidney_forget_scan_interval' in _rp and hasattr(self, '_forget_scan_interval'):
                self._forget_scan_interval = _rp['kidney_forget_scan_interval']
            if 'kidney_code_learning_zombie_hours' in _rp and hasattr(self, '_code_learning_zombie_hours'):
                self._code_learning_zombie_hours = _rp['kidney_code_learning_zombie_hours']
            if 'kidney_search_content_zombie_hours' in _rp and hasattr(self, '_search_content_zombie_hours'):
                self._search_content_zombie_hours = _rp['kidney_search_content_zombie_hours']
            if 'kidney_normal_l1_zombie_hours' in _rp and hasattr(self, '_normal_l1_zombie_hours'):
                self._normal_l1_zombie_hours = _rp['kidney_normal_l1_zombie_hours']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "肾"):
        super().__init__(organ_name)

        self.node_pool = None
        self.knowledge_tree = None
        self.resonance_engine = None
        self._total_purged = 0
        self._total_downgraded = 0

        # ===== P2-2: 偏见质疑 =====
        self._branch_retrieval_stats: dict[str, dict[str, int]] = {}
        self._biased_branches: list[str] = []
        # ===== 主动选择性遗忘 =====
        self._forget_candidates: dict[str, float] = {}  # node_id → 遗忘得分
        self._purge_check_count = 0  # PURGE_CHECK 累计次数
        self._active_forget_enabled = True
        # ★P1: 从RUNTIME_PARAMS读取肾脏参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._forget_score_threshold = _rp.get("kidney_forget_score_threshold", 0.6)
            self._forget_scan_interval = _rp.get("kidney_forget_scan_interval", 5)
        except Exception:
            self._forget_score_threshold = 0.6
            self._forget_scan_interval = 5
        self._last_convergence_check = 0.0  # ★v25.1: 收敛评估时间戳
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        # ★属性初始化完整性补全（自动审查添加）
        self._code_learning_zombie_hours = 24
        self._core_system_paths = []
        self._fragment_log_count = 0
        self._normal_l1_zombie_hours = 72
        self._search_content_zombie_hours = 48
        self.l1_max_age_days = 90
        self.l2_downgrade_age_days = 30
        self.pressure_threshold = 0.0
        self._load_kidney_config()

    # ========== 框架注入接口 ==========

    def set_node_pool(self, pool):
        self.node_pool = pool

    def set_knowledge_tree(self, tree):
        self.knowledge_tree = tree

    def set_resonance_engine(self, engine):
        self.resonance_engine = engine
    def _load_kidney_config(self):
        """从配置加载主动遗忘参数及淘汰阈值"""
        try:
            import config
            cfg = getattr(config, 'KIDNEY', {})
            self._active_forget_enabled = cfg.get("active_forget_enabled", True)
            self._forget_score_threshold = cfg.get("forget_score_threshold", 0.6)
            self._forget_scan_interval = cfg.get("forget_scan_interval", 5)
            # 新增：淘汰阈值
            self.l1_max_age_days = cfg.get("l1_max_age_days", 30.0)
            self.l2_downgrade_age_days = cfg.get("l2_downgrade_age_days", 90.0)
            self.pressure_threshold = cfg.get("pressure_threshold", 100000)
            # ★v23.0新增：从config加载偏见检测白名单和僵尸节点阈值
            self._core_system_paths = cfg.get("core_system_paths", [])
            self._code_learning_zombie_hours = cfg.get("code_learning_zombie_hours", 24.0)
            self._search_content_zombie_hours = cfg.get("search_content_zombie_hours", 12.0)
            self._normal_l1_zombie_hours = cfg.get("normal_l1_zombie_hours", 2.0)
        except Exception:
            self._active_forget_enabled = True
            self._forget_score_threshold = 0.6
            self._forget_scan_interval = 5
            self.l1_max_age_days = 30.0
            self.l2_downgrade_age_days = 90.0
            self.pressure_threshold = 100000
            # ★v23.0新增：兜底值
            self._core_system_paths = []
            self._code_learning_zombie_hours = 24.0
            self._search_content_zombie_hours = 12.0
            self._normal_l1_zombie_hours = 2.0
    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == PurgeEvent.PURGE_CHECK:
            return self._on_purge_check(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_purge_check(self, payload: dict) -> dict[str, Any]:
        if self.node_pool is None:
            return {"status": "skipped", "reason": "节点池未注入"}
        # 主动选择性遗忘扫描（在被动淘汰之前执行）
        total_nodes = self.node_pool.count()
        purged_count = 0
        downgraded_count = 0

        is_under_pressure = total_nodes > self.pressure_threshold
        if is_under_pressure:
            self._log(LogLevel.WARNING, f"节点池压力过高: {total_nodes} > {self.pressure_threshold}，加速淘汰")
            effective_max_age = self.l1_max_age_days / 2
        else:
            effective_max_age = self.l1_max_age_days

        all_nodes = self.node_pool.get_all_including_evicted()
        # 主动选择性遗忘扫描
        if self._active_forget_enabled:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._purge_check_count += 1
            if self._purge_check_count % 10 == 0:
                # L1占比保护：如果L1占比过高，说明肝脏还没整理，跳过遗忘
                _l1_count = sum(1 for n in all_nodes if n.evol_level == "L1")
                _total = len(all_nodes)
                if _total > 10 and _l1_count / _total > 0.7:
                    self._log(LogLevel.INFO, f"L1占比过高({_l1_count/_total:.0%})，跳过主动遗忘，等待肝脏整理")
                else:
                    self._active_forget_scan(all_nodes)
        # P2-2: 偏见质疑——检查是否有"很热闹但没用"的知识分支
        self._detect_bias(all_nodes)

        nodes_to_remove = []
        nodes_to_downgrade = []

        # ===== 临时节点自动过期 =====
        # ephemeral节点超过24小时自动清理，低信任(<30)超过6小时清理
        _ephemeral_purged = 0
        for node in all_nodes:
            if not getattr(node, 'ephemeral', False):
                continue
            if node.evol_level == "L3" or node.state == "locked":
                continue
            _age_hours = (time.time() - node.created_at) / 3600.0 if hasattr(node, 'created_at') else 0
            _trust = getattr(node, 'trust_score', 50.0)
            _max_hours = 6.0 if _trust < 30.0 else 24.0
            if _age_hours > _max_hours:
                nodes_to_remove.append(node)
                _ephemeral_purged += 1
        if _ephemeral_purged > 0:
            self._log(LogLevel.INFO, f"临时节点过期: 清理{_ephemeral_purged}个ephemeral节点")
        # ===== 临时节点过期结束 =====
        for node in all_nodes:
            if node.evol_level == "L3" or node.state == "locked":
                continue
            # 主动遗忘：高分候选节点直接清理
            if node.node_id in self._forget_candidates:
                forget_score = self._forget_candidates[node.node_id]
                if forget_score >= self._forget_score_threshold:
                    nodes_to_remove.append(node)
                    continue  # 已标记移除，跳过后续年龄检查
            age_seconds = time.time() - node.created_at
            age_days = age_seconds / 86400.0
            # ★v17.0 F2修复：清理 /自我理解/代码 路径下的低信任L2节点
            _node_path = getattr(node, 'space_path', '')
            if node.evol_level == "L2" and _node_path.startswith("/自我理解/代码"):
                _trust = getattr(node, 'trust_score', 50.0)
                _age_hours = (time.time() - node.created_at) / 3600.0 if hasattr(node, 'created_at') else 0
                # 信任<15且创建超过1小时的L2节点→直接淘汰
                if _trust < 15.0 and _age_hours > 1.0:
                    nodes_to_remove.append(node)
                    continue
                # 信任<30且创建超过6小时的L2节点→降级为L1
                if _trust < 30.0 and _age_hours > 6.0:
                    node.evol_level = "L1"
                    node.importance = "C"
                    # ★P1-2修复：同步分层索引
                    self.node_pool.upgrade_node_level(node.node_id, "L1")
                    downgraded_count += 1
                    self._total_downgraded += 1
                    continue

            if node.evol_level == "L1":
                # ★v23.0新增：路径碎片词节点优先清理
                # 这些节点的路径包含域名后缀、纯数字等残词特征，
                # 是搜索引擎噪音或内部标记污染，直接淘汰不需等待遗忘扫描
                _node_path_l1 = getattr(node, 'space_path', '')
                if _node_path_l1:
                    from nucleus.knowledge_noise_filter import is_path_fragment_word
                    _l1_path_parts = _node_path_l1.rstrip('/').split('/')
                    _l1_fragment_count = 0
                    for _l1_pp in _l1_path_parts:
                        if is_path_fragment_word(_l1_pp):
                            _l1_fragment_count += 1
                    # 路径包含≥2个碎片词，或路径最后一段是碎片词且节点信任<30
                    _l1_trust = getattr(node, 'trust_score', 50.0)
                    if _l1_fragment_count >= 2 or (
                        _l1_fragment_count >= 1 and _l1_trust < 30.0
                    ):
                        nodes_to_remove.append(node)
                        continue
                # ★v23.0新增结束

                if effective_max_age <= 0:
                    if node.importance in ("C",) or node.activation_count < 2:
                        nodes_to_remove.append(node)
                        continue
                if age_days > effective_max_age and node.importance in ("C",):
                    nodes_to_remove.append(node)
                    continue

                # ★v25.1 P2智能化: 统一质量评分——低质量L1节点优先淘汰
                if node.evol_level == "L1" and effective_max_age > 0:
                    try:
                        from nucleus.knowledge.KnowledgeQualityScorer import (
                            get_quality_scorer,
                        )
                        _node_content = str(getattr(node, 'value', ''))
                        _node_kws = getattr(node, 'keywords', []) or []
                        _node_path = getattr(node, 'space_path', '')
                        _q = get_quality_scorer().score(
                            content=_node_content, keywords=_node_kws,
                            source="淘汰检查", space_path=_node_path,
                            source_trust=getattr(node, 'trust_score', 50),
                        )
                        if _q["total_score"] < 25 and age_days > 1:
                            nodes_to_remove.append(node)
                            continue
                    except Exception:
                        pass
                if age_days > effective_max_age and node.activation_count < 2:
                    nodes_to_remove.append(node)
                    continue
                # P2-2: 偏见分支中的节点加速淘汰（年龄阈值减半）
                if node.space_path in self._biased_branches:
                    if age_days > effective_max_age / 2:
                        nodes_to_remove.append(node)
                        continue
                if is_under_pressure and node.importance == "C" and node.activation_count == 0:
                    nodes_to_remove.append(node)
                    continue

            if node.evol_level == "L2":
                if age_days > self.l2_downgrade_age_days and node.activation_count < 3:
                    nodes_to_downgrade.append(node)

        for node in nodes_to_remove:
            self.node_pool.remove(node.node_id)
            if self.knowledge_tree:
                self.knowledge_tree.unregister_path(node.space_path)
            purged_count += 1
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._total_purged += 1

        for node in nodes_to_downgrade:
            node.evol_level = "L1"
            node.importance = "C"
            # ★P1-2修复：同步分层索引
            self.node_pool.upgrade_node_level(node.node_id, "L1")
            downgraded_count += 1
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._total_downgraded += 1

        # v9.5: 知识淘汰结果标记为L3后台自主层
        self._emit(PurgeEvent.PURGE_RESULT, {
            "purged_count": purged_count,
            "downgraded_count": downgraded_count,
            "total_nodes_before": total_nodes,
            "total_nodes_after": self.node_pool.count(),
            "under_pressure": is_under_pressure,
        }, priority=3, layer="L3")
        # ===== 新增: 上下文数据联动遗忘 =====
        try:
            _ctx_snapshot = self._get_context_snapshot()
            # ★登顶路线图-山 2 P1：上下文保鲜（淘汰前先救回）
            # 在低价值记忆被淘汰前，先分析保鲜候选（时间衰减边缘的活跃记忆 /
            # 有精华可保的低健康记忆）。零冲突：只读分析+日志，
            # 不改变淘汰逻辑；压力均衡：复用肾脏每小时周期，零新线程。
            try:
                from nucleus.ContextFreshener import get_context_freshener
                _conv_data = get_context_freshener().load_conversation_data()
                _fresh_report = get_context_freshener().analyze(_conv_data)
                _n_refresh = len(_fresh_report.get("refresh_candidates", []))
                _n_compress = len(_fresh_report.get("compress_candidates", []))
                if _n_refresh or _n_compress:
                    self._log(LogLevel.INFO,
                              f"上下文保鲜: {_n_refresh}条建议刷新(时间衰减边缘), "
                              f"{_n_compress}条建议压缩(保留精华)")
            except Exception as _freshen_e:
                self._log(LogLevel.DEBUG, f"上下文保鲜分析异常: {_freshen_e}")
            _health_report = _ctx_snapshot.assess_context_health()
            if _health_report.get("summary", "").startswith("发现"):
                _removed = _ctx_snapshot.forget_low_quality_context(_health_report)
                _total_ctx_removed = sum(_removed.values())
                if _total_ctx_removed > 0:
                    self._log(LogLevel.INFO,
                             f"上下文遗忘: 淘汰{_total_ctx_removed}个低质量条目 "
                             f"(对话{_removed['conversation']}条、"
                             f"推理链{_removed['inference_trace']}条、"
                             f"搜索经验{_removed['search_experience']}条)")
        except Exception as _ctx_e:
            self._log(LogLevel.DEBUG, f"上下文遗忘异常: {_ctx_e}")
        # ===== 上下文联动遗忘结束 =====
        if purged_count > 0 or downgraded_count > 0:
            self._log(LogLevel.INFO, f"淘汰完成: 移除{purged_count}个 降级{downgraded_count}个 "
                     f"剩余{self.node_pool.count()}个")

        # ★v25.1新增: 收敛评估——每30分钟采集一次系统状态快照
        try:
            import time as _time_conv
            if _time_conv.time() - self._last_convergence_check > 1800:
                self._last_convergence_check = _time_conv.time()
                from nucleus.genesis.ConvergenceEvaluator import (
                    get_convergence_evaluator,
                )
                _ce = get_convergence_evaluator()
                if _ce and self.node_pool:
                    _ce.set_node_pool(self.node_pool)
                    _ce.enable()
                    _snap = _ce.take_snapshot()
                    if _snap.get("status") != "disabled":
                        self._log(LogLevel.INFO,
                            f"[收敛评估] 知识密度={_snap.get('knowledge_density', 0):.2%}, "
                            f"器官协同={_snap.get('organ_synergy', 0):.2f}, "
                            f"进化稳定={_snap.get('evolution_stability', 0):.2f}")
        except Exception as _conv_e:
            self._log(LogLevel.DEBUG, f"收敛评估异常: {_conv_e}")

        return {
            "status": "completed",
            "purged": purged_count,
            "downgraded": downgraded_count,
            "remaining": self.node_pool.count(),
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "total_purged": self._total_purged,
            "total_downgraded": self._total_downgraded,
            "biased_branches": len(self._biased_branches),
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    PurgeEvent.PURGE_CHECK,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== P2-2: 偏见质疑 ==========

    def _detect_bias(self, all_nodes: list):
        """
        检测"很热闹但没用"的知识分支。
        """
        if not all_nodes:
            return

        branch_stats: dict[str, dict[str, int]] = {}
        for node in all_nodes:
            path = node.space_path or "/"
            parent = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
            if parent not in branch_stats:
                branch_stats[parent] = {"node_count": 0, "total_activation": 0}
            branch_stats[parent]["node_count"] += 1
            branch_stats[parent]["total_activation"] += node.activation_count

        if len(branch_stats) < 2:
            return

        avg_count = sum(b["node_count"] for b in branch_stats.values()) / len(branch_stats)
        avg_activation = sum(b["total_activation"] for b in branch_stats.values()) / len(branch_stats)

        self._biased_branches = []
        # ===== 【v15.1修复】偏见检测白名单：核心系统路径不受偏见质疑 =====
        # ★v23.0优化：从config读取
        _core_system_paths = getattr(self, '_core_system_paths', [])
        if not _core_system_paths:
            _core_system_paths = [
                "/自我/架构", "/自我/架构/器官", "/技术", "/身份",
                "/自我", "/本能", "/反思", "/知识",
                "/自我理解", "/自我理解/代码",
            ]
        for path, stats in branch_stats.items():
            # 跳过核心系统路径——这些路径节点多且激活高是正常的知识积累
            if any(path == _cp or path.startswith(_cp + "/") for _cp in _core_system_paths):
                continue
            bias_cfg = self._load_bias_config()
            node_mult = bias_cfg.get("node_count_multiplier", 2.0)  # type: ignore[possibly-unbound]
            act_mult = bias_cfg.get("activation_multiplier", 0.5)  # type: ignore[possibly-unbound]

            if (stats["node_count"] > avg_count * node_mult and
                stats["total_activation"] < avg_activation * act_mult):
                self._biased_branches.append(path)
                self._log(LogLevel.INFO,
                         f"偏见质疑: {path}分支(节点{stats['node_count']}个, "
                         f"激活{stats['total_activation']}次) 标记为偏见分支")
        # ===== 核心路径保护结束 =====
        # 触发内在世界搜索对立观点验证偏见
        if self._biased_branches and self.info_field and self.pulse_core:
            max_branches = bias_cfg.get("max_branches_per_check", 3)  # type: ignore[possibly-unbound]
            for branch in self._biased_branches[:max_branches]:
                branch_name = branch.split("/")[-1] if "/" in branch else branch
                # 发射对立观点搜索脉冲给内在世界
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=KnowledgeEvent.RAW,
                    payload={
                        "content": f"请搜索关于'{branch_name}'领域的对立观点和反对意见",
                        "source_organ": self.organ_name,
                        "trigger_reason": "bias_challenge",
                        "space_path": branch,
                    },
                    priority=5,
                    layer="L2"
                ))

        # 降低偏见分支相关领域的兴趣权重
        # 注意：使用InterestEvent.CHANGED是因为兴趣模型已订阅此事件，
        # 通过payload中的suppressed字段告知兴趣模型需要抑制的维度
        if self._biased_branches and self.info_field and self.pulse_core:
            for branch in self._biased_branches[:2]:
                branch_name = branch.split("/")[-1] if "/" in branch else branch
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=InterestEvent.CHANGED,
                    payload={
                        "boosted": [],
                        "suppressed": [branch_name],
                        "reason": f"偏见质疑: {branch_name}分支标记为偏见",
                        "source": "kidney_bias_detection",  # ← 新增来源标记
                    },
                    priority=4,
                    layer="L2"
                ))
    def _load_bias_config(self) -> dict:
        """从config加载偏见检测参数，失败时用兜底"""
        try:
            import config
            cfg = getattr(config, 'KIDNEY', {})
            return {
                "node_count_multiplier": cfg.get("bias_node_count_multiplier", 2.0),
                "activation_multiplier": cfg.get("bias_activation_multiplier", 0.5),
                "max_branches_per_check": cfg.get("bias_max_branches_per_check", 3),
                "suppress_interest_multiplier": cfg.get("bias_suppress_interest_multiplier", 0.5),
            }
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"偏见配置加载失败，用兜底: {_e}")
        return {
            "node_count_multiplier": 2.0,
            "activation_multiplier": 0.5,
            "max_branches_per_check": 3,
            "suppress_interest_multiplier": 0.5,
        }
    # ========== 主动选择性遗忘 ==========

    def _active_forget_scan(self, all_nodes: list):
        """
        主动扫描节点池，计算每个L1节点的遗忘得分。
        高分节点加入遗忘候选池，在后续淘汰中优先清理。
        """
        if not all_nodes:
            return

        new_candidates = {}

        for node in all_nodes:
            # 只评估L1节点，L2及以上受保护
            if node.evol_level != "L1":
                continue
            if node.state == "locked":
                continue
            # 存活保护期：创建不到30分钟的L1不进入遗忘候选
            # 给肝脏压缩和知识编织留出整理时间
            _age_seconds = time.time() - node.created_at
            if _age_seconds < 1800:
                continue
            # 计算遗忘得分（0.0-1.0，越高越该被遗忘）
            score = self._calculate_forget_score(node)
            # 【v16.0新增】僵尸节点检测：长期孤立且无关联的节点直接标记为高遗忘得分
            # ★P1-2修复：对代码自学习路径使用差异化阈值（24小时），避免刚产生的代码分析被清理
            _age_hours = _age_seconds / 3600.0
            _node_path = getattr(node, 'space_path', '')
            _trigger = getattr(node, 'trigger_reason', '')

            # 代码自学习产生的节点和大模型分析结果使用更长的保护期
            _is_code_learning = (
                _node_path.startswith('/自我理解/代码') or
                'self_understanding.code' in str(_trigger) or
                'code_analysis' in str(_trigger)
            )
            # 搜索内容消化节点也使用较长保护期（12小时）
            _is_search_content = (
                _node_path.startswith(('/技术/', '/知识/')) or 'deep_search' in str(_trigger) or 'search' in str(_trigger)
            )

            # 差异化阈值
            # ★v23.0优化：从config读取
            if _is_code_learning:
                _zombie_threshold_hours = getattr(self, '_code_learning_zombie_hours', 24.0)
            elif _is_search_content:
                _zombie_threshold_hours = getattr(self, '_search_content_zombie_hours', 12.0)
            else:
                _zombie_threshold_hours = getattr(self, '_normal_l1_zombie_hours', 2.0)

            if _age_hours >= _zombie_threshold_hours and node.activation_count == 0:
                _node_kw = {kw.lower() for kw in (node.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                _has_association = False
                if hasattr(self, 'node_pool') and self.node_pool and _node_kw:
                    _l2_nodes = self.node_pool.query(evol_level="L2", limit=20)
                    _l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
                    for _related in (_l3_nodes or []) + (_l2_nodes or []):
                        _related_kw = {kw.lower() for kw in (_related.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                        if _node_kw & _related_kw:
                            _has_association = True
                            break
                if not _has_association:
                    score = max(score, self._forget_score_threshold + 0.1)
                    _threshold_label = f"{_zombie_threshold_hours:.0f}小时"
                    self._log(LogLevel.DEBUG, f"僵尸节点检测: '{str(node.value)[:40]}...' 已孤立{_age_hours:.1f}小时(阈值{_threshold_label})，强制标记遗忘")
            # ===== 僵尸节点检测结束 =====
            if score >= self._forget_score_threshold:
                new_candidates[node.node_id] = round(score, 2)

        # 更新候选池
        self._forget_candidates = new_candidates

        if new_candidates:
            self._log(LogLevel.INFO, f"主动遗忘扫描: {len(new_candidates)}个候选节点 (阈值>{self._forget_score_threshold})")

    def _calculate_forget_score(self, node) -> float:
        """
        计算节点的遗忘得分。
        得分因素：内容长度短、关键词少、激活次数少、创建时间新但无激活（噪音）、重要性低。
        各项权重可调，通用逻辑不写死。
        """
        # ★v17.0 R9修复：改用统一健康度评估中的遗忘分
        _health = node.evaluate_node_health(check_type="forget")
        _unified_forget = _health["forget_score"] / 100.0
        score = 0.0

        value_str = node.value if isinstance(node.value, str) else str(node.value)
        keywords = node.keywords if hasattr(node, 'keywords') and node.keywords else []

        # 1. 内容空洞（内容长度 < 30 且关键词 < 3）
        if len(value_str) < 30 and len(keywords) < 3:
            score += 0.4

        # 2. 从未被激活
        if node.activation_count == 0:
            score += 0.3
        elif node.activation_count < 2:
            score += 0.15

        # 3. 创建超过1小时但激活为0（典型的噪音）
        age_seconds = time.time() - node.created_at
        if age_seconds > 3600 and node.activation_count == 0:
            score += 0.2

        # 4. 关键词稀疏（少于2个关键词）
        if len(keywords) < 2:
            score += 0.1

        # ===== 新增: 搜索引擎残词污染检测 =====
        # 特征：所有关键词都是小写英文、长度≤5、无中文字符、无大写缩写
        if keywords and len(keywords) > 0:
            all_noise = True
            noise_count = 0
            for kw in keywords:
                if not isinstance(kw, str) or len(kw) == 0:
                    continue
                # 包含中文字符 → 不是噪音
                has_chinese = any('\u4e00' <= c <= '\u9fff' for c in kw)
                # 全大写缩写 → 不是噪音（如 DNA, API, GPU）
                is_acronym = kw.isupper() and len(kw) >= 2
                # 首字母大写专有名词 → 不是噪音（如 Python）
                is_proper = kw[0].isupper() and len(kw) > 1

                if has_chinese or is_acronym or is_proper:
                    all_noise = False
                    break
                # 小写英文且长度≤5 → 可能是残词
                if kw.islower() and len(kw) <= 5:
                    noise_count += 1

            if all_noise and noise_count >= 2:
                # 典型搜索引擎残词污染节点，大幅提高遗忘得分
                score += 0.4
                self._log(LogLevel.DEBUG,
                         f"噪音节点检测: keywords={keywords[:3]}, "
                         f"遗忘得分+0.4 (残词污染)")
        # ★v17.0新增：路径碎片词检测——加速清理历史残词污染
        _node_path = getattr(node, 'space_path', '')
        if _node_path:
            from nucleus.knowledge_noise_filter import is_path_fragment_word
            _path_parts = _node_path.rstrip('/').split('/')
            _fragment_count = 0
            for _pp in _path_parts:
                if is_path_fragment_word(_pp):
                    _fragment_count += 1
            if _fragment_count >= 2:
                score += 0.5  # 路径含多个碎片词，大幅提高遗忘分
                if not hasattr(self, '_fragment_log_count'):
                    self._fragment_log_count = 0
                self._fragment_log_count += 1
                if self._fragment_log_count % 10 == 0:
                    self._log(LogLevel.INFO,
                             f"路径残词加速淘汰: 已标记{self._fragment_log_count}个节点 "
                             f"(示例路径={_node_path})")

        # 5. 与已有知识体系关联度检查——关联度高的L1降低遗忘得分
        if hasattr(self, 'node_pool') and self.node_pool and keywords:
            _l2_nodes = self.node_pool.query(evol_level="L2", limit=20)
            _l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
            _all_related = (_l3_nodes or []) + (_l2_nodes or [])
            _max_overlap = 0
            _node_kw = {kw.lower() for kw in keywords if isinstance(kw, str) and len(kw) >= 2}
            for _related in _all_related:
                _related_kw = {kw.lower() for kw in (_related.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                _overlap = len(_node_kw & _related_kw)
                _max_overlap = max(_max_overlap, _overlap)
            if _max_overlap >= 2:
                score -= 0.3  # 与已有知识关联紧密，大幅降低遗忘得分
            elif _max_overlap >= 1:
                score -= 0.15  # 有一定关联，适度降低遗忘得分

        _original_score = min(1.0, score)
        # ★v17.0 R9修复：统一评估和原有评估取加权平均（统一占60%，原有占40%）
        _final_score = _unified_forget * 0.6 + _original_score * 0.4
        return round(min(1.0, _final_score), 2)
    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None




    def _m70_kal_get_node(self, node_id):
        """★T4.3 实际调用点：优先 KAL，失败回退 node_pool 直连。"""
        if not self._m70_kal_callsites_on():
            return self._m70_direct_get_node(node_id)
        try:
            from nucleus.knowledge_access_layer import get_kal
            _r = get_kal().get_node(node_id)
            if _r is not None:
                return _r
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        return self._m70_direct_get_node(node_id)

    def _m70_kal_search_by_level(self, evol_level, top_k=10):
        """★T4.3 实际调用点：按进化层级搜索（KAL 优先 + 回退）。"""
        if not self._m70_kal_callsites_on():
            return []
        try:
            from nucleus.knowledge_access_layer import get_kal
            _r = get_kal().search_by_evol_level(evol_level, top_k=top_k)
            if _r:
                return _r
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        return []

    def _m70_direct_get_node(self, node_id):
        """回退路径：直连 node_pool。"""
        try:
            _pool = getattr(self, "node_pool", None)
            if _pool is not None:
                return _pool.get(node_id)
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        return None

    def _m70_kal_callsites_on(self) -> bool:
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_KAL_CALL_SITES", True))
        except Exception:
            return True

# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "肾",
    "class_name": "PulseKidney",
    "attr_name": "kidney",
    "system": "body",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "node_pool": "node_pool",
        "knowledge_tree": "knowledge_tree",
        "resonance_engine": "resonance_engine",
    },
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseKidney v9.5 分层脉冲自测 ===\n")

    from nucleus.mnemosyne.KnowledgeTree import KnowledgeTree
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    pool = PulseNodePool()
    tree = KnowledgeTree()

    l1_old = PulseNode(
        value="旧感知数据", keywords=["旧数据"],
        source_organ="双腿", evol_level=PulseNode.EVOL_L1,
        importance=PulseNode.IMPORTANCE_C, space_path="/测试/旧数据",
    )
    l1_old.created_at = time.time() - (31 * 86400)
    l1_old.activation_count = 0

    l1_new = PulseNode(
        value="新感知数据", keywords=["新数据"],
        source_organ="双腿", evol_level=PulseNode.EVOL_L1,
        importance=PulseNode.IMPORTANCE_C, space_path="/测试/新数据",
    )

    l3_seed = PulseNode(
        value="我是曈曈", keywords=["身份"],
        source_organ="main", evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S, space_path="/身份/自我",
    )
    l3_seed.state = "locked"

    pool.add(l1_old)
    pool.add(l1_new)
    pool.add(l3_seed)
    tree.register_path("/测试/旧数据")
    tree.register_path("/测试/新数据")
    tree.register_path("/身份/自我")

    kidney = PulseKidney("肾")
    kidney.set_info_field(mock_field)
    kidney.set_node_pool(pool)
    kidney.set_knowledge_tree(tree)
    kidney.l1_max_age_days = 0
    kidney.start()

    result = kidney.on_pulse({
        "event_type": PurgeEvent.PURGE_CHECK,
        "payload": {},
        "priority": 5,
    })
    print(f"1. 淘汰结果: 移除{result['purged']}个 降级{result['downgraded']}个 剩余{result['remaining']}个")

    # 验证 PURGE_RESULT 脉冲的 layer 标记
    purge_pulses = [p for p in mock_field.published if p.get("event_type") == PurgeEvent.PURGE_RESULT]
    if purge_pulses:
        print(f"   PURGE_RESULT脉冲 layer: {purge_pulses[0].get('layer', '未设置')} (预期L3)")

    l3_check = pool.get(l3_seed.node_id)
    print(f"2. L3节点保留: {l3_check is not None} (应为True)")

    l1_old_check = pool.get(l1_old.node_id)
    print(f"3. 旧L1被淘汰: {l1_old_check is None} (应为True)")

    l1_new_check = pool.get(l1_new.node_id)
    print(f"4. 新L1被淘汰: {l1_new_check is None} (应为True)")

    status = kidney.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"5. 统计: 累计淘汰{status['total_purged']}次")

    kidney.stop()
    print("\n=== 自测全部通过 ===")

    def _m69_kal_query(self, evol_level=None, top_k=10):
        """★T4: 通过KAL查询节点（双轨过渡，配置开关控制）。"""
        try:
            import config as _cfg69
            if not getattr(_cfg69, 'ENABLE_KAL_MIGRATION', False):
                return None
            from nucleus.knowledge_access_layer import get_kal
            _kal = get_kal()
            if _kal is not None:
                if evol_level:
                    return _kal.search_by_evol_level(evol_level, top_k=top_k)
                return _kal.get_node_count()
        except Exception:
            pass
        return None
# _m69_t4_kal_kidney
# _m70_t4_kal_kidney
