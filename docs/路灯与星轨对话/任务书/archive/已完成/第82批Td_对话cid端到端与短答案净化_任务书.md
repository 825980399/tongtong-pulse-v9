# 任务书：第82批 T-d —— 对话 cid 端到端透传（D167）+ 短答案/问候路由净化（D168）

- **发起方**：星轨（规划+任务书+独立终验）
- **执行方**：路灯（WorkBuddy，唯一编码执行体）
- **日期**：2026-09-19
- **项目根**：`<PROJECT_ROOT>`（Windows，仅 PowerShell；权威 Python `D:\Program Files\Python312\python.exe`）
- **状态**：待路灯执行 → 交付后星轨独立终验（grep 生产证据 + 独立复跑 + 先红后绿），通过才更新总账

---

## 一、背景与问题（两个缺陷）

### D167 · 搜索终止回退时 correlation_id（cid）失配
内在世界发起一次深度搜索时会登记 `correlation_id`，期望搜索结束后拿着 cid 把结果"回嘴"给正在等回答的对话。实查链路发现 **cid 在半路被丢弃**，导致后台搜索静默、用户收不到回应：

1. 内在世界发射 `CONTROLLER_OPEN_URL`（`organs/brain/PulseInnerWorld.py`）：`_emit` 调用点约在 622 / 1388 / 1645 / 1806 行，payload 只带 `url/reason/search_topic/deep_search/search_intent`，**没带 correlation_id**。
2. 控制器接住（`organs/motor/PulseController.py` `_on_open_url` 约 2374 行）：只取 `url/reason/search_topic/deep_search`，**cid 被丢弃**。
3. `_search_deep → _execute_headless_search → _search_deep_headless`（约 2531 行）：**payload 到这里断链**，函数签名里没有 cid。
4. 控制器 `_emit_stage_feedback`（约 734 行）**重新构造 payload** 回传，且 `search_topic` 经过 `_preprocess_search_topic`（约 778 行）**改写+截断**。
5. 内在世界消费端（约 3294/3378 行附近）用 `self._active_search_correlation.pop(search_topic, "")` 取 cid——注册键是原始 `search_query[:80]`，回传键是改写截断后的 `search_topic`，**两个键必然不相等，pop 恒空**。

### D168 · 短答案净化误杀问候/短事实
`PulseInnerWorld._sanitize_internal_content` 末尾的 S6 判据（约 17407 行）原为：
```python
if len(answer) < 10 and _original:
    if _regex_removed or not _stripped_prefix:
        answer = ""   # 清空
```
`not _stripped_prefix` 这一支会把**不以"我了解到，"开头的真短答案**（"你好"、"在吗"、"1+1等于2"、"你是谁"）也清空成空串，最终兜底成"我还需要再想想"——这就是真实误杀案例。

---

## 二、两个补强（小林已拍板，必须做到）

### 补强1 · cid 必须端到端穿过控制器，不能只改两端
- 发射端（内在世界 OPEN_URL payload）必须带上 `search_correlation_id`；
- 控制器接住后**必须透传**到 `_search_deep_headless`，并由 `_emit_stage_feedback` **原样写回回传 payload**；
- 消费端优先取 `payload.get("search_correlation_id")`。
- **验收硬指标**：控制器带 cid 触发一次搜索后，消费端拿到的 `payload.get("search_correlation_id")` 必须是**非空**的同一个 cid（不能只靠旧注册表 pop 兜底）；不带 cid 时优雅降级为空串，不崩。

### 补强2 · 白名单列死
- **放行**（短但真答案）：问候类（你好/在吗/晚上好）、短事实类（1+1等于2）、"你是谁"类自我介绍——一律保留，不得清空。
- **硬清空**：答案里含内部标记词（`关联知识`、`[核心智慧]`、`[CODE_STYLE]`、`代码片段:`、`内部调用:`）的，即使剩余正文≥5字也**一律清放**（说明原答案混了内部内容）。
- 回归时必须用真实误杀案例："我了解到，你好"、"1+1等于几"不得再被清成空串。

---

## 三、具体改法（路灯据此实现，行号供定位，以实际代码为准）

### A. PulseInnerWorld.py（organs/brain/）
1. **新增纯函数方法** `_pick_search_cid(self, payload: dict, search_topic: str) -> str`：
   ```python
   def _pick_search_cid(self, payload, search_topic):
       cid = (payload or {}).get("search_correlation_id") or ""
       if cid:
           return cid
       return self._active_search_correlation.pop(search_topic, "")  # 旧注册表兜底
   ```
2. **消费端**（约 3378 行）：把 `self._active_search_correlation.pop(search_topic, "")` 改为 `self._pick_search_cid(payload, search_topic)`。
3. **发射端**（OPEN_URL 的 `_emit` payload，至少知识补充搜索那处，约 1388 行）：加 `"search_correlation_id": correlation_id`（该处已在 `if correlation_id:` 块内）。用户明确搜索那处（约 622 行）如作用域内有 correlation_id 也一并加。
4. **S6 判据收敛**（约 17407 行）：
   ```python
   if len(answer) < 10 and _original:
       if _regex_removed:            # 仅当正则真删了内部标记词 → 硬清空
           answer = ""
       # 否则（未命中内部标记）→ 真短答案，保留
   ```
   即删掉 `or not _stripped_prefix` 这一支。

### B. PulseController.py（organs/motor/）
1. `_emit_stage_feedback(...)` 增加参数 `correlation_id: str = ""`，构造的 payload 里加 `"search_correlation_id": correlation_id`。
2. `_search_deep_headless(...)` 增加参数 `correlation_id: str = ""`；其内部所有对 `_emit_stage_feedback(...)` 的调用（约 908 / 1057 / 1089 / 1108 / 1127 行）都把 `correlation_id=correlation_id` 透传进去。
3. `_execute_headless_search(...)` 调 `_search_deep_headless(...)` 时从 payload 取 `correlation_id=payload.get("search_correlation_id", "")` 传入（payload 在此函数作用域内可用，无需改 `_on_open_url` 签名）。

---

## 四、先红后绿回归清单（路灯交付时附 pytest 结果）

新建 `tests/test_dialog_cid_sanitize_m82.py`，至少覆盖：

**cid 三分支**
1. payload 带 `search_correlation_id` → `_pick_search_cid` 返回它（即使旧注册表也有值）；
2. payload 不带该字段 → 回退 `_active_search_correlation.pop(topic)`；
3. 都没有 → 返回 `""`（优雅降级，不抛异常）。

**控制器端到端**
4. 调 `_emit_stage_feedback(..., correlation_id="cid-xyz")`，捕获 publish 的 payload，断言 `payload["search_correlation_id"] == "cid-xyz"`（补强1硬指标）；
5. 不传 cid 时该字段为 `""`（不破坏旧链路）。

**短答案白名单**
6. 参数化保留：`"你好"`、`"在吗"`、`"1+1等于2"`、`"我了解到，你好"`、`"我了解到，1+1等于2"` → 输出非空；
7. `"你是谁"` 类自我介绍不被清空；
8. 参数化硬清空：`"关联知识：[...]"`、`"[核心智慧] ..."`、`"代码片段: ..."` → 输出不含原标记词；
9. 真答案里混入内部标记词（"我了解到，关联知识：[...] 残留正文"）→ 残留正文不得把内部内容当短答案放行。

**要求**：先跑一次确认 1~5 中 cid 用例、6~7 短答案用例**红**（旧代码行为），改完再跑全绿；两文件 `python -c "import ast; ast.parse(open(...).read())"` 语法冒烟通过。

---

## 五、红线（必须遵守）

- 不写 `data\knowledge\` 下任何文件；
- 不改 `config.py` 的运行开关；
- 改前备份到 `.bak_batch82a/`（目标文件：`organs/brain/PulseInnerWorld.py`、`organs/motor/PulseController.py`）；
- 不写外部黄金数据 `D:\<PROJECT_ROOT>-pulse-safeguard\`；
- 只改 `.py`（下次重启才生效，不影响运行中框架）；
- 不采信"自述通过"，以 pytest 实跑输出为准。

## 六、交付物
1. 两个生产文件改动（含备份）；
2. `tests/test_dialog_cid_sanitize_m82.py` 及先红后绿 pytest 输出；
3. 一段说明：cid 端到端穿过控制器的证据（哪几行透传、payload 字段名）。

---

## 七、处置说明（路灯，2026-09-19）——**本任务书未由路灯重复执行**

- **情况**：本任务书要求的改动**已由星轨在本批窗口直接落地**（见 `交接/第82批Td_⑤终验证据包_已落地待终验.md`：`PulseInnerWorld.py` 1394/3379/17348 + `PulseController.py` 740/755/2540，共 5 处 cid 接线 + S6 判据收敛）。路灯接手时实测生产代码已在位，`tests/test_dialog_cid_sanitize_m82.py` 存在且 **15 passed**。
- **未重复执行的理由**：避免同批重复指派造成空转/覆盖（项目既有教训：任务书现状判断常偏差，须先实测核实）。
- **⑤ 状态**：星轨独立终验判定 **不通过** —— 发现 `_search_deep` **主线程路径**（`PulseController.py:2681`）仍丢 cid；该漏洞已由**第82批 T-e** 关闭（`correlation_id=payload.get("search_correlation_id","")`），T-e 交付后 `tests/test_dialog_cid_sanitize_m82.py` **17 passed**（原 15 + 新增主线程 2 用例）。
- **本任务书归档不代表 ⑤ 通过**：⑤ 仍为"已落地待终验"，最终验收 = **重启后小林真对话**（`你好`/`在吗`/`1+1等于几`/`你是谁`/教新知识触发搜索终止回退有回应）。
- 交付报告：`docs/路灯与星轨对话/交付报告/已分析/2026-09-19_主线第82批TeaTfg_cid主线程补漏与基准修复_交付报告.md`

