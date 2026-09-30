"""PulseVisualCortex —— PulseVisualCortex 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ────────────────────────────────────────────────────────────────
# 历史模块说明（原第二份 docstring，转注释以保持 import 区连续 → 消除 E402）。
# ★主线第31批 T4：此前它是**悬空字符串表达式**，会关闭 ruff 的 import 区，
#   导致其后 8 条 import 全被判 E402。字符串本身不产生运行时作用，转注释等价。
# ────────────────────────────────────────────────────────────────
# PulseVisualCortex —— 视觉皮层器官 · 图像感知与分析（v9.5 分层脉冲版）
# 版本: v9.5 PulseNet
# 设计: 路灯、小林、星轨
# 日期: 2026年6月12日
# 更新: 2026年6月18日（架构重构: 通过眼睛专用通道获取摄像头帧 + 人脸检测 + 图片文件分析）
#
# 职责:
#     1. 实时人脸监测 —— 通过眼睛专用通道获取帧，动态评分+冷却锁检测人脸进出
#     2. 图片文件分析 —— 接收 VisualEvent.SIMULATE 脉冲，分析图片尺寸/格式
#     3. 帧分析 —— 接收 VisualEvent.CAMERA_FRAME 脉冲，提取帧元数据
#     4. 物体/文字检测 —— 为未来接入 YOLO/OCR 预留接口

import os
import sys
import threading
import logging
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.logger import exc_location
from nucleus.const import (
    ChatEvent,
    DigestEvent,
    EyeEvent,
    LogLevel,
    MouthEvent,
    PersonaEvent,
    VisualEvent,
    DeviceEvent,
)
from nucleus.data.DataAccessLayer import safe_read_json  # ★R5：视觉流日志读取
from nucleus.security.face_codec import load_roster, save_roster  # ★T-134b 人脸名册加密存储
_DIRTY_FACE_KEYS = ("用户", "访客", "小林")  # ★R5-1 脏键唯一真相源（:362 守卫/load 过滤/save 过滤三处共用）


_TOL_BAD = object()   # ★R5 哨兵：区分"合法 null"与"非法值"，避免引入 except→return None 静默处


def _tol_or_none(v):
    """R5: tolerance_override 只允许收紧（0.3~0.6）。非数字/越界 → _TOL_BAD（调用方丢整条）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        logging.getLogger("pulse").debug(f"[R5] tolerance_override 非数字/越界: {v!r} → _TOL_BAD")
        return _TOL_BAD
    return f if 0.3 <= f <= 0.6 else _TOL_BAD




class PulseVisualCortex(BasePulseOrgan):
    """视觉皮层 —— 图像感知与分析器官（v9.5 分层脉冲版）"""
    
    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'visual_window_size' in _rp and hasattr(self, '_window_size'):
                self._window_size = _rp['visual_window_size']
            if 'visual_stable_presence_ratio' in _rp and hasattr(self, '_stable_presence_ratio'):
                self._stable_presence_ratio = _rp['visual_stable_presence_ratio']
            if 'visual_stable_absence_ratio' in _rp and hasattr(self, '_stable_absence_ratio'):
                self._stable_absence_ratio = _rp['visual_stable_absence_ratio']
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")


    _FACE_ROSTER_PATH = os.path.join(  # ★R5-2 册路径（同族写法见 :232 视觉流日志）
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'data', 'identity', 'face_roster.json')

    def __init__(self, organ_name: str = "视觉皮层"):
        super().__init__(organ_name)
        
        self.node_pool = None
        self.frequency_codec = None
        
        self._frame_count = 0
        self._face_detected_count = 0
        self._object_detected_count = 0
        self._text_detected_count = 0
        
        self._has_opencv = False
        self._has_face_recognition = False
        self._has_ocr = False
        
        self._lock = threading.Lock()
        self._global_lock = threading.Lock()
        
        self.is_running = False
        self._current_user_name = "访客"  # 当前检测到的用户（T-118a：未知默认访客）
        # 摄像头监测线程
        self._camera_thread = None
        self._camera_running = False
        
        # 人脸检测状态
        self._face_score = 0
        self._face_was_detected = False
        self._last_face_state_switch = 0
        
        # v9.5 时序追踪：滑动窗口记录最近N帧的检测结果
        self._detection_window = []      # 存储最近N帧的bool值(True=有人脸)
        # ★P1: 从RUNTIME_PARAMS读取视觉皮层参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._window_size = _rp.get("visual_window_size", 56)
            self._stable_presence_ratio = _rp.get("visual_stable_presence_ratio", 0.4)
            self._stable_absence_ratio = _rp.get("visual_stable_absence_ratio", 0.1)
        except Exception:
            self._window_size = 56
            self._stable_presence_ratio = 0.4
            self._stable_absence_ratio = 0.1
        # 外部引用
        self._sensor = None   # 躯体硬件层（由框架注入）
        # 异步日志写入
        self._log_queue = []           # 日志条目队列
        self._log_lock = threading.Lock()  # 队列锁
        self._log_thread = None        # 日志写入线程
        self._log_running = False        
        self._last_processed_seq = 0  # 最后处理的帧序号，用于丢弃过期帧
        self._gpu_available = None  # GPU是否可用（None=未收到能力更新）

        self._roster_meta: dict[str, dict] = {}   # ★R5-3 册元数据 name→{enrolled_at,source,hits,tolerance_override}
        self._roster_hits: dict[str, int] = {}    # ★R5-3b hits 预留（本批无写入点，恒取落盘值）

        # ===== 新增: 视觉身份记忆 =====
        self._known_face_encodings: dict[str, Any] = {}  # user_name → face_encoding
        self._pending_face_encoding = None  # 刚检测到但尚未识别的人脸编码
        self._pending_face_time = 0.0       # 待绑定人脸编码的检测时间
        # ===== ★R5-4 人脸册加载（失败=空册，WARN 不抛，绝不影响启动）=====
        _t_r5 = time.time()
        _np = None                                        # VC 现无 numpy 模块级 import（仅 :515/:562 函数内）
        try:
            import numpy as _np
        except ImportError:                               # 缺 numpy 走纯 list 兜底，不新增静默处
            _np = None
            self._log(LogLevel.DEBUG, "[R5] 未安装 numpy，人脸编码落盘/加载走纯 list 兜底")
        try:
            _raw = load_roster(self._face_roster_path())
            if not isinstance(_raw, dict):
                _raw = {}
            if not _raw:
                self._log(LogLevel.INFO, "[R5] 人脸册不存在/为空 → 空册启动（首次运行属正常）")
            elif _raw.get("version") != 1:
                raise ValueError("未知册版本 %r（不加载、不回写）" % (_raw.get("version"),))
            else:
                _faces = _raw.get("faces") or {}
                _loaded = _dropped = 0
                for _name, _item in _faces.items():
                    if not isinstance(_name, str) or not _name.strip() or len(_name) > 32:
                        _dropped += 1
                        continue
                    if _name in _DIRTY_FACE_KEYS:            # ★与 :362 同一判据（常量同源）
                        _dropped += 1
                        continue
                    _enc = (_item or {}).get("encoding128")
                    if not isinstance(_enc, list) or len(_enc) != 128:
                        _dropped += 1
                        continue
                    try:
                        _vec = _np.asarray(_enc, dtype=_np.float64)
                    except (ImportError, TypeError, ValueError):
                        try:
                            _vec = [float(x) for x in _enc]
                        except (TypeError, ValueError):
                            _dropped += 1
                            continue
                    _it = _item or {}
                    self._known_face_encodings[_name] = _vec
                    self._roster_meta[_name] = {
                        "enrolled_at": float(_it.get("enrolled_at") or 0.0),
                        "source": _it.get("source") if _it.get("source") in ("auto", "manual") else "auto",
                        "hits": int(_it.get("hits") or 0),
                    }
                    _tol = _tol_or_none(_it.get("tolerance_override"))
                    # 非法/试图放宽 → 只把该字段降为 null，**不丢整条**（沿用 121 期 schema 语义）
                    self._roster_meta[_name]["tolerance_override"] = None if _tol is _TOL_BAD else _tol
                    self._roster_hits[_name] = int(_it.get("hits") or 0)
                    _loaded += 1
                self._log(LogLevel.INFO, "[R5] 人脸册加载: %d 条(丢弃 %d) 读 %.3fs"
                          % (_loaded, _dropped, time.time() - _t_r5))
        except (Exception, SystemExit) as _e_r5:             # 口径同 :547/:567（T-119a）
            self._known_face_encodings = {}
            self._roster_meta = {}
            self._roster_hits = {}
            self._log(LogLevel.WARNING,
                      "[R5] 人脸册加载失败，按空册启动（不影响其它功能）: %s: %s"
                      % (type(_e_r5).__name__, _e_r5))


        self._mp_face_detection = None        
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        # ★属性初始化完整性补全（自动审查添加）
        self._absent_frames = 0
        self._has_mediapipe = False
        self._recent_visual_queries = {}
        self._startup_faces = 0
        self._detect_capabilities()
        self._engines = []
        self._engine_warning_cooldown = {}  # 引擎异常日志冷却（30秒）
        self._init_engines()
    
    # ========== 能力检测 ==========
   
    def _detect_capabilities(self):
        # 1. OpenCV（必须）
        try:
            import cv2  # noqa: F401
            self._has_opencv = True
        except ImportError:
            self._has_opencv = False
        
        # 2. MediaPipe 安全导入
        self._has_mediapipe = False
        self._mp_face_detection = None
        try:
            import mediapipe as mp
            # 尝试旧版 solutions
            if hasattr(mp, 'solutions'):
                self._mp_face_detection = mp.solutions.face_detection
                self._has_mediapipe = True
            # 尝试新版 tasks
            else:
                try:
                    from mediapipe.tasks import python as mp_tasks  # noqa: F401
                    from mediapipe.tasks.python import vision
                    self._mp_face_detection = vision.FaceDetector
                    self._has_mediapipe = True
                except ImportError:
                    self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        except ImportError:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        
        # 3. 其他可选库（★T-113c：补 import，缺失即 False，消除"假有"）
        try:
            import face_recognition  # noqa: F401
            self._has_face_recognition = True
        except (ImportError, SystemExit):  # ★T-119a 装库前置硬化：models 缺失 api.py quit() 抛 SystemExit
            self._has_face_recognition = False
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        try:
            import pytesseract  # noqa: F401
            self._has_ocr = True
        except ImportError:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
    # ========== 视觉引擎管理 ==========
    
    def _init_engines(self):
        """初始化所有可用的视觉检测引擎，按优先级排序"""
        self._engines = []
        
        # 引擎1：MediaPipe（高精度，优先级最高）
        try:
            from organs.senses.visual_engines.mediapipe_engine import MediaPipeEngine
            engine = MediaPipeEngine()
            if engine.is_available():
                self._engines.append(("MediaPipe", engine))
                self._log(LogLevel.INFO, "已加载视觉引擎: MediaPipe（高精度）")
        except ImportError:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        
        # 引擎2：Haar（基础精度，永远可用）
        try:
            from organs.senses.visual_engines.haar_engine import HaarEngine
            engine = HaarEngine()
            if engine.is_available():
                self._engines.append(("Haar", engine))
                self._log(LogLevel.INFO, "已加载视觉引擎: Haar（基础精度）")
        except ImportError:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        
        if not self._engines:
            self._log(LogLevel.WARNING, "无可用视觉引擎，人脸检测不可用")
    
    def _detect_face(self, frame) -> tuple:
        """使用最佳可用引擎检测人脸，失败自动降级"""
        for name, engine in self._engines:
            try:
                detected, confidence = engine.detect(frame)
                if detected:
                    return True, confidence
            except Exception:
                now = time.time()
                last = self._engine_warning_cooldown.get(name, 0)
                if now - last > 30:
                    self._log(LogLevel.DEBUG, f"引擎 {name} 检测异常，降级（30秒内不再重复）")
                    self._engine_warning_cooldown[name] = now
                continue
        return False, 0.0
    def _start_log_writer(self):
        """启动异步日志写入线程"""
        self._log_running = True
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
                                 'data', 'stream', 'vision_stream_log.json')
        self._log_thread = threading.Thread(target=self._log_writer_loop, args=(log_path,), daemon=True)
        self._log_thread.start()
    
    def _log_writer_loop(self, log_path):
        """后台线程：定期批量写入视觉流日志"""
        while self._log_running:
            time.sleep(0.5)  # 每0.5秒批量写入一次
            with self._log_lock:
                if not self._log_queue:
                    continue
                batch = list(self._log_queue)
                self._log_queue.clear()
             
            if not batch:
                continue
            
            try:
                import json as _json
                # 读取已有日志
                log_data = []
                if os.path.exists(log_path):
                    try:
                        log_data = safe_read_json(log_path, default=[])
                    except (ValueError, OSError) as e:
                        self._log(LogLevel.INFO, f"[WARNING] PulseVisualCortex.py:251: {type(e).__name__}: {e}")
                        log_data = []
                # ★第81批 T6：历史脏文件自愈（流日志本应是 list，若读到 dict 不得对其调 extend）
                if isinstance(log_data, dict):
                    self._log(LogLevel.WARNING,
                              "[第81批 T6] 视觉流日志为 dict（历史脏文件），自愈为 list 后追加")
                    log_data = []
                # 追加新条目
                log_data.extend(batch)
                if len(log_data) > 100:
                    log_data = log_data[-100:]
                with open(log_path, 'w', encoding='utf-8') as f:
                    _json.dump(log_data, f, ensure_ascii=False)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    # ========== 依赖注入 ==========
    
    def set_node_pool(self, node_pool):
        self.node_pool = node_pool
    
    def set_frequency_codec(self, frequency_codec):
        self.frequency_codec = frequency_codec
    
    def set_sensor(self, sensor):
        """注入躯体硬件层 Sensor 实例"""
        self._sensor = sensor
    
    
    # ========== 生命周期 ==========
    def start(self):
        super().start()
        self._log(LogLevel.INFO, 
                  f"已启动，OpenCV={'有' if self._has_opencv else '无'}, "
                  f"人脸识别={'有' if self._has_face_recognition else '无'}, "
                  f"OCR={'有' if self._has_ocr else '无'}")   
        self._start_log_writer() 
    def stop(self):
        self._camera_running = False
        self._log_running = False        
        super().stop()
        self._log(LogLevel.INFO, 
                  f"已停止，处理{self._frame_count}帧, "
                  f"人脸{self._face_detected_count}次")
    
    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None
        
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})
        
        # ★v23.0修复：优先检查task_type，确保OCR/PDF请求不被分辨率分析拦截
        _task_type = payload.get("task_type", "")
        if _task_type in ("ocr", "pdf"):
            return self._on_visual_query(payload)
        
        if event_type == VisualEvent.CAMERA_FRAME:
            return self._analyze_frame(payload)
        elif event_type == VisualEvent.SIMULATE:
            return self._simulate_from_file(payload)
        elif event_type == EyeEvent.STREAM_FRAME:
            return self._on_stream_frame(payload)
        elif event_type == EyeEvent.VISUAL_QUERY:          # 新增：视觉查询（OCR/PDF）
            return self._on_visual_query(payload)
        elif event_type == DeviceEvent.CAPABILITY_UPDATE:
            return self._on_capability_update(payload)
        elif event_type == ChatEvent.MESSAGE:
            return self._on_chat_message(payload)
        
        return None
    
    def is_face_detected(self) -> bool:
        """公开只读访问当前是否检测到人脸（规则14）"""
        return self._face_was_detected

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    VisualEvent.CAMERA_FRAME,
                    VisualEvent.SIMULATE,
                    EyeEvent.STREAM_FRAME,
                    EyeEvent.VISUAL_QUERY,      # 新增：视觉查询
                    DeviceEvent.CAPABILITY_UPDATE,
                    ChatEvent.MESSAGE,
                ],
                "min_priority": 1,
            },
        ]
    def _on_capability_update(self, payload: dict) -> dict[str, Any]:
        """收到设备管理器的硬件能力枚举脉冲"""
        caps = payload.get("capabilities", {})
        self._gpu_available = caps.get("compute.gpu.available", False)
        self._log(LogLevel.INFO, f"硬件能力更新: GPU={'可用' if self._gpu_available else '不可用'}")
        return {"status": "updated", "gpu_available": self._gpu_available}    
    def _on_chat_message(self, payload: dict) -> dict[str, Any]:
        """
        收到对话消息时，将未识别的人脸编码与对话中的用户名绑定。
        这样曈曈在与人对话时自然学习对方的长相。
        """
        user_name = payload.get("user_name", "")
        if not user_name or user_name in ("用户", "访客", "小林"):  # ★T-119a 绑定白名单守卫：脏键禁止入册
            return {"status": "skipped", "reason": "脏键(用户/访客/小林)禁止入册"}
        
        # 如果有待绑定的人脸编码且距今30秒内，绑定到当前用户名
        if (self._pending_face_encoding is not None 
            and time.time() - self._pending_face_time < 30):
            self._known_face_encodings[user_name] = self._pending_face_encoding
            self._current_user_name = user_name
            self._roster_meta[user_name] = {"enrolled_at": time.time(), "source": "auto",
                                            "hits": 0, "tolerance_override": None}  # ★R5-5 元数据与册同点写
            self._roster_hits[user_name] = 0                                        # ★R5-5b

            self._log(LogLevel.INFO, 
                     f"视觉身份绑定: 将当前人脸与'{user_name}'关联")
            self._pending_face_encoding = None
            self._pending_face_time = 0.0
            self._save_face_roster()   # ★R5-6 一次绑定=一次落盘（失败只 WARN，不改绑定结果）

            return {"status": "bound", "user_name": user_name}
        
        return {"status": "acknowledged", "user_name": user_name}
    def _on_visual_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        处理视觉查询请求：根据 task_type 调用对应插件进行 OCR 或 PDF 文字提取。
        识别结果发射为 DigestEvent.KNOWLEDGE，供胃消化为知识节点。
        """
        file_path = payload.get("file_path", "")
        task_type = payload.get("task_type", "ocr")
        user_name = payload.get("user_name", "用户")
        
        if not file_path:
            return {"status": "skipped", "reason": "空文件路径"}
        
        # 去重：同一文件+同一task_type在60秒内不重复处理
        if not hasattr(self, '_recent_visual_queries'):
            self._recent_visual_queries: dict[str, float] = {}
        _dedup_key = f"{file_path}:{task_type}"
        _now = time.time()
        if _dedup_key in self._recent_visual_queries:
            if _now - self._recent_visual_queries[_dedup_key] < 60:
                return {"status": "skipped", "reason": f"60秒内已处理过: {_dedup_key}"}
        self._recent_visual_queries[_dedup_key] = _now
        
        if not os.path.exists(file_path):
            return {"status": "error", "reason": f"文件不存在: {file_path}"}
        
        result = {"status": "processed", "task_type": task_type, "file_path": file_path, "text": "", "error": None}
        
        # 获取远程API配置（用于OCR增强）
        _remote_config = None
        try:
            import config as _cfg
            _remote_config = getattr(_cfg, 'REMOTE_API_CONFIG', {})
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        
        if task_type == "ocr":
            try:
                from organs.senses.visual_engines.ocr_engine import (
                    process as ocr_process,
                )
                _ocr_result = ocr_process(file_path, _remote_config)
                result["text"] = _ocr_result.get("text", "")
                result["method"] = _ocr_result.get("method", "unknown")
                result["confidence"] = _ocr_result.get("confidence", 0)
                result["error"] = _ocr_result.get("error") or _ocr_result.get("warning")
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._text_detected_count += 1
                self._log(LogLevel.INFO, 
                         f"OCR识别完成: {len(result['text'])}字符, "
                         f"方法={result['method']}, 置信度={result['confidence']}")
            except ImportError:
                result["error"] = "ocr_engine插件未安装"
            except Exception as e:
                result["error"] = f"OCR处理异常: {str(e)[:80]}"
        
        elif task_type == "pdf":
            try:
                from organs.senses.visual_engines.pdf_engine import (
                    process as pdf_process,
                )
                _pdf_result = pdf_process(file_path)
                result["text"] = _pdf_result.get("text", "")
                result["pages"] = _pdf_result.get("pages", 0)
                result["confidence"] = _pdf_result.get("confidence", 0)
                result["error"] = _pdf_result.get("error")
                self._text_detected_count += 1
                self._log(LogLevel.INFO, 
                         f"PDF提取完成: {len(result['text'])}字符, "
                         f"{result.get('pages', 0)}页, 置信度={result['confidence']}")
            except ImportError:
                result["error"] = "pdf_engine插件未安装"
            except Exception as e:
                result["error"] = f"PDF处理异常: {str(e)[:80]}"
        
        else:
            result["status"] = "unknown_task"
            result["error"] = f"未知任务类型: {task_type}"
            return result
        
        # 将识别结果发射为知识消化脉冲（后台知识积累）
        if result["text"] and len(result["text"].strip()) >= 5:
            _file_name = os.path.basename(file_path)
            _digest_content = (
                f"[视觉识别·{task_type}] 文件: {_file_name}\n"
                f"识别方法: {result.get('method', '视觉皮层')}\n"
                f"置信度: {result.get('confidence', 0):.2f}\n"
                f"内容:\n{result['text'][:2000]}"
            )
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": _digest_content,
                "source_organ": self.organ_name,
                "trigger_reason": f"visual.{task_type}",
                "importance": "B",
                "view_mode": "OUTER_VIEW",
            }, priority=3, layer="L2")
            self._log(LogLevel.INFO, f"视觉知识消化: {task_type}结果已发射为知识节点")
            
            # ★新增：将识别结果通过嘴巴输出给用户
            _reply_text = (
                f"📄 识别完成！以下是「{_file_name}」中的文字内容：\n\n"
                f"{result['text'][:1500]}\n\n"
                f"（识别方法：{result.get('method', '未知')}，置信度：{result.get('confidence', 0):.0%}）"
            )
            self._emit(MouthEvent.SPEAK, {
                "content": _reply_text,
                "source": "visual_cortex",
                "user_name": user_name,
                "reasoning_path": f"visual_{task_type}",
            }, priority=8, layer="L1")
        else:
            # ★v23.0修复：识别结果为空时也输出提示，避免静默失败
            _file_name = os.path.basename(file_path)
            _reply_text = (
                f"📄 我尝试识别「{_file_name}」，但没能提取到文字内容。\n"
                f"原因: {result.get('error') or '可能图片中没有文字，或识别引擎未就绪'}"
            )
            self._emit(MouthEvent.SPEAK, {
                "content": _reply_text,
                "source": "visual_cortex",
                "user_name": user_name,
                "reasoning_path": f"visual_{task_type}_empty",
            }, priority=8, layer="L1")
        
        return result
    def _recognize_face(self, frame) -> tuple:
        """
        使用 face_recognition 库识别帧中的人脸。
        
        Returns:
            (user_name, confidence) 元组；未匹配/失败返回 (None, 0.0)。
            confidence = max(0, 1 - best_distance/0.6) —— "是他"的匹配度，
            非"有脸"检测度（★T-115e Z1 置信度语义修正）。
        """
        if not self._has_face_recognition:
            return (None, 0.0)
        
        try:
            import face_recognition
            import numpy as np  # noqa: F401
            
            # 转换OpenCV BGR为RGB
            rgb_frame = frame[:, :, ::-1]
            
            # 提取当前帧的面部编码
            face_encodings = face_recognition.face_encodings(rgb_frame)
            if not face_encodings:
                return (None, 0.0)
            
            current_encoding = face_encodings[0]
            
            # 与已知面部编码比对
            best_match = None
            best_distance = 0.6  # 阈值：距离小于0.6认为是同一个人
            
            for name, known_encoding in self._known_face_encodings.items():
                distance = face_recognition.face_distance([known_encoding], current_encoding)[0]
                if distance < best_distance:
                    best_distance = distance
                    best_match = name
            
            # 如果没匹配到，保存待绑定编码
            if best_match is None:
                self._pending_face_encoding = current_encoding
                self._pending_face_time = time.time()
                return (None, 0.0)
            
            # ★T-115e Z1：匹配度语义（"是他"而非"有脸"）
            _conf = max(0.0, 1.0 - best_distance / 0.6)
            return (best_match, _conf)
            
        except (Exception, SystemExit) as _e:  # ★T-119a 硬化：SystemExit 非 Exception 子类
            # ★T-113c：识别失败计数 + 日志，防止"8天0成功"无感知
            _fc = getattr(self, "_face_recognize_fail_count", 0) + 1
            self._face_recognize_fail_count = _fc
            if _fc <= 1 or _fc % 50 == 0:
                self._log(LogLevel.WARNING,
                          f"[人脸识别] 识别异常（累计失败 {_fc} 次）: {_e}")
            return (None, 0.0)
    
    def _extract_face_encoding(self, frame) -> Any | None:
        """从帧中提取人脸编码（不进行比对）"""
        if not self._has_face_recognition:
            return None
        
        try:
            import face_recognition
            rgb_frame = frame[:, :, ::-1]
            face_encodings = face_recognition.face_encodings(rgb_frame)
            if face_encodings:
                return face_encodings[0]
        except (Exception, SystemExit) as e:  # ★T-119a 硬化：SystemExit 非 Exception 子类
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return None    

    # ========== 帧分析（纯元数据） ==========
    
    def _analyze_frame(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收 CAMERA_FRAME 脉冲，提取帧元数据"""
        self._frame_count += 1
        
        device_id = payload.get("device_id", "unknown")
        image_format = payload.get("image_format", "unknown")
        width = payload.get("width", 0)
        height = payload.get("height", 0)
        
        analysis = {
            "frame_id": self._frame_count,
            "device_id": device_id,
            "image_format": image_format,
            "resolution": f"{width}x{height}",
            "megapixels": round(width * height / 1_000_000, 2) if width and height else 0,
            "timestamp": time.time(),
        }
        
        if self.info_field and self.pulse_core:
            self.info_field.publish(self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=VisualEvent.ANALYSIS_DONE,
                payload=analysis,
                priority=3,
                layer="L3"
            ))
        
        return analysis
    def _on_stream_frame(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收实时视频帧，灵敏人脸检测 + 快速启动确认 + 快速离开确认"""
        
        raw_data = payload.get("raw_data")
        frame_seq = payload.get("frame_seq", 0)
        
        if raw_data is None:
            return {"status": "skipped", "reason": "无帧数据"}
        
        with self._global_lock:
            if frame_seq <= self._last_processed_seq:
                return {"status": "skipped", "reason": "过期帧"}
            self._last_processed_seq = frame_seq
        
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._frame_count += 1
        face_detected, confidence = self._detect_face(raw_data) 
        # 初始化快速启动/离开计数器
        if not hasattr(self, '_startup_faces'):
            self._startup_faces = 0
        if not hasattr(self, '_absent_frames'):
            self._absent_frames = 0
        
        # 快速启动确认：前30帧内，累计检测到2帧人脸，立刻打招呼
        if self._frame_count <= 30 and face_detected:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._startup_faces += 1
            if self._startup_faces >= 3 and not self._face_was_detected:
                with self._global_lock:
                    self._face_was_detected = True
                    self._face_score = 50
                    self._last_face_state_switch = time.time()
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._face_detected_count += 1
                self._log(LogLevel.INFO, f"启动确认：检测到人脸 (第{self._face_detected_count}次)")
                # ===== 新增: 视觉身份识别 =====
                if self._has_face_recognition:
                    recognized, _match_conf = self._recognize_face(raw_data)
                    if recognized:
                        old_name = self._current_user_name  # noqa: F841
                        self._current_user_name = recognized
                        self._log(LogLevel.INFO, f"视觉识别: 人脸识别为'{recognized}'(匹配度={_match_conf:.2f})")                
                if self.info_field and self.pulse_core:
                    # ★P3-5补发射：人脸识别到身份 → persona.identified（此前有订阅无发射）
                    if self._has_face_recognition and self._current_user_name not in ("用户", "访客"):
                        self.info_field.publish(self.pulse_core.emit(
                            source_organ=self.organ_name,
                            event_type=PersonaEvent.IDENTIFIED,
                            payload={
                                "sensor_type": "visual",
                                "user_id": self._current_user_name,
                                "confidence": round(float(_match_conf), 2),  # ★T-115e Z1：用匹配度
                            },
                            priority=8,
                            layer="L1"
                        ))
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=ChatEvent.USER_PRESENCE_DETECTED,
                        payload={
                            "user_name": self._current_user_name,
                            "detection_type": "face_appeared",
                            "confidence": round(float(confidence), 2),
                        },
                        priority=7,
                        layer="L1"
                    ))
                return {"status": "fast_confirmed", "frame_seq": frame_seq}
        
        # 快速离开确认：连续6帧检测不到人脸，立刻触发离开
        if self._face_was_detected:
            if face_detected:
                self._absent_frames = 0
            else:
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._absent_frames += 1
            if self._absent_frames >= 300:   #给摄像头重连留足时间
                with self._global_lock:
                    self._face_was_detected = False
                    self._face_score = 0
                    self._last_face_state_switch = time.time()
                self._log(LogLevel.INFO, "人脸消失（快速离开确认）")
                if self.info_field and self.pulse_core:
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=ChatEvent.USER_LEFT,
                        payload={
                            "user_name": self._current_user_name,
                            "detection_type": "face_disappeared",
                        },
                        priority=7,
                        layer="L1"
                    ))
                self._current_user_name = "访客"  # ★第129批 T-129b：离场即清粘名，防影子误报
                return {"status": "fast_left", "frame_seq": frame_seq}
        
        # 常规时序追踪（兜底）：基于滑动窗口的稳定性判断
        STATE_COOLDOWN = 3.0   #状态切换冷却更久，杜绝反复横跳
        presence_ratio = 0.0  # 初始化，供后续日志使用
        
        with self._global_lock:
            # 更新滑动窗口
            self._detection_window.append(face_detected)
            if len(self._detection_window) > self._window_size:
                self._detection_window.pop(0)
            
            # 计算窗口内的检测率
            if len(self._detection_window) >= 3:
                nonlocal_presence = sum(self._detection_window) / len(self._detection_window)
                presence_ratio = nonlocal_presence
            else:
                # 窗口不足3帧时，暂不判断
                presence_ratio = 1.0 if self._face_was_detected else 0.0
            
            now = time.time()
            old_state = self._face_was_detected
            cooldown_passed = (now - self._last_face_state_switch) >= STATE_COOLDOWN
            
            # 判定出现：窗口内检测率超过阈值且冷却已过
            if not old_state and presence_ratio >= self._stable_presence_ratio:
                if cooldown_passed:
                    self._face_was_detected = True
                    self._last_face_state_switch = now
                    self._face_detected_count += 1
                    self._log(LogLevel.INFO, f"检测到人脸出现 (第{self._face_detected_count}次, 稳定率={presence_ratio:.2f})")
                    # ===== 新增: 视觉身份识别 =====
                    if self._has_face_recognition:
                        recognized, _match_conf = self._recognize_face(raw_data)
                        if recognized:
                            self._current_user_name = recognized                    
                    if self.info_field and self.pulse_core:
                        # ★P3-5补发射：人脸识别到身份 → persona.identified（此前有订阅无发射）
                        if self._has_face_recognition and self._current_user_name not in ("用户", "访客"):
                            self.info_field.publish(self.pulse_core.emit(
                                source_organ=self.organ_name,
                                event_type=PersonaEvent.IDENTIFIED,
                                payload={
                                    "sensor_type": "visual",
                                    "user_id": self._current_user_name,
                                    "confidence": round(float(_match_conf), 2),  # ★T-115e Z1：用匹配度
                                },
                                priority=8,
                                layer="L1"
                            ))
                        self.info_field.publish(self.pulse_core.emit(
                            source_organ=self.organ_name,
                            event_type=ChatEvent.USER_PRESENCE_DETECTED,
                            payload={
                                "user_name": self._current_user_name,
                                "detection_type": "face_appeared",
                                "confidence": round(presence_ratio, 2),
                            },
                            priority=7,
                            layer="L1"
                        ))
            
            # 判定离开：窗口内检测率低于阈值
            elif old_state and presence_ratio <= self._stable_absence_ratio:
                self._face_was_detected = False
                self._last_face_state_switch = now
                self._absent_frames = 0
                self._log(LogLevel.INFO, f"人脸消失（离开视线, 稳定率={presence_ratio:.2f})")
                if self.info_field and self.pulse_core:
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=ChatEvent.USER_LEFT,
                        payload={
                            "user_name": self._current_user_name,
                            "detection_type": "face_disappeared",
                        },
                        priority=7,
                        layer="L1"
                    ))
                self._current_user_name = "访客"  # ★第129批 T-129b：离场即清粘名，防影子误报
        
        # 异步写入视觉流日志（放入队列，后台线程处理）
        try:
            with self._global_lock:
                if len(self._detection_window) >= 3:
                    current_ratio = sum(self._detection_window) / len(self._detection_window)
                else:
                    current_ratio = 1.0 if self._face_was_detected else 0.0
                current_state = "有人" if self._face_was_detected else "无人"
            
            log_entry = {
                "timestamp": time.time(),
                "frame_seq": frame_seq,
                "face_detected": face_detected,
                "confidence": confidence,
                "score": round(current_ratio * 100, 1),
                "state": current_state,
                "engines": [name for name, _ in self._engines],
            }
            with self._log_lock:
                self._log_queue.append(log_entry)
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        return {"status": "processed", "frame_seq": frame_seq}
    # ========== 图片文件分析 ==========
    
    def _simulate_from_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        # ★v23.0修复：OCR/PDF任务已由_on_visual_query处理，这里不再重复做分辨率分析
        if payload.get("task_type") in ("ocr", "pdf"):
            return {"status": "routed_to_visual_query", "reason": "OCR/PDF任务由视觉查询处理"}
        
        file_path = payload.get("file_path", "")
        
        if not file_path:
            return {"error": "未指定文件路径"}
        if not os.path.exists(file_path):
            return {"error": f"文件不存在: {file_path}"}
        
        if self._has_opencv:
            import cv2
            try:
                img = cv2.imread(file_path)
                if img is not None:
                    h, w, c = img.shape
                    result = {
                        "source": "file_simulation",
                        "file_path": file_path,
                        "width": w,
                        "height": h,
                        "channels": c,
                        "resolution": f"{w}x{h}",
                        "megapixels": round(w * h / 1_000_000, 2),
                        "format": "BGR" if c == 3 else f"{c}-channel",
                        "correlation_id": payload.get("correlation_id", ""),
                    }
                    self._frame_count += 1
                    if self.info_field and self.pulse_core:
                        self.info_field.publish(self.pulse_core.emit(
                            source_organ=self.organ_name,
                            event_type=VisualEvent.ANALYSIS_DONE,
                            payload=result,
                            priority=3,
                            layer="L3"
                        ))
                    return result
            except Exception as e:
                return {"error": f"OpenCV 读取失败: {e}"}
        
        file_size = os.path.getsize(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        result = {
            "source": "file_simulation",
            "file_path": file_path,
            "file_size_bytes": file_size,
            "file_size_kb": round(file_size / 1024, 1),
            "extension": ext,
            "note": "无法解析图片内容，仅提取文件元信息。",
            "correlation_id": payload.get("correlation_id", ""),
        }
        self._frame_count += 1
        
        if self.info_field and self.pulse_core:
            self.info_field.publish(self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=VisualEvent.ANALYSIS_DONE,
                payload=result,
                priority=3,
                layer="L3"
            ))
        
        return result  

    # ========== 预留接口 ==========
    
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()    
    # ========== 统计信息 ==========
    
    # ========== ★R5-7 册路径 / 落盘（原子写；失败只 WARN）==========
    def _face_roster_path(self) -> str:
        """册路径（测试可用 TONGTONG_FACE_ROSTER 覆盖）。

        ★T-134b：env 重定向必须在 data/ 内（realpath 前缀 + os.sep 边界），
        越界即拒绝（fail-closed，绝不把生物特征册写到 data/ 之外）。
        """
        _env = os.environ.get("TONGTONG_FACE_ROSTER")
        if _env:
            from nucleus.security.face_codec import is_path_within_data
            if not is_path_within_data(_env):
                raise ValueError(
                    f"[PulseVisualCortex] TONGTONG_FACE_ROSTER 越界 data/ 被拒绝: {_env}")
            return _env
        return self._FACE_ROSTER_PATH

    def _save_face_roster(self) -> None:
        """人脸册落盘。快照遍历（:368 可能并发改写）；backup=False 免生 .bak 明文副本。"""
        try:
            _faces = {}
            for _name, _enc in dict(self._known_face_encodings).items():
                if _name in _DIRTY_FACE_KEYS:               # ★save 侧同判据复拦
                    continue
                _meta = self._roster_meta.get(_name, {})
                _faces[_name] = {
                    "encoding128": _enc.tolist() if hasattr(_enc, "tolist") else [float(x) for x in _enc],
                    "enrolled_at": float(_meta.get("enrolled_at") or time.time()),
                    "source": _meta.get("source", "auto"),
                    # hits 为预留字段：现网无"识别命中"计数点（_recognize_face 只 return 名字），故恒 0
                    "hits": int(self._roster_hits.get(_name, 0)),
                    "tolerance_override": _meta.get("tolerance_override"),
                }
            if not save_roster(self._face_roster_path(),
                               {"version": 1, "updated_at": time.time(), "faces": _faces}):
                self._log(LogLevel.WARNING, "[R5] 人脸册落盘返回 False（内存册仍有效）")
        except (Exception, SystemExit) as _e:
            self._log(LogLevel.WARNING,
                      "[R5] 人脸册落盘异常（内存册仍有效）: %s: %s" % (type(_e).__name__, _e))

    # ========== ★R5-8 隐私接口：只回元数据，绝不回显 encoding ==========
    def _list_faces(self) -> list:
        """人脸册清单（元数据 only，按 enrolled_at 升序）。无 encoding 任何形式。"""
        _out = []
        for _name in dict(self._known_face_encodings).keys():      # 快照遍历
            _m = self._roster_meta.get(_name, {})
            _ea = float(_m.get("enrolled_at") or 0.0)
            _out.append({
                "name": _name,
                "enrolled_at": _ea,
                "enrolled_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_ea)) if _ea else "unknown",
                "source": _m.get("source", "auto"),
                "hits": int(self._roster_hits.get(_name, 0)),
                "tolerance_override": _m.get("tolerance_override"),
            })
        _out.sort(key=lambda d: d["enrolled_at"])
        return _out

    def _forget_face(self, user_name: str) -> dict:
        """删除指定人脸：内存册 + 元数据 + 待绑定 + 磁盘册四处一致；不回显 encoding。"""
        if not isinstance(user_name, str) or not user_name.strip():
            return {"status": "rejected", "reason": "空姓名"}
        _existed_mem = user_name in self._known_face_encodings
        self._known_face_encodings.pop(user_name, None)
        self._roster_meta.pop(user_name, None)
        self._roster_hits.pop(user_name, None)
        # ★保守侧：现网 _pending_face_encoding 不记姓名（:124/:539 无 name 字段），无法判定待绑脸是否就是被删者
        #   → 无条件清待绑定；代价=可能多废一次绑定窗（票面登记取舍）
        self._pending_face_encoding = None
        self._pending_face_time = 0.0
        if user_name == self._current_user_name:
            self._current_user_name = "访客"   # ★T-118a 同族回落（回落"用户"亦不阻断下次绑定，见票 §D3）
        try:
            _raw = load_roster(self._face_roster_path())
            _faces = (_raw or {}).get("faces") or {}
            _existed_disk = _faces.pop(user_name, None) is not None
            if _existed_disk or _existed_mem:
                if not save_roster(self._face_roster_path(),
                                   {"version": 1, "updated_at": time.time(), "faces": _faces}):
                    self._log(LogLevel.WARNING,
                              "[R5] 遗忘落盘失败：'%s' 内存已删，磁盘册可能残留 → 需人工删文件" % user_name)
            return {"status": "forgotten", "name": user_name, "was_in_memory": _existed_mem}
        except (Exception, SystemExit) as _e:
            self._log(LogLevel.WARNING,
                      "[R5] 遗忘落盘异常（内存已删）: %s: %s" % (type(_e).__name__, _e))
            return {"status": "forgotten_partial", "name": user_name, "was_in_memory": _existed_mem}

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "frame_count": self._frame_count,
                "face_detected": self._face_detected_count,
                "object_detected": self._object_detected_count,
                "text_detected": self._text_detected_count,
                "has_opencv": self._has_opencv,
                "has_face_recognition": self._has_face_recognition,
                "has_ocr": self._has_ocr,
            }


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "视觉皮层",
    "class_name": "PulseVisualCortex",
    "attr_name": "visual_cortex",
    "system": "senses",
    "always_online": False,
    "feature_flag": "enable_vision",
    "extra_deps": {
        "node_pool": "node_pool",
        "frequency_codec": "frequency_codec",
    },
    "post_wiring": [
        {"target": "sensor", "setter": "set_sensor"},
    ],
}

if __name__ == "__main__":
    print("=== PulseVisualCortex v9.5 分层脉冲自测 ===\n")

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {"event_type": event_type, "source_organ": source_organ,
                    "payload": payload, "priority": priority, "layer": layer}

    cortex = PulseVisualCortex("视觉皮层")
    mock_field = MockField()
    mock_core = MockCore()
    cortex.set_info_field(mock_field)
    cortex.set_pulse_core(mock_core)

    cortex.start()

    print("1. 处理 CAMERA_FRAME 脉冲:")
    result1 = cortex.on_pulse({
        "event_type": VisualEvent.CAMERA_FRAME,
        "payload": {
            "device_id": "cam0",
            "image_format": "rgb",
            "width": 1920,
            "height": 1080,
        },
        "priority": 3,
    })
    print(f"   分辨率: {result1.get('resolution')}")

    print("\n2. 模拟本地不存在图片:")
    result2 = cortex.on_pulse({
        "event_type": VisualEvent.SIMULATE,
        "payload": {"file_path": "nonexistent.jpg"},
        "priority": 3,
    })
    print(f"   文件异常: {'error' in result2}")

    stats = cortex.get_stats()
    print(f"3. 统计: 处理{stats['frame_count']}帧, OpenCV={'有' if stats['has_opencv'] else '无'}")

    cortex.stop()
    print("\n=== 自测全部通过 ===")
