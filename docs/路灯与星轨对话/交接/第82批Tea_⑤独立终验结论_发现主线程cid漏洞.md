# ⑤独立终验结论（星轨，非自证）—— 不通过，发现 cid 主线程漏洞

> 终验人：新窗口星轨。方法：grep 生产代码逐跳读 + 本机独立复跑 + 先红后绿，不采信上一轮自证。
> 日期：2026-09-19。**结论：⑤不通过，需路灯补一个主线程 cid 漏洞后再重启验收。**

## 一、复跑与 diff 独立复核
- `pytest tests/test_dialog_cid_sanitize_m82.py`：**15 passed**（独立复跑，非上一轮输出）。
- diff 独立复核：PulseInnerWorld 4 处、PulseController 8 处，与任务书规格一致，无意外改动。

## 二、cid 链逐跳读码（重点，最易翻车处）

| 跳 | 位置 | 生产代码事实 | 判定 |
|---|---|---|---|
| 发射端 | PulseInnerWorld:1393 | OPEN_URL payload 加 `"search_correlation_id": correlation_id` | ✅ |
| 控制器接住 | PulseController:2424 | `_on_open_url` 从 payload 取字段，但 `return self._search_deep(url, reason, search_topic, payload)` **把原 payload 原样传下，未重新构造** | ✅ 透传 |
| 异步线程路径 | :2631 | `executor.submit(self._execute_headless_search, url, reason, search_topic, payload)` | ✅ payload 入 |
| 异步取 cid | :2537 | `_execute_headless_search` 内 `correlation_id=payload.get("search_correlation_id","")` 传 `_search_deep_headless` | ✅ |
| 回传写回 | :757 | `_emit_stage_feedback` payload 加 `"search_correlation_id": correlation_id` | ✅ |
| 消费端 | PulseInnerWorld:3378/17347 | `_pick_search_cid` 优先 `payload.get("search_correlation_id")` | ✅ |

## 三、终验抓到的漏洞（pytest 未覆盖）❌

**主线程路径丢 cid**：`PulseController.py:2681-2683`
```python
result = self._search_deep_headless(
    search_topic or reason, reason, max_articles
)
```
- `_search_deep` 有**两条**进 `_search_deep_headless` 的路径：
  - 异步线程路径（:2628 判断后走 :2631 `_execute_headless_search`）——已取 cid ✅；
  - **主线程路径（:2681 直接调 `_search_deep_headless`）——没传 correlation_id** ❌。
- 深度搜索脉冲若在主线程触发（或非 Playwright 线程池路径），cid 在此断链，回传 payload 里 `search_correlation_id` 为空串，消费端落到旧注册表 pop（键又失配）→ 后台静默，**正是 D167 原缺陷在主线程路径上复现**。
- 这是上一轮"自己改自己验"漏掉的点，交接文件预警的"控制器重新构造/半路丢弃"果然存在——只是藏在分支里。

## 四、D168 白名单（pytest 层面）
- 短答案保留（你好/在吗/1+1/你是谁）、内部标记硬清空（关联知识/[核心智慧]/代码片段:）——15 用例绿。
- **但真对话终验（"我还需要再想想"误杀案例、真实问候）必须等重启后小林真人测**，pytest 不算数。

## 五、处置
- ⑤**不标通过**，保持"已落地待终验 + 发现漏洞"。
- 已写补漏任务书给路灯：`docs\路灯与星轨对话\任务书\第82批Tea_主线程cid漏洞补漏_任务书.md`。
- 补漏合并后，再约小林重启②③⑤真对话验收。
