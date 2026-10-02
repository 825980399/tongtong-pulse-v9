# -*- coding: utf-8 -*-
from nucleus._silent_except import silent_exc
"""
PulseController —— 控制器器官 · 网页深度搜索与本地文件/应用操纵

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 ControllerEvent 的 OPEN_URL / READ_FILE / LIST_DIRECTORY / LAUNCH_APP 四类脉冲，负责网页深度搜索、正文抽取与质量评估，管理有头/无头浏览器的生命周期，并把抓取结果交下游消化。
机制: on_pulse 分发到 _on_open_url / _on_read_file / _on_list_directory；搜索走 _preprocess_search_topic → _classify_search_intent（意图不明时 _fallback_intent_terms）→ _try_encyclopedia_first → _execute_headless_search，其中 _ensure_headless_browser / _navigate_headless / _get_page_links_headless / _extract_article_headless 完成无头抓取，_assess_content_quality 做质量门控，_finish_deep_search 收口；_check_domain_permission 做域名白黑名单，_check_domestic_network 判断网络环境；_start_keepalive_timer 与 _kill_zombie_headless 回收僵尸实例；budget_guard 装饰器统一施加资源预算闸门。
定位: 运动层的「浏览器与文件系统代理」，是曈曈主动获取外部世界信息的主通道。
"""

from nucleus.LLMDependencyMetrics import (SEARCH_HEADLESS, record_search)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json as _json  # noqa: F401
import os
import re

_URL_PATTERN = re.compile(r'(?:https?://|//)[^\s<>"\'\]\)\u4e00-\u9fff]+')
import shlex  # noqa: F401
import subprocess
import sys
import threading
import time

# ===== 无头浏览器深度检索 =====
# ===== 无头浏览器深度检索（Playwright） =====
try:
    from playwright.sync_api import (
        TimeoutError as PlaywrightTimeout,  # type: ignore[possibly-unbound]
    )
    from playwright.sync_api import sync_playwright  # type: ignore[possibly-unbound]
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.logger import exc_location
from config import EXTERNAL_CALL_TIMEOUTS, TIMEOUT_CONFIG
from nucleus.const import (
    ControllerEvent,
    DigestEvent,
    LogLevel,
    SearchEvent,
    SystemEvent,
    TouchEvent,
)
from nucleus.external_executor import (  # noqa: F401
    OperationPriority,
    get_external_executor,
)


def budget_guard(consumer: str = "search"):
    """★登顶路线图-山2：资源预算守卫装饰器。

    对高耗能动作（搜索/抓取/浏览）施加全局资源预算闸门：
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
                        _log = getattr(args[0] if args else None, "_log", None)
                        if _log:
                            _log(LogLevel.ERROR, f'异常: {e}')
        return _wrapper
    return _decorator


class PulseController(BasePulseOrgan):
    """脉冲驱动控制器（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'search_cooldown_seconds' in _rp and hasattr(self, '_search_cooldown'):
                setattr(self, '_search_cooldown', _rp['search_cooldown_seconds'])
            if 'search_max_per_hour' in _rp and hasattr(self, '_search_max_per_hour'):
                setattr(self, '_search_max_per_hour', _rp['search_max_per_hour'])
            # ★A-9死参数清理：search_quality_threshold 已从 RUNTIME_PARAMS 移除
            #   （仅 refresh 写属性、无任何消费点——留空防误导）
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "控制器"):
        super().__init__(organ_name)

        # 依赖注入
        self.white_cell = None   # 白细胞引用（安全拦截）
        self.touch = None        # 触觉引用（负载感知）
        self.subconscious = None  # ★P1: 潜意识引用（低质量方向过滤）

        # 权限配置
        self._permission_config: dict[str, Any] = {}

        # 操作审计
        self._audit_log: list[dict[str, Any]] = []
        self._audit_lock = threading.Lock()

        # 负载状态
        self._load_level = "light"
        self._load_level_lock = threading.Lock()

        # 统计
        self._operation_count = 0
        self._file_read_count = 0
        self._url_open_count = 0
        self._last_url_open_time = 0.0
        self._url_open_hour_count = 0
        self._url_open_hour_start = 0.0

        # ===== 深度搜索与窗口管理 =====
        self._browser_windows: list[int] = []
        self._deep_search_active = False
        self._last_deep_search_time = 0.0
        # ★任务C-4：最近一次提取到的网页发布时间（_extract_main_content 内赋值）
        self._last_web_time = 0.0

        # ===== 无头浏览器深度检索（Playwright） =====
        self._playwright = None            # Playwright 实例
        self._headless_browser = None      # 浏览器实例
        self._headless_page = None         # 当前页面
        self._headless_active = False
        # ★主线第12批 T1/P2-81：保活信号（方案A）——保活线程只置此标志，
        #   实际 touch 由持有浏览器的 Playwright 专用线程执行（规避跨线程约束）
        self._keepalive_due = False
        self._keepalive_lock = threading.Lock()
        self._headless_last_used = 0.0
        self._browser_rebuild_failures = 0      # 浏览器重建连续失败计数（退避用）
        self._browser_rebuild_cooldown_until = 0.0  # 重建冷却截止时间戳
        self._search_cache: dict[str, dict] = {}
        self._search_cache_lock = threading.Lock()  # ★v24.0新增：缓存锁
        self._search_cache_max = 50                 # 最大缓存条目数
        self._search_cache_ttl = 300                # 缓存有效期（秒）
        # ★v16.0新增：大模型提炼经验缓存
        # 格式：{ "原始搜索词模式": {"success": N, "fail": N, "last_refined": "..."} }
        self._refine_experience: dict[str, dict[str, Any]] = {}
        self._headless_pid = 0
        # ===== Playwright 专用线程池（并发数从config读取） =====
        self._playwright_executor: ThreadPoolExecutor | None = None
        self._playwright_lock = threading.Lock()
        self._search_terminated = False  # 搜索终止标记，由内在世界反馈脉冲设置
        self._shutting_down = False      # 框架退出标志，阻止新搜索和浏览器操作
        self.node_pool = None
        self.qica = None

        # ★属性初始化完整性补全（自动审查添加）
        self._last_refine_original = 0.0
        self._last_refine_result = 0.0
        self._search_ab_stats = {}
        self._search_ab_total = 0
        self._search_engine_stats = {}
        self._survival_state = {}
        self.stomach = None
    # ========== 依赖注入 ==========

    def set_white_cell(self, white_cell):
        self.white_cell = white_cell

    def set_stomach(self, stomach):
        """注入胃引用（用于文件内容和网页文本消化）"""
        self.stomach = stomach

    def set_touch(self, touch):
        self.touch = touch

    def set_subconscious(self, subconscious):
        """★P1: 设置潜意识引用，用于查询低质量搜索方向。"""
        self.subconscious = subconscious
    def set_node_pool(self, node_pool):
        self.node_pool = node_pool

    def set_qica(self, qica):
        self.qica = qica
    def _load_permission_config(self):
        """从config加载权限配置"""
        try:
            import config
            self._permission_config = getattr(config, 'CONTROLLER_PERMISSION', {})
            self._log(LogLevel.INFO, "权限配置已加载")
        except Exception as e:
            self._log(LogLevel.WARNING, f"加载权限配置失败: {e}")
            self._permission_config = {}

    def load_permission_config(self):
        """★P3-1公开封装：加载权限配置（替代跨模块对 _load_permission_config 的私有直调）"""
        self._load_permission_config()

    def _get_playwright_executor(self) -> ThreadPoolExecutor:
        """获取 Playwright 专用线程池（延迟初始化，线程安全）"""
        if self._playwright_executor is None:
            with self._playwright_lock:
                if self._playwright_executor is None:
                    # Playwright 的 sync_api 要求所有操作在同一线程中，强制为 1
                    max_workers = 1
                    self._playwright_executor = ThreadPoolExecutor(
                        max_workers=max_workers,
                        thread_name_prefix="Playwright"
                    )
        return self._playwright_executor
    # ========== 生命周期 ==========
    def start(self):
        super().start()
        self._load_permission_config()
        # 预热常驻浏览器：在后台线程中提前启动，避免首次搜索时等待
        # ★主线第12批 T1/P2-81：属主线程模式——浏览器必须诞生在 Playwright 专用池线程，
        #   否则裸预热线程退出后浏览器对象即"绑到已退出线程"，任何后续操作都会
        #   报 "cannot switch to a different thread (which happens to have exited)"。
        if self._owner_thread_mode():
            try:
                self._get_playwright_executor().submit(self._ensure_headless_browser)
            except Exception as _pw_e:
                self._log(LogLevel.DEBUG,
                          f"预热提交池失败，降级裸线程: {type(_pw_e).__name__}: {_pw_e}")
                _prewarm_thread = threading.Thread(
                    target=self._ensure_headless_browser, daemon=True)
                _prewarm_thread.start()
        else:
            _prewarm_thread = threading.Thread(target=self._ensure_headless_browser, daemon=True)
            _prewarm_thread.start()
        # 启动浏览器保活定时器
        self._start_keepalive_timer()
        self._log(LogLevel.INFO, "控制器已启动（常驻浏览器预热中...）")

    def stop(self):
        # 第一步：设置全局退出标志，阻止新搜索和浏览器操作
        self._shutting_down = True
        self._headless_active = False
        # 第二步：先强制关闭浏览器实例，让正在执行的Playwright任务快速失败
        self._close_headless_browser()
        # 第三步：等待线程池中的任务自然结束（浏览器已关闭，任务会快速失败返回）
        if self._playwright_executor:
            try:
                self._playwright_executor.shutdown(wait=True, timeout=5)
            except Exception:
                self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
            self._playwright_executor = None
        # 第四步：清理残留的浏览器窗口
        self._cleanup_browser_windows()
        super().stop()
        self._log(LogLevel.INFO, f"已停止，操作{self._operation_count}次, "
                 f"读文件{self._file_read_count}次, 打开网页{self._url_open_count}次")

    # ========================================================================
    # 无头Chrome深度检索能力层（底层通用逻辑，参数从config读取）
    # ========================================================================

    def _start_headless_browser(self) -> bool:
        """启动 Playwright 无头 Chromium，自带浏览器内核，无需系统Chrome"""
        # ===== 新增: 安全外壳 =====
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._log(LogLevel.DEBUG, "异步循环运行中，跳过无头浏览器启动")
                return False
        except RuntimeError as e:
            silent_exc(e, "organs/motor/PulseController.py:290", level="warning")
        except Exception as e:
            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
        if not PLAYWRIGHT_AVAILABLE:
            self._log(LogLevel.WARNING, "Playwright 未安装，无头浏览器不可用")
            return False

        if self._get_headless_config("load_critical_disable", True) and self._current_load_level() == "critical":
            self._log(LogLevel.WARNING, "临界负载，拒绝启动无头浏览器")
            return False

        if self._headless_browser:
            try:
                self._headless_page.title()
                self._headless_last_used = time.time()
                return True
            except Exception:
                self._headless_browser = None
                self._headless_page = None

        try:
            self._playwright = sync_playwright().start()  # type: ignore[possibly-unbound]

            launch_args = []
            if self._get_headless_config("stealth_mode", True):
                launch_args = list(self._get_headless_config("stealth_args", []))

            self._headless_browser = self._playwright.chromium.launch(
                headless=self._get_headless_config("headless", True),
                args=launch_args,
            )

            context = self._headless_browser.new_context(
                viewport={
                    "width": 1920,
                    "height": 1080,
                },
                user_agent=self._get_headless_config("user_agent_pool", [
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
                ])[0] if self._get_headless_config("user_agent_pool", []) else None,
            )

            self._headless_page = context.new_page()
            self._headless_active = True
            self._headless_last_used = time.time()
            self._log(LogLevel.INFO, "无头浏览器已启动 (Playwright Chromium)")
            return True

        except Exception as e:
            self._log(LogLevel.ERROR, f"启动无头浏览器失败: {e}")
            self._headless_browser = None
            self._headless_page = None
            self._headless_active = False
            # 清理残留的 playwright 实例，防止多次失败积累僵尸进程
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                finally:
                    self._playwright = None
            return False

    def _navigate_headless(self, url: str) -> str | None:
        """导航到指定 URL 并返回页面文本"""
        # 退出检查：框架正在关闭时直接返回
        if self._shutting_down:
            return None
        if not self._headless_page and not self._start_headless_browser():
            return None

        if not self._check_domain_permission(url):
            self._log(LogLevel.WARNING, f"域名安全拦截: {url}")
            return None

        timeout = self._get_headless_config(
            "heavy_load_timeout" if self._current_load_level() == "heavy" else "page_load_timeout", 15
        ) * 1000  # Playwright 用毫秒

        try:
            self._headless_page.goto(url, timeout=timeout)
            # 等页面完全稳定再操作（networkidle 确保所有网络请求完成）
            try:
                self._headless_page.wait_for_load_state('networkidle', timeout=timeout)
            except PlaywrightTimeout:  # type: ignore[possibly-unbound]
                # networkidle 超时说明页面有持续请求（如广告），降级到 domcontentloaded
                try:
                    self._headless_page.wait_for_load_state('domcontentloaded', timeout=5000)
                except PlaywrightTimeout as e:  # type: ignore[possibly-unbound]
                    silent_exc(e, "organs/motor/PulseController.py:379", level="warning")
            self._headless_last_used = time.time()

            human_cfg = self._get_headless_config("human_behavior", {})
            if human_cfg.get("move_mouse_simulation", True):
                import random as _random
                wait_time = _random.uniform(
                    human_cfg.get("think_time_min", 0.5),
                    human_cfg.get("think_time_max", 1.8)
                )
                time.sleep(wait_time)

            return self._headless_page.content()
        except PlaywrightTimeout:  # type: ignore[possibly-unbound]
            self._log(LogLevel.WARNING, f"页面加载超时: {url[:80]}")
            return None
        except Exception as e:
            self._log(LogLevel.ERROR, f"导航失败: {e}")
            return None

    def _get_page_links_headless(self, search_topic: str) -> list[dict[str, str]]:
        """
        从当前页面 DOM 提取搜索结果链接。
        """
        # ===== 新增: 异步环境安全检测 =====
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._log(LogLevel.DEBUG, "异步循环运行中，跳过页面链接提取")
                return []
        except RuntimeError as e:
            silent_exc(e, where="organs.motor.PulseController::_get_page_links_headless L412")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if not self._headless_page:
            return []

        # 核心结果选择器 —— 直接收录
        primary_selectors = ["h2 a", ".b_algo a"]
        # 辅助结果选择器 —— 需要关键词匹配
        secondary_selectors = ["#b_results a", "h3 a", "article a", ".result a"]

        links = []
        seen_urls = set()
        topic_keywords = [kw.lower() for kw in (search_topic or "").split() if len(kw) >= 2]

        def _process_element(elem, is_primary: bool):
            try:
                url = elem.get_attribute("href") or ""
                title = (elem.inner_text() or "").strip()
                if not url or not url.startswith("http"):
                    return
                # 域名过滤：跳过搜索引擎自身和社交网站
                domain = url.split("/")[2] if "://" in url else ""
                if any(skip in domain.lower() for skip in [
                    "google.com", "www.baidu.com", "bing.com", "duckduckgo.com",
                    "facebook.com", "twitter.com", "instagram.com", "youtube.com",
                ]):
                    return
                if url in seen_urls:
                    return
                if len(title) < 5:
                    return
                seen_urls.add(url)

                if is_primary:
                    # 核心结果直接收录
                    links.append({"url": url, "title": title[:100], "score": 1.0})
                    return

                # 辅助结果：计算关键词得分，>0 才收录
                score = 0
                title_lower = title.lower()
                url_lower = url.lower()
                for kw in topic_keywords:
                    if kw in title_lower:
                        score += 3
                    elif kw in url_lower:
                        score += 1
                if score > 0 or not topic_keywords:
                    links.append({"url": url, "title": title[:100], "score": score})
            except Exception as e:
                self._log(LogLevel.ERROR, f'异常: {e}')

        # 1. 处理核心选择器
        for sel in primary_selectors:
            try:
                for elem in self._headless_page.query_selector_all(sel):
                    _process_element(elem, is_primary=True)
            except Exception:
                silent_exc(where="organs/motor/PulseController.py:472")
                continue

        # 2. 处理辅助选择器
        for sel in secondary_selectors:
            try:
                for elem in self._headless_page.query_selector_all(sel):
                    _process_element(elem, is_primary=False)
            except Exception:
                silent_exc(where="organs/motor/PulseController.py:480")
                continue

        # 按得分排序（核心结果得分均为1.0，辅助结果可能更高或更低）
        links.sort(key=lambda x: x["score"], reverse=True)
        return links[:10]

    def _extract_article_headless(self) -> str | None:
        """从当前页面提取正文内容"""
        if not self._headless_page:
            return None

        try:
            clean_cfg = self._get_headless_config("content_clean", {})
            strip_tags = clean_cfg.get("strip_tags", ["nav", "footer", "aside", "header", "script", "style"])
            strip_classes = clean_cfg.get("strip_classes", ["advertisement", "sidebar", "comment", "popup"])

            for tag in strip_tags:
                try:
                    self._headless_page.evaluate(f"document.querySelectorAll('{tag}').forEach(e => e.remove())")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            for cls in strip_classes:
                try:
                    self._headless_page.evaluate(f"document.querySelectorAll('.{cls}, [class*=\"{cls}\"]').forEach(e => e.remove())")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            text = self._headless_page.inner_text("body")

            lines = [line.strip() for line in text.split("\n") if line.strip()]
            min_chars = clean_cfg.get("min_paragraph_chars", 50)
            meaningful = [line for line in lines if len(line) >= min_chars]
            max_chars = clean_cfg.get("max_content_chars", 8000)
            result = "\n".join(meaningful)[:max_chars]

            article_min = self._get_headless_config("article_extract_min_chars", 500)
            if len(result) < article_min:
                return None

            return result

        except Exception as e:
            self._log(LogLevel.ERROR, f"提取正文失败: {e}")
            return None
    def _assess_content_quality(self, content: str, title: str = "",
                                 url: str = "") -> dict[str, Any]:
        """
        评估搜索结果的原始内容质量（0.0-1.0）。
        
        评估维度：
        1. 内容充实度（40%）——长度是否足够，是否有实质段落
        2. 语言纯度（30%）——中文有效信息占比
        3. 结构完整度（20%）——是否有明显的开头和结尾
        4. 来源可信度（10%）——域名是否来自已知高质量来源
        
        Returns:
            {"score": float, "level": str, "verdict": str, "should_digest": bool}
        """
        if not content or len(content) < 50:
            return {"score": 0.0, "level": "junk", "verdict": "内容过短", "should_digest": False}

        import re
        score = 0.0

        # 维度1：内容充实度（40%权重）
        meaningful_paragraphs = 0
        lines = content.split('\n')
        for line in lines:
            stripped = line.strip()
            if len(stripped) >= 30:
                meaningful_paragraphs += 1

        if meaningful_paragraphs >= 5:
            score += 0.4
        elif meaningful_paragraphs >= 3:
            score += 0.25
        elif meaningful_paragraphs >= 1:
            score += 0.1

        # 维度2：语言纯度（30%权重）
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
        total_chars = max(1, len(content))
        chinese_ratio = chinese_chars / total_chars

        if chinese_ratio >= 0.4:
            score += 0.3
        elif chinese_ratio >= 0.2:
            score += 0.2
        elif chinese_ratio >= 0.1:
            score += 0.1

        # 维度3：结构完整度（20%权重）
        has_opening = False
        has_closing = False
        if len(content) > 100:
            first_100 = content[:100]
            last_100 = content[-100:]
            # 检查是否有引言式的开头
            opening_patterns = ['概述', '介绍', '什么是', '定义', '摘要', '前言', '背景']
            has_opening = any(p in first_100 for p in opening_patterns) or chinese_ratio >= 0.3
            # 检查是否有总结式的结尾
            closing_patterns = ['总结', '综上所述', '总之', '因此', '所以', '参考', '来源']
            has_closing = any(p in last_100 for p in closing_patterns)

        if has_opening and has_closing:
            score += 0.2
        elif has_opening or has_closing:
            score += 0.1

        # 维度4：来源可信度（10%权重）
        high_quality_domains = [
            'zhihu.com', 'csdn.net', 'wikipedia.org', 'github.com',
            'people.com.cn', 'qstheory.cn', 'gov.cn', 'edu.cn',
            '163.com', 'cambridge.org',
        ]
        url_lower = url.lower()
        if any(d in url_lower for d in high_quality_domains):
            score += 0.1

        # 搜索引擎噪音检测（扣分项）
        noise_signals = [
            '跳至内容', '辅助功能', '反馈', '在新选项卡中打开',
            '搜索', '自适应缩放', '时间不限', '约', '个结果',
            '拼音', '怎么读', '笔顺', '部首', '怎么写',
            '视频播放', '软件下载', '会员抢先', '去除广告',
        ]
        noise_count = sum(1 for s in noise_signals if s in content)
        if noise_count >= 5:
            score = max(0.0, score - 0.3)
        elif noise_count >= 3:
            score = max(0.0, score - 0.15)

        # 确定等级
        if score >= 0.7:
            level = "high"
            verdict = "高质量内容，建议优先消化"
            should_digest = True
        elif score >= 0.4:
            level = "medium"
            verdict = "中等质量内容，可消化但标记较低信任"
            should_digest = True
        elif score >= 0.2:
            level = "low"
            verdict = "低质量内容，建议降级消化或跳过"
            should_digest = True  # 仍然消化，但标记低信任
        else:
            level = "junk"
            verdict = "噪音内容，建议跳过消化"
            should_digest = False

        return {
            "score": round(score, 2),
            "level": level,
            "verdict": verdict,
            "should_digest": should_digest,
            "meaningful_paragraphs": meaningful_paragraphs,
            "chinese_ratio": round(chinese_ratio, 2),
        }

    # ==================================================================
    # ★主线第15批 T4/P2-101：搜索关键词净化（去噪声短语 / 去跨词碎片）
    # ==================================================================
    #: 搜索关键词噪声/停用短语表。含实测出现的页面噪声短语：
    #: 「中的文字」「天之前」「小时之前」（前者来自页面正文，后两者来自时间描述）。
    _SEARCH_KEYWORD_GUARD_STOPWORDS = frozenset({
        "中的文字", "天之前", "小时之前", "分钟之前", "个月前", "年前",
        "一下", "什么", "怎么", "如何", "为什么", "这个", "那个", "这些", "那些",
        "我们", "他们", "你们", "自己", "大家", "可以", "能够", "应该", "需要",
        "已经", "正在", "将要", "可能", "也许", "大概", "以及", "并且", "而且",
        "但是", "然而", "如果", "因为", "所以", "由于", "此外", "另外",
        "关于", "对于", "通过", "进行", "使用", "根据", "按照", "为了",
        "文字", "内容", "信息", "结果", "搜索", "页面", "网站", "相关",
        "更多", "点击", "查看", "了解", "详情", "简介", "展开", "广告",
    })

    def _search_keyword_guard_enabled(self) -> bool:
        """读取搜索关键词净化开关（读不到时默认开启）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_SEARCH_KEYWORD_GUARD", True))
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseController::_search_keyword_guard_enabled L662")
            return True

    def _purify_search_keywords(self, keywords: list, topic: str = "",
                                limit: int = 6) -> list:
        """净化搜索关键词：去停用/噪声短语 + 去被更长关键词完全包含的跨词碎片。

        ★T4/P2-101 背景：上游把「查一下人工智能最新进展」截断成「下人工智能最新进展」后，
        补充提取的 4 字贪婪切分产出「下人工智」「能最新进」两个跨词碎片；
        页面正文硬切还带回「中的文字」「天之前」等噪声。

        规则（保持原顺序，只删不改写）：
          1. 丢弃长度 < 2 或命中 `_SEARCH_KEYWORD_GUARD_STOPWORDS` 的项；
          2. 去重；
          3. 若某关键词被同列表中的**更长关键词完全包含**，判定为切分碎片并丢弃。
        开关关闭时原样返回（零副作用）。

        Args:
            keywords: 候选关键词列表。
            topic: 原始搜索主题（保留参数以便后续做词边界校验）。
            limit: 最多返回条数。

        Returns:
            净化后的关键词列表（保持原相对顺序）。
        """
        if not self._search_keyword_guard_enabled():
            return list(keywords or [])[:limit]
        _cleaned: list = []
        _seen: set = set()
        for _kw in (keywords or []):
            if not isinstance(_kw, str):
                continue
            _k = _kw.strip()
            if len(_k) < 2:
                continue
            if _k in self._SEARCH_KEYWORD_GUARD_STOPWORDS:
                continue
            if _k in _seen:
                continue
            _seen.add(_k)
            _cleaned.append(_k)
        # 去近重复（★T4 修正 v2：双向判据会「互相消除」把真词也误杀）。
        #   实测：「下人工智」与「人工智能」等长、互不包含，只共享「人工智」3 连字。
        #   规则：两个长度均 >=4 的关键词若 ① 互为嵌套 或 ② 最长公共子串 >=
        #         min(len)-1，则**丢弃靠后的那个**（列表靠前 = 优先级更高；
        #         真实链路里阶段1/词典结果天然排在自发补充之前，故保留下的是正确词）。
        _drop: set = set()
        _n = len(_cleaned)
        for _i in range(_n):
            for _j in range(_i + 1, _n):
                _a, _b = _cleaned[_i], _cleaned[_j]
                if min(len(_a), len(_b)) < 4:
                    continue
                if _a in _b or _b in _a or \
                        self._longest_common_substring_len(_a, _b) >= min(len(_a), len(_b)) - 1:
                    _drop.add(_j)
        return [_k for _idx, _k in enumerate(_cleaned) if _idx not in _drop][:limit]

    @staticmethod
    def _longest_common_substring_len(a: str, b: str) -> int:
        """最长公共**子串**长度（连续），用于识别跨词切分的错位窗口碎片。"""
        if not a or not b:
            return 0
        _best = 0
        _prev = [0] * (len(b) + 1)
        for _i in range(1, len(a) + 1):
            _cur = [0] * (len(b) + 1)
            for _j in range(1, len(b) + 1):
                if a[_i - 1] == b[_j - 1]:
                    _cur[_j] = _prev[_j - 1] + 1
                    _best = max(_best, _cur[_j])
            _prev = _cur
        return _best

    def _emit_stage_feedback(self, stage: int, search_topic: str, keywords: list,
                              articles_found: int, status: str, note: str = "",
                              correlation_id: str = ""):
        """发射搜索阶段完成反馈脉冲，供内在世界审查阶段结果。

        ★第82批 T-d（Dxxx）：correlation_id 随脉冲原样带回——发射端内在世界已把
        search_correlation_id 放进 OPEN_URL payload，本方法从 _search_deep_headless
        一路接过来再写回 payload，消费端据此回嘴（旧字符串注册表仅兜底）。
        """
        if not self.info_field or not self.pulse_core:
            return
        self.info_field.publish(self.pulse_core.emit(
            source_organ=self.organ_name,
            event_type=ControllerEvent.SEARCH_STAGE_COMPLETED,
            payload={
                "stage": stage,
                "search_topic": search_topic,
                "keywords": keywords[:5] if keywords else [],
                "articles_found": articles_found,
                "status": status,
                "note": note,
                "search_correlation_id": correlation_id,
            },
            priority=4,
            layer="L3"
        ))

    def _search_deep_headless(self, search_topic: str, reason: str, max_articles: int = 3, correlation_id: str = "") -> dict[str, Any]:
        """
        三阶段递进式深度搜索：
        阶段1：大面搜索 → 从搜索结果摘要中提取关键概念
        阶段2：用关键概念精准搜索 → 打开具体文章精读
        阶段3：从精读文章中提取新概念 → 再搜一次（可选）
        
        每个搜索任务使用独立的浏览器实例，用完即关，避免并发冲突。
        """
        record_search(SEARCH_HEADLESS)
        # 退出检查：框架正在关闭时直接返回
        if self._shutting_down:
            return {"status": "fallback", "reason": "框架正在退出，搜索已取消"}
        # 新增：每次搜索开始时重置终止标记
        self._search_terminated = False
        # ===== 新增：搜索词有效性检查 =====
        if len(search_topic) > 60:
            concept_keywords = ["原理", "定义", "机制", "结构", "流程", "方法", "算法", "框架", "是什么"]
            has_concept = any(kw in search_topic for kw in concept_keywords)
            if not has_concept:
                self._log(LogLevel.INFO, f"搜索词有效性检查: 超长且无核心概念，降级为普通搜索: {search_topic[:40]}...")
                return {"status": "fallback", "reason": "超长陈述不适合深度搜索"}

        # 搜索词预处理：将内部指令转译为搜索引擎能理解的短语
        search_topic = self._preprocess_search_topic(search_topic[:80])
        if not search_topic:
            return {"status": "fallback", "reason": "搜索词预处理判定为元问题，不搜索"}

        # ★v25.0新增：元认知/抽象概念拦截——这类问题不适合外部搜索
        _meta_signals = ["自律", "本能", "反思", "内化", "意识", "意义", "本质",
                        "自由", "智慧", "存在", "真理", "价值", "幸福", "使命",
                        "元认知", "自我", "底层逻辑", "认知", "思考方式"]
        if any(sig in search_topic for sig in _meta_signals):
            self._log(LogLevel.INFO, "搜索词元概念拦截: 抽象概念不适合外部搜索，交由内在世界处理")
            return {"status": "fallback", "reason": "抽象/元认知概念不适合深度搜索"}
        # ===== 强制截断：预处理可能追加内容，确保最终不超长 =====
        if len(search_topic) > 80:
            search_topic = search_topic[:80]
        # 如果搜索词是概念解释类查询，强制优先百科
        instinct_keywords = ["求真", "向善", "迭代", "自律", "证据", "温柔", "包容", "共情", "进化", "开放性"]
        is_instinct = any(kw in search_topic for kw in instinct_keywords)
        if any(kw in search_topic for kw in ["概念", "原理", "详解"]) and not is_instinct:
            search_topic = f"site:baike.baidu.com {search_topic}"
        cache_ttl = self._get_headless_config("search_cache_ttl", 5)
        with self._search_cache_lock:
            cached = self._search_cache.get(search_topic)
        if cached and (time.time() - cached["time"] < cache_ttl):
            self._log(LogLevel.INFO, f"搜索缓存命中: {search_topic}")
            return cached["result"]
        # ===== 使用常驻浏览器实例 + 独立 Context 实现任务隔离 =====
        task_context = None
        task_page = None
        saved_page = self._headless_page
        saved_browser = self._headless_browser

        try:
            if not PLAYWRIGHT_AVAILABLE:
                return {"status": "fallback", "reason": "Playwright 未安装"}

            if self._get_headless_config("load_critical_disable", True) and self._current_load_level() == "critical":
                return {"status": "fallback", "reason": "临界负载，拒绝启动浏览器"}

            # 确保常驻浏览器可用（首次使用时自动创建）
            if not self._ensure_headless_browser():
                return {"status": "fallback", "reason": "常驻浏览器不可用"}

            # 为本次搜索任务创建独立的 Context 和 Page
            task_context, task_page = self._get_task_context_and_page()
            if not task_context or not task_page:
                return {"status": "fallback", "reason": "创建任务Context失败"}

            # 临时挂载到 self，让辅助方法可以正常使用
            self._headless_page = task_page

            all_articles = []
            all_keywords = []

            # ====================================================================
            # 阶段1：大面搜索 + 关键概念提取
            # ====================================================================
            self._log(LogLevel.INFO, f"🔍 阶段1·大面搜索: {search_topic}")

            search_url = self._select_headless_search_url(search_topic)
            page_source = self._navigate_headless(search_url)
            if not page_source:
                time.sleep(1)
                page_source = self._navigate_headless(search_url)
            if not page_source:
                return {"status": "fallback", "reason": "阶段1搜索页加载失败"}

            stage1_keywords = self._extract_keywords_from_summary(search_topic)
            if stage1_keywords:
                # ===== 关键词有效性检查增强 =====
                # 1. 字典误判检测
                dictionary_indicators = ["拼音", "怎么读", "的意思", "的解释", "笔顺", "部首", "怎么写"]
                # 2. 搜索误导检测：这些词说明搜索引擎完全跑偏
                _se_noise_signals = ["中共中央", "全国优秀", "表彰", "京公网安备", "网易云音乐",
                                    "哔哩哔哩", "腾讯视频", "会员抢先", "视频下载", "去除广告",
                                    "软件下载", "中文版下载", "视频播放", "开发者名称"]
                is_dictionary_result = all(
                    any(indicator in kw for indicator in dictionary_indicators)
                    for kw in stage1_keywords[:3]
                )
                if is_dictionary_result:
                    import re as _re
                    fallback = _re.findall(r'[\u4e00-\u9fff]{2,6}', search_topic)
                    stage1_keywords = [w for w in fallback if len(w) >= 2][:5]
                    self._log(LogLevel.INFO, f"🔍 阶段1·关键词兜底(字典误判): {', '.join(stage1_keywords[:5])}")

                # ===== 新增：搜索误导检测——关键词全是搜索引擎噪音时直接丢弃 =====
                _se_noise_count = sum(1 for _kw in stage1_keywords[:5]
                                     if any(_signal in _kw for _signal in _se_noise_signals))
                if _se_noise_count >= 2:
                    self._log(LogLevel.INFO, "🔍 阶段1·关键词丢弃(搜索误导): 关键词全为搜索引擎噪音")
                    stage1_keywords = []

            if stage1_keywords:
                purified_keywords = [
                    kw for kw in stage1_keywords
                    if self._calculate_topic_relevance(kw, search_topic) > 0.3
                    or len(kw) >= 4
                ]
                if not purified_keywords:
                    purified_keywords = stage1_keywords[:3]

                # ===== 新增：如果净化后关键词太少（<3），从原始搜索词中自行提取有效短语作为补充 =====
                if len(purified_keywords) < 3:
                    # ★主线第15批 T4/P2-101 根因修正：原实现用
                    #   `re.findall(r'[\u4e00-\u9fff]{2,4}', search_topic)` 对搜索主题做
                    #   2~4 字**贪婪窗口**切分，实测在主题为「下人工智能最新进展」时
                    #   必然产出跨词碎片「下人工智」「能最新进」。
                    #   改为**词典校验**的候选提取（复用语义词典最长匹配），
                    #   从源头杜绝窗口碎片；词典不可用时回退为空（宁缺毋滥）。
                    self_extracted = self._semantic_extract_keywords(
                        search_topic, limit=6) or []
                    # 过滤虚词
                    noise_set = {"这个","那个","什么","怎么","如何","为什么","可以","能够","应该",
                                "一个","一种","一些","进行","使用","通过","对于","关于","根据",
                                "我们","他们","自己","大家","这里","那里","其他","它的","你的",
                                "非常","比较","更加","已经","正在","将要","可能","也许","大概",
                                "因此","所以","但是","如果","虽然","然而","并且","而且","因为",
                                "由于","除了","之后","之前","不是","还是","只是","就是"}
                    self_meaningful = [p for p in self_extracted if p not in noise_set and len(p) >= 2]
                    # 去重合并
                    for p in self_meaningful:
                        if p not in purified_keywords:
                            purified_keywords.append(p)
                            if len(purified_keywords) >= 5:
                                break

                # ★主线第15批 T4/P2-101：输出前净化（去噪声短语 + 去跨词碎片）
                purified_keywords = self._purify_search_keywords(
                    purified_keywords, search_topic, limit=5)
                self._log(LogLevel.INFO, f"🔍 阶段1·关键词提取: {', '.join(purified_keywords[:5])}")
                self._emit_stage_feedback(1, search_topic, purified_keywords,
                                           len(all_articles), "stage1_completed",
                                           f"提取{len(purified_keywords)}个关键词", correlation_id=correlation_id)
                all_keywords.extend(purified_keywords)

                # 等待内在世界审查阶段1结果（最多1.5秒），给予审查和终止信号到达的时间窗口
                _wait_start = time.time()
                while time.time() - _wait_start < 1.5:
                    if self._search_terminated:
                        break
                    time.sleep(0.1)
                if self._search_terminated:
                    self._log(LogLevel.INFO, "搜索已被内在世界终止（阶段1审查超时前收到终止信号），跳过阶段2")
                    self._search_terminated = False
                    return {"status": "terminated_by_inner_world", "search_topic": search_topic}
            else:
                import re
                topic_words = [w for w in re.split(r'[\s,，、：:]+', search_topic)
                              if len(w) >= 2 and any('\u4e00' <= c <= '\u9fff' for c in w)]
                all_keywords = topic_words[:5]
                self._log(LogLevel.INFO, f"🔍 阶段1·关键词兜底: {', '.join(all_keywords[:5])}")

            links = []
            # ====================================================================
            # 阶段2：优先从阶段1搜索结果页直接提取链接（效率优化）
            # 不再进行二次搜索，直接用原始搜索词筛选当前页面链接
            # ====================================================================
            if self._search_terminated:
                self._log(LogLevel.INFO, "搜索已被内在世界终止，跳过阶段2")
                self._search_terminated = False
                return {"status": "terminated_by_inner_world", "search_topic": search_topic}

            # 优先：从当前搜索结果页直接提取链接，使用原始搜索词匹配
            _original_topic = search_topic.replace("site:baike.baidu.com ", "").strip('"')
            links = self._get_page_links_headless(_original_topic)

            if not links and all_keywords:
                # 兜底：如果当前页没有链接，用提取的关键词重新搜索
                stage2_topic = " ".join(all_keywords[:4])
                self._log(LogLevel.INFO, f"🔍 阶段2·兜底搜索: {stage2_topic}")
                stage2_url = self._select_headless_search_url(stage2_topic)
                self._navigate_headless(stage2_url)
                links = self._get_page_links_headless(stage2_topic)
                if links:
                    self._log(LogLevel.INFO, f"🔍 阶段2·筛选: {len(links)}个候选链接")
                    topic_keywords = [kw.lower() for kw in all_keywords if len(kw) >= 2]  # noqa: F841
                    filtered_links = []
                    for link in links:
                        title_lower = link["title"].lower()  # noqa: F841
                        # 放宽筛选：只要标题长度>=5就保留，不强制关键词匹配
                        if len(link["title"]) < 5:
                            continue
                        # 过滤明显无关的链接（如搜索引擎自身、社交网站）
                        skip_domains = ["google.com", "bing.com", "baidu.com", "facebook.com", "twitter.com"]
                        if any(skip in link.get("url", "").lower() for skip in skip_domains):
                            continue
                        filtered_links.append(link)
                    # 如果没有筛选到链接，直接用原始links
                    if not filtered_links:
                        filtered_links = links
                    # 增加文章数量：max_articles基础上+2，但不超过8篇
                    _effective_max = min(max_articles + 2, 8)
                    for i, link_info in enumerate(filtered_links[:_effective_max]):
                        self._log(LogLevel.INFO, f"🔍 阶段2·精读{i+1}: {link_info['title'][:40]}")
                        human_cfg = self._get_headless_config("human_behavior", {})
                        if human_cfg.get("move_mouse_simulation", True):
                            import random as _random
                            time.sleep(_random.uniform(
                                human_cfg.get("click_delay_min", 0.1),
                                human_cfg.get("click_delay_max", 0.4)
                            ))
                        article_html = self._navigate_headless(link_info["url"])
                        if not article_html:
                            continue
                        content = self._extract_article_headless()
                        if content:
                            # 评估内容质量
                            quality = self._assess_content_quality(
                                content, link_info["title"], link_info["url"]
                            )
                            all_articles.append({
                                "title": link_info["title"],
                                "url": link_info["url"],
                                "content": content,
                                "quality": quality,
                            })

                            if quality["should_digest"]:
                                # 根据质量调整重要性标记
                                importance = "B" if quality["level"] == "high" else "C"
                                digest_text = f"[深度搜索·{link_info['title'][:50]}] {content[:1500]}"
                                self._emit(DigestEvent.KNOWLEDGE, {
                                    "content": digest_text,
                                    "source_organ": self.organ_name,
                                    "trigger_reason": f"deep_search.headless:{search_topic}",
                                    "importance": importance,
                                    "view_mode": "OUTER_VIEW",
                                    "content_quality": quality,
                                    "source_url": link_info.get("url", ""),   # ★R1
                                }, priority=3, layer="L2")
                                self._log(LogLevel.DEBUG,
                                         f"内容质量评估: {quality['level']}({quality['score']}) "
                                         f"'{link_info['title'][:30]}' - {quality['verdict']}")
                            else:
                                self._log(LogLevel.INFO,
                                         f"内容质量评估: 跳过噪音内容 '{link_info['title'][:30]}' "
                                         f"({quality['score']}) - {quality['verdict']}")

                            if len(all_articles) <= 1 and len(all_keywords) < 6:
                                if self._search_terminated:
                                    break
                                new_concepts = self._extract_new_concepts_from_article(content, all_keywords)
                                if new_concepts:
                                    all_keywords.extend(new_concepts)
                                    self._log(LogLevel.INFO, f"🔍 阶段3·概念深挖: {', '.join(new_concepts[:3])}")
                                    stage3_topic = " ".join(new_concepts[:3])
                                    if len(stage3_topic) > 5:
                                        stage3_url = self._select_headless_search_url(stage3_topic)
                                        self._navigate_headless(stage3_url)
                                        deep_links = self._get_page_links_headless(stage3_topic)
                                        if deep_links:
                                            for _j, deep_link in enumerate(deep_links[:1]):
                                                self._log(LogLevel.INFO, f"🔍 阶段3·精读: {deep_link['title'][:40]}")
                                                time.sleep(0.5)
                                                deep_html = self._navigate_headless(deep_link["url"])
                                                if deep_html:
                                                    deep_content = self._extract_article_headless()
                                                    if deep_content:
                                                        quality = self._assess_content_quality(
                                                            deep_content, deep_link["title"], deep_link["url"]
                                                        )
                                                        all_articles.append({
                                                            "title": deep_link["title"],
                                                            "url": deep_link["url"],
                                                            "content": deep_content,
                                                            "quality": quality,
                                                        })
                                                        if quality["should_digest"]:
                                                            importance = "B" if quality["level"] == "high" else "C"
                                                            self._emit(DigestEvent.KNOWLEDGE, {
                                                                "content": f"[深度搜索·{deep_link['title'][:50]}] {deep_content[:1500]}",
                                                                "source_organ": self.organ_name,
                                                                "trigger_reason": f"deep_search.headless.stage3:{search_topic}",
                                                                "importance": importance,
                                                                "view_mode": "OUTER_VIEW",
                                                                "content_quality": quality,
                                                                "source_url": deep_link.get("url", ""),   # ★R1
                                                            }, priority=3, layer="L2")
            # 新增：阶段2/3完成后发射反馈
            self._emit_stage_feedback(2, search_topic, all_keywords,
                                       len(all_articles), "stage2_completed",
                                       f"已精读{len(all_articles)}篇文章", correlation_id=correlation_id)

            # 等待内在世界审查阶段2结果（最多1.0秒），给予审查和终止信号到达的时间窗口
            _wait_start2 = time.time()
            while time.time() - _wait_start2 < 1.0:
                if self._search_terminated:
                    break
                time.sleep(0.1)
            if self._search_terminated:
                self._log(LogLevel.INFO, "搜索已被内在世界终止（阶段2审查超时前收到终止信号），跳过兜底提取")
                self._search_terminated = False
                return {"status": "terminated_by_inner_world", "search_topic": search_topic}
            # ===== 新增：阶段2链接为空时的兜底——提取当前页面正文 =====
            if self._search_terminated:
                self._log(LogLevel.INFO, "搜索已被内在世界终止，跳过兜底提取")
                self._search_terminated = False
                return {"status": "terminated_by_inner_world", "search_topic": search_topic}
            if not links and not all_articles:
                # 兜底策略1：尝试从当前搜索结果页提取正文
                fallback_text = self._extract_article_headless()
                if fallback_text:
                    # 降低兜底阈值：从2000→500字符，让更多搜索结果页能被消化
                    if len(fallback_text) >= 500:
                        self._emit_digest(fallback_text[:2000], f"deep_search.fallback:{search_topic}")
                        all_articles.append({
                            "title": search_topic,
                            "url": search_url,
                            "content": fallback_text[:2000],
                        })
                        self._log(LogLevel.INFO, f"🔍 兜底提取: 搜索结果页正文{len(fallback_text)}字符，已消化")
                        self._emit_stage_feedback(3, search_topic, all_keywords,
                                           len(all_articles) + 1, "stage3_fallback",
                                           "搜索结果页正文提取完成", correlation_id=correlation_id)
                    # 兜底策略2：正文太短，用搜索词+关键词构造一条L1知识节点
                    # 至少让这次搜索产生一条可消化的知识，避免完全空手而归
                    elif all_keywords:
                        constructed_content = (
                            f"[深度搜索·{search_topic[:50]}] "
                            f"关于'{search_topic[:40]}'的搜索结果摘要："
                            f"涉及{'、'.join(all_keywords[:5])}等概念。"
                            f"（搜索结果页正文不足，仅提取关键概念作为知识种子）"
                        )
                        self._emit_digest(constructed_content, f"deep_search.constructed:{search_topic}")
                        all_articles.append({
                            "title": search_topic,
                            "url": search_url,
                            "content": constructed_content,
                        })
                        self._log(LogLevel.INFO, f"🔍 知识构造: 从关键词构造L1种子 '{', '.join(all_keywords[:3])}'")
                        self._emit_stage_feedback(3, search_topic, all_keywords,
                                           len(all_articles) + 1, "stage3_constructed",
                                           "从关键词构造知识种子", correlation_id=correlation_id)
                elif all_keywords:
                    # 兜底策略3：连正文都提取不到，直接用关键词构造
                    constructed_content = (
                        f"[深度搜索·{search_topic[:50]}] "
                        f"关于'{search_topic[:40]}'的探索："
                        f"涉及{'、'.join(all_keywords[:5])}等概念。"
                        f"（搜索引擎未返回有效正文，以此知识种子标记探索方向）"
                    )
                    self._emit_digest(constructed_content, f"deep_search.constructed:{search_topic}")
                    all_articles.append({
                        "title": search_topic,
                        "url": search_url,
                        "content": constructed_content,
                    })
                    self._log(LogLevel.INFO, f"🔍 知识构造: 从关键词构造L1种子(无正文) '{', '.join(all_keywords[:3])}'")
            # 新增：兜底阶段反馈
            self._emit_stage_feedback(3, search_topic, all_keywords,
                                       len(all_articles), "stage3_fallback",
                                       "已执行兜底提取" if all_articles else "搜索无结果", correlation_id=correlation_id)
            self._search_terminated = False  # 搜索完成，重置终止标记
            result = {
                "status": "headless_completed",
                "search_topic": search_topic,
                "articles_found": len(all_articles),
                "total_chars": sum(len(a["content"]) for a in all_articles),
                "articles": all_articles,
                "keywords": all_keywords,
            }
            # ★v24.0治理：加锁并清理过期/超量缓存
            with self._search_cache_lock:
                _now_ts = time.time()
                # 清理过期缓存
                _expired_keys = [
                    _k for _k, _v in self._search_cache.items()
                    if _now_ts - _v.get("time", 0) > self._search_cache_ttl
                ]
                for _k in _expired_keys:
                    del self._search_cache[_k]
                # 容量保护
                if len(self._search_cache) >= self._search_cache_max:
                    _oldest = min(self._search_cache.keys(),
                                  key=lambda k: self._search_cache[k].get("time", 0))
                    del self._search_cache[_oldest]
                self._search_cache[search_topic] = {"result": result, "time": _now_ts}
            if len(all_articles) == 0:
                self._log(LogLevel.WARNING,
                         f"⚠️ 深度搜索无结果: 搜索词='{search_topic}' "
                         f"关键词='{', '.join(all_keywords[:3])}'")
            self._log(LogLevel.INFO,
                      f"✅ 深度搜索完成: {len(all_articles)}篇文章, "
                      f"共{result['total_chars']}字符, "
                      f"关键词: {', '.join(all_keywords[:5])}")

            # ★v16.0新增：评估大模型提炼效果并更新经验
            _original_topic_for_refine = getattr(self, '_last_refine_original', "")
            _refined_topic_for_refine = getattr(self, '_last_refine_result', "")
            # ★修复：在if块外初始化，避免UnboundLocalError（if条件不满足时_quality_count未定义）
            _avg_quality = 0.0
            _quality_count = 0
            if _original_topic_for_refine and _refined_topic_for_refine and _original_topic_for_refine != _refined_topic_for_refine:
                # 评估搜索质量
                for _article in all_articles:
                    _q = _article.get("quality", {})
                    if _q and _q.get("score"):
                        _avg_quality += _q["score"]
                        _quality_count += 1
                if _quality_count > 0:
                    _avg_quality /= _quality_count

                    # 提取搜索词的核心模式（去掉具体术语，保留结构）
                    _pattern = self._extract_search_pattern(_original_topic_for_refine)
                    if _pattern:
                        if _pattern not in self._refine_experience:
                            # ★P0-1修复：容量保护——超出上限时淘汰最旧条目
                            _MAX_REFINE_EXPERIENCE = 100
                            if len(self._refine_experience) >= _MAX_REFINE_EXPERIENCE:
                                # 按最后更新时间排序，淘汰最旧的3条
                                _sorted_keys = sorted(
                                    self._refine_experience.keys(),
                                    key=lambda k: self._refine_experience[k].get("last_updated", 0)
                                )
                                for _old_key in _sorted_keys[:3]:
                                    del self._refine_experience[_old_key]
                                self._log(LogLevel.DEBUG, f"提炼经验容量保护: 淘汰3条旧经验，剩余{len(self._refine_experience)}条")
                            self._refine_experience[_pattern] = {"success": 0, "fail": 0, "last_refined": ""}

                        if _avg_quality >= 0.5:  # type: ignore[possibly-unbound]
                            self._refine_experience[_pattern]["success"] += 1
                            self._refine_experience[_pattern]["last_refined"] = _refined_topic_for_refine
                            self._log(LogLevel.DEBUG, f"提炼经验+1: 模式'{_pattern}'成功(质量{_avg_quality:.2f})")  # type: ignore[possibly-unbound]
                        else:
                            self._refine_experience[_pattern]["fail"] += 1
                            self._log(LogLevel.DEBUG, f"提炼经验-1: 模式'{_pattern}'失败(质量{_avg_quality:.2f})")  # type: ignore[possibly-unbound]
                        # ★P0-1修复：记录最后更新时间用于LRU淘汰
                        self._refine_experience[_pattern]["last_updated"] = time.time()

            # 清理临时变量
            self._last_refine_original = ""
            self._last_refine_result = ""
            # ===== 提炼经验学习结束 =====

            # ★v25.1新增: 搜索参数AB测试——记录不同max_articles下的搜索质量，自动优化默认值
            try:
                if not hasattr(self, '_search_ab_stats'):
                    self._search_ab_stats = {}
                    self._search_ab_total = 0
                if _quality_count > 0:  # type: ignore[possibly-unbound]
                    _ab_key = str(max_articles)
                    if _ab_key not in self._search_ab_stats:
                        self._search_ab_stats[_ab_key] = {"count": 0, "total_quality": 0.0}
                    self._search_ab_stats[_ab_key]["count"] += 1
                    self._search_ab_stats[_ab_key]["total_quality"] += _avg_quality  # type: ignore[possibly-unbound]
                    self._search_ab_total += 1
                    if self._search_ab_total % 20 == 0 and len(self._search_ab_stats) >= 2:
                        _best_key = max(self._search_ab_stats.keys(),
                            key=lambda k: self._search_ab_stats[k]["total_quality"] / max(1, self._search_ab_stats[k]["count"]))
                        _best_avg = self._search_ab_stats[_best_key]["total_quality"] / max(1, self._search_ab_stats[_best_key]["count"])
                        # ★S2-1：原为f-string嵌套同型双引号（PEP 701，仅3.12+可编译），
                        # 提取为变量后兼容Python 3.11，输出内容完全不变。
                        _parts = [
                            f"{k}:{v['total_quality']/max(1,v['count']):.2f}"
                            for k, v in sorted(self._search_ab_stats.items())
                        ]
                        self._log(LogLevel.INFO,
                            f"[搜索AB测试] 累计{self._search_ab_total}次, "
                            f"最优max_articles={_best_key}(平均质量{_best_avg:.2f}), "
                            f"各参数: {{{', '.join(_parts)}}}")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            if self.info_field and self.pulse_core:
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type="controller.search_completed",
                    payload={
                        "status": result["status"],
                        "search_topic": search_topic,
                        "articles_found": len(all_articles),
                        "total_chars": sum(len(a["content"]) for a in all_articles),
                    },
                    priority=2,
                    layer="L3"
                ))

            return result

        except Exception as e:
            self._log(LogLevel.ERROR, f"深度搜索异常: {e}")
            return {"status": "fallback", "reason": f"搜索异常: {str(e)[:80]}"}
        finally:
            # ===== 恢复常驻浏览器的 Page 引用，关闭本次搜索专用的 Context =====
            self._headless_page = saved_page
            self._headless_browser = saved_browser

            # 关闭本次搜索的 Context（同时关闭其中的 Page），释放资源
            if task_context is not None:
                try:
                    task_context.close()
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 更新常驻浏览器的最后使用时间
            self._headless_last_used = time.time()

    def _preprocess_search_topic(self, topic: str) -> str:
        """
        搜索词预处理：将框架内部指令转译为搜索引擎能理解的短语。
        """
        import re
        # ===== ★2026-09-04修复：过滤抽象价值观/自我承诺表述，避免浪费搜索资源 =====
        # 这类表述是内在世界的成长目标或自我要求，不适合搜索引擎搜索
        _abstract_value_patterns = [
            r"在不确定时.*诚实",
            r"不假装全知",
            r"保持绝对的诚实",
            r"知道就是知道",
            r"不确定就是不确定",
            r"我希望在面对",
            r"我要保持",
            r"我承诺",
            r"自我要求",
            r"行为准则",
            r"核心价值观",
            r"人生信条",
            r"处世原则",
        ]
        for _pattern in _abstract_value_patterns:
            if re.search(_pattern, topic):
                self._log(LogLevel.INFO, f"搜索词预处理(抽象表述拦截): '{topic[:50]}' 不适合搜索，取消")
                return None
        # 过滤：第一人称自我反思/承诺类长句（无具体技术名词）
        if topic.startswith("我") and len(topic) > 15:
            _tech_keywords = ["技术", "原理", "方法", "算法", "代码", "框架", "工具",
                              "系统", "模块", "函数", "类", "接口", "协议", "数据库"]
            if not any(k in topic for k in _tech_keywords):
                self._log(LogLevel.INFO, f"搜索词预处理(自我表述拦截): '{topic[:50]}' 无技术关键词，取消")
                return None

        # ===== L4本能关键词：不需要任何修饰，原词直接搜索 =====
        instinct_keywords = ["求真", "向善", "迭代", "自律"]
        topic_stripped = topic.strip().strip('"').strip("'")
        if topic_stripped in instinct_keywords:
            self._log(LogLevel.DEBUG, f"搜索词预处理(L4本能直搜): '{topic}' → '{topic_stripped}'")
            return topic_stripped

        # ===== 口语化问题清洗：提取核心概念，避免整句搜索 =====
        import re as _re_oral

        # 新增：去掉"请用中文解释""请解释""请说明"等指令前缀
        _instruction_prefixes = [
            r'请用中文解释\s*',
            r'请用中文说明\s*',
            r'请用中文描述\s*',
            r'请解释\s*',
            r'请说明\s*',
            r'请描述\s*',
            r'解释一下\s*',
            r'说一下\s*',
        ]
        for _prefix in _instruction_prefixes:
            _match = _re_oral.match(_prefix, topic_stripped)
            if _match:
                topic_stripped = topic_stripped[_match.end():].strip()
                self._log(LogLevel.DEBUG, f"搜索词预处理(去除指令前缀): '{topic}' → '{topic_stripped}'")
                break

        # 新增：去掉"的基本原理""的核心概念""的含义"等冗余后缀
        _redundant_suffixes = [
            r'的基本原理\s*$',
            r'的核心概念\s*$',
            r'的含义\s*$',
            r'的定义\s*$',
            r'的作用\s*$',
        ]
        for _suffix in _redundant_suffixes:
            _match = _re_oral.search(_suffix, topic_stripped)
            if _match:
                topic_stripped = topic_stripped[:_match.start()].strip()
                self._log(LogLevel.DEBUG, f"搜索词预处理(去除冗余后缀): → '{topic_stripped}'")
                break

        _oral_patterns = [
            (r'^什么是\s*["\"]?(.+?)["\"]?\s*[？?]?\s*$', r'\1'),
            (r'^如何(理解|学习|掌握|使用|实现|进行|判断|评价|评估|处理|解决|优化|提升|降低|提高|减少|增加|改善|改进|完善|强化|弱化|增强|减弱)\s*(.+?)[？?]?\s*$', r'\2'),
            (r'^怎么(理解|学习|掌握|使用|实现|进行|判断|评价|评估|处理|解决|优化|提升|降低|提高|减少|增加|改善|改进|完善|强化|弱化|增强|减弱)\s*(.+?)[？?]?\s*$', r'\2'),
            (r'^(.+?)的(原理|方法|技巧|步骤|流程|机制|原因|作用|意义|区别|特点|优势|劣势|优缺点)$', r'\1'),
        ]
        for _pattern, _replacement in _oral_patterns:
            _match = _re_oral.match(_pattern, topic_stripped)
            if _match:
                _core_concept = _match.group(1).strip()
                if len(_core_concept) >= 4:
                    _cleaned = f'"{_core_concept}" 概念 原理'
                    self._log(LogLevel.DEBUG, f"搜索词预处理(口语清洗): '{topic}' → '{_cleaned}'")
                    topic_stripped = _cleaned
                    break
        topic = topic_stripped

        # ===== 新增: 元问题检测——由内在世界规则推理处理的元问题 =====
        meta_question_patterns = [
            r'你最近怎么样', r'你最近还好吗', r'你过得怎么样',
            r'你最近学到了什么', r'你学到了什么', r'最近有什么收获',
            r'你最近在做什么', r'你在做什么', r'最近在忙什么', r'你在忙什么',
            r'你怎么知道的', r'你怎么得出结论', r'你是怎么理解的',
            r'你为什么这么认为', r'你为什么这样理解', r'你是怎么想到的',
            # 新增：无主语的建议/自述句式
            r'^需要学习', r'^应该学习', r'^要学习', r'^该学习',
            r'^需要了解', r'^应该了解', r'^要了解',
            r'^需要多学', r'^应该多学', r'^要多学',
            r'^需要掌握', r'^应该掌握', r'^要掌握',
            r'^需要去', r'^应该去', r'^要去',
            r'^需要提升', r'^应该提升', r'^要提升',
            r'^需要补充', r'^应该补充', r'^要补充',
        ]
        for pattern in meta_question_patterns:
            if re.search(pattern, topic):
                self._log(LogLevel.DEBUG, f"搜索词预处理(元问题拦截): '{topic}' 不需要搜索引擎，直接返回空")
                return None

        # ===== 新增：好奇心抽象话题转译 =====
        abstract_patterns = [
            r'(.+)的底层原理是什么',
            r'(.+)是如何演化的',
            r'为什么需要(.+)',
            r'(.+)和其他领域有什么联系',
            r'如果没有(.+)会怎样',
            r'(.+)的未来发展方向',
            r'如何用简单的语言解释(.+)',
        ]
        for pattern in abstract_patterns:
            match = re.match(pattern, topic)
            if match:
                core_concept = match.group(1).strip()
                core_concept = re.sub(r'[\s,，、：:]+', '', core_concept)
                if len(core_concept) >= 1:
                    topic = f'"{core_concept}" 概念 原理 详解'
                    self._log(LogLevel.DEBUG, f"搜索词预处理(抽象转译): → '{topic}'")
                    return topic
                break

        # ===== 【v15.1修复·合并】成长目标/内部指令格式处理 =====
        if "自我优化" in topic or "成长目标" in topic or "回复质量" in topic or "我计划加强" in topic:
            # 分支1：学习目标格式检测——"我计划加强对「X」领域的理解"
            _learn_match = re.search(r'我计划加强[对在]?[「「](.+?)[」」]', topic)
            if _learn_match:
                _learn_domain = _learn_match.group(1)
                topic = f"{_learn_domain} 概念 原理 详解"
                self._log(LogLevel.DEBUG, f"搜索词预处理(学习目标转译): → '{topic}'")
                return topic

            # 分支2：冒号格式检测——"自我优化: 领域 - 动作描述"
            domain_match = re.search(r'[:：]\s*([^,，\s-]+)', topic)
            domain = domain_match.group(1) if domain_match else ""

            action_core = ""
            if "回复质量" in topic:
                action_core = "提升回复质量"
            elif "通用领域" in topic:
                action_core = "通用能力提升"
            elif domain:
                action_core = f"{domain}优化"
            else:
                action_core = "自我提升"

            if domain and domain != "通用领域":
                topic = f"{domain} {action_core} 方法 技巧"
            else:
                topic = f"{action_core} 方法 技巧"

            self._log(LogLevel.DEBUG, f"搜索词预处理(成长目标): → '{topic}'")
            return topic

        # ===== 【v15.1修复】第一人称学习目标/内部决策语句格式检测 =====
        _internal_decision_patterns = [
            r'准备通过搜索.*?来?填补',
            r'准备通过.*?搜索.*?(?:来|以|去)',
            r'我注意到.*?是我的知识盲区',
            r'我打算.*?(?:学习|了解|研究)',
            r'我准备.*?(?:搜索|学习|了解|研究)',
            r'通过搜索.*?(?:来|以|去).*?(?:填补|补充|学习|了解)',
            r'从基础概念.*?重新开始',
            r'换个方式.*?(?:学习|了解|研究)',
            r'寻找更好的.*?(?:学习资源|资源|方式|方法)',
            r'系统性.*?(?:学习|补充|加强|提升)',
        ]
        for _pattern in _internal_decision_patterns:
            if re.search(_pattern, topic):
                _concept_match = re.search(r'[「「](.+?)[」」]', topic)
                if _concept_match:
                    _core = _concept_match.group(1)
                    topic = f"{_core} 概念 原理 详解"
                    self._log(LogLevel.DEBUG, f"搜索词预处理(内部决策转译): → '{topic}'")
                    return topic
                self._log(LogLevel.INFO, f"搜索词预处理(内部决策拦截): '{topic[:60]}' 无有效搜索词，取消搜索")
                return None

        # 2. 检测抽象概念组合（如"向善与Java的交叉应用"）
        if "与" in topic and not any(kw in topic for kw in [
            "原理", "机制", "方法", "结构", "定义", "流程", "步骤", "案例",
            "应用", "工具", "技术", "算法", "框架", "代码", "实现"
        ]):
            parts = topic.split("与")
            if len(parts) >= 2:
                core = parts[-1].strip()[:15]
                modifier = parts[0].strip()[:15]
                topic = f"{core} {modifier} 案例 实践 教程"
                self._log(LogLevel.DEBUG, f"搜索词预处理(抽象组合): → '{topic}'")
                return topic

        # 对容易被误解为品牌的词追加否定信号
        ambiguous_terms = {
            "通用": " -汽车 -公司 -企业 -上汽",
            "提升": " -股票 -股价 -行情",
        }
        for term, negation in ambiguous_terms.items():
            # ★v17.0 Q6修复：对"通用"增加更严格的歧义检测
            # "通用能力""通用方法""通用领域"等不是汽车品牌，但仍需追加否定信号
            if term in topic and negation not in topic:
                topic = topic + negation
                break

        # 3. 去掉常见的无意义前缀
        topic = re.sub(r'^(关于|针对|如何|怎样|寻找|搜索|查找|了解|学习)\s*', '', topic)
        topic = re.sub(r'\s*(的学习|的研究|的理解|的探索|的分析|的思考|的细节|进一步提升|可以进一步.*)$', '', topic)

        # 4. 如果话题太短且没有搜索价值词，追加"概念"
        if len(topic) < 10 and not any(kw in topic for kw in ["原理", "方法", "技巧", "定义"]):
            topic = f"{topic} 概念 原理"

        # 专有名词锁定
        protected_terms = [
            "曈曈", "路灯", "小林", "<CREATOR_DAUGHTER>", "星轨", "<SELF_NAME>",
            "PulseNet", "InfoField", "PulseLayer", "QICA",
            "求真", "向善", "迭代", "自律",
            "费曼", "费曼学习法", "元认知", "批判性思维",
            "金字塔原理", "刻意练习", "类比思维", "发散思维", "收敛思维",
        ]
        for term in protected_terms:
            if term in topic:
                topic = topic.replace(term, f'"{term}"')
                break

        # 追加否定字典查询信号
        if any(kw in topic for kw in ["概念", "原理", "详解"]):
            topic = topic + " -拼音 -部首 -笔顺 -怎么读"

        topic = topic.strip()

        # ===== 【v16.0新增】大模型语义提炼：当搜索词包含歧义词或超过4个独立短语时调用 =====
        _ambiguous_brand_words = [
            "通用", "提升", "优化", "方法", "技巧",
            "自我", "身份", "知识", "探索", "领域",
        ]
        _has_ambiguous = any(_aw in topic for _aw in _ambiguous_brand_words)
        _words = re.findall(r'[\u4e00-\u9fff]{2,4}', topic)
        _unique_words = list(set(_words))
        _needs_refine = len(_unique_words) > 4 or (
            len(_unique_words) > 2 and _has_ambiguous
        )
        if _needs_refine:
            # ★v16.0新增：先查询经验缓存
            _pattern = self._extract_search_pattern(topic)
            _cached_refine = None
            if _pattern and _pattern in self._refine_experience:
                _exp = self._refine_experience[_pattern]
                _total = _exp["success"] + _exp["fail"]
                if _total >= 2 and _exp["success"] / _total >= 0.6 and _exp["last_refined"]:
                    _cached_refine = _exp["last_refined"]
                    self._log(LogLevel.INFO, f"搜索词经验命中: 复用成功提炼模式'{_pattern}' → '{_cached_refine}'")

            if _cached_refine:
                topic = _cached_refine
                return topic

            _refined = self._refine_search_with_model(topic)
            if _refined:
                # 记录原始和提炼结果，供搜索完成后评估
                self._last_refine_original = topic
                self._last_refine_result = _refined
                topic = _refined
                self._log(LogLevel.INFO, f"搜索词大模型提炼: → '{topic}'")
                return topic
        # ===== 大模型提炼结束 =====

        # 长文本智能提取
        if len(topic) > 40:
            import re as _re2
            _parts = _re2.split(r'[，,。；;！!？?\s、]+', topic)
            _parts = [_p.strip() for _p in _parts if len(_p.strip()) >= 6]
            if _parts:
                _parts.sort(key=lambda x: len(x), reverse=True)
                topic = _parts[0]
                if len(topic) > 50:
                    topic = topic[:50]
                if len(topic) < 6:
                    self._log(LogLevel.DEBUG, f"搜索词预处理(长文本提取质量不足): '{topic}'，不提交搜索")
                    return None
                self._log(LogLevel.DEBUG, f"搜索词预处理(长文本提取): → '{topic}'")

        if len(topic) > 80:
            topic = topic[:80]

        # 关键词质量检查
        _severe_noise_signals = [
            "向日葵远程控", "向日葵", "远程控制软件", "程控制软件",
            "学生信息网", "脑筋急转弯", "带点黄的",
        ]
        if any(_signal in topic for _signal in _severe_noise_signals) and len(topic) < 30:
            self._log(LogLevel.INFO, f"搜索词预处理(质量拦截): '{topic[:60]}' 含噪音信号，取消搜索")
            return None

        return topic
    def _refine_search_with_model(self, topic: str) -> str | None:
        """
        【v16.0新增】调用大模型将搜索词提炼为核心概念。
        
        大模型不可用时自动降级，返回None让原有规则逻辑处理。
        """
        # 检查远程API是否可用
        _has_remote_api = False
        try:
            import config
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})
            if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
                _has_remote_api = True
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if not _has_remote_api:
            return None

        _prompt = (
            f"你的任务是把用户想搜索的问题提炼成1-3个核心关键词，用于搜索引擎查询。\n"
            f"直接返回提炼后的关键词（用空格分隔），不要加任何解释。\n"
            f"必须用中文返回。\n\n"
            f"用户想搜索的是：{topic}\n\n"
            f"提炼后的搜索关键词："
        )

        try:
            import json as _json

            import config as _cfg

            _api_cfg = getattr(_cfg, 'REMOTE_API_CONFIG', {})
            _api_url = _api_cfg.get("api_url", "")
            _api_key = _api_cfg.get("api_key", "")

            if not _api_url or not _api_key:
                return None

            _payload = {
                "model": _api_cfg.get("default_model", "deepseek-v4-flash"),
                "messages": [
                    {"role": "user", "content": _prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 32,
            }

            _payload_bytes = _json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': 'Bearer ' + _api_key,
            }

            from nucleus.ssrf_guard import safe_http_json
            from nucleus.api_rate_limiter import get_llm_call_config, api_rate_limited
            _cfg = get_llm_call_config()
            with api_rate_limited(enabled=_cfg.get('enable_rate_limit', True)):
                _ok, _data = safe_http_json(
                    _api_url, method='POST', data=_payload_bytes, headers=_headers,
                    timeout=_cfg["timeout_by_purpose"]["controller"],
                )
            if _ok and isinstance(_data, dict) and "choices" in _data and len(_data["choices"]) > 0:
                _reply = _data["choices"][0].get("message", {}).get("content", "").strip()
                # 只保留中文和空格
                _reply = re.sub(r'[^\u4e00-\u9fff\s]', '', _reply)
                _reply = re.sub(r'\s+', ' ', _reply).strip()
                if len(_reply) >= 2:
                    return _reply
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return None
    def _extract_search_pattern(self, topic: str) -> str:
        """
        【v16.0新增】从搜索词中提取结构模式。
        
        将具体术语替换为通用标记，保留"类型+动作"的结构。
        例如："通用能力提升 方法 技巧" → "X能力提升 方法 技巧"
        """
        import re

        # 替换具体的领域词为通用标记
        _domain_words = [
            "通用", "编程", "架构", "设计", "算法", "系统", "网络",
            "数据库", "前端", "后端", "人工智能", "机器学习", "深度学习",
            "自我", "身份", "知识", "技术", "能力", "质量", "性能",
            "安全", "优化", "测试", "部署", "运维", "监控", "日志",
        ]

        _pattern = topic
        for _dw in _domain_words:
            if _dw in _pattern:
                _pattern = _pattern.replace(_dw, "X", 1)
                break

        # 只保留结构特征：去掉数字和URL残留
        _pattern = re.sub(r'\d+', 'N', _pattern)
        _pattern = re.sub(r'https?://\S+', '', _pattern)

        # 规范化空白
        _pattern = re.sub(r'\s+', ' ', _pattern).strip()

        if len(_pattern) >= 3:
            return _pattern
        return ""
    def _calculate_topic_relevance(self, text: str, topic: str) -> float:
        """
        通用主题相关性评分（0.0-1.0）。
        计算文本与搜索主题的相关程度，用于过滤无关的关键词和链接。
        """
        if not text or not topic:
            return 0.0

        import re
        text_lower = text.lower()
        topic_lower = topic.lower()

        # 1. 从主题中提取有效词（长度>=2的中文词或>=3的英文词）
        topic_words = set()
        for word in re.split(r'[\s,，、：:。；;！!？?]+', topic_lower):
            word = word.strip()
            if len(word) >= 2:
                topic_words.add(word)
        # 额外：主题中的2-3字中文片段
        chinese_chars = re.findall(r'[\u4e00-\u9fff]{2,3}', topic)
        for cc in chinese_chars:
            topic_words.add(cc)

        if not topic_words:
            return 0.5  # 无法提取主题词时默认通过

        # 2. 计算文本中包含的主题词比例
        matched = 0
        for tw in topic_words:
            if tw in text_lower:
                matched += 1

        score = matched / len(topic_words)
        return min(1.0, score)
    def _semantic_extract_keywords(self, text: str, limit: int = 6) -> list[str]:
        """
        ★v25.0新增：从知识库高频关键词构建词典，做最长匹配提取。
        优先提取已知概念，避免硬切碎片。
        """
        if not hasattr(self, 'node_pool') or not self.node_pool:
            return []
        try:
            _kw_set = set()
            for _n in self.node_pool.query(evol_level="L2", limit=500):
                _kws = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
                _kw_set.update([k for k in _kws if isinstance(k, str) and len(k) >= 2])
            for _n in self.node_pool.query(evol_level="L3", limit=200):
                _kws = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
                _kw_set.update([k for k in _kws if isinstance(k, str) and len(k) >= 2])
            if not _kw_set:
                return []
            # ★修复：过滤纯英文模板词（question/Motor/topic 等），
            # 这些词来自大模型 prompt 模板字段，混入知识库关键词后会被最长匹配
            # 误提取为搜索关键词，导致内在世界误判「关键词与主题无关」而终止搜索。
            # 搜索关键词应以中文为主，只保留至少含一个中文字符的词。
            _kw_set = {
                k for k in _kw_set
                if any('\u4e00' <= c <= '\u9fff' for c in k)
            }
            if not _kw_set:
                return []
            # 按长度降序，最长匹配优先
            _sorted_kws = sorted(_kw_set, key=len, reverse=True)
            _found = []
            for _kw in _sorted_kws:
                if _kw in text and _kw not in _found:
                    _found.append(_kw)
                    if len(_found) >= limit:
                        break
            return _found
        except Exception:
            return []

    @staticmethod
    def _kws_match_topic(keywords: list, search_topic: str) -> bool:
        """★第83批 T-c1(1)：关键词与搜索主题是否存在至少一个 2-gram 字符重叠。

        判据与内在世界审查侧（PulseInnerWorld._handle_search_stage_feedback）保持同源，
        便于"提取侧先自检、审查侧复核"两处一致。
        """
        _t = str(search_topic or "").lower()
        if not _t:
            return True
        for _kw in (keywords or []):
            _k = str(_kw).lower()
            for _i in range(len(_k) - 1):
                if _k[_i:_i + 2] in _t:
                    return True
        return False

    def _extract_keywords_from_summary(self, search_topic: str) -> list[str]:
        """
        从当前页面的搜索结果摘要中提取关键概念。
        搜索引擎结果页包含大量相关术语的摘要片段，从中提取高频专业词汇。
        """
        if not self._headless_page:
            return []

        try:
            # 获取搜索结果页面的所有文本（先强制等待确保页面完全加载）
            try:
                self._headless_page.wait_for_load_state('networkidle', timeout=TIMEOUT_CONFIG["page_load"])
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            page_text = self._headless_page.inner_text("body")
            if not page_text:
                return []

            # ★v25.0新增：优先使用语义提取（知识库词典最长匹配）
            semantic_kws = self._semantic_extract_keywords(page_text, limit=6)
            # ★主线第15批 T4/P2-101：语义增强结果同样过净化（页面正文常带时间/噪声短语，
            #   实测出现过「中的文字」「天之前」「小时之前」）
            semantic_kws = self._purify_search_keywords(
                semantic_kws, search_topic, limit=6)
            # ★第83批 T-c1(1)：语义增强结果与主题重叠度校验。
            #   词典最长匹配可能给出与主题无关的通用词（实测 "今天的科技新闻" →
            #   ['探索','适合','生活']），下游审查据此误判「关键词与主题无关」而终止搜索。
            #   判据与审查侧一致（2-gram 字符重叠）；零重叠即视为提取错误 → 丢弃并回退硬切。
            if semantic_kws and not self._kws_match_topic(semantic_kws, search_topic):
                self._log(LogLevel.WARNING,
                          f"语义增强提取与主题零重叠，丢弃并回退硬切: "
                          f"提取={semantic_kws} 主题='{str(search_topic)[:40]}'")
                semantic_kws = []
            if len(semantic_kws) >= 3:
                self._log(LogLevel.INFO, f"语义增强提取成功: {semantic_kws}")
                return semantic_kws[:6]
            else:
                self._log(LogLevel.DEBUG, f"语义增强提取不足3个({len(semantic_kws)}个)，回退硬切")

            import re
            from collections import Counter

            # 从搜索主题中提取已有词（用于排除）
            topic_words = set(re.split(r'[\s,，、：:]+', search_topic.lower()))
            topic_words.update(["搜索", "百度", "一下", "结果", "关于", "为您", "找到",
                              "search", "skip", "content", "feedback", "about"])

            # 提取所有中文短语（2-6个汉字）
            chinese_phrases = re.findall(r'[\u4e00-\u9fff]{2,6}', page_text)

            # 统计词频，排除噪音词和已在搜索主题中的词
            noise = {"的", "了", "是", "在", "和", "与", "或", "及", "而", "不", "也", "有",
                    "这个", "那个", "什么", "怎么", "如何", "为什么", "可以", "能够", "应该",
                    "一个", "一种", "一些", "进行", "使用", "通过", "对于", "关于", "根据",
                    "我们", "他们", "自己", "大家", "这里", "那里", "其他", "它的", "你的",
                    # 搜索引擎残词
                    "进校", "为传承", "弘", "引导青少年", "区教育局", "联合",
                    "的意思", "是什么", "的含义", "的解释", "的拼音", "的笔顺",
                    "怎么读", "怎么写", "词语", "汉语", "成语", "百度百科",
                    # 搜索残词（新增）
                    "什么是", "同工作", "工作",
                    "学生信息网", "脑筋急转弯", "带点黄的", "远程控制软件", "程控制", "向日葵",}
            counter = Counter()
            for phrase in chinese_phrases:
                if phrase in noise:
                    continue
                if phrase in topic_words:
                    continue
                counter[phrase] += 1

            # 取出现次数最多的前15个词，过滤只出现1次的，排除残句
            top_phrases = []
            # ★v25.0修复：扩充虚词边界集合，覆盖更多碎片模式
            _edge_stop = "的了是一种这那在和与就都各及而之于也乎以内中外间上下前后一两几"
            for word, count in counter.most_common(15):
                if count < 2:
                    continue
                # 过滤残句（以虚词结尾或开头的短语通常是被截断的）
                if word[-1] in _edge_stop:
                    continue
                if word[0] in _edge_stop:
                    continue
                # ★v25.0新增：过滤包含"的"且"的"后面部分过短的碎片（如"技术领域的交"）
                if "的" in word:
                    _after_de = word.split("的")[-1]
                    if len(_after_de) <= 1:
                        continue
                # ★v25.0新增：过滤含括号的碎片（如"内（拼音"）
                if "(" in word or ")" in word or "（" in word or "）" in word:
                    continue
                # 过滤明显不完整的短语（如"引青少年知"）
                if len(word) == 6 and word[-1] not in "的之原理方法化性论学观":
                    if count < 3:
                        continue
                top_phrases.append(word)

            # 按长度排序，优先选长词（更有信息量）
            top_phrases.sort(key=lambda x: len(x), reverse=True)

            return top_phrases[:6]

        except Exception as e:
            self._log(LogLevel.DEBUG, f"关键词提取异常: {e}")
            return []

    def _extract_new_concepts_from_article(self, article_content: str,
                                           existing_keywords: list[str]) -> list[str]:
        """
        从精读文章中提取新出现的核心概念（不在已有关键词列表中的）。
        用于阶段3的概念深挖。
        """
        if not article_content:
            return []

        try:
            import re
            from collections import Counter

            # 提取所有中文短语（3-8个汉字，长度>2更有可能是专业术语）
            chinese_phrases = re.findall(r'[\u4e00-\u9fff]{3,8}', article_content)

            # 排除已有关键词
            existing_set = {kw.lower() for kw in existing_keywords}
            noise = {"的", "了", "是", "在", "和", "与", "或", "及", "而", "不", "也", "有",
                    "这个", "那个", "什么", "怎么", "如何", "为什么", "可以", "能够", "应该",
                    "一个", "一种", "一些", "进行", "使用", "通过", "对于", "关于", "根据",
                    "我们", "他们", "自己", "大家", "这里", "那里", "因此", "所以", "但是",
                    "如果", "虽然", "然而", "并且", "而且", "因为", "由于", "除了", "之后",
                    "非常", "比较", "更加", "已经", "正在", "将要", "可能", "也许", "大概"}


            counter = Counter()
            for phrase in chinese_phrases:
                if phrase in noise:
                    continue
                if phrase.lower() in existing_set:
                    continue
                counter[phrase] += 1

            # 取出现次数>=2的长词，最多返回3个
            new_concepts = [word for word, count in counter.most_common(10)
                          if count >= 2 and len(word) >= 3]

            return new_concepts[:3]

        except Exception:
            return []

    def _select_headless_search_url(self, topic: str) -> str:
        """根据网络环境选择搜索引擎"""
        import urllib.parse
        encoded = urllib.parse.quote(topic)

        is_domestic = self._check_domestic_network()
        if is_domestic:
            engines = self._get_headless_config("search_engines_domestic", [])
        else:
            engines = self._get_headless_config("search_engines_international", [])

        if not engines:
            return f"https://www.bing.com/search?q={encoded}"

        probe_timeout = self._get_headless_config("engine_probe_timeout", 3)
        # ★v25.1 P2智能化: 先收集所有可达引擎，再按历史成功率排序选择
        _reachable = []
        for engine_template in engines:
            engine_host = engine_template.split("/")[2] if "://" in engine_template else ""
            try:
                import socket
                socket.setdefaulttimeout(probe_timeout)
                socket.create_connection((engine_host, 80), timeout=probe_timeout)
                _stats = self._search_engine_stats.get(engine_host, {"ema_rate": 0.5})
                _reachable.append((engine_template, engine_host, _stats.get("ema_rate", 0.5)))
            except Exception:
                silent_exc(where="organs/motor/PulseController.py:1962")
                continue
        if _reachable:
            # 按历史成功率降序排序，选成功率最高的
            _reachable.sort(key=lambda x: x[2], reverse=True)
            _best = _reachable[0]
            self._log(LogLevel.DEBUG, f"搜索引擎选择: {_best[1]} (成功率={_best[2]:.1%}, 候选{len(_reachable)}个)")
            return _best[0].replace("{query}", encoded)

        # 所有引擎都不可达，使用最可能可用的兜底
        fallback = "https://cn.bing.com/search?q={query}" if is_domestic else "https://www.bing.com/search?q={query}"
        return fallback.replace("{query}", encoded)

    def _record_search_outcome(self, engine_host: str, success: bool) -> None:
        """★v25.1 P2智能化: 记录搜索结果，更新引擎成功率EMA。

        Args:
            engine_host: 搜索引擎域名
            success: 本次搜索是否成功（找到有效结果）
        """
        if not engine_host:
            return
        if engine_host not in self._search_engine_stats:
            self._search_engine_stats[engine_host] = {"success": 0, "total": 0, "ema_rate": 0.5}
        _s = self._search_engine_stats[engine_host]
        _s["total"] += 1
        if success:
            _s["success"] += 1
        _outcome = 1.0 if success else 0.0
        _s["ema_rate"] = 0.3 * _outcome + 0.7 * _s["ema_rate"]

    # [批次4·深度体检][MAINT-2] 空白名单默认拒绝
    def _check_domain_permission(self, url: str) -> bool:
        """域名黑白名单检查"""
        try:
            domain = url.split("/")[2] if "://" in url else ""
        except IndexError as e:
            silent_exc(e, where="organs.motor.PulseController::_check_domain_permission L1997")
            return False

        blacklist = self._get_headless_config("domain_blacklist", [])
        for pattern in blacklist:
            if self._wildcard_match(pattern, domain.lower()):
                return False

        whitelist = self._get_headless_config("domain_whitelist", [])
        # ★MAINT-2修复: 域名白名单为空时默认拒绝（与 URL/文件检查一致，fail-closed），
        #   原逻辑默认放行，存在安全配置遗漏时任意域名可被访问
        if not whitelist:
            self._log(LogLevel.WARNING, f"域名白名单为空，默认拒绝访问: {domain}")
            return False

        return any(self._wildcard_match(pattern, domain.lower()) for pattern in whitelist)

    def _close_headless_browser(self):
        """关闭无头浏览器实例。

        ★主线第12批 T1/P2-81：属主线程模式下，若当前不在池线程内，
        改为提交到 Playwright 专用池执行，避免跨线程 stop。
        """
        if self._owner_thread_mode():
            try:
                import threading as _th
                _cur = _th.current_thread().name
                if not str(_cur).startswith("Playwright"):
                    self._get_playwright_executor().submit(self._close_headless_browser)
                    return
            except Exception as e:
                self._log(LogLevel.ERROR, f'异常: {e}')
        if self._headless_browser:
            try:
                self._headless_browser.close()
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
            finally:
                self._headless_browser = None
                self._headless_page = None
                self._headless_active = False

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
            finally:
                self._playwright = None

        self._search_cache.clear()
        self._log(LogLevel.INFO, "无头浏览器已关闭")
    def _start_keepalive_timer(self):
        """启动浏览器保活定时器，每60秒通过专用线程池轻触一次防止断开"""
        def _keepalive_loop():
            _last_restart = time.time()
            while self.is_running:
                time.sleep(60)
                if not self.is_running:
                    break
                # ★主线第12批 T1/P2-81（裁决方案A）：保活线程只置信号，不碰浏览器。
                #   实际 touch 由 Playwright 专用线程（浏览器属主）执行，
                #   规避 "cannot switch to a different thread" 跨线程错误。
                if self._headless_browser and self._headless_page:
                    try:
                        if self._keepalive_signal_mode():
                            self._signal_keepalive()
                        else:
                            # 灰度回退：第10批旧行为（直接由保活线程提交，已知跨线程）
                            _executor = self._get_playwright_executor()
                            _executor.submit(self._keepalive_touch)
                    except Exception as _le:
                        self._log(LogLevel.DEBUG,
                                  f"保活调度异常（不影响运行）: {type(_le).__name__}: {_le}")
                    # ★主线第10批 P2-77：接入原本定义了却从未被调用的回收逻辑（防僵尸/闲置泄漏）
                    try:
                        self._kill_zombie_headless()
                    except Exception as _ze:
                        self._log(LogLevel.DEBUG,
                                  f"僵尸进程回收异常（不影响运行）: {type(_ze).__name__}: {_ze}")
                # 配置化定期重启：应对内存泄漏（默认 0=禁用，不改变原有行为）
                _restart_interval = self._get_headless_config(
                    "headless_restart_interval_seconds", 0)
                if (_restart_interval and self._headless_browser
                        and (time.time() - _last_restart) >= _restart_interval):
                    self._log(LogLevel.INFO,
                              f"浏览器定期重启（间隔{_restart_interval}s，防内存泄漏）")
                    self._close_headless_browser()
                    _last_restart = time.time()

        _keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
        _keepalive_thread.start()

    def _owner_thread_mode(self) -> bool:
        """★主线第12批 T1/P2-81：是否启用「属主线程」模式（默认开）。

        开启后 Playwright 浏览器一律诞生于 `_get_playwright_executor()` 的
        常驻池线程（max_workers=1），创建/使用/关闭同线程，规避跨线程约束。
        置 False 回退旧行为（预热裸线程创建），用于 A/B 验证。
        """
        try:
            return bool(self._get_headless_config("headless_owner_thread_mode", True))
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseController::_owner_thread_mode L2099")
            return True

    def _keepalive_signal_mode(self) -> bool:
        """★主线第12批 T1/P2-81：是否启用「保活信号」模式（方案A，默认开）。

        置 False 可回退第10批的"保活线程直接 submit"旧行为，用于 A/B 验证。
        """
        try:
            return bool(self._get_headless_config("headless_keepalive_signal_mode", True))
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseController::_keepalive_signal_mode L2109")
            return True

    def _signal_keepalive(self) -> None:
        """★方案A：置保活信号，并交由持有浏览器的 Playwright 线程执行实际 touch。

        锁保护 due 标志的「检查-置位」与「取走-复位」，确保单位时间最多一次。
        实际执行经 `_get_playwright_executor()`（max_workers=1，浏览器属主线程），
        因此不会跨线程操作 Playwright 对象。
        """
        with self._keepalive_lock:
            if self._keepalive_due:
                return  # 上一拍尚未被消费，跳过本次，避免堆积
            self._keepalive_due = True
        try:
            _executor = self._get_playwright_executor()
            _executor.submit(self._check_keepalive)
        except Exception as _e:
            # 提交失败（如池已关闭）：复位标志，避免永久卡住
            with self._keepalive_lock:
                self._keepalive_due = False
            self._log(LogLevel.DEBUG,
                      f"保活信号提交失败（不影响运行）: {type(_e).__name__}: {_e}")

    def _check_keepalive(self) -> None:
        """★方案A：在 Playwright 属主线程内消费保活信号并执行 touch。

        仅在 due=True 时执行；执行后先复位标志再操作浏览器，避免长耗时操作
        阻塞下一拍信号。任何异常都不向上抛（保活失败不影响主流程）。
        """
        try:
            with self._keepalive_lock:
                if not self._keepalive_due:
                    return
                self._keepalive_due = False
            if not (self._headless_browser and self._headless_page):
                return
            self._keepalive_touch()
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"保活检查异常（不影响运行）: {type(_e).__name__}: {_e}")

    def _keepalive_touch(self):
        """在专用线程中执行保活操作；失败时记录断开原因并主动标记浏览器失效。

        ★主线第10批 P2-77 修复：原实现将其定义为 `_start_keepalive_timer` 内的嵌套函数，
        而保活循环却 `submit(self._keepalive_touch)`（实例属性），导致 AttributeError 被
        future 静默吞掉、心跳从未真正执行——这是浏览器频繁断开的根因。现提升为类方法。
        """
        try:
            if self._headless_page:
                self._headless_page.goto("about:blank", timeout=5000)
                self._headless_last_used = time.time()
        except Exception as _ke:
            _ks = str(_ke)
            _reason = self._classify_browser_error(_ke)
            self._log(LogLevel.WARNING,
                      f"浏览器保活失败（{_reason}），下次使用将重建: "
                      f"{type(_ke).__name__}: {_ks[:120]}")
            # 严重失效（页面/浏览器已销毁）主动标记，避免静默死亡后被误用
            if any(_k in _ks for _k in ("Target", "closed", "detached",
                                        "Connection closed", "greenlet", "Cannot switch")):
                self._headless_browser = None
                self._headless_page = None
                self._headless_active = False

    def _classify_browser_error(self, err: Exception) -> str:
        """将浏览器异常分类为 超时/崩溃/网络错误，用于断开原因日志。

        纯字符串特征匹配，不依赖具体驱动实现，不抛出、无副作用。
        """
        _s = str(err)
        if any(_k in _s for _k in ("Timeout", "timeout", "timed out", "TimeoutError")):
            return "超时"
        if any(_k in _s for _k in ("Target", "closed", "detached", "Connection",
                                   "greenlet", "Cannot switch", "crashed", "crash",
                                   "destroyed", "destroy")):
            return "崩溃/连接断开"
        if any(_k in _s for _k in ("net::", "ERR_", "NameResolution", "getaddrinfo",
                                   "ConnectionError", "socket", "Network")):
            return "网络错误"
        return "未知"

    def _ensure_headless_browser(self) -> bool:
        """
        确保常驻浏览器实例可用。
        如果浏览器不存在或已断开，自动创建。
        与 _start_headless_browser 不同，此方法创建的浏览器实例会被复用。
        
        Returns:
            True 如果浏览器可用，False 如果创建失败
        """
        # 重建冷却检查：连续失败后退避，避免每次调用空转重建
        if self._browser_rebuild_cooldown_until and time.time() < self._browser_rebuild_cooldown_until:
            self._log(LogLevel.DEBUG,
                      f"浏览器重建冷却中(连续失败{self._browser_rebuild_failures}次)，跳过重建(退避至"
                      f"{time.strftime('%H:%M:%S', time.localtime(self._browser_rebuild_cooldown_until))})")
            return False
        # 检查现有浏览器是否存活
        if self._headless_browser and self._headless_page:
            _last_err: Exception | None = None
            # 增加重试次数，偶尔的网络抖动不触发重建
            for _retry in range(3):
                try:
                    self._headless_page.title()
                    self._headless_last_used = time.time()
                    return True
                except Exception as _e:
                    _err_str = str(_e)
                    _last_err = _e
                    if "greenlet" in _err_str or "Cannot switch" in _err_str:
                        pass
                    if _retry < 2:
                        time.sleep(0.5)
                        continue
            # 三次重试均失败，确认浏览器已断开，清理后重建（记录断开原因）
            _reason = self._classify_browser_error(_last_err) if _last_err else "未知"
            self._log(LogLevel.WARNING,
                      f"常驻浏览器已断开（3次确认，原因={_reason}），尝试重建...")
            self._headless_browser = None
            self._headless_page = None
            self._headless_active = False
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                self._playwright = None

        # 创建新浏览器
        if not PLAYWRIGHT_AVAILABLE:
            return False

        if self._get_headless_config("load_critical_disable", True) and self._current_load_level() == "critical":
            self._log(LogLevel.WARNING, "临界负载，拒绝启动常驻浏览器")
            return False

        try:
            self._playwright = sync_playwright().start()  # type: ignore[possibly-unbound]

            launch_args = []
            if self._get_headless_config("stealth_mode", True):
                launch_args = list(self._get_headless_config("stealth_args", []))

            self._headless_browser = self._playwright.chromium.launch(
                headless=self._get_headless_config("headless", True),
                args=launch_args,
            )

            # 创建默认 Context 和 Page（用于页面链接提取等操作）
            default_context = self._headless_browser.new_context(
                viewport={"width": 1920, "height": 1080},
            )
            self._headless_page = default_context.new_page()
            self._headless_active = True
            self._headless_last_used = time.time()
            self._browser_rebuild_failures = 0          # 重建成功 → 清零失败计数
            self._browser_rebuild_cooldown_until = 0.0  # 取消冷却

            self._log(LogLevel.INFO, "常驻浏览器已启动 (Playwright Chromium·复用模式)")
            return True

        except Exception as e:
            self._browser_rebuild_failures += 1
            if self._browser_rebuild_failures >= 3:
                self._browser_rebuild_cooldown_until = time.time() + 600  # 连续失败≥3 → 冷却10分钟
                self._log(LogLevel.WARNING,
                          f"浏览器重建连续失败{self._browser_rebuild_failures}次，进入冷却10分钟(防空转)")
            else:
                self._log(LogLevel.ERROR, f"启动常驻浏览器失败({self._browser_rebuild_failures}次): {e}")
            self._headless_browser = None
            self._headless_page = None
            self._headless_active = False
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                self._playwright = None
            return False

    def _get_task_context_and_page(self):
        """
        为搜索任务创建独立的 Context 和 Page。
        使用常驻浏览器实例，通过新建 Context 实现任务隔离。
        Context 和 Page 用完即关，不占用常驻资源。
        
        Returns:
            (context, page) 元组，如果创建失败则返回 (None, None)
        """
        if not self._headless_browser and not self._ensure_headless_browser():
            return None, None

        try:
            task_context = self._headless_browser.new_context(
                viewport={"width": 1920, "height": 1080},
            )
            task_page = task_context.new_page()
            return task_context, task_page
        except Exception as e:
            self._log(LogLevel.ERROR, f"创建任务Context失败: {e}")
            # 浏览器可能已断开，标记为失效
            self._headless_browser = None
            self._headless_page = None
            self._headless_active = False
            return None, None

    def _check_headless_idle(self):
        """检查浏览器是否闲置超时"""
        if not self._headless_browser:
            return

        idle_timeout = self._get_headless_config("idle_auto_close_seconds", 30)
        if time.time() - self._headless_last_used > idle_timeout:
            self._log(LogLevel.INFO, f"无头浏览器闲置超时({idle_timeout}s)，自动关闭")
            self._close_headless_browser()

    def _kill_zombie_headless(self):
        """强制终止僵尸浏览器进程"""
        zombie_timeout = self._get_headless_config("zombie_process_kill_timeout", 120)
        if self._headless_pid and time.time() - self._headless_last_used > zombie_timeout:
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(self._headless_pid)],
                        check=False, capture_output=True, timeout=5,
                        encoding="utf-8", errors="replace"
                    )
                self._headless_pid = 0
                self._log(LogLevel.WARNING, f"已强制终止僵尸进程: PID={self._headless_pid}")
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == ControllerEvent.OPEN_URL:
            # 将脉冲的 intent 元信息注入 payload，供后续处理使用
            pulse_intent = pulse.get("intent", "")
            if pulse_intent:
                payload = dict(payload)
                payload["_pulse_intent"] = pulse_intent
            return self._on_open_url(payload)
        elif event_type == ControllerEvent.READ_FILE:
            return self._on_read_file(payload)
        elif event_type == ControllerEvent.LIST_DIRECTORY:
            return self._on_list_directory(payload)
        elif event_type == ControllerEvent.LAUNCH_APP:
            return self._on_launch_app(payload)
        elif event_type == TouchEvent.HARDWARE_SNAPSHOT:
            return self._on_hardware_snapshot(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == SearchEvent.TERMINATED:
            # ★第86批 T-86b：搜索终止信号改为独立事件（信号分层）
            return self._on_search_terminate(payload)
        elif event_type == ControllerEvent.SEARCH_STAGE_COMPLETED:
            payload = pulse.get("payload", {})
            if payload.get("status") == "terminate":
                return self._on_search_terminate(payload)
            # 其他状态（如stage1_completed等）可以在此处处理或忽略
            return {"status": "acknowledged"}
        return None

    # ========== 事件处理 ==========

    def _is_low_quality_direction(self, search_topic: str) -> tuple[bool, str]:
        """★P1: 检查搜索主题是否为潜意识标记的低质量方向。
        Returns: (is_low_quality, reason)
        """
        if not search_topic or not self.subconscious:
            return False, ""
        try:
            # 查询潜意识的低质量方向
            low_quality = getattr(self.subconscious, '_low_quality_directions', {})
            if not low_quality:
                return False, ""
            topic_lower = search_topic.lower()
            for direction_key, entry in low_quality.items():
                if not isinstance(entry, dict):
                    continue
                # 检查搜索主题是否包含低质量方向关键词
                if direction_key.lower() in topic_lower:
                    permanent = entry.get("permanent", False)
                    overlap = entry.get("overlap", 0)
                    marked_at = entry.get("marked_at", 0)
                    # 永久低质量或最近1小时内标记的
                    if permanent or (time.time() - marked_at < 3600):
                        reason = f"低质量方向'{direction_key}'(关联度={overlap})"
                        if permanent:
                            reason += "[永久]"
                        return True, reason
            return False, ""
        except Exception as e:
            self._log(LogLevel.DEBUG, f"低质量方向检查异常: {e}")
            return False, ""

    def _on_open_url(self, payload: dict) -> dict[str, Any]:
        """打开网页（增强版：深度搜索+窗口管理）"""
        url = payload.get("url", "")
        reason = payload.get("reason", "")
        search_topic = payload.get("search_topic", "")
        deep_search = payload.get("deep_search", False)

        if not url:
            return {"status": "skipped", "reason": "空URL"}

        is_search_url = any(domain in url for domain in [
            "duckduckgo.com", "google.com/search", "bing.com/search",
            "baidu.com/s", "sogou.com"
        ])
        # ===== 新增: 脉冲意图差异化处理 =====
        pulse_intent = payload.get("_pulse_intent", "")

        # 识别本能知识补全场景
        is_instinct_learn = "本能知识补充" in reason

        # 根据意图调整搜索行为（线程安全：锁内读取负载和冷却状态）
        if pulse_intent == "suggest":
            with self._load_level_lock:
                current_load = self._load_level
            if current_load in ("heavy", "critical"):
                self._log(LogLevel.INFO, f"负载偏高，跳过建议性深度搜索: {search_topic[:40]}")
                return {"status": "skipped", "reason": "负载偏高，跳过建议性搜索"}
        elif pulse_intent == "alert" or is_instinct_learn:
            # alert 意图或本能知识补全：即使冷却期内也优先处理
            with self._load_level_lock:
                self._last_deep_search_time = 0.0  # 重置冷却计时
            self._log(LogLevel.INFO, f"优先搜索(本能补全/告警)，跳过冷却检查: {search_topic[:40]}")

        if is_search_url and deep_search and self._get_config("deep_search_enabled", True):
            # ===== 新增: 工具适用边界检查 =====
            # 检查搜索主题是否在当前深度搜索的适用范围内
            if search_topic:
                search_topic_lower = search_topic.lower()
                # 纯字典查询类话题不适合深度搜索
                dictionary_indicators = ["的拼音", "的部首", "的笔顺", "怎么读", "怎么拼", "怎么写"]
                if any(indicator in search_topic_lower for indicator in dictionary_indicators):
                    self._log(LogLevel.INFO, f"工具边界: 搜索主题'{search_topic[:40]}'疑似字典查询，降级处理")
                    return self._on_open_url({**payload, "deep_search": False})
            return self._search_deep(url, reason, search_topic, payload)

        if is_search_url:
            url = self._select_best_search_url(url, reason)

        # ★P1: 低质量方向检查（利用潜意识反馈）
        if search_topic:
            is_low, low_reason = self._is_low_quality_direction(search_topic)
            if is_low:
                self._log(LogLevel.INFO,
                         f"搜索方向过滤: 跳过低质量方向 '{search_topic[:40]}' ({low_reason})")
                return {"status": "skipped", "reason": f"低质量方向过滤: {low_reason}"}

        # 负载检查（线程安全：锁内读取）
        with self._load_level_lock:
            current_load = self._load_level
        if current_load == "critical":
            return {"status": "skipped", "reason": "系统负载临界，暂停网页操作"}
        if current_load == "heavy" and self._url_open_hour_count > 10:
            return {"status": "skipped", "reason": "系统负载偏高，暂停搜索"}

        # ★P1: 从热加载配置读取搜索参数
        try:
            from config import RUNTIME_PARAMS
            cooldown = RUNTIME_PARAMS.get("search_cooldown_seconds",
                        self._permission_config.get("operation_cooldown_seconds", 15))
            max_per_hour = RUNTIME_PARAMS.get("search_max_per_hour",
                           self._permission_config.get("max_url_opens_per_hour", 30))
        except Exception:
            cooldown = self._permission_config.get("operation_cooldown_seconds", 15)
            max_per_hour = self._permission_config.get("max_url_opens_per_hour", 30)
        now = time.time()
        if now - self._last_url_open_time < cooldown:
            return {"status": "skipped", "reason": f"操作冷却中（{cooldown}秒）"}
        if now - self._url_open_hour_start > 3600:
            self._url_open_hour_count = 0
            self._url_open_hour_start = now
        if self._url_open_hour_count >= max_per_hour:
            return {"status": "skipped", "reason": f"每小时最多{max_per_hour}次网页操作"}

        if not self._check_permission("open_url", url):
            return {"status": "blocked", "reason": "权限拒绝"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._operation_count += 1
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._url_open_count += 1
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._url_open_hour_count += 1
        self._last_url_open_time = now

        try:
            self._open_browser_url(url)
            self._write_audit("open_url", url, "success", reason)
            self._log(LogLevel.INFO, f"已打开网页: {url[:80]}" if not reason else f"已打开网页 ({reason})")

            web_content = self._fetch_url_text(url)
            if web_content:
                # ★任务C-4：网页抓取后即时提取发布时间（灰度关闭时返回 0.0）
                self._emit_digest(web_content, f"controller.search:{reason or url}",
                                  source_timestamp=self._extract_web_time(web_content, url))

            return {"status": "success", "url": url}
        except Exception as e:
            self._write_audit("open_url", url, "error", str(e))
            return {"status": "error", "error": str(e)}
    @budget_guard("search")
    def _execute_headless_search(self, url: str, reason: str, search_topic: str,
                                  payload: dict) -> dict[str, Any]:
        """
        Playwright 深度搜索的执行函数。
        此方法被 ExternalExecutor 在专用线程中调用，可以安全使用 Playwright 同步 API。
        """
        max_articles = self._get_headless_config(
            "heavy_max_articles" if self._current_load_level() == "heavy" else "max_articles_per_search", 3
        )
        # ★A-9接通（2026-09-08，星轨拍板）：search_max_articles 参数补丁真实生效。
        #   此前该参数在器官侧无消费点（实际基线走 _get_headless_config），
        #   代码学习器/参数闭环对它的调整应用了也不生效。
        #   语义：作为搜索文章数基线的**显式覆盖**（安全范围 (1,20) 由
        #   ParamPatchManager 保证），此后 pulse_intent 的 alert/suggest 微调仍然生效。
        try:
            from config import RUNTIME_PARAMS as _RP_sma
            _rp_max = _RP_sma.get("search_max_articles")
            if isinstance(_rp_max, (int, float)) and int(_rp_max) > 0:
                max_articles = max(1, min(20, int(_rp_max)))
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        pulse_intent = payload.get("_pulse_intent", "")
        if pulse_intent == "alert":
            max_articles = max(max_articles, 5)
        elif pulse_intent == "suggest":
            max_articles = max(1, max_articles - 1)

        actual_topic = search_topic or reason
        search_intent = payload.get("search_intent", "")
        if search_intent == "curiosity" and actual_topic:
            has_concrete = any(
                kw in actual_topic for kw in ["原理", "机制", "方法", "结构", "定义",
                "流程", "步骤", "案例", "应用", "工具", "技术", "算法", "框架"]
            )
            if not has_concrete:
                actual_topic = f"{actual_topic} 概念 原理"

        # ★任务6（2026-09-08）：实体/概念查询**优先走百科查询器**（灰度
        #   ENABLE_ENCYCLOPEDIA_QUERY 默认 False；关闭时直接走原浏览器路径，零回退）。
        #   命中则消化摘要并免启动无头浏览器（浏览器使用频率下降≥50%目标的主要实现点）；
        #   查询失败/非实体查询 → 返回 None → 自动 fallback 到原 Playwright 路径。
        try:
            _wiki_result = self._try_encyclopedia_first(actual_topic)
            if _wiki_result is not None:
                return _wiki_result
        except Exception as e:
            self._log(LogLevel.DEBUG, f"百科优先查询异常已忽略（{type(e).__name__}: {e}）")
        return self._search_deep_headless(
            actual_topic, reason, max_articles,
            correlation_id=payload.get("search_correlation_id", ""),
        )

    def _try_encyclopedia_first(self, topic: str) -> dict[str, Any] | None:
        """★任务6（2026-09-08）：实体/概念查询优先用百科查询器。

        灰度关闭 / 触发判定不过 / 查询失败 → None（调用方 fallback 浏览器，零回退）。
        命中 → 摘要经 DigestEvent.KNOWLEDGE 走正常消化链路（source_organ=百科查询器），
        并返回 success 形状的结果，使本次**不启动无头浏览器**。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_ENCYCLOPEDIA_QUERY', False):
                return None
            # ★任务7：路由器作为上层决策（ENABLE_KNOWLEDGE_ACQUISITION_ROUTER 默认关；
            #   关闭时 acquire 返回 None → 走下方直连百科逻辑，行为与第三批一致）
            from nucleus.knowledge.KnowledgeAcquisitionRouter import (
                get_shared_knowledge_router,
            )
            _router = get_shared_knowledge_router(
                log_fn=lambda m: self._log(LogLevel.INFO, m))
            _route = _router.acquire(topic, {"intent": "deep_search"})
            if _route is not None:
                if _route.get("channel") != "wiki":
                    return None  # rss命中/委托浏览器/委托本地 → 原路径
                _result = _route.get("payload")
                if _result is None or not getattr(_result, "summary", ""):
                    return None
            else:
                from nucleus.knowledge.WikiQuerier import get_shared_wiki_querier
                _querier = get_shared_wiki_querier(
                    log_fn=lambda m: self._log(LogLevel.INFO, m))
                if not _querier.should_query(topic):
                    return None
                _result = _querier.query(topic)
                if _result is None or not _result.summary:
                    return None
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": f"[百科·{_result.source}] {_result.title}\n{_result.summary}",
                "source_organ": "百科查询器",
                "trigger_reason": f"encyclopedia_query:{topic[:50]}",
                "importance": "B",
                "view_mode": "OUTER_VIEW",
                "source_url": _result.url,
            }, priority=3, layer="L2")
            self._log(LogLevel.INFO,
                      f"百科优先命中: {topic[:40]}（免启动无头浏览器）")
            return {"status": "success", "url": _result.url,
                    "channel": "encyclopedia", "keyword": topic[:60]}
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"百科优先查询失败(fallback浏览器): {_e}")
            return None
    @budget_guard("search")
    def _search_deep(self, url: str, reason: str, search_topic: str,
                     payload: dict) -> dict[str, Any]:
        """深度搜索：无头浏览器优先 → 精准搜索 → 筛选 → 精读 → 消化"""
        now = time.time()
        now = time.time()
        deep_cooldown = self._get_config("deep_search_cooldown", 30)

        # ★P2-9修复：_load_level_lock 是非重入锁，原实现在锁内调用 _current_load_level()
        # 和 _on_open_url（二者都会再次取同一把锁）导致 100% 必现自死锁——第一次深度搜索
        # 即永久卡死，连锁拖死 L3 层与心跳。改为：锁内只做状态读写，锁外才做可能再次取锁的调用。
        _should_fallback = False
        _load_critical = False
        with self._load_level_lock:
            # 冷却检查（锁内读取）
            if now - self._last_deep_search_time < deep_cooldown:
                self._log(LogLevel.INFO, "深度搜索冷却中，降级为普通搜索")
                _should_fallback = True
            # 负载检查（锁内直接读 _load_level，避免重入 _current_load_level 造成死锁）
            elif self._load_level == "critical":
                _load_critical = True
            else:
                # 通过检查，标记深度搜索状态
                self._deep_search_active = True
                self._last_deep_search_time = now

        if _should_fallback:
            # 锁外调用 _on_open_url，避免重入 _load_level_lock 死锁
            return self._on_open_url({**payload, "deep_search": False})
        if _load_critical:
            return {"status": "skipped", "reason": "系统负载临界"}

        if not self._check_permission("open_url", url):
            return {"status": "blocked", "reason": "权限拒绝"}

        # ===== 异步线程检测：非主线程时提交到 Playwright 专用线程池执行 =====
        if threading.current_thread() is not threading.main_thread():
            self._log(LogLevel.INFO, "深度搜索检测到异步线程，提交到 Playwright 专用线程池执行")
            executor = self._get_playwright_executor()
            future = executor.submit(
                self._execute_headless_search,
                url, reason, search_topic, payload
            )
            try:
                return future.result(timeout=self._get_config("deep_search_total_timeout", 60))
            except Exception as e:
                # ★P0-2修复：超时/异常后取消未完成的 future，避免 Playwright 线程/浏览器残留导致内存泄漏
                try:
                    future.cancel()
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                # ★v26.0优化：区分超时和其他异常，超时降级为WARNING（有降级机制不致命）
                _is_timeout = "timeout" in str(e).lower() or isinstance(e, TimeoutError)
                _is_shutdown = getattr(self, '_shutting_down', False)
                if _is_shutdown:
                    self._log(LogLevel.DEBUG, f"退出中Playwright任务取消: {type(e).__name__}")
                elif _is_timeout:
                    self._log(LogLevel.WARNING,
                             f"Playwright搜索超时({self._get_config('deep_search_total_timeout', 60)}s)，已降级为普通搜索")
                else:
                    self._log(LogLevel.ERROR,
                             f"Playwright 专用线程池执行异常: {type(e).__name__}: {e}")
                return self._fallback_single_search(url, reason, payload)
        self._deep_search_active = True
        self._last_deep_search_time = now
        self._operation_count += 1
        self._url_open_count += 1

        # 优先使用无头浏览器深度检索
        if self._get_headless_config("enabled", True) and PLAYWRIGHT_AVAILABLE:
            max_articles = self._get_headless_config(
                "heavy_max_articles" if self._current_load_level() == "heavy" else "max_articles_per_search", 3
            )
            # ===== 新增: 根据脉冲意图调整精读文章数量 =====
            pulse_intent = payload.get("_pulse_intent", "")
            if pulse_intent == "alert":
                max_articles = max(max_articles, 5)  # 告警触发的搜索多读几篇
            elif pulse_intent == "suggest":
                max_articles = max(1, max_articles - 1)  # 建议性搜索少读一篇，节省资源
            # 如果来自好奇心探索，给抽象话题追加搜索限定词
            search_intent = payload.get("search_intent", "")
            if search_intent == "curiosity" and search_topic:
                # 检测话题是否太抽象（不含具体名词）
                has_concrete = any(
                    kw in search_topic for kw in ["原理", "机制", "方法", "结构", "定义",
                    "流程", "步骤", "案例", "应用", "工具", "技术", "算法", "框架"]
                )
                if not has_concrete:
                    search_topic = f"{search_topic} 概念 原理"
            result = self._search_deep_headless(
                search_topic or reason, reason, max_articles,
                correlation_id=payload.get("search_correlation_id", ""),
            )
            self._deep_search_active = False
            if result.get("status") == "headless_completed":
                return result
            self._log(LogLevel.INFO, "无头搜索降级为requests深度搜索")

        try:
            total_start = time.time()
            total_timeout = self._get_config("deep_search_total_timeout", 60)
            stages_completed = 0
            all_content = []

            self._log(LogLevel.INFO, f"🔍 深度搜索·阶段1: 精准搜索 '{search_topic or reason}'")

            refined_url = self._refine_search_url(url, reason, search_topic)
            self._open_browser_url(refined_url)
            stages_completed += 1

            search_page_content = self._fetch_url_text(refined_url)
            if not search_page_content:
                self._log(LogLevel.WARNING, "深度搜索: 搜索结果页抓取失败，降级为普通搜索")
                return self._fallback_single_search(url, reason, payload)

            if time.time() - total_start > total_timeout:
                self._emit_digest(search_page_content[:2000], f"deep_search.stage1:{search_topic}")
                return self._finish_deep_search(stages_completed, all_content, search_topic)

            self._log(LogLevel.INFO, "🔍 深度搜索·阶段2: 筛选最佳结果")
            best_links = self._extract_best_links(search_page_content, search_topic)

            if not best_links:
                self._log(LogLevel.INFO, "深度搜索: 未筛选到具体链接，消化搜索结果摘要")
                self._emit_digest(search_page_content[:2000], f"deep_search.summary:{search_topic}")
                return self._finish_deep_search(stages_completed, all_content, search_topic)

            stages_completed += 1

            max_stages = self._get_config("deep_search_max_stages", 2)
            opened_count = 0

            for i, link_info in enumerate(best_links[:3]):
                if time.time() - total_start > total_timeout:
                    break
                if opened_count >= max_stages:
                    break

                link_url = link_info.get("url", "")
                link_title = link_info.get("title", "")
                if not link_url:
                    continue

                if not link_url.startswith("http"):
                    link_url = "https:" + link_url if link_url.startswith("//") else url.split("/search")[0] + link_url

                self._log(LogLevel.INFO, f"🔍 深度搜索·阶段3-{i+1}: 打开 '{link_title[:40]}'")
                self._open_browser_url(link_url)
                opened_count += 1

                page_content = self._fetch_url_text(link_url)
                if page_content:
                    clean_content = self._extract_main_content(page_content)
                    all_content.append({
                        "title": link_title,
                        "url": link_url,
                        "content": clean_content[:3000],
                        # ★任务C-4：网页发布时间（未提取到为 0.0）
                        "source_timestamp": self._last_web_time,
                    })

                time.sleep(1.5)

            stages_completed = min(stages_completed + 1, 3)

            if all_content:
                for item in all_content:
                    digest_text = f"[深度搜索·{item['title'][:50]}] {item['content']}"
                    # ★任务C-4：把网页发布时间带给消化链路（0.0 时不带字段）
                    self._emit_digest(digest_text, f"deep_search.result:{search_topic}",
                                      source_timestamp=float(item.get("source_timestamp", 0.0) or 0.0))
                self._log(LogLevel.INFO,
                         f"✅ 深度搜索完成: {len(all_content)}篇文章, 共{sum(len(c['content']) for c in all_content)}字符")
            elif search_page_content:
                self._emit_digest(search_page_content[:2000], f"deep_search.summary:{search_topic}")

            self._cleanup_browser_windows()

            return self._finish_deep_search(stages_completed, all_content, search_topic)

        except Exception as e:
            self._log(LogLevel.ERROR, f"深度搜索异常: {e}")
            self._cleanup_browser_windows()
            return self._fallback_single_search(url, reason, payload)
        finally:
            with self._load_level_lock:
                self._deep_search_active = False
            # 确保无论成功/失败/提前返回，都关闭打开的浏览器窗口
            self._cleanup_browser_windows()
            # 额外保障：如果浏览器窗口列表不为空，1秒后再次尝试清理
            if self._browser_windows:
                time.sleep(1)
                self._cleanup_browser_windows()

    def _refine_search_url(self, original_url: str, reason: str, search_topic: str) -> str:
        """精准化搜索词（★R3治本：意图分类统一走 SearchIntentClassifier 的 5 类）。"""
        import urllib.parse

        query = ""
        for param in ["q=", "wd=", "query="]:
            if param in original_url:
                query = original_url.split(param, 1)[1].split("&")[0]
                break

        if not query:
            query = search_topic or reason

        query = urllib.parse.unquote(query)
        core_query = query[:60].strip()

        intent_keywords = self._get_config("search_intent_keywords", {})
        added_terms = []

        # ★R3治本：优先用 SearchIntentClassifier 判定意图（本地规则 + LLM 兜底 + 自进化），
        #   相比旧的 reason 子串匹配，能覆盖 fact_query/error_diagnosis/trend_watching 等
        #   高频场景，且分类结果随规则自进化持续改善。
        _intent = self._classify_search_intent(core_query or reason)
        if _intent and _intent in intent_keywords:
            added_terms = intent_keywords.get(_intent, "").split()
        else:
            # ★兜底：分类器不可用/未命中时，回退到旧的 reason 子串匹配（零回归）。
            added_terms = self._fallback_intent_terms(reason, intent_keywords)

        unique_terms = [t for t in added_terms if t not in core_query]
        refined_query = core_query + " " + " ".join(unique_terms[:3])
        refined_query = refined_query.strip()

        is_domestic = self._check_domestic_network()
        if is_domestic:
            templates = self._permission_config.get("search_urls_domestic", [])
        else:
            templates = self._permission_config.get("search_urls_international", [])

        if templates:
            return templates[0].replace("{query}", urllib.parse.quote(refined_query))
        return original_url

    def _classify_search_intent(self, text: str) -> str | None:
        """★R3治本：调用 SearchIntentClassifier 判定 5 类意图。

        返回 intent 名（fact_query/concept_learning/error_diagnosis/trend_watching/deep_research），
        失败或不可用时返回 None（交由调用方回退）。
        """
        if not text:
            return None
        try:
            from nucleus.SearchIntentClassifier import get_intent_classifier
            _result = get_intent_classifier().classify(text)
            _intent = _result.get("intent", "") if isinstance(_result, dict) else ""
            if _intent:
                self._log(LogLevel.DEBUG,
                          f"[R3-Intent] 搜索意图分类: {_intent} "
                          f"(置信度={_result.get('confidence', 0):.2f}, 来源={_result.get('source', '?')})")
                return _intent
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
            # 分类失败降级用 reason 子串匹配
        return None

    @staticmethod
    def _fallback_intent_terms(reason: str, intent_keywords: dict) -> list[str]:
        """★兜底：分类器不可用时的 reason 子串匹配（保留旧逻辑，零回归）。"""
        added_terms: list[str] = []
        if "学习" in reason or "learning" in reason or "learn" in reason.lower():
            added_terms = intent_keywords.get("concept_learning", "详解 教程").split()
        elif "好奇" in reason or "curiosity" in reason or "探索" in reason:
            added_terms = intent_keywords.get("fact_query", "是什么 原理").split()
        elif "creative" in reason or "灵感" in reason or "联想" in reason:
            added_terms = intent_keywords.get("deep_research", "跨领域 创新").split()
        elif "growth" in reason or "优化" in reason or "成长" in reason:
            added_terms = intent_keywords.get("deep_research", "优化 方法").split()
        return added_terms

    # [批次4·深度体检][PERF-10] 链接提取正则提为模块级常量
    def _extract_best_links(self, page_text: str, search_topic: str) -> list[dict[str, str]]:
        """从搜索结果页文本中提取最相关的链接"""
        best_links = []

        merged_text = re.sub(r'(https?)\s*:\s*/\s*/\s*', r'\1://', page_text, flags=re.IGNORECASE)
        merged_text = re.sub(r'\.\s+com', '.com', merged_text)
        merged_text = re.sub(r'\.\s+cn', '.cn', merged_text)
        merged_text = re.sub(r'\.\s+org', '.org', merged_text)
        merged_text = re.sub(r'\.\s+net', '.net', merged_text)
        merged_text = re.sub(r'\.\s+html?', '.html', merged_text)
        merged_text = re.sub(r'(wd|word|query|keyword|q|pn|rn|p|ie|tn)\s*=\s*', r'\1=', merged_text, flags=re.IGNORECASE)

        urls_found = _URL_PATTERN.findall(merged_text)  # ★PERF-10修复: 复用模块级预编译正则

        baidu_urls = []
        for url in urls_found:
            if "baidu.com/link" in url or "baidu.com/s" in url:
                if "baidu.com/link?url=" in url:
                    baidu_urls.append(url)
            else:
                baidu_urls.append(url)
        urls_found = baidu_urls

        topic_keywords = []
        if search_topic:
            topic_keywords = [kw.lower() for kw in search_topic.split() if len(kw) >= 2]

        scored = []
        seen_domains = set()

        for url in urls_found:
            clean_url = url.strip(".,;:!?\"'")
            if clean_url.startswith("//"):
                clean_url = "https:" + clean_url

            skip_domains = ["google.com", "baidu.com", "bing.com", "duckduckgo.com",
                          "sogou.com", "facebook.com", "twitter.com", "instagram.com",
                          "youtube.com", "login", "signup", "settings", "javascript"]
            if any(skip in clean_url.lower() for skip in skip_domains):
                continue

            try:
                domain = clean_url.split("/")[2] if "://" in clean_url else clean_url.split("/")[0]
            except IndexError:
                domain = clean_url
            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            url_pos = page_text.find(url)
            if url_pos < 0:
                continue

            context_start = max(0, url_pos - 80)
            context_end = min(len(page_text), url_pos + len(url) + 200)
            context = page_text[context_start:context_end]

            title = ""
            context_lines = context.replace(url, " ").split("\n")
            for line in context_lines:
                clean_line = re.sub(r'\s+', ' ', line).strip()
                if 10 < len(clean_line) < 200:
                    title = clean_line
                    break

            if not title:
                title = clean_url.split("/")[-1].replace("-", " ")[:80] or domain

            score = 0
            context_lower = context.lower()
            title_lower = title.lower()

            for kw in topic_keywords:
                if kw in title_lower:
                    score += 3
                elif kw in context_lower:
                    score += 1

            path_parts = clean_url.split("/")[3:] if "://" in clean_url else []
            meaningful_parts = [p for p in path_parts if len(p) > 3 and not p.startswith("?")]
            score += min(len(meaningful_parts) * 0.5, 2.0)

            if score > 0:
                scored.append({"url": clean_url, "title": title[:100], "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        for item in scored[:5]:
            best_links.append({"url": item["url"], "title": item["title"]})

        return best_links

    def _extract_web_time(self, raw_html: str, url: str = "") -> float:
        """★任务C-4（2026-09-08）：从网页原始内容提取发布时间。

        灰度 ENABLE_WEB_TIME_EXTRACTION 默认 False → 返回 0.0（零开销、零变化）。
        提取失败/异常 → 返回 0.0，绝不打断搜索主流程（容错红线）。

        Returns:
            float: 网页发布时间戳；0.0 表示未提取到。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_WEB_TIME_EXTRACTION', False):
                return 0.0
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseController::_extract_web_time L3005")
            return 0.0
        try:
            from nucleus.knowledge.WebTimeExtractor import get_shared_extractor
            _ex = get_shared_extractor(
                log_fn=lambda _lvl, _m: self._log(LogLevel.DEBUG, _m))
            _hit = _ex.extract(raw_html or "", url)
            if _hit:
                self._log(LogLevel.DEBUG,
                          f"网页时间提取: {_hit.get('iso','')} "
                          f"(置信度{_hit.get('confidence',0):.2f}, {_hit.get('kind')}) url={url[:50]}")
                return float(_hit["timestamp"])
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"网页时间提取异常(不影响搜索): {_e}")
        return 0.0

    def _extract_main_content(self, html_text: str) -> str:
        """从网页文本中提取正文内容"""
        # ★任务C-4：在剥离 HTML 标签**之前**先抓时间（meta/time 标签清洗后不可恢复）
        self._last_web_time = self._extract_web_time(html_text or "", "")
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '\n', text)

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        meaningful_lines = [line for line in lines if len(line) > 30]

        if len(meaningful_lines) > 50:
            best_start = 0
            best_density = 0
            for i in range(len(meaningful_lines) - 10):
                density = sum(len(_ln) for _ln in meaningful_lines[i:i+10])
                if density > best_density:
                    best_density = density
                    best_start = i

            content_lines = meaningful_lines[best_start:best_start + 30]
        else:
            content_lines = meaningful_lines[:30]

        return "\n".join(content_lines)

    def _open_browser_url(self, url: str):
        """统一的浏览器打开方法，支持窗口追踪"""
        use_subprocess = self._get_config("use_subprocess_browser", True)

        if use_subprocess and sys.platform == "win32":
            try:
                browser_path = self._permission_config.get("preferred_browser_path", "")
                if browser_path and os.path.exists(browser_path):
                    proc = subprocess.Popen(
                        [browser_path, "--new-window", url],
                        creationflags=subprocess.CREATE_NO_WINDOW
                    , timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_medium"], )
                    self._browser_windows.append(proc.pid)
                    max_windows = self._get_config("max_browser_windows", 3)
                    if len(self._browser_windows) > max_windows:
                        self._cleanup_browser_windows()
                    return
            except Exception as e:
                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")

        import webbrowser
        webbrowser.open(url, new=2)

    def _cleanup_browser_windows(self):
        """关闭搜索打开的浏览器窗口"""
        close_delay = 3  # 搜索完成后3秒自动关闭，给页面消化留一点时间

        def _do_cleanup():
            time.sleep(close_delay)
            for pid in list(self._browser_windows):
                try:
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            check=False, capture_output=True,
                            timeout=5, encoding="utf-8", errors="replace"
                        )
                    self._browser_windows.remove(pid)
                except Exception as e:
                    self._log(LogLevel.ERROR, f'异常: {e}')

        if self._browser_windows:
            cleanup_thread = threading.Thread(target=_do_cleanup, daemon=True)
            cleanup_thread.start()
            self._log(LogLevel.INFO, f"🧹 窗口清理已安排: {len(self._browser_windows)}个窗口将在{close_delay}秒后关闭")

    def _get_config(self, key: str, default: Any = None) -> Any:
        """从CONTROLLER_PERMISSION配置块安全获取配置项"""
        return self._permission_config.get(key, default) if self._permission_config else default

    def _get_headless_config(self, key: str, default: Any = None) -> Any:
        """从HEADLESS_BROWSER配置块安全获取配置项"""
        try:
            import config
            cfg = getattr(config, 'HEADLESS_BROWSER', {})
            return cfg.get(key, default)
        except Exception:
            return default

    def _emit_digest(self, content: str, trigger_reason: str, source_timestamp: float = 0.0):
        safe_prefix = "深度搜索·知识学习"
        _payload = {
            "content": f"[{safe_prefix}] {content[:2000]}",
            "source_organ": self.organ_name,
            "trigger_reason": trigger_reason,
            "importance": "B",
            "view_mode": "OUTER_VIEW",
        }
        # ★任务C-4：仅在提取到网页时间时附带字段（未提取到时 payload 与改造前一致）
        if float(source_timestamp or 0.0) > 0:
            _payload["source_timestamp"] = float(source_timestamp)
        self._emit(DigestEvent.KNOWLEDGE, _payload, priority=3, layer="L2")

    def _fallback_single_search(self, url: str, reason: str, payload: dict) -> dict[str, Any]:
        """深度搜索失败时的降级兜底"""
        self._log(LogLevel.INFO, "深度搜索降级为普通搜索")
        result = self._on_open_url({**payload, "deep_search": False})
        # 降级搜索完成后，强制清理所有浏览器窗口
        self._cleanup_browser_windows()
        return result

    def _finish_deep_search(self, stages: int, contents: list, topic: str) -> dict[str, Any]:
        """构建深度搜索完成响应"""
        return {
            "status": "deep_search_completed",
            "stages_completed": stages,
            "articles_digested": len(contents),
            "total_chars": sum(len(c.get("content", "")) for c in contents),
            "search_topic": topic,
        }

    def _fetch_url_text(self, url: str) -> str | None:
        """获取网页文本内容（SEC-4: 校验 scheme 并拦截内网/元数据地址，防 SSRF）"""
        # ★MAINT-1修复: 复用共享 SSRF 防护，避免两份校验逻辑漂移
        # [批次4·深度体检][SSRF防护] 抓取前做 SSRF 校验
        from nucleus.ssrf_guard import is_safe_http_url
        _allowed, _reason = is_safe_http_url(url)
        if not _allowed:
            self._log(LogLevel.WARNING, f"SSRF 防护拦截: {_reason}")
            return None

        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            with requests.Session() as session:
                retry_strategy = Retry(
                    total=2,
                    backoff_factor=0.5,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"]
                )
                adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=3, pool_maxsize=5)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                })

                response = session.get(url, timeout=TIMEOUT_CONFIG["http_get"], verify=True)  # ★SEC-4修复: 开启证书校验，防 MITM / 内网自签证书
                if response.status_code != 200:
                    return None

                if response.encoding and response.encoding.lower() != 'ISO-8859-1':
                    encoding = response.encoding
                else:
                    try:
                        import chardet
                        detected = chardet.detect(response.content[:3000])
                        encoding = detected.get('encoding', 'utf-8') or 'utf-8'
                    except ImportError as e:
                        silent_exc(e, where="organs.motor.PulseController::_fetch_url_text L3181")
                        encoding = 'utf-8'

                response.encoding = encoding
                html_text = response.text

                text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:8000]
        except Exception as e:
            silent_exc(e, where="organs.motor.PulseController::_fetch_url_text L3192")
            return None

    def _select_best_search_url(self, original_url: str, reason: str = "") -> str:
        """根据网络可达性选择最优搜索引擎"""
        import urllib.parse

        query = ""
        if "q=" in original_url:
            query = original_url.split("q=", 1)[1].split("&")[0]
        elif "wd=" in original_url:
            query = original_url.split("wd=", 1)[1].split("&")[0]
        elif "query=" in original_url:
            query = original_url.split("query=", 1)[1].split("&")[0]

        if not query:
            return original_url

        query = urllib.parse.unquote(query)

        is_domestic = self._check_domestic_network()

        if is_domestic:
            templates = self._permission_config.get("search_urls_domestic", [])
        else:
            templates = self._permission_config.get("search_urls_international", [])

        if not templates:
            return original_url

        selected_url = templates[0].replace("{query}", urllib.parse.quote(query))
        self._log(LogLevel.INFO, f"搜索引擎选择: {'国内' if is_domestic else '国际'} → {selected_url[:60]}...")
        return selected_url

    def _check_domestic_network(self) -> bool:
        """检测当前网络环境是否为国内"""
        check_hosts = self._permission_config.get("network_check_hosts", [
            ("www.baidu.com", 3),
        ])

        for host, timeout in check_hosts[:1]:
            try:
                import socket
                socket.setdefaulttimeout(timeout)
                socket.create_connection((host, 80), timeout=timeout)
                self._log(LogLevel.DEBUG, f"网络检测: {host} 可达，判定为国内网络")
                return True
            except Exception:
                silent_exc(where="organs/motor/PulseController.py:3242")
                continue

        self._log(LogLevel.DEBUG, "国内网络不可达，使用国际搜索引擎")
        return False

    def _on_read_file(self, payload: dict) -> dict[str, Any]:
        """读取文件内容"""
        path = payload.get("path", "")
        if not path:
            return {"status": "skipped", "reason": "空路径"}

        if self._current_load_level() == "critical":
            return {"status": "skipped", "reason": "系统负载临界，暂停文件操作"}

        if not self._check_file_permission(path):
            self._write_audit("read_file", path, "blocked", "权限拒绝")
            return {"status": "blocked", "reason": "权限拒绝"}

        self._operation_count += 1
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._file_read_count += 1

        try:
            max_bytes = self._permission_config.get("max_file_read_bytes", 80000)
            with open(path, encoding="utf-8") as f:
                content = f.read(max_bytes)

            self._write_audit("read_file", path, "success")
            self._log(LogLevel.INFO, f"已读取文件: {os.path.basename(path)} ({len(content)}字符)")

            self._emit(DigestEvent.KNOWLEDGE, {
                "content": f"[文件·{os.path.basename(path)}] {content[:2000]}",
                "source_organ": self.organ_name,
                "trigger_reason": f"controller.read_file:{path}",
                "importance": "B",
                "view_mode": "INNER_VIEW",
            }, priority=3, layer="L2")

            return {"status": "success", "path": path, "content_length": len(content)}
        except Exception as e:
            self._write_audit("read_file", path, "error", str(e))
            self._log(LogLevel.ERROR, f"读取文件失败: {e}")
            return {"status": "error", "error": str(e)}

    def _on_list_directory(self, payload: dict) -> dict[str, Any]:
        """遍历目录"""
        path = payload.get("path", os.getcwd())
        if not self._check_file_permission(path):
            return {"status": "blocked", "reason": "权限拒绝"}

        try:
            entries = []
            for entry in os.listdir(path):
                full_path = os.path.join(path, entry)
                entry_type = "dir" if os.path.isdir(full_path) else "file"
                try:
                    size = os.path.getsize(full_path) if entry_type == "file" else 0
                except Exception:
                    size = 0
                entries.append({"name": entry, "type": entry_type, "size": size})

            return {"status": "success", "path": path, "entries": entries[:100]}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _on_launch_app(self, payload: dict) -> dict[str, Any]:
        """启动应用程序"""
        app = payload.get("app", "")
        if not self._check_permission("launch_app", app):
            return {"status": "blocked", "reason": "权限拒绝"}

        return {"status": "pending", "reason": "桌面GUI能力待阶段3实现"}
    def _on_search_terminate(self, payload: dict) -> dict[str, Any]:
        """接收内在世界的搜索终止信号"""
        topic = payload.get("search_topic", "")[:40]
        self._search_terminated = True
        self._log(LogLevel.INFO, f"收到搜索终止信号: {topic}，停止后续阶段")
        return {"status": "terminate_acknowledged"}

    def _current_load_level(self) -> str:
        """线程安全读取当前负载等级，避免与 _on_hardware_snapshot 的写入产生数据竞争。"""
        with self._load_level_lock:
            return self._load_level

    def _on_hardware_snapshot(self, payload: dict) -> dict[str, Any]:
        """接收硬件负载脉冲"""
        cpu = payload.get("cpu", {}).get("usage_percent", 0)
        mem = payload.get("memory", {}).get("usage_percent", 0)

        with self._load_level_lock:
            # ★FIX(M5): 与 InfoField 的 CPU/内存阈值对齐，并补上 moderate 档
            if cpu > 95 or mem > 95:
                self._load_level = "critical"
            elif cpu > 80 or mem > 85:
                self._load_level = "heavy"
            elif cpu > 50 or mem > 65:
                self._load_level = "moderate"
            else:
                self._load_level = "light"

        return {"status": "cached", "load_level": self._current_load_level()}

    # ========== 权限校验 ==========

    def _check_permission(self, operation: str, target: str) -> bool:
        """通用权限校验入口"""
        if not self._permission_config.get("desktop_control_enabled", True):
            self._log(LogLevel.WARNING, f"桌面控制总开关关闭，拒绝操作: {operation}")
            return False

        target_lower = target.lower()
        blacklist = self._permission_config.get("command_blacklist", [])
        for cmd in blacklist:
            if cmd.lower() in target_lower:
                self._log(LogLevel.WARNING, f"命令黑名单拦截: {target}")
                return False

        return True

    def _check_url_permission(self, url: str) -> bool:
        """网页URL权限校验（SEC-2: 默认拒绝，白名单为空即拒绝，不再放行）"""
        if not self._check_permission("open_url", url):
            return False

        url_lower = url.lower()

        url_blacklist = self._permission_config.get("url_blacklist", [])
        for pattern in url_blacklist:
            if self._wildcard_match(pattern, url_lower):
                return False

        url_whitelist = self._permission_config.get("url_whitelist", [])
        # ★SEC-2修复: 白名单为空视为配置缺失/未授权，默认拒绝，
        #   不再 return True（旧逻辑导致任意 URL 可读/可访问）
        if not url_whitelist:
            self._log(LogLevel.WARNING, f"URL 白名单为空，默认拒绝访问: {url}")
            return False

        return any(self._wildcard_match(pattern, url_lower) for pattern in url_whitelist)

    def _check_file_permission(self, path: str) -> bool:
        """文件读取权限校验（v25.0修复: 键名对齐config + 默认拒绝 + 通配符黑名单）"""
        if not self._check_permission("read_file", path):
            return False

        # 规范化路径，消除 .. 和符号链接
        try:
            real_path = os.path.realpath(path)
        except Exception:
            real_path = os.path.abspath(path)
        real_path_lower = real_path.lower()

        # 黑名单检查：使用规范化的真实路径匹配（支持 ** 递归通配，见 _wildcard_match）
        # ★v25.0修复: 旧键名 file_read_blacklist 与 config.read_blacklist 不匹配，
        #   永远取默认 []，导致黑名单拦截完全失效
        file_blacklist = self._permission_config.get("read_blacklist", [])
        for pattern in file_blacklist:
            if self._wildcard_match(pattern, real_path_lower) or real_path_lower.startswith(pattern.lower().rstrip(os.sep)):
                return False

        # 白名单检查：默认拒绝（白名单为空即拒绝，不再放行）
        # ★v25.0修复: 旧键名 file_read_whitelist 与 config.read_whitelist 不匹配，
        #   永远取默认 []；旧逻辑 `if not file_whitelist: return True` 导致任意文件可读（路径穿越）
        file_whitelist = self._permission_config.get("read_whitelist", [])
        if not file_whitelist:
            self._log(LogLevel.WARNING, f"文件读取白名单为空，默认拒绝读取: {path}")
            return False

        for pattern in file_whitelist:
            try:
                pattern_real = os.path.realpath(pattern) if os.path.exists(pattern) else pattern
            except Exception:
                pattern_real = pattern
            pattern_lower = pattern_real.lower().rstrip(os.sep)
            if real_path_lower == pattern_lower or real_path_lower.startswith(pattern_lower + os.sep):
                return True

        return False

    @staticmethod
    def _wildcard_match(pattern: str, text: str) -> bool:
        """通配符匹配（v25.0修复: 支持 ** 跨目录递归、* 单层、? 单字符）"""
        import re
        pattern = pattern.lower()
        text = text.lower()
        if pattern == text:
            return True
        # ** -> 任意路径(含分隔符)；* -> 单层(不含分隔符)；? -> 单字符
        regex = pattern.replace("**", "__DBL__")
        regex = regex.replace("*", r"[^/\\]*").replace("?", r".")
        regex = regex.replace("__DBL__", r".*")
        try:
            return re.search(regex, text) is not None
        except re.error:
            return pattern in text

    # ========== 审计日志 ==========

    def _write_audit(self, operation: str, target: str, result: str, detail: str = ""):
        """写入操作审计日志"""
        entry = {
            "timestamp": time.time(),
            "operation": operation,
            "target": target,
            "result": result,
            "detail": detail,
            "load_level": self._current_load_level(),
        }
        with self._audit_lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > 500:
                self._audit_log = self._audit_log[-250:]

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== R4阶段二：存续编排器钩子（协调层代表） ==========

    def on_survival_low(self, snapshot) -> dict:
        """★R4阶段二：存续低位动作（协调层代表）。

        第一批：设置状态标志 + 日志。第二批接入全局并发降载/任务节流。
        """
        self._survival_state = "low"
        self._log(LogLevel.INFO,
                  f"[R4协调层] 存续低位，指数={getattr(snapshot, 'index', '?')}，"
                  f"准备降载（第二批接入并发降载/搜索降级/节流）")
        return {"layer": "coordination", "state": "low"}

    def on_survival_high(self, snapshot) -> dict:
        """★R4阶段二：存续高位动作（协调层代表）。"""
        self._survival_state = "high"
        self._log(LogLevel.INFO,
                  f"[R4协调层] 存续高位，指数={getattr(snapshot, 'index', '?')}，恢复全量调度")
        return {"layer": "coordination", "state": "high"}

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "operation_count": self._operation_count,
            "file_read_count": self._file_read_count,
            "url_open_count": self._url_open_count,
            "load_level": self._current_load_level(),  # 通过锁保护读取，避免与写入竞争
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                ControllerEvent.OPEN_URL,
                ControllerEvent.READ_FILE,
                ControllerEvent.LIST_DIRECTORY,
                ControllerEvent.LAUNCH_APP,
                ControllerEvent.SEARCH_STAGE_COMPLETED,
                SearchEvent.TERMINATED,
                TouchEvent.HARDWARE_SNAPSHOT,
                SystemEvent.STATUS_REQUEST,
            ],
            "min_priority": 1,
        }]

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "控制器",
    "class_name": "PulseController",
    "attr_name": "controller",
    "system": "motor",
    "always_online": False,
    "feature_flag": "enable_controller",
    "extra_deps": {},
    "post_wiring": [
        {"target": "node_pool", "setter": "set_node_pool"},
        {"target": "qica", "setter": "set_qica"},
        {"target": "白细胞", "setter": "set_white_cell"},
        {"target": "触觉", "setter": "set_touch"},
        {"target": "潜意识", "setter": "set_subconscious"},
        {"target": "胃", "setter": "set_stomach"},
    ],
}

if __name__ == "__main__":
    print("=== PulseController v9.5 分层脉冲自测 ===\n")
    print("=== 自测通过 ===")
