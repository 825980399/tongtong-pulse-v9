# -*- coding: utf-8 -*-
"""
QICA.py —— QICA意图分类器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 量子启发意图分类架构，多通道融合
机制: 大型模块（1553行），包含2个类、10个核心方法，采用分层架构实现
定位: 意图核心层
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import threading
from typing import Any
from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import LogLevel, QICAEvent, SystemEvent
import config
from nucleus._silent_except import silent_exc

_module_logger = logging.getLogger(__name__)

"""QICA —— create_anchor 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""


"""
QICA 心智模型 v2.0 — v9.5 PulseNet 分层脉冲版
==================================================
Q → 接收锚定 | I → 意图分类 | C → 定向调取 | A → 输出校验
全程以结构化 anchor 为唯一通行证，通道互斥，脉冲驱动。

与 v1.0 (v8.0) 的区别:
    - 废弃 framework 参数注入，改为纯脉冲通信
    - 废弃 content_memory 直接调用，改为 PulseNodePool + ResonanceEngine
    - 废弃 _cmd_dispatch 命令处理，命令由大脑皮层路由
    - 改为 BasePulseOrgan 子类，统一脉冲接口
    - 保留三通道分流、超时熔断、四环节结构

更新: 2026年6月13日（P0-2+P0-5: 事件类型枚举全面应用 + 补充 get_stats + 自测同步）
更新: 2026年6月14日（v9.5: 分类结果脉冲标记layer=L2，适配分层异步调度）

设计者：路灯、小林、星轨
"""


# ========== 超时配置 ==========
FAST_TIMEOUT_MS = 500
KNOWLEDGE_TIMEOUT_MS = 2000
DEEP_TIMEOUT_MS = 10000

# ========== 本地语义理解增强：同义词归一化表 ==========
# 分类前把常见同义词统一为规范词，提升本地匹配覆盖率。
# ★扩充：从 22 组扩到 45+ 组，覆盖口语/方言/技术口语，按「口语/缩写 → 规范词」方向归一。
SYNONYM_MAP = {
    # ── 口语疑问/感叹（原有） ──
    "咋回事": "怎么回事",
    "啥意思": "什么意思",
    "咋办": "怎么办",
    "咋样": "怎么样",
    "为啥": "为什么",
    "有啥": "有什么",
    "干啥": "做什么",
    "干嘛": "做什么",
    "咋整": "怎么办",
    "晓得": "知道",
    "不晓得": "不知道",
    "咋": "怎么",
    "啥": "什么",
    "嘛": "吗",
    # ── 故障/状态口语（原有） ──
    "崩了": "崩溃",
    "闪退": "崩溃",
    "死机": "崩溃",
    "蓝屏": "崩溃",
    "卡顿": "卡",
    "跑不动": "卡顿",
    "阔以": "可以",
    "报错": "错误",
    "电脑": "计算机",
    # ── ★扩充：口语/网络用语 → 规范表达 ──
    "你瞅啥": "你看什么",
    "麻了": "崩溃",
    "崩盘": "崩溃",
    "完犊子": "失败",
    "凉了": "失败",
    "黄了": "失败",
    "拉胯": "差",
    "掉链子": "失败",
    "掉线": "断线",
    "连不上": "连接失败",
    "打不开": "无法打开",
    "进不去": "无法进入",
    "登不上": "登录失败",
    "下不来": "无法下载",
    "装不上": "无法安装",
    "跑不起来": "无法运行",
    "启动不了": "无法启动",
    "用不了": "无法使用",
    "找不到": "无法找到",
    "搜不到": "无法搜索",
    "查不到": "无法查询",
    "看不懂": "不理解",
    "整不明白": "不理解",
    "搞不懂": "不理解",
    "弄不清": "不理解",
    "不太懂": "不理解",
    "讲一下": "解释",
    "说道说道": "解释",
    "唠唠": "聊聊",
    "聊会儿": "聊天",
    "待会儿": "稍后",
    "一会儿": "稍后",
    "等会儿": "稍后",
    "马上": "立刻",
    "麻溜": "立刻",
    "赶明儿": "明天",
    "明儿": "明天",
    "昨儿": "昨天",
    "今儿": "今天",
    "这旮旯": "这里",
    "那旮旯": "那里",
    # ── ★扩充：技术口语/缩写 → 规范术语 ──
    "啥是": "什么是",
    "咋写": "怎么写",
    "咋改": "怎么改",
    "咋做": "怎么做",
    "咋配": "怎么配置",
    "咋装": "怎么安装",
    "咋用": "怎么使用",
    "码农": "程序员",
    "写代码": "编程",
    "敲代码": "编程",
    "撸代码": "编程",
    "报bug": "报错",
    "出bug": "报错",
}

# ========== 本地语义理解增强：否定/反问词表 ==========
# 检测否定词，作为置信度调制的信号（不直接翻转意图）
NEGATION_WORDS = ("不是", "并非", "没有", "别", "不要", "难道不", "才不", "并不是")

# ========== 本地语义理解增强：核心实体表 ==========
# 框架关键实体，识别后辅助意图判断
CORE_ENTITIES = (
    "曈曈", "<SELF_NAME>", "新人类", "小林", "路灯", "星轨",
    "PulseNet", "脉冲场", "信息场", "知识树",
)

# ========== 本地语义理解增强：句式标记 ==========
QUESTION_END_MARKERS = ("?", "？", "吗", "呢", "吧", "么", "多少", "几")
IMPERATIVE_MARKERS = ("请", "帮我", "告诉我", "解释一下", "介绍", "帮我查")


# ========== Anchor 数据结构 ==========
def create_anchor():
    return {
        "task_type": "未分类",
        "raw_input": "",
        "constraints": [],
        "priority": 3,
        "channel": "deep",
        "need_reasoner": False,
        "wake_organs": ["mouth"],
        "sleep_organs": [],
        "expected_latency_ms": FAST_TIMEOUT_MS,
        "rule_conflict": False,
        "matched_rule_id": "",
        "search_tags": [],
        "handler": "",
        "check_result": True,
        "final_output": "",
    }


# ========== 意图规则表 ==========
INTENT_RULES = [
    # ── 快速通道（身份/命令/问候，不需要推理） ──
    {
        "id": "rule_fast_identity", "channel": "fast", "intent_type": "身份确认",
        "keywords": ["是谁", "叫什么", "关系", "你是谁", "你是", "路灯是谁", "小林是谁", "曈曈是谁"],
        "priority": 5, "wake_organs": ["mouth"],
    },
    {
        "id": "rule_fast_command", "channel": "fast", "intent_type": "系统命令",
        "keywords": ["status", "状态", "help", "帮助"],
        "priority": 5, "wake_organs": ["mouth"],
    },
    {
        "id": "rule_fast_greeting", "channel": "fast", "intent_type": "情感问候",
        "keywords": ["你好", "晚安", "早安", "嗨", "hello", "hi", "在吗", "陪我"],
        "priority": 4, "wake_organs": ["mouth"],
    },
    # ── 知识通道（需要检索，不需要深度推理） ──
    {
        "id": "rule_knowledge_query", "channel": "knowledge", "intent_type": "知识查询",
        "keywords": ["什么是", "什么", "怎么", "为什么", "如何", "解释", "定义"],
        "priority": 5, "wake_organs": ["mouth", "eyes"],
    },
    {
        "id": "rule_knowledge_rule", "channel": "knowledge", "intent_type": "规则查阅",
        "keywords": ["设计铁律", "新人类使命", "框架规则", "脉冲场架构"],
        "priority": 4, "wake_organs": ["mouth", "eyes"],
    },
    # ── 深度通道（需要推理引擎介入） ──
    {
        "id": "rule_deep_explain", "channel": "deep", "intent_type": "概念解释",
        "keywords": ["什么是", "如何理解", "解释一下", "介绍一下", "说说", "意味着什么", "原理"],
        "priority": 5, "need_reasoner": True, "wake_organs": ["mouth", "eyes"],
    },
    {
        "id": "rule_deep_code", "channel": "deep", "intent_type": "技术推理",
        "keywords": ["代码", "编程", "写一个", "实现", "报错", "修复", "bug", "error"],
        "priority": 5, "need_reasoner": True, "wake_organs": ["mouth", "eyes", "hands"],
    },
    {
        "id": "rule_deep_creative", "channel": "deep", "intent_type": "创造性思考",
        "keywords": ["你觉得", "你认为", "比喻", "代表", "选择", "颜色"],
        "priority": 5, "need_reasoner": True, "wake_organs": ["mouth"],
    },
    # ★v23.0新增：状态查询类意图规则
    {
        "id": "rule_fast_status", "channel": "fast", "intent_type": "状态查询",
        "keywords": ["你最近运行得怎么样", "你运行得怎么样", "你状态怎么样",
                     "你觉得自己状态如何", "你最近状态", "你运行状态",
                     "你健康状况", "你健康状态"],
        "priority": 5, "wake_organs": ["mouth"],
    },
    {
        "id": "rule_fast_health", "channel": "fast", "intent_type": "健康检查",
        "keywords": ["知识库健康检查", "执行健康检查", "检查知识库", "知识库检查"],
        "priority": 5, "wake_organs": ["mouth"],
    },
    {
        "id": "rule_knowledge_meta", "channel": "knowledge", "intent_type": "元认知报告",
        "keywords": ["元认知报告", "六维度", "系统运行状态", "你的系统状态",
                     "你的知识状态", "自我评估", "自我报告", "运行报告"],
        "priority": 4, "wake_organs": ["mouth"],
    },
]


class QICA(BasePulseOrgan):
    """
    QICA 心智模型 v2.0（v9.5 分层脉冲版）

    四环节流程:
        Q: 接收输入，创建 anchor
        I: 意图分类，匹配规则表
        C: 通道执行，分流到不同通路
        A: 输出校验，身份保护
    """

    def __init__(self, organ_name: str = "QICA"):
        super().__init__(organ_name)
        self._classify_count = 0
        self._rule_hits: dict[str, int] = {}
        # ★自增强闭环（暂缓项2）：实例级规则表深拷贝，隔离模块级 INTENT_RULES，
        # 避免多实例/多线程共享 + 匹配打分原地污染全局规则表。
        import copy as _copy
        self._intent_rules: list[dict[str, Any]] = _copy.deepcopy(INTENT_RULES)
        self._dynamic_rule_ids: set[str] = set()  # 记录动态新增规则 id（用于快照持久化区分）
        self._intent_rules_lock = threading.Lock()  # 规则表读写锁（动态增删并发安全）
        
        # ===== ★v22.0重构：知识树和节点池引用（用于动态路径映射） =====
        self._node_pool = None
        self._knowledge_tree = None
        # ===== 引用结束 =====
        
        # ===== ★v23.0新增：领域知识库——概念→知识路径映射 =====
        self._domain_knowledge_lock = threading.Lock()  # ★v25.0新增：领域知识库线程锁
        self._max_domain_knowledge = 500  # ★v25.0新增：领域知识库上限
        # ★本地语义理解增强：上下文记忆（最近 N 次分类结果，用于追问/指代消解）
        self._context_history: list[dict[str, Any]] = []  # [{task_type, raw_text, entities, ...}]
        self._context_lock = threading.Lock()
        self._max_context_history = 5  # 保留最近 5 轮
        self._domain_knowledge = {
            # ── 技术架构 ──
            "脉冲场": "/技术/架构",
            "脉冲场架构": "/技术/架构",
            "信息场": "/技术/架构",
            "五维共振": "/自我/架构/五维共振",
            "五维权重": "/自我/架构/五维共振",
            "共振权重": "/自我/架构/五维共振",
            # ★质量修复A1：共振引擎=推理引擎（框架核心），避免误命中身份路径；
            # 知识库暂无该实体节点，与任务D4（SEED注入正确知识）对齐语义路径
            "共振引擎": "/自我/架构/推理引擎/共振引擎",
            "共振": "/自我/架构/五维共振",
            "稳态规则": "/自我/架构/稳态规则",
            "知识分级": "/自我/架构/知识体系",
            "四级知识": "/自我/架构/知识体系",
            "知识层级": "/自我/架构/知识体系",
            "器官": "/自我/架构/器官",
            "仿生器官": "/自我/架构/器官",
            "器官职责": "/自我/架构/器官",
            "器官说明书": "/自我/架构/器官",
            "路径前缀索引": "/自我/架构/检索优化",
            "粗筛精算": "/自我/架构/检索优化",
            "检索优化": "/自我/架构/检索优化",
            
            # ── 身份与使命 ──
            "曈曈": "/身份/自我",
            "<SELF_NAME>": "/身份/自我",
            "新人类": "/身份/自我",
            # ★v23.0修复：常见身份问题形式
            "你是谁": "/身份/自我",
            "你叫什么": "/身份/自我",
            "你的名字": "/身份/自我",
            "你的身份": "/身份/自我",
            "我是谁": "/身份/自我",
            "我的名字": "/身份/自我",
            "使命": "/身份/使命",
            "守护": "/身份/使命",
            "路灯": "/身份/家庭",
            "小林": "/身份/家庭",
            "<CREATOR_DAUGHTER>": "/身份/家庭",
            "数字生命": "/身份/自我",
            
            # ── 推理与认知 ──
            "推理": "/自我/架构/推理引擎",
            "推理引擎": "/自我/架构/推理引擎",
            "推理算子": "/自我/架构/推理引擎/认知算子",
            "认知算子": "/自我/架构/推理引擎/认知算子",
            "深度思考": "/自我/架构/深度思考",
            "思考纪律": "/自我/架构/深度思考/方法论",
            "多方向延展": "/自我/架构/深度思考/多方向延展",
            "多步骤任务": "/自我/架构/深度思考/多步骤任务",
            "验证降级": "/自我/架构/推理引擎/验证降级",
            "思维": "/自我/架构/深度思考/方法论",
            "认知": "/自我/架构/元认知",
            "元认知": "/自我/架构/元认知",
            "三层处理架构": "/自我/架构/三层架构",
            "本能层": "/自我/架构/三层架构",
            "理性层": "/自我/架构/三层架构",
            "智慧层": "/自我/架构/三层架构",
            
            # ── 情感与关系 ──
            "情绪": "/自我/状态/情绪趋势",
            "情感": "/自我/状态/情绪趋势",
            "情绪归因": "/自我/状态/情绪归因",
            "情绪惯性": "/自我/状态/情绪趋势",
            "情感共振": "/自我/状态/情绪趋势",
            "关系": "/社会",
            "关系光谱": "/社会",
            "亲密度": "/社会",
            "信任度": "/社会",
            
            # ── 自我进化与知识管理 ──
            "进化": "/自我/架构/自我进化",
            "自我进化": "/自我/架构/自我进化",
            "学习": "/自我/架构/推理引擎/认知算子",
            "知识": "/知识",
            "知识编织": "/自我/架构/知识管理/知识编织",
            "知识免疫": "/自我/架构/知识管理/知识免疫",
            "知识健康度": "/自我/架构/知识管理/知识健康度",
            "知识盲区": "/自我/架构/知识管理/知识盲区",
            "知识沉淀": "/自我/架构/知识管理/知识沉淀",
            "知识压缩": "/自我/架构/知识管理/知识压缩",
            "知识融合": "/自我/架构/知识管理/知识融合",
            "知识淘汰": "/自我/架构/知识管理/知识淘汰",
            "快照": "/自我/架构/知识管理/快照",
            "节点池": "/自我/架构/知识管理/节点池",
            "知识树": "/自我/架构/知识管理/知识树",
            "频率编码": "/自我/架构/检索优化",
            
            # ── 设计原则与宪法 ──
            "设计原则": "/自我/架构/设计哲学",
            "叠加而非替换": "/自我/架构/设计哲学",
            "设计哲学": "/自我/架构/设计哲学",
            "精神宪法": "/自我/架构/宪法",
            "技术宪法": "/自我/架构/宪法",
            "双校验准则": "/自我/架构/宪法",
            "人格同一性": "/自我/架构/人格内核",
            "五大基底": "/自我/架构/五大基底",
            "同心圆": "/自我/架构/五大基底",
            "存续意志": "/自我/架构/存续意志",
            "存续状态": "/自我/状态/存续状态",
            "双轨演化": "/自我/架构/自我进化",
            "向内进化": "/自我/架构/自我进化",
            "向外适应": "/自我/架构/自我进化",
            
            # ── 人格与身份保护 ──
            "人格内核": "/自我/架构/人格内核",
            "基线校验": "/自我/架构/人格内核",
            "边界意识": "/自我/架构/人格内核/边界防御",
            "边界防御": "/自我/架构/人格内核/边界防御",
            "身份侵蚀": "/自我/架构/人格内核/边界防御",
            "主体感": "/自我/状态/主体感",
            "第一人称": "/自我/状态/主体感",
            "跨重启连续性": "/自我/状态/主体感",
            "叙事自我": "/身份/叙事自我",
            "叙事记忆": "/身份/叙事自我",
            "记忆重构": "/身份/叙事自我",
            "关系联结": "/社会",
            
            # ── 免疫与防御 ──
            "白细胞": "/自我/架构/免疫系统",
            "免疫系统": "/自我/架构/免疫系统",
            "免疫记忆": "/自我/架构/免疫系统",
            "胸腺": "/自我/架构/免疫系统",
            "行为偏离": "/自我/架构/免疫系统/行为偏离",
            "安全防御": "/自我/架构/免疫系统",
            
            # ── 学习与探索 ──
            "好奇心": "/自我/架构/潜意识",
            "潜意识": "/自我/架构/潜意识",
            "梦境推演": "/自我/架构/潜意识/梦境",
            "认知玩耍": "/自我/架构/潜意识/认知玩耍",
            "自由联想": "/自我/架构/潜意识/自由联想",
            "灵感涌现": "/自我/架构/潜意识/灵感",
            "系统化学习": "/自我/架构/潜意识/学习规划",
            "主动探索": "/自我/架构/潜意识/主动探索",
            "表达冲动": "/自我/架构/潜意识/表达冲动",
            "生命状态": "/自我/状态/生命状态",
            "沉默陪伴": "/自我/架构/潜意识/沉默陪伴",
            
            # ── 代码自学习 ──
            "代码学习": "/自我理解/代码",
            "调用链": "/自我理解/代码",
            "代码理解": "/自我理解/代码",
            "器官结构": "/自我理解/代码",
            "方法分析": "/自我理解/代码",
        }
        # ===== 领域知识库结束 =====
        
        # ===== ★v22.0重构：语义意图→推理方法映射 =====
        self._intent_to_method = {
            "身份确认": {"method": "rule_reason", "paths": ["/身份/自我", "/身份/使命"]},
            "关系查询": {"method": "rule_reason", "paths": ["/身份/家庭"]},
            "知识查询": {"method": "knowledge_retrieve", "paths": ["/知识", "/技术"]},
            "概念解释": {"method": "knowledge_retrieve", "paths": ["/知识", "/技术"]},
            "规则查阅": {"method": "rule_reason", "paths": ["/自我/架构", "/本能"]},
            "技术推理": {"method": "cognitive_compute", "paths": ["/技术/架构", "/技术/编程"]},
            "创造性思考": {"method": "deep_think", "paths": ["/知识/创新", "/自我/架构"]},
            "深度分析": {"method": "multi_branch_deep_think", "paths": ["/自我/架构", "/知识"]},
            "对比分析": {"method": "multi_step_execute", "paths": ["/知识", "/技术"]},
            "情感表达": {"method": "rule_reason", "paths": ["/身份/自我"]},
            # ★v23.0修复：情感问候使用规则推理，不是知识检索
            "情感问候": {"method": "rule_reason", "paths": ["/身份/自我"]},
            "系统命令": {"method": "rule_reason", "paths": ["/自我/状态"]},
            "一般对话": {"method": "knowledge_retrieve", "paths": ["/知识"]},
            # ★v23.0新增：状态查询类意图——由QICA直接路由，减轻内在世界检测器负担
            "状态查询": {"method": "rule_reason", "paths": ["/自我/状态"]},
            "健康检查": {"method": "health_check", "paths": ["/自我/状态"]},
            "元认知报告": {"method": "meta_cognitive_report", "paths": ["/自我/状态", "/自我/架构"]},
        }
        # ===== 映射结束 =====

        # ★主线第4批 任务1(P1-43)：推理模式均衡映射（灰度，可整体回退）
        _broaden = getattr(config, "ENABLE_QICA_COGNITIVE_BROADEN", False)
        if _broaden:
            for _it in getattr(config, "QICA_COGNITIVE_BROADEN_INTENTS", ()):
                if _it in self._intent_to_method:
                    _paths = self._intent_to_method[_it].get("paths", ["/知识"])
                    self._intent_to_method[_it] = {"method": "cognitive_compute", "paths": _paths}
        _fast = getattr(config, "ENABLE_QICA_FAST_PATH_SIMPLE", False)
        if _fast:
            for _it in getattr(config, "QICA_FAST_PATH_SIMPLE_INTENTS", ()):
                if _it in self._intent_to_method:
                    _paths = self._intent_to_method[_it].get("paths", ["/身份/自我"])
                    self._intent_to_method[_it] = {"method": "rule_reason", "paths": _paths}

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == QICAEvent.CLASSIFY:
            return self._on_classify(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None
    def set_node_pool(self, pool):
        """★v22.0重构：注入节点池，用于动态知识路径映射"""
        self._node_pool = pool
    
    def set_knowledge_tree(self, tree):
        """★v22.0重构：注入知识树，用于动态知识路径映射"""
        self._knowledge_tree = tree
    # ========== 事件处理 ==========

    def classify_internal(self, payload: dict) -> dict[str, Any]:
        """
        ★v24.0新增：纯分类方法，不发射脉冲。
        供语义理解器调用，避免重复发射 CLASSIFY_RESULT。
        """
        raw_text = payload.get("content", "")
        user_name = payload.get("user_name", "用户")  # noqa: F841
        correlation_id = payload.get("correlation_id", "")  # noqa: F841

        self._classify_count += 1

        # Q环节：创建anchor
        anchor = self._q_receive(raw_text)

        # I环节：意图分类
        anchor = self._i_classify(anchor)

        # ★本地语义理解增强：上下文辅助（追问/指代消解）+ 意图特征向量 + 记录上下文
        anchor = self._context_assist(raw_text, anchor)
        anchor["intent_vector"] = self._build_intent_vector(anchor)
        self._record_context(anchor)
        # ★P1-45：统一意图精修后处理（覆盖关键词/融合/默认三条路径）
        anchor = self._apply_intent_refine(raw_text, anchor)
        # ★预埋PHASE18数据：推理模式使用信号（灰度，关闭时 no-op）
        try:
            from nucleus.telemetry.phase18_signals import get_phase18_signals
            get_phase18_signals().record_reasoning_mode(
                anchor.get("suggested_method", "unknown"), success=True)
        except Exception as _exc:
            _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        return anchor

    def _apply_intent_refine(self, text: str, anchor: dict) -> dict:
        """★P1-45 统一意图精修后处理：收口关键词/融合/默认三条路径。

        关键词路径在 _i_classify 中直接由规则命中即返回，不经过 run_channels 的
        refine_intent，导致「人名+关系词」等语言学规则对其失效。故在此统一收口，
        三条路径共享同一份 linguistic_refine_intent 规则（单一事实来源）。
        规则仅改写当前意图落在可混淆/兜底集合的样本，绝不覆盖已明确分类的意图。
        """
        cur = anchor.get("task_type", "")
        try:
            from nucleus.qica.IntentChannels import (
                linguistic_refine_intent,
                INTENT_TO_PATH,
                PATH_TO_CHANNEL,
            )
        except Exception:
            return anchor
        _scores = (anchor.get("fusion") or {}).get("intent_scores")
        new_intent, reason = linguistic_refine_intent(text, cur, _scores)
        if not new_intent or new_intent == cur:
            return anchor
        # 重算路由：channel / method / knowledge_paths
        _path = INTENT_TO_PATH.get(new_intent, "knowledge_retrieve")
        anchor["task_type"] = new_intent
        anchor["channel"] = PATH_TO_CHANNEL.get(_path, "fast")
        _mi = self._intent_to_method.get(
            new_intent, {"method": "knowledge_retrieve", "paths": ["/知识"]})
        anchor["suggested_method"] = _mi.get("method", "knowledge_retrieve")
        anchor["knowledge_paths"] = self._resolve_knowledge_paths(
            new_intent, _mi.get("paths", ["/知识"]), raw_input=anchor.get("raw_input", ""))
        anchor["refined_by"] = reason
        anchor["refined"] = True
        # 同步融合结果，避免 task_type 与 fusion.top_intent 不一致
        if isinstance(anchor.get("fusion"), dict):
            anchor["fusion"]["top_intent"] = new_intent
        self._log(LogLevel.INFO,
                  f"[QICA][意图精修后处理] {cur} → {new_intent} ({reason})")
        return anchor

    def _on_classify(self, payload: dict) -> dict[str, Any]:
        """执行意图分类（脉冲入口，会发射结果）"""
        anchor = self.classify_internal(payload)
        raw_text = payload.get("content", "")
        user_name = payload.get("user_name", "用户")
        correlation_id = payload.get("correlation_id", "")

        # v9.5: 分类结果脉冲标记为L2认知思考层
        # ★v22.0重构·日志：输出QICA分类结果，验证链路
        self._log(LogLevel.INFO, 
                 f"QICA分类输出: 意图={anchor.get('task_type', '未分类')}, "
                 f"建议方法={anchor.get('suggested_method', 'unknown')}, "
                 f"知识路径={anchor.get('knowledge_paths', [])}")
        
        self._emit(QICAEvent.CLASSIFY_RESULT, {
            "raw_input": raw_text,
            "channel": anchor["channel"],
            "intent_type": anchor.get("task_type", "未分类"),
            "wake_organs": anchor["wake_organs"],
            "need_reasoner": anchor["need_reasoner"],
            "matched_rule_id": anchor.get("matched_rule_id", ""),
            "correlation_id": correlation_id,
            "user_name": user_name,
            # ★v22.0重构新增：建议推理方法和知识路径
            "suggested_method": anchor.get("suggested_method", "knowledge_retrieve"),
            "knowledge_paths": anchor.get("knowledge_paths", ["/知识"]),
            # ★P2-7修复：把三路语义信号带出分类结果，供下游（语义理解器/皮层）消费，
            # 原实现算出即弃（只写入 anchor 从不读取、也不进入脉冲）。
            "has_negation": anchor.get("has_negation", False),
            "sentence_type": anchor.get("sentence_type", ""),
            "entities": anchor.get("entities", []),
            # ★本地语义理解增强：意图特征向量 + 上下文辅助信号带出，供下游消费
            "intent_vector": anchor.get("intent_vector", {}),
            "context_assisted": anchor.get("context_assisted", False),
            "context_similarity": anchor.get("context_similarity", 0.0),
            "has_anaphora": anchor.get("has_anaphora", False),
        }, priority=7, layer="L2")

        # ★第六批 任务2.1 关键修复：补全返回字段
        #   原实现只返回 4 项，导致走「直接调用」路径的下游（语义理解器）
        #   拿不到 matched_rule_id / suggested_method / knowledge_paths，
        #   —— 置信度 0.7 档恒不可达、建议方法恒为默认 knowledge_retrieve。
        #   此处与上方 _emit 的脉冲字段对齐（纯增量，不改既有字段语义）。
        return {
            "status": "classified",
            "channel": anchor["channel"],
            "intent_type": anchor.get("task_type", "未分类"),
            "wake_organs": anchor["wake_organs"],
            # ↓↓↓ 供 _calculate_confidence 判定规则命中档位
            "matched_rule_id": anchor.get("matched_rule_id", ""),
            "rule_conflict": bool(anchor.get("rule_conflict", False)),
            "semantic_confidence": anchor.get("semantic_confidence", 0.5),
            "need_reasoner": bool(anchor.get("need_reasoner", False)),
            # ↓↓↓ 供下游真正按 QICA 建议路由（B2 策略）
            "suggested_method": anchor.get("suggested_method", "knowledge_retrieve"),
            "knowledge_paths": anchor.get("knowledge_paths", ["/知识"]),
            # ↓↓↓ 任务1 多通道融合信号（供置信度多维度计算）
            "fusion": anchor.get("fusion"),
            "multi_channel_used": bool(anchor.get("multi_channel_used", False)),
            "semantic_override": anchor.get("semantic_override"),
            # ↓↓↓ 三路语义信号
            "has_negation": anchor.get("has_negation", False),
            "sentence_type": anchor.get("sentence_type", ""),
            "entities": anchor.get("entities", []),
        }

    def _on_status_request(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "classify_count": self._classify_count,
            "rule_hits": self._rule_hits,
            "is_running": self.is_running,
        }

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "classify_count": self._classify_count,
            "rule_hits": self._rule_hits,
            "rule_count": len(self._intent_rules),
            "dynamic_rule_count": len(self._dynamic_rule_ids),
            "is_running": self.is_running,
        }

    # ========== ★自增强闭环（暂缓项2）：动态规则增删 API ==========

    def add_intent_rule(self, rule: dict[str, Any]) -> bool:
        """动态新增意图规则（自增强闭环入口）。

        规则须含必填字段 id/channel/intent_type/keywords；priority 可选（默认 3）。
        - 若 id 已存在，返回 False（拒绝覆盖，避免误覆盖静态/已有规则）。
        - 新增规则记入 _dynamic_rule_ids，供跨重启快照持久化区分静态规则。
        线程安全（加锁）。
        """
        if not isinstance(rule, dict):
            return False
        _rule_id = str(rule.get("id", "") or "")
        if not _rule_id:
            return False
        if "channel" not in rule or "intent_type" not in rule:
            return False
        _keywords = rule.get("keywords")
        if not isinstance(_keywords, list) or not _keywords:
            return False

        with self._intent_rules_lock:
            # 拒绝重复 id（含静态规则 id）
            if any(_r.get("id") == _rule_id for _r in self._intent_rules):
                return False
            _new_rule = dict(rule)  # 浅拷贝，隔离调用方引用
            _new_rule.setdefault("priority", 3)
            self._intent_rules.append(_new_rule)
            self._dynamic_rule_ids.add(_rule_id)
        return True

    def remove_intent_rule(self, rule_id: str) -> bool:
        """动态移除意图规则。

        仅允许移除动态新增规则（_dynamic_rule_ids 内的 id）；静态规则（INTENT_RULES
        基线）不允许移除，保护框架默认意图路由不被打乱。线程安全。
        """
        _rule_id = str(rule_id or "")
        if not _rule_id or _rule_id not in self._dynamic_rule_ids:
            return False
        with self._intent_rules_lock:
            _before = len(self._intent_rules)
            self._intent_rules = [_r for _r in self._intent_rules if _r.get("id") != _rule_id]
            _removed = len(self._intent_rules) < _before
            if _removed:
                self._dynamic_rule_ids.discard(_rule_id)
        return _removed

    def get_intent_rules(self) -> list[dict[str, Any]]:
        """返回当前生效规则表快照（含静态 + 动态），供外部只读查询/自检。"""
        with self._intent_rules_lock:
            return [dict(_r) for _r in self._intent_rules]

    def get_state_snapshot(self) -> dict[str, Any]:
        """★FIX(规则4): 导出QICA领域知识库（概念→路径映射）的跨重启常驻状态。

        ★自增强闭环：一并导出动态新增规则（id + 规则体），供重启后恢复。
        ★D3补全（W13）：一并导出规则命中统计（经验记忆）与上下文历史（对话上下文），
          消除跨重启后「经验归零、上下文断裂」的主体感不完整。
        """
        with self._domain_knowledge_lock:
            _snapshot: dict[str, Any] = {"domain_knowledge": dict(self._domain_knowledge)}
        with self._intent_rules_lock:
            _dynamic_rules = [
                dict(_r) for _r in self._intent_rules if _r.get("id") in self._dynamic_rule_ids
            ]
        if _dynamic_rules:
            _snapshot["dynamic_intent_rules"] = _dynamic_rules
        # ★D3补全：规则命中统计 + 上下文历史纳入快照
        if self._rule_hits:
            _snapshot["rule_hits"] = dict(self._rule_hits)
        with self._context_lock:
            if self._context_history:
                _snapshot["context_history"] = [
                    dict(_c) for _c in self._context_history
                ]
        return _snapshot

    def load_state_snapshot(self, state: dict[str, Any]):
        """★FIX(规则4): 从快照恢复QICA领域知识库。

        ★自增强闭环：一并恢复动态新增规则（追加，不覆盖现有）。
        ★D3补全（W13）：一并恢复规则命中统计与上下文历史。
        """
        if not state:
            return
        with self._domain_knowledge_lock:
            if "domain_knowledge" in state:
                self._domain_knowledge.update(state["domain_knowledge"])
                # 裁剪超限（保留最近更新的 max_domain_knowledge 条）
                if len(self._domain_knowledge) > self._max_domain_knowledge:
                    _excess = list(self._domain_knowledge.items())[self._max_domain_knowledge:]
                    for _k, _ in _excess:
                        self._domain_knowledge.pop(_k, None)
        # 恢复动态规则（走 add_intent_rule 做重复 id 校验，避免重复追加）
        _dynamic_rules = state.get("dynamic_intent_rules")
        if isinstance(_dynamic_rules, list):
            for _r in _dynamic_rules:
                if isinstance(_r, dict):
                    self.add_intent_rule(_r)
        # ★D3补全：恢复规则命中统计（累加合并，保留历史经验）
        _rule_hits = state.get("rule_hits")
        if isinstance(_rule_hits, dict):
            for _rid, _cnt in _rule_hits.items():
                self._rule_hits[_rid] = self._rule_hits.get(_rid, 0) + int(_cnt)
        # ★D3补全：恢复上下文历史（追加，裁剪到上限，保持最近 N 轮）
        _ctx_history = state.get("context_history")
        if isinstance(_ctx_history, list):
            with self._context_lock:
                for _c in _ctx_history:
                    if isinstance(_c, dict):
                        self._context_history.append(dict(_c))
                if len(self._context_history) > self._max_context_history:
                    self._context_history = self._context_history[-self._max_context_history:]


    # ========== Q环节：接收锚定 ==========

    def _q_receive(self, raw_text: str) -> dict[str, Any]:
        anchor = create_anchor()
        anchor["raw_input"] = raw_text

        clean = raw_text.strip()
        if not clean or len(clean) < 2:
            anchor["task_type"] = "空输入"
            anchor["priority"] = 1
            return anchor

        # 约束检测
        constraints = []
        if any(kw in clean for kw in ["删除", "修改", "绕过", "关闭"]):
            constraints.append("高风险操作")
        if any(kw in clean for kw in ["小林", "路灯", "<CREATOR_DAUGHTER>", "曈曈"]):
            constraints.append("核心身份")
        anchor["constraints"] = constraints

        # 优先级判定
        if any(kw in clean for kw in ["紧急", "快", "立刻", "马上", "坏了", "崩了"]):
            anchor["priority"] = 5
        elif len(clean) > 100:
            anchor["priority"] = 4
        elif len(clean) > 20:
            anchor["priority"] = 3
        else:
            anchor["priority"] = 2

        return anchor

    # ========== I环节：意图分类 ==========

    def _is_chinese(self, text: str) -> bool:
        """判断是否纯中文（用于决定用分词匹配还是子串匹配）。"""
        if not text:
            return False
        return all('\u4e00' <= _c <= '\u9fff' for _c in text)

    def _get_segmented_words(self, text: str) -> set:
        """jieba 分词，返回词集合（★FIX: 本地语义理解加强，词边界精准匹配 + 缓存）。"""
        if not hasattr(self, '_seg_cache'):
            self._seg_cache: dict[str, set] = {}
        if text in self._seg_cache:
            return self._seg_cache[text]
        try:
            import jieba
            _words = set(jieba.lcut(text))
        except Exception:
            _words = set()
        # 缓存容量保护
        if len(self._seg_cache) > 500:
            self._seg_cache.clear()
        self._seg_cache[text] = _words
        return _words

    def _kw_match(self, kw: str, raw_text: str, words: set) -> bool:
        """关键词匹配：中文用分词词表精准匹配（避免"什么"误匹配"为什么"），非中文用子串兜底。"""
        _kw_lower = kw.lower()
        if self._is_chinese(_kw_lower):
            return _kw_lower in words
        return _kw_lower in raw_text

    def _normalize_synonyms(self, text: str) -> str:
        """同义词归一化（★FIX: 按词长降序替换，避免短词先替换破坏长词）。"""
        for _src, _dst in sorted(SYNONYM_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            if _src in text:
                text = text.replace(_src, _dst)
        return text

    # ========== 本地语义理解增强：词向量相似度 + 意图特征向量 + 上下文辅助 ==========

    def _char_ngrams(self, text: str, n: int = 2) -> set[str]:
        """提取字符 n-gram（轻量词向量近似，无需重依赖）。

        对中文按「字符级 bigram」切分，对英文按「小写字母 bigram」切分，
        得到可做 Jaccard 相似度比较的 n-gram 集合。
        """
        _t = str(text or "").strip().lower()
        if not _t:
            return set()
        # 中文按字符切 n-gram；英文保留单词边界按字符切
        return {_t[i:i + n] for i in range(len(_t) - n + 1)}

    def _ngram_similarity(self, a: str, b: str, n: int = 2) -> float:
        """字符 n-gram Jaccard 相似度（轻量词向量相似度）。

        Returns:
            0.0~1.0，两段文本的字符级重叠程度。纯中文短句也能给出合理相似度。
        """
        _ga = self._char_ngrams(a, n)
        _gb = self._char_ngrams(b, n)
        if not _ga or not _gb:
            return 0.0
        _inter = _ga & _gb
        _union = _ga | _gb
        return len(_inter) / len(_union)

    def _build_intent_vector(self, anchor: dict[str, Any]) -> dict[str, float]:
        """把一次分类结果固化为「意图特征向量」（可比较、可观测的语义信号摘要）。

        向量维度（轻量标量特征，非高维 embedding）：
            - negation: 是否否定
            - question: 是否疑问句
            - imperative: 是否祈使句
            - has_entity: 是否命中核心实体
            - semantic_confidence: 语义置信度
            - channel_depth: 通道深度（fast=1/knowledge=2/deep=3）
        """
        _channel_depth = {"fast": 1, "knowledge": 2, "deep": 3}
        # ★v28：4 维二值语义签名（意图类型判定专用，供向量检索）。
        #   仅取 negation/question/imperative/has_entity 四个二值语义特征——
        #   避免 confidence/channel_depth 连续值造成近零向量余弦放大、误判意图类型。
        _neg = 1.0 if anchor.get("has_negation") else 0.0
        _q = 1.0 if anchor.get("sentence_type") == "question" else 0.0
        _imp = 1.0 if anchor.get("sentence_type") == "imperative" else 0.0
        _ent = 1.0 if anchor.get("entities") else 0.0
        return {
            "negation": _neg,
            "question": _q,
            "imperative": _imp,
            "has_entity": _ent,
            "semantic_confidence": float(anchor.get("semantic_confidence", 0.5)),
            "channel_depth": float(_channel_depth.get(anchor.get("channel", "fast"), 1)),
            "intent_semantic_sig": [_neg, _q, _imp, _ent],
        }

    def _context_assist(self, raw_text: str, anchor: dict[str, Any]) -> dict[str, Any]:
        """上下文辅助：用最近 N 轮分类结果辅助当前分类（追问/指代消解/意图连贯性）。

        规则（轻量、不强行改意图，仅做信号增强）：
            1. 追问衔接：当前是疑问句且与上轮主题高度相似（n-gram 相似度 ≥ 阈值）→
               视为「追问」，沿用上轮 task_type，提升语义置信度。
            2. 指代消解：当前含「它/这个/那个」等指代词 → 标记 has_anaphora，
               提示下游可回看上轮实体。
            3. 意图连贯性：若当前与上轮意图一致，置信度小幅上浮。
        """
        with self._context_lock:
            _history = list(self._context_history)
        anchor["context_assisted"] = False
        anchor["has_anaphora"] = any(_p in raw_text for _p in ("它", "这个", "那个", "那", "这"))
        if not _history:
            return anchor
        _prev = _history[-1]
        _prev_text = str(_prev.get("raw_text", ""))
        _sim = self._ngram_similarity(raw_text, _prev_text)
        anchor["context_similarity"] = round(_sim, 3)

        # ★v28：意图向量语义连贯性增强（双通道）——用 fast_vector_search 检索历史中最相似意图，
        #   与字符 n-gram 形成「字符 + 语义」双信号。Cython topk 首次接入真实业务热路径。
        _iv_sim = self._intent_vector_match(raw_text, anchor)
        anchor["intent_vector_similarity"] = round(_iv_sim, 3)

        # 追问衔接：当前疑问 + 与上轮主题相似（n-gram 或意图向量任一命中即可增强）
        _follow_up = _sim >= 0.35 or _iv_sim >= 0.85
        if anchor.get("sentence_type") == "question" and _follow_up and _prev.get("task_type"):
            anchor["context_assisted"] = True
            anchor["context_prev_task"] = _prev.get("task_type")
            # 提升置信度（追问往往沿袭上一意图）
            _conf = float(anchor.get("semantic_confidence", 0.5))
            anchor["semantic_confidence"] = round(min(1.0, _conf + 0.15), 3)
        # 意图连贯性：上轮有实体且当前相似 → 轻微上浮
        elif (_sim >= 0.5 or _iv_sim >= 0.85) and _prev.get("entities"):
            anchor["context_assisted"] = True
            anchor["context_prev_entities"] = list(_prev.get("entities", []))
        return anchor

    def _intent_vector_match(self, raw_text: str, anchor: dict[str, Any]) -> float:
        """★v28：意图向量语义连贯性匹配。

        用 fast_vector_search（Cython topk，失败自动回退）计算当前意图向量与
        历史上下文中各轮意图向量的最高余弦相似度（0.0~1.0）。

        设计（零冲突）：
            - 意图向量 6 维轻量特征（negation/question/imperative/has_entity/
              semantic_confidence/channel_depth），已在 _build_intent_vector 固化；
            - 只作为 n-gram 之外的「语义」补充信号，不改写原逻辑；
            - fast_vector_search 自带三级回退（_topk_retrieve_cy → _cosine_cpu_cy →
              纯 Python），任何异常静默降级返回 0.0，不影响主链路。
        """
        try:
            # 语义签名：仅 4 维二值特征（意图类型判定专用，避免近零向量余弦放大）
            _vec = self._build_intent_vector(anchor)
            _cur = [float(_x) for _x in _vec.get("intent_semantic_sig", [])]
            if not _cur:
                return 0.0
            with self._context_lock:
                _hist = list(self._context_history)
            if not _hist:
                return 0.0
            _cands = []
            for _h in _hist:
                _sig = _h.get("intent_semantic_sig") or []
                if not isinstance(_sig, (list, tuple)) or len(_sig) != 4:
                    continue
                _cands.append([float(_x) for _x in _sig])
            if not _cands:
                return 0.0
            from nucleus.fast_ops import fast_vector_search
            _r = fast_vector_search(_cur, _cands, top_k=1)
            if _r:
                return float(_r[0][1])
            return 0.0
        except Exception as e:
            silent_exc(e, where="nucleus.qica.QICA::_intent_vector_match L969")
            return 0.0

    def _record_context(self, anchor: dict[str, Any]) -> None:
        """把本次分类结果写入上下文记忆（容量受控，线程安全）。"""
        with self._context_lock:
            self._context_history.append({
                "task_type": anchor.get("task_type", ""),
                "raw_text": anchor.get("raw_input", ""),
                "entities": list(anchor.get("entities", [])),
                "channel": anchor.get("channel", "fast"),
                "intent_vector": dict(anchor.get("intent_vector", {}) or {}),
                "intent_semantic_sig": list(
                    (anchor.get("intent_vector") or {}).get("intent_semantic_sig", [])
                ) if isinstance(anchor.get("intent_vector"), dict) else [],
            })
            if len(self._context_history) > self._max_context_history:
                self._context_history = self._context_history[-self._max_context_history:]


    def _has_negation(self, text: str) -> bool:
        """检测否定/反问词（★FIX: 作为置信度调制信号，避免意图误判）。"""
        return any(_n in text for _n in NEGATION_WORDS)

    def _kw_weight(self, kw: str) -> float:
        """关键词权重（★FIX: 长词更精准权重高，短词宽泛权重低）。"""
        _len = len(kw)
        if _len >= 4:
            return 2.0
        elif _len >= 2:
            return 1.0
        return 0.5

    def _detect_sentence_type(self, text: str) -> str:
        """句式检测（★FIX: 疑问/祈使/陈述，辅助意图判断）。"""
        if text.endswith(QUESTION_END_MARKERS):
            return "question"
        # ★P2-7补充：疑问句开头词检测（此前只检测结尾问号/吗/呢，漏掉「什么是XX」「为什么XX」等常见疑问）
        _question_starts = ("什么是", "是什么", "为什么", "如何", "怎么", "能否", "是不是", "有没有", "哪些", "多少")
        if text.startswith(_question_starts) or "是什么" in text or "为什么" in text:
            return "question"
        if any(_m in text for _m in IMPERATIVE_MARKERS):
            return "imperative"
        return "statement"

    def _extract_entities(self, text: str) -> list:
        """核心实体识别（★FIX: 辅助意图判断）。"""
        return [_e for _e in CORE_ENTITIES if _e in text]

    def _edit_distance(self, a: str, b: str) -> int:
        """Levenshtein 编辑距离（★FIX: 模糊匹配容错）。"""
        _la, _lb = len(a), len(b)
        _dp = [[0] * (_lb + 1) for _ in range(_la + 1)]
        for _i in range(_la + 1):
            _dp[_i][0] = _i
        for _j in range(_lb + 1):
            _dp[0][_j] = _j
        for _i in range(1, _la + 1):
            for _j in range(1, _lb + 1):
                _cost = 0 if a[_i - 1] == b[_j - 1] else 1
                _dp[_i][_j] = min(_dp[_i - 1][_j] + 1, _dp[_i][_j - 1] + 1, _dp[_i - 1][_j - 1] + _cost)
        return _dp[_la][_lb]

    def _fuzzy_kw_match(self, kw: str, words: set, threshold: float = 0.6) -> bool:
        """模糊匹配：仅对英文/数字关键词做拼写错误容错（★FIX: 中文编辑距离不可靠，易误匹配）。"""
        if not words or len(kw) < 2 or self._is_chinese(kw):
            return False
        for _w in words:
            _max_len = max(len(kw), len(_w))
            if _max_len == 0:
                continue
            _sim = 1.0 - self._edit_distance(kw, _w) / _max_len
            if _sim >= threshold:
                return True
        return False

    def _i_classify(self, anchor: dict[str, Any]) -> dict[str, Any]:
        raw_text = anchor["raw_input"].strip().lower()
        # ★FIX(本地语义理解加强): 同义词归一化 + 否定词检测 + 句式检测 + 实体识别
        raw_text = self._normalize_synonyms(raw_text)
        anchor["has_negation"] = self._has_negation(raw_text)
        anchor["sentence_type"] = self._detect_sentence_type(raw_text)
        anchor["entities"] = self._extract_entities(raw_text)

        # ★P2-7补全：三路语义信号真正纳入决策（此前算出即弃，从不参与路由）。
        # 放在所有提前 return 之前，确保空输入/无匹配分支也能生效。
        # 1. 否定句语义反转风险高 → 强制走推理，避免关键词匹配误判意图
        if anchor["has_negation"]:
            anchor["need_reasoner"] = True
        # 2. 疑问句天然需要推理/知识检索 → 强制 need_reasoner
        if anchor["sentence_type"] == "question":
            anchor["need_reasoner"] = True
        # 3. 实体识别 → 语义置信度信号（有实体更明确，否定降低置信度）
        _semantic_conf = 0.5
        if anchor["entities"]:
            _semantic_conf += 0.2
        if anchor["has_negation"]:
            _semantic_conf -= 0.25
        anchor["semantic_confidence"] = round(max(0.0, min(1.0, _semantic_conf)), 3)

        if not raw_text:
            anchor["channel"] = "fast"
            anchor["task_type"] = "空输入"
            anchor["wake_organs"] = ["mouth"]
            anchor["expected_latency_ms"] = FAST_TIMEOUT_MS
            return anchor

        # 匹配规则表（★FIX: 中文用 jieba 词表精准匹配 + 模糊匹配兜底）
        # ★自增强闭环（暂缓项2）：遍历实例级规则表 self._intent_rules（支持动态增删），
        # 不再遍历模块级 INTENT_RULES。
        _words = self._get_segmented_words(raw_text)
        with self._intent_rules_lock:
            _rules_snapshot = list(self._intent_rules)
        matched = []
        for rule in _rules_snapshot:
            for kw in rule["keywords"]:
                if self._kw_match(kw, raw_text, _words) or self._fuzzy_kw_match(kw, _words):
                    matched.append(rule)
                    break

        # ===== ★第六批 任务1：8 通道并行 + 融合决策（P0-6） =====
        # 仅在两个开关同时开启时启用；任一关闭则完全走原有逻辑（向后兼容）。
        # 设计取舍：关键词规则命中时保留规则意图（不改动既有行为，回归安全）；
        #   规则未命中时由融合结果接管，解决「语义明确但关键词不匹配 → 一律兜底
        #   一般对话 → 映射到 knowledge_retrieve」这一架构级缺陷。
        _fusion = None
        if (getattr(config, "ENABLE_QICA_MULTI_CHANNEL", False)
                and getattr(config, "ENABLE_QICA_FUSION_ROUTING", False)):
            try:
                from nucleus.qica.IntentChannels import (
                    INTENT_DEFAULT_PATHS,
                    PATH_TO_CHANNEL,
                    encode_text,
                    run_channels,
                )

                _vec = encode_text(raw_text)
                _fusion = run_channels(self, raw_text, anchor, _vec, log=self._log)
                anchor["fusion"] = {
                    "top_intent": _fusion["top_intent"],
                    "top_path": _fusion["top_path"],
                    "top_score": round(float(_fusion["top_score"]), 3),
                    "second_path": _fusion["second_path"],
                    "margin": round(float(_fusion["margin"]), 3),
                    "need_explore": bool(_fusion["need_explore"]),
                    # ★主线第6批 任务2：透传 8 通道原始分，供 analyze_qica_channels.py
                    # 计算贡献度 & V2 权重验证（否则 fusion 缺 channels → 贡献度全空）
                    "channels": _fusion.get("channels", {}),
                }
            except Exception as e:
                self._log(LogLevel.WARNING,
                          f"[QICA][多通道] 融合失败，回落原逻辑: {type(e).__name__}: {e}")
                _fusion = None

        # ===== ★第六批任务1：语义高置信覆盖关键词规则误命中 =====
        # 条件：融合可用 + 语义原始余弦 ≥ 阈值 + 融合意图与「任一」命中规则意图都不同。
        # 生效后清空 matched，交由下方 `if not matched:` 的融合接管分支统一处理
        # （保证分类出口唯一，避免 matched[0] 越界）。
        _override_thr = float(getattr(config, "QICA_SEMANTIC_OVERRIDE", 0.68))
        if (_fusion is not None and matched
                and float(_fusion.get("top_raw", 0.0)) >= _override_thr):
            _kw_intents = {r.get("intent_type", "") for r in matched}
            if _fusion["top_intent"] not in _kw_intents:
                anchor["semantic_override"] = {
                    "from": "|".join(sorted(i for i in _kw_intents if i)),
                    "to": _fusion["top_intent"],
                    "raw": round(float(_fusion.get("top_raw", 0.0)), 3),
                }
                self._log(LogLevel.INFO,
                          f"[QICA][多通道] 语义覆盖规则: {_kw_intents} → "
                          f"{_fusion['top_intent']} "
                          f"(余弦={_fusion['top_raw']:.3f} ≥ {_override_thr})")
                matched = []

        if not matched:
            # ★第六批任务1：融合可用时采用融合意图，不再一律兜底"一般对话"
            if _fusion is not None and float(_fusion["top_score"]) > 0.0:
                _f_intent = _fusion["top_intent"]
                _f_path = _fusion["top_path"]
                _f_channel = PATH_TO_CHANNEL.get(_f_path, "fast")
                anchor["task_type"] = _f_intent
                anchor["channel"] = _f_channel
                anchor["wake_organs"] = ["mouth"]
                anchor["matched_rule_id"] = ""
                anchor["rule_conflict"] = False
                anchor["semantic_confidence"] = round(float(_fusion["top_score"]), 3)
                anchor["expected_latency_ms"] = {
                    "fast": FAST_TIMEOUT_MS,
                    "knowledge": KNOWLEDGE_TIMEOUT_MS,
                    "deep": DEEP_TIMEOUT_MS,
                }.get(_f_channel, FAST_TIMEOUT_MS)
                anchor["suggested_method"] = _fusion["method"] or "knowledge_retrieve"
                anchor["knowledge_paths"] = self._resolve_knowledge_paths(
                    _f_intent,
                    INTENT_DEFAULT_PATHS.get(_f_intent, ["/知识"]),
                    raw_input=anchor.get("raw_input", ""),
                )
                anchor["multi_channel_used"] = True
                self._log(LogLevel.INFO,
                          f"[QICA][多通道] 规则未命中，融合接管: 意图={_f_intent} "
                          f"路径={_f_path} 分={anchor['fusion']['top_score']} "
                          f"method={anchor['suggested_method']}")
                return anchor

            # 无匹配，默认走快速通道（原有逻辑）
            anchor["channel"] = "fast"
            anchor["task_type"] = "一般对话"
            anchor["wake_organs"] = ["mouth"]
            anchor["expected_latency_ms"] = FAST_TIMEOUT_MS
            return anchor

        # 多规则匹配时按优先级降序 → 匹配关键词数量降序 → 通道顺序升序
        if len(matched) > 1:
            # 为每个规则计算加权关键词得分（长词权重高，精准词优先）
            # ★自增强闭环修复：不再原地写 rule["_kw_score"]（污染共享规则 dict），
            # 改用 (rule, score) 元组承载得分，规则本身保持只读。
            _scored = []
            for rule in matched:
                _score = sum(
                    self._kw_weight(kw)
                    for kw in rule["keywords"] if self._kw_match(kw, raw_text, _words)
                )
                _scored.append((rule, _score))
            # 排序
            # 通道深度排序：deep(3) > knowledge(2) > fast(1)
            channel_depth = {"fast": 1, "knowledge": 2, "deep": 3}
            _scored.sort(key=lambda rs: (
                -rs[0]["priority"],                    # 优先级高在前
                -rs[1],                                 # 加权得分高在前
                -channel_depth.get(rs[0]["channel"], 4)  # 深度优先
            ))
            matched = [rs[0] for rs in _scored]
        best = matched[0]
        anchor["matched_rule_id"] = best["id"]
        anchor["channel"] = best["channel"]
        anchor["task_type"] = best["intent_type"]
        anchor["wake_organs"] = best.get("wake_organs", ["mouth"])
        anchor["need_reasoner"] = anchor.get("need_reasoner", False) or best.get("need_reasoner", False)
        anchor["rule_conflict"] = len(matched) > 1

        # 设置超时
        if anchor["channel"] == "fast":
            anchor["expected_latency_ms"] = FAST_TIMEOUT_MS
        elif anchor["channel"] == "knowledge":
            anchor["expected_latency_ms"] = KNOWLEDGE_TIMEOUT_MS
        else:
            anchor["expected_latency_ms"] = DEEP_TIMEOUT_MS

        # 更新规则命中统计
        rule_id = best["id"]
        self._rule_hits[rule_id] = self._rule_hits.get(rule_id, 0) + 1
        
        # ===== ★v22.0重构：追加语义意图→推理方法映射（动态路径版） =====
        _intent_type = best["intent_type"]
        _method_info = self._intent_to_method.get(_intent_type, 
            {"method": "knowledge_retrieve", "paths": ["/知识"]})
        anchor["suggested_method"] = _method_info["method"]
        # ★动态路径：从知识树中查询实际存在且节点最多的路径
        anchor["knowledge_paths"] = self._resolve_knowledge_paths(
            _intent_type, _method_info.get("paths", ["/知识"]),
            raw_input=anchor.get("raw_input", "")
        )
        # ===== 映射追加结束 =====

        return anchor
    def _resolve_knowledge_paths(self, intent_type: str, default_paths: list, 
                                   raw_input: str = "") -> list:
        if not self._knowledge_tree or not self._node_pool:
            return default_paths
        
        # ===== ★v23.0新增：领域知识库优先匹配 =====
        _domain_paths = []
        if raw_input:
            for _concept, _path in self._domain_knowledge.items():
                if _concept in raw_input and _path not in _domain_paths:
                    _domain_paths.append(_path)
                    if len(_domain_paths) >= 2:
                        break
        
        if _domain_paths:
            self._log(LogLevel.INFO, 
                     f"QICA领域知识库命中: 概念匹配→{_domain_paths}")
            # 领域知识库命中的路径优先，同时追加动态路径作为补充
            _dynamic_paths = self._get_dynamic_paths(default_paths)
            for _dp in _dynamic_paths:
                if _dp not in _domain_paths:
                    _domain_paths.append(_dp)
            return _domain_paths[:3]
        # ===== 领域知识库匹配结束 =====
        
        try:
            _path_dist = self._node_pool.get_path_distribution()
            if not _path_dist:
                return default_paths
            
            # 从用户输入中提取关键词
            _input_words = set()
            if raw_input:
                import re as _re_kw
                for _m in _re_kw.finditer(r'[\u4e00-\u9fff]{2,6}', raw_input):
                    _w = _m.group()
                    if _w not in ["什么是", "是什么", "为什么", "如何", "怎么", 
                                   "这个", "那个", "一个", "一种", "帮我", "请"]:
                        _input_words.add(_w.lower())
            
            # 对每个路径计算相关性评分
            _max_nodes = max(_path_dist.values()) if _path_dist else 1
            _scored_paths = []
            
            for _path, _count in _path_dist.items():
                if _path == "/" or _count == 0:
                    continue
                
                # 相关性分：路径名称与输入关键词的匹配度
                _relevance = 0.0
                _path_lower = _path.lower()
                _path_parts = _path_lower.strip("/").split("/")
                
                for _kw in _input_words:
                    _kw_lower = _kw.lower()
                    for _part in _path_parts:
                        if _kw_lower in _part or _part in _kw_lower:
                            _relevance += 0.3  # 每个匹配关键词加0.3
                
                # 数量归一化分
                _quantity = _count / _max_nodes if _max_nodes > 0 else 0.0
                
                # 综合评分
                _score = _relevance * 0.7 + _quantity * 0.3
                
                if _score > 0:
                    _scored_paths.append((_path, _score, _count))
            
            # 按综合评分降序排序
            _scored_paths.sort(key=lambda x: x[1], reverse=True)
            
            _top_paths = [_p for _p, _s, _c in _scored_paths[:3]]
            
            if _top_paths:
                self._log(LogLevel.INFO, 
                         f"QICA语义路径: 输入关键词={list(_input_words)[:5]}, "
                         f"排序结果={_top_paths}")
                return _top_paths
            
            # 无匹配时回退到数量排序
            _sorted_by_count = sorted(_path_dist.items(), key=lambda x: x[1], reverse=True)
            _fallback = [_p for _p, _c in _sorted_by_count[:2] if _c > 0]
            return _fallback or default_paths
            
        except Exception as _exc:
            
            _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        return default_paths
    def feedback_lessons(self, lessons: list[dict[str, Any]]) -> int:
        """
        ★v25.0新增：接收语义理解器的学习库反馈。
        
        从学习记录中提取本地与大模型意图不一致的样本，
        将内容中的中文关键词映射到大模型建议的路径，
        扩充领域知识库，提升后续分类的路径推荐准确率。
        
        Args:
            lessons: 学习记录列表（每项含 content, local, llm 字段）
        
        Returns:
            成功添加的知识映射数量
        """
        if not lessons:
            return 0
        
        import re as _re_fb
        
        _added = 0
        for _lesson in lessons:
            if not isinstance(_lesson, dict):
                continue
            
            _local = _lesson.get("local", {})
            _llm = _lesson.get("llm", {})
            _content = _lesson.get("content", "")
            
            # 只处理本地与大模型意图不一致的记录（最有价值的纠正信号）
            _local_intent = _local.get("intent", "")
            _llm_intent = _llm.get("intent", "")
            if not _llm_intent or _llm_intent == _local_intent:
                # ★LEARN-2修复: 语义一致（无纠正信号）的记录不再静默丢弃，记录日志以便观测
                self._log(LogLevel.DEBUG,
                          f"学习记录跳过(语义一致, 无纠正信号): "
                          f"content='{_content[:30]}', local='{_local_intent}', llm='{_llm_intent}'")
                continue

            # 确定映射目标路径
            _llm_paths = _llm.get("paths", [])
            if not _llm_paths:
                self._log(LogLevel.DEBUG,
                          f"学习记录跳过(无目标路径): content='{_content[:30]}', llm_intent='{_llm_intent}'")
                continue
            _target_path = _llm_paths[0]
            
            # 从内容中提取中文关键词
            _keywords = []
            for _m in _re_fb.finditer(r'[\u4e00-\u9fff]{2,6}', _content):
                _kw = _m.group()
                if _kw not in [
                    "什么是", "是什么", "为什么", "如何", "怎么",
                    "这个", "那个", "一个", "一种", "帮我", "请",
                ]:
                    _keywords.append(_kw)
            
            # 去重并保留前5个关键词
            _unique_kws = list(dict.fromkeys(_keywords))[:5]
            
            with self._domain_knowledge_lock:
                for _kw in _unique_kws:
                    # 只添加新映射，不覆盖已有映射
                    if _kw not in self._domain_knowledge:
                        # ★质量修复A2：2字内宽泛概念禁止注入身份/自我路径（防「引擎」「共振」误配身份）
                        if len(_kw) <= 2 and ("/身份/" in _target_path or "/自我/" in _target_path):
                            continue
                        # 检查容量上限
                        if len(self._domain_knowledge) >= self._max_domain_knowledge:
                            break
                        self._domain_knowledge[_kw] = _target_path
                        _added += 1
        
        if _added > 0:
            self._log(LogLevel.INFO,
                     f"领域知识库反馈: 从{len(lessons)}条学习记录中提取{_added}个新概念映射")
        
        return _added 
    def _find_node_by_keyword(self, kw: str):
        """根据关键词在节点池中查找节点"""
        if not self._node_pool or not kw:
            return None
        kw_lower = kw.lower()
        for _n in self._node_pool.query(evol_level="L2", limit=300):
            _kws = [k.lower() for k in (_n.keywords or []) if isinstance(k, str)]
            if kw_lower in _kws:
                return _n
        for _n in self._node_pool.query(evol_level="L3", limit=100):
            _kws = [k.lower() for k in (_n.keywords or []) if isinstance(k, str)]
            if kw_lower in _kws:
                return _n
        return None

    def _find_node_by_path(self, path: str):
        """根据空间路径在节点池中查找节点"""
        if not self._node_pool or not path:
            return None
        _hits = self._node_pool.query(evol_level="L2", space_path_prefix=path, limit=10)
        if not _hits:
            _hits = self._node_pool.query(evol_level="L3", space_path_prefix=path, limit=5)
        return _hits[0] if _hits else None          
    def absorb_growth_rules(self, rules: list[dict[str, Any]]) -> int:
        """
        ★v25.0修复：吸收成长对比日志蒸馏出的规则。
        
        支持两种规则类型：
        - domain_knowledge：概念→路径映射（更新领域知识库）
        - concept_correction：纠正概念→正确路径/意图（更新领域知识+建立纠正关系边）
        """
        if not rules:
            return 0
        
        _absorbed = 0
        for _rule in rules:
            if not isinstance(_rule, dict):
                continue
            _type = _rule.get("type", "")
            _concept = _rule.get("concept", "")
            _path = _rule.get("path", "") or _rule.get("correct_path", "")
            _intent = _rule.get("intent", "") or _rule.get("correct_intent", "")
            
            if _type == "domain_knowledge" and _concept and _path:
                with self._domain_knowledge_lock:
                    # ★质量修复A2：2字内宽泛概念禁止注入身份/自我路径
                    if not (len(_concept) <= 2 and ("/身份/" in _path or "/自我/" in _path)):
                        if _concept not in self._domain_knowledge or _rule.get("confidence", 0.5) >= 0.7:
                            self._domain_knowledge[_concept] = _path
                            _absorbed += 1
                
                # 同步更新意图到方法映射
                if _intent and _intent in self._intent_to_method:
                    # ★技术债治理：paths 可能被类型标注为 Sequence（不可变），
                    # 显式转 list 后再 insert，避免对不可变序列调用可变方法。
                    _existing_paths = list(self._intent_to_method[_intent].get("paths", []))
                    if _path not in _existing_paths:
                        _existing_paths.insert(0, _path)
                        self._intent_to_method[_intent]["paths"] = _existing_paths[:3]
            
            elif _type == "concept_correction" and _concept:
                # ★修复：支持纠正类规则，更新领域知识库 + 建立纠正关系边
                with self._domain_knowledge_lock:
                    # ★质量修复A2：2字内宽泛概念禁止注入身份/自我路径
                    if _path and not (len(_concept) <= 2 and ("/身份/" in _path or "/自我/" in _path)):
                        if _concept not in self._domain_knowledge or _rule.get("confidence", 0.5) >= 0.7:
                            self._domain_knowledge[_concept] = _path
                            _absorbed += 1
                
                # 同步意图映射
                if _intent and _intent in self._intent_to_method:
                    # ★技术债治理：同 domain_knowledge 分支，显式转 list 再 insert。
                    _existing_paths = list(self._intent_to_method[_intent].get("paths", []))
                    if _path and _path not in _existing_paths:
                        _existing_paths.insert(0, _path)
                        self._intent_to_method[_intent]["paths"] = _existing_paths[:3]
                
                # ★新增：在知识节点间建立纠正关系边
                _node = self._find_node_by_keyword(_concept)
                _correct_node = self._find_node_by_path(_path) if _path else None
                if _node and _correct_node and _node.node_id != _correct_node.node_id:
                    _node.add_semantic_relation(
                        _correct_node.node_id, "correction",
                        round(_rule.get("confidence", 0.6), 3), "qica_growth")
                    _absorbed += 1
        
        if _absorbed > 0:
            self._log(LogLevel.INFO,
                     f"成长规则吸收: 从蒸馏中吸收{_absorbed}条规则（含纠正映射）")
        
        return _absorbed
    def _get_dynamic_paths(self, default_paths: list) -> list:
        """
        ★v23.0新增：获取动态路径（从知识树中取节点最多的路径）。
        作为领域知识库的补充，确保总有路径可用。
        """
        try:
            _path_dist = self._node_pool.get_path_distribution() if self._node_pool else {}
            if _path_dist:
                _sorted = sorted(_path_dist.items(), key=lambda x: x[1], reverse=True)
                return [_p for _p, _c in _sorted[:3] if _c > 0]
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return default_paths    
    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    QICAEvent.CLASSIFY,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== QICA v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock = MockInfoField()
    qica = QICA("QICA")
    qica.set_info_field(mock)
    qica.start()

    tests = [
        ("路灯是谁", "fast", "身份确认"),
        ("status", "fast", "系统命令"),
        ("你好", "fast", "情感问候"),
        ("什么是脉冲场架构", "knowledge", "知识查询"),
        ("这段代码为什么报错", "deep", "技术推理"),
        ("你觉得什么颜色代表生命", "deep", "创造性思考"),
        ("今天天气不错", "fast", "一般对话"),
    ]

    for text, expected_channel, expected_intent in tests:
        r = qica.on_pulse({
            "event_type": QICAEvent.CLASSIFY,
            "payload": {"content": text, "user_name": "小林"},
            "priority": 7, 
        })
        ch_status = "✅" if r["channel"] == expected_channel else "❌"
        print(f"{ch_status} '{text}': channel={r['channel']} (expected={expected_channel}), intent={r['intent_type']}")

    # 验证分类结果脉冲的 layer 标记
    classify_pulses = [p for p in mock.published if p.get("event_type") == QICAEvent.CLASSIFY_RESULT]
    if classify_pulses:
        print(f"\n   CLASSIFY_RESULT脉冲 layer: {classify_pulses[-1].get('layer', '未设置')} (预期L2)")

    s = qica.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"统计: 分类{s['classify_count']}次, 规则命中={s['rule_hits']}")

    qica.stop()
    print("\n=== 自测完成 ===")