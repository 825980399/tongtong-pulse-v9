# -*- coding: utf-8 -*-
"""
DataAccessLayer.py —— 数据访问层

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 统一数据访问接口，隔离存储实现
机制: 函数式模块，包含6个工具函数
定位: 数据基础设施层
"""

import os
import json
import shutil
import time
import threading
import logging


_module_logger = logging.getLogger("nucleus.data.DataAccessLayer")

_WRITE_LOCK = threading.Lock()
_MAX_BACKUPS = 3

# JSON解析失败限流：同一个文件在_WARNING_INTERVAL秒内只输出一次WARNING
_WARNING_INTERVAL = 60.0  # 60秒
_last_warning_time = {}  # path -> timestamp
_last_backup_time = {}   # path -> timestamp


def _keep_backup(path: str) -> None:
    """保留最多 ``_MAX_BACKUPS`` 个历史备份（.bak 最新，.bak.1/.bak.2 旧）。"""
    if not os.path.exists(path):
        return
    try:
        slots = [f"{path}.bak", f"{path}.bak.1", f"{path}.bak.2"]
        if os.path.exists(slots[2]):
            os.remove(slots[2])
        if os.path.exists(slots[1]):
            shutil.copy2(slots[1], slots[2])
        if os.path.exists(slots[0]):
            shutil.copy2(slots[0], slots[1])
        shutil.copy2(path, slots[0])
    except Exception as _exc:
        _module_logger.debug(f"[异常已忽略] backup type={type(_exc).__name__} {_exc}")


def _backup_corrupted(path: str) -> None:
    """将损坏的 JSON 文件归档到 data/corrupted/，文件名带时间戳避免覆盖。"""
    try:
        dest_dir = os.path.join("data", "corrupted")
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.basename(path)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(dest_dir, f"{base}.{stamp}.corrupted")
        n = 0
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(dest_dir, f"{base}.{stamp}.{n}.corrupted")
        shutil.copy2(path, dest)
    except Exception as _exc:
        _module_logger.debug(f"[异常已忽略] corrupted-backup type={type(_exc).__name__} {_exc}")


def safe_write_json(path, data, backup=True, ensure_ascii=False,
                    indent=None, encoding="utf-8"):
    """原子写 JSON：tmp + os.replace，失败返回 False（不抛异常）。"""
    _tmp = None
    try:
        with _WRITE_LOCK:
            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            if backup:
                _keep_backup(path)
            _tmp = f"{path}.{int(time.time() * 1000)}.tmp"
            with open(_tmp, "w", encoding=encoding) as f:
                json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            os.replace(_tmp, path)
        return True
    except Exception as _exc:
        _module_logger.debug(f"[写入失败] path={path} type={type(_exc).__name__} {_exc}")
        if _tmp and os.path.exists(_tmp):
            try:
                os.remove(_tmp)
            except Exception:
                pass
        return False


def safe_read_json(path, default=None):
    """健壮读 JSON：文件不存在→默认；编码错误→utf-8/gbk/latin-1 回退；解析失败→归档并返回默认。
    
    优化（2026-09-10 星轨）：
    1. 空文件（0字节）直接返回default，不输出WARNING（可能是原子写中间状态）
    2. 解析失败日志限流：同一文件60秒内只输出一次WARNING，后续降级为DEBUG
    3. 归档限流：同一文件60秒内只归档一次，避免产生大量重复文件
    """
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    
    # 空文件特殊处理：直接返回默认，不报警（可能是原子写的中间状态）
    try:
        if os.path.getsize(path) == 0:
            return default
    except Exception:
        pass
    
    raw = None
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as _exc:
            _module_logger.debug(f"[读取失败] path={path} enc={enc} type={type(_exc).__name__} {_exc}")
            return default
    if raw is None:
        return default
    if not raw.strip():
        # 内容为空（只有空白字符），直接返回默认
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as _exc:
        now = time.time()
        # 日志限流：同一文件60秒内只输出一次WARNING
        last_warn = _last_warning_time.get(path, 0)
        if now - last_warn >= _WARNING_INTERVAL:
            _module_logger.warning(f"[JSON解析失败·已归档] path={path}: {_exc}")
            _last_warning_time[path] = now
        else:
            _module_logger.debug(f"[JSON解析失败·限流] path={path}: {_exc}")
        # 归档限流：同一文件60秒内只归档一次
        last_backup = _last_backup_time.get(path, 0)
        if now - last_backup >= _WARNING_INTERVAL:
            _backup_corrupted(path)
            _last_backup_time[path] = now
        return default


def safe_write_text(path, text, backup=True, encoding="utf-8"):
    """原子写文本：tmp + os.replace，失败返回 False（不抛异常）。"""
    _tmp = None
    try:
        with _WRITE_LOCK:
            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            if backup:
                _keep_backup(path)
            _tmp = f"{path}.{int(time.time() * 1000)}.tmp"
            with open(_tmp, "w", encoding=encoding) as f:
                f.write(text)
            os.replace(_tmp, path)
        return True
    except Exception as _exc:
        _module_logger.debug(f"[写入失败] path={path} type={type(_exc).__name__} {_exc}")
        if _tmp and os.path.exists(_tmp):
            try:
                os.remove(_tmp)
            except Exception:
                pass
        return False


def safe_read_text(path, default=""):
    """健壮读文本：文件不存在→默认；编码错误→utf-8/gbk/latin-1 回退。"""
    if not os.path.exists(path):
        return default
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as _exc:
            _module_logger.debug(f"[读取失败] path={path} type={type(_exc).__name__} {_exc}")
            return default
    return default
