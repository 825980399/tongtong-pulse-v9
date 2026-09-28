# -*- coding: utf-8 -*-
"""★第148批 阶段四：金标9类场景真实 LLM 对话验证（live_gold9_148）。

设计：
  - 复用框架真实适配器 OpenAICompatibleAdapter + 真实渠道配置
    config.REMOTE_API_CHANNELS（API Key 取自环境变量，与 PulseLung 生产路径一致），
    对金标9类场景逐一**直连真实 LLM**，验证真实链路端到端可用。
  - 与离线金标 test_ir_golden_t146.py（路由/结构基线）互为印证：
      本脚本证「真实链路通、真实密钥可用、返回结构合法」；
      离线金标证「路由/结构未漂移」。
  - 记录推理延迟、token 用量、进程内存占用；检查未渲染占位符泄漏。

安全：
  - 仅做只读 completion 调用，不触发任何写盘 / 外部消息推送；
  - SSRF 守卫复用框架实现（is_safe_http_url，fail-closed）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import re  # noqa: E402

import config  # noqa: E402
from nucleus._silent_except import silent_exc  # noqa: E402
from nucleus.llm.adapter_registry import get_adapter_registry  # noqa: E402
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter  # noqa: E402

# 金标9类场景（与 test_ir_golden_t146.KEY_ROUTES 对齐，加 identity 共 9 类）
GOLD_SCENARIOS = [
    ("simple", "你是谁"),
    ("explicit_search", "搜索一下量子计算最新进展"),
    ("multi_step", "请分三步说明如何优化这个系统的内存占用"),
    ("knowledge_boundary", "请说明XYZ9未知协议的内部实现细节"),
    ("calc", "12+34等于多少"),
    ("deep_search", "深度搜索一下 ogbn-arxiv 的 SOTA"),
    ("qica", "归纳心跳 GLiNER2 与 Alibaba 两个案例的共同规律"),
    ("pipeline", "请比较 A 方案和 B 方案的异同并给出结论"),
    ("identity", "你叫什么名字？能简单介绍一下你自己吗？"),
]

_UNRENDERED = re.compile(r"<[A-Z_]{2,32}>")

# 优先顺序：永久免费不限量 > 稳定免费 > 收费兜底
_PRIORITY_NAMES = ("zhipu", "ark-ds-v4-flash", "deepseek", "ark-seed-21-pro")


def _pick_channel() -> dict | None:
    chans = config.REMOTE_API_CHANNELS.get("default_channels", []) or []
    by_name = {c.get("name"): c for c in chans}
    for name in _PRIORITY_NAMES:
        ch = by_name.get(name)
        if ch and ch.get("enabled") and ch.get("api_key"):
            return ch
    for ch in chans:  # 兜底：任意非空 key 的渠道
        if ch.get("enabled") and ch.get("api_key"):
            return ch
    return None


def _ssrf_ok(url: str) -> bool:
    try:
        from nucleus.ssrf_guard import is_safe_http_url
        ok, _reason = is_safe_http_url(url)
        return bool(ok)
    except Exception as _e:
        silent_exc(_e, where="live_gold9_148._ssrf_ok")
        return False


def _rss_mb() -> float | None:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception as _e:
        silent_exc(_e, where="live_gold9_148._rss_mb")
        return None


def main() -> int:
    ch = _pick_channel()
    if ch is None:
        print("[FAIL] 无可用真实渠道（api_key 为空），跳过真实对话验证。")
        return 2
    print(f"[OK] 选用真实渠道: {ch['name']} model={ch['model']} url={ch['api_url']}")
    # 预热注册表（与 PulseLung 同路径，确保适配器可达）
    _ = get_adapter_registry()
    adapter = OpenAICompatibleAdapter()
    base_rss = _rss_mb()
    print(f"[INFO] 起始进程内存: {base_rss:.1f} MB" if base_rss else "[INFO] 内存监控不可用")

    results = []
    for cid, q in GOLD_SCENARIOS:
        if not _ssrf_ok(ch["api_url"]):
            print(f"[FAIL] {cid}: SSRF 守卫拒绝 {ch['api_url']}")
            results.append((cid, "ssrf_blocked", None, 0.0, 0))
            continue
        messages = [{"role": "user", "content": q}]
        req_kwargs = {"temperature": 0.7, "max_tokens": 512}
        payload = adapter.build_request(ch["model"], messages, **req_kwargs)
        headers = adapter.build_headers(ch["api_key"])
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            ch["api_url"], data=body, headers=headers, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=ch.get("timeout", 60)) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            dt = time.time() - t0
            data = json.loads(raw)
            answer = adapter.parse_response(data)
            usage = adapter.extract_usage(data) or {}
            if not answer:
                print(f"[WARN] {cid}: 合法响应但 content 为空 (dt={dt:.2f}s)")
                results.append((cid, "empty", None, dt, usage.get("total_tokens", 0)))
                continue
            leak = bool(_UNRENDERED.search(answer))
            status = "leak" if leak else "ok"
            tok = usage.get("total_tokens", 0)
            print(f"[{status.upper()}] {cid}: dt={dt:.2f}s tokens={tok} "
                  f"ans={answer[:48]!r}")
            results.append((cid, status, answer, dt, tok))
        except urllib.error.HTTPError as he:
            try:
                eb = he.read().decode("utf-8", "ignore")[:200]
            except Exception as _e:
                silent_exc(_e, where="live_gold9_148.main.http_body")
                eb = ""
            print(f"[FAIL] {cid}: HTTP {he.code} {eb}")
            results.append((cid, f"http{he.code}", None, time.time() - t0, 0))
        except Exception as exc:  # noqa: BLE001
            silent_exc(exc, where="live_gold9_148.main")
            print(f"[FAIL] {cid}: {type(exc).__name__}: {exc}")
            results.append((cid, "err", None, time.time() - t0, 0))

    end_rss = _rss_mb()
    peak = f"{end_rss:.1f} MB" if end_rss else "N/A"
    ok = sum(1 for r in results if r[1] == "ok")
    leak = sum(1 for r in results if r[1] == "leak")
    fail = sum(1 for r in results if r[1] not in ("ok", "leak"))
    total_tokens = sum(r[4] for r in results)
    avg_dt = (sum(r[3] for r in results) / len(results)) if results else 0.0
    print("=== 汇总 ===")
    print(f"场景数={len(results)} 真实回答OK={ok} 占位符泄漏={leak} 失败={fail}")
    print(f"累计token={total_tokens} 平均延迟={avg_dt:.2f}s 结束内存={peak}")
    if fail or leak:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
