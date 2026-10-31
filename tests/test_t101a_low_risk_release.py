"""T-101a（P0）：批准→落盘断链修复 —— 低风险可放行路径 + 全链落盘验证。

验收：
  ✅ T7 闸门有低风险放行路径（save_pending_patch 将 runtime_verified 的低风险补丁升 approved + auto_released）
  ✅ 至少 1 条补丁走完全链（提交→批准→落盘→复验）
  ✅ 落盘后磁盘探针验证 modified_code 在盘
"""
import os
import sys
import tempfile
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nucleus.reasoning.PatchManager import PatchManager
def _master_on(monkeypatch):
    """★372刀2（裁决 C-1 甲）：B 通道已并入总开关，总开关关闭即拒。

    本文件验的是「B 通道**自身**判据」，故显式打开总开关，
    让被测条件（嵌套 verified / baseline / 风险档）成为唯一变量 ——
    否则所有用例都会因总开关关闭而恒 False，测试失去判别力。
    """
    import config
    monkeypatch.setitem(config.EVOLUTION_CONFIG, "auto_apply_enabled", True)
    # ★406刀4（2026-10-31 裁决）：再关掉「diff 窄闸」。
    #   本文件验的是 B 通道**自身**判据（嵌套 verified / baseline / 风险档），
    #   而 406 窄闸只放注释/文档/测试类 diff；本文件探针是
    #   `return 'ORIGINAL_MARKER' → 'T101A_LANDED_MARKER'` 的真实逻辑改动
    #   ⇒ 窄闸在位时全链落地必然被拦，T-101a 的落地能力就测不到了。
    #   窄闸在位时"确实会拦"由 test_406_narrow_gate_blocks_logic_landing 钉住。
    monkeypatch.setattr(config, "PATCH_M406_NARROW_GATE", False, raising=False)


class _DummyBackup:
    def create_backup(self, files=None, reason=None, source=None):
        return os.path.join(tempfile.gettempdir(), "dummy_backup")


def _make_pm(tmp):
    pm = PatchManager(project_root=PROJECT_ROOT)
    pm._pending_file = os.path.join(tmp, "pending_patches.json")
    pm._history_file = os.path.join(tmp, "patch_history.json")
    pm._backup_manager = _DummyBackup()
    # 隔离「重启计数持久化」——该步骤会写 data/patches/restart_count.txt，
    # 生产环境由 WriteGuard 放行；测试进程被 WriteGuard 拒绝属预期安全行为，
    # 与「低风险放行→落盘」验证无关，故以 no-op 绕过，专测落地链路。
    pm._save_restart_counter = lambda c: True
    pm._load_restart_counter = lambda: 0
    pm._max_restart_count = 10 ** 9
    return pm


def _make_target(tmp):
    # 探针目标必须落在与项目同盘的目录（路径沙箱禁止跨盘符改写），
    # 故置于项目内 tmp/，而非系统 C: 盘临时目录。
    os.makedirs(os.path.join(PROJECT_ROOT, "tmp"), exist_ok=True)
    return os.path.join(PROJECT_ROOT, "tmp", f"t101a_target_{uuid.uuid4().hex}.py")


def test_low_risk_release_promotes_and_lands_on_disk(monkeypatch):
    _master_on(monkeypatch)   # ★372刀2：B 通道已并入总开关
    tmp = tempfile.mkdtemp(prefix="t101a_")
    target = _make_target(tmp)
    original = "def probe_func():\n    return 'ORIGINAL_MARKER'\n"
    with open(target, "w", encoding="utf-8") as f:
        f.write(original)
    modified = "def probe_func():\n    return 'T101A_LANDED_MARKER'\n"

    patch = {
        "id": "t101a_probe",
        "file": target,
        "method": "probe_func",
        "original_code": original,
        "modified_code": modified,
        "risk_level": "\u4f4e",
        # ★第105批 T-105a：放行判据改读**嵌套** runtime_verify_result.verified
        # （顶层 runtime_verified 为污染字段，不可作依据）+ baseline_errors>0。
        "runtime_verify_result": {"verified": True, "baseline": 3},
        "baseline_errors": 3,
        "source": "local_rule", "issue_type": "periodic_task_no_reentry",   # ★372刀2：llm 排除；★372刀1 起未登记族升中，故取已登记低危族
        "status": "verified",   # ★372刀2：置 verified 以避开 A2 低风险自动审批抢跑（B 通道正为这类补丁而设）
        # ★372刀2：用 status=verified 隔离 A2（见下）；trust 保持 90 ——
        #   落地门 auto_apply_min_trust=60，压到门槛下会卡在"信任分数不足"。
        "trust_score": 90,
        "confidence": "high",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
    }

    pm = _make_pm(tmp)
    # ① 提交 → 低风险放行路径应将其升为 approved + auto_released
    pm.save_pending_patch(patch)
    assert patch.get("status") == "approved", patch.get("status")
    assert patch.get("auto_released") is True, "release path did not mark auto_released"

    # ② 批准 → 落盘：apply_all_pending(only_approved=True) 应用该补丁
    res = pm.apply_all_pending(only_approved=True)
    assert res.get("applied", 0) >= 1, res

    # ③ 复验：磁盘探针确认 modified_code 已落盘、original 已替换
    #   （apply_all_pending 改写的是 _pending_file 加载出的副本并落盘到目标文件，
    #    故以磁盘探针为最终证据，而非输入 patch 变量的 in-memory 字段）
    with open(target, "r", encoding="utf-8") as f:
        content = f.read()
    assert "T101A_LANDED_MARKER" in content, "disk probe: modified_code NOT landed"
    assert "ORIGINAL_MARKER" not in content, "disk probe: original_code still present"


def test_406_narrow_gate_blocks_logic_landing(monkeypatch):
    """★406刀4：同一份**逻辑改动**补丁，在窄闸在位时不得被免签放行、磁盘不得被改写。

    与上一条用例是同一份夹具、同一份补丁 —— 唯一差别是窄闸开关。
    这样写是为了让"本刀收窄了什么"与"T-101a 原本能做什么"并排可读。
    """
    import config
    from nucleus.evolution.patch_risk_gate import gate_allows, judge

    monkeypatch.setitem(config.EVOLUTION_CONFIG, "auto_apply_enabled", True)
    monkeypatch.setattr(config, "PATCH_M406_NARROW_GATE", True, raising=False)

    tmp = tempfile.mkdtemp(prefix="t101a_gate_")
    target = _make_target(tmp)
    original = "def probe_func():\n    return 'ORIGINAL_MARKER'\n"
    with open(target, "w", encoding="utf-8") as f:
        f.write(original)

    patch = {
        "id": "t101a_gate_probe",
        "file": target,
        "method": "probe_func",
        "original_code": original,
        "modified_code": "def probe_func():\n    return 'T101A_LANDED_MARKER'\n",
        "risk_level": "\u4f4e",
        "runtime_verify_result": {"verified": True, "baseline": 3},
        "baseline_errors": 3,
        "source": "local_rule",
        "issue_type": "periodic_task_no_reentry",
        "status": "verified",
        "trust_score": 90,
        "confidence": "high",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
    }

    # 判据层：diff 是逻辑改动 ⇒ medium，永不 eligible
    _j = judge(patch)
    assert _j["diff_kind"] == "logic", _j
    assert _j["eligible"] is False, _j
    _ok, _why = gate_allows(patch)
    assert _ok is False and _why == "medium:logic", (_ok, _why)

    # 链路层：即便 T-101a 的其余判据全过，也不得升 approved / 落盘
    pm = _make_pm(tmp)
    pm.save_pending_patch(patch)
    assert patch.get("status") != "approved", patch.get("status")
    assert not patch.get("auto_released")
    with open(target, encoding="utf-8") as f:
        assert f.read() == original, "★窄闸在位时磁盘不得被改写"


def test_high_risk_or_unverified_not_auto_released(monkeypatch):
    """回归：高风险 / 未通过复现的补丁不应被低风险路径放行。

    ★372刀2：总开关显式打开、source 改为非 llm —— 让"未验证/高风险"
    成为本条用例**唯一**的否决原因（否则会被新加的两道闸一并挡住，
    这条负控就退化成恒真）。
    """
    _master_on(monkeypatch)
    tmp = tempfile.mkdtemp(prefix="t101a_neg_")
    target = _make_target(tmp)
    with open(target, "w", encoding="utf-8") as f:
        f.write("def f():\n    return 1\n")
    pm = _make_pm(tmp)

    # 未通过复现（runtime_verified 缺失）
    p1 = {
        "id": "neg1", "file": target, "method": "f",
        "original_code": "def f():\n    return 1\n",
        "modified_code": "def f():\n    return 2\n",
        "risk_level": "\u4f4e", "source": "local_rule", "issue_type": "periodic_task_no_reentry", "status": "verified",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
    }
    pm.save_pending_patch(p1)
    assert p1.get("status") != "approved", "unverified low-risk patch must NOT auto-release"

    # 高风险（即使嵌套 verified=True 也不得放行——风险闸门独立于验证闸门）
    p2 = {
        "id": "neg2", "file": target, "method": "f",
        "original_code": "def f():\n    return 1\n",
        "modified_code": "def f():\n    return 2\n",
        "risk_level": "\u9ad8", "runtime_verify_result": {"verified": True, "baseline": 3},
        "baseline_errors": 3, "source": "local_rule", "issue_type": "periodic_task_no_reentry", "status": "verified",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
    }
    pm.save_pending_patch(p2)
    assert p2.get("status") != "approved", "high-risk patch must NOT auto-release"
