"""188·刀4 隔离单测：训练对沉淀物化（call_recorder → JSONL 影子通道）。

验证点：
- build_training_pair：非 dict / 非 success / 空 prompt / 空 response 均返回 None；
  成功且含输入输出 → 返回 {input,output,...}（已 strip）；
- convert_calls_to_training_pairs：批量把 calls JSONL 转可训数据落盘，返回转换条数；
- LLMCallRecorder.record()：注入临时目录后，成功调用对被物化为 training_pairs_*.jsonl，
  stats() 暴露 materialized/materialized_failed；record 返回值不变（仍是 m40-<hex> 串）；
  失败调用对不物化（build_training_pair 返回 None）；
- 零行为变化：既有 calls 落盘不受影响（written 照常累加）。

隔离：所有 IO 落在注入的临时目录，不触碰生产 data/llm_traces；不改动任何既有测试集合。
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, ".")

import nucleus.llm.call_recorder as cr


# ---------------------------------------------------------------------------
# build_training_pair
# ---------------------------------------------------------------------------
def test_build_pair_rejects_nondict():
    assert cr.build_training_pair("not a dict") is None
    assert cr.build_training_pair(None) is None


def test_build_pair_rejects_non_success():
    rec = {"status": "error", "prompt": "p", "response": "r"}
    assert cr.build_training_pair(rec) is None
    rec2 = {"prompt": "p", "response": "r"}  # 缺 status 默认非 success
    assert cr.build_training_pair(rec2) is None


def test_build_pair_rejects_empty_io():
    ok = {"status": "success", "prompt": "  ", "response": "r"}
    assert cr.build_training_pair(ok) is None
    ok2 = {"status": "success", "prompt": "p", "response": ""}
    assert cr.build_training_pair(ok2) is None


def test_build_pair_ok_strips_and_copies_fields():
    rec = {
        "status": "success",
        "trace_id": "m40-abc123",
        "ts": 1234.5,
        "origin": "user_query",
        "model": "gpt-x",
        "prompt": "  你好世界  ",
        "response": "  你好  ",
        "prompt_version": "v1",
        "channel": "web",
    }
    pair = cr.build_training_pair(rec)
    assert pair is not None
    assert pair["input"] == "你好世界"
    assert pair["output"] == "你好"
    assert pair["trace_id"] == "m40-abc123"
    assert pair["ts"] == 1234.5
    assert pair["model"] == "gpt-x"
    assert pair["prompt_version"] == "v1"
    assert pair["channel"] == "web"
    # 不携带 status/error 等留盘内部字段
    assert "status" not in pair
    assert "error" not in pair


# ---------------------------------------------------------------------------
# convert_calls_to_training_pairs
# ---------------------------------------------------------------------------
def test_convert_batch(tmp_path):
    src = tmp_path / "calls_20261011.jsonl"
    out = tmp_path / "pairs" / "training_pairs_20261011.jsonl"
    lines = [
        json.dumps({"status": "success", "prompt": "如何做 A？", "response": "步骤1"}),
        json.dumps({"status": "success", "prompt": "如何做 B？", "response": "步骤2"}),
        json.dumps({"status": "error", "prompt": "坏调用", "response": "失败"}),
        json.dumps({"status": "success", "prompt": "空响应", "response": "   "}),
        "not-json",
    ]
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    n = cr.convert_calls_to_training_pairs(str(src), str(out))
    assert n == 2  # 仅 2 条 success 且含非空输入输出
    assert out.exists()
    parsed = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(parsed) == 2
    assert parsed[0]["input"] == "如何做 A？"
    assert parsed[1]["output"] == "步骤2"


# ---------------------------------------------------------------------------
# record() 影子物化
# ---------------------------------------------------------------------------
@pytest.fixture
def rec():
    d = tempfile.mkdtemp(prefix="m188_blade4_")
    inst = cr.LLMCallRecorder(base_dir=d)
    inst._written = 0  # 隔离计数起点
    yield inst
    # 清理临时目录
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_record_materializes_success(rec):
    tid = rec.record(origin="user_query", prompt="解释一下闭包",
                     response="闭包是……", model="gpt-x")
    # 返回值不变：仍是 m40-<hex12> 串
    assert isinstance(tid, str) and tid.startswith("m40-")
    # 物化计数 +1
    s = rec.stats()
    assert s["materialized"] == 1
    assert s["materialized_failed"] == 0
    # training_pairs_*.jsonl 落盘且含 1 条
    base = rec.base_dir()
    tp_files = [f for f in os.listdir(base) if f.startswith("training_pairs_") and f.endswith(".jsonl")]
    assert len(tp_files) == 1
    with open(os.path.join(base, tp_files[0]), encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh.read().splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["input"] == "解释一下闭包"
    assert rows[0]["output"] == "闭包是……"
    # 零行为变化：calls 照常留盘
    assert s["written"] == 1


def test_record_does_not_materialize_failure(rec):
    tid = rec.record(origin="user_query", prompt="会失败的调用",
                     response="报错信息", status="error")
    assert isinstance(tid, str) and tid.startswith("m40-")
    # 失败调用对不物化
    s = rec.stats()
    assert s["materialized"] == 0
    # 但 calls 仍落盘（既有行为不变）
    assert s["written"] == 1
    tp_files = [f for f in os.listdir(rec.base_dir())
                if f.startswith("training_pairs_") and f.endswith(".jsonl")]
    assert tp_files == []


def test_record_stats_shape(rec):
    rec.record(origin="user_query", prompt="Q1", response="A1")
    s = rec.stats()
    assert "materialized" in s
    assert "materialized_failed" in s
    assert isinstance(s["materialized"], int)
