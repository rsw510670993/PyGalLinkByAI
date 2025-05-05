from flask import Flask, render_template, request, jsonify
import threading
import json
import tool

app = Flask(__name__)

# 全局变量存储爬虫状态
spider_status = {
    'running': False,
    'progress': 0,
    'current_year': None,
    'current_month': None,
    'total_years': 0
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_spider', methods=['POST'])
def start_spider():
    if spider_status['running']:
        return jsonify({'status': 'error', 'message': '爬虫已在运行中'})
    
    data = request.json
    start_year = int(data['start_year'])
    end_year = int(data['end_year'])
    
    spider_status['running'] = True
    spider_status['current_year'] = start_year
    spider_status['total_years'] = end_year - start_year + 1
    spider_status['progress'] = 0
    
    # 在新线程中启动爬虫
    threading.Thread(target=run_spider, args=(start_year, end_year)).start()
    
    return jsonify({'status': 'success', 'message': '爬虫已启动'})

@app.route('/stop_spider', methods=['POST'])
def stop_spider():
    spider_status['running'] = False
    return jsonify({'status': 'success', 'message': '爬虫停止请求已发送'})

@app.route('/get_status', methods=['GET'])
def get_status():
    return jsonify(spider_status)

@app.route('/years')
def get_all_years():
    # 直接获取年份列表
    years = tool.get_years_list()
    
    return jsonify({
        'years': years
    })

@app.route('/games')
def get_games():
    # 从config.json读取分页配置，默认50
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    per_page = config.get('per_page', 50)
    
    # 获取分页参数
    page = request.args.get('page', default=1, type=int)
    
    # 获取筛选参数
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    
    # 获取所有游戏数据
    all_games = tool.get_games_data()
    
    # 应用筛选条件
    if year:
        all_games = [g for g in all_games if g.year == year]
    if month:
        all_games = [g for g in all_games if g.month == month]
        
    total = len(all_games)
    
    # 计算分页
    start = (page - 1) * per_page
    end = start + per_page
    games_data = all_games[start:end]
    
    return jsonify({
        'data': [{
            'year': g.year,
            'month': g.month,
            'name': g.name,
            'company': g.company,
            'download_url': g.link,
            'nyaa_name': g.nyaa_name,
            'comment': g.comment
        } for g in games_data],
        'current_page': page,
        'per_page': per_page,
        'total': total
    })

@app.route('/data')
def data_page():
    return render_template('data.html')

@app.route('/start_download', methods=['POST'])
def start_download():
    try:
        data = request.json
        year = int(data['year'])
        month = int(data['month']) if 'month' in data and data['month'] != '0' else None
        
        if month:
            print(f"开始下载{year}年{month}月的游戏数据")
        else:
            print(f"开始下载{year}年的游戏数据")
        
        # 调用工具模块执行下载
        download_result = tool.download_games_by_month(year, month) if month else all(tool.download_games_by_month(year, m) for m in range(1, 13))
        
        if download_result:
            if month:
                print(f"{year}年{month}月游戏数据下载完成")
                return jsonify({'status': 'success', 'message': f'{year}年{month}月游戏数据下载完成'})
            else:
                print(f"{year}年游戏数据下载完成")
                return jsonify({'status': 'success', 'message': f'{year}年游戏数据下载完成'})
        else:
            if month:
                print(f"{year}年{month}月游戏数据下载失败")
                return jsonify({'status': 'error', 'message': f'{year}年{month}月下载失败，请检查日志'})
            else:
                print(f"{year}年游戏数据下载失败")
                return jsonify({'status': 'error', 'message': '下载失败，请检查日志'})
    except Exception as e:
        print(f"下载过程中发生错误: {str(e)}")
        return jsonify({'status': 'error', 'message': f'下载错误: {str(e)}'})

def run_spider(start_year, end_year):
    for year in range(start_year, end_year + 1):
        if not spider_status['running']:
            break
            
        spider_status['current_year'] = year
        
        # 处理1-12月数据
        for month in range(1, 13):
            if not spider_status['running']:
                break
                
            spider_status['current_month'] = month
            spider_status['progress'] = ((year - start_year) * 12 + (month - 1)) / (spider_status['total_years'] * 12) * 100
            spider_status['current_month'] = month
            
            # 获取并处理游戏数据
            games = tool.get_raw_getchu_games(year, month)
            
            # 将数据保存到数据库
            success = tool.get_all_getchu_games(year, year, month, month)
            if success:  # 确保数据库操作完成
                spider_status['progress'] = ((year - start_year) * 12 + (month - 1)) / (spider_status['total_years'] * 12) * 100
                spider_status['current_game'] = f"已完成{year}年{month}月数据入库"
            
            # 处理每个游戏
            for i, game in enumerate(games):
                if not spider_status['running']:
                    break
                spider_status['current_game'] = f"正在处理{year}年{month}月游戏: {game.name} ({i+1}/{len(games)})"
    
    spider_status['running'] = False

if __name__ == '__main__':
    app.run(debug=True)