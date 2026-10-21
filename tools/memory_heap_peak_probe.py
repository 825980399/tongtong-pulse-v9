# -*- coding: utf-8 -*-
"""memory_heap_peak_probe.py —— heap 单日峰值测量脚本（338 批刀2 验收⑦）

口径（写死，别在报告里另说一套）：

- **RSS** = `psutil.Process().memory_info().rss`（进程常驻）
- **镜像 image_mb** = `memory_maps()` 各段 rss 之和（装载即驻、**运行中释放不掉**）
- **heap_mb** = `max(0, RSS - image_mb)`（私有堆 = 真正可释放的靶面）
- 取不到 image（无 psutil / 无权限）⇒ `heap_mb=None`，**不填 0**
  （填 0 会让 `heap=RSS` 全量，把治理靶面凭空放大 4 倍）

本脚本只**读**。不写 data/、不改 config、不动框架。

用法：
    python tools/memory_heap_peak_probe.py --out tmp/p338_root/heap_peak.json
    python tools/memory_heap_peak_probe.py --self-test          # 验证脚本自身可测
    python tools/memory_heap_peak_probe.py --samples 1440 --interval 60   # 一天
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 采样间隔下限（秒）。低于 30s 的采样对"单日峰值"没有信息增益，只会把日志刷满。
MIN_INTERVAL_SEC = 30.0


def _proc_gen() -> str:
    """代次唯一取数口（不自己拼 pid，取不到记 unknown 不填 0）。"""
    try:
        from nucleus.proc_gen import proc_gen_str
        return str(proc_gen_str())
    except Exception as _e:
        import logging
        logging.getLogger("pulse.memory_heap_peak_probe").warning(
            "[静默异常可见化] memory_heap_peak_probe::_proc_gen %s: %s",
            type(_e).__name__, _e)
        return "unknown"


def sample_once() -> dict:
    """采一次样。任何一项取不到记 None（不填 0）。"""
    _rss = None
    _image = None
    try:
        import psutil
        _p = psutil.Process()
        _rss = round(_p.memory_info().rss / (1024.0 * 1024.0), 1)
        _tot = 0
        for _mm in _p.memory_maps():
            _tot += int(getattr(_mm, "rss", 0) or 0)
        _image = round(_tot / (1024.0 * 1024.0), 1)
    except Exception as _e:
        import logging
        logging.getLogger("pulse.memory_heap_peak_probe").debug(
            "[静默异常可见化] memory_heap_peak_probe::sample_once %s: %s",
            type(_e).__name__, _e)
    _heap = None
    if _rss is not None and _image is not None:
        _heap = round(max(0.0, float(_rss) - float(_image)), 1)
    return {"ts": time.time(), "rss_mb": _rss, "image_mb": _image, "heap_mb": _heap}


def summarize(samples: list, *, proc_gen: str = "unknown") -> dict:
    """由采样序列算出峰值与判定（纯函数，便于自测）。"""
    from nucleus.mnemosyne.release_policy import (degrade_verdict,
                                                  judge_pass_line)

    _heaps = [s.get("heap_mb") for s in samples if isinstance(s.get("heap_mb"), (int, float))]
    _rsss = [s.get("rss_mb") for s in samples if isinstance(s.get("rss_mb"), (int, float))]
    _heap_peak = max(_heaps) if _heaps else None
    _rss_peak = max(_rsss) if _rsss else None
    # 期末 RSS = **最后一次采到**的 RSS（不是峰值）。取不到就 None。
    _rss_end = None
    for _s in reversed(samples):
        if isinstance(_s.get("rss_mb"), (int, float)):
            _rss_end = _s.get("rss_mb")
            break
    return {
        "version": "338.heap.p1",
        "proc_gen": proc_gen,
        "sample_count": len(samples),
        "heap_samples": len(_heaps),
        "heap_peak_mb": _heap_peak,
        "rss_peak_mb": _rss_peak,
        "rss_end_mb": _rss_end,
        "degrade": degrade_verdict(_rss_end, _heap_peak),
        "pass_line": judge_pass_line(_rss_end, _heap_peak),
        "first_ts": (samples[0].get("ts") if samples else None),
        "last_ts": (samples[-1].get("ts") if samples else None),
    }


def _write(path: str, payload: dict) -> None:
    _d = os.path.dirname(path)
    if _d and not os.path.exists(_d):
        os.makedirs(_d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _self_test() -> int:
    """自测：不依赖 psutil，喂合成序列验证"峰值/期末/判定"三件事都算对。

    这一步的意义：验收硬线是"单日 heap 峰值 ≤2.0GB"，如果测量脚本自己算错峰值，
    那么无论治理做得多好，结论都是假的。先证脚本可测，再谈治理效果。
    """
    _ok = 0
    _bad = []

    def chk(tag, cond, detail=""):
        nonlocal _ok
        if cond:
            _ok += 1
        else:
            _bad.append(tag + " " + detail)
        print("%-48s %s %s" % (tag, "PASS" if cond else "FAIL", detail))

    # 场景1：heap 峰值 1900 < 2000，RSS 期末 11000 < 12288 ⇒ pass
    _s1 = [{"ts": 1, "rss_mb": 9000, "image_mb": 7200, "heap_mb": 1800},
           {"ts": 2, "rss_mb": 11200, "image_mb": 9300, "heap_mb": 1900},
           {"ts": 3, "rss_mb": 11000, "image_mb": 9200, "heap_mb": 1800}]
    _r1 = summarize(_s1, proc_gen="pid1@t")
    chk("峰值取 max 而非末值", _r1["heap_peak_mb"] == 1900, "peak=%s" % _r1["heap_peak_mb"])
    chk("期末 RSS 取最后一次", _r1["rss_end_mb"] == 11000, "end=%s" % _r1["rss_end_mb"])
    chk("场景1 判定线 pass", _r1["pass_line"]["verdict"] == "pass",
        "verdict=%s" % _r1["pass_line"]["verdict"])

    # 场景2：heap 峰值 2100 > 2000 ⇒ fail（即使 RSS 好看）
    _s2 = [{"ts": 1, "rss_mb": 9000, "image_mb": 6900, "heap_mb": 2100},
           {"ts": 2, "rss_mb": 9500, "image_mb": 7400, "heap_mb": 2100}]
    _r2 = summarize(_s2)
    chk("场景2 RSS 好看但 heap 破线 ⇒ fail",
        _r2["pass_line"]["verdict"] == "fail" and _r2["pass_line"]["heap_ok"] is False,
        "verdict=%s why=%s" % (_r2["pass_line"]["verdict"], _r2["pass_line"]["why"]))

    # 场景3：降级线双轨——RSS 触线
    _r3 = summarize([{"ts": 1, "rss_mb": 10500, "image_mb": 9700, "heap_mb": 800}])
    chk("场景3 降级 RSS 轨触发", _r3["degrade"]["triggers"] == ["rss"],
        "triggers=%s" % _r3["degrade"]["triggers"])
    # 场景4：降级线双轨——heap 触线
    _r4 = summarize([{"ts": 1, "rss_mb": 8000, "image_mb": 5400, "heap_mb": 2600}])
    chk("场景4 降级 heap 轨触发", _r4["degrade"]["triggers"] == ["heap"],
        "triggers=%s" % _r4["degrade"]["triggers"])

    # 场景5：image 取不到 ⇒ heap=None ⇒ 判定 unavailable（不得判 pass）
    _r5 = summarize([{"ts": 1, "rss_mb": 9000, "image_mb": None, "heap_mb": None}])
    chk("场景5 无 image ⇒ unavailable 不判 pass",
        _r5["pass_line"]["verdict"] == "unavailable",
        "verdict=%s" % _r5["pass_line"]["verdict"])

    print("\nSELF-TEST PASS %d / %d" % (_ok, _ok + len(_bad)))
    return 0 if not _bad else 1


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="heap 单日峰值测量（只读）")
    _ap.add_argument("--out", default="tmp/p338_root/heap_peak.json")
    _ap.add_argument("--samples", type=int, default=1)
    _ap.add_argument("--interval", type=float, default=MIN_INTERVAL_SEC)
    _ap.add_argument("--self-test", action="store_true")
    _a = _ap.parse_args(argv)

    if _a.self_test:
        return _self_test()

    _n = max(1, int(_a.samples))
    _iv = max(0.0, float(_a.interval))
    _samples = []
    for _i in range(_n):
        _samples.append(sample_once())
        if _i + 1 < _n and _iv > 0:
            time.sleep(_iv)
    _payload = summarize(_samples, proc_gen=_proc_gen())
    _payload["samples"] = _samples
    _payload["interval_sec"] = _iv
    _payload["note"] = ("heap_mb = max(0, RSS - 镜像)；镜像取不到则 heap=None（不填 0）。"
                        "判定线双写：期末RSS<12GB ∧ heap单日峰值<2.0GB。")
    _write(_a.out, _payload)
    print(json.dumps({k: _payload[k] for k in
                      ("proc_gen", "sample_count", "heap_peak_mb", "rss_end_mb")},
                     ensure_ascii=False))
    print("degrade=%s" % json.dumps(_payload["degrade"], ensure_ascii=False))
    print("pass_line=%s" % json.dumps(_payload["pass_line"], ensure_ascii=False))
    print("written: %s" % _a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
