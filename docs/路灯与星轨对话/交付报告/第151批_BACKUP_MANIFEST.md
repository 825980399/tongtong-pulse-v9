# 第151批 备份清单（.bak_batch151/）

> Python os.walk 白名单精准备份，建在第一次改动之前；补丁脚本以 `rb` 读、检测 `had_crlf`、写回时按 `had_crlf` 还原 CRLF，确保行尾零翻转。

| 文件 | 字节 | 说明 |
| --- | --- | --- |
| `organs/brain/PulseInnerWorld.py` | 951199 | pre-CR 基线（三刀拆分前最新态，T151-3 施工前） |
| `organs/brain/PulseInnerWorld_post_cr.py` | 954422 | post-CR（T151-3 `_cognitive_reflection` 拆分后、VK 前） |
| `organs/brain/PulseInnerWorld_post_vk.py` | 956174 | post-VK（T151-4 `_validate_knowledge_consistency` 拆分后、EA 前基线） |

## 补丁脚本（外部搬运，不入库）

- `tmp/patch_cr.py`：T151-3 抽取 27 个 `_cr_*` helper（原 353 行 → 编排器 + 27 helper）。
- `tmp/patch_vk.py`：T151-4 抽取 13 个 `_vk_*` helper，emit 2 次 pri=3 守恒。
- `tmp/patch_ea.py`：T151-5 抽取 21 个 `_ea_*` helper（含 emotion 块二级切：dispatcher `_ea_emotion_style` + 4 子 helper `_ea_emotion_sad/joy/confused/expect`）。
- 上述脚本位于 `tmp/`，被 `.gitignore` 忽略，**不随本批提交**；属单点临时产物，本清单仅作存在性记录，保底副本在 `.bak_batch151/` 的三份全量快照内（可按字节还原到任意中间态）。

## CRLF 守卫指标（交付时实测）

- 目标文件 `organs/brain/PulseInnerWorld.py`：总行数 17954 / CRLF 17954 / loneLF 0 / CRCRLF 0 —— 行尾零翻转。
- 三份 `.bak_*` 快照与交付后工作树逐字节比对：CRLF 形态完全一致。

## 回滚路径

若需回退至某中间态：`cp .bak_batch151/<对应快照> organs/brain/PulseInnerWorld.py`，再 `git checkout <commit>` 复位 CI 基线（commit `3d2f186` 与 `9865e0f`，均本地、未 push）。
