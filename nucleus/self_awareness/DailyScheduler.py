# -*- coding: utf-8 -*-
"""自我认知引擎 · 每日低频调度器（★主线第37批 T1 / P2-210）。

背景（第36批核实结论）：
    `main.py:_init_self_awareness()` 只**注册**分析器，全库**无** `run_all_analyses()`
    生产调用点 → 设计 §5.6「每日凌晨自动生成报告」未落地，引擎"集成但未调度"。
    ★主线第77批摸底更正：本模块即为该缺口的修复（第37批 T1），
    且 `start_daily_schedule()` 已在 `main.py` 接线启动 →
    上方背景描述的是**修复前**状态，该债务可关闭。
    本模块补上这一环：框架运行时由一个**低优先级 daemon 线程**每天触发一次
    分析 + 报告落盘，让引擎真正"跑起来"。

设计要点：
    · 低频      默认每天 03:00（低峰期），每 60s 轮询一次判断是否到期；
    · 防重复    当天已有报告文件（`report_YYYYMMDD_*.txt`）→ 跳过（重启不重跑）；
    · 异常隔离  单个分析器失败由引擎自身容错（`_run_with_timeout` → timeout/异常
                只打 WARNING 并跳过）；调度层再兜一层 `except`，保证线程不退出；
    · 零副作用  `ENABLE_SELF_AWARENESS_DAILY_SCHEDULE=False` 时不创建任何线程；
    · 可测      `engine` / `now_fn` / `check_interval` / `output_dir` / `project_root`
                全部可注入，单元测试无需真等 24 小时。

线程模型：
    `threading.Thread(..., daemon=True)` + `threading.Event` 停止信号。
    框架退出走 `os._exit()`（跳过 atexit），daemon 线程随进程终止，无需显式清理；
    但仍提供 `stop()` 供测试与优雅关闭使用。
"""

import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("SelfAwarenessDailyScheduler")

#: 默认轮询间隔（秒）。每轮醒来判断一次「今天是否已过调度点且尚未执行」。
DEFAULT_CHECK_INTERVAL_SEC = 60.0


def _cfg(name: str, default: Any) -> Any:
    """读取 config 配置项（导入失败或缺失时回退默认值）。"""
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception as _e:  # pragma: no cover - 配置模块缺失属环境异常
        _module_logger.debug("读取配置 %s 失败，用默认值: %s: %s",
                             name, type(_e).__name__, _e)
        return default


def schedule_enabled() -> bool:
    """每日调度总开关（`ENABLE_SELF_AWARENESS_DAILY_SCHEDULE`，默认 True）。"""
    return bool(_cfg("ENABLE_SELF_AWARENESS_DAILY_SCHEDULE", True))


def schedule_hour() -> int:
    """每日触发时刻（时，0-23）。越界自动收敛到合法区间。"""
    try:
        _v = int(_cfg("SELF_AWARENESS_SCHEDULE_HOUR", 3))
    except Exception:
        _v = 3
    return min(max(_v, 0), 23)


def schedule_minute() -> int:
    """每日触发时刻（分，0-59）。越界自动收敛到合法区间。"""
    try:
        _v = int(_cfg("SELF_AWARENESS_SCHEDULE_MINUTE", 0))
    except Exception:
        _v = 0
    return min(max(_v, 0), 59)


def default_output_dir() -> str:
    """报告与画像的输出目录（相对项目根，默认 `data/self_awareness`）。"""
    return str(_cfg("SELF_AWARENESS_OUTPUT_DIR", "data/self_awareness"))


def _project_root() -> str:
    """项目根（本文件的 ../../..）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SelfAwarenessDailyScheduler:
    """每日一次触发自我认知分析 + 报告落盘的后台调度器。

    参数:
        engine: 自我认知引擎实例；None 时惰性取全局单例
            （`get_self_awareness_engine()`）。测试可注入假引擎。
        now_fn: 取当前时间的可调用对象，默认 `datetime.now`。测试注入以模拟时刻。
        check_interval: 轮询间隔（秒）；None 时读配置
            `SELF_AWARENESS_SCHEDULE_CHECK_INTERVAL_SEC`（默认 60）。
        output_dir: 落盘目录；None 时读配置 `SELF_AWARENESS_OUTPUT_DIR`。
        project_root: 项目根；None 时按本文件位置推导。

    示例:
        >>> s = SelfAwarenessDailyScheduler(engine=fake, now_fn=lambda: dt)
        >>> s.start()            # 启动后台线程（开关关闭时返回 False）
        True
        >>> s.run_once()         # 手动触发一次（测试/运维用）
        {'status': 'ok', ...}
    """

    def __init__(self, engine: Any = None,
                 now_fn: Callable[[], datetime] | None = None,
                 check_interval: float | None = None,
                 output_dir: str | None = None,
                 project_root: str | None = None) -> None:
        self._engine_obj = engine
        self._now_fn = now_fn or datetime.now
        self._check_interval_override = check_interval
        self._output_dir_override = output_dir
        self._project_root = project_root or _project_root()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._runs = 0
        self._skipped = 0
        self._errors = 0
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------ 配置
    def enabled(self) -> bool:
        """调度总开关是否开启。"""
        return schedule_enabled()

    def check_interval(self) -> float:
        """轮询间隔（秒）。"""
        if self._check_interval_override is not None:
            return float(self._check_interval_override)
        try:
            _v = float(_cfg("SELF_AWARENESS_SCHEDULE_CHECK_INTERVAL_SEC",
                            DEFAULT_CHECK_INTERVAL_SEC))
        except Exception:
            _v = DEFAULT_CHECK_INTERVAL_SEC
        return _v if _v > 0 else DEFAULT_CHECK_INTERVAL_SEC

    def output_dir(self) -> str:
        """落盘目录（绝对路径）。"""
        _p = self._output_dir_override or default_output_dir()
        if os.path.isabs(_p):
            return _p
        return os.path.join(self._project_root, _p.replace("/", os.sep))

    @staticmethod
    def _is_test_env() -> bool:
        """当前进程是否处于 pytest 环境（★用于**防止测试污染生产目录**）。

        ★主线第37批（交付期间实测）：验证过程中曾观测到 `data/self_awareness/`
        出现一组疑似**测试产出**的 `profile_/report_<ts>` 文件。根因是"某测试
        隐式使用了默认 output_dir（= 生产路径）"。本方法 + `_is_production_output_dir`
        构成防御：测试环境**拒绝写生产目录**。
        """
        return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules

    def _is_production_output_dir(self, path: str) -> bool:
        """给定目录是否等于**生产**输出目录（`SELF_AWARENESS_OUTPUT_DIR`，相对项目根）。"""
        try:
            _prod = os.path.join(self._project_root,
                                 default_output_dir().replace("/", os.sep))
            return (os.path.normcase(os.path.abspath(path))
                    == os.path.normcase(os.path.abspath(_prod)))
        except Exception as _e:
            _module_logger.debug("[自我认知调度] 生产目录比对失败: %s: %s",
                                 type(_e).__name__, _e)
            return False

    def _engine(self) -> Any:
        """惰性获取引擎实例（注入优先）。"""
        if self._engine_obj is not None:
            return self._engine_obj
        from nucleus.self_awareness import get_self_awareness_engine
        self._engine_obj = get_self_awareness_engine()
        return self._engine_obj

    # -------------------------------------------------------------- 生命周期
    def start(self) -> bool:
        """启动后台调度线程。

        返回:
            True  = 已启动；False = 开关关闭或线程已在运行（不会重复启动）。

        示例:
            >>> SelfAwarenessDailyScheduler().start()   # doctest: +SKIP
            True
        """
        if not self.enabled():
            _module_logger.info(
                "[自我认知调度] 开关关闭（ENABLE_SELF_AWARENESS_DAILY_SCHEDULE=False），不启动")
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            try:
                self._thread = threading.Thread(
                    target=self._loop, name="SelfAwarenessDaily", daemon=True)
                self._thread.start()
            except Exception as _e:
                _module_logger.warning(
                    "[自我认知调度] 线程启动失败: %s: %s", type(_e).__name__, _e)
                self._thread = None
                return False
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        """发出停止信号并等待线程退出（幂等）。

        参数:
            timeout: 等待秒数。

        返回:
            True 表示线程已不在运行（含从未启动的情况）。
        """
        self._stop.set()
        _t = self._thread
        if _t is not None and _t.is_alive():
            _t.join(timeout=timeout)
            return not _t.is_alive()
        return True

    def is_running(self) -> bool:
        """后台线程是否存活。"""
        _t = self._thread
        return bool(_t is not None and _t.is_alive())

    # ------------------------------------------------------------------ 核心
    def _due(self, now: datetime) -> bool:
        """当前时刻是否已过「今日调度点」（时:分 比较）。"""
        return (now.hour, now.minute) >= (schedule_hour(), schedule_minute())

    def _today_done(self, now: datetime | None = None) -> bool:
        """当天是否已生成过报告（★防重复调度的依据）。

        判据：输出目录内存在 `report_<YYYYMMDD>_*.txt`。
        找不到目录 / 读取失败 → 视为"未执行"（宁可多跑一次，不可漏跑）。
        """
        _now = now or self._now_fn()
        _day = _now.strftime("%Y%m%d")
        _d = self.output_dir()
        try:
            if not os.path.isdir(_d):
                return False
            _prefix = "report_%s_" % _day
            for _fn in os.listdir(_d):
                if _fn.startswith(_prefix) and _fn.endswith(".txt"):
                    return True
        except Exception as _e:
            _module_logger.debug("[自我认知调度] 检查当日报告失败（按未执行处理）: %s: %s",
                                 type(_e).__name__, _e)
        return False

    def run_once(self) -> dict[str, Any]:
        """执行一次「全量分析 + 画像落盘 + 报告落盘」。

        返回:
            dict，字段：`status`（"ok"/"error"/"empty"）、`elapsed_ms`、
            `profile_path`、`report_path`、`dimensions`、`error`。

        示例:
            >>> s = SelfAwarenessDailyScheduler(engine=fake, output_dir="/tmp/x")
            >>> s.run_once()["status"] in ("ok", "empty")
            True
        """
        _t0 = time.perf_counter()
        _now = self._now_fn()
        _ts = _now.strftime("%Y%m%d_%H%M%S")
        _out = self.output_dir()
        # ★防御（第37批）：pytest 环境**不得写生产目录**。对生产零影响
        #   （生产进程不在 pytest 环境），只拦截测试污染。
        # _m37_guard
        if self._is_test_env() and self._is_production_output_dir(_out):
            _module_logger.warning(
                "[自我认知调度] pytest 环境下检测到生产输出目录，已跳过落盘"
                "（防止测试污染）: %s", _out)
            self._skipped += 1
            self._last_result = {
                "status": "skipped_test_env", "elapsed_ms": 0.0,
                "profile_path": None, "report_path": None,
                "dimensions": 0, "error": None,
            }
            return self._last_result
        _res: dict[str, Any] = {
            "status": "ok", "elapsed_ms": 0.0, "profile_path": None,
            "report_path": None, "dimensions": 0, "error": None,
        }
        try:
            _eng = self._engine()
            _profile = _eng.run_all_analyses()
            _res["dimensions"] = self._count_dimensions(_profile, _eng)
            os.makedirs(_out, exist_ok=True)
            _pp = os.path.join(_out, "profile_%s.json" % _ts)
            if _eng.save_profile(_pp):
                _res["profile_path"] = _pp
            _rp = os.path.join(_out, "report_%s.txt" % _ts)
            _eng.generate_report(_rp)
            _res["report_path"] = _rp
            # ★主线第50批 T1（P0-1）：自认知报告 → ReportBus
            #   （`generate_report` 内部已发；此处补一条带调度上下文的记录）
            try:
                from nucleus.reporting.publishers import publish_generic as _m50_pg
                _m50_pg("self_cognition", "DailyScheduler.run_once",
                        content={"report_path": _rp, "dimensions": _res["dimensions"]})
            except Exception as _m50_dse:
                _module_logger.debug("[M50-T1] 日报发布失败（已忽略）: %s",
                                     type(_m50_dse).__name__)
            if _res["dimensions"] == 0:
                _res["status"] = "empty"
            # ★主线第42批 T2（P0-250）：补丁质量评估（L1 仅观测，不改变进化决策）。
            #   挂在每日任务上 = 低频（1 次/天）→ IO 可忽略；测试环境跳过（防污染生产 data/）。
            if not self._is_test_env():
                try:
                    # 函数内导入：规避巨型文件的存量 E402（第17批约定）
                    from nucleus.evolution.patch_quality_evaluator import evaluate_and_report
                    _pq = evaluate_and_report(logger=_module_logger)
                    if _pq:
                        _res["patch_quality"] = _pq["summary"]
                        # ★主线第50批 T1（P0-1）：补丁质量 → ReportBus
                        try:
                            from nucleus.reporting.publishers import (
                                publish_patch_quality as _m50_ppq)
                            _m50_ppq(_pq, generator="DailyScheduler.run_once")
                        except Exception as _m50_pqe:
                            _module_logger.debug(
                                "[M50-T1] 补丁质量发布失败（已忽略）: %s",
                                type(_m50_pqe).__name__)
                except Exception as _pqe:               # 评估失败不得影响调度
                    _module_logger.warning(
                        "[自我认知调度] 补丁质量评估失败（忽略）: %s: %s",
                        type(_pqe).__name__, _pqe)

            # ★第158批 上-A O-A2（P0）：分面成熟度台账登记（机读）。
            #   「每批自动登记」的落点：低频（1 次/天）→ IO 可忽略；
            #   台账件内部自带 test-env 保护，此处再套一层调度侧保护双保险。
            #   修复率/不可验证率由台账件按 N-9 口径自行采集，本处不另立判据。
            if not self._is_test_env():
                try:
                    # 函数内导入：规避巨型文件的存量 E402（第17批约定）
                    from nucleus.self_awareness.maturity_ledger import register_daily
                    _led = register_daily()
                    if isinstance(_led, dict) and not _led.get("error"):
                        _res["maturity_ledger"] = {
                            "score": _led.get("score"),
                            "level": _led.get("level"),
                            "rates": _led.get("rates"),
                            "probe_count": _led.get("probe_count"),
                        }
                except Exception as _mle:               # 登记失败不得影响调度
                    _module_logger.warning(
                        "[自我认知调度] 成熟度台账登记失败（忽略）: %s: %s",
                        type(_mle).__name__, _mle)

            # ★第158批 上-A T-自我审计-2（P1）：能力四态账本随批自动更新。
            #   与 O-A2 **共基建**——落盘复用 maturity_ledger，本处只登记。
            #   空转读数走 **v2 四归位**（存档/观测/事件/真空转）；71.1% 口径已证伪，
            #   不再作为基线。登记失败只 warning，不得影响调度。
            if not self._is_test_env():
                try:
                    from nucleus.self_awareness.capability_ledger import (
                        register_capability_ledger as _t2_reg)
                    _t2 = _t2_reg(batch="daily")
                    if isinstance(_t2, dict) and not _t2.get("error"):
                        _iv = _t2.get("idle_v2") or {}
                        _res["capability_ledger"] = {
                            "states": (_t2.get("capability") or {}).get("states"),
                            "by_destination": _iv.get("by_destination"),
                            "true_idle_rate": _iv.get("true_idle_rate"),
                        }
                except Exception as _t2e:               # 登记失败不得影响调度
                    _module_logger.warning(
                        "[自我认知调度] 能力四态账本登记失败（忽略）: %s: %s",
                        type(_t2e).__name__, _t2e)

            # ★主线第42批 T3（P1-265）：经验语义检索观测（L1 仅观测，不替换现有检索器）。
            #   仅在编码器**已就绪**时执行（observe_daily 内部自检）→ 首次加载不阻塞调度。
            if not self._is_test_env():
                try:
                    from nucleus.mnemosyne.experience_retriever import (
                        ExperienceRetriever,
                        analyze_pollution,
                        save_pollution_report,
                    )
                    _repo = analyze_pollution()
                    save_pollution_report(_repo)
                    # ★主线第50批 T1（P0-1）：污染报告 → ReportBus
                    #   消费者端：污染率 > 50% → 写入清洗建议待办（不自动清洗）
                    try:
                        from nucleus.reporting.publishers import (
                            publish_pollution as _m50_pp)
                        if isinstance(_repo, dict):
                            _m50_pp(_repo, generator="DailyScheduler.run_once")
                    except Exception as _m50_pe:
                        _module_logger.debug(
                            "[M50-T1] 污染报告发布失败（已忽略）: %s",
                            type(_m50_pe).__name__)
                    _er = ExperienceRetriever()
                    if _er.available():
                        _res["experience_retrieval"] = _er.observe_daily(
                            sample=3, logger=_module_logger)
                except Exception as _ere:               # 观测失败不得影响调度
                    _module_logger.warning(
                        "[自我认知调度] 经验检索观测失败（忽略）: %s: %s",
                        type(_ere).__name__, _ere)
            # ★主线第43批 T1（P1-255）：LLM 留存数据质量评估（L1 仅观测，每日 1 次）。
            #   测试环境跳过（防污染生产 data/llm_traces）。
            if not self._is_test_env():
                try:
                    from nucleus.llm.data_quality_evaluator import evaluate_and_report
                    _dq = evaluate_and_report(logger=_module_logger)
                    if _dq:
                        # ★主线第50批 T1（P0-1）：LLM 留存数据质量 → ReportBus
                        try:
                            from nucleus.reporting.publishers import (
                                publish_data_quality as _m50_pdq)
                            _m50_pdq(_dq, generator="DailyScheduler.run_once")
                        except Exception as _m50_dqe:
                            _module_logger.debug(
                                "[M50-T1] 数据质量发布失败（已忽略）: %s",
                                type(_m50_dqe).__name__)
                        _res["data_quality"] = {
                            "score": _dq.get("score"),
                            "records": _dq.get("scored_records"),
                            "purity": (_dq.get("purity") or {}).get("purity"),
                        }
                except Exception as _dqe:                   # 评估失败不得影响调度
                    _module_logger.warning(
                        "[自我认知调度] 数据质量评估失败（忽略）: %s: %s",
                        type(_dqe).__name__, _dqe)

            # ★主线第43批 T3（P1-256）：模型自更新器框架（每日检查增量；仅登记版本，不训练）。
            if not self._is_test_env():
                try:
                    from nucleus.llm.model_self_updater import ModelSelfUpdater
                    _mu = ModelSelfUpdater()
                    _chk = _mu.check_for_updates()
                    _res["model_update_check"] = {
                        "current": _chk.get("current_version"),
                        "incremental_due": _chk.get("incremental_due"),
                        "full_retrain_due": _chk.get("full_retrain_due"),
                    }
                    if _chk.get("incremental_due"):
                        _up = _mu.incremental_update()
                        _module_logger.info(
                            "[模型自更新] 状态=%s 版本=%s",
                            _up.get("status"), _up.get("version"))
                    if _chk.get("full_retrain_due"):
                        _rp = _mu.full_retrain()
                        _module_logger.info(
                            "[模型自更新] 全量重训计划已产出（不执行训练）: 版本=%s",
                            _rp.get("version"))
                except Exception as _mue:                   # 自更新失败不得影响调度
                    _module_logger.warning(
                        "[自我认知调度] 模型自更新检查失败（忽略）: %s: %s",
                        type(_mue).__name__, _mue)
            _res["elapsed_ms"] = round((time.perf_counter() - _t0) * 1000.0, 2)
            _module_logger.info(
                "[自我认知调度] 每日分析完成: %d 维度 / %.1fms → %s",
                _res["dimensions"], _res["elapsed_ms"], _rp)
            self._runs += 1
        except Exception as _e:
            _res["status"] = "error"
            _res["error"] = "%s: %s" % (type(_e).__name__, _e)
            _res["elapsed_ms"] = round((time.perf_counter() - _t0) * 1000.0, 2)
            self._errors += 1
            _module_logger.warning("[自我认知调度] 每日分析失败: %s", _res["error"])
        self._last_result = _res
        return _res

    @staticmethod
    def _count_dimensions(profile: Any, engine: Any) -> int:
        """统计本次分析实际产出的维度数（用于日志与状态判定）。"""
        try:
            _stats = engine.get_stats().get("last_run", {}) or {}
            _ok = [k for k, v in _stats.items()
                   if isinstance(v, dict) and v.get("status") == "ok"]
            if _ok:
                return len(_ok)
        except Exception as _e:
            _module_logger.debug("[自我认知调度] 读取 stats 失败: %s: %s",
                                 type(_e).__name__, _e)
        try:
            _prof = profile if profile is not None else engine.get_profile()
            return len([1 for _f in ("code_health", "runtime_health", "knowledge_health",
                                     "evolution_health", "production_consumption",
                                     "fake_loops")
                        if getattr(_prof, _f, None)])
        except Exception as _e:
            _module_logger.debug("[自我认知调度] 统计维度失败: %s: %s",
                                 type(_e).__name__, _e)
            return 0

    def _loop(self) -> None:
        """后台循环：到期且当日未执行 → 跑一次；异常不退出线程。"""
        _h, _m = schedule_hour(), schedule_minute()
        _iv = self.check_interval()
        _module_logger.info(
            "[自我认知调度] 后台调度线程已启动（每日 %02d:%02d 触发，轮询 %.0fs，daemon）",
            _h, _m, _iv)
        while not self._stop.is_set():
            try:
                _now = self._now_fn()
                if self._due(_now):
                    if self._today_done(_now):
                        self._skipped += 1
                        _module_logger.debug(
                            "[自我认知调度] 当日（%s）已生成报告，跳过本次",
                            _now.strftime("%Y-%m-%d"))
                    else:
                        self.run_once()
            except Exception as _e:
                # 调度层兜底：任何未预期异常都不得让线程退出
                _module_logger.warning(
                    "[自我认知调度] 调度循环异常（线程继续）: %s: %s",
                    type(_e).__name__, _e)
            self._stop.wait(_iv)
        _module_logger.info("[自我认知调度] 后台调度线程已退出")

    # ------------------------------------------------------------------ 统计
    def get_stats(self) -> dict[str, Any]:
        """调度统计（执行次数 / 跳过次数 / 失败次数 / 最近结果）。"""
        return {
            "enabled": self.enabled(),
            "running": self.is_running(),
            "runs": self._runs,
            "skipped": self._skipped,
            "errors": self._errors,
            "schedule": "%02d:%02d" % (schedule_hour(), schedule_minute()),
            "check_interval_sec": self.check_interval(),
            "output_dir": self.output_dir(),
            "last_result": dict(self._last_result) if self._last_result else None,
        }


# ---------------------------------------------------------------- 进程内单例
_scheduler: SelfAwarenessDailyScheduler | None = None
_scheduler_lock = threading.Lock()


def get_daily_scheduler() -> SelfAwarenessDailyScheduler:
    """取进程内调度器单例（惰性创建）。"""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = SelfAwarenessDailyScheduler()
        return _scheduler


def start_daily_schedule() -> bool:
    """启动进程内每日调度（供 `main.py` 接线调用）。

    返回:
        True 表示线程已启动；False 表示开关关闭或已在运行。
        永不抛异常（接线点属启动路径，失败不得影响框架启动）。
    """
    try:
        return get_daily_scheduler().start()
    except Exception as _e:
        _module_logger.warning("[自我认知调度] 启动失败（不影响框架）: %s: %s",
                               type(_e).__name__, _e)
        return False


def stop_daily_schedule() -> None:
    """停止进程内每日调度（幂等，永不抛异常）。"""
    try:
        _s = _scheduler
        if _s is not None:
            _s.stop()
    except Exception as _e:
        _module_logger.debug("[自我认知调度] 停止失败: %s: %s", type(_e).__name__, _e)


def reset_daily_scheduler() -> None:
    """重置单例（测试用；先停线程再丢弃）。"""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            try:
                _scheduler.stop()
            except Exception as _e:
                _module_logger.debug("[自我认知调度] 重置时停止失败: %s: %s",
                                     type(_e).__name__, _e)
        _scheduler = None
# _m50_t1_ds1_done
# _m50_t1_ds2_done
# _m50_t1_ds3_done
# _m50_t1_ds4_done
