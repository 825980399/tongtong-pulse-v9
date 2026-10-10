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
        _digest_path = os.path.join(_out_dir, "digest_{}.md".format(_ymd))

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
                _L.append("- {}".format(_a))
        else:
            _L.append("(无)")
        _L.append("")
        _L.append("## 四、复跑命令")
        _L.append("")
        _L.append("```bash")
        _L.append("python tools/night_orch_shutdown.py --digest")
        _L.append("```")
        _L.append("")

        with open(_digest_path, "w", encoding="utf-8") as _f:
            _f.write("\n".join(_L) + "\n")
        return _digest_path
    except Exception as _e:
        silent_exc(_e, where="night_orchestration.generate_night_digest")
        return None
