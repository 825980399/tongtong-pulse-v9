#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""免费/低价模型渠道连通性探针（★478批）。

用途：在不改 config、不动运行中进程的前提下，验证某平台的 API Key 是否可用、
有哪些模型可调。用于「**先验证、后加池**」，避免把 401 的死渠道塞进渠道池。

用法：
  python tools/probe_free_channels.py --provider all
  python tools/probe_free_channels.py --provider modelscope --list
  python tools/probe_free_channels.py --provider qianfan --model ernie-speed-8k
  python tools/probe_free_channels.py --provider hunyuan --model hunyuan-lite

说明：
- 只读环境变量，**不打印密钥明文**（最多前3后3）。
- 只用标准库 urllib，3.12 / 3.14t 都能跑。
- 每次 chat 只花 max_tokens=16，成本可忽略。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 统一可见化：优先项目内 silent_exc（cw2 门禁认它）；独立运行兜底等价实现。
try:
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from nucleus._silent_except import silent_exc
except Exception:  # 独立运行兜底（不静默：写 stderr）
    def silent_exc(e, where="", level="debug"):  # type: ignore[misc]
        sys.stderr.write(f"[probe] {where} {type(e).__name__}: {e}\n")


_PROVIDERS: dict[str, dict] = {
    "modelscope": {
        "env": "MODELSCOPE_API_KEY",
        "base": "https://api-inference.modelscope.cn/v1",
        "default_model": "",   # 需先 --list 探明
        "note": "魔搭 API-Inference，token 形如 ms-xxx",
    },
    "qianfan": {
        "env": "QIANFAN_API_KEY",
        "base": "https://qianfan.baidubce.com/v2",
        "default_model": "ernie-speed-8k",
        "note": "百度千帆 v2（OpenAI 兼容），Key 形如 bce-v3/ALTAK-...",
    },
    "hunyuan": {
        "env": "HUNYUAN_API_KEY",
        "base": "https://api.hunyuan.cloud.tencent.com/v1",
        "default_model": "hunyuan-lite",
        "note": "腾讯混元（OpenAI 兼容）；Key 用的是 **TokenHub 的 sk-**，不是 CAM 密钥",
    },
    "tokenhub": {
        "env": "TOKENHUB_API_KEY",
        "base": "https://tokenhub.tencentmaas.com/v1",
        "default_model": "",   # 需先 --list 探明
        "note": "腾讯云 TokenHub（广州节点）；Key 形如 sk-",
    },
}


def _mask(key: str) -> str:
    if not key:
        return "(未设置)"
    if len(key) <= 6:
        return "***"
    return key[:3] + "***" + key[-3:]


def _req(url: str, key: str, payload: dict | None = None, timeout: float = 25.0):
    """发请求，返回 (status, body_text)。不抛异常。"""
    _data = None
    _headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    _method = "GET"
    if payload is not None:
        _data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        _method = "POST"
    _rq = urllib.request.Request(url, data=_data, headers=_headers, method=_method)
    try:
        with urllib.request.urlopen(_rq, timeout=timeout) as _r:
            return int(getattr(_r, "status", 200)), _r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as _e:
        try:
            _b = _e.read().decode("utf-8", "replace")
        except Exception as _e_read:
            silent_exc(_e_read, where="probe::_req.read", level="debug")
            _b = ""
        return int(_e.code), _b
    except Exception as _e:
        silent_exc(_e, where="probe::_req", level="debug")
        return -1, f"{type(_e).__name__}: {str(_e)[:200]}"


def _list_models(name: str, spec: dict, key: str) -> None:
    _url = spec["base"] + "/models"
    _st, _body = _req(_url, key)
    print(f"[{name}] GET {_url} -> {_st}")
    if _st != 200:
        print("   body:", _body[:300])
        return
    try:
        _d = json.loads(_body)
        _rows = _d.get("data") if isinstance(_d, dict) else None
        if not isinstance(_rows, list):
            print("   非标准返回:", _body[:300])
            return
        _ids = [str((x or {}).get("id") or "") for x in _rows if isinstance(x, dict)]
        _ids = [i for i in _ids if i]
        print(f"   模型数={len(_ids)}")
        for _i in _ids[:40]:
            print("   -", _i)
        if len(_ids) > 40:
            print("   ...（截断）")
    except Exception as _e:
        print("   解析失败:", type(_e).__name__, _body[:200])


def _chat(name: str, spec: dict, key: str, model: str) -> bool:
    if not model:
        print(f"[{name}] 跳过 chat：未指定模型（先 --list 探明）")
        return False
    _url = spec["base"] + "/chat/completions"
    _payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复：OK"}],
        "max_tokens": 16,
    }
    _st, _body = _req(_url, key, _payload)
    print(f"[{name}] POST {_url} model={model} -> {_st}")
    if _st != 200:
        print("   body:", _body[:400])
        return False
    try:
        _d = json.loads(_body)
        _txt = ""
        _ch = _d.get("choices") if isinstance(_d, dict) else None
        if isinstance(_ch, list) and _ch and isinstance(_ch[0], dict):
            _txt = str((_ch[0].get("message") or {}).get("content") or "")
        print("   回复:", _txt[:80].replace("\n", " "))
        return True
    except Exception as _e:
        print("   解析失败:", type(_e).__name__, _body[:200])
        return False


def main() -> int:
    _ap = argparse.ArgumentParser(description="免费渠道连通性探针")
    _ap.add_argument("--provider", default="all",
                     choices=["all"] + sorted(_PROVIDERS))
    _ap.add_argument("--model", default="")
    _ap.add_argument("--list", action="store_true", help="只列模型，不 chat")
    _args = _ap.parse_args()

    _names = sorted(_PROVIDERS) if _args.provider == "all" else [_args.provider]
    _any_ok = False
    for _n in _names:
        _spec = _PROVIDERS[_n]
        _key = os.environ.get(_spec["env"], "").strip()
        print("=" * 60)
        print(f"[{_n}] {_spec['note']}")
        print(f"   env={_spec['env']}  key={_mask(_key)}  base={_spec['base']}")
        if not _key:
            print("   → 未设置，跳过")
            continue
        if _args.list or not (_args.model or _spec.get("default_model")):
            _list_models(_n, _spec, _key)
            if _args.list:
                continue
        _m = _args.model or str(_spec.get("default_model") or "")
        _any_ok = _chat(_n, _spec, _key, _m) or _any_ok
    print("=" * 60)
    print("结论:", "至少一个渠道可用" if _any_ok else "没有渠道通过 chat 验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
