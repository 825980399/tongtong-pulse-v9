# -*- coding: utf-8 -*-
"""
QualityClosedLoop.py —— 质量闭环

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 质量问题发现-修复-验证闭环管理
机制: 基于ParamTuningClosedLoop类实现，包含10个核心方法
定位: 进化治理层
"""

import json
import math
import os
import threading
import time
from collections.abc import Callable
from typing import Any
from nucleus._silent_except import silent_exc


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ★第158批 第5刀（P2·空转#1/#4/#5/#9 观测查询入口）显式标注：
#   data/param_tuning/<loop>.jsonl 的**消费者 = functions/health_ui.py 的
#   /data/param_tuning 只读端点**（_serve_param_tuning_data）。本闭环为"写入方"，
#   记录经健康面板只读查阅，非二阶断点孤儿写入。请勿因"无源码读取方"误判为缺陷。
_RECORD_DIR = os.path.join(_PROJECT_ROOT, "data", "param_tuning")

# 共享 ParamPatchManager（两个闭环共用一份补丁历史/互斥锁；ParamPatchManager 自身带锁）
_shared_ppm = None
_shared_ppm_lock = threading.Lock()


def get_shared_param_patch_manager():
    """获取共享的 ParamPatchManager 单例（懒加载，避免循环导入）。"""
    global _shared_ppm
    with _shared_ppm_lock:
        if _shared_ppm is None:
            from nucleus.evolution.ParamPatchManager import ParamPatchManager
            _shared_ppm = ParamPatchManager()
        return _shared_ppm


# 通用阈值默认值（可被 config.QUALITY_CLOSED_LOOP_CONFIG 覆盖）
_DEFAULTS = {
    "bad_threshold": 3,         # 窗口内坏信号 ≥ 此数 → 触发一次调整
    "window_sec": 600,          # 坏信号统计窗口（秒）
    "cooldown_sec": 900,        # 两次调整之间的最小间隔（秒）
    "verify_window_sec": 600,   # 调整后的验证窗口（秒）
    "verify_min_obs": 5,        # 验证判定的最少观察数（不足则判"证据不足"）
    "max_obs": 200,             # 内存观察环形上限
}


class ParamTuningClosedLoop:
    """通用参数调优闭环控制器。

    一个闭环实例 = 一个参数域 + 一组策略 + 一个开关。
    器官侧只需在质量信号产生处调用 observe()，其余全部由本类完成。
    """

    def __init__(self, name: str,
                 is_enabled: Callable[[], bool],
                 strategies: list[dict[str, Any]],
                 log_fn: Callable[[str], None] | None = None,
                 cfg: dict[str, Any] | None = None):
        """
        Args:
            name:       闭环名（如 "搜索质量闭环"），也用于持久化文件名
            is_enabled: 灰度开关读取函数（每次 observe 实时读取，支持热切换）
            strategies: 策略列表，每项:
                {
                  "name":     策略名，
                  "signals":  set[str]，响应的坏信号名（按窗口内出现频次选主导策略），
                  "param":    参数名（必须在 ParamPatchManager 安全范围表内），
                  "step_fn":  Callable[[current_value], new_value|None]，
                              由当前值算新值；返回 None 表示该策略此时不适用
                }
            log_fn:     日志函数（接收单条 msg；器官侧包一层固定 INFO 级别）
            cfg:        阈值覆盖（来自 config.QUALITY_CLOSED_LOOP_CONFIG）
        """
        self.name = name
        self._is_enabled = is_enabled
        self._strategies = strategies
        self._log_fn = log_fn or (lambda _msg: None)
        _c = dict(_DEFAULTS)
        if cfg:
            _c.update({k: v for k, v in cfg.items() if k in _DEFAULTS})
        self._bad_threshold = int(_c["bad_threshold"])
        self._window_sec = float(_c["window_sec"])
        self._cooldown_sec = float(_c["cooldown_sec"])
        self._verify_window_sec = float(_c["verify_window_sec"])
        self._verify_min_obs = int(_c["verify_min_obs"])
        self._max_obs = int(_c["max_obs"])

        # 运行状态
        self._lock = threading.Lock()
        self._obs: list[tuple[float, bool, str]] = []  # (ts, bad, signal)
        self._trial: dict[str, Any] | None = None      # 进行中的验证试验
        self._last_adjust = 0.0
        self._adjustment_log: list[dict[str, Any]] = []  # 内存审计记录（上限50）
        self._max_log = 50

    # ========== 对外接口 ==========

    def observe(self, signal: dict[str, Any]) -> bool:
        """记录一次质量观察；满足触发条件时自动调整参数。

        Args:
            signal: {"signal": 信号名, "bad": 是否坏信号, ...其余字段自由}

        Returns:
            True = 本次调用触发了一次参数调整（已应用，进入验证窗口）
        """
        try:
            if not self._is_enabled():
                return False
            now = time.time()
            _sig_name = str(signal.get("signal", "unknown"))
            _bad = bool(signal.get("bad"))
            with self._lock:
                self._obs.append((now, _bad, _sig_name))
                self._trim()
                # 1) 验证窗口判定优先
                if self._trial is not None:
                    self._trial["obs_since_apply"] += 1  # ★修复：验证期观察计数
                    if (now >= self._trial["verify_until"]
                            or self._trial["obs_since_apply"] >= self._verify_min_obs):
                        self._finish_trial(now)
                    return False
                # 2) 冷却期
                if now - self._last_adjust < self._cooldown_sec:
                    return False
                # 3) 窗口坏信号计数
                _window_bad = [(ts, sig) for (ts, bad, sig) in self._obs
                               if bad and now - ts <= self._window_sec]
                if len(_window_bad) < self._bad_threshold:
                    return False
                return self._try_adjust(_window_bad, now)
        except Exception as _e:
            self._log_safe(f"观察处理异常已忽略: {_e}")
            return False

    def get_adjustment_log(self, limit: int = 20) -> list[dict[str, Any]]:
        """取最近的调整记录（供状态查询/上层消费）。"""
        with self._lock:
            return list(self._adjustment_log[-limit:])

    def get_state(self) -> dict[str, Any]:
        """当前闭环状态（供 get_stats 类接口调用）。"""
        with self._lock:
            return {
                "name": self.name,
                "enabled": bool(self._is_enabled()),
                "obs_in_memory": len(self._obs),
                "trial_active": self._trial is not None,
                "trial_param": (self._trial or {}).get("param"),
                "last_adjust_at": self._last_adjust,
                "adjustment_count": len(self._adjustment_log),
            }

    # ========== 内部实现 ==========

    def _trim(self):
        while len(self._obs) > self._max_obs:
            self._obs.pop(0)

    def _log_safe(self, msg: str):
        try:
            self._log_fn(f"[{self.name}] {msg}")
        except Exception as e:
            silent_exc(e, "nucleus/evolution/QualityClosedLoop.py:168:质量闭环异常", level="warning")

    def _bad_ratio(self, since_ts: float, until_ts: float) -> tuple[float, int]:
        """[since_ts, until_ts] 内坏信号占比与观察总数。"""
        _in = [o for o in self._obs if since_ts <= o[0] <= until_ts]
        if not _in:
            return 0.0, 0
        _bad = sum(1 for o in _in if o[1])
        return _bad / len(_in), len(_in)

    def _pick_strategies(self, window_bad: list[tuple[float, str]]) -> list[tuple[dict, str]]:
        """按窗口内坏信号频次返回**按优先级排序的候选策略列表**（子串匹配）。

        ★A-13（2026-09-08，P1-15）：支持参数优先级队列——单参数到边界后
        自动降级到下一个候选策略，避免闭环空转（开关 ENABLE_LOOP_MULTI_PARAM）。

        返回 [(strategy, dominant_signal), ...] 按频次降序；空列表=无策略命中。
        """
        _freq: dict[str, int] = {}
        for _ts, _sig in window_bad:
            for _st in self._strategies:
                for _s in _st["signals"]:
                    if _s and _s in _sig:
                        _freq[_s] = _freq.get(_s, 0) + 1

        def _st_hits(_st):
            return max((_freq.get(_s, 0) for _s in _st["signals"]), default=0)

        _out: list[tuple[dict, str]] = []
        for _st in self._strategies:
            _hit = _st_hits(_st)
            if _hit > 0:
                _dom = max(((s, _freq.get(s, 0)) for s in _st["signals"]),
                           key=lambda x: x[1])[0]
                _out.append((_st, _dom))
        _out.sort(key=lambda x: -_st_hits(x[0]))
        return _out

    def _current_param_value(self, param: str):
        try:
            from config import RUNTIME_PARAMS
            return RUNTIME_PARAMS.get(param)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.QualityClosedLoop::_current_param_value L212")
            return None

    def _try_adjust(self, window_bad: list[tuple[float, str]], now: float) -> bool:
        """尝试生成并应用一次参数补丁。

        ★A-13（P1-15）：多参数模式（ENABLE_LOOP_MULTI_PARAM）下按优先级**依序尝试**
        候选策略——首选策略到参数边界（step 返回 None）时自动降级到下一个，
        避免闭环空转；单参数模式（默认关）保持原行为：只试首个命中策略。
        """
        # 多参数开关（每次触发时读取，支持热切换）
        _multi = False
        try:
            import config as _cfg
            _multi = bool(getattr(_cfg, 'ENABLE_LOOP_MULTI_PARAM', False))
        except Exception:
            _multi = False

        _candidates = self._pick_strategies(window_bad)
        if not _candidates:
            self._log_safe("坏信号频繁但无对应可调策略，仅记录不动作")
            self._last_adjust = now  # 避免每条坏信号都重复打日志
            return False
        if not _multi:
            _candidates = _candidates[:1]  # 单参数模式：保持原行为

        _chosen = _new = _dominant = _cur = None
        for _st, _dom in _candidates:
            _cur = self._current_param_value(_st["param"])
            if _cur is None:
                self._log_safe(f"参数{_st['param']}不在RUNTIME_PARAMS中，跳过")
                continue
            _nv = None
            try:
                _nv = _st["step_fn"](_cur)
            except Exception as _e:
                self._log_safe(f"策略{_st['name']}计算新值异常: {_e}")
            if _nv is not None and _nv != _cur:
                _chosen, _new, _dominant = _st, _nv, _dom
                break
            self._log_safe(f"策略{_st['name']}此时不适用（param={_st['param']}={_cur}）"
                           + ("，降级到下一候选" if _multi else ""))
        if _chosen is None or _new is None:
            self._log_safe("全部候选策略均不适用（参数到边界），仅记录不动作")
            self._last_adjust = now
            return False

        _st = _chosen
        _param = _st["param"]

        _ppm = get_shared_param_patch_manager()
        _reason = (f"{self.name}: {_dominant} 频发"
                   f"（{len(window_bad)}次/{int(self._window_sec)}s）→ {_st['name']}")
        _patch = _ppm.generate_patch(_param, _new, _reason,
                                     source=f"closed_loop:{self.name}")
        if not _patch or _patch.get("status") == "rejected":
            self._log_safe(f"补丁被安全检查拒绝: {_patch.get('reason') if _patch else 'None'}")
            self._last_adjust = now
            return False
        _v = _ppm.verify_patch(_patch)
        if not _v.get("verified"):
            self._log_safe(f"补丁副本验证失败: {_v.get('issues')}")
            self._last_adjust = now
            return False
        _r = _ppm.apply_patch(_patch)
        if not _r.get("applied"):
            self._log_safe(f"补丁应用失败: {_r.get('error')}")
            self._last_adjust = now
            return False

        # 应用成功 → 进入验证窗口
        # ★applied_at 取**应用之后**的时间（apply_patch 内含 sleep(1)），
        #   保证验证窗口 [applied_at, now] 严格排除触发期的坏信号样本
        _applied_at = time.time()
        _base_ratio, _base_n = self._bad_ratio(now - self._window_sec, now)
        self._trial = {
            "param": _param,
            "patch": _patch,
            "strategy": _st["name"],
            "dominant_signal": _dominant,
            "applied_at": _applied_at,
            "verify_until": _applied_at + self._verify_window_sec,
            "baseline_ratio": _base_ratio,
            "baseline_n": _base_n,
            "obs_since_apply": 0,
        }
        self._last_adjust = now
        self._log_safe(
            f"触发调整: {_param} {_cur}→{_new}（{_st['name']}，信号={_dominant}"
            f"×{len(window_bad)}/{int(self._window_sec)}s），"
            f"进入验证窗口{int(self._verify_window_sec)}s（基线坏信号率={_base_ratio:.2f}）")
        return True

    def _finish_trial(self, now: float):
        """验证窗口结束：对比坏信号率，保留或回滚，写审计记录。"""
        _t = self._trial
        self._trial = None
        if not _t:
            return
        _patch = _t["patch"]
        _post_ratio, _post_n = self._bad_ratio(_t["applied_at"], now)
        _base_ratio = _t["baseline_ratio"]

        if _post_n < 2:
            _verdict, _action = "证据不足（验证期内无足够观察），默认保留", "keep"
        elif _post_ratio < _base_ratio:
            _verdict, _action = f"改善（{_base_ratio:.2f}→{_post_ratio:.2f}）", "keep"
        elif abs(_post_ratio - _base_ratio) < 1e-9:
            _verdict, _action = f"持平（{_base_ratio:.2f}）", "keep"
        else:
            _verdict, _action = f"恶化（{_base_ratio:.2f}→{_post_ratio:.2f}）", "rollback"

        if _action == "rollback":
            _rb = get_shared_param_patch_manager().rollback_patch(_patch)
            if not _rb.get("rolled_back"):
                self._log_safe(f"回滚失败: {_rb.get('error')}（补丁{_patch.get('id')}保持应用态，需人工介入）")

        _record = {
            "时间": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "调整前状态": f"基线坏信号率={_base_ratio:.2f}（{_t['baseline_n']}条观察），"
                        f"信号={_t['dominant_signal']}",
            "调整内容": f"{_t['param']} {_patch.get('old_value')}→{_patch.get('new_value')}"
                      f"（{_t['strategy']}，patch_id={_patch.get('id')}）",
            "调整后效果": f"坏信号率={_post_ratio:.2f}（{_post_n}条观察）→ {_verdict}",
            "是否保留": "保留" if _action == "keep" else "回滚",
        }
        self._adjustment_log.append(_record)
        if len(self._adjustment_log) > self._max_log:
            del self._adjustment_log[:len(self._adjustment_log) - self._max_log]
        self._log_safe(
            f"验证完成: {_record['调整内容'][:60]} | {_record['调整后效果'][:50]} "
            f"| 处置={_record['是否保留']}")
        self._persist_record(_record)

    def _persist_record(self, record: dict[str, Any]):
        """审计记录持久化（JSONL，追加写；失败仅降级日志，不影响主流程）。"""
        try:
            os.makedirs(_RECORD_DIR, exist_ok=True)
            _path = os.path.join(_RECORD_DIR, f"{self.name}.jsonl")
            with open(_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as _e:
            self._log_safe(f"审计记录写盘失败(已忽略): {_e}")


# ========== 工厂：两个具体闭环 ==========

def _load_loop_cfg() -> dict[str, Any]:
    try:
        from config import QUALITY_CLOSED_LOOP_CONFIG
        return dict(QUALITY_CLOSED_LOOP_CONFIG or {})
    except Exception:
        return {}


def _make_enabled(attr_name: str) -> Callable[[], bool]:
    def _enabled() -> bool:
        try:
            import config
            return bool(getattr(config, attr_name, False))
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.QualityClosedLoop::_enabled L372")
            return False
    return _enabled


def create_search_quality_loop(log_fn=None) -> ParamTuningClosedLoop:
    """任务2：搜索质量闭环。

    参数域（**仅含已确认有真实消费点**的参数）：
        search_cooldown_seconds —— PulseController.py:2177 直接消费（搜索冷却秒数）。
        ⚠️ search_max_articles / search_low_overlap_threshold /
           innerworld_search_quality_threshold / search_quality_threshold
           当前在器官侧**无消费点**（死参数），纳入只会造成"调了没用"的假闭环，
           故不纳入。是否接通这些参数留给内部协作者决策（见交付报告遗留项）。

    策略：
        S1 止损——"stage1_terminate"（阶段1关键词与主题无关，频繁打转）主导时
           拉长冷却 ×1.3（上限300s）：减少无效搜索频率；
        S2 提频——"search_empty"（阶段2无文章产出）主导时
           缩短冷却 ×0.8（下限5s）：更快尝试新方向/新源。
    """
    def _cooldown_up(cur):
        try:
            _n = min(300, int(math.ceil(cur * 1.3)))
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.QualityClosedLoop::_cooldown_up L396")
            return None
        return _n if _n > cur else None

    def _cooldown_down(cur):
        try:
            _n = max(5, int(math.floor(cur * 0.8)))
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.QualityClosedLoop::_cooldown_down L403")
            return None
        return _n if _n < cur else None

    return ParamTuningClosedLoop(
        name="搜索质量闭环",
        is_enabled=_make_enabled("ENABLE_SEARCH_QUALITY_CLOSED_LOOP"),
        strategies=[
            {
                "name": "止损·拉长搜索冷却",
                "signals": {"stage1_terminate"},
                "param": "search_cooldown_seconds",
                "step_fn": _cooldown_up,
            },
            {
                "name": "提频·缩短搜索冷却",
                "signals": {"search_empty"},
                "param": "search_cooldown_seconds",
                "step_fn": _cooldown_down,
            },
        ],
        log_fn=log_fn,
        cfg=_load_loop_cfg(),
    )


def create_digestion_quality_loop(log_fn=None) -> ParamTuningClosedLoop:
    """任务3：消化质量闭环。

    参数域（**仅含已确认有真实消费点**的参数）：
        stomach_keyword_min_length —— PulseStomach.py:1082(提取)/:1573(质量校验) 消费；
        stomach_purity_threshold   —— PulseStomach.py:1084(兜底提取纯度过滤) 消费。
        ⚠️ stomach_min_keywords 当前无消费点（refresh 后写进 _min_keywords 但从未读取），
           属死参数，不纳入。

    策略：
        S1 放宽有效关键词口径——"有效关键词过少"类信号主导时
           stomach_keyword_min_length -1（下限1）；
        S2 放宽兜底提取纯度——其他关键词类信号（含"关键词"字样）主导时
           stomach_purity_threshold -0.05（下限0.1）。
        路径类/密度类信号无对应可调参数（路径分配与内容本身决定），仅记录不动作。
    """
    def _min_len_down(cur):
        try:
            _n = max(1, int(cur) - 1)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.QualityClosedLoop::_min_len_down L448")
            return None
        return _n if _n < cur else None

    def _purity_down(cur):
        try:
            _n = round(max(0.1, float(cur) - 0.05), 3)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.QualityClosedLoop::_purity_down L455")
            return None
        return _n if _n < cur else None

    return ParamTuningClosedLoop(
        name="消化质量闭环",
        is_enabled=_make_enabled("ENABLE_DIGESTION_QUALITY_CLOSED_LOOP"),
        strategies=[
            {
                "name": "放宽·关键词最小长度-1",
                "signals": {"有效关键词过少"},
                "param": "stomach_keyword_min_length",
                "step_fn": _min_len_down,
            },
            {
                "name": "放宽·兜底提取纯度阈值-0.05",
                # ★A-13：min_length 到下限(1)后的**级联候选**——同一信号降级到本策略
                "signals": {"有效关键词过少", "关键词不足", "纯度"},
                "param": "stomach_purity_threshold",
                "step_fn": _purity_down,
            },
        ],
        log_fn=log_fn,
        cfg=_load_loop_cfg(),
    )
