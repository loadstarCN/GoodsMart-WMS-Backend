import os

exclude_dirs = {'__pycache__', '.git', '.pytest_cache','logs','venv'}
total_files = 0
total_lines = 0

for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for file in files:
        filepath = os.path.join(root, file)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = len(f.readlines())
                total_lines += lines
                total_files += 1
                print(f"{filepath}: {lines} 行")
        except:
            pass  # 忽略二进制文件

print(f"文件总数: {total_files}")
print(f"代码行数: {total_lines}")