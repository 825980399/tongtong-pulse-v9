# -*- coding: utf-8 -*-
"""179批 刀3 门控单测：KNOWLEDGE_BACKUP_KEEP 3->2 落地（隔离单跑）。

验证：
  - config.KNOWLEDGE_BACKUP_KEEP == 2（179A 刀3 落地值）
  - ENABLE_KNOWLEDGE_BACKUP_ROTATION 保持开启（关闭才回落旧值 5，零回归）
  - 消费点 nucleus/mnemosyne/PulseSnapshot.py:_m41_backup_keep 读取该值
不依赖框架运行（曈曈停机态亦可跑）。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402


def test_keep_value_is_two():
    assert config.KNOWLEDGE_BACKUP_KEEP == 2


def test_rotation_switch_still_enabled():
    # 开关关闭才会回落旧值 5；179A 仅改 KEEP，未碰开关
    assert config.ENABLE_KNOWLEDGE_BACKUP_ROTATION is True


def test_consumer_reads_keep_value():
    # 消费点 _m41_backup_keep 读 getattr(config, "KNOWLEDGE_BACKUP_KEEP", 3)
    try:
        from nucleus.mnemosyne.PulseSnapshot import _m41_backup_keep
    except Exception:
        pytest.skip("PulseSnapshot 重导入在本环境不可达，跳过消费函数直测")
    assert _m41_backup_keep() == 2
