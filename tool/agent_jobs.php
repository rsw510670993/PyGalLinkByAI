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
                <code>status/agent_jobs/outbox/</code>，此处直接读回。复杂判定（整理、孤儿、移 todo）不在这里。
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
                <div class="col-6 col-md-2">
                    <label for="start-year" class="form-label">起始年份</label>
                    <input id="start-year" type="number" class="form-control" min="1990" max="2100" value="2020">
                </div>
                <div class="col-6 col-md-2">
                    <label for="end-year" class="form-label">结束年份</label>
                    <input id="end-year" type="number" class="form-control" min="1990" max="2100" placeholder="同起始年">
                </div>
                <div class="col-12 col-md-4">
                    <label for="job-action" class="form-label">动作</label>
                    <select id="job-action" class="form-select">
                        <option value="crawl" data-hint="抓取该年度的 EGS 游戏清单">获取游戏清单 (crawl)</option>
                        <option value="magnet" data-hint="为该年度记录获取下载用磁链">获取下载用磁链 (magnet)</option>
                        <option value="submit" data-hint="把磁链提交到 115 离线下载">提交 115 (submit)</option>
                        <option value="old_audit" data-hint="只读审计 /GAL.old/GAL-YYYY，只出报告，没有任何删除模式">核对旧库 (old_audit · 只读)</option>
                        <option value="old_clean" data-hint="清理审计结论为「旧⊆新」的冗余候选：删前现场复验，删除进 115 回收站可恢复；关闭“真正执行”即只预览">清理旧库 (old_clean)</option>
                    </select>
                    <div id="action-hint" class="form-text"></div>
                </div>
                <div class="col-12 col-md-2">
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" id="execute-switch">
                        <label class="form-check-label" for="execute-switch">真正执行</label>
                    </div>
                    <div class="form-text">关闭=dry-run</div>
                </div>
                <div class="col-12 col-md-2">
                    <button id="start-btn" class="btn btn-primary w-100" type="button">启动</button>
                </div>
            </div>
            <div class="d-flex flex-wrap gap-2 align-items-center mt-3">
                <button class="btn btn-outline-secondary btn-sm quick" type="button"
                        data-action="crawl" data-execute="0">清单 2020（预演）</button>
                <button class="btn btn-outline-secondary btn-sm quick" type="button"
                        data-action="old_audit" data-execute="0">核对旧库 2020</button>
                <button class="btn btn-outline-secondary btn-sm quick" type="button"
                        data-action="old_clean" data-execute="0">清理旧库（预览）</button>
                <button class="btn btn-outline-danger btn-sm quick" type="button"
                        data-action="old_clean" data-execute="1">清理旧库（真删）</button>
                <button class="btn btn-outline-secondary btn-sm quick" type="button"
                        data-action="submit" data-execute="0">提交 115 2020（预演）</button>
                <span class="input-group input-group-sm" style="max-width:220px">
                    <span class="input-group-text">超时(秒)</span>
                    <input id="job-timeout" type="number" min="60" class="form-control" placeholder="按动作默认">
                </span>
                <span id="start-message" class="small text-muted ms-auto"></span>
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
                        ${job.action === 'old_audit'
                            ? '<span class="badge text-bg-secondary">只读</span>'
                            : (job.dry_run ? '<span class="badge text-bg-info">dry-run</span>' : '<span class="badge text-bg-warning">真删</span>')}
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
            $('start-message').textContent = `已入队 ${data.job_id}（超时 ${data.timeout_s}s）`;
            selectedJob = data.job_id;
            await load();
        } catch (error) {
            $('start-message').textContent = error.message;
        } finally {
            $('start-btn').disabled = false;
        }
    }

    function syncHint() {
        const option = $('job-action').selectedOptions[0];
        $('action-hint').textContent = option ? option.dataset.hint : '';
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
    $('start-btn').addEventListener('click', () => {
        const action = $('job-action').value;
        const year = parseInt($('start-year').value, 10);
        const endRaw = $('end-year').value.trim();
        const yearEnd = endRaw ? parseInt(endRaw, 10) : year;
        if (!year) { $('start-message').textContent = '请填写起始年份'; return; }
        if ($('execute-switch').checked && !confirm(`确认真正执行 ${action} ${year}-${yearEnd}？dry-run 才安全。`)) return;
        startJob(action, year, yearEnd, $('execute-switch').checked);
    });

    document.querySelectorAll('.quick').forEach(button => {
        button.addEventListener('click', () => {
            $('job-action').value = button.dataset.action;
            $('execute-switch').checked = button.dataset.execute === '1';
            syncHint();
            $('start-btn').click();
        });
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
