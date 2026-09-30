"""tcp_client —— 躯体TCP通信客户端

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc


class RobotBodyTCP:
    """躯体TCP通信客户端"""
    
    # 协议常量
    HEADER = 0xAA
    FOOTER = 0xBB
    PORT = 8080
    
    # 指令类型
    CMD_SERVO = 0x01        # 舵机角度控制
    CMD_SPEECH = 0x02       # 语音播报文本
    CMD_TRACK = 0x03        # 云台跟随偏移
    CMD_STATUS = 0x04       # 设备状态查询
    CMD_IMAGE = 0x05        # 图像帧上传（下位机→上位机）
    CMD_AUDIO = 0x06        # 音频流上传（下位机→上位机）
    
    def __init__(self, host: str = "192.168.4.1"):
        self._host = host
        self._sock: socket.socket | None = None
        self._running = False
        self._recv_buffer = bytearray()
        self._recv_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        
        # 回调注册
        self._image_callback: Callable[[bytes], None] | None = None
        self._audio_callback: Callable[[bytes], None] | None = None
        self._status_callback: Callable[[dict[str, Any]], None] | None = None
        
        # 统计
        self._packets_sent = 0
        self._packets_received = 0
        self._last_heartbeat = 0.0
        self._connected = False
    
    def set_image_callback(self, callback: Callable[[bytes], None]):
        """注册图像帧回调"""
        self._image_callback = callback
    
    def set_audio_callback(self, callback: Callable[[bytes], None]):
        """注册音频流回调"""
        self._audio_callback = callback
    
    def set_status_callback(self, callback: Callable[[dict[str, Any]], None]):
        """注册设备状态回调"""
        self._status_callback = callback
    
    def connect(self) -> bool:
        """连接ESP32服务端"""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5)
            self._sock.connect((self._host, self.PORT))
            self._sock.settimeout(1)
            self._connected = True
            self._running = True
            self._last_heartbeat = time.time()
            
            # 启动接收线程
            self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self._recv_thread.start()
            
            # 启动心跳线程
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._heartbeat_thread.start()
            
            print(f"[RobotBodyTCP] 已连接 ESP32: {self._host}:{self.PORT}")
            return True
        except Exception as e:
            print(f"[RobotBodyTCP] 连接失败: {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """断开连接"""
        self._running = False
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception as e:
                silent_exc(e, where="tcp_client.disconnect:97")
            self._sock = None
        print("[RobotBodyTCP] 已断开连接")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def send_servo_command(self, channel: int, angle: int) -> bool:
        """发送舵机角度指令（0x01）"""
        if not 0 <= channel <= 255 or not 0 <= angle <= 180:
            return False
        payload = struct.pack('BB', channel, angle)
        return self._send_packet(self.CMD_SERVO, payload)
    
    def send_speech_text(self, text: str) -> bool:
        """发送语音播报指令（0x02）"""
        payload = text.encode('utf-8')
        return self._send_packet(self.CMD_SPEECH, payload)
    
    def send_track_offset(self, offset_x: int, offset_y: int) -> bool:
        """发送云台跟随偏移指令（0x03）"""
        # 使用有符号16位整数，小端序
        payload = struct.pack('<hh', offset_x, offset_y)
        return self._send_packet(self.CMD_TRACK, payload)
    
    def send_status_query(self) -> bool:
        """发送设备状态查询（0x04）"""
        return self._send_packet(self.CMD_STATUS, b'')
    
    def _send_packet(self, cmd_type: int, payload: bytes) -> bool:
        """封装并发送标准数据包"""
        if not self._sock or not self._connected:
            return False
        
        data_len = len(payload)
        if data_len > 255:
            print(f"[RobotBodyTCP] 载荷过长: {data_len}字节")
            return False
        
        # 计算校验和（指令类型 + 长度 + 载荷，取低8位）
        checksum = (cmd_type + data_len + sum(payload)) & 0xFF
        
        # 组装数据包
        packet = struct.pack('BBBB', self.HEADER, cmd_type, data_len, checksum)
        packet += payload
        packet += struct.pack('B', self.FOOTER)
        
        try:
            with self._lock:
                self._sock.sendall(packet)
            self._packets_sent += 1
            return True
        except Exception as e:
            print(f"[RobotBodyTCP] 发送失败: {e}")
            self._connected = False
            return False
    
    def _recv_loop(self):
        """接收循环：处理粘包，完整解析单包"""
        while self._running:
            try:
                if self._sock:
                    data = self._sock.recv(4096)
                    if data:
                        self._recv_buffer.extend(data)
                        self._parse_packets()
            except TimeoutError:
                continue
            except Exception as e:
                if self._running:
                    print(f"[RobotBodyTCP] 接收异常: {e}")
                    self._connected = False
                    self._attempt_reconnect()
            time.sleep(0.01)
    
    def _parse_packets(self):
        """从接收缓冲区解析完整数据包"""
        while len(self._recv_buffer) >= 5:  # 最小包长：0xAA + 类型 + 长度 + 校验 + 0xBB
            # 查找包头
            header_idx = self._recv_buffer.find(bytes([self.HEADER]))
            if header_idx == -1:
                self._recv_buffer.clear()
                return
            if header_idx > 0:
                del self._recv_buffer[:header_idx]
            
            # 检查是否有足够数据
            if len(self._recv_buffer) < 5:
                return
            
            cmd_type = self._recv_buffer[1]
            data_len = self._recv_buffer[2]
            checksum_received = self._recv_buffer[3]
            
            total_len = 5 + data_len
            if len(self._recv_buffer) < total_len:
                return
            
            # 检查包尾
            if self._recv_buffer[total_len - 1] != self.FOOTER:
                del self._recv_buffer[0]
                continue
            
            # 提取载荷
            payload = bytes(self._recv_buffer[4:4 + data_len])
            
            # 验证校验和
            checksum_calc = (cmd_type + data_len + sum(payload)) & 0xFF
            if checksum_calc != checksum_received:
                print(f"[RobotBodyTCP] 校验失败: 收到{checksum_received} 计算{checksum_calc}")
                del self._recv_buffer[:total_len]
                continue
            
            # 分发处理
            self._dispatch_packet(cmd_type, payload)
            del self._recv_buffer[:total_len]
            self._packets_received += 1
    
    def _dispatch_packet(self, cmd_type: int, payload: bytes):
        """根据指令类型分发数据"""
        if cmd_type == self.CMD_IMAGE and self._image_callback:
            self._image_callback(payload)
        elif cmd_type == self.CMD_AUDIO and self._audio_callback:
            self._audio_callback(payload)
        elif cmd_type == self.CMD_STATUS and self._status_callback:
            # 解析设备状态
            if len(payload) >= 4:
                voltage = payload[0] / 10.0
                psram_free = (payload[1] << 8) | payload[2]
                servo_temp = payload[3]
                self._status_callback({
                    "voltage": voltage,
                    "psram_free_kb": psram_free,
                    "servo_temp": servo_temp,
                })
        self._last_heartbeat = time.time()
    
    def _heartbeat_loop(self):
        """心跳维持：定时查询设备状态"""
        while self._running:
            if self._connected:
                self.send_status_query()
                # 检查心跳超时
                if time.time() - self._last_heartbeat > 3:
                    print("[RobotBodyTCP] 心跳超时，尝试重连...")
                    self._connected = False
                    self._attempt_reconnect()
            time.sleep(1)
    
    def _attempt_reconnect(self):
        """断线重连"""
        if self._sock:
            try:
                self._sock.close()
            except Exception as e:
                silent_exc(e, where="tcp_client._attempt_reconnect:252")
            self._sock = None
        
        time.sleep(2)
        print("[RobotBodyTCP] 正在重连...")
        self.connect()
    
    def get_stats(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "host": self._host,
            "port": self.PORT,
            "packets_sent": self._packets_sent,
            "packets_received": self._packets_received,
            "last_heartbeat": self._last_heartbeat,
            "buffer_size": len(self._recv_buffer),
        }