'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n, digits = 1) => Number(n).toLocaleString(undefined, {maximumFractionDigits: digits});
const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const state = {model: null, selected: null, changes: {}, clones: [], activities: {}, result: null, job: null, view: 'resources', pendingView: null};
const fields = ['scenario-name', 'horizon', 'repetitions', 'demand', 'sla', 'routing', 'seed'];
const titles = {resources: ['Resource profiles', 'Your process, understood.', 'Explore resource behavior learned from historical work.'], explore: ['Data explorer', 'What the history says.', 'How the work, the waiting and the people look in the historical log, before any simulation.'], scenario: ['Scenario builder', 'What would you change?', 'Build an alternative. Keep the historical baseline intact.'], results: ['Results', 'See what changes.', 'Compare outcomes across paired simulation repetitions.'], history: ['Experiment history', 'A record of your experiments.', 'Return to a result, reuse its settings, or export the event logs.'], assistant: ['Simulation assistant', 'Understand your process.', 'Ask about historical data, resource patterns or a research simulation.']};
function remember(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} }
function recall(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
async function api(path, options = {}) {
  return jsonRequest('/api/workbench' + path, options);
}
async function jsonRequest(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, signal: AbortSignal.timeout(20000), ...options});
  let data;
  try { data = await response.json(); } catch { throw new Error('The server returned an unreadable response. Check that it is still running.'); }
  if (!response.ok) {
    const detail = data.detail;
    const error = new Error(Array.isArray(detail) ? detail.map(d => `${d.loc.slice(1).join('.')}: ${d.msg}`).join('; ') : detail || 'Request failed.');
    error.status = response.status;
    throw error;
  }
  return data;
}
function safe(action) { return (...args) => { try { return Promise.resolve(action(...args)).catch(error => notice(error.message)); } catch(error) { notice(error.message); } }; }
function navigate(view) {
  if (!titles[view]) return;
  state.view = view;
  history.replaceState(null, '', '#' + view);
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== 'view-' + view);
  document.querySelectorAll('[data-view]').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  const [crumb, title, subtitle] = titles[view];
  $('breadcrumb').textContent = crumb; $('page-title').textContent = title; $('page-subtitle').textContent = subtitle;
  // The assistant is a separate tool (the research engine, not the workbench): hide the dataset/
  // run-comparison chrome that only applies to the empirical workbench above it.
  const isAssistant = view === 'assistant';
  document.querySelector('.dataset-strip').hidden = isAssistant;
  $('run-top').hidden = isAssistant;
  $('model-stats').hidden = isAssistant || !state.model;
  $('assistant-context').textContent = state.model ? `Selected dataset: ${state.model.dataset}. Model ${state.model.model_id.slice(0, 8)}.` : 'No model selected. Learn a dataset in Resource profiles to ask data questions.';
  $('empty-state').hidden = isAssistant || !!state.model;
  $('workspace').hidden = isAssistant || !state.model;
  if (view === 'history') safe(renderHistory)();
  if (view === 'explore') renderExplorer();
  if (view === 'assistant') safe(initAssistant)();
}
function saveDraft() {
  if (state.model) remember('process-lab-draft', {model_id: state.model.model_id, changes: state.changes, clones: state.clones, activities: state.activities, fields: Object.fromEntries(fields.map(f => [f, $(f).value]))});
}
function busy(job) {
  state.job = job;
  $('run-top').disabled = !state.model || !!job;
  $('run-scenario').disabled = !state.model || !!job;
  $('learn').disabled = !!job;
  $('dataset').disabled = !!job;
  if (job) { remember('process-lab-job', job.id); $('job-panel').hidden = false; }
}
async function loadModel(id, restore = true) {
  const model = await api('/models/' + encodeURIComponent(id));
  state.model = model;
  state.selected = [...model.resources].sort((a,b) => b.event_count - a.event_count)[0]?.id;
  state.changes = {}; state.clones = []; state.activities = {};
  $('dataset').value = model.dataset;
  $('scenario-name').value = model.dataset.replace('BPIC_', 'BPI ') + ' · staffing scenario';
  const draft = restore && recall('process-lab-draft');
  if (draft && draft.model_id === id) {
    state.changes = draft.changes || {}; state.clones = draft.clones || []; state.activities = draft.activities || {};
    fields.forEach(f => { if (draft.fields?.[f] !== undefined) $(f).value = draft.fields[f]; });
  }
  remember('process-lab-model', id);
  $('empty-state').hidden = true; $('workspace').hidden = false; $('model-stats').hidden = false;
  $('model-label').textContent = 'Model saved · ' + new Date(model.created_at).toLocaleDateString();
  $('model-stats').innerHTML = [
    ['Training cases', fmt(model.training_cases, 0), `${fmt(model.training_events, 0)} historical events`],
    ['Resource profiles', fmt(model.resources.length, 0), 'Individual schedules & capabilities'],
    ['Process activities', fmt(model.activities.length, 0), 'Observed case sequences preserved'],
    ['Held-out cases', fmt(model.test_cases, 0), 'For historical reference']
  ].map(([label, value, help]) => `<div class="stat"><span class="label">${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(help)}</small></div>`).join('');
  $('resource-count').textContent = model.resources.length;
  $('activity-select').innerHTML = model.activities.map(a => `<option value="${esc(a.name)}">${esc(a.name)}</option>`).join('');
  $('model-version').textContent = `${model.engine} · ${model.model_id.slice(0,8)}`;
  $('model-notes-content').innerHTML = `<p>Learned from ${esc(model.training_start.slice(0,10))} to ${esc(model.training_end.slice(0,10))}. ${fmt(model.excluded_boundary_cases,0)} cases spanning the temporal split were excluded.</p><p><strong>${fmt(model.overlap_event_pct)}% of training events overlap earlier work by the same resource.</strong> The scenario engine uses one task per resource at a time; this can affect baseline fidelity. ${fmt(model.zero_duration_pct)}% of source events have zero duration.</p><ul>${model.assumptions.map(a => `<li>${esc(a)}</li>`).join('')}</ul><p>Source SHA-256: <code>${esc(model.source_hash)}</code></p>`;
  renderResources(); renderProfile(); renderChanges(); busy(state.job);
  navigate(state.view);
}
function renderResources() {
  if (!state.model) return;
  const query = $('resource-search').value.toLowerCase();
  const resources = state.model.resources.filter(r => (r.name + ' ' + Object.keys(r.activities).join(' ')).toLowerCase().includes(query)).sort((a,b) => b.event_count - a.event_count);
  $('resource-rows').innerHTML = resources.map(r => {
    const change = state.changes[r.id];
    const tag = change?.enabled === false ? ['removed', 'Removed'] : change ? ['changed', 'Adjusted'] : ['', 'Historical'];
    return `<tr data-resource="${esc(r.id)}" class="${state.selected === r.id ? 'selected' : ''}"><td><button class="resource-name" data-select="${esc(r.id)}"><span class="avatar">${esc(r.name.slice(-3))}</span>${esc(r.name)}</button></td><td>${Object.keys(r.activities).length}</td><td>${fmt(r.event_count,0)}</td><td><span class="tag ${tag[0]}">${tag[1]}</span></td></tr>`;
  }).join('');
  $('resource-empty').hidden = resources.length > 0;
}
function hour(value) { const h = Math.floor(value / 3600); return String(h).padStart(2,'0') + ':' + String(Math.floor(value % 3600 / 60)).padStart(2,'0'); }
function renderProfile() {
  const r = state.model?.resources.find(r => r.id === state.selected);
  if (!r) return;
  const change = state.changes[r.id];
  const schedule = change?.schedule;
  const shifts = r.calendar.filter(Boolean);
  const description = [...new Set(shifts.map(s => `${hour(s[0])}–${hour(s[1])}`))].join(', ');
  $('profile-detail').innerHTML = `<div class="profile-top"><span class="avatar">${esc(r.name.slice(-3))}</span><div><h2>${esc(r.name)}</h2><p class="subtle">${fmt(r.case_count,0)} historical cases · ${fmt(r.event_count,0)} events</p></div></div>
    <div class="detail-label">Typical processing time</div>${Object.entries(r.activities).map(([a,d]) => `<div class="mini-row"><span>${esc(a.replace(/^W_/,''))}</span><strong>${fmt(d.median_seconds/60)} min</strong></div>`).join('')}
    <p class="field-help">Median estimated working time per activity.</p><div class="detail-label">Observed schedule · UTC</div><div class="week">${days.map((d,i) => `<span class="day ${r.calendar[i] ? 'on' : ''}" title="${r.calendar[i] ? hour(r.calendar[i][0]) + '–' + hour(r.calendar[i][1]) : 'No observed hours'}">${d}</span>`).join('')}</div><p class="field-help">${esc(description)}</p>
    <div class="detail-label">Frequent handoffs</div>${r.handoffs.slice(0,3).map(h => `<div class="mini-row"><span>${esc(h.name)}</span><strong>${fmt(h.probability*100,0)}%</strong></div>`).join('') || '<p class="subtle">No outgoing handoffs observed.</p>'}
    <hr class="divider"><h3>Change this resource</h3><form id="resource-form"><label class="checkbox"><input id="resource-enabled" type="checkbox" ${change?.enabled !== false ? 'checked' : ''}>Include in scenario</label><label>Processing time <span class="unit">% of historical · lower is faster</span><input id="resource-duration" type="number" min="10" max="500" value="${(change?.duration_multiplier || 1)*100}" required></label>
    <label class="checkbox"><input type="checkbox" id="schedule-override" ${schedule ? 'checked' : ''}>Set working hours</label><div id="schedule-fields" ${schedule ? '' : 'hidden'}><div class="week-input">${days.map((d,i) => `<label><input type="checkbox" name="weekday" value="${i}" ${(schedule?.weekdays || [0,1,2,3,4]).includes(i) ? 'checked' : ''}>${d}</label>`).join('')}</div><div class="field-grid"><label>Start hour (UTC)<input id="schedule-start" type="number" min="0" max="23.5" step="0.5" value="${schedule?.start_hour ?? 9}"></label><label>End hour (UTC)<input id="schedule-end" type="number" min="0.5" max="24" step="0.5" value="${schedule?.end_hour ?? 17}"></label></div></div><button class="secondary wide" type="submit">Apply resource change</button></form>
    <hr class="divider"><h3>Create a resource from this history</h3><p class="field-help">Copies the historical capabilities, timings and handoff position. Each copy has its own capacity.</p><form id="clone-form"><label>New resource name<input id="clone-name" maxlength="70" value="${esc(r.name)} copy" required></label><div class="field-grid"><label>Number of copies<input id="clone-count" type="number" min="1" max="20" value="1" required></label><label>Processing time (%)<input id="clone-duration" type="number" min="10" max="500" value="100" required></label></div><label class="checkbox"><input type="checkbox" id="clone-schedule">Use the working hours entered above</label><button class="primary wide" type="submit">+ Add resource to scenario</button></form>`;
  $('schedule-override').onchange = () => $('schedule-fields').hidden = !$('schedule-override').checked;
  $('clone-schedule').onchange = () => { if ($('clone-schedule').checked) $('schedule-fields').hidden = false; };
  $('resource-form').onsubmit = safe(event => {
    event.preventDefault();
    const edit = {resource_id:r.id, enabled:$('resource-enabled').checked, duration_multiplier:Number($('resource-duration').value)/100, schedule:$('schedule-override').checked ? readSchedule() : null};
    if (edit.enabled && edit.duration_multiplier === 1 && !edit.schedule) delete state.changes[r.id]; else state.changes[r.id] = edit;
    changed(); notice('Resource settings applied to the scenario. The historical model is unchanged.');
  });
  $('clone-form').onsubmit = safe(event => {
    event.preventDefault();
    const name = $('clone-name').value.trim();
    if (!name) throw new Error('Enter a name for the new resource.');
    state.clones.push({source_id:r.id, name, count:Number($('clone-count').value), duration_multiplier:Number($('clone-duration').value)/100, schedule:$('clone-schedule').checked ? readSchedule() : null});
    changed(); notice(`Added ${name} to the scenario. Open Scenario builder to review and run.`);
  });
}
function readSchedule() {
  const weekdays = [...document.querySelectorAll('input[name=weekday]:checked')].map(e => Number(e.value));
  const start_hour = Number($('schedule-start').value), end_hour = Number($('schedule-end').value);
  if (!weekdays.length || end_hour <= start_hour || start_hour < 0 || end_hour > 24) throw new Error('Choose at least one weekday and an end hour after the start hour.');
  return {weekdays, start_hour, end_hour};
}
function changed() { renderChanges(); renderResources(); saveDraft(); }
function renderChanges() {
  const name = id => state.model?.resources.find(r => r.id === id)?.name || id;
  const rows = [];
  for (const [id, r] of Object.entries(state.changes)) rows.push([`Resource · ${name(id)}`, r.enabled ? `${fmt(r.duration_multiplier*100,0)}% processing time${r.schedule ? ' · custom schedule' : ''}` : 'Removed from scenario', 'resource', id]);
  state.clones.forEach((c,i) => rows.push([`Add ${c.count} × ${c.name}`, `Based on ${name(c.source_id)} · ${fmt(c.duration_multiplier*100,0)}% processing time${c.schedule ? ' · custom schedule' : ''}`, 'clone', i]));
  for (const [id,a] of Object.entries(state.activities)) rows.push([a.activity, `${fmt(a.duration_multiplier*100,0)}% processing time · ${fmt(a.delay_multiplier*100,0)}% residual delay`, 'activity', id]);
  $('change-count').textContent = rows.length;
  $('changes-list').innerHTML = rows.length ? rows.map(([title,help,type,id]) => `<div class="change-item"><div><strong>${esc(title)}</strong><p>${esc(help)}</p></div><button data-remove="${type}" data-id="${esc(id)}" aria-label="Remove ${esc(title)}">×</button></div>`).join('') : '<p class="subtle">No resource or activity changes yet. Select a profile to add capacity, adjust working hours, or change processing times.</p>';
}
function request() {
  if (!state.model) throw new Error('Learn a model first.');
  return {model_id:state.model.model_id, name:$('scenario-name').value.trim(), horizon_days:Number($('horizon').value), repetitions:Number($('repetitions').value), seed:Number($('seed').value), sla_hours:Number($('sla').value), demand_multiplier:Number($('demand').value)/100, routing:$('routing').value, resource_changes:Object.values(state.changes), clones:state.clones, activity_changes:Object.values(state.activities)};
}
async function run() {
  if (state.job) return;
  navigate('scenario');
  if (!$('scenario-form').reportValidity()) return;
  notice(); saveDraft();
  $('run-top').disabled = $('run-scenario').disabled = true;
  try { const job = await api('/experiments', {method:'POST', body:JSON.stringify(request())}); busy(job); poll(job.id); }
  catch (error) { busy(null); throw error; }
}
async function poll(id) {
  $('retry-poll').hidden = true;
  try {
    let job;
    do {
      job = await api('/jobs/' + id);
      busy(job); $('job-message').textContent = job.message; $('job-percent').textContent = job.progress + '%'; $('job-progress').value = job.progress;
      if (['queued','running'].includes(job.status)) await new Promise(resolve => setTimeout(resolve, 1200));
    } while (['queued','running'].includes(job.status));
    busy(null); remember('process-lab-job', null); $('job-panel').hidden = true;
    if (job.status === 'error') throw new Error(job.error);
    if (job.kind === 'discover') {
      const target = state.pendingView || 'resources';
      state.pendingView = null;
      await loadModel(job.model_id);
      // Only jump the user to the tab this was launched from if they're still there — a
      // background learn shouldn't yank them off a different tab they've since moved to
      // (e.g. mid-conversation with the assistant).
      if (state.view === target) navigate(target);
      else notice('Resource profiles updated in the background.');
    }
    else { await showResult(job.id); }
  } catch (error) {
    notice(error.message);
    if (state.job) { $('job-note').textContent = 'Connection interrupted. The job may still be running on the server.'; $('retry-poll').hidden = false; $('retry-poll').onclick = () => poll(id); }
  }
}
const metricCards = [
  ['median_cycle_hours','Median case duration','h',false], ['p90_cycle_hours','90th percentile duration','h',false],
  ['mean_wait_hours','Mean queue & calendar wait / case','h',false], ['throughput_per_day','Completions within horizon','/day',true],
  ['sla_met_pct','Cases meeting the deadline','%',true], ['backlog_at_horizon','Unfinished cases at horizon','cases',false]
];
async function showResult(id) { state.result = await api('/experiments/' + id); renderResults(); navigate('results'); }
const tone = value => Math.abs(value) < 10 ? 'ok' : Math.abs(value) < 25 ? 'warn' : 'bad';
const diff = value => value === null || value === undefined ? '' : `<span class="diff ${tone(value)}">${value > 0 ? '+' : ''}${fmt(value, 0)}%</span>`;
function renderValidation(r) {
  const v = r.validation;
  if (!v) return `<div class="result-note"><strong>Baseline fidelity check:</strong> simulated median case duration ${fmt(r.comparison.metrics.median_cycle_hours.baseline.mean)} h; held-out historical median ${fmt(r.historical_cycle_hours.median)} h. Learn the dataset again for a detailed validation.</div>`;
  const rows = list => list.map(row => `<tr><td>${esc(row.label)}</td><td>${fmt(row.real)}</td><td>${fmt(row.simulated)}</td><td>${diff(row.difference_pct)}</td></tr>`).join('');
  const scope = v.censoring_controlled
    ? `Compared with ${fmt(v.cases, 0)} of ${fmt(v.all_test_cases, 0)} held-out cases that had at least ${fmt(v.follow_up_days)} days to finish. The historical log ends on a fixed date, so cases arriving later are cut short and would look unrealistically fast (all held-out cases would show a mean of ${fmt(v.all_test_cycle_hours.mean)} h).`
    : `<strong>Not corrected:</strong> too little history remains after the split to leave out cut-short cases, so real durations may look too fast.`;
  return `<section class="card padded validation"><p class="eyebrow">HOW WELL DOES THE BASELINE MATCH HISTORY?</p><h2>Simulated baseline vs. real held-out cases</h2>
    <p class="subtle">${scope} Simulated over ${v.horizon_days} arrival days, averaged over ${r.baseline_runs.length} repetitions. Green is within 10%, amber within 25%.</p>
    <div class="table-wrap"><table><thead><tr><th>Measure</th><th>Real</th><th>Simulated</th><th>Difference</th></tr></thead><tbody>${rows(v.cycle)}${rows(v.flow)}</tbody></table></div>
    <h3>Idle time before each task (hours)</h3><p class="subtle">Time between a case's previous task ending and this task starting, in the log and in the simulation. Large gaps show where the model is wrong.</p>
    <div class="table-wrap"><table><thead><tr><th>Activity</th><th>Tasks</th><th>Real median</th><th>Sim median</th><th>Real mean</th><th>Sim mean</th><th>Mean diff.</th></tr></thead><tbody>${v.activities.map(a => `<tr><td>${esc(a.name.replace(/^W_/, ''))}</td><td>${fmt(a.events, 0)}</td><td>${fmt(a.real_median)}</td><td>${fmt(a.simulated_median)}</td><td>${fmt(a.real_mean)}</td><td>${fmt(a.simulated_mean)}</td><td>${diff(a.mean_difference_pct)}</td></tr>`).join('')}</tbody></table></div>
    <h3>Who holds the simulated queues</h3><p class="subtle">If a few people hold most of the queueing while their observed load is modest, a few unusually long recorded tasks are probably blocking them in the simulation.</p>
    <div class="table-wrap"><table><thead><tr><th>Resource</th><th>Queue hours</th><th>Share of all queueing</th><th>Observed load in history</th></tr></thead><tbody>${v.queue_resources.map(q => `<tr><td>${esc(q.name)}</td><td>${fmt(q.queue_hours, 0)}</td><td>${fmt(q.share_pct, 0)}%</td><td>${fmt(q.observed_load_pct)}%</td></tr>`).join('')}</tbody></table></div></section>`;
}
function renderResults() {
  const r = state.result, comparison = r.comparison;
  const m = comparison.metrics;
  $('no-results').hidden = true; $('results-content').hidden = false;
  const cards = metricCards.map(([key,label,unit,higher]) => {
    const value = m[key], diff = value.delta.mean;
    const kind = Math.abs(diff) < .000001 ? '' : (higher ? diff > 0 : diff < 0) ? 'good' : 'bad';
    const delta = Math.abs(diff) < .000001 ? 'No change' : value.change_pct === null ? `${diff > 0 ? '+' : ''}${fmt(diff)} ${unit}` : `${value.change_pct > 0 ? '+' : ''}${fmt(value.change_pct)}% vs baseline`;
    return `<div class="card result-card"><h3>${label}</h3><div class="result-value"><span class="from">${fmt(value.baseline.mean)}</span><span class="arrow">→</span>${fmt(value.scenario.mean)} <small style="font-size:12px">${unit}</small></div><span class="delta ${kind}">${delta}</span><p class="subtle">Scenario range: ${fmt(value.scenario.min)}–${fmt(value.scenario.max)} ${unit} across repetitions</p></div>`;
  }).join('');
  const activityMax = Math.max(.01, ...comparison.activities.flatMap(a => [a.baseline,a.scenario]));
  const bars = comparison.activities.sort((a,b) => b.scenario-a.scenario).map(a => `<div class="chart-row"><div class="chart-label">${esc(a.name.replace(/^W_/,''))}</div><div class="bars">${[['baseline',a.baseline],['scenario',a.scenario]].map(([side,value]) => `<div class="bar-row"><div class="bar-track"><div class="bar ${side}" style="width:${value/activityMax*100}%"></div></div><span class="bar-value">${fmt(value)} h</span></div>`).join('')}</div></div>`).join('');
  const resourceRows = [...comparison.resources].sort((a,b) => (b.scenario_utilization ?? -1)-(a.scenario_utilization ?? -1)).map(row => `<tr><td>${esc(row.name)}</td><td>${row.baseline_utilization === null ? '<span class="tag changed">New</span>' : fmt(row.baseline_utilization)+'%'}</td><td>${row.scenario_utilization === null ? '<span class="tag removed">Removed</span>' : fmt(row.scenario_utilization)+'%'}</td></tr>`).join('');
  $('results-content').innerHTML = `<div class="results-heading"><div><h2>${esc(r.request.name)}</h2><p>${esc(r.dataset)} · ${r.request.horizon_days} arrival days · ${r.request.repetitions} paired repetitions · seed ${r.request.seed} · deadline ${fmt(r.request.sla_hours)} h</p></div><button class="secondary" id="reuse-result">Reuse these settings</button></div>
    <div class="legend"><span><i></i>Historical baseline</span><span><i class="green"></i>Your scenario</span></div><div class="result-cards">${cards}</div>
    <div class="result-note">${fmt(m.active_resources.baseline.mean,0)} → ${fmt(m.active_resources.scenario.mean,0)} active resources. ${fmt(m.arrived_cases.baseline.mean,0)} → ${fmt(m.arrived_cases.scenario.mean,0)} arrivals on average. Durations and deadline compliance include every arrived case, including those finishing after the horizon. Utilization and throughput cover only the selected horizon. Ranges show repetition variability, not confidence intervals.</div>
    <div class="results-grid"><section class="card padded"><h2>Where cases wait</h2><p class="subtle">Mean queue and calendar wait per activity instance, in hours.</p>${bars}</section><section class="card"><div class="padded"><h2>Resource utilization</h2><p class="subtle">Working time occupied ÷ scheduled capacity within the horizon.</p></div><div class="table-wrap" style="max-height:450px;overflow:auto"><table><thead><tr><th>Resource</th><th>Baseline</th><th>Scenario</th></tr></thead><tbody>${resourceRows}</tbody></table></div></section></div>
    ${renderValidation(r)}
    <details class="result-details"><summary>All metrics & paired differences</summary><div class="table-wrap"><table><thead><tr><th>Metric</th><th>Baseline mean</th><th>Scenario mean</th><th>Paired difference range</th></tr></thead><tbody>${Object.entries(m).map(([k,v])=>`<tr><td>${esc(k.replaceAll('_',' '))}</td><td>${fmt(v.baseline.mean)}</td><td>${fmt(v.scenario.mean)}</td><td>${fmt(v.delta.min)} to ${fmt(v.delta.max)}</td></tr>`).join('')}</tbody></table></div></details>
    <section class="card padded" style="margin-top:22px"><h3>Take the results with you</h3><p class="subtle">Every repetition has separate event logs. The JSON includes all settings, seeds, model identity and assumptions.</p><div class="download-row"><a href="/api/workbench/experiments/${r.id}/files/result.json" download>↓ Full comparison JSON</a><label><span class="subtle">Repetition </span><select id="export-repetition" aria-label="Export repetition">${Array.from({length:r.request.repetitions},(_,i)=>`<option value="${i+1}">${i+1}</option>`).join('')}</select></label><a id="baseline-download" download>↓ Baseline CSV</a><a id="scenario-download" download>↓ Scenario CSV</a></div></section>`;
  $('reuse-result').onclick = safe(() => reuse(r));
  const updateLinks = () => { for (const side of ['baseline','scenario']) $(side+'-download').href = `/api/workbench/experiments/${r.id}/files/${side}_${$('export-repetition').value}.csv`; };
  $('export-repetition').onchange = updateLinks; updateLinks();
}
async function reuse(result) {
  if (state.job) throw new Error('Wait for the running job before loading another scenario.');
  if (state.model?.model_id !== result.model_id) await loadModel(result.model_id, false);
  const req = result.request;
  state.changes = Object.fromEntries(req.resource_changes.map(c=>[c.resource_id,c]));
  state.clones = structuredClone(req.clones); state.activities = Object.fromEntries(req.activity_changes.map(c=>[c.activity,c]));
  const values = {'scenario-name':req.name, horizon:req.horizon_days, repetitions:req.repetitions, demand:req.demand_multiplier*100, sla:req.sla_hours, routing:req.routing, seed:req.seed};
  Object.entries(values).forEach(([key,value])=>$(key).value=value); changed(); renderProfile(); navigate('scenario'); notice();
}
async function renderHistory() {
  const jobs = await api('/jobs');
  $('history-list').innerHTML = jobs.length ? jobs.map(j => `<section class="card history-card"><div><h3>${esc(j.name)}</h3><p>${esc(j.dataset)} · ${j.kind === 'discover' ? 'Model discovery' : 'Comparison'} · ${esc(new Date(j.created_at).toLocaleString())} · ${esc(j.status)}</p>${j.error ? `<p class="inline-warning">${esc(j.error)}</p>` : ''}</div><div class="actions">${j.status === 'done' ? j.kind === 'experiment' ? `<button class="secondary" data-result="${j.id}">View result ↗</button>` : `<button class="secondary" data-model="${j.model_id}">Open model</button>` : ['running','queued'].includes(j.status) ? `<button class="secondary" data-job="${j.id}">Follow progress</button>` : ''}</div></section>`).join('') : '<p class="subtle">No saved experiments yet.</p>';
}
document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => navigate(button.dataset.view));
$('resource-search').oninput = renderResources;
$('resource-rows').onclick = event => { const row = event.target.closest('[data-resource]'); if (row) { state.selected = row.dataset.resource; renderResources(); renderProfile(); } };
$('changes-list').onclick = event => {
  const button = event.target.closest('[data-remove]'); if (!button) return;
  if (button.dataset.remove === 'clone') state.clones.splice(Number(button.dataset.id),1);
  if (button.dataset.remove === 'resource') delete state.changes[button.dataset.id];
  if (button.dataset.remove === 'activity') delete state.activities[button.dataset.id];
  changed(); renderProfile();
};
$('reset-changes').onclick = () => { state.changes={}; state.clones=[]; state.activities={}; changed(); renderProfile(); };
$('choose-resource').onclick = () => navigate('resources');
$('build-scenario').onclick = () => navigate('scenario');
$('run-top').onclick = safe(run);
$('scenario-form').onsubmit = event => { event.preventDefault(); safe(run)(); };
fields.forEach(f => $(f).addEventListener('change',saveDraft));
const presets = {
  'pooled': {name: 'Pooled allocation (earliest qualified person)', routing: 'pooled'},
  'halve-delays': {name: 'Halve all residual delays', delays: true},
  'remove-busiest': {name: 'Remove the busiest person', remove: true},
  'copy-busiest': {name: 'Add 3 copies of the busiest person', copies: 3},
  'demand': {name: 'Demand +50%', demand: 150}
};
$('presets').onclick = event => {
  const button = event.target.closest('[data-preset]'); if (!button) return;
  if (!state.model) return notice('Learn a model first.');
  const preset = presets[button.dataset.preset];
  const busiest = [...state.model.resources].sort((a,b) => b.event_count - a.event_count)[0];
  state.changes = {}; state.clones = []; state.activities = {};
  $('routing').value = preset.routing || 'historical';
  $('demand').value = preset.demand || 100;
  if (preset.delays) state.model.activities.forEach(a => { state.activities[a.name] = {activity: a.name, duration_multiplier: 1, delay_multiplier: .5}; });
  if (preset.remove) state.changes[busiest.id] = {resource_id: busiest.id, enabled: false, duration_multiplier: 1, schedule: null};
  if (preset.copies) state.clones = [{source_id: busiest.id, name: busiest.name + ' copy', count: preset.copies, duration_multiplier: 1, schedule: null}];
  $('scenario-name').value = state.model.dataset.replace('BPIC_', 'BPI ') + ' · ' + preset.name;
  changed(); renderProfile(); saveDraft();
  notice(preset.remove || preset.copies ? `Preset applied to ${busiest.name}, the resource with the most observed events.` : 'Preset applied. Review the settings, then run.');
};
$('activity-form').onsubmit = event => {
  event.preventDefault(); const activity=$('activity-select').value;
  const change={activity,duration_multiplier:Number($('activity-duration').value)/100,delay_multiplier:Number($('activity-delay').value)/100};
  if (change.duration_multiplier===1 && change.delay_multiplier===1) delete state.activities[activity]; else state.activities[activity]=change;
  changed(); notice('Activity settings applied.');
};
$('refresh-history').onclick = safe(renderHistory);
$('history-list').onclick = safe(async event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.result) await showResult(button.dataset.result);
  if (button.dataset.model) { if (state.job) throw new Error('Wait for the running job to finish.'); await loadModel(button.dataset.model); navigate('resources'); }
  if (button.dataset.job && !state.job) { busy({id:button.dataset.job}); poll(button.dataset.job); }
});
$('learn').onclick = safe(async () => {
  if (state.job) return;
  notice(); $('learn').disabled=true;
  state.pendingView = state.view === 'resources' ? null : state.view;  // stay on the current tab once the model is ready, unless it's already 'resources'
  try { const job=await api('/models',{method:'POST',body:JSON.stringify({dataset:$('dataset').value})}); busy(job); poll(job.id); }
  catch(error) { busy(null); throw error; }
});
$('dataset').onchange = () => { $('model-label').textContent = state.model?.dataset === $('dataset').value ? 'Current learned model' : 'Click Learn to load this dataset. The current workspace is unchanged.'; };
safe(async () => {
  const datasets=await api('/datasets');
  $('dataset').innerHTML=datasets.map(d=>`<option value="${esc(d.name)}" ${d.supported ? '' : 'disabled'}>${esc(d.name)}${d.supported ? '' : ' · missing durations'}</option>`).join('');
  if(datasets.some(d=>d.name==='BPIC_2017_W')) $('dataset').value='BPIC_2017_W';
  const history=await api('/jobs');
  const remembered=recall('process-lab-model');
  const candidates=[remembered, ...history.filter(j=>j.kind==='discover' && j.status==='done' && j.dataset===$('dataset').value).map(j=>j.model_id)].filter(Boolean);
  let loaded=false;
  for (const id of [...new Set(candidates)]) {
    try { await loadModel(id); loaded=true; break; }
    catch (error) { /* an older model version, or the file is gone — try the next candidate */ }
  }
  if(!loaded && candidates.length) { remember('process-lab-model',null); notice('The saved model is no longer available (it may be an older version). Click "Learn resource profiles" to rebuild it — this takes under a minute.'); }
  const running=history.find(j=>['queued','running'].includes(j.status));
  if(running) { busy(running); poll(running.id); }
  else { remember('process-lab-job',null); const last=history.find(j=>j.kind==='experiment' && j.status==='done' && j.model_id===state.model?.model_id); if(last) { state.result=await api('/experiments/'+last.id); renderResults(); } }
})();

function renderExplorer() {
  if (typeof loadAnalyst === 'function') safe(loadAnalyst)();
  const box = $('explore-content'), e = state.model?.explore;
  if (!e) { box.innerHTML = '<div class="card empty-state"><h2>Learn a dataset to explore it.</h2><p>This page describes the whole historical log: waiting, common paths, working rhythm and handovers. Learn the dataset again if this model predates the explorer.</p></div>'; return; }
  const short = name => name.replace(/^W_/, '');
  const shown = e.activities.filter(a => a.share >= .005), rare = e.activities.length - shown.length;
  const maxGap = Math.max(.01, ...shown.map(a => a.mean_gap_hours));
  const maxHour = Math.max(1, ...e.hour_of_week.flat());
  const hourCells = e.hour_of_week.map((row, d) => `<div class="lab">${days[d]}</div>` + row.map((n, h) => `<div class="cell" style="background:rgba(32,122,89,${(n / maxHour * .95 + (n ? .05 : 0)).toFixed(3)})" title="${days[d]} ${String(h).padStart(2, '0')}:00 UTC · ${fmt(n, 0)} tasks started"></div>`).join('')).join('');
  const hourAxis = '<div></div>' + Array.from({length: 24}, (_, h) => `<div class="lab" style="justify-content:center;padding:0">${h % 3 === 0 ? h : ''}</div>`).join('');
  const maxMonth = Math.max(.01, ...e.months.map(m => m.median_cycle_days));
  const names = e.handover.names;
  const matrixHead = '<div></div>' + names.map(n => `<div class="lab col">${esc(n)}</div>`).join('');
  const matrix = e.handover.matrix.map((row, i) => `<div class="lab">${esc(names[i])}</div>` + row.map((v, k) => `<div class="cell" style="background:rgba(32,122,89,${Math.min(1, v * 2.2).toFixed(3)})" title="${esc(names[i])} → ${esc(names[k])}: ${fmt(v * 100)}% of ${esc(names[i])}'s handoffs">${v >= .05 ? fmt(v * 100, 0) : ''}</div>`).join('')).join('');
  const touch = e.touch_share_pct;
  const noun = e.full_log ? 'events' : 'tasks';  // full log includes application/offer state changes, not just work items
  const stats = [
    ['Cases', fmt(e.cases, 0), `${fmt(e.events, 0)} ${noun} · ${e.resource_count} people · ${e.activity_count} activities`],
    ['Median case duration', `${fmt(e.cycle_days.median)} days`, `mean ${fmt(e.cycle_days.mean)} · 90th percentile ${fmt(e.cycle_days.p90)} days`],
    ['Recorded task time per case', `${fmt(e.median_touch_minutes, 0)} min`, 'median, all task durations added up'],
    ['Cases repeating a task', `${fmt(e.repeat_case_pct, 0)}%`, `${fmt(e.variant_count, 0)} distinct paths in ${fmt(e.cases, 0)} cases`]
  ].map(([label, value, help]) => `<div class="stat"><span class="label">${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(help)}</small></div>`).join('');
  const variants = e.variants.map(v => { const seen = new Set(); return `<div style="margin:10px 0"><div class="chips">${v.steps.map(s => { const again = seen.has(s); seen.add(s); return `<span class="chip ${again ? 'repeat' : ''}">${esc(short(s))}</span>`; }).join('')}</div><div class="bar-line" style="grid-template-columns:1fr 110px;margin:0"><span class="track"><i class="fill" style="width:${v.share / e.variants[0].share * 100}%"></i></span><span class="val">${fmt(v.share * 100)}% · ${fmt(v.cases, 0)}</span></div></div>`; }).join('');
  box.innerHTML = `${e.full_log ? '<p class="tag changed" style="margin-bottom:14px">Full log: application, offer and work-item events</p>' : ''}<div class="stats-grid">${stats}</div>
  <div class="explore-grid">
    <section class="explore-card wide"><p class="eyebrow">RECORDED TASK TIME</p><p class="big-claim">Median case duration: <strong>${fmt(e.cycle_days.median)} days</strong>. Median summed task duration per case: <strong>${fmt(e.median_touch_minutes, 0)} minutes</strong>.</p>
      <div class="share-bar" title="${fmt(touch)}% recorded task duration"><span style="width:${Math.min(100, Math.max(.5, touch))}%"></span></div><p class="subtle">Across all cases, summed task durations are ${fmt(touch)}% of summed case durations.${e.full_log ? ' Application and offer state changes are recorded instantaneously (no observed duration) and do not add to this share.' : ''} These timestamps can include off-hours and overlapping tasks; they do not measure active work or establish why a case waited. These are whole-log statistics, including held-out cases.</p></section>
    <section class="explore-card"><h2>Idle time before each ${noun.slice(0, -1)}</h2><p class="subtle">Average hours between the previous ${noun.slice(0, -1)} ending and this one starting. Median length on the right.</p>
      ${shown.map(a => `<div class="bar-line"><span title="${fmt(a.events, 0)} ${noun}">${esc(short(a.name))}</span><span class="track"><i class="fill" style="width:${a.mean_gap_hours / maxGap * 100}%"></i></span><span class="val">${fmt(a.mean_gap_hours)} h · ${fmt(a.median_minutes)} min</span></div>`).join('')}${rare ? `<p class="subtle">${rare} rare ${rare === 1 ? 'activity' : 'activities'} (under 0.5% of ${noun}) not shown.</p>` : ''}</section>
    <section class="explore-card"><h2>Most common paths</h2><p class="subtle">The top ${e.variants.length} of ${fmt(e.variant_count, 0)} distinct paths cover ${fmt(e.top_variant_coverage_pct, 0)}% of cases. Highlighted steps are repeats.${e.full_log ? ' Paths include application and offer states, not just work items.' : ''}</p>${variants}</section>
    <section class="explore-card wide"><h2>When work happens</h2><p class="subtle">${noun[0].toUpperCase()}${noun.slice(1)} started per weekday and hour, in UTC. Darker means busier. This is the pattern the simulator infers working hours from${e.full_log ? ' (from work items only; application/offer events are shown here too but are not used for calendars)' : ''}.</p>
      <div class="heat" style="grid-template-columns:34px repeat(24,minmax(14px,1fr))">${hourAxis}${hourCells}</div></section>
    <section class="explore-card"><h2>Case duration by arrival month</h2><p class="subtle">Median days from first to last ${noun.slice(0, -1)}. The latest months are cut short because the log ends on a fixed date.</p>
      <div class="month-bars">${e.months.map(m => `<div title="${esc(m.month)}: ${fmt(m.cases, 0)} cases, median ${fmt(m.median_cycle_days)} days"><i style="height:${m.median_cycle_days / maxMonth * 130}px"></i>${esc(m.month.slice(2))}</div>`).join('')}</div></section>
    <section class="explore-card"><h2>Who hands work to whom</h2><p class="subtle">For the ${names.length} busiest people: the share of each person's handoffs (rows) that go to each other person (columns). ${fmt(e.handover.same_resource_pct, 0)}% of all handoffs stay with the same person.</p>
      <div class="heat matrix" style="grid-template-columns:64px repeat(${names.length},minmax(18px,1fr))">${matrixHead}${matrix}</div></section>
  </div>`;
}

// ---- Simulation assistant: chat UI for the LangGraph research-engine orchestrator (/api/sessions) ----
const assistant = {threadId: null, ready: false, initializing: false, polling: false, revision: -1, messages: []};
function saveAssistant() { remember('process-lab-assistant', {threadId: assistant.threadId, revision: assistant.revision, messages: assistant.messages}); }
// A minimal, dependency-free markdown-lite renderer for the assistant's own prose (bold, inline
// code, bullet/numbered lists, short headings, paragraphs) — enough to structure what the LLM
// writes without pulling in a markdown library for one small chat panel. Always escapes first,
// then layers on trusted tags via regex, so nothing the model outputs can inject markup.
function mdToHtml(text) {
  const blocks = []; let list = null, para = [];
  const flushPara = () => { if (para.length) { blocks.push({type: 'p', text: para.join(' ')}); para = []; } };
  const flushList = () => { if (list) { blocks.push(list); list = null; } };
  for (const raw of String(text ?? '').split('\n')) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (heading) { flushPara(); flushList(); blocks.push({type: 'h', level: heading[1].length, text: heading[2]}); }
    else if (bullet) { flushPara(); if (list?.type !== 'ul') { flushList(); list = {type: 'ul', items: []}; } list.items.push(bullet[1]); }
    else if (numbered) { flushPara(); if (list?.type !== 'ol') { flushList(); list = {type: 'ol', items: []}; } list.items.push(numbered[1]); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  const inline = raw => esc(raw)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(?<![*\w])\*([^*\n]+)\*(?!\w)/g, '<em>$1</em>');
  return blocks.map(b => b.type === 'h' ? `<h5>${inline(b.text)}</h5>`
    : b.type === 'ul' ? `<ul>${b.items.map(i => `<li>${inline(i)}</li>`).join('')}</ul>`
    : b.type === 'ol' ? `<ol>${b.items.map(i => `<li>${inline(i)}</li>`).join('')}</ol>`
    : `<p>${inline(b.text)}</p>`).join('') || '<p></p>';
}
function assistantMsg(role, text, save = true) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  if (role === 'assistant') el.innerHTML = mdToHtml(text); else el.textContent = text;
  $('assistant-chat').appendChild(el);
  $('assistant-chat').scrollTop = $('assistant-chat').scrollHeight;
  if (save) { assistant.messages.push({role, text}); saveAssistant(); }
}
function assistantTyping(show) {
  let el = document.getElementById('assistant-typing');
  if (show && !el) {
    el = document.createElement('div'); el.id = 'assistant-typing'; el.className = 'assistant-typing'; el.textContent = 'Thinking…';
    $('assistant-chat').appendChild(el);
  } else if (!show && el) {
    el.remove();
  }
  $('assistant-chat').scrollTop = $('assistant-chat').scrollHeight;
}
function assistantSetInputEnabled(enabled) {
  $('assistant-input').disabled = !enabled;
  $('assistant-send').disabled = !enabled;
}
function assistantConfirmCard(payload) {
  const el = document.createElement('div');
  el.className = 'confirm-card';
  const cols = payload.columns || {};
  const mode = payload.determine_automatically ? 'auto-determined' : `central orchestration=${payload.central_orchestration}, extraneous delays=${payload.extr_delays}`;
  el.innerHTML = `<h3>Confirm this simulation run</h3><table>
    <tr><td>Dataset</td><td class="v">${esc(payload.dataset_name)}</td></tr>
    <tr><td>Simulations</td><td class="v">${esc(String(payload.num_simulations))}</td></tr>
    <tr><td>Mode</td><td class="v">${esc(mode)}</td></tr>
    <tr><td>Columns</td><td class="v">${esc([cols.case_id, cols.activity_name, cols.resource, cols.start_timestamp, cols.end_timestamp].filter(Boolean).join(' · '))}</td></tr>
  </table><div class="row"><button type="button" class="primary">Confirm & run</button><button type="button" class="secondary">Cancel</button></div>`;
  $('assistant-chat').appendChild(el);
  $('assistant-chat').scrollTop = $('assistant-chat').scrollHeight;
  const [confirmButton, cancelButton] = el.querySelectorAll('button');
  const decide = async approved => {
    confirmButton.disabled = true; cancelButton.disabled = true;
    try {
      await jsonRequest(`/api/sessions/${assistant.threadId}/confirm`, {method: 'POST', body: JSON.stringify({approved})});
      el.remove();
      assistantSetInputEnabled(false);
      assistantTyping(true);
      await assistantPoll();
    } catch (error) {
      assistantFailure(error);
      confirmButton.disabled = false; cancelButton.disabled = false;
      assistantSetInputEnabled(false);
    }
  };
  confirmButton.onclick = () => decide(true);
  cancelButton.onclick = () => decide(false);
}
async function assistantPoll() {
  if (assistant.polling) return;
  assistant.polling = true;
  try {
    while (true) {
      const data = await jsonRequest(`/api/sessions/${assistant.threadId}/status`);
      if (data.status === 'processing') { await new Promise(resolve => setTimeout(resolve, 1500)); continue; }
      assistantTyping(false);
      document.querySelectorAll('.confirm-card').forEach(el => el.remove());
      if (data.revision !== assistant.revision) {
        (data.messages || []).forEach(m => assistantMsg(m.role, m.content));
        if (data.status === 'error') assistantMsg('system', data.error || 'The request failed. Please retry.');
        assistant.revision = data.revision;
        saveAssistant();
      }
      if (data.status === 'confirmation_required' && data.interrupt) assistantConfirmCard(data.interrupt);
      assistantSetInputEnabled(data.status !== 'confirmation_required');
      $('assistant-retry').hidden = true;
      break;
    }
  } catch (error) {
    assistantFailure(error);
  } finally {
    assistant.polling = false;
  }
}
function assistantFailure(error) {
  assistantTyping(false);
  assistantMsg('system', error.name === 'TimeoutError' ? 'The server did not respond in time. Retry connection to check the request.' : error.message);
  $('assistant-retry').hidden = false;
  // The POST may have reached the server. Check its status before allowing a duplicate.
  assistantSetInputEnabled(false);
  if (error.status === 404) { assistant.ready = false; assistant.threadId = null; saveAssistant(); }
}
async function assistantSend() {
  const text = $('assistant-input').value.trim();
  if (!text || !assistant.threadId) return;
  assistantMsg('user', text);
  $('assistant-input').value = '';
  assistantSetInputEnabled(false);
  assistantTyping(true);
  try {
    await jsonRequest(`/api/sessions/${assistant.threadId}/messages`, {method: 'POST', body: JSON.stringify({text, model_id: state.model?.model_id || null})});
    await assistantPoll();
  } catch (error) {
    $('assistant-input').value = text;
    assistantFailure(error);
  }
}
async function initAssistant() {
  if (assistant.ready) { $('assistant-input').focus(); return; }
  if (assistant.initializing) return;
  assistant.initializing = true;
  assistantSetInputEnabled(false);
  try {
    const saved = recall('process-lab-assistant');
    if (!assistant.messages.length && saved?.messages) {
      assistant.messages = saved.messages;
      assistant.messages.forEach(m => assistantMsg(m.role, m.text, false));
      assistant.threadId = saved.threadId;
      assistant.revision = saved.revision;
    }
    if (!assistant.threadId) {
      const data = await jsonRequest('/api/sessions', {method: 'POST'});
      assistant.threadId = data.thread_id;
      assistant.revision = -1;
      assistantMsg('system', 'Ask about the selected dataset or a resource, or ask me to run the research simulator. Research runs require confirmation. Staffing and delay changes are available in Scenario builder.');
    }
    assistant.ready = true;
    saveAssistant();
    await assistantPoll();
    $('assistant-input').focus();
  } catch (error) {
    assistant.ready = false;
    assistantFailure(error);
  } finally {
    assistant.initializing = false;
  }
}
$('assistant-composer').onsubmit = event => { event.preventDefault(); safe(assistantSend)(); };
$('assistant-retry').onclick = () => assistant.ready ? assistantPoll() : initAssistant();
window.addEventListener('hashchange', () => navigate(location.hash.slice(1) || 'resources'));
if (titles[location.hash.slice(1)]) navigate(location.hash.slice(1));
