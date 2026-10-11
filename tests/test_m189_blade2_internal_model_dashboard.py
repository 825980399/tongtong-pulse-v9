# -*- coding: utf-8 -*-
"""189批刀2 隔离单测：内在模型进度仪表盘。

验证点：
  - 四层 L1-L4 在册状态一页可见；
  - L3 external_dependency_observe 仅观测不实装（real_implementation=False）；
  - 三指标趋势含外部依赖度 + 说明；
  - 留存率动态计算且标注「非旧口径 2.4%」；
  - L3 缺口声明明确（declared + refs）；
  - 只读 / 未翻生产开关 / 未新建组件（meta）；
  - MD 渲染含 L3 缺口声明。
"""
from __future__ import annotations

from tools.export_internal_model_dashboard import (
    compute_dashboard,
    export_internal_model_dashboard,
    export_internal_model_dashboard_md,
    render_dashboard_md,
)


def test_dashboard_layers_present():
    _p = compute_dashboard()
    assert set(_p["layers"].keys()) == {"L1", "L2", "L3", "L4"}
    for _lv in ("L1", "L2", "L3", "L4"):
        assert _p["layers"][_lv], _lv


def test_dashboard_component_status_and_module():
    _p = compute_dashboard()
    _all = []
    for _lv in ("L1", "L2", "L3", "L4"):
        for _name, _c in _p["layers"][_lv].items():
            assert _c.get("module"), (_lv, _name)
            assert _c.get("status"), (_lv, _name)
            assert _c.get("present") in (True, False)
            _all.append((_lv, _name, _c))
    assert len(_all) >= 6  # L1*2 + L2*1 + L3*1 + L4*2


def test_dashboard_l3_observe_only():
    _p = compute_dashboard()
    _l3 = _p["layers"]["L3"]["external_dependency_observe"]
    assert _l3["observe_only"] is True
    assert _l3["real_implementation"] is False
    assert _l3["status"]


def test_dashboard_three_metrics():
    _p = compute_dashboard()
    _tm = _p["three_metrics"]
    assert "external_dependency" in _tm
    assert 0.0 <= float(_tm["external_dependency"]) <= 1.0
    assert "note" in _tm and _tm["note"]


def test_dashboard_retention_not_old_24():
    _p = compute_dashboard()
    _rr = _p["retention_rate"]
    assert isinstance(_rr["value"], float)
    assert _rr["materialized_pairs"] >= 0
    assert _rr["eligible_calls"] >= 0
    assert "2.4" in _rr["note"]
    # 旧口径 2.4% 不应被硬编码为返回值
    assert _rr["value"] != 0.024


def test_dashboard_l3_gap_declared():
    _p = compute_dashboard()
    _gap = _p["l3_gap"]
    assert _gap["declared"] is True
    assert _gap["refs"]


def test_dashboard_meta_readonly():
    _p = compute_dashboard()
    assert _p["meta"]["read_only"] is True
    assert _p["meta"]["production_switch_flipped"] is False
    assert _p["meta"]["no_new_components"] is True


def test_dashboard_json_roundtrip():
    _s = export_internal_model_dashboard(as_json=True)
    import json
    _o = json.loads(_s)
    assert _o["version"] == "v1"
    assert "layers" in _o


def test_dashboard_md_contains_l3_gap():
    _md = export_internal_model_dashboard_md()
    assert "L3 缺口声明" in _md
    assert "内在模型进度仪表盘" in _md


def test_render_dashboard_md_from_payload():
    _p = compute_dashboard()
    _md = render_dashboard_md(_p)
    assert "L1" in _md and "L4" in _md
