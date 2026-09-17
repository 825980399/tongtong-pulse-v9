# -*- coding: utf-8 -*-
"""
SelfAwarenessEngine.py —— PHASE18 阶段一：自我认知引擎核心

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 提供"自我认知画像"的统一数据模型与调度框架——把分散在各处的静态分析能力
      （代码健康 / 运行时健康 / 知识健康 / 进化健康 / 产出-消费 / 虚假闭环）汇总为
      一份可序列化、可合并、可报告的 SelfAwarenessProfile。
机制: 引擎以「分析器注册表」组织能力：任何模块通过 register_analyzer 注册
      `func(engine) -> dict`，`run_all_analyses()` 按注册顺序执行并汇总到画像。
      单个分析器在独立 daemon 线程中执行并 `join(timeout)`，超时即放弃等待、
      丢弃结果并打 WARNING —— 保证慢分析器**不拖垮调用方**。
定位: PHASE18 的**地基**（阶段一核心）。★红线：全部为静态分析，不触发任何运行时
      保存/加载，不参与框架主流程；默认开关 `ENABLE_SELF_AWARENESS_ENGINE=True`
      但**启动时只初始化、不自动分析**（避免拖慢启动）。

开关（config）:
    ENABLE_SELF_AWARENESS_ENGINE  总开关（默认 True）；关闭时 run_all_analyses 直接返回
    SELF_AWARENESS_TIMEOUT_SEC    单个分析器超时（默认 30 秒）
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from nucleus.logger import get_module_logger

_logger = get_module_logger("SelfAwarenessEngine")

#: 分析器签名：接收引擎，返回该维度的分析结果 dict。
AnalyzerFunc = Callable[["SelfAwarenessEngine"], dict]

#: 画像中所有"字典型"字段名（汇总结果的合法落点）。
_PROFILE_DICT_FIELDS = (
    "code_health",
    "runtime_health",
    "knowledge_health",
    "evolution_health",
    "production_consumption",
    "fake_loops",
    # ★主线第20批 T3：PHASE18 阶段二「动静结合」的两个动态维度
    "runtime_events",
    "organ_activity",
    # ★主线第21批 T3：跨文件调用图 → 代码结构健康度（P3-3）
    "call_graph_health",
    "extra",
)


def _engine_enabled() -> bool:
    """读取引擎总开关（默认 True）。config 不可用时按开启处理。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_SELF_AWARENESS_ENGINE", True))
    except Exception as e:  # 开关读取不得影响业务
        _logger.debug("读取自我认知引擎开关失败，按开启处理: %s: %s",
                      type(e).__name__, e)
        return True


def _timeout_sec() -> float:
    """读取单分析器超时秒数（默认 30）。"""
    try:
        import config
        return float(getattr(config, "SELF_AWARENESS_TIMEOUT_SEC", 30))
    except Exception as e:
        _logger.debug("读取分析器超时配置失败，用默认 30s: %s: %s",
                      type(e).__name__, e)
        return 30.0


def _project_root() -> str:
    """项目根目录（`nucleus/self_awareness/xxx.py` 上溯 3 层）。

    ★注意：这是**本文件位置**推导的"代码所在项目"，与"运行时 cwd"无关，
    静态分析类集成（LogAnalyzer / CodeReviewEngine）都应基于它。
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _integration_enabled(attr: str, default: bool = True) -> bool:
    """读取某个整合开关（默认 True；config 不可用时按默认值）。"""
    try:
        import config
        return bool(getattr(config, attr, default))
    except Exception as e:  # 开关读取不得影响业务
        _logger.debug("读取整合开关 %s 失败，按默认值 %s: %s: %s",
                      attr, default, type(e).__name__, e)
        return default


#: 审查/统计时应排除的目录名（备份 / 临时 / 缓存）
_NON_SOURCE_SEGMENTS = ("tmp", "__pycache__", ".git", ".pytest_cache",
                        "node_modules", "venv", "logs", "data")


def _is_non_source_path(file_path: Any) -> bool:
    """判断路径是否属于「非源码」目录（备份 / 临时 / 缓存），用于过滤审查结果。

    ★主线第19批 T4 实测：CodeReviewEngine 的 ruff 全项目扫描会包含
    `.bak_batchN/` 备份目录，若不过滤会把它算进问题数（污染基线）。
    """
    _p = str(file_path or "").replace("\\", "/").lower()
    if not _p:
        return False
    for _seg in _p.split("/"):
        if not _seg:
            continue
        if _seg.startswith(".bak") or _seg in _NON_SOURCE_SEGMENTS:
            return True
    return False


def _dig(data: Any, keys: list, default: Any = 0) -> Any:
    """安全取嵌套字典值（任一层缺失或类型不符都返回 default）。"""
    _cur = data
    for _k in keys:
        if not isinstance(_cur, dict):
            return default
        _cur = _cur.get(_k)
        if _cur is None:
            return default
    return _cur


def _now_iso() -> str:
    """当前本地时间 ISO 串（秒精度）。"""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    except Exception as e:
        _logger.debug("时间格式化失败: %s: %s", type(e).__name__, e)
        return ""


@dataclass
class SelfAwarenessProfile:
    """自我认知画像（统一数据模型）。

    所有字段均有默认值，保证向后兼容：旧版本 JSON 缺字段时 `from_dict` 仍可解析。
    """

    timestamp: str = ""
    code_health: dict = field(default_factory=dict)
    runtime_health: dict = field(default_factory=dict)
    knowledge_health: dict = field(default_factory=dict)
    evolution_health: dict = field(default_factory=dict)
    production_consumption: dict = field(default_factory=dict)
    fake_loops: dict = field(default_factory=dict)
    #: ★主线第20批 T3：运行时事件统计（EventTap 旁路数据，动静结合）
    runtime_events: dict = field(default_factory=dict)
    #: ★主线第20批 T3：器官活跃度分析（基于 runtime_events.by_source）
    organ_activity: dict = field(default_factory=dict)
    #: ★主线第21批 T3：跨文件调用图健康度（孤立/热点/循环/深度/耦合）
    call_graph_health: dict = field(default_factory=dict)
    summary: str = ""
    #: 未归类分析器结果的落点（扩展位，保证任意分析器都不会丢结果）
    extra: dict = field(default_factory=dict)
    # ★主线第38批 T1（P2-213）：综合评分 / 健康等级 / 最严重问题
    #   · overall_score：各**可用**维度评分的加权平均（权重可配置，默认等权）；
    #     可用维度 < 2 个时为 None（不可用维度不参与，见 compute_overall_score）。
    #   · health_level：≥80 healthy / 60-79 moderate / 40-59 concerning /
    #     <40 critical；overall_score 为 None 时 "unknown"。
    #   · top_issues：跨维度按归一化 severity 排序的前 N 条（默认 5）。
    # _m38_t1t2
    overall_score: float | None = None
    health_level: str = "unknown"
    top_issues: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """转可序列化字典。"""
        return {
            "timestamp": self.timestamp,
            "code_health": dict(self.code_health or {}),
            "runtime_health": dict(self.runtime_health or {}),
            "knowledge_health": dict(self.knowledge_health or {}),
            "evolution_health": dict(self.evolution_health or {}),
            "production_consumption": dict(self.production_consumption or {}),
            "fake_loops": dict(self.fake_loops or {}),
            "runtime_events": dict(self.runtime_events or {}),
            "organ_activity": dict(self.organ_activity or {}),
            "call_graph_health": dict(self.call_graph_health or {}),
            "summary": self.summary,
            "extra": dict(self.extra or {}),
            # ★主线第38批 T1（P2-213）：非字典型字段（标量 + 列表）
            "overall_score": self.overall_score,
            "health_level": self.health_level,
            "top_issues": [dict(x) for x in (self.top_issues or [])
                           if isinstance(x, dict)],
        }

    @classmethod
    def from_dict(cls, data: Any) -> SelfAwarenessProfile:
        """从字典构造（容错：非 dict 入参返回空画像，缺字段用默认值）。"""
        if not isinstance(data, dict):
            _logger.debug("SelfAwarenessProfile.from_dict 收到非字典入参: %r",
                          type(data).__name__)
            return cls()
        _p = cls()
        _p.timestamp = str(data.get("timestamp", "") or "")
        _p.summary = str(data.get("summary", "") or "")
        for _f in _PROFILE_DICT_FIELDS:
            _v = data.get(_f)
            setattr(_p, _f, dict(_v) if isinstance(_v, dict) else {})
        # ★主线第38批 T1（P2-213）：overall_score/health_level/top_issues 单独容错解析
        _os = data.get("overall_score")
        _p.overall_score = (float(_os) if isinstance(_os, (int, float))
                            and not isinstance(_os, bool) else None)
        _hl = str(data.get("health_level", "") or "").strip()
        _p.health_level = _hl if _hl else "unknown"
        _ti = data.get("top_issues")
        _p.top_issues = ([dict(x) for x in _ti if isinstance(x, dict)]
                         if isinstance(_ti, list) else [])
        return _p

    def merge(self, other: Any) -> SelfAwarenessProfile:
        """与另一份画像合并（other 覆盖同名字段；返回 self 便于链式调用）。

        · 字典字段：逐键覆盖（other 优先）
        · timestamp：取较新（字符串比较，ISO 格式天然可比）
        · summary：非空则拼接
        """
        if other is None:
            return self
        _o = other if isinstance(other, SelfAwarenessProfile) else \
            SelfAwarenessProfile.from_dict(other)
        for _f in _PROFILE_DICT_FIELDS:
            _cur = getattr(self, _f, None) or {}
            _new = getattr(_o, _f, None) or {}
            if _new:
                _merged = dict(_cur)
                _merged.update(_new)
                setattr(self, _f, _merged)
        if _o.timestamp and _o.timestamp > (self.timestamp or ""):
            self.timestamp = _o.timestamp
        if _o.summary:
            self.summary = ("%s | %s" % (self.summary, _o.summary)).strip(" |") \
                if self.summary else _o.summary
        # ★主线第38批 T1（P2-213）：标量/列表字段的合并语义（other 优先，空值不覆盖）
        if getattr(_o, "overall_score", None) is not None:
            self.overall_score = _o.overall_score
        if getattr(_o, "health_level", "") and _o.health_level != "unknown":
            self.health_level = _o.health_level
        if getattr(_o, "top_issues", None):
            self.top_issues = [dict(x) for x in _o.top_issues
                               if isinstance(x, dict)]
        return self


class SelfAwarenessEngine:
    """自我认知引擎（可多实例；生产建议用 get_self_awareness_engine() 单例）。"""

    def __init__(self, timeout_sec: float | None = None) -> None:
        self._lock = threading.RLock()
        self._analyzers: list[tuple[str, AnalyzerFunc, str]] = []
        self._profile = SelfAwarenessProfile(timestamp=_now_iso())
        self._last_run: dict[str, dict[str, Any]] = {}
        self._run_count = 0
        self._skipped_off = 0
        self._timeout_sec = timeout_sec

    # ------------------------------------------------------------------
    # 分析器注册
    # ------------------------------------------------------------------
    def register_analyzer(self, name: str, analyzer_func: AnalyzerFunc,
                          target_field: str | None = None,
                          replace: bool = False) -> bool:
        """注册一个分析器。

        Args:
            name: 分析器唯一名（如 "production_consumption"）。
            analyzer_func: 签名 `(engine) -> dict`。
            target_field: 结果写入画像的哪个字段；None 时按 name 自动匹配
                （匹配不到则落入 `extra`）。
            replace: 同名分析器是否替换（默认 False 时忽略重复注册）。

        Returns:
            是否成功注册（重复且未 replace 时返回 False）。
        """
        if not callable(analyzer_func):
            raise TypeError("analyzer_func 必须是可调用对象")
        _name = str(name or "").strip()
        if not _name:
            raise ValueError("分析器 name 不能为空")
        _fld = target_field if target_field in _PROFILE_DICT_FIELDS else (
            _name if _name in _PROFILE_DICT_FIELDS else "")
        with self._lock:
            _exists = any(n == _name for n, _, _ in self._analyzers)
            if _exists and not replace:
                _logger.debug("分析器已存在，忽略重复注册: %s", _name)
                return False
            if _exists:
                self._analyzers = [x for x in self._analyzers if x[0] != _name]
            self._analyzers.append((_name, analyzer_func, _fld))
        return True

    def unregister_analyzer(self, name: str) -> bool:
        """移除某个分析器。"""
        with self._lock:
            _before = len(self._analyzers)
            self._analyzers = [x for x in self._analyzers if x[0] != name]
            return len(self._analyzers) != _before

    def list_analyzers(self) -> list[str]:
        """已注册分析器名（按注册顺序）。"""
        with self._lock:
            return [n for n, _, _ in self._analyzers]

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def _run_with_timeout(self, name: str, func: AnalyzerFunc,
                          timeout: float) -> tuple[Any, str | None]:
        """在独立 daemon 线程中执行分析器；超时返回 (None, "timeout")。

        ★真正的 Python 单线程无法被外部中断，故采用「放弃等待」语义：
        超时后不再取用该线程结果（线程为 daemon，不阻塞进程退出）。
        """
        _box: dict[str, Any] = {}
        _err: dict[str, Any] = {}

        def _target() -> None:
            try:
                _box["value"] = func(self)
            except Exception as e:  # 单分析器失败不得影响整体
                _err["error"] = e

        _t = threading.Thread(target=_target, name="SelfAwareness-%s" % name,
                              daemon=True)
        _t.start()
        _t.join(max(0.001, float(timeout)))
        if _t.is_alive():
            return None, "timeout"
        if "error" in _err:
            _e = _err["error"]
            return None, "error: %s: %s" % (type(_e).__name__, _e)
        return _box.get("value"), None

    def run_all_analyses(self, scope: str = "all") -> SelfAwarenessProfile:
        """执行全部（或指定）分析器并汇总为新的画像。

        Args:
            scope: "all" 执行全部；否则只执行 name 或 target_field 命中的分析器。

        Returns:
            汇总后的 SelfAwarenessProfile（同时成为引擎当前画像）。
        """
        if not _engine_enabled():
            with self._lock:
                self._skipped_off += 1
            _logger.debug("自我认知引擎开关关闭，run_all_analyses 直接返回")
            return self.get_profile()

        with self._lock:
            _analyzers = list(self._analyzers)
        _timeout = self._timeout_sec if self._timeout_sec is not None else _timeout_sec()
        _profile = SelfAwarenessProfile(timestamp=_now_iso())
        _runs: dict[str, dict[str, Any]] = {}

        for _name, _func, _fld in _analyzers:
            if scope != "all" and scope not in (_name, _fld):
                continue
            _t0 = time.perf_counter()
            _value, _err = self._run_with_timeout(_name, _func, _timeout)
            _elapsed = round((time.perf_counter() - _t0) * 1000.0, 2)
            _runs[_name] = {
                "status": "ok" if _err is None else _err,
                "elapsed_ms": _elapsed,
                "field": _fld or _name,
            }
            if _err == "timeout":
                _logger.warning(
                    "分析器 %s 超时(>%.1fs)已跳过（结果丢弃，不阻塞调用方）",
                    _name, _timeout)
            elif _err is not None:
                _logger.warning("分析器 %s 执行失败: %s", _name, _err)
            if _err is None and isinstance(_value, dict):
                _key = _fld or _name
                if _key in _PROFILE_DICT_FIELDS:
                    setattr(_profile, _key, dict(_value))
                else:
                    _profile.extra[_key] = dict(_value)

        # ★主线第38批 T1（P2-213）：汇总后计算综合评分 / 健康等级 / 最严重问题
        if _overall_enabled():
            try:
                _apply_overall(_profile)
            except Exception as e:      # 计算失败不得影响画像汇总
                _logger.warning("综合评分计算失败（保持缺省）: %s: %s",
                                type(e).__name__, e)
        _profile.summary = self._build_summary(_profile)
        with self._lock:
            self._profile = _profile
            self._last_run = _runs
            self._run_count += 1
        return _profile

    def run_analyzer(self, name: str) -> dict[str, Any] | None:
        """单独执行一个分析器，返回其结果（不改变当前画像）。"""
        with self._lock:
            _hit = next((x for x in self._analyzers if x[0] == name), None)
        if _hit is None:
            return None
        _timeout = self._timeout_sec if self._timeout_sec is not None else _timeout_sec()
        _value, _err = self._run_with_timeout(_hit[0], _hit[1], _timeout)
        if _err is not None:
            _logger.debug("单独执行分析器 %s 未成功: %s", name, _err)
            return None
        return _value if isinstance(_value, dict) else None

    @staticmethod
    def _build_summary(profile: SelfAwarenessProfile) -> str:
        """按各维度结果规模生成一句话总结。"""
        _parts = []
        for _f, _label in (("code_health", "代码"),
                           ("runtime_health", "运行时"),
                           ("knowledge_health", "知识"),
                           ("evolution_health", "进化"),
                           ("production_consumption", "产出消费"),
                           ("fake_loops", "虚假闭环"),
                           ("runtime_events", "运行时事件"),
                           ("organ_activity", "器官活跃度"),
                           ("call_graph_health", "代码结构")):
            _v = getattr(profile, _f, None) or {}
            if _v:
                _parts.append("%s %d 项" % (_label, len(_v)))
        if not _parts:
            return "暂无分析结果"
        return "自我认知画像：" + "，".join(_parts)

    # ------------------------------------------------------------------
    # 画像读取 / 报告 / 持久化
    # ------------------------------------------------------------------
    def get_profile(self) -> SelfAwarenessProfile:
        """当前画像（副本，调用方可安全修改）。"""
        with self._lock:
            return SelfAwarenessProfile.from_dict(self._profile.to_dict())

    def set_profile(self, profile: Any) -> None:
        """替换当前画像（合并语义：`merge`）。"""
        with self._lock:
            self._profile = SelfAwarenessProfile.from_dict(self._profile.to_dict())
            self._profile.merge(profile)

    # ------------------------------------------------------------------
    # ★主线第40批 T4（P2-261 / P2-216）：PHASE18 阶段二 · 只读接入接口
    #   设计依据：docs/设计文档/PHASE18_自我认知引擎_阶段二_结果接入设计_v1.0.md §3.1/§3.2
    #   红线：全部**只读** —— 绝不触发分析、绝不写盘、绝不修改决策。
    # _m40_t4_interfaces
    # ------------------------------------------------------------------
    def _m40_output_dir(self) -> str:
        """画像落盘目录（绝对路径；读 ``SELF_AWARENESS_OUTPUT_DIR``）。"""
        _rel = "data/self_awareness"
        try:
            import config
            _rel = str(getattr(config, "SELF_AWARENESS_OUTPUT_DIR", _rel) or _rel)
        except Exception as e:
            _logger.debug("读取自我认知输出目录失败，用默认值: %s: %s",
                          type(e).__name__, e)
        if os.path.isabs(_rel):
            return _rel
        return os.path.join(_project_root(), _rel.replace("/", os.sep))

    def _m40_latest_profile_file(self) -> str | None:
        """输出目录内**最新**的 ``profile_<ts>.json``（按 mtime）；无则 None。"""
        try:
            _d = self._m40_output_dir()
            if not os.path.isdir(_d):
                return None
            _best, _best_m = None, -1.0
            for _fn in os.listdir(_d):
                if not (_fn.startswith("profile_") and _fn.endswith(".json")):
                    continue
                _fp = os.path.join(_d, _fn)
                try:
                    _m = os.path.getmtime(_fp)
                except OSError:
                    continue
                if _m > _best_m:
                    _best, _best_m = _fp, _m
            return _best
        except Exception as e:
            _logger.debug("查找最新画像文件失败: %s: %s", type(e).__name__, e)
            return None

    def _m40_memory_profile_usable(self) -> bool:
        """内存态画像是否"有内容"。

        ★判据必须是**维度数据非空** —— **不能**用 ``timestamp``：
        ``__init__`` 会预置 ``_now_iso()``，导致"从未跑过分析"的引擎也被判为
        有效（实测踩到，会让 ``get_latest_profile()`` 返回空画像而非 ``None``）。
        # _m40_t4_memfix
        """
        try:
            _p = self.get_profile()
        except Exception as e:
            _logger.debug("读取内存画像失败: %s: %s", type(e).__name__, e)
            return False
        if _p is None:
            return False
        return any(bool(getattr(_p, _f, None)) for _f in _PROFILE_DICT_FIELDS)

    def get_latest_profile(self) -> SelfAwarenessProfile | None:
        """返回**最新一次**分析产生的画像（只读，不触发分析、不写盘）。

        数据源优先级（设计文档 §3.1）：
            ① 内存中最近一次 ``run_all_analyses()`` 结果（当次有效）；
            ② 输出目录内最新的 ``profile_<ts>.json``（跨进程 / 冷启动可用）；
            ③ 都不可用 → ``None``（调用方必须容忍 None）。
        """
        if self._m40_memory_profile_usable():
            return self.get_profile()
        _fp = self._m40_latest_profile_file()
        if _fp:
            return self.load_profile(_fp)
        return None

    def get_top_issues(self, n: int = 5) -> list:
        """最严重的 N 个问题（**不返回 None**，便于调用方直接迭代）。

        返回项结构见设计文档 §3.1：``dimension`` / ``dimension_label`` /
        ``severity``（统一 5 级）/ ``original_severity`` / ``description`` /
        ``suggestion``。
        """
        try:
            _limit = int(n) if n is not None else 5
        except (TypeError, ValueError):
            _limit = 5
        if _limit <= 0:
            return []
        _p = self.get_latest_profile()
        if _p is None:
            return []
        _ti = getattr(_p, "top_issues", None) or []
        return [dict(x) for x in _ti[:_limit] if isinstance(x, dict)]

    def get_health_level(self) -> str:
        """健康等级：``healthy``/``moderate``/``concerning``/``critical``/``unknown``。"""
        _p = self.get_latest_profile()
        if _p is None:
            return "unknown"
        _hl = str(getattr(_p, "health_level", "") or "").strip()
        return _hl if _hl else "unknown"

    def get_dimension_score(self, dimension: str) -> float | None:
        """指定维度的 0–100 评分；维度不可用 / 名称非法 → ``None``。"""
        if not dimension:
            return None
        _p = self.get_latest_profile()
        if _p is None:
            return None
        if not hasattr(_p, str(dimension)):
            return None
        return _score_of(getattr(_p, str(dimension), None))

    def is_fresh(self, max_age_sec: float = 86400.0) -> bool:
        """画像是否"足够新"（默认 24h 内）。场景 2/3 的使用前置条件。"""
        _p = self.get_latest_profile()
        if _p is None:
            return False
        _ts = str(getattr(_p, "timestamp", "") or "")
        if not _ts:
            return False
        try:
            _t = time.strptime(_ts[:19], "%Y-%m-%dT%H:%M:%S")
            return (time.time() - time.mktime(_t)) <= float(max_age_sec)
        except Exception as e:
            _logger.debug("画像时间解析失败: %s: %s", type(e).__name__, e)
            return False

    def get_public_summary(self) -> dict:
        """面向**用户可见**的摘要（不含文件名 / 行号 / 内部路径）。"""
        _p = self.get_latest_profile()
        if _p is None:
            return {"health_level": "unknown", "overall_score": None,
                    "best_dimension": None, "worst_dimension": None,
                    "headline_issue": ""}
        _scored = []
        for _dim, _label in _DIMENSION_LABELS.items():
            _s = _score_of(getattr(_p, _dim, None))
            if _s is not None:
                _scored.append((_s, _dim, _label))
        _best = _worst = None
        if _scored:
            _b = max(_scored, key=lambda x: x[0])
            _w = min(_scored, key=lambda x: x[0])
            _best = {"name": _b[2], "score": round(float(_b[0]), 2)}
            _worst = {"name": _w[2], "score": round(float(_w[0]), 2)}
        _head = ""
        for _it in (getattr(_p, "top_issues", None) or []):
            if isinstance(_it, dict) and _it.get("description"):
                _head = _sanitize_public_text(_it.get("description"))
                break
        _os = getattr(_p, "overall_score", None)
        return {
            "health_level": self.get_health_level(),
            "overall_score": (round(float(_os), 2)
                              if isinstance(_os, (int, float))
                              and not isinstance(_os, bool) else None),
            "best_dimension": _best,
            "worst_dimension": _worst,
            "headline_issue": _head,
        }

    def generate_report(self, output_path: str | None = None) -> str:
        """生成可读文本报告；给了 output_path 则同时落盘。

        Returns:
            报告全文。
        """
        _p = self.get_profile()
        _lines = [
            "=" * 64,
            "曈曈 PulseNet · 自我认知画像报告",
            "生成时间: %s" % (_p.timestamp or _now_iso()),
            "=" * 64,
            "",
            "【总结】%s" % (_p.summary or "暂无"),
            "",
        ]
        # ★主线第38批 T1（P2-213）：报告开头显示综合评分 / 健康等级 / 最严重问题
        _lines.extend(SelfAwarenessEngine._report_overall_section(_p))
        for _f, _label in (("code_health", "代码健康"),
                           ("runtime_health", "运行时健康"),
                           ("knowledge_health", "知识健康"),
                           ("evolution_health", "进化健康"),
                           ("production_consumption", "产出-消费配对"),
                           ("fake_loops", "虚假闭环检测"),
                           ("extra", "其他")):
            _v = getattr(_p, _f, None) or {}
            if not _v:
                continue
            _lines.append("【%s】(%d 项)" % (_label, len(_v)))
            for _k in sorted(_v.keys(), key=lambda x: str(x))[:50]:
                _vv = _v[_k]
                if isinstance(_vv, (dict, list)):
                    _lines.append("  · %s: %s" % (_k, _brief(_vv)))
                else:
                    _lines.append("  · %s: %s" % (_k, _vv))
            _lines.append("")
        # ★主线第20批 T3：动静结合 —— 运行时事件统计 + 器官活跃度分析
        _lines.extend(SelfAwarenessEngine._report_runtime_sections(_p))
        # ★主线第21批 T4：代码结构健康度（跨文件调用图）
        _lines.extend(SelfAwarenessEngine._report_call_graph_section(_p))
        # ★主线第23批 T5：知识质量健康度（第五维，P3-4）+ 五维总览
        _lines.extend(SelfAwarenessEngine._report_knowledge_quality_section(_p))
        _lines.extend(SelfAwarenessEngine._report_five_dimension_overview(_p))
        _lines.append("—— 报告结束 ——")
        _text = "\n".join(_lines)
        if output_path:
            try:
                _dir = os.path.dirname(os.path.abspath(output_path))
                if _dir and not os.path.isdir(_dir):
                    os.makedirs(_dir, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(_text)
            except Exception as e:  # 落盘失败不应中断报告生成
                _logger.warning("自我认知报告落盘失败 %s: %s: %s",
                                output_path, type(e).__name__, e)
        # ★主线第50批 T1（P0-1）：报告发布到 ReportBus。
        #   ★这是「生成了没人读」的头号修复点：此前本方法
        #   只返回字符串（默认不落盘），谁也不知道报告已生成。
        #   不影响原有落盘/返回行为；发布失败也不会中断。
        try:
            from nucleus.reporting.publishers import publish_self_cognition as _m50_pub
            _m50_pub(_text, summary=self._m50_report_summary(_p),
                     generator="SelfAwarenessEngine.generate_report")
        except Exception as _m50_e:
            # ★第50批修正：记**异常消息**（原只记类型 → 实际故障难以定位）
            _logger.debug("[M50-T1] 自认知报告发布失败（已忽略）: %s: %s",
                          type(_m50_e).__name__, _m50_e)
        return _text

    def _m50_report_summary(self, profile: Any) -> dict:
        """★第50批 T1：从画像提取**可消费**的摘要（供 ReportBus 信封）。

        ★修正记录（第50批自检发现）：原实现用
        ``json.loads(json.dumps(profile, default=str))``。而 ``profile`` 是
        **dataclass**（``SelfAwarenessProfile``）不可直接 JSON 序列化 →
        ``default=str`` 会把**整个对象**变成 repr 字符串 → ``json.loads``
        得到 ``str`` → ``.get()`` 抛 ``AttributeError`` →
        **报告发布恒失败**（且被 except 吞成 DEBUG 日志）。
        现优先用画像自带 ``to_dict()``，其次 ``dataclasses.asdict``。
        """
        _d: dict = {}
        try:
            _fn = getattr(profile, "to_dict", None)
            if callable(_fn):
                _d = _fn() or {}
            elif isinstance(profile, dict):
                _d = profile
            else:
                import dataclasses as _dc
                _d = _dc.asdict(profile)
        except Exception as _m50_pe:
            _logger.debug("[M50-T1] 画像摘要提取失败（降级为空）: %s: %s",
                          type(_m50_pe).__name__, _m50_pe)
            _d = {}
        if not isinstance(_d, dict):
            _d = {}
        _os_ = _d.get("overall_score")
        return {
            "health_level": _d.get("health_level"),
            "overall_score": _os_ if isinstance(_os_, (int, float)) else None,
            "best_dimension": _d.get("best_dimension"),
            "worst_dimension": _d.get("worst_dimension"),
            "headline_issue": _d.get("headline_issue"),
            "summary": str(_d.get("summary") or "")[:300],
        }

    def save_profile(self, path: str) -> bool:
        """画像持久化为 JSON。"""
        try:
            _dir = os.path.dirname(os.path.abspath(path))
            if _dir and not os.path.isdir(_dir):
                os.makedirs(_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.get_profile().to_dict(), f,
                          ensure_ascii=False, indent=2, default=str)
            return True
        except Exception as e:
            _logger.warning("画像保存失败 %s: %s: %s", path, type(e).__name__, e)
            return False

    def load_profile(self, path: str) -> SelfAwarenessProfile | None:
        """从 JSON 载入画像（不覆盖当前画像）。

        ★主线第19批 T1：本方法由**虚假闭环检测器自我发现**（缺非空/类型校验，
        score 80）。现按四步防御实现，**任何失败路径都返回 None 且绝不抛出**：

            ① 路径为空或文件不存在            → DEBUG + None
            ② JSON 解析失败（损坏/权限/编码） → WARNING + None
            ③ 顶层不是 dict                   → WARNING + None
            ④ 缺字段                          → `from_dict` 用默认值填充（不抛）

        Returns:
            载入的画像；任一失败路径返回 None。
        """
        # ① 文件存在性检查
        try:
            if not path or not os.path.isfile(path):
                _logger.debug("画像文件不存在，跳过载入: %r", path)
                return None
        except Exception as e:  # 存在性检查本身失败也不抛出
            _logger.debug("画像文件存在性检查失败 %r: %s: %s",
                          path, type(e).__name__, e)
            return None
        # ② JSON 解析（损坏/权限/编码 → WARNING，不抛）
        try:
            with open(path, encoding="utf-8") as f:
                _data = json.load(f)
        except Exception as e:  # 含 JSONDecodeError / OSError
            _logger.warning("画像解析失败（返回 None）%s: %s: %s",
                            path, type(e).__name__, e)
            return None
        # ③ 顶层类型校验
        if not isinstance(_data, dict):
            _logger.warning("画像格式异常（顶层非 dict，返回 None）%s: 实际 %s",
                            path, type(_data).__name__)
            return None
        # ④ 构造（缺字段用默认值；异常兜底）
        try:
            return SelfAwarenessProfile.from_dict(_data)
        except Exception as e:
            _logger.warning("画像构造失败（返回 None）%s: %s: %s",
                            path, type(e).__name__, e)
            return None

    #: 趋势对比的指标定义： (展示名, 取值路径, 是否"越小越好")
    #:   · True  —— 指标下降 = 改善
    #:   · None  —— 中性指标（只报变化，不判好坏）
    _COMPARE_METRICS: tuple = (
        ("虚假闭环候选数", ("fake_loops", "summary", "candidates"), True),
        ("虚假闭环严重数", ("fake_loops", "summary", "critical"), True),
        ("数据文件数", ("production_consumption", "summary", "data_files"), None),
        ("疑似无消费数", ("production_consumption", "summary", "no_consumer"), True),
        ("动态路径调用数", ("production_consumption", "summary", "dynamic_calls"), True),
        ("运行时错误数", ("runtime_health", "errors"), True),
        ("运行时严重数", ("runtime_health", "criticals"), True),
        ("代码问题总数", ("code_health", "total_issues"), True),
        ("代码 P0 问题数", ("code_health", "by_severity", "P0"), True),
    )

    @classmethod
    def compare_profiles(cls, baseline: Any, current: Any) -> dict[str, Any]:
        """对比两份画像的关键指标，输出变化量与「改善 / 退化」判定。

        ★主线第19批 T5：为设计文档阶段三的「进化趋势分析」建立能力。
        ★**不修改** SelfAwarenessProfile 数据模型，只读取字段。

        Args:
            baseline: 基线画像（SelfAwarenessProfile 或等价的 dict）。
            current:  当前画像（同上）。

        Returns:
            {
                "metrics": {名称: {baseline, current, delta, delta_rate, verdict}},
                "improved": [...], "degraded": [...], "unchanged": [...],
                "summary": "一句话结论",
            }
            基线或当前缺失（None / 空）时返回 **空 dict** 并打 INFO。
        """
        _b = baseline if isinstance(baseline, SelfAwarenessProfile) else \
            (SelfAwarenessProfile.from_dict(baseline) if baseline else None)
        _c = current if isinstance(current, SelfAwarenessProfile) else \
            (SelfAwarenessProfile.from_dict(current) if current else None)
        if _b is None or _c is None:
            _logger.info("趋势对比跳过：基线或当前画像缺失")
            return {}

        _metrics: dict[str, dict[str, Any]] = {}
        _improved: list[str] = []
        _degraded: list[str] = []
        _unchanged: list[str] = []
        for _label, _path, _lower_better in cls._COMPARE_METRICS:
            _bv = _dig(getattr(_b, _path[0], None), list(_path[1:]), 0)
            _cv = _dig(getattr(_c, _path[0], None), list(_path[1:]), 0)
            try:
                _bn, _cn = int(_bv), int(_cv)
            except (TypeError, ValueError):
                continue
            _delta = _cn - _bn
            _rate = None if _bn == 0 else round(_delta / float(_bn), 4)
            if _delta == 0:
                _verdict = "unchanged"
            elif _lower_better is None:
                _verdict = "neutral"
            elif _lower_better:
                _verdict = "improved" if _delta < 0 else "degraded"
            else:
                _verdict = "improved" if _delta > 0 else "degraded"
            _metrics[_label] = {
                "baseline": _bn, "current": _cn, "delta": _delta,
                "delta_rate": _rate, "verdict": _verdict,
            }
            if _verdict == "improved":
                _improved.append(_label)
            elif _verdict == "degraded":
                _degraded.append(_label)
            elif _verdict == "unchanged":
                _unchanged.append(_label)

        _summary = ("趋势对比：改善 %d 项（%s）；退化 %d 项（%s）；持平 %d 项"
                    % (len(_improved), "、".join(_improved) or "-",
                       len(_degraded), "、".join(_degraded) or "-",
                       len(_unchanged)))
        return {
            "metrics": _metrics,
            "improved": _improved,
            "degraded": _degraded,
            "unchanged": _unchanged,
            "summary": _summary,
        }

    def get_stats(self) -> dict[str, Any]:
        """引擎运行统计（供自省/诊断消费）。"""
        with self._lock:
            return {
                "enabled": _engine_enabled(),
                "timeout_sec": self._timeout_sec if self._timeout_sec is not None
                else _timeout_sec(),
                "analyzers": [n for n, _, _ in self._analyzers],
                "analyzer_count": len(self._analyzers),
                "run_count": self._run_count,
                "skipped_off": self._skipped_off,
                "last_run": {k: dict(v) for k, v in self._last_run.items()},
                "profile_timestamp": self._profile.timestamp,
            }

    # ------------------------------------------------------------------
    # 与现有模块整合（阶段一：留接口，后续批次填充）
    # ------------------------------------------------------------------
    def integrate_log_analyzer(self, log_file: str | None = None,
                               project_root: str | None = None) -> dict:
        """接入 `nucleus/evolution/LogAnalyzer.py` → `runtime_health` 维度。

        ★主线第19批 T3：填充阶段一的留空接口（"整合而非替代"）。
        LogAnalyzer 扫描运行日志（默认 `logs/pulse.log`）提取
        Traceback / ERROR / CRITICAL 的动态归因定位清单。

        开关 `ENABLE_LOG_ANALYZER_INTEGRATION`（默认 True）关闭 → 空 dict。
        **任何异常都返回空 dict 并打 WARNING**（整合失败绝不影响引擎）。

        Args（可选，便于测试隔离）:
            log_file: 指定日志文件；None 时用 LogAnalyzer 默认（logs/pulse.log）。
            project_root: 指定项目根；None 时用本文件位置推导的项目根。
        """
        if not _integration_enabled("ENABLE_LOG_ANALYZER_INTEGRATION"):
            _logger.debug("LogAnalyzer 整合开关关闭，跳过")
            return {}
        try:
            from nucleus.evolution.LogAnalyzer import LogAnalyzer
            _analyzer = LogAnalyzer(project_root or _project_root())
            _r = _analyzer.analyze(log_file=log_file)
            _summary = _r.get("summary", {}) or {}
            _issues = list(_r.get("issues", []) or [])
            _by_type: dict[str, int] = {}
            _latest = ""
            for _it in _issues:
                _et = str(_it.get("error_type", "") or "unknown")
                _by_type[_et] = _by_type.get(_et, 0) + 1
                _ls = str(_it.get("last_seen", "") or "")
                _latest = max(_latest, _ls)
            return {
                "total_lines": int(_r.get("total_lines", 0) or 0),
                "tracebacks": int(_summary.get("tracebacks", 0) or 0),
                "errors": int(_summary.get("errors", 0) or 0),
                "criticals": int(_summary.get("criticals", 0) or 0),
                "unique_locations": int(_summary.get("unique_locations", 0) or 0),
                "top_issue": _summary.get("top_issue"),
                "error_type_distribution": _by_type,
                "latest_error_time": _latest,
                "issues_preview": [
                    {
                        "organ": str(_it.get("organ", "") or ""),
                        "error_type": str(_it.get("error_type", "") or ""),
                        "file": os.path.basename(str(_it.get("file_path") or "")),
                        "line": _it.get("line"),
                        "count": _it.get("count"),
                        "message": str(_it.get("message", "") or "")[:120],
                    }
                    for _it in _issues[:10]
                ],
            }
        except Exception as e:  # 整合失败不影响引擎
            _logger.warning("LogAnalyzer 整合失败（返回空 dict）: %s: %s",
                            type(e).__name__, e)
            return {}

    def integrate_code_review(self, file_path: str | None = None,
                              project_root: str | None = None) -> dict:
        """接入 `nucleus/review/CodeReviewEngine.py` → `code_health` 维度。

        ★主线第19批 T4：填充阶段一的留空接口。
        ★两个**必须**的调用参数（否则语义/性能都不对）：
            · `incremental=False` —— 默认 True 只审「最近 1 小时改动」的文件，
              首份基线需要**全量**；
            · `use_pyright=False` —— pyright 全项目极慢，极易撞 30s 超时保护，
              ruff 已能覆盖 F/E 类问题。

        开关 `ENABLE_CODE_REVIEW_INTEGRATION`（默认 True）关闭 → 空 dict。
        与虚假闭环检测器**互补不重复**（前者通用代码质量，后者 save/load 闭环）。

        Args（可选，便于测试隔离）:
            file_path: 只审指定文件；None 表示全项目。
            project_root: 指定项目根；None 时用本文件位置推导的项目根。
        """
        if not _integration_enabled("ENABLE_CODE_REVIEW_INTEGRATION"):
            _logger.debug("CodeReviewEngine 整合开关关闭，跳过")
            return {}
        try:
            from nucleus.review.CodeReviewEngine import CodeReviewEngine
            _engine = CodeReviewEngine(project_root or _project_root())
            _res = _engine.review(file_path=file_path, use_ruff=True,
                                  use_pyright=False, incremental=False)
            _by_rule: dict[str, int] = {}
            _by_file: dict[str, int] = {}
            _by_sev: dict[str, int] = {}
            _filtered = 0
            for _it in list(getattr(_res, "issues", []) or []):
                _fp = str(getattr(_it, "file_path", "") or "")
                # ★过滤备份/临时/缓存目录（否则 .bak_batchN/ 会污染统计）
                if _is_non_source_path(_fp):
                    _filtered += 1
                    continue
                _rule = str(getattr(_it, "rule", "") or "unknown")
                _by_rule[_rule] = _by_rule.get(_rule, 0) + 1
                # ★severity 同样按"过滤后"口径重算（原样透传会与总数不一致）
                _sev = str(getattr(_it, "severity", "") or "unknown")
                _by_sev[_sev] = _by_sev.get(_sev, 0) + 1
                if _fp:
                    _by_file[_fp] = _by_file.get(_fp, 0) + 1
            _top_files = sorted(_by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
            _raw_total = getattr(_res, "total_files", 0)
            return {
                "total_files": (_raw_total if isinstance(_raw_total, int)
                                else len(_by_file)),
                "scanned_source_files": len(_by_file),
                "filtered_non_source_issues": _filtered,
                "total_issues": sum(_by_rule.values()),
                "by_severity": dict(sorted(_by_sev.items())),
                "by_rule": dict(sorted(_by_rule.items(),
                                       key=lambda kv: (-kv[1], kv[0]))[:20]),
                "top_files": [{"file": _k, "issues": _v} for _k, _v in _top_files],
                "tools_used": list(getattr(_res, "tools_used", []) or []),
                "review_time_sec": round(
                    float(getattr(_res, "review_time", 0.0) or 0.0), 2),
                # ★主线第30批 T3：显式标注**统计口径**，避免「同一个数字不同人算出不同值」。
                "scan_scope": "project_root_recursive (含 tests/)",
                "ruleset": "项目 ruff.toml（select/ignore/exclude 见该文件）",
                "reproduce_cmd": "ruff check --output-format json .",
                "ruleset_note": (
                    "口径说明：本数字为「项目根递归扫描（含 tests/）」的 ruff 问题总数。"
                    "若需与「核心模块口径」对比，其范围是 nucleus+organs+functions+tools（不含 tests/）"
                    "——2026-09-12 实测两者分别约 836 / 578 条。"
                    "复现：ruff check --output-format json .（或指定目录）。"),
            }
        except Exception as e:  # 整合失败不影响引擎
            _logger.warning("CodeReviewEngine 整合失败（返回空 dict）: %s: %s",
                            type(e).__name__, e)
            return {}

    # ------------------------------------------------------------------
    # ★主线第38批 T2（P2-214）：evolution_health 维度整合
    # ------------------------------------------------------------------
    def integrate_evolution_health(self, executor: Any = None) -> dict:
        """接入 `nucleus/reasoning/SafeEvolutionExecutor.py` → `evolution_health`。

        ★主线第38批 T2（P2-214）：设计 6 维中该维**此前无任何分析器 → 端到端恒空**。

        ★只读红线：仅读取执行器的内存态（``_patch_log`` / 崩溃统计）与**补丁历史
        文件**（``_patch_manager.load_json``）——**绝不**调用 verify/rollback/apply
        等会改动状态的接口（``verify_applied_patches`` 会自动回滚，故不使用）。

        开关 `ENABLE_EVOLUTION_HEALTH_INTEGRATION`（默认 True）关闭 → 空 dict。
        不可用 / 无记录 → ``status="unavailable"`` 且 ``score=None``（不参与综合评分）。

        Args:
            executor: 注入的执行器实例（测试用）；None 时取
                ``get_safe_evolution_executor()`` 单例。
        """
        if not _integration_enabled("ENABLE_EVOLUTION_HEALTH_INTEGRATION"):
            _logger.debug("evolution_health 整合开关关闭，跳过")
            return {}
        try:
            if executor is None:
                from nucleus.reasoning.SafeEvolutionExecutor import (
                    get_safe_evolution_executor)
                executor = get_safe_evolution_executor()
            if executor is None:
                return {}
            # ★主线第39批 T3（P2-242）：优先走**公共统计接口**；不可用则回退私有访问
            _raw = _evolution_stats_via_public(executor)
            if _raw is None:
                _logger.warning(
                    "SafeEvolutionExecutor 公共接口 get_evolution_stats() 不可用，"
                    "回退到私有属性访问（P2-242 兼容路径）")
                _raw = _evolution_raw_stats(executor)
            return _score_evolution_health(_raw)
        except Exception as e:      # 整合失败不影响引擎
            _logger.warning("evolution_health 整合失败（返回空 dict）: %s: %s",
                            type(e).__name__, e)
            return {}

    # ------------------------------------------------------------------
    # ★主线第20批 T1（PHASE18 阶段二）：EventTap 运行时事件统计整合
    # ------------------------------------------------------------------
    def integrate_event_tap(self, tap: Any = None) -> dict:
        """接入 `nucleus/events/EventTap.py` → `runtime_events` 维度。

        把第17批建成的**旁路事件统计**并入画像，使评估从「纯静态代码分析」
        升级为「动静结合」。

        ★红线（任务书 §三.T1）：
            · **只读** —— 绝不调用 `reset_stats()`，绝不修改 EventTap 任何状态；
            · `get_stats()` 内部为加锁深拷贝、耗时极短，直接调用即可，
              **不得**为它另开线程/加锁（避免阻塞事件总线）。

        开关 `ENABLE_EVENT_TAP_INTEGRATION`（默认 True）关闭 → `{"disabled": True}`。
        EventTap 不可用/导入失败/取数异常 → 空 dict + WARNING（绝不影响引擎）。

        Args:
            tap: 注入的 EventTap 实例（测试用）；None 时取 `get_event_tap()` 单例。
        """
        if not _integration_enabled("ENABLE_EVENT_TAP_INTEGRATION"):
            _logger.debug("EventTap 整合开关关闭，跳过")
            return {"disabled": True}
        try:
            if tap is None:
                from nucleus.events.EventTap import get_event_tap
                tap = get_event_tap()
            if tap is None:
                _logger.warning("EventTap 整合失败：未取到实例（未启动或导入失败）")
                return {}
            _st = tap.get_stats()
            if not isinstance(_st, dict):
                _logger.warning("EventTap 整合失败：get_stats() 返回非字典 %s",
                                type(_st).__name__)
                return {}
            _by_name = dict(_st.get("by_name") or {})
            _by_source = dict(_st.get("by_source") or {})
            _interval = dict(_st.get("interval") or {})
            return {
                "total_events": int(_st.get("total", 0) or 0),
                "distinct_event_names": int(_st.get("distinct_names", 0) or 0),
                "active_sources": len(_by_source),
                "top_event_names": [
                    {"name": _k, "count": int(_v)}
                    for _k, _v in sorted(_by_name.items(),
                                         key=lambda kv: (-kv[1], kv[0]))[:10]
                ],
                "top_sources": [
                    {"source": _k, "count": int(_v)}
                    for _k, _v in sorted(_by_source.items(),
                                         key=lambda kv: (-kv[1], kv[0]))[:10]
                ],
                # 原始字典（供 analyze_organ_activity 与报告使用）
                "by_name": _by_name,
                "by_source": _by_source,
                "by_priority": dict(_st.get("by_priority") or {}),
                "interval": {
                    "min": _interval.get("min"),
                    "max": float(_interval.get("max", 0.0) or 0.0),
                    "avg": float(_interval.get("avg", 0.0) or 0.0),
                    "samples": int(_interval.get("samples", 0) or 0),
                },
                "tap_enabled": bool(_st.get("enabled", False)),
                "tap_started": bool(_st.get("started", False)),
                # 提示：EventTap 统计是进程级累积，非时间窗口
                "note": "统计自框架启动以来累积",
            }
        except Exception as e:  # 整合失败不影响引擎
            _logger.warning("EventTap 整合失败（返回空 dict）: %s: %s",
                            type(e).__name__, e)
            return {}

    # ------------------------------------------------------------------
    # ★主线第20批 T2：器官活跃度排名与异常识别
    # ------------------------------------------------------------------
    def analyze_organ_activity(self, event_stats: Any = None,
                               silent_threshold: float = 0.01,
                               overactive_threshold: float = 0.20) -> dict:
        """基于事件来源分布，计算器官活跃度排名与异常（沉默/过热/集中度）。

        Args:
            event_stats: 事件统计。**优先读 `by_source`**（EventTap 原始口径）；
                若只给了 `top_sources`（T1 的转换输出）也兼容（仅覆盖已列出的
                Top 项，百分比会以可见项为分母，故生产链路应传原始 stats）。
            silent_threshold: 低于该占比视为「沉默器官」（默认 1%）。
            overactive_threshold: 高于该占比视为「过热器官」（默认 20%）。

        Returns:
            dict；无数据时 `{"no_data": True}`。
        """
        _src: dict[str, int] = {}
        if isinstance(event_stats, dict):
            _raw = event_stats.get("by_source")
            if isinstance(_raw, dict):
                _src = {str(k): int(v) for k, v in _raw.items()}
            else:
                for _row in (event_stats.get("top_sources") or []):
                    if isinstance(_row, dict) and _row.get("source") is not None:
                        _src[str(_row["source"])] = int(_row.get("count", 0) or 0)
        if not _src:
            _logger.debug("器官活跃度分析：无 by_source 数据，跳过")
            return {"no_data": True}
        _total = sum(_src.values())
        if _total <= 0:
            _logger.debug("器官活跃度分析：事件总数为 0，跳过")
            return {"no_data": True}
        _ranked = sorted(_src.items(), key=lambda kv: (-kv[1], kv[0]))
        _ranking = [
            {"organ": _k, "count": _v,
             "percentage": round(_v * 100.0 / _total, 2)}
            for _k, _v in _ranked
        ]
        _silent = [{"organ": _k, "count": _v}
                   for _k, _v in _ranked if _v / _total < silent_threshold]
        _over = [{"organ": _k, "count": _v,
                  "percentage": round(_v * 100.0 / _total, 2)}
                 for _k, _v in _ranked if _v / _total > overactive_threshold]
        _concentration = round(_ranked[0][1] / _total, 4)
        return {
            "ranking": _ranking,
            "silent_organs": _silent,
            "overactive_organs": _over,
            "total_organs": len(_src),
            "total_events": _total,
            "concentration_ratio": _concentration,
            "silent_threshold": silent_threshold,
            "overactive_threshold": overactive_threshold,
            "note": "统计自框架启动以来累积",
        }

    # ------------------------------------------------------------------
    # ★主线第21批 T3：跨文件调用图整合（P3-3）
    # ------------------------------------------------------------------
    def integrate_call_graph(self, project_root: str | None = None,
                             scan_dirs: list | None = None) -> dict:
        """接入 `CallGraphAnalyzer` → `call_graph_health` 维度（代码结构健康度）。

        纯静态 AST 分析（不执行任何代码），产出孤立/热点/循环/深度/耦合五维
        指标与 0-100 综合评分。

        开关 `ENABLE_CALL_GRAPH_ANALYSIS`（默认 True）关闭 → 空 dict。
        **任何异常都返回空 dict 并打 WARNING**（整合失败绝不影响引擎）。

        Args:
            project_root: 指定项目根（测试隔离用）；None 时自动推导。
            scan_dirs: 指定扫描范围；None 时读 config.CALL_GRAPH_SCAN_DIRS。
        """
        if not _integration_enabled("ENABLE_CALL_GRAPH_ANALYSIS"):
            _logger.debug("调用图分析开关关闭，跳过")
            return {}
        try:
            from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
            _cg = CallGraphAnalyzer(project_root=project_root, scan_dirs=scan_dirs)
            _graph = _cg.analyze()
            _health = _cg.analyze_health(_graph)
            _health["graph_stats"] = dict(_graph.get("stats") or {})
            return _health
        except Exception as e:  # 整合失败不影响引擎
            _logger.warning("调用图分析整合失败（返回空 dict）: %s: %s",
                            type(e).__name__, e)
            return {}

    def integrate_knowledge_quality(self, nodes: list | None = None,
                                    project_root: str | None = None) -> dict:
        """接入 `KnowledgeQualityAnalyzer` → `knowledge_health` 维度（知识质量，P3-4）。

        全库知识质量体检：一致性（矛盾/冲突）+ 覆盖率（盲区/孤岛）+ 老化（过时/休眠）
        + 知识深度（L2/L3 占比）+ 知识广度（领域数）→ 0-100 综合评分与改进建议。

        数据源：`data/knowledge/parquet/evol_level=L*` 分区优先（实测 10496 节点约 3.7s），
        回退 L1 快照；**绝不整体加载 406MB 主快照**。

        开关 `ENABLE_KNOWLEDGE_QUALITY_ANALYSIS`（默认 True）关闭 → 空 dict。
        **任何异常都返回空 dict 并打 WARNING**（整合失败绝不影响引擎）。

        Args:
            nodes: 指定节点集（测试隔离用）；None 时由分析器自动加载。
            project_root: 指定项目根（测试隔离用）；None 时自动推导。
        """
        if not _integration_enabled("ENABLE_KNOWLEDGE_QUALITY_ANALYSIS"):
            _logger.debug("知识质量分析开关关闭，跳过")
            return {}
        try:
            from nucleus.self_awareness.KnowledgeQualityAnalyzer import (
                KnowledgeQualityAnalyzer,
            )
            _kq = KnowledgeQualityAnalyzer(project_root=project_root)
            return _kq.generate_report(nodes)
        except Exception as e:  # 整合失败不影响引擎
            _logger.warning("知识质量分析整合失败（返回空 dict）: %s: %s",
                            type(e).__name__, e)
            return {}

    # ------------------------------------------------------------------
    # 报告专用段落（运行时事件 / 器官活跃度 / 代码结构 / 知识质量）
    # ------------------------------------------------------------------
    @staticmethod
    def _report_runtime_sections(profile: Any) -> list:
        """生成「运行时事件统计」「器官活跃度分析」两段文本。"""
        _rt = getattr(profile, "runtime_events", None) or {}
        _oa = getattr(profile, "organ_activity", None) or {}
        _out: list = ["【运行时事件统计】"]
        # --- 运行时事件 ---
        if not _rt or _rt.get("disabled"):
            _out.append("  · 运行时数据不可用（EventTap未启用或统计为空）")
        elif not _rt.get("total_events"):
            _out.append("  · 运行时数据不可用（EventTap 统计为空）")
        else:
            _out.append("  · 总事件数: %s（种类 %s / 活跃来源 %s）"
                        % (_rt.get("total_events"),
                           _rt.get("distinct_event_names"),
                           _rt.get("active_sources")))
            _tops = (_rt.get("top_event_names") or [])[:5]
            if _tops:
                _out.append("  · Top5 事件名: %s"
                            % ", ".join("%s=%s" % (x.get("name"), x.get("count"))
                                        for x in _tops))
            _topsrc = (_rt.get("top_sources") or [])[:5]
            _tot = int(_rt.get("total_events", 0) or 0)
            if _topsrc:
                _out.append("  · Top5 来源器官: %s"
                            % ", ".join(
                                "%s=%s (%s%%)" % (x.get("source"), x.get("count"),
                                                  round(int(x.get("count", 0)) * 100.0
                                                        / _tot, 1) if _tot else 0)
                                for x in _topsrc))
            _iv = _rt.get("interval") or {}
            _out.append("  · 到达间隔: min=%s / max=%ss / avg=%ss（样本 %s）"
                        % (_iv.get("min"), _iv.get("max"), _iv.get("avg"),
                           _iv.get("samples")))
            if _rt.get("by_priority"):
                _out.append("  · 优先级分布: %s" % _rt.get("by_priority"))
            _out.append("  · 说明: %s" % (_rt.get("note") or "累积统计"))
        _out.append("")
        # --- 器官活跃度 ---
        _out.append("【器官活跃度分析】")
        if not _oa or _oa.get("no_data"):
            _out.append("  · 运行时数据不可用（EventTap未启用或统计为空）")
        else:
            _out.append("  · 参与器官: %s 个（事件合计 %s）"
                        % (_oa.get("total_organs"), _oa.get("total_events")))
            _sil = _oa.get("silent_organs") or []
            _out.append("  · 沉默器官（<%s%%）: %s"
                        % (round(float(_oa.get("silent_threshold", 0.01)) * 100, 1),
                           ", ".join("%s=%s" % (x.get("organ"), x.get("count"))
                                     for x in _sil[:10]) if _sil else "无"))
            _ovr = _oa.get("overactive_organs") or []
            _out.append("  · 过热器官（>%s%%）: %s"
                        % (round(float(_oa.get("overactive_threshold", 0.2)) * 100, 1),
                           ", ".join("%s=%s (%s%%)"
                                     % (x.get("organ"), x.get("count"),
                                        x.get("percentage"))
                                     for x in _ovr[:10]) if _ovr else "无"))
            _c = float(_oa.get("concentration_ratio", 0.0) or 0.0)
            _verdict = "正常" if _c < 0.5 else ("偏高" if _c < 0.8 else "过高")
            _out.append("  · 集中度（Top1 占比）: %.4f（%s）" % (_c, _verdict))
        _out.append("")
        return _out

    @staticmethod
    def _report_call_graph_section(profile: Any) -> list:
        """生成「代码结构健康度」段落文本（PHASE18 阶段二 T4）。"""
        _cg = getattr(profile, "call_graph_health", None) or {}
        _out: list = ["【代码结构健康度】"]
        if not _cg or _cg.get("no_data"):
            _out.append("  · 调用图数据不可用（分析未执行或项目为空）")
            _out.append("")
            return _out
        _out.append("  · 综合评分: %s / 100（%s）"
                    % (_cg.get("score"), _cg.get("grade")))
        _iso = _cg.get("isolated") or {}
        _out.append("  · 孤立函数: %s 个（占比 %.2f%%，内部孤立 %s 个）——%s"
                    % (_iso.get("count", 0),
                       float(_iso.get("ratio", 0.0) or 0.0) * 100,
                       _iso.get("internal_count", "-"), _iso.get("level", "-")))
        _top_iso = (_iso.get("top20") or [])[:5]
        for _x in _top_iso:
            _out.append("      - %s  (%s:%s)"
                        % (_x.get("name"), _x.get("file"), _x.get("line")))
        _hot = _cg.get("hot") or {}
        _top_hot = (_hot.get("top20") or [])[:5]
        if _top_hot:
            _out.append("  · 热点函数 Top5: %s"
                        % ", ".join("%s=%s" % (x.get("name"), x.get("calls"))
                                    for x in _top_hot))
        _out.append("  · 超级函数（>100 次）: %s 个"
                    % len(_hot.get("super_functions") or []))
        _cyc = _cg.get("cyclic") or {}
        _chains = _cyc.get("chains") or []
        if _chains:
            _shortest = min(_chains, key=lambda c: c.get("length", 99))
            _out.append("  · 循环调用: %s 组（%s）；最短链（%s）: %s"
                        % (_cyc.get("count", 0), _cyc.get("level", "-"),
                           _shortest.get("kind"),
                           " -> ".join(str(x).split(":")[-1]
                                       for x in (_shortest.get("nodes") or [])[:6])))
        else:
            _out.append("  · 循环调用: 0 组（正常）")
        _dep = _cg.get("depth") or {}
        _out.append("  · 调用深度: 最深 %s 层 / 平均 %s"
                    % (_dep.get("deepest"), _dep.get("average")))
        _cpl = _cg.get("coupling") or {}
        _out.append("  · 跨文件调用占比: %.2f%%"
                    % (float(_cpl.get("cross_file_ratio", 0.0) or 0.0) * 100))
        _recs = (_cg.get("recommendations") or [])[:3]
        for _i, _r in enumerate(_recs, 1):
            _out.append("  · 建议%d: %s" % (_i, _r))
        _out.append("")
        return _out

    @staticmethod
    def _report_knowledge_quality_section(profile: Any) -> list:
        """生成「知识质量健康度」段落文本（主线第23批 T5 / 债务 P3-4）。"""
        _kq = getattr(profile, "knowledge_health", None) or {}
        _out: list = ["【知识质量健康度】"]
        if not _kq or _kq.get("no_data") or "score" not in _kq:
            _out.append("  · 知识质量数据不可用（分析未执行或开关关闭）")
            _out.append("")
            return _out
        _dim = _kq.get("dimensions") or {}
        _out.append("  · 综合评分: %s / 100（%s）"
                    % (_kq.get("score"), _kq.get("level")))
        _out.append("  · 五维分解: 一致性 %s｜覆盖率 %s｜老化 %s｜知识深度 %s｜知识广度 %s"
                    % (_dim.get("consistency"), _dim.get("coverage"),
                       _dim.get("aging"), _dim.get("depth"), _dim.get("breadth")))
        _st = _kq.get("stats") or {}
        _conf = _st.get("conflicts") or {}
        _out.append("  · 冲突 %s 处（高危 %s / 待人工确认 %s）｜盲区领域 %s 个"
                    "｜孤岛节点 %s 个｜老化节点 %s 个"
                    % (_conf.get("total", 0),
                       (_conf.get("severity") or {}).get("high", 0),
                       _conf.get("needs_review", 0),
                       _st.get("blind_spots", 0), _st.get("islands", 0),
                       _st.get("aged", 0)))
        _ld = _st.get("level_dist") or {}
        if _ld:
            _out.append("  · 知识层级分布: %s"
                        % ", ".join("%s=%s" % (k, v) for k, v in sorted(_ld.items())))
        _tops = (_kq.get("top_issues") or [])[:5]
        for _x in _tops:
            _out.append("      - [%s/%s] %s"
                        % (_x.get("kind"), _x.get("severity"), _x.get("detail")))
        _recs = (_kq.get("suggestions") or [])[:4]
        for _i, _r in enumerate(_recs, 1):
            _out.append("  · 建议%d: %s" % (_i, _r))
        _out.append("")
        return _out

    @staticmethod
    def _report_overall_section(profile: Any) -> list:
        """★主线第38批 T1（P2-213）：报告开头的「综合评分 / 健康等级 / 最严重问题」。"""
        _sc = getattr(profile, "overall_score", None)
        _lv = str(getattr(profile, "health_level", "unknown") or "unknown")
        _lv_zh = {"healthy": "健康", "moderate": "中等", "concerning": "堪忧",
                  "critical": "危急", "unknown": "未知"}.get(_lv, _lv)
        _out: list = ["【综合评分】%s / 100 （%s）"
                      % (("%.2f" % _sc) if isinstance(_sc, (int, float)) else "不可用",
                         _lv_zh)]
        if not isinstance(_sc, (int, float)):
            _out.append("  · 说明：可用维度不足 2 个，无法给出综合评分")
        _ti = getattr(profile, "top_issues", None) or []
        if _ti:
            _out.append("【最严重问题】(Top %d)" % len(_ti))
            for _i, _x in enumerate(_ti, 1):
                if not isinstance(_x, dict):
                    continue
                _out.append("  %d. [%s/%s] %s"
                            % (_i,
                               _x.get("dimension_label", _x.get("dimension", "?")),
                               _x.get("severity", "?"),
                               _x.get("description", "")))
                if _x.get("suggestion"):
                    _out.append("     建议: %s" % _x["suggestion"])
        else:
            _out.append("【最严重问题】无")
        _out.append("")
        return _out

    @staticmethod
    def _report_five_dimension_overview(profile: Any) -> list:
        """生成「五维健康度总览」段落（主线第23批 T5）。

        五维：代码静态 / 数据文件 / 运行时动态 / 代码结构 / 知识质量。
        各维度评分缺失时显示「不可用」，综合分取**可用维度**的均值（不虚构缺失项）。
        """
        _rows = (
            ("① 代码静态健康度", _score_of(getattr(profile, "code_health", None))),
            ("② 数据文件健康度",
             _score_of(getattr(profile, "production_consumption", None))),
            ("③ 运行时动态健康度", _score_of(getattr(profile, "runtime_health", None))),
            ("④ 代码结构健康度", _score_of(getattr(profile, "call_graph_health", None))),
            ("⑤ 知识质量健康度", _score_of(getattr(profile, "knowledge_health", None))),
        )
        _out: list = ["【五维健康度总览】"]
        _vals = []
        for _name, _v in _rows:
            if isinstance(_v, (int, float)):
                _vals.append(float(_v))
                _out.append("  · %s: %.2f / 100" % (_name, float(_v)))
            else:
                _out.append("  · %s: 不可用（该维度未执行或无数据）" % _name)
        if _vals:
            _out.append("  · 五维综合（可用维度均值）: %.2f / 100（%s）"
                        % (sum(_vals) / len(_vals), _grade(sum(_vals) / len(_vals))))
        _out.append("")
        return _out


#: ★主线第38批 T1（P2-213）：维度名 → 报告/问题列表中的显示名
_DIMENSION_LABELS = {
    "code_health": "代码静态",
    "runtime_health": "运行时动态",
    "knowledge_health": "知识质量",
    "evolution_health": "进化健康",
    "production_consumption": "数据文件",
    "fake_loops": "虚假闭环",
    "runtime_events": "运行时事件",
    "organ_activity": "器官活跃度",
    "call_graph_health": "代码结构",
}

#: ★主线第40批 T4（P2-261）：用户可见摘要中的**内部信息**模式（文件名/行号/路径）
#   —— ``get_public_summary()`` 必须过滤，只给用户可读的健康摘要。
_INTERNAL_REF_RE = None


def _sanitize_public_text(text: Any) -> str:
    """把含内部实现细节（文件名 / 行号 / 路径）的文本替换为中性描述。

    例：``"xxx.py:123 存在 3 处问题"`` → ``"<内部文件> 存在 3 处问题"``。
    """
    global _INTERNAL_REF_RE
    try:
        import re
        if _INTERNAL_REF_RE is None:
            _INTERNAL_REF_RE = re.compile(
                r"[A-Za-z0-9_\-./\\]*[A-Za-z0-9_\-]\.(?:py|json|jsonl|md|txt"
                r"|log|yaml|yml|csv|tsv|parquet|db|sqlite|pkl|pickle|npz|npy)"
                r"(?::\d+)?")
        _s = text if isinstance(text, str) else str(text or "")
        return _INTERNAL_REF_RE.sub("<内部文件>", _s)
    except Exception as e:  # 清洗失败不得影响摘要生成
        _logger.debug("公开摘要文本清洗失败: %s: %s", type(e).__name__, e)
        return str(text or "")

#: ★主线第39批 T4（P2-241）：统一的 **5 级** severity（高 → 低）
#: critical（致命：系统不可用/数据丢失）> high（严重：核心功能受损/安全风险）
#: > medium（中等：功能受限/性能问题）> low（轻微：体验问题/小缺陷）> info（信息）
_SEV_LEVELS = ("critical", "high", "medium", "low", "info")

#: ★T4：各维度**原始口径** → 统一 5 级的映射表
#: 依据任务书 §T4.2：knowledge_health ``error``→``critical``；fake_loops ``severe``→``critical``；
#: evolution_health 保持 ``high``/``medium``/``low``/``info``；其余同义归并。
_SEV_MAP = {
    # critical
    "critical": "critical", "fatal": "critical", "severe": "critical",
    "error": "critical", "blocker": "critical", "p0": "critical",
    # high
    "high": "high", "major": "high", "p1": "high",
    # medium
    "medium": "medium", "moderate": "medium", "warning": "medium",
    "warn": "medium", "p2": "medium",
    # low
    "low": "low", "minor": "low", "trivial": "low", "p3": "low",
    # info
    "info": "info", "information": "info", "debug": "info", "notice": "info",
}


def _normalize_severity(severity: Any) -> str:
    """★主线第39批 T4（P2-241）：把各维度 severity 口径归一化为统一 5 级字符串。

    无法识别的取值一律归为 ``"info"``（**最保守**：不夸大严重度）。
    """
    _s = str(severity or "").strip().lower()
    if not _s:
        return "info"
    return _SEV_MAP.get(_s, "info")


def _severity_rank(severity: Any) -> int:
    """统一 5 级的排序权重：``0=critical`` / 1=high / 2=medium / 3=low / 4=info。

    ★主线第38批 T1 引入（原为 3 档 high/medium/low）；**主线第39批 T4（P2-241）**
    细化为任务书要求的 **5 级**（新增 ``critical`` 与独立 ``info``），
    并新增 ``original_severity`` 字段保留各维度原始口径（不丢信息）。
    """
    _lv = _normalize_severity(severity)
    try:
        return _SEV_LEVELS.index(_lv)
    except ValueError:      # pragma: no cover - _SEV_LEVELS 恒含全部取值
        return len(_SEV_LEVELS) - 1


def _health_level(score: Any) -> str:
    """★主线第38批 T1（P2-213）：综合评分 → 健康等级（任务书 §T1.3）。"""
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return "unknown"
    if score >= 80:
        return "healthy"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "concerning"
    return "critical"


def _overall_enabled() -> bool:
    """T1 综合评分开关（默认 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_SELF_AWARENESS_OVERALL_SCORE", True))
    except Exception as e:
        _logger.debug("读取综合评分开关失败，按开启处理: %s: %s", type(e).__name__, e)
        return True


def _dimension_weights() -> dict:
    """T1 维度权重表（默认空 dict = 等权）。"""
    try:
        import config
        _v = getattr(config, "SELF_AWARENESS_DIMENSION_WEIGHTS", None)
        return dict(_v) if isinstance(_v, dict) else {}
    except Exception as e:
        _logger.debug("读取维度权重失败，按等权处理: %s: %s", type(e).__name__, e)
        return {}


def _top_issues_limit() -> int:
    """T1 top_issues 条数上限（默认 5）。"""
    try:
        import config
        return max(1, int(getattr(config, "SELF_AWARENESS_TOP_ISSUES_LIMIT", 5) or 5))
    except Exception:
        return 5


def _dimension_issues(field: str, data: Any) -> list[dict]:
    """从单个维度结果中抽取 issues，统一为
    ``{dimension, dimension_label, severity, description, suggestion}``。

    ★主线第38批 T1（P2-213）：各维度 issues 形态不一（knowledge_health 用
    ``top_issues``、fake_loops 用 ``critical``、code_health 用 ``by_severity`` 汇总、
    evolution_health 用 ``issues``），此处做**适配层**而非要求上游统一改造。
    """
    if not isinstance(data, dict) or not data:
        return []
    _label = _DIMENSION_LABELS.get(field, field)
    _out: list[dict] = []

    def _add(_sev: Any, _desc: Any, _sug: str = "") -> None:
        _d = str(_desc or "").strip()
        if not _d:
            return
        _orig = str(_sev or "unknown").strip().lower() or "unknown"
        _out.append({
            "dimension": field, "dimension_label": _label,
            # ★T4（P2-241）：severity = 统一 5 级；original_severity = 该维度原始口径
            "severity": _normalize_severity(_orig),
            "original_severity": _orig,
            "description": _d[:300], "suggestion": str(_sug or "")[:300],
        })

    for _it in (data.get("issues") or []):          # ① 标准 issues 列表
        if isinstance(_it, dict):
            _add(_it.get("severity"),
                 _it.get("description") or _it.get("detail"),
                 _it.get("suggestion"))
    for _it in (data.get("top_issues") or []):      # ② knowledge_health
        if isinstance(_it, dict):
            _add(_it.get("severity"), _it.get("detail") or _it.get("kind"),
                 _it.get("suggestion"))
    for _it in (data.get("critical") or []):        # ③ fake_loops
        if isinstance(_it, dict):
            _sc = _it.get("score")
            _sev = "high" if (isinstance(_sc, (int, float)) and _sc <= 30) else "medium"
            _add(_sev, "%s.%s 闭环健康度 %s 分"
                 % (_it.get("module", "?"), _it.get("class", "?"), _sc),
                 "检查 save/load 闭环实现（空操作/路径不一致/异常静默）")
    _bysev = data.get("by_severity")                # ④ code_health（汇总口径）
    if isinstance(_bysev, dict):
        for _k, _v in _bysev.items():
            # ★T4：5 级下「warning 及以上」= rank ≤ 2（critical/high/medium）
            if _severity_rank(_k) <= 2 and isinstance(_v, (int, float)) and _v > 0:
                _add(_k, "代码质量问题 %s 条（ruff %s）[汇总型]" % (int(_v), _k))
    _sum = data.get("summary")                      # ⑤ production_consumption
    if isinstance(_sum, dict):
        _wp = _sum.get("no_consumer_with_producer")
        if isinstance(_wp, (int, float)) and _wp > 0:
            _add("medium", "写了没人读（源码有产出方、无消费方）%d 个" % int(_wp),
                 "确认是否废弃 → 删除 / 归档 / 补消费方")
        _npd = _sum.get("no_producer")
        if isinstance(_npd, (int, float)) and _npd > 0:
            _add("low", "疑似无产出（读了没人写）%d 个" % int(_npd),
                 "确认来源（外部输入 / 遗留读取）")
    return _out


def _collect_top_issues(profile: Any, limit: int = 5) -> list[dict]:
    """★主线第38批 T1（P2-213）：跨维度收集 issues，按归一化 severity 排序取前 N。"""
    _all: list[dict] = []
    for _f in _PROFILE_DICT_FIELDS:
        _all.extend(_dimension_issues(_f, getattr(profile, _f, None)))
    _all.sort(key=lambda x: (_severity_rank(x.get("severity")),
                             str(x.get("dimension", "")),
                             str(x.get("description", ""))))
    return _all[:max(1, int(limit or 5))]


def compute_overall_score(profile: Any) -> dict:
    """★主线第38批 T1（P2-213）：各**可用**维度评分的加权平均。

    可用维度 = `_score_of()` 取到数值 **且** 未显式标 ``status == "unavailable"``
    或 ``no_data is True``。可用维度 < 2 个 → ``overall_score`` 为 None（任务书 §T1.1）。

    Returns:
        ``{"overall_score": float|None, "available_dimensions": [...],
           "weighted": bool, "reason": str}``
    """
    _weights = _dimension_weights()
    _pairs: list[tuple[str, float]] = []
    for _f in _PROFILE_DICT_FIELDS:
        _d = getattr(profile, _f, None)
        if not isinstance(_d, dict) or not _d:
            continue
        if str(_d.get("status", "") or "").strip().lower() == "unavailable":
            continue
        if _d.get("no_data") is True:
            continue
        _sc = _score_of(_d)
        if _sc is None:
            continue
        _pairs.append((_f, float(_sc)))
    if len(_pairs) < 2:
        return {"overall_score": None,
                "available_dimensions": [p[0] for p in _pairs],
                "weighted": False, "reason": "可用维度不足 2 个"}
    _tw = 0.0
    _acc = 0.0
    _custom = 0
    for _f, _sc in _pairs:
        _w = _weights.get(_f)
        if isinstance(_w, (int, float)) and not isinstance(_w, bool) and _w > 0:
            _custom += 1
        else:
            _w = 1.0
        _tw += _w
        _acc += _sc * _w
    return {"overall_score": (round(_acc / _tw, 2) if _tw > 0 else None),
            "available_dimensions": [p[0] for p in _pairs],
            "weighted": _custom > 0, "reason": ""}


def _apply_overall(profile: Any) -> None:
    """★主线第38批 T1（P2-213）：把综合评分/等级/最严重问题写回画像。"""
    _r = compute_overall_score(profile)
    profile.overall_score = _r["overall_score"]
    profile.health_level = _health_level(_r["overall_score"])
    profile.top_issues = _collect_top_issues(profile, _top_issues_limit())


def _evolution_raw_stats(executor: Any) -> dict:
    """★主线第38批 T2（P2-214）：**只读**采集 SafeEvolutionExecutor 的执行统计。

    ★只读红线：仅调用 ``get_stats`` / ``get_crash_stats`` /
    ``get_strategy_learning_stats``（纯读取）与 ``_patch_manager.load_json``
    （读补丁历史文件）——**绝不**调用 ``verify_applied_patches``（会自动回滚）
    或任何 apply/rollback 接口。所有子读取独立 try，失败的项保留默认值。

    ★主线第41批 T4（P2-268）：补丁历史统计**只有一套实现**
    （``stats_from_patch_history``）；本函数是"执行器无 ``get_evolution_stats()``
    公共接口"时的**回退入口**，不再包含第二套算法。
    # _m38_t2_helpers
    """
    _raw = {"total_patches_generated": 0, "history_total": 0, "applied": 0,
            "verified": 0, "failed": 0, "rolled_back": 0, "pending_verify": 0,
            "avg_effectiveness": 0.0, "crash_total": 0, "strategies": 0}
    try:
        _gs = executor.get_stats()
        if isinstance(_gs, dict):
            _raw["total_patches_generated"] = int(
                _gs.get("total_patches_generated", 0) or 0)
    except Exception as e:
        _logger.debug("读取 evolution get_stats 失败: %s: %s", type(e).__name__, e)
    try:
        _cs = executor.get_crash_stats()
        if isinstance(_cs, dict):
            _raw["crash_total"] = int(_cs.get("total", 0) or 0)
    except Exception as e:
        _logger.debug("读取 evolution get_crash_stats 失败: %s: %s",
                      type(e).__name__, e)
    try:
        _pm = getattr(executor, "_patch_manager", None)
        _hist = _pm.load_json(_pm.get_history_file(), []) if _pm is not None else []
        if isinstance(_hist, list):
            # ★主线第41批 T4（P2-268）：**删除灰度回退分支** —— 统一口径成为唯一路径
            #   （与 SafeEvolutionExecutor.get_evolution_stats() 共用单一真相源
            #   nucleus.evolution.evolution_stats.stats_from_patch_history）。
            #   删除理由：第40批已用真实语料验证两路径结果完全一致；
            #   保留双份实现反而有再次漂移的风险（P2-247 的教训）。
            # _m41_t4
            from nucleus.evolution.evolution_stats import stats_from_patch_history
            _st = stats_from_patch_history(_hist)
            _raw["history_total"] = _st["total"]
            _raw["applied"] = _st["applied"]
            _raw["verified"] = _st["successful"]
            _raw["failed"] = _st["failed"]
            _raw["rolled_back"] = _st["rolled_back"]
            _raw["pending_verify"] = _st["pending_verify"]
            _raw["avg_effectiveness"] = _st["avg_effectiveness"]
    except Exception as e:
        _logger.debug("读取补丁历史失败: %s: %s", type(e).__name__, e)
    try:
        _sl = executor.get_strategy_learning_stats()
        if isinstance(_sl, dict):
            _raw["strategies"] = len(_sl.get("strategies", {}) or {})
    except Exception as e:
        _logger.debug("读取策略学习统计失败: %s: %s", type(e).__name__, e)
    return _raw


def _evolution_stats_via_public(executor: Any) -> dict | None:
    """★主线第39批 T3（P2-242）：经**公共接口**取进化统计并转为内部 raw 结构。

    ★只读：仅调用 ``get_evolution_stats()``（其本身保证无副作用）。

    Returns:
        内部 raw dict；公共接口不存在 / 返回非 dict / 抛异常时返回 **None**
        （调用方据此回退到私有访问路径，保持向后兼容）。
    """
    _fn = getattr(executor, "get_evolution_stats", None)
    if not callable(_fn):
        return None
    try:
        _pub = _fn()
    except Exception as e:
        _logger.warning("调用 get_evolution_stats() 失败: %s: %s",
                        type(e).__name__, e)
        return None
    if not isinstance(_pub, dict):
        _logger.warning("get_evolution_stats() 返回非 dict（%s），视为不可用",
                        type(_pub).__name__)
        return None
    try:
        return {
            "total_patches_generated": int(_pub.get("total_generated", 0) or 0),
            "history_total": int(_pub.get("total_patches", 0) or 0),
            "applied": int(_pub.get("applied_patches", 0) or 0),
            "verified": int(_pub.get("successful_patches", 0) or 0),
            "rolled_back": int(_pub.get("rolled_back_patches", 0) or 0),
            "pending_verify": int(_pub.get("pending_verify_patches", 0) or 0),
            "avg_effectiveness": float(_pub.get("avg_effectiveness", 0.0) or 0.0),
            "crash_total": int(_pub.get("crash_count", 0) or 0),
            "strategies": int(_pub.get("strategies", 0) or 0),
            # 透传公共接口原值，便于报告与诊断
            "public_stats": dict(_pub),
        }
    except Exception as e:
        _logger.warning("公共统计结构转换失败: %s: %s", type(e).__name__, e)
        return None


def _score_evolution_health(raw: Any) -> dict:
    """★主线第38批 T2（P2-214）：由原始统计算 evolution_health 分数与 issues。

    评分（任务书 §T2.3，权重可配置 `EVOLUTION_HEALTH_WEIGHTS`）：
        成功率 40% + 回滚率 30% + 执行稳定性 30%

    issues（任务书 §T2.4）：
        · 成功率 < `EVOLUTION_HEALTH_SUCCESS_MIN`(80%) → high
        · 回滚率 > `EVOLUTION_HEALTH_ROLLBACK_MAX`(20%)  → high
        · 无执行记录                                     → info
        另补：子进程崩溃 → medium/low（稳定性维度的可诊断信号）
    """
    _H = {"success": 0.4, "rollback": 0.3, "stability": 0.3}
    _smin, _rmax = 80.0, 20.0
    try:
        import config
        _w = getattr(config, "EVOLUTION_HEALTH_WEIGHTS", None)
        if isinstance(_w, dict) and _w:
            _H = _w
        _smin = float(getattr(config, "EVOLUTION_HEALTH_SUCCESS_MIN", 80.0))
        _rmax = float(getattr(config, "EVOLUTION_HEALTH_ROLLBACK_MAX", 20.0))
    except Exception as e:
        _logger.debug("读取进化健康权重失败，用默认: %s: %s", type(e).__name__, e)
    _raw = raw if isinstance(raw, dict) else {}
    _applied = int(_raw.get("applied", 0) or 0)
    _verified = int(_raw.get("verified", 0) or 0)
    _rolled = int(_raw.get("rolled_back", 0) or 0)
    _crash = int(_raw.get("crash_total", 0) or 0)
    _issues: list = []

    if _applied <= 0:
        _issues.append({"severity": "info", "type": "no_record",
                        "description": "暂无进化执行记录",
                        "suggestion": "等待自主进化循环产出补丁后自动统计"})
        return {"status": "unavailable", "score": None, "issues": _issues,
                "stats": dict(_raw), "note": "无已应用补丁记录"}

    _denom = max(1, _applied)
    _success_rate = round(100.0 * _verified / _denom, 2)
    _rollback_rate = round(100.0 * _rolled / _denom, 2)
    _crash_rate = 100.0 * _crash / _denom
    _stability = max(0.0, 100.0 - min(100.0, _crash_rate * 10.0))

    def _w_of(k: str) -> float:
        _v = _H.get(k, 0.0)
        return float(_v) if isinstance(_v, (int, float))             and not isinstance(_v, bool) else 0.0

    _tw = _w_of("success") + _w_of("rollback") + _w_of("stability")
    if _tw <= 0:
        _tw = 1.0
    _score = (_success_rate * _w_of("success")
              + (100.0 - _rollback_rate) * _w_of("rollback")
              + _stability * _w_of("stability")) / _tw
    _score = round(max(0.0, min(100.0, _score)), 2)

    if _success_rate < _smin:
        _issues.append({"severity": "high", "type": "low_success_rate",
                        "description": "进化补丁成功率 %.1f%%（< %.0f%%）"
                                       % (_success_rate, _smin),
                        "suggestion": "检查失败补丁的根因/回滚原因，收紧自动应用阈值"})
    if _rollback_rate > _rmax:
        _issues.append({"severity": "high", "type": "high_rollback_rate",
                        "description": "进化补丁回滚率 %.1f%%（> %.0f%%）"
                                       % (_rollback_rate, _rmax),
                        "suggestion": "提高补丁验证门槛；复核被回滚补丁的类型分布"})
    if _crash > 0:
        _issues.append({"severity": ("medium" if _crash_rate >= 10.0 else "low"),
                        "type": "evolution_crash",
                        "description": "进化子进程崩溃 %d 次" % _crash,
                        "suggestion": "查看 get_crash_stats 的原因分布，修复高频崩溃点"})
    if not _issues:
        _issues.append({"severity": "info", "type": "healthy",
                        "description": "进化执行健康（成功率 %.1f%% / 回滚率 %.1f%%）"
                                       % (_success_rate, _rollback_rate)})

    return {
        "status": "ok", "score": _score, "issues": _issues,
        "success_rate": _success_rate, "rollback_rate": _rollback_rate,
        "stability": round(_stability, 2),
        "avg_effectiveness": _raw.get("avg_effectiveness", 0.0),
        "stats": dict(_raw),
        "dimensions": {"success": _success_rate,
                       "rollback": round(100.0 - _rollback_rate, 2),
                       "stability": round(_stability, 2)},
    }



def _score_of(data: Any) -> float | None:
    """从维度 dict 中提取 0-100 评分（兼容 score / total_score / health_score）。"""
    if not isinstance(data, dict):
        return None
    for _k in ("score", "total_score", "health_score"):
        _v = data.get(_k)
        if isinstance(_v, (int, float)):
            return float(_v)
    return None


def _grade(score: float) -> str:
    """等级划分（与 KnowledgeQualityAnalyzer 保持一致口径）。"""
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 50:
        return "一般"
    if score >= 30:
        return "较差"
    return "危险"


def _brief(value: Any, limit: int = 3) -> str:
    """把 dict/list 摘要成一行短文本，避免报告过长。"""
    try:
        if isinstance(value, dict):
            _keys = list(value.keys())[:limit]
            _more = "" if len(value) <= limit else ", …"
            return "{%s%s}" % (", ".join(str(k) for k in _keys), _more)
        if isinstance(value, list):
            _more = "" if len(value) <= limit else ", …"
            return "[%s%s]" % (", ".join(str(x) for x in value[:limit]), _more)
        return str(value)
    except Exception as e:
        _logger.debug("报告摘要失败: %s: %s", type(e).__name__, e)
        return "?"


# ======================================================================
# 单例
# ======================================================================
_engine: SelfAwarenessEngine | None = None
_engine_lock = threading.Lock()


def get_self_awareness_engine() -> SelfAwarenessEngine:
    """返回全局自我认知引擎单例。"""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = SelfAwarenessEngine()
    return _engine


def reset_self_awareness_engine() -> None:
    """销毁单例（测试隔离用）。"""
    global _engine
    with _engine_lock:
        _engine = None
# _m50_t1_sa_done
