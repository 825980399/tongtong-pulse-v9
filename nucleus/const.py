# -*- coding: utf-8 -*-
"""


const.py —— 常量定义

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架全局常量、枚举、默认值集中管理
机制: 基于PulseLayer类实现，包含0个核心方法
定位: 配置基础设施层
"""

# ========== ★主线第133批 T-133c：备份树扫描白名单（制度化） ==========
#   供 self_inspector 防御过滤与 glob/os.walk 扫描排除共用，避免 dead_code 扫描
#   误入 backups/ code_backups/ tmp/ 等备份/临时树（前序实测 9 分钟 → 目标 <1 分钟）。
SCAN_EXCLUDE_DIRS = ["backups/", "data/code_backups/", "tmp/", ".aionclaw-tmp/"]  # ★157-E T-框架-1：aionclaw 临时目录纳入单一真相集
SCAN_EXCLUDE_PREFIX = [".bak_batch", ".bak_"]
# 由 SCAN_EXCLUDE_DIRS 推导的段名白名单（去尾部斜杠、取 basename）
SCAN_EXCLUDE_DIR_BASENAMES = {
    _d.rstrip("/").split("/")[-1] for _d in SCAN_EXCLUDE_DIRS
}


# ========== ★第134批 T-134c：self_inspector 三检测器配置 ==========
#   集中常量，便于巡检策略调参；补丁改不动（与 GOD_FILE_EXEMPT 同源约束）。
# ---- B1 silent_growth：趋势账 + 节流 + boot 静默 ----
SELF_INSPECTOR_BOOT_SILENCE_SEC = 1800          # boot 后静默 30 分钟（B1/B2 共用）
SELF_INSPECTOR_B1_THROTTLE_SEC = 21600          # 6 小时节流（趋势账最小采样间隔）
SELF_INSPECTOR_B1_WEEKLY_LOC_DELTA = 500        # 行数周增 > 500 告警
SELF_INSPECTOR_B1_SILENT_RISE_PCT = 20          # 静默计数周升 > 20% 告警
SELF_INSPECTOR_HISTORY_PATH = "data/self_inspector_history.jsonl"  # 趋势账（git-ignored）

# ---- B2 l3_inversion：框架运行态时间戳时效（runtime_state.json 顶层 timestamp） ----
SELF_INSPECTOR_B2_STALE_SEC = 300               # 时间戳陈旧 > 300s 告警（框架停滞/宕机 5 分钟内报警）
SELF_INSPECTOR_B2_L1_FLOOR = 400                # l1（L1 层代码行数）< 400 不执法

# ---- B3 god_file：天花板豁免制 + ceiling 基线 ----
SELF_INSPECTOR_B3_CEILING_PCT = 5               # 相对首扫基线 > +5% 才报
SELF_INSPECTOR_B3_BASELINE_REL = "data/god_file_baseline.json"  # 首扫基线（git-ignored）

# ---- ★第136批 T-136c：import cycles 检测器（Tarjan 强连通分量） ----
SELF_INSPECTOR_C1_BASELINE_REL = "data/import_cycles_baseline.json"  # 首扫基线（git-ignored）

# ---- ★第136批 T-136d：不可达代码检测器（AST 扫描 return/raise 后死语句） ----
SELF_INSPECTOR_D1_BASELINE_REL = "data/unreachable_code_baseline.json"  # 首扫基线（git-ignored）

# ---- ★第138批 T-138a：圈复杂度 CC 检测器（radon cc_visit，函数/方法级） ----
#   阈值来自 137 期全树实测分布（6,689 块，319 文件）：
#     ≥25 函数 227 个（进 per_file 趋势观察账，不直接告警）；
#     ≥50 函数  58 个（棘轮本体，**新增**才告警，只降不升）。
#   15/10 阈值被否（578 / 1,094 员 → 噪声）。
SELF_INSPECTOR_B6_OBS_MIN = 25                  # 观察账阈值：≥25 进 per_file 计数
SELF_INSPECTOR_B6_GATE_MIN = 50                 # 棘轮闸阈值：≥50 计入 blocks_ge50（新增才报）
SELF_INSPECTOR_B6_BASELINE_REL = "data/cc_baseline.json"  # 首扫基线（git-ignored）

# B3 豁免表（T-135a 收窄为 3 个地基文件）：仅 main.py / self_inspector.py / config.py 不参与
# god_file 膨胀告警；其余巨型器官（含 PulseInnerWorld 等）改走 ceiling 基线，避免噪声刷屏。
GOD_FILE_EXEMPT = {
    "main.py",
    "self_inspector.py",
    "config.py",
}



# ========== v9.5新增: 分层脉冲层级枚举 ==========

class PulseLayer:
    """
    分层脉冲层级枚举 —— v9.5自进化基座核心定义。
    
    四层独立线程池调度，优先级从高到低：
        L0 生命线层 —— 心跳、熔断、L3锁定校验、安全告警
        L1 实时交互层 —— 对话输入输出、意图路由、风险预判
        L2 认知思考层 —— 内在世界推理、五维共振检索、知识消化
        L3 后台自主层 —— 行为种子、噪音清理、兴趣演化、知识淘汰
    
    使用方式:
        from nucleus.const import PulseLayer
        self._emit(HeartEvent.BEAT, payload, priority=7, layer=PulseLayer.L0_LIFELINE)
    """
    L0_LIFELINE = "L0"      # 生命线层：最高优先级，单线程独占，禁止并行
    L1_REALTIME = "L1"      # 实时交互层：高优先级，多线程并行
    L2_COGNITIVE = "L2"     # 认知思考层：中优先级，全并行
    L3_AUTONOMOUS = "L3"    # 后台自主层：低优先级，最大化并行，高负载自动缩容
class ViewMode:
    """知识视角模式（内视自身框架 / 外视物理世界）"""
    INNER_VIEW = "INNER_VIEW"   # 内视：自身框架、代码、调度规则、硬件状态
    OUTER_VIEW = "OUTER_VIEW"   # 外视：物理世界、网络资讯、外部知识、对话交互



# ========== 系统事件类型 ==========

class SystemEvent:
    """系统级脉冲事件类型"""
    # 生命周期
    BOOT = "system.boot"
    STOP = "system.stop"
    STATUS_REQUEST = "system.status.request"
    
    # 告警
    ALARM = "system.alarm"
    ERROR = "system.error"
    SAFE_MODE = "system.safe_mode"
    
    # 硬件
    DEVICE_ATTACHED = "hardware.device.attached"
    DEVICE_DETACHED = "hardware.device.detached"
    STATUS_RESPONSE = "system.status.response"
    ERROR_REPORT = "system.error_report"
    RECOVERY_ATTEMPT = "system.recovery_attempt"  # ★v24.0修复：恢复尝试事件

    # 时间中枢（TimeCore）
    TIME_TICK = "time.tick"  # ★v9.x TimeCore：周期性时间广播（墙钟/逻辑时间/语义时间）


class HeartEvent:
    """心脏相关事件"""
    BEAT = "heart.beat"
    ALIVE = "heart.alive"
    TASK_REGISTER = "heart.task.register"


class KnowledgeEvent:
    """知识管理事件"""
    WRITTEN = "knowledge.written"
    RAW = "knowledge.raw"
    COMPRESSED = "knowledge.compressed"
    FUSED = "knowledge.fused"
    ARBITRATED = "knowledge.arbitrated"  # ★内部知识仲裁结果（非对话输出）


class DigestEvent:
    """消化相关事件"""
    KNOWLEDGE = "digest.knowledge"


class ChatEvent:
    """对话交互事件"""
    MESSAGE = "chat.message"
    INITIATIVE = "chat.initiative"
    SILENCE_TIMEOUT = "chat.silence_timeout"  # ⭐ 新增
    USER_PRESENCE_DETECTED = "chat.user_presence_detected"  # ← 新增
    USER_LEFT = "chat.user_left"  # ← 新增：用户离开

class MotorEvent:
    """运动系统事件"""
    EXECUTE = "motor.execute"
    FILE_DIGEST = "motor.file_digest" 


class ReflectionEvent:
    """前额叶复盘事件"""
    INSIGHT = "reflection.insight"
    ISSUE_FOUND = "reflection.issue_found"


class InterestEvent:
    """兴趣模型事件"""
    CHANGED = "interest.changed"


class PersonaEvent:
    """人物画像事件"""
    QUERY = "persona.query"
    UPDATE = "persona.update"
    RECORD_INTERACTION = "persona.record_interaction"
    IDENTIFIED = "persona.identified"
    SWITCHED = "persona.switched"
    RELATION_CHANGED = "persona.relation_changed"


class MouthEvent:
    """嘴巴相关事件"""
    SPEAK = "mouth.speak"
    REPLY = "mouth.reply"


class EarEvent:
    """耳朵相关事件"""
    HEARD = "ears.heard"
    INTENT_DETECTED = "ears.intent_detected"


class CodeEvent:
    """代码执行事件"""
    RESULT = "code.result"


class VisualEvent:
    """视觉皮层事件"""
    CAMERA_FRAME = "sensor.camera.frame"
    FACE_DETECTED = "sensor.camera.face_detected"
    OBJECT_DETECTED = "sensor.camera.object_detected"
    TEXT_DETECTED = "sensor.camera.text_detected"
    ANALYSIS_DONE = "visual.analysis.done"
    SIMULATE = "visual.simulate"


class AudioEvent:
    """音频事件"""
    AUDIO_CHUNK = "sensor.microphone.audio_chunk"
    SPEECH_DETECTED = "sensor.microphone.speech_detected"
    SPEECH_RECOGNIZED = "sensor.microphone.speech_recognized"
    SPEAKER_IDENTIFIED = "sensor.microphone.speaker_identified"


class ProprioceptionEvent:
    """本体感知事件"""
    REQUEST = "proprioception.request"
    REPORT = "proprioception.report"


class TouchEvent:
    """触觉事件"""
    HARDWARE_SNAPSHOT = "touch.hardware_snapshot"
    SNAPSHOT = "touch.snapshot"


class InferenceEvent:
    """推理事件"""
    REQUEST = "inference.request"
    RESULT = "inference.result"
class InnerWorldEvent:
    # TODO: 预留接口，待未来功能使用（事件占位类，当前成员 CACHE_CLEAR 无引用）
    """内在世界相关事件"""
    CACHE_CLEAR = "inner_world.cache_clear"    


class SubconsciousEvent:
    """潜意识事件"""
    CURIOSITY_TICK = "curiosity.tick"
    EXPLORE = "subconscious.explore"
    SEARCH_FEEDBACK = "subconscious.search_feedback"  # ← 新增：搜索质量反馈
class LungEvent:
    """肺/模型池相关事件"""
    SELECT_MODEL = "lungs.select_model"
    # ★P2-37（主线第3批 任务1）：MODEL_SELECTED（lungs.model_selected）已摘除 ——
    #   四步验证（Class.MEMBER / 'value' / getattr）全库零引用；真正在用的是
    #   SELECT_MODEL（lungs.select_model，PulseLung.py 等处 _emit 共 11 次），
    #   此为笔误遗留的重复死常量，予以清理。
class VascularEvent:
    """血管相关事件"""
    SILENT_ORGAN = "organ.silent"
    # ★F4：沉默器官分级自愈处置事件（由血管发射，具备处置能力的器官消费）
    ORGAN_ESCALATION = "organ.escalation"


# ★F4收尾：沉默检测豁免器官统一常量（血管检测 + 系统管理器处置共用，避免两边不一致）
# 这些器官属低频/被动响应型，空闲静默属正常，不应被沉默检测误判，更不应被处置。
SILENCE_EXEMPT_ORGANS = {
    # 感知器官（低频）
    "视觉皮层", "耳朵", "眼睛", "触觉", "PulseVisualCortex",
    "PulseEars", "PulseEyes", "PulseTouch",
    # 认知/反思被动响应器官（仅在收到认知处理请求时活跃，空闲静默属正常）
    # ★W2修复：大脑皮层/前额叶此前中文名缺失，血管用中文 organ_name 追踪活跃，
    # 导致被误判沉默并升级处置（见日志 111/160 次）。
    "大脑皮层", "PulseCortex",
    "前额叶", "PulseReflection",
    # 系统管理/硬件/设备类（★W2修复：补齐中文名——血管追踪用中文 organ_name）
    "PulseSystemManager", "PulseHealthMonitor", "PulseDeviceManager",
    "PulseHardwareLauncher", "PulseEmergencyHandler",
    "硬件启动器", "健康监控",
    # 遗传/安全/低频器官（★W2修复：补齐 胸腺/白细胞 中文名——英文类名已在豁免，
    # 但血管按中文 organ_name 追踪，中文名缺失导致 140/72 次误判）
    "PulseDNARepair", "PulseReproductionEthics", "PulseBoneMarrow",
    "PulseThymus", "PulseSkin", "PulseWhiteCell",
    "胸腺", "白细胞",
    # 低频器官（中文名）
    "肾", "肾脏", "PulseKidney",
    "人格内核", "PulsePersonalityKernel",
    "主动交互", "PulseInitiative",
    # 被动响应型模块（仅在用户输入时活跃）
    "对话模块", "ChatService",
    "激素", "PulseHormones",
    "嘴巴", "PulseMouth",
    "肺", "PulseLung",
    "潜意识", "PulseSubconscious",
    # 其余被动响应型/低频器官
    "代码学习", "动机循环", "语义理解器", "自我认知", "兴趣模型", "伦理", "胃",
    "控制器",
    # 身份/成长/免疫低频被动器官（★W2修复：精神核心/成长仅按需响应，
    # 双腿为运动输出器官、空闲静默正常，补齐中文名+类名，消除 137/77/10 次误判）
    "精神核心", "PulseSpiritualCore",
    "成长", "PulseGrowth",
    "双腿", "PulseLegs",
    # 异步自主线程器官：独立线程持续工作（融合/压缩等），
    # 不走血管脉冲协议上报活跃，沉默检测对其无意义 → 豁免避免误报。
    # ★v9.5修复：肝(PulseLiver)高频异步融合/压缩被误判沉默，
    # 每心跳巡检 DEBUG + 周期性 EmergencyHandler WARNING 刷屏。
    "肝", "PulseLiver",
    # 遗传/核心系统动态加载的低频被动器官（英文类名）
    "PulseEvolution", "PulseBonding", "PulseConsent", "PulseNurture",
    "PulseEnergyMetabolism", "PulseStressAxis",
    # ★2026-09-03日志巡检修复：事件驱动/消费者型器官，空闲静默属正常，不应被沉默检测误判
    "framework_launch_plan_consumer", "framework_auto_save", "framework_emotion_consumer",
    "framework_motivation_consumer", "framework_knowledge_consumer",
    "能量代谢", "系统管理器", "设备管理器", "企业微信桥接器-回复监听", "对话模块-全局回复监听", "Web对话-人脸监听",
    # ★R2修复（P1，2026-09-05）：补齐 11 个被误报器官的**中文 organ_name**。
    #   这是 253-254 行 W2 修复的**未覆盖残留**——当时只给 胸腺/白细胞 补齐了中文名，
    #   本批器官当时只写了英文类名，未补中文名。
    #   判据链（已逐行核实）：
    #     - 血管按中文 organ_name 追踪活跃：InfoField.py:461 取 condition["organ_name"]
    #       （即器官 self.organ_name），写入 _organ_last_active（InfoField.py:591）
    #     - 血管合并后按 organ_name 比对豁免：PulseBloodVessel.py:201
    #       `if organ_name in self._exempt_organs: continue`
    #     - 器官的 self.organ_name 默认为中文（如 PulseConsent.py:30 "共同决策"）
    #   → 白名单里只有英文类名 "PulseConsent" 时，中文键 "共同决策" 永远匹配不上，
    #     豁免形同虚设。7小时运行实测 6 次误报，11 个器官全部中招（0/11 命中）。
    #   注意：本判定路径下，**纯英文类名条目对中文 organ_name 无效**（死条目），
    #     故此处必须补中文名。英文类名保留仅为兼容其他按类名判定的调用方。
    "QICA",                      # nucleus/qica/QICA.py:238（本身即英文名，无中文别名）
    "共同决策",                   # PulseConsent.py:30
    "应激轴",                     # PulseStressAxis.py:29
    "双手",                       # PulseHands.py:37
    "骨髓",                       # PulseBoneMarrow.py:39
    "情感羁绊",                   # PulseBonding.py:31
    "生育伦理",                   # PulseReproductionEthics.py:30
    "进化",                       # PulseEvolution.py:44
    "养育",                       # PulseNurture.py:30
    "皮肤",                       # PulseSkin.py:40
    "DNA修复",                    # PulseDNARepair.py:44
    # ★阶段三 TimeCore 修复（2026-09-08）：时间中枢沉默误报。
    #   TimeCore（nucleus/chronos/TimeCore.py）为**内核单例**，非 BasePulseOrgan 子类，
    #   不注册器官网心跳活性，平时**静默属正常**。它自带 daemon 线程 + threading.Timer 的
    #   独立驱动/心跳机制（复用 GlobalClock），因此**不需要**、也无法通过血管脉冲协议上报活跃。
    #   沉默检测（PulseBloodVessel._check_silent_organs）此前每心跳巡检误报其为沉默器官。
    #   注：血管按 source_organ / 中文 organ_name 比对豁免（PulseBloodVessel.py:201），
    #   TimeCore 发脉冲时写 "source_organ": "TimeCore"（TimeCore.py:176/199），
    #   故英文类名 "TimeCore" 即可命中；"时间中枢" 为保险条目（兼容按中文名追踪的调用方）。
    "TimeCore",                  # 时间中枢（非器官内核单例，有独立心跳机制）
    "时间中枢",                   # 同上，中文别名保险
    # ★第106批 T-106a（P1 短期止血）：补齐 4 个被沉默检测误报的非器官实体/被动模块。
    #   根因：血管按 organ_name 比对豁免，而下列实体的实际追踪名（self.organ_name）与既有
    #   豁免条目（"企业微信桥接器-回复监听" / "Web对话-人脸监听" 等带后缀变体）不一致 →
    #   豁免命中失败 → 误报刷屏（任务书：1292 次/每 40 秒）。
    #   任务书明确要求补「裸名」以对齐实际追踪名；存量死键/带后缀条目保留不动。
    "企业微信", "framework_self_modify_gate", "Web对话", "FunctionLoader",
}


# ========== 器官状态枚举 ==========

class OrganStatus:
    """器官生命周期状态"""
    IDLE = "idle"
    RUNNING = "running"
    SUSPENDED = "suspended"
    FUSED = "fused"
    RECOVERING = "recovering"
    ERROR = "error"
    STOPPED = "stopped"


# ========== 通用错误码 ==========

class ErrorCode:
    """标准化错误分类"""
    DEPENDENCY_MISSING = "dependency_missing"
    CONFIG_INVALID = "config_invalid"
    DIRECTORY_NOT_FOUND = "directory_not_found"
    
    ORGAN_TIMEOUT = "organ_timeout"
    PULSE_TIMEOUT = "pulse_timeout"
    EXECUTION_FAILED = "execution_failed"
    
    SECURITY_BLOCKED = "security_blocked"
    PERMISSION_DENIED = "permission_denied"
    INPUT_REJECTED = "input_rejected"
    
    KNOWLEDGE_CORRUPTED = "knowledge_corrupted"
    SNAPSHOT_LOAD_FAILED = "snapshot_load_failed"


# ========== 日志级别 ==========

class LogLevel:
    """统一日志级别"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
class PurgeEvent:
    """知识淘汰相关事件"""
    PURGE_CHECK = "kidney.purge.check"
    PURGE_RESULT = "kidney.purge.result"
class DecisionEvent:
    """决策相关事件"""
    # ★主线第8批 任务4（P2-37）：RESULT（decision.result）已摘除 ——
    #   四步验证全库零引用；非传感器词汇/外部协议，确认死常量。
class EyeEvent:
    """眼睛/搜索相关事件"""
    SEARCH = "eyes.search"
    SEARCH_RESULT = "eyes.search_result"
    VISUAL_QUERY = "eyes.visual_query" 
    STREAM_FRAME = "eyes.stream_frame"  # ← 新增：眼睛主动推送实时帧给视觉皮层
class RiskEvent:
    """风险感知相关事件"""
    ALERT = "risk.alert"
    # ★第161批段B B2：危机转介事件（独立于 risk.alert / risk.terminate 处置链）
    CRISIS_REFERRAL = "risk.crisis_referral"
class MediaEvent:
    """媒体文件相关事件"""
    METADATA = "media.metadata"
    IMAGE_DETECTED = "media.image.detected"
    AUDIO_DETECTED = "media.audio.detected"
    VIDEO_DETECTED = "media.video.detected"
    UNKNOWN = "media.unknown"
class HandsEvent:
    """双手/任务调度相关事件"""
    EXECUTE = "hands.execute"
    RESULT = "hands.result"
class LegsEvent:
    """双腿/网络抓取相关事件"""
    FETCH = "legs.fetch"
class WhiteCellEvent:
    """白细胞相关事件"""
    SCAN = "white_cell.scan"
    SCAN_RESULT = "white_cell.scan_result"
class HormonesEvent:
    """激素/情绪相关事件"""
    DETECT = "hormones.detect"
    EMOTION_DETECTED = "hormones.emotion_detected"
    CARE_NEEDED = "hormones.care_needed"
class EvolutionEvent:
    """进化相关事件""" 
    MUTATE = "evolution.mutate"
    SAVE_BLUEPRINT = "evolution.save_blueprint"
    MUTATION_SUCCESS = "evolution.mutation_success"
class ReproductionEthicsEvent:
    ETHICS_CHECK = "reproduction.ethics_check"
    ETHICS_RESULT = "reproduction.ethics_result"
class QICAEvent:
    """QICA心智模型相关事件"""
    CLASSIFY = "qica.classify"
    CLASSIFY_RESULT = "qica.classify_result"
class EthicsEvent:
    """伦理审查相关事件"""
    REVIEW = "ethics.review"
    REVIEW_RESULT = "ethics.review_result"

class GrowthEvent:
    """成长评估相关事件""" 
    ASSESS = "growth.assess"
    MILESTONE_REACHED = "growth.milestone_reached"
    NEED_DETECTED = "growth.need_detected"

class NarrativeEvent:
    """叙事自我相关事件"""
    RECORD = "narrative.record"
    REFLECT = "narrative.reflect"
    UPDATED = "narrative.updated"
    REFLECTION_RESULT = "narrative.reflection_result"

class PersonalityEvent:
    """人格内核相关事件"""
    VERIFY = "personality.verify"
    BOUNDARY_CHECK = "personality.boundary_check"
    INTEGRITY_REPORT = "personality.integrity_report"

class SelfAwarenessEvent:
    """自我认知相关事件"""
    CHECK_IDENTITY = "self_awareness.check_identity"

class ThymusEvent:
    """胸腺相关事件"""
    TRAIN = "thymus.train"
    TRAIN_RESULT = "thymus.train_result"

class SkinEvent:
    """皮肤相关事件"""
    REVIEW_PATCH = "skin.review_patch"
    REVIEW_RESULT = "skin.review_result"

class BoneMarrowEvent:
    """骨髓相关事件"""
    GENERATE = "bone_marrow.generate"
    GENERATE_RESULT = "bone_marrow.generate_result"

class DNARepairEvent:
    """DNA修复相关事件"""
    FIX = "dna_repair.fix"
    SOLUTION_GENERATED = "dna_repair.solution_generated"

class ConsentEvent:
    """共同决策相关事件"""
    PROPOSE = "consent.propose"
    DECIDE = "consent.decide"
    RESULT = "consent.result"

class NurtureEvent:
    """养育相关事件"""
    # ★P2-37：STAGE_CHANGED（nurture.stage_changed）已摘除 ——
    #   该脉冲此前已按 P3-5 作为孤儿脉冲删除（纯状态广播，全库无订阅方），
    #   此处同步摘除常量，避免后来者误以为还在发射。
    ADVANCE = "nurture.advance"

class BondingEvent:
    """情感羁绊相关事件"""
    # ★P2-37：UPDATED（bonding.updated）已摘除 —— 同上，P3-5 孤儿脉冲清理。
    RECORD = "bonding.record"

class EnergyEvent:
    """能量代谢相关事件"""
    ASSESS = "energy.assess"
    METABOLISM_SNAPSHOT = "energy.metabolism_snapshot"

class HealthEvent:
    """健康监控相关事件"""
    CHECK = "health.check"
    REPORT = "health.report"

class HardwareEvent:
    """硬件启动器相关事件"""
    ASSESS = "hardware.assess"
    LAUNCH_PLAN = "hardware.launch_plan"

class InferenceEngineEvent:
    """推理引擎相关事件"""
    EXECUTE = "inference.execute"
    RESULT = "inference.result"

class MetricsEvent:
    """指标采集器相关事件"""
    COLLECT = "metrics.collect"
    SNAPSHOT = "metrics.snapshot"

class DeviceEvent:
    """设备管理器相关事件"""
    ALLOCATE = "device.allocate"
    ALLOCATED = "device.allocated"
    REFRESH = "device.refresh"
    CAPABILITY_UPDATE = "device.capability_update"

class StressAxisEvent:
    """应激轴相关事件"""
    ACTIVATE = "stress.activate"
    RECOVER = "stress.recover"
    LEVEL_CHANGED = "stress.level_changed"

class SpinalCordEvent:
    """脊髓相关事件"""
    INSPECT = "spinal.inspect"
    INSPECTION_REPORT = "spinal.inspection_report"

class SystemManagerEvent:
    """系统管理器相关事件"""
    HEALTH_CHECK = "system.health_check"
    START_SERVICE = "system.start_service"
    SYSTEM_READY = "system.ready"
class ObservabilityEvent:
    """可观测性相关事件"""
    SNAPSHOT = "observability.snapshot"
class SecurityEvent:
    """P2-2: 三级安全沙箱事件"""
    THREAT_DETECTED = "security.threat_detected"
    BLOCKED = "security.blocked"
    PASSED = "security.passed"
    SANDBOX_VIOLATION = "security.sandbox_violation"
# ========== 脉冲意图枚举（P1预埋） ==========

class PulseIntent:
    """
    脉冲意图枚举 —— 标准化脉冲元信息中的意图字段。
    
    使用方式:
        pulse_payload["intent"] = PulseIntent.REQUEST
    """
    REQUEST = "request"
    SUGGEST = "suggest"
    ALERT = "alert"
    NOTIFY = "notify"
    QUESTION = "question"

# ========== v9.5.1: 控制器器官事件 ==========

class ControllerEvent:
    """控制器器官相关事件"""
    # 进程调度
    LAUNCH_APP = "controller.launch_app"
    OPEN_URL = "controller.open_url"
    CLOSE_WINDOW = "controller.close_window"
    SEARCH_STAGE_COMPLETED = "controller.search_stage_completed"  # 新增：搜索阶段完成反馈   
    SEARCH_COMPLETED = "controller.search_completed"     
    
    # 文件操作
    READ_FILE = "controller.read_file"
    WRITE_FILE = "controller.write_file" # 预留：文件写入能力（阶段3）
    LIST_DIRECTORY = "controller.list_directory" # 预留：文件内容回传（阶段3）
    FILE_CONTENT = "controller.file_content"
    
    # 桌面自动化
    MOUSE_CLICK = "controller.mouse_click"
    INPUT_TEXT = "controller.input_text"
    CAPTURE_SCREEN = "controller.capture_screen"
    
    # 结果回传
    RESULT = "controller.result"
    ERROR = "controller.error"

class SearchEvent:
    """★第86批 T-86b 预埋：搜索生命周期事件契约（信号分层）。

    背景（第83批 T-c1(3) 提出、本批落地）：此前「终止信号」与「结果信号」混用同一个
    ``controller.search_stage_completed``。订阅方无法从**事件类型**上区分
    「这一阶段有结果了」与「这次搜索被终止了」，只能靠 payload["status"] 猜；
    终止信号（stage=-1 / articles_found=0）一旦落到结果审查通路，就会被读成
    「阶段2无文章产出 → 接受兜底」，即把『终止』误读为『我来兜底』。

    分层后每个终态是独立事件，订阅方按事件类型分流：
        COMPLETED   —— 搜索正常产出结果（结果信号）
        FAILED      —— 搜索执行失败（结果信号，可按失败策略处理）
        TERMINATED  —— 被内在世界审查判定终止（终止信号，不得进结果审查/兜底）
    旧的 STAGE_COMPLETED 仍保留，仅表示「某阶段进行中反馈」，不再承载终止语义。
    """

    # 预留：搜索成功完成（结果信号）
    COMPLETED = "search.completed"
    # 预留：搜索执行失败（结果信号）
    FAILED = "search.failed"
    # 搜索被内在世界审查终止（终止信号，非结果信号）
    TERMINATED = "search.terminated"
    # 旧「阶段完成反馈」事件名（非终态信号，供控制器↔内在世界进行中反馈）
    STAGE_COMPLETED = ControllerEvent.SEARCH_STAGE_COMPLETED


    


# ========== 第58批 T5：统一事件常量（集中管理此前散落的硬编码事件名）==========

class Event:
    """
    统一事件常量（主线第58批 T5 专项）。

    此前各器官中大量事件名以裸字符串形式硬编码在 _emit / emit / publish 调用处
    （如 self._emit("express.urge", ...)），存在拼写难查、重构困难、追踪不完整等问题。
    本类将高频事件名集中为常量，业务代码须统一引用 Event.XXX，禁止直接硬编码事件名字符串。

    命名规范：事件名 "a.b.c" -> 常量 A_B_C；常量值与原字符串完全一致，替换不改变运行时行为。
    新增事件必须在此登记（见下方 register_event / is_registered_event）。
    """
    # —— 复用既有 const 事件类成员（单一事实来源，不重复定义值）——
    HEART_BEAT = HeartEvent.BEAT
    MOTOR_EXECUTE = MotorEvent.EXECUTE
    REFLECTION_INSIGHT = ReflectionEvent.INSIGHT
    INNER_WORLD_CACHE_CLEAR = InnerWorldEvent.CACHE_CLEAR
    LUNGS_SELECT_MODEL = LungEvent.SELECT_MODEL
    HORMONES_DETECT = HormonesEvent.DETECT
    GROWTH_NEED_DETECTED = GrowthEvent.NEED_DETECTED
    NARRATIVE_RECORD = NarrativeEvent.RECORD
    STRESS_RECOVER = StressAxisEvent.RECOVER
    CONTROLLER_OPEN_URL = ControllerEvent.OPEN_URL
    CONTROLLER_SEARCH_STAGE_COMPLETED = ControllerEvent.SEARCH_STAGE_COMPLETED
    # —— 第86批 T-86b：搜索终态事件契约（信号分层，统一注册表登记）——
    SEARCH_COMPLETED = SearchEvent.COMPLETED
    SEARCH_FAILED = SearchEvent.FAILED
    SEARCH_TERMINATED = SearchEvent.TERMINATED
    # —— 此前纯硬编码、本次新增常量 ——
    EXPRESS_URGE = "express.urge"
    LEGS_LEARN_NOW = "legs.learn_now"
    SEMANTIC_CLASSIFY = "semantic.classify"
    CORTEX_DIALOG_START = "cortex.dialog.start"
    ORGAN_HANDBOOK_UPDATED = "organ_handbook_updated"
    TOOL_CREATED = "tool.created"
    INTUITION_REINFORCE = "intuition.reinforce"
    LIFE_STATE_CHANGED = "life_state.changed"
    DREAM_DEDUCTION = "dream.deduction"
    # —— 主线第59批 T3：补充 11 个低频事件常量（此前纯硬编码）——
    CARE_INITIATIVE = "care.initiative"
    CHAT_MESSAGE = "chat.message"
    CURIOSITY_TICK = "curiosity.tick"
    DIGEST_KNOWLEDGE = "digest.knowledge"
    ENVIRONMENT_MUTATED = "environment.mutated"
    GLOBAL_LEARNER_DRIFT_DETECTED = "global_learner.drift_detected"
    MOTIVATION_URGE = "motivation.urge"
    CONSTRAINT_SELF_MODIFY_FORBIDDEN = "constraint.self_modify_forbidden"
    CONSTRAINT_AGGRESSIVE_RESTRUCTURE_FORBIDDEN = "constraint.aggressive_restructure_forbidden"
    UNKNOWN_EVENT = "unknown.event"
    STRESS_ACTIVATE = StressAxisEvent.ACTIVATE  # ★复用既有 StressAxisEvent.ACTIVATE，不重复定义值


# ========== 第58批 T5：事件注册表与登记机制 ==========
_EVENT_CONSTANTS = {
    _n: _v for _n, _v in vars(Event).items()
    if not _n.startswith("_") and isinstance(_v, str)
}


def register_event(name, value):
    """登记新事件常量（唯一入口）。新增事件禁止在业务代码中硬编码字符串，必须在此登记。"""
    if not isinstance(name, str) or not name:
        raise ValueError("事件常量名必须为非空字符串")
    if not isinstance(value, str) or not value:
        raise ValueError("事件值必须为非空字符串")
    if name in _EVENT_CONSTANTS:
        if _EVENT_CONSTANTS[name] != value:
            raise ValueError(
                "事件常量 {} 已存在且值不一致: {!r} != {!r}".format(name, _EVENT_CONSTANTS[name], value)
            )
        return
    setattr(Event, name, value)
    _EVENT_CONSTANTS[name] = value


def is_registered_event(value):
    """判断某事件名字符串是否已在统一常量中登记（用于硬编码校验 / lint）。"""
    return value in set(_EVENT_CONSTANTS.values())


def all_event_values():
    """返回全部已登记事件名字符串集合（用于去重与硬编码排查）。"""
    return frozenset(_EVENT_CONSTANTS.values())
