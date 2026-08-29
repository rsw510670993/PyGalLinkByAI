# Getchu缩略图功能使用指南

## 📖 快速开始

### 1. 查看帮助信息

```bash
# 查看缩略图下载命令帮助
python tool/cli.py spider download_thumbnails --help

# 输出:
# usage: cli.py spider download_thumbnails [-h] [--limit LIMIT] [--batch-size BATCH_SIZE]
#
# options:
#   -h, --help            show this help and exit
#   --limit LIMIT         一次下载的缩略图数量 (默认: 1000)
#   --batch-size BATCH_SIZE  每批处理的数量 (默认: 100)
```

### 2. 下载缩略图

```bash
# 下载所有待下载的缩略图（默认最多1000个）
python tool/cli.py spider download_thumbnails

# 下载指定数量的缩略图
python tool/cli.py spider download_thumbnails --limit 500

# 调整批量处理大小
python tool/cli.py spider download_thumbnails --limit 200 --batch-size 50
```

## 🔍 常用操作

### 检查缩略图状态

```bash
# 方法1: 使用SQLite直接查询
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('getchu.db')
cursor = conn.cursor()

# 统计信息
cursor.execute('''
    SELECT 
        COUNT(*) as total,
        COUNT(getchu_id) as with_gid,
        COUNT(thumb_url) as with_thumb_url,
        COUNT(thumb_path) as with_thumb_path
    FROM getchu_games
''')

stats = cursor.fetchone()
print(f"总游戏数: {stats[0]}")
print(f"有Getchu ID: {stats[1]}")
print(f"有缩略图URL: {stats[2]}")
print(f"有缩略图文件: {stats[3]}")

conn.close()
EOF

# 方法2: 检查文件目录
ls -la thumbnails/*.jpg | wc -l
```

### 查看待下载的游戏

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('getchu.db')
cursor = conn.cursor()

cursor.execute('''
    SELECT getchu_id, name, thumb_url 
    FROM getchu_games 
    WHERE thumb_url IS NOT NULL 
      AND (thumb_path IS NULL OR thumb_path = '')
    LIMIT 10
''')

print("待下载缩略图的游戏:")
for row in cursor.fetchall():
    print(f"GID: {row[0]}, Name: {row[1][:40]}")

conn.close()
EOF
```

### 手动触发单个缩略图下载

```bash
# 如果需要测试单个游戏的缩略图下载
python3 << 'EOF'
import requests
import os

gid = "1260822"  # 替换为实际的Getchu ID
thumb_url = f"https://www.getchu.com/brandnew/{gid}/rc{gid}package.jpg"
thumb_dir = "thumbnails"
os.makedirs(thumb_dir, exist_ok=True)

headers = {"Referer": f"https://www.getchu.com/soft.phtml?id={gid}"}
response = requests.get(thumb_url, headers=headers, timeout=30)

if response.status_code == 200:
    local_path = os.path.join(thumb_dir, f"{gid}.jpg")
    with open(local_path, 'wb') as f:
        f.write(response.content)
    print(f"✅ 下载成功: {local_path} ({len(response.content)} bytes)")
else:
    print(f"❌ 下载失败: HTTP {response.status_code}")
EOF
```

## 🛠️ 高级操作

### 批量修复缺失的缩略图URL

```bash
python3 << 'EOF'
import sqlite3

conn = sqlite3.connect('getchu.db')
cursor = conn.cursor()

# 为有getchu_id但没有thumb_url的游戏生成URL
cursor.execute('''
    SELECT getchu_id, name 
    FROM getchu_games 
    WHERE getchu_id IS NOT NULL 
      AND (thumb_url IS NULL OR thumb_url = '')
''')

games = cursor.fetchall()
print(f"需要修复的游戏数: {len(games)}")

for gid, name in games:
    thumb_url = f"https://www.getchu.com/brandnew/{gid}/rc{gid}package.jpg"
    cursor.execute('UPDATE getchu_games SET thumb_url = ? WHERE getchu_id = ?', (thumb_url, gid))

conn.commit()
print(f"✅ 修复完成")

conn.close()
EOF
```

### 清理无效的缩略图记录

```bash
python3 << 'EOF'
import sqlite3
import os

conn = sqlite3.connect('getchu.db')
cursor = conn.cursor()

# 找出数据库中记录存在但文件不存在的记录
cursor.execute('SELECT getchu_id, thumb_path FROM getchu_games WHERE thumb_path IS NOT NULL')

invalid_records = []
for row in cursor.fetchall():
    gid, path = row
    if not os.path.exists(path):
        invalid_records.append((gid, path))

print(f"发现 {len(invalid_records)} 条无效记录")

# 清理这些记录
for gid, path in invalid_records:
    cursor.execute('UPDATE getchu_games SET thumb_path = NULL WHERE getchu_id = ?', (gid,))
    print(f"清理: {gid}")

conn.commit()
conn.close()
print("✅ 清理完成")
EOF
```

## 📊 监控和报告

### 生成缩略图下载报告

```bash
python3 << 'EOF'
import sqlite3
import os
from datetime import datetime

conn = sqlite3.connect('getchu.db')
cursor = conn.cursor()

# 获取统计信息
cursor.execute('''
    SELECT 
        COUNT(*) as total,
        COUNT(getchu_id) as with_gid,
        COUNT(thumb_url) as with_thumb_url,
        COUNT(thumb_path) as with_thumb_path,
        COUNT(CASE WHEN thumb_url IS NOT NULL AND thumb_path IS NULL THEN 1 END) as pending
    FROM getchu_games
''')

stats = cursor.fetchone()

# 获取文件大小信息
thumb_dir = "thumbnails"
total_size = 0
file_count = 0
if os.path.exists(thumb_dir):
    for filename in os.listdir(thumb_dir):
        if filename.endswith('.jpg'):
            filepath = os.path.join(thumb_dir, filename)
            total_size += os.path.getsize(filepath)
            file_count += 1

# 生成报告
print("📊 Getchu缩略图下载报告")
print("=" * 50)
print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()
print("数据库统计:")
print(f"  总游戏数: {stats[0]:,}")
print(f"  有Getchu ID: {stats[1]:,}")
print(f"  有缩略图URL: {stats[2]:,}")
print(f"  有缩略图文件: {stats[3]:,}")
print(f"  待下载: {stats[4]:,}")
print()
print("文件统计:")
print(f"  文件数量: {file_count}")
print(f"  总大小: {total_size / 1024 / 1024:.2f} MB")
print(f"  平均大小: {total_size / file_count / 1024:.2f} KB" if file_count > 0 else "平均大小: N/A")
print()
print("完成度:")
completion = (stats[3] / stats[2] * 100) if stats[2] > 0 else 0
print(f"  缩略图完成度: {completion:.1f}%")

conn.close()
EOF
```

## 🐛 故障排查

### 缩略图下载失败

```bash
# 检查特定游戏的缩略图URL是否有效
python3 << 'EOF'
import requests

gid = "1260822"  # 替换为实际ID
thumb_url = f"https://www.getchu.com/brandnew/{gid}/rc{gid}package.jpg"
headers = {"Referer": f"https://www.getchu.com/soft.phtml?id={gid}"}

try:
    response = requests.head(thumb_url, headers=headers, timeout=10)
    print(f"URL: {thumb_url}")
    print(f"状态码: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'N/A')}")
    print(f"Content-Length: {response.headers.get('Content-Length', 'N/A')}")
    
    if response.status_code == 200:
        print("✅ URL有效")
    else:
        print("❌ URL无效或不存在")
except Exception as e:
    print(f"❌ 检查失败: {str(e)}")
EOF
```

### 数据库字段检查

```bash
python3 << 'EOF'
import sqlite3

conn = sqlite3.connect('getchu.db')
cursor = conn.cursor()

# 检查表结构
cursor.execute('PRAGMA table_info(getchu_games)')
columns = cursor.fetchall()

print("getchu_games 表结构:")
for col in columns:
    print(f"  {col[1]}: {col[2]}")

# 检查必要字段
required = ['getchu_id', 'thumb_url', 'thumb_path', 'detail_url']
existing = [col[1] for col in columns]

print("\n字段检查:")
for field in required:
    status = "✅" if field in existing else "❌ 缺失"
    print(f"  {status} {field}")

conn.close()
EOF
```

## 💡 最佳实践

1. **定期下载**: 建议定期运行缩略图下载命令，保持数据完整性
2. **批量处理**: 对于大量数据，使用合理的批量大小（50-100）避免内存问题
3. **错误监控**: 关注下载失败的记录，及时处理
4. **存储管理**: 定期清理无效的缩略图文件，节省存储空间

## 🔗 相关命令

```bash
# 查看日历数据（包含缩略图统计）
python tool/cli.py calendar

# 查看爬虫状态
python tool/cli.py spider status

# 重试失败的详情抓取（可能包含缩略图信息）
python tool/cli.py spider detail_retry --limit 100
```