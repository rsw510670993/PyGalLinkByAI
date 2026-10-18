<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>任务总线 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .job-card { cursor: pointer; }
        .job-card:hover { border-color: var(--bs-primary); }
        .job-card.active { border-color: var(--bs-primary); box-shadow: 0 0 0 .15rem rgba(13,110,253,.15); }
        .result-json { max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
        .count-chip { font-variant-numeric: tabular-nums; }
    </style>
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

<main class="container py-4">
    <div class="d-flex flex-wrap align-items-center gap-2 mb-3">
        <div>
            <h4 class="mb-1">任务总线</h4>
            <div class="text-muted small">
                网页手动启动确定性流程；执行器在后台跑，结果以 JSON 落在
                <code>status/agent_jobs/outbox/</code>，此处直接读回。整理115 例外：<strong>只入队</strong>，
                由 TUI 侧领取执行（<code>python -m tool.agent_jobs run --once</code> 或 agent 按 skill 处理）；
                复杂判定（孤儿、移 todo、搬月）仍不在这里。
            </div>
        </div>
        <div class="ms-auto d-flex gap-2">
            <button id="refresh-btn" class="btn btn-outline-primary btn-sm" type="button">刷新</button>
        </div>
    </div>

    <div id="page-alert" class="alert alert-secondary py-2 small">正在加载任务列表...</div>

    <div class="card mb-3">
        <div class="card-header">启动任务</div>
        <div class="card-body">
            <div class="row g-3 align-items-end">
                <div class="col-6 col-xl-2">
                    <label for="start-year" class="form-label">起始年份</label>
                    <input id="start-year" type="number" class="form-control" min="1990" max="2100" value="2020">
                </div>
                <div class="col-6 col-xl-2">
                    <label for="end-year" class="form-label">结束年份</label>
                    <input id="end-year" type="number" class="form-control" min="1990" max="2100" placeholder="同起始年">
                </div>
                <div class="col-12 col-xl-3">
                    <label for="job-action" class="form-label">动作</label>
                    <select id="job-action" class="form-select">
                        <option value="crawl" data-hint="抓取该年 EGS 清单">获取游戏清单 (crawl)</option>
                        <option value="magnet" data-hint="补该年缺失的磁链">获取下载用磁链 (magnet)</option>
                        <option value="submit" data-hint="提交磁链到 115 离线">提交 115 (submit)</option>
                        <option value="organize" data-hint="整理目录名与映射（只入队）">整理 115 (organize · 只入队)</option>
                        <option value="old_audit" data-hint="核对 /GAL.old 与 /GAL 的重合">核对旧库 (old_audit)</option>
                    </select>
                </div>
                <div class="col-6 col-xl-2">
                    <label for="job-timeout" class="form-label">超时(秒)</label>
                    <input id="job-timeout" type="number" min="60" class="form-control" placeholder="按动作默认">
                </div>
                <div class="col-6 col-xl-2">
                    <span class="form-label d-block">执行方式</span>
                    <div class="d-flex align-items-center gap-3" style="height:38px">
                        <div class="form-check mb-0">
                            <input class="form-check-input" type="radio" name="execute-mode" id="mode-preview" value="0" checked>
                            <label class="form-check-label" for="mode-preview">预演</label>
                        </div>
                        <div class="form-check mb-0">
                            <input class="form-check-input" type="radio" name="execute-mode" id="mode-execute" value="1">
                            <label class="form-check-label" for="mode-execute">真正执行</label>
                        </div>
                    </div>
                </div>
                <div class="col-12 col-xl-1">
                    <button id="start-btn" class="btn btn-primary w-100 px-2" type="button">启动</button>
                </div>
            </div>
            <div class="form-text d-flex flex-wrap gap-3 mt-2">
                <span id="action-hint"></span>
                <span id="execute-hint">预演：只出报告，不改数据</span>
                <span id="start-message" class="text-muted ms-auto"></span>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <span>任务记录</span>
            <span id="job-counts" class="small text-muted"></span>
        </div>
        <div id="job-list" class="card-body">
            <div class="text-muted text-center py-4">加载中...</div>
        </div>
    </div>

    <div id="detail-card" class="card mt-3" hidden>
        <div class="card-header d-flex flex-wrap align-items-center gap-2">
            <span>任务详情</span>
            <code id="detail-id" class="small"></code>
            <span id="detail-status"></span>
            <span id="detail-progress" class="small text-muted"></span>
            <button id="detail-stop" class="btn btn-outline-danger btn-sm ms-auto" type="button" hidden>停止</button>
            <button id="detail-refresh" class="btn btn-outline-secondary btn-sm" type="button">刷新详情</button>
        </div>
        <div class="card-body">
            <div id="detail-highlights"></div>
            <div id="detail-artifacts" class="small mt-2"></div>
            <div id="detail-next" class="small mt-2"></div>
            <details class="mt-3" open>
                <summary class="small text-muted">原始 JSON</summary>
                <pre id="detail-json" class="result-json small bg-light border rounded p-2 mt-2"></pre>
            </details>
        </div>
    </div>
</main>

<script>
(() => {
    const $ = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','>':'&gt;','<':'&lt;','"':'&quot;',"'":'&#39;'}[c]));
    const TERMINAL = new Set(['complete', 'partial', 'error', 'no_old_dir']);
    let selectedJob = new URLSearchParams(location.search).get('job_id');
    let timer = null;

    const stateMeta = job => {
        if (job._state === 'queued') return ['排队中', 'text-bg-secondary'];
        if (job._state === 'running') return ['执行中', 'text-bg-primary'];
        return ({
            complete: ['已完成', 'text-bg-success'],
            no_old_dir: ['已完成（无旧库）', 'text-bg-info'],
            partial: ['部分完成', 'text-bg-warning'],
            error: ['失败', 'text-bg-danger'],
            cancelled: ['已停止', 'text-bg-secondary']
        })[job.status] || [job.status || '未知', 'text-bg-secondary'];
    };
    const badge = job => {
        const [label, cls] = stateMeta(job);
        return `<span class="badge ${cls}">${escape(label)}</span>`;
    };
    const isRunning = job => job._state === 'queued' || job._state === 'running';

    async function api(action, options = {}) {
        const response = await fetch(`api.php?action=${action}`, options);
        const data = await response.json();
        if (!response.ok || data.status === 'error') throw new Error(data.message || `请求失败 (${response.status})`);
        return data;
    }

    function renderJobs(jobs) {
        const running = jobs.filter(isRunning).length;
        $('job-counts').textContent = `共 ${jobs.length} 条${running ? ` · 进行中 ${running}` : ''}`;
        if (!jobs.length) {
            $('job-list').innerHTML = '<div class="text-muted text-center py-4">尚无任务</div>';
            return;
        }
        $('job-list').innerHTML = `<div class="row g-2">${jobs.map(job => {
            const counts = job.counts || {};
            const chips = ['total', 'success', 'failed', 'skipped']
                .filter(key => counts[key] !== undefined)
                .map(key => `<span class="badge text-bg-light count-chip">${escape(key)} ${escape(counts[key])}</span>`)
                .join(' ');
            const summary = (job.highlights || [])[0] || job.error || (job._state === 'queued' ? '等待执行' : '');
            const progressChip = job.progress
                ? `<span class="badge text-bg-primary count-chip">进度 ${escape(job.progress.done)}/${escape(job.progress.total)}</span>` : '';
            return `<div class="col-12">
                <div class="job-card border rounded p-3 ${selectedJob === job.job_id ? 'active' : ''}" data-job-id="${escape(job.job_id)}">
                    <div class="d-flex flex-wrap gap-2 align-items-center">
                        ${badge(job)}
                        <span class="badge text-bg-light">${escape(job.label || job.action)}</span>
                        <span class="badge text-bg-light">${escape(job.year)}${job.year_end && job.year_end !== job.year ? '-' + escape(job.year_end) : ''}</span>
                        ${job.action === 'organize'
                            ? (job.dry_run ? '<span class="badge text-bg-info">预演</span>' : '<span class="badge text-bg-warning">真跑</span>')
                            : (job.dry_run ? '<span class="badge text-bg-info">dry-run</span>' : '<span class="badge text-bg-warning">真删</span>')}
                        ${job.action === 'organize' && job._state === 'queued'
                            ? '<span class="badge text-bg-secondary">待 TUI 领取</span>' : ''}
                        ${progressChip}
                        ${chips}
                        <span class="small text-muted ms-auto">${escape(job.created_at || job.finished_at || '')}</span>
                    </div>
                    <div class="small mt-2">${escape(summary)}</div>
                    <code class="small text-muted">${escape(job.job_id)}</code>
                </div>
            </div>`;
        }).join('')}</div>`;
    }

    function renderDetail(job) {
        selectedJob = job.job_id;
        $('detail-card').hidden = false;
        $('detail-id').textContent = job.job_id;
        $('detail-status').innerHTML = badge(job);
        const pg = job.progress;
        $('detail-progress').textContent = job.duration_s
            ? `耗时 ${job.duration_s}s`
            : (pg ? `已完成 ${pg.done}/${pg.total} · ${pg.current || ''}` : (job._state === 'running' ? '执行中…' : ''));
        $('detail-stop').hidden = job._state !== 'running';
        $('detail-highlights').innerHTML = (job.highlights || []).length
            ? `<ul class="mb-1 small">${job.highlights.map(item => `<li>${escape(item)}</li>`).join('')}</ul>` : '';
        $('detail-artifacts').innerHTML = (job.artifacts || []).length
            ? `产物：${job.artifacts.map(item => `<code>${escape(item)}</code>`).join(' ')}` : '';
        $('detail-next').innerHTML = (job.next_actions || []).length
            ? `待人工：${job.next_actions.map(item => `<span class="badge text-bg-warning">${escape(item.type)} ${escape(item.count ?? '')}</span>`).join(' ')}` : '';
        if (job.error) $('detail-highlights').innerHTML += `<div class="alert alert-danger py-2 small mb-0">${escape(job.error)}</div>`;
        $('detail-json').textContent = JSON.stringify(job, null, 2);
    }

    async function load() {
        try {
            const data = await api('agent_job_list&limit=30');
            const jobs = data.jobs || [];
            renderJobs(jobs);
            const busy = jobs.some(isRunning);
            $('page-alert').className = `alert py-2 small ${busy ? 'alert-primary' : 'alert-secondary'}`;
            $('page-alert').textContent = busy ? '有任务正在执行，列表会自动刷新。' : '当前没有进行中的任务。';
            if (selectedJob) {
                const detail = await api(`agent_job_status&job_id=${encodeURIComponent(selectedJob)}`);
                renderDetail(detail);
            }
            schedule(busy);
        } catch (error) {
            $('page-alert').className = 'alert alert-danger py-2 small';
            $('page-alert').textContent = error.message;
        }
    }

    function schedule(busy) {
        clearTimeout(timer);
        timer = setTimeout(load, busy ? 5000 : 30000);
    }

    async function startJob(action, year, yearEnd, execute) {
        $('start-message').textContent = '正在入队...';
        $('start-btn').disabled = true;
        try {
            const payload = {action, year, year_end: yearEnd, execute: !!execute};
            const timeout = parseInt($('job-timeout').value, 10);
            if (timeout > 0) payload.timeout = timeout;
            const data = await api('agent_job_start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            $('start-message').textContent = data.queued_only
                ? `已入队 ${data.job_id}（等待 TUI 侧领取执行）`
                : `已入队 ${data.job_id}（超时 ${data.timeout_s}s）`;
            selectedJob = data.job_id;
            await load();
        } catch (error) {
            $('start-message').textContent = error.message;
        } finally {
            $('start-btn').disabled = false;
        }
    }

    const EXECUTE_HINTS = {
        old_audit: '会删「旧⊆新」冗余进回收站',
        old_clean: '会删冗余目录进回收站',
        organize: '仍只入队，等 TUI 领取',
    };

    function syncMode() {
        const execute = $('mode-execute').checked;
        const action = $('job-action').value;
        $('execute-hint').textContent = execute
            ? '真正执行：' + (EXECUTE_HINTS[action] || '按动作执行，不可撤销')
            : '预演：只出报告，不改数据';
    }

    function syncHint() {
        const option = $('job-action').selectedOptions[0];
        $('action-hint').textContent = option ? option.dataset.hint : '';
        syncMode();
    }

    $('refresh-btn').addEventListener('click', load);
    $('detail-refresh').addEventListener('click', load);
    $('detail-stop').addEventListener('click', async () => {
        if (!selectedJob || !confirm(`确认停止 ${selectedJob}？`)) return;
        try {
            const data = await api('agent_job_stop', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({job_id: selectedJob})
            });
            $('page-alert').textContent = data.message || '已请求停止';
        } catch (error) {
            $('page-alert').textContent = error.message;
        }
        load();
    });
    $('job-action').addEventListener('change', syncHint);
    document.querySelectorAll('input[name="execute-mode"]').forEach(radio => {
        radio.addEventListener('change', syncMode);
    });
    $('start-btn').addEventListener('click', () => {
        const action = $('job-action').value;
        const year = parseInt($('start-year').value, 10);
        const endRaw = $('end-year').value.trim();
        const yearEnd = endRaw ? parseInt(endRaw, 10) : year;
        if (!year) { $('start-message').textContent = '请填写起始年份'; return; }
        const execute = $('mode-execute').checked;
        if (execute) {
            const warn = EXECUTE_HINTS[action] || '不可撤销';
            if (!confirm(`确认真正执行 ${action} ${year}-${yearEnd}？${warn}`)) return;
        }
        startJob(action, year, yearEnd, execute);
    });

    $('job-list').addEventListener('click', event => {
        const card = event.target.closest('.job-card');
        if (!card) return;
        selectedJob = card.dataset.jobId;   // load() 会带出详情并给该卡片打上 active
        load();
    });

    syncHint();
    load();
})();
</script>
</body>
</html>
