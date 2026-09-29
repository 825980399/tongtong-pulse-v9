"""修复FAISS_USE_GPU配置和注释"""

file_path = r'<PROJECT_ROOT>\config.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 替换注释和FAISS_USE_GPU
old_block = '''# ★T3/P1：FAISS 向量库。★依赖未安装（faiss ModuleNotFoundError）→ 自动回退暴力余弦。
ENABLE_FAISS_VECTOR_STORE = True
FAISS_INDEX_TYPE = "auto"
FAISS_INDEX_PATH = "data/knowledge/faiss/index.faiss"
FAISS_BATCH_SIZE = 1000
FAISS_USE_GPU = True
FAISS_TRAIN_THRESHOLD = 10000
FAISS_SEARCH_CACHE_SIZE = 1000
VECTOR_DIMENSION = 512'''

new_block = '''# ★T3/P1：FAISS 向量库。faiss-cpu 1.15.0 已安装（2026-09-17），不可用时自动回退暴力余弦。
ENABLE_FAISS_VECTOR_STORE = True
FAISS_INDEX_TYPE = "auto"
FAISS_INDEX_PATH = "data/knowledge/faiss/index.faiss"
FAISS_BATCH_SIZE = 1000
FAISS_USE_GPU = False  # faiss-cpu不支持GPU，必须为False
FAISS_TRAIN_THRESHOLD = 10000
FAISS_SEARCH_CACHE_SIZE = 1000
VECTOR_DIMENSION = 512'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('✅ FAISS_USE_GPU已修改为False，注释已更新')
else:
    print('❌ 未找到目标块，尝试单独替换FAISS_USE_GPU = True')
    # 单独替换
    content = content.replace('FAISS_USE_GPU = True', 'FAISS_USE_GPU = False  # faiss-cpu不支持GPU，必须为False')
    # 更新注释
    content = content.replace('★依赖未安装（faiss ModuleNotFoundError）→ 自动回退暴力余弦', 'faiss-cpu 1.15.0 已安装（2026-09-17），不可用时自动回退暴力余弦')
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('✅ 已通过单独替换完成修改')

# 验证修改
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'FAISS_USE_GPU' in line:
        print(f'  验证 - 第{i+1}行: {line.rstrip()}')
