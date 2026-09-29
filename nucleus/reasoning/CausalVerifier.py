# -*- coding: utf-8 -*-
"""
CausalVerifier.py —— 因果验证器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 因果推断结果的验证与修正
机制: 基于CausalVerifier类实现，包含10个核心方法
定位: 推理验证层
"""

from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


try:
    from nucleus.logger import get_module_logger
    _logger = get_module_logger("CausalVerifier")
except Exception:
    _logger = None


def _log_info(msg: str) -> None:
    if _logger is not None:
        try:
            _logger.info(msg)
        except Exception as e:
            _logger.debug(f"异常已忽略（需关注）: {type(e).__name__}: {e}")


def _log_debug(msg: str) -> None:
    if _logger is not None:
        try:
            _logger.debug(msg)
        except Exception as e:
            _logger.debug(f"异常已忽略（需关注）: {type(e).__name__}: {e}")


class CausalVerifier(SilentLogMixin):
    """多步因果验证器。"""

    # 来源支撑阈值：低于此信任分的来源节点视为「支撑不足」
    MIN_SOURCE_TRUST = 40.0
    # 断链后单步置信度折扣
    BROKEN_STEP_CONFIDENCE = 0.5
    # 通过验证后可维持的置信度上限比例
    VERIFIED_CONFIDENCE_CAP = 0.95

    def verify_chain(self, steps: list[dict[str, Any]],
                     source_nodes: list[str] | None = None,
                     node_pool: Any | None = None,
                     base_confidence: float = 0.0) -> dict[str, Any]:
        """对一条推理链做逐级符号级验证。

        Args:
            steps: 步骤列表，每步含 cause / effect（或可从 chain 文本解析）
            source_nodes: 来源节点 ID 列表（可选）
            node_pool: 知识节点池（可选，用于来源支撑检查）
            base_confidence: 推导方给出的原始置信度（0~100）

        Returns:
            dict: {
                verified: bool,          # 整链是否全部通过
                status: str,             # verified / partial / broken
                confidence: float,       # 调整后置信度（0~100）
                broken_at: int | None,   # 第一个断链步骤索引（从0起）
                steps: [ {index, cause, effect, verified, reason}, ... ],
                adjusted: bool,          # 置信度是否被下调
            }
        """
        if not steps:
            return {
                "verified": False,
                "status": "broken",
                "confidence": 0.0,
                "broken_at": 0,
                "steps": [],
                "adjusted": True,
                "reason": "空链条",
            }

        _normalized = self._normalize_steps(steps)
        _results: list[dict[str, Any]] = []
        _broken_at = None
        _all_ok = True

        for _i, _step in enumerate(_normalized):
            _cause = _step.get("cause", "")
            _effect = _step.get("effect", "")
            _reason_parts = []

            # 1. 步内完整性
            _ok = True
            if not _cause:
                _ok = False
                _reason_parts.append("缺少原因(cause)")
            if not _effect:
                _ok = False
                _reason_parts.append("缺少结果(effect)")

            # 2. 步骤间传递闭合（前步 effect == 后步 cause）
            if _i > 0 and _ok:
                _prev_effect = _results[_i - 1].get("effect", "")
                if _prev_effect and not self._relation_match(_prev_effect, _cause):
                    _ok = False
                    _reason_parts.append(
                        f"传递断链: 前步结果「{_prev_effect}」≠ 本步原因「{_cause}」"
                    )

            # 3. 来源支撑
            _source_ok = self._check_source(_i, _normalized, source_nodes, node_pool)
            if _source_ok is False:
                _ok = False
                _reason_parts.append("来源支撑不足")

            _results.append({
                "index": _i,
                "cause": _cause,
                "effect": _effect,
                "verified": _ok,
                "reason": "；".join(_reason_parts) if _reason_parts else "通过",
            })
            if not _ok and _broken_at is None:
                _broken_at = _i
                _all_ok = False

        # 整链判定
        if _all_ok:
            _status = "verified"
            _verified = True
        elif _broken_at == 0:
            _status = "broken"
            _verified = False
        else:
            _status = "partial"
            _verified = False

        # 后台日志：记录验证结果（断链/降置信度是有价值的运行信号）
        if _logger is not None:
            _step_n = len(_normalized)
            if _status == "verified":
                _log_debug(f"因果链验证通过: {_step_n}步, 置信度={base_confidence}")
            else:
                _log_info(f"因果链断链: status={_status}, 断链步={_broken_at}, "
                          f"总步数={_step_n}, 原置信度={base_confidence}")

        # 置信度调整：断链则下调
        _adjusted = False
        _conf = float(base_confidence)
        if _broken_at is not None:
            _adjusted = True
            # 断链步数占比越靠前，下调越狠
            _discount = self.BROKEN_STEP_CONFIDENCE
            _conf = _conf * _discount
        else:
            # 全部通过：可维持，但不超过上限
            _cap = 100.0 * self.VERIFIED_CONFIDENCE_CAP
            _conf = min(_conf, _cap)
        _conf = round(max(0.0, min(100.0, _conf)), 1)

        return {
            "verified": _verified,
            "status": _status,
            "confidence": _conf,
            "broken_at": _broken_at,
            "steps": _results,
            "adjusted": _adjusted,
            "reason": self._summary(_status, _broken_at),
        }

    # ========== 内部工具 ==========

    def _normalize_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把步骤归一化为 {cause, effect} 结构。

        支持两种输入：
          - 结构化：每步含 cause/effect 键
          - 链式文本：每步含 chain 键（如 "A→B→C"），解析为有序步骤
        """
        _out: list[dict[str, Any]] = []
        for _s in steps:
            if isinstance(_s, str):
                # 文本步骤：尝试解析 "cause→effect"
                _c, _e = self._parse_arrow(_s)
                _out.append({"cause": _c, "effect": _e})
                continue
            if not isinstance(_s, dict):
                continue
            if _s.get("cause") is not None or _s.get("effect") is not None:
                _out.append({
                    "cause": str(_s.get("cause", "")).strip(),
                    "effect": str(_s.get("effect", "")).strip(),
                })
            elif _s.get("step1") and _s.get("step2"):
                # 显式前提优先：step1/step2 是因果链算子的实际输入（零虚构前提）
                _out.append(self._parse_step_text(_s.get("step1", "")))
                _out.append(self._parse_step_text(_s.get("step2", "")))
            elif _s.get("chain"):
                # 链式文本 "A→B→C" → 拆分为多个单步
                _nodes = self._split_chain(_s["chain"])
                for _j in range(len(_nodes) - 1):
                    _out.append({
                        "cause": _nodes[_j],
                        "effect": _nodes[_j + 1],
                    })
        return _out

    def _parse_arrow(self, text: str) -> tuple[str, str]:
        """解析 "A→B" / "A->B" / "A 导致 B" 文本为 (cause, effect)"""
        for _sep in ("→", "->", "⇒"):
            if _sep in text:
                _parts = text.split(_sep, 1)
                return _parts[0].strip(), _parts[1].strip()
        # 因果关键词
        for _kw in ("导致", "因此", "所以", "使得", "造成", "引起"):
            if _kw in text:
                _parts = text.split(_kw, 1)
                return _parts[0].strip(), _parts[1].strip()
        return text.strip(), ""

    def _parse_step_text(self, text: str) -> dict[str, str]:
        """解析 causal_chain_derive 的 step1 文本：『A』导致『B』"""
        _c, _e = "", ""
        _seg = text
        # 去掉引号对：『...』
        _seg = _seg.replace("『", "|").replace("」", "|").replace("「", "|").replace("』", "|")
        _parts = [p.strip() for p in _seg.split("|") if p.strip()]
        if len(_parts) >= 2:
            _c = _parts[0]
            _e = _parts[-1]
        return {"cause": _c, "effect": _e}

    def _split_chain(self, chain: str) -> list[str]:
        """把 "A→B→C" 拆为 ["A","B","C"]"""
        for _sep in ("→", "->", "⇒"):
            if _sep in chain:
                return [s.strip() for s in chain.split(_sep) if s.strip()]
        return [chain.strip()]

    def _relation_match(self, prev_effect: str, cur_cause: str) -> bool:
        """传递闭合判定：前步结果与后步原因是否匹配（全等或包含）"""
        if not cur_cause:
            return True  # 后步无原因，交由步内完整性判定
        _pe = prev_effect.strip()
        _cc = cur_cause.strip()
        return _pe == _cc or _pe in _cc or _cc in _pe

    def _check_source(self, index: int, steps: list[dict[str, Any]],
                      source_nodes: list[str] | None,
                      node_pool: Any | None) -> bool | None:
        """来源支撑检查：
          - 无 source_nodes / node_pool → None（不判定，交给其他规则）
          - 有来源但查询不到 / 信任分不足 → False
        """
        if not source_nodes or not node_pool:
            return None
        _sid = None
        if index < len(source_nodes):
            _sid = source_nodes[index]
        if not _sid:
            return None
        try:
            _node = node_pool.get(_sid)
            if _node is None:
                return False
            _trust = getattr(_node, "trust_score", None)
            if _trust is None:
                _trust = getattr(_node, "trust", 50.0)
            return not (_trust < self.MIN_SOURCE_TRUST)
        except Exception:
            return None

    def _summary(self, status: str, broken_at: int | None) -> str:
        if status == "verified":
            return "全部步骤通过符号级验证，结论可信"
        if status == "partial":
            return f"第{broken_at + 1}步起断链，后续结论标记为待验证（不视为已成立）"
        return "链条在首步即断链，结论不可用"

    # ========== 便捷接口 ==========

    def verify_causal_chain_result(self, result: dict[str, Any],
                                   node_pool: Any | None = None) -> dict[str, Any]:
        """便捷接口：直接验证 causal_chain_derive 的返回结果，附加 verification 字段。

        兼容输入: {chain_steps: {chain/step1/step2/...}, confidence, source_nodes}
        """
        if not result:
            return result
        _steps = result.get("chain_steps")
        _steps_list = [_steps] if isinstance(_steps, dict) else (_steps or [])
        _conf = float(result.get("confidence", 0.0) or 0.0)
        _ver = self.verify_chain(
            _steps_list,
            source_nodes=result.get("source_nodes"),
            node_pool=node_pool,
            base_confidence=_conf,
        )
        # 附加验证报告 + 调整置信度
        _copy = dict(result)
        _copy["verification"] = _ver
        if _ver.get("adjusted") and _ver.get("status") != "verified":
            _copy["confidence"] = _ver.get("confidence", _conf)
        # ★v9.5山1-P1自我校准器：验证结果反馈给校准器（错误反向修正置信度分布）
        try:
            from nucleus.reasoning.SelfCalibrator import get_self_calibrator
            _cal = get_self_calibrator()
            _verified = _ver.get("verified", False)
            _cal.record_outcome("causal_chain", _copy.get("confidence", _conf), _verified)
            _copy["confidence"] = _cal.calibrate(_copy.get("confidence", _conf), "causal_chain")
            _copy["calibrated_confidence"] = _copy["confidence"]
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _copy


# ========== 模块级单例 ==========
_verifier: CausalVerifier | None = None


def get_causal_verifier() -> CausalVerifier:
    """获取 CausalVerifier 单例"""
    global _verifier
    if _verifier is None:
        _verifier = CausalVerifier()
    return _verifier
