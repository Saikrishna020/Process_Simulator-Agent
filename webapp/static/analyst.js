'use strict';
const analystUI = {model: null, catalog: null, loading: null, busy: false, job: null, previous: null, history: []};
const analystLabel = value => value.replaceAll('_', ' ');
function analystStatus(message, error = false) {
  $('analyst-status').hidden = !message;
  $('analyst-status').textContent = message;
  $('analyst-status').classList.toggle('inline-warning', error);
}
function analystBusy(busy) {
  analystUI.busy = busy;
  document.querySelectorAll('#analyst-question-form button, #analyst-suggestions button').forEach(b => b.disabled = busy);
}
async function loadAnalyst() {
  const model = state.model?.model_id;
  if (!model || analystUI.model === model || analystUI.loading === model) return;
  analystUI.loading = model;
  analystUI.previous = null;
  $('analyst-result').hidden = true;
  $('analyst-summary').textContent = 'Reading the local data catalog...';
  analystBusy(true);
  try {
    const catalog = await api('/models/' + model + '/analyst');
    if (state.model?.model_id !== model) return;
    analystUI.model = model; analystUI.catalog = catalog; analystUI.history = catalog.history;
    const wait = catalog.customer_wait;
    $('analyst-summary').innerHTML = `<p>${fmt(catalog.case_count,0)} cases · ${fmt(catalog.event_count,0)} work-item events · ${fmt(catalog.outcome_coverage,0)} cases matched to full-log outcomes.</p>` +
      (wait ? `<div class="analyst-finding"><span class="eyebrow">OBSERVED CUSTOMER-RESPONSE INTERVAL</span><strong>${wait.median_hours === null ? 'No matched responses' : fmt(wait.median_hours) + ' hours'}</strong><p>Median across ${fmt(wait.observed,0)} cases with matched sent/returned offers. ${fmt(wait.missing,0)} cases have no measured response and are excluded. Overlapping intervals count once. This measures elapsed time, not its cause.</p></div>` : '<p class="inline-warning">Full-log outcomes are not available for this dataset. Task and resource analyses still work.</p>');
    $('analyst-suggestions').innerHTML = catalog.presets.map((p,i) => `<button class="secondary" type="button" data-analysis-preset="${i}">${esc(p.question)} <span aria-hidden="true">↗</span></button>`).join('');
    analystHistory(); analystStatus();
    const pending = catalog.history.find(j => j.status === 'running');
    if (pending) { analystUI.job = pending.id; analystPoll(pending.id, model); }
    else analystBusy(false);
  } catch (error) {
    $('analyst-summary').textContent = 'The data catalog could not load.';
    analystStatus(error.message, true);
    $('analyst-summary').appendChild(Object.assign(document.createElement('button'), {textContent:'Retry data catalog', className:'secondary', onclick:() => loadAnalyst()}));
    analystBusy(false);
  } finally { analystUI.loading = null; }
}
async function analystSubmit(question, plan = null) {
  if (analystUI.busy || !analystUI.catalog) return;
  const model = state.model.model_id;
  analystBusy(true); analystStatus(plan ? 'Calculating from local records...' : 'Planning the question, then calculating locally...');
  try {
    const job = await api('/analyses', {method:'POST', body:JSON.stringify({model_id:model, question, plan, previous_plan:plan ? null : analystUI.previous})});
    analystUI.job = job.id;
    await analystPoll(job.id, model);
  } catch (error) { analystBusy(false); analystStatus(error.message, true); }
}
async function analystPoll(id, model) {
  analystBusy(true);
  try {
    let job;
    const deadline = Date.now() + 150000;
    do {
      job = await api('/analyses/' + id);
      if (state.model?.model_id !== model) return;
      if (job.status !== 'running') break;
      if (Date.now() > deadline) throw new Error('Analysis is taking longer than expected. Reconnect to check its status.');
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (true);
    analystUI.history = [job, ...analystUI.history.filter(j => j.id !== id)].slice(0,20);
    analystHistory();
    if (job.status === 'done') { analystStatus(); analystResult(job); }
    else analystStatus(job.error, true);
    analystUI.job = null;
  } catch (error) {
    analystStatus(error.message, true);
    const retry = Object.assign(document.createElement('button'), {textContent:'Reconnect',className:'secondary',onclick:() => analystPoll(id,model)});
    $('analyst-status').appendChild(retry);
  } finally { if (state.model?.model_id === model) analystBusy(false); }
}
function analystChart(result) {
  const rows = result.rows.filter(r => r.value !== null);
  if (!rows.length || result.plan.chart === 'table') return '';
  const max = Math.max(1, ...rows.map(r => r.value));
  if (result.plan.chart === 'line' && !result.plan.split_by) {
    const x = i => 55 + i * 680 / Math.max(1, rows.length-1), y = v => 190-v/max*160;
    const points = rows.map((r,i) => `${x(i)},${y(r.value)}`).join(' ');
    return `<svg class="analyst-line" viewBox="0 0 780 240" role="img" aria-label="${esc(result.plan.aggregation+' '+result.plan.metric+' by month')}"><text x="8" y="30">${fmt(max)}</text><text x="20" y="195">0</text><path d="M55 25V190H750" fill="none" stroke="#adbdb5"/><polyline points="${points}" fill="none" stroke="#207a59" stroke-width="3"/>${rows.map((r,i) => `<circle cx="${x(i)}" cy="${y(r.value)}" r="4" fill="#207a59"><title>${esc(r.label)}: ${fmt(r.value)} ${esc(result.unit)}</title></circle>${i % Math.max(1,Math.ceil(rows.length/6)) === 0 ? `<text x="${x(i)}" y="215" text-anchor="middle">${esc(r.label)}</text>` : ''}`).join('')}</svg>`;
  }
  return `<div class="analyst-bars" role="img" aria-label="Bar chart; exact values are in the table below">${rows.map(r => `<div class="analyst-bar"><span>${esc(r.label)}</span><div class="track"><i class="fill" style="width:${r.value/max*100}%"></i></div><strong>${fmt(r.value)} ${esc(result.unit)}</strong></div>`).join('')}</div>`;
}
function analystResult(job) {
  const r = job.result;
  analystUI.previous = r.plan;
  $('analyst-context').textContent = 'Follow-up questions use this displayed query. Choose “Start a fresh question” to clear that context. No records or calculated results are sent to the language planner.';
  $('analyst-result').hidden = false;
  const stale = analystUI.catalog?.fingerprint !== r.fingerprint;
  $('analyst-result').innerHTML = `<p class="eyebrow">ANALYSIS · ${esc(r.dataset)}</p><h2>${esc(job.question)}</h2>${stale ? '<p class="inline-warning">Saved result from an older data snapshot. Run the question again for current data.</p>' : ''}<p>${esc(r.headline)}</p>${analystChart(r)}
    <p>${esc(r.explanation)}</p><div class="table-wrap"><table><thead><tr><th>Group</th><th>${esc(r.plan.aggregation)} ${esc(analystLabel(r.plan.metric))} (${esc(r.unit)})</th><th>Records</th><th>Observed</th><th>Missing</th><th>Share of matching records</th>${r.plan.split_by ? '<th>Share within primary group</th>' : ''}</tr></thead><tbody>${r.rows.map(row => `<tr><td>${esc(row.label)}</td><td>${row.value === null ? 'Not observed' : fmt(row.value,2)}</td><td>${fmt(row.records,0)}</td><td>${fmt(row.observed,0)}</td><td>${fmt(row.missing,0)}</td><td>${fmt(row.share_pct)}%</td>${r.plan.split_by ? `<td>${fmt(row.within_group_pct)}%</td>` : ''}</tr>`).join('') || '<tr><td colspan="7">No records match these filters. Try a broader population.</td></tr>'}</tbody></table></div>
    <ul>${r.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul><p><strong>Next investigation:</strong> ${esc(r.next_step)}</p>
    <div class="actions"><button class="secondary" id="analyst-scenario">Test a hypothesis in Scenario builder</button><button class="secondary" id="analyst-export">Download result JSON</button><button class="secondary" id="analyst-csv">Download table CSV</button></div>
    <details><summary>Query, definitions and source identity</summary><p>${fmt(r.matching_records,0)} of ${fmt(r.total_records,0)} ${esc(r.plan.table)}; ${r.group_count} groups. Whole-log descriptive analysis, not a training-only estimate.</p><pre>${esc(JSON.stringify(r.plan,null,2))}</pre><dl>${Object.entries(r.definitions).map(([k,v]) => `<dt>${esc(analystLabel(k))}</dt><dd>${esc(v)}</dd>`).join('')}</dl><p class="analyst-hash">Source fingerprints: ${esc(r.fingerprint)}</p></details>`;
  $('analyst-scenario').onclick = () => navigate('scenario');
  $('analyst-export').onclick = () => analystDownload(JSON.stringify(job,null,2), 'application/json', `analysis-${job.id}.json`);
  $('analyst-csv').onclick = () => {
    const quote = value => '"' + String(value ?? '').replace(/^[=+@-]/, "'$&").replaceAll('"','""') + '"';
    const keys = ['label','value','records','observed','missing','share_pct','within_group_pct'];
    analystDownload([keys.join(','), ...r.rows.map(row => keys.map(k => quote(row[k])).join(','))].join('\r\n'), 'text/csv', `analysis-${job.id}.csv`);
  };
}
function analystDownload(content, type, name) {
  const url = URL.createObjectURL(new Blob([content], {type})); const link = document.createElement('a');
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
function analystHistory() {
  $('analyst-history').innerHTML = analystUI.history.map(j => `<button class="secondary" type="button" data-analysis-history="${esc(j.id)}">${esc(j.question)} · ${esc(j.status)}</button>`).join('') || '<p class="subtle">Your questions and results will be saved here.</p>';
}
$('analyst-question-form').onsubmit = event => { event.preventDefault(); analystSubmit($('analyst-question').value.trim()); };
$('analyst-suggestions').onclick = event => { const b = event.target.closest('[data-analysis-preset]'); if (b) { const p = analystUI.catalog.presets[Number(b.dataset.analysisPreset)]; $('analyst-question').value = p.question; analystSubmit(p.question,p.plan); } };
$('analyst-history').onclick = event => { const b = event.target.closest('[data-analysis-history]'); if (!b) return; const job = analystUI.history.find(j => j.id === b.dataset.analysisHistory); if (job?.status === 'done') analystResult(job); else analystStatus(job?.error || 'Analysis is still running.'); };
$('analyst-fresh').onclick = () => { analystUI.previous = null; $('analyst-question').value = ''; $('analyst-context').textContent = 'New question: no previous query context. Only the question and field catalog are sent to the language planner.'; $('analyst-question').focus(); };
