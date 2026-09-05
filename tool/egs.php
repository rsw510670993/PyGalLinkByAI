<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>EGS 数据 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        #gamesTable { table-layout: fixed; }
        .check-col { width: 64px; max-width: 64px; }
        .ym-col { width: 96px; max-width: 96px; white-space: nowrap; }
        .game-name-cell { width: 320px; min-width: 260px; max-width: 420px; white-space: normal; word-wrap: break-word; }
        .company-col { width: 180px; max-width: 180px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .kind-col { width: 110px; max-width: 110px; white-space: nowrap; }
        .actions-col { width: 300px; max-width: 300px; white-space: nowrap; }
        .review-candidate { border: 1px solid #dee2e6; border-radius: .375rem; padding: .5rem; }
        .review-candidate + .review-candidate { margin-top: .5rem; }
        .editable-cell { cursor: pointer; transition: background-color .15s; }
        .editable-cell:hover { background-color: rgba(13,110,253,.08); text-decoration: underline; text-decoration-style: dotted; text-underline-offset: 3px; }
        .editable-cell input { width: 100%; border: 1px solid #0d6efd; border-radius: 3px; padding: 2px 4px; font-size: inherit; font-family: inherit; }
    </style>
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

<div class="container mt-4">
    <div class="card mb-3">
        <div class="card-body">
            <form id="filter-form" class="row g-2 align-items-end">
                <div class="col-6 col-md-2">
                    <label class="form-label mb-1" for="year">年份</label>
                    <select id="year" class="form-select">
                        <option value="">全部</option>
                        <option value="2026" selected>2026</option>
                    </select>
                </div>
                <div class="col-6 col-md-2">
                    <label class="form-label mb-1" for="month">月份</label>
                    <select id="month" class="form-select">
                        <option value="">全部</option>
                        <?php for ($m = 1; $m <= 12; $m++): ?>
                            <option value="<?= $m ?>"><?= sprintf('%02d', $m) ?></option>
                        <?php endfor; ?>
                    </select>
                </div>
                <div class="col-12 col-md-4">
                    <label class="form-label mb-1" for="q">搜索</label>
                    <input id="q" class="form-control" type="search" placeholder="游戏名 / 公司 / 假名">
                </div>
                <div class="col-6 col-md-2">
                    <label class="form-label mb-1" for="brand-kind">公司/社团</label>
                    <select id="brand-kind" class="form-select">
                        <option value="">全部</option>
                        <option value="CORPORATION">企业</option>
                        <option value="CIRCLE">社团</option>
                    </select>
                </div>
                <div class="col-6 col-md-2 d-grid">
                    <button class="btn btn-primary" type="submit">筛选</button>
                </div>
            </form>
            <div class="d-flex justify-content-end mt-2">
                <button id="review-blacklist-btn" class="btn btn-outline-secondary btn-sm" type="button">审核黑名单</button>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-2">
                <button id="toggle-select" class="btn btn-outline-secondary btn-sm" type="button">全选</button>
                <button id="batch-115-download" class="btn btn-success btn-sm" type="button" disabled>批量115云下载</button>
            </div>
            <span id="page-info" class="text-muted small">加载中...</span>
        </div>
        <div class="table-responsive">
            <table class="table table-striped table-hover align-middle mb-0" id="gamesTable">
                <thead class="table-dark">
                    <tr>
                        <th class="check-col text-center">选择</th>
                        <th class="ym-col">年月</th>
                        <th class="game-name-cell">游戏名称</th>
                        <th class="company-col">公司</th>
                        <th class="kind-col">公司/社团</th>
                        <th class="actions-col">操作</th>
                    </tr>
                </thead>
                <tbody id="games-body">
                    <tr><td colspan="6" class="text-center text-muted py-4">加载中...</td></tr>
                </tbody>
            </table>
        </div>
        <div class="card-footer d-flex justify-content-between align-items-center">
            <button id="prev-page" class="btn btn-outline-primary btn-sm">上一页</button>
            <button id="next-page" class="btn btn-outline-primary btn-sm">下一页</button>
        </div>
    </div>
</div>

<div class="modal fade" id="checkModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">磁链校验与115提交</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <input type="hidden" id="check-modal-egs-id">
                <div class="mb-2">
                    <label class="form-label mb-0 small">游戏</label>
                    <div id="modal-game-name" class="fw-semibold"></div>
                </div>
                <div class="mb-2">
                    <label class="form-label mb-0 small">磁力链接</label>
                    <textarea id="modal-magnet" class="form-control" rows="2" readonly></textarea>
                </div>
                <div class="mb-3">
                    <label class="form-label mb-0 small">115 保存路径</label>
                    <input id="modal-save-path" class="form-control">
                </div>
                <div class="d-flex gap-2 mb-3">
                    <button id="modal-check-btn" class="btn btn-outline-info btn-sm">检查115是否存在</button>
                    <button id="modal-submit-btn" class="btn btn-success btn-sm">提交到115</button>
                </div>
                <div id="modal-result" class="small" style="white-space:pre-wrap;word-break:break-all;"></div>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="reviewModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">低分候选审核</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <input type="hidden" id="review-modal-egs-id">
                <div class="mb-2">
                    <label class="form-label mb-0 small">游戏</label>
                    <div id="review-game-name" class="fw-semibold"></div>
                    <div id="review-game-meta" class="small text-muted"></div>
                </div>
                <div id="review-candidates"></div>
                <div id="review-history" class="mt-3" hidden>
                    <div class="small fw-semibold text-warning">疑似同名复刻 / 跨年重发</div>
                    <div id="review-history-list" class="small"></div>
                </div>
                <hr>
                <div class="mb-2">
                    <label class="form-label mb-0 small" for="review-manual-magnet">手动磁链（候选都不合适时使用）</label>
                    <textarea id="review-manual-magnet" class="form-control" rows="2" placeholder="magnet:?xt=urn:btih:..."></textarea>
                </div>
                <div class="mb-2">
                    <label class="form-label mb-0 small" for="review-manual-nyaa-name">手动磁链显示名（可选）</label>
                    <input id="review-manual-nyaa-name" class="form-control">
                </div>
                <div class="d-flex gap-2 mt-3">
                    <button id="review-manual-approve-btn" class="btn btn-success btn-sm">通过手动磁链</button>
                    <button id="review-reject-btn" class="btn btn-outline-danger btn-sm">全部拒绝</button>
                    <button id="review-reopen-btn" class="btn btn-outline-secondary btn-sm">重新待审</button>
                </div>
                <div id="review-result" class="small mt-2" style="white-space:pre-wrap;word-break:break-all;"></div>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="blacklistModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">审核黑名单</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <div class="table-responsive mb-3" style="max-height:240px;overflow:auto;">
                    <table class="table table-sm align-middle mb-0">
                        <thead><tr><th>公司</th><th>备注</th><th class="text-end">操作</th></tr></thead>
                        <tbody id="blacklist-body"></tbody>
                    </table>
                </div>
                <div class="row g-2">
                    <div class="col-12 col-md-6">
                        <label class="form-label mb-0 small" for="blacklist-company">公司名</label>
                        <input id="blacklist-company" class="form-control form-control-sm">
                    </div>
                    <div class="col-12 col-md-6">
                        <label class="form-label mb-0 small" for="blacklist-note">备注</label>
                        <input id="blacklist-note" class="form-control form-control-sm">
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">关闭</button>
                <button type="button" class="btn btn-primary btn-sm" id="blacklist-add-btn">添加</button>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="editModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">编辑 EGS 记录</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <input type="hidden" id="edit-egs-id">
                <div class="row g-2 mb-2">
                    <div class="col-6">
                        <label class="form-label mb-0 small">年份</label>
                        <input type="number" id="edit-year" class="form-control" min="1980" max="3000">
                    </div>
                    <div class="col-6">
                        <label class="form-label mb-0 small">月份</label>
                        <input type="number" id="edit-month" class="form-control" min="1" max="12">
                    </div>
                </div>
                <div class="mb-2">
                    <label class="form-label mb-0 small">游戏名称</label>
                    <input id="edit-name" class="form-control">
                </div>
                <div class="mb-2">
                    <label class="form-label mb-0 small">公司</label>
                    <input id="edit-company" class="form-control">
                </div>
                <div class="mb-2">
                    <label class="form-label mb-0 small">Nyaa名称</label>
                    <input id="edit-nyaa-name" class="form-control">
                </div>
                <div class="mb-2">
                    <label class="form-label mb-0 small">磁力链接</label>
                    <textarea id="edit-link" class="form-control" rows="2"></textarea>
                </div>
            </div>
            <div class="modal-footer d-flex justify-content-between">
                <button type="button" class="btn btn-outline-danger btn-sm" id="edit-delete-btn">删除记录</button>
                <div>
                    <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">取消</button>
                    <button type="button" class="btn btn-primary btn-sm" id="edit-save-btn">保存</button>
                </div>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>
<script>
(function () {
    let currentPage = 1;
    const perPage = 50;

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function buildQuery() {
        const year = document.getElementById('year').value;
        const month = document.getElementById('month').value;
        const q = document.getElementById('q').value.trim();
        const brandKind = document.getElementById('brand-kind').value;
        const params = new URLSearchParams({
            action: 'egs_games',
            page: currentPage,
            per_page: perPage
        });
        if (year) params.set('year', year);
        if (month) params.set('month', month);
        if (q) params.set('q', q);
        if (brandKind) params.set('brand_kind', brandKind);
        return params.toString();
    }

    function render(data) {
        const tbody = document.getElementById('games-body');
        if (!data.data || data.data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">没有数据</td></tr>';
        } else {
            tbody.innerHTML = data.data.map(row => {
                const ym = row.date || (row.release_ts ? String(row.release_ts).slice(0, 7) : '');
                const ymText = ym ? ym.replace('-', '/') : '';
                const magnet = row.link || '';
                const nyaaName = row.nyaa_name || '';
                const downloaded = parseInt(row.downloaded || 0, 10) === 1;
                const submitted = parseInt(row.submitted_115 || 0, 10) === 1;
                const rowClass = downloaded ? 'table-success' : submitted ? 'table-info' : '';
                const candidateCount = parseInt(row.candidate_count || 0, 10);
                const reviewStatus = row.review_status || '';
                const blacklisted = parseInt(row.review_blacklisted || 0, 10) === 1;
                const showReview = candidateCount > 0 && !magnet && !blacklisted;
                const pendingReview = showReview && (!reviewStatus || reviewStatus === 'pending');
                const canMagnet = !!magnet;
                const reviewBadge = pendingReview
                    ? '<span class="badge text-bg-warning ms-1">待审核</span>'
                    : (!blacklisted && !magnet && reviewStatus === 'rejected')
                        ? '<span class="badge text-bg-secondary ms-1">已拒绝</span>'
                        : '';
                const reviewBtn = showReview
                    ? '<button type="button" class="btn btn-outline-warning btn-sm review-btn">审核</button>'
                    : '';
                const brandKindText = row.brand_kind === 'CIRCLE'
                    ? '社团'
                    : row.brand_kind === 'CORPORATION' ? '企业' : (row.brand_kind || '-');
                return `
                    <tr class="${rowClass}"
                        data-egs-id="${esc(row.egs_id)}"
                        data-date="${esc(ym)}"
                        data-name="${esc(row.name)}"
                        data-company="${esc(row.company || '')}"
                        data-nyaa-name="${esc(nyaaName)}"
                        data-magnet="${esc(magnet)}"
                        data-downloaded="${esc(row.downloaded || 0)}"
                        data-submitted-115="${esc(row.submitted_115 || 0)}"
                        data-submitted-pick-code="${esc(row.submitted_pick_code || '')}"
                        data-review-status="${esc(reviewStatus)}"
                        data-candidate-count="${esc(candidateCount)}">
                        <td class="check-col text-center">
                            <input type="checkbox" class="game-checkbox form-check-input">
                        </td>
                        <td class="ym-col">${esc(ymText)}</td>
                        <td class="game-name-cell editable-cell">${esc(row.name)}${reviewBadge}${nyaaName ? `<div class="text-muted small"${magnet ? ' style="display:none;"' : ''}>${esc(nyaaName)}</div>` : ''}</td>
                        <td class="company-col">${esc(row.company || '')}</td>
                        <td class="kind-col">${esc(brandKindText)}</td>
                        <td class="actions-col">
                            <button type="button" class="btn btn-success btn-sm btn-115-submit" ${canMagnet ? '' : 'disabled'}>115云下载</button>
                            <button type="button" class="btn btn-outline-secondary btn-sm magnet-check-btn" ${canMagnet ? '' : 'disabled'}>校验</button>
                            ${reviewBtn}
                        </td>
                    </tr>
                `;
            }).join('');
        }

        const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
        document.getElementById('page-info').textContent =
            `第 ${data.current_page} / ${totalPages} 页 · 共 ${data.total} 条`;
        document.getElementById('prev-page').disabled = data.current_page <= 1;
        document.getElementById('next-page').disabled = data.current_page >= totalPages;
        updateSelectionButtons();
    }

    async function load() {
        const tbody = document.getElementById('games-body');
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">加载中...</td></tr>';
        try {
            const res = await fetch(`api.php?${buildQuery()}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (data.status === 'error') throw new Error(data.message || '接口错误');
            render(data);
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-4">加载失败：${esc(err.message)}</td></tr>`;
        }
    }

    function updateSelectionButtons() {
        const boxes = Array.from(document.querySelectorAll('#games-body input.game-checkbox'));
        const anyChecked = boxes.some(cb => cb.checked);
        const allChecked = boxes.length > 0 && boxes.every(cb => cb.checked);
        document.getElementById('toggle-select').textContent = allChecked ? '全不选' : '全选';
        document.getElementById('batch-115-download').disabled = !anyChecked;
    }

    document.getElementById('toggle-select').addEventListener('click', () => {
        const boxes = Array.from(document.querySelectorAll('#games-body input.game-checkbox'));
        const allChecked = boxes.length > 0 && boxes.every(cb => cb.checked);
        boxes.forEach(cb => cb.checked = !allChecked);
        updateSelectionButtons();
    });

    document.getElementById('games-body').addEventListener('change', e => {
        if (e.target.matches('input.game-checkbox')) updateSelectionButtons();
    });

    function yearFromRow(tr) {
        return (tr.dataset.date || '').split('-')[0] || '';
    }

    async function updateEgsRow(egsId, body) {
        const res = await fetch('api.php?action=egs_update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ egs_id: egsId, ...body })
        });
        return await res.json();
    }

    async function submit115(tr, btn) {
        const magnet = tr.dataset.magnet || '';
        const year = yearFromRow(tr);
        if (!magnet || !year || tr.dataset.reviewStatus === 'pending') return;

        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '提交中...';
        try {
            const res = await fetch('api.php?action=115_submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ magnet, dir: `/GAL/GAL-${year}` })
            });
            const data = await res.json();
            if (data.success) {
                await updateEgsRow(tr.dataset.egsId, {
                    new_submitted_115: 1,
                    new_submitted_pick_code: data.pick_code || ''
                });
                btn.className = 'btn btn-success btn-sm disabled';
                btn.textContent = '✓已提交';
                setTimeout(() => load(), 300);
            } else {
                btn.className = 'btn btn-outline-danger btn-sm disabled';
                btn.textContent = '✗失败';
                alert('提交失败：' + (data.message || ''));
            }
        } catch (err) {
            btn.className = 'btn btn-outline-danger btn-sm disabled';
            btn.textContent = '✗失败';
            alert('提交失败：' + err.message);
        } finally {
            setTimeout(() => {
                btn.disabled = false;
                btn.className = 'btn btn-success btn-sm btn-115-submit';
                btn.textContent = origText;
            }, 1500);
        }
    }

    async function openReviewModal(egsId, fallbackName = '-') {
        const resultEl = document.getElementById('review-result');
        resultEl.textContent = '加载候选中...';
        try {
            const res = await fetch(`api.php?action=egs_review_detail&egs_id=${encodeURIComponent(egsId)}`, { cache: 'no-store' });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || '读取失败');
            document.getElementById('review-modal-egs-id').value = egsId;
            document.getElementById('review-game-name').textContent = data.game.name || fallbackName;
            const score = data.game.best_score;
            document.getElementById('review-game-meta').textContent =
                `${data.game.date || ''} · ${data.game.company || ''} · 最佳分 ${score === null || score === undefined ? '-' : score}` +
                ` · 状态 ${data.game.review_status || '待审核'}`;
            renderReviewCandidates(data.candidates || []);
            renderReviewHistory(data.cross_year_suspect, data.history || []);
            document.getElementById('review-manual-magnet').value = data.game.link || '';
            document.getElementById('review-manual-nyaa-name').value = data.game.nyaa_name || '';
            resultEl.textContent = '';
            bootstrap.Modal.getOrCreateInstance(document.getElementById('reviewModal')).show();
        } catch (err) {
            resultEl.textContent = '加载失败：' + err.message;
            bootstrap.Modal.getOrCreateInstance(document.getElementById('reviewModal')).show();
        }
    }

    function renderReviewHistory(suspect, history) {
        const wrap = document.getElementById('review-history');
        const list = document.getElementById('review-history-list');
        if (!suspect || !history.length) {
            wrap.hidden = true;
            list.innerHTML = '';
            return;
        }
        list.innerHTML = history.map(h => `
            <div class="border-bottom py-1">
                <div>${esc(h.egs_date || '-')} · ${esc(h.egs_company || '-')}</div>
                ${h.official_url ? `<a href="${esc(h.official_url)}" target="_blank">官方页面</a>` : ''}
            </div>
        `).join('');
        wrap.hidden = false;
    }

    function renderReviewCandidates(candidates) {
        const box = document.getElementById('review-candidates');
        if (!candidates.length) {
            box.innerHTML = '<div class="text-muted small">没有已保存的候选磁链</div>';
            return;
        }
        box.innerHTML = candidates.map(c => {
            const selected = parseInt(c.selected || 0, 10) === 1;
            return `
                <div class="review-candidate">
                    <div class="d-flex justify-content-between align-items-start gap-2">
                        <div class="small">
                            <div class="fw-semibold">${esc(c.nyaa_title || '-')}</div>
                            <div class="text-muted">分数 ${esc(c.score ?? '-')} · ${esc(c.nyaa_date || '-')}${c.size ? ' · ' + esc(c.size) : ''}</div>
                            <div class="text-break text-muted small">${esc(c.magnet || '')}</div>
                        </div>
                        <div class="d-flex flex-column gap-1 flex-shrink-0">
                            ${c.view_url ? `<a class="btn btn-outline-secondary btn-sm" href="${esc(c.view_url)}" target="_blank">查看</a>` : ''}
                            <button type="button" class="btn btn-success btn-sm review-approve-btn" data-candidate-id="${esc(c.id)}">采用</button>
                            ${selected ? '<span class="badge text-bg-success">已选</span>' : ''}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    async function decideReview(body) {
        const resultEl = document.getElementById('review-result');
        resultEl.textContent = '保存审核结果中...';
        try {
            const res = await fetch('api.php?action=egs_review_decide', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || '保存失败');
            resultEl.textContent = '已保存';
            setTimeout(() => {
                bootstrap.Modal.getOrCreateInstance(document.getElementById('reviewModal')).hide();
                load();
            }, 250);
        } catch (err) {
            resultEl.textContent = '保存失败：' + err.message;
        }
    }

    document.getElementById('review-candidates').addEventListener('click', e => {
        const btn = e.target.closest('.review-approve-btn');
        if (!btn) return;
        decideReview({
            egs_id: parseInt(document.getElementById('review-modal-egs-id').value, 10),
            decision: 'approve',
            candidate_id: parseInt(btn.dataset.candidateId, 10)
        });
    });

    document.getElementById('review-manual-approve-btn').addEventListener('click', () => {
        decideReview({
            egs_id: parseInt(document.getElementById('review-modal-egs-id').value, 10),
            decision: 'approve',
            manual_magnet: document.getElementById('review-manual-magnet').value.trim(),
            manual_nyaa_name: document.getElementById('review-manual-nyaa-name').value.trim()
        });
    });

    document.getElementById('review-reject-btn').addEventListener('click', () => {
        decideReview({
            egs_id: parseInt(document.getElementById('review-modal-egs-id').value, 10),
            decision: 'reject'
        });
    });

    document.getElementById('review-reopen-btn').addEventListener('click', () => {
        decideReview({
            egs_id: parseInt(document.getElementById('review-modal-egs-id').value, 10),
            decision: 'reopen'
        });
    });

    async function loadBlacklist() {
        const tbody = document.getElementById('blacklist-body');
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-2">加载中...</td></tr>';
        try {
            const res = await fetch('api.php?action=egs_review_blacklist', { cache: 'no-store' });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || '读取失败');
            const rows = data.data || [];
            if (!rows.length) {
                tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-2">暂无公司</td></tr>';
                return;
            }
            tbody.innerHTML = rows.map(row => `
                <tr>
                    <td class="fw-semibold">${esc(row.company)}</td>
                    <td class="small text-muted">${esc(row.note || '')}</td>
                    <td class="text-end">
                        <button type="button" class="btn btn-outline-danger btn-sm blacklist-remove-btn" data-company="${esc(row.company)}">删除</button>
                    </td>
                </tr>
            `).join('');
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="3" class="text-center text-danger py-2">${esc(err.message)}</td></tr>`;
        }
    }

    document.getElementById('review-blacklist-btn').addEventListener('click', loadBlacklist);

    document.getElementById('blacklist-add-btn').addEventListener('click', async function () {
        const btn = this;
        const company = document.getElementById('blacklist-company').value.trim();
        const note = document.getElementById('blacklist-note').value.trim();
        if (!company) return;
        btn.disabled = true;
        btn.textContent = '保存中...';
        try {
            const res = await fetch('api.php?action=egs_review_blacklist_add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ company, note })
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || '保存失败');
            document.getElementById('blacklist-company').value = '';
            document.getElementById('blacklist-note').value = '';
            await loadBlacklist();
            load();
        } catch (err) {
            alert('添加失败：' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = '添加';
        }
    });

    document.getElementById('blacklist-body').addEventListener('click', async e => {
        const btn = e.target.closest('.blacklist-remove-btn');
        if (!btn) return;
        const company = btn.dataset.company || '';
        if (!confirm(`确定从审核黑名单移除 ${company} 吗？`)) return;
        btn.disabled = true;
        try {
            const res = await fetch('api.php?action=egs_review_blacklist_remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ company })
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || '删除失败');
            await loadBlacklist();
            load();
        } catch (err) {
            alert('删除失败：' + err.message);
            btn.disabled = false;
        }
    });

    document.getElementById('games-body').addEventListener('click', e => {
        const submitBtn = e.target.closest('.btn-115-submit');
        const checkBtn = e.target.closest('.magnet-check-btn');
        const reviewBtn = e.target.closest('.review-btn');
        const nameCell = e.target.closest('.editable-cell');

        if (submitBtn) {
            const tr = submitBtn.closest('tr');
            submit115(tr, submitBtn);
            return;
        }

        if (reviewBtn) {
            const tr = reviewBtn.closest('tr');
            openReviewModal(parseInt(tr.dataset.egsId, 10), tr.dataset.name || '-');
            return;
        }

        if (checkBtn) {
            const tr = checkBtn.closest('tr');
            document.getElementById('check-modal-egs-id').value = tr.dataset.egsId || '';
            document.getElementById('modal-game-name').textContent = tr.dataset.name || '-';
            document.getElementById('modal-magnet').value = tr.dataset.magnet || '';
            document.getElementById('modal-save-path').value = `/GAL/GAL-${yearFromRow(tr)}`;
            document.getElementById('modal-result').textContent = '';
            document.getElementById('modal-check-btn').disabled = false;
            document.getElementById('modal-submit-btn').disabled = false;
            new bootstrap.Modal(document.getElementById('checkModal')).show();
            return;
        }

        if (nameCell) {
            const tr = nameCell.closest('tr');
            const date = tr.dataset.date || '';
            const parts = date.split('-');
            document.getElementById('edit-egs-id').value = tr.dataset.egsId || '';
            document.getElementById('edit-year').value = parts[0] || '';
            document.getElementById('edit-month').value = parts[1] ? parseInt(parts[1], 10) : '';
            document.getElementById('edit-name').value = tr.dataset.name || '';
            document.getElementById('edit-company').value = tr.dataset.company || '';
            document.getElementById('edit-nyaa-name').value = tr.dataset.nyaaName || '';
            document.getElementById('edit-link').value = tr.dataset.magnet || '';
            new bootstrap.Modal(document.getElementById('editModal')).show();
        }
    });

    document.getElementById('modal-check-btn').addEventListener('click', async function () {
        const magnet = document.getElementById('modal-magnet').value.trim();
        const dir = document.getElementById('modal-save-path').value.trim();
        const egsId = document.getElementById('check-modal-egs-id').value;
        if (!magnet) return;

        this.disabled = true;
        const resultEl = document.getElementById('modal-result');
        resultEl.textContent = '检查中...';
        try {
            const res = await fetch('api.php?action=115_check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ magnet, dir })
            });
            const data = await res.json();
            let msg = '';
            if (data.exists) {
                msg += '该磁链已存在于 115 网盘\n';
                if (data.in_offline_tasks) msg += '（离线任务列表中）\n';
                msg += '置信度: ' + (data.confidence === 'high' ? '高' : data.confidence === 'low' ? '低' : '无') + '\n';
                if (data.matched_files && data.matched_files.length > 0) {
                    msg += '\n匹配文件:\n';
                    data.matched_files.forEach(f => { msg += '  - ' + f.name + '\n'; });
                }
                if (egsId) await updateEgsRow(egsId, { new_downloaded: 1 });
                setTimeout(() => {
                    bootstrap.Modal.getInstance(document.getElementById('checkModal')).hide();
                    load();
                }, 300);
            } else {
                msg += '未在 115 网盘找到该磁链\n';
                if (data.infohash_hex) msg += 'InfoHash: ' + data.infohash_hex + '\n';
                if (data.dn) msg += '文件名: ' + data.dn;
                msg += '\n\n可点击「提交到115」将其加入离线下载';
            }
            resultEl.textContent = msg;
        } catch (err) {
            resultEl.textContent = '检查失败: ' + err.message;
        } finally {
            this.disabled = false;
        }
    });

    document.getElementById('modal-submit-btn').addEventListener('click', async function () {
        const magnet = document.getElementById('modal-magnet').value.trim();
        const dir = document.getElementById('modal-save-path').value.trim();
        const egsId = document.getElementById('check-modal-egs-id').value;
        if (!magnet) return;

        this.disabled = true;
        const resultEl = document.getElementById('modal-result');
        resultEl.textContent = '提交中...';
        try {
            const res = await fetch('api.php?action=115_submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ magnet, dir })
            });
            const data = await res.json();
            if (data.success) {
                resultEl.textContent = '提交成功！\nPick Code: ' + (data.pick_code || '') + '\n文件将保存到: ' + dir;
                if (egsId) await updateEgsRow(egsId, {
                    new_submitted_115: 1,
                    new_submitted_pick_code: data.pick_code || ''
                });
                setTimeout(() => {
                    bootstrap.Modal.getInstance(document.getElementById('checkModal')).hide();
                    load();
                }, 300);
            } else {
                resultEl.textContent = '提交失败: ' + (data.message || '');
            }
        } catch (err) {
            resultEl.textContent = '提交失败: ' + err.message;
        } finally {
            this.disabled = false;
        }
    });

    document.getElementById('batch-115-download').addEventListener('click', async function () {
        const rows = Array.from(document.querySelectorAll('#games-body tr'))
            .filter(tr => tr.querySelector('input.game-checkbox')?.checked);
        if (!rows.length) return;

        const btn = this;
        btn.disabled = true;
        btn.textContent = '提交中...';
        let success = 0, fail = 0;

        for (const tr of rows) {
            const submitBtn = tr.querySelector('.btn-115-submit');
            if (!tr.dataset.magnet || !yearFromRow(tr) || tr.dataset.reviewStatus === 'pending') { fail++; continue; }
            try {
                const res = await fetch('api.php?action=115_submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ magnet: tr.dataset.magnet, dir: `/GAL/GAL-${yearFromRow(tr)}` })
                });
                const data = await res.json();
                if (data.success) {
                    await updateEgsRow(tr.dataset.egsId, {
                        new_submitted_115: 1,
                        new_submitted_pick_code: data.pick_code || ''
                    });
                    success++;
                } else {
                    fail++;
                }
            } catch {
                fail++;
            }
        }

        btn.disabled = false;
        btn.textContent = '批量115云下载';
        alert(`提交完成：成功 ${success} 条，失败 ${fail} 条`);
        load();
    });

    document.getElementById('edit-save-btn').addEventListener('click', async function () {
        const btn = this;
        const egsId = parseInt(document.getElementById('edit-egs-id').value, 10);
        const year = document.getElementById('edit-year').value.trim();
        const month = document.getElementById('edit-month').value.trim();
        const newName = document.getElementById('edit-name').value.trim();
        const newCompany = document.getElementById('edit-company').value.trim();
        const newNyaaName = document.getElementById('edit-nyaa-name').value.trim();
        const newLink = document.getElementById('edit-link').value.trim();

        const body = { egs_id: egsId };
        if (year && month) body.new_date = `${year}-${String(parseInt(month, 10)).padStart(2, '0')}`;
        if (newName) body.new_name = newName;
        if (newCompany) body.new_company = newCompany;
        body.new_nyaa_name = newNyaaName;
        body.new_link = newLink;

        btn.disabled = true;
        btn.textContent = '保存中...';
        try {
            const data = await updateEgsRow(egsId, body);
            if (data.success) {
                bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
                load();
            } else {
                alert('保存失败：' + (data.message || ''));
            }
        } catch (err) {
            alert('保存失败：' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = '保存';
        }
    });

    document.getElementById('edit-delete-btn').addEventListener('click', async function () {
        const egsId = parseInt(document.getElementById('edit-egs-id').value, 10);
        const name = document.getElementById('edit-name').value.trim();
        const year = document.getElementById('edit-year').value.trim();
        const month = document.getElementById('edit-month').value.trim();
        if (!confirm(`确定删除该记录吗？\n\n${year}-${month} / ${name}`)) return;

        const btn = this;
        btn.disabled = true;
        btn.textContent = '删除中...';
        try {
            const res = await fetch('api.php?action=egs_delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ egs_id: egsId })
            });
            const data = await res.json();
            if (data.success) {
                bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
                load();
            } else {
                alert('删除失败：' + (data.message || ''));
            }
        } catch (err) {
            alert('删除失败：' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = '删除记录';
        }
    });

    document.getElementById('filter-form').addEventListener('submit', e => {
        e.preventDefault();
        currentPage = 1;
        load();
    });
    document.getElementById('q').addEventListener('keydown', e => {
        if (e.key === 'Enter') {
            e.preventDefault();
            currentPage = 1;
            load();
        }
    });
    document.getElementById('prev-page').addEventListener('click', () => {
        if (currentPage > 1) { currentPage--; load(); }
    });
    document.getElementById('next-page').addEventListener('click', () => {
        currentPage++;
        load();
    });

    async function initFilters() {
        const params = new URLSearchParams(window.location.search);
        const yearSelect = document.getElementById('year');
        let years = [];
        try {
            const res = await fetch('api.php?action=years&source=egs').then(r => r.json());
            years = Array.isArray(res.years) ? res.years : [];
        } catch (e) {}
        const selectedYear = params.has('year') ? params.get('year') : String(new Date().getFullYear());
        if (/^\d{4}$/.test(selectedYear) && !years.includes(Number(selectedYear))) years.push(Number(selectedYear));
        years.sort((a, b) => b - a);
        yearSelect.innerHTML = '<option value="">全部</option>' + years.map(y => `<option value="${Number(y)}">${Number(y)}</option>`).join('');
        yearSelect.value = selectedYear;
        const month = Number(params.get('month'));
        document.getElementById('month').value = month >= 1 && month <= 12 ? String(month) : '';
        document.getElementById('q').value = params.get('q') || '';
        const brandKind = params.get('brand_kind');
        document.getElementById('brand-kind').value = brandKind === 'CIRCLE' || brandKind === 'CORPORATION' ? brandKind : '';
        load();
    }
    initFilters();
})();
</script>
</body>
</html>
