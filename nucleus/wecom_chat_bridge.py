# -*- coding: utf-8 -*-
"""
wecom_chat_bridge.py —— 企业微信聊天桥

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 企业微信聊天接口桥接
机制: 基于WeComChatBridge类实现，包含10个核心方法
定位: 外部接口层
"""
import asyncio
import os
import threading
import time
from aibot import WSClient
from aibot.types import WSClientOptions
from nucleus.aibot_logger import get_aibot_logger  # ★v9.5压制AiBotSDK心跳DEBUG日志
from nucleus.logger import get_module_logger
from nucleus.const import EyeEvent, LogLevel, MotorEvent
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus._silent_except import silent_exc






_logger = get_module_logger("wecom_chat_bridge")


class WeComChatBridge(SilentLogMixin):
    """企业微信对话桥接器"""

    # ★v23.0：userid → 框架内用户名映射
    USERID_MAP = {
        # ★T-101d：不内置任何实际 userid，需显式配置（环境变量 / config_override.json）
    }

    def __init__(self, bot_id: str, secret: str,
                 info_field=None, pulse_core=None, framework=None,
                 admin_userid: str = ""):
        self._bot_id = bot_id
        self._secret = secret
        self._info_field = info_field
        self._pulse_core = pulse_core
        self._framework = framework
        self._admin_userid = admin_userid  # ★v23.0新增：通知接收者的userid

        self._client: WSClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        # 回复监听器ID（用于退出时注销）
        self._reply_listener_id = None

    # ========== 生命周期 ==========

    def start(self):
        """启动后台线程，建立长连接"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

        # 注册框架回复监听
        if self._info_field:
            self._reply_listener_id = self._info_field.register_condition(
                organ_name="企业微信桥接器-回复监听",
                event_types=["mouth.reply"],
                handler=self._on_framework_reply,
            )
        _logger.info("对话桥接器已启动")

    def stop(self):
        """停止服务"""
        self._running = False
        if self._reply_listener_id and self._info_field:
            try:
                self._info_field.unregister_condition(self._reply_listener_id)
            except Exception as e:
                silent_exc(e, where="nucleus.wecom_chat_bridge::stop L83")
            self._reply_listener_id = None
        # ★LEAK-3修复: 先异步断开 WSClient 连接（disconnect 为异步方法，须 await），
        #   再停止事件循环，避免 WebSocket 连接泄漏（CLOSE_WAIT）
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._async_shutdown())
                )
            except Exception as e:
                silent_exc(e, where="nucleus.wecom_chat_bridge::stop L93")

    async def _async_shutdown(self):
        """异步关闭：断开 WSClient 连接并停止事件循环"""
        try:
            if self._client is not None:
                await self._client.disconnect()
        except Exception as e:
            _logger.warning(f"断开连接异常: {e}")
        finally:
            self._client = None
            if self._loop is not None:
                self._loop.stop()

    def send_notification(self, title: str, content: str):
        """
        ★v23.0新增：发送通知（复用对话长连接，不建立新连接）。
        
        Args:
            title: 通知标题
            content: 通知内容
        """
        msg = f"**[{title}]**\n{content}"
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(
                    self._push_reply, self._admin_userid, msg
                )
            except Exception as e:
                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
    # ========== 事件循环 ==========

    def _run_async_loop(self):
        """独立线程中的事件循环（含指数退避自动重连）"""
        _reconnect_count = 0
        _max_backoff = 60.0  # type: ignore[possibly-unbound]
        while self._running:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._async_connect())
                if self._client is not None:
                    _reconnect_count = 0
                    _logger.info("长连接已建立，开始监听消息")
                self._loop.run_forever()
            except Exception as e:
                _reconnect_count += 1
                _backoff = min(2.0 * (2 ** (_reconnect_count - 1)), _max_backoff)
                _logger.warning(f"连接异常(第{_reconnect_count}次): {e}，{_backoff:.0f}s后重连")  # type: ignore[possibly-unbound]
            finally:
                try:
                    self._loop.close()
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            if self._running and _reconnect_count > 0:
                time.sleep(_backoff)  # type: ignore[possibly-unbound]

    async def _async_connect(self):
        """异步建立连接并注册消息事件"""
        try:
            options = WSClientOptions(
                bot_id=self._bot_id,
                secret=self._secret,
                # ★v9.5：注入压制 logger（DEBUG/INFO 静默，压制心跳平，WARN/ERROR 转框架日志）
                logger=get_aibot_logger(),
            )
            self._client = WSClient(options)
            # 注册消息事件
            self._client.on("message", self._on_wecom_message)
            # ★P2-2修复（第十批）：注册连接状态事件，补上重连成功/断开的日志。
            #   此前只注册 message，网络中断后 SDK 内部自动重连，框架层完全无感知，
            #   星轨 9 小时日志只见「WebSocket connection closed」与
            #   「Failed to create WebSocket connection」两条底层 ERROR，
            #   后续无任何「重连成功」日志，无法判断企业微信是否真的恢复在线。
            #   此处注册 connected/disconnected/reconnecting 三事件，
            #   断开、重连中、重连成功均有框架日志可追溯。
            try:
                self._client.on("connected", self._on_ws_connected)
                self._client.on("disconnected", self._on_ws_disconnected)
                self._client.on("reconnecting", self._on_ws_reconnecting)
            except Exception:
                # 旧版 SDK 可能不支持某些事件名，注册失败不影响主流程
                _logger.debug("连接状态事件注册失败(旧版SDK可能不支持)，忽略")
            await self._client.connect()
            _logger.info("长连接已建立")
        except Exception as e:
            _logger.error(f"连接失败: {e}")
            self._client = None

    # ========== 企业微信连接状态回调（P2-2） ==========

    def _on_ws_connected(self, *_args):
        """WebSocket 连接建立（初次或重连成功）。"""
        _logger.info("企业微信长连接已建立(connected)")

    def _on_ws_disconnected(self, reason: str = "", *_args):
        """WebSocket 连接断开，记录原因供排查。"""
        _logger.warning(f"企业微信长连接断开: {reason or '未知原因'}")

    def _on_ws_reconnecting(self, attempt: int = 0, *_args):
        """正在重连（SDK 内部指数退避），记录第 N 次尝试。"""
        _logger.info(f"企业微信正在重连(第{attempt}次)")

    # ========== 企业微信消息处理 ==========

    def _on_wecom_message(self, frame: dict):
        """收到企业微信消息（在事件循环线程中）"""
        try:
            body = frame.get("body", {})
            msgtype = body.get("msgtype", "")
            userid = body.get("from", {}).get("userid", "")
            user_name = self.USERID_MAP.get(userid, userid or "访客")

            if msgtype == "text":
                text_content = body.get("text", {}).get("content", "")
                # ★立即确认收到
                self._quick_ack(frame, f"我收到了，正在思考「{text_content[:20]}...」")
                # ★异步发送到框架
                self._send_to_framework(text_content, user_name, userid)
            elif msgtype == "image":
                # ★图片消息：下载解密后交给视觉皮层OCR
                self._quick_ack(frame, "我收到了图片，正在识别...")
                asyncio.ensure_future(self._handle_image_message(body, userid, user_name))
            elif msgtype == "file":
                # ★文件消息：下载解密后交给框架处理
                self._quick_ack(frame, "我收到了文件，正在处理...")
                asyncio.ensure_future(self._handle_file_message(body, userid, user_name))
            elif msgtype == "voice":
                # 语音消息：文档说已转为文字
                text_content = body.get("voice", {}).get("content", "")
                if text_content:
                    self._quick_ack(frame, f"我收到了，正在思考「{text_content[:20]}...」")
                    self._send_to_framework(text_content, user_name, userid)
                else:
                    self._quick_ack(frame, "我收到了语音，但没听清内容。")
            else:
                self._quick_ack(frame, "这条消息我还不会处理，试试发文字给我。")

        except Exception as e:
            _logger.error(f"消息处理异常: {e}")
    async def _handle_image_message(self, body: dict, userid: str, user_name: str):
        """★v23.0新增：处理图片消息——下载解密后交给视觉皮层OCR"""
        try:
            # ★v23.0修复：设置回复目标
            self._last_reply_target = {"userid": userid, "user_name": user_name}
            
            image_info = body.get("image", {})
            url = image_info.get("url", "")
            aes_key = image_info.get("aeskey", "")
            if not url:
                return

            # 下载解密
            file_data, filename = await self._client.download_file(url, aes_key)
            if not file_data:
                return

            # 保存到临时目录
            upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            # ★v25.0安全修复(P0-6): 净化外部可控文件名，防止路径穿越写出上传目录
            _base = os.path.basename(filename or "file")
            _base = _base.replace(os.sep, "_").replace("/", "_")
            safe_name = f"wecom_{int(time.time())}_{_base}"
            file_path = os.path.join(upload_dir, safe_name)
            if not os.path.realpath(file_path).startswith(os.path.realpath(upload_dir)):
                _logger.warning(f"非法上传文件名被拒绝: {filename}")
                return
            with open(file_path, 'wb') as f:
                f.write(file_data)

            # 发射视觉查询脉冲给视觉皮层做OCR
            if self._info_field and self._pulse_core:
                pulse = self._pulse_core.emit(
                    source_organ="企业微信",
                    event_type=EyeEvent.VISUAL_QUERY,
                    payload={
                        "file_path": file_path,
                        "user_name": user_name,
                        "task_type": "ocr",
                    },
                    priority=7,
                    layer="L1",
                )
                self._info_field.publish(pulse)
        except Exception as e:
            _logger.error(f"图片处理异常: {e}")

    async def _handle_file_message(self, body: dict, userid: str, user_name: str):
        """★v23.0新增：处理文件消息——下载解密后根据类型路由"""
        try:
            # ★v23.0修复：设置回复目标，这样框架处理完的回复能推回企业微信
            self._last_reply_target = {"userid": userid, "user_name": user_name}

            file_info = body.get("file", {})
            url = file_info.get("url", "")
            aes_key = file_info.get("aeskey", "")
            if not url:
                return

            # 下载解密
            file_data, filename = await self._client.download_file(url, aes_key)
            if not file_data:
                return

            # 保存到临时目录
            upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            # ★v25.0安全修复(P0-6): 净化外部可控文件名，防止路径穿越写出上传目录
            _base = os.path.basename(filename or "file")
            _base = _base.replace(os.sep, "_").replace("/", "_")
            safe_name = f"wecom_file_{int(time.time())}_{_base}"
            file_path = os.path.join(upload_dir, safe_name)
            if not os.path.realpath(file_path).startswith(os.path.realpath(upload_dir)):
                _logger.warning(f"非法上传文件名被拒绝: {filename}")
                return
            with open(file_path, 'wb') as f:
                f.write(file_data)

            # ★v23.0修复：根据文件扩展名路由
            _ext = os.path.splitext(filename)[1].lower() if filename else ""

            if _ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'):
                # 图片 → 视觉皮层OCR
                _event_type = EyeEvent.VISUAL_QUERY
                _pulse_payload = {
                    "file_path": file_path,
                    "user_name": user_name,
                    "task_type": "ocr",
                }
            elif _ext == '.pdf':
                # PDF → 视觉皮层PDF提取
                _event_type = EyeEvent.VISUAL_QUERY
                _pulse_payload = {
                    "file_path": file_path,
                    "user_name": user_name,
                    "task_type": "pdf",
                }
            else:
                # 其他文件 → 文件消化器
                _event_type = MotorEvent.FILE_DIGEST
                _pulse_payload = {
                    "file_path": file_path,
                    "user_name": user_name,
                }

            if self._info_field and self._pulse_core:
                pulse = self._pulse_core.emit(
                    source_organ="企业微信",
                    event_type=_event_type,
                    payload=_pulse_payload,
                    priority=7,
                    layer="L1",
                )
                self._info_field.publish(pulse)
        except Exception as e:
            _logger.error(f"文件处理异常: {e}")
    def _quick_ack(self, frame: dict, content: str):
        """立即确认收到（markdown格式，带chat_type）"""
        if not self._client:
            return
        try:
            userid = frame.get("body", {}).get("from", {}).get("userid", "")
            if not userid:
                return
            asyncio.ensure_future(
                self._async_send_message(userid, f"💭 {content}")
            )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _send_to_framework(self, content: str, user_name: str, userid: str):
        """将消息发射到框架的对话链路"""
        if not self._info_field or not self._pulse_core:
            return
        try:
            # ★保存最后回复目标，供回复时使用
            self._last_reply_target = {
                "userid": userid,
                "user_name": user_name,
            }
            chat_pulse = self._pulse_core.emit(
                source_organ="企业微信",
                event_type="chat.message",
                payload={
                    "content": content,
                    "original_input": content,
                    "user_name": user_name,
                    "is_complex": len(content) > 50,
                    "code_blocks": [],
                    "file_paths": [],
                },
                priority=5,
                layer="L1",
            )
            self._info_field.publish(chat_pulse)
        except Exception as e:
            _logger.error(f"发射消息失败: {e}")

    # ========== 框架回复处理 ==========

    def _on_framework_reply(self, pulse: dict):
        """收到框架回复，推送到企业微信"""
        try:
            content = pulse.get("payload", {}).get("content", "")
            if not content:
                return
            userid = getattr(self, "_last_reply_target", {}).get("userid", "")
            if not userid:
                return
            # 异步推送到企业微信
            if self._loop:
                self._loop.call_soon_threadsafe(
                    self._push_reply, userid, content
                )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _push_reply(self, userid: str, content: str):
        """在事件循环中推送回复"""
        try:
            if self._client and self._client.is_connected:
                asyncio.ensure_future(
                    self._async_send_message(userid, content)
                )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    async def _async_send_message(self, userid: str, content: str):
        """主动推送消息（markdown格式，带chat_type=1单聊）"""
        try:
            # ★v23.0修复：主动推送支持markdown，需要chat_type
            body = {
                "msgtype": "markdown",
                "chat_type": 1,  # 1=单聊
                "markdown": {"content": content},
            }
            await self._client.send_message(userid, body)
        except Exception as e:
            _logger.warning(f"推送失败: {e}")