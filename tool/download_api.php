<?php
/**
 * 整理115 API（独立于api.php，避免并行开发冲突）
 *
 * actions:
 *   organize_preview GET/POST ?year=&month=&name=  -> 115 organize ... --dry-run
 *   organize_run     POST      ?year=&month=&name=  -> 115 organize ...
 */

function org_json_response($data) {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function org_run_cli($args) {
    $root = realpath(__DIR__ . '/..');
    $cli = __DIR__ . '/cli.py';

    $python = getenv('PYTHON_BIN');
    if (!$python) {
        $venv_python = $root . '/.venv/bin/python';
        $python = is_file($venv_python) ? $venv_python : 'python3';
    }

    $cmd = escapeshellcmd($python) . ' ' . escapeshellarg($cli);
    foreach ($args as $a) {
        $cmd .= ' ' . escapeshellarg($a);
    }

    $spec = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $env = array_merge($_SERVER, ['HOME' => $root]);

    $proc = proc_open($cmd, $spec, $pipes, $root, $env);
    if (!is_resource($proc)) {
        return ['status' => 'error', 'message' => '无法启动Python进程'];
    }
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    proc_close($proc);

    $decoded = json_decode($out, true);
    if (is_array($decoded)) return $decoded;
    return ['status' => 'error', 'message' => 'Python返回非JSON', 'stderr' => $err];
}

$action = isset($_GET['action']) ? $_GET['action'] : '';

if ($action === 'organize_preview' || $action === 'organize_run') {
    $year = isset($_REQUEST['year']) && preg_match('/^\d{4}$/', $_REQUEST['year']) ? $_REQUEST['year'] : null;
    $month = isset($_REQUEST['month']) && preg_match('/^\d{1,2}$/', $_REQUEST['month']) ? $_REQUEST['month'] : null;
    $name = isset($_REQUEST['name']) ? trim($_REQUEST['name']) : null;

    if ($year === null) {
        org_json_response(['status' => 'error', 'message' => '缺少year参数']);
    }

    $args = ['115', 'organize', '--year', $year];
    if ($month !== null) $args[] = '--month';
    if ($month !== null) $args[] = $month;
    if ($name !== null && $name !== '') $args[] = '--name';
    if ($name !== null && $name !== '') $args[] = $name;
    if ($action === 'organize_preview') $args[] = '--dry-run';

    org_json_response(org_run_cli($args));
}

org_json_response(['status' => 'error', 'message' => '未知action: ' . $action]);
