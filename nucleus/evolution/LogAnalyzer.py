# -*- coding: utf-8 -*-
"""
LogAnalyzer.py —— 日志分析器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 运行日志深度分析与问题挖掘
机制: 基于LogAnalyzer类实现，包含10个核心方法
定位: 进化监测层
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底

# ★343刀2⑤：两套文件域声明只打一次（进程级），避免每拍刷屏
_M343_DOMAIN_DECLARED = False

# 匹配 Traceback 的 File 行:  File "/path/to/file.py", line 123, in func_name
_TRACEBACK_FILE_RE = re.compile(
    r'File "(?P<file>[^"]+\.py)", line (?P<line>\d+), in (?P<func>\w+)'
)
# 匹配 ERROR/CRITICAL 日志行中的器官名: [器官名] [ERROR] 或 [器官名] ERROR
_LOG_ORGAN_RE = re.compile(r'\[([^\]]+)\]\s*(?:\[)?(ERROR|CRITICAL|WARNING|Traceback|Exception)')
# 匹配时间戳前缀（用于提取时间）
_LOG_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})')
# ★主线第29批 T4/P2-176：日志行「真实级别标记」提取 —— 替代子串匹配。
#   背景：原实现用 `"ERROR" in _upper` 子串匹配，导致消息正文含 "JSONDecodeError"
#   （胃的 JSON 解析失败 WARNING 共 45 次）等字样的行被误判为 ERROR。
#   兼容两种格式：`[器官] LEVEL: msg` 与 `[器官] [LEVEL] msg`。
_LOG_LEVEL_MARKER_RE = re.compile(
    r'\[([^\]]+)\]\s*\[?(DEBUG|INFO|WARNING|ERROR|CRITICAL|TRACE)\]?\s*[:：]?'
)


# ★第90批 相关任务：日志定位修复开关（bug#1 message 时机 + 有界重试 / bug#4 末帧）。
#   开启（默认）→ 与修复同批的所有行为；关闭 → 逐字回到第90批前。
#   默认值内联本模块（遵守红线「不改 config.py 开关」）。
def _m90_log_locate_v2_on() -> bool:
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M90_LOG_LOCATE_V2", True))
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.LogAnalyzer::_m90_log_locate_v2_on L49")
        return True


def is_error_level_line(line: str) -> bool:
    """该行是否为**错误级**日志（ERROR / CRITICAL）。

    ★主线第30批 T2：供各处「日志文件级别统计」共用，替代 `"ERROR" in line`
    子串匹配（后者会被消息正文里的 `JSONDecodeError` / `ERROR消息` 等字样命中）。

    判定顺序：
      1) 有真实级别标记 → 按标记判定（WARNING/INFO/DEBUG 一律不算错误）；
      2) 无标记（旧格式 / 裸堆栈）→ 回退原有宽松子串判定，**保持向后兼容**。

    参数:
        line: 单行日志文本（可含前导时间戳与 `[器官]` 前缀；None 按空串处理）。

    返回:
        True  = 该行属于错误级（ERROR / CRITICAL）；
        False = 其他级别，或无法判定。

    示例:
        is_error_level_line("[胃] WARNING: JSONDecodeError: x")  -> False
        is_error_level_line("[肺] ERROR: 渠道不可用")             -> True
        is_error_level_line("Traceback (most recent call last):") -> True  # 无标记→宽松判定
    """
    _lv = extract_log_level(line)
    if _lv:
        return _lv in ("ERROR", "CRITICAL")
    _u = (line or "").upper()
    return "ERROR" in _u or "CRITICAL" in _u


def extract_log_level(line: str) -> str:
    """从日志行提取「真实级别标记」（大写）；无标记返回空串。

    ★主线第29批 T4/P2-176：供 LogAnalyzer / HealthScore 共享，
    保证日志级别判定口径一致，避免各模块各写一套子串匹配。

    参数:
        line: 单行日志文本（None 按空串处理）。

    返回:
        大写级别名（"DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL" / "TRACE"）；
        行内无 `[器官] LEVEL:` 或 `[器官] [LEVEL]` 形式标记时返回空串 ""。

    示例:
        extract_log_level("[肺] INFO: 渠道池就绪")   -> "INFO"
        extract_log_level("裸堆栈行，无级别标记")     -> ""
    """
    _m = _LOG_LEVEL_MARKER_RE.search(line or "")
    return _m.group(2).upper() if _m else ""


class LogAnalyzer(SilentLogMixin):
    """运行日志动态归因分析器。"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        # ★343刀2：域外 Traceback 计数桶（键 = "文件:行"，值 = {n, why}）
        self._m343_skipped: dict[str, dict[str, Any]] = {}
        # ★343刀2⑤：两套文件域显式声明（进程内一次；失败不阻断分析）
        try:
            self._m343_domain_rel = self._m343_declare_two_domains()
        except Exception as _m343d_e:                            # noqa: BLE001
            self._m343_domain_rel = {"error": type(_m343d_e).__name__}
            silent_exc(_m343d_e, where="LogAnalyzer::__init__ 343两域声明")

    def _default_log_file(self) -> str:
        return os.path.join(self._project_root, "logs", "pulse.log")

    # ========== 主入口 ==========

    def analyze(self, log_file: str | None = None,
                include_runtime_metrics: bool = True,
                max_issues: int = 30) -> dict[str, Any]:
        """
        分析运行日志，返回动态归因的问题定位清单。

        返回:
            {
                "issues": [IssueLocation, ...],
                "summary": {"tracebacks": N, "errors": N, "criticals": N,
                            "unique_locations": N, "top_issue": ...},
                "total_lines": N,
            }
        """
        _log_file = log_file or self._default_log_file()
        _issues: dict[str, dict[str, Any]] = {}

        if os.path.exists(_log_file):
            self._scan_log_file(_log_file, _issues)

        if include_runtime_metrics:
            self._scan_runtime_metrics(_issues)

        # 按出现次数降序排序，输出 top issues
        _sorted = sorted(_issues.values(), key=lambda x: (-x["count"], x.get("last_seen", 0)))
        _sorted = _sorted[:max_issues]

        return {
            "issues": _sorted,
            "summary": self._summarize(_sorted, _issues),
            "total_lines": self._count_lines(_log_file),
        }

    # ========== 日志扫描 ==========

    # ===== ★343批刀2（E-10）：Traceback 命中的**域过滤** =====
    #   为什么必须在这里做：`_scan_log_file` 是"日志 → 补丁候选"的**唯一入口**，
    #   过滤放在下游（生成侧/入池侧）都晚一步 —— 垃圾条目一旦入池，就会占掉
    #   入池速率额度（O3 真门槛 1 条/天）并污染待修池读数。
    @staticmethod
    def _m343_domain_roots() -> list:
        """域白名单：config 显式配置优先，留空回退 `TOOL_SCAN_ROOTS`（同口径）。"""
        try:
            import config as _c343
            _r = list(getattr(_c343, "LOG_SCAN_DOMAIN_ROOTS", []) or [])
            if _r:
                return [str(x) for x in _r]
            return [str(x) for x in (getattr(_c343, "TOOL_SCAN_ROOTS", []) or [])]
        except Exception as _e:
            silent_exc(_e, where="LogAnalyzer::_m343_domain_roots")
            return []

    @staticmethod
    def _m343_filter_on() -> bool:
        """域过滤开关（`LOG_SCAN_DOMAIN_FILTER`，默认 True）。

        读不到配置 ⇒ True：A-17 的代价是"垃圾进池污染账"，
        关掉过滤的收益只有"多收几条日志噪声"——两头不对称，fail-closed。
        """
        try:
            import config as _c343
            return bool(getattr(_c343, "LOG_SCAN_DOMAIN_FILTER", True))
        except Exception as _e:
            silent_exc(_e, where="LogAnalyzer::_m343_filter_on")
            return True

    # ===== ★343刀2⑤（342挂账45）：**两套文件域并存**必须显式声明 =====
    #   342 实证：本仓有两套"代码面"集合，各自服务不同目的，且**不相等**：
    #     · `config.TOOL_SCAN_ROOTS`（11 项）= **扫描域**：Ruff/Bandit/日志分析
    #       愿意去看哪些路径（"看得见"）；
    #     · `M339_INTAKE_PATH_WHITELIST` / `_M339_WL_DEFAULT`（6 项）= **入池域**：
    #       判据允许哪些路径变成补丁候选（"能落地"）。
    #   差集 = 只扫不入池 6 项 + 只入池不被扫 1 项（`scripts/`）。
    #   风险不是"泄漏"，而是**"扫到了却没下文"的静默**：一条 functions/ 下的
    #   Traceback 会被日志分析命中，却在入池口被丢掉，两边都不报错。
    @staticmethod
    def _m343_two_domains() -> dict:
        """计算两套域的显式关系。返回 both / only_tool / only_wl 三个集合。

        归一化：去尾 `/`、反斜杠转 `/`、去 `./` 前缀后按**根名**比对。
        读不到入池域 ⇒ `only_wl` 记空 + `wl_source="unavailable"`（不猜、不填 0）。
        """
        def _norm(_xs):
            _out = []
            for _x in (_xs or []):
                _t = str(_x).replace("\\", "/").strip().lstrip("./")
                if not _t:
                    continue
                _t = _t.rstrip("/")
                if _t:
                    _out.append(_t)
            return _out

        _tool, _wl, _wl_src = [], [], "unavailable"
        try:
            import config as _c343d
            _tool = _norm(getattr(_c343d, "TOOL_SCAN_ROOTS", []) or [])
            _wlv = getattr(_c343d, "M339_INTAKE_PATH_WHITELIST", None)
            if _wlv:
                _wl, _wl_src = _norm(_wlv), "config"
        except Exception as _e:                                  # noqa: BLE001
            silent_exc(_e, where="LogAnalyzer::_m343_two_domains config")
        if _wl_src == "unavailable":
            try:
                from nucleus.self_inspector import _M339_WL_DEFAULT as _wld
                _wl, _wl_src = _norm(_wld), "self_inspector"
            except Exception as _e:                              # noqa: BLE001
                silent_exc(_e, where="LogAnalyzer::_m343_two_domains wl")
        _st, _sw = set(_tool), set(_wl)
        return {
            "tool_roots": _tool, "intake_wl": _wl, "wl_source": _wl_src,
            "both": sorted(_st & _sw),
            "only_tool": sorted(_st - _sw),
            "only_wl": sorted(_sw - _st),
            "n_tool": len(_tool), "n_wl": len(_wl),
        }

    def _m343_declare_two_domains(self) -> dict:
        """逐根打一行 `domain=` 留痕（进程内只打一次）。返回关系表。"""
        global _M343_DOMAIN_DECLARED
        _rel = self._m343_two_domains()
        if _M343_DOMAIN_DECLARED:
            return _rel
        _M343_DOMAIN_DECLARED = True
        _rows = ([(x, "both") for x in _rel["both"]]
                 + [(x, "tool_roots") for x in _rel["only_tool"]]
                 + [(x, "intake_wl") for x in _rel["only_wl"]])
        for _root, _dom in sorted(_rows, key=lambda t: (t[1], t[0])):
            if _dom == "both":
                _note = "两域同在：既被扫描也可能入池"
            elif _dom == "tool_roots":
                _note = "只在扫描域 ⇒ 日志能命中但**入池口会丢**（不是泄漏，是静默）"
            else:
                _note = "只在入池域 ⇒ 入池口允许但**扫描面看不到**（无源可入）"
            self._log(LogLevel.INFO,
                      "[343域过滤] domain=%s root=%s note=%s"
                      % (_dom, _root, _note))
        self._log(LogLevel.INFO,
                  "[343域过滤] 两域声明 tool_roots=%d intake_wl=%d both=%d "
                  "only_tool=%d only_wl=%d wl_source=%s"
                  "（TOOL_SCAN_ROOTS=扫描域 · M339入池白名单=入池域，**不是同一套**）"
                  % (_rel["n_tool"], _rel["n_wl"], len(_rel["both"]),
                     len(_rel["only_tool"]), len(_rel["only_wl"]),
                     _rel["wl_source"]))
        return _rel

    def _m343_in_domain(self, file_path: str) -> tuple:
        """该 Traceback 文件是否在**判据域**内。返回 `(bool, reason)`。

        归一化：盘符/绝对前缀/反斜杠全部拉平成本仓库内的相对路径再判。
        `reason` 取值：`ok` / `disabled` / `empty_roots` / `backup_copy` /
        `deny_dir:<段>` / `not_in_roots` / `outside_root`。
        """
        if not self._m343_filter_on():
            return True, "disabled"
        _roots = self._m343_domain_roots()
        if not _roots:
            # 白名单空 = 判据不可用。**不默认全放行**（否则过滤形同虚设），
            # 也不默认全拦（那会把真实缺陷挡在门外）⇒ 放行但显式报原因。
            return True, "empty_roots"
        _f = str(file_path or "").replace("\\", "/").strip()
        if not _f:
            return False, "not_in_roots"
        if ".bak" in _f.lower():
            return False, "backup_copy"
        _rel = _f
        try:
            _root = os.path.abspath(self._project_root).replace("\\", "/")
            _abs = os.path.abspath(_f).replace("\\", "/")
            if _abs.lower().startswith(_root.lower() + "/"):
                _rel = _abs[len(_root) + 1:]
            elif os.path.isabs(_f):
                return False, "outside_root"
        except (OSError, ValueError) as _e:
            # 路径归一失败不致命：退回原串继续判域，但要留痕（否则域过滤判定不可追溯）
            silent_exc(_e, where="LogAnalyzer::_m343_in_domain abspath")
            _rel = _f
        _segs = [s for s in _rel.split("/") if s and s not in (".",)]
        if not _segs:
            return False, "not_in_roots"
        try:
            import config as _c343d
            _deny = [str(x) for x in (getattr(_c343d, "LOG_SCAN_DOMAIN_DENY", []) or [])]
        except Exception as _e:
            silent_exc(_e, where="LogAnalyzer::_m343_in_domain deny")
            _deny = ["tmp", "tests"]
        _low = [s.lower() for s in _segs]
        for _d in _deny:
            if _d.lower() in _low:
                return False, "deny_dir:%s" % _d
        _head = _segs[0]
        if _head in _roots:
            return True, "ok"
        if len(_segs) == 1 and _head in _roots:
            return True, "ok"
        return False, "not_in_roots"

    def _m343_note_out_domain(self, file_path: str, line_no: int, why: str) -> None:
        """域外 Traceback 留痕（按 (文件,行) 去重，避免刷屏；汇总行在扫描末尾打）。"""
        _key = "%s:%s" % (file_path, line_no)
        if _key in self._m343_skipped:
            self._m343_skipped[_key]["n"] += 1
            return
        self._m343_skipped[_key] = {"n": 1, "why": why}
        self._log(LogLevel.INFO,
                  "[343域过滤] 跳过域外Traceback: %s %s why=%s"
                  "（不建补丁：tmp/测试/备份/第三方崩溃不属于生产缺陷）"
                  % (file_path, line_no, why))

    def _scan_log_file(self, log_file: str, issues: dict[str, dict[str, Any]]) -> None:
        """扫描日志文件，提取 Traceback/ERROR/CRITICAL 的精确位置。"""
        _context_buf: list[str] = []  # 最近几行，用于 Traceback 上下文
        _pending_traceback = False

        try:
            with open(log_file, encoding="utf-8", errors="ignore") as _f:
                for _line in _f:
                    _line = _line.rstrip("\n")
                    _ts = self._extract_ts(_line)

                    # Traceback 的 File 行：精准定位
                    _m = _TRACEBACK_FILE_RE.search(_line)
                    if _m:
                        _file = _m.group("file")
                        _line_no = int(_m.group("line"))
                        _func = _m.group("func")
                        # ★343刀2：先判域 —— 域外直接跳过（不建补丁、留痕）。
                        _in_dom, _dom_why = self._m343_in_domain(_file)
                        if not _in_dom:
                            self._m343_note_out_domain(_file, _line_no, _dom_why)
                            _pending_traceback = True
                            _context_buf.append(_line)
                            if len(_context_buf) > 20:
                                _context_buf.pop(0)
                            continue
                        _key = f"{_file}:{_line_no}:{_func}"
                        _issue = self._get_or_create(issues, _key, _file, _line_no, _func, "Traceback")
                        _issue["count"] += 1
                        _issue["last_seen"] = _ts or time.time()
                        if not _issue["message"]:
                            _issue["message"] = f"Traceback 定位到 {os.path.basename(_file)}:{_line_no} 方法 {_func}"
                        _issue["related_lines"] = list(_context_buf[-3:])
                        _pending_traceback = True

                    # ERROR/CRITICAL 日志行
                    _upper = _line.upper()
                    # ★主线第29批 T4/P2-176：先按「真实级别标记」判定 —— 明确标注为
                    #   WARNING/INFO/DEBUG/TRACE 的行直接跳过，即便消息正文含 "ERROR" 字样。
                    _real_level = extract_log_level(_line)
                    if _real_level and _real_level not in ("ERROR", "CRITICAL"):
                        _context_buf.append(_line)
                        if len(_context_buf) > 20:
                            _context_buf.pop(0)
                        continue
                    if "ERROR" in _upper or "CRITICAL" in _upper:
                        # ★修复: 跳过 Traceback 上下文中的非 ERROR 行（如 raise XXX / ValueError:）
                        #   它们不含 ERROR/CRITICAL 关键字却被误判，实际是 Traceback 的堆栈内容。
                        _organ_m = _LOG_ORGAN_RE.search(_line)
                        _organ = _organ_m.group(1) if _organ_m else "未知"
                        # ★真实级别优先；无标记时回退原子串判定（向后兼容旧格式日志）
                        _level = _real_level if _real_level in ("ERROR", "CRITICAL") else (
                            "CRITICAL" if "CRITICAL" in _upper else "ERROR")
                        # 仅当行首是器官标记（[器官]）或含明确的 ERROR/CRITICAL 标记时才作为独立错误
                        _is_real_error_line = bool(_organ_m) and _organ_m.group(1) not in ("未知", "")
                        if not _is_real_error_line:
                            _context_buf.append(_line)
                            if len(_context_buf) > 20:
                                _context_buf.pop(0)
                            continue
                        # ★修复: 聚合 key 去掉时间戳，避免同一错误因时间不同被拆成多条
                        _msg_key = re.sub(r'^\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}\s*', '', _line.strip())[:80]
                        _key = f"log:{_organ}:{_level}:{_msg_key}"
                        _issue = self._get_or_create(issues, _key, "", 0, "", _level)
                        _issue["count"] += 1
                        _issue["last_seen"] = _ts or time.time()
                        _issue["organ"] = _organ
                        # ★第90批 相关任务 bug#1（上半）：message 必须在定位**之前**填充。
                        #   改前 message 在 locate 之后两行才赋值 ⇒ 首次定位调用的是
                        #   `_locate_for_issue(organ, "")`，而
                        #   `guess_method_from_message("")` 恒返回空方法名 ⇒ 即便文件
                        #   定位成功也拿不到行号，且置信度被 -0.2 惩罚。
                        if _m90_log_locate_v2_on() and not _issue["message"]:
                            _issue["message"] = _line.strip()[:200]
                        # ★P0-7 位置补全：普通 ERROR 行不带 file/method/line，
                        #   SafeEvolutionExecutor 拿不到位置就无法生成补丁。
                        #   此处用 self_inspector 从「器官名 + 错误消息」反推。
                        #   ★第90批 相关任务 bug#1（下半）：由「试一次即永久封死」
                        #     改为「有界重试」（message 细化后可再试，总次数 ≤
                        #     _LOCATE_MAX_ATTEMPTS）；开关关闭→旧判据。
                        if self._m90_should_locate(_issue):
                            self._m90_mark_located(_issue)
                            self._m90_apply_location(
                                _issue, self._locate_for_issue(_organ, _issue["message"]))
                        # ★兼容旧路径：message 兜底填充（v2 开启时此处恒为空操作的
                        #   分支已被上面提前填过；关闭时即改前行为）。
                        if not _issue["message"]:
                            _issue["message"] = _line.strip()[:200]

                    _context_buf.append(_line)
                    if len(_context_buf) > 20:
                        _context_buf.pop(0)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.LogAnalyzer::_scan_log_file L232")
        # ★343刀2：域过滤汇总行（可 grep；`跳过=0` 也是一条**事实**，
        #   能区分「没有域外崩溃」与「过滤没生效」——铁律166）。
        try:
            _sk = self._m343_skipped
            if _sk:
                _n = sum(v["n"] for v in _sk.values())
                _why = {}
                for _v in _sk.values():
                    _why[_v["why"]] = _why.get(_v["why"], 0) + _v["n"]
                self._log(LogLevel.INFO,
                          "[343域过滤] 汇总 跳过=%d 唯一位置=%d 域内问题=%d 归因=%s"
                          % (_n, len(_sk), len(issues), _why))
            else:
                self._log(LogLevel.DEBUG,
                          "[343域过滤] 汇总 跳过=0 域内问题=%d（本次扫描未见域外 Traceback）"
                          % len(issues))
        except Exception as _se:
            silent_exc(_se, where="LogAnalyzer::_scan_log_file 343域过滤汇总")

    def _scan_runtime_metrics(self, issues: dict[str, dict[str, Any]]) -> None:
        """从 runtime_metrics 的 error_snapshots 提取异常现场，关联代码位置。"""
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _rt = get_runtime_metrics()
            _snap = _rt.get_snapshot()
            for _err in _snap.get("error_snapshots", []):
                _tb = _err.get("traceback", "")
                # ★第90批 相关任务 bug#4：Traceback 取**最后一帧**（= 致错点）。
                #   改前用 `.search()` 取首帧 = **最外层调用者**
                #   （实测嵌套三层时得到 `_level1`，真凶是 `_level3`），
                #   ⇒ 定位到的位置永远停在调用链顶端。
                #   开关关闭 → 逐字回到 `.search()` 首帧行为。
                _frames = _TRACEBACK_FILE_RE.findall(_tb)
                if not _frames:
                    continue
                _fr = _frames[0] if not _m90_log_locate_v2_on() else _frames[-1]
                _file, _line_no, _func = _fr[0], int(_fr[1]), _fr[2]
                _key = f"rt:{_file}:{_line_no}:{_func}"
                _issue = self._get_or_create(issues, _key, _file, _line_no, _func, "Exception")
                _issue["count"] += 1
                _issue["last_seen"] = _err.get("timestamp", time.time())
                if not _issue["message"]:
                    _issue["message"] = _err.get("error", "")[:200]
                _issue["pulse_type"] = _err.get("pulse_type", "")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 工具方法 ==========

    # ★P0-7：定位置信度低于此值的问题标记为"需人工确认"，不进自动修复队列
    _LOCATE_CONF_THRESHOLD = 0.5
    # ★第90批 相关任务 bug#1：定位重试上限（同一条 message 只试一次；
    #   message 被细化后可再试一次）。改前是「试过一次即永久封死」。
    _LOCATE_MAX_ATTEMPTS = 2

    def _m90_should_locate(self, issue: dict[str, Any]) -> bool:
        """此刻是否应尝试定位（第90批 相关任务 bug#1 修）。

        改前：`not file_path and not _locate_attempted` —— 一旦试过就永久封死，
        而首次尝试时 message 还是空串（见 `_scan_log_file` 的填入时机）⇒
        该问题整个生命周期内定位机会为 0。

        改后（有界重试）：
          · 已定位到文件 → 不再试；
          · message 为空 → 不试（无从下手，等 message 填充后再试）；
          · 尝试次数 ≥ `_LOCATE_MAX_ATTEMPTS` → 不再试；
          · 上次失败且 message 与上次**完全相同** → 不再试（避免纯重复开销）。
        开关关闭 → 逐字回到改前判据。
        """
        if issue.get("file_path"):
            return False
        if not _m90_log_locate_v2_on():
            return not issue.get("_locate_attempted")
        _msg = str(issue.get("message", "") or "")
        if not _msg:
            return False
        if int(issue.get("_locate_attempts", 0) or 0) >= self._LOCATE_MAX_ATTEMPTS:
            return False
        #   上次失败且 message 未变化 → 纯重复，不再试（SIM103：直接返回布尔）
        return issue.get("_locate_tried_msg") != _msg

    def _m90_mark_located(self, issue: dict[str, Any]) -> None:
        """登记一次定位尝试（次数 + 本次所用 message），供有界重试判据使用。"""
        issue["_locate_attempted"] = True     # 兼容既有字段（外部只读语义不变）
        issue["_locate_attempts"] = int(issue.get("_locate_attempts", 0) or 0) + 1
        issue["_locate_tried_msg"] = str(issue.get("message", "") or "")

    def _m90_apply_location(self, issue: dict[str, Any],
                            loc: dict[str, Any] | None) -> None:
        """把定位结果写回问题记录（纯抽取，语义与改动前逐字一致）。"""
        if not loc or not loc.get("file"):
            return
        issue["file_path"] = loc["file"]
        issue["line"] = loc.get("line", 0)
        issue["method"] = loc.get("method", "")
        issue["locate_confidence"] = loc.get("confidence", 0.0)
        issue["needs_human_confirm"] = (
            loc.get("confidence", 0.0) < self._LOCATE_CONF_THRESHOLD)


    def _get_inspector(self):
        """惰性获取 SelfInspector（仅在需要补全位置时创建，避免拖慢常规扫描）。

        Returns:
            SelfInspector 实例；获取失败返回 None（并用 False 标记避免重复尝试）
        """
        inst = getattr(self, "_inspector", None)
        if inst is not None:
            return inst or None
        try:
            from nucleus.self_inspector import SelfInspector
            inst = SelfInspector()
            self._inspector = inst
        except Exception as e:
            self._log(LogLevel.WARNING,
                      f"[LogAnalyzer] SelfInspector 不可用，跳过位置补全: "
                      f"{type(e).__name__}: {e}")
            self._inspector = False
            inst = False
        return inst or None

    def _locate_for_issue(self, organ: str, msg: str) -> dict[str, Any] | None:
        """用 self_inspector 从「器官名 + 错误消息」反推代码位置。"""
        ins = self._get_inspector()
        if not ins or not hasattr(ins, "locate_issue"):
            return None
        try:
            return ins.locate_issue(organ, msg, "")
        except Exception as e:
            self._log(LogLevel.WARNING,
                      f"[LogAnalyzer] 问题定位失败: {type(e).__name__}: {e}")
            return None

    def _get_or_create(self, issues: dict[str, dict[str, Any]], key: str,
                       file_path: str, line: int, method: str,
                       error_type: str) -> dict[str, Any]:
        if key not in issues:
            issues[key] = {
                "file_path": file_path,
                "line": line,
                "method": method,
                "error_type": error_type,
                "message": "",
                "count": 0,
                "first_seen": time.time(),
                "last_seen": 0,
                "organ": "",
                "pulse_type": "",
                "related_lines": [],
                # ★P0-7 位置补全
                "locate_confidence": 0.0,
                "needs_human_confirm": False,
                "_locate_attempted": False,
                "_locate_attempts": 0,
                "_locate_tried_msg": "",
            }
        return issues[key]

    def _extract_ts(self, line: str) -> float:
        _m = _LOG_TS_RE.match(line)
        if not _m:
            return 0.0
        try:
            import datetime
            _ts_str = _m.group(1).replace("T", " ")
            _dt = datetime.datetime.strptime(_ts_str, "%Y-%m-%d %H:%M:%S")  # 日志时间戳为本地时间
            return _dt.timestamp()
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.LogAnalyzer::_extract_ts L383")
            return 0.0

    def _count_lines(self, log_file: str) -> int:
        if not os.path.exists(log_file):
            return 0
        try:
            with open(log_file, encoding="utf-8", errors="ignore") as _f:
                return sum(1 for _ in _f)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.LogAnalyzer::_count_lines L392")
            return 0

    def _summarize(self, sorted_issues: list[dict[str, Any]],
                   all_issues: dict[str, dict[str, Any]]) -> dict[str, Any]:
        _tracebacks = sum(1 for _i in all_issues.values() if _i["error_type"] == "Traceback")
        _errors = sum(1 for _i in all_issues.values() if _i["error_type"] == "ERROR")
        _criticals = sum(1 for _i in all_issues.values() if _i["error_type"] == "CRITICAL")
        return {
            "tracebacks": _tracebacks,
            "errors": _errors,
            "criticals": _criticals,
            "unique_locations": len(all_issues),
            "top_issue": sorted_issues[0] if sorted_issues else None,
        }


# ========== 便捷函数 ==========

def analyze_logs(project_root: str, log_file: str | None = None,
                 max_issues: int = 30) -> dict[str, Any]:
    """便捷入口：分析运行日志，返回动态归因的问题定位清单。"""
    return LogAnalyzer(project_root).analyze(log_file=log_file, max_issues=max_issues)


if __name__ == "__main__":
    # 自测：分析当前项目日志
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _r = analyze_logs(_root)
    print(f"日志总行数: {_r['total_lines']}")
    print(f"归因摘要: Traceback={_r['summary']['tracebacks']}, "
          f"ERROR={_r['summary']['errors']}, CRITICAL={_r['summary']['criticals']}, "
          f"唯一定位={_r['summary']['unique_locations']}")
    for _i in _r["issues"][:10]:
        _loc = f"{os.path.basename(_i['file_path'])}:{_i['line']}" if _i["file_path"] else f"[{_i['organ']}]"
        print(f"  [{_i['error_type']}] {_loc} {_i['method']} ×{_i['count']} - {_i['message'][:60]}")
