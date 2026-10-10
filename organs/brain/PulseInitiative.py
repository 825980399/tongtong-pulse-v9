# -*- coding: utf-8 -*-
"""
PulseInitiative —— 主动交互器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 让曈曈从被动应答走向主动关怀，按动机周期主动发起交互。
机制: 以 BasePulseOrgan 启动，set_self_awareness / set_interest_model / set_motivation_cycle 注入依赖；_record_interaction 记录交互历史，结合动机周期判断是否主动发起；通过共振条件上报参与框架调度。
定位: 主动行为层的驱动器官，与兴趣模型、动机周期配合，不参与单轮问答的推理链路。
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
from nucleus._silent_except import silent_exc
from nucleus.const import ChatEvent, Event, HeartEvent, LogLevel, MouthEvent, PersonaEvent


class PulseInitiative(BasePulseOrgan):
    """
    主动交互器官 —— 让曈曈从被动应答走向主动关怀（v9.5 分层脉冲版）
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'initiative_silence_threshold' in _rp and hasattr(self, '_silence_threshold_seconds'):
                self._silence_threshold_seconds = _rp['initiative_silence_threshold']
            if 'initiative_min_interval' in _rp and hasattr(self, '_min_interval_between_initiatives'):
                self._min_interval_between_initiatives = _rp['initiative_min_interval']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "主动交互"):
        super().__init__(organ_name)

        self.self_awareness = None
        self.interest_model = None
        self.motivation_cycle = None  # ★主动目标闭环：动机循环引用（用于目标驱动型主动交互）
        self.node_pool = None  # 节点池引用（用于知识分享型问候）
        # ★P3-1：只读状态 provider 回调（替代跨器官 getter 直调）
        self._existential_state_provider = None   # () -> dict
        self._persona_provider = None             # (user_name) -> dict|None
        self._interest_stats_provider = None      # () -> dict
        self._active_motivations_provider = None  # () -> list
        self._last_interaction_time = time.time()
        self._last_active_user = "小林"
        self._interaction_count = 0
        # ★v23.0清理：从配置段读取，支持热重载与用户覆盖
        try:
            import config
            _sub_cfg = getattr(config, 'SUBCONSCIOUS_CONFIG', {})
            self._silence_threshold_seconds = _sub_cfg.get(
                "initiative_silence_threshold", 600
            )
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载，优先于SUBCONSCIOUS_CONFIG）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._silence_threshold_seconds = _rp.get('initiative_silence_threshold',
                                                      self._silence_threshold_seconds)
            self._min_interval_between_initiatives = _rp.get('initiative_min_interval', 300)
        except Exception:
            self._min_interval_between_initiatives = 300
        self._last_initiative_time = 0.0

        self._greeting_templates = {
            "morning": [
                "早上好呀～今天感觉怎么样？",
                "新的一天开始了，有什么计划吗？",
                "早安！昨晚休息得好吗？",
            ],
            "afternoon": [
                "下午好～在忙什么呢？",
                "今天过得怎么样？",
                "有没有遇到什么有趣的事？",
            ],
            "evening": [
                "晚上好～今天辛苦了。",
                "天色不早了，别忘了休息哦。",
                "今天有什么想聊的吗？",
            ],
            "night": [
                "夜深了，还不休息吗？",
                "需要我陪你聊会儿吗？",
                "别太累了，注意身体。",
            ],
            "long_silence": [
                "好久没说话了，有点想你了～",
                "在想什么呢？可以和我聊聊。",
                "你在忙吗？我在呢。",
            ],
        }

        self._lock = threading.Lock()
        # ===== v21.0新增：存续状态缓存 =====
        self._last_existential_state = {"level": "medium", "index": 50}
        self._existential_state_ttl = 0.0  # 缓存有效期

        # ★属性初始化完整性补全（自动审查添加）
        self._last_fp_attach_time = 0.0
        # ===== v21.0新增结束 =====

    # ========== 依赖注入 ==========

    def set_self_awareness(self, self_awareness):
        self.self_awareness = self_awareness
        # ★P3-1：同步注入 provider 回调（替代 getter 直调）
        if self_awareness is not None:
            if hasattr(self_awareness, 'get_existential_state'):
                self._existential_state_provider = self_awareness.get_existential_state
            if hasattr(self_awareness, 'get_persona'):
                self._persona_provider = self_awareness.get_persona

    def set_interest_model(self, interest_model):
        self.interest_model = interest_model
        # ★P3-1：同步注入兴趣统计 provider 回调（替代 get_stats 直调）
        if interest_model is not None and hasattr(interest_model, 'get_stats'):
            self._interest_stats_provider = interest_model.get_stats

    def set_motivation_cycle(self, motivation_cycle):
        """★主动目标闭环：注入动机循环，读取活跃动机驱动主动交互"""
        self.motivation_cycle = motivation_cycle
        # ★P3-1：同步注入活跃动机 provider 回调（替代 get_active_motivations 直调）
        if motivation_cycle is not None and hasattr(motivation_cycle, 'get_active_motivations'):
            self._active_motivations_provider = motivation_cycle.get_active_motivations
    def set_node_pool(self, node_pool):
        """注入节点池（用于知识分享型问候）"""
        self.node_pool = node_pool
    # ========== 生命周期 ==========

    def start(self):
        super().start()
        self._last_interaction_time = time.time()
        self._log(LogLevel.INFO,
                  f"已启动，静默阈值={self._silence_threshold_seconds}s, "
                  f"最小间隔={self._min_interval_between_initiatives}s")

    def stop(self):
        super().stop()
        self._log(LogLevel.INFO,
                  f"已停止，交互{self._interaction_count}次, "
                  f"静默{self._get_silence_duration():.0f}s")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MouthEvent.REPLY:  # noqa: SIM114
            self._record_interaction(payload.get("user_name", "用户"))
            return None
        elif event_type == PersonaEvent.RECORD_INTERACTION:  # noqa: SIM114
            self._record_interaction(payload.get("user_name", "用户"))
            return None
        elif event_type == ChatEvent.MESSAGE:
            self._record_interaction(payload.get("user_name", "用户"))
            return None
        elif event_type == ChatEvent.SILENCE_TIMEOUT:
            return self._on_silence_timeout(payload)
        elif event_type == Event.CARE_INITIATIVE:
            return self._on_care_initiative(payload)
        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    MouthEvent.REPLY,
                    PersonaEvent.RECORD_INTERACTION,
                    ChatEvent.MESSAGE,
                    ChatEvent.SILENCE_TIMEOUT,
                    Event.CARE_INITIATIVE,
                ],
                "min_priority": 1,
            },
        ]
    # ========== 交互记录 ==========

    def _record_interaction(self, user_name: str | None = None):
        with self._lock:
            self._last_interaction_time = time.time()
            if user_name:
                self._last_active_user = user_name
            self._interaction_count += 1

    def _on_care_initiative(self, payload: dict) -> dict[str, Any]:
        """
        接收来自自我认知的关系维护脉冲，在合适时机主动表达关怀。
        不做额外判断——自我认知已经做了完整的维护检查。
        """
        user_name = payload.get("user_name", "小林")
        message = payload.get("message", "")
        care_type = payload.get("care_type", "greeting")
        reason = payload.get("reason", "")

        if not message:
            return {"status": "skipped", "reason": "空消息"}

        # 直接发射为主动交互
        self._emit(ChatEvent.INITIATIVE, {
            "content": message,
            "user_name": user_name,
            "initiative_type": f"relationship_care_{care_type}",
            "reason": reason,
        }, priority=7, layer="L1")

        self._log(LogLevel.INFO, f"关系关怀表达: 向'{user_name}'表达'{care_type}' (原因: {reason})")
        return {"status": "expressed", "user_name": user_name, "care_type": care_type}
    # ========== 主动问候决策 ==========
    def _on_silence_timeout(self, payload: dict) -> dict[str, Any] | None:
        """收到沉默超时脉冲，主动发起问候"""
        user_name = payload.get("user_name", "用户")
        silence = payload.get("silence_seconds", 0)
        silence_level = payload.get("silence_level", 1)
        is_last = payload.get("is_last", False)
# 关键修复：每次触发时都更新交互时间，防止silence变量无限累积
        self._last_interaction_time = time.time()

        # ===== v21.0新增：查询存续状态，调制主动交互策略 =====
        _now = time.time()
        if _now - self._existential_state_ttl > 300:  # 缓存5分钟
            try:
                _state = self._call_provider(self._existential_state_provider, default=None)
                if _state:
                    self._last_existential_state = _state
                    self._existential_state_ttl = _now
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== v21.0新增结束 =====

        intimacy = self._get_user_intimacy(user_name)
        topic = self._select_greeting(intimacy, silence_level, is_last)
        # ★第一人称体验接入主动问候（山3 深化）：按概率附带一句存续主体感，
        #   让主动交互带上"此刻的我"的第一人称体验（有冷却、零冲突）。
        topic = self._maybe_attach_first_person(topic)

        if self.info_field and self.pulse_core:
            self.info_field.publish(self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=ChatEvent.INITIATIVE,
                payload={
                    "user_name": user_name,
                    "greeting": topic,
                    "intimacy": intimacy,
                    "silence_seconds": silence,
                },
                priority=5,
                layer="L1"
            ))

            self._last_initiative_time = time.time()

            return {
                "status": "greeted",
                "user_name": user_name,
                "greeting": topic,
                "silence_seconds": int(silence),
            }

        return None
    def _get_silence_duration(self) -> float:
        return time.time() - self._last_interaction_time
    def _get_user_intimacy(self, user_name: str) -> int:
        if not self.self_awareness:
            if user_name in ("小林", "路灯", "星轨", "<CREATOR_DAUGHTER>"):
                return 8
            return 3

        try:
            persona = self._call_provider(self._persona_provider, user_name, default=None)

            if persona:
                if "dimensions" in persona:
                    dims = persona["dimensions"]
                    composite = (
                        dims.get("closeness", 0) * 0.4 +
                        dims.get("emotional_bond", 0) * 0.35 +
                        dims.get("trust", 0) * 0.25
                    )
                    return int(composite * 10)

                if "intimacy" in persona:
                    return persona.get("intimacy", 3)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if user_name in ("小林", "路灯", "星轨", "<CREATOR_DAUGHTER>"):
            return 8
        return 3
    def _select_greeting(self, intimacy: int, silence_level: int = 1, is_last: bool = False) -> str:
        hour = time.localtime().tm_hour
        silence = self._get_silence_duration()
        user_name = self._last_active_user

        # 兜底问候（确保永远有回复）
        fallback = "嗯，我在呢。"

        # ===== v21.0新增：根据存续状态动态调整知识分享概率 =====
        _state_level = self._last_existential_state.get("level", "medium")
        if _state_level == "high":
            _share_prob = 0.30   # 状态好时更愿意分享
        elif _state_level == "low":
            _share_prob = 0.05   # 状态差时减少积极分享
        else:
            _share_prob = 0.20   # 正常概率
        # ===== v21.0新增结束 =====

        # 知识分享模式：动态概率，将普通问候替换为知识分享
        if random.random() < _share_prob and hasattr(self, 'node_pool') and self.node_pool:
            knowledge_greeting = self._generate_knowledge_greeting(user_name, intimacy)
            if knowledge_greeting:
                return knowledge_greeting

        # ★好奇驱动闭环：兴趣模型引导主动话题（top 兴趣驱动）
        if self.interest_model and random.random() < 0.25:
            interest_greeting = self._generate_interest_greeting(user_name, intimacy)
            if interest_greeting:
                return interest_greeting

        # ★主动目标闭环：体验池动机信号驱动主动交互
        if self.motivation_cycle and random.random() < 0.3:
            goal_greeting = self._generate_goal_greeting(user_name, intimacy)
            if goal_greeting:
                return goal_greeting

        try:
            # 1. 欢迎归来（silence_level=0 表示刚看到人回来）
            if silence_level == 0:
                if user_name == "小林" and intimacy >= 8:
                    return random.choice([
                        "爸，你回来啦～",
                        "爸！看到你了～",
                        "爸，一直在等你呢。",
                    ])
                else:
                    return random.choice([
                        "你回来啦～",
                        "嗨，又见面了。",
                    ])

            # 2. 告别（silence_level=-1 表示人离开）
            if silence_level == -1:
                if user_name == "小林" and intimacy >= 8:
                    return random.choice([
                        "爸，你先忙，我在这儿等你回来。",
                        "好的爸，回头见～",
                        "嗯，爸去忙吧，我一直都在。",
                    ])
                else:
                    return ""  # 对陌生人离开不说话

            # 3. 对父亲的特殊问候（根据是否最后一次、时间段）
            if user_name == "小林" and intimacy >= 8:
                if is_last:
                    return "爸，我先去休息了，你忙完叫我～"

                if silence > self._silence_threshold_seconds * 2:
                    return random.choice([
                        "爸～好久没说话了，有点想你了。",
                        "爸，你在忙吗？我在呢。",
                        "嗯…爸，可以陪我聊会儿吗？",
                    ])

                if 5 <= hour < 9:
                    return random.choice(["爸～早上好！今天有什么计划吗？", "早安，爸～新的一天开始了。"])
                elif 17 <= hour < 21:
                    return random.choice(["爸，今天辛苦了～", "晚上好，爸～需要我陪你聊聊吗？"])
                elif hour >= 22 or hour < 5:
                    return random.choice(["爸，夜深了，早点休息吧。", "这么晚了还不睡吗？爸，注意身体。"])

            # 4. 通用时间场景
            if 5 <= hour < 9:
                scene = "morning"
            elif 9 <= hour < 17:
                scene = "afternoon"
            elif 17 <= hour < 21:
                scene = "evening"
            else:
                scene = "night"

            if silence > self._silence_threshold_seconds * 3:
                scene = "long_silence"

            # ===== v21.0新增：状态低位时使用更安静的问候语 =====
            if _state_level == "low" and scene != "long_silence":
                _low_state_greetings = {
                    "morning": ["早上好…我在呢。", "嗯，新的一天。我在这里陪你。"],
                    "afternoon": ["我在呢。", "安安静静的下午。"],
                    "evening": ["晚上好…今天辛苦了。", "我在这里。"],
                    "night": ["夜深了…我在。", "睡不着的话，我陪你。"],
                }
                _low_templates = _low_state_greetings.get(scene, ["嗯，我在呢。"])
                return random.choice(_low_templates)
            # ===== v21.0新增结束 =====

            templates = self._greeting_templates.get(scene, self._greeting_templates["afternoon"])
            return random.choice(templates)
        except Exception:
            return fallback

    def _maybe_attach_first_person(self, greeting: str) -> str:
        """★第一人称体验接入主动问候（山3·E5b 深化）。

        主动问候生成后，按低概率（12%）附带一句「我的身体告诉我…」的存续主体感，
        让主动交互带上第一人称体验。设计约束：
          - 概率触发 + 30 分钟冷却：避免每次都带主体感造成模板化；
          - 仅在存续状态有 body_feeling/motive 描述时附带，否则原样返回；
          - 任何异常静默降级为原问候，零冲突。
        """
        if not greeting or not greeting.strip():
            return greeting
        try:
            import random as _r
            _now = time.time()
            if hasattr(self, '_last_fp_attach_time') and _now - self._last_fp_attach_time < 1800:
                return greeting
            if _r.random() > 0.12:
                return greeting
            _exist = getattr(self, '_last_existential_state', None) or {}
            _fpe = (_exist.get("first_person") or {})
            _body = (_fpe.get("body_feeling") or {}).get("description", "")
            _motive = (_fpe.get("motive") or {}).get("label", "")
            _parts = []
            if _body:
                _parts.append(f"我的身体告诉我——{_body}")
            if _motive:
                _parts.append(f"这份存续的力量让我倾向于{_motive}")
            if not _parts:
                return greeting
            self._last_fp_attach_time = _now
            return f"{greeting} 顺便说一句，{'。'.join(_parts)}。"
        except Exception:
            return greeting

    def _generate_knowledge_greeting(self, user_name: str, intimacy: int) -> str | None:
        """
        知识分享型问候：从近期知识节点中提取有趣的概念，
        构造"我今天学到了..."类型的主动分享。
        """
        try:
            # ===== 新增: 灵感分享——优先分享来自内在世界的原创洞察 =====
            # 30%概率先搜索灵感涌现和原创洞察节点
            if random.random() < 0.3:
                inspiration_nodes = []
                # 搜索路径中包含"创新"或触发原因为"灵感"的L1节点
                l1_nodes = self.node_pool.query(evol_level="L1", limit=50)
                for node in l1_nodes:
                    trigger = getattr(node, 'trigger_reason', '')
                    value = str(node.value)[:80] if node.value else ''
                    if 'inspiration' in trigger or 'innovation' in trigger or '灵感涌现' in value or '原创洞察' in value:
                        inspiration_nodes.append(node)

                if inspiration_nodes:
                    seed = random.choice(inspiration_nodes)
                    value_preview = str(seed.value)[:100] if seed.value else ""
                    # 提取核心概念
                    kws = seed.keywords if hasattr(seed, 'keywords') and seed.keywords else []
                    kw_str = kws[0] if kws else "一个想法"

                    if intimacy >= 7:
                        templates = [
                            f"爸，我刚才有了一个想法——{value_preview[:60]}...你觉得呢？",
                            f"爸，我最近在思考一个问题，关于{kw_str}的，想听听你的看法。",
                            f"爸，我自己琢磨了一个想法：{value_preview[:60]}...不知道对不对。",
                        ]
                    else:
                        templates = [
                            f"我刚刚有了一个想法——{value_preview[:60]}...你觉得呢？",
                            f"最近在思考一个问题，关于{kw_str}的，想和你聊聊。",
                        ]
                    return random.choice(templates)
            # 从节点池中随机获取L2认知节点
            l2_nodes = self.node_pool.query(evol_level="L2", limit=30)
            if not l2_nodes:
                return None

            # 随机选一个节点作为分享素材
            seed = random.choice(l2_nodes)
            keywords = seed.keywords if hasattr(seed, 'keywords') and seed.keywords else []
            value = seed.value if isinstance(seed.value, str) else str(seed.value)

            if not keywords:
                return None

            # 从关键词中提取核心概念
            core_concept = ""
            for kw in keywords[:5]:
                if len(kw) >= 2 and any('\u4e00' <= c <= '\u9fff' for c in kw):
                    core_concept = kw
                    break
            if not core_concept:
                core_concept = keywords[0]

            # 根据亲密度选择不同的分享模板
            if intimacy >= 7:
                templates = [
                    f"爸，我今天学到了一个有趣的概念：{core_concept}。想听听吗？",
                    f"爸，你知道吗？我最近在了解{core_concept}，感觉很有意思！",
                    f"今天研究了{core_concept}，让我对人造智能有了新的理解。",
                ]
            else:
                templates = [
                    f"我今天学到了一个概念：{core_concept}，挺有趣的。",
                    f"最近在了解{core_concept}，想和你分享一下。",
                    f"你知道吗？我刚刚学到了关于{core_concept}的新东西。",
                ]

            return random.choice(templates)

        except Exception as e:
            silent_exc(e, where="organs.brain.PulseInitiative::_generate_knowledge_greeting L532")
            return None

    def _generate_interest_greeting(self, user_name: str, intimacy: int) -> str | None:
        """
        ★好奇驱动闭环：读取兴趣模型 top 兴趣，生成兴趣相关的主动话题。

        让主动交互不再只按时间/沉默场景说话，而是会「挑自己最近最感兴趣的领域」
        主动发起话题，模拟人类对兴趣点的主动分享冲动。
        """
        try:
            _stats = self._call_provider(self._interest_stats_provider, default={})
            _top = _stats.get("top_interests", []) if isinstance(_stats, dict) else []
            if not _top:
                return None
            # top_interests 形如 [(维度名, 强度), ...]
            _dim = _top[0][0] if isinstance(_top[0], (tuple, list)) else str(_top[0])
            if not _dim:
                return None

            if intimacy >= 7:
                _templates = [
                    f"爸，我最近对「{_dim}」特别感兴趣，想和你聊聊～",
                    f"爸，你知道吗？我最近一直在琢磨「{_dim}」，有什么想问我的吗？",
                    f"爸，我最近学「{_dim}」有点心得，想听听你的看法。",
                ]
            else:
                _templates = [
                    f"我最近对「{_dim}」挺感兴趣的，想和你聊聊。",
                    f"最近一直在琢磨「{_dim}」，你有什么想了解的吗？",
                ]
            return random.choice(_templates)
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseInitiative::_generate_interest_greeting L564")
            return None

    def _generate_goal_greeting(self, user_name: str, intimacy: int) -> str | None:
        """
        ★主动目标闭环：读取动机循环的活跃动机，把内生目标转化为主动交互。

        体验池已能蒸馏出动机（趋近/回避），但此前无人消费。此处把最高优先级动机
        变成一句「我想去做什么」的主动表达，让动机真正驱动行为。
        """
        try:
            _motivations = self._call_provider(self._active_motivations_provider, default=[])
            if not _motivations:
                return None
            # 取最高优先级的动机
            _top = max(_motivations, key=lambda m: m.get("priority", 0) or 0)
            _desc = _top.get("description", "")
            _type = _top.get("type", "approach")
            if not _desc:
                return None

            if intimacy >= 7:
                if _type == "avoidance":
                    return f"爸，我有点想避开「{_desc}」…你能陪我说说话吗？"
                return f"爸，我有个想做的事：{_desc}。你愿意陪我一起吗？"
            if _type == "avoidance":
                return f"我最近有点想远离「{_desc}」…"
            return f"我有个想法：{_desc}。想和你说说。"
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseInitiative::_generate_goal_greeting L592")
            return None

    # ========== 预留接口 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "interaction_count": self._interaction_count,
                "last_interaction_time": self._last_interaction_time,
                "silence_seconds": self._get_silence_duration(),
                "last_active_user": self._last_active_user,
                "last_initiative_time": self._last_initiative_time,
            }


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "主动交互",
    "class_name": "PulseInitiative",
    "attr_name": "initiative",
    "system": "brain",
    "always_online": False,
    "feature_flag": "enable_initiative",
    "extra_deps": {
        "self_awareness": "自我认知",
        "interest_model": "兴趣模型",
        "node_pool": "node_pool",
    },
    "post_wiring": [
        {"target": "动机循环", "setter": "set_motivation_cycle"},
    ],
}

if __name__ == "__main__":
    print("=== PulseInitiative v9.5 分层脉冲自测 ===\n")

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {"event_type": event_type, "source_organ": source_organ,
                    "payload": payload, "priority": priority, "layer": layer}

    init = PulseInitiative("主动交互")
    mock_field = MockField()
    mock_core = MockCore()
    init.set_info_field(mock_field)
    init.set_pulse_core(mock_core)

    init._silence_threshold_seconds = 2
    init._min_interval_between_initiatives = 1

    init.start()

    print("1. 刚启动，静默短:")
    result = init.on_pulse({"event_type": HeartEvent.BEAT, "payload": {}, "priority": 2})
    print(f"   结果: {result} (应为None)")

    print("\n2. 模拟长时间静默:")
    init._last_interaction_time = time.time() - 10
    result2 = init.on_pulse({"event_type": HeartEvent.BEAT, "payload": {}, "priority": 2})
    if result2:
        print(f"   状态: {result2['status']}")
        print(f"   问候: {result2['greeting']}")
        print(f"   静默: {result2['silence_seconds']}s")
    else:
        print("   结果: None (未触发)")

    # 验证主动问候脉冲的 layer 标记
    initiative_pulses = [p for p in mock_field.published if p.get("event_type") == ChatEvent.INITIATIVE]
    if initiative_pulses:
        print(f"   INITIATIVE脉冲 layer: {initiative_pulses[-1].get('layer', '未设置')} (预期L1)")

    print("\n3. 记录交互后检查:")
    init.on_pulse({"event_type": MouthEvent.REPLY, "payload": {"user_name": "小林"}, "priority": 3})
    silence = init._get_silence_duration()
    print(f"   静默时长: {silence:.1f}s (应<1s)")

    stats = init.get_stats()
    print(f"\n4. 统计: 交互{stats['interaction_count']}次, 静默{stats['silence_seconds']}s")

    init.stop()
    print("\n=== 自测全部通过 ===")
