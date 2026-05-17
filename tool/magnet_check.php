<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>磁链校验</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
</head>
<body style="padding-top: 56px;">
<?php $base = rtrim(dirname(dirname($_SERVER['SCRIPT_NAME'])), '/'); ?>
<?php
function call_magnet_meta($magnet) {
    $root = realpath(__DIR__ . '/..');
    $python = $root . '/.venv/bin/python';
    if (!is_file($python)) $python = 'python3';

    $cmd = escapeshellcmd($python) . ' ' . escapeshellarg(__DIR__ . '/magnet_meta.py');
    $spec = [
        0 => ['pipe', 'r'],
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ];
    $proc = proc_open($cmd, $spec, $pipes, $root);
    if (!is_resource($proc)) {
        return ['ok' => false, 'errors' => ['proc_open_failed']];
    }

    $payload = json_encode(['magnet' => $magnet, 'timeout_sec' => 20], JSON_UNESCAPED_UNICODE);
    fwrite($pipes[0], $payload);
    fclose($pipes[0]);

    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $code = proc_close($proc);

    $decoded = json_decode($out, true);
    if (is_array($decoded)) return $decoded;
    return ['ok' => false, 'errors' => ['python_return_invalid'], 'stderr' => $err, 'exit_code' => $code];
}

$magnet = $_POST['magnet'] ?? '';
$name = $_POST['name'] ?? '';
$company = $_POST['company'] ?? '';
$result = null;
if ($magnet) {
    $result = call_magnet_meta($magnet);
}
?>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">磁链校验</a>
            <div class="navbar-nav">
                <a class="nav-link" href="<?= $base ?>/index.php">首页</a>
                <a class="nav-link" href="<?= $base ?>/tool/data.php">数据展示</a>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        <div class="card mb-3">
            <div class="card-header">当前记录</div>
            <div class="card-body">
                <div class="row g-2">
                    <div class="col-12 col-lg-6">
                        <div class="text-muted small">游戏</div>
                        <div class="fw-semibold"><?= htmlspecialchars($name ?: '-') ?></div>
                    </div>
                    <div class="col-12 col-lg-6">
                        <div class="text-muted small">公司</div>
                        <div class="fw-semibold"><?= htmlspecialchars($company ?: '-') ?></div>
                    </div>
                    <div class="col-12">
                        <div class="text-muted small">磁链</div>
                        <textarea class="form-control" rows="3" readonly><?= htmlspecialchars($magnet ?: '') ?></textarea>
                    </div>
                </div>
            </div>
        </div>

        <?php if (!$magnet): ?>
        <div class="alert alert-warning">未提供磁链，请从数据展示页点击“校验”进入。</div>
        <?php else: ?>
        <div class="card mb-3">
            <div class="card-header">解析结果</div>
            <div class="card-body">
                <?php if (!is_array($result)): ?>
                    <div class="alert alert-danger mb-0">解析失败</div>
                <?php else: ?>
                    <?php $errors = $result['errors'] ?? []; ?>
                    <?php if ($errors): ?>
                        <div class="alert alert-warning">校验提示：<?= htmlspecialchars(implode(', ', $errors)) ?></div>
                    <?php else: ?>
                        <div class="alert alert-success">磁链格式校验通过</div>
                    <?php endif; ?>

                    <div class="row g-2">
                        <div class="col-12 col-lg-6">
                            <div class="text-muted small">infohash(hex)</div>
                            <div class="font-monospace"><?= htmlspecialchars($result['infohash_hex'] ?? '-') ?></div>
                        </div>
                        <div class="col-12 col-lg-6">
                            <div class="text-muted small">dn</div>
                            <div><?= htmlspecialchars($result['dn'] ?? '-') ?></div>
                        </div>
                        <div class="col-12">
                            <div class="text-muted small">trackers</div>
                            <div class="small">
                                <?php foreach (($result['trackers'] ?? []) as $tr): ?>
                                    <div class="font-monospace"><?= htmlspecialchars($tr) ?></div>
                                <?php endforeach; ?>
                                <?php if (!($result['trackers'] ?? [])): ?>
                                    <div class="text-muted">-</div>
                                <?php endif; ?>
                            </div>
                        </div>
                    </div>
                <?php endif; ?>
            </div>
        </div>

        <div class="card">
            <div class="card-header">预计下载内容物（元数据）</div>
            <div class="card-body">
                <?php
                    $metaErrors = $result['metadata_errors'] ?? [];
                    $meta = $result['metadata'] ?? null;
                ?>
                <?php if (!$meta && $metaErrors): ?>
                    <div class="alert alert-warning">
                        获取元数据失败：<?= htmlspecialchars(implode(', ', $metaErrors)) ?>
                        <?php if (!empty($result['metadata_detail'])): ?>
                            <div class="mt-2 small font-monospace" style="white-space: pre-wrap;"><?= htmlspecialchars($result['metadata_detail']) ?></div>
                        <?php endif; ?>
                        <div class="mt-2">需要服务器安装 aria2c 且网络可获取 torrent 元数据。</div>
                    </div>
                <?php elseif (!$meta): ?>
                    <div class="alert alert-secondary mb-0">未获取到元数据。</div>
                <?php else: ?>
                    <div class="row g-2 mb-3">
                        <div class="col-12 col-lg-6">
                            <div class="text-muted small">torrent name</div>
                            <div class="fw-semibold"><?= htmlspecialchars($meta['name'] ?? '-') ?></div>
                        </div>
                        <div class="col-12 col-lg-6">
                            <div class="text-muted small">total size</div>
                            <div class="fw-semibold"><?= htmlspecialchars((string)($meta['total_size'] ?? 0)) ?> bytes</div>
                        </div>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-sm table-striped align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>路径</th>
                                    <th style="width: 160px;">大小(bytes)</th>
                                </tr>
                            </thead>
                            <tbody>
                                <?php foreach (($meta['files'] ?? []) as $f): ?>
                                    <tr>
                                        <td class="font-monospace"><?= htmlspecialchars($f['path'] ?? '') ?></td>
                                        <td class="font-monospace"><?= htmlspecialchars((string)($f['size'] ?? 0)) ?></td>
                                    </tr>
                                <?php endforeach; ?>
                            </tbody>
                        </table>
                    </div>
                <?php endif; ?>
            </div>
        </div>
        <?php endif; ?>
    </div>
</body>
</html>

