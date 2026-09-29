# -*- coding: utf-8 -*-
"""
WebTimeExtractor.py —— 网页时间提取器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 从网页内容中提取时间信息
机制: 基于WebTimeExtractor类实现，包含10个核心方法
定位: 知识获取层
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any


__all__ = ["WebTimeExtractor", "get_shared_extractor"]


class WebTimeExtractor:
    """网页发布时间提取器（无状态、线程安全）。"""

    # ---- A. 结构化时间（HTML meta / time 标签 / JSON-LD）----
    _RE_META_PUB = re.compile(
        r'<meta[^>]+(?:property|name)\s*=\s*["\']'
        r'(?:article:published_time|article:modified_time|'
        r'og:published_time|pubdate|publish[-_]?date|date|'
        r'citation_publication_date|sailthru\.date|parsely-pub-date)'
        r'["\'][^>]*content\s*=\s*["\']([^"\']{6,40})["\']',
        re.IGNORECASE)
    _RE_META_REV = re.compile(  # content 出现在 name 之前的写法
        r'<meta[^>]+content\s*=\s*["\']([^"\']{6,40})["\'][^>]*'
        r'(?:property|name)\s*=\s*["\']'
        r'(?:article:published_time|article:modified_time|pubdate|'
        r'publish[-_]?date|date)["\']',
        re.IGNORECASE)
    _RE_TIME_TAG = re.compile(
        r'<time[^>]+datetime\s*=\s*["\']([^"\']{6,40})["\']', re.IGNORECASE)
    _RE_JSONLD = re.compile(
        r'"date(?:Published|Modified|Created)"\s*:\s*"([^"]{6,40})"',
        re.IGNORECASE)

    # ---- B. 绝对日期 ----
    _RE_ABS_FULL = re.compile(
        r'(?<!\d)(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?'
        r'(?:[\sT]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?')
    _RE_ABS_YM = re.compile(r'(?<!\d)(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*月?')
    _RE_ABS_SLASH = re.compile(  # 05/01/2026（美式）兜底，容易误判，置信度最低
        r'(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2})(?!\d)')

    # ---- C. 相对时间 ----
    _RE_REL = re.compile(
        r'(?<!\d)(\d{1,4})\s*(秒|分钟|分|小时|小时前|钟头|天|日|周|星期|个月|月|年)\s*前')
    _RE_YESTERDAY = re.compile(r'(昨天|昨日|前天|前日)')

    # ---- D. 上下文前缀 ----
    _RE_CTX = re.compile(
        r'(?:发布于|发表于|发布|发表|更新于|更新时间|修改于|最后更新|'
        r'posted\s+on|published\s+on|published|updated\s+on|updated|'
        r'date\s*[::])\s*[:：]?\s*'
        r'((?:20\d{2}\s*[-/年.]\s*)?\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?'
        r'(?:\s*\d{1,2}:\d{2}(?::\d{2})?)?'
        r'|20\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)')

    # 置信度常量（调用方可用 confidence 决定信任程度）
    CONF_STRUCTURED = 0.95   # meta/time/JSON-LD 结构化声明
    CONF_ABSOLUTE = 0.85     # 正文绝对日期
    CONF_CONTEXT = 0.80      # 带"发布于"等前缀的日期
    CONF_RELATIVE = 0.70     # 相对时间（依赖抓取时刻）
    CONF_WEAK = 0.50         # 美式日期等易误判格式

    # 单次扫描的最大文本长度（性能保护：只扫前 N 字符，时间一般在头部）
    MAX_SCAN = 20000

    def __init__(self, max_scan: int = MAX_SCAN, log_fn=None):
        self._max_scan = int(max_scan) if max_scan else self.MAX_SCAN
        self._log_fn = log_fn
        # 统计（供 get_stats）
        self._total_calls = 0
        self._total_hits = 0
        self._hits_by_kind: dict[str, int] = {}

    # ========== 对外主接口 ==========

    def extract(self, text: str, url: str = "") -> dict[str, Any] | None:
        """从文本中提取发布时间。

        Args:
            text: 网页 HTML 或正文文本
            url:  可选，仅用于日志追溯

        Returns:
            {"timestamp": float, "raw": str, "confidence": float,
             "kind": str, "iso": str} 或 None（未提取到）
        """
        self._total_calls += 1
        if not text or not isinstance(text, str):
            return None
        _scan = text[: self._max_scan]
        _now = time.time()

        _hit = (
            self._try_structured(_scan)
            or self._try_context(_scan, _now)
            or self._try_absolute(_scan, _now)
            or self._try_relative(_scan, _now)
        )
        if _hit is None:
            return None

        # 防御：未来时间（超过当前 +2天）大概率是误提取（如倒计时/预约时间）
        if _hit["timestamp"] > _now + 172800:
            self._log("DEBUG", f"[网页时间] 丢弃未来时间: {_hit.get('raw', '')[:40]}")
            return None
        # 防御：早于 2000 年的时间戳判为误提取
        if _hit["timestamp"] < 946684800:  # 2000-01-01
            return None

        self._total_hits += 1
        _kind = str(_hit.get("kind", "unknown"))
        self._hits_by_kind[_kind] = self._hits_by_kind.get(_kind, 0) + 1
        try:
            _hit["iso"] = datetime.fromtimestamp(_hit["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            _hit["iso"] = ""
        self._log("DEBUG", f"[网页时间] 提取成功 url={url[:60]} "
                           f"时间={_hit.get('iso','')} 置信度={_hit.get('confidence',0):.2f} "
                           f"来源={_kind}")
        return _hit

    def extract_timestamp(self, text: str, url: str = "") -> float:
        """只取时间戳（0.0 表示未提取到），供调用方最简接入。"""
        _r = self.extract(text, url)
        return float(_r["timestamp"]) if _r else 0.0

    def extract_from_html(self, html: str, url: str = "") -> dict[str, Any] | None:
        """HTML 专用入口：先结构化后正文（语义同 extract，保留独立入口便于扩展）。"""
        return self.extract(html, url)

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_calls": self._total_calls,
            "total_hits": self._total_hits,
            "hit_rate": round(self._total_hits / self._total_calls, 3) if self._total_calls else 0.0,
            "hits_by_kind": dict(self._hits_by_kind),
        }

    # ========== 内部：分层尝试 ==========

    def _try_structured(self, text: str) -> dict[str, Any] | None:
        """A. meta / time 标签 / JSON-LD（置信度最高）。"""
        for _re in (self._RE_META_PUB, self._RE_META_REV, self._RE_TIME_TAG, self._RE_JSONLD):
            _m = _re.search(text)
            if not _m:
                continue
            _ts = self._parse_iso(_m.group(1))
            if _ts:
                return {"timestamp": _ts, "raw": _m.group(1),
                        "confidence": self.CONF_STRUCTURED, "kind": "structured"}
        return None

    def _try_context(self, text: str, now: float) -> dict[str, Any] | None:
        """D. 带"发布于/更新于"等前缀的日期（语义最强，排在绝对日期之前）。"""
        _m = self._RE_CTX.search(text)
        if not _m:
            return None
        _raw = _m.group(1)
        _ts = self._parse_absolute(_raw, default_year=None)
        if not _ts:
            return None
        return {"timestamp": _ts, "raw": _raw.strip(),
                "confidence": self.CONF_CONTEXT, "kind": "context"}

    def _try_absolute(self, text: str, now: float) -> dict[str, Any] | None:
        """B. 绝对日期（YYYY-MM-DD / YYYY年M月D日）。"""
        _m = self._RE_ABS_FULL.search(text)
        if _m:
            _ts = self._parse_absolute(_m.group(0))
            if _ts:
                return {"timestamp": _ts, "raw": _m.group(0).strip(),
                        "confidence": self.CONF_ABSOLUTE, "kind": "absolute"}
        _m = self._RE_ABS_YM.search(text)
        if _m:
            _ts = self._parse_absolute(_m.group(0))
            if _ts:
                return {"timestamp": _ts, "raw": _m.group(0).strip(),
                        "confidence": self.CONF_ABSOLUTE - 0.05, "kind": "absolute_ym"}
        _m = self._RE_ABS_SLASH.search(text)
        if _m:
            _ts = self._parse_absolute(f"{_m.group(3)}-{_m.group(1)}-{_m.group(2)}")
            if _ts:
                return {"timestamp": _ts, "raw": _m.group(0).strip(),
                        "confidence": self.CONF_WEAK, "kind": "absolute_slash"}
        return None

    def _try_relative(self, text: str, now: float) -> dict[str, Any] | None:
        """C. 相对时间（X分钟/小时/天前，昨天/前天）。"""
        _m = self._RE_REL.search(text)
        if _m:
            _n = int(_m.group(1))
            _unit = _m.group(2)
            _delta = {
                "秒": 1, "分钟": 60, "分": 60, "小时": 3600, "小时前": 3600,
                "钟头": 3600, "天": 86400, "日": 86400, "周": 604800,
                "星期": 604800, "个月": 2592000, "月": 2592000, "年": 31536000,
            }.get(_unit)
            if _delta:
                return {"timestamp": now - _n * _delta, "raw": _m.group(0).strip(),
                        "confidence": self.CONF_RELATIVE, "kind": "relative"}
        _m = self._RE_YESTERDAY.search(text)
        if _m:
            _days = 1 if _m.group(1) in ("昨天", "昨日") else 2
            return {"timestamp": now - _days * 86400, "raw": _m.group(1),
                    "confidence": self.CONF_RELATIVE - 0.05, "kind": "relative_day"}
        return None

    # ========== 内部：解析 ==========

    @staticmethod
    def _parse_iso(raw: str) -> float:
        """解析 ISO8601 / 常见日期字符串（纯标准库，尽量宽容）。"""
        if not raw:
            return 0.0
        _s = raw.strip().replace("Z", "+00:00")
        # 去掉时区里的冒号差异（+08:00 → +0800，Python3.7+ 其实支持 +08:00，双保险）
        try:
            _dt = datetime.fromisoformat(_s)
            return _dt.timestamp()
        except Exception:
            pass
        for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                     "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
                     "%Y年%m月%d日", "%Y年%m月%d日 %H:%M", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(_s[:26], _fmt).timestamp()
            except Exception:
                continue
        return 0.0

    @staticmethod
    def _parse_absolute(raw: str, default_year: int | None = 2000) -> float:
        """解析"2026-05-01 / 2026年5月1日 / 2026年5月"等为时间戳。"""
        if not raw:
            return 0.0
        _s = raw.strip()
        _m = re.search(r'(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})', _s)
        if _m:
            _y, _mo, _d = int(_m.group(1)), int(_m.group(2)), int(_m.group(3))
            _h = _mi = 0
            _hm = re.search(r'(\d{1,2}):(\d{2})', _s)
            if _hm:
                _h, _mi = int(_hm.group(1)), int(_hm.group(2))
            try:
                return datetime(_y, _mo, _d, _h, _mi).timestamp()
            except Exception:
                return 0.0
        _m = re.search(r'(20\d{2})\s*[-/年.]\s*(\d{1,2})', _s)
        if _m:
            try:
                return datetime(int(_m.group(1)), int(_m.group(2)), 1).timestamp()
            except Exception:
                return 0.0
        # 无年份（上下文前缀可能只给"5月1日"）→ 用当前年兜底
        if default_year is None:
            _m = re.search(r'(\d{1,2})\s*[-/月.]\s*(\d{1,2})', _s)
            if _m:
                try:
                    _now_dt = datetime.now()
                    return datetime(_now_dt.year, int(_m.group(1)), int(_m.group(2))).timestamp()
                except Exception:
                    return 0.0
        return 0.0

    # ========== 内部：日志 ==========

    def _log(self, level: str, msg: str):
        if self._log_fn is not None:
            try:
                self._log_fn(level, msg)
            except Exception:
                pass


# ========== 模块级共享实例（与 RssCollector / WikiQuerier 同风格）==========
_extractor: WebTimeExtractor | None = None
_extractor_lock = None  # 延迟创建，避免 import threading 开销争议


def get_shared_extractor(log_fn=None, max_scan: int = WebTimeExtractor.MAX_SCAN) -> WebTimeExtractor:
    """获取共享提取器（无状态，可跨器官复用）。"""
    global _extractor, _extractor_lock
    if _extractor is None:
        try:
            import threading
            if _extractor_lock is None:
                _extractor_lock = threading.Lock()
            with _extractor_lock:
                if _extractor is None:
                    _extractor = WebTimeExtractor(max_scan=max_scan, log_fn=log_fn)
        except Exception:
            _extractor = WebTimeExtractor(max_scan=max_scan, log_fn=log_fn)
    return _extractor


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== WebTimeExtractor 自测 ===\n")
    _e = WebTimeExtractor()

    _cases = [
        ('<meta property="article:published_time" content="2026-05-01T10:00:00+08:00">', "structured"),
        ('<time datetime="2026-03-15T08:30:00Z">正文</time>', "structured"),
        ('<script type="application/ld+json">{"datePublished":"2026-01-09T12:00:00"}</script>', "structured"),
        ("发布于：2026年5月1日 10:30", "context"),
        ("本文发表于 2025-12-20，作者某某", "context"),
        ("这是一篇测试文章 2024-08-15 的内容", "absolute"),
        # "更新于"前缀命中 context（优先级高于裸绝对日期，语义更可靠）
        ("更新时间：2026年7月", "context"),
        ("3小时前", "relative"),
        ("2天前更新", "relative"),
        ("昨天 12:00 发布", "relative_day"),
        ("没有任何时间信息的纯文本", None),
        ("倒计时：2099-01-01 活动开始", None),  # 未来时间 → 丢弃
    ]
    _ok = 0
    for _txt, _expect_kind in _cases:
        _r = _e.extract(_txt, url="http://example.com/a")
        _got = _r["kind"] if _r else None
        _flag = "✅" if _got == _expect_kind else "❌"
        if _got == _expect_kind:
            _ok += 1
        _detail = f"→ {_r['iso']} 置信度{_r['confidence']:.2f}" if _r else ""
        print(f"  {_flag} 期望={_expect_kind} 实际={_got} {_detail}")

    print(f"\n统计: {_e.get_stats()}")
    assert _ok == len(_cases), f"自测失败: {_ok}/{len(_cases)}"
    print(f"✅ 自测全部通过（{_ok}/{len(_cases)}）")
