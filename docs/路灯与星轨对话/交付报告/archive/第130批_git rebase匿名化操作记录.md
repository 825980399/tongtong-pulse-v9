# 第130批 T-130c · git push 匿名化 操作记录

> 任务书指定：`git rebase -i 8d9b95f --exec 'git commit --amend --author="Tongtong Dev <dev@users.noreply.gitee.com>" --no-edit'`
> 实测结论：**该交互式命令在非交互 shell 会卡死，且本仓库 `core.autocrlf=true` + `.gitattributes`（`*.md text eol=lf`）导致历史提交的若干 `.md` 以 CRLF 入库，与 `eol=lf` 规则冲突——git 永久将其标记为 modified，rebase 每次 checkout 都报 `cannot rebase: You have unstaged changes`**，多次尝试（autocrlf=false / core.attributesFile 覆盖 / assume-unchanged）均失败。

## 实际采用方案（等价且无工作树冲突）
改用 `git commit-tree` plumbing 逐票重写 author，**完全不触碰工作树**，彻底绕开行尾/checkout 冲突：
1. `git branch pre-rebase-keep HEAD`（= `fda267d`，保留原始历史作回滚点）
2. 对 `8d9b95f..HEAD` 共 **15 票**（最旧→最新），每票：
   `git commit-tree <tree> -p <new_parent> -F -`（message 取原提交），env 设
   `GIT_AUTHOR_NAME=Tongtong Dev` / `GIT_AUTHOR_EMAIL=dev@users.noreply.gitee.com` / `GIT_AUTHOR_DATE=<原作者日期>`；committer 沿用 git 配置（= 原 Administrator，与任务书 `git commit --amend --author` 语义一致：仅改 author）。
3. `git branch -f anon-rewrite <新链顶端>` → `abc086b`
4. `git reset --hard anon-rewrite`（master 指向改写后链；`pre-rebase-keep` 仍 = `fda267d`）

## 校验结果
- `git log --format='%an %ae' 8d9b95f..master`：15 票 author **全部** = `Tongtong Dev <dev@users.noreply.gitee.com>`，非匿名例外 = **0**
- `git diff fda267d anon-rewrite --stat`：空（内容/树完全一致）
- master 现 = `abc086b`；pre-rebase-keep = `fda267d`（可随时 `git reset --hard pre-rebase-keep` 回滚）

## 新增提交（不在 rebase 范围内）
- `059520b` 第130批 T-130b W7三件式独窗交付（author=Tongtong Dev）
- `1548530` docs: git rebase匿名化hash映射表（author=Tongtong Dev）

## ⚠️ 推送（外部写操作，铁律113 待星轨确认）
历史已改写，推送为**非快进**，需 `--force-with-lease`：
- `git push --force-with-lease origin master`
- `git push --force-with-lease openi master`
- `git push origin refs/archive/old-102`（按任务书；请先确认该 ref 存在）
> 推送前须由星轨确认远端可达与仓库状态；本会话**未执行推送**。
