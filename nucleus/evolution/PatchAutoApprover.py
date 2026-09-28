# -*- coding: utf-8 -*-
"""
PatchAutoApprover.py —— 补丁自动审批器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 基于规则的补丁自动审批与风险评估
机制: 基于PatchAutoApprover类实现，包含10个核心方法
定位: 进化治理层

★第116批 T-116c⑤ 状态标注 —— 审批面 deprecated（本批删 0 行）:
  AutoApprover 的「检测面」（is_obsolete / 按龄判废）已由裁决 CLI 的
  `adjudicate_patch.py stale-audit` 只读子命令收编（内联同判据，出三本账建议作废单）。
  ★处置约定：stale-audit 连续两个周期「无独有捕获」（即它发现的项 AutoApprover 也全部发现）
    后，才物理删除本模块的审批面；本批不做删除。

★第53批 T1（P0-补丁3）状态标注 —— 短期 deprecated（星轨裁决2·选项B）:
  本模块的审批/清理入口（classify / scan_pending / prune_pending）经全库排查
  **无生产调用点**（仅 record_evolution_round 被 SafeEvolutionExecutor 调用）。
  当前生产侧自动审批由 PatchManager 入队时的内联逻辑承担。
  本模块保留用于人工审计与长期集成评估；两处信任分门槛已统一为 40。
"""

import json
import os
import shutil
import threading
import time
from typing import Any

from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.logger import get_module_logger

_logger = get_module_logger("PatchAutoApprover")

# 测试隔离：tmp/test_isolation 的 redirect_all 会 patch 本模块的该变量
_ISO_BASE_DIR: str | None = None

# ============ 可配置门槛（★不写死，config.py 可热加载覆盖） ============

# ★第53批 T1（P0-补丁1/3）：30 → 40，与 PatchManager 入队自动审批
#   （EVOLUTION_CONFIG.auto_apply_min_trust）统一，消除两套机制阈值漂移。
#   沿革：第5批曾由 40 降到 30，以放行 9 条 trust=30 的积压补丁；实测该积压在
#   2026-09-08 后已清零（pending 队列 0 条），且 09-09 起补丁 trust 全为 40。
#   ★读取优先级：config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD > 本常量（见 _trust_threshold）。
AUTO_APPROVE_MIN_TRUST = 40
# 等待时长门槛：自动放行仍要求等待足够久（避免刚生成就自动应用），保留原 24h 语义。
AUTO_APPROVE_MIN_WAIT_HOURS = 24.0
# 补丁超过该天数未应用 → 自动归档 stale（拒绝应用）。默认 7，可被 config 覆盖。
STALE_DAYS_DEFAULT = 7
LOW_RISK_TOKENS = ("低", "low", "LOW", "Low")
HIGH_RISK_TOKENS = ("高", "high", "HIGH", "High")
CONFIDENCE_HIGH_TOKENS = ("high", "High", "HIGH", "高")
CONFIDENCE_MEDIUM_TOKENS = ("medium", "Medium", "MEDIUM", "mid", "Mid", "中")
CONFIDENCE_LOW_TOKENS = ("low", "Low", "LOW", "低")
_VERIFIED_TRUTHY = (True, "true", "True", "TRUE", "是", "yes", "Yes", "YES", 1, "1")


def _cfg(name: str, default):
    """★第五批 任务4：门槛常量热加载读取。每次裁决实时 import config，
    修改 config.py 无需重启框架即可生效（降级：config 不可用时回退默认值）。"""
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception:
        return default


def _trust_threshold() -> float:
    return float(_cfg("PATCH_AUTO_APPROVE_TRUST_THRESHOLD", AUTO_APPROVE_MIN_TRUST))


def _stale_days() -> float:
    return float(_cfg("PATCH_AUTO_APPROVE_STALE_DAYS", STALE_DAYS_DEFAULT))


AUDIT_FILE = "auto_approved.json"
PENDING_FILE = "pending_patches.json"

# 决策枚举
DECISION_APPROVE = "auto_approve"     # verified+high+low+trust达标 → 自动应用
DECISION_HUMAN = "need_human"         # trust达标+verified+risk≤medium → 待人工审批
DECISION_STALE = "reject_stale"       # 超过阈值天数未应用 → 过期拒绝
DECISION_OBSOLETE = "mark_obsolete"   # 目标代码已被后续修改覆盖 → 标记 obsolete
DECISION_LOW_TRUST = "reject_low_trust"    # 信任分不足 或 高风险
DECISION_TOO_NEW = "reject_too_new"        # 等待时长不足
DECISION_DUP = "reject_duplicate"     # 重复补丁

_APPROVABLE = (DECISION_APPROVE,)


def _project_root() -> str:
    """项目根目录。

    本文件位于 <root>/nucleus/evolution/PatchAutoApprover.py，需上溯 **3 层**
    （evolution → nucleus → 项目根）。写成 2 层会得到 <root>/nucleus，
    导致所有相对路径拼接错误、is_stale 恒为真。
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PatchAutoApprover:
    """低风险补丁自动裁决器（含审计 + 可选自动应用）。"""

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.RLock()
        if base_dir is None and _ISO_BASE_DIR:
            base_dir = _ISO_BASE_DIR
        if base_dir is None:
            base_dir = os.path.join(_project_root(), "data", "patches")
        self._base_dir = base_dir
        try:
            os.makedirs(self._base_dir, exist_ok=True)
        except Exception as e:
            _logger.warning(f"补丁目录创建失败: {type(e).__name__}: {e}")
        self._audit_path = os.path.join(self._base_dir, AUDIT_FILE)
        self._pending_path = os.path.join(self._base_dir, PENDING_FILE)
        self._audit: list[dict[str, Any]] = []
        self._load_audit()

    # ============ 判定 ============

    @staticmethod
    def _is_low_risk(patch: dict[str, Any]) -> bool:
        _r = str(patch.get("risk_level", "") or "")
        return any(t in _r for t in LOW_RISK_TOKENS) and \
            not any(t in _r for t in HIGH_RISK_TOKENS)

    @staticmethod
    def _risk_category(patch: dict[str, Any]) -> str:
        """将 risk_level 归一到 low/medium/high。"""
        _r = str(patch.get("risk_level", "") or "")
        if any(t in _r for t in HIGH_RISK_TOKENS):
            return "high"
        if any(t in _r for t in LOW_RISK_TOKENS):
            return "low"
        return "medium"

    @staticmethod
    def _confidence(patch: dict[str, Any]) -> str:
        """将 confidence 归一到 low/medium/high。"""
        _c = str(patch.get("confidence", "") or "").strip()
        if any(t == _c or t in _c for t in CONFIDENCE_HIGH_TOKENS):
            return "high"
        if any(t == _c or t in _c for t in CONFIDENCE_MEDIUM_TOKENS):
            return "medium"
        if any(t == _c or t in _c for t in CONFIDENCE_LOW_TOKENS):
            return "low"
        return "unknown"

    @staticmethod
    def _verified(patch: dict[str, Any]) -> bool:
        if patch.get("verified") in _VERIFIED_TRUTHY:
            return True
        # ★主线第5批 P0-7：兼容运行时验证产物（runtime_verified / status=runtime_verified）。
        #   自动生成的补丁用 status="runtime_verified" 表达"已验证"，但旧逻辑只看 verified 字段
        #   → 全部判为未验证 → 卡在 need_human 无法自动放行（11 条积压补丁根因）。
        #   受 config.PATCH_AUTO_APPROVE_ACCEPT_RUNTIME_VERIFIED 开关控制（默认开启=修复生效）。
        try:
            import config as _c
            if not getattr(_c, "PATCH_AUTO_APPROVE_ACCEPT_RUNTIME_VERIFIED", True):
                return False
        except Exception:
            pass
        if patch.get("runtime_verified") in _VERIFIED_TRUTHY:
            return True
        return str(patch.get("status", "")) == "runtime_verified"

    @staticmethod
    def _trust_ok(patch: dict[str, Any]) -> bool:
        try:
            return float(patch.get("trust_score", 0) or 0) >= _trust_threshold()
        except Exception:
            return False

    @staticmethod
    def _wait_hours(patch: dict[str, Any]) -> float:
        _g = float(patch.get("generated_at", 0) or 0)
        if _g <= 0:
            return -1.0
        return (time.time() - _g) / 3600.0

    def _wait_ok(self, patch: dict[str, Any]) -> bool:
        _h = self._wait_hours(patch)
        return _h >= float(AUTO_APPROVE_MIN_WAIT_HOURS)

    def is_stale(self, patch: dict[str, Any]) -> bool:
        """原代码片段已不在目标文件中 → 问题已被他处修复，补丁过期（obsolete）。"""
        return self.is_obsolete(patch)

    def is_obsolete(self, patch: dict[str, Any]) -> bool:
        """★第五批 任务4：目标代码已被后续修改覆盖 → 补丁 obsolete。

        与 _is_stale_by_age（按时间归档）区分：这里是「内容已被他处改动」。
        """
        _rel = patch.get("file") or ""
        _orig = (patch.get("original_code") or "").strip()
        if not _rel or not _orig:
            return False
        _p = os.path.join(_project_root(), _rel)
        if not os.path.exists(_p):
            return True
        try:
            with open(_p, encoding="utf-8") as _f:
                _src = _f.read()
        except Exception as e:
            _logger.debug(f"过期检测读取失败: {type(e).__name__}: {e}")
            return False
        return _orig not in _src

    def _is_stale_by_age(self, patch: dict[str, Any]) -> bool:
        """★第五批 任务4：补丁超过阈值天数未应用 → 自动归档为 stale。"""
        _h = self._wait_hours(patch)
        if _h < 0:
            return False
        return _h >= float(_stale_days()) * 24.0

    def classify(self, patch: dict[str, Any]) -> tuple[str, str]:
        """★第五批 任务4（P1-4）：组合判定（取代单一 trust≥40）。

        判定优先级：
          1) 格式非法 → need_human
          2) 超过阈值天数未应用（stale） → reject_stale
          3) 目标代码已被覆盖（obsolete） → mark_obsolete
          4) 风险=high 或 信任分<阈值 → reject（low_trust）
          5) verified + confidence=high + risk=low
               且 等待时长达标 → auto_approve
               否则（等待不足）→ need_human（转人工）
          6) trust≥阈值 + verified + risk≤medium → need_human（待人工审批）
          7) 其余 → need_human
        """
        if not isinstance(patch, dict):
            return DECISION_HUMAN, "补丁格式非法"

        # 2) 过期（时间维度）：超过阈值天数未应用
        if self._is_stale_by_age(patch):
            return DECISION_STALE, (
                f"补丁已 {self._wait_hours(patch):.1f}h 未应用，"
                f"超过 {_stale_days()} 天阈值，归档为 stale 拒绝应用")

        # 3) 过期（内容维度）：目标代码已被后续修改覆盖
        if self.is_obsolete(patch):
            return DECISION_OBSOLETE, "目标代码已被后续修改覆盖，标记 obsolete"

        _verified = self._verified(patch)
        _risk = self._risk_category(patch)
        _conf = self._confidence(patch)
        _trust_ok = self._trust_ok(patch)

        # 4) 高风险或信任分不足 → 自动拒绝
        if _risk == "high" or not _trust_ok:
            _reason = (f"风险等级={patch.get('risk_level')}(高风险)"
                       if _risk == "high"
                       else f"信任分 {patch.get('trust_score')} < 门槛 {_trust_threshold()}")
            return DECISION_LOW_TRUST, _reason

        # 5) 组合放行：verified + high + low
        if _verified and _conf == "high" and _risk == "low":
            if self._wait_ok(patch):
                return DECISION_APPROVE, "verified+置信度high+风险low+信任分达标+等待达标 → 自动放行"
            return DECISION_HUMAN, (
                f"等待 {self._wait_hours(patch):.1f}h < 门槛 {AUTO_APPROVE_MIN_WAIT_HOURS}h，转人工")

        # 6) trust≥阈值 + verified + risk≤medium → 待人工审批（不自动拒绝）
        if _trust_ok and _verified and _risk in ("low", "medium"):
            return DECISION_HUMAN, "信任分达标+已验证+风险≤medium → 待人工审批"

        # 7) 其余情况：保守转人工，绝不自动应用/自动拒绝
        return DECISION_HUMAN, "未满足自动放行条件，转人工审批"

    # ============ 积压清理 + 可观测 ============

    def prune_pending(self, dry_run: bool = False) -> dict[str, Any]:
        """★主线第5批 P0-7：自动归档已解决补丁，降低待审批积压计数。

        已解决定义：classify 判定为 obsolete / stale，或 auto_approve 且已应用（applied=True）。
        归档写入 pending_patches_resolved.json（追加），重写 pending 仅保留未解决项。
        受 config.PATCH_AUTO_APPROVE_PRUNE_PENDING 开关控制（默认开启）。
        """
        if not _cfg("PATCH_AUTO_APPROVE_PRUNE_PENDING", True):
            return {"pruned": 0, "remaining": -1, "skipped_by_flag": True}
        if not os.path.exists(self._pending_path):
            return {"pruned": 0, "remaining": 0}
        try:
            _patches = safe_read_json(self._pending_path, default={})
        except (ValueError, OSError) as e:
            _logger.warning(f"读取待审批补丁失败: {type(e).__name__}: {e}")
            return {"pruned": 0, "remaining": -1, "error": str(e)}
        if not isinstance(_patches, list):
            return {"pruned": 0, "remaining": 0}
        _kept, _resolved = [], []
        for _p in _patches:
            _dec, _ = self.classify(_p)
            _applied = bool(_p.get("applied", False))
            if _dec in (DECISION_OBSOLETE, DECISION_STALE) or (_dec == DECISION_APPROVE and _applied):
                _resolved.append(_p)
            else:
                _kept.append(_p)
        if dry_run:
            return {"pruned": len(_resolved), "remaining": len(_kept), "dry_run": True}
        if _resolved:
            _arch_path = os.path.join(self._base_dir, "pending_patches_resolved.json")
            _arch = []
            try:
                if os.path.exists(_arch_path):
                    _arch = safe_read_json(_arch_path, default={})
                    if not isinstance(_arch, list):
                        _arch = []
            except Exception as e:
                print(f"[WARNING] PatchAutoApprover.py:303: {type(e).__name__}: {e}")
                _arch = []
            _arch.extend(_resolved)
            try:
                with open(_arch_path, "w", encoding="utf-8") as f:
                    json.dump(_arch, f, ensure_ascii=False, indent=2)
            except Exception as e:
                _logger.warning(f"归档已解决补丁失败: {type(e).__name__}: {e}")
            try:
                with open(self._pending_path, "w", encoding="utf-8") as f:
                    json.dump(_kept, f, ensure_ascii=False, indent=2)
            except Exception as e:
                _logger.warning(f"重写 pending 失败: {type(e).__name__}: {e}")
                return {"pruned": 0, "remaining": len(_patches), "error": str(e)}
        return {"pruned": len(_resolved), "remaining": len(_kept)}

    def get_pending_stats(self) -> dict[str, Any]:
        """★主线第5批 P0-7：可观测端点——待审批补丁分布统计。"""
        if not os.path.exists(self._pending_path):
            return {"total": 0, "by_decision": {}, "by_risk": {}, "by_trust": {}}
        try:
            _patches = safe_read_json(self._pending_path, default={})
        except (ValueError, OSError) as e:
            print(f"[WARNING] PatchAutoApprover.py:325: {type(e).__name__}: {e}")
            return {"total": 0, "by_decision": {}, "by_risk": {}, "by_trust": {}}
        if not isinstance(_patches, list):
            return {"total": 0, "by_decision": {}, "by_risk": {}, "by_trust": {}}
        _by_dec, _by_risk, _by_trust = {}, {}, {}
        for _p in _patches:
            _dec, _ = self.classify(_p)
            _by_dec[_dec] = _by_dec.get(_dec, 0) + 1
            _r = self._risk_category(_p)
            _by_risk[_r] = _by_risk.get(_r, 0) + 1
            _t = _p.get("trust_score")
            _tb = ">=30" if float(_t or 0) >= 30 else "<30"
            _by_trust[_tb] = _by_trust.get(_tb, 0) + 1
        return {"total": len(_patches), "by_decision": _by_dec,
                "by_risk": _by_risk, "by_trust": _by_trust}

    # ============ 应用 ============

    def apply_patch(self, patch: dict[str, Any]) -> bool:
        """应用补丁（original_code → modified_code 文本替换），前先备份。

        仅在 classify() 判定为 auto_approve 后调用；失败绝不上抛。
        """
        _rel = patch.get("file") or ""
        _orig = patch.get("original_code") or ""
        _new = patch.get("modified_code") or ""
        if not _rel or not _orig:
            return False
        _p = os.path.join(_project_root(), _rel)
        try:
            with open(_p, encoding="utf-8") as f:
                _src = f.read()
            if _orig not in _src:
                return False
            _bak = _p + ".bak_batch11cd"
            if not os.path.exists(_bak):
                shutil.copy2(_p, _bak)
            with open(_p, "w", encoding="utf-8") as f:
                f.write(_src.replace(_orig, _new, 1))
            _logger.info(f"[自动裁决] 已应用补丁 → {_rel}")
            return True
        except Exception as e:
            _logger.warning(f"[自动裁决] 应用补丁失败（需关注）: {type(e).__name__}: {e}")
            return False

    # ============ 审计 ============

    def _load_audit(self) -> None:
        if not os.path.exists(self._audit_path):
            return
        try:
            _d = safe_read_json(self._audit_path, default={})
            if isinstance(_d, list):
                self._audit = _d
        except Exception as e:
            _logger.debug(f"审计加载异常已忽略: {type(e).__name__}: {e}")

    def _save_audit(self) -> None:
        try:
            _tmp = self._audit_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(self._audit, f, ensure_ascii=False, indent=2)
            os.replace(_tmp, self._audit_path)
        except Exception as e:
            _logger.debug(f"审计落盘异常已忽略: {type(e).__name__}: {e}")

    def record_decision(self, patch: dict[str, Any], decision: str,
                        reason: str, applied: bool = False) -> None:
        """★绝不静默：任何决策（含拒绝）都落审计。"""
        with self._lock:
            self._audit.append({
                "patch_id": patch.get("id"),
                "file": patch.get("file"),
                "issue_type": patch.get("issue_type"),
                "risk_level": patch.get("risk_level"),
                "trust_score": patch.get("trust_score"),
                "decision": decision,
                "reason": reason,
                "applied": bool(applied),
                "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            # 审计仅追加，保留全量，便于追溯
        self._save_audit()

    # ============ 批量扫描 ============

    def scan_pending(self, apply_approved: bool = False) -> dict[str, Any]:
        """扫描 pending_patches.json，逐条裁决（可选自动应用）。

        返回统计：{total, auto_approved, need_human, rejected, by_decision}
        """
        if not os.path.exists(self._pending_path):
            return {"total": 0, "by_decision": {}, "detail": []}
        try:
            _patches = safe_read_json(self._pending_path, default={})
        except (ValueError, OSError) as e:
            _logger.warning(f"读取待审批补丁失败: {type(e).__name__}: {e}")
            return {"total": 0, "by_decision": {}, "detail": []}
        if not isinstance(_patches, list):
            return {"total": 0, "by_decision": {}, "detail": []}

        _seen: set[str] = set()
        _by: dict[str, int] = {}
        _detail: list[dict[str, Any]] = []
        for _p in _patches:
            _dec, _reason = self.classify(_p)
            # 同文件 + 同原码 → 重复（第二条起拒绝）
            _key = f"{_p.get('file')}|{(_p.get('original_code') or '')[:60]}"
            if _dec == DECISION_APPROVE:
                if _key in _seen:
                    _dec, _reason = DECISION_DUP, "同文件同原码重复补丁"
                else:
                    _seen.add(_key)
            _applied = False
            if _dec == DECISION_APPROVE and apply_approved:
                _applied = self.apply_patch(_p)
            self.record_decision(_p, _dec, _reason, _applied)
            _by[_dec] = _by.get(_dec, 0) + 1
            _detail.append({"file": _p.get("file"), "decision": _dec,
                            "reason": _reason, "applied": _applied})

        _approved = _by.get(DECISION_APPROVE, 0)
        _logger.info(
            f"[自动裁决] 扫描完成：共 {len(_patches)} 条，自动放行 {_approved} 条，"
            f"明细={_by}")
        if _approved == 0 and len(_patches) > 0:
            _logger.warning(
                f"[自动裁决] 本轮无补丁被放行——请检查门槛配置"
                f"（信任分门槛={_trust_threshold()}，过期天数={_stale_days()}，"
                f"等待门槛={AUTO_APPROVE_MIN_WAIT_HOURS}h）"
                f"与补丁实际 trust_score 分布是否匹配")
        # ★主线第5批 P0-7：预埋 PHASE18 自主进化信号（关闭时 no-op）
        try:
            import config as _cfg_evo
            if getattr(_cfg_evo, "ENABLE_PHASE18_SIGNALS", False):
                from nucleus.telemetry.phase18_signals import get_phase18_signals
                _total = len(_patches)
                _approval_rate = round(_approved / _total, 4) if _total else 0.0
                get_phase18_signals().record_evolution(
                    approval_rate=_approval_rate,
                    auto_approved=_approved,
                    pending_count=_total,
                    by_decision=_by)
        except Exception:
            pass

        return {"total": len(_patches), "auto_approved": _approved, "by_decision": _by,
                "detail": _detail}


# ============ 任务3.3：自主进化修复率监控 ============

class EvolutionEffectivenessMonitor:
    """自主进化修复率监控：每轮记录 发现/修复/通过率，连续低修复率告警。"""

    LOW_RATE_THRESHOLD = 0.10   # 修复率 < 10% 视为低
    LOW_RATE_STREAK = 3         # 连续 3 轮低修复率 → WARNING
    DEFAULT_FILE = "evolution_effectiveness.json"

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.RLock()
        if base_dir is None and _ISO_BASE_DIR:
            base_dir = _ISO_BASE_DIR
        if base_dir is None:
            base_dir = os.path.join(_project_root(), "data", "metrics")
        self._base_dir = base_dir
        try:
            os.makedirs(self._base_dir, exist_ok=True)
        except Exception as e:
            _logger.warning(f"指标目录创建失败: {type(e).__name__}: {e}")
        self._save_path = os.path.join(self._base_dir, self.DEFAULT_FILE)
        self._rounds: list[dict[str, Any]] = []
        self._low_streak = 0
        self._load()

    def record_round(self, found: int, fixed: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """记录一轮自主进化：发现 X 个 / 修复 Y 个 / 通过率 Z%。"""
        _found = max(0, int(found or 0))
        _fixed = max(0, int(fixed or 0))
        _rate = round(_fixed / _found, 4) if _found > 0 else 0.0
        _rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ts_epoch": time.time(),
            "found": _found,
            "fixed": _fixed,
            "fix_rate": _rate,
        }
        if extra:
            _rec.update(extra)
        with self._lock:
            self._rounds.append(_rec)
            self._rounds = self._rounds[-500:]
            if _found > 0 and _rate < self.LOW_RATE_THRESHOLD:
                self._low_streak += 1
            else:
                self._low_streak = 0
            _streak = self._low_streak
        self._save()
        _logger.info(
            f"[进化效能] 发现 {_found} 个 / 修复 {_fixed} 个 / 修复率 {_rate:.1%}")
        if _streak >= self.LOW_RATE_STREAK:
            _logger.warning(
                f"[进化效能] 连续 {_streak} 轮修复率 < {self.LOW_RATE_THRESHOLD:.0%}，"
                f"自主进化闭环可能受阻，请检查补丁裁决与本地修复能力")
        return _rec

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            _n = len(self._rounds)
            _avg = round(sum(x.get("fix_rate", 0.0) for x in self._rounds) / _n, 4) if _n else 0.0
            return {
                "rounds": _n,
                "avg_fix_rate": _avg,
                "low_streak": self._low_streak,
                "last": self._rounds[-1] if self._rounds else None,
                "history": list(self._rounds[-50:]),
            }

    def _load(self) -> None:
        if not os.path.exists(self._save_path):
            return
        try:
            _d = safe_read_json(self._save_path, default={})
            if isinstance(_d, dict):
                self._rounds = list(_d.get("history", []) or [])
                self._low_streak = int(_d.get("low_streak", 0) or 0)
        except Exception as e:
            _logger.debug(f"修复率指标加载异常已忽略: {type(e).__name__}: {e}")

    def _save(self) -> None:
        try:
            _data = self.get_stats()
            _tmp = self._save_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(_data, f, ensure_ascii=False, indent=2)
            os.replace(_tmp, self._save_path)
        except Exception as e:
            _logger.debug(f"修复率指标落盘异常已忽略: {type(e).__name__}: {e}")


# ============ 单例 ============

_approver: PatchAutoApprover | None = None
_monitor: EvolutionEffectivenessMonitor | None = None
_lock = threading.RLock()


def get_patch_auto_approver() -> PatchAutoApprover:
    global _approver
    with _lock:
        if _approver is None:
            _approver = PatchAutoApprover()
        return _approver


def get_evolution_monitor() -> EvolutionEffectivenessMonitor:
    global _monitor
    with _lock:
        if _monitor is None:
            _monitor = EvolutionEffectivenessMonitor()
        return _monitor


def reset_patch_auto_approver() -> None:
    global _approver, _monitor
    with _lock:
        _approver = None
        _monitor = None


def record_evolution_round(found: int, fixed: int, extra: dict[str, Any] | None = None) -> None:
    """供自主进化主流程调用：记录一轮的发现/修复数。"""
    try:
        get_evolution_monitor().record_round(found, fixed, extra)
    except Exception as e:
        _logger.debug(f"进化效能埋点异常已忽略: {type(e).__name__}: {e}")
