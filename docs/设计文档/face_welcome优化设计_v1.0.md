# face_welcome 优化设计 v1.0

**状态**：v1.0 待评审
**批次**：主线第52批 T5
**债号**：P2-365
**日期**：2026-09-14
**执行方**：路灯
**性质**：**纯设计文档**（本批不修改任何业务代码）

---

## 一、问题描述

第51批运行日志监测发现：**人脸识别欢迎流程（face_welcome）存在不必要的大模型调用**。

| 项 | 实测值 |
|---|---|
| 触发方式 | 人脸识别（`ChatEvent.USER_PRESENCE_DETECTED`） |
| 自动生成的问题 | `"你是谁"`（**自问自答**，非用户输入） |
| 本地回答相关性 | **0.49**（低于阈值 0.5） |
| 后果 | 触发大模型补救调用（`deepseek-v4-flash`，2.16s，517 字） |
| 频率 | **每次人脸识别浪费一次 LLM API 调用** |
| 关键证据 | `correlation_id=face_welcome_1789385` |

---

## 二、完整代码路径分析（实测取证）

### 2.1 触发点 → 欢迎 + 自问自答

**文件**：`functions/chat/chat_service.py`（`_on_global_event`，行 117-178）

```python
if event_type == ChatEvent.USER_PRESENCE_DETECTED:        # 行 129
    user_name = payload.get("user_name", "用户")
    print(f"\n👁️ 曈曈看到你回来了，{user_name}！")           # 行 133（纯打印欢迎）
    self._reset_silence_timer()

    if self.pulse_core and self.info_field:
        # ① 轻量欢迎脉冲（L1）
        welcome_pulse = self.pulse_core.emit(
            source_organ="对话模块",
            event_type=ChatEvent.SILENCE_TIMEOUT,          # 行 140
            payload={"user_name": ..., "silence_seconds": 0, ...},
            priority=7, layer="L1")
        self.info_field.publish(welcome_pulse)

        # ② ★自问自答：发射"你是谁"推理请求（L2）—— 浪费的源头
        _correlation_id = f"face_welcome_{int(time.time())}"   # 行 153
        _cortex.register_pending_inner_world(_correlation_id, {
            "content": "你是谁", "user_name": user_name, ...})
        inquiry_pulse = self.pulse_core.emit(
            source_organ="对话模块",
            event_type=InferenceEvent.REQUEST,              # 行 168
            payload={"question": "你是谁",                  # 行 170
                     "user_name": user_name,
                     "correlation_id": _correlation_id},
            priority=7, layer="L2")
        self.info_field.publish(inquiry_pulse)
```

### 2.2 推理请求 → 本地回答

**文件**：`organs/brain/PulseInnerWorld.py`

| 位置 | 内容 |
|---|---|
| 行 531 | `if event_type == InferenceEvent.REQUEST:` —— 订阅该事件 |
| 行 641 | 管道注册：`(101, self._detect_simple_query_local)` —— **优先级 101：简单问题本地回答** |
| 行 2013 | `_detect_simple_query_local(ctx)` —— 实现 |
| 行 2043 | **身份类分支**：`if _answer is None and len(q) <= 10 and re.search(r'你是谁\|你叫什么\|你的名字\|你的身份', q):` → `self._build_who_am_i_response(...)` |

**"你是谁" 长度 = 3 ≤ 10 → 应被本地回答命中** ✓

### 2.3 相关性验证 → 大模型补救

| 位置 | 内容 |
|---|---|
| `organs/brain/PulseSemanticComprehension.py` | 计算 `relevance` 维度（参与置信度） |
| `organs/body/PulseLung.py` | **二次补救调用**（输出验证拦截 / 低置信度时补救） |
| `PulseInnerWorld.py` 行 749 | `if _relevance < 0.10:` —— 一处相关性降级判定 |

**实测**：本地回答相关性 **0.49** < 阈值 **0.5** → 触发大模型补救。

### 2.4 链路总览

```
人脸识别 (USER_PRESENCE_DETECTED)
        │
        ├─▶ ① 打印欢迎语（"👁️ 曈曈看到你回来了，X！"）
        ├─▶ ② 发射欢迎脉冲（L1, SILENCE_TIMEOUT）
        └─▶ ③ 发射推理请求（L2, InferenceEvent.REQUEST, question="你是谁")
                    │
        PulseInnerWorld._detect_simple_query_local（优先级 101）
                    │
                    ├─▶ 本地回答命中（_build_who_am_i_response）
                    │            │
                    │            ▼
                    │      相关性评分 = 0.49
                    │            │
                    │      0.49 < 0.5 ❌
                    │            │
                    └────────────┴──▶ ★大模型补救调用（2.16s / 517 字 / 消耗额度）
                                        │
                                        ▼
                                 结果回填对话模块 → 输出
```

### 2.5 ★设计文档的诚实标注（未在本批定位的部分）

**相关性阈值 0.5 的精确判定点未在本批定位到**。本批实测到的相关度阈值有：

* `PulseInnerWorld.py:253`：`memory_relevance_threshold = 0.3`（记忆匹配最低相关度）
* `PulseInnerWorld.py:749`：`if _relevance < 0.10:`
* `PulseSemanticComprehension.py:695-697`：`relevance = min(1.0, 0.5 + local_confidence * 0.5)`，API 纠正时 `min(relevance, 0.4)`

⇒ **实施前需先确认**（见 §六 前置调查项 P0）：`0.49` 这个值由哪个函数产出、
`0.5` 阈值写在何处（`config.py` 还是硬编码）。

---

## 三、三个修复方案对比

### 方案 A（推荐）：face_welcome **直接生成欢迎语**，不走问答流程

**做法**：人脸识别后直接调用欢迎语生成（模板 / 简单规则），**不发射** `InferenceEvent.REQUEST`。

```python
# chat_service._on_global_event 中
if event_type == ChatEvent.USER_PRESENCE_DETECTED:
    # ① 保留：打印欢迎语 + 欢迎脉冲（L1）
    ...
    # ② ★移除：不再发射 "你是谁" 推理请求（L2）
    # ③ 改为：直接发一条欢迎内容脉冲（走向嘴巴输出）
    if self.pulse_core and self.info_field:
        _greet = self._build_welcome_text(user_name)   # 模板/规则，零 LLM
        _p = self.pulse_core.emit(
            source_organ="对话模块",
            event_type=MouthEvent.SPEAK,               # 直接交给嘴巴
            payload={"text": _greet, "source": "face_welcome"},
            priority=7, layer="L1")
        self.info_field.publish(_p)
```

| 维度 | 评估 |
|---|---|
| 实现复杂度 | **低**（改 1 个文件 1 处；新增 1 个纯函数 `_build_welcome_text`） |
| 改动范围 | `functions/chat/chat_service.py`（约 15 行）；可选 `config.py`（开关） |
| 风险 | **低**：不触碰推理管道、语义理解、相关性校验 |
| 回滚 | 灰度开关 `ENABLE_FACE_WELCOME_DIRECT`（默认 True；False → 退回原流程） |
| 对现有功能影响 | **正面**：移除一次无意义自问自答；`register_pending_inner_world` 不再被调用（需确认无其他依赖该 correlation_id 的逻辑） |
| API 成本 | **每次人脸识别省 1 次 LLM 调用** |

**★需确认**：原流程中"你是谁"的回答**是否会融入自我画像**（代码注释行 135 提到
`★v17.0 Q8修复：改为发射"你是谁"推理请求，让回答能融入自我画像`）。
若该副作用有价值，方案 A 需保留"融入画像"的路径（改为低成本方式）。

---

### 方案 B：标记 face_welcome 为**内部对话**，不触发大模型补救

**做法**：`InferenceEvent.REQUEST` 的 payload 增加 `internal=True`；
相关性校验处**内部对话即使相关性低也不触发补救**。

```python
# chat_service（发射侧）
payload={"question": "你是谁", "user_name": user_name,
         "correlation_id": _correlation_id, "internal": True}

# 相关性校验处（补救前）
if not ctx.internal and relevance < _threshold:
    _do_llm_rescue()      # 仅非内部对话才补救
```

| 维度 | 评估 |
|---|---|
| 实现复杂度 | **中**（需改发射侧 + 校验侧；`ctx` 需支持 `internal` 字段） |
| 改动范围 | `chat_service.py` + `PulseInnerWorld.py`（`InferenceContext` + 补救判定点）+ 可能 `PulseLung.py` |
| 风险 | **中**：`internal` 标记需贯穿整条管道；遗漏任一点则补救照旧（静默失败） |
| 回滚 | 开关 `ENABLE_FACE_WELCOME_INTERNAL`（默认 True） |
| 对现有功能影响 | 本地回答仍会执行（保留"融入画像"的语义路径），**但不再补救** → 欢迎语可能偏短 |

**优点**：保留原设计意图（自问自答融入画像）。
**缺点**：改动面更大、`internal` 语义需在 3 处以上一致实现，**易漏**。

---

### 方案 C：**优化本地回答**，提高相关性到 0.5 以上

**做法**：为 face_welcome 场景定制本地回答模板，使其相关性 ≥ 0.5，从而**不触发**补救。

| 维度 | 评估 |
|---|---|
| 实现复杂度 | **中高**：需先定位相关性算法与阈值（本批未定位，见 §2.5） |
| 改动范围 | `PulseInnerWorld._build_who_am_i_response` + 可能相关性算法本身 |
| 风险 | **高**：相关性算法服务于**全管道**，为其调参可能影响其他场景的补救判定（回归面不可控） |
| 回滚 | 较难（参数影响面广） |
| 对现有功能影响 | **不确定**：可能"治好一个、弄坏一片" |

**★不推荐**：为单场景调全局算法的相关度，属"用错杠杆"。

---

### 3.1 方案对比总表

| 维度 | **A（推荐）** | B | C |
|---|---|---|---|
| 实现复杂度 | **低** | 中 | 中高 |
| 改动范围 | 1 文件 / ~15 行 | 3 文件 / 多点 | 算法层 |
| 风险 | **低** | 中 | **高** |
| 回滚 | 易（单开关） | 易 | 难 |
| 消除 LLM 调用 | ✅ 彻底 | ✅（补救侧） | ✅（依赖阈值达标） |
| 保留"融入画像" | ⚠️ 需补充 | ✅ | ✅ |
| 全局回归风险 | **无** | 低 | **高** |

**⇒ 推荐方案 A**（若"融入画像"必要，则 A + 补充低成本画像更新路径）。

---

## 四、推荐方案（A）实施计划

### 4.1 前置调查（**P0，必须先做**）

| # | 调查项 | 目的 |
|---|---|---|
| 1 | `0.49` 由哪个函数产出、`0.5` 阈值写在何处 | 确认"浪费"的精确触发点（本批未定位） |
| 2 | "你是谁" 的回答是否被其他逻辑消费（如自我画像写入） | 决定是否需保留副作用 |
| 3 | `register_pending_inner_world(_correlation_id, ...)` 的消费方 | 移除发射后是否有悬挂上下文 |
| 4 | `MouthEvent.SPEAK` 的直接使用先例 | 确认方案 A 的输出通道可行 |

### 4.2 修改清单（预计工时 **2~3 小时**）

| 文件 | 改动 |
|---|---|
| `functions/chat/chat_service.py` | ① 移除 `InferenceEvent.REQUEST` 发射（约 12 行）<br>② 新增 `_build_welcome_text(user_name) -> str`（纯函数，模板/规则）<br>③ 改为发射 `MouthEvent.SPEAK` |
| `config.py` | 追加 `ENABLE_FACE_WELCOME_DIRECT = True`（灰度开关） |

### 4.3 测试策略

| 层 | 用例 |
|---|---|
| 单元测试（新增 `tests/test_face_welcome_m53.py`） | ① 开关开：`USER_PRESENCE_DETECTED` **不发射** `InferenceEvent.REQUEST`<br>② 开关开：发射 `MouthEvent.SPEAK` 且文本含用户名<br>③ 开关关：**完全退回**原行为（发射 REQUEST）<br>④ `_build_welcome_text` 边界（空/超长/特殊字符用户名）<br>⑤ 不引入新依赖（pulse_core 为 None 时不崩） |
| 集成验证（需重启） | 观察日志：人脸识别后**不再出现** `correlation_id=face_welcome_*` 的 `[大脑皮层] 输出锁已加`；LLM 调用计数不增长 |
| 回归 | 全量 pytest 0 failed；`verify` 0 FAIL |

### 4.4 验收标准

* ✅ 人脸识别后**无** `InferenceEvent.REQUEST`（开关开时）
* ✅ 欢迎语仍正常输出（含用户名）
* ✅ 开关关闭时行为与原实现**逐字一致**（零回归）
* ✅ 日志中 face_welcome 相关 LLM 调用降为 0
* ✅ pytest / verify 全绿

---

## 五、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | 移除自问自答后，"融入自我画像"副作用丢失 | 低 | 前置调查 #2；必要时补低成本画像更新 |
| 2 | `register_pending_inner_world` 上下文悬挂 | 中 | 前置调查 #3；同步移除注册调用 |
| 3 | `MouthEvent.SPEAK` 通道语义不符 | 低 | 前置调查 #4；或改用现有"欢迎脉冲"通道 |
| 4 | 开关误开导致回归 | 低 | 默认值经评审；开关关闭路径纳入门控测试 |

---

## 六、结论

1. **根因**：face_welcome **自问自答**（"你是谁"）→ 本地回答相关性 0.49 < 0.5 → 触发大模型补救。
2. **推荐方案 A**（直接生成欢迎语、不走问答流程）：改动最小（1 文件 ~15 行）、风险最低、彻底消除浪费。
3. **实施前必须完成 §4.1 的四项前置调查**（尤其 #1 相关性阈值定位 —— 本批未定位到）。
4. **本批仅设计，不修改业务代码**；实施建议列入后续批次（需重启验证）。

---

## 附：关键代码位置索引

| 位置 | 内容 |
|---|---|
| `functions/chat/chat_service.py:129` | `ChatEvent.USER_PRESENCE_DETECTED` 分支入口 |
| `functions/chat/chat_service.py:133` | 打印欢迎语 |
| `functions/chat/chat_service.py:153` | `correlation_id = f"face_welcome_{ts}"` |
| `functions/chat/chat_service.py:166-177` | 发射 `InferenceEvent.REQUEST`（**浪费源头**） |
| `organs/brain/PulseInnerWorld.py:531` | 订阅 `InferenceEvent.REQUEST` |
| `organs/brain/PulseInnerWorld.py:641` | 管道注册（优先级 101 本地回答） |
| `organs/brain/PulseInnerWorld.py:2013` | `_detect_simple_query_local` 实现 |
| `organs/brain/PulseInnerWorld.py:2043` | 身份类本地回答分支（`len(q) <= 10`） |
| `organs/brain/PulseSemanticComprehension.py:695` | `relevance` 计算 |
| `organs/body/PulseLung.py` | 二次补救调用（输出验证拦截） |

**路灯 · 2026-09-14**
