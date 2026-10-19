"""302批 刀4：代码问题趋势门禁（只读·供 CI 调用）。

作用：把「代码问题（high/medium）」变成**可回归的门禁指标**——
每轮 CI 跑一次，与基线比较；新增 high 或 medium 超基线即 FAIL，
避免问题再次悄悄积累到 801 个才发现。

用法：
  python tools/check_code_issue_trend_302.py [--json] [--max-high 0] [--max-medium 60]

退出码：
  0 = 在阈值内（PASS）
  1 = 超阈值（FAIL：high > max-high 或 medium > max-medium）
  2 = 运行错误

说明：
- ★第306批 刀3：SKIP **去掉 dead_code** —— 302 建此门禁时把它跳过，于是
  「27 → 541」这种量级的变化不会触发任何 FAIL（尺子跟不上判据）。现在
  dead_code 单列一条阈值（`--max-dead-code`，默认 40 ≈ 现值 27×1.5）。
- 仍跳过 unused_imports / config_audit：二者与进化链路无关且量极大。
- ★判据版本戳：`CRITERIA_VERSION` 变了（检测器增删、severity 口径、dead_code
  存活判据升级）就**必须按新版重算阈值**，不得跨版本比基线——`rescan_high_risk_levels`
  只升不降，存量会按新判据整体上移，旧基线在新版本下没有可比性。
- ★第308批 刀2：待复核清单（dead_code 中间档）已**降级为统计口径**——不再作为
  issue 存在于扫描结果中，数量改由 `get_dead_code_review_stats()` 提供；门禁照旧
  **不判死**（只展示），阈值语义不变。
- 纯只读：不写 data/、不写 logs/、不改任何文件。
"""
from __future__ import annotations

import argparse
import json
import sys

DEFAULT_MAX_HIGH = 0
# ★第303批 刀3：起步阈值由 60 收紧为 48（现值 44）。
#   原 60 宽于现值 ⇒ 头几轮形同不拦（烛微 302 建议）；48 留出 4 条缓冲，
#   既拦住回涨又不至于把正常波动判 FAIL。
DEFAULT_MAX_MEDIUM = 48
# ★第306批 刀3：dead_code 单列阈值（现值 27；留 48% 余量仍能拦住 27→541 这类回涨）
DEFAULT_MAX_DEAD_CODE = 40
SKIP = ["unused_imports", "config_audit"]

#: ★第306批 刀3：**判据版本戳**。判据一变（检测器增删 / 严重度口径 /
#: dead_code 存活判据升级）必须升版，阈值随之按新版重算；跨版本比基线无效。
CRITERIA_VERSION = "2026-10-19.308"


def main() -> int:
    _ap = argparse.ArgumentParser(description="代码问题趋势门禁（302批刀4）")
    _ap.add_argument("--json", action="store_true", help="输出 JSON")
    _ap.add_argument("--max-high", type=int, default=DEFAULT_MAX_HIGH)
    _ap.add_argument("--max-medium", type=int, default=DEFAULT_MAX_MEDIUM)
    _ap.add_argument("--max-dead-code", type=int, default=DEFAULT_MAX_DEAD_CODE)
    _ap.add_argument("--stamp", action="store_true",
                     help="只打印判据版本戳（供 CI 记录基线所属版本）")
    _a = _ap.parse_args()
    if _a.stamp:
        print(CRITERIA_VERSION)
        return 0

    try:
        from nucleus.self_inspector import get_self_inspector
        # ★第306批 刀3：不再下推 min_severity —— dead_code 恒为 low，
        #   若仍按 medium 过滤，即使不 SKIP 也永远数不到它（这正是
        #   「SKIP 去掉 dead_code」单独不够的原因）。严重度分桶在下面做。
        _issues = get_self_inspector().detect_code_issues(
            skip_detectors=SKIP, min_severity=None)
    except Exception as e:
        print(f"[ERROR] 扫描失败: {type(e).__name__}: {e}")
        return 2

    _high = [i for i in _issues if str(i.get("severity", "")).lower() == "high"]
    _med = [i for i in _issues if str(i.get("severity", "")).lower() == "medium"]
    # ★第307批 刀1：dead_code 分两档 —— 判死（severity=low）才进门禁；
    #   中间档产出的「待复核清单」（severity=info / review_only）**不计入 FAIL**，
    #   只作为不阻断的观测值输出（图解析率不足时它本来就不可信）。
    _dead = [i for i in _issues
             if str(i.get("type", "")) == "dead_code"
             and str(i.get("severity", "")).lower() != "info"]
    # ★第308批 刀2：待复核清单已降级为**统计口径**（不再进 issues）——
    #   数量从检测器的模块级统计读；兼容口径切换期（若仍有 info 级 dead_code
    #   落在 issues 里，以实测条数为准），取两者较大值避免漏数。
    _dead_review_in_issues = [i for i in _issues
                              if str(i.get("type", "")) == "dead_code"
                              and str(i.get("severity", "")).lower() == "info"]
    _review_stats: dict = {}
    if not _dead_review_in_issues:
        try:
            from nucleus.self_inspector import get_dead_code_review_stats
            _review_stats = get_dead_code_review_stats()
        except Exception as e:
            print(f"[WARN] 读取待复核统计失败（按 0 计）: {type(e).__name__}: {e}")
    _review_count = max(len(_dead_review_in_issues),
                        int(_review_stats.get("count", 0) or 0))
    _by_type: dict[str, int] = {}
    for _i in _issues:
        _t = str(_i.get("type", "?"))
        _by_type[_t] = _by_type.get(_t, 0) + 1

    _fail = (len(_high) > _a.max_high or len(_med) > _a.max_medium
             or len(_dead) > _a.max_dead_code)
    _payload = {
        "criteria_version": CRITERIA_VERSION,
        "high": len(_high),
        "medium": len(_med),
        "dead_code": len(_dead),
        "dead_code_review": _review_count,
        "dead_code_review_stats": _review_stats,
        "max_high": _a.max_high,
        "max_medium": _a.max_medium,
        "max_dead_code": _a.max_dead_code,
        "by_type": dict(sorted(_by_type.items(), key=lambda kv: -kv[1])),
        "high_items": [{"file": i.get("file"), "type": i.get("type"),
                        "method": i.get("method"), "line": i.get("line")} for i in _high],
        "verdict": "FAIL" if _fail else "PASS",
    }
    if _a.json:
        print(json.dumps(_payload, ensure_ascii=False, indent=2))
    else:
        print(f"[代码问题趋势] criteria={CRITERIA_VERSION} "
              f"high={len(_high)}(阈值{_a.max_high}) "
              f"medium={len(_med)}(阈值{_a.max_medium}) "
              f"dead_code={len(_dead)}(阈值{_a.max_dead_code})"
              f" 待复核={_review_count}(统计口径,不进issues,不判) => {_payload['verdict']}")
        for _t, _n in _payload["by_type"].items():
            print(f"  {_t}: {_n}")
        for _h in _payload["high_items"]:
            print(f"  [HIGH] {_h['type']} {_h['file']}:{_h['line']} {_h['method']}")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
