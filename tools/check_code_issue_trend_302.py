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
- 默认 **跳过 dead_code / unused_imports / config_audit** 三个检测器——
  它们量极大（本窗 638 条）且 severity 恒为 low，进不了进化链路，
  只为避免每轮多花数十秒（与 self_inspector 的 skip_detectors 设计一致）。
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
SKIP = ["dead_code", "unused_imports", "config_audit"]


def main() -> int:
    _ap = argparse.ArgumentParser(description="代码问题趋势门禁（302批刀4）")
    _ap.add_argument("--json", action="store_true", help="输出 JSON")
    _ap.add_argument("--max-high", type=int, default=DEFAULT_MAX_HIGH)
    _ap.add_argument("--max-medium", type=int, default=DEFAULT_MAX_MEDIUM)
    _a = _ap.parse_args()

    try:
        from nucleus.self_inspector import get_self_inspector
        _issues = get_self_inspector().detect_code_issues(
            skip_detectors=SKIP, min_severity="medium")
    except Exception as e:
        print(f"[ERROR] 扫描失败: {type(e).__name__}: {e}")
        return 2

    _high = [i for i in _issues if str(i.get("severity", "")).lower() == "high"]
    _med = [i for i in _issues if str(i.get("severity", "")).lower() == "medium"]
    _by_type: dict[str, int] = {}
    for _i in _issues:
        _t = str(_i.get("type", "?"))
        _by_type[_t] = _by_type.get(_t, 0) + 1

    _fail = len(_high) > _a.max_high or len(_med) > _a.max_medium
    _payload = {
        "high": len(_high),
        "medium": len(_med),
        "max_high": _a.max_high,
        "max_medium": _a.max_medium,
        "by_type": dict(sorted(_by_type.items(), key=lambda kv: -kv[1])),
        "high_items": [{"file": i.get("file"), "type": i.get("type"),
                        "method": i.get("method"), "line": i.get("line")} for i in _high],
        "verdict": "FAIL" if _fail else "PASS",
    }
    if _a.json:
        print(json.dumps(_payload, ensure_ascii=False, indent=2))
    else:
        print(f"[代码问题趋势] high={len(_high)}(阈值{_a.max_high}) "
              f"medium={len(_med)}(阈值{_a.max_medium}) => {_payload['verdict']}")
        for _t, _n in _payload["by_type"].items():
            print(f"  {_t}: {_n}")
        for _h in _payload["high_items"]:
            print(f"  [HIGH] {_h['type']} {_h['file']}:{_h['line']} {_h['method']}")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
