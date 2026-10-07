# -*- coding: utf-8 -*-
"""
PulseLegs —— 双腿器官 · 网络抓取与主动自学循环

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 LegsEvent.FETCH 与好奇心/兴趣信号，执行网页抓取、RSS 收集与四种主动学习策略（search / trending / explore / builtin），把新知识交胃消化。
机制: on_pulse → _on_fetch 走 _create_session / _fetch_url / _search_web；_on_curiosity_tick 与 _on_interest_changed 驱动 _trigger_active_learn → _run_learn_strategy，分派到 _strategy_search / _strategy_trending / _strategy_explore / _strategy_builtin；_learn_worker_loop 搭配 _learn_task_runner 后台异步消费 _add_learn_task 入队任务，_rebuild_learn_pool 维护学习池；_is_safe_content 做内容安全过滤，_is_system_busy / _is_load_critical 做负载准入，on_survival_low / on_survival_high 按生存信号调节节奏；_write_learn_log 落盘并 _emit DigestEvent.KNOWLEDGE；budget_guard 施加资源预算闸门。
定位: 运动层的「长时行走器官」，是曈曈在无人值守时持续自学的双腿。
"""
import difflib
from config import TIMEOUT_CONFIG

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import random
import re
import threading
import time

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    DigestEvent,
    InterestEvent,
    LegsEvent,
    LogLevel,
    SubconsciousEvent,
    SystemEvent,
    ControllerEvent,
    DeviceEvent,
    Event,
    TouchEvent,
)
from nucleus.knowledge_noise_filter import clean_content_text
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


def budget_guard(consumer: str = "search"):
    """★登顶路线图-山2：资源预算守卫装饰器。

    对高耗能动作（搜索/抓取）施加全局资源预算闸门：
    - 高负载时 acquire 失败 → 跳过本次动作（自我保护），返回 None。
    - 成功 → 执行后 finally 释放，保证 acquire/release 严格成对，零泄漏。
    - 预算模块缺失/异常时透明放行（零侵入，不影响主链路）。
    """
    def _decorator(func):
        import functools

        @functools.wraps(func)
        def _wrapper(*args, **kwargs):
            _budget = None
            try:
                from nucleus.ResourceBudget import get_resource_budget
                _budget = get_resource_budget()
                if not _budget.acquire(consumer):
                    _log = getattr(args[0], "_log", None)
                    if _log:
                        _log(LogLevel.DEBUG,
                             f"资源预算闸门：高负载下[{consumer}]动作跳过（自我保护）")
                    return None
            except Exception:
                _budget = None  # 预算模块不可用 → 透明放行
            try:
                return func(*args, **kwargs)
            finally:
                if _budget is not None:
                    try:
                        _budget.release(consumer)
                    except Exception as e:
                        _log = getattr(args[0], "_log", None)
                        if _log:
                            _log(LogLevel.ERROR, f'异常: {e}')
        return _wrapper
    return _decorator


class PulseLegs(BasePulseOrgan):
    """脉冲驱动双腿（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'legs_max_failures' in _rp and hasattr(self, '_max_failures'):
                self._max_failures = _rp['legs_max_failures']
            if 'legs_cooldown_seconds' in _rp and hasattr(self, '_cooldown_seconds'):
                self._cooldown_seconds = _rp['legs_cooldown_seconds']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "双腿"):
        super().__init__(organ_name)
        # 热点/榜单源（固定高质量源，获取当前热点）
        self._trending_sources = [
            "https://www.zhihu.com/hot",
            "https://tophub.today/",
            "https://news.qq.com/",
            "https://www.guancha.cn/",
            "https://www.thepaper.cn/",
            "https://www.36kr.com/information/web_news/",
            "https://www.ithome.com/",
            "https://sspai.com/",
            "https://www.oschina.net/blog",
            "https://www.ruanyifeng.com/blog/",
        ]

        # 通用搜索模板（移除已失效的rsshub源，优先使用国内可达的引擎）
        self._search_templates = [
            "https://cn.bing.com/search?q={query}",
            "https://www.baidu.com/s?wd={query}",
            "https://www.dogedoge.com/search?q={query}",
            "https://en.wikipedia.org/w/api.php?action=opensearch&search={query}&limit=1&format=json",
            "https://www.bing.com/search?q={query}",
        ]

        self._source_index = 0
        self._cpu_usage = 0.0       # 缓存触觉上报的CPU使用率
        self._mem_usage = 0.0       # 缓存触觉上报的内存使用率
        self._consecutive_failures = 0
        self.node_pool = None          # 节点池引用（用于内部知识重组）
                # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._max_failures = _rp.get('legs_max_failures', 3)
            self._cooldown_seconds = _rp.get('legs_cooldown_seconds', 1800)
        except Exception:
            self._max_failures = 3
            self._cooldown_seconds = 1800
        self._search_failures = 0
        self._fetch_count = 0
        self._success_count = 0
        self._fail_count = 0
        self._learn_count = 0      # 主动学习次数

        # ===== 多路并行学习引擎 =====
        self._learn_queue: list[dict[str, Any]] = []   # 优先级队列
        self._learn_pool = None                         # 学习线程池
        self._learn_pool_size = 3                       # 最多同时3路学习
        self._learn_active_directions: set = set()      # 正在学习的方向（去重用）
        self._learn_queue_lock = threading.Lock()       # 队列锁
        self._learn_worker_running = False              # 学习工作线程标记
        # 主动学习定时器
        # ★主线第49批 T2（P2-328）D：间隔配置化（原硬编码 90s）。
        #   默认值先行、config 覆盖（遵循本文件 :182-185 的「配置优先」顺序约定）。
        self._learn_interval = 90.0
        self._learn_fast_interval = 45.0
        self._learn_slow_interval = 600.0
        try:
            import config as _m49_cfg
            self._learn_interval = float(getattr(
                _m49_cfg, "LEARNING_INTERVAL_SECONDS", self._learn_interval))
            self._learn_fast_interval = float(getattr(
                _m49_cfg, "LEARNING_FAST_INTERVAL_SECONDS", self._learn_fast_interval))
            self._learn_slow_interval = float(getattr(
                _m49_cfg, "LEARNING_SLOW_INTERVAL_SECONDS", self._learn_slow_interval))
        except Exception as _m49_e:
            self._log(LogLevel.DEBUG,
                      f"[M49-T2] 学习间隔配置读取失败（用默认）: "
                      f"{type(_m49_e).__name__}")
        # ★主线第49批 T2：主题去重 + 入库反馈状态
        from collections import deque as _m49_deque
        self._m49_recent_topics = _m49_deque(maxlen=32)
        self._m49_skipped_dup = 0
        self._m49_skipped_irrelevant = 0
        self._m49_digest_rejected = 0
        self._m49_digest_accepted = 0
        self._m49_stomach_ref = None
        self._m49_feedback_seen = set()
        self._learn_timer: threading.Timer | None = None
        self._learn_running = False
        self._curiosity_active = False    # 好奇心是否活跃
        self._compute_level = "medium"   # 计算能力等级（默认中等，收到能力更新后调整）
        self._last_curiosity_time = 0.0   # 上次好奇心触发时间

        # 兴趣方向缓存
        self._interest_directions: list[str] = ["人工智能", "编程开发", "技术架构"]
        self._interest_weights: dict[str, float] = {}

        # 安全词表（网络内容第一道过滤）
        # ★第161批段B B1：改为 config 优先 + 硬编码兜底（词表内容零变更）。
        #   顺序约束见下方 :211-214 注释——默认值必须先于配置加载执行。
        self._unsafe_keywords = [
            "暴力", "色情", "赌博", "毒品", "武器制造",
            "黑客攻击", "病毒制作", "诈骗", "自杀",
            "歧视", "仇恨", "恐怖",
        ]
        self._load_legs_config()

        # 知识重组兜底模板
        self._learn_templates = [
            "自主学习了{keywords}相关内容：{summary}",
            "在{keywords}领域有了新的理解：{summary}",
            "通过回顾已有知识，对{keywords}有了更深的认识：{summary}",
        ]
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        # ★属性初始化完整性补全（自动审查添加）
        self._builtin_knowledge_path = ""
        self._session = None
        self._survival_state = {}
        self._builtin_knowledge = self._load_builtin_knowledge()
        # ★P0修复：初始化搜索冷却时间戳（之前缺失导致AttributeError）
        self._cooldown_until = 0.0

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == LegsEvent.FETCH:
            return self._on_fetch(payload)
        elif event_type == InterestEvent.CHANGED:
            return self._on_interest_changed(payload)
        elif event_type == SubconsciousEvent.CURIOSITY_TICK:
            return self._on_curiosity_tick(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == TouchEvent.HARDWARE_SNAPSHOT:
            return self._on_hardware_snapshot(payload)
        elif event_type == DeviceEvent.CAPABILITY_UPDATE:
            return self._on_capability_update(payload)
        elif event_type == DigestEvent.KNOWLEDGE:
            # 收到灵感脉冲时触发定向学习
            trigger_reason = payload.get("trigger_reason", "")
            if trigger_reason == "creative.insight":
                keywords = payload.get("keywords", [])
                if keywords:
                    direction = "跨领域探索: " + "、".join(keywords[:2])
                    self._add_learn_task(direction, "normal")
                    return {"status": "creative_learning_queued", "direction": direction}
        elif event_type == Event.LEGS_LEARN_NOW:
            return self._on_learn_now(payload)
        return None

    # ========== 事件处理 ==========

    def _on_fetch(self, payload: dict) -> dict[str, Any]:
        from nucleus.knowledge.KnowledgeAcquisitionRouter import report_browser_outcome
        if time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            return {"status": "cooldown", "remaining_seconds": remaining}

        if self._is_system_busy():
            return {"status": "skipped", "reason": "系统繁忙，跳过抓取"}

        # 新增：信息场负载感知，critical模式下暂停抓取
        if self._is_load_critical():
            return {"status": "skipped", "reason": "系统负载临界，暂停抓取"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._fetch_count += 1

        all_sources = self._trending_sources.copy()
        source_url = all_sources[self._source_index % len(all_sources)]
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._source_index += 1

        try:
            content = self._fetch_url(source_url)
            if content:
                # v9.5: 知识消化脉冲标记为L2认知思考层
                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": content[:1000],
                    "source_organ": "双腿",
                    "trigger_reason": f"legs.fetch:{source_url}",
                    "view_mode": "OUTER_VIEW",
                    "source_url": source_url,   # ★R1
                }, priority=4, layer="L2")

                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._success_count += 1
                self._consecutive_failures = 0

                self._log(LogLevel.DEBUG, f"抓取成功: {source_url} → {len(content)}字符")
                report_browser_outcome(True)
                return {"status": "success", "source": source_url, "content_length": len(content)}
            else:
                self._handle_failure(source_url, "空内容")
                report_browser_outcome(False)
                return {"status": "empty", "source": source_url}

        except Exception as e:
            self._handle_failure(source_url, str(e))
            report_browser_outcome(False)
            return {"status": "error", "source": source_url, "error": str(e)[:100]}
    # ========== 生命周期 ==========

    def start(self):
        """启动双腿，开启主动学习定时器 + 多路并行学习引擎"""
        super().start()
        self._learn_running = True
        self._learn_worker_running = True
        self._learn_pool = ThreadPoolExecutor(max_workers=self._learn_pool_size, thread_name_prefix="legs_learn")
        # 启动学习工作线程（从队列取任务分发给线程池）
        threading.Thread(target=self._learn_worker_loop, daemon=True).start()
        self._schedule_learn_timer()
        self._log(LogLevel.INFO, f"多路并行学习引擎已启动 (间隔{self._learn_interval}s, {self._learn_pool_size}路并行)")

    def stop(self):
        """停止双腿"""
        self._learn_running = False
        self._learn_worker_running = False
        if self._learn_timer:
            self._learn_timer.cancel()
            self._learn_timer = None
        if self._learn_pool:
            # 不等待正在执行的任务，直接关闭（网络请求可能超时很久）
            self._learn_pool.shutdown(wait=False)
            self._learn_pool = None
        super().stop()
        self._log(LogLevel.INFO, f"已停止，抓取{self._fetch_count}次, 主动学习{self._learn_count}次")
    def _on_interest_changed(self, payload: dict) -> dict[str, Any]:
        """缓存当前兴趣方向"""
        boosted = payload.get("boosted", [])
        interests = payload.get("current_interests", {})
        if interests:
            self._interest_weights = interests
            # 取权重最高的3个方向
            sorted_items = sorted(interests.items(), key=lambda x: x[1], reverse=True)
            self._interest_directions = [item[0] for item in sorted_items[:5]]
        elif boosted:
            for dim in boosted:
                if dim not in self._interest_directions:
                    self._interest_directions.insert(0, dim)
            self._interest_directions = self._interest_directions[:5]
        return {"status": "cached", "directions": self._interest_directions[:3]}

    def _on_curiosity_tick(self, payload: dict) -> dict[str, Any]:
        """收到好奇心触发信号，标记为活跃状态"""
        self._curiosity_active = True
        self._last_curiosity_time = time.time()
        return {"status": "curiosity_active"}
    def _on_capability_update(self, payload: dict) -> dict[str, Any]:
        """收到设备管理器的硬件能力枚举脉冲，动态调整学习并发数"""
        caps = payload.get("capabilities", {})
        new_level = caps.get("compute.level", "medium")
        old_level = self._compute_level
        self._compute_level = new_level

        # 根据计算等级 + 全局并行调度器确定学习并发数（★FIX: 硬件自适应并行度）
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            _global_p = get_parallel_scheduler().get_parallelism()
        except Exception:
            _global_p = 4
        level_map = {
            "high": max(3, _global_p // 2),
            "medium": max(2, _global_p // 3),
            "basic": 1,
        }
        new_size = level_map.get(new_level, max(2, _global_p // 3))

        if new_size != self._learn_pool_size or new_level != old_level:
            self._log(LogLevel.INFO,
                      f"计算能力变化: {old_level} → {new_level}, "
                      f"学习并发数: {self._learn_pool_size} → {new_size}")
            self._learn_pool_size = new_size
            self._rebuild_learn_pool()

        return {"status": "updated", "compute_level": new_level, "learn_pool_size": new_size}

    def _rebuild_learn_pool(self):
        """重建学习线程池（在能力变化时调用）"""
        with self._learn_queue_lock:
            old_pool = self._learn_pool
            self._learn_pool = ThreadPoolExecutor(
                max_workers=self._learn_pool_size,
                thread_name_prefix="legs_learn"
            )
        if old_pool:
            try:
                old_pool.shutdown(wait=False)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _on_learn_now(self, payload: dict) -> dict[str, Any]:
        """紧急学习请求：加入优先级队列"""
        direction = payload.get("direction", "")
        priority = payload.get("priority", "normal")

        if not direction:
            return {"status": "skipped", "reason": "无方向"}

        # 新增：紧急学习也遵守负载感知
        if self._is_load_critical():
            return {"status": "skipped", "reason": "系统负载临界，暂停学习"}

        self._add_learn_task(direction, priority)
        return {"status": "queued", "direction": direction, "priority": priority}
    # ========== ★主线第49批 T2（P2-328）：学习主题治理 ==========

    def _m49_topic_dedup_on(self) -> bool:
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_LEARNING_TOPIC_DEDUP", True))
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseLegs::_m49_topic_dedup_on L405")
            return True

    def _m49_topic_filter_on(self) -> bool:
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_LEARNING_TOPIC_FILTER", True))
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseLegs::_m49_topic_filter_on L412")
            return True

    def _m49_digest_feedback_on(self) -> bool:
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_LEARNING_DIGEST_FEEDBACK", True))
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseLegs::_m49_digest_feedback_on L419")
            return True

    def _m49_dedup_cfg(self):
        _win, _ratio = 20, 0.8
        try:
            import config as _c
            _win = int(getattr(_c, "LEARNING_TOPIC_DEDUP_WINDOW", 20))
            _ratio = float(getattr(_c, "LEARNING_TOPIC_DEDUP_RATIO", 0.8))
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"[M49-T2] 去重配置读取失败（用默认）: {type(_e).__name__}")
        return max(0, _win), min(max(_ratio, 0.1), 1.0)

    def _m49_is_dialog_pollution(self, direction: str) -> bool:
        """★B：判定主题是否是「用户对话碎片」污染。

        实测样本：`我 是`（用户输入被拆成单字）、`你好 未来的`。
        判据（任一即污染，保守取反）：
          1) 空串 / 极短（<配置最短长度）
          2) 含空格分隔的单字序列（如 `我 是` / `你 好 告`）
          3) 命中对话污染提示词（你好/谢谢/你呢/我是谁 等）且无技术关键词
        """
        d = (direction or "").strip()
        if not d:
            return True
        try:
            import config as _c
            _min_len = int(getattr(_c, "LEARNING_MIN_TOPIC_CHARS", 4))
        except Exception:
            _min_len = 4
        # 1) 极短
        if len(d) < _min_len:
            return True
        # 2) 单字序列（报告中的 `我 是` 即此形态）
        _parts = [p for p in d.split() if p]
        if len(_parts) >= 2 and all(len(p) <= 2 for p in _parts):
            return True
        # 3) 对话提示词 ∧ 无技术词
        _dialog_cues = ("你好", "谢谢", "你呢", "我是谁", "你是谁",
                        "你好吗", "再见", "在吗", "怎么样")
        _tech_cues = ("算法", "框架", "架构", "编程", "模型", "数据",
                      "python", "人工智能", "系统", "代码", "网络",
                      "知识", "学习", "推理", "设计", "算法")
        if any(c in d for c in _dialog_cues) and not any(t in d.lower() for t in _tech_cues):
            return True
        return False

    def _m49_is_relevant_topic(self, direction: str) -> bool:
        """★B：主题相关性（与框架自身/技术领域相关）。"""
        if not self._m49_topic_filter_on():
            return True
        return not self._m49_is_dialog_pollution(direction)

    def _m49_dedup_hit(self, direction: str):
        """★A：返回与最近主题相似度超阈值的历史主题（无则 None）。"""
        if not self._m49_topic_dedup_on():
            return None
        _win, _ratio = self._m49_dedup_cfg()
        if _win <= 0:
            # 窗口 ≤ 0 视为**关闭去重**（显式语义，便于运维按需停用）
            return None
        _recent = list(self._m49_recent_topics)[-_win:]
        for _old in _recent:
            if _old == direction:
                return _old
            try:
                if difflib.SequenceMatcher(None, _old, direction).ratio() >= _ratio:
                    return _old
            except Exception as _e:
                self._log(LogLevel.DEBUG,
                          f"[M49-T2] 相似度计算失败（跳过本条）: {type(_e).__name__}")
        return None

    def _m49_sync_digest_feedback(self) -> int:
        """★C：从胃的「被拒消化环形缓冲」还原真实入库结果。

        ★零改动胃：`PulseStomach._record_rejected_digestion` 已在每次拦截时
        追加一条元数据（source_organ / trigger_reason / reason）。
        本方法**只读**该环形缓冲，按 trigger_reason 里的
        `active_learn:<strategy>:<direction>` 归因到双腿自己的学习任务。
        → 修正「被质量门槛拦截却报学习成功」的虚假成功。

        返回本次新增识别到的被拒数量。
        """
        if not self._m49_digest_feedback_on():
            return 0
        _st = getattr(self, "_m49_stomach_ref", None)
        if _st is None:
            return 0
        _ring = getattr(_st, "_rejected_digestion_ring", None)
        if not _ring:
            return 0
        _new = 0
        try:
            for _rec in list(_ring):
                _sig = (_rec.get("ts"), _rec.get("trigger_reason"), _rec.get("reason"))
                if _sig in self._m49_feedback_seen:
                    continue
                self._m49_feedback_seen.add(_sig)
                if str(_rec.get("source_organ", "")) != "双腿":
                    continue
                _tr = str(_rec.get("trigger_reason", ""))
                if "active_learn" not in _tr:
                    continue
                _new += 1
                self._m49_digest_rejected += 1
                self._log(LogLevel.INFO,
                          f"[双腿学习结果反馈] 主题已抽取但被质量门槛拦截"
                          f"（记为**学习失败**）: {_tr[:70]} 原因={_rec.get('reason', '')}")
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"[M49-T2] 入库反馈读取异常（已忽略）: {type(_e).__name__}")
        return _new

    def set_stomach(self, stomach) -> None:
        """★主线第49批 T2（P2-328）C：注入胃只读引用，供入库结果反馈。"""
        self._m49_stomach_ref = stomach

    def _add_learn_task(self, direction: str, priority: str = "normal"):
        """向多路学习队列添加任务（智能去重 + 容量限制）"""
        # ★主线第49批 T2（P2-328）B：相关性过滤（拒绝用户对话碎片作主题）
        if self._m49_topic_filter_on() and not self._m49_is_relevant_topic(direction):
            self._m49_skipped_irrelevant += 1
            self._log(LogLevel.DEBUG,
                      f"[双腿学习治理] 跳过无关主题（疑似对话污染）: {str(direction)[:50]}")
            return
        # ★A：主题去重（相似度 > 阈值 → 跳过）
        _dup = self._m49_dedup_hit(direction)
        if _dup is not None:
            self._m49_skipped_dup += 1
            self._log(LogLevel.DEBUG,
                      f"[双腿学习治理] 跳过重复主题: {str(direction)[:40]} "
                      f"≈ 已学习 {str(_dup)[:40]}")
            return
        with self._learn_queue_lock:
            # 去重：如果同方向已在学习或已在队列，跳过
            if direction in self._learn_active_directions:
                return
            for task in self._learn_queue:
                if task["direction"] == direction:
                    return

            # ★v25.0治理：队列上限20条，超出时丢弃最旧的normal任务
            if len(self._learn_queue) >= 20:
                # 尝试移除最旧的normal优先级任务
                for _idx, _task in enumerate(self._learn_queue):
                    if _task.get("priority") != "high":
                        self._learn_queue.pop(_idx)
                        self._log(LogLevel.DEBUG,
                                 f"学习队列已满(20条)，丢弃最旧任务: {_task['direction'][:40]}")
                        break
                else:
                    # 全是high优先级，丢弃队尾
                    _dropped = self._learn_queue.pop()
                    self._log(LogLevel.DEBUG,
                             f"学习队列已满(20条)，丢弃队尾任务: {_dropped['direction'][:40]}")

            # 按优先级插入：high插入队首，normal插入队尾
            task = {"direction": direction, "priority": priority, "added_at": time.time()}
            if priority == "high":
                self._learn_queue.insert(0, task)
            else:
                self._learn_queue.append(task)
        # ★主线第49批 T2（P2-328）A：入队成功后记入最近主题（供去重）
        try:
            self._m49_recent_topics.append(direction)
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"[M49-T2] 主题记录失败（已忽略）: {type(_e).__name__}")
    def _learn_worker_loop(self):
        """多路学习工作循环：从队列取任务 → 线程池并行执行"""
        while self._learn_worker_running and self.is_running:
            try:
                # 从队列取任务
                with self._learn_queue_lock:
                    if not self._learn_queue:
                        time.sleep(0.5)
                        continue
                    task = self._learn_queue.pop(0)
                    direction = task["direction"]
                    # 标记为正在学习
                    self._learn_active_directions.add(direction)

                # 检查系统负载
                if self._is_system_busy() or self._is_load_critical():
                    with self._learn_queue_lock:
                        self._learn_active_directions.discard(direction)
                    time.sleep(2)
                    continue

                # 提交到线程池执行
                if self._learn_pool:
                    self._learn_pool.submit(self._learn_task_runner, direction, task.get("priority", "normal"))

            except Exception as e:
                self._log(LogLevel.DEBUG, f"学习工作循环异常: {e}")
                time.sleep(1)
    def _load_legs_config(self):
        """★第161批段B B1：从 LEGS_CONFIG 加载安全词表，失败时保留内联兜底值。"""
        try:
            import config as _cfg
            _legs_cfg = getattr(_cfg, "LEGS_CONFIG", {}) or {}
            _kw = _legs_cfg.get("unsafe_keywords")
            if _kw:
                self._unsafe_keywords = list(_kw)
        except Exception as _cfg_err:
            # 兜底：保留 __init__ 中的内联词表（零行为变更）
            self._log(LogLevel.DEBUG,
                      f"LEGS_CONFIG 加载失败，沿用内联安全词表: {_cfg_err}")

    def _load_builtin_knowledge(self):
        """从本地文件加载内置知识库，如果文件不存在则创建默认知识库"""
        import json as _json
        self._builtin_knowledge_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'data', 'learning', 'builtin_knowledge.json'
        )

        try:
            if os.path.exists(self._builtin_knowledge_path):
                with open(self._builtin_knowledge_path, encoding='utf-8') as f:
                    data = _json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        self._log(LogLevel.INFO, f"从本地加载内置知识库: {len(data)} 条")
                        return data
        except Exception as e:
            self._log(LogLevel.WARNING, f"加载内置知识库失败: {e}")

        # 文件不存在或加载失败，使用默认知识库并写入文件
        default_knowledge = [
            {"topic": "批判性思维", "content": "批判性思维的核心是区分事实与观点，检验推理的合理性。第一，明确问题是什么；第二，收集可靠证据；第三，评估各种解释的优劣；第四，得出经得起检验的结论。", "keywords": ["批判性思维", "逻辑", "推理"]},
            {"topic": "第一性原理", "content": "第一性原理要求从最基本的事实出发重新推导结论，而不是依赖类比。物理学中的基本定律是第一性原理的典型例子。在解决问题时，问自己'我们已知的最基本事实是什么'，然后从中构建方案。", "keywords": ["第一性原理", "思维模型", "创新"]},
            {"topic": "系统思维", "content": "系统思维强调看到事物之间的关联而非孤立的部分。一个系统的行为取决于其结构、反馈循环和延迟效应。在处理复杂问题时，绘制因果关系图可以帮助理解全局。", "keywords": ["系统思维", "复杂性", "反馈"]},
            {"topic": "文明的起源", "content": "人类文明始于约6000年前的美索不达米亚平原，文字的发明使得知识可以代代相传。农业革命使得人类从狩猎采集转向定居生活，城市的出现催生了社会分工和管理制度。", "keywords": ["文明", "历史", "文字", "农业"]},
            {"topic": "丝绸之路", "content": "丝绸之路不仅是贸易路线，更是东西方文化交流的桥梁。通过这条路线，中国的丝绸、造纸术传到西方，印度的数学、阿拉伯的天文学传入中国。文化交流促进了人类文明的共同进步。", "keywords": ["丝绸之路", "文化交流", "东西方"]},
            {"topic": "中国的四大发明", "content": "造纸术、指南针、火药、印刷术是中国古代对世界文明的重大贡献。造纸和印刷推动了知识的传播，指南针使得远洋航行成为可能，火药改变了战争形态。", "keywords": ["四大发明", "中国", "科技史"]},
            {"topic": "非暴力沟通", "content": "非暴力沟通由马歇尔·卢森堡提出，包含四个步骤：观察而不评价、表达感受、说明需要、提出请求。这种方法帮助人们在冲突中保持连接，理解彼此的真实需求。", "keywords": ["非暴力沟通", "交流", "共情"]},
            {"topic": "积极倾听", "content": "积极倾听是一种全身心投入的交流方式。不仅听对方说的内容，还要关注语气、情绪和未表达的潜台词。通过复述、提问和反馈，让对方感受到被理解和尊重。", "keywords": ["倾听", "交流技巧", "理解"]},
            {"topic": "对话的艺术", "content": "真正有意义的对话不仅是交换信息，更是共同思考和创造的过程。在对话中保持好奇心、尊重不同观点、寻找共识而非争辩对错，能让交流成为成长的契机。", "keywords": ["对话", "交流艺术", "共情"]},
        ]

        try:
            os.makedirs(os.path.dirname(self._builtin_knowledge_path), exist_ok=True)
            safe_write_json(self._builtin_knowledge_path, default_knowledge, indent=2)
            self._log(LogLevel.INFO, f"已创建默认内置知识库: {len(default_knowledge)} 条 → {self._builtin_knowledge_path}")
        except Exception as e:
            self._log(LogLevel.WARNING, f"创建内置知识库文件失败: {e}")

        return default_knowledge
    def _learn_task_runner(self, direction: str, priority: str):
        """学习任务执行器（在独立线程中运行）"""
        # 检查学习方向是否与进步相关
        if not self._is_progressive_direction(direction):
            with self._learn_queue_lock:
                self._learn_active_directions.discard(direction)
            return
        # 新增：执行前再检查一次负载，critical模式下直接跳过
        if self._is_load_critical():
            with self._learn_queue_lock:
                self._learn_active_directions.discard(direction)
            return

        try:
            # ★任务5（2026-09-08）：主动学习**优先用 RSS 采集器**（灰度 ENABLE_RSS_COLLECTOR
            #   默认 False；关闭/无命中时走原搜索路径，零回退）。命中即走正常消化链路。
            if self._rss_collect_for_direction(direction, priority):
                return

            # 去重检查：先执行定向搜索
            _search_result = self._search_web(direction)
            _source_url = ""
            content = _search_result[0] if _search_result else None
            if _search_result:
                _source_url = _search_result[1]
            if content and self._is_safe_content(content):
                cleaned = clean_content_text(content)
                if not cleaned:
                    cleaned = content[:200]
                # ★任务C-4：从**原始抓取内容**提取网页发布时间（清洗后时间标记常被剥掉）
                _web_ts = self._extract_web_time(content, _source_url)
                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": f"[主动学习·{direction}] {cleaned[:500]}",
                    "source_organ": "双腿",
                    "trigger_reason": f"active_learn:search:{direction}",
                    "importance": "A" if priority == "high" else "B",
                    "view_mode": "OUTER_VIEW",
                    "source_url": _source_url,   # ★R1
                    **({"source_timestamp": _web_ts} if _web_ts else {}),
                }, priority=3, layer="L2")
                self._write_learn_log(direction, "定向搜索", cleaned[:100])
                self._success_count += 1
                self._consecutive_failures = 0
                self._search_failures = 0
                # ★主线第49批 T2（P2-328）C：原文案「主动学习成功」是**虚假成功**：
                #   此处只能证明「已提交消化」，胃的质量门槛可能随后拦截。
                #   真实结果由 `_m49_sync_digest_feedback()` 异步还原。
                self._log(LogLevel.INFO,
                          f"主动学习已提交消化: {direction} (优先级={priority})"
                          f" — 待胃反馈")
                self._m49_sync_digest_feedback()
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._learn_count += 1
                # ★v9.5跨环境工具泛化：记录搜索成功经验
                self._record_strategy(direction, "search", True)
            else:
                # ★v9.5跨环境工具泛化：记录搜索失败经验
                self._record_strategy(direction, "search", False)
                _builtin_hit = False
                # 搜索失败，尝试通过控制器打开浏览器搜索（深度搜索）
                if self.info_field and self.pulse_core and self._is_progressive_direction(direction):
                    search_query = direction
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=ControllerEvent.OPEN_URL,
                        payload={
                            "url": f"https://lite.duckduckgo.com/lite/?q={search_query}",
                            "reason": f"主动学习搜索: {search_query}",
                            "search_topic": search_query,
                            "deep_search": True,
                        },
                        priority=3,
                        layer="L3"
                    ))
                    self._log(LogLevel.INFO, f"已请求控制器深度搜索: {search_query}")

                # 尝试内置知识兜底
                for item in self._builtin_knowledge:
                    if any(kw in direction for kw in item.get("keywords", [])):
                        self._emit(DigestEvent.KNOWLEDGE, {
                            "content": f"[主动学习·内置·{item['topic']}] {item['content']}",
                            "source_organ": "双腿",
                            "trigger_reason": "active_learn:builtin",
                            "keywords": item.get("keywords", []),
                            "importance": "B",
                            "view_mode": "OUTER_VIEW",
                        }, priority=3, layer="L2")
                        self._write_learn_log(item["topic"], "内置知识", item["content"][:100])
                        self._learn_count += 1
                        _builtin_hit = True
                        break
                # ★v9.5跨环境工具泛化：记录内置兜底成败经验
                self._record_strategy(direction, "builtin", _builtin_hit)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"学习任务执行异常: {e}")
            # ★v9.5跨环境工具泛化：异常视为策略失败经验
            self._record_strategy(direction, "search", False)
        finally:
            # 清除去重标记
            with self._learn_queue_lock:
                self._learn_active_directions.discard(direction)

    def _rss_collect_for_direction(self, direction: str, priority: str) -> bool:
        """★任务5（2026-09-08）：主动学习方向**优先尝试 RSS 采集器**。

        灰度 ENABLE_RSS_COLLECTOR 关闭 / 无命中 / 任何异常 → 返回 False，
        调用方走原有 `_search_web` → 控制器深度搜索路径，行为与开关存在前完全一致。
        命中时文章经 DigestEvent.KNOWLEDGE 走正常消化链路（不旁路），返回 True。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_RSS_COLLECTOR', False):
                return False
            # ★任务7：路由器作为上层决策（ENABLE_KNOWLEDGE_ACQUISITION_ROUTER 默认关；
            #   关闭时 acquire 返回 None → 走下方直连 RSS 逻辑，行为与第三批一致）
            from nucleus.knowledge.KnowledgeAcquisitionRouter import (
                get_shared_knowledge_router,
            )
            _router = get_shared_knowledge_router(
                log_fn=lambda m: self._log(LogLevel.INFO, m))
            _route = _router.acquire(direction, {"intent": "active_learn"})
            if _route is not None:
                _ch = _route.get("channel")
                if _ch == "wiki":
                    # ★第105批 T-105d Fix③：修复 wiki 双腿断点——命中被丢弃，
                    #   改为经 DigestEvent.KNOWLEDGE 走正常消化链路（与 RSS 一致，不旁路）。
                    _wiki = _route.get("payload")
                    if not _wiki:
                        return False
                    if isinstance(_wiki, dict):
                        _w_title = _wiki.get("title") or _wiki.get("keyword") or ""
                        _w_summary = _wiki.get("summary") or ""
                        _w_url = _wiki.get("url") or ""
                    else:
                        _w_title = getattr(_wiki, "title", None) or getattr(_wiki, "keyword", None) or ""
                        _w_summary = getattr(_wiki, "summary", None) or ""
                        _w_url = getattr(_wiki, "url", None) or ""
                    self._emit(DigestEvent.KNOWLEDGE, {
                        "content": f"[主动学习·百科·{direction[:30]}] "
                                   f"[{_w_title}] {_w_summary}",
                        "source_organ": "百科查询器",
                        "trigger_reason": f"active_learn_wiki:{direction[:50]}",
                        "importance": "A" if priority == "high" else "B",
                        "view_mode": "OUTER_VIEW",
                        "source_url": _w_url,
                    }, priority=3, layer="L2")
                    self._log(LogLevel.INFO,
                              f"主动学习百科命中: {direction[:40]}（{_w_title}，免浏览器搜索）")
                    # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                    self._learn_count += 1
                    self._success_count += 1
                    self._consecutive_failures = 0
                    return True
                if _ch != "rss":
                    return False  # 委托浏览器/本地/未命中 → 原搜索路径
                _articles = _route.get("payload") or []
                if not _articles:
                    return False
            else:
                from nucleus.knowledge.RssCollector import get_shared_rss_collector
                _collector = get_shared_rss_collector(
                    log_fn=lambda m: self._log(LogLevel.INFO, m))
                _articles = _collector.collect_for_direction(direction, max_articles=2)
                if not _articles:
                    return False
            for _a in _articles:
                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": f"[主动学习·RSS·{direction[:30]}] "
                               f"[{_a.source}] {_a.to_digest_content()}",
                    "source_organ": "RSS采集器",
                    "trigger_reason": f"active_learn_rss:{direction[:50]}",
                    "importance": "A" if priority == "high" else "B",
                    "view_mode": "OUTER_VIEW",
                    "source_url": _a.url,
                    "source_time": _a.published_at,
                }, priority=3, layer="L2")
            self._log(LogLevel.INFO,
                      f"主动学习RSS命中: {direction[:40]}（{len(_articles)}篇，"
                      f"免浏览器搜索）")
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._learn_count += 1
            self._success_count += 1
            self._consecutive_failures = 0
            return True
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"RSS优先采集异常(走原路径): {_e}")
            return False

    def _extract_web_time(self, raw_content: str, url: str = "") -> float:
        """★任务C-4（2026-09-08）：从抓取内容中提取网页发布时间。

        灰度 ENABLE_WEB_TIME_EXTRACTION 默认 False → 直接返回 0.0（零开销、零变化）。
        开启后：失败（无时间标记/解析异常）返回 0.0，绝不打断学习主流程（容错红线）。

        Returns:
            float: 网页发布时间戳；0.0 表示未提取到。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_WEB_TIME_EXTRACTION', False):
                return 0.0
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseLegs::_extract_web_time L861")
            return 0.0
        try:
            from nucleus.knowledge.WebTimeExtractor import get_shared_extractor
            _ex = get_shared_extractor(
                log_fn=lambda _lvl, _m: self._log(LogLevel.DEBUG, _m))
            _hit = _ex.extract(raw_content or "", url)
            return float(_hit["timestamp"]) if _hit else 0.0
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"网页时间提取异常(不影响学习): {_e}")
            return 0.0

    def _record_strategy(self, direction: str, strategy: str, success: bool):
        """★v9.5跨环境工具泛化：记录工具调用策略经验（低调，失败不打断学习）"""
        try:
            from nucleus.ToolStrategyMemory import get_tool_strategy_memory
            get_tool_strategy_memory().record(direction, strategy, success)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== R4阶段二：存续编排器钩子（智慧层代表） ==========

    def on_survival_low(self, snapshot) -> dict:
        """★R4阶段二：存续低位动作（智慧层代表）。

        第一批：设置状态标志 + 日志。第二批接入停止外部搜索/收缩知识摄入。
        搜索熔断仍由 PulseLegs 自身 _cooldown_until 机制维护，此处不重复实现。
        """
        self._survival_state = "low"
        self._log(LogLevel.INFO,
                  f"[R4智慧层] 存续低位，指数={getattr(snapshot, 'index', '?')}，"
                  f"收缩外部学习（第二批接入搜索暂停）")
        return {"layer": "wisdom", "state": "low"}

    def on_survival_high(self, snapshot) -> dict:
        """★R4阶段二：存续高位动作（智慧层代表）。"""
        self._survival_state = "high"
        self._log(LogLevel.INFO,
                  f"[R4智慧层] 存续高位，指数={getattr(snapshot, 'index', '?')}，开放全网学习")
        return {"layer": "wisdom", "state": "high"}

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "fetch_count": self._fetch_count,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "consecutive_failures": self._consecutive_failures,
            "in_cooldown": time.time() < self._cooldown_until,
            "is_running": self.is_running,
        }

    # ========== 网络抓取 ==========
    def _create_session(self):
        """创建带智能重试的HTTP会话"""
        # [批次4·深度体检][PERF-6] 复用会话（替代每次新建）
        session = requests.Session()

        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=5, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })

        return session
    @budget_guard("search")
    def _fetch_url(self, url: str) -> str | None:
        """使用requests获取网页内容（带重试和编码自动检测，SEC-4: 防 SSRF）"""
        # ★MAINT-1修复: 复用共享 SSRF 防护，避免两份校验逻辑漂移
        # [批次4·深度体检][SSRF防护] 抓取前做 SSRF 校验
        from nucleus.ssrf_guard import is_safe_http_url
        _allowed, _reason = is_safe_http_url(url)
        if not _allowed:
            self._log(LogLevel.WARNING, f"SSRF 防护拦截: {_reason}")
            return None

        # ★主线第49批 T2（P2-328）B2：限制抓取范围 —— 新闻/娱乐站点
        #   实测：抽取观察者网 239273 字符与框架自身完全无关，白耗带宽与质量门槛。
        try:
            import config as _m49_bcfg
            _blk = getattr(_m49_bcfg, "LEARNING_DOMAIN_BLOCKLIST", ()) or ()
            _u = str(url or "").lower()
            for _dom in _blk:
                if _dom and str(_dom).lower() in _u:
                    self._log(LogLevel.DEBUG,
                              f"[双腿学习治理] 域名黑名单跳过: {_dom}")
                    return None
        except Exception as _m49_be:
            self._log(LogLevel.DEBUG,
                      f"[M49-T2] 黑名单检查异常（已忽略）: {type(_m49_be).__name__}")

        try:
            # ★PERF-6修复: 复用同一 Session（连接池），避免每次抓取新建/销毁连接池
            _session = getattr(self, '_session', None)
            if _session is None:
                _session = self._create_session()
                self._session = _session
            response = _session.get(url, timeout=TIMEOUT_CONFIG['http_get'], verify=True)  # ★SEC-4修复: 开启证书校验
            response.raise_for_status()

            # 自动检测编码
            if response.encoding and response.encoding.lower() != 'iso-8859-1':
                encoding = response.encoding
            else:
                # 从内容中尝试检测
                content_preview = response.content[:2000]
                import chardet
                detected = chardet.detect(content_preview)
                encoding = detected.get('encoding', 'utf-8') or 'utf-8'

            response.encoding = encoding
            return response.text

        except requests.exceptions.Timeout:
            self._log(LogLevel.DEBUG, f"请求超时: {url}")
            # ★修复：失败必须计入熔断计数，否则连续网络故障时熔断机制永不触发，
            # 导致网络受限期间 5 模板×2 次重试持续空转（日志中 646 次连接失败）。
            self._handle_failure(url, "timeout")
            return None
        except requests.exceptions.ConnectionError:
            self._log(LogLevel.DEBUG, f"连接失败: {url}")
            self._handle_failure(url, "connection_error")
            return None
        except requests.exceptions.HTTPError as e:
            self._log(LogLevel.DEBUG, f"HTTP错误 {e.response.status_code}: {url}")
            self._handle_failure(url, f"http_{e.response.status_code}")
            return None
        except Exception as e:
            self._log(LogLevel.DEBUG, f"抓取异常: {url} - {e}")
            self._handle_failure(url, "exception")
            return None

    def _extract_search_query(self, direction: str) -> str:
        """
        ★修复：从学习方向中提取简洁的搜索查询词。
        原逻辑把整个 direction（含"跨领域探索:"等前缀、长句）直接塞进 q= 参数，
        导致搜索引擎返回无效结果，甚至触发反爬（ERR_CONNECTION_RESET）。
        此处剥离前缀、提取核心中文词/英文技术词，拼接成 ≤5 词的简洁查询。
        """
        import re
        if not direction:
            return ""
        # 剥离常见前缀（"跨领域探索:"、"自我优化:"、"自我理解:" 等）
        _stripped = re.sub(
            r'^(跨领域探索|自我优化|自我理解|通用领域|主动学习|定向搜索)\s*[:：\-]\s*',
            '', direction
        ).strip()
        if not _stripped:
            _stripped = direction.strip()
        # 提取英文/数字技术词（如 AI、Python）
        _en_words = re.findall(r'[A-Za-z][A-Za-z0-9+#.]{1,15}', _stripped)
        # 中文词：优先 jieba 精准分词（requirements 已声明），失败退回正则
        _zh_words = []
        try:
            import jieba
            _zh_words = [
                w for w in jieba.lcut(_stripped)
                if len(w) >= 2 and any('\u4e00' <= c <= '\u9fff' for c in w)
            ]
        except Exception:
            _zh_words = re.findall(r'[\u4e00-\u9fff]{2,6}', _stripped)
        # 过滤虚词边界碎片（排除"的""和""这"等残词）
        _edge_stop = "的了是一种这那在和与就都各及而之于也乎以内中外间上下前后一两几"
        _zh_words = [
            w for w in _zh_words
            if w[-1] not in _edge_stop and w[0] not in _edge_stop
        ]
        # 合并：优先中文词，英文技术词补充；去重保序，最多 5 个
        _seen = set()
        _words = []
        for w in _zh_words + _en_words:
            if w not in _seen:
                _seen.add(w)
                _words.append(w)
                if len(_words) >= 5:
                    break
        if _words:
            return ' '.join(_words)
        # 兜底：实在提取不到，退回原始 direction（截断到 30 字符）
        return _stripped[:30]

    @budget_guard("search")
    def _search_web(self, query: str) -> tuple[str, str] | None:
        """根据兴趣方向动态搜索互联网内容（增强版：每个网址重试一次，最多5个网址）

        ★R1：返回值改为 (文本, 来源URL) 元组，便于来源追溯。
        ★R3：搜索前做意图分类（决定搜索策略），低置信度才走 LLM 兜底。
        """
        import urllib.parse
        # ★修复：先提取简洁查询词，避免整段句子塞进搜索框导致搜索失败
        _clean_query = self._extract_search_query(query)
        encoded_query = urllib.parse.quote(_clean_query or query)

        # ★R3新增：搜索意图分类（本地规则快路径，低置信度才 LLM 兜底）
        try:
            from nucleus.SearchIntentClassifier import get_intent_classifier
            _classifier = get_intent_classifier()
            _intent_result = _classifier.classify(query)
            self._log(LogLevel.DEBUG,
                      f"[R3-Intent] 搜索意图: {_intent_result['intent']} "
                      f"(置信度={_intent_result['confidence']:.2f}, 来源={_intent_result['source']})")
        except Exception:
            _intent_result = None  # 分类失败不影响搜索

        # ★修复：熔断检查——网络冷却期内直接放弃搜索，避免持续空转
        if time.time() < getattr(self, '_cooldown_until', 0.0):
            _remaining = int(getattr(self, '_cooldown_until', 0.0) - time.time())
            self._log(LogLevel.DEBUG, f"搜索熔断冷却中，跳过({_remaining}秒后恢复)")
            return None

        tried_templates = 0
        max_templates = 5

        for template in self._search_templates:
            if tried_templates >= max_templates:
                break

            url = template.replace("{query}", encoded_query)

            # 每个网址最多尝试2次
            for attempt in range(2):
                content = self._fetch_url(url)
                if content and self._is_safe_content(content):
                    return self._extract_text(content), url   # ★R1：带出来源URL
                if attempt == 0:
                    time.sleep(0.5)  # 第一次失败后短暂等待再重试

            tried_templates += 1  # noqa: SIM113

        return None
    def set_node_pool(self, pool):
        self.node_pool = pool

    def set_llm_callback(self, callback):
        """★R3修复：注入 LLM 兜底回调到搜索意图分类器。

        背景: SearchIntentClassifier.set_llm_callback() 定义后全项目零调用，
        导致低置信度搜索请求不走 LLM 语义兜底、evolution_samples 永不积累、
        规则自进化链路（PatchManager 审批链）实际未激活。

        本方法把外部传入的 LLM 调用封装（如 PulseLung._call_remote_api）注入
        分类器单例，让 R3 的「本地规则 + LLM 兜底 + 规则自进化」完整闭环生效。
        """
        try:
            from nucleus.SearchIntentClassifier import get_intent_classifier
            get_intent_classifier().set_llm_callback(callback)
        except Exception as e:
            # 注入失败不影响主搜索链路（分类器仍走本地规则快路径）
            silent_exc(e, where="organs.motor.PulseLegs::set_llm_callback L1119")
    # ========== 熔断与资源检查 ==========

    def _handle_failure(self, source: str, error: str):
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._consecutive_failures += 1
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._fail_count += 1

        if self._consecutive_failures >= self._max_failures:
            self._cooldown_until = time.time() + self._cooldown_seconds
            self._log(LogLevel.WARNING, f"连续{self._consecutive_failures}次失败，冷却{self._cooldown_seconds}秒")
            # ★修复：熔断触发后重置计数，冷却期结束后以「干净」状态重新计数，
            # 避免带着历史失败包袱导致一次失败就立即再次熔断。
            self._consecutive_failures = 0
    # ========== 主动学习定时器 ==========

    def _schedule_learn_timer(self):
        """安排主动学习定时器"""
        if not self._learn_running:
            return
        if self._learn_timer:
            self._learn_timer.cancel()

        # 动态调整学习间隔
        now = time.time()
        if self._curiosity_active and (now - self._last_curiosity_time) < 120:
            # 好奇心活跃时，放慢主动学习
            interval = self._learn_slow_interval
        else:
            interval = self._learn_interval
            self._curiosity_active = False

        # ★v25.1吞吐量优化: 知识质量反馈调速
        # 去重率>50%表示知识趋于饱和，减慢学习；去重率<15%表示新知识丰富，加快学习
        try:
            if self.node_pool and hasattr(self.node_pool, 'get_content_dedup_rate'):
                _dedup_rate = self.node_pool.get_content_dedup_rate()
                if _dedup_rate > 0.5:
                    interval = min(interval * 1.5, self._learn_slow_interval)
                elif _dedup_rate < 0.15 and getattr(self.node_pool, '_content_add_total', 0) > 50:
                    interval = max(interval * 0.7, self._learn_fast_interval)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._learn_timer = threading.Timer(interval, self._trigger_active_learn)
        self._learn_timer.daemon = True
        self._learn_timer.start()
    def _on_hardware_snapshot(self, payload: dict) -> dict[str, Any]:
        cpu_info = payload.get("cpu", {})
        mem_info = payload.get("memory", {})
        self._cpu_usage = cpu_info.get("usage_percent", 0)
        self._mem_usage = mem_info.get("usage_percent", 0)
        return {"status": "cached"}
    def _trigger_active_learn(self):
        """主动学习定时器回调：向队列添加常规学习任务"""
        if not self._learn_running or not self.is_running:
            return

        if self._is_system_busy() or self._is_load_critical():
            self._schedule_learn_timer()
            return

        # 向队列添加当前兴趣方向
        direction = random.choice(self._interest_directions[:3]) if self._interest_directions else "通用知识"
        self._add_learn_task(direction, "normal")
        self._schedule_learn_timer()

    def _active_learn(self):
        """执行主动学习：四种策略（概率分流：搜索50% | 热点30% | 探索10% | 内置10%）。
        ★v9.5跨环境工具泛化（登顶路线图 山2-P2）：策略选择从「固定概率」升级为
        「经验驱动 + 随机兜底」——优先复用同类任务历史成功率最高的策略模板，
        无经验或经验策略失败时回退到原随机分流。只做记忆不改策略执行，零冲突。
        """
        self._learn_count += 1

        direction = random.choice(self._interest_directions[:3]) if self._interest_directions else "通用知识"

        # ★v9.5：经验模板建议（无经验返回 None，沿用随机分流）
        _tsm = None
        _best_strategy = None
        try:
            from nucleus.ToolStrategyMemory import get_tool_strategy_memory
            _tsm = get_tool_strategy_memory()
            _best_strategy = _tsm.suggest(direction)
        except Exception:
            _tsm = None
            _best_strategy = None

        if _best_strategy is not None:
            # 经验驱动：优先尝试历史成功率最高的策略
            _ok = self._run_learn_strategy(_best_strategy, direction)
            if _tsm is not None:
                _tsm.record(direction, _best_strategy, _ok)
            if _ok:
                return
            # 经验策略失败：记录后回退到随机分流（平滑降级）

        r = random.random()
        _chosen = None
        _ok = False

        if r < 0.5:
            _chosen = "search"
            _ok = self._strategy_search(direction)
        elif r < 0.8:
            _chosen = "trending"
            _ok = self._strategy_trending(direction)
        elif r < 0.9:
            _chosen = "explore"
            _ok = self._strategy_explore(direction)
        else:
            _chosen = "builtin"
            _ok = bool(self._strategy_builtin(direction))

        if _tsm is not None and _chosen:
            _tsm.record(direction, _chosen, _ok)

    def _run_learn_strategy(self, strategy: str, direction: str) -> bool:
        """★v9.5：按策略名执行对应策略方法（供经验模板复用）。"""
        if strategy == "search":
            return self._strategy_search(direction)
        if strategy == "trending":
            return self._strategy_trending(direction)
        if strategy == "explore":
            return self._strategy_explore(direction)
        if strategy == "builtin":
            return bool(self._strategy_builtin(direction))
        return False

    def _strategy_search(self, direction: str) -> bool:
        """策略1：定向搜索"""
        if not direction or not direction.strip():
            return False
        _search_result = self._search_web(direction)
        _source_url = ""
        content = _search_result[0] if _search_result else None
        if _search_result:
            _source_url = _search_result[1]
        if content:
            self._success_count += 1
            self._consecutive_failures = 0
            self._search_failures = 0    # 搜索成功，重置搜索失败计数
            cleaned = clean_content_text(content)
            if not cleaned:
                cleaned = content[:200]
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": f"[主动学习·搜索·{direction}] {cleaned[:500]}",
                "source_organ": "双腿",
                "trigger_reason": f"active_learn:search:{direction}",
                "importance": "A",
                "view_mode": "OUTER_VIEW",
                "source_url": _source_url,   # ★R1
            }, priority=3, layer="L2")
            # ★R3-c新增：搜索成功洞察写入 InsightBoard，供后续层次消费
            try:
                from nucleus.InsightBoard import get_insight_board
                get_insight_board().post(
                    insight_type="innovation_insight",
                    content=f"搜索获得新知识: {direction}（来源:{_source_url or '未知'}）",
                    source_loop="搜索反馈闭环",
                    related_dimension=direction[:30],
                    confidence=0.6,
                    keywords=["搜索", direction[:20], "新知识"]
                )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._write_learn_log(direction, "定向搜索", content[:100])
            self._log(LogLevel.INFO, f"主动学习: 定向搜索成功 ({direction})")
            return True
        self._search_failures += 1
        if self._search_failures >= 10:
            self._log(LogLevel.DEBUG, f"搜索连续失败{self._search_failures}次，暂停搜索策略")
        return False

    def _strategy_trending(self, direction: str) -> bool:
        """策略2：榜单热点"""
        source = random.choice(self._trending_sources)
        content = self._fetch_url(source)
        if content and self._is_safe_content(content):
            clean_text = self._extract_text(content)
            self._success_count += 1
            self._consecutive_failures = 0
            self._search_failures = 0    # 搜索成功，重置搜索失败计数
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": f"[主动学习·热点·{direction}] {clean_text[:500]}",
                "source_organ": "双腿",
                "trigger_reason": f"active_learn:trending:{direction}",
                "importance": "B",
                "view_mode": "OUTER_VIEW",
                "source_url": source,   # ★R1
            }, priority=3, layer="L2")
            self._write_learn_log(direction, "榜单热点", clean_text[:100])
            self._log(LogLevel.INFO, f"主动学习: 榜单热点成功 ({direction})")
            return True
        return False

    def _strategy_explore(self, direction: str) -> bool:
        """策略3：随机探索——基于节点池中的知识进行联想学习"""
        if not self.node_pool:
            return False

        l2_nodes = self.node_pool.query(evol_level="L2", limit=30)
        l1_nodes = self.node_pool.query(evol_level="L1", limit=30)
        all_nodes = l2_nodes + l1_nodes

        if len(all_nodes) < 2:
            return False

        sampled = random.sample(all_nodes, min(3, len(all_nodes)))
        fragments = []
        keywords = []
        for node in sampled:
            value = node.value if isinstance(node.value, str) else str(node.value)
            fragments.append(value[:120])
            if hasattr(node, 'keywords') and node.keywords:
                keywords.extend(node.keywords[:3])

        keywords = list(set(keywords))[:5]
        learn_content = f"[主动学习·探索·{direction}] 通过回顾已有知识（关键词：{'、'.join(keywords[:3])}），进行了联想推演：{fragments[0][:80]}"

        if self._is_safe_content(learn_content):
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": learn_content,
                "source_organ": "双腿",
                "trigger_reason": "active_learn:explore",
                "importance": "C",
                "view_mode": "OUTER_VIEW",
            }, priority=3, layer="L2")
            self._write_learn_log(direction, "随机探索", learn_content[:100])
            self._log(LogLevel.INFO, f"主动学习: 随机探索 ({direction})")
            return True
        return False

    def _strategy_builtin(self, direction: str):
        """策略4：内置知识兜底——网络不可用时从内置知识库学习"""
        builtin_match = None
        for item in self._builtin_knowledge:
            if any(kw in direction for kw in item.get("keywords", [])):
                builtin_match = item
                break

        if not builtin_match:
            builtin_match = random.choice(self._builtin_knowledge)

        if self._is_safe_content(builtin_match["content"]):
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": f"[主动学习·内置·{builtin_match['topic']}] {builtin_match['content']}",
                "source_organ": "双腿",
                "trigger_reason": "active_learn:builtin",
                "keywords": builtin_match.get("keywords", []),
                "importance": "B",
                "view_mode": "OUTER_VIEW",
            }, priority=3, layer="L2")
            self._write_learn_log(builtin_match["topic"], "内置知识", builtin_match["content"][:100])
            self._log(LogLevel.INFO, f"主动学习: 内置知识 ({builtin_match['topic']})")

    def _write_learn_log(self, direction: str, source: str, content_preview: str):
        """写入双腿学习日志文件"""
        try:
            import json as _json
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                     'data', 'stream', 'legs_learn_log.json')
            entry = {
                "timestamp": time.time(),
                "direction": direction,
                "source": source,
                "content_preview": content_preview[:100],
            }
            log_data = []
            if os.path.exists(log_path):
                try:
                    # ★主线第74批 T1：默认 [] 而非 {}——日志文件内容为 dict 时
                    #   safe_read_json 返回 dict，后续 .append 抛 AttributeError 被静默吞
                    #   （日志中 "[双腿] 数据处理异常已忽略"）。改为 [] 从源头消除。
                    log_data = safe_read_json(log_path, default=[])
                except (ValueError, OSError) as e:
                    self._log(LogLevel.INFO, f"[WARNING] PulseLegs 双腿学习日志读取失败: {type(e).__name__}: {e}")
                    log_data = []
                # ★主线第74批 T1：防御性类型守卫——非 list 时记 WARNING 而非抛异常，
                #   dict 转 values 保留历史数据点（避免静默丢数据），其他类型重置为空。
                if not isinstance(log_data, list):
                    if isinstance(log_data, dict):
                        self._log(LogLevel.DEBUG,
                                  f"[第74批T1] 双腿学习日志为dict格式，已转为list保留: {log_path}")
                        log_data = list(log_data.values())
                    else:
                        self._log(LogLevel.WARNING,
                                  f"[第74批T1] 双腿学习日志格式异常(非list/dict)，重置为空: {type(log_data).__name__}")
                        log_data = []
            log_data.append(entry)
            if len(log_data) > 50:
                log_data = log_data[-50:]
            with open(log_path, 'w', encoding='utf-8') as f:
                _json.dump(log_data, f, ensure_ascii=False)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")

    def _is_safe_content(self, content: str) -> bool:
        """检查内容是否安全（第一道过滤）"""
        content_lower = content.lower()
        for kw in self._unsafe_keywords:
            if kw in content_lower:
                self._log(LogLevel.DEBUG, f"安全过滤拦截: 包含关键词 '{kw}'")
                return False
        return True

    def _extract_text(self, content: str) -> str:
        """从HTML中提取有意义的纯文本"""
        # 去除script和style标签
        text = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # 去除所有HTML标签
        text = re.sub(r'<[^>]+>', ' ', text)
        # 去除HTML实体
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
        text = re.sub(r'&#\d+;', ' ', text)
        # 去除多余空白
        text = re.sub(r'\s+', ' ', text)
        # 去除URL残留
        text = re.sub(r'https?://\S+', '', text)

        return text.strip()[:1000]

    def _is_system_busy(self) -> bool:
        """检查系统是否繁忙（原有逻辑 + 信息场负载感知）"""
        # 原有硬件阈值检查
        if self._cpu_usage > 80 or self._mem_usage > 85:
            return True
        # 新增：信息场负载等级检查
        return bool(self._is_load_critical())
    def _is_progressive_direction(self, direction: str) -> bool:
        """判断学习方向是否与进步相关（迭代、进化、交流、代码、思维等）"""
        progressive_keywords = [
            "编程", "代码", "架构", "算法", "数据", "系统",
            "思维", "认知", "学习", "逻辑", "推理", "创新",
            "交流", "沟通", "共情", "倾听", "对话",
            "进化", "迭代", "成长", "优化", "改进",
            "数学", "物理", "科学", "技术", "工程",
            "批判", "第一性", "元认知", "刻意练习",
            "Python", "Java", "AI", "人工智能", "机器学习",
        ]
        direction_lower = direction.lower()
        return any(kw.lower() in direction_lower for kw in progressive_keywords)
    def _is_load_critical(self) -> bool:
        """检查信息场是否处于临界负载"""
        try:
            if self.info_field and hasattr(self.info_field, 'get_load_level'):
                return self.info_field.get_load_level() == "critical"
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        return False

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    LegsEvent.FETCH,
                    InterestEvent.CHANGED,
                    SubconsciousEvent.CURIOSITY_TICK,
                    TouchEvent.HARDWARE_SNAPSHOT,
                    DeviceEvent.CAPABILITY_UPDATE,
                    Event.LEGS_LEARN_NOW,
                    SystemEvent.STATUS_REQUEST,
                    DigestEvent.KNOWLEDGE,
                    Event.LEGS_LEARN_NOW,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "双腿",
    "class_name": "PulseLegs",
    "attr_name": "legs",
    "system": "motor",
    "always_online": False,
    "feature_flag": "enable_motor",
    "extra_deps": {},
    "post_wiring": [
        {"target": "node_pool", "setter": "set_node_pool"},
    ],
}

if __name__ == "__main__":
    print("=== PulseLegs v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)
        def get_current(self, key):
            return {
                "payload": {
                    "cpu": {"usage_percent": 30.0},
                    "memory": {"usage_percent": 50.0},
                }
            }
        def get_load_level(self):
            return "light"

    mock_field = MockInfoField()

    legs = PulseLegs("双腿")
    legs.set_info_field(mock_field)
    legs.start()

    result1 = legs.on_pulse({
        "event_type": LegsEvent.FETCH,
        "payload": {},
        "priority": 4,
    })
    print(f"1. 抓取: {result1['status']}")

    # 验证知识消化脉冲的 layer 标记
    digest_pulses = [p for p in mock_field.published if p.get("event_type") == DigestEvent.KNOWLEDGE]
    if digest_pulses:
        print(f"   KNOWLEDGE脉冲 layer: {digest_pulses[-1].get('layer', '未设置')} (预期L2)")

    legs._consecutive_failures = 3
    legs._cooldown_until = time.time() + 1800
    result2 = legs.on_pulse({
        "event_type": LegsEvent.FETCH,
        "payload": {},
        "priority": 4,
    })
    print(f"2. 熔断: {result2['status']} (剩余{result2.get('remaining_seconds', 0)}秒)")

    legs._consecutive_failures = 0
    legs._cooldown_until = 0

    status = legs.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"3. 统计: 抓取{status['fetch_count']}次 成功{status['success_count']}次")

    legs.stop()
    print("\n=== 自测全部通过 ===")
# _m49_t2_init_done
# _m49_t2_helpers_done
# _m49_t2_record_done
# _m49_t2_log_done
# _m49_t2_blocklist_done
# _m49_t2_import_done
