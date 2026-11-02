# -*- coding: utf-8 -*-
"""426 刀2：effect_verified 回填工具（有证据才填 / 没证据标待验证 / 幂等）。

basetemp：`--basetemp=tmp/m426_bt_d2`（本批每文件独立 basetemp，铁律 58）。
所有用例都在 tmp 副本上跑 —— 绝不碰生产 `data/patches/patch_history.json`（铁律 38）。
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import m426_backfill_effect_verified as BF

_BAK = os.path.join(_ROOT, ".bak_batch426", "data__patches__patch_history.json")


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _stage(tmp_path):
    """把改前备份铺成 <tmp>/data/patches/patch_history.json 的隔离根。"""
    _d = tmp_path / "data" / "patches"
    _d.mkdir(parents=True)
    _dst = _d / "patch_history.json"
    shutil.copy2(_BAK, _dst)
    return str(tmp_path), str(_dst)


@pytest.mark.skipif(not os.path.exists(_BAK), reason="缺 .bak_batch426 基线备份")
def test_10_dry_run_writes_nothing(tmp_path):
    _root, _hp = _stage(tmp_path)
    _sha0 = _sha(_hp)
    _rc = BF.main(["--root", _root])
    assert _rc == 0
    assert _sha(_hp) == _sha0, "干跑不许改文件"

    _h = json.loads(Path(_hp).read_text(encoding="utf-8"))
    _tgt = [p for p in _h if p.get("applied") is True and p.get("effect_verified") is None]
    assert len(_tgt) == 36, len(_tgt)


@pytest.mark.skipif(not os.path.exists(_BAK), reason="缺 .bak_batch426 基线备份")
def test_20_write_fills_29_true_and_leaves_7_pending(tmp_path):
    _root, _hp = _stage(tmp_path)
    assert BF.main(["--root", _root, "--write"]) == 0
    _h = json.loads(Path(_hp).read_text(encoding="utf-8"))

    _filled = [p for p in _h if p.get("effect_verified_batch") == "426"
               and p.get("effect_verified") is True]
    _pending = [p for p in _h if p.get("effect_verified_batch") == "426"
                and p.get("effect_verified") is None]
    assert len(_filled) == 29, len(_filled)
    assert len(_pending) == 7, len(_pending)

    # 填 True 的每一条都必须真的有判别力证据：baseline>=1 且 after==0
    for _p in _filled:
        assert _p.get("reprobe_verdict") == "true_pass", _p.get("id")
        assert int(_p.get("reprobe_baseline_hits")) >= 1, _p.get("id")
        assert int(_p.get("reprobe_after_hits")) == 0, _p.get("id")
        assert _p.get("effect_verified_source") == "reprobe_verdict"

    # 待验证的每一条都必须写清原因，且不能被填成 False（partial 不是"没生效"）
    for _p in _pending:
        assert _p.get("reprobe_verdict") == "partial_fix", _p.get("id")
        assert _p.get("effect_verify_pending_reason"), _p.get("id")

    # 只有 applied=True 的才该被碰；applied=False 的 11 条一律不许出现批次标记
    for _p in _h:
        if _p.get("applied") is not True:
            assert _p.get("effect_verified_batch") != "426", _p.get("id")

    # 行尾守恒：改前后都必须是纯 CRLF
    _raw = Path(_hp).read_bytes()
    assert _raw.count(b"\r\n") > 0
    assert _raw.count(b"\n") - _raw.count(b"\r\n") == 0


@pytest.mark.skipif(not os.path.exists(_BAK), reason="缺 .bak_batch426 基线备份")
def test_30_second_run_is_noop(tmp_path):
    _root, _hp = _stage(tmp_path)
    assert BF.main(["--root", _root, "--write"]) == 0
    _sha1 = _sha(_hp)
    assert BF.main(["--root", _root, "--write"]) == 0  # targets=0，直接返回不写盘
    assert _sha(_hp) == _sha1, "第二次运行必须幂等（不得重写文件）"

    _h = json.loads(Path(_hp).read_text(encoding="utf-8"))
    assert sum(1 for p in _h if p.get("effect_verified_batch") == "426") == 36


def test_40_zero_baseline_true_pass_is_not_filled():
    """判别力门槛：基线 0 处命中时，"修复后 0 处"证明不了任何事 ⇒ 不许填 True。"""
    _kind, _why = BF.classify({
        "reprobe_verdict": "true_pass", "reprobe_baseline_hits": 0, "reprobe_after_hits": 0})
    assert _kind == "pending", _why

    _kind2, _why2 = BF.classify({
        "reprobe_verdict": "true_pass", "reprobe_baseline_hits": 1, "reprobe_after_hits": 0})
    assert _kind2 == "fill_true", _why2

    _kind3, _ = BF.classify({"reprobe_verdict": None})
    assert _kind3 == "pending"
