<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>EGS 数据管理</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        #gamesTable { table-layout: fixed; }
        .check-col { width: 64px; max-width: 64px; }
        .ym-col { width: 96px; max-width: 96px; white-space: nowrap; }
        .game-name-cell { width: 320px; min-width: 260px; max-width: 420px; white-space: normal; word-wrap: break-word; }
        .company-col { width: 180px; max-width: 180px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .actions-col { width: 180px; max-width: 180px; white-space: nowrap; }
    </style>
</head>
<body class="bg-light">
<?php $base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/'); ?>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
    <div class="container">
        <a class="navbar-brand" href="#">EGS 数据管理</a>
        <div class="navbar-nav">
            <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
            <a class="nav-link" href="<?= $base ?>/tool/data.php">旧数据展示</a>
            <a class="nav-link active" href="<?= $base ?>/tool/egs.php">EGS 数据</a>
            <a class="nav-link" href="<?= $base ?>/calendar.php">年历</a>
        </div>
    </div>
</nav>

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
                <div class="col-12 col-md-6">
                    <label class="form-label mb-1" for="q">搜索</label>
                    <input id="q" class="form-control" type="search" placeholder="游戏名 / 公司 / 假名">
                </div>
                <div class="col-12 col-md-2 d-grid">
                    <button class="btn btn-primary" type="submit">筛选</button>
                </div>
            </form>
        </div>
    </div>

    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-2">
                <button id="toggle-select" class="btn btn-outline-secondary btn-sm" type="button">全选</button>
                <button id="batch-delete" class="btn btn-outline-danger btn-sm" type="button" disabled>批量删除</button>
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
                        <th class="actions-col">操作</th>
                    </tr>
                </thead>
                <tbody id="games-body">
                    <tr><td colspan="5" class="text-center text-muted py-4">加载中...</td></tr>
                </tbody>
            </table>
        </div>
        <div class="card-footer d-flex justify-content-between align-items-center">
            <button id="prev-page" class="btn btn-outline-primary btn-sm">上一页</button>
            <button id="next-page" class="btn btn-outline-primary btn-sm">下一页</button>
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
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                <button type="button" class="btn btn-primary" id="edit-save-btn">保存</button>
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
        const params = new URLSearchParams({
            action: 'egs_games',
            page: currentPage,
            per_page: perPage
        });
        if (year) params.set('year', year);
        if (month) params.set('month', month);
        if (q) params.set('q', q);
        return params.toString();
    }

    function render(data) {
        const tbody = document.getElementById('games-body');
        if (!data.data || data.data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">没有数据</td></tr>';
        } else {
            tbody.innerHTML = data.data.map(row => {
                const ym = row.date || (row.release_ts ? String(row.release_ts).slice(0, 7) : '');
                const ymText = ym ? ym.replace('-', '/') : '';
                const magnet = row.link || '';
                const nyaaName = row.nyaa_name || '';
                return `
                    <tr data-egs-id="${esc(row.egs_id)}"
                        data-date="${esc(ym)}"
                        data-name="${esc(row.name)}"
                        data-company="${esc(row.company || '')}"
                        data-nyaa-name="${esc(nyaaName)}"
                        data-magnet="${esc(magnet)}"
                        data-downloaded="${esc(row.downloaded || 0)}">
                        <td class="check-col text-center">
                            <input type="checkbox" class="game-checkbox form-check-input">
                        </td>
                        <td class="ym-col">${esc(ymText)}</td>
                        <td class="game-name-cell">${esc(row.name)}${nyaaName ? `<div class="text-muted small"${magnet ? ' style="display:none;"' : ''}>${esc(nyaaName)}</div>` : ''}</td>
                        <td class="company-col">${esc(row.company || '')}</td>
                        <td class="actions-col">
                            <button type="button" class="btn btn-outline-primary btn-sm edit-btn">编辑</button>
                            <button type="button" class="btn btn-outline-danger btn-sm delete-btn">删除</button>
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
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">加载中...</td></tr>';
        try {
            const res = await fetch(`api.php?${buildQuery()}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (data.status === 'error') throw new Error(data.message || '接口错误');
            render(data);
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-4">加载失败：${esc(err.message)}</td></tr>`;
        }
    }

    function updateSelectionButtons() {
        const boxes = Array.from(document.querySelectorAll('#games-body input.game-checkbox'));
        const anyChecked = boxes.some(cb => cb.checked);
        const allChecked = boxes.length > 0 && boxes.every(cb => cb.checked);
        document.getElementById('toggle-select').textContent = allChecked ? '全不选' : '全选';
        document.getElementById('batch-delete').disabled = !anyChecked;
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

    document.getElementById('batch-delete').addEventListener('click', async function () {
        const rows = Array.from(document.querySelectorAll('#games-body tr'))
            .filter(tr => tr.querySelector('input.game-checkbox')?.checked);

        if (!rows.length) return;
        if (!confirm(`确定删除选中的 ${rows.length} 条记录吗？`)) return;

        const btn = this;
        btn.disabled = true;
        btn.textContent = '删除中...';
        let success = 0, fail = 0;

        for (const tr of rows) {
            const egsId = tr.dataset.egsId;
            try {
                const res = await fetch('api.php?action=egs_delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ egs_id: parseInt(egsId, 10) })
                });
                const data = await res.json();
                if (data.success) success++; else fail++;
            } catch {
                fail++;
            }
        }

        btn.disabled = false;
        btn.textContent = '批量删除';
        alert(`删除完成：成功 ${success} 条，失败 ${fail} 条`);
        load();
    });

    document.getElementById('games-body').addEventListener('click', e => {
        const editBtn = e.target.closest('.edit-btn');
        const deleteBtn = e.target.closest('.delete-btn');
        if (!editBtn && !deleteBtn) return;

        const tr = (editBtn || deleteBtn).closest('tr');
        const egsId = parseInt(tr.dataset.egsId, 10);
        const date = tr.dataset.date || '';
        const name = tr.dataset.name || '';

        if (editBtn) {
            const parts = date.split('-');
            document.getElementById('edit-egs-id').value = egsId;
            document.getElementById('edit-year').value = parts[0] || '';
            document.getElementById('edit-month').value = parts[1] ? parseInt(parts[1], 10) : '';
            document.getElementById('edit-name').value = name;
            document.getElementById('edit-company').value = tr.dataset.company || '';
            document.getElementById('edit-nyaa-name').value = tr.dataset.nyaaName || '';
            document.getElementById('edit-link').value = tr.dataset.magnet || '';
            new bootstrap.Modal(document.getElementById('editModal')).show();
            return;
        }

        if (deleteBtn) {
            if (!confirm(`确定删除该记录吗？\n\n${date} / ${name}`)) return;
            deleteBtn.disabled = true;
            fetch('api.php?action=egs_delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ egs_id: egsId })
            }).then(r => r.json()).then(data => {
                if (!data.success) alert('删除失败：' + (data.message || ''));
                load();
            }).catch(err => {
                alert('删除失败：' + err.message);
                deleteBtn.disabled = false;
            });
        }
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
            const res = await fetch('api.php?action=egs_update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
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

    load();
})();
</script>
</body>
</html>
