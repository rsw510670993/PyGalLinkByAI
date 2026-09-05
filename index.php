<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>年历与控制 · pyGal</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .calendar-table td { min-width: 155px; vertical-align: top; }
        .calendar-table .counts { font-size: .8rem; margin-top: .25rem; white-space: nowrap; }
        .calendar-table .month-link { display: block; color: inherit; text-decoration: none; }
        .calendar-table .month-select { border: 0; background: none; padding: 0; color: inherit; font: inherit; font-weight: 700; cursor: pointer; }
        .calendar-table .month-select:hover { text-decoration: underline; }
        .calendar-table .month-select:focus-visible { outline: 2px solid #0d6efd; outline-offset: 3px; border-radius: 2px; }
        #task-details { max-height: 360px; overflow: auto; }
        .scope-form .form-control, .scope-form .form-select { min-width: 0; }
    </style>
</head>
<body class="bg-light">
<?php require __DIR__ . '/includes/header.php'; ?>
<main class="container my-4">
    <h1 class="h3 mb-3">年历与控制</h1>
    <section class="card mb-3" aria-label="任务控制">
        <div class="card-body">
            <form id="scope-form" class="scope-form">
                <div class="row g-3 align-items-end">
                    <div class="col-6 col-md-3">
                        <label for="start-year" class="form-label">起始年份</label>
                        <input id="start-year" type="number" class="form-control" min="1980" max="3000" required>
                    </div>
                    <div class="col-6 col-md-3">
                        <label for="end-year" class="form-label">结束年份</label>
                        <input id="end-year" type="number" class="form-control" min="1980" max="3000" required>
                    </div>
                    <div class="col-6 col-md-3">
                        <label for="task-month" class="form-label">月份</label>
                        <select id="task-month" class="form-select">
                            <option value="0">全年</option>
                            <?php for ($m = 1; $m <= 12; $m++): ?><option value="<?= $m ?>"><?= $m ?>月</option><?php endfor; ?>
                        </select>
                    </div>
                    <div class="col-6 col-md-3">
                        <div class="form-check mb-2">
                            <input id="organize-preview" class="form-check-input" type="checkbox" checked>
                            <label for="organize-preview" class="form-check-label">整理115仅预览</label>
                        </div>
                    </div>
                </div>
                <div class="d-flex flex-wrap gap-2 mt-3">
                    <button type="button" data-action="crawl" class="btn btn-primary">获取游戏清单</button>
                    <button type="button" data-action="magnet" class="btn btn-outline-primary">获取下载用磁链</button>
                    <button type="button" data-action="check" class="btn btn-outline-info">校对115</button>
                    <button type="button" data-action="submit" class="btn btn-outline-success">提交115</button>
                    <button type="button" data-action="organize" class="btn btn-outline-secondary">整理115</button>
                </div>
                <div class="small text-muted mt-3">五项操作均使用上方年月范围。清单来自 EGS；磁链仅补充未匹配项目；校对检查尚未确认下载的作品；提交跳过已提交或已下载作品。整理按发售日和公司命名，归入 /GAL/GAL-年份，取消“仅预览”后执行。</div>
            </form>
            <div id="control-message" class="mt-2" role="status"></div>
        </div>
    </section>
    <section id="task-card" class="card mb-3" hidden aria-label="任务进度">
        <div class="card-body">
            <div class="d-flex justify-content-between align-items-center gap-2">
                <h2 id="task-title" class="h6 mb-0"></h2>
                <button type="button" id="stop-task" class="btn btn-sm btn-outline-danger" hidden>停止任务</button>
            </div>
            <div id="task-message" class="small my-2" role="status"></div>
            <div class="progress" style="height: 12px;"><div id="task-progress" class="progress-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" style="width: 0%"></div></div>
            <div id="task-counts" class="small text-muted mt-2"></div>
            <details class="mt-2"><summary>查看处理结果</summary><div id="task-details" class="small mt-2"></div></details>
        </div>
    </section>
    <div class="modal fade" id="pendingReviewModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">存在待审核记录</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    当前范围内还有 <span id="pending-review-count" class="fw-semibold">0</span> 条待审核记录。<br>
                    请先完成审核，再批量校对115。
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">知道了</button>
                    <a id="pending-review-link" class="btn btn-primary btn-sm" href="#">去审核</a>
                </div>
            </div>
        </div>
    </div>
    <section class="card" aria-label="EGS 年历">
        <div class="card-header d-flex flex-wrap gap-2 align-items-center justify-content-between">
            <label for="calendar-year" class="fw-semibold">年历（所选年及前两年）</label>
            <div class="d-flex gap-2"><input id="calendar-year" type="number" class="form-control form-control-sm" min="1980" max="3000" aria-label="年历年份" style="width:110px"><button id="refresh-calendar" class="btn btn-sm btn-outline-secondary">刷新</button></div>
        </div>
        <div class="card-body py-2 small text-muted">点击年份设置整年任务范围；点击月份设置当月任务范围，点击数量查看 EGS 明细。绿色：有磁链作品全部校对已下载；蓝色：全部已提交；黄色：尚未全部完成；灰色：无数据。</div>
        <div class="table-responsive"><table class="table table-bordered calendar-table mb-0"><thead><tr><th>年份</th><?php for ($m = 1; $m <= 6; $m++): ?><th><?= $m ?> / <?= $m + 6 ?>月</th><?php endfor; ?></tr></thead><tbody id="calendar-body"></tbody></table></div>
    </section>
</main>
<script>const basePath = <?= json_encode($base, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;</script>
<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.1/js/bootstrap.bundle.min.js"></script>
<script src="<?= htmlspecialchars($base, ENT_QUOTES, 'UTF-8') ?>/assets/dashboard.js"></script>
</body>
</html>
