#!/usr/bin/env python3
"""
Getchu 详情页抓取与缩略图下载模块
- 解析详情页获取精确发售日、品牌等信息
- 下载包装缩略图到本地（带 Referer 防盗链）
- 支持断点续跑和失败重试
"""

import logging
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .runtime import runtime_paths


logger = logging.getLogger(__name__)
HEADERS_TEMPLATE = {"Referer": "https://www.getchu.com/soft.phtml?id={}"}


def fetch_detail_and_update(gid, game_name, company):
    """
    抓取单个Getchu详情页，解析信息并更新数据库
    
    Args:
        gid (str): Getchu ID
        game_name (str): 游戏名称（用于日志）
        company (str): 制作公司（用于日志）
    
    Returns:
        dict: 抓取结果 {'success': bool, 'data': dict, 'error': str}
    """
    start_time = time.time()
    result = {'success': False, 'data': {}, 'error': None}
    
    try:
        # 构建URL和请求头
        url = f"https://www.getchu.com/soft.phtml?id={gid}"
        headers = {"Referer": f"https://www.getchu.com/soft.phtml?id={gid}"}
        
        logger.info("开始抓取详情: %s (%s) - %s", gid, game_name[:30], company[:20])
        
        # 请求详情页
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = 'euc-jp'
        response.raise_for_status()
        
        # 解析详情页
        soup = BeautifulSoup(response.text, "html.parser")
        detail_data = _parse_detail_page(soup, gid)
        
        if not detail_data:
            raise ValueError("详情页解析失败，未找到有效数据")
        
        # 下载缩略图
        if detail_data.get('thumb_url'):
            thumb_path = _download_thumbnail(gid, detail_data['thumb_url'], headers)
            if thumb_path:
                detail_data['thumb_path'] = thumb_path
                logger.info("✅ 下载缩略图: %s (%.1fKB)", 
                           gid, os.path.getsize(thumb_path)/1024 if os.path.exists(thumb_path) else 0)
        
        result['success'] = True
        result['data'] = detail_data
        
        # 记录耗时
        elapsed = time.time() - start_time
        logger.info("✅ 完成: %s (%.2f秒)", gid, elapsed)
        
    except Exception as e:
        error_msg = f"抓取详情 {gid} 失败: {str(e)}"
        logger.error(error_msg)
        result['error'] = error_msg
        
        # 如果是网络错误，标记为可重试
        if any(keyword in str(e).lower() for keyword in ['timeout', 'connection', 'network']):
            result['retryable'] = True
        
    return result


def _parse_detail_page(soup, gid):
    """
    解析详情页HTML，提取关键信息
    
    Args:
        soup: BeautifulSoup对象
        gid: Getchu ID（用于日志）
    
    Returns:
        dict: 包含解析结果的字典
    """
    data = {}
    
    # 解析信息表格（通过定位"発売日："、"ブランド："等关键词）
    try:
        # 查找所有包含关键字的td
        text_content = soup.get_text()
        
        # 提取发售日（YYYY/MM/DD格式）
        date_patterns = [
            r'発売日[：:]\s*(\d{4}/\d{2}/\d{2})',
            r'発売日[：:]\s*(\d{4}-\d{2}-\d{2})',
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text_content)
            if match:
                date_str = match.group(1)
                data['release_date'] = date_str.replace('-', '/')
                logger.debug("✅ 解析发售日: %s", date_str)
                break
        
        # 提取品牌（制作公司）
        brand_patterns = [
            r'ブランド[：:]\s*([^,\n]+)',
            r'制作会社[：:]\s*([^,\n]+)',
        ]
        for pattern in brand_patterns:
            match = re.search(pattern, text_content)
            if match:
                brand = match.group(1).strip()
                data['company'] = brand
                logger.debug("✅ 解析品牌: %s", brand)
                break
        
        # 提取媒体类型
        media_patterns = [
            r'メディア[：:]\s*([^\n]+)',
            r'媒体[：:]\s*([^\n]+)',
        ]
        for pattern in media_patterns:
            match = re.search(pattern, text_content)
            if match:
                media = match.group(1).strip()
                data['media'] = media
                logger.debug("✅ 解析媒体: %s", media)
                break
        
        # 提取价格（含税）
        price_patterns = [
            r'定価[：:]\s*([^\n]+)',
            r'価格[：:]\s*([^\n]+)',
        ]
        for pattern in price_patterns:
            match = re.search(pattern, text_content)
            if match:
                price = match.group(1).strip()
                data['price'] = price
                logger.debug("✅ 解析价格: %s", price)
                break
        
        # 提取缩略图URL（查找包含"パッケージ画像"的img标签）
        package_img = soup.find('img', {'alt': re.compile(r'パッケージ画像', re.I)})
        if package_img:
            src = package_img.get('src', '')
            if src:
                # 确保URL是完整的
                if src.startswith('/'):
                    src = urljoin('https://www.getchu.com', src)
                data['thumb_url'] = src
                logger.debug("✅ 解析缩略图URL: %s", src)
        
        # 如果解析到缩略图，验证格式
        if data.get('thumb_url'):
            thumb_gid = re.search(r'rc(\d+)package\.jpg', data['thumb_url'])
            if thumb_gid and thumb_gid.group(1) != gid:
                logger.warning("⚠️ 缩略图ID不匹配: 图片gid=%s, 当前gid=%s", 
                              thumb_gid.group(1), gid)
                # 但仍保留，可能是换包装等情况
        
    except Exception as e:
        logger.error("详情页解析异常 (gid=%s): %s", gid, str(e))
        raise
    
    # 返回解析结果
    if data:
        logger.debug("✅ 详情页解析成功 (gid=%s): %s", gid, data)
    else:
        logger.warning("⚠️ 详情页解析结果为空 (gid=%s)", gid)
    
    return data


def _download_thumbnail(gid, url, headers):
    """
    下载缩略图到本地
    
    Args:
        gid: Getchu ID
        url: 图片URL
        headers: 请求头
    
    Returns:
        str: 本地文件路径（相对于项目根目录）
    """
    try:
        # 确保缩略图目录存在
        paths = runtime_paths()
        thumb_dir = paths.get('thumbnail_dir', 'thumbnails')
        os.makedirs(thumb_dir, exist_ok=True)
        
        # 构建本地文件名
        local_filename = f"{gid}.jpg"
        local_path = os.path.join(thumb_dir, local_filename)
        
        # 如果文件已存在且大小合理，跳过下载
        if os.path.exists(local_path) and os.path.getsize(local_path) > 5000:  # 至少5KB
            logger.debug("缩略图已存在: %s", local_path)
            return local_path
        
        # 下载图片
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        
        # 保存到本地
        with open(local_path, 'wb') as f:
            f.write(response.content)
        
        # 设置全局可读权限（Web服务器以www-data用户运行，需要能读取）
        try:
            os.chmod(local_path, 0o644)
        except OSError:
            pass
        
        # 验证文件
        if os.path.getsize(local_path) < 1000:  # 小于1KB可能是错误页面
            logger.warning("⚠️ 下载的缩略图过小: %s (%d bytes)", local_path, os.path.getsize(local_path))
            # 不删除文件，让下次重试覆盖
        
        return local_path
        
    except Exception as e:
        logger.error("下载缩略图失败 (gid=%s, url=%s): %s", gid, url, str(e))
        return None


def batch_update_details(games_batch, conn, start_time=None):
    """
    批量更新游戏详情
    
    Args:
        games_batch: 待更新的游戏列表 [{'gid': str, 'name': str, 'company': str}, ...]
        conn: 数据库连接
        start_time: 开始时间（用于进度计算）
    
    Returns:
        dict: 更新统计
    """
    stats = {
        'total': len(games_batch),
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'retry_failed': 0,
        'retried': 0,
        'start_time': start_time or time.time()
    }
    
    cursor = conn.cursor()
    
    for i, game in enumerate(games_batch, 1):
        try:
            gid = game['gid']
            name = game.get('name', '')
            company = game.get('company', '')
            
            # 检查是否已经完成
            cursor.execute("""
                SELECT detail_fetched FROM getchu_games 
                WHERE getchu_id = ? AND detail_fetched = 1
            """, (gid,))
            if cursor.fetchone():
                logger.debug("⏭️ 已处理过: %s", gid)
                stats['skipped'] += 1
                continue
            
            # 检查重试次数（最多3次）
            cursor.execute("""
                SELECT detail_retry FROM getchu_games 
                WHERE getchu_id = ? 
            """, (gid,))
            row = cursor.fetchone()
            if row and row[0] >= 3:
                logger.debug("⏭️ 超过重试次数: %s", gid)
                stats['retry_failed'] += 1
                continue
            
            # 更新重试次数
            cursor.execute("""
                UPDATE getchu_games SET detail_retry = detail_retry + 1 
                WHERE getchu_id = ?
            """, (gid,))
            conn.commit()
            
            # 执行详情抓取
            result = fetch_detail_and_update(gid, name, company)
            
            if result['success']:
                # 更新数据库
                update_fields = []
                params = []
                
                if 'release_date' in result['data']:
                    update_fields.append("release_date = ?")
                    params.append(result['data']['release_date'])
                
                if 'company' in result['data']:
                    update_fields.append("company = ?")
                    params.append(result['data']['company'])
                
                if 'media' in result['data']:
                    update_fields.append("size = ?")  # 复用size字段
                    params.append(result['data']['media'])
                
                if 'price' in result['data']:
                    update_fields.append("price = ?")
                    params.append(result['data']['price'])
                
                if 'thumb_url' in result['data']:
                    update_fields.append("thumb_url = ?")
                    params.append(result['data']['thumb_url'])
                
                if 'thumb_path' in result['data']:
                    update_fields.append("thumb_path = ?")
                    params.append(result['data']['thumb_path'])
                
                if update_fields:
                    update_fields.append("detail_fetched = 1")
                    update_fields.append("detail_fetched_at = datetime('now', 'localtime')")
                    
                    params.extend([gid])
                    update_sql = f"""
                        UPDATE getchu_games 
                        SET {', '.join(update_fields)}
                        WHERE getchu_id = ?
                    """
                    cursor.execute(update_sql, tuple(params))
                    conn.commit()
                    
                    stats['success'] += 1
                    logger.info("✅ 成功: %s (%d/%d)", gid, stats['success'], stats['total'])
                    
            else:
                # 更新失败状态
                cursor.execute("""
                    UPDATE getchu_games 
                    SET detail_fetched = 2, 
                        detail_fetched_at = datetime('now', 'localtime')
                    WHERE getchu_id = ?
                """, (gid,))
                conn.commit()
                
                if result.get('retryable'):
                    stats['retried'] += 1
                    logger.warning("⚠️ 可重试: %s", gid)
                else:
                    stats['failed'] += 1
                    logger.error("❌ 失败: %s (%s)", gid, result.get('error', '未知错误'))
            
            # 限速：每1-2秒一个
            time.sleep(1.5)
            
            # 进度报告
            if i % 20 == 0 or i == len(games_batch):
                elapsed = time.time() - stats['start_time']
                progress = i / stats['total']
                eta = elapsed / progress - elapsed if progress > 0 else 0
                
                logger.info("进度: %d/%d (%.1f%%) | 成功:%d 失败:%d 跳过:%d | 已时:%.1fs 剩余:%.1fs", 
                          i, stats['total'], progress * 100,
                          stats['success'], stats['failed'], stats['skipped'],
                          elapsed, eta)
        
        except Exception as e:
            logger.error("批量更新异常 (第%d项): %s", i, str(e))
            stats['failed'] += 1
            
            # 继续处理下一项，不中断整个批次
            if conn:
                conn.rollback()
    
    return stats


def get_pending_games(conn, limit=1000):
    """
    获取待处理的游戏列表
    
    Args:
        conn: 数据库连接
        limit: 限制返回数量
    
    Returns:
        list: 待处理游戏列表 [{'gid': str, 'name': str, 'company': str}, ...]
    """
    cursor = conn.cursor()
    
    # 查询有getchu_id但未完成详情抓取的记录
    cursor.execute("""
        SELECT getchu_id, name, company 
        FROM getchu_games 
        WHERE getchu_id IS NOT NULL 
          AND detail_fetched != 1
        ORDER BY date, name
        LIMIT ?
    """, (limit,))
    
    return [
        {'gid': row[0], 'name': row[1], 'company': row[2]}
        for row in cursor.fetchall()
    ]


def batch_download_thumbnails(games_batch, conn, start_time=None, sleep_seconds=0.5):
    """
    批量下载游戏缩略图（基于已有的thumb_url）
    
    Args:
        games_batch: 待下载缩略图的游戏列表 [{'gid': str, 'thumb_url': str, ...}, ...]
        conn: 数据库连接
        start_time: 开始时间（用于进度计算）
        sleep_seconds: 每个请求后的限速秒数
    
    Returns:
        dict: 下载统计
    """
    stats = {
        'total': len(games_batch),
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'start_time': start_time or time.time()
    }
    
    cursor = conn.cursor()
    
    for i, game in enumerate(games_batch, 1):
        try:
            gid = game.get('gid') or game.get('getchu_id')
            # thumb_url 缺失时按 Getchu 固定格式从 gid 兜底构造
            thumb_url = game.get('thumb_url') or (
                f"https://www.getchu.com/brandnew/{gid}/rc{gid}package.jpg" if gid else None
            )
            
            if not gid or not thumb_url:
                stats['skipped'] += 1
                continue
            
            # 检查是否已经下载过
            cursor.execute("""
                SELECT thumb_path FROM getchu_games 
                WHERE getchu_id = ? AND thumb_path IS NOT NULL
            """, (gid,))
            if cursor.fetchone():
                logger.debug("⏭️ 缩略图已存在: %s", gid)
                stats['skipped'] += 1
                continue
            
            # 下载缩略图
            headers = {"Referer": f"https://www.getchu.com/soft.phtml?id={gid}"}
            thumb_path = _download_thumbnail(gid, thumb_url, headers)
            
            if thumb_path:
                # 更新数据库
                cursor.execute("""
                    UPDATE getchu_games 
                    SET thumb_path = ? 
                    WHERE getchu_id = ?
                """, (thumb_path, gid))
                conn.commit()
                
                stats['success'] += 1
                logger.info("✅ 下载缩略图: %s (%d/%d)", gid, stats['success'], stats['total'])
            else:
                stats['failed'] += 1
                logger.warning("⚠️ 下载失败: %s", gid)
            
            # 限速：避免请求过快被Getchu限制
            time.sleep(sleep_seconds)
            
            # 进度报告
            if i % 50 == 0 or i == len(games_batch):
                elapsed = time.time() - stats['start_time']
                progress = i / stats['total']
                eta = elapsed / progress - elapsed if progress > 0 else 0
                
                logger.info("进度: %d/%d (%.1f%%) | 成功:%d 失败:%d 跳过:%d | 已时:%.1fs 剩余:%.1fs", 
                          i, stats['total'], progress * 100,
                          stats['success'], stats['failed'], stats['skipped'],
                          elapsed, eta)
        
        except Exception as e:
            logger.error("批量下载缩略图异常 (第%d项): %s", i, str(e))
            stats['failed'] += 1
    
    return stats


def get_games_without_thumbnails(conn, limit=1000):
    """
    获取有thumb_url但没有thumb_path的游戏列表
    
    Args:
        conn: 数据库连接
        limit: 限制返回数量
    
    Returns:
        list: 待下载缩略图游戏列表
    """
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT getchu_id, thumb_url, name, company 
        FROM getchu_games 
        WHERE thumb_url IS NOT NULL 
          AND (thumb_path IS NULL OR thumb_path = '')
        ORDER BY date DESC
        LIMIT ?
    """, (limit,))
    
    return [
        {'gid': row[0], 'thumb_url': row[1], 'name': row[2], 'company': row[3]}
        for row in cursor.fetchall()
    ]


if __name__ == '__main__':
    # 测试模式：抓取单个详情页
    import sys
    
    if len(sys.argv) > 1:
        gid = sys.argv[1]
        result = fetch_detail_and_update(gid, "测试游戏", "测试公司")
        print(f"测试结果: {result}")
    else:
        print("用法: python getchu_detail.py <getchu_id>")