<?php
// pyGal 接口层：只读面板 + 机械任务开关。
// 判定 / 审核 / 修正 / 整理全部由 AI agent（DSH）通过 tool/cli.py 完成，
// 这里不再提供写记录、审核候选、AI 整理的入口。

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

function run_cli($args, $entry = 'cli.py') {
    $root = realpath(__DIR__ . '/..');
    $cli = __DIR__ . '/' . $entry;

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
    $env = [];
    foreach ($_SERVER as $key => $value) {
        if (is_string($key) && is_scalar($value)) $env[$key] = strval($value);
    }
    $env['HOME'] = $root;

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

if ($action === 'overview') {
    [$code, $data] = run_cli(['egs', 'overview']);
    json_response($data);
}

if ($action === 'years') {
    [$code, $data] = run_cli(['years', '--source', ($_GET['source'] ?? '') === 'egs' ? 'egs' : 'getchu']);
    json_response($data);
}

if ($action === 'calendar') {
    $year = as_int($_GET['year'] ?? null, null);
    $args = ['calendar'];
    if ($year !== null) { $args[] = '--year'; $args[] = strval($year); }
    [$code, $data] = run_cli($args);
    json_response($data);
}

if ($action === 'egs_games') {
    $page = as_int($_GET['page'] ?? 1, 1);
    $perPage = as_int($_GET['per_page'] ?? 24, 24);
    $year = as_int($_GET['year'] ?? null, null);
    $month = as_int($_GET['month'] ?? null, null);
    $q = isset($_GET['q']) ? trim((string)$_GET['q']) : '';
    $brandKind = strtoupper(trim(strval($_GET['brand_kind'] ?? '')));
    if (!in_array($brandKind, ['CIRCLE', 'CORPORATION'], true)) $brandKind = '';
    $stage = trim(strval($_GET['stage'] ?? ''));
    if (!in_array($stage, ['downloaded', 'linked', 'none', 'failed'], true)) $stage = '';

    $args = ['egs', 'games', '--page', strval(max(1, $page)), '--per-page', strval(max(1, $perPage))];
    if ($year !== null) { $args[] = '--year'; $args[] = strval($year); }
    if ($month !== null) { $args[] = '--month'; $args[] = strval($month); }
    if ($q !== '') { $args[] = '--q'; $args[] = $q; }
    if ($brandKind !== '') { $args[] = '--brand-kind'; $args[] = $brandKind; }
    if ($stage !== '') { $args[] = '--stage'; $args[] = $stage; }

    [$code, $data] = run_cli($args);
    json_response($data);
}

if ($action === 'pipeline_preflight') {
    $actionName = strval($_GET['pipeline_action'] ?? 'check');
    $args = ['preflight', '--action', $actionName,
             '--start-year', strval(as_int($_GET['start_year'] ?? null, 0)),
             '--end-year', strval(as_int($_GET['end_year'] ?? null, 0)),
             '--month', strval(as_int($_GET['month'] ?? null, 0))];
    [$code, $data] = run_cli($args, 'pipeline_entry.py');
    json_response($data);
}

if (in_array($action, ['pipeline_start', 'pipeline_status', 'pipeline_stop'], true)) {
    if ($action !== 'pipeline_status' && ($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        json_response(['status' => 'error', 'message' => '请使用 POST 请求']);
    }
    $body = read_json_body();
    if ($action === 'pipeline_status') {
        $args = ['status'];
    } elseif ($action === 'pipeline_stop') {
        $args = ['stop', '--job-id', strval($body['job_id'] ?? '')];
    } else {
        $args = ['start', '--action', strval($body['action'] ?? ''),
                 '--start-year', strval(as_int($body['start_year'] ?? null, 0)),
                 '--end-year', strval(as_int($body['end_year'] ?? null, 0)),
                 '--month', strval(as_int($body['month'] ?? null, 0))];
        if (($body['execute'] ?? false) === true) $args[] = '--execute';
    }
    [$code, $data] = run_cli($args, 'pipeline_entry.py');
    json_response($data);
}

if ($action === '115_login_status') {
    [$code, $data] = run_cli(['115', 'login_status']);
    json_response($data);
}

// 维护：清理任务记录与 db 快照（默认预览，execute=true 才真删）
if ($action === 'agent_job_prune') {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        json_response(['status' => 'error', 'message' => '请使用 POST 请求']);
    }
    $body = read_json_body();
    $args = ['prune',
             '--keep', strval(as_int($body['keep'] ?? null, 50)),
             '--days', strval(as_int($body['days'] ?? null, 14)),
             '--db-keep', strval(as_int($body['db_keep'] ?? null, 3)),
             '--db-days', strval(as_int($body['db_days'] ?? null, 7)),
             '--requested-by', 'web'];
    if (($body['execute'] ?? false) === true) $args[] = '--execute';
    [$code, $data] = run_cli($args, 'agent_jobs_entry.py');
    json_response($data);
}

json_response(['status' => 'error', 'code' => 404, 'message' => '未知接口']);
