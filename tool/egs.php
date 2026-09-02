<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>EGS 游戏清单</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .game-name { min-width: 280px; }
        .egs-id { width: 100px; }
        .release-col { width: 130px; }
        .company-col { width: 170px; }
        .kind-col { width: 120px; }
        .genre-col { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .external-col { width: 150px; }
    </style>
</head>
<body class="bg-light">
<?php $base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/'); ?>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
    <div class="container">
        <a class="navbar-brand" href="#">EGS 游戏清单</a>
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
            <span class="fw-semibold">PC 18禁游戏</span>
            <span id="page-info" class="text-muted small">加载中...</span>
        </div>
        <div class="table-responsive">
            <table class="table table-striped table-hover align-middle mb-0">
                <thead class="table-dark">
                    <tr>
                        <th class="egs-id">EGS ID</th>
                        <th class="release-col">发售日</th>
                        <th class="game-name">游戏名称</th>
                        <th class="company-col">公司</th>
                        <th class="kind-col">类型</th>
                        <th class="genre-col">ジャンル</th>
                        <th class="external-col">链接</th>
                    </tr>
                </thead>
                <tbody id="games-body">
                    <tr><td colspan="7" class="text-center text-muted py-4">加载中...</td></tr>
                </tbody>
            </table>
        </div>
        <div class="card-footer d-flex justify-content-between align-items-center">
            <button id="prev-page" class="btn btn-outline-primary btn-sm">上一页</button>
            <button id="next-page" class="btn btn-outline-primary btn-sm">下一页</button>
        </div>
    </div>
</div>

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

    function externalLinks(row) {
        const links = [];
        const egs = 'https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/game.php?game=' +
            encodeURIComponent(row.egs_id);
        links.push(`<a href="${egs}" target="_blank" rel="noopener">EGS</a>`);
        if (row.official_url) {
            links.push(`<a href="${esc(row.official_url)}" target="_blank" rel="noopener">OHP</a>`);
        }
        if (row.dlsite_id) {
            const domain = row.dlsite_id.startsWith('VJ') ? 'pro' : 'maniax';
            links.push(`<a href="https://www.dlsite.com/${domain}/work/=/product_id/${encodeURIComponent(row.dlsite_id)}.html" target="_blank" rel="noopener">DLsite</a>`);
        }
        return links.join(' / ');
    }

    function render(data) {
        const tbody = document.getElementById('games-body');
        if (!data.data || data.data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">没有数据</td></tr>';
        } else {
            tbody.innerHTML = data.data.map(row => `
                <tr>
                    <td>${esc(row.egs_id)}</td>
                    <td>${esc(row.release_ts || row.date)}</td>
                    <td class="game-name">${esc(row.name)}</td>
                    <td>${esc(row.company || '')}</td>
                    <td>${esc(row.brand_kind || '')}</td>
                    <td class="genre-col" title="${esc(row.genre || '')}">${esc(row.genre || '')}</td>
                    <td>${externalLinks(row)}</td>
                </tr>
            `).join('');
        }

        const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
        document.getElementById('page-info').textContent =
            `第 ${data.current_page} / ${totalPages} 页 · 共 ${data.total} 条`;
        document.getElementById('prev-page').disabled = data.current_page <= 1;
        document.getElementById('next-page').disabled = data.current_page >= totalPages;
    }

    async function load() {
        const tbody = document.getElementById('games-body');
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">加载中...</td></tr>';
        try {
            const res = await fetch(`api.php?${buildQuery()}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (data.status === 'error') throw new Error(data.message || '接口错误');
            render(data);
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-4">加载失败：${esc(err.message)}</td></tr>`;
        }
    }

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
