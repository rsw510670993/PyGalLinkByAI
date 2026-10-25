<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>维护 · pyGal</title>
    <link href="../assets/vendor/bootstrap/bootstrap.min.css" rel="stylesheet">
    <style>
        .mono { font-variant-numeric: tabular-nums; }
        .oldest-names { max-height: 7rem; overflow: auto; }
    </style>
</head>
<body class="bg-light">
<?php require dirname(__DIR__) . '/includes/header.php'; ?>
<?php
$root = dirname(__DIR__);
$historyPath = $root . '/status/maintenance_history.jsonl';

function h($value) { return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8'); }

function fmt_bytes($n) {
    $n = (float)$n;
    foreach (['B', 'KB', 'MB', 'GB'] as $unit) {
        if ($n < 1024 || $unit === 'GB') {
            return $unit === 'B' ? sprintf('%d B', $n) : sprintf('%.1f %s', $n, $unit);
        }
        $n /= 1024;
    }
    return sprintf('%.1f GB', $n);
}

$history = [];
if (is_file($historyPath)) {
    $fp = @fopen($historyPath, 'rb');
    if ($fp) {
        while (($line = fgets($fp)) !== false) {
            $entry = json_decode($line, true);
            if (is_array($entry)) $history[] = $entry;
        }
        fclose($fp);
    }
}
$history = array_slice(array_reverse($history), 0, 20);

$logDir = $root . '/logs';
$logs = [];
if (is_dir($logDir)) {
    foreach (scandir($logDir) as $name) {
        if (substr($name, -4) === '.log' && is_file($logDir . '/' . $name)) {
            $logs[$name] = @filemtime($logDir . '/' . $name) ?: 0;
        }
    }
}
arsort($logs);
$latestLog = $logs ? array_key_first($logs) : '';
$latestContent = '';
if ($latestLog) {
    $data = @file_get_contents($logDir . '/' . $latestLog);
    if ($data !== false) {
        $lines = explode("\n", str_replace("\r\n", "\n", $data));
        $latestContent = implode("\n", array_slice($lines, -120));
    }
}
?>
<main class="container py-4">
    <div class="mb-3">
        <h1 class="h3 mb-1">维护 · 清理履历</h1>
        <div class="text-muted small">
            清理任务总线留下的记录与 <code>db_backups/</code> 里的旧快照；默认只预览，删数据库快照是唯一回滚凭据，执行前请确认。
        </div>
    </div>

    <div id="page-alert" class="alert alert-secondary py-2 small">正在读取当前占用…</div>

    <div class="card mb-3">
        <div class="card-header">清理策略</div>
        <div class="card-body">
            <div class="row g-3 align-items-end">
                <div class="col-6 col-xl-2">
                    <label for="keep" class="form-label">任务记录保留个数</label>
                    <input id="keep" type="number" min="0" class="form-control" value="50">
                </div>
                <div class="col-6 col-xl-2">
                    <label for="days" class="form-label">任务记录保留天数</label>
                    <input id="days" type="number" min="0" class="form-control" value="14">
                </div>
                <div class="col-6 col-xl-2">
                    <label for="db-keep" class="form-label">db 快照保留个数</label>
                    <input id="db-keep" type="number" min="0" class="form-control" value="3">
                </div>
                <div class="col-6 col-xl-2">
                    <label for="db-days" class="form-label">db 快照保留天数</label>
                    <input id="db-days" type="number" min="0" class="form-control" value="7">
                </div>
                <div class="col-12 col-xl-4 d-flex gap-2">
                    <button id="preview-btn" class="btn btn-outline-primary" type="button">预览清理</button>
                    <button id="execute-btn" class="btn btn-outline-danger" type="button">执行清理</button>
                </div>
            </div>
            <div class="small text-muted mt-3">
                任务记录只清 <code>status/agent_jobs/outbox/</code>（inbox / working 永不删）；db 快照指
                <code>db_backups/egs.before_*.db</code> 与 <code>egs.db.pre-*</code>；审计缓存
                <code>status/gal_old_audit_*.json</code> 与其他清单 JSON 不在清理范围。
            </div>
        </div>
    </div>

    <div class="card mb-3">
        <div class="card-header">当前占用</div>
        <div id="inventory" class="card-body small text-muted">读取中…</div>
    </div>

    <div id="preview-card" class="card mb-3" hidden>
        <div class="card-header">本次预览</div>
        <div id="preview-body" class="card-body small"></div>
    </div>

    <div class="card mb-3">
        <div class="card-header d-flex justify-content-between align-items-center">
            <span>清理执行履历</span>
            <span class="small text-muted">最近 <?= count($history) ?> 次真删</span>
        </div>
        <div class="card-body p-0">
            <?php if (!$history): ?>
                <div class="text-muted small p-3">还没有执行过清理。</div>
            <?php else: ?>
                <table class="table table-sm mb-0 align-middle">
                    <thead>
                    <tr><th>时间</th><th>删除</th><th>释放</th><th>任务记录</th><th>db 快照</th><th>备注</th></tr>
                    </thead>
                    <tbody>
                    <?php foreach ($history as $entry): ?>
                        <?php
                        $spool = is_array($entry['spool'] ?? null) ? $entry['spool'] : [];
                        $db = is_array($entry['db_backups'] ?? null) ? $entry['db_backups'] : [];
                        $errors = is_array($entry['errors'] ?? null) ? $entry['errors'] : [];
                        ?>
                        <tr>
                            <td class="mono"><?= h($entry['time'] ?? '-') ?></td>
                            <td class="mono"><?= h($entry['deleted'] ?? 0) ?></td>
                            <td class="mono"><?= h(fmt_bytes($entry['freed_bytes'] ?? 0)) ?></td>
                            <td class="mono"><?= h($spool['delete'] ?? 0) ?> / 共 <?= h($spool['total'] ?? 0) ?></td>
                            <td class="mono"><?= h($db['delete'] ?? 0) ?> / 共 <?= h($db['total'] ?? 0) ?></td>
                            <td class="small <?= $errors ? 'text-danger' : 'text-muted' ?>">
                                <?= $errors ? h(implode('；', array_slice($errors, 0, 2))) : 'ok' ?>
                            </td>
                        </tr>
                    <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </div>
    </div>

    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <span>最近日志<?= $latestLog ? '（' . h($latestLog) . '）' : '' ?></span>
            <a class="small" href="<?= h($base) ?>/tool/logs.php">刷新</a>
        </div>
        <div class="card-body">
            <pre class="mb-0 small" style="white-space:pre-wrap;word-break:break-all;max-height:40vh;overflow:auto;"><?= h($latestContent ?: '（暂无日志）') ?></pre>
        </div>
    </div>
</main>

<script>
(() => {
    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','>':'&gt;','<':'&lt;','"':'&quot;',"'":'&#39;'}[ch]));
    const bytes = n => {
        n = Number(n) || 0;
        for (const unit of ['B', 'KB', 'MB', 'GB']) {
            if (n < 1024 || unit === 'GB') return unit === 'B' ? `${Math.round(n)} B` : `${n.toFixed(1)} ${unit}`;
            n /= 1024;
        }
        return `${n.toFixed(1)} GB`;
    };

    function params(execute) {
        return {
            execute: !!execute,
            keep: parseInt($('keep').value, 10) || 0,
            days: parseInt($('days').value, 10) || 0,
            db_keep: parseInt($('db-keep').value, 10) || 0,
            db_days: parseInt($('db-days').value, 10) || 0
        };
    }

    async function prune(execute) {
        const response = await fetch('api.php?action=agent_job_prune', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(params(execute))
        });
        const data = await response.json();
        if (!response.ok || data.status === 'error') throw new Error(data.message || `请求失败 (${response.status})`);
        return data;
    }

    function renderInventory(data) {
        const inv = data.inventory || {};
        $('inventory').innerHTML = `
            <div class="row g-2">
                <div class="col-6 col-xl-3">任务记录：<strong class="mono">${esc(data.spool?.total ?? 0)}</strong> 个 · ${esc(bytes(data.spool?.total_bytes ?? 0))}</div>
                <div class="col-6 col-xl-3">db 快照：<strong class="mono">${esc(inv.db_count ?? 0)}</strong> 个 · ${esc(bytes(inv.db_bytes ?? 0))}</div>
                <div class="col-6 col-xl-3">审计缓存：<strong class="mono">${esc(inv.audit_cache_count ?? 0)}</strong> 个 · ${esc(bytes(inv.audit_cache_bytes ?? 0))}</div>
                <div class="col-6 col-xl-3">日志：<strong class="mono">${esc(inv.log_count ?? 0)}</strong> 个 · ${esc(bytes(inv.log_bytes ?? 0))}</div>
            </div>
            <div class="text-muted mt-2">db 快照目录：<code>${esc(inv.db_dir ?? '')}</code></div>`;
    }

    function renderPreview(data) {
        const list = items => (items || []).map(n => `<li><code>${esc(n)}</code></li>`).join('') || '<li class="text-muted">（无）</li>';
        $('preview-card').hidden = false;
        $('preview-body').innerHTML = `
            <ul class="mb-2">${(data.highlights || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>
            <div class="row g-3">
                <div class="col-12 col-xl-6">
                    <div class="fw-semibold">将删的任务记录（最老 5 个）</div>
                    <ul class="oldest-names mono mb-0">${list(data.spool?.oldest_to_delete)}</ul>
                </div>
                <div class="col-12 col-xl-6">
                    <div class="fw-semibold">将删的 db 快照（最老 5 个）</div>
                    <ul class="oldest-names mono mb-0">${list(data.db_backups?.oldest_to_delete)}</ul>
                </div>
            </div>`;
    }

    // 执行结果要在 reload 之后还能看到：存进 sessionStorage，初始化时优先展示，
    // 否则会被页面加载时那次自动预览的提示覆盖。
    const RESULT_KEY = 'pygal.cleanupResult';

    function takeStashed(key) {
        const value = sessionStorage.getItem(key);
        if (value !== null) sessionStorage.removeItem(key);
        return value;
    }

    async function refresh() {
        $('page-alert').className = 'alert alert-primary py-2 small';
        $('page-alert').textContent = '正在读取当前占用…';
        try {
            const data = await prune(false);
            renderInventory(data);
            renderPreview(data);
            const done = takeStashed(RESULT_KEY);
            if (done) {
                $('page-alert').className = 'alert alert-success py-2 small';
                $('page-alert').textContent = done;
            } else {
                $('page-alert').className = 'alert alert-secondary py-2 small';
                $('page-alert').textContent = '预览完成，未删除任何文件。';
            }
        } catch (error) {
            $('page-alert').className = 'alert alert-danger py-2 small';
            $('page-alert').textContent = error.message;
        }
    }

    $('preview-btn').addEventListener('click', refresh);
    $('execute-btn').addEventListener('click', async () => {
        const p = params(true);
        if (!confirm(`确认执行清理？\n任务记录保留最新 ${p.keep} 个 + 最近 ${p.days} 天；\ndb 快照保留最新 ${p.db_keep} 个 + 最近 ${p.db_days} 天。\ndb 快照是唯一回滚凭据，删除后不可恢复。`)) return;
        $('page-alert').className = 'alert alert-warning py-2 small';
        $('page-alert').textContent = '正在清理…';
        try {
            const data = await prune(true);
            const summary = `已删除 ${data.deleted} 个文件，释放 ${bytes(data.freed_bytes)}。`;
            sessionStorage.setItem(RESULT_KEY, summary);
            $('page-alert').className = 'alert alert-success py-2 small';
            $('page-alert').textContent = summary;
            setTimeout(() => location.reload(), 1000);
        } catch (error) {
            $('page-alert').className = 'alert alert-danger py-2 small';
            $('page-alert').textContent = error.message;
        }
    });

    refresh();
})();
</script>
</body>
</html>
