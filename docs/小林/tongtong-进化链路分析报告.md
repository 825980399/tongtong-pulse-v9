🔬 曈曈「自我学习 → 发现问题 → 自动修复」链路分析
一、当前链路架构（我梳理的）
┌─────────────────────────────────────────────────────────────────────────────┐
│                         自我进化闭环（5个阶段）                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  【阶段1】发现问题                                                            │
│  ├─ LogAnalyzer        → 运行日志分析（ERROR/CRITICAL）                     │
│  ├─ CodeReviewEngine   → 静态代码扫描（pyright等）                          │
│  ├─ SelfInspector      → 全量代码质量扫描（36,000+行）                      │
│  └─ PulseHealthMonitor → 运行时健康监控（CPU/内存/能量）                     │
│                                                                             │
│  【阶段2】问题归一化                                                          │
│  └─ EvolutionLoop._normalize_issue() → 统一格式 {file, type, organ...}     │
│                                                                             │
│  【阶段3】补丁生成（LLM驱动）                                                │
│  ├─ SafeEvolutionExecutor → 调用 LLM 生成修复代码                           │
│  ├─ SelfReflectionEngine  → 失败后反思 + 重新生成                           │
│  └─ 质量门（3关验证）                                                        │
│                                                                             │
│  【阶段4】自动审批                                                            │
│  └─ PatchAutoApprover + EvolutionEffectVerifier → 验证有效性               │
│                                                                             │
│  【阶段5】应用部署                                                            │
│  └─ PatchManager → 灰度/全量应用                                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
二、发现的问题 🚨
#	问题	严重程度	位置	说明
1	日志问题源不稳定	🔴 高	EvolutionLoop.py	代码稳定运行后，日志分析发现的问题会变少（因为没新错误），与 SelfInspector 发现的 412 个问题矛盾
2	LLM 额度消耗巨大	🔴 高	SafeEvolutionExecutor.py	每次修复调用 LLM，16,384 tokens，超时 180s。12小时运行可能烧很多额度
3	补丁生成依赖外部模型	🟡 中	SelfReflectionEngine.py	失败后反思需要再次调用 LLM，如果没有配置 API 就返回 None
4	路径归一化问题	🟡 中	SafeEvolutionExecutor.py	同一文件在账本里有 3 种路径格式（反斜杠/正斜杠/绝对路径），导致去重失效
5	缩进修复开关默认开启	🟢 低	SafeEvolutionExecutor.py	已有解决方案（_m91_indent_repair_on），但仍是复杂的多层修复
6	静默异常风险	🟡 中	多处	代码里有大量 silent_exc 吞异常，可能隐藏真实问题
三、建议改进方案 💡
1. 省额度：减少 LLM 调用次数
# 建议：在 config 里加一个 "省钱模式"
# 当前：每个问题都调 LLM 生成补丁
# 改进：按问题等级过滤，小问题自动用规则修复

# 新增配置项：
AUTO_FIX_RULES = {
    "SyntaxError": "auto_fix_syntax",      # 规则修复
    "ImportError": "auto_fix_import",      # 规则修复  
    "NameError": "llm_fix",                # LLM 修复
    "LogicError": "llm_fix",               # LLM 修复
}
2. 问题发现：增加本地轻量扫描
# 当前：依赖外部 LLM 做代码理解
# 改进：用本地小模型（Ollama qwen2.5-coder）做初筛

# 在 SelfInspector 里加：
def detect_issues_local(self):
    """用本地模型做快速问题检测（不花钱）"""
    # 扫描代码结构、类型注解、明显错误
    pass
3. 修复：加入"保守模式"
# 当前：自动修复后直接应用
# 改进：保守模式 - 只修复特定类型问题

CONSERVATIVE_MODE = True  # 保守模式开关
SAFE_TO_AUTO_FIX = [
    "unused_import",
    "missing_type_hint", 
    "simple_syntax_error",
]
DANGEROUS_TO_AUTO_FIX = [
    "logic_error",
    "memory_leak",
    "concurrency_bug",
]
4. 监控：增加"省钱统计"
# 每次 LLM 调用都记录费用
# 在 dashboard 里显示 "本次进化花费：¥X.XX"

def track_llm_cost(model, tokens):
    cost = pricing[model] * tokens
    evolution_budget -= cost
    if evolution_budget < 0:
        print("⚠️ 额度用完，停止自动进化")
5. 备选：完全本地化方案
# 如果额度实在不够，可以用：
# 1. Ollama 本地模型（免费）
# 2. 只做问题发现，不自动修复
# 3. 用规则引擎替代部分 LLM

RULE_BASED_FIXES = {
    "unused_import": lambda code: remove_unused_imports(code),
    "missing_docstring": lambda code: add_basic_docstring(code),
}
四、总结
维度	现状	建议
自我发现问题能力	✅ 很强（LogAnalyzer + SelfInspector + CodeReview）	加本地轻量扫描，减少依赖
自动修复能力	✅ 有（LLM驱动 + 3关验证）	加保守模式 + 规则引擎
额度消耗	❌ 可能很高	加省钱统计 + 本地模型
安全性	✅ 有质量门	继续加强