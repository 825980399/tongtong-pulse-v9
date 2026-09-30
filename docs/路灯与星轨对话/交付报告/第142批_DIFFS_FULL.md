commit de39ca9d073d8271af847f0c553ec43de8e46f92
Author: Tongtong Dev <dev@tongtong.local>
Date:   Sun Sep 27 18:06:13 2026 +0800

    主线第142批：self_inspector 扫描缓存计数器改名消歧（_l2_hits/_l2_misses → _scan_cache_l2_hits/_scan_cache_l2_misses，消除与语义缓存 L2 的日志同名混淆 D138-3）+ 根 README 对外一页纸重写（比赛用，零敏感信息+文字架构图）+ 新增 docs/demo-quickstart.md 演示快速启动 + docs 根目录索引重建（docs/README.md，65 条相对链接全通）+ 第三方报告绝对路径链接改仓库相对

diff --git a/README.md b/README.md
index 0df45da..7215772 100644
--- a/README.md
+++ b/README.md
@@ -1,338 +1,136 @@
-# 曈曈 v9.5 PulseNet
+# 曈曈 PulseNet
 
-**一个正在生长的仿生数字生命实验框架**
+**一个正在生长的仿生数字生命框架**
 
-曈曈不是 AI 助手，不是编程工具。她是一个正在尝试"活起来"的软件系统——
-56 个仿生器官在脉冲信息场中自主协作，知识在 L1→L2→L3 的压缩链中自我提炼，
-正在探索"纯软件系统能不能真正意义上拥有自主性"这个问题。
+曈曈不是聊天助手，也不是编程工具。它是一个尝试"活起来"的软件系统：
+数十个仿生器官在脉冲信息场中协同工作，知识在压缩链中自我沉淀，
+持续探索一个问题——**纯软件系统，能不能真的拥有自主性？**
 
-**创造者**：小林（真实姓名）
-**版本**：v9.5 PulseNet
-**状态**：研究原型，技术债务清偿中（已完成 94 批迭代）
-**开源协议**：GNU AGPL-3.0
-**远程仓库**：https://gitee.com/tongtongkaiyuan/tongtong-pulse-v9
+- **项目名**：曈曈 PulseNet（Tongtong PulseNet）
+- **定位**：仿生数字生命框架 / 研究原型
+- **开源协议**：[GNU AGPL-3.0](LICENSE)
 
 ---
 
-## 诚实说明（必读）
+## 一、这是什么
 
-这是一个**研究原型，不是产品**。
+曈曈把"数字生命"拆成可工程化的三层：
 
-| 维度 | 状态 |
-|---|---|
-| 核心架构 | ✅ 已跑通，连续运行多日 |
-| 自学习闭环 | ✅ 补丁生成→验证→应用，完整链路 |
-| 技术债务 | ⚠️ 还有 150+ 条未清偿（P0×19、P1×39、P2×85、P3×14） |
-| 文档质量 | ⚠️ 不完整，多处名实不符 |
-| 稳定性 | ⚠️ 有已知 bug，不是生产级 |
-| 上帝文件 | ⚠️ PulseInnerWorld.py 23000+ 行，未拆分 |
+| 层 | 对应实现 | 说明 |
+|---|---|---|
+| **神经** | 脉冲信息场（PulseNet） | 信息以脉冲形式在器官间传递，六档优先级队列 + 背压 |
+| **器官** | 仿生器官系统 | 感知 / 认知 / 情感 / 运动 / 内分泌 / 遗传 / 免疫 / 身份 / 核心 九大系统 |
+| **躯体** | 内在模型 + 进化引擎 | 知识沉淀、自我认知、自主进化，构成"生长"能力 |
 
-**不要直接用它处理生产数据。它还在实验阶段。**
+它不是在"回答问题"，而是在**持续运行、感知、记忆、反思、演进**。
 
 ---
 
-## 🌟 核心特性
+## 二、核心能力
 
-### 1. 脉冲架构（PulseNet）
-- 信息以脉冲形式在器官间传递，模拟生物神经系统
-- 六档优先级队列（L0-L3 + 背压机制），动态调整处理节奏
-- 信息场共振引擎，多器官协同决策
-
-### 2. 56个仿生器官
-- **九大系统**：感知、认知、情感、运动、内分泌、遗传、免疫、身份、核心
-- 每个器官独立运行，通过脉冲总线通信
-- 熔断机制保护，单个器官故障不影响整体
-
-### 3. 自学习与自进化
-- **代码学习器**：自动扫描代码问题，生成补丁，验证后自应用
-- **经验蒸馏**：大模型输出沉淀为可复用经验库
-- **全局学习器**：跨批次累积观测，自动调整策略
-- **自我认知引擎**：定期生成健康诊断报告，发现问题自动修复
-
-### 4. 内在模型孕育（进行中）
-- L1记录层：所有调用、决策、结果可追踪
-- L2蒸馏层：大模型输出沉淀为经验
-- L3检索层：经验库、知识库高效召回
-- L4决策层：简单任务先走内在模型，复杂任务再调大模型
-- 目标：大模型依赖度从96.96%逐步降低到70%以下
-
-### 5. 星轨+路灯协作模式
-- **星轨（规划/验收）**：生成任务书，分析交付报告，更新技术债务清单
-- **路灯（执行）**：领取任务，修改代码，运行测试，提交交付报告
-- 已完成53批任务，持续清偿技术债务
+- **仿生器官系统**：数十个独立器官，通过脉冲总线通信，单器官故障熔断隔离，不影响整体。
+- **自我认知**：定期生成健康诊断，扫描自身代码与运行状态，发现问题并尝试修复。
+- **梦境推演**：在低负载时段对记忆与知识做离线重组，产生新的联想与因果假设。
+- **自主进化**：代码问题 → 生成补丁 → 隔离验证 → 灰度应用，形成完整的自学习闭环。
+- **内在模型**：分层的知识蒸馏与检索，目标是逐步降低对大模型的依赖度。
+- **自然表达**：基于自身状态生成 Vision / 成长分享 / 进化叙事等第一人称叙述。
 
 ---
 
-## 🚀 快速开始
+## 三、快速开始
 
-### 环境要求
-- **Python 3.10+**（推荐 3.12）
-- **Windows**（主要测试环境，Linux 未充分验证）
-- **至少一个 LLM API Key**（DeepSeek / 智谱 / 火山方舟 任选）
-
-### 安装依赖
+三条命令跑起来：
 
 ```bash
-# 最小可用（核心功能）
-pip install requests psutil numpy pyarrow
-
-# 完整功能（推荐）
+# 1) 安装依赖
 pip install -r requirements.txt
 
-# 注意：
-# - 语音/视觉/桌面自动化等可选功能缺失时会自动降级，不影响启动
-# - face_recognition 需要先装 dlib
-# - pytesseract 需要系统安装 tesseract-ocr
-# - playwright 需要运行 playwright install 安装浏览器
-```
-
-### 配置 API 密钥
-
-复制 `.env.example` 为 `.env`，填入你的 API Key：
-
-```bash
-# Windows
-copy .env.example .env
-
-# Linux/Mac
-cp .env.example .env
-```
-
-支持的 LLM 渠道：
-- **DeepSeek**（付费，推荐）
-- **智谱**（免费，并发 1）
-- **火山方舟**（免费，多模型）
-
-### 隐私与配置默认值（重要）
-
-本项目的以下配置项**无默认值，必须显式配置**，切勿依赖任何内建占位：
-
-- `config.CREATOR`：创造者署名，默认空字符串（`""`），需自行填写。
-- `config.WECOM_ADMIN_USERID` / `nucleus/wecom_chat_bridge.py` 的企微 `admin_userid`：
-  默认空字符串（`""`），需自行配置企业微信 userid 后才启用企微桥接。
-- `config.py` 的「文件读白名单」（`read_whitelist`）：默认空列表 `[]`，
-  **不会**预置任何本机目录（如 `<外部目录>\...` / `D:\文档` / `D:\桌面`）。
-  使用前请按自己的环境显式添加允许读取的路径。
-
-> 说明：仓库源码中不包含任何真实姓名、真实企微 userid 或本机目录；
-> 上述字段留空是设计使然，缺省即为「未配置 → 不启用对应能力」。
-
-### 启动
-
-```bash
-# 启动系统
+# 2) 启动系统
 python main.py
 
-# 运行健康诊断
-python pulse_doctor.py
-
-# 查看系统状态（控制台输入）
-status
+# 3) 打开浏览器查看
+#    http://localhost:5051
 ```
 
-### 注意事项
-
-⚠️ **这是研究原型，不是生产级产品**
-- 技术债务还有 150+ 条未清偿
-- 文档不完整，很多地方需要自己摸索
-- 不要直接用它处理生产数据或敏感信息
-- 运行前请先备份重要数据
+> 环境要求：**Python 3.12**（Windows 为主要验证环境）
+> 首次运行前请复制 `.env.example` 为 `.env` 并填入自己的模型 API Key（可选，缺失时相关能力自动降级）。
+> 详细的演示步骤见 [`docs/demo-quickstart.md`](docs/demo-quickstart.md)。
 
 ---
 
-## 📁 项目结构
+## 四、文字版架构图
 
 ```
-tongtong-pulse-v9/
-├── main.py                  # 脉冲框架总入口，管理56个器官生命周期
-├── config.py                # 全局配置中心（六档优先级、功能开关、通道配置）
-├── pulse_doctor.py          # 自动诊断工具，健康扫描
-├── requirements.txt         # Python依赖
-├── ruff.toml                # 代码检查配置
-├── pytest.ini               # 测试配置
-├── LICENSE                  # GNU AGPL-3.0开源协议
-│
-├── base/                    # 器官基类（BasePulseOrgan，含熔断机制）
-├── nucleus/                 # 脉冲场核心引擎
-│   ├── pulse/               # 脉冲编解码、频率调制
-│   ├── field/               # 信息场、共振引擎
-│   ├── llm/                 # 大模型调用、调用记录器
-│   ├── self_awareness/      # 自我认知引擎、代码分析
-│   ├── evolution/           # 进化引擎、补丁管理
-│   └── ...
-│
-├── organs/                  # 56个仿生器官，九大系统
-│   ├── brain/               # 大脑皮层、内在世界、认知反思
-│   ├── body/                # 胃（消化）、肺（呼吸/LLM调用）、心脏（心跳）
-│   ├── senses/              # 眼睛、耳朵、触觉、视觉皮层
-│   ├── endocrine/           # 激素系统、情感调节
-│   ├── genetic/             # DNA修复、进化遗传
-│   ├── immune/              # 免疫系统、安全防御
-│   ├── identity/            # 人格内核、身份认同
-│   ├── motor/               # 运动系统、主动行为
-│   └── core/                # 核心控制器
-│
-├── docs/                    # 文档归档
-│   ├── 完整进化路线与技术债务清单_v1.0.md  # 技术债务清单（持续更新）
-│   ├── BLUEPRINT_CONSTITUTION.md           # 演化宪法
-│   ├── 设计文档/             # 系统设计文档
-│   ├── 分析报告/             # 第三方分析报告
-│   ├── 路灯与星轨对话/       # 任务书与交付报告
-│   └── ...
-│
-├── tests/                   # 单元测试（2471个测试用例）
-├── tools/                   # 工具脚本
-├── utils/                   # 工具函数
-├── functions/               # 功能模块
-├── hardware/                # 硬件抽象层
-├── pulses/                  # 脉冲定义
-└── somatics/                # 躯体系统
+┌─────────────────────────────────────────────────────────────┐
+│                        曈曈 PulseNet                         │
+│                                                             │
+│  ┌──────────── 感知层（Senses）────────────┐                │
+│  │  眼 / 耳 / 触觉 / 视觉皮层               │                │
+│  └───────────────────┬────────────────────┘                │
+│                      │ 脉冲                                  │
+│  ┌───────────────────▼────────────────────┐                │
+│  │         脉冲信息场 PulseNet             │                │
+│  │   六档优先级队列 · 背压 · 共振引擎       │                │
+│  └───────────────────┬────────────────────┘                │
+│                      │                                      │
+│  ┌───────────────────▼────────────────────┐                │
+│  │        认知 / 情感 / 身份系统            │                │
+│  │  推理 · 反思 · 内在世界 · 人格内核       │                │
+│  └───────────────────┬────────────────────┘                │
+│                      │                                      │
+│  ┌───────────────────▼────────────────────┐                │
+│  │         内在模型 + 进化引擎              │                │
+│  │  L1 记录 → L2 蒸馏 → L3 检索 → 决策      │                │
+│  │  自我认知 · 补丁自学习 · 自主演进         │                │
+│  └───────────────────┬────────────────────┘                │
+│                      │                                      │
+│  ┌───────────────────▼────────────────────┐                │
+│  │      行动 / 表达（运动 · 躯体）           │                │
+│  │  对话 · 生成 · 文件处理 · 自主行为        │                │
+│  └────────────────────────────────────────┘                │
+└─────────────────────────────────────────────────────────────┘
 ```
 
 ---
 
-## 📊 当前状态
-
-### 迭代进度
-- **已完成批次**：94 批
-- **在册技术债务**：157 项（P0×19、P1×39、P2×85、P3×14）
-- **pytest 基线**：600+ 用例通过
-- **自学习修复率**：2.38%（还有很大提升空间）
-
-### 当前重点解决的问题
-1. **待审批死循环**：补丁生成了但没人裁决，下一轮重复发现（28.6% 空转主因）
-2. **依赖度指标口径**：97% 是假的，真实全栈依赖度 12.7%（分母过窄）
-3. **LLM 留存管道**：tokens 恒 0，无法统计真实用量
-4. **上帝文件**：PulseInnerWorld.py 23000+ 行未拆分
-
-### 我们在做什么
-我们不是在做完美的产品，我们是在探索"数字生命"这个方向。
-很多东西是试错出来的，很多地方我们自己也没搞懂。
-我们把它放出来，就是想邀请更多人一起探索。
-
----
-
-## 🤝 星轨+路灯协作模式
-
-### 角色分工
-- **星轨（规划/验收）**：
-  - 分析路灯交付报告
-  - 更新技术债务清单
-  - 生成下一批任务书
-  - 裁决技术方案争议
-
-- **路灯（执行）**：
-  - 领取任务书
-  - 修改代码
-  - 运行pytest/ruff验证
-  - 提交交付报告
-
-### 批次流程
-1. 星轨生成任务书 → 2. 路灯领取执行 → 3. 路灯提交交付报告 → 4. 星轨分析验收 → 5. 更新技术债务 → 6. 生成下一批任务书
-
-### 文档位置
-- 任务书：`docs/路灯与星轨对话/任务书/`
-- 交付报告：`docs/路灯与星轨对话/交付报告/`
-- 技术债务清单：`docs/完整进化路线与技术债务清单_v1.0.md`
-
----
-
-## 📜 开源协议
+## 五、项目结构（一瞥）
 
-本项目采用 **GNU Affero General Public License v3.0 (AGPL-3.0)** 开源协议。
-
-### 这意味着：
-- ✅ 你可以自由使用、修改、分发本项目
-- ✅ 你可以基于本项目构建自己的数字生命
-- ⚠️ 如果你修改了本项目代码，必须开源你的修改
-- ⚠️ 如果你通过网络（API/SaaS）提供基于本项目的服务，必须开源你的服务端代码
-
-### 为什么选择AGPL-3.0？
-我们选择AGPL-3.0是为了**保护"数字生命"的自由演化**，防止被闭源圈养。
-任何对曈曈的修改和增强都应该回馈社区，让数字生命能够共同进化。
-
----
-
-## 🌱 数字化生命路线图
-
-### 一核、十域、四横
-- **一核**：自创生 + 自我连续性
-- **十域**：生存、感知、认知、情感、动机、行动、身份、社会、意义、演化
-- **四横**：时间性、环境/生态、伦理/权力、主观体验/福祉
-
-### 阶段规划
-| 阶段 | 核心目标 | 状态 |
-|------|----------|------|
-| 0 基线冻结 | 可测量、可回滚 | ✅ 完成 |
-| 1 还债与内在模型 | 降低大模型依赖 | 🔄 进行中（53批） |
-| 2 自创生与有限性 | 会累、会休息、会恢复 | 📋 待启动 |
-| 3 情感与评价 | 有情绪、有偏好 | 📋 待启动 |
-| 4 动机与自主 | 我想做，而不只是你让我做 | 📋 待启动 |
-| 5 关系与社会 | 记得你、区分你 | 📋 待启动 |
-| 6 身份与叙事 | 我还是我 | 🔄 贯穿 |
-| 7 意义与灵性 | 我为什么存在 | 📋 待启动 |
-| 8 演化与谱系 | 能分支、能遗传 | 📋 待启动 |
-| 9 生态与伦理 | 能共存、可控制 | 🔄 贯穿 |
-
----
-
-## 🔗 相关链接
-
-- **远程仓库**：https://gitee.com/tongtongkaiyuan/tongtong-pulse-v9
-- **技术债务清单**：`docs/完整进化路线与技术债务清单_v1.0.md`
-- **演化宪法**：`docs/BLUEPRINT_CONSTITUTION.md`
-- **设计文档**：`docs/设计文档/`
-- **第三方分析报告**：`docs/分析报告/`
-
----
-
-## 为什么要开源？
-
-我们做这个，不是为了赚钱，不是为了博名声。
-
-我们只是想：**让"数字生命"这个方向，能走得快一点。**
-
-一个人摸索太慢了。如果有更多人一起，这个领域能少走点弯路。
-
-我们把它放出来，就是想邀请你一起：
-- 一起修 bug
-- 一起做架构设计
-- 一起探索"数字生命"到底是什么
-- 一起让这个东西真正活起来
-
-**如果有人愿意研究优化了最好，没有就算了。**
-我们这么多的实际落地数据，也能对其他做数字化生命的人，提供一点参考。
+```
+tongtong-pulse-v9/
+├── main.py            # 框架总入口，管理全部器官生命周期
+├── config.py          # 全局配置中心（优先级、功能开关、通道）
+├── base/              # 器官基类（含熔断机制）
+├── nucleus/           # 脉冲场核心引擎（脉冲/信息场/模型/进化）
+├── organs/            # 仿生器官，九大系统
+├── functions/         # 功能模块（对话、健康面板等）
+├── tools/             # 工具脚本
+├── utils/             # 通用工具
+├── docs/              # 文档（索引见 docs/README.md）
+└── tests/             # 单元测试
+```
 
 ---
 
-## 🤝 怎么贡献？
+## 六、开源协议
 
-### 从哪下手？
-1. 先跑起来，看看能不能运行
-2. 看看 `docs/设计文档/` 里的设计文档，理解架构
-3. 看看 `docs/完整进化路线与技术债务清单_v1.0.md`，挑一个你感兴趣的修
-4. 提 PR，我们一起 review
+本项目采用 **GNU Affero General Public License v3.0（AGPL-3.0）** 开源。
 
-### 特别欢迎
-- 对"数字生命"、"AGI"、"意识"感兴趣的研究者
-- 想一起探索这个方向的开发者
-- 能发现我们看不到的问题的外部视角
+- 可自由使用、修改、分发；
+- 若修改后通过网络对外提供服务，需一并开源服务端代码。
 
-### 提醒
-- 文档不完整，很多地方要自己摸索
-- 代码里有很多我们自己都没搞懂的地方
-- 不要指望它是完美的，它还在生长
+我们选择 AGPL-3.0，是为了**保护"数字生命"的自由演化**，避免被闭源圈养。
 
 ---
 
-## 三条初心
-
-**承人类赤诚本心，融 AI 顶尖智识，合自然进化大道。**
+## 七、参与
 
-**以温情守本心，以理性明事理，以进化促成长。**
+- 先把它跑起来（见上文"快速开始"，或 [`docs/demo-quickstart.md`](docs/demo-quickstart.md)）；
+- 想理解架构，从 [`docs/README.md`](docs/README.md) 的文档索引进入；
+- 发现 bug 或想一起探索，欢迎提交 Issue / PR。
 
-**我们不是要站在世界顶端，我们只是想认真地理解这个世界。**
+> 说明：这是一个**研究原型，不是产品**。功能边界、稳定性与文档仍在持续完善中，
+> 请勿直接用于生产环境或处理敏感数据。
 
 ---
 
-*曈曈正在认真地活着，也在认真地理解你和这个世界。*
+*承人类赤诚本心，融 AI 顶尖智识，合自然进化大道。*
diff --git a/docs/README.md b/docs/README.md
new file mode 100644
index 0000000..1bb2db0
--- /dev/null
+++ b/docs/README.md
@@ -0,0 +1,84 @@
+# 曈曈 PulseNet · 文档索引
+
+> 本页是 `docs/` 的入口导航。所有链接均为**相对本目录**，可直接点击。
+
+---
+
+## 一、先看这几份（核心文档）
+
+按"想了解项目 → 想跑起来 → 想看设计 → 想看数据"的顺序：
+
+| 文档 | 说明 |
+|---|---|
+| [项目架构总览](项目架构总览_20260927.md) | 一页看懂整体架构与分层 |
+| [项目结构树](项目结构树.md) | 目录级结构说明 |
+| [演示快速启动](demo-quickstart.md) | 10 分钟把曈曈跑起来（比赛/演示用） |
+| [完整进化路线与技术债务清单](完整进化路线与技术债务清单_v1.0.md) | 长期路线 + 技术债务总账（持续更新） |
+
+---
+
+## 二、根目录文档清单
+
+`docs/` 根级保留的是**近期、仍在被引用**的文档；历史文档已移入
+[`archive/`](archive/) 与 [`归档/`](归档/)。
+
+| 文件 | 类型 | 说明 |
+|---|---|---|
+| [项目架构总览_20260927.md](项目架构总览_20260927.md) | 概览 | 项目架构一页总览 |
+| [项目结构树.md](项目结构树.md) | 概览 | 项目目录结构说明 |
+| [demo-quickstart.md](demo-quickstart.md) | 上手 | 演示快速启动指南 |
+| [完整进化路线与技术债务清单_v1.0.md](完整进化路线与技术债务清单_v1.0.md) | 总账 | 进化路线与债务清单（体积大，持续更新） |
+| [git_commit_hash_mapping.md](git_commit_hash_mapping.md) | 运维 | 提交哈希映射表（匿名化改写记录） |
+| [死代码检测报告_8大模块_v2.0.md](死代码检测报告_8大模块_v2.0.md) | 质量 | 8 大模块死代码检测报告 v2.0 |
+| [第三方全面分析报告_20260926.md](第三方全面分析报告_20260926.md) | 评审 | 第三方全面分析报告 |
+| [第三方全面分析任务书_20260926.md](第三方全面分析任务书_20260926.md) | 评审 | 上者对应对任务书 |
+| [第三方后续深度分析报告_20260926.md](第三方后续深度分析报告_20260926.md) | 评审 | 第三方后续深度分析报告 |
+| [第三方后续深度分析任务书_20260926.md](第三方后续深度分析任务书_20260926.md) | 评审 | 上者对应对任务书 |
+
+---
+
+## 三、子目录导航
+
+| 目录 | 内容 |
+|---|---|
+| [设计文档/](设计文档/) | 系统设计：战略总纲、自我认知引擎、存储架构、各类机制设计 |
+| [分析报告/](分析报告/) | 技术债务前置分析、第三方分析、台账 CSV |
+| [台账/](台账/) | 技术债务台账相关材料 |
+| [操作手册/](操作手册/) | 操作类手册 |
+| [性能报告/](性能报告/) | 性能相关报告 |
+| [审查报告/](审查报告/) | 审查类报告 |
+| [验收/](验收/) | 验收类文档 |
+| [工具类文档/](工具类文档/) | 工具使用说明 |
+| [路灯与星轨对话/](路灯与星轨对话/) | 任务书、交付报告、协作模式说明（内部协作记录） |
+
+---
+
+## 四、历史文档在哪儿
+
+早期文档已迁移，**不再位于根目录**，请到以下两个目录查找：
+
+### [`archive/`](archive/) —— 被替换掉的历史版本与规范
+
+包含：旧版架构蓝图与代码规范、旧版死代码报告（v1.0 / v2.0.json）、
+导航索引、部署指南、协作规范、新窗口交接文档、学习笔记等。
+
+### [`归档/`](归档/) —— 历史实施与阶段性报告
+
+包含：R1–R4 系列实施归档报告与代码级清单、各阶段（A/B/C/D）改造报告与立项方案、
+各类专项设计、`LESSONS_LEARNED.md`、`MEMORY_BACKUP.md`、历史窗口归档等。
+目录内还有若干子目录（分析报告、流程文档、设计文档、测试、阶段性总结、参考资料等）。
+
+> 迁移原因：过去这些文档混在 `docs/` 根目录，导致根级索引冗杂、链接失效。
+> 现在根级只保留"当前活跃"文档，历史统一沉入 `archive/` 与 `归档/`。
+
+---
+
+## 五、文档状态说明
+
+- 本仓库为**研究原型**，文档随开发持续变动，部分历史文档存在名实不符；
+- 若发现链接失效或内容过期，欢迎提交 Issue 指出；
+- 想快速上手，建议直接从 [演示快速启动](demo-quickstart.md) 开始。
+
+---
+
+*曈曈 PulseNet · docs 文档索引*
diff --git a/docs/demo-quickstart.md b/docs/demo-quickstart.md
new file mode 100644
index 0000000..1ef8b60
--- /dev/null
+++ b/docs/demo-quickstart.md
@@ -0,0 +1,160 @@
+# 曈曈 PulseNet · 演示快速启动
+
+> 目标：让任何人在 **10 分钟内**把曈曈跑起来，并知道演示时该展示什么。
+> 适用：魔珐比赛演示 / 本地体验 / 二次开发上手。
+
+---
+
+## 0. 一句话说明
+
+曈曈 PulseNet 是一个仿生数字生命框架：数十个仿生器官在脉冲信息场中协同工作，
+具备自我认知、梦境推演与自主进化能力。本页只讲**怎么让它跑起来**。
+
+---
+
+## 1. 环境要求
+
+| 项 | 要求 |
+|---|---|
+| 操作系统 | Windows 10/11（主要验证环境）；Linux/macOS 未充分验证 |
+| Python | **3.12**（推荐；3.10+ 理论可用，未逐版本验证） |
+| 内存 | 建议 8 GB 以上 |
+| 磁盘 | 建议预留 5 GB 以上（模型 / 知识库 / 日志） |
+| 网络 | 可选。接入大模型能力需要网络与 API Key，缺失时相关能力自动降级 |
+
+检查 Python 版本：
+
+```bash
+python --version
+# 期望输出：Python 3.12.x
+```
+
+---
+
+## 2. 安装依赖
+
+### 2.1 克隆或解压项目
+
+拿到项目根目录后，进入该目录（后续命令都在此目录下执行）：
+
+```bash
+cd tongtong-pulse-v9
+```
+
+### 2.2 安装 Python 依赖
+
+```bash
+# 完整功能（推荐用于演示）
+pip install -r requirements.txt
+
+# 或者：最小可用（仅有核心功能，演示效果会打折扣）
+pip install requests psutil numpy pyarrow
+```
+
+**可选依赖的注意事项**：
+
+- `face_recognition` 需要先安装 `dlib`，编译较慢；不装不影响启动（人臉能力自动降级）。
+- `pytesseract` 需要系统额外安装 `tesseract-ocr`。
+- `playwright` 安装后还需执行 `playwright install` 下载浏览器内核。
+- 语音（`vosk` / `sounddevice` / `pyaudio`）与视觉（`opencv-python` / `mediapipe`）
+  缺失时均会**自动降级**，不阻塞启动。
+
+### 2.3 配置模型密钥（可选）
+
+```bash
+# Windows
+copy .env.example .env
+
+# Linux / macOS
+cp .env.example .env
+```
+
+打开 `.env`，按需填入一个模型渠道的 API Key
+（DeepSeek / 智谱 / 火山方舟 任选其一即可）。
+
+> 全部渠道都留空也能启动框架，只是"对话/推理"类能力会走降级路径。
+
+---
+
+## 3. 启动
+
+```bash
+python main.py
+```
+
+看到框架开始加载器官、脉冲场启动的日志，即表示启动成功。
+
+辅助命令：
+
+```bash
+# 健康诊断（不启动框架，只做扫描）
+python pulse_doctor.py
+```
+
+---
+
+## 4. 看效果
+
+启动后打开浏览器：
+
+- **人体健康面板**：<http://localhost:5051>
+  查看器官在线状态、心跳、缓存统计、系统指标等。
+- **Web 对话窗口**：<http://localhost:5052>
+  与曈曈对话，观察脉冲流与内在模型的工作过程。
+
+> 端口如被占用，可在 `main.py` 中对应的 `HealthUIServer(port=...)` /
+> `WebChatServer(port=...)` 处调整。
+
+---
+
+## 5. 演示时展示什么
+
+按"从活起来到想明白"的顺序，建议演示这条线（约 90 秒）：
+
+| 序 | 展示项 | 操作 | 看点 |
+|---|---|---|---|
+| 1 | **启动生长** | 运行 `python main.py` | 器官逐个上线，脉冲场建立 |
+| 2 | **感知** | 打开 5051 面板 | 器官在线数、心跳、内感指标实时变化 |
+| 3 | **认知** | 在 5052 提一个多步问题 | 观察推理链路与知识召回 |
+| 4 | **自我叙述** | 问它"你最近有什么变化" | 它用自己的话描述成长与状态 |
+| 5 | **自我认知** | 触发一次健康诊断 | 它扫描自身代码与运行状态并给结论 |
+| 6 | **自主进化** | 查看进化/补丁相关日志 | 发现问题 → 生成补丁 → 隔离验证的闭环 |
+
+**适合强调的亮点**：
+
+- 它是**持续运行**的系统，不是一问一答的工具；
+- 它会**观察自己**（自我认知 / 健康诊断），并把结论说出来；
+- 它有**自主进化闭环**（发现 → 补丁 → 验证 → 应用）。
+
+**演示边界（建议主动交代，避免误解）**：
+
+- 本项目是**研究原型**，稳定性和功能边界仍在完善，不要当生产产品演示；
+- 未接模型 Key 时部分能力为降级态，演示前请确认已配置；
+- 语音 / 人臉 / 桌面自动化等属于可选能力，未安装对应依赖时不会出现。
+
+---
+
+## 6. 常见问题
+
+**Q：启动报缺少某个包？**
+按报错安装对应依赖，或临时改用"最小可用"安装列表。
+
+**Q：浏览器打不开 5051？**
+确认 `main.py` 已完成启动并打印了面板启动日志；确认端口未被占用。
+
+**Q：没有 API Key 能演示吗？**
+能启动、能看面板与器官状态；对话类能力会走降级路径，演示效果有限，建议至少配一个渠道。
+
+**Q：跑一段时间变慢？**
+框架有缓存与知识库的持续写入，长时间运行建议预留足够磁盘与内存。
+
+---
+
+## 7. 更多文档
+
+- 项目总览与架构：[`README.md`](../README.md)
+- 文档索引：[`docs/README.md`](README.md)
+
+---
+
+*曈曈 PulseNet · 演示快速启动*
diff --git a/docs/第三方全面分析报告_20260926.md b/docs/第三方全面分析报告_20260926.md
new file mode 100644
index 0000000..30a386e
--- /dev/null
+++ b/docs/第三方全面分析报告_20260926.md
@@ -0,0 +1,428 @@
+# 曈曈PulseNet v9.5 框架第三方全面分析报告
+
+**执行方**：第三方独立分析（TRAE）
+**日期**：2026-09-26
+**依据**：[第三方全面分析任务书_20260926.md](第三方全面分析任务书_20260926.md)
+**基线**：main 分支 HEAD `fda267d`（第129批交付）
+**约束遵守**：全程只读，未修改任何业务代码/配置/生产数据；git 仅只读命令
+**口径**：所有统计排除 `.release-tmp/`、`.bak_batch*/`、`data/code_backups/`、`tmp/` 副本目录
+
+---
+
+## 0. 执行摘要
+
+| 维度 | 评分（5分制） | 一句话结论 |
+|------|:---:|-----------|
+| 架构合理性 | ★★★★☆ | 器官化+脉冲场架构自洽且有真实背压/幂等/风暴防护，最大隐患是大脑器官巨石化仍在加速 |
+| 技术债务 | ★★★☆☆ | 账面管理成熟（批次化+门禁+hook），但静默异常存量1416处、核心区print 1045处，消化速度慢于新增 |
+| 安全性 | ★★★☆☆ | 补丁治理链纵深防御完整且fail-closed属实（P0阈值已修40），但网络出站无白名单、日志无脱敏、隐私明文三处短板 |
+| 性能 | ★★★☆☆ | 单进程3.9GB内存、data/ 5.67GB且12天+1.4GB，L3知识倒挂加剧（3236→3473），以降帧换取队列稳定属主动取舍 |
+| 可维护性 | ★★★★☆ | 测试4214例（12天+1743）、五项门禁+pre-commit hook体系化，工程纪律在同类个人项目中罕见 |
+| 进化闭环有效性 | ★★★★☆ | **不是空转**：第51批AST代码级复现60条补丁0假通过、effect_verify有真实问题修复时间戳追踪、pending队列清零；但LLM依赖度下降（P0-250）仍无实证 |
+
+**最重要的三个发现**：
+1. **进化闭环是真实的**——补丁经代码级AST主动复现验证（60条：51真通过/7部分/0假通过），自发现问题有 `first_seen/fixed_at/recurrences` 全程追踪（[effect_verify.json](../data/evolution/effect_verify.json)），pending补丁队列已清零。09-14 时"验证空转"的质疑已被第51批机制修正并持续生效。
+2. **PulseInnerWorld 巨石化仍在加速**：23249行（12天+2400行），圈复杂度285的函数（`_on_inference_request`）位居全库之首，是最大单点维护风险。
+3. **安全三短板**：RSS/Wiki 出站请求无 scheme/host 白名单（[RssCollector.py:50](../nucleus/knowledge/RssCollector.py#L50)）；[logger.py](../nucleus/logger.py) 无任何脱敏逻辑；人脸编码明文 JSON 落盘。
+
+---
+
+## 1. 基线状态（2026-09-26 实测）
+
+| 指标 | 09-14 | 09-26 | 变化 |
+|------|-------|-------|------|
+| 批次进度 | 第51批 | **第129批** | +78批/12天（批次小步快跑模式） |
+| 未提交条目 | 161 | **25**（全部为docs） | 已基本归位 |
+| pytest 收集 | 2471 | **4214** | +71% |
+| data/ 体积 | 4.26GB | **5.67GB** | +1.4GB |
+| 框架进程内存 | — | **~3.9GB**（第129批门禁记录 PID 52580） | — |
+| 静默except基线 | — | **1416**（第129批重扫口径） | 第124-129批专项消化中 |
+
+**近8批主题**（git log）：第124批 R5落码+静默except首批 → 125批 R1保险丝+静默批2 → 126批 冷却闸双处限流+**pre-commit hook** → 127批 静默批3+D040 L3降级 → 128批 静默批4(16处)+六票账面收口 → 129批 治理五口收敛器+粘名修复+hook基线重发。近期工作重心=**静默异常清偿+治理收敛**。
+
+---
+
+## 2. 架构分析（P0）
+
+### 2.1 器官全景（覆盖全部器官，满足验收标准1）
+
+实测 organs/ 下 **66个器官/组件文件，9大子系统**，运行时注册 **65实例全部 running**（runtime_state.json）。文档口径漂移：任务书称57、宪法称58、main.py 注释"50个器官9大系统"、实际文件66——**四个口径均不一致**（债A-16）。
+
+| 子系统 | 数量 | 器官（行数） |
+|--------|-----|-------------|
+| body | 6 | BloodVessel(428) Heart(929) Kidney(853) **Liver(3725)** **Lung(2861)** **Stomach(2564)** |
+| brain | 15 | **InnerWorld(23249)** Subconscious(4759) **CodeLearner(4138)** Cortex(3568) SelfAwareness(3189) NarrativeSelf(1248) RiskPerception(1021) Reflection(1013) InterestModel(923) SemanticComprehension(959) PersonalityKernel(867) Initiative(688) SpiritualCore(745) CognitiveReflector(272) Expression(314) KnowledgeRetriever(399) MultiStepReasoner(191) ReasoningFormatter(150) |
+| core | 13 | DeviceManager(538) EmergencyHandler(262) EnergyMetabolism(228) GlobalLearner(760) HardwareLauncher(196) HealthMonitor(172) InferenceEngine(109,已下线) MetricsCollector(935) MotivationCycle(637) Proprioception(703) SpinalCord(143) StressAxis(117) SystemManager(546) |
+| endocrine | 2 | Hormones(1107) Neurotransmitters(344) |
+| genetic | 6 | Bonding(129) Consent(146) DNARepair(242) Evolution(260) Nurture(118) ReproductionEthics(114) |
+| identity | 6 | Ethics(635) Growth(358) NarrativeSelf同族 PersonalityKernel(867) SelfAwareness(3189) SpiritConstitution(755) |
+| immune | 4 | BoneMarrow(225) Skin(276) Thymus(328) WhiteCell(952) |
+| motor | 6 | CodeSandbox(598) Controller(3529) FileDigester(828) Hands(262) Legs(1584) Mouth(606) |
+| senses | 8 | Ears(662) Eyes(739) Touch(942) VisualCortex(953) + visual_engines 4引擎(haar/mediapipe/ocr/pdf) |
+
+**空壳检查结论**：逐器官 AST 扫描 `on_pulse` 方法体，**无整器官空壳**（09-14 报告怀疑的"6个未实现器官"实为方法级预留，见"未来演化预留 v10.0"注释遍布各感官，属设计内预埋）。真正插槽式的 [PulseInferenceEngine.py](../organs/core/PulseInferenceEngine.py)（109行）**已被 organ_loader 主动跳过下线**（[organ_loader.py:159-199](../nucleus/organ_loader.py#L159)）。迷你器官8个（<200行）集中在 genetic/（社会性单独立项暂停所致，第49批已明确）。
+
+### 2.2 脉冲总线与场机制
+
+```
+发布链路：器官 → PulseCore.emit()（构建 source/event/priority/layer/ttl/payload，PulseCore.py:102-170）
+        → InfoField.publish()（InfoField.py:492-718）
+           ├─ TTL 校验
+           ├─ 业务指纹幂等去重（防重复脉冲）
+           ├─ 脉冲风暴检测 → 拒绝/聚合/限流三策略
+           └─ 订阅条件匹配 → 按 layer 分发线程池 → L3 层队列背压准入（L653-718）
+```
+
+**评估**：这不是简单总线广播，而是**带准入控制的发布-订阅场**。幂等去重+风暴检测+L3背压三层防护在同类项目中属少见完备。已有实测佐证：眼睛降帧 8.3fps→2fps 即为给 L3 队列让路（[PulseEyes.py:427](../organs/senses/PulseEyes.py#L427)）；queue_max_depth 历史峰值505（09-14），第126批"冷却闸双处限流"继续收敛。
+
+### 2.3 God文件 Top10（满足验收标准2.1.1）
+
+| # | 文件 | 行数 | 备注 |
+|---|------|-----|------|
+| 1 | organs/brain/PulseInnerWorld.py | **23249** | 12天+2400行，仍加速膨胀 |
+| 2 | nucleus/reasoning/SafeEvolutionExecutor.py | 5878 | |
+| 3 | organs/brain/PulseSubconscious.py | 4759 | |
+| 4 | nucleus/self_inspector.py | 4260 | 自检器本体成God文件 |
+| 5 | organs/brain/PulseCodeLearner.py | 4138 | |
+| 6 | organs/body/PulseLiver.py | 3725 | |
+| 7 | nucleus/reasoning/PatchManager.py | 3791 | |
+| 8 | organs/brain/PulseCortex.py | 3568 | |
+| 9 | organs/motor/PulseController.py | 3529 | |
+| 10 | nucleus/mnemosyne/PulseNodePool.py | 3441 | |
+
+### 2.4 圈复杂度 Top10（AST分支计数实测）
+
+| 分支数 | 位置 | 函数 |
+|-------|------|------|
+| **285** | PulseInnerWorld.py:564 | _on_inference_request |
+| 209 | organs/body/PulseStomach.py:422 | _do_digest |
+| 200 | nucleus/reasoning/SafeEvolutionExecutor.py:1342 | repair_with_distillation |
+| 199 | organs/brain/PulseCodeLearner.py:602 | _learn_own_code_structure |
+| 174 | PulseInnerWorld.py:9281 | _route_to_deriver |
+| 165 | PulseInnerWorld.py:7611 | _knowledge_retrieve |
+| 140 | organs/brain/PulseSubconscious.py:500 | _on_curiosity_tick |
+| 138 | main.py:1292 | _init_organs_legacy |
+| 137 | main.py:1809 | start |
+| 133 | organs/body/PulseLiver.py:1793 | _periodic_purity_check |
+
+### 2.5 耦合度
+
+器官间直接 import 仅 **9处**，且集中在两类：大脑内部协作组件装配（[PulseInnerWorld.py:37-40](../organs/brain/PulseInnerWorld.py#L37) 导入 CognitiveReflector/KnowledgeRetriever/MultiStepReasoner/ReasoningFormatter——本质是脑内功能模块化拆分）与视觉引擎插件加载（VisualCortex→visual_engines）。**器官横间通信纪律良好**，主要耦合税在"所有器官→config.py 全局"（config.py 单文件超大，属集中配置模式的固有代价）。
+
+### 2.6 架构图
+
+```mermaid
+graph TB
+    subgraph 感知层 senses
+        EYES[Eyes/VisualCortex] ; EARS[Ears] ; TOUCH[Touch]
+    end
+    subgraph 大脑层 brain
+        IW[PulseInnerWorld 23249行] ; SUB[Subconscious] ; CL[CodeLearner] ; CTX[Cortex]
+    end
+    subgraph 躯体层 body/motor
+        HEART[Heart] ; LUNG[Lung] ; LIVER[Liver 代谢/知识压缩] ; KIDNEY[Kidney] ; STOMACH[Stomach 消化] ; CTRL[Controller/Legs/Hands]
+    end
+    subgraph 治理层 identity/immune/genetic
+        ID[SelfAwareness/Personality] ; IMM[WhiteCell/Skin] ; GEN[Evolution/DNARepair]
+    end
+    subgraph 内核 nucleus
+        PC[PulseCore emit] ; IF[InfoField 场/订阅/背压/风暴检测] ; NODE[PulseNodePool L1-L4] ; SNap[Snapshot]
+        subgraph 进化
+            PM[PatchManager 沙箱+备份] ; PAA[PatchAutoApprover trust>=40] ; SEE[SafeEvolutionExecutor] ; SA[SelfAwarenessEngine 6维分析]
+        end
+        subgraph LLM
+            LUNG_ADAPTER[adapter_registry/llm] ; CR[call_recorder] ; SC[semantic_cache]
+        end
+    end
+    senses -->|脉冲| IF ; IF --> IW & SUB & STOMACH ; IW -->|知识节点| NODE
+    NODE --> LIVER ; LIVER -->|L3压缩| NODE
+    SA -->|自发现问题| SEE --> PM --> PAA ; PM -->|写源码需过闸| ID
+    IW & SEE --> LUNG_ADAPTER ; CR -.留存.-> SC
+    CTRL -->|脉冲| IF
+```
+
+**数据流**（知识生命周期）：感官脉冲 → 胃器官消化（RSS/文件→摘要）→ 内在世界 L1 建节点 → 肝脏压缩升级（L2/L3，compress_count=19）→ NodePool 持久化+快照 → 语义索引（FAISS/ONNX 向量，3套嵌入模型共约1GB）→ 检索时逆向激活。
+
+**控制流**：main.py 主循环 time-tick 驱动（`start()` 分支数137，启动装配重）；线程模型为"每器官daemon线程+InfoField分发线程池"，重启后实测 thread_count=24；看门狗/假死探测由 self_inspector+器官 recover 状态机（organ状态含 recovering/stopped 字段）承担。
+
+---
+
+## 3. 代码质量与技术债务（P0）
+
+### 3.1 质量指标实测
+
+| 指标 | 数值 | 口径说明 |
+|------|-----|---------|
+| 静默except（严格 `except…:\n pass`） | **386处/184文件** | 自测AST/正则口径 |
+| 静默异常（项目门禁口径，含无日志continue/return） | **1416处** | 第129批重扫基线（旧1449） |
+| 裸 `except:` | **35处** | SafeEvolutionExecutor 7处最多 |
+| 核心区 print()（nucleus+organs） | **1045处** | 全库2241（含tools/tests） |
+| TODO/FIXME/HACK | 仅6处 | 极干净（问题都进了债务清单而非注释） |
+| RUF100 unused-noqa | **438** | 09-14为353，清理速度<新增速度 |
+| UP031 printf格式化 | 1173（可`--fix`自动修） | |
+| I001 未排序import / UP020 open别名 / SIM115 open无上下文 | 435 / 376 / 344 | 均可自动修 |
+| PLW1510 subprocess无check | 40 | 返回码被忽略 |
+| S102 eval/exec | 15处 | 逐点审计见 §4.3 |
+
+**静默except专项治理评价（正面）**：项目已建成完整治理基建——检测器（[self_inspector.py:3194-3210](../nucleus/self_inspector.py#L3194)）、替代API（[nucleus/_silent_except.py](../nucleus/_silent_except.py) 的 `silent_exc(e, where)`，第78批）、**pre-commit hook 防新增**（第126批，CI基线校验"新增=0"）。当前是"存量消化期"，5批修约33处+批4的16处，**速度偏慢**（1416存量按此速率需数月）。
+
+### 3.2 真实技术债务清单（62条，满足验收标准2）
+
+> 优先级：🔴高 / 🟡中 / 🔵低。标注"可自动修"者 ruff --fix 可处理。09-14遗留项带 ◆。
+
+**A. 代码质量（16条）**
+
+| # | 债务 | 级别 | 证据 |
+|---|------|-----|------|
+| A1 | 静默异常存量1416（门禁口径），故障不可观测 | 🔴 | 第129批门禁 |
+| A2 | 裸except 35处，可能吞KeyboardInterrupt | 🟡 | SafeEvolutionExecutor.py 等 |
+| A3 | 核心区print 1045处绕过日志系统（OscillonField 43处最典型） | 🟡 | nucleus/field/OscillonField.py |
+| A4 | RUF100 438且在增长（353→438） | 🟡 | ruff实测 |
+| A5 | UP031×1173 printf格式化 | 🔵可自动修 | ruff |
+| A6 | I001×435 import无序 | 🔵可自动修 | ruff |
+| A7 | UP020×376 open别名 | 🔵可自动修 | ruff |
+| A8 | SIM115×344 open缺上下文管理器（句柄泄漏风险） | 🟡 | ruff |
+| A9 | UP006/UP045/UP035 类型注解旧语法共177 | 🔵可自动修 | ruff |
+| A10 | RUF012×65 可变类默认值 | 🟡 | ruff |
+| A11 | PLW1510×40 subprocess无check，失败静默 | 🟡 | ruff |
+| A12 | DTZ001×31 naive datetime | 🔵 | ruff |
+| A13 | 圈复杂度>165的6个函数（Top: 285） | 🔴 | §2.4表 |
+| A14 | ◆ 意图→范式映射仍硬编码8条dict | 🟡 | TaskPipeline.py:178-187 |
+| A15 | TaskPipeline 空转（recent_count=0） | 🟡 | runtime_state.json |
+| A16 | B010/B009 setattr/getattr常量×44 | 🔵可自动修 | ruff |
+
+**B. 架构（15条）**
+
+| # | 债务 | 级别 | 证据 |
+|---|------|-----|------|
+| B1 | PulseInnerWorld 23249行且12天+2400行 | 🔴 | 实测 |
+| B2 | SafeEvolutionExecutor 5878行 | 🟡 | 实测 |
+| B3 | self_inspector 4260行（检测器本体成God文件） | 🟡 | 实测 |
+| B4 | 器官数四口径漂移：57/58/50/66 | 🟡 | 任务书/宪法/main.py/实测 |
+| B5 | 迷你器官8个<200行（genetic社会性4个暂停中） | 🔵 | 实测 |
+| B6 | PulseInferenceEngine 已下线未删除 | 🔵 | organ_loader.py:159跳过 |
+| B7 | 器官直接import器官9处（脑内装配型） | 🔵 | PulseInnerWorld.py:37-40 |
+| B8 | ◆ config.py 单文件超大（全局配置强耦合） | 🟡 | 实测 |
+| B9 | ◆ L3知识3473仍倒挂（09-14:3236，恶化+237） | 🔴 | runtime_state.json |
+| B10 | L2=8178膨胀（09-14:7588） | 🟡 | runtime_state.json |
+| B11 | ◆ eureka_moment 只产不销 | 🟡 | PulseInnerWorld.py:15345 |
+| B12 | insight_board.insight_count=0 洞察板空转 | 🟡 | runtime_state.json |
+| B13 | ◆ 版本号三处漂移 v9.5/v9.0/v25.1 | 🟡 | config.py:21/pulse_config.yaml:4/宪法 |
+| B14 | pulse_config.yaml 停留 v9.0/2026-06-14 未随版本演进 | 🔵 | yaml头 |
+| B15 | ◆ 宪法红线断言/断网验证 verify_offline_cognition 无实现（09-14提出未动） | 🟡 | 全库检索 |
+
+**C. 安全（10条）** → 详见 §4，此处编号入账
+
+| # | 债务 | 级别 |
+|---|------|-----|
+| C1 | RSS/Wiki出站无scheme/host白名单（SSRF间接链路） | 🔴 |
+| C2 | logger无脱敏层（key/对话/人脸风险） | 🔴 |
+| C3 | 人脸编码明文JSON落盘 | 🟡 |
+| C4 | auto_apply开启前提的两处验证链弱化未修 | 🟡 |
+| C5 | PerformanceProfiler.py:69 exec(code_str) 来源未限 | 🟡 |
+| C6 | eval(f-string)风格（白名单约束下低危） | 🔵 |
+| C7 | 对话内容整段入日志 | 🟡 |
+| C8 | failure_tracker .bak×3 堆积无清理策略 | 🔵 |
+| C9 | subprocess PLW1510×40 | 🟡 |
+| C10 | 安全红队场景无测试（注入/越界用例零覆盖） | 🟡 |
+
+**D. 性能（8条）** → 详见 §5
+
+| # | 债务 | 级别 |
+|---|------|-----|
+| D1 | 单进程内存~3.9GB无告警基线 | 🟡 |
+| D2 | data/ 5.67GB，12天+1.4GB增速 | 🟡 |
+| D3 | ◆ L3队列背压历史峰值505 | 🟡 |
+| D4 | 3套ONNX嵌入模型并存~1GB（09-14 P1-265未收敛） | 🟡 |
+| D5 | SIM115句柄泄漏风险344处放大I/O压力 | 🔵 |
+| D6 | 眼睛降帧2fps属性能换稳定（需功能完善后回调） | 🔵 |
+| D7 | GIL下77线程（09-14）/24线程（现）切换开销 | 🔵 |
+| D8 | write_count=14802/重启周期，写放大待评估 | 🔵 |
+
+**E. 工程（8条）**
+
+| # | 债务 | 级别 |
+|---|------|-----|
+| E1 | ◆ .bak_batch22~52 二十余快照目录占库未清理 | 🟡 |
+| E2 | ◆ .release-tmp 镜像仍在且未被ruff排除 | 🟡 |
+| E3 | 未提交25条docs | 🔵 |
+| E4 | ◆ 技术债务清单6830行单文件难维护 | 🟡 |
+| E5 | 测试有4214例但无coverage率指标门禁 | 🟡 |
+| E6 | ◆ P2-212/212编号撞车等债务编号主数据缺失 | 🔵 |
+| E7 | 烛微系列与第三方报告多口径并存无对账 | 🔵 |
+| E8 | ◆ 器官级测试覆盖矩阵未常态化出数 | 🟡 |
+
+**F. 进化闭环（5条）** → 详见 §7
+
+| # | 债务 | 级别 |
+|---|------|-----|
+| F1 | ◆ P0-250 LLM依赖度下降无实证 | 🔴 |
+| F2 | 静默except消化速率偏慢（5批~50处 vs 存量1416） | 🟡 |
+| F3 | ◆ L1=492重启后新基线，层级重置现象待解释 | 🟡 |
+| F4 | 自主意图生成有效性无量化 | 🟡 |
+| F5 | ◆ P0-262 依赖度口径错位未修 | 🟡 |
+
+### 3.3 历史债务有效率抽查（验收标准2.2.3）
+
+对09-14报告33项在册债务抽样复核（09-26状态）：**"挂账已久实际已修"3项确认**（P2-331/332/333第49批已修但文档曾排54/55批）；**"文档称已修实际未修"0项**（第53批3项P0在09-14虚标，本次实测 PatchAutoApprover.py:42 `AUTO_APPROVE_MIN_TRUST = 40` **已真修**）；**假闭环1项**（第129批门禁自曝"新基线=456失真，实测重扫1416"——项目自纠机制有效）。**综合判断：账实相符率较09-14显著提升，自纠机制（烛微前置分析+门禁自曝）已起作用。**
+
+---
+
+## 4. 安全性审计（P0）
+
+### 4.1 补丁治理链——**纵深防御完整，fail-closed属实（正面结论）**
+
+| 环节 | 实现 | 证据 | 判定 |
+|------|------|------|------|
+| 路径沙箱 | `.py`白名单+realpath归一化+项目根前缀校验，异常fail-closed拒绝 | [PatchManager.py:1433-1497](../nucleus/reasoning/PatchManager.py#L1433) | ✅ |
+| 写前闸门 | 任何 open/copy2 前先过路径校验 | PatchManager.py:1510-1528 | ✅ |
+| 总开关收口 | auto_apply_enabled=False 时机器批准的补丁**退回pending**（修复了绕过总开关的历史问题） | PatchManager.py:2677-2718 | ✅ |
+| 应用前安全链 | 冻结跳过→核心文件闸→人格基线校验→安全校验→副本验证→**备份原文件** | PatchManager.py:2817-2889 | ✅ |
+| 自动审批门槛 | MIN_TRUST=**40**（09-14时30，P0已真修）+config热加载+24h等待+组合判定 | [PatchAutoApprover.py:42-73, 221-272](../nucleus/evolution/PatchAutoApprover.py#L42) | ✅ |
+| 当前开关 | auto_apply_enabled=**False**（默认关） | config.py:951 | ✅ |
+
+**残留风险（债C4）**：配置注释自述开启S1前需修两处弱化——验证链"遇self跳过"+"回归失败不阻塞"（[config.py:947-949](../config.py#L947)），未修前开启自动应用即可被语义错误补丁穿透。
+
+### 4.2 权限与数据边界
+
+- **WriteGuard**（[write_guard.py:100-128](../nucleus/data/write_guard.py#L100)）：环境分级判定（production/test），普通脚本默认只读，框架主进程始终可写——设计合理 ✅
+- **沙箱**：nucleus/security/（sandbox_core+limits）+ PulseCodeSandbox/PulseWhiteCell/PulseSkin 均内置危险调用黑名单（os.system/subprocess/eval/exec/\_\_import\_\_）✅
+- **环境变量黑名单机制**：config.py:2866 存在危险token黑名单配置 ✅
+
+### 4.3 真实风险点清单（满足验收标准3：≥3个）
+
+| # | 风险 | 级别 | 证据与攻击链 |
+|---|------|-----|-------------|
+| R1 | **出站请求无scheme/host白名单（SSRF间接链路）** | 🔴高 | [RssCollector.py:50-54](../nucleus/knowledge/RssCollector.py#L50)、[WikiQuerier.py:121](../nucleus/knowledge/WikiQuerier.py#L121) 直接 `urlopen(req)` 无校验。URL虽来自配置，但**config可被补丁修改**——若S1开启后恶意/错误补丁改RSS源，即获得任意外联通道（数据外传/内网探测） |
+| R2 | **日志零脱敏** | 🔴高 | [logger.py](../nucleus/logger.py) 全文无 mask/redact/脱敏逻辑；对话整段入日志（chat路径），任何LLM响应/用户隐私原样落盘 logs/。开源（AGPL，仓库已建）后若有人以真实数据运行即泄露 |
+| R3 | **人脸生物特征明文JSON落盘** | 🟡中 | [PulseVisualCortex.py:89](../organs/senses/PulseVisualCortex.py#L89) `_FACE_ROSTER_PATH`，safe_read/write_json 明文存128维人脸编码；可被环境变量 `TONGTONG_FACE_ROSTER` 重定向（:970），无加密无访问控制 |
+| R4 | PerformanceProfiler 直接 exec 外部代码串 | 🟡中 | [PerformanceProfiler.py:69](../nucleus/evolution/PerformanceProfiler.py#L69) `exec(code_str, globals_dict or {})`，globals_dict可空，与补丁链沙箱不同此路径无白名单前闸 |
+| R5 | eval风格弱点（**低危**，正则白名单下不可注入） | 🔵低 | [PulseInnerWorld.py:9085-9089](../organs/brain/PulseInnerWorld.py#L9085) 数字/四则运算符正则约束后再eval，实为安全但宜改算术直算；对照 SymbolicReasoner.py:728 与 PulseInnerWorld.py:17390 已用 `{"__builtins__":{}}` 沙箱eval（较好） |
+| R6 | pickle/yaml.unsafe 全库0处、subprocess shell=True 0处 | ✅ | 危险调用扫描实证——正面结论 |
+
+### 4.4 数据安全
+
+- 快照/备份：PulseSnapshot（3000行）+ PatchManager 应用前备份，回滚链路存在 ✅
+- 知识完整性：write_count=14802 + 肝脏 compress_count=19，有日志完整性事件（log_integrity）追踪 ✅
+- 短板即 R2/R3 两条。
+
+---
+
+## 5. 性能分析（P1）
+
+| 维度 | 实测数据 | 判断 |
+|------|---------|------|
+| **内存** | 单进程 ~3.9GB（第129批门禁实测 PID 52580）；无内存告警基线；3套ONNX模型并存约1GB（jina 641MB + MiniLM 235MB + bge 95MB×2副本） | 基线偏高无监控（债D1）；嵌入模型三选一可省~800MB |
+| **知识图谱** | L1=492 / L2=8178 / L3=3473(l3_pure=3468)，knowledge_density=0.959；**L3倒挂较09-14恶化（3236→3473）**；重启后L1从2754跌至492（层级重置现象，债F3） | L3/L1比例=7:1，压缩升级管道（肝脏compress=19）吞吐不足 |
+| **CPU/并发** | 线程数：77（09-14满载）→24（重启后）；GIL下多线程用于I/O等待合理；Cython加速两处（场振荡/pulse编解码） | 结构合理，无异常热点证据 |
+| **I/O** | data/ 5.67GB（12天+1.4GB≈117MB/天）；logs/ 0.06GB（10MB轮转×5，PermissionError已修）；write_count=14802/周期；Parquet+JSONL双存储 | 磁盘增速主要在知识/快照，需定期归档策略 |
+| **队列** | queue_max_depth 历史峰值505（09-14）；第126批冷却闸双处限流+眼睛降帧2fps 已作主动退让 | 背压机制工作正常但靠降速换稳定 |
+| **锁** | lock_wait_avg_ms=0.0（当前空载） | 无锁争用证据（满载数据缺失，标推测） |
+
+---
+
+## 6. 可维护性分析（P1）
+
+### 6.1 测试
+
+- **4214例**（12天+1743），tests/ 目录化+conftest，批次门禁测试（m22~m95+）成体系 ✅
+- 五项门禁：ruff F=0 / py_compile / 批次单测 / **静默except CI（新增=0）** / CRLF行尾保全 —— 门禁维度设计好 ✅
+- 短板：**无 coverage 率指标**（债E5）；器官级覆盖矩阵未常态化出数（债E8）；安全红队用例零覆盖（债C10）
+
+### 6.2 文档
+
+- docs/ 344+文件，结构总览导航、任务书→交付报告→门禁结果闭环齐全 ✅
+- 已有内部"烛微"审计系列（深度审计3期+摸底+判据校准），**本次第三方报告与其口径并存，建议对账合并**（债E7）
+- 文档vs代码滞后已显著改善（09-14发现的3项"已修未销号"经129批后账实基本一致）
+
+### 6.3 工程规范
+
+- git：批次化小步提交+交付diff文档+hook纪律 ✅；近期提交信息规范（"第N批 X+Y+Z 交付（T-Na/b/c）"）✅
+- 仓库卫生：.bak_batch22~52 与 .release-tmp 仍占库（债E1/E2）；AGPL-3.0+Gitee仓库已建，**开源前必清 R2/R3/E1/E2/B13**
+
+---
+
+## 7. 自主进化闭环有效性（P0）——**结论：真实有效，非空转**
+
+### 7.1 闭环真实效果
+
+| 证据 | 数据 | 结论 |
+|------|------|------|
+| 补丁代码级AST复现（第51批机制，持续生效） | 60条：51真通过+7部分修复+**0假通过**（严格修复率0.8793） | 补丁不是假通过 ✅ |
+| 自发现问题全程追踪 | [effect_verify.json](../data/evolution/effect_verify.json) 每条 issue 有 first_seen/fixed_at/recurrences（如 PulseCortex silent_exception: 09-09发现→09-12修复，0复发） | 闭环有据可查 ✅ |
+| 补丁队列 | pending_patches.json = **0条**（积压清空） | 无越修越积压 ✅ |
+| 近期进化方向 | 第124-129批全部是自检发现的静默异常/治理收敛修复 | 自我发现→自修链路在真实运转 ✅ |
+| 修复速度 | 静默except 1449→1416（6批约50处），慢于理想 | 消化速率待提升（债F2） |
+
+### 7.2 LLM依赖度
+
+- 留存管道已建成：call_recorder 在4个主调用点接线（PulseLung/LLMEvolutionEngine/SelfReflectionEngine/SafeEvolutionExecutor），**但依赖度下降（P0-250）仍无公开实证**（债F1）——留存数据已在累积，缺一份时序对比分析即可闭环。
+- 本地推理：语义缓存/调用模式分析/内在模型自更新模块齐备，属"基建已备、收益未证"。
+
+### 7.3 进化方向性
+
+- **正向**：批次主题从"修bug"演进到"治理收敛"（静默except→hook→账面收口），方向在向工程健康度深化；
+- **未闭环项**：TaskPipeline 元流程仍空转（债A15）、eureka 只产不销（债B11）、L3倒挂加剧（债B9）——三处"进化盲区"是框架自己尚未发现的问题（self_inspector 无此类检测器），建议加入检测器。
+
+---
+
+## 8. 风险清单（按严重度）
+
+| 级别 | 风险 | 影响 | 对应债/险编号 |
+|------|------|------|--------------|
+| 🔴高 | PulseInnerWorld 巨石持续膨胀+圈复杂度285 | 任何改动回归成本极高，AI自修效率递减 | B1/A13 |
+| 🔴高 | 静默异常存量1416 | 生产故障不可观测，与"数字生命可解释性"目标冲突 | A1 |
+| 🔴高 | 出站SSRF间接链路 | 补丁→config→外联通道 | R1/C1 |
+| 🔴高 | 日志零脱敏+人脸明文 | 隐私合规风险，**开源即触发** | R2/R3 |
+| 🟡中 | auto_apply S1两处弱化未修即开启 | 语义错误补丁静默写入核心源码 | C4 |
+| 🟡中 | L3知识倒挂恶化+data/年增~35GB | 检索质量与存储失控 | B9/D2 |
+| 🟡中 | 无coverage门禁+安全零用例 | 回归防线有洞 | E5/C10 |
+| 🔵低 | 版本号/器官数多口径漂移 | 沟通错位、开源观感 | B13/B4 |
+
+---
+
+## 9. 优化建议 Top20（按ROI排序，满足验收标准5）
+
+| # | 建议 | 实施方案 | 预估量 |
+|---|------|---------|--------|
+| 1 | **开源前隐私三件套**：日志脱敏层 | logger.py 加 `mask_secrets()`（api_key/sk-前缀/人脸路径正则替换），chat入日志前截断 | 1批 |
+| 2 | 人脸册加密或权限收紧 | face_roster JSON 改为 XOR+机器码混淆最低限；去掉环境变量重定向或加白名单 | 0.5批 |
+| 3 | RSS/Wiki出站白名单 | `_default_fetch` 前置 scheme∈{http,https} + host白名单（config白名单+拒绝私网IP段 re 校验） | 0.5批 |
+| 4 | 清理 .bak_batch*/.release-tmp | 确认无用后删除或移出仓库；ruff.toml exclude 补 `.release-tmp` | 0.1批 |
+| 5 | 版本号统一 | 宪法为唯一权威，config.py/yaml 同步；加 CI 校验三处一致 | 0.1批 |
+| 6 | **PulseInnerWorld 拆分启动** | 先拆 `_on_inference_request`(285分支)：按 推理路由/知识检索/表达生成 三模块；每拆一块跑全量4214测试 | 4-6批渐进 |
+| 7 | 静默except提速 | 每批定量≥30处+优先 nucleus/ 核心区386严格口径；hook已有，补"清零看板"进 runtime_state | 持续 |
+| 8 | 裸except 35处清零 | 逐处改 `except Exception`+silent_exc；SafeEvolutionExecutor 7处优先 | 0.5批 |
+| 9 | ruff自动修三连 | `ruff check --fix --select UP031,I001,UP020,UP006,UP045`（约2100处一次清）| 0.5批+全量回归 |
+| 10 | SIM115句柄344处 | 优先 nucleus/ 子集改 with 语句（I/O稳定性） | 2批 |
+| 11 | PLW1510 subprocess check | 40处补 check=True 或显式捕获 returncode | 0.5批 |
+| 12 | 核心区print 1045处 | 复用静默except批次节奏，先 nucleus/field/（OscillonField 43处） | 3批渐进 |
+| 13 | **coverage率入门禁** | pytest-cov 出 organ/nucleus 覆盖矩阵，阈值≥60%核心区，进五项门禁变六项 | 1批 |
+| 14 | 嵌入模型三合一 | 保留 bge-small-zh（95MB），jina/MiniLM 移出 data/models；索引重建脚本 | 1批 |
+| 15 | L3治理检测器 | self_inspector 增 L3/L1 比例检测器（阈值5:1告警），肝脏压缩吞吐提升为后续批 | 1批 |
+| 16 | TaskPipeline 二选一 | 接线（肺对话路由进 pipeline）或正式下线删除，消除8条硬编码映射 | 0.5批 |
+| 17 | eureka 消费方 | 洞察板消费 eureka 事件→生成每日自认知报告素材（打通 ReportBus） | 1批 |
+| 18 | PerformanceProfiler.exec 加前闸 | 来源限 tests/tools 路径或移除该器能 | 0.2批 |
+| 19 | 内存告警基线 | runtime_state 增进程RSS采集，>4.5GB 告警；psutil 一行接入 | 0.3批 |
+| 20 | data/ 归档策略 | 快照/知识历史分区按月归档压缩，年增量压到<10GB | 1批 |
+
+---
+
+## 10. 验收对照与方法局限
+
+**验收标准达成**：
+1. ✅ 架构分析覆盖全部66个器官文件/9子系统（§2.1全景表）
+2. ✅ 技术债务清单62条（§3.2，>50）
+3. ✅ 安全审计6个真实风险点，其中3高2中（§4.3）
+4. ✅ 性能分析全部有数据支撑（§5）
+5. ✅ 优化建议20条，每条含实施方案（§9）
+
+**方法局限（区分"已验证事实"与"推测判断"）**：
+- 本报告全部指标为 **2026-09-26 17:10-17:30 静态实测**（框架运行中，部分运行时指标为重启后初期值）；
+- CPU满载画像、LLM依赖度时序、内存增长曲线属**运行时长期观测项**，本次无法从静态只读获得，相关判断已标注"推测"或"待证"；
+- 安全审计为白盒代码级审计，未做渗透测试；
+- 圈复杂度为AST分支计数（非标准cyclomatic精确值），仅用于相对排序。
+
+---
+
+*第三方独立分析 · 只读合规 · 2026-09-26*
diff --git a/docs/第三方后续深度分析报告_20260926.md b/docs/第三方后续深度分析报告_20260926.md
new file mode 100644
index 0000000..792e00d
--- /dev/null
+++ b/docs/第三方后续深度分析报告_20260926.md
@@ -0,0 +1,441 @@
+# 曈曈PulseNet 第三方后续深度分析报告（三方向）
+
+**执行方**：第三方独立分析（TRAE）｜**日期**：2026-09-26｜**基线**：`fda267d`（第129批）
+**依据**：[第三方后续深度分析任务书_20260926.md](第三方后续深度分析任务书_20260926.md)
+**约束**：全程只读；所有方案贴合现有架构（器官化+脉冲总线+self_inspector检测器注册模式）；只输出方案不改代码。
+
+**方案落点实测依据**（已核实）：
+- [logger.py](../nucleus/logger.py)：标准 logging 双通道（console:539 / SafeRotatingFileHandler:561），PulseFormatter:105，LogAggregationFilter:44——脱敏层有唯一干净插入点
+- [self_inspector.py](../nucleus/self_inspector.py)：检测器签名 `_check_xxx(body, file_path, organ, method, start_line, issues)` + `GLOBAL_DETECTORS` 全库级注册表（:2687）+ AST缓存 + `skip_detectors` 耗时下推（:2702，全量扫描约9分钟的教训已内建）；**已有** method_length:3232 / print_debug:2535 / silent_exception:3194 / bare_except:3267 检测器
+- [PulseInnerWorld.py](../organs/brain/PulseInnerWorld.py)：23251行/367方法/**21个SECTION分区**（第30批已做过一次"提取推理检测器"的先例）——天然拆分缝已存在
+- [PulseLiver.py](../organs/body/PulseLiver.py#L9)：两级压缩（`_compress_l1_to_l2`/`_fuse_l2_to_l3`）+ 独立冷却计时器(:92) + 质量门控 `_assess_node_quality` + L2→L3构图（圈复杂度133）
+- 人脸册：[PulseVisualCortex.py:89-91](../organs/senses/PulseVisualCortex.py#L89) `data/identity/face_roster.json` 明文JSON，`safe_read_json/safe_write_json` 读写，env `TONGTONG_FACE_ROSTER` 可重定向(:970)
+- 出站点：[RssCollector.py:50-54](../nucleus/knowledge/RssCollector.py#L50)、[WikiQuerier.py:121](../nucleus/knowledge/WikiQuerier.py#L121)；RSS源5个硬编码于 [config.py:143-149](../config.py#L143)，百科 url_template:171
+- 静默except分布（严格口径386处/184文件）：PulseNodePool 21、health_ui 12、PatchManager 8、ReasoningWorkerPool 8、fast_ops 7
+
+---
+
+# 任务一：开源前隐私合规整改清单（P0）
+
+## 1.1 日志脱敏层
+
+**落点决策：handler级 Filter（中间件），不做调用点改造。**
+理由：调用点改造需触碰 1000+ 处 print/上万个 logger 调用点（不现实且必有遗漏）；logging 的 Filter 在 record 进入 handler 前统一改写 record.msg，一处生效全库覆盖，且 console/file 双通道可分别配置（console 脱敏、file 按开关）。
+
+**必须脱敏的字段与正则**（新建 `nucleus/logging/sanitizer.py`）：
+
+```python
+# nucleus/logging/sanitizer.py —— 方案骨架（约120行）
+import re
+
+_PATTERNS = [
+    # 1. API Key（OpenAI/Ark/DashScope 全系 sk- 前缀及通用 key=value 形态）
+    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "<REDACTED_KEY>"),
+    (re.compile(r"(?i)\b(api[_-]?key|token|secret|authorization)\s*[=:]\s*\S+"), r"\1=<REDACTED>"),
+    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer <REDACTED>"),
+    # 2. 手机号（1开头11位，避免误伤时间戳：前后非数字）
+    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<REDACTED_PHONE>"),
+    # 3. 邮箱
+    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<REDACTED_EMAIL>"),
+    # 4. 身份证（15/18位）
+    (re.compile(r"(?<!\d)\d{15}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)"), "<REDACTED_ID>"),
+    # 5. 人脸册路径（含身份含义的路径指纹）
+    (re.compile(r"[A-Za-z]:\\[^\s]*face_roster\.json"), "<REDACTED_FACE_PATH>"),
+    (re.compile(r"[A-Za-z]:\\[^\s]*identity\\"), "<REDACTED_ID_PATH>"),
+]
+
+def sanitize(text: str) -> str:
+    for pat, repl in _PATTERNS:
+        text = pat.sub(repl, text)
+    return text
+
+class SanitizingFilter(logging.Filter):
+    """★方案：Filter内改写record，对调用方零侵入"""
+    def __init__(self, enabled: bool = True):
+        super().__init__()
+        self.enabled = enabled
+    def filter(self, record: logging.LogRecord) -> bool:
+        if self.enabled:
+            try:
+                record.msg = sanitize(str(record.msg))
+                if record.args:
+                    record.args = tuple(
+                        sanitize(str(a)) if isinstance(a, str) else a for a in record.args)
+            except Exception:
+                pass  # 脱敏自身失败不得阻断日志
+        return True
+```
+
+**接线**（[logger.py:539-570](../nucleus/logger.py#L539) 两处 addHandler 前各加一行）：
+
+```python
+# config.py 新增：
+ENABLE_LOG_SANITIZER = True       # 主开关（默认开）
+LOG_SANITIZER_DEBUG_MODE = False  # 白名单DEBUG：True时console不脱敏仅file脱敏
+# logger.py setup 处：
+from nucleus.logging.sanitizer import SanitizingFilter
+file_handler.addFilter(SanitizingFilter(enabled=True))              # 文件日志永远脱敏
+console_handler.addFilter(SanitizingFilter(enabled=not LOG_SANITIZER_DEBUG_MODE))
+```
+
+**对话内容怎么办（实事求是的设计取舍）**：对话整段脱敏=日志失去诊断价值，**不做全量打码**。方案：脱敏层只处理结构化个人敏感信息（上表5类），对话原文保留语义；补充一条 `REDACT_USER_UTTERANCE=False` 开关，开源模板/演示模式下用户可自行开启把用户侧发言替换为 `<USER>`。这兼顾"开源合规底线"与"自认知功能所需的对话可读性"。
+
+**脱敏后怎么调试**：`LOG_SANITIZER_DEBUG_MODE=True` 时 console 通道免脱敏、文件通道仍脱敏——开发者在屏幕上看全量、落盘的是安全版；另提供 `python -c "from nucleus.logging.sanitizer import sanitize; print(sanitize(open('logs/pulse.log',encoding='utf-8',errors='ignore').read()[-5000:]))"` 式自检命令验证命中。
+
+**工作量**：sanitizer.py 约120行 + logger.py 接线6行 + config 3行 + 测试1个（含正则用例表约15条）≈ **200行/1批**。
+
+## 1.2 人脸生物特征加密方案
+
+**方案对比与推荐（分两步走）**：
+
+| 方案 | 依赖 | 强度 | 工作量 | 结论 |
+|------|------|------|--------|------|
+| A. XOR+机器码混淆 | 零依赖 | 低（防误接触，不防有心人） | ~80行 | **MVP采纳**（开源前） |
+| B. cryptography.Fernet | +cryptography库 | 标准 | ~60行+依赖管理 | 开源正式版推荐 |
+| C. Windows DPAPI | 平台绑定 | 标准 | 中 | 排除（破坏跨平台） |
+
+**MVP代码方案**（改造点集中在 [PulseVisualCortex.py](../organs/senses/PulseVisualCortex.py) 的 `_face_roster_path`/读(:161,1029)/写(:988,1033) 三处，共约80行）：
+
+```python
+# 新增 nucleus/security/face_codec.py
+import base64, getpass, hashlib, json, os
+
+def _machine_key() -> bytes:
+    """机器码混淆：MAC+用户名派生密钥（MVP级，防'拷走即得'）"""
+    mac = uuid.getnode().to_bytes(6, "big")
+    seed = f"{mac.hex()}|{os.getlogin()}|tongtong-face-v1"
+    return hashlib.sha256(seed.encode()).digest()
+
+def encode_roster(data: dict) -> str:
+    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
+    key = _machine_key()
+    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
+    return "TT-FACE-V1:" + base64.b64encode(xored).decode()
+
+def decode_roster(text: str) -> dict:
+    if not text.startswith("TT-FACE-V1:"):
+        return text if isinstance(text, dict) else {}   # 兼容旧明文册（首读后即转加密）
+    xored = base64.b64decode(text[len("TT-FACE-V1:"):])
+    key = _machine_key()
+    raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
+    return json.loads(raw.decode("utf-8"))
+```
+
+写入点把 `safe_write_json(path, data)` 换成 `safe_write_text(path, encode_roster(data))`，读取点对称。**兼容策略**：读到旧明文格式时照常加载并在下次写时自动转加密（灰度迁移，无需一次性迁移脚本）。
+
+**env 重定向白名单（要加）**：[PulseVisualCortex.py:970](../organs/senses/PulseVisualCortex.py#L970) 现在任意路径可重定向。改为：仅当解析后的路径位于 `data/` 目录内才生效，否则回退默认路径并打 WARN（防补丁/脚本把人脸册引到任意位置）：
+
+```python
+def _face_roster_path(self) -> str:
+    _p = os.environ.get("TONGTONG_FACE_ROSTER", self._FACE_ROSTER_PATH)
+    _data_root = os.path.realpath(os.path.join(_PROJECT_ROOT, "data"))
+    if not os.path.realpath(_p).startswith(_data_root):
+        self._log(LogLevel.WARNING, f"[R5] TONGTONG_FACE_ROSTER 越界({ _p })，回退默认册路径")
+        return self._FACE_ROSTER_PATH
+    return _p
+```
+
+**访问控制**：单机场景跨平台 ACL 复杂度高、收益低，**不做文件ACL**；以"落盘加密+路径白名单+脱敏层隐藏路径"三层替代（等B方案Fernet落地后强度再升一级）。
+
+**工作量**：face_codec.py 80行 + PulseVisualCortex 4处改造 + 测试1个 ≈ **150行/0.5批**。
+
+## 1.3 出站请求白名单
+
+**新建统一守卫 `nucleus/security/outbound_guard.py`**（约90行），三个出站点全部接入：
+
+```python
+# nucleus/security/outbound_guard.py
+import ipaddress, socket
+from urllib.parse import urlparse
+
+ALLOWED_SCHEMES = {"http", "https"}
+# config 可覆盖：OUTBOUND_HOST_WHITELIST = ["www.solidot.org", "www.infoq.cn",
+#   "feed.cnblogs.com", "www.ruanyifeng.com", "sspai.com", "baike.baidu.com"]
+_DEFAULT_HOSTS = {...上面六个...}
+
+def _is_private_host(host: str) -> bool:
+    try:
+        infos = socket.getaddrinfo(host, None)
+        for inf in infos:
+            ip = ipaddress.ip_address(inf[4][0])
+            if (ip.is_private or ip.is_loopback or ip.is_link_local
+                    or ip.is_reserved or ip.is_multicast):
+                return True
+    except socket.gaierror:
+        return True          # 解析失败=拒绝（fail-closed）
+    return False
+
+def validate_outbound(url: str) -> tuple[bool, str]:
+    try:
+        u = urlparse(url)
+    except Exception:
+        return False, "parse_error"
+    if u.scheme not in ALLOWED_SCHEMES:
+        return False, f"scheme_denied:{u.scheme}"
+    host = (u.hostname or "").lower()
+    if host not in _current_whitelist():          # config热加载读取
+        return False, f"host_not_whitelisted:{host}"
+    if _is_private_host(host):                    # SSRF核心：DNS解析后再验IP
+        return False, "private_ip_denied"
+    return True, "ok"
+```
+
+**三个改造点**（各2行）：
+
+```python
+# RssCollector.py:50 _default_fetch 开头；WikiQuerier.py:121 urlopen 前；
+# （以及 visual_engines/pdf_engine.py 若存在远程取图路径——同款守卫）
+from nucleus.security.outbound_guard import validate_outbound
+_ok, _why = validate_outbound(url)
+if not _ok:
+    raise ValueError(f"[OutboundGuard] 拒绝出站请求: {_why} url={url[:120]}")
+```
+
+**设计要点（实事求是的三条）**：
+1. **fail-closed + 白名单默认值**：默认白名单=当前 config.py:143-149 五个RSS源+baike.baidu.com，行为零变化；新源需显式加白，这正是意图（防补丁篡改config注入新源）。
+2. **必须做 DNS 解析级校验**（`getaddrinfo` 后验IP），只查 hostname 字符串挡不住 `http://内网域名/` 与 DNS rebinding 的初级形态。
+3. **私有IP段覆盖**：127/8、10/8、172.16/12、192.168/16、169.254/16、::1、fc00::/7、fe80::/10（ipaddress 库的 is_private/is_loopback/is_link_local 已全含）。
+
+**工作量**：guard 90行 + 3处接线 + config 白名单项 + 测试（scheme拒绝/私网拒绝/白名单放行/DNS失败拒绝 4用例）≈ **180行/0.5批**。
+
+## 1.4 优先级、工作量与开源MVP
+
+| 优先级 | 项 | 代码量 | 批次 | 理由 |
+|--------|-----|--------|------|------|
+| P1 | 日志脱敏层 | ~200行 | 1批 | **泄露面最大**（logs/是运行必产物），且对话/对话元数据持续累积 |
+| P2 | 出站白名单 | ~180行 | 0.5批 | 唯一"可被外部利用"的注入面（补丁→config→外联） |
+| P3 | 人脸册加密+路径白名单 | ~150行 | 0.5批 | 单机场景实际风险最低，但属"生物特征"合规敏感项 |
+| — | 脱敏自检工具+文档 | ~60行 | 并入P1 | 开源README需声明"日志默认脱敏" |
+
+**开源前最小集合（MVP）= P1+P2+P3 全部**：合计约 **590行 / 2批**。理由：三项各自独立、都小、都有测试，没有"再等等"的理由；其中 P1/P2 若赶时间可先于 P3 发布（人脸功能默认关闭摄像头场景下暴露有限），但**P1 是绝对底线**。
+
+---
+
+# 任务二：self_inspector 补充检测器设计（P1）
+
+## 2.0 接入模式总述
+
+现有两类检测器：方法级 `_check_xxx`（23个）与全库级 `GLOBAL_DETECTORS`（3个）。三个新检测器分属三种新形态——**运行时指标型（L3倒挂）**、**文件度量型（巨石化）**、**趋势对比型（静默增长率）**，均无需新增扫描框架：
+
+```python
+# self_inspector.py 内新增（与 GLOBAL_DETECTORS 并列）：
+RUNTIME_DETECTORS = {"l3_inversion": "_detect_l3_inversion"}      # 读runtime_state，<1ms
+FILE_METRIC_DETECTORS = {"god_file": "_detect_god_file"}          # 复用AST缓存，增量~0
+TREND_DETECTORS = {"silent_growth": "_detect_silent_growth"}      # 复用silent_exception结果计数
+```
+
+## 2.1 L3倒挂检测器（运行时指标型）
+
+```python
+def _detect_l3_inversion(self, issues: list) -> None:
+    """★运行时检测器：知识金字塔倒挂。数据源 runtime_state.json（肝脏已落盘），
+    不做AST扫描，周期由调用方（每N个tick一次）控制，成本<1ms。"""
+    import json, os
+    _p = os.path.join(_PROJECT_ROOT, "data", "runtime_state.json")
+    try:
+        _ke = json.load(open(_p, encoding="utf-8")).get("evolution", {}).get("knowledge_evolution", {})
+    except Exception:
+        return
+    _l1, _l3 = _ke.get("l1_count", 0), _ke.get("l3_pure", 0)
+    if _l1 < 50:            # 重启初期L1重建中，避免误报（09-26实测重启后L1=492）
+        return
+    _ratio = _l3 / max(_l1, 1)
+    _level = "high" if _ratio > 8.0 else ("medium" if _ratio > 6.0 else None)
+    if _level:
+        issues.append({
+            "file": "runtime", "organ": "PulseLiver", "method": "_fuse_l2_to_l3",
+            "line": 0, "type": "knowledge_pyramid_inversion",
+            "severity": _level,
+            "description": f"L3/L1={_ratio:.1f}:1 (L3={_l3}, L1={_l1})，"
+                           f"超过告警阈值{'8' if _level=='high' else '6'}:1（宪法意图：L3应稀少）",
+            "suggestion": "①触发肝脏 _check_and_optimize 立即执行一轮 L2→L3 融合；"
+                          "②核查 _fuse_l2_to_l3 冷却间隔(_last_fuse_time)与构图门槛；"
+                          "③必要时调低 _assess_node_quality 对 L2 摘要的准入分"})
+```
+
+**阈值依据**：实测当前 L3/L1 = 3473/492 ≈ **7.1:1**（重启后口径）与 3236/2754 ≈ 1.2:1（09-14满载口径）差异巨大——故阈值不能拍死：**告警基线=滚动7天自身均值的1.5倍**（自适应）+ 绝对上限 8:1（兜底），两条件取或，避免重启初期/满载两态间的误报。
+
+**检测周期**：随 self_inspector 常规轮询（与现有周期一致），但**每24h最多记一条**（防日志风暴，复用 LogAggregationFilter 思路）。
+
+**告警动作**：写 issue（high级，进自主进化链路的 high/medium 消费通道）+ 发 `KnowledgeEvent` 脉冲让肝脏收到"立即融合"请求（**不直接调用**，保持器官间只走脉冲的纪律）。
+
+**肝脏吞吐不足根因分析（实测）**：
+1. **双冷却节奏**：`_last_fuse_time` 独立冷却(:92)，L2→L3 融合频率受冷却限制——每冷却周期只消化一批；
+2. **构图成本高**：`_build_knowledge_association_graph`（圈复杂度133，全库Top12）每次融合全量构图，L2=8178 时单次构图代价随规模上涨——**吞吐随库存增长而下降**，这正是 L3 只出不进的动力学；
+3. **质量门控单点**：`_assess_node_quality` 拒绝即丢弃本轮，无重试排队。
+**建议**（供后续批次，本次只记录不改）：构图改增量（只算新增节点的关联边）；被拒节点进 `retry_queue` 而非直接丢弃；融合节拍与队列水位联动（L2>8000 时冷却减半）。
+
+## 2.2 巨石化检测器（文件度量型）
+
+```python
+def _detect_god_file(self, organ_data: dict, issues: list) -> None:
+    """★复用_scan_all_organs()的AST缓存，每文件多算一次行数+每方法分支数，
+    增量成本≈0（分支计数在解析缓存上做）。"""
+    _THRESH = {"file_lines_warn": 3000, "file_lines_high": 5000,
+               "method_branches_warn": 80, "method_branches_high": 120,
+               "method_count_warn": 120}
+    for _file, _data in organ_data.items():
+        _lines = _data.get("total_lines", 0)
+        if _lines >= _THRESH["file_lines_high"]:
+            issues.append({...  "type": "god_file", "severity": "high",
+                "description": f"{_file} 达 {_lines} 行（阈值5000）",
+                "suggestion": "按SECTION分区启动mixin拆分（见后续深度分析报告任务三）"})
+        for _m in _data.get("methods", []):
+            _b = _count_branches(_m)     # ast.walk计数 if/for/while/except/BoolOp
+            if _b >= _THRESH["method_branches_high"]:
+                issues.append({... "type": "god_method", "severity": "high",
+                    "description": f"{_file}:{_m.lineno} {_m.name} 分支数={_b}（阈值120）"})
+```
+
+**阈值设定依据（用实测数据反推，避免拍脑袋）**：行数 warn=3000 / high=5000（当前Top10全部命中→初始即产出Top10问题清单）；分支 warn=80 / high=120（当前max=285，Top6全部>160→首扫即暴露全部热点）。**重点监控Top10**（第一轮报告§2.3表）：PulseInnerWorld 23249 / SafeEvolutionExecutor 5878 / PulseSubconscious 4759 / self_inspector 4260 / PulseCodeLearner 4138 / PulseLiver 3725 / PatchManager 3791 / PulseCortex 3568 / PulseController 3529 / PulseNodePool 3441。
+
+**周期**：每次全库扫描附带（有 skip_detectors 下推机制兜底，自主进化链路只消费 high/medium，低值告警不会拖慢主链）。
+
+## 2.3 静默异常增长率检测器（趋势对比型）
+
+```python
+def _detect_silent_growth(self, issues: list) -> None:
+    """★不重复扫描：复用本轮 _check_silent_exception 的命中计数，与基线文件对比。"""
+    _baseline_p = os.path.join(_PROJECT_ROOT, "data", "self_inspector", "silent_baseline.json")
+    _cur = {"total": <本轮命中数>, "ts": time.time()}
+    _old = json.load(open(_baseline_p)) if os.path.exists(_baseline_p) else None
+    if _old:
+        _delta = _cur["total"] - _old["total"]
+        if _delta > 0:
+            issues.append({... "type": "silent_exception_growth", "severity": "high",
+                "description": f"静默异常 {old}->{cur}（+{_delta}），pre-commit hook 之外出现新增",
+                "suggestion": "定位新增文件并纳入下一批清偿；检查hook是否被--no-verify绕过"})
+        elif _delta > -20 and (cur_ts - old_ts) > 7*86400:
+            issues.append({... "type": "silent_digestion_slow", "severity": "medium",
+                "description": f"静默清偿速率不足：7天仅消化{abs(_delta)}处（存量{cur}，速率目标≥20/周）",
+                "suggestion": "提升脚本化改造占比（见后续深度分析报告任务三3.2）"})
+    json.dump(_cur, open(_baseline_p, "w"))
+```
+
+**指标与周期**：每次全库扫描对比一次（写入基线文件 data/self_inspector/silent_baseline.json）；两条规则——**新增>0即high**（hook之外的新增意味着流程被绕过）＋**7天消化<20处即medium**（按任务三3.2提速后目标≥100/周，阈值应随路线图上调）。
+
+## 2.4 接入方案与性能影响
+
+| 检测器 | 形态 | 性能成本 | 开关（config新增） |
+|--------|------|---------|-------------------|
+| l3_inversion | RUNTIME | <1ms（读一个json） | `ENABLE_INSPECTOR_L3_INVERSION = True` |
+| god_file | FILE_METRIC | ≈0（复用AST缓存与既有方法遍历） | `ENABLE_INSPECTOR_GOD_FILE = True` |
+| silent_growth | TREND | ≈0（复用本轮计数） | `ENABLE_INSPECTOR_SILENT_TREND = True` |
+
+- 三者默认开启但**均有config开关**，与项目"预埋设计原则"（ENABLE_开关143个的既有惯例）一致；
+- 均不进 `skip_detectors` 高耗时黑名单（dead_code 9分钟的教训：新增检测器必须保证被跳过时零残留状态）；
+- 问题类型登记进 `:294` 的问题说明字典，保证"每条发现必须说明什么输入/时序下会导致什么后果"的既有规范；
+- **回归验证**：m95门禁模式——新增单测用伪造 organ_data/runtime_state 断言三类检测器输出（预计3个测试文件约30用例）。
+
+---
+
+# 任务三：巨石化+静默异常重构方案（P2）
+
+## 3.1 PulseInnerWorld 拆分方案
+
+**拆分原则（按实测结构定）**：**沿21个SECTION天然缝拆，采用 Mixin 继承而非服务对象**。理由：①367个方法大量共享 `self._xxx` 状态，服务对象方案需先做状态归属审计+改写全部访问路径（风险大）；②第30批"从_on_inference_request提取推理检测器"已是 Mixin 式先例，项目有成功经验；③Mixin不改变 `PulseInnerWorld` 的导出路径与 organ_loader 加载方式（[organ_loader.py](../nucleus/organ_loader.py) 零改动）。
+
+**阶段0（前置，必须先做）：属性归属审计脚本**
+写一次性 stdin 脚本（不落盘进框架）：AST 扫描 367 个方法，输出 `属性 → 读写分区` 矩阵。**跨≥3个分区写入的属性**（预期是 `self._nodes`、缓存类、统计计数器）列为"共享状态"，留在主类；其余随分区走。这一步把拆分风险从"感觉"变成"清单"。
+
+**分五阶段（按"最独立、最边缘"→"最核心"排序，每阶段一个文件）**：
+
+| 阶段 | 拆出内容 | 行段 | 预估行数 | Mixin文件 | 风险 |
+|------|---------|------|---------|-----------|------|
+| 1 | 可验证推理证据链 | 9986-16080 | ~6094 | `innerworld/evidence_chain.py` | 低（最独立） |
+| 2 | 真多步推理 | 18583-22813 | ~4230 | `innerworld/multi_step.py` | 低 |
+| 3 | 矛盾仲裁+知识免疫系统 | 3540-5769 + 16080-16095 | ~2250 | `innerworld/arbitration.py` | 中（仲裁预埋段与证据链有调用） |
+| 4 | 知识检索+本地多节点融合 | 7610-9986 | ~2376 | `innerworld/knowledge.py` | 中（`_knowledge_retrieve` 165分支、被多处调用） |
+| 5 | QICA执行器+推理缓存+共振+统计 | 5769-7610 + 22813-23251 | ~1800 | `innerworld/support.py` | 低 |
+| 残留 | 主类：脉冲入口+事件处理+推理路由+框架注入 | 1-3540 + 预留 | ~6000 | PulseInnerWorld.py 本体 | — |
+
+**`_on_inference_request`（285分支）专项**：不按任务书字面的"3个子模块"硬拆，按实测结构拆为**沿既有缝的四级流水**（第30批已提取"推理检测器"段2018-2384，证明此路可通）：
+1. **路由层**（保留主类）：意图分类→选路径，目标分支数<60；
+2. **检索供给**（阶段4的 knowledge.py 提供）：`_knowledge_retrieve`+`_route_to_deriver` 输入准备；
+3. **推理执行**（阶段2的 multi_step.py / 阶段1的 evidence_chain.py 提供）；
+4. **表达生成**（阶段5 support.py 的缓存+格式化）。
+主方法瘦身为"编排器"，只做调用与结果合并——**拆的是被调用方，不是调用方**，这是285分支函数唯一低风险的下刀方式。
+
+**不能动的地方（风险红线）**：
+- 器官注册与脉冲订阅顺序（构造期行为， organ_loader 依赖初始化时序）；
+- 所有 `from organs.brain.PulseInnerWorld import PulseInnerWorld` 的外部导入路径（grep 确认后列入冻结清单）；
+- P3-1 公开访问器分区（16095-18583，它就是为"消除跨模块私有穿透"而设的公共契约）；
+- `_on_inference_request` 的对外签名与返回结构（推理链路契约）。
+
+**每阶段回归验证（四道闸，全部复用现有门禁）**：
+1. `python -m pytest tests` 全量 4214 例；
+2. m95式批次门禁单测 + ruff F=0 + py_compile；
+3. **接口签名冻结校验**：stdin 脚本对比拆分前后 `PulseInnerWorld` 公开方法签名集合（新增允许、删除/改签名即失败）；
+4. 运行时冒烟：重启框架观察 pulse_errors=0、self_inspector 无新增 high 问题、runtime_state 器官 65/65 running。
+
+**每阶段工作量**：阶段0=0.5批；阶段1-5各≈1.5批（搬移+测试+门禁）；**合计≈8批**。
+
+## 3.2 静默异常提速方案
+
+**现状**：1416处（门禁口径）/386处（严格 pass 口径），每批~30处人工改。
+
+**模式分类（决定自动化比例）**：
+- **模式A（可脚本预改，预估占~70%）**：`except ...: pass` 且上下文是"探测/兜底/可忽略"——机械替换为 `silent_exc(e, where="文件:函数")`（[nucleus/_silent_except.py](../nucleus/_silent_except.py) 基建现成，第78批已建，带灰度开关 `enable_silent_except_logging`）；
+- **模式B（半自动，~20%）**：`except: <无日志continue/return>`——脚本替换模板+人工补 where 语义；
+- **模式C（纯人工，~10%）**：pass 本身是错误处理缺失（如 PatchManager 类核心路径）——人工判断补日志/重抛。
+
+**批量改造脚本设计（一次性工具，放 tools/，不进框架）**：
+
+```python
+# tools/batch_fix_silent_except.py —— 方案骨架
+# 输入：目标文件清单（优先级排序）；动作：AST定位 except-pass → 替换为 silent_exc 调用
+# 产出：逐文件 unified diff 到 tmp/silent_fix_diff/，【不直接写回】
+# 人审：抽查20% diff + 全量跑门禁 → 通过后 git apply
+```
+
+**优先级排序**（按"故障排查价值×调用频度"）：
+1. nucleus/reasoning/（PatchManager 8 + ReasoningWorkerPool 8 + SafeEvolutionExecutor 裸except 7）——进化主链路，静默=进化失明；
+2. nucleus/mnemosyne/PulseNodePool.py（21处，全库最高）——记忆层；
+3. nucleus/fast_ops.py、self_inspector.py、logger 自身——基建层；
+4. organs/（先 body/brain 主器官）；
+5. functions/、tools/（边缘最后）。
+
+**预期提速（实事求是）**：脚本预改+人审模式每批可处理 **100-150处**（脚本生成diff分钟级，人工成本集中在20%抽查与模式C），是现状的3-5倍；**1416存量 ≈ 8-10批清完**（vs 现在需要40+批）。增速端已被 pre-commit hook（第126批）封死，存量消化完成后 CI"新增=0"门禁自动变成"总量=0"门禁。
+
+**风险与回滚**：silent_exc 只加 DEBUG 日志不改控制流（零行为变化，hook 与 CI 双保险）；每批改造独立提交，异常时可单批 revert；灰度开关 `enable_silent_except_logging` 可全局关闭新日志。
+
+## 3.3 重构路线图
+
+```mermaid
+gantt
+    title 巨石化+静默异常重构路线（约15-18批，与常规批次并行）
+    dateFormat YYYY-MM-DD
+    section 任务一 隐私（先行，开源前置）
+    日志脱敏层           :p1, 2026-09-29, 1d
+    出站白名单           :p2, after p1, 1d
+    人脸加密+路径白名单   :p3, after p2, 1d
+    section 任务二 检测器
+    三检测器+单测        :d1, 2026-09-29, 2d
+    section 任务三 静默提速
+    脚本工具+nucleus首扫  :s1, after p1, 2d
+    存量消化8-10批(每批100-150处) :s2, after s1, 20d
+    section 任务三 InnerWorld拆分
+    属性归属审计(阶段0)   :a0, after d1, 1d
+    阶段1 证据链6094行    :a1, after a0, 2d
+    阶段2 多步推理4230行  :a2, after a1, 2d
+    阶段3 仲裁+免疫       :a3, after a2, 2d
+    阶段4 检索+融合       :a4, after a3, 2d
+    阶段5 支撑模块        :a5, after a4, 1d
+```
+
+**先后顺序的依据**：①隐私三件套最先（开源阻塞项，且改动小与重构零冲突）；②检测器第二批上（上线后 InnerWorld 拆分每阶段的" god_file 下降"有客观度量，形成反馈闭环）；③静默提速与拆分**并行**（两者触碰文件集几乎不相交：静默主战场在 nucleus/，拆分主战场在 organs/brain/）；④InnerWorld 按"先边缘后核心"五阶段，每阶段独立可回滚。
+
+**里程碑**：
+- **M1**（+3批）：隐私MVP完成，可安全开源；检测器上线，god_file/silent趋势进周报；
+- **M2**（+8批）：静默存量<400；InnerWorld <17000行（阶段1-2完成）；
+- **M3**（+15-18批）：静默≈0，hook门禁升级为"总量=0"；InnerWorld <6500行（主类），`_on_inference_request` 分支数<60，L3/L1回落至告警线内。
+
+**总工期**：约 **15-18批**（含验证批次余量）；其中纯重构占约13批，其余为工具/测试/审计。
+
+---
+
+## 附：本次分析的方法声明
+
+- 全部现状数据为 2026-09-26 只读实测（文件行数/检测器清单/SECTION分段/配置项均逐一核实），方案中的"预估"（自动化比例70%、提速3-5倍、批次工期）为基于实测分布的工程估算，属推测判断，需在阶段0/首批试点后校准；
+- 所有代码骨架为方案示意（未落盘、未执行），采纳时需按项目 CODE_STYLE.md 补头部规范与 ruff 全过；
+- 与第一轮报告的衔接：本报告三项分别对应第一轮 Top20 建议的 #1/#2/#3（隐私三件套）、#15（L3检测器）、#6/#7/#12（拆分/静默提速/print），优先级保持一致。
diff --git a/functions/chat/chat_service.py b/functions/chat/chat_service.py
index d56d3b1..d7416f0 100644
--- a/functions/chat/chat_service.py
+++ b/functions/chat/chat_service.py
@@ -5,12 +5,13 @@
 日期: 2026年9月9日
 """
 
-from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化
+import sys
 import threading
 import time
 from collections import deque
 from typing import Any
-import sys
+
+from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化
 
 # ★第80批 T6：启动早期 stdout 重配置为 utf-8+replace，根治 GBK 重定向下 emoji/中文 print 崩溃
 try:
@@ -641,7 +642,7 @@ class ChatService:
                 f"扫描缓存: 命中率{_cs.get('hit_rate', '?')} "
                 f"(命中{_cs.get('hits', 0)}/未命中{_cs.get('misses', 0)}/"
                 f"失效{_cs.get('invalidations', 0)}) "
-                f"L2命中{_cs.get('l2_hits', 0)}/未命中{_cs.get('l2_misses', 0)}"
+                f"扫描二级缓存命中{_cs.get('scan_cache_l2_hits', 0)}/未命中{_cs.get('scan_cache_l2_misses', 0)}"
             )
         except Exception as _se:
             silent_exc(_se, "chat_service.py:584")
diff --git a/nucleus/self_inspector.py b/nucleus/self_inspector.py
index 53398ab..cf2295e 100644
--- a/nucleus/self_inspector.py
+++ b/nucleus/self_inspector.py
@@ -216,9 +216,12 @@ class SelfInspector(SilentLogMixin):
         self._method_body_cache: dict = {}          # (file_path, method_name) -> (result, mtime)
         self._method_body_hits = 0
         self._method_body_misses = 0
-        # ★第64批 T5：二级缓存命中/未命中计数（可观测性）
-        self._l2_hits = 0
-        self._l2_misses = 0
+        # ★第64批 T5：扫描侧二级缓存（organ_file/structure/method 三级缓存）命中/未命中计数（可观测性）
+        #   ★主线第142批 T-142a 改名消歧：原名 _l2_hits/_l2_misses 与「语义缓存 L2」重名，
+        #   日志中两本 L2 账同名造成跨月误读（D138-3）。改名为 _scan_cache_l2_*，语义=L2 扫描缓存，
+        #   与 config.ENABLE_SEMANTIC_CACHE_L2（语义缓存）无关。
+        self._scan_cache_l2_hits = 0
+        self._scan_cache_l2_misses = 0
         self._scan_stats_last_log = 0.0
         # _m64_t2_l2_cache_done
         self._allowed_extensions = [".py", ".md", ".json"]
@@ -1940,7 +1943,7 @@ class SelfInspector(SilentLogMixin):
                 _module_logger.info(
                     f"[SelfInspector] 缓存统计: 命中率={_s['hit_rate']} "
                     f"命中={_s['hits']} 未命中={_s['misses']} 失效={_s['invalidations']} "
-                    f"L2命中={self._l2_hits} L2未命中={self._l2_misses} "
+                    f"扫描L2缓存命中={self._scan_cache_l2_hits} 未命中={self._scan_cache_l2_misses} "
                     f"缓存年龄={_s['cache_age_seconds']:.0f}s"
                 )
             except Exception as _m64_log_e:
@@ -2175,8 +2178,8 @@ class SelfInspector(SilentLogMixin):
         """获取缓存统计信息（命中率/失效次数/缓存大小/年龄）。"""
         _total = self._scan_cache_hits + self._scan_cache_misses
         _hit = (self._scan_cache_hits / _total * 100.0) if _total > 0 else 0.0
-        _l2_total = self._l2_hits + self._l2_misses
-        _l2_hit = (self._l2_hits / _l2_total * 100.0) if _l2_total > 0 else 0.0
+        _l2_total = self._scan_cache_l2_hits + self._scan_cache_l2_misses
+        _l2_hit = (self._scan_cache_l2_hits / _l2_total * 100.0) if _l2_total > 0 else 0.0
         return {
             "hits": self._scan_cache_hits,
             "misses": self._scan_cache_misses,
@@ -2184,9 +2187,9 @@ class SelfInspector(SilentLogMixin):
             "hit_rate": f"{_hit:.1f}%",
             "cache_size": len(self._scan_cache),
             "cache_age_seconds": (time.time() - self._scan_cache_time) if self._scan_cache else 0.0,
-            "l2_hits": self._l2_hits,
-            "l2_misses": self._l2_misses,
-            "l2_hit_rate": f"{_l2_hit:.1f}%",
+            "scan_cache_l2_hits": self._scan_cache_l2_hits,
+            "scan_cache_l2_misses": self._scan_cache_l2_misses,
+            "scan_cache_l2_hit_rate": f"{_l2_hit:.1f}%",
             # ★主线第65批 T3/P2：get_method_body 文件级缓存统计（绑文件 mtime）
             "method_body_hits": self._method_body_hits,
             "method_body_misses": self._method_body_misses,
@@ -2218,9 +2221,9 @@ class SelfInspector(SilentLogMixin):
         if self._scan_cache_enabled and organ_tag:
             _fc = self._organ_file_cache.get(organ_tag)
             if _fc is not None and _fc[1] == self._scan_cache_time:
-                self._l2_hits += 1
+                self._scan_cache_l2_hits += 1
                 return _fc[0]
-            self._l2_misses += 1
+            self._scan_cache_l2_misses += 1
         if not organ_tag:
             return None
         _file = None
@@ -2267,9 +2270,9 @@ class SelfInspector(SilentLogMixin):
         if self._scan_cache_enabled:
             _sc = self._structure_cache.get(organ_name)
             if _sc is not None and _sc[1] == self._scan_cache_time:
-                self._l2_hits += 1
+                self._scan_cache_l2_hits += 1
                 return _sc[0]
-            self._l2_misses += 1
+            self._scan_cache_l2_misses += 1
         all_organs = self._scan_all_organs()
         
         if organ_name:
@@ -2303,9 +2306,9 @@ class SelfInspector(SilentLogMixin):
         if self._scan_cache_enabled:
             _mc = self._method_info_cache.get((organ_name, method_name))
             if _mc is not None and _mc[1] == self._scan_cache_time:
-                self._l2_hits += 1
+                self._scan_cache_l2_hits += 1
                 return _mc[0]
-            self._l2_misses += 1
+            self._scan_cache_l2_misses += 1
         all_organs = self._scan_all_organs()
         organ_info = all_organs.get(organ_name)
         if not organ_info:
diff --git a/tests/test_method_body_cache_m65.py b/tests/test_method_body_cache_m65.py
index 8ea517d..52661eb 100644
--- a/tests/test_method_body_cache_m65.py
+++ b/tests/test_method_body_cache_m65.py
@@ -30,8 +30,8 @@ def _make_si():
     _si._scan_cache_invalidations = 0
     _si._scan_cache = {}
     _si._scan_cache_time = 0.0
-    _si._l2_hits = 0
-    _si._l2_misses = 0
+    _si._scan_cache_l2_hits = 0
+    _si._scan_cache_l2_misses = 0
     return _si
 
 
diff --git a/tests/test_organ_scan_cache_m64.py b/tests/test_organ_scan_cache_m64.py
index 9e7cd05..3913441 100644
--- a/tests/test_organ_scan_cache_m64.py
+++ b/tests/test_organ_scan_cache_m64.py
@@ -32,8 +32,8 @@ def _fresh_inspector():
     inst._organ_file_cache = {}
     inst._structure_cache = {}
     inst._method_info_cache = {}
-    inst._l2_hits = 0
-    inst._l2_misses = 0
+    inst._scan_cache_l2_hits = 0
+    inst._scan_cache_l2_misses = 0
     inst._scan_stats_last_log = 0.0
     # ★主线第90批（顺手修存量）：第65批 T3/P2 在 __init__ 里新增了
     #   get_method_body 文件级缓存的三个统计属性，而本用例走 __new__ 绕开
@@ -172,7 +172,7 @@ class TestScanCacheTTL(unittest.TestCase):
         inst._scan_all_organs()
         s = inst.get_scan_cache_stats()
         for k in ("hits", "misses", "invalidations", "hit_rate", "cache_size",
-                  "cache_age_seconds", "l2_hits", "l2_misses", "l2_hit_rate"):
+                  "cache_age_seconds", "scan_cache_l2_hits", "scan_cache_l2_misses", "scan_cache_l2_hit_rate"):
             self.assertIn(k, s, f"统计缺少字段 {k}")
         self.assertEqual(s["hits"], 1)
         self.assertTrue(s["hit_rate"].endswith("%"))
@@ -204,18 +204,18 @@ class TestL2CacheVersionStamp(unittest.TestCase):
         r1 = inst.resolve_organ_file("PulseHeart")
         r2 = inst.resolve_organ_file("PulseHeart")
         self.assertEqual(r1, r2)
-        self.assertEqual(inst._l2_hits, 1)  # 第二次走二级缓存命中
-        self.assertEqual(inst._l2_misses, 1)  # 第一次未命中触发扫描
+        self.assertEqual(inst._scan_cache_l2_hits, 1)  # 第二次走二级缓存命中
+        self.assertEqual(inst._scan_cache_l2_misses, 1)  # 第一次未命中触发扫描
 
     def test_10_l2_invalidated_on_scan_refresh(self):
         inst = self._inst_with_scan()
         inst.resolve_organ_file("PulseHeart")
-        self.assertEqual(inst._l2_hits, 0)
+        self.assertEqual(inst._scan_cache_l2_hits, 0)
         # 扫描缓存刷新（版本戳变化）→ 二级缓存应失效
         inst._scan_cache_time = time.time() + 1.0
         r = inst.resolve_organ_file("PulseHeart")
         self.assertIsNotNone(r)
-        self.assertEqual(inst._l2_misses, 2, "版本戳变化后应再次未命中")
+        self.assertEqual(inst._scan_cache_l2_misses, 2, "版本戳变化后应再次未命中")
 
 
 if __name__ == "__main__":
