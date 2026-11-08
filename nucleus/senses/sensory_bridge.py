# -*- coding: utf-8 -*-
"""感官桥客户端（★477批，3.14t 主进程侧）。

主进程跑 3.14t（自由线程 / GIL disabled），`cv2` / `sounddevice`(PortAudio) /
`pyttsx3`(COM) 这些 C 扩展在此会**原生崩溃**（0xC0000005）。本模块把「眼睛/嘴巴/耳朵」
的脏活转交给 **Python 3.12 sidecar**（`tools/senses/sensory_bridge.py`），主进程只做
子进程 + JSON 通信，保持纯 Python、不碰任何会崩的原生库。

对外接口（**全部 graceful**：任何失败都返回 `{"ok": False, ...}`，绝不抛异常给调用方）：
  caps()              能力自检
  look(save=None)     拍一帧：亮度 / 人脸数 / 尺寸 / 一句话描述
  listen(seconds=5)   录音 → Vosk 中文识别 → 文本
  speak(text)         pyttsx3(SAPI) 朗读
  selftest()          caps + look

灰度：`config.ENABLE_SENSORY_BRIDGE=False` 时所有接口直接返回 `{"ok": False,
"error": "disabled"}`（零副作用回退）。
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from nucleus._silent_except import silent_exc

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _script_path() -> str:
    return os.path.join(_PROJECT_ROOT, "tools", "senses", "sensory_bridge.py")


def _cfg(name: str, default):
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception as _e:
        silent_exc(_e, where="nucleus.senses.sensory_bridge::_cfg", level="debug")
        return default


def enabled() -> bool:
    """感官桥总开关（默认开；`PULSE_SENSORY_BRIDGE=0` 可关）。"""
    return bool(_cfg("ENABLE_SENSORY_BRIDGE", True))


def bridge_python() -> str:
    """3.12 解释器路径（sidecar 必须用它跑）。"""
    return str(_cfg("SENSORY_BRIDGE_PYTHON", "") or "")


def run(cmd: str, timeout: float | None = None, **kwargs) -> dict:
    """调用 sidecar 一次，返回解析后的 JSON dict。

    参数：
      cmd: caps / look / listen / speak / selftest
      kwargs: 透传为 `--key value`（None/False 跳过；True 只带 flag）
    """
    if not enabled():
        return {"ok": False, "error": "disabled"}
    _py = bridge_python()
    _script = _script_path()
    if not _py or not os.path.isfile(_py):
        return {"ok": False, "error": f"bridge_python_missing: {_py}"}
    if not os.path.isfile(_script):
        return {"ok": False, "error": f"bridge_script_missing: {_script}"}

    _argv = [_py, _script, "--cmd", str(cmd)]
    for _k, _v in (kwargs or {}).items():
        if _v is None or _v is False:
            continue
        _flag = "--" + str(_k).replace("_", "-")
        if _v is True:
            _argv.append(_flag)
        else:
            _argv.extend([_flag, str(_v)])

    _to = float(timeout if timeout is not None else _cfg("SENSORY_BRIDGE_TIMEOUT", 30.0))
    try:
        _cp = subprocess.run(_argv, capture_output=True, text=True, check=False,
                             timeout=max(5.0, _to), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as _e:
        silent_exc(_e, where="nucleus.senses.sensory_bridge::run timeout", level="debug")
        return {"ok": False, "error": "timeout", "cmd": cmd}
    except Exception as _e:
        silent_exc(_e, where="nucleus.senses.sensory_bridge::run spawn", level="debug")
        return {"ok": False, "error": f"{type(_e).__name__}: {str(_e)[:200]}"}

    # 从 stdout 里取最后一行 JSON（原生库可能往 stdout 打杂讯）
    _lines = (_cp.stdout or "").strip().splitlines()
    for _ln in reversed(_lines):
        _s = _ln.strip()
        if _s.startswith("{"):
            try:
                _d = json.loads(_s)
                if isinstance(_d, dict):
                    _d.setdefault("cmd", cmd)
                    return _d
            except Exception as _e:
                silent_exc(_e, where="nucleus.senses.sensory_bridge::run parse",
                           level="debug")
    _tail = ((_cp.stderr or "").strip() or (_cp.stdout or "").strip())[-200:]
    return {"ok": False, "error": "no_json_output", "cmd": cmd,
            "rc": int(_cp.returncode or 0), "tail": _tail,
            "ts": time.time()}


# ------------------------------------------------------------------ 便捷封装
def caps() -> dict:
    return run("caps", timeout=20.0)


def look(save: str | None = None) -> dict:
    return run("look", timeout=25.0, save=save)


def listen(seconds: float = 5.0) -> dict:
    return run("listen", timeout=float(seconds) + 25.0, seconds=seconds)


def speak(text: str) -> dict:
    _t = (text or "").strip()
    if not _t:
        return {"ok": False, "error": "empty_text"}
    return run("speak", timeout=30.0, text=_t[:500])


def selftest() -> dict:
    return run("selftest", timeout=40.0)
