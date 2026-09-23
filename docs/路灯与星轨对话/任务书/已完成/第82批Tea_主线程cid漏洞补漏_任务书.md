# 任务书：第82批 T-e —— 补 cid 主线程路径漏洞（PulseController）

- 发起：星轨；执行：路灯；日期：2026-09-19
- 背景：第82批 T-d（⑤）cid 端到端修复后，星轨独立终验发现主线程路径仍丢 cid。

## 一、缺陷
`organs/motor/PulseController.py` `_search_deep` 有两条进 `_search_deep_headless` 的路径：
- 异步线程路径（:2628 判断后走 `:2631 executor.submit(self._execute_headless_search, ...)`）——已在 `:2537` 取 `payload.get("search_correlation_id","")` 传入 ✅；
- **主线程路径（`:2681-2683` 直接调 `_search_deep_headless(search_topic or reason, reason, max_articles)`）——未传 correlation_id** ❌。

深度搜索脉冲若在主线程触发，cid 在此断链，回传 payload 里 `search_correlation_id` 为空，消费端落旧注册表 pop（键失配）→ 后台静默，D167 原缺陷在主线程复现。

## 二、改法（最小改动）
`PulseController.py:2681-2683` 改为：
```python
result = self._search_deep_headless(
    search_topic or reason, reason, max_articles,
    correlation_id=payload.get("search_correlation_id", ""),
)
```
同时检查 `:2689` 起的 requests 降级路径（非 Playwright）内若有 `_emit_stage_feedback(...)` 调用，一并补 `correlation_id=payload.get("search_correlation_id","")`；若该段不发 stage 回传，说明即可，不要新增。

## 三、先红后绿回归
- 新增用例（或补进 `tests/test_dialog_cid_sanitize_m82.py`）：构造一个 `_search_deep` 主线程分支（可 mock `threading.current_thread()` 返回主线程、mock headless 调用），断言 `_search_deep_headless` 收到的 `correlation_id` 等于 payload 里的非空 cid。
- 改前该用例红（收到空串），改后绿。
- 全量复跑 `tests/test_dialog_cid_sanitize_m82.py` 保持 15 passed + 新增用例。

## 四、红线
- 改前备份到 `.bak_batch82a/`（已存在则覆盖为 `.bak_tea`）；
- 不改 config.py 开关；不写 data\knowledge\；只改 `.py`（重启才生效）。

## 五、交付
- diff、新增/修改测试先红后绿输出、一句话确认 requests 降级路径是否需补。

---

## 六、交付结论（路灯，2026-09-19）

- 交付报告：`docs/路灯与星轨对话/交付报告/已分析/2026-09-19_主线第82批TeaTfg_cid主线程补漏与基准修复_交付报告.md`
- **状态：完成**。

| 项 | 结果 |
|---|---|
| 主线程路径补 cid | `PulseController.py:2681-2684` 改为 `correlation_id=payload.get("search_correlation_id", "")`（payload 在同作用域已可用，未改任何签名） |
| requests 降级路径是否需补 | **不需要**。该段（:2689-2769）只调 `_emit_digest`/`_finish_deep_search`/`_fallback_single_search`；全文件 `_emit_stage_feedback` 仅 5 处调用（:917/:1066/:1098/:1117/:1136），**全部在 `_search_deep_headless` 内**且均已带 `correlation_id=correlation_id`。 |
| 先红后绿 | 改前 `1 failed, 16 passed`（`AssertionError: 主线程路径丢 cid（任务书 T-e 缺陷）: {'correlation_id': '', 'topic': '主题'}`）；改后 **`17 passed`** |
| 全量复跑 | `tests/test_dialog_cid_sanitize_m82.py` **17 passed**（原 15 + 新增 2 用例 `TestMainThreadPathCarriesCid`） |
| 门禁 | ruff F 全项目 `All checks passed!`；E402 零新增；py_compile EXIT 0 |
| 备份 | `.bak_tea/`（`.bak_batch82a/` 已存在 → 按红线改用 `.bak_tea/`），含 `PulseController.py.bak` + 测试文件改动前副本 |
| 证据 | `tmp/m82_tea_red.txt`、`tmp/m82_tea_green.txt`、`tmp/m82_tea_ruff.txt`、`tmp/m82_tea_pyc.txt` |

**遗留**：本改动为 `.py`，**重启才生效**（交付时框架 PID 14532 运行中，加载旧代码）；⑤ 真对话终验（问候/短事实/cid 真回来）须重启后小林真人测，pytest 不算数。

