<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>日志 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .log-file-form { display: flex; align-items: center; gap: .5rem; }
        .log-file-form .form-select { flex: 1 1 0; min-width: 0; width: auto; }
        .log-file-form .btn { flex: 0 0 auto; white-space: nowrap; }
    </style>
</head>
<body>
<?php

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

function list_log_files($dir) {
    $files = [];
    if (!is_dir($dir)) return $files;
    foreach (scandir($dir) as $name) {
        if ($name === '.' || $name === '..') continue;
        if (!ends_with($name, '.log')) continue;
        $p = $dir . DIRECTORY_SEPARATOR . $name;
        if (!is_file($p)) continue;
        $files[] = $name;
    }
    usort($files, function($a, $b) use ($dir) {
        $pa = $dir . DIRECTORY_SEPARATOR . $a;
        $pb = $dir . DIRECTORY_SEPARATOR . $b;
        return (@filemtime($pb) ?: 0) <=> (@filemtime($pa) ?: 0);
    });
    return $files;
}

function pick_logs_dir($primary) {
    $root = realpath(__DIR__ . '/..');
    $candidates = [$primary];
    if ($root) $candidates[] = $root . DIRECTORY_SEPARATOR . 'logs';
    $candidates[] = __DIR__ . DIRECTORY_SEPARATOR . 'logs';

    $uniq = [];
    foreach ($candidates as $d) {
        if (!$d) continue;
        $key = rtrim($d, DIRECTORY_SEPARATOR);
        if (!$key) continue;
        $uniq[$key] = $key;
    }

    $bestDir = $primary;
    $bestCount = -1;
    foreach ($uniq as $d) {
        $count = count(list_log_files($d));
        if ($count > $bestCount) {
            $bestCount = $count;
            $bestDir = $d;
        }
    }
    return $bestDir;
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
$dir = pick_logs_dir(logs_dir($config));
$days = intval($config['log_retention_days'] ?? 14);
if ($days <= 0) $days = 14;
$auto = ($config['log_auto_cleanup'] ?? true) ? true : false;
$removed = 0;
if ($auto) {
    $removed = cleanup_logs($dir, $days);
}

$files = list_log_files($dir);

$selected = $_GET['file'] ?? '';
if (!$selected && count($files) > 0) $selected = $files[0];
$safe = basename($selected);
$selectedPath = $dir . DIRECTORY_SEPARATOR . $safe;
$content = $safe ? tail_lines($selectedPath, 600, 600000) : '';
?>
<?php require dirname(__DIR__) . '/includes/header.php'; ?>

    <div class="container mt-4">
        <div class="card mb-3">
            <div class="card-body">
                <div class="row g-2 align-items-end">
                    <div class="col-12 col-lg-8">
                        <label for="log-file" class="form-label mb-1">选择日志文件</label>
                        <form method="GET" class="log-file-form">
                            <select id="log-file" name="file" class="form-select">
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
                            日志目录：<?= htmlspecialchars($dir) ?><br>
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
