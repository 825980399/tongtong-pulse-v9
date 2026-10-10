"""T-101a（P0）：批准→落盘断链修复 —— 低风险可放行路径 + 全链落盘验证。

验收：
  ✅ T7 闸门有低风险放行路径（save_pending_patch 将 runtime_verified 的低风险补丁升 approved + auto_released）
  ✅ 至少 1 条补丁走完全链（提交→批准→落盘→复验）
  ✅ 落盘后磁盘探针验证 modified_code 在盘
"""
import io
import os
import sys
import tempfile
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nucleus.reasoning.PatchManager import PatchManager


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
    return os.path.join(PROJECT_ROOT, "tmp", "t101a_target_{}.py".format(uuid.uuid4().hex))


def test_low_risk_release_promotes_and_lands_on_disk():
    tmp = tempfile.mkdtemp(prefix="t101a_")
    target = _make_target(tmp)
    original = "def probe_func():\n    return 'ORIGINAL_MARKER'\n"
    with io.open(target, "w", encoding="utf-8") as f:
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
        "source": "llm",
        "status": "pending",
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
    with io.open(target, "r", encoding="utf-8") as f:
        content = f.read()
    assert "T101A_LANDED_MARKER" in content, "disk probe: modified_code NOT landed"
    assert "ORIGINAL_MARKER" not in content, "disk probe: original_code still present"


def test_high_risk_or_unverified_not_auto_released():
    """回归：高风险 / 未通过复现的补丁不应被低风险路径放行。"""
    tmp = tempfile.mkdtemp(prefix="t101a_neg_")
    target = _make_target(tmp)
    with io.open(target, "w", encoding="utf-8") as f:
        f.write("def f():\n    return 1\n")
    pm = _make_pm(tmp)

    # 未通过复现（runtime_verified 缺失）
    p1 = {
        "id": "neg1", "file": target, "method": "f",
        "original_code": "def f():\n    return 1\n",
        "modified_code": "def f():\n    return 2\n",
        "risk_level": "\u4f4e", "source": "llm", "status": "pending",
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
        "baseline_errors": 3, "source": "llm", "status": "pending",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
    }
    pm.save_pending_patch(p2)
    assert p2.get("status") != "approved", "high-risk patch must NOT auto-release"
