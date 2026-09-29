"""修复第67批未完成项：PulseSnapshot中FULL_SAVE_INTERVAL和INCREMENTAL_MAX_NODES读取config"""
import sys

file_path = r'<PROJECT_ROOT>\nucleus\mnemosyne\PulseSnapshot.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 在_m67_batch_size方法后面添加两个新方法
old_batch_end = '''        except Exception:
            return 1000

    def _save_async(self) -> bool:'''

new_batch_end = '''        except Exception:
            return 1000

    def _m67_full_save_interval(self) -> int:
        """★T1：全量保存间隔（秒）。读 config.SNAPSHOT_FULL_SAVE_INTERVAL，默认回退类级常量 600。"""
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_FULL_SAVE_INTERVAL", self.FULL_SAVE_INTERVAL))
        except Exception:
            return self.FULL_SAVE_INTERVAL

    def _m67_incremental_max_nodes(self) -> int:
        """★T1：增量保存最大变更节点数。读 config.SNAPSHOT_INCREMENTAL_MAX_NODES，默认回退类级常量 50。"""
        try:
            import config as _cfg67
            return int(getattr(_cfg67, "SNAPSHOT_INCREMENTAL_MAX_NODES", self.INCREMENTAL_MAX_NODES))
        except Exception:
            return self.INCREMENTAL_MAX_NODES

    def _save_async(self) -> bool:'''

if old_batch_end in content:
    content = content.replace(old_batch_end, new_batch_end)
    print("✅ 已添加_m67_full_save_interval和_m67_incremental_max_nodes方法")
else:
    print("❌ 未找到_m67_batch_size结束位置")
    sys.exit(1)

# 2. 替换使用self.FULL_SAVE_INTERVAL的地方（第416行）
old_usage1 = '_seconds_since_full = _now - self._last_full_save_time if hasattr(self, \'_last_full_save_time\') else self.FULL_SAVE_INTERVAL + 1'
new_usage1 = '_seconds_since_full = _now - self._last_full_save_time if hasattr(self, \'_last_full_save_time\') else self._m67_full_save_interval() + 1'

if old_usage1 in content:
    content = content.replace(old_usage1, new_usage1)
    print("✅ 已替换第1处self.FULL_SAVE_INTERVAL")
else:
    print("⚠️ 未找到第1处self.FULL_SAVE_INTERVAL使用位置")

# 3. 替换使用self.FULL_SAVE_INTERVAL和self.INCREMENTAL_MAX_NODES的地方（第425-426行）
old_usage2 = '''            _use_incremental = (
                _seconds_since_full < self.FULL_SAVE_INTERVAL 
                and _changed_count <= self.INCREMENTAL_MAX_NODES'''

new_usage2 = '''            _use_incremental = (
                _seconds_since_full < self._m67_full_save_interval()
                and _changed_count <= self._m67_incremental_max_nodes()'''

if old_usage2 in content:
    content = content.replace(old_usage2, new_usage2)
    print("✅ 已替换第2处self.FULL_SAVE_INTERVAL和self.INCREMENTAL_MAX_NODES")
else:
    print("⚠️ 未找到第2处使用位置，尝试更宽松的匹配...")
    # 尝试更宽松的匹配（可能有空格差异）
    if 'self.FULL_SAVE_INTERVAL' in content and 'self.INCREMENTAL_MAX_NODES' in content:
        # 只替换这两个变量名（在_save_locked方法中）
        # 注意：类级常量定义处不能替换
        content = content.replace(
            '_seconds_since_full < self.FULL_SAVE_INTERVAL',
            '_seconds_since_full < self._m67_full_save_interval()'
        )
        content = content.replace(
            '_changed_count <= self.INCREMENTAL_MAX_NODES',
            '_changed_count <= self._m67_incremental_max_nodes()'
        )
        print("✅ 已通过宽松匹配替换使用位置")
    else:
        print("❌ 未找到任何使用位置")

# 写回文件
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n✅ 文件已更新")
