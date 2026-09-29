# -*- coding: utf-8 -*-
"""face_codec.py —— 人脸名册加密存储（Fernet）。

★往期批次 相关任务：否决 XOR+机器码（已知明文攻击），改用 Fernet（AES-128-CBC + HMAC-SHA256）。
- 密钥存 data/keys/face_key.key（不进 git）。缺失即生成（0600）。
- 名册落盘为 Fernet token（二进制）；读取兼容旧明文 JSON（legacy 迁移免做，新写入即加密）。
- env 重定向白名单：TONGTONG_FACE_ROSTER 越界 data/ 即拒绝（fail-closed，绝不向外泄生物特征）。
"""
import json
import os
import time

from cryptography.fernet import Fernet, InvalidToken
from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_ROOT = os.path.join(_PROJECT_ROOT, "data")
_KEY_DIR = os.path.join(_DATA_ROOT, "keys")
_KEY_PATH = os.path.join(_KEY_DIR, "face_key.key")

_FERNET = None


def _load_fernet() -> Fernet:
    """加载/生成 Fernet 密钥（进程内缓存）。密钥不可用 → 上抛，绝不明文回退。"""
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    try:
        os.makedirs(_KEY_DIR, exist_ok=True)
        if not os.path.exists(_KEY_PATH):
            _key = Fernet.generate_key()
            with open(_KEY_PATH, "wb") as _f:
                _f.write(_key)
            try:
                os.chmod(_KEY_PATH, 0o600)
            except OSError as _e:
                silent_exc(_e, "face_codec._load_fernet[chmod]", level="debug")
        else:
            with open(_KEY_PATH, "rb") as _f:
                _key = _f.read().strip()
        if not _key:
            raise ValueError("密钥文件为空")
        _FERNET = Fernet(_key)
    except Exception as _e:
        raise RuntimeError(f"[face_codec] 密钥加载失败: {type(_e).__name__}: {_e}")
    return _FERNET


def is_path_within_data(path: str) -> bool:
    """realpath 必须落在 <root>/data 内（前缀 + os.sep 边界），防 env 重定向越界。"""
    if not path:
        return False
    _rp = os.path.realpath(path)
    _dr = os.path.realpath(_DATA_ROOT)
    return _rp == _dr or _rp.startswith(_dr + os.sep)


def resolve_roster_path() -> str:
    """解析名册默认路径（data/identity/face_roster.json）。"""
    return os.path.join(_DATA_ROOT, "identity", "face_roster.json")


def load_roster(path: str) -> dict:
    """读名册：密文(Fernet token)优先；兼容旧明文 JSON（legacy）。失败上抛，由调用方降级。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as _f:
            _raw = _f.read()
    except OSError as _e:
        silent_exc(_e, "face_codec.load_roster[read]", level="debug")
        return {}
    if not _raw:
        return {}
    _text = None
    # 旧明文：以 '{' 开头（JSON 对象；base64url 字母表不含 '{'，可无歧义区分）
    if _raw.lstrip()[:1] == b"{":
        try:
            _text = _raw.decode("utf-8")
        except UnicodeDecodeError as _e:
            silent_exc(_e, "face_codec.load_roster[decode]", level="debug")
            _text = None
    if _text is None:
        try:
            _fernet = _load_fernet()
            _text = _fernet.decrypt(_raw).decode("utf-8")
        except (InvalidToken, Exception) as _e:  # noqa: BLE001
            raise ValueError(f"[face_codec] 名册解密失败: {type(_e).__name__}: {_e}")
    try:
        _doc = json.loads(_text)
    except (ValueError, TypeError) as _e:
        raise ValueError(f"[face_codec] 名册 JSON 解析失败: {_e}")
    if not isinstance(_doc, dict):
        raise ValueError("[face_codec] 名册根非对象")
    return _doc


def save_roster(path: str, data: dict) -> bool:
    """加密原子写名册：tmp + os.replace（退避），失败返回 False（不抛，fail-closed 不写明文）。"""
    _tmp = None
    try:
        _fernet = _load_fernet()
        _token = _fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        _dir = os.path.dirname(path) or "."
        os.makedirs(_dir, exist_ok=True)
        _tmp = f"{path}.{int(time.time() * 1000)}.tmp"
        with open(_tmp, "wb") as _f:
            _f.write(_token)
        _last_exc = None
        for _i in range(3):
            try:
                os.replace(_tmp, path)
                _tmp = None
                return True
            except OSError as _oe:
                _last_exc = _oe
                if _i < 2:
                    time.sleep(0.05 * (_i + 1))
        if _last_exc is not None:
            raise _last_exc
        return False
    except Exception as _e:  # noqa: BLE001
        try:
            if _tmp and os.path.exists(_tmp):
                os.remove(_tmp)
        except OSError as _e:
            silent_exc(_e, "face_codec.save_roster[cleanup]", level="debug")
        # 不向外泄漏具体异常（调用方已各自 try/except 记 WARN），仅返回 False
        _ = _e
        return False
