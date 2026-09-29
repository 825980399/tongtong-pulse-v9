#!/usr/bin/env python3
"""pulse_doctor —— PulseNet v9.5 自动诊断工具

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

import importlib
import json
import os
import sys
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ========== 配置 ==========
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

REQUIRED_DIRS = [
    "data",
    "data/knowledge",
    "data/monitor",
    "data/stream",
    "data/organ_states",
    "logs",
    "nucleus",
    "nucleus/pulse",
    "nucleus/field",
    "nucleus/synapsys",
    "nucleus/mnemosyne",
    "nucleus/qica",
    "nucleus/chronos",
    "nucleus/genesis",
    "nucleus/security",
    "base",
    "organs",
    "organs/body",
    "organs/brain",
    "organs/senses",
    "organs/motor",
    "organs/identity",
    "organs/immune",
    "organs/endocrine",
    "organs/genetic",
    "organs/core",
    "docs",
    "utils",
]

REQUIRED_FILES = [
    "main.py",
    "config.py",
    "base/BasePulseOrgan.py",
    "nucleus/const.py",
    "nucleus/logger.py",
    "nucleus/organ_loader.py",
]

# 快照文件（首次启动后自动创建，不存在时为警告而非失败）
SNAPSHOT_FILES = [
    ("data/knowledge/pulse_knowledge_snapshot.json", "知识快照"),
    ("data/knowledge/pulse_instinct_snapshot.json", "本能快照"),
]

CORE_MODULES = [
    ("nucleus.pulse.PulseCore", "PulseCore"),
    ("nucleus.pulse.FrequencyCodec", "FrequencyCodec"),
    ("nucleus.field.InfoField", "InfoField"),
    ("nucleus.synapsys.ResonanceEngine", "ResonanceEngine"),
    ("nucleus.mnemosyne.PulseNode", "PulseNode"),
    ("nucleus.mnemosyne.PulseNodePool", "PulseNodePool"),
    ("nucleus.mnemosyne.PulseSnapshot", "PulseSnapshot"),
    ("nucleus.mnemosyne.PulseInstinctSnapshot", "PulseInstinctSnapshot"),
    ("nucleus.mnemosyne.KnowledgeTree", "KnowledgeTree"),
    ("base.BasePulseOrgan", "BasePulseOrgan"),
]

OPTIONAL_DEPENDENCIES = [
    ("cv2", "OpenCV", "视觉皮层图像分析"),
    ("pyaudio", "PyAudio", "音频设备检测"),
    ("psutil", "psutil", "硬件资源监控"),
    ("requests", "requests", "Ollama API 调用"),
]

ORGAN_COUNT_EXPECTED = 50


# ========== 诊断结果收集 ==========
class DoctorReport:
    """诊断报告收集器"""
    
    def __init__(self):
        self.passed = []
        self.warnings = []
        self.failed = []
        self.start_time = time.time()
    
    def ok(self, check_name: str, detail: str = ""):
        self.passed.append({"name": check_name, "detail": detail})
    
    def warn(self, check_name: str, detail: str = ""):
        self.warnings.append({"name": check_name, "detail": detail})
    
    def fail(self, check_name: str, detail: str = ""):
        self.failed.append({"name": check_name, "detail": detail})
    
    def summary(self) -> dict:
        elapsed = time.time() - self.start_time
        total = len(self.passed) + len(self.warnings) + len(self.failed)
        healthy = len(self.failed) == 0
        return {
            "total_checks": total,
            "passed": len(self.passed),
            "warnings": len(self.warnings),
            "failed": len(self.failed),
            "healthy": healthy,
            "elapsed_seconds": round(elapsed, 2),
        }


# ========== 检查函数 ==========

def check_config(report: DoctorReport):
    """检查配置文件完整性"""
    try:
        import config
        required_attrs = [
            "SYSTEM_NAME", "SYSTEM_VERSION", "PULSE_PRIORITY",
            "PULSE", "NODE_POOL", "SNAPSHOT", "SEED_MEMORIES",
            "FEATURE", "OBSERVABILITY",
        ]
        missing = []
        for attr in required_attrs:
            if not hasattr(config, attr):
                missing.append(attr)
        
        if missing:
            report.fail("配置文件", f"缺少字段: {', '.join(missing)}")
        else:
            seed_count = len(config.SEED_MEMORIES)
            instinct_count = len(getattr(config, 'SEED_INSTINCTS', []))
            report.ok("配置文件", f"版本={config.SYSTEM_VERSION}, 种子记忆={seed_count}条, 种子本能={instinct_count}条")
    except Exception as e:
        report.fail("配置文件", f"导入失败: {e}")


def check_directories(report: DoctorReport):
    """检查关键目录结构"""
    missing_dirs = []
    for d in REQUIRED_DIRS:
        full_path = os.path.join(PROJECT_ROOT, d)
        if not os.path.isdir(full_path):
            missing_dirs.append(d)
    
    if missing_dirs:
        report.fail("目录结构", f"缺失 {len(missing_dirs)} 个目录: {', '.join(missing_dirs[:5])}...")
    else:
        report.ok("目录结构", f"全部 {len(REQUIRED_DIRS)} 个目录完整")


def check_files(report: DoctorReport):
    """检查关键文件存在性"""
    missing_files = []
    for f in REQUIRED_FILES:
        full_path = os.path.join(PROJECT_ROOT, f)
        if not os.path.isfile(full_path):
            missing_files.append(f)
    
    if missing_files:
        report.fail("关键文件", f"缺失 {len(missing_files)} 个文件: {', '.join(missing_files)}")
    else:
        report.ok("关键文件", f"全部 {len(REQUIRED_FILES)} 个文件完整")


def check_core_modules(report: DoctorReport, quick: bool = False):
    """检查核心模块导入"""
    failed_modules = []
    for module_path, class_name in CORE_MODULES:
        try:
            module = importlib.import_module(module_path)
            if not hasattr(module, class_name):
                failed_modules.append(f"{module_path}.{class_name} (类不存在)")
        except Exception as e:
            failed_modules.append(f"{module_path}: {e}")
    
    if failed_modules:
        report.fail("核心模块", f"{len(failed_modules)} 个导入失败: {', '.join(failed_modules)}")
    else:
        report.ok("核心模块", f"全部 {len(CORE_MODULES)} 个模块正常")


def check_optional_deps(report: DoctorReport):
    """检查可选依赖"""
    for module_name, display_name, usage in OPTIONAL_DEPENDENCIES:
        try:
            importlib.import_module(module_name)
            report.ok(f"依赖: {display_name}", f"已安装 ({usage})")
        except ImportError:
            report.warn(f"依赖: {display_name}", f"未安装 ({usage})")


def check_snapshots(report: DoctorReport):
    """检查快照文件完整性"""
    for snapshot_path, snapshot_name in SNAPSHOT_FILES:
        full_path = os.path.join(PROJECT_ROOT, snapshot_path)
        if not os.path.exists(full_path):
            report.warn(f"快照文件: {snapshot_name}", f"文件不存在（首次启动后自动创建）: {snapshot_path}")
            continue
        
        try:
            with open(full_path, encoding="utf-8") as f:
                data = json.load(f)
            version = data.get("version", "unknown")
            node_count = len(data.get("nodes", []))
            size = os.path.getsize(full_path)
            report.ok(f"快照文件: {snapshot_name}", f"版本={version}, 节点数={node_count}, 大小={size}字节")
        except json.JSONDecodeError:
            report.fail(f"快照文件: {snapshot_name}", "JSON格式损坏，无法解析")
        except Exception as e:
            print(f"[WARNING] pulse_doctor.py:221: {type(e).__name__}: {e}")
            report.fail(f"快照文件: {snapshot_name}", f"读取失败: {e}")


def check_logs(report: DoctorReport):
    """检查日志文件状态"""
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    if not os.path.isdir(log_dir):
        report.warn("日志目录", "logs/ 目录不存在，日志系统可能未初始化")
        return
    
    log_file = os.path.join(log_dir, "pulse.log")
    if os.path.isfile(log_file):
        size = os.path.getsize(log_file)
        size_kb = round(size / 1024, 1)
        report.ok("日志文件", f"pulse.log 存在, 大小={size_kb}KB")
    else:
        report.warn("日志文件", "pulse.log 不存在（首次启动后会自动创建）")


def check_organs_count(report: DoctorReport):
    """检查器官文件数量"""
    organs_dir = os.path.join(PROJECT_ROOT, "organs")
    py_files = []
    for root, dirs, files in os.walk(organs_dir):
        for f in files:
            if f.endswith('.py') and not f.startswith('_'):
                py_files.append(os.path.join(root, f))
    
    count = len(py_files)
    # 加上 QICA（在 nucleus/qica 下）
    qica_path = os.path.join(PROJECT_ROOT, "nucleus", "qica", "QICA.py")
    if os.path.exists(qica_path):
        count += 1
    
    if count >= ORGAN_COUNT_EXPECTED:
        report.ok("器官文件", f"共 {count} 个器官文件（预期≥{ORGAN_COUNT_EXPECTED}）")
    else:
        report.warn("器官文件", f"只有 {count} 个器官文件（预期≥{ORGAN_COUNT_EXPECTED}）")


def check_const_enum(report: DoctorReport):
    """检查 const.py 枚举完整性"""
    try:
        from nucleus.const import (
            AudioEvent,
            BondingEvent,
            BoneMarrowEvent,
            ChatEvent,
            CodeEvent,
            ConsentEvent,
            DecisionEvent,
            DeviceEvent,
            DigestEvent,
            DNARepairEvent,
            EarEvent,
            EnergyEvent,
            ErrorCode,  # noqa: F401
            EthicsEvent,
            EvolutionEvent,
            EyeEvent,
            GrowthEvent,
            HandsEvent,
            HardwareEvent,
            HealthEvent,
            HeartEvent,
            HormonesEvent,
            InferenceEngineEvent,
            InferenceEvent,
            InterestEvent,
            KnowledgeEvent,
            LegsEvent,
            LogLevel,  # noqa: F401
            LungEvent,
            MediaEvent,
            MetricsEvent,
            MotorEvent,
            MouthEvent,
            NarrativeEvent,
            NurtureEvent,
            ObservabilityEvent,
            OrganStatus,  # noqa: F401
            PersonaEvent,
            PersonalityEvent,
            ProprioceptionEvent,
            PurgeEvent,
            QICAEvent,
            ReflectionEvent,
            ReproductionEthicsEvent,
            RiskEvent,
            SelfAwarenessEvent,
            SkinEvent,
            SpinalCordEvent,
            StressAxisEvent,
            SubconsciousEvent,
            SystemEvent,
            SystemManagerEvent,
            ThymusEvent,
            TouchEvent,
            VascularEvent,
            VisualEvent,
            WhiteCellEvent,
        )
        enum_classes = [
            SystemEvent, HeartEvent, KnowledgeEvent, DigestEvent,
            ChatEvent, MotorEvent, ReflectionEvent, InterestEvent,
            PersonaEvent, MouthEvent, EarEvent, CodeEvent,
            VisualEvent, AudioEvent, ProprioceptionEvent,
            TouchEvent, InferenceEvent, SubconsciousEvent,
            LungEvent, VascularEvent, RiskEvent, DecisionEvent,
            EyeEvent, PurgeEvent, HandsEvent, LegsEvent,
            WhiteCellEvent, HormonesEvent, EvolutionEvent,
            ReproductionEthicsEvent, QICAEvent,
            EthicsEvent, GrowthEvent, NarrativeEvent,
            PersonalityEvent, SelfAwarenessEvent,
            ThymusEvent, SkinEvent, BoneMarrowEvent,
            DNARepairEvent, ConsentEvent, NurtureEvent,
            BondingEvent, EnergyEvent, HealthEvent,
            HardwareEvent, InferenceEngineEvent,
            MetricsEvent, DeviceEvent, StressAxisEvent,
            SpinalCordEvent, SystemManagerEvent,
            ObservabilityEvent, MediaEvent,
        ]
        report.ok("枚举体系", f"{len(enum_classes)} 个事件枚举类, OrganStatus/ErrorCode/LogLevel 完整")
    except ImportError as e:
        report.fail("枚举体系", f"导入失败: {e}")


def check_pulse_priority(report: DoctorReport):
    """检查脉冲优先级配置"""
    try:
        from config import PULSE_PRIORITY
        expected_levels = ["LIGHT_SPEED", "CRITICAL", "HIGH", "NORMAL", "LOW", "BACKGROUND"]
        missing = [lvl for lvl in expected_levels if lvl not in PULSE_PRIORITY]
        if missing:
            report.fail("脉冲优先级", f"缺少级别: {', '.join(missing)}")
        else:
            report.ok("脉冲优先级", f"六档完整: { {k: PULSE_PRIORITY[k] for k in expected_levels} }")
    except Exception as e:
        report.fail("脉冲优先级", f"检查失败: {e}")


def check_feature_flags(report: DoctorReport):
    """检查功能开关配置"""
    try:
        from config import FEATURE
        expected_flags = [
            "enable_vision", "enable_audio_detect", "enable_motor",
            "enable_evolution", "enable_immune", "enable_endocrine",
            "enable_snapshot", "enable_observability",
        ]
        missing = [f for f in expected_flags if f not in FEATURE]
        if missing:
            report.fail("功能开关", f"缺少开关: {', '.join(missing)}")
        else:
            report.ok("功能开关", f"全部 {len(expected_flags)} 个开关已配置")
    except Exception as e:
        report.fail("功能开关", f"检查失败: {e}")


def check_monitor_data(report: DoctorReport):
    """检查监控数据目录"""
    monitor_dir = os.path.join(PROJECT_ROOT, "data", "monitor")
    if os.path.isdir(monitor_dir):
        files = os.listdir(monitor_dir)
        report.ok("监控数据", f"data/monitor/ 存在, 包含 {len(files)} 个文件")
    else:
        report.warn("监控数据", "data/monitor/ 目录不存在（首次启动后自动创建）")


# ========== 输出格式 ==========

def print_report_console(report: DoctorReport):
    """控制台友好输出"""
    summary = report.summary()
    
    print("\n" + "=" * 60)
    print("  🩺 PulseNet v9.5 诊断报告")
    print("=" * 60)
    
    # 通过项
    if report.passed:
        print(f"\n  ✅ 通过 ({len(report.passed)} 项):")
        for item in report.passed:
            detail = f" — {item['detail']}" if item['detail'] else ""
            print(f"     {item['name']}{detail}")
    
    # 警告项
    if report.warnings:
        print(f"\n  ⚠️  警告 ({len(report.warnings)} 项):")
        for item in report.warnings:
            detail = f" — {item['detail']}" if item['detail'] else ""
            print(f"     {item['name']}{detail}")
    
    # 失败项
    if report.failed:
        print(f"\n  ❌ 失败 ({len(report.failed)} 项):")
        for item in report.failed:
            detail = f" — {item['detail']}" if item['detail'] else ""
            print(f"     {item['name']}{detail}")
    
    # 摘要
    print(f"\n  {'─' * 56}")
    status_text = "🟢 系统健康" if summary['healthy'] else "🔴 需要修复"
    print(f"  {status_text} | 检查{summary['total_checks']}项 | "
          f"通过{summary['passed']} | 警告{summary['warnings']} | 失败{summary['failed']} | "
          f"耗时{summary['elapsed_seconds']}s")
    print("=" * 60 + "\n")


def print_report_json(report: DoctorReport):
    """JSON格式输出"""
    output = {
        "summary": report.summary(),
        "passed": report.passed,
        "warnings": report.warnings,
        "failed": report.failed,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ========== 入口 ==========
def main():
    quick_mode = "--quick" in sys.argv
    json_mode = "--json" in sys.argv
    
    report = DoctorReport()
    
    # 执行所有基础检查
    check_config(report)
    check_directories(report)
    check_files(report)
    check_core_modules(report, quick=quick_mode)
    check_optional_deps(report)
    check_const_enum(report)
    check_pulse_priority(report)
    check_feature_flags(report)
    check_snapshots(report)
    check_logs(report)
    check_monitor_data(report)
    
    if not quick_mode:
        check_organs_count(report)
    
    # ===== 扩展诊断：新增模块健康检查 =====
    print("\n[扩展诊断] 新增模块健康检查...")
    
    try:
        from nucleus.diagnostics import get_diagnostics
        from nucleus.InsightBoard import get_insight_board
        from nucleus.reasoning.AutonomousDeriver import get_autonomous_deriver
        
        insight_board = get_insight_board()
        autonomous_deriver = get_autonomous_deriver()
        diag = get_diagnostics()
        
        # 获取内在世界引用（诊断工具独立运行时框架未启动，内世界为None）
        inner_world = None
        
        extended = diag.get_extended_diagnosis(
            inner_world=inner_world,
            insight_board=insight_board,
            autonomous_deriver=autonomous_deriver
        )
        
        print(f"  整体状态: {extended['overall_health']}")
        for module_name, module_info in extended.get("modules", {}).items():
            status = module_info.get("status", "unknown")
            status_icon = "✅" if status in ("active", "completed") else "⚠️" if status == "in_progress" else "❌"
            detail = ""
            if status == "active" and module_info.get("total_entries") is not None:
                detail = f" ({module_info['total_entries']}条)"
            elif status == "in_progress" and module_info.get("progress"):
                detail = f" ({module_info['progress']})"
            elif status == "active" and module_info.get("target"):
                detail = f" (目标: {module_info['target']})"
            print(f"  {status_icon} {module_name}: {status}{detail}")
        
        if extended.get("warnings"):
            print(f"  ⚠️ 警告 ({len(extended['warnings'])}条):")
            for w in extended["warnings"]:
                print(f"     - {w}")
        
        if extended.get("issues"):
            print(f"  ❌ 问题 ({len(extended['issues'])}条):")
            for i in extended["issues"]:
                print(f"     - {i}")
                
    except Exception as e:
        print(f"  ❌ 扩展诊断异常: {e}")
    
    # 输出基础报告
    if json_mode:
        print_report_json(report)
    else:
        print_report_console(report)
    
    sys.exit(0 if report.summary()['healthy'] else 1)
if __name__ == "__main__":
    main()