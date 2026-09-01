<?php
/**
 * 审核面板 API（独立文件，不碰其他api）
 *
 * actions:
 *   overview       GET  -> 各类待审数量统计
 *   editions       GET  ?year= -> 待审版次/特典建议（dedup_log edition_of，过滤已删/已决定）
 *   review115      GET  -> 115整理待审清单（status/review_115.json，过滤已决定）
 *   dnmismatch     GET  -> dn_mismatch待审行（有链无release_ts）
 *   edition_merge  POST ?year=&month=&canonical=&member= -> 人工确认合并
 *   decide         POST ?kind=&date=&name=&decision= -> 记录审核决定
 *   setts          POST ?date=&name=&ts= -> 手动设定release_ts
 */

function rv_json_response($data) {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function rv_run_cli($args) {
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
    fclose($pipes[1]); fclose($pipes[2]);
    proc_close($proc);
    $decoded = json_decode($out, true);
    if (is_array($decoded)) return $decoded;
    return ['status' => 'error', 'message' => 'Python返回非JSON', 'stderr' => $err];
}

function rv_decisions() {
    $path = __DIR__ . '/../status/review_decisions.json';
    if (!is_file($path)) return [];
    $data = json_decode(file_get_contents($path), true);
    return isset($data['items']) && is_array($data['items']) ? $data['items'] : [];
}

$action = isset($_GET['action']) ? $_GET['action'] : '';

// ---------- overview ----------
if ($action === 'overview') {
    rv_json_response(rv_run_cli(['dedup', 'overview']));
}

// ---------- editions ----------
if ($action === 'editions') {
    $year = isset($_GET['year']) && preg_match('/^\d{4}$/', $_GET['year']) ? $_GET['year'] : null;
    $res = rv_run_cli(['dedup', 'editions']);
    if (($res['status'] ?? '') !== 'ok') rv_json_response($res);
    $items = $res['items'] ?? [];
    if ($year) {
        $items = array_values(array_filter($items, function ($i) use ($year) {
            return strpos((string)$i['date'], $year) === 0;
        }));
    }
    rv_json_response(['status' => 'ok', 'total' => count($items), 'items' => $items]);
}

// ---------- review115 ----------
if ($action === 'review115') {
    rv_json_response(rv_run_cli(['115', 'review', '--todo']));
}

// ---------- dnmismatch ----------
if ($action === 'dnmismatch') {
    rv_json_response(rv_run_cli(['dedup', 'dnmismatch']));
}

// ---------- edition_merge ----------
if ($action === 'edition_merge') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') rv_json_response(['status' => 'error', 'message' => '仅POST']);
    $year = isset($_REQUEST['year']) ? $_REQUEST['year'] : '';
    $month = isset($_REQUEST['month']) ? $_REQUEST['month'] : '';
    $canonical = isset($_REQUEST['canonical']) ? trim($_REQUEST['canonical']) : '';
    $member = isset($_REQUEST['member']) ? trim($_REQUEST['member']) : '';
    if (!preg_match('/^\d{4}$/', $year) || !preg_match('/^\d{1,2}$/', $month)
        || $canonical === '' || $member === '') {
        rv_json_response(['status' => 'error', 'message' => '参数不完整']);
    }
    $res = rv_run_cli(['dedup', 'merge', '--year', $year, '--month', $month,
                       '--canonical', $canonical, '--member', $member,
                       '--reason', '审核UI人工确认合并']);
    rv_json_response($res);
}

// ---------- decide ----------
if ($action === 'decide') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') rv_json_response(['status' => 'error', 'message' => '仅POST']);
    $kind = isset($_REQUEST['kind']) ? $_REQUEST['kind'] : '';
    $date = isset($_REQUEST['date']) ? $_REQUEST['date'] : '';
    $name = isset($_REQUEST['name']) ? $_REQUEST['name'] : '';
    $decision = isset($_REQUEST['decision']) ? $_REQUEST['decision'] : '';
    $note = isset($_REQUEST['note']) ? $_REQUEST['note'] : null;
    if (!in_array($kind, ['edition', 'review115'], true) || $date === '' || $name === '' || $decision === '') {
        rv_json_response(['status' => 'error', 'message' => '参数不完整']);
    }
    if ($kind === 'review115') {
        $res = rv_run_cli(['115', 'resolve', '--date', $date, '--name', $name,
                           '--decision', $decision, '--note', (string)$note]);
    } else {
        $res = rv_run_cli(['dedup', 'decide', '--kind', $kind, '--date', $date,
                           '--name', $name, '--decision', $decision, '--note', (string)$note]);
    }
    rv_json_response($res);
}

// ---------- setts ----------
if ($action === 'setts') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') rv_json_response(['status' => 'error', 'message' => '仅POST']);
    $date = isset($_REQUEST['date']) ? $_REQUEST['date'] : '';
    $name = isset($_REQUEST['name']) ? $_REQUEST['name'] : '';
    $ts = isset($_REQUEST['ts']) ? trim($_REQUEST['ts']) : '';
    if (!preg_match('/^\d{4}-\d{2}$/', $date) || $name === '' || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $ts)) {
        rv_json_response(['status' => 'error', 'message' => '参数格式错误']);
    }
    $res = rv_run_cli(['magnet', 'setts', '--date', $date, '--name', $name, '--ts', $ts]);
    rv_json_response($res);
}

rv_json_response(['status' => 'error', 'message' => '未知action: ' . $action]);
