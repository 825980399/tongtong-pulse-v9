"""验证第67批修复：FULL_SAVE_INTERVAL和INCREMENTAL_MAX_NODES读取config"""
import sys
sys.path.insert(0, r'D:\xinrenlei\tongtong-pulse-v9')

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot

# 创建实例
snap = PulseSnapshot()

# 验证方法存在
print("=== 验证新增方法 ===")
print(f"  ✅ _m67_full_save_interval 存在: {hasattr(snap, '_m67_full_save_interval')}")
print(f"  ✅ _m67_incremental_max_nodes 存在: {hasattr(snap, '_m67_incremental_max_nodes')}")

# 验证读取config的值
print("\n=== 验证读取config的值 ===")
full_interval = snap._m67_full_save_interval()
incremental_max = snap._m67_incremental_max_nodes()
print(f"  FULL_SAVE_INTERVAL (类级常量): {snap.FULL_SAVE_INTERVAL}")
print(f"  SNAPSHOT_FULL_SAVE_INTERVAL (config): {full_interval}")
print(f"  INCREMENTAL_MAX_NODES (类级常量): {snap.INCREMENTAL_MAX_NODES}")
print(f"  SNAPSHOT_INCREMENTAL_MAX_NODES (config): {incremental_max}")

# 验证是否正确读取了config的值（应该是3600和200，不是600和50）
print("\n=== 验证结果 ===")
if full_interval == 3600:
    print(f"  ✅ FULL_SAVE_INTERVAL正确读取config: {full_interval}秒（任务书要求3600）")
else:
    print(f"  ❌ FULL_SAVE_INTERVAL未正确读取config: {full_interval}秒（期望3600）")

if incremental_max == 200:
    print(f"  ✅ INCREMENTAL_MAX_NODES正确读取config: {incremental_max}个（任务书要求200）")
else:
    print(f"  ❌ INCREMENTAL_MAX_NODES未正确读取config: {incremental_max}个（期望200）")

# 验证类级常量仍然保留（可回退）
print("\n=== 验证可回退性 ===")
if snap.FULL_SAVE_INTERVAL == 600:
    print("  ✅ 类级常量FULL_SAVE_INTERVAL保留为600（config缺失时可回退）")
if snap.INCREMENTAL_MAX_NODES == 50:
    print("  ✅ 类级常量INCREMENTAL_MAX_NODES保留为50（config缺失时可回退）")

print("\n=== 验证完成 ===")
