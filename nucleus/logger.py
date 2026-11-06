# -*- coding: utf-8 -*-
"""
logger.py —— 日志器

版本: v10 PulseNet
设计: PulseNet 工程团队
日期: 2026年9月11日

职责: 基础日志功能封装
机制: 基于LogAggregationFilter类实现，包含7个核心方法
定位: 日志基础设施层
"""

import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import time

import config
from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.logging.sanitizer import SanitizingFilter, sanitize


def _m153_sanitizer_enabled() -> bool:
    """第153批 T153-2：ENABLE_LOG_SANITIZER 总开关接线。

    config.py 注释承诺「总开关：关闭后全库不脱敏（调试用）」，接线前该键零引用。
    默认（ENABLE_LOG_SANITIZER=True / LOG_SANITIZER_DEBUG_MODE=False）返回 True，
    与接线前三处 SanitizingFilter(enabled=...) 的取值逐一相等 ⇒ 零回归。
    """
    try:
        if not bool(getattr(config, "ENABLE_LOG_SANITIZER", True)):
            return False
        return not bool(getattr(config, "LOG_SANITIZER_DEBUG_MODE", False))
    except Exception as _e:
        silent_exc(_e, "_m153_sanitizer_enabled", level="debug")
        return True


# 日志级别字符串 → logging 常量映射
_LEVEL_MAP = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}

# 确保日志目录存在
_log_dir = getattr(config, "LOG_DIR", "logs")
os.makedirs(_log_dir, exist_ok=True)

# ★434刀2：日志队列化 —— 文件写盘从「各线程抢 SafeRotatingFileHandler 锁」降为
#   「业务线程入队 + QueueListener 单线程落盘」。这些模块级引用供停机 flush 与
#   树外 logger 桥复用（root.handlers 里挂的是 QueueHandler，不是文件 handler）。
_FILE_HANDLER = None       # SafeRotatingFileHandler（唯一落盘者）
_QUEUE_LISTENER = None     # QueueListener（后台落盘线程）
_QUEUE_HANDLER = None      # QueueHandler（挂在 root / 树外 logger 的入队口）

# ========== ★473刀1 日志队列有界化 ==========

_M473_QUEUE_DROPPED = 0          # 因队列满而丢弃的记录数（进程级累计）
_M473_QUEUE_DROP_WARN_AT = 0.0   # 上次丢弃告警的时刻（限流用）
_M473_DROP_WARN_INTERVAL = 60.0  # 丢弃告警最小间隔（秒）


class _M473NonBlockingQueueHandler(logging.handlers.QueueHandler):
    """★473刀1：入队永不阻塞、队列满即丢的 QueueHandler。

    为什么不能直接用标准 QueueHandler + 有界 Queue：
      标准 `QueueHandler.enqueue` 只捕 `ValueError`，`queue.Full` 会冒到
      `emit()` 的 `except Exception` ⇒ `handleError` ⇒ 默认 `raiseExceptions=True`
      时往 stderr 打整段 traceback。队列一满就刷屏，反而放大故障。
    本类口径：
      * 入队一律 `put_nowait`，**绝不阻塞业务线程**（阻塞式入队会让所有打日志的
        线程排队等磁盘，正是 434 刀2 之前那条死路）；
      * 满则丢弃 + 累加计数，最多每 60s 往 stderr 写一行提示
        （**不经过 logging**，否则会在已满的队列上递归）。
    """

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
            return
        except queue.Full:
            global _M473_QUEUE_DROPPED, _M473_QUEUE_DROP_WARN_AT
            _M473_QUEUE_DROPPED += 1
            _now = time.time()
            if _now - _M473_QUEUE_DROP_WARN_AT >= _M473_DROP_WARN_INTERVAL:
                _M473_QUEUE_DROP_WARN_AT = _now
                try:
                    _cap = getattr(self.queue, "maxsize", -1)
                    sys.stderr.write(
                        f"[473刀1] 日志队列已满（容量 {_cap}），累计丢弃 "
                        f"{_M473_QUEUE_DROPPED} 条；消费端可能被磁盘/轮转卡住。\n")
                    sys.stderr.flush()
                except Exception as _e:
                    silent_exc(_e, where="nucleus.logger::_M473NonBlockingQueueHandler.stderr",
                               level="debug")
            return
        except Exception as _e:
            silent_exc(_e, where="nucleus.logger::_M473NonBlockingQueueHandler.enqueue",
                       level="warning")



# ========== ★F3 重复日志聚合降噪 ==========

class LogAggregationFilter(logging.Filter):
    """
    ★F3：同类重复日志按周期聚合输出，控制日志爆炸。

    设计要点：
    - 仅对 DEBUG / INFO 级别聚合（高频周期日志的典型级别），
      WARNING 及以上（错误/告警）不聚合，保证关键信息不丢。
    - 同一 (logger名, 消息) 首次输出，随后在聚合窗口内静默，
      窗口结束或超过阈值时输出一次摘要「(聚合 N 次)」。
    - 有锁保护，线程安全；定期清理过期条目防止内存增长。
    """
    def __init__(self, window_seconds: float = 60.0, max_suppressed: int = 500):
        super().__init__()
        self._window = window_seconds
        self._max_suppressed = max_suppressed
        self._lock = threading.Lock()
        # key -> {"count": 抑制次数, "window_start": 窗口起始时间}
        self._suppressed: dict[tuple[str, str], dict] = {}
        self._last_cleanup = time.time()

    def filter(self, record: logging.LogRecord) -> bool:
        # 只聚合 DEBUG / INFO；WARNING 及以上直接放行
        if record.levelno >= logging.WARNING:
            return True

        # ★F3收尾：key 纳入 levelno，保证「同一消息不同级别不聚合」
        #   （先 INFO 后 DEBUG 视为不同条目，各自独立聚合）
        key = (record.name, record.getMessage(), record.levelno)
        now = time.time()

        with self._lock:
            # 定期清理超过 2 倍窗口的过期条目
            if now - self._last_cleanup > self._window * 2:
                _expired = [k for k, v in self._suppressed.items()
                            if now - v["window_start"] > self._window * 2]
                for k in _expired:
                    del self._suppressed[k]
                self._last_cleanup = now

            entry = self._suppressed.get(key)
            if entry is None:
                # 首次出现：记录窗口起点，放行
                self._suppressed[key] = {"count": 0, "window_start": now}
                return True

            # 已出现过：判断是否仍在窗口内
            if now - entry["window_start"] <= self._window:
                # 窗口内重复 → 抑制
                entry["count"] += 1
                return False
            else:
                # 窗口结束 → 追加聚合摘要后放行，开启新窗口
                if entry["count"] > 0:
                    record.msg = f"{record.getMessage()} (聚合 {entry['count']} 次)"
                    record.args = ()
                self._suppressed[key] = {"count": 0, "window_start": now}
                return True


# ========== 自定义格式器 ==========

#: ★436刀3：英文日志前缀 → 中文器官/模块名。**统一在 logger 做，不改各模块**。
#:   收录依据：`logs/pulse.log` 尾部 5 万行实测前缀频次（改造前英文前缀 top40）。
#:   未收录的一律原样返回（第三方库/一次性脚本不管，不硬凑译名）。
_M436_TAG_ZH = {
    # —— 高频（>500 行/5万行）——
    "verify_feedback": "验证反馈",
    "self_inspector": "自检视",
    "audit_problem_pool_consumer": "问题池消费",
    "SafeEvolutionExecutor": "安全演化执行",
    "audit_problem_pool": "问题池",
    "main": "主循环",
    "PatchExecutionDispatch": "补丁派单",
    "PulseNodePool": "节点池",
    "InfoField": "信息场",
    "verification_learning_hub": "验证学习枢纽",
    "NightOrchestration": "夜间编排",
    # —— 中频（50~500）——
    "CallRecorder": "调用记录",
    "runtime_metrics": "运行时指标",
    "PulseSnapshot": "快照",
    "subagent_runner": "子代理",
    "pulse.semantic_cache_l2": "语义缓存",
    "CodeProgressStore": "代码进度",
    "live_gate": "活体闸门",
    "release_policy": "发布策略",
    "ast_release": "语法树发布",
    "EvolutionBudget": "演化预算",
    "pulse": "框架",
    "pulse.llm.balance": "模型余额",
    "external_dependency": "外部依赖",
    "shadow_profile": "影子画像",
    "pulse.resonance": "共振",
    "pulse.silent_except": "静默异常",
    "pulse.tracer": "脉冲追踪",
    "CallGraphAnalyzer": "调用图",
    "WikiQuerier": "百科查询",
    "PatchManager": "补丁管理",
    "request_dedup": "请求去重",
    "SelfCalibrator": "自校准",
    # —— 低频（<50）——
    "K1Offload": "K1卸载",
    "DataQualityGuard": "数据质量",
    "CapabilityFramework": "能力框架",
    "exploration_audit": "探索审计",
    "LLMDependencyMetrics": "模型依赖度量",
    "EvolutionLoop": "演化闭环",
    "PatchAutoApprover": "补丁自动审批",
    "PulseMetricsCollector": "指标采集器",
    "PulseLung": "肺",
    "PulseHeart": "心脏",
    "PulseStomach": "胃",
    "PulseLiver": "肝",
    "PulseBrain": "大脑",
    "PulseEyes": "眼睛",
    "PulseEars": "耳朵",
    "PulseLegs": "双腿",
    "PulseBloodVessel": "血管",
    "PulseCodeLearner": "代码学习",
    "PulseInnerWorld": "内在世界",
    "PulseVisualCortex": "视觉皮层",
}


def _m436_zh_tag_on() -> bool:
    """★436刀3：日志前缀中文化开关（缺省 True）。关 ⇒ 逐字回到英文原名。"""
    try:
        return bool(getattr(config, "ENABLE_M436_LOG_TAG_ZH", True))
    except Exception as _e:
        silent_exc(_e, where="nucleus.logger::_m436_zh_tag_on", level="debug")
        return True


def _m436_zh_tag(tag: str, name: str = "") -> str:
    """★436刀3：英文前缀 → 中文；未收录原样返回（第三方库不管）。"""
    if not _m436_zh_tag_on():
        return tag
    _z = _M436_TAG_ZH.get(tag)
    if _z:
        return _z
    # `pulse.organ.PulseHeart` 会被 format 截成 `Heart` —— 补查 `Pulse` + tag
    _z = _M436_TAG_ZH.get("Pulse" + tag)
    if _z:
        return _z
    if name and name != tag:
        _z = _M436_TAG_ZH.get(name)
        if _z:
            return _z
    return tag


class PulseFormatter(logging.Formatter):
    """
    将 logger 名称映射为简洁的中文标签。
    - pulse.organ.PulseHeart → "心脏"
    - pulse.organ.PulseStomach → "胃"
    - pulse.framework → "框架"
    - pulse.module.PulseSnapshot → "PulseSnapshot"
    """
    def format(self, record: logging.LogRecord) -> str:
        name = record.name
        if name.startswith("pulse.organ."):
            organ_full = name.split(".")[-1]
            if organ_full.startswith("Pulse"):
                short = organ_full[5:]
            else:
                short = organ_full
            # ★436刀3：疑似英文前缀 → 查中文映射（未收录原样）
            record.organ_tag = _m436_zh_tag(short, name)
        elif name == "pulse.framework":
            record.organ_tag = "框架"
        elif name.startswith("pulse.module."):
            # 非器官模块，保留模块原名作为标签；★436刀3 再查中文映射
            record.organ_tag = _m436_zh_tag(name.split(".")[-1], name)
        else:
            # ★436刀3：直接 logger 名（如 verify_feedback / self_inspector / main）
            record.organ_tag = _m436_zh_tag(name, name)
        # ★348刀1③（A-11 根治）：**每一行末尾**自带 `pid=<n> gen=<pid@创建时刻>`。
        #   为什么放行尾、不放标签后面：`LogAnalyzer._LOG_ORGAN_RE` 的正则是
        #   `\[([^\]]+)\]\s*(?:\[)?(ERROR|CRITICAL|WARNING|Traceback|Exception)`
        #   —— organ 标签与级别必须**相邻**。把 pid@gen 插在中间，该正则整条失配，
        #   organ 抽取会**静默全丢**（比不加更糟）。行尾追加对既有解析零影响，
        #   同时满足 A-11 的目的：任何一行都能自证出自哪个进程代次
        #   （340 前置 §0 判据：机制行须 `ts≥启动时刻 ∧ 行内 proc_gen==活体`）。
        _m348_line = super().format(record)
        try:
            _m348_stamp = self._m348_proc_stamp()
        except Exception as _m348_e:
            silent_exc(_m348_e, where="nucleus.logger::PulseFormatter.format pid", level="debug")
            _m348_stamp = ""
        return (_m348_line + "  " + _m348_stamp) if _m348_stamp else _m348_line

    #: ★348刀1③：进程级缓存（pid/proc_gen 一个进程内不会变，避免每行都问内核）
    _m348_pid: int | None = None
    _m348_gen: str = ""

    @classmethod
    def _m348_proc_stamp(cls) -> str:
        """`pid=<n> gen=<proc_gen>`；`PULSE_LOG_NO_PROC_STAMP=1` 可关（应急降噪）。"""
        if os.environ.get("PULSE_LOG_NO_PROC_STAMP"):
            return ""
        if cls._m348_pid is None:
            cls._m348_pid = os.getpid()
        if not cls._m348_gen:
            from nucleus.proc_gen import proc_gen_str as _m348_pgs
            cls._m348_gen = _m348_pgs() or "unknown"
        return "pid=%d gen=%s" % (cls._m348_pid, cls._m348_gen)
    def formatException(self, ei) -> str:
        _s = super().formatException(ei)
        return sanitize(_s) if _s else _s
    def formatStack(self, stack_info) -> str:
        _s = super().formatStack(stack_info)
        return sanitize(_s) if _s else _s


# ========== ★主线第42批 T1（P0-272）：日志留存治理 ==========  # _m42_t1b
#   实测证据链（详见交付报告）：
#     ① logs/pulse.log 的 ctime = 09-03 未变，而内容 22119 行**全部**属于 09-13（无旧内容残留）；
#        实验证明 truncate 不改 ctime、delete+重建会改 → 属「原地截断」而非删除；
#     ② 框架代码全库 AST 扫描无任何截断/删除 pulse.log 的逻辑
#        （3 处 open(log_path,"w") 均写 data/stream/*.json 业务数据）；
#     ③ 项目内无清理脚本、无计划任务；logs/ 下 param_reports/ 与 config_changes.log 完好。
#     → 根因：**外部清理**（如 `> logs/pulse.log` 重定向 / Clear-Content / 编辑器保存）。
#     外部操作无法拦截，故此处提供两项能力：只清超期 + 被截断必留痕。

_LOG_STATE_FILE = ".log_state.json"
_LOG_INTEGRITY_FILE = "log_integrity_events.log"
# ★D017 / 相关任务：日志轮转自感知标记。轮转发生时写入（含唯一时间戳），
#   check_log_integrity 据此把「size 下降」复判为框架内轮转而非外部截断。
#   该文件为 .json，不被 cleanup_old_logs 当作日志清理，也不影响 .1/.2 编号备份（保留既有留存测试）。
_LOG_ROLLOVER_MARKER = ".rollover_marker.json"
# 轮转自感知时间窗（秒）：标记时间戳距当前 <= 此值才复判为轮转，避免长期掩盖真实外部截断。
_ROLLOVER_AWARE_WINDOW_SEC = 120.0


def _log_in_test_env() -> bool:
    """测试环境判定：pytest 下禁止向生产 logs/ 写盘（避免污染真实日志）。"""
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.logger::_log_in_test_env L178", level="debug")
        return False


def log_retention_days() -> int:
    """超期日志保留天数；返回 0 表示不清理（开关关闭 / 配置非法）。"""
    try:
        if not bool(getattr(config, "ENABLE_LOG_RETENTION_CLEANUP", True)):
            return 0
        _d = int(getattr(config, "LOG_RETENTION_DAYS", 7))
        return max(0, _d)
    except Exception as e:
        silent_exc(e, where="nucleus.logger::log_retention_days L189", level="debug")
        return 0


def _is_log_file(name: str) -> bool:
    """是否日志文件或轮转备份（pulse.log / pulse.log.1 / any.log）。"""
    return bool(name.endswith(".log")) or bool(re.search(r"\.log\.\d+$", name))


def cleanup_old_logs(log_dir: str | None = None, days: int | None = None,
                     now: float | None = None) -> list:
    """删除**超过 N 天未更新**的日志与轮转备份，返回被删路径列表。

    与「清空当前日志」的本质区别：
      · 只按 **mtime** 判定 —— 正在写入的文件 mtime 恒为当下，**永不会被删**；
      · `LOG_RETENTION_PROTECT` 内的文件名（默认 `pulse_crash.log`）**永不删**；
      · `days<=0` / 开关关闭 → 直接返回空列表（零副作用）。
    """
    _dir = log_dir if log_dir is not None else _log_dir
    _days = log_retention_days() if days is None else int(days)
    if _days <= 0 or not os.path.isdir(_dir):
        return []
    _now = time.time() if now is None else float(now)
    _cut = _now - _days * 86400.0
    try:
        _protect = set(getattr(config, "LOG_RETENTION_PROTECT", None) or [])
    except Exception:
        _protect = set()
    _protect.add(_LOG_STATE_FILE)
    _protect.add(_LOG_ROLLOVER_MARKER)
    _removed = []
    try:
        _names = os.listdir(_dir)
    except OSError as _m360e:
        # ★360刀1：列目录失败 ⇒ 清理整体跳过，此前零痕迹（读的人以为"没有旧日志"）
        silent_exc(_m360e, "logger.py:254 旧日志清理列目录失败", level="warning")
        return []
    for _name in _names:
        _p = os.path.join(_dir, _name)
        try:
            if not os.path.isfile(_p) or not _is_log_file(_name) or _name in _protect:
                continue
            if os.path.getmtime(_p) >= _cut:
                continue
            os.remove(_p)
            _removed.append(_p)
        except OSError as _m360e:
            # ★360刀1：删除失败静默 continue ⇒ 磁盘回收"看着没执行"
            silent_exc(_m360e, "logger.py:265 旧日志删除失败(跳过)", level="warning")
            continue
    return _removed


def _fingerprint(path: str):
    """文件指纹（size/ino/mtime）；不存在或不可读 → None。"""
    try:
        _st = os.stat(path)
        return {"size": int(_st.st_size),
                "ino": int(getattr(_st, "st_ino", 0) or 0),
                "mtime": float(_st.st_mtime)}
    except OSError as e:
        silent_exc(e, where="nucleus.logger::_fingerprint L245", level="debug")
        return None


def _append_integrity_event(log_dir: str, res: dict, force: bool = False) -> None:
    """把完整性事件追加到**独立**留痕文件（不写主日志，避免随主日志被一起清空）。

    ``force=True`` 用于测试/工具显式指定目录的场景（此时不受测试环境静音限制）。
    ``event == "rollover"`` 时为框架内轮转自感知事件，note 区别于外部截断。
    """
    if not force and _log_in_test_env():
        return
    try:
        _p = os.path.join(log_dir, _LOG_INTEGRITY_FILE)
        _prev = res.get("previous") or {}
        _cur = res.get("current") or {}
        _status = res.get("status")
        if _status == "rollover":
            _note = "日志轮转自感知：识别为框架内轮转（非外部截断）"
        else:
            _note = "检测到日志被外部截断/替换（框架内无此逻辑）"
        _line = json.dumps({
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": _status,
            "prev_size": _prev.get("size"), "cur_size": _cur.get("size"),
            "prev_ino": _prev.get("ino"), "cur_ino": _cur.get("ino"),
            "archive": res.get("rollover_archive"),
            "note": _note,
        }, ensure_ascii=False)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(sanitize(_line) + "\n")
    except OSError as _e:
        print(f"[logger] 完整性事件写入失败: {type(_e).__name__}: {_e}",
              file=sys.stderr)


def _log_max_bytes() -> int:
    """★往期批次 T146-8：单日志文件轮转上限（bytes）；不可读/未配置 → 0（表示未知）。"""
    try:
        _v = int(getattr(config, "LOG_MAX_BYTES", 0) or 0)
    except Exception:
        # ★相关任务：门禁友好——非静默上报（配置读取失败按「未知」处理，不臆断）
        logging.getLogger(__name__).debug("LOG_MAX_BYTES 读取失败，按未知处理")
        return 0
    return max(0, _v)


def _reset_log_state(log_dir: str, log_file: str) -> None:
    """★往期批次 T146-8：把状态文件的指纹复位为**轮转后**的当前值。

    根因：轮转前 size 远大于轮转后 size，若状态文件仍记着旧的大 size，
    下一次 check_log_integrity 必然算成「size 下降 → 外部截断」。只有在同一次检查里
    恰好命中「近期 marker」才能被复判为 rollover；marker 一旦过期/被清，
    正常轮转就会被误报成本该告警的外部截断。
    """
    try:
        _sp = os.path.join(log_dir, _LOG_STATE_FILE)
        _fp = _fingerprint(log_file) or {}
        with open(_sp, "w", encoding="utf-8") as _f:
            json.dump({"ts": time.time(), "file": log_file,
                       "size": _fp.get("size"), "ino": _fp.get("ino"),
                       "mtime": _fp.get("mtime")}, _f, ensure_ascii=False)
    except OSError as _e:
        print(f"[logger] 轮转后状态复位失败: {type(_e).__name__}: {_e}",
              file=sys.stderr)


def _write_rollover_marker(log_dir: str, archive_path: str, prev_size,
                           log_file: str | None = None) -> None:
    """★D017 / 相关任务：轮转成功的唯一标记。

    写入 ``{log_dir}/.rollover_marker.json``（含唯一时间戳），供 check_log_integrity
    把后续的「size 下降」复判为框架内轮转，而非误判外部截断。同时直接留痕一条
    rollover 事件（与 check_log_integrity 口径一致）。
    """
    try:
        _p = os.path.join(log_dir, _LOG_ROLLOVER_MARKER)
        _now = time.time()
        with open(_p, "w", encoding="utf-8") as _f:
            json.dump({"ts": _now, "archive": archive_path, "prev_size": prev_size},
                      _f, ensure_ascii=False)
        _append_integrity_event(log_dir, {
            "status": "rollover",
            "previous": {"size": prev_size},
            "current": {"size": 0},
            "file": archive_path,
            "rollover_archive": archive_path,
        })
        # ★往期批次 T146-8（判据 A）：写 marker 的同时把状态文件复位到轮转后的指纹，
        #   使下一次 check_log_integrity 直接判 ok，不再依赖 marker 时间窗兜底。
        if log_file:
            _reset_log_state(log_dir, log_file)
    except OSError:
        # ★相关任务：门禁友好——非静默上报（轮转标记写入失败属非致命，但须留痕）
        logging.getLogger(__name__).debug("rollover 标记写入失败(非致命): %s", log_dir)


def _read_rollover_marker(log_dir: str) -> dict | None:
    """读取轮转标记；缺失/损坏 → None。"""
    try:
        _p = os.path.join(log_dir, _LOG_ROLLOVER_MARKER)
        if not os.path.isfile(_p):
            return None
        with open(_p, encoding="utf-8") as _f:
            _d = json.loads(_f.read())
        return _d if isinstance(_d, dict) else None
    except (OSError, ValueError):
        # ★相关任务：门禁友好——非静默上报（标记缺失/损坏即视为无轮转，但须留痕）
        logging.getLogger(__name__).debug("rollover 标记读取失败(视为缺失): %s", log_dir)
        return None


def check_log_integrity(log_dir: str | None = None, log_file: str | None = None,
                        state_path: str | None = None) -> dict:
    """比对上次记录的日志指纹，检测**外部截断/删除**并留痕。

    返回 status ∈ {first_run, ok, truncated, replaced, missing, skipped}：
      · `truncated` —— 当前 size < 上次 size（★本批要抓的场景）；
      · `replaced`  —— ino 变化（文件被删除后重建）；
      · `missing`   —— 文件不存在。
    `truncated/replaced/missing` 三种会向 `logs/log_integrity_events.log` 追加一条 JSON。
    """
    _dir = log_dir if log_dir is not None else _log_dir
    _file = log_file if log_file is not None else os.path.join(
        _dir, getattr(config, "LOG_FILE", "pulse.log"))
    _sp = state_path if state_path is not None else os.path.join(_dir, _LOG_STATE_FILE)
    try:
        if not bool(getattr(config, "ENABLE_LOG_INTEGRITY_CHECK", True)):
            return {"status": "skipped", "reason": "disabled"}
    except Exception:
        return {"status": "skipped", "reason": "config_error"}

    _cur = _fingerprint(_file)
    _prev = None
    try:
        if os.path.isfile(_sp):
            with open(_sp, encoding="utf-8") as _f:
                _prev = json.loads(_f.read())
            if not isinstance(_prev, dict):
                _prev = None
    except (OSError, ValueError):
        _prev = None

    _res = {"status": "first_run", "current": _cur, "previous": _prev, "file": _file}
    if isinstance(_prev, dict):
        if _cur is None:
            _res["status"] = "missing"
        elif _prev.get("ino") and _cur.get("ino") and int(_prev["ino"]) != int(_cur["ino"]):
            _res["status"] = "replaced"
        elif isinstance(_prev.get("size"), int) and int(_cur["size"]) < int(_prev["size"]):
            _res["status"] = "truncated"
        else:
            _res["status"] = "ok"

    # ★D017 / 相关任务：轮转自感知 —— 「size 下降(truncated)」或「inode 变化(replaced)」
    #   若与近期框架内轮转吻合，复判为 rollover。轮转既可能截断当前文件（copy-truncate 路径，
    #   size 下降），也可能 rename 后新建文件（rename 路径，inode 变化），两者都要覆盖，
    #   避免把自己的轮转误判成「外部截断/替换」（框架内从无截断逻辑）。
    if _res["status"] in ("truncated", "replaced"):
        _mk = _read_rollover_marker(_dir)
        if _mk is None and isinstance(_prev, dict) and _cur is not None:
            # ★往期批次 T146-8（判据 B）：无 marker 也不必急着喊外部截断——
            #   若「上次 size 已达轮转上限」且「<log>.1 归档确实存在」，
            #   这只可能是我们自己的 SafeRotatingFileHandler 转出去的。
            _maxb = _log_max_bytes()
            _prev_sz = _prev.get("size")
            if (_maxb > 0 and isinstance(_prev_sz, int) and _prev_sz >= _maxb
                    and os.path.isfile(_file + ".1")):
                _res["status"] = "rollover"
                _res["rollover_archive"] = _file + ".1"
                _res["rollover_note"] = (
                    "补充判据：prev.size(%d) 已达轮转上限(%d) 且 %s.1 归档存在，"
                    "判定为框架内轮转" % (_prev_sz, _maxb, os.path.basename(_file)))
        if _mk:
            _win = _ROLLOVER_AWARE_WINDOW_SEC
            try:
                _win = float(getattr(config, "LOG_ROLLOVER_AWARE_WINDOW_SEC", _ROLLOVER_AWARE_WINDOW_SEC))
            except Exception:
                # ★相关任务：门禁友好——非静默上报（窗口配置读取失败回退默认，但须留痕）
                logging.getLogger(__name__).debug(
                    "轮转感知窗口配置读取失败，回退默认: %s", _ROLLOVER_AWARE_WINDOW_SEC)
                _win = _ROLLOVER_AWARE_WINDOW_SEC
            if (time.time() - float(_mk.get("ts", 0))) <= _win:
                _res["status"] = "rollover"
                _res["rollover_archive"] = _mk.get("archive")
                _res["rollover_note"] = ("size 下降/inode 变化与框架内轮转时间窗吻合，"
                                         "复判为轮转而非外部截断/替换")
                # 标记已消费：避免长期掩盖真实外部截断（消费后若再发生真实截断会正确告警）
                try:
                    os.remove(os.path.join(_dir, _LOG_ROLLOVER_MARKER))
                except OSError:
                    # ★相关任务：门禁友好——非静默上报（标记消费失败属非致命，但须留痕）
                    logging.getLogger(__name__).debug("rollover 标记消费(删除)失败(非致命)")

    if _res["status"] in ("truncated", "replaced", "missing", "rollover"):
        # 显式指定 state_path = 测试/工具模式 → 允许写入该目录（force）
        _append_integrity_event(_dir, _res, force=(state_path is not None))

    if state_path is not None or not _log_in_test_env():
        try:
            _st2 = _cur or {}
            with open(_sp, "w", encoding="utf-8") as _f:
                json.dump({"ts": time.time(), "file": _file,
                           "size": _st2.get("size"), "ino": _st2.get("ino"),
                           "mtime": _st2.get("mtime")}, _f, ensure_ascii=False)
        except OSError as _e:
            print(f"[logger] 日志状态写入失败: {type(_e).__name__}: {_e}",
                  file=sys.stderr)
    return _res


# ========== 全局初始化 ==========
_log_initialized = False
_log_init_lock = threading.Lock()


# ★★主线第49批 T3-3（P2-333）：Windows 文件占用下的安全日志轮转。
#   背景：标准库 `RotatingFileHandler.doRollover()` 用 `os.rename()` 轮转，
#   Windows 不允许重命名**被占用**的文件 → `PermissionError [WinError 32]`，
#   logging 内部把完整调用栈（~30 行）打到控制台 → 刷屏。
#   修复：子类化并重写 `doRollover()` ——
#     · 捕获 `PermissionError`，**有限重试**（文件可能瞬间被释放）
#     · 仍失败 → 记一条**单行 WARNING**，继续写当前文件（不中断运行）
#     · 其他异常上报 logging 内部处理管道（`handleError`），不自行打印栈

# ---- ★349刀10①：轮转**属主**判据（348 生产事故根治）----
#   事故：一个验证脚本 `import nucleus.logger` 就触发了**生产日志轮转**
#   （`pulse.log.1` 消失、`.5` 被当天内容覆盖）。根因不是"轮转写坏了"，
#   是**轮转被路过者执行了**：任何 import 本模块的进程都会建一个
#   RotatingFileHandler，它打开时看到文件已超 maxBytes，emit 第一条就轮转。
#   而真正的写者（main.py 那个进程）根本没打算轮转 —— 两个 handler 抢同一份
#   备份序列（`.4->.5`、`.1->.2`），互相踩踏，于是出现
#   `FileNotFoundError: logs/pulse.log.4 -> logs/pulse.log.5`。
#
#   判据：**只有项目入口进程（`__main__` 是 main.py）才允许轮转**。
#     · 生产主进程 —— 允许（行为与改前一致）；
#     · 脚本 / 工具 / 测试 —— 不允许（它们只是"看一眼日志"）；
#     · 环境变量 `PULSE_LOG_ROTATE=1|0` 可显式覆盖（运维应急口）。
#   ★为什么不按"文件大小/自己写了多少"判：那种判据在**重启后首次打开**
#     与"文件早已超限"两种情形下会给出互相矛盾的答案，且都要额外状态。
#     "我是谁"是唯一稳定的事实。
_M349_ROTATE_DECISION: dict = {}


def _m349_rotate_allowed() -> tuple:
    """本进程是否允许执行日志轮转。返回 `(allowed, why)`（结果进程内缓存）。"""
    if _M349_ROTATE_DECISION.get("_decided"):
        return (_M349_ROTATE_DECISION["allowed"], _M349_ROTATE_DECISION["why"])
    _env = str(os.environ.get("PULSE_LOG_ROTATE", "") or "").strip().lower()
    if _env in ("1", "true", "yes", "on"):
        _a, _w = True, "env_force_on"
    elif _env in ("0", "false", "no", "off"):
        _a, _w = False, "env_force_off"
    else:
        _mf = ""
        try:
            _mn = sys.modules.get("__main__")
            _mf = str(getattr(_mn, "__file__", "") or "").replace("\\", "/")
        except Exception as _e:  # noqa: BLE001
            _mf = ""
            print("[Logger] 轮转属主判定读 __main__ 失败: %s: %s"
                  % (type(_e).__name__, _e))
        if _mf.endswith("/main.py") or _mf.endswith("/main.pyc"):
            _a, _w = True, "main_entry"
        else:
            _a, _w = False, "non_entry_process:%s" % (_mf.rsplit("/", 1)[-1] or "?")
    _M349_ROTATE_DECISION["_decided"] = True
    _M349_ROTATE_DECISION["allowed"] = _a
    _M349_ROTATE_DECISION["why"] = _w
    return _a, _w


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """★第49批 T3-3：轮转失败不打印调用栈、不中断写入。"""

    def doRollover(self) -> None:
        # ★349刀10①：**先问"是不是我该轮"**。非入口进程一律不动备份序列
        #   （348 事故现场：脚本把 `.1` 挪没了、把 `.5` 覆盖了，主进程毫无察觉）。
        _m349_allow, _m349_why = _m349_rotate_allowed()
        if not _m349_allow:
            _k = "_blocked_%s" % _m349_why
            if not _M349_ROTATE_DECISION.get(_k):
                _M349_ROTATE_DECISION[_k] = True
                print("[Logger] 轮转被拦截：本进程不是日志写者（%s），"
                      "保留现有备份序列不动 %s" % (_m349_why, self.baseFilename))
            return
        _base = self.baseFilename
        _dir = os.path.dirname(_base) or "."
        _prev_size = None
        try:
            _prev_fp = _fingerprint(_base)
            if isinstance(_prev_fp, dict):
                _prev_size = _prev_fp.get("size")
        except Exception:
            _prev_size = None
        _retry = 3
        _wait = 0.2
        try:
            _retry = int(getattr(config, "LOG_ROLLOVER_RETRY", 3))
            _wait = float(getattr(config, "LOG_ROLLOVER_RETRY_WAIT", 0.2))
        except Exception as _rc:
            _retry = 3
            _wait = 0.2
            try:
                _log_rollover_cfg_error(_rc, "cfg")
            except Exception as _m360e:
                # ★360刀1：告警器自身失败不能静默（否则"告警没出现"会被读成"没故障"）
                silent_exc(_m360e, "logger.py:584 轮转配置告警器自身失败",
                           level="warning")
        if _retry < 1:
            _retry = 1
        if _wait < 0.0:
            _wait = 0.0
        for _attempt in range(_retry):
            try:
                super().doRollover()
                try:
                    _write_rollover_marker(_dir, _base + ".1", _prev_size, _base)
                except Exception as _m360e:
                    # ★360刀1：marker 写失败 ⇒ 后续 size 下降会被误判"外部截断"
                    silent_exc(_m360e, "logger.py:595 轮转标记写入失败(doRollover)",
                               level="warning")
                return
            except PermissionError as _pe:
                if _attempt < _retry - 1:
                    try:
                        time.sleep(min(_wait * (2 ** _attempt), 2.0))
                    except Exception as _m360e:
                        silent_exc(_m360e, "logger.py:602 轮转重试 sleep 失败",
                                   level="warning")
                    continue
                # ★360刀4：重试耗尽 ⇒ 轮转被放弃，此前**零痕迹**
                _m360_rollover_note(_dir, "permission_retry_exhausted", _pe,
                                    attempt=_attempt + 1, retry=_retry)
                break
            except OSError as _oe:
                # ★360刀4：OSError 直接 break ⇒ 静默放弃轮转
                _m360_rollover_note(_dir, "oserror", _oe,
                                    attempt=_attempt + 1, retry=_retry)
                break
            except Exception as _ue:
                # ★360刀4：兜底分支静默 break（连异常类型都看不见）
                _m360_rollover_note(_dir, "unknown", _ue,
                                    attempt=_attempt + 1, retry=_retry)
                break
        try:
            self._copy_truncate_rollover()
            try:
                _write_rollover_marker(_dir, _base + ".1", _prev_size, _base)
            except Exception as _m360e:
                silent_exc(_m360e, "logger.py:614 轮转标记写入失败(复制截断兜底)",
                           level="warning")
            return
        except Exception as _ce:
            # ★360刀1/刀4：兜底路径也失败 ⇒ 轮转彻底放弃，必须留痕
            _m360_rollover_note(_dir, "copy_truncate_failed", _ce)
            try:
                _warn_rollover_blocked_cooled(_ce)
            except Exception as _m360e:
                silent_exc(_m360e, "logger.py:620 轮转兜底告警器自身失败",
                           level="warning")
            return

    def _copy_truncate_rollover(self) -> None:
        # ★主线第59批 T1（方案B）：复制+截断式轮转，规避 Windows 文件锁。
        # 标准库 doRollover 用 os.rename 重命名「当前打开的日志文件」，
        # Windows 下该文件被本进程持有句柄（未授予 FILE_SHARE_DELETE）→
        # PermissionError[WinError 32]。本方法不重命名打开的文件：
        # 先把当前内容复制为 .1 备份，再就地截断当前文件为空。
        # 调用点已持 self.lock（emit 内），无需额外加锁防并发写入。
        _base = self.baseFilename
        # ★356刀12②：先问滑窗预算——超限直接放弃本轮（不再复制），避免轮转风暴
        if not _m356_rollover_budget_ok():
            return
        _max = 1
        try:
            _max = int(getattr(config, "LOG_BACKUP_COUNT", 5))
        except Exception as e:
            silent_exc(e, where="nucleus.logger::_copy_truncate_rollover L530", level="debug")
            _max = 1
        # 1) 推移既有备份（已关闭文件，rename 安全）
        if _max >= 1:
            try:
                for _i in range(_max - 1, 0, -1):
                    _src = f"{_base}.{_i}"
                    _dst = f"{_base}.{_i + 1}"
                    if os.path.exists(_src):
                        if os.path.exists(_dst):
                            os.remove(_dst)
                        os.rename(_src, _dst)
            except Exception as _se:
                _log_rollover_cfg_error(_se, "shift")
        # 2) 复制当前内容 → .1（append 模式流不可读，另开读句柄读取已 flush 的磁盘内容）
        _backup = f"{_base}.1"
        try:
            if os.path.exists(_backup):
                os.remove(_backup)
        except OSError as e:
            silent_exc(e, where="nucleus.logger::_copy_truncate_rollover L549", level="debug")
        # ★253批修复：self.stream可能为None（如文件句柄已关闭），先检查再flush
        if self.stream is not None:
            try:
                self.stream.flush()
            except Exception as _m360e:
                # ★360刀1：flush 失败 ⇒ 复制的是未落盘内容，备份会短一截
                silent_exc(_m360e, "logger.py:664 轮转前 flush 失败",
                           level="warning")
        with open(_base, "rb") as _fin, open(_backup, "wb") as _fout:
            _fout.write(_fin.read())
        # 3) 截断当前文件（就地清空，不重命名）
        # ★356刀12①：**stream=None 时必须另开句柄截断**。
        #   253 批加的 `if self.stream is not None` 保护，在停机/close 后把轮转
        #   变成"只复制不截断"：`.1` 是完整副本、主文件却一点没变小，下一次 size
        #   判定立刻再触发 ⇒ 风暴。这里 stream 不可用就退回独立读写句柄。
        _m356_truncated = False
        if self.stream is not None:
            try:
                self.stream.seek(0)
                self.stream.truncate(0)
                self.stream.flush()
                _m356_truncated = True
            except Exception as _te356a:
                # ★356刀12③：截断失败**必须告警**（原来这里是 pass，静默）
                _log_rollover_cfg_error(_te356a, "truncate_stream")
        if not _m356_truncated:
            try:
                with open(_base, "r+b") as _tr356:
                    _tr356.seek(0)
                    _tr356.truncate(0)
                    _tr356.flush()
                    os.fsync(_tr356.fileno())
                _m356_truncated = True
            except OSError as _te356b:
                _log_rollover_cfg_error(_te356b, "truncate_fallback")
        if not _m356_truncated:
            # 复制成功但截断失败 = "轮转成功但主文件没换新"，必须显式喊出来，
            # 否则调用方照写 rollover marker，读的人以为轮转干净了。
            print("[Logger] 轮转截断失败：主文件未被清空（已复制 %s.1，但 %s 仍为原大小）"
                  % (_base, os.path.basename(_base)))

def _log_rollover_cfg_error(exc: BaseException, stage: str = "") -> None:
    """轮转**失败**的诊断（避免静默）。

    ★349刀10①：加 `stage` 分档 —— 348 现场打的是「轮转重试配置读取失败:
    FileNotFoundError: logs/pulse.log.4 -> .5」，读的人会去查配置，而真因是
    **备份推移时文件被别人挪走了**。同一个函数被三种事实调用，措辞必须能分开
    （铁律166：不同事实不同措辞）。
    """
    _stage = str(stage or "unknown")
    print(f"[Logger] 轮转失败[{_stage}] {type(exc).__name__}: {exc}")


# ★356刀12②：轮转风暴防护（星轨实拍：停机收尾窗 17 秒内 20+ 条 rollover 事件）
_M356_ROLLOVER_WINDOW_SEC = 60.0
_M356_ROLLOVER_MAX_PER_WINDOW = 3
_M356_ROLLOVER_STAMPS: list = []
_M356_ROLLOVER_STORM_WARNED = False
_m356_rollover_lock = threading.Lock()


def _m356_rollover_budget_ok() -> bool:
    """★356刀12②：同进程滑窗限额——超限即冷却降级（只告警一次，不再复制）。

    为什么要有它：截断失败时"复制成功 + 主文件没变小"会让下一次 size 判定
    **立刻**再次触发轮转，而每次轮转都写一条 rollover marker ⇒ 17 秒 20+ 条，
    既刷爆 `log_integrity_events.log`，又把磁盘反复写满副本。
    """
    global _M356_ROLLOVER_STORM_WARNED
    _now = time.time()
    with _m356_rollover_lock:
        _stamps = [t for t in _M356_ROLLOVER_STAMPS
                   if _now - t < _M356_ROLLOVER_WINDOW_SEC]
        if not _stamps:
            _M356_ROLLOVER_STORM_WARNED = False      # 窗口清空 ⇒ 告警资格复位
        if len(_stamps) >= _M356_ROLLOVER_MAX_PER_WINDOW:
            _M356_ROLLOVER_STAMPS[:] = _stamps
            if not _M356_ROLLOVER_STORM_WARNED:
                _M356_ROLLOVER_STORM_WARNED = True
                print("[Logger] 轮转风暴冷却：%d 秒内已轮转 %d 次（上限 %d），"
                      "本窗口内不再复制——大概率是截断失败导致主文件没变小"
                      % (_M356_ROLLOVER_WINDOW_SEC, len(_stamps),
                         _M356_ROLLOVER_MAX_PER_WINDOW))
            return False
        _stamps.append(_now)
        _M356_ROLLOVER_STAMPS[:] = _stamps
        return True


# 轮转降级告警冷却（避免刷屏）：默认 5 分钟内最多报一次
_ROLLOVER_WARN_COOLDOWN_SEC = 300.0
_last_rollover_warn_ts = 0.0
_rollover_warn_lock = threading.Lock()


def _warn_rollover_blocked_cooled(exc: BaseException) -> None:
    # ★主线第59批 T1：轮转彻底失败（rename + 复制截断均失败）时的冷却 WARNING。
    # 只记单行 WARNING、绝不打印调用栈；冷却期内不重复刷屏。
    global _last_rollover_warn_ts
    try:
        _now = time.time()
        _cooldown = _ROLLOVER_WARN_COOLDOWN_SEC
        try:
            _cooldown = float(getattr(config, "LOG_ROLLOVER_WARN_COOLDOWN_SEC",
                                      _ROLLOVER_WARN_COOLDOWN_SEC))
        except Exception as e:
            silent_exc(e, "logger.py:506 轮转冷却读", level="warning")
        _emit = False
        with _rollover_warn_lock:
            if _now - _last_rollover_warn_ts >= _cooldown:
                _emit = True
                _last_rollover_warn_ts = _now
        if not _emit:
            return
        _lg = logging.getLogger(__name__)
        _lg.warning("日志轮转失败（复制+截断兜底也失败），将继续写入当前文件: "
                    "%s: %s", type(exc).__name__, exc)
    except Exception as _le:
        print(f"[Logger] 日志轮转失败且告警失败: {type(_le).__name__}")


# ==========================================================================
# ★第360批 刀1/刀4：轮转失败路径**统一留痕**（烛微 359 A 类）
# --------------------------------------------------------------------------
# 背景：轮转族此前 9 处 `except ...: pass/break`，失败**零痕迹**。
#   349 刀10① 加了 `_log_rollover_cfg_error`（只覆盖配置读取），356 刀12 加了
#   681 截断告警与风暴防护；本批补齐剩下的面：
#   - 596 处 pass（marker 写失败 / 告警器自身失败 / flush 失败）
#   - 606/608 处 break（重试耗尽、OSError、未知异常 ⇒ 轮转**静默放弃**）
# 落点：① 模块 warning（带 stage 分档，铁律166：不同事实不同措辞）；
#       ② 追加一行 JSON 到 `log_integrity_events.log`（与轮转成功标记同文件，
#          运维查一处即可看到成功/失败全貌）；③ 进程内计数（可观测/可断言）。
# ==========================================================================
_M360_ROLLOVER_NOTES: dict = {}
_M360_ROLLOVER_NOTES_LOCK = threading.Lock()


def _m360_rollover_note(log_dir: str, stage: str, exc: BaseException,
                        attempt: int = 0, retry: int = 0,
                        force: bool = False) -> dict:
    """轮转失败留痕：**只留痕，不改变任何控制流**（调用方自己 break/return）。"""
    _stage = str(stage or "unknown")
    try:
        with _M360_ROLLOVER_NOTES_LOCK:
            _M360_ROLLOVER_NOTES[_stage] = int(
                _M360_ROLLOVER_NOTES.get(_stage) or 0) + 1
            _n = _M360_ROLLOVER_NOTES[_stage]
    except Exception as _e:
        silent_exc(_e, "logger.py:_m360_rollover_note 计数", level="warning")
        _n = -1
    _msg = ("日志轮转失败[stage=%s] attempt=%s/%s 累计%d次: %s: %s"
            % (_stage, attempt or "-", retry or "-", _n, type(exc).__name__, exc))
    try:
        logging.getLogger(__name__).warning(_msg)
    except Exception as _e:
        silent_exc(_e, "logger.py:_m360_rollover_note warning", level="warning")
    try:
        if force or not _log_in_test_env():
            _p = os.path.join(log_dir or "", _LOG_INTEGRITY_FILE)
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(json.dumps({
                    "ts": time.time(),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "event": "rollover_failed",
                    "stage": _stage,
                    "attempt": attempt, "retry": retry, "count": _n,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "note": "轮转失败（360刀1/刀4 留痕；此前为静默 pass/break）",
                }, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, "logger.py:_m360_rollover_note 写完整性事件",
                   level="warning")
    return {"stage": _stage, "count": _n}


def m360_rollover_notes() -> dict:
    """轮转失败分档计数读数出口（测试/心跳可观察；无失败时为空 dict）。"""
    try:
        with _M360_ROLLOVER_NOTES_LOCK:
            return dict(_M360_ROLLOVER_NOTES)
    except Exception as _m360e:
        silent_exc(_m360e, "logger.py:m360_rollover_notes 读数失败",
                   level="warning")
        return {}

# ========== ★206批 刀1：树外 logger 根治（案C 显式名单注册） ==========
# 显式名单注册 16 个生产模块（来自205刀1普查黑盒名录；semantic_cache_l2 已在205改名入树故不在名单）。
# 仅当各名字自身无 handler 时挂共享 FileHandler（尊重模块自配）；propagate=False 避免回灌 root 造成
# 重复输出；console 面不单独挂 handler（由全局 WARNING 阈值兜底）。设计为一次性幂等装配。
_TREE_OUTSIDE_LOGGER_NAMES = (
    "Config", "config", "TaskOrchestrator", "nucleus.data.DataAccessLayer",
    "EvolutionLoop", "rt", "compat", "PollutionTagger", "IndexStore",
    "subagent_runner", "ReportPublishers", "rule_engine", "self_inspector",
    "PulseCodeLearner", "import_nodes_to_neo4j", "tools.measure_baseline",
)
_OUTSIDE_BRIDGE_INSTALLED = False
_OUTSIDE_BRIDGE_LOCK = threading.Lock()


_ASSEMBLY_COUNT = 0  # 215批·刀3：树外logger装配(已点亮)累计计数（每进程启动累加）

def install_tree_outside_logger_bridges() -> None:
    """★206批 刀1：一次性点亮 16 个树外生产 logger 的文件通道。

    仅对「自身无 handler」的名字挂共享 FileHandler；模块若已自配 handler 则尊重不动。
    ENABLE_LOGGER_ROOT_CAUSE=False 时整体跳过（零副作用）。调用幂等（全局只生效一次）。
    """
    global _OUTSIDE_BRIDGE_INSTALLED
    if _OUTSIDE_BRIDGE_INSTALLED:
        return
    with _OUTSIDE_BRIDGE_LOCK:
        if _OUTSIDE_BRIDGE_INSTALLED:
            return
        _OUTSIDE_BRIDGE_INSTALLED = True
    try:
        if _log_in_test_env():
            return  # 测试环境不挂载，避免污染生产 pulse.log
        if not bool(getattr(config, "ENABLE_LOGGER_ROOT_CAUSE", True)):
            return
        _log_file = os.path.join(_log_dir, getattr(config, "LOG_FILE", "pulse.log"))
        # ★356刀12③：**优先复用主 SafeRotatingFileHandler 实例**。
        #   原先这里 new 一个**普通 FileHandler** 直写同一个 pulse.log，与 SafeRotating
        #   各持一个文件偏移：SafeRotating 截断后另一方的偏移还在旧位置，下一次 flush
        #   会在旧偏移处写出一大段 NUL 空洞、把文件重新撑大 ⇒ 主文件永远轮不干净。
        # ★434刀2：主 handler 已换成 QueueHandler（挂 root），真正的文件 handler 由
        #   QueueListener 持有、存模块级 _FILE_HANDLER。优先复用它（否则这里会因
        #   root.handlers 里找不到 SafeRotatingFileHandler 而自建直写 handler，复现 NUL 空洞）。
        _fh = None
        try:
            if isinstance(_FILE_HANDLER, SafeRotatingFileHandler):
                _fh = _FILE_HANDLER
        except Exception as _he356:
            silent_exc(_he356, "logger.install_tree_outside_logger_bridges 复用主handler", level="debug")
        _m356_created = False
        if _fh is None:
            _fh = SafeRotatingFileHandler(
                _log_file,
                maxBytes=int(getattr(config, "LOG_MAX_BYTES", 10 * 1024 * 1024)),
                backupCount=int(getattr(config, "LOG_BACKUP_COUNT", 5)),
                encoding="utf-8")
            _m356_created = True
        if _m356_created:
            # 只在自建时设级别/格式；复用主 handler 时**不得**改它的格式（那是主链的）
            _fh.setLevel(_LEVEL_MAP.get(getattr(config, "LOG_LEVEL_FILE", LogLevel.DEBUG), logging.DEBUG))
            _fh.setFormatter(PulseFormatter(
                fmt="%(asctime)s [%(organ_tag)s] %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"))
            _fh.addFilter(SanitizingFilter(enabled=_m153_sanitizer_enabled()))
        _attached = 0
        # ★434刀2：树外 logger 也挂 QueueHandler（入队），与主链同队列、由
        #   QueueListener 单线程落盘，避免它们仍直挂 SafeRotatingFileHandler 同步写盘
        #   （那会让"全进程不抢文件锁"落空一半）。QueueHandler 不存在时回退挂文件 handler。
        _attach_handler = _QUEUE_HANDLER if _QUEUE_HANDLER is not None else _fh
        for _name in _TREE_OUTSIDE_LOGGER_NAMES:
            _lg = logging.getLogger(_name)
            if _lg.handlers:
                continue  # 尊重模块自配
            _lg.setLevel(logging.DEBUG)
            _lg.propagate = False
            _lg.addHandler(_attach_handler)  # 共享同一 handler 实例
            _attached += 1
        if _attached > 0:
            logging.getLogger("pulse.framework").info(
                "[树外logger根治] 已点亮 %d/%d 个树外生产 logger 文件通道"
                % (_attached, len(_TREE_OUTSIDE_LOGGER_NAMES)))
            global _ASSEMBLY_COUNT
            _ASSEMBLY_COUNT += _attached
            logging.getLogger("pulse.framework").info(
                "[装配计数] 本窗已点亮=%d 累计装配=%d 树外通道总数=%d"
                % (_attached, _ASSEMBLY_COUNT, len(_TREE_OUTSIDE_LOGGER_NAMES)))
    except Exception as e:
        silent_exc(e, where="nucleus.logger::install_tree_outside_logger_bridges", level="debug")


def _init_root_logger():
    """初始化根 logger 'pulse'，添加控制台和文件 handler（仅执行一次）"""
    global _log_initialized
    if _log_initialized:
        return
    with _log_init_lock:
        if _log_initialized:
            return
        _log_initialized = True

    root = logging.getLogger("pulse")
    root.setLevel(logging.DEBUG)

    # 控制台 handler
    console_level_str = getattr(config, "LOG_LEVEL_CONSOLE", LogLevel.INFO)
    console_level = _LEVEL_MAP.get(console_level_str, logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_format = PulseFormatter(
        fmt="[%(organ_tag)s] [%(levelname)s] %(message)s"
    )
    console_handler.setFormatter(console_format)
    console_handler.addFilter(
        SanitizingFilter(enabled=_m153_sanitizer_enabled()))
    root.addHandler(console_handler)

    # 文件 handler（带轮转）
    file_level_str = getattr(config, "LOG_LEVEL_FILE", LogLevel.DEBUG)
    file_level = _LEVEL_MAP.get(file_level_str, logging.DEBUG)
    log_file = os.path.join(_log_dir, getattr(config, "LOG_FILE", "pulse.log"))
    # ★PeriodicTestScheduler隔离：子进程通过 PULSE_LOG_FILE 环境变量指定独立日志文件，
    #   避免测试脚本的 VectorStore/VectorEncoder 日志混入主进程 pulse.log 造成混淆。
    _env_log_file = os.environ.get("PULSE_LOG_FILE", "").strip()
    if _env_log_file:
        log_file = _env_log_file
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    max_bytes = getattr(config, "LOG_MAX_BYTES", 10 * 1024 * 1024)
    backup_count = getattr(config, "LOG_BACKUP_COUNT", 5)

    # ★主线第49批 T3-3（P2-333）：用自定义 handler 代替标准库 handler
    file_handler = SafeRotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_format = PulseFormatter(
        fmt="%(asctime)s [%(organ_tag)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)
    file_handler.addFilter(SanitizingFilter(enabled=_m153_sanitizer_enabled()))

    # ★434刀2：文件写盘从「抢锁+落盘」降为「入队 + 后台单线程落盘」。
    #   SafeRotatingFileHandler 只由 QueueListener 单线程持有，业务线程只往
    #   queue.Queue 入队（无锁竞争），消除全进程最大串行点（历史上 L0 看门狗
    #   卡死就是等日志写盘）。console 仍直挂 root（写 stdout 非写盘，不动）。
    # ★473刀1：队列**有界**。原实现 `queue.Queue(-1)` 无上限 —— 消费端一旦变慢
    #   （磁盘慢 / 轮转卡住 / 全进程被换出），生产速度 > 消费速度就会无限涨内存。
    #   有界 + 满则丢新日志，保证"日志永不拖死业务"这条第一原则。
    _m473_qmax = int(getattr(config, "LOG_QUEUE_MAXSIZE", 20000) or 20000)
    _log_queue = queue.Queue(maxsize=_m473_qmax)
    _queue_handler = _M473NonBlockingQueueHandler(_log_queue)
    root.addHandler(_queue_handler)
    _queue_listener = logging.handlers.QueueListener(_log_queue, file_handler)
    _queue_listener.start()
    global _FILE_HANDLER, _QUEUE_LISTENER, _QUEUE_HANDLER
    _FILE_HANDLER = file_handler
    _QUEUE_LISTENER = _queue_listener
    _QUEUE_HANDLER = _queue_handler
    # ★第164批 刀A5（P1）：日志→告警落盘桥（ERROR/WARNING 入档）
    #   根因：既有告警链路仅信封/P0 异常驱动，ERROR/WARNING 日志流从未入档。
    #   挂一个去重+限流的 Handler 到根日志器，受 write_guard 约束（测试环境不挂载）。
    try:
        from nucleus.reporting.alert_log_bridge import install_alert_log_bridge
        install_alert_log_bridge(root)
    except (ImportError, OSError, ValueError, TypeError) as _alb_e:
        print(f"[logger] 告警落盘桥挂载失败（降级跳过）: {type(_alb_e).__name__}: {_alb_e}", file=sys.stderr)


    # ★F3：重复日志聚合降噪（受 config 开关控制，默认开启）
    # filter 仅挂在 file_handler 上，只作用于落盘的文件日志（避免误伤 console 实时输出）；
    # 仅聚合 DEBUG/INFO，WARNING 及以上不聚合。
    _agg_enabled = bool(getattr(config, "LOG_AGGREGATION_ENABLED", True))
    _agg_window = float(getattr(config, "LOG_AGGREGATION_WINDOW", 60.0))
    if _agg_enabled:
        file_handler.addFilter(LogAggregationFilter(window_seconds=_agg_window))

    # ★主线第42批 T1（P0-272）：日志留存治理（测试环境跳过，避免污染生产 logs/）
    if not _log_in_test_env():
        try:
            _removed = cleanup_old_logs()
            if _removed:
                root.info("[日志治理] 已清理超期日志 %d 个（>%d 天）: %s" % (
                    len(_removed), log_retention_days(),
                    ", ".join(os.path.basename(_x) for _x in _removed[:5])))
            _ir = check_log_integrity()
            if _ir.get("status") == "rollover":
                # ★D017 / 相关任务：框架内轮转自感知，正常行为，不告警
                root.info(
                    "[日志完整性] 日志轮转自感知：识别为框架内轮转（非外部截断），"
                    f"已留痕 {_LOG_INTEGRITY_FILE}")
            elif _ir.get("status") in ("truncated", "replaced", "missing"):
                _prv = (_ir.get("previous") or {}).get("size")
                _now = (_ir.get("current") or {}).get("size")
                root.warning(
                    "[日志完整性] ★检测到日志被外部{}：上次 size={} → 本次 size={}"
                    "（框架内无此逻辑，已留痕 {}）".format(_ir["status"], _prv, _now, _LOG_INTEGRITY_FILE))
        except Exception as _e:
            print(f"[logger] 日志治理初始化异常（忽略）: {type(_e).__name__}: {_e}", file=sys.stderr)

    # ★206批 刀1：树外 logger 根治（案C 显式名单注册）—— 一次性点亮 16 生产模块文件通道
    install_tree_outside_logger_bridges()


def flush_queue_logs() -> bool:
    """★434刀2：停机前 flush 日志队列，不丢日志。

    框架退出走 `os._exit`（跳过 atexit），QueueListener 线程不会因进程退出而自然
    flush —— 必须在退出路径显式调用本函数。`QueueListener.stop()` 会 put sentinel
    并 join（同步等队列里的记录全部落盘）。
    """
    global _QUEUE_LISTENER
    _ql = _QUEUE_LISTENER
    if _ql is None:
        return True
    try:
        _ql.stop()
    except Exception as _e:
        silent_exc(_e, where="nucleus.logger::flush_queue_logs", level="warning")
        return False
    _QUEUE_LISTENER = None
    return True

def get_log_queue_stats() -> dict:
    """★473刀1：上报日志队列的容量 / 积压 / 丢弃计数。

    供 health_snapshot、状态查询与巡检使用。**只读**，不改任何状态。
    `dropped` 是累计值（进程级），非零就说明消费端曾经跟不上，
    此时应该去看是不是磁盘慢 / 日志轮转卡住 / 全进程被换出。
    """
    _q = None
    _h = _QUEUE_HANDLER
    if _h is not None:
        _q = getattr(_h, "queue", None)
    if _q is None:
        return {"enabled": False, "size": 0, "maxsize": 0, "dropped": _M473_QUEUE_DROPPED,
                "fill_ratio": 0.0}
    _size = -1
    try:
        _size = int(_q.qsize())
    except Exception as _e:
        silent_exc(_e, where="nucleus.logger::get_log_queue_stats", level="debug")
    _max = int(getattr(_q, "maxsize", 0) or 0)
    return {
        "enabled": True,
        "size": _size,
        "maxsize": _max,
        "dropped": _M473_QUEUE_DROPPED,
        "fill_ratio": (round(_size / _max, 4) if _max > 0 and _size >= 0 else 0.0),
    }



def get_organ_logger(organ_name: str) -> logging.Logger:
    """获取指定器官的日志器，挂载在 pulse.organ 下"""
    _init_root_logger()
    return logging.getLogger(f"pulse.organ.{organ_name}")


def init_framework_logger() -> logging.Logger:
    """获取框架主流程日志器，挂载在 pulse.framework 下"""
    _init_root_logger()
    return logging.getLogger("pulse.framework")


def noise_reduction_enabled() -> bool:
    """★主线第22批 T5/P2-123：日志噪音治理总开关（默认 True）。

    开启时，以下 4 类**非关键**日志降级为 DEBUG（均附降级理由注释）：
      ① CallGraphAnalyzer 语法错误/解析失败跳过 —— 预期行为（坏文件用于验证错误处理）
      ② PulseCodeLearner 存量代码问题明细 —— 每次扫描重复命中同一批已知问题
      ③ InfoField 池重建残留计数已自动归零 —— 已自愈，无需告警
      ④ PatchManager 相同补丁历史失败去重跳过 —— 正常去重行为
    关闭时全部保持原 WARNING（零回归）。

    实现说明：各调用点用 ``(_logger.debug if noise_reduction_enabled()
    else _logger.warning)(...)`` 单行切换，避免为降噪改动控制流。
    """
    try:
        import config
        return bool(getattr(config, "ENABLE_LOG_NOISE_REDUCTION", True))
    except Exception as e:
        silent_exc(e, where="nucleus.logger::noise_reduction_enabled L708", level="debug")
        return True


def get_module_logger(module_name: str) -> logging.Logger:
    """
    获取非器官模块的日志器，挂载在 pulse.module 下。
    用于 PulseSnapshot、InfoField 等核心模块。
    """
    _init_root_logger()
    return logging.getLogger(f"pulse.module.{module_name}")


# ========== ★往期批次 相关任务② / R4-B22：冒烟隔离规矩 ==========
SMOKE_TAG = "[SMOKE]"
SMOKE_LOG_FILE = "smoke.log"


def get_smoke_logger(name: str = "smoke") -> logging.Logger:
    """★往期批次 相关任务②（R4-B22）：冒烟 / 合成指纹用例专用日志器。

    背景（内部分析 117 §3 实测）：停机窗 pulse.log 出现一行
        ``[指纹咨询硬闸] 指纹=a.py|m|silent_exception ...``
    ——那是**合成指纹**（file="a.py"、method="m"）驱动的冒烟产物，却被生产判据
    当成真实命中（对「INFO>=1」类判据构成**假阳性风险**，本次差点误导结论）。

    规矩：凡用合成指纹 / 假数据驱动的冒烟与单测，一律走本日志器，不得写进 pulse.log。

    三保险：
      ① 独立文件 ``logs/smoke.log``（与 pulse.log 物理隔离）；
      ② 每条前缀 ``[SMOKE]``（即便被复制粘贴到别处也一眼可辨）；
      ③ ``propagate = False``（绝不冒泡到 root 'pulse'，双重不污染）。

    用法（冒烟脚本 / 单测）：
        ``mod._module_logger = get_smoke_logger("my_smoke_case")``
    """
    _lg = logging.getLogger(f"pulse.smoke.{name}")
    _lg.setLevel(logging.DEBUG)
    _lg.propagate = False
    if not any(getattr(_h, "_pulse_smoke", False) for _h in _lg.handlers):
        try:
            os.makedirs(_log_dir, exist_ok=True)
            _h = logging.FileHandler(
                os.path.join(_log_dir, SMOKE_LOG_FILE), encoding="utf-8")
            _h.setLevel(logging.DEBUG)
            _h.setFormatter(logging.Formatter(
                "%(asctime)s " + SMOKE_TAG + " [%(name)s] %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"))
            _h._pulse_smoke = True
            _h.addFilter(SanitizingFilter(enabled=_m153_sanitizer_enabled()))
            _lg.addHandler(_h)
        except Exception as _se:
            print(f"[logger] smoke 日志句柄初始化失败(降级为纯内存): {type(_se).__name__}: {_se}", file=sys.stderr)
    return _lg


# ========== ★第158批 N-8（P1）：pytest / 验证脚本日志隔离 ==========
PYTEST_TAG = "[PYTEST]"
PYTEST_LOG_FILE = "pytest.log"


def get_pytest_logger(name: str = "pytest") -> logging.Logger:
    """★第158批 N-8（P1）：pytest / 验证脚本专用日志器。

    背景（路灯触发指令第 4 刀）：pytest 运行会触发大量**测试专用**日志
    （VectorStore 模拟磁盘写满 / 进程池「模拟重建失败」ERROR / CallGraphAnalyzer
    语法错误跳过 等），经 root FileHandler 写进**生产** ``logs/pulse.log``，
    淹没真实故障线索（原 conftest 用丢弃过滤器静音，虽保 pulse.log 干净但一并丢失）。

    N-8 改为物理隔离到 ``logs/pytest.log``，使 ``pulse.log`` 只收真实运行事件。

    三保险（与 get_smoke_logger 同构）：
      ① 独立文件 ``logs/pytest.log``（与 pulse.log 物理隔离）；
      ② 每条前缀 ``[PYTEST]``（即便被复制粘贴到别处也一眼可辨）；
      ③ ``propagate = False``（绝不冒泡到 root 'pulse'，双重不污染）。

    配合 ``tests/conftest.py`` 在 pytest 启动期设置的 ``PULSE_LOG_FILE`` 环境变量，
    框架根日志器（含 VectorStore / 进程池）在 pytest 运行期即直接落 ``logs/pytest.log``；
    本日志器供测试夹具 / 验证脚本显式写入诊断行。

    用法（测试夹具 / 验证脚本）：
        ``_lg = get_pytest_logger("my_case")``
    """
    _lg = logging.getLogger(f"pulse.pytest.{name}")
    _lg.setLevel(logging.DEBUG)
    _lg.propagate = False
    if not any(getattr(_h, "_pulse_pytest", False) for _h in _lg.handlers):
        try:
            os.makedirs(_log_dir, exist_ok=True)
            _h = logging.FileHandler(
                os.path.join(_log_dir, PYTEST_LOG_FILE), encoding="utf-8")
            _h.setLevel(logging.DEBUG)
            _h.setFormatter(logging.Formatter(
                "%(asctime)s " + PYTEST_TAG + " [%(name)s] %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"))
            _h._pulse_pytest = True
            _h.addFilter(SanitizingFilter(enabled=_m153_sanitizer_enabled()))
            _lg.addHandler(_h)
        except Exception as _pe:
            print(f"[logger] pytest 日志句柄初始化失败(降级为纯内存): {type(_pe).__name__}: {_pe}", file=sys.stderr)
    return _lg


# ========== ★主线第32批 T3（P2-190）：异常/调用位置动态获取 ==========
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def exc_location(depth: int = 1) -> str:
    """返回「相对项目根的路径:行号」，用于异常日志（替代硬编码行号）。

    ★主线第32批 T3/P2-190：项目里存在大量形如
        `self._log(..., "[主线N批] 静默异常已记录: org/x.py:315")`
    的日志 —— 行号**硬编码**，代码一改即失真（实测已有多处错位）。

    取值优先级：
      1) **有活动异常**（在 `except` 块内调用）→ 取异常链**最深帧**的行号，
         即「真正抛出异常的那一行」，比 except 块自身的位置更有诊断价值；
      2) **无活动异常** → 回退到调用方帧的当前行号（由 `depth` 指定上溯层数）。

    参数:
        depth: 无异常时，从本函数向上回溯的帧层数；1 = 调用 `exc_location()` 的那一行。
               若通过包装方法调用（如 `self._exc_loc()`），需相应增大。

    返回:
        `"<相对项目根路径>:<行号>"`（路径统一为正斜杠）；
        项目外路径保留绝对路径；任何异常一律回退 `"<unknown>:0"`，绝不抛出。

    示例:
        try:
            1 / 0
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
            # -> "[主线10批] 静默异常已记录: organs/body/PulseStomach.py:123"
            #    （123 = `1 / 0` 所在行，即异常抛出点）
    """
    try:
        _tb = sys.exc_info()[2]
        if _tb is not None:
            while _tb.tb_next is not None:      # 取最深帧 = 异常抛出点
                _tb = _tb.tb_next
            _file = _tb.tb_frame.f_code.co_filename
            _line = _tb.tb_lineno
        else:
            _fr = sys._getframe(max(1, int(depth)))
            _file = _fr.f_code.co_filename
            _line = _fr.f_lineno
        _file = str(_file).replace("\\", "/")
        try:
            from nucleus.data.path_utils import (
                normalize_relpath as _normalize_relpath,  # ★第55批 T3（跨盘安全）+ 第169批 C2 归一
            )

            _rel = _normalize_relpath(_file, _PROJECT_ROOT)
            if not _rel.startswith(".."):
                _file = _rel
        except Exception as e:
            silent_exc(e, where="nucleus.logger::exc_location L814", level="debug")
        return f"{_file}:{_line}"
    except Exception as e:
        silent_exc(e, where="nucleus.logger::exc_location L817", level="debug")
        return "<unknown>:0"
# _m49_t3_3_class_done
# _m49_t3_3_use_done

