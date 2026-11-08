"""★479：语义编码 sidecar（**必须用 3.12 跑**）。

## 为什么需要它

3.14t 是自由线程构建，`fastembed` / `onnxruntime` 没有 cp314t wheel ⇒
`VectorEncoder` 只能走关键词通道 ⇒ 语义缓存、知识检索、经验召回全部退化。
而 3.12 里模型、ONNX、tokenizer 全都齐备（`data/models/fast-bge-small-zh-v1.5/`），
实测：加载 1.56s、编码 **0.004s**、维度 512，
与存量向量库 `data/knowledge/vectors.npz`（14899×512）最高相似 0.6525
⇒ **同一语义空间，存量向量无需重建**。

所以把「编码」这个脏活隔离到 3.12 子进程，主进程只做 IPC。

## 协议（按行 JSON，长驻）

请求（stdin，每行一个）::

    {"texts": ["文本1", "文本2"], "batch_size": 32}

响应（stdout，每行一个）::

    {"ok": true, "dim": 512, "vectors": [[...], [...]], "elapsed": 0.004}
    {"ok": false, "error": "...", "dim": 512, "vectors": []}

## 为什么长驻而不是每次 spawn

模型加载 1.56s，spawn 一次就白烧 1.5s。长驻后每批只花毫秒级。
`nucleus/semantic/bridge_encoder.py` 负责保活与超时重启。

## 用法

    python tools/semantic/encoder_sidecar.py            # 长驻，读 stdin
    python tools/semantic/encoder_sidecar.py --selftest # 自检一次就退出
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from nucleus._silent_except import silent_exc
except Exception:
    def silent_exc(e, where: str = "", level: str = "debug") -> None:  # type: ignore[misc]
        """独立运行（仓外/未装包）时的兜底：写 stderr，绝不静默。"""
        sys.stderr.write(f"[silent:{where}] {type(e).__name__}: {e}\n")


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_CACHE = "data/models"
EXPECT_DIM = 512


def _load_model(model_name: str = DEFAULT_MODEL, cache_dir: str = DEFAULT_CACHE):
    """加载本地模型。**只读本地缓存**，不联网（镜像设了也不下载）。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # 关闭 HF 的联网校验：本地已有模型时别去探远端（否则代理 502 会拖死启动）
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from fastembed import TextEmbedding
    _t0 = time.time()
    _m = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=4)
    return _m, round(time.time() - _t0, 2)


def _encode(model, texts: list[str], batch_size: int = 32) -> dict:
    _t0 = time.time()
    _vecs = [list(map(float, v)) for v in model.embed(texts, batch_size=batch_size)]
    return {
        "ok": True,
        "dim": len(_vecs[0]) if _vecs else EXPECT_DIM,
        "vectors": _vecs,
        "count": len(_vecs),
        "elapsed": round(time.time() - _t0, 4),
    }


def _serve() -> int:
    try:
        _model, _cost = _load_model()
    except Exception as _e:
        silent_exc(_e, where="encoder_sidecar::load", level="error")
        sys.stdout.write(json.dumps({
            "ok": False, "ready": False, "dim": EXPECT_DIM,
            "error": f"{type(_e).__name__}: {str(_e)[:200]}",
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 1

    # 首行是 ready 握手（客户端据此判断是否可用）
    sys.stdout.write(json.dumps({
        "ok": True, "ready": True, "dim": EXPECT_DIM,
        "model": DEFAULT_MODEL, "load_cost": _cost,
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    sys.stderr.write(f"[encoder_sidecar] ready model={DEFAULT_MODEL} "
                     f"load={_cost}s dim={EXPECT_DIM}\n")

    for _line in sys.stdin:
        _line = _line.strip()
        if not _line:
            continue
        if _line in ('{"cmd":"exit"}', "exit"):
            break
        try:
            _req = json.loads(_line)
            _texts = _req.get("texts") or []
            _bs = int(_req.get("batch_size") or 32)
            if not isinstance(_texts, list) or not _texts:
                _resp = {"ok": False, "dim": EXPECT_DIM, "vectors": [],
                         "error": "texts 为空或非数组"}
            else:
                _resp = _encode(_model, [str(t) for t in _texts], _bs)
        except Exception as _e:
            silent_exc(_e, where="encoder_sidecar::handle", level="warning")
            _resp = {"ok": False, "dim": EXPECT_DIM, "vectors": [],
                     "error": f"{type(_e).__name__}: {str(_e)[:200]}"}
        try:
            sys.stdout.write(json.dumps(_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as _e:
            silent_exc(_e, where="encoder_sidecar::write", level="error")
            return 1
    return 0


def _selftest() -> int:
    try:
        _model, _cost = _load_model()
        _r = _encode(_model, ["曈曈的语义内核如何工作", "hello world"])
    except Exception as _e:
        silent_exc(_e, where="encoder_sidecar::selftest", level="error")
        print(json.dumps({"ok": False, "error": f"{type(_e).__name__}: {str(_e)[:200]}"},
                         ensure_ascii=False))
        return 1
    print(json.dumps({
        "ok": _r["ok"], "dim": _r["dim"], "count": _r["count"],
        "load_cost": _cost, "encode_cost": _r["elapsed"],
    }, ensure_ascii=False))
    return 0 if _r["ok"] else 1


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return _selftest()
    return _serve()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
