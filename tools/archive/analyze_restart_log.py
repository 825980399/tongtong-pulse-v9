"""分析第67批重启后的运行日志，验证性能收益"""
import re
from collections import Counter

log_path = r'D:\xinrenlei\tongtong-pulse-v9\logs\pulse.log'

# 读取日志
with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

print(f"日志总行数: {len(lines)}")

# 找到重启后的日志（2026-09-17 00:38之后）
restart_lines = []
for line in lines:
    if '2026-09-17 00:3' in line or '2026-09-17 00:4' in line or '2026-09-17 00:5' in line:
        restart_lines.append(line)

print(f"重启后日志行数: {len(restart_lines)}")

# 统计日志级别
log_levels = Counter()
for line in restart_lines:
    if 'ERROR' in line:
        log_levels['ERROR'] += 1
    elif 'WARNING' in line:
        log_levels['WARNING'] += 1
    elif 'INFO' in line:
        log_levels['INFO'] += 1
    elif 'DEBUG' in line:
        log_levels['DEBUG'] += 1

print("\n=== 日志级别统计（重启后）===")
for level, count in log_levels.most_common():
    print(f"  {level}: {count}")

# 查找快照保存相关日志
print("\n=== 快照保存相关日志 ===")
for line in restart_lines:
    if 'PulseSnapshot' in line and ('保存' in line or 'save' in line.lower() or '耗时' in line):
        # 提取时间和关键信息
        time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
        time_str = time_match.group(1) if time_match else '??:??:??'
        # 清理乱码，只保留关键信息
        clean_line = line.strip()
        if '全量保存完成' in clean_line or '开始全量保存' in clean_line or 'L1' in clean_line or 'Parquet' in clean_line:
            print(f"  [{time_str}] {clean_line[-150:]}")

# 查找队列深度相关日志
print("\n=== 队列深度相关日志 ===")
queue_count = 0
for line in restart_lines:
    if '队列' in line or 'queue' in line.lower() or 'runtime_metrics' in line:
        queue_count += 1
        if queue_count <= 10:
            time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            time_str = time_match.group(1) if time_match else '??:??:??'
            print(f"  [{time_str}] {line.strip()[-120:]}")
print(f"  ... 共{queue_count}条相关日志")

# 查找ERROR日志
print("\n=== ERROR日志（重启后）===")
error_count = 0
for line in restart_lines:
    if 'ERROR' in line:
        error_count += 1
        if error_count <= 10:
            time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            time_str = time_match.group(1) if time_match else '??:??:??'
            print(f"  [{time_str}] {line.strip()[-150:]}")
if error_count == 0:
    print("  ✅ 无ERROR日志")

# 查找WARNING日志（排除DEBUG中的WARNING）
print("\n=== WARNING日志（重启后，前10条）===")
warning_count = 0
for line in restart_lines:
    if 'WARNING' in line and 'DEBUG' not in line:
        warning_count += 1
        if warning_count <= 10:
            time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            time_str = time_match.group(1) if time_match else '??:??:??'
            print(f"  [{time_str}] {line.strip()[-120:]}")
print(f"  ... 共{warning_count}条WARNING日志")

# 查找性能指标
print("\n=== 性能指标 ===")
for line in restart_lines:
    if '耗时' in line or '秒' in line and ('保存' in line or '调用' in line):
        time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
        time_str = time_match.group(1) if time_match else '??:??:??'
        # 提取耗时
        duration_match = re.search(r'(\d+\.?\d*)s', line)
        duration_str = duration_match.group(1) if duration_match else '?'
        if '保存' in line or '调用' in line:
            print(f"  [{time_str}] 耗时={duration_str}s: {line.strip()[-100:]}")

print("\n=== 分析完成 ===")
