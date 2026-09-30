# -*- coding: utf-8 -*-
"""
WikiQuerier.py —— 百科查询器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 维基百科等百科数据源查询
机制: 基于WikiResult类实现，包含10个核心方法
定位: 知识获取层
"""

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nucleus.logger import get_module_logger
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


_logger = get_module_logger("WikiQuerier")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "wiki_cache")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ★主线第65批 T6/P2：UA 轮换池（重试时轮换，降低被识别为爬虫的风控概率）
_UA_POOL = [
    _UA,
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
]


# ========== ★主线第13批 P2-85：403 反爬治理开关 ==========

def _rate_limit_enabled() -> bool:
    """P2-85 治理开关（config.ENABLE_WIKI_QUERIER_RATE_LIMIT，默认 True）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True))
    except Exception as e:
        silent_exc(e, where="nucleus.knowledge.WikiQuerier::_rate_limit_enabled L57")
        return True


def _get_ua() -> str:
    """合规 User-Agent（开关开启时含联系方式；关闭时回落原 _UA，零回归）。"""
    if not _rate_limit_enabled():
        return _UA
    try:
        import config as _cfg
        _ua = str(getattr(_cfg, "WIKI_QUERIER_USER_AGENT", "") or "").strip()
        return _ua or _UA
    except Exception:
        return _UA


def _get_min_interval() -> float:
    """连续请求最小间隔（秒）；关闭开关时返回 0.0（不节流）。"""
    if not _rate_limit_enabled():
        return 0.0
    try:
        import config as _cfg
        _v = float(getattr(_cfg, "WIKI_QUERIER_MIN_INTERVAL", 1.0))
        return max(0.0, _v)
    except Exception as e:
        silent_exc(e, where="nucleus.knowledge.WikiQuerier::_get_min_interval L81")
        return 1.0


@dataclass
class WikiResult:
    """百科查询结果（摘要级，不存全文）"""
    keyword: str
    title: str
    summary: str
    url: str
    source: str = "百度百科"
    fetched_at: float = 0.0


def _default_fetch(url: str, timeout: float = 8.0) -> str:
    """默认网络抓取（可注入替身做离线测试）。

    ★P2-85：开关开启时使用含联系方式的合规 UA；遇 403 时短退避重试一次
    （部分站点对「首次请求」与「稍后重试」的风控策略不同），仍失败则上抛
    由调用方 query() 捕获并 fallback 浏览器。
    """
    _headers = {
        "User-Agent": _get_ua(),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    # ★往期批次 相关任务：出站白名单前置检查（fail-closed，拒绝即上抛，不放行）。
    from nucleus.ssrf_guard import is_safe_http_url
    _ok, _reason = is_safe_http_url(url)
    if not _ok:
        raise ValueError(f"[SSRF] 拒绝出站请求: {url} -> {_reason}")
    _retry_on_403 = _rate_limit_enabled()
    # ★主线第65批 T6/P2：重试 3 次（间隔 5s），降低 403 反爬瞬时失败率
    _attempts = 3 if _retry_on_403 else 1
    _last_err = None
    for _i in range(_attempts):
        try:
            # ★往期批次 相关任务：UA 决策一处收口——合规 UA（_get_ua()）优先；
            #   仅当被拒（403 等）后才轮换 _UA_POOL，避免合规 UA 被无条件覆盖零生效。
            _h = dict(_headers)
            if _i == 0:
                _h["User-Agent"] = _get_ua()
            else:
                _h["User-Agent"] = _UA_POOL[(_i - 1) % len(_UA_POOL)]
            req = Request(url, headers=_h)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as _he:
            _last_err = _he
            # ★往期批次 相关任务：403 响应体留证（前 512B + 关键响应头），零误吞。
            #   便于定位反爬策略（含 X-Baidu-* / Retry-After），而非仅上抛 code。
            if getattr(_he, "code", None) == 403:
                try:
                    _body512 = _he.read(512) if hasattr(_he, "read") else b""
                    if isinstance(_body512, bytes):
                        _body512 = _body512.decode("utf-8", "replace")
                    _ev_hdr = {_k: _he.headers.get(_k) for _k in
                               ("Retry-After", "X-Baidu-Error", "X-Baidu-Trace")}
                    _logger.warning(
                        f"[WikiQuerier] 403 反爬命中: url={url[:80]} "
                        f"body512={_body512!r} headers={_ev_hdr}")
                except Exception as _le:
                    _logger.debug(f"[WikiQuerier] 403 留证异常: {_le}")
            if getattr(_he, "code", None) == 403 and _i < _attempts - 1:
                # 403 降级：5s 退避后重试（带微抖动避免同步重试风暴）
                time.sleep(5.0 + (_i * 0.5))
                continue
            raise
        except Exception as _e:
            _last_err = _e
            raise
    # 理论不可达（循环内必 return 或 raise），兜底上抛最后一次异常
    if _last_err is not None:
        raise _last_err
    return ""


class WikiQuerier:
    """百科查询器（线程安全；无自建线程，按需同步查询）"""

    def __init__(self, cfg: dict[str, Any] | None = None,
                 log_fn: Callable[[str], None] | None = None,
                 fetch_fn: Callable[[str], str] | None = None):
        try:
            import config as _cfg
            _conf = dict(getattr(_cfg, 'ENCYCLOPEDIA_QUERY_CONFIG', {}) or {})
        except Exception:
            _conf = {}
        if cfg:
            _conf.update(cfg)
        self._cfg = _conf
        self._log_fn = log_fn or (lambda _m: None)
        self._fetch = fetch_fn or _default_fetch

        self._cache_ttl = float(_conf.get("cache_ttl", 86400))  # ★24小时（内部协作者批复）
        self._timeout = float(_conf.get("timeout", 8))
        self._max_summary_length = int(_conf.get("max_summary_length", 500))
        self._url_template = str(_conf.get("url_template",
                                           "https://baike.baidu.com/item/{keyword}"))

        # 缓存：内存薄封装 + 文件持久化
        self._mem_cache: dict[str, WikiResult] = {}
        self._cache_lock = threading.Lock()

        # 统计（缓存命中率目标 >60%）
        self._stats = {"query_total": 0, "cache_hits": 0, "fetch_ok": 0,
                       "fetch_fail": 0, "should_query_rejects": 0,
                       "rate_limited": 0, "forbidden_403": 0}  # ★P2-85

        # ★P2-85：进程内请求节流时间戳（同一实例连续请求最小间隔）
        self._last_fetch_ts = 0.0
        self._throttle_lock = threading.Lock()

        # ★相关任务：域级冷却（被拒/Retry-After 后逐级退避，防封禁升级）
        self._domain_cooldown_until = {}
        self._domain_backoff_level = {}
        self._domain_cooldown_lock = threading.Lock()

    # ========== 对外接口 ==========

    def query(self, keyword: str) -> WikiResult | None:
        """查询实体/概念。缓存命中直接返回；未命中抓取百度百科并写缓存。

        Returns:
            WikiResult；查询失败/触发判定不过 → None（调用方 fallback 浏览器）
        """
        _kw = str(keyword or "").strip()
        if not _kw or len(_kw) > 30:
            return None
        # ★P2-85：<2 字超短词预处理。单字查询在百科几乎必然 403/空页，
        #   开关开启时补全为「X是什么」形态再查（提升命中、减少无效请求）。
        if _rate_limit_enabled() and len(_kw) == 1:
            _kw = f"{_kw}是什么"
        self._stats["query_total"] += 1

        _cached = self._get_cache(_kw)
        if _cached is not None:
            self._stats["cache_hits"] += 1
            return _cached

        try:
            _t0 = time.time()
            _result = self.query_baidu(_kw)
            _elapsed = time.time() - _t0
        except Exception as _e:
            self._stats["fetch_fail"] += 1
            self._log(f"查询[{_kw}]失败(调用方将fallback浏览器): {_e}")
            return None
        if _result is None or not _result.summary:
            self._stats["fetch_fail"] += 1
            self._log(f"查询[{_kw}]无有效摘要(调用方将fallback浏览器)")
            return None
        self._stats["fetch_ok"] += 1
        self._set_cache(_kw, _result)
        self._log(f"查询[{_kw}]成功（{_elapsed:.1f}s，摘要{len(_result.summary)}字，已缓存）")
        return _result

    def query_baidu(self, keyword: str) -> WikiResult | None:
        """百度百科 HTTP 查询（★URL 中文 quote 编码修复——预研发现的编码问题）"""
        # ★P2-85：请求前按最小间隔节流（开关关闭时 _get_min_interval 返回 0.0）。
        self._throttle()
        _url = self._url_template.format(keyword=quote(str(keyword), safe=""))
        # ★相关任务：域级冷却——冷却期内直接 fallback，不发起请求（防封禁升级）。
        _domain = self._domain_of_url(_url)
        if self._domain_in_cooldown(_domain):
            self._stats["rate_limited"] += 1
            self._log(f"查询[{keyword}]域 {_domain} 冷却中，跳过并 fallback 浏览器")
            return None
        try:
            _html = self._fetch(_url, timeout=self._timeout)
        except HTTPError as _he:
            if getattr(_he, "code", None) == 403:
                self._stats["forbidden_403"] += 1
                # ★相关任务：403 触发域冷却（含 Retry-After，若存在）。
                self._domain_trigger_cooldown(_domain, self._http_retry_after(_he))
                self._log(f"查询[{keyword}]被站点拒绝(403)，将 fallback 浏览器")
            raise
        _r = self.parse_baidu_html(_html, keyword)
        if _r is not None:
            _r.url = _url
        return _r

    def parse_baidu_html(self, html: str, keyword: str) -> WikiResult | None:
        """解析百度百科 HTML：优先 meta description（最稳），兜底 lemma-summary 段落"""
        _summary = ""
        # 1) meta description（百科词条页稳定存在）
        _m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
                       html, re.IGNORECASE)
        if _m:
            _summary = _m.group(1).strip()
        # 2) 兜底：lemma-summary 内首个 para 段落
        if not _summary:
            _m2 = re.search(r'<div[^>]+class=["\'][^"\']*para[^"\']*["\'][^>]*>(.*?)</div>',
                            html, re.DOTALL)
            if _m2:
                _summary = self._strip_tags(_m2.group(1))
        _summary = self._strip_tags(_summary)[:self._max_summary_length].strip()
        if len(_summary) < 10:  # 太短视为无效（反爬页/重定向页）
            return None
        # 标题：lemma-title 或 open 标签
        _t = re.search(r'<title>([^<]+)', html, re.IGNORECASE)
        _title = self._strip_tags(_t.group(1)).replace("_百度百科", "").strip() if _t else keyword
        return WikiResult(keyword=keyword, title=_title or keyword,
                          summary=_summary, url="", fetched_at=time.time())

    def should_query(self, query: str) -> bool:
        """触发判定：查询含明确实体名才值得发百科请求（内部协作者补充要求2）。

        命中形态：什么是X / X是什么 / X的定义 / X简介 / 引号内的名词 / 短名词短语。
        排除：时效类（最新/新闻/今天…）、过长句子、教程/方法类请求。
        ★A-12（2026-09-08，P1-14）：收紧模式（灰度 ENABLE_WIKI_TRIGGER_STRICT）——
            运行期实测：30+字复合概念长句触发百度百科 403 反爬。收紧规则（★第五批
            任务2A 已放宽实体定义类阈值至 长度≤30、标点≤2）：
            1) 长度>30字直接拒绝（只对简短实体名/概念名发起）
            2) 含动词/谓词信号（内化/反思/验证/分析…）的句子拒绝
            3) 标点>2个（复合句特征）拒绝
        ★第五批任务2A：任何拒绝都输出 DEBUG 日志，含查询词、拒绝原因与判定条件值，
          便于排查 wiki 通道命中率过低问题。
        """
        _ok, _reason = self._should_query_reason(query)
        if not _ok:
            self._stats["should_query_rejects"] += 1
            if _reason:
                _logger.debug(
                    f"[百科查询器][拒绝] query={str(query)!r} | 原因={_reason}")
        return _ok

    def _should_query_reason(self, query: str) -> tuple[bool, str]:
        """内部实现：返回 (是否触发, 拒绝原因)。供单测断言原因，亦支撑 DEBUG 日志。"""
        _q = str(query or "").strip()
        if not _q:
            return False, "空查询"
        if len(_q) > 40:
            return False, f"长度>40字(len={len(_q)})"
        # ★A-12 收紧模式
        try:
            import config as _cfg
            if bool(getattr(_cfg, 'ENABLE_WIKI_TRIGGER_STRICT', False)):
                # ★第五批任务2A：放宽实体定义类阈值至 ≤30字 / 标点≤2
                if len(_q) > 30:
                    return False, f"A-12收紧:长度>30字(len={len(_q)})"
                _verb_marks = ("内化", "反思", "验证", "提升", "增强", "学习", "处理",
                               "分析", "生成", "执行", "实现", "优化", "评估", "判断",
                               "考虑", "尝试", "调整", "是否", "应该")
                if any(_v in _q for _v in _verb_marks):
                    return False, "A-12收紧:含动词/谓词信号"
                _punct = sum(_q.count(_c) for _c in "，。？！；：、（）()——…·\"'")
                if _punct > 2:
                    return False, f"A-12收紧:标点>2(={_punct})"
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.WikiQuerier::_should_query_reason L331")
        # 时效/方法类 → 不是实体查询
        if any(_k in _q for _k in ("最新", "新闻", "今天", "现在", "动态", "热点",
                                   "怎么", "如何", "教程", "步骤")):
            return False, "时效/方法类请求"
        # 实体问句形态
        _m = re.search(r'什么是(.{2,20}?)？?$', _q)
        if _m:
            return True, ""
        _m = re.search(r'^(.{2,20}?)是什么', _q)
        if _m:
            return True, ""
        if ("的定义" in _q or "简介" in _q or "百科" in _q):
            return True, ""
        # 引号内名词
        if re.search(r'[“「『\'](.{2,20})[”」』\']', _q):
            return True, ""
        # 短名词短语（≤12字、无动词性词尾、至少含2个汉字）
        _zh = len(re.findall(r'[\u4e00-\u9fff]', _q))
        if 2 <= len(_q) <= 12 and _zh >= 2 and not re.search(r'(吗|呢|吧|了|的)$', _q):
            return True, ""
        return False, "非实体查询形态"

    def get_stats(self) -> dict[str, Any]:
        _hit_rate = (self._stats["cache_hits"] / self._stats["query_total"]
                     if self._stats["query_total"] else 0.0)
        return {**self._stats, "cache_hit_rate": round(_hit_rate, 2),
                "mem_cache_size": len(self._mem_cache)}

    # ========== 内部实现 ==========

    def _cache_path(self, keyword: str) -> str:
        return os.path.join(_CACHE_DIR, f"{hashlib.md5(keyword.encode('utf-8')).hexdigest()}.json")

    def _get_cache(self, keyword: str) -> WikiResult | None:
        with self._cache_lock:
            _r = self._mem_cache.get(keyword)
            if _r is not None:
                if time.time() - _r.fetched_at < self._cache_ttl:
                    return _r
                self._mem_cache.pop(keyword, None)
        # 文件缓存
        try:
            _path = self._cache_path(keyword)
            if os.path.exists(_path):
                _d = safe_read_json(_path, default={})
                if time.time() - float(_d.get("fetched_at", 0)) < self._cache_ttl:
                    _r = WikiResult(**_d)
                    with self._cache_lock:
                        self._mem_cache[keyword] = _r
                    return _r
        except Exception as _e:
            self._log(f"缓存读取失败(按未命中处理): {_e}")
        return None

    def _throttle(self):
        """★P2-85：进程内请求节流——保证同一实例连续请求间隔 ≥ 最小间隔。

        开关关闭时 _get_min_interval() 返回 0.0，本方法只更新时间戳不 sleep，
        与改造前行为一致（零回归）。线程安全。
        """
        _interval = _get_min_interval()
        with self._throttle_lock:
            if _interval > 0.0 and self._last_fetch_ts > 0.0:
                _wait = _interval - (time.time() - self._last_fetch_ts)
                if _wait > 0:
                    self._stats["rate_limited"] += 1
                    time.sleep(_wait)
            self._last_fetch_ts = time.time()

    # ========== ★相关任务：域级冷却 ==========
    _DOMAIN_BACKOFF_SCHEDULE = (1800.0, 7200.0, 21600.0)  # 30min / 2h / 6h

    def _domain_of_url(self, url: str) -> str:
        """从 URL 提取域名（小写）作为冷却键。"""
        try:
            from urllib.parse import urlparse
            return (urlparse(url).netloc or "unknown").lower()
        except Exception as e:
            silent_exc(e, "WikiQuerier:_domain_of_url:URL解析异常", level="warning")
            return "unknown"

    def _domain_in_cooldown(self, domain: str) -> bool:
        """域是否处于冷却期（冷却期内不发起请求，直接 fallback，避免封禁升级）。"""
        if not _rate_limit_enabled():
            return False
        with self._domain_cooldown_lock:
            _until = self._domain_cooldown_until.get(domain, 0.0)
        return time.time() < _until

    @staticmethod
    def _http_retry_after(he) -> "float | None":
        """解析 HTTP 响应头 Retry-After（秒数或 HTTP 日期），无则 None。"""
        try:
            _ra = getattr(he, "headers", None)
            if not _ra:
                return None
            _val = _ra.get("Retry-After")
            if not _val:
                return None
            _val = str(_val).strip()
            if _val.isdigit():
                return float(_val)
            from email.utils import parsedate_to_datetime
            try:
                _dt = parsedate_to_datetime(_val)
                return max(0.0, _dt.timestamp() - time.time())
            except Exception as e:
                silent_exc(e, "WikiQuerier:_http_retry_after:日期解析异常", level="warning")
                return None
        except Exception as e:
            silent_exc(e, "WikiQuerier:_http_retry_after:解析异常", level="warning")
            return None

    def _domain_trigger_cooldown(self, domain: str, retry_after: "float | None" = None):
        """被拒时触发/升级域冷却：默认按退避档（30min→2h→6h），有 Retry-After 取较大值。"""
        if not _rate_limit_enabled():
            return
        with self._domain_cooldown_lock:
            _lvl = min(self._domain_backoff_level.get(domain, 0),
                       len(self._DOMAIN_BACKOFF_SCHEDULE) - 1)
            _sched = self._DOMAIN_BACKOFF_SCHEDULE[_lvl]
            _now = time.time()
            _wait = max(_sched, float(retry_after)) if retry_after else _sched
            self._domain_cooldown_until[domain] = _now + _wait
            self._domain_backoff_level[domain] = min(
                _lvl + 1, len(self._DOMAIN_BACKOFF_SCHEDULE) - 1)
            _logger.warning(
                f"[WikiQuerier][T-131d] 域 {domain} 触发冷却 {_wait / 60:.0f}min"
                f"（退避档={_lvl}，Retry-After={retry_after}）")

    def _set_cache(self, keyword: str, result: WikiResult):
        with self._cache_lock:
            self._mem_cache[keyword] = result
            if len(self._mem_cache) > 200:  # 内存薄缓存上限（文件才是主缓存）
                self._mem_cache.pop(next(iter(self._mem_cache)))
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            _path = self._cache_path(keyword)
            _tmp = _path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump({"keyword": result.keyword, "title": result.title,
                           "summary": result.summary, "url": result.url,
                           "source": result.source, "fetched_at": result.fetched_at},
                          f, ensure_ascii=False)
            os.replace(_tmp, _path)
        except Exception as _e:
            self._log(f"缓存写盘失败(已忽略): {_e}")

    @staticmethod
    def _strip_tags(text: str) -> str:
        return re.sub(r"<[^>]+>", "", str(text)).replace("&nbsp;", " ").strip()

    def _log(self, msg: str):
        try:
            self._log_fn(f"[百科查询器] {msg}")
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.WikiQuerier::_log L488")


# ========== 模块级单例（供控制器等器官接线使用） ==========

_shared_querier: WikiQuerier | None = None
_shared_lock = threading.Lock()


def get_shared_wiki_querier(log_fn=None) -> WikiQuerier:
    global _shared_querier
    with _shared_lock:
        if _shared_querier is None:
            _shared_querier = WikiQuerier(log_fn=log_fn)
        return _shared_querier
