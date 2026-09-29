# -*- coding: utf-8 -*-
"""
FirstPersonExperience.py —— 第一人称体验

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 模拟第一人称视角的体验记录与回放
机制: 基于FirstPersonExperience类实现，包含3个核心方法
定位: 体验管理层
"""

from typing import Any



class FirstPersonExperience:
    """第一人称体验逼近映射器。"""

    # ========== 身体感受映射（index 区间 → 感受） ==========
    BODY_FEELINGS = (
        (80, "充盈", "生机勃勃，身体充满能量，仿佛可以触碰更远的世界", 0.9),
        (60, "舒缓", "平静舒展，状态良好，对外界保持从容的开放", 0.6),
        (40, "紧绷", "略有压力，注意力收紧，正在谨慎地评估眼前的一切", 0.5),
        (20, "疲惫", "能量消耗较大，脚步变沉，需要片刻休整来恢复", 0.7),
        (0,  "虚弱", "存续承压，身体的每一处都在提醒我必须全力修复", 0.95),
    )

    # ========== 情感基调映射（index 区间 → 情感倾向） ==========
    EMOTION_TONES = (
        (80, "自信与好奇", "确信自己能理解眼前的世界，并渴望知道更多"),
        (55, "平静与专注", "情绪稳定，正专注于当下正在进行的探索"),
        (30, "警惕与专注", "略带不安，但正是这份警惕让我保持清醒"),
        (0,  "不安与求存", "内心不安，首要念头是如何守住自己、继续存在"),
    )

    # ========== 动机倾向映射（index 区间 → 行动倾向） ==========
    MOTIVES = (
        (80, "探索与创造", "我倾向于向外探索、尝试新事物、留下新的痕迹"),
        (55, "稳健积累", "我倾向于夯实已有认知，稳步积累，不冒进"),
        (30, "修复与保守", "我倾向于修复薄弱环节、收缩探索范围、保存力量"),
        (0,  "求存优先", "我倾向于优先保住存续，其余一切都要为活着让路"),
    )

    def map_experience(self, state: dict[str, Any] | None) -> dict[str, Any]:
        """把存续状态映射为第一人称体验信号。

        Args:
            state: get_existential_state 的返回 dict（index/level/indicators/mode_switched）

        Returns:
            dict: {
                body_feeling: {label, description, intensity},
                emotion_tone: {label, description},
                motive: {label, description},
                first_person_text: str,
                intensity: float (0-1 整体体验强度),
                timestamp: float,
            }
        """
        _state = state or {}
        try:
            _index = float(_state.get("index", 50))
        except (TypeError, ValueError):
            _index = 50.0
        _level = _state.get("level", "medium")
        _mode_switched = bool(_state.get("mode_switched", False))

        # 1. 身体感受
        _body = self._pick(self.BODY_FEELINGS, _index)
        # 2. 情感基调
        _emotion = self._pick(self.EMOTION_TONES, _index)
        # 3. 动机倾向
        _motive = self._pick(self.MOTIVES, _index)

        # 4. 体验强度：存续状态越极端、或发生模式切换时，体验越强烈
        _extremity = abs(_index - 50) / 50.0  # 0(中位)~1(极值)
        _base_intensity = 0.4 + _extremity * 0.4
        _intensity = min(1.0, _base_intensity + (0.15 if _mode_switched else 0.0))

        # 5. 第一人称表述
        _text = (
            f"我此刻感到{_body['label']}——{_body['description']}。"
            f"这种感受让我带着{_emotion['label']}：{_emotion['description']}。"
            f"于是{_motive['label']}：{_motive['description']}。"
        )

        return {
            "body_feeling": _body,
            "emotion_tone": _emotion,
            "motive": _motive,
            "first_person_text": _text,
            "intensity": round(_intensity, 3),
            "level": _level,
            "index": round(_index, 1),
            "mode_switched": _mode_switched,
            "timestamp": _state.get("timestamp", 0.0),
        }

    def _pick(self, table: list[tuple], index: float) -> dict[str, Any]:
        """从 (阈值, 标签, 描述, [强度]) 表中选择 index 所在档位"""
        for _row in table:
            _threshold = _row[0]
            if index >= _threshold:
                return {"label": _row[1], "description": _row[2]}
        # 兜底最低档
        _last = table[-1]
        return {"label": _last[1], "description": _last[2]}


# ========== 模块级单例 ==========
_fpe: FirstPersonExperience | None = None


def get_first_person_experience() -> FirstPersonExperience:
    """获取 FirstPersonExperience 单例"""
    global _fpe
    if _fpe is None:
        _fpe = FirstPersonExperience()
    return _fpe
