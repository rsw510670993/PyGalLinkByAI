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
    <a class="brand" href="<?= htmlspecialchars($base, ENT_QUOTES) ?>/index.php"><i></i>gshelf</a>
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
<nav class="tabbar" aria-label="底部导航">
  <a class="tb<?= $isDash ? ' on' : '' ?>" href="<?= htmlspecialchars($base, ENT_QUOTES) ?>/index.php">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7.5" height="7.5" rx="1.8"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.8"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.8"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.8"/></svg>
    <span>总控</span>
  </a>
  <a class="tb<?= $isData ? ' on' : '' ?>" href="<?= htmlspecialchars($base, ENT_QUOTES) ?>/tool/egs.php">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2.2"/><path d="M3 9.5h18M9.5 9.5V20"/></svg>
    <span>数据</span>
  </a>
</nav>
