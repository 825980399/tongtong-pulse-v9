# 第128批 T-128a · 静默except批4（收口16员）DIFF 与核验

## 背景与决策
烛微 `dz128` 重锚表磁盘已清理，精确 16 员名单缺失。按用户决策 **「自主重建(接受偏差)」**：
基于任务书点名 3 员 + 全库扫描挑低风险模块补满 16 员，避开 `PulseInnerWorld` 头部巨量
`except` 与 `PatchManager` 等高危大文件。**验收口径变更**：不再强求烛微精选域 472→456，
改为「全库净减 16 + CI 新增=0」（交付报告注明偏差）。

## 改造模式
裸 `except Exception:` / `except (TypeError, ValueError):` → 加 `as e`（若无别名）+
在 handler 体首语句前插入 `silent_exc(e, "file:line:语义", level="warning")`，并把
`pass`/`return None` 等体改为带日志的兜底返回。导入 `from nucleus._silent_except import silent_exc`
按需注入（多行 import 块用安全位插入，已规避语法破坏）。

## 16 员清单
### 点名 / 大文件（3）
| 文件 | 行号 | 语义 |
|------|------|------|
| `organs/body/PulseLiver.py` | 3622 | 状态时间字段解析异常 (except (TypeError,ValueError)) |
| `organs/brain/PulseInnerWorld.py` | 18680 | 知识检索探针异常 (except Exception: return None) |
| `nucleus/reporting/report_bus.py` | 60 | 上报总线异常 (except Exception: pass→return True) |

### 低风险小文件（13，含 tracer:169）
| 文件 | 行号 |
|------|------|
| `nucleus/llm/semantic_cache.py` | 54 |
| `nucleus/mnemosyne/experience_pool.py` | 97 |
| `nucleus/reasoning/ReasoningWorkerPool.py` | 36 |
| `nucleus/fast_ops.py` | 22 |
| `nucleus/reasoning/SelfCalibrator.py` | 30 |
| `nucleus/device_router.py` | 62 |
| `nucleus/knowledge_access_layer.py` | 35 |
| `nucleus/diagnostics.py` | 154 |
| `nucleus/llm/model_self_updater.py` | 66 |
| `nucleus/synapsys/ResonanceEngine.py` | 149 |
| `nucleus/evolution/EvolutionLoop.py` | 47 |
| `nucleus/evolution/QualityClosedLoop.py` | 168 |
| `utils/pulse_tracer.py` | 169 |

## 静默 handler 计数（AST 全库口径，排除 tests/ci/benchmark）
| 项 | 值 |
|----|----|
| 改前 | 238 |
| 改后 | 222 |
| **净减** | **16** |
| CI 名单外新增 | 0 |

## 行尾保全
4 文件 CRLF（PulseLiver / PulseInnerWorld / knowledge_access_layer / report_bus）、
12 文件 LF，均**整体一致、无混排、无 CRCRLF**；补丁按各文件自身 dominant 行尾插入，未翻转任何行尾。

## 门禁摘要（详见「第128批_门禁结果.md」）
- ruff F = 0（修复 knowledge_access_layer.py `except` 漏 `as e` 的 F821 后）
- 16 .py + tracer py_compile 全过
- m95：39 passed / 4 skipped / 0 failed
- 静默except CI：PASS（新增=0，净减16，parse 通过，CRCRLF 无）

> 完整逐文件 diff 见同目录 `第128批_changes.diff`。
