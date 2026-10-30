<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>总控 · pyGal</title>
<link rel="stylesheet" href="assets/app.css?v=<?= rawurlencode((string) filemtime(__DIR__ . '/assets/app.css')) ?>">
</head>
<body>
<?php require __DIR__ . '/includes/header.php'; ?>
<main>
  <div class="grid">

    <section class="card" aria-label="流水线">
      <div class="card-h">流水线 <span class="muted small" id="pipeline-updated" style="font-weight:400"></span></div>
      <div class="card-b"><div class="steps" id="steps"></div></div>
    </section>

    <section class="card" aria-label="控制器">
      <div class="card-h">控制器</div>
      <div class="card-b">
        <div class="control">
          <div class="field"><label for="start-year">起始年</label><input id="start-year" class="inp num" type="number" min="1980" max="3000"></div>
          <div class="field"><label for="end-year">结束年</label><input id="end-year" class="inp num" type="number" min="1980" max="3000"></div>
          <div class="field"><label for="task-month">月份</label>
            <select id="task-month" class="inp">
              <option value="0">全年</option>
              <?php for ($m = 1; $m <= 12; $m++): ?><option value="<?= $m ?>"><?= $m ?>月</option><?php endfor; ?>
            </select>
          </div>
          <button id="btn-sync" class="btn primary" type="button">同步 <span class="sub">清单 + 磁链</span></button>
          <button id="btn-submit" class="btn" type="button">提交并校对 <span class="sub" id="submit-sub"></span></button>
          <div class="spacer"></div>
          <button id="btn-stop" class="btn" type="button" hidden>停止</button>
        </div>
        <div class="gate" id="gate" hidden></div>
        <div class="hint" id="control-msg" role="status"></div>
      </div>
    </section>

    <section class="card" aria-label="当前任务">
      <div class="card-h">当前任务 <span class="pill p-mute" id="task-state">空闲</span></div>
      <div class="card-b">
        <div class="bar"><i id="bar" style="width:0%"></i></div>
        <div class="kv" style="margin-top:12px">
          <div><span>已完成</span> <b class="num" id="k-done">0 / 0</b></div>
          <div><span>成功</span> <b class="num" id="k-success">0</b></div>
          <div><span>失败</span> <b class="num" id="k-failed">0</b></div>
          <div><span>跳过</span> <b class="num" id="k-skipped">0</b></div>
          <div><span>当前</span> <b id="k-current">—</b></div>
        </div>
        <div id="task-results" style="margin-top:14px"></div>
      </div>
    </section>

    <section class="card" aria-label="年历">
      <div class="card-h">年历</div>
      <div class="card-b" style="padding:0 18px 0">
        <table class="cal">
          <thead>
            <tr><th rowspan="2" style="width:72px">年份</th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th></tr>
            <tr><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th><th>12月</th></tr>
          </thead>
          <tbody id="cal-body"><tr><td colspan="7" class="empty">加载中…</td></tr></tbody>
        </table>
      </div>
      <div class="legend"><span><i style="background:var(--ok-soft)"></i>下载齐</span><span><i style="background:var(--warn-soft)"></i>有缺口</span><span><i style="background:var(--surface-2)"></i>无数据</span></div>
    </section>

    <section class="card" aria-label="维护">
      <details class="fold">
        <summary>维护 · 清理任务记录与 db 快照</summary>
        <div class="fold-b">
          <div class="control">
            <div class="field"><label for="m-keep">任务记录保留个数</label><input id="m-keep" class="inp num" type="number" value="50" min="0"></div>
            <div class="field"><label for="m-days">保留天数</label><input id="m-days" class="inp num" type="number" value="14" min="0"></div>
            <div class="field"><label for="m-dbkeep">db 快照保留个数</label><input id="m-dbkeep" class="inp num" type="number" value="3" min="0"></div>
            <button id="btn-prune-preview" class="btn" type="button">预览清理</button>
            <button id="btn-prune-run" class="btn" type="button">执行清理</button>
          </div>
          <div class="hint" id="prune-msg"></div>
        </div>
      </details>
    </section>

  </div>
</main>
<script src="assets/app.js?v=<?= rawurlencode((string) filemtime(__DIR__ . '/assets/app.js')) ?>"></script>
<script>pyGal.initDashboard();</script>
</body>
</html>
