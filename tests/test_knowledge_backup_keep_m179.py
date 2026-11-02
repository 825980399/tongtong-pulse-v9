# -*- coding: utf-8 -*-
"""179批 刀3 门控单测：KNOWLEDGE_BACKUP_KEEP 3->2 落地（隔离单跑）。

验证：
  - config.KNOWLEDGE_BACKUP_KEEP 与当前定值一致（见下「★424 对齐」）
  - ENABLE_KNOWLEDGE_BACKUP_ROTATION 保持开启（关闭才回落旧值 5，零回归）
  - 消费点 nucleus/mnemosyne/PulseSnapshot.py:_m41_backup_keep 读取该值
不依赖框架运行（曈曈停机态亦可跑）。

★424 对齐（本文件唯一一次规格断言改钉，铁律 68）：
  179A 落地值 2 已被 **384批刀3（commit 4e353197，2026-10-28）改为 1**
  （冷备压缩 1433MB→71.3MB，热档不动；备份份数随之由 2 降到 1）。
  本文件最后修改是 202 批（829c4bbc，2026-10-13），未跟上该变更，
  于是从 10-28 起一直假红、且未登记 known_fail —— 424 批回归时发现。
  这里把断言从 2 改钉为 1，是**跟随规格**而非放宽：若将来再调 KEEP，
  本条应同步改钉，禁止改成 `>= 1` 之类无量纲断言。
  回滚点：`git checkout 4e353197^ -- tests/test_knowledge_backup_keep_m179.py`
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config


def test_keep_value_matches_current_spec():
    # ★424：2 → 1（384批刀3 定值），详见文件头「★424 对齐」
    assert config.KNOWLEDGE_BACKUP_KEEP == 1


def test_rotation_switch_still_enabled():
    # 开关关闭才会回落旧值 5；179A 仅改 KEEP，未碰开关
    assert config.ENABLE_KNOWLEDGE_BACKUP_ROTATION is True


def test_consumer_reads_keep_value():
    # 消费点 _m41_backup_keep 读 getattr(config, "KNOWLEDGE_BACKUP_KEEP", 3)
    try:
        from nucleus.mnemosyne.PulseSnapshot import _m41_backup_keep
    except Exception:
        pytest.skip("PulseSnapshot 重导入在本环境不可达，跳过消费函数直测")
    assert _m41_backup_keep() == 1  # ★424：跟随 384批刀3 的 KEEP=1
