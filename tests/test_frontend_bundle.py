"""前端 bundle 的冒烟测试：assets/app.js 必须在最小 DOM stub 下完整执行。

背景（2026-11-26）：`nextCover` 曾被定义在 `initData()` 内部，而 IIFE 顶层
`window.gshelf = { ..., nextCover }` 引用它，运行时抛 ReferenceError，整个
IIFE 中断、`window.gshelf` 变成 undefined，页面上的 `gshelf.initDashboard()`
随之失败——首页与年历都停在「加载中」。`node --check` 只查语法，查不出这类
作用域错误，所以这里真的执行一遍。
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "assets" / "app.js"

STUB = r"""
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const noop = () => {};
const el = {
  value: '', textContent: '', innerHTML: '', style: {},
  classList: { add: noop, remove: noop }, addEventListener: noop,
  querySelector: () => null, querySelectorAll: () => [], dataset: {},
};
global.document = {
  getElementById: () => el,
  querySelector: () => ({ src: 'http://localhost/assets/app.js', getAttribute: () => '' }),
  querySelectorAll: () => [], addEventListener: noop,
};
global.window = {};
global.fetch = () => new Promise(() => {});
global.location = { search: '' };
global.setInterval = noop;
eval(code);
const api = global.window.gshelf;
if (!api) { console.log('FAIL: window.gshelf 未定义'); process.exit(2); }
for (const name of ['initDashboard', 'initData', 'api', 'nextCover']) {
  if (typeof api[name] !== 'function') { console.log('FAIL: 缺少 ' + name); process.exit(3); }
}
try { api.initDashboard(); } catch (e) { console.log('FAIL: initDashboard 抛错 ' + e.message); process.exit(4); }
try { api.initData(); } catch (e) { console.log('FAIL: initData 抛错 ' + e.message); process.exit(5); }
console.log('OK');
"""


@unittest.skipUnless(shutil.which("node"), "需要 node 才能执行前端冒烟测试")
class AppBundleTests(unittest.TestCase):
    def test_iife_runs_and_exports_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub.js"
            stub.write_text(STUB, encoding="utf-8")
            result = subprocess.run(
                ["node", str(stub), str(APP_JS)],
                capture_output=True, text=True, cwd=str(ROOT), timeout=60,
            )
        self.assertEqual(
            result.returncode, 0,
            f"app.js 执行失败: {result.stdout.strip()} {result.stderr.strip()}",
        )
        self.assertIn("OK", result.stdout)
