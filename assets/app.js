/* pyGal 前端：总控（index.php）与数据（tool/egs.php）两页共用。
   页面只读 + 机械任务开关；判定 / 审核 / 整理由 AI agent（DSH）在会话里通过 CLI 完成。 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
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

  function fmtSeconds(s) {
    const m = Math.floor(s / 60), r = s % 60;
    return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
  }

  async function loadTop() {
    try {
      const ov = await api('overview');
      $('total-count').textContent = Number(ov.total).toLocaleString('zh-CN') + ' 条';
      let ok = false;
      try {
        const login = await api('115_login_status');
        ok = !!login.logged_in;
      } catch (e) { ok = false; }
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
    let submitAt = Number(localStorage.getItem('pygal.submitDoneAt') || 0);
    let busy = false;

    const ACTION_LABEL = {
      sync: '同步', crawl: '获取清单', magnet: '获取磁链', submit: '提交115',
      check: '校对115', organize: '整理115',
    };

    function scope() {
      return {
        start_year: Number($('start-year').value) || 0,
        end_year: Number($('end-year').value) || 0,
        month: Number($('task-month').value) || 0,
      };
    }

    function renderSteps() {
      const running = !!(task && task.running);
      const action = (task && task.action) || '';
      const pendingReview = ov ? Number(ov.pending_review || 0) : 0;
      const issues = ov ? Number(ov.issues || 0) : 0;

      // ① 同步
      let s1 = 'wait', b1 = '待跑', d1 = '清单 + 磁链两阶段';
      if (running && ['sync', 'crawl', 'magnet'].includes(action)) { s1 = 'run'; b1 = '运行中'; d1 = task.current || '准备中'; }
      else if (task && ['sync', 'crawl', 'magnet'].includes(action) && !running && task.outcome !== 'error') { s1 = 'done'; b1 = '完成'; d1 = task.message || '已跑完'; }
      else if (task && task.outcome === 'error' && ['sync', 'crawl', 'magnet'].includes(action)) { s1 = 'block'; b1 = '失败'; d1 = task.message || ''; }

      // ② DSH 审核
      let s2 = 'wait', b2 = '待跑', d2 = '无待审';
      if (pendingReview > 0) { s2 = 'block'; b2 = '阻断'; d2 = `<b>${pendingReview}</b> 条低分磁链待确认`; }
      else { s2 = 'done'; b2 = '清空'; d2 = '无待审记录'; }

      // ③ 提交并校对
      const left = submitAt ? Math.max(0, 600 - Math.floor((Date.now() - submitAt) / 1000)) : 0;
      let s3 = 'wait', b3 = '待跑', d3 = ov ? `${ov.pending_submit} 条待提交 · 提交后等 10 分钟` : '';
      if (running && ['submit', 'check'].includes(action)) { s3 = 'run'; b3 = '运行中'; d3 = task.current || '准备中'; }
      else if (submitAt && left > 0) { s3 = 'run'; b3 = '等待校对'; d3 = `已提交，${fmtSeconds(left)} 后自动校对`; }
      else if (task && ['submit', 'check'].includes(action) && !running && task.outcome !== 'error') { s3 = 'done'; b3 = '完成'; d3 = task.message || ''; }

      // ④ 最终核查
      let s4 = 'wait', b4 = '待跑', d4 = '整理 issue / 旧库残留';
      if (issues > 0) { s4 = 'block'; b4 = '有积压'; d4 = `<b>${issues}</b> 条整理 issue 待 DSH 处理`; }
      else { s4 = 'done'; b4 = '清空'; d4 = '无整理积压'; }

      $('steps').innerHTML = [
        [s1, '1', '同步', b1, d1],
        [s2, '2', 'DSH 审核', b2, d2],
        [s3, '3', '提交并校对', b3, d3],
        [s4, '4', '最终核查', b4, d4],
      ].map(([st, n, t, badge, desc]) => {
        const badgeCls = { done: 'b-ok', run: 'b-run', block: 'b-warn', wait: 'b-wait' }[st] || 'b-wait';
        const num = st === 'done' ? '✓' : n;
        return `<div class="step ${st}"><div class="step-top"><span class="step-n">${num}</span></div>`
          + `<div class="step-t">${t} <span class="step-badge ${badgeCls}">${badge}</span></div>`
          + `<div class="step-d">${desc}</div></div>`;
      }).join('');

      $('pipeline-updated').textContent = task && task.updated_at ? `${task.updated_at} 更新` : '';
    }

    function renderGate(pending) {
      const gate = $('gate');
      const n = Number(pending || 0);
      if (n > 0) {
        gate.hidden = false;
        gate.innerHTML = `⚠ <div>第 2 步有 <b>${n} 条低分磁链</b>待 DSH 审核。点「提交并校对」时按所选年份范围检查，范围内有待审才锁定。`
          + `<span class="muted">（egs review_detail / review_decide）</span></div>`;
      } else {
        gate.hidden = true;
        gate.innerHTML = '';
      }
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
        .map((g) => `<details class="result-group"><summary><span>${esc(g.type)}</span><span class="pill ${badge[g.outcome] || 'p-mute'}">${g.rows.length}</span></summary>`
          + `<div class="rg-b">${g.rows.map((r) => `<div class="result-row"><strong>${esc(r.name)}</strong>：${esc(r.message)}`
            + (r.detail && r.detail.target_path ? `<div class="rr-t">${esc(r.detail.old_path || '')} → ${esc(r.detail.target_path)}</div>` : '')
            + `</div>`).join('')}</div></details>`)
        .join('');
    }

    function renderTask() {
      const running = !!(task && task.running);
      $('btn-sync').disabled = busy || running || !ov;
      const left = submitAt ? Math.max(0, 600 - Math.floor((Date.now() - submitAt) / 1000)) : 0;
      $('btn-submit').disabled = busy || running || !ov || left > 0;
      $('btn-stop').hidden = !running;
      $('btn-stop').disabled = false;

      $('submit-sub').textContent = left > 0 ? `等待 ${fmtSeconds(left)}` : (ov ? `${ov.pending_submit} 条` : '');

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
        $('k-done').textContent = `${done} / ${total}`;
        $('k-success').textContent = Number(task.success || 0);
        $('k-failed').textContent = Number(task.failed || 0);
        $('k-skipped').textContent = Number(task.skipped || 0);
        $('k-current').textContent = task.current || '—';
        $('task-results').innerHTML = renderTaskResults(task.results);
      } else {
        $('bar').style.width = '0%';
        $('k-done').textContent = '0 / 0';
        $('k-success').textContent = '0';
        $('k-failed').textContent = '0';
        $('k-skipped').textContent = '0';
        $('k-current').textContent = '—';
        $('task-results').innerHTML = '';
      }
      renderSteps();
      renderGate(ov ? ov.pending_review : 0);
    }

    async function poll() {
      try {
        const t = await api('pipeline_status');
        const wasRunning = prevRunning;
        task = t;
        prevRunning = !!t.running;
        // 提交刚完成：记录时间点，10 分钟后自动校对
        if (wasRunning && !t.running && t.action === 'submit' && t.outcome !== 'error' && !submitAt) {
          submitAt = Date.now();
          localStorage.setItem('pygal.submitDoneAt', String(submitAt));
          $('control-msg').textContent = '提交完成，10 分钟后自动开始校对。';
        }
        renderTask();
      } catch (e) {
        $('control-msg').textContent = `状态读取失败：${e.message}`;
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
        if (action === 'submit') {
          const pf = await api(`pipeline_preflight&pipeline_action=submit&start_year=${sc.start_year}&end_year=${sc.end_year}&month=${sc.month}`);
          if (Number(pf.count || 0) > 0) {
            renderGate(pf.count);
            $('control-msg').textContent = `当前范围还有 ${pf.count} 条低分磁链待 DSH 审核，提交已锁定。`;
            return;
          }
        }
        const res = await api('pipeline_start', { action, start_year: sc.start_year, end_year: sc.end_year, month: sc.month, execute: false });
        $('control-msg').textContent = res.message || '已启动';
        if (action === 'submit') {
          submitAt = 0;
          localStorage.removeItem('pygal.submitDoneAt');
        }
        await poll();
      } catch (e) {
        $('control-msg').textContent = e.message;
        if (e.payload && e.payload.pending_review_count) renderGate(e.payload.pending_review_count);
      } finally {
        busy = false;
        renderTask();
      }
    }

    async function triggerCheck() {
      const sc = scope();
      try {
        await api('pipeline_start', { action: 'check', start_year: sc.start_year, end_year: sc.end_year, month: sc.month, execute: false });
        submitAt = 0;
        localStorage.removeItem('pygal.submitDoneAt');
        $('control-msg').textContent = '10 分钟已到，开始校对 115 下载结果。';
        await poll();
      } catch (e) {
        $('control-msg').textContent = `自动校对未启动：${e.message}`;
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
      if (submitAt) {
        const left = Math.max(0, 600 - Math.floor((Date.now() - submitAt) / 1000));
        if (left > 0) renderTask();
        else triggerCheck();
      }
    }, 1000);
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

    function cover(r) {
      const gid = r.getchu_id ? String(r.getchu_id) : '';
      const did = r.dlsite_id ? String(r.dlsite_id) : '';
      if (!gid && !did) return '<div class="cov ph">无封面</div>';
      const primary = gid
        ? `${basePath}/thumbnails/web/${esc(gid)}.webp`
        : `${basePath}/thumbnails/web/dlsite/${esc(did)}.webp`;
      const fb = gid
        ? `${basePath}/thumbnails/${esc(gid)}.jpg`
        : `${basePath}/thumbnails/dlsite/${esc(did)}.jpg`;
      const onerr = fb
        ? `onerror="if(this.dataset.fb){this.src=this.dataset.fb;delete this.dataset.fb}else{this.parentNode.classList.add('ph');this.parentNode.textContent='无封面'}"`
        : `onerror="this.parentNode.classList.add('ph');this.parentNode.textContent='无封面'"`;
      return `<div class="cov"><img loading="lazy" alt="" src="${primary}"${fb ? ` data-fb="${esc(fb)}"` : ''} ${onerr}></div>`;
    }

    function render(data) {
      total = Number(data.total || 0);
      const rows = data.data || [];
      $('cards').innerHTML = rows.length ? rows.map((r) => {
        const [stage, label, cls] = stageOf(r);
        const sub = [r.company, r.date].filter(Boolean).map(esc).join(' · ');
        const note = r.comment ? `<div class="note">${esc(r.comment)}</div>` : '';
        const coverUrl = r.getchu_id
          ? `${basePath}/thumbnails/web/${esc(String(r.getchu_id))}.webp`
          : (r.dlsite_id ? `${basePath}/thumbnails/web/dlsite/${esc(String(r.dlsite_id))}.webp` : '');
        const open = coverUrl ? ` data-cover="${esc(coverUrl)}"` : '';
        return `<div class="game" data-stage="${stage}"${open}>`
          + cover(r)
          + `<div class="meta"><div class="t">${esc(r.name)}</div>`
          + `<div class="s">${sub}</div>`
          + `<div class="badges"><span class="pill ${cls}">${label}</span></div>${note}</div></div>`;
      }).join('') : '<div class="empty">没有符合条件的记录</div>';

      const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      $('page-info').textContent = `第 ${page} / ${pages} 页 · 共 ${total.toLocaleString('zh-CN')} 条`;
      $('prev').disabled = page <= 1;
      $('next').disabled = page >= pages;
      $('list-meta').textContent = `只读 · 共 ${total.toLocaleString('zh-CN')} 条`;

      $('cards').querySelectorAll('[data-cover]').forEach((el) => {
        el.addEventListener('click', (ev) => {
          const url = el.getAttribute('data-cover');
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
      $('cards').innerHTML = '<div class="empty">加载中…</div>';
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
    $('prev').addEventListener('click', () => { if (page > 1) { page--; load(); } });
    $('next').addEventListener('click', () => { page++; load(); });
    $('lightbox').addEventListener('click', (e) => {
      if (e.target.id === 'lightbox' || e.target.classList.contains('lb-close')) $('lightbox').classList.remove('on');
    });

    loadYears();
    load();
    loadTop().catch(() => {});
  }

  window.pyGal = { initDashboard, initData, api };
})();
