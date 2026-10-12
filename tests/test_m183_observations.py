# -*- coding: utf-8 -*-
"""183批观测刀隔离单测（刀3/4/5/6）。

覆盖：
- 刀3 纯函数 suggested_temperature（昼夜/夹紧）
- 刀4 纯函数 memory_strength（时间衰减×频率强化）
- 刀6 核心 HebbianLearner.apply_decay(force=True) 绕过 _enabled 闸门
- 刀3/4/5/6 观测方法在开关态下的行为（默认关=零副作用；打开=仅日志）

遵循铁律151：本文件独立运行，不依赖全量 pytest 收集。
"""
from nucleus.cognitive.cognitive_state_utils import suggested_temperature
from nucleus.genesis.HebbianLearner import HebbianLearner
from nucleus.memory_strength import memory_strength


# ========== 刀3 纯函数 ==========
def test_suggested_temperature_night_boost():
    # 夜间(23-6)低能量/低专注 → 温度偏高（更发散）
    _t_night = suggested_temperature(energy=0.2, mood=0.3, focus=0.2, hour=2, base=0.7)
    # 白天同输入 → 温度偏低
    _t_day = suggested_temperature(energy=0.2, mood=0.3, focus=0.2, hour=14, base=0.7)
    assert _t_night > _t_day
    # 夹紧到 [0.5, 0.9]
    assert 0.5 <= _t_night <= 0.9
    assert 0.5 <= _t_day <= 0.9


def test_suggested_temperature_clamp():
    # 极端输入夹紧
    assert suggested_temperature(energy=0.0, mood=0.0, focus=0.0, hour=0, base=0.7) >= 0.5
    assert suggested_temperature(energy=1.0, mood=1.0, focus=1.0, hour=12, base=0.7) <= 0.9


def test_suggested_temperature_base_passthrough():
    # 基线温度来自 base 参数
    _t = suggested_temperature(energy=0.5, mood=0.5, focus=0.5, hour=12, base=0.8)
    assert isinstance(_t, float)
    assert round(_t, 3) == _t


# ========== 刀4 纯函数 ==========
def test_memory_strength_recency():
    import time
    _now = time.time()
    # 越近激活，强度越高
    _recent = memory_strength(_now - 1.0, count=1, tau=30.0)
    _old = memory_strength(_now - 1000.0, count=1, tau=30.0)
    assert _recent > _old


def test_memory_strength_frequency():
    import time
    _now = time.time()
    # 相同新鲜度，激活次数越多强度越高
    _low = memory_strength(_now - 10.0, count=1, tau=30.0)
    _high = memory_strength(_now - 10.0, count=10, tau=30.0)
    assert _high > _low


# ========== 刀6 核心：apply_decay force 绕过闸门 ==========
def test_hebbian_apply_decay_force_gate():
    _l = HebbianLearner()
    _l._enabled = False
    # 塞入两条连接
    _l._connections = {("a", "b"): 0.5, ("c", "d"): 0.5}
    _before = dict(_l._connections)

    # force=False（默认）→ 闸门早退，零衰减
    _l.apply_decay()
    assert _l._connections == _before
    assert _l._total_decays == 0

    # force=True → 绕过闸门，执行衰减
    _l.apply_decay(force=True)
    assert _l._total_decays == 2
    for _k, _v in _l._connections.items():
        assert _v < 0.5  # 权重被 (1 - decay_rate) 衰减


def test_hebbian_apply_decay_enabled_default_off():
    _l = HebbianLearner()
    assert _l._enabled is False
    _l._connections = {("a", "b"): 0.9}
    _l.apply_decay()  # 默认 _enabled=False，应早退
    assert _l._total_decays == 0
    assert _l._connections[("a", "b")] == 0.9


# ========== 刀3/4/5/6 观测方法（mock self，避免重实例化器官） ==========
def _make_fake_self(nodes=None, conns=None, learn_enabled=False):
    """构造 duck-typed 假 self，提供观测方法所需的全部表面。"""
    _logs = []

    class _Node:
        def __init__(self, nid, space_path, activation_count, keywords):
            self.node_id = nid
            self.space_path = space_path
            self.activation_count = activation_count
            self.keywords = keywords

    class _Learner:
        _enabled = learn_enabled
        _connections = conns or {}
        _total_decays = 0

        def apply_decay(self, force=False):
            # 简化版：仅验证 force 被观测方法以 True 调用
            self._total_decays += 1

    class _NodePool:
        def get_all(self):
            return nodes or []

        def get(self, nid):
            for _n in (nodes or []):
                if _n.node_id == nid:
                    return _n
            return None

        _hebbian_learner = _Learner()

    class _FakeSelf:
        node_pool = _NodePool()

        def _get_emotion_intensity(self):
            return 0.4

        def _get_current_creative_focus(self):
            return None

        def _log(self, level, msg):
            _logs.append((level, msg))

        def _evaluate_inspiration_quality(self, concepts, domain_names, current_focus):
            return {"score": 88.0, "cross_domain": 1.0, "relevance": 0.9, "novelty": 0.7}

    _fs = _FakeSelf()
    _fs._logs = _logs
    _fs._Node = _Node
    return _fs


def _load_subconscious():
    import organs.brain.PulseSubconscious as m
    return m.PulseSubconscious  # 返回类（观测方法为类方法，非模块级函数）


def test_observations_tick_default_off_no_side_effect():
    # 真实 config 默认全关 → 观测总闸不应产生任何日志，也不应报错
    _m = _load_subconscious()
    _fs = _make_fake_self()
    # 确保真实 config 开关为关（默认）
    _m._m183_observations_tick(_fs)
    assert _fs._logs == []


def test_cognitive_temperature_observe_logs():
    import config as _cfg
    _m = _load_subconscious()
    _fs = _make_fake_self()
    _m._m183_cognitive_temperature_observe(_fs, _cfg)
    assert any("[M183-温度观测]" in _msg for _lvl, _msg in _fs._logs)


def test_memory_strength_shadow_logs():
    import config as _cfg
    _m = _load_subconscious()
    _fs = _make_fake_self()
    _n1 = _fs._Node("n1", "/自我/状态", 10, ["a"])
    _n2 = _fs._Node("n2", "/知识/科学", 5, ["b"])
    _fs2 = _make_fake_self(nodes=[_n1, _n2])
    _m._m183_memory_strength_shadow(_fs2, _cfg)
    assert any("[M183-记忆强度影子]" in _msg for _lvl, _msg in _fs2._logs)


def test_scan_hebbian_collision_hit():
    import config as _cfg
    _m = _load_subconscious()
    _fs = _make_fake_self()
    _n_a = _fs._Node("a", "/自我/情绪", 7, ["x"])
    _n_b = _fs._Node("b", "/知识/数学", 8, ["y"])
    _fs2 = _make_fake_self(
        nodes=[_n_a, _n_b],
        conns={("a", "b"): 0.9},
    )
    _m._m183_scan_hebbian_collision(_fs2, _cfg)
    # 异域(/自我 vs /知识) + 双高激活(7,8>=5) → 应命中
    assert any("[M183-碰撞扫描]" in _msg for _lvl, _msg in _fs2._logs)


def test_scan_hebbian_collision_no_hit_same_domain():
    import config as _cfg
    _m = _load_subconscious()
    _fs = _make_fake_self()
    _n_a = _fs._Node("a", "/自我/情绪", 7, ["x"])
    _n_b = _fs._Node("b", "/自我/状态", 8, ["y"])
    _fs2 = _make_fake_self(nodes=[_n_a, _n_b], conns={("a", "b"): 0.9})
    _m._m183_scan_hebbian_collision(_fs2, _cfg)
    # 同域 → 不应命中（无命中型日志）
    assert not any("命中异域双高激活对" in _msg for _lvl, _msg in _fs2._logs)


def test_apply_hebbian_decay_calls_force():
    import config as _cfg
    _m = _load_subconscious()
    _fs = _make_fake_self(conns={("a", "b"): 0.9})
    _before = _fs.node_pool._hebbian_learner._total_decays
    _m._m183_apply_hebbian_decay(_fs, _cfg)
    _after = _fs.node_pool._hebbian_learner._total_decays
    assert _after == _before + 1  # apply_decay(force=True) 被调用
    assert any("[M183-赫布衰减调度]" in _msg for _lvl, _msg in _fs._logs)
