"""PulseEyes —— PulseEyes 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

"""
PulseEyes —— 脉冲驱动眼睛（知识检索器官 · v9.5 分层脉冲版）
版本: v9.5 PulseNet
设计: 路灯、小林、星轨 
日期: 2026年6月9日
更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+统一日志+命名规范+get_stats+自测同步）
更新: 2026年6月14日（v9.5: 检索结果脉冲标记layer=L2，适配分层异步调度）

职责:
    1. 接收 EyeEvent.SEARCH 脉冲 → 执行知识检索
    2. 五维共振匹配 → 调用 ResonanceEngine.resonate()
    3. 频率编码 → 通过 FrequencyCodec 将查询转为频率签名
    4. 结果排序 → 按共振强度降序，返回 Top-K
    5. 兜底检索 → 共振引擎未注入时，回退关键词匹配
"""
 
import re
import threading  # ← 加这一行
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import EyeEvent, LogLevel, SystemEvent, VisualEvent
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


class PulseEyes(BasePulseOrgan):
    """脉冲驱动眼睛（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'eyes_top_k' in _rp and hasattr(self, 'top_k'):
                self.top_k = _rp['eyes_top_k']
            if 'eyes_min_score' in _rp and hasattr(self, 'min_score'):
                self.min_score = _rp['eyes_min_score']
            if 'eyes_reopen_backoff' in _rp and hasattr(self, '_reopen_backoff'):
                self._reopen_backoff = _rp['eyes_reopen_backoff']
        except Exception as e:
            silent_exc(e, where="organs.senses.PulseEyes::refresh_runtime_params L54")


    def __init__(self, organ_name: str = "眼睛"):
        super().__init__(organ_name)

        self.node_pool = None
        self.resonance_engine = None
        self.frequency_codec = None

        # ★P1: 从RUNTIME_PARAMS读取眼睛参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self.top_k = _rp.get("eyes_top_k", 20)
            self.min_score = _rp.get("eyes_min_score", 0.1)
        except Exception:
            self.top_k = 20
            self.min_score = 0.1

        self._search_count = 0
        
        # 摄像头设备（由眼睛管理，供视觉皮层通过专用通道获取帧）
        self._camera_cap = None          # OpenCV VideoCapture 对象
        self._camera_lock = threading.Lock()  # 帧读取锁
        self._sensor = None           # 躯体硬件层引用
        self._camera_thread = None    # 摄像头监测线程
        self._camera_running = False
        self._stream_thread = None   # 推流线程
        self._stream_running = False
        self._frame_seq = 0         # 帧序列号
        self._camera_init_lock = threading.Lock()  # 摄像头初始化锁
        self._camera_initialized = False  # 是否已完成初始化
        self._camera_available = None  # 摄像头是否可用（None=未收到能力更新，等待中）

        # ★属性初始化完整性补全（自动审查添加）
        self._reopen_backoff = 1.0
        self._reopen_cooldown_until = 0.0
        self._reopen_fail_count = 0
    # ========== 框架注入接口 ==========

    def set_node_pool(self, pool):
        self.node_pool = pool

    def set_resonance_engine(self, engine):
        self.resonance_engine = engine

    def set_frequency_codec(self, codec):
        self.frequency_codec = codec
    def start(self):
        """启动眼睛"""
        super().start()
        # 乐观初始化：先尝试打开摄像头，能力更新到达后再根据实际硬件调整
        # _init_camera 有防重入锁，收到能力更新后再次调用不会重复初始化
        self._init_camera()
    
    def _init_camera(self):
        """初始化摄像头设备（防重入）

        修复记录：
        - 使用DSHOW后端（Windows上比MSMF更稳定）
        - 打开后验证能否真正读取帧（避免isOpened=True但read()失败）
        - 失败后自动重试（最多3次，间隔2秒）
        - 增加预热帧数（10帧）
        """
        with self._camera_init_lock:
            if self._camera_initialized:
                return  # 已经初始化过，不再重复
            if self._camera_available is False:
                self._log(LogLevel.INFO, "摄像头不可用，跳过推流初始化")
                return
            try:
                import cv2

                # 尝试多个后端：DSHOW优先（Windows稳定），默认后端兜底
                backends = [
                    (cv2.CAP_DSHOW, "DSHOW"),
                    (0, "默认(MSMF)"),
                ]

                cap = None
                used_backend = None
                for backend_idx, backend_name in backends:
                    for attempt in range(3):
                        if backend_idx == 0:
                            cap = cv2.VideoCapture(0)
                        else:
                            cap = cv2.VideoCapture(0, backend_idx)
                        if cap.isOpened():
                            # 验证能否真正读取帧
                            test_ret, test_frame = cap.read()
                            if test_ret and test_frame is not None:
                                used_backend = backend_name
                                break
                            else:
                                cap.release()
                                cap = None
                                time.sleep(2)
                        else:
                            cap.release()
                            cap = None
                            time.sleep(2)
                    if cap is not None:
                        break

                if cap is None:
                    self._log(LogLevel.WARNING, "无法打开摄像头设备（尝试了DSHOW和默认后端各3次）")
                    self._camera_cap = None
                    return

                self._camera_cap = cap
                self._camera_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self._camera_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self._camera_cap.set(cv2.CAP_PROP_FPS, 5)  # 星轨调整：30fps→5fps，降低采集负载
                self._camera_cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                self._camera_cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)

                # 预热10帧，确保摄像头稳定
                for _ in range(10):
                    self._camera_cap.read()
                    time.sleep(0.05)

                # 再次验证
                verify_ret, verify_frame = self._camera_cap.read()
                if not verify_ret or verify_frame is None:
                    self._log(LogLevel.WARNING, "摄像头打开成功但预热后无法读取帧，推流可能不稳定")

                self._log(LogLevel.INFO, f"眼睛已打开并优化摄像头 (640x480@5fps，后端={used_backend}，星轨调整降低负载)")
                self._stream_running = True
                self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True, name="EyeStream")
                self._stream_thread.start()
                self._log(LogLevel.INFO, "实时视觉流已启动（主动推流模式）")
                self._camera_initialized = True
            except Exception as e:
                self._log(LogLevel.WARNING, f"打开摄像头失败: {type(e).__name__}: {e}")
                self._camera_cap = None
    def stop(self):
        """停止眼睛"""
        self._stream_running = False
        self._camera_initialized = False
        if self._camera_cap:
            self._camera_cap.release()
            self._camera_cap = None
            self._log(LogLevel.INFO, "摄像头设备已释放")
        super().stop()
    def set_sensor(self, sensor):
        """注入躯体硬件层 Sensor 实例"""
        self._sensor = sensor
    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == EyeEvent.SEARCH:
            return self._on_search(payload)
        elif event_type == EyeEvent.VISUAL_QUERY:
            return self._on_visual_query(payload)
        elif event_type == "device.capability_update":
            return self._on_capability_update(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None
    # ========== 事件处理 ==========

    def _on_search(self, payload: dict) -> dict[str, Any]:
        query = payload.get("query", "")
        user_name = payload.get("user_name", "用户")
        top_k = payload.get("top_k", self.top_k)

        if not query:
            return {"status": "skipped", "reason": "空查询"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._search_count += 1
        start_time = time.time()

        results = []
        candidates = self._get_candidates()

        if self.resonance_engine and self.frequency_codec and candidates:
            results = self._resonance_search(query, candidates, top_k)
        else:
            results = self._keyword_search(query, candidates, top_k)

        elapsed_ms = (time.time() - start_time) * 1000

        # v9.5: 检索结果脉冲标记为L2认知思考层
        self._emit(EyeEvent.SEARCH_RESULT, {
            "query": query,
            "results": results,
            "total_found": len(results),
            "elapsed_ms": round(elapsed_ms, 2),
            "user_name": user_name,
            "method": "resonance" if self.resonance_engine else "keyword",
        }, priority=7, layer="L2")

        self._log(LogLevel.DEBUG, f"检索完成: '{query[:30]}...' → {len(results)}条 ({elapsed_ms:.1f}ms)")

        return {
            "status": "completed",
            "query": query,
            "total_found": len(results),
            "elapsed_ms": round(elapsed_ms, 2),
            "top_result": results[0]["value"][:60] if results else None,
        }
    def _on_visual_query(self, payload: dict) -> dict[str, Any]:
        """收到大脑皮层的视觉查询，转发给视觉皮层处理"""
        file_path = payload.get("file_path", "")
        user_name = payload.get("user_name", "用户")
        correlation_id = payload.get("correlation_id", "")

        if not file_path:
            return {"status": "skipped", "reason": "空文件路径"}

        # 判断文件类型，分发给对应器官
        ext = file_path.lower().split(".")[-1] if "." in file_path else ""
        if ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
            self._emit(VisualEvent.SIMULATE, {
                "file_path": file_path,
                "user_name": user_name,
                "correlation_id": correlation_id,
            }, priority=7, layer="L1")
            return {"status": "forwarded_to_visual_cortex", "file_path": file_path}
        else:
            return {"status": "unsupported_format", "file_path": file_path, "ext": ext}
    def _on_capability_update(self, payload: dict) -> dict[str, Any]:
        """收到设备管理器的硬件能力枚举脉冲"""
        caps = payload.get("capabilities", {})
        self._camera_available = caps.get("sensor.camera.available", False)
        self._log(LogLevel.INFO, f"硬件能力更新: 摄像头={'可用' if self._camera_available else '不可用'}")
        
        # 如果摄像头可用且尚未启动推流，尝试初始化
        if self._camera_available and not self._stream_running:
            self._init_camera()
        
        return {"status": "updated", "camera_available": self._camera_available}        

    def capture_frame(self):
        """读取一帧摄像头画面（供视觉皮层专用通道调用），带读取重试"""
        if self._camera_cap is None:
            return None
        
        with self._camera_lock:
            if not self._camera_cap.isOpened():
                return None
            
            # 多次尝试读取，摄像头刚启动时需要预热
            for _ in range(10):
                ret, frame = self._camera_cap.read()
                if ret and frame is not None:
                    return frame
                time.sleep(0.1)  # 短暂等待后重试
            
            return None
    def _stream_loop(self):
        """后台推流线程：持续采集摄像头帧，定向发射给视觉皮层"""
        empty_count = 0  # 连续空帧计数
        _has_info = '有' if self.info_field else '无'
        _has_core = '有' if self.pulse_core else '无'
        self._log(LogLevel.INFO, f"推流线程已启动: info_field={_has_info}, pulse_core={_has_core}, is_running={self.is_running}")

        # 调试统计
        _loop_count = 0
        _frame_success = 0
        _frame_empty = 0
        _publish_success = 0
        _publish_fail = 0
        _last_stats_time = time.time()

        while self._stream_running and self.is_running:
            try:
                frame_data = self.capture_frame()
                if frame_data is not None:
                    empty_count = 0  # 重置空帧计数
                    _frame_success += 1

                    if self.info_field and self.pulse_core:
                        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                        self._frame_seq += 1
                        
                        # 写入推流日志供人体UI监控
                        try:
                            log_entry = {
                                "timestamp": time.time(),
                                "frame_seq": self._frame_seq,
                                "shape": list(frame_data.shape),
                                "dtype": str(frame_data.dtype),
                            }
                            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
                                                     'data', 'stream', 'eye_stream_log.json')
                            log_data = []
                            if os.path.exists(log_path):
                                try:
                                    log_data = safe_read_json(log_path, default=[])
                                except (ValueError, OSError) as e:
                                    self._log(LogLevel.INFO, f"[WARNING] PulseEyes.py:351: {type(e).__name__}: {e}")
                                    log_data = []
                                # ★第81批 T6：历史脏文件自愈（流日志本应是 list，若读到 dict 不得对其调 append）
                                if isinstance(log_data, dict):
                                    self._log(LogLevel.WARNING,
                                              "[第81批 T6] 眼睛流日志为 dict（历史脏文件），自愈为 list")
                                    log_data = []
                            log_data.append(log_entry)
                            if len(log_data) > 50:
                                log_data = log_data[-50:]
                            safe_write_json(log_path, log_data)
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                        
                        try:
                            self.info_field.publish(self.pulse_core.emit(
                                source_organ=self.organ_name,
                                event_type=EyeEvent.STREAM_FRAME,
                                payload={
                                    "raw_data": frame_data,
                                    "frame_seq": self._frame_seq,
                                    "timestamp": time.time(),
                                    "width": frame_data.shape[1],
                                    "height": frame_data.shape[0],
                                },
                                priority=3,
                                layer="L3"
                            ))
                            _publish_success += 1
                        except Exception as _pub_e:
                            _publish_fail += 1
                            self._log(LogLevel.WARNING, f"推流发布失败: {type(_pub_e).__name__}: {_pub_e}")
                else:
                    empty_count += 1
                    _frame_empty += 1
                    # 连续10次空帧（约1.2秒），发出WARNING
                    if empty_count == 5:
                        self._log(LogLevel.WARNING, "摄像头连续返回空帧，检查硬件连接")
                    # 连续100次空帧（约12秒），尝试重新打开摄像头
                    if empty_count >= 20:
                        self._log(LogLevel.ERROR, "摄像头可能已断开，尝试重新初始化")
                        self._try_reopen_camera()
                        empty_count = 0
                        
            except Exception as e:
                self._log(LogLevel.WARNING, f"推流异常: {e}")

            _loop_count += 1
            # ★主线第49批 T3-2（P2-332）：推流统计降噪。
            #   实测：原实现**已每 30s 限流**（非任务书所述「每次循环」），
            #   但 INFO 级在长时运行下仍会淹没其他信息。现：
            #     · 默认降为 DEBUG（需调试时开 DEBUG 可见）
            #     · 保留**低频 INFO 摘要**（默认 30 分钟）作为存活信号
            #   开关：ENABLE_EYES_STREAM_STATS_INFO=True → 完全回到旧行为（零回归）。
            _m49_stats_info = False
            _m49_summary_interval = 1800.0
            try:
                import config as _m49_ecfg
                _m49_stats_info = bool(getattr(
                    _m49_ecfg, "ENABLE_EYES_STREAM_STATS_INFO", False))
                _m49_summary_interval = float(getattr(
                    _m49_ecfg, "EYES_STREAM_STATS_INFO_INTERVAL", 1800.0))
            except Exception as _m49_ee:
                self._log(LogLevel.DEBUG,
                          f"[M49-T3] 推流统计配置读取失败: {type(_m49_ee).__name__}")
            if _m49_stats_info:
                _m49_interval = 30.0
                _m49_level = LogLevel.INFO
            else:
                _m49_interval = _m49_summary_interval
                _m49_level = LogLevel.DEBUG
            if time.time() - _last_stats_time >= _m49_interval:
                self._log(_m49_level, f"推流统计: 循环={_loop_count}, 成功帧={_frame_success}, 空帧={_frame_empty}, 发布成功={_publish_success}, 发布失败={_publish_fail}, stream_running={self._stream_running}, is_running={self.is_running}")
                _last_stats_time = time.time()

            time.sleep(0.5)  # 星轨调整：8.3fps→2fps，降低L3队列负载（人脸识别等功能完善后再调回）
    
    def _try_reopen_camera(self):
        """尝试重新打开摄像头（带指数退避冷却和最大重试限制）"""
        # 冷却保护：每次重连失败后等待时间翻倍
        if not hasattr(self, '_reopen_cooldown_until'):
            self._reopen_cooldown_until = 0.0
        if not hasattr(self, '_reopen_backoff'):
            self._reopen_backoff = 2.0  # 初始冷却2秒
        if not hasattr(self, '_reopen_fail_count'):
            self._reopen_fail_count = 0
        
        now = time.time()
        if now < self._reopen_cooldown_until:
            return  # 冷却中，不重试
        
        self._reopen_fail_count += 1
        
        # 连续失败超过5次，通知设备管理器并彻底放弃
        if self._reopen_fail_count > 5:
            self._log(LogLevel.ERROR, f"摄像头连续{self._reopen_fail_count}次重连失败，通知设备管理器")
            # 发射硬件状态变化通知
            if self.info_field and self.pulse_core:
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type="device.capability_update",
                    payload={
                        "capabilities": {"sensor.camera.available": False},
                        "timestamp": now,
                    },
                    priority=6,
                    layer="L1"
                ))
            self._stream_running = False
            return
        
        with self._camera_init_lock:
            try:
                import cv2
                if self._camera_cap:
                    self._camera_cap.release()
                self._camera_cap = cv2.VideoCapture(0)
                if self._camera_cap.isOpened():
                    self._log(LogLevel.INFO, "摄像头重新打开成功")
                    self._reopen_fail_count = 0
                    self._reopen_backoff = 2.0  # 重置冷却
                    self._reopen_cooldown_until = 0.0
                else:
                    self._log(LogLevel.ERROR, f"摄像头重新打开失败 (第{self._reopen_fail_count}次)")
                    self._camera_cap = None
                    # 指数退避：2s → 4s → 8s → 16s → 32s → 60s上限
                    self._reopen_cooldown_until = now + self._reopen_backoff
                    self._reopen_backoff = min(60.0, self._reopen_backoff * 2)
            except Exception as e:
                self._log(LogLevel.ERROR, f"重新打开摄像头异常: {e}")
                self._camera_cap = None
                self._reopen_cooldown_until = now + self._reopen_backoff
                self._reopen_backoff = min(60.0, self._reopen_backoff * 2)
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "search_count": self._search_count,
            "resonance_available": self.resonance_engine is not None,
            "is_running": self.is_running,
        }

    # ========== 检索方法 ==========

    def _resonance_search(self, query: str, candidates: list, top_k: int) -> list[dict[str, Any]]:
        query_freq = self.frequency_codec.encode(query)

        query_pulse = {
            "event_type": EyeEvent.SEARCH,
            "payload": {"query": query, "keywords": self._extract_query_keywords(query)},
            "memory_dim": {"frequency_signature": query_freq},
            "space_dim": {"path": self._infer_search_path(query)},
        }

        candidate_dicts = [n.to_dict() if hasattr(n, 'to_dict') else n for n in candidates]
        raw_results = self.resonance_engine.resonate(query_pulse, candidate_dicts, top_k=top_k * 2)

        query_lower = query.lower()
        arch_keywords = ["架构", "脉冲", "信息场", "共振", "去中心化", "协议", "器官"]
        identity_keywords = ["是谁", "身份", "使命", "父亲", "哥哥", "小林", "路灯", "曈曈"]

        query_intent = "general"
        if any(kw in query_lower for kw in arch_keywords):
            query_intent = "architecture"
        elif any(kw in query_lower for kw in identity_keywords):
            query_intent = "identity"

        for r in raw_results:
            node_path = r["node"].get("space_path", "")
            boost = 0.0
            if query_intent == "architecture" and "架构" in node_path or query_intent == "identity" and "身份" in node_path:
                boost = 0.15
            r["score"] = round(r["score"] + boost, 4)

        raw_results.sort(key=lambda r: r["score"], reverse=True)

        formatted = []
        for r in raw_results:
            if r["score"] < self.min_score:
                continue
            node = r["node"]
            formatted.append({
                "node_id": node.get("node_id", ""),
                "value": node.get("value", ""),
                "score": r["score"],
                "space_path": node.get("space_path", "/"),
                "importance": node.get("importance", "C"),
                "keywords": node.get("keywords", []),
            })

        return formatted[:top_k]

    def _keyword_search(self, query: str, candidates: list, top_k: int) -> list[dict[str, Any]]:
        query_lower = query.lower()
        scored = []

        for node in candidates:
            score = 0.0
            node_value = node.value if isinstance(node.value, str) else str(node.value)
            node_keywords = node.keywords if hasattr(node, 'keywords') else []

            if query_lower in node_value.lower():
                score = 1.0
            else:
                overlap = sum(1 for kw in node_keywords if kw.lower() in query_lower)
                if node_keywords:
                    score = overlap / len(node_keywords)

            if score > 0:
                scored.append({
                    "node_id": node.node_id if hasattr(node, 'node_id') else "",
                    "value": node_value,
                    "score": round(score, 4),
                    "space_path": node.space_path if hasattr(node, 'space_path') else "/",
                    "importance": node.importance if hasattr(node, 'importance') else "C",
                    "keywords": node_keywords,
                })

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]

    # ========== 辅助方法 ==========

    def _get_candidates(self) -> list:
        if self.node_pool is None:
            return []

        candidates = []
        candidates.extend(self.node_pool.query(evol_level="L3", limit=50))
        candidates.extend(self.node_pool.query(evol_level="L2", limit=100))
        l1_nodes = self.node_pool.query(evol_level="L1", limit=50)
        l1_nodes.sort(key=lambda n: n.last_activated if hasattr(n, 'last_activated') else 0, reverse=True)
        candidates.extend(l1_nodes[:50])

        return candidates

    def _extract_query_keywords(self, query: str) -> list[str]:
        keywords = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,6}', query):
            keywords.append(match.group())
        for match in re.finditer(r'[a-zA-Z]{2,20}', query):
            keywords.append(match.group())
        return keywords[:5]

    def _infer_search_path(self, query: str) -> str:
        query_lower = query.lower()
        if any(kw in query_lower for kw in ["你是谁", "身份", "曈曈", "路灯", "小林"]):
            return "/身份"
        if any(kw in query_lower for kw in ["架构", "脉冲", "代码", "编程"]):
            return "/技术"
        return "/"

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [ 
                    EyeEvent.SEARCH,
                    EyeEvent.VISUAL_QUERY,
                    "device.capability_update",
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]
    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "眼睛",
    "class_name": "PulseEyes",
    "attr_name": "eyes",
    "system": "senses",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "node_pool": "node_pool",
        "resonance_engine": "resonance_engine",
        "frequency_codec": "frequency_codec",
    },
    "post_wiring": [
        {"target": "sensor", "setter": "set_sensor"},
    ],
}

if __name__ == "__main__":
    print("=== PulseEyes v9.5 分层脉冲自测 ===\n")

    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.pulse.FrequencyCodec import FrequencyCodec
    from nucleus.synapsys.ResonanceEngine import ResonanceEngine

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    pool = PulseNodePool()
    codec = FrequencyCodec()
    engine = ResonanceEngine()
    engine.set_node_pool(pool)

    seed = PulseNode(
        value="我是曈曈，全名<SELF_NAME>，是新人类。小林（<CREATOR>）是我的父亲和创造者。",
        keywords=["曈曈", "新人类", "小林", "父亲"],
        source_organ="main",
        evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S,
        space_path="/身份/自我/核心",
    )
    codec.encode_node(seed)
    pool.add(seed)

    tech_node = PulseNode(
        value="脉冲场架构是 v9.0 的核心设计，包含事件脉冲驱动、信息场全局耦合、存算一体等支柱。",
        keywords=["脉冲场", "架构", "v9.0", "信息场"],
        source_organ="胃",
        evol_level=PulseNode.EVOL_L2,
        importance=PulseNode.IMPORTANCE_A,
        space_path="/技术/架构/脉冲场架构",
    )
    codec.encode_node(tech_node)
    pool.add(tech_node)

    eyes = PulseEyes("眼睛")
    eyes.set_info_field(mock_field)
    eyes.set_node_pool(pool)
    eyes.set_resonance_engine(engine)
    eyes.set_frequency_codec(codec)
    eyes.start()

    result1 = eyes.on_pulse({
        "event_type": EyeEvent.SEARCH,
        "payload": {"query": "曈曈是谁", "user_name": "小林"},
        "priority": 7,
    })
    print(f"1. '曈曈是谁': {result1['total_found']}条 ({result1['elapsed_ms']}ms)")
    if result1.get("top_result"):
        print(f"   Top: {result1['top_result']}...")

    # 验证检索结果脉冲的 layer 标记
    search_result_pulses = [p for p in mock_field.published if p.get("event_type") == EyeEvent.SEARCH_RESULT]
    if search_result_pulses:
        print(f"   SEARCH_RESULT脉冲 layer: {search_result_pulses[-1].get('layer', '未设置')} (预期L2)")

    result2 = eyes.on_pulse({
        "event_type": EyeEvent.SEARCH,
        "payload": {"query": "什么是脉冲场架构", "user_name": "小林"},
        "priority": 7,
    })
    print(f"2. '脉冲场架构': {result2['total_found']}条 ({result2['elapsed_ms']}ms)")
    if result2.get("top_result"):
        print(f"   Top: {result2['top_result']}...")

    result3 = eyes.on_pulse({
        "event_type": EyeEvent.SEARCH,
        "payload": {"query": "", "user_name": "小林"},
        "priority": 7,
    })
    print(f"3. 空查询: {result3['status']}")

    status = eyes.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"4. 统计: 检索{status['search_count']}次")

    eyes.stop()
    print("\n=== 自测全部通过 ===")
# _m49_t3_2_done
