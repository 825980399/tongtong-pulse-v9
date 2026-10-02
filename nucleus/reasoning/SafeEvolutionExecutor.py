# -*- coding: utf-8 -*-
"""
SafeEvolutionExecutor.py —— 安全进化执行器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 安全可控的进化代码执行
机制: 大型模块（3772行），包含1个类、10个核心方法，采用分层架构实现
定位: 进化执行层
"""

from nucleus.LLMDependencyMetrics import (SCENE_EVOLUTION, record_llm_call)
from nucleus.llm.call_recorder import trace_evolution_call
import os
import time
from typing import Any

from nucleus.evolution.LogAnalyzer import is_error_level_line  # ★第30批 T2
from nucleus.logger import get_module_logger


# ★第九批 B-3：置信度证据化——由「硬编码常数」改为
#   0.9 × 该类型历史成功率系数 × 证据强度系数（开关关闭时原值返回）
from nucleus.reasoning.SelfCalibrator import evidence_confidence as _evidence_conf
from nucleus.data.DataAccessLayer import safe_read_json
# ★第117批 T-117d①：跨盘安全 relpath（path_utils 只依赖 os，无循环导入风险）
from nucleus.data.path_utils import safe_relpath as _safe_relpath
from nucleus.api_rate_limiter import get_llm_call_config, api_rate_limited
from nucleus._silent_except import silent_exc
import config  # ★主线第59批 T2：问题发现器路径过滤需读取 config 运行时配置
from config import DEFAULT_BENEFIT_SCORE as _DEF_BENEFIT_SCORE  # ★第55批 T1


_module_logger = get_module_logger("SafeEvolutionExecutor")

# ★第117批 T-117d①（烛微 N4）：项目根（供问题身份键做路径归一）
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _normalize_file_key(file_val: str) -> str:
    """★第117批 T-117d①（N4）：把 file 字段归一成「形制唯一」的键。

    背景（烛微 117 §2-N4 实证）：补丁账本里同一目标文件**三种形制并存** ——
        ``organs\\brain\\PulseSubconscious.py``（反斜杠·相对）
        ``organs/brain/PulseSubconscious.py``（正斜杠·相对）
        ``D:\\...\\organs\\brain\\PulseSubconscious.py``（反斜杠·绝对）
    于是同一个题在「去重 / 分组 / 冷却」三处被算成 2~3 个不同的键，
    冷却计数被稀释、去重失效 —— 双指纹格式已于 T-116a③ 统一，
    但**路径形制**这一层此前没归一。

    归一四步：normpath（消 ``.``/``..``/重复分隔符）→ safe_relpath(项目根)
    （绝对→相对，跨盘安全降级）→ normcase（消大小写）→ 分隔符统一 ``/``。
    """
    if not file_val:
        return ""
    try:
        _p = os.path.normpath(str(file_val))
        _p = _safe_relpath(_p, _PROJECT_ROOT)
        _p = os.path.normcase(_p)
        return _p.replace(os.sep, "/").replace("\\", "/")
    except Exception:
        return str(file_val)

# ★第86批 T-86a（P0）：LLM 修复补丁零产出根因修复 —— 推理模型 token 预算。
#   根因（已实测复现）：REMOTE_API_CONFIG 指向的 deepseek-v4-flash 属**推理模型**，
#   响应先产出 reasoning_content 再产出 content。原 max_tokens=1500 被推理解析
#   全部吃光 → finish_reason=length、content 为空串、completion_tokens 打满 1500，
#   再由 `if not answer: return None` 把整条 LLM 补丁通道变成恒零产出且无任何日志。
#   修复（最小）：为修复调用预留足够预算。实测同 prompt：1500 → length/content 0 字；
#   8000 → stop/content 1311 字（reasoning 约 1.8 万字，completion 约 6.5k tokens）。
_LLM_REPAIR_MAX_TOKENS = 16384

# ★第86批 T-86a：推理模型单次耗时显著更长（实测 8000 档约 27s），而
#   api_rate_limiter 的 evolution 默认超时仅 30s，余量过紧易被读超时打断，
#   故修复调用取「配置值与下限的较大者」；**不修改全局超时配置**。
_LLM_REPAIR_MIN_TIMEOUT = 180
# ★第91批 T-91b：LLM 补丁**缩进契约**开关（prompt 约束 + 后处理修复 + 基础缩进对齐）。
#   开启（默认）→ 三层同时生效：
#     ① system prompt 显式要求「保持与输入代码完全相同的缩进层级与宽度、禁用 Tab」；
#     ② `_clean_llm_code` 阶段3：把「缩进漂移」修复为结构自洽
#        （行首 tab→空格 + 只修 Python 实际报错那一行的缩进层级）；
#     ③ 补丁构造时把 `modified_code` 的**基础缩进**整体对齐到 `original_code`。
#   关闭 → 三层同时关闭，逐字回到第90批末行为，零回归。
#   为什么必须补（第91批 T0 实测，非推测）：
#     · 4 次 LLM 补丁尝试全部因 `unindent does not match any outer indentation
#       level (<llm-patch>, line 26/19/11/9)` 被完整性关2 拒绝；
#     · 原 prompt 只有「ASCII/标点/括号」要求，**完全未提缩进**；
#     · 真正危险的是**静默分支**：缩进塌陷到 col0 时 ast.parse 反而通过 ⇒
#       2026-09-20 已把 PulseInnerWorld.py（类方法 367→270）与 PulseLung.py
#       （86→45）的类体提前终止，而三关 + py_compile + import **全部放行**。
def _m91_indent_repair_on() -> bool:
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M91_LLM_INDENT_REPAIR", True))
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_m91_indent_repair_on L98")
        return True


def _m91_gate2_parse(code: str) -> tuple:
    """与 `PatchManager._check_llm_patch_completeness` **关2 同口径**的解析，
    返回 `(是否通过, 失败文案, 包裹后文本中的错误行号)`。

    口径与关2 逐字一致：
        ast.parse("def _wrap():\n" + textwrap.indent(textwrap.dedent(mod).strip(), "    "),
                  filename="<llm-patch>")
    行号（无错为 0）用于 `_m91_repair_indentation` **精确定位**要修的那一行。

    ★为什么不能改用 `_clean_llm_code` 自身的 `ast.parse(_code)` 作判据（第91批 T0 实测）：
    真实补丁的 original/modified 是**原样缩进的方法体片段**（base 4/8/12…，含 return），
    直接 `ast.parse` 会同时踩「顶层 unexpected indent」与「顶层 return」两个坑，
    对 base>0 的片段**恒**失败 ⇒ 任何「修复结果」都无法通过判据、永不被采纳。
    即：修复必须用**下游验证关的口径**自检，否则修了也白修。
    """
    try:
        import ast as _ast_g2
        import textwrap as _tw_g2
        _g2_norm = _tw_g2.dedent(code or "").strip()
        _ast_g2.parse("def _wrap():\n" + _tw_g2.indent(_g2_norm, "    "),
                      filename="<llm-patch>")
        return True, "", 0
    except SyntaxError as _g2_e:
        return False, f"{type(_g2_e).__name__}: {_g2_e}", int(getattr(_g2_e, "lineno", 0) or 0)
    except Exception as _g2_e2:
        return False, f"{type(_g2_e2).__name__}: {_g2_e2}", 0


def _m91_gate2_check(code: str) -> tuple:
    """关2 同口径判据，返回 `(是否通过, 失败文案)`。

    ★同口径同步铁律：本函数与 PatchManager 关2 必须同步演进。
    `tests/test_llm_indent_contract_m91.py` 用**生产补丁库的真补丁**交叉校验
    （它们的关2 结论均为 PASS ⇒ 本函数也必须为 True），关2 改动破坏同步时该测试转红。
    """
    _g2a, _g2b, _g2c = _m91_gate2_parse(code)
    return _g2a, _g2b

# ★W4修复：高危问题类型永不自动修复（含 LLM 回退）。
# 与 nucleus.self_inspector._issue_severity 中的 high 级类型保持一致
# （unsafe_eval/subprocess_shell/sql_injection）。进化闭环升级（阶段A）后，
# 未命中 6 类本地规则的类型会回退 LLM 生成补丁，导致 unsafe_eval 等高危类型
# 也进入自动修复链路；此处显式拦截，维护「高危不越界」的安全边界。
_HIGH_RISK_NON_FIXABLE = frozenset({
    "unsafe_eval", "subprocess_shell", "sql_injection",
})

# ★PHASE12-P1-2（2026-09-06）：问题类型名别名映射（跨模块笔误补全）。
#   背景（实测）：self_inspector 产出的类型名与下游 SafeEvolutionExecutor
#   期望的类型名存在两处不一致，导致这两类问题「永远匹配不上」：
#       self_inspector 产出      下游期望            后果
#       mutable_default      →   mutable_default_arg   本地规则不修 → 走LLM
#       print_debug          →   print_instead_of_log  本地规则不修 → 走LLM
#   后果链：本地 6/12 类规则表命中不到 → 全部降级走 LLM 通道 →
#           LLM 调用量虚高、本地修复率统计偏低、修复周期被拉长。
#   修复策略（★最小侵入 / ★叠加而非替换）：
#       不改动上游 self_inspector 的类型名（避免破坏既有日志/统计口径），
#       而是在下游入口做一次性归一化，把别名收敛到标准名。
#   双向保险：若将来上游改名，反向别名仍能把新名映射到标准名，
#       两套名字都不会落到「未知类型」分支。
_ISSUE_TYPE_ALIASES: dict[str, str] = {
    "mutable_default": "mutable_default_arg",
    "print_debug": "print_instead_of_log",
    # 反向别名（上游若先行改名，此处保证旧名仍可识别）
    "mutable_default_arg": "mutable_default_arg",
    "print_instead_of_log": "print_instead_of_log",
}


def _normalize_issue_type(raw_type: Any) -> str:
    """★PHASE12-P1-2：把任意来源的问题类型名归一化到下游标准名。

    归一化规则（按顺序尝试，任一命中即返回）：
        1. 空值 → 返回 ""（由调用方按未知类型处理，不抛异常）
        2. 去空白 + 大小写归一（HTTP 类问题名存在大小写混用）
        3. 查别名表命中 → 返回标准名
        4. 未命中 → 原样返回（新增类型自动放行，不需要改这里）

    设计约束：
        - 纯函数、无 IO、无异常抛出，可在任何热路径安全调用。
        - 不做「智能猜测」，只做显式映射，避免误伤未知类型。
    """
    if raw_type is None:
        return ""
    try:
        _name = str(raw_type).strip()
    except Exception as e:
        silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_normalize_issue_type L188")
        return ""
    if not _name:
        return ""
    # 大小写归一：先按原名查，再按小写查（保留原名优先，避免误改合法驼峰名）
    if _name in _ISSUE_TYPE_ALIASES:
        return _ISSUE_TYPE_ALIASES[_name]
    _lower = _name.lower()
    if _lower in _ISSUE_TYPE_ALIASES:
        return _ISSUE_TYPE_ALIASES[_lower]
    return _name


def _issue_filter_reason(file_path: str, project_root: str = "") -> str:
    # ★主线第60批 T1（基于第59批 T2 扩展）：返回过滤原因，供入口日志区分。
    #   返回 ""（保留）/ "stdlib"（标准库或第三方包）/ "backup"（备份目录）/
    #   "external"（项目外绝对路径）。不过度过滤：空路径与项目内文件返回 ""。
    if not file_path:
        return ""
    _norm = file_path.replace("\\", "/")
    try:
        _patterns = list(getattr(config, "EVOLUTION_ISSUE_PATH_EXCLUDE_PATTERNS", []) or [])
    except Exception:
        _patterns = []
    for _pat in _patterns:
        _p = (_pat or "").replace("\\", "/")
        if _p and _p in _norm:
            return "stdlib"
    # ★主线第60批 T1：备份目录过滤（.bak_batchN / .bak_tmp / .bak_mainlineN 等）。
    #   按路径「段前缀 .bak」判定：foo.py.bak 这类文件名不含独立 .bak 段，不误杠；
    #   而 .bak_batch59/organs/... 的段 ".bak_batch59" 以 ".bak" 开头 → 命中。
    try:
        _bak_prefixes = list(
            getattr(config, "EVOLUTION_ISSUE_BACKUP_DIR_PREFIXES", [".bak"]) or []) or [".bak"]
    except Exception:
        _bak_prefixes = [".bak"]
    for _seg in _norm.split("/"):
        if any(_seg.startswith(_bp) for _bp in _bak_prefixes):
            return "backup"
    # 绝对路径判定（Windows 盘符 `X:` 或类 Unix 根 `/`）
    _is_abs = _norm.startswith("/") or (len(_norm) > 1 and _norm[1:2] == ":")
    if _is_abs:
        _root = (project_root or "").replace("\\", "/")
        if _root and not _norm.startswith(_root):
            return "external"
    return ""

def _infer_organ_from_path(file_path: str) -> str:
    # ★主线第60批 T5：organ/method 均为空时，从问题文件路径推断 organ。
    #   仅按已知目录前缀推断；推断不到返回 ""（此类问题应被跳过，不消耗名额）。
    if not file_path:
        return ""
    _norm = file_path.replace("\\", "/")
    if "organs/body/" in _norm:
        return "body"
    if "organs/brain/" in _norm:
        return "brain"
    if "nucleus/" in _norm:
        return "nucleus"
    return ""

def _issue_file_out_of_scope(file_path: str, project_root: str = "") -> bool:
    # ★主线第59批 T2 / 第60批 T1：单点过滤——委托 _issue_filter_reason，
    #   保持 bool 语义（既有门控测试 test_evolution_issue_path_filter_m59 不受影响）。
    return bool(_issue_filter_reason(file_path, project_root))


class SafeEvolutionExecutor:
    """
    安全进化执行器。
    
    将推演方案转化为具体的修改建议，输出到洞察黑板和日志。
    绝不自动修改任何文件。
    """
    
    def __init__(self):
        # ★主线第15批 T3/P1-93：子进程崩溃统计（按 mode 记次数与原因分布）
        self._crash_stats: dict[str, Any] = {}
        self._patch_log: list[dict[str, Any]] = []
        self._max_log = 20
        # ★9-问题3修复（2026-09-06）：修复吞吐上限，由硬编码改为可配置。
        #   原值硬编码在方法体内（_plan_multi_step_repair 的 _max_steps=5、
        #   repair_with_distillation 的 min(10, ...)），生产实测：
        #       20 个 high/medium 问题 → 按 (file, method) 分成 11 组
        #       → _max_steps=5 直接截断到 5 组 → 展开仅 10 个问题
        #       → _process_count=min(10,10)=10 → **10 个问题被静默丢弃**
        #   日志表现为「发现 20 个 / 处理 10 个」，且与上游 discover_all_issues
        #   的 max_issues=20 不匹配：上游放进来 20 个，下游只肯修 10 个，
        #   剩下 10 个每轮重新排队却永远轮不上（下一轮同样的 20 个又排在最前）。
        #   现与上游对齐（main.py:1746 传 max_issues=20），并留一格余量。
        #
        # ★PHASE12-P1-1（2026-09-06）：由硬编码实例属性改为读取 config.EVOLUTION_CONFIG，
        #   运维可直接在 config.py 调整，无需改源码（遵循「配置化优先」原则）。
        #   读取失败时回落保守默认值，绝不让配置问题阻断进化闭环。
        # ★PHASE17-C1（2026-09-07）：单轮步数上限按硬件 tier 自适应。
        #   固定值 12 在高端机上让自主进化吞吐被人为卡死（534 个问题需 45 轮），
        #   在低配机上又可能挤占主循环。改为 tier 查表，standard 档仍为 12
        #   （行为零变化），high/extreme 档自动放开。
        self._max_repair_steps = self._resolve_max_steps_by_tier()
        self._max_repair_issues = self._read_evolution_config_int(
            "max_issues_per_round", default=20, minimum=1, maximum=500)
        # 被截断丢弃的问题是否打延期标记（供上游下一轮识别，避免饥饿队列）
        self._mark_deferred_issues = self._read_evolution_config_bool(
            "mark_deferred_issues", default=True)

        # ★PHASE13-P1-3（2026-09-07）：僵尸问题冷却隔离表。
        #   动机详见 config.EVOLUTION_CONFIG["no_fix_cooldown_enabled"] 注释。
        #   一句话：上一轮已判定「不可自动修复」的问题，本轮起在冷却期内
        #   不再进入多选/normalize之后的竞争，把 12 步的名额让给真正可修的问题。
        #   结构是 指纹 -> 解冻时间戳(time.monotonic)，带过期而非永久剔除，
        #   因此代码块一旦被人工改动或被 LLM 修复解冻，问题仍能重新被召回。
        self._no_fix_cooldown_enabled = self._read_evolution_config_bool(
            "no_fix_cooldown_enabled", default=True)
        self._no_fix_cooldown: dict[str, float] = {}
        self._no_fix_cooldown_secs: dict[str, float] = {}
        # ★第114批 T-114a（治病·断6修复）：冷却表落盘，重启不丢冷却记录。
        #   冷却用 time.monotonic() 绝对截止，跨重启不可比；故落盘存「剩余秒数」
        #   而非绝对时间，加载时重建为 新时钟 + 剩余。详见 _m114a_* 方法。
        self._no_fix_cooldown_rounds: dict[str, int] = {}
        self._m114a_cooldown_file: str | None = None  # 惰性解析，避免 _project_root 顺序依赖
        try:
            import config as _cd_cfg
            _cd_raw = getattr(_cd_cfg, "EVOLUTION_CONFIG", {})
            if isinstance(_cd_raw, dict):
                _cd_secs = _cd_raw.get("no_fix_cooldown_seconds", {})
                if isinstance(_cd_secs, dict):
                    self._no_fix_cooldown_secs = {
                        str(_k): float(_v) for _k, _v in _cd_secs.items()
                        if isinstance(_v, (int, float)) and not isinstance(_v, bool)
                    }
        except Exception:
            self._no_fix_cooldown_secs = {}
        if not self._no_fix_cooldown_secs:
            # ★第95批 T-95a：回填表不再含死配置「已有待审批」
            #   （与 config.py EVOLUTION_CONFIG 同步清理）。
            self._no_fix_cooldown_secs = {
                "高危·安全拦截": 86400.0,
                "本地无规则·转LLM": 21600.0, "_default": 3600.0,
                "验证失败·3轮": 86400.0,  # ★T-119d 同步 114a 活键（config 同名）
                "验证失败": 3600.0,  # ★T-119d 同步 114a 活键
            }
        # ★v16.0新增：初始化PatchManager
        # ★主线第58批 T1（P1）：_project_root 提升为实例变量，
        #   供 _read_snippet_from_file / _m41_* 等方法经 self._project_root 引用，修复 AttributeError。
        self._project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # ★第140批 T-140c①：PatchManager 改为函数内延迟导入，
        #   切断「PatchManager ↔ SafeEvolutionExecutor」模块级循环边
        #   （PatchManager 模块级 import SafeEvolutionExecutor，若此处也模块级
        #    import PatchManager 则形成模块级环）。延迟到实例化时导入，
        #   此时两模块均已完成定义，环消失。
        from nucleus.reasoning.PatchManager import PatchManager
        self._patch_manager = PatchManager(self._project_root)

        # ★第157批 N-2：生产启动即加载冷却落盘（闭环「冷却落盘→重启加载」）
        #   修复 cooldown.json 只写不读 / 棘轮每轮归零 / [指纹咨询硬闸] 全窗 0 次。
        #   活体验收（cooldown.json 轮次>0、硬闸可触发）待小林协调核验（D5 停框架）。
        try:
            self._m114a_load_cooldown()
        except Exception as _n2e:
            _module_logger.debug(f"[冷却落盘] 启动加载异常(已忽略): {type(_n2e).__name__}: {_n2e}")

    # ===== ★PHASE12-P1-1：进化参数外置读取（配置化优先）=====
    @staticmethod
    def _read_evolution_config_int(key: str, default: int,
                                   minimum: int, maximum: int) -> int:
        """从 config.EVOLUTION_CONFIG 读取整数型进化参数，失败回落 default。

        设计约束（★最小侵入 + 绝不阻断闭环）：
            - config 模块延迟导入，规避与 nucleus 包的循环导入风险。
            - 任何异常（模块缺失/键缺失/类型错误/越界）都回落 default，
              并仅在 DEBUG 级记日志，不打扰正常运行。
            - 上下界钳制：防止运维误填 0 或超大值把进化闭环卡死/撑爆。
        """
        try:
            import config as _cfg
            _raw = getattr(_cfg, "EVOLUTION_CONFIG", {})
            _val = _raw.get(key, default) if isinstance(_raw, dict) else default
            if isinstance(_val, bool) or not isinstance(_val, (int, float)):
                return default
            _val = int(_val)
            if _val < minimum:
                return minimum
            if _val > maximum:
                return maximum
            return _val
        except Exception as _e:
            _module_logger.debug(
                f"[进化配置] 读取 EVOLUTION_CONFIG['{key}'] 失败，回落默认值 {default}: {_e}")
            return default

    @staticmethod
    def _read_evolution_config_bool(key: str, default: bool) -> bool:
        """从 config.EVOLUTION_CONFIG 读取布尔型进化参数，失败回落 default。"""
        try:
            import config as _cfg
            _raw = getattr(_cfg, "EVOLUTION_CONFIG", {})
            _val = _raw.get(key, default) if isinstance(_raw, dict) else default
            if isinstance(_val, bool):
                return _val
            if isinstance(_val, str):
                return _val.strip().lower() in ("1", "true", "yes", "on", "是")
            if isinstance(_val, (int, float)):
                return bool(_val)
            return default
        except Exception as _e:
            _module_logger.debug(
                f"[进化配置] 读取 EVOLUTION_CONFIG['{key}'] 失败，回落默认值 {default}: {_e}")
            return default

    # ★PHASE17-C1：tier 探测结果缓存（类级）。
    #   detect_hardware_tier() 内部有 GPU 探测，不宜在每次实例化时重复执行。
    _TIER_CACHE: dict | None = None

    # ★PHASE17-C1：max_steps_per_round 的「出厂默认值」。
    #   用于区分「运维显式调过」与「一直是默认值」——只有后者才允许 tier 自适应覆盖，
    #   以免配置被悄悄改写、让运维产生「改了没生效」的困惑。
    FACTORY_DEFAULT_MAX_STEPS: int = 12

    @classmethod
    def _detect_tier_cached(cls) -> str:
        """探测并缓存硬件 tier，失败回落 'standard'（保守中性值）。"""
        if cls._TIER_CACHE is not None:
            return str(cls._TIER_CACHE.get("tier", "standard"))
        try:
            from nucleus.hardware_probe import detect_hardware_tier
            _hw = detect_hardware_tier() or {}
            cls._TIER_CACHE = _hw if isinstance(_hw, dict) else {}
        except Exception as _e:
            _module_logger.debug(f"[进化配置] 硬件 tier 探测失败，按 standard 处理: {_e}")
            cls._TIER_CACHE = {"tier": "standard"}
        return str(cls._TIER_CACHE.get("tier", "standard"))

    @classmethod
    def _resolve_max_steps_by_tier(cls) -> int:
        """★PHASE17-C1：按硬件 tier 解析单轮最大步数。

        优先级：
            1. 开关 tier_adaptive_max_steps=False → 直接用 max_steps_per_round
            2. ★配置值非法（"abc" / True / 缺失）→ 严格回落出厂默认 12，
               **不再走 tier 自适应**。理由：非法值回落出的 12 是「兜底值」而非
               「运维意图」，若被 tier 放大成 24/32，等于把配置错误静默放大。
            3. ★配置是合法整数且 ≠ 出厂默认 12 → 绝对尊重人工设置。
               这条是为了不破坏「配置化优先」契约：运维一旦动手调过这个值，
               就说明他有明确意图，tier 自适应不得再覆盖它（否则配置看起来「失效」）。
            4. 合法整数且 == 出厂默认、tier 命中表 → 用 tier 值
            5. 其他任何异常/未命中 → 回落 max_steps_per_round（默认 12）

        ★行为兼容：standard 档 tier 值就是 12，与修复前固定值完全相同，
          因此绝大多数现有部署的行为不变；只有 high/extreme 档才会上调。
        """
        _fixed = cls._read_evolution_config_int(
            "max_steps_per_round", default=cls.FACTORY_DEFAULT_MAX_STEPS,
            minimum=1, maximum=200)
        try:
            if not cls._read_evolution_config_bool("tier_adaptive_max_steps", True):
                return _fixed
            import config as _cfg
            _raw = getattr(_cfg, "EVOLUTION_CONFIG", {})
            if not isinstance(_raw, dict):
                return _fixed
            # 判定「配置值是否被显式设置成一个合法整数」——
            # 不能只看 _fixed，因为 _read_evolution_config_int 对非法值也返回 default，
            # 无法与「显式设为 12」区分。此处直接读原始值做类型判断。
            _raw_val = _raw.get("max_steps_per_round", None)
            _is_valid_int = (
                isinstance(_raw_val, (int, float))
                and not isinstance(_raw_val, bool)
            )
            if not _is_valid_int:
                # 非法/缺失 → 严格回落，不放大
                return _fixed
            if float(_raw_val) != cls.FACTORY_DEFAULT_MAX_STEPS:
                # 人工显式调过 → 不再自适应
                return _fixed
            _table = _raw.get("max_steps_per_round_by_tier", {})
            if not isinstance(_table, dict) or not _table:
                return _fixed
            _tier = cls._detect_tier_cached()
            _val = _table.get(_tier)
            if isinstance(_val, bool) or not isinstance(_val, (int, float)):
                return _fixed
            _val = int(_val)
            if _val < 1 or _val > 200:       # 与固定值同用一套钳制边界
                _module_logger.warning(
                    f"[进化配置] tier={_tier} 的步数 {_val} 越界，回落固定值 {_fixed}")
                return _fixed
            if _val != _fixed:
                _module_logger.info(
                    f"[进化配置] 单轮最大步数按硬件自适应: tier={_tier} → {_val} "
                    f"(固定值 {_fixed} 已被覆盖)")
            return _val
        except Exception as _e:
            _module_logger.debug(
                f"[进化配置] tier 自适应步数解析失败，回落固定值 {_fixed}: {_e}")
            return _fixed

    @staticmethod
    def _is_core_file(file_path: str) -> bool:
        """判断是否核心文件（基础架构/主入口/脉冲引擎/快照），改动需更严格验证。"""
        _norm = (file_path or "").replace("\\", "/")
        # ★修复: 原 marker 用 "/nucleus/pulse/" 依赖路径带前导斜杠，
        #   实际补丁多为相对路径（如 "nucleus/pulse/..."），导致核心目录从未被识别。
        #   改为同时匹配「路径前缀」与「含前导斜杠」两种形式，确保判定正确。
        _core_markers = (
            "main.py",
            "config.py",  # type: ignore[possibly-unbound]
            "/base/",
            "base/",
            "nucleus/pulse/",
            "nucleus/field/",
            "nucleus/mnemosyne/",
            "nucleus/data/",
            "nucleus/reasoning/",
            # ★T-135d：大脑器官目录（含 PulseInnerWorld 等巨型器官）纳入核心保护
            "organs/brain/",
            # ★主线第80批 T7-3：补全核心文件清单
            "nucleus/security/",
            "nucleus/evolution/",
            # 安全/宪法相关器官（精神宪法/人格内核/伦理）
            "PulseSpiritConstitution.py",
            "PulsePersonalityKernel.py",
            "PulseEthics.py",
        )
        return any(m in _norm for m in _core_markers)

    def _m94_pending_blocks_regeneration(self, file_path: str,
                                         method_name: str) -> bool:
        """★第94批 T-94a.3：同位置「已有待审批」是否**阻塞再生**。

        * 老化开关 ``ENABLE_PENDING_QUEUE_AGING`` **关闭（默认）** → 逐字等价于既有
          ``PatchManager.has_pending_patch_for``（file+method 双匹配即阻塞），
          **零行为变化**；
        * 开关**开启** → 改走 ``PatchManager.find_blocking_pending_patch``：仅当该
          待审批补丁仍在老化窗口内（未超 ``PENDING_AGING_MAX_AGE_HOURS``）才算阻塞。
          超期未裁决的补丁不再永久冻结同位置的再生 —— 这正是第93批实测的
          「已有待审批」死循环（pending 只增不减、同位置问题每轮重复发现，
          单项占未修复原因 28.6%）。即任务书 §T-94a.3 的「计时器改为按补丁」。

        ★只解除**阻塞**，不代替**放行**（放行判据见 ``PatchManager._m94_aging_eligible``）。
        ★判据异常 → 回落既有 ``has_pending_patch_for`` 口径；再异常则返回 False，
          与既有实现「读取失败不阻断生成」的保守口径保持一致。
        """
        if not file_path or not method_name:
            return False
        try:
            if not self._patch_manager._m94_pending_aging_on():
                return bool(self._patch_manager.has_pending_patch_for(
                    file_path, method_name))
            return self._patch_manager.find_blocking_pending_patch(
                file_path, method_name) is not None
        except Exception as _e:
            _module_logger.debug(
                "[M94老化] 待审批阻塞判定异常，回落既有口径: %s: %s",
                type(_e).__name__, _e)
            try:
                return bool(self._patch_manager.has_pending_patch_for(
                    file_path, method_name))
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_m94_pending_blocks_regeneration L539")
                return False

    @staticmethod
    def _core_auto_apply_allowed() -> bool:
        """★主线第79批 T1(P0 安全审计): 灰度开关——是否允许核心文件自动批准。

        默认 False：安全红线，核心文件(main/config/base/nucleus/*)永远等待人工审批
        (status=verified)，绝不自动 approved。
        置 EVOLUTION_CONFIG.allow_core_auto_apply=True 可一键回退到旧行为
        (核心文件也自动 approved)，仅用于紧急回退。
        """
        try:
            import config  # type: ignore[possibly-unbound]
            return bool(getattr(config, 'EVOLUTION_CONFIG', {}).get("allow_core_auto_apply", False))
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_core_auto_apply_allowed L552")
            return False

    def _resolve_core_auto_apply_status(self, is_core: bool) -> str:
        """★主线第79批 T1(P0 安全审计): 决定自动批准分支下补丁的最终状态。

        核心文件(is_core=True)默认 'verified'——强制人工审批(安全红线)；
        仅当灰度开关 _core_auto_apply_allowed() 为真才回退为 'approved'。
        非核心文件(is_core=False)恒为 'approved'。

        该封装同时服务于 execute() 与 _verify_and_save_patch() 两条路径，
        保证两条状态决策逻辑不再分叉，且可被单测直接覆盖。
        """
        if is_core and not self._core_auto_apply_allowed():
            return "verified"
        return "approved"

    def _find_related_logs(self, file_path: str, method_name: str, limit: int = 3) -> str:
        """在运行日志中查找与指定文件/方法相关的错误日志（★FIX: 日志关联，精准定位问题）"""
        try:
            import config  # type: ignore[possibly-unbound]
            _log_dir = getattr(config, "LOG_DIR", "logs")  # type: ignore[possibly-unbound]
            _log_file = os.path.join(_log_dir, getattr(config, "LOG_FILE", "pulse.log"))  # type: ignore[possibly-unbound]
            if not os.path.exists(_log_file):
                return ""
            _base = os.path.basename(file_path)
            with open(_log_file, encoding='utf-8', errors='ignore') as _f:
                _lines = _f.readlines()
            _related = []
            _i = len(_lines) - 1
            while _i >= 0 and len(_related) < limit:
                _line = _lines[_i]
                _hit = (_base in _line) or (method_name and method_name in _line)
                _is_error = ("ERROR" in _line or "CRITICAL" in _line
                             or "Traceback" in _line or 'File "' in _line)
                if _hit and _is_error:
                    _start = max(0, _i - 2)
                    _end = min(len(_lines), _i + 3)
                    _ctx = "".join(_lines[_start:_end]).strip()
                    if _ctx not in _related:
                        _related.append(_ctx)
                _i -= 1
            return "\n---\n".join(reversed(_related[-limit:]))
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_find_related_logs L595")
            return ""

    @staticmethod
    def _infer_root_cause(issue: dict[str, Any]) -> str:
        """
        ★根因分析深度(P1红项)：从问题类型/描述推导「根因陈述」——
        为什么会有这个问题、影响面、修复方向。供 LLM 修复 prompt 注入（先验根因）
        与补丁 root_cause_analysis 字段（人工审查可见"为什么"）。零冲突：纯只读辅助。
        """
        _hints = {
            "silent_exception": "except 分支静默吞异常：异常被捕获后无任何日志/处理，故障被隐藏，排障无从下手；"
                                "修复应记录异常（self._log）以暴露问题。",
            "bare_except": "裸 except 捕获所有异常（含 KeyboardInterrupt/SystemExit），既掩盖严重错误又阻止正常退出；"
                           "修复应限定为 Exception 并记录。",
            "unbounded_deque": "无界 deque 持续 append 导致内存无限增长，长时间运行产生内存泄漏；修复应限制容量(maxlen)。",
            "thread_no_daemon": "非守护线程会阻塞进程退出（Ctrl+C 无法终止）或造成后台线程泄漏；修复应设 daemon=True。",
            "no_timeout_http": "网络请求无超时，下游服务故障时调用线程将永久挂死，阻塞整体流水线；修复应加 timeout。",
            "status_request_duplicate": "状态请求存在重复处理路径，可能重复计数或状态不一致；修复应统一到单一实现。",
            "mutable_default": "可变默认参数（def f(x=[])）跨调用共享同一对象，状态在调用间泄漏；修复应改为 None+运行时创建。",
            "hardcoded_abs_path": "字符串字面量硬编码绝对路径（形如 D: 盘根/C: 盘根），环境迁移即失效；修复应改用配置/相对路径。",
            "print_debug": "方法体内残留 print/pprint 调试输出，污染控制台且无日志级别控制；修复应改为日志或移除。",
        }
        _t = issue.get("type", "")
        for _k, _hint in _hints.items():
            if _k in _t:
                return _hint
        # 通用：结合描述给出方向（未知类型不编造根因，仅提示结合日志）
        _desc = (issue.get("description") or "").strip()
        if _desc:
            return f"根因需结合运行日志与上下文确认；检测描述: {_desc[:120]}"
        return "根因需结合运行日志与代码上下文进一步确认。"

    def _deep_root_cause_analysis(self, issue: dict[str, Any],
                                   self_inspector=None) -> dict[str, Any]:
        """★P1-2(2026-09-03)：深度根因分析——代码上下文+日志关联+历史经验+风险评估。"""
        _result = {
            "issue_type": issue.get("type", ""),
            "static_analysis": self._infer_root_cause(issue),
            "code_context": None,
            "log_analysis": None,
            "historical_experience": None,
            "risk_assessment": None,
            "summary": "",
        }

        # 1. 代码上下文分析（★第二阶段增强：调用链+影响范围+类上下文）
        try:
            if self_inspector and issue.get("organ") and issue.get("method"):
                _body = self_inspector.get_method_body(issue["organ"], issue["method"])
                if _body:
                    _code = _body.get("body", "")
                    _lines = _code.count("\n") + 1 if _code else 0
                    _patterns = []
                    if "except:" in _code or "except Exception" in _code:
                        _patterns.append("异常处理")
                    if "threading.Thread" in _code or "Thread(" in _code:
                        _patterns.append("线程创建")
                    if "requests." in _code or "urllib" in _code:
                        _patterns.append("网络调用")
                    if "open(" in _code or ".write(" in _code:
                        _patterns.append("文件操作")
                    if "while True" in _code or "while 1" in _code:
                        _patterns.append("无限循环")
                    _triple_double = '"""'
                    _triple_single = "'''"
                    _has_doc = (_triple_double in _code[:200]) or (_triple_single in _code[:200])

                    # ★第二阶段新增：调用链深度分析
                    _call_chain = _body.get("call_chain", {})
                    _chain_depth = 0
                    _chain_methods = set()
                    def _count_chain_depth(chain, d=0):
                        nonlocal _chain_depth, _chain_methods
                        if not chain:
                            return
                        _chain_depth = max(_chain_depth, d)
                        _chain_methods.add(chain.get("method", ""))
                        for sub in chain.get("calls", []):
                            _count_chain_depth(sub, d + 1)
                    _count_chain_depth(_call_chain)

                    # ★第二阶段新增：影响范围分析（被调用者数量）
                    _callers = _body.get("callers", [])
                    _caller_count = _body.get("caller_count", len(_callers))
                    _impact_scope = "局部" if _caller_count == 0 else (
                        "中等" if _caller_count <= 3 else "广泛"
                    )

                    # ★第二阶段新增：类上下文
                    _class_ctx = _body.get("class_context", {})
                    _base_class = _class_ctx.get("base_class", "")
                    _class_method_count = _class_ctx.get("method_count", 0)

                    _result["code_context"] = {
                        "lines": _lines,
                        "patterns": _patterns,
                        "has_docstring": _has_doc,
                        "preview": _code[:300] if _code else "",
                        # ★第二阶段新增字段
                        "call_chain_depth": _chain_depth,
                        "call_chain_methods": len(_chain_methods),
                        "call_chain_methods_list": sorted(_chain_methods)[:10],
                        "callers": _callers[:10],
                        "caller_count": _caller_count,
                        "impact_scope": _impact_scope,
                        "base_class": _base_class,
                        "class_method_count": _class_method_count,
                    }
        except Exception as _ctx_err:
            _result["code_context"] = {"error": str(_ctx_err)}

        # 2. 日志关联分析
        try:
            _file = issue.get("file", "")
            _method = issue.get("method", "")
            _related = self._find_related_logs(_file, _method, limit=5)
            _error_count = 0
            if _related:
                _error_count = _related.count("ERROR") + _related.count("CRITICAL") + _related.count("Traceback")
            _result["log_analysis"] = {
                "related_logs": _related[:500] if _related else "",
                "error_count": _error_count,
                "has_runtime_impact": _error_count > 0,
            }
        except Exception as _log_err:
            _result["log_analysis"] = {"error": str(_log_err)}

        # 3. 历史修复经验
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            _hub = get_verification_learning_hub()
            if hasattr(_hub, "query"):
                _hist = _hub.query(organ="code_learner", task_type="code_repair_distill", limit=3)
                if _hist:
                    _lessons = [h.get("lesson", "") for h in _hist if h.get("lesson")]
                    _result["historical_experience"] = {
                        "similar_issues": len(_hist),
                        "lessons": _lessons[:2],
                    }
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 4. 风险评估（★第二阶段增强：加入影响范围和调用链深度）
        try:
            _risk_score = 0
            _risk_factors = []
            _ctx = _result.get("code_context") or {}
            if _ctx.get("lines", 0) > 100:
                _risk_score += 2
                _risk_factors.append("方法过长")
            if (_result.get("log_analysis") or {}).get("has_runtime_impact"):
                _risk_score += 3
                _risk_factors.append("有运行时错误")
            _patterns = _ctx.get("patterns", [])
            _risk_patterns = [p for p in _patterns if p in ("线程创建", "网络调用", "文件操作")]
            if _risk_patterns:
                _risk_score += 2
                _risk_factors.append("涉及" + "/".join(_risk_patterns))
            if issue.get("type", "") in ("thread_no_daemon", "no_timeout_http", "unbounded_deque"):
                _risk_score += 2
                _risk_factors.append("资源泄漏类")
            # ★第二阶段新增：影响范围风险
            _impact = _ctx.get("impact_scope", "")
            if _impact == "广泛":
                _risk_score += 2
                _risk_factors.append("影响范围广泛(被" + str(_ctx.get("caller_count", 0)) + "个方法调用)")
            elif _impact == "中等":
                _risk_score += 1
                _risk_factors.append("影响范围中等")
            # ★第二阶段新增：调用链深度风险
            if _ctx.get("call_chain_depth", 0) >= 3:
                _risk_score += 1
                _risk_factors.append("调用链较深(深度" + str(_ctx.get("call_chain_depth", 0)) + ")")
            _result["risk_assessment"] = {
                "risk_score": min(10, _risk_score),
                "risk_level": "高" if _risk_score >= 6 else ("中" if _risk_score >= 3 else "低"),
                "risk_factors": _risk_factors,
                "repair_priority": "P0" if _risk_score >= 7 else ("P1" if _risk_score >= 4 else "P2"),
            }
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 5. 综合摘要（★第二阶段增强：加入影响范围和调用链）
        try:
            _parts = [_result["static_analysis"]]
            _ctx = _result.get("code_context") or {}
            if _ctx.get("patterns"):
                _parts.append("代码模式: " + ", ".join(_ctx["patterns"]))
            if (_result.get("log_analysis") or {}).get("has_runtime_impact"):
                _parts.append("运行时已观测到相关错误")
            # ★第二阶段新增：影响范围
            if _ctx.get("impact_scope") and _ctx["impact_scope"] != "局部":
                _parts.append("影响范围: " + _ctx["impact_scope"] +
                              "(被" + str(_ctx.get("caller_count", 0)) + "个方法调用)")
            # ★第二阶段新增：调用链
            if _ctx.get("call_chain_depth", 0) >= 2:
                _parts.append("调用链深度: " + str(_ctx["call_chain_depth"]) +
                              "(涉及" + str(_ctx.get("call_chain_methods", 0)) + "个方法)")
            _ra = _result.get("risk_assessment") or {}
            if _ra:
                _parts.append("修复风险: " + _ra.get("risk_level", "未知"))
            _result["summary"] = "；".join(_parts)
        except Exception:
            _result["summary"] = _result["static_analysis"]

        return _result

    def _classify_issue_complexity(self, issue: dict[str, Any]) -> str:
        """★学生-老师模式：问题复杂度分级，决定询问策略。
        L1简单：本地规则可修 → 不问老师（已在上层处理）
        L2中等：模式明确但本地无规则 → 一次询问
        L3复杂：需根因分析/架构设计 → 多轮追问
        """
        _type = issue.get("type", "")
        _desc = issue.get("description", "").lower()
        # L3复杂问题特征
        _complex_keywords = ["架构", "设计", "性能", "死锁", "竞态", "内存泄漏",
                             "重构", "优化", "根因", "崩溃", "数据丢失"]
        if any(k in _desc for k in _complex_keywords):
            return "L3"
        # L2中等：本地不可修但有明确类型
        if _type and _type not in {"silent_exception", "bare_except", "status_request_duplicate",
                                    "unbounded_deque", "thread_no_daemon", "no_timeout_http",
                                    "mutable_default_arg", "comparison_with_none",
                                    "boolean_comparison", "os_path_join", "print_instead_of_log",
                                    "fstring_preferred"}:
            return "L2"
        return "L1"

    def _read_snippet_from_file(self, file_path: str, method_name: str = "",
                                max_lines: int = 40) -> str:
        """★第22批 T4/P2-122：按文件路径（可选方法名）读取代码片段。

        用于 self_inspector 方法级索引定位不到、但日志已带出文件路径的场景。

        安全护栏：仅读**项目根目录内**、体积 < 2MB 的文本文件；命中方法定义时
        从其所在行起取 ``max_lines`` 行，否则取文件头 ``max_lines`` 行。

        Returns:
            片段文本（截断至 2000 字）；不可读/越界/超限时返回 ""。
        """
        if not file_path:
            return ""
        try:
            _p = file_path
            if not os.path.isabs(_p):
                _p = os.path.join(self._project_root, _p)
            _p = os.path.normpath(_p)
            _root = os.path.normpath(self._project_root)
            if not _p.startswith(_root):
                return ""
            if not os.path.isfile(_p):
                _module_logger.debug(
                    f"[代码片段] 文件不存在: {file_path}")
                return ""
            if os.path.getsize(_p) > 2 * 1024 * 1024:
                return ""
            with open(_p, encoding="utf-8", errors="ignore") as _f:
                _lines = _f.readlines()
        except Exception as _e:
            _module_logger.debug(
                f"[代码片段] 文件读取失败: {file_path} - {type(_e).__name__}: {_e}")
            return ""
        if method_name:
            for _i, _ln in enumerate(_lines):
                if f"def {method_name}" in _ln:
                    return "".join(_lines[_i:_i + max_lines]).strip()[:2000]
        return "".join(_lines[:max_lines]).strip()[:2000]

    def _extract_comparison_material(self, issue: dict[str, Any], related_logs: str,
                                     file_path: str = "", method_name: str = "") -> str:
        """★第22批 T4/P2-122：为「修复蒸馏」提取可用的 LLM 对比素材。

        优先级：① 文件（+方法）片段；② issue 的描述/消息等文本字段；③ 相关日志。
        返回 "" 表示确实无素材 —— 由调用方跳过并记录原因，不再是「空片段静默跳过」。

        Args:
            issue: 问题字典（LogAnalyzer 构造，含 description/organ/method 等）。
            related_logs: ``_find_related_logs`` 的返回（可为空串）。
            file_path: 问题关联文件（可空）。
            method_name: 问题关联方法（可空）。

        Returns:
            对比素材文本（截断 2000 字）；无素材返回 ""。
        """
        _snip = self._read_snippet_from_file(file_path, method_name)
        if _snip:
            return _snip
        _cands: list[str] = []
        if isinstance(issue, dict):
            for _k in ("description", "message", "msg", "error", "detail",
                       "content", "traceback", "raw"):
                _v = issue.get(_k)
                if isinstance(_v, str) and _v.strip():
                    _cands.append(_v.strip())
        if isinstance(related_logs, str) and related_logs.strip():
            _cands.append(related_logs.strip())
        elif isinstance(related_logs, (list, tuple)):
            _cands.extend(str(_x).strip() for _x in related_logs if str(_x).strip())
        for _c in _cands:
            if _c:
                return _c[:2000]
        return ""

    def _build_differentiated_prompt(self, issue: dict[str, Any], code_snippet: str,
                                      related_logs: str, complexity: str,
                                      local_analysis: str, prior_answer: str = "",
                                      followup_round: int = 0) -> str:
        """★学生-老师模式：差异化询问模板，针对不同问题类型用不同问法。"""
        _type = issue.get("type", "")
        _desc = issue.get("description", "")
        _log_part = f"\n相关运行日志:\n```\n{related_logs[:1500]}\n```\n" if related_logs else ""
        _local_part = f"\n我的初步分析:\n{local_analysis}\n" if local_analysis else ""
        _prior_part = f"\n上一轮你的回答:\n{prior_answer[:1000]}\n" if prior_answer else ""

        # 追问模式：针对上一轮回答的不足点追问
        if followup_round > 0:
            return (
                f"关于上一个问题，你的回答我还有疑问：\n"
                f"{_prior_part}"
                f"请针对以下点进一步说明：\n"
                f"1. 修复的根因是否准确？是否有其他可能原因？\n"
                f"2. 修复方案是否有副作用？边界情况如何处理？\n"
                f"3. 请给出更完整的修复代码（包含必要的导入和上下文）。\n"
                f"问题类型: {_type}\n问题描述: {_desc}\n"
                f"相关代码:\n```python\n{code_snippet[:2000]}\n```\n"
                f"{_log_part}"
                f"请直接输出修复后的完整代码。"
            )

        # 根据问题类型选择询问模板
        if complexity == "L3":
            # L3复杂问题：先请老师分析根因，再给方案
            return (
                f"我遇到一个复杂的代码问题，需要你的深度指导。\n"
                f"问题类型: {_type}\n问题描述: {_desc}\n"
                f"{_local_part}"
                f"相关代码:\n```python\n{code_snippet[:2000]}\n```\n"
                f"{_log_part}"
                f"请按以下步骤指导我：\n"
                f"1. 分析根本原因（不要只看表面现象）\n"
                f"2. 评估可能的修复方案及其权衡\n"
                f"3. 给出推荐方案的完整修复代码\n"
                f"4. 说明修复后的验证方法"
            )
        elif "timeout" in _type or "超时" in _desc:
            # 超时类问题：询问超时原因和合理阈值
            return (
                f"我遇到一个超时相关的代码问题。\n"
                f"问题类型: {_type}\n问题描述: {_desc}\n"
                f"{_local_part}"
                f"相关代码:\n```python\n{code_snippet[:2000]}\n```\n"
                f"{_log_part}"
                f"请分析：1) 超时的根本原因 2) 合理的超时阈值应该是多少 3) 完整修复代码"
            )
        elif "exception" in _type or "异常" in _desc or "error" in _type.lower():
            # 异常处理类：询问异常类型和处理策略
            return (
                f"我遇到一个异常处理相关的代码问题。\n"
                f"问题类型: {_type}\n问题描述: {_desc}\n"
                f"{_local_part}"
                f"相关代码:\n```python\n{code_snippet[:2000]}\n```\n"
                f"{_log_part}"
                f"请分析：1) 应该捕获哪些具体异常类型 2) 异常处理逻辑是否完整 3) 完整修复代码"
            )
        else:
            # L2通用模式：带着自己的分析去问
            return (
                f"我遇到一个代码问题，自己做了初步分析但不确定，请帮我确认和完善。\n"
                f"问题类型: {_type}\n问题描述: {_desc}\n"
                f"{_local_part}"
                f"相关代码:\n```python\n{code_snippet[:2000]}\n```\n"
                f"{_log_part}"
                f"请：1) 确认我的分析是否正确 2) 给出更完善的修复代码 3) 说明需要注意的边界情况"
            )

    @trace_evolution_call(prompt_pos=3, version="evolution.repair.v1")
    def _call_llm_with_followup(self, api_url: str, api_key: str, prompt: str,
                                 max_followups: int = 1,
                                 scene: str | None = None,
                                 model: str = "") -> str | None:
        """★学生-老师模式：多轮追问机制。第一次回答不完整时自动追加追问。

        ★第95批 T-95d：新增可选 ``scene``（默认 ``None`` ⇒ ``SCENE_EVOLUTION``，
        既有行为逐字不变）。``PulseCodeLearner`` 经 ``repair_with_distillation``
        透传 ``SCENE_CODE_LEARN``，让「代码学习」场景不再恒 0 —— 同一调用
        **只记一次**，只是场景归类不同，**不重复计数**（不虚增 llm_total）。

        ★第96批 T-96a：新增可选 ``model``（默认 ``""`` ⇒ 沿用全局默认模型，
        既有行为逐字不变）。渠道池命中时由 ``_call_llm_for_repair`` 传入该
        渠道的 ``model``（火山方舟为**推理接入点 ID**），避免「换了渠道却
        仍在请求旧模型」——这正是免费额度实际用不上的表现之一。
        """
        record_llm_call(scene or SCENE_EVOLUTION)
        import json

        from nucleus.ssrf_guard import safe_http_json

        def _call(p: str) -> str | None:
            # ★第54批 T6.1（根因3）：要求 LLM 只输出纯 ASCII 代码，
            #   避免中文标点导致「补丁完整性检查失败: 语法错误」。
            #   灰度 ENABLE_LLM_PATCH_ASCII_PROMPT：关闭 → 沿用旧 prompt，零回归。
            _sys_prompt = "你是曈曈的代码自学习助手，负责生成精确的代码修复建议。"
            try:
                import config as _cfg_prompt
                if getattr(_cfg_prompt, "ENABLE_LLM_PATCH_ASCII_PROMPT", True):
                    _sys_prompt = (
                        "你是曈曈的代码自学习助手，负责生成精确的Python代码修复建议。"
                        "只输出Python代码，使用纯ASCII字符，不要使用中文标点。"
                        "确保语法正确，所有引号和括号必须匹配。不要输出解释文字，只输出代码。")

                    # ★第91批 T-91b：缩进契约（T0 实测：原 prompt **完全未提缩进**，
                    #   而 4 次 LLM 补丁尝试全部因 unindent 语法错误被拒）。
                    if _m91_indent_repair_on():
                        _sys_prompt += (
                            "保持与输入代码**完全相同的缩进层级与缩进宽度**："
                            "每个缩进层级使用 4 个空格，禁止使用制表符；"
                            "只返回修复后的完整方法（含 def 行及其原有缩进），"
                            "不要提升或降低代码所在的层级。")
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _payload = {
                # ★第96批 T-96a：渠道池命中时以**渠道自带的 model**（推理接入点 ID）
                #   为准；为空（回落 REMOTE_API_CONFIG 路径）才用全局默认模型。
                "model": model or get_llm_call_config().get("default_model", "deepseek-v4-flash"),
                "messages": [
                    {"role": "system", "content": _sys_prompt},
                    {"role": "user", "content": p},
                ],
                "temperature": 0.3,
                "max_tokens": _LLM_REPAIR_MAX_TOKENS,
            }
            _bytes = json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {'Content-Type': 'application/json; charset=utf-8', 'Authorization': 'Bearer ' + api_key}
            _timeout = max(
                int((get_llm_call_config().get("timeout_by_purpose") or {}).get("evolution", 30)),
                _LLM_REPAIR_MIN_TIMEOUT)
            with api_rate_limited(enabled=get_llm_call_config().get("enable_rate_limit", True)):
                _ok, _data = safe_http_json(api_url, method='POST', data=_bytes, headers=_headers, timeout=_timeout)
            if _ok and isinstance(_data, dict):
                # ★第94批 T-94b：暂存 usage 供 `trace_evolution_call` 装饰器留存。
                #   （本方法被装饰，装饰器在 finally 读 `_last_llm_usage`；
                #    多次追问时取**最后一次**调用的用量，属可接受的近似。）
                #   ★第95批 T-95e：属性统一命名（原 `_m44_last_usage`）。
                self._last_llm_usage = _data.get("usage")  # _m94_extract_usage_marker
                _choices = _data.get("choices", [])
                if _choices:
                    _msg = _choices[0].get("message") or {}
                    _content = _msg.get("content") or ""
                    if not _content:
                        # ★第86批 T-86a：推理模型把预算耗在 reasoning_content 上时
                        #   content 会是空串；旧代码静默返回 ""，外层再静默 return None，
                        #   导致「LLM 补丁通道零产出」长期无迹可循。此处必须留痕。
                        _usage = _data.get("usage") or {}
                        _module_logger.warning(
                            "[LLM修复] 模型返回 content 为空（finish_reason=%s, completion_tokens=%s, "
                            "reasoning_content=%d字, max_tokens=%d）"
                            "——疑似推理预算被耗尽，请上调 _LLM_REPAIR_MAX_TOKENS",
                            _choices[0].get("finish_reason"), _usage.get("completion_tokens"),
                            len(_msg.get("reasoning_content") or ""), _LLM_REPAIR_MAX_TOKENS)
                    return _content
            _module_logger.warning(
                "[LLM修复] 调用未返回可用结果（HTTP ok=%s, data_type=%s）",
                _ok, type(_data).__name__)
            return None

        # 第一轮
        answer = _call(prompt)
        if not answer:
            return None

        # 判断是否需要追问：回答过短或缺少代码块
        _need_followup = len(answer) < 100 or "```" not in answer
        if _need_followup and max_followups > 0:
            _followup_prompt = (
                f"关于上一个问题，你的回答不够完整。\n"
                f"上一轮你的回答:\n{answer}\n"
                f"请补充：1) 完整的修复代码（用```python包裹） 2) 修复的关键改动点说明"
            )
            _better = _call(_followup_prompt)
            if _better and len(_better) > len(answer):
                return _better
        return answer

    # ========== ★第96批 T-96a（P0）：进化通道接入渠道池 ==========
    #   背景（烛微第1期 N3 + 第96批 T0 实测）：进化链 LLM 调用直连
    #   `REMOTE_API_CONFIG`（api.deepseek.com，**收费**），既不走渠道池、
    #   不享受智谱/火山免费额度，也不接 ChannelHealthTracker 熔断。
    #   本段提供「按优先级取可用渠道」的能力，默认**关闭**（灰度），
    #   任何异常/缺渠道都回落到既有 REMOTE_API_CONFIG 路径。

    @staticmethod
    def _m96_channel_pool_on() -> bool:
        """第96批 T-96a 灰度开关：True → 进化通道走渠道池。"""
        try:
            import config as _c96
            return bool(getattr(_c96, "ENABLE_EVOLUTION_USE_CHANNEL_POOL", False))
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_m96_channel_pool_on L1095")
            return False

    @staticmethod
    def _m96_set_lung(lung) -> None:
        """★注入钩子：允许 main 传入**运行中的** PulseLung 实例。

        传入后与其**共享** ChannelHealthTracker 熔断状态，避免两处健康度
        各自记账。不传（默认）→ 走 `_m96_select_channel` 的内部等价实现，
        行为与 PulseLung 的渠道选择器同判据、同顺序。
        """
        SafeEvolutionExecutor._M96_LUNG = lung

    _M96_LUNG = None

    @staticmethod
    def _m96_channel_health():
        """取渠道健康度跟踪器；优先复用注入肺实例的那一个（同账本）。"""
        _lung = SafeEvolutionExecutor._M96_LUNG
        if _lung is not None:
            try:
                _h = _lung._get_channel_health()
                if _h is not None:
                    return _h
            except Exception as _e96h:
                # ★不留痕的 except: pass 会被 m7 门禁判为「静默吞异常」
                #   （烛微 N7 TIER1 同族）⇒ 一律记录 type + msg。
                _module_logger.debug(
                    "[进化渠道] 复用肺实例健康度失败，转本地构造: %s: %s",
                    type(_e96h).__name__, _e96h)
        try:
            with SafeEvolutionExecutor._M96_HEALTH_LOCK:
                if getattr(SafeEvolutionExecutor, "_M96_HEALTH", None) is None:
                    import config as _c96
                    from nucleus.llm.channel_health import ChannelHealthTracker
                    _hcfg = ((getattr(_c96, "REMOTE_API_CHANNELS", {}) or {})
                             .get("health", {}) or {})
                    SafeEvolutionExecutor._M96_HEALTH = ChannelHealthTracker(
                        int(_hcfg.get("window_size", 10)),
                        int(_hcfg.get("circuit_break_threshold", 3)),
                        float(_hcfg.get("circuit_break_seconds", 300.0)))
                return SafeEvolutionExecutor._M96_HEALTH
        except Exception as e:
            silent_exc(e, "nucleus/reasoning/SafeEvolutionExecutor.py:1130:_m96_channel_health", level="warning")
            return None

    _M96_HEALTH = None
    _M96_HEALTH_LOCK = __import__("threading").Lock()

    def _m96_select_channel(self):
        """★第96批 T-96a：从渠道池按优先级选一个可用渠道。

        返回渠道 dict（含 name/model/api_url/api_key）；无可用 → None。
        ★判据与 PulseLung 的生产选择器**同源**：
          ① `config.get_active_channels()`（已按 priority 升序过滤 enabled）；
          ② `ChannelQuotaMonitor.apply_to_channels()`（额度降级/暂停策略）；
          ③ `ChannelHealthTracker.is_available()`（跳过熔断中的渠道）。
        ★优先复用**注入的肺实例**的选择器（`_select_default_channel`），
          保证与主对话通道选择结果一致；无注入时用本实现（等价判据）。
        """
        _lung = SafeEvolutionExecutor._M96_LUNG
        if _lung is not None:
            try:
                _ch = _lung._select_default_channel()
                if isinstance(_ch, dict) and _ch.get("api_url") and _ch.get("model"):
                    return _ch
            except Exception as _e96a:
                _module_logger.debug(
                    "[进化渠道] 复用肺实例选择器失败，转本地实现: %s: %s",
                    type(_e96a).__name__, _e96a)
        try:
            import config as _c96
            _pool = _c96.get_active_channels()
        except Exception as _e96b:
            _module_logger.debug(
                "[进化渠道] 取渠道池失败: %s: %s", type(_e96b).__name__, _e96b)
            return None
        if not _pool:
            return None
        # 额度策略（与主链路同口径）
        try:
            from nucleus.llm.ChannelQuotaMonitor import (  # type: ignore
                get_channel_quota_monitor as _m96_qm,
            )
            _pool = _m96_qm().apply_to_channels(list(_pool))
        except Exception as _e96q:
            _module_logger.debug(
                "[进化渠道] 额度策略应用失败（用原始池）: %s",
                type(_e96q).__name__)
        _h = self._m96_channel_health()
        for _ch in _pool:
            if not isinstance(_ch, dict):
                continue
            _name = _ch.get("name") or "?"
            if not (_ch.get("api_url") and _ch.get("model")):
                continue
            if _h is not None:
                try:
                    if not _h.is_available(_name):
                        _module_logger.debug("[进化渠道] %s 熔断中，跳过", _name)
                        continue
                except Exception as _e96a2:
                    # ★同上：判定失败必须留痕，否则故障无声。
                    _module_logger.debug(
                        "[进化渠道] 熔断可用性判定失败，按可用处理: %s: %s",
                        type(_e96a2).__name__, _e96a2)
            return _ch
        return None

    def _m96_record_channel_result(self, channel_name: str, success: bool,
                                   latency: float = 0.0) -> None:
        """把进化链的调用结果计入渠道健康度（与对话链路**同账本**）。"""
        if not channel_name:
            return
        try:
            _h = self._m96_channel_health()
            if _h is not None:
                _h.record(channel_name, bool(success), float(latency))
        except Exception as _e96r:
            _module_logger.debug(
                "[进化渠道] 健康度回写失败: %s: %s", type(_e96r).__name__, _e96r)

    def _call_llm_for_repair(self, issue: dict[str, Any], code_snippet: str,
                             related_logs: str = "",
                             scene: str | None = None) -> str | None:
        """★自适应询问模式（v3）：使用AdaptiveQueryStrategyGenerator动态生成询问策略。
        不写死询问模板，根据问题特征、自我分析、历史经验创造性组织询问方式。
        每次询问后评估效果，成功策略沉淀为可复用模式，实现元学习。"""
        try:
            import config  # type: ignore[possibly-unbound]
            # ★第96批 T-96a：默认仍是 REMOTE_API_CONFIG；开关打开且渠道池
            #   可取到渠道时才切换（三层回落：无渠道路/异常/字段不全）。
            _m96_ch = None
            _m96_name = ""
            if self._m96_channel_pool_on():
                _m96_ch = self._m96_select_channel()
                if isinstance(_m96_ch, dict):
                    _m96_name = str(_m96_ch.get("name") or "")
                    _module_logger.info(
                        "[进化渠道] 已选渠道 %s（model=%s）",
                        _m96_name or "?", _m96_ch.get("model", ""))
                else:
                    _module_logger.info(
                        "[进化渠道] 渠道池不可用，回落 REMOTE_API_CONFIG")
            if _m96_ch is not None:
                _api_url = _m96_ch.get("api_url", "")
                _api_key = _m96_ch.get("api_key", "")
                _api_model = _m96_ch.get("model", "")
                if not _api_key:
                    _api_key = getattr(config, 'REMOTE_API_CONFIG', {}).get(
                        "api_key", "")
            else:
                _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})
                _api_url = _api_cfg.get("api_url", "")
                _api_key = _api_cfg.get("api_key", "")
                _api_model = ""
            if not _api_url or not _api_key or not code_snippet:
                return None

            # ★引入自适应询问策略生成器
            from nucleus.reasoning.AdaptiveQueryStrategyGenerator import (
                get_query_strategy_generator,
            )
            _generator = get_query_strategy_generator()

            # ★思考前置：自己先分析（深度根因分析：代码上下文+日志+风险评估）
            _deep = self._deep_root_cause_analysis(issue, self_inspector=getattr(self, '_self_inspector', None))
            _local_analysis = _deep.get("summary", self._infer_root_cause(issue))
            _prior_err = issue.get("prior_error") or ""
            if _prior_err:
                _local_analysis += f"\n上一版验证失败: {_prior_err}"

            # ★问题特征提取（不写死分类，基于特征动态推断）
            _features = _generator.extract_issue_features(
                issue, context=related_logs,
                prior_attempts=issue.get("prior_attempts", []))

            # ★策略生成（动态选择开场/信息顺序/提问结构/期望输出）
            _strategy = _generator.generate_strategy(
                _features, local_analysis=_local_analysis,
                code_snippet=code_snippet, related_logs=related_logs)

            # ★从模式库中查找同类问题的最佳策略（快速匹配）
            _best = _generator.get_best_strategy_for_category(_features["category"])
            if _best and _best.get("avg_quality", 0) > 0.7:
                _strategy = _best  # 使用历史验证过的最佳策略

            # ★动态组装prompt（不写死模板，用策略要素创造性组合）
            _prompt = _generator.build_prompt(
                _strategy, issue, local_analysis=_local_analysis,
                code_snippet=code_snippet, related_logs=related_logs)

            # ★第114批 T-114a（断5）：指纹咨询台账硬闸。
            #   同指纹问题若已 ≥3 次咨询且无任何成功记录，跳过本轮 LLM 问询，
            #   避免确定性回环持续烧 LLM（hub 记录按 task_type=patch_verification_learning 查）。
            if self._m114a_should_skip_ask(issue):
                # ★T-115d：拦截 INFO 日志已下沉到 _m114a_should_skip_ask（指纹级·可审计）
                return None
            # ★多轮追问（根据策略动态决定追问次数）
            _answer = self._call_llm_with_followup(
                _api_url, _api_key, _prompt, _strategy["max_followups"], scene,
                _api_model)

            # ★效果评估 + 策略沉淀（元学习闭环）
            if _answer:
                _quality = _generator.evaluate_answer_quality(_answer, _strategy, issue)
                _generator.record_strategy_result(_strategy, _quality, issue)
                if _quality.get("needs_followup") and _strategy["max_followups"] == 0:
                    # 评估认为需要追问但策略没安排，补一次追问
                    _followup = f"你的回答不够完整。\n上一轮回答:\n{_answer}\n请补充完整的修复代码（用```python包裹）和关键改动说明。"
                    _better = self._call_llm_with_followup(
                        _api_url, _api_key, _followup, 0, scene, _api_model)
                    if _better and len(_better) > len(_answer):
                        _answer = _better

            return _answer
        except Exception as e:
            # ★第86批 T-86a：原为裸 `except Exception: return None`——任何底层异常
            #   （配置缺失/JSON 解析/prompt 组装/HTTP 封装）都被吞成 None，表现为
            #   「LLM 补丁路径零产出且日志无任何线索」。改为留痕后仍降级返回 None。
            _module_logger.warning(
                f"[LLM修复] 修复建议生成异常，本轮 LLM 通道降级返回 None: "
                f"{type(e).__name__}: {e}")
            return None

    def _aesthetic_guidance(self) -> str:
        """获取框架审美偏好指引（生成回流），异常降级为空串（不阻断生成）。"""
        try:
            from nucleus.evolution.AestheticJudge import get_aesthetic_judge
            return get_aesthetic_judge().feedback_guidance()
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_aesthetic_guidance L1325")
            return ""

    # ===== ★主线第58批 T1（P1）：自主进化健康度监控 =====
    def _log_evolution_health(self, found: int, fixed: int, extra: dict) -> None:
        """★主线第58批 T1（P1）：自主进化健康度监控。

        每轮输出 发现数/修复数/修复率/失败原因分布，便于持续监控闭环健康。
        失败原因分布取自 extra['skip_reasons']/extra['fail_reasons']。
        任何异常都降级为 DEBUG 日志，绝不阻断主流程。
        """
        try:
            _found = int(found or 0)
            _fixed = int(fixed or 0)
            _rate = (_fixed / _found) if _found else 0.0
            _dist = extra.get("skip_reasons") or extra.get("fail_reasons") or {}
            _dist_items = list(_dist.items())[:8] if isinstance(_dist, dict) else []
            _dist_str = " / ".join(f"{_k}={_v}" for _k, _v in _dist_items)
            _skipped = extra.get("skipped")
            _skipped_str = f" 跳过={_skipped}" if _skipped is not None else ""
            _module_logger.info(
                f"[进化健康] 本轮 发现={_found} 修复={_fixed}{_skipped_str} 修复率={_rate:.1%}"
                + (f" 失败分布={_dist_str}" if _dist_str else ""))
        except Exception as _e:
            _module_logger.debug(f"[进化健康] 日志异常已忽略: {type(_e).__name__}: {_e}")

    def repair_with_distillation(self, issues: list[dict[str, Any]], self_inspector=None,
                                 scene: str | None = None) -> dict[str, Any]:
        """
        ★FIX: 「自身修复 → 大模型对比 → 知识蒸馏」闭环。
        对每个问题：先判断本地规则能否修复，再调大模型获取修复建议做对比，
        把对比结果（含 api_better 标记与大模型建议）记录到验证学习枢纽，沉淀为可复用规则。
        """
        # ★主线第59批 T2：问题文件路径过滤（排除标准库/项目外路径），
        #   避免 stdlib Traceback 占用修复名额。放在入口最前，确保被过滤问题
        #   不进入 alias 归一化/冷却/多步规划，不消耗 20 名额。不过度过滤：
        #   相对路径与项目内文件一律保留（即使 Traceback 含标准库帧也保留）。
        _filtered_out = 0
        _filtered_stdlib = 0
        _filtered_backup = 0
        _filtered_external = 0
        _skipped_empty_om = 0
        if issues:
            _kept = []
            for _i in issues:
                if not isinstance(_i, dict):
                    _kept.append(_i)
                    continue
                _fp = _i.get("file") or _i.get("file_path") or _i.get("path") or ""
                _reason = _issue_filter_reason(_fp, self._project_root)
                if _reason:
                    _filtered_out += 1
                    if _reason == "stdlib":
                        _filtered_stdlib += 1
                    elif _reason == "backup":
                        _filtered_backup += 1
                    elif _reason == "external":
                        _filtered_external += 1
                    continue
                # ★主线第60批 T5：organ/method 均为空且无法从路径推断 → 跳过（不消耗名额）
                _om_organ = (_i.get("organ") or "").strip()
                _om_method = (_i.get("method") or "").strip()
                if not _om_organ and not _om_method:
                    _module_logger.debug(
                        f"[代码片段] organ和method均为空，无法定位: {_fp}")
                    _inf = _infer_organ_from_path(_fp)
                    if not _inf:
                        _skipped_empty_om += 1
                        _module_logger.warning(
                            f"[问题过滤] organ和method均为空且无法推断，跳过: {_fp} ({_i.get('type', '')})")
                        continue
                    # 能推断 organ → 回填便于下游定位/日志（不写 _file，避免统计失真）
                    _i["organ"] = _inf
                _kept.append(_i)
            if _filtered_out or _skipped_empty_om:
                _module_logger.debug(
                    f"[问题过滤] 本轮过滤{_filtered_out}个"
                    f"（标准库{_filtered_stdlib}个 / 备份目录{_filtered_backup}个 / 项目外{_filtered_external}个），"
                    f"organ/method空跳过{_skipped_empty_om}个，保留{len(_kept)}个")
            issues = _kept
        from nucleus.mnemosyne.verification_learning_hub import (
            get_verification_learning_hub,
        )
        _hub = get_verification_learning_hub()
        # ★PHASE12-P1-2（2026-09-06）：入口一次性做类型名归一化。
        #   上游 self_inspector 产出的 mutable_default / print_debug
        #   与下游规则表期望的 mutable_default_arg / print_instead_of_log 不一致，
        #   导致这两类问题本地规则永远匹配不上、全部降级走 LLM 通道。
        #   在此统一收敛（原地改写 issues 中的 type 字段），
        #   后续所有 `type in {...}` 判定无需逐个改造，单点生效、零扩散。
        _alias_renamed = 0
        for _i in (issues or []):
            if isinstance(_i, dict) and "type" in _i:
                _old = _i.get("type")
                _new = _normalize_issue_type(_old)
                if _new != _old:
                    _i["type"] = _new
                    _alias_renamed += 1
        if _alias_renamed:
            _module_logger.info(
                f"[类型归一] 本轮修正{_alias_renamed}个问题的类型名笔误"
                f"（mutable_default→mutable_default_arg / "
                f"print_debug→print_instead_of_log），现已可命中本地规则")
        # ★主线C(C1)：接线 AdaptiveDecision（统一经验→决策反馈环，此前完全未接线的死代码）。
        #   用 ε-greedy + EMA 成功率跟踪「本地规则 vs LLM」两种修复策略的历史表现，
        #   为「本地修复精度是否已足够、能否减少 LLM 依赖」提供数据支撑（不改变
        #   现有的「并行对比」核心逻辑，仅在结果里附加自适应策略提示，零侵入）。
        try:
            from nucleus.reasoning.AdaptiveDecision import get_adaptive_decision
            _strategy_ad = get_adaptive_decision(
                "code_repair_strategy",
                strategies=["local_rule", "llm"],
                epsilon=0.3,
            )
        except Exception:
            _strategy_ad = None
        # ★P1补丁覆盖扩展：本地规则可修类型从 4 类扩到 6 类，
        # 与 _generate_patch 的 elif 分支保持一致（silent_exception/bare_except/
        # status_request_duplicate/unbounded_deque/thread_no_daemon/no_timeout_http）。
        _local_fixable_types = {
            "silent_exception", "bare_except", "status_request_duplicate",
            "unbounded_deque", "thread_no_daemon", "no_timeout_http",
            # ★2026-09-03扩展：6类→12类高置信度纯文本替换
            "mutable_default_arg",   # 可变默认参数 def f(x=[]) → None+初始化
            "comparison_with_none",  # == None → is None
            "boolean_comparison",    # == True/== False → 直接判断
            "os_path_join",          # 路径字符串拼接 → os.path.join
            "print_instead_of_log",  # print( → self._log(（器官类中）
            "fstring_preferred",     # .format() → f-string
            # ★主线第76批 T3：except 块内「裸 return None 且无日志」→ 补 WARNING 日志。
            #   实测该类型长期走「本地无规则·转LLM」，但语义明确、可高置信度文本替换。
            "bare_return_none_in_except",
        }
        # ★2026-09-03自主迭代闭环修复（v2）：本地修复与大模型对比并行，比对差异，学习提升。
        # 架构原则：框架自身先处理 → 大模型处理 → 比对差异 → 框架学习 → 逐步提升精度。
        # 大模型调用减少的前提：本地修复成功率>90%且质量与大模型相当，而非简单跳过。
        _local_fixable = 0
        _local_patched = 0       # 本地成功生成补丁
        _local_verified = 0      # 本地补丁验证通过
        _local_submitted = 0     # 本地补丁最终提交
        _llm_compared = 0        # 大模型对比次数
        _llm_better = 0          # 大模型方案更优次数
        _distilled = 0           # 知识蒸馏次数
        _submitted = 0           # LLM补丁提交次数
        _both_verified = 0       # 本地和大模型都验证通过（需择优）
        _llm_skipped_pending = 0  # ★P1-3：因已有待审批补丁而被门禁跳过的 LLM 调用次数
        # ★2026-09-04多步规划：复杂问题拆解为有序修复序列
        #   按依赖关系排序（基础→中等→复杂），同一方法的问题合并为一步

        # ★PHASE13-P1-3（2026-09-07）：僵尸问题冷却隔离入口。
        #   位置必须在 _plan_multi_step_repair **之前**——
        #   若放在之后，僵尸已经占掉那 12 步名额，再剔除也只是把它变成空位，
        #   可修问题依旧在第 13 位排队。只有先把僵尸从候选池里摘出去，
        #   让出问题：白名单/优先级排序是在剩余候选里重排的，名额才真能释放。
        _cooldown_skipped = 0
        _cooldown_expired = 0
        if self._no_fix_cooldown_enabled and issues:
            _now_mono = time.monotonic()
            _alive = []
            for _i in issues:
                if not isinstance(_i, dict):
                    _alive.append(_i)
                    continue
                _fp = self._cooldown_key(_i)
                _deadline = self._no_fix_cooldown.get(_fp)
                if _deadline is None:
                    _alive.append(_i)
                elif _now_mono < _deadline:
                    _cooldown_skipped += 1
                else:
                    # 冷却到期：解冻，本轮重新参与竞争
                    self._no_fix_cooldown.pop(_fp, None)
                    _cooldown_expired += 1
                    _alive.append(_i)
            if _cooldown_skipped or _cooldown_expired:
                _module_logger.info(
                    f"[P1-3 冷却隔离] 候选{len(issues)}个 → 放行{len(_alive)}个"
                    f"（冷却跳过{_cooldown_skipped}个"
                    f"／到期解冻{_cooldown_expired}个）")
            issues = _alive

        _steps = self._plan_multi_step_repair(issues, self_inspector)
        # 从规划步骤中展开为有序问题列表
        _sorted_issues = []
        for _step in _steps:
            _sorted_issues.extend(_step["issues"])
        # 去重（同一问题可能出现在多个步骤中）
        _seen = set()
        _unique_issues = []
        _dedup_dropped = 0  # ★C3：统计去重丢弃数，纳入结果分布
        for _i in _sorted_issues:
            # ★D3：改用统一身份键（含描述摘要），
            #   避免无 file/method 的日志类问题被去重键坍缩成同一个。
            _key = self._issue_identity(_i)
            if _key not in _seen:
                _seen.add(_key)
                _unique_issues.append(_i)
            else:
                _dedup_dropped += 1
        _sorted_issues = _unique_issues
        # ★9-问题3修复（2026-09-06）：由硬编码 min(10, ...) 改为实例属性
        #   self._max_repair_issues（默认 20），与上游 discover_all_issues
        #   的 max_issues=20 对齐（main.py:1746）。
        #   原 10 的上限低于上游放行的 20 个，是修复吞吐的第二道闸：
        #   即便多步规划不再截断，这里仍会把 20 个砍回 10 个。
        _process_count = min(self._max_repair_issues, len(_sorted_issues))
        if len(_sorted_issues) > _process_count:
            _module_logger.info(
                f"[修复蒸馏] 本轮{len(_sorted_issues)}个问题超出单轮上限"
                f"{self._max_repair_issues}，其余{len(_sorted_issues) - _process_count}个留待下轮")
        _module_logger.info(
            f"[修复蒸馏] 共{len(issues)}个问题，多步规划{len(_steps)}步，"
            f"处理前{_process_count}个（基础类型优先）")
        # ★P1-2修复（第十批）：逐问题结果归类，循环结束后输出「未修复原因分布」。
        #   此前修复率 17%（17 个问题只修 1 个）但无任何原因日志，
        #   无法判断剩余 16 个是「类型不支持」「无代码片段」「验证失败」还是「被去重」。
        _result_reasons: dict[str, int] = {}
        def _bump_reason(_r: str) -> None:
            _result_reasons[_r] = _result_reasons.get(_r, 0) + 1
        # ★C3：去重丢弃数也纳入分布（星轨要求的「去重丢弃」类别）
        # ★PHASE13（2026-09-07）：原写法把数量拼进 key（"去重丢弃×1"），
        #   而汇总输出处又会再拼一次 `_k×_v`，于是日志里出现
        #   「去重丢弃×1×1」这种重复尾巴；且数量变化时 key 也变，无法正确聚合。
        #   key 只应是类别名，数量交给 _bump_reason 累加。
        if _dedup_dropped > 0:
            _bump_reason("去重丢弃")

        for _issue in _sorted_issues[:_process_count]:
            _type = _issue.get("type", "")
            _organ = _issue.get("organ", "")
            _method = _issue.get("method", "")
            _local_ok = _type in _local_fixable_types
            if _local_ok:
                _local_fixable += 1
            # ★A1修复（主线A）：日志类问题（有 organ 标签、无 file/method）此前
            #   因 `_organ and _method` 不成立（_method 为空）拿不到代码片段，
            #   且 organ 标签（"Heart"/"代码学习"/"ScriptExecutor"）与 self_inspector
            #   的文件索引键（"PulseHeart"）不匹配，无法反查文件 → 永远落入
            #   「无代码片段(日志类)」无法修复。现用 resolve_organ_file 反查文件，
            #   至少拿到文件级定位（_file 补上），让日志类问题不再「凭空消失」。
            _file = _issue.get("file", "")
            _resolved_file = ""
            if self_inspector and _organ and not _file:
                try:
                    if hasattr(self_inspector, "resolve_organ_file"):
                        _resolved_file = self_inspector.resolve_organ_file(_organ) or ""
                        if _resolved_file:
                            _issue["file"] = _resolved_file
                            _file = _resolved_file
                except Exception as _resolve_err:
                    _module_logger.debug(
                        f"[修复蒸馏] organ→file 反查失败 {_organ}: {_resolve_err}")
            # ★第87批 T-87b：非器官标签的二级反查兜底（修复「补丁缺少 file 字段」）。
            #   实测根因（09-18 14:14 ~ 09-20 09:03 共 19 次沙箱拒绝）：日志类问题的
            #   organ 标签多为「非器官模块/类名」——PulseSnapshot / PatchManager /
            #   InfoField / PulseNodePool，而上一段的 resolve_organ_file 只覆盖
            #   organs/ 目录，对这些标签一律返回 None ⇒ issue["file"] 保持空字符串
            #   ⇒ 下游 LLM 补丁的 file 字段为空 ⇒ 路径沙箱在验证第一关拒绝并丢弃补丁
            #   （LLM token 与一次 verify_in_copy 全部白耗）。
            #   这里复用 self_inspector 既有的「全项目类索引」
            #   （_build_project_class_index：AST 扫描 organs/ 之外的 nucleus/、
            #   base/、functions/ 等，源码注释给出的口径是覆盖率 100%），
            #   按类名精确命中文件路径。
            #   ⚠ 口径红线：**只回填 issue["file"]**（LLM/本地补丁 file 字段的唯一来源），
            #     **不触碰 _file / _resolved_file** —— PHASE13 修正过的
            #     「日志类·已定位文件但缺方法 / 日志类·文件未定位」统计口径与代码片段
            #     获取路径仍由那两个变量决定，此处保持零变化。
            #   灰度开关 ENABLE_NONORGAN_FILE_RESOLVE（默认开）；关闭时与改造前一致。
            if self_inspector and _organ and not _issue.get("file"):
                _nor_on = True
                try:
                    import config as _cfg_nor87
                    _nor_on = bool(getattr(
                        _cfg_nor87, "ENABLE_NONORGAN_FILE_RESOLVE", True))
                except Exception:
                    _nor_on = True
                if _nor_on:
                    try:
                        _lookup87 = getattr(
                            self_inspector, "_lookup_class_in_project", None)
                        _hit87 = _lookup87(_organ) if callable(_lookup87) else None
                        _hit87_file = str((_hit87 or {}).get("file_path") or "")
                        if _hit87_file and os.path.isfile(_hit87_file):
                            _issue["file"] = _hit87_file
                            _module_logger.debug(
                                f"[修复蒸馏] 非器官标签二级反查命中: {_organ} → "
                                f"{_hit87_file}（resolve_organ_file 未覆盖，"
                                f"原会因缺少 file 被路径沙箱拒绝）")
                    except Exception as _lookup87_err:
                        _module_logger.debug(
                            f"[修复蒸馏] 非器官标签二级反查失败 {_organ}: "
                            f"{type(_lookup87_err).__name__}: {_lookup87_err}")
            # ★P2-18（2026-09-09 技术债务第一批）：日志里的 file/method 定位兜底。
            #   日志类问题（LogAnalyzer 的 ERROR 行分支）构造 issue 时只填了 organ
            #   标签，file 与 method 皆为空字符串 → 原实现在日志里一律打「未定位」，
            #   运维和自主进化看到「未定位」就**无从下手**（定位不到文件就修不了），
            #   这条日志等于白记。现按 file → organ → method → 默认 的优先级
            #   生成**展示用**标签，至少给出可追查的线索。
            #   ⚠ 零冲突红线：这里**只新增 _loc_label 供日志展示，绝不动 _file /
            #     _resolved_file** —— :1152 的「日志类·已定位文件但缺方法」与
            #     「日志类·文件未定位」这两个统计口径正由这两个变量决定（PHASE13 修正过，
            #     :1247 还拿它算 skipped_no_snippet）。一旦把 organ 回填进 _file，
            #     大批问题会被错误地计入「已定位文件」，统计分布直接失真。
            #   「未知」是 LogAnalyzer 正则未命中时填的占位（非真实器官名），视为缺失。
            _organ_clue = _organ if _organ and _organ != "未知" else ""
            _method_clue = _method if _method and _method != "未知" else ""
            _loc_label = (_file or _resolved_file or _organ_clue
                          or _method_clue or "未定位")
            # 读取问题代码片段
            _snippet = ""
            if self_inspector and _organ and _method:
                try:
                    _detail = self_inspector.get_method_body(_organ, _method)
                    _snippet = _detail.get("body", "") if _detail else ""
                    if not _snippet:
                        _module_logger.debug(
                            f"[代码片段] 方法不存在: {_file or _resolved_file}:{_method}")
                except Exception as _snippet_err:
                    _module_logger.debug(
                        f"[修复蒸馏] 获取代码片段失败 {_organ}.{_method}: {_snippet_err}")
            _related_logs = self._find_related_logs(_file or _resolved_file, _method)
            _snippet_source = "method_body" if _snippet else ""
            # ★第88批 T-88a：素材来源标记与开关预置（在 _fallback_on 分支外
            #   初始化，保证第88批守卫读取时恒有定义，零 NameError 风险）。
            _m88_src = ""
            _m88_mat_on = False
            if not _snippet:
                # ★主线第22批 T4/P2-122：空片段兜底 —— 不再直接跳过 LLM 对比。
                #   原实现「方法体取不到 → 直接跳过」，导致日志类 ERROR（如
                #   ReasoningWorkerPool / SelfAwarenessEngine，日志里只有器官标签、
                #   没有 method）**永远进不了**修复蒸馏，自我修复能力被削弱。
                #   现按「文件+方法 → 文件 → ERROR 消息/相关日志」优先级兜底，
                #   仍无素材才跳过（并明确记录原因，不再是静默空片段）。
                #   开关 ENABLE_REPAIR_SNIPPET_FALLBACK 关闭时与改造前完全一致。
                _fallback_on = True
                try:
                    import config as _cfg_m22
                    _fallback_on = bool(
                        getattr(_cfg_m22, "ENABLE_REPAIR_SNIPPET_FALLBACK", True))
                except Exception:
                    _fallback_on = True
                if _fallback_on:
                    # ★第88批 T-88a（根因：错误素材 ⇒ 结构性不可应用补丁）：
                    #   第87批把「非器官标签」的真文件路径回填进了 issue["file"]
                    #   （受 PHASE13 口径红线约束，刻意未动 _file/_resolved_file），
                    #   但本处素材提取读的是 _file/_resolved_file（此时仍为空）
                    #   ⇒ 读不到文件 ⇒ 素材退化成 ERROR 日志文本 ⇒ 补丁
                    #   original_code 不是代码：
                    #     ① 完整性关3「与原文相似度≥0.5」必然判 0.03（实测 11:18:20）；
                    #     ② 即便绕过，PatchManager._verify_in_copy 的
                    #        「original_code not in full_content」仍会拦下。
                    #   ⇒ 把已解析的 issue["file"] 并入素材来源（只读，不写回
                    #     _file/_resolved_file，PHASE13 统计口径零变化）。
                    #   灰度 ENABLE_M88_MATERIAL_FROM_RESOLVED_FILE：关闭 ⇒
                    #   素材选择与补丁构造判定逐字回到第87批行为。
                    _m88_mat_on = True
                    try:
                        import config as _cfg_m88
                        _m88_mat_on = bool(getattr(
                            _cfg_m88, "ENABLE_M88_MATERIAL_FROM_RESOLVED_FILE", True))
                    except Exception:
                        _m88_mat_on = True
                    _m88_mat_file = _file or _resolved_file
                    if _m88_mat_on and not _m88_mat_file:
                        _m88_mat_file = str(_issue.get("file", "") or "")
                    _snippet = self._extract_comparison_material(
                        _issue, _related_logs, _m88_mat_file, _method)
                    # 素材来源判定：能按「文件(+方法)」读出代码 ⇒ 代码素材；
                    # 否则为 ERROR 日志文本（非代码，补丁不可应用）。
                    if _snippet and _m88_mat_file:
                        try:
                            if self._read_snippet_from_file(_m88_mat_file, _method):
                                _m88_src = "code_from_file"
                        except Exception as _m88_se:
                            _module_logger.debug(
                                f"[修复蒸馏] 素材来源判定失败（按非代码处理）: "
                                f"{type(_m88_se).__name__}: {_m88_se}")
                    if _snippet:
                        _snippet_source = "error_message"
                        if _m88_mat_on:
                            _m88_src = _m88_src or "error_message"
                            _snippet_source = _m88_src
                        _module_logger.debug(
                            f"[修复蒸馏] 方法体为空 → 改用文件/ERROR消息作为对比输入: "
                            f"type={_type}, organ={_organ}, method={_method}, "
                            f"file={_loc_label}, 素材={len(_snippet)}字, "
                            f"来源={_snippet_source}")
                    else:
                        _module_logger.debug(
                            f"[修复蒸馏] 无法提取代码片段，跳过修复蒸馏: "
                            f"type={_type}, organ={_organ}, method={_method}, "
                            f"file={_loc_label}")
                else:
                    _module_logger.debug(
                        f"[修复蒸馏] 代码片段为空，跳过LLM对比: "
                        f"type={_type}, organ={_organ}, method={_method}, "
                        f"file={_loc_label}")  # ★P2-18：无文件时退而给出 organ/method 线索

            # ===== 路径A：框架自身先处理（本地规则修复）=====
            _local_patch = None
            _local_verify_passed = False
            # ★C3：前置检查「同文件同方法已有待审批补丁」，命中则统计并跳过本地生成，
            #   避免重复生成（_generate_patch 内部 :2047 的预检是最终兜底，此处是
            #   可观测性增强——让「已有待审批」在结果分布里可见，而非坍缩进「无有效方案」）。
            _has_pending_dup = False
            if _file and _method:
                try:
                    if self._m94_pending_blocks_regeneration(_file, _method):
                        _has_pending_dup = True
                        _bump_reason("已有待审批")
                except Exception as _exc:
                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            if _local_ok and self_inspector and _organ and _method and _snippet and not _has_pending_dup:
                try:
                    _plan = {
                        "type": _type,
                        "target": _organ,
                        "method": _method,
                        "description": _issue.get("description", ""),
                        "risk_score": _issue.get("risk_score", 2),
                        "benefit_score": _issue.get("benefit_score", _DEF_BENEFIT_SCORE),
                    }
                    _local_patch = self._generate_patch(_plan, self_inspector)
                    if _local_patch and _local_patch.get("modified_code") and _local_patch["modified_code"] != _local_patch.get("original_code", ""):
                        _local_patched += 1
                        _verify = self._patch_manager.verify_in_copy(_local_patch)
                        _local_patch["verification"] = _verify
                        _local_patch["source"] = "local_rule"
                        if _verify.get("passed"):
                            _local_verified += 1
                            _local_verify_passed = True
                            # ★第117批 T-117b：成功即出清同指纹连败计数（断5 棘轮自愈）。
                            #   失败侧在下方 else 分支递增，成功侧此前零出清 ⇒ 单向棘轮。
                            self._m114a_clear_ratchet(
                                self._cooldown_key(_issue),
                                reason="本地修复验证通过")
                            # ★主线C(C1)：本地规则修复成功，报告到 AdaptiveDecision
                            if _strategy_ad is not None:
                                try:
                                    _strategy_ad.report("local_rule", success=True)
                                except Exception as _exc:
                                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                        else:
                            # ★第114批 T-114a（断4）：失败原因解码——
                            #   _verify_in_copy 结果此前无 reason 字段，永远落「未知」；
                            #   现改用 failed_check（syntax/import/in_copy_match/indent…）。
                            _fc = _verify.get("failed_check") or _verify.get("reason", "未知")
                            _module_logger.debug(
                                f"[本地修复] {_organ}.{_method} ({_type}) 验证未通过: {str(_fc)[:80]}")
                            # ★第114批 T-114a（断2）：验证失败的题登记冷却，阻断每轮重扫重问。
                            #   _issue 即本轮被问的题，冷却指纹与 discover 产出的身份一致。
                            self._m114a_register_verify_failure(
                                self._cooldown_key(_issue), detail=str(_fc))
                            # ★主线C(C1)：本地规则修复失败，报告到 AdaptiveDecision
                            if _strategy_ad is not None:
                                try:
                                    _strategy_ad.report("local_rule", success=False)
                                except Exception as _exc:
                                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                    else:
                        _module_logger.debug(
                            f"[本地修复] {_organ}.{_method} ({_type}) 补丁生成失败或无变化")
                        # ★第116批 T-116a①：该主分支此前**零冷却登记**（断2 只覆盖"验证未通过"），
                        #   导致 Liver×2 每轮被重扫重问、永无冷却。补登记使其进冷却闭环。
                        self._m114a_register_verify_failure(
                            self._cooldown_key(_issue), detail="补丁生成失败或无变化")
                except Exception as _le:
                    _module_logger.error(f"[本地修复] {_organ}.{_method} ({_type}) 异常: {_le}")
            # ★M85-1（第85批 T-85a）：本地学习尝试通道。
            #   对「本地无规则」的问题先做一次**保守低风险**修复尝试
            #   （不替代 LLM 通道 / 不自动应用 / 只入队待审批），并把结果记入
            #   data/patches/local_learning_attempts.jsonl，形成
            #   「尝试 → 验证 → 学习 → 提升」闭环。
            #   ★零侵入：不 return / 不 continue、不复用 _local_patch 变量，
            #     路径A 与路径B 的行为逐字不变；关闭开关即完全短路。
            if (not _local_ok and not _has_pending_dup and _snippet
                    and self_inspector and _organ and _method
                    and (_file or _resolved_file)):
                try:
                    self._m85_learning_attempt(
                        _issue, _file or _resolved_file, _method, _snippet, _organ)
                except Exception as _m85e:
                    _module_logger.debug(
                        "[M85 学习尝试] 通道异常（已忽略）: %s: %s",
                        type(_m85e).__name__, _m85e)

            # ===== 路径B：大模型处理（并行对比，学习阶段不可避免）=====
            # ★PHASE12-P1-3（2026-09-06）：LLM 通道补丁生成门禁。
            #   实测证据：9 小时运行产生 34 次重复补丁生成，同一 (file, method)
            #   的问题被反复送进 LLM——路径A已有 has_pending 预检，路径B完全没有，
            #   于是「本地被门禁拦下、LLM 照跑不误」，白烧 token 且往 pending 队列
            #   继续堆重复项（入队阶段虽有去重，但 LLM 调用成本已经发生且不可回收）。
            #   门禁策略（★最小侵入，不改动「本地 vs LLM 并行对比学习」的设计意图）：
            #       仅在「同位置已有待审批补丁」时跳过 LLM —— 此时再生成一次
            #       属确定性浪费；本地已修好的场景仍继续对比学习，保持原有闭环。
            _llm_suggestion = None
            if _snippet and not _has_pending_dup:
                _llm_suggestion = self._call_llm_for_repair(
                    _issue, _snippet, _related_logs, scene)
            elif _has_pending_dup:
                _llm_skipped_pending += 1
                _module_logger.info(
                    f"[LLM门禁] 跳过 {_loc_label}.{_method} ({_type})："  # ★P2-18
                    f"该位置已有待审批补丁，等待人工裁决后再生成，避免重复调用")
            else:
                # ★主线第60批 T5：_snippet 为空降级 —— 文件存在且项目内 → 取前100行
                #   作为上下文调用 LLM（尽力而为，不保证100%修复成功，但避免修复率恒为0%）。
                #   灰度开关 ENABLE_REPAIR_SNIPPET_DEGRADATION 关闭时与改造前完全一致。
                _degrade_on = True
                try:
                    import config as _cfg_m60
                    _degrade_on = bool(
                        getattr(_cfg_m60, "ENABLE_REPAIR_SNIPPET_DEGRADATION", True))
                except Exception:
                    _degrade_on = True
                if _degrade_on and _file and not _has_pending_dup:
                    try:
                        _deg = self._read_snippet_from_file(_file, "", max_lines=100)
                        if _deg:
                            _snippet = _deg
                            _snippet_source = "file_head_degrade"
                            _module_logger.debug(
                                f"[修复蒸馏] _snippet为空降级：用文件前100行调用LLM: "
                                f"type={_type}, file={_file}, 素材={len(_snippet)}字")
                            _llm_suggestion = self._call_llm_for_repair(
                                _issue, _snippet, _related_logs, scene)
                    except Exception as _deg_err:
                        _module_logger.debug(
                            f"[修复蒸馏] _snippet为空降级失败: {type(_deg_err).__name__}: {_deg_err}")
            _llm_patch = None
            _llm_verify_passed = False
            if _llm_suggestion:
                _llm_compared += 1
                # 尝试将LLM建议转为补丁并验证
                try:
                    _llm_clean = self._clean_llm_code(_llm_suggestion)  # ★PHASE17-A2
                    _llm_clean, _ = self._m91_align_base_indent(
                        _snippet, _llm_clean,
                        f"file={_issue.get('file', '')}, "
                        f"method={_issue.get('method', '')}")  # ★第91批T-91b
                    # ★第87批 T-87b：补丁 file 字段必填校验（防呆 + 留痕）。
                    #   若上面两级反查后 issue["file"] 仍为空（如 organ 是"胃"这类
                    #   中文器官名），构造出的补丁必然被 _check_patch_path 以
                    #   「补丁缺少 file 字段」拒绝 —— 既然注定被拒，就不再生成、
                    #   也不再消耗一次 verify_in_copy（文件复制 + 语法 + 导入三轮检查），
                    #   改为显式留痕，让「未定位」在日志里可见而不是伪装成验证失败。
                    _llm_no_file = not str(_issue.get("file", "") or "").strip()
                    # ★第90批 T-90a：original_code 必填（与 _llm_no_file /
                    #   _llm_bad_material **同型**处置）。
                    #   补丁的 original_code 取自素材 _snippet；素材为空时
                    #   `_check_llm_patch_completeness` 关0 会以「缺少 original_code」
                    #   拒绝（改前则由关3 以「相似度过低(0.00)」拒绝）⇒ 注定失败，
                    #   不如不构造、不消耗一次 verify_in_copy，并显式留痕。
                    #   ★可复现的现状（第90批 T0 实测）：`_llm_suggestion` 仅在
                    #     `_snippet` 非空时被赋值（:1465 与降级分支 :1487）⇒ 本闸
                    #     当前**结构性不可达**，属契约显式化（零行为变化），
                    #     用于在素材提取链路未来变更时自保。
                    _llm_no_snippet = not str(_snippet or "").strip()
                    # ★第88批 T-88a：素材非代码（ERROR 日志文本）时不得构造补丁 ——
                    #   补丁契约要求 original_code 能在目标文件里定位
                    #   （_verify_in_copy 的「original_code not in full_content」），
                    #   日志文本找不到 ⇒ 该补丁注定以「与原文相似度过低(0.03<0.5)」
                    #   或「code_not_found」失败，且把「素材取错」误报成「LLM 生成
                    #   残缺/截断」，掩盖真因。与第87批 _llm_no_file 同型处置：
                    #   注定失败就不再生成、也不再消耗一次 verify_in_copy，改为留痕。
                    _llm_bad_material = bool(_m88_mat_on and _m88_src == "error_message")
                    if _llm_no_snippet:
                        _module_logger.warning(
                            "[LLM修复] 跳过补丁构造：对比素材为空（无法提供 "
                            "original_code，补丁字段契约不完整）——"
                            "file=%s, organ=%s, method=%s, type=%s, 素材=0字",
                            _loc_label, _organ, _method, _type)
                    elif _llm_no_file:
                        _module_logger.warning(
                            "[LLM修复] 跳过补丁构造：问题未定位到文件"
                            "（organ=%s, method=%s, type=%s）——补丁会因缺少 file "
                            "字段被路径沙箱拒绝，先跳过避免无效验证",
                            _organ, _method, _type)
                    elif _llm_bad_material:
                        _module_logger.warning(
                            "[LLM修复] 跳过补丁构造：对比素材非代码（ERROR 日志文本，"
                            "file=%s, organ=%s, method=%s, type=%s, 素材=%d字）——"
                            "original_code 无法在目标文件中定位，补丁必然以"
                            "「与原文相似度过低」或「code_not_found」失败；"
                            "请按「无方法名的日志类问题缺少修复单元」处理",
                            _loc_label, _organ, _method, _type, len(_snippet))
                    if (_llm_clean and _llm_clean.strip() != _snippet.strip()
                            and not _llm_no_file and not _llm_bad_material
                            and not _llm_no_snippet):  # _M90_FIELD_CONTRACT
                        _llm_patch = {
                            "id": f"patch_llm_{int(__import__('time').time())}_{abs(hash(_llm_clean)) & 0xFFFF:04x}",
                            "file": _issue.get("file", ""),
                            "method": _method,
                            "issue_type": _type,
                            "risk_level": _issue.get("risk_level", "低"),
                            "description": _issue.get("description", ""),
                            "original_code": _snippet,
                            "modified_code": _llm_clean,
                            "diff_summary": self._generate_diff_summary(_snippet, _llm_clean),
                            "trust_score": 70,
                            "generated_at": __import__('time').time(),
                            "status": "pending",
                            "applied": False,
                            "source": "llm",
                        }
                        _llm_verify = self._patch_manager.verify_in_copy(_llm_patch)
                        _llm_patch["verification"] = _llm_verify
                        if _llm_verify.get("passed"):
                            _llm_verify_passed = True
                            # ★主线C(C1)：LLM 修复成功，报告到 AdaptiveDecision
                            if _strategy_ad is not None:
                                try:
                                    _strategy_ad.report("llm", success=True)
                                except Exception as _exc:
                                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                        # ★主线C(C1)：LLM 修复失败，报告到 AdaptiveDecision
                        elif _strategy_ad is not None:
                            try:
                                _strategy_ad.report("llm", success=False)
                            except Exception as _exc:
                                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # ===== 路径C：比对差异 + 知识蒸馏 + 择优提交 =====
            _llm_is_better = False  # ★v26.0修复：移到块外初始化，避免_llm_suggestion为False时变量未定义
            if _llm_suggestion:
                # 比对：大模型是否提供了更优解
                if _local_verify_passed and _llm_verify_passed:
                    _both_verified += 1
                    # 简单择优：LLM修改更全面（diff更长）或本地未覆盖时认为LLM更优
                    _local_diff_len = len(_local_patch.get("diff_summary", ""))
                    _llm_diff_len = len(_llm_patch.get("diff_summary", ""))
                    _llm_is_better = _llm_diff_len > _local_diff_len * 1.5
                elif not _local_verify_passed and _llm_verify_passed:
                    _llm_is_better = True  # 本地失败，LLM成功
                if _llm_is_better:  # type: ignore[possibly-unbound]
                    _llm_better += 1

                # 知识蒸馏：记录对比结果到学习枢纽
                try:
                    _hub.record(
                        organ="code_learner",
                        task_type="code_repair_distill",
                        input_summary=f"{_type}:{_organ}.{_method}",
                        local_result={
                            "local_fixable": _local_ok,
                            "local_patched": _local_patch is not None,
                            "local_verified": _local_verify_passed,
                            "snippet": _snippet[:300],
                        },
                        confidence=0.7,
                        relevance_score=0.8,
                        needs_verification=not _local_ok,
                        verification_result={
                            "llm_suggestion": _llm_suggestion[:800],
                            "llm_verified": _llm_verify_passed,
                            "llm_better": _llm_is_better,  # type: ignore[possibly-unbound]
                            "related_logs": _related_logs[:500],
                        },
                        api_better=_llm_is_better,  # type: ignore[possibly-unbound]
                        lesson=f"{'大模型方案更优，需学习' if _llm_is_better else '本地方案已足够，大模型无增量'}: {_type}修复对比（本地{'验证通过' if _local_verify_passed else '验证失败'}, LLM{'验证通过' if _llm_verify_passed else '验证失败'}）",  # type: ignore[possibly-unbound]
                    )
                    _distilled += 1
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # ===== 择优提交：优先提交验证通过的补丁 =====
            # 策略：本地验证通过→提交本地（零冲突高置信）；本地失败但LLM通过→提交LLM
            if _local_verify_passed and _local_patch:
                try:
                    _local_patch["status"] = "verified"
                    # ★P1-2(2026-09-03)：记录修复时间戳和基线错误数，用于运行时日志验证
                    _local_patch["fixed_at"] = time.time()
                    # ★第41批 T1（P0-263）：基线改为「修复前 N 天」窗口（原 since=0 全量历史）
                    _local_patch["baseline_errors"] = self._count_errors_for_location(
                        _issue.get("file", ""), _method,
                        since=self._m41_baseline_since())
                    _local_patch["needs_runtime_verify"] = True
                    self._patch_manager.save_pending_patch(_local_patch)
                    _local_submitted += 1
                    _module_logger.info(
                        f"[自主修复] {_organ}.{_method} ({_type}) 本地补丁验证通过，提交待审批"
                        f"{'（大模型方案更优，建议人工对比）' if _llm_is_better else ''}")  # type: ignore[possibly-unbound]
                except Exception as _se:
                    _module_logger.error(f"[自主修复] 本地补丁提交失败: {_se}")
            elif _llm_verify_passed and _llm_patch:
                try:
                    _llm_patch["status"] = "verified"
                    # ★P1-2(2026-09-03)：记录修复时间戳和基线错误数
                    _llm_patch["fixed_at"] = time.time()
                    # ★第41批 T1（P0-263）：基线改为「修复前 N 天」窗口
                    _llm_patch["baseline_errors"] = self._count_errors_for_location(
                        _issue.get("file", ""), _method,
                        since=self._m41_baseline_since())
                    _llm_patch["needs_runtime_verify"] = True
                    self._patch_manager.save_pending_patch(_llm_patch)
                    _submitted += 1
                    _module_logger.info(
                        f"[自主修复] {_organ}.{_method} ({_type}) 本地修复失败，LLM补丁验证通过，提交待审批")
                except Exception as _se:
                    _module_logger.error(f"[自主修复] LLM补丁提交失败: {_se}")

            # ★P1-2修复（第十批）：记录本问题的处理结果，供循环结束后的原因分布统计。
            if _has_pending_dup:
                # ★C3：已有待审批的问题已在前置检查处计入，此处跳过后续归类，避免重复计数
                continue
            if _local_verify_passed or _llm_verify_passed:
                _bump_reason("已修复提交")
            elif _type in _HIGH_RISK_NON_FIXABLE:
                # ★PHASE13-P1-1（2026-09-07）：安全边界的归类必须**优先于**
                #   片段可用性。原链条把 `not _local_ok and not _snippet`
                #   排在最前，于是一个 unsafe_eval 问题只要恰好取不到代码片段，
                #   就会被记成「日志类·文件未定位」——把它伪装成了定位能力缺陷，
                #   掩盖了「这是安全策略故意不修」的事实。
                #   后果很实际：运维会以为该去补 self_inspector 的索引，
                #   而真正该做的是「别动它」。**类型归属先于可用性**更符合语义。
                _bump_reason(f"高危·安全拦截({_type})")
            elif not _local_ok and not _snippet:
                # ★A1修复（主线A）：细分「日志类问题」的定位状态，形成可观测闭环——
                #   已反查到文件（仍缺 method 无法方法级修复）vs 连文件都无法定位。
                if _file or _resolved_file:
                    _bump_reason("日志类·已定位文件但缺方法")
                else:
                    _bump_reason("日志类·文件未定位")
            elif not _local_ok:
                # ★PHASE13（2026-09-07）：拆分「不支持」的两种截然不同的语义。
                #   原措辞统一是「类型不支持(X)」，读者无从分辨，星轨据此在
                #   15 小时分析报告里建议「把 unsafe_eval/sql_injection 补进
                #   白名单」—— 那是 _HIGH_RISK_NON_FIXABLE 安全边界，补了等于
                #   允许自动改写注入点，风险极高。措辞不清直接导致了错误结论。
                #   现拆为两类，让日志自己说清楚：
                #     高危·安全拦截(X)   = 故意不修，防线在工作，属健康信号
                #     本地无规则·转LLM(X) = 设计如此，本地只做高置信度文本替换
                _bump_reason(f"本地无规则·转LLM({_type})")
            elif not _snippet:
                _bump_reason("无代码片段")
            elif _local_patch is None and _llm_suggestion is None:
                _bump_reason("本地+LLM均无有效方案")
            elif _local_patch is not None and _llm_suggestion is None:
                _bump_reason("本地补丁验证失败")
            elif _local_patch is None and _llm_suggestion is not None:
                _bump_reason("LLM补丁验证失败")
            else:
                _bump_reason("补丁验证均失败")

        # ★P1-2修复（第十批）：输出未修复原因分布，便于定位修复率瓶颈。
        if _result_reasons:
            _reason_str = "，".join(f"{_k}×{_v}" for _k, _v in sorted(
                _result_reasons.items(), key=lambda kv: -kv[1]))
            _module_logger.info(
                f"[修复蒸馏] 问题处理结果分布: {_reason_str}")

        # ★PHASE13-P1-3：回填冷却表。
        #   只有「本轮确实处理过、且判定为不可自动修复」的问题才登记，
        #   未进入本轮候选（已被 max_steps 截到后面）的不登记——
        #   否则会把「还没轮到的」误判成「修不了的」，那才是真的饥饿。
        #   分类判据刻意只取「仅凭 issue 自身即可 100% 确定」的两类：
        #     ① type ∈ _HIGH_RISK_NON_FIXABLE → 安全边界，查表即得，不会误判
        #     ② type ∉ _local_fixable_types   → 本地无规则，只能转 LLM
        #   刻意**不**重算「已有待审批」：那一类需要查 PatchManager，
        #   且 PulseCodeLearner 侧已有成熟门禁在拦，此处重复登记只会增加误伤面。
        #   宁可漏登记（下轮多跑一次），不可误登记（把能修的冻住）。
        def _cooldown_classify(_it: dict[str, Any]) -> str | None:
            _t = str(_it.get("type", "") or "")
            if not _t:
                return None
            if _t in _HIGH_RISK_NON_FIXABLE:
                return "高危·安全拦截"
            if _t not in _local_fixable_types:
                return "本地无规则·转LLM"
            return None

        _cooled_down = 0
        if self._no_fix_cooldown_enabled:
            _now_mono2 = time.monotonic()
            for _issue in _sorted_issues[:_process_count]:
                if not isinstance(_issue, dict):
                    continue
                _why = _cooldown_classify(_issue)
                if _why is None:
                    continue
                _ttl = self._cooldown_ttl_for(_why)
                self._no_fix_cooldown[
                    self._cooldown_key(_issue)] = _now_mono2 + _ttl
                _cooled_down += 1
            # 顺带清理已解冻的历史项，避免字典随运行时间无界增长
            if len(self._no_fix_cooldown) > 512:
                for _k in [k for k, v in self._no_fix_cooldown.items()
                           if v <= _now_mono2]:
                    self._no_fix_cooldown.pop(_k, None)
            if _cooled_down:
                _module_logger.info(
                    f"[P1-3 冷却隔离] 本轮登记 {_cooled_down} 个不可修复问题，"
                    f"冷却期内不再占用修复名额（到期自动解冻复检）")
                # ★第114批 T-114a（断6）：冷却登记变更后落盘，重启可续。
                self._m114a_save_cooldown()

        # ★T3修复（N1/P1）：原返回字典**没有** repaired 与 pass_rate 两个键，
        #   而 main.py:1768-1769 用的是
        #     _result.get('repaired', 0) / _result.get('pass_rate', 0)
        #   → 无论实际修复是否成功，日志恒显示「0/N个, 通过率=0%」。
        #   生产 11:21:43 的「修复完成: 0/1个, 通过率=0%」即该恒等式的产物，
        #   无法据此判断自主进化到底有没有干活（星轨据此报了 N1）。
        #   此处补齐两个键，并额外给出是否全部跳过的标记，便于排查。
        _repaired = _local_submitted + _submitted
        # ★M84-4（第84批 T-84b）：口径分层——`_repaired` 实为「提交待审批数」，
        #   与「已应用」「已验证修复」是三个不同的量。此前统一叫"修复"，导致下游
        #   （含只读诊断）把 _repaired=0 误读为"要求运行时验证通过才 +1 的天花板"。
        #   本行**只读**补丁历史做汇总，不改任何既有返回值与流程。
        _m84_ledger = self._m84_patch_ledger()
        _total_in = max(1, len(_sorted_issues[:_process_count]))
        # ★PHASE13-P1-1（2026-09-07）：skipped_no_snippet 口径修错。
        #   原写法 `_total_in - _repaired` 把**所有未修复**问题都算成
        #   「缺少 organ/method 无法定位代码」，于是 main.py 打出
        #     「13个因缺少organ/method无法定位代码而跳过」
        #   而真实分布是（见 2h33m 日志 09:49:16）：
        #     已有待审批×4 / 高危·安全拦截(unsafe_eval)×3 / 高危·安全拦截(sql_injection)×3
        #     / 本地无规则·转LLM×2 / 去重丢弃×1
        #   —— 没有一个是「缺少 organ/method」。这条误导日志直接掩盖了
        #   「队列被僵尸问题占满」的真根因，是本轮最贵的一处错误信息。
        #   此处改为只统计真正因拿不到代码片段而被跳过的三类。
        _NO_SNIPPET_KEYS = ("日志类·已定位文件但缺方法",
                            "日志类·文件未定位",
                            "无代码片段")
        _skipped_no_snippet = sum(_result_reasons.get(_k, 0)
                                  for _k in _NO_SNIPPET_KEYS)
        # ★主线第60批 T5：本轮被「跳过」总计数 = 入口过滤（备份/标准库/项目外）
        #   + organ/method 空无法推断。让运维看清有多少问题被跳过，而非误以为修复率低。
        _skipped = _filtered_out + _skipped_empty_om
        # ★第十一批 批次C 3.3：自主进化修复率监控埋点。
        #   此处是唯一同时掌握「本轮处理问题数」与「真实修复数 _repaired」的位置
        #   （main.py 的 repaired/pass_rate 正是取自本返回字典），故埋在这里最准。
        #   只做计数与落盘，绝不改变原有返回值与流程。
        _m84_extra = {"skip_reasons": _result_reasons, "skipped": _skipped,
                      "submitted": _submitted,
                      "local_patched": _local_patched, "local_verified": _local_verified}
        if _m84_ledger:
            _m84_extra.update(_m84_ledger)
        self._log_evolution_health(len(_sorted_issues[:_process_count]), _repaired,
                                   _m84_extra)
        try:
            from nucleus.evolution.PatchAutoApprover import record_evolution_round
            _m84_round_extra = {"submitted": _submitted,
                                "local_patched": _local_patched,
                                "local_verified": _local_verified}
            if _m84_ledger:
                _m84_round_extra.update(_m84_ledger)
            record_evolution_round(
                len(_sorted_issues[:_process_count]), _repaired, _m84_round_extra)
        except Exception as e:
            _module_logger.debug(f"修复率埋点异常已忽略: {type(e).__name__}: {e}")
        return {
            "repaired": _repaired,
            "pass_rate": (_repaired / _total_in) if _total_in else 0.0,
            "processed": len(_sorted_issues[:_process_count]),
            # 只含真正拿不到代码片段的数量；其余原因见 skip_reasons
            "skipped_no_snippet": _skipped_no_snippet,
            # ★主线第60批 T5：本轮被跳过（过滤 + organ/method 空无法推断）的问题总数
            "skipped": _skipped,
            "local_fixable": _local_fixable,
            "local_patched": _local_patched,
            "local_verified": _local_verified,
            "local_submitted": _local_submitted,
            "llm_compared": _llm_compared,
            "llm_better": _llm_better,
            "both_verified": _both_verified,
            "distilled": _distilled,
            "submitted": _submitted,
            # ★PHASE12-P1-3：LLM 门禁拦截次数（可观测：省下了多少次无效调用）
            "llm_skipped_pending": _llm_skipped_pending,
            # ★PHASE13-P1-3：僵尸冷却隔离成效（长期为 0 说明已无可排队的僵尸）
            "cooldown_skipped": _cooldown_skipped,
            "cooldown_registered": _cooled_down,
            # ★PHASE12-P1-2：本轮修正的类型名笔误数量（>0 说明上游命名仍未对齐）
            "type_alias_renamed": _alias_renamed,
            # ★P1-2修复（第十批）：问题处理结果分布，供上游日志/监控消费。
            # ★PHASE13-P1-1：以 skip_reasons 之名再暴露一份副本。
            #   result_reasons 含「已修复提交」，语义上是「全量结果分布」；
            #   上游 main.py 需要的是「未修复原因分布」，语义不同。
            #   与其让上游自己减，不如在这里给一个名字干净的副本，
            #   避免再出现「把总数当某一类」的同口径事故。
            "result_reasons": _result_reasons,
            "skip_reasons": {_k: _v for _k, _v in _result_reasons.items()
                             if _k != "已修复提交"},
            # ★主线C(C1)：自适应策略提示——本地 vs LLM 的 EMA 成功率，
            #   供上游判断「本地修复精度是否已足够、能否减少 LLM 依赖」。
            "adaptive_strategy": (_strategy_ad.get_stats() if _strategy_ad is not None else None),
        }

    # ========== ★P1-2(2026-09-03) 运行时日志验证 + 修复效果对比 ==========

    def _count_errors_for_location(self, file_path: str, method: str,
                                   since: float = 0, until: float = 0) -> int:
        """统计指定文件（+方法）在 ``[since, until)`` 窗口内的日志错误数。

        用于修复效果对比：修复前统计基线，修复后统计剩余错误。

        ★主线第41批 T1（P0-263）修正 —— **原判据结构性恒 0**：
            旧实现要求**同一行同时出现**文件名 **和** 方法名；但框架日志格式为
            ``[模块名] 级别: 消息``（例：``[渠道] test-ch 调用失败: TimeoutError: ...``），
            **从不输出方法名** → 判据几乎永不成立。实测：56 个已应用补丁位置在
            全量日志中合计命中 **0** 条（放宽为"仅文件名"后为 **8** 条）。
            现按 ``EVOLUTION_BASELINE_MATCH_MODE`` 分档：
                · ``file_method``    —— 旧判据（文件名 + 方法名），向后兼容
                · ``file``（默认）   —— 仅文件名
                · ``file_or_method`` —— 文件名 或 方法名
            总开关 ``ENABLE_EVOLUTION_BASELINE_FIX`` 关闭时**行为与修复前完全一致**。

        Args:
            file_path: 源码路径（取 basename 匹配）。
            method:    方法名。
            since:     窗口起点（epoch 秒；0 = 不限）。
            until:     窗口终点（epoch 秒；0 = 不限）。
        # _m41_t1
        """
        if not file_path:
            return 0
        _fix_on = self._m41_baseline_fix_on()
        if not method and not _fix_on:
            return 0          # 修复前语义：两者皆空才算无效
        try:
            _log_file = os.path.join(self._project_root, "logs", "pulse.log")
            if not os.path.exists(_log_file):
                return 0
            _mode = self._m41_match_mode() if _fix_on else "file_method"
            _count = 0
            _file_basename = os.path.basename(file_path)
            with open(_log_file, encoding="utf-8", errors="ignore") as _f:
                for _line in _f:
                    # 解析时间戳
                    _ts_match = __import__("re").match(
                        r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", _line)
                    if _ts_match:
                        try:
                            _ts = __import__("time").mktime(
                                __import__("time").strptime(
                                    f"{_ts_match.group(1)} {_ts_match.group(2)}",
                                    "%Y-%m-%d %H:%M:%S"))
                            if since and _ts < since:
                                continue
                            if until and _ts > until:
                                continue
                        except Exception as e:
                            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                    # 匹配错误行：包含目标位置，且是 ERROR/CRITICAL/Traceback
                    # ★主线第30批 T2：ERROR/CRITICAL 改用真实级别标记判定
                    if (is_error_level_line(_line)
                            or "Traceback" in _line or "Exception" in _line):
                        if self._m41_line_matches(_line, _file_basename,
                                                  method, _mode):
                            _count += 1
            return _count
        except Exception as e:
            _module_logger.debug(
                "统计位置错误数失败（按 0 处理）: %s: %s", type(e).__name__, e)
            return 0

    # ==================== ★主线第41批 T1（P0-263）辅助 ====================

    @staticmethod
    def _m41_baseline_fix_on() -> bool:
        """是否启用「进化验证真实基线」修复（``ENABLE_EVOLUTION_BASELINE_FIX``）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_EVOLUTION_BASELINE_FIX", True))
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_m41_baseline_fix_on L2291")
            return True

    @staticmethod
    def _m41_match_mode() -> str:
        """日志匹配档位（``file_method`` / ``file`` / ``file_or_method``）。"""
        try:
            import config as _c
            _m = str(getattr(_c, "EVOLUTION_BASELINE_MATCH_MODE", "file") or "file")
        except Exception:
            _m = "file"
        return _m if _m in ("file_method", "file", "file_or_method") else "file"

    @staticmethod
    def _m41_line_matches(line: str, file_basename: str, method: str,
                          mode: str) -> bool:
        """单行是否命中目标位置（按档位）。"""
        _has_file = bool(file_basename) and file_basename in line
        _has_method = bool(method) and method in line
        if mode == "file_method":
            return _has_file and _has_method
        if mode == "file_or_method":
            return _has_file or _has_method
        return _has_file          # "file"（默认）

    def _m41_baseline_since(self) -> float:
        """基线统计窗口起点（默认修复前 N 天；0 = 全量历史，与修复前一致）。"""
        try:
            import config as _c
            _days = int(getattr(_c, "EVOLUTION_BASELINE_WINDOW_DAYS", 7) or 0)
        except Exception:
            _days = 7
        if not self._m41_baseline_fix_on():
            return 0.0
        return (time.time() - _days * 86400.0) if _days > 0 else 0.0

    def verify_fix_from_logs(self, patch: dict[str, Any]) -> dict[str, Any]:
        """★P1-2：运行时日志验证——分析补丁提交后的日志，确认修复是否有效。

        对比修复前后的错误数，计算修复效果评分。
        返回: {"verified": bool, "baseline": int, "after_fix": int,
               "effectiveness": float, "new_issues": int, "detail": str}
        """
        _file = patch.get("file", "")
        _method = patch.get("method", "")
        _fixed_at = patch.get("fixed_at", 0)
        _baseline = patch.get("baseline_errors", 0)

        if not _file or not _method or not _fixed_at:
            return {"verified": False, "baseline": 0, "after_fix": 0,
                    "post_apply_errors": 0,
                    "effectiveness": 0.0, "new_issues": 0,
                    "detail": "缺少修复时间戳或位置信息，无法验证"}

        # 修复后需要至少运行5分钟才有统计意义
        _elapsed = time.time() - _fixed_at
        if _elapsed < 300:
            return {"verified": False, "baseline": _baseline, "after_fix": 0,
                    "post_apply_errors": 0,
                    "effectiveness": 0.0, "new_issues": 0,
                    "detail": f"修复后仅运行{_elapsed:.0f}秒，需至少300秒才有统计意义"}

        _after = self._count_errors_for_location(_file, _method, since=_fixed_at)

        # 计算修复效果：错误减少率
        _undecidable = False
        if _baseline > 0:
            _effectiveness = max(0.0, min(1.0, 1.0 - _after / _baseline))
            _verified = _after == 0 or _effectiveness >= 0.8
            _detail = (f"修复前错误={_baseline}, 修复后错误={_after}, "
                       f"效果={_effectiveness:.0%}, 运行时长={_elapsed:.0f}秒")
        else:
            # ★T-99d：baseline_errors=0 时无错误基线可对比，无法判定修复效果
            # （不适用/无法验证）。此前会误判为 effectiveness=1.0 + verified=True
            # （假成功），污染修复率与平均效果统计。此处明确标记为无法验证，
            # 既不算修复成功，也不参与平均效果计算。
            _effectiveness = None
            _verified = False
            _undecidable = True
            _detail = (f"修复前错误=0(baseline_errors=0)，无错误基线可对比，"
                       f"无法判定修复效果（不适用）；修复后错误={_after}, "
                       f"运行时长={_elapsed:.0f}秒")

        _module_logger.info(
            f"[运行时验证] {os.path.basename(_file)}.{_method}: {_detail}"
            f" → {'验证通过' if _verified else '验证未通过/无法验证'}")

        return {
            "verified": _verified,
            "baseline": _baseline,
            "after_fix": _after,
            # ★第41批 T1（P0-263）：任务书要求写入 post_apply_errors（此前完全缺失）
            "post_apply_errors": _after,
            # ★T-99d：baseline=0 时为 None（无法验证），不参与平均效果计算
            "effectiveness": _effectiveness,
            "new_issues": _after,
            "detail": _detail,
            # ★第114批 T-114b①：baseline=0 无错误基线可对比 -> undecidable=True，
            #   调用方据此把补丁转入 needs_reverify（延迟复验）而非 runtime_failed，
            #   避免"无法验证"被误折叠为"修复失败"进而误回滚。
            "undecidable": _undecidable,
        }

    def _m84_recompute_split(self, patch: dict[str, Any]) -> bool:
        """★M84-3（第84批 T-84a）：运行时验证写回后重算 problem_fixed。

        背景（实测）：``patch_verification_split.apply_split`` 此前**只在补丁落盘到
        history 时**被调用一次（``PatchManager.apply_all_pending`` 内，L1960），那一刻
        ``post_apply_errors`` 尚未产生、``baseline_errors`` 多为 0 → ``problem_fixed``
        恒为 None（实测 63/64）→ ``real_fix_rate`` 恒 0.0%。

        本方法在运行时验证写回 ``runtime_verify_result`` / ``post_apply_errors``
        **之后**重算一次拆分字段，闭合"跑了但没写回"的断链。

        ★绝不改变验证/回滚流程与任何返回值；异常降级为 DEBUG（不阻断）。
        """
        try:
            if not isinstance(patch, dict):
                return False
            from nucleus.evolution.patch_verification_split import (
                apply_split as _apply_split84,
            )
            _apply_split84(patch)
            return True
        except Exception as _e84:
            _module_logger.debug(
                f"[语义拆分] 运行时写回重算失败（已忽略）: {type(_e84).__name__}: {_e84}")
            return False

    def _m84_patch_ledger(self) -> dict[str, Any]:
        """★M84-4（第84批 T-84b）：补丁账本分层计数（**只读**，不改任何状态）。

        把此前被混为一谈的三个口径分开暴露：
            * ``applied_total``        —— 已落盘到活代码的补丁数
            * ``problem_fixed_true``   —— 语义拆分判定「问题真消失」的补丁数
            * ``problem_fixed_known``  —— 可判定的补丁数（problem_fixed 非 None）
            * ``history_total``        —— 补丁历史总条数

        动机：``_repaired`` 实为「提交待审批数」（见 L1582），却与"修复数"同名，
        下游（含只读诊断）据此误判过一轮。本方法只读汇总，供日志与埋点分层呈现。
        异常一律返回空 dict（调用方跳过附加字段）。
        """
        try:
            _hist = self._patch_manager.load_json(
                self._patch_manager.get_history_file(), [])
            if not isinstance(_hist, list):
                return {}
            _applied = sum(1 for _p in _hist
                           if isinstance(_p, dict) and _p.get("applied"))
            _pf_true = sum(1 for _p in _hist
                           if isinstance(_p, dict) and _p.get("problem_fixed") is True)
            _pf_known = sum(1 for _p in _hist
                            if isinstance(_p, dict) and _p.get("problem_fixed") is not None)
            return {"history_total": len(_hist), "applied_total": _applied,
                    "problem_fixed_true": _pf_true,
                    "problem_fixed_known": _pf_known}
        except Exception as _e84:
            _module_logger.debug(
                f"[补丁账本] 分层计数失败（已忽略）: {type(_e84).__name__}: {_e84}")
            return {}

    def verify_submitted_patches(self) -> dict[str, Any]:
        """★P1-2：批量验证所有已提交补丁的运行时修复效果。

        遍历待审批补丁队列，对标记needs_runtime_verify的补丁进行日志验证，
        验证结果记录到补丁元数据和验证学习枢纽。
        """
        try:
            _pending = self._patch_manager.load_json(
                self._patch_manager.get_pending_file(), [])
            if not _pending:
                return {"total": 0, "verified": 0, "failed": 0,
                        "skipped": 0, "avg_effectiveness": 0.0}

            _total = 0
            _verified = 0
            _failed = 0
            _skipped = 0
            _effects = []
            _reverified = 0
            _reverify_failed = 0
            _needs_reverify = 0
            _reverify_delay = 3600  # 延迟复验窗口（秒）：到点后重采基线

            # ★第114批 T-114b①：延迟复验前置——每轮开头对到点(>=reverify_after)的
            #   needs_reverify 补丁重采基线(重数修复后错误)，据实判定，避免 baseline=0
            #   的"不可判定"被永久折叠进 runtime_failed + 回滚。未到点者跳过，等下轮。
            _now = time.time()
            for _patch in _pending:
                if _patch.get("status") != "needs_reverify":
                    continue
                _ra = _patch.get("reverify_after", 0) or 0
                if _ra and _now < _ra:
                    continue
                _rfile = _patch.get("file", "")
                _rmethod = _patch.get("method", "")
                _rfixed = _patch.get("fixed_at", 0) or 0
                _rafter = self._count_errors_for_location(
                    _rfile, _rmethod, since=_rfixed) if _rfixed else 0
                _rverdict = {
                    "verified": _rafter == 0,
                    "baseline": 0,
                    "after_fix": _rafter,
                    "post_apply_errors": _rafter,
                    "effectiveness": 1.0 if _rafter == 0 else 0.0,
                    "new_issues": _rafter,
                    "detail": (f"[延迟复验] 重采基线: 修复后错误={_rafter}, "
                               f'{"通过" if _rafter == 0 else "未通过"}'),
                    "undecidable": False,
                }
                _patch["runtime_verify_result"] = _rverdict
                # ★第116批 T-116b：顶层 runtime_verified 取嵌套真值（原恒 True，
                #   与 runtime_verify_result.verified=False 背离 ⇒ C6 污染）。
                _patch["runtime_verified"] = bool(_rverdict.get("verified"))
                _patch.pop("reverify_after", None)
                _patch["reverify_count"] = int(_patch.get("reverify_count", 0)) + 1
                self._m84_recompute_split(_patch)
                self._learn_from_verification(_patch, _rverdict)
                if _rafter == 0:
                    _reverified += 1
                    _patch["status"] = "runtime_verified"
                else:
                    _reverify_failed += 1
                    _patch["status"] = "runtime_failed"
                    _patch["needs_repair"] = True
                    if _patch.get("applied"):
                        try:
                            _rb = self._patch_manager.rollback_patch(
                                _patch.get("id", ""))
                            if _rb.get("ok"):
                                _patch["rolled_back"] = True
                                _patch["rolled_back_at"] = time.time()
                                _module_logger.warning(
                                    f"[延迟复验] 失败补丁已自动回滚: "
                                    f"{os.path.basename(_rfile)}.{_rmethod} "
                                    f"({_rverdict.get('detail','')})")
                            else:
                                _module_logger.warning(
                                    f"[延迟复验] 失败补丁回滚失败: "
                                    f"{os.path.basename(_rfile)}.{_rmethod} "
                                    f"reason={_rb.get('reason','')}")
                        except Exception as _rb_e:
                            _module_logger.error(
                                f"[延迟复验] 失败补丁回滚异常: {_rb_e}")
                _module_logger.info(
                    f"[延迟复验] 到点重采基线: {os.path.basename(_rfile)}.{_rmethod} "
                    f'-> {"runtime_verified" if _rafter == 0 else "runtime_failed"}')

            for _patch in _pending:
                if not _patch.get("needs_runtime_verify"):
                    continue
                # ★第116批 T-116b：runtime_verified 语义改为"验证通过"，故跳过判据
                #   补 OR runtime_verify_result 存在（=已验过，无论通过与否），保持原有
                #   "已验证过即跳过、失败补丁不每轮重试"的行为不变。
                if _patch.get("runtime_verified") or _patch.get("runtime_verify_result") is not None:
                    # ★M84-3（第84批 T-84a）：历史已验补丁补算 problem_fixed——
                    #   此前本分支直接 continue，使旧补丁永久停在 None（实测 4 条
                    #   PulseKidney 补丁 baseline>0 效果 100% 却无该字段）。
                    self._m84_recompute_split(_patch)
                    continue  # 已验证过，跳过
                if _patch.get("status") == "needs_reverify":
                    continue  # ★第114批 T-114b①：由延迟复验前置处理，避免重复判定
                _total += 1

                _result = self.verify_fix_from_logs(_patch)
                _patch["runtime_verify_result"] = _result
                # ★第116批 T-116b：顶层 runtime_verified 不再无条件置 True（C6 污染根因），
                #   改为按判定结果落到下方"通过/失败/不可判定"三个分支中。
                # ★第105批 T-105a：同步顶层 baseline_errors（供放行判据读取，
                #   避免仅依赖嵌套字段），回退 runtime_verify_result.baseline。
                if not isinstance(_patch.get("baseline_errors"), (int, float)) or _patch.get("baseline_errors") <= 0:
                    _b = (_result or {}).get("baseline")
                    if isinstance(_b, (int, float)) and _b > 0:
                        _patch["baseline_errors"] = _b
                # ★第41批 T1（P0-263）：回写应用后错误数（任务书要求，此前缺失）
                _patch["post_apply_errors"] = _result.get(
                    "post_apply_errors", _result.get("after_fix", 0))
                # ★M84-3（第84批 T-84a）：baseline + post_apply_errors 均已齐备 →
                #   重算语义拆分，把 problem_fixed 从 None 落到 True/False。
                self._m84_recompute_split(_patch)

                if _result.get("undecidable"):
                    # ★第114批 T-114b①：baseline=0 不可判定 -> 延迟复验，
                    #   不折叠进 runtime_failed（避免误回滚/误标 needs_repair）。
                    _patch["runtime_verified"] = False
                    _needs_reverify += 1
                    _patch["status"] = "needs_reverify"
                    _patch["reverify_after"] = time.time() + _reverify_delay
                    _patch["reverify_count"] = int(_patch.get("reverify_count", 0)) + 1
                    _ufile = _patch.get("file", "")
                    _umethod = _patch.get("method", "")
                    _module_logger.info(
                        f"[延迟复验] baseline=0 无法判定，延迟到 "
                        f"{_patch['reverify_after']:.0f} 重采基线: "
                        f"{os.path.basename(_ufile)}.{_umethod}")
                    continue
                if _result["verified"]:
                    _verified += 1
                    _patch["status"] = "runtime_verified"
                    _patch["runtime_verified"] = True
                else:
                    _failed += 1
                    _patch["status"] = "runtime_failed"
                    _patch["runtime_verified"] = False
                    _patch["needs_repair"] = True  # 标记需要重新修复
                    # ★A2修复（主线A）：运行时验证失败且补丁已应用到源码时，
                    #   自动回滚，避免「失败的修复」持续留在活代码里造成损害。
                    #   此前只标记 runtime_failed + needs_repair，但已写入的
                    #   源码改动未撤销，与「自主进化可信」原则相悖。
                    _file = _patch.get("file", "")
                    _method = _patch.get("method", "")
                    if _patch.get("applied"):
                        try:
                            _rb = self._patch_manager.rollback_patch(
                                _patch.get("id", ""))
                            if _rb.get("ok"):
                                _patch["rolled_back"] = True
                                _patch["rolled_back_at"] = time.time()
                                _module_logger.warning(
                                    f"[运行时验证] 失败补丁已自动回滚: "
                                    f"{os.path.basename(_file)}.{_method} "
                                    f"({_result.get('detail','')})")
                            else:
                                _module_logger.warning(
                                    f"[运行时验证] 失败补丁回滚失败: "
                                    f"{os.path.basename(_file)}.{_method} "
                                    f"reason={_rb.get('reason','')}")
                        except Exception as _rb_e:
                            _module_logger.error(
                                f"[运行时验证] 失败补丁回滚异常: {_rb_e}")

                # ★第四阶段：从验证结果中学习
                self._learn_from_verification(_patch, _result)

                _eff = _result.get("effectiveness")
                if _eff is not None:
                    _effects.append(_eff)

            # ★第105批 T-105a（P0）：补回边——验证写回后复查并放行 T-101a 合格补丁。
            #   旧逻辑仅在「入队时」调用放行判据（彼时 runtime_verify_result 尚不存在
            #   ⇒ 判据恒假），验证写回后又无人复查 ⇒ 闭环断裂（实测 30 条 rv=True 仍 0 放行）。
            #   此处遍历 status∈{pending, runtime_verified} 的条目，对满足 T-101a 判据
            #   （已改读嵌套 runtime_verify_result.verified + baseline_errors>0）者置 approved。
            _released = 0
            for _patch in _pending:
                if str(_patch.get("status", "")) not in ("pending", "runtime_verified"):
                    continue
                if self._patch_manager._m105_try_release_low_risk(_patch):
                    _released += 1
            if _released:
                _module_logger.info(
                    f"[运行时验证] T-105a 补回边：验证后复查放行 {_released} 条 T-101a 合格补丁")

            # 保存更新后的补丁队列
            self._patch_manager._save_json(
                self._patch_manager.get_pending_file(), _pending)

            _avg = sum(_effects) / len(_effects) if _effects else 0.0

            _module_logger.info(
                f"[运行时验证] 批量验证完成: 总数={_total}, "
                f"通过={_verified}, 失败={_failed}, 平均效果={_avg:.0%}, "
                f"延迟复验新入队={_needs_reverify}, 到点复核通过={_reverified}, "
                f"到点复核失败={_reverify_failed}")

            # 记录到验证学习枢纽
            try:
                from nucleus.mnemosyne.verification_learning_hub import (
                    get_verification_learning_hub,
                )
                _hub = get_verification_learning_hub()
                _hub.record(
                    organ="evolution_executor",
                    task_type="runtime_fix_verification",
                    input_summary=f"批量验证{_total}个补丁",
                    local_result={"verified": _verified, "failed": _failed},
                    confidence=0.8,
                    relevance_score=0.9,
                    needs_verification=False,
                    verification_result={"avg_effectiveness": _avg},
                    lesson=f"运行时验证: {_verified}/{_total}通过, 平均效果{_avg:.0%}",
                )
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            return {
                "total": _total,
                "verified": _verified,
                "failed": _failed,
                "skipped": _skipped,
                "needs_reverify": _needs_reverify,
                "reverified": _reverified,
                "reverify_failed": _reverify_failed,
                "avg_effectiveness": round(_avg, 2),
            }
        except Exception as _e:
            _module_logger.error(f"[运行时验证] 批量验证异常: {_e}")
            return {"total": 0, "verified": 0, "failed": 0,
                    "skipped": 0, "avg_effectiveness": 0.0, "error": str(_e)}

    def verify_applied_patches(self) -> dict[str, Any]:
        """★A2修复（主线A）：验证「已应用补丁」的运行时效果，失败则自动回滚。

        背景（与 verify_submitted_patches 的语义分工）：
            verify_submitted_patches 遍历的是 pending（待审批）队列，
            此时补丁 applied=False（尚未应用），它验证的是「提交后」的日志，
            而非「应用后」的效果。真正决定「修复是否有效」的，是补丁
            apply_all_pending 应用进活代码之后，错误是否真的消失。

        本方法遍历 history 中「已应用且仍标记 needs_runtime_verify」的补丁：
            - 对比应用后的错误数（since=applied_at）与 baseline_errors
            - 错误未降反升 → 自动回滚（rollback_patch），撤销失败的修复
            - 错误已消失/显著下降 → 标记 runtime_verified，闭环完成
        """
        try:
            _history = self._patch_manager.load_json(
                self._patch_manager.get_history_file(), [])
            if not _history:
                return {"total": 0, "verified": 0, "failed": 0,
                        "rolled_back": 0, "avg_effectiveness": 0.0}

            _total = 0
            _verified = 0
            _failed = 0
            _rolled_back = 0
            _effects = []

            for _patch in _history:
                # 只验证「已应用 + 待运行时验证 + 尚未运行时验证过」的补丁
                if not _patch.get("applied"):
                    continue
                if not _patch.get("needs_runtime_verify"):
                    continue
                # ★第116批 T-116b：同上，跳过判据补 OR runtime_verify_result 存在。
                if _patch.get("runtime_verified") or _patch.get("runtime_verify_result") is not None:
                    # ★M84-3（第84批 T-84a）：已验补丁补算 problem_fixed（与
                    #   verify_submitted_patches 同源处置）。
                    self._m84_recompute_split(_patch)
                    continue
                _total += 1

                _file = _patch.get("file", "")
                _method = _patch.get("method", "")
                _applied_at = _patch.get("applied_at", 0)
                _baseline = _patch.get("baseline_errors", 0)

                if not _file or not _method or not _applied_at:
                    # ★第116批 T-116b：原恒置 True 与嵌套 verified=False 背离（C6 污染）。
                    _patch["runtime_verified"] = False
                    _patch["runtime_verify_result"] = {
                        "verified": False, "detail": "缺位置/时间戳，跳过运行时验证"}
                    continue

                # 应用后需至少运行 300 秒才有统计意义
                _elapsed = time.time() - _applied_at
                if _elapsed < 300:
                    continue  # 运行时间不足，本轮跳过，留待下轮

                _after = self._count_errors_for_location(
                    _file, _method, since=_applied_at)
                if _baseline > 0:
                    _effectiveness = max(0.0, min(1.0, 1.0 - _after / _baseline))
                    _verified_ok = _after == 0 or _effectiveness >= 0.8
                    _detail = (f"应用后错误={_after}, 基线={_baseline}, "
                               f"效果={_effectiveness:.0%}, 运行{_elapsed:.0f}秒")
                else:
                    # ★T-99d：baseline=0 无法判定修复效果（不适用/无法验证），
                    # 不算修复成功，effectiveness 记为 None（不参与平均）。
                    _effectiveness = None
                    _verified_ok = False
                    _detail = (f"应用后错误={_after}, 基线=0(baseline_errors=0)，"
                               f"无错误基线可对比，无法判定修复效果（不适用），"
                               f"运行{_elapsed:.0f}秒")

                # ★第116批 T-116b：顶层取实测真值 _verified_ok（原恒 True ⇒ C6 污染）。
                _patch["runtime_verified"] = bool(_verified_ok)
                _patch["runtime_verify_result"] = {
                    "verified": _verified_ok,
                    "baseline": _baseline,
                    "after_fix": _after,
                    # ★T-99d：baseline=0 时为 None（无法验证），不参与平均
                    "effectiveness": _effectiveness,
                    "detail": _detail,
                }
                # ★M84-3（第84批 T-84a）：应用后计数已实测 → 回写顶层 post_apply_errors
                #   并重算语义拆分（此前只写 runtime_verify_result，problem_fixed 不更新）。
                _patch["post_apply_errors"] = _after
                self._m84_recompute_split(_patch)

                if _verified_ok:
                    _verified += 1
                    _patch["status"] = "runtime_verified"
                    _patch["needs_runtime_verify"] = False  # 闭环完成
                else:
                    _failed += 1
                    _patch["status"] = "runtime_failed"
                    _patch["needs_repair"] = True
                    # ★A2核心：失败且已应用 → 自动回滚，撤销失败修复
                    try:
                        _rb = self._patch_manager.rollback_patch(
                            _patch.get("id", ""))
                        if _rb.get("ok"):
                            _rolled_back += 1
                            _patch["rolled_back"] = True
                            _patch["rolled_back_at"] = time.time()
                            _patch["needs_runtime_verify"] = False  # 已回滚，无需再验
                            _module_logger.warning(
                                f"[A2运行时验证] 失败补丁已自动回滚: "
                                f"{os.path.basename(_file)}.{_method} "
                                f"({_patch['runtime_verify_result']['detail']})")
                        else:
                            _module_logger.warning(
                                f"[A2运行时验证] 失败补丁回滚失败: "
                                f"{os.path.basename(_file)}.{_method} "
                                f"reason={_rb.get('reason','')}")
                    except Exception as _rb_e:
                        _module_logger.error(
                            f"[A2运行时验证] 失败补丁回滚异常: {_rb_e}")

                if _effectiveness is not None:
                    _effects.append(_effectiveness)

            # 保存更新后的 history
            self._patch_manager._save_json(
                self._patch_manager.get_history_file(), _history)

            _avg = sum(_effects) / len(_effects) if _effects else 0.0
            if _total > 0:
                _module_logger.info(
                    f"[A2运行时验证] 已应用补丁验证完成: 总数={_total}, "
                    f"通过={_verified}, 失败={_failed}, 自动回滚={_rolled_back}, "
                    f"平均效果={_avg:.0%}")

            return {
                "total": _total,
                "verified": _verified,
                "failed": _failed,
                "rolled_back": _rolled_back,
                "avg_effectiveness": round(_avg, 2),
            }
        except Exception as _e:
            _module_logger.error(f"[A2运行时验证] 已应用补丁验证异常: {_e}")
            return {"total": 0, "verified": 0, "failed": 0,
                    "rolled_back": 0, "avg_effectiveness": 0.0, "error": str(_e)}

    def _learn_from_verification(self, patch: dict[str, Any],
                                   verify_result: dict[str, Any]) -> None:
        """★第四阶段：从单个补丁的验证结果中提取经验。

        记录成功/失败模式，用于未来的修复决策优化。
        """
        try:
            _strategy = patch.get("applied_strategy", "unknown")
            _type = patch.get("type", patch.get("issue_type", "unknown"))
            _success = verify_result.get("verified", False)
            _eff_raw = verify_result.get("effectiveness")
            _effectiveness = 0.0 if _eff_raw is None else float(_eff_raw)

            # 记录策略效果统计
            if not hasattr(self, "_strategy_stats"):
                self._strategy_stats = {}
            if _strategy not in self._strategy_stats:
                self._strategy_stats[_strategy] = {
                    "total": 0, "success": 0, "fail": 0,
                    "total_effectiveness": 0.0,
                }
            _stat = self._strategy_stats[_strategy]
            _stat["total"] += 1
            _stat["total_effectiveness"] += _effectiveness
            if _success:
                _stat["success"] += 1
            else:
                _stat["fail"] += 1

            # 记录失败原因模式
            if not _success:
                _fail_reason = "unknown"
                if verify_result.get("after_fix", 0) > 0:
                    _fail_reason = "错误仍存在"
                elif verify_result.get("new_issues", 0) > 0:
                    _fail_reason = "引入新问题"
                else:
                    _fail_reason = "效果不明显"

                if not hasattr(self, "_failure_patterns"):
                    self._failure_patterns = {}
                _key = f"{_type}_{_strategy}_{_fail_reason}"
                self._failure_patterns[_key] = self._failure_patterns.get(_key, 0) + 1

                # ★第114批 T-114a（断2）：验证失败（本地/LLM 统一学习入口）登记冷却。
                #   补丁指纹 best-effort 构造，兜底覆盖 LLM 路径；本地路径已在修复落点精确登记。
                # ★第116批 T-116a③：双指纹格式统一 —— 原此处为 organ|type|desc 第二格式，
                #   与本地路径 _cooldown_key（file|method|type）并存 ⇒ 同题两路=两键、冷却被稀释。
                #   统一为 _cooldown_key(patch)（file|method|type，与断2 同构）。
                _fp = self._cooldown_key(patch)
                self._m114a_register_verify_failure(_fp, detail=str(_fail_reason))
                _module_logger.info(
                    f"[经验学习] 失败模式记录: type={_type}, strategy={_strategy}, "
                    f"reason={_fail_reason}, effectiveness={_effectiveness:.0%}"
                )
            else:
                _module_logger.info(
                    f"[经验学习] 成功模式记录: type={_type}, strategy={_strategy}, "
                    f"effectiveness={_effectiveness:.0%}"
                )
                # ★第117批 T-117b：补丁验证通过 —— 出清同指纹连败计数。
                #   与上方失败分支的 _m114a_register_verify_failure 严格对称：
                #   此处是「本地/LLM 统一学习入口」的成功侧，一处覆盖全部补丁路径
                #   （含延迟复验 :2461 与主验证循环 :2576 两处调用）。
                self._m114a_clear_ratchet(
                    self._cooldown_key(patch), reason="补丁验证通过")

            # 记录到验证学习枢纽
            try:
                from nucleus.mnemosyne.verification_learning_hub import (
                    get_verification_learning_hub,
                )
                _hub = get_verification_learning_hub()
                _hub.record(
                    organ="evolution_executor",
                    task_type="patch_verification_learning",
                    input_summary=f"{_type}/{_strategy}",
                    local_result={"success": _success, "effectiveness": _effectiveness},
                    confidence=_evidence_conf(0.9, "evolution", verify_result),
                    relevance_score=0.8,
                    needs_verification=False,
                    verification_result=verify_result,
                    lesson=f"{'成功' if _success else '失败'}: {_type}使用{_strategy}策略, "
                           f"效果{_effectiveness:.0%}",
                )
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        except Exception as _e:
            _module_logger.debug(f"经验学习记录异常: {_e}")

    def get_strategy_learning_stats(self) -> dict[str, Any]:
        """★第四阶段：获取策略学习统计。

        返回各修复策略的成功率、平均效果、失败模式等。
        """
        if not hasattr(self, "_strategy_stats") or not self._strategy_stats:
            return {"status": "no_data", "strategies": {}}

        _result = {}
        for _strategy, _stat in self._strategy_stats.items():
            _total = _stat["total"]
            _success_rate = _stat["success"] / _total if _total > 0 else 0
            _avg_effect = _stat["total_effectiveness"] / _total if _total > 0 else 0
            _result[_strategy] = {
                "total": _total,
                "success": _stat["success"],
                "fail": _stat["fail"],
                "success_rate": round(_success_rate, 2),
                "avg_effectiveness": round(_avg_effect, 2),
            }

        return {
            "status": "ok",
            "total_strategies": len(_result),
            "strategies": _result,
            "failure_patterns": getattr(self, "_failure_patterns", {}),
        }

    def _strip_code_fence(self, text: str) -> str:
        """清理 LLM 输出的 markdown 代码块标记（```python ... ```）。"""
        if not text:
            return ""
        import re as _re
        _t = text.strip()
        _t = _re.sub(r'^```(?:python|py)?\s*\n?', '', _t)
        _t = _re.sub(r'\n?```\s*$', '', _t)
        return _t.strip()

    # ★PHASE17-A2（2026-09-07）：LLM 补丁全角字符归一化。
    #   运行实证：17:06:39 补丁完整性检查失败: 语法错误: invalid character '，' (U+FF0C) (<unknown>, line 4)
    #   LLM 生成的 Python 代码常混入全角标点，导致语法错误 → 验证失败 → 补丁丢弃。
    #   框架虽有「自我反思」记录根因，但无法自我修正，同一个坑反复踩（P0-10）。
    #
    #   设计取舍（最小侵入 + 零副作用）：
    #   1) 只在 ast.parse 失败后才归一化 —— 合法代码一个字符都不动；
    #   2) 归一化只作用于「代码区」，跳过字符串字面量与 # 注释，
    #      避免把中文字符串/注释里的「，」误改成半角（保语义、保可读性）；
    #   3) 归一化后再次 ast.parse，仅当真的修好了才记 INFO 日志。
    _FULLWIDTH_CODE_MAP = {
        "，": ",",   # 全角逗号（实证最高频）
        "（": "(", "）": ")",
        "：": ":", "；": ";",
        "？": "?", "！": "!",
        "［": "[", "］": "]",
        "｛": "{", "｝": "}",
        "＝": "=", "　": " ",   # 全角等号 / 全角空格
        "＋": "+", "－": "-", "＊": "*", "／": "/",
        "＜": "<", "＞": ">", "％": "%", "＆": "&", "｜": "|",
        "\u201c": '"', "\u201d": '"',   # 中文左右双引号
        "\u2018": "'", "\u2019": "'",   # 中文左右单引号
        # ★主线第15批 T5/P2-96：补齐实测最高频的三个漏网字符
        #   （补丁语法错误 22 次中：「 10 次 / 。 6 次 / → 1 次，此前均不在映射表内）
        "「": '"', "」": '"',             # 直角引号（实证最高频）
        "『": '"', "』": '"',
        "。": ".",                        # 中文句号（实证第 2 高频）
        "、": ",",                        # 中文顿号
        "→": "->", "←": "<-",             # 箭头（实证出现过）
        "《": "<", "》": ">",
        "【": "[", "】": "]",
        "…": "...", "～": "~", "·": ".",
        "―": "-",
    }

    def _normalize_fullwidth_in_code(self, code: str) -> tuple[str, int]:
        """把「代码区」（非字符串字面量、非注释）的全角标点归一化为半角。

        Returns:
            (归一化后的代码, 替换次数)
        """
        if not code:
            return "", 0
        _out: list[str] = []
        _count = 0
        _i = 0
        _n = len(code)
        while _i < _n:
            _ch = code[_i]
            # 三引号字符串：整体跳过（原样保留）
            if code.startswith('"""', _i) or code.startswith("'''", _i):
                _q = code[_i:_i + 3]
                _j = code.find(_q, _i + 3)
                if _j == -1:
                    _out.append(code[_i:])
                    break
                _out.append(code[_i:_j + 3])
                _i = _j + 3
                continue
            # 单行字符串：整体跳过（原样保留）
            if _ch in ('"', "'"):
                _j = _i + 1
                while _j < _n:
                    if code[_j] == '\\':
                        _j += 2
                        continue
                    if code[_j] == _ch or code[_j] == '\n':
                        break
                    _j += 1
                if _j >= _n:
                    _out.append(code[_i:])
                    break
                _out.append(code[_i:_j + 1])
                _i = _j + 1
                continue
            # 注释：整体跳过（保留中文可读性）
            if _ch == '#':
                _j = code.find('\n', _i)
                if _j == -1:
                    _out.append(code[_i:])
                    break
                _out.append(code[_i:_j])
                _i = _j
                continue
            # 代码区：归一化
            _rep = self._FULLWIDTH_CODE_MAP.get(_ch)
            if _rep is not None:
                _out.append(_rep)
                _count += 1
                _i += 1
                continue
            # ★主线第15批 T5/P2-96：映射表之外的**非 ASCII 标点**再做两级兜底：
            #   ① NFKC 归一化后若得到 ASCII，则采用（如 ﹣→- 、﹕→: 等兼容字符）；
            #   ② 仍得不到 ASCII 且属「标点/符号」类，则直接剔除
            #      （代码区出现中文标点一定是笔误；字符串/注释区已在上面整体跳过）。
            if ord(_ch) > 127:
                import unicodedata as _ud
                _nk = _ud.normalize("NFKC", _ch)
                if _nk and _nk != _ch and all(ord(c) < 128 for c in _nk):
                    _out.append(_nk)
                    _count += 1
                    _i += 1
                    continue
                if _ud.category(_ch).startswith("P") or _ud.category(_ch) == "Sm":
                    _count += 1
                    _i += 1
                    continue
            _out.append(_ch)
            _i += 1
        return "".join(_out), _count

    # ========== ★第91批 T-91b：LLM 补丁缩进契约 ==========

    _M91_INDENT_UNIT = 4
    _M91_REPAIR_ROUNDS = 8

    @staticmethod
    def _m91_base_indent(code: str) -> int:
        """代码片段的**基础缩进宽度** = 首个**可执行行**的前导空白宽度（tab 按 4 展开）。

        为什么它是要害：`PatchManager._verify_in_copy` 用
        `full_content.replace(original_code, modified_code)` **原位整段替换**，
        对此后的缩进**一字不改** ⇒ modified 的基础缩进必须与 original 一致。
        不一致时替换结果**仍是合法 Python**，却会把**类体/函数体提前终止**。
        2026-09-20 实测（星轨 T-91d 自动应用 LLM 补丁，三关 + py_compile + import 全放行）：
          PulseInnerWorld.py 类方法 367 → 270（-97）
          PulseLung.py       类方法  86 →  45（-41）

        ★为什么必须跳过**空行与纯注释行**（第91批 T0 实测，真实补丁取证）：
        LLM 常见形态是「在 col 0 前置模块级注释/import，再保留原有方法体」，例如
        patch_llm_1789462016_8789 首行是 col 0 的注释、其后方法体仍在 8；
        Python 词法器**不把注释当作缩进层级** ⇒ 首行注释落在 col 0 **无害**。
        若按「首个非空行」算会把 base 误判为 0，触发**多余的整体平移**
        （把注释与整个方法体一起推深），改变补丁内容却没有必要。
        同理 patch_llm_1789886195_fb44（PulseInterestModel）是纯注释前置，
        实测其结构损伤仅为「注释缩进退化」（无害），与此判据一致。
        """
        if not code:
            return -1
        for _ln in str(code).split("\n"):
            _s = _ln.strip()
            if not _s or _s.startswith("#"):
                continue
            return len(_ln[: len(_ln) - len(_ln.lstrip(" \t"))].expandtabs(4))
        return -1

    @staticmethod
    def _m91_indent_levels(lines, unit: int = 4) -> set:
        """给定若干行，返回其中**代码行**已出现过的缩进宽度集合（tab 按 unit 展开）。

        跳空行与纯注释行，口径与 `_m91_base_indent` 一致（注释不构成缩进层级）。
        """
        _out = set()
        for _ln in lines:
            _b = _ln.lstrip(" \t")
            if not _b or _b.startswith("#"):
                continue
            _out.add(len(_ln[: len(_ln) - len(_b)].expandtabs(unit)))
        return _out

    def _m91_repair_indentation(self, code: str) -> str:
        """把「缩进漂移」修复为结构自洽（**只改 Python 实际报错那一行的行首空白**）。

        契约（三条都是可断言的行为，见 tests/test_llm_indent_contract_m91.py）：
          1) 输入若能通过「关2 同口径」判据 ⇒ **逐字原样返回**（零误伤，构造上成立）；
          2) 返回值**要么与输入逐字相同，要么能通过关2 同口径判据**（无中间态）；
          3) 只改「行首空白」，不改任何行的 `strip()` 后内容。

        算法：反复取关2 同口径解析的**首个错误行号** → 用 `SyntaxError.lineno` 定位原始行
        （包裹文本第 1 行是 `def _wrap():`，其后第 k 行对应内容第 k-1 行；再补偿
        `strip()` 去掉的行前空行数）→ 该行缩进吸附到「本行之前已出现过的合法层级」
        （**等距取更深者**：`def f():`(4) / `x=1`(8) / `return x`(6) 中 6 应吸附 8，
        否则 `return` 落到函数外）；每轮只在**能过关2 判据**时采纳，最多 8 轮。

        ★为什么**不能**改成「逐行试吸附」（第91批 T0 实测教训）：
        逐行试吸附会重排**多行字符串内部**的行 —— SafeEvolutionExecutor 内实测
        **35 个方法**的 docstring 被压到 col 0（字符串内容被静默改写）。
        行定位法从构造上排除该风险：字符串行**永远不会**触发缩进错误，
        故被修改的行必然是代码行。同时「输入已过关2 则逐字不动」也一并成立。
        """
        if not code:
            return code
        _unit = self._M91_INDENT_UNIT
        _cur = str(code)
        for _ in range(self._M91_REPAIR_ROUNDS):
            _g2_ok, _g2_err, _g2_line = _m91_gate2_parse(_cur)
            if _g2_ok:
                break
            if _g2_line <= 1:
                break                      # 非「可定位到内容行」的错误（如非法字符）
            _lines = _cur.split("\n")
            _lead = 0
            while _lead < len(_lines) and not _lines[_lead].strip():
                _lead += 1
            _i = _lead + _g2_line - 2      # 包裹行号 → 原始行下标
            if not (0 <= _i < len(_lines)):
                break
            _body = _lines[_i].lstrip(" \t")
            if not _body or _body.startswith("#"):
                break
            _raw = _lines[_i][: len(_lines[_i]) - len(_body)]
            _w = len(_raw.expandtabs(_unit))
            _cands = set(self._m91_indent_levels(_lines[:_i], _unit))
            _cands.add((_w // _unit) * _unit)
            _cands.add(((_w // _unit) + 1) * _unit)
            _next = None
            for _c in sorted(_cands, key=lambda _c: (abs(_c - _w), -_c)):
                if _c == _w and _raw == " " * _w:
                    continue
                _trial = list(_lines)
                _trial[_i] = " " * _c + _body
                _trial_s = "\n".join(_trial)
                if _m91_gate2_check(_trial_s)[0]:
                    _next = _trial_s
                    break
            if _next is None and _g2_err.startswith("TabError"):
                # ★TabError 的报错行**可能落在 tab 行的下一行**：词法器要等到下一次
                #   INDENT/DEDENT 比较时才察觉 tab/空格混用（T0 实测：tab 在第 3 行、
                #   报错行却是第 4 行）⇒ 行定位修不掉。兜底为「行首 tab 按 4 列展开」：
                #   只改行首空白、不触碰任何 token，且仍要求关2 同口径自检通过才采纳。
                _tabs = "\n".join(
                    (" " * len(_ln[: len(_ln) - len(_ln.lstrip(" \t"))].expandtabs(_unit))
                     + _ln.lstrip(" \t")) if _ln.strip() else _ln
                    for _ln in _lines)
                if _tabs != _cur and _m91_gate2_check(_tabs)[0]:
                    _next = _tabs
            if _next is None:
                break
            _module_logger.debug(
                "[LLM缩进修复] 第%d行缩进 %d -> 修复，关2 同口径自检通过（原报错: %s）",
                _i + 1, _w, _g2_err)
            _cur = _next
        if _m91_gate2_check(_cur)[0]:
            return _cur
        return code

    def _m91_align_base_indent(self, original_code: str, modified_code: str,
                               context: str = "") -> tuple:
        """把 modified 的**基础缩进**整体平移到与 original 同级；返回 (代码, 位移)。

        · 均匀平移只给每个非空行加同一前缀 ⇒ 相对缩进结构与全部 token 守恒；
        · 位移为 0（或开关关闭）→ 原样返回，零副作用；
        · 平移后用**关2 同口径**判据复验，不通过则放弃（宁可交给验证关以明确理由拒绝，
          也不引入新的破损）；
        · 反向（modified 更深）不处理：裁剪缩进可能把行裁空，风险高于收益，
          交给验证关以明确理由拒绝。
        · 改变了补丁内容就必须留痕（本项目「伪静默」铁律）⇒ 内部 info 日志。
        """
        if not _m91_indent_repair_on() or not original_code or not modified_code:
            return modified_code, 0
        _bo = self._m91_base_indent(original_code)
        _bm = self._m91_base_indent(modified_code)
        if _bo < 0 or _bm < 0 or _bo <= _bm:
            return modified_code, 0
        _delta = _bo - _bm
        _pad = " " * _delta
        _fixed = "\n".join(
            (_pad + _ln) if _ln.strip() else _ln
            for _ln in str(modified_code).split("\n"))
        _g2_ok_a, _g2_err_a, _ = _m91_gate2_parse(_fixed)
        if not _g2_ok_a:
            _module_logger.debug(
                "[LLM缩进对齐] 放弃对齐（平移后关2 同口径判据仍未通过: %s）%s",
                _g2_err_a, context)
            return modified_code, 0
        _module_logger.info(
            "[LLM缩进对齐] 基础缩进 %d -> %d（位移 %+d 空格），已消除"
            "「类体/函数体被提前终止」风险 %s", _bm, _bo, _delta, context)
        return _fixed, _delta

    def _clean_llm_code(self, text: str) -> str:
        """LLM 输出代码清理：去 markdown 围栏 +（必要时）全角标点归一化。

        ★PHASE17-A2：替代直接调用 _strip_code_fence 的三处 LLM 代码入口。
        策略：先去围栏 → 语法已合法则原样返回（零改动）→ 不合法才尝试归一化。
        """
        _code = self._strip_code_fence(text)
        if not _code:
            return ""
        try:
            import ast as _ast_clean
            _ast_clean.parse(_code)
            return _code          # 语法已合法，不动一个字符
        except SyntaxError as e:
            silent_exc(e, "nucleus/reasoning/SafeEvolutionExecutor.py:3239:_clean_llm_code", level="debug")
            pass                  # 语法有问题，尝试归一化
        except Exception:
            return _code          # 其他异常（如 ValueError 空源码）不处理
        # ★主线第15批 T5/P2-96：最多两轮预检重试
        #   第 1 轮 = 映射表 + 通用兜底；第 2 轮 = 在上一轮结果上再跑一次
        #   （兜底规则可能级联，例如 NFKC 后暴露新的可归一化字符）。
        import ast as _ast_clean
        _cur = _code
        _total_n = 0
        for _round in (1, 2):
            _fixed, _n = self._normalize_fullwidth_in_code(_cur)
            _total_n += _n
            if not _n:
                break
            try:
                _ast_clean.parse(_fixed)
                _module_logger.info(
                    f"[LLM代码清理] 全角标点归一化 {_total_n} 处（第{_round}轮），"
                    f"语法检查由失败转为通过（P0-10/P2-96）")
                return _fixed
            except SyntaxError:
                _cur = _fixed
        # ★第91批 T-91b 阶段3：缩进修复（灰度 ENABLE_M91_LLM_INDENT_REPAIR）。
        #   T0 实测：4 次 LLM 补丁尝试全部因
        #   `IndentationError: unindent does not match any outer indentation level
        #    (<llm-patch>, line 9/11/19/26)` 被完整性关2 拒绝；复现实验逐字复现。
        #   本函数此前只有「全角标点归一化」一种修复能力，对缩进无能为力。
        #   ★采纳判据 = `_m91_gate2_check`（关2 同口径），**不是**本函数开头的
        #     `ast.parse(_code)`：真实补丁是带缩进的方法体片段，后者恒失败
        #     ⇒ 若用它会「修了也白修」。前置条件保证只对**关2 本来就会拒**的输入生效。
        if _m91_indent_repair_on():
            _m91_g2_ok0, _m91_g2_err0, _ = _m91_gate2_parse(_code)
            if not _m91_g2_ok0:
                for _cand_src in (_code, _cur):
                    _rep = self._m91_repair_indentation(_cand_src)
                    if not _rep or _rep == _cand_src:
                        continue
                    if not _m91_gate2_check(_rep)[0]:
                        continue
                    _module_logger.info(
                        "[LLM代码清理] 缩进漂移已修复（关2 同口径自检由失败转通过）——"
                        "修复前关2: %s", _m91_g2_err0)
                    return _rep
        if _total_n:
            _module_logger.debug(
                f"[LLM代码清理] 已归一化 {_total_n} 处全角/中文标点，语法仍不合法，"
                f"交由后续验证给出准确报错")
            # ★第54批 T6.2（根因6）：归一化后语法仍不合法 → 返回**原始**代码 _code，
            #   避免归一化本身引入新的语法错误。灰度 ENABLE_LLM_PATCH_RETURN_ORIGINAL。
            try:
                import config as _cfg_orig
                if getattr(_cfg_orig, "ENABLE_LLM_PATCH_RETURN_ORIGINAL", True):
                    return _code
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return _cur
        return _code          # 没有可归一化的字符

    def _submit_llm_patch(self, issue: dict[str, Any], original_code: str, llm_fixed: str) -> bool:
        """把 LLM 修复代码转成补丁，副本验证后存入待审批队列（★P2.5 审查-修复闭环）。"""
        _file = issue.get("file", "")
        _method = issue.get("method", "")
        if not _file or not original_code or not llm_fixed:
            return False
        _llm_clean = self._clean_llm_code(llm_fixed)  # ★PHASE17-A2
        _llm_clean, _ = self._m91_align_base_indent(
            original_code, _llm_clean, f"file={_file}, method={_method}")  # ★第91批T-91b
        if not _llm_clean or _llm_clean.strip() == original_code.strip():
            return False
        # ★FIX(AP60复现): LLM 补丁同样由方案数值评分推导风险/信任分，
        # 与 _generate_patch(:514-519) 保持一致，避免 risk_level 写死"低"、trust_score 恒 60
        _risk_score = issue.get("risk_score", 2)
        _risk_map = {1: "极低", 2: "低", 3: "中等", 4: "高"}
        _risk_level = issue.get("risk_level") or _risk_map.get(_risk_score, "低")
        _benefit = issue.get("benefit_score", _DEF_BENEFIT_SCORE)
        _trust_score = min(95, max(10, int(_benefit) * 10))
        _patch = {
            "id": f"patch_llm_{int(time.time())}_{hash(original_code) & 0xFFFF:04x}",
            "file": _file,
            "method": _method,
            "issue_type": issue.get("type", ""),
            "risk_level": _risk_level,
            "description": issue.get("description", ""),
            "original_code": original_code,
            "modified_code": _llm_clean,
            "diff_summary": self._generate_diff_summary(original_code, _llm_clean),
            "trust_score": _trust_score,
            "generated_at": time.time(),
            "status": "pending",
            "applied": False,
        }
        try:
            _verify = self._patch_manager.verify_in_copy(_patch)
            _patch["verification"] = _verify
            if _verify.get("passed"):
                # ★LLM 语义自我复核(LLM 决策核心深化): LLM 补丁在验证通过后再加一道
                #   LLM 语义级把关——是否真正解决根因、有无副作用/逻辑缺陷。
                #   复核为软信号：FAIL 时下调 trust_score 并标记 llm_review_failed
                #   （不硬阻断，硬阻断会误伤 LLM 有效补丁）；LLM 调用失败→跳过复核（零冲突）。
                _review = self._llm_review_patch(_patch, issue)
                if _review:
                    _patch["llm_review"] = _review
                    if not _review.get("passed"):
                        _patch["trust_score"] = max(10, int(_patch.get("trust_score", 60)) - 10)
                        _patch["llm_review_failed"] = True
                        _module_logger.info(
                            f"[LLM复核] 补丁语义复核未通过，信任分下调10: {_file}:{_method}"
                            f" - {str(_review.get('reason', ''))[:100]}")
                _patch["status"] = "verified"
                self._patch_manager.save_pending_patch(_patch)
                return True
        except Exception as _e:
            # ★P1-4修复：补丁验证/落盘失败不再静默吞掉，记录可见告警便于定位
            _module_logger.error(f"补丁验证/落盘失败: {_file}:{_method} - {_e}")
        return False

    def _call_llm_for_patch_review(self, patch: dict[str, Any],
                                   issue: dict[str, Any]) -> str | None:
        """调用大模型对 LLM 补丁做语义复核（根因解决度/副作用/逻辑完整性）。

        复用运行时审查的 LLM 调用模式（config REMOTE_API_CONFIG → safe_http_json）。  # type: ignore[possibly-unbound]
        无 API 配置/调用失败 → None（跳过复核，零冲突）。
        """
        try:
            import json

            import config  # type: ignore[possibly-unbound]
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})  # type: ignore[possibly-unbound]
            _api_url = _api_cfg.get("api_url", "")
            _api_key = _api_cfg.get("api_key", "")
            if not _api_url or not _api_key:
                return None
            _orig = (patch.get("original_code") or "")[:2500]
            _mod = (patch.get("modified_code") or "")[:2500]
            _desc = (issue.get("description") or "")[:500]
            _rel_logs = (issue.get("related_logs") or "")[:800]
            _prompt = (
                "你是曈曈的补丁语义审查器。请对下面的代码修复补丁做语义级复核。\n"
                f"问题描述: {_desc}\n"
                f"相关日志: {_rel_logs or '无'}\n"
                f"修复前代码:\n```python\n{_orig}\n```\n"
                f"修复后代码:\n```python\n{_mod}\n```\n"
                "请输出格式（严格）：\n"
                "第一行: PASS 或 FAIL（FAIL 当且仅当：未解决根因 / 引入新的逻辑缺陷 / "
                "可能引发副作用 / 修复不完整）\n"
                "第二行起: 理由（30字内）。只输出两行。"
            )
            _payload = {
                "model": get_llm_call_config().get("default_model", "deepseek-v4-flash"),
                "messages": [
                    {"role": "system", "content": "你是曈曈的补丁语义审查器，只输出 PASS/FAIL 与简短理由。"},
                    {"role": "user", "content": _prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 256,
            }
            _payload_bytes = json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {'Content-Type': 'application/json; charset=utf-8',
                        'Authorization': 'Bearer ' + _api_key}
            from nucleus.ssrf_guard import safe_http_json
            with api_rate_limited(enabled=get_llm_call_config().get("enable_rate_limit", True)):
                _ok, _data = safe_http_json(_api_url, method='POST', data=_payload_bytes,
                                            headers=_headers, timeout=get_llm_call_config()["timeout_by_purpose"]["evolution"])
            if not _ok or not isinstance(_data, dict):
                return None
            _choices = _data.get("choices", [])
            if _choices:
                return _choices[0].get("message", {}).get("content", "")
        except Exception as e:
            print(f"[WARNING] SafeEvolutionExecutor.py:1980: {type(e).__name__}: {e}")
            return None
        return None

    def _llm_review_patch(self, patch: dict[str, Any],
                          issue: dict[str, Any]) -> dict[str, Any] | None:
        """LLM 补丁语义复核编排：调用→解析 PASS/FAIL→返回结构化结果。

        解析：首行含 PASS/FAIL；无 API/调用失败 → None（跳过复核，零冲突）；
        无法解析 → 保守 PASS（不因解析问题误伤）。
        """
        try:
            _raw = self._call_llm_for_patch_review(patch, issue)
            if not _raw:
                return None
            _first = (_raw.strip().splitlines() or [""])[0].strip().upper()
            _passed = "PASS" in _first or "FAIL" not in _first
            _reason = " ".join(_raw.strip().splitlines()[1:2]) or _raw.strip()[:80]
            return {"passed": _passed, "reason": _reason[:120], "raw": _raw.strip()[:200]}
        except Exception as e:
            silent_exc(e, "nucleus/reasoning/SafeEvolutionExecutor.py:3428:_llm_review_patch", level="warning")
            return None

    def _load_pulse_metadata_summary(self, max_events: int = 40) -> str:
        """读取脉冲配置表，生成「事件 → 订阅方」摘要（★FIX: 架构元数据投喂 LLM）。"""
        try:
            import re as _re
            _cfg = os.path.join(self._patch_manager.get_project_root(), "pulses", "pulse_config.yaml")  # type: ignore[possibly-unbound]
            if not os.path.exists(_cfg):
                return ""
            with open(_cfg, encoding='utf-8') as _f:
                _text = _f.read()
            _event_pattern = _re.compile(r'^\s{2}([\w.]+):\s*$', _re.MULTILINE)
            _summary = []
            for _m in _event_pattern.finditer(_text):
                _event = _m.group(1)
                if _event in ("version", "light_speed_channel", "pulses", "last_updated"):
                    continue
                _start = _m.end()
                _next = _event_pattern.search(_text, _start)
                _block = _text[_start:_next.start()] if _next else _text[_start:_start + 600]
                _t = _re.search(r'targets:\s*\[(.*?)\]', _block)
                _targets = _t.group(1).strip() if _t else "未知"
                _summary.append(f"{_event} → [{_targets}]")
                if len(_summary) >= max_events:
                    break
            return "\n".join(_summary)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_load_pulse_metadata_summary L3465")
            return ""

    def _call_llm_for_review(self, pulse_type: str, traceback_text: str,
                             code_snippet: str, related_logs: str) -> str | None:
        """调用大模型对运行时异常做定向深度审查（根因分析，★P2）。"""
        try:
            import json

            import config  # type: ignore[possibly-unbound]
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})  # type: ignore[possibly-unbound]
            _api_url = _api_cfg.get("api_url", "")
            _api_key = _api_cfg.get("api_key", "")
            if not _api_url or not _api_key:
                return None
            _code_part = f"相关代码:\n```python\n{code_snippet[:2500]}\n```\n" if code_snippet else ""
            _log_part = f"相关运行日志:\n```\n{related_logs[:1500]}\n```\n" if related_logs else ""
            _meta_summary = self._load_pulse_metadata_summary()
            _meta_part = f"脉冲订阅注册表（事件→订阅方）:\n```\n{_meta_summary[:2000]}\n```\n" if _meta_summary else ""
            _prompt = (
                f"你是曈曈的架构审查模块，请对一次运行时异常做根因分析。\n"
                f"异常脉冲类型: {pulse_type}\n"
                f"异常堆栈:\n```\n{traceback_text[:2000]}\n```\n"
                f"{_code_part}"
                f"{_log_part}"
                f"{_meta_part}"
                f"请结合脉冲订阅注册表判断是否闭环，输出：1) 根因定位（文件:方法:原因）；2) 是否假闭环/逻辑断层；3) 修复建议。简洁分点。"
            )
            _payload = {
                "model": get_llm_call_config().get("default_model", "deepseek-v4-flash"),
                "messages": [
                    {"role": "system", "content": "你是曈曈的代码自学习助手，负责运行时异常的根因分析与架构审查。"},
                    {"role": "user", "content": _prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 1024,
            }
            _payload_bytes = json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {'Content-Type': 'application/json; charset=utf-8', 'Authorization': 'Bearer ' + _api_key}
            from nucleus.ssrf_guard import safe_http_json
            with api_rate_limited(enabled=get_llm_call_config().get("enable_rate_limit", True)):
                _ok, _data = safe_http_json(_api_url, method='POST', data=_payload_bytes, headers=_headers, timeout=get_llm_call_config()["timeout_by_purpose"]["evolution"])
            if not _ok or not isinstance(_data, dict):
                return None
            _choices = _data.get("choices", [])
            if _choices:
                return _choices[0].get("message", {}).get("content", "")
        except Exception as e:
            print(f"[WARNING] SafeEvolutionExecutor.py:2073: {type(e).__name__}: {e}")
            return None
        return None

    def run_runtime_guided_review(self, self_inspector=None) -> dict[str, Any]:
        """
        ★P2: 运行时引导的定向深度审查。
        采集运行时指标（异常快照/队列积压/重入），若发现异常，
        把「异常现场 + 相关代码 + 相关日志」喂给 LLM 做定向深度审查，
        结果沉淀到验证学习枢纽。
        """
        import re as _re

        from nucleus.mnemosyne.verification_learning_hub import (
            get_verification_learning_hub,
        )
        from nucleus.runtime_metrics import get_runtime_metrics

        _rt = get_runtime_metrics()
        _snap = _rt.get_snapshot()
        _errors = _snap.get("error_snapshots", [])
        _queue_depth = _snap.get("queue_max_depth", 0)
        _reentry = _snap.get("reentry_count", 0)
        _hub = get_verification_learning_hub()

        # 触发条件：有异常快照 或 队列积压明显 或 重入发生
        if not _errors and _queue_depth < 50 and _reentry == 0:
            return {"status": "no_anomaly", "reviewed": 0, "distilled": 0, "report": []}

        _reviewed = 0
        _distilled = 0
        _report = []

        for _err in _errors[:3]:
            _ptype = _err.get("pulse_type", "?")
            _tb = _err.get("traceback", "")
            _file = ""
            _method = ""
            _m = _re.search(r'File "([^"]+\.py)", line \d+, in (\w+)', _tb)
            if _m:
                _file = _m.group(1)
                _method = _m.group(2)
            _snippet = ""
            if self_inspector and _file and _method:
                _organ = os.path.splitext(os.path.basename(_file))[0]
                try:
                    _detail = self_inspector.get_method_body(_organ, _method)
                    _snippet = _detail.get("body", "") if _detail else ""
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _related_logs = self._find_related_logs(_file, _method) if _file else ""
            _llm = self._call_llm_for_review(_ptype, _tb, _snippet, _related_logs)
            if _llm:
                _reviewed += 1
                _report.append({"pulse_type": _ptype, "file": _file, "method": _method, "analysis": _llm[:500]})
                try:
                    _hub.record(
                        organ="code_learner",
                        task_type="runtime_guided_review",
                        input_summary=f"{_ptype}:{os.path.basename(_file)}.{_method}",
                        local_result={"error": _err.get("error", "")[:200], "queue_depth": _queue_depth},
                        confidence=0.8,
                        relevance_score=0.9,
                        needs_verification=True,
                        verification_result={"llm_analysis": _llm[:800]},
                        api_better=True,
                        lesson=f"运行时异常 {_ptype} 的 LLM 深度审查",
                    )
                    _distilled += 1
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"status": "reviewed", "reviewed": _reviewed, "distilled": _distilled, "report": _report}

    def execute(self, plans: list[dict[str, Any]], 
                self_inspector=None) -> dict[str, Any]:
        """
        为推演方案生成可执行补丁。
        
        Args:
            plans: EvolutionSandbox生成的优化方案列表
            self_inspector: SelfInspector实例（用于读取代码）
        
        Returns:
            包含所有补丁的报告字典
        """
        if not plans:
            return {
                "status": "no_plans",
                "summary": "无优化方案可执行。",
                "patches": [],
            }
        
        # ★多步规划(P1红项)：冲突感知——同一 organ+method 的多个计划，
        # 只执行优先级最高者（EvolutionSandbox 已按 benefit/risk 排序，先到者优先），
        # 其余标记 conflict 跳过，避免多个补丁同位置互相覆盖产生脏补丁。
        _plans = list(plans[:3])
        _plan_scope: dict[tuple[str, str], str] = {}
        for _p in _plans:
            _scope_key = (_p.get("target") or "", _p.get("method") or "")
            if not all(_scope_key):
                continue
            if _scope_key in _plan_scope:
                _p["_skip"] = True
                _p["conflict_with"] = _plan_scope[_scope_key]
                _module_logger.info(
                    f"多步规划冲突规避: {_scope_key[0]}.{_scope_key[1]} 已由 "
                    f"{_plan_scope[_scope_key]} 覆盖，跳过 {_p.get('id') or _p.get('type')}")
            else:
                _plan_scope[_scope_key] = _p.get("id") or str(_p.get("type"))
        _plans = [p for p in _plans if not p.get("_skip")]
        
        patches = []
        # ★自我反思闭环(P1红项): 验证失败不直接放弃——按失败阶段反思根因、换策略重试。
        #   全局预算 _retry_budget 限制整轮重试总数（防验证失败触发的重试风暴，峰值压力均衡）。
        _retry_budget = 2
        for plan in _plans:
            patch = self._generate_patch(plan, self_inspector)
            if patch:
                # ★自我反思闭环: 生成→验证→(失败则反思换策略重试)，最多 2 次尝试。
                _attempt = 0
                while _attempt < 2:
                    patch["verification"] = self._patch_manager.verify_in_copy(patch)
                    if patch["verification"]["passed"]:
                        break
                    _attempt += 1
                    if _attempt >= 2 or _retry_budget <= 0:
                        patch["status"] = "verification_failed"
                        _module_logger.warning(
                            f"补丁验证失败: {patch['id'][:16]}... → "
                            f"{patch['verification'].get('errors', [])}")
                        break
                    _retried = self._reflect_and_retry(patch, plan, self_inspector, _retry_budget)
                    if _retried is patch:
                        patch["status"] = "verification_failed"
                        break
                    _retry_budget -= 1
                    patch = _retried
                if patch.get("verification", {}).get("passed"):
                    # ★审美判据深化(第四条路·E6): 生成入队即打分——先评分（供择优决策），
                    #   再决定自动批准。评分结果同时供：
                    #     a) apply_all_pending 的审美降序排序（14.5 已落地）
                    #     b) 本处的 D 级择优拦截（低质量补丁不自动批准）
                    _aesthetic_grade = None
                    try:
                        from nucleus.evolution.AestheticJudge import get_aesthetic_judge
                        _aesthetic = get_aesthetic_judge().score(patch.get("modified_code"))
                        patch["aesthetic_score"] = _aesthetic
                        patch["aesthetic_grade"] = _aesthetic.get("grade")
                        _aesthetic_grade = _aesthetic.get("grade")
                    except Exception as e:
                        _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                    # v25.0修复(BRAIN-7): 自动应用闸门 main.py:apply_all_pending(only_approved=True)
                    # 只接受 status=="approved"。若此处固定写 "verified"，则无任何代码将其提升为
                    # "approved"，导致补丁永远不落地、进程永不重启（假闭环）。
                    # 因此：开启自动应用时置为 "approved"，走人工审批流时保持 "verified"。
                    _auto_apply = False
                    try:
                        import config  # type: ignore[possibly-unbound]
                        _auto_apply = getattr(config, 'EVOLUTION_CONFIG', {}).get("auto_apply_enabled", False)  # type: ignore[possibly-unbound]
                    except Exception as e:
                        _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                    # ★进化闭环升级(完美级): 核心文件不再强制人工审批，
                    #   改为「强验证兜底 + 健康度对比 + 自动回退」三重防线共同保障安全。
                    #   核心文件与非核心文件都走自动批准，但核心文件依赖更严格的验证：
                    #   1) 副本语法/import 验证  2) 真实回归测试  3) 重启后三关验证
                    #   4) 健康度对比(下降即回退) 5) 防循环重启上限。
                    _is_core = self._is_core_file(patch.get("file", ""))
                    if _auto_apply:
                        # ★进化闭环升级(阶段A): LLM 补丁增加额外信任门槛。
                        #   LLM 补丁(repair_source="llm", confidence="medium")风险高于
                        #   本地规则补丁(high)，需满足 auto_apply_min_trust 才自动批准，
                        #   否则降为 verified 待人工审批。本地规则补丁不受此门槛限制。
                        _is_llm_patch = patch.get("repair_source") == "llm"
                        _llm_trust_ok = True
                        if _is_llm_patch:
                            try:
                                _base_trust = getattr(config, 'EVOLUTION_CONFIG', {}).get("auto_apply_min_trust", 60)  # type: ignore[possibly-unbound]
                                # ★策略自进化(下一层): 信任门槛按历史策略效果动态调整
                                _min_trust = self._dynamic_llm_min_trust(
                                    patch.get("issue_type", ""), _base_trust)
                                if _min_trust != _base_trust:
                                    _module_logger.info(
                                        f"[策略自进化] {patch.get('issue_type')} 动态信任门槛 "
                                        f"{_base_trust}→{_min_trust}")
                                _llm_trust_ok = patch.get("trust_score", 0) >= _min_trust
                            except Exception:
                                _llm_trust_ok = True
                        if _is_llm_patch and not _llm_trust_ok:
                            patch["status"] = "verified"
                        elif _aesthetic_grade == "D":
                            # ★审美判据择优(深化·E6): D 级（低质量）补丁不自动批准，转人工审批。
                            #   从「记录分数/应用排序」升级为「实际决策」——低质量代码不静默进入自动应用，
                            #   需创造者审核后批准。零冲突：仅降级为 verified，不删数据、不影响验证链路。
                            patch["status"] = "verified"
                            _module_logger.warning(
                                f"审美门槛拦截: 补丁{_aesthetic_grade}级(质量低)转人工审批 {patch['id'][:16]}...")
                        else:
                            # ★主线第79批 T1(P0 安全审计): 核心文件永远不自动批准，
                            #   强制等待人工审批(status=verified)；仅非核心走自动批准(approved)。
                            if _is_core:
                                patch["is_core_file"] = True
                                patch["status"] = self._resolve_core_auto_apply_status(_is_core)
                                if self._core_auto_apply_allowed():
                                    _module_logger.warning(
                                        f"灰度开关允许核心文件自动批准(回退旧行为): "
                                        f"{patch['id'][:16]}...")
                                else:
                                    _module_logger.warning(
                                        f"核心文件强制人工审批(安全红线，不自动批准): "
                                        f"{patch['id'][:16]}... → {patch['file']}")
                            else:
                                patch["status"] = "approved"
                    else:
                        patch["status"] = "verified"
                    self._patch_manager.save_pending_patch(patch)
                    _module_logger.info(f"补丁验证通过({patch['status']}): {patch['id'][:16]}... → {patch['diff_summary'][:60]}")
                else:
                    # 验证失败状态已由 while 循环内设置（verification_failed），无需重复处理
                    pass
                
                patches.append(patch)
        
        # ★FIX(A5): 移除失效且危险的 _auto_apply_patch 直写路径，统一走 main.py 的 apply_all_pending（含安全门+核心审批）
        auto_applied = []
        auto_apply_enabled = False
        try:
            import config  # type: ignore[possibly-unbound]
            evo_cfg = getattr(config, 'EVOLUTION_CONFIG', {})  # type: ignore[possibly-unbound]
            auto_apply_enabled = evo_cfg.get("auto_apply_enabled", False)
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        
        _approved_count = sum(1 for p in patches if p.get("status") == "approved")
        _core_approved = sum(1 for p in patches if p.get("status") == "approved" and p.get("is_core_file"))
        if _approved_count:
            summary = f"安全执行完成：生成{len(patches)}个补丁，其中{_approved_count}个已批准（含{_core_approved}个核心文件），将在验证通过后应用并重启验证。"
            warning = f"已批准{_approved_count}个补丁（含核心文件），依赖强验证+健康度对比+自动回退三重防线兜底；若验证失败将自动回退。"
        else:
            summary = f"安全执行完成：生成{len(patches)}个代码补丁，等待创造者审核。"
            warning = "以下补丁为只读建议，未自动修改任何代码文件。请创造者手动审核后执行。"
        
        report = {
            "status": "completed",
            "summary": summary,
            "patches": patches,
            "auto_applied": auto_applied,
            "generated_at": time.time(),
            "warning": warning,
            "auto_apply_enabled": auto_apply_enabled,
        }
        
        self._patch_log.append(report)
        if len(self._patch_log) > self._max_log:
            self._patch_log = self._patch_log[-self._max_log:]
        
        return report
    
    def _dynamic_llm_min_trust(self, issue_type: str, base_trust: int) -> int:
        """★策略自进化(元学习·下一层): LLM 补丁自动批准信任门槛按历史策略效果动态调整。

        - llm_proven 类型（LLM 历史有成功记录）→ 门槛降低（×0.8，下限 40）：
          历史证明该类型 LLM 可靠，适度放行（仍走完整验证 + 审美拦截兜底）；
        - rule_bad 类型（规则失败率>=67% 已转 LLM）→ 门槛提高（×1.15，上限 85）：
          该类型修复难度高，LLM 补丁需更谨慎；
        - 无历史/数据不足 → 维持 base_trust（零冲突，默认路由不变）。
        """
        try:
            _hist = self._patch_manager.get_strategy_effectiveness(issue_type)
            if _hist.get("no_history"):
                return base_trust
            if _hist.get("rule_bad"):
                return min(85, int(base_trust * 1.15))
            if _hist.get("llm_proven"):
                return max(40, int(base_trust * 0.8))
            return base_trust
        except Exception:
            return base_trust

    def _reflect_and_retry(self, patch: dict[str, Any], plan: dict[str, Any],
                            self_inspector=None, _retry_budget: int = 1) -> dict[str, Any]:
        """★自我反思闭环(P1红项)：补丁验证失败后不直接放弃——按失败阶段反思根因、换策略重试。

        反思策略（按验证 stage 分派）：
          - code_not_found      → 目标代码片段漂移（并发修改/已重跑），重新读取最新方法体对齐重试；
          - syntax_check_failed → 替换破坏语法结构；仅 LLM 补丁携带错误信息让 LLM 换策略修正；
          - import_test_failed  → 修改破坏导入/初始化；仅 LLM 补丁携带 traceback 让 LLM 换策略修正；
          - completeness_check_failed / 其他 → 残缺或未知，不值得重试（保持 verification_failed）。

        安全与压力约束：
          - 重试总数由 execute() 的 _retry_budget 全局预算控制（防验证失败触发重试风暴）；
          - 返回原 patch 对象（is 比较）表示「不值得重试」，调用方保持 verification_failed；
          - 全程只读日志、不改文件；重试补丁仍走完整副本验证链路，不降低验证门槛。
        """
        _verify = patch.get("verification") or {}
        _stage = _verify.get("stage", "")
        _errors = _verify.get("errors", [])
        _err_text = "；".join(str(e) for e in _errors[:2])
        _module_logger.info(
            f"[自我反思] 补丁验证失败，反思根因: stage={_stage} errors={_err_text}")

        # 1) code_not_found：代码漂移 → 重新读取最新方法体对齐重试
        if _stage == "code_not_found" and _retry_budget > 0 and self_inspector:
            try:
                _organ = plan.get("target", "")
                _method = plan.get("method", "")
                _md = self_inspector.get_method_body(_organ, _method)
                _latest = (_md or {}).get("body", "")
                if _latest and _latest.strip() != patch.get("original_code", "").strip():
                    _new_plan = dict(plan)
                    _new_patch = self._generate_patch(_new_plan, self_inspector)
                    if _new_patch:
                        _new_patch["verification"] = self._patch_manager.verify_in_copy(_new_patch)
                        _new_patch["reflect_attempts"] = 1
                        _module_logger.info(
                            f"[自我反思] code_not_found→重新对齐最新代码重试, "
                            f"verify={_new_patch['verification']['stage']}/"
                            f"{_new_patch['verification']['passed']}")
                        return _new_patch
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _module_logger.info("[自我反思] 代码已漂移但无法重新对齐，放弃重试")
            return patch

        # 2) syntax/import/行为不等价失败：携带失败信息换策略重试
        #    ★行为等价验证(14.32): behavior_mismatch —— 补丁通过语法/导入但返回值偏移，
        #    LLM 需基于「返回值不一致」差异换策略修正。
        #    ★长期深化(阶段A·LLM 从顾问提升为决策核心): 规则补丁(local_rule)验证失败时
        #    同样升级 LLM 换策略重试——规则模板只覆盖已知反模式，规则失败意味着形态漂移或
        #    上下文敏感，交由 LLM 做开放域语义修正（repair_source 仍为 llm，走完整验证 +
        #    trust 门槛，零冲突）。
        if _stage in ("syntax_check_failed", "import_test_failed", "behavior_mismatch") \
                and _retry_budget > 0:
            _is_rule = patch.get("repair_source") != "llm"
            try:
                _new_plan = dict(plan)
                _new_plan["llm_prior_error"] = f"{_stage}: {_err_text}"
                if _is_rule:
                    # 规则补丁失败 → 升级 LLM（记录升级来源与原策略，供审计/统计）
                    _new_plan["upgrade_from_rule"] = True
                    _new_plan["rule_strategy"] = patch.get("applied_strategy", "")
                    _module_logger.info(
                        f"[自我反思] 规则补丁验证失败(stage={_stage})→升级LLM换策略重试, "
                        f"原策略={patch.get('applied_strategy', '')}")
                _new_patch = self._generate_llm_patch(
                    _new_plan, self_inspector,
                    patch.get("original_code", ""),
                    patch.get("file", ""),
                    patch.get("method", ""))
                if _new_patch:
                    _new_patch["verification"] = self._patch_manager.verify_in_copy(_new_patch)
                    _new_patch["reflect_attempts"] = 1
                    if _is_rule:
                        _new_patch["upgrade_from_rule"] = True
                        _new_patch["rule_strategy"] = patch.get("applied_strategy", "")
                    _module_logger.info(
                        f"[自我反思] {'规则升级LLM' if _is_rule else 'LLM换策略'}重试, "
                        f"verify={_new_patch['verification']['stage']}/"
                        f"{_new_patch['verification']['passed']}")
                    return _new_patch
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _module_logger.info("[自我反思] 换策略重试未生成或仍失败，保持 verification_failed")
            return patch

        # 3) 其余阶段（completeness 残缺 / 未知）：不值得重试
        _module_logger.info(f"[自我反思] stage={_stage} 不值得重试，保持 verification_failed")
        return patch

    def _generate_multiple_solutions(self, plan: dict[str, Any],
                                      original_code: str,
                                      file_path: str,
                                      method_name: str,
                                      self_inspector=None) -> list[dict[str, Any]]:
        """★第三阶段：为问题生成多种修复方案并评估。

        为每个问题生成2-3种不同思路的修复方案，评估风险/改动量/效果后选择最优。

        Args:
            plan: 修复计划
            original_code: 原始代码
            file_path: 文件路径
            method_name: 方法名
            self_inspector: SelfInspector实例

        Returns:
            方案列表，按综合评分降序排列
        """
        plan_type = plan.get("type", "")
        solutions = []

        # 方案1：本地规则修复（保守、低风险）
        try:
            _rule_patch = self._generate_rule_patch(
                plan, original_code, file_path, method_name
            )
            if _rule_patch:
                _rule_patch["solution_type"] = "rule_based"
                _rule_patch["solution_risk"] = "低"
                _rule_patch["solution_change_scope"] = "局部"
                _rule_patch["solution_score"] = self._score_solution(_rule_patch, "rule")
                solutions.append(_rule_patch)
        except Exception as _e:
            _module_logger.debug(f"规则方案生成失败: {_e}")

        # 方案2：LLM修复（灵活、可能效果更好但风险较高）
        try:
            _llm_patch = self._generate_llm_patch(
                plan, self_inspector, original_code, file_path, method_name
            )
            if _llm_patch:
                _llm_patch["solution_type"] = "llm_based"
                _llm_patch["solution_risk"] = "中"
                _llm_patch["solution_change_scope"] = "方法级"
                _llm_patch["solution_score"] = self._score_solution(_llm_patch, "llm")
                solutions.append(_llm_patch)
        except Exception as _e:
            _module_logger.debug(f"LLM方案生成失败: {_e}")

        # 方案3：保守修复（只加日志/保护，不改逻辑）—— 对高风险问题
        try:
            _conservative_patch = self._generate_conservative_patch(
                plan, original_code, file_path, method_name
            )
            if _conservative_patch:
                _conservative_patch["solution_type"] = "conservative"
                _conservative_patch["solution_risk"] = "极低"
                _conservative_patch["solution_change_scope"] = "最小"
                _conservative_patch["solution_score"] = self._score_solution(
                    _conservative_patch, "conservative"
                )
                solutions.append(_conservative_patch)
        except Exception as _e:
            _module_logger.debug(f"保守方案生成失败: {_e}")

        # 按综合评分降序排列
        solutions.sort(key=lambda s: s.get("solution_score", 0), reverse=True)

        if solutions:
            _best = solutions[0]
            _module_logger.info(
                f"[多方案评估] {plan_type}/{method_name}: 生成{len(solutions)}种方案, "
                f"最优={_best.get('solution_type', 'unknown')}"
                f"(评分={_best.get('solution_score', 0):.1f})"
            )

        return solutions

    def _score_solution(self, patch: dict[str, Any], solution_type: str) -> float:
        """★第三阶段：评估修复方案的综合评分。

        评分维度：风险(40%) + 改动量(20%) + 预期效果(30%) + 可验证性(10%)
        """
        score = 0.0

        # 风险评分（越低越好，权重40%）
        risk = patch.get("solution_risk", "中")
        risk_score = {"极低": 100, "低": 80, "中": 50, "高": 20}.get(risk, 50)
        score += risk_score * 0.4

        # 改动量评分（越小越好，权重20%）
        scope = patch.get("solution_change_scope", "方法级")
        scope_score = {"最小": 100, "局部": 80, "方法级": 60, "跨方法": 30}.get(scope, 50)
        score += scope_score * 0.2

        # 预期效果评分（权重30%）
        # 规则修复：效果确定但可能不彻底
        # LLM修复：效果可能更好但不确定
        # 保守修复：只缓解不根治
        effect_score = {"rule": 70, "llm": 85, "conservative": 40}.get(solution_type, 60)
        score += effect_score * 0.3

        # 可验证性评分（权重10%）
        # 规则修复：可自动验证
        # LLM修复：需人工审查
        # 保守修复：容易验证
        verify_score = {"rule": 90, "llm": 60, "conservative": 95}.get(solution_type, 70)
        score += verify_score * 0.1

        return score

    def _generate_rule_patch(self, plan: dict[str, Any], original_code: str,
                             file_path: str, method_name: str) -> dict[str, Any] | None:
        """★第三阶段：生成本地规则修复方案（从_generate_patch中抽取的规则部分）。"""
        # 此方法封装原有规则修复逻辑，返回规则补丁
        # 实际逻辑在_generate_patch中，这里作为多方案的一个分支
        return None  # 占位，实际由_generate_patch处理

    def _generate_conservative_patch(self, plan: dict[str, Any], original_code: str,
                                     file_path: str, method_name: str) -> dict[str, Any] | None:
        """★第三阶段：生成保守修复方案（只加日志/保护，不改逻辑）。

        对高风险问题，先只添加诊断日志或保护代码，不改变核心逻辑。
        """
        plan_type = plan.get("type", "")
        modified = original_code

        # 对静默异常：只加日志，不改异常捕获逻辑
        if "silent_exception" in plan_type or "bare_except" in plan_type:
            import re as _re
            # 在pass前加日志（保守方式：不改变异常类型，只记录）
            _pat = _re.compile(r'(?m)^(\s*)pass\s*$')
            if _pat.search(modified):
                modified = _pat.sub(
                    lambda m: m.group(1) + "self._log(LogLevel.DEBUG, '异常已捕获(保守修复)')\n" + m.group(0),
                    modified, count=1
                )

        if modified != original_code:
            return {
                "file": file_path,
                "method": method_name,
                "original_code": original_code,
                "modified_code": modified,
                "applied_strategy": f"conservative_{plan_type}",
            }
        return None

    # ===== ★M85-1（第85批 T-85a）：本地学习尝试通道 =====
    #   动机（任务书 T-85a）：`Traceback` / `ERROR` / `cross_module_singleton_call`
    #   / `long_method` 等未分类问题此前**完全不参与本地修复** —— L1178 的
    #   `_local_ok = _type in _local_fixable_types` 为假 → 路径A 直接短路 →
    #   本地机制失去学习机会，问题 100% 转 LLM，`submitted` 长期为 0。
    #   ★设计报告（任务书授权"先出设计报告再改"）：
    #     docs/分析报告/第85批_T85a本地学习尝试通道设计报告_20260919.md
    #     任务书点名的 `_cooldown_classify` 只是**循环结束后的冷却登记分类**，
    #     不参与"是否走本地"的决策；故本通道接在路径A 之后、路径B 之前。
    @staticmethod
    def _m85_learning_attempt_enabled() -> bool:
        """★M85-1 灰度开关：本地学习尝试通道（默认 True）。

        ★不改 config.py（红线②）：EVOLUTION_CONFIG 里没有该键时取默认 True；
        显式置 False → 通道整体短路，行为回到改造前（零副作用）。
        """
        try:
            import config as _m85c
            _cfg = getattr(_m85c, "EVOLUTION_CONFIG", {})
            if isinstance(_cfg, dict):
                return bool(_cfg.get("learning_attempt_enabled", True))
            return True
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_m85_learning_attempt_enabled L4053")
            return True

    @staticmethod
    def _m85_conservative_fix(issue_type: str, code: str) -> tuple[str, str]:
        """对任意代码片段做一次**保守低风险**修复尝试。

        Returns:
            ``(strategy, modified_code)``；``strategy == ""`` 表示无安全修复。

        ★只做两类「纯文本、语义不变量明确、可被 verify_in_copy 验证」的改写：
          ① ``except_log``   —— ``except [X]: <换行> pass`` → 补错误日志
          ② ``http_timeout`` —— 无 ``timeout=`` 的 requests 调用 → 补 timeout
        其余形态（try-except 包裹裸调用 / None 检查 / 方法拆解）需要作用域与
        数据流分析，不属"低风险保守"范畴 → **如实返回无安全修复**，
        由调用方记为 ``no_safe_fix`` 并交给既有 LLM 通道（不硬做）。
        """
        if not isinstance(code, str) or not code.strip():
            return "", code or ""
        import ast as _m85_ast
        import re as _m85_re
        import textwrap as _m85_tw

        _cands: list[tuple[str, str]] = []

        # ---- ① except 块内裸 pass → 补日志（与既有 silent_exception 同形）----
        try:
            _pat_pass = _m85_re.compile(
                r"(?m)^(\s*)except([^\n:]*):\s*\n\s*pass\b")

            def _repl_pass(_m):
                _exc = _m.group(2).strip()
                # 去掉已存在的 as 别名，避免 "except X as e as e:"
                _exc = _m85_re.sub(r"\s+as\s+\w+\s*$", "", _exc).strip()
                if not _exc:
                    _exc = "Exception"
                return ("%sexcept %s as e:\n%s    self._log("
                        "LogLevel.ERROR, f'异常: {e}')"
                        % (_m.group(1), _exc, _m.group(1)))

            _cand1 = _pat_pass.sub(_repl_pass, code)
            if _cand1 != code:
                _cands.append(("except_log", _cand1))
        except Exception as _cand1_err:
            # ★第87批：m7 门禁（7 个核心文件不得出现静默 except）要求兜底必须留痕。
            #   行为零变化 —— 仍是「丢弃候选①、继续尝试候选②」，仅补一条 DEBUG。
            _module_logger.debug(
                f"[M85 保守修复] 候选生成失败（已忽略）: except 补日志 "
                f"({type(_cand1_err).__name__}: {_cand1_err})")

        # ---- ② requests 无 timeout → 补 timeout ----
        try:
            _pat_http = _m85_re.compile(
                r"requests\.(?:get|post|put|delete|patch|head)\(")
            for _m in _pat_http.finditer(code):
                _depth, _i = 1, _m.end()
                while _i < len(code) and _depth > 0:
                    _ch = code[_i]
                    if _ch == "(":
                        _depth += 1
                    elif _ch == ")":
                        _depth -= 1
                    _i += 1
                if _depth != 0:
                    continue
                _args = code[_m.end():_i - 1]
                if "timeout" in _args:
                    continue
                _sep = ", " if _args.strip() else ""
                _cand2 = (code[:_i - 1] + _sep + "timeout=10" + code[_i - 1:])
                _cands.append(("http_timeout", _cand2))
                break
        except Exception as _cand2_err:
            # ★第87批：同上（m7 门禁）。行为零变化，仅补 DEBUG 留痕。
            _module_logger.debug(
                f"[M85 保守修复] 候选生成失败（已忽略）: requests 补 timeout "
                f"({type(_cand2_err).__name__}: {_cand2_err})")

        # ---- 自检：改写结果必须能解析为合法 Python，否则放弃（保守）----
        for _s, _cand in _cands:
            if not _cand or _cand == code:
                continue
            _ok = False
            for _v in (_cand, _m85_tw.dedent(_cand)):
                try:
                    _m85_ast.parse(_v)
                    _ok = True
                    break
                except Exception as e:
                    silent_exc(e, "nucleus/reasoning/SafeEvolutionExecutor.py:4131:_m85_conservative_fix", level="warning")
                    continue
            if _ok:
                return _s, _cand
        return "", code

    def _m85_record_learning_attempt(self, issue_type, file_path, method,
                                     strategy, res, verify_reason="") -> None:
        """★M85-1：把一次学习尝试追加写入 JSONL（append-only，永不覆盖）。

        落盘：``<project_root>/data/patches/local_learning_attempts.jsonl``
        （任务书 §T-85a.2 指定字段 + patch_id / verify_reason 便于审计）。
        """
        import json as _m85_json
        _dir = os.path.join(self._project_root, "data", "patches")
        os.makedirs(_dir, exist_ok=True)
        _row = {
            "ts": round(time.time(), 3),
            "issue_type": issue_type,
            "file": file_path,
            "method": method,
            "attempted_fix": strategy,
            "strategy": strategy,
            "result": res.get("result", ""),
            "llm_fallback": bool(res.get("llm_fallback", True)),
            "patch_id": res.get("patch_id", ""),
            "verify_reason": verify_reason,
            "version": "M85-1",
        }
        _path = os.path.join(_dir, "local_learning_attempts.jsonl")
        with open(_path, "a", encoding="utf-8") as _fh:
            _fh.write(_m85_json.dumps(_row, ensure_ascii=False) + "\n")

    def _m85_learning_attempt(self, issue, file_path, method, snippet,
                              organ="") -> dict:
        """★M85-1（第85批 T-85a）：对未分类问题做一次保守修复尝试并记录学习结果。

        流程：生成保守修复 → 副本验证 → 通过则**入队待审批**（★不自动应用，
        见 PatchManager._m85_local_low_risk_auto_apply 显式排除 learning_attempt）
        → 无论成败均追加写学习日志。

        ★不改变调用方行为：本方法**不抛异常**，返回值仅供调用方可选使用。
        """
        _type = str((issue or {}).get("type", "") or "")
        _res = {"result": "no_safe_fix", "strategy": "",
                "llm_fallback": True, "patch_id": ""}
        if not self._m85_learning_attempt_enabled():
            _res["result"] = "disabled"
            return _res
        _strategy, _verify_reason = "", ""
        try:
            _strategy, _modified = self._m85_conservative_fix(_type, snippet)
            if not _strategy or not _modified or _modified == snippet:
                _res["result"] = "no_safe_fix"
            else:
                _patch = {
                    "id": "patch_learn_%d_%04x" % (
                        int(time.time()), abs(hash(_modified)) & 0xFFFF),
                    "file": file_path,
                    "method": method,
                    "issue_type": _type,
                    "risk_level": "低",
                    "description": "本地学习尝试(%s): %s" % (_strategy, _type),
                    "original_code": snippet,
                    "modified_code": _modified,
                    "diff_summary": self._generate_diff_summary(snippet, _modified),
                    "trust_score": 55,
                    "confidence": "high",
                    "repair_source": "local_learning",
                    "generated_at": time.time(),
                    "status": "pending",
                    "applied": False,
                    "source": "local_learning",
                    "learning_attempt": True,
                    "attempted_fix": _strategy,
                }
                _verify = self._patch_manager.verify_in_copy(_patch)
                _patch["verification"] = _verify
                _verify_reason = str(_verify.get("reason", ""))[:200]
                if _verify.get("passed"):
                    _res["result"] = "success"
                    _res["patch_id"] = _patch["id"]
                    _res["llm_fallback"] = False
                    try:
                        _patch["status"] = "verified"
                        _patch["needs_runtime_verify"] = True
                        self._patch_manager.save_pending_patch(_patch)
                        _module_logger.info(
                            "[M85 学习尝试] %s.%s (%s) 保守修复通过验证，已入队待审批"
                            "（策略=%s）", organ, method, _type, _strategy)
                    except Exception as _qe:
                        _module_logger.warning(
                            "[M85 学习尝试] 入队失败（已忽略）: %s: %s",
                            type(_qe).__name__, _qe)
                else:
                    _res["result"] = "failed"
            _res["strategy"] = _strategy
        except Exception as _e:
            _module_logger.debug(
                "[M85 学习尝试] 异常已忽略: %s: %s", type(_e).__name__, _e)
            _res["result"] = "error"
            _verify_reason = "%s: %s" % (type(_e).__name__, _e)
        try:
            self._m85_record_learning_attempt(
                _type, file_path, method, _strategy, _res, _verify_reason)
        except Exception as _re:
            _module_logger.debug(
                "[M85 学习尝试] 学习记录写入失败（已忽略）: %s: %s",
                type(_re).__name__, _re)
        return _res


    def _generate_patch(self, plan: dict[str, Any], 
                         self_inspector=None) -> dict[str, Any] | None:
        """
        【v16.0增强】生成包含完整修改前后代码对比的补丁。
        ★第三阶段：先进行多方案评估，选择最优方案后生成补丁。
        
        Returns:
            补丁字典，包含：
            - id: 唯一标识
            - file/method: 目标文件和位置
            - original_code: ★修改前完整代码
            - modified_code: ★修改后完整代码
            - diff_summary: ★变更摘要
            - verification: 验证结果（待填充）
            - status: 补丁状态（pending/verified/approved/applied）
        """
        plan_type = plan.get("type", "")
        organ_name = plan.get("target", "")
        method_name = plan.get("method", "")
        
        # 读取原始代码
        original_code = ""
        file_path = ""
        if self_inspector and organ_name and method_name:
            try:
                method_detail = self_inspector.get_method_body(organ_name, method_name)
                if method_detail:
                    original_code = method_detail.get("body", "")
                    all_organs = self_inspector.scan_all_organs()
                    organ_info = all_organs.get(organ_name, {})
                    file_path = organ_info.get("file_path", "")
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        
        if not file_path or not original_code:
            return None

        # ★P1-1修复（第十批）：生成前预检——同文件同方法已有待审批补丁时，
        #   直接跳过生成，避免「同一处改动反复生成 + LLM 反复调用」。
        #   星轨 9 小时日志实测：PulseLiver/PulseCortex 两个 silent_exception
        #   补丁重复生成 34 次，每次都走「同题择优保留」白白浪费 LLM 调用。
        #   此处从源头拦截：只要同位置已有待审批补丁，就不再重新生成。
        try:
            if self._m94_pending_blocks_regeneration(file_path, method_name):
                _module_logger.debug(
                    f"[补丁预检] 同文件同方法已有待审批补丁，跳过生成: "
                    f"{os.path.basename(file_path) if isinstance(file_path, str) else file_path}:{method_name}")
                return None
        except Exception as e:
            # 预检异常不阻断生成（保守放行，交给入队阶段的去重兜底）
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★PHASE17-A3（2026-09-07）：FailureTracker 升级预检——填补「只写不读」缺口。
        #   FailureTracker 自 v25 起就在 apply_all_pending 里记账（record_failure/record_success），
        #   但全项目**零消费方**：should_escalate() 从未被调用，注释宣称的「连续失败 3 次升级」
        #   从未真正生效（P2-4）。结果是同一位置同一类问题会被反复用同一策略生成补丁、
        #   反复失败，白白消耗 LLM 调用与验证算力。
        #   此处补上消费端：达到升级阈值 → 跳过自动生成，转「需人工介入」，并输出 WARNING。
        #   零冲突：仅在预检阶段拦截，不改动生成/验证/应用任何既有逻辑；
        #   计数器异常时保守放行（与相邻预检一致）。
        try:
            from nucleus.reasoning.FailureTracker import get_failure_tracker
            _ft = get_failure_tracker(self._patch_manager.get_project_root())
            _ft_sig = _ft.build_signature(file_path, str(plan_type or "unknown"))
            if _ft.should_escalate(_ft_sig):
                _ft_count = _ft.get_failure_count(_ft_sig)
                _module_logger.warning(
                    f"[补丁升级] 该问题已连续失败 {_ft_count} 次达到升级阈值，"
                    f"跳过自动生成，转需人工介入: "
                    f"{os.path.basename(file_path) if isinstance(file_path, str) else file_path}"
                    f":{method_name} (类型={plan_type})")
                return None
        except Exception as e:
            # 预检异常不阻断生成（保守放行）
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 根据方案类型生成修改后代码
        # ★P1补丁覆盖扩展（3→12）：12类高置信度纯文本替换自动修复。
        # 设计原则：只扩「语义明确、纯文本替换、能安全验证」的类型，高危/需上下文判断的不碰。
        # ★置信度标记：12 类均为「高置信度自动修复」（语义确定、替换无歧义）。
        # ★方案生成智能性(P1红项)：本地规则从「固定缩进单模板」升级为「多策略择优」——
        #   ① 正则匹配任意缩进（消除 12 空格硬编码的形态盲区）；
        #   ② 同类提供多个修复策略按优先级尝试，第一个命中者采用；
        #   ③ 补丁记录实际采用的策略（applied_strategy），供人工审查/审计。
        _applied_strategy = "unknown"
        # ★策略自进化(元学习·长期深化): 依据同类问题历史策略效果调整路由——
        #   无历史/数据不足 → 维持规则优先（零冲突）；历史显示规则策略失败率高
        #   (total>=3 且通过率<=33%) → 跳过规则直接 LLM 升级（LLM 决策核心前置化）。
        _hist_sig = self._patch_manager.get_strategy_effectiveness(plan_type)
        if _hist_sig.get("rule_bad"):
            _module_logger.info(
                f"[策略自进化] 历史策略效果显示 {plan_type} 规则策略失败率高，直接 LLM 升级"
                f"({_hist_sig.get('summary', '')})")
            return self._generate_llm_patch(
                plan, self_inspector, original_code, file_path, method_name)
        if "silent_exception" in plan_type:
            import re as _re_se
            # 策略A：except Exception: pass（无 as）→ 限定 + 加日志（任意缩进）
            _pat_se_a = _re_se.compile(r'(?m)^(\s*)except\s+Exception\s*:\s*\n\s*pass')
            # 策略B：except Exception as e: pass → 保留 e + 加日志（任意缩进）
            _pat_se_b = _re_se.compile(r'(?m)^(\s*)except\s+Exception\s+as\s+\w+\s*:\s*\n\s*pass')
            _tmp_b = _pat_se_b.sub(
                lambda m: m.group(1) + "except Exception as e:\n" + m.group(1) + "    self._log(LogLevel.ERROR, f'异常: {e}')",
                original_code)
            modified_code = _pat_se_a.sub(
                lambda m: m.group(1) + "except Exception as e:\n" + m.group(1) + "    self._log(LogLevel.ERROR, f'异常: {e}')",
                original_code)
            if _tmp_b != original_code:
                # 策略B 命中（含 as e 形态），B 优先级更高（保留语义）
                modified_code = _tmp_b
                _applied_strategy = "silent_exception_as_e"
            elif modified_code != original_code:
                _applied_strategy = "silent_exception_plain"
            else:
                _applied_strategy = "silent_exception_no_match"
        elif "bare_except" in plan_type:
            import re as _re_be
            # 裸 except: → 限定 Exception + 加日志（任意缩进）
            # ★v25.1修复: 匹配完整 except:\n pass 块（含换行），避免pass被拼接到日志行
            _pat_be = _re_be.compile(r'(?m)^(\s*)except\s*:\s*\n\s*pass')
            modified_code = _pat_be.sub(
                lambda m: m.group(1) + "except Exception as e:\n" + m.group(1) + "    self._log(LogLevel.ERROR, f'异常: {e}')",
                original_code)
            # 兜底：没有匹配到pass块时，只替换except:行（不消耗换行）
            if modified_code == original_code:
                _pat_be2 = _re_be.compile(r'(?m)^(\s*)except\s*:')
                modified_code = _pat_be2.sub(
                    lambda m: m.group(1) + "except Exception as e:\n" + m.group(1) + "    self._log(LogLevel.ERROR, f'异常: {e}')",
                    original_code)
            _applied_strategy = "bare_except_qualified" if modified_code != original_code else "bare_except_no_match"
        elif "status_request_duplicate" in plan_type:
            modified_code = "    def _on_status_request(self):\n        return self.get_stats()"
            _applied_strategy = "status_dup_unify"
        elif "unbounded_deque" in plan_type:
            # 无界 deque → 有界（maxlen=1000 保守兜底，防内存无限膨胀，非业务阈值）
            modified_code = original_code.replace("deque()", "deque(maxlen=1000)")
            _applied_strategy = "unbounded_deque_maxlen"
        elif "thread_no_daemon" in plan_type:
            _applied_strategy = "thread_no_daemon_guard"
            # 未设 daemon 的线程 → 守护线程（后台常驻线程标准实践，退出自动回收）。
            # ★精确替换：只替换「无 daemon 参数」的 threading.Thread( 调用，避免误伤已设 daemon 的线程。
            # 用回调函数逐处判断：找到每个 threading.Thread( 到其第一个 ) 之间的文本，无 daemon 才加。
            import re as _re

            def _add_daemon(m: "_re.Match[str]") -> str:
                _prefix = m.group(0)
                # 从当前匹配位置往后找第一个 )（与检测器 _check_thread_no_daemon 逻辑一致）
                _call_end = original_code.find(')', m.end() - 1)
                if _call_end < 0:
                    return _prefix
                _call_text = original_code[m.start():_call_end + 1]
                if 'daemon' in _call_text:
                    return _prefix  # 已有 daemon，不重复添加
                return 'threading.Thread(daemon=True, '

            modified_code = _re.sub(
                r'threading\.Thread\(',
                _add_daemon,
                original_code,
            )
        elif "no_timeout_http" in plan_type:
            _applied_strategy = "no_timeout_http_guard"
            # 无超时网络请求 → 加 timeout=30（通用保守兜底，避免线程永久挂死）。
            # ★v25.1修复: 匹配完整调用 requests.get(...)，在闭合括号前插入timeout=30，
            #   避免生成 requests.get(timeout=30, url) 这种"关键字参数在位置参数前"的语法错误。
            import re as _re

            def _add_timeout(m: "_re.Match[str]") -> str:
                _full_call = m.group(0)  # 完整调用如 'requests.get(url)'
                if 'timeout' in _full_call:
                    return _full_call  # 已有 timeout，不改动
                # 在闭合括号前插入 , timeout=30
                return _full_call[:-1] + ', timeout=30)'

            modified_code = _re.sub(
                r'requests\.get\([^)]*\)',
                _add_timeout,
                original_code,
            )
        elif "mutable_default_arg" in plan_type:
            import re as _re_mda
            # 可变默认参数：def f(x=[]) → def f(x=None)
            _pat_list = _re_mda.compile(r'(def\s+\w+\([^)]*=\s*)\[\](\s*[,)])')
            modified_code = _pat_list.sub(r'\1None\2', original_code)
            if modified_code != original_code:
                _applied_strategy = "mutable_default_list_none"
            else:
                _pat_dict = _re_mda.compile(r'(def\s+\w+\([^)]*=\s*)\{\}(\s*[,)])')
                modified_code = _pat_dict.sub(r'\1None\2', original_code)
                _applied_strategy = "mutable_default_dict_none" if modified_code != original_code else "mutable_default_no_match"
        elif "comparison_with_none" in plan_type:
            import re as _re_cwn
            _pat_eq = _re_cwn.compile(r'(\w+)\s*==\s*None')
            _pat_ne = _re_cwn.compile(r'(\w+)\s*!=\s*None')
            modified_code = _pat_eq.sub(r'\1 is None', original_code)
            modified_code = _pat_ne.sub(r'\1 is not None', modified_code)
            _applied_strategy = "comparison_none_is" if modified_code != original_code else "comparison_none_no_match"
        elif "boolean_comparison" in plan_type:
            import re as _re_bc
            _pat_true = _re_bc.compile(r'(\w+)\s*==\s*True')
            _pat_false = _re_bc.compile(r'(\w+)\s*==\s*False')
            modified_code = _pat_true.sub(r'\1', original_code)
            modified_code = _pat_false.sub(r'not \1', modified_code)
            _applied_strategy = "boolean_direct" if modified_code != original_code else "boolean_no_match"
        elif "os_path_join" in plan_type:
            import re as _re_opj
            # 简单路径拼接："a" + "/" + "b" → os.path.join("a", "b")
            _pat = _re_opj.compile(r'"([^"]+)"\s*\+\s*"/"\s*\+\s*"([^"]+)"')
            modified_code = _pat.sub(r'os.path.join("\1", "\2")', original_code)
            _applied_strategy = "os_path_join_simple" if modified_code != original_code else "os_path_join_no_match"
        elif "print_instead_of_log" in plan_type:
            import re as _re_pil
            _pat = _re_pil.compile(r'(?m)^(\s*)print\((.+)\)\s*$')
            modified_code = _pat.sub(r'\1self._log(LogLevel.INFO, \2)', original_code)
            _applied_strategy = "print_to_log_info" if modified_code != original_code else "print_to_log_no_match"
        elif "fstring_preferred" in plan_type:
            import re as _re_fp
            _pat = _re_fp.compile(r'"([^"]*)\{([^}]+)\}([^"]*)"\.format\(([^)]+)\)')
            def _to_fstring(m):
                return 'f"' + m.group(1) + '{' + m.group(2) + '}' + m.group(3) + '"'
            modified_code = _pat.sub(_to_fstring, original_code)
            _applied_strategy = "format_to_fstring" if modified_code != original_code else "fstring_no_match"
        elif "bare_return_none_in_except" in plan_type:
            # ★主线第76批 T3：except 块内裸 `return None`（无日志）→ 补一行 WARNING 日志。
            #   保守约束：只处理带 `as <名称>` 的 except（有异常对象可记）；
            #   无 `as` 时不动，绝不臆造变量名。开关关闭即退回「转 LLM」旧行为。
            import re as _re_brne
            _brne_on = True
            try:
                import config as _cfg_brne
                _brne_on = bool(getattr(_cfg_brne, "ENABLE_LOCAL_FIX_BARE_RETURN_NONE", True))
            except Exception:
                _brne_on = True
            if not _brne_on:
                _applied_strategy = "bare_return_none_disabled"
            else:
                _pat_brne = _re_brne.compile(
                    r'(?m)^([ \t]*)except[ \t]+([^\n:]+?)[ \t]+as[ \t]+(\w+):[ \t]*\n'
                    r'([ \t]+)return None[ \t]*$')

                def _brne_repl(_m):
                    return ("%sexcept %s as %s:\n"
                            "%sself._log(LogLevel.WARNING, "
                            "f\"[异常已忽略] {type(%s).__name__}: {%s}\")\n"
                            "%sreturn None" % (_m.group(1), _m.group(2), _m.group(3),
                                               _m.group(4), _m.group(3), _m.group(3),
                                               _m.group(4)))

                modified_code = _pat_brne.sub(_brne_repl, original_code)
                _applied_strategy = ("bare_return_none_add_log"
                                     if modified_code != original_code
                                     else "bare_return_none_no_match")
        else:
            # ★W4修复：高危类型（unsafe_eval/subprocess_shell/sql_injection）永不
            #   进入自动修复链路（含 LLM 回退），保持「高危不越界」边界，交由人工处理。
            if plan_type in _HIGH_RISK_NON_FIXABLE:
                return None
            # ★进化闭环升级(阶段A): 通用问题不再直接放弃，回退到 LLM 生成针对性补丁。
            #   规则表覆盖 12 类已知模式，遇到第 13 类及以后的问题，交给 LLM 做开放域
            #   语义理解与修复。LLM 补丁标记 repair_source="llm"、confidence="medium"，
            #   走完整验证链路（副本验证 + 回归 + 动态测试）通过后才允许自动应用。
            return self._generate_llm_patch(plan, self_inspector, original_code, file_path, method_name)

        # ★替换有效性校验：若替换未命中（modified_code == original_code），
        # 说明检测器误报或代码形态不匹配，不应生成空补丁（避免「无实质性变更」的假补丁）。
        if modified_code == original_code:
            return None
        
        # 生成变更摘要
        diff_summary = self._generate_diff_summary(original_code, modified_code)
        
        # ★P1-2(2026-09-04)：补丁质量评估——最小化改动约束 + 零冲突检查
        _quality = self._assess_patch_quality(original_code, modified_code, file_path)
        if not _quality.get("pass", True):
            _module_logger.info(
                f"[补丁质量] 拒绝低质量补丁: {plan_type} {method_name}, "
                f"得分={_quality['score']}, 问题={_quality['issues'][:2]}")
            return None
        if _quality.get("issues"):
            _module_logger.info(
                f"[补丁质量] {plan_type} {method_name} 质量警告: "
                f"得分={_quality['score']}, {_quality['issues'][0]}")
        
        # ★FIX(A3): 从方案数值评分推导风险等级与信任分（此前 risk_level 恒为"低"，trust_score 硬编码）
        _risk_score = plan.get("risk_score", 2)
        _risk_map = {1: "极低", 2: "低", 3: "中等", 4: "高"}
        risk_level = plan.get("risk_level") or _risk_map.get(_risk_score, "低")
        _benefit = plan.get("benefit_score", _DEF_BENEFIT_SCORE)
        trust_score = min(95, max(10, int(_benefit) * 10))

        # ★第九批 B-4：复发检测——同一个问题（文件+类型+方法）修完又冒出来时，
        #   说明上次根本没修好或被后续改动带回来了。此处提升其优先级
        #   （信任分 +boost，上限 95），让它在这一轮有机会被真正处理。
        try:
            import config as _cfg_rec
            if getattr(_cfg_rec, "ENABLE_EVOLUTION_EFFECT_VERIFY", False):
                from nucleus.evolution.EvolutionEffectVerifier import (
                    EvolutionEffectVerifier as _EV,
                )
                from nucleus.evolution.EvolutionEffectVerifier import (
                    get_effect_verifier,
                )
                _rec = get_effect_verifier().check_recurrence(
                    _EV.issue_signature(
                        {"type": plan_type, "file": file_path, "method": method_name}))
                _boost = int(_rec.get("priority_boost", 0) or 0)
                if _boost:
                    trust_score = min(95, trust_score + _boost)
                    _module_logger.info(
                        f"[复发检测] {file_path}:{method_name} {_rec.get('reason')}，"
                        f"信任分 +{_boost} → {trust_score}")
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {
            "id": f"patch_{int(time.time())}_{hash(original_code) & 0xFFFF:04x}",
            "file": file_path,
            "method": method_name,
            "issue_type": plan_type,
            "risk_level": risk_level,
            "description": plan.get("description", ""),
            "original_code": original_code,
            "modified_code": modified_code,
            "diff_summary": diff_summary,
            "trust_score": trust_score,
            # ★P1置信度标记：12 类本地规则修复统一标记「高置信度自动修复」，
            # 与 LLM 补丁（_submit_llm_patch）的「中置信度」区分，便于审计与灰度。
            "confidence": "high",
            "repair_source": "local_rule",
            # ★方案生成智能性：记录实际采用的修复策略（人工审查/审计可见）
            "applied_strategy": _applied_strategy,
            # ★根因分析深度：记录根因陈述（人工审查可见"为什么"）
            "root_cause_analysis": self._infer_root_cause(plan),
            "generated_at": time.time(),
            "status": "pending",
            "verification": None,
            "applied": False,
            "applied_at": 0,
        }
    
    def _assess_patch_quality(self, original_code: str, modified_code: str,
                                file_path: str = "") -> dict[str, Any]:
        """★P1-2(2026-09-04)：补丁质量评估——最小化改动约束 + 零冲突检查。

        返回质量评分和问题列表，不合格的补丁应降级或拒绝。
        """
        _issues = []
        _score = 100  # 满分100，扣分制

        # 1. 最小化改动约束：修改行数比例
        try:
            _orig_lines = original_code.count("\n") + 1
            _mod_lines = modified_code.count("\n") + 1
            _added = max(0, _mod_lines - _orig_lines)
            _ratio = _added / max(1, _orig_lines)
            if _ratio > 0.5:
                _issues.append(f"新增行数过多({_added}行，占比{_ratio:.0%})，可能违反最小化改动")
                _score -= 20
            elif _ratio > 0.3:
                _issues.append(f"新增行数偏多({_added}行，占比{_ratio:.0%})")
                _score -= 10
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 2. 零冲突检查：语法正确性
        # ★v25.2修复(2026-09-04): 用ast.parse替代临时文件+py_compile，
        # 改进包装逻辑：先class+method（支持self），再function（无self），
        # 最后直接编译。避免临时文件IO和包装缩进不一致的问题。
        try:
            import ast as _ast_qc
            import textwrap as _textwrap_qc

            def _try_ast_parse(code: str) -> tuple:
                try:
                    # ★第83批 T-a2：ast.parse 不传 filename 时告警显示 <unknown>，
                    #   无法定位是哪段 LLM 补丁代码。统一标注来源文件。
                    _ast_qc.parse(code, filename="<llm-patch>")
                    return (True, None)
                except SyntaxError as _e:
                    return (False, f"{_e.msg} (line {_e.lineno})")

            # 第一次尝试：直接ast.parse(适用于完整文件替换)
            _ok, _err = _try_ast_parse(modified_code)
            if not _ok:
                # 统一dedent（去除公共缩进）
                _dedented = _textwrap_qc.dedent(modified_code)
                # 第二次尝试：包装class+method(适用于含self的完整方法体)
                _wrapped_cls = "class _PatchQC:\n    def _check(self):\n" + _textwrap_qc.indent(_dedented, "        ")
                _ok2, _err2 = _try_ast_parse(_wrapped_cls)
                if not _ok2:
                    # 第三次尝试：包装function(适用于无self的代码片段)
                    _wrapped_fn = "def _patch_qc_check():\n" + _textwrap_qc.indent(_dedented, "    ")
                    _ok3, _err3 = _try_ast_parse(_wrapped_fn)
                    if not _ok3:
                        # 第四次尝试：如果modified_code本身包含def行，直接用dedented
                        if "def " in _dedented or "class " in _dedented:
                            _ok4, _err4 = _try_ast_parse(_dedented)
                            if not _ok4:
                                _final_err = _err4 or _err3 or _err2 or _err
                                _issues.append(f"语法错误: {_final_err}")
                                _score -= 50
                        else:
                            _final_err = _err3 or _err2 or _err
                            _issues.append(f"语法错误: {_final_err}")
                            _score -= 50
        except Exception as _syntax_err:
            _issues.append(f"语法检查异常: {_syntax_err}")
            _score -= 10

        # 3. 零冲突检查：是否引入新的沉默异常
        try:
            import re as _re_qc
            # 检查修改后的代码是否新增了裸except或pass吞异常
            _new_bare = len(_re_qc.findall(r"except\s*:", modified_code))
            _old_bare = len(_re_qc.findall(r"except\s*:", original_code))
            if _new_bare > _old_bare:
                _issues.append("引入了新的裸except，可能掩盖错误")
                _score -= 15
            # 检查是否新增了except...pass
            _new_silent = len(_re_qc.findall(r"except[^:]*:\s*\n\s*pass", modified_code))
            _old_silent = len(_re_qc.findall(r"except[^:]*:\s*\n\s*pass", original_code))
            if _new_silent > _old_silent:
                _issues.append("引入了新的沉默异常(pass)，可能掩盖错误")
                _score -= 15
        except Exception as e:
            _module_logger.debug(f"数据处理异常已忽略: {type(e).__name__}: {e}")

        # 4. 零冲突检查：是否删除了关键逻辑（return/raise/log）
        try:
            import re as _re_logic
            _old_returns = len(_re_logic.findall(r"\breturn\b", original_code))
            _new_returns = len(_re_logic.findall(r"\breturn\b", modified_code))
            if _new_returns < _old_returns:
                _issues.append(f"return语句减少({_old_returns}→{_new_returns})，可能删除了关键逻辑")
                _score -= 10
        except Exception as e:
            _module_logger.debug(f"数据处理异常已忽略: {type(e).__name__}: {e}")

        # 5. 完整性检查：修改后代码非空且有实质内容
        if not modified_code or len(modified_code.strip()) < 10:
            _issues.append("修改后代码为空或过短")
            _score -= 30

        _quality = "high" if _score >= 80 else ("medium" if _score >= 60 else "low")
        return {
            "score": max(0, _score),
            "quality": _quality,
            "issues": _issues,
            "pass": _score >= 60,  # 60分以下视为不合格
        }

    @staticmethod
    def _issue_identity(issue: dict[str, Any]) -> tuple[str, str, str]:
        """★D3修复（P1，2026-09-05）：问题身份键——统一供「分组」与「去重」两处使用。

        背景（本方法是 T3 修复的**遗漏补漏**）：
            T3 只修了上游 EvolutionLoop 的去重键，但本文件的下游还有**两道**
            依赖 file/method 的键构造：
              · _plan_multi_step_repair 的分组键（原 :2140）
              · repair_with_distillation 展开后的去重键（原 :568）
            日志类问题由 LogAnalyzer 产出，来源是**普通日志行、没有 Traceback**，
            因此 file 与 method 天然为空（只有从 `[ScriptExecutor]` 前缀提取到的 organ）。
            于是两个键双双退化：
                分组键 ("", "")          → 4 个问题合并为 1 步
                去重键 ("", "", "ERROR") → 再坍缩为 1 个
            生产实测（pulse.log 12:44:19）：「发现4个问题」却「处理前1个」，
            与本退化路径逐字吻合。

        修复策略：
            file/method 任一有值时维持原语义（保证代码类问题行为不变）；
            两者皆空时退化为「organ + type + 描述摘要」，
            确保不同的日志类问题仍能被区分，不再被合并成一个。

        回归提醒：
            本修复让统计变诚实（4 个问题会被如实处理 4 次），
            但**并不能提高修复成功率**——这些日志类问题依然没有 method，
            依然取不到代码片段。修复率问题的根因另在别处（见 D6）。
        """
        _f = str(issue.get("file", "") or "")
        _m = str(issue.get("method", "") or "")
        if _f or _m:
            # ★第117批 T-117d①：file 先做形制归一（详见 _normalize_file_key），
            #   使「反斜杠相对 / 正斜杠相对 / 绝对」三种形制收敛为同一键。
            return (_normalize_file_key(_f), _m,
                    str(issue.get("type", "") or ""))
        # 无代码位置：退化为 器官 + 类型 + 描述摘要
        _o = str(issue.get("organ", "") or "")
        _t = str(issue.get("type", "") or "")
        _d = str(issue.get("description", "") or issue.get("message", "") or "")
        return (f"organ:{_o}", f"type:{_t}", _d[:120])

    @classmethod
    def _cooldown_key(cls, issue: dict[str, Any]) -> str:
        """★PHASE13-P1-3：僵尸问题的冷却指纹。

        直接复用 `_issue_identity` 的结果拼字符串，保证与「分组 / 去重」
        使用同一套身份语义——否则会出现同一问题在去重里算一个、
        在冷却表里算两个的错位。
        """
        return "|".join(cls._issue_identity(issue))

    def _cooldown_ttl_for(self, reason: str) -> float:
        """★PHASE13-P1-3：按原因取冷却时长（秒）。

        reason 形如「高危·安全拦截(unsafe_eval)」，括号内是具体类型，
        故用前缀匹配而非全等匹配。匹配不到就用 `_default`，
        再兜底 1 小时——宁可冷却短一点（多跑一次），不可无限期冻结。
        """
        for _prefix, _secs in self._no_fix_cooldown_secs.items():
            if _prefix == "_default":
                continue
            if reason.startswith(_prefix):
                return _secs
        return float(self._no_fix_cooldown_secs.get("_default", 3600.0))

    # ========== ★第114批 T-114a（治病·断2/断6）冷却落盘与验证失败登记 ==========
    def _m114a_cooldown_path(self) -> str:
        """惰性解析冷却落盘路径（data/evolution/cooldown.json）。"""
        if not isinstance(self, SafeEvolutionExecutor):
            # ★T-115d：占位/Dummy 实例不应承担冷却落盘职责
            return ""
        if self._m114a_cooldown_file is None:
            _root = getattr(self, "_project_root", None) or os.getcwd()
            self._m114a_cooldown_file = os.path.join(
                _root, "data", "evolution", "cooldown.json")
        return self._m114a_cooldown_file

    def _m114a_load_cooldown(self) -> None:
        """★断6：从 cooldown.json 加载冷却（存剩余秒数，跨重启可续）。"""
        if not isinstance(self, SafeEvolutionExecutor):
            # ★T-115d：占位/Dummy 实例不应承担冷却落盘职责
            return
        try:
            import json as _json
            _path = self._m114a_cooldown_path()
            if not os.path.exists(_path):
                return
            with open(_path, "r", encoding="utf-8") as _f:
                _data = _json.load(_f)
            if not isinstance(_data, dict):
                return
            _now = time.monotonic()
            for _fp, _v in _data.items():
                if not isinstance(_v, (list, tuple)) or len(_v) < 1:
                    continue
                _rem = float(_v[0])
                if _rem <= 0:
                    continue
                self._no_fix_cooldown[_fp] = _now + _rem
                self._no_fix_cooldown_rounds[_fp] = int(_v[1]) if len(_v) > 1 else 0
            _n = len(self._no_fix_cooldown)
            if _n:
                _module_logger.info(
                    f"[冷却落盘] 重启加载 {_n} 条验证失败冷却记录（跨重启续期）")
        except Exception as _e:
            _module_logger.debug(f"[冷却落盘] 加载失败(已忽略): {type(_e).__name__}: {_e}")

    def _m114a_save_cooldown(self) -> None:
        """★断6：把冷却表（剩余秒数 + 升级轮数）落盘到 cooldown.json。"""
        if not isinstance(self, SafeEvolutionExecutor):
            # ★T-115d：占位/Dummy 实例不应承担冷却落盘职责
            return
        try:
            import json as _json
            _now = time.monotonic()
            _data: dict[str, list] = {}
            for _fp, _dl in self._no_fix_cooldown.items():
                _rem = _dl - _now
                if _rem <= 0:
                    continue
                _data[_fp] = [_rem, int(self._no_fix_cooldown_rounds.get(_fp, 0))]
            _path = self._m114a_cooldown_path()
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            with open(_path, "w", encoding="utf-8") as _f:
                _json.dump(_data, _f, ensure_ascii=False)
        except Exception as _e:
            _module_logger.debug(f"[冷却落盘] 保存失败(已忽略): {type(_e).__name__}: {_e}")

    def _m114a_register_verify_failure(self, fp: str, detail: str = "") -> None:
        """★断2：验证失败的题登记冷却，避免每轮全量重扫重问。
        同因累计满 3 轮升级到 86400s（日级复检）；落盘见 _m114a_save_cooldown。
        """
        if not isinstance(self, SafeEvolutionExecutor):
            # ★T-115d：占位/Dummy 实例不应承担冷却落盘职责
            return
        try:
            _rnd = int(self._no_fix_cooldown_rounds.get(fp, 0)) + 1
            self._no_fix_cooldown_rounds[fp] = _rnd
            _reason = "验证失败·3轮" if _rnd >= 3 else "验证失败"
            _ttl = self._cooldown_ttl_for(_reason)
            self._no_fix_cooldown[fp] = time.monotonic() + _ttl
            self._m114a_save_cooldown()
            _module_logger.info(
                f"[验证失败冷却] 指纹={fp[:80]} 验证未通过({detail})，"
                f"同因累计{_rnd}轮→冷却{int(_ttl)}s"
                f'{"（升86400s/日级复检）" if _rnd >= 3 else ""}')
        except Exception as _e:
            _module_logger.debug(f"[验证失败冷却] 登记异常(已忽略): {type(_e).__name__}: {_e}")

    def _m114a_clear_ratchet(self, fp: str, reason: str = "") -> None:
        """★第117批 T-117b（N1）：成功侧出清 —— 同指纹连败计数归零（棘轮自愈）。

        背景（烛微 §2-N1 实测）：断5 换源后判据源 = `_no_fix_cooldown_rounds`，
        计数只在 `_m114a_register_verify_failure` 里**递增**，成功侧**无任何出清点**。
        于是某指纹累计 >=3 连败后被 `_m114a_should_skip_ask` 永久跳过；计数还会
        随 `_m114a_save_cooldown` 落盘 ⇒ **跨重启永续**，棘轮单向、无自愈出口。
        而该函数 docstring 自称判据是「无成功记录」——成功了也不重置，名实不符。

        修法：修复/验证成功的分支调用本方法，把该指纹连败计数清零，
        棘轮从此可逆（「3 连败 → 1 次成功 → 重新获得 LLM 问询资格」）。

        ★刻意不动 `_no_fix_cooldown`（deadline 表）：那是「冷却隔离」的另一套
        机制（:1447 到期自动解冻），与断5 棘轮无关；并入本批会扩大改动面。
        """
        if not isinstance(self, SafeEvolutionExecutor):
            # ★T-115d：占位/Dummy 实例不应承担冷却落盘职责
            return
        try:
            _had = int(self._no_fix_cooldown_rounds.get(fp, 0))
            if _had <= 0:
                return  # 无连败记录：零开销短路，不产生日志噪音
            self._no_fix_cooldown_rounds.pop(fp, None)
            self._m114a_save_cooldown()
            _module_logger.info(
                f"[棘轮重置] 指纹={fp[:80]} 连败清零（{_had}→0）"
                f"{('·' + reason) if reason else ''}"
                f"——该指纹恢复本轮 LLM 问询资格")
        except Exception as _e:
            _module_logger.debug(
                f"[棘轮重置] 出清异常(已忽略): {type(_e).__name__}: {_e}")

    def _m114a_should_skip_ask(self, issue: dict[str, Any]) -> bool:
        """★断5（T-115d 指纹级降档）：同指纹问题若已累计 ≥3 轮验证失败且无成功记录，
        跳过本轮 LLM 问询，阻断确定性回环烧 LLM。

        判据源改用断2/断6 维护的 _no_fix_cooldown_rounds（真实指纹计数，非空集 hub），
        指纹键与 _issue_identity（file|method|type 三元组）同构（_cooldown_key），
        避免原"类型级近似"造成的「整类沉默死锁」；拦截记 INFO 可审计。
        """
        try:
            _fp = self._cooldown_key(issue)  # file|method|type 同断2
            _rounds = int(self._no_fix_cooldown_rounds.get(_fp, 0))
            if _rounds >= 3:
                # 同指纹已累计 ≥3 轮验证失败且无成功记录 → 跳过本轮 LLM 问询
                _module_logger.info(
                    f"[指纹咨询硬闸] 指纹={_fp[:80]} 同指纹验证失败累计{_rounds}轮无成功记录，"
                    f"跳过本轮 LLM 问询（指纹级·源=_no_fix_cooldown_rounds）")
                return True
        except Exception as _e:
            _module_logger.debug(
                f"[指纹咨询硬闸] 判定异常(降级为照常问询): {type(_e).__name__}: {_e}")
        return False

    @staticmethod
    def _plan_group_key(issue: dict[str, Any]) -> tuple[str, ...]:
        """★D3修复（P1，2026-09-05）：多步规划的「合并粒度」键。

        与 _issue_identity 的分工：
            _issue_identity → 决定「哪两条问题是同一个」（去重，逐条判定）
            _plan_group_key → 决定「哪些问题并进同一步」（分组，批量合并）

        两者粒度刻意不同：
          · 代码类问题（file/method 有值）→ 二元组 (file, method)。
            同一方法的多条问题合并成一步，是原设计意图，必须保留。
          · 日志类问题（file/method 皆空）→ 三元组 (organ, type, 描述摘要)。

        为什么日志类不能也取二元组：
            若取 (organ, type)，则「同一器官 + 同为 ERROR」的所有日志问题
            仍会被并成一大组。生产实测 4 条问题（描述各不相同）会就此
            塌缩为 2 步，D3 的展开目标依然落空。补上第三维「描述摘要」
            后，4 条问题按描述差异如实展开为 3 步。

        边界：描述摘要取前 120 字符，真正的重复描述仍会正确合并，
        不会出现「同一次失败被拆成多步」的反向问题。
        """
        _f = str(issue.get("file", "") or "")
        _m = str(issue.get("method", "") or "")
        if _f or _m:
            return (_f, _m)
        # 无代码位置：交由 _issue_identity 产出含描述摘要的三元组
        return SafeEvolutionExecutor._issue_identity(issue)

    def _plan_multi_step_repair(self, issues: list[dict[str, Any]],
                                  self_inspector=None) -> list[dict[str, Any]]:
        """★P1-2(2026-09-04)：多步规划——复杂问题拆解为有序修复序列。

        分析问题间的依赖关系，按依赖顺序排列，确保先修复基础问题再修复依赖问题。
        返回有序的问题列表，每个问题带step_index和depends_on字段。
        """
        if not issues:
            return []

        # 1. 按文件+方法分组，同一方法的问题合并为一步
        # ★R1纵深防御：上游（main.py 自主进化循环）此前误把
        #   discover_all_issues 的返回值 dict 当 list 传入，
        #   遍历该 dict 得到的是键名字符串，.get() 抛 AttributeError，
        #   导致整个修复流程中断（7小时13次，自主进化完全失效）。
        #   此处过滤非 dict 元素：单个脏数据只被跳过并记录，
        #   不再中断整批修复（符合「单点失败不影响全局」的容错原则）。
        _groups = {}
        for _issue in issues:
            if not isinstance(_issue, dict):
                _module_logger.warning(
                    f"[多步规划] 跳过非字典问题项(类型={type(_issue).__name__}, "
                    f"值={str(_issue)[:60]!r})，上游传入了非预期的数据结构")
                continue
            # ★D3：改用 _plan_group_key。
            #   代码类维持 (file, method) 两维（同一方法合并为一步）；
            #   日志类自动带上描述摘要，不再全部挤进 ("", "") 一组。
            _key = self._plan_group_key(_issue)
            if _key not in _groups:
                _groups[_key] = []
            _groups[_key].append(_issue)

        # 2. 分析依赖关系：
        #    - 基础类型（语法/格式）优先
        #    - 依赖类型（逻辑/架构）在后
        _base_types = {"print_instead_of_log", "fstring_preferred", "comparison_with_none",
                       "boolean_comparison", "os_path_join", "mutable_default_arg"}
        _medium_types = {"silent_exception", "bare_except", "no_timeout_http",
                         "unbounded_deque", "thread_no_daemon"}
        _complex_types = {"status_request_duplicate", "architecture", "performance",
                          "deadlock", "race_condition", "memory_leak"}

        def _priority(issue):
            _t = issue.get("type", "")
            if _t in _base_types:
                return 0
            if _t in _medium_types:
                return 1
            if _t in _complex_types:
                return 2
            return 1

        # 3. 构建有序修复序列
        _steps = []
        _step_index = 0
        # 按优先级排序：基础→中等→复杂
        # ★第105批 T-105b（P1）：打破「延期标记只写不读」导致的永久饥饿。
        #   上一轮被截断丢弃的问题会打 _deferred_from_prev_round=True，但此前该标记
        #   从无读取点 → 下一轮排序仍靠前、仍被截掉（实测 8 位置永久轮不上）。
        #   此处补读取点：以「是否延期」作次级排序键，延期组排到同优先级队尾，
        #   让当轮新鲜问题优先入窗；当新鲜问题被消化后，延期组自然升入窗口被处理。
        def _group_is_deferred(_gis):
            return any(isinstance(_x, dict) and _x.get("_deferred_from_prev_round")
                       for _x in _gis)
        _sorted_groups = sorted(
            _groups.items(),
            key=lambda g: (min(_priority(i) for i in g[1]),
                           1 if _group_is_deferred(g[1]) else 0))
        _n_deferred = sum(1 for _, _g in _sorted_groups if _group_is_deferred(_g))
        if _n_deferred:
            _module_logger.info(
                f"[多步规划] T-105b 读取延期标记：{_n_deferred} 组"
                f"_deferred_from_prev_round=True 已排到同优先级队尾（打破永久饥饿）")
        # ★P0-1修复（第十批）：分组键可能是二元组(代码类)或三元组(日志类)。
        #   _plan_group_key 对「无 file/method 的日志类问题」返回
        #   _issue_identity 的三元组 (organ, type, 描述摘要)，
        #   原代码 `for (_file, _method), _group_issues in ...` 硬编码二元组解包，
        #   一旦混入日志类问题即抛 `too many values to unpack (expected 2)`。
        #   第7轮起「发现18个问题」较前6轮「17个」多出1个日志类问题，
        #   触发此路径导致自主进化修复流程连续8轮中断。
        #   修复：统一按「前两维 file/method」取值，第三维(若有)仅作区分，
        #   不再参与解包 —— 既保留 D3「日志类不塌缩」意图，又不崩溃。
        for _key, _group_issues in _sorted_groups:
            if not _group_issues:
                continue
            # 分组键可能是二元组(代码类)或三元组(日志类)。
            #   二元组 → 前两维是真 file/method，原样取出；
            #   三元组 → 前两维是 _issue_identity 产出的 "organ:xxx"/"type:xxx" 前缀，
            #     不是真 file/method，须置空，避免后续 get_method_body 拿伪 method 空查。
            if isinstance(_key, (tuple, list)) and len(_key) >= 3:
                _file, _method = "", ""
            elif isinstance(_key, (tuple, list)) and len(_key) == 2:
                _file, _method = _key[0], _key[1]
            else:
                _file = _key if isinstance(_key, str) else ""
                _method = ""
            # 同一方法的多个问题合并为一步
            _step = {
                "step_index": _step_index,
                "file": _file,
                "method": _method,
                "issues": _group_issues,
                "depends_on": [_step_index - 1] if _step_index > 0 else [],
                "priority": min(_priority(i) for i in _group_issues),
                "issue_count": len(_group_issues),
            }
            _steps.append(_step)
            _step_index += 1

        # 4. 限制最大步数（避免过多步骤导致超时）
        # ★9-问题3修复（2026-09-06）：由硬编码 5 改为实例属性 self._max_repair_steps（默认 12）。
        #   硬编码 5 在生产实测中直接吞掉一半问题：
        #     20 个 high/medium 问题按 (file, method) 分成 11 组 → 截取 5 组
        #     → 展开仅 10 个 → 下游 _process_count=min(10,10) 再卡一道
        #     → 最终 processed=10，另外 10 个被静默丢弃。
        #   且这是**每轮反复发生**的：被丢弃的 10 个下一轮同样排序靠前、
        #   同样被截掉，形成「永远轮不上」的饥饿队列。
        #   默认值 12 可覆盖当前实测的 11 组，并留一格余量。
        _max_steps = self._max_repair_steps
        if len(_steps) > _max_steps:
            _dropped_steps = _steps[_max_steps:]
            _dropped_issue_count = sum(s.get("issue_count", 0) for s in _dropped_steps)
            # ★PHASE12-P1-1：截断不再静默。三件事必须做全：
            #   ① 日志带精确丢弃数（步数 + 问题数），让吞吐缺口可观测、可告警；
            #   ② 给被丢弃的问题打 _deferred_from_prev_round 标记，
            #      上游下一轮可据此把它们排到队尾之外，打破「永远轮不上」的饥饿；
            #   ③ 记录被丢弃位置清单（文件.方法），便于人工核查是否被长期饿死。
            if self._mark_deferred_issues:
                for _s in _dropped_steps:
                    for _i in _s.get("issues", []):
                        if isinstance(_i, dict):
                            _i["_deferred_from_prev_round"] = True
            _module_logger.warning(
                f"[多步规划] 单轮吞吐触顶：共{len(_steps)}步/需处理"
                f"{sum(s.get('issue_count', 0) for s in _steps)}个问题，"
                f"本轮仅取前{_max_steps}步（上限来自 "
                f"EVOLUTION_CONFIG['max_steps_per_round']={_max_steps}），"
                f"丢弃{len(_dropped_steps)}步/{_dropped_issue_count}个问题"
                + ("（已打延期标记 _deferred_from_prev_round）"
                   if self._mark_deferred_issues else "（未打延期标记）")
                + "；被延期位置："
                + ", ".join(f"{s.get('file', '?')}.{s.get('method', '?')}"
                            for s in _dropped_steps[:8])
                + ("..." if len(_dropped_steps) > 8 else ""))
            _steps = _steps[:_max_steps]

        if _steps:
            _module_logger.info(
                f"[多步规划] 拆解为{len(_steps)}步修复，"
                f"基础{sum(1 for s in _steps if s['priority']==0)}步/"
                f"中等{sum(1 for s in _steps if s['priority']==1)}步/"
                f"复杂{sum(1 for s in _steps if s['priority']==2)}步")

        return _steps

    def _generate_llm_patch(self, plan: dict[str, Any], self_inspector,
                            original_code: str, file_path: str,
                            method_name: str) -> dict[str, Any] | None:
        """
        ★进化闭环升级(阶段A): LLM 主导的补丁生成。

        当本地规则表（6 类已知模式）无法匹配时，回退到 LLM 做开放域语义修复。
        这是曈曈从「规则驱动自修复」跨越到「LLM 驱动智能修复」的关键一步。

        安全设计:
            - LLM 补丁标记 repair_source="llm"、confidence="medium"（区别于本地规则 high）。
            - LLM 输出清理 markdown 代码块 + 与原文对比（无实质变化则放弃）。
            - 补丁状态为 pending，后续经副本验证 + 回归 + 动态测试通过后才可自动批准。
        """
        try:
            # 从 plan 构造 issue 字典（LLM 调用所需字段）
            _issue = {
                "type": plan.get("type", "unknown"),
                "description": plan.get("description", ""),
                "organ": plan.get("target", ""),
                "method": method_name,
                "file": file_path,
                "risk_score": plan.get("risk_score", 3),
                "benefit_score": plan.get("benefit_score", _DEF_BENEFIT_SCORE),
                # ★自我反思闭环: 上一版补丁验证失败原因（供 LLM 换策略修正）
                "prior_error": plan.get("llm_prior_error", ""),
                # ★上下文记忆(黄项收敛): 历史同类补丁经验（跨会话学习 few-shot 注入）
                "history_experience": self._patch_manager._load_history_experience(
                    plan.get("type", "")),
            }
            # 关联日志（若有）
            _related_logs = self._find_related_logs(file_path, method_name)
            # 调用 LLM 生成修复（单文件）
            _llm_fixed = self._call_llm_for_repair(_issue, original_code, _related_logs)
            if not _llm_fixed:
                # ★阶段B接线(跨文件补丁): 单文件修复失败时，接入 LLMEvolutionEngine
                #   生成跨文件补丁包（改接口时同步迁移调用方）。此前该引擎已实现但
                #   从未在主链路被调用——此处正式接线：
                #   - 包内补丁逐个经 _verify_and_save_patch（副本验证+状态提升+入队），
                #     与 execute() 主流程逻辑保持一致，由 apply_all_pending 统一应用；
                #   - 生成/解析/验证任一失败 → 返回 None（原逻辑），零回归；
                #   - 全部补丁入队后返回 None，避免与 execute() 重复处理。
                try:
                    from nucleus.evolution.LLMEvolutionEngine import LLMEvolutionEngine
                    _mf_engine = LLMEvolutionEngine(self._patch_manager.get_project_root())
                    _multi = _mf_engine.generate_multi_file_patch(
                        _issue, original_code, file_path, method_name,
                        call_chain=None, self_inspector=self_inspector)
                    if _multi:
                        _saved = 0
                        for _mp in _multi:
                            if self._verify_and_save_patch(_mp):
                                _saved += 1
                        if _saved:
                            _module_logger.info(
                                f"阶段B跨文件补丁包已入队: {_saved}/{len(_multi)} 个补丁"
                                f"(主文件+调用方迁移)，待统一应用验证")
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                return None
            # 清理 LLM 输出的 markdown 代码块
            _llm_clean = self._clean_llm_code(_llm_fixed)  # ★PHASE17-A2
            _llm_clean, _ = self._m91_align_base_indent(
                original_code, _llm_clean,
                f"file={_issue.get('file', '')}, method={method_name}")  # ★第91批T-91b
            if not _llm_clean or _llm_clean.strip() == original_code.strip():
                return None

            # 生成变更摘要
            _diff_summary = self._generate_diff_summary(original_code, _llm_clean)

            # 从方案数值评分推导风险等级与信任分（与本地规则补丁保持一致）
            _risk_score = plan.get("risk_score", 3)
            _risk_map = {1: "极低", 2: "低", 3: "中等", 4: "高"}
            _risk_level = plan.get("risk_level") or _risk_map.get(_risk_score, "中等")
            _benefit = plan.get("benefit_score", _DEF_BENEFIT_SCORE)
            _trust_score = min(90, max(10, int(_benefit) * 10))

            return {
                "id": f"patch_llm_{int(time.time())}_{hash(original_code) & 0xFFFF:04x}",
                "file": file_path,
                "method": method_name,
                "issue_type": plan.get("type", "unknown"),
                "risk_level": _risk_level,
                "description": plan.get("description", ""),
                "original_code": original_code,
                "modified_code": _llm_clean,
                "diff_summary": _diff_summary,
                "trust_score": _trust_score,
                "confidence": "medium",           # ★与本地规则 high 区分
                "repair_source": "llm",           # ★标记来源为 LLM
                # ★根因分析深度：LLM 修复同样记录根因陈述（供审查与知识沉淀）
                "root_cause_analysis": self._infer_root_cause(plan),
                "generated_at": time.time(),
                "status": "pending",
                "verification": None,
                "applied": False,
                "applied_at": 0,
            }
        except Exception as e:
            silent_exc(e, "nucleus/reasoning/SafeEvolutionExecutor.py:5179:_generate_llm_patch", level="warning")
            return None

    def _verify_and_save_patch(self, patch: dict[str, Any]) -> bool:
        """★阶段B接线：副本验证 + 状态提升 + 入队（与 execute() 主流程逻辑保持一致）。

        供跨文件补丁包内每个补丁复用，避免与 execute() 的验证/状态提升逻辑分叉。
        返回是否成功入队（验证通过且写入 pending 队列）。
        """
        try:
            _vr = self._patch_manager.verify_in_copy(patch)
            patch["verification"] = _vr
            if not _vr.get("passed"):
                patch["status"] = "verification_failed"
                _module_logger.warning(
                    f"跨文件补丁验证失败: {patch.get('id', '')[:16]}... → {_vr.get('errors', [])}")
                return False
            import config  # type: ignore[possibly-unbound]
            _auto_apply = getattr(config, 'EVOLUTION_CONFIG', {}).get("auto_apply_enabled", False)  # type: ignore[possibly-unbound]
            # ★审美判据深化(第四条路·E6): 跨文件补丁同样生成入队即打分——先评分，
            #   再决定自动批准（供 D 级择优拦截 + apply_all_pending 应用排序）。
            _aesthetic_grade = None
            try:
                from nucleus.evolution.AestheticJudge import get_aesthetic_judge
                _aesthetic = get_aesthetic_judge().score(patch.get("modified_code"))
                patch["aesthetic_score"] = _aesthetic
                patch["aesthetic_grade"] = _aesthetic.get("grade")
                _aesthetic_grade = _aesthetic.get("grade")
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            if _auto_apply:
                _is_llm = patch.get("repair_source") in ("llm", "llm_multi_file")
                _llm_trust_ok = True
                if _is_llm:
                    _base_trust = getattr(config, 'EVOLUTION_CONFIG', {}).get("auto_apply_min_trust", 60)  # type: ignore[possibly-unbound]
                    # ★策略自进化(下一层): 跨文件补丁同样按历史策略效果动态调整门槛
                    _min_trust = self._dynamic_llm_min_trust(
                        patch.get("issue_type", ""), _base_trust)
                    _llm_trust_ok = patch.get("trust_score", 0) >= _min_trust
                if _is_llm and not _llm_trust_ok:
                    patch["status"] = "verified"
                elif _aesthetic_grade == "D":
                    # ★审美判据择优(深化·E6): 跨文件补丁 D 级同样不自动批准。
                    patch["status"] = "verified"
                    _module_logger.warning(
                        f"审美门槛拦截: 跨文件补丁D级(质量低)转人工审批 {patch.get('id', '')[:16]}...")
                else:
                    # ★主线第79批 T1(P0 安全审计): 跨文件补丁同样——核心文件永远不自动批准，
                    #   强制等待人工审批(status=verified)；仅非核心走自动批准(approved)。
                    _is_core = self._is_core_file(patch.get("file", ""))
                    if _is_core:
                        patch["is_core_file"] = True
                        patch["status"] = self._resolve_core_auto_apply_status(_is_core)
                        if self._core_auto_apply_allowed():
                            _module_logger.warning(
                                f"灰度开关允许核心文件自动批准(回退旧行为): "
                                f"{patch.get('id', '')[:16]}...")
                        else:
                            _module_logger.warning(
                                f"核心文件强制人工审批(安全红线，不自动批准): "
                                f"{patch.get('id', '')[:16]}... → {patch.get('file', '')}")
                    else:
                        patch["status"] = "approved"
            else:
                patch["status"] = "verified"
            self._patch_manager.save_pending_patch(patch)
            _module_logger.info(
                f"跨文件补丁验证通过({patch['status']}): {patch['id'][:16]}... → {patch['diff_summary'][:50]}")
            return True
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_verify_and_save_patch L5269")
            return False

    def _generate_diff_summary(self, original: str, modified: str) -> str:
        """生成人类可读的变更摘要"""
        if not original or not modified:
            return "无法生成变更摘要"
        orig_lines = original.strip().split('\n')
        mod_lines = modified.strip().split('\n')
        
        changes = []
        for i, (o, m) in enumerate(zip(orig_lines, mod_lines)):
            if o != m:
                o_short = o.strip()[:40]
                m_short = m.strip()[:40]
                changes.append(f"第{i+1}行: '{o_short}' → '{m_short}'")
        
        if len(mod_lines) > len(orig_lines):
            for i in range(len(orig_lines), len(mod_lines)):
                changes.append(f"新增第{i+1}行: '{mod_lines[i].strip()[:60]}'")
        elif len(orig_lines) > len(mod_lines):
            for i in range(len(mod_lines), len(orig_lines)):
                changes.append(f"删除第{i+1}行: '{orig_lines[i].strip()[:60]}'")
        
        return "; ".join(changes[:5]) if changes else "无实质性变更"
    
    def _patch_silent_exception(self, plan: dict[str, Any], 
                                 file_path: str, original_code: str) -> dict[str, Any]:
        """为静默异常生成补丁"""
        description = (
            f"在 {plan.get('target', '?')}.{plan.get('method', '?')} 方法中，"
            f"将静默异常捕获 except Exception: pass 改为记录日志。"
        )
        
        return {
            "file": file_path or f"organs/{plan.get('target', '?')}.py",
            "method": plan.get("method", ""),
            "type": "add_exception_logging",
            "description": description,
            "action": plan.get("action", ""),
            "risk_level": "低",
            "original_pattern": "except Exception:\n    pass",
            "suggested_code": (
                "except Exception as e:\n"
                "    self._log(LogLevel.ERROR, f'异常: {e}')"
            ),
        }
    
    def _patch_bare_except(self, plan: dict[str, Any], 
                            file_path: str, original_code: str) -> dict[str, Any]:
        """为裸except生成补丁"""
        return {
            "file": file_path or f"organs/{plan.get('target', '?')}.py",
            "method": plan.get("method", ""),
            "type": "fix_bare_except",
            "description": "将裸 except: 改为 except Exception: 并添加日志",
            "action": plan.get("action", ""),
            "risk_level": "低",
            "original_pattern": "except:",
            "suggested_code": (
                "except Exception as e:\n"
                "    self._log(LogLevel.ERROR, f'异常: {e}')"
            ),
        }
    
    def _patch_status_request(self, plan: dict[str, Any], 
                               file_path: str) -> dict[str, Any]:
        """为_on_status_request重复代码生成补丁"""
        return {
            "file": file_path or f"organs/{plan.get('target', '?')}.py",
            "method": "_on_status_request",
            "type": "dedup_status_request",
            "description": "简化 _on_status_request 方法，改为调用 self.get_stats()",
            "action": plan.get("action", ""),
            "risk_level": "极低",
            "original_pattern": "手动维护的重复代码",
            "suggested_code": (
                "def _on_status_request(self):\n"
                "    return self.get_stats()"
            ),
        }
    
    def _patch_generic(self, plan: dict[str, Any], 
                        file_path: str, original_code: str) -> dict[str, Any]:
        """为通用问题生成补丁"""
        return {
            "file": file_path or f"organs/{plan.get('target', '?')}.py",
            "method": plan.get("method", ""),
            "type": "manual_review",
            "description": plan.get("description", "需要手动审查"),
            "action": plan.get("action", "建议创造者手动审查该位置代码"),
            "risk_level": "待评估",
            "original_pattern": "需手动审查",
            "suggested_code": f"// 请审查 {plan.get('target', '?')}.{plan.get('method', '?')} 方法",
        }
    def _auto_apply_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """
        自动应用补丁（仅在auto_apply_enabled=True时调用）。
        
        安全检查：
        1. 检查总开关
        2. 检查补丁信任分数
        3. 检查风险等级
        4. 检查冷却时间
        5. 创建快照备份
        6. 执行修改
        7. 验证修改结果
        
        Returns:
            应用结果字典
        """
        try:
            import config  # type: ignore[possibly-unbound]
            evo_cfg = getattr(config, 'EVOLUTION_CONFIG', {})  # type: ignore[possibly-unbound]
            
            # 安全检查1：总开关
            if not evo_cfg.get("auto_apply_enabled", False):
                return {"applied": False, "reason": "自动执行总开关未开启"}
            
            # 安全检查2：信任分数
            min_trust = evo_cfg.get("auto_apply_min_trust", 100)
            patch_trust = patch.get("trust_score", 50)
            if patch_trust < min_trust:
                return {"applied": False, "reason": f"信任分数不足({patch_trust}<{min_trust})"}
            
            # 安全检查3：风险等级
            max_risk = evo_cfg.get("auto_apply_max_risk", 1)
            risk_map = {"极低": 1, "低": 2, "中等": 3, "高": 4}
            patch_risk = risk_map.get(patch.get("risk_level", "中等"), 3)
            if patch_risk > max_risk:
                return {"applied": False, "reason": f"风险等级过高({patch.get('risk_level', '未知')})"}
            
            # 安全检查4：冷却时间
            if not hasattr(self, '_last_auto_apply_time'):
                self._last_auto_apply_time = 0.0
            cooldown = evo_cfg.get("auto_apply_cooldown", 86400)
            if time.time() - self._last_auto_apply_time < cooldown:
                return {"applied": False, "reason": f"冷却中({cooldown}s)"}
            
            # 安全检查5：创建快照备份
            backup_path = ""
            if evo_cfg.get("auto_apply_backup_required", True):
                backup_path = self._create_backup_before_apply(patch)
            
            # 安全检查6：执行修改
            file_path = patch.get("file", "")
            if not file_path or not os.path.exists(file_path):
                return {"applied": False, "reason": f"文件不存在: {file_path}"}
            
            apply_result = self._apply_patch_to_file(file_path, patch)
            
            if apply_result.get("success"):
                self._last_auto_apply_time = time.time()
                return {
                    "applied": True,
                    "file": file_path,
                    "method": patch.get("method", ""),
                    "type": patch.get("type", ""),
                    "backup_path": backup_path,
                    "applied_at": time.time(),
                }
            else:
                return {"applied": False, "reason": apply_result.get("error", "未知错误")}
                
        except Exception as e:
            print(f"[WARNING] SafeEvolutionExecutor.py:3544: {type(e).__name__}: {e}")
            return {"applied": False, "reason": f"自动应用异常: {str(e)[:80]}"}
    
    def _create_backup_before_apply(self, patch: dict[str, Any]) -> str:
        """
        在自动修改前创建代码文件备份。
        
        Args:
            patch: 补丁信息
        
        Returns:
            备份文件路径
        """
        file_path = patch.get("file", "")
        if not file_path or not os.path.exists(file_path):
            return ""
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = f"{file_path}.{timestamp}.evo_bak"
        
        try:
            import shutil
            shutil.copy2(file_path, backup_path)
            return backup_path
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_create_backup_before_apply L5449")
            return ""
    
    def _apply_patch_to_file(self, file_path: str, 
                              patch: dict[str, Any]) -> dict[str, Any]:
        """
        将补丁应用到文件中。
        
        当前仅支持简单替换模式：查找原模式并替换为建议代码。
        对于复杂修改（如方法拆分、代码移动），仍需创造者手动执行。
        
        Args:
            file_path: 文件路径
            patch: 补丁信息
        
        Returns:
            应用结果
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            
            original_pattern = patch.get("original_pattern", "")
            suggested_code = patch.get("suggested_code", "")
            
            if not original_pattern or not suggested_code:
                return {"success": False, "error": "补丁信息不完整"}
            
            if original_pattern == "手动维护的重复代码" or original_pattern == "需手动审查":
                return {"success": False, "error": "该补丁类型暂不支持自动执行，需手动修改"}
            
            # 执行替换
            if original_pattern in content:
                new_content = content.replace(original_pattern, suggested_code)
                
                # 验证替换后的内容语法完整性
                try:
                    compile(new_content, file_path, 'exec')
                except SyntaxError as e:
                    return {"success": False, "error": f"替换后代码语法错误: {e}"}
                
                # 原子写入（★FIX: 改用 mkstemp 随机名，避免预测名 TOCTOU/符号链接重定向）
                import tempfile
                _fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(file_path) or ".",
                                                 prefix=".patch_", suffix=".tmp")
                try:
                    with os.fdopen(_fd, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    os.replace(tmp_path, file_path)
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError as _exc:
                            _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                return {"success": True}
            else:
                return {"success": False, "error": "未找到匹配的原始模式"}
                
        except Exception as e:
            return {"success": False, "error": str(e)[:80]}    

    # ==================================================================
    # ★主线第15批 T3/P1-93：子进程崩溃可诊断 + 自动重试 + 降级
    # ==================================================================
    @staticmethod
    def _crash_retry_config() -> tuple[bool, int]:
        """读取崩溃重试配置：(开关, 最大重试次数)。"""
        enabled, retries = True, 2
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "ENABLE_EVOLUTION_CRASH_RETRY", True))
            retries = int(getattr(_cfg, "EVOLUTION_CRASH_MAX_RETRIES", 2) or 2)
        except Exception as _e:
            _module_logger.debug(
                f"崩溃重试配置读取失败，使用默认值(True,2): {type(_e).__name__}: {_e}")
        return enabled, max(0, retries)

    @staticmethod
    def _read_text_tail(path: str, limit: int = 1200) -> str:
        """读取文本文件尾部（用于崩溃后取出子进程 stderr）。"""
        try:
            if not path or not os.path.exists(path):
                return ""
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()[-limit:]
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SafeEvolutionExecutor::_read_text_tail L5535")
            return ""

    def _record_crash(self, mode: str, reason: str, detail: str = "") -> None:
        """累计崩溃统计（按 mode 记录次数 + 原因分布）。"""
        try:
            _m = self._crash_stats.setdefault(
                mode, {"count": 0, "reasons": {}, "last_detail": ""})
            _m["count"] += 1
            _key = (reason or "未知").split(":")[0].strip()[:60]
            _m["reasons"][_key] = _m["reasons"].get(_key, 0) + 1
            _m["last_detail"] = (detail or "")[-1200:]
        except Exception as e:
            _module_logger.debug(f"崩溃统计记录失败（已忽略）: {type(e).__name__}: {e}")

    def get_crash_stats(self) -> dict[str, Any]:
        """子进程崩溃统计（次数 + 原因分布），供自省/诊断消费。"""
        _out: dict[str, Any] = {}
        _total = 0
        for _mode, _m in (self._crash_stats or {}).items():
            _total += int(_m.get("count", 0))
            _out[_mode] = {
                "count": _m.get("count", 0),
                "reasons": dict(_m.get("reasons", {})),
                "last_detail": _m.get("last_detail", ""),
            }
        _out["total"] = _total
        return _out

    def _run_subprocess_once(self, input_path: str, output_path: str,
                             stderr_path: str, mode: str,
                             timeout: float) -> dict[str, Any]:
        """执行**一次**子进程迭代；崩溃时给出可诊断的原因（stderr + 崩溃载荷）。"""
        import multiprocessing

        from nucleus.reasoning.evolution_worker import run_evolution_worker_captured
        proc = multiprocessing.Process(
            target=run_evolution_worker_captured,
            args=(input_path, output_path, stderr_path),
            name="PulseEvolutionWorker",
            daemon=True,
        )
        _t0 = time.time()
        proc.start()
        _module_logger.info(
            f"[自主迭代] 子进程启动: mode={mode}, pid={proc.pid}, timeout={timeout}s")
        proc.join(timeout=timeout)
        _elapsed = time.time() - _t0

        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
            _module_logger.warning(
                f"[自主迭代] 子进程超时({timeout}s)，已强制终止，mode={mode}")
            self._record_crash(mode, "timeout", f"超时{timeout}s")
            return {"status": "timeout", "error": f"子进程超时({timeout}s)，已强制终止",
                    "stats": {}, "duration_ms": timeout * 1000}

        if proc.exitcode != 0:
            # ★T3：把「子进程 stderr 尾部 + 输出文件里的崩溃载荷」一并打出，
            #   彻底终结「只有 exitcode、没有原因」的长期盲区。
            _stderr_tail = self._read_text_tail(stderr_path)
            _payload = safe_read_json(output_path, default={}) or {}
            _crash_err = str(_payload.get("error", "") or "")
            _crash_tb = str(_payload.get("traceback", "") or "")
            _stderr_lines = _stderr_tail.strip().splitlines()
            _stderr_last = _stderr_lines[-1] if _stderr_lines else ""
            _reason = _crash_err or _stderr_last or f"exitcode={proc.exitcode}"
            _module_logger.warning(
                f"[自主迭代] 子进程崩溃: exitcode={proc.exitcode}, mode={mode}, "
                f"耗时={_elapsed:.1f}s, 原因={_reason}")
            if _crash_tb:
                _module_logger.warning(
                    f"[自主迭代] 子进程崩溃 traceback(尾): {_crash_tb[-800:]}")
            if _stderr_tail:
                _module_logger.warning(
                    f"[自主迭代] 子进程 stderr(尾): {_stderr_tail[-800:]}")
            self._record_crash(mode, _reason, _crash_tb or _stderr_tail)
            return {"status": "crashed",
                    "error": f"子进程退出码={proc.exitcode}: {_reason}",
                    "crash_traceback": _crash_tb[-2000:],
                    "stderr_tail": _stderr_tail[-1200:],
                    "stats": {}, "duration_ms": round(_elapsed * 1000, 1)}

        # 读取结果
        if os.path.exists(output_path):
            _result = safe_read_json(output_path, default={})
            if _result.get("status") == "crashed":
                # worker 的内部 except 捕获到的失败（进程退出码为 0）
                _err = str(_result.get("error", "") or "")
                _module_logger.warning(
                    f"[自主迭代] 子进程内部失败: mode={mode}, 原因={_err}")
                self._record_crash(mode, _err, str(_result.get("traceback", "")))
                return {"status": "crashed", "error": _err, "stats": {},
                        "crash_traceback": str(_result.get("traceback", ""))[-2000:],
                        "duration_ms": round(_elapsed * 1000, 1)}
            _stats = _result.get("stats", {}) if isinstance(_result.get("stats"), dict) else {}
            if mode == "review":
                _review_status = _stats.get("status", "unknown")
                _reviewed = _stats.get("reviewed", 0)
                _report_len = len(_stats.get("report", []))
                _module_logger.info(
                    f"[自主迭代] 子进程完成: mode=review, result={_review_status}, "
                    f"审查={_reviewed}个异常, 报告={_report_len}条, 耗时={_elapsed:.1f}s, "
                    f"pid={proc.pid}")
            elif mode == "repair":
                _local_fixable = _stats.get("local_fixable", "?")
                _local_generated = _stats.get("local_generated", "?")
                _local_verified = _stats.get("local_verified", "?")
                _local_submitted = _stats.get("local_submitted", "?")
                _module_logger.info(
                    f"[自主迭代] 子进程完成: mode=repair, "
                    f"可修={_local_fixable}, 生成={_local_generated}, "
                    f"验证={_local_verified}, 提交={_local_submitted}, "
                    f"耗时={_elapsed:.1f}s, pid={proc.pid}")
            else:
                _module_logger.info(
                    f"[自主迭代] 子进程完成: mode={mode}, status={_result.get('status')}, "
                    f"耗时={_elapsed:.1f}s, pid={proc.pid}")
            return _result
        _module_logger.warning(
            f"[自主迭代] 子进程未生成输出文件: mode={mode}, 耗时={_elapsed:.1f}s")
        self._record_crash(mode, "未生成输出文件", "")
        return {"status": "error", "error": "子进程未生成输出文件", "stats": {}}

    def _run_in_process_degraded(self, issues: list[dict[str, Any]], mode: str,
                                 plans: list[dict[str, Any]] | None,
                                 max_issues: int, max_plans: int) -> dict[str, Any] | None:
        """★T3 降级路径：子进程连续失败时，在主进程内同步执行同一任务。

        Returns: 成功时返回与子进程同构的结果 dict；无法降级时返回 None。
        """
        try:
            from nucleus.self_inspector import get_self_inspector
            _inspector = get_self_inspector()
        except Exception as e:
            _module_logger.warning(
                f"[自主迭代] 降级同步执行失败（无法获取 inspector）: "
                f"{type(e).__name__}: {e}")
            return None
        try:
            _t0 = time.time()
            if mode == "repair":
                _stats = self.repair_with_distillation(
                    (issues or [])[:max_issues], _inspector)
            elif mode == "review":
                _stats = self.run_runtime_guided_review(_inspector)
            elif mode == "execute":
                _stats = self.execute((plans or [])[:max_plans], _inspector)
            else:
                return None
            return {"status": "success", "stats": _stats, "degraded": "in_process",
                    "duration_ms": round((time.time() - _t0) * 1000, 1)}
        except Exception as e:
            _module_logger.warning(
                f"[自主迭代] 降级同步执行异常: {type(e).__name__}: {e}")
            return None

    def run_in_subprocess(self, issues: list[dict[str, Any]], mode: str = "repair",
                          timeout: float = 300.0, max_issues: int = 5,
                          plans: list[dict[str, Any]] | None = None,
                          max_plans: int = 3) -> dict[str, Any]:
        """★P1(2026-09-03)：在独立子进程中执行自主迭代，完成后子进程销毁，内存完全释放。

        设计原则：
        - 一次性进程，不用常驻池，防止内存积累
        - 子进程独立初始化inspector/executor，与主进程完全隔离
        - 临时JSON文件传参，避免大对象IPC序列化
        - 超时强制terminate，防止子进程卡死
        - 补丁在子进程中生成+验证，核心文件不自动应用

        Args:
            issues: 问题列表
            mode: "repair"（修复蒸馏）或 "review"（运行时引导审查）
            timeout: 子进程超时秒数，默认300秒
            max_issues: 最多处理的问题数，默认5个

        Returns:
            {"status": "success"/"timeout"/"crashed"/"error", "stats": {...}, "error": "", "duration_ms": 1234}
        """
        import json
        import tempfile

        # 创建临时文件（用项目目录下的tmp子目录，避免系统临时目录权限问题）
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _tmp_dir = os.path.join(_project_root, "tmp")
        os.makedirs(_tmp_dir, exist_ok=True)

        input_fd, input_path = tempfile.mkstemp(suffix="_evo_in.json", prefix="pulse_", dir=_tmp_dir)
        output_fd, output_path = tempfile.mkstemp(suffix="_evo_out.json", prefix="pulse_", dir=_tmp_dir)
        # ★T3：子进程 stderr 落盘文件，崩溃后由父进程读出并打进日志
        err_fd, stderr_path = tempfile.mkstemp(suffix="_evo_err.log", prefix="pulse_", dir=_tmp_dir)
        os.close(input_fd)
        os.close(output_fd)
        os.close(err_fd)

        try:
            # 写入输入
            with open(input_path, "w", encoding="utf-8") as f:
                json.dump({
                    "mode": mode, "issues": issues, "max_issues": max_issues,
                    "plans": plans or [], "max_plans": max_plans,
                }, f, ensure_ascii=False)

            # ★主线第15批 T3/P1-93：带重试的子进程执行
            #   （每次崩溃都记原因；重试仍失败 → 降级为同进程同步执行）
            _retry_on, _max_retries = self._crash_retry_config()
            _attempts = 1 + (_max_retries if _retry_on else 0)
            _last_fail: dict[str, Any] = {}
            for _attempt in range(1, _attempts + 1):
                _res = self._run_subprocess_once(
                    input_path, output_path, stderr_path, mode, timeout)
                if _res.get("status") not in ("crashed", "error"):
                    return _res
                _last_fail = _res
                if _attempt < _attempts:
                    _module_logger.warning(
                        f"[自主迭代] 子进程第{_attempt}次失败({_res.get('status')})，"
                        f"自动重试（{_attempt}/{_attempts - 1}）: "
                        f"{str(_res.get('error', ''))[:200]}")
            _deg = self._run_in_process_degraded(issues, mode, plans, max_issues, max_plans)
            if _deg is not None:
                _module_logger.warning(
                    f"[自主迭代] 子进程连续失败{_attempts}次，已降级为同进程同步执行: mode={mode}")
                return _deg
            return _last_fail or {"status": "crashed", "error": "子进程执行失败",
                                  "stats": {}}

        except Exception as e:
            _module_logger.error(f"[自主迭代] 子进程调用异常: {e}")
            return {"status": "error", "error": str(e), "stats": {}}
        finally:
            # 清理临时文件（含 T3 新增的 stderr 文件）
            for _p in (input_path, output_path, stderr_path):
                try:
                    if os.path.exists(_p):
                        os.remove(_p)
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def get_stats(self) -> dict[str, Any]:
        """获取执行统计"""
        return {
            "total_patches_generated": len(self._patch_log),
        }

    # ------------------------------------------------------------------
    # ★主线第39批 T3（P2-242）：面向自我认知引擎的**只读**公共统计接口
    # ------------------------------------------------------------------
    def get_evolution_stats(self) -> dict[str, Any]:
        """★P2-242：进化执行统计的**只读公共接口**（供 `SelfAwarenessEngine` 消费）。

        动机（第38批遗留）：`integrate_evolution_health()` 此前直接访问
        ``self._patch_manager`` **私有属性** 读取补丁历史，违反封装；内部实现变化
        会让 evolution_health 维度静默断裂。本方法将这条路径**收口为公共接口**。

        ★只读红线：**绝不**调用 ``verify_applied_patches()``（它会**自动回滚**）、
        ``apply_all_pending()`` / ``rollback_*`` 等改状态的接口；仅读内存态
        （``_patch_log`` / ``_crash_stats``）与补丁历史文件。

        统计口径（与第38批私有访问路径**保持一致**）：
            · successful_patches = ``applied`` 且 ``runtime_verified``
            · failed_patches     = ``applied`` 且 ``runtime_verify_result.verified is False``
            · rolled_back_patches= ``rolled_back`` / ``status == "rolled_back"``（当前语料恒 0）
            · success_rate / rollback_rate 分母为 ``applied_patches``
            · 异常一律记日志后降级为 0 / {}（**绝不静默**，见 m7 核心文件约束）

        Returns:
            dict，8 项主指标 + 4 项诊断指标：
                total_patches / successful_patches / failed_patches /
                rolled_back_patches / success_rate / rollback_rate /
                crash_count / strategy_learning_stats
                （附 applied_patches / pending_verify_patches / total_generated /
                 avg_effectiveness / strategies）
        """
        _out: dict[str, Any] = {
            "total_patches": 0, "applied_patches": 0,
            "successful_patches": 0, "failed_patches": 0,
            "rolled_back_patches": 0, "pending_verify_patches": 0,
            "success_rate": 0.0, "rollback_rate": 0.0,
            "crash_count": 0, "strategy_learning_stats": {},
            "total_generated": 0, "avg_effectiveness": 0.0, "strategies": 0,
        }
        try:
            _gs = self.get_stats()
            if isinstance(_gs, dict):
                _out["total_generated"] = int(
                    _gs.get("total_patches_generated", 0) or 0)
        except Exception as _e:
            _module_logger.debug(
                "get_evolution_stats: 读取 get_stats 失败（按 0 处理）: %s: %s",
                type(_e).__name__, _e)
        try:
            _cs = self.get_crash_stats()
            if isinstance(_cs, dict):
                _out["crash_count"] = int(_cs.get("total", 0) or 0)
        except Exception as _e:
            _module_logger.debug(
                "get_evolution_stats: 读取 get_crash_stats 失败（按 0 处理）: %s: %s",
                type(_e).__name__, _e)
        try:
            _sl = self.get_strategy_learning_stats()
            if isinstance(_sl, dict):
                _out["strategy_learning_stats"] = _sl
                _out["strategies"] = len(_sl.get("strategies", {}) or {})
        except Exception as _e:
            _module_logger.debug(
                "get_evolution_stats: 读取策略学习统计失败（按空处理）: %s: %s",
                type(_e).__name__, _e)
        try:
            _pm = getattr(self, "_patch_manager", None)
            _hist = _pm.load_json(_pm.get_history_file(), []) \
                if _pm is not None else []
            if isinstance(_hist, list):
                # ★主线第40批 T3（P2-260/P2-247）：收敛为**单一真相源**
                #   本段口径与 stats_from_patch_history() 完全一致（纯等价重构），
                #   与 SelfAwarenessEngine._evolution_raw_stats() 共用同一实现。
                # _m40_t3_executor
                from nucleus.evolution.evolution_stats import (
                    stats_from_patch_history)
                _st = stats_from_patch_history(_hist)
                _out["total_patches"] = _st["total"]
                _out["applied_patches"] = _st["applied"]
                _out["successful_patches"] = _st["successful"]
                _out["failed_patches"] = _st["failed"]
                _out["rolled_back_patches"] = _st["rolled_back"]
                _out["pending_verify_patches"] = _st["pending_verify"]
                _out["avg_effectiveness"] = _st["avg_effectiveness"]
        except Exception as _e:
            _module_logger.warning(
                "get_evolution_stats: 读取补丁历史失败（统计降级为 0）: %s: %s",
                type(_e).__name__, _e)
        _denom = max(1, int(_out["applied_patches"]))
        _out["success_rate"] = round(
            100.0 * int(_out["successful_patches"]) / _denom, 2)
        _out["rollback_rate"] = round(
            100.0 * int(_out["rolled_back_patches"]) / _denom, 2)
        return _out


# ===== 单例获取函数（供main.py自主进化循环调用） =====
_safe_evolution_executor_instance = None
_safe_evolution_lock = None

def get_safe_evolution_executor():
    """获取SafeEvolutionExecutor单例（线程安全）。"""
    global _safe_evolution_executor_instance, _safe_evolution_lock
    if _safe_evolution_lock is None:
        import threading
        _safe_evolution_lock = threading.Lock()
    if _safe_evolution_executor_instance is None:
        with _safe_evolution_lock:
            if _safe_evolution_executor_instance is None:
                _safe_evolution_executor_instance = SafeEvolutionExecutor()
    return _safe_evolution_executor_instance
