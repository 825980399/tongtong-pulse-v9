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
import re
import sys
import threading
import time

import config
from nucleus.const import LogLevel
from nucleus._silent_except import silent_exc
from nucleus.logging.sanitizer import sanitize, SanitizingFilter

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
        silent_exc(_e, "_m153_sanitizer_enabled")
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
            record.organ_tag = short
        elif name == "pulse.framework":
            record.organ_tag = "框架"
        elif name.startswith("pulse.module."):
            # 非器官模块，保留模块原名作为标签
            record.organ_tag = name.split(".")[-1]
        else:
            record.organ_tag = name
        return super().format(record)
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
        silent_exc(e, where="nucleus.logger::_log_in_test_env L178")
        return False


def log_retention_days() -> int:
    """超期日志保留天数；返回 0 表示不清理（开关关闭 / 配置非法）。"""
    try:
        if not bool(getattr(config, "ENABLE_LOG_RETENTION_CLEANUP", True)):
            return 0
        _d = int(getattr(config, "LOG_RETENTION_DAYS", 7))
        return _d if _d > 0 else 0
    except Exception as e:
        silent_exc(e, where="nucleus.logger::log_retention_days L189")
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
    except OSError:
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
        except OSError:
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
        silent_exc(e, where="nucleus.logger::_fingerprint L245")
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
        print("[logger] 完整性事件写入失败: %s: %s" % (type(_e).__name__, _e),
              file=sys.stderr)


def _log_max_bytes() -> int:
    """★往期批次 T146-8：单日志文件轮转上限（bytes）；不可读/未配置 → 0（表示未知）。"""
    try:
        _v = int(getattr(config, "LOG_MAX_BYTES", 0) or 0)
    except Exception:
        # ★相关任务：门禁友好——非静默上报（配置读取失败按「未知」处理，不臆断）
        logging.getLogger(__name__).debug("LOG_MAX_BYTES 读取失败，按未知处理")
        return 0
    return _v if _v > 0 else 0


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
        print("[logger] 轮转后状态复位失败: %s: %s" % (type(_e).__name__, _e),
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
        pass


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
            print("[logger] 日志状态写入失败: %s: %s" % (type(_e).__name__, _e),
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
class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """★第49批 T3-3：轮转失败不打印调用栈、不中断写入。"""

    def doRollover(self) -> None:      # noqa: N802 (logging 基类命名)
        # ★主线第59批 T1（方案B）：先尝试标准库 rename 式轮转，
        #   失败（Windows 文件锁 → WinError 32）后改用「复制+截断」兜底，
        #   从根本上避免重命名被占用文件。
        # ★D017 / 相关任务：轮转成功后写唯一标记，供 check_log_integrity 自感知（避免误判外部截断）。
        _base = self.baseFilename
        _dir = os.path.dirname(_base) or "."
        _prev_fp = _fingerprint(_base)
        _prev_size = _prev_fp.get("size") if isinstance(_prev_fp, dict) else None
        _retry = 3
        _wait = 0.2
        try:
            _retry = int(getattr(config, "LOG_ROLLOVER_RETRY", 3))
            _wait = float(getattr(config, "LOG_ROLLOVER_RETRY_WAIT", 0.2))
        except Exception as _rc:
            _wait = 0.2
            _log_rollover_cfg_error(_rc)
        # 阶段一：rename 式重试（文件可能瞬间被释放）
        for _attempt in range(max(1, _retry)):
            try:
                super().doRollover()
                _write_rollover_marker(_dir, _base + ".1", _prev_size, _base)
                return
            except PermissionError as _pe:
                if _attempt < max(1, _retry) - 1:
                    # ★主线第74批 T4：指数退避（0.2/0.4/0.8…上限2s），避免高频重试刷屏
                    time.sleep(min(_wait * (2 ** _attempt), 2.0))
                    continue
                break
            except OSError as _oe:
                break
        # 阶段二：方案B（复制+截断）——不重命名打开的文件，规避 Windows 锁
        try:
            self._copy_truncate_rollover()
            _write_rollover_marker(_dir, _base + ".1", _prev_size, _base)
            return
        except Exception as _ce:
            # 彻底降级：记一条冷却 WARNING 后继续写当前文件（不中断运行）
            _warn_rollover_blocked_cooled(_ce)
            return

    def _copy_truncate_rollover(self) -> None:
        # ★主线第59批 T1（方案B）：复制+截断式轮转，规避 Windows 文件锁。
        # 标准库 doRollover 用 os.rename 重命名「当前打开的日志文件」，
        # Windows 下该文件被本进程持有句柄（未授予 FILE_SHARE_DELETE）→
        # PermissionError[WinError 32]。本方法不重命名打开的文件：
        # 先把当前内容复制为 .1 备份，再就地截断当前文件为空。
        # 调用点已持 self.lock（emit 内），无需额外加锁防并发写入。
        import io as _io
        _base = self.baseFilename
        _max = 1
        try:
            _max = int(getattr(config, "LOG_BACKUP_COUNT", 5))
        except Exception:
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
                _log_rollover_cfg_error(_se)
        # 2) 复制当前内容 → .1（append 模式流不可读，另开读句柄读取已 flush 的磁盘内容）
        _backup = f"{_base}.1"
        try:
            if os.path.exists(_backup):
                os.remove(_backup)
        except OSError:
            pass
        self.stream.flush()
        with _io.open(_base, "rb") as _fin:
            with _io.open(_backup, "wb") as _fout:
                _fout.write(_fin.read())
        # 3) 截断当前文件（self.stream 已持有句柄，就地清空，不重命名）
        self.stream.seek(0)
        self.stream.truncate(0)
        self.stream.flush()

def _log_rollover_cfg_error(exc: BaseException) -> None:
    """轮转配置读取失败的诊断（避免静默）。"""
    print(f"[Logger] 轮转重试配置读取失败: {type(exc).__name__}: {exc}")


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
    root.addHandler(file_handler)

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
                    "已留痕 %s" % _LOG_INTEGRITY_FILE)
            elif _ir.get("status") in ("truncated", "replaced", "missing"):
                _prv = (_ir.get("previous") or {}).get("size")
                _now = (_ir.get("current") or {}).get("size")
                root.warning(
                    "[日志完整性] ★检测到日志被外部%s：上次 size=%s → 本次 size=%s"
                    "（框架内无此逻辑，已留痕 %s）"
                    % (_ir["status"], _prv, _now, _LOG_INTEGRITY_FILE))
        except Exception as _e:
            print("[logger] 日志治理初始化异常（忽略）: %s: %s"
                  % (type(_e).__name__, _e), file=sys.stderr)


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
        silent_exc(e, where="nucleus.logger::noise_reduction_enabled L708")
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
            from nucleus.data.path_utils import safe_relpath as _safe_relpath  # ★第55批 T3（跨盘安全）

            _rel = _safe_relpath(_file, _PROJECT_ROOT).replace("\\", "/")
            if not _rel.startswith(".."):
                _file = _rel
        except Exception as e:
            silent_exc(e, where="nucleus.logger::exc_location L814")
        return f"{_file}:{_line}"
    except Exception as e:
        silent_exc(e, where="nucleus.logger::exc_location L817")
        return "<unknown>:0"
# _m49_t3_3_class_done
# _m49_t3_3_use_done
