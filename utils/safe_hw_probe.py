"""safe_hw_probe —— 安全硬件探测（v10 · 隔离子进程版）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import json
import subprocess
import sys

from nucleus._silent_except import silent_exc


def _run_isolated(script: str, timeout: float = 6.0):
    """在隔离子进程中执行探测脚本，返回解析后的 dict 或 None（崩溃/超时/异常）。

    绝不抛异常，绝不阻塞主进程超过 timeout 秒。
    """
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        silent_exc(e, where="safe_hw_probe._run_isolated:spawn")
        return None

    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 超时：强制结束子进程，避免残留
        try:
            proc.kill()
        except Exception as e:
            silent_exc(e, where="safe_hw_probe._run_isolated:kill")
        try:
            proc.communicate()
        except Exception as e:
            silent_exc(e, where="safe_hw_probe._run_isolated:drain")
        return None
    except Exception as e:
        silent_exc(e, where="safe_hw_probe._run_isolated:final")
        return None

    if not out:
        return None
    try:
        # 取最后一行（防止其它库的意外输出干扰 JSON 解析）
        last = out.strip().splitlines()[-1]
        return json.loads(last)
    except Exception as e:
        print(f"[WARNING] safe_hw_probe.py:52: {type(e).__name__}: {e}")
        return None


_AUDIO_SCRIPT = """import json
try:
    import pyaudio
    p = pyaudio.PyAudio()
    ch_key = {ch_key!r}
    devs = []
    ok = False
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get(ch_key, 0) > 0:
            ok = True
            devs.append({{
                "index": i,
                "name": info.get("name", "?"),
                "sample_rate": int(info.get("defaultSampleRate", 16000)),
                "channels": int(info.get(ch_key, 1)),
            }})
    p.terminate()
    print(json.dumps({{"ok": ok, "devices": devs}}))
except Exception as e:
    print(json.dumps({{"ok": False, "devices": []}}))
"""

_CAMERA_SCRIPT = """import json
try:
    import cv2
    devs = []
    ok = False
    for i in range(3):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok = True
            devs.append({{"index": i}})
        cap.release()
    print(json.dumps({{"ok": ok, "devices": devs}}))
except Exception as e:
    print(json.dumps({{"ok": False, "devices": []}}))
"""


def safe_audio_devices(want_input: bool, timeout: float = 6.0):
    """隔离子进程探测音频设备。

    want_input=True 探测麦克风（输入通道>0）；False 探测扬声器（输出通道>0）。
    返回 {'ok': bool, 'devices': [...]} 或 None（崩溃/超时）。
    """
    ch_key = "maxInputChannels" if want_input else "maxOutputChannels"
    script = _AUDIO_SCRIPT.format(ch_key=ch_key)
    return _run_isolated(script, timeout)


def safe_camera_devices(timeout: float = 6.0):
    """隔离子进程探测摄像头。

    返回 {'ok': bool, 'devices': [{'index': i}]} 或 None（崩溃/超时）。
    """
    return _run_isolated(_CAMERA_SCRIPT, timeout)

