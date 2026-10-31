<?php
// 公共顶栏：两页共用（总控 index.php / 数据 tool/egs.php）。
$script = str_replace('\\', '/', (string)($_SERVER['SCRIPT_NAME'] ?? '/index.php'));
$page = basename($script);
$dir = str_replace('\\', '/', dirname($script));
$base = rtrim(basename($dir) === 'tool' ? dirname($dir) : $dir, '/');
if ($base === '.' || $base === '/') $base = '';
$isDash = $page === 'index.php';
$isData = $page === 'egs.php';
?>
<div class="topbar">
  <div class="topbar-in">
    <a class="brand" href="<?= htmlspecialchars($base, ENT_QUOTES) ?>/index.php"><i></i>pyGal</a>
    <nav class="tabs" aria-label="主导航">
      <a class="tab<?= $isDash ? ' on' : '' ?>" href="<?= htmlspecialchars($base, ENT_QUOTES) ?>/index.php">总控</a>
      <a class="tab<?= $isData ? ' on' : '' ?>" href="<?= htmlspecialchars($base, ENT_QUOTES) ?>/tool/egs.php">数据</a>
    </nav>
    <div class="topbar-right">
      <span id="login-state"><span class="dot"></span><span>115 …</span></span>
      <span class="num" id="total-count">— 条</span>
    </div>
  </div>
</div>
