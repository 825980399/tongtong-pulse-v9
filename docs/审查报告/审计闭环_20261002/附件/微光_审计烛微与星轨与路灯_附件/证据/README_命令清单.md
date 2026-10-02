# 微光审计 证据清单 · 2026-10-02（HEAD 6b5708b）
复算脚本原文：任务工作区 .aionclaw-tmp/recomp1.py recomp2.py gates.py verify_tickets.py verify2.py

- A1: ruff check . --statistics  (D:\Program Files\Python312\Scripts\ruff.exe, 0.16.5)
- A2: python tools/scan_except_pass.py ; grep _create_organ; wc -l PCT; grep 每日分析完成 logs/pulse.log*
- A3: registry csv columns; git show 5bb14b5:...登记册.csv (105 rows)
- B1: git show 471b1d8 (20 new rows); git show 4aee8fc (1 file 1 line)
- B2: 协作模式说明_v1.0.md L232-243 (六方角色 v1.2)
- C1: python tools/ci/check_event_string_gate.py 等 6 门禁 -> 6/6 PASS
- C2/C3: main.py L1212/1245-1254; SEE L343-349/L36; aibot_logger 导出; PulseCore L261/L291
