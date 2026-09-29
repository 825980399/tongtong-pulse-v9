"""读取技术债务清单中胃JSON解析问题的记录"""

file_path = r'<PROJECT_ROOT>\docs\完整进化路线与技术债务清单_v1.0.md'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"文件总行数: {len(lines)}")
print("\n=== 第12760-12800行 ===")
for i in range(12759, min(12800, len(lines))):
    print(f"{i+1}: {lines[i].rstrip()}")

print("\n=== 第12880-12910行 ===")
for i in range(12879, min(12910, len(lines))):
    print(f"{i+1}: {lines[i].rstrip()}")
