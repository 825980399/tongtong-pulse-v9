"""检查技术债务清单末尾，并追加胃模块JSON解析失败问题"""

file_path = r'<PROJECT_ROOT>\docs\完整进化路线与技术债务清单_v1.0.md'

# 读取文件末尾2000字符
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f"文件总长度: {len(content)} 字符")
print("\n=== 文件末尾1500字符 ===")
print(content[-1500:])

# 检查是否已有第一百六十章
if '第一百六十章' in content:
    print("\n✅ 第一百六十章已存在")
else:
    print("\n❌ 第一百六十章不存在")

# 检查是否已有胃模块JSON解析问题
if '胃模块JSON解析失败' in content or 'PulseStomach JSON策略2' in content:
    print("\n⚠️  胃模块JSON解析问题已存在")
else:
    print("\n📝 胃模块JSON解析问题尚未记录，需要追加")
