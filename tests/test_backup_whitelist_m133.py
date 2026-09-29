# -*- coding: utf-8 -*-
"""相关任务 备份树扫描白名单单元测试（制度化 SCAN_EXCLUDE_*）。

验收判据：
  - .bak* 段（.bak_batchN / .bak_tmp 等）识别为备份目录；
  - backups/ data/code_backups/ tmp/ 三族均被识别为备份/临时树；
  - 普通源码与「文件名含 .bak 后缀」的正常文件不被误伤。
"""
import sys

sys.path.insert(0, ".")

from nucleus.self_inspector import _issue_file_in_backup_dir as _in_backup


def test_bak_segment_detected():
    assert _in_backup(".bak_batch133/nucleus/const.py") is True
    assert _in_backup(".bak_tmp/foo.py") is True


def test_backup_tree_detected():
    assert _in_backup("backups/2026/snapshot.py") is True
    assert _in_backup("data/code_backups/old/main.py") is True


def test_tmp_tree_detected():
    assert _in_backup("tmp/scratch.py") is True


def test_normal_source_not_flagged():
    assert _in_backup("organs/brain/PulseInnerWorld.py") is False
    assert _in_backup("nucleus/self_inspector.py") is False


def test_bak_suffix_filename_not_flagged():
    # 文件名含 .bak 后缀的正常文件，不应被误判为备份目录
    assert _in_backup("src/main.py.bak") is False
