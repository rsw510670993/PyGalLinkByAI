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
                    <div class="col-12 col-lg-5 d-flex gap-2 justify-content-lg-end">
                        <button id="get-all-links" class="btn btn-success" type="button">全部下载链接</button>
                    </div>
                    <div class="col-12">
                        <textarea id="download-links-output" class="form-control" rows="6" style="display:none;"></textarea>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header d-flex align-items-center justify-content-between">
                <div class="fw-semibold">游戏列表</div>
                <div id="page-info" class="text-muted small">第1页</div>
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

    <script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.6.4/jquery.min.js"></script>
    <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap-datepicker/1.9.0/js/bootstrap-datepicker.min.js"></script>
    <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap-datepicker/1.9.0/locales/bootstrap-datepicker.zh-CN.min.js"></script>
    <script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>

    <script>
const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;
let currentPage = 1;

function updateTable(data) {

    const tbody = document.querySelector('#gamesTable tbody');

    const urlCounts = {};
    data.forEach(game => {
        if (game.download_url) {
            urlCounts[game.download_url] = (urlCounts[game.download_url] || 0) + 1;
        }
    });

    tbody.innerHTML = data.map(game => {
        let btnClass = 'btn-primary';
        if (game.download_url && urlCounts[game.download_url] > 1) {
            btnClass = 'btn-danger';
        }
        return `
        <tr>
            <td class="check-col text-center">
                ${(game.download_url) ?
                    `<input type="checkbox"
                        class="game-checkbox"
                        ${game.download_url ? 'checked' : ''}
                        onchange="handleCheckboxChange(this)">`
                    : ''}
            </td>
            <td class="ym-col">${game.year}/${game.month}</td>
            <td class="game-name-cell">${game.name}${game.nyaa_name ? `<div class="text-muted small" style="display:${game.download_url ? 'none' : ''}">${game.nyaa_name}</div>` : ''}</td>
            <td class="company-col">${game.company}</td>
            <td class="actions-col">
                ${game.download_url ?
                    `<div class="btn-group actions-group" role="group" style="display:${game.download_url ? '' : 'none'}">
                        <a href="${game.download_url}" class="btn ${btnClass} download-btn btn-sm">下载</a>
                        <button type="button" class="btn btn-outline-secondary btn-sm magnet-check-btn"
                            data-magnet="${encodeURIComponent(game.download_url)}"
                            data-name="${encodeURIComponent(game.name)}"
                            data-company="${encodeURIComponent(game.company)}">校验</button>
                    </div>`
                    : ''
                }
            </td>
        </tr>
        `;
    }).join('');
}

function parseMonthValue(value) {
    const raw = (value || '').trim();
    if (!raw) return null;
    const m = raw.match(/^(\d{4})-(\d{1,2})$/);
    if (!m) return null;
    return { year: m[1], month: String(parseInt(m[2], 10)) };
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
            updateTable(res.data);

            currentPage = res.current_page;
            document.getElementById('page-info').textContent =
                `第${currentPage}页，共${Math.ceil(res.total / res.per_page)}页`;

            document.getElementById('prev-page').disabled = currentPage <= 1;
            document.getElementById('next-page').disabled =
                currentPage >= Math.ceil(res.total / res.per_page);
        })
        .catch(() => {
            alert('筛选失败，请检查接口是否可访问');
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
    loadPage(1);
});

document.getElementById('prev-page').addEventListener('click', () => {
    if (currentPage > 1) loadPage(currentPage - 1);
});

document.getElementById('next-page').addEventListener('click', () => {
    loadPage(currentPage + 1);
});

function getAllDownloadLinks() {
    const downloadLinks = Array.from(document.querySelectorAll('#gamesTable a.download-btn'))
        .filter(link => link.offsetParent !== null)
        .map(link => link.href)
        .join('\n');

    const outputBox = document.getElementById('download-links-output');

    if (downloadLinks) {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(downloadLinks)
                .then(() => {
                    alert('下载链接已复制到剪贴板!');
                    outputBox.style.display = 'none';
                })
                .catch(err => {
                    console.error('无法复制到剪贴板:', err);
                    outputBox.value = downloadLinks;
                    outputBox.style.display = 'block';
                    alert('自动复制失败，链接已显示在下方文本框中');
                });
        } else {
            outputBox.value = downloadLinks;
            outputBox.style.display = 'block';
            alert('您的浏览器不支持自动复制，链接已显示在下方文本框中');
        }
    } else {
        alert('没有找到可下载的链接');
    }
}

function handleCheckboxChange(checkbox) {
    const tr = checkbox.closest('tr');
    const group = tr.querySelector('.actions-group');
    if (group) group.style.display = checkbox.checked ? '' : 'none';
    const nyaaDiv = tr.querySelector('.text-muted');
    if(nyaaDiv) nyaaDiv.style.display = checkbox.checked ? 'none' : '';
}

document.getElementById('get-all-links').addEventListener('click', getAllDownloadLinks);

document.querySelector('#gamesTable tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('.magnet-check-btn');
    if (!btn) return;
    const magnet = decodeURIComponent(btn.dataset.magnet || '');
    const name = decodeURIComponent(btn.dataset.name || '');
    const company = decodeURIComponent(btn.dataset.company || '');
    openMagnetCheck(magnet, name, company);
});

function openMagnetCheck(magnet, name, company) {
    if (!magnet) return;
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = `${basePath}/tool/magnet_check.php`;
    form.target = '_blank';

    const fields = { magnet, name, company };
    Object.keys(fields).forEach((k) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = k;
        input.value = fields[k];
        form.appendChild(input);
    });

    document.body.appendChild(form);
    form.submit();
    form.remove();
}
</script>
</body>
</html>
