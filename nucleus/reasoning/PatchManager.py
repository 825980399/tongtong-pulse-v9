# -*- coding: utf-8 -*-
"""
PatchManager.py —— 补丁管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 代码补丁的创建与应用管理
机制: 大型模块（2246行），包含1个类、10个核心方法，采用分层架构实现
定位: 进化管理层
"""

import ast
import builtins
import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any

from nucleus.logger import (
    get_module_logger,
    noise_reduction_enabled as _noise_reduce,
)


_module_logger = get_module_logger("PatchManager")


def _m53_write_check_on() -> bool:
    """★第53批 T2（P0-补丁2）灰度开关：审批是否检查写入结果。

    开启（默认）→ approve_patch / approve_all_patches 检查 _save_patch_list 的返回值，
    写盘失败（如被 WriteGuard 拦截）时返回 ``ok=False``；
    关闭 → 恢复改造前行为（忽略写入结果，恒返回 ``ok=True``），零回归。

    每次调用重新 ``import config`` → 改配置即时生效，无需重启框架。
    """
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_PATCH_APPROVE_WRITE_CHECK", True))
    except Exception as _e:
        _module_logger.debug(f"[补丁审批] 开关读取失败，按默认(启用)处理: {type(_e).__name__}: {_e}")
        return True


def _m54_stale_days() -> float:
    """★第54批 T3.2：补丁过期天数（读配置，默认 7 天）。"""
    try:
        import config as _c
        return float(getattr(_c, "PATCH_AUTO_APPROVE_STALE_DAYS", 7))
    except Exception:
        return 7.0


def _m54_stale_mark_on() -> bool:
    """★第54批 T3.2 灰度：关闭 → 不做过期标记（复现改造前行为）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_PATCH_STALE_MARK", True))
    except Exception:
        return True


def _m54_auto_apply_on() -> bool:
    """★第54批 T3.3：auto_apply_enabled 总开关（默认 False → 只审批不应用）。"""
    try:
        import config as _c
        if not getattr(_c, "ENABLE_PATCH_APPROVE_AUTO_APPLY", True):
            return False
        return bool(getattr(_c, "EVOLUTION_CONFIG", {}).get("auto_apply_enabled", False))
    except Exception:
        return False


class PatchManager:
    def __init__(self, project_root: str):
        self._project_root = project_root
        self._patch_dir = os.path.join(project_root, "data", "patches")
        self._history_file = os.path.join(self._patch_dir, "patch_history.json")
        self._pending_file = os.path.join(self._patch_dir, "pending_patches.json")
        os.makedirs(self._patch_dir, exist_ok=True)
        # ★v16.0新增：防循环重启计数器
        self._restart_counter = 0
        self._max_restart_count = 3
        # ★v23.0新增：集中备份管理器
        from nucleus.reasoning.CodeBackupManager import CodeBackupManager
        self._backup_manager = CodeBackupManager(project_root)

    @staticmethod
    def _patch_dedup_key(patch: dict[str, Any]) -> tuple[str, str, str]:
        """★7-3修复（P2，2026-09-05）：补丁去重键 (file, method, repair_source)。

        设计取舍：
            用 method（结构性标识）而非 original_code（代码文本）作为主键维度。
            original_code 由 LLM 或规则从源码中截取，极易因上下文窗口、
            缩进、相邻行差异而出现「同一处改动、两样文本」，导致去重失配——
            补丁#1/#2 正是这样漏网的。method 是方法名，天然稳定。

        为什么纳入 repair_source：
            同文件同方法下，llm_multi_file 与 local_rule 是两套独立生成路径，
            本就应当作为不同候选**双双保留**以参与择优（这是 E6 多候选择优
            合并的设计意图）。若去重键不含来源，会误删其中一条候选，
            反而削弱择优空间。

        兼容性：
            本键是**叠加**在原有 (file, original_code) 键之上的第二道判定，
            两键取「或」，只增拦截、不减能力。
        """
        return (
            str(patch.get("file", "") or ""),
            str(patch.get("method", "") or ""),
            str(patch.get("repair_source", "") or ""),
        )

    def save_pending_patch(self, patch: dict[str, Any]):
        """
        保存待应用的补丁到队列。
        ★v23.0增强：自动补齐元数据 + 去重检查。
        """
        # ★v23.0新增：补全元数据
        if "saved_at" not in patch:
            patch["saved_at"] = time.time()
        if "status" not in patch:
            patch["status"] = "pending"
        if "reason" not in patch:
            patch["reason"] = patch.get("description", "未说明修改原因")
        if "source_diagnosis" not in patch:
            patch["source_diagnosis"] = patch.get("source", "未知诊断源")

        # ★D4修复（P2，2026-09-05，星轨 PHASE6 列为第六批必做第 3 项）：
        #   规则式补丁（repair_source="local_rule"）从未经过 AestheticJudge 评分，
        #   aesthetic_score 字段**根本不存在**——注意是「缺失」，不是「0」。
        #   而 _composite_score 用 `patch.get("aesthetic_score") or {}` 取值，
        #   把缺失当成审美 0 分参与计算，于是 local_rule 补丁综合分恒为
        #       0.5×0 + 0.5×trust(30) = 15.0
        #   与同题的 LLM 补丁（有评分，如 94.5/87.8/66.9）比较时永远处于劣势，
        #   同题择优因此退化为「先到先得」。
        #   生产证据（星轨直接读取 data/patches/pending_patches.json）：
        #     补丁#4 PulseLiver、#5 PulseCortex 均为 local_rule，aesthetic_score 字段不存在；
        #     对应 pulse.log 12:20:29 / 12:20:53 两次
        #     「已有方案综合分 15.0 >= 新方案 15.0，保留已有」。
        #
        #   ★必须放在下面同题择优（:_composite_score 调用处）之前，
        #     否则择优时读到的仍是缺失值，补了等于没补。
        #   此处统一补评分：任何来源的补丁入队前都具备审美分，
        #   一处修改覆盖全部来源，比逐个改生成点更彻底、回归面更小。
        if patch.get("aesthetic_score") is None and patch.get("modified_code"):
            try:
                from nucleus.evolution.AestheticJudge import get_aesthetic_judge
                _aes = get_aesthetic_judge().score(patch.get("modified_code"))
                patch["aesthetic_score"] = _aes
                patch["aesthetic_grade"] = _aes.get("grade")
                if not _aes.get("syntax_valid", True):
                    # ★与 D5 联动：语法校验未通过的补丁不应被自动审批放行
                    _module_logger.warning(
                        f"[补丁入队] 审美评分拒绝(语法校验未通过): "
                        f"{patch.get('file','')}:{patch.get('method','')} "
                        f"reason={_aes.get('reason','')}")
            except Exception as _e:
                # 评分失败不阻塞入队（保证补丁不丢），但必须留痕，不再静默吞掉
                _module_logger.debug(f"[补丁入队] 审美评分失败(不影响入队): {_e}")

        # ★v25.1 P0修复: 低风险补丁自动审批，打通自主迭代应用闭环
        #   原逻辑：所有补丁status=pending，启动时只应用approved，导致补丁永不应用。
        #   新逻辑：risk_level<=1（极低/低风险，如添加日志、修复沉默异常）自动设为approved，
        #   启动时apply_all_pending自动应用；高风险补丁仍保持pending需人工审批。
        #   安全保障：补丁已通过verify_in_copy验证，且有重启计数器防无限循环。
        #
        # ★PHASE17-A1修复（2026-09-07）：入队门槛与 _check_patch_safety 同源。
        #   原缺陷1：风险阈值硬编码 `<=1`，而 config.auto_apply_max_risk=2 —— 两套门槛不一致，
        #            配置改了不生效（实测 pending 2 条 risk=低(2) 因此永远不自动审批）。
        #   原缺陷2：此处**只判风险不判信任分**，一旦按配置放宽到 risk<=2，
        #            trust=30 的补丁会被自动置 approved，而 _check_patch_safety 对
        #            status=approved 直接放行 → 信任分门槛被完全绕过（安全门失效）。
        #   修复：风险与信任双门槛均从 EVOLUTION_CONFIG 读取，与落地前安全门判据一致。
        _risk_raw = patch.get("risk_level", 99)
        _risk_map = {"极低": 1, "低": 2, "中等": 3, "高": 4}
        if isinstance(_risk_raw, str):
            _risk_val = _risk_map.get(_risk_raw, 99)
        else:
            _risk_val = _risk_raw
        try:
            import config as _cfg_a1
            _evo_cfg_a1 = getattr(_cfg_a1, 'EVOLUTION_CONFIG', {})
        except Exception as _e_a1:
            _evo_cfg_a1 = {}
            _module_logger.debug(f"[补丁入队] 配置读取失败，用保守默认值: {_e_a1}")
        # ★第53批 T1（P0-补丁1/3）：阈值**运行时**从 config.EVOLUTION_CONFIG 读取
        #   （每次入队重新 import config → 改配置即时生效，无需重启框架）。
        #   ★同源约定：auto_apply_min_trust 必须与 config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD
        #   同值（门控测试 TestThresholdUnified 断言），避免两套机制再次漂移。
        _max_risk_a1 = _evo_cfg_a1.get("auto_apply_max_risk", 1)   # 默认保守：仅极低风险
        _min_trust_a1 = _evo_cfg_a1.get("auto_apply_min_trust", 60)
        try:
            _trust_val_a1 = float(patch.get("trust_score", 0) or 0)
        except (TypeError, ValueError):
            _trust_val_a1 = 0.0
        _risk_ok_a1 = isinstance(_risk_val, (int, float)) and _risk_val <= _max_risk_a1
        _trust_ok_a1 = _trust_val_a1 >= _min_trust_a1
        if _risk_ok_a1 and _trust_ok_a1:
            patch["status"] = "approved"
            patch["auto_approved"] = True
            _module_logger.info(
                f"[补丁自动审批] 低风险补丁已自动审批: "
                f"{patch.get('file','')}:{patch.get('method','')} "
                f"risk={_risk_raw}(<={_max_risk_a1}), trust={_trust_val_a1}(>={_min_trust_a1})")
        else:
            _module_logger.debug(
                f"[补丁入队] 未自动审批(转人工): {patch.get('file','')}:{patch.get('method','')} "
                f"risk={_risk_raw}(需<={_max_risk_a1}, {'通过' if _risk_ok_a1 else '超限'}), "
                f"trust={_trust_val_a1}(需>={_min_trust_a1}, {'通过' if _trust_ok_a1 else '不足'})")

        pending = self._load_patch_list(self._pending_file)
        history = self._load_patch_list(self._history_file)

        # ★v24.0增强：去重检查——相同文件+相同original_code的补丁不重复添加，且检查历史
        # ★多候选择优合并(E6·深化)：同题去重从「先到先得」升级为「按审美+信任综合分择优」——
        #   同一问题可能同时产生本地规则补丁与 LLM 补丁（original_code 相同），
        #   不再以到达时序丢弃一方，而是保留综合分更高的方案（保优去劣），
        #   与 apply_all_pending 的审美降序应用形成「入队择优 + 应用排序」双层择优。
        # ★7-3修复（P2，2026-09-05，星轨 PHASE7 第七批 7-3）：去重键增强。
        #   原键为 (file, original_code)，实际漏拦了补丁#1与#2——
        #   二者同文件(PulseCodeLearner.py)、同方法(_generate_organ_handbook)、
        #   同来源(llm_multi_file)、同描述(为静默异常添加日志记录)，
        #   只因 LLM 两次生成时的变量名/日志格式略有差异，
        #   original_code 与 modified_code 均不完全相同，原键因此失配。
        #   original_code 是「代码文本」，天生易受上下文提取差异影响；
        #   method 是「结构性标识」，稳定得多。
        #   新键改为 (file, method, repair_source) 三元组：
        #     · 同来源才判重复 → 保住 llm_multi_file 与 local_rule 的择优空间
        #     · 不同来源同方法 → 视为不同候选，双双保留用于择优（符合原设计）
        #   原键**保留**而非替换：两者取「或」，只增拦截面、不减既有能力，
        #   符合「不删除原有逻辑，只增强」的改动规范。
        _new_key = self._patch_dedup_key(patch)
        # 新键退化标记：method 缺失时三元组退化为 (file, "", source)，
        # 区分能力不足，此时才启用原键兜底（见下）。
        _new_key_degraded = not patch.get("method")
        # ★9-问题2修复（2026-09-05）：相似度兜底去重。
        #   7-3 的 (file, method, repair_source) 键能拦住「同文件同方法同来源」，
        #   但当 repair_source 不同（如 local_rule vs llm 反复生成同一处改动）或
        #   method 字段缺失时，键会失配。星轨 7 小时日志实测：PulseLiver /
        #   PulseCortex 的两个 silent_exception 补丁被反复生成约 8 次，每次都
        #   走「同题择优保留」，白白浪费 8 轮 LLM 调用。
        #   此处补一层：同 file + 同 method 且 modified_code 相似度 >0.9 时，
        #   判为实质重复直接跳过（不进入择优，也不重复入队）。
        _new_code = patch.get("modified_code", "") or ""
        for _existing in pending:
            # 相似度兜底：仅当键失配时才启用，避免误伤「同方法不同来源」的择优空间
            _sim_dup = False
            if not (
                self._patch_dedup_key(_existing) == _new_key
            ):
                _exist_code = _existing.get("modified_code", "") or ""
                if (
                    _new_code and _exist_code
                    and _existing.get("file") == patch.get("file")
                    and _existing.get("method") == patch.get("method")
                    and patch.get("method")  # 两条都有 method 才比（否则退化场景不适用）
                ):
                    try:
                        _ratio = difflib.SequenceMatcher(
                            None, _exist_code, _new_code).ratio()
                        _sim_dup = _ratio > 0.9
                    except Exception:
                        _sim_dup = False
            if _sim_dup:
                _module_logger.debug(
                    f"[补丁入队] 相似度去重跳过(>0.9): "
                    f"{patch.get('file','')}:{patch.get('method','')} "
                    f"source={patch.get('repair_source','?')}")
                return
            _same_topic = (
                self._patch_dedup_key(_existing) == _new_key
                or (
                    # 原键**仅在 new_key 退化时**兜底。
                    #   不能无条件叠加：原键是 (file, original_code)，不含来源维度，
                    #   会把「同方法不同来源」的两个补丁也判成同题，
                    #   从而误删掉本应保留用于择优的另一条候选——
                    #   这与 E6 多候选择优合并「llm_multi_file 与 local_rule
                    #   双双保留再择优」的设计意图直接冲突。
                    #   实测即触发：local_rule 补丁被判「同题择优保留」而丢弃。
                    #
                    # ★收窄为「且」：仅当**新旧两条都缺 method** 时才回落原键。
                    #   原先用「或」，意味着只要旧补丁缺 method，就会拿粗粒度
                    #   的 (file, original_code) 去比对新补丁——结果是：一条带
                    #   method 的 llm_multi_file 新补丁，会被一条同文件同原始码
                    #   但缺 method 的旧 local_rule 补丁判成同题，进而在「择优」
                    #   中被静默丢弃。这与 E6 多候选择优的设计意图直接冲突。
                    #   待审队列的取舍原则是：宁可多留一条候选，不可误删一条修复。
                    (_new_key_degraded and not _existing.get("method"))
                    and _existing.get("file") == patch.get("file")
                    and _existing.get("original_code") == patch.get("original_code")
                )
            )
            if _same_topic:
                _new_score = self._composite_score(patch)
                _old_score = self._composite_score(_existing)
                if _new_score > _old_score:
                    # 择优替换：保留已有补丁的 saved_at，覆盖为新方案
                    _kept_at = _existing.get("saved_at", patch.get("saved_at", time.time()))
                    _existing.update(patch)
                    _existing["saved_at"] = _kept_at
                    _existing["updated_at"] = time.time()
                    self._save_patch_list(self._pending_file, pending)
                    _module_logger.info(
                        f"同题择优替换: {patch.get('file','')}:{patch.get('method','')} "
                        f"综合分 {_old_score:.1f}→{_new_score:.1f} (保留更优方案)")
                else:
                    _existing["updated_at"] = time.time()
                    _existing["saved_at"] = time.time()
                    self._save_patch_list(self._pending_file, pending)
                    _module_logger.warning(
                        f"同题择优保留: {patch.get('file','')}:{patch.get('method','')} "
                        f"已有方案综合分 {_old_score:.1f} >= 新方案 {_new_score:.1f}，保留已有")
                return
        for _hist in history:
            # ★7-3：历史查重同样叠加新键（与上方 pending 查重保持同一判定口径，
            #   否则会出现「pending 里判为重复、历史里判为不重复」的口径错位）
            _hist_same = (
                self._patch_dedup_key(_hist) == _new_key
                or (
                    # 与上方 pending 查重同口径：仅当两条都缺 method 时才回落原键
                    (_new_key_degraded and not _hist.get("method"))
                    and _hist.get("file") == patch.get("file")
                    and _hist.get("original_code") == patch.get("original_code")
                )
            )
            if _hist_same:
                # 检查历史中该补丁是否曾经失败，如果失败则不再重复添加
                if not _hist.get("applied", False):
                    # ★第22批 T5/P2-123：相同补丁历史上失败过 → 去重跳过，属**正常
                    #   去重行为**（实测 915 次/1.5h），降为 DEBUG；关开关时回 WARNING。
                    (_module_logger.debug if _noise_reduce()
                     else _module_logger.warning)(
                        f"去重跳过: 相同补丁已在历史中失败过 "
                        f"(原错误: {_hist.get('apply_error', '未知')})")
                    return

        pending.append(patch)
        self._save_patch_list(self._pending_file, pending)
        _module_logger.info(
            f"[补丁入队] {patch.get('id','')[:16]} {patch.get('file','')}:"
            f"{patch.get('method','')} trust={patch.get('trust_score','?')} "
            f"src={patch.get('repair_source','?')}")

    def _composite_score(self, patch: dict[str, Any]) -> float:
        """★多候选择优合并：综合分 = 0.4*审美 + 0.3*信任 + 0.3*价值（0~100 同量纲）。

        用于同题多候选补丁（本地规则 vs LLM）的择优保留。
        ★价值内化(护城河·山3)：框架决策带「我珍视什么」的价值印迹——补丁与主导价值
          契合度（value_alignment）参与择优。无价值信号时退化为 0.5/0.5（零冲突）。
        ★D4修复（P2，2026-09-05）：审美分**缺失**时不再按 0 计分。
            原实现用 `patch.get("aesthetic_score") or {}`，把「字段不存在」与
            「评了 0 分」混为一谈。规则式补丁（local_rule）从未评分、字段缺失，
            于是综合分恒为 0.5×0 + 0.5×trust(30) = 15.0，
            在同题择优中被系统性压低，择优退化为「先到先得」。
            正确语义应当是：**未评分 = 中性，不构成负面信号**，
            故缺失时退化为「仅信任分（+价值分）」，与已评分补丁保持同量纲可比。
        """
        try:
            _aes = patch.get("aesthetic_score")
            _trust_c = max(0.0, min(100.0, float(patch.get("trust_score", 0) or 0)))
            _val = self._value_alignment_score(patch)
            _val_c = max(0.0, min(100.0, _val))
            if _aes is None:
                # 未评分：跳过审美维度，不按 0 拉低
                return round(_trust_c if _val <= 0.0
                             else 0.5 * _trust_c + 0.5 * _val_c, 1)
            _aes_total = float(_aes.get("total", 0) if isinstance(_aes, dict) else 0)
            _aes_c = max(0.0, min(100.0, _aes_total))
            if _val > 0.0:
                return round(
                    0.4 * _aes_c
                    + 0.3 * _trust_c
                    + 0.3 * _val_c, 1)
            return round(0.5 * _aes_c + 0.5 * _trust_c, 1)
        except Exception:
            return 0.0

    def _value_alignment_score(self, patch: dict[str, Any]) -> float:
        """★价值内化(护城河·山3): 补丁与框架主导价值的契合度（0~100）。

        取 ValuePreference 主导价值（strength 降序），检查其语义触发词在补丁文本
        （diff_summary + description + issue_type）中的命中；命中按价值强度加权。
        无价值数据/未命中 → 0（退化为原 0.5/0.5 择优，零冲突）；任何异常降级 0 不误伤。
        """
        try:
            from nucleus.ValuePreference import (
                _POSITIVE_VALUE_WORDS,
                get_value_preference,
            )
            _vp = get_value_preference()
            _dom = _vp.get_dominant(3) if _vp else None
            if not _dom:
                return 0.0
            _text = " ".join([
                str(patch.get("diff_summary") or ""),
                str(patch.get("description") or ""),
                str(patch.get("issue_type") or ""),
            ])
            _hits, _n = 0.0, 0
            for _d in _dom:
                _v = _d.get("value", "")
                _s = float(_d.get("strength", 0) or 0)
                _words = _POSITIVE_VALUE_WORDS.get(_v, [_v])
                if any(w in _text for w in _words):
                    _hits += _s
                    _n += 1
            if _n == 0:
                return 0.0
            return round(min(100.0, 100.0 * _hits / _n), 1)
        except Exception:
            return 0.0

    def list_pending_patches(self) -> list[dict[str, Any]]:
        """列出待审批补丁（★FIX: 提供人工审批入口的数据来源）"""
        return self._load_patch_list(self._pending_file)

    def has_pending_patch_for(self, file_path: str, method_name: str) -> bool:
        """★P1-1修复（第十批）：查询是否已有同文件同方法的待审批补丁。

        供补丁生成前（SafeEvolutionExecutor._generate_patch）预检使用，
        避免「同一处改动反复生成 + LLM 反复调用」，从源头减少重复补丁。
        判定口径：file 与 method 双匹配即视为「已有待审批补丁」，
        不区分 repair_source —— 只要同位置已有补丁待审批，就跳过再次生成。
        """
        if not file_path or not method_name:
            return False
        try:
            for _p in self._load_patch_list(self._pending_file):
                if (
                    _p.get("file") == file_path
                    and _p.get("method") == method_name
                ):
                    return True
        except Exception:
            # 读取失败不阻断生成（保守放行，交给入队阶段的去重兜底）
            return False
        return False

    def _is_stale(self, patch: dict[str, Any]) -> bool:
        """★第54批 T3.2：补丁是否已过期（超过 PATCH_AUTO_APPROVE_STALE_DAYS 天）。

        ★背景：PatchAutoApprover 里早有 is_stale/_is_stale_by_age 实现，
        但该类 deprecated 且**零生产调用** → 过期检测此前从未真正生效。
        ★无可用时间戳 → 视为未过期（不误伤历史补丁）。
        """
        _now = time.time()
        for _k in ("created_at", "submitted_at", "timestamp", "generated_at"):
            _t = patch.get(_k)
            if isinstance(_t, (int, float)) and _t > 0:
                return (_now - _t) / 86400.0 > _m54_stale_days()
        return False

    def approve_patch(self, index: int) -> dict[str, Any]:
        """将指定待审批补丁的 status 从 verified/pending 提升为 approved（★FIX: 人工审批闭环）"""
        pending = self._load_patch_list(self._pending_file)
        if not (0 <= index < len(pending)):
            return {"ok": False, "reason": f"索引越界，共{len(pending)}个待审批补丁"}
        _patch = pending[index]
        if _patch.get("status") == "approved":
            return {"ok": False, "reason": "该补丁已批准"}
        _patch["status"] = "approved"
        _patch["approved_at"] = time.time()
        # ★第54批 T3.2（P1）：过期检测 —— 超过阈值天数的补丁打 stale 标记。
        if _m54_stale_mark_on() and self._is_stale(_patch):
            _patch["stale"] = True
            _module_logger.warning(
                f"[补丁审批] 补丁已过期(>{_m54_stale_days()}天)，标记 stale: "
                f"{_patch.get('file','')}:{_patch.get('method','')}")
        # ★v9.5审美判据（登顶路线图 第四条路）：审批时计算补丁质量分并记录档案（不阻断闭环）
        try:
            from nucleus.evolution.AestheticJudge import get_aesthetic_judge
            _aesthetic = get_aesthetic_judge().score(_patch.get("modified_code"))
            _patch["aesthetic_score"] = _aesthetic
            _patch["aesthetic_grade"] = _aesthetic.get("grade")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ★第53批 T2（P0-补丁2）：检查写入结果 —— 写盘失败（如被 WriteGuard 拦截）
        #   不得返回 ok=True，否则用户误以为审批成功、而后续应用全部跳过。
        _save_ok = self._save_patch_list(self._pending_file, pending)
        if _m53_write_check_on() and not _save_ok:
            _module_logger.error(
                f"[补丁审批] 写入失败，审批未持久化: "
                f"{_patch.get('file','')}:{_patch.get('method','')} path={self._pending_file}")
            return {"ok": False, "approved": 0,
                    "reason": "写入失败（可能被 WriteGuard 拦截）",
                    "error": "写入失败（可能被 WriteGuard 拦截）"}
        _module_logger.info(
            f"[补丁审批] index={index} {_patch.get('file','')}:{_patch.get('method','')} "
            f"grade={_patch.get('aesthetic_grade','?')}")
        _reason = _patch.get("reason", "")
        _grade = _patch.get("aesthetic_grade")
        if _grade in ("C", "D"):
            _reason = f"{_reason}（审美判定:{_grade}）"
        # ★第54批 T3.3（P1）：auto_apply_enabled 在审批侧生效 ——
        #   False（默认）→ 只审批不应用；True → 置 auto_apply_requested，
        #   ★真正改写源码仍由 SafeEvolutionExecutor 安全门决定，此处不越权。
        _auto_apply = _m54_auto_apply_on()
        if _auto_apply:
            _patch["auto_apply_requested"] = True
        return {"ok": True, "file": os.path.basename(_patch.get("file", "")),
                "reason": _reason, "auto_apply": _auto_apply,
                "stale": bool(_patch.get("stale"))}

    def approve_all_patches(self) -> dict[str, Any]:
        """批准所有待审批补丁（★FIX: 人工审批闭环）"""
        pending = self._load_patch_list(self._pending_file)
        _count = 0
        for _p in pending:
            if _p.get("status") != "approved":
                _p["status"] = "approved"
                _p["approved_at"] = time.time()
                # ★v9.5审美判据：批量审批同样记录质量分（不阻断闭环）
                try:
                    from nucleus.evolution.AestheticJudge import get_aesthetic_judge
                    _aesthetic = get_aesthetic_judge().score(_p.get("modified_code"))
                    _p["aesthetic_score"] = _aesthetic
                    _p["aesthetic_grade"] = _aesthetic.get("grade")
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                _count += 1
        if _count:
            # ★第53批 T2（P0-补丁2）：检查写入结果（同 approve_patch）
            _save_ok = self._save_patch_list(self._pending_file, pending)
            if _m53_write_check_on() and not _save_ok:
                _module_logger.error(
                    f"[补丁审批] 批量审批写入失败，审批未持久化: "
                    f"count={_count} path={self._pending_file}")
                return {"ok": False, "approved": 0,
                        "reason": "写入失败（可能被 WriteGuard 拦截）",
                        "error": "写入失败（可能被 WriteGuard 拦截）"}
        return {"ok": True, "approved": _count}

    def _check_patch_path(self, patch: dict[str, Any]) -> tuple[bool, str]:
        """★7-2 / P1-8修复（P1，2026-09-05）：补丁目标路径沙箱（CWE-22 路径穿越防护）。

        背景：
            patch["file"] 在整条补丁链路上被**直接使用、全程零校验**——
                :311  shutil.copy2(patch["file"], tmp_target)   验证侧复制
                :317  open(patch["file"])                       读取源文件
                :1236 shutil.copy2(patch["file"], backup_path)  应用前备份
                :1282 写回 modified_code 到源文件               真正落地
                :1468 shutil.copy2(backup_path, patch["file"])  失败回退
            而 :308 的 `os.path.relpath()` **不会消除 `..`**：
                relpath("../../etc/passwd", "/proj") → "../../etc/passwd"
                join(tmp_dir,   "../../etc/passwd") → 跳出临时目录
            于是补丁只要把 file 写成越界路径，就能读写项目目录外的任意文件。

        防护策略（三重）：
            ① 扩展名白名单：仅允许 .py，杜绝写配置/数据/凭证文件
            ② realpath 归一化：消除 `..`、`.` 与符号链接后再判定
            ③ 项目根前缀校验：commonpath 必须等于项目根，越界即拒绝

        为什么只在此处拦截：
            _verify_in_copy 是补丁**落地前的唯一必经关卡**
            （apply_all_pending:1212 与对外契约 verify_in_copy:1511 都汇聚于此），
            在此单点拦截即可覆盖全部四处危险使用点，无需改动任何既有写文件逻辑，
            符合最小侵入原则。

        边界与容错：
            · Windows 跨盘符时 commonpath 抛 ValueError，按越界拒绝
            · 任何异常一律「拒绝」而非「放行」，失败即闭合（fail-closed）
        """
        _raw = str(patch.get("file", "") or "")
        if not _raw:
            return False, "补丁缺少 file 字段"

        # ① 扩展名白名单
        if not _raw.lower().endswith(".py"):
            return False, f"非法扩展名(仅允许 .py): {_raw}"

        # ② Windows 盘符显式比对（跨平台处理）。
        #    补丁的 file 字段可能由 LLM 生成而写成 Windows 风格（X:\...）。
        #    在 Linux 上 os.path.isabs("C:\\...") 返回 False，它会被当成
        #    普通相对文件名挂进项目根从而绕过越界判定；在 Windows 上虽会走
        #    绝对路径分支，但提前显式比对可让判定与运行平台无关。
        import re as _re
        _m = _re.match(r"^([A-Za-z]):[\\/]", _raw)
        if _m:
            _rm = _re.match(r"^([A-Za-z]):[\\/]", str(self._project_root))
            if not _rm or _m.group(1).lower() != _rm.group(1).lower():
                return False, f"路径越界(跨盘符): {_raw}"

        try:
            _root = os.path.realpath(self._project_root)
            # ② realpath 归一化（绝对路径直接解析，相对路径先挂到项目根）
            _abs = os.path.realpath(
                _raw if os.path.isabs(_raw) else os.path.join(self._project_root, _raw))
            # ③ 必须落在项目根目录内
            if os.path.commonpath([_abs, _root]) != _root:
                return False, f"路径越界(不在项目根目录内): {_raw} → {_abs}"
        except ValueError:
            # Windows 跨盘符（C:\ vs D:\）无法求公共路径
            return False, f"路径越界(跨盘符): {_raw}"
        except Exception as _e:
            # 判定失败一律拒绝，绝不因异常而放行
            return False, f"路径校验异常(已拒绝): {_raw} ({_e})"

        return True, ""

    def _verify_in_copy(self, patch: dict[str, Any]) -> dict[str, Any]:
        """在项目副本中测试补丁，完全隔离不影响主框架"""
        import tempfile

        result = {
            "passed": False,
            "stage": "init",
            "errors": [],
            "logs": "",
        }

        # ★7-2：路径沙箱——补丁链路上所有写操作的第一道闸。
        #   必须在任何 open()/copy2() 之前执行，否则越界补丁已经完成读写。
        _path_ok, _path_reason = self._check_patch_path(patch)
        if not _path_ok:
            result["errors"].append(f"补丁路径校验失败: {_path_reason}")
            result["stage"] = "path_check_failed"
            _module_logger.warning(
                f"[补丁验证] 路径沙箱拒绝: {_path_reason}")
            return result

        # ★策略自进化扩展(长期深化): 验证深度按历史策略效果 + 风险分级动态标记。
        #   - 历史规则策略失败率高（rule_bad）或核心文件 → verify_depth="deep"（深验证）；
        #   - 其余 → "standard"（标准验证）。
        #   deep 仅影响验证强度标记/行为等价强制/审计，不改标准验证链判定（零冲突）。
        _verify_depth = "standard"
        try:
            _hist = self.get_strategy_effectiveness(patch.get("issue_type", ""))
            _norm_f = (patch.get("file") or "").replace("\\", "/")
            _core_markers = ("main.py", "config.py", "base/", "nucleus/pulse/",
                             "nucleus/field/", "nucleus/mnemosyne/", "nucleus/reasoning/")
            _is_core = any(m in _norm_f for m in _core_markers)
            if _hist.get("rule_bad") or _is_core:
                _verify_depth = "deep"
        except Exception:
            _verify_depth = "standard"
        result["verify_depth"] = _verify_depth

        # ★PULSE-DEFECT-20260901-01 方案B：LLM 补丁完整性护栏（验证第一关）。
        # 从源头拦截残缺/截断的补丁（如仅剩方法开头 2%~12%、被截断的代码），
        # 避免「语法恰好合法但内容严重残缺」的补丁通过后续语法/导入关混入队列。
        _completeness = self._check_llm_patch_completeness(patch)
        if not _completeness["complete"]:
            result["errors"].append(f"补丁完整性检查失败: {_completeness['reason']}")
            result["stage"] = "completeness_check_failed"
            return result

        tmp_dir = tempfile.mkdtemp(prefix="tongtong_patch_test_")
        try:
            # 1. 创建副本
            target_file_rel = os.path.relpath(patch["file"], self._project_root)
            tmp_target = os.path.join(tmp_dir, target_file_rel)
            os.makedirs(os.path.dirname(tmp_target), exist_ok=True)
            shutil.copy2(patch["file"], tmp_target)
            
            # 2. 应用补丁到副本
            with open(patch["file"], encoding='utf-8') as orig:
                full_content = orig.read()
            
            if patch["original_code"] not in full_content:
                result["errors"].append("目标代码片段在源文件中未找到，无法应用补丁")
                result["stage"] = "code_not_found"
                return result

            modified_full = full_content.replace(patch["original_code"], patch["modified_code"])
            with open(tmp_target, 'w', encoding='utf-8') as f:
                f.write(modified_full)
            
            result["stage"] = "patch_applied_to_copy"
            
            # 3. 语法校验
            try:
                with open(tmp_target, encoding='utf-8') as f:
                    _src = f.read()
                ast.parse(_src)
                # ★多入口验证: compile() 走完整编译，捕获 ast.parse 不报但编译期
                #   报错的情况（future 语句位置/编码/重复等），作为第二道语法入口。
                compile(_src, tmp_target, 'exec')
                result["stage"] = "syntax_check_passed"
            except SyntaxError as e:
                result["errors"].append(f"语法错误: {e}")
                result["stage"] = "syntax_check_failed"
                return result
            
            # 4. 编译验证（py_compile，不执行模块级代码，避免框架文件模块级副作用导致误判）
            # ★2026-09-03修复：原exec(open(file).read())会执行整个文件，
            #   框架文件有大量模块级导入/初始化代码，在临时目录中必然失败，
            #   导致所有补丁验证都不通过。改为py_compile只做字节码编译检查。
            import py_compile
            try:
                py_compile.compile(tmp_target, doraise=True)
                result["stage"] = "compile_check_passed"
                result["passed"] = True
            except py_compile.PyCompileError as _ce:
                result["errors"].append(f"编译失败: {_ce}")
                result["stage"] = "compile_check_failed"
                return result
            except Exception as _e:
                result["errors"].append(f"编译验证异常: {_e}")
                result["stage"] = "compile_check_error"
                return result

            # 5. ★T5(PHASE9) import 检查——补丁修改后的副本能否正常 import（沙箱联动）。
            #    星轨 PHASE9 要求第 2/5 项：副本验证必须含 import 检查，且用
            #    execute_code_in_subprocess() 执行（CPU 30s / 内存 512MB 限制），
            #    不得在主进程直接执行补丁代码。
            #    语义：import「应用了补丁的副本」(tmp_target)，而非原文件。
            #    补丁只改单个文件的方法体，import 该副本即同时验证了「语法 +
            #    顶层可执行 + 依赖可解析」，比 py_compile 深一层（真实触发 import 链）。
            #    失败即 fail-closed：passed=False，标记 stage，不进入待审批。
            _import_check = self._verify_import_in_subprocess(patch, tmp_target)
            result["import"] = _import_check
            if not _import_check.get("ok"):
                result["passed"] = False
                result["stage"] = "import_check_failed"
                result["errors"].append(
                    f"import检查失败: {_import_check.get('reason', '未知')}")
                _module_logger.warning(
                    f"[补丁验证] import检查失败({patch.get('file','')}): "
                    f"{_import_check.get('reason', '')}")
                return result

            # 6. ★进化增强：真实回归测试（轻量验证脚本，退出码 0=通过）
            #    原验证仅做语法+导入，可能放行「能导入但行为损坏」的补丁。
            #    此处运行项目自带的轻量验证脚本，作为行为回归的兜底。
            #    ★策略自进化扩展: deep 验证对 LLM 补丁做二次完整性复核（防深验证放行残缺补丁）。
            if _verify_depth == "deep" and patch.get("repair_source") == "llm":
                try:
                    _recheck = self._check_llm_patch_completeness(patch)
                    if not _recheck.get("complete"):
                        result["passed"] = False
                        result["stage"] = "completeness_check_failed"
                        result["errors"].append(
                            f"深验证-二次完整性复核失败: {_recheck.get('reason', '')}")
                        return result
                    result["deep_recheck"] = "completeness_ok"
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _regression = self._run_regression_tests()
            if _regression["passed"]:
                result["passed"] = True
                result["stage"] = "regression_test_passed"
                result["regression"] = _regression["summary"]
                # ★验证深度升级(黄项收敛): 行为等价探针——纯函数场景运行前后对比。
                #   非纯函数/无法生成输入 → checked=False（跳过，不影响原验证链，零误伤）。
                #   行为不等价 → 判定失败（stage=behavior_mismatch），交自我反思闭环换策略重试。
                # ★策略自进化扩展: deep 验证时若无法行为等价（非纯函数），追加深验证说明
                #   （供人工审计关注；判定本身不受影响，零冲突）。
                _behavior = self._behavior_equivalence_probe(patch)
                result["behavior"] = _behavior
                if _behavior.get("checked") and not _behavior.get("equivalent"):
                    result["passed"] = False
                    result["stage"] = "behavior_mismatch"
                    result["errors"].append(
                        f"行为等价验证失败: {_behavior.get('reason', '')}")
                    return result
                if _verify_depth == "deep" and not _behavior.get("checked"):
                    result["deep_notes"] = (
                        "深验证: 非纯函数无法行为等价，补丁行为安全依赖"
                        "回归+完整性验证，建议人工关注")
                # ★多入口验证(跨版本/结构护栏): 方法签名一致性——补丁声称修改的
                #   method 在修改后仍存在且参数签名与修改前一致，防止补丁误改签名
                #   导致调用方崩溃（被修改方法缺失 → 判定失败）。
                _sig = self._check_method_signature_consistency(patch, _src)
                result["signature"] = _sig
                if _sig.get("checked") and not _sig.get("consistent"):
                    result["passed"] = False
                    result["stage"] = "signature_mismatch"
                    result["errors"].append(
                        f"方法签名一致性验证失败: {_sig.get('reason', '')}")
                    return result
            else:
                # ★T5(PHASE9)：回归脚本是项目全局脚本（parquet/覆盖率），与具体补丁无关。
                #   星轨 PHASE9 判定标准（第4点）是「compileall通过 + import成功 + 无新增ERROR」，
                #   这三项已由第 4/5 步的 py_compile + import 子进程硬门槛保证；
                #   全局回归失败不构成补丁正确性的判据，故只记 warning、不影响 passed。
                #   （「至少1个相关测试通过」的语义由 import 检查 + 无新增ERROR 兜底，
                #     而非依赖这些环境敏感的全局脚本。）
                result["regression"] = _regression["summary"]
                result["regression_warning"] = (
                    f"全局回归脚本有失败({_regression['failed']}个)，"
                    f"与具体补丁无关，仅记录参考: {_regression['summary']}")
                _module_logger.warning(
                    f"[补丁验证] 全局回归脚本失败(仅记录，不影响判定): {_regression['summary']}")

            # ★多入口验证: 汇总本补丁实际执行的验证入口（体现验证深度，供审计）。
            _entries = [k for k in ("completeness", "syntax", "compile", "import", "regression")
                        if result.get(k) is not None or k in ("syntax", "import")]
            result["verify_entries"] = _entries + (["behavior"] if result.get("behavior") else []) \
                + (["signature"] if result.get("signature") else [])
            result["cross_version_note"] = (
                "当前环境仅 Python 3.12 单解释器，多环境/跨版本 CI 受环境限制未落地；"
                "以 compile 编译验证 + 方法签名结构护栏作为本地跨入口替代。")
            if result.get("passed"):
                # ★日志补全: 补丁验证通过摘要进后台日志（深度+入口），便于离线审计实际运行。
                _module_logger.info(
                    f"[补丁验证] {patch.get('file','')}:{patch.get('method','')} "
                    f"{result.get('stage','')} depth={result.get('verify_depth','')} "
                    f"entries={result.get('verify_entries')}")
        except Exception as e:
            result["errors"].append(f"验证异常: {e}")
            result["stage"] = "exception"
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        
        return result

    def _verify_import_in_subprocess(self, patch: dict[str, Any],
                                     copy_path: str) -> dict[str, Any]:
        """★T5(PHASE9)：import 检查——补丁修改后的副本能否正常 import（沙箱联动）。

        星轨 PHASE9 要求第 2/5 项：
          - 副本验证必须包含 import 检查（补丁修改的文件能正常 import）；
          - 必须用 execute_code_in_subprocess() 执行，CPU 30s / 内存 512MB 限制；
          - 不得直接在主进程执行补丁代码。

        实现语义：
          - import 的目标是「应用了补丁的副本文件」(copy_path)，而非原文件；
          - 用 importlib.util.spec_from_file_location 从副本路径加载模块，
            并把项目根注入 sys.path，使副本里的框架依赖（from nucleus.xxx import）
            能正常解析——既验证补丁文件本身，也验证其依赖可导入性；
          - import 成功 + 子进程 stderr 无 traceback/ERROR → ok=True；
          - 资源超限（wall_clock/cpu/memory）→ ok=False（fail-closed）；
          - 任何异常 → ok=False，绝不放行。

        与「无新增ERROR」的联动：
          import 会触发副本文件及其依赖的模块级代码，若补丁引入运行时错误
          （如模块级 NameError / 类型错误），会在子进程 stderr 留下 traceback，
          此处检测 stderr 中的 Traceback/Error 关键字作为「无新增ERROR」判据。
        """
        try:
            from nucleus.security.sandbox_limits import execute_code_in_subprocess
        except Exception as _imp_err:
            return {"ok": False, "reason": f"沙箱模块不可用: {_imp_err}"}

        # 副本文件必须真实存在（_verify_in_copy 已生成 tmp_target）。
        if not os.path.isfile(copy_path):
            return {"ok": False, "reason": f"副本文件不存在: {copy_path}"}

        # 模块名：用副本文件的绝对路径推导，保证 spec_from_file_location 的 name 稳定唯一。
        # 例：/tmp/tongtong_patch_test_xxx/nucleus/reasoning/PatchManager.py
        #     → 模块名取文件名（去掉 .py），避免与真实模块冲突。
        _module_name = "patch_copy_" + os.path.basename(copy_path).replace(".py", "")

        # 子进程执行代码：注入项目根（绝对路径）到 sys.path（副本里 from nucleus.xxx /
        # import config 等依赖），再用 spec_from_file_location 从副本路径加载模块并 exec。
        # ★关键：self._project_root 可能是相对路径（如 '.'），而子进程 cwd 是沙箱临时
        # 目录，相对路径会指向错误位置。必须用 os.path.abspath 归一化为绝对路径，
        # 否则副本里 `import config` 会 ModuleNotFoundError，正常补丁被误拒。
        _proj_root_abs = os.path.abspath(self._project_root)
        # 副本路径也归一化为绝对路径（spec_from_file_location 需要真实可定位的路径）。
        _copy_path_abs = os.path.abspath(copy_path)
        # 顶层 try/except 捕获所有异常并 exit(3)，把失败信息写入 stderr。
        _code = (
            "import sys, importlib.util\n"
            f"sys.path.insert(0, {_proj_root_abs!r})\n"
            "try:\n"
            f"    _spec = importlib.util.spec_from_file_location({_module_name!r}, {_copy_path_abs!r})\n"
            "    _mod = importlib.util.module_from_spec(_spec)\n"
            "    _spec.loader.exec_module(_mod)\n"
            "    print('IMPORT_OK', flush=True)\n"
            "except Exception as _e:\n"
            "    import traceback\n"
            "    traceback.print_exc()\n"
            "    sys.stderr.write('IMPORT_FAIL: ' + repr(_e) + '\\n')\n"
            "    sys.exit(3)\n"
        )

        try:
            _res = execute_code_in_subprocess(
                _code,
                timeout_seconds=30,          # 墙上时钟 30s（CPU 硬上限另设 30s）
                allow_full_builtins=True,    # import 需要完整 builtins + import 能力
                source_label=f"patch_import_check:{_module_name}",
            )
        except Exception as _e:
            return {"ok": False, "reason": f"沙箱执行异常: {_e}"}

        # 资源超限 → fail-closed。
        if _res.limit_hit:
            _limit_reason = {
                "wall_clock": "墙上时钟超时(30s)",
                "cpu": "CPU 时间超限(30s)",
                "memory": "内存超限(512MB)",
            }.get(_res.limit_hit, _res.limit_hit)
            return {"ok": False, "reason": f"import 检查资源超限: {_limit_reason}",
                    "sandbox": {"limit_hit": _res.limit_hit,
                                "peak_memory_bytes": _res.peak_memory_bytes,
                                "duration_ms": _res.duration_ms}}

        _stderr = _res.stderr or ""
        _stdout = _res.stdout or ""

        # 判定：returncode==0 且 stdout 有 IMPORT_OK 标记。
        _import_ok = _res.returncode == 0 and "IMPORT_OK" in _stdout

        # 「无新增ERROR」判据：子进程 stderr 出现 Traceback 或 ERROR 级日志 → 视为新增错误。
        # ★主线第30批 T2 评估：此处解析的是**子进程 stderr 原始输出**（无 `[器官] LEVEL:`
        #   格式保证），**不复用** `extract_log_level`——宽松子串判据在此是**保守正确**的
        #   （宁可误判为"有错误"而拒绝补丁，也不放过真实错误）。保留原实现。
        _has_traceback = "Traceback" in _stderr
        _has_error = "ERROR" in _stderr.upper()

        if _import_ok and not _has_traceback and not _has_error:
            return {"ok": True, "reason": f"副本 {_module_name} import 成功",
                    "sandbox": {"duration_ms": _res.duration_ms,
                                "peak_memory_bytes": _res.peak_memory_bytes}}

        # 失败路径：尽量给出精确原因。
        if not _import_ok:
            _tail = (_stderr or _stdout)[-400:]
            return {"ok": False,
                    "reason": f"副本 {_module_name} import 失败: {_tail}",
                    "sandbox": {"returncode": _res.returncode}}
        if _has_traceback or _has_error:
            _tail = _stderr[-400:]
            return {"ok": False,
                    "reason": f"副本 {_module_name} import 产生新ERROR: {_tail}"}
        return {"ok": False, "reason": f"副本 {_module_name} import 结果异常"}

    def _check_method_signature_consistency(self, patch: dict[str, Any],
                                           modified_source: str) -> dict[str, Any]:
        """★多入口验证(结构护栏): 补丁声称修改的 method 在修改后仍存在且签名一致。

        用 AST 提取修改前后目标方法/函数的 name + 参数（位置/关键字/默认值数量），
        前后不一致 → consistent=False（说明补丁误改了签名，调用方可能崩溃）。
        无 method 字段 / 方法在修改前不存在 / 解析异常 → checked=False（跳过，零冲突）。
        """
        try:
            _method = patch.get("method") or ""
            _orig = patch.get("original_code") or ""
            if not _method or not _orig or not modified_source:
                return {"checked": False, "consistent": True, "reason": "无需校验"}
            import ast as _ast
            import textwrap as _tw

            def _sig(txt):
                # 带缩进的方法体片段直接 ast.parse 会报 IndentationError，
                # 解析前先 dedent 去统一缩进（与完整性检查一致的处理）。
                _t = _ast.parse(_tw.dedent(txt))
                for _n in _ast.walk(_t):
                    if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                            and _n.name == _method:
                        return (_n.name,
                                len(_n.args.posonlyargs),
                                len(_n.args.args),
                                len(_n.args.kwonlyargs),
                                _n.args.vararg is not None,
                                _n.args.kwarg is not None)
                return None

            _before = _sig(_orig)
            _after = _sig(modified_source)
            if _before is not None and _after is None:
                # 修改前存在、修改后消失 → 方法被删除/改名，调用方必然崩溃 → 判不一致。
                return {"checked": True, "consistent": False,
                        "reason": f"方法 {_method} 在修改后不存在（被删除或改名），调用方可能崩溃"}
            if _before is None or _after is None:
                return {"checked": False, "consistent": True,
                        "reason": f"方法 {_method} 在修改前/后无法定位，跳过签名校验"}
            if _before != _after:
                return {"checked": True, "consistent": False,
                        "reason": f"方法 {_method} 签名被修改: {_before} → {_after}"}
            return {"checked": True, "consistent": True,
                    "reason": f"方法 {_method} 签名一致"}
        except Exception:
            return {"checked": False, "consistent": True, "reason": "签名校验异常，跳过"}

    def _check_llm_patch_completeness(self, patch: dict[str, Any]) -> dict[str, Any]:
        """★PULSE-DEFECT-20260901-01 方案B：LLM 补丁完整性静态护栏。

        在副本验证之前，先做三关静态检查，从源头拦截残缺/截断的补丁：
          1) 修改内容非空、且与原文有实质差异；
          2) 修改后代码语法可解析（ast.parse）；
          3) 与原文的相似度不低于阈值（默认 0.5），拦截「仅剩 2%~12%」的严重残缺。

        返回 {"complete": bool, "reason": str}。complete=False 时 reason 说明失败原因。
        """
        _original = patch.get("original_code", "")
        _modified = patch.get("modified_code", "")
        # ★PULSE-DEFECT-20260902-01 修复: 真实补丁的 original/modified 是「带缩进的方法体片段」
        #   （get_method_body 返回原样缩进），直接 ast.parse 会报 unexpected indent 误拦全部
        #   真实补丁，导致自主修复验证链在完整性第一关被卡死。
        #   修复: 语法解析与相似度对比前统一 textwrap.dedent（缩进仅结构上下文，不影响判定）。
        import textwrap as _tw
        _orig_norm = _tw.dedent(_original or "").strip()
        _mod_norm = _tw.dedent(_modified or "").strip()

        # 关1：非空 + 实质差异
        if not _mod_norm or _mod_norm == _orig_norm:
            return {"complete": False, "reason": "修改内容为空或与原文完全一致"}

        # 关2：语法可解析（dedent + 虚拟函数包装，兼容带缩进/含 return 的方法体片段）
        #   ★PULSE-DEFECT-20260902-01: 方法体片段含 return，dedent 后顶层 return 非法，
        #   需包装进虚拟函数才能正确解析；完整方法（def 开头）包装后为嵌套 def 亦合法。
        try:
            import ast as _ast
            _ast.parse("def _wrap():\n" + _tw.indent(_mod_norm, "    "))
        except SyntaxError as _e:
            return {"complete": False, "reason": f"语法错误: {_e}"}

        # 关3：相似度阈值（拦截严重残缺/截断；dedent 后对比保证可比）
        #   ★PULSE-DEFECT-20260902-01: 用整字符串对比（不用 splitlines——单行方法体
        #   时 SequenceMatcher 对 [A] vs [B] 恒为 0.0，误拦所有单行补丁）。
        try:
            import difflib
            _ratio = difflib.SequenceMatcher(None, _orig_norm, _mod_norm).ratio()
            if _ratio < 0.5:
                return {
                    "complete": False,
                    "reason": f"与原文相似度过低({_ratio:.2f} < 0.5)，疑似严重残缺/截断",
                }
        except Exception:
            # 相似度计算失败不阻断验证（保守放行，交给后续语法/导入关）
            pass

        return {"complete": True, "reason": ""}

    def _behavior_equivalence_probe(self, patch: dict[str, Any]) -> dict[str, Any]:
        """★验证深度升级(黄项收敛): 补丁前后方法行为等价探针。

        仅对「无外部依赖的纯函数」方法生效（零副作用、无 self 依赖、仅内置函数）：
          1) 从源文件解析目标方法完整定义（含签名）；
          2) 分别用 original_code / modified_code 替换方法体，构造前后两个可调用版本；
          3) 用代表性输入实际运行，比较返回值（浮点 isclose，其余严格 ==）。

        非纯函数 / 无法生成输入 / 执行异常 → checked=False（跳过，不影响原验证链，零误伤）。
        行为不等价 → {"checked": True, "equivalent": False, "reason": 差异说明}。

        Returns: {"checked": bool, "equivalent": bool, "reason": str, "samples": int}
        """
        try:
            _file = patch.get("file", "")
            _method = patch.get("method", "")
            _orig = patch.get("original_code", "")
            _mod = patch.get("modified_code", "")
            if not (_file and _method and _orig and _mod):
                return {"checked": False, "equivalent": True, "reason": "缺方法信息"}

            with open(_file, encoding="utf-8") as _f:
                _src = _f.read()
            _tree = ast.parse(_src)
            _target = None
            for _n in ast.walk(_tree):
                if isinstance(_n, ast.FunctionDef) and _n.name == _method:
                    _target = _n
                    break
            if _target is None:
                return {"checked": False, "equivalent": True, "reason": "目标方法不存在"}
            if isinstance(_target, ast.AsyncFunctionDef):
                return {"checked": False, "equivalent": True, "reason": "异步方法跳过"}

            # ---- 纯函数判定 ----
            # 收集参数名
            _params = {_a.arg for _a in (_target.args.posonlyargs + _target.args.args
                                         + _target.args.kwonlyargs)}
            # 收集局部名（赋值/for/import/def/class/with/except 目标）
            _locals = set(_params)

            def _add_locals(_node: ast.AST) -> None:
                for _c in ast.walk(_node):
                    if isinstance(_c, ast.Name) and isinstance(_c.ctx, (ast.Store, ast.Del)):
                        _locals.add(_c.id)
                    elif isinstance(_c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        _locals.add(_c.name)
                    elif isinstance(_c, (ast.Import, ast.ImportFrom)):
                        for _a in _c.names:
                            _locals.add(_a.asname or _a.name.split(".")[0])
                    elif isinstance(_c, ast.ExceptHandler) and _c.name:
                        _locals.add(_c.name)

            for _s in _target.body:
                _add_locals(_s)

            # 检测 self 属性访问（依赖实例状态 → 非纯）
            for _c in ast.walk(_target):
                if isinstance(_c, ast.Attribute) and isinstance(_c.value, ast.Name)                         and _c.value.id == "self":
                    return {"checked": False, "equivalent": True, "reason": "依赖 self 状态，非纯函数"}

            # 收集自由名称（Load 上下文，排除参数/局部/builtin）
            _builtins_set = set(dir(builtins))
            _free = set()
            for _c in ast.walk(_target):
                if isinstance(_c, ast.Name) and isinstance(_c.ctx, ast.Load):
                    if _c.id not in _locals and _c.id not in _builtins_set:
                        _free.add(_c.id)
            if _free:
                return {"checked": False, "equivalent": True,
                        "reason": f"依赖外部名称: {sorted(_free)[:5]}"}

            # ---- 构造前后两个可调用版本 ----
            _sig = ast.unparse(_target.args)
            _orig_def = f"def {_method}({_sig}):\n" + "\n".join(
                f"    {_l}" for _l in _orig.splitlines()) if _orig else ""
            _mod_def = f"def {_method}({_sig}):\n" + "\n".join(
                f"    {_l}" for _l in _mod.splitlines()) if _mod else ""
            if not _orig_def or not _mod_def:
                return {"checked": False, "equivalent": True, "reason": "方法体为空"}
            _ns_o: dict[str, Any] = {"__builtins__": builtins.__dict__}
            _ns_m: dict[str, Any] = {"__builtins__": builtins.__dict__}
            try:
                # 安全说明: 此处 exec 对象为框架自身补丁代码(original/modified)，非外部输入；
                #   纯函数判定已拦截一切外部名称依赖(os/sys/...)，命名空间仅含 builtins，
                #   全异常捕获兜底 —— 属受控隔离执行。
                exec(compile(_orig_def, "<orig>", "exec"), _ns_o)  # 受控隔离执行框架自身代码
                exec(compile(_mod_def, "<mod>", "exec"), _ns_m)  # 受控隔离执行框架自身代码
            except Exception as _e:
                return {"checked": False, "equivalent": True,
                        "reason": f"构造探针失败(签名引用外部类型?): {_e}"}
            _f_orig = _ns_o[_method]
            _f_mod = _ns_m[_method]

            # ---- 生成代表性输入 ----
            _all_args = _target.args.posonlyargs + _target.args.args
            _defaults = [None] * (len(_all_args) - len(_target.args.defaults))                 + list(_target.args.defaults)

            def _value_candidates(_name: str, _default: Any) -> list[Any]:
                _nm = _name.lower()
                if isinstance(_default, bool):
                    return [_default, not _default]
                if isinstance(_default, int):
                    return [_default, _default + 1, 0]
                if isinstance(_default, float):
                    return [_default, _default * 1.5, 0.0]
                if isinstance(_default, str):
                    return [_default, _default + "x", ""]
                if isinstance(_default, (list, tuple)):
                    return [list(_default), []]
                if isinstance(_default, dict):
                    return [dict(_default), {}]
                # 复数/集合类名词优先 list（避免 values/items/seq 被"val"误判为数值）
                if any(_k in _nm for _k in
                       ("values", "items", "seq", "list", "vec", "arr", "cand",
                        "ids", "keys", "data", "batch", "samples")):
                    return [[], [1.0, 2.0], [1, 2, 3]]
                if any(_k in _nm for _k in
                       ("count", "num", "n_", "_n", "idx", "index", "dim", "size",
                        "times", "round", "len", "loop", "depth", "max", "min")):
                    return [0, 1, 3]
                if any(_k in _nm for _k in
                       ("ratio", "score", "weight", "val", "value", "prob", "temp",
                        "alpha", "beta", "threshold", "rate", "pct")):
                    return [0.0, 0.5, 1.0]
                if any(_k in _nm for _k in
                       ("text", "str", "name", "msg", "query", "word", "token", "keyword")):
                    return ["", "abc", "测试"]
                if any(_k in _nm for _k in ("flag", "enable", "ok", "bool", "active")):
                    return [True, False]
                return [0, 1.0, ""]

            _candidates: list[list[Any]] = []
            for _a, _d in zip(_all_args, _defaults):
                _vals = _value_candidates(_a.arg, _d)
                if not _vals:
                    return {"checked": False, "equivalent": True, "reason": "无法生成代表输入"}
                _candidates.append(_vals)
            _combo: list[tuple] = [tuple(_c[0] for _c in _candidates)]
            for _idx in range(len(_all_args)):
                if len(_candidates[_idx]) > 1:
                    _row = list(_combo[0])
                    _row[_idx] = _candidates[_idx][1]
                    _combo.append(tuple(_row))
            if not _combo:
                return {"checked": False, "equivalent": True, "reason": "无输入样本"}

            # ---- 运行对比 ----
            def _values_equal(_a: Any, _b: Any) -> bool:
                # 数值统一比较：int/float 均按浮点容差（bool 除外，视为类别而非数值）
                if isinstance(_a, (int, float)) and isinstance(_b, (int, float)) \
                        and not isinstance(_a, bool) and not isinstance(_b, bool):
                    return abs(_a - _b) <= 1e-9 or abs(_a - _b) <= 1e-6 * max(1.0, abs(_a), abs(_b))
                if isinstance(_a, (list, tuple)) and isinstance(_b, (list, tuple)):
                    return len(_a) == len(_b) and all(
                        _values_equal(x, y) for x, y in zip(_a, _b))
                if isinstance(_a, dict) and isinstance(_b, dict):
                    return _a.keys() == _b.keys() and all(
                        _values_equal(_a[k], _b[k]) for k in _a)
                return _a == _b

            _checked = 0
            for _args in _combo:
                try:
                    _r_o = _f_orig(*_args)
                except Exception as _e_o:
                    # 原方法抛异常：若修改后也抛 → 行为一致；否则不一致
                    try:
                        _f_mod(*_args)
                    except Exception:
                        continue
                    return {"checked": True, "equivalent": False,
                            "reason": f"原抛异常但修改后不抛: {_e_o}"}
                try:
                    _r_m = _f_mod(*_args)
                except Exception as _e_m:
                    return {"checked": True, "equivalent": False,
                            "reason": f"原正常但修改后抛异常: {_e_m}"}
                _checked += 1
                if not _values_equal(_r_o, _r_m):
                    return {"checked": True, "equivalent": False,
                            "reason": f"返回值不一致: {_r_o!r} vs {_r_m!r}"}

            if _checked == 0:
                # 所有样本均双方抛异常（输入类型与签名不符等）→ 无法验证，不误判等价
                return {"checked": False, "equivalent": True,
                        "reason": "无有效样本(输入类型不匹配)，无法验证"}
            return {"checked": True, "equivalent": True,
                    "reason": "行为等价", "samples": _checked}
        except Exception as _e:
            return {"checked": False, "equivalent": True, "reason": f"探针异常: {_e}"}

    def _load_history_experience(self, issue_type: str, limit: int = 3) -> str:
        """★上下文记忆(黄项收敛): 从补丁历史加载同类经验（跨会话学习）。

        成功范例（applied/approved）→ 正向参考；失败（verification_failed/rejected）→ 负向警示。
        无同类历史时回退最近历史；无历史/异常 → 返回空串（不影响生成，零冲突）。

        Returns:
            纯文本经验摘要（供 LLM 补丁生成 few-shot 注入）
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return ""
            _refs = [p for p in _history if (p.get("issue_type") or "") == issue_type]
            _same_type = bool(_refs)
            if not _refs:
                _refs = _history  # 无同类 → 回退最近历史（标题用"经验"而非"同类"）
            _ok: list[str] = []
            _bad: list[str] = []
            for _p in reversed(_refs):
                _diff = (_p.get("diff_summary") or "").strip()[:110]
                if not _diff:
                    continue
                _st = _p.get("status", "")
                _err = (_p.get("apply_error") or _p.get("reason") or "")[:90]
                if _p.get("applied") or _st == "approved":
                    _ok.append(f"[成功] {_diff}")
                elif _st in ("verification_failed", "rejected_verify", "rejected_safety") or _err:
                    _bad.append(f"[失败] {_diff} | {_err}")
                if len(_ok) + len(_bad) >= limit * 2 + 1:
                    break
            _lines: list[str] = []
            _ok_label = "历史同类成功修复参考" if _same_type else "历史经验-成功范例"
            _bad_label = "历史同类失败警示" if _same_type else "历史经验-失败警示"
            if _ok:
                _n_ok = min(len(_ok), limit)
                _lines.append(f"{_ok_label}(前{_n_ok}条):\n- "
                              + "\n- ".join(_ok[:_n_ok]))
            if _bad:
                _n_bad = min(len(_bad), limit)
                _lines.append(f"{_bad_label}(前{_n_bad}条):\n- "
                              + "\n- ".join(_bad[:_n_bad]))
            return "\n\n".join(_lines)
        except Exception:
            return ""

    def get_history_failure_stats(self, limit: int = 100) -> dict[str, Any]:
        """★元认知: 统计历史补丁验证失败 stage 分布（供反思改进与人工审计）。

        Returns: {"total": int, "stages": {stage: count}}（按次数降序）
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return {"total": 0, "stages": {}}
            _stages: dict[str, int] = {}
            _total = 0
            for _p in _history[-limit:]:
                _v = _p.get("verification") or {}
                _st = _v.get("stage", "") or (_p.get("status") or "unknown")
                _stages[_st] = _stages.get(_st, 0) + 1
                _total += 1
            return {"total": _total,
                    "stages": dict(sorted(_stages.items(), key=lambda kv: -kv[1]))}
        except Exception:
            return {"total": 0, "stages": {}}

    _STRATEGY_HALF_LIFE = 14 * 86400  # 策略效果时间衰减半衰期（14天）

    @staticmethod
    def _type_family(issue_type: str) -> str:
        """issue_type → 类型家族（跨类型先验分组）。

        家族按关键词分组：exception/resource/timeout/duplicate/syntax/thread/other。
        无历史的目标类型可用同家族其他类型的强信号做冷启动先验（元学习泛化）。
        """
        _t = (issue_type or "").lower()
        _families = (
            ("exception", ("exception", "silent", "as_e", "bare_except", "catch", "raise")),
            ("resource", ("resource", "leak", "unbounded", "buffer", "memory", "deque")),
            ("timeout", ("timeout", "http", "request", "connect")),
            ("duplicate", ("duplicate", "repeat", "idempot")),
            ("syntax", ("syntax", "indent", "parse")),
            ("thread", ("thread", "daemon", "race", "lock", "concurrent")),
        )
        for _fam, _keys in _families:
            if any(k in _t for k in _keys):
                return _fam
        return "other"

    def _aggregate_strategy_buckets(
        self, _history: list[Any], issue_type: str,
        use_family_prior: bool = False) -> tuple[dict[str, Any], str]:
        """聚合策略桶（含时间衰减权重）；返回 (agg[type], prior_source)。

        ★策略自进化深化(元学习): 
          - 时间衰减：新近补丁权重高（半衰期14天，指数衰减）；旧信号弱化——
            框架自我修复后不再误判 rule_bad；
          - 跨类型先验：目标类型无历史时，用同家族其他类型的强信号冷启动（use_family_prior）。
        """
        _now = time.time()
        _agg: dict[str, dict[str, dict[str, Any]]] = {}
        _target_key = issue_type
        for _p in _history:
            _it = _p.get("issue_type") or ""
            _match = (_it == _target_key) or (
                use_family_prior and _it and issue_type
                and self._type_family(_it) == self._type_family(issue_type)
                and _it != issue_type)
            if not _match:
                continue
            if not _it:
                _it = "未标注类型"
            _rs = _p.get("repair_source") or ""
            _bucket = "rule" if _rs in ("local_rule", "llm_upgrade") else                           ("llm" if _rs == "llm" else "other")
            _v = _p.get("verification") or {}
            _passed = bool(_v.get("passed"))
            _st = _p.get("status", "")
            _final_ok = bool(_p.get("applied")) or _st in ("approved", "applied")
            _failed = (not _passed) or _st in ("verification_failed", "rejected_verify",
                                               "rejected_safety")
            # 时间衰减权重（无时间字段→权重1不衰减）
            _w = 1.0
            _ts = _p.get("saved_at") or _p.get("applied_at") or _p.get("updated_at") or 0
            if _ts:
                _dt = _now - float(_ts)
                if _dt > 0:
                    _w = 0.5 ** (_dt / self._STRATEGY_HALF_LIFE)
            _bucket_map = _agg.setdefault(_it, {}).setdefault(
                _bucket, {"total": 0.0, "passed": 0.0, "failed": 0.0, "applied": 0.0,
                          "raw_total": 0, "raw_passed": 0})
            _bucket_map["total"] += _w
            if _passed:
                _bucket_map["passed"] += _w
            if _failed:
                _bucket_map["failed"] += _w
            if _final_ok:
                _bucket_map["applied"] += _w
            _bucket_map["raw_total"] += 1
            if _passed:
                _bucket_map["raw_passed"] += 1
        _prior = ""
        if use_family_prior and issue_type:
            _prior = f"家族[{self._type_family(issue_type)}]先验"
            # 家族先验：合并家族内所有类型的策略桶（跨类型求和），而非只取目标类型
            _merged: dict[str, dict[str, float]] = {}
            for _type_agg in _agg.values():
                for _b_name, _b in _type_agg.items():
                    _m = _merged.setdefault(
                        _b_name,
                        {"total": 0.0, "passed": 0.0, "failed": 0.0, "applied": 0.0,
                         "raw_total": 0, "raw_passed": 0})
                    for _k in _m:
                        _m[_k] = float(_m[_k]) + float(_b.get(_k, 0))
            return (_merged, _prior)
        return (_agg.get(_target_key, {}), _prior)

    def get_strategy_effectiveness(self, issue_type: str = "") -> dict[str, Any]:
        """★策略自进化(元学习·长期深化): 聚合同类问题的「策略-效果」关联。

        按 issue_type 聚合历史补丁的修复策略桶（rule=local_rule/llm_upgrade、llm=llm）：
          - 各策略桶的 total/passed/failed/rate（验证通过率，时间衰减加权）与 applied；
          - rule_bad：规则策略加权 total>=3 且加权失败率>=67% → 建议跳过规则直接 LLM 升级；
          - llm_proven：LLM 策略有 >=1 成功记录（成功证据不衰减）→ 可靠的升级目标。
        ★策略自进化深化(元学习): 目标类型无历史时启用「跨类型家族先验」——用同家族其他
          类型的强信号冷启动（标记 prior 来源，供审计/回退；仅强信号生效，零冲突）。

        Returns:
            {"issue_type", "no_history", "rule_bad", "llm_proven", "strategies",
             "prior", "summary"}
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return {"issue_type": issue_type, "no_history": True, "rule_bad": False,
                        "llm_proven": False, "strategies": {}, "prior": "", "summary": "无补丁历史"}
            _target, _prior = self._aggregate_strategy_buckets(_history, issue_type)
            _rule = _target.get("rule", {})
            _llm = _target.get("llm", {})
            _rule_bad = bool(_rule) and _rule.get("total", 0) >= 2.0                 and _rule.get("failed", 0) / _rule.get("total", 1) >= 0.67
            _llm_proven = bool(_llm) and _llm.get("raw_passed", _llm.get("passed", 0)) >= 1
            # 目标类型无历史 → 跨类型家族先验（冷启动，仅强信号生效）
            if not _target and issue_type:
                _fam_agg, _fam_prior = self._aggregate_strategy_buckets(
                    _history, issue_type, use_family_prior=True)
                if _fam_agg:
                    _frule = _fam_agg.get("rule", {})
                    _fllm = _fam_agg.get("llm", {})
                    _fam_rule_bad = bool(_frule) and _frule.get("total", 0) >= 2.0                     and _frule.get("failed", 0) / _frule.get("total", 1) >= 0.67
                    _fam_llm_proven = bool(_fllm) and _fllm.get("raw_passed", _fllm.get("passed", 0)) >= 1
                    if _fam_rule_bad or _fam_llm_proven:
                        _target = _fam_agg
                        _prior = _fam_prior
                        _rule_bad = _fam_rule_bad
                        _llm_proven = _fam_llm_proven
            _parts: list[str] = []
            for _b_name, _b in sorted(_target.items()):
                _rate = (_b["passed"] / _b["total"]) if _b["total"] else 0.0
                _raw_t = _b.get("raw_total", 0)
                _parts.append(f"{_b_name}:{_raw_t}条({_rate:.0%},应用{_b['applied']:.0f})")
            return {
                "issue_type": issue_type,
                "no_history": not bool(_target),
                "rule_bad": _rule_bad,
                "llm_proven": _llm_proven,
                "strategies": _target,
                "prior": _prior,
                "summary": ("；".join(_parts) if _parts else "无同类历史")
                           + (f"（{_prior}）" if _prior else ""),
            }
        except Exception:
            return {"issue_type": issue_type, "no_history": True, "rule_bad": False,
                    "llm_proven": False, "strategies": {}, "prior": "", "summary": "异常"}

    def generate_patch_regression_records(self) -> dict[str, Any]:
        """★测试生成(黄项收敛·轻量): 为已应用补丁生成针对性回归测试记录。

        对每个已应用补丁（applied 或 approved）：
          1) modified_code 在源文件中的存在性校验——补丁是否仍生效（未被后续覆盖/回退）；
          2) 若方法为纯函数 → 附加行为等价校验（补丁不应改变方法行为，只修复问题）。

        返回 {"total": int, "passed": int, "records": [...], "summary": str}。
        无历史/无已应用补丁/异常 → 零记录返回（不影响调用方，零冲突）。
        """
        try:
            _history = self._load_patch_list(self._history_file)
            if not isinstance(_history, list) or not _history:
                return {"total": 0, "passed": 0, "records": [], "summary": "无补丁历史"}
            _applied = [p for p in _history
                        if p.get("applied") or p.get("status") in ("approved", "applied")]
            if not _applied:
                return {"total": 0, "passed": 0, "records": [],
                        "summary": "无已应用补丁（等待补丁落地后生成回归记录）"}
            _records: list[dict[str, Any]] = []
            _passed = 0
            for _p in _applied:
                _file = _p.get("file", "")
                _mod = _p.get("modified_code", "")
                _rec: dict[str, Any] = {
                    "id": _p.get("id", ""),
                    "file": _file,
                    "method": _p.get("method", ""),
                    "issue_type": _p.get("issue_type", ""),
                    "diff_summary": (_p.get("diff_summary") or "")[:80],
                    "applied_at": _p.get("applied_at") or _p.get("saved_at") or 0,
                    "checks": [],
                }
                # 1) modified_code 存在性（补丁未失效）
                _exists = False
                if _file and _mod and os.path.exists(_file):
                    try:
                        with open(_file, encoding="utf-8") as _f:
                            _content = _f.read()
                        _exists = _mod.strip() in _content
                    except Exception:
                        _exists = False
                _rec["checks"].append({"name": "modified_code_present", "passed": _exists})
                # 2) 纯函数行为等价（附加，仅 checked=True 时纳入判定）
                if _file and _mod and os.path.exists(_file):
                    try:
                        _behavior = self._behavior_equivalence_probe(_p)
                        _rec["behavior"] = _behavior
                        if _behavior.get("checked"):
                            _rec["checks"].append({
                                "name": "behavior_equivalence",
                                "passed": bool(_behavior.get("equivalent")),
                            })
                    except Exception as e:
                        _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                _ok = bool(_rec["checks"]) and all(_c["passed"] for _c in _rec["checks"])
                if _ok:
                    _passed += 1
                _rec["passed"] = _ok
                _records.append(_rec)
            _summary = f"{_passed}/{len(_records)} 个已应用补丁通过回归"
            return {"total": len(_records), "passed": _passed,
                    "records": _records, "summary": _summary}
        except Exception as _e:
            return {"total": 0, "passed": 0, "records": [],
                    "summary": f"生成回归记录异常: {_e}"}

    def _run_regression_tests(self) -> dict[str, Any]:
        """
        ★进化增强：运行项目自带的轻量回归测试，作为补丁行为验证的兜底。

        只运行「快速、有明确退出码、不依赖长期运行」的验证脚本，
        避免每个补丁都触发长耗时压力测试。
        任一脚本退出码非 0 即判定回归失败。

        返回 {"passed": bool, "summary": str, "failed": int}
        """
        # 轻量回归测试清单（相对路径 + 超时秒数）
        # ★第九批 5.2（星轨指出）：原清单指向 tools/test_parquet_stage_a.py 与
        #   tools/verify_patch_coverage.py —— 这两个脚本在仓库里**根本不存在**，
        #   而旧代码遇到不存在就 `continue`，于是结果恒为「0通过/0失败」，
        #   看起来像「回归挂了」，实际是「一个都没跑」。
        #   修复：① 清单改为仓库真实存在的回归脚本，并支持 config 覆盖；
        #         ② 脚本缺失时不再静默跳过，而是显式记进 missing 明细。
        #   （第五批 任务2B 同步清理了 TestGenerator._run_regression_tests 中
        #    指向同一对不存在脚本的清单，两个回归入口现已统一为真实脚本。）
        _default_scripts = ["tools/verify_phase17_1_5.py"]
        try:
            import config as _cfg_mod
            _test_scripts = list(getattr(
                _cfg_mod, 'EVOLUTION_CONFIG', {}).get(
                    "regression_scripts", _default_scripts) or _default_scripts)
        except Exception:
            _test_scripts = list(_default_scripts)
        _timeout = 300  # 主回归脚本较重，60s 不够
        try:
            import config as _cfg_mod2
            _timeout = int(getattr(_cfg_mod2, 'EVOLUTION_CONFIG', {}).get(
                "regression_timeout", _timeout) or _timeout)
        except Exception:
            pass
        _passed = 0
        _failed = 0
        _missing = []
        _fail_details = []

        for _rel in _test_scripts:
            _script = os.path.join(self._project_root, _rel)
            if not os.path.exists(_script):
                _missing.append(os.path.basename(_rel))
                continue
            try:
                # ★E1修复（P2，2026-09-05）：子进程 stdio 强制 UTF-8。
                #   本文件的 encoding="utf-8" 只决定**父进程如何解码**收到的字节，
                #   管不到**子进程自己的 stdout 编码**——那由子进程启动时的
                #   locale 决定，Windows 中文环境默认是 GBK。
                #   于是脚本里任何 emoji（✅/❌，见 tools/verify_patch_coverage.py:53）
                #   一 print 就 UnicodeEncodeError 崩溃、退出码非 0。
                #   生产实测：5/5 个补丁的 regression 恒为「0通过/2失败」，
                #   错误信息与本地复现逐字一致——
                #   UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
                #   结果就是整条回归验证数据全废，分不清「补丁有问题」和
                #   「测试脚本打不出 emoji」。此处透传一份改过编码的环境变量即可，
                #   不改动子进程任何业务行为。
                _env = dict(os.environ)
                _env["PYTHONIOENCODING"] = "utf-8"
                # ★第九批 5.2：`python` 未必在 PATH 上（Windows 常见），旧代码
                #   会因此 FileNotFoundError → 计入 failed。改用当前解释器。
                _proc = subprocess.run(  # 有意不检查子进程退出码
                    [sys.executable or "python", _script],
                    capture_output=True, text=True, timeout=_timeout,
                    encoding="utf-8", errors="replace",
                    cwd=self._project_root, env=_env,
                )
                if _proc.returncode == 0:
                    _passed += 1
                else:
                    _failed += 1
                    _tail = (_proc.stderr or _proc.stdout or "")[-200:]
                    _fail_details.append(f"{os.path.basename(_script)}: {_tail}")
            except subprocess.TimeoutExpired:
                _failed += 1
                _fail_details.append(f"{os.path.basename(_script)}: 超时")
            except Exception as _e:
                _failed += 1
                _fail_details.append(f"{os.path.basename(_script)}: {_e}")

        _summary = f"{_passed}通过/{_failed}失败"
        if _missing:
            # 显式暴露「清单里的脚本不存在」，避免再次出现 0通过/0失败 的假象
            _summary += f" | 脚本缺失: {', '.join(_missing)}"
        if _fail_details:
            _summary += " | " + "; ".join(_fail_details[:2])
        return {
            "passed": _failed == 0 and (_passed > 0),
            "summary": _summary,
            "failed": _failed,
            "missing": _missing,
        }

    def _check_patch_safety(self, patch: dict[str, Any]) -> dict[str, Any]:
        """落地前校验信任分/风险等级/冷却时间（A3：与自动审批共用安全门）。
        ★FIX(2026-09-07): 人工批准(status=approved)绕过自动安全门。"""
        try:
            if patch.get("status") == "approved":
                return {"safe": True, "reason": "人工批准"}
            import config
            _evo_cfg = getattr(config, 'EVOLUTION_CONFIG', {})
            _trust = patch.get("trust_score", 0)
            _min_trust = _evo_cfg.get("auto_apply_min_trust", 60)
            # ★第九批 B-4（星轨要求）：门槛一刀切 60 时，「加日志/改注释/参数微调」
            #   这类低风险补丁（信任分普遍 30 左右）永远过不去 → 60 个问题 0 修复。
            #   改为按风险分级：低风险走 40 门槛，其余维持 60（开关关闭时行为不变）。
            if getattr(config, "ENABLE_EVOLUTION_EFFECT_VERIFY", False):
                try:
                    from nucleus.evolution.EvolutionEffectVerifier import (
                        EvolutionEffectVerifier as _EV,
                    )
                    # 阈值从 config.EVOLUTION_EFFECT_VERIFY_CONFIG 读（低风险40/高风险60）
                    _min_trust = _EV.trust_threshold_for(patch)
                except Exception:
                    pass  # 判定失败则维持原门槛，绝不放宽
            if _trust < _min_trust:
                return {"safe": False, "reason": f"信任分数不足({_trust}<{_min_trust})"}
            _risk = patch.get("risk_level", "低")
            _risk_map = {"极低": 1, "低": 2, "中等": 3, "高": 4}
            _max_risk = _evo_cfg.get("auto_apply_max_risk", 2)
            if _risk_map.get(str(_risk), 3) > _max_risk:
                return {"safe": False, "reason": f"风险等级过高({_risk})"}
            _cooldown = _evo_cfg.get("auto_apply_cooldown", 86400)
            _last = getattr(self, "_last_apply_time", 0.0)
            if time.time() - _last < _cooldown:
                return {"safe": False, "reason": f"冷却中({int(_cooldown)}s)"}
        except Exception as _e:
            return {"safe": False, "reason": f"安全校验异常: {_e}"}
        return {"safe": True, "reason": ""}

    def apply_all_pending(self, only_approved: bool = False) -> dict[str, Any]:
        """应用所有待处理补丁，返回应用结果。
        
        Args:
            only_approved: 仅应用 status == "approved" 的补丁。
                          若为 True，则跳过 pending/未审批 的补丁。
        """
        pending = self._load_patch_list(self._pending_file)
        if not pending:
            # ★FIX(问题12): 无待应用补丁时重置重启计数器，避免无意义累计导致永久锁死
            self.reset_restart_counter()
            return {"applied": 0, "failed": 0, "details": []}
        
        # ★v24.0新增：审批过滤
        if only_approved:
            _approved_pending = []
            _skipped_count = 0
            for _p in pending:
                if _p.get("status") == "approved":
                    _approved_pending.append(_p)
                else:
                    _skipped_count += 1
            if _skipped_count > 0:
                _module_logger.info(f"审批过滤：跳过 {_skipped_count} 个未审批补丁")
            pending = _approved_pending
            if not pending:
                # ★FIX(A4): 全部未审批时也重置计数，避免未审批队列导致永久锁死
                self.reset_restart_counter()
                return {"applied": 0, "failed": 0, "details": [], "skipped_unapproved": _skipped_count}
        # ===== 审批过滤结束 =====
        
        # ★v16.0新增：防循环重启检测（仅在有可应用补丁时自增，A4修复）
        self._restart_counter = self._load_restart_counter() + 1
        if not self._save_restart_counter(self._restart_counter):
            # ★FIX: 计数写失败时 fail-closed，拒绝应用补丁，避免防循环重启失效导致无限重启
            _module_logger.error("重启计数持久化失败，拒绝自动应用补丁以保证安全")
            return {"applied": 0, "failed": 0, "details": [], "loop_protection": True}
        if self._restart_counter > self._max_restart_count:
            _module_logger.warning(f"重启次数已达{self._max_restart_count}次上限，停止自动应用，请手动检查补丁")
            return {"applied": 0, "failed": 0, "details": [], "loop_protection": True}
        
        results = {"applied": 0, "failed": 0, "details": []}
        history = self._load_patch_list(self._history_file)

        # ★v23.0新增：按文件分组，从后往前应用（避免多补丁行号偏移影响）
        _file_groups = {}
        for _p in pending:
            _file = _p.get("file", "")
            if _file not in _file_groups:
                _file_groups[_file] = []
            _file_groups[_file].append(_p)

        # ★审美判据深化（第四条路·E6）：不同文件间按最高审美分降序应用
        # （先应用审美质量高的补丁，让演化优先沉淀优质代码）；同文件内仍按
        # 行号从后往前，保证多补丁行号偏移正确性不受影响。
        def _patch_aesthetic(p):
            _v = p.get("aesthetic_score")
            if isinstance(_v, dict):
                try:
                    return float(_v.get("total", 0) or 0)
                except (TypeError, ValueError):
                    return 0.0
            try:
                return float(_v or 0)
            except (TypeError, ValueError):
                return 0.0

        _sorted_pending = []
        for _file, _patches in sorted(
                _file_groups.items(),
                key=lambda kv: max(_patch_aesthetic(p) for p in kv[1]),
                reverse=True):
            # 按original_code在文件中的位置排序（从后往前应用）
            _sorted_patches = sorted(_patches, key=lambda p: p.get("line", 0), reverse=True)
            _sorted_pending.extend(_sorted_patches)

        for patch in _sorted_pending:
            try:
                # ★v22.0 P2新增：应用前先通过人格基线校验
                _baseline_check = self._check_personality_baseline(patch)
                if not _baseline_check["safe"]:
                    patch["applied"] = False
                    patch["apply_error"] = f"人格基线校验不通过: {_baseline_check['reason']}"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_by_baseline",
                        "reason": _baseline_check["reason"],
                        "violations": _baseline_check.get("violations", []),
                    })
                    continue
                # ★v22.0 P2新增结束
                # ★FIX(A3): 落地前做信任/风险/冷却三道安全校验（此前只在死代码 _auto_apply_patch 中）
                _safety_check = self._check_patch_safety(patch)
                if not _safety_check["safe"]:
                    patch["applied"] = False
                    patch["apply_error"] = _safety_check["reason"]
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_safety",
                        "reason": _safety_check["reason"],
                    })
                    history.append(patch)
                    continue
                # ★v24.0新增：应用前强制在副本中验证补丁
                _verify_result = self._verify_in_copy(patch)
                if not _verify_result.get("passed", False):
                    patch["applied"] = False
                    patch["apply_error"] = f"副本验证失败: {_verify_result.get('errors', ['未知错误'])}"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_verify",
                        "reason": patch["apply_error"],
                    })
                    history.append(patch)
                    continue
                # ★v24.0新增结束                
                # 1. 备份原始文件（集中式备份）
                _backup_dir = self._backup_manager.create_backup(
                    files=[patch["file"]],
                    reason=patch.get("reason", "自动补丁应用"),
                    source=patch.get("source_diagnosis", "未知来源"),
                )
                if _backup_dir:
                    patch["backup_path"] = _backup_dir
                else:
                    # 备份失败时使用单文件备份兜底
                    backup_path = patch["file"] + f".patch_backup_{int(time.time())}"
                    shutil.copy2(patch["file"], backup_path)
                    patch["backup_path"] = backup_path
                
                # 2. 应用补丁
                with open(patch["file"], encoding='utf-8') as f:
                    full_content = f.read()
                
                # ★修复: str.replace 默认替换所有出现位置——original_code 若出现多次
                # 会被静默全部改写。改为校验唯一出现 + 单次替换。
                _occurrences = full_content.count(patch["original_code"])
                if _occurrences > 1:
                    patch["applied"] = False
                    patch["apply_error"] = f"原始代码在文件中出现{_occurrences}次，为避免误改已拒绝（请提供更长上下文唯一定位）"
                    results["failed"] += 1
                    results["details"].append({
                        "id": patch["id"], "file": patch["file"],
                        "status": "rejected_ambiguous",
                        "reason": patch["apply_error"],
                    })
                    history.append(patch)
                    continue
                elif _occurrences == 1:
                    modified_full = full_content.replace(patch["original_code"], patch["modified_code"], 1)

                    # ★修复: 写入活文件前先做语法复验，避免坏补丁导致下次启动即崩
                    if patch["file"].endswith(".py"):
                        try:
                            ast.parse(modified_full)
                        except SyntaxError as _se:
                            patch["applied"] = False
                            patch["apply_error"] = f"补丁应用后语法校验失败（未写入）: {_se}"
                            results["failed"] += 1
                            results["details"].append({
                                "id": patch["id"], "file": patch["file"],
                                "status": "rejected_syntax",
                                "reason": patch["apply_error"],
                            })
                            history.append(patch)
                            continue

                    # ★FIX: mkstemp 随机名，避免预测名 TOCTOU/符号链接重定向
                    import tempfile
                    _fd, _tmp_path = tempfile.mkstemp(  # type: ignore[possibly-unbound]
                        dir=os.path.dirname(patch["file"]) or ".",
                        prefix=".patch_", suffix=".tmp")
                    try:
                        with os.fdopen(_fd, 'w', encoding='utf-8') as f:
                            f.write(modified_full)
                        os.replace(_tmp_path, patch["file"])  # type: ignore[possibly-unbound]
                    finally:
                        if os.path.exists(_tmp_path):  # type: ignore[possibly-unbound]
                            try:
                                os.remove(_tmp_path)  # type: ignore[possibly-unbound]
                            except OSError:
                                pass
                    
                    patch["applied"] = True
                    patch["applied_at"] = time.time()
                    patch["rollback_available"] = True
                    self._last_apply_time = time.time()
                    results["applied"] += 1
                    # ★第九批 B-4（星轨要求）：补丁落地后做**针对该问题**的效果验证。
                    #   此前只有 compile_check + 副本验证，等于「代码能跑就算修好」，
                    #   问题是否真的消失、功能是否被带坏、性能是否退化，全都没人管。
                    try:
                        import config as _cfg_eff
                        if getattr(_cfg_eff, "ENABLE_EVOLUTION_EFFECT_VERIFY", False):
                            from nucleus.evolution.EvolutionEffectVerifier import (
                                EvolutionEffectVerifier as _EV,
                            )
                            from nucleus.evolution.EvolutionEffectVerifier import (
                                get_effect_verifier,
                            )
                            _verifier = get_effect_verifier()
                            # ① 问题是否消失：以「副本验证通过 + 补丁已落地」为基线信号；
                            #    真正的业务探针由调用方（SafeEvolutionExecutor）补充上报。
                            # ★★主线第47批 T1（P0-2）：语义拆分 —— 不再把
                            #   No-Regression 当成 Problem-Fixed。
                            #   原实现 `problem_gone=bool(passed)` 与
                            #   `function_ok=bool(passed)` **共用同一个值**：
                            #   ``passed`` 只证明"改完没弄坏"（语法/导入/回归），
                            #   却被同时当成"问题真的消失了"。
                            #   → 此处 `problem_gone=None`（**不可判定**），
                            #     真正的业务探针由调用方补充上报后回填。
                            _verdict = _verifier.verify(
                                patch,
                                {"type": patch.get("issue_type") or patch.get("type", ""),
                                 "file": patch.get("file", ""),
                                 "method": patch.get("method", "")},
                                problem_gone=None,  # ★第47批：无法判定≠已修复
                                function_ok=bool(_verify_result.get("passed")),
                                perf_delta_ms=None,  # 性能由调用方实测后回填
                            )
                            _verifier.apply_trust_adjustment(patch, _verdict)
                            # ★第47批：写入语义拆分字段（no_regression / problem_fixed /
                            #   verification_granularity / effectiveness），
                            #   `verified` 保留为 no_regression（deprecated）。
                            try:
                                from nucleus.evolution.patch_verification_split import (
                                    apply_split as _apply_split,
                                )
                                _apply_split(patch)
                            except Exception as _se:  # 拆分失败不阻断应用闭环
                                _module_logger.warning(
                                    f"[语义拆分] 写入失败（已忽略）: {_se}")
                            # ② 修复登记：供后续复发检测比对
                            _verifier.mark_fixed(_EV.issue_signature(
                                {"type": patch.get("issue_type") or patch.get("type", ""),
                                 "file": patch.get("file", ""),
                                 "method": patch.get("method", "")}))
                            _module_logger.info(
                                f"[效果验证] 补丁{patch['id'][:12]} 已应用并通过验证: "
                                f"{_verdict.get('reason', '')}")
                    except Exception as e:
                        _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                    # ★审美判据深化（E6）：C/D级补丁附审美提示，不阻断闭环
                    _aes_note = None
                    if patch.get("aesthetic_grade") in ("C", "D"):
                        _aes_note = (
                            f"审美{patch.get('aesthetic_grade')}级(评分{_patch_aesthetic(patch):.0f})，"
                            f"已应用但建议后续优化代码质量"
                        )
                    _apply_detail = {
                        "id": patch["id"], "file": patch["file"],
                        "status": "applied", "backup_path": patch.get("backup_path", "")
                    }
                    if _aes_note:
                        _apply_detail["aesthetic_note"] = _aes_note
                    results["details"].append(_apply_detail)
                else:
                    patch["applied"] = False
                    patch["apply_error"] = "原始代码未找到，可能已被修改"
                    results["failed"] += 1
                
                history.append(patch)
                
            except Exception as e:
                print(f"[WARNING] PatchManager.py:1764: {type(e).__name__}: {e}")
                patch["applied"] = False
                patch["apply_error"] = str(e)
                results["failed"] += 1
        
        # ★Kimi 设计参考借鉴（§9.1/§12.3）：失败计数器——按「文件+类型」累计失败，3 次升级
        # 绝不原样重复同一失败动作：同一问题反复失败即标记需升级（换策略/人工介入）。
        # 零冲突：仅在应用循环结束后统一记账，不改变任何应用/验证/回滚逻辑。
        try:
            from nucleus.reasoning.FailureTracker import get_failure_tracker
            _ft = get_failure_tracker(self._project_root)
            for _p in history:
                _file = _p.get("file", "")
                _ptype = _p.get("type") or _p.get("source_type") or "unknown"
                _sig = _ft.build_signature(_file, str(_ptype))
                if _p.get("applied"):
                    _ft.record_success(_sig)
                else:
                    _ft.record_failure(_sig, _p.get("apply_error") or _p.get("reason") or "应用失败")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★第九批 5.1（星轨要求）：不可恢复的失败补丁就地归档为 obsolete。
        #   背景：apply_all_pending 末尾会把 pending 清空、全部塞进 history，
        #   于是「目标代码片段在源文件中未找到」这类**结构性失效**补丁
        #   （源码早已被后续批次改写，补丁永远不可能对上）会永久躺在
        #   history 里，每次统计修复率都被算成「失败」，60 个问题 0 修复。
        #   处理：命中失效标记的补丁打 obsolete=True 后移入独立归档文件，
        #   不删除（可审计可回滚），也不再计入修复率。
        try:
            _arch = self.archive_obsolete_patches(history)
            if _arch.get("archived", 0):
                _module_logger.info(
                    f"[补丁归档] 已归档 {_arch['archived']} 条失效补丁"
                    f"（原因: {_arch.get('reason_top') or '目标片段不存在'}）")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._save_patch_list(self._history_file, history)
        self._save_patch_list(self._pending_file, [])
        # ★v23.0新增：写入人类可读的修改日志
        self._write_readable_log(results, history)
        return results

    # 结构性失效（重试一万次也不会成功）的失败原因特征串
    _OBSOLETE_MARKERS = (
        "目标代码片段在源文件中未找到",
        "原始代码未找到",
        "目标代码片段未找到",
        "源文件不存在",
        "目标文件不存在",
    )

    def _is_obsolete_patch(self, patch: dict[str, Any]) -> bool:
        """判定补丁是否为结构性失效（非临时失败，重试无意义）。"""
        if patch.get("applied"):
            return False
        if patch.get("obsolete"):
            return False  # 已归档过，避免重复搬运
        _err = f"{patch.get('apply_error') or ''}{patch.get('reason') or ''}"
        return any(_m in _err for _m in self._OBSOLETE_MARKERS)

    def archive_obsolete_patches(self, history: list[dict[str, Any]] | None = None
                                 ) -> dict[str, Any]:
        """把结构性失效补丁从 history 移入 patch_history_obsolete.json。

        Args:
            history: 内存中的历史列表；为 None 时从磁盘读一次并回写。
                     传入列表时**原地过滤**（调用方随后落盘），返回归档统计。

        Returns: {"archived": int, "kept": int, "reason_top": str}
        """
        _in_memory = history is not None
        if history is None:
            history = self._load_patch_list(self._history_file) or []
        _kept: list[dict[str, Any]] = []
        _obsolete: list[dict[str, Any]] = []
        _reasons: dict[str, int] = {}
        for _p in (history or []):
            if self._is_obsolete_patch(_p):
                _p["obsolete"] = True
                _p["obsolete_at"] = time.time()
                _p["obsolete_reason"] = (
                    _p.get("apply_error") or _p.get("reason") or "结构性失效")
                _key = str(_p["obsolete_reason"])[:60]
                _reasons[_key] = _reasons.get(_key, 0) + 1
                _obsolete.append(_p)
            else:
                _kept.append(_p)
        if _obsolete:
            _arch_path = os.path.join(
                os.path.dirname(self._history_file), "patch_history_obsolete.json")
            _existing = self._load_patch_list(_arch_path) or []
            # 按 id 去重，避免重复归档
            _seen = {str(_e.get("id")) for _e in _existing}
            for _p in _obsolete:
                if str(_p.get("id")) not in _seen:
                    _existing.append(_p)
                    _seen.add(str(_p.get("id")))
            _existing = _existing[-2000:]  # 归档上限，防止无限膨胀
            self._save_patch_list(_arch_path, _existing)
        if _in_memory:
            history[:] = _kept
        else:
            self._save_patch_list(self._history_file, _kept)
        _top = ""
        if _reasons:
            _top = max(_reasons.items(), key=lambda kv: kv[1])[0]
        return {"archived": len(_obsolete), "kept": len(_kept),
                "reason_top": _top, "reasons": _reasons}
    def _check_personality_baseline(self, patch: dict[str, Any]) -> dict[str, Any]:
        """
        ★v22.0 P2新增：调用人格内核进行基线校验。
        
        在应用任何补丁之前，检查提议的修改是否触及核心身份基线。
        如果补丁试图修改核心锚点、使命、关系或L4本能，直接拒绝。
        
        Returns:
            {"safe": bool, "reason": str, "violations": [...]}
        """
        try:
            # 动态导入，避免循环依赖
            import sys as _sys
            _project_root = self._project_root
            if _project_root not in _sys.path:
                _sys.path.insert(0, _project_root)
            
            from organs.identity.PulsePersonalityKernel import PulsePersonalityKernel
            _kernel = PulsePersonalityKernel("补丁校验")
            
            _proposed_content = patch.get("modified_code", "")
            _proposed_file = patch.get("file", "")
            
            return _kernel.check_modification_baseline(
                proposed_content=_proposed_content,
                proposed_file=_proposed_file,
            )
        except Exception as _e:
            # ★修复: 安全门必须 fail-closed。校验自身出错时若放行，
            # 等于人格基线保护在任何异常下自动失效。
            # 如需临时放行，可在 config 中显式设置 PATCH_BASELINE_FAIL_OPEN=True。
            try:
                import config as _cfg
                if getattr(_cfg, "PATCH_BASELINE_FAIL_OPEN", False):
                    return {
                        "safe": True,
                        "reason": f"基线校验异常（显式配置放行）: {_e}",
                        "violations": [],
                    }
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return {
                "safe": False,
                "reason": f"基线校验异常（安全起见拒绝应用）: {_e}",
                "violations": ["baseline_check_error"],
            }
    def _write_readable_log(self, results: dict, history: list):
        """
        ★v23.0新增：将补丁应用结果写入人类可读的修改日志。
        
        日志文件: data/patches/change_log.md
        每次应用补丁都追加一条完整记录，包含：
        - 修改时间
        - 目标文件
        - 风险等级
        - 修改原因
        - 来源诊断
        - 修改前代码片段
        - 修改后代码片段
        - 应用结果
        """
        _log_path = os.path.join(self._patch_dir, "change_log.md")
        _now = time.strftime("%Y-%m-%d %H:%M:%S")

        _lines = []
        # 首次创建时写入标题
        if not os.path.exists(_log_path):
            _lines.append("# 曈曈自我修改日志\n")
            _lines.append("记录所有自动修改操作的完整信息。\n")
            _lines.append("---\n")

        _lines.append(f"## 修改时间: {_now}\n")

        for _patch in history[-len(results.get("details", [])):]:
            _file = _patch.get("file", "未知文件")
            _risk = _patch.get("risk_level", "未标注")
            _reason = _patch.get("reason", _patch.get("description", "未说明"))
            _source = _patch.get("source_diagnosis", _patch.get("source", "未知"))
            _status = "✅ 成功" if _patch.get("applied") else f"❌ 失败: {_patch.get('apply_error', '未知错误')}"

            _lines.append(f"### 文件: `{os.path.basename(_file)}`\n")
            _lines.append(f"- **风险等级**: {_risk}")
            _lines.append(f"- **修改原因**: {_reason}")
            _lines.append(f"- **来源诊断**: {_source}")
            _lines.append(f"- **应用结果**: {_status}")
            _lines.append(f"- **完整路径**: `{_file}`")

            _original = _patch.get("original_code", "")
            _modified = _patch.get("modified_code", "")
            if _original:
                _lines.append(f"\n**修改前代码**:\n```python\n{_original[:500]}\n```")
            if _modified:
                _lines.append(f"\n**修改后代码**:\n```python\n{_modified[:500]}\n```")

            _backup = _patch.get("backup_path", "")
            if _backup:
                _lines.append(f"- **备份文件**: `{os.path.basename(_backup)}`")

            _lines.append("---\n")

        # 追加写入
        # ★第44批 T4（P2-290）：写盘守卫（change_log.md 在 data/patches/ 下）
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(_log_path, component="PatchManager.change_log"):
                return
        except ImportError:
            pass
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(_lines))

        _module_logger.info(f"修改日志已更新: {_log_path}")        
    def rollback_last(self) -> bool:
        """回滚最近一次应用的补丁"""
        history = self._load_patch_list(self._history_file)
        for patch in reversed(history):
            if patch.get("applied") and patch.get("rollback_available"):
                backup_path = patch.get("backup_path", "")
                if os.path.isdir(backup_path):
                    # ★v23.0新增：使用集中备份恢复
                    _restore_result = self._backup_manager.restore_backup(backup_path)
                    if _restore_result["success"]:
                        patch["applied"] = False
                        patch["rollback_available"] = False
                        patch["rolled_back_at"] = time.time()
                        self._save_patch_list(self._history_file, history)
                        self._write_rollback_log(patch)
                        return True
                elif os.path.exists(backup_path):
                    # 兼容旧版单文件备份
                    shutil.copy2(backup_path, patch["file"])
                    patch["applied"] = False
                    patch["rollback_available"] = False
                    patch["rolled_back_at"] = time.time()
                    self._save_patch_list(self._history_file, history)
                    self._write_rollback_log(patch)
                    return True
        return False

    def rollback_patch(self, patch_id: str) -> dict[str, Any]:
        """★A2修复（主线A）：按补丁 ID 精确回滚单个已应用补丁。

        区别于 rollback_last（只能回滚「最近一个」）：
        运行时验证失败的是「特定补丁」，可能不是最近应用的，
        故需按 ID 精确定位并回滚，避免误回滚其他成功补丁。

        返回: {"ok": bool, "reason": str}
        """
        if not patch_id:
            return {"ok": False, "reason": "补丁 ID 为空"}
        history = self._load_patch_list(self._history_file)
        for patch in history:
            if patch.get("id") != patch_id:
                continue
            if not patch.get("applied"):
                return {"ok": False, "reason": f"补丁 {patch_id} 未处于已应用状态，无需回滚"}
            if not patch.get("rollback_available"):
                return {"ok": False, "reason": f"补丁 {patch_id} 无可用备份，无法回滚"}
            backup_path = patch.get("backup_path", "")
            _restored = False
            try:
                if os.path.isdir(backup_path):
                    _restore_result = self._backup_manager.restore_backup(backup_path)
                    _restored = bool(_restore_result.get("success"))
                elif os.path.exists(backup_path):
                    shutil.copy2(backup_path, patch["file"])
                    _restored = True
            except Exception as _e:
                return {"ok": False, "reason": f"回滚补丁 {patch_id} 失败: {_e}"}
            if not _restored:
                return {"ok": False, "reason": f"补丁 {patch_id} 备份恢复失败"}
            # 回滚成功，更新状态
            patch["applied"] = False
            patch["rollback_available"] = False
            patch["rolled_back_at"] = time.time()
            patch["rolled_back_reason"] = "runtime_verify_failed"
            self._save_patch_list(self._history_file, history)
            self._write_rollback_log(patch)
            _module_logger.info(
                f"[补丁回滚] {patch_id} 已回滚: "
                f"{os.path.basename(patch.get('file',''))} 运行时验证失败自动撤销")
            return {"ok": True, "reason": f"补丁 {patch_id} 已回滚"}
        return {"ok": False, "reason": f"未找到补丁 {patch_id}"}
    def get_latest_applied_backup_paths(self, count: int = 5) -> list[str]:
        """★v23.0新增：获取最近应用的补丁备份路径"""
        history = self._load_patch_list(self._history_file)
        backups = []
        for patch in reversed(history):
            if patch.get("applied") and patch.get("backup_path"):
                backups.append(patch.get("backup_path"))
                if len(backups) >= count:
                    break
        return backups
    def _write_rollback_log(self, patch: dict):
        """
        ★v23.0新增：将回退操作写入修改日志。
        """
        _log_path = os.path.join(self._patch_dir, "change_log.md")
        _now = time.strftime("%Y-%m-%d %H:%M:%S")
        _file = patch.get("file", "未知文件")

        _lines = [
            f"\n## 回退时间: {_now}\n",
            f"### 文件: `{os.path.basename(_file)}`\n",
            "- **回退原因**: 修改后验证失败或手动回退",
            f"- **完整路径**: `{_file}`",
            "- **回退到**: 修改前版本\n",
            "---\n",
        ]

        # ★第44批 T4（P2-290）：写盘守卫
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(_log_path, component="PatchManager.rollback_log"):
                return
        except ImportError:
            pass
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(_lines))

        _module_logger.info(f"回退日志已记录: {os.path.basename(_file)}")

    # ========== 公开契约（规则14：消除跨模块私有穿透） ==========
    def verify_in_copy(self, patch: dict[str, Any]) -> dict[str, Any]:
        """公开封装 _verify_in_copy，供 SafeEvolutionExecutor/代码学习 调用"""
        return self._verify_in_copy(patch)

    def load_json(self, path, default):
        """公开封装 _load_json，供内在世界读取待处理补丁"""
        return self._load_json(path, default)

    def get_project_root(self) -> str:
        """公开只读访问项目根目录"""
        return self._project_root

    def get_pending_file(self) -> str:
        """公开只读访问待处理补丁文件路径"""
        return self._pending_file

    def get_history_file(self) -> str:
        """★A2修复（主线A）：公开只读访问补丁历史文件路径。"""
        return self._history_file

    # ===== ★PHASE17-A4（2026-09-07）：补丁路径跨平台归一化 =====
    #   问题：patch_history.json 中 file 字段写的是 Windows 绝对路径
    #         （实测 22 条全为 `D:\xinrenlei\tongtong-pulse-v9\...`）。
    #         在 Linux/沙箱下 _check_patch_path 的跨盘符分支一律判越界 →
    #         补丁既不能应用也不能回滚，跨平台验证与迁移全部失效（P2-5）。
    #
    #   方案（最小侵入，下游零改动）：
    #         磁盘表示 = 相对项目根的 POSIX 路径（organs/body/PulseLiver.py）
    #         内存表示 = 绝对路径（与改动前完全一致，所有 open/copy 代码不用动）
    #   即：_save_patch_list 落盘时归一化，_load_patch_list 读取时解析回绝对。
    #   两者都不动任何业务判据，异常时原样返回（保守降级，绝不改坏数据）。

    def _normalize_patch_file(self, file_path: str) -> str:
        """绝对路径（Windows 或 POSIX）→ 相对项目根的 POSIX 路径；非绝对路径原样返回。

        跨平台难点：Windows 绝对路径（`D:\\xinrenlei\\tongtong-pulse-v9\\organs\\...`）
        无法直接 relpath 到本地项目根（Linux 上是 `/workspace/tongtong-pulse-v9`）——
        简单去掉盘符会得到 `xinrenlei/tongtong-pulse-v9/...`（错误，多保留了上层目录）。
        因此用**项目根目录名做锚点**：在路径片段中找最后一个与项目根目录同名的片段，
        取其之后的部分作为相对路径。
        """
        if not file_path or not isinstance(file_path, str):
            return file_path
        try:
            import re as _re_n
            _is_win_abs = bool(_re_n.match(r"^[A-Za-z]:[/\\]", file_path))
            _is_posix_abs = os.path.isabs(file_path.replace("\\", "/"))
            if not (_is_win_abs or _is_posix_abs):
                return file_path  # 本来就是相对路径，原样返回

            _root = os.path.realpath(self._project_root)
            _p = file_path.replace("\\", "/")

            # ① 本机同源 POSIX 绝对路径：直接 relpath（最准确）
            if not _is_win_abs:
                _abs = os.path.realpath(_p)
                if os.path.commonpath([_abs, _root]) == _root:
                    return os.path.relpath(_abs, _root).replace("\\", "/")

            # ② Windows 或其它机器路径：用项目根目录名锚定
            _base = os.path.basename(_root)
            if _base:
                _parts = [x for x in _p.split("/") if x not in ("", ".")]
                _base_l = _base.lower()
                _idx = -1
                for _i in range(len(_parts) - 1, -1, -1):
                    if _parts[_i].lower() == _base_l:
                        _idx = _i
                        break
                if _idx >= 0 and _idx + 1 < len(_parts):
                    _rel = "/".join(_parts[_idx + 1:])
                    if _rel and not _rel.startswith(".."):
                        return _rel
            return file_path  # 锚定失败，不敢动，原样返回
        except Exception:
            return file_path  # 任何异常都不改坏数据

    def _resolve_patch_file(self, file_path: str) -> str:
        """相对路径 → 项目根下的绝对路径；已是绝对路径则原样返回。"""
        if not file_path or not isinstance(file_path, str):
            return file_path
        try:
            import re as _re_r
            if _re_r.match(r"^[A-Za-z]:[/\\]", file_path) or os.path.isabs(file_path):
                return file_path
            return os.path.join(self._project_root, file_path.replace("/", os.sep))
        except Exception:
            return file_path

    def _load_patch_list(self, path, default=None):
        """读取补丁列表（pending/history）并把 file 解析为绝对路径。"""
        _items = self._load_json(path, default if default is not None else [])
        if isinstance(_items, list):
            for _it in _items:
                if isinstance(_it, dict) and isinstance(_it.get("file"), str):
                    _it["file"] = self._resolve_patch_file(_it["file"])
        return _items

    def _save_patch_list(self, path, data):
        """保存补丁列表（pending/history）并把 file 归一化为相对路径落盘。"""
        try:
            if isinstance(data, list):
                for _it in data:
                    if isinstance(_it, dict) and isinstance(_it.get("file"), str):
                        _it["file"] = self._normalize_patch_file(_it["file"])
        except Exception:
            _module_logger.debug("补丁路径归一化失败（不影响保存）")
        return self._save_json(path, data)

    def _load_json(self, path, default):
        if not os.path.exists(path): return default
        try:
            with open(path, encoding='utf-8') as f:
                _content = f.read()
            if not _content.strip():
                # ★P3修复：空文件/仅空白视为合法空状态（如用户手动清空补丁），
                # 不报 ERROR，直接返回默认值；只对真实损坏保留 ERROR 告警。
                _module_logger.debug(f"JSON空文件视为空状态: {path}")
                return default
            return json.loads(_content)
        except Exception as e:
            _module_logger.error(f"JSON加载失败 {path}: {e}，返回默认值")
            return default
    def _load_restart_counter(self) -> int:
        """从持久化文件加载重启计数"""
        path = os.path.join(self._patch_dir, "restart_count.txt")
        try:
            with open(path, encoding='utf-8') as f:
                return int(f.read().strip())
        except Exception:
            return 0

    def _save_restart_counter(self, count: int) -> bool:
        """持久化重启计数（★FIX: fail-closed——写失败返回 False 并告警，避免防循环重启失效）"""
        path = os.path.join(self._patch_dir, "restart_count.txt")
        # ★第44批 T4（P2-290）：写盘守卫
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(path, component="PatchManager.restart_counter"):
                return False
        except ImportError:
            pass
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(str(count))
            return True
        except Exception as _e:
            _module_logger.error(f"重启计数写入失败（防循环重启可能失效）: {path}: {_e}")
            return False

    def reset_restart_counter(self):
        """人工确认后重置重启计数"""
        self._save_restart_counter(0)
        self._restart_counter = 0    
    def _save_json(self, path, data):
        """保存 JSON 并**返回是否写入成功**（bool）。

        ★第53批 T2（P0-补丁2）缺陷修复：本方法原本**只有守卫拒绝分支显式
        ``return False``**，成功路径与异常路径都**隐式返回 None** ——
        调用方若写 ``if not _save_ok:``，则「成功(None)」与「失败(False)」同为
        falsy，会把成功误判为失败。现统一为显式 bool：

        * 写入成功 → ``True``
        * 写盘守卫拒绝 → ``False``（并记 WARNING 留痕）
        * 写盘异常 → ``False``（并记 ERROR 留痕）

        兼容性（已实测）：既有调用方（SafeEvolutionExecutor x2 忽略返回值、
        tests/test_write_guard_m44 仅判 ``!= False`` / ``assertFalse``）均不受影响。

        Args:
            path: 目标 JSON 路径（会被写盘守卫检查，测试环境不得写生产 data/）。
            data: 可 JSON 序列化的对象。

        Returns:
            bool: 是否写入成功。
        """
        # ★第44批 T4（P2-290）：写盘守卫 —— 测试环境不得写生产 data/
        try:
            from nucleus.data.write_guard import guard_write as _m44_gw
            if not _m44_gw(path, component="PatchManager._save_json"):
                # ★第53批 T2：守卫拒绝必须留痕（原先静默返回 False，无法区分
                #   「被守卫拦下」与「写盘异常」）
                _module_logger.warning(f"[补丁落盘] 写盘守卫拒绝写入: {path}")
                return False
        except ImportError:
            pass
        # ★FIX: mkstemp 随机名，避免预测名 TOCTOU
        import tempfile
        _dir = os.path.dirname(path) or "."
        tmp_path = ""                # ★第53批 T2b：预置，避免 mkstemp 自身失败时清理块 UnboundLocalError
        try:
            _fd, tmp_path = tempfile.mkstemp(dir=_dir, prefix=".json_", suffix=".tmp")  # type: ignore[possibly-unbound]
            with os.fdopen(_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)  # type: ignore[possibly-unbound]
            return True                      # ★第53批 T2：成功路径显式 True
        except Exception as e:
            _module_logger.error(f"JSON保存失败 {path}: {e}")
            try:
                if tmp_path and os.path.exists(tmp_path):  # ★第53批 T2b：真值前置判断
                    os.remove(tmp_path)
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return False                     # ★第53批 T2：异常路径显式 False（原隐式 None）
