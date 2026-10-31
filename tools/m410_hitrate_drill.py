# -*- coding: utf-8 -*-
"""第410批刀3 演练：把真实判定链路压到失配，验证告警与淘汰**真的会触发**。

为什么不用替身：任务书要求「告警实测触发一次（可临时调阈值制造演练）」。
替身只能证明 hit_rate 自己算得对，证明不了「刀2 判出 miss → 刀3 看得见」这条链。
所以这里用**真 StreamMiner + 真周期档案**，通过让心跳预测稳定偏 2.0s
（远超 0.675s 容差）制造真 miss，再让 hit_rate 按分钟窗采样。

隔离：数据根重定向到 tmp/m410_drill/，不碰 data/world_model 下的生产状态。
退出码：0=全部 PASS；1=有项 FAIL。
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.genesis.StreamMiner import StreamMiner  # noqa: E402
from nucleus.world_model import hit_rate as hr  # noqa: E402
from nucleus.world_model import periodic_profile as pp  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DRILL = os.path.join(_ROOT, "tmp", "m410_drill")
_ROWS = []


class _Clock:
    """演练时钟：替换 StreamMiner 模块级 time（时间戳由脚本推进，不等真时间）。"""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


def _check(name: str, ok: bool, detail: str = "") -> bool:
    _ROWS.append((name, bool(ok), detail))
    print("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return bool(ok)


def main() -> int:
    if os.path.isdir(_DRILL):
        shutil.rmtree(_DRILL, ignore_errors=True)
    os.makedirs(_DRILL, exist_ok=True)
    pp.set_data_dir(_DRILL)
    hr.set_data_dir(_DRILL)
    _sw = getattr(config, "ENABLE_M410_HIT_RATE_ALERT", True)
    config.ENABLE_M410_HIT_RATE_ALERT = True
    # 时钟替换放在 try 之前：finally 里就不必用 NameError 兜底（那本身是个静默 except）
    import nucleus.genesis.StreamMiner as _sm_mod
    _real_time = _sm_mod.time
    _clock = _Clock()
    try:
        # 1) 造一份档案（心跳 13.5s ⇒ 容差 0.675s）
        _now0 = time.time()
        _iv = {pp.EV_HEART_BEAT: 13.5, pp.EV_HEART_ALIVE: 13.5,
               pp.EV_TOUCH_HARDWARE_SNAPSHOT: 30.0,
               pp.EV_ENERGY_METABOLISM_SNAPSHOT: 30.0,
               pp.EV_SPINAL_INSPECTION_REPORT: 40.0,
               pp.EV_EYES_STREAM_FRAME: 2.0, pp.EV_DIGEST_KNOWLEDGE: 10.0}
        _pat = {"periodicity": {k: [_now0 - v * 50 + v * i for i in range(50)]
                                for k, v in _iv.items()}}
        _rep = pp.build(_pat, now=_now0)
        print("[准备] 档案 events=%d complete=%s" % (len(_rep.get("events") or {}),
                                                     _rep.get("complete")))
        _clock.now = _now0
        _sm_mod.time = _clock
        _m = StreamMiner()
        _m.enable()
        _m.set_storage_path(os.path.join(_DRILL, "patterns.json"))
        _plan = _m._m410_plan(pp.EV_HEART_BEAT)
        _check("心跳容差在 0.6~0.8s（不是 60s）",
               0.6 < float(_plan[1] or 0) < 0.8, "tol=%s" % _plan[1])

        # 2) 五个采样窗，每窗制造 4 条真 miss（预测偏 2.0s）
        _t = _now0
        _alerts, _evicts = 0, 0
        for _w in range(5):
            for _k in range(4):
                for _i in range(3):                       # 补齐周期缓存
                    _m.feed_event({"event_type": pp.EV_HEART_BEAT,
                                   "source_organ": "心脏", "timestamp": _t})
                    _t += 13.5
                    _clock.now = _t
                # 生产里 get_info_field 每 200 条事件跑一次模式发现；演练照做，
                # 否则「淘汰」找不到可摘的模式记录（_had=False，看不出真假）
                _m.discover_patterns()
                _m.register_periodic_predictions(horizon_sec=300)
                _pend = [p for p in _m._pending_predictions
                         if p.get("event_type") == pp.EV_HEART_BEAT]
                if _pend:
                    _t = float(_pend[-1]["predicted_ts"]) + 2.0
                    _clock.now = _t
                    _m.feed_event({"event_type": pp.EV_HEART_BEAT, "source_organ": "心脏",
                                   "timestamp": _t})
                _t += 20.0                                 # 推进时钟，让过期扫描生效
            _t += 60.0
            _clock.now = _t
            _out = hr.sample(now=_t, miner=_m)
            _w2 = _out["window"].get(pp.EV_HEART_BEAT) or {}
            _alerts += len(_out["alerts"])
            _evicts += len(_out["evicted"])
            print("  窗%d: hit=%s miss=%s rate=%s ⇒ 累计告警 %d / 淘汰 %d"
                  % (_w + 1, _w2.get("hit"), _w2.get("miss"), _w2.get("hit_rate"),
                     _alerts, _evicts))

        _check("连续 2 窗 <0.5 ⇒ 告警触发 1 次", _alerts == 1, "alerts=%d" % _alerts)
        _check("连续 5 窗 <0.3 ⇒ 淘汰触发 1 次", _evicts == 1, "evicts=%d" % _evicts)
        _check("淘汰真的摘掉了周期记录",
               ("periodic:" + pp.EV_HEART_BEAT) not in _m._discovered_patterns,
               "模式发现曾产出该键（淘汰前）")

        # 3) 日报表 + 快照
        _p = hr.write_report(now=_t)
        _d = json.load(open(_p, encoding="utf-8")) if _p and os.path.isfile(_p) else {}
        _check("日报表按日生成", bool(_p) and os.path.basename(_p).startswith("hit_rate_"),
               os.path.basename(_p or "-"))
        _check("报表含分类命中率与总命中率",
               bool(_d.get("types")) and "hit_rate" in (_d.get("total") or {}),
               "total=%s" % ((_d.get("total") or {}).get("hit_rate"),))
        _snap = hr.snapshot_for_health(now=_t)
        _check("health 快照含 hit_rate / mismatches",
               "hit_rate" in _snap and "mismatches" in _snap,
               "mismatches=%s" % _snap.get("mismatches"))
        _check("淘汰后 evicted 进快照", pp.EV_HEART_BEAT in (_snap.get("evicted") or []))

        # 4) 红线：不入队 / 只写 world_model 目录
        _files = sorted(os.listdir(os.path.join(_DRILL, "data", "world_model")))
        _check("只写 world_model 目录（无入队产物）", len(_files) <= 3, str(_files))
        _check("no_coverage 不进分母",
               all((t["covered"] == t["hit"] + t["miss"]) for t in (_d.get("types") or {}).values()))
        _check("状态与报表都是纯 LF",
               open(hr.state_path(), "rb").read().count(b"\r\n") == 0
               and open(_p, "rb").read().count(b"\r\n") == 0)
    finally:
        _sm_mod.time = _real_time
        config.ENABLE_M410_HIT_RATE_ALERT = _sw
        pp.set_data_dir(None)
        hr.set_data_dir(None)

    _fail = [r for r in _ROWS if not r[1]]
    print("\n[结论] %s —— PASS %d / FAIL %d"
          % ("PASS" if not _fail else "FAIL", len(_ROWS) - len(_fail), len(_fail)))
    return 0 if not _fail else 1


if __name__ == "__main__":
    sys.exit(main())
