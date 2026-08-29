<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>爬虫控制面板</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<?php $base = rtrim(dirname($_SERVER['SCRIPT_NAME']), '/'); ?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">游戏链接采集</a>
            <div class="navbar-nav">
                <a class="nav-link active" href="<?= $base ?>/index.php">首页</a>
                  
                <a class="nav-link" href="<?= $base ?>/tool/data.php">数据展示</a>
                <a class="nav-link" href="<?= $base ?>/calendar.php">年历</a>
                <a class="nav-link" href="<?= $base ?>/tool/logs.php">日志</a>
            </div>
        </div>
    </nav>
    <div class="container" style="padding-top: 80px;">
        <div class="d-flex align-items-center justify-content-between mb-3">
            <h1 class="h3 mb-0">爬虫控制面板</h1>
        </div>

        <div class="row g-3">
            <div class="col-12 col-lg-6">
                <div class="card">
                    <div class="card-header">爬取控制</div>
                    <div class="card-body">
                        <div class="row g-2 align-items-end">
                            <div class="col-6">
                                <label for="spider_start_year" class="form-label mb-1">开始年</label>
                                <input type="number" id="spider_start_year" class="form-control" min="1980" max="3000" value="2018">
                            </div>
                            <div class="col-6">
                                <label for="spider_end_year" class="form-label mb-1">结束年</label>
                                <input type="number" id="spider_end_year" class="form-control" min="1980" max="3000" value="2020">
                            </div>
                            <div class="col-12 d-flex gap-2">
                                <button id="start_btn" class="btn btn-primary">开始爬取</button>
                                <button id="stop_btn" class="btn btn-danger" disabled>停止爬取</button>
                            </div>
                        </div>
                        <div class="small text-muted mt-2">爬取Getchu游戏清单（按年获取），重复爬取不会删除旧数据，已存在记录会被自动跳过。</div>
                    </div>
                </div>
            </div>

            <div class="col-12 col-lg-6">
                <div class="card">
                    <div class="card-header">下载链接</div>
                    <div class="card-body">
                        <div class="row g-2 align-items-end">
                            <div class="col-6">
                                <label for="download_start_date" class="form-label mb-1">开始年月</label>
                                <input type="month" id="download_start_date" class="form-control" min="1980-01" max="2100-12" value="2020-01">
                            </div>
                            <div class="col-6">
                                <label for="download_end_date" class="form-label mb-1">结束年月</label>
                                <input type="month" id="download_end_date" class="form-control" min="1980-01" max="2100-12" value="2020-12">
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                    
                                
                            </div>
                            <div class="col-12 d-flex gap-2">
                                <button id="download_btn" class="btn btn-success">获取Nyaa磁链</button>
                                <button id="download_stop_btn" class="btn btn-outline-danger" disabled>停止下载</button>
                            </div>
                        </div>
                        <div class="small text-muted mt-2">下载任务会获取Nyaa磁链并更新数据库中的链接与 nyaa_name。</div>
                    </div>
                </div>
            </div>

            <div class="col-12">
                <div class="card">
                    <div class="card-header">状态信息</div>
                    <div class="card-body">
                        <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between">
                            <div>
                                <div id="status_text" class="fw-semibold">爬虫未运行</div>
                                <div id="month_stats" class="text-muted small"></div>
                            </div>
                            <div id="download_status_text" class="text-muted small"></div>
                        </div>

                        <div class="mt-3">
                            <div class="d-flex justify-content-between small text-muted">
                                <div id="progress_text"></div>
                                <div id="month_display" class="text-muted"></div>
                            </div>
                            <div class="progress mt-1" style="height: 18px;">
                                <div class="progress-bar" id="progress_bar" role="progressbar" style="width: 0%;" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>
                            </div>
                            <div id="game_display" class="text-muted small mt-2"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

    <script>
        const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;
        const startBtn = document.getElementById('start_btn');
        const stopBtn = document.getElementById('stop_btn');
        const downloadBtn = document.getElementById('download_btn');
        const downloadStopBtn = document.getElementById('download_stop_btn');
        const statusText = document.getElementById('status_text');
        const progressText = document.getElementById('progress_text');
        const progressBar = document.getElementById('progress_bar');
        const downloadStatusText = document.getElementById('download_status_text');
        const monthStats = document.getElementById('month_stats');
        const monthDisplay = document.getElementById('month_display');
        const gameDisplay = document.getElementById('game_display');

        let intervalId = null;
        let downloadIntervalId = null;
        let downloadNotifiedRunIdMem = null;

        function getDownloadRunId(data) {
            const startedAt = data && data.started_at ? String(data.started_at) : '';
            const pid = data && data.pid ? String(data.pid) : '';
            const year = data && data.year ? String(data.year) : '';
            const month = data && data.month ? String(data.month) : '';
            return [startedAt, pid, year, month].join(':');
        }

        function isDownloadNotified(runId) {
            if (!runId) return false;
            if (downloadNotifiedRunIdMem === runId) return true;
            try {
                return localStorage.getItem('download_notified_run_id') === runId;
            } catch (e) {
                return false;
            }
        }

        function markDownloadNotified(runId) {
            if (!runId) return;
            downloadNotifiedRunIdMem = runId;
            try {
                localStorage.setItem('download_notified_run_id', runId);
            } catch (e) {}
        }

        function resetDownloadNotified() {
            downloadNotifiedRunIdMem = null;
            try {
                localStorage.removeItem('download_notified_run_id');
            } catch (e) {}
        }

        downloadBtn.addEventListener('click', async () => {
            const startDate = document.getElementById('download_start_date').value;
            const endDate = document.getElementById('download_end_date').value;
              
              // 将日期转换为年份和月份参数，处理日期范围
              const startYear = new Date(startDate).getFullYear();
              const startMonth = new Date(startDate).getMonth() + 1;
              const endYear = new Date(endDate).getFullYear();
              const endMonth = new Date(endDate).getMonth() + 1;

            try {
                const response = await fetch(`${basePath}/tool/api.php?action=start_download`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        start_year: startYear,
                        start_month: startMonth,
                          end_year: endYear,
                          end_month: endMonth
                    })
                });

                const data = await response.json();
                if (data.status === 'success') {
                    resetDownloadNotified();
                    downloadBtn.disabled = true;
                    downloadStopBtn.disabled = false;
                    downloadStatusText.textContent = '下载任务已启动...';
                    if (downloadIntervalId) clearInterval(downloadIntervalId);
                    downloadIntervalId = setInterval(updateDownloadStatus, 3000);
                } else {
                    alert(data.message);
                }
            } catch (error) {
                console.error('Error:', error);
                alert('请求失败');
            }
        });

        downloadStopBtn.addEventListener('click', async () => {
            try {
                const response = await fetch(`${basePath}/tool/api.php?action=stop_download`, { method: 'POST' });
                const data = await response.json();
                if (data.status === 'success') {
                    downloadStopBtn.disabled = true;
                    downloadStatusText.textContent = '正在停止下载任务...';
                } else {
                    alert(data.message);
                }
            } catch (error) {
                console.error('Error:', error);
            }
        });

        startBtn.addEventListener('click', async () => {
            const startYear = document.getElementById('spider_start_year').value;
            const endYear = document.getElementById('spider_end_year').value;

            try {
                const response = await fetch(`${basePath}/tool/api.php?action=start_spider`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        start_year: startYear,
                        end_year: endYear
                    })
                });

                const data = await response.json();
                if (data.status === 'success') {
                    startBtn.disabled = true;
                    stopBtn.disabled = false;
                    statusText.textContent = '爬虫运行中...';
                    monthStats.textContent = '';
                    monthDisplay.textContent = '';
                    gameDisplay.textContent = '';

                    if (intervalId) clearInterval(intervalId);
                    intervalId = setInterval(updateStatus, 3000);
                } else {
                    alert(data.message);
                }
            } catch (error) {
                console.error('Error:', error);
                alert('请求失败');
            }
        });

        stopBtn.addEventListener('click', async () => {
            try {
                const response = await fetch(`${basePath}/tool/api.php?action=stop_spider`, { method: 'POST' });
                const data = await response.json();
                if (data.status === 'success') {
                    stopBtn.disabled = true;
                    statusText.textContent = '正在停止爬虫...';
                } else {
                    alert(data.message);
                }
            } catch (error) {
                console.error('Error:', error);
            }
        });

        async function updateStatus() {
            try {
                const response = await fetch(`${basePath}/tool/api.php?action=get_status`);
                const data = await response.json();

                if (!data.running) {
                    clearInterval(intervalId);
                    startBtn.disabled = false;
                    stopBtn.disabled = true;
                    if (!data.pid && !data.started_at) {
                        statusText.textContent = '爬虫未运行';
                        progressText.textContent = '';
                        progressBar.style.width = `0%`;
                        progressBar.setAttribute('aria-valuenow', '0');
                        monthStats.textContent = '';
                        monthDisplay.textContent = '';
                        gameDisplay.textContent = '';
                        return;
                    }
                    statusText.textContent = '爬虫已停止';
                    progressText.textContent = `进度: ${Math.round(data.progress || 0)}%`;
                    progressBar.style.width = `${data.progress || 0}%`;
                    progressBar.setAttribute('aria-valuenow', `${Math.round(data.progress || 0)}`);
                    return;
                }

                statusText.textContent = `正在处理 ${data.current_year} 年数据`;
                progressText.textContent = `进度: ${Math.round(data.progress || 0)}%`;
                progressBar.style.width = `${data.progress || 0}%`;
                progressBar.setAttribute('aria-valuenow', `${Math.round(data.progress || 0)}`);

                if (data.current_month) {
                    monthDisplay.textContent = `${data.current_year}年${data.current_month}月`;
                } else {
                    monthDisplay.textContent = '';
                }

                if (data.current_game) {
                    gameDisplay.textContent = data.current_game;
                } else {
                    gameDisplay.textContent = '';
                }

                const fetched = data.current_month_fetched ?? null;
                const inserted = data.current_month_inserted ?? null;
                const skipped = data.current_month_skipped ?? null;
                if (fetched !== null || inserted !== null || skipped !== null) {
                    monthStats.textContent = `本月：抓取 ${fetched ?? 0} / 新增 ${inserted ?? 0} / 跳过 ${skipped ?? 0}`;
                } else {
                    monthStats.textContent = '';
                }
            } catch (error) {
                console.error('Error:', error);
            }
        }

        async function updateDownloadStatus() {
            try {
                const response = await fetch(`${basePath}/tool/api.php?action=download_status`);
                const data = await response.json();
                if (!data.running) {
                    clearInterval(downloadIntervalId);
                    downloadBtn.disabled = false;
                    downloadStopBtn.disabled = true;
                    if (!data.pid && !data.started_at && !data.message && !data.stopped_reason && !(data.total_months > 0)) {
                        downloadStatusText.textContent = '';
                        return;
                    }
                    if (data.message === 'success') {
                        downloadStatusText.textContent = '下载任务已完成';
                        const runId = getDownloadRunId(data);
                        if (!isDownloadNotified(runId)) {
                            alert('下载链接获取已完成');
                            markDownloadNotified(runId);
                        }
                    } else if (data.stopped_reason) {
                        downloadStatusText.textContent = '下载任务已停止';
                    } else if (data.message === 'failed') {
                        downloadStatusText.textContent = '下载任务失败';
                        const runId = getDownloadRunId(data);
                        if (!isDownloadNotified(runId)) {
                            alert('下载任务失败，请检查日志');
                            markDownloadNotified(runId);
                        }
                    } else {
                        downloadStatusText.textContent = '';
                    }
                    return;
                }

                downloadStatusText.textContent = `下载任务运行中... (完成 ${data.finished_months || 0}/${data.total_months || 0})`;
            } catch (error) {
                console.error('Error:', error);
            }
        }

        window.addEventListener('DOMContentLoaded', async () => {
            await updateStatus();
            await updateDownloadStatus();
        });
    </script>
</body>
</html>
