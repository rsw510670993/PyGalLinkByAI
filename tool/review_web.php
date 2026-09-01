<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>审核面板</title>
<style>
* { box-sizing:border-box; }
body { font-family:"Segoe UI","Microsoft YaHei",sans-serif; max-width:1280px; margin:0 auto; padding:20px; background:#f4f6f8; color:#2c3e50; }
h1 { font-size:22px; margin-bottom:4px; }
.sub { color:#7f8c8d; font-size:13px; margin-bottom:16px; }
.tabs { display:flex; gap:6px; margin-bottom:14px; flex-wrap:wrap; }
.tab { padding:8px 16px; background:#fff; border:1px solid #dcdfe6; border-radius:6px 6px 0 0; cursor:pointer; font-size:14px; }
.tab.active { background:#3498db; color:#fff; border-color:#3498db; }
.tab .badge { display:inline-block; min-width:20px; padding:0 6px; border-radius:10px; background:#e74c3c; color:#fff; font-size:11px; margin-left:4px; }
.card { background:#fff; border-radius:8px; padding:16px 18px; margin-bottom:14px; box-shadow:0 1px 3px rgba(0,0,0,.08); }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:8px 6px; border-bottom:1px solid #eee; vertical-align:top; }
th { color:#95a5a6; font-weight:600; font-size:12px; white-space:nowrap; }
.name { word-break:break-all; }
.mono { font-family:Consolas,monospace; font-size:12px; word-break:break-all; color:#16a085; }
.tag { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; white-space:nowrap; }
.tag.shared_cid { background:#fdecea; color:#e74c3c; }
.tag.ambiguous { background:#fef5e7; color:#f39c12; }
.tag.missing_in_115 { background:#fdecea; color:#e74c3c; }
button { padding:6px 12px; border:none; border-radius:4px; font-size:12px; cursor:pointer; white-space:nowrap; }
.btn-merge { background:#27ae60; color:#fff; }
.btn-merge:hover { background:#1e8449; }
.btn-keep { background:#ecf0f1; color:#2c3e50; }
.btn-keep:hover { background:#dfe4e8; }
.btn-ok { background:#3498db; color:#fff; }
.btn-ok:hover { background:#2980b9; }
button:disabled { background:#b0c4d4; cursor:not-allowed; }
input { padding:6px 8px; border:1px solid #dcdfe6; border-radius:4px; font-size:12px; }
.msg { padding:10px 12px; border-radius:4px; margin:10px 0; font-size:13px; display:none; }
.msg.ok { background:#e8f8f0; color:#1e8449; display:block; }
.msg.err { background:#fdecea; color:#c0392b; display:block; }
.loading { color:#7f8c8d; font-size:13px; padding:14px; }
.hint { color:#95a5a6; font-size:12px; margin-top:6px; }
</style>
</head>
<body>
<?php $base = rtrim(dirname($_SERVER['SCRIPT_NAME']), '/'); ?>
<h1>人工审核面板</h1>
<div class="sub">待人工确认项集中处理：去重版次/特典建议 · 115整理待办 · 无日期码行 &nbsp;|&nbsp; <a href="../index.php">← 控制面板</a></div>

<div class="tabs">
    <div class="tab active" data-tab="editions">去重建议 <span class="badge" id="b-editions">-</span></div>
    <div class="tab" data-tab="review115">115整理待办 <span class="badge" id="b-review115">-</span></div>
    <div class="tab" data-tab="dnmismatch">无日期码行 <span class="badge" id="b-dnmismatch">-</span></div>
</div>

<div id="msg" class="msg"></div>

<div id="tab-editions" class="card">
    <table id="tbl-editions"><thead>
        <tr><th>月份</th><th>公司</th><th>行（建议并入→）</th><th>本体行</th><th>置信</th><th>理由</th><th>操作</th></tr>
    </thead><tbody></tbody></table>
    <div class="hint">「合并」将被建议行并入本体行（保留本体，被并行归档可回溯）；「保持独立」记录决定并不再提示。</div>
</div>

<div id="tab-review115" class="card" style="display:none">
    <table id="tbl-review115"><thead>
        <tr><th>月份</th><th>状态</th><th>游戏</th><th>说明 / 建议</th><th>目标路径</th><th>操作</th></tr>
    </thead><tbody></tbody></table>
    <div class="hint">115整理时安全跳过的项：shared_cid=多行引用同一目录（建议合并行后重跑organize）；ambiguous=多候选需人工指定；missing_in_115=需重提磁链。</div>
</div>

<div id="tab-dnmismatch" class="card" style="display:none">
    <table id="tbl-dnmismatch"><thead>
        <tr><th>登记月</th><th>公司</th><th>游戏</th><th>磁链名(dn)</th><th>getchu预定</th><th>设定发布时间</th><th>操作</th></tr>
    </thead><tbody></tbody></table>
    <div class="hint">这些行磁链无日期码（或名字不匹配未自动解析）→ 人工确定发布时间后计入；展示月份会同步移动。</div>
</div>

<script>
const API = 'review_api.php';
function $(id){ return document.getElementById(id); }
function esc(s){ return (s==null?'':String(s)).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showMsg(ok, text){ const m=$('msg'); m.className='msg '+(ok?'ok':'err'); m.textContent=text; setTimeout(()=>{m.className='msg';},6000); }
async function api(action, params){
    const url = new URL(API, location.href);
    url.searchParams.set('action', action);
    for (const k in (params||{})) url.searchParams.set(k, params[k]);
    const opt = (params && params.__post) ? {method:'POST'} : {};
    if (opt.method==='POST'){
        const body = new URLSearchParams();
        for (const k in params){ if(k!=='__post') body.set(k, params[k]); }
        opt.body = body;
    }
    const r = await fetch(url, opt);
    return r.json();
}

async function loadOverview(){
    try {
        const d = await api('overview');
        $('b-editions').textContent = d.editions ?? '-';
        $('b-review115').textContent = d.review115 ?? '-';
        $('b-dnmismatch').textContent = d.dnmismatch ?? '-';
    } catch(e){}
}

async function loadEditions(){
    const tb = $('tbl-editions').querySelector('tbody');
    tb.innerHTML = '<tr><td colspan="7" class="loading">加载中…</td></tr>';
    const d = await api('editions', {year: '2026'});
    tb.innerHTML = '';
    if (!d.items || !d.items.length){ tb.innerHTML = '<tr><td colspan="7" class="loading">✅ 无待审建议</td></tr>'; return; }
    for (const it of d.items){
        const tr = document.createElement('tr');
        const [y, m] = it.date.split('-');
        tr.innerHTML = `<td>${esc(it.date)}</td><td>${esc(it.company||'')}</td>`
            + `<td class="name">${esc(it.name)}</td><td class="name">${esc(it.of)}</td>`
            + `<td>${esc(it.confidence)}</td><td class="name">${esc(it.reason||'')}</td>`
            + `<td><button class="btn-merge" data-y="${y}" data-m="${m}" data-can="${esc(it.of)}" data-mem="${esc(it.name)}">合并</button> `
            + `<button class="btn-keep" data-date="${it.date}" data-name="${esc(it.name)}">保持独立</button></td>`;
        tb.appendChild(tr);
    }
    tb.querySelectorAll('.btn-merge').forEach(b => b.onclick = () => doEditionMerge(b));
    tb.querySelectorAll('.btn-keep').forEach(b => b.onclick = () => doDecide('edition', b.dataset.date, b.dataset.name, 'keep', '保持独立（不再提示）', loadEditions));
}
async function doEditionMerge(btn){
    btn.disabled = true; btn.textContent = '合并中…';
    try {
        const d = await api('edition_merge', {__post:1, year: btn.dataset.y, month: btn.dataset.m,
            canonical: btn.dataset.can, member: btn.dataset.mem});
        if (d.status === 'ok') { showMsg(true, '已合并: ' + btn.dataset.mem + ' → ' + btn.dataset.can); loadEditions(); loadOverview(); }
        else { showMsg(false, d.message || JSON.stringify(d).slice(0,200)); btn.disabled=false; btn.textContent='合并'; }
    } catch(e){ showMsg(false, String(e)); btn.disabled=false; btn.textContent='合并'; }
}

async function loadReview115(){
    const tb = $('tbl-review115').querySelector('tbody');
    tb.innerHTML = '<tr><td colspan="6" class="loading">加载中…</td></tr>';
    const d = await api('review115');
    tb.innerHTML = '';
    if (!d.items || !d.items.length){ tb.innerHTML = '<tr><td colspan="6" class="loading">✅ 无待办</td></tr>'; return; }
    for (const it of d.items){
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${esc(it.date)}</td><td><span class="tag ${esc(it.status)}">${esc(it.status)}</span></td>`
            + `<td class="name">${esc(it.name)}</td><td class="name">${esc(it.advice||it.message||'')}</td>`
            + `<td class="mono">${esc(it.target_path||it.old_path||'')}</td>`
            + `<td><button class="btn-ok" data-date="${it.date}" data-name="${esc(it.name)}">标记已处理</button></td>`;
        tb.appendChild(tr);
    }
    tb.querySelectorAll('.btn-ok').forEach(b => b.onclick = () => doDecide('review115', b.dataset.date, b.dataset.name, 'resolved', '已处理', loadReview115));
}

async function loadDnmismatch(){
    const tb = $('tbl-dnmismatch').querySelector('tbody');
    tb.innerHTML = '<tr><td colspan="7" class="loading">加载中…</td></tr>';
    const d = await api('dnmismatch');
    tb.innerHTML = '';
    if (!d.items || !d.items.length){ tb.innerHTML = '<tr><td colspan="7" class="loading">✅ 无待处理</td></tr>'; return; }
    for (const it of d.items){
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${esc(it.getchu_date)}</td><td>${esc(it.getchu_company||'')}</td>`
            + `<td class="name">${esc(it.getchu_name)}</td><td class="name" style="max-width:260px">${esc(it.dn||'(dn缺失)')}</td>`
            + `<td>${esc(it.release_date||'')}</td>`
            + `<td><input type="date" data-date="${it.getchu_date}" data-name="${esc(it.getchu_name)}"></td>`
            + `<td><button class="btn-ok">计入</button></td>`;
        tb.appendChild(tr);
    }
    tb.querySelectorAll('.btn-ok').forEach(b => b.onclick = async () => {
        const inp = b.closest('tr').querySelector('input');
        const ts = inp.value;
        if (!ts) { showMsg(false, '请先选择日期'); return; }
        b.disabled = true;
        try {
            const d2 = await api('setts', {__post:1, date: inp.dataset.date, name: inp.dataset.name, ts});
            if (d2.status === 'ok') { showMsg(true, '已计入 ' + d2.release_ts + '（展示月 ' + d2.date_moved + '）'); loadDnmismatch(); loadOverview(); }
            else { showMsg(false, d2.message || '失败'); b.disabled=false; }
        } catch(e){ showMsg(false, String(e)); b.disabled=false; }
    });
}

async function doDecide(kind, date, name, decision, okText, reload){
    try {
        const d = await api('decide', {__post:1, kind, date, name, decision});
        if (d.status === 'ok') { showMsg(true, okText + ': ' + name.slice(0,30)); reload(); loadOverview(); }
        else showMsg(false, d.message || '失败');
    } catch(e){ showMsg(false, String(e)); }
}

document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    ['editions','review115','dnmismatch'].forEach(k => {
        $('tab-'+k).style.display = (k === t.dataset.tab) ? '' : 'none';
    });
});

loadOverview(); loadEditions();
</script>
</body>
</html>
