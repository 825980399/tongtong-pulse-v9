"""188·刀2 隔离单测：经验库盘存一致性修复（指纹去重过计修复 + 97条归因诊断）。

验证点：
- 指纹去重命中（同 content 二次写入）→ 合并、不新增条目，total_recorded 不重复自增；
- 真实新增条目 → total_recorded 与 experiences 长度同步 +1；
- get_inventory_consistency_report() 结构正确、consistent=True，并明确'97条'归因
  （主经验池与推理经验库为独立数据源）。

隔离：注入临时 base_dir（非 data/，避开写盘守卫），关闭自动清洗线程/开关，避免后台噪声。
不改动任何既有测试集合。
"""
import sys
import tempfile

import pytest

sys.path.insert(0, ".")

import nucleus.mnemosyne.experience_pool as ep


@pytest.fixture
def pool():
    # 隔离：关闭自动清洗（含后台线程），避免污染判定/线程噪声影响计数
    ep.ExperiencePool._auto_clean_enabled = lambda self: False
    ep.ExperiencePool._start_auto_clean_thread = lambda self: None
    tmp = tempfile.mkdtemp(prefix="exp_pool_test_")
    inst = ep.ExperiencePool(base_dir=tmp)
    yield inst
    # 清理
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_fingerprint_dedup_does_not_overcount(pool):
    c = "unique experience content alpha beta"
    id1 = pool.record_experience(content=c)
    assert id1 != ""
    assert pool._total_recorded == 1
    assert len(pool._experiences) == 1

    # 同 content 二次写入 → 指纹去重命中合并，不新增条目
    id2 = pool.record_experience(content=c)
    assert id2 == id1
    # ★修复点：total_recorded 不应因合并而重复自增
    assert pool._total_recorded == 1
    assert len(pool._experiences) == 1


def test_real_append_increments_both_counters(pool):
    id1 = pool.record_experience(content="first distinct experience content x")
    assert id1 != ""
    assert pool._total_recorded == 1 and len(pool._experiences) == 1
    id2 = pool.record_experience(content="second distinct experience content y")
    assert id2 != id1
    assert pool._total_recorded == 2 and len(pool._experiences) == 2


def test_consistency_report(pool):
    pool.record_experience(content="alpha experience content long enough")
    pool.record_experience(content="beta experience content long enough")
    rep = pool.get_inventory_consistency_report()
    assert rep["experiences_len"] == 2
    assert rep["total_recorded"] == 2
    assert rep["consistent"] is True
    assert "97" in rep["note"]
    assert "推理经验库" in rep["note"]
    # 重复写入同一内容 → 仍一致（不破计数）
    pool.record_experience(content="alpha experience content long enough")
    rep2 = pool.get_inventory_consistency_report()
    assert rep2["total_recorded"] == 2
    assert rep2["experiences_len"] == 2
    assert rep2["consistent"] is True
