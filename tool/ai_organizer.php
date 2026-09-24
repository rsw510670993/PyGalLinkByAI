<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AI 整理 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .job-card { cursor: pointer; }
        .job-card:hover { border-color: var(--bs-primary); }
        .event-line { border-left: 3px solid #dee2e6; padding-left: .75rem; }
        .event-line.error { border-color: #dc3545; }
        .event-line.warning { border-color: #ffc107; }
        .event-payload { max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
        .prompt-box { min-height: 110px; }
    </style>
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

<main class="container py-4">
    <div class="d-flex flex-wrap align-items-center gap-2 mb-3">
        <div>
            <h4 class="mb-1">AI 115 整理工作台</h4>
            <div class="text-muted small">EGS 磁链仍由人工审核；此处负责提交后的下载核验、目录整理和历史资源归属。</div>
        </div>
        <div class="ms-auto d-flex gap-2">
            <button id="refresh-btn" class="btn btn-outline-primary btn-sm" type="button">刷新</button>
        </div>
    </div>

    <div id="driver-alert" class="alert alert-secondary py-2">正在检查 AI API...</div>

    <div class="card mb-3">
        <div class="card-header">交给 AI</div>
        <div class="card-body">
            <div class="row g-3">
                <div class="col-md-2">
                    <label for="scope-year" class="form-label">限定年份</label>
                    <input id="scope-year" type="number" class="form-control" min="1990" max="2100" placeholder="必填" required>
                </div>
                <div class="col-md-10">
                    <label for="task-prompt" class="form-label">任务</label>
                    <textarea id="task-prompt" class="form-control prompt-box" maxlength="12000"
                              placeholder="例如：检查并整理 2022 年已提交到 115 的记录；自动处理证据完整的项目，冲突项保留并说明原因。"></textarea>
                </div>
            </div>
            <div class="d-flex flex-wrap gap-2 align-items-center mt-3">
                <button id="start-btn" class="btn btn-primary" type="button">启动 AI 整理</button>
                <button class="btn btn-outline-secondary btn-sm quick-prompt" type="button"
                        data-prompt="检查指定年份已经提交到 115 的记录；确认下载结果后完成目录命名、移动和数据库映射，证据冲突的项目不要猜测。">年度检查与整理</button>
                <button class="btn btn-outline-secondary btn-sm quick-prompt" type="button"
                        data-prompt="处理当前全部 115 整理待办；安全项目直接完成，合集、不同哈希或独有文件必须列出证据。">处理现有待办</button>
                <span id="start-message" class="small text-muted"></span>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="card-header d-flex justify-content-between">
            <span>任务记录</span><span id="job-counts" class="small text-muted"></span>
        </div>
        <div id="job-list" class="card-body">
            <div class="text-muted text-center py-4">加载中...</div>
        </div>
    </div>

    <div id="detail-card" class="card mt-3" hidden>
        <div class="card-header d-flex align-items-center gap-2">
            <span>任务详情</span>
            <code id="detail-id" class="small"></code>
            <span id="detail-status"></span>
            <button id="stop-btn" class="btn btn-outline-danger btn-sm ms-auto" type="button" hidden>停止</button>
        </div>
        <div class="card-body">
            <div id="detail-prompt" class="mb-3"></div>
            <div id="detail-summary" class="alert alert-light border" hidden></div>
            <h6>事件时间线</h6>
            <div id="event-list" class="vstack gap-2"></div>
        </div>
    </div>
</main>

<script>
(() => {
    const $ = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','>':'&gt;','<':'&lt;','"':'&quot;',"'":'&#39;'}[c]));
    const terminal = new Set(['completed', 'partial', 'blocked', 'failed', 'cancelled']);
    let selectedJob = new URLSearchParams(location.search).get('job_id');
    let refreshTimer = null;

    const statusMeta = status => ({
        queued: ['排队中', 'text-bg-secondary'], running: ['执行中', 'text-bg-primary'],
        completed: ['已完成', 'text-bg-success'], partial: ['部分完成', 'text-bg-warning'],
        blocked: ['需人工', 'text-bg-warning'], failed: ['失败', 'text-bg-danger'],
        cancelled: ['已停止', 'text-bg-secondary']
    }[status] || [status || '未知', 'text-bg-secondary']);

    function badge(status) {
        const [label, cls] = statusMeta(status);
        return `<span class="badge ${cls}">${escape(label)}</span>`;
    }

    async function api(action, options = {}) {
        const response = await fetch(`api.php?action=${action}`, options);
        const data = await response.json();
        if (!response.ok || data.success === false) throw new Error(data.message || `请求失败 (${response.status})`);
        return data;
    }

    function renderJobs(jobs, counts) {
        $('job-counts').textContent = Object.entries(counts || {}).map(([k, v]) => `${statusMeta(k)[0]} ${v}`).join(' · ');
        if (!jobs.length) {
            $('job-list').innerHTML = '<div class="text-muted text-center py-4">尚无 AI 整理任务</div>';
            return;
        }
        $('job-list').innerHTML = `<div class="row g-2">${jobs.map(job => `
            <div class="col-12">
                <div class="job-card border rounded p-3" data-job-id="${escape(job.job_id)}">
                    <div class="d-flex gap-2 align-items-center">
                        ${badge(job.status)}
                        <code class="small">${escape(job.job_id.slice(0, 12))}</code>
                        ${job.scope_year ? `<span class="badge text-bg-light">${escape(job.scope_year)}年</span>` : ''}
                        <span class="small text-muted ms-auto">${escape(job.created_at)}</span>
                    </div>
                    <div class="mt-2">${escape(job.summary || job.error || '等待结果')}</div>
                </div>
            </div>`).join('')}</div>`;
    }

    function renderDetail(job) {
        selectedJob = job.job_id;
        $('detail-card').hidden = false;
        $('detail-id').textContent = job.job_id;
        $('detail-status').innerHTML = badge(job.status);
        $('detail-prompt').innerHTML = `<div class="small text-muted mb-1">任务说明</div><div>${escape(job.prompt)}</div>`;
        $('stop-btn').hidden = terminal.has(job.status);
        const summary = job.summary || job.error;
        $('detail-summary').hidden = !summary;
        $('detail-summary').textContent = summary || '';
        const events = job.events || [];
        $('event-list').innerHTML = events.length ? events.map(event => `
            <div class="event-line ${escape(event.level)}">
                <div class="d-flex gap-2"><code>${escape(event.event_type)}</code><span class="small text-muted ms-auto">${escape(event.created_at)}</span></div>
                <div>${escape(event.message)}</div>
                ${event.payload ? `<details><summary class="small text-muted">结构化数据</summary><pre class="event-payload small bg-light p-2 mt-1">${escape(JSON.stringify(event.payload, null, 2))}</pre></details>` : ''}
            </div>`).join('') : '<div class="text-muted">暂无事件</div>';
    }

    async function load() {
        try {
            const data = await api('ai_organizer_status');
            const driver = data.driver || {};
            const ok = driver.available && driver.logged_in;
            $('driver-alert').className = `alert py-2 ${ok ? 'alert-success' : 'alert-warning'}`;
            $('driver-alert').textContent = ok
                ? `AI API 已就绪 · ${driver.provider || ''} / ${driver.model || ''}`
                : `AI API 尚未就绪：${driver.message || 'Provider 或 API Key 未配置'}`;
            $('start-btn').disabled = !ok || (data.jobs || []).some(job => ['queued', 'running'].includes(job.status));
            renderJobs(data.jobs || [], data.counts || {});
            if (selectedJob) await loadDetail(selectedJob);
        } catch (error) {
            $('driver-alert').className = 'alert alert-danger py-2';
            $('driver-alert').textContent = error.message;
        }
    }

    async function loadDetail(jobId) {
        const data = await api(`ai_job_detail&job_id=${encodeURIComponent(jobId)}`);
        renderDetail(data.job);
    }

    $('job-list').addEventListener('click', event => {
        const card = event.target.closest('[data-job-id]');
        if (card) loadDetail(card.dataset.jobId).catch(error => alert(error.message));
    });

    document.querySelectorAll('.quick-prompt').forEach(button => button.addEventListener('click', () => {
        $('task-prompt').value = button.dataset.prompt;
        $('task-prompt').focus();
    }));

    $('start-btn').addEventListener('click', async () => {
        const prompt = $('task-prompt').value.trim();
        const yearValue = $('scope-year').value.trim();
        if (!prompt) return alert('请输入任务说明');
        if (!yearValue) return alert('请输入整理年份');
        $('start-btn').disabled = true;
        $('start-message').textContent = '正在创建后台任务...';
        try {
            const data = await api('ai_job_start', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prompt, year: yearValue ? Number(yearValue) : null})
            });
            selectedJob = data.job_id;
            $('start-message').textContent = '任务已启动';
            await load();
            await loadDetail(data.job_id);
        } catch (error) {
            $('start-message').textContent = error.message;
            $('start-btn').disabled = false;
        }
    });

    $('stop-btn').addEventListener('click', async () => {
        if (!selectedJob || !confirm('确认停止当前 AI 整理任务？')) return;
        try {
            await api('ai_job_stop', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({job_id: selectedJob})
            });
            await loadDetail(selectedJob);
        } catch (error) { alert(error.message); }
    });

    $('refresh-btn').addEventListener('click', load);
    load();
    refreshTimer = setInterval(load, 4000);
})();
</script>
</body>
</html>
