<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>游戏数据展示</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap-datepicker/1.9.0/css/bootstrap-datepicker.min.css" rel="stylesheet">
    <style>
        #gamesTable {
            table-layout: fixed;
        }

        .game-name-cell {
            width: 320px;
            min-width: 260px;
            max-width: 420px;
            white-space: normal;
            word-wrap: break-word;
        }

        .check-col {
            width: 64px;
            max-width: 64px;
        }

        .check-col input[type="checkbox"] {
            margin: 0;
            vertical-align: middle;
        }

        .ym-col {
            width: 96px;
            max-width: 96px;
            white-space: nowrap;
        }

        .company-col {
            width: 180px;
            max-width: 180px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .actions-col {
            width: 240px;
            max-width: 240px;
            white-space: nowrap;
        }

        .actions-col .btn-group {
            max-width: 100%;
        }

        .datepicker-dropdown {
            margin-top: 30px;
        }

        .editable-cell {
            cursor: pointer;
            transition: background-color 0.15s;
        }

        .editable-cell:hover {
            background-color: rgba(13, 110, 253, 0.08);
            text-decoration: underline;
            text-decoration-style: dotted;
            text-underline-offset: 3px;
        }

        .editable-cell input {
            width: 100%;
            border: 1px solid #0d6efd;
            border-radius: 3px;
            padding: 2px 4px;
            font-size: inherit;
            font-family: inherit;
        }
    </style>
</head>
<body style="padding-top: 56px;">
<?php $base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/'); ?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">已采集游戏数据</a>
            <div class="navbar-nav">
                <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
                <a class="nav-link active" href="<?= $base ?>/tool/data.php">数据展示</a>
                <a class="nav-link" href="<?= $base ?>/calendar.php">年历</a>
            </div>
        </div>
    </nav>
    <div class="container mt-4">
        <div class="card mb-3">
            <div class="card-body">
                <div class="row g-2 align-items-end">
                    <div class="col-12 col-lg-7" style="position: relative;">
                        <label for="month-picker" class="form-label mb-1">选择月份</label>
                        <div class="input-group">
                            <button id="prev-year" class="btn btn-outline-secondary" type="button">去年</button>
                            <button id="prev-month" class="btn btn-outline-secondary" type="button">上月</button>
                            <input type="text" class="form-control" id="month-picker" placeholder="选择月份">
                            <button id="next-month" class="btn btn-outline-secondary" type="button">下月</button>
                            <button id="next-year" class="btn btn-outline-secondary" type="button">明年</button>
                        </div>
                    </div>
                    <div class="col-12 col-lg-5 d-flex gap-2 justify-content-end">
                        <button id="toggle-select" class="btn btn-outline-secondary" type="button">全选</button>
                        <button id="batch-115-check" class="btn btn-outline-info" type="button">批量校验已下载</button>
                        <button id="batch-115-download" class="btn btn-success" type="button">批量115云下载</button>
                    </div>
                </div>
            </div>
        </div>

        <div class="card mb-3" id="check-progress-card" style="display:none;">
            <div class="card-body">
                <div class="d-flex justify-content-between small text-muted mb-1">
                    <span id="check-progress-text">准备中...</span>
                    <span id="check-progress-count"></span>
                </div>
                <div class="progress" style="height: 18px;">
                    <div class="progress-bar progress-bar-striped progress-bar-animated bg-info" id="check-progress-bar"
                        role="progressbar" style="width: 0%;" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>
                </div>
            </div>
        </div>

        <div class="card mb-3" id="panel-115-login">
            <div class="card-body py-2 d-flex align-items-center gap-2 flex-wrap">
                <span class="fw-semibold me-1">115 网盘</span>
                <span id="data-115-badge" class="badge bg-secondary">未登录</span>
                <span id="data-115-user" class="text-muted small"></span>
                <button id="data-115-login-btn" class="btn btn-primary btn-sm ms-auto">扫码登录</button>
                <button id="data-115-logout-btn" class="btn btn-outline-danger btn-sm ms-auto" style="display:none;">退出登录</button>
                <div id="data-115-qrcode-section" style="display:none;" class="w-100 text-center">
                    <img id="data-115-qrcode-img" class="img-thumbnail mb-1" style="max-width:180px;max-height:180px;" alt="二维码">
                    <div id="data-115-qrcode-status" class="text-muted small mb-2">等待扫码...</div>
                    <div class="d-flex gap-2 justify-content-center">
                        <button id="data-115-refresh-qrcode" class="btn btn-outline-secondary btn-sm">刷新</button>
                        <button id="data-115-cancel-login" class="btn btn-outline-danger btn-sm">取消</button>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header d-flex align-items-center justify-content-between">
                <div class="fw-semibold">游戏列表</div>
                <div class="d-flex align-items-center gap-2">
                    <span id="month-all-downloaded-badge" class="badge bg-success" style="display:none;">全部已下载</span>
                    <div id="page-info" class="text-muted small">第1页</div>
                </div>
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
                    <tbody></tbody>
                </table>
            </div>
            <div class="card-footer d-flex justify-content-between align-items-center">
                <button id="prev-page" class="btn btn-outline-primary btn-sm" type="button">上一页</button>
                <button id="next-page" class="btn btn-outline-primary btn-sm" type="button">下一页</button>
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
                    <input type="hidden" id="check-modal-date">
                    <input type="hidden" id="check-modal-name">
                    <div class="mb-2">
                        <span id="modal-115-badge" class="badge bg-secondary">未登录</span>
                        <span id="modal-115-user" class="text-muted small ms-2"></span>
                    </div>
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
                        <a id="modal-debug-link" class="btn btn-outline-secondary btn-sm" target="_blank" rel="noopener">调试</a>
                    </div>
                    <div id="modal-result" class="small" style="white-space:pre-wrap;word-break:break-all;"></div>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade" id="editModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">编辑游戏信息</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="edit-old-name">
                    <input type="hidden" id="edit-date">
                    <input type="hidden" id="edit-old-magnet">
                    <input type="hidden" id="edit-old-nyaa-name">
                    <div class="mb-2">
                        <label class="form-label mb-0 small">游戏名称</label>
                        <input id="edit-game-name" class="form-control">
                    </div>
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
                        <label class="form-label mb-0 small">公司</label>
                        <input id="edit-company" class="form-control">
                    </div>
                    <div class="mb-2">
                        <label class="form-label mb-0 small">Nyaa名称</label>
                        <input id="edit-nyaa-name" class="form-control" placeholder="nyaa 站点上的游戏名称">
                    </div>
                    <div class="mb-2">
                        <label class="form-label mb-0 small">磁力链接</label>
                        <textarea id="edit-magnet" class="form-control" rows="2"></textarea>
                    </div>
                    <div class="mb-2">
                        <label class="form-label mb-0 small">已下载状态</label>
                        <select id="edit-downloaded" class="form-select">
                            <option value="0">未下载 / 未知</option>
                            <option value="1">已下载到115</option>
                        </select>
                    </div>
                </div>
                <div class="modal-footer d-flex justify-content-between">
                    <button class="btn btn-sm btn-outline-danger" id="edit-delete-btn">删除记录</button>
                    <div>
                        <button class="btn btn-sm btn-primary" id="edit-save-btn">保存</button>
                        <button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.6.4/jquery.min.js"></script>
    <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap-datepicker/1.9.0/js/bootstrap-datepicker.min.js"></script>
    <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap-datepicker/1.9.0/locales/bootstrap-datepicker.zh-CN.min.js"></script>
    <script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>

    <script>
const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;
let currentPage = 1;
let suppressDateChangeLoad = false;

function updateTable(data) {

    const tbody = document.querySelector('#gamesTable tbody');

    tbody.innerHTML = data.map(game => {
        const isDownloaded = game.downloaded == 1;
        const isSubmitted = game.submitted_115 == 1;
        const rowClass = isDownloaded ? ' class="table-success"' : isSubmitted ? ' class="table-info"' : '';
        const dateFull = `${game.year}-${String(game.month).padStart(2, '0')}`;
        const magnet = game.download_url || '';
        const encName = encodeURIComponent(game.name);
        const encNyaa = game.nyaa_name ? encodeURIComponent(game.nyaa_name) : '';
        return `
        <tr${rowClass} data-date="${dateFull}" data-name="${encName}" data-magnet="${encodeURIComponent(magnet)}" data-downloaded="${game.downloaded || 0}" data-submitted-115="${game.submitted_115 || 0}" data-nyaa-name="${encNyaa}">
            <td class="check-col text-center">
                ${!magnet ? '<span class="text-muted small">-</span>'
                    : isDownloaded ? '<span class="badge bg-success">已下载</span>'
                    : isSubmitted ? '<span class="badge bg-primary">已提交</span>'
                    : `<input type="checkbox" class="game-checkbox" checked onchange="handleCheckboxChange(this)">`
                }
            </td>
            <td class="ym-col">${game.year}/${game.month}</td>
            <td class="game-name-cell editable-cell" data-field="name">${game.name}${game.nyaa_name ? `<div class="text-muted small" style="display:${magnet ? 'none' : ''}">${game.nyaa_name}</div>` : ''}</td>
            <td class="company-col editable-cell" data-field="company">${game.company}</td>
            <td class="actions-col">
                ${magnet ?
                    `<div class="btn-group actions-group" role="group">
                        <button type="button" class="btn btn-success btn-sm btn-115-submit"${isSubmitted ? ' disabled' : ''}
                            data-magnet="${encodeURIComponent(magnet)}"
                            data-year="${game.year}">115云下载</button>
                        <button type="button" class="btn btn-outline-secondary btn-sm magnet-check-btn"
                            data-magnet="${encodeURIComponent(magnet)}"
                            data-name="${encName}"
                            data-year="${game.year}"
                            data-date="${dateFull}">校验</button>
                    </div>`
                    : '<span class="text-muted small">暂无可编辑</span>'
                }
            </td>
        </tr>
        `;
    }).join('');

    updateBatchButtons();
    updateToggleButtonText();
}

function parseMonthValue(value) {
    const raw = (value || '').trim();
    if (!raw) return null;
    const m = raw.match(/^(\d{4})-(\d{1,2})$/);
    if (!m) return null;
    return { year: m[1], month: String(parseInt(m[2], 10)) };
}

function showEmptyMessage(message) {
    const tbody = document.querySelector('#gamesTable tbody');
    tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted py-4">${message}</td></tr>`;
    document.getElementById('page-info').textContent = '';
    updateMonthAllDownloadedBadge(null, null);
    document.getElementById('prev-page').disabled = true;
    document.getElementById('next-page').disabled = true;
}

function updateMonthAllDownloadedBadge(monthValue, monthStats) {
    const badge = document.getElementById('month-all-downloaded-badge');
    if (!badge) return;
    const hasMonth = !!(monthValue && parseMonthValue(monthValue));
    const ok = !!(hasMonth && monthStats && monthStats.all_magnet_downloaded);
    badge.style.display = ok ? '' : 'none';
}

function loadPage(page) {
    const monthValue = document.getElementById('month-picker').value;

    let url = `${basePath}/tool/api.php?action=games&page=${page}`;
    if (monthValue) {
        const parsed = parseMonthValue(monthValue);
        if (!parsed) {
            alert('月份格式不正确，请使用 YYYY-MM');
            return;
        }
        url += `&year=${parsed.year}&month=${parsed.month}`;
    }

    fetch(url)
        .then(response => response.json())
        .then(res => {
            if (res.status === 'error') {
                let msg = '数据加载失败：' + (res.message || '未知错误');
                if (res.stderr) msg += ' (stderr: ' + res.stderr + ')';
                showEmptyMessage(msg);
                return;
            }

            if (res.total === 0) {
                showEmptyMessage('暂无符合条件的数据');
                return;
            }

            updateTable(res.data);
            updateToggleButtonText();
            updateMonthAllDownloadedBadge(monthValue, res.month_stats);

            currentPage = res.current_page;
            document.getElementById('page-info').textContent =
                `第${currentPage}页，共${Math.ceil(res.total / res.per_page)}页`;

            document.getElementById('prev-page').disabled = currentPage <= 1;
            document.getElementById('next-page').disabled =
                currentPage >= Math.ceil(res.total / res.per_page);
        })
        .catch(() => {
            showEmptyMessage('筛选失败，请检查接口是否可访问');
        });
}

function initMonthPicker() {
    $('#month-picker').datepicker({
        language: 'zh-CN',
        orientation: 'auto',
        format: 'yyyy-mm',
        minViewMode: 'months',
        autoclose: true,
        todayHighlight: true,
        clearBtn: true
    }).on('changeDate', function() {
        if (suppressDateChangeLoad) return;
        loadPage(1);
    });

    document.getElementById('prev-year').addEventListener('click', () => {
        const date = $('#month-picker').datepicker('getDate');
        if (date) {
            date.setFullYear(date.getFullYear() - 1);
            $('#month-picker').datepicker('setDate', date);
            loadPage(1);
        }
    });

    document.getElementById('prev-month').addEventListener('click', () => {
        const date = $('#month-picker').datepicker('getDate');
        if (date) {
            date.setMonth(date.getMonth() - 1);
            $('#month-picker').datepicker('setDate', date);
            loadPage(1);
        }
    });

    document.getElementById('next-month').addEventListener('click', () => {
        const date = $('#month-picker').datepicker('getDate');
        if (date) {
            date.setMonth(date.getMonth() + 1);
            $('#month-picker').datepicker('setDate', date);
            loadPage(1);
        }
    });

    document.getElementById('next-year').addEventListener('click', () => {
        const date = $('#month-picker').datepicker('getDate');
        if (date) {
            date.setFullYear(date.getFullYear() + 1);
            $('#month-picker').datepicker('setDate', date);
            loadPage(1);
        }
    });
}

window.addEventListener('DOMContentLoaded', () => {
    initMonthPicker();
    data115CheckLoginStatus();

    const params = new URLSearchParams(window.location.search);
    const paramYear = params.get('year');
    const paramMonth = params.get('month');

    if (paramYear && paramMonth) {
        suppressDateChangeLoad = true;
        $('#month-picker').datepicker('setDate', new Date(parseInt(paramYear, 10), parseInt(paramMonth, 10) - 1, 1));
        suppressDateChangeLoad = false;
        loadPage(1);
    } else {
        fetch(`${basePath}/tool/api.php?action=latest_month`)
            .then(r => r.json())
            .then((res) => {
                const y = res && res.year ? parseInt(res.year, 10) : null;
                const m = res && res.month ? parseInt(res.month, 10) : null;
                if (y && m) {
                    suppressDateChangeLoad = true;
                    $('#month-picker').datepicker('setDate', new Date(y, m - 1, 1));
                    suppressDateChangeLoad = false;
                }
                loadPage(1);
            })
            .catch(() => {
                loadPage(1);
            });
    }
});

document.getElementById('prev-page').addEventListener('click', () => {
    if (currentPage > 1) loadPage(currentPage - 1);
});

document.getElementById('next-page').addEventListener('click', () => {
    loadPage(currentPage + 1);
});

function updateToggleButtonText() {
    const btn = document.getElementById('toggle-select');
    const checkboxes = document.querySelectorAll('#gamesTable tbody input.game-checkbox');
    const anyChecked = Array.from(checkboxes).some(cb => cb.checked);
    btn.textContent = anyChecked ? '全不选' : '全选';
}

function updateBatchButtons() {
    const anyChecked = Array.from(document.querySelectorAll('#gamesTable tbody input.game-checkbox')).some(cb => cb.checked);
    const anyNeedCheck = Array.from(document.querySelectorAll('#gamesTable tbody tr')).some((tr) => {
        const magnet = tr.dataset.magnet ? decodeURIComponent(tr.dataset.magnet) : '';
        const downloaded = parseInt(tr.dataset.downloaded || '0', 10) === 1;
        return !!magnet && !downloaded;
    });
    document.getElementById('batch-115-download').disabled = !anyChecked;
    document.getElementById('batch-115-check').disabled = !anyNeedCheck;
}

function handleCheckboxChange(checkbox) {
    const tr = checkbox.closest('tr');
    const nyaaDiv = tr.querySelector('.text-muted');
    if(nyaaDiv) nyaaDiv.style.display = checkbox.checked ? 'none' : '';
    updateToggleButtonText();
    updateBatchButtons();
}

function batch115Download() {
    const rows = Array.from(document.querySelectorAll('#gamesTable tbody tr'))
        .filter((tr) => {
            const cb = tr.querySelector('input.game-checkbox');
            return cb && cb.checked;
        });

    if (rows.length === 0) {
        alert('请先勾选需要下载的游戏');
        return;
    }

    const btn = document.getElementById('batch-115-download');
    btn.disabled = true;
    btn.textContent = '提交中...';

    let success = 0;
    let fail = 0;
    const results = [];

    function submitNext(index) {
        if (index >= rows.length) {
            btn.disabled = false;
            btn.textContent = '批量115云下载';
            const msg = `提交完成：成功 ${success} 条，失败 ${fail} 条`;
            if (fail > 0) {
                alert(msg + '\n\n失败详情：\n' + results.filter(r => !r.ok).map(r => '  - ' + r.name + ': ' + r.reason).join('\n'));
            } else {
                alert(msg);
            }
            return;
        }

        const tr = rows[index];
        const ymText = tr.querySelector('.ym-col')?.textContent || '';
        const year = ymText.split('/')[0] || '';
        const name = tr.querySelector('.game-name-cell')?.textContent?.trim() || '';
        const subBtn = tr.querySelector('.btn-115-submit');
        const magnet = subBtn ? decodeURIComponent(subBtn.dataset.magnet || '') : '';

        if (!magnet || !year) {
            fail++;
            results.push({ ok: false, name, reason: !magnet ? '无磁链' : '无年份' });
            submitNext(index + 1);
            return;
        }

        const savePath = `/GAL/GAL-${year}`;

        fetch(`${basePath}/tool/api.php?action=115_submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ magnet, dir: savePath }),
        })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                success++;
                results.push({ ok: true, name });
                const date = tr.dataset.date || '';
                const rawName = tr.dataset.name ? decodeURIComponent(tr.dataset.name) : '';
                if (date && rawName) {
                    fetch(`${basePath}/tool/api.php?action=update_game`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            date,
                            old_name: rawName,
                            new_submitted_115: 1,
                            new_submitted_pick_code: res.pick_code || '',
                        }),
                    }).catch(() => {});
                }
            } else {
                fail++;
                results.push({ ok: false, name, reason: res.message || '未知错误' });
            }
            submitNext(index + 1);
        })
        .catch(err => {
            fail++;
            results.push({ ok: false, name, reason: err.message });
            submitNext(index + 1);
        });
    }

    submitNext(0);
}

function setAllCheckboxes(checked) {
    Array.from(document.querySelectorAll('#gamesTable tbody input.game-checkbox')).forEach((cb) => {
        cb.checked = checked;
        const tr = cb.closest('tr');
        const nyaaDiv = tr.querySelector('.text-muted');
        if(nyaaDiv) nyaaDiv.style.display = checked ? 'none' : '';
    });
    updateToggleButtonText();
}

document.getElementById('toggle-select').addEventListener('click', () => {
    const btn = document.getElementById('toggle-select');
    setAllCheckboxes(btn.textContent === '全选');
});

document.getElementById('batch-115-download').addEventListener('click', batch115Download);

let checkAllIntervalId = null;

document.getElementById('batch-115-check').addEventListener('click', function() {
    const btn = this;
    if (!confirm('将对当前月份的所有游戏进行115云下载校验（含已下载记录），是否继续？')) return;

    btn.disabled = true;
    btn.textContent = '启动中...';

    if (checkAllIntervalId) {
        clearInterval(checkAllIntervalId);
        checkAllIntervalId = null;
    }

    const monthValue = document.getElementById('month-picker').value;
    const parsed = parseMonthValue(monthValue);
    const body = {};
    if (parsed) {
        body.year = parseInt(parsed.year, 10);
        body.month = parseInt(parsed.month, 10);
    }

    fetch(`${basePath}/tool/api.php?action=115_check_all_start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
        .then(r => r.json())
        .then(res => {
            if (res.status === 'error') {
                alert('启动校验失败: ' + res.message);
                btn.disabled = false;
                btn.textContent = '批量校验已下载';
                return;
            }

            document.getElementById('check-progress-card').style.display = '';
            document.getElementById('check-progress-text').textContent = '校验中...';
            document.getElementById('check-progress-bar').style.width = '0%';
            document.getElementById('check-progress-bar').setAttribute('aria-valuenow', '0');
            document.getElementById('check-progress-count').textContent = '';

            checkAllIntervalId = setInterval(pollCheckAllStatus, 3000);
        })
        .catch(err => {
            alert('启动校验失败: ' + err.message);
            btn.disabled = false;
            btn.textContent = '批量校验已下载';
        });
});

function pollCheckAllStatus() {
    fetch(`${basePath}/tool/api.php?action=115_check_all_status`)
        .then(r => r.json())
        .then(status => {
            if (status.running) {
                const progress = status.total > 0 ? Math.round(status.checked / status.total * 100) : 0;
                document.getElementById('check-progress-text').textContent =
                    `校验中... (已检查 ${status.checked}/${status.total})`;
                document.getElementById('check-progress-bar').style.width = `${progress}%`;
                document.getElementById('check-progress-bar').setAttribute('aria-valuenow', `${progress}`);
                document.getElementById('check-progress-count').textContent =
                    `发现 ${status.found_downloaded} 条已下载`;
            } else {
                clearInterval(checkAllIntervalId);
                checkAllIntervalId = null;

                document.getElementById('check-progress-card').style.display = 'none';

                const btn = document.getElementById('batch-115-check');
                btn.disabled = false;
                btn.textContent = '批量校验已下载';

                let msg = `校验完成\n\n总记录: ${status.total}\n已检查: ${status.checked}\n发现已下载: ${status.found_downloaded}`;
                if (status.errors && status.errors.length > 0) {
                    msg += '\n\n错误（前10条）:\n' + status.errors.join('\n');
                }
                alert(msg);
                loadPage(currentPage);
            }
        })
        .catch(err => {
            clearInterval(checkAllIntervalId);
            checkAllIntervalId = null;

            document.getElementById('check-progress-card').style.display = 'none';

            const btn = document.getElementById('batch-115-check');
            btn.disabled = false;
            btn.textContent = '批量校验已下载';
            alert('获取校验状态失败: ' + err.message);
        });
}

// === 115 登录 ===
let data115QrPollTimer = null;
let data115QrData = null;

function data115CheckLoginStatus() {
    fetch(`${basePath}/tool/api.php?action=115_login_status`)
        .then(r => r.json())
        .then(res => {
            const badge = document.getElementById('data-115-badge');
            const userEl = document.getElementById('data-115-user');
            const loginBtn = document.getElementById('data-115-login-btn');
            const logoutBtn = document.getElementById('data-115-logout-btn');
            if (res.logged_in) {
                badge.className = 'badge bg-success ms-2';
                badge.textContent = '已登录';
                loginBtn.style.display = 'none';
                logoutBtn.style.display = '';
                userEl.textContent = res.user || '';
            } else {
                badge.className = 'badge bg-secondary ms-2';
                badge.textContent = '未登录';
                loginBtn.style.display = '';
                logoutBtn.style.display = 'none';
                userEl.textContent = res.reason || '';
            }
            const modalBadge = document.getElementById('modal-115-badge');
            const modalUser = document.getElementById('modal-115-user');
            if (modalBadge) {
                modalBadge.className = res.logged_in ? 'badge bg-success' : 'badge bg-secondary';
                modalBadge.textContent = res.logged_in ? '已登录' : '未登录';
                modalUser.textContent = res.logged_in ? (res.user || '') : (res.reason || '');
            }
        }).catch(() => {});
}

function data115StartQRLogin() {
    document.getElementById('data-115-login-btn').disabled = true;
    document.getElementById('data-115-qrcode-section').style.display = '';
    document.getElementById('data-115-login-actions').style.display = 'none';
    data115FetchQRCode();
}

function data115FetchQRCode() {
    document.getElementById('data-115-qrcode-status').textContent = '获取二维码中...';
    fetch(`${basePath}/tool/api.php?action=115_login_qrcode`)
        .then(r => r.json())
        .then(res => {
            if (!res.qrcode_base64) {
                document.getElementById('data-115-qrcode-status').textContent = '获取二维码失败';
                return;
            }
            data115QrData = res;
            document.getElementById('data-115-qrcode-img').src = 'data:image/png;base64,' + res.qrcode_base64;
            document.getElementById('data-115-qrcode-status').textContent = '请使用 115 App 扫码';
            if (data115QrPollTimer) clearInterval(data115QrPollTimer);
            data115QrPollTimer = setInterval(data115PollQRStatus, 2000);
        }).catch(() => {
            document.getElementById('data-115-qrcode-status').textContent = '获取二维码失败，请重试';
        });
}

function data115PollQRStatus() {
    if (!data115QrData) return;
    const uid = data115QrData.uid, time = data115QrData.time, sign = data115QrData.sign;
    fetch(`${basePath}/tool/api.php?action=115_login_qrcode_status&uid=${encodeURIComponent(uid)}&time=${encodeURIComponent(time)}&sign=${encodeURIComponent(sign)}`)
        .then(r => r.json())
        .then(res => {
            const el = document.getElementById('data-115-qrcode-status');
            if (res.status === 2) {
                clearInterval(data115QrPollTimer);
                data115QrPollTimer = null;
                el.textContent = '扫码成功，确认登录中...';
                fetch(`${basePath}/tool/api.php?action=115_login_confirm`, {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({uid, app:'alipaymini'}),
                }).then(r => r.json()).then(cr => {
                    if (cr.success) {
                        el.textContent = '登录成功';
                        data115CancelQRLogin();
                        data115CheckLoginStatus();
                    } else {
                        el.textContent = '登录失败: ' + (cr.message || '');
                    }
                });
            } else if (res.status === 1) {
                el.textContent = '已扫描，请在手机上确认';
            } else if (res.status === -1) {
                clearInterval(data115QrPollTimer);
                data115QrPollTimer = null;
                el.textContent = '二维码已过期，请刷新';
            } else {
                el.textContent = '等待扫码...';
            }
        }).catch(() => {});
}

function data115CancelQRLogin() {
    if (data115QrPollTimer) { clearInterval(data115QrPollTimer); data115QrPollTimer = null; }
    document.getElementById('data-115-login-btn').disabled = false;
    document.getElementById('data-115-qrcode-section').style.display = 'none';
    document.getElementById('data-115-login-actions').style.display = '';
    data115QrData = null;
}

document.getElementById('data-115-login-btn').addEventListener('click', data115StartQRLogin);
document.getElementById('data-115-refresh-qrcode').addEventListener('click', data115FetchQRCode);
document.getElementById('data-115-cancel-login').addEventListener('click', data115CancelQRLogin);
document.getElementById('data-115-logout-btn').addEventListener('click', () => {
    if (!confirm('确定退出 115 登录？')) return;
    fetch(`${basePath}/tool/api.php?action=115_logout`, {method:'POST'}).then(r=>r.json()).then(res => {
        if (res.success) data115CheckLoginStatus(); else alert('退出失败');
    });
});

// === 单行 115 云下载 ===
document.querySelector('#gamesTable tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-115-submit');
    if (!btn) return;
    const tr = btn.closest('tr');
    const magnet = decodeURIComponent(btn.dataset.magnet || '');
    const year = btn.dataset.year || '';
    if (!magnet) return;
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = '提交中...';
    const savePath = `/GAL/GAL-${year}`;
    fetch(`${basePath}/tool/api.php?action=115_submit`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({magnet, dir: savePath}),
    }).then(r => r.json()).then(res => {
        if (res.success) {
            const date = tr ? (tr.dataset.date || '') : '';
            const rawName = tr && tr.dataset.name ? decodeURIComponent(tr.dataset.name) : '';
            if (date && rawName) {
                fetch(`${basePath}/tool/api.php?action=update_game`, {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({
                        date,
                        old_name: rawName,
                        new_submitted_115: 1,
                        new_submitted_pick_code: res.pick_code || '',
                    }),
                }).then(() => {
                    loadPage(currentPage);
                }).catch(() => {
                    loadPage(currentPage);
                });
            }
            btn.className = 'btn btn-success btn-sm disabled';
            btn.textContent = '✓已提交';
            setTimeout(() => { btn.disabled = false; btn.className = 'btn btn-success btn-sm btn-115-submit'; btn.textContent = origText; }, 3000);
        } else {
            btn.className = 'btn btn-outline-danger btn-sm disabled';
            btn.textContent = '✗失败';
            setTimeout(() => { btn.disabled = false; btn.className = 'btn btn-success btn-sm btn-115-submit'; btn.textContent = origText; }, 3000);
        }
    }).catch(() => {
        btn.className = 'btn btn-outline-danger btn-sm disabled';
        btn.textContent = '✗失败';
        setTimeout(() => { btn.disabled = false; btn.className = 'btn btn-success btn-sm btn-115-submit'; btn.textContent = origText; }, 3000);
    });
});

// === 校验 Modal ===
document.querySelector('#gamesTable tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('.magnet-check-btn');
    if (!btn) return;
    const magnet = decodeURIComponent(btn.dataset.magnet || '');
    const name = decodeURIComponent(btn.dataset.name || '');
    const year = btn.dataset.year || '';
    const date = btn.dataset.date || '';
    openCheckModal(magnet, name, year, date);
});

function openCheckModal(magnet, name, year, date) {
    if (!magnet) return;
    document.getElementById('check-modal-date').value = date;
    document.getElementById('check-modal-name').value = name;
    document.getElementById('modal-game-name').textContent = name || '-';
    document.getElementById('modal-magnet').value = magnet;
    document.getElementById('modal-save-path').value = `/GAL/GAL-${year}`;
    const debugLink = document.getElementById('modal-debug-link');
    if (debugLink) {
        const dir = `/GAL/GAL-${year}`;
        debugLink.href = `${basePath}/tool/check_debug.php?magnet=${encodeURIComponent(magnet)}&dir=${encodeURIComponent(dir)}`;
    }
    document.getElementById('modal-result').textContent = '';
    document.getElementById('modal-check-btn').disabled = false;
    document.getElementById('modal-submit-btn').disabled = false;
    data115CheckLoginStatus();
    const modal = new bootstrap.Modal(document.getElementById('checkModal'));
    modal.show();
}

document.getElementById('modal-check-btn').addEventListener('click', function() {
    const magnet = document.getElementById('modal-magnet').value.trim();
    const dir = document.getElementById('modal-save-path').value.trim();
    if (!magnet) return;
    this.disabled = true;
    const resultEl = document.getElementById('modal-result');
    resultEl.textContent = '检查中...';
    fetch(`${basePath}/tool/api.php?action=115_check`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({magnet, dir}),
    }).then(r => r.json()).then(res => {
        let msg = '';
        if (res.exists) {
            msg += '该磁链已存在于 115 网盘\n';
            if (res.in_offline_tasks) msg += '（离线任务列表中）\n';
            msg += '置信度: ' + (res.confidence === 'high' ? '高' : res.confidence === 'low' ? '低' : '无') + '\n';
            if (res.matched_files && res.matched_files.length > 0) {
                msg += '\n匹配文件:\n';
                res.matched_files.forEach(f => { msg += '  - ' + f.name + '\n'; });
            }
            const date = document.getElementById('check-modal-date').value;
            const name = document.getElementById('check-modal-name').value;
            if (date && name) {
                fetch(`${basePath}/tool/api.php?action=update_game`, {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({date, old_name: name, new_downloaded: 1}),
                }).then(() => {
                    bootstrap.Modal.getInstance(document.getElementById('checkModal')).hide();
                    loadPage(currentPage);
                }).catch(() => {
                    bootstrap.Modal.getInstance(document.getElementById('checkModal')).hide();
                    loadPage(currentPage);
                });
            }
        } else {
            msg += '未在 115 网盘找到该磁链\n';
            if (res.infohash_hex) msg += 'InfoHash: ' + res.infohash_hex + '\n';
            if (res.dn) msg += '文件名: ' + res.dn;
            msg += '\n\n可点击「提交到115」将其加入离线下载';
        }
        resultEl.textContent = msg;
    }).catch(err => {
        resultEl.textContent = '检查失败: ' + err.message;
    }).finally(() => {
        this.disabled = false;
    });
});

document.getElementById('modal-submit-btn').addEventListener('click', function() {
    const magnet = document.getElementById('modal-magnet').value.trim();
    const dir = document.getElementById('modal-save-path').value.trim();
    if (!magnet) return;
    this.disabled = true;
    const resultEl = document.getElementById('modal-result');
    resultEl.textContent = '提交中...';
    fetch(`${basePath}/tool/api.php?action=115_submit`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({magnet, dir}),
    }).then(r => r.json()).then(res => {
        if (res.success) {
            resultEl.textContent = '提交成功！\nPick Code: ' + (res.pick_code || '') + '\n文件将保存到: ' + dir;
            const date = document.getElementById('check-modal-date').value;
            const name = document.getElementById('check-modal-name').value;
            if (date && name) {
                fetch(`${basePath}/tool/api.php?action=update_game`, {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({
                        date,
                        old_name: name,
                        new_submitted_115: 1,
                        new_submitted_pick_code: res.pick_code || '',
                    }),
                }).then(() => {
                    bootstrap.Modal.getInstance(document.getElementById('checkModal')).hide();
                    loadPage(currentPage);
                }).catch(() => {
                    bootstrap.Modal.getInstance(document.getElementById('checkModal')).hide();
                    loadPage(currentPage);
                });
            }
        } else {
            resultEl.textContent = '提交失败: ' + (res.message || '');
        }
    }).catch(err => {
        resultEl.textContent = '提交失败: ' + err.message;
    }).finally(() => {
        this.disabled = false;
    });
});

// === 编辑弹窗 ===
document.querySelector('#gamesTable tbody').addEventListener('click', (e) => {
    const cell = e.target.closest('.editable-cell');
    if (!cell) return;

    const row = cell.closest('tr');
    const nameCell = row.querySelector('.game-name-cell');
    const companyCell = row.querySelector('.company-col');

    const oldName = decodeURIComponent(row.dataset.name || '');
    const date = row.dataset.date || '';
    const magnet = decodeURIComponent(row.dataset.magnet || '');
    const downloaded = row.dataset.downloaded || '0';
    const oldNyaa = row.dataset.nyaaName ? decodeURIComponent(row.dataset.nyaaName) : '';

    const dateParts = date.split('-');
    const oldYear = dateParts[0] || '';
    const oldMonth = dateParts[1] ? parseInt(dateParts[1], 10).toString() : '';

    document.getElementById('edit-old-name').value = oldName;
    document.getElementById('edit-date').value = date;
    document.getElementById('edit-old-magnet').value = magnet;
    document.getElementById('edit-old-nyaa-name').value = oldNyaa;
    document.getElementById('edit-game-name').value = oldName;
    document.getElementById('edit-year').value = oldYear;
    document.getElementById('edit-month').value = oldMonth;
    document.getElementById('edit-company').value = companyCell?.textContent.trim() || '';
    document.getElementById('edit-nyaa-name').value = oldNyaa;
    document.getElementById('edit-magnet').value = magnet;
    document.getElementById('edit-downloaded').value = downloaded;

    new bootstrap.Modal(document.getElementById('editModal')).show();
});

document.getElementById('edit-save-btn').addEventListener('click', function() {
    const btn = this;
    const date = document.getElementById('edit-date').value;
    const oldName = document.getElementById('edit-old-name').value;
    const oldMagnet = document.getElementById('edit-old-magnet').value;
    const oldNyaa = document.getElementById('edit-old-nyaa-name').value;
    const newName = document.getElementById('edit-game-name').value.trim();
    const newYear = document.getElementById('edit-year').value.trim();
    const newMonth = document.getElementById('edit-month').value.trim();
    const newCompany = document.getElementById('edit-company').value.trim();
    const newNyaaName = document.getElementById('edit-nyaa-name').value.trim();
    const newLink = document.getElementById('edit-magnet').value.trim();
    const newDownloaded = parseInt(document.getElementById('edit-downloaded').value, 10);

    btn.disabled = true;
    btn.textContent = '保存中...';

    const newDate = newYear && newMonth ? `${newYear}-${String(parseInt(newMonth, 10)).padStart(2, '0')}` : '';

    const body = { date, old_name: oldName };
    if (newDate && newDate !== date) body.new_date = newDate;
    if (newName && newName !== oldName) body.new_name = newName;
    if (newCompany) body.new_company = newCompany;
    if (newLink !== oldMagnet) body.new_link = newLink;
    if (newNyaaName !== oldNyaa) body.new_nyaa_name = newNyaaName;
    body.new_downloaded = newDownloaded;

    fetch(`${basePath}/tool/api.php?action=update_game`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(r => r.json()).then(res => {
        if (res.success) {
            bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
            loadPage(currentPage);
        } else {
            alert('保存失败: ' + (res.message || ''));
        }
    }).catch(err => {
        alert('保存失败: ' + err.message);
    }).finally(() => {
        btn.disabled = false;
        btn.textContent = '保存';
    });
});

document.getElementById('editModal').addEventListener('hidden.bs.modal', () => {
    document.getElementById('edit-save-btn').disabled = false;
    document.getElementById('edit-save-btn').textContent = '保存';
});

document.getElementById('edit-delete-btn').addEventListener('click', function() {
    const date = document.getElementById('edit-date').value;
    const name = document.getElementById('edit-old-name').value;
    if (!confirm(`确定要删除该记录吗？\n\n${date} / ${name}`)) return;

    const btn = this;
    btn.disabled = true;
    btn.textContent = '删除中...';

    fetch(`${basePath}/tool/api.php?action=delete_game`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date, name }),
    }).then(r => r.json()).then(res => {
        if (res.success) {
            bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
            loadPage(currentPage);
        } else {
            alert('删除失败: ' + (res.message || ''));
        }
    }).catch(err => {
        alert('删除失败: ' + err.message);
    }).finally(() => {
        btn.disabled = false;
        btn.textContent = '删除记录';
    });
});
</script>
</body>
</html>
