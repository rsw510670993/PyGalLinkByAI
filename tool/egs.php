<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据 · pyGal</title>
<link rel="stylesheet" href="../assets/app.css?v=<?= rawurlencode((string) filemtime(__DIR__ . '/../assets/app.css')) ?>">
</head>
<body>
<?php require dirname(__DIR__) . '/includes/header.php'; ?>
<main>
  <div class="grid">

    <section class="card" aria-label="筛选">
      <div class="card-b" style="padding:14px 16px">
        <div class="filters">
          <div class="field"><label for="f-year">年份</label><select id="f-year" class="inp"><option value="">全部</option></select></div>
          <div class="field"><label for="f-month">月份</label>
            <select id="f-month" class="inp">
              <option value="">全部</option>
              <?php for ($m = 1; $m <= 12; $m++): ?><option value="<?= $m ?>"><?= $m ?>月</option><?php endfor; ?>
            </select>
          </div>
          <div class="field"><label for="f-q">搜索</label><input id="f-q" class="inp" type="search" placeholder="游戏名 / 公司 / 假名"></div>
          <div class="field"><label for="f-stage">阶段</label>
            <select id="f-stage" class="inp">
              <option value="">全部</option>
              <option value="downloaded">已下载</option>
              <option value="linked">有磁链</option>
              <option value="none">无资源</option>
              <option value="failed">下载失败</option>
            </select>
          </div>
          <div class="field"><label for="f-brand">公司类型</label>
            <select id="f-brand" class="inp">
              <option value="">全部</option>
              <option value="CIRCLE">社团</option>
              <option value="CORPORATION">企业</option>
            </select>
          </div>
          <button id="f-apply" class="btn primary" type="button">筛选</button>
        </div>
      </div>
    </section>

    <section class="card" aria-label="EGS 清单">
      <div class="card-h">EGS 清单 <span class="muted small" id="list-meta" style="font-weight:400">只读</span></div>
      <div class="cards" id="cards"><div class="empty">加载中…</div></div>
      <div class="foot">
        <span class="num" id="page-info">—</span>
        <div class="spacer"></div>
        <button class="btn sm" id="prev" type="button" disabled>上一页</button>
        <button class="btn sm" id="next" type="button">下一页</button>
      </div>
    </section>

  </div>
</main>
<div class="lightbox" id="lightbox"><span class="lb-close" role="button" aria-label="关闭">×</span><img id="lb-img" alt=""></div>
<script src="../assets/app.js?v=<?= rawurlencode((string) filemtime(__DIR__ . '/../assets/app.js')) ?>"></script>
<script>pyGal.initData();</script>
</body>
</html>
