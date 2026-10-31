# -*- coding: utf-8 -*-
"""第162批刀7 · 夜间编排前置通道-1：shutdown_request.json 优雅退出指令通道。

背景（烛微 162C 类 §四实测推翻 v2 两前提）：
  ① data/runtime.lock 盘上不存在且全仓无创建代码（原"检测 runtime.lock"恒判无框架）；
  ② Windows 无外部优雅 SIGTERM，无无人值守优雅退出通道。

本模块提供两类能力：
  A. NightShutdownChannel：data/shutdown_request.json 的 request/has/consume 三件套
     —— 复刻 EvolutionDriver.apply_now 同型语义（EvolutionDriver.py:261/284/296），
        但载体置于 data/ 顶层（与 data/evolution/apply_now.json 平级、独立语义）。
     外部编排脚本写指令 → main.py 主循环每拍检测 → 存在则进入优雅退出（触发 finally 清理）。
  B. is_framework_running(root)：psutil 进程判据替代失效的 runtime.lock
     —— basename=="main.py" 精确匹配（tests/conftest.py:205-216 同型），
        供外部编排脚本做互斥判据（写指令前先确认框架在跑）。

退出时长基准约 21s（快照≈15s），编排超时建议 ≥120s（见 config.NIGHT_ORCH_SHUTDOWN_TIMEOUT）。
"""

import json
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger
from nucleus.proc_gen import proc_gen_str as _m335_pg

_module_logger = get_module_logger("NightOrchestration")

# ===== ★333批刀2：夜间编制定时点（04:00±容差）=====
# 背景（331 运行验证坐实）：`generate_night_digest` 在全部代码中**没有任何调用方**——
#   main.py 只用 NightShutdownChannel（退出通道），docstring 里"由 04:00 夜间编排
#   经 tools/night_orch_shutdown.py --digest 调用"的约定**从未兑现**，
#   ⇒ 压测通过线「ledger 04:00 活体自转（source="live" 新增）」机制缺失，
#   ledger 永远 120 行 / 19:48 / live=0。
# 修正：在 main.py **既有主循环**加一个轻量定时点（不新建调度器、不起线程）。
NIGHT_DIGEST_HOUR = 4              # 调度时刻（时）
NIGHT_DIGEST_MINUTE = 0            # 调度时刻（分）
NIGHT_DIGEST_TOLERANCE_SEC = 600.0  # ±容差（04:00±10min 视为窗口内）
NIGHT_DIGEST_STATE_REL = "data/reports/night_digest_state.json"
#: `generate_night_digest` 最近一次 sweep 的读数（留痕用；不跨进程持久化）
_LAST_SWEEP_RESULT: dict = {}

_SHUTDOWN_FLAG_FILE = "shutdown_request.json"


def _flag_path(root):
    return os.path.join(root, "data", _SHUTDOWN_FLAG_FILE)


def _safe_read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as _f:
            return json.load(_f)
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.night_orchestration._safe_read_json")
        return {}


class NightShutdownChannel:
    """shutdown_request.json 的 request/has/consume 三件套（apply_now 同型）。"""

    def __init__(self, project_root):
        self._project_root = project_root
        self._flag_path = _flag_path(project_root)

    # ---- request：外部编排脚本写入退出指令 ----
    def request_shutdown(self, reason="night_orch", requester="external", timeout=None):
        _data = {
            "requested_at": time.time(),
            "reason": reason,
            "requester": requester,
            "consumed": False,
            "timeout": timeout,
        }
        try:
            with open(self._flag_path, "w", encoding="utf-8") as _f:
                json.dump(_data, _f, ensure_ascii=False, indent=2)
            return True
        except Exception as _e:
            silent_exc(_e, where="nucleus.evolution.night_orchestration.NightShutdownChannel.request_shutdown")
            return False

    # ---- has：主循环每拍检测（未消费即视为有效指令）----
    def has_shutdown_request(self):
        if not os.path.exists(self._flag_path):
            return False
        try:
            _data = _safe_read_json(self._flag_path)
            return not _data.get("consumed", False)
        except Exception as _e:
            silent_exc(_e, where="nucleus.evolution.night_orchestration.NightShutdownChannel.has_shutdown_request")
            return False

    # ---- consume：主循环消费（标记已消费，返回指令详情供退出流程使用）----
    def consume_shutdown_request(self):
        if not os.path.exists(self._flag_path):
            return None
        try:
            _data = _safe_read_json(self._flag_path)
            _data["consumed"] = True
            _data["consumed_at"] = time.time()
            with open(self._flag_path, "w", encoding="utf-8") as _f:
                json.dump(_data, _f, ensure_ascii=False, indent=2)
            return _data
        except Exception as _e:
            silent_exc(_e, where="nucleus.evolution.night_orchestration.NightShutdownChannel.consume_shutdown_request")
            return None

    # ---- read：外部编排脚本轮询用（返回当前指令 dict，无则 {}）----
    def read_request(self):
        return _safe_read_json(self._flag_path)

    # ---- clear：框架启动时清理上一轮遗留指令（防止粘滞触发）----
    def clear_stale_request(self):
        try:
            if os.path.exists(self._flag_path):
                os.remove(self._flag_path)
                return True
        except Exception as _e:
            silent_exc(_e, where="nucleus.evolution.night_orchestration.NightShutdownChannel.clear_stale_request")
        return False


def _cmdline_is_framework_main(cmdline):
    """命令行是否**指向框架入口** main.py（basename 精确匹配，复用 conftest 同型判据）。"""
    for _a in cmdline or []:
        try:
            if os.path.basename(str(_a)).lower() == "main.py":
                return True
        except Exception as _e:
            silent_exc(_e, where="nucleus.evolution.night_orchestration._cmdline_is_framework_main")
            continue
    return False


def is_framework_running(root=None):
    """★第162批刀7 互斥判据：用 psutil 枚举"命令行指向 main.py"的进程（排除本进程）。

    返回三层语义（与 conftest._detect_framework_process 对齐）：
      True  = 探测到框架在跑；
      False = 探测可用但未找到（未运行）；
      None  = psutil 不可用，无法判定（调用方应保守处理，不误判为未运行）。
    """
    try:
        import psutil
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.night_orchestration.is_framework_running.import_psutil")
        return None
    _self_pid = os.getpid()
    try:
        for _p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if _p.info.get("pid") == _self_pid:
                    continue
                _cl = _p.info.get("cmdline") or []
                if _cmdline_is_framework_main(_cl):
                    return True
            except Exception as _e:
                silent_exc(_e, where="nucleus.evolution.night_orchestration.is_framework_running.loop")
                continue
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.night_orchestration.is_framework_running")
        return None
    return False


# ==================== ★第162批刀8：编排退出前置补丁闸门 ====================
def count_approved_patches(project_root):
    """统计当前 approved 状态的补丁数（供刀8 退出闸门判定）。

    复用 PatchManager.list_pending_patches() 过滤 status=="approved"；
    fail-open：任何异常（含 PatchManager 不可用）均返回 0（不阻断退出），
    仅 silent_exc 留痕，绝不抛出。
    """
    try:
        from nucleus.reasoning.PatchManager import PatchManager
        _pm = PatchManager(project_root)
        return sum(1 for _p in _pm.list_pending_patches() if _p.get("status") == "approved")
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.night_orchestration.count_approved_patches")
        return 0


def orchestration_exit_blocked_by_patches(project_root, enabled=True):
    """★第162批刀8 闸门判定：编排退出是否被 approved 补丁拦截。

    返回 True 表示应跳过退出（有 approved 补丁且开关开启）；否则 False。
    fail-open：任何异常返回 False（不阻断退出）。
    """
    if not enabled:
        return False
    return count_approved_patches(project_root) > 0


def write_exit_blocked_report(count, project_root, reason="EXIT_BLOCKED_BY_PATCH"):
    """刀8：退出被 approved 补丁拦截时，写留痕文件 data/night_orch_exit_blocked.json。

    供外部编排脚本读取，明确退出为何未生效（被补丁自重启劫持风险已规避）。
    异常仅 silent_exc 留痕，绝不抛出。
    """
    try:
        _dir = os.path.join(project_root, "data")
        os.makedirs(_dir, exist_ok=True)
        _path = os.path.join(_dir, "night_orch_exit_blocked.json")
        _payload = {
            "blocked_at": time.time(),
            "approved_count": count,
            "reason": reason,
        }
        with open(_path, "w", encoding="utf-8") as _f:
            json.dump(_payload, _f, ensure_ascii=False, indent=2)
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.night_orchestration.write_exit_blocked_report")



def _m333_digest_state_path(project_root):
    """状态文件路径（★铁律158：**可注入**，隔离验证不得写生产）。"""
    return os.path.join(project_root, NIGHT_DIGEST_STATE_REL)


def _m333_read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, dict) else {}
    except Exception as _e:
        silent_exc(_e, where="night_orchestration._m333_read_state")
        return {}


def _m333_write_state(path, data):
    """原子写状态文件（先 .tmp 再 os.replace）；失败留痕不抛。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # ★335批刀10：落盘即带本代次 —— 状态文件也要能归属进程
        #   （A-11 同理：不带代次的状态，重启后读起来跟"同一进程一直没跑"没区别）
        if isinstance(data, dict):
            data["proc_gen"] = _m335_pg()
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, path)
        return True
    except Exception as _e:
        _module_logger.warning("[夜间编排] digest 状态写失败 %s: %s: %s proc_gen=%s",
                               path, type(_e).__name__, _e, _m335_pg())
        return False


def _m348_impl_maybe_run_night_digest(project_root, now=None, state_path=None,
                                      tolerance_sec=None, enabled=None):
    """★333批刀2：夜间编排 digest 的**真实调度入口**（main.py 主循环调用）。

    ★348刀7：本函数改名为 `_m348_impl_*`，公开入口变成同文件末尾的**可观测包装**
      （只加 DEBUG 留痕，判定语义零变化）。

    Args:
        project_root: 项目根（**隔离验证传隔离根**，ledger 路径随 root 走）
        now:          注入当前时间（datetime；测试用）
        state_path:   状态文件路径（默认 `<root>/data/reports/night_digest_state.json`）
        tolerance_sec: 04:00 的 ± 容差秒数（默认 600s）
        enabled:      覆盖 `config.NIGHT_ORCH_DIGEST_ENABLED`

    Returns:
        `{ran, reason, digest, added, source, ymd}` —— 五态可区分，**不填 0**：
          ran=True                    跑了一次
          reason=already_ran_today    当日幂等命中（不重复写）
          reason=out_of_window        不在 04:00±容差窗内（静默，不刷屏）
          reason=missed_window        今日窗已过且未跑（**WARNING 一次**）
          reason=disabled / failed    开关关 / 生成失败

    边界：只写 digest 文件 ＋ ledger(source=live) ＋ 本状态文件；不碰其他数据面。
    """
    _root = project_root or os.getcwd()
    _tol = float(tolerance_sec if tolerance_sec is not None
                 else NIGHT_DIGEST_TOLERANCE_SEC)
    _sp = state_path or _m333_digest_state_path(_root)
    if now is None:
        from datetime import datetime as _dt
        now = _dt.now()
    _ymd = now.strftime("%Y%m%d")
    _st = _m333_read_state(_sp)
    _out = {"ran": False, "reason": "", "digest": None, "added": None,
            "source": "live", "ymd": _ymd, "state_path": _sp}

    if enabled is None:
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "NIGHT_ORCH_DIGEST_ENABLED", True))
        except Exception as _e:
            silent_exc(_e, where="night_orchestration.maybe_run_night_digest.cfg")
            enabled = True
    if not enabled:
        _out["reason"] = "disabled"
        return _out
    if str(_st.get("last_run_ymd") or "") == _ymd:
        _out["reason"] = "already_ran_today"
        return _out

    from datetime import datetime as _dt2
    from datetime import timedelta as _td
    _target = _dt2(now.year, now.month, now.day,
                   NIGHT_DIGEST_HOUR, NIGHT_DIGEST_MINUTE)
    _lo = _target - _td(seconds=_tol)
    _hi = _target + _td(seconds=_tol)
    if now < _lo:
        _out["reason"] = "out_of_window"
        return _out
    if now > _hi:
        # ★「没跑」必须说出来一次：静默错过与"跑了但 0 新增"长得一模一样
        _out["reason"] = "missed_window"
        if str(_st.get("missed_ymd") or "") != _ymd:
            _st["missed_ymd"] = _ymd
            _st["missed_at"] = time.time()
            # ★335批刀10（334挂账④）：文案改**代次口径**。
            #   原写"窗内进程未运行"会误导 —— 今日 04:00 进程**一直**在跑
            #   （例如本进程 09:33 才起来），真正的原因是「**本代次没覆盖到那个窗**」
            #   而不是"进程没跑"。两种说法对应的处置完全不同：
            #   前者要查调度点是否挂上，后者只能等下一个 04:00。
            _st["missed_reason"] = (
                "本代次（proc_gen=%s）未覆盖今日 %02d:%02d±%.0fs 窗"
                % (_m335_pg(), NIGHT_DIGEST_HOUR, NIGHT_DIGEST_MINUTE, _tol))
            _m333_write_state(_sp, _st)
            _module_logger.warning(
                "[夜间编排] digest **未生成**：%s（今日 %s；窗=%s ~ %s）"
                " ⇒ ledger 活体自转今日不会发生（不填 0，记为未发生） proc_gen=%s",
                _st["missed_reason"], _ymd,
                _lo.strftime("%H:%M:%S"), _hi.strftime("%H:%M:%S"), _m335_pg())
        return _out

    _digest = generate_night_digest(_root, as_of=now, enabled=enabled)
    _sw = dict(_LAST_SWEEP_RESULT or {})
    _added = _sw.get("added")
    _out["digest"] = _digest
    _out["added"] = _added
    if _digest:
        _out["ran"] = True
        _out["reason"] = "ok"
        _st["last_run_ymd"] = _ymd
        _st["last_run_ts"] = time.time()
        _st["last_digest"] = str(_digest)
        _st["last_added"] = _added
        _m333_write_state(_sp, _st)
        _module_logger.info(
            "[夜间编排] digest 生成：%s 新增 %s 条（source=live）"
            " ｜ ledger 累计 %s 条 ｜ verdict=%s proc_gen=%s",
            _digest,
            "unavailable" if _added is None else _added,
            _sw.get("total", "unavailable"), _sw.get("verdicts", {}), _m335_pg())
        # ★337批刀7：SC-3 一致性自检 —— **接夜间链**（04:00 digest 之后跑一次）。
        #   为什么挂这里：单跑要建图（实测 28.1s）⇒ 禁每拍；04:00 之后 ledger 当日
        #   自转数据与 `a4.coverage.*` 都最新，且与 digest 同窗不增加调度点。
        #   红线：**只产计数与结论**，不把 SC-3 送进 issues/趋势门禁
        #   （一次尺子抖动会变成补丁洪峰）。
        try:
            from nucleus.world_model.expectation_ledger import (
                sc3_consistency as _sc3,
            )
            _out["sc3"] = _sc3(project_root=_root)
            _module_logger.info(
                "[337刀7][SC-3] 夜间链实测 total=%s mismatch=%s rate=%s "
                "status=%s legacy_skipped=%s proc_gen=%s",
                (_out["sc3"] or {}).get("total"),
                (_out["sc3"] or {}).get("mismatch"),
                (_out["sc3"] or {}).get("rate"),
                (_out["sc3"] or {}).get("status"),
                (_out["sc3"] or {}).get("legacy_skipped"), _m335_pg())
        except Exception as _se:
            silent_exc(_se, where="night_orchestration::maybe_run_night_digest sc3",
                       level="warning")
            _out["sc3"] = {"status": "UNAVAILABLE",
                           "reason": "SC-3 异常: %s" % type(_se).__name__}
        # ★339批刀5：**存量回算**——同链（04:00 digest 之后一次），
        #   **禁每拍**（全量回算要读全部留样 + patch_history）。
        #   红线：**只产计数与结论**，不改任何一侧数据（SC-3 同源）。
        try:
            from nucleus.review_recompute import run_recompute as _m339_rr
            _out["recompute"] = _m339_rr(scope="incremental", project_root=_root)
            _module_logger.info(
                "[339回算] 夜间链实测 scope=%s verdict=%s 可判性=%s "
                "死代码硬档=%s legacy标注=%s proc_gen=%s",
                (_out["recompute"] or {}).get("scope"),
                (_out["recompute"] or {}).get("verdict"),
                ((_out["recompute"] or {}).get("blocks") or {}).get(
                    "A_judgeability", {}).get("new"),
                ((_out["recompute"] or {}).get("blocks") or {}).get(
                    "B_dead_code_hard", {}).get("new"),
                ((_out["recompute"] or {}).get("blocks") or {}).get(
                    "C_legacy_applied", {}).get("legacy_tagged"),
                _m335_pg())
        except Exception as _rce:
            silent_exc(_rce,
                       where="night_orchestration::maybe_run_night_digest recompute",
                       level="warning")
            _out["recompute"] = {"status": "UNAVAILABLE",
                                 "reason": "回算异常: %s" % type(_rce).__name__}
    else:
        _out["reason"] = "failed"
        _module_logger.warning(
            "[夜间编排] digest 生成失败（返回 None）：root=%s ymd=%s"
            " ⇒ 当日不重试，次日 04:00 窗再跑 proc_gen=%s", _root, _ymd, _m335_pg())
    return _out


# ==================== ★第162批刀16：自报 digest（每晚 04:00 挂靠夜间编排） ====================
def generate_night_digest(project_root, as_of=None, enabled=None):
    """★第162批刀16（T-自报digest-1，P2，依赖刀7）每晚自认知报告后产出人读摘要。

    只读 ``data/reports/alerts.jsonl`` 中 ``needs_human=True`` 条目，做
    去重 + 分级（P0/P1 计数）+ Top5 问题 + 建议动作 + 复跑命令，
    产出 ``data/reports/digest_YYYYMMDD.md``（一页内）。

    约束（任务书）：
      - 只读 alerts/todo/补丁队列，不修改任何业务数据；
      - 不新增除 digest 外的任何文件；
      - 由外部 04:00 调度经 ``tools/night_orch_shutdown.py --digest`` 调用，
        不新增独立调度。

    返回 digest 文件路径；关闭/无数据/异常时返回 None（all except → silent_exc）。
    """
    if enabled is None:
        try:
            import config as _cfg
            enabled = getattr(_cfg, "NIGHT_ORCH_DIGEST_ENABLED", True)
        except Exception as _e:
            silent_exc(_e, where="night_orchestration.generate_night_digest.cfg")
            enabled = True
    if not enabled:
        return None
    try:
        import json as _json
        from datetime import datetime

        _alerts_path = os.path.join(project_root, "data", "reports", "alerts.jsonl")
        _as_of = as_of or datetime.now()
        _ymd = _as_of.strftime("%Y%m%d")
        _out_dir = os.path.join(project_root, "data", "reports")
        os.makedirs(_out_dir, exist_ok=True)
        _digest_path = os.path.join(_out_dir, f"digest_{_ymd}.md")

        # 1) 读取 needs_human 条目
        _rows = []
        if os.path.isfile(_alerts_path):
            with open(_alerts_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _rec = _json.loads(_line)
                    except Exception as _je:
                        silent_exc(_je, where="night_orchestration.generate_night_digest.parse")
                        continue
                    if _rec.get("needs_human"):
                        _rows.append(_rec)

        # 2) 去重（按 report_id + anomaly_type + target；None 视为空串避免 TypeError）
        _seen = set()
        _deduped = []
        for _r in _rows:
            _key = (
                _r.get("report_id") or "",
                _r.get("anomaly_type") or "",
                _r.get("target") or "",
            )
            if _key in _seen:
                continue
            _seen.add(_key)
            _deduped.append(_r)

        # 3) 分级 P0 / P1
        def _sev(r):
            return str(r.get("severity", "")).upper()
        _p0 = [r for r in _deduped if _sev(r) == "P0"]
        _p1 = [r for r in _deduped if _sev(r) == "P1"]

        # 4) Top5（按 ts 倒序）
        def _ts(r):
            try:
                return float(r.get("ts", 0) or 0)
            except Exception as _e:
                silent_exc(_e, where="night_orchestration.generate_night_digest._ts")
                return 0.0
        _top = sorted(_deduped, key=_ts, reverse=True)[:5]

        # 5) 建议动作去重
        _actions = []
        _act_seen = set()
        for _r in _deduped:
            _a = _r.get("suggested_action")
            if _a and _a not in _act_seen:
                _act_seen.add(_a)
                _actions.append(_a)

        # 6) 拼装 markdown
        _L = []
        _L.append("# 曈曈自报消费闭环 · 每日 digest（{}）".format(_as_of.strftime("%Y-%m-%d")))
        _L.append("")
        _L.append("> 由夜间编排 04:00 流程自动产出（只读 alerts.jsonl，不改写 alerts/todo/补丁队列）。")
        _L.append("")
        _L.append("## 一、概览")
        _L.append("")
        _L.append("- 需人工介入条目（去重后）：**%d** 条" % len(_deduped))
        _L.append("- P0：**%d** 条 ｜ P1：**%d** 条" % (len(_p0), len(_p1)))
        _L.append("- 原始 alerts.jsonl 中 needs_human 命中：**%d** 条（含重复）" % len(_rows))
        _L.append("")
        _L.append("## 二、Top5 问题（按时间倒序）")
        _L.append("")
        if _top:
            for _i, _r in enumerate(_top, 1):
                _L.append("%d. [%s] %s — %s（target=%s）" % (
                    _i, _r.get("severity", "?"), _r.get("anomaly_type", "?"),
                    _r.get("description", ""), _r.get("target", "")))
        else:
            _L.append("(无)")
        _L.append("")
        _L.append("## 三、建议动作")
        _L.append("")
        if _actions:
            for _a in _actions:
                _L.append(f"- {_a}")
        else:
            _L.append("(无)")
        _L.append("")
        _L.append("## 四、复跑命令")
        _L.append("")
        _L.append("```bash")
        _L.append("python tools/night_orch_shutdown.py --digest")
        _L.append("```")
        _L.append("")

        # ★317批刀4：世界模型最小版（只观测不决策）——复用 04:00 编译点，不新建调度器。
        try:
            from nucleus.world_model.expectation_ledger import sweep as _ledger_sweep
            # ★320批刀0：04:00 属**活体**自然产出，显式标记 source=live
            #   （验证运行一律 source=verification + 隔离根，见刀3 硬规）
            _led = _ledger_sweep(project_root, source="live")
            # ★333批刀2：把读数记到模块级，供 `maybe_run_night_digest` 打
            #   「新增 X 条（source=live）」——否则"跑了但 0 新增"与"压根没跑"分不清。
            try:
                _LAST_SWEEP_RESULT.clear()
                _LAST_SWEEP_RESULT.update(dict(_led or {}))
            except Exception as _e:
                silent_exc(_e, where="night_orchestration.generate_night_digest.trace")
            _L.append("## 五、预期台账（world_model · 只观测不决策）")
            _L.append("")
            _L.append("- 触发：triggered=%s ｜ 新增 **%s** 条 ｜ 幂等跳过 %s 条 ｜ reason=%s"
                      % (_led.get("triggered"), _led.get("added", 0),
                         _led.get("skipped", 0), _led.get("reason", "")))
            _L.append("- 来源：source=%s（活体自然产出）" % (_led.get("source", "live"),))
            _L.append("- 台账累计：**%s** 条" % (_led.get("total", 0),))
            _L.append("- verdict 分布：%s" % (_led.get("verdicts", {}),))
            _L.append("- 红线：`violated` 不参与入队/动机（本模块只写台账）")
            _L.append("")
        except Exception as _le:
            silent_exc(_le, where="night_orchestration.generate_night_digest.ledger")

        # ★411批刀6（410 遗留①）：周期档案自动重建 —— **必须排在命中率报表之前**，
        #   否则报表用的还是旧容差（档案过期后七类会退成 no_coverage，读数直接变假）。
        #   与报表同一 04:00 编译点，不新建调度器（410 红线延续）。
        try:
            from nucleus.world_model import periodic_profile as _pp411
            _prof_written = _pp411.build()
            _prof_path = _pp411.save_profile(_prof_written)
            _psum = _pp411.summarize(_prof_written)
            _L.append("## 五点五、周期档案重建（world_model · 七类）")
            _L.append("")
            _L.append("- 产物：%s" % (_prof_path or "**未落盘**"))
            _L.append("- 七类齐全：%s（ok %s/%s ｜ 分布 %s）"
                      % ("PASS" if _psum.get("complete") else "FAIL",
                         _psum.get("ok", 0), _psum.get("total", 0),
                         _psum.get("by_predict_type", {})))
            _L.append("- 容差口径：%s" % _psum.get("tolerance_basis", {}))
            _L.append("")
        except Exception as _pe411:
            silent_exc(_pe411, where="night_orchestration.generate_night_digest.periodic_profile")
            _L.append("## 五点五、周期档案重建（world_model · 七类）")
            _L.append("")
            _L.append("- **重建失败**（已留痕，不阻断摘要）：%s: %s"
                      % (type(_pe411).__name__, _pe411))
            _L.append("")

        # ★410批刀3：预测命中率日报表 —— 复用同一 04:00 编译点，不新建调度器。
        try:
            from nucleus.world_model import hit_rate as _hr410
            _hp = _hr410.write_report()
            _hrep = _hr410.report()
            _htot = _hrep.get("total") or {}
            _L.append("## 六、预测命中率（world_model · 滚动 24h）")
            _L.append("")
            _L.append("- 总命中率：%s ｜ 有判定 **%s** 条（hit %s / miss %s）｜ 无覆盖 %s 条"
                      % (_htot.get("hit_rate"), _htot.get("covered", 0),
                         _htot.get("hit", 0), _htot.get("miss", 0),
                         _htot.get("no_coverage", 0)))
            _L.append("- 采样窗：%s 个 ｜ 日报表：%s"
                      % (_hrep.get("windows_sampled", 0), _hp or "未落盘"))
            _L.append("- 失配中：%s" % (list(_hrep.get("mismatches") or []) or "无"))
            _L.append("- 已淘汰模式：%s" % (list(_hrep.get("evicted") or []) or "无"))
            for _et, _t in (_hrep.get("types") or {}).items():
                if _t.get("covered"):
                    _L.append("  - `%s`：hit %s / miss %s ⇒ **%s**（无覆盖 %s）"
                              % (_et, _t["hit"], _t["miss"], _t["hit_rate"],
                                 _t.get("no_coverage", 0)))
            _L.append("")
        except Exception as _he:
            silent_exc(_he, where="night_orchestration.generate_night_digest.hit_rate")

        with open(_digest_path, "w", encoding="utf-8") as _f:
            _f.write("\n".join(_L) + "\n")
        return _digest_path
    except Exception as _e:
        silent_exc(_e, where="night_orchestration.generate_night_digest")
        return None


# ==================== ★348刀7：夜间编排调用可观测 ====================
def maybe_run_night_digest(project_root, now=None, state_path=None,
                           tolerance_sec=None, enabled=None):
    """★348刀7：`_m348_impl_maybe_run_night_digest` 的**可观测包装**（公开入口）。

    为什么必须包这一层（实测依据，348刀7 三候选验证）：
      原实现里 `out_of_window`（一天里 99.9% 的调用）**完全无留痕**，
      于是「根本没被调用」与「调用了、但每次都落在窗外」在日志上**一模一样**。
      10-23 04:00 第二拍未自转无法定因，根子就在这里（不是 fail-closed：
      隔离实测证明开关全 True 且窗外调用会写 missed_window + WARNING）。

    本包装对**每一次**调用打一条 DEBUG（reason/ymd/ran/added/窗/proc_gen），
    下一次 04:00 窗到点，看有没有这几行即可一刀定生死：
      有 `reason=out_of_window` 的大量行 ⇒ 调度点活着，问题在窗口覆盖；
      一行都没有 ⇒ 主循环没走到调度点（下一刀查主循环）。

    红线：只加日志，**不改任何判定/写盘/幂等语义**（全部沿用 impl 的返回值）。
    """
    _out = _m348_impl_maybe_run_night_digest(
        project_root, now=now, state_path=state_path,
        tolerance_sec=tolerance_sec, enabled=enabled)
    try:
        _tol = float(tolerance_sec if tolerance_sec is not None
                     else NIGHT_DIGEST_TOLERANCE_SEC)
        _module_logger.debug(
            "[夜间编排] 调用 reason=%s ymd=%s ran=%s added=%s 窗=%02d:%02d±%.0fs proc_gen=%s",
            (_out or {}).get("reason"), (_out or {}).get("ymd"),
            (_out or {}).get("ran"), (_out or {}).get("added"),
            NIGHT_DIGEST_HOUR, NIGHT_DIGEST_MINUTE, _tol, _m335_pg())
    except Exception as _le:
        silent_exc(_le, where="night_orchestration.maybe_run_night_digest.log")
    return _out
