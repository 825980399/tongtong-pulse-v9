from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化
from nucleus._warn_throttle import should_warn
from config import EXTERNAL_CALL_TIMEOUTS  # noqa: F401
"""main —— v9.5 PulseNet 脉冲框架总入口（自进化基座版）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys
from typing import Any

# ===== 第三方库弃用警告抑制（不影响功能，仅净化启动日志）=====
import warnings
# jieba 内部使用已弃用的 pkg_resources API（setuptools>=81 触发）
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
# PyTorch 旧版 pynvml 弃用警告（已卸载 pynvml，保留作双保险）
warnings.filterwarnings("ignore", message="The pynvml package is deprecated")
# ===== 警告抑制结束 =====

# ===== 性能优化：尝试注册 C 扩展，绕过 GIL =====
# json 解析优化
try:
    import json as _json  # noqa: F401

    import orjson  # noqa: F401
    # 未来所有 json 操作可替换为 orjson
    print("[性能] orjson 可用，JSON 解析性能将大幅提升")
except ImportError as _se:
    silent_exc(_se, "main.py:29")

# 正则表达式缓存（避免重复编译）
import re as _re  # noqa: I001
_re._cache = {}  # 预留给后续正则优化
# ===== 性能优化结束 =====
import signal
import threading
import time
import logging

# ★P1修复: 启动时把当前工作目录归一化到项目根目录
# 背景: 知识快照/parquet/日志/本能快照均使用相对路径（如 data/knowledge/...），
# 若从其他目录启动（如 cd /home && python /path/to/main.py），
# 相对路径会被解析到错误的 cwd，导致数据「写错位置、知识看似消失」。
# 此处强制 chdir 到 main.py 所在目录，保证所有相对路径稳定落在项目内。
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)

# ★主线第51批 T3（P2-354）：框架主进程标识。
#   write_guard 用它区分「框架可写」与「普通脚本只读」
#   （见 nucleus/data/write_guard.py::is_framework_process）。
#   必须在任何可能触发写盘的 import 之前设置。
os.environ.setdefault("PULSE_FRAMEWORK", "1")

# ★主线往期批次 相关任务（P1）：退出确认交互的防呆常量。
#   背景：_confirm_apply_pending_on_quit 的 input() 在交互终端会无限阻塞，
#   若 stdin 判定为 TTY 但实际无输入（伪 TTY / 管道 / 期望脚本驱动），
#   进程将永久卡在退出路径，无法自愈也无法退出。
#   PULSE_QUIT_CONFIRM=0：非交互直接跳过确认（按「应用并重启」默认语义继续）。
#   PULSE_QUIT_TIMEOUT_SEC：input 等待上限（秒），超时按默认 Y 继续，0 表示沿用默认。
PULSE_QUIT_CONFIRM_ENV = "PULSE_QUIT_CONFIRM"
PULSE_QUIT_TIMEOUT_ENV = "PULSE_QUIT_TIMEOUT_SEC"
PULSE_QUIT_CONFIRM_DEFAULT_TIMEOUT = 30.0

import config

# -- 功能模块 --
from functions.function_loader import FunctionLoader
from nucleus.CompanionBridge import get_companion_bridge  # 新人类族群通信桥
from nucleus.const import LogLevel, PulseLayer, SystemEvent  # v9.5新增PulseLayer
from nucleus.field.InfoField import (
    InfoField,  # 全局信息场: 脉冲广播/条件匹配/历史查询（v9.5分层异步调度）
)
try:
    from nucleus.InsightBoard import (
        get_insight_board,  # 闭环间洞察共享黑板
    )
except Exception:
    # ★第81批 T7：导入兜底——确保模块级名字永远绑定。
    #   原裸导入在模块热重载等中间态下可能未绑定，导致
    #   UnboundLocalError: cannot access local variable 'get_insight_board'。
    #   兜底为始终返回 None 的 stub，配合 _safe_get_insight_board() 降级为空黑板。
    def get_insight_board():
        return None
from nucleus.logger import (
    get_module_logger,  # 模块级日志器（★主线第20批 T6）
    init_framework_logger,  # 框架日志器
)
# ★主线第20批 T6/P2-113：模块级 logger —— 补丁应用 / 自验证流程改用 logger 落盘
#   （原为 print()，只进控制台不进日志文件，事后无法追溯自动回退/自验证过程）
_logger = get_module_logger("main")

def _safe_get_insight_board():
    """★第81批 T7：安全获取洞察黑板单例。
    导入失败 / 名字未绑定 / 返回 None / 抛异常时统一返回 None，
    调用方据此降级为空黑板（不抛 UnboundLocalError）。仅在需要时惰性获取。
    """
    try:
        _b = get_insight_board
    except NameError as e:
        silent_exc(e, where="main::_safe_get_insight_board L103")
        return None
    if not callable(_b):
        return None
    try:
        return _b()
    except Exception as e:
        silent_exc(e, where="main::_safe_get_insight_board L109")
        return None

from nucleus.mnemosyne.ContextSnapshot import (
    get_context_snapshot,  # 上下文持久化: 对话记忆/推理链/搜索经验
)
from nucleus.mnemosyne.KnowledgeTree import KnowledgeTree  # 知识树: 五维空间坐标系
from nucleus.mnemosyne.PulseInstinctSnapshot import PulseInstinctSnapshot  # L4 本能快照
from nucleus.mnemosyne.PulseNode import PulseNode  # 知识节点: L1/L2/L3分级
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # 节点池: 热/温/冷三层管理
from nucleus.mnemosyne.PulseSnapshot import (
    PulseSnapshot,  # 快照持久化: 增量保存+历史轮转
)
from nucleus.organ_loader import OrganLoader  # P2-3: 插件化器官加载器
from nucleus.pulse.FrequencyCodec import FrequencyCodec  # 频率编解码: MD5+正弦映射

# ===== 脉冲场核心引擎层（六大支柱） =====
from nucleus.pulse.PulseCore import PulseCore  # 脉冲引擎: 创建/分发/幂等/超时熔断
from nucleus.qica.QICA import QICA  # QICA心智模型: Q→I→C→A四环节三通道
from nucleus.reasoning.AutonomousDeriver import (
    get_autonomous_deriver,  # 自主知识推导引擎
)

# ★第九批 B-3：置信度证据化——由「硬编码常数」改为
#   0.9 × 该类型历史成功率系数 × 证据强度系数（开关关闭时原值返回）
from nucleus.reasoning.SelfCalibrator import evidence_confidence as _evidence_conf
from nucleus.self_inspector import (
    get_self_inspector,  # ★v24.0新增：全局学习器依赖  # type: ignore[possibly-unbound]
)
from nucleus.synapsys.ResonanceEngine import (
    ResonanceEngine,  # 五维共振引擎: 记忆40%/空间30%/逻辑15%/时间10%/状态5%
)
from organs.body.PulseBloodVessel import PulseBloodVessel  # 血管: 场数据循环连通性监测

# ===== 仿生器官层（50个器官，9大系统） =====
# -- 核心脏器（6个） --
from organs.body.PulseHeart import PulseHeart  # 心脏: 脉冲调度/心跳维持
from organs.body.PulseKidney import PulseKidney  # 肾: 知识淘汰/L1/L2/L3分级清理
from organs.body.PulseLiver import PulseLiver  # 肝: L1→L2压缩/L2→L3融合/复盘压缩
from organs.body.PulseLung import PulseLung  # 肺: 模型池管理/智能模型选择
from organs.body.PulseStomach import PulseStomach  # 胃: 知识消化/词表驱动/五维归属
from organs.brain.PulseCodeLearner import PulseCodeLearner  # ★v16.0新增: 代码自主学习

# -- 大脑系统（7个） --
from organs.brain.PulseCortex import PulseCortex  # 大脑皮层: 意图识别/决策路由
from organs.brain.PulseInitiative import (
    PulseInitiative,  # 主动交互: 静默监测/亲密度驱动问候
)
from organs.brain.PulseInnerWorld import PulseInnerWorld  # 内在世界: 规则推理/知识检索
from organs.brain.PulseInterestModel import (
    PulseInterestModel,  # 兴趣模型: 12维兴趣光谱/错误驱动学习
)
from organs.brain.PulseReflection import (
    PulseReflection,  # 前额叶: 对话复盘/人格一致性校验
)
from organs.brain.PulseRiskPerception import (
    PulseRiskPerception,  # 风险感知: 五维风险预判
)
from organs.brain.PulseSemanticComprehension import (
    PulseSemanticComprehension,  # 语义理解器: 本地+大模型比对  
)
from organs.brain.PulseSpiritualCore import PulseSpiritualCore  # ★v16.0新增: 精神整合
from organs.brain.PulseSubconscious import (
    PulseSubconscious,  # 潜意识: 好奇心引擎/自主探索
)
from organs.core.PulseDeviceManager import (
    PulseDeviceManager,  # 设备管理器: 设备抽象/分配/健康监控 # noqa: F401
)
from organs.core.PulseEmergencyHandler import (
    PulseEmergencyHandler,  # 紧急处理: L3/L4响应/安全模式 # noqa: F401
)

# -- 核心系统（11个） --
from organs.core.PulseEnergyMetabolism import (
    PulseEnergyMetabolism,  # 能量代谢: 四维能力画像/能量水平 # noqa: F401
)
from organs.core.PulseGlobalLearner import (
    PulseGlobalLearner,  # 全局学习器: 全域自学习循环
)
from organs.core.PulseHardwareLauncher import (
    PulseHardwareLauncher,  # 硬件启动器: 硬件等级评估/器官加载计划 # noqa: F401
)
from organs.core.PulseHealthMonitor import (
    PulseHealthMonitor,  # 健康监控: 四级异常分级/告警触发  # noqa: F401
)
from organs.core.PulseInferenceEngine import (
    PulseInferenceEngine,  # 推理引擎: 统一推理接口/KV缓存 # noqa: F401
)
from organs.core.PulseMetricsCollector import (
    PulseMetricsCollector,  # 指标采集器: 分子/细胞/器官三级采集+可观测性 # noqa: F401
)
from organs.core.PulseMotivationCycle import (
    PulseMotivationCycle,  # 动机循环: 动机-压力-奖赏闭环
)
from organs.core.PulseProprioception import (
    PulseProprioception,  # 本体感知: 五维本体画像/升级路径建议 # noqa: F401
)
from organs.core.PulseSpinalCord import (
    PulseSpinalCord,  # 脊髓: 器官巡检/存活检测 # noqa: F401
)
from organs.core.PulseStressAxis import (
    PulseStressAxis,  # 应激轴: 交感激活/应激恢复 # noqa: F401
)
from organs.core.PulseSystemManager import (
    PulseSystemManager,  # 系统管理器: 服务自启停/依赖检测/环境自愈 # noqa: F401
)

# -- 内分泌系统（2个） --
from organs.endocrine.PulseHormones import (
    PulseHormones,  # 激素: 情绪检测/语气适配/主动关怀
)
from organs.endocrine.PulseNeurotransmitters import (
    PulseNeurotransmitters,  # 神经递质: 多巴胺/血清素/去甲肾上腺素等6种递质调节
)
from organs.genetic.PulseBonding import (
    PulseBonding,  # 情感羁绊: 关系记录/好感度管理 # noqa: F401
)
from organs.genetic.PulseConsent import PulseConsent  # 共同决策: 提议/接受/拒绝 # noqa: F401
from organs.genetic.PulseDNARepair import (
    PulseDNARepair,  # DNA修复: 错误修复/补丁生成/经验库 # noqa: F401
)

# -- 遗传系统（6个） --
from organs.genetic.PulseEvolution import (
    PulseEvolution,  # 进化: 影子实验/参数自搜索/自动回滚 # noqa: F401
)
from organs.genetic.PulseNurture import PulseNurture  # 养育: 成长阶段/教育传承 # noqa: F401
from organs.genetic.PulseReproductionEthics import (
    PulseReproductionEthics,  # 生育伦理: 繁衍前伦理审查 # noqa: F401
)
from organs.identity.PulseEthics import PulseEthics  # 伦理模块: 安全审查/价值冲突权衡
from organs.identity.PulseGrowth import PulseGrowth  # 成长模块: 能力评估/进化里程碑
from organs.identity.PulseNarrativeSelf import (
    PulseNarrativeSelf,  # 叙事自我: 动态价值观/表征危机检测
)
from organs.identity.PulsePersonalityKernel import (
    PulsePersonalityKernel,  # 人格内核: SHA256锁定/不可变锚点
)

# -- 身份系统（5个） --
from organs.identity.PulseSelfAwareness import (
    PulseSelfAwareness,  # 自我认知: 多维关系光谱v4.0
)
from organs.identity.PulseSpiritConstitution import (
    PulseSpiritConstitution,  # 精神宪法运行时: run_mode/校验/成熟度
)
from organs.immune.PulseBoneMarrow import PulseBoneMarrow  # 骨髓: 错误特征库生成 # noqa: F401
from organs.immune.PulseSkin import PulseSkin  # 皮肤: 补丁预检/安全沙箱 # noqa: F401
from organs.immune.PulseThymus import PulseThymus  # 胸腺: T细胞训练/策略优化 # noqa: F401
# -- 免疫系统（4个） --
from organs.immune.PulseWhiteCell import (
    PulseWhiteCell,  # 白细胞: 异常检测/自动修复/免疫记忆 # noqa: F401
)
from organs.motor.PulseCodeSandbox import (
    PulseCodeSandbox,  # 代码沙箱: 安全隔离执行/30项黑名单
)
from organs.motor.PulseController import PulseController  # 控制器器官创建与注入
from organs.motor.PulseFileDigester import (
    PulseFileDigester,  # 文件消化器: 77种格式全认知/分级路由
)
from organs.motor.PulseHands import PulseHands  # 双手: GPU/CPU动态任务调度
from organs.motor.PulseLegs import PulseLegs  # 双腿: 网络抓取/定向学习/熔断机制

# -- 运动系统（5个） --
from organs.motor.PulseMouth import PulseMouth  # 嘴巴: 三通路保活/人格过滤/回复生成
from organs.senses.PulseEars import PulseEars  # 耳朵: 意图识别/指代消解/代码块检测
from organs.senses.PulseEyes import PulseEyes  # 眼睛: 五维共振检索/知识搜索

# -- 感知系统（4个） --
from organs.senses.PulseTouch import PulseTouch  # 触觉: 硬件检测/CPU/内存/GPU快照
from organs.senses.PulseVisualCortex import (
    PulseVisualCortex,  # 视觉皮层: 图像帧分析/视觉记忆
)


class PulseFramework:
    """
    v9.5 脉冲框架（自进化基座版）
    
    管理所有核心模块（六大支柱）和仿生器官（50个）的生命周期。
    负责依赖注入、启动前置检查、器官注册、优雅退出。
    
    v9.5新增:
        - InfoField 分层异步调度线程池的优雅关闭
        - system.boot/system.stop 脉冲标记为 L0 生命线层
        - 启动日志展示分层调度状态
    """

    def __init__(self):
        # ===== 初始化框架专用日志器 =====
        self._logger = init_framework_logger()
        self._logger.info(f"初始化 {config.SYSTEM_NAME} {config.SYSTEM_VERSION}...")

        # ===== 运行状态标记 =====
        self._running = False

        # ===== 步骤1: 加载全局配置 =====
        self.config = config
        self._logger.info("配置加载完成")

        # ===== 步骤2: 初始化六大支柱模块 =====
        # PulseCore: 脉冲核心引擎（创建、分发、幂等保护、超时熔断）
        self.pulse_core = PulseCore(max_completed_fingerprints=config.PULSE["fingerprint_cache_size"])
        # FrequencyCodec: 频率编解码器（MD5+正弦映射，知识→频率签名）
        self.frequency_codec = FrequencyCodec()
        # InfoField: 全局信息场（v9.5: 分层异步调度版）
        self.info_field = InfoField(max_history=config.PULSE["info_field_max_history"])
        # ★登顶路线图-山 2：全局资源预算中枢（资源自主管理）
        # 注入负载探针 + 注册高耗能消费者；器官可通过 get_resource_budget().acquire() 主动节流。
        try:
            from nucleus.ResourceBudget import get_resource_budget, set_resource_budget
            _budget = get_resource_budget()
            set_resource_budget(_budget)
            # 负载探针：优先读 InfoField 硬件快照
            _budget._load_probe = (lambda: self.info_field.get_load_level() if hasattr(self, "info_field") else "light")
            # 注册高耗能消费者（权重表示单次执行资源占用）
            _budget.register_consumer("search", weight=4.0, desc="外部搜索/网页抓取")
            _budget.register_consumer("browser", weight=5.0, desc="浏览器操作/页面渲染")
            _budget.register_consumer("code_exec", weight=3.0, desc="代码沙箱/脚本执行")
            _budget.register_consumer("snapshot_io", weight=2.0, desc="快照保存/大文件IO")
            _budget.register_consumer("embedding", weight=3.0, desc="向量嵌入/模型推理")
            self._log(LogLevel.INFO, "全局资源预算中枢已启用（消费者: search/browser/code_exec/snapshot_io/embedding）")
            # ★v9.5：注入资源预算日志（拦截时 WARNING 进入后台）
            try:
                from nucleus.logger import get_module_logger
                _budget.set_logger(get_module_logger("ResourceBudget"))
            except Exception as _se:
                silent_exc(_se, "main.py:297")
        except Exception as _e:
            self._log(LogLevel.WARNING, f"资源预算中枢初始化失败（不影响主链路）: {_e}")
        # ResonanceEngine: 五维共振检索引擎
        self.resonance_engine = ResonanceEngine()
        # PulseNodePool: 知识节点池（热/温/冷三层管理）
        # ★P1: 优先从RUNTIME_PARAMS读取参数（支持热加载）
        _np_max_hot = config.RUNTIME_PARAMS.get("nodepool_max_hot", config.NODE_POOL["max_hot"])
        _np_max_warm = config.RUNTIME_PARAMS.get("nodepool_max_warm", config.NODE_POOL["max_warm"])
        self.node_pool = PulseNodePool(max_hot=_np_max_hot, max_warm=_np_max_warm)
        # ★登顶路线图-山 1 P1：启动记忆验证闭环定时调度
        # 周期性检验记忆活性（强化活跃/沉睡重要/清理过时），
        # daemon 线程不块塞主链路；如平台配置关闭则跳过。
        try:
            # ★主线第61批 T2/P1：函数内导入 random（main.py 无模块级 random，且规避 E402）
            import random as _rd61

            if config.FEATURE.get("use_memory_verification", True):
                _interval_h = config.FEATURE.get("memory_verification_interval_hours", 6)
                # ★主线第61批 T2/P1：随机首跑延迟（默认 5~30 分钟），避免与启动高峰期重叠。
                #   灰度：ENABLE_RANDOM_INITIAL_DELAY=False → 0（复现旧行为：首次即等一个完整周期）。
                _mv_delay_h = 0.0
                if bool(getattr(config, "ENABLE_RANDOM_INITIAL_DELAY", True)):
                    try:
                        _mv_delay_h = _rd61.uniform(
                            float(config.get_task_offset("memory_verify_initial_delay_min", 300)),
                            float(config.get_task_offset("memory_verify_initial_delay_max", 1800)),
                        ) / 3600.0
                    except Exception as _mv_e:
                        self._log(LogLevel.DEBUG, f"[记忆验证] 首跑延迟配置读取失败: {_mv_e}")
                self.node_pool._mem_verify_logger = lambda msg: self._log(LogLevel.INFO, msg)
                self.node_pool.start_memory_verification_loop(
                    interval_hours=float(_interval_h),
                    initial_delay_hours=_mv_delay_h)
                self._log(LogLevel.INFO,
                          f"[记忆验证] 闭环已启用（首跑推迟{_mv_delay_h * 3600:.0f}秒，"
                          f"每{_interval_h}小时自检）")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"记忆验证闭环启动失败（不影响主链路）: {_e}")
        # ★PHASE17-1.4：语义内核接线（向量 provider 注入 + 异步编码队列）
        #   开关关闭时 install() 内部直接返回，框架行为**零变化**；
        #   任何异常都会被吞掉并降级为关键词通道，不影响主链路。
        try:
            from nucleus.semantic.SemanticKernelService import (
                install as _install_semantic_kernel,
            )
            _sem = _install_semantic_kernel(self.resonance_engine, self.node_pool)
            if _sem.get("installed"):
                self._log(LogLevel.INFO,
                          f"[语义内核] 已接入：向量库 {_sem.get('vectors', 0)} 条，"
                          f"编码队列 {'运行中' if _sem.get('queue_running') else '未启动'}")
            else:
                self._log(LogLevel.DEBUG,
                          f"[语义内核] 未启用：{_sem.get('reason', '')}")
        except Exception as _e:
            self._log(LogLevel.WARNING, f"语义内核接入失败（不影响主链路）: {_e}")
        # ★第十批 任务3（2026-09-09）：向量库启动自检。
        #   检查生产库 model/dim 是否与出厂黄金值一致；若此前被测试污染，
        #   自动备份并清空（从空库启动，语义内核仍可用）。任何异常吞掉，不影响主链路。
        try:
            from nucleus.semantic.VectorStore import VectorStore
            VectorStore.get_instance().self_check_at_startup()
        except Exception as _e:
            self._log(LogLevel.WARNING, f"向量库启动自检异常（不影响主链路）: {_e}")
        # ★PHASE17-阶段二子任务3：极性判别层接线（破坏性操作拦截）
        #   开关关闭时直接跳过，框架行为零变化；异常吞掉降级为不拦截，不影响主链路。
        try:
            _pg_cfg = getattr(config, "POLARITY_GUARD_CONFIG", {}) or {}
            if _pg_cfg.get("enable", False):
                from nucleus.reasoning.PolarityGuard import (
                    DESTRUCTIVE_PATTERNS,
                    PolarityGuard,
                )
                _pg_patterns = list(DESTRUCTIVE_PATTERNS) + list(
                    _pg_cfg.get("extra_patterns", []) or [])
                _pg_guard = PolarityGuard(_pg_patterns)
                self.resonance_engine.set_polarity_guard(_pg_guard)
                self._log(LogLevel.INFO,
                          "[极性判别] 已接入共振引擎（破坏性操作拦截，"
                          f"{len(_pg_patterns)} 个模式）")
                # ★阶段二子任务4.3：PolarityGuard 同时作为第一个 rule_provider。
                #   需同时开启 enable_rule_channel 才注入规则通道（否则只做拦截）。
                _sk_cfg = getattr(config, "SEMANTIC_KERNEL_CONFIG", {}) or {}
                if _sk_cfg.get("enable_rule_channel", False):
                    from nucleus.reasoning.PolarityGuard import PolarityRuleProvider
                    self.resonance_engine.set_rule_provider(
                        PolarityRuleProvider(_pg_guard))
                    self._log(LogLevel.INFO,
                              "[规则通道] PolarityGuard 已作为 rule_provider 注入"
                              "（破坏性=0 / 正常=1）")
            else:
                self._log(LogLevel.DEBUG,
                          "[极性判别] 未启用（POLARITY_GUARD_CONFIG.enable=False）")
        except Exception as _e:
            self._log(LogLevel.WARNING, f"极性判别接入失败（不影响主链路）: {_e}")
        # ★PHASE17-阶段二子任务4.1：推理经验双写索引器接线
        #   不改 ReasoningExperience.py，只装配双写索引器（依赖注入 + 开关）。
        #   开关关闭时 record_with_index 退化为纯 JSON 写，零风险回退。
        try:
            from nucleus.reasoning.ReasoningExperienceIndexer import (
                get_reasoning_experience_indexer,
            )
            _rei_cfg = getattr(config, "REASONING_EXPERIENCE_INDEX_CONFIG", {}) or {}
            _rei = get_reasoning_experience_indexer()
            _rei.set_dependencies(node_pool=self.node_pool)
            # 向量库 + 编码队列：语义内核已装配时注入（供语义检索命中推理经验节点）
            try:
                from nucleus.semantic.VectorStore import get_vector_store
                _rei.set_dependencies(vector_store=get_vector_store())
            except Exception as _se:
                silent_exc(_se, "main.py:407")
            try:
                from nucleus.semantic.AsyncEncodeQueue import get_encode_queue
                _rei.set_dependencies(encode_queue=get_encode_queue())
            except Exception as _se:
                silent_exc(_se, "main.py:412")
            _rei.set_enabled(bool(_rei_cfg.get("enable_reasoning_double_write", False)))
            self._log(LogLevel.INFO if _rei.double_write_enabled else LogLevel.DEBUG,
                      f"[推理经验双写] {'已启用' if _rei.double_write_enabled else '未启用'}"
                      f"（enable_reasoning_double_write="
                      f"{_rei_cfg.get('enable_reasoning_double_write', False)}）")
        except Exception as _e:
            self._log(LogLevel.WARNING, f"推理经验双写接入失败（不影响主链路）: {_e}")
        # ★第一阶段P1：代码审查引擎 + 脚本执行引擎（自我进化能力基础设施）
        # CodeReviewEngine: 统一RUFF+Pyright审查，发现代码问题
        # ScriptExecutor: 脚本生成/验证/执行/管理，万能工具能力
        try:
            from nucleus.review import (
                get_code_analyzer,
                get_code_review_engine,
                get_environment_manager,
                get_script_executor,
                get_task_orchestrator,
                get_tool_installer,
                init_default_capabilities,
            )
            self.code_review_engine = get_code_review_engine()
            self.script_executor = get_script_executor()
            self.environment_manager = get_environment_manager()
            self.task_orchestrator = get_task_orchestrator("framework")
            self.code_analyzer = get_code_analyzer()
            self.tool_installer = get_tool_installer()
            # 能力无上限架构：统一编排6大能力
            self.capability_framework = init_default_capabilities()
            self._log(LogLevel.INFO, "自我进化能力体系已初始化（能力无上限架构+6大核心能力）")
            # ★P1: 启动参数补丁管理器后台自动应用线程
            try:
                from nucleus.evolution.ParamPatchManager import get_param_patch_manager
                self.param_patch_manager = get_param_patch_manager()
                # ★主线第61批 T2/P1：随机首跑延迟（默认 1~4 分钟），避免「立即首跑」
                #   恰与第30次心跳（4 个任务重叠）撞车。关闭开关 → 0（复现旧行为）。
                _pp_delay = 0.0
                if bool(getattr(config, "ENABLE_RANDOM_INITIAL_DELAY", True)):
                    try:
                        _pp_delay = _rd61.uniform(
                            float(config.get_task_offset("param_patch_initial_delay_min", 60)),
                            float(config.get_task_offset("param_patch_initial_delay_max", 240)),
                        )
                    except Exception as _pp_e:
                        self._log(LogLevel.DEBUG, f"[参数补丁] 首跑延迟配置读取失败: {_pp_e}")
                self.param_patch_manager.start_auto_apply(interval=300, initial_delay=_pp_delay)
                self._log(LogLevel.INFO,
                          f"[参数补丁] 自动应用线程已启动（首跑延迟{_pp_delay:.0f}秒，每5分钟检查）")
            except Exception as _ppe:
                self._log(LogLevel.WARNING, f"参数补丁管理器启动失败: {_ppe}")
                self.param_patch_manager = None
        except Exception as _e:
            self._log(LogLevel.WARNING, f"自我进化能力体系初始化失败（不影响主链路）: {_e}")
            self.code_review_engine = None
            self.script_executor = None
            self.environment_manager = None
            self.task_orchestrator = None
            self.code_analyzer = None
            self.tool_installer = None
            self.capability_framework = None
        # ★health_ui独立进程第一步：运行时状态写入器（每2秒把内存数据汇总到data/runtime_state.json）
        # 零风险：异常全部吞掉，不影响主流程；低开销：后台daemon线程，数据量<100KB
        try:
            from functions.runtime_state_writer import start_runtime_state_writer
            self.runtime_state_writer = start_runtime_state_writer(interval=2.0)
            self._log(LogLevel.INFO, "运行时状态写入器已启动（每2秒写入data/runtime_state.json，供独立监控进程读取）")
        except Exception as _rse:
            self._log(LogLevel.DEBUG, f"运行时状态写入器启动失败（不影响主链路）: {_rse}")
            self.runtime_state_writer = None
        # ★阶段三子任务1：TimeCore时间中枢启动（受ENABLE_TIME_CORE开关保护）
        # 内部协作者设计：内核单例+独立daemon定时器，每60s经InfoField广播time.tick
        # 零风险：开关False时直接return不建线程；异常吞掉不影响主流程
        try:
            from nucleus.chronos.TimeCore import get_time_core
            self.time_core = get_time_core()
            self.time_core.start()
            if self.time_core.is_enabled():
                self._log(LogLevel.INFO, "TimeCore时间中枢已启动（每60s广播time.tick）")
        except Exception as _tce:
            self._log(LogLevel.DEBUG, f"TimeCore启动失败（不影响主链路）: {_tce}")
            self.time_core = None
        # ★登顶路线图-山 3 P1：真正意图自主生成（daemon 线程）
        # 综合价值偏好/体验动机/状态信号，周期性生成框架自己的意图，
        # 写入体验池沉淀为自主体验。零冲突：不改任何现有器官逻辑；
        # 压力均衡：随机错峰首跑 + 节流 2700s（与现有周期任务错开）。
        try:
            if config.FEATURE.get("use_autonomous_intent", True):
                import threading as _th

                from nucleus.IntentGenerator import get_intent_generator
                from nucleus.mnemosyne.experience_pool import get_experience_pool
                from nucleus.ValuePreference import get_value_preference
                _intent_ig = get_intent_generator()

                def _intent_value_provider():
                    _vp = get_value_preference()
                    _dom = _vp.get_dominant()
                    return _dom

                def _intent_exp_provider():
                    try:
                        _pool = get_experience_pool()
                        return _pool.query_experiences(limit=10)
                    except Exception:
                        return []

                def _intent_state_provider():
                    try:
                        _curiosity = float(getattr(self, "curiosity_level", 0.3) or 0.3)
                        return {"curiosity": _curiosity}
                    except Exception:
                        return {"curiosity": 0.3}

                _intent_ig._value_provider = _intent_value_provider
                _intent_ig._experience_provider = _intent_exp_provider
                _intent_ig._state_provider = _intent_state_provider
                _intent_ig._min_interval = 2700.0

                def _autonomous_intent_loop():
                    # 随机错峰首跑（1-15 分钟），避开启动峰值
                    import random as _rd
                    import time as _tm

                    # ★B3【P2】待处理意图队列（内存 deque，maxlen=50）：
                    #   只记录不执行，为后续「自主意图→任务」积累数据。
                    from collections import deque as _deque
                    _pending_intent_queue = _deque(maxlen=50)
                    _tm.sleep(_rd.uniform(60, 900))
                    while True:
                        try:
                            _intent = _intent_ig.generate()
                            if _intent:
                                _pool = get_experience_pool()
                                _pool.record_experience(
                                    motivation=_intent["intent_text"],
                                    motivation_intensity=float(_intent["confidence"]),
                                    reward_type="cognitive",  # 白名单内: achievement/cognitive/connection
                                    reward_intensity=float(_intent["confidence"]) * 0.5,
                                    content=f"自主意图: {_intent['intent_text']}",
                                )
                                # ★B3【P2】记录到待处理意图队列（只记录，不注入任务执行链路）
                                _pending_intent_queue.append({
                                    "intent_id": _intent.get("intent_id", ""),
                                    "intent_text": _intent["intent_text"],
                                    "confidence": _intent["confidence"],
                                    "source": _intent.get("source", {}),
                                    "ts": _intent.get("timestamp", _tm.time()),
                                })
                                self._log(LogLevel.INFO, f"自主意图生成: {_intent['intent_text']} (置信 {_intent['confidence']})")
                                self._log(LogLevel.INFO, f"[B3意图队列] 待处理意图数={len(_pending_intent_queue)}（仅记录，不执行）")
                        except Exception as _e2:
                            self._log(LogLevel.DEBUG, f"自主意图生成异常: {_e2}")
                        _tm.sleep(2700.0)

                _intent_thread = _th.Thread(target=_autonomous_intent_loop, name="IntentGenerator", daemon=True)
                _intent_thread.start()
                self._log(LogLevel.INFO, "自主意图生成已启用（每45分钟一次，错峰运行）")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"自主意图生成启动失败（不影响主链路）: {_e}")
        # ★阶段C'新增：外置索引磁盘化（开关 use_external_index 开启时注入 IndexStore）
        # 默认关闭，零侵入；开启后 node_pool 维护磁盘索引副本，不改查询逻辑。
        if config.FEATURE.get("use_external_index", False):
            try:
                from nucleus.mnemosyne.IndexStore import IndexStore
                _index_dir = os.path.join(
                    os.path.dirname(config.SNAPSHOT["path"]) or "data/knowledge",
                    "index",
                )
                self.index_store = IndexStore(index_dir=_index_dir)
                self.node_pool.set_index_store(self.index_store)
                self._log(LogLevel.INFO, f"外置索引磁盘化已启用（{_index_dir}）")
            except Exception as _e:
                self._log(LogLevel.WARNING, f"外置索引初始化失败，降级为全内存索引: {_e}")
                self.index_store = None
        # ★阶段B'新增：L1 冷存储分治（开关 use_cold_storage 开启时启用冷池驱逐）
        # 默认关闭，零侵入；开启后冷池超限驱逐到磁盘冷存，召回时提升温池。
        if config.FEATURE.get("use_cold_storage", False):
            _cold_dir = os.path.join(
                os.path.dirname(config.SNAPSHOT["path"]) or "data/knowledge",
                "cold",
            )
            self.node_pool.set_cold_storage(
                enabled=True,
                max_cold_cache=config.RUNTIME_PARAMS.get("nodepool_max_cold_cache",
                                                         config.NODE_POOL.get("max_cold_cache", 5000)),
                cold_dir=_cold_dir,
            )
            self._log(LogLevel.INFO, f"L1 冷存储分治已启用（冷缓存上限 {config.NODE_POOL.get('max_cold_cache', 5000)}，冷存目录 {_cold_dir}）")
        # PulseSnapshot: 脉冲快照（增量保存+历史轮转）
        self.snapshot = PulseSnapshot(snapshot_path=config.SNAPSHOT["path"])
        # PulseInstinctSnapshot: 本能快照（启动时全量加载）
        _instinct_path = config.INSTINCT.get("snapshot_path", "data/knowledge/pulse_instinct_snapshot.json")
        self.instinct_snapshot = PulseInstinctSnapshot(snapshot_path=_instinct_path)
        self.instinct_snapshot.set_node_pool(self.node_pool)
        # KnowledgeTree: 知识树（五维空间坐标系）
        self.knowledge_tree = KnowledgeTree()
        # ★v23.0新增：梯度追踪器——激活v10预埋的场梯度感知能力
        from nucleus.field.GradientTracker import GradientTracker
        self.gradient_tracker = GradientTracker(window_size=120, sample_interval_seconds=10.0)
        self.gradient_tracker.enable()  # 从预埋状态激活
        
        # ★v24.0新增：体验记忆池——与知识库物理隔离
        from nucleus.mnemosyne.experience_pool import get_experience_pool
        self.experience_pool = get_experience_pool()
        # ★主线第50批 T1（P0-1）：启动时注册 ReportBus 内置消费者。
        #   ★这是 P0-1「自认知闭环断裂」的关键一环：此前 ReportBus
        #   只有骨架、无订阅者注册 → 发布的报告永远无人消费（消费率 0%）。
        #   注册失败不得影响启动（全包 try/except + 日志）。
        try:
            import config as _m50_cfg
            if getattr(_m50_cfg, "ENABLE_REPORT_BUS", True):
                from nucleus.reporting import get_report_bus as _m50_gb
                from nucleus.reporting.consumers import (
                    register_builtin_consumers as _m50_reg)
                _m50_types = _m50_reg(_m50_gb())
                self._log(LogLevel.INFO,
                          "自认知报告总线已启用，已注册消费者: %s" % _m50_types)
        except Exception as _m50_re:
            try:
                self._log(LogLevel.WARNING,
                          "自认知报告总线注册失败（已忽略）: %s" % type(_m50_re).__name__)
            except Exception as _se:
                silent_exc(_se, "main.py:634")
        self._log(LogLevel.INFO, "体验记忆池已初始化")
        
        # ★v23.0新增：振荡场监视器——衔接OscillonField与GradientTracker
        from nucleus.field.OscillonField import FrequencyPhaseLock, OscillonField
        
        # 创建最小具体实现——振荡场监视器
        class _FieldStatusMonitor(OscillonField):
            def propagate(self, signal):
                return []
            def register_node(self, node_id, resonant_frequencies=None):
                self._nodes[node_id] = resonant_frequencies or []
                return True
            def unregister_node(self, node_id):
                return self._nodes.pop(node_id, None) is not None
        
        self.oscillon_monitor = _FieldStatusMonitor(field_name="pulse_net_monitor")
        self.gradient_tracker.set_oscillon_monitor(self.oscillon_monitor)
        
        # ★v23.0新增：赫布学习器——激活"一起激活的节点连在一起"
        from nucleus.genesis.HebbianLearner import HebbianLearner
        self.hebbian_learner = HebbianLearner()
        self.hebbian_learner.enable()  # 从预埋状态激活
        # 注入到快照管理器
        if hasattr(self.snapshot, 'set_hebbian_learner'):
            self.snapshot.set_hebbian_learner(self.hebbian_learner)
        # ★v23.0新增：注入到节点池
        if hasattr(self.node_pool, 'set_hebbian_learner'):
            self.node_pool.set_hebbian_learner(self.hebbian_learner)
        self._log(LogLevel.INFO, "赫布学习器已激活 (共现窗口=60s, 学习率=0.15)")
        
        # ★FIX: 事件流挖掘器——发现重复/新兴/周期性事件模式
        from nucleus.genesis.StreamMiner import get_stream_miner
        self.stream_miner = get_stream_miner()
        self.stream_miner.enable()
        if hasattr(self.info_field, 'set_stream_miner'):
            self.info_field.set_stream_miner(self.stream_miner)
        self._log(LogLevel.INFO, "事件流挖掘器已激活 (窗口=100, 基线=500)")
        
        # 频率-相位锁定器——注入共振引擎，为未来自组织协同奠定基础
        self.phase_lock = FrequencyPhaseLock(lock_threshold=0.1)
        if hasattr(self.resonance_engine, 'set_phase_lock'):
            self.resonance_engine.set_phase_lock(self.phase_lock)
        
        self._log(LogLevel.INFO, "振荡场监视器已激活 (场状态追踪+频率相位锁定)")

        # ===== 步骤3: 核心模块间依赖注入 =====
        # PulseCore ↔ InfoField 双向绑定
        self.pulse_core.set_info_field(self.info_field)
        self.info_field.set_pulse_core(self.pulse_core)
        # ResonanceEngine 注入节点池和信息场
        self.resonance_engine.set_node_pool(self.node_pool)
        self.resonance_engine.set_info_field(self.info_field)
        self.info_field.set_resonance_engine(self.resonance_engine)
        self.node_pool.set_resonance_engine(self.resonance_engine)
        # PulseSnapshot 注入节点池和共振引擎
        self.snapshot.set_node_pool(self.node_pool)
        # ★第81批补 T2：显式再绑定冷召回源双保险（与 set_node_pool 内自动绑定不冲突）
        if hasattr(self.snapshot, "set_cold_recall_source") and hasattr(self.node_pool, "recall_cold_nodes_batch"):
            self.snapshot.set_cold_recall_source(self.node_pool.recall_cold_nodes_batch)
        self.snapshot.set_resonance_engine(self.resonance_engine)

        # 器官注册表（name → organ instance）
        self.organs = {}

        # 功能模块加载器（v9.5: 在 __init__ 中预置为 None，start() 后由 main() 赋值）
        self.function_loader = None

        # ===== 步骤4: 启动前置检查（P0-3） =====
        self._pre_check()

        # ===== 步骤5: 初始化所有器官（手动创建 + 插件化自动加载） =====
        self._init_organs()

        self._logger.info("核心模块初始化完成，依赖注入完成")

    # ========================================================================
    # P0-3: 启动前置检查
    # ========================================================================

    def _pre_check(self):
        """启动前置检查（P0-3）"""
        self._log(LogLevel.INFO, "执行启动前置检查...")
        issues = []

        # 1. 配置项合法性校验
        if config.PULSE.get("heartbeat_interval_seconds", 30) <= 0:
            issues.append("心跳间隔必须 > 0")
        if not config.SNAPSHOT.get("path", ""):
            issues.append("快照路径未配置")

        # 2. 关键目录存在性检查
        for dir_key, default_path in [
            ("snapshot", config.SNAPSHOT.get("path", "data/")),
        ]:
            dir_path = os.path.dirname(default_path) if default_path.endswith(".json") else default_path
            if dir_path and not os.path.exists(dir_path):
                try:
                    os.makedirs(dir_path, exist_ok=True)
                    self._log(LogLevel.INFO, f"[前置检查] 自动创建目录: {dir_path}")
                except Exception as e:
                    issues.append(f"无法创建目录 {dir_path}: {e}")

        # 3. 核心模块实例非空校验
        if not self.pulse_core:
            issues.append("PulseCore 初始化失败")
        if not self.info_field:
            issues.append("InfoField 初始化失败")
        if not self.node_pool:
            issues.append("PulseNodePool 初始化失败")

        # 4. 可选依赖检测
        try:
            import cv2  # noqa: F401
            self._has_cv2 = True
        except ImportError:
            self._has_cv2 = False
            self._log(LogLevel.WARNING, "[前置检查] OpenCV 未安装，视觉皮层将使用文件元信息模式")

        try:
            import pyaudio  # noqa: F401
            self._has_pyaudio = True
        except ImportError:
            self._has_pyaudio = False
            self._log(LogLevel.WARNING, "[前置检查] pyaudio 未安装，音频设备检测将降级")

        # 5. 关键器官类可用性验证
        essential_organs = [
            ("心脏", "PulseHeart"),
            ("大脑皮层", "PulseCortex"),
            ("胃", "PulseStomach"),
            ("内在世界", "PulseInnerWorld"),
        ]
        for name, class_name in essential_organs:
            try:
                cls = globals().get(class_name)
                if cls is None:
                    issues.append(f"关键器官 {name}({class_name}) 未导入")
            except Exception as e:
                issues.append(f"关键器官 {name} 类检查失败: {e}")

        # 汇总结果
        fatal_issues = [i for i in issues if "失败" in i or "未导入" in i or "未配置" in i or "无法创建" in i]
        if fatal_issues:
            self._log(LogLevel.ERROR, f"发现 {len(fatal_issues)} 个致命问题:")
            for i in fatal_issues:
                self._log(LogLevel.ERROR, f"  - {i}")
            raise RuntimeError("前置检查失败，无法启动。请修复以上问题后重试。")
        elif issues:
            self._log(LogLevel.WARNING, f"发现 {len(issues)} 个警告:")
            for i in issues:
                self._log(LogLevel.WARNING, f"  - {i}")
            self._log(LogLevel.INFO, "继续启动...")
        else:
            self._log(LogLevel.INFO, "✅ 全部通过")

    # ========================================================================
    # P0-1: 通用器官创建方法
    # ========================================================================

    def create_organ(self, organ_class, name, **extra_deps):
        """公开器官创建契约（规则14：供声明式装配器/加载器调用）"""
        return self._create_organ(organ_class, name, **extra_deps)

    def _create_organ(self, organ_class, name, **extra_deps):
        """
        通用器官创建方法（P0-1）。
        
        自动注入 info_field + pulse_core，然后注入额外依赖，存入 organs 字典。

        ★第158批 _create_organ 退役（实测口径，2026-10-03）：本方法**不是**待退役的
          硬编码装配残留，而是**声明式装配路径的底层创建原语**，全仓唯一创建实现。
          证据：OrganAssembler.assemble()（nucleus/organ_assembler.py:420）与
          OrganLoader（nucleus/organ_loader.py:194）均经 framework.create_organ()
          转发至本方法；实测 56/56 声明器官经此创建成功。
          因此「退役」的真实对象不是本方法，而是 legacy 硬编码段
          _init_organs_legacy()（main.py:1344）——在 FEATURE
          ['use_declarative_assembly']=True（config.py:1827）下已不执行。
          保留本方法为声明式唯一创建原语；legacy 段的停用标注见其 def 处。
        """
        organ = organ_class(name)
        organ.set_info_field(self.info_field)
        organ.set_pulse_core(self.pulse_core)
        for attr_name, value in extra_deps.items():
            setter = getattr(organ, f"set_{attr_name}", None)
            if setter:
                setter(value)
        self.organs[name] = organ
        return organ

    # ========================================================================
    # 器官初始化
    # ========================================================================

    def _init_organs(self):
        """初始化所有器官（P3-2阶段3：默认硬编码，可用 FEATURE 开关切换声明式装配）"""
        FEATURE = self.config.FEATURE
        # ★一期（硬件自适应）：硬件 tier 驱动器官降级（开关默认 False，保守灰度）
        # 装配前独立探测硬件 tier，低配环境自动关闭非核心器官，实现「低配保命、高配全开」。
        if FEATURE.get("use_hardware_tier_degradation", False):
            try:
                from nucleus.hardware_probe import (
                    compute_degraded_feature,
                    detect_hardware_tier,
                )
                _hw = detect_hardware_tier()
                _tier = _hw.get("tier", "high")
                _degraded = compute_degraded_feature(FEATURE, _tier)
                # ★PHASE14-闭环修复：原实现只在「发生降级」时打日志，tier=high
                #   （不降级）时完全无痕——内部协作者无法从日志确认家底到底被判成哪一档，
                #   闭环无从验证。改为无论是否降级都留痕（启动期仅 1 次，无 IO 压力）。
                self._log(LogLevel.INFO,
                          f"硬件 tier 评估: tier={_tier} (评分={_hw.get('score')} "
                          f"核心={_hw.get('cores')} 内存={_hw.get('memory_gb')}GB "
                          f"GPU={'有' if _hw.get('has_gpu') else '无'}) "
                          f"→ {'全功能装配' if _degraded == FEATURE else '按档降级装配'}")
                if _degraded != FEATURE:
                    # 有降级发生：用降级后的 FEATURE 替换本次装配使用的配置视图
                    self._log(LogLevel.INFO,
                              f"硬件 tier 降级生效: tier={_tier} (核心={_hw.get('cores')} 内存={_hw.get('memory_gb')}GB GPU={'有' if _hw.get('has_gpu') else '无'})，"
                              f"已关闭非核心器官")
                    # 临时替换 self.config.FEATURE 为降级视图（仅影响本次装配）
                    _orig_feature = self.config.FEATURE
                    self.config.FEATURE = _degraded
                    try:
                        self._init_organs_with_feature()
                    finally:
                        self.config.FEATURE = _orig_feature
                    return
            except Exception as _e:
                self._log(LogLevel.WARNING, f"硬件 tier 降级评估失败，回退全量装配: {_e}")

        # ★v9.5打通硬件断链：深度探测结果喂给并行调度器（硬件自适应并行/异步）
        try:
            from nucleus.hardware_probe import detect_hardware_tier
            _hw = detect_hardware_tier()
            _deep = _hw.get("deep", {}) or {}
            from nucleus.parallel_scheduler import get_parallel_scheduler
            get_parallel_scheduler().update_hardware_capability(
                tier=_hw.get("tier"),
                cores=_hw.get("cores"),
                physical_cores=_deep.get("physical_cores"),
                memory_usage_pct=_deep.get("mem_usage_pct"),
                gpu=_deep.get("gpu"),
            )
            _gpu_desc = _deep.get("gpu", {}).get("model", "无")
            _gpu_mem = _deep.get("gpu", {}).get("memory_free_mb", 0)
            self._log(LogLevel.INFO,
                      f"硬件深度自适应: 物理核{_deep.get('physical_cores')} "
                      f"内存余量{_deep.get('mem_available_gb')}GB "
                      f"GPU={_gpu_desc}空闲显存{_gpu_mem}MB")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"硬件深度自适应注入跳过: {_e}")

        # ★G2（v9.5-GPU共享内存利用）：GPUCore 精确探测 GPU 计算资源并进后台日志。
        # torch 真探测（显存/算力/共享内存口径），供并行调度器与未来向量计算使用。
        try:
            from nucleus.GPUCore import get_gpu_core
            _gpu_info = get_gpu_core().probe_gpu(force=True)
            if _gpu_info.get("available"):
                self._log(LogLevel.INFO,
                          f"GPU计算加速: {_gpu_info.get('model')} "
                          f"算力{_gpu_info.get('compute_cap')} "
                          f"显存{_gpu_info.get('vram_total_mb')}MB/"
                          f"空闲{_gpu_info.get('vram_free_mb')}MB/"
                          f"占用{_gpu_info.get('vram_used_pct')}% "
                          f"torch={_gpu_info.get('torch_version')}"
                          + (f" 共享内存参考{_gpu_info.get('shared_hint_mb')}MB"
                             if _gpu_info.get('shared_hint_mb') else ""))
                # ★PHASE14：GPU 能力自检说明。
                #   内部协作者实测困惑：「日志说有 GPU，任务管理器却纹丝不动」。
                #   真相是框架当前的向量检索热点规模太小（意图匹配 4 维 × 数十条），
                #   送进 GPU 反而被 PCIe 传输 + kernel launch 固定开销拖垮（预计慢 20~100 倍）。
                #   所以「不用 GPU」是正确行为，不该静默——这里显式说明触发条件，
                #   免得每次都被当成 bug 重新排查一遍。
                try:
                    import config as _gcfg
                    _gs = getattr(_gcfg, "GPU_VECTOR_SEARCH", {}) or {}
                    self._log(
                        LogLevel.INFO,
                        f"GPU向量检索: 能力已就绪，达阈值时自动启用"
                        f"（需候选≥{_gs.get('min_candidates', 2000)}条 且 "
                        f"维度≥{_gs.get('min_dim', 64)}维）；"
                        f"当前业务热点规模远小于此，走 CPU/Cython 属预期行为。"
                        f"若需实测显卡加速效果，运行 python verify_gpu_bench.py"
                        + ("" if _gs.get("enabled", True) else "（当前已在 config 中关闭）"))
                except Exception as _se:
                    silent_exc(_se, "main.py:908")
            else:
                self._log(LogLevel.DEBUG, "GPU计算加速不可用（torch探测），回退CPU")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"GPU计算加速探测跳过: {_e}")

        # ★PHASE14：Cython 扩展加载状态自检（内部协作者已点头）。
        #   此前各模块自己 try/except 静默回退，性能差 3 倍却无提示——
        #   连我都曾据沙箱日志误判为"未编译"。现在启动即明确告知，
        #   并区分「开关关闭」与「真的没编译」两种完全不同的情况。
        #   放在装配前：此时 import 成功会进 sys.modules，后续使用零额外开销。
        try:
            from nucleus.cython_status import (
                format_cython_report,
                probe_cython_extensions,
            )
            _cy = probe_cython_extensions(do_import=True)
            _cy_report = format_cython_report(_cy)
            self._log(LogLevel.INFO
                      if _cy["all_loaded"] else LogLevel.WARNING, _cy_report)
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"Cython扩展自检跳过: {_e}")

        self._init_organs_with_feature()

        # ===== ★主线第17批 T5/P2-63：EventTap 旁路监听器初始化 =====
        #   所有器官装配完成后初始化；失败只记 WARNING，不影响框架启动。
        self._init_event_tap()

        # ===== ★主线第18批 T4/P0：自我认知引擎初始化（PHASE18 阶段一）=====
        #   位置同 EventTap：器官装配完成后；失败只记 WARNING，不影响启动。
        #   注意：只注册分析器，不触发分析（避免拖慢启动）。
        self._init_self_awareness()

        # ===== R4阶段二：存续编排器接入主链路 =====
        # 在所有器官装配完成后，统一装配 SurvivalOrchestrator（兼容 legacy/declarative 双路径）。
        self._wire_survival_orchestrator()

    def _init_event_tap(self):
        """★主线第17批 T5/P2-63：初始化 EventTap 旁路监听器。

        最小侵入：仅在器官装配完成后触发单例创建与订阅（`**` 全部事件），
        初始化失败只记 WARNING，绝不影响框架启动。
        """
        try:
            from nucleus.events.EventTap import get_event_tap
            _tap = get_event_tap()
            if _tap is not None and _tap.started:
                self._log(LogLevel.INFO,
                          "[EventTap] 旁路监听器已启动，订阅 ** 全部事件")
            else:
                self._log(LogLevel.WARNING,
                          "[EventTap] 旁路监听器未订阅（可能被开关或异常影响）")
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[EventTap] 旁路监听器初始化失败（不影响启动）: "
                      f"{type(_e).__name__}: {_e}")

    def _init_self_awareness(self):
        """★主线第18批 T4/P0：初始化自我认知引擎（PHASE18 阶段一核心）。

        最小入侵：创建单例并注册两个**静态**分析器（产出-消费配对 / 虚假闭环检测），
        **不自动触发分析**（ast 全项目扫描约 4 秒，启动期不宜阻塞）。
        函数内导入以规避 main.py 存量 E402；初始化失败只记 WARNING。
        """
        try:
            from nucleus.self_awareness import get_self_awareness_engine
            from nucleus.self_awareness.FakeLoopDetector import analyze_fake_loops
            from nucleus.self_awareness.ProductionConsumptionMatcher import (
                analyze_production_consumption,
            )

            _engine = get_self_awareness_engine()
            _engine.register_analyzer(
                "production_consumption", analyze_production_consumption,
                "production_consumption", replace=True)
            _engine.register_analyzer(
                "fake_loops", analyze_fake_loops, "fake_loops", replace=True)
            # ★主线第19批 T3/T4：整合现有分析模块（"整合而非替代"）
            #   注意：分析器签名是 (engine)->dict，而 integrate_* 是绑定方法
            #   ()->dict，故用 lambda 适配；两个开关分别控制，关闭即空 dict。
            _engine.register_analyzer(
                "log_analyzer", lambda _e: _e.integrate_log_analyzer(),
                "runtime_health", replace=True)
            _engine.register_analyzer(
                "code_review", lambda _e: _e.integrate_code_review(),
                "code_health", replace=True)
            # ★主线第36批 T1：补齐两维分析器注册（阶段二已实现但**未接线**）。
            #   ★主线第77批摸底更正：该缺口已于下方接线完成
            #     （call_graph / knowledge_quality 两个 register_analyzer），
            #     「未接线」表述已过期；保留原文仅作历史追溯，
            #     后续摸底勿据此判定为未落地。
            #   跨文件调用图（第21批）/ 知识质量（第23批）此前**只**由手动脚本
            #   tools/run_self_awareness_analysis.py 注册 → 引擎在框架内缺这两维
            #   （端到端实测 profile.call_graph_health / knowledge_health 为空）。
            #   注册本身**零开销**（不触发扫描），执行仍完全由 run_all_analyses 决定，
            #   故不影响启动耗时与主对话流程。
            _engine.register_analyzer(
                "call_graph", lambda _e: _e.integrate_call_graph(),
                "call_graph_health", replace=True)
            _engine.register_analyzer(
                "knowledge_quality", lambda _e: _e.integrate_knowledge_quality(),
                "knowledge_health", replace=True)
            # ★主线第38批 T2（P2-214）：evolution_health 维度接线。
            #   设计 6 维中该维**此前无任何分析器 → 端到端恒空**（第36批 T1 缺口表征）；
            #   本批接入 SafeEvolutionExecutor 的**只读**统计（不触发 verify/rollback）。
            #   注册本身零开销，执行仍由 run_all_analyses 决定。
            # _m38_t2_wired
            _engine.register_analyzer(
                "evolution_health", lambda _e: _e.integrate_evolution_health(),
                "evolution_health", replace=True)
            self._log(LogLevel.INFO, "[SelfAwareness] 自我认知引擎已初始化")
            # ★主线第37批 T1（P2-210）：启动每日低频调度（让引擎真正"跑起来"）
            #   函数内导入以规避 main.py 存量 E402；
            #   daemon 线程随进程退出（os._exit 跳过 atexit），无需显式清理；
            #   失败只记 WARNING，不影响框架启动。
            # _m37_t1_scheduler_wired
            try:
                from nucleus.self_awareness.DailyScheduler import start_daily_schedule
                _sched_ok = start_daily_schedule()
                self._log(LogLevel.INFO,
                          f"[SelfAwareness] 每日调度"
                          f"{'已启动' if _sched_ok else '未启动（开关关闭或已在运行）'}")
            except Exception as _se:
                self._log(LogLevel.WARNING,
                          f"[SelfAwareness] 每日调度启动失败（不影响启动）: "
                          f"{type(_se).__name__}: {_se}")
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[SelfAwareness] 自我认知引擎初始化失败（不影响启动）: "
                      f"{type(_e).__name__}: {_e}")

    def _wire_survival_orchestrator(self):
        """★R4阶段二：装配存续编排器（任务#40）。

        职责：创建单例编排器 → 注入存续状态提供函数 → 注册五层次代表器官。
        心跳驱动在 PulseHeart._trigger_heartbeat 中调用 orchestrator.on_heartbeat(beat_count)。

        五层次映射（定稿）：
            生命层     → self.self_awareness   (PulseSelfAwareness)
            协调层     → self.controller       (PulseController)
            智慧层     → self.legs             (PulseLegs)
            创造层     → self.code_learner     (PulseCodeLearner)
            精神层     → self.spirit_constitution (PulseSpiritConstitution)

        原则：最小侵入。总开关 config.FEATURE["survival_orchestrator_enabled"] 默认 False（灰度控制），
        关闭时仅装配单例但不执行任何动作，零行为改变。
        """
        try:
            from nucleus.SurvivalOrchestrator import get_survival_orchestrator

            orchestrator = get_survival_orchestrator()

            # 注入存续状态提供函数（读 get_existential_state）
            _awareness = getattr(self, "self_awareness", None)
            if _awareness is not None and hasattr(_awareness, "get_existential_state"):
                orchestrator.set_existential_provider(_awareness.get_existential_state)

            # 注册五层次代表器官（存在且实现钩子才注册，缺失则跳过不影响其它层）
            _layer_map = {
                "life": getattr(self, "self_awareness", None),
                "coordination": getattr(self, "controller", None),
                "wisdom": getattr(self, "legs", None),
                "creation": getattr(self, "code_learner", None),
                "spirit": getattr(self, "spirit_constitution", None),
            }
            _registered = 0
            for _layer, _organ in _layer_map.items():
                if _organ is not None and hasattr(_organ, "on_survival_low") \
                        and hasattr(_organ, "on_survival_high"):
                    orchestrator.register_layer(_layer, _organ)
                    _registered += 1

            self.survival_orchestrator = orchestrator
            # ★R4阶段二治理：开关唯一真源 = config.FEATURE["survival_orchestrator_enabled"]
            _enabled = bool(self.config.FEATURE.get("survival_orchestrator_enabled", False))

            # 注入到心脏，驱动心跳检测（每5拍检测一次）
            _heart = getattr(self, "heart", None)
            if _heart is not None and hasattr(_heart, "set_survival_orchestrator"):
                _heart.set_survival_orchestrator(orchestrator)

            self._log(LogLevel.INFO,
                      f"[R4阶段二] 存续编排器已装配：注册{_registered}/5个层次代表器官，"
                      f"总开关={'开启' if _enabled else '关闭（灰度，默认False，热重载可切换）'}")
        except Exception as _e:
            # 编排器装配失败不影响框架主流程（降级为「不启用编排器」）
            self.survival_orchestrator = None
            self._log(LogLevel.WARNING, f"[R4阶段二] 存续编排器装配失败，已跳过: {_e}")

    def _init_organs_with_feature(self):
        """实际执行器官装配（legacy 或声明式，受当前 self.config.FEATURE 控制）。"""
        FEATURE = self.config.FEATURE
        # ★P3-2阶段3：声明式装配开关（默认关闭走回退路径，一键回退）
        _asm_mode = "声明式" if FEATURE.get("use_declarative_assembly", False) else "legacy"
        self._log(LogLevel.INFO, f"器官装配模式: {_asm_mode}")
        if FEATURE.get("use_declarative_assembly", False):
            self._init_organs_declarative()
        else:
            self._init_organs_legacy()
        # 装配完成后台日志（供日志巡检确认路径与器官规模）
        self._log(LogLevel.INFO,
                  f"器官装配完成: {len(self.organs)} 器官（{_asm_mode}装配）")

        # ★第158批第6刀（T-QICA声明-1 + N-6★）：装配具名差集自检
        # 口径：声明集 = ORGAN_META 扫描（基线，统一定为器官计数基准）；装配集 = self.organs 实际实例。
        # 具名差集 = 装配集 − 声明集；应全部属于 FRAMEWORK_QUASI_ORGANS（如 QICA，
        # 框架组件以 _create_organ 实例化纳管，预期偏差），否则为「未声明却装配」真缺陷。
        try:
            from nucleus.organ_loader import OrganLoader
            from nucleus.organ_assembler import OrganAssembler
            _metas = OrganLoader(self).scan_organs_directory()
            _diff = OrganAssembler(_metas).diff_declared_vs_instantiated(set(self.organs.keys()))
            self._log(LogLevel.INFO,
                      f"[装配具名差集] 声明基线(ORGAN_META)={_diff['declared_count']} "
                      f"装配={_diff['instantiated_count']} "
                      f"差集={_diff['named_diff']} "
                      f"框架准器官={_diff['quasi_organ_extra']} "
                      f"未声明异常={_diff['undeclared_instantiated']}")
            if _diff["undeclared_instantiated"]:
                self._log(LogLevel.WARNING,
                          f"[装配具名差集] 发现未声明却实例化的器官"
                          f"（需补 ORGAN_META 或归入 FRAMEWORK_QUASI_ORGANS）: "
                          f"{_diff['undeclared_instantiated']}")
        except Exception as _de:
            silent_exc(_de, "main.py:1156 装配具名差集自检", level="warning")

    def _init_organs_declarative(self):
        """
        ★P3-2阶段3：声明式装配（实验性）。

        由 FEATURE['use_declarative_assembly']=True 开启，读 ORGAN_META 依赖声明，
        用 OrganAssembler 做拓扑排序装配，替代硬编码 _create_organ。

        与 legacy 的差异：
          - 器官创建顺序由「硬依赖拓扑排序」决定，而非代码书写顺序；
          - 前向引用/环依赖通过 post_wiring 晚绑定统一回填；
          - 新增器官只需「放入 organs/ + 声明 ORGAN_META」即可被装配。
        """
        from nucleus.organ_assembler import OrganAssembler
        from nucleus.reasoning.EvolutionSandbox import get_evolution_sandbox
        from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool

        # ===== Phase 0: 装配前组件（原在硬编码装配中内联创建） =====
        from somatics.sensor import Sensor
        self.sensor = Sensor()
        from organs.brain.PulseExpression import PulseExpression
        self.expression = PulseExpression()
        # QICA 作为组件创建（位于 nucleus/qica，不在 organs/ 注册表内）
        self.qica = self._create_organ(QICA, "QICA")
        if hasattr(self.qica, 'set_node_pool'):
            self.qica.set_node_pool(self.node_pool)
        if hasattr(self.qica, 'set_knowledge_tree'):
            self.qica.set_knowledge_tree(self.knowledge_tree)
        # 三级安全沙箱核心引擎
        from nucleus.security.SandboxCore import SandboxCore
        self.sandbox_core = SandboxCore()

        # ===== 组件解析器（非器官依赖 → 实例） =====
        def _resolve_component(name: str):
            if name == "framework":
                return self
            if hasattr(self, name):
                return getattr(self, name)
            _factories = {
                "insight_board": get_insight_board,  # type: ignore[possibly-unbound]
                "evolution_sandbox": get_evolution_sandbox,
                "self_inspector": get_self_inspector,  # type: ignore[possibly-unbound]
                "autonomous_deriver": get_autonomous_deriver,
                "companion_bridge": get_companion_bridge,
                "reasoning_pool": get_reasoning_pool,
                "context_snapshot": get_context_snapshot,
            }
            if name in _factories:
                try:
                    return _factories[name]()
                except Exception as e:
                    silent_exc(e, "main.py:1192 _resolve_component", level="warning")
                    return None
            return None

        # ===== Phase 1-3: 声明式装配 =====
        self.organ_loader = OrganLoader(self)
        metas = self.organ_loader.load_organs()
        assembler = OrganAssembler(metas)
        assembler.assemble(self, component_resolver=_resolve_component)
        # 说明：self.organs 已由 _create_organ 逐个填充；assemble() 返回的 created 仅含 None 占位，不覆盖。

        # ★PHASE17-B3（2026-09-07）：装配后声明自检。
        #   缺陷：OrganAssembler.validate() 实现了「未声明硬依赖 / 依赖成环 /
        #   post_wiring 未知符号 / always_online 语义冲突」四类强校验，但全项目
        #   从未有任何调用点——声明写错时不会报错，只在运行期表现为器官属性为
        #   None 的诡异行为，排查成本极高。
        #   策略：装配后立即自检，ERROR 与 WARNING 均以日志输出，**不阻塞启动**
        #   （符合「非核心不阻断」原则）；自检器自身异常也仅降级为 debug。
        try:
            _vreport = assembler.validate()
            _verrs = list(_vreport.get("errors") or [])
            _vwarns = list(_vreport.get("warnings") or [])
            _vstats = _vreport.get("stats") or {}
            if _verrs:
                self._logger.warning(
                    f"[装配自检] 发现 {len(_verrs)} 处声明错误（不阻塞启动，请尽快修正）:"
                )
                for _e in _verrs[:10]:
                    self._logger.warning(f"[装配自检] ERROR: {_e}")
            for _w in _vwarns[:10]:
                self._logger.warning(f"[装配自检] WARN: {_w}")
            self._logger.info(
                f"[装配自检] 完成 | 器官={_vreport.get('organ_count', len(metas))} "
                f"核心={_vstats.get('always_online_count', '?')} "
                f"可降级={_vstats.get('degradable_count', '?')} "
                f"硬依赖边={_vstats.get('hard_organ_edges', '?')} "
                f"软接线={_vstats.get('soft_wiring_count', '?')} "
                f"错误={len(_verrs)} 警告={len(_vwarns)}"
            )
            # ★C-7(b) 声明-实例差集（等价校验）：load_organs 声明集 vs 实际装配器官
            #   活体验收：重启后日志出现「[C-7b 声明-实例差集]」行（待小林协调核验，D5 停框架）
            try:
                _declared_names = {getattr(_m, "name", None) for _m in (metas or [])}
                _declared_names.discard(None)
                _wired_names = set(getattr(self, "organs", {}) or {})
                _missing = sorted(_declared_names - _wired_names)
                _extra = sorted(_wired_names - _declared_names)
                if _missing or _extra:
                    self._logger.warning(
                        f"[装配自检][C-7b 声明-实例差集] 声明={len(_declared_names)} "
                        f"装配={len(_wired_names)} 缺={_missing[:10]} 多={_extra[:10]}")
                else:
                    self._logger.info(
                        f"[装配自检][C-7b 声明-实例差集] 一致({len(_declared_names)}个)")
            except Exception as _c7be:
                self._logger.debug(f"[装配自检][C-7b] 差集计算跳过: {_c7be}")
        except Exception as _ve:
            self._logger.debug(f"[装配自检] 跳过（校验器异常，不影响启动）: {_ve}")

        # ===== 装配后特殊逻辑（非纯依赖注入） =====
        # ★P3-2插件化启用：legacy 等价回填——双腿注入 LLM 兜底回调（复用肺的远程调用封装），
        #   使 SearchIntentClassifier 的「本地规则 + LLM 兜底 + 规则自进化」闭环生效。
        #   与 legacy 路径（_init_organs_legacy）行为一致；注入失败不影响主链路。
        if getattr(self, 'legs', None) is not None:
            try:
                _lung_remote = getattr(self.lung, "_call_remote_api", None)
                if callable(_lung_remote):
                    # ★主线第49批 T1（P1-327）B：双腿调用**绕过**
                    #   `_on_select_model`，旧实现读共享属性 → 残留 + 竞态。
                    #   显式声明「后台学习」来源（线程局部，3 路并行互不影响）。
                    _m49_mark = getattr(self.lung, "_m49_mark_call_source", None)

                    def _m49_legs_llm(_p, _mark=_m49_mark, _call=_lung_remote):
                        if callable(_mark):
                            _mark(True)   # 双腿搜索意图分类 = 后台学习
                        return _call(_p, enable_thinking=False)

                    self.legs.set_llm_callback(_m49_legs_llm)
            except Exception as _se:
                silent_exc(_se, "main.py:1223")
        # 控制器权限配置加载（初始化动作，非依赖注入）
        if hasattr(self, 'controller') and self.controller:
            self.controller.load_permission_config()
        # ★F4处置闭环：注入 framework 只读引用给系统管理器（血管检测 → 系统管理器调度 → 器官执行）
        if hasattr(self, 'system_manager') and self.system_manager \
                and hasattr(self.system_manager, 'set_framework'):
            self.system_manager.set_framework(self)
        # 注册验证学习枢纽 applier（审查→蒸馏→回灌 自增强闭环）
        self._register_learning_hub_appliers()

    def _register_learning_hub_appliers(self):
        """注册验证学习枢纽的规则吸收回调（审查→蒸馏→回灌 自增强闭环）。"""
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            _vl_hub = get_verification_learning_hub()
            if hasattr(self.qica, 'absorb_growth_rules'):
                _vl_hub.register_applier("stomach", self.qica.absorb_growth_rules)
            _inspector = get_self_inspector()  # type: ignore[possibly-unbound]

            def _code_learner_applier(rules):
                _qica_rules = [r for r in rules if r.get("type") in ("domain_knowledge", "concept_correction")]
                _inspector_rules = [r for r in rules if r.get("type") == "code_issue_lesson"]
                _applied = 0
                if _qica_rules and hasattr(self.qica, 'absorb_growth_rules'):
                    _applied += self.qica.absorb_growth_rules(_qica_rules)
                if _inspector_rules and hasattr(_inspector, 'absorb_growth_rules'):
                    _applied += _inspector.absorb_growth_rules(_inspector_rules)
                return _applied

            _vl_hub.register_applier("code_learner", _code_learner_applier)
        except Exception as _se:
            silent_exc(_se, "main.py:1257")

    def _init_organs_legacy(self):
        """硬编码装配（原 _init_organs，作为声明式装配的回退路径）。

        ★第158批 _create_organ 退役★（P2·2026-10-03 停框架施工期标注）：
          本段 37 处硬编码 _create_organ 调用（实测口径：全仓 38 处 = 本段 37 处
          + declarative Phase 0 的 QICA 1 处）**已被声明式装配取代**——
          FEATURE['use_declarative_assembly']=True（config.py:1827）时本段不执行，
          入口 _init_organs_with_feature()（main.py:1148）直接分流至
          _init_organs_declarative()。

          保留而不删除的理由（实测取证，非推测）：
            1. 本段是唯一的一键回退通道（开关置 False 即回退），
               删除会丧失装配层的故障兜底能力；
            2. 框架处于停机状态，删除后无法做装配活体验证（纪律 D5：
               不擅自启停框架），须待小林恢复运行后经实测回归方可移除；
            3. 声明面等价性已实测：legacy 37 处中 36 处已被 ORGAN_META 声明覆盖
               （唯一例外 QICA 不在 organs/ 扫描面，由 declarative Phase 0 承接，
               见 FRAMEWORK_QUASI_ORGANS）。

          防回潮：tools/ci/check_legacy_assembly_gate.py 门禁校验本段不得新增
          _create_organ 调用行数（基线 37），防止已停用路径被重新扩张。
          ★退役完成的前置条件 = 小林恢复运行后完成装配活体验证 + 声明面 100% 覆盖
            （含 QICA 承接方案定稿），届时方可删除本段。
        """
        FEATURE = self.config.FEATURE

        # ===== 核心脏器（始终在线） =====
        self.heart = self._create_organ(PulseHeart, "心脏") 
        self.stomach = self._create_organ(PulseStomach, "胃",
                                          knowledge_tree=self.knowledge_tree,
                                          node_pool=self.node_pool,
                                          frequency_codec=self.frequency_codec)
        self.lung = self._create_organ(PulseLung, "肺")
        # ★第97批 相关任务：注入运行中的肺实例，使进化通道与对话链路共享同一
        #   ChannelHealthTracker 账本（熔断/健康度双写同源、结果回写同一账本）。
        #   灰度沿用 ENABLE_EVOLUTION_USE_CHANNEL_POOL：关闭时不注入（走本地等价账本）。
        if getattr(config, "ENABLE_EVOLUTION_USE_CHANNEL_POOL", False):
            try:
                from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
                SafeEvolutionExecutor._m96_set_lung(self.lung)
            except Exception as _e97a:
                try:
                    from nucleus.logger import get_module_logger
                    get_module_logger("main").warning(
                        "[进化渠道] 注入肺实例失败(降级本地账本): %s: %s",
                        type(_e97a).__name__, _e97a)
                except Exception as e:
                    silent_exc(e, "main.py:1314 注入肺实例降级", level="warning")
        self.liver = self._create_organ(PulseLiver, "肝",
                                        node_pool=self.node_pool,
                                        knowledge_tree=self.knowledge_tree,
                                        frequency_codec=self.frequency_codec,
                                        resonance_engine=self.resonance_engine)
        self.liver.set_snapshot(self.snapshot)
        # ★v17.0性能优化：注入推理进程池到肝脏
        try:
            from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
            self.liver.set_reasoning_pool(get_reasoning_pool())
        except Exception as _se:
            silent_exc(_se, "main.py:1281")
        self.kidney = self._create_organ(PulseKidney, "肾",
                                         node_pool=self.node_pool,
                                         knowledge_tree=self.knowledge_tree,
                                         resonance_engine=self.resonance_engine)
        self.blood_vessel = self._create_organ(PulseBloodVessel, "血管")

        # ===== 大脑系统（始终在线） =====
        self.cortex = self._create_organ(PulseCortex, "大脑皮层")
        self.inner_world = self._create_organ(PulseInnerWorld, "内在世界",
                                              node_pool=self.node_pool,
                                              resonance_engine=self.resonance_engine,
                                              frequency_codec=self.frequency_codec,
                                              knowledge_tree=self.knowledge_tree)
        # ★v25.0新增：注入体验池，用于推理失败体验记录
        self.inner_world.set_experience_pool(self.experience_pool)        
        self.subconscious = self._create_organ(PulseSubconscious, "潜意识",
                                               knowledge_tree=self.knowledge_tree,
                                               node_pool=self.node_pool)

        self.reflection = self._create_organ(PulseReflection, "前额叶",
                                             node_pool=self.node_pool,
                                             resonance_engine=self.resonance_engine)
        # ★v25.0新增：注入体验池，用于复盘体验记录
        self.reflection.set_experience_pool(self.experience_pool)
        # ★v16.0新增: 代码自主学习器官
        if FEATURE.get("enable_code_learner", True):
            self.code_learner = self._create_organ(PulseCodeLearner, "代码学习",
                                                   node_pool=self.node_pool,
                                                   frequency_codec=self.frequency_codec,
                                                   knowledge_tree=self.knowledge_tree)
        else:
            self.code_learner = None          
        self.risk_perception = self._create_organ(PulseRiskPerception, "风险感知")
        # ★v25.0新增：注入代码学习器到肝脏，用于代码调用关联
        if hasattr(self, 'liver') and self.liver:
            self.liver.set_code_learner(self.code_learner)          
        # ===== v24.0新增：注入自我认知，使风险等级考虑用户关系 =====
        if hasattr(self, 'self_awareness') and self.self_awareness:
            self.risk_perception.set_self_awareness(self.self_awareness)
        # ===== v24.0新增结束 =====
        self.interest_model = self._create_organ(PulseInterestModel, "兴趣模型")
        # ★P3-4修复：注入事件流挖掘器，让兴趣模型消费其发现的模式（消除孤岛）
        if hasattr(self, 'stream_miner') and self.stream_miner is not None \
                and hasattr(self.interest_model, 'set_stream_miner'):
            self.interest_model.set_stream_miner(self.stream_miner)

        # QICA 心智模型
        self.qica = self._create_organ(QICA, "QICA")
        self.cortex.set_qica(self.qica)
        # ★FIX: 为非语义器官也注册规则吸收回调，避免 stomach/code_learner 蒸馏规则被静默丢弃
        # ★P3-12修复：code_learner 规则按类型分发——
        #   domain_knowledge/concept_correction → qica，code_issue_lesson → self_inspector，
        #   让 LLM 审查结论真正反哺规则引擎，打通「审查→蒸馏→回灌」自增强闭环。
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            from nucleus.self_inspector import (
                get_self_inspector,  # type: ignore[possibly-unbound]
            )
            _vl_hub = get_verification_learning_hub()
            if hasattr(self.qica, 'absorb_growth_rules'):
                _vl_hub.register_applier("stomach", self.qica.absorb_growth_rules)
            _inspector = get_self_inspector()  # type: ignore[possibly-unbound]

            def _code_learner_applier(rules):
                _qica_rules = [r for r in rules if r.get("type") in ("domain_knowledge", "concept_correction")]
                _inspector_rules = [r for r in rules if r.get("type") == "code_issue_lesson"]
                _applied = 0
                if _qica_rules and hasattr(self.qica, 'absorb_growth_rules'):
                    _applied += self.qica.absorb_growth_rules(_qica_rules)
                if _inspector_rules and hasattr(_inspector, 'absorb_growth_rules'):
                    _applied += _inspector.absorb_growth_rules(_inspector_rules)
                return _applied

            _vl_hub.register_applier("code_learner", _code_learner_applier)
        except Exception as _se:
            silent_exc(_se, "main.py:1359")
        # ★v22.0重构：注入节点池和知识树到QICA，用于动态知识路径映射
        if hasattr(self.qica, 'set_node_pool'):
            self.qica.set_node_pool(self.node_pool)
        if hasattr(self.qica, 'set_knowledge_tree'):
            self.qica.set_knowledge_tree(self.knowledge_tree)
        self.cortex.set_inner_world(self.inner_world)
        # ===== v24.0新增：语义理解器 =====
        self.semantic_comprehension = self._create_organ(
            PulseSemanticComprehension, "语义理解器",
            qica=self.qica,
            lung=self.lung,
            node_pool=self.node_pool,
        )
        # 让大脑皮层发射语义理解事件而不是直接发给QICA
        # 修改大脑皮层的发射逻辑（见下面）
        # ===== v24.0新增结束 =====        
        # ===== 躯体硬件层（提前创建，供视觉皮层和设备管理器使用） =====
        from somatics.sensor import Sensor
        self.sensor = Sensor()        
        # ===== 感知系统 =====
        self.touch = self._create_organ(PulseTouch, "触觉")
        self.eyes = self._create_organ(PulseEyes, "眼睛",
                                       node_pool=self.node_pool,
                                       resonance_engine=self.resonance_engine,
                                       frequency_codec=self.frequency_codec)
        self.eyes.set_sensor(self.sensor)        
        self.ears = self._create_organ(PulseEars, "耳朵")

        if FEATURE.get("enable_vision", True):
            self.visual_cortex = self._create_organ(PulseVisualCortex, "视觉皮层",
                                                    node_pool=self.node_pool,
                                                    frequency_codec=self.frequency_codec)
            self.visual_cortex.set_sensor(self.sensor)
        else:
            self.visual_cortex = None


        # ===== 运动系统 =====
        self.mouth = self._create_organ(PulseMouth, "嘴巴",)
        # ===== v24.0新增：创建表达增强模块并注入到嘴巴 =====
        from organs.brain.PulseExpression import PulseExpression
        self.expression = PulseExpression()
        self.mouth.set_expression(self.expression)
        self.mouth.set_context_snapshot(get_context_snapshot())
        # ===== v24.0新增结束 =====
        if FEATURE.get("enable_motor", True):
            self.hands = self._create_organ(PulseHands, "双手")
            self.legs = self._create_organ(PulseLegs, "双腿")
            self.legs.set_node_pool(self.node_pool)
            # ★主线第49批 T2（P2-328）C：注入胃的**只读引用**，
            #   供双腿从 `_rejected_digestion_ring` 还原真实入库结果
            #   （修正「被质量门槛拦截却报成功」）。未注入时反馈自动跳过。
            try:
                _m49_st = getattr(self, "stomach", None)
                _m49_set = getattr(self.legs, "set_stomach", None)
                if _m49_st is not None and callable(_m49_set):
                    _m49_set(_m49_st)
            except Exception as _se:
                silent_exc(_se, "main.py:1418")
            # ★R3修复：注入 LLM 兜底回调到搜索意图分类器（复用肺的远程调用封装），
            # 使 SearchIntentClassifier 的「本地规则 + LLM 兜底 + 规则自进化」闭环生效。
            # 回调签名需满足 (prompt: str) -> str | None，此处用 lambda 适配肺的
            # _call_remote_api(prompt, model, enable_thinking) 签名。
            try:
                _lung_remote = getattr(self.lung, "_call_remote_api", None)
                if callable(_lung_remote):
                    # ★主线第49批 T1（P1-327）B：双腿调用**绕过**
                    #   `_on_select_model`，旧实现读共享属性 → 残留 + 竞态。
                    #   显式声明「后台学习」来源（线程局部，3 路并行互不影响）。
                    _m49_mark = getattr(self.lung, "_m49_mark_call_source", None)

                    def _m49_legs_llm(_p, _mark=_m49_mark, _call=_lung_remote):
                        if callable(_mark):
                            _mark(True)   # 双腿搜索意图分类 = 后台学习
                        return _call(_p, enable_thinking=False)

                    self.legs.set_llm_callback(_m49_legs_llm)
            except Exception as _se:
                silent_exc(_se, "main.py:1438")
            self.code_sandbox = self._create_organ(PulseCodeSandbox, "代码沙箱")
            self.file_digester = self._create_organ(PulseFileDigester, "文件消化器")
        else:
            self.hands = self.legs = self.code_sandbox = self.file_digester = None

        # ===== 身份系统（始终在线） =====
        self.self_awareness = self._create_organ(PulseSelfAwareness, "自我认知",
                                                 node_pool=self.node_pool,
                                                 frequency_codec=self.frequency_codec)
        # ===== v24.0修复：注入框架引用，供存续状态感知访问梯度追踪器 =====
        if hasattr(self.self_awareness, 'set_framework_ref'):
            self.self_awareness.set_framework_ref(self)
        # ===== v24.0修复结束 =====
        self.inner_world.set_self_awareness(self.self_awareness)
        # 注入自我认知到风险感知
        if hasattr(self, 'risk_perception') and self.risk_perception:
            self.risk_perception.set_self_awareness(self.self_awareness)        
        # ★P3-2审计：set_companion_bridge 方法不存在（hasattr 恒 False），幻影死代码已移除
        # 注入闭环间洞察共享黑板
        _insight_board = _safe_get_insight_board()
        if _insight_board is not None and hasattr(self.inner_world, 'set_insight_board'):
            self.inner_world.set_insight_board(_insight_board)
        if hasattr(self.inner_world, 'set_autonomous_deriver'):
            self.inner_world.set_autonomous_deriver(get_autonomous_deriver())
        # ★XMOD-1修复: 直接注入（setter 现已存在），移除 hasattr 守卫以避免静默跳过
        from nucleus.reasoning.EvolutionSandbox import get_evolution_sandbox
        # [批次4·深度体检][XMOD-1] 启动期直接注入演化沙箱
        self.inner_world.set_evolution_sandbox(get_evolution_sandbox())
        # ★骨架优化：同步注入代码学习器（此前靠跨模块单例直调，现统一依赖注入）
        if hasattr(self, 'code_learner') and self.code_learner and hasattr(self.code_learner, 'set_evolution_sandbox'):
            self.code_learner.set_evolution_sandbox(get_evolution_sandbox())
        if hasattr(self, 'code_learner') and self.code_learner and hasattr(self.code_learner, 'set_self_inspector'):
            self.code_learner.set_self_inspector(get_self_inspector())  # type: ignore[possibly-unbound]
        if hasattr(self.subconscious, 'set_insight_board'):
            self.subconscious.set_insight_board(_insight_board)
        self.cortex.set_self_awareness(self.self_awareness)
        self.cortex.set_risk_perception(self.risk_perception)
        # ★P0-3：注入自主推导引擎和节点池
        if hasattr(self.cortex, 'set_autonomous_deriver'):
            self.cortex.set_autonomous_deriver(get_autonomous_deriver())
        if hasattr(self.cortex, 'set_node_pool'):
            self.cortex.set_node_pool(self.node_pool)
        # ★P1-1修复：主动交互器官受FEATURE开关控制
        if FEATURE.get("enable_initiative", True):
            self.initiative = self._create_organ(PulseInitiative, "主动交互",
                                                 self_awareness=self.self_awareness,
                                                 interest_model=self.interest_model,
                                                 node_pool=self.node_pool)
        else:
            self.initiative = None
        self.ethics = self._create_organ(PulseEthics, "伦理")
        self.growth = self._create_organ(PulseGrowth, "成长",
                                         node_pool=self.node_pool,
                                         frequency_codec=self.frequency_codec)
        self.narrative = self._create_organ(PulseNarrativeSelf, "叙事自我",
                                            node_pool=self.node_pool,
                                            frequency_codec=self.frequency_codec)
        # 内在世界 ← 叙事自我注入（动态身份回答）
        if hasattr(self, 'inner_world') and hasattr(self, 'narrative'):
            self.inner_world.set_narrative_self(self.narrative)
        self.personality = self._create_organ(PulsePersonalityKernel, "人格内核",
                                              node_pool=self.node_pool)
        # ★P0修复：把人格内核注入嘴巴，使输出前的人格把关真正由人格内核执行
        # （原实现人格内核只注入给宪法守护，从未接入输出链路，把关是假的）
        if hasattr(self, 'mouth') and self.mouth is not None:
            self.mouth.set_personality_kernel(self.personality)
        # ===== v24.0新增：精神宪法运行时 =====
        self.spirit_constitution = self._create_organ(
            PulseSpiritConstitution, "宪法守护",
            self_awareness=self.self_awareness,
            personality_kernel=self.personality,
            narrative_self=self.narrative,
            node_pool=self.node_pool,
        )
        # ===== v24.0新增结束 =====
        # ===== P2-3: 插件化自动加载 =====
        self.organ_loader = OrganLoader(self)
        self.organ_loader.load_organs()        
        # ===== 心脏注入自我认知（动态心率需要） =====
        if hasattr(self, 'heart') and hasattr(self, 'self_awareness'):
            self.heart.set_self_awareness(self.self_awareness)
        # ===== 潜意识注入双腿（主动学习需要） =====
        if hasattr(self, 'subconscious') and hasattr(self, 'legs'):
            self.subconscious.set_legs(self.legs)
        if hasattr(self, 'self_awareness') and self.self_awareness:
            self.subconscious.set_self_awareness(self.self_awareness)            
        if FEATURE.get("enable_immune", True):
            # ★v18.0修复：免疫系统改为OrganLoader动态加载
            _immune_result = self.organ_loader.load_organs_from_dir("immune")
            self.white_cell = _immune_result.get("PulseWhiteCell")
            self.skin = _immune_result.get("PulseSkin")
            self.thymus = _immune_result.get("PulseThymus")
            self.bone_marrow = _immune_result.get("PulseBoneMarrow")
            # 保持原有的依赖注入（胸腺和骨髓需要白细胞引用）
            if self.thymus and self.white_cell:
                if hasattr(self.thymus, 'set_white_cell'):
                    self.thymus.set_white_cell(self.white_cell)
                if hasattr(self.white_cell, 'set_thymus'):
                    self.white_cell.set_thymus(self.thymus)
            if self.bone_marrow and self.white_cell:
                if hasattr(self.bone_marrow, 'set_white_cell'):
                    self.bone_marrow.set_white_cell(self.white_cell)
        else:
            self.white_cell = self.skin = self.thymus = self.bone_marrow = None

        # ===== 内分泌系统 =====
        if FEATURE.get("enable_endocrine", True):
            self.hormones = self._create_organ(PulseHormones, "激素")
            if hasattr(self, 'self_awareness'):
                self.hormones.set_self_awareness(self.self_awareness)
            # ===== v24.0新增：注入激素引用到宪法运行时 =====
            if hasattr(self, 'spirit_constitution') and self.spirit_constitution:
                self.spirit_constitution.set_hormones(self.hormones)
            # ===== v24.0新增结束 =====
            # ===== v24.0修复：注入激素引用到自我认知，供存续状态感知直接获取情绪趋势 =====
            if hasattr(self, 'self_awareness') and hasattr(self.self_awareness, 'set_hormones_ref'):
                self.self_awareness.set_hormones_ref(self.hormones)
            # ===== v24.0修复结束 =====
        # ★内分泌扩展：神经递质系统（6种递质调节情绪/注意力/觉醒/学习）
        if FEATURE.get("enable_neurotransmitters", True):
            self.neurotransmitters = self._create_organ(PulseNeurotransmitters, "神经递质")
            # 激素 → 神经递质联动（情绪事件触发递质释放）
            if hasattr(self, 'hormones') and self.hormones:
                if hasattr(self.hormones, 'set_neurotransmitters_ref'):
                    self.hormones.set_neurotransmitters_ref(self.neurotransmitters)
        # 内在世界 ← 激素注入（情绪感知）
        if hasattr(self, 'inner_world') and hasattr(self, 'hormones'):
            self.inner_world.set_hormones(self.hormones)
        # ★v16.0新增: 精神整合器官（放在 narrative 和 hormones 创建之后）
        if FEATURE.get("enable_spiritual_core", True):
            self.spiritual_core = self._create_organ(PulseSpiritualCore, "精神核心")
            if hasattr(self, 'narrative') and self.narrative:
                self.spiritual_core.set_narrative_self(self.narrative)
            if hasattr(self, 'hormones') and self.hormones:
                self.spiritual_core.set_hormones(self.hormones)
            if hasattr(self, 'node_pool'):
                self.spiritual_core.set_node_pool(self.node_pool)
            _insight_board_sp = _safe_get_insight_board()
            if _insight_board_sp:
                self.spiritual_core.set_insight_board(_insight_board_sp)
        else:
            self.spiritual_core = None

        if hasattr(self, 'risk_perception') and self.risk_perception:
            self.inner_world.set_risk_perception(self.risk_perception) 
        if hasattr(self, 'inner_world') and hasattr(self, 'ethics') and self.ethics:
            self.inner_world.set_ethics(self.ethics)            
        # ★v17.0 F2修复：注入代码学习器官引用，支持代码调用链查询
        if hasattr(self, 'inner_world') and hasattr(self, 'code_learner') and self.code_learner:
            self.inner_world.set_code_learner(self.code_learner)            
        # 注入上下文快照管理器
        if hasattr(self.inner_world, 'set_context_snapshot'):
            self.inner_world.set_context_snapshot(get_context_snapshot())
        
        # ===== 注入推理进程池 =====
        try:
            from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
            _reasoning_pool = get_reasoning_pool()
            if hasattr(self.inner_world, 'set_reasoning_pool'):
                self.inner_world.set_reasoning_pool(_reasoning_pool)
        except Exception as _se:
            silent_exc(_se, "main.py:1600")
        # ===== 推理进程池注入结束 =====                     
        if FEATURE.get("enable_evolution", True):
            # ★v18.0修复：遗传系统改为OrganLoader动态加载
            _genetic_result = self.organ_loader.load_organs_from_dir("genetic")
            self.evolution = _genetic_result.get("PulseEvolution")
            self.dna_repair = _genetic_result.get("PulseDNARepair")
            self.bonding = _genetic_result.get("PulseBonding")
            self.consent = _genetic_result.get("PulseConsent")
            self.nurture = _genetic_result.get("PulseNurture")
            self.reproduction_ethics = _genetic_result.get("PulseReproductionEthics")
        else:
            self.evolution = self.dna_repair = self.bonding = None
            self.consent = self.nurture = self.reproduction_ethics = None

        # ===== 核心系统（始终在线） =====
        # ===== 核心系统（动态加载） =====
        # ===== 核心系统（★v18.0修复：改为OrganLoader动态加载） =====
        _core_result = self.organ_loader.load_organs_from_dir("core")
        # ===== v24.0新增：动机-压力-奖赏闭环器官 =====
        # 优先从动态加载的核心器官结果中获取实例
        self.motivation_cycle = _core_result.get("PulseMotivationCycle")
        if self.motivation_cycle is None:
            # 动态加载未发现（可能被功能开关排除），手动创建
            self.motivation_cycle = self._create_organ(
                PulseMotivationCycle, "动机循环"
            )
        # 注入依赖
        self.motivation_cycle.set_experience_pool(self.experience_pool)
        # ★P0-4修复：stress_axis 在下方（约:800）才被赋值，此处注入恒为空，
        # 已改为在 stress_axis 赋值后二次注入（见下方），此处删除以免误导。
        if hasattr(self, 'self_awareness') and self.self_awareness:
            self.motivation_cycle.set_self_awareness(self.self_awareness)
        if hasattr(self, 'oscillon_monitor') and self.oscillon_monitor:
            self.motivation_cycle.set_oscillon_field(self.oscillon_monitor)
        if hasattr(self, 'node_pool') and self.node_pool and hasattr(self.motivation_cycle, 'set_node_pool'):
            self.motivation_cycle.set_node_pool(self.node_pool)
        # ★v24.0统一日志名称：动态加载实例的日志标签为英文，这里改为中文
        if self.motivation_cycle.organ_name != "动机循环":
            self.motivation_cycle.organ_name = "动机循环"
            self.motivation_cycle.reset_logger("动机循环")
        # ===== v24.0新增结束 =====
        # ★主动目标闭环：动机循环注入主动交互器官，让内生动机驱动主动交互
        if hasattr(self, 'initiative') and self.initiative and hasattr(self.initiative, 'set_motivation_cycle'):
            self.initiative.set_motivation_cycle(self.motivation_cycle)

        # ===== v24.0新增：全域自学习循环器官 =====
        self.global_learner = _core_result.get("PulseGlobalLearner")
        if self.global_learner is None:
            self.global_learner = self._create_organ(
                PulseGlobalLearner, "全局学习器"
            )
        # 注入依赖
        self.global_learner.set_experience_pool(self.experience_pool)
        if hasattr(self, 'semantic_comprehension') and self.semantic_comprehension:
            self.global_learner.set_semantic_comprehension(self.semantic_comprehension)
        self.global_learner.set_self_inspector(get_self_inspector())  # type: ignore[possibly-unbound]
        if hasattr(self, 'code_learner') and self.code_learner:
            self.global_learner.set_code_learner(self.code_learner)
        if hasattr(self, 'lung') and self.lung:
            self.global_learner.set_lung(self.lung)
        # ★v24.0统一日志名称
        if self.global_learner.organ_name != "全局学习器":
            self.global_learner.organ_name = "全局学习器"
            self.global_learner.reset_logger("全局学习器")
        # ===== v24.0新增结束 =====   
        self.energy_metabolism = _core_result.get("PulseEnergyMetabolism")
        self.health_monitor = _core_result.get("PulseHealthMonitor")
        self.emergency_handler = _core_result.get("PulseEmergencyHandler")
        self.spinal_cord = _core_result.get("PulseSpinalCord")
        self.stress_axis = _core_result.get("PulseStressAxis")
        # ★P0-4修复：应激轴在此处才赋值，补上动机循环的应激轴注入（原先在:764 恒为空）
        if self.stress_axis and hasattr(self, 'motivation_cycle') and self.motivation_cycle:
            self.motivation_cycle.set_stress_axis(self.stress_axis)
        # ★压力闭环：应激轴注入内在世界，用于压力感知调节推理策略
        if self.stress_axis and hasattr(self, 'inner_world') and self.inner_world:
            self.inner_world.set_stress_axis(self.stress_axis)
        self.hardware_launcher = _core_result.get("PulseHardwareLauncher")
        self.metrics_collector = _core_result.get("PulseMetricsCollector")
        if self.metrics_collector:
            self.metrics_collector.set_node_pool(self.node_pool)
        self.inference_engine = _core_result.get("PulseInferenceEngine")
        self.device_manager = _core_result.get("PulseDeviceManager")
        if self.device_manager:
            self.device_manager.set_sensor(self.sensor)
        self.system_manager = _core_result.get("PulseSystemManager")
        self.proprioception = _core_result.get("PulseProprioception")
        if self.proprioception and self.touch and self.system_manager:
            if hasattr(self.proprioception, 'set_touch'):
                self.proprioception.set_touch(self.touch)
            if hasattr(self.proprioception, 'set_system_manager'):
                self.proprioception.set_system_manager(self.system_manager)
        # ===== 控制器器官（电脑操控能力） =====
        if FEATURE.get("enable_controller", True):
            self.controller = self._create_organ(PulseController, "控制器")
            # ★v25.0新增：注入节点池和QICA，用于语义增强搜索
            if hasattr(self.controller, 'set_node_pool'):
                self.controller.set_node_pool(self.node_pool)
            if hasattr(self.controller, 'set_qica'):
                self.controller.set_qica(self.qica)
            if hasattr(self, 'white_cell') and self.white_cell:
                self.controller.set_white_cell(self.white_cell)
            if hasattr(self, 'touch'):
                self.controller.set_touch(self.touch)
            if hasattr(self, 'stomach'):
                self.controller.set_stomach(self.stomach)
            self.controller.load_permission_config()
        else:
            self.controller = None

        # ===== P2-2: 三级安全沙箱核心引擎 =====
        from nucleus.security.SandboxCore import SandboxCore
        self.sandbox_core = SandboxCore()
        if hasattr(self.skin, 'set_sandbox_core'):
            self.skin.set_sandbox_core(self.sandbox_core)
        if hasattr(self.code_sandbox, 'set_sandbox_core'):
            self.code_sandbox.set_sandbox_core(self.sandbox_core)
        # ★P3-2审计：ethics 无 set_sandbox_core 方法（hasattr 恒 False），幻影死代码已移除
        if hasattr(self.white_cell, 'set_sandbox_core'):
            self.white_cell.set_sandbox_core(self.sandbox_core)
        # ★P3-2审计：subconscious.set_legs 已在第 794-795 行注入，此处重复注入已移除
    # ========================================================================
    # P0-1: 通用器官注册方法
    # ========================================================================

    def _register_organ(self, organ, organ_name: str):
        """通用器官启动 + 共振条件注册（P0-1）"""
        organ.start()
        cond_ids = []
        for cond in organ.get_resonance_conditions():
            cid = self.info_field.register_condition(
                organ_name=cond["organ_name"],
                event_types=cond.get("event_types", []),
                min_priority=cond.get("min_priority", 1),
                handler=organ.on_pulse,
            )
            cond_ids.append(cid)
        # ★修复P0-4: 记录本器官注册的共振条件ID，便于stop时反注册，
        # 避免同实例 stop→start 时同一处理器被注册成两份（双倍分发）及 stop 后残留脉冲仍调用已停止器官
        # ★阶段三子任务1：自动为所有器官订阅time.tick事件（受ENABLE_TIME_CORE开关保护）
        # TimeCore广播time.tick，但器官默认未订阅，导致收不到。此处统一注册。
        try:
            import config as _cfg_time
            if getattr(_cfg_time, "ENABLE_TIME_CORE", False):
                from nucleus.const import SystemEvent
                _tick_cid = self.info_field.register_condition(
                    organ_name=organ_name,
                    event_types=[SystemEvent.TIME_TICK],
                    min_priority=1,
                    handler=organ.on_pulse,  # _handle_pulse_safe会拦截并走on_time_tick
                )
                cond_ids.append(_tick_cid)
        except Exception as _tick_err:
            self._log(LogLevel.DEBUG, f"{organ_name} time.tick订阅失败（不影响主链路）: {_tick_err}")
        organ.set_registered_cond_ids(cond_ids)
        self._log(LogLevel.INFO, f"{organ_name}已接入，共振条件已注册({len(cond_ids)}个)")

    # ========================================================================
    # 框架启动
    # ========================================================================

    def start(self):
        """启动框架：恢复快照、注入种子记忆、启动所有器官、发布 system.boot 脉冲"""
        if getattr(self, "_running", False):
            self._log(LogLevel.WARNING, "框架已在运行，忽略重复启动请求（防止双倍注册/幽灵处理）")
            return
        self._running = True

        print(f"\n{'='*50}")
        print(f"  {config.SYSTEM_NAME} {config.SYSTEM_VERSION} 启动中...")
        print(f"{'='*50}\n")

        # 步骤0：加载本能快照（在所有知识加载之前）
        instinct_nodes = self.instinct_snapshot.load()
        if instinct_nodes:
            self.node_pool.load_instincts(instinct_nodes)
        
        # 步骤5-6: 恢复快照、注入种子记忆
        restored_nodes = self.snapshot.load()
        # 加载L1独立快照
        l1_nodes = self.snapshot.load_l1()
        if l1_nodes:
            # ★优化：批量加载替代循环逐个add，减少锁竞争
            self.node_pool.load_batch(l1_nodes)
            for node in l1_nodes:
                self.knowledge_tree.register_path(node.space_path)
            self._log(LogLevel.INFO, f"L1快照恢复: {len(l1_nodes)} 个L1节点已加载(批量)")        
        # ===== 新增: 完整性/分层校验失败时自动回退 =====
        # ★第80批 T4：回退条件扩展为「空结果 / 分层塌缩（主全L1但备份有L2/L3） / 校验FAIL」。
        _need_backup = False
        if not restored_nodes:
            _need_backup = True
        else:
            # 分层塌缩检测（G0 事故特征）：主快照全 L1 但备份存在 L2/L3 → 回退备份。
            _main_l2l3 = sum(1 for _n in restored_nodes
                             if str(getattr(_n, "evol_level", "")).upper() in ("L2", "L3"))
            if _main_l2l3 == 0:
                try:
                    _bk = self.snapshot.load_from_backup()
                    _bk_l2l3 = sum(1 for _n in _bk
                                   if str(getattr(_n, "evol_level", "")).upper() in ("L2", "L3"))
                    if _bk_l2l3 > 0:
                        self._log(LogLevel.ERROR,
                                  f"[第80批 T4] 主快照分层塌缩检测：主 L2/L3={_main_l2l3} "
                                  f"但备份 L2/L3={_bk_l2l3}，回退备份")
                        restored_nodes = _bk
                        _need_backup = False
                except Exception as _bke:
                    # ★第80批验收修正：备份探测失败不得静默吞（m78 零静默 pass 门禁），
                    #   记日志后沿用主快照，交由后续 _need_backup/校验FAIL 路径处理。
                    self._log(LogLevel.WARNING,
                              f"[第80批 T4] 分层塌缩备份探测失败，沿用主快照: "
                              f"{type(_bke).__name__}: {_bke}")
            # 校验FAIL 信号（由 PulseSnapshot.load() 暴露）
            if getattr(self.snapshot, "_m80_last_load_checksum_failed", False):
                self._log(LogLevel.ERROR, "[第80批 T4] 主快照校验和不匹配，回退备份")
                _need_backup = True
        if _need_backup and os.path.exists(self.snapshot.snapshot_path):
            self._log(LogLevel.WARNING, "主快照恢复异常，尝试从备份恢复...")
            restored_nodes = self.snapshot.load_from_backup()
        
        if restored_nodes:
            # ★阶段D：启动按需加载——冷存储启用时冷节点落磁盘按需召回，热/温节点进内存；
            # 冷存储未启用时 load_batch_lazy 内部回退 load_batch（零回归）。
            _lazy_stat = self.node_pool.load_batch_lazy(restored_nodes)
            if config.FEATURE.get("use_cold_storage", False):
                self._log(LogLevel.INFO,
                          f"启动按需加载: {_lazy_stat.get('in_memory', 0)} 节点进内存, "
                          f"{_lazy_stat.get('in_cold_disk', 0)} 冷节点落磁盘按需召回")
            # ★启动优化：批量注册知识树路径，替代8130次循环逐个注册
            _main_paths = [n.space_path for n in restored_nodes if hasattr(n, 'space_path') and n.space_path]
            _main_registered = self.knowledge_tree.register_paths_batch(_main_paths)
            self._log(LogLevel.INFO, f"知识树批量注册: {_main_registered}条路径(单次锁获取)")
        # ★第158批 上-A O-A1（P0）：任务态账本恢复（重启对账）。
        #   落点已裁定——「标 running 无存活任务」此前**无持久化载体**
        #   （scheduler 纯内存 / cooldown.json 纯冷却账 / runtime_state 的 running
        #   是器官计数），故本刀新建 data/evolution/task_ledger.json。此处挂在启动
        #   恢复序列：load → 对账 → 「running 但存活 pid 已不在」者**单事务**改判
        #   interrupted + 出诊断回执（★不回放不续跑；未送达回复随附、免重做）。
        #   冷却账仅**只读核对**（不失明），绝不改写。恢复失败**不阻断启动**。
        try:
            from nucleus.evolution.task_ledger import reconcile_on_startup
            _tl_res = reconcile_on_startup()
            if _tl_res.get("reconciled"):
                self._log(LogLevel.WARNING,
                          f"任务态账本恢复: {_tl_res['reconciled']} 个任务改判 interrupted"
                          f"（标 running 但无存活任务）")
            else:
                self._log(LogLevel.DEBUG, "任务态账本恢复: 无需改判")
        except Exception as _tl_e:
            silent_exc(_tl_e, where="main.py:start 任务态账本恢复（不阻断启动）")
        # ===== 新增: 恢复生命连续性状态 =====
        extra_state = self.snapshot.get_extra_state()
        if extra_state:
            life_state_info = extra_state.get("life_state")
            emotion_state_info = extra_state.get("emotion_state")
            social_memory_info = extra_state.get("social_memory") 
            narrative_count = extra_state.get("narrative_count")
            saved_values = extra_state.get("values")
            
            if life_state_info and hasattr(self, 'organs'):
                subcon = self.organs.get("潜意识")
                if subcon and hasattr(subcon, 'set_life_state'):
                    subcon.set_life_state(life_state_info)
            
            if emotion_state_info and hasattr(self, 'organs'):
                hormones = self.organs.get("激素")
                if hormones and hasattr(hormones, 'set_emotion_state'):
                    hormones.set_emotion_state(emotion_state_info)
                # ===== 新增: 恢复情绪时间线 =====
                emotion_timeline_info = extra_state.get("emotion_timeline")
                if hormones and hasattr(hormones, 'set_emotion_timeline') and emotion_timeline_info:
                    hormones.set_emotion_timeline(emotion_timeline_info)
                if hormones and hasattr(hormones, 'set_social_memory') and social_memory_info:
                    hormones.set_social_memory(social_memory_info)
            
            if narrative_count is not None and saved_values is not None:
                narrative = self.organs.get("叙事自我") if hasattr(self, 'organs') else None
                if narrative:
                    # 恢复动态价值观
                    narrative.merge_dynamic_values(saved_values)
                    # 【生命叙事持久化】恢复周期报告
                    _saved_reports = extra_state.get("weekly_reports", [])
                    if _saved_reports:
                        narrative.set_weekly_reports(_saved_reports)
                    self._log(LogLevel.INFO, 
                             f"生命连续性恢复: 叙事事件{narrative_count}条, "
                             f"价值观{list(saved_values.keys())}, "
                             f"周期报告{len(_saved_reports)}份")
        # ===== 新增: 恢复上下文数据（对话记忆/推理链/搜索经验/学习目标/代码进度）=====
        _ctx_snapshot = get_context_snapshot()
        _ctx_data = _ctx_snapshot.load_all()
        if hasattr(self, 'inner_world') and self.inner_world:
            # 恢复对话记忆
            _conv_data = _ctx_data.get("conversation_memory", {})
            if isinstance(_conv_data, dict):
                _memories_for_user = []
                # 遍历所有用户的记忆，合并到内存中
                for _uname, _upart in _conv_data.get("users", {}).items():  # noqa: PERF102
                    _memories_for_user.extend(_upart.get("memories", []))
                if _memories_for_user:
                    self.inner_world.set_conversation_memory(_memories_for_user)
                    self._log(LogLevel.INFO, f"上下文恢复: {len(_memories_for_user)}条对话记忆已加载")
            
            # 恢复推理链
            _traces = _ctx_data.get("inference_trace", [])
            if _traces:
                self.inner_world.set_inference_trace(_traces)
                self._log(LogLevel.INFO, f"上下文恢复: {len(_traces)}条推理链已加载")
            
            # 恢复搜索经验
            _experiences = _ctx_data.get("search_experience", {})
            if _experiences:
                self.inner_world.set_search_experience(_experiences)
                self._log(LogLevel.INFO, f"上下文恢复: {len(_experiences)}条搜索经验已加载")
            
            # 恢复学习目标
            _goals = _ctx_data.get("learning_goals", {})
            if _goals.get("active_goal"):
                self.inner_world.set_active_learning_goal(_goals["active_goal"])
                self._log(LogLevel.INFO, "上下文恢复: 活跃学习目标已加载")
            if _goals.get("goal_queue"):
                self.inner_world.set_learning_goal_queue(_goals["goal_queue"])
            
            # 恢复代码理解进度
            _code_prog = _ctx_data.get("code_progress", {})
            if _code_prog.get("understood", 0) > 0:
                _progress = self.inner_world.get_code_understanding_progress()
                _progress["understood"] = _code_prog.get("understood", 0)
                _progress["total_methods"] = _code_prog.get("total_methods", 0)
                self.inner_world.set_code_understanding_progress(_progress)
                # ★L17修复：日志同口径，钳制 understood 不超过 total
                _safe_u = _code_prog.get("understood", 0)
                _safe_t = _code_prog.get("total_methods", 0)
                if _safe_t > 0:
                    _safe_u = min(_safe_u, _safe_t)
                self._log(LogLevel.INFO, f"上下文恢复: 代码理解进度{_safe_u}/{_safe_t}")
        # ===== 恢复器官额外状态（organ_states 子字典，兼容旧快照顶层键） =====
        _organ_states = extra_state.get("organ_states", {}) if extra_state else {}

        # ===== 恢复内在世界的 pending_extra_state（代码理解进度等） =====
        if hasattr(self, 'inner_world') and self.inner_world:
            _iw_extra = _organ_states.get("内在世界", {})
            if not _iw_extra and extra_state:
                _iw_extra = extra_state.get("内在世界", {})
            if _iw_extra:
                self.inner_world.set_pending_extra_state(_iw_extra)
                self._log(LogLevel.INFO, "内在世界额外状态已从快照恢复")
        # ===== pending_extra_state 恢复结束 =====

        # ===== 上下文数据恢复结束 =====
        # ===== v16.0: 恢复代码学习进度状态 =====
        # ★P1-1修复：增加空值保护，开关关闭时跳过
        if hasattr(self, 'code_learner') and self.code_learner is not None:
            _iw_extra = _organ_states.get("内在世界", {})
            if not _iw_extra and extra_state:
                _iw_extra = extra_state.get("内在世界", {})
            _cl_extra = _organ_states.get("代码学习", {})
            if not _cl_extra and extra_state:
                _cl_extra = extra_state.get("代码学习", {})
            # 兼容旧快照：代码学习进度可能在"内在世界"或"代码学习"键下
            _merged_extra = {}
            if _iw_extra:
                _merged_extra.update(_iw_extra)
            if _cl_extra:
                _merged_extra.update(_cl_extra)
            if _merged_extra:
                self.code_learner.set_pending_extra_state(_merged_extra)
                self._log(LogLevel.INFO, "代码学习进度状态已从快照恢复")
        # ===== 代码学习进度恢复结束 =====

        # ★启动优化：4个器官状态恢复并行化（线程池）
        from concurrent.futures import ThreadPoolExecutor as _TPE
        _state_tasks = []
        if hasattr(self, 'interest_model') and self.interest_model and hasattr(self.interest_model, 'load_state_snapshot'):
            _im_state = _organ_states.get("兴趣模型", {})
            if _im_state:
                _state_tasks.append(("兴趣模型", lambda: self.interest_model.load_state_snapshot(_im_state)))
        if hasattr(self, 'qica') and self.qica and hasattr(self.qica, 'load_state_snapshot'):
            _qica_state = _organ_states.get("QICA", {})
            if _qica_state:
                _state_tasks.append(("QICA", lambda: self.qica.load_state_snapshot(_qica_state)))
        if hasattr(self, 'hebbian_learner') and self.hebbian_learner and hasattr(self.hebbian_learner, 'load_state_snapshot'):
            _hb_state = _organ_states.get("赫布学习", {})
            if _hb_state:
                _state_tasks.append(("赫布学习", lambda: self.hebbian_learner.load_state_snapshot(_hb_state)))
        if hasattr(self, 'liver') and self.liver and hasattr(self.liver, 'load_state_snapshot'):
            _liver_state = _organ_states.get("肝", {})
            if _liver_state:
                _state_tasks.append(("肝", lambda: self.liver.load_state_snapshot(_liver_state)))
        # ★神经递质状态恢复
        if hasattr(self, 'neurotransmitters') and self.neurotransmitters and hasattr(self.neurotransmitters, 'load_state_snapshot'):
            _nt_state = _organ_states.get("神经递质", {})
            if _nt_state:
                try:
                    self.neurotransmitters.load_state_snapshot(_nt_state)
                    self._log(LogLevel.INFO, "神经递质状态已从快照恢复")
                except Exception as _nte:
                    self._log(LogLevel.WARNING, f"神经递质状态恢复异常: {_nte}")
        if _state_tasks:
            with _TPE(max_workers=min(4, len(_state_tasks))) as _sexec:
                _sfutures = [_sexec.submit(_fn) for _, _fn in _state_tasks]
                for (_name, _), _sf in zip(_state_tasks, _sfutures):
                    try:
                        _sf.result(timeout=10.0)
                        self._log(LogLevel.INFO, f"{_name}状态已从快照恢复(并行)")
                    except Exception as _se:
                        self._log(LogLevel.WARNING, f"{_name}状态恢复异常: {_se}")
        # ★v25.0新增：一次性清理快照中的旧噪音碎片节点
        self._cleanup_legacy_noise_nodes()
        self._inject_seed_memories()
        self._inject_seed_instincts() 
        # 步骤7: 启动所有器官并注册共振条件（★两阶段：并行start + 串行注册）
        _active_organs = [(name, o) for name, o in self.organs.items() if o is not None]
        _skipped = [name for name, o in self.organs.items() if o is None]
        for name in _skipped:
            self._log(LogLevel.INFO, f"{name}已跳过（功能开关关闭）")
        # 阶段1：并行启动器官（start()仅设状态+起线程，可安全并行）
        from concurrent.futures import ThreadPoolExecutor
        _org_start_ok = 0
        with ThreadPoolExecutor(max_workers=min(8, len(_active_organs))) as _exec:
            _futures = {_exec.submit(o.start): name for name, o in _active_organs}
            for _f, _name in _futures.items():
                try:
                    _f.result(timeout=10.0)
                    _org_start_ok += 1
                except Exception as _oe:
                    self._log(LogLevel.WARNING, f"器官{_name}启动异常: {_oe}")
        self._log(LogLevel.INFO, f"器官并行启动完成: {_org_start_ok}/{len(_active_organs)} 个")

        # ★P1: 初始化混合并行调度器（CPU密集型多进程真正并行 + IO密集型多线程）
        try:
            from nucleus.HybridParallelScheduler import get_hybrid_scheduler
            _hs = get_hybrid_scheduler()
            _hs._ensure_pools()  # 主动初始化进程池和线程池
            _stats = _hs.get_stats()
            self._log(LogLevel.INFO,
                     f"混合并行调度器初始化: 进程={_stats['process_workers']}个 "
                     f"(物理核={_stats['physical_cores']}), 线程={_stats['thread_workers']}个, "
                     f"内存={_stats['memory_gb']}GB")
        except Exception as _hs_e:
            self._log(LogLevel.WARNING, f"混合并行调度器初始化失败: {_hs_e}")
        # ★P1: 初始化结构化并行调度器（任务组隔离+协调者汇总+完整生命周期日志）
        try:
            from nucleus.StructuredParallelScheduler import (
                get_structured_parallel_scheduler,
            )
            _sps = get_structured_parallel_scheduler()
            _sps_stats = _sps.get_stats()
            self._log(LogLevel.INFO,
                     f"结构化并行调度器初始化: 任务组隔离+协调者模式, "
                     f"历史任务组={_sps_stats['total_groups']}个")
        except Exception as _sps_e:
            self._log(LogLevel.WARNING, f"结构化并行调度器初始化失败: {_sps_e}")
        # 阶段2：串行注册共振条件（InfoField注册需保序）
        for organ_name, organ in _active_organs:
            try:
                cond_ids = []
                for cond in organ.get_resonance_conditions():
                    cid = self.info_field.register_condition(
                        organ_name=cond["organ_name"],
                        event_types=cond.get("event_types", []),
                        min_priority=cond.get("min_priority", 1),
                        handler=organ.on_pulse,
                    )
                    cond_ids.append(cid)
                # ★阶段三子任务1：自动为所有器官订阅time.tick事件（受ENABLE_TIME_CORE开关保护）
                try:
                    import config as _cfg_time2
                    if getattr(_cfg_time2, "ENABLE_TIME_CORE", False):
                        from nucleus.const import SystemEvent
                        _tick_cid = self.info_field.register_condition(
                            organ_name=organ_name,
                            event_types=[SystemEvent.TIME_TICK],
                            min_priority=1,
                            handler=organ.on_pulse,  # _handle_pulse_safe会拦截并走on_time_tick
                        )
                        cond_ids.append(_tick_cid)
                except Exception as _tick_err2:
                    self._log(LogLevel.DEBUG, f"{organ_name} time.tick订阅失败: {_tick_err2}")
                organ.set_registered_cond_ids(cond_ids)
                self._log(LogLevel.DEBUG, f"{organ_name}共振条件已注册({len(cond_ids)}个)")
            except Exception as _re:
                self._log(LogLevel.WARNING, f"{organ_name}共振条件注册失败: {_re}")
        self._log(LogLevel.INFO, f"器官共振条件注册完成: {len(_active_organs)} 个器官")

        # ★启动优化：启动蒸馏异步化（不阻塞启动流程）
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            def _run_distill():
                try:
                    get_verification_learning_hub().run_startup_distill()
                except Exception as _se:
                    silent_exc(_se, "main.py:2050")
            _distill_t = threading.Thread(target=_run_distill, name="启动蒸馏", daemon=True)
            _distill_t.start()
        except Exception as _se:
            silent_exc(_se, "main.py:2054")

        # ★v24.0新增：注册自动保存事件处理器
        self._auto_save_cond_id = self.info_field.register_condition(
            organ_name="framework_auto_save",
            event_types=["snapshot.auto_save"],
            min_priority=1,
            handler=lambda p: self._on_auto_save(p)
        )
        # ★FIX(问题3): 注册动机约束订阅，使压力约束真正闭合到自我修改闸门
        self._self_modify_forbidden_until = 0.0
        self._constraint_cond_id = self.info_field.register_condition(
            organ_name="framework_self_modify_gate",
            event_types=["constraint.self_modify_forbidden", "constraint.aggressive_restructure_forbidden"],
            min_priority=1,
            handler=lambda p: self._on_self_modify_constraint(p)
        )
        # ★FIX(问题3续): 注册动机冲动订阅，使动机强度有明确订阅方并落盘可检索
        self._latest_motivation_urge = None
        self._motivation_urge_cond_id = self.info_field.register_condition(
            organ_name="framework_motivation_consumer",
            event_types=["motivation.urge"],
            min_priority=1,
            handler=lambda p: self._on_motivation_urge(p)
        )
        # ★FIX(激素无响应): 注册激素情绪输出订阅，使情绪检测真正有下游消费方
        self._latest_emotion = None
        self._emotion_cond_id = self.info_field.register_condition(
            organ_name="framework_emotion_consumer",
            event_types=["hormones.emotion_detected", "hormones.care_needed"],
            min_priority=1,
            handler=lambda p: self._on_emotion_signal(p)
        )
        # ★FIX(M4): 注册硬件启动计划的消费方
        self._latest_launch_plan = None
        self._launch_plan_cond_id = self.info_field.register_condition(
            organ_name="framework_launch_plan_consumer",
            event_types=["hardware.launch_plan"],
            min_priority=1,
            handler=lambda p: self._on_launch_plan(p)
        )
        # ===== 【自主健康守护】启动时执行一次系统健康诊断（★异步，不阻塞启动） =====
        try:
            _health_t = threading.Thread(
                target=self._startup_health_check,
                name="启动健康诊断",
                daemon=True
            )
            _health_t.start()
            self._log(LogLevel.INFO, "启动健康诊断已异步启动（后台线程，不阻塞启动）")
        except Exception as _he:
            self._log(LogLevel.WARNING, f"启动健康诊断异步启动失败，降级为同步: {_he}")
            self._startup_health_check()
        # ===== 自主健康守护结束 =====

        # ===== 【第五阶段·自主进化闭环】EvolutionLoop定期调用（★异步，不阻塞启动） =====
        try:
            from nucleus.evolution.EvolutionLoop import EvolutionLoop
            self.evolution_loop = EvolutionLoop(project_root=os.getcwd())
            self._evolution_loop_running = True
            self._evolution_loop_count = 0
            # ★PHASE12-P1-6（2026-09-06）：拆分两个语义不同的计数器。
            #   原只有一个 _evolution_loop_count，在循环体开头无条件 +1，
            #   于是「空转一轮」「异常一轮」「真正修了一轮」在日志里长得一模一样，
            #   无法回答「自主进化到底尝试过几次」——这正是内部协作者 N1
            #   （「修复完成 0/N，通过率 0%」无法判断是没干活还是干了没成）的延痛。
            #   现拆为两个口径，互不干扰：
            #     _evolution_loop_count   = 循环轮次（进入即计，含空转与异常）
            #     _evolution_attempt_count = 实际触发修复流程的轮次（有 problems 才计）
            #   二者差值即「空转/异常轮次」，可直接观测进化闭环的有效率。
            self._evolution_attempt_count = 0
            self._evolution_empty_rounds = 0   # 发现 0 个问题的轮次

            def _evolution_loop_worker():
                """自主进化循环工作线程：每30分钟发现一次问题。"""
                import random as _rd61e  # ★第61批 T2：函数内导入（规避 E402）
                import time as _time
                # ★主线第61批 T2/P1：随机轮间隔（默认 20~40 分钟，±10 分钟抖动），
                #   避免固定 1800s 与第180次心跳（T+30 分钟，4 任务重叠）精确撞车。
                #   灰度：ENABLE_RANDOM_INITIAL_DELAY=False → 恒为 1800s（复现旧行为）。
                _m61_evo_on = bool(getattr(config, "ENABLE_RANDOM_INITIAL_DELAY", True))
                _m61_evo_lo, _m61_evo_hi = 1200.0, 2400.0
                try:
                    _m61_evo_lo = float(config.get_task_offset("evolution_initial_delay_min", 1200))
                    _m61_evo_hi = float(config.get_task_offset("evolution_initial_delay_max", 2400))
                except Exception as _m61_evo_cfg_e:
                    self._log(LogLevel.DEBUG,
                              f"[自主进化] 首跑延迟范围读取失败，使用默认: "
                              f"{type(_m61_evo_cfg_e).__name__}: {_m61_evo_cfg_e}")
                _m61_evo_next = (_rd61e.uniform(_m61_evo_lo, _m61_evo_hi)
                                 if _m61_evo_on else 1800.0)
                self._log(LogLevel.INFO,
                          f"[自主进化] 循环已启动（首跑延迟{_m61_evo_next:.0f}秒，每30分钟一轮）")
                while getattr(self, '_evolution_loop_running', False):
                    try:
                        # ★第61批 T2：每轮重新取随机间隔，避免长期与心跳任务共振
                        _time.sleep(_m61_evo_next)
                        if _m61_evo_on:
                            _m61_evo_next = _rd61e.uniform(_m61_evo_lo, _m61_evo_hi)
                        if not getattr(self, '_evolution_loop_running', False):
                            break
                        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                        # NOTE: 单线程自增，当前无并发风险；free-threaded迁移时需加锁（★A2）
                        self._evolution_loop_count += 1
                        self._log(LogLevel.INFO,
                                 f"[自主进化] 第{self._evolution_loop_count}轮问题发现开始...")
                        # ★PHASE13-P1-2（2026-09-07）：把硬编码 max_issues=20 接回配置。
                        #   上一批（PHASE12-P1-1）把 executor 侧的吞吐上限外置到了
                        #   EVOLUTION_CONFIG，却漏了此处这个**真正的源头**——
                        #   候选池大小仍写死 20，于是「发现 17 个」的真实来源是
                        #   discover_all_issues 三源限流后的 unique[:20]，
                        #   并不是什么硬编码 17。这是内部协作者上一批的疏漏，特此补上。
                        #   同时放大候选池：P1-3 的僵尸冷却会先剔除不可修问题，
                        #   池子太小会导致过滤后填不满 12 步名额。
                        try:
                            import config as _evo_cfg
                            _discover_max = int(
                                getattr(_evo_cfg, "EVOLUTION_CONFIG", {})
                                .get("discover_max_issues", 60))
                        except Exception as e:
                            _discover_max = 60
                            logging.getLogger("pulse").warning(f"进化发现上限回退失败(沿用60): {type(e).__name__}: {e}")
                        _raw = self.evolution_loop.discover_all_issues(
                            log_file="logs/pulse.log",
                            max_issues=_discover_max,
                            include_code_review=True,
                        )
                        # ★R1修复（P0）：discover_all_issues 返回的是
                        #   {"issues": [...], "summary": {...}} 字典
                        #   （见 nucleus/evolution/EvolutionLoop.py:115-122），
                        #   而此处原代码直接把它当 list 使用，造成两个后果：
                        #   1) len(dict) == 2，日志恒显示"发现2个问题"，
                        #      真实问题数被永久掩盖（本轮实为0个也显示2个）；
                        #   2) 该 dict 被传入 repair_with_distillation 后，
                        #      在 _plan_multi_step_repair 的
                        #      `for _issue in issues`（SafeEvolutionExecutor.py:2114）
                        #      遍历字典得到的是**键名字符串** "issues"/"summary"，
                        #      随后 _issue.get("file","") 抛出
                        #      'str' object has no attribute 'get'
                        #      → 自主进化每30分钟崩溃一次（7小时13次，功能完全失效）。
                        #   此处统一归一化为 list[dict]，并兼容两种返回形态。
                        if isinstance(_raw, dict):
                            _issues = _raw.get("issues", []) or []
                        elif isinstance(_raw, (list, tuple)):
                            _issues = list(_raw)
                        else:
                            _issues = []
                        _issue_count = len(_issues)
                        if _issue_count <= 0:
                            self._evolution_empty_rounds += 1
                        self._log(LogLevel.INFO,
                                 f"[自主进化] 第{self._evolution_loop_count}轮完成: "
                                 f"发现{_issue_count}个问题"
                                 f"（累计{self._evolution_loop_count}轮/"
                                 f"空转{self._evolution_empty_rounds}轮/"
                                 f"已尝试修复{self._evolution_attempt_count}轮）")
                        if _issues and _issue_count > 0:
                            # 触发修复流程
                            # ★PHASE12-P1-6：只有真正进入修复才计 attempt，
                            #   与「循环轮次」区分开，让闭环有效率可被直接观测。
                            self._evolution_attempt_count += 1
                            try:
                                from nucleus.reasoning.SafeEvolutionExecutor import (
                                    get_safe_evolution_executor,
                                )
                                # ★9-问题1修复：repair_with_distillation 需要 self_inspector
                                #   才能读取问题代码片段（get_method_body）。原调用只传
                                #   _issues，导致 self_inspector 为 None → :588 的
                                #   `if self_inspector and _organ and _method` 恒不成立
                                #   → 代码片段永远取不到 → 修复被静默跳过。
                                #   现显式传入，让问题1接入的 self_inspector 源能被真正修复。
                                try:
                                    from nucleus.self_inspector import (
                                        get_self_inspector,
                                    )
                                    _inspector = get_self_inspector()
                                except Exception as e:
                                    _inspector = None
                                    logging.getLogger("pulse").warning(f"代码审查器初始化失败(跳过): {type(e).__name__}: {e}")
                                _executor = get_safe_evolution_executor()
                                _result = _executor.repair_with_distillation(
                                    _issues, self_inspector=_inspector)
                                # ★T3修复：原日志只显示 repaired/pass_rate，
                                #   而当问题缺少 organ/method 导致代码片段取不到时，
                                #   修复会被静默跳过，这里只能看到「0/N, 0%」，
                                #   无法判断是「修复失败」还是「压根没尝试」（内部协作者 N1 的痛点）。
                                #   补上处理数与跳过数，让「没干活」和「干了没成」可区分。
                                _repaired = _result.get('repaired', 0)
                                _skipped = _result.get('skipped_no_snippet', 0)
                                # ★PHASE13-P1-1：取真实原因分布。
                                #   skipped_no_snippet 如今只含「真拿不到代码片段」，
                                #   其余原因（待审批/安全拦截/转LLM/去重）在
                                #   skip_reasons 里，必须分开说，不能再混为一谈。
                                _skip_reasons = (_result.get('skip_reasons', {})
                                                 or {})
                                _log_line = (f"[自主进化] 修复完成: "
                                             f"{_repaired}/{_issue_count}个, "
                                             f"通过率={_result.get('pass_rate', 0):.0%}, "
                                             f"处理{_result.get('processed', 0)}个")
                                if _skipped:
                                    _log_line += (f"（{_skipped}个因缺少organ/method"
                                                  f"无法定位代码）")
                                if _skip_reasons:
                                    _rs = "，".join(
                                        f"{_k}×{_v}" for _k, _v in sorted(
                                            _skip_reasons.items(),
                                            key=lambda kv: -kv[1]))
                                    _log_line += f"；未修复原因: {_rs}"
                                if not _repaired and not _skipped and _skip_reasons:
                                    _log_line += "（均为已知不可自动修复项，非修复失败）"
                                # ★PHASE12 可观测性：把第 2 批三项修复的效果打进主日志，
                                #   否则改了也看不见——内部协作者读日志时无从判断门禁有没有生效。
                                _llm_gated = _result.get('llm_skipped_pending', 0)
                                if _llm_gated:
                                    _log_line += (f"；LLM门禁拦截{_llm_gated}次"
                                                  f"（已有待审批补丁，避免重复调用）")
                                _renamed = _result.get('type_alias_renamed', 0)
                                if _renamed:
                                    _log_line += (f"；修正{_renamed}个类型名笔误")
                                self._log(LogLevel.INFO, _log_line)
                            except Exception as _re:
                                self._log(LogLevel.WARNING,
                                         f"[自主进化] 修复流程异常: {_re}")
                    except Exception as _e:
                        self._log(LogLevel.WARNING,
                                 f"[自主进化] 循环异常: {_e}")
                        _time.sleep(60)  # 异常后等待1分钟再重试

            _evolution_t = threading.Thread(
                target=_evolution_loop_worker,
                name="自主进化循环",
                daemon=True,
            )
            _evolution_t.start()
            self._log(LogLevel.INFO,
                     "自主进化循环已启动（每30分钟自动发现问题并修复）")
        except Exception as _evo_e:
            self._log(LogLevel.WARNING,
                     f"自主进化循环启动失败（不影响主链路）: {_evo_e}")
            self.evolution_loop = None
        # ===== 自主进化闭环结束 =====

        # ===== 【直觉冷启动】导入直觉种子模式 =====
        try:
            if hasattr(self, 'risk_perception') and self.risk_perception:
                self.risk_perception.seed_intuition_patterns()
        except Exception as _se:
            silent_exc(_se, "main.py:2299")
        # ===== 直觉冷启动结束 =====

        # ===== 启动同步确认：验证所有器官的共振条件已正确注册 =====
        _active_conditions = self.info_field.get_stats().get("active_conditions", 0)
        _expected_organs = len([o for o in self.organs.values() if o is not None])
        if _active_conditions < _expected_organs:
            self._log(LogLevel.WARNING,
                     f"启动同步检查: 活跃条件数({_active_conditions})少于器官数({_expected_organs})，"
                     f"差值={_expected_organs - _active_conditions}个，可能存在注册时序问题")
        else:
            self._log(LogLevel.INFO,
                     f"启动同步确认: 全部{_active_conditions}个活跃条件已注册，与{_expected_organs}个器官一致 ✅")
        # ===== 启动同步确认结束 =====

        # 步骤9: 发布全局 system.boot 脉冲
        boot_pulse = self.pulse_core.emit(
            source_organ="main",
            event_type=SystemEvent.BOOT,
            payload={"version": config.SYSTEM_VERSION, "time": time.time()},
            priority=10,
            layer=PulseLayer.L0_LIFELINE,  # v9.5: L0生命线层
        )
        self.info_field.publish(boot_pulse)

        # ★FIX(M4): 启动时触发一次硬件评估，使硬件启动器不再死器官
        try:
            _assess_pulse = self.pulse_core.emit(
                source_organ="main",
                event_type="hardware.assess",
                payload={},
                priority=3,
                layer="L3",
            )
            self.info_field.publish(_assess_pulse)
        except Exception as _se:
            silent_exc(_se, "main.py:2335")

        # 步骤10: 系统就绪，输出状态摘要
        stats_node = self.node_pool.get_stats()
        stats_field = self.info_field.get_stats()
        self._log(LogLevel.INFO, f"✅ {config.SYSTEM_NAME} 已就绪")
        instinct_count = stats_node.get('instinct_count', 0)
        self._log(LogLevel.INFO,
                  f"知识节点: {stats_node['total_nodes']} "
                  f"(热{stats_node['hot_count']}/温{stats_node['warm_count']}/冷{stats_node['cold_count']}"
                  f"/本能{instinct_count})")
        self._log(LogLevel.INFO,
                  f"信息场: {stats_field['total_published']}次发布 "
                  f"{stats_field['active_conditions']}个活跃条件 "
                  f"(分层调度: {len(stats_field.get('layer_pools', {}))}层线程池)")
        self._log(LogLevel.INFO, f"知识树: {self.knowledge_tree.get_stats()['total_paths']}条路径")
        # ===== P1预埋启用: 输出数字生命注册表标识 =====
        registry = getattr(config, 'DIGITAL_LIFE_REGISTRY', {})
        instance_id = registry.get("instance_id", "TTP-001-UNKNOWN")
        global_id = registry.get("global_personality_id", "TTP-001")
        self._log(LogLevel.INFO, f"数字生命标识: 全局人格={global_id}, 实例={instance_id}")
        self._log(LogLevel.INFO, "等待交互...")
        print()

    # ========================================================================
    # 框架优雅退出（v9.5增强: InfoField线程池关闭）
    # ========================================================================

    def stop(self):
        """优雅退出：停止所有器官、保存快照、关闭信息场线程池、输出运行统计"""
        _stop_t0 = time.time()
        self._log(LogLevel.INFO, "收到退出信号，开始优雅关闭...")

        # ===== 修复：在停止器官之前先保存上下文数据 =====
        try:
            _ctx_snapshot = get_context_snapshot()
            _ctx_result = _ctx_snapshot.save_all(
                inner_world=self.inner_world if hasattr(self, 'inner_world') else None,
                self_awareness=self.self_awareness if hasattr(self, 'self_awareness') else None
            )
            self._log(LogLevel.INFO, f"上下文保存: 对话记忆={_ctx_result['conversation_memory']} "
                     f"推理链={_ctx_result['inference_trace']} "
                     f"搜索经验={_ctx_result['search_experience']} "
                     f"学习目标={_ctx_result['learning_goals']} "
                     f"代码进度={_ctx_result['code_progress']}")
        except Exception as _ctx_e:
            self._log(LogLevel.WARNING, f"上下文保存异常: {_ctx_e}")
        # ===== 上下文提前保存结束 =====

        # ★P1-5修复：先给所有器官发STOP脉冲，再依次停止
        self._log(LogLevel.INFO, "发送停止脉冲到所有器官...")
        if self.info_field and self.pulse_core:
            stop_pulse = self.pulse_core.emit(
                source_organ="main",
                event_type=SystemEvent.STOP,
                priority=10,
                layer=PulseLayer.L0_LIFELINE,
            )
            self.info_field.publish(stop_pulse)
        
        # 等待异步脉冲被分发处理
        time.sleep(0.3)
        
        self._running = False
        
        # ★终止优化：器官停止并行化（心脏最后停）
        # ★v25.1 P0修复: 添加每个器官停止耗时日志 + 避免with上下文管理器阻塞
        from concurrent.futures import ThreadPoolExecutor as _TPE
        _stop_tasks = []
        for organ_name, organ in list(self.organs.items()):
            if organ_name == "心脏":
                continue  # 心脏最后停
            if organ and getattr(organ, "is_running", False):
                _stop_tasks.append((organ_name, organ))
        if _stop_tasks:
            _stopexec = _TPE(max_workers=min(8, len(_stop_tasks)))
            _sfutures = {}
            _stop_start = time.time()
            for _name, _o in _stop_tasks:
                def _stop_with_log(_n=_name, _organ=_o):
                    _t0 = time.time()
                    try:
                        _organ.stop()
                        _dur = time.time() - _t0
                        if _dur > 3.0:
                            self._log(LogLevel.WARNING,
                                     f"器官停止耗时较长: {_n} ({_dur:.1f}s)")
                        else:
                            self._log(LogLevel.DEBUG,
                                     f"器官停止完成: {_n} ({_dur:.2f}s)")
                    except Exception as _se:
                        self._log(LogLevel.WARNING,
                                 f"器官{_n}停止异常: {_se} (耗时{time.time()-_t0:.1f}s)")
                _sfutures[_name] = _stopexec.submit(_stop_with_log)
            # 等待所有器官停止，每个最多等15秒
            for _name, _sf in _sfutures.items():
                try:
                    _sf.result(timeout=15.0)
                except Exception:
                    self._log(LogLevel.WARNING,
                             f"器官{_name}停止超时(>15s)，将在后台强制结束")
            # 不使用with上下文管理器，避免卡住的线程阻塞整个退出
            _stopexec.shutdown(wait=False)
            self._log(LogLevel.INFO,
                     f"器官并行停止完成: {len(_stop_tasks)}个 "
                     f"(耗时{time.time()-_stop_start:.1f}s, 总耗时{time.time()-_stop_t0:.1f}s)")

        # ★P0-1修复：器官停止后立即停止记忆验证后台线程（冷节点召回/记忆验证），
        # 避免退出期间后台线程仍在读写node_pool，与快照保存争抢锁导致退出挂起
        if hasattr(self, 'node_pool') and self.node_pool:
            try:
                self.node_pool.stop_memory_verification_loop()
                self._log(LogLevel.INFO, "记忆验证后台线程已停止")
            except Exception as _bg_e:
                self._log(LogLevel.WARNING, f"停止记忆验证线程异常: {_bg_e}")

        # 最后停止心脏
        if hasattr(self, 'heart') and self.heart and self.heart.is_running:
            self.heart.stop()

        # ★修复P0-4: 反注册所有器官的共振条件，防止 stop 后残留脉冲仍调用已停止器官（幽灵处理），
        # 以及同实例 stop→start 时同一处理器被注册成两份（双倍分发）
        if hasattr(self, 'info_field') and self.info_field:
            for _on, _og in list(self.organs.items()):
                _ids = _og.get_registered_cond_ids()
                if _ids:
                    for _cid in _ids:
                        self.info_field.unregister_condition(_cid)
                    _og.clear_registered_cond_ids()
            # ★FIX: 反注册自动保存条件，避免 stop→start 重复注册导致重复自动保存
            _auto_save_cid = getattr(self, "_auto_save_cond_id", None)
            if _auto_save_cid:
                self.info_field.unregister_condition(_auto_save_cid)
                self._auto_save_cond_id = None
            # ★FIX: 反注册自我修改约束条件
            _constraint_cid = getattr(self, "_constraint_cond_id", None)
            if _constraint_cid:
                self.info_field.unregister_condition(_constraint_cid)
                self._constraint_cond_id = None
            # ★FIX: 反注册动机冲动订阅
            _urge_cid = getattr(self, "_motivation_urge_cond_id", None)
            if _urge_cid:
                self.info_field.unregister_condition(_urge_cid)
                self._motivation_urge_cond_id = None
            # ★FIX: 反注册激素情绪订阅
            _emotion_cid = getattr(self, "_emotion_cond_id", None)
            if _emotion_cid:
                self.info_field.unregister_condition(_emotion_cid)
                self._emotion_cond_id = None
            # ★FIX: 反注册硬件启动计划订阅
            _launch_cid = getattr(self, "_launch_plan_cond_id", None)
            if _launch_cid:
                self.info_field.unregister_condition(_launch_cid)
                self._launch_plan_cond_id = None
            self._log(LogLevel.INFO, "已反注册所有器官共振条件")

        # ===== 新增: 收集生命连续性状态（★并行化） =====
        extra_state = {}
        from concurrent.futures import ThreadPoolExecutor as _TPE2
        _collect_results: dict[str, Any] = {}

        def _collect_subcon():
            if hasattr(self, 'organs'):
                subcon = self.organs.get("潜意识")
                if subcon and hasattr(subcon, 'get_life_state'):
                    return subcon.get_life_state()
            return None

        def _collect_hormones():
            if hasattr(self, 'organs'):
                hormones = self.organs.get("激素")
                if hormones:
                    _result = {}
                    if hasattr(hormones, '_current_emotion'):
                        _result["emotion_state"] = {
                            "emotion": hormones.get_current_emotion(),
                            "intensity": hormones.get_emotion_intensity(),
                        }
                    if hormones.emotion_timeline:
                        _result["emotion_timeline"] = hormones.emotion_timeline[-5:]
                    _result["social_memory"] = hormones.social_memory
                    return _result
            return None

        def _collect_narrative():
            if hasattr(self, 'organs'):
                narrative = self.organs.get("叙事自我")
                if narrative:
                    _result = {
                        "narrative_count": len(narrative.narrative_events),
                        "values": getattr(narrative, 'dynamic_values', {}),
                    }
                    if narrative.weekly_reports:
                        _recent_reports = narrative.weekly_reports[-5:]
                        _result["weekly_reports"] = [
                            {
                                "id": r.get("id", ""),
                                "timestamp": r.get("timestamp", 0),
                                "summary": r.get("summary", "")[:300],
                                "values": r.get("values", {}),
                                "values_changed": r.get("values_changed", {}),
                                "life_stage": r.get("life_stage", ""),
                            }
                            for r in _recent_reports
                        ]
                    return _result
            return None

        with _TPE2(max_workers=3) as _cexec:
            _cf1 = _cexec.submit(_collect_subcon)
            _cf2 = _cexec.submit(_collect_hormones)
            _cf3 = _cexec.submit(_collect_narrative)
            try:
                _r1 = _cf1.result(timeout=5.0)
                if _r1:
                    extra_state["life_state"] = _r1
            except Exception as _se:
                silent_exc(_se, "main.py:2552")
            try:
                _r2 = _cf2.result(timeout=5.0)
                if _r2:
                    extra_state.update(_r2)
            except Exception as _se:
                silent_exc(_se, "main.py:2558")
            try:
                _r3 = _cf3.result(timeout=5.0)
                if _r3:
                    extra_state.update(_r3)
            except Exception as _se:
                silent_exc(_se, "main.py:2564")
        self.snapshot.set_extra_state(extra_state)
        # ★神经递质状态持久化
        if hasattr(self, 'neurotransmitters') and self.neurotransmitters:
            try:
                _nt_state = self.neurotransmitters.get_state_snapshot()
                if _nt_state:
                    self.snapshot.merge_organ_extra_state("神经递质", _nt_state)
            except Exception as _se:
                silent_exc(_se, "main.py:2573")
        # 合并内在世界的额外状态
        if hasattr(self, 'inner_world') and self.inner_world:
            try:
                _iw_state = self.inner_world.get_pending_extra_state()
                if _iw_state:
                    self.snapshot.merge_organ_extra_state("内在世界", _iw_state)
            except Exception as _se:
                silent_exc(_se, "main.py:2581")
        # ★v16.0: 收集代码学习器官的进度状态
        if hasattr(self, 'code_learner') and self.code_learner is not None:
            try:
                _cl_state = self.code_learner.get_pending_extra_state()
                if _cl_state:
                    self.snapshot.merge_organ_extra_state("代码学习", _cl_state)
            except Exception as _se:
                silent_exc(_se, "main.py:2589")
        # ★FIX(规则4): 收集兴趣模型/QICA领域库/赫布连接三处常驻状态，随快照持久化
        try:
            if hasattr(self, 'interest_model') and self.interest_model and hasattr(self.interest_model, 'get_state_snapshot'):
                _im_state = self.interest_model.get_state_snapshot()
                if _im_state:
                    self.snapshot.merge_organ_extra_state("兴趣模型", _im_state)
        except Exception as _se:
            silent_exc(_se, "main.py:2597")
        try:
            if hasattr(self, 'qica') and self.qica and hasattr(self.qica, 'get_state_snapshot'):
                _qica_state = self.qica.get_state_snapshot()
                if _qica_state:
                    self.snapshot.merge_organ_extra_state("QICA", _qica_state)
        except Exception as _se:
            silent_exc(_se, "main.py:2604")
        try:
            if hasattr(self, 'hebbian_learner') and self.hebbian_learner and hasattr(self.hebbian_learner, 'get_state_snapshot'):
                _hb_state = self.hebbian_learner.get_state_snapshot()
                if _hb_state:
                    self.snapshot.merge_organ_extra_state("赫布学习", _hb_state)
        except Exception as _se:
            silent_exc(_se, "main.py:2611")
        # ★F2：持久化肝脏融合/压缩冷却计时器，避免重启后冷却失效导致重试风暴
        try:
            if hasattr(self, 'liver') and self.liver and hasattr(self.liver, 'get_state_snapshot'):
                _liver_state = self.liver.get_state_snapshot()
                if _liver_state:
                    self.snapshot.merge_organ_extra_state("肝", _liver_state)
        except Exception as _se:
            silent_exc(_se, "main.py:2619")
        # ★P4修复（退出挂起）：保存快照前先关闭并行调度线程池与探查器。
        # 背景：大脑皮层并行审查/探查任务提交到 parallel_scheduler 线程池，退出时若仍在写
        # node_pool，会与 snapshot.save() 争抢锁导致退出挂起（10:16 重启需强制退出）。
        # 先关池可让排队中任务不再执行；再加下方 90s 超时保护兜底任意阻塞。
        try:
            from nucleus.parallel_scheduler import shutdown_parallel_scheduler
            shutdown_parallel_scheduler()
            from nucleus.self_inspector import shutdown_self_inspector
            shutdown_self_inspector()
        except Exception as _se:
            silent_exc(_se, "main.py:2630")

        # ★P0-1修复：快照保存前提前关闭推理进程池，避免保存期间仍有任务提交导致子进程异常
        try:
            from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
            _pool = get_reasoning_pool()
            if _pool:
                _pool.shutdown()
                self._log(LogLevel.INFO, f"推理进程池已提前关闭 (耗时{time.time()-_stop_t0:.1f}s)")
        except Exception as _se:
            silent_exc(_se, "main.py:2640")

        self._log(LogLevel.INFO, f"开始保存快照 (耗时{time.time()-_stop_t0:.1f}s)")

        # 保存快照（★P4修复：90s 超时保护，防任意后台阻塞导致退出挂起）
        try:
            import threading as _save_th

            def _do_save_snapshots():
                try:
                    # ★P0-1修复(2026-09-03)：退出时强制全量保存。
                    # 增量保存8700节点需170s(读全文件+解析+写回)，全量仅9s，
                    # 退出时距上次全量保存近会触发增量导致90s超时。
                    self.snapshot.save(force_full=True)
                    self.snapshot.save_l1()
                    if hasattr(self, 'instinct_snapshot') and self.instinct_snapshot:
                        self.instinct_snapshot.save(force_full=True)
                except Exception as _e:
                    self._log(LogLevel.WARNING, f"快照保存异常: {_e}")

            _t = _save_th.Thread(target=_do_save_snapshots, name="exit-snapshot-save", daemon=True)
            _t.start()
            # ★第80批 T3：退出前强制等完整全量保存完成（删除"数据由下次启动增量恢复"无实物承诺）。
            #   join 超时放大到 300s，确保大快照也能落盘；超时则如实告警未保存变更将丢失。
            _t.join(timeout=300)
            if _t.is_alive():
                self._log(LogLevel.WARNING,
                          "快照保存超时(300s)，退出前未能完成全量保存，未保存变更将丢失（不谎称增量恢复）")
            else:
                self._log(LogLevel.INFO, "快照保存完成（退出前）")
        except Exception as _e:
            self._log(LogLevel.WARNING, f"快照保存调度异常: {_e}")
        self._log(LogLevel.INFO, f"快照保存阶段结束 (耗时{time.time()-_stop_t0:.1f}s)")
        # 关闭推理进程池（兜底：若上方提前关闭失败，此处再关一次；幂等）
        try:
            from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
            _pool = get_reasoning_pool()
            if _pool:
                _pool.shutdown()
        except Exception as _se:
            silent_exc(_se, "main.py:2678")
        
        # v9.5新增: 优雅关闭信息场所有分层线程池
        if hasattr(self, 'info_field') and self.info_field:
            self._log(LogLevel.INFO, "关闭信息场分层调度线程池...")
            self.info_field.shutdown()
            self._log(LogLevel.INFO, "信息场线程池已关闭")

        # ★P1: 关闭混合并行调度器（进程池+线程池），确保真正并行的进程被正确回收
        try:
            from nucleus.HybridParallelScheduler import get_hybrid_scheduler
            _hs = get_hybrid_scheduler()
            if _hs and not _hs._shutdown_complete:
                self._log(LogLevel.INFO, "关闭混合并行调度器（进程池+线程池）...")
                _hs.shutdown(wait=True, timeout=30.0)
                self._log(LogLevel.INFO, "混合并行调度器已关闭")
        except Exception as _hs_e:
            self._log(LogLevel.WARNING, f"混合并行调度器关闭异常: {_hs_e}")
        # ★P1: 关闭结构化并行调度器（任务组线程池），确保并行任务被正确回收
        try:
            from nucleus.StructuredParallelScheduler import (
                get_structured_parallel_scheduler,
            )
            _sps = get_structured_parallel_scheduler()
            if _sps:
                _sps_stats = _sps.get_stats()
                self._log(LogLevel.INFO,
                         f"关闭结构化并行调度器... (历史任务组={_sps_stats['total_groups']}个, "
                         f"活跃={_sps_stats['active_groups']}个)")
                _sps.shutdown(wait=True, timeout=30.0)
                self._log(LogLevel.INFO, "结构化并行调度器已关闭")
        except Exception as _sps_e:
            self._log(LogLevel.WARNING, f"结构化并行调度器关闭异常: {_sps_e}")

        # ★P0批次3（规则4 违宪收敛）: 统一复位模块级单例，使重启回到器官零状态。
        #   不再仅调用实例 shutdown()（旧实例仍被全局变量持有，重启会复用），而是置空
        #   全局变量，下次 get_xxx() 重建全新实例。覆盖全部 8 个模块级全局单例。
        _shutdown_singletons = [
            ("nucleus.external_executor", "shutdown_global_executor"),
            ("nucleus.parallel_scheduler", "shutdown_parallel_scheduler"),
            ("nucleus.runtime_metrics", "shutdown_runtime_metrics"),
            ("nucleus.api_rate_limiter", "shutdown_api_limiter"),
            ("nucleus.InsightBoard", "shutdown_insight_board"),
            ("nucleus.self_inspector", "shutdown_self_inspector"),
            ("nucleus.self_probe", "shutdown_probe_orchestrator"),
            ("nucleus.tooling_runner", "shutdown_tooling_runner"),
            ("nucleus.mnemosyne.verification_learning_hub", "shutdown_verification_learning_hub"),
            # ★P1: 补齐剩余模块级全局单例复位，覆盖规则4 违宪债（仅置空不足以回到零状态）
            ("nucleus.reasoning.ReasoningWorkerPool", "shutdown_reasoning_pool"),
            ("nucleus.mnemosyne.experience_pool", "shutdown_experience_pool"),
            ("nucleus.mnemosyne.ContextSnapshot", "shutdown_context_snapshot"),
            ("nucleus.mnemosyne.ReasoningExperience", "shutdown_reasoning_experience"),
            ("nucleus.genesis.StreamMiner", "shutdown_stream_miner"),
            ("nucleus.reasoning.EvolutionSandbox", "shutdown_evolution_sandbox"),
            ("nucleus.reasoning.FailureTracker", "shutdown_failure_tracker"),
            ("nucleus.reasoning.AutonomousDeriver", "shutdown_autonomous_deriver"),
            ("nucleus.CompanionBridge", "shutdown_companion_bridge"),
            ("nucleus.search_scheduler", "shutdown_search_scheduler"),
            ("nucleus.diagnostics", "shutdown_diagnostics"),
        ]
        # ★终止优化：单例关闭并行化
        def _shutdown_one(_mod: str, _fn: str):
            try:
                _m = __import__(_mod, fromlist=[_fn])
                getattr(_m, _fn)()
            except Exception as _se:
                silent_exc(_se, "main.py:2744")
        with _TPE(max_workers=min(8, len(_shutdown_singletons))) as _singexec:
            _singfutures = [_singexec.submit(_shutdown_one, _m, _f) for _m, _f in _shutdown_singletons]
            for _sf in _singfutures:
                try:
                    _sf.result(timeout=10.0)
                except Exception as _se:
                    silent_exc(_se, "main.py:2751")
        self._log(LogLevel.INFO, f"单例并行关闭完成: {len(_shutdown_singletons)}个")

        # 输出运行统计
        stats_pulse = self.pulse_core.get_stats()
        stats_pool = self.node_pool.get_stats() 
        self._log(LogLevel.INFO, "运行统计:")
        self._log(LogLevel.INFO,
                  f"脉冲: 发射{stats_pulse['total_emitted']} "
                  f"完成{stats_pulse['total_completed']} "
                  f"超时{stats_pulse['total_timeout']}")
        self._log(LogLevel.INFO,
                  f"节点: 总数{stats_pool['total_nodes']} "
                  f"添加{stats_pool['total_added']} "
                  f"淘汰{stats_pool['total_removed']}")
        # ★v25.1: 退出前诊断——记录残留非守护线程和子进程，帮助定位退出不干净的问题
        try:
            import multiprocessing as _mp_diag
            import threading as _th_diag
            _alive = [t for t in _th_diag.enumerate() if t.is_alive() and not t.daemon and t != _th_diag.current_thread()]
            if _alive:
                self._log(LogLevel.WARNING,
                         f"退出诊断: 仍有{len(_alive)}个非守护线程存活: "
                         + ", ".join(f"{t.name}(存活{t.is_alive()})" for t in _alive[:5]))
            # 检查子进程（active_children只返回multiprocessing创建的子进程）
            _children = _mp_diag.active_children()
            if _children:
                self._log(LogLevel.WARNING,
                         f"退出诊断: 仍有{len(_children)}个子进程存活: "
                         + ", ".join(f"pid={c.pid},name={c.name}" for c in _children[:5]))
                # 尝试终止残留子进程
                for _c in _children:
                    try:
                        _c.terminate()
                    except Exception as _se:
                        silent_exc(_se, "main.py:2786")
        except Exception as _diag_e:
            self._log(LogLevel.DEBUG, f"退出诊断异常: {_diag_e}")

        # ★FIX(退出顺序): 改为"停止指令已发出"，真正的"已关闭"在 main.py 的 os._exit 前输出
        #   避免"已关闭"消息后还有后台异步任务（肝融合、Snapshot 增量保存等）在运行造成的歧义
        self._log(LogLevel.INFO, f"{config.SYSTEM_NAME} 停止指令已发出，正在清理后台任务... (总耗时{time.time()-_stop_t0:.1f}s)")
        print()

    # ========================================================================
    # 内部方法
    # ========================================================================
    def _cleanup_legacy_noise_nodes(self):
        """
        ★v25.0新增：启动时清理旧快照中的噪音碎片节点。
        只处理L1节点，L2/L3不碰。
        """
        try:
            from nucleus.knowledge_noise_filter import is_path_fragment_word
            _cleaned = 0
            _l1_nodes = self.node_pool.query(evol_level="L1", limit=2000)
            for _node in _l1_nodes:
                _path = getattr(_node, 'space_path', '')
                _value = str(_node.value) if _node.value else ""
                # 检查路径是否含碎片段或重复段
                _parts = _path.strip('/').split('/')
                _has_fragment = any(is_path_fragment_word(p) or "(" in p or "（" in p or "拼音" in p for p in _parts)
                # 检查重复段
                _has_duplicate = len(_parts) != len(set(_parts))
                # 检查值是否明显是搜索噪音（拼音/读音/笔顺）
                _is_search_noise = any(kw in _value for kw in ["拼音", "读音", "笔顺", "部首", "百度百科"])
                
                if _has_fragment or _has_duplicate or _is_search_noise:
                    # 从节点池移除
                    if hasattr(self.node_pool, 'remove_by_id'):
                        self.node_pool.remove_by_id(_node.node_id)
                    else:
                        _node.ephemeral = True
                        _node.state = "dormant"
                        _node.trust_score = max(5.0, getattr(_node, 'trust_score', 30.0) - 30.0)
                    _cleaned += 1
            
            if _cleaned > 0:
                self._log(LogLevel.INFO, f"旧噪音节点清理: 标记{_cleaned}个L1节点为临时/休眠")
        except Exception as _e:
            self._log(LogLevel.WARNING, f"旧噪音节点清理异常: {_e}")
            
    def _inject_seed_memories(self):
        """注入5条核心种子记忆为 L3 智慧节点（永久锁定，不可淘汰）"""
        for seed in config.SEED_MEMORIES:
            # ★任务8：node_id 由 value 确定性生成 → 只改了路径的种子记忆会命中幂等检查而被跳过。
            #   若该内容已存在于**旧路径**下，走「路径迁移」而非新建（不删节点），
            #   保证 config.SEED_MEMORIES 的路径始终是唯一事实来源。
            _exist = self.node_pool.find_by_value(seed["value"], level=PulseNode.EVOL_L3)
            if _exist is not None and self.node_pool.update_node_path(_exist.node_id, seed["space_path"]):
                self.knowledge_tree.register_path(seed["space_path"])
                continue
            node = PulseNode(
                value=seed["value"],
                keywords=seed["keywords"],
                source_organ="main",
                evol_level=PulseNode.EVOL_L3,
                importance=PulseNode.IMPORTANCE_S,
                abstraction=1.0,
                space_path=seed["space_path"],
            )
            node.state = "locked"
            self.frequency_codec.encode_node(node)
            self.node_pool.add(node)
            self.knowledge_tree.register_path(seed["space_path"])
        self._log(LogLevel.INFO, f"种子记忆注入完成: {len(config.SEED_MEMORIES)} 条 L3 智慧节点")
    def _inject_seed_instincts(self):
        """注入首批种子本能（L4本能节点，永久锁定）"""
        if hasattr(config, 'SEED_INSTINCTS'):
            for seed in config.SEED_INSTINCTS:
                node = PulseNode(
                    value=seed["value"],
                    keywords=seed.get("keywords", []),
                    source_organ="main",
                    evol_level=PulseNode.EVOL_L3,
                    importance=PulseNode.IMPORTANCE_S,
                    abstraction=1.0,
                    space_path="/本能/核心",
                )
                node.state = "locked"
                node.instinct = True
                node.instinct_at = time.time()
                node.instinct_last_use = time.time()
                self.frequency_codec.encode_node(node)
                self.node_pool.add(node)
                # 同时载入本能池
                if hasattr(self.node_pool, 'load_instincts'):
                    self.node_pool.load_instincts([node])
            self._log(LogLevel.INFO, f"种子本能注入完成: {len(config.SEED_INSTINCTS)} 条 L4 本能节点")
    def _startup_health_check(self):
        """
        【自主健康守护】启动时自动执行系统健康诊断（后台线程异步执行）。
        
        检查项：
        1. 代码问题检测（SelfInspector）
        2. 知识库完整性（快照校验）
        3. 器官在线状态
        4. 上下文数据恢复状态
        
        诊断结果写入日志和洞察黑板。
        """
        self._log(LogLevel.INFO, "执行启动健康诊断...")
        
        _issues_found = 0
        _warnings = []
        
        # 1. 代码问题检测
        try:
            from nucleus.self_inspector import (
                get_self_inspector,  # type: ignore[possibly-unbound]
            )
            _inspector = get_self_inspector()  # type: ignore[possibly-unbound]
            _code_issues = _inspector.detect_code_issues()
            _code_count = len(_code_issues) if _code_issues else 0
            # ★v17.0 F3修复：展示有效问题数（排除legacy历史遗留）
            _lifecycle = _inspector.get_issue_lifecycle_stats()
            _active_issues = _lifecycle.get("new", 0) + _lifecycle.get("persistent", 0) + _lifecycle.get("reopened", 0)
            
            # ★v18.0新增：启动时修复验证闭环
            # 对比本次启动与上次退出时的代码问题数量
            _last_issue_count = _lifecycle.get("last_total", 0)
            if _last_issue_count > 0:
                _delta = _code_count - _last_issue_count
                if _delta < 0:
                    # 问题减少了！发射叙事事件记录
                    _trend_msg = f"代码问题减少了{abs(_delta)}个（{_last_issue_count}→{_code_count}），质量持续改善"
                    self._log(LogLevel.INFO, f"  代码趋势: ✅ {_trend_msg}")
                    # 发射叙事事件
                    try:
                        _narrative_pulse = self.pulse_core.emit(
                            source_organ="main",
                            event_type="narrative.record",
                            payload={
                                "content": f"曈曈的代码质量持续改善——{_trend_msg}",
                                "event_type": "code_health",
                                "user_name": "系统",
                                "emotional_tone": "positive",
                            },
                            priority=4,
                            layer="L2"
                        )
                        self.info_field.publish(_narrative_pulse)
                    except Exception as _se:
                        silent_exc(_se, "main.py:2934")
                    # ★v18.0新增：记录修复里程碑到洞察黑板
                    try:
                        _board = _safe_get_insight_board()
                        if _board is not None:
                            _board.post(
                                insight_type="code_health_improvement",
                                content=_trend_msg,
                                source_loop="代码审视闭环",
                                related_dimension="代码健康",
                                # 第九批 B-3：原硬编码 0.95
                                confidence=_evidence_conf(0.95, "health", [_trend_msg]),
                                keywords=["代码质量", "修复验证", "趋势好转"]
                            )
                    except Exception as _se:
                        silent_exc(_se, "main.py:2951")
                elif _delta > 0:
                    self._log(LogLevel.WARNING, f"  代码趋势: ⚠️ 问题增加了{_delta}个，需关注")
                else:
                    self._log(LogLevel.INFO, "  代码趋势: → 问题数量稳定")
            else:
                # 首次启动，记录基线
                self._log(LogLevel.INFO, f"  代码趋势: 📊 首次记录基线（{_code_count}个问题）")
            
            if _code_count > 10:
                _warnings.append(f"代码问题: {_code_count}个（建议优先修复P0级）")
                _issues_found += 1
            self._log(LogLevel.INFO, f"  代码健康: {_active_issues}个有效问题 (共{_code_count}个，{_lifecycle.get('legacy', 0)}个历史遗留)")
        except Exception as e:
            self._log(LogLevel.WARNING, f"  代码检测异常: {e}")
        
        # 2. 知识库状态
        try:
            _stats = self.node_pool.get_stats()
            _total = _stats.get("total_nodes", 0)
            _l3 = _stats.get("evol_distribution", {}).get("L3", 0)
            if _total < 50:
                _warnings.append(f"知识节点仅{_total}个，知识库尚小")
                _issues_found += 1
            self._log(LogLevel.INFO, f"  知识库: {_total}节点, L3={_l3}个")
        except Exception as e:
            self._log(LogLevel.WARNING, f"  知识库检测异常: {e}")
        
        # 3. 器官在线状态
        try:
            _offline = [name for name, organ in self.organs.items() 
                       if organ is None or not getattr(organ, 'is_running', False)]
            if _offline:
                _warnings.append(f"器官离线: {len(_offline)}个（{', '.join(_offline[:5])}）")
                _issues_found += 1
            self._log(LogLevel.INFO, f"  器官状态: {len(self.organs) - len(_offline)}/{len(self.organs)}在线")
        except Exception as e:
            self._log(LogLevel.WARNING, f"  器官检测异常: {e}")
        
        # 4. 上下文恢复状态
        try:
            _ctx = get_context_snapshot()
            _ctx_stats = _ctx.get_stats()
            self._log(LogLevel.INFO, f"  上下文: 对话记忆={_ctx_stats.get('conversation_count', 0)}条, "
                     f"推理链={_ctx_stats.get('trace_count', 0)}条")
        except Exception as _se:
            silent_exc(_se, "main.py:2997")
        
        # 5. ★往期批次 相关任务③a：启动即锁死WARNING
        #    棘轮锁死状态此前仅在 apply_all_pending 运行时落日志，重启后若未触发
        #    apply 则该状态"失忆"。此处启动即重报，确保重启后立即可见。
        try:
            from nucleus.reasoning.PatchManager import PatchManager
            _pm = PatchManager(os.path.dirname(os.path.abspath(__file__)))
            _pm._m114b_emit_ratchet_lock_warning()
        except Exception as _re:
            silent_exc(_re, "main.py:启动锁死WARNING")
        
        # 汇总
        if _issues_found > 0:
            self._log(LogLevel.WARNING, 
                     f"健康诊断完成: 发现{_issues_found}个关注项 - {'; '.join(_warnings[:3])}")
            # 写入洞察黑板
            try:
                _board = _safe_get_insight_board()
                if _board is not None:
                    for _w in _warnings[:3]:
                        _board.post(
                            insight_type="startup_health",
                            content=_w,
                            source_loop="自主健康守护",
                            related_dimension="系统健康",
                            confidence=_evidence_conf(0.9, "health", [_w]),
                            keywords=["启动诊断", "健康检查"]
                        )
            except Exception as _se:
                silent_exc(_se, "main.py:3016")
        else:
            self._log(LogLevel.INFO, "健康诊断完成: ✅ 所有检查项通过")   
            # ★P3 自主深度探查：启动首轮（异步，不阻塞启动）+ 低频周期巡检
        # 让探查器「自行决定查什么/查多深/何时停」，结果仅发布 InsightBoard + 后台日志，
        # 零副作用；周期间隔可配（默认 30 分钟低频，与既有周期任务错峰，压力均衡）。
        try:
            if config.SELF_PROBE_CONFIG.get("enabled", True):
                from nucleus.self_probe import get_probe_orchestrator
                _probe_interval = float(config.SELF_PROBE_CONFIG.get("cycle_interval_seconds", 1800))

                def _probe_once():
                    try:
                        get_probe_orchestrator().run_probe_cycle(force=True)
                    except Exception as _pe:
                        self._log(LogLevel.DEBUG, f"自主探查首轮失败: {_pe}")

                def _probe_loop():
                    _probe_once()
                    while getattr(self, "_running", False):
                        try:
                            time.sleep(_probe_interval)
                        except Exception:
                            break
                        if not getattr(self, "_running", False):
                            break
                        try:
                            get_probe_orchestrator().run_probe_cycle()
                        except Exception as _pe:
                            self._log(LogLevel.DEBUG, f"自主探查周期失败: {_pe}")

                _probe_t = threading.Thread(target=_probe_loop, name="自主探查周期", daemon=True)
                _probe_t.start()
                self._log(LogLevel.INFO,
                          f"自主深度探查已启用（启动首轮 + 每{_probe_interval:.0f}秒周期巡检）")
        except Exception as _pe:
            self._log(LogLevel.DEBUG, f"自主探查启用失败（不影响主链路）: {_pe}")

    def _on_auto_save(self, pulse):
        """处理自动保存事件"""
        try:
            self.snapshot.save()
            self.snapshot.save_l1()
            # ★第159批 刀4 D-4：节流命中（pending_flush）表示未真正写盘，
            #   不得误报「已保存」，据标志如实区分状态。
            if getattr(self.snapshot, "pending_flush", False):
                self._log(LogLevel.DEBUG,
                          "自动保存延迟（节流命中，内存持有最新数据，pending_flush=True）")
                return {"status": "auto_deferred"}
            self._log(LogLevel.INFO, "自动保存完成")
        except Exception as e:
            self._log(LogLevel.WARNING, f"自动保存失败: {e}")
            return {"status": "auto_failed"}
        return {"status": "auto_saved"}            

    def _on_self_modify_constraint(self, pulse):
        """处理自我修改约束信号，设置一段时间的禁止窗口（问题3闭环）"""
        try:
            self._self_modify_forbidden_until = time.time() + 300.0
            _reason = pulse.get("payload", {}).get("reason", "未知原因")
            self._log(LogLevel.WARNING, f"自我修改约束触发(300s): {_reason}")
        except Exception as _se:
            silent_exc(_se, "main.py:3071")
        return {"status": "constraint_set"}

    def _on_motivation_urge(self, pulse):
        """处理动机冲动信号，存储最新动机供决策层检索并落盘日志（问题3续）"""
        try:
            self._latest_motivation_urge = {
                "payload": dict(pulse.get("payload", {})),
                "received_at": time.time(),
            }
            _desc = pulse.get("payload", {}).get("description", "")
            _intensity = pulse.get("payload", {}).get("intensity", 0.0)
            self._log(LogLevel.INFO, f"动机冲动接收: {_desc}(强度{_intensity:.2f})")
        except Exception as _se:
            silent_exc(_se, "main.py:3085")
        return {"status": "motivation_stored"}

    def _on_emotion_signal(self, pulse):
        """处理激素情绪输出，存储最新情绪供行为决策检索并落盘（激素无响应修复）"""
        try:
            _event = pulse.get("event_type", "")
            _payload = dict(pulse.get("payload", {}))
            self._latest_emotion = {
                "event_type": _event,
                "payload": _payload,
                "received_at": time.time(),
            }
            _emotion = _payload.get("emotion", "")
            _intensity = _payload.get("intensity", 0.0)
            # ★P3-8修复：情绪输出频率控制——仅当情绪类型变化或超过 30 秒冷却才记日志，
            # 避免每次心跳都刷「情绪输出接收」，降低日志噪音（原实现无频率控制）。
            _now = time.time()
            _last_time = getattr(self, "_last_emotion_log_time", 0.0)
            _last_emotion = getattr(self, "_last_logged_emotion", "")
            if _emotion != _last_emotion or _now - _last_time >= 30.0:
                self._last_emotion_log_time = _now
                self._last_logged_emotion = _emotion
                if _event == "hormones.care_needed":
                    self._log(LogLevel.INFO, f"关怀需求接收: {_emotion}(强度{_intensity:.2f})")
                else:
                    self._log(LogLevel.INFO, f"情绪输出接收: {_emotion}(强度{_intensity:.2f})")
        except Exception as _se:
            silent_exc(_se, "main.py:3113")
        return {"status": "emotion_stored"}

    def _on_launch_plan(self, pulse):
        """处理硬件启动计划，存储并落盘日志（M4修复）"""
        try:
            self._latest_launch_plan = dict(pulse.get("payload", {}))
            _tier = pulse.get("payload", {}).get("tier", "unknown")
            self._log(LogLevel.INFO, f"硬件启动计划接收: tier={_tier}")
        except Exception as _se:
            silent_exc(_se, "main.py:3123")
        return {"status": "launch_plan_stored"}

    def get_latest_framework_signals(self) -> dict[str, Any]:
        """★P2-6修复：暴露三个主框架订阅存储的最新信号，供决策层/健康面板检索，
        消除「只写不读」死槽。值为 None 表示尚未收到对应信号。"""
        return {
            "motivation_urge": self._latest_motivation_urge,
            "emotion": self._latest_emotion,
            "launch_plan": self._latest_launch_plan,
        }

    @property
    def logger(self):
        """公开日志器访问（规则14：供功能加载器/对话服务读取）"""
        return self._logger

    def log(self, level: str, msg: str):
        """公开日志输出契约（规则14）"""
        self._log(level, msg)

    def _log(self, level: str, msg: str):
        """框架统一日志输出"""
        log_level = {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }.get(level, 20)
        self._logger.log(log_level, msg)


# ========================================================================
# 程序入口
# ========================================================================

# ★P0-3修复（第十二批）：自重启「接班进程」登记注册表。
#   背景：主循环「立即应用补丁」路径（_apply_pending_patches_and_restart 返回 True
#   后 sys.exit(0)）位于 try 块内，SystemExit 会触发 main() 的 finally 退出清理，
#   而清理段末尾的 psutil.children(recursive=True).terminate() 会把刚 Popen 出来的
#   接班进程一并杀掉 —— 自重启 100% 失败，且接班进程可能已持有快照写锁（父进程
#   framework.stop() 最长 90s），存在快照损坏风险。
#   对策：接班进程独立进程组 + 登记 PID，清理段精确排除。
_SELF_RESTART_CHILD_PIDS: set = set()
_SELF_RESTART_PIDS_LOCK = threading.Lock()


def _spawn_self_restart() -> bool:
    """拉起一个新的框架进程接管（自重启），并登记其 PID 以免被退出清理误杀。

    返回 True 表示已成功拉起接班进程。
    """
    try:
        import os as _os
        import subprocess
        import sys as _sys
        # ★控制台交接修复：Windows上CREATE_NEW_CONSOLE给接班进程独立控制台，
        #   避免与PowerShell共享控制台导致输入混乱、无法Ctrl+C退出。
        # 显式指定工作目录为项目根目录（main.py所在目录），
        #   确保无论父进程从哪个目录启动，接班进程的相对路径都正确。
        _project_root = _os.path.dirname(_os.path.abspath(__file__))
        # ★T-131a③：自重启接班进程 stdout/stderr 重定向到 logs/boot_crash.log，
        #   避免启动期崩溃信息随控制台丢失、无迹可查。
        try:
            _os.makedirs(_os.path.join(_project_root, "logs"), exist_ok=True)
            _boot_log_fh = open(_os.path.join(_project_root, "logs", "boot_crash.log"),
                                 "a", encoding="utf-8")
        except Exception as e:
            silent_exc(e, "main:_spawn_self_restart:boot_crash.log打开失败", level="warning")
            _boot_log_fh = None
        if _sys.platform == 'win32':
            _proc = subprocess.Popen(
                [_sys.executable, __file__],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=_project_root,
                stdout=_boot_log_fh,
                stderr=_boot_log_fh,
            )
        else:
            _proc = subprocess.Popen(
                [_sys.executable, __file__],
                start_new_session=True,
                cwd=_project_root,
                stdout=_boot_log_fh,
                stderr=_boot_log_fh,
            )
        with _SELF_RESTART_PIDS_LOCK:
            _SELF_RESTART_CHILD_PIDS.add(_proc.pid)
        if _boot_log_fh is not None:
            try:
                _boot_log_fh.close()
            except Exception as e:
                silent_exc(e, "main:_spawn_self_restart:boot_crash.log关闭失败", level="warning")
        _logger.info(f"[自重启] 接班进程已启动(pid={_proc.pid}, 独立会话)，父进程即将退出")
        return True
    except Exception as _spawn_e:
        _logger.error(f"[自重启] 接班进程启动失败: {_spawn_e}")
        return False


def _is_self_restart_child(pid) -> bool:
    """判断某 PID 是否为本次运行登记的「接班进程」（退出清理时须跳过）。"""
    try:
        with _SELF_RESTART_PIDS_LOCK:
            return pid in _SELF_RESTART_CHILD_PIDS
    except Exception as e:
        silent_exc(e, where="main::_is_self_restart_child L3327")
        return False


def _apply_pending_patches_and_restart(framework) -> bool:
    """
    ★进化闭环升级(完美级): 应用待批准的补丁并重启验证。

    从 finally 退出块中抽取，使「应用+重启」既可被「框架退出」触发，
    也可被主循环中的「立即应用请求」标记主动触发（打通「验证通过即应用」）。

    返回 True 表示已应用补丁并触发重启（调用方应立即 sys.exit）。
    """
    try:
        from nucleus.reasoning.PatchManager import PatchManager
        _patch_mgr = PatchManager(os.path.dirname(os.path.abspath(__file__)))
        if time.time() < getattr(framework, "_self_modify_forbidden_until", 0.0):
            _logger.info("[补丁] 自我修改约束已触发，跳过自动应用补丁")
            _patch_result = {"applied": 0, "failed": 0, "details": [], "skipped_constraint": True}
        else:
            _patch_result = _patch_mgr.apply_all_pending(only_approved=True)
        if _patch_result["applied"] > 0 or _patch_result["failed"] > 0:
            _logger.info(f"[补丁] 应用完成: {_patch_result['applied']}个成功, {_patch_result['failed']}个失败")
            for _detail in _patch_result["details"]:
                _icon = "✅" if _detail['status'] == 'applied' else "❌"
                _reason = _detail.get('reason', '')
                print(f"  {_icon} {os.path.basename(_detail['file'])}{(' - ' + _reason) if _reason else ''}")
            # 自动重启验证（N-3①②：验证不可判定时抑制整机自重启）
            if _patch_result.get("applied", 0) > 0 and not _patch_result.get("restart_allowed", True):
                _logger.info("[N-3①②] 已应用补丁但验证不可判定，抑制整机自重启（待人工/活体核验）")
            elif _patch_result.get("applied", 0) > 0:
                # ★v23.0新增：发送补丁应用通知（复用桥接器连接）
                if hasattr(framework, 'wecom_bridge') and framework.wecom_bridge:
                    _patch_files = [os.path.basename(d['file']) for d in _patch_result['details'] if d.get('status') == 'applied']
                    framework.wecom_bridge.send_notification(
                        "补丁应用",
                        f"成功应用{_patch_result['applied']}个补丁\n"
                        f"修改文件: {', '.join(_patch_files[:5])}\n"
                        f"即将自动重启验证"
                    )
                # ★v23.0新增：写入待验证标记
                try:
                    from nucleus.reasoning.SelfVerifier import SelfVerifier
                    _verifier = SelfVerifier(os.path.dirname(os.path.abspath(__file__)))
                    _verifier.mark_pending(_patch_result["applied"])
                    _logger.info("[自验证] 已写入待验证标记")
                except Exception as _ve:
                    _logger.info(f"[自验证] 标记写入失败: {_ve}")
                # ★v24.0修复：重启防循环由PatchManager内部持久化计数管理
                _logger.info("[补丁] 补丁已应用，3秒后自动重启框架验证...")
                time.sleep(3)
                # ★P0-3: 改用统一自重启入口（独立会话 + PID 登记），
                #   避免被 main() 的 finally 退出清理段 psutil terminate 误杀
                _spawn_self_restart()
                return True
    except Exception as _patch_e:
        _logger.info(f"[补丁] 应用异常: {_patch_e}")
    return False


def _prompt_with_timeout(prompt: str, timeout: float):
    """★主线往期批次 相关任务：带超时的 input 包装。

    返回二元组 (answered, value)：
      - answered=True  + value=str ：用户在时限内提交了输入；
      - answered=False + value="" ：超时无输入（调用方按默认值 Y 继续）；
      - answered=False + value=None：EOF / Ctrl+C / 异常（用户取消，视为拒绝）。
    工作线程设为 daemon，主线程超时后立即返回，不会因残留线程阻塞进程退出。
    """
    _ev = threading.Event()
    _box: "dict[str, Any]" = {}

    def _worker() -> None:
        try:
            _box["v"] = input(prompt)
        except (EOFError, KeyboardInterrupt):
            # ★门禁可见化：用户取消输入属预期路径，但仍留痕便于排查
            _logger.debug("[补丁] 退出确认被用户中断（EOF/Ctrl+C）")
            _box["v"] = None
        except Exception as _we:
            silent_exc(_we, "main.py:3397")
            _box["v"] = None
        finally:
            _ev.set()

    _th = threading.Thread(target=_worker, name="pulse-quit-confirm", daemon=True)
    _th.start()
    if not _ev.wait(timeout):
        # 超时：判定为非交互/无输入，交由调用方走「默认 Y」分支
        _logger.info(
            f"[补丁] 退出确认等待 {timeout:.0f}s 无输入，按默认 Y 继续"
        )
        return False, ""
    _val = _box.get("v")
    if _val is None:
        return False, None
    return True, _val


def _confirm_apply_pending_on_quit(framework) -> bool:
    """★主线第59批 T4：用户主动退出（SIGINT/SIGTERM）时的待应用补丁确认提示。

    仅在交互终端（sys.stdin.isatty()）弹确认提示，默认 Y（应用并重启验证）；
    非交互终端（作为服务/后台进程运行）或无已批准补丁时，维持原「退出即应用」语义，
    返回 True；任何异常（无 TTY、import 失败、输入中断）一律返回 False 且不阻断退出。

    返回 True 表示应继续调用 _apply_pending_patches_and_restart 应用并重启；
    返回 False 表示跳过应用，直接退出。
    """
    try:
        # ★主线往期批次 相关任务：显式关闭确认（CI/脚本/容器驱动退出）：
        #   PULSE_QUIT_CONFIRM=0 时不做任何交互，直接按「应用并重启」继续。
        _confirm_env = os.environ.get(PULSE_QUIT_CONFIRM_ENV, "").strip().lower()
        if _confirm_env in ("0", "false", "no", "off"):
            return True
        # 非交互终端（服务/后台）：无法交互，保持原行为直接应用
        if not sys.stdin.isatty():
            return True
        from nucleus.reasoning.PatchManager import PatchManager
        _pm = PatchManager(os.path.dirname(os.path.abspath(__file__)))
        _approved = [p for p in _pm.list_pending_patches()
                     if p.get("status") == "approved"]
        if not _approved:
            return False
        _n = len(_approved)
        # ★主线往期批次 相关任务（P1）：超时兜底。
        #   原实现直接调用 input()，在 TTY 阻塞无输入时会把退出路径挂死。
        #   这里改用「工作线程 + 事件超时」：超时后按默认 Y 继续，主线程绝不无限等待。
        _timeout = PULSE_QUIT_CONFIRM_DEFAULT_TIMEOUT
        try:
            _raw_to = os.environ.get(PULSE_QUIT_TIMEOUT_ENV, "").strip()
            if _raw_to:
                _parsed = float(_raw_to)
                if _parsed > 0:
                    _timeout = _parsed
        except (TypeError, ValueError) as _te:
            # ★门禁可见化：非法超时配置回落默认值（往期批次 silent_exc 惯例）
            silent_exc(_te, "main.py:PULSE_QUIT_TIMEOUT_SEC", level="warning")
            _timeout = PULSE_QUIT_CONFIRM_DEFAULT_TIMEOUT
        _answered, _ans = _prompt_with_timeout(
            f"\n[补丁] 检测到 {_n} 个已批准的待应用补丁，"
            f"是否应用并重启验证？(Y/n，默认 Y): ",
            _timeout,
        )
        if not _answered and _ans is None:
            # 用户取消输入（Ctrl+D / Ctrl+C）：视为拒绝，避免意外重启
            return False
        # 超时（_answered=False, _ans=""）或正常输入：按默认 Y 语义判定
        return (_ans or "").strip().lower() in ("", "y", "yes")
    except Exception as _e:
        _logger.warning(f"[补丁] 退出确认提示异常，默认不应用补丁: {_e}")
        return False


def main():
    """
    主入口函数（v9.5）。
    """
    framework = None
    # ★主线往期批次 相关任务（Dxxx）：启动即把 pulse.* 的 ERROR/CRITICAL 桥接进 error_snapshots，
    #   故障面板/HTTP 从此可见（"9类故障全盲"治理）；下次重启生效。
    try:
        from nucleus.runtime_metrics import install_error_capture
        install_error_capture()
    except Exception as _se:
        silent_exc(_se, "main.py:3384")
    try:
        framework = PulseFramework()
    except RuntimeError as e:
        print(f"[框架] 前置检查失败: {e}")
        # ★v23.0新增：启动失败时尝试自动回退
        try:
            from nucleus.reasoning.SelfVerifier import SelfVerifier
            _verifier = SelfVerifier(os.path.dirname(os.path.abspath(__file__)))
            if _verifier.has_pending():
                _logger.info("[自验证] 检测到启动失败且有待验证补丁，尝试回退...")
                from nucleus.reasoning.PatchManager import PatchManager
                _patch_mgr = PatchManager(os.path.dirname(os.path.abspath(__file__)))
                if _patch_mgr.rollback_last():
                    _logger.info("[自验证] 回退成功，3秒后重启...")
                    # ★v24.0修复：回退重启前先停止框架保存快照，避免双进程争抢
                    if framework is not None:
                        try:
                            framework.stop()
                        except Exception as e:
                            silent_exc(e, "main.py:3328")
                    time.sleep(3)
                    # ★P0-3: 统一自重启入口（独立会话 + PID 登记）
                    _spawn_self_restart()
                    sys.exit(0)
                else:
                    _logger.info("[自验证] 回退失败，请人工介入")
        except Exception as _se:
            silent_exc(_se, "main.py:3336")
        # 如果初始化中途失败但framework对象已创建，尝试清理
        if framework is not None:
            try:
                framework.stop()
            except Exception as _se:
                silent_exc(_se, "main.py:3342")
        sys.exit(1)

    exit_requested = False
    # ★主线第59批 T4：标记「用户主动退出」（SIGINT/SIGTERM），用于退出时补丁确认提示。
    #   与异常/强制退出区分，避免干扰故障自愈/回退流程。
    user_quit_requested = False

    def signal_handler(sig, frame):
        nonlocal exit_requested, user_quit_requested
        exit_requested = True
        user_quit_requested = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # P1修复：全局异常钩子——任何线程（含守护线程）抛出的未捕获异常都写入崩溃日志，
    # 避免"静默退出"无迹可寻（此前框架在 flaky 硬件上卡死后被外部杀掉，无任何日志）。
    def _crash_hook(exc_type, exc_value, exc_tb):
        import traceback as _tb
        _msg = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
        try:
            os.makedirs("logs", exist_ok=True)
            with open("logs/pulse_crash.log", "a", encoding="utf-8") as _f:
                _f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UNCAUGHT {exc_type.__name__}:\n{_msg}\n")
        except Exception as _se:
            silent_exc(_se, "main.py:3368")
        try:
            _tb.print_exception(exc_type, exc_value, exc_tb)
        except Exception as _se:
            silent_exc(_se, "main.py:3372")
    sys.excepthook = _crash_hook
    threading.excepthook = lambda args: _crash_hook(
        args.exc_type, args.exc_value, args.exc_traceback)

    # 启动框架
    framework.start()

    # ★v23.0新增：为内在世界设置框架引用，供自动升级窗口检查使用
    try:
        if hasattr(framework, 'inner_world') and framework.inner_world:
            framework.inner_world.set_framework_ref(framework)
    except Exception as _se:
        silent_exc(_se, "main.py:3385")

    # ★v23.0新增：企业微信对话桥接器（对话+通知，单一长连接）
    # ★安全修复：凭证改为从 config（环境变量）读取，不再硬编码明文
    try:
        from nucleus.wecom_chat_bridge import WeComChatBridge
        if not getattr(config, "WECOM_BOT_ID", "") or not getattr(config, "WECOM_SECRET", ""):
            raise ValueError("企业微信凭证未配置：请设置环境变量 WECOM_BOT_ID / WECOM_SECRET")
        wecom_bridge = WeComChatBridge(
            bot_id=config.WECOM_BOT_ID,
            secret=config.WECOM_SECRET,
            info_field=framework.info_field,
            pulse_core=framework.pulse_core,
            framework=framework,
            admin_userid=getattr(config, "WECOM_ADMIN_USERID", ""),
        )
        wecom_bridge.start()
        framework.wecom_bridge = wecom_bridge
        print("[企业微信] 对话桥接器已启动（对话+通知单一连接）")
        # 发送启动通知
        _online_organs = len([o for o in framework.organs.values() if o is not None])
        _total_organs = len(framework.organs)
        wecom_bridge.send_notification(
            "框架启动",
            f"曈曈已成功启动\n{_online_organs}/{_total_organs}个器官在线（实时扫描）\n知识节点: {framework.node_pool.count()}个"
        )
    except Exception as e:
        logging.getLogger("pulse").warning(f"[企业微信] 桥接器启动失败（不影响框架运行）: {e}")
        framework.wecom_bridge = None

    # ★v23.0新增：自我验证
    from nucleus.reasoning.SelfVerifier import SelfVerifier
    _verifier = SelfVerifier(os.path.dirname(os.path.abspath(__file__)))

    if _verifier.has_pending():
        _logger.info("[自验证] 检测到待验证补丁，执行自我验证...")
        _passed = _verifier.run_verification(framework)

        if _passed:
            _logger.info("[自验证] ✅ 验证通过，清除待验证标记")
            _verifier.clear_pending()
        else:
            # ★进化闭环升级(阶段C): 失败后自我反思——先尝试 LLM 反思改进，
            #   改进版补丁验证通过则应用（迭代式进化），反思无效才回退。
            _verification_detail = _verifier.run_verification_detailed(framework)
            _logger.info("[自验证] ❌ 验证失败，尝试自我反思改进...")
            _verifier.increment_attempt()

            _reflection_applied = False
            try:
                from nucleus.evolution.SelfReflectionEngine import SelfReflectionEngine
                from nucleus.reasoning.PatchManager import PatchManager as _PM
                _reflect_engine = SelfReflectionEngine(os.path.dirname(os.path.abspath(__file__)))
                _patch_mgr_ref = _PM(os.path.dirname(os.path.abspath(__file__)))
                # 读取最近失败补丁（从历史中取最后一条 applied 的补丁）
                _last_failed = _patch_mgr_ref._load_json(
                    _patch_mgr_ref._history_file, [])
                _last_applied = None
                for _h in reversed(_last_failed):
                    if _h.get("applied"):
                        _last_applied = _h
                        break
                if _last_applied:
                    _improved = _reflect_engine.reflect_and_improve(
                        _last_applied, _verification_detail)
                    if _improved:
                        _verify = _patch_mgr_ref.verify_in_copy(_improved)
                        if _verify.get("passed"):
                            # ★主线第80批 T7 (P0-3)：反思改进版也须经免签改写闸门，
                            #   核心文件或总开关关闭 → 置 verified 待人工，禁止自动 approved。
                            if _PM._m80_is_core_file(_improved.get("file", "")) or \
                                    not _PM._m80_auto_apply_enabled():
                                _improved["status"] = "verified"
                                _logger.info(
                                    f"[自验证][T7] 反思改进版为核心文件或总开关关闭，置verified待人工: "
                                    f"{_improved.get('file','')}:{_improved.get('method','')}")
                            else:
                                _improved["status"] = "approved"
                            _improved["reflection_of"] = _last_applied.get("id", "")
                            _patch_mgr_ref.save_pending_patch(_improved)
                            _logger.info("[自验证] 💡 反思改进版补丁已生成并通过验证，将在下次应用时自动落地")
                            _reflection_applied = True
                        else:
                            _logger.info("[自验证] 反思改进版补丁验证失败，回退到自动回退流程")
            except Exception as _reflect_e:
                _logger.info(f"[自验证] 反思改进异常(忽略，走回退): {_reflect_e}")

            if _reflection_applied:
                # 反思改进成功，不立即回退，等改进版补丁应用后再次验证
                _verifier.clear_pending()
                _logger.info("[自验证] 反思改进已提交，清除本次待验证标记，等待改进版补丁应用")
            elif _verifier.has_rolled_back():
                _logger.info("[自验证] ⚠️ 本轮已自动回退过一次，验证仍失败。停止再次回退，请人工排查。")
                if hasattr(framework, 'wecom_bridge') and framework.wecom_bridge:
                    framework.wecom_bridge.send_notification(
                        "自验证告警",
                        "补丁回退后验证仍失败\n"
                        "已停止自动回退以保护历史正常补丁\n"
                        "请人工检查框架状态"
                    )
                _verifier.clear_pending()  # 清除标记，避免每次启动都触发告警循环
            else:
                from nucleus.reasoning.PatchManager import PatchManager
                _patch_mgr = PatchManager(os.path.dirname(os.path.abspath(__file__)))
                _rollback_ok = _patch_mgr.rollback_last()
                if _rollback_ok:
                    _verifier.mark_rolled_back()
                    _logger.info("[自验证] 已回退最近补丁，3秒后重启...")
                    # ★v24.0修复：回退重启前先停止框架保存快照
                    if framework is not None:
                        try:
                            framework.stop()
                        except Exception as _se:
                            silent_exc(_se, "main.py:3490")
                    # ★v23.0新增：发送回退通知（复用桥接器连接）
                    if hasattr(framework, 'wecom_bridge') and framework.wecom_bridge:
                        framework.wecom_bridge.send_notification(
                            "自动回退",
                            "检测到补丁验证失败\n"
                            "已自动回退到修改前版本\n"
                            "即将重启恢复"
                        )
                    import sys as _sys
                    time.sleep(3)
                    # ★P0-3: 统一自重启入口（独立会话 + PID 登记）
                    _spawn_self_restart()
                    _sys.exit(0)
                else:
                    # ★第159批 D-3（T-回滚额度-1）：回滚失败不得置位 rolled_back，
                    #   否则「每次待验证只自动回退一次」额度被凭空烧掉。
                    #   记录失败原因备查，停止自动重启，转人工。
                    _verifier.mark_rollback_failed(
                        "rollback_last() 返回 False（无可用备份/路径越界/历史无已应用补丁）")
                    _logger.info("[自验证] 回退失败，停止自动重启，请人工介入检查日志")
    
    
    # 启动人体UI监控面板（独立Web服务）
    health_ui = None
    try:
        from functions.health_ui import HealthUIServer
        health_ui = HealthUIServer(port=5051)
        health_ui.set_node_pool(framework.node_pool)
        health_ui.start()
    except Exception as e:
        logging.getLogger("pulse").warning(f"[框架] 人体UI启动失败 (端口5051): {e}")
    
    # 启动Web对话窗口（独立Web服务）
    web_chat = None
    try:
        from functions.web_chat import WebChatServer
        web_chat = WebChatServer(port=5052)
        web_chat.start(info_field=framework.info_field, pulse_core=framework.pulse_core)
    except Exception as e:
        logging.getLogger("pulse").warning(f"[框架] Web对话窗口启动失败 (端口5052): {e}")
    
    # 启动功能模块加载器
    framework.function_loader = FunctionLoader(framework)
    framework.function_loader.load_all()

    # P1-5: 启动配置热重载监听
    config.start_config_watcher()

    # ★P1: 注册各器官的热重载回调（配置变更后自动刷新参数）
    try:
        _hot_reload_organs = []
        for _attr_name in ['lung', 'cortex', 'stomach', 'controller', 'subconscious',
                           'inner_world', 'interest_model', 'risk_perception',
                           'reflection', 'code_learner', 'liver', 'kidney', 'visual_cortex',
                           'node_pool', 'heart', 'eyes', 'blood_vessel', 'initiative',
                           'semantic_comprehension', 'code_sandbox', 'legs', 'mouth',
                           'ears', 'touch', 'spiritual_core', 'file_digester',
                           'cognitive_reflector', 'expression', 'knowledge_retriever',
                           'multi_step_reasoner', 'reasoning_formatter', 'hands',
                           'bonding', 'consent', 'dna_repair', 'evolution',
                           'nurture', 'reproduction_ethics']:
            _organ = getattr(framework, _attr_name, None)
            if _organ and hasattr(_organ, 'refresh_runtime_params'):
                _hot_reload_organs.append(_attr_name)
                config.register_hot_reload_callback(_organ.refresh_runtime_params)
        if _hot_reload_organs:
            print(f"[Config] 热重载回调已注册（{len(_hot_reload_organs)}个器官: {', '.join(_hot_reload_organs)}）")
    except Exception as _hre:
        logging.getLogger("pulse").warning(f"[Config] 热重载回调注册失败: {_hre}")

    # ========== 假死探测器（P1） ==========
    # 框架启动后若长时间无任何脉冲被实际处理（疑似卡死/死锁），自动 dump 所有线程
    # 堆栈到 logs/pulse_crash.log，便于定位冻结点（普通异常由 excepthook 捕获，此处针对“静默假死”）。
    try:
        import faulthandler as _fh
        try:
            _crash_fh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pulse_crash.log"), "a", encoding="utf-8", errors="replace")  # noqa: SIM115 - 有意持有句柄供 faulthandler 常驻
        except Exception as e:
            _crash_fh = None
            logging.getLogger("pulse").warning(f"崩溃日志句柄初始化失败(留空): {type(e).__name__}: {e}")
        # 关键：把原生崩溃（如 PortAudio 的 access violation）堆栈也重定向到日志文件。
        # 否则 faulthandler 只打印到控制台，不会写入 pulse_crash.log（这正是上次日志为空的原因）。
        try:
            if _crash_fh is not None:
                _fh.enable(file=_crash_fh)
            else:
                _fh.enable()
        except Exception:
            try:
                _fh.enable()
            except Exception as _se:
                silent_exc(_se, "main.py:3576")
    except Exception as e:
        _fh = None
        _crash_fh = None
        logging.getLogger("pulse").warning(f"日志句柄初始化失败(禁用落盘): {type(e).__name__}: {e}")

    _lv_fw = framework  # 捕获闭包引用

    def _liveness_watchdog():
        nonlocal _crash_fh
        _last = 0
        _stall = 0
        _grace = time.time() + 25  # 启动宽限 25s
        while not exit_requested:
            time.sleep(10)
            if time.time() < _grace:
                continue
            try:
                _inf = getattr(_lv_fw, "info_field", None)
                _getter = getattr(_inf, "get_total_handled", None)
                _handled = _getter() if _getter else None
            except Exception as e:
                if should_warn("main:false_death_probe", 300):
                    silent_exc(e, "main.py:3700 假死探测取handled", level="warning")
                continue
            if _handled is None:
                continue
            if _handled == _last:
                _stall += 1
                if _stall >= 3:  # 连续约 30s 无任何脉冲被处理
                    _msg = ("\n[假死探测器] 框架疑似冻结：连续约30秒无任何脉冲被处理。\n"
                            "[假死探测器] 正在 dump 所有线程堆栈到 logs/pulse_crash.log ...\n")
                    # E 可观测化：触发进日志文件（原 print 不落盘，无法自证）
                    logging.getLogger("pulse").warning(_msg)
                    if _fh is not None:
                        try:
                            # 崩溃日志体积上限轮转（faulthandler 常驻句柄，需重新 enable）
                            _cp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "logs", "pulse_crash.log")
                            if os.path.exists(_cp) and os.path.getsize(_cp) > 20 * 1024 * 1024:
                                try:
                                    _fh.disable()
                                except Exception as e:
                                    silent_exc(e, "main.py:3719 崩溃日志轮转disable", level="warning")
                                try:
                                    if os.path.exists(_cp + ".1"):
                                        os.remove(_cp + ".1")
                                    os.rename(_cp, _cp + ".1")
                                except Exception as e:
                                    silent_exc(e, "main.py:3725 崩溃日志轮转rename", level="warning")
                                try:
                                    _crash_fh = open(_cp, "a", encoding="utf-8", errors="replace")
                                    _fh.enable(file=_crash_fh)
                                except Exception as _re:
                                    logging.getLogger("pulse").warning(f"[假死探测器] 轮转后重开崩溃日志失败: {_re}")
                            if _crash_fh is not None:
                                _crash_fh.write(
                                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] WATCHDOG_DUMP "
                                    f"stall={_stall} handled={_handled}\n"
                                )
                                _crash_fh.flush()
                            _fh.dump_traceback(_crash_fh or sys.stderr)
                        except Exception as _se:
                            logging.getLogger("pulse").warning(f"[假死探测器] dump_traceback 失败: {type(_se).__name__}: {_se}")
                    _stall = 0  # 避免刷屏，下次再报需再静默约30秒
            else:
                _last = _handled
                _stall = 0

    _lv_thread = threading.Thread(target=_liveness_watchdog, name="LivenessWatchdog", daemon=True)
    _lv_thread.start()

    # 主循环：等待退出信号 + 周期检测「立即应用补丁」请求
    try:
        _apply_check_counter = 0
        while not exit_requested:
            time.sleep(1)
            # ★进化闭环升级(完美级): 周期检测「验证通过即应用」请求。
            #   审视生成的补丁若已批准，会写入 apply_now.json 标记，
            #   此处检测到后走「应用+重启」流程，不再被动等待框架退出。
            _apply_check_counter += 1
            if _apply_check_counter % 10 == 0:  # 每 10 秒检查一次，避免频繁磁盘 IO
                try:
                    from nucleus.evolution.EvolutionDriver import EvolutionDriver
                    _evo_driver = EvolutionDriver(os.path.dirname(os.path.abspath(__file__)))
                    if _evo_driver.has_apply_request():
                        _evo_driver.consume_apply_request()
                        print("[进化] 检测到「立即应用补丁」请求，准备应用并重启验证...")
                        # 停止框架（复用退出时的清理流程，保证快照保存）
                        try:
                            if framework and hasattr(framework, 'stop'):
                                framework.stop()
                        except Exception as _se:
                            silent_exc(_se, "main.py:3639")
                        # 应用补丁并重启
                        if _apply_pending_patches_and_restart(framework):
                            sys.exit(0)
                except Exception as _apply_check_e:
                    logging.getLogger("pulse").warning(f"[进化] 应用请求检测异常(忽略): {_apply_check_e}")
    except KeyboardInterrupt as _se:
        silent_exc(_se, "main.py:3646")
    except Exception as _main_loop_e:
        # ★P3-4修复：全局异常兜底
        print(f"[框架] [CRITICAL] 运行时异常，触发紧急保存: {_main_loop_e}")
        try:
            if framework and hasattr(framework, 'snapshot'):
                framework._log(LogLevel.CRITICAL, f"紧急保存: 运行时异常 {_main_loop_e}")
                framework.snapshot.save()
                framework.snapshot.save_l1()
        except Exception as _se:
            silent_exc(_se, "main.py:3656")
    finally:
        # ===== 步骤1: 停止功能模块 =====
        if framework.function_loader:
            try:
                framework.function_loader.stop_all()
            except Exception as _se:
                silent_exc(_se, "main.py:3663")

        # ===== 步骤2: 停止HTTP服务 =====
        try:
            health_ui.stop()
        except Exception as _se:
            silent_exc(_se, "main.py:3669")
        try:
            web_chat.stop()
        except Exception as _se:
            silent_exc(_se, "main.py:3673")

        # 给 HTTP 服务器一点时间释放 socket
        time.sleep(0.3)

        # ★P1-4修复：停止配置热重载监听
        try:
            # ★P1: 停止参数补丁管理器后台线程
            if hasattr(framework, 'param_patch_manager') and framework.param_patch_manager:
                framework.param_patch_manager.stop_auto_apply()
                print("[框架] 参数补丁自动应用线程已停止")
            config.stop_config_watcher()
        except Exception as _se:
            silent_exc(_se, "main.py:3686")

        # ★PHASE17-1.5：语义内核显式落盘。
        #   ★关键：本框架退出走 os._exit（见下方），会**跳过所有 atexit 钩子**，
        #     VectorStore 注册的 atexit flush 不会被执行 —— 必须在这里显式调用，
        #    否则进程一退出，内存里未落盘的向量就全丢了。
        try:
            from nucleus.semantic.SemanticKernelService import (
                shutdown as _shutdown_semantic_kernel,
            )
            _shutdown_semantic_kernel()
        except Exception as _se:
            silent_exc(_se, "main.py:3698")

        # ===== 步骤3: 停止框架 =====
        framework.stop()

        # ★v16.0新增：自动应用验证通过的代码补丁
        # ★进化闭环升级(完美级): 抽取为复用函数，退出时仍保留此应用入口
        # ★主线第59批 T4：用户主动 quit（SIGINT/SIGTERM）时先弹确认提示（默认 Y）；
        #   异常/强制退出保持原行为（退出即应用），避免干扰故障自愈/回退流程。
        if user_quit_requested:
            if _confirm_apply_pending_on_quit(framework):
                if _apply_pending_patches_and_restart(framework):
                    sys.exit(0)
        else:
            if _apply_pending_patches_and_restart(framework):
                sys.exit(0)

        # ===== 步骤4: 兜底强制退出 =====
        # 等待最多 2 秒让非守护线程自然结束
        deadline = time.time() + 2.0
        while time.time() < deadline:
            alive = [t for t in threading.enumerate()
                     if t != threading.main_thread() and not t.daemon]
            if not alive:
                break
            time.sleep(0.2)
        else:
            alive = [t for t in threading.enumerate()
                     if t != threading.main_thread() and not t.daemon]
            if alive:
                print(f"[框架] [WARNING] 强制退出，残留 {len(alive)} 个非守护线程: "
                      f"{[t.name for t in alive[:5]]}")

        # ★FIX(进程残留): 主动清理残留子进程——sys.exit 不会自动 terminate 子进程
        #   历史上 ProcessPoolExecutor worker / subprocess.Popen 启动的服务进程会残留，
        #   导致用户必须手动 taskkill 才能彻底退出
        # ★A5【P1】核实结论 —— ★已于第十二批 P0-3 修正★
        #   原结论「不存在误杀接班进程的风险」不成立：它只分析了「退出路径」
        #   （_apply_pending_patches_and_restart 在 finally 内被调用，sys.exit 抛于
        #   finally 中不再重入，确实安全），却漏掉了「主循环立即应用路径」——
        #   该路径的 sys.exit(0) 位于 try 块内，SystemExit 会触发本 finally 段，
        #   一路执行到下方 psutil.children(recursive=True).terminate()，
        #   把 _spawn_self_restart() 刚拉起的接班进程杀掉，自重启 100% 失败。
        #   现对策（双保险）：
        #     1) 接班进程 start_new_session=True —— 独立会话/进程组；
        #     2) PID 登记于 _SELF_RESTART_CHILD_PIDS，清理段精确跳过。
        #   本段三层兜底（active_children / ReasoningWorkerPool.shutdown / psutil）
        #   保持不变，仅新增「排除项」，属叠加而非替换。
        try:
            import multiprocessing as _mp
            for _child in _mp.active_children():
                try:
                    if _child.is_alive():
                        _child.terminate()
                        _child.join(timeout=0.5)
                except Exception as _se:
                    silent_exc(_se, "main.py:3754")
        except Exception as _se:
            silent_exc(_se, "main.py:3756")
        # 关闭 ProcessPoolExecutor（如果 stop 流程未关闭）
        try:
            from nucleus.reasoning.ReasoningWorkerPool import get_reasoning_pool
            _pool = get_reasoning_pool()
            if _pool:
                _pool.shutdown()
        except Exception as _se:
            silent_exc(_se, "main.py:3764")
        # 用 psutil 列出所有子进程并 terminate（如果可用）
        try:
            import psutil
            _parent = psutil.Process(os.getpid())
            for _child in _parent.children(recursive=True):
                try:
                    # ★P0-3修复：跳过本次运行登记的「接班进程」——
                    #   它是来接班的，不是残留。误杀会导致自重启 100% 失败。
                    #   （start_new_session=True 已让其脱离进程组，但 psutil.children
                    #    依据的是父子关系而非进程组，故此处仍须显式排除，双保险。）
                    if _is_self_restart_child(_child.pid):
                        _logger.info(f"[自重启] 跳过接班进程(pid={_child.pid})，不参与残留清理")
                        continue
                    _child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied) as _se:
                    silent_exc(_se, "main.py:3780")
        except ImportError as _se:
            silent_exc(_se, "main.py:3782")
        except Exception as _se:
            silent_exc(_se, "main.py:3784")

        # ★FIX(进程残留): 用 os._exit 强制退出，不等待任何非 daemon 线程/子进程
        #   相比 sys.exit，os._exit 立即终止进程，跳过所有 atexit 清理
        #   先 flush 确保日志不丢
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception as _se:
            silent_exc(_se, "main.py:3793")
        print("[框架] [INFO] 进程退出")
        os._exit(0)
        sys.exit(0)

if __name__ == "__main__":
    import os as _os
    _os.environ.setdefault("OPENCV_LOG_LEVEL", "0")
    _os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
    main()
# _m49_t1_legs_done
# _m49_t2_wire_done
# _m50_t1_main_done

# _m51_t3_main