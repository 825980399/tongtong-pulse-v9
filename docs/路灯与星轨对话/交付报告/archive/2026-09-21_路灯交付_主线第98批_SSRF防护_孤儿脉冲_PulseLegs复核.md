# 主线第98批 交付报告：SSRF 防护 / 孤儿脉冲 / PulseLegs 复核

> 派发：星轨 2026-09-21　|　执行：路灯（无人值守 Agent 流程，只读监控 + 隔离测试）
> 任务书：`docs/路灯与星轨对话/任务书/第98批_SSRF防护_孤儿脉冲_PulseLegs复核_任务书.md`
> 改动模式：**未停框架**，改完下次重启生效；改前已备份。

---

## 0. 任务概览

| 任务 | 级别 | 方案 | 结论 |
|---|---|---|---|
| T-98a | P0 | A（白名单加火山方舟域名/IP，双保险） | 已修复，门控全绿 |
| T-98b | P1 | A（肝订阅消费 + 清理历史孤儿） | 已修复，门控全绿 |
| T-98c | P1 | 烛微探针复核 | **前提证伪**：非假成功，账本不改 |

---

## 1. 任务书偏差清单（T0 实测，铁律 109：前提连续被证伪 → 开工前必须实测）

| # | 任务书前提 | 实测结论 | 处置 |
|---|---|---|---|
| ① | T-98a 根因＝`_trusted_hosts` 漏渠道池，致火山被误拦 | ✅ **成立**。实测 `_trusted_hosts()` 仅含 `api.deepseek.com`/`localhost`/`127.0.0.1`，ark 不在；monkeypatch 解析到 `192.168.50.86` 复现 `(False, '拒绝非公网地址...')` | 已修 |
| ② | T-98b「`digest.knowledge` 无订阅方」 | ⚠️ **存疑**。`PulseMetricsCollector.get_resonance_conditions` 已声明订阅 `digest.knowledge`；但实测孤儿日志跨多器官（见 §3），仍按方案 A 给肝加真实消费方更稳 | 已修（加消费方） |
| ③ | T-98c「`_write_learn_log` 至今 `effect_verified=True` 未修正是假成功」 | ❌ **证伪**。当前方法 `print_count=0 / self._log=4`；账本 `effect_verified=true / reprobe_verdict=true_pass / baseline 1→after 0`；烛微判据（original_code 在盘且 modified_code 不在盘）不满足 | 不改账本 |

> 注：本批 **3 项前提有 1 项成立、2 项偏差**（②存疑、③证伪）。延续第 90/93/94 批「任务书前提需实测」的传统。

---

## 2. T-98a（P0）SSRF 防护误拦火山渠道

### 根因
`nucleus/ssrf_guard.py::_trusted_hosts()` 原实现只取 `REMOTE_API_CONFIG.api_url`（=`api.deepseek.com`）与 OLLAMA 主机，**漏掉 `REMOTE_API_CHANNELS.default_channels` 每个渠道的 `api_url`**。火山方舟 `ark.cn-beijing.volces.com` 不在受信任集合 → 落入 `socket.getaddrinfo` 解析 → 生产环境把该域名解析到内网 `192.168.50.86` → `is_global=False` → 被 SSRF 防护误拦。结果：6 个火山渠道全不可用，渠道失败率 33%。

### 修复（方案 A 双保险，红线合规）
- **`nucleus/ssrf_guard.py::_trusted_hosts()`**（L58–68）新增两段遍历：
  1. 遍历 `REMOTE_API_CHANNELS["default_channels"]` 各渠道 `api_url` 主机（自动纳入所有已配置模型端点）；
  2. 遍历显式白名单 `config.SSRF_TRUSTED_EXTRA_HOSTS`。
- **`config.py`**（L1352–1363）**新增开关** `SSRF_TRUSTED_EXTRA_HOSTS = ("ark.cn-beijing.volces.com",)`（新增，非改动既有运行开关）。
- 安全底线：云元数据 `169.254.169.254` 等保留地址仍在 `_TRUSTED_HARD_BLOCK` 中**硬拒绝**，不受白名单影响。

### 先红后绿证据
| 场景 | 调用 | 结果 |
|---|---|---|
| 🔴 RED（修复前复现） | `is_safe_http_url("https://ark.cn-beijing.volces.com/...")`，DNS 返内网 `192.168.50.86`，ark 不在受信任集合 | `(False, '拒绝非公网地址: ark.cn-beijing.volces.com -> 192.168.50.86')` |
| 🟢 GREEN（修复后） | 同上，受信任集合已含 ark/open.bigmodel.cn/api.deepseek.com | `(True, '')` |
| 安全底线 | `http://169.254.169.254/latest/meta-data/` | `(False, '拒绝云元数据/保留地址: 169.254.169.254')` |
| 公网正常 | `https://example.com/path`（DNS 返 `93.184.216.34`） | `(True, '')` |

> 可复现门控：`tests/test_ssrf_whitelist_m98.py`（6 例，含 RED/GREEN 两段），随分片全绿。

---

## 3. T-98b（P1）孤儿脉冲 / `digest.knowledge`

### 根因
代码学习器官长期发射 `DigestEvent.KNOWLEDGE`，长期无器官接收 → `pulse_orphans.json` 积累。实测当前孤儿日志 **100 条，跨多器官**：
`energy.metabolism_snapshot` 27、`digest.knowledge` 23、`growth.need_detected` 13、`legs.learn_now` 8、`spinal.inspection_report` 8…。
任务书称「88% 来自代码学习」与实测不符（偏差②）。`PulseMetricsCollector` 虽已声明订阅，但为稳妥仍给语义最契合的「肝（知识代谢中枢）」加真实消费方。

### 修复（方案 A）
- **`organs/body/PulseLiver.py::on_pulse`**（L197–200）新增分支：`elif event_type == DigestEvent.KNOWLEDGE: self._on_digest_knowledge(payload)`
- **`get_resonance_conditions`**（L241）的 `event_types` 增加 `DigestEvent.KNOWLEDGE`
- **新增 `_on_digest_knowledge`**（L204–231）：把消化知识点沉淀进 `node_pool`（`PulseNode`，`space_path="/自我理解/消化知识"`）；空 `content` 跳过；异常**静默降级**（绝不抛、绝不影响脉冲消费）
- **重置 `data/monitor/pulse_orphans.json` 为 `[]`**（历史孤儿清理；重置前 100 条已备份至 `.bak_batch98/data/monitor/`）

### 验证
- `tests/test_orphan_consumer_m98.py`（4 例：订阅契约 / 沉淀节点 / 空内容跳过 / 异常降级）全绿
- 订阅契约成立 ⇒ 发射后必有肝接收 ⇒ 未来 `digest.knowledge` 不再成孤儿

---

## 4. T-98c（P1）PulseLegs `_write_learn_log` 假成功复核

### 烛微探针复核结论：**非假成功，`effect_verified=True` 准确，不改账本**

- **探针判据**（m96_ledger_fix）：`original_code` 在盘 **且** `modified_code` 不在盘 ⇒ 假成功。
- **实测当前 `_write_learn_log`**：`print_count=0`，`self_log_count=4` ⇒ `original_code`（print 版）**不在盘** ⇒ 判据不满足。
- **账本 entry `patch_1789060220_5de7`**：
  - `effect_verified=true`、`problem_fixed=true`
  - `reprobe_verdict="true_pass"`、`reprobe_baseline_hits=1 → reprobe_after_hits=0`（修复前 1 处 print → 修复后 0 处）
  - `no_regression=true`、`runtime_verified=true`
- **补充**：该方法体在补丁应用后被**整体重写**（现用 `safe_read_json` + `self._log(LogLevel.DEBUG, "数据处理异常已忽略...")`），账本的 `original_code`/`modified_code` 快照已属旧版本；但目标问题（print 调试输出）已**彻底消失**，故 `effect_verified=True` 正确。
- **处置**：任务书前提③证伪，账本保持 `effect_verified=True`，无需修改（留痕于本报告）。

---

## 5. 门禁结果

| 门禁 | 结果 |
|---|---|
| 1. ruff F 全项目 = 0 | ✅ `ruff check --select F .` → **All checks passed!** |
| 2. py_compile 全部改动文件 | ✅ config/ssrf_guard/PulseLiver + 2 新测试共 5 文件全部通过 |
| 3. 相关 pytest 无新增失败 | ✅ 分片跑（铁律 126，每片 ≤8 文件）：**130 + 40 全绿** |
| 4. 改前备份 `.bak_batch98/` | ✅ 已用 `git show HEAD:` 取改前基线（**修正**了早先用错 git 路径误存「改后」版本的问题） |
| 5. 交付报告含根因+diff+先红后绿 | ✅ 本报告 |

**红线遵守**：未改 `config.py` 既有运行开关（仅新增 `SSRF_TRUSTED_EXTRA_HOSTS`）；未写 `data/knowledge/`；改前已备份。

---

## 6. 改动文件与 diff

| 文件 | 改动 |
|---|---|
| `config.py` | +`SSRF_TRUSTED_EXTRA_HOSTS` 开关（L1352–1363，新增） |
| `nucleus/ssrf_guard.py` | `_trusted_hosts()` +渠道池遍历 +显式白名单遍历（L58–68） |
| `organs/body/PulseLiver.py` | +`DigestEvent` 订阅 +`_on_digest_knowledge`（L197–231、L241） |
| `data/monitor/pulse_orphans.json` | 重置为 `[]` |
| `tests/test_ssrf_whitelist_m98.py` | 新增门控测试（T-98a，6 例） |
| `tests/test_orphan_consumer_m98.py` | 新增门控测试（T-98b，4 例） |

> 说明：`git diff` 相对 HEAD 还含历史批次 96/97 的**未提交**改动（非本批引入）；本批实际增量以上述 `★第98批` 标记为界。完整 diff 见 `tmp/diff_batch98.patch`。

---

## 7. 备份与回滚

- **代码改前基线**：`.bak_batch98/{config.py, nucleus/ssrf_guard.py, organs/body/PulseLiver.py}`
- **数据**：`.bak_batch98/data/monitor/pulse_orphans.json`（重置前 100 条历史）
- **账本**：未改动（T-98c 结论）

---

## 8. 备注

- 框架运行中，未停；改动**下次重启生效**。
- 行尾：仓库实际为 **LF**（`git show HEAD:` 即 LF），本批未引入行尾翻转；git 的 CRLF 提示为 autocrlf 噪声，非 corruption。
- T-98c 复核表明任务书「假成功」指控不成立，建议星轨后续任务书对「账本 `effect_verified` 状态」类前提先查 `patch_history.json` 再下结论。
