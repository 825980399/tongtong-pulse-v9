# -*- coding: utf-8 -*-
"""
wecom_notifier.py —— 企业微信通知器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 企业微信消息通知推送
机制: 基于WeComNotifier类实现，包含6个核心方法
定位: 外部接口层
"""
import asyncio
from nucleus.const import LogLevel
from nucleus.logger import get_module_logger
import threading
import time
from aibot import WSClient
from aibot.types import WSClientOptions
from nucleus.aibot_logger import get_aibot_logger  # ★v9.5压制AiBotSDK心跳DEBUG日志
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底





_logger = get_module_logger("wecom_notifier")




class WeComNotifier(SilentLogMixin):
    """企业微信智能机器人通知器"""

    def __init__(self, bot_id: str, secret: str, userid: str):
        self._bot_id = bot_id
        self._secret = secret
        self._userid = userid
        self._client = None
        self._loop = None
        self._thread = None
        self._running = False

    def start(self):
        """启动后台线程，建立长连接"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止通知服务"""
        self._running = False
        # ★LEAK-3修复: 先异步断开 WSClient 连接（disconnect 为异步方法，须 await），
        #   再停止事件循环，避免 WebSocket 连接泄漏（CLOSE_WAIT）
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._async_shutdown())
                )
            except Exception:
                pass

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

    def send(self, title: str, content: str):
        """
        发送通知（非阻塞，失败静默）。
        """
        if not self._running:
            return
        msg = f"[曈曈通知] {title}\n{content}"
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._safe_send, msg)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _safe_send(self, msg: str):
        """在事件循环线程中安全调度发送"""
        try:
            if self._client and self._client.is_connected:
                asyncio.ensure_future(self._async_send(msg))
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    async def _async_send(self, msg: str):
        """异步发送文本消息"""
        try:
            body = {
                "msgtype": "text",
                "text": {"content": msg},
            }
            await self._client.send_message(self._userid, body)
        except Exception:
            # 静默失败，不影响框架
            pass

    async def _async_connect(self):
        """异步建立连接"""
        try:
            options = WSClientOptions(
                bot_id=self._bot_id,
                secret=self._secret,
                # ★v9.5：注入压制 logger（DEBUG/INFO 静默，压制心跳平，WARN/ERROR 转框架日志）
                logger=get_aibot_logger(),
            )
            self._client = WSClient(options)
            await self._client.connect()
        except Exception:
            self._client = None

    def _run_async_loop(self):
        """后台线程的事件循环（含指数退避自动重连）"""
        _reconnect_count = 0
        _max_backoff = 60.0  # type: ignore[possibly-unbound]
        while self._running:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._async_connect())
                if self._client is not None:
                    _reconnect_count = 0
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