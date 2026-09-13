(() => {
    const $ = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const labels = {crawl:'获取游戏清单', magnet:'获取下载用磁链', check:'校对115', submit:'提交115', organize:'整理115'};
    const resultTypeLabels = {
        success:'成功', failed:'失败/待处理', skipped:'跳过',
        already_ok:'已符合规范', found_set_downloaded:'已确认下载',
        renamed:'已重命名', moved:'已移动', renamed_moved:'已重命名并移动',
        wrapped_file:'已归入文件夹', would_rename:'预览：重命名',
        would_move:'预览：移动', would_rename_moved:'预览：重命名并移动',
        would_wrap_file:'预览：归入文件夹', would_set_downloaded:'预览：补记下载',
        cross_year_confirm:'待确认跨年', month_shift_confirm:'待确认搬月',
        conflict:'名称冲突', missing_in_115:'115 未找到', in_offline:'等待离线下载',
        not_downloaded:'尚未下载', no_link:'无磁链', no_dn_date:'磁链缺日期',
        duplicate_magnet:'共链重复', not_submittable:'已排除', error:'错误'
    };
    let currentTask = null;
    let loading = false;
    let launching = false;
    let ready = false;
    let calendarRequest = 0;
    let lastRefresh = 0;
    let crossYearRows = [];
    let crossYearBatchRunning = false;
    const crossYearExcludedIds = new Set();
    let confirmationLoadedJob = '';
    let resultGroupJob = '';
    const resultGroupOpen = new Map();
    const params = new URLSearchParams(location.search);
    const year = Number(params.get('year')) || new Date().getFullYear();
    $('start-year').value = $('end-year').value = $('calendar-year').value = year;
    const month = Number(params.get('month'));
    if (month >= 1 && month <= 12) $('task-month').value = month;

    async function api(action, body) {
        const response = await fetch(`${basePath}/tool/api.php?action=${action}`, body ? {
            method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
        } : {});
        if (!response.ok) throw new Error(`请求失败 (${response.status})`);
        const result = await response.json();
        if (result.status === 'error') throw new Error(result.message || '操作失败');
        return result;
    }
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const isPendingMoveError = error => {
        const message = String(error?.message || error || '');
        return message.includes('990009') || message.includes('尚未执行完成');
    };
    async function confirmCrossYearRow(row) {
        const d = row.detail || {};
        const date = row.date || d.date || '';
        const name = row.name || '';
        if (!date || !name) throw new Error('待办缺少日期或游戏名');
        try {
            return await api('egs_organize_confirm', {date, name});
        } catch (error) {
            if (!isPendingMoveError(error)) throw error;
            await wait(2000);
            return await api('egs_organize_confirm', {date, name});
        }
    }
    function buttons() {
        document.querySelectorAll('[data-action]').forEach(button => {button.disabled = !ready || launching || !!currentTask?.running;});
    }
    function renderCrossYearRows() {
        $('cross-year-body').innerHTML = crossYearRows.map((row, idx) => {
            const d = row.detail || {};
            const checked = !crossYearExcludedIds.has(Number(row.id));
            const actualDate = d.proposed_actual_release_ts || d.dn_date || '';
            const kindText = d.confirmation_kind === 'month_shift'
                ? `EGS ${escape((d.egs_date || d.release_ts || '').slice(0, 7) || '-')} → 实际 ${escape(actualDate || '-')}`
                : `${escape(d.source_year || '')} → ${escape(d.target_year || '')}`;
            return `
            <tr data-cross-idx="${idx}">
                <td class="text-center align-middle">
                    <input class="form-check-input cross-year-select" type="checkbox" data-id="${escape(row.id)}" ${checked ? 'checked' : ''} ${crossYearBatchRunning ? 'disabled' : ''} aria-label="选择 ${escape(row.name || '待确认项')}">
                </td>
                <td class="small pending-move-info">
                    <div class="fw-semibold">${escape(kindText)}</div>
                    <div class="text-muted path-arrow">${escape(d.old_path || d.old_name || '')}</div>
                    <div class="text-muted path-arrow">${escape(d.target_path || d.target_name || '')}</div>
                    ${row.batchError ? `<div class="text-danger mt-1">批量处理失败：${escape(row.batchError)}</div>` : ''}
                </td>
                <td class="text-end align-middle pending-move-actions">
                    <div class="d-grid gap-1">
                        <button type="button" class="btn btn-outline-warning btn-sm confirm-cross-year-btn" ${crossYearBatchRunning ? 'disabled' : ''} data-id="${escape(row.id)}" data-hidden-date="${escape(row.date || d.date || '')}" data-hidden-name="${escape(row.name || '')}" data-label="${row.status === 'month_shift_confirm' ? '批准搬月' : '批准跨年移动'}">
                            ${row.status === 'month_shift_confirm' ? '批准搬月' : '批准跨年移动'}</button>
                        <button type="button" class="btn btn-outline-danger btn-sm reject-cross-year-btn" ${crossYearBatchRunning ? 'disabled' : ''} data-id="${escape(row.id)}" data-kind="${escape(row.status || '')}">驳回</button>
                    </div>
                </td>
            </tr>`;
        }).join('') || '<tr><td colspan="3" class="text-center text-muted py-3">暂无待确认项</td></tr>';
        const selectedCount = crossYearRows.filter(row => !crossYearExcludedIds.has(Number(row.id))).length;
        const selectAll = $('cross-year-select-all');
        if (selectAll) {
            selectAll.disabled = crossYearBatchRunning || !crossYearRows.length;
            selectAll.checked = !!crossYearRows.length && selectedCount === crossYearRows.length;
            selectAll.indeterminate = selectedCount > 0 && selectedCount < crossYearRows.length;
        }
        const allButton = $('confirm-all-cross-year');
        if (allButton) {
            allButton.disabled = crossYearBatchRunning || selectedCount === 0;
            allButton.textContent = crossYearBatchRunning ? '批量处理中...' : `批量同意已选（${selectedCount}）`;
        }
    }

    function renderTaskResults(results, jobId) {
        if (resultGroupJob !== jobId) {
            resultGroupJob = jobId;
            resultGroupOpen.clear();
        }
        if (!results.length) return '<span class="text-muted">暂无处理结果</span>';
        const groups = new Map();
        results.forEach((row, index) => {
            const type = row.detail?.status || row.outcome || 'other';
            if (!groups.has(type)) groups.set(type, {type, outcome:row.outcome || 'other', rows:[], index});
            groups.get(type).rows.push(row);
        });
        const priority = {failed:0, skipped:1, success:2, other:3};
        return [...groups.values()].sort((a, b) =>
            (priority[a.outcome] ?? 3) - (priority[b.outcome] ?? 3) || a.index - b.index
        ).map(group => {
            const badge = group.outcome === 'failed' ? 'text-bg-danger'
                : group.outcome === 'success' ? 'text-bg-success'
                : group.outcome === 'skipped' ? 'text-bg-secondary' : 'text-bg-light';
            const rows = group.rows.map(row => {
                const detail = row.detail;
                const target = detail?.target_path
                    ? `<div class="text-muted">${escape(detail.old_path || '待定位')} → ${escape(detail.target_path)}</div>` : '';
                const shared = Array.isArray(detail?.shared_with) ? detail.shared_with : [];
                const sharedHtml = shared.length
                    ? `<div class="small text-danger mt-1">关联的 115 目录记录：${shared.map(s => {
                        const label = `${escape(s.date || '')}/${escape(s.name || '')}`;
                        const path = s.folder_path || s.folder_name || '';
                        const sameEgs = Number(s.egs_id) === Number(detail.egs_id);
                        return `${sameEgs ? '[同一 EGS 记录] ' : ''}${label}${path ? `（${escape(path)}）` : ''}`;
                    }).join('；')}</div>`
                    : '';
                return `<div class="border-bottom py-2"><strong>${escape(row.name)}</strong>：${escape(row.message)}${target}${sharedHtml}</div>`;
            }).join('');
            const isOpen = resultGroupOpen.has(group.type)
                ? resultGroupOpen.get(group.type) : group.outcome === 'failed';
            return `<details class="task-result-group border rounded mb-2" data-result-type="${escape(group.type)}"${isOpen ? ' open' : ''}>
                <summary class="px-2 py-2"><span>${escape(resultTypeLabels[group.type] || group.type)}</span>
                    <span class="badge ${badge}">${group.rows.length}</span></summary>
                <div class="px-2">${rows}</div></details>`;
        }).join('');
    }

    function showTask(task) {
        currentTask = task;
        buttons();
        $('task-card').hidden = !task.job_id;
        if (!task.job_id) return;
        $('task-title').textContent = `${labels[task.action] || task.label} · ${task.start_year}–${task.end_year} · ${task.month ? task.month+'月' : '全年'}${task.action === 'organize' && task.execute ? ' · 执行' : ''}`;
        $('task-message').textContent = task.running ? (task.current || '准备中…') : task.message;
        $('stop-task').hidden = !task.running;
        const percent = task.total ? Math.round(task.done / task.total * 100) : 0;
        $('task-progress').style.width = percent + '%';
        $('task-progress').setAttribute('aria-valuenow', percent);
        $('task-counts').innerHTML = `已处理 ${task.done || 0}/${task.total || 0} · 成功 ${task.success || 0} · 待处理/失败 ${task.failed || 0} · 跳过 ${task.skipped || 0}`
            + (task.action === 'organize' && !task.running && (task.failed || 0) > 0
                ? ` · <a href="${basePath}/tool/organize_review.php">打开整理待办 →</a>` : '');
        const taskDetails = $('task-details');
        taskDetails.innerHTML = renderTaskResults(task.results || [], task.job_id);
        taskDetails.querySelectorAll('.task-result-group').forEach(group => {
            group.addEventListener('toggle', () => resultGroupOpen.set(group.dataset.resultType, group.open));
        });

    }

    async function loadOpenConfirmations(task) {
        if (task.action !== 'organize' || task.running || confirmationLoadedJob === task.job_id) return;
        confirmationLoadedJob = task.job_id;
        try {
            const data = await api('organize_issues');
            const startYear = Number(task.start_year || 0), endYear = Number(task.end_year || 0);
            const taskMonth = Number(task.month || 0);
            crossYearExcludedIds.clear();
            crossYearRows = (data.open || []).filter(row => {
                if (!['month_shift_confirm', 'cross_year_confirm'].includes(row.status)) return false;
                const [rowYear, rowMonth] = String(row.date || '').split('-').map(Number);
                if ((startYear && rowYear < startYear) || (endYear && rowYear > endYear)) return false;
                return !taskMonth || rowMonth === taskMonth;
            });
            renderCrossYearRows();
            if (crossYearRows.length) bootstrap.Modal.getOrCreateInstance($('crossYearModal')).show();
        } catch (error) {
            confirmationLoadedJob = '';
            console.error('读取整理待办失败', error);
        }
    }

    async function poll() {
        if (loading) return;
        loading = true;
        try {
            const previous = currentTask;
            const task = await api('pipeline_status');
            ready = true;
            showTask(task);
            await loadOpenConfirmations(task);
            if ((previous?.running && !task.running) || (task.running && Date.now() - lastRefresh > 15000)) await loadCalendar();
        } catch (error) {
            ready = false;
            buttons();
            $('control-message').textContent = `状态读取失败：${error.message}`;
        } finally { loading = false; }
    }
    async function start(action) {
        const form = $('scope-form');
        if (!form.reportValidity()) return;
        const startYear = Number($('start-year').value), endYear = Number($('end-year').value);
        const month = Number($('task-month').value);
        if (endYear < startYear) { $('control-message').textContent = '结束年份不能小于起始年份'; return; }
        if (action === 'organize') {
            const scopeLabel = month ? `${startYear}年${month}月`
                : (startYear === endYear ? `${startYear}年` : `${startYear}–${endYear}年`);
            if (!confirm(`整理将实际移动/重命名 115 目录，范围：${scopeLabel}。确认执行？`)) return;
        }
        launching = true;
        buttons();
        try {
            if (action === 'check') {
                const preflight = await api(`pipeline_preflight&pipeline_action=check&start_year=${startYear}&end_year=${endYear}&month=${month}`);
                if (Number(preflight.count || 0) > 0) {
                    $('pending-review-count').textContent = preflight.count;
                    const reviewParams = new URLSearchParams({review: 'pending'});
                    const reviewYear = Number(preflight.review_year);
                    if (Number.isInteger(reviewYear) && reviewYear >= 1980 && reviewYear <= 3000) {
                        reviewParams.set('year', String(reviewYear));
                    } else if (startYear === endYear) {
                        reviewParams.set('year', String(startYear));
                    }
                    if (month >= 1 && month <= 12) {
                        reviewParams.set('month', String(month));
                    }
                    $('pending-review-link').href = `${basePath}/tool/egs.php?${reviewParams.toString()}`;
                    bootstrap.Modal.getOrCreateInstance($('pendingReviewModal')).show();
                    return;
                }
            }
            const response = await api('pipeline_start', {action, start_year:startYear, end_year:endYear,
                month:month, execute:action === 'organize'});
            $('control-message').textContent = response.message;
            $('stop-task').disabled = false;
            await poll();
        } catch (error) { $('control-message').textContent = error.message; }
        finally { launching = false; buttons(); }
    }
    function monthCell(y, m) {
        const cls = !m.has_data ? 'table-light text-muted' : m.all_magnet_downloaded ? 'table-success' : m.all_magnet_submitted ? 'table-primary' : 'table-warning';
        return `<td class="${cls}"><button type="button" class="month-select" data-scope-year="${y}" data-scope-month="${m.month}" aria-label="选择 ${y} 年 ${m.month} 月为任务范围" title="选择当月任务范围">${m.month}月</button><a class="month-link" href="${basePath}/tool/egs.php?year=${y}&month=${m.month}"><div class="counts">作品 ${m.total} · 磁链 ${m.magnet_total}</div><div class="counts">已提交 ${m.magnet_submitted} · 已下载 ${m.magnet_downloaded}</div></a></td>`;
    }
    async function loadCalendar() {
        const year = Number($('calendar-year').value);
        if (!$('calendar-year').reportValidity()) return;
        const request = ++calendarRequest;
        try {
            const response = await api(`calendar&year=${year}`);
            if (request !== calendarRequest) return;
            if (!Array.isArray(response.years)) throw new Error('年历数据格式错误');
            $('calendar-body').innerHTML = response.years.map(y => `<tr><th rowspan="2" class="align-middle"><button class="btn btn-link" data-scope-year="${y.year}" data-scope-month="0">${y.year}</button></th>${y.months.slice(0,6).map(m => monthCell(y.year,m)).join('')}</tr><tr>${y.months.slice(6).map(m => monthCell(y.year,m)).join('')}</tr>`).join('');
            lastRefresh = Date.now();
        } catch (error) {
            if (request === calendarRequest) $('calendar-body').innerHTML = `<tr><td colspan="7">${escape(error.message)}</td></tr>`;
        }
    }
    document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => start(button.dataset.action)));
    $('scope-form').addEventListener('submit', event => event.preventDefault());
    $('stop-task').addEventListener('click', async () => {
        $('stop-task').disabled = true;
        try {
            const response = await api('pipeline_stop', {job_id:currentTask.job_id});
            $('control-message').textContent = response.message;
        } catch (error) { $('control-message').textContent = error.message; $('stop-task').disabled = false; }
    });
    $('calendar-body').addEventListener('click', event => {
        const button = event.target.closest('[data-scope-year]');
        if (!button) return;
        $('start-year').value = $('end-year').value = button.dataset.scopeYear;
        $('task-month').value = button.dataset.scopeMonth;
        $('scope-form').scrollIntoView({behavior:'smooth', block:'center'});
    });
    $('calendar-year').addEventListener('change', loadCalendar);
    $('refresh-calendar').addEventListener('click', loadCalendar);

    $('cross-year-select-all').addEventListener('change', event => {
        const checked = event.target.checked;
        for (const row of crossYearRows) {
            if (checked) crossYearExcludedIds.delete(Number(row.id));
            else crossYearExcludedIds.add(Number(row.id));
        }
        renderCrossYearRows();
    });

    $('cross-year-body').addEventListener('change', event => {
        const checkbox = event.target.closest('.cross-year-select');
        if (!checkbox) return;
        const id = Number(checkbox.dataset.id);
        if (checkbox.checked) crossYearExcludedIds.delete(id); else crossYearExcludedIds.add(id);
        renderCrossYearRows();
    });

    $('cross-year-body').addEventListener('click', async event => {
        const rejectButton = event.target.closest('.reject-cross-year-btn');
        const confirmButton = event.target.closest('.confirm-cross-year-btn');
        if (!rejectButton && !confirmButton) return;

        if (rejectButton) {
            const id = Number(rejectButton.dataset.id);
            if (!id) return;
            if (!confirm('确认驳回该候选？后续整理不会再次提示同一个候选。')) return;
            rejectButton.disabled = true;
            rejectButton.textContent = '驳回中...';
            try {
                const result = await api('organize_issue_reject', {id});
                if (result.success === false) throw new Error(result.message || '驳回失败');
                crossYearExcludedIds.delete(id);
                crossYearRows = crossYearRows.filter(row => Number(row.id) !== id);
                renderCrossYearRows();
                if (!crossYearRows.length) {
                    bootstrap.Modal.getOrCreateInstance($('crossYearModal')).hide();
                }
                await loadCalendar();
            } catch (error) {
                rejectButton.disabled = false;
                rejectButton.textContent = '驳回';
                alert(error.message);
            }
            return;
        }

        const date = confirmButton.dataset.hiddenDate;
        const name = confirmButton.dataset.hiddenName;
        const id = Number(confirmButton.dataset.id);
        const originalLabel = confirmButton.dataset.label || '确认移动';
        if (!date || !name) return;
        confirmButton.disabled = true;
        confirmButton.textContent = '确认中...';
        try {
            const row = crossYearRows.find(item => Number(item.id) === id)
                || {id, date, name, detail: {date}};
            const result = await confirmCrossYearRow(row);
            if (result.success === false || result.status === 'error') throw new Error(result.message || '确认失败');
            crossYearExcludedIds.delete(id);
            crossYearRows = crossYearRows.filter(row => id
                ? Number(row.id) !== id
                : !(row.detail?.date === date && row.name === name));
            renderCrossYearRows();
            if (!crossYearRows.length) {
                bootstrap.Modal.getOrCreateInstance($('crossYearModal')).hide();
            }
            await loadCalendar();
        } catch (error) {
            confirmButton.disabled = false;
            confirmButton.textContent = originalLabel;
            alert(error.message);
        }
    });

    $('confirm-all-cross-year').addEventListener('click', async () => {
        const pending = crossYearRows.filter(row => !crossYearExcludedIds.has(Number(row.id)));
        if (crossYearBatchRunning || !pending.length) return;
        const total = pending.length;
        const excluded = crossYearRows.length - total;
        if (!confirm(`确认依次批准已选的 ${total} 条搬月/跨年移动？未选项目不会处理，失败项会保留。`)) return;
        crossYearBatchRunning = true;
        let succeeded = 0;
        let failed = 0;
        renderCrossYearRows();
        try {
            for (let index = 0; index < pending.length; index += 1) {
                const row = pending[index];
                $('cross-year-batch-status').textContent = `正在处理 ${index + 1}/${total}：${row.name || ''}`;
                try {
                    const result = await confirmCrossYearRow(row);
                    if (result.success === false || result.status === 'error') {
                        throw new Error(result.message || '确认失败');
                    }
                    succeeded += 1;
                    crossYearExcludedIds.delete(Number(row.id));
                    crossYearRows = crossYearRows.filter(item => Number(item.id) !== Number(row.id));
                } catch (error) {
                    failed += 1;
                    const current = crossYearRows.find(item => Number(item.id) === Number(row.id));
                    if (current) current.batchError = error.message || String(error);
                }
                renderCrossYearRows();
                if (index + 1 < pending.length) await wait(750);
            }
            $('cross-year-batch-status').textContent = `批量处理完成：成功 ${succeeded}，失败 ${failed}，未选 ${excluded}`;
            await loadCalendar();
            if (!crossYearRows.length) {
                setTimeout(() => bootstrap.Modal.getOrCreateInstance($('crossYearModal')).hide(), 600);
            }
        } finally {
            crossYearBatchRunning = false;
            renderCrossYearRows();
        }
    });

    buttons();
    loadCalendar();
    poll();
    setInterval(poll, 3000);
})();
