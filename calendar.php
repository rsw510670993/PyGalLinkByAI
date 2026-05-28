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
        .month-cell a:hover {
            background-color: rgba(13, 110, 253, 0.06);
            border-radius: 4px;
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

        <div class="modal fade" id="yearCheckModal" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-lg modal-dialog-scrollable">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">115 整年校对</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="d-flex align-items-center justify-content-between mb-2">
                            <div class="fw-semibold" id="yearCheckTitle"></div>
                            <button type="button" class="btn btn-outline-danger btn-sm" id="yearCheckStopBtn">停止</button>
                        </div>
                        <div class="d-flex justify-content-between small text-muted mb-1">
                            <span id="yearCheckText">准备中...</span>
                            <span id="yearCheckCount"></span>
                        </div>
                        <div class="progress mb-2" style="height: 18px;">
                            <div class="progress-bar progress-bar-striped progress-bar-animated bg-info" id="yearCheckBar"
                                role="progressbar" style="width: 0%;" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>
                        </div>
                        <div id="yearCheckErrors" class="small text-muted" style="white-space:pre-wrap;word-break:break-all;"></div>
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
    headRow.innerHTML = '<th class="text-center" style="width:96px;">年份</th>' + Array.from({length: 6}).map((_, i) => `<th class="text-center">${i + 1}</th>`).join('');
}

function cellClass(m) {
    if (!m.has_data) return 'table-light text-muted';
    if (m.all_magnet_downloaded) return 'table-success';
    if (m.all_magnet_submitted) return 'table-primary';
    return 'table-warning';
}

function cellHtml(year, m) {
    if (!m.has_data) return '<div class="month-cell"><div class="small">-</div></div>';
    const badges = [];
    badges.push('<span class="badge bg-secondary">有数据</span>');
    if (m.all_magnet_submitted) badges.push('<span class="badge bg-light text-dark">全提交</span>');
    if (m.all_magnet_downloaded) badges.push('<span class="badge bg-light text-dark">全下载</span>');
    const href = `${basePath}/tool/data.php?year=${year}&month=${String(m.month).padStart(2, '0')}`;
    return `
        <div class="month-cell">
            <a href="${href}" class="text-decoration-none d-block text-reset">
                <div class="fw-semibold text-center">${String(m.month).padStart(2, '0')}</div>
                <div class="badges">${badges.join('')}</div>
                <div class="counts">磁链 S:${m.magnet_submitted}/${m.magnet_total} D:${m.magnet_downloaded}/${m.magnet_total}</div>
            </a>
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
        const first = y.months.slice(0, 6).map(m => `<td class="${cellClass(m)}">${cellHtml(y.year, m)}</td>`).join('');
        const second = y.months.slice(6, 12).map(m => `<td class="${cellClass(m)}">${cellHtml(y.year, m)}</td>`).join('');
        return `<tr><th class="text-center" rowspan="2"><div class="fw-semibold">${y.year}</div><button type="button" class="btn btn-outline-info btn-sm mt-2 year-check-btn" data-year="${y.year}">115整年校对</button></th>${first}</tr><tr>${second}</tr>`;
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

let yearCheckIntervalId = null;
let yearCheckYear = null;

function resetYearCheckUI() {
    document.getElementById('yearCheckText').textContent = '准备中...';
    document.getElementById('yearCheckCount').textContent = '';
    document.getElementById('yearCheckErrors').textContent = '';
    const bar = document.getElementById('yearCheckBar');
    bar.style.width = '0%';
    bar.setAttribute('aria-valuenow', '0');
}

function openYearCheckModal(year) {
    yearCheckYear = year;
    document.getElementById('yearCheckTitle').textContent = `年份：${year}`;
    resetYearCheckUI();
    new bootstrap.Modal(document.getElementById('yearCheckModal')).show();
}

function pollYearCheckStatus() {
    fetch(`${basePath}/tool/api.php?action=115_check_all_status`)
        .then(r => r.json())
        .then(status => {
            const bar = document.getElementById('yearCheckBar');
            if (status.running) {
                const progress = status.total > 0 ? Math.round(status.checked / status.total * 100) : 0;
                document.getElementById('yearCheckText').textContent = `校对中... (已检查 ${status.checked}/${status.total})`;
                document.getElementById('yearCheckCount').textContent = `发现 ${status.found_downloaded} 条已下载`;
                bar.style.width = `${progress}%`;
                bar.setAttribute('aria-valuenow', `${progress}`);
                if (status.errors && status.errors.length > 0) {
                    document.getElementById('yearCheckErrors').textContent = '错误（前10条）:\n' + status.errors.join('\n');
                }
                return;
            }

            if (yearCheckIntervalId) {
                clearInterval(yearCheckIntervalId);
                yearCheckIntervalId = null;
            }

            bootstrap.Modal.getInstance(document.getElementById('yearCheckModal'))?.hide();

            let msg = `校对完成\n\n总记录: ${status.total}\n已检查: ${status.checked}\n发现已下载: ${status.found_downloaded}`;
            if (status.errors && status.errors.length > 0) {
                msg += '\n\n错误（前10条）:\n' + status.errors.join('\n');
            }
            alert(msg);

            const sel = document.getElementById('calendar-year');
            loadCalendar(sel.value);
        })
        .catch(() => {});
}

function startYearCheck(year) {
    openYearCheckModal(year);
    if (yearCheckIntervalId) {
        clearInterval(yearCheckIntervalId);
        yearCheckIntervalId = null;
    }

    fetch(`${basePath}/tool/api.php?action=115_check_all_start`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ year: parseInt(year, 10) }),
    }).then(r => r.json()).then(res => {
        if (res.status === 'error') {
            alert('启动失败: ' + (res.message || '未知错误'));
            bootstrap.Modal.getInstance(document.getElementById('yearCheckModal'))?.hide();
            return;
        }
        yearCheckIntervalId = setInterval(pollYearCheckStatus, 3000);
    }).catch(err => {
        alert('启动失败: ' + err.message);
        bootstrap.Modal.getInstance(document.getElementById('yearCheckModal'))?.hide();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initYears();
    document.getElementById('calendar-year').addEventListener('change', (e) => {
        loadCalendar(e.target.value);
    });

    document.getElementById('calendar-body').addEventListener('click', (e) => {
        const btn = e.target.closest('.year-check-btn');
        if (!btn) return;
        const year = btn.dataset.year;
        if (!year) return;
        if (!confirm(`将对 ${year} 年所有有磁链且未标记已下载的记录进行115校对，是否继续？`)) return;
        startYearCheck(year);
    });

    document.getElementById('yearCheckStopBtn').addEventListener('click', () => {
        fetch(`${basePath}/tool/api.php?action=115_check_all_stop`, { method: 'POST' }).catch(() => {});
        if (yearCheckIntervalId) {
            clearInterval(yearCheckIntervalId);
            yearCheckIntervalId = null;
        }
        bootstrap.Modal.getInstance(document.getElementById('yearCheckModal'))?.hide();
    });
});
    </script>
</body>
</html>
