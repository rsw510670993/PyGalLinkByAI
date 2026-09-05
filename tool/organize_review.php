<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>整理待办 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .issue-detail { font-size: .8125rem; color: #6c757d; word-break: break-all; }
        .issue-detail .path-arrow { font-family: monospace; }
        .table td { vertical-align: middle; }
    </style>
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

<div class="container mt-4">
    <div class="card mb-3">
        <div class="card-body d-flex flex-wrap align-items-center gap-3">
            <div>
                <h5 class="mb-0">整理115 待办</h5>
                <div class="small text-muted mt-1">
                    每次整理运行后自动记录未成功的结果：待确认（搬月/跨年）、等待离线下载、以及各类失败。
                    在这里逐条确认或重试，解决后自动移入历史。
                </div>
            </div>
            <div class="ms-auto d-flex align-items-center gap-2">
                <span id="issue-counts" class="text-muted small">加载中...</span>
                <button id="refresh-btn" class="btn btn-outline-primary btn-sm" type="button">刷新</button>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <span>待处理</span>
            <span id="open-badge" class="badge bg-danger"></span>
        </div>
        <div class="table-responsive">
            <table class="table table-hover align-middle mb-0">
                <thead class="table-light">
                    <tr>
                        <th style="min-width:120px">类型</th>
                        <th style="width:88px">月份</th>
                        <th style="min-width:220px">游戏</th>
                        <th>详情</th>
                        <th style="width:150px">记录时间</th>
                        <th style="min-width:230px">操作</th>
                    </tr>
                </thead>
                <tbody id="open-body">
                    <tr><td colspan="6" class="text-center text-muted py-4">加载中...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="card mt-3">
        <div class="card-header">
            <button class="btn btn-link btn-sm text-decoration-none p-0" type="button"
                    data-bs-toggle="collapse" data-bs-target="#resolved-wrap" aria-expanded="false">
                已处理记录（最近100条）
            </button>
        </div>
        <div id="resolved-wrap" class="collapse">
            <div class="table-responsive">
                <table class="table table-sm align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th style="min-width:110px">类型</th>
                            <th style="width:88px">月份</th>
                            <th style="min-width:200px">游戏</th>
                            <th>结果</th>
                            <th style="width:150px">记录时间</th>
                            <th style="width:150px">解决时间</th>
                        </tr>
                    </thead>
                    <tbody id="resolved-body">
                        <tr><td colspan="6" class="text-center text-muted py-3">加载中...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
(() => {
    const $ = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    const STATUS_META = {
        month_shift_confirm: {label: '待确认搬月', badge: 'bg-warning text-dark', action: '批准搬月并整理'},
        cross_year_confirm:  {label: '待确认跨年', badge: 'bg-warning text-dark', action: '批准跨年并整理'},
        in_offline:          {label: '离线下载中', badge: 'bg-info text-dark', action: null},
        missing_in_115:      {label: '115未找到', badge: 'bg-danger', action: '重试整理'},
        ambiguous:           {label: '多候选目录', badge: 'bg-danger', action: '重试整理'},
        shared_cid:          {label: '目录被共用', badge: 'bg-danger', action: '重试整理'},
        conflict:            {label: '同名记录冲突', badge: 'bg-danger', action: '重试整理'},
        not_dir:             {label: '定位到非目录', badge: 'bg-danger', action: '重试整理'},
        no_dn_date:          {label: '磁链缺日期', badge: 'bg-danger', action: '重试整理'},
        error:               {label: '处理出错', badge: 'bg-danger', action: '重试整理'},
    };
    const metaOf = status => STATUS_META[status] || {label: status || '未知', badge: 'bg-secondary', action: '重试整理'};

    async function api(action, body) {
        const response = await fetch(`api.php?action=${action}`, body ? {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
        } : {});
        if (!response.ok) throw new Error(`请求失败 (${response.status})`);
        return await response.json();
    }

    function issueBadge(status) {
        const meta = metaOf(status);
        return `<span class="badge ${meta.badge}">${escape(meta.label)}</span>`;
    }

    function detailHtml(issue) {
        const d = issue.detail || {};
        const lines = [];
        if (d.old_path && d.target_path) {
            lines.push(`<div class="path-arrow">${escape(d.old_path)}<br>→ ${escape(d.target_path)}</div>`);
        } else if (d.target_path) {
            lines.push(`<div class="path-arrow">目标：${escape(d.target_path)}</div>`);
        } else if (d.old_path) {
            lines.push(`<div class="path-arrow">当前位置：${escape(d.old_path)}</div>`);
        }
        return lines.join('');
    }

    function egsLink(issue) {
        const [y, m] = String(issue.date || '').split('-');
        return y && m ? `egs.php?year=${encodeURIComponent(y)}&month=${encodeURIComponent(m)}` : 'egs.php';
    }

    function renderOpen(items) {
        if (!items.length) {
            $('open-body').innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">暂无待处理项 ✓</td></tr>';
            return;
        }
        $('open-body').innerHTML = items.map(issue => {
            const meta = metaOf(issue.status);
            const actionBtn = meta.action
                ? `<button class="btn btn-outline-primary btn-sm issue-retry" data-date="${escape(issue.date)}" data-name="${escape(issue.name)}" data-kind="${escape(issue.status)}">${meta.action}</button>`
                : '<span class="text-muted small">等待下载完成，稍后重跑整理</span>';
            return `
            <tr>
                <td>${issueBadge(issue.status)}${issue.outcome === 'failed' ? '<div class="small text-danger mt-1">失败</div>' : ''}</td>
                <td class="small">${escape(issue.date)}</td>
                <td class="small" style="max-width:280px"><span style="word-break:break-all">${escape(issue.name)}</span></td>
                <td class="issue-detail"><div>${escape(issue.message || '')}</div>${detailHtml(issue)}</td>
                <td class="small text-muted">${escape(issue.run_at || '')}</td>
                <td>
                    <div class="d-flex gap-1 flex-wrap">
                        ${actionBtn}
                        <button class="btn btn-outline-secondary btn-sm issue-resolve" data-id="${issue.id}">标记已处理</button>
                        <a class="btn btn-outline-secondary btn-sm" href="${egsLink(issue)}" target="_blank">EGS↗</a>
                    </div>
                </td>
            </tr>`;
        }).join('');
    }

    function renderResolved(items) {
        if (!items.length) {
            $('resolved-body').innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">暂无历史记录</td></tr>';
            return;
        }
        $('resolved-body').innerHTML = items.map(issue => `
            <tr class="text-muted">
                <td>${issueBadge(issue.status)}</td>
                <td class="small">${escape(issue.date)}</td>
                <td class="small" style="max-width:260px;word-break:break-all">${escape(issue.name)}</td>
                <td class="small issue-detail">${escape(issue.message || '')}</td>
                <td class="small">${escape(issue.run_at || '')}</td>
                <td class="small">${escape(issue.resolved_at || '')}</td>
            </tr>`).join('');
    }

    async function load() {
        $('refresh-btn').disabled = true;
        try {
            const data = await api('organize_issues');
            if (data.success === false) throw new Error(data.message || '读取失败');
            const counts = data.counts || {};
            $('issue-counts').textContent = `待处理 ${counts.open || 0} · 已处理 ${counts.resolved || 0}`;
            $('open-badge').textContent = counts.open || 0;
            renderOpen(data.open || []);
            renderResolved(data.resolved || []);
        } catch (error) {
            $('issue-counts').textContent = `加载失败：${error.message}`;
        } finally {
            $('refresh-btn').disabled = false;
        }
    }

    $('open-body').addEventListener('click', async event => {
        const retry = event.target.closest('.issue-retry');
        const resolve = event.target.closest('.issue-resolve');
        if (retry) {
            retry.disabled = true;
            retry.textContent = '整理中...';
            try {
                const result = await api('egs_organize_confirm', {date: retry.dataset.date, name: retry.dataset.name});
                if (result.success === false) throw new Error(result.message || '整理失败');
                await load();
            } catch (error) {
                retry.disabled = false;
                retry.textContent = metaOf(retry.dataset.kind).action;
                alert(error.message);
            }
        } else if (resolve) {
            resolve.disabled = true;
            try {
                const result = await api('organize_issue_resolve', {id: Number(resolve.dataset.id)});
                if (result.success === false) throw new Error(result.message || '操作失败');
                await load();
            } catch (error) {
                resolve.disabled = false;
                alert(error.message);
            }
        }
    });

    $('refresh-btn').addEventListener('click', load);
    load();
})();
</script>
</body>
</html>
