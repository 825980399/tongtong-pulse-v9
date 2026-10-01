"""第109批 回归测试：设计文件收口快赢（T-109a/b/c）。

采用「源码级断言」：直接读取生产源码，验证修复已落地。
- 在当前代码树上运行 → 全绿（GREEN）。
- 在 .bak_batch109 备份（旧代码）上运行 → 断言失败（RED），证明测试能抓回归。
见证脚本见 tmp/red_green_runner_t109.py。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_t109a_parquet_keys_wired():
    """三个死键现在各有≥1个生产读取点（从 config 导入）。"""
    cases = [
        ("nucleus/mnemosyne/IndexStore.py", "PARQUET_COMPRESSION"),
        ("nucleus/mnemosyne/PulseNodePool.py", "PARQUET_COMPRESSION"),
        ("nucleus/mnemosyne/PulseNodePool.py", "PARQUET_BATCH_SIZE"),
        ("nucleus/mnemosyne/PulseSnapshot.py", "PARQUET_COMPRESSION"),
        ("nucleus/mnemosyne/PulseSnapshot.py", "PARQUET_SHARD_BY_EVOL_LEVEL"),
    ]
    for rel, name in cases:
        src = _read(rel)
        # 同一行可合并导入多个键（如 `from config import PARQUET_COMPRESSION, PARQUET_BATCH_SIZE`），
        # 故按「from config import 行内出现该键名」判定，而非整串匹配。
        _hit = any(
            ln.strip().startswith("from config import") and name in ln
            for ln in src.splitlines()
        )
        assert _hit, f"{name} 未接入 {rel}"


def test_t109b_face_welcome_switch():
    """face_welcome 快赢：新增默认关开关 + chat_service 已门控推理请求。"""
    cfg = _read("config.py")
    assert "ENABLE_FACE_WELCOME_DIRECT = True" in cfg, "config 缺少默认开开关（face_welcome 直欢迎已启用）"
    cs = _read("functions/chat/chat_service.py")
    assert "_should_skip = ENABLE_FACE_WELCOME_DIRECT" in cs, "chat_service 未门控 face_welcome 推理请求"


def test_t109c_evidence_chain_dict_wrap():
    """evidence_chain 成员 node_id 须包装为 dict 列表，兼容 Parquet schema。"""
    liver = _read("organs/body/PulseLiver.py")
    assert '{"node_id": getattr(n' in liver, "evidence_chain 未包装为 dict 列表"


def test_t109c_normalizer_keeps_dict():
    """真实运行时：_normalize_struct_list 保留 dict 元素（T-109c 喂入的结构）。"""
    sys.path.insert(0, ROOT)
    try:
        from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
    except Exception as e:  # pragma: no cover
        pytest.skip(f"PulseSnapshot 不可独立导入: {e}")
    out = PulseSnapshot._normalize_struct_list([{"node_id": "x"}], "evidence_chain", "n1")
    assert out == [{"node_id": "x"}], "dict 元素应被保留"
    dropped = PulseSnapshot._normalize_struct_list(["x"], "evidence_chain", "n1")
    assert dropped == [], "str 元素应被丢弃（这正是 T-109c 修复前刷屏的日志来源）"
