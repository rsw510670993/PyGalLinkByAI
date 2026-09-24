<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>2022 年整理 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

<main class="container py-4">
    <div class="d-flex flex-wrap align-items-center gap-2 mb-3">
        <div>
            <h1 class="h3 mb-1">2022 年 115 整理</h1>
            <div class="text-muted">启动年度 AI 任务，由模型读取 115 实时清单并判断需要整理的内容。</div>
        </div>
        <div class="ms-auto d-flex gap-2">
            <a class="btn btn-outline-secondary btn-sm" href="ai_organizer.php">全部 AI 任务</a>
            <button id="refresh-btn" class="btn btn-outline-primary btn-sm" type="button">刷新状态</button>
        </div>
    </div>

    <div class="alert alert-info py-2 small">
        页面不会预先生成候选清单。AI 会分页读取 <code>/GAL/GAL-2022</code>、<code>/GAL/todo</code>、
        115 离线任务与 2022 年 EGS 记录，再判断归属、合集和重复关系。
        EGS 磁链候选仍由人工审核。
    </div>


    <div class="row g-2 mb-3">
        <div class="col-12 col-lg-6"><div id="driver-alert" class="alert alert-secondary py-2 mb-0">正在检查 AI API...</div></div>
        <div class="col-12 col-lg-6"><div id="p115-alert" class="alert alert-secondary py-2 mb-0">正在检查 115...</div></div>
    </div>

    <section class="card">
        <div class="card-header fw-semibold">年度 AI 整理</div>
        <div class="card-body">
            <p>
                AI 将以 115 返回的实际目录为起点，而不是依赖页面规则筛选的数据库候选。
                它可以自动执行经过安全预览的常规改名、移动和状态同步；跨年、搬月、冲突、不同哈希与删除仍受业务护栏限制。
            </p>
            <div class="d-flex flex-wrap gap-2 align-items-center">
                <button id="start-btn" class="btn btn-primary" type="button" disabled>让 AI 读取 115 并整理 2022</button>
                <span id="page-message" class="small text-muted">正在检查运行条件...</span>
            </div>
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

    function syncSelection() {
        $('start-btn').disabled = !driverReady || !p115Ready || activeJob;
        $('page-message').textContent = activeJob ? '已有 AI 整理任务正在执行' :
            (driverReady && p115Ready ? 'AI API 与 115 已就绪' : '请先完成上方运行条件');
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
                ? `AI API 已就绪 · ${escape(driver.provider || '')} / ${escape(driver.model || '')}`
                : `AI API 尚未就绪：${driver.message || 'Provider 或 API Key 未配置'}`;
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
        if (!confirm('AI 将读取 115 的 2022 年实时清单并自行判断整理对象。安全动作可以直接执行，冲突项会保留。继续？')) return;
        $('start-btn').disabled = true;
        try {
            const data = await api('ai_job_start', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({
                    year: YEAR,
                    prompt: '读取 115 返回的 2022 年实际目录、todo 与离线任务清单，再与 2022 年 EGS 记录比对，由 AI 判断哪些内容需要整理。识别合集及后续重复，安全项目执行并验证；不同哈希、内容冲突或独有文件不得猜测或删除。'
                })
            });
            location.href = `ai_organizer.php?job_id=${encodeURIComponent(data.job_id)}`;
        } catch (error) {
            alert(error.message);
            await loadHealth();
        }
    });
    $('refresh-btn').addEventListener('click', loadHealth);
    $('login-modal').addEventListener('hidden.bs.modal', () => clearInterval(qrTimer));
    loadHealth();
})();
</script>
</body>
</html>
