<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>115 校对调试</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        pre {
            white-space: pre-wrap;
            word-break: break-all;
            margin: 0;
        }
    </style>
</head>
<body style="padding-top: 56px;">
<?php $base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/'); ?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">115 校对调试</a>
            <div class="navbar-nav">
                <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
                <a class="nav-link" href="<?= $base ?>/tool/data.php">数据展示</a>
                <a class="nav-link active" href="<?= $base ?>/tool/check_debug.php">校对调试</a>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        <div class="card mb-3">
            <div class="card-header fw-semibold">参数</div>
            <div class="card-body">
                <div class="mb-2">
                    <label class="form-label mb-1">磁力链接</label>
                    <textarea id="dbg-magnet" class="form-control" rows="3"><?= htmlspecialchars($_GET['magnet'] ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></textarea>
                </div>
                <div class="mb-3">
                    <label class="form-label mb-1">115 保存路径</label>
                    <input id="dbg-dir" class="form-control" value="<?= htmlspecialchars($_GET['dir'] ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>">
                </div>
                <div class="d-flex gap-2">
                    <button id="dbg-run" class="btn btn-primary">执行校对（debug）</button>
                    <a class="btn btn-outline-secondary" href="<?= $base ?>/tool/data.php">返回数据页</a>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header d-flex align-items-center justify-content-between">
                <span class="fw-semibold">结果</span>
                <span id="dbg-badge" class="badge bg-secondary">未执行</span>
            </div>
            <div class="card-body">
                <pre id="dbg-out" class="small text-muted">点击“执行校对（debug）”开始</pre>
            </div>
        </div>
    </div>

    <script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>
    <script>
const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;

function setBadge(ok, text) {
    const badge = document.getElementById('dbg-badge');
    badge.textContent = text;
    badge.className = ok ? 'badge bg-success' : 'badge bg-secondary';
}

function pretty(obj) {
    try { return JSON.stringify(obj, null, 2); } catch (e) { return String(obj); }
}

document.getElementById('dbg-run').addEventListener('click', () => {
    const magnet = document.getElementById('dbg-magnet').value.trim();
    const dir = document.getElementById('dbg-dir').value.trim();
    if (!magnet) return;
    const out = document.getElementById('dbg-out');
    out.textContent = '执行中...';
    setBadge(false, '执行中');

    fetch(`${basePath}/tool/api.php?action=115_check`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ magnet, dir, debug: true }),
    }).then(r => r.json()).then(res => {
        out.textContent = pretty(res);
        setBadge(!!res.exists, res.exists ? '已命中' : '未命中');
    }).catch(err => {
        out.textContent = '失败: ' + err.message;
        setBadge(false, '失败');
    });
});
    </script>
</body>
</html>

