#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""感官桥 sidecar（★477批）—— **必须用 Python 3.12 运行**。

为什么单独一个进程
==================
主进程跑在 3.14t（自由线程 / GIL disabled）。`cv2`、`sounddevice`(PortAudio)、
`pyttsx3`(COM) 这些 C 扩展在自由线程下会**原生崩溃**（0xC0000005，见
`logs/pulse_crash.log` 的 sounddevice→PulseEars 栈）。3.12 有 GIL，这些库稳定可用。
本桥把「眼睛 / 嘴巴 / 耳朵」的脏活隔离到 3.12，主进程只经子进程 + JSON 通信。

命令（单次执行，stdout 输出**一行 JSON**；stderr 只放诊断）
==========================================================
  caps                          能力自检（相机/麦克风/TTS/Vosk 是否可用）
  look [--save PATH]            拍一帧：亮度 / 人脸数 / 尺寸 / 一句话描述
  listen [--seconds N]          录 N 秒（默认 5）→ Vosk 中文识别 → 文本
  speak --text "..."            pyttsx3（Windows SAPI）朗读
  selftest                      caps + look（不落盘），用于验收
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_VOSK_MODEL = os.path.join(_ROOT, "models", "vosk-model-small-cn-0.22")

# 统一可见化：优先用项目内的 silent_exc（cw2 门禁认它）；独立运行时兜底等价实现。
try:
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from nucleus._silent_except import silent_exc
except Exception:  # 独立运行兜底（不静默：写 stderr）
    def silent_exc(e, where="", level="debug"):  # type: ignore[misc]
        sys.stderr.write(f"[sensory_bridge] {where} {type(e).__name__}: {e}\n")


def _out(payload: dict) -> None:
    """输出一行 JSON（客户端按最后一行解析）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _err(where: str, exc: BaseException) -> dict:
    """把异常变成可见的结构化错误（绝不静默吞掉）。"""
    sys.stderr.write(
        f"[sensory_bridge] {where} 失败: {type(exc).__name__}: {str(exc)[:300]}\n")
    return {"ok": False, "where": where,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


# --------------------------------------------------------------------- 眼睛
def _haar_cascade():
    import cv2
    _p = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    return cv2.CascadeClassifier(_p)


def cmd_look(save_path: str | None = None) -> dict:
    """拍一帧并给出可用的结构化描述（亮度/人脸/尺寸）。"""
    try:
        import cv2
        import numpy as np
    except ImportError as _e:
        silent_exc(_e, where="sensory_bridge::look.import", level="debug")
        return _err("look.import", _e)

    _cap = None
    try:
        _cap = cv2.VideoCapture(0)
        if not _cap.isOpened():
            return {"ok": False, "where": "look.open", "error": "camera_not_opened"}
        _ok, _frame = _cap.read()
        if not _ok or _frame is None:
            return {"ok": False, "where": "look.read", "error": "camera_read_failed"}

        _gray = cv2.cvtColor(_frame, cv2.COLOR_BGR2GRAY)
        _bright = float(np.mean(_gray))
        _faces = 0
        try:
            _casc = _haar_cascade()
            _rects = _casc.detectMultiScale(_gray, scaleFactor=1.15, minNeighbors=5,
                                            minSize=(40, 40))
            _faces = len(_rects) if _rects is not None else 0
        except Exception as _e_face:  # 人脸模型失败不应拖垮整次 look
            silent_exc(_e_face, where="sensory_bridge::look.face", level="debug")

        _h, _w = _frame.shape[:2]
        _level = ("很暗" if _bright < 40 else "偏暗" if _bright < 80
                  else "正常" if _bright < 190 else "偏亮")
        _desc = f"画面 {_w}x{_h}，亮度 {_bright:.0f}（{_level}）"
        if _faces > 0:
            _desc += f"，检测到 {_faces} 张人脸"

        if save_path:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                cv2.imwrite(save_path, _frame)
            except Exception as _e_save:
                silent_exc(_e_save, where="sensory_bridge::look.save", level="debug")

        return {"ok": True, "cmd": "look", "desc": _desc,
                "width": _w, "height": _h,
                "brightness": round(_bright, 1), "level": _level,
                "faces": _faces, "saved": bool(save_path),
                "ts": time.time()}
    except Exception as _e:
        silent_exc(_e, where="sensory_bridge::look", level="debug")
        return _err("look", _e)
    finally:
        if _cap is not None:
            try:
                _cap.release()
            except Exception as _e_rel:
                silent_exc(_e_rel, where="sensory_bridge::look.release", level="debug")


# --------------------------------------------------------------------- 耳朵
def cmd_listen(seconds: float = 5.0) -> dict:
    """录 N 秒 → Vosk 离线中文识别。"""
    try:
        import sounddevice as sd
        import vosk
    except ImportError as _e:
        silent_exc(_e, where="sensory_bridge::listen.import", level="debug")
        return _err("listen.import", _e)

    if not os.path.isdir(_VOSK_MODEL):
        return {"ok": False, "where": "listen.model",
                "error": f"vosk_model_missing: {_VOSK_MODEL}"}

    _sec = max(1.0, min(30.0, float(seconds)))
    try:
        _model = vosk.Model(_VOSK_MODEL)
        _rec = vosk.KaldiRecognizer(_model, 16000)
        _rec.SetWords(False)
        _rate = 16000
        _audio = sd.rec(int(_sec * _rate), samplerate=_rate, channels=1,
                        dtype="int16", blocking=True)
        sd.wait()
        _text = ""
        if _rec.AcceptWaveform(_audio.tobytes()):
            _text = str(json.loads(_rec.Result()).get("text") or "")
        else:
            _text = str(json.loads(_rec.FinalResult()).get("text") or "")
        return {"ok": True, "cmd": "listen", "text": _text,
                "seconds": _sec, "ts": time.time()}
    except Exception as _e:
        silent_exc(_e, where="sensory_bridge::listen", level="debug")
        return _err("listen", _e)


# --------------------------------------------------------------------- 嘴巴
def cmd_speak(text: str) -> dict:
    """pyttsx3（Windows SAPI）朗读一段文本。"""
    _t = (text or "").strip()
    if not _t:
        return {"ok": False, "where": "speak.input", "error": "empty_text"}
    try:
        import pyttsx3
    except ImportError as _e:
        silent_exc(_e, where="sensory_bridge::speak.import", level="debug")
        return _err("speak.import", _e)
    try:
        _engine = pyttsx3.init()
        _engine.setProperty("rate", 180)
        _engine.say(_t[:500])
        _engine.runAndWait()
        try:
            _engine.stop()
        except Exception as _e_stop:
            silent_exc(_e_stop, where="sensory_bridge::speak.stop", level="debug")
        return {"ok": True, "cmd": "speak", "chars": len(_t), "ts": time.time()}
    except Exception as _e:
        silent_exc(_e, where="sensory_bridge::speak", level="debug")
        return _err("speak", _e)


# --------------------------------------------------------------------- 自检
def cmd_caps() -> dict:
    """能力自检：哪些感官在本机真实可用。"""
    _caps = {"camera": False, "mic": False, "tts": False, "vosk": False}
    _notes: dict[str, str] = {}
    try:
        import cv2
        _c = cv2.VideoCapture(0)
        _caps["camera"] = bool(_c.isOpened())
        _c.release()
    except Exception as _e:
        silent_exc(_e, where="sensory_bridge::caps.camera", level="debug")
        _notes["camera"] = type(_e).__name__
    try:
        import sounddevice as sd
        _devs = sd.query_devices()
        _inp = [d for d in _devs if int(d.get("max_input_channels") or 0) > 0]
        _caps["mic"] = bool(_inp)
        _notes["mic_count"] = str(len(_inp))
    except Exception as _e:
        silent_exc(_e, where="sensory_bridge::caps.mic", level="debug")
        _notes["mic"] = type(_e).__name__
    try:
        import pyttsx3
        _e_t = pyttsx3.init()
        _caps["tts"] = bool(_e_t.getProperty("voices"))
        try:
            _e_t.stop()
        except Exception as _e_stop:
            silent_exc(_e_stop, where="sensory_bridge::caps.tts.stop", level="debug")
    except Exception as _e:
        silent_exc(_e, where="sensory_bridge::caps.tts", level="debug")
        _notes["tts"] = type(_e).__name__
    _caps["vosk"] = bool(os.path.isdir(_VOSK_MODEL))
    return {"ok": True, "cmd": "caps", "caps": _caps, "notes": _notes,
            "python": sys.version.split()[0], "ts": time.time()}


def cmd_selftest() -> dict:
    _caps = cmd_caps()
    _look = cmd_look(None) if _caps.get("caps", {}).get("camera") else {"ok": False}
    return {"ok": bool(_caps.get("ok")), "cmd": "selftest",
            "caps": _caps.get("caps"), "look": _look,
            "python": sys.version.split()[0], "ts": time.time()}


def main() -> int:
    _ap = argparse.ArgumentParser(description="感官桥 sidecar (run on Python 3.12)")
    _ap.add_argument("--cmd", required=True,
                     choices=["caps", "look", "listen", "speak", "selftest"])
    _ap.add_argument("--save", default=None)
    _ap.add_argument("--seconds", type=float, default=5.0)
    _ap.add_argument("--text", default="")
    _args = _ap.parse_args()

    try:
        if _args.cmd == "caps":
            _res = cmd_caps()
        elif _args.cmd == "look":
            _res = cmd_look(_args.save)
        elif _args.cmd == "listen":
            _res = cmd_listen(_args.seconds)
        elif _args.cmd == "speak":
            _res = cmd_speak(_args.text)
        elif _args.cmd == "selftest":
            _res = cmd_selftest()
        else:  # argparse choices 已兜住；此处仅防御
            _res = {"ok": False, "error": "unknown_cmd"}
    except Exception as _e:  # 兜底：任何未捕获异常都变成可见 JSON
        silent_exc(_e, where="sensory_bridge::main", level="debug")
        _res = _err("main", _e)
        _res["traceback"] = traceback.format_exc()[-800:]

    _out(_res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
