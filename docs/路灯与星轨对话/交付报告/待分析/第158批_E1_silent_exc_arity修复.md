# 第158批 · E-1 silent_exc 模板缺陷修复（P0）· 交付报告

> 触发：路灯_触发指令_E1修复与3新票核验与活体余票_20261002.md（星轨 2026-10-02 晚）
> 性质：P0 修复（一次性领取，与 3 新票核验 + 活体余票同批）

## 一、现象与根因

- **现象**：运行时 `silent_exc() missing 'e'` ERROR ×19（被吞的异常本应可见化，却因缺参直接抛 TypeError）。
- **根因**：`tools/convert_except_pass.py:67` 模板（bc3aaf8 引入）生成的调用为 `silent_exc(where="<file>:<line>")`，漏传必填位置参数 `e`；签名 `nucleus/_silent_except.py:19` 的 `silent_exc(e, where="", level="debug")` 的 `e` 为必填。
- **坏调用点（9 处 / 跨 7 文件）**：`chat_service.py:337`、`health_ui.py:1278`、`tcp_client.py:170`、`PulsePersonalityKernel.py:220`、`PulseController.py:473/482/1965/3246`、`PulseFileDigester.py:630` —— 均为 `except X:`（无 `as e`）块内 `silent_exc(where=...)`。

## 二、修复内容

1. **9 处坏调用补实参 + 绑定异常变量**：统一改 `except X:` → `except X as e:`，`silent_exc(where=...)` → `silent_exc(e, where=...)`。
2. **模板根因修复**（`tools/convert_except_pass.py`）：生成 `silent_exc(e, where="...")` 并自动把 `except X:` 改写为 `except X as e:`（仅简单单异常形态，不动多异常/已绑定），防再生。
3. **门控 arity AST 检查**（T-自我审计-3 route② 第一发实证弹）：新建 `tools/ci/check_silent_exc_arity.py`，扫全仓 `.py`（跳过 tmp/.git/.bak 等）对每个 `silent_exc(...)` 校验必填 `e`（首参 或 `e=` 关键字），缺失即 FAIL；接入 `.git/hooks/pre-commit` 1.8 段；含 `--selftest` 自证；cw2 友好（防御 except 接 `silent_exc`）。

## 三、验收

| 项 | 结果 |
|---|---|
| `grep "silent_exc(where="` 源码归零（仅门禁文档/示例残留，非调用） | ✅ |
| ruff F（系统 0.16.5，`check --select F`） | ✅ All checks passed |
| cw2 静默except / CRCRLF / 未跟踪盲区 | ✅ PASS（零新增静默 handler） |
| 新增 silent_exc arity 门禁（绝对路径运行 + selftest） | ✅ PASS |
| 全门禁（deprecated-import / event-string / slice1-collect 4487 / broken-chain 红线=基线 / CW3-B / 登记册 152 条到期未裁 0） | ✅ PASS |

## 四、提交

- 代码修复：`10eb02f`（6 业务文件 + 1 模板工具，精确暂存零并入他方遗留）
- 门禁件：`cd2fa8b`（`[ci-gate-change]`，仅 CI 文件，T150-5 隔离守卫 + commit-msg 守卫）

## 五、157 余票状态声明行

`157余票状态：C-8接线[已触发·8a73ab4] / T-框架-2[已触发・验收登记・verify rc=0] / T-基础-1[已触发・见提交 eb328e8] / 报告契约-3[已触发・见提交 92cd205] / E-1[已触发・见提交 10eb02f+cd2fa8b] / T-落盘路径泄漏-1[待] / T-向量库元数据污染-1[待] / N-4[待活体窗] / N-5[待活体窗] / N-8[待] / 空转[待活体窗] / N-6★[待] / N-9[待]`

> 纪律：逐刀独立提交、精确暂存零并入他方 4 个 `M` 遗留 + 6 未跟踪 doc（非我产）；未 push（铁律113）；匿名 `Tongtong Dev`；PII 0。
