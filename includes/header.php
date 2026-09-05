<?php
// Shared navigation for root pages and pages in tool/.
$scriptPath = $_SERVER['SCRIPT_NAME'] ?? '';
$pageFile = basename($scriptPath);
$pageDir = str_replace('\\', '/', dirname($scriptPath));
$base = rtrim(basename($pageDir) === 'tool' ? dirname($pageDir) : $pageDir, '/');
if ($base === '.' || $base === '/') $base = '';
$navItems = [
    ['index.php', '年历与控制'],
    ['tool/egs.php', 'EGS 数据'],
    ['tool/logs.php', '日志'],
];
$escapeHeader = static fn($value) => htmlspecialchars($value, ENT_QUOTES, 'UTF-8');
?>
<style>
    .pygal-header { position: sticky; top: 0; z-index: 1030; }
    .pygal-header > .container { flex-wrap: nowrap; min-height: 40px; gap: 16px; }
    .pygal-header .navbar-brand { flex-shrink: 0; margin-right: 0; }
    .pygal-header .navbar-nav { flex-direction: row; gap: 16px; min-width: 0; overflow-x: auto; white-space: nowrap; }
    .pygal-header .nav-link { flex-shrink: 0; }
</style>
<nav class="navbar navbar-dark bg-dark pygal-header" aria-label="主导航">
    <div class="container">
        <a class="navbar-brand" href="<?= $escapeHeader($base . '/index.php') ?>">pyGal</a>
        <div class="navbar-nav">
            <?php foreach ($navItems as [$path, $label]): ?>
                <?php $active = $pageFile === basename($path) || ($path === 'index.php' && $pageFile === 'calendar.php'); ?>
                <a class="nav-link<?= $active ? ' active' : '' ?>" href="<?= $escapeHeader($base . '/' . $path) ?>"<?= $active ? ' aria-current="page"' : '' ?>><?= $escapeHeader($label) ?></a>
            <?php endforeach; ?>
        </div>
    </div>
</nav>
