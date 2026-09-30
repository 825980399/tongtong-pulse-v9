曈曈 v17.0 代码风格规范
版本：v17.0
制定日期：2026年6月11日
更新日期：2026年7月24日（v17.0窗口完整实践：五个杠杆支点、9个问题修复、4个断层修复、12个深度复盘问题。新增设计原则3条：代码理解进度基准原则、周期任务独立触发原则、情绪数据双通道兜底原则。反模式速查表扩展至AP52。）
参考基准：PulseHeart.py（小林/路灯 从零搭建的标准器官）
适用范围：所有新器官、骨架模块、测试文件

十五、数据持久化与生命周期
十六、自我审视与代码理解
十七、远程API调用与模型管理
十八、审查常见反模式速查（v17.0更新）
十九、核心设计原则（各窗口精华汇总）

| AP50 | SelfInspector路径计算错误 | 项目根路径解析失败 |
| AP51 | 快照恢复pending为空未重扫描 | 代码学习永久停滞 |
| AP52 | 周期任务计数器被业务拦截 | 深度审视/画像同步永远不触发 |

周期任务独立触发原则：深度审视等周期任务计数器必须移到业务拦截之前
情绪数据双通道兜底原则：信息场缓存+知识库查询双通道，确保无人对话时也能获取数据
文档版本：v17.0（2026年7月24日更新：整合精简全部章节，从24节合并为19节。反模式速查表扩展至AP52。新增v17.0设计原则3条：代码理解进度基准原则、周期任务独立触发原则、情绪数据双通道兜底原则。）
状态：53个器官全部在线，框架总评分95/100。+⭐ 工程稳定原则（v18.0）：

曈曈 v25.1 代码风格规范
版本：v25.1
制定日期：2026年6月11日
更新日期：2026年8月29日（v18-v25.1 演进：推理调度结构化、补丁安全门、运行时埋点、全局并行调度、工业级工具层、探查代理编排、本地语义理解增强、审查-修复闭环。反模式速查表扩展至 AP70。）
参考基准：PulseHeart.py（小林/路灯 从零搭建的标准器官）
适用范围：所有新器官、骨架模块、测试文件

目录
一、文件与类结构
二、依赖注入与模块安全
三、日志与调试
四、脉冲通信（核心规范）
五、线程安全与并发
六、配置管理与硬编码
七、安全与防御
八、命名与类型
九、统计与状态
十、自测与验证
十一、冷却与资源管理
十二、知识污染防治
十三、搜索与推理安全
十四、逻辑死循环防护
十五、数据持久化与生命周期
十六、自我审视与代码理解
十七、远程API调用与模型管理
十八、审查常见反模式速查（v25.1更新）
十九、核心设计原则（各窗口精华汇总）
二十、运行时埋点与资源生命周期（v25.1）
二十一、静态审查与自主迭代安全（v25.1）

一、文件与类结构
1.1 文件头部模板
python
"""
PulseXxx —— Xxx器官 · 简短功能描述
版本: v9.5 PulseNet
设计: 路灯、小林、星轨
日期: 2026年6月11日

职责:
    1. 第一条职责
    2. 第二条职责

触发机制:
    订阅 xxx 脉冲，被动触发。无轮询，符合 v9.5 脉冲场架构。
"""
1.2 类结构顺序
类文档字符串 → 2. 常量定义 → 3. __init__ → 4. 依赖注入区块 → 5. 生命周期区块 → 6. 脉冲入口区块 → 7. 核心逻辑区块 → 8. 统计信息区块 → 9. 预留接口区块 → 10. 自测区块
1.3 导入规范
所有导入放在文件顶部，禁止在方法内部重复导入
禁止导入未被使用的模块或枚举常量
新增器官文件时，必须同步确认 main.py 中的导入和创建代码
二、依赖注入与模块安全
2.1 依赖注入格式
每个依赖独立一个方法，完整文档字符串。不允许将依赖注入内联在 __init__ 中。

python
    def set_node_pool(self, node_pool):
        """注入节点池。Args: node_pool: PulseNodePool 实例"""
        self.node_pool = node_pool
2.2 跨模块依赖注入四处同步
当器官新增方法引用了新的依赖模块时，必须同步更新四处：

__init__方法：将新依赖属性初始化为None
set_xxx注入方法：新增对应的依赖注入方法
main.py的_init_organs：增加对应的注入调用
main.py的导入区域：增加新器官类的导入
2.3 跨器官通信安全原则
所有跨器官数据获取必须通过公开接口，禁止直接访问其他器官的私有属性。
所有订阅心跳脉冲（HeartEvent.BEAT）的器官必须显式实现set_info_field方法。

2.4 器官创建时序依赖检查
当A器官依赖B器官时，必须在 _init_organs 中确保B在A之前创建。 不能假设"hasattr检查能兜底"——如果顺序错了，依赖静默失效。

三、日志与调试
3.1 日志级别使用 LogLevel 枚举
python
from nucleus.const import LogLevel
self._log(LogLevel.INFO, "已启动")
self._log(LogLevel.WARNING, f"节点池过大: {total_nodes}")
3.2 关键路径日志级别要求
关键路径上的状态变更、异常跳过、任务失败必须使用 INFO 或 WARNING 级别日志。 DEBUG 级别仅用于高频且不关键的事件。如果某个日志在控制台中永远看不到，但它能解释"为什么某个功能没有工作"，那它就不应该是 DEBUG。

3.3 高频日志必须配备频率控制
高频触发的方法必须在日志输出处增加计数器或冷却机制。 当数值未变化时不再重复输出。诊断日志必须明确说明失败原因，不只是记录"失败了"，还要记录"为什么失败"。

四、脉冲通信（核心规范）
4.1 脉冲处理模式
python
def on_pulse(self, pulse: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not self.is_running: return None
    event_type = pulse.get("event_type", "")
    payload = pulse.get("payload", {})
    if event_type == "xxx.yyy": return self._handle_xxx(payload)
    elif event_type == "heart.beat": return self._handle_beat()
    return None
4.2 所有订阅事件必须有对应处理分支
python
# ✅ 正确：每个订阅事件都有处理分支
def get_resonance_conditions(self) -> list:
    return [{"organ_name": self.organ_name,
             "event_types": [GrowthEvent.ASSESS, SystemEvent.STATUS_REQUEST],
             "min_priority": 1}]

def on_pulse(self, pulse):
    if event_type == GrowthEvent.ASSESS: return self._on_assess(payload)
    elif event_type == SystemEvent.STATUS_REQUEST: return self._on_status_request()
    return None
4.3 器官间禁止直接方法调用，所有跨器官通信必须通过信息场脉冲
python
# ✅ 正确：脉冲通信
self._emit(InferenceEvent.REQUEST, {...})

# ❌ 错误：直接调用
self.inner_world._rule_reason(content)
4.4 脉冲事件类型应反映发射方自身的身份和意图
python
# ✅ 正确：伦理模块发射自己的审查结果
self._emit(EthicsEvent.REVIEW_RESULT, {"verdict": "warning"})

# ❌ 错误：肾脏发射兴趣模型的事件类型
self._emit(InterestEvent.CHANGED, {...})  # 肾脏不应以兴趣模型身份发射脉冲
4.5 新增通信链路必须双向确认（发射方+接收方+处理分支）
发射方正确地 _emit 了该事件
接收方在 get_resonance_conditions 中订阅了该事件
接收方在 on_pulse 中有对应的处理分支
4.6 所有 _emit 调用必须指定 layer（L0/L1/L2/L3）
python
# ✅ 正确
self._emit(HeartEvent.BEAT, payload, layer="L0")   # 生命线
self._emit(ChatEvent.MESSAGE, payload, layer="L1") # 实时交互
self._emit(KnowledgeEvent.WRITTEN, payload, layer="L2") # 认知思考
self._emit(PurgeEvent.PURGE_CHECK, payload, layer="L3") # 后台自主

# ❌ 错误
self._emit(HeartEvent.BEAT, payload)  # 缺少 layer
4.7 禁止在 on_pulse、_emit、get_resonance_conditions 中使用裸字符串
python
# ✅ 正确
if event_type == HeartEvent.BEAT: ...

# ❌ 错误
if event_type == "heart.beat": ...
4.8 脉冲意图规范
跨器官脉冲建议携带 intent 参数，帮助接收方理解意图类型。

python
intent="request"   # 请求执行操作
intent="suggest"   # 建议执行操作（非强制）
intent="alert"     # 告警通知
intent="notify"    # 一般通知
五、线程安全与并发
5.1 锁内禁止发射脉冲
python
with self._fuse_lock:
    need_alarm = self._error_count >= self._fuse_threshold
# 锁外发射
if need_alarm:
    self._emit(SystemEvent.ALARM, alarm_info, layer="L0")
5.2 共享字典的COW策略（深拷贝+原子替换）
python
# ✅ 正确：深拷贝+原子替换
import copy
new_copy = copy.deepcopy(self._config_dict)
_deep_merge(new_copy, override_dict)
self._config_dict = new_copy  # 原子替换

# ❌ 错误：原地修改全局字典
_deep_merge(self._config_dict, override_dict)
5.3 共享变量的读写保护（使用 threading.Lock 或 threading.RLock 保护）
如果器官使用了 threading.Lock，get_stats 应在锁内读取数据。

5.4 嵌套调用场景使用RLock
所有可能在多线程中并发访问的共享变量（字典、列表、计数器）必须使用 threading.Lock 或 threading.RLock 保护。

全局代码审查发现的遗漏案例：

KnowledgeTree._path_stats（无锁）
PulseSelfAwareness._personas（无锁）
PulseHormones._social_memory（无锁）
PulseMetricsCollector缓存字典（无锁）
PulseController._load_level（无锁）
PulseSubconscious._user_present和_interest_weights（无锁）
嵌套调用场景使用RLock：当同一个线程需要多次获取同一把锁时（如方法A获取锁后调用方法B，方法B也尝试获取同一把锁），使用threading.RLock()。
六、配置管理与硬编码
6.1 所有词表/阈值/映射从config读取，必须有完整的try/except兜底
python
# ✅ 正确
def _load_config(self):
    try:
        import config
        cfg = getattr(config, 'CONFIG_BLOCK_NAME', {})
        self._threshold = cfg.get("key", default_value)
    except Exception:
        self._threshold = default_value
6.2 新增配置块使用大写蛇形命名（如HEADLESS_BROWSER）
新增配置块时，使用大写蛇形命名（如HEADLESS_BROWSER、RISK_PATTERNS），配置项使用小写蛇形命名（如page_load_timeout、cpu_critical）。

6.3 兜底保护
每个从config读取的方法必须有完整的try/except兜底，确保在config不可用时功能不中断。兜底值应保证基本可用性。

6.4 配置硬编码迁移清单
以下类型的参数必须从代码迁移到config.py：

运行时可能需要调整的阈值（冷却时间、触发频率、评分权重）
需要热更新的词表（领域关键词、安全词表、情绪映射）
与具体环境相关的配置（路径、URL、端口）
产品策略相关的参数（探索频率、问候间隔）
以下类型保留在代码中，不需要迁移：

底层框架约定（文件扩展名映射、器官名称列表）
核心算法参数（五维共振权重）
一次性定义的常量（人格锚点）
6.5 迁移完成后验证清单
每次配置硬编码迁移完成后，必须验证：

使用原硬编码值的所有位置已改为 self._xxx 或从 config 读取
config 中的配置块命名符合规范（器官名_CONFIG）
兜底值与原硬编码值完全一致
重启后功能正常（python main.py 无报错）
七、安全与防御
7.1 三级安全沙箱：L1皮肤 → L2沙箱 → L3伦理
7.2 白细胞必须区分真正攻击与高频正常交互
7.3 安全词表必须支持上下文白名单，避免复合词误杀
7.4 Python中 else 的归属由缩进决定，任何 else 出现时必须向上追溯确认匹配的是哪个 if 或 try
八、命名与类型
类型	规范	示例
文件名/类名	PascalCase	PulseHeart
方法名/私有方法	snake_case / _snake_case	on_pulse / _analyze_text
常量	UPPER_SNAKE	MAX_FILE_SIZE
实例变量	snake_case	self._beat_count
所有方法签名必须有完整类型提示。

九、统计与状态
9.1 所有器官必须实现 get_stats
每个器官必须提供 get_stats 方法，返回字典至少包含 organ 和核心计数器。

9.2 _on_status_request 统一调用 get_stats
_on_status_request方法体统一为return self.get_stats()，消除代码重复。

python
# ✅ 正确：_on_status_request 调用 get_stats
def get_stats(self) -> Dict[str, Any]:
    return {
        "organ": self.organ_name,
        "xxx_count": self._xxx_count,
        "is_running": self.is_running,
    }

def _on_status_request(self) -> Dict[str, Any]:
    return self.get_stats()

# ❌ 错误：两个方法返回相同字典结构的重复代码
def get_stats(self) -> Dict[str, Any]:
    return {"organ": self.organ_name, "xxx_count": self._xxx_count}

def _on_status_request(self) -> Dict[str, Any]:
    return {"organ": self.organ_name, "xxx_count": self._xxx_count}  # 重复！
9.3 统计信息应覆盖核心业务计数、运行状态标记、器官特有指标
如果器官只有 _on_status_request 而没有 get_stats 方法，应将 _on_status_request 的返回内容提取为新的 get_stats 方法，然后 _on_status_request 调用它。

9.4 统计信息应覆盖
核心业务计数
运行状态标记
器官特有指标
十、自测与验证
10.1 自测覆盖正常/异常/边界三条路径
每个器官文件必须包含 if __name__ == "__main__": 自测块，覆盖：

正常路径（功能正常执行）
异常路径（错误被正确捕获）
边界路径（极值情况）
10.2 文件操作后运行最小验证
bash
python -c "from organs.body.PulseStomach import PulseStomach; print('导入成功')"
或直接运行 python main.py 确认启动正常。

10.3 测试参数恢复
所有为测试降低的参数必须在验证通过后立即恢复为正常生产值。

十一、冷却与资源管理
11.1 内部触发必须配备冷却机制
任何由内部触发、涉及外部资源消耗的行为（深度搜索、网络请求、文件读写），必须在设计阶段就配备冷却机制。

11.2 三种标准冷却模式
模式一：时间间隔冷却
模式二：触发轮次冷却
模式三：维度独立冷却

11.3 外部资源必须在finally块中确保回收
所有涉及浏览器窗口、文件句柄、网络连接的操作，必须在finally块中执行资源回收。

11.4 高负载下的自我保护
所有器官的异步任务应通过 info_field.submit_adaptive_task() 提交，由信息场根据当前硬件负载决定是否执行。

11.5 周期性认知任务配备轮转计数器
在心跳驱动的认知反思中新增重量维度时，必须为每个重量维度设置独立的轮转触发频率，避免单次心跳负载过重。

11.6 补丁应用防循环保护（v16.0新增）
任何自动触发重启的机制必须有循环保护。 补丁应用后自动重启框架验证，如果连续重启超过3次，必须停止自动重启并告警。使用文件计数器记录重启次数，启动成功后清零。

十二、知识污染防治
12.1 九道防线全链路覆盖
防线	位置	防护内容
零	胃 _extract_keywords	关键词源头净化
一	胃 _create_sub_path	路径合法性校验
二	肝 _compress_l1_to_l2	压缩前过滤污染L1
三	肝 _fuse_group	L3提取实质内容
四	肝 _reverse_activate_related_nodes	逆向激活领域相关性检查
五	肝 _semantic_association_scan	语义扫描追加关键词限制
六	内在世界知识编织	追加关键词前检查路径相关性
七	肝 _periodic_purity_check	L2出口拦截+顽固噪音降级
八	肾 _on_purge_check	低信任L2节点清理
12.2 关键词追加必须检查领域相关性
任何向已有知识节点追加关键词的操作，必须验证候选关键词与目标节点路径存在语义关联。

12.3 知识陈述不应触发外部搜索
包含定义/解释性动词（"是指""通过""利用""包括""具有"等）且长度超过25字的输入，应判定为知识陈述，直接走内在沉思而非深度搜索。

12.4 L4本能概念不适合外部搜索
"求真""向善""迭代""自律"及其子概念是哲学层面的抽象概念，搜索引擎只能返回字典释义或无关内容，应走内在沉思通路。

12.5 合并成功后必须降级源节点重要性
压缩/融合产生新节点后，被合并的源L1/L2节点必须降低重要性和抽象度，避免它们在下一轮压缩中再次被选中导致无限循环。

12.6 低质量节点不应持久化
质量评分（_assess_node_quality）< 0.5 的L2节点应标记为临时节点，让时间自然淘汰。

12.7 知识入口的URL碎片过滤（v5.0新增）
胃消化创建L1节点时，必须在清洗阶段过滤URL碎片、搜索引擎格式残留和域名片段，确保进入知识库的内容纯净。

12.8 知识编织的内容质量预检（v5.0新增）
内在世界的知识编织入口必须对L1节点内容进行质量预检，跳过明显的搜索引擎格式残留节点。

12.9 知识编织的推理模式领域过滤（v8.2新增）
推理过程中只编织与推理核心路径相关的知识节点。 内在世界在推理模式下（_is_inference_mode=True）执行知识编织时，只关联以下路径的节点：/自我/架构、/技术/架构、/知识/、/本能/、/推理/、/身份/自我、/反思/。这阻止了物理百科、PDF文件结构、闲聊碎片等非架构类知识污染推理上下文。

12.10 搜索反馈关键词白名单保护（v8.2新增）
技术/架构类核心关键词禁止被搜索反馈闭环永久降级。 在 PulseSubconscious._on_search_feedback 中，42个保护关键词（如"架构""框架""推理""逻辑""知识体系"等）永远不进入永久降级通道，仅记录日志。

12.11 清理工具路径白名单保护（v16.0新增）
清理工具的"内容去重"步骤必须保护核心自我知识路径。 /自我/架构/ 下的知识节点（器官职责、五维共振、稳态规则、推理算子等）是框架的基础认知，不应被去重逻辑误合并或删除。

python
# ✅ 正确：内容去重时跳过核心自我知识路径
if path_a.startswith("/自我/架构") or path_b.startswith("/自我/架构"):
    continue
12.12 代码学习知识路径保护（v16.0新增）
清理工具的噪音检测必须跳过代码学习路径。 /自我理解/代码 路径下的知识节点是代码自学习的结果，不应被噪音信号误清理。

十三、搜索与推理安全
13.1 搜索主题必须经过语义范畴判断
在发射搜索脉冲之前，必须判断搜索主题是否属于"适合外部搜索引擎"的范畴。

13.2 推理链路中的变量必须在使用前初始化
在 _on_inference_request 等长方法中，所有可能被引用的局部变量必须在方法早期初始化。

13.3 内在沉思不应阻断深度搜索
当知识库中无相关知识时，沉思应返回 None 让外层继续执行深度搜索触发逻辑，而非生成模板回答直接返回。

13.4 低相关性种子记忆不应阻断搜索
关键词匹配到种子本能节点但匹配分数不足时，应继续走深度搜索。

13.5 搜索终止标记必须在每次搜索开始时重置
控制器的 _search_terminated 标记必须在每次新搜索开始时重置为 False。

13.6 推理缓存必须配备过期机制
推理缓存条目必须设置有效期。过期后自动清除并重新检索。

13.7 搜索反馈黑名单必须防止无限循环
同一低质量方向累计标记超过阈值后，应永久降低其探索优先级。

13.8 口语化搜索词清洗（v5.0新增）
搜索词预处理必须清洗"什么是/如何/怎么"等口语化前缀，提取核心概念后组合为搜索引擎能理解的术语形式。

13.9 搜索阶段效率优化（v5.0新增）
阶段1搜索后优先从当前页面直接提取链接，使用原始搜索词匹配筛选，避免不必要的二次搜索。

13.10 搜索词预处理必须去除指令前缀（v6.0新增）
搜索词在发送给搜索引擎之前，必须去除"请用中文解释""请解释""请说明"等面向框架的指令前缀，以及"的基本原理""的核心概念""的含义"等冗余后缀。这些是人类对框架的指令，不是搜索引擎能理解的搜索主题。

python
# ✅ 正确：去除指令前缀和后缀
_instruction_prefixes = [r'请用中文解释\s*', r'请解释\s*', r'请说明\s*']
for _prefix in _instruction_prefixes:
    _match = re.match(_prefix, topic)
    if _match:
        topic = topic[_match.end():].strip()
        break
_redundant_suffixes = [r'的基本原理\s*$', r'的核心概念\s*$', r'的含义\s*$']
for _suffix in _redundant_suffixes:
    topic = re.sub(_suffix, '', topic)
13.11 复杂问题应优先大模型而非搜索引擎（v6.0新增）
当问题复杂度>0.4且远程API可用时，应优先将问题交给大模型处理，而非送入搜索引擎。搜索引擎无法理解复杂的长句查询，会产生字典碎片噪音。大模型更适合处理需要理解、组织、推理的复杂问题。

13.12 推理题跳过搜索经验查询（v8.2新增）
大脑皮层在工具选择时，推理类问题不应查询搜索经验库。 推理走内在世界算子，不走搜索通道。在 _assess_tool_suitability 中增加推理信号检测，匹配到推理信号则直接设置 should_search=False 并跳过搜索经验查询。

13.13 经验匹配路由成功后直接返回（v8.2新增）
经验匹配路由命中后，算子执行成功则直接返回结果；算子执行失败则走大模型/诚实兜底，不再继续正则路由。 这防止了经验库判断了正确类型但算子执行失败后，控制流被后续正则路由的错误类型抢走。

13.14 大模型提炼触发优化（v16.0新增）
当搜索词包含品牌歧义词时，即使独立短语数量较少也应触发大模型提炼。 如"通用能力提升"中的"通用"容易被搜索引擎误解为"上汽通用汽车"。

13.15 提炼经验缓存复用（v16.0新增）
大模型提炼成功后的结果应缓存为模式经验。 下次遇到相同结构的搜索词时，优先复用缓存，减少大模型调用次数。

十四、逻辑死循环防护
14.1 任何循环操作必须配备单次执行上限
在心跳驱动的循环逻辑中，如果跳过冷却可能导致无限循环，必须增加单次心跳内的执行次数上限。

14.2 紧急通道必须与常规通道有明确的分流条件
紧急通道不能简单重复调用常规通道的同一方法——必须确保紧急通道的执行会改变触发条件。

14.3 合并/去重操作必须检查是否真正减少了待处理量
知识合并成功后，必须确认被合并的源节点已降级，L1总数确实减少。

14.4 两个正确设计意图可能产生对冲
设计时必须检查两个独立机制在极端条件下的交互行为。

14.5 去重合并的黑洞效应防护（v5.0新增）
当合并重叠率达到100%时，说明新L2与已有节点完全重复，应跳过不合并，避免一个节点吸收所有新内容导致L2永远不增长。

14.6 跨路径压缩的路径分配策略（v5.0新增）
跨路径压缩时必须按L1节点的实际路径分布动态选择L2归属路径，避免将所有L1强行归入单一路径导致压缩产物高度重叠。

14.7 防重入保护必须覆盖多路径触发场景（v6.0新增）
当同一个操作可能被多条逻辑路径触发时（如搜索终止回退和复杂度直接推给大模型同时触发肺调用），必须使用集合或字典记录已触发的事件，防止重复执行。

python
# ✅ 正确：使用集合记录已触发的问题，防止重复调用
self._direct_to_lung_questions.add(question.strip())
# 在另一条路径中检查
if search_topic in self._direct_to_lung_questions:
    return  # 已经处理过，跳过
十五、数据持久化与生命周期
15.1 搜索经验必须被系统化记录
每次搜索完成后，必须在经验库中记录：搜索主题、关键词、是否成功、是否被终止、最佳工具推荐。

15.2 经验记录与经验查询必须使用统一的key构造方法
记录端和查询端如果使用不同的分词逻辑构造经验key，会导致经验永远无法被匹配。必须使用同一个_make_experience_key方法。

15.3 大脑皮层必须能在工具选择前查询经验库
在_assess_tool_suitability方法中，必须在设置默认工具选择后、返回前查询搜索经验库，并根据经验覆盖默认选择。

15.4 成功率完全为0时强制跳过搜索
当经验库显示某类搜索的成功率=0%时，_assess_tool_suitability必须强制设置should_search = False，门槛只需1次尝试即可生效。

15.5 内在世界必须根据tool_hint调整回退策略
内在世界的元认知决策阶段必须检查tool_hint.should_search字段，为False时从fallback_tools中移除deep_search。
十六、自我审视与代码理解
16.1 L1节点必须独立持久化
L1节点默认不持久化到主快照，需要专门的pulse_l1_snapshot.json文件独立存储。启动时在主快照加载后恢复L1快照。

16.2 压缩成功后必须清理被压缩的源L1
肝脏压缩产生新L2节点后，必须从节点池中移除被压缩的源L1节点，释放内存并避免快照膨胀。

16.3 知识信任分数必须基于多维评估
信任分数不能仅基于来源判定，必须综合评估来源可信度、关联验证度、内容质量、时间衰减和领域匹配度。

16.4 被检索命中是对知识的实践验证
知识节点被检索命中时，信任分数应有更明显的提升（≥2.0），体现"被使用就是被验证"的原则。

16.5 所有回复必须纳入消化链路
嘴巴输出的所有回复——包括模型生成、内在沉思兜底回答、代码执行结果——都必须发射消化脉冲，确保知识演化链路没有断点。

16.6 快照精简必须定期执行（v6.0新增）
框架长期运行后，快照文件可能积累大量过期节点。必须定期清理：创建超过24小时且信任分数<30的L1节点、ephemeral=True的临时节点、超过5份的冗余备份。

16.7 器官额外状态必须在退出前合并到快照（v6.0新增）
器官（如内在世界）的运行时状态（代码理解进度、对话记忆、活跃学习目标、等待队列）必须在框架退出前通过merge_organ_extra_state合并到快照的extra_state中，确保重启后可恢复。

16.8 叙事自我周期报告必须独立持久化（v8.2新增）
叙事自我的 _weekly_reports 列表必须在框架退出时保存到 extra_state，启动时恢复。 这确保了曈曈重启后不会丢失成长阶段记忆——周期报告是生命叙事中最重要的"阶段性总结"，缺失它们会导致"我能恢复叙事事件数量但不知道经历过哪些成长阶段"的问题。

python
# ✅ 正确：main.py stop() 中保存周期报告
if hasattr(narrative, '_weekly_reports') and narrative._weekly_reports:
    _recent_reports = narrative._weekly_reports[-5:]
    extra_state["weekly_reports"] = [...]

# ✅ 正确：main.py start() 中恢复周期报告
_saved_reports = extra_state.get("weekly_reports", [])
if _saved_reports and hasattr(narrative, '_weekly_reports'):
    narrative._weekly_reports = _saved_reports
16.9 新器官的进度状态持久化（v16.0新增）
拆分出的新器官如果有运行时进度（如代码学习器官的pending列表），必须在退出时通过 get_pending_extra_state 收集状态，在启动时从快照恢复。

十七、远程API调用与模型管理
17.1 远程API调用必须配备完整的降级链：高级模型→默认模型→本地模型→兜底回复
17.2 大模型回复必须经过四维度质量评估才能消化为知识
17.3 大模型回复必须通过MouthEvent.SPEAK脉冲发送
17.4 不能在方法内硬编码API密钥作为默认值
17.5 根据task_type智能选择模型——代码分析/深度推理使用v4-pro，普通对话使用v4-flash
十八、审查常见反模式速查（v17.0更新）
编号	反模式	说明
AP1	跨器官私有属性访问	self.other_organ._private_attr
AP2	脉冲事件类型混用	器官A发射器官B的事件类型
AP3	订阅事件缺少处理分支	订阅但on_pulse中未处理
AP4	配置硬编码	词表/阈值写死在代码中
AP5	线程安全遗漏	共享状态无锁保护
AP6	get_stats重复	两个方法返回相同字典
AP7	主动拉取而非订阅缓存	替代脉冲订阅
AP8	锁内发射脉冲	with self._lock: self._emit(...)
AP9	变量定义在使用之后	局部变量未提前初始化
AP10	关键词追加无领域检查	未验证语义关联
AP11	沉思阻断深度搜索	模板回答阻断搜索
AP12	高频日志无频率控制	日志洪流
AP13	合并后未降级源节点	L1占比不降
AP14	缓存无过期机制	回答过时
AP15	黑名单无累计计数	无限循环
AP16	经验记录写入冲突	多个模块写入冲突
AP17	经验key不一致	记录端和查询端key不同
AP18	变量作用域丢失	分支修改变量后丢失
AP19	代码理解缺少进度表	重复处理已理解方法
AP20	远程API调用后未发射脉冲	用户看不到回复
AP21	方法内硬编码API密钥	密钥泄露风险
AP22	同一操作被多路径重复触发	缺少防重入标记
AP23	两个同名方法导致覆盖	旧版方法未删除
AP24	推理路由基于特定问法硬编码	缺乏通用性
AP25	结构化报告被长度截断破坏	元认知报告被截成碎片
AP26	对话/后台信号混用	后台学习内容通过嘴巴输出
AP27	快照校验字段未同步更新	导入工具未更新校验字段
AP28	变量名冲突导致字典被覆盖	_cooldown同时用作字典和整数
AP29	共享状态的周期性任务被拦截	被推理链不足条件阻止
AP30	正则过拟合特定题干格式	一个变量一个专用正则
AP31	路由触发词堆叠	elif链堆积，路由互抢
AP32	方法内重复 import re	异步线程调用下可能失效
AP33	闭包变量在异步线程中访问失败	应通过实例属性访问
AP34	正则字符类包含Unicode特殊字符	应使用非捕获分组替代
AP35	经验库存储unknown类型经验	无法导向任何算子
AP36	推理信号拦截不完整	多入口缺乏推理信号检测
AP37	文件分析检测在推理路由之后	文件问题被推理路由拦截
AP38	冲突判定依赖中间变量传递	变量被后续代码覆盖
AP39	经验库惯性污染路由	历史经验占比过高
AP40	兜底路由无排他性保护	抢占了其他路由的题型
AP41	缩进错位导致else归属错误	else与try而非if配对
AP42	依赖注入时序颠倒	依赖方在依赖对象创建之前注入
AP43	重复发射启动脉冲	器官重复初始化
AP44	清理工具误删核心知识	未保护/自我/架构路径
AP45	代码学习关键词污染	参数默认值混入关键词
AP46	补丁自重启无循环保护	反复重启形成死循环
AP47	异常捕获后静默失败	except Exception: pass无日志
AP48	心跳驱动器官缺少info_field注入	心跳脉冲无法接收
AP49	异步任务提交未检查返回值	任务静默丢失
AP50	SelfInspector路径计算错误	项目根路径解析失败
AP51	快照恢复pending为空未重扫描	代码学习永久停滞
AP52	周期任务计数器被业务拦截	深度审视/画像同步永远不触发
AP53	周期任务无 try/finally 续期	异常时重调度跳过，定时器永久停摆
AP54	锁内执行 O(n²) 重型操作	矛盾检测/图谱构建持锁，阻塞其他线程
AP55	幂等去重使用唯一 ID	每次 emit 都唯一，去重永不命中
AP56	看门狗/重建同尺寸早退	current==target 直接 return，池永不重建
AP57	补丁状态生产/消费不一致	写 verified 但只认 approved，永不落地
AP58	信任分/风险等级硬编码	默认门槛 100、补丁无 trust_score，永远拒绝
AP59	启动期蒸馏先于回调注册	规则产出即被清空丢弃
AP60	字符串与整数比较	risk_level="低" 与 1 比较必抛 TypeError
AP61	持久化恢复丢字段	调用图保存含 args、恢复时丢 args
AP62	全局检索相关性门过宽	任意 2 字重叠即判相关，张冠李戴
AP63	单例无锁初始化	多线程并发首次调用产生双实例
AP64	超时释放槽位但泄漏线程	future.result(timeout) 不 cancel 运行线程
AP65	重启计数器误用为调用计数器	每次调用 +1，满 3 次永久锁死
AP66	人格 prompt 不统一	system 弱约束/user 角色混用，无反向约束
AP67	订阅输入事件而非输出事件	旁路过滤层（如 mouth.speak vs mouth.reply）
AP68	二进制内容 rstrip	multipart 解析 rstrip 损坏图片/PDF 末尾字节
AP69	innerHTML 未转义	用户输入/AI 回复注入 / 触发 XSS
AP70	非 daemon 线程/进程未 shutdown	进程退出后后台 python 残留
十九、核心设计原则（各窗口精华汇总）
⭐ 地基原则（v9.0-v9.7）：

叠加而非替换 · 预埋而非实现 · 最小侵入
使命即技术约束 · 物种视角 · 硬件自适应
来源决定持久化 · 通用逻辑+冷却保护
源头到终点的全链路保护 · 安全进化原则
⭐ 推理与知识原则（v15.0-v15.3）：

通用逻辑优于特定修复 · 插件化优于集中式
独立持久化优于杂项字段 · 路由结构特征优于关键词堆叠
闭包安全原则 · 经验库自我进化优于硬编码路由规则
从"修复问题"到"建立机制"是架构思维的质变
推理输出纯净性 · 冲突判定语义对立 · 碎片词通用防护
融合架构降级安全 · 知识净化自动化防线
⭐ 架构与协作原则（v16.0）：

器官拆分耦合度评估 · 代码自动修复安全分层
清理工具白名单保护 · 依赖注入时序检查
⭐ 自我感知原则（v17.0）：

杠杆支点优先：用最少的代码修改撬动最大的框架质变
自我认知统一汇聚：一个方法汇聚十一维度，串联优于单点优化
正向反馈闭环：任何周期性的自主活动都应有进度感知和情感反馈
能力串联优于单点优化：十个90分能力不如一个将它们串联的入口
外部审查常态化：每个窗口至少安排一次核心模块的外部审查
知识路径源头污染防治：修复必须同时作用于源头和传播路径
info_field注入强制检查：心跳驱动器官必须显式实现注入方法
代码理解进度基准原则：已理解数直接从知识库统计，不依赖内存变量
周期任务独立触发原则：深度审视等周期任务计数器必须移到业务拦截之前
情绪数据双通道兜底原则：信息场缓存+知识库查询双通道，确保无人对话时也能获取数据
⭐ 工程稳定原则（v18.0）：

推理调度结构化：巨型分支拆为检测器+调度循环
并行重构三步法：提取保留旧逻辑→验证覆盖→再删旧代码
变量前置初始化：跨分支共享变量方法开头赋默认值
独立通道解冲突：机制对冲时开辟独立通道
乐观重试悲观兜底：重试必须设永久放弃上限
异常因果分析法：数据波动先分析因果，不急于判 bug
环境预检原则：依赖外部编译/API 的能力启动先预检，不可用自动降级
补丁开关统一：自动修改逻辑共用一个全局总开关
⭐ 人格深化原则（v21.0）：

内层优先于外层：基底层级越靠内优先级越高
状态先于行为：自主行为决策前先评估存续状态
恢复优先于成长：低位时自我修复优先于探索进化
连续性高于增益：自我修改不得牺牲人格连续性
⭐ 基础设施原则（v25.1）：

分级初筛原则：规则引擎按置信度分级（高置信输出/中置信推 LLM/低置信记录），降噪
层级交叉校验原则：静态疑点→运行时验证；LLM 结论→规则反哺；运行时异常→LLM 定向审查
审查-修复闭环原则：发现问题必须打通到解决问题（发现→分析→修复→验证→审批→应用→回流）
零阻塞埋点原则：指标采集不得在锁内、不得阻塞主链路，有界队列+异步聚合
硬件自适应并行原则：并行度随 CPU 核数与负载动态调整，串行任务强制串行
核心变更审批原则：基础设施/补丁系统/启动入口的自动变更必须人工审批
现场快照原则：锁等待超时/队列积压/重入触发时抓取调用栈+锁状态+脉冲类型
工具优先于手写规则：语法/类型/安全等确定性检查优先用 ruff/mypy/bandit
二十、运行时埋点与资源生命周期（v25.1）
20.1 运行时埋点必须零阻塞
运行时指标（脉冲耗时、锁等待、队列深度、线程数、重入、异常快照）通过 nucleus/runtime_metrics.py 采集，统一遵守：

有界队列 + put_nowait：队列满即丢弃，永不背压主链路
独立聚合线程批量消费，禁止在锁内、禁止在脉冲处理主路径上直接更新指标
python
# ✅ 正确：投递，不阻塞
from nucleus.runtime_metrics import get_runtime_metrics
get_runtime_metrics().record_pulse(duration_ms=..., lock_wait_ms=..., queue_depth=...)

# ❌ 错误：在 with self._lock 内直接计算/写指标
20.2 异常必须携带现场快照
锁等待超时、队列积压、周期任务重入、脉冲处理异常时，必须记录「脉冲类型 + traceback + 锁状态 + 线程数」，而非仅计数。事后能定位根因，而非只有数字。

python
get_runtime_metrics().record_error(pulse_type, error, traceback_text, lock_held)
get_runtime_metrics().record_reentry(task_name)
20.3 进程退出必须彻底清理
stop() 必须显式关闭所有非 daemon 资源，避免退出后后台残留：

分层线程池 shutdown(wait=False, cancel_futures=True)
外部执行器 / 并行调度器 shutdown()
运行时聚合线程 stop()
推理进程池：shutdown 后遍历子进程 terminate()
20.4 硬件自适应并行
并行度不得硬编码固定值，需随 os.cpu_count() 与运行时负载动态计算；快照保存、补丁应用、文件写、关闭等操作必须标记为串行。

二十一、静态审查与自主迭代安全（v25.1）
21.1 确定性检查优先用工业级工具
语法/规范/类型/安全四类检查统一走 nucleus/tooling_runner.py（compileall/ruff/mypy/bandit），不要手写正则去仿。self_inspector 只负责工具结果聚合 + 高置信度坏味道初筛。

21.2 分级初筛与假阳性治理
规则引擎输出必须标注置信度分级（high 直接输出 / medium 推 LLM / low 仅记录），假阳性库必须磁盘持久化（data/false_positive_rules.json），避免审查结果噪音过大导致无人使用。

21.3 自主迭代三道安全门
补丁自动落地前必须同时通过：

信任分：由方案 benefit_score 推导，不得硬编码
风险等级：由方案 risk_score 映射，中等/高风险禁止自动应用
冷却时间：上次应用后未过冷却禁止再次应用
21.4 核心变更强制人工审批
main.py/config.py/base/nucleus/pulse/field/mnemosyne/reasoning 等基础设施补丁必须置为 verified（仅建议），仅非核心且低风险补丁才允许 approved 自动应用。这落实「最终决策权在创造者手中」。

21.5 补丁落盘原子化 + fail-closed
补丁/队列/快照写入必须 mkstemp + os.replace 原子替换
防循环重启计数器：写失败时拒绝应用（fail-closed），避免磁盘满时无限重启
文档版本：v25.1（2026年8月29日更新：反模式速查表扩展至 AP70；新增 v18/v21/v25.1 设计原则；新增「运行时埋点与资源生命周期」「静态审查与自主迭代安全」两节。）
状态：约 58 个器官在线，反模式速查 70 条，核心原则覆盖 v9.0–v25.1。