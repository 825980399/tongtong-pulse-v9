# -*- coding: utf-8 -*-
"""
search_scheduler.py —— 搜索调度器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 知识搜索任务调度与结果合并
机制: 基于SearchScheduler类实现，包含10个核心方法
定位: 搜索管理层

⚠️ @deprecated (157-D C-9 只卸不装 / 第七型断链):
    本模块整文件不可达——全仓无 `import nucleus.search_scheduler`、无 `SearchScheduler(` 实例化，
    仅被 main.py 停机注册表以字符串引用。当前仅存在"卸载路径"（shutdown_search_scheduler），
    无"装载路径"（无工厂函数、无装配接线）。
    决策（Q157 定案）：③ 弃用标注封存，不补装载路径；若需复活，须待 B156-10 打通联网前置
    （联网检索）后升①补工厂 + 装载点 + InnerWorld 检索入口接线。禁止新代码 import 本模块。
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc


class SearchScheduler:
    """
    深度搜索统一调度器。
    
    所有深度搜索请求统一提交到此调度器，
    由调度器根据优先级、并发数、去重规则决定执行策略。
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        
        # 搜索去重缓存：{search_topic: last_search_time}
        self._search_dedup_cache: OrderedDict = OrderedDict()
        self._max_dedup_cache = 100
        
        # 正在执行的任务数
        self._active_count = 0
        
        # 统计
        self._total_submitted = 0
        self._total_executed = 0
        self._total_dedup_skipped = 0
        self._total_downgraded = 0
        
        # 加载配置
        self._load_config()
    
    def _load_config(self):
        """从config加载调度参数"""
        try:
            import config
            cfg = getattr(config, 'SEARCH_SCHEDULER', {})
            self._max_concurrent = cfg.get("max_concurrent", 2)
            self._dedup_interval = cfg.get("dedup_interval", 1800)  # 30分钟
            self._queue_warning_threshold = cfg.get("queue_warning_threshold", 5)
            self._queue_downgrade_threshold = cfg.get("queue_downgrade_threshold", 10)
        except Exception:
            self._max_concurrent = 2
            self._dedup_interval = 1800
            self._queue_warning_threshold = 5
            self._queue_downgrade_threshold = 10
    
    def try_schedule(self, search_topic: str, priority: str = "normal",
                     executor_func: Callable | None = None,
                     fallback_func: Callable | None = None,
                     **kwargs) -> dict[str, Any]:
        """
        尝试调度一个深度搜索任务。
        
        Args:
            search_topic: 搜索主题（用于去重判断）
            priority: 优先级（urgent/high/normal/low）
            executor_func: 实际的搜索执行函数（使用Playwright）
            fallback_func: 降级时的备选函数（使用requests）
            **kwargs: 传递给执行函数的额外参数
        
        Returns:
            调度结果，包含status和说明

        ⚠️ 本方法当前不可达（157-D C-9 ③封存）：全仓无调用点；复活须待 B156-10 联网前置后
        补工厂 + 装载点 + InnerWorld 检索入口接线（升①）。
        """
        self._total_submitted += 1
        
        # 1. 去重检查
        if self._is_duplicate(search_topic):
            self._total_dedup_skipped += 1
            return {
                "status": "dedup_skipped",
                "reason": f"搜索主题 '{search_topic[:40]}' 在冷却期内，跳过"
            }
        
        # 2. 记录去重时间
        self._mark_searched(search_topic)
        
        # 3. 检查是否需要降级
        if self._should_downgrade(priority):
            self._total_downgraded += 1
            if fallback_func:
                try:
                    result = fallback_func(**kwargs)
                    return {
                        "status": "downgraded",
                        "reason": "队列过长，降级为requests搜索",
                        "result": result
                    }
                except Exception as e:
                    return {
                        "status": "downgraded_failed",
                        "reason": f"降级搜索也失败了: {e}"
                    }
            return {
                "status": "downgraded",
                "reason": "队列过长，且无降级函数可用"
            }
        
        # 5. 执行
        if executor_func is None:
            return {
                "status": "skipped",
                "reason": "无执行函数"
            }

        # 4. 检查并发数 + 占用额度（必须原子，见下）
        # ★7-1/P1-13修复(2026-09-05)：原实现是「先检查上限、再自增」两个分离步骤，
        #   在多线程下构成典型 TOCTOU 竞态——
        #       线程A: 读到 _active_count=4 < 上限5 → 放行
        #       线程B: 读到 _active_count=4 < 上限5 → 放行
        #       线程A: +=1 → 5
        #       线程B: +=1 → 6   ← _max_concurrent 被突破
        #   即并发上限形同虚设。修复：把「检查 + 自增」并入同一把锁成为原子操作。
        #   注意仅给自增单独加锁是**不够**的，必须连判断一起锁。
        #   另：本块已上移到「无执行函数」判空之后，因此不会占用额度后提前返回。
        with self._lock:
            if self._active_count >= self._max_concurrent:
                return {
                    "status": "queued",
                    "reason": f"当前活跃{self._active_count}个任务，已达并发上限{self._max_concurrent}，排队等待"
                }
            self._active_count += 1
            self._total_executed += 1

        try:
            result = executor_func(**kwargs)
            return {
                "status": "executed",
                "result": result
            }
        except Exception as e:
            # 执行失败，尝试降级
            if fallback_func:
                try:
                    result = fallback_func(**kwargs)
                    return {
                        "status": "execution_failed_downgraded",
                        "reason": f"主搜索失败({e})，降级成功",
                        "result": result
                    }
                except Exception as e2:
                    return {
                        "status": "failed",
                        "reason": f"主搜索和降级搜索均失败: {e}, {e2}"
                    }
            return {
                "status": "failed",
                "reason": f"搜索执行失败: {e}"
            }
        finally:
            # ★7-1/P1-13：归还额度同样需在锁内，与上方占用保持同一临界区，
            #   否则「占用(锁内) / 归还(锁外)」配不起来，计数仍会漂移。
            with self._lock:
                self._active_count = max(0, self._active_count - 1)
    
    def _is_duplicate(self, search_topic: str) -> bool:
        """检查搜索主题是否在冷却期内"""
        topic_key = search_topic.strip().lower()[:80]
        with self._lock:
            if topic_key in self._search_dedup_cache:
                last_time = self._search_dedup_cache[topic_key]
                if time.time() - last_time < self._dedup_interval:
                    return True
            return False
    
    def _mark_searched(self, search_topic: str):
        """标记搜索主题已执行"""
        topic_key = search_topic.strip().lower()[:80]
        with self._lock:
            self._search_dedup_cache[topic_key] = time.time()
            # 缓存上限保护
            while len(self._search_dedup_cache) > self._max_dedup_cache:
                self._search_dedup_cache.popitem(last=False)
    
    def _should_downgrade(self, priority: str) -> bool:
        """判断是否需要降级"""
        # urgent 和 high 优先级永不降级
        if priority in ("urgent", "high"):
            return False
        # normal 和 low 在队列过长时降级
        return self._active_count >= self._queue_downgrade_threshold
    
    def get_active_count(self) -> int:
        """获取当前活跃任务数"""
        return self._active_count
    
    def get_stats(self) -> dict[str, Any]:
        """获取调度器统计信息"""
        return {
            "total_submitted": self._total_submitted,
            "total_executed": self._total_executed,
            "total_dedup_skipped": self._total_dedup_skipped,
            "total_downgraded": self._total_downgraded,
            "active_count": self._active_count,
            "max_concurrent": self._max_concurrent,
            "dedup_interval": self._dedup_interval,
            "dedup_cache_size": len(self._search_dedup_cache),
        }


# ========== 模块级单例 ==========
_search_scheduler: SearchScheduler | None = None
_search_scheduler_lock = threading.Lock()



def shutdown_search_scheduler() -> None:
    """★P1: 复位 SearchScheduler 单例，满足器官零状态（规则4）。

    ⚠️ 本函数是本模块当前唯一的活跃路径（经 main.py 停机注册表字符串引用）。
    157-D C-9 ③封存：search_scheduler 整文件不可达，仅"卸载"无"装载"；复活见模块 docstring。"""
    global _search_scheduler
    _inst = _search_scheduler
    _search_scheduler = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.search_scheduler::shutdown_search_scheduler L242")
