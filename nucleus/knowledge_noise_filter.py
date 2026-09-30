# -*- coding: utf-8 -*-
"""
knowledge_noise_filter.py —— 知识噪声过滤器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 过滤知识库中的低质量与冗余节点
机制: 函数式模块，包含10个工具函数
定位: 知识治理层
"""

from nucleus._silent_except import silent_exc
import time  # noqa: F401
from typing import Any

from nucleus.const import ViewMode
from nucleus.logger import get_module_logger


_logger = get_module_logger("knowledge_noise_filter")

# ===== 网页导航噪音词（全小写） =====
# 这些词出现在搜索引擎结果页的导航栏、页脚、侧边栏中，对知识积累毫无价值
WEB_NAVIGATION_NOISE = {
    # 搜索引擎结果页通用导航
    "search", "skip", "content", "accessibility", "feedback",
    "about", "results", "open", "links", "new", "tab", "any",
    "time", "page", "home", "next", "previous", "more", "click",
    "here", "this", "that", "what", "when", "where", "which",
    "with", "from", "your", "have", "been", "were", "they",
    "will", "would", "could", "should", "there", "their",
    "menu", "close", "send", "back", "top", "footer", "header",
    # 搜索引擎功能按钮
    "safesearch", "help", "settings", "sign", "login", "logout",
    "privacy", "terms", "cookie", "cookies", "ad", "ads",
    "subscribe", "newsletter", "share", "follow", "comment",
    "reply", "download", "loading", "please", "enable",
    "javascript", "disable", "block", "allow", "accept",
    "account", "register", "policy", "legal", "notice",
    # 通用UI文本
    "all", "past", "hour", "day", "week", "month", "year",
    "sort", "filter", "view", "list", "grid", "show",
    "hide", "expand", "collapse", "submit", "reset", "cancel",
    "confirm", "delete", "edit", "update", "create", "remove",
    "add", "save", "copy", "cut", "paste", "undo", "redo",
    "read", "less", "full", "story", "article",
    "source", "image", "video", "audio", "document",
    "website", "web", "site", "url", "domain", "http",
    "https", "www", "html", "css", "js", "xml", "json",
}
# ===== 域名后缀列表（供胃、肝脏等器官共用） =====
DOMAIN_SUFFIXES = ['.com', '.cn', '.org', '.net', '.gov', '.edu', '.io', '.html', '.htm']
# ===== 动态扩展机制 =====
# 运行时追加的噪音词（从config加载 + 代码动态添加），与静态集合合并使用
_DYNAMIC_NOISE: set = set()

# 运行时统计：记录被过滤词的频率，用于发现新的噪音模式
_FILTERED_WORD_STATS: dict = {}
_STATS_THRESHOLD = 100  # 单个词被过滤超过此次数时输出提示

def _load_noise_from_config():
    """从config读取补充噪音词列表，合并到动态集合中"""
    try:
        import config
        cfg = getattr(config, 'KNOWLEDGE_NOISE_FILTER', {})
        additional = cfg.get("additional_noise_words", [])
        if additional:
            for word in additional:
                if isinstance(word, str) and len(word) >= 2:
                    _DYNAMIC_NOISE.add(word.lower())
    except Exception as e:
        silent_exc(e, "knowledge_noise_filter.py:72:_load_noise_from_config", level="warning")


def add_noise_word(word: str):
    """
    运行时动态追加一个噪音词。
    供其他模块在发现新的噪音模式时调用。
    
    Args:
        word: 需要追加的噪音词（自动转小写）
    """
    if word and len(word) >= 2:
        _DYNAMIC_NOISE.add(word.lower())


def _log_filtered_word(word: str):
    """
    记录被过滤词的频率统计。
    当某个未知词被频繁过滤时（超过阈值），输出提示供人工审查。
    """
    if len(word) <= 3:
        return  # 过短的词（介词/冠词碎片）不统计
    
    if not word.isascii():
        return  # 非纯英文词不统计
    
    if word in WEB_NAVIGATION_NOISE:
        return  # 已在静态集合中的不重复统计
    
    count = _FILTERED_WORD_STATS.get(word, 0) + 1
    _FILTERED_WORD_STATS[word] = count
    
    if count == _STATS_THRESHOLD:
        # ★v26.0补强：达到阈值时自动添加为噪音词，无需人工干预
        add_noise_word(word)
        _logger.info(f"自动添加噪音词: '{word}' (已被过滤{_STATS_THRESHOLD}次)")
    
    # 统计字典上限保护
    if len(_FILTERED_WORD_STATS) > 500:
        sorted_items = sorted(_FILTERED_WORD_STATS.items(), key=lambda x: x[1], reverse=True)
        _FILTERED_WORD_STATS.clear()
        _FILTERED_WORD_STATS.update(dict(sorted_items[:200]))


def is_noise_keyword(keyword: str) -> bool:
    """
    判断一个关键词是否为无价值的网页导航噪音。
    
    判断规则（只针对英文词）：
    1. 包含中文字符 → 不是噪音（中文术语一定有知识价值）
    2. 包含数字 → 不是噪音（可能是版本号、型号等）
    3. 首字母大写且长度>3 → 不是噪音（可能是专有名词如Python）
    4. 全小写且在噪音词表中 → 是噪音
    
    这样确保不会误杀 Python、DNA、QICA、GPT-4 等有价值的英文术语。
    """
    if not keyword:
        return True
    
    # 包含中文字符 → 不是噪音
    for char in keyword:
        if '\u4e00' <= char <= '\u9fff':
            return False
    
    # 包含数字 → 不是噪音（如 GPT-4, BERT, 3D）
    for char in keyword:
        if '0' <= char <= '9':
            return False
    
    # 纯英文词判断
    keyword_lower = keyword.lower()
    
    # 首字母大写且长度>3 → 很可能是专有名词（Python, Linux, Chrome）
    if keyword[0].isupper() and len(keyword) > 3:
        return False
    
    # 全大写 → 很可能是缩写（DNA, API, GPU, QICA）
    if keyword.isupper() and len(keyword) >= 2:
        return False
    
    # 首字母大写短词（2-3字符） → 可能是缩写（Go, AI, Io）
    if keyword[0].isupper() and 2 <= len(keyword) <= 3:
        return False
    
    # 全小写且在噪音词表中（含静态和动态） → 是噪音
    if keyword_lower in WEB_NAVIGATION_NOISE or keyword_lower in _DYNAMIC_NOISE:
        return True
    
    # 全小写且长度<=3 → 很可能是介词/冠词/代词碎片（the, and, for, is）
    if len(keyword) <= 3 and keyword.islower():
        _log_filtered_word(keyword_lower)
        return True
    
    # 其他情况默认为有价值
    return False




def get_top_valuable_keywords(keywords: list, top_n: int = 10) -> list:
    """
    从关键词列表中选出最有价值的top_n个词。
    优先保留：
    1. 包含中文的词
    2. 首字母大写的英文词（专有名词）
    3. 全大写的缩写词
    最后才考虑全小写的英文词（通常已被过滤）
    """
    if not keywords:
        return []
    
    # 按价值分组
    chinese_keywords = []
    proper_nouns = []
    acronyms = []
    lowercase_english = []
    
    for kw in keywords:
        if is_noise_keyword(kw):
            continue
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in kw)
        if has_chinese:
            chinese_keywords.append(kw)
        elif kw.isupper() and len(kw) >= 2:
            acronyms.append(kw)
        elif kw[0].isupper() and len(kw) > 1:
            proper_nouns.append(kw)
        else:
            lowercase_english.append(kw)
    
    # 按优先级拼接：中文 > 缩写 > 专有名词 > 小写英文
    # 每组内按出现频次排序
    from collections import Counter
    result = []
    
    for group in [chinese_keywords, acronyms, proper_nouns, lowercase_english]:
        if group:
            counter = Counter(group)
            result.extend([kw for kw, _ in counter.most_common(top_n)])
    
    return result[:top_n]


def get_view_mode(source_organ: str, trigger_reason: str) -> str:
    """
    根据信息来源自动判定视角标记。
    内视：本地文件读取、日志扫描、硬件快照、自身源码
    外视：网页搜索、双腿网络抓取、好奇心探索、对话交互
    """
    # 控制器文件读取 → 内视
    if source_organ == "控制器" and trigger_reason.startswith("controller.read_file"):
        return ViewMode.INNER_VIEW
    # 网页搜索 → 外视
    if source_organ == "控制器" and "search" in trigger_reason:
        return ViewMode.OUTER_VIEW
    # 双腿网络抓取 → 外视
    if source_organ == "双腿" and "search" in trigger_reason:
        return ViewMode.OUTER_VIEW
    if source_organ == "双腿" and "fetch" in trigger_reason:
        return ViewMode.OUTER_VIEW
    # 好奇心探索 → 外视
    if trigger_reason in ("curiosity.explore", "dream.deduction", "creative.insight"):
        return ViewMode.OUTER_VIEW
    # 对话消化 → 外视
    if source_organ in ("耳朵", "嘴巴", "大脑皮层"):
        return ViewMode.OUTER_VIEW
    # 自我反思、内部知识 → 内视
    if source_organ in ("前额叶", "肝", "肾", "内在世界"):
        return ViewMode.INNER_VIEW
    # 默认外视
    return ViewMode.OUTER_VIEW



# ===== 模块初始化：加载配置中的补充噪音词 =====
_load_noise_from_config()
# ★v23.0优化：导航噪音模式提取为模块级常量，避免每次调用重复创建
NAV_NOISE_PATTERNS = [
    r'跳至内容\s*', r'辅助功能反馈\s*', r'国内版\s*国际版\s*',
    r'约\s*[\d,]+\s*个结果\s*', r'在新选项卡中打开链接\s*',
    r'时间不限\s*', r'自适应缩放\s*', r'搜索\s*自适应缩放\s*',
    r'安全搜索\s*', r'设置\s*', r'登录\s*', r'反馈\s*',
    r'隐私权\s*', r'条款\s*', r'帮助\s*',
]


def clean_content_text(text: str) -> str:
    """
    对知识节点的value文本进行基本清洗。
    去除HTML标签、URL、搜索引擎导航文本碎片。
    不改变文本的语义内容，只去除明显的格式噪音。
    
    Args:
        text: 原始文本
    
    Returns:
        清洗后的文本，如果清洗后无效则返回空字符串
    """
    if not text or len(text) < 5:
        return ""
    
    import re
    
    # 1. 去除HTML标签
    cleaned = re.sub(r'<[^>]+>', '', text)
    
    # 2. 去除URL
    cleaned = re.sub(r'https?://\S+|www\.\S+', '', cleaned)
    
    # 3. 去除常见的搜索引擎导航碎片（使用模块级常量）
    for pattern in NAV_NOISE_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned)
    
    # 4. 压缩多余空白
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # 5. 长度检查
    if len(cleaned) < 5:
        return ""
    
    return cleaned

# ========== 知识免疫系统（v12.0新增）==========

# ★v23.0优化：否定词对提取为模块级常量
NEGATION_PAIRS = [
    ("是", "不是"), ("可以", "不可以"), ("能", "不能"),
    ("正确", "错误"), ("真", "假"), ("有", "没有"),
    ("存在", "不存在"), ("有效", "无效"), ("成功", "失败"),
    ("支持", "不支持"), ("允许", "禁止"), ("开启", "关闭"),
    ("增加", "减少"), ("上升", "下降"), ("提高", "降低"),
]


def detect_value_contradiction(val_a: str, val_b: str) -> bool:
    """
    检测两个节点值是否相互矛盾。
    供内在世界、肝脏、胃共用。

    ★2026-09-07 阶段二：判定逻辑抽到 nucleus/reasoning/ContradictionDetector.py，
    本函数保留对外接口，转发到 ContradictionDetector.detect_semantic（行为等价）。

    检测方法：
    1. 关键词级别的否定检测——一个包含"A是B"，另一个包含"A不是B"
    2. 极性反转——一个包含正面词，另一个包含对应的负面词

    Returns:
        True 如果存在矛盾
    """
    from nucleus.reasoning.ContradictionDetector import ContradictionDetector
    return ContradictionDetector.detect_semantic(val_a, val_b)

def check_self_consistency_for_node(node_value: str, node_keywords: list, self_nodes: list | None = None) -> dict[str, Any]:
    """
    【v12.0新增】知识免疫核心引擎：检查一个知识节点是否与自我架构知识矛盾。
    
    供内在世界、肝脏、胃等器官共用。传入自我节点列表可复用查询结果。
    
    Args:
        node_value: 候选节点的内容
        node_keywords: 候选节点的关键词
        self_nodes: 可选的自我节点列表（如果不传，返回空结果，调用方需自行获取）
    
    Returns:
        {
            "contradiction_found": bool,
            "contradiction_count": int,
            "trust_penalty": float,
            "conflicting_self_paths": [...]
        }
    """
    result = {
        "contradiction_found": False,
        "contradiction_count": 0,
        "trust_penalty": 0.0,
        "conflicting_self_paths": [],
    }
    
    if not node_value or not node_keywords or not self_nodes:
        return result
    
    _node_kw_set = {kw.lower() for kw in node_keywords if isinstance(kw, str) and len(kw) >= 2}
    if len(_node_kw_set) < 2:
        return result
    
    for _self_node in self_nodes:
        _self_kw = _self_node.keywords if hasattr(_self_node, 'keywords') and _self_node.keywords else []
        _self_kw_set = {kw.lower() for kw in _self_kw if isinstance(kw, str) and len(kw) >= 2}
        
        if len(_self_kw_set) < 2:
            continue
        
        _overlap = len(_node_kw_set & _self_kw_set)
        _min_size = min(len(_node_kw_set), len(_self_kw_set))
        if _min_size == 0:
            continue
        _overlap_ratio = _overlap / _min_size
        
        if _overlap_ratio >= 0.5:
            _self_value = str(_self_node.value) if _self_node.value else ""
            _is_contradiction = detect_value_contradiction(node_value, _self_value)
            
            if _is_contradiction:
                result["contradiction_found"] = True
                result["contradiction_count"] += 1
                _self_path = getattr(_self_node, 'space_path', '/自我/架构')
                result["conflicting_self_paths"].append(_self_path)
    
    if result["contradiction_count"] >= 2:
        result["trust_penalty"] = 25.0
    elif result["contradiction_count"] == 1:
        result["trust_penalty"] = 15.0
    
    return result
# ★v23.0优化：路径碎片词提取为模块级常量，使用set实现O(1)查找
_PATH_FRAGMENT_WORDS: set = {
    # 内部标记
    "节点A", "节点B", "总节点", "层级结构", "六大维度",
    "正在识别", "内容预览", "文件内容",
    # 闲聊碎片
    "夜深了", "早安", "晚安", "好的", "可以", "谢谢", "你好",
    "需要我", "陪我会", "聊会儿",
    # 系统运行时碎片
    "请稍候", "识别完成", "关键内容",
    # ★v17.0新增：大模型分析JSON元字段碎片
    "功能", "参数", "依赖", "风险", "关键步骤",
    "依赖数据", "潜在风险", "功能描述", "参数列表",
    # ★v17.0新增：搜索引擎残词碎片
    "上汽通用", "通用汽车", "有限公司", "通用生物",
    "菜鸟教程", "向日葵远程", "学生信息网", "脑筋急转弯",
    # ★v17.0新增：代码分析元描述碎片
    "代码片段", "方法体不可读", "待大模型分析",
    "无明确风险", "无明确入口", "无明确叶子",
    # ★v17.0新增：对话残词碎片
    "我们聊过", "刚才还在想", "和你聊",
}


def is_path_fragment_word(word: str) -> bool:
    """
    判断一个关键词是否应该被排除在知识树路径之外。
    这些词是内部标记、闲聊碎片或系统运行时产生的临时标签，
    不应该出现在知识节点的路径层级中。
    ★v23.0优化：碎片词表提取为模块级常量，避免每次调用重复创建。
    """
    if not word or len(word) < 2:
        return True
    
    if word in _PATH_FRAGMENT_WORDS:
        return True
    
    # 包含冒号的词（如"节点A:"）——路径中不应出现
    return bool(":" in word or "：" in word)

# ========== v26.0补强：中文停用词与内容质量评估 ==========

# 中文停用词（无知识价值的常见虚词）
CHINESE_STOP_WORDS = {
    "的", "了", "是", "在", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看",
    "好", "自己", "这", "他", "她", "它", "们", "那", "些", "什么", "怎么",
    "如何", "为什么", "可以", "可能", "应该", "需要", "已经", "正在", "将",
    "被", "把", "让", "使", "从", "向", "对", "与", "及", "或", "但", "而",
    "如果", "因为", "所以", "虽然", "但是", "然而", "因此", "于是", "并且",
    "或者", "还是", "不是", "不能", "不会", "不要", "不用", "不行",
    "这个", "那个", "这些", "那些", "这样", "那样", "这里", "那里",
    "一下", "一些", "一点", "有点", "比较", "非常", "十分", "相当",
    "关于", "对于", "由于", "根据", "通过", "进行", "开始", "结束",
    "知道", "觉得", "认为", "发现", "看到", "听到", "想到", "感到",
    "时候", "地方", "东西", "事情", "问题", "方法", "方式", "情况",
    "现在", "以前", "以后", "之前", "之后", "今天", "明天", "昨天",
}


def is_chinese_stop_word(word: str) -> bool:
    """判断是否为中文停用词（无知识价值的虚词）"""
    if not word or len(word) < 1:
        return True
    return word in CHINESE_STOP_WORDS


def is_noise_keyword_enhanced(keyword: str) -> bool:
    """增强版噪音词判断：同时检查英文导航噪音和中文停用词。"""
    if not keyword:
        return True
    # 中文停用词检查
    if is_chinese_stop_word(keyword):
        return True
    # 原有英文噪音检查
    return is_noise_keyword(keyword)


def filter_keywords_enhanced(keywords: list) -> list:
    """增强版关键词过滤：同时过滤英文导航噪音和中文停用词。"""
    return [kw for kw in keywords if not is_noise_keyword_enhanced(kw)]



