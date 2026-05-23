<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>年历</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .calendar-table td, .calendar-table th {
            vertical-align: top;
            padding: 8px;
            min-width: 72px;
        }
        .month-cell .badges {
            margin-top: 4px;
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
        }
        .month-cell .counts {
            margin-top: 4px;
            font-size: 12px;
            opacity: 0.9;
            white-space: nowrap;
        }
    </style>
</head>
<body style="padding-top: 56px;">
<?php $base = rtrim(dirname($_SERVER['SCRIPT_NAME']), '/'); if ($base === '/') $base = ''; ?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">年历</a>
            <div class="navbar-nav">
                <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
                <a class="nav-link" href="<?= $base ?>/tool/data.php">数据展示</a>
                <a class="nav-link active" href="<?= $base ?>/calendar.php">年历</a>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        <div class="card mb-3">
            <div class="card-body">
                <div class="row g-2 align-items-end">
                    <div class="col-12 col-lg-6">
                        <label class="form-label mb-1">选择年份（展示该年及前两年）</label>
                        <select id="calendar-year" class="form-select"></select>
                    </div>
                    <div class="col-12 col-lg-6">
                        <div class="small text-muted">
                            颜色说明：绿色=全部已下载，蓝色=全部已提交115，黄色=已采集但未全完成，灰色=无数据
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header fw-semibold">月度概览</div>
            <div class="table-responsive">
                <table class="table table-bordered calendar-table mb-0">
                    <thead class="table-dark">
                        <tr id="calendar-head-row">
                            <th style="width:96px;">年份</th>
                        </tr>
                    </thead>
                    <tbody id="calendar-body"></tbody>
                </table>
            </div>
        </div>
    </div>

    <script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>
    <script>
const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;

function buildHeader() {
    const headRow = document.getElementById('calendar-head-row');
    headRow.innerHTML = '<th style="width:96px;">年份</th>' + Array.from({length: 6}).map((_, i) => `<th class="text-center">${i + 1}</th>`).join('');
}

function cellClass(m) {
    if (!m.has_data) return 'table-light text-muted';
    if (m.all_magnet_downloaded) return 'table-success';
    if (m.all_magnet_submitted) return 'table-primary';
    return 'table-warning';
}

function cellHtml(m) {
    if (!m.has_data) return '<div class="month-cell"><div class="small">-</div></div>';
    const badges = [];
    badges.push('<span class="badge bg-secondary">有数据</span>');
    if (m.all_magnet_submitted) badges.push('<span class="badge bg-light text-dark">全提交</span>');
    if (m.all_magnet_downloaded) badges.push('<span class="badge bg-light text-dark">全下载</span>');
    return `
        <div class="month-cell">
            <div class="fw-semibold text-center">${String(m.month).padStart(2, '0')}</div>
            <div class="badges">${badges.join('')}</div>
            <div class="counts">磁链 S:${m.magnet_submitted}/${m.magnet_total} D:${m.magnet_downloaded}/${m.magnet_total}</div>
        </div>
    `;
}

function renderCalendar(res) {
    const body = document.getElementById('calendar-body');
    if (!res || !Array.isArray(res.years)) {
        body.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">加载失败</td></tr>';
        return;
    }

    body.innerHTML = res.years.map(y => {
        const first = y.months.slice(0, 6).map(m => `<td class="${cellClass(m)}">${cellHtml(m)}</td>`).join('');
        const second = y.months.slice(6, 12).map(m => `<td class="${cellClass(m)}">${cellHtml(m)}</td>`).join('');
        return `<tr><th class="text-center" rowspan="2">${y.year}</th>${first}</tr><tr>${second}</tr>`;
    }).join('');
}

function loadCalendar(year) {
    const url = `${basePath}/tool/api.php?action=calendar&year=${encodeURIComponent(year)}`;
    fetch(url).then(r => r.json()).then(renderCalendar).catch(() => {
        renderCalendar(null);
    });
}

function initYears() {
    buildHeader();
    fetch(`${basePath}/tool/api.php?action=years`)
        .then(r => r.json())
        .then(res => {
            const sel = document.getElementById('calendar-year');
            const nowYear = new Date().getFullYear();
            const years = Array.isArray(res.years) ? res.years.slice() : [];
            if (!years.includes(nowYear)) years.push(nowYear);
            years.sort((a,b) => b-a);
            sel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join('');
            sel.value = years[0] || nowYear;
            loadCalendar(sel.value);
        })
        .catch(() => {
            const sel = document.getElementById('calendar-year');
            const nowYear = new Date().getFullYear();
            sel.innerHTML = `<option value="${nowYear}">${nowYear}</option>`;
            loadCalendar(nowYear);
        });
}

document.addEventListener('DOMContentLoaded', () => {
    initYears();
    document.getElementById('calendar-year').addEventListener('change', (e) => {
        loadCalendar(e.target.value);
    });
});
    </script>
</body>
</html>
