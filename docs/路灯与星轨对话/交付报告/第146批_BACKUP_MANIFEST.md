# 第146批 BACKUP_MANIFEST（备份回执与缺口声明）

- 备份根目录：`.bak_batch146/`（git-ignored，第146批专用）
- 建立时机：**T0 预检之后、本批首次改动之前**（铁律131 / T-145 强化口径）
- 基线 HEAD：`bdc204c`
- 行尾判定：按 `rb` 二进制读（铁律139）

---

## 一、计划内备份清单（11 个目标文件）

| # | 文件 | 备份字节 | SHA1(前12) | 行尾 | CRCRLF | 本批 +/- 行 |
|---|---|---:|---|---|---:|---|
| 1 | `config.py` | 313755 | `f5f92a84312f` | CRLF | 0 | +19/-8 |
| 2 | `tools/package_full_project.py` | 6156 | `df6974dedc8b` | LF | 0 | +74/-39 |
| 3 | `tools/export_public.py` | 17572 | `ea2dc24023db` | LF | 0 | +68/-0 |
| 4 | `nucleus/reasoning/PatchManager.py` | 215284 | `493905188b3e` | CRLF | 0 | +5/-1 |
| 5 | `organs/core/PulseMetricsCollector.py` | 45938 | `523cef3ef862` | CRLF | 0 | +8/-4 |
| 6 | `nucleus/logger.py` | 34819 | `ea466866bdeb` | CRLF | 0 | +52/-3 |
| 7 | `organs/identity/PulseSelfAwareness.py` | 151898 | `ef8a1a7b2b41` | CRLF | 0 | +18/-0 |
| 8 | `docs/比赛准备/运行数据卡片_20260927.md` | 4025 | `17efee5f503a` | LF | 0 | +35/-17 |
| 9 | `docs/设计文档/暂缓考虑/远期数据存储备选方案_v1.0.md` | 8275 | `e8521dc20ade` | LF | 0 | +21/-0 |
| 10 | `docs/归档/设计文档/存储引擎长期子目标立项评估.md` | 5412 | `b9d6c56ec016` | LF | 0 | +21/-0 |
| 11 | `docs/设计文档/暂缓考虑/SERP数据清洗策略_v1.0.md` | 9628 | `1049677ff5f1` | CRLF | 0 | +21/-0 |

- 命中：**11 / 11**；缺失：**0**

---

## 二、⚠ 备份缺口（如实声明，不留粉饰）

以下 5 个文件是 **T146-1/3 实证渲染阶段**追加发现的泄漏点，属计划外改动，
**改前副本未被捕获**——初始备份清单（`tmp/dz146_backup.py`）没有覆盖到它们。
这是本批对铁律131 的执行缺口，不是「无需备份」的判断。

| # | 文件 | 改前副本 | 兜底回滚路径 | 本批 +/- 行 | 行尾 | 判定 |
|---|---|---|---|---:|---|---|
| 1 | `organs/brain/PulseInnerWorld.py` | ❌ 无 | `git show bdc204c:organs/brain/PulseInnerWorld.py` | +2/-2 | CRLF | 缺口已标注 |
| 2 | `organs/brain/pulse_inner_world_support.py` | ❌ 无 | `git show bdc204c:organs/brain/pulse_inner_world_support.py` | +6/-1 | CRLF | 缺口已标注 |
| 3 | `organs/brain/PulseReflection.py` | ❌ 无 | `git show bdc204c:organs/brain/PulseReflection.py` | +1/-1 | LF | 缺口已标注 |
| 4 | `organs/motor/PulseMouth.py` | ❌ 无 | `git show bdc204c:organs/motor/PulseMouth.py` | +1/-1 | LF | 缺口已标注 |
| 5 | `tools/ci/cw2_t2e_ci_gate_silent_except.py` | ❌ 无 | `git show bdc204c:tools/ci/cw2_t2e_ci_gate_silent_except.py` | +7/-5 | CRLF | 缺口已标注 |

**行尾未翻转的替代验证（第127批同款口径）**：

这些文件虽无改前磁盘副本，但可用「`git diff --numstat` 的改动局部性」证伪翻转——
一旦发生行尾翻转，diff 会退化为「整篇删除 + 整篇新增」，改动行数 ≈ 文件总行数。
实测占比均远低于阈值：

| 文件 | +行 | -行 | 总行 | diff 占比 | 判定 |
|---|---:|---:|---:|---:|---|
| `organs/brain/PulseInnerWorld.py` | 2 | 2 | 17657 | 0.0002 | OK(局部) |
| `organs/brain/pulse_inner_world_support.py` | 6 | 1 | 2078 | 0.0034 | OK(局部) |
| `organs/brain/PulseReflection.py` | 1 | 1 | 1013 | 0.0020 | OK(局部) |
| `organs/motor/PulseMouth.py` | 1 | 1 | 606 | 0.0033 | OK(局部) |
| `tools/ci/cw2_t2e_ci_gate_silent_except.py` | 7 | 5 | 269 | 0.0446 | OK(局部) |

> 口径提醒：仓库 `core.autocrlf=true`，`git show HEAD:<rel>` 返回的是**索引 blob**（已归一化为 LF），
> 而工作副本 checkout 后呈 CRLF。因此 **HEAD blob 不可直接用作磁盘行尾基线**，
> 否则会把「本来就是 CRLF 的工作副本」误判为翻转。

---

## 三、既有工作树污染的处理

- T0 预检发现 `organs/identity/PulseSelfAwareness.py` 处于 **既有 Dirty** 状态
  （第145批遗留的 4 行 `_rp` 渲染改动，未经 `/commit` 提交）。
- 处理方式：**先存档再还原** —— `tmp/dz146_pollution_145leftover.patch`（967 字节，LF）
- 随后 `git checkout --` 还原到 HEAD 纯净基线，并验证 `git apply --check` 可重放
  （证明该 patch 完整可读回，随时可恢复，不会出现「存档了但贴不回去」）。
- ⚠ 该 145 批遗留改动**未随本批提交**，仍处于未追踪状态；是否提交通知由星轨裁决。

---

## 四、本批未执行的删除动作（守卫生效记录）

- 重跑导出时尝试 `rm -rf tmp/release_check_146`（699 项），
  被 **safe-delete 守卫拦截**：`SAFE_DELETE_BULK_CONFIRM_REQUIRED`（count=699 > threshold=50）。
- 处理方式：**不绕过守卫**，改导出到全新目录 `tmp/release_check_146_final/`。
- 因此 `tmp/release_check_146/`（本批早期产物）与 `tmp/release_check_146_final/`（最终证据）并存，
  最终交付证据以 **`_final`** 为准，并在交付报告中标注。

