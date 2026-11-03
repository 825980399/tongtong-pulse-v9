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

import json
import logging
import os
import shutil
import threading
import time

_module_logger = logging.getLogger("nucleus.data.DataAccessLayer")

try:  # ★363刀B1：底层模块用 try 兜底导入，绝不让「记账依赖」把数据层拖崩
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover
    def silent_exc(_e: Exception, where: str = "", level: str = "debug") -> None:
        _module_logger.log(
            {"debug": 10, "info": 20, "warning": 30, "error": 40,
             "critical": 50}.get(level, 10),
            f"[静默异常可见化] {where} {type(_e).__name__}: {_e}")


_WRITE_LOCK = threading.Lock()  # 兼容保留（已不再作为全局唯一写锁，见 D186-A 按路径分锁）
_MAX_BACKUPS = 3

#: ★435刀1：备份分级阈值（字节）。<= 该值 = 关键小文件（保留 1 代）；> 该值 = 大文件（不备份）。
#:   任务书口径：关键小文件（配置/台账）保留 1 代、大文件（快照/日志）不备份。
#:   实测背景：原实现每次写前都 copy2 三代（.bak→.bak.1→.bak.2 逐级滚动），
#:   单次写产生 3 次整文件复制（写放大 4×）；盘上 34 个 .bak 文件、copy2 占 6.4% CPU。
_M435_BACKUP_SMALL_MAX_BYTES = 1024 * 1024  # 1 MiB


def _m435_backup_tier_on() -> bool:
    """★435刀1：备份分级开关（缺省 True）。关闭 ⇒ 逐字回到原 3 代滚动语义。"""
    try:
        import config as _cfg435
        return bool(getattr(_cfg435, "ENABLE_M435_BACKUP_TIER", True))
    except Exception as _e:
        silent_exc(_e, where="nucleus/data/DataAccessLayer.py:_m435_backup_tier_on", level="debug")
        return True


def _m435_backup_small_max_bytes() -> int:
    """★435刀1：小文件阈值（字节）← config.M435_BACKUP_SMALL_MAX_BYTES，回退 1 MiB。"""
    try:
        import config as _cfg435
        _v = int(getattr(_cfg435, "M435_BACKUP_SMALL_MAX_BYTES", _M435_BACKUP_SMALL_MAX_BYTES))
        return _v if _v > 0 else _M435_BACKUP_SMALL_MAX_BYTES
    except Exception as _e:
        silent_exc(_e, where="nucleus/data/DataAccessLayer.py:_m435_backup_small_max_bytes", level="debug")
        return _M435_BACKUP_SMALL_MAX_BYTES

# ── D186-A：按规范化路径分锁，消除「写A阻塞写B」的全局互斥 convoy ──
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS = {}  # normcase(abspath) -> threading.Lock
_PATH_LOCK_LRU_MAX = 1024


def _get_path_lock(path):
    """返回 path 对应的进程内写锁（按规范化路径分锁，带 LRU 收敛）。"""
    _key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        _lk = _PATH_LOCKS.get(_key)
        if _lk is None:
            _lk = threading.Lock()
            _PATH_LOCKS[_key] = _lk
            # LRU 收敛：超过上限丢弃最旧引用（仅弃引用，不影响已持锁线程）
            if len(_PATH_LOCKS) > _PATH_LOCK_LRU_MAX:
                _PATH_LOCKS.pop(next(iter(_PATH_LOCKS)), None)
        return _lk


def _replace_with_retry(tmp_path, target_path, max_retries=3):
    """os.replace 退避重试（D186-D）：缓解 Windows 读者持句柄导致的 WinError 32/5。"""
    _last_exc = None
    for _i in range(max_retries):
        try:
            os.replace(tmp_path, target_path)
            return
        except OSError as _oe:
            _last_exc = _oe
            if _i < max_retries - 1:
                time.sleep(0.05 * (_i + 1))
    if _last_exc is not None:
        raise _last_exc
    raise OSError('replace failed')

# JSON解析失败限流：同一个文件在_WARNING_INTERVAL秒内只输出一次WARNING
_WARNING_INTERVAL = 60.0  # 60秒
_last_warning_time = {}  # path -> timestamp
_last_backup_time = {}   # path -> timestamp


def _keep_backup(path: str) -> None:
    """保留历史备份：小文件 1 代、大文件 0 代（关灰度 ⇒ 原 3 代滚动）。

    ★435刀1：原实现每次写前都 copy2 三代（.bak→.bak.1→.bak.2 逐级滚动），
      单次写产生 **3 次整文件复制**（写放大 4×）。改为按文件大小分级：
        · size <= 阈值（默认 1 MiB，配置/台账类关键小文件）→ 只保留 1 代 `.bak`；
        · size >  阈值（快照/日志类大文件）→ **不备份**（0 代，写放大 1×）。
      `ENABLE_M435_BACKUP_TIER=False` 时逐字回到原 3 代滚动（零行为变化）。
    """
    if not os.path.exists(path):
        return
    try:
        if not _m435_backup_tier_on():
            # —— 回退路径：原 3 代滚动（与改前逐字一致）——
            slots = [f"{path}.bak", f"{path}.bak.1", f"{path}.bak.2"]
            if os.path.exists(slots[2]):
                os.remove(slots[2])
            if os.path.exists(slots[1]):
                shutil.copy2(slots[1], slots[2])
            if os.path.exists(slots[0]):
                shutil.copy2(slots[0], slots[1])
            shutil.copy2(path, slots[0])
            return
        try:
            _size = os.path.getsize(path)
        except OSError as _sz_e:
            silent_exc(_sz_e, where="nucleus/data/DataAccessLayer.py:_keep_backup 取大小", level="debug")
            _size = 0
        if _size > _m435_backup_small_max_bytes():
            # 大文件不备份（0 代）——快照/日志类，重写代价远大于可回溯收益
            return
        # 小文件只保留 1 代：清掉更早的 .bak.1/.bak.2，只维护最新一份 .bak
        for _stale in (f"{path}.bak.1", f"{path}.bak.2"):
            if os.path.exists(_stale):
                try:
                    os.remove(_stale)
                except OSError as _rm_e:
                    silent_exc(_rm_e, where="nucleus/data/DataAccessLayer.py:_keep_backup 清理旧代", level="debug")
        shutil.copy2(path, f"{path}.bak")
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
                    indent=None, encoding="utf-8", default=None):
    """原子写 JSON：tmp + os.replace，失败返回 False（不抛异常）。"""
    _tmp = None
    try:
        # D186-A：备份 I/O 移出路径锁外，缩短锁持有时间，缓解写 convoy
        if backup:
            _keep_backup(path)
        _lk = _get_path_lock(path)
        with _lk:
            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            _tmp = f"{path}.{int(time.time() * 1000)}.tmp"
            with open(_tmp, "w", encoding=encoding) as f:
                json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent, default=default)
                # ★275批刀1：fsync 确保数据真正落盘（非正常终止不留全零/半写文件）
                f.flush()
                os.fsync(f.fileno())
            _replace_with_retry(_tmp, path)
        return True
    except Exception as _exc:
        # ★275批刀1：写入失败不再静默吞（原 debug ⇒ 改 ERROR），
        #   且**保留 tmp 供取证**（原实现删除 tmp ⇒ 现场被抹掉，
        #   无法定位"全零文件"从何而来）。
        _module_logger.error(
            f"[写入失败] path={path} type={type(_exc).__name__} {_exc}"
            f" tmp={'保留:' + _tmp if _tmp and os.path.exists(_tmp) else '无'}")
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
    except Exception as _m363e1:
        # ★363刀B1：原裸 pass。取不到 size（权限/路径消失/被占用）本身就是要看的信号
        _m363_exc_note("dal_getsize", _m363e1, level="warning", where=path)

    # ★360遗留：冷档 gzip 透明读（如 pulse_knowledge_snapshot.json.<ts>.bak.gz）。
    #   必须读侧先兼容再谈压缩——否则压缩后的备份在 _load_from_backup 里
    #   全部读不出来，「有备份但恢复不出节点」比占磁盘更严重。
    if str(path).lower().endswith(".gz"):
        try:
            import gzip as _m360_gzip
            with _m360_gzip.open(path, "rb") as _gf:
                _rawb = _gf.read()
        except Exception as _gzexc:
            _module_logger.error(
                f"[读取失败·gz] path={path} type={type(_gzexc).__name__} {_gzexc}")
            return default
        _gzraw = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                _gzraw = _rawb.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError) as _m369_e:
                silent_exc(_m369_e, where="nucleus/data/DataAccessLayer.py:safe_read_json", level="debug")
                continue
        if _gzraw is None:
            _module_logger.error(f"[读取失败·gz编码] path={path} 四种编码均无法解码")
            return default
        if not _gzraw.strip():
            return default
        return _parse_json_raw(_gzraw, path, default)

    raw = None
    # ★第144批 T-144d：``utf-8-sig`` 置于首位——透明剥离 UTF-8 BOM，
    #   避免带 BOM 的 JSON 被 utf-8 成功解码后 json.loads 报 "Unexpected UTF-8 BOM"。
    #   （无 BOM 时行为与 utf-8 完全一致；有 BOM 则自动去除，免手动清洗。）
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, UnicodeError) as _m369_e:
            silent_exc(_m369_e, where="nucleus/data/DataAccessLayer.py:safe_read_json", level="debug")
            continue
        except Exception as _exc:
            _module_logger.debug(f"[读取失败] path={path} enc={enc} type={type(_exc).__name__} {_exc}")
            return default
    if raw is None:
        return default
    if not raw.strip():
        # 内容为空（只有空白字符），直接返回默认
        return default
    return _parse_json_raw(raw, path, default)


def _parse_json_raw(raw, path, default):
    """★360遗留：从「已解码文本」解析 JSON，解析失败走限流告警 + 归档（语义与原实现逐字一致）。

    抽出来的唯一目的：让 ``.gz`` 冷档与明文档共用**同一套**失败处理，
    避免压缩档走一条「静默 return default」的捷径 —— 那正是 340 刀5 那类
    「通道静默归零」的复现路径（读不出来看起来像「没数据」）。
    """
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
        # D186-A：备份 I/O 移出路径锁外
        if backup:
            _keep_backup(path)
        _lk = _get_path_lock(path)
        with _lk:
            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            _tmp = f"{path}.{int(time.time() * 1000)}.tmp"
            with open(_tmp, "w", encoding=encoding) as f:
                f.write(text)
            _replace_with_retry(_tmp, path)
        return True
    except Exception as _exc:
        _module_logger.debug(f"[写入失败] path={path} type={type(_exc).__name__} {_exc}")
        if _tmp and os.path.exists(_tmp):
            try:
                os.remove(_tmp)
            except Exception as _m363e2:
                # ★363刀B1：临时文件删不掉会**污染下一轮**（撞已存在的 .tmp），必须可见
                _m363_exc_note("dal_rm_tmp", _m363e2, level="warning", where=str(_tmp))
        return False


# ============================================================
# ★363刀B1：裸 pass 收口后的「计数 + 末次快照」出口
# 为什么还要计数：只打日志的话，「同一处异常每分钟 300 次」和「一小时 1 次」
# 在日志里看起来都是「有这条日志」——没有量级就无法判断要不要修。
# ============================================================
_M363_EXC_COUNTS: dict = {}
_M363_EXC_LAST: dict = {}


def _m363_exc_note(tag: str, e: Exception, level: str = "warning",
                   where: str = "") -> None:
    """★363刀B1：记一次「原本被 pass 吞掉」的异常（计数 + 末次快照 + 日志）。"""
    try:
        _M363_EXC_COUNTS[tag] = _M363_EXC_COUNTS.get(tag, 0) + 1
        _M363_EXC_LAST[tag] = {"type": type(e).__name__, "msg": str(e)[:300],
                               "where": where, "ts": time.time()}
        silent_exc(e, where="%s %s" % (tag, where), level=level)
    except Exception as _ne:
        _module_logger.debug("[363B1] 异常记账失败: %s: %s",
                             type(_ne).__name__, _ne)


def m363_exc_stats() -> dict:
    """★363刀B1：读本进程内「原裸 pass」异常计数与末次快照（只读拷贝）。"""
    return {"counts": dict(_M363_EXC_COUNTS), "last": dict(_M363_EXC_LAST)}


def safe_read_text(path, default=""):
    """健壮读文本：文件不存在→默认；编码错误→utf-8/gbk/latin-1 回退。"""
    if not os.path.exists(path):
        return default
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError) as _m369_e:
            silent_exc(_m369_e, where="nucleus/data/DataAccessLayer.py:safe_read_text", level="debug")
            continue
        except Exception as _exc:
            _module_logger.debug(f"[读取失败] path={path} type={type(_exc).__name__} {_exc}")
            return default
    return default
