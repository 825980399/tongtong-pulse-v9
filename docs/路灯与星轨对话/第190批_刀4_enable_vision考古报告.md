# 第 190 批 · 刀4 · `enable_vision` 键段考古报告

> 依据：第 190 批施工任务书 刀4 ①「`config.py:1937` `"enable_vision": True` 键段上下文考古（所属配置段 / 与 Lung 无接线证据确认）」
> 处置纪律：仅考古、不接线、不翻开关（验收要求）。

## 1. 目标键

| 项 | 值 |
|----|----|
| 文件 | `config.py` |
| 行号 | `:1937` |
| 原文 | `"enable_vision": True,` |

## 2. 所属配置段

`enable_vision` 位于 **`FEATURE = {`** 字典内部（`config.py:1927`），其段注释为 `:1926`「`# ========== 功能总开关 ==========`」。

即 `enable_vision` 是「功能总开关」`FEATURE` dict 的一个布尔键，与 `l3_*` 动态扩缩容配置、`enable_audio_detect` / `enable_motor` / `enable_evolution` / `enable_immune` 等同段并列。

**暴露方式（重要）**：`enable_vision` **不是** `config` 模块顶层属性，须以 `config.FEATURE["enable_vision"]` 访问；`hasattr(config, "enable_vision")` 为 `False`。实测验证：

```
>>> config.FEATURE["enable_vision"]
True
>>> hasattr(config, "enable_vision")
False
```

## 3. 与 Lung 无接线证据

- `organs/body/PulseLung.py`（含 刀2 刚扩展的 `_build_channel_messages :878` 多模态序列化层）全文 grep `enable_vision` → **0 命中**。
- `_call_channel` / `_build_channel_messages` 仅读取 `ENABLE_OUTPUT_LENGTH_OPTIMIZATION`、`ENABLE_CHANNEL_TIMEOUT_OVERRIDE`、`REMOTE_API_CONFIG`、`LLM_MAX_TOKENS_OVERRIDE` 等，**从不读取** `enable_vision` / `FEATURE["enable_vision"]`。
- 因此 `FEATURE["enable_vision"] = True` 当前是一个**未被任何业务路径消费的死开关**，对 Lung 多模态序列化层的行为**零影响**。

## 4. 易混淆点：两套 `enable_vision` 语义

- `config.py:1937` 的 `FEATURE["enable_vision"]`：**功能总开关段**的预留开关（当前无人消费）。
- `nucleus/hardware_probe.py:236 / :249` 另有独立 `"enable_vision": False`：属于**硬件能力探测字段**（视觉皮层设备探测），与 FEATURE 开关是**两套语义**，请勿混淆。

## 5. 结论

- **考古结论**：`enable_vision` 属 `FEATURE`「功能总开关」段，当前值 `True`，但与 Lung 多模态序列化层**零接线**，为后续（推 191）远端视觉模型选型时再接线的预留开关。
- **本批处置**：仅考古、不接线、不翻开关。远端视觉模型选型 + 真帧共窗推 191。
- **后续**：191 立项时，应显式在 `_build_channel_messages` / `_call_channel` 读取 `FEATURE["enable_vision"]` 完成接线，并补对应门控测试。
