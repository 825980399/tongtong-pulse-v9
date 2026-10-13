# -*- coding: utf-8 -*-
"""
test_schedule_convergence_m203.py —— 第203批 刀2/刀3 隔离单测

刀2：Heart._scheduled_tasks 并入 TimeCore 配置块（双引擎收敛）
    TIMECORE_SCHEDULE_CONFIG 应包含 Heart 全部 22 个调度任务（含已存在的
    knowledge_purge / snapshot_auto_save），与 PulseHeart.start() 注册定义一致；
    复用 PulseHeart 既有的「主动调度开启即从 _scheduled_tasks 弹出」机制（:841-850）。

刀3：B类家务 Top3（runtime_metrics / metrics_collector / snapshot_rotate）
    作为灰度新路径加入 TIMECORE_SCHEDULE_CONFIG（消费侧接线待观察窗）。

验收要点：
    1. TIMECORE_SCHEDULE_CONFIG 含全部 22 个 Heart 任务，interval/event_type 与注册一致。
    2. 刀3 B类Top3 三项已加入配置块。
    3. ENABLE_TIMECORE_ACTIVE_SCHEDULE 为 True（接管生效）。
"""
import config


# (task_id, interval, event_type) —— 与 PulseHeart.start() 注册定义 1:1 对齐
HEART_TASKS = {
    "knowledge_purge": (3600, "kidney.purge.check"),
    "snapshot_auto_save": (1800, "snapshot.auto_save"),
    "immune_scan": (600, "white_cell.scan"),
    "personality_verify": (3600, "personality.verify"),
    "health_check": (1800, "health.check"),
    "metrics_collect": (300, "metrics.collect"),
    "thymus_train": (3600, "thymus.train"),
    "energy_assess": (300, "energy.assess"),
    "spinal_inspect": (600, "spinal.inspect"),
    "proprioception_request": (300, "proprioception.request"),
    "touch_snapshot": (300, "touch.snapshot"),
    "narrative_reflect": (3600, "narrative.reflect"),
    "growth_assess": (3600, "growth.assess"),
    "nurture_advance": (21600, "nurture.advance"),
    "device_refresh": (600, "device.refresh"),
    "personality_boundary_check": (3600, "personality.boundary_check"),
    "skin_review_patch": (1800, "skin.review_patch"),
    "bone_marrow_generate": (21600, "bone_marrow.generate"),
    "evolution_save_blueprint": (21600, "evolution.save_blueprint"),
    "dna_repair_fix": (3600, "dna_repair.fix"),
    "evolution_mutate": (21600, "evolution.mutate"),
    "reproduction_ethics_check": (86400, "reproduction.ethics_check"),
}

B_CLASS_TOP3 = {
    "runtime_metrics": (60, "runtime.metrics.collect"),
    "metrics_collector": (300, "metrics.collector.tick"),
    "snapshot_rotate": (1800, "snapshot.rotate.tick"),
}


def test_active_schedule_enabled():
    assert getattr(config, "ENABLE_TIMECORE_ACTIVE_SCHEDULE", False) is True


def test_heart_tasks_converged_to_timecore():
    cfg = getattr(config, "TIMECORE_SCHEDULE_CONFIG", {})
    for tid, (interval, event_type) in HEART_TASKS.items():
        assert tid in cfg, f"TIMECORE_SCHEDULE_CONFIG 缺 Heart 任务 {tid}"
        assert cfg[tid].get("interval") == interval, f"{tid} interval 不符"
        assert cfg[tid].get("event_type") == event_type, f"{tid} event_type 不符"


def test_b_class_top3_in_config():
    cfg = getattr(config, "TIMECORE_SCHEDULE_CONFIG", {})
    for tid, (interval, event_type) in B_CLASS_TOP3.items():
        assert tid in cfg, f"刀3 B类Top3 缺 {tid}"
        assert cfg[tid].get("interval") == interval
        assert cfg[tid].get("event_type") == event_type


def test_no_duplicate_keys():
    cfg = getattr(config, "TIMECORE_SCHEDULE_CONFIG", {})
    assert len(cfg) == len(set(cfg)), "TIMECORE_SCHEDULE_CONFIG 存在重复键"
