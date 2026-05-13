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
    $cli = $root . '/cli.py';
    $python = getenv('PYTHON_BIN');
    if (!$python) $python = 'python3';

    $cmd = escapeshellcmd($python) . ' ' . escapeshellarg($cli);
    foreach ($args as $a) {
        $cmd .= ' ' . escapeshellarg($a);
    }

    $spec = [
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ];
    $proc = proc_open($cmd, $spec, $pipes, $root);
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

json_response(['status' => 'error', 'message' => 'unknown action']);

