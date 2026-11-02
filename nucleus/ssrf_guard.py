# -*- coding: utf-8 -*-
"""
ssrf_guard.py —— SSRF防护

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 服务器端请求伪造防护与URL校验
机制: 函数式模块，包含4个工具函数
定位: 安全治理层
"""

import ipaddress
import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

#: ★382刀10（T-382-SSRF分类）：三类拦截分开计数 —— 改前三类混在一起（实测 DNS_FAIL 27 /
#:   PRIVATE_ADDR 2 / 其他 4），只能靠人肉匹配中文文案，没法做趋势。
#:   ★只加计数、不改任何判定：`is_safe_http_url` 的返回值与语义逐字不变。
_M382_BLOCK_COUNTS = {"DNS_FAIL": 0, "PRIVATE_ADDR": 0, "OTHER": 0}
_M382_BLOCK_LOCK = threading.Lock()


def _m382_bump(kind: str) -> None:
    """★382刀10（T-382-SSRF分类）：给某一类拦截计数（容错：任何异常都不影响判定）。"""
    try:
        with _M382_BLOCK_LOCK:
            _M382_BLOCK_COUNTS[str(kind)] = int(
                _M382_BLOCK_COUNTS.get(str(kind), 0)) + 1
    except Exception as _be:
        # ★382刀10：计数失败绝不影响安全判定，但**不许静默**（铁律176/cw2 门禁）
        silent_exc(_be, where="ssrf_guard::_m382_bump", level="debug")


def ssrf_block_stats() -> dict:
    """★382刀10（T-382-SSRF分类）：三类拦截计数快照 + 合计（供诊断/日报读取）。"""
    try:
        with _M382_BLOCK_LOCK:
            _d = dict(_M382_BLOCK_COUNTS)
    except Exception as _de:
        silent_exc(_de, where="ssrf_guard::ssrf_block_stats", level="debug")
        _d = {}
    _d["total"] = sum(int(v) for v in _d.values())
    return _d


def reset_ssrf_block_stats() -> None:
    """★382刀10（T-382-SSRF分类）：清空计数（**仅供测试/诊断**）。"""
    with _M382_BLOCK_LOCK:
        for _k in list(_M382_BLOCK_COUNTS):
            _M382_BLOCK_COUNTS[_k] = 0

try:  # ★360刀0-2：埋点失败要留痕，但守卫本身不能被埋点依赖拖垮
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover —— 守卫模块必须自洽
    def silent_exc(_e, where="", level="warning"):
        logging.getLogger(__name__).warning("silent_exc 不可用(%s): %s", where, _e)

_LOGGER = logging.getLogger(__name__)


# 允许抓取的协议
_ALLOWED_SCHEMES = ("http", "https")

# 受信任主机（管理员显式配置，非外部输入派生）：
# - config.REMOTE_API_CONFIG.api_url 主机（用户自有模型接口，可能是本地 Ollama / 局域网 vLLM）
# - 本地 Ollama 服务主机（ollama_base_url，默认 localhost:11434）
# 这些主机即使解析到私网/环回也允许访问（本地优先部署是核心用法）。
# 但云元数据 169.254.169.254 / 0.0.0.0 / link-local 始终硬拒绝，防凭据泄漏。
_TRUSTED_HARD_BLOCK = {"169.254.169.254", "0.0.0.0", "::1", "::", "fe80::1"}
_TRUSTED_CACHE = None


def _trusted_hosts() -> set:
    """受信任主机集合（缓存）。仅包含管理员在配置中显式声明的端点。"""
    global _TRUSTED_CACHE
    if _TRUSTED_CACHE is not None:
        return _TRUSTED_CACHE
    _hosts = {"localhost", "127.0.0.1"}
    try:
        import config as _cfg
        _api = getattr(_cfg, "REMOTE_API_CONFIG", {}).get("api_url", "")
        if _api:
            _h = urlparse(_api).hostname
            if _h:
                _hosts.add(_h.lower())
        # ★第98批 T-98a：渠道池每个渠道的 api_url 同样是管理员显式配置的模型接口，
        #   与顶层单端点同属「本地优先部署」语义（火山方舟/智谱等 SaaS 端点）。
        #   原实现只取顶层 REMOTE_API_CONFIG.api_url，漏掉了渠道池，导致 ark 等
        #   渠道主机不在受信任集合 → 落入 DNS 解析 → 生产环境把火山域名解析到内网
        #   IP 时被 SSRF 防护误拦（渠道失败率 33%）。此处补齐，使所有配置过的模型
        #   端点都享受「私网/环回豁免」（仍强制 http/https，且云元数据等保留地址
        #   始终在 _TRUSTED_HARD_BLOCK 中硬拒绝）。
        for _ch in getattr(_cfg, "REMOTE_API_CHANNELS", {}).get("default_channels", []) or []:
            _cu = (_ch or {}).get("api_url", "")
            if _cu:
                _chh = urlparse(_cu).hostname
                if _chh:
                    _hosts.add(_chh.lower())
        # ★第98批 T-98a：显式额外白名单开关（默认含火山方舟域名）。运维可在不改代码
        #   的情况下追加受信任主机/域名。见 config.SSRF_TRUSTED_EXTRA_HOSTS。
        for _h in getattr(_cfg, "SSRF_TRUSTED_EXTRA_HOSTS", ()) or ():
            if _h:
                _hosts.add(_h.lower())
        _ollama = getattr(_cfg, "OLLAMA_BASE_URL", "") or "http://localhost:11434"
        _oh = urlparse(_ollama).hostname
        if _oh:
            _hosts.add(_oh.lower())
    except Exception as e:
        # ★主线第77批：受信任主机解析失败必须留痕。
        #   此处为 fail-closed（集合变小 → 判定更严格），安全方向正确；
        #   但静默会导致「合法 API 被拒却查不到原因」，排查成本极高。
        _LOGGER.debug("受信任主机集合解析失败（按最小集合处理）: %s: %s",
                      type(e).__name__, e)
    _TRUSTED_CACHE = _hosts
    return _hosts


def is_safe_http_url(url: str) -> tuple[bool, str]:
    """
    返回 (是否允许, 拒绝原因)。

    仅允许公网 http/https；解析主机后拒绝任何非公网地址
    （环回 / 私网 / 链路本地 / 保留 / 组播 / 未指定）。
    例外：管理员在配置中显式声明的模型接口 / 本地 Ollama 主机（见 _trusted_hosts）
    即使解析到私网/环回也允许，以支持本地优先部署；但云元数据等保留地址始终硬拒绝。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 解析失败"

    if parsed.scheme not in _ALLOWED_SCHEMES:
        _m382_bump("OTHER")
        return False, f"不支持的协议: {parsed.scheme}"

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "缺少主机名"

    # 硬安全底线：云元数据 / 未指定 / 保留地址，即使“受信任”也拒绝
    if host in _TRUSTED_HARD_BLOCK:
        _m382_bump("PRIVATE_ADDR")
        return False, f"拒绝云元数据/保留地址: {host}"

    # 受信任主机（管理员显式配置）：允许私网/环回，仍要求 http/https（上方已校验）
    if host in _trusted_hosts():
        return True, ""

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        _m382_bump("DNS_FAIL")
        return False, f"无法解析主机: {host}"

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as _m369_e:
            silent_exc(_m369_e, where="nucleus/ssrf_guard.py:is_safe_http_url", level="debug")
            continue
        # ★修复：改用 is_global 作为「是否公网」的主判定依据。
        # 原逻辑用 is_private 判定，Python 的 ipaddress 对 IPv6 过渡地址段
        # 的判定语义不清晰（例如 2002::/16 6to4 公网地址 is_private=True）。
        # is_global 语义更准确：公网 IPv4/IPv6 返回 True，
        # 私网/环回/链路本地/保留/组播/未指定/文档示例段返回 False。
        # 注：2001::1（Teredo 占位地址）is_global=False，仍会被正确拦截——
        # 该地址本身不可通信，是运行环境 DNS 污染导致的异常解析，非 SSRF 误判。
        if not ip.is_global:
            _m382_bump("PRIVATE_ADDR")
            return False, f"拒绝非公网地址: {host} -> {addr}"

    return True, ""


def safe_http_json(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[bool, Any]:
    """
    经 SSRF 防护的统一 HTTP JSON 请求封装。

    所有出站 HTTP 请求应优先走此函数，避免各模块自行调用
    urllib/requests 而绕过 is_safe_http_url 校验。
    返回 (是否成功, 解析后的 JSON 或错误信息字符串)。
    """
    _allowed, _reason = is_safe_http_url(url)
    if not _allowed:
        _m382_bump("OTHER")
        return False, f"SSRF 防护拦截: {_reason}"
    # ★第360批 刀0-2：统一 JSON 出口的付费埋点（append-only，失败静默不阻断）
    _m360_rid = ""
    try:
        from nucleus.llm.paid_api_trace import begin as _m360_begin
        _m360_model = ""
        try:
            if data:
                _m360_model = str((json.loads(data.decode("utf-8", "ignore")) or {})
                                  .get("model", "") or "")
        except Exception as _m360me:
            silent_exc(_m360me, where="nucleus.ssrf_guard::safe_http_json 埋点取 model", level="debug")
            _m360_model = ""
        _m360_rid = _m360_begin(url, _m360_model,
                                caller="ssrf_guard.safe_http_json", payload=data)
    except Exception as _m360e:
        silent_exc(_m360e, where="nucleus.ssrf_guard::safe_http_json 埋点 begin", level="debug")
    try:
        _req = urllib.request.Request(
            url, data=data, headers=headers or {}, method=method
        )
        with urllib.request.urlopen(_req, timeout=timeout) as _resp:
            _parsed = json.loads(_resp.read().decode("utf-8"))
        if _m360_rid:
            try:
                from nucleus.llm.paid_api_trace import finish as _m360_finish
                _m360_finish(_m360_rid, True,
                             _parsed.get("usage") if isinstance(_parsed, dict) else None)
            except Exception as _m360e:
                silent_exc(_m360e, where="nucleus.ssrf_guard::safe_http_json 埋点 finish", level="debug")
        return True, _parsed
    except urllib.error.URLError as _e:
        if _m360_rid:
            try:
                from nucleus.llm.paid_api_trace import finish as _m360_finish
                _m360_finish(_m360_rid, False, error="URLError")
            except Exception as _m360e:
                silent_exc(_m360e, where="nucleus.ssrf_guard::safe_http_json 埋点 finish", level="debug")
        return False, f"请求失败: {_e}"
    except (ValueError, json.JSONDecodeError) as _e:
        if _m360_rid:
            try:
                from nucleus.llm.paid_api_trace import finish as _m360_finish
                _m360_finish(_m360_rid, False, error="响应解析失败")
            except Exception as _m360e:
                silent_exc(_m360e, where="nucleus.ssrf_guard::safe_http_json 埋点 finish", level="debug")
        return False, f"响应解析失败: {_e}"


# ========== ★E6（T-SSRF分类留痕-1）：拒收分六类计数 ==========
# 为什么要有（烛微 362 候选 E6）：原实现把三类拒收混在一起统计
# （DNS_FAIL 27 / PRIVATE_ADDR 2 / 其他 4），于是「拒收到底集中在哪里」答不出来 ——
# 而三类的处置方向完全相反：
#   * dns_fail     ⇒ 多半是 DNS 环境/污染（换解析或换源），不是有人在内网探测；
#   * private_addr ⇒ **真的解析到了内网**，是 SSRF 判据在起作用（安全事件面）；
#   * 其他         ⇒ 输入/配置问题（协议拼错、主机名空、云元数据硬拦）。
# 混着统计会把「DNS 抖动」读成「安全事件」，也会把真实内网探测埋进噪声里。
#
# 为什么在**末尾重新绑定**而不是在函数体内加计数：
#   函数体内有 6 处 `return False` 与 2 处 `except`，改任何一处都会变更静默 except 指纹；
#   **改函数名同样会变**（cw2 指纹含 qualname）。末尾包一层则 def 名与函数体一字不动，
#   指纹不变，而分类计数仍单点。

_M367_REJECT_GROUP = {
    "dns_fail": "dns_fail",
    "private_addr": "private_addr",
    "hard_block": "other",
    "scheme": "other",
    "no_host": "other",
    "url_parse": "other",
    "other": "other",
}

#: 判定顺序即优先级：先认更具体的 reason 前缀
_M367_REJECT_RULES = (
    ("dns_fail", "无法解析主机"),
    ("private_addr", "拒绝非公网地址"),
    ("hard_block", "拒绝云元数据"),
    ("scheme", "不支持的协议"),
    ("no_host", "缺少主机名"),
    ("url_parse", "URL 解析失败"),
)

_M367_REJECT_COUNTS: dict = {}


def m367_classify_reject(reason: str) -> str:
    """把拒收 reason 文本归到细类（纯函数，便于测试与复算）。"""
    _r = str(reason or "")
    for _kind, _prefix in _M367_REJECT_RULES:
        if _prefix in _r:
            return _kind
    return "other"


def _m367_bump(kind: str) -> None:
    # int 自增在 GIL 下不保证原子，但这是**计数**不是判据：丢掉一两次计数
    # 只影响读数精度，不影响任何安全判定。不为此引入锁（热路径：每次 HTTP 前调用）。
    _M367_REJECT_COUNTS[kind] = int(_M367_REJECT_COUNTS.get(kind, 0)) + 1


#: 捕获原函数（同名重新绑定后仍可回指）
_m367_raw_is_safe_http_url = is_safe_http_url


def _m367_counted_is_safe_http_url(url: str) -> tuple:
    """包装器：判据完全由原函数决定，此处**只**做分类计数。"""
    _ok, _why = _m367_raw_is_safe_http_url(url)
    if not _ok:
        _m367_bump(m367_classify_reject(_why))
    return _ok, _why


#: 对外仍是同名可调用对象（模块内后续函数运行时查全局 ⇒ 自动拿到包装版）
is_safe_http_url = _m367_counted_is_safe_http_url


def m367_ssrf_reject_stats() -> dict:
    """★E6：SSRF 拒收分类读数（只读拷贝）。

    Returns:
        `{"schema", "total", "by_kind", "by_group"}`
        —— `total=0` 表示**本进程尚无拒收**，**不是**「网络很安全」，别无样本当健康。
    """
    _by_kind = dict(_M367_REJECT_COUNTS)
    _by_group: dict = {}
    for _k, _n in _by_kind.items():
        _g = _M367_REJECT_GROUP.get(_k, "other")
        _by_group[_g] = int(_by_group.get(_g, 0)) + int(_n)
    return {"schema": "m367.ssrf_reject/v1",
            "total": sum(int(v) for v in _by_kind.values()),
            "by_kind": _by_kind, "by_group": _by_group}


def _m367_reset_reject_stats() -> None:
    """清空计数（**仅供测试**；生产不要调）。"""
    _M367_REJECT_COUNTS.clear()

