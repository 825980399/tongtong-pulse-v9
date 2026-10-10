# -*- coding: utf-8 -*-
"""
RssCollector.py —— RSS采集器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: RSS订阅源内容采集与解析
机制: 基于RssArticle类实现，包含10个核心方法
定位: 知识获取层
"""

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.request import Request, urlopen

from nucleus._silent_except import silent_exc

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SEEN_PATH = os.path.join(_PROJECT_ROOT, "data", "rss_cache", "seen_hashes.json")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


@dataclass
class RssArticle:
    """结构化文章（只存摘要与链接，不存全文）"""
    title: str
    summary: str
    url: str
    source: str = ""
    category: str = ""
    published_at: str = ""
    quality_score: float = 0.0
    content_hash: str = field(default="", repr=False)

    def to_digest_content(self, max_chars: int = 600) -> str:
        """转成喂给胃的消化文本（标题+摘要，截断）"""
        _sum = (self.summary or self.title or "").strip()
        return f"{self.title}\n{_sum[:max_chars]}"


def _default_fetch(url: str, timeout: float = 8.0) -> bytes:
    """默认网络抓取（可注入替身做离线测试）"""
    # ★往期批次 相关任务：出站白名单前置检查（fail-closed，拒绝即上抛，不放行）。
    from nucleus.ssrf_guard import is_safe_http_url
    _ok, _reason = is_safe_http_url(url)
    if not _ok:
        raise ValueError(f"[SSRF] 拒绝出站请求: {url} -> {_reason}")
    req = Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


class RssCollector:
    """RSS 采集器（线程安全；非器官，不自建心跳，由 start() 的 daemon 定时器驱动）"""

    def __init__(self, cfg: dict[str, Any] | None = None,
                 log_fn: Callable[[str], None] | None = None,
                 fetch_fn: Callable[[str], bytes] | None = None):
        try:
            import config as _cfg
            _conf = dict(getattr(_cfg, 'RSS_COLLECTOR_CONFIG', {}) or {})
        except Exception:
            _conf = {}
        if cfg:
            _conf.update(cfg)
        self._cfg = _conf
        self._log_fn = log_fn or (lambda _m: None)
        self._fetch = fetch_fn or _default_fetch

        self._feeds: list[dict[str, Any]] = list(_conf.get("feeds", []))
        for _f in self._feeds:
            _f.setdefault("name", _f.get("url", "?"))
            _f.setdefault("fail_count", 0)      # 连续失败计数
            _f.setdefault("paused", False)      # 连续失败3次自动暂停
            _f.setdefault("last_pull_at", 0.0)
            _f.setdefault("slow_count", 0)      # ★主线第75批 T4：超时（慢源）计数
            _f.setdefault("deprioritized", False)  # ★主线第75批 T4：慢源降级标记
        self._pull_interval = float(_conf.get("pull_interval", 1800))
        self._max_articles_per_pull = int(_conf.get("max_articles_per_pull", 10))
        self._min_quality_score = float(_conf.get("min_quality_score", 0.5))
        self._blacklist = list(_conf.get("keyword_blacklist",
                                         ["广告", "推广", "招聘"]))
        self._dedup_ttl = float(_conf.get("dedup_ttl", 86400 * 7))
        self._max_consecutive_failures = int(_conf.get("max_consecutive_failures", 3))
        self._timeout = float(_conf.get("per_source_timeout", 15))  # ★主线第75批 T4：8→15
        self._slow_threshold = int(_conf.get("slow_source_threshold", 3))  # 超时N次降级
        self._emit_priority = int(_conf.get("emit_priority", 4))   # 消化优先级（低=后台）

        # 去重：内存 + 磁盘
        self._seen: dict[str, float] = {}   # content_hash -> first_seen_ts
        self._seen_lock = threading.Lock()
        self._load_seen()

        # 统计
        self._stats = {"pull_total": 0, "articles_new": 0, "articles_dup": 0,
                       "articles_filtered": 0, "pull_fail": 0}

        # 定时器
        self._timer = None
        self._running = False
        self._emitter: Callable[..., Any] | None = None  # set_emitter 注入

    # ========== 对外接口 ==========

    def set_emitter(self, emitter: Callable[..., Any]):
        """注入脉冲发射函数（签名同 BasePulseOrgan._emit：event_type, payload, priority=, layer=）。

        未注入时使用 _default_emitter()（★A-11：经 InfoField 全局场发布，TimeCore 同款
        非器官发射模式），保证 daemon 定时拉取的文章**真实进入胃消化链路**，
        不再出现"只 print 不消化"的断链。
        """
        self._emitter = emitter

    def _default_emitter(self, event_type: str, payload: dict, priority: int = 3,
                         layer: str = "L2"):
        """★A-11：默认发射器——非器官模块的标准发脉冲方式（复用 TimeCore 模式）。

        经 InfoField 全局场 publish；场未就绪时静默跳过（下轮心跳重试拉取，
        文章仍在去重表中不会丢失内容，只是本次不消化）。
        """
        try:
            from nucleus.field.InfoField import get_info_field
            _field = get_info_field()
            if _field is None:
                self._log("InfoField 尚未就绪，本轮文章暂不推送消化")
                return
            _field.publish({
                "event_type": event_type,
                "source_organ": "RSS采集器",
                "payload": payload,
                "priority": priority,
                "layer": layer,
            })
        except Exception as _e:
            self._log(f"默认发射器发布异常(已忽略): {_e}")

    def start(self):
        """启动定时拉取（daemon 线程，首次延迟5秒）"""
        if self._running:
            return
        self._running = True
        self._timer = threading.Thread(target=self._loop, daemon=True,
                                       name="RssCollectorTimer")
        self._timer.start()
        self._log(f"采集器已启动（{len(self._feeds)}个源，间隔{int(self._pull_interval)}s）")

    def stop(self):
        self._running = False

    def pull_all(self, push: bool = True) -> list[RssArticle]:
        """拉取所有**未暂停**源（单源失败互不影响），返回本轮新文章。"""
        _all_new: list[RssArticle] = []
        # ★主线第75批 T4：慢源（deprioritized）排到最后拉取，优先保障其他源
        _feeds_sorted = sorted(
            self._feeds,
            key=lambda f: (bool(f.get("deprioritized", False)), f.get("slow_count", 0)))
        for _feed in _feeds_sorted:
            if _feed.get("paused"):
                continue
            try:
                _t0 = time.time()
                _arts = self.pull_feed(_feed)
                _elapsed = time.time() - _t0
                _feed["last_pull_at"] = time.time()
                _feed["fail_count"] = 0  # 成功即清零
                _feed["slow_count"] = 0
                _feed["deprioritized"] = False
                _new = self._process_articles(_arts, _feed)
                _all_new.extend(_new)
                self._stats["pull_total"] += 1
                # ★A-11（2026-09-08）：拉取日志补全——每个源每次拉取都留痕，
                #   新文章=0（全部去重）也要能观测到，便于监控增量节奏
                if _new:
                    self._log(f"源[{_feed['name']}] 拉取{_elapsed:.1f}s，"
                              f"新文章{len(_new)}/{len(_arts)}")
                else:
                    self._log(f"源[{_feed['name']}] 拉取{_elapsed:.1f}s，"
                              f"抓到{len(_arts)}篇，新文章0篇（全部去重）")
                if _elapsed > 3.0:
                    self._log(f"源[{_feed['name']}] 拉取耗时{_elapsed:.1f}s 超过3秒目标")
            except Exception as _e:
                # 容错：单源失败不影响其他源；连续失败3次自动暂停并告警
                self._stats["pull_fail"] += 1
                _feed["fail_count"] = int(_feed.get("fail_count", 0)) + 1
                # ★主线第75批 T4：超时源降级（记录慢源，下次排到最后）
                if self._is_timeout_error(_e):
                    _feed["slow_count"] = int(_feed.get("slow_count", 0)) + 1
                    if _feed["slow_count"] >= self._slow_threshold:
                        _feed["deprioritized"] = True
                        self._log(f"⚠️ 慢源降级: 源[{_feed['name']}] 超时"
                                  f"{_feed['slow_count']}次，已降低优先级（下次最后拉取）")
                if _feed["fail_count"] >= self._max_consecutive_failures:
                    _feed["paused"] = True
                    self._log(f"⚠️ 告警: 源[{_feed['name']}] 连续失败"
                              f"{_feed['fail_count']}次，已自动暂停（原因: {_e}）")
                else:
                    self._log(f"源[{_feed['name']}] 拉取失败"
                              f"({_feed['fail_count']}/{self._max_consecutive_failures}): {_e}")
        if push and _all_new:
            # ★A-11：未注入 emitter 时走默认发射器（InfoField 总线），
            #   daemon 拉取的文章不再断链
            self._push_to_digest(_all_new)
        return _all_new

    def pull_feed(self, feed: dict[str, Any]) -> list[RssArticle]:
        """拉取单个源：网络抓取 → feedparser 解析"""
        _raw = self._fetch(feed["url"], timeout=self._timeout)
        return self.parse_feed(_raw, feed)

    def parse_feed(self, content, feed: dict[str, Any]) -> list[RssArticle]:
        """解析 RSS/Atom（feedparser 支持字节/字符串/URL）"""
        import feedparser
        _parsed = feedparser.parse(content)
        _arts: list[RssArticle] = []
        for _e in (_parsed.entries or [])[:self._max_articles_per_pull * 2]:
            _title = str(getattr(_e, "title", "") or "").strip()
            _url = str(getattr(_e, "link", "") or "").strip()
            if not _title and not _url:
                continue
            _summary = self._clean_html(str(getattr(_e, "summary", "")
                                            or getattr(_e, "description", "") or ""))
            _pub = ""
            _pp = getattr(_e, "published_parsed", None) or getattr(_e, "updated_parsed", None)
            if _pp:
                try:
                    _pub = time.strftime("%Y-%m-%d %H:%M", _pp)
                except Exception:
                    _pub = ""
            _arts.append(RssArticle(
                title=_title[:200], summary=_summary[:1500], url=_url,
                source=feed.get("name", ""), category=feed.get("category", ""),
                published_at=_pub,
            ))
        return _arts

    def deduplicate(self, articles: list[RssArticle]) -> list[RssArticle]:
        """标题+链接哈希去重（含历史记录 TTL 淘汰）"""
        _now = time.time()
        _out: list[RssArticle] = []
        with self._seen_lock:
            # TTL 淘汰
            if len(self._seen) > 5000:
                self._seen = {k: v for k, v in self._seen.items()
                              if _now - v < self._dedup_ttl}
            for _a in articles:
                _h = self._hash_article(_a)
                _a.content_hash = _h
                if _h in self._seen:
                    self._stats["articles_dup"] += 1
                    continue
                self._seen[_h] = _now
                _out.append(_a)
            self._save_seen()
        return _out

    def filter_quality(self, articles: list[RssArticle]) -> list[RssArticle]:
        """质量过滤：黑名单关键词 + 来源权重×内容分 ≥ 阈值"""
        _out: list[RssArticle] = []
        for _a in articles:
            _text = f"{_a.title} {_a.summary}"
            if any(_kw in _text for _kw in self._blacklist):
                self._stats["articles_filtered"] += 1
                continue
            _feed = next((f for f in self._feeds if f.get("name") == _a.source), {})
            _weight = float(_feed.get("weight", 0.6))
            _content_score = min(1.0, 0.3 + 0.1 * (len(_a.summary) >= 80)
                                 + 0.1 * (len(_a.summary) >= 200)
                                 + 0.1 * (len(_a.title) >= 10))
            _a.quality_score = round(_weight * _content_score, 2)
            if _a.quality_score < self._min_quality_score:
                self._stats["articles_filtered"] += 1
                continue
            _out.append(_a)
        return _out

    def should_collect(self, query: str) -> bool:
        """判断查询是否适合用 RSS 获取（新闻/动态/教程类）"""
        _q = str(query or "")
        return any(_k in _q for _k in
                   ("最新", "新闻", "动态", "资讯", "热点", "周刊", "行业"))

    def collect_for_direction(self, direction: str,
                              max_articles: int = 3) -> list[RssArticle]:
        """供双腿主动学习/路由器调用：拉取并返回与方向相关的新文章（不推送）。

        关联判定：方向分词后与标题/摘要做包含匹配，命中≥1个词即相关。
        分词策略：空白/标点切段 + 长段（>4字）额外生成 2 字滑窗片段
        （"AI行业最新动态"→"行业/最新/动态/…"，整串匹配几乎必失手）。
        """
        _arts = self.pull_all(push=False)
        if not _arts:
            return []
        import re as _re
        _runs = [w for w in _re.findall(r'[\u4e00-\u9fffA-Za-z0-9]{2,}', direction)
                 if len(w) >= 2]
        _words = set(_runs)
        for _run in _runs:
            if len(_run) > 4:  # 长段滑窗，提升召回（topic 关联本就允许宽匹配）
                for _i in range(len(_run) - 1):
                    _words.add(_run[_i:_i + 2])
        if not _words:
            return []
        _hits: list[RssArticle] = []
        for _a in _arts:
            _text = f"{_a.title} {_a.summary} {_a.category}".lower()
            if any(w.lower() in _text for w in _words):
                _hits.append(_a)
                if len(_hits) >= max_articles:
                    break
        return _hits

    def get_stats(self) -> dict[str, Any]:
        _paused = [f["name"] for f in self._feeds if f.get("paused")]
        return {**self._stats,
                "feeds_total": len(self._feeds),
                "feeds_paused": _paused,
                "seen_size": len(self._seen),
                "running": self._running}

    # ========== 内部实现 ==========

    @staticmethod
    def _is_timeout_error(e: Exception) -> bool:
        """★主线第75批 T4：判断异常是否由抓取超时引起（用于慢源降级）。"""
        import socket
        if isinstance(e, (socket.timeout, TimeoutError)):
            return True
        _reason = getattr(e, "reason", None)
        if _reason is not None and isinstance(_reason, (socket.timeout, TimeoutError)):
            return True
        _s = str(e).lower()
        return "timed out" in _s or "timeout" in _s

    def _process_articles(self, arts: list[RssArticle],
                          feed: dict[str, Any]) -> list[RssArticle]:
        """单源文章后处理：去重 → 质量过滤 → 限量"""
        _deduped = self.deduplicate(arts)
        _qualified = self.filter_quality(_deduped)
        self._stats["articles_new"] += len(_qualified)
        return _qualified[:self._max_articles_per_pull]

    def _push_to_digest(self, articles: list[RssArticle]):
        """走正常消化链路：DigestEvent.KNOWLEDGE → 胃 → 知识树（不旁路）"""
        _emitter = self._emitter if self._emitter is not None else self._default_emitter
        for _a in articles:
            try:
                _emitter("digest.knowledge", {
                    "content": f"[RSS·{_a.source}] {(_a.category + ' ') if _a.category else ''}"
                               f"{_a.to_digest_content()}",
                    "source_organ": "RSS采集器",
                    "trigger_reason": "rss_collect",
                    "importance": "B",
                    "view_mode": "OUTER_VIEW",
                    "source_url": _a.url,
                    "source_time": _a.published_at,
                }, priority=self._emit_priority, layer="L2")
            except Exception as _e:
                self._log(f"推送消化失败(已忽略): {_e}")

    @staticmethod
    def _hash_article(a: RssArticle) -> str:
        """★内部协作者要求：标题+链接哈希去重"""
        return hashlib.md5(f"{a.title}|{a.url}".encode()).hexdigest()[:16]

    @staticmethod
    def _clean_html(text: str) -> str:
        import re as _re
        return _re.sub(r"<[^>]+>", "", str(text)).replace("&nbsp;", " ").strip()

    def _load_seen(self):
        try:
            if os.path.exists(_SEEN_PATH):
                with open(_SEEN_PATH, encoding="utf-8") as f:
                    self._seen = {k: float(v) for k, v in json.load(f).items()}
        except Exception as _e:
            self._log(f"去重记录加载失败(空表启动): {_e}")

    def _save_seen(self):
        try:
            os.makedirs(os.path.dirname(_SEEN_PATH), exist_ok=True)
            _tmp = _SEEN_PATH + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(self._seen, f, ensure_ascii=False)
            os.replace(_tmp, _SEEN_PATH)
        except Exception as _e:
            self._log(f"去重记录写盘失败(已忽略): {_e}")

    def _loop(self):
        time.sleep(5)  # 启动缓冲，避开框架启动高峰
        while self._running:
            try:
                self.pull_all(push=True)
            except Exception as _e:
                self._log(f"定时拉取异常(已忽略): {_e}")
            # 分片 sleep，保证 stop() 能及时退出
            _deadline = time.time() + self._pull_interval
            while self._running and time.time() < _deadline:
                time.sleep(min(5, max(0.5, _deadline - time.time())))

    def _log(self, msg: str):
        try:
            self._log_fn(f"[RSS采集器] {msg}")
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.RssCollector::_log L412")


# ========== 模块级单例（供双腿等器官接线使用） ==========

_shared_collector: RssCollector | None = None
_shared_lock = threading.Lock()


def get_shared_rss_collector(log_fn=None) -> RssCollector:
    global _shared_collector
    with _shared_lock:
        if _shared_collector is None:
            _shared_collector = RssCollector(log_fn=log_fn)
            _shared_collector.start()  # 定时增量拉取（30分钟）
        return _shared_collector


if __name__ == "__main__":
    # ★A-11说明：__main__ 演示不再注入 print 假发射器（曾误导排查——daemon 断链根因
    #   曾被误认为此处）。现在不注入即走 InfoField 默认发射器。
    _c = RssCollector(cfg={"feeds": [
        {"name": "本地测试", "url": "file:///nonexistent", "weight": 0.8,
         "category": "测试"}]})
    print(_c.get_stats())
