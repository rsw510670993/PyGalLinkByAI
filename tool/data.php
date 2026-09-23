<?php
// Preserve bookmarked month filters after retiring the legacy data page.
$params = array_intersect_key($_GET, array_flip(['year', 'month', 'q']));
$query = http_build_query($params);
header('Location: egs.php' . ($query ? '?' . $query : ''), true, 302);
exit;
