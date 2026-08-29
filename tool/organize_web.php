<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>整理115</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            max-width: 1100px;
            margin: 0 auto;
            padding: 20px;
            background: #f4f6f8;
            color: #2c3e50;
        }
        h1 { font-size: 22px; margin-bottom: 4px; }
        .sub { color: #7f8c8d; font-size: 13px; margin-bottom: 20px; }
        .card {
            background: #fff; border-radius: 8px;
            padding: 18px 20px; margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,.08);
        }
        h2 { font-size: 15px; margin: 0 0 12px; color: #34495e; }
        .row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; align-items: center; }
        input, button {
            padding: 8px 10px; border: 1px solid #dcdfe6;
            border-radius: 4px; font-size: 13px;
        }
        input { width: 110px; }
        input.wide { flex: 1; min-width: 220px; }
        button {
            background: #3498db; color: #fff; border: none;
            cursor: pointer; white-space: nowrap; padding: 8px 16px;
        }
        button:hover { background: #2980b9; }
        button.ghost { background: #ecf0f1; color: #2c3e50; }
        button.ghost:hover { background: #dfe4e8; }
        button.danger { background: #e67e22; }
        button.danger:hover { background: #d35400; }
        button:disabled { background: #b0c4d4; cursor: not-allowed; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #eee; vertical-align: top; }
        th { color: #95a5a6; font-weight: 600; font-size: 12px; white-space: nowrap; }
        .name { word-break: break-all; }
        .arrow { color: #95a5a6; padding: 0 4px; }
        .path { font-family: Consolas, monospace; font-size: 12px; word-break: break-all; color: #16a085; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; white-space: nowrap; }
        .tag.renamed, .tag.already_ok { background: #e8f8f0; color: #27ae60; }
        .tag.would_rename { background: #eaf2fd; color: #2980b9; }
        .tag.not_found, .tag.error, .tag.conflict, .tag.not_dir, .tag.no_date_code { background: #fdecea; color: #e74c3c; }
        .tag.ambiguous { background: #fef5e7; color: #f39c12; }
        .msg { padding: 10px 12px; border-radius: 4px; margin: 10px 0; font-size: 13px; display: none; }
        .msg.ok  { background: #e8f8f0; color: #1e8449; display: block; }
        .msg.err { background: #fdecea; color: #c0392b; display: block; }
        .empty { color: #b2babb; text-align: center; padding: 14px 0; font-size: 13px; }
        .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
        .chip { background: #f0f3f5; border-radius: 12px; padding: 3px 10px; font-size: 12px; }
        .muted { color: #95a5a6; font-size: 12px; }
    </style>
</head>
<body>
    <h1>整理115</h1>
    <div class="sub">根据下载内容文件名定位游戏文件夹，重命名为 [发布年月日][公司]游戏名。修正记录写入DB，之后精确寻址。</div>

    <div class="card">
        <h2>选择范围</h2>
        <div class="row">
            <input id="year" type="number" value="2026" min="2000" max="2100" title="年份">
            <input id="month" type="number" placeholder="月(可空)" min="1" max="12" title="月份，留空=整年">
            <input id="gname" class="wide" placeholder="游戏名（可空=该范围全部已下载游戏）">
            <button class="ghost" onclick="preview()">预览 (dry-run)</button>
            <button class="danger" onclick="runOrganize()">执行重命名</button>
        </div>
        <div class="muted">预览不会修改115。执行前请务必先预览确认。日期码取磁链[YYMMDD]，缺失时用Getchu发售日。</div>
        <div id="msg" class="msg"></div>
    </div>

    <div class="card" id="result-card" style="display:none;">
        <h2>结果</h2>
        <div class="chips" id="chips"></div>
        <table>
            <thead><tr><th>状态</th><th>日期码</th><th>游戏</th><th>当前名 → 目标名</th><th>路径 / 说明</th></tr></thead>
            <tbody id="tb"></tbody>
        </table>
    </div>

<script>
const API = 'download_api.php';
function $(id){ return document.getElementById(id); }
function showMsg(text, ok){
    const m = $('msg');
    m.textContent = text;
    m.className = 'msg ' + (ok ? 'ok' : 'err');
}
function setBusy(b){
    document.querySelectorAll('button').forEach(x => x.disabled = b);
}
const STATUS_TEXT = {
    renamed: '已改名', already_ok: '已符合', would_rename: '将改名',
    not_found: '未找到', ambiguous: '有歧义', not_dir: '非目录',
    conflict: '名称冲突', no_date_code: '无日期码', error: '错误'
};
async function doOrganize(action){
    const year = $('year').value.trim();
    if (!year){ showMsg('请填写年份', false); return; }
    const month = $('month').value.trim();
    const name = $('gname').value.trim();
    if (action === 'organize_run' && !confirm('确定执行真实重命名？此操作会修改115中的文件夹名。')) return;

    setBusy(true);
    showMsg(action === 'organize_preview' ? '预览中…' : '执行中…（范围大时需要一些时间）', true);
    try {
        const params = new URLSearchParams({action, year});
        if (month) params.set('month', month);
        if (name) params.set('name', name);
        const r = await fetch(API + '?' + params.toString(), {method: 'POST'});
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        if (d.status === 'error'){ showMsg(d.message + (d.stderr ? ': ' + d.stderr : ''), false); return; }
        render(d);
        showMsg(`完成：共 ${d.total} 个，` + Object.entries(d.summary||{}).map(([k,v])=>`${STATUS_TEXT[k]||k} ${v}`).join('，'), true);
    } catch(e){ showMsg('请求失败: ' + e.message, false); }
    finally { setBusy(false); }
}
function preview(){ doOrganize('organize_preview'); }
function runOrganize(){ doOrganize('organize_run'); }

function render(d){
    $('result-card').style.display = '';
    const chips = $('chips');
    chips.innerHTML = '';
    Object.entries(d.summary||{}).forEach(([k,v])=>{
        const c = document.createElement('span');
        c.className = 'chip';
        c.textContent = `${STATUS_TEXT[k]||k}: ${v}`;
        chips.appendChild(c);
    });
    $('tb').innerHTML = (d.results||[]).map(r => {
        const nameCell = r.status === 'already_ok'
            ? `<span class="name">${r.target_name}</span>`
            : `<span class="name">${r.old_name ?? '-'}</span><span class="arrow">→</span><span class="name" style="color:#2980b9;">${r.target_name ?? '-'}</span>`;
        const info = r.old_path
            ? `<span class="path">${r.old_path}</span>` + (r.located_by ? ` <span class="muted">(${r.located_by === 'db_record' ? 'DB精确' : '搜索'})</span>` : '')
            : (r.message || '-');
        const extra = r.candidates ? `<br><span class="muted">候选: ${r.candidates.join(' / ')}</span>` : '';
        return `<tr>
            <td><span class="tag ${r.status}">${STATUS_TEXT[r.status]||r.status}</span></td>
            <td class="muted">${r.date_code||'-'}</td>
            <td class="name" style="max-width:260px;">${r.name}</td>
            <td style="max-width:420px;">${nameCell}</td>
            <td>${info}${extra}</td>
        </tr>`;
    }).join('');
}
</script>
</body>
</html>
