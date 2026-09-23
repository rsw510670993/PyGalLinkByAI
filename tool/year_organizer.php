<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>2022 年整理 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .metric-value { font-size: 1.6rem; font-weight: 700; }
        .hash { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .candidate-table td { vertical-align: middle; }
    </style>
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

<main class="container py-4">
    <div class="d-flex flex-wrap align-items-center gap-2 mb-3">
        <div>
            <h1 class="h3 mb-1">2022 年 115 整理</h1>
            <div class="text-muted">先只读筛选缺少目录映射的记录，再把勾选项交给 AI 核对、整理和验证。</div>
        </div>
        <div class="ms-auto d-flex gap-2">
            <a class="btn btn-outline-secondary btn-sm" href="ai_organizer.php">全部 AI 任务</a>
            <button id="refresh-btn" class="btn btn-outline-primary btn-sm" type="button">重新扫描</button>
        </div>
    </div>

    <div class="alert alert-info py-2 small">
        扫描本身不会修改 115 或数据库。EGS 磁链候选仍需在 EGS 页面人工审核；本页只处理已提交后的目录整理。
        无直接 EGS 归属的历史资源统一保留在 <code>/GAL/todo</code>。
    </div>

    <div class="row g-2 mb-3">
        <div class="col-6 col-lg"><div class="card h-100"><div class="card-body"><div class="text-muted small">2022 总记录</div><div id="metric-total" class="metric-value">-</div></div></div></div>
        <div class="col-6 col-lg"><div class="card h-100"><div class="card-body"><div class="text-muted small">可处理磁链</div><div id="metric-actionable" class="metric-value">-</div></div></div></div>
        <div class="col-6 col-lg"><div class="card h-100"><div class="card-body"><div class="text-muted small">已完成映射</div><div id="metric-mapped" class="metric-value text-success">-</div></div></div></div>
        <div class="col-6 col-lg"><div class="card h-100"><div class="card-body"><div class="text-muted small">等待下载</div><div id="metric-waiting" class="metric-value text-warning">-</div></div></div></div>
        <div class="col-12 col-lg"><div class="card h-100 border-primary"><div class="card-body"><div class="text-muted small">建议交给 AI</div><div id="metric-ai" class="metric-value text-primary">-</div></div></div></div>
    </div>

    <div class="row g-2 mb-3">
        <div class="col-12 col-lg-6"><div id="driver-alert" class="alert alert-secondary py-2 mb-0">正在检查 Codex...</div></div>
        <div class="col-12 col-lg-6"><div id="p115-alert" class="alert alert-secondary py-2 mb-0">正在检查 115...</div></div>
    </div>

    <section class="card">
        <div class="card-header d-flex flex-wrap align-items-center gap-2">
            <span class="fw-semibold">AI 判定候选</span>
            <span id="selection-label" class="small text-muted"></span>
            <div class="ms-auto d-flex gap-2">
                <button id="select-all-btn" class="btn btn-outline-secondary btn-sm" type="button">全选</button>
                <button id="start-btn" class="btn btn-primary btn-sm" type="button" disabled>交给 AI 判定并整理</button>
            </div>
        </div>
        <div id="page-message" class="card-body text-muted">正在扫描 2022 年数据...</div>
        <div id="candidate-wrap" class="table-responsive" hidden>
            <table class="table table-sm candidate-table mb-0">
                <thead class="table-light">
                <tr>
                    <th class="text-center" style="width:42px"><input id="select-all" class="form-check-input" type="checkbox" checked aria-label="全选候选"></th>
                    <th>月份 / EGS</th>
                    <th>游戏</th>
                    <th>证据</th>
                    <th>提交 AI 的原因</th>
                </tr>
                </thead>
                <tbody id="candidate-body"></tbody>
            </table>
        </div>
    </section>
</main>

<div class="modal fade" id="login-modal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-sm modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header"><h5 class="modal-title">登录 115</h5><button class="btn-close" data-bs-dismiss="modal"></button></div>
            <div class="modal-body text-center">
                <div id="qr-message" class="small text-muted mb-2">正在生成二维码...</div>
                <img id="qr-image" class="img-fluid border rounded" alt="115 登录二维码" hidden>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>
<script>
(() => {
    const YEAR = 2022;
    const $ = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','>':'&gt;','<':'&lt;','"':'&quot;',"'":'&#39;'}[c]));
    let candidates = [];
    let driverReady = false;
    let p115Ready = false;
    let activeJob = false;
    let qrTimer = null;

    async function api(action, options = {}) {
        const response = await fetch(`api.php?action=${action}`, options);
        const data = await response.json();
        if (!response.ok || data.success === false || data.status === 'error') {
            throw new Error(data.message || `请求失败 (${response.status})`);
        }
        return data;
    }

    const selectedIds = () => [...document.querySelectorAll('.candidate-check:checked')].map(input => Number(input.value));

    function syncSelection() {
        const selected = selectedIds().length;
        $('selection-label').textContent = `已选 ${selected} / ${candidates.length}`;
        $('select-all').checked = candidates.length > 0 && selected === candidates.length;
        $('select-all').indeterminate = selected > 0 && selected < candidates.length;
        $('start-btn').disabled = !driverReady || !p115Ready || activeJob || selected === 0;
    }

    function renderPreview(data) {
        const counts = data.counts || {};
        $('metric-total').textContent = counts.total ?? 0;
        $('metric-actionable').textContent = counts.actionable ?? 0;
        $('metric-mapped').textContent = counts.mapped ?? 0;
        $('metric-waiting').textContent = counts.awaiting_download ?? 0;
        $('metric-ai').textContent = counts.needs_ai ?? 0;
        candidates = data.candidates || [];
        if (!candidates.length) {
            $('page-message').hidden = false;
            $('page-message').className = 'card-body text-success';
            $('page-message').textContent = '2022 年没有需要提交 AI 判定的整理候选。';
            $('candidate-wrap').hidden = true;
            syncSelection();
            return;
        }
        $('page-message').hidden = true;
        $('candidate-wrap').hidden = false;
        $('candidate-body').innerHTML = candidates.map(item => `
            <tr>
                <td class="text-center"><input class="form-check-input candidate-check" type="checkbox" value="${escape(item.egs_id)}" checked aria-label="选择 ${escape(item.name)}"></td>
                <td class="small"><div>${escape(item.date)}</div><code>#${escape(item.egs_id)}</code></td>
                <td><div class="fw-semibold">${escape(item.name)}</div><div class="small text-muted">${escape(item.company || '-')}</div></td>
                <td class="small"><div class="hash font-monospace" title="${escape(item.infohash_hex)}">${escape(item.infohash_hex)}</div><div>${item.folder_cid ? `CID ${escape(item.folder_cid)}` : '<span class="text-warning">尚无目录映射</span>'}</div></td>
                <td class="small">${escape(item.reason)}</td>
            </tr>`).join('');
        document.querySelectorAll('.candidate-check').forEach(input => input.addEventListener('change', syncSelection));
        syncSelection();
    }

    async function loadPreview() {
        $('refresh-btn').disabled = true;
        try {
            renderPreview(await api(`ai_year_preview&year=${YEAR}`));
        } catch (error) {
            $('page-message').hidden = false;
            $('page-message').className = 'card-body text-danger';
            $('page-message').textContent = `扫描失败：${error.message}`;
        } finally {
            $('refresh-btn').disabled = false;
        }
    }

    async function loadHealth() {
        const [aiResult, p115Result] = await Promise.allSettled([
            api('ai_organizer_status'), api('115_login_status')
        ]);
        if (aiResult.status === 'fulfilled') {
            const driver = aiResult.value.driver || {};
            driverReady = !!(driver.available && driver.logged_in);
            activeJob = (aiResult.value.jobs || []).some(job => ['queued', 'running'].includes(job.status));
            $('driver-alert').className = `alert py-2 mb-0 ${driverReady ? 'alert-success' : 'alert-warning'}`;
            $('driver-alert').textContent = driverReady
                ? `Codex 已就绪 · ${driver.version || ''}`
                : `Codex 尚未就绪：${driver.message || 'Web 运行账户未登录'}`;
        } else {
            $('driver-alert').className = 'alert alert-danger py-2 mb-0';
            $('driver-alert').textContent = aiResult.reason.message;
        }
        if (p115Result.status === 'fulfilled') {
            p115Ready = !!p115Result.value.logged_in;
            $('p115-alert').className = `alert py-2 mb-0 ${p115Ready ? 'alert-success' : 'alert-warning'}`;
            $('p115-alert').innerHTML = p115Ready
                ? `115 已登录 · ${escape(p115Result.value.user || '')}`
                : `115 未登录：${escape(p115Result.value.reason || '')} <button id="login-btn" class="btn btn-sm btn-warning ms-2" type="button">扫码登录</button>`;
            $('login-btn')?.addEventListener('click', beginLogin);
        } else {
            p115Ready = false;
            $('p115-alert').className = 'alert alert-danger py-2 mb-0';
            $('p115-alert').textContent = p115Result.reason.message;
        }
        syncSelection();
    }

    async function beginLogin() {
        bootstrap.Modal.getOrCreateInstance($('login-modal')).show();
        $('qr-message').textContent = '正在生成二维码...';
        $('qr-image').hidden = true;
        try {
            const token = await api('115_login_qrcode');
            $('qr-image').src = `data:image/png;base64,${token.qrcode_base64}`;
            $('qr-image').hidden = false;
            $('qr-message').textContent = '请使用 115 客户端扫码并确认';
            clearInterval(qrTimer);
            qrTimer = setInterval(async () => {
                try {
                    const state = await api(`115_login_qrcode_status&uid=${encodeURIComponent(token.uid)}&time=${encodeURIComponent(token.time)}&sign=${encodeURIComponent(token.sign)}`);
                    $('qr-message').textContent = state.message || '等待扫码';
                    if (Number(state.status) === 2) {
                        clearInterval(qrTimer);
                        await api('115_login_confirm', {
                            method: 'POST', headers: {'Content-Type':'application/json'},
                            body: JSON.stringify({uid: token.uid, app: 'alipaymini'})
                        });
                        bootstrap.Modal.getOrCreateInstance($('login-modal')).hide();
                        await loadHealth();
                    } else if (Number(state.status) < 0) {
                        clearInterval(qrTimer);
                    }
                } catch (error) {
                    clearInterval(qrTimer);
                    $('qr-message').textContent = error.message;
                }
            }, 2000);
        } catch (error) {
            $('qr-message').textContent = error.message;
        }
    }

    $('start-btn').addEventListener('click', async () => {
        const ids = selectedIds();
        if (!ids.length) return;
        if (!confirm(`将 ${ids.length} 条 2022 年候选交给 AI 核对并整理。证据完整的项目允许执行，冲突项目会保留。继续？`)) return;
        $('start-btn').disabled = true;
        try {
            const data = await api('ai_job_start', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({
                    year: YEAR,
                    egs_ids: ids,
                    prompt: '核对并整理页面选中的 2022 年 EGS 记录。确认实际 115 CID、父目录、名称和 InfoHash；证据完整时完成目录命名、移动和映射并逐项验证。无直接 EGS 归属的历史资源放入 /GAL/todo；不同哈希、合集内容冲突或独有文件不得猜测。'
                })
            });
            location.href = `ai_organizer.php?job_id=${encodeURIComponent(data.job_id)}`;
        } catch (error) {
            alert(error.message);
            syncSelection();
        }
    });
    $('select-all').addEventListener('change', event => {
        document.querySelectorAll('.candidate-check').forEach(input => { input.checked = event.target.checked; });
        syncSelection();
    });
    $('select-all-btn').addEventListener('click', () => {
        document.querySelectorAll('.candidate-check').forEach(input => { input.checked = true; });
        syncSelection();
    });
    $('refresh-btn').addEventListener('click', loadPreview);
    $('login-modal').addEventListener('hidden.bs.modal', () => clearInterval(qrTimer));

    Promise.all([loadPreview(), loadHealth()]);
})();
</script>
</body>
</html>
