"""T-100a 回归测试：待审批队列不得包含"假成功"补丁。

假成功定义（T-99d 新验证器口径 + m96 应用探针）：
  - baseline_errors == 0 却被标记为 verified=True（T-99d 旧口径导致）
  - 且仍处于待审批队列（status in pending/approved/runtime_verified）
  - 且补丁从未真正应用到源码（original_code 仍在盘、modified_code 不在盘）

本测试应先红后绿：
  - 修复前：队列含 23 条 B 级假成功 → 断言失败（RED）
  - 修复后：假成功已清出队列 → 断言通过（GREEN）
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING = os.path.join(ROOT, "data", "patches", "pending_patches.json")


def _load_items():
    with open(PENDING, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("patches") or data.get("items") or []


def _is_false_success(it):
    rv = it.get("runtime_verify_result") or {}
    if not isinstance(rv, dict):
        rv = {}
    baseline = it.get("baseline_errors") or 0
    verified = rv.get("verified")
    status = it.get("status")
    pending_status = status in ("runtime_verified", "approved", "pending")
    # 新口径：baseline=0 却标记 verified，且仍在待审批队列 => 假成功
    return baseline == 0 and verified is True and pending_status


def test_pending_queue_has_no_false_success():
    items = _load_items()
    false_success = [it for it in items if _is_false_success(it)]
    assert not false_success, (
        f"发现 {len(false_success)} 条假成功补丁仍在待审批队列: "
        f"{[it.get('id') for it in false_success]}"
    )


def test_pending_queue_item_has_applied_marker():
    """队列中每条待审批项必须带 applied 标记（防止无应用探针的模糊项）。"""
    items = _load_items()
    for it in items:
        assert "applied" in it, f"补丁 {it.get('id')} 缺少 applied 字段"
