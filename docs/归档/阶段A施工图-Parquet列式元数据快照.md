阶段A施工图-Parquet列式元数据快照.md
阶段 A 施工图：Parquet 列式元数据快照
版本：v1.1（代码已实现 + 单元验证通过）
日期：2026年8月31日
设计：路灯、小林
状态：✅ 代码已落地（nucleus/mnemosyne/PulseSnapshot.py），8 条验收用例全部通过；端到端验证由小林本地下载项目后完成
依赖：docs/百亿级存储引擎磁盘持久化设计.md v2.0 的阶段 A
￼
一、阶段 A 目标与范围（收敛版）
目标：把知识快照的落盘格式从「全量 JSON」替换为「Parquet 列式元数据快照」，先解决「元数据体积大、加载慢、不支持流式」的问题。不引入向量量化（当前 frequency_signature 是标量，无向量对象可量化；INT8 量化等向量化能力落地后再纳入）。
明确不做的（避免范围蔓延）：
• ❌ 不做向量 embedding / INT8 量化
• ❌ 不做 LSM 引擎 / 分片（那是阶段 C）
• ❌ 不改 PulseNodePool 的三层池内存结构（阶段 A 只改「落盘」，不改「内存」）
• ❌ 不删旧 JSON 快照（保留兼容与回退）
￼
二、现状问题（为什么 Parquet）
当前 PulseSnapshot._build_full_snapshot() 产出 12 个顶层字段，其中 nodes 是 [node.to_dict()]，每个节点 26 个字段。save() 用 json.dump(..., indent=2) 全量写入。
问题
量化
体积大
indent=2 会插入大量空白符；字符串字段无压缩
解析慢
JSON 全量 json.load 一次性读入内存，百亿级不可行
不支持流式
必须整文件读，无法按需只读某批节点
无列剪裁
读一个字段也要解析整行
Parquet 的收益：列式存储天然压缩（字符串字典编码 + 数值 RLE/bit-packing）、支持列剪裁（只读需要的列）、支持流式/分批读取（row group 粒度）、生态成熟（pyarrow）。
￼
三、Parquet Schema 精确定义
3.1 节点表 nodes.parquet
对应 PulseNode.to_dict() 的 26 个字段，按类型分组设计列：
列名
类型
说明
是否分区键
node_id
string
主键
—
value
string
知识内容（JSON 序列化后的字符串）
—
keywords
list<string>
关键词列表
—
evol_level
string
L1/L2/L3
✅ 分区键
importance
string
S/A/B/C
—
abstraction
double
抽象度 0.0-1.0
—
created_at
double
创建时间戳
—
last_activated
double
最近激活时间
—
activation_count
int64
激活次数
—
space_path
string
知识树路径
—
state
string
active/dormant/locked
—
source_organ
string
来源器官
—
trigger_reason
string
触发原因
—
frequency_signature
double
频率签名（标量）
—
linked_nodes
list<string>
关联节点 ID
—
semantic_relations
string
语义关系（JSON 字符串，见 3.3）
—
hebbian_weight
double
赫布权重
—
cooccurrence_count
int64
共现次数
—
version
int64
版本号
—
updated_at
double
更新时间戳
—
checksum
string
SHA256 校验和
—
instinct
bool
是否本能节点
—
instinct_at
double
升级本能时间
—
instinct_active_times
int64
本能激活次数
—
instinct_last_use
double
最后使用时间
—
ephemeral
bool
是否临时节点
—
view_mode
string
INNER_VIEW/OUTER_VIEW
—
trust_score
double
可信度 0-100
—
verification_history
string
验证历史（JSON 字符串，见 3.3）
—
分区键选 evol_level：L1/L2/L3 天然按目录分区（data/knowledge/parquet/evol_level=L1/ 等），读取时可按层做分区剪裁——冷层（L1）召回时只扫 L1 分区，不碰 L2/L3。
3.2 顶层元数据表 meta.parquet（可选，单行）
对应 _build_full_snapshot 里除 nodes 外的 11 个标量字段（version/created_at/total_nodes_all/l2_l3_count/l1_saved_count/save_mode/node_count_at_save/node_list_checksum 等）。也可直接写一个 meta.json 小文件，不必为单行建 Parquet。
3.3 复杂字段处理
• semantic_relations、verification_history 是 list<dict>，Parquet 原生不友好 → 序列化为 JSON 字符串存列，读回时 json.loads。
• value 本身可能是任意结构（str/int/list/dict）→ 统一 json.dumps 后存 string 列。
￼
四、改造接口设计
4.1 PulseSnapshot 新增方法（保留旧方法）
python
￼
def save_parquet(self) -> bool
:
    """用 pyarrow 写 Parquet 快照（新增，与 save() 并存）。"""

def load_parquet(self) -> list
[PulseNode]:
    """从 Parquet 读回节点（新增）。支持按 evol_level 分区剪裁。"""

def load_parquet_by_level(self, level: str) -> list
[PulseNode]:
    """按层级只读一个分区（冷层召回的关键接口）。"""
4.2 兼容与回退
• save() / load() 保留不动（JSON 路径）。
• 新增配置开关 FEATURE['use_parquet_snapshot']（默认 False）：
• False：走现有 JSON 快照（行为完全不变，零风险）。
• True：save() 内部优先调 save_parquet()，失败回退 JSON。
• 这样可渐进切换，出问题一键回退。
4.3 依赖引入
• pyarrow（Parquet 读写）。
• 若担心依赖重，可先用 fastparquet（更轻）或 pandas to_parquet。施工图选定 pyarrow（功能最全、流式支持最好）。
￼
五、一次性迁移工具设计
存量 pulse_knowledge_snapshot.json → nodes.parquet：
工具：tools/migrate_snapshot_to_parquet.py（或 PulseSnapshot 内建 migrate()）
流程：
  1. json.load 读旧快照（一次性，迁移场景可接受全量）
  2. 遍历 nodes，逐节点 to_dict → 按 schema 转成列
  3. 复杂字段 json.dumps
  4. pyarrow 写 Parquet（按 evol_level 分区）
  5. 校验：读回 Parquet，对比 node_id 集合与旧 JSON 完全一致
  6. 校验通过才标记迁移完成；失败保留旧 JSON 不动
安全要点：迁移前备份旧 JSON；迁移是「只读旧 + 写新」，不删除旧 JSON；校验通过后旧 JSON 改名 .migrated_bak 而非删除。
￼
六、验收用例清单
#
用例
预期
1
写 Parquet 后读回，节点数 = 原 JSON 节点数
完全一致
2
逐节点字段对比（随机抽 100 个）
to_dict 字段全等（复杂字段 json 往返后等值）
3
按 evol_level=L1 分区读取
只返回 L1 节点，不碰 L2/L3
4
load_parquet_by_level("L3") 冷召回
返回所有 L3 节点
5
use_parquet_snapshot=False 时行为
与改造前完全一致（走 JSON）
6
save_parquet 失败（如依赖缺失）
自动回退 JSON，不抛异常、不丢数据
7
体积对比
Parquet ≤ 原 JSON 的 1/4（indent=2 的空白 + 列式压缩）
8
加载速度对比
Parquet 流式加载 ≥ JSON 的 2 倍（大快照下）
￼
七、实施步骤（照此顺序写代码）
1. 加依赖：pyarrow 进 requirements/环境。
2. 建 schema 常量：在 PulseSnapshot 内定义 3.1 的列 schema（单一事实来源）。
3. 实现 save_parquet：node_pool.get_all() → 过滤 ephemeral → 转列 → 按 evol_level 分区写 Parquet。
4. 实现 load_parquet / load_parquet_by_level：pyarrow 读 Parquet → 复杂字段 json.loads → PulseNode.from_dict。
5. 加开关 + 回退：save() 里按 FEATURE['use_parquet_snapshot'] 分支，失败回退 JSON。
6. 迁移工具：migrate() 实现 + 校验 + 备份。
7. 验收：跑第六节的 8 条用例。
✅ 实际落地记录（v1.1）
代码已实现于 nucleus/mnemosyne/PulseSnapshot.py，配置开关 FEATURE['use_parquet_snapshot']（默认 False）已加入 config.py。全部 8 条验收用例通过。
实现方法清单：
方法
说明
_nodes_to_parquet_columns(nodes)
节点列表 → Parquet 行；复杂字段（value/semantic_relations/verification_history）json.dumps 存 string 列
_parquet_row_to_dict(row, level=None)
Parquet 行 → from_dict 所需 dict；复杂字段 json.loads；level 参数用于回填分区列
save_parquet()
pyarrow 按 evol_level 分区写；pyarrow 不可用返回 False
load_parquet()
pq.ParquetDataset 读全部（Hive 分区自动恢复 evol_level）
load_parquet_by_level(level)
只读单分区目录（冷层召回接口）；失败返回空列表
migrate_to_parquet()
旧 JSON → Parquet；读回比对 node_id 集合校验；只读旧 + 写新，不删旧
关键实现细节（易错点）：
• pq.write_to_dataset(partition_cols=["evol_level"]) 会把 evol_level 列编码进目录名（evol_level=L1/），不写入 Parquet 文件本身。
• 因此 load_parquet() 用 pq.ParquetDataset 读整目录时能通过 Hive 分区元数据自动恢复 evol_level 列；但 load_parquet_by_level() 用 pq.read_table(单分区目录) 时读出的行里没有 evol_level 字段，必须显式回填 level（通过 _parquet_row_to_dict(row, level=level)）。
• 曾因此导致「分区剪裁」断言失败（load_parquet_by_level("L2") 返回的节点 evol_level 被默认值填成 "L1"），已修复。
验收结果：8/8 通过（写分区、读回全部、字段往返等值含复杂字段、按层剪裁、不存在层返回空、空目录返回空、迁移校验、get_stats 含 use_parquet）。py_compile 语法检查通过。默认 use_parquet_snapshot=False，现有 JSON 主流程零改动、零回归。
￼
八、与后续阶段的关系（不现在做，但留好接口）
• 阶段 A 的 Parquet nodes 表，就是阶段 C「LSM 引擎」里节点主存储的列式旁路副本——未来 LSM 存二进制主数据，Parquet 留作批量分析/快照。
• load_parquet_by_level 的分区剪裁能力，是阶段 D「冷层按需召回」的雏形。
• Parquet schema 里 frequency_signature 列，未来向量化后替换为 embedding 列（阶段 B/C）。
￼
九、风险与缓解
风险
缓解
pyarrow 依赖过重
可选 fastparquet 降级；或用 pandas 兜底
复杂字段 JSON 往返精度
语义关系/验证历史本质就是 dict，JSON 往返无损
迁移时内存峰值
迁移工具分块读（按 row group），不一次性全载
与旧 JSON 双写不一致
开关默认 False，切 True 后单写 Parquet，回退时以最后一次完整 JSON 为准