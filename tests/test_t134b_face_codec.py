# -*- coding: utf-8 -*-
"""往期批次 相关任务：人脸名册加密 face_codec 单元测试。

覆盖：
  1) 加解密往返：save_roster → 磁盘密文（不含明文 encoding128）→ load_roster 等价
  2) 旧明文兼容：遗留明文 JSON 名册可被 load_roster 正常读取（迁移免做）
  3) 路径白名单：is_path_within_data 对 data/ 内外正确判定
  4) 密钥自举：首次 save 自动生成 data/keys/face_key.key
"""
import json
import os

import nucleus.security.face_codec as _fc


def test_encrypt_decrypt_roundtrip(tmp_path):
    p = str(tmp_path / "roster.json")
    data = {"version": 1, "updated_at": 1.0,
            "faces": {"张三": {"encoding128": [0.1] * 128, "enrolled_at": 1.0,
                               "source": "auto", "hits": 0, "tolerance_override": None}}}
    assert _fc.save_roster(p, data) is True
    # 磁盘应为密文：不得含明文特征串
    with open(p, "rb") as f:
        raw = f.read()
    assert b"encoding128" not in raw, "名册明文泄漏到磁盘"
    assert b"{" not in raw[:1], "名册未加密（首字节应为密文）"
    # 读回等价
    got = _fc.load_roster(p)
    assert got == data, "加解密往返不相等"


def test_legacy_plaintext_compat(tmp_path):
    p = str(tmp_path / "legacy.json")
    legacy = {"version": 1, "updated_at": 1.0,
              "faces": {"李四": {"encoding128": [0.2] * 128, "enrolled_at": 2.0,
                                 "source": "manual", "hits": 1, "tolerance_override": 0.4}}}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(legacy, f, ensure_ascii=False)
    got = _fc.load_roster(p)
    assert got == legacy, "旧明文名册未被兼容读取"


def test_path_whitelist():
    root = _fc._PROJECT_ROOT
    in_data = os.path.join(root, "data", "identity", "face_roster.json")
    assert _fc.is_path_within_data(in_data) is True
    # 边界：data 自身也算（极端情况）
    assert _fc.is_path_within_data(os.path.join(root, "data")) is True
    # 越界：系统临时目录 / 父目录
    assert _fc.is_path_within_data("/tmp/escape.json") is False
    assert _fc.is_path_within_data(os.path.join(os.path.dirname(root), "other", "x.json")) is False


def test_key_self_bootstrap(tmp_path):
    # 首次 save 应自举密钥文件
    p = str(tmp_path / "roster.json")
    _fc.save_roster(p, {"version": 1, "faces": {}})
    assert os.path.exists(_fc._KEY_PATH), "data/keys/face_key.key 未自举生成"
    assert os.path.getsize(_fc._KEY_PATH) > 0


def test_load_missing_returns_empty(tmp_path):
    p = str(tmp_path / "nope.json")
    assert _fc.load_roster(p) == {}
