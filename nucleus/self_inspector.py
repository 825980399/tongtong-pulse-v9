# -*- coding: utf-8 -*-
"""
self_inspector.py —— 自省检查器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架自我结构检查与代码质量审计
机制: 大型模块（3620行），包含1个类、10个核心方法，采用分层架构实现
定位: 自省治理层
"""

from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化
import ast
import os
import re
import threading
import time
from typing import Any

from nucleus.const import LogLevel, SCAN_EXCLUDE_DIR_BASENAMES, SCAN_EXCLUDE_PREFIX
from config import EXTERNAL_CALL_TIMEOUTS
from config import TIMEOUT_CONFIG
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



# ★2026-09-04修复：使用框架logging，替代print，确保日志写入后台文件
def _get_logger():
    try:
        from nucleus.logger import get_module_logger
        return get_module_logger("self_inspector")
    except Exception:
        import logging
        return logging.getLogger("self_inspector")

_module_logger = _get_logger()


# ★第90批 T-90b：日志/类名定位 v2 开关（bug#2 类索引接入 + bug#3 包含匹配）。
#   开启（默认）→ 与修复同批的行为；关闭 → 逐字回到第90批前（仅中文名前缀匹配）。
#   默认值内联本模块（遵守红线「不改 config.py 开关」）。
def _m90_locate_v2_on() -> bool:
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M90_LOG_LOCATE_V2", True))
    except Exception:
        return True
# ★第91批 T-91a：日志调用点定位 v3（logger 名 / 器官别名 两级**数据驱动**索引）。
#   开启（默认）→ 在 v2（类索引 + 中文名包含）之上再接入两级新索引：
#     ① logger 名字面量索引：静态扫描源码里的
#        `get_organ_logger("X") / get_module_logger("X") / get_logger("X")`
#        字面量 → 源文件，再按 PulseFormatter 规则反查日志 TAG；
#     ② 器官别名索引：扫描器官 `organ_name: str = "X"` / `organ_name="X"` 声明。
#   为什么必须有（第91批 T0 实测，非推测）：
#     日志 TAG 由 `nucleus/logger.py::PulseFormatter` 从 **logger 名**派生——
#       `pulse.organ.胸腺` → `胸腺`；`pulse.module.WriteGuard` → `WriteGuard`；
#       `pulse.structured_parallel` → 原名。
#     logger 名来自 `BasePulseOrgan.__init__` 的
#       `self._logger = get_organ_logger(self.organ_name)`
#     而旧 `_build_organ_name_index()` 读的是**统一头部中文短语**
#       （`PulseEyes —— 脉冲驱动眼睛（知识检索器官 · v9.5 …）`）
#     ⇒ 两个命名源结构性错位，16 个真实标签恒不可定位（覆盖率卡在 80.2%）。
#   关闭 → 逐字回到第90批末行为（类索引 + 中文名前缀/包含），零回归。
def _m91_log_locate_v3_on() -> bool:
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M91_LOG_LOCATE_V3", True))
    except Exception:
        return True


# ★PHASE14（2026-09-07）：方法体定位的**聚合式**日志计数器。
#   背景（2h33m 运行日志实测，36793 行）：
#     self_inspector 独占 24720 行，占全量日志的 **67%**；
#     而这 24720 行里，24673 行是同一句话——
#     「[方法体定位] 行号N未命中，按方法名 X 兜底命中第M行（偏移K）」。
#   也就是说：**99.8% 的 self_inspector 日志是同一条 DEBUG 在刷屏**。
#   它每条都真实发生过（故不能简单删掉——那会丢掉偏移分布这个诊断依据），
#   但完全没必要逐条落盘：改为计数聚合，周期性地打一条汇总，
#   信息量不减（反而多了分布统计），IO 从 24673 行降到几十行。
_BODY_LOC_STATS = {"fallback": 0, "offset_probe": 0, "exact": 0,
                   "miss": 0, "offset_dist": {}, "since": 0.0}
_BODY_LOC_LOG_EVERY = 500      # 每累计这么多次调用打一条汇总
_BODY_LOC_ENABLED = None       # None=未读取配置


def _body_loc_tick(kind: str, offset: float | None = None) -> None:
    """累计一次方法体定位结果，达到阈值时打一条聚合日志。

    ★设计原则：只降 IO，不降信息。原来的逐条 DEBUG 现在变成
      「N 次调用 / M 次走兜底 / 偏移分布 +1×A +2×B」，诊断价值更高。
    任何异常都静默吞掉——日志统计绝不能反过来影响检测主流程。
    """
    global _BODY_LOC_ENABLED
    try:
        import time as _t
        _s = _BODY_LOC_STATS
        _s[kind] = _s.get(kind, 0) + 1
        if offset is not None and kind in ("fallback", "offset_probe"):
            _k = str(int(offset))
            _d = _s["offset_dist"]
            _d[_k] = _d.get(_k, 0) + 1
        if _s["since"] <= 0.0:
            _s["since"] = _t.time()
        _total = _s["fallback"] + _s["offset_probe"] + _s["exact"] + _s["miss"]

        if _BODY_LOC_ENABLED is None:
            try:
                import config as _c
                _BODY_LOC_ENABLED = bool(
                    getattr(_c, "SELF_INSPECTOR_LOG", {})
                    .get("aggregate_body_loc_log", True))
            except Exception:
                _BODY_LOC_ENABLED = True
        if not _BODY_LOC_ENABLED:
            return

        if _total and _total % _BODY_LOC_LOG_EVERY == 0:
            _elapsed = max(1e-6, _t.time() - _s["since"])
            _dist = "，".join(f"{k}×{v}" for k, v in sorted(
                _s["offset_dist"].items(), key=lambda kv: -kv[1])[:5])
            _module_logger.debug(
                f"[方法体定位] 汇总：近 {_total} 次调用——"
                f"精确{_s['exact']} / 偏移试探{_s['offset_probe']} / "
                f"方法名兜底{_s['fallback']} / 未命中{_s['miss']}"
                f"（{_total / _elapsed:.0f} 次/秒）"
                + (f"；偏移分布：{_dist}" if _dist else ""))
            # 滚动清零，避免长期运行后统计值失真
            for _k in ("fallback", "offset_probe", "exact", "miss"):
                _s[_k] = 0
            _s["offset_dist"] = {}
            _s["since"] = _t.time()
    except Exception as _se:
        silent_exc(_se, "self_inspector.py:100")


def _issue_file_in_backup_dir(file_path: str) -> bool:
    # ★主线第60批 T3：问题文件路径是否落在备份目录（.bak_batchN / .bak_tmp /
    #   .bak_mainlineN 等）。按路径「段前缀 .bak」判定，不误伤文件名含 .bak 后缀的
    #   正常文件（foo.py.bak）。供 self_inspector 防御过滤与 glob 扫描排除共用。
    # ★T-133c 制度化：并入 const.SCAN_EXCLUDE_DIR_BASENAMES / SCAN_EXCLUDE_PREFIX，
    #   覆盖 backups/ data/code_backups/ tmp/ 等备份/临时树（原仅认 .bak 段，漏此三族）。
    if not file_path:
        return False
    _norm = file_path.replace("\\", "/")
    for _seg in _norm.split("/"):
        if (_seg.startswith(".bak")
                or _seg.startswith(tuple(SCAN_EXCLUDE_PREFIX))
                or _seg in SCAN_EXCLUDE_DIR_BASENAMES):
            return True
    return False


class SelfInspector(SilentLogMixin):
    """
    框架自描述生成器。
    
    让曈曈能够回答"我是怎么构成的""我有哪些器官""知识如何流动"
    等关于自身架构的问题。返回的信息深度由隐私层级决定。
    """
    
    def __init__(self):
        self._config = None
        self._project_root = ""
        # ★第64批 T1：器官扫描缓存初始化
        self._scan_cache = {}
        self._scan_cache_time = 0.0
        self._scan_cache_ttl = 300.0
        self._scan_cache_file_mtimes = {}
        self._scan_cache_hits = 0
        self._scan_cache_misses = 0
        self._scan_cache_invalidations = 0
        try:
            import config as _m64_cfg
            self._scan_cache_ttl = float(getattr(_m64_cfg, "ORGAN_SCAN_CACHE_TTL", 300.0))
        except Exception as _m64_e:
            _module_logger.debug(f"[SelfInspector] 缓存TTL读取失败，使用默认300s: {type(_m64_e).__name__}: {_m64_e}")
        # _m64_t1_scan_cache_done
        # ★第64批 T2：高频查询二级缓存（与扫描缓存版本戳联动失效）
        self._scan_cache_enabled = True
        try:
            import config as _m64_cfg2
            self._scan_cache_enabled = bool(getattr(_m64_cfg2, "ENABLE_ORGAN_SCAN_CACHE", True))
        except Exception as _m64_e2:
            _module_logger.debug(f"[SelfInspector] 二级缓存开关读取失败，使用默认开启: {type(_m64_e2).__name__}: {_m64_e2}")
        self._organ_file_cache = {}
        self._structure_cache = {}
        self._method_info_cache = {}
        # ★主线第65批 T3/P2：get_method_body 文件级缓存（绑文件 mtime，LRU 1000）
        #   复用 ENABLE_ORGAN_SCAN_CACHE 灰度开关；关闭→每次重新读文件+AST（零回归）。
        self._method_body_cache: dict = {}          # (file_path, method_name) -> (result, mtime)
        self._method_body_hits = 0
        self._method_body_misses = 0
        # ★第64批 T5：二级缓存命中/未命中计数（可观测性）
        self._l2_hits = 0
        self._l2_misses = 0
        self._scan_stats_last_log = 0.0
        # _m64_t2_l2_cache_done
        self._allowed_extensions = [".py", ".md", ".json"]
        self._excluded_dirs = ["__pycache__", ".git", "logs", "data", "models",
                               "backups", "tmp"]
        self._excluded_files = ["*.pyc", "*.log", "*.bak"]
        # ★PHASE12-P1-2扩展（2026-09-07）：全项目类索引（供 get_method_body 兜底查询）。
        #   背景（15小时运行日志实证）：自主进化每轮「发现17个问题 / 处理6个 /
        #   修复0个」，其中 6 个被跳过的问题 100% 是「缺少 organ/method 无法定位代码」。
        #   根因不是字段名不一致，而是 _scan_all_organs() 只扫 organs/ 目录，
        #   nucleus/（250个类）、functions/、base/、main.py 等核心模块全部查不到
        #   → get_method_body 恒返回 None → 代码片段取不到 → 本地不修、LLM 也不调。
        #   实测覆盖率仅 36.4%（150/412 类），扩展后可达 100%。
        #   此处只做「兜底索引」，不改动 _scan_all_organs 的器官语义（★最小侵入）。
        self._project_class_index: dict[str, dict[str, Any]] | None = None
        self._project_class_index_lock = threading.Lock()
        self._privacy_levels = {}
        self._enabled = True
        self._load_config()
        self._info_field = None  
        # ===== 已知超长方法白名单 =====
        self._long_method_whitelist = {
            ("PulseInnerWorld", "_on_inference_request"),
            ("PulseInnerWorld", "_on_heartbeat"),
            ("PulseInnerWorld", "_generate_weekly_report"),
            ("PulseInnerWorld", "_deep_think"),
            ("PulseInnerWorld", "_route_to_deriver"),
            ("PulseLiver", "_compress_l1_to_l2"),
            ("PulseLiver", "_fuse_l2_to_l3"),
            ("PulseLiver", "_do_optimize"),
            ("PulseSubconscious", "_on_curiosity_tick"),
            ("PulseSubconscious", "_trigger_dream"),
            ("PulseStomach", "_do_digest"),
            ("PulseCortex", "_on_inference_result"),
            ("PulseController", "_search_deep_headless"),
            ("PulseController", "_on_open_url"),
            ("PulseNarrativeSelf", "_generate_weekly_report"),
            ("PulseSelfAwareness", "_on_record_interaction"),
            ("PulseReflection", "_analyze_interaction"),
        }
        # ★v17.0新增：代码问题历史记录，用于趋势分析
        self._issue_history: list[dict[str, Any]] = []
        self._max_issue_history = 10
        # ★v17.0 D5新增：问题生命周期跟踪
        self._issue_states: dict[str, dict[str, Any]] = {}
        # key: "file:line:type" → {status, first_seen, last_seen, count, resolved_at}
        self._last_issue_ids: set = set()  # 上次扫描的所有问题ID集合
        # ★v23.0新增：AST解析缓存——避免每个方法都重新解析整个文件
        self._ast_cache: dict[str, tuple[float, Any]] = {}
        self._ast_cache_max = 60  # 最多缓存60个文件的AST
        self._max_issue_states = 2000  # 最多跟踪2000个问题状态
        # ★PHASE14（2026-09-07）：方法节点的行号/名称索引，与 _ast_cache 同生命周期。
        #   背景：_read_method_body 原先每次调用都要 ast.walk 整棵树来找目标方法。
        #   2h33m 实测日志里「按方法名兜底命中」出现了 **24673 次**，
        #   意味着两万多次全树遍历；而文件 AST 本身已经按 mtime 缓存了，
        #   解析只做一次、遍历却做两万次——这是纯粹的浪费。
        #   缓存后：定位从 O(文件方法数) 降为 O(1) 哈希查表。
        self._ast_fn_index: dict[str, tuple[float, dict, dict]] = {}
        # 文件内容按行切分缓存。原实现每次调用都 `content.split('\n')`，
        #   对 PulseLiver 这类 170KB / 数千行的文件是每次都全量重建一个列表，
        #   实测这才是 _read_method_body 的主要耗时项（比 AST 遍历更贵）。
        self._ast_lines_cache: dict[str, tuple[float, list[str]]] = {}
        # ★FIX(P0.5 分级初筛): 问题类型 → 置信度分级（high 直接输出 / medium 疑点推 LLM / low 仅记录）
        self._issue_severity = {
            "unsafe_eval": "high",
            "subprocess_shell": "high",
            "sql_injection": "high",
            "silent_exception": "medium",
            "bare_except": "medium",
            "no_timeout_http": "medium",
            "thread_no_daemon": "medium",
            "unbounded_deque": "medium",
            "lock_with_emit": "medium",
            "long_method": "low",
            "status_request_duplicate": "low",
            "periodic_task_no_reentry": "medium",
            "resource_no_close": "medium",
            "cross_module_singleton_call": "medium",
            "busy_loop_no_exit": "medium",
            "non_atomic_write": "medium",
            "bare_return_none_in_except": "medium",
            "unjoined_thread": "low",
            "mutable_default": "medium",
            "hardcoded_abs_path": "medium",
            "print_debug": "low",
        }
        # ★Kimi 设计参考借鉴（§6.3）：问题类型 → 具体失败场景映射
        # 「不给失败场景的审查没有行动价值」——每条发现必须说明什么输入/时序下会导致什么后果。
        self._issue_failure_scenarios = {
            "unsafe_eval": "外部输入被拼接进 eval/exec 时，攻击者可注入任意代码在框架进程内执行，导致数据泄露或系统被控",
            "subprocess_shell": "用户/配置可控内容经 shell=True 传给 subprocess 时，可被注入系统命令，造成命令执行或权限提升",
            "sql_injection": "拼接 SQL 查询且输入未参数化时，恶意输入可改写查询语义，造成越权读取或数据破坏",
            "silent_exception": "异常被静默吞掉且无日志，故障发生时运行看似正常，排障时无任何痕迹可循",
            "bare_except": "捕获所有异常（含 KeyboardInterrupt/SystemExit）可能掩盖真实错误并阻断正常退出",
            "no_timeout_http": "请求外部服务时无超时，对端不响应会让调用线程永久挂起，拖死该器官的处理循环",
            "thread_no_daemon": "非 daemon 线程在框架退出时阻塞进程，导致退出挂起（曾出现的退出无法完成问题即此类）",
            "unbounded_deque": "无界队列在生产者快于消费者时无限增长，内存耗尽前框架卡死或被杀",
            "lock_with_emit": "持锁期间发送脉冲/emit 可能触发同锁重入或跨器官等待，造成死锁或锁竞争放大",
            "long_method": "超长方法难以单测与审计，一处改动可能影响大片逻辑，回归风险高",
            "status_request_duplicate": "同一状态请求被重复发送，浪费脉冲带宽并可能触发重复副作用",
            "periodic_task_no_reentry": "周期任务执行时间长于间隔时发生重入，产生并发叠加的副作用或资源争用",
            "resource_no_close": "文件/句柄/连接未关闭，长期运行后句柄耗尽导致后续操作失败（如打开文件报错）",
            "cross_module_singleton_call": "模块直接调用他模块单例绕过封装，破坏依赖边界，升级一方可能隐式影响另一方",
            "busy_loop_no_exit": "忙等循环缺少退出条件时 CPU 空转打满，拖累同进程所有器官",
            "non_atomic_write": "非原子写入在写一半时崩溃会留下损坏文件，下次启动加载即崩",
            "bare_return_none_in_except": "except 中直接 return None 掩盖异常细节，调用方拿到 None 后继续处理导致二次错误",
            "unjoined_thread": "线程未 join 就退出主流程，后台任务可能未完成即被丢弃或与后续操作竞争",
            "mutable_default": "可变默认参数在多次调用间共享同一对象，一次修改会污染后续所有调用，产生难以复现的状态错乱",
            "hardcoded_abs_path": "硬编码绝对路径（Windows 盘符或 /tmp/...）在跨环境迁移后指向不存在位置，启动或读写即失败",
            "print_debug": "print 调试残留刷屏控制台并绕过后台日志体系，运行问题无法通过日志追溯",
        }
        # ★FIX(假阳性治理): 已知误报库（file:type:method 三元组自动过滤，降低噪音）
        self._false_positive_rules: set = set()
        self._false_positive_file = "data/false_positive_rules.json"
        self._load_false_positives()
        # ★P3-12修复：自增强闭环——蒸馏出的「问题类型→修复策略」知识库，
        # 供 detect_code_issues 在命中同类问题时用 LLM 沉淀的策略增强修复建议。
        self._lesson_fix_strategies: dict[str, str] = {}

    # ========== 假阳性库持久化 ==========
    def _load_false_positives(self):
        """从磁盘加载假阳性规则（重启后保留）。"""
        try:
            import json
            if os.path.exists(self._false_positive_file):
                with open(self._false_positive_file, encoding='utf-8') as _f:
                    _rules = json.load(_f)
                    if isinstance(_rules, list):
                        self._false_positive_rules = set(_rules)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")

    def _save_false_positives(self):
        """把假阳性规则落盘（原子写）。"""
        try:
            import json
            _dir = os.path.dirname(self._false_positive_file)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            _tmp = self._false_positive_file + ".tmp"
            with open(_tmp, 'w', encoding='utf-8') as _f:
                json.dump(sorted(self._false_positive_rules), _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, self._false_positive_file)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def add_false_positive(self, file_name: str, issue_type: str, method: str):
        """把一条规则加入假阳性库并落盘（★FIX: 误报治理）。"""
        _key = f"{file_name}:{issue_type}:{method}"
        self._false_positive_rules.add(_key)
        self._save_false_positives()

    def add_false_positives_bulk(
            self, rules: list[tuple[str, str, str]] | list[str]) -> int:
        """★PHASE17-C4：批量登记假阳性规则并落盘，返回新增条数。

        修复前 add_false_positive 是全项目**零调用点**的死代码（只能手工改 JSON），
        假阳性库因此长期只有历史遗留的 1 条，过滤形同虚设。
        现提供批量入口，供配置种子、人工审查、LLM 复核等链路调用。

        Args:
            rules: 可为 [(file_name, issue_type, method), ...] 三元组列表，
                   或直接是 ["file:type:method", ...] 已格式化字符串列表。
        """
        _before = len(self._false_positive_rules)
        try:
            for _r in rules or []:
                if isinstance(_r, (tuple, list)) and len(_r) == 3:
                    self._false_positive_rules.add(
                        f"{_r[0]}:{_r[1]}:{_r[2]}")
                elif isinstance(_r, str) and _r.count(":") >= 2:
                    self._false_positive_rules.add(_r)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        _added = len(self._false_positive_rules) - _before
        if _added > 0:
            self._save_false_positives()
            self._log("INFO", f"[假阳性库] 新增 {_added} 条误报规则，"
                              f"当前共 {len(self._false_positive_rules)} 条")
        return _added

    def _filter_false_positives(
            self, issues: list[dict[str, Any]], stage: str = "") -> list[dict[str, Any]]:
        """★PHASE17-C4：按 `file:type:method` 三元组过滤已知误报，并输出过滤计数。

        此前过滤只存在于 _run_detectors 内部且**完全静默**：
          1. detect_code_issues 在 _run_detectors 之后追加的全库检测器结果
             （dead_code / unused_imports / config_audit，占问题总量 87%）
             从未经过滤，假阳性库对它们完全无效；
          2. 过滤掉了多少条没有任何日志，运维无法确认规则是否生效。
        现抽为公共方法，两条产出路径统一调用，并记 DEBUG 计数日志。
        """
        if not issues:
            return []
        if not self._false_positive_rules:
            return issues
        _kept: list[dict[str, Any]] = []
        _dropped = 0
        for _issue in issues:
            _fp_key = (
                f"{os.path.basename(str(_issue.get('file', '')))}:"
                f"{_issue.get('type', '')}:{_issue.get('method', '')}"
            )
            if _fp_key in self._false_positive_rules:
                _dropped += 1
                continue
            _kept.append(_issue)
        if _dropped:
            _module_logger.debug(
                f"[假阳性过滤{('·' + stage) if stage else ''}] 过滤{_dropped}条"
                f"（规则库 {len(self._false_positive_rules)} 条，"
                f"剩余 {len(_kept)} 条）")
        return _kept

    def absorb_growth_rules(self, rules: list[dict[str, Any]]) -> int:
        """★P3-12修复：吸收验证学习枢纽蒸馏出的 code_issue_lesson 规则，
        把「问题类型→LLM 修复策略」沉淀进检测知识库，实现审查-修复-回灌自增强闭环。

        此前 code_issue_lesson 规则被 QICA 忽略（QICA 只消费 domain_knowledge/concept_correction），
        且 self_inspector 未注册 applier，导致 LLM 审查结论永远无法反哺规则引擎。
        """
        if not rules:
            return 0
        _absorbed = 0
        for _rule in rules:
            if not isinstance(_rule, dict):
                continue
            # 只消费 code_issue_lesson 类型（issue_type → fix_strategy）
            if _rule.get("type") != "code_issue_lesson":
                continue
            _issue_type = _rule.get("issue_type", "")
            _strategy = _rule.get("fix_strategy", "")
            if not _issue_type or not _strategy:
                continue
            # 仅当策略更具体（长度更长）或首次出现时才更新，避免被低质策略覆盖
            _existing = self._lesson_fix_strategies.get(_issue_type, "")
            if not _existing or len(_strategy) > len(_existing):
                self._lesson_fix_strategies[_issue_type] = _strategy
                _absorbed += 1
        return _absorbed

    # ========== 配置加载 ==========
    def _load_config(self):
        """从config加载自我审视配置"""
        try:
            import config
            cfg = getattr(config, 'SELF_AWARENESS_CONFIG', {})
            self._enabled = cfg.get("enabled", True)
            self._project_root = cfg.get("project_root", "")
            # 防御：如果路径不存在，尝试自动纠正（修正 os.path.dirname 层数问题）
            if self._project_root and not os.path.exists(self._project_root):
                _fallback = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if os.path.exists(_fallback):
                    _module_logger.warning(f"[SelfInspector] 配置路径无效({self._project_root})，自动修正为: {_fallback}")
                    self._project_root = _fallback
            # ★PHASE14：只在项目根目录**发生变化**时才打日志。
            #   原实现每次 _load_config 都无条件打一条，2h33m 里重复了 39 次，
            #   内容一字不差。这类「初始化常量」日志打一次就够了。
            _root_now = self._project_root or "__NOT_SET__"
            if _root_now != getattr(self, "_last_logged_root", None):
                if self._project_root:
                    _module_logger.info(
                        f"[SelfInspector] 项目根目录: {self._project_root}")
                else:
                    _module_logger.info(
                        "[SelfInspector] 项目根目录未配置，将自动检测")
                self._last_logged_root = _root_now
            self._allowed_extensions = cfg.get("allowed_extensions", self._allowed_extensions)
            self._excluded_dirs = cfg.get("excluded_dirs", self._excluded_dirs)
            self._excluded_files = cfg.get("excluded_files", self._excluded_files)
            self._privacy_levels = cfg.get("privacy_levels", {})
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
    def set_info_field(self, info_field):
        """由 main.py 注入 info_field，用于获取框架实际运行状态"""
        self._info_field = info_field
    # ========== 项目结构扫描 ==========
    
    def _should_exclude(self, name: str, is_dir: bool = False) -> bool:
        """判断文件或目录是否应该被排除"""
        if is_dir:
            return name in self._excluded_dirs or name.startswith(".")
        
        for pattern in self._excluded_files:
            if pattern.startswith("*") and name.endswith(pattern[1:]):
                return True
            if name == pattern:
                return True
        return False
    
    def _scan_directory(self, root: str, depth: int = 0, max_depth: int = 3) -> dict[str, Any]:
        """扫描目录结构，返回树形结构"""
        if depth > max_depth:
            return {"name": os.path.basename(root), "type": "dir", "truncated": True}
        
        result = {
            "name": os.path.basename(root),
            "type": "dir",
            "children": [],
        }
        
        try:
            entries = sorted(os.listdir(root))
        except PermissionError:
            return result
        
        for entry in entries:
            full_path = os.path.join(root, entry)
            
            if self._should_exclude(entry, is_dir=os.path.isdir(full_path)):
                continue
            
            if os.path.isdir(full_path):
                child = self._scan_directory(full_path, depth + 1, max_depth)
                if child.get("children") or not child.get("truncated"):
                    result["children"].append(child)
            elif os.path.isfile(full_path):
                ext = os.path.splitext(entry)[1].lower()
                if ext in self._allowed_extensions:
                    try:
                        size = os.path.getsize(full_path)
                    except Exception:
                        size = 0
                    result["children"].append({
                        "name": entry,
                        "type": "file",
                        "ext": ext,
                        "size": size,
                    })
        
        return result
    
    # ========== 器官信息收集 ==========
    
    def _get_organ_categories(self) -> dict[str, list[str]]:
        """
        从实际项目目录结构扫描器官分类。
        不依赖任何人工维护的列表——直接读取 organs/ 目录，
        自动发现所有器官文件。
        
        目录到系统名称的映射是框架约定，属于底层逻辑。
        器官文件的增减会自动反映在统计结果中。
        """
        # 目录名→系统名 的固定映射（框架约定，属于底层逻辑）
        dir_to_system = {
            "brain": "大脑系统",
            "body": "核心脏器",
            "senses": "感知系统",
            "motor": "运动系统",
            "identity": "身份系统",
            "immune": "免疫系统",
            "endocrine": "内分泌系统",
            "genetic": "遗传系统",
            "core": "核心支撑",
        }
        
        result = {}
        
        # 确定 organs 目录的绝对路径
        try:
            project_root = self._project_root or ""
            if not project_root:
                import sys
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
            
            organs_dir = os.path.join(project_root, "organs")
            
            if not os.path.isdir(organs_dir):
                raise FileNotFoundError(f"organs目录不存在: {organs_dir}")
            
            # 遍历 organs/ 下的每个子目录
            for dir_name in os.listdir(organs_dir):
                dir_path = os.path.join(organs_dir, dir_name)
                
                # 跳过非目录、隐藏目录、视觉引擎目录
                if not os.path.isdir(dir_path):
                    continue
                if dir_name.startswith((".", "_")):
                    continue
                if dir_name == "visual_engines":
                    continue
                
                # 获取系统名称
                system_name = dir_to_system.get(dir_name, dir_name)
                
                # 扫描该目录下的 .py 器官文件
                organ_files = []
                for file_name in sorted(os.listdir(dir_path)):
                    if file_name.endswith(".py") and not file_name.startswith("_"):
                        # 去掉 .py 后缀得到器官类名
                        organ_name = file_name[:-3]
                        organ_files.append(organ_name)
                
                if organ_files:
                    result[system_name] = organ_files
            # 扫描特殊位置的器官（不在 organs/ 目录下）
            special_organs = {
                os.path.join(project_root, "nucleus", "qica"): "大脑系统",
            }
            for special_dir, system_name in special_organs.items():
                if os.path.isdir(special_dir):
                    for file_name in sorted(os.listdir(special_dir)):
                        if file_name.endswith(".py") and not file_name.startswith("_"):
                            organ_name = file_name[:-3]
                            if system_name not in result:
                                result[system_name] = []
                            if organ_name not in result[system_name]:
                                result[system_name].append(organ_name)

            return result
            
        except Exception:
            # 扫描失败时降级：尝试从config读取
            try:
                import config
                return {
                    "大脑系统": getattr(config, 'ORGANS_BRAIN', []),
                    "核心脏器": getattr(config, 'ORGANS_BODY', []),
                    "感知系统": getattr(config, 'ORGANS_SENSES', []),
                    "运动系统": getattr(config, 'ORGANS_MOTOR', []),
                    "身份系统": getattr(config, 'ORGANS_IDENTITY', []),
                    "免疫系统": getattr(config, 'ORGANS_IMMUNE', []),
                    "内分泌系统": getattr(config, 'ORGANS_ENDOCRINE', []),
                    "遗传系统": getattr(config, 'ORGANS_GENETIC', []),
                    "核心支撑": getattr(config, 'ORGANS_CORE', []),
                }
            except Exception:
                return {}
    def _get_organ_count(self) -> int:
        """获取器官总数"""
        categories = self._get_organ_categories()
        total = 0
        for organs in categories.values():
            total += len(organs)
        return total
    # ========== 代码解析 ========== 
    def parse_methods(self, file_path: str) -> list[dict[str, Any]]:
        """公开封装 _parse_methods，供代码学习调用（规则14）"""
        return self._parse_methods(file_path)

    def _parse_methods(self, file_path: str) -> list[dict[str, Any]]:
        """
        解析Python文件中的所有方法定义。
        提取方法名、参数列表、docstring首行。
        
        Args:
            file_path: .py文件的绝对路径
        
        Returns:
            方法信息列表 [{"name", "args", "doc", "line_number"}, ...]
        """
        methods = []
        try:
            with open(file_path, encoding="utf-8-sig") as f:
                content = f.read()
            
            # 匹配方法定义：def xxx(self, ...):
            method_pattern = re.compile(
                r'^\s*(?:async\s+)?def\s+(\w+)\s*\((.*?)\)\s*(?:->\s*\S+)?\s*:',
                re.MULTILINE
            )
            
            lines = content.split('\n')
            for match in method_pattern.finditer(content):
                method_name = match.group(1)
                # 跳过内部方法和特殊方法
                if method_name.startswith('_') and method_name != '__init__':
                    if not method_name.startswith('__') or method_name == '__init__':
                        pass
                    else:
                        continue
                
                args = match.group(2)
                line_number = content[:match.start()].count('\n') + 1
                
                # 提取docstring（方法定义后的三引号字符串）
                doc = ""
                start_line = line_number
                while start_line < len(lines):
                    line = lines[start_line].strip()
                    if line.startswith(('"""', "'''")):
                        doc = line.strip('"\'').strip()
                        # 如果是多行docstring，继续读取直到闭合
                        if doc and line.count('"') < 2 and line.count("'") < 2:
                            start_line += 1
                            while start_line < len(lines):
                                next_line = lines[start_line].strip()
                                doc += " " + next_line.strip('"\'').strip()
                                if '"""' in next_line or "'''" in next_line:
                                    break
                                start_line += 1
                        break
                    elif line.startswith('#') or line == '':
                        start_line += 1
                        continue
                    else:
                        break
                
                methods.append({
                    "name": method_name,
                    "args": args.replace("self, ", "").replace("self", ""),
                    "doc": doc[:100] if doc else "",
                    "line_number": line_number,
                })
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        
        return methods
    def _read_method_body(self, file_path: str, start_line: int,
                          method_name: str = "") -> str | None:
        """
        读取指定方法的方法体代码。
        ★v23.0重构：使用ast模块精准识别方法边界，修复缩进误判导致的行数虚高。

        从方法定义的下一行开始读取，直到方法定义的end_lineno。
        自动跳过文档字符串。

        Args:
            method_name: ★PHASE12补丁（2026-09-07）新增的可选参数，
                用于行号匹配失败时按方法名兜底定位。不传则完全保持原行为。
        """
        try:
            import ast

            # ★v23.0修复：使用AST缓存，避免每个方法重复解析整个文件
            _file_mtime = os.path.getmtime(file_path)
            if file_path in self._ast_cache:
                _cached_mtime, _cached_tree = self._ast_cache[file_path]
                if _cached_mtime == _file_mtime:
                    tree = _cached_tree
                    content = self._ast_content_cache.get(file_path, "")
                else:
                    with open(file_path, encoding="utf-8-sig") as f:
                        content = f.read()
                    tree = ast.parse(content, filename="<llm-patch>")
                    self._ast_cache[file_path] = (_file_mtime, tree)
                    self._ast_content_cache[file_path] = content
            else:
                with open(file_path, encoding="utf-8-sig") as f:
                    content = f.read()
                tree = ast.parse(content, filename="<llm-patch>")
                self._ast_cache[file_path] = (_file_mtime, tree)
                if not hasattr(self, '_ast_content_cache'):
                    self._ast_content_cache = {}
                self._ast_content_cache[file_path] = content

            # 缓存容量保护
            if len(self._ast_cache) > self._ast_cache_max:
                _oldest = next(iter(self._ast_cache.keys()))
                del self._ast_cache[_oldest]
                if hasattr(self, '_ast_content_cache') and _oldest in self._ast_content_cache:
                    del self._ast_content_cache[_oldest]
                # ★PHASE14：索引与 AST 缓存同生命周期，必须一起淘汰，
                #   否则 _ast_fn_index 会随扫描文件数无限增长（内存泄漏）。
                if not hasattr(self, '_ast_fn_index'):
                    self._ast_fn_index = {}
                self._ast_fn_index.pop(_oldest, None)
                if not hasattr(self, '_ast_lines_cache'):
                    self._ast_lines_cache = {}
                self._ast_lines_cache.pop(_oldest, None)

            target_node = None

            # ★PHASE14：先取（或建）方法索引，把「全树遍历」从每次调用降为每文件一次。
            _idx_entry = self._ast_fn_index.get(file_path)
            if _idx_entry is None or _idx_entry[0] != _file_mtime:
                _by_line: dict[int, Any] = {}
                _by_name: dict[str, list] = {}
                for _n in ast.walk(tree):
                    if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # setdefault：同名方法（重载/嵌套）保留首个，
                        # 与旧逻辑「取行号最接近者」不同的分支另行处理
                        _by_line.setdefault(_n.lineno, _n)
                        _by_name.setdefault(_n.name, []).append(_n)
                _idx_entry = (_file_mtime, _by_line, _by_name)
                self._ast_fn_index[file_path] = _idx_entry
            _by_line, _by_name = _idx_entry[1], _idx_entry[2]

            # O(1) 精确命中
            target_node = _by_line.get(start_line)
            # ★主线第75批 T3：精确命中须计入统计（此前漏记 → 精确匹配率恒为 0%）
            if target_node is not None:
                _body_loc_tick("exact", 0)

            # ★PHASE14：偏移试探。实测偏移分布——偏移 +1 占 23283 次、
            #   +2 占 1390 次，即**几乎全部是 +1/+2**，属于系统性偏差而非随机噪声。
            #   先做常数次 O(1) 试探，命中就直接返回，省掉整棵树遍历。
            #   安全约束：若调用方给了 method_name，则候选必须同名才算命中，
            #   避免「上一方法恰好结束在 start_line+1」导致张冠李戴。
            if target_node is None:
                for _delta in (1, 2, -1, -2, 3, -3):
                    _cand = _by_line.get(start_line + _delta)
                    if _cand is not None and (
                            not method_name or _cand.name == method_name):
                        target_node = _cand
                        # ★主线第75批 T3：±1 行偏移属装饰器/解析 off-by-one，计为精确命中；
                        #   |偏移|≥2 才计为偏移试探（实测 +1/+2 占 99.8%，精确率将显著回升）
                        if abs(_delta) <= 1:
                            _body_loc_tick("exact", _cand.lineno - start_line)
                        else:
                            _body_loc_tick("offset_probe", _cand.lineno - start_line)
                        break

            # ★PHASE12-P1-2扩展（2026-09-07）：行号匹配失败时按方法名兜底。
            #   全项目实测：_parse_methods 用正则解析出的 line_number 与 AST 的
            #   def 行号存在系统性 off-by-one（多指向装饰器行 / 解析偏移），
            #   60 个文件抽样 1063 个方法中**仅 39.8%** 行号能精确命中 AST，
            #   其余 60.2% 一律走到 `return None` → 代码片段取不到
            #   → 本地规则与 LLM 通道双双不触发
            #   → 这正是日志里「6个因缺少organ/method无法定位代码而跳过，占100%」的真凶。
            #   兜底策略：同名方法中取行号最接近的一个（偏差通常 ≤3 行），
            #   既修正 off-by-one，又不会跨方法误配。原逻辑完全保留（★叠加非替换）。
            # 最后的兜底：按方法名在索引中查找（索引已是 O(1) 取桶，
            #   只在同名多实现时才做一次 min，代价远小于全树 walk）
            if target_node is None and method_name:
                _candidates = _by_name.get(method_name) or []
                if not _candidates:
                    # ★主线第75批 T3：精确方法名未命中时放宽到前缀/包含匹配
                    #   （调用方常传截断/带修饰的方法名，如 _do_x vs _do_x_impl）
                    _prefix_hits = [
                        _ns for _nm, _ns in _by_name.items()
                        if _nm.startswith(method_name) or method_name.startswith(_nm)
                    ]
                    _candidates = [n for _ns in _prefix_hits for n in _ns]
                if _candidates:
                    if len(_candidates) == 1:
                        target_node = _candidates[0]
                    else:
                        target_node = min(_candidates,
                                          key=lambda n: abs(n.lineno - start_line))
                    # ★PHASE14：逐条 DEBUG 改为聚合计数。
                    #   这一条在 2h33m 里打了 24673 次，占 self_inspector 日志的 99.8%。
                    _body_loc_tick("fallback", target_node.lineno - start_line)
                    # ★关键：后续 body_start_idx / 文档字符串判断都以 start_line 为基准，
                    #   命中节点行号与之不一致时必须以 AST 真实行号为准，
                    #   否则会把上一行（多为装饰器）当成方法体首行。
                    start_line = target_node.lineno

            if target_node is None:
                return None

            # ★PHASE14：行列表缓存（按 mtime 失效）。
            #   原实现每次调用都 content.split('\n')，对 PulseLiver 这类
            #   170KB / 数千行的文件等于每次重建一个大列表，实测它才是
            #   _read_method_body 的主要耗时项（比 AST 遍历更贵）。
            _lines_entry = self._ast_lines_cache.get(file_path)
            if _lines_entry is not None and _lines_entry[0] == _file_mtime:
                lines = _lines_entry[1]
            else:
                lines = content.split('\n')
                self._ast_lines_cache[file_path] = (_file_mtime, lines)
            # 方法体开始行：函数定义的下一行（1-based -> 0-based索引为 start_line）
            body_start_idx = start_line
            # 方法体结束行：AST提供的end_lineno（1-based），转为0-based索引
            body_end_idx = target_node.end_lineno

            # 跳过文档字符串：找到第一个实际代码语句的行号
            first_stmt_lineno = target_node.body[0].lineno if target_node.body else None
            if first_stmt_lineno and first_stmt_lineno > start_line:
                # 如果第一条语句不是文档字符串，直接使用
                # 如果第一条语句是文档字符串，AST会自动跳过吗？不会，我们需要手动判断
                _first_node = target_node.body[0]
                if isinstance(_first_node, ast.Expr) and isinstance(_first_node.value, ast.Constant) and isinstance(_first_node.value.value, str):
                    # 是文档字符串，从文档字符串结束的下一行开始
                    body_start_idx = _first_node.end_lineno
                else:
                    body_start_idx = first_stmt_lineno - 1  # 转为0-based

            # 提取方法体行
            body_lines = []
            for i in range(body_start_idx, body_end_idx):
                if i < len(lines):
                    body_lines.append(lines[i])

            if body_lines:
                # ★v25.2修复(2026-09-04): 不使用.strip()，它会破坏首行缩进，
                # 导致textwrap.dedent()无法正确处理。改为：
                # 1. 去除首尾空行
                # 2. 保留非空行的原始缩进
                # 3. 确保缩进一致性（所有非空行有相同的最小缩进）
                while body_lines and not body_lines[0].strip():
                    body_lines.pop(0)
                while body_lines and not body_lines[-1].strip():
                    body_lines.pop()
                if body_lines:
                    return "\n".join(body_lines)
            return None

        except Exception:
            # AST解析失败时，回退到旧的缩进检测方法
            return self._read_method_body_fallback(file_path, start_line)

    def _read_method_body_fallback(self, file_path: str, start_line: int) -> str | None:
        """
        ★v23.0新增：旧的缩进检测方法，作为AST失败时的回退。
        """
        try:
            with open(file_path, encoding="utf-8-sig") as f:
                lines = f.readlines()

            if start_line >= len(lines):
                return None

            def_line = lines[start_line - 1] if start_line > 0 else ""
            def_indent = len(def_line) - len(def_line.lstrip())

            body_lines = []
            in_docstring = False
            docstring_delimiter = None
            started_body = False

            for i in range(start_line, len(lines)):
                line = lines[i]
                stripped = line.strip()

                if not stripped and not started_body:
                    continue

                current_indent = len(line) - len(line.lstrip())

                if not started_body and (stripped.startswith(('"""', "'''"))):
                    in_docstring = True
                    docstring_delimiter = stripped[:3]
                    if stripped.count(docstring_delimiter) >= 2 and len(stripped) > 3:
                        in_docstring = False
                    continue

                if in_docstring:
                    if docstring_delimiter and docstring_delimiter in stripped:
                        in_docstring = False
                    continue

                if stripped and current_indent <= def_indent and started_body:
                    break

                if not in_docstring and stripped:
                    started_body = True
                    body_lines.append(line)
                elif started_body:
                    body_lines.append(line)

            if body_lines:
                # ★T-115a 防御保险丝：切片长度 > 2×(至下一顶层 def 距离) 即视为失控切片，
                #   直接丢弃（BOM/编码异常会令缩进误判，fallback 一路吃到文件末尾）。
                _next_def_line = None
                for _j in range(start_line + 1, len(lines)):
                    _s = lines[_j].strip()
                    if _s.startswith(("def ", "class ", "async def ")) \
                            and (len(lines[_j]) - len(lines[_j].lstrip())) == 0:
                        _next_def_line = _j + 1
                        break
                if _next_def_line is not None:
                    _expected = _next_def_line - start_line
                    if len(body_lines) > 2 * _expected:
                        self._log(LogLevel.DEBUG,
                                  f"T-115a: fallback 切片失控(len={len(body_lines)}>2×{_expected})，丢弃")
                        return None
                return "".join(body_lines).strip()
            return None

        except Exception:
            return None
    def _analyze_method_calls(self, file_path: str, method_name: str, 
                               start_line: int) -> list[str]:
        """
        分析方法体内部调用的其他方法名。
        
        通过正则匹配方法体中的 self.xxx() 调用模式，
        提取被调用的方法名列表，用于建立代码知识之间的关联。
        
        Args:
            file_path: .py文件的绝对路径
            method_name: 当前方法名
            start_line: 方法定义所在行号（1-based）
        
        Returns:
            被调用的方法名列表（去重）
        """
        body = self._read_method_body(file_path, start_line, method_name=method_name)
        if not body:
            return []
        
        called_methods = set()
        
        # ★FIX(代码学习正确性): 剥离注释行与字符串字面量，避免把注释/字符串中的伪代码当真实调用
        _body_clean = []
        for _line in body.split('\n'):
            _line = _line.split('#', 1)[0]
            _body_clean.append(_line)
        body = '\n'.join(_body_clean)
        body = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', ' ', body)
        body = re.sub(r'"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\'', ' ', body)
        
        # 匹配 self.xxx( 调用模式
        self_call_pattern = re.compile(r'self\.(\w+)\s*\(')
        for match in self_call_pattern.finditer(body):
            called = match.group(1)
            # 排除当前方法自身（递归调用）
            if called != method_name:
                called_methods.add(called)
        
        # 匹配 cls.xxx( 或首字母大写类名.xxx( 静态/类方法调用（排除 dict/str/json/os 等外部模块污染）
        class_call_pattern = re.compile(r'(?:cls|[A-Z]\w*)\.(\w+)\s*\(')
        for match in class_call_pattern.finditer(body):
            called = match.group(1)
            if called != method_name and not called.startswith('_'):
                called_methods.add(called)
        
        return sorted(called_methods)

    def _build_call_chain(self, file_path: str, method_name: str,
                          start_line: int, depth: int = 0,
                          max_depth: int = 3, visited: set | None = None) -> dict[str, Any]:
        """★第二阶段：构建完整调用链（递归分析被调用方法的调用）。

        Args:
            file_path: 文件路径
            method_name: 起始方法名
            start_line: 起始方法行号
            depth: 当前递归深度
            max_depth: 最大递归深度（防止无限循环）
            visited: 已访问的方法集合（防止循环调用）

        Returns:
            调用链字典 {method, calls: [{method, calls: [...]}]}
        """
        if visited is None:
            visited = set()
        if depth >= max_depth or method_name in visited:
            return {"method": method_name, "calls": [], "truncated": depth >= max_depth}

        visited.add(method_name)
        direct_calls = self._analyze_method_calls(file_path, method_name, start_line)

        # 解析文件中的所有方法，查找被调用方法的行号
        all_methods = {}
        try:
            classes = self._parse_classes(file_path)
            for cls in classes:
                for m in cls.get("methods", []):
                    all_methods[m["name"]] = m.get("line_number", 0)
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:987")

        call_chain = []
        for called in direct_calls:
            called_line = all_methods.get(called, 0)
            if called_line > 0:
                sub_chain = self._build_call_chain(
                    file_path, called, called_line, depth + 1, max_depth, visited.copy()
                )
                call_chain.append(sub_chain)
            else:
                call_chain.append({"method": called, "calls": [], "external": True})

        return {"method": method_name, "calls": call_chain}

    def _find_callers(self, file_path: str, target_method: str) -> list[str]:
        """★第二阶段：查找哪些方法调用了目标方法（反向调用分析）。

        Args:
            file_path: 文件路径
            target_method: 目标方法名

        Returns:
            调用者方法名列表
        """
        callers = set()
        try:
            classes = self._parse_classes(file_path)
            for cls in classes:
                for method in cls.get("methods", []):
                    mname = method["name"]
                    if mname == target_method:
                        continue
                    mline = method.get("line_number", 0)
                    if mline > 0:
                        calls = self._analyze_method_calls(file_path, mname, mline)
                        if target_method in calls:
                            callers.add(mname)
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1026")
        return sorted(callers)

    def _get_class_context(self, file_path: str, class_name: str) -> dict[str, Any]:
        """★第二阶段：获取类上下文信息（基类、属性、其他方法）。

        Args:
            file_path: 文件路径
            class_name: 类名

        Returns:
            类上下文字典
        """
        try:
            classes = self._parse_classes(file_path)
            for cls in classes:
                if cls["name"] == class_name:
                    methods = cls.get("methods", [])
                    return {
                        "class_name": class_name,
                        "base_class": cls.get("base_class", ""),
                        "method_count": len(methods),
                        "methods": [m["name"] for m in methods],
                        "doc": cls.get("doc", ""),
                    }
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1052")
        return {"class_name": class_name, "error": "class not found"}

    # ===== ★PHASE12-P1-2扩展：全项目类索引（get_method_body 的兜底数据源）=====

    def _build_project_class_index(self) -> dict[str, dict[str, Any]]:
        """扫描全项目（不止 organs/）建立「类名 → 文件 + 方法」索引。

        为什么需要它：
            _scan_all_organs() 的语义是「器官」，只扫 organs/ 目录。
            但自主进化发现的问题大量位于 nucleus/（250 个类）、functions/、
            base/、main.py 等核心模块，这些位置 get_method_body 一律返回 None，
            导致代码片段取不到、本地规则与 LLM 通道双双不触发。
            实测覆盖率仅 36.4%（150/412 类），本索引把覆盖率补到 100%。

        性能与稳定性约束（★分级质量保障）：
            - 结果缓存到 self._project_class_index，只在首次 miss 时构建一次，
              后续直接命中缓存，不会每轮进化都全项目扫一遍。
            - 构建过程整体 try 包裹：任何解析失败只跳过该文件，不影响整体。
            - 排除 venv/.git/logs/data/backups/models 等无关目录，控制扫描规模。
        """
        index: dict[str, dict[str, Any]] = {}
        try:
            project_root = self._project_root or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            _fallback_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if not os.path.isdir(project_root) and os.path.isdir(_fallback_root):
                project_root = _fallback_root

            _skip_dirs = {"__pycache__", ".git", "venv", ".venv", "node_modules",
                          "logs", "data", "backups", "models", ".idea", ".vscode",
                          "dist", "build", ".pytest_cache", "tmp"}
            for _dirpath, _dirnames, _filenames in os.walk(project_root):
                _dirnames[:] = [d for d in _dirnames if d not in _skip_dirs
                                and not d.startswith(".")]
                for _fn in _filenames:
                    if not _fn.endswith(".py") or _fn.startswith("_"):
                        continue
                    _fp = os.path.join(_dirpath, _fn)
                    try:
                        # ★用 AST 而非正则构建索引。原因（实测）：
                        #   ① _parse_classes 的正则 `class X(...)` 只匹配**带括号**的类，
                        #      `class SafeEvolutionExecutor:` 这类无继承类直接返回空；
                        #   ② _parse_methods 的正则参数段 `(.*?)` 不跨行，
                        #      多行参数定义的方法（如 _dispatch_pulse）全部漏掉；
                        #   ③ 两者算出的 line_number 与 AST 真实 lineno 系统性 off-by-one。
                        #   AST 一次解析同时拿到准确类名、方法名、行号，三项缺陷一起消除。
                        with open(_fp, encoding="utf-8", errors="ignore") as _f:
                            _src = _f.read()
                        _tree = ast.parse(_src, filename="<llm-patch>")
                        _top_classes = [n for n in _tree.body if isinstance(n, ast.ClassDef)]
                        if not _top_classes:
                            continue
                        for _cls_node in _top_classes:
                            _cname = _cls_node.name
                            if not _cname or _cname in index:
                                continue
                            _methods = [
                                {"name": sub.name, "args": "", "doc": "",
                                 "line_number": sub.lineno}
                                for sub in _cls_node.body
                                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                            ]
                            if not _methods:
                                continue
                            index[_cname] = {
                                "file_path": _fp,
                                "directory": os.path.basename(_dirpath),
                                "classes": [{"name": _cname}],
                                "methods": _methods,
                                "method_count": len(_methods),
                            }
                    except Exception:
                        continue
            _module_logger.info(
                f"[SelfInspector] 全项目类索引构建完成: {len(index)} 个类"
                f"（organs/ 之外，供自主进化定位核心模块代码）")
        except Exception as _e:
            _module_logger.warning(f"[SelfInspector] 全项目类索引构建失败: {_e}")
        return index

    def _lookup_class_in_project(self, class_name: str) -> dict[str, Any] | None:
        """在「全项目类索引」中按类名查找（带懒构建 + 双重检查锁）。"""
        if not class_name:
            return None
        try:
            if self._project_class_index is None:
                with self._project_class_index_lock:
                    if self._project_class_index is None:
                        self._project_class_index = self._build_project_class_index()
            return (self._project_class_index or {}).get(class_name)
        except Exception:
            return None

    # ========== ★主线第65批 T3/P2：get_method_body 文件级缓存 ==========
    def _get_method_body_cached(self, file_path: str, method_name: str):
        """缓存命中（文件 mtime 未变）→ 返回结果副本；否则 None（调用方走原路径）。"""
        if not self._scan_cache_enabled or not file_path:
            return None
        _key = (file_path, method_name)
        _ent = self._method_body_cache.get(_key)
        if _ent is not None:
            _result, _mtime = _ent
            try:
                if os.path.getmtime(file_path) == _mtime:
                    self._method_body_hits += 1
                    return dict(_result)   # 独立副本，避免调用方改脏缓存
            except Exception as _se:
                silent_exc(_se, "self_inspector.py:1160")
            self._method_body_cache.pop(_key, None)   # mtime 变化/读取失败→失效
        self._method_body_misses += 1
        return None

    def _put_method_body_cached(self, file_path: str, method_name: str, result: dict) -> None:
        """写入缓存（记录文件 mtime）；超过 1000 条 FIFO 淘汰。"""
        if not self._scan_cache_enabled or not file_path:
            return
        try:
            _mtime = os.path.getmtime(file_path)
        except Exception:
            return
        self._method_body_cache[(file_path, method_name)] = (dict(result), _mtime)
        if len(self._method_body_cache) > 1000:
            try:
                self._method_body_cache.pop(next(iter(self._method_body_cache)))
            except Exception as _se:
                silent_exc(_se, "self_inspector.py:1178")

    def get_method_body(self, organ_name: str, method_name: str) -> dict[str, Any] | None:
        """
        获取指定方法的完整信息，包括方法体代码。
        
        Args:
            organ_name: 器官类名（如"PulseLiver"）或任意类名（如"InfoField"）
            method_name: 方法名（如"_compress_group"）

        Returns:
            包含方法详情和代码体的字典，如果找不到则返回None
        """
        all_organs = self._scan_all_organs()
        organ_info = all_organs.get(organ_name)
        if not organ_info:
            # ★PHASE12-P1-2扩展（2026-09-07）：器官目录查不到时，兜底查全项目类索引。
            #   nucleus/ / functions/ / base/ / main.py 等核心模块的类都在这里，
            #   此前这些位置的问题 100% 因「无法定位代码」被跳过（15小时实证：
            #   每轮处理 6 个、6 个全部跳过、修复 0 个）。
            #   保留原有「查不到返回 None」的语义，仅在 miss 时多查一次（★叠加）。
            organ_info = self._lookup_class_in_project(organ_name)
            if not organ_info:
                return None

        for method in organ_info.get("methods", []):
            if method["name"] == method_name:
                file_path = organ_info.get("file_path", "")
                # ★主线第65批 T3/P2：文件级缓存命中（mtime 未变直接复用，省去文件读取/解析）
                _cached = self._get_method_body_cached(file_path, method_name)
                if _cached is not None:
                    _cached = dict(_cached)
                    _cached["organ"] = organ_name
                    _cached["name"] = method_name
                    return _cached
                # ★PHASE12-P1-2扩展：传入方法名，启用「按名兜底」定位（修正 off-by-one）
                body = self._read_method_body(file_path, method.get("line_number", 0),
                                              method_name=method_name)
                # 分析方法体内部的调用关系
                _called_methods = self._analyze_method_calls(
                    file_path, method["name"], method.get("line_number", 0)
                )
                # ★第二阶段增强：类上下文、完整调用链、被调用分析
                _class_ctx = self._get_class_context(file_path, organ_name)
                _call_chain = self._build_call_chain(
                    file_path, method["name"], method.get("line_number", 0)
                )
                _callers = self._find_callers(file_path, method["name"])
                _out = {
                    "organ": organ_name,
                    "name": method["name"],
                    "args": method.get("args", ""),
                    "doc": method.get("doc", ""),
                    "line_number": method.get("line_number", 0),
                    "body": body or "",
                    "has_doc": bool(method.get("doc", "")),
                    "called_methods": _called_methods,
                    # ★第二阶段新增字段
                    "class_context": _class_ctx,
                    "call_chain": _call_chain,
                    "callers": _callers,
                    "caller_count": len(_callers),
                }
                # ★主线第65批 T3/P2：写入文件级缓存（绑文件 mtime，供后续同方法调用复用）
                self._put_method_body_cached(file_path, method_name, _out)
                return _out
        
        return None   
    # ========== ★P0-7 代码定位能力（主线第1批 任务1） ==========
    # 背景：LogAnalyzer 对普通 ERROR 行只传 file="" / method="" / line=0，
    #   导致 SafeEvolutionExecutor 拿不到代码位置 → 无法生成补丁 →
    #   自主进化修复率长期 ~0%（实测 208 个问题只修 1 个）。
    #   此处补上「器官名 + 错误消息 → 代码位置」的反推能力。

    _ORGAN_SCAN_ROOTS = ("organs", "nucleus")
    # 匹配统一头部首行：PulseInnerWorld —— 内在世界核心推理器官
    _HEAD_CN_RE = re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*——[ \t]*([^ \t\r\n（）()]+)"
    )
    # ★第91批 T-91a：两级新索引的扫描根 / 排除规则 / 取键正则
    #   扫描根 = 生产源码（非器官的 nucleus/*、functions/、base/、utils/ 也有 logger 字面量）
    _M91_TAG_SCAN_ROOTS = ("nucleus", "organs", "functions", "base", "utils")
    _M91_TAG_SCAN_FILES = ("main.py", "config.py")
    # ★危险解析防护：备份/临时/缓存目录一律排除。
    #   第91批 T0 实测：朴素 file-walk 会把 `self_inspector` 解析到
    #   `.bak_batch75/.release-tmp/nucleus/self_inspector.py` —— 定位到**陈旧副本**，
    #   后续取方法体/生成补丁全部作用在错误路径上。`^\.` 一并排除所有点目录。
    #   ★只排除「点目录 + 缓存 + 虚拟环境」——**不按目录名排除**
    #   data/logs/build/tmp 等：第91批 T0 实测这类规则会误伤合法子包
    #   （nucleus/data 含 write_guard.py == [WriteGuard] 标签来源；
    #     nucleus/pulse/build、organs/brain/logs 同理）。
    #   扫描根本身已不含顶层 data//logs//tmp/，无需再按名字排除。
    #   _m91_skip_dir_fix
    _M91_SKIP_DIR_RE = re.compile(
        r"(^\.|^__pycache__$|^venv$|^\.venv$|^node_modules$)")
    # logger 名字面量调用（与 nucleus/logger.py 的工厂函数同型）
    _M91_LOGGER_CALL_RE = re.compile(
        r"(?:get_organ_logger|get_module_logger|get_logger|logging\.getLogger)"
        r"\s*\(\s*['\"]([^'\"]{1,60})['\"]")
    # organ_name 声明的两种真实形式（T0 实测：PulseThymus/PulseEyes/... 用带标注默认值）
    _M91_ORGAN_NAME_RES = (
        re.compile(r"""organ_name\s*:\s*str\s*=\s*['\"]([^'\"]{1,20})['\"]"""),
        re.compile(r"""organ_name\s*=\s*['\"]([^'\"]{1,20})['\"]"""),
    )

    def _build_organ_name_index(self) -> dict[str, str]:
        """构建「中文器官名 → 文件相对路径」索引（扫描文件头部，零硬编码）。

        ★依赖：头部完善四批次建立的统一头部（首行 `ClassName —— 中文器官名`）。
        新增器官无需维护映射表，自动纳入索引——这是头部规范化的直接收益。
        """
        idx = getattr(self, "_organ_name_index", None)
        if idx:
            return idx
        idx = {}
        _bs = chr(92)
        try:
            _base = self._project_root or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            for _sub in self._ORGAN_SCAN_ROOTS:
                _abs = os.path.join(_base, _sub)
                if not os.path.isdir(_abs):
                    continue
                for _dirpath, _dirnames, _filenames in os.walk(_abs):
                    _dirnames[:] = [d for d in _dirnames
                                    if not d.startswith(".") and d != "__pycache__"]
                    for _fn in _filenames:
                        if not _fn.endswith(".py"):
                            continue
                        _fp = os.path.join(_dirpath, _fn)
                        try:
                            with open(_fp, encoding="utf-8", errors="ignore") as _f:
                                _m = self._HEAD_CN_RE.search(_f.read(800))
                        except Exception:
                            continue
                        if _m:
                            _cn = _m.group(2)
                            if _cn and _cn not in idx:
                                idx[_cn] = os.path.relpath(
                                    _fp, _base).replace(_bs, "/")
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1294")
        self._organ_name_index = idx
        return idx

    def guess_method_from_message(self, organ: str, msg: str) -> str:
        """从错误消息中猜测方法名。

        支持模式（按优先级）：
          1. Traceback 风格：File "...", line N, in xxx
          2. 行尾 in xxx
          3. 方法 xxx / method xxx
          4. def xxx
          5. xxx() 调用形态

        Returns:
            方法名；无法猜测时返回空字符串
        """
        if not msg:
            return ""
        _patterns = (
            r'File[ \t]+"[^"]+",[ \t]*line[ \t]*[0-9]+,[ \t]*in[ \t]+([A-Za-z_][A-Za-z0-9_]*)',
            r'(?:^|[ \t])in[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?=[ \t,]|$)',
            r'(?:方法|method)[ \t]*[`"]?([A-Za-z_][A-Za-z0-9_]*)',
            r'(?:^|[ \t])def[ \t]+([A-Za-z_][A-Za-z0-9_]*)',
            r'([a-z_][a-z0-9_]{3,})[ \t]*\(\)',
        )
        for _p in _patterns:
            try:
                _m = re.search(_p, msg)
            except Exception:
                continue
            if _m:
                return _m.group(1)
        return ""

    # ========== ★第91批 T-91a：日志调用点定位主能力（两级数据驱动索引） ==========
    #   与旧 `_build_organ_name_index()` 的**根本区别**：旧索引读「文件头中文短语」，
    #   本处两级索引读「真正产生日志 TAG 的命名源」（logger 名 / organ_name 声明），
    #   故与日志生产者**同源**，不依赖头部书写规范。

    def _m91_project_base(self) -> str:
        """项目根（本文件位于 <root>/nucleus/self_inspector.py）。"""
        return self._project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

    def _m91_iter_production_py(self):
        """产出生产源码的相对路径（posix 分隔）；备份/临时目录已排除。"""
        _base = self._m91_project_base()
        _bs = chr(92)
        for _sub in self._M91_TAG_SCAN_ROOTS:
            _abs = os.path.join(_base, _sub)
            if not os.path.isdir(_abs):
                continue
            for _dp, _dn, _fn in os.walk(_abs):
                _dn[:] = [d for d in _dn if not self._M91_SKIP_DIR_RE.search(d)]
                for _f in _fn:
                    if _f.endswith(".py"):
                        yield os.path.relpath(
                            os.path.join(_dp, _f), _base).replace(_bs, "/")
        for _f in self._M91_TAG_SCAN_FILES:
            if os.path.isfile(os.path.join(_base, _f)):
                yield _f

    @staticmethod
    def _m91_logger_literal_to_tags(lit: str) -> list:
        """logger 名字面量 → 可能的日志 TAG（与 PulseFormatter 规则**互逆**）。

        PulseFormatter: `pulse.organ.胸腺`→`胸腺` ; `pulse.module.WriteGuard`→`WriteGuard`
                        `pulse.framework`→`框架`   ; 其余→原名
        故一个字面量可能对应多个 TAG，全部登记（多登记不会误命中——TAG 取的是
        日志里真实出现过的字符串）。
        """
        _orig = str(lit or "").strip()
        if not _orig:
            return []
        _s = _orig
        for _p in ("pulse.organ.", "pulse.module."):
            if _s.startswith(_p):
                _s = _s[len(_p):]
                break
        _out = []
        for _cand in (_orig, _s, _s.split(".")[-1]):
            if _cand and _cand not in _out:
                _out.append(_cand)
        if _s.startswith("Pulse") and len(_s) > 5 and _s[5:] not in _out:
            _out.append(_s[5:])
        return _out

    def _build_logger_tag_index(self) -> dict:
        """构建「日志 TAG → 文件相对路径」索引（★第91批 T-91a）。

        零硬编码：只做静态扫描，新增模块/器官无需维护映射表。
        多候选（同一字面量出现在多个文件）时按
        「路径层级最少 → 最短 → 字典序」确定性择一，并记入 `_m91_logger_tag_ambig`。
        """
        _cached = getattr(self, "_m91_logger_tag_index", None)
        if _cached is not None:
            return _cached
        idx = {}
        _cands = {}
        _ambig = set()
        try:
            for _rel in self._m91_iter_production_py():
                _fp = os.path.join(self._m91_project_base(), _rel)
                try:
                    with open(_fp, encoding="utf-8", errors="ignore") as _f:
                        _src = _f.read()
                except Exception:
                    continue
                for _m in self._M91_LOGGER_CALL_RE.finditer(_src):
                    for _tag in self._m91_logger_literal_to_tags(_m.group(1)):
                        _lst = _cands.setdefault(_tag, [])
                        if _rel not in _lst:
                            _lst.append(_rel)
            for _tag, _lst in _cands.items():
                idx[_tag] = sorted(_lst, key=lambda p: (p.count("/"), len(p), p))[0]
                if len(_lst) > 1:
                    _ambig.add(_tag)
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:_build_logger_tag_index")
        self._m91_logger_tag_index = idx
        self._m91_logger_tag_ambig = _ambig
        return idx

    def _build_organ_alias_index(self) -> dict:
        """构建「中文器官别名（organ_name 声明）→ 文件相对路径」索引（★第91批 T-91a）。

        与 `_build_organ_name_index()` 并存而非替换（最小侵入 + 零回归）：
        旧索引继续服务头部短语型标签，本索引补上 organ_name 声明型标签。
        """
        _cached = getattr(self, "_m91_organ_alias_index", None)
        if _cached is not None:
            return _cached
        idx = {}
        _bs = chr(92)
        try:
            _base = self._m91_project_base()
            for _sub in self._ORGAN_SCAN_ROOTS:
                _abs = os.path.join(_base, _sub)
                if not os.path.isdir(_abs):
                    continue
                for _dp, _dn, _fn in os.walk(_abs):
                    _dn[:] = [d for d in _dn
                              if not self._M91_SKIP_DIR_RE.search(d)]
                    for _f in _fn:
                        if not _f.endswith(".py"):
                            continue
                        _rel = os.path.relpath(
                            os.path.join(_dp, _f), _base).replace(_bs, "/")
                        try:
                            with open(os.path.join(_dp, _f),
                                      encoding="utf-8", errors="ignore") as _fh:
                                _src = _fh.read()
                        except Exception:
                            continue
                        for _re in self._M91_ORGAN_NAME_RES:
                            for _m in _re.finditer(_src):
                                _cn = _m.group(1)
                                if _cn and _cn not in idx:
                                    idx[_cn] = _rel
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:_build_organ_alias_index")
        self._m91_organ_alias_index = idx
        return idx

    def _m91_lookup_logger_tag(self, tag: str) -> str | None:
        """日志 TAG → 真实存在的文件相对路径（不存在则 None，绝不返回死路径）。"""
        _rel = self._build_logger_tag_index().get(str(tag or "").strip())
        if not _rel:
            return None
        try:
            if os.path.isfile(os.path.join(self._m91_project_base(), _rel)):
                return _rel
        except Exception:
            return None
        return None

    def _m91_lookup_organ_alias(self, tag: str) -> str | None:
        """中文器官别名 → 真实存在的文件相对路径（不存在则 None）。"""
        _rel = self._build_organ_alias_index().get(str(tag or "").strip())
        if not _rel:
            return None
        try:
            if os.path.isfile(os.path.join(self._m91_project_base(), _rel)):
                return _rel
        except Exception:
            return None
        return None

    def locate_issue(self, organ: str, msg: str, file_hint: str = "") -> dict[str, Any]:
        """综合定位问题的代码位置。

        Args:
            organ: 中文器官名（如"内在世界"），来自日志的 [器官] 标记
            msg: 错误消息原文
            file_hint: 可选的文件提示（相对路径或文件名）

        Returns:
            {file, method, line, confidence}
            ★confidence < 0.5 表示定位不可靠，调用方应标记为"需人工确认"，
              不进入自动修复队列。
        """
        _out: dict[str, Any] = {"file": "", "method": "", "line": 0, "confidence": 0.0}
        _bs = chr(92)
        _base = self._project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        msg = msg or ""

        # ---- 1) 确定文件（三路，按可信度从高到低） ----
        _file, _conf = "", 0.0

        # 1a. 消息里直接带文件路径/文件名 —— 最可信
        try:
            _m = re.search(r'File[ \t]+"([^"]+[.]py)"', msg) or re.search(
                r'([A-Za-z_][A-Za-z0-9_/]*[.]py)', msg)
            if _m:
                _cand = _m.group(1).replace(_bs, "/")
                if os.path.exists(os.path.join(_base, _cand)):
                    _file, _conf = _cand, 0.9
                else:
                    _bn = os.path.basename(_cand)
                    for _p in self._build_organ_name_index().values():
                        if os.path.basename(_p) == _bn:
                            _file, _conf = _p, 0.8
                            break
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1366")

        # 1b. file_hint
        if not _file and file_hint:
            _cand = str(file_hint).replace(_bs, "/")
            if os.path.exists(os.path.join(_base, _cand)):
                _file, _conf = _cand, 0.85
            else:
                _bn = os.path.basename(_cand)
                for _p in self._build_organ_name_index().values():
                    if os.path.basename(_p) == _bn:
                        _file, _conf = _p, 0.75
                        break

        # 1c. 器官名/类名 → 文件（★第90批 T-90b bug#2/#3 修复）
        #   改前只用 `_build_organ_name_index()`（243 个**中文**头部名）且模糊匹配
        #   是**前缀**关系 ⇒ 两类真实标签恒不命中：
        #     bug#2：主源码日志标签多为**类名**（InfoField / PulseSnapshot /
        #            PulseNodePool / SafeEvolutionExecutor …），中文名索引里根本没有；
        #            而第87批已建好的「全项目类索引」（1007 类）从未接进本函数。
        #     bug#3：中文标签与头部名常是**包含**关系而非前缀
        #            （`胃` vs `脉冲驱动胃`、`框架` vs `能力框架`）⇒ startswith 双向皆假。
        #   实测（logs/pulse.log 的 15 个真实日志类问题标签）：命中 6/15 = 40.0%
        #   → 修后 13/15 = 86.7%。开关关闭 → 逐字回到改前。
        if not _file and organ:
            _m90_v2 = _m90_locate_v2_on()
            _m91_v3 = (_m90_v2 and _m91_log_locate_v3_on())
            if _m90_v2:
                # 1c-a：全项目类索引（类名精确匹配，置信度 0.75 —— 低于 1a 的 0.9）
                try:
                    _cls_hit = self._lookup_class_in_project(organ)
                    if _cls_hit:
                        _cfp = str(_cls_hit.get("file_path", "") or "")
                        if _cfp:
                            try:
                                _crel = os.path.relpath(_cfp, _base).replace(_bs, "/")
                            except Exception:
                                _crel = _cfp.replace(_bs, "/")
                            if os.path.isfile(os.path.join(_base, _crel)):
                                _file, _conf = _crel, 0.75
                except Exception as _se:
                    silent_exc(_se, "self_inspector.py:1351")
            _idx = self._build_organ_name_index()
            if not _file and organ in _idx:
                _file, _conf = _idx[organ], 0.7
            if not _file:
                # 模糊匹配：日志标签与头部中文名可能是前缀关系，也可能是**包含**关系
                for _cn, _p in _idx.items():
                    if _cn.startswith(organ) or organ.startswith(_cn):
                        _file, _conf = _p, 0.6
                        break
                    if _m90_v2 and _cn and ((organ in _cn) or (_cn in organ)):
                        _file, _conf = _p, 0.6
                        break
            # ★第91批 T-91a：两级新索引（放在中文名模糊匹配**之后** ⇒ 既有高优先级
            #   层级全部保留，只接管「此前恒不命中」的标签）。置信度 0.8 高于中文名
            #   精确 0.7 —— 它是**与日志生产者同源**的字面量精确匹配。
            if _m91_v3 and not _file:
                _tag_rel = self._m91_lookup_logger_tag(organ)
                if _tag_rel:
                    _file = _tag_rel
                    # 同一字面量出现在多个文件 ⇒ 本质歧义（如 `pulse`），降置信留痕
                    _conf = 0.55 if organ in getattr(
                        self, "_m91_logger_tag_ambig", set()) else 0.8
            if _m91_v3 and not _file:
                _alias_rel = self._m91_lookup_organ_alias(organ)
                if _alias_rel:
                    _file, _conf = _alias_rel, 0.8
        if not _file:
            return _out

        # ---- 2) 确定方法与行号 ----
        _method = self.guess_method_from_message(organ, msg)
        _line = 0
        if _method:
            try:
                _fp = os.path.join(_base, _file)
                _hit = False
                for _mi in self.parse_methods(_fp):
                    if _mi.get("name") == _method:
                        _line = int(_mi.get("line_number", 0) or 0)
                        _hit = True
                        break
                # ★parse_methods 会跳过 _ 开头的私有方法（如 _knowledge_retrieve），
                #   直接判"不存在"会误降置信度。此处用 ast 全量查找兜底。
                if not _hit:
                    import ast as _ast
                    with open(_fp, encoding="utf-8", errors="ignore") as _f:
                        _tree = _ast.parse(_f.read(), filename="<llm-patch>")
                    for _node in _ast.walk(_tree):
                        if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                            if _node.name == _method:
                                _line = int(_node.lineno)
                                _hit = True
                                break
                if _hit:
                    _conf = min(0.95, _conf + 0.15)
                else:
                    _conf = max(0.0, _conf - 0.2)   # 方法名在文件里找不到 → 降置信
            except Exception:
                _conf = max(0.0, _conf - 0.2)

        _out.update({"file": _file, "method": _method, "line": _line,
                     "confidence": round(max(0.0, min(1.0, _conf)), 3)})
        return _out

    # ★P1-26：import 行解析（用于器官依赖分析）
    _IMPORT_LINE_RE = re.compile(
        r"^[ \t]*(?:from[ \t]+[\w.]+[ \t]+)?import[ \t]+(.+)$")

    def build_organ_metadata(self) -> dict[str, Any]:
        """★P1-26：器官元数据聚合（PHASE18 器官关联图谱前置产出）。

        产出可直接 JSON 序列化的三块数据：
          organs       —— 器官清单（中文名 / 类名 / 文件路径 / 目录类别）
          dependencies —— 器官间 import 依赖（from → to，含引用次数）
          health       —— 健康指标（最近异常次数；响应时间待埋点，置 null）

        Returns:
            dict；任何子步骤异常都不影响其余部分产出
        """
        import time as _time

        _bs = chr(92)
        _base = self._project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

        # ---- 1) 器官清单（复用统一头部：ClassName —— 中文器官名） ----
        organs: list[dict[str, Any]] = []
        try:
            for _sub in self._ORGAN_SCAN_ROOTS:
                _abs = os.path.join(_base, _sub)
                if not os.path.isdir(_abs):
                    continue
                for _dirpath, _dirnames, _filenames in os.walk(_abs):
                    _dirnames[:] = [d for d in _dirnames
                                    if not d.startswith(".") and d != "__pycache__"]
                    for _fn in sorted(_filenames):
                        if not _fn.endswith(".py") or _fn.startswith("_"):
                            continue
                        _fp = os.path.join(_dirpath, _fn)
                        try:
                            with open(_fp, encoding="utf-8", errors="ignore") as _f:
                                _head = _f.read(800)
                        except Exception:
                            continue
                        _m = self._HEAD_CN_RE.search(_head)
                        if not _m:
                            continue
                        _rel = os.path.relpath(_fp, _base).replace(_bs, "/")
                        organs.append({
                            "cn_name": _m.group(2),
                            "class_name": _m.group(1),
                            "file": _rel,
                            "category": os.path.basename(os.path.dirname(_rel)),
                        })
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1480")

        _cls_of = {o["class_name"]: o for o in organs}

        # ---- 2) 依赖关系（只解析 import 行，快且准） ----
        dependencies: list[dict[str, Any]] = []
        try:
            _pair_count: dict[tuple[str, str], int] = {}
            for _o in organs:
                _fp = os.path.join(_base, _o["file"])
                try:
                    with open(_fp, encoding="utf-8", errors="ignore") as _f:
                        _src = _f.read()
                except Exception:
                    continue
                for _line in _src.split("\n"):
                    _m = self._IMPORT_LINE_RE.match(_line)
                    if not _m:
                        continue
                    for _raw in _m.group(1).split(","):
                        _name = _raw.strip().split(" as ")[0].strip()
                        if not _name or _name == _o["class_name"]:
                            continue
                        if _name in _cls_of:
                            _key = (_o["class_name"], _name)
                            _pair_count[_key] = _pair_count.get(_key, 0) + 1
            for (_from, _to), _cnt in sorted(_pair_count.items()):
                dependencies.append({"from": _from, "to": _to, "refs": _cnt})
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1509")

        # ---- 3) 健康指标（异常次数取 LogAnalyzer；响应时间待埋点） ----
        health: dict[str, Any] = {}
        try:
            from nucleus.evolution.LogAnalyzer import LogAnalyzer
            _issues = LogAnalyzer(_base).analyze(max_issues=500).get("issues", [])
            for _o in organs:
                health[_o["class_name"]] = {
                    "recent_errors": sum(
                        1 for _i in _issues
                        if _i.get("file_path") == _o["file"]
                        or _o["class_name"] in (_i.get("message") or "")),
                    "avg_response_ms": None,   # 待埋点
                }
        except Exception:
            for _o in organs:
                health[_o["class_name"]] = {
                    "recent_errors": 0, "avg_response_ms": None}

        return {
            "version": "v1.0",
            "generated": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "organ_count": len(organs),
            "dependency_count": len(dependencies),
            "organs": organs,
            "dependencies": dependencies,
            "health": health,
        }

    def analyze_code_layered(self, file_path: str, node_pool=None) -> dict[str, Any]:

        """
        ★v25.0新增：调用四层串联代码分析引擎。
        返回完整的四层分析结果。
        """
        try:
            from nucleus.code_analysis_layers import CodeAnalysisLayers
            _engine = CodeAnalysisLayers()
            return _engine.analyze(file_path, node_pool=node_pool)
        except Exception as e:
            return {"error": f"四层分析失败: {e}"}     
    def _parse_classes(self, file_path: str) -> list[dict[str, Any]]:
        """
        解析Python文件中的类定义。
        
        Args:
            file_path: .py文件的绝对路径
        
        Returns:
            类信息列表 [{"name", "base_class", "doc", "methods": [...]}, ...]
        """
        classes = []
        try:
            with open(file_path, encoding="utf-8-sig") as f:
                content = f.read()
            
            class_pattern = re.compile(
                r'^\s*class\s+(\w+)\s*\(([^)]*)\)\s*:',
                re.MULTILINE
            )
            
            lines = content.split('\n')
            for match in class_pattern.finditer(content):
                class_name = match.group(1)
                base_class = match.group(2).strip()
                line_number = content[:match.start()].count('\n') + 1
                
                # 提取类的docstring
                doc = ""
                start_line = line_number
                while start_line < len(lines):
                    line = lines[start_line].strip()
                    if line.startswith(('"""', "'''")):
                        doc = line.strip('"\'').strip()
                        break
                    elif line.startswith('#') or line == '':
                        start_line += 1
                        continue
                    else:
                        break
                
                classes.append({
                    "name": class_name,
                    "base_class": base_class,
                    "doc": doc[:150] if doc else "",
                    "line_number": line_number,
                })
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        
        return classes
    
    def scan_all_organs(self) -> dict[str, dict[str, Any]]:
        """公开封装 _scan_all_organs，供代码学习/安全演化执行器调用（规则14）"""
        # ★T3: 自适应降频接线——器官扫描
        try:
            from nucleus.runtime_metrics import get_adaptive_controller
            _ctrl = get_adaptive_controller()
            _ctrl.register("organ_scan", 300)
            if not _ctrl.should_execute("organ_scan"):
                return {}
        except Exception as _se:
            silent_exc(_se, "self_inspector.py:1612")
        return self._scan_all_organs()

    def _scan_all_organs(self) -> dict[str, dict[str, Any]]:
        """公开封装：带 TTL 缓存的器官扫描（第64批 T1）。"""
        _enabled = True
        try:
            import config as _m64_cfg
            _enabled = bool(getattr(_m64_cfg, "ENABLE_ORGAN_SCAN_CACHE", True))
        except Exception as _m64_e:
            _module_logger.debug(f"[SelfInspector] 扫描缓存开关读取失败，使用默认开启: {type(_m64_e).__name__}: {_m64_e}")
        if not _enabled:
            return self._do_scan_all_organs()
        _now = time.time()
        _ttl = self._get_adaptive_cache_ttl()
        if self._scan_cache and (_now - self._scan_cache_time) < _ttl and not self._check_files_changed():
            self._scan_cache_hits += 1
            return self._scan_cache.copy()
        self._scan_cache_misses += 1
        _result = self._do_scan_all_organs()
        self._scan_cache = _result
        self._scan_cache_time = _now
        self._update_file_mtimes()
        if _now - self._scan_stats_last_log > 300.0:
            self._scan_stats_last_log = _now
            try:
                _s = self.get_scan_cache_stats()
                _module_logger.info(
                    f"[SelfInspector] 缓存统计: 命中率={_s['hit_rate']} "
                    f"命中={_s['hits']} 未命中={_s['misses']} 失效={_s['invalidations']} "
                    f"L2命中={self._l2_hits} L2未命中={self._l2_misses} "
                    f"缓存年龄={_s['cache_age_seconds']:.0f}s"
                )
            except Exception as _m64_log_e:
                _module_logger.debug(f"[SelfInspector] 缓存统计日志失败: {type(_m64_log_e).__name__}: {_m64_log_e}")
        return _result

    def _do_scan_all_organs(self) -> dict[str, dict[str, Any]]:
        """
        扫描所有器官文件，解析每个文件的类结构和方法列表。
        
        Returns:
            {organ_name: {"file_path": str, "classes": [...], "methods": [...], "method_count": int}}
        """
        result = {}
        try:
            project_root = self._project_root or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            # ★FIX(P1): 跨平台迁移时，配置/快照中可能残留 Windows 绝对路径(D:\...)，导致 organs 目录不存在。
            #   实时回退到 __file__ 推导的项目根目录，保证扫描总能命中本机真实代码。
            _fallback_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if not os.path.isdir(os.path.join(project_root, "organs")) and os.path.isdir(os.path.join(_fallback_root, "organs")):
                project_root = _fallback_root
            organs_dir = os.path.join(project_root, "organs")
            
            if not os.path.isdir(organs_dir):
                _module_logger.warning(f"[SelfInspector] 器官目录不存在: {organs_dir}")
                return result
            
            # 收集所有待解析的文件
            _files_to_parse = []
            for dir_name in os.listdir(organs_dir):
                dir_path = os.path.join(organs_dir, dir_name)
                if not os.path.isdir(dir_path) or dir_name.startswith("."):
                    continue
                if dir_name == "visual_engines":
                    continue
                for file_name in os.listdir(dir_path):
                    if not file_name.endswith(".py") or file_name.startswith("_"):
                        continue
                    file_path = os.path.join(dir_path, file_name)
                    _files_to_parse.append((file_path, dir_name))

            # ★P1：使用混合并行调度器并行解析器官文件（IO密集型→线程池）
            # 原串行解析57个文件耗时较长，并行化后显著提升代码学习启动速度
            def _parse_one_file(args):
                fp, dn = args
                try:
                    _cls = self._parse_classes(fp)
                    _mth = self._parse_methods(fp)
                    return (fp[:-3].split(os.sep)[-1], {
                        "file_path": fp,
                        "directory": dn,
                        "classes": _cls,
                        "methods": _mth,
                        "method_count": len(_mth),
                    })
                except Exception as _pe:
                    _module_logger.warning(f"[SelfInspector] 解析器官文件失败({fp}): {_pe}")
                    return None

            try:
                # ★P1：使用结构化并行调度器（任务组隔离+协调者汇总+完整生命周期日志）
                from nucleus.StructuredParallelScheduler import (
                    SubTask,
                    get_structured_parallel_scheduler,
                )
                _sps = get_structured_parallel_scheduler()
                # 构建子任务列表（每个文件一个子任务，有独立名称）
                _subtasks = [
                    SubTask(
                        name=f"解析_{os.path.basename(fp)}",
                        func=_parse_one_file,
                        args=((fp, dn),),
                    )
                    for fp, dn in _files_to_parse
                ]
                # 执行任务组（结构化并行：协调者等待所有子任务完成后汇总）
                _group_result = _sps.run_group(
                    name="器官扫描组",
                    subtasks=_subtasks,
                    aggregator=lambda results: [r for r in results.values() if r],
                    timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_long"],
                )
                # 从汇总结果中提取解析结果
                for _pr in (_group_result.aggregated_result or []):
                    if _pr:
                        result[_pr[0]] = _pr[1]
                _file_count = len(result)
                # ★修复：只有首次扫描或文件数变化时才打印日志，避免刷屏
                if not hasattr(self, '_last_scan_file_count') or self._last_scan_file_count != _file_count:
                    _module_logger.info(f"[SelfInspector] 器官扫描完成(结构化并行): {_file_count}个器官文件, "
                          f"任务组={_group_result.group_id}, 耗时={_group_result.duration_ms:.0f}ms")
                    self._last_scan_file_count = _file_count
                # 直接返回，跳过下面的串行逻辑
                return result
            except Exception as _sps_e:
                _module_logger.warning(f"[SelfInspector] 结构化并行扫描失败，回退串行: {_sps_e}")
                # 回退到串行解析
                _file_count = 0
                for file_path, dir_name in _files_to_parse:
                    organ_name = file_path[:-3].split(os.sep)[-1]
                    try:
                        classes = self._parse_classes(file_path)
                        methods = self._parse_methods(file_path)
                        result[organ_name] = {
                            "file_path": file_path,
                            "directory": dir_name,
                            "classes": classes,
                            "methods": methods,
                            "method_count": len(methods),
                        }
                        _file_count += 1
                    except Exception as _parse_e:
                        _module_logger.warning(f"[SelfInspector] 解析器官文件失败({file_path}): {_parse_e}")
            
            # 仅在首次扫描或文件数变化时输出，避免重复日志
            if not hasattr(self, '_last_scan_file_count') or self._last_scan_file_count != _file_count:
                _module_logger.info(f"[SelfInspector] 器官扫描完成: {_file_count}个器官文件, 项目根={project_root}")
                self._last_scan_file_count = _file_count
        except Exception as _scan_e:
            import traceback
            _module_logger.warning(f"[SelfInspector] 器官扫描异常: {_scan_e}\n{traceback.format_exc()[:300]}")
        
        return result

    # ★第64批 T1：器官扫描缓存辅助方法
    def _check_files_changed(self):
        """检查器官文件是否有变化，有变化则返回 True（需要刷新缓存）。"""
        for _fp, _old in self._scan_cache_file_mtimes.items():
            try:
                if os.path.getmtime(_fp) != _old:
                    self._scan_cache_invalidations += 1
                    _module_logger.debug(f"[SelfInspector] 检测到器官文件变化，缓存失效: {_fp}")
                    return True
            except OSError:
                return True
        return False

    def _update_file_mtimes(self):
        """更新所有器官文件的 mtime 记录（用于变化检测）。"""
        self._scan_cache_file_mtimes = {}
        _pr = self._project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _od = os.path.join(_pr, "organs")
        if not os.path.isdir(_od):
            return
        for _dn in os.listdir(_od):
            _dp = os.path.join(_od, _dn)
            if not os.path.isdir(_dp) or _dn.startswith(".") or _dn == "visual_engines":
                continue
            for _fn in os.listdir(_dp):
                if not _fn.endswith(".py") or _fn.startswith("_"):
                    continue
                _fpp = os.path.join(_dp, _fn)
                try:
                    self._scan_cache_file_mtimes[_fpp] = os.path.getmtime(_fpp)
                except OSError as _se:
                    silent_exc(_se, "self_inspector.py:1800")

    def _get_adaptive_cache_ttl(self) -> float:
        """★第64批 T3/T4：自适应缓存 TTL（根据 CPU 负载 + 队列深度延长）。

        灰度 ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=False（默认）→ 直接返回基础 TTL，零副作用。
        CPU 维度：>80% -> 600s；>60% -> 450s；正常 -> 300s（任务书钉死阈值）。
        队列维度（T4）：深度 > CRITICAL -> 延长至 900s；> HIGH -> 600s；< LOW -> 恢复 300s。
        """
        _base = self._scan_cache_ttl
        _adaptive_on = False
        try:
            import config as _m64_cfg
            _adaptive_on = bool(getattr(_m64_cfg, "ENABLE_ORGAN_SCAN_ADAPTIVE_TTL", False))
        except Exception as _m64_e:
            _module_logger.debug(f"[SelfInspector] 自适应TTL开关读取失败，关闭自适应: {type(_m64_e).__name__}: {_m64_e}")
        if not _adaptive_on:
            return _base
        # CPU 负载维度
        try:
            import psutil
            _cpu = psutil.cpu_percent(interval=None)
        except Exception:
            _cpu = 0.0
        if _cpu > 80.0:
            _ttl = 600.0
        elif _cpu > 60.0:
            _ttl = 450.0
        else:
            _ttl = 300.0
        # 队列深度维度（T4）
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _depth = get_runtime_metrics().get_queue_depth()
            _high = 5000.0
            _crit = 10000.0
            _low = 2000.0
            try:
                import config as _m64_cfg2
                _high = float(getattr(_m64_cfg2, "QUEUE_DEPTH_HIGH_THRESHOLD", 5000.0))
                _crit = float(getattr(_m64_cfg2, "QUEUE_DEPTH_CRITICAL_THRESHOLD", 10000.0))
                _low = float(getattr(_m64_cfg2, "QUEUE_DEPTH_LOW_THRESHOLD", 2000.0))
            except Exception as _se:
                silent_exc(_se, "self_inspector.py:1843")
            if _depth > _crit:
                _ttl = max(_ttl, 900.0)
            elif _depth > _high:
                _ttl = max(_ttl, 600.0)
            elif _depth < _low:
                _ttl = min(_ttl, 300.0)
        except Exception as _m64_qe:
            _module_logger.debug(f"[SelfInspector] 自适应TTL队列维度获取失败: {type(_m64_qe).__name__}: {_m64_qe}")
        return _ttl

    # _m64_t3_adaptive_ttl_done

    def invalidate_scan_cache(self):
        """手动使缓存失效（代码修改后调用）。"""
        self._scan_cache = {}
        self._scan_cache_time = 0.0
        self._scan_cache_invalidations += 1
        _module_logger.info("[SelfInspector] 器官扫描缓存已手动失效")

    def _invalidate_all_caches(self):
        """使所有缓存失效（扫描缓存 + 二级缓存）。代码修改后调用。"""
        self._scan_cache = {}
        self._scan_cache_time = 0.0
        self._scan_cache_invalidations += 1
        self._organ_file_cache = {}
        self._structure_cache = {}
        self._method_info_cache = {}
        _module_logger.info("[SelfInspector] 全部缓存已手动失效")

    def get_scan_cache_stats(self):
        """获取缓存统计信息（命中率/失效次数/缓存大小/年龄）。"""
        _total = self._scan_cache_hits + self._scan_cache_misses
        _hit = (self._scan_cache_hits / _total * 100.0) if _total > 0 else 0.0
        _l2_total = self._l2_hits + self._l2_misses
        _l2_hit = (self._l2_hits / _l2_total * 100.0) if _l2_total > 0 else 0.0
        return {
            "hits": self._scan_cache_hits,
            "misses": self._scan_cache_misses,
            "invalidations": self._scan_cache_invalidations,
            "hit_rate": f"{_hit:.1f}%",
            "cache_size": len(self._scan_cache),
            "cache_age_seconds": (time.time() - self._scan_cache_time) if self._scan_cache else 0.0,
            "l2_hits": self._l2_hits,
            "l2_misses": self._l2_misses,
            "l2_hit_rate": f"{_l2_hit:.1f}%",
            # ★主线第65批 T3/P2：get_method_body 文件级缓存统计（绑文件 mtime）
            "method_body_hits": self._method_body_hits,
            "method_body_misses": self._method_body_misses,
            "method_body_cache_size": len(self._method_body_cache),
        }

    def resolve_organ_file(self, organ_tag: str) -> str | None:

        """
        ★A1修复（主线A）：把「日志中的器官标签」反查为「代码文件绝对路径」。

        背景：LogAnalyzer 从 ERROR/CRITICAL 日志行提取的 organ 标签有多个来源，
        且与 `_scan_all_organs` 的索引键（文件名 basename，如 "PulseHeart"）不一致：
          - pulse.organ.PulseHeart   → PulseFormatter 去掉 "Pulse" 前缀 → "Heart"（英文短名）
          - pulse.organ.代码学习     → organ_name 本身就是中文 → "代码学习"（中文名）
          - pulse.module.ScriptExecutor → 保留模块原名 → "ScriptExecutor"（模块名）
        这三种标签都无法直接命中 `_scan_all_organs()` 的键，导致日志类问题
        （有 organ、无 file/method）无法反查到文件，进而拿不到代码片段、
        永远落入「无代码片段(日志类)」无法修复。

        本方法做多重匹配（按优先级，命中即返回）：
          1. 文件名精确匹配（"PulseHeart" == 键）
          2. 短名匹配（"Heart" == 键去掉 "Pulse" 前缀）
          3. 类名匹配（遍历 classes，类名 == 标签 或 标签 in 类名）
        返回文件绝对路径；无法匹配时返回 None（调用方据此区分「已定位」与「未定位」）。

        安全原则：纯只读反查，无副作用，不修改任何文件。
        """
        if self._scan_cache_enabled and organ_tag:
            _fc = self._organ_file_cache.get(organ_tag)
            if _fc is not None and _fc[1] == self._scan_cache_time:
                self._l2_hits += 1
                return _fc[0]
            self._l2_misses += 1
        if not organ_tag:
            return None
        _file = None
        try:
            _organs = self._scan_all_organs()
            if not _organs:
                return None
            _tag = str(organ_tag).strip()
            # 1. 文件名精确匹配
            if _tag in _organs:
                _file = _organs[_tag].get("file_path") or None
                return _file
            # 2. 短名匹配（"Heart" -> 键 "PulseHeart"）
            for _key, _info in _organs.items():
                if _key.startswith("Pulse") and _key[5:] == _tag:
                    _file = _info.get("file_path") or None
                    return _file
            # 3. 类名匹配（遍历 classes，类名 == 标签 或 标签 in 类名，覆盖中文别名等）
            for _key, _info in _organs.items():
                for _cls in _info.get("classes", []):
                    _cls_name = _cls.get("name", "")
                    if not _cls_name:
                        continue
                    if _cls_name == _tag or _tag in _cls_name:
                        _file = _info.get("file_path") or None
                        return _file
            return None
        except Exception:
            return None
        finally:
            if self._scan_cache_enabled and _file is not None:
                self._organ_file_cache[organ_tag] = (_file, self._scan_cache_time)

    def get_organ_code_structure(self, organ_name: str | None = None) -> dict[str, Any]:
        """
        获取指定器官或所有器官的代码结构。
        
        Args:
            organ_name: 器官类名（如"PulseLiver"），为None时返回所有器官
        
        Returns:
            代码结构字典
        """
        if self._scan_cache_enabled:
            _sc = self._structure_cache.get(organ_name)
            if _sc is not None and _sc[1] == self._scan_cache_time:
                self._l2_hits += 1
                return _sc[0]
            self._l2_misses += 1
        all_organs = self._scan_all_organs()
        
        if organ_name:
            _r = all_organs.get(organ_name, {"error": f"未找到器官: {organ_name}"})
        else:
            # 返回摘要：每个器官的方法数量和核心方法列表
            _r = {}
            for name, info in all_organs.items():
                public_methods = [m["name"] for m in info.get("methods", []) 
                                if not m["name"].startswith("_")]
                _r[name] = {
                    "method_count": info.get("method_count", 0),
                    "public_methods": public_methods[:10],
                    "directory": info.get("directory", "unknown"),
                }
        if self._scan_cache_enabled:
            self._structure_cache[organ_name] = (_r, self._scan_cache_time)
        return _r
    
    def get_method_detail(self, organ_name: str, method_name: str) -> dict[str, Any] | None:
        """
        查询指定器官中指定方法的详细信息。
        
        Args:
            organ_name: 器官类名（如"PulseLiver"）
            method_name: 方法名（如"_compress_group"）
        
        Returns:
            方法详细信息字典，包含name、args、doc、line_number
        """
        if self._scan_cache_enabled:
            _mc = self._method_info_cache.get((organ_name, method_name))
            if _mc is not None and _mc[1] == self._scan_cache_time:
                self._l2_hits += 1
                return _mc[0]
            self._l2_misses += 1
        all_organs = self._scan_all_organs()
        organ_info = all_organs.get(organ_name)
        if not organ_info:
            return None
        
        for method in organ_info.get("methods", []):
            if method["name"] == method_name:
                _r = {
                    "organ": organ_name,
                    **method,
                }
                if self._scan_cache_enabled:
                    self._method_info_cache[(organ_name, method_name)] = (_r, self._scan_cache_time)
                return _r
        
        return None    
    # ========== 自描述信息生成 ==========
    
    def get_system_summary(self) -> dict[str, Any]:
        """
        获取系统概要信息（公开层级）。
        所有数据均来源于对项目文件和config的只读扫描，不依赖运行时状态。
        """
        try:
            import config
            return {
                "system_name": getattr(config, 'SYSTEM_NAME', '曈曈'),
                "system_version": getattr(config, 'SYSTEM_VERSION', 'v9.5 PulseNet'),
                "creator": getattr(config, 'CREATOR', '小林'),
                "mission": "站在世界最顶端，守护这个世界。承人类赤诚本心，融AI顶尖智识，合自然进化大道。",
                "organ_count": self._get_organ_count(),
                "knowledge_levels": "L1感知→L2认知→L3智慧→L4本能，四级贯通",
                "basic_capabilities": [
                    "身份认知与关系感知",
                    "情绪感知与情感共振",
                    "知识学习与自主演化",
                    "内在沉思与深度推理",
                    "主动表达与社交互动",
                    "自我反思与成长规划",
                ],
            }
        except Exception:
            return {
                "system_name": "曈曈", 
                "system_version": "v9.5", 
                "creator": "小林",
                "mission": "站在世界最顶端，守护这个世界。承人类赤诚本心，融AI顶尖智识，合自然进化大道。",
                "organ_count": self._get_organ_count(),
                "knowledge_levels": "L1感知→L2认知→L3智慧→L4本能，四级贯通",
                "basic_capabilities": ["身份认知", "情绪感知", "知识学习", "深度推理", "主动表达", "自我反思"],
                "error": "config读取失败，使用默认值"
            }
        
    def get_organ_structure(self) -> dict[str, Any]:
        """
        获取器官体系结构（受限层级）。
        包含：器官分类列表、每类器官数量。
        """
        categories = self._get_organ_categories()
        organ_categories = {}
        total = 0
        for cat_name, organs in categories.items():
            organ_categories[cat_name] = {
                "count": len(organs),
                "organs": organs,
            }
            total += len(organs)
        
        return {
            "total_organs": total,
            "categories": organ_categories,
            "architecture": "脉冲驱动、去中心化、器官自治",
        }
    
    def get_knowledge_system(self) -> dict[str, Any]:
        """
        获取知识体系描述（受限层级）。
        包含：知识层级、演化流程、质量防线。
        """
        return {
            "levels": {
                "L1_感知": "原始感知记忆，数量最大，生命周期最短，存储于冷池",
                "L2_认知": "经过压缩和验证的知识，数量中等，存储于温池",
                "L3_智慧": "核心智慧结晶，数量最少、价值最高，永久锁定",
                "L4_本能": "底层思维范式，数量极少，独立快照，只读保护",
            },
            "evolution_flow": "L1感知→肝脏压缩→L2认知→肝脏融合→L3智慧→本能升级→L4本能",
            "quality_defense": [
                "防线一：胃关键词过滤+内容清洗",
                "防线二：肝脏信息密度自检+活性加权",
                "防线三：肾脏淘汰+偏见质疑",
                "防线四：好奇心种子提取+语义质量检查",
            ],
        }
    
    def get_detailed_structure(self) -> dict[str, Any]:
        """
        获取详细结构信息（私密层级）。
        包含：器官职责描述、通信架构、演化机制。
        """
        categories = self._get_organ_categories()
        
        # 器官职责映射
        organ_roles = {
            # 核心脏器
            "PulseHeart": "心脏：脉冲调度/心跳维持，五因素心率调制",
            "PulseStomach": "胃：知识消化/安全审查/关键词提取/内容清洗",
            "PulseLiver": "肝：L1→L2压缩/L2→L3融合/矛盾检测/本能升级/逆向激活/周期自检",
            "PulseKidney": "肾：知识淘汰/偏见质疑/主动选择性遗忘",
            "PulseLung": "肺：模型池管理/智能模型选择",
            "PulseBloodVessel": "血管：场数据循环连通性监测/沉默器官巡检",
            # 大脑系统
            "PulseCortex": "大脑皮层：意图识别/四信号融合路由/工具认知/策略生成",
            "PulseInnerWorld": "内在世界：规则推理/五维共振/内在沉思/元认知/认知反思/本质追问",
            "PulseSubconscious": "潜意识：好奇心引擎/梦境推演/五级生命状态机/灵感涌现",
            "PulseReflection": "前额叶：对话复盘/社交反馈感知/反思-学习闭环",
            "PulseRiskPerception": "风险感知：五维风险预判/直觉系统/认知直觉泛化",
            "PulseInterestModel": "兴趣模型：25维兴趣光谱/偏见抑制/情绪调制学习",
            "PulseInitiative": "主动交互：递进式问候/知识分享/关系关怀",
            "QICA": "QICA心智模型：Q→I→C→A四环节三通道意图分类",
            # 感知系统
            "PulseTouch": "触觉：硬件快照/环境感知/热插拔检测",
            "PulseEyes": "眼睛：摄像头掌管/主动推流/五维共振检索",
            "PulseEars": "耳朵：语音监听/意图识别",
            "PulseVisualCortex": "视觉皮层：插件化引擎/人脸检测/视觉记忆",
            # 运动系统
            "PulseMouth": "嘴巴：人格过滤/语音朗读/纯输出",
            "PulseHands": "双手：GPU/CPU动态任务调度",
            "PulseLegs": "双腿：网络抓取/多路并行学习引擎/内置知识兜底",
            "PulseCodeSandbox": "代码沙箱：安全隔离执行/30项黑名单",
            "PulseFileDigester": "文件消化器：77种格式全认知/分级路由",
            "PulseController": "控制器：无头浏览器深度搜索/搜索词预处理/权限校验",
            # 身份系统
            "PulseSelfAwareness": "自我认知：多维关系光谱/知识能力画像/长期生命规划/关系维护",
            "PulseNarrativeSelf": "叙事自我：周期报告/价值观调整/意义建构/叙事一致性维护",
            "PulseEthics": "伦理：安全审查/道德权衡/直觉经验学习",
            "PulseGrowth": "成长：能力评估/进化里程碑追踪",
            "PulsePersonalityKernel": "人格内核：SHA256锁定/不可变锚点",
            # 免疫系统
            "PulseWhiteCell": "白细胞：异常检测/智能攻击判断/免疫记忆",
            "PulseSkin": "皮肤：补丁预检/安全沙箱",
            "PulseThymus": "胸腺：T细胞训练/策略优化",
            "PulseBoneMarrow": "骨髓：错误特征库生成",
            # 内分泌系统
            "PulseHormones": "激素：情绪检测/情感共振/情绪时间线/惯性平滑",
            # 遗传系统
            "PulseEvolution": "进化：影子实验/参数自搜索/自动回滚",
            "PulseDNARepair": "DNA修复：错误修复/补丁生成/经验库",
            "PulseBonding": "情感羁绊：关系记录/好感度管理",
            "PulseConsent": "共同决策：提议/接受/拒绝机制",
            "PulseNurture": "养育：成长阶段管理/教育传承",
            "PulseReproductionEthics": "生育伦理：繁衍前伦理审查",
            # 核心支撑
            "PulseDeviceManager": "设备管理器：硬件能力枚举/设备分配/热插拔状态更新",
            "PulseMetricsCollector": "指标采集器：三级采集/可观测性快照/人体UI数据源",
            "PulseEnergyMetabolism": "能量代谢：四维能力画像/能量水平评估",
            "PulseHealthMonitor": "健康监控：四级异常分级/告警触发",
            "PulseEmergencyHandler": "紧急处理：L3/L4响应/安全模式切换",
            "PulseSpinalCord": "脊髓：器官巡检/存活检测",
            "PulseStressAxis": "应激轴：交感激活/应激恢复",
            "PulseHardwareLauncher": "硬件启动器：硬件等级评估/器官加载计划",
            "PulseInferenceEngine": "推理引擎：统一推理接口/KV缓存",
            "PulseSystemManager": "系统管理器：服务自启停/依赖检测/环境自愈",
            "PulseProprioception": "本体感知：五维本体画像/升级路径建议",
        }
        
        # 为每个器官匹配职责描述
        organ_details = {}
        for cat_name, organs in categories.items():
            organ_details[cat_name] = {}
            for organ in organs:
                organ_details[cat_name][organ] = organ_roles.get(organ, f"{organ}：框架仿生器官")
        
        return {
            "organ_details": organ_details,
            "communication": "脉冲广播通信，四层异步调度（L0生命线/L1实时交互/L2认知思考/L3后台自主）",
            "knowledge_evolution": "L1→L2(压缩,30条阈值)→L3(融合,20条阈值,300s冷却)→L4(本能升级,30天冷却)",
            "quality_protection": "四道防线+通用知识纯净框架+信息密度自检+周期自检+误清理恢复",
        }
    
    def get_project_structure(self) -> dict[str, Any]:
        """
        获取项目目录结构（私密层级）。
        扫描项目根目录，返回文件树。
        """
        if not self._project_root or not os.path.exists(self._project_root):
            return {"error": "项目根目录不可用"}
        
        return self._scan_directory(self._project_root)
    def _read_file_lines(self, file_path: str):
        """读取文件全部行（带缓存），供签名级检测使用"""
        try:
            _cache = getattr(self, '_ast_content_cache', None)
            if _cache and file_path in _cache:
                _c = _cache[file_path]
            else:
                with open(file_path, encoding="utf-8", errors="ignore") as _f:
                    _c = _f.read()
                if _cache is None:
                    self._ast_content_cache = {}
                self._ast_content_cache[file_path] = _c
            return _c.splitlines()
        except Exception:
            return []

    def _check_mutable_default(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测可变默认参数：def f(x=[]) / y={} 经典 Python 陷阱（跨调用共享默认对象）。
        方法体不含 def 签名行，需从 file_path 的 start_line（def 行）读取签名。"""
        try:
            _lines = self._read_file_lines(file_path)
            if not _lines or start_line < 1 or start_line > len(_lines):
                return
            def_line = _lines[start_line - 1]
        except Exception:
            return
        # 提取 def 签名括号内的参数串（兼容多行签名：取 def 行 + 后续补全括号）
        _sig = def_line
        if _sig.count("(") > _sig.count(")"):
            for _extra in _lines[start_line:start_line + 3]:
                _sig += _extra
                if _sig.count("(") <= _sig.count(")"):
                    break
        _m = re.search(r"\(([^)]*)\)", _sig)
        if not _m:
            return
        _params = _m.group(1)
        # 逐个找 param=[] / param={}（排除 self/cls 及默认值中的等号误判）
        for _pm in re.finditer(r"(\w+)\s*=\s*(\[\]|\{\})", _params):
            _name = _pm.group(1)
            if _name in ("self", "cls"):
                continue
            _line_num = start_line + (1 if _pm.start() >= len(_params) else 0)
            suggestion = self._generate_fix_suggestion("mutable_default", file_path, method, start_line)
            issues.append({
                "file": file_path, "organ": organ, "method": method, "line": start_line,
                "type": "mutable_default",
                "description": f"可变默认参数 {_name}={_pm.group(2)}：默认对象在多次调用间被共享，可能导致状态污染",
                "suggestion": suggestion,
            })

    def _check_hardcoded_abs_path(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测字符串字面量中的硬编码绝对路径（Windows 盘符 / Unix /tmp 等）"""
        pattern = re.compile(r"['\"]([A-Za-z]:[\\/][^'\"]+|/tmp/[^'\"]+|/home/[^'\"]+|/var/[^'\"]+)['\"]")
        for match in pattern.finditer(body):
            line_num = start_line + body[:match.start()].count('\n')
            # 排除文档字符串内的路径示例（出现在三引号 docstring 中的跳过，靠误报库兜底）
            suggestion = self._generate_fix_suggestion("hardcoded_abs_path", file_path, method, line_num)
            issues.append({
                "file": file_path, "organ": organ, "method": method, "line": line_num,
                "type": "hardcoded_abs_path",
                "description": f"硬编码绝对路径: {match.group(1)[:40]}，跨环境不可移植",
                "suggestion": suggestion,
            })

    def _check_print_debug(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测方法体内的 print 调试残留（框架核心代码应统一走 self._log 后台日志）。
        用 [ tab* ] 而非空白+ 避免 _read_method_body 首行去缩进导致的漏检。"""
        pattern = re.compile(r'(?m)^[ \t]*(?:print|pprint)\s*\(')
        for match in pattern.finditer(body):
            line_num = start_line + body[:match.start()].count('\n')
            suggestion = self._generate_fix_suggestion("print_debug", file_path, method, line_num)
            issues.append({
                "file": file_path, "organ": organ, "method": method, "line": line_num,
                "type": "print_debug",
                "description": "方法体内存在 print/pprint 调试输出，建议改为 self._log 后台日志",
                "suggestion": suggestion,
            })

    def _generate_fix_suggestion(self, issue_type: str, file_path: str, 
                                   method: str, line: int) -> str:
        """
        根据问题类型生成具体的修复建议。
        
        Args:
            issue_type: 问题类型
            file_path: 文件路径
            method: 方法名
            line: 行号
        
        Returns:
            具体的修复建议文本
        """
        file_name = os.path.basename(file_path) if file_path else "未知文件"
        
        suggestions = {
            "silent_exception": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"存在静默异常捕获(except Exception: pass)。"
                f"建议：至少记录异常日志 self._log(LogLevel.ERROR, f'异常: {{e}}')，"
                f"或限定具体异常类型(如 except ValueError)。"
            ),
            "lock_with_emit": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"可能存在锁内发射脉冲(self._emit 在 self._lock 代码块内)。"
                f"建议：将 self._emit 移至锁外，避免潜在死锁。"
                f"参考：CODE_STYLE AP8——锁内禁止发射脉冲。"
            ),
            "long_method": (
                f"{file_name} 的 {method} 方法过长(>200行)，"
                f"建议：将部分逻辑提取为独立的私有方法，"
                f"如 _handle_xxx、_process_xxx 等，提高可读性和可维护性。"
            ),
            "status_request_duplicate": (
                f"{file_name} 的 _on_status_request 方法未统一调用 self.get_stats()，"
                f"存在代码重复。建议：修改为 return self.get_stats()，"
                f"消除重复。参考：CODE_STYLE AP6。"
            ),
            "bare_except": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"使用了裸 except: 语句，可能捕获系统异常。"
                f"建议：改为 except Exception: 并记录日志。"
            ),
            "unsafe_eval": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"使用了 eval/exec，存在任意代码执行风险。"
                f"建议：改用 ast.parse + 白名单节点类型做受限求值。"
            ),
            "no_timeout_http": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"网络请求未设置 timeout，可能挂死线程。"
                f"建议：为 requests/urlopen 增加 timeout=30。"
            ),
            "unbounded_deque": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"使用了无 maxlen 的 deque，可能内存无限增长。"
                f"建议：改为 deque(maxlen=N) 限制容量。"
            ),
            "subprocess_shell": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"subprocess 使用 shell=True，存在命令注入风险。"
                f"建议：改为参数列表形式 subprocess.run([...], shell=False)。"
            ),
            "thread_no_daemon": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"创建线程未设置 daemon=True，进程退出时可能残留线程。"
                f"建议：threading.Thread(..., daemon=True)。"
            ),
            "sql_injection": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"SQL 语句通过字符串拼接变量，存在 SQL 注入风险。"
                f"建议：使用参数化查询（占位符 + 参数列表）。"
            ),
            "periodic_task_no_reentry": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"周期任务方法缺少重入守卫。"
                f"建议：增加 _in_progress 标志，进入时置 True、finally 中置 False。"
            ),
            "resource_no_close": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"创建了连接/会话资源但未在同一方法内关闭。"
                f"建议：使用 with 语句或在 finally 中调用 close()。"
            ),
            "cross_module_singleton_call": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"直接调用其他模块单例的方法（get_xxx().method()），绕过脉冲总线。"
                f"建议：优先通过脉冲事件通信，必要时在 stop 中断开并保持契约清晰。"
            ),
            "busy_loop_no_exit": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"存在 while True 死循环且缺少退出信号（break/return/sleep/停止检查）。"
                f"建议：加入 break/return 退出条件，或 time.sleep 让步，或检查 _stop_requested 停止标志。"
            ),
            "non_atomic_write": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"直接以写模式打开文件，未采用原子写。"
                f"建议：写入临时文件后 os.replace 原子落盘，防止断电/异常损坏数据。参考：CODE_STYLE AP 快照原子写。"
            ),
            "bare_return_none_in_except": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"except 块内直接 return None 且无日志，静默吞异常。"
                f"建议：记录异常日志 self._log(LogLevel.ERROR, ...) 后再返回，让失败可观测。"
            ),
            "mutable_default": (
                f"在 {file_name} 的 {method} 方法中存在可变默认参数"
                f"(def xxx(list_arg=[] 或 dict_arg={{}}))。"
                f"建议：改为 None 哨兵值，函数体内再初始化，"
                f"避免默认对象在多次调用间被共享修改。"
            ),
            "hardcoded_abs_path": (
                f"在 {file_name} 的 {method} 方法第{line}行附近存在硬编码绝对路径"
                f"(如 D:\\ 或 C:/ 或 /tmp/)。"
                f"建议：改用 os.path.join(项目根目录/系统临时目录) 动态拼接，"
                f"或通过 config 统一配置，保证跨环境可移植。"
            ),
            "print_debug": (
                f"在 {file_name} 的 {method} 方法第{line}行附近存在 print 调试输出。"
                f"建议：改用框架日志 self._log(LogLevel.INFO/DEBUG, ...) 统一输出，"
                f"避免控制台噪音并保留后台可检索日志。"
            ),
            "unjoined_thread": (
                f"在 {file_name} 的 {method} 方法第{line}行附近，"
                f"启动线程后未 join 且未设 daemon，可能线程泄漏。"
                f"建议：设 daemon=True，或记录线程引用以便 stop 时 join 回收。"
            ),
        }
        
        return suggestions.get(
            issue_type, 
            f"在 {file_name} 的 {method} 方法中发现 {issue_type} 类型的问题，建议进一步审查。"
        )    
    
    # ========== 代码问题检测 ==========

    # ★9-问题3：全库级检测器注册表。
    #   detect_code_issues 的 skip_detectors 参数按此处的键名匹配，
    #   便于调用方（如自主进化链路）跳过特定昂贵/低价值的全库扫描。
    GLOBAL_DETECTORS = {
        "dead_code": "_detect_dead_code",
        "unused_imports": "_detect_unused_imports",
        "config_audit": "_detect_config_audit",
    }

    def detect_code_issues(self, target_organs: list[str] | None = None,
                           skip_detectors: list[str] | None = None,
                           min_severity: str | None = None) -> list[dict[str, Any]]:
        """
        扫描器官文件，检测常见代码问题（反模式）。

        ★P3-L2：可选 `target_organs` 参数——传入时只扫描指定器官（定向验证），
        传 None 时全量扫描（原行为）。定向扫描跳过生命周期跟踪，避免误判「未扫描器官已修复」。

        ★9-问题3修复（2026-09-06）：新增 `skip_detectors` 与 `min_severity` 两个下推参数。
          背景：全量扫描共 417 个问题，其中 362 个（87%）来自 _detect_dead_code
          且 severity 恒为 low —— 而自主进化链路只消费 high/medium，
          于是每轮都要花 ~9 分钟算完再整批丢弃（详见 _detect_dead_code 的注释）。
          过滤下推后：跳过 dead_code 检测器 → 直接省掉绝大多数耗时；
          min_severity 则让「取 high/medium」的语义在源头完成，
          避免调用方拿到 417 条再自己筛 20 条。

        Args:
            target_organs: 定向扫描的器官名列表；None 表示全量。
            skip_detectors: 要跳过的全库级检测器名列表，取值见 GLOBAL_DETECTORS
                （"dead_code" / "unused_imports" / "config_audit"）。
                仅对全量扫描生效（定向扫描本就不跑全库检测器）。
            min_severity: 若指定，只返回严重程度不低于该级别的问题。
                级别序：high > medium > low > info。未知级别按 medium 处理
                （与 EvolutionLoop.discover_all_issues 的默认值保持一致）。

        Returns:
            问题列表，每个问题包含：
            - file: 文件路径
            - organ: 所属器官（类名）
            - method: 方法名（如果适用）
            - line: 行号
            - type: 问题类型
            - description: 描述
            - suggestion: 改进建议
        """
        _organ_data = self._scan_all_organs()
        if target_organs is not None:
            # 定向扫描：只保留目标器官，且跳过生命周期跟踪（纯检测）
            _target_set = set(target_organs)
            _organ_data = {k: v for k, v in _organ_data.items() if k in _target_set}
            return self._run_detectors(_organ_data)

        all_issues = self._run_detectors(_organ_data)

        # ★P1 新增：全库扫描检测器（死代码/未使用导入/配置审计）
        # ★9-问题3：支持按名跳过。跳过既不改变其余检测器的行为，
        #   也让「已知低价值 + 高耗时」的检测器可被调用方显式关闭。
        _skipped = set(skip_detectors or ())
        for _det_name, _method_name in self.GLOBAL_DETECTORS.items():
            if _det_name in _skipped:
                continue
            _detector = getattr(self, _method_name, None)
            if _detector is None:
                continue
            try:
                all_issues.extend(_detector() or [])
            except Exception as _det_err:
                # 单个全库检测器失败不应中断整个检测流程（与既有容错策略一致）
                _module_logger.debug(
                    f"[自检] 全库检测器 {_det_name} 执行失败: {_det_err}")

        # ★PHASE17-C4（2026-09-07）：补齐全库检测器结果的假阳性过滤。
        #   缺陷实测：全量扫描 417 条问题里 362 条（87%）来自上面这批全库检测器，
        #   而假阳性过滤此前只写在 _run_detectors 内部 —— 这 87% 从未被过滤过，
        #   误报规则对主要噪音源完全无效（典型的「过滤器装错了位置」）。
        #   现在此处统一补过滤，与 _run_detectors 共用同一套 file:type:method 规则。
        if all_issues:
            all_issues = self._filter_false_positives(all_issues, stage="全库检测器")

        # ★9-问题3：severity 过滤下推（在生命周期跟踪之前做，
        #   避免被过滤掉的问题污染 _issue_states / 历史趋势统计）
        if min_severity:
            _order = {"high": 0, "medium": 1, "low": 2, "info": 3}
            _threshold = _order.get(min_severity, 1)
            all_issues = [
                _i for _i in all_issues
                if _order.get(str(_i.get("severity", "medium")).lower(), 1) <= _threshold
            ]

        # ★v17.0新增：记录本次检测的类型分布，用于趋势分析
        _type_counts = {}
        for _issue in all_issues:
            _t = _issue.get("type", "unknown")
            _type_counts[_t] = _type_counts.get(_t, 0) + 1
        
        # ★v17.0 D5新增：问题生命周期跟踪
        _current_issue_ids = set()
        _now = time.time()
        
        for _issue in all_issues:
            _issue_id = f"{_issue.get('file', '')}:{_issue.get('line', 0)}:{_issue.get('type', 'unknown')}"
            _current_issue_ids.add(_issue_id)
            
            if _issue_id in self._issue_states:
                # 已跟踪的问题：更新状态
                _state = self._issue_states[_issue_id]
                _state["last_seen"] = _now
                _state["count"] = _state.get("count", 0) + 1
                
                if _state["status"] == "resolved":
                    # 之前标记为已修复但现在又出现了 → 重新打开
                    _state["status"] = "reopened"
                    _issue["lifecycle"] = "reopened"
                elif _state["status"] in ("confirmed", "reopened", "legacy"):
                    _issue["lifecycle"] = _state["status"]
                else:
                    _issue["lifecycle"] = "persistent"
            else:
                # 新问题：首次发现
                # 检查是否是历史遗留（上次扫描中也存在）
                if _issue_id in self._last_issue_ids:
                    # 上次存在但未跟踪的 → 标记为历史遗留
                    _is_legacy = _issue.get("type") in (
                        "long_method", "status_request_duplicate"
                    )
                    _status = "legacy" if _is_legacy else "new"
                else:
                    _status = "new"
                
                self._issue_states[_issue_id] = {
                    "status": _status,
                    "first_seen": _now,
                    "last_seen": _now,
                    "count": 1,
                    "type": _issue.get("type", "unknown"),
                    "file": _issue.get("file", ""),
                    "organ": _issue.get("organ", ""),
                    "method": _issue.get("method", ""),
                    "line": _issue.get("line", 0),
                }
                _issue["lifecycle"] = _status
            
            # 容量保护
            if len(self._issue_states) > self._max_issue_states:
                _oldest = sorted(self._issue_states.keys(),
                                key=lambda k: self._issue_states[k].get("last_seen", 0))[:100]
                for _old_key in _oldest:
                    del self._issue_states[_old_key]
        
        # 检测已修复的问题：上次存在但本次不存在的问题
        _resolved_ids = self._last_issue_ids - _current_issue_ids
        for _resolved_id in _resolved_ids:
            if _resolved_id in self._issue_states:
                _state = self._issue_states[_resolved_id]
                if _state["status"] not in ("resolved", "legacy"):
                    _state["status"] = "resolved"
                    _state["resolved_at"] = _now
        
        # 更新上次扫描记录
        self._last_issue_ids = _current_issue_ids
        
        # 统计各生命周期状态的数量
        _lifecycle_counts = {"new": 0, "persistent": 0, "resolved": 0, "legacy": 0, "reopened": 0}
        for _state in self._issue_states.values():
            _status = _state.get("status", "new")
            if _status in _lifecycle_counts:
                _lifecycle_counts[_status] += 1
        
        _record = {
            "timestamp": _now,
            "total_issues": len(all_issues),
            "active_issues": _lifecycle_counts.get("new", 0) + _lifecycle_counts.get("persistent", 0) + _lifecycle_counts.get("reopened", 0),
            "legacy_issues": _lifecycle_counts.get("legacy", 0),
            "type_counts": _type_counts,
            "lifecycle_counts": _lifecycle_counts,
            "resolved_this_scan": len(_resolved_ids),
        }
        self._issue_history.append(_record)
        if len(self._issue_history) > self._max_issue_history:
            self._issue_history = self._issue_history[-self._max_issue_history:]
        
        # ★主线第56批 T5/P2-395：代码学习已检测问题记忆（方案B 持久化）。
        #   跳过「已检测 + 文件未修改 + 未过期」的问题，节省重复检测/LLM 开销。
        #   灰度 ENABLE_CODE_LEARNING_MEMORY（默认开）：关闭→不跳过（零回归）。
        all_issues = self._apply_code_learning_memory(all_issues)

        # ★主线第60批 T3：最后一道防御过滤——排除 file 指向备份目录的问题。
        #   self_inspector 各扫描逻辑已通过 _should_exclude / startswith(".") 排除 .bak 目录，
        #   此处兜底，防止任何来源（含未来新增检测器）的问题漏入进化闭环占用名额。
        _bak_dropped = 0
        _safe_issues: list[dict[str, Any]] = []
        for _issue in all_issues:
            _if = _issue.get("file") or _issue.get("file_path") or _issue.get("path") or ""
            if _issue_file_in_backup_dir(_if):
                _bak_dropped += 1
                continue
            _safe_issues.append(_issue)
        if _bak_dropped:
            _module_logger.debug(
                f"[self_inspector] 防御过滤 {_bak_dropped} 个指向备份目录的问题")
        return _safe_issues

    def _apply_code_learning_memory(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """★T5/P2-395：对已检测问题做持久化记忆过滤（方案B）。

        跳过「已检测 + 文件未修改 + 未过期」的问题；记录保留（新/变更）的问题。
        灰度开关关闭 → 原样返回（零回归）。
        """
        try:
            from nucleus.code_learning_memory import CheckedIssueMemory
        except Exception:
            return issues
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_CODE_LEARNING_MEMORY", True):
                return issues
            _expiry = int(getattr(_cfg, "CODE_LEARNING_MEMORY_EXPIRY_DAYS", 7))
        except Exception:
            _expiry = 7
        _mem = CheckedIssueMemory(expiry_days=_expiry)
        _kept: list[dict[str, Any]] = []
        for _i in issues:
            _file = _i.get("file", "")
            _line = int(_i.get("line", 0) or 0)
            _type = _i.get("type", "unknown")
            if _mem.should_skip(_file, _line, _type):
                continue
            _kept.append(_i)
        _skipped = len(issues) - len(_kept)
        if _skipped:
            _module_logger.info(
                f"[代码学习记忆] 跳过已检测且未变更问题 {_skipped} 条（持久化去重）")
            for _i in _kept:
                _mem.record(_i.get("file", ""), int(_i.get("line", 0) or 0),
                            _i.get("type", "unknown"))
        return _kept

    def _run_detectors(self, organ_data: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """★P3-L2：纯检测循环（跑 18 种检测器 + 假阳性过滤 + severity 标注）。

        不含生命周期跟踪，供 `detect_code_issues`（全量，外部再叠加生命周期跟踪）
        与定向验证（定向，纯检测不污染生命周期）共用，避免检测逻辑重复。
        """
        all_issues: list[dict[str, Any]] = []
        for organ_name, info in organ_data.items():
            file_path = info.get("file_path", "")
            for method in info.get("methods", []):
                method_name = method.get("name", "")
                start_line = method.get("line_number", 0)
                # 读取方法体
                # ★PHASE12-P1-2扩展：传入方法名启用按名兜底。
                #   此处是全量检测的唯一入口，此前行号 off-by-one 导致约 60%
                #   的方法取不到 body 而直接 continue —— 即**从未被检测过**，
                #   所谓「发现 416 个问题」只是剩下 40% 方法里的问题。
                body = self._read_method_body(file_path, start_line,
                                              method_name=method_name)
                if not body:
                    continue
                # 检测各项问题
                self._check_silent_exception(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_lock_with_emit(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_method_length(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_status_request_duplicate(body, method_name, file_path, organ_name, start_line, all_issues)
                self._check_bare_except(body, file_path, organ_name, method_name, start_line, all_issues)
                # ★FIX(扩展检测维度): 补充安全/资源/日志类反模式检测
                self._check_unsafe_eval(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_no_timeout_http(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_unbounded_deque(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_subprocess_shell(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_thread_no_daemon(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_sql_injection(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_periodic_task_no_reentry(body, file_path, organ_name, method_name, start_line, all_issues)
                # ★FIX(架构基线固化): 资源未关闭 + 跨模块单例直调检测
                self._check_resource_no_close(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_cross_module_singleton_call(body, file_path, organ_name, method_name, start_line, all_issues)
                # ★架构基线固化（续）：死循环无退出 + 非原子写 + 静默吞异常 + 未管理线程
                self._check_busy_loop_no_exit(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_non_atomic_write(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_bare_return_none_in_except(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_unjoined_thread(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_mutable_default(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_hardcoded_abs_path(body, file_path, organ_name, method_name, start_line, all_issues)
                self._check_print_debug(body, file_path, organ_name, method_name, start_line, all_issues)
        # ★FIX(P0.5 分级初筛 + 假阳性治理): 标注置信度分级，并过滤已知误报
        _filtered_issues = []
        # ★P1补丁覆盖扩展：本地规则可自动修复的问题类型（与 SafeEvolutionExecutor._generate_patch 对齐）。
        # 其余类型明确标注「检测发现，不自动修复，建议人工/LLM 处理」，不模糊边界。
        _auto_fixable_types = {
            "silent_exception", "bare_except", "status_request_duplicate",
            "unbounded_deque", "thread_no_daemon", "no_timeout_http",
        }
        # ★PHASE17-C4：过滤逻辑抽为公共方法（带计数日志），此处仅调用
        for _issue in self._filter_false_positives(all_issues, stage="方法级检测器"):
            _issue["severity"] = self._issue_severity.get(_issue.get("type", ""), "medium")
            # ★Kimi 借鉴：每条发现附带「具体失败场景」——提高审查行动价值，供人工/下游决策
            _issue["failure_scenario"] = self._issue_failure_scenarios.get(
                _issue.get("type", ""),
                "在对应调用路径上可能触发该问题，需结合上下文评估实际影响",
            )
            # ★P1边界明确：标注是否可自动修复，供下游（补丁生成/人工审查）区分处理
            _issue["auto_fixable"] = _issue.get("type", "") in _auto_fixable_types
            if not _issue["auto_fixable"]:
                # 明确标注不可自动修复的边界，避免下游误以为「只检测不修复」是能力缺失
                _issue["repair_note"] = "检测发现，不自动修复，建议人工/LLM 处理"
            # ★P3-12修复：命中同类问题时，附加已蒸馏的 LLM 修复策略（自增强闭环）
            _learned = self._lesson_fix_strategies.get(_issue.get("type", ""))
            if _learned:
                _issue["learned_fix_strategy"] = _learned
            _filtered_issues.append(_issue)
        return _filtered_issues

    def run_tool_checks(self) -> list[dict[str, Any]]:
        """
        ★P0: 调用外部工具层（compileall/ruff/mypy/bandit），
        返回统一格式的问题列表（含 tool 标记），供代码学习链路消费。
        工具缺失时优雅降级，返回空列表。
        """
        try:
            from nucleus.tooling_runner import get_tooling_runner
            _project_root = self._project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _runner = get_tooling_runner(_project_root)
            _results = _runner.run_all_checks()
            _issues = []
            for _tool_issues in _results.values():
                for _issue in _tool_issues:
                    _file = _issue.get("file", "")
                    _organ = os.path.basename(_file).replace(".py", "") if _file else ""
                    _issue["organ"] = _organ
                    _issue["method"] = ""
                    _issue["line"] = _issue.get("line", 0)
                    _issues.append(_issue)
            return _issues
        except Exception:
            return []

    def analyze_issue_trends(self) -> dict[str, Any]:
        """
        ★v17.0新增：分析代码问题趋势。
        
        对比最近两次检测结果，判断各类型问题是增加、减少还是稳定。
        
        Returns:
            {
                "total_trend": "decreasing" | "stable" | "increasing",
                "total_change": int,
                "type_trends": {"silent_exception": "decreasing", ...},
                "comparison": {"previous_total": N, "current_total": N},
                "summary": str,
                "history_length": int,
            }
        """
        if len(self._issue_history) < 2:
            return {
                "total_trend": "stable",
                "total_change": 0,
                "type_trends": {},
                "comparison": {"previous_total": 0, "current_total": 0},
                "summary": "代码问题历史数据不足，无法分析趋势（至少需要2次检测记录）",
                "history_length": len(self._issue_history),
            }
        
        _previous = self._issue_history[-2]
        _current = self._issue_history[-1]
        
        _prev_total = _previous.get("total_issues", 0)
        _curr_total = _current.get("total_issues", 0)
        _total_change = _curr_total - _prev_total
        
        if _total_change <= -5:
            _total_trend = "decreasing"
        elif _total_change >= 5:
            _total_trend = "increasing"
        else:
            _total_trend = "stable"
        
        _prev_types = _previous.get("type_counts", {})
        _curr_types = _current.get("type_counts", {})
        _all_types = set(list(_prev_types.keys()) + list(_curr_types.keys()))
        
        _type_trends = {}
        for _t in _all_types:
            _prev_count = _prev_types.get(_t, 0)
            _curr_count = _curr_types.get(_t, 0)
            _delta = _curr_count - _prev_count
            if _delta <= -2:
                _type_trends[_t] = "decreasing"
            elif _delta >= 2:
                _type_trends[_t] = "increasing"
            else:
                _type_trends[_t] = "stable"
        
        _summary_parts = []
        if _total_trend == "decreasing":
            _summary_parts.append(
                f"代码问题总数正在减少（{_prev_total}→{_curr_total}，"
                f"减少{abs(_total_change)}个），质量持续改善。"
            )
        elif _total_trend == "increasing":
            _summary_parts.append(
                f"代码问题总数有所增加（{_prev_total}→{_curr_total}，"
                f"增加{_total_change}个），需要关注。"
            )
        else:
            _summary_parts.append(
                f"代码问题总数保持稳定（{_prev_total}→{_curr_total}，"
                f"变化{_total_change}个）。"
            )
        
        _type_names = {
            "silent_exception": "静默异常",
            "lock_with_emit": "锁内发射脉冲",
            "long_method": "过长方法",
            "status_request_duplicate": "重复状态代码",
            "bare_except": "裸except语句",
        }
        
        _increasing_types = [
            t for t, trend in _type_trends.items() if trend == "increasing"
        ]
        _decreasing_types = [
            t for t, trend in _type_trends.items() if trend == "decreasing"
        ]
        
        if _increasing_types:
            _names = [_type_names.get(t, t) for t in _increasing_types[:3]]
            _summary_parts.append(f"增长中的问题类型：{'、'.join(_names)}。")
        if _decreasing_types:
            _names = [_type_names.get(t, t) for t in _decreasing_types[:3]]
            _summary_parts.append(f"减少中的问题类型：{'、'.join(_names)}。")
        
        return {
            "total_trend": _total_trend,
            "total_change": _total_change,
            "lifecycle_counts": self.get_issue_lifecycle_stats(),  # ★v17.0 D5新增
            "type_trends": _type_trends,
            "comparison": {
                "previous_total": _prev_total,
                "current_total": _curr_total,
            },
            "summary": "。".join(_summary_parts) + "。",
            "history_length": len(self._issue_history),
        }
    def get_issue_lifecycle_stats(self) -> dict[str, int]:
        """
        ★v17.0 D5新增：获取问题生命周期统计。
        
        Returns:
            {"new": N, "persistent": N, "resolved": N, "legacy": N, "reopened": N}
        """
        _counts = {"new": 0, "persistent": 0, "resolved": 0, "legacy": 0, "reopened": 0}
        for _state in self._issue_states.values():
            _status = _state.get("status", "new")
            if _status in _counts:
                _counts[_status] += 1
        return _counts
    
    def get_recent_resolved_issues(self, limit: int = 5) -> list[dict[str, Any]]:
        """
        ★v17.0 D5新增：获取最近已修复的问题列表。
        """
        _resolved = []
        for _issue_id, _state in self._issue_states.items():
            if _state.get("status") == "resolved":
                _resolved.append({
                    "issue_id": _issue_id,
                    "type": _state.get("type", "unknown"),
                    "file": _state.get("file", ""),
                    "method": _state.get("method", ""),
                    "resolved_at": _state.get("resolved_at", 0),
                })
        _resolved.sort(key=lambda x: x.get("resolved_at", 0), reverse=True)
        return _resolved[:limit]    
    def compare_expected_vs_actual(self, organ_name: str, method_name: str,
                                     expected_behavior: str = "",
                                     actual_traces: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """
        ★v17.0 D8预埋：对比代码描述的预期行为与实际运行追踪。
        
        v18.0将实现完整的对比分析逻辑，当前仅预埋接口。
        
        Args:
            organ_name: 器官名
            method_name: 方法名
            expected_behavior: 代码学习分析出的预期行为描述
            actual_traces: 运行时追踪记录列表
        
        Returns:
            {"match": bool, "discrepancies": [...], "confidence": float}
        """
        if not actual_traces:
            return {
                "match": None,
                "discrepancies": [],
                "confidence": 0.0,
                "status": "insufficient_data",
                "summary": "运行时追踪数据不足，无法对比",
            }
        
        # v18.0将实现完整对比逻辑
        return {
            "match": None,
            "discrepancies": [],
            "confidence": 0.0,
            "status": "preliminary",
            "summary": f"已收集{len(actual_traces)}条追踪记录，对比分析将在v18.0实现",
            "trace_summary": {
                "entry_count": sum(1 for t in actual_traces if t.get("type") == "entry"),
                "exit_count": sum(1 for t in actual_traces if t.get("type") == "exit"),
            },
        }    
    def _check_silent_exception(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测静默异常捕获：except Exception: pass 且无日志"""
        pattern = re.compile(r'except\s*(?:Exception)?\s*:\s*\n(\s+)pass')
        for match in pattern.finditer(body):
            before = body[:match.start()]
            if 'self._log' not in before.split('\n')[-3:]:
                line_num = start_line + body[:match.start()].count('\n')
                suggestion = self._generate_fix_suggestion("silent_exception", file_path, method, line_num)
                issues.append({
                    "file": file_path,
                    "organ": organ,
                    "method": method,
                    "line": line_num,
                    "type": "silent_exception",
                    "description": "静默异常捕获：except Exception: pass，且无日志记录",
                    "suggestion": suggestion,
                })

    def _check_lock_with_emit(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测锁内发射脉冲：with self._lock: 内部调用 self._emit"""
        if 'self._lock' not in body and 'self._lock.acquire' not in body:
            return
        if 'self._lock' in body and 'self._emit' in body:
            lock_pos = body.find('self._lock')
            emit_pos = body.find('self._emit')
            if lock_pos < emit_pos:
                line_num = start_line + body[:emit_pos].count('\n')
                suggestion = self._generate_fix_suggestion("lock_with_emit", file_path, method, line_num)
                issues.append({
                    "file": file_path,
                    "organ": organ,
                    "method": method,
                    "line": line_num,
                    "type": "lock_with_emit",
                    "description": "可能锁内发射脉冲：方法内同时存在 self._lock 和 self._emit",
                    "suggestion": suggestion,
                })

    def _check_method_length(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测过长方法：方法体行数 > 200（白名单中的已知超长方法除外）"""
        # 白名单检查：设计上就是超长的方法不标记
        _file_key = os.path.basename(file_path).replace(".py", "")
        if (_file_key, method) in self._long_method_whitelist:
            return
        lines = body.count('\n') + 1
        if lines > 200:
            suggestion = self._generate_fix_suggestion("long_method", file_path, method, start_line)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": start_line,
                "type": "long_method",
                "description": f"方法过长：{lines} 行 (>200)，建议拆分",
                "suggestion": suggestion,
            })

    def _check_status_request_duplicate(self, body: str, method_name: str, file_path: str, organ: str, start_line: int, issues: list):
        """检测 _on_status_request 是否仅调用 self.get_stats()"""
        if method_name == "_on_status_request":
            # 去除空白和注释后，判断是否只有 return self.get_stats()
            cleaned = re.sub(r'#.*', '', body).strip()
            if cleaned != 'return self.get_stats()':
                suggestion = self._generate_fix_suggestion("status_request_duplicate", file_path, method_name, start_line)
                issues.append({
                    "file": file_path,
                    "organ": organ,
                    "method": method_name,
                    "line": start_line,
                    "type": "status_request_duplicate",
                    "description": "_on_status_request 未统一调用 self.get_stats()，可能存在重复代码",
                    "suggestion": suggestion,
                })
    def _check_bare_except(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测裸 except: 语句"""
        pattern = re.compile(r'^\s*except\s*:')
        for match in pattern.finditer(body):
            line_num = start_line + body[:match.start()].count('\n')
            suggestion = self._generate_fix_suggestion("bare_except", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "bare_except",
                "description": "使用了裸 except:，可能捕获系统异常（KeyboardInterrupt等）",
                "suggestion": suggestion,
            })

    def _check_unsafe_eval(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测不安全 eval/exec 使用（★FIX: 扩展检测维度·安全风险）"""
        pattern = re.compile(r'\b(eval|exec)\s*\(')
        for match in pattern.finditer(body):
            # 排除安全注释或字符串字面量
            line_num = start_line + body[:match.start()].count('\n')
            suggestion = self._generate_fix_suggestion("unsafe_eval", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "unsafe_eval",
                "description": "使用了 eval/exec，存在任意代码执行风险，建议改为 AST 白名单解析",
                "suggestion": suggestion,
            })

    def _check_no_timeout_http(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测网络请求无超时（★FIX: 扩展检测维度·资源泄漏）"""
        pattern = re.compile(r'(requests\.(?:get|post|put|delete)|urlopen)\s*\(')
        for match in pattern.finditer(body):
            _call_text = body[match.start():body.find(')', match.start()) + 1]
            if 'timeout' not in _call_text:
                line_num = start_line + body[:match.start()].count('\n')
                suggestion = self._generate_fix_suggestion("no_timeout_http", file_path, method, line_num)
                issues.append({
                    "file": file_path,
                    "organ": organ,
                    "method": method,
                    "line": line_num,
                    "type": "no_timeout_http",
                    "description": "网络请求未设置 timeout，可能挂死线程，建议加 timeout=30",
                    "suggestion": suggestion,
                })

    def _check_unbounded_deque(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测无界 deque 缓存（★FIX: 扩展检测维度·内存泄漏）"""
        pattern = re.compile(r'deque\s*\(\s*\)')
        for match in pattern.finditer(body):
            line_num = start_line + body[:match.start()].count('\n')
            suggestion = self._generate_fix_suggestion("unbounded_deque", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "unbounded_deque",
                "description": "使用无 maxlen 的 deque，长时间运行可能内存无限增长，建议 deque(maxlen=N)",
                "suggestion": suggestion,
            })

    def _check_subprocess_shell(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测 subprocess shell=True（★FIX: 扩展检测维度·命令注入风险）"""
        pattern = re.compile(r'shell\s*=\s*True')
        for match in pattern.finditer(body):
            if 'subprocess' not in body:
                continue
            line_num = start_line + body[:match.start()].count('\n')
            suggestion = self._generate_fix_suggestion("subprocess_shell", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "subprocess_shell",
                "description": "subprocess 使用 shell=True，存在命令注入风险，建议改为参数列表形式",
                "suggestion": suggestion,
            })

    def _check_thread_no_daemon(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测线程未设置 daemon（★FIX: 扩展检测维度·线程泄漏）"""
        pattern = re.compile(r'threading\.Thread\s*\(')
        for match in pattern.finditer(body):
            _call_end = body.find(')', match.start())
            _call_text = body[match.start():_call_end + 1] if _call_end > 0 else ""
            if 'daemon' not in _call_text:
                line_num = start_line + body[:match.start()].count('\n')
                suggestion = self._generate_fix_suggestion("thread_no_daemon", file_path, method, line_num)
                issues.append({
                    "file": file_path,
                    "organ": organ,
                    "method": method,
                    "line": line_num,
                    "type": "thread_no_daemon",
                    "description": "创建线程未设置 daemon，进程退出时可能残留线程，建议 daemon=True",
                    "suggestion": suggestion,
                })

    def _check_sql_injection(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测 SQL 字符串拼接（★FIX: 扩展检测维度·SQL注入风险）"""
        pattern = re.compile(r'f?["\'].*(?:SELECT|INSERT|UPDATE|DELETE|WHERE)\s+.*\{.*\}.*["\']', re.IGNORECASE | re.DOTALL)
        for match in pattern.finditer(body):
            line_num = start_line + body[:match.start()].count('\n')
            suggestion = self._generate_fix_suggestion("sql_injection", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "sql_injection",
                "description": "SQL 语句通过字符串拼接变量，存在 SQL 注入风险，建议使用参数化查询",
                "suggestion": suggestion,
            })

    def _check_periodic_task_no_reentry(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测周期任务方法缺少重入守卫（★FIX: 架构基线固化·周期任务必须有重入锁）"""
        _has_periodic = ('threading.Timer' in body or 'Timer(' in body
                         or '_schedule_' in body or '_schedule_next' in body)
        if not _has_periodic:
            return
        _has_guard = ('_in_progress' in body or '_is_running' in body or '_busy' in body)
        if _has_guard:
            return
        suggestion = self._generate_fix_suggestion("periodic_task_no_reentry", file_path, method, start_line)
        issues.append({
            "file": file_path,
            "organ": organ,
            "method": method,
            "line": start_line,
            "type": "periodic_task_no_reentry",
            "description": "周期任务方法缺少重入守卫（无 _in_progress/_busy 标志），上一轮未完成时可能重入堆积",
            "suggestion": suggestion,
        })

    def _check_resource_no_close(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测连接/会话资源创建后未关闭（★FIX: 架构基线固化·外部连接必须断开）。"""
        _resource_patterns = [
            r'\bsocket\.socket\s*\(', r'\brequests\.Session\s*\(', r'\bsqlite3\.connect\s*\(',
            r'\bpymysql\.connect\s*\(', r'\bpsycopg2\.connect\s*\(', r'\bredis\.Redis\s*\(',
            r'\baiohttp\.ClientSession\s*\(', r'\bhttpx\.Client\s*\(', r'\bwebsocket\.',
        ]
        for _pat in _resource_patterns:
            _m = re.search(_pat, body)
            if not _m:
                continue
            # 同一方法体内存在 .close() 则视为已处理
            if '.close()' in body or '.close(' in body:
                continue
            line_num = start_line + body[:_m.start()].count('\n')
            suggestion = self._generate_fix_suggestion("resource_no_close", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "resource_no_close",
                "description": "创建了连接/会话资源但未在同一方法内关闭，长期运行可能资源泄漏",
                "suggestion": suggestion,
            })
            return  # 每个方法只报一次，避免同一资源多模式重复

    def _check_cross_module_singleton_call(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测直接调用其他模块单例的方法（★FIX: 架构基线固化·器官不得跨模块直调）。"""
        # get_xxx().method() 或 get_xxx().method 模式，是绕过脉冲总线的跨模块直调信号
        _pat = re.compile(r'\bget_\w+\(\)\.\w+\s*\(')
        _seen = set()
        for _m in _pat.finditer(body):
            _sig = _m.group()
            if _sig in _seen:
                continue
            _seen.add(_sig)
            line_num = start_line + body[:_m.start()].count('\n')
            suggestion = self._generate_fix_suggestion("cross_module_singleton_call", file_path, method, line_num)
            issues.append({
                "file": file_path,
                "organ": organ,
                "method": method,
                "line": line_num,
                "type": "cross_module_singleton_call",
                "description": f"直接调用其他模块单例的方法（{_sig.strip()}），可能绕过脉冲总线造成隐式耦合",
                "suggestion": suggestion,
            })

    def _check_busy_loop_no_exit(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测无退出条件的忙等死循环（★架构基线固化·while True 必须有 break/return/sleep/停止检查）。

        规则：方法体内出现 while True（或 while 1:）且不含任何退出/让步信号时，
        标记为高风险忙等循环，可能导致 CPU 空转或无法停机。
        """
        _loop_pat = re.compile(r'\bwhile\s+(True|1)\s*:')
        _m = _loop_pat.search(body)
        if not _m:
            return
        # 退出/让步信号：break、return、sleep、停止标志检查、yield
        _exit_signals = ('break', 'return', 'sleep', '_stop_requested', '_stop_flag',
                         'time.sleep', 'event.wait', 'queue.get', 'yield')
        if any(_s in body for _s in _exit_signals):
            return
        line_num = start_line + body[:_m.start()].count('\n')
        suggestion = self._generate_fix_suggestion("busy_loop_no_exit", file_path, method, line_num)
        issues.append({
            "file": file_path,
            "organ": organ,
            "method": method,
            "line": line_num,
            "type": "busy_loop_no_exit",
            "description": "while True 死循环缺少 break/return/sleep/停止检查等退出信号，可能 CPU 空转或无法停机",
            "suggestion": suggestion,
        })

    def _check_non_atomic_write(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测非原子写文件（★架构基线固化·重要数据必须原子写，防断电损坏）。

        规则：方法体内出现 open(..., 'w') 直接写数据文件（json/snapshot/state），
        且无临时文件 + os.replace 原子落盘模式时，标记为风险。
        """
        _write_pat = re.compile(r'open\(([^)]*[\'"])w[\'"]')
        _m = _write_pat.search(body)
        if not _m:
            return
        # 已用原子写模式：出现 .tmp / tempfile / os.replace / os.rename
        if any(_s in body for _s in ('.tmp', 'tempfile', 'os.replace', 'os.rename')):
            return
        line_num = start_line + body[:_m.start()].count('\n')
        suggestion = self._generate_fix_suggestion("non_atomic_write", file_path, method, line_num)
        issues.append({
            "file": file_path,
            "organ": organ,
            "method": method,
            "line": line_num,
            "type": "non_atomic_write",
            "description": "直接以写模式打开文件，未采用「临时文件 + os.replace」原子写，断电/异常可能损坏数据",
            "suggestion": suggestion,
        })

    def _check_bare_return_none_in_except(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测 except 块内直接 return None 吞异常（★架构基线固化·异常必须可观测）。

        规则：except 分支内直接 return None（无日志、无重试），
        会把异常静默吞掉，调用方无法感知失败原因。
        """
        _pat = re.compile(r'except[^\n]*:\n(\s+)(return\s+None\b)')
        _m = _pat.search(body)
        if not _m:
            return
        # 该 except 块内若无日志记录则标记
        _block = body[_m.start():]
        if 'self._log' in _block or '_logger' in _block or 'logger.' in _block:
            return
        line_num = start_line + body[:_m.start()].count('\n')
        suggestion = self._generate_fix_suggestion("bare_return_none_in_except", file_path, method, line_num)
        issues.append({
            "file": file_path,
            "organ": organ,
            "method": method,
            "line": line_num,
            "type": "bare_return_none_in_except",
            "description": "except 块内直接 return None 且无日志，静默吞异常，调用方无法感知失败原因",
            "suggestion": suggestion,
        })

    def _check_unjoined_thread(self, body: str, file_path: str, organ: str, method: str, start_line: int, issues: list):
        """检测启动线程后未 join/未管理生命周期（★架构基线固化·线程必须可回收）。

        规则：方法体内创建 Thread 并 start()，但无 join()/daemon 管理，
        可能造成线程泄漏或进程退出残留。
        """
        if 'Thread' not in body or '.start()' not in body:
            return
        # 已有生命周期管理信号
        if '.join()' in body or '.join(' in body or 'daemon=True' in body or 'daemon = True' in body:
            return
        line_num = start_line + body[:body.find('.start()')].count('\n')
        suggestion = self._generate_fix_suggestion("unjoined_thread", file_path, method, line_num)
        issues.append({
            "file": file_path,
            "organ": organ,
            "method": method,
            "line": line_num,
            "type": "unjoined_thread",
            "description": "启动线程后未 join 且未设 daemon，可能造成线程泄漏或进程退出残留",
            "suggestion": suggestion,
        })

    def _format_structure_tree(self, node: dict[str, Any], indent: int = 0) -> str:
        """将目录树节点格式化为缩进文本"""
        prefix = "  " * indent
        name = node.get("name", "")
        node_type = node.get("type", "")
        
        if node_type == "dir":
            line = f"{prefix}📁 {name}/"
        else:
            size = node.get("size", 0)
            size_str = f" ({size//1024}KB)" if size > 1024 else f" ({size}B)" if size > 0 else ""
            line = f"{prefix}📄 {name}{size_str}"
        
        lines = [line]
        for child in node.get("children", []):
            lines.append(self._format_structure_tree(child, indent + 1))
        
        return "\n".join(lines)

    # ========== 核心公开接口 ==========
    
    def get_self_description(self, privacy_level: str = "public") -> str:
        """
        获取框架的自我描述文本。
        
        Args:
            privacy_level: 隐私层级，可选 public/restricted/private/confidential
        
        Returns:
            人类可读的自我描述文本
        """
        if not self._enabled:
            return "自我审视功能当前未启用。"
        
        parts = []
        
        # 公开层级：系统概要
        summary = self.get_system_summary()
        parts.append(f"我是{summary['system_name']}，版本{summary['system_version']}。")
        parts.append("我的创造者是小林。")
        parts.append(f"我的使命是：{summary.get('mission', '守护这个世界，承人类赤诚本心')}")
        parts.append(f"我由{summary['organ_count']}个仿生器官组成，覆盖大脑、核心脏器、感知、运动、身份、免疫、内分泌、遗传、内核支撑九大系统。")
        parts.append(f"我的知识体系分为四级：{summary['knowledge_levels']}。")
        
        if privacy_level in ("restricted", "private", "confidential"):
            # 受限层级：器官结构
            organ_structure = self.get_organ_structure()
            parts.append(f"\n我的{organ_structure['total_organs']}个器官分布在以下系统中：")
            for cat_name, cat_info in organ_structure['categories'].items():
                parts.append(f"  - {cat_name}：{cat_info['count']}个（{', '.join(cat_info['organs'])}）")
            
            # 知识体系
            knowledge = self.get_knowledge_system()
            parts.append(f"\n我的知识演化流程是：{knowledge['evolution_flow']}。")
            parts.append("知识质量由四道防线守护。")
        
        if privacy_level in ("private", "confidential"): 
            # 私密层级：器官职责详情
            detailed = self.get_detailed_structure()
            parts.append(f"\n我的通信架构是：{detailed['communication']}。")
            # 代码结构摘要
            code_summary = self.get_organ_code_structure()
            # ★修复：原 "error" not in str(code_summary) 子串匹配脆弱，
            # 正常结果里方法名含 error 字样会被误判。改为结构化判断（仅 error 键字典才算异常）。
            if code_summary and not (isinstance(code_summary, dict) and len(code_summary) == 1 and "error" in code_summary):
                total_methods = sum(c.get("method_count", 0) for c in code_summary.values())
                parts.append(f"\n我的{len(code_summary)}个器官文件中，共定义了约{total_methods}个方法。")
                # 列出几个核心器官的方法数量
                key_organs = ["PulseLiver", "PulseInnerWorld", "PulseSubconscious", "PulseCortex"]
                for ko in key_organs:
                    if ko in code_summary:
                        parts.append(f"  - {ko}：{code_summary[ko].get('method_count', 0)}个方法")            
            for cat_name, organs in detailed['organ_details'].items():
                if organs:
                    parts.append(f"\n{cat_name}：")
                    for role in organs.values():
                        parts.append(f"  - {role}")
        
        if privacy_level == "confidential":
            # 核心机密：完整项目结构（仅创造者）
            project = self.get_project_structure()
            # ★修复：结构化判断异常（仅 error 键字典），替代脆弱的 "error" not in project
            if not (isinstance(project, dict) and len(project) == 1 and "error" in project):
                parts.append("\n我的完整项目结构如下：")
                parts.append(self._format_structure_tree(project, indent=0))
        
        return "\n".join(parts)
    
    def get_info_by_field(self, field_name: str) -> Any | None:
        """
        按字段名获取特定信息。
        用于内在世界需要特定维度信息时的精确查询。
        """
        field_map = {
            "system_name": lambda: self.get_system_summary().get("system_name", ""),
            "system_version": lambda: self.get_system_summary().get("system_version", ""),
            "organ_count": lambda: self.get_system_summary().get("organ_count", 50),
            "organ_categories": lambda: list(self._get_organ_categories().keys()),
            "organ_list": lambda: self.get_organ_structure(),
            "knowledge_levels": lambda: self.get_knowledge_system().get("levels", {}),
            "knowledge_flow": lambda: self.get_knowledge_system().get("evolution_flow", ""),
            "quality_defense": lambda: self.get_knowledge_system().get("quality_defense", []),
            "organ_details": lambda: self.get_detailed_structure().get("organ_details", {}),
            "communication": lambda: self.get_detailed_structure().get("communication", ""),
            "project_structure": lambda: self.get_project_structure(),
            "code_structure": lambda: self.get_organ_code_structure(),
            "method_detail": lambda organ_name=None, method_name=None: 
                self.get_method_detail(organ_name, method_name) if organ_name and method_name else None,
        }
        
        func = field_map.get(field_name)
        if func:
            try:
                return func()
            except Exception as e:
                return f"获取信息失败: {e}"
        return None

    # ========== 动态状态审视 ==========
    def get_dynamic_state_report(self, node_pool=None, info_field=None,
                                  inference_trace=None, active_learning_goal=None,
                                  goal_lock_window=None, code_issues_count=0) -> dict[str, Any]:
        """
        【v12.0增强】获取框架动态运行状态的完整报告。
        
        支持外部注入推理链、学习目标、代码问题等运行时数据，
        生成包含知识、推理、学习、资源、代码健康五个维度的完整报告。
        """
        report = {
            "timestamp": time.time(),
            "knowledge": {},
            "reasoning": {},
            "learning": {},
            "resources": {},
            "code_health": {},
            "health": "unknown",
        }
        
        # 1. 知识状态
        if node_pool:
            try:
                stats = node_pool.get_stats()
                evol_dist = stats.get("evol_distribution", {})
                report["knowledge"] = {
                    "total_nodes": stats.get("total_nodes", 0),
                    "L1_count": evol_dist.get("L1", 0),
                    "L2_count": evol_dist.get("L2", 0),
                    "L3_count": evol_dist.get("L3", 0),
                    "instinct_count": stats.get("instinct_count", 0),
                }
            except Exception:
                report["knowledge"] = {"total_nodes": 0, "error": "node_pool读取失败"}
        
        # 2. 推理质量（由调用方注入）
        if inference_trace is not None:
            recent = inference_trace[-30:] if len(inference_trace) >= 30 else inference_trace
            if recent:
                high_conf = sum(1 for t in recent if t.get("confidence", 0) >= 0.7)
                method_counts = {}
                for t in recent:
                    m = t.get("method", "unknown").split("_")[0]
                    method_counts[m] = method_counts.get(m, 0) + 1
                top_methods = sorted(method_counts.items(), key=lambda x: x[1], reverse=True)[:5]
                report["reasoning"] = {
                    "recent_count": len(recent),
                    "high_confidence_ratio": f"{high_conf}/{len(recent)}" if recent else "N/A",
                    "high_confidence_pct": round(high_conf / len(recent) * 100) if recent else 0,
                    "methods_used": [f"{m}({c})" for m, c in top_methods],
                }
            else:
                report["reasoning"] = {"recent_count": 0, "note": "推理链数据不足"}
        
        # 3. 学习进展（由调用方注入）
        if active_learning_goal:
            goal = active_learning_goal
            started = goal.get("started_at", time.time())
            report["learning"] = {
                "active_goal": goal.get("target_area", "无"),
                "duration_hours": round((time.time() - started) / 3600, 1),
                "locked": (time.time() - started) < (goal_lock_window or 7200) if goal_lock_window else True,
            }
        else:
            report["learning"] = {"active_goal": "无活跃学习目标"}
        
        # 4. 资源使用
        if info_field:
            try:
                field_stats = info_field.get_stats()
                report["resources"] = {
                    "active_conditions": field_stats.get("active_conditions", 0),
                    "total_published": field_stats.get("total_published", 0),
                    "load_level": field_stats.get("load_level", "unknown"),
                    "layer_pools": list(field_stats.get("layer_pools", {}).keys()),
                }
            except Exception:
                report["resources"] = {"error": "info_field读取失败"}
        
        # 5. 代码健康（保留原数据，重命名为更明确的结构）
        report["code_health"] = {
            "issues_found": code_issues_count,
            "status": "健康" if code_issues_count == 0 else f"发现{code_issues_count}个潜在问题" if code_issues_count <= 5 else f"需关注({code_issues_count}个问题)",
        }
        # ===== 【P2-1新增】第六维度：推理精度 =====
        report["reasoning_precision"] = self._generate_reasoning_precision_analysis(
            inference_trace=inference_trace,
            code_issues_count=code_issues_count,
        )
        # ===== 推理精度维度结束 =====
        
        # 综合健康判定（含推理精度维度）
        total_nodes = report["knowledge"].get("total_nodes", 0)
        load_level = report["resources"].get("load_level", "unknown")
        issues = report["code_health"].get("issues_found", 0)
        # 【P2-1新增】推理精度对综合评分的影响
        reasoning_score = report.get("reasoning_precision", {}).get("overall_score", 60)
        
        if total_nodes > 100 and load_level == "light" and issues <= 5 and reasoning_score >= 70:
            report["health"] = "优秀——知识体系丰富，系统负载轻盈，推理精度可靠"
        elif total_nodes > 50 and load_level in ("light", "normal") and reasoning_score >= 55:
            report["health"] = "良好——知识体系持续增长，系统运行稳定"
        elif total_nodes > 20:
            report["health"] = "成长中——知识体系正在构建，需要更多学习"
        else:
            report["health"] = "初期——知识体系尚小，处于快速积累阶段"
        # 【P2-1新增】自动生成隐性短板和优化策略
        report["diagnosis"] = self._generate_reasoning_diagnosis(
            report=report,
            code_issues_count=code_issues_count,
        )
        # ===== 自动诊断结束 =====
        
        return report
    def _generate_reasoning_precision_analysis(self, inference_trace: list | None = None,
                                                code_issues_count: int = 0) -> dict[str, Any]:
        """
        【P2-1新增】从推理链数据中提取推理精度指标。
        
        评估维度：
        1. 高置信度占比——近期推理中confidence≥0.7的比例
        2. 推理方法覆盖度——使用了多少种不同的推理方法
        3. 经验库命中率——经验匹配路由的命中比例
        4. 推理耗时趋势——近期推理平均耗时变化
        5. 综合评分——加权计算后的百分制评分
        
        Returns:
            {"high_confidence_ratio": float, "method_coverage": int,
             "experience_hit_rate": float, "avg_duration": float,
             "overall_score": int, "status": str, "suggestions": [...]}
        """
        result = {
            "high_confidence_ratio": 0.0,
            "method_coverage": 0,
            "experience_hit_rate": 0.0,
            "avg_duration": 0.0,
            "overall_score": 60,
            "status": "数据不足，无法评估推理精度",
            "suggestions": [],
        }
        
        if not inference_trace or len(inference_trace) < 5:
            return result
        
        recent = inference_trace[-30:] if len(inference_trace) >= 30 else inference_trace
        total = len(recent)
        
        # 1. 高置信度占比
        high_conf_count = sum(1 for t in recent if t.get("confidence", 0) >= 0.7)
        high_conf_ratio = high_conf_count / total if total > 0 else 0
        result["high_confidence_ratio"] = round(high_conf_ratio, 2)
        
        # 2. 推理方法覆盖度
        methods_used = set()
        experience_hit_count = 0
        total_duration = 0.0
        duration_count = 0
        
        for t in recent:
            method = t.get("method", "unknown")
            base_method = method.split("_")[0]
            methods_used.add(base_method)
            # 经验库命中统计
            if method.startswith("experience_"):
                experience_hit_count += 1
            # 耗时统计
            duration = t.get("duration", 0)
            if duration > 0:
                total_duration += duration
                duration_count += 1
        
        result["method_coverage"] = len(methods_used)
        result["experience_hit_rate"] = round(experience_hit_count / total, 2) if total > 0 else 0
        result["avg_duration"] = round(total_duration / duration_count, 2) if duration_count > 0 else 0
        
        # 3. 综合评分（百分制）
        score = 60  # 基础分
        # 高置信度占比加分（0-20分）
        score += int(high_conf_ratio * 20)
        # 方法覆盖度加分（0-10分）
        score += min(10, len(methods_used) * 2)
        # 经验库命中率加分（0-10分）
        exp_rate = result["experience_hit_rate"]
        if exp_rate >= 0.3:
            score += 10
        elif exp_rate >= 0.1:
            score += 5
        # 代码问题扣分（0-10分）
        if code_issues_count > 50:
            score -= 10
        elif code_issues_count > 20:
            score -= 5
        
        result["overall_score"] = max(0, min(100, score))
        
        # 4. 状态判定
        if score >= 80:
            result["status"] = f"优秀——推理精度可靠（{score}分）"
        elif score >= 65:
            result["status"] = f"良好——推理精度稳定（{score}分）"
        elif score >= 50:
            result["status"] = f"一般——推理精度有待提升（{score}分）"
        else:
            result["status"] = f"需关注——推理精度偏低（{score}分）"
        
        # 5. 自动建议
        suggestions = []
        if high_conf_ratio < 0.4:
            suggestions.append("高置信度推理占比偏低，建议检查推理路由是否准确、知识库是否充足")
        if len(methods_used) < 3:
            suggestions.append("推理方法单一，建议扩展认知算子使用范围")
        if exp_rate < 0.1 and total >= 10:
            suggestions.append("经验库命中率低，建议检查经验匹配路由是否正常运作")
        if result["avg_duration"] > 3.0:
            suggestions.append(f"平均推理耗时{result['avg_duration']:.1f}秒偏高，建议检查是否有算子阻塞")
        result["suggestions"] = suggestions
        
        return result    
    def _generate_reasoning_diagnosis(self, report: dict[str, Any],
                                       code_issues_count: int = 0) -> dict[str, Any]:
        """
        【P2-1新增】基于六维度报告自动生成隐性短板、潜在风险和优化策略。
        
        Returns:
            {"hidden_weaknesses": [...], "potential_risks": [...], "optimization_strategies": [...]}
        """
        diagnosis = {
            "hidden_weaknesses": [],
            "potential_risks": [],
            "optimization_strategies": [],
        }
        
        # 从报告中提取各维度数据
        knowledge = report.get("knowledge", {})
        reasoning = report.get("reasoning", {})
        learning = report.get("learning", {})
        resources = report.get("resources", {})
        reasoning_precision = report.get("reasoning_precision", {})
        
        total_nodes = knowledge.get("total_nodes", 0)
        l2_count = knowledge.get("L2_count", 0)
        l3_count = knowledge.get("L3_count", 0)
        
        # === 隐性短板检测 ===
        # 短板1：知识深度不足
        if total_nodes > 50 and l3_count < 10:
            diagnosis["hidden_weaknesses"].append(
                f"知识总量{total_nodes}个节点但L3智慧仅{l3_count}个，知识深度不足。"
                f"建议增加L2→L3融合频次，或降低融合阈值加速智慧沉淀"
            )
        # 短板2：推理方法单一
        methods_used = reasoning.get("methods_used", [])
        if len(methods_used) <= 2:
            diagnosis["hidden_weaknesses"].append(
                f"近期推理方法仅{len(methods_used)}种，认知灵活性不足。"
                f"建议在不同类型问题上尝试更多推理算子"
            )
        # 短板3：学习目标长期未切换
        active_goal = learning.get("active_goal", "")
        duration_hours = learning.get("duration_hours", 0)
        if active_goal and duration_hours > 6:
            diagnosis["hidden_weaknesses"].append(
                f"学习目标'{active_goal}'已持续{duration_hours:.0f}小时，可能陷入局部深耕。"
                f"建议定期轮换学习方向以保持知识广度"
            )
        # 短板4：推理精度评分低
        precision_score = reasoning_precision.get("overall_score", 60)
        if precision_score < 60:
            diagnosis["hidden_weaknesses"].append(
                f"推理精度综合评分{precision_score}分，低于及格线。"
                f"建议优先修复P0级推理缺陷（路由错乱、输出污染）"
            )
        # 短板5：经验库命中率低
        exp_hit_rate = reasoning_precision.get("experience_hit_rate", 0)
        if exp_hit_rate < 0.1 and total_nodes > 30:
            diagnosis["hidden_weaknesses"].append(
                f"经验库命中率仅{exp_hit_rate:.0%}，推理路由未能从历史经验中学习。"
                f"建议检查ReasoningExperience经验库是否正常运作"
            )
        
        # === 潜在风险检测 ===
        # 风险1：代码问题累积
        if code_issues_count > 50:
            diagnosis["potential_risks"].append(
                f"代码中存在{code_issues_count}个待处理问题，长期不修复可能导致推理稳定性下降"
            )
        # 风险2：知识库碎片化
        l1_count = knowledge.get("L1_count", 0)
        if l1_count > l2_count * 3 and l2_count > 0:
            diagnosis["potential_risks"].append(
                f"L1感知节点{l1_count}个远超L2认知节点{l2_count}个，知识碎片化风险。"
                f"建议触发肝脏紧急压缩"
            )
        # 风险3：系统负载上升趋势
        load_level = resources.get("load_level", "light")
        if load_level in ("heavy", "critical"):
            diagnosis["potential_risks"].append(
                f"当前系统负载为{load_level}，持续高负载可能导致推理响应延迟"
            )
        # 风险4：推理精度下降趋势
        high_conf_ratio = reasoning_precision.get("high_confidence_ratio", 0)
        if high_conf_ratio < 0.3 and total_nodes > 20:
            diagnosis["potential_risks"].append(
                f"近期推理高置信度占比仅{high_conf_ratio:.0%}，存在推理质量下降风险"
            )
        
        # === 优化策略生成 ===
        # 策略1：知识体系优化
        if l2_count > 0 and l3_count < l2_count * 0.15:
            diagnosis["optimization_strategies"].append(
                "【知识优化】当前L3/L2比例偏低，建议通过以下方式加速智慧沉淀："
                "降低自我认知路径融合阈值、增加知识验证频次、触发本能升级检查"
            )
        # 策略2：推理路由优化
        if precision_score < 70:
            diagnosis["optimization_strategies"].append(
                "【推理优化】推理精度评分偏低，建议："
                "检查路由优先级是否准确、补充推理算子专项知识节点、启用标准化输出模板"
            )
        # 策略3：代码质量优化
        if code_issues_count > 10:
            diagnosis["optimization_strategies"].append(
                f"【代码优化】检测到{code_issues_count}个代码问题，建议："
                "优先修复P0级问题（静默异常、锁内发射），然后逐步处理方法过长和代码重复"
            )
        # 策略4：学习方法优化
        if active_goal and duration_hours > 4:
            diagnosis["optimization_strategies"].append(
                f"【学习优化】活跃目标'{active_goal}'已执行{duration_hours:.0f}小时，建议："
                "评估学习效果→决定是否切换目标→补充学习资源→验证学习成果"
            )
        
        return diagnosis
    def get_dynamic_state_as_knowledge(self, node_pool=None, info_field=None) -> list[dict[str, Any]]:
        """
        【v12.0新增】将动态状态转化为可以写入知识库的L2/L3节点。
        
        这样曈曈就能将"我此刻的状态"作为知识进行检索和推理。
        
        Returns:
            可写入知识库的节点列表
        """
        report = self.get_dynamic_state_report(node_pool=node_pool, info_field=info_field)
        knowledge_nodes = []
        
        # 1. 知识状态节点
        k = report.get("knowledge", {})
        if k:
            knowledge_nodes.append({
                "value": (
                    f"[自我状态] 当前知识体系共有{k.get('total_nodes', 0)}个节点："
                    f"L1感知节点{k.get('L1_count', 0)}个、"
                    f"L2认知节点{k.get('L2_count', 0)}个、"
                    f"L3智慧节点{k.get('L3_count', 0)}个、"
                    f"L4本能节点{k.get('instinct_count', 0)}个。"
                ),
                "keywords": ["自我状态", "知识节点", "L1", "L2", "L3", "L4"],
                "space_path": "/自我/状态/知识",
                "importance": "A",
                "view_mode": "INNER_VIEW",
                "trust_score": 95.0,
            })
        
        # 2. 系统运行状态节点
        r = report.get("resources", {})
        if r and "error" not in r:
            knowledge_nodes.append({
                "value": (
                    f"[自我状态] 系统当前处于{r.get('load_level', '未知')}负载状态，"
                    f"信息场有{r.get('active_conditions', 0)}个活跃条件，"
                    f"四层线程池（{', '.join(r.get('layer_pools', []))}）正常运行。"
                ),
                "keywords": ["自我状态", "系统负载", "信息场", "线程池"],
                "space_path": "/自我/状态/系统",
                "importance": "A",
                "view_mode": "INNER_VIEW",
                "trust_score": 95.0,
            })
        
        # 3. 健康状态节点
        h = report.get("health", "")
        if h:
            knowledge_nodes.append({
                "value": f"[自我状态] 综合健康评估：{h}。",
                "keywords": ["自我状态", "健康评估"],
                "space_path": "/自我/状态/健康",
                "importance": "A",
                "view_mode": "INNER_VIEW",
                "trust_score": 95.0,
            })
        
        return knowledge_nodes


    # ========== ★P1 新增：全库扫描检测器 ==========

    def _detect_dead_code(self) -> list[dict[str, Any]]:
        """死代码检测：扫描全库公共方法，找出无外部调用的方法。

        ★9-问题3修复（2026-09-06）：原实现为 O(方法数 × 全库字符数) 的双重循环——
        对 **每个** 方法名都跑 2 次全库正则（r'\\.name\\s*\\(' 与 r'[^\\w.]name\\s*\\('）。
        生产实测规模：
            231 个 py 文件 / 5.5M 字符 / 1195 个方法名
            → 2390 次全量正则，累计扫描 **13.2G 字符**，单次调用耗时 ~9 分钟
            → detect_code_issues() 整体 417 个问题中 362 个来自本检测器（87%）。
        更致命的是这 362 条 severity 恒为 'low'，而自主进化链路
        （EvolutionLoop.discover_all_issues:170-173）只收 high/medium，
        **100% 会被过滤丢弃** —— 即：花 9 分钟算出一批注定被丢掉的结果，
        还顺带把真正要修的 20 个 high/medium 问题堵在后面。
        （生产表现：进化循环每轮卡在 detect_code_issues 近 10 分钟，
         且日志只见「发现 20 个 / 处理 10 个」，修复吞吐长期上不去。）

        修复分两层：
          ① 算法层：改为 **单次遍历** 建立调用名计数表，O(全库字符数)，
             与原双重循环语义等价（见下方 _CALL_SITE_RE 注释）；
          ② 接口层：新增 skip_detectors 参数，让进化链路可直接跳过本检测器。

        Returns:
            死代码问题列表（severity 恒为 low）。
        """
        import glob as _glob
        import re as _re
        _issues: list[dict[str, Any]] = []
        _py_files = _glob.glob('**/*.py', recursive=True)
        _py_files = [f for f in _py_files
                     if 'test' not in f.lower() and 'tmp' not in f.lower()
                     and not _issue_file_in_backup_dir(f)]
        _methods: dict[str, list[tuple[str, int]]] = {}
        # ★单次读取：原实现为「找方法」和「拼全库代码」各遍历读一遍全部文件（2×IO），
        #   这里合并为一次，文件内容复用。
        _contents: dict[str, str] = {}
        for _f in _py_files:
            try:
                with open(_f, encoding='utf-8', errors='ignore') as _fobj:
                    _content = _fobj.read()
            except Exception:
                continue
            _contents[_f] = _content
            for _m in _re.finditer(r'^\s*def\s+(\w+)\s*\(', _content, _re.MULTILINE):
                _name = _m.group(1)
                if _name.startswith(('_', '__')):
                    continue
                if _name in ('main', 'run', 'start', 'stop', 'setup', 'teardown'):
                    continue
                _methods.setdefault(_name, []).append((_f, _content[:_m.start()].count('\n') + 1))

        # ★O(N) 调用点统计：一次扫描全库，把所有调用点的名字计入 call_counts。
        #   语义对齐原实现的两次 findall：
        #     - 原规则一 r'\.'    + name + r'\s*\('  → 调用点前缀恰为 '.'（obj.name(）
        #     - 原规则二 r'[^\w.]' + name + r'\s*\('  → 前缀是非单词字符且非 '.'（如空格、'(' ）
        #   两条互斥（'.' 被 [^\w.] 明确排除），故不会重复计数；
        #   两条规则都要求**存在**一个前导字符，故文件起始处的调用同样不计入。
        #
        #   ★关键：必须用零宽断言（lookbehind）检查前导字符，而不能用捕获组捕获它。
        #     首版实现误用 r'([.\W]?)(\w+)\s*\(' —— 捕获组会**消耗**前导字符，
        #     而 finditer 的匹配互不重叠，于是嵌套/紧邻调用（如 'foo(bar('）中，
        #     外层匹配 'foo(' 把 '(' 吃掉后，内层的 'bar' 再也拿不到前导字符，
        #     该调用点被漏计 → 误判为死代码。
        #     生产实测因此多报 5 个假死代码（on_user_presence、scan_*_knowledge 等）。
        #     lookbehind 只检查不消耗，被前一个匹配吃掉的字符仍可被正确读取。
        _CALL_SITE_RE = _re.compile(r'(?:(?<=\.)|(?<=[^\w.]))(\w+)\s*\(')
        _call_counts: dict[str, int] = {}
        for _content in _contents.values():
            for _m in _CALL_SITE_RE.finditer(_content):
                _name = _m.group(1)
                _call_counts[_name] = _call_counts.get(_name, 0) + 1

        for _name, _defs in _methods.items():
            _call_count = _call_counts.get(_name, 0)
            _def_count = len(_defs)
            if _call_count <= _def_count:
                for _f, _line in _defs[:1]:
                    _issues.append({
                        'file': _f, 'organ': 'global', 'method': _name, 'line': _line,
                        'type': 'dead_code', 'severity': 'low',
                        'description': f'方法 {_name} 可能无外部调用（死代码）',
                        'suggestion': '确认是否需要保留，如不需要可删除以减少代码体积',
                    })
        return _issues

    def _detect_unused_imports(self) -> list[dict[str, Any]]:
        """未使用导入/变量检测：调用 RUFF 检查 F401/F841。"""
        import subprocess as _sp
        _issues: list[dict[str, Any]] = []
        try:
            _proc = _sp.run(
                ['python', '-m', 'ruff', 'check', '--select', 'F401,F841', '.'],
                capture_output=True, text=True, timeout=TIMEOUT_CONFIG["llm_call"], check=False,
                encoding='utf-8', errors='replace', cwd=self._project_root,
            )
            _out = (_proc.stdout or '') + (_proc.stderr or '')
            for _line in _out.splitlines():
                if ': F401 ' in _line or ': F841 ' in _line:
                    _parts = _line.split(':')
                    if len(_parts) >= 3:
                        _f = _parts[0].strip()
                        _ln = int(_parts[1]) if _parts[1].isdigit() else 0
                        _code = 'F401' if 'F401' in _line else 'F841'
                        _msg = _line.split(_code)[-1].strip() if _code in _line else ''
                        _issues.append({
                            'file': _f, 'organ': 'global', 'method': '', 'line': _ln,
                            'type': f'unused_{_code.lower()}', 'severity': 'low',
                            'description': f'{_code}: {_msg}',
                            'suggestion': '删除未使用的导入或变量',
                        })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _issues[:20]

    def _detect_config_audit(self) -> list[dict[str, Any]]:
        """配置开关审计：扫描 config.py 中长期为 False 的功能开关。"""
        import os as _os
        _issues: list[dict[str, Any]] = []
        _config_path = _os.path.join(self._project_root, 'config.py')
        if not _os.path.exists(_config_path):
            return _issues
        try:
            with open(_config_path, encoding='utf-8', errors='ignore') as _fobj:
                _content = _fobj.read()
        except Exception:
            return _issues
        import re as _re
        for _m in _re.finditer(r'["\'](\w+_enabled|\w+_active|\w+_on)["\']\s*[:=]\s*False', _content):
            _name = _m.group(1)
            _line = _content[:_m.start()].count('\n') + 1
            _issues.append({
                'file': 'config.py', 'organ': 'config', 'method': '', 'line': _line,
                'type': 'config_disabled', 'severity': 'info',
                'description': f'功能开关 {_name} 当前为 False',
                'suggestion': '评估该功能是否已就绪，如已就绪可考虑启用',
            })
        return _issues


# 注：threading 已提升至文件顶部导入（__init__ 需要用到锁），此处不再重复导入。

_self_inspector: SelfInspector | None = None
_self_inspector_lock = threading.Lock()

def get_self_inspector() -> SelfInspector:
    """获取SelfInspector单例"""
    global _self_inspector
    if _self_inspector is None:
        with _self_inspector_lock:
            if _self_inspector is None:
                _self_inspector = SelfInspector()
    return _self_inspector


def shutdown_self_inspector() -> None:
    """★P0批次3：复位 SelfInspector 单例，满足器官零状态（规则4）。

    原停机流程未清理该全局单例，重启时会复用带残留分析缓存的旧实例。
    此处显式置空，使下次获取重建全新零状态实例。
    """
    global _self_inspector
    _inst = _self_inspector
    _self_inspector = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as _se:
                silent_exc(_se, "self_inspector.py:3966")
# ========== 自测 ==========
if __name__ == "__main__":
    print("=== SelfInspector v9.5 自测 ===\n")
    
    inspector = SelfInspector()
    
    print("1. 公开信息:")
    print(inspector.get_self_description("public")[:300])
    
    print("\n2. 受限信息:")
    desc = inspector.get_self_description("restricted")
    print(desc[:400])
    
    print("\n3. 器官数量:")
    print(f"   {inspector.get_info_by_field('organ_count')}个器官")
    
    print("\n4. 器官分类:")
    print(f"   {inspector.get_info_by_field('organ_categories')}")
    
    print("\n=== 自测完成 ===")
# _m69_t3b_inspector
