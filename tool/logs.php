<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>日志</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
</head>
<body style="padding-top: 56px;">
<?php
$base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/');

function load_tool_config() {
    $configPath = __DIR__ . '/config.json';
    $config = [];
    try {
        $txt = @file_get_contents($configPath);
        if ($txt !== false) $config = json_decode($txt, true) ?: [];
    } catch (Exception $e) {}
    return is_array($config) ? $config : [];
}

function logs_dir($config) {
    $logPath = $config['log_path'] ?? ('logs' . DIRECTORY_SEPARATOR . 'app.log');
    $logDir = $config['log_dir'] ?? dirname($logPath);
    $root = realpath(__DIR__ . '/..');
    $abs = $logDir;
    if (!preg_match('/^\\//', $logDir)) {
        $abs = $root . DIRECTORY_SEPARATOR . $logDir;
    }
    return $abs;
}

function ends_with($s, $suffix) {
    $len = strlen($suffix);
    if ($len === 0) return true;
    return substr($s, -$len) === $suffix;
}

function cleanup_logs($dir, $days) {
    if (!is_dir($dir)) return 0;
    $cutoff = time() - $days * 86400;
    $removed = 0;
    foreach (scandir($dir) as $name) {
        if ($name === '.' || $name === '..') continue;
        if (!ends_with($name, '.log')) continue;
        $p = $dir . DIRECTORY_SEPARATOR . $name;
        if (!is_file($p)) continue;
        $mt = @filemtime($p);
        if ($mt !== false && $mt < $cutoff) {
            if (@unlink($p)) $removed += 1;
        }
    }
    return $removed;
}

function tail_lines($filePath, $maxLines = 400, $maxBytes = 400000) {
    if (!is_file($filePath)) return '';
    $size = filesize($filePath);
    $fp = fopen($filePath, 'rb');
    if (!$fp) return '';
    $seek = 0;
    if ($size > $maxBytes) $seek = $size - $maxBytes;
    fseek($fp, $seek);
    $data = stream_get_contents($fp);
    fclose($fp);
    if ($data === false) return '';
    $data = str_replace("\r\n", "\n", $data);
    $lines = explode("\n", $data);
    if (count($lines) > $maxLines) {
        $lines = array_slice($lines, -$maxLines);
    }
    return implode("\n", $lines);
}

$config = load_tool_config();
$dir = logs_dir($config);
$days = intval($config['log_retention_days'] ?? 14);
if ($days <= 0) $days = 14;
$auto = ($config['log_auto_cleanup'] ?? true) ? true : false;
$removed = 0;
if ($auto) {
    $removed = cleanup_logs($dir, $days);
}

$files = [];
if (is_dir($dir)) {
    foreach (scandir($dir) as $name) {
        if ($name === '.' || $name === '..') continue;
        if (!ends_with($name, '.log')) continue;
        $p = $dir . DIRECTORY_SEPARATOR . $name;
        if (!is_file($p)) continue;
        $files[] = $name;
    }
}
sort($files);
usort($files, function($a, $b) use ($dir) {
    $pa = $dir . DIRECTORY_SEPARATOR . $a;
    $pb = $dir . DIRECTORY_SEPARATOR . $b;
    return (@filemtime($pb) ?: 0) <=> (@filemtime($pa) ?: 0);
});

$selected = $_GET['file'] ?? '';
if (!$selected && count($files) > 0) $selected = $files[0];
$safe = basename($selected);
$selectedPath = $dir . DIRECTORY_SEPARATOR . $safe;
$content = $safe ? tail_lines($selectedPath, 600, 600000) : '';
?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">日志</a>
            <div class="navbar-nav">
                <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
                <a class="nav-link" href="<?= $base ?>/tool/data.php">数据展示</a>
                <a class="nav-link" href="<?= $base ?>/calendar.php">年历</a>
                <a class="nav-link active" href="<?= $base ?>/tool/logs.php">日志</a>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        <div class="card mb-3">
            <div class="card-body">
                <div class="row g-2 align-items-end">
                    <div class="col-12 col-lg-8">
                        <label class="form-label mb-1">选择日志文件</label>
                        <form method="GET" class="d-flex gap-2">
                            <select name="file" class="form-select">
                                <?php foreach ($files as $f): ?>
                                    <option value="<?= htmlspecialchars($f) ?>" <?= $f === $safe ? 'selected' : '' ?>>
                                        <?= htmlspecialchars($f) ?>
                                    </option>
                                <?php endforeach; ?>
                            </select>
                            <button class="btn btn-primary" type="submit">查看</button>
                            <a class="btn btn-outline-secondary" href="<?= $base ?>/tool/logs.php?file=<?= urlencode($safe) ?>">刷新</a>
                        </form>
                    </div>
                    <div class="col-12 col-lg-4 text-lg-end">
                        <div class="small text-muted">
                            自动清理：超过 <?= intval($days) ?> 天的 .log 已清理 <?= intval($removed) ?> 个
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header fw-semibold"><?= htmlspecialchars($safe ?: '日志内容') ?></div>
            <div class="card-body">
                <pre class="mb-0" style="white-space:pre-wrap;word-break:break-all;max-height:70vh;overflow:auto;"><?= htmlspecialchars($content) ?></pre>
            </div>
        </div>
    </div>
</body>
</html>
