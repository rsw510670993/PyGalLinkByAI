<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>115 下载</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        #qrcode-img { max-width: 240px; max-height: 240px; }
        #magnet-result { white-space: pre-wrap; word-break: break-all; }
    </style>
</head>
<body style="padding-top: 56px;">
<?php $base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/'); ?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">115 下载</a>
            <div class="navbar-nav">
                <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
                <a class="nav-link" href="<?= $base ?>/tool/data.php">数据展示</a>
                <a class="nav-link active" href="<?= $base ?>/tool/115.php">115 下载</a>
            </div>
        </div>
    </nav>
    <div class="container mt-4">
        <div class="row g-3">
            <div class="col-12 col-lg-6">
                <div class="card">
                    <div class="card-header d-flex align-items-center justify-content-between">
                        <span>115 网盘登录</span>
                        <span id="login-badge" class="badge bg-secondary">未登录</span>
                    </div>
                    <div class="card-body text-center" id="login-card-body">
                        <div id="qrcode-section" style="display:none;">
                            <img id="qrcode-img" class="img-thumbnail mb-2" alt="二维码">
                            <div id="qrcode-status" class="text-muted small mb-2">等待扫码...</div>
                            <div class="d-flex gap-2 justify-content-center">
                                <button id="refresh-qrcode" class="btn btn-outline-secondary btn-sm">刷新二维码</button>
                                <button id="cancel-login" class="btn btn-outline-danger btn-sm">取消</button>
                            </div>
                        </div>
                        <div id="login-actions">
                            <button id="login-btn" class="btn btn-primary">扫码登录</button>
                            <button id="logout-btn" class="btn btn-outline-danger" style="display:none;">退出登录</button>
                        </div>
                        <div id="login-user" class="mt-2 text-muted small"></div>
                    </div>
                </div>
            </div>
            <div class="col-12 col-lg-6">
                <div class="card">
                    <div class="card-header">磁力链接提交</div>
                    <div class="card-body">
                        <div class="mb-2">
                            <label class="form-label mb-1">磁力链接</label>
                            <textarea id="magnet-input" class="form-control" rows="2" placeholder="magnet:?xt=urn:btih:..."></textarea>
                        </div>
                        <div class="mb-3">
                            <label class="form-label mb-1">保存路径</label>
                            <input id="save-path" class="form-control" value="/我的下载/Getchu">
                        </div>
                        <div class="d-flex gap-2">
                            <button id="check-btn" class="btn btn-outline-info">检查是否已存在</button>
                            <button id="submit-btn" class="btn btn-success">提交到 115</button>
                        </div>
                        <div id="magnet-result" class="mt-3 small"></div>
                    </div>
                </div>
            </div>
            <div class="col-12">
                <div class="card">
                    <div class="card-header d-flex align-items-center justify-content-between">
                        <span>提交记录</span>
                        <button id="clear-records" class="btn btn-outline-secondary btn-sm">清空记录</button>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-striped table-hover align-middle mb-0">
                            <thead class="table-dark">
                                <tr>
                                    <th style="width:80px;">状态</th>
                                    <th>磁力链接</th>
                                    <th style="width:120px;">保存路径</th>
                                    <th style="width:160px;">时间</th>
                                </tr>
                            </thead>
                            <tbody id="records-body"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;
let qrPollTimer = null;
let qrData = null;

function apiUrl(action, params) {
    let url = `${basePath}/tool/api.php?action=${action}`;
    if (params) {
        url += '&' + new URLSearchParams(params).toString();
    }
    return url;
}

function apiPost(action, body) {
    return fetch(`${basePath}/tool/api.php?action=${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(r => r.json());
}

function apiGet(action, params) {
    return fetch(apiUrl(action, params)).then(r => r.json());
}

function updateLoginUI(status) {
    const badge = document.getElementById('login-badge');
    const loginBtn = document.getElementById('login-btn');
    const logoutBtn = document.getElementById('logout-btn');
    const userInfo = document.getElementById('login-user');

    if (status.logged_in) {
        badge.className = 'badge bg-success';
        badge.textContent = '已登录';
        loginBtn.style.display = 'none';
        logoutBtn.style.display = '';
        userInfo.textContent = '用户: ' + (status.user || '未知');
    } else {
        badge.className = 'badge bg-secondary';
        badge.textContent = '未登录';
        loginBtn.style.display = '';
        logoutBtn.style.display = 'none';
        userInfo.textContent = '';
    }
}

function checkLoginStatus() {
    apiGet('115_login_status').then(res => {
        updateLoginUI(res);
    }).catch(() => {});
}

function startQRLogin() {
    document.getElementById('login-btn').disabled = true;
    document.getElementById('qrcode-section').style.display = '';
    document.getElementById('login-actions').style.display = 'none';

    fetchQRCode();
}

function fetchQRCode() {
    document.getElementById('qrcode-status').textContent = '获取二维码中...';
    apiGet('115_login_qrcode').then(res => {
        if (!res.qrcode_base64) {
            document.getElementById('qrcode-status').textContent = '获取二维码失败';
            return;
        }
        qrData = res;
        document.getElementById('qrcode-img').src = 'data:image/png;base64,' + res.qrcode_base64;
        document.getElementById('qrcode-status').textContent = '请使用 115 App 扫码';
        if (qrPollTimer) clearInterval(qrPollTimer);
        qrPollTimer = setInterval(pollQRStatus, 2000);
    }).catch(() => {
        document.getElementById('qrcode-status').textContent = '获取二维码失败，请重试';
    });
}

function pollQRStatus() {
    if (!qrData) return;
    apiGet('115_login_qrcode_status', {
        uid: qrData.uid,
        time: qrData.time,
        sign: qrData.sign,
    }).then(res => {
        const statusEl = document.getElementById('qrcode-status');
        if (res.status === 2) {
            clearInterval(qrPollTimer);
            qrPollTimer = null;
            statusEl.textContent = '扫码成功，确认登录中...';
            apiPost('115_login_confirm', { uid: qrData.uid, app: 'alipaymini' }).then(confirmRes => {
                if (confirmRes.success) {
                    statusEl.textContent = '登录成功';
                    cancelQRLogin();
                    checkLoginStatus();
                } else {
                    statusEl.textContent = '登录失败: ' + (confirmRes.message || '');
                }
            });
        } else if (res.status === 1) {
            statusEl.textContent = '已扫描，请在手机上确认';
        } else if (res.status === -1) {
            clearInterval(qrPollTimer);
            qrPollTimer = null;
            statusEl.textContent = '二维码已过期，请刷新';
        } else {
            statusEl.textContent = '等待扫码...';
        }
    }).catch(() => {});
}

function cancelQRLogin() {
    if (qrPollTimer) {
        clearInterval(qrPollTimer);
        qrPollTimer = null;
    }
    document.getElementById('login-btn').disabled = false;
    document.getElementById('qrcode-section').style.display = 'none';
    document.getElementById('login-actions').style.display = '';
    qrData = null;
}

function loadRecords() {
    const tbody = document.getElementById('records-body');
    try {
        const records = JSON.parse(localStorage.getItem('115_submit_records') || '[]');
        if (records.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">暂无提交记录</td></tr>';
            return;
        }
        tbody.innerHTML = records.slice().reverse().map(r => {
            const statusClass = r.status === 'success' ? 'text-success' : r.status === 'exists' ? 'text-warning' : 'text-danger';
            const statusText = r.status === 'success' ? '已提交' : r.status === 'exists' ? '已存在' : '失败';
            const magnet = r.magnet || '';
            const shortMagnet = magnet.length > 60 ? magnet.slice(0, 60) + '...' : magnet;
            return `<tr>
                <td><span class="${statusClass} fw-semibold">${statusText}</span></td>
                <td style="word-break:break-all;" title="${magnet}">${shortMagnet}</td>
                <td>${r.dir || '-'}</td>
                <td class="text-nowrap">${r.time || ''}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">暂无提交记录</td></tr>';
    }
}

function addRecord(magnet, dir, status) {
    try {
        const records = JSON.parse(localStorage.getItem('115_submit_records') || '[]');
        records.push({
            magnet: magnet,
            dir: dir,
            status: status,
            time: new Date().toLocaleString(),
        });
        if (records.length > 200) records.splice(0, records.length - 200);
        localStorage.setItem('115_submit_records', JSON.stringify(records));
        loadRecords();
    } catch (e) {}
}

function showResult(msg, isError) {
    const el = document.getElementById('magnet-result');
    el.textContent = msg;
    el.className = 'mt-3 small' + (isError ? ' text-danger' : '');
}

document.getElementById('login-btn').addEventListener('click', startQRLogin);
document.getElementById('refresh-qrcode').addEventListener('click', fetchQRCode);
document.getElementById('cancel-login').addEventListener('click', cancelQRLogin);

document.getElementById('logout-btn').addEventListener('click', () => {
    if (!confirm('确定退出 115 登录？')) return;
    apiPost('115_logout', {}).then(res => {
        if (res.success) {
            checkLoginStatus();
        } else {
            alert('退出失败: ' + (res.message || ''));
        }
    }).catch(() => {
        alert('请求失败');
    });
});

document.getElementById('check-btn').addEventListener('click', () => {
    const magnet = document.getElementById('magnet-input').value.trim();
    if (!magnet) {
        showResult('请输入磁力链接', true);
        return;
    }
    showResult('检查中...', false);
    document.getElementById('check-btn').disabled = true;
    const dir = document.getElementById('save-path').value.trim();
    apiPost('115_check', { magnet, dir }).then(res => {
        if (res.exists) {
            let msg = '该磁链已存在于 115 网盘';
            if (res.in_offline_tasks) msg += '（离线任务列表中）';
            if (res.matched_files && res.matched_files.length > 0) {
                msg += '\n\n匹配文件:\n';
                res.matched_files.forEach(f => {
                    msg += '  - ' + f.name + (f.size ? ' (' + f.size + ')' : '') + '\n';
                });
            }
            msg += '\n置信度: ' + (res.confidence === 'high' ? '高' : res.confidence === 'low' ? '低' : '无');
            showResult(msg, false);
        } else {
            let msg = '未在 115 网盘找到该磁链';
            if (res.infohash_hex) msg += '\nInfoHash: ' + res.infohash_hex;
            if (res.dn) msg += '\n文件名: ' + res.dn;
            showResult(msg, false);
        }
    }).catch(err => {
        showResult('检查失败: ' + err.message, true);
    }).finally(() => {
        document.getElementById('check-btn').disabled = false;
    });
});

document.getElementById('submit-btn').addEventListener('click', () => {
    const magnet = document.getElementById('magnet-input').value.trim();
    if (!magnet) {
        showResult('请输入磁力链接', true);
        return;
    }
    showResult('提交中...', false);
    document.getElementById('submit-btn').disabled = true;
    const dir = document.getElementById('save-path').value.trim();
    apiPost('115_submit', { magnet, dir }).then(res => {
        if (res.success) {
            showResult('提交成功！\nPick Code: ' + (res.pick_code || '') + '\n文件将保存到: ' + (dir || '默认目录'), false);
            addRecord(magnet, dir, 'success');
        } else {
            showResult('提交失败: ' + (res.message || ''), true);
            addRecord(magnet, dir, 'failed');
        }
    }).catch(err => {
        showResult('提交失败: ' + err.message, true);
        addRecord(magnet, dir, 'failed');
    }).finally(() => {
        document.getElementById('submit-btn').disabled = false;
    });
});

document.getElementById('clear-records').addEventListener('click', () => {
    if (!confirm('确定清空所有提交记录？')) return;
    localStorage.removeItem('115_submit_records');
    loadRecords();
});

window.addEventListener('DOMContentLoaded', () => {
    checkLoginStatus();
    loadRecords();
});
</script>
</body>
</html>
