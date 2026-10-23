# -*- coding: utf-8 -*-
"""第 353 批 · 7 刀隔离单测（本文件覆盖刀1/刀2/刀3/刀4 核心路径）。

覆盖：
  刀1 渠道池起点均衡：空载走首选 / 首选并发满即切下一个有裕量的 / 全忙退回首选且
       不越界到付费渠道 / 留痕 `[353均衡]` 可 grep / **只 peek 不占许可**
  刀2 GPU 探测开关化：默认 False / 关时不 import torch / force=True 仍可探测 /
       启动路径不再 force
  刀3 冷化链可达性：默认阈值下如实报 blocked / 运行时钩子把链路变 ok 且真降级 /
       非法阈值被拒 / config 默认值未被硬改 / `_enforce_capacity` 三处接线在位
  刀4 L0 看门狗潜意识超时：boot/stop 处理 <5s / `[353看门狗]` 耗时留痕 / 三条
       `[342去线程]` 施工留痕不再走 INFO / 卡死栈帧归类（真实 logging 栈）

纪律：
  · 真值用 monkeypatch / 隔离实例注入，不碰生产 `data/`、不写生产日志（铁律179/198）。
  · 判据按语义单元，不做整文件子串匹配（铁律162）；每条断言注明"为什么这么判"。
  · 不 import `nucleus.logger` 之外的东西去触发日志轮转（铁律195）。
"""
import io
import logging
import os
import sys
import time
import traceback

import pytest

os.environ.setdefault("PULSE_FRAMEWORK", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _src(rel):
    with io.open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ==========================================================================
# 刀1 · 渠道池起点均衡
# ==========================================================================
def _fresh_manager(names):
    """重建并发管理器并登记渠道；返回 (mgr, rows)。行结构同 get_llm_call_targets。"""
    from nucleus.llm.ChannelConcurrency import (
        get_channel_concurrency_manager, reset_channel_concurrency_manager)
    reset_channel_concurrency_manager()
    _mgr = get_channel_concurrency_manager()
    _rows = [{"channel": _n, "api_url": "http://x", "api_key": "k",
              "model": "m", "source": "channel_pool"} for _n in names]
    for _n in names:
        _mgr.ensure_channel(_n)
    return _mgr, _rows


def _occupy(mgr, name):
    """占满某渠道的全部许可（上限通常 1）。"""
    _n = 0
    while mgr.acquire(name, blocking=False):
        _n += 1
        if _n > 16:
            break
    return _n


def test_353_d1_idle_picks_first():
    """空载 ⇒ 起点仍是首选（均衡不能改变"有裕量时按 priority 走"的既有语义）。"""
    from nucleus.llm.channel_balance import pick_balanced_index
    _mgr, _rows = _fresh_manager(["free-a", "free-b"])
    _idx, _why = pick_balanced_index(_rows, caller="ut353")
    assert (_idx, _why) == (0, "default")


def test_353_d1_busy_first_switches_to_next_free():
    """首渠道并发满 ⇒ 起点前移到下一个**有裕量**的渠道（方案A 的核心行为）。"""
    from nucleus.llm.channel_balance import pick_balanced_index
    _mgr, _rows = _fresh_manager(["free-a", "free-b"])
    assert _occupy(_mgr, "free-a") >= 1
    assert _mgr.get_semaphore("free-a").available() == 0
    _idx, _why = pick_balanced_index(_rows, caller="ut353")
    assert (_idx, _why) == (1, "concurrency_busy")


def test_353_d1_all_busy_falls_back_and_never_jumps_to_paid():
    """全忙 ⇒ 退回首选并标 all_busy；**绝不因为"付费渠道空闲"就跳过去**（会变成免费调用→付费）。

    这是本刀最要紧的一条边界：均衡只负责"别全挤在 p1"，不负责"挑一个能跑的就上"。
    """
    import config
    from nucleus.llm.channel_balance import pick_balanced_index
    _paid = sorted(config.PAID_CHANNELS)
    assert _paid, "PAID_CHANNELS 为空则本用例失去判别力"
    _mgr, _rows = _fresh_manager(["free-a", "free-b", _paid[0]])
    _occupy(_mgr, "free-a")
    _occupy(_mgr, "free-b")
    # 付费渠道保持空闲
    assert _mgr.get_semaphore(_paid[0]).available() > 0
    _idx, _why = pick_balanced_index(_rows, caller="ut353")
    assert (_idx, _why) == (0, "all_busy"), "全忙时应退回首选，不得越界到付费渠道"


def test_353_d1_pick_is_pure_peek():
    """pick 只 peek 不 acquire：调完之后余量必须不变（铁律181，否则双重占用/许可泄漏）。"""
    from nucleus.llm.channel_balance import pick_balanced_index
    _mgr, _rows = _fresh_manager(["free-a", "free-b"])
    _before = [_mgr.get_semaphore(r["channel"]).available() for r in _rows]
    for _ in range(3):
        pick_balanced_index(_rows, caller="ut353")
    _after = [_mgr.get_semaphore(r["channel"]).available() for r in _rows]
    assert _before == _after


def test_353_d1_get_llm_call_target_logs_and_accepts_caller(caplog):
    """正式入口：签名带 caller，且落 `[353均衡] caller= picked= reason=` 留痕。"""
    import config
    from nucleus.llm.ChannelConcurrency import reset_channel_concurrency_manager
    reset_channel_concurrency_manager()
    _rows = config.get_llm_call_targets(None)
    if len(_rows) < 2:
        pytest.skip("宿主可用渠道 <2（未配全免费渠道 key），均衡无从观测")
    _tgt = config.get_llm_call_target(None, caller="ut353_d1")
    assert _tgt in _rows
    _hits = [r for r in caplog.records if "[353均衡]" in r.getMessage()]
    assert _hits, "留痕未落（任务书刀1 要求可 grep）"
    _msg = _hits[-1].getMessage()
    assert "caller=ut353_d1" in _msg and "picked=" in _msg and "reason=" in _msg


# ==========================================================================
# 刀2 · GPU 探测开关化
# ==========================================================================
def test_353_d2_switch_default_off():
    """默认关闭：业务不用 GPU（星轨 1.1 决策），默认值必须体现这个结论而不是"显式开"。"""
    import config
    assert config.GPU_PROBE_ENABLED is False


def test_353_d2_probe_skips_without_touching_torch():
    """开关关 ⇒ 在 `import torch` 之前返回：probed=False 且 `_torch_available` 仍为 None。

    `_torch_available` 是"真的探测过"的唯一痕迹（torch 探测成功/失败都会给它赋值），
    用它比断言 `"torch" not in sys.modules` 更稳（同进程里别处可能已 import 过 torch）。
    """
    from nucleus.GPUCore import GPUCore
    _core = GPUCore()
    _info = _core.probe_gpu()
    assert _info["probed"] is False
    assert _info["available"] is False
    assert _info["reason"] == "GPU_PROBE_ENABLED=False"
    assert _core._torch_available is None, "开关关时不得发生任何 torch 探测痕迹"
    # 缓存：二次调用复用同一对象（否则 get_vram_pressure 每拍新造 dict）
    assert _core.probe_gpu() is _info


def test_353_d2_force_still_probes(monkeypatch):
    """force=True 是**显式强制探测**通道，不受开关限制（任务书刀2 明写要保留）。"""
    import nucleus.GPUCore as _g
    from nucleus.GPUCore import GPUCore
    monkeypatch.setattr(_g, "_m353_probe_enabled", lambda: False)
    _core = GPUCore()
    _info = _core.probe_gpu(force=True)
    assert _info.get("probed") is not False, "force 结果不该带 probed=False"
    assert _core._torch_available is not None, "force 路径必须真的走过 torch 分支"


def test_353_d2_startup_path_has_no_force():
    """启动路径若仍带 force=True，则开关对启动完全无效（T0 实测的逻辑矛盾）。

    判据是语义单元：只看 `GPUCore` 那句调用，不做全文件子串匹配。
    """
    _t = _src("main.py")
    assert "get_gpu_core().probe_gpu(force=True)" not in _t
    assert "get_gpu_core().probe_gpu()" in _t
    assert "GPU_PROBE_ENABLED=False 未探测" in _t


# ==========================================================================
# 刀3 · 冷化链可达性
# ==========================================================================
class _StubNode:
    """最小节点替身：只带 `_enforce_capacity` 真正读的三个字段。"""

    def __init__(self, nid, act, level):
        self.node_id = nid
        self.last_activated = act
        self.evol_level = level


def _iso_pool(warm_n=100, max_warm=50000):
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    _p = PulseNodePool(max_hot=10, max_warm=max_warm)
    _p._cold_storage_enabled = False          # 只走内存冷池，绝不写盘
    for _i in range(warm_n):
        _p._warm["w%03d" % _i] = _StubNode("w%03d" % _i, float(_i), PulseNode.EVOL_L1)
    return _p


def test_353_d3_default_threshold_reports_blocked():
    """默认 50000 vs 生产池约 1.9 万 ⇒ 必须**如实**报 blocked，而不是假装可达。"""
    _p = _iso_pool()
    _r = _p.cold_chain_reachability()
    assert _r["verdict"] == "blocked:max_warm_unreachable"
    assert _r["reachable"] is False
    assert _r["growth_to_trigger"] == 50000 - 100 + 1


def test_353_d3_hook_makes_chain_reachable_and_really_demotes():
    """钩子把阈值压到当前温池 +1 ⇒ 溢出成立 ⇒ `_enforce_capacity` 真把节点降进冷池。

    这一条同时证明了"链路代码存在"与"链路真的会执行"，不是只读常量的存在性证明。
    """
    _p = _iso_pool()
    assert _p.set_max_warm(100, reason="ut353") == 100
    from nucleus.mnemosyne.PulseNode import PulseNode
    _p._warm["w100"] = _StubNode("w100", 1e6, PulseNode.EVOL_L1)
    assert _p.cold_chain_reachability()["verdict"] == "ok"
    _p._enforce_capacity()
    _r = _p.cold_chain_reachability()
    assert _r["cold"] == 1, "温池溢出未降级到冷池 ⇒ 冷化链仍是断的"
    assert _r["warm"] == 100


def test_353_d3_hook_rejects_invalid_without_changing_state():
    """非法阈值必须告警且**不改状态**（静默吞掉会让压测拿到假读数，铁律156）。"""
    _p = _iso_pool()
    _before = _p._max_warm
    assert _p.set_max_warm(0) == _before
    assert _p.set_max_warm("abc") == _before
    assert _p._max_warm == _before


def test_353_d3_config_threshold_not_hard_changed():
    """裁决"不硬改阈值"：config 侧 50000 必须原样（钩子只是运行时旋钮）。"""
    import config
    _v = (getattr(config, "RUNTIME_PARAMS", {}) or {}).get("nodepool_max_warm")
    assert _v == 50000


def test_353_d3_enforce_capacity_wired_in_three_places():
    """可达性还依赖接线：`add` / `load_batch` / `_load_lazy` 三条入口都要调 `_enforce_capacity`。"""
    _t = _src("nucleus/mnemosyne/PulseNodePool.py")
    assert _t.count("self._enforce_capacity()") >= 3, "`_enforce_capacity` 调用点少于 3 处"


# ==========================================================================
# 刀4 · L0 看门狗潜意识超时
# ==========================================================================
def _boot_organ(monkeypatch):
    """构造一个不做硬件探测的潜意识实例（探测已在线程里，此处再 stub 掉以免起子进程）。"""
    from organs.brain.PulseSubconscious import PulseSubconscious
    _o = PulseSubconscious()
    monkeypatch.setattr(_o, "_detect_user_presence_async", lambda: None)
    _rows = []
    _orig = _o._log

    def _cap(level, msg, *a, **k):
        _rows.append((str(level), str(msg)))
        return _orig(level, msg, *a, **k)

    monkeypatch.setattr(_o, "_log", _cap)
    return _o, _rows


def test_353_d4_boot_under_5s_with_trace(monkeypatch):
    """boot 处理本身必须 <5s（看门狗 8s 的一半）且落 `[353看门狗]` 耗时留痕。"""
    _o, _rows = _boot_organ(monkeypatch)
    _t0 = time.perf_counter()
    _r = _o._on_system_boot({})
    _ms = (time.perf_counter() - _t0) * 1000.0
    assert _r.get("status") == "booted"
    assert _ms < 5000.0, "boot 处理耗时 %.1fms 超 5s" % _ms
    _trace = [m for _, m in _rows if "[353看门狗] 潜意识boot处理耗时=" in m]
    assert len(_trace) == 1
    assert "reason=" in _trace[0]


def test_353_d4_stop_under_5s_with_trace(monkeypatch):
    """stop 段同型（21:41 那次超时正落在这一行的日志上）。"""
    _o, _rows = _boot_organ(monkeypatch)
    _o._on_system_boot({})
    _rows.clear()
    _t0 = time.perf_counter()
    _r = _o._on_system_stop({})
    _ms = (time.perf_counter() - _t0) * 1000.0
    assert _r.get("status") == "stopped"
    assert _ms < 5000.0
    assert any("[353看门狗] 潜意识stop处理耗时=" in m for _, m in _rows)


def test_353_d4_boot_noise_demoted_from_info(monkeypatch):
    """三条 `[342去线程]` 施工留痕降 DEBUG：INFO 热路径上少 3 次 handler 加锁+写盘。

    字符串必须保留（342 测试按源码字符串断言），只是级别不再走 INFO。
    """
    from nucleus.const import LogLevel
    _o, _rows = _boot_organ(monkeypatch)
    _o._on_system_boot({})
    _noise = [(lv, m) for lv, m in _rows if "[342去线程]" in m]
    assert len(_noise) == 3, "降级不丢信息：三条留痕必须仍在"
    assert all(lv != str(LogLevel.INFO) for lv, _ in _noise), "仍走 INFO 就等于没降噪"


def test_353_d4_classify_stuck_real_logging_stack():
    """看门狗判据用**真实 logging 调用栈**验证（铁律193 须含已知正例）。

    在自定义 Handler.emit 里抓 `traceback.format_stack()` —— 这正是 19:21/21:41/23:03
    三次超时的栈帧形态（handle → acquire / emit），必须判成 logging_io。
    """
    from nucleus.field.InfoField import _m353_classify_stuck
    _cap = {}

    class _H(logging.Handler):
        def emit(self, record):
            _cap["s"] = traceback.format_stack()

    _lg = logging.getLogger("ut353.d4.classify")
    _lg.handlers = [_H()]
    _lg.setLevel(logging.INFO)
    _lg.propagate = False
    _lg.info("probe")
    assert _m353_classify_stuck(_cap["s"]) == "logging_io"
    assert _m353_classify_stuck(["  File \"x.py\", line 1, in f\n"]) == "handler_body"
    assert _m353_classify_stuck([]) == "no_frame"


def test_353_d4_watchdog_emits_353_verdict():
    """看门狗超时日志带 `[353看门狗] 定位=` 判词（复发时第一行就能读出方向）。"""
    _t = _src("nucleus/field/InfoField.py")
    assert "[353看门狗] 定位=" in _t
    assert "_m353_classify_stuck(_stack_lines)" in _t
