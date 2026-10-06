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
