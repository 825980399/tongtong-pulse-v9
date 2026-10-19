# 第156批 · B156-8 · P2-79 except_pass 续推 · 实测与协议

> 执行：路灯（自动化）｜ 签发：星轨 ｜ 日期：2026-10-02 ｜ 未 push（铁律113）
> 配套任务书：`docs/路灯与星轨对话/任务书/第156批_任务书_20260930.md` §B156-8「P2-79 except_pass 380/182 文件续推（utils→hardware→functions→organs→nucleus）」
> 数据底座：`tools/scan_except_pass.py`（AST 扫描）+ `tools/convert_except_pass.py`（转化器）

---

## 一、T0 实测（扫描口径）

> 扫描口径：except 子句体为 `pass` / `continue` / `break` / `return None` 且**未含 `silent_exc` 调用**的吸收型 except（即把异常静默吞掉、可能掩盖真实错误的代码点）。

| 指标 | 数值 |
|---|---|
| 吸收型 except 总数 | **331** |
| 续推前（本批开工） | 331 |
| 本批 pilot 转化 | **9 处 / 6 文件** |
| 续推后剩余 | **322** |

**按顶层包分布**（开工 331）：

| 层 | 命中 | 备注 |
|---|---|---|
| tests | 129 | 多为测试清理吸收，按既有约定不强制转（除非掩盖真实断言失败） |
| other（main.py 等） | 106 | 含框架生命周期/IO 容错，需逐文件裁决 |
| tools | 24 | 余量批 R1 已大量处理，残余数 |
| nucleus/self_inspector | 10 | 核心观测，后续批 |
| nucleus/reasoning | 7 | 后续批 |
| nucleus/knowledge | 6 | 后续批 |
| nucleus/synapsys | 6 | 后续批 |
| organs | 6 | **本批 pilot 已全转**（见 §三） |
| nucleus/data | 5 | 后续批 |
| nucleus/llm | 5 | 后续批 |
| 其余 nucleus 子模块 | ~40 | 后续批 |
| functions | 2 | **本批 pilot 已转** |
| hardware | 1 | **本批 pilot 已转** |

---

## 二、续推协议（机械、零行为变化、可回滚）

对任一吸收型 except，按以下规则转化（已固化于 `tools/convert_except_pass.py`）：

1. **保留原 except 子句与异常类型**，不改控制流；
2. 在 body 的 `continue` / `break` **之前**插入 `silent_exc(where="<file>:<line>")`；
   若 body 为纯 `pass`，则把 `pass` 直接替换为 `silent_exc(...)`；
3. `silent_exc` 仅记录日志、不重抛，故 `continue`/`break` 仍照常执行 → **行为等价，仅异常由「全静默」变「可见日志」**；
4. 文件缺失 `from nucleus._silent_except import silent_exc` 时自动补；已存在则复用；
5. 幂等：handler 内已含 `silent_exc` 则跳过，可重复运行；
6. 默认 dry-run，仅 `--apply` 落盘；落盘前打印逐行 diff 预览。

> 与门禁协同：本转化**新增 `silent_exc` 调用**属项目 sanctioned 模式（B156-6 已验证 cw2 静默except 门禁对新增 `silent_exc` 不拦截）；转化**不新增裸 `except:`**，符合要求。

---

## 三、本批 pilot 已落（非核心层，按任务书 utils→hardware→functions→organs 顺序）

> utils 层命中 0，故从 hardware 起。

| 文件 | 行 | 原 except | 转化 |
|---|---|---|---|
| `hardware/robot_body/tcp_client.py` | 166 | `except TimeoutError: continue` | +`silent_exc(where=...)` |
| `functions/health_ui.py` | 1277 | `except Exception: continue` | +`silent_exc(where=...)` |
| `functions/chat/chat_service.py` | 322 | `except EOFError: break` | +`silent_exc(where=...)` |
| `organs/identity/PulsePersonalityKernel.py` | 219 | `except Exception: continue` | +`silent_exc(where=...)` |
| `organs/motor/PulseController.py` | 472 / 480 / 1962 / 3242 | `except Exception: continue` ×4 | +`silent_exc(where=...)` ×4 |
| `organs/motor/PulseFileDigester.py` | 629 | `except UnicodeDecodeError: continue` | +`silent_exc(where=...)` |

合计 **9 处**；6 文件 ruff F 全过；复扫该 6 文件吸收型 except = **0**（门控测试 `tests/test_b156_8_except_pass_pilot.py` 固化）。

---

## 四、门禁结果

| 项 | 结果 |
|---|---|
| `ruff --select F`（6 转化文件 + 转化器 + 扫描器） | 全过 |
| pytest `tests/test_b156_8_except_pass_pilot.py` | **1 passed**（复扫 pilot 文件 = 0 吸收型 except） |
| collect 节点 | 4479 → 4480（+1，±5 内） |
| PII / 行尾 | 未引入 PII；转化仅加单行调用，行尾随仓库约定 |

---

## 五、剩余与后续批（本批不施工）

- **nucleus/ 核心层（~70）**：风险最高（观测/推理/知识/突触/LLM/数据），须逐文件裁决 + 隔离测试，排后续专门批；遵循「整函数静默 handler 一并转 silent_exc 归零」以避 cw2 nth 漂移陷阱。
- **tests/（129）**：测试清理吸收，按约定不强制转，除非掩盖真实断言失败，另行裁决。
- **other（106，含 main.py）**：框架生命周期/IO 容错，需逐文件裁决（部分容错吸收是合理设计，不应转）。

---

## 六、提交（未 push · 铁律113）

- `tools/scan_except_pass.py` + `tools/convert_except_pass.py`（扫描 + 转化工具）
- 6 个 pilot 文件转化（9 处）
- `tests/test_b156_8_except_pass_pilot.py`（门控测试）
- 本交付文档
- 仅含本批文件；其余他会话遗留脏文件零并入。
