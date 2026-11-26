/* gshelf 前端：总控（index.php）与数据（tool/egs.php）两页共用。
   页面只读 + 机械任务开关；判定 / 审核 / 整理由 AI agent（DSH）在会话里通过 CLI 完成。 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  /* 封面图片加载失败时按 data-chain 逐级回退，全部失败才显示占位。
     必须放在顶层：window.gshelf 要导出它，卡片内联 onerror 也依赖全局引用。 */
  function nextCover(img) {
    let chain = [];
    try { chain = JSON.parse(img.dataset.chain || '[]'); } catch (e) { chain = []; }
    if (chain.length) {
      img.src = chain.shift();
      img.dataset.chain = JSON.stringify(chain);
      return;
    }
    const box = img.parentNode;
    if (!box) return;
    box.classList.add('ph');
    box.textContent = '无封面';
  }
  /* 结果状态码 → 中文标签（对应后端 egs_organize 的 detail.status） */
  const STATUS_LABEL = {
    already_ok: '已就位', renamed: '已整理', moved: '已整理', renamed_moved: '已整理',
    would_rename: '待整理', would_move: '待整理', would_rename_moved: '待整理',
    would_set_downloaded: '待补记', found_set_downloaded: '已补记',
    cross_year_confirm: '跨年待确认', month_shift_confirm: '搬月待确认',
    month_shift_rejected: '已拒绝搬动', cross_year_rejected: '已拒绝搬动',
    in_offline: '离线下载中', missing_in_115: '115 中找不到',
    not_downloaded: '未下载', wrapped_file: '已包装', would_wrap_file: '待包装',
    repaired_double_wrap: '已修复双层包装', would_repair_double_wrap: '待修复双层包装',
    conflict: '冲突', ambiguous: '无法确定', no_dn_date: '缺日期', no_link: '无磁链',
    duplicate_magnet: '磁链重复', not_submittable: '不可提交', outside_scope: '不在范围',
    shared_cid: '目录映射冲突',
    adopted_exact_torrent_name: '已认领', adopted_same_infohash: '已认领',
    error: '出错',
  };
  const STATUS_TONE = {
    '出错': 'p-err', '冲突': 'p-err', '无法确定': 'p-err', '115 中找不到': 'p-err',
    '跨年待确认': 'p-warn', '搬月待确认': 'p-warn', '缺日期': 'p-warn', '目录映射冲突': 'p-warn',
    '已整理': 'p-ok', '已就位': 'p-ok', '已补记': 'p-ok', '已认领': 'p-ok',
  };
  const scriptEl = document.querySelector('script[src*="app.js"]');
  const basePath = scriptEl
    ? new URL(scriptEl.src).pathname.replace(/\/assets\/app\.js.*$/, '')
    : '';

  async function api(action, body) {
    const options = body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { cache: 'no-store' };
    const res = await fetch(`${basePath}/tool/api.php?action=${action}`, options);
    let data = {};
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || data.status === 'error') {
      const err = new Error(data.message || `请求失败 (${res.status})`);
      err.payload = data;
      throw err;
    }
    return data;
  }

  async function loadTop() {
    try {
      const ov = await api('overview');
      $('total-count').textContent = Number(ov.total).toLocaleString('zh-CN') + ' 条';
      let ok = false;
      try {
        const login = await api('115_login_status');
        ok = !!login.logged_in;
      } catch (e) {
        // 401 是「站点 SSO 未登录」，不是 115 失效，必须分开显示
        if (e.payload && String(e.payload.message || '').includes('登录')) {
          $('login-state').innerHTML = '<span class="dot bad"></span><span>站点未登录</span>';
          return ov;
        }
        ok = false;
      }
      $('login-state').innerHTML = `<span class="dot ${ok ? 'ok' : 'bad'}"></span><span>115 ${ok ? '已登录' : '未登录'}</span>`;
      return ov;
    } catch (e) {
      $('login-state').innerHTML = '<span class="dot bad"></span><span>115 状态未知</span>';
      throw e;
    }
  }

  /* ==================== 总控 ==================== */
  function initDashboard() {
    const now = new Date();
    const initialYear = Number(new URLSearchParams(location.search).get('year')) || now.getFullYear();
    $('start-year').value = $('end-year').value = initialYear;

    let ov = null;
    let task = null;
    let prevRunning = false;
    let busy = false;

    function scope() {
      return {
        start_year: Number($('start-year').value) || 0,
        end_year: Number($('end-year').value) || 0,
        month: Number($('task-month').value) || 0,
      };
    }

    function renderTodo() {
      if (!ov) return;
      const setNum = (id, value) => {
        const el = $(id);
        if (el) el.textContent = Number(value || 0).toLocaleString('zh-CN');
      };
      setNum('t-review', ov.pending_review);
      setNum('t-submit', ov.pending_submit);
      setNum('t-cover', ov.cover_missing_downloaded);
      setNum('t-issues', ov.issues);
      const stamp = $('todo-updated');
      if (stamp) stamp.textContent = task && task.updated_at ? `${task.updated_at} 更新` : '';
    }

    function renderTaskResults(results) {
      const groups = new Map();
      (results || []).forEach((row, i) => {
        const type = (row.detail && row.detail.status) || row.outcome || 'other';
        if (!groups.has(type)) groups.set(type, { type, outcome: row.outcome || 'other', rows: [], i });
        groups.get(type).rows.push(row);
      });
      if (!groups.size) return '';
      const priority = { failed: 0, skipped: 1, success: 2, other: 3 };
      const badge = { failed: 'p-err', success: 'p-ok', skipped: 'p-mute' };
      return [...groups.values()]
        .sort((a, b) => (priority[a.outcome] ?? 3) - (priority[b.outcome] ?? 3) || a.i - b.i)
        .map((g) => {
          const label = STATUS_LABEL[g.type] || g.type;
          const tone = STATUS_TONE[label] || badge[g.outcome] || 'p-mute';
          return `<details class="result-group"><summary><span>${esc(label)}</span>`
            + `<span class="pill ${tone}">${g.rows.length}</span></summary>`
            + `<div class="rg-b">${g.rows.map((r) => `<div class="result-row"><strong>${esc(r.name)}</strong>：${esc(r.message)}`
              + (r.detail && r.detail.target_path ? `<div class="rr-t">${esc(r.detail.old_path || '')} → ${esc(r.detail.target_path)}</div>` : '')
              + `</div>`).join('')}</div></details>`;
        })
        .join('');
    }

    function renderTask() {
      const running = !!(task && task.running);
      $('btn-sync').disabled = busy || running || !ov;
      $('btn-submit').disabled = busy || running || !ov;
      $('btn-stop').hidden = !running;
      $('btn-stop').disabled = false;

      $('submit-sub').textContent = ov ? `${ov.pending_submit} 条` : '';

      const state = $('task-state');
      if (running) { state.className = 'pill p-info'; state.textContent = '运行中'; }
      else if (task && task.job_id) {
        const ok = task.outcome === 'complete' || task.outcome === 'partial' || task.outcome === 'stopped';
        state.className = `pill ${ok ? 'p-ok' : 'p-err'}`;
        state.textContent = task.message || task.outcome || '空闲';
      } else { state.className = 'pill p-mute'; state.textContent = '空闲'; }

      if (task && task.job_id) {
        const total = Number(task.total || 0), done = Number(task.done || 0);
        $('bar').style.width = total ? Math.round(done / total * 100) + '%' : '0%';
        $('k-done').textContent = done;
        $('k-total').textContent = total;
        $('k-success').textContent = Number(task.success || 0);
        $('k-failed').textContent = Number(task.failed || 0);
        $('k-skipped').textContent = Number(task.skipped || 0);
        $('k-current').textContent = task.current || '—';
        $('task-results').innerHTML = renderTaskResults(task.results);
      } else {
        $('bar').style.width = '0%';
        $('k-done').textContent = '0';
        $('k-total').textContent = '0';
        $('k-success').textContent = '0';
        $('k-failed').textContent = '0';
        $('k-skipped').textContent = '0';
        $('k-current').textContent = '—';
        $('task-results').innerHTML = '';
      }
      renderTodo();
    }

    async function poll() {
      try {
        const t = await api('pipeline_status');
        const wasRunning = prevRunning;
        task = t;
        prevRunning = !!t.running;
        // 提交刚完成：校对由 driver/CLI 在 10 分钟后触发，页面只提示不代跑
        if (wasRunning && !t.running && t.action === 'submit' && t.outcome !== 'error') {
          $('control-msg').textContent = '提交完成；校对由提交 driver 或 CLI 在 10 分钟后触发。';
        }
        renderTask();
      } catch (e) {
        const needLogin = e.payload && String(e.payload.message || '').includes('登录');
        $('control-msg').innerHTML = needLogin
          ? '站点登录已过期 · <a href="/login">重新登录</a>'
          : `状态读取失败：${esc(e.message)}`;
      }
    }

    async function start(action) {
      if (busy) return;
      const sc = scope();
      if (!sc.start_year || !sc.end_year) { $('control-msg').textContent = '请填写起止年份'; return; }
      if (sc.end_year < sc.start_year) { $('control-msg').textContent = '结束年份不能小于起始年份'; return; }
      busy = true;
      $('btn-sync').disabled = true;
      $('btn-submit').disabled = true;
      try {
        const res = await api('pipeline_start', { action, start_year: sc.start_year, end_year: sc.end_year, month: sc.month, execute: false });
        $('control-msg').textContent = res.message || '已启动';
        await poll();
      } catch (e) {
        $('control-msg').textContent = e.message;
      } finally {
        busy = false;
        renderTask();
      }
    }

    async function loadCalendar() {
      const year = Number($('start-year').value) || new Date().getFullYear();
      try {
        const res = await api(`calendar&year=${year}`);
        const years = Array.isArray(res.years) ? res.years : [];
        const cell = (y, m) => {
          const cls = !m.has_data ? 'none' : (m.all_magnet_downloaded ? 'good' : 'warn');
          return `<td><a class="m ${cls}" href="${basePath}/tool/egs.php?year=${y}&month=${m.month}">`
            + `<span class="mn">${m.month}月</span>`
            + `<span class="mc"><span>作品 <b>${m.total}</b></span><span>磁链 <b>${m.magnet_total}</b></span><span>已下 <b>${m.magnet_downloaded}</b></span></span></a></td>`;
        };
        $('cal-body').innerHTML = years.map((y) => {
          const first = y.months.slice(0, 6).map((m) => cell(y.year, m)).join('');
          const second = y.months.slice(6).map((m) => cell(y.year, m)).join('');
          return `<tr><td class="y" rowspan="2">${y.year}</td>${first}</tr><tr>${second}</tr>`;
        }).join('') || '<tr><td colspan="7" class="empty">没有数据</td></tr>';
      } catch (e) {
        $('cal-body').innerHTML = `<tr><td colspan="7" class="empty">年历加载失败：${esc(e.message)}</td></tr>`;
      }
    }

    async function prune(execute) {
      $('prune-msg').textContent = execute ? '执行中…' : '预览中…';
      try {
        const res = await api('agent_job_prune', {
          keep: Number($('m-keep').value) || 0,
          days: Number($('m-days').value) || 0,
          db_keep: Number($('m-dbkeep').value) || 0,
          db_days: Number($('m-days').value) || 0,
          execute: execute,
        });
        $('prune-msg').textContent = res.message || JSON.stringify(res);
      } catch (e) {
        $('prune-msg').textContent = `失败：${e.message}`;
      }
    }

    $('btn-sync').addEventListener('click', () => start('sync'));
    $('btn-submit').addEventListener('click', () => start('submit'));
    $('btn-stop').addEventListener('click', async () => {
      if (!task || !task.job_id) return;
      $('btn-stop').disabled = true;
      try {
        const res = await api('pipeline_stop', { job_id: task.job_id });
        $('control-msg').textContent = res.message || '已请求停止';
      } catch (e) { $('control-msg').textContent = e.message; }
    });
    $('start-year').addEventListener('change', loadCalendar);
    $('btn-prune-preview').addEventListener('click', () => prune(false));
    $('btn-prune-run').addEventListener('click', () => prune(true));

    loadTop().then((data) => { ov = data; renderTask(); }).catch(() => {});
    loadCalendar();
    poll();
    setInterval(poll, 3000);
    setInterval(() => {
      loadTop().then((data) => { ov = data; renderTask(); }).catch(() => {});
    }, 30000);
  }

  /* ==================== 数据 ==================== */
  function initData() {
    const PAGE_SIZE = 24;
    let page = 1;
    let total = 0;

    function stageOf(r) {
      if (Number(r.download_failed)) return ['failed', '下载失败', 'p-err'];
      if (Number(r.magnet_duplicate)) return ['dup', '重复', 'p-mute'];
      if (Number(r.downloaded)) return ['downloaded', '已下载', 'p-ok'];
      if (Number(r.submitted_115)) return ['submitted', '已提交', 'p-info'];
      if (r.link) return ['linked', '有磁链', 'p-warn'];
      return ['none', '无资源', 'p-mute'];
    }

    /* 封面地址链：getchu → dlsite → dmm → 官网，按序回退（与后端 has_cover 同口径） */
    function coverChain(r) {
      const list = [];
      if (r.getchu_id) {
        const gid = esc(String(r.getchu_id));
        list.push(`${basePath}/thumbnails/web/${gid}.webp`, `${basePath}/thumbnails/${gid}.jpg`);
      }
      if (r.dlsite_id) {
        const did = esc(String(r.dlsite_id));
        list.push(`${basePath}/thumbnails/web/dlsite/${did}.webp`, `${basePath}/thumbnails/dlsite/${did}.jpg`);
      }
      if (r.dmm) list.push(`${basePath}/thumbnails/web/dmm/${esc(String(r.dmm))}.webp`);
      if (r.egs_id) list.push(`${basePath}/thumbnails/web/official/${esc(String(r.egs_id))}.webp`);
      return list;
    }

    function cover(r) {
      const list = coverChain(r);
      if (!list.length) return '<div class="cov ph">无封面</div>';
      return `<div class="cov"><img loading="lazy" alt="" src="${list[0]}"`
        + ` data-chain="${esc(JSON.stringify(list.slice(1)))}" onerror="gshelf.nextCover(this)"></div>`;
    }

    function render(data) {
      total = Number(data.total || 0);
      const rows = data.data || [];
      $('cards').innerHTML = rows.length ? rows.map((r) => {
        const [stage, label, cls] = stageOf(r);
        const sub = [r.company, r.date].filter(Boolean).map(esc).join(' · ');
        const note = r.comment ? `<div class="note">${esc(r.comment)}</div>` : '';
        const review = r.review_status === 'pending'
          ? '<span class="pill p-warn">待审</span>' : '';
        const score = (r.best_score !== null && r.best_score !== undefined && r.best_score !== '')
          ? `<span class="pill p-mute">${Math.round(Number(r.best_score))} 分</span>` : '';
        const chain = coverChain(r);
        const open = chain.length ? ` data-cover="${esc(chain[0])}"` : '';
        return `<div class="game" data-stage="${stage}"${open}>`
          + cover(r)
          + `<div class="meta"><div class="t">${esc(r.name)}</div>`
          + `<div class="s">${sub}</div>`
          + `<div class="badges"><span class="pill ${cls}">${label}</span>${review}${score}</div>${note}</div></div>`;
      }).join('') : '<div class="empty">没有符合条件的记录</div>';

      const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      $('page-info').textContent = `第 ${page} / ${pages} 页 · 共 ${total.toLocaleString('zh-CN')} 条`;
      $('prev').disabled = page <= 1;
      $('next').disabled = page >= pages;
      $('list-meta').textContent = `只读 · 共 ${total.toLocaleString('zh-CN')} 条`;

      $('cards').querySelectorAll('[data-cover]').forEach((el) => {
        el.addEventListener('click', (ev) => {
          const img = el.querySelector('.cov img');
          const url = (img && img.getAttribute('src')) || el.getAttribute('data-cover');
          if (!url) return;
          ev.preventDefault();
          $('lb-img').src = url;
          $('lightbox').classList.add('on');
        });
      });
    }

    function query() {
      const params = new URLSearchParams({ page: String(page), per_page: String(PAGE_SIZE) });
      const year = $('f-year').value, month = $('f-month').value;
      const q = $('f-q').value.trim(), stage = $('f-stage').value, brand = $('f-brand').value;
      if (year) params.set('year', year);
      if (month) params.set('month', month);
      if (q) params.set('q', q);
      if (stage) params.set('stage', stage);
      if (brand) params.set('brand_kind', brand);
      return params.toString();
    }

    async function load() {
      if (!$('cards').querySelector('.game')) $('cards').innerHTML = '<div class="empty">加载中…</div>';
      try {
        const data = await api(`egs_games&${query()}`);
        render(data);
      } catch (e) {
        $('cards').innerHTML = `<div class="err-box">加载失败：${esc(e.message)}</div>`;
      }
    }

    async function loadYears() {
      try {
        const res = await api('years&source=egs');
        const years = Array.isArray(res.years) ? res.years : [];
        $('f-year').innerHTML = '<option value="">全部</option>'
          + years.slice().reverse().map((y) => `<option value="${esc(y)}">${esc(y)}</option>`).join('');
      } catch (e) { /* 年份下拉不是关键路径 */ }
    }

    $('f-apply').addEventListener('click', () => { page = 1; load(); });
    $('f-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') { page = 1; load(); } });
    // 输入即搜（主流做法，400ms 防抖），下拉改变也即时生效
    let searchTimer = null;
    $('f-q').addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { page = 1; load(); }, 400);
    });
    ['f-year', 'f-month', 'f-stage', 'f-brand'].forEach((id) => {
      $(id).addEventListener('change', () => { page = 1; load(); });
    });
    $('prev').addEventListener('click', () => { if (page > 1) { page--; load(); } });
    $('next').addEventListener('click', () => { page++; load(); });
    $('lightbox').addEventListener('click', (e) => {
      if (e.target.id === 'lightbox' || e.target.classList.contains('lb-close')) $('lightbox').classList.remove('on');
    });

    loadYears();
    load();
    loadTop().catch(() => {});
  }

  window.gshelf = { initDashboard, initData, api, nextCover };
})();
