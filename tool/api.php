<?php

function json_response($data) {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function read_json_body() {
    $raw = file_get_contents('php://input');
    if (!$raw) return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function as_int($value, $default = null) {
    if ($value === null || $value === '') return $default;
    if (!is_numeric($value)) return $default;
    return intval($value);
}

function run_cli($args) {
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

    $spec = [
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ];

    $env = array_merge($_SERVER, [
        'HOME' => $root,
    ]);
    if (strtoupper(substr(PHP_OS, 0, 3)) === 'WIN') {
        $env['USERPROFILE'] = $root;
        if (preg_match('/^[A-Za-z]:/', $root)) {
            $env['HOMEDRIVE'] = substr($root, 0, 2);
            $env['HOMEPATH'] = substr($root, 2);
        }
    }

    $proc = proc_open($cmd, $spec, $pipes, $root, $env);
    if (!is_resource($proc)) {
        return [1, ['status' => 'error', 'message' => '无法启动Python进程']];
    }
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $code = proc_close($proc);

    $decoded = json_decode($out, true);
    if (is_array($decoded)) return [$code, $decoded];

    return [$code, ['status' => 'error', 'message' => 'Python返回非JSON', 'stderr' => $err]];
}

$action = isset($_GET['action']) ? $_GET['action'] : '';

if ($action === 'years') {
    [$code, $data] = run_cli(['years']);
    json_response($data);
}

if ($action === 'latest_month') {
    [$code, $data] = run_cli(['latest_month']);
    json_response($data);
}

if ($action === 'games') {
    $page = as_int($_GET['page'] ?? 1, 1);
    $year = as_int($_GET['year'] ?? null, null);
    $month = as_int($_GET['month'] ?? null, null);

    $args = ['games', '--page', strval(max(1, $page))];
    if ($year !== null) $args[] = '--year';
    if ($year !== null) $args[] = strval($year);
    if ($month !== null) $args[] = '--month';
    if ($month !== null) $args[] = strval($month);

    [$code, $data] = run_cli($args);
    json_response($data);
}

if ($action === 'get_status') {
    [$code, $data] = run_cli(['spider', 'status']);
    json_response($data);
}

if ($action === 'start_spider') {
    $body = read_json_body();
    $startYear = as_int($body['start_year'] ?? null, null);
    $endYear = as_int($body['end_year'] ?? null, null);
    if ($startYear === null || $endYear === null) {
        json_response(['status' => 'error', 'message' => '参数错误']);
    }
    [$code, $data] = run_cli(['spider', 'start', '--start-year', strval($startYear), '--end-year', strval($endYear)]);
    json_response($data);
}

if ($action === 'stop_spider') {
    [$code, $data] = run_cli(['spider', 'stop']);
    json_response($data);
}

if ($action === 'start_download') {
    $body = read_json_body();
    $year = as_int($body['year'] ?? null, null);
    $month = as_int($body['month'] ?? 0, 0);
    if ($year === null) {
        json_response(['status' => 'error', 'message' => '参数错误']);
    }
    [$code, $data] = run_cli(['download', 'start', '--year', strval($year), '--month', strval($month)]);
    json_response($data);
}

if ($action === 'download_status') {
    [$code, $data] = run_cli(['download', 'status']);
    json_response($data);
}

if ($action === 'stop_download') {
    [$code, $data] = run_cli(['download', 'stop']);
    json_response($data);
}

if ($action === '115_login_qrcode') {
    [$code, $data] = run_cli(['115', 'login_qrcode']);
    json_response($data);
}

if ($action === '115_login_qrcode_status') {
    $uid = $_GET['uid'] ?? '';
    $time = $_GET['time'] ?? '';
    $sign = $_GET['sign'] ?? '';
    [$code, $data] = run_cli(['115', 'login_qrcode_status', '--uid', $uid, '--time', $time, '--sign', $sign]);
    json_response($data);
}

if ($action === '115_login_confirm') {
    $body = read_json_body();
    $uid = $body['uid'] ?? '';
    $app = $body['app'] ?? 'alipaymini';
    [$code, $data] = run_cli(['115', 'login_confirm', '--uid', $uid, '--app', $app]);
    json_response($data);
}

if ($action === '115_logout') {
    [$code, $data] = run_cli(['115', 'logout']);
    json_response($data);
}

if ($action === '115_login_status') {
    [$code, $data] = run_cli(['115', 'login_status']);
    json_response($data);
}

if ($action === '115_check') {
    $body = read_json_body();
    $magnet = $body['magnet'] ?? '';
    $dir = $body['dir'] ?? '';
    [$code, $data] = run_cli(['115', 'check', '--magnet', $magnet, '--dir', $dir]);
    json_response($data);
}

if ($action === '115_submit') {
    $body = read_json_body();
    $magnet = $body['magnet'] ?? '';
    $dir = $body['dir'] ?? '';
    [$code, $data] = run_cli(['115', 'submit', '--magnet', $magnet, '--dir', $dir]);
    json_response($data);
}

if ($action === '115_check_all_start') {
    $body = read_json_body();
    $year = $body['year'] ?? '';
    $month = $body['month'] ?? '';
    $args = ['115', 'check_all', 'start'];
    if ($year) { $args[] = '--year'; $args[] = strval($year); }
    if ($month) { $args[] = '--month'; $args[] = strval($month); }
    [$code, $data] = run_cli($args);
    json_response($data);
}

if ($action === '115_check_all_status') {
    [$code, $data] = run_cli(['115', 'check_all', 'status']);
    json_response($data);
}

if ($action === '115_check_all_stop') {
    [$code, $data] = run_cli(['115', 'check_all', 'stop']);
    json_response($data);
}

if ($action === 'update_game') {
    $body = read_json_body();
    $date = $body['date'] ?? '';
    $old_name = $body['old_name'] ?? '';
    $new_date = $body['new_date'] ?? '';
    $new_name = $body['new_name'] ?? '';
    $new_company = $body['new_company'] ?? '';
    $new_link = $body['new_link'] ?? null;
    $new_downloaded = $body['new_downloaded'] ?? null;
    $new_nyaa_name = $body['new_nyaa_name'] ?? null;
    if (!$date || !$old_name) {
        json_response(['success' => false, 'message' => '缺少必填字段 date/old_name']);
    }
    $args = ['update_game', '--date', $date, '--old-name', $old_name];
    if ($new_date) { $args[] = '--new-date'; $args[] = $new_date; }
    if ($new_name) { $args[] = '--new-name'; $args[] = $new_name; }
    if ($new_company) { $args[] = '--new-company'; $args[] = $new_company; }
    if ($new_link !== null) { $args[] = '--new-link'; $args[] = $new_link; }
    if ($new_downloaded !== null) { $args[] = '--new-downloaded'; $args[] = strval($new_downloaded); }
    if ($new_nyaa_name !== null) { $args[] = '--new-nyaa-name'; $args[] = $new_nyaa_name; }
    [$code, $data] = run_cli($args);
    json_response($data);
}

if ($action === 'delete_game') {
    $body = read_json_body();
    $date = $body['date'] ?? '';
    $name = $body['name'] ?? '';
    if (!$date || !$name) {
        json_response(['success' => false, 'message' => '缺少必填字段 date/name']);
    }
    [$code, $data] = run_cli(['delete_game', '--date', $date, '--name', $name]);
    json_response($data);
}

json_response(['status' => 'error', 'message' => 'unknown action']);
