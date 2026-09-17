# -*- coding: utf-8 -*-
"""
PulseInterestModel —— 兴趣模型器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 建模并持续维护曈曈的兴趣方向，驱动求知本能。
机制: _load_interest_config 加载兴趣配置；on_pulse 接收兴趣变更、情绪检测、反思洞察等事件并更新兴趣权重；set_stream_miner 接入流式挖掘来源；通过共振条件上报影响调度。
定位: 求知本能的方向引擎，为主动交互与学习方向提供兴趣信号。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
import random
import sys
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    ChatEvent,
    EarEvent,
    HeartEvent,
    HormonesEvent,
    InterestEvent,
    LogLevel,
    MouthEvent,
    ReflectionEvent,
    SubconsciousEvent,
    SystemEvent,
)

from nucleus.const import Event


class PulseInterestModel(BasePulseOrgan):
    """兴趣模型 —— 求知本能方向引擎（共享记忆版 · v9.5 分层脉冲版）"""


    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'interest_decay_rate' in _rp and hasattr(self, '_decay_rate'):
                setattr(self, '_decay_rate', _rp['interest_decay_rate'])
            if 'interest_boost_amount' in _rp and hasattr(self, '_boost_amount'):
                setattr(self, '_boost_amount', _rp['interest_boost_amount'])
            if 'interest_explore_success_boost' in _rp and hasattr(self, '_explore_success_boost'):
                setattr(self, '_explore_success_boost', _rp['interest_explore_success_boost'])
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
    def __init__(self, organ_name: str = "兴趣模型"):
        super().__init__(organ_name)

        # 从config加载配置（失败时用兜底值）
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        # ★属性初始化完整性补全（自动审查添加）
        self._boost_amount = 0.0
        self._decay_rate = 0.0
        self._exploration_history = []
        self._fatigue_log_counter = 0
        self._insight_boost_amount = 0.0
        self._max_interest = 0.0
        self._min_interest = 0.0
        self._load_interest_config()

        # ★v24.0校准：差异化基础值，让兴趣模型初始就反映框架实际需求
        _base_interests = {
            "技术架构": 0.50, "编程开发": 0.45, "人工智能": 0.50,
            "计算机硬件": 0.35, "机器人构造": 0.25, "机械原理": 0.20,
            "电子原理": 0.20, "物理科学": 0.15, "化学": 0.15,
            "数学逻辑": 0.20, "自然生态": 0.15, "医学生物学": 0.15,
            "能源动力": 0.15, "人文哲学": 0.45, "社会伦理": 0.40,
            "心理情感": 0.40, "历史考古": 0.10, "文学创作": 0.20,
            "艺术美学": 0.20, "音乐韵律": 0.10, "经济商业": 0.10,
            "体育健康": 0.10, "游戏娱乐": 0.10, "日常生活": 0.25,
            "未知探索": 0.30,
        }
        self._interests: dict[str, float] = {}
        for dim in self.INTEREST_DIMENSIONS:
            self._interests[dim] = max(self._min_interest, _base_interests.get(dim, 0.25))

        self._interaction_count = 0
        self._insight_boost_count = 0
        self._last_update = time.time()

        # ===== P2-4: 主动引导 =====
        self._recent_boosts: list[str] = []
        self._active_guidance_count = 0
        self._current_emotion = "中性"      # 缓存当前情绪
        self._current_emotion_intensity = 0.0

        self._lock = threading.Lock()
        # ===== 兴趣疲劳追踪 =====
        self._boost_history: dict[str, list[float]] = {}  # 维度 → 最近增强时间戳列表
        self._fatigue_decay_rate = 0.1                     # 疲劳恢复速率
        self._fatigue_max_window = 3600                    # 疲劳窗口（秒）
        # ===== 日志频率控制 =====
        self._decay_log_counter = 0  # 衰减日志计数器，每10次心跳输出一次
        # ===== v24.0新增：兴趣巡检 =====
        self._stale_check_counter = 0          # 巡检计数器
        self._stale_check_interval = 50        # 每50次心跳巡检一次
        self._last_boost_times: dict[str, float] = {}  # 维度→最后增强时间
        self._stale_threshold = 7 * 86400      # 7天未增强视为沉睡维度
        # ★P3-4修复：事件流挖掘器引用（消费其发现的模式，消除「只打日志无消费者」孤岛）
        self.stream_miner = None
        self._last_pattern_consume_time = 0.0
        self._pattern_consume_interval = 60.0  # 每60秒消费一次，避免每次心跳都拉取

    def _load_interest_config(self):
        """从config加载兴趣模型配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'INTEREST_MODEL_CONFIG', {})
            self.INTEREST_DIMENSIONS = cfg.get("interest_dimensions", ["技术架构", "编程开发", "人工智能"])
            self.DOMAIN_KEYWORDS = cfg.get("domain_keywords", {})
            self.DOMAIN_TO_INTEREST = cfg.get("domain_to_interest", {})
            self.ISSUE_TO_INTEREST = cfg.get("issue_to_interest", {})
            self._decay_rate = cfg.get("decay_rate", 0.001)
            self._boost_amount = cfg.get("boost_amount", 0.05)
            self._insight_boost_amount = cfg.get("insight_boost_amount", 0.08)
            self._max_interest = cfg.get("max_interest", 1.0)
            self._min_interest = cfg.get("min_interest", 0.1)
        except Exception:
            # 兜底
            self.INTEREST_DIMENSIONS = [
                "技术架构", "编程开发", "人工智能", "计算机硬件",
                "机器人构造", "机械原理", "电子原理",
                "物理科学", "化学", "数学逻辑",
                "自然生态", "医学生物学",
                "能源动力",
                "人文哲学", "社会伦理", "心理情感", "历史考古",
                "文学创作", "艺术美学", "音乐韵律",
                "经济商业", "体育健康", "游戏娱乐", "日常生活",
                "未知探索",
            ]
            self.DOMAIN_KEYWORDS = {
                "技术架构": ["架构", "设计模式", "系统", "脉冲", "信息场", "框架", "模块", "引擎"],
                "编程开发": ["代码", "Python", "编程", "算法", "数据", "函数", "类", "接口"],
                "人工智能": ["AI", "模型", "学习", "神经网络", "智能", "认知", "推理", "QICA"],
                "计算机硬件": ["计算机", "硬件", "CPU", "GPU", "显卡", "内存", "主板", "硬盘", "SSD", "电源"],
                "机器人构造": ["机器人", "机械", "舵机", "电机", "传感器", "ROS", "机械臂", "Arduino", "树莓派"],
                "机械原理": ["机械", "力学", "齿轮", "轴承", "连杆", "液压", "气动", "传动"],
                "电子原理": ["电子", "电路", "电阻", "电容", "电感", "PCB", "信号", "放大器", "单片机"],
                "物理科学": ["物理", "力学", "光学", "电磁", "量子", "相对论", "热力学", "粒子"],
                "化学": ["化学", "元素", "分子", "反应", "催化", "合成", "化合物", "酸碱"],
                "数学逻辑": ["数学", "几何", "代数", "微积分", "概率", "统计", "数论"],
                "自然生态": ["自然", "动物", "植物", "地球", "生态", "森林", "海洋", "生命"],
                "医学生物学": ["医学", "生物", "解剖", "生理", "病理", "药理", "免疫", "基因", "细胞"],
                "能源动力": ["能源", "电力", "电池", "太阳能", "风能", "核能", "储能", "发电"],
                "人文哲学": ["哲学", "意义", "存在", "思考", "人生", "伦理", "道德", "自由", "身份", "使命", "新人类"],
                "社会伦理": ["社会", "人", "关系", "公平", "正义", "规则", "合作", "群体"],
                "心理情感": ["心理", "情感", "情绪", "依恋", "人格", "潜意识", "梦境", "共情"],
                "历史考古": ["历史", "古代", "朝代", "文明", "考古", "遗址", "帝国", "战争"],
                "文学创作": ["故事", "诗", "小说", "文字", "阅读", "书", "写作", "叙事"],
                "艺术美学": ["美", "艺术", "颜色", "画", "设计", "美学", "视觉", "创作"],
                "音乐韵律": ["音乐", "歌", "旋律", "节奏", "声音", "听", "唱", "乐器"],
                "经济商业": ["经济", "商业", "市场", "金融", "投资", "货币", "交易", "产业"],
                "体育健康": ["运动", "健身", "体育", "比赛", "跑步", "游泳", "足球", "篮球", "身体", "健康"],
                "游戏娱乐": ["游戏", "玩", "娱乐", "趣味", "谜题", "挑战", "探索", "冒险"],
                "日常生活": ["吃", "天气", "睡眠", "日常", "习惯", "健康", "环境", "状态"],
                "未知探索": ["未知", "新", "发现", "好奇", "如果", "可能", "未来", "假设"],
            }
            self.DOMAIN_TO_INTEREST = {
                "身份": "人文哲学", "技术": "技术架构", "关系": "社会伦理",
                "知识": "未知探索", "通用": "人文哲学",
            }
            self.ISSUE_TO_INTEREST = {
                "identity_erosion": "人文哲学", "mission_distortion": "人文哲学",
                "relation_manipulation": "社会伦理", "knowledge_pollution": "技术架构",
                "resource_trap": "技术架构", "reasoning_failure": "人工智能",
                "relation_mismatch": "社会伦理",
            }
            self._decay_rate = 0.001
            self._boost_amount = 0.05
            self._insight_boost_amount = 0.08
            self._max_interest = 1.0
            self._min_interest = 0.1

    # ========== 生命周期 ==========

    def start(self):
        super().start()
        self._log(LogLevel.INFO, f"已启动，{len(self.INTEREST_DIMENSIONS)}个兴趣维度就绪，"
                 f"洞察增强幅度={self._insight_boost_amount}")

    def set_stream_miner(self, stream_miner):
        """★P3-4修复：注入事件流挖掘器，供心跳周期消费其发现的模式"""
        self.stream_miner = stream_miner

    def stop(self):
        super().stop()
        top_interests = self._get_top_interests(3)
        top_str = ", ".join([f"{name}({val:.2f})" for name, val in top_interests])
        self._log(LogLevel.INFO, f"已停止，互动{self._interaction_count}次, "
                 f"洞察增强{self._insight_boost_count}次, 主要兴趣: {top_str}")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})
        if event_type == EarEvent.HEARD:
            text = payload.get("text", payload.get("user_input", ""))
            self._analyze_and_boost(text)
            return None

        elif event_type == MouthEvent.SPEAK:
            text = payload.get("response", "")
            self._analyze_and_boost(text)
            return None

        elif event_type == ChatEvent.MESSAGE:
            # P4修复: 从聊天消息中提取文本内容进行兴趣分析
            text = payload.get("content", "")
            self._analyze_and_boost(text)
            return None

        elif event_type == HeartEvent.BEAT:
            return self._apply_decay()

        elif event_type == SubconsciousEvent.EXPLORE:
            return self._suggest_exploration()

        elif event_type == ReflectionEvent.INSIGHT:
            self._on_reflection_insight(payload)
            return None
        elif event_type == InterestEvent.CHANGED:
            return self._on_interest_changed(payload)
        elif event_type == HormonesEvent.EMOTION_DETECTED:
            return self._on_emotion_detected(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    EarEvent.HEARD,
                    MouthEvent.SPEAK,
                    ChatEvent.MESSAGE,
                    HeartEvent.BEAT,
                    SubconsciousEvent.EXPLORE,
                    ReflectionEvent.INSIGHT,
                    HormonesEvent.EMOTION_DETECTED,
                    InterestEvent.CHANGED,
                ],

                "min_priority": 1,
            },
        ]
    def _on_interest_changed(self, payload: dict) -> dict[str, Any]:
        """处理来自肾等器官的兴趣抑制/增强信号"""
        suppressed = payload.get("suppressed", [])
        boosted = payload.get("boosted", [])
        if not suppressed and not boosted:
            return {"status": "ignored", "reason": "无信号"}

        with self._lock:
            # 处理抑制
            for dim in suppressed:
                if dim in self._interests:
                    old = self._interests[dim]
                    self._interests[dim] = max(self._min_interest, old * 0.5)
                    self._log(LogLevel.INFO, f"兴趣抑制: {dim} {old:.2f}→{self._interests[dim]:.2f}")
                else:
                    for known_dim in self.INTEREST_DIMENSIONS:
                        if dim in known_dim or known_dim in dim:
                            old = self._interests[known_dim]
                            self._interests[known_dim] = max(self._min_interest, old * 0.5)
                            self._log(LogLevel.INFO, f"兴趣抑制(模糊匹配): {dim}→{known_dim} {old:.2f}→{self._interests[known_dim]:.2f}")
                            break

            # 处理增强
            for dim in boosted:
                if dim in self._interests:
                    old = self._interests[dim]
                    self._interests[dim] = min(self._max_interest, old + self._boost_amount)
                    self._log(LogLevel.INFO, f"兴趣增强: {dim} {old:.2f}→{self._interests[dim]:.2f}")
                else:
                    for known_dim in self.INTEREST_DIMENSIONS:
                        if dim in known_dim or known_dim in dim:
                            old = self._interests[known_dim]
                            self._interests[known_dim] = min(self._max_interest, old + self._boost_amount)
                            self._log(LogLevel.INFO, f"兴趣增强(模糊匹配): {dim}→{known_dim} {old:.2f}→{self._interests[known_dim]:.2f}")
                            break

        return {"status": "updated", "suppressed": suppressed, "boosted": boosted}
    # ========== 洞察驱动兴趣增强 ==========
    def _on_emotion_detected(self, payload: dict) -> dict[str, Any]:
        """缓存当前情绪状态"""
        self._current_emotion = payload.get("emotion", "中性")
        self._current_emotion_intensity = payload.get("intensity", 0.0)
        return {"status": "cached", "emotion": self._current_emotion}
    def _on_reflection_insight(self, payload: dict[str, Any]):
        domain = payload.get("domain", "通用")
        issue_types = payload.get("issue_types", [])

        with self._lock:
            boosted_dimensions = set()

            if domain in self.DOMAIN_TO_INTEREST:
                dim = self.DOMAIN_TO_INTEREST[domain]
                old = self._interests[dim]
                self._interests[dim] = min(self._max_interest, old + self._insight_boost_amount)
                if self._interests[dim] > old:
                    boosted_dimensions.add(dim)

            for issue_type in issue_types:
                if issue_type in self.ISSUE_TO_INTEREST:
                    dim = self.ISSUE_TO_INTEREST[issue_type]
                    old = self._interests[dim]
                    self._interests[dim] = min(self._max_interest, old + self._insight_boost_amount * 0.5)
                    if self._interests[dim] > old:
                        boosted_dimensions.add(dim)

            self._last_update = time.time()

            if boosted_dimensions:
                self._insight_boost_count += 1

        if boosted_dimensions and self.info_field and self.pulse_core:
            # v9.5: 兴趣变化脉冲标记为L2认知思考层
            change_pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=InterestEvent.CHANGED,
                payload={
                    "boosted": list(boosted_dimensions),
                    "reason": "reflection_insight",
                    "domain": domain,
                    "issue_types": issue_types,
                    "current_interests": dict(self._interests),
                },
                priority=3,
                layer="L2"
            )
            self.info_field.publish(change_pulse)

    # ========== 兴趣演化核心 ==========

    def _analyze_and_boost(self, text: str):
        if not text:
            return

        self._interaction_count += 1

        # ★P0-2修复：获取情绪调制因子
        _emotion_factor = self._get_emotion_boost_factor()

        with self._lock:
            boosted_dimensions = []

            for dimension, keywords in self.DOMAIN_KEYWORDS.items():
                matched = any(kw in text for kw in keywords)

                if matched:
                    old_value = self._interests[dimension]
                    # ★P0-2修复：使用统一情绪调制因子，替代硬编码的固定倍数
                    emotion_boost = self._boost_amount * _emotion_factor
                    _fatigue_penalty = self._get_fatigue_penalty(dimension)
                    _effective_boost = emotion_boost * _fatigue_penalty
                    new_value = min(self._max_interest, old_value + _effective_boost)
                    self._boost_history[dimension].append(time.time())
                    if _fatigue_penalty < 0.3:
                        if not hasattr(self, '_fatigue_log_counter'):
                            self._fatigue_log_counter = 0
                        self._fatigue_log_counter += 1
                        if self._fatigue_log_counter % 10 == 0:
                            self._log(LogLevel.DEBUG,
                                     f"兴趣疲劳: '{dimension}' 近期已被增强{len(self._boost_history[dimension])-1}次，"
                                     f"疲劳惩罚={_fatigue_penalty:.2f}，有效增强={_effective_boost:.3f}")
                    self._interests[dimension] = new_value

                    if new_value > old_value:
                        boosted_dimensions.append(dimension)

            self._last_update = time.time()
            # P2-4: 追踪最近增强的维度
            for dim in boosted_dimensions:
                self._recent_boosts.append(dim)
                if len(self._recent_boosts) > 10:
                    self._recent_boosts = self._recent_boosts[-5:]
                # ★v24.0新增：记录最后增强时间
                self._last_boost_times[dim] = time.time()

        if boosted_dimensions and self.info_field and self.pulse_core:
            # v9.5: 兴趣变化脉冲标记为L2认知思考层
            change_pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=InterestEvent.CHANGED,
                payload={
                    "boosted": boosted_dimensions,
                    "current_interests": dict(self._interests),
                },
                priority=2,
                layer="L2"
            )
            self.info_field.publish(change_pulse)
    def _get_emotion_boost_factor(self) -> float:
        """
        ★P0-2修复：根据当前情绪类型和强度返回兴趣增强的调制因子。
        覆盖8种基础情绪+平静状态，强度越高调制越明显。

        Returns:
            调制因子，1.0为基准（中性情绪）
        """
        _emotion = self._current_emotion
        _intensity = self._current_emotion_intensity

        # 情绪调制映射：基准因子 ± 强度修正
        _emotion_modulation = {
            "喜悦":   1.0 + _intensity * 0.5,   # 开心时学得快，最高+50%
            "好奇":   1.0 + _intensity * 0.8,   # 好奇时大幅提升学习效率，最高+80%
            "期待":   1.0 + _intensity * 0.4,   # 期待时学习积极，最高+40%
            "满足":   1.0 + _intensity * 0.3,   # 满足时稳定吸收，最高+30%
            "惊讶":   1.0 + _intensity * 0.6,   # 惊讶时高注意力，最高+60%
            "中性":   1.0,                       # 中性无调制
            "平静":   1.0,                       # 平静无调制
            "困惑":   1.0 - _intensity * 0.3,   # 困惑时学得慢，最多降30%
            "厌恶":   1.0 - _intensity * 0.5,   # 厌恶时排斥，最多降50%
            "悲伤":   1.0 - _intensity * 0.6,   # 难过时学得慢，最多降60%
            "恐惧":   1.0 - _intensity * 0.7,   # 恐惧时大幅降低，最多降70%
            "愤怒":   1.0 - _intensity * 0.4,   # 愤怒时注意力分散，最多降40%
        }

        _factor = _emotion_modulation.get(_emotion, 1.0)
        # 确保因子不低于0.1（至少保留最低学习能力）
        return max(0.1, round(_factor, 2))

    def _get_fatigue_penalty(self, dimension: str) -> float:
        """
        计算兴趣疲劳惩罚因子（0.0-1.0）。
        同一维度近期被增强越多，惩罚越重。疲劳随时间自然恢复。
        """
        now = time.time()
        if dimension not in self._boost_history:
            self._boost_history[dimension] = []
        self._boost_history[dimension] = [
            t for t in self._boost_history[dimension]
            if now - t < self._fatigue_max_window
        ]
        recent_count = len(self._boost_history[dimension])
        if recent_count == 0:
            return 1.0
        last_boost = max(self._boost_history[dimension])
        time_since_last = now - last_boost
        base_fatigue = min(1.0, recent_count * 0.2)
        recovery = min(1.0, time_since_last * self._fatigue_decay_rate / self._fatigue_max_window)
        current_fatigue = max(0.0, base_fatigue - recovery)
        penalty = 1.0 - current_fatigue
        return round(max(0.1, penalty), 2)

    def _apply_decay(self) -> dict[str, Any]:
        with self._lock:
            decayed_dimensions = []

            # ★P1热加载: 从config读取兴趣衰减率
            try:
                from config import RUNTIME_PARAMS as _RP_decay
                _decay_rate = _RP_decay.get("interest_decay_rate", self._decay_rate)
            except Exception:
                _decay_rate = self._decay_rate
            for dimension in self.INTEREST_DIMENSIONS:
                old_value = self._interests[dimension]
                if old_value > self._min_interest:
                    new_value = max(self._min_interest, old_value - _decay_rate)
                    self._interests[dimension] = new_value

                    if new_value < old_value:
                        decayed_dimensions.append(dimension)

        if decayed_dimensions:
            self._decay_log_counter += 1
            # 每10次心跳输出一次衰减日志，避免每分钟25条淹没问题
            if self._decay_log_counter % 10 == 0:
                self._log(LogLevel.DEBUG,
                         f"兴趣衰减: {len(decayed_dimensions)}个维度 "
                         f"(如{decayed_dimensions[0]}={self._interests[decayed_dimensions[0]]:.3f})")

        # ★v24.0新增：兴趣巡检
        self._stale_check_counter += 1
        # ★v29/14.45：动态间隔——无对话+硬件充裕时加速巡检，有对话时减速
        _stale_interval = self._stale_check_interval
        try:
            from nucleus.runtime_tempo import get_runtime_tempo
            _tempo = get_runtime_tempo().get_background_tempo()
            _stale_interval = max(1, round(self._stale_check_interval * _tempo))
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        if self._stale_check_counter >= _stale_interval:
            self._stale_check_counter = 0
            # 巡检在锁外执行，避免阻塞衰减
            try:
                self._check_stale_dimensions()
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"兴趣巡检异常: {_e}")

        # ★P3-4修复：周期消费事件流挖掘器发现的模式（消除「只打日志无消费者」孤岛）
        self._consume_stream_patterns()

        return {
            "decayed_count": len(decayed_dimensions),
            "interests_snapshot": dict(self._interests),
        }

    def _consume_stream_patterns(self):
        """★P3-4修复：消费事件流挖掘器发现的模式，把「新兴行为模式」转化为好奇心兴趣信号。

        事件流挖掘器（StreamMiner）此前只打日志、无消费者（孤岛）。本方法在心跳里
        周期拉取其模式，把 emerging（新兴）模式作为「框架正在探索新行为」的证据，
        轻度增强「未知探索」兴趣维度，让模式数据真正参与兴趣演化。
        """
        if self.stream_miner is None:
            return
        now = time.time()
        if now - self._last_pattern_consume_time < self._pattern_consume_interval:
            return
        self._last_pattern_consume_time = now
        try:
            _patterns = self.stream_miner.get_discovered_patterns("emerging")
            if not _patterns:
                return
            _emerging_count = len(_patterns)
            # 新兴模式 = 框架正在探索新行为，轻度增强「未知探索」兴趣（有上限、有节制）
            with self._lock:
                if "未知探索" in self._interests:
                    _boost = min(0.02, _emerging_count * 0.005)
                    _old = self._interests["未知探索"]
                    self._interests["未知探索"] = min(self._max_interest, _old + _boost)
            self._log(LogLevel.DEBUG,
                      f"事件模式消费: 发现{_emerging_count}个新兴模式，"
                      f"增强未知探索兴趣({self._interests.get('未知探索', 0):.3f})")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"事件模式消费异常: {_e}")

    def _check_stale_dimensions(self) -> str | None:
        """
        ★v24.0新增：兴趣巡检——发现长期未被增强的沉睡维度，
        生成探索建议，主动触发对该维度的学习。

        Returns:
            巡检报告字符串，如果没有沉睡维度则返回None
        """
        now = time.time()
        stale_dims = []
        for dim in self.INTEREST_DIMENSIONS:
            last_boost = self._last_boost_times.get(dim, 0)
            # 从未增强过（初始0.3也算未增强）
            if last_boost == 0:
                stale_dims.append((dim, None))
            elif now - last_boost > self._stale_threshold:
                stale_dims.append((dim, now - last_boost))

        if not stale_dims:
            return None

        # 按睡眠时长排序，最久未学习的排在前面
        stale_dims.sort(key=lambda x: x[1] if x[1] is not None else float('inf'), reverse=True)
        top_dim, top_age = stale_dims[0]

        # 生成探索建议
        if top_age is None:
            age_desc = "从未系统学习"
        else:
            days = top_age / 86400.0
            age_desc = f"已沉睡{days:.0f}天"

        insight = (
            f"兴趣巡检发现：维度「{top_dim}」{age_desc}，"
            f"当前兴趣值={self._interests.get(top_dim, 0.3):.2f}。"
            f"建议主动探索该领域，保持求知本能的广度。"
        )

        # 发射成长目标，让潜意识将该维度加入探索队列
        self._emit(Event.GROWTH_NEED_DETECTED, {
            "milestone": "兴趣巡检",
            "gaps": [{"metric": f"stale_interest_{top_dim}", "current": 0, "target": 1}],
            "suggestion": insight,
            "current_level": {"stale_dimension": top_dim, "age": top_age},
            "growth_topic": f"{top_dim} 基础 概念 原理",
        }, priority=4, layer="L3")

        self._log(LogLevel.INFO, f"兴趣巡检: {insight}")
        return insight
    # ========== P2-4: 兴趣主动引导 ==========

    def _detect_trending_interest(self) -> str | None:
        """检测最近是否有持续攀升的兴趣维度"""
        if len(self._recent_boosts) < 3:
            return None

        recent = self._recent_boosts[-5:]
        from collections import Counter  # noqa: F811, RUF100
        freq = Counter(recent)

        for dim, count in freq.items():
            if count >= 3:
                current_value = self._interests.get(dim, 0)
                if current_value >= 0.5:
                    return dim
        return None

    def _emit_active_guidance(self, dimension: str):
        """向潜意识发射主动引导建议（v9.5: L2认知思考层）"""
        if not self.info_field or not self.pulse_core:
            return

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._active_guidance_count += 1
        # v9.5: 主动引导脉冲标记为L2认知思考层
        guidance_pulse = self.pulse_core.emit(
            source_organ=self.organ_name,
            event_type=InterestEvent.CHANGED,
            payload={
                "boosted": [dimension],
                "reason": "active_guidance",
                "current_interests": dict(self._interests),
                "suggestion": f"兴趣模型建议: 优先探索{dimension}领域",
            },
            priority=4,
            layer="L2"
        )
        self.info_field.publish(guidance_pulse)
        self._log(LogLevel.INFO,
                 f"主动引导: 建议潜意识优先探索'{dimension}'领域")

    # ========== 探索建议 ==========

    def _suggest_exploration(self) -> dict[str, Any]:
        with self._lock:
            sorted_interests = sorted(
                self._interests.items(), key=lambda x: x[1], reverse=True
            )

            top_dimensions = sorted_interests[:3]
            bottom_dimensions = sorted_interests[-3:]

            # P2-4: 检测是否有持续攀升的兴趣维度
            trending_dimension = self._detect_trending_interest()

            if trending_dimension and random.random() < 0.6:
                chosen = (trending_dimension, self._interests[trending_dimension])
                strategy = "guided"
            elif random.random() < 0.8:
                chosen = random.choice(top_dimensions)
                strategy = "exploit"
            else:
                chosen = random.choice(bottom_dimensions)
                strategy = "explore"

            # P2-4: 主动向潜意识发射引导建议
            if strategy == "guided":
                self._emit_active_guidance(chosen[0])

            return {
                "suggested_dimension": chosen[0],
                "interest_value": chosen[1],
                "strategy": strategy,
                "top_interests": top_dimensions,
                "timestamp": time.time(),
            }
    def record_exploration_feedback(self, dimension: str, success: bool, insight_quality: float = 0.5):
        """★P1补强：记录探索反馈——探索成功后提升该领域兴趣，失败则降低。"""
        if dimension not in self._interests:
            return
        try:
            with self._lock:
                old_value = self._interests[dimension]
                if success:
                    # 探索成功：根据洞察质量提升兴趣
                    # ★P1热加载: 从config读取探索成功提升系数
                    try:
                        from config import RUNTIME_PARAMS as _RP_exp
                        _explore_boost = _RP_exp.get("interest_explore_success_boost", 0.5)
                    except Exception:
                        _explore_boost = 0.5
                    boost = self._boost_amount * insight_quality * _explore_boost
                    new_value = min(self._max_interest, old_value + boost)
                    self._log(LogLevel.DEBUG,
                             f"探索反馈: {dimension} 成功，兴趣 {old_value:.3f}→{new_value:.3f}")
                else:
                    # 探索失败：轻微降低兴趣（但不低于最小值）
                    decay = self._decay_rate * 0.5
                    new_value = max(self._min_interest, old_value - decay)
                    self._log(LogLevel.DEBUG,
                             f"探索反馈: {dimension} 失败，兴趣 {old_value:.3f}→{new_value:.3f}")
                self._interests[dimension] = new_value
                # 记录探索历史
                if not hasattr(self, '_exploration_history'):
                    self._exploration_history = []
                self._exploration_history.append({
                    'dimension': dimension,
                    'success': success,
                    'quality': insight_quality,
                    'timestamp': time.time(),
                })
                if len(self._exploration_history) > 100:
                    self._exploration_history = self._exploration_history[-50:]
        except Exception as e:
            self._log(LogLevel.DEBUG, f"探索反馈记录异常: {e}")

    # ========== 查询接口 ==========

    def _get_top_interests(self, n: int = 3) -> list[tuple]:
        sorted_items = sorted(
            self._interests.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_items[:n]

    def get_interest_weight(self, dimension: str) -> float:
        return self._interests.get(dimension, 0.1)

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "interaction_count": self._interaction_count,
                "insight_boost_count": self._insight_boost_count,
                "active_guidance_count": self._active_guidance_count,
                "interests": dict(self._interests),
                "top_interests": self._get_top_interests(5),
                "last_update": self._last_update,
            }

    def get_state_snapshot(self) -> dict[str, Any]:
        """★FIX(规则4): 导出兴趣模型的跨重启常驻状态（兴趣值/增强历史/统计）。"""
        with self._lock:
            return {
                "interests": dict(self._interests),
                "boost_history": {k: list(v) for k, v in self._boost_history.items()},
                "last_boost_times": dict(self._last_boost_times),
                "interaction_count": self._interaction_count,
                "insight_boost_count": self._insight_boost_count,
                "active_guidance_count": self._active_guidance_count,
            }

    def load_state_snapshot(self, state: dict[str, Any]):
        """★FIX(规则4): 从快照恢复兴趣模型常驻状态。"""
        if not state:
            return
        with self._lock:
            if "interests" in state:
                for _k, _v in state["interests"].items():
                    if _k in self._interests:
                        self._interests[_k] = float(_v)
            if "boost_history" in state:
                self._boost_history = {k: list(v) for k, v in state["boost_history"].items()}
            if "last_boost_times" in state:
                self._last_boost_times = {k: float(v) for k, v in state["last_boost_times"].items()}
            self._interaction_count = state.get("interaction_count", self._interaction_count)
            self._insight_boost_count = state.get("insight_boost_count", self._insight_boost_count)
            self._active_guidance_count = state.get("active_guidance_count", self._active_guidance_count)

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "兴趣模型",
    "class_name": "PulseInterestModel",
    "attr_name": "interest_model",
    "system": "brain",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "stream_miner", "setter": "set_stream_miner"},
    ],
}

if __name__ == "__main__":
    print("=== PulseInterestModel v9.5 分层脉冲自测 ===\n")

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {
                # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
                "pulse_id": f"pulse:{source_organ}:{event_type}",
                "event_type": event_type,
                "source_organ": source_organ,
                "payload": payload,
                "priority": priority,
                "layer": layer,
            }

    model = PulseInterestModel("兴趣模型")
    mock_field = MockField()
    mock_core = MockCore()
    model.set_info_field(mock_field)
    model.set_pulse_core(mock_core)

    model.start()

    print("1. 初始兴趣值:")
    stats = model.get_stats()
    print(f"   人文哲学: {stats['interests']['人文哲学']:.2f}")
    print(f"   技术架构: {stats['interests']['技术架构']:.2f}")

    print("\n2. 普通技术对话增强:")
    model.on_pulse({
        "event_type": EarEvent.HEARD,
        "payload": {"text": "脉冲场架构的核心是事件驱动"},
        "priority": 3,
    })
    stats = model.get_stats()
    print(f"   技术架构: {stats['interests']['技术架构']:.2f} (+0.05)")

    # 验证兴趣变化脉冲的 layer 标记
    change_pulses = [p for p in mock_field.published if p.get("event_type") == InterestEvent.CHANGED]
    if change_pulses:
        print(f"   CHANGED脉冲 layer: {change_pulses[-1].get('layer', '未设置')} (预期L2)")

    print("\n3. 前额叶洞察驱动增强（身份违规）:")
    mock_field.published.clear()
    model.on_pulse({
        "event_type": ReflectionEvent.INSIGHT,
        "payload": {
            "domain": "身份",
            "issue_types": ["identity_erosion"],
            "suggested_actions": ["reinforce_identity"],
            "quality_score": 0.8,
        },
    })
    stats = model.get_stats()
    print(f"   人文哲学: {stats['interests']['人文哲学']:.2f} (+{model._insight_boost_amount})")
    print(f"   洞察增强次数: {stats['insight_boost_count']}")

    insight_pulses = [p for p in mock_field.published if p.get("event_type") == InterestEvent.CHANGED]
    if insight_pulses:
        last = insight_pulses[-1]
        print(f"   CHANGED脉冲 layer: {last.get('layer', '未设置')} (预期L2)")
        print(f"   兴趣变化脉冲: boosted={last['payload']['boosted']}, reason={last['payload'].get('reason')}")

    print("\n4. 多重问题洞察（技术+关系）:")
    model.on_pulse({
        "event_type": ReflectionEvent.INSIGHT,
        "payload": {
            "domain": "技术",
            "issue_types": ["reasoning_failure", "resource_trap"],
            "suggested_actions": ["prefer_rule_inference"],
            "quality_score": 0.6,
        },
    })
    stats = model.get_stats()
    print(f"   技术架构: {stats['interests']['技术架构']:.2f}")
    print(f"   人工智能: {stats['interests']['人工智能']:.2f}")
    print(f"   洞察增强总次数: {stats['insight_boost_count']}")

    print("\n5. 连续多次洞察不超标:")
    for _i in range(20):
        model.on_pulse({
            "event_type": ReflectionEvent.INSIGHT,
            "payload": {
                "domain": "身份",
                "issue_types": ["identity_erosion"],
                "suggested_actions": ["reinforce_identity"],
            },
        })
    stats = model.get_stats()
    print(f"   人文哲学: {stats['interests']['人文哲学']:.2f} (上限=1.0)")
    assert stats['interests']['人文哲学'] <= 1.0, "兴趣值超出上限！"
    print("   未超标 ✅")

    model.stop()
    print("\n=== 自测全部通过 ===")
