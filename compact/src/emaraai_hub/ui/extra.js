/* New pages of the Control Center: Company, Tasks, Decisions, Knowledge, Deliverables, Reports, Workflows.
   Loaded after the main script of dashboard.py; it registers pages into V / TABS / GROUPS and uses its helpers
   (api, post, toast, esc, go, render, state). Everything here lives inside one function so no name leaks. */
(() => {
const $ = id => document.getElementById(id);
const del = (p, b) => api(p, {method: 'DELETE', body: JSON.stringify(b || {})});
const qs = o => Object.entries(o).filter(([, v]) => v !== '' && v != null).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');
const done = (r, okMsg) => { toast(r.ok ? okMsg : (r.error.message + (r.error.fix ? ' ' + r.error.fix : '')), !r.ok); return r.ok; };

/* ---------- formatting ---------- */
const ago = ts => { if (!ts) return ''; const s = Math.max(0, Date.now() / 1000 - ts); return s < 60 ? 'just now' : s < 3600 ? Math.floor(s / 60) + ' min ago' : s < 86400 ? Math.floor(s / 3600) + ' h ago' : Math.floor(s / 86400) + ' d ago'; };
const hm = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const dayOf = ts => { const d = new Date(ts * 1000), t = new Date(); return d.toDateString() === t.toDateString() ? 'Today' : d.toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'short'}); };
const cap = s => String(s || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
const size = n => n == null ? '' : n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
const HUES = [212, 262, 158, 28, 338, 190, 46, 292];
const hue = s => HUES[[...String(s)].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];
const initials = n => String(n || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('');
const STATUS = {WORKING: 'Working', WAITING: 'Waiting', BLOCKED: 'Blocked', ERROR: 'Error', PAUSED: 'Paused', IDLE: 'Idle', OFFLINE: 'Offline'};
const HEALTH = {excellent: 'Excellent', healthy: 'Healthy', attention: 'Needs attention', at_risk: 'At risk', critical: 'Critical'};
const TONE = {done: 'ok', review: 'accent', blocked: 'warn', failed: 'err', in_progress: 'accent', pending: '', cancelled: '', planned: ''};
const TASK = {pending: 'Ready', in_progress: 'In progress', review: 'In review', blocked: 'Blocked', failed: 'Failed', done: 'Done', cancelled: 'Cancelled', planned: 'Planned'};

/* ---------- components (the design system in code) ---------- */
const av = (name, status, cls = '') => `<span class="av ${cls}" style="background:hsl(${hue(name)} 42% 42%)">${esc(initials(name))}${status ? `<i class="dot st-${status}"></i>` : ''}</span>`;
const status = s => `<span class="status st-${s}"><i class="dot"></i>${STATUS[s] || s}</span>`;
const health = l => `<span class="hl ${l}"><i></i>${HEALTH[l] || l}</span>`;
const tag = (t, tone = '') => `<span class="tag ${tone}">${esc(t)}</span>`;
const bar = (pct, tone = '') => `<span class="bar ${tone}"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></span>`;
const loadTone = b => ({overloaded: 'err', busy: 'warn', healthy: 'ok', underloaded: ''}[b]);
const open = (kind, ...a) => `data-open="${kind}" ${a.map((v, i) => `data-a${i}="${esc(v)}"`).join(' ')}`;
const act = (fn, ...a) => `data-do="${fn}" ${a.map((v, i) => `data-a${i}="${esc(v)}"`).join(' ')}`;
const who = (p, sub) => `<span class="who">${av(p.name, p.status)}<div><b class="clip">${esc(p.name)}</b><small class="clip">${esc(sub ?? p.role)}</small></div></span>`;
const empty = (title, text, btn = '') => `<div class="empty"><b>${esc(title)}</b>${esc(text)}${btn ? '<br>' + btn : ''}</div>`;
const ICON = {
  home: '<path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/>', org: '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="2" y="16" width="6" height="5" rx="1"/><rect x="16" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/>',
  proj: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>', task: '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="10" rx="1"/><rect x="17" y="4" width="4" height="13" rx="1"/>',
  act: '<path d="M3 12h4l3-8 4 16 3-8h4"/>', msg: '<path d="M21 12a8 8 0 0 1-12 7l-5 1 1-4.5A8 8 0 1 1 21 12z"/>', know: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM8 7h7M8 11h7"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5"/>', rep: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', dec: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  adv: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.3l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2.3-1.3L14 3h-4l-.3 2.5a7 7 0 0 0-2.3 1.3l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .9.1 1.3l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2.3 1.3L10 21h4l.3-2.5a7 7 0 0 0 2.3-1.3l2.3 1 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', plus: '<path d="M12 5v14M5 12h14"/>'};
const ic = n => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[n]}</svg>`;

const S = {route: [], pulse: '', decisions: 0, drawer: null, org: {zoom: 1, closed: {}, q: '', project: ''}, feed: {category: 'all'}, board: {}, chat: {}, rep: {kind: 'daily'}, know: {tab: 'company', q: '', cat: ''}};
S.ctab = 'chart';
/* ---------- views ---------- */
const VIEWS = {}, AFTER2 = {};
const feed = (items, ctx = {}) => {
  let day = '';
  return `<div class="tl">` + items.map(a => {
    const d = dayOf(a.ts), head = d !== day ? `<div><span class="day">${d}</span></div>` : ''; day = d;
    let text = esc(a.text);
    if (a.agent && a.agent_key && !ctx.noAgent) text = text.replace(esc(a.agent), `<a class="b" ${open('person', a.project, a.agent_key)}>${esc(a.agent)}</a>`);
    return head + `<div class="${a.tone}"><time>${hm(a.ts)}</time><i class="pt"></i><div>${text}${a.task_id ? ` <a class="dim xs" ${open('task', a.task_id)}>open</a>` : ''}${ctx.noProject || !a.project ? '' : ` <span class="dim xs">· ${esc(a.project)}</span>`}</div></div>`;
  }).join('') + `</div>`;
};
const needCard = a => {
  const go2 = a.kind === 'office' ? `href="/company#/office"` : a.kind === 'decision' ? act('goto', 'decisions') : a.kind === 'task' ? open('task', a.id) : a.who_key ? open('person', a.project, a.who_key) : act('goto', 'recovery');
  const body = `<a class="need ${a.tone}" style="display:block" ${go2}><div class="b">${esc(a.title)}</div><div class="sm mut">${esc(a.who)}${a.project ? ' · ' + esc(a.project) : ''}${a.note ? ' — ' + esc(a.note) : ''}</div></a>`;
  return a.action ? `<div>${body}<div style="margin:-4px 0 10px"><button class="s pri" ${act('needAct', a.action.url, a.action.label)}>${esc(a.action.label)}</button></div></div>` : body;
};
const projCard = p => `<a class="card click" href="#/projects/${encodeURIComponent(p.name)}"><div class="row"><h2 class="clip sp">${esc(p.name)}</h2>${p.status === 'active' ? health(p.health) : tag(cap(p.status))}</div>
  <p class="sm mut" style="margin:4px 0 12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(p.goal)}</p>
  <div class="row sm"><span class="b">${p.progress}%</span>${bar(p.progress, p.progress === 100 ? 'ok' : '')}</div>
  <div class="row sm mut" style="margin-top:10px"><span>${p.team} people</span><span>${p.counts.in_progress + p.counts.pending} open</span>${p.counts.review ? `<span>${p.counts.review} in review</span>` : ''}${p.counts.blocked ? `<span style="color:var(--warn)">${p.counts.blocked} blocked</span>` : ''}<span class="sp"></span><span>${p.counts.done} done</span></div></a>`;

const approvalCard = x => `<div class="need err"><div class="b">${x.agent ? esc(x.agent) + ' wants' : 'A chat wants'} to run a command that ${esc(x.reason)}</div>
  <div class="sm mut">${x.project ? esc(x.project) + ' · ' : ''}Nothing runs until you say yes. Only this exact command, once.</div><pre>${esc(x.command)}</pre>
  <div class="row" style="margin-top:8px"><button class="pri s" ${act('approve', x.id, '1')}>Approve and run</button><button class="s" ${act('allowSimilar', x.id)} title="Approve this one and every command of the same kind in this project, now and from now on">Allow all similar${x.similar > 1 ? ' (' + x.similar + ' waiting)' : ''}</button><button class="s" ${act('approve', x.id, '')}>Reject</button><span class="xs dim">expires ${hm(x.expires)}</span></div></div>`;

/* company: org chart, people, departments */
VIEWS.company = async (tab = 'chart') => {
  const o = await api('/company/org');
  if (!o.ok) return empty('Could not load the company', o.error.message);
  S.orgData = o;
  const head = `<div class="ph"><span class="sp"></span>
    <div class="seg">${[['chart', 'Org chart'], ['people', 'People'], ['departments', 'Departments'], ['story', 'Story']].map(([k, l]) => `<a ${act('ctab', k)} class="${tab === k ? 'on' : ''}">${l}</a>`).join('')}</div>
    <button ${act('newDept')}>New department</button><button class="pri" ${act('hire')}>${ic('plus')} Hire</button></div>`;
  if (!o.people.length) return head + `<div class="card">${empty('Your company has no employees yet', 'Start a project and the Master hires the team it needs. You can also hire people yourself.', `<button class="pri" ${act('newProject')}>Start a project</button>`)}</div>`;
  if (tab === 'people') return head + `<div class="card" style="padding:0"><table><tr><th>Person</th><th>Department</th><th>Project</th><th>Status</th><th>Now</th><th style="width:150px">Workload</th><th>Done</th></tr>
    ${o.people.filter(p => p.kind === 'agent').map(p => `<tr class="click" ${open('person', p.project, p.key)}><td>${who(p)}</td><td>${esc(p.department)}</td><td class="mut">${esc(p.project)}</td><td>${status(p.status)}</td><td class="sm mut clip" style="max-width:260px">${esc(p.activity)}</td>
      <td><div class="row sm">${bar(p.workload.pct, loadTone(p.workload.band))}<span class="mut" style="width:34px">${p.workload.pct}%</span></div></td><td>${p.performance.done}</td></tr>`).join('')}</table></div>`;
  if (tab === 'departments') return head + `<div class="grid gauto">${o.departments.map(d => `<a class="card click" ${open('dept', d.name)}><div class="row"><h2 class="sp">${esc(d.name)}</h2>${health(d.health)}</div>
      <p class="sm mut" style="margin:4px 0 12px;min-height:38px">${esc(d.mission || 'No mission written yet.')}</p>
      <div class="sm">${d.manager ? 'Led by <b>' + esc(d.manager) + '</b>' : '<span class="mut">No lead yet</span>'}</div>
      <div class="row sm mut" style="margin-top:8px"><span>${d.members} people</span><span>${d.tasks.active} active</span><span>${d.tasks.done} done</span><span class="sp"></span><span>load ${d.workload}%</span></div></a>`).join('')}</div>`;
  if (tab === 'story') return head + await VIEWS.activity();
  const projects = o.projects.filter(p => p.status !== 'archived');
  return head + `<div class="row wrap" style="margin-bottom:var(--s3)"><input id="orgq" placeholder="Find a person, role or department" style="max-width:280px" value="${esc(S.org.q)}">
      <select id="orgp" style="max-width:220px"><option value="">All projects</option>${projects.map(p => `<option ${S.org.project === p.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
      <span class="sp"></span><span class="xs mut">drag a person onto a manager or department to move them</span><button class="s" ${act('zoom', '-')}>−</button><button class="s" ${act('zoom', '0')}>${Math.round(S.org.zoom * 100)}%</button><button class="s" ${act('zoom', '+')}>+</button></div>
    <div class="orgwrap" id="orgwrap"><div class="org" id="org" style="transform:scale(${S.org.zoom})">${orgTree(o)}</div></div>`;
};
function orgTree(o) {
  const people = o.people.filter(p => !S.org.project || p.project === S.org.project);
  const q = S.org.q.trim().toLowerCase();
  const hit = p => q && [p.name, p.role, p.department, p.project].join(' ').toLowerCase().includes(q);
  const node = p => `<div class="node ${hit(p) ? 'hit' : q ? 'fade' : ''}" ${open('person', p.project, p.key)} data-id="${p.id}" data-boss="${p.manager_id}" ${p.kind === 'agent' ? 'draggable="true"' : ''} data-key="${esc(p.key)}" data-project="${esc(p.project)}" data-kind="${p.kind}" data-dept="${esc(p.department)}">
      <div class="who">${av(p.name, p.status)}<div><b class="clip">${esc(p.name)}</b><small class="clip">${esc(p.role)}</small></div></div>
      <div class="task clip">${esc(p.activity)}</div>${p.kind === 'agent' ? `<div class="row xs mut" style="margin-top:6px">${bar(p.workload.pct, loadTone(p.workload.band))}<span>${p.workload.pct}%</span></div>` : `<div class="xs mut" style="margin-top:6px">${esc(p.project)}</div>`}</div>`;
  const kids = (list, id) => {
    if (!list.length) return '';
    const closed = S.org.closed[id];
    return `<a class="tog" ${act('fold', id)} title="${closed ? 'Expand' : 'Collapse'}">${closed ? '+' : '−'}</a>` + (closed ? '' : `</div><ul>${list.join('')}</ul><div style="display:none">`);
  };
  const sub = p => { const reports = people.filter(x => x.manager_id === p.id && x.kind === 'agent'); const inner = kids(reports.map(sub), p.id); return `<li>${node(p).replace(/<\/div>$/, inner + '</div>')}</li>`; };
  const masters = people.filter(p => p.kind === 'master');
  const branch = m => {
    const direct = people.filter(x => x.manager_id === m.id && x.kind === 'agent');
    const depts = [...new Set(direct.map(x => x.department))].sort();
    const groups = depts.length > 1 ? depts.map(d => { const id = m.id + ':' + d, mem = direct.filter(x => x.department === d);
      return `<li><div class="node grp ${q && d.toLowerCase().includes(q) ? 'hit' : ''}" ${open('dept', d)} data-group="${esc(d)}" data-project="${esc(m.project)}"><b>${esc(d)}</b><div class="xs mut">${mem.length} people</div>${kids(mem.map(sub), id)}</div></li>`; }) : direct.map(sub);
    return `<li>${node(m).replace(/<\/div>$/, kids(groups, m.id) + '</div>')}</li>`;
  };
  const owner = (S.company || {}).owner || 'You';
  return `<ul><li><div class="node" style="cursor:default"><div class="who">${av(owner)}<div><b>${esc(owner)}</b><small>Owner</small></div></div>${masters.length ? `</div><ul>${masters.map(branch).join('')}</ul><div style="display:none">` : ''}</div></li></ul>`;
}
AFTER2.company = () => {
  const w = $('orgwrap'); if (!w) return;
  let drag = null;
  w.onmousedown = e => { if (e.target.closest('.node')) return; drag = {x: e.clientX, y: e.clientY, l: w.scrollLeft, t: w.scrollTop}; w.classList.add('drag'); };
  window.onmouseup = () => { drag = null; w.classList.remove('drag'); };
  w.onmousemove = e => { if (drag) { w.scrollLeft = drag.l - (e.clientX - drag.x); w.scrollTop = drag.t - (e.clientY - drag.y); } };
  w.onwheel = e => { if (e.ctrlKey) { e.preventDefault(); ACT.zoom({a0: e.deltaY < 0 ? '+' : '-'}); } };
  w.onmouseover = e => { const n = e.target.closest('.node[data-id]'); w.querySelectorAll('.node.line').forEach(x => x.classList.remove('line')); let cur = n; while (cur && cur.dataset.boss) { cur = w.querySelector(`.node[data-id="${cur.dataset.boss}"]`); if (cur) cur.classList.add('line'); } };
  if (!S.org.scrolled) { w.scrollLeft = (w.scrollWidth - w.clientWidth) / 2; S.org.scrolled = 1; } else { w.scrollLeft = S.org.l || 0; w.scrollTop = S.org.t || 0; }
  w.onscroll = () => { S.org.l = w.scrollLeft; S.org.t = w.scrollTop; };
const BUSY = v => { state.busy = v; };
  /* drag a person onto a manager, the Master or a department to move them there */
  let dragged = null;
  const clear = () => w.querySelectorAll('.node.drop').forEach(x => x.classList.remove('drop'));
  w.ondragstart = e => { const n = e.target.closest('.node[draggable="true"]'); if (!n) return; dragged = n.dataset; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', n.dataset.key); BUSY(true); };
  w.ondragend = () => { dragged = null; clear(); BUSY(false); };
  w.ondragover = e => { const t = e.target.closest('.node[data-key], .node[data-group]'); clear();
    if (!dragged || !t || t.dataset.project !== dragged.project || t.dataset.key === dragged.key) return; e.preventDefault(); t.classList.add('drop'); };
  w.ondrop = async e => { const t = e.target.closest('.node[data-key], .node[data-group]'); clear(); if (!dragged || !t) return; e.preventDefault();
    const who = dragged, body = t.dataset.group ? {team: t.dataset.group, manager: 'master'} : t.dataset.kind === 'master' ? {manager: 'master'} : {manager: t.dataset.key, team: t.dataset.dept};
    dragged = null; BUSY(false);
    const r = await post(`/projects/${encodeURIComponent(who.project)}/agents/${encodeURIComponent(who.key)}/reassign`, body);
    if (r.ok === false) return toast(r.error.message + ' ' + (r.error.fix || ''), true);
    toast(t.dataset.group ? `Moved to ${t.dataset.group}` : `Now reports to ${t.dataset.kind === 'master' ? 'the Master' : t.querySelector('b').textContent}`);
    S.orgData = null; render(true); };
  const redraw = () => { $('org').innerHTML = orgTree(S.orgData); };
  $('orgq').oninput = e => { S.org.q = e.target.value; redraw(); const h = w.querySelector('.node.hit'); if (h) h.scrollIntoView({block: 'center', inline: 'center', behavior: 'smooth'}); };
  $('orgp').onchange = e => { S.org.project = e.target.value; redraw(); };
};

/* tasks */
const tk = t => t.plan_step ? `<div class="tk plan"><div class="t">${esc(t.title)}</div><div class="xs mut">${esc(t.id)} · ${esc(t.agent)}</div></div>`
  : `<div class="tk" ${open('task', t.id)}><div class="t">${esc(t.title)}</div><div class="row xs mut">${av(t.agent, '', 'sm')}<span class="clip sp">${esc(t.agent)}</span>${t.priority <= 2 ? `<span class="pri${t.priority}">P${t.priority}</span>` : ''}${t.stalled ? tag('stalled', 'warn') : ''}${t.reworked ? tag('reworked') : ''}</div>
    ${t.status === 'in_progress' || t.status === 'review' ? `<div class="row xs mut" style="margin-top:8px">${bar(t.progress)}<span>${t.progress}%</span></div>` : ''}${S.board.project ? '' : `<div class="xs dim" style="margin-top:6px">${esc(t.project)}</div>`}</div>`;
async function boardHtml() {
  const b = await api('/company/board?' + qs(S.board));
  if (!b.total) return `<div class="card">${empty('Your teams are currently clear', 'No tasks exist yet. The Master assigns work after it wrote the plan, or you can assign a task yourself.')}</div>`;
  return `<div class="board">${b.columns.map(c => `<div class="lane"><h3>${c.label} <span>${c.tasks.length}</span></h3>${c.tasks.map(tk).join('') || '<div class="xs dim">—</div>'}</div>`).join('')}</div>`;
}
VIEWS.tasks = async () => {
  const o = await api('/company/org');
  delete S.board.project;
  const sel = (id, label, list, cur) => `<select id="${id}" style="max-width:200px"><option value="">${label}</option>${list.map(x => `<option ${cur === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>`;
  if (S.board.projectName) S.board.project = S.board.projectName;
  return `<div class="ph"><span class="sp"></span>
    ${sel('bp', 'All projects', (o.projects || []).map(p => p.name), S.board.projectName)}${sel('bd', 'All departments', (o.departments || []).map(d => d.name), S.board.department)}<button class="pri" ${act('assign', S.board.projectName || '')}>${ic('plus')} Assign task</button></div>` + await boardHtml();
};
AFTER2.tasks = () => { $('bp').onchange = e => { S.board.projectName = e.target.value; render(); }; $('bd').onchange = e => { S.board.department = e.target.value; render(); }; };

/* activity */
const CATS = [['all', 'All'], ['company', 'Company'], ['projects', 'Projects'], ['agents', 'People'], ['tasks', 'Tasks'], ['messages', 'Messages'], ['errors', 'Problems'], ['decisions', 'Decisions'], ['deliverables', 'Deliverables']];
VIEWS.activity = async () => {
  const f = await api('/company/activity?' + qs({category: S.feed.category, limit: 150}));
  return `<div class="ph"><span class="sp"></span><div class="seg">${CATS.map(([k, l]) => `<a ${act('feedCat', k)} class="${S.feed.category === k ? 'on' : ''}">${l}</a>`).join('')}</div></div>
    <div class="card">${(f.items || []).length ? feed(f.items) : empty('Nothing has happened yet', 'Your company is waiting for its first assignment.')}
    <div class="xs mut" style="margin-top:var(--s3)">Raw events, tool calls and traces are on the <a ${act('goto', 'activity')} style="text-decoration:underline">Activity</a> page.</div></div>`;
};

/* knowledge */
const memoryHtml = (m, inProject) => `<div class="grid g2"><div class="card"><h3>What people know</h3>${m.people.length ? `<div class="list">${m.people.map(x => `<div style="align-items:flex-start">${av(x.who, '', 'sm')}<div class="sp"><div class="sm"><a class="b" ${open('person', x.project, x.who_key)}>${esc(x.who)}</a> <span class="mut">${esc(x.label)}${inProject ? '' : ' · ' + esc(x.project)}</span></div><div class="prose sm">${esc(x.text)}</div></div></div>`).join('')}</div>` : '<div class="mut sm">Nobody has written anything into their own memory yet. Agents do it as they learn; you can add tips on a person\'s profile.</div>'}</div>
  <div class="card"><h3>What the project${inProject ? '' : 's'} know${inProject ? 's' : ''}</h3>${m.projects.length ? `<div class="list">${m.projects.map(x => `<div style="align-items:flex-start"><div class="sp"><div class="sm"><b>${esc(x.title || cap(x.kind))}</b> ${tag(cap(x.kind))} ${inProject ? '' : `<span class="mut">${esc(x.project)}</span>`}</div><div class="prose sm mut" style="max-height:120px;overflow:auto">${esc(x.text)}</div></div></div>`).join('')}</div>` : '<div class="mut sm">No project memory yet.</div>'}</div></div>`;
VIEWS.knowledge = async () => {
  const head = `<div class="ph"><span class="sp"></span>
    <div class="seg">${[['company', 'Company'], ['memory', 'People & projects']].map(([k, l]) => `<a ${act('knowTab', k)} class="${S.know.tab === k ? 'on' : ''}">${l}</a>`).join('')}</div><input id="kq" placeholder="Search knowledge" style="max-width:240px" value="${esc(S.know.q)}">${S.know.tab === 'company' ? `<button class="pri" ${act('newKnow')}>${ic('plus')} Add</button>` : ''}</div>`;
  if (S.know.tab === 'memory') return head + memoryHtml(await api('/company/memory?' + qs({q: S.know.q})));
  const k = await api('/company/knowledge?' + qs({q: S.know.q, category: S.know.cat}));
  return head + `<div class="grid" style="grid-template-columns:230px minmax(0,1fr)"><div class="card"><div class="list"><a ${act('knowCat', '')} class="${S.know.cat ? '' : 'b'}">Everything</a>${k.categories.map(c => `<a ${act('knowCat', c.name)} class="${S.know.cat === c.name ? 'b' : ''}"><span class="sp">${esc(c.name)}</span><span class="dim">${c.count || ''}</span></a>`).join('')}</div></div>
    <div class="col">${k.items.length ? k.items.map(x => `<div class="card"><div class="row"><h2 class="sp">${esc(x.title)}</h2>${tag(x.category)}${x.source === 'owner' ? `<button class="quiet s" ${act('delKnow', x.id)}>Delete</button>` : tag({decision: 'from a decision', report: 'project summary'}[x.source] || x.source)}</div><div class="prose sm" style="margin-top:8px">${esc(x.text)}</div><div class="xs dim" style="margin-top:8px">${x.project ? esc(x.project) + ' · ' : ''}${ago(x.updated)}</div></div>`).join('')
      : `<div class="card">${empty('No company knowledge yet', 'Write the rules and standards every team must follow. Every chat reads them first. Your decisions and finished projects are added here automatically.', `<button class="pri" ${act('newKnow')}>Add the first rule</button>`)}</div>`}</div></div>`;
};
AFTER2.knowledge = () => { const i = $('kq'); i.onkeydown = e => { if (e.key === 'Enter') { S.know.q = i.value; render(); } }; };

/* deliverables */
const filesHtml = a => !(a.items || []).length ? `<div class="card">${empty('No deliverables yet', 'Files the team produces and the screenshots of visual work appear here.')}</div>`
  : `<div class="card" style="padding:0"><table><tr><th>File</th><th>Produced by</th><th>For</th><th>Project</th><th>When</th></tr>${a.items.map(f => `<tr><td>${f.stored ? `<a class="b" href="/api/v1/files/${f.id}" target="_blank">${esc(f.name)}</a> <span class="dim xs">${size(f.size)}</span>` : `<span class="b">${esc(f.name)}</span>`}<div class="xs dim mono clip" style="max-width:420px">${esc(f.path)}</div></td>
      <td>${f.by_key ? `<a ${open('person', f.project, f.by_key)}>${esc(f.by)}</a>` : esc(f.by)}</td><td>${f.task_id ? `<a ${open('task', f.task_id)}>${esc(f.task)}</a> ${f.accepted ? tag('accepted', 'ok') : ''}` : '<span class="mut">message</span>'}</td><td class="mut">${esc(f.project)}</td><td class="mut sm">${ago(f.ts)}</td></tr>`).join('')}</table></div>`;
VIEWS.files = async () => `<div class="ph"><span class="sp"></span></div>` + filesHtml(await api('/company/artifacts'));

/* reports */
const reportHtml = r => !r ? empty('No report', '') : `<div class="card"><div class="row"><h2 class="sp">${esc(r.title)}</h2><button class="s" ${act('copyReport')}>Copy as text</button></div>
  <div class="stats" style="margin:var(--s4) 0">${[['completed', 'completed'], ['active', 'active'], ['blocked', 'blocked'], ['failed', 'failed'], ['people', 'people']].map(([k, l]) => `<div class="stat"><b>${r.numbers[k]}</b><span>${l}</span></div>`).join('')}</div>
  <div class="grid g3">${[['Achievements', r.achievements, 'Nothing was completed in this period.'], ['Needs attention', r.attention, 'Nothing needs attention.'], ['Next', r.next, 'No open work.']].map(([h, items, none]) => `<div><h3 style="margin-bottom:8px">${h}</h3>${items.length ? `<ul style="margin:0;padding-left:18px">${items.map(i => `<li class="sm" style="margin-bottom:4px">${esc(i)}</li>`).join('')}</ul>` : `<div class="mut sm">${none}</div>`}</div>`).join('')}</div>
  ${r.people.length > 1 ? `<h3 style="margin:var(--s5) 0 8px">People</h3><table>${r.people.map(p => `<tr class="click" ${open('person', p.project, p.key)}><td>${who(p)}</td><td class="mut">${esc(p.project)}</td><td>${p.done} done</td><td>${p.open} open</td><td>${status(p.status)}</td></tr>`).join('')}</table>` : ''}<textarea id="reptext" style="display:none">${esc(r.text)}</textarea></div>`;
VIEWS.reports = async () => {
  const o = await api('/company/org');
  const r = S.rep, extra = r.kind === 'project' ? `<select id="rarg" style="max-width:220px">${(o.projects || []).map(p => `<option ${r.project === p.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>`
    : r.kind === 'department' ? `<select id="rarg" style="max-width:220px">${(o.departments || []).map(d => `<option ${r.department === d.name ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select>` : '';
  if (r.kind === 'project' && !r.project) r.project = ((o.projects || [])[0] || {}).name;
  if (r.kind === 'department' && !r.department) r.department = ((o.departments || [])[0] || {}).name;
  const rep = await api('/company/report?' + qs({kind: r.kind, project: r.kind === 'project' ? r.project : '', department: r.kind === 'department' ? r.department : ''}));
  return `<div class="ph"><span class="sp"></span><div class="seg">${[['daily', 'Today'], ['weekly', 'This week'], ['project', 'Project'], ['department', 'Department']].map(([k, l]) => `<a ${act('repKind', k)} class="${r.kind === k ? 'on' : ''}">${l}</a>`).join('')}</div>${extra}</div>`
    + (rep.ok ? reportHtml(rep.report) : `<div class="card">${empty('No report', rep.error.message)}</div>`);
};
AFTER2.reports = () => { if ($('rarg')) $('rarg').onchange = e => { S.rep[S.rep.kind] = e.target.value; render(); }; };

/* decisions */
VIEWS.decisions = async () => {
  const d = await api('/company/decisions');
  if (!d.ok) return empty('Could not load decisions', d.error.message);
  S.decisions = d.questions.length + d.approvals.length;
  const qcard = x => { const left = Math.round((x.deadline - Date.now() / 1000) / 60);
    return `<div class="card"><div class="row"><span class="who">${av(x.from_person || x.from)}<div><b>${esc(x.from_person || x.from)}</b><small>${esc(x.from)} · ${esc(x.project)}</small></div></span><span class="sp"></span>${x.status === 'escalated' ? tag('decided without you; your answer still wins', 'warn') : tag(left > 0 ? left + ' min left' + (x.recommendation ? ', then the recommendation is applied' : '') : 'deciding now', 'accent')}</div>
      <div style="font-size:15px;margin:12px 0 6px" class="b">${esc(x.question)}</div>${x.recommendation ? `<div class="sm mut">Recommended: ${esc(x.recommendation)}</div>` : ''}
      ${x.options.length ? `<div class="row wrap" style="margin-top:10px">${x.options.map(o => `<button ${act('answer', x.id, o)}>${esc(o)}${x.recommendation && x.recommendation.toLowerCase().startsWith(o.toLowerCase()) ? ' ★' : ''}</button>`).join('')}</div>` : ''}
      <div class="row" style="margin-top:10px"><input id="q_${x.id}" placeholder="Or write your own answer"><button class="pri" ${act('answer', x.id)}>Answer</button></div></div>`; };
  const none = !d.questions.length && !d.approvals.length;
  return `<div class="ph"><span class="sp"></span></div>
    <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)"><div class="col">${none ? `<div class="card">${empty('Nothing is waiting for you', 'When the Master or a lead needs a decision that is yours, or an agent wants to run a risky command, it appears here.')}</div>` : ''}
      ${d.approvals.length ? `<div class="card"><h3>Commands waiting for your approval</h3>${d.approvals.map(approvalCard).join('')}</div>` : ''}${d.questions.map(qcard).join('')}
      ${(d.approval_rules || []).length ? `<div class="card"><h3>Always allowed</h3><div class="xs mut" style="margin-bottom:6px">Commands of these kinds run without asking you. Each one is still listed under Decided.</div><div class="list">${d.approval_rules.map(x => `<div><div class="sp sm">Commands that <b>${esc(x.reason)}</b>${x.project ? ' · ' + esc(x.project) : ''}</div><button class="s" ${act('ruleOff', x.id)}>Ask me again</button></div>`).join('')}</div></div>` : ''}
      <div class="card"><h3>Decided</h3>${d.history.length || d.approval_history.length ? `<div class="list">${d.history.map(x => `<div style="align-items:flex-start"><div class="sp"><div class="b sm">${esc(x.question)}</div><div class="sm mut prose">${esc(x.answer)}</div></div>${x.answered_by === 'client' ? tag('you', 'ok') : `<span class="row" style="gap:6px">${tag(x.answered_by === 'auto' ? 'automatic: you did not answer' : 'the Master', 'warn')}<button class="s" ${act('overrule', x.id)}>Overrule</button></span>`}</div>`).join('')}
        ${d.approval_history.map(x => `<div style="align-items:flex-start"><div class="sp"><div class="mono clip" style="max-width:520px">${esc(x.command.slice(0, 160))}</div><div class="xs mut">${esc(x.agent || x.plugin)} · ${esc(x.reason)} ${x.result ? '· ' + esc(x.result.slice(0, 80)) : ''}</div></div>${tag(cap(x.status) + (x.by_rule ? ' · by your rule' : ''), {executed: 'ok', approved: 'ok', rejected: 'err'}[x.status] || '')}</div>`).join('')}</div>` : '<div class="mut sm">No decisions have been taken yet. They are also recorded in Knowledge.</div>'}</div></div>
      <div class="col"><div class="card"><h3>Also worth a look</h3>${d.attention.map(needCard).join('') || '<div class="mut sm">No blockers, errors or overloads.</div>'}</div></div></div>`;
};

/* ---------- drawers ---------- */
async function drawer(kind, a0, a1, quiet) {
  S.drawer = {kind, args: [a0, a1]};
  const el = $('drawer');
  if (!quiet) el.innerHTML = `<div class="scrim" data-close="1"></div><div class="drawer"><div class="c mut">Loading…</div></div>`;
  const html = await DRAWERS[kind](a0, a1);
  if (!S.drawer || S.drawer.kind !== kind) return;
  const keep = el.querySelector('.drawer>.c'), top = keep ? keep.scrollTop : 0;
  el.innerHTML = `<div class="scrim" data-close="1"></div><div class="drawer ${quiet ? '' : 'fresh'}">${html}</div>`;
  if (quiet && el.querySelector('.drawer>.c')) el.querySelector('.drawer>.c').scrollTop = top;
}
const closeDrawer = () => { S.drawer = null; $('drawer').innerHTML = ''; };
const DRAWERS = {};
DRAWERS.person = async (project, key) => {
  const [r, sk, ai] = await Promise.all([api(`/company/people/${encodeURIComponent(project)}/${encodeURIComponent(key)}`),
    api(`/projects/${encodeURIComponent(project)}/agents/${encodeURIComponent(key)}/skills`), api('/ai')]);
  if (!r.ok) return `<div class="c">${empty('Not found', r.error.message)}</div>`;
  const skills = sk.skills || [], recommended = (sk.recommended || []).filter(x => !skills.some(s => s.id === x)), providers = ai.providers || [];
  const p = r.person, pf = p.performance, isAgent = p.kind === 'agent', e = encodeURIComponent(project);
  const openTasks = p.tasks.filter(t => !['done', 'cancelled'].includes(t.status));
  const kinds = {};
  p.memory.forEach(m => (kinds[m.label] = kinds[m.label] || []).push(m));
  return `<div class="h">${av(p.name, p.status, 'lg')}<div class="sp"><h1>${esc(p.name)}</h1><div>${esc([cap(p.seniority), p.role].filter(Boolean).join(' '))}</div>
      <div class="sm mut">${esc(p.department)} · reports to ${p.manager_key ? `<a ${open('person', project, p.manager_key)} style="text-decoration:underline">${esc(p.manager_name)}</a>` : esc(p.manager_name)} · <a ${act('goProject', project)} data-close="1" style="text-decoration:underline">${esc(project)}</a></div>
      <div style="margin-top:8px">${status(p.status)} <span class="sm">— ${esc(p.activity)}</span></div></div><button class="quiet" data-close="1">✕</button></div>
    <div class="c"><div class="row wrap" style="margin-bottom:var(--s5)"><button class="pri" ${act('talk', project, key)}>Message</button>${isAgent ? `<button ${act('assign', project, key)}>Assign task</button>` : ''}${p.chat && p.chat.url ? `<a class="btn" href="${esc(p.chat.url)}" target="_blank">Open chat</a>` : ''}
        ${isAgent ? (p.state === 'suspended' || p.state === 'archived' ? `<button ${act('agent', project, key, 'restore')}>Restore</button>` : `<button ${act('agent', project, key, 'suspend')}>Suspend</button>`) : ''}<button class="quiet" ${act('goProject', project, 'team')} data-close="1">Edit profile</button></div>
      ${isAgent ? `<section><h3>Workload and record</h3><div class="row sm" style="margin-bottom:10px">${bar(p.workload.pct, loadTone(p.workload.band))}<b>${p.workload.pct}%</b><span class="mut">${cap(p.workload.band)}</span></div>
        <div class="stats"><div class="stat"><b>${p.workload.active}</b><span>active</span></div><div class="stat"><b>${p.workload.waiting}</b><span>in review</span></div><div class="stat"><b>${p.workload.blocked}</b><span>blocked</span></div><div class="stat"><b>${pf.done}</b><span>completed</span></div>
        <div class="stat"><b>${pf.success_rate == null ? '–' : pf.success_rate + '%'}</b><span>success rate</span></div><div class="stat"><b>${pf.first_pass_rate == null ? '–' : pf.first_pass_rate + '%'}</b><span>accepted first time</span></div><div class="stat"><b>${pf.avg_hours == null ? '–' : pf.avg_hours < 1 ? Math.round(pf.avg_hours * 60) + ' min' : pf.avg_hours + ' h'}</b><span>average per task</span></div></div></section>` : ''}
      <section><h3>About</h3>${p.career ? `<div class="sm">${esc(p.career)}</div>` : ''}${p.responsibilities ? `<div class="prose sm mut" style="margin-top:6px;max-height:160px;overflow:auto">${esc(p.responsibilities)}</div>` : ''}
        ${p.personality ? `<div class="sm" style="margin-top:8px"><span class="mut">How they work:</span> ${esc(p.personality)}</div>` : ''}${(p.skills || []).length ? `<div class="row wrap" style="margin-top:10px;gap:6px">${p.skills.map(s => tag(s)).join('')}</div>` : ''}</section>
      <section><h3>AI</h3><div class="row wrap"><select id="ai_p" style="max-width:240px"><option value="">Default (${esc(ai.default || 'chatgpt')})</option>${providers.map(x => `<option value="${x.key}" ${p.provider === x.key ? 'selected' : ''}>${esc(x.label)}${x.ready ? '' : ' — not set up'}</option>`).join('')}</select>
          <input id="ai_m" list="ai_ml" placeholder="Model (empty = that AI's default)" value="${esc(p.model || '')}" style="max-width:230px"><datalist id="ai_ml"></datalist>
          <select id="ai_e" style="max-width:150px" title="How hard it thinks before it answers">${[['', 'Effort: default (' + (p.effort_used || 'medium') + ')'], ['low', 'Effort: low'], ['medium', 'Effort: medium'], ['high', 'Effort: high']].map(([v, l]) => `<option value="${v}" ${(p.effort || '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <button class="s" ${act('aiModels')}>Models</button><button class="s" ${act('aiTest')}>Test</button><button class="pri s" ${act('aiSave', project, key)}>Save</button></div>
        <div class="xs mut" style="margin-top:6px">Runs on <b>${esc(p.ai || 'chatgpt')}</b>${p.model ? ' · ' + esc(p.model) : ''}${p.effort ? ' · effort ' + esc(p.effort) : ''}. A change applies from this agent's next chat. Model and effort are sent to API providers; in a ChatGPT browser chat, effort switches "Think" on (medium, high) or off (low), and the model is the one selected in that site. Keys: Settings › ai.</div></section>
      <section><h3>Skills</h3>${skills.map(s => `<div class="row sm" style="margin-bottom:8px;align-items:flex-start"><div class="sp"><b>${esc(s.name)}</b> <span class="dim xs">${esc(s.source)}</span><div class="mut">${esc((s.description || '').slice(0, 170))}</div></div><a class="xs dim" ${act('skillDrop', project, key, s.id)}>remove</a></div>`).join('') || '<div class="mut sm">No skills yet. A skill is a how-to guide the agent reads at the start of every chat.</div>'}
        <div class="row wrap" style="margin-top:8px;gap:6px">${recommended.map(x => `<button class="s" ${act('skillAdd', project, key, x)} title="${esc(x)}">+ ${esc(x.split(':')[1])}</button>`).join('')}<input id="sk_ref" placeholder="owner/repo:skill, or a skills.sh link" style="max-width:250px"><button class="s" ${act('skillAdd', project, key)}>Add</button></div></section>
      <section><h3>Current work</h3>${openTasks.length ? `<div class="list">${openTasks.map(t => `<a ${open('task', t.id)}><div class="sp clip">${esc(t.title)}</div>${t.status === 'in_progress' ? `<span class="xs mut">${t.progress}%</span>` : ''}${tag(TASK[t.status], TONE[t.status])}</a>`).join('')}</div>` : '<div class="mut sm">No assigned work.</div>'}</section>
      ${p.reports.length ? `<section><h3>${isAgent ? 'Manages' : 'Direct reports'}</h3><div class="list">${p.reports.map(x => `<a ${open('person', project, x.key)}>${who(x)}<span class="sp"></span>${status(x.status)}</a>`).join('')}</div></section>` : ''}
      ${p.kind === 'agent' ? `<section><div class="row"><button class="s" ${act('reuse', project, key)}>Use in another project</button><span class="xs mut">Arrives with name, skills and what they learned (tips, lessons). Nothing about this project goes along.</span></div></section>` : ''}
      <section><h3>What ${esc(p.name.split(' ')[0])} knows <span class="sp"></span><a class="xs" ${act('addMemory', project, key)}>add a tip</a></h3>${p.memory.length ? Object.entries(kinds).map(([label, items]) => `<div class="sm b" style="margin:8px 0 4px">${esc(label)}</div>${items.slice(0, 6).map(m => `<div class="row sm" style="align-items:flex-start;margin-bottom:4px"><span class="prose sp">${esc(m.text)}</span><a class="xs dim" ${act('delMemory', m.id)}>remove</a></div>`).join('')}`).join('') : '<div class="mut sm">Nothing yet. People write down lessons and facts as they work; your tips are read at the start of each of their chats.</div>'}</section>
      <section><h3>Recent messages</h3>${p.messages.length ? p.messages.slice(-6).map(m => `<div class="sm" style="margin-bottom:8px"><b>${esc(m.from)}</b> <span class="mut">to ${esc(m.to)} · ${ago(m.ts)}</span><div class="mut clip">${esc(m.text.slice(0, 200))}</div></div>`).join('') : '<div class="mut sm">No messages yet.</div>'}</section>
      ${p.deliverables.length ? `<section><h3>Deliverables</h3>${p.deliverables.slice(0, 8).map(f => `<div class="sm clip">${f.stored ? `<a href="/api/v1/files/${f.id}" target="_blank" style="text-decoration:underline">${esc(f.name)}</a>` : esc(f.name)} <span class="dim">${esc(f.task)}</span></div>`).join('')}</section>` : ''}
      <section><h3>Timeline</h3>${personFeed(p)}</section></div>`;
};
const personFeed = p => { const items = Array.isArray(p.timeline) ? p.timeline : []; return items.length ? feed(items, {noAgent: 1, noProject: 1}) : '<div class="mut sm">No recorded activity yet.</div>'; };
DRAWERS.task = async id => {
  const r = await api('/company/tasks/' + encodeURIComponent(id));
  if (!r.ok) return `<div class="c">${empty('Task not found', r.error.message)}</div>`;
  const t = r.task;
  return `<div class="h"><div class="sp"><div class="xs mut">${esc(t.id)} · <a ${act('goProject', t.project)} data-close="1">${esc(t.project)}</a>${t.plan_step ? ' · ' + esc(t.plan_step) : ''}</div><h1>${esc(t.title)}</h1>
      <div style="margin-top:6px">${tag(TASK[t.status], TONE[t.status])} ${t.priority <= 2 ? tag('Priority ' + t.priority, 'warn') : ''} ${t.visual ? tag('visual') : ''} ${t.stalled ? tag('stalled', 'warn') : ''}</div></div><button class="quiet" data-close="1">✕</button></div>
    <div class="c"><section><div class="flowline">${t.flow.map((f, i) => (i ? '<i></i>' : '') + `<span class="${f.bad ? 'bad' : f.reached ? 'on' : ''}">${f.label}</span>`).join('')}</div>
        ${t.status === 'in_progress' || t.status === 'review' ? `<div class="row sm" style="margin-top:12px">${bar(t.progress)}<b>${t.progress}%</b></div>` : ''}</section>
      <section><div class="grid g2 sm"><div><span class="mut">Assigned to</span><br>${t.agent_key ? `<a class="b" ${open('person', t.project, t.agent_key)}>${esc(t.agent)}</a> <span class="mut">${esc(t.agent_role)}</span>` : esc(t.agent)}</div><div><span class="mut">Department</span><br>${esc(t.department || '–')}</div>
        <div><span class="mut">Given by</span><br>${esc(t.by)}</div><div><span class="mut">Manager</span><br>${esc(t.manager)}</div><div><span class="mut">Started</span><br>${t.started ? ago(t.started) : 'not yet'}</div><div><span class="mut">Finished</span><br>${t.finished ? ago(t.finished) : 'not yet'}</div></div></section>
      ${t.status === 'blocked' || t.status === 'failed' ? `<section class="need ${t.status === 'failed' ? 'err' : 'warn'}" style="display:block"><h3>This task stands still</h3>
        <div class="sm">${esc(t.agent)} ${t.status === 'failed' ? 'could not do it' : 'cannot go on'}. The reason is under "Result" below. It is normally the Master's (or the lead's) job to solve it; you can also do it here.</div>
        <textarea id="tk_note" rows="3" style="width:100%;margin-top:8px" placeholder="Your answer or instruction (optional)"></textarea>
        <div class="row wrap" style="margin-top:8px"><button class="pri s" ${act('taskAct', t.id, 'unblock')}>Answer and continue</button><button class="s" ${act('taskAct', t.id, 'escalate')}>Tell the Master to solve it now</button><button class="s" ${act('taskAct', t.id, 'cancel')}>Cancel the task</button></div></section>` : ''}
      <section><h3>What to do</h3><div class="prose sm">${esc(t.instructions)}</div></section>
      ${t.acceptance.length ? `<section><h3>Done when</h3><ul style="margin:0;padding-left:18px">${t.acceptance.map(a => `<li class="sm">${esc(a)}</li>`).join('')}</ul></section>` : ''}
      ${t.result_summary ? `<section><h3>Result</h3><div class="prose sm">${esc(t.result_summary)}</div>${t.result_details ? `<pre style="margin-top:8px">${esc(t.result_details)}</pre>` : ''}${t.result_files.length ? `<div class="xs mono mut" style="margin-top:8px">${t.result_files.map(esc).join('<br>')}</div>` : ''}
        ${t.images.map(f => `<a href="/api/v1/files/${f.file_id}" target="_blank"><img src="/api/v1/files/${f.file_id}" style="max-width:100%;border-radius:8px;border:1px solid var(--line);margin-top:10px"></a>`).join('')}</section>` : ''}
      ${t.review_note || t.visual_review ? `<section><h3>Review</h3><div class="prose sm">${esc(t.review_note)}</div>${t.visual_review ? `<div class="prose sm mut" style="margin-top:6px">${esc(t.visual_review)}</div>` : ''}</section>` : ''}
      <section><h3>Conversation</h3>${t.thread.length ? t.thread.map(m => `<div class="sm" style="margin-bottom:10px"><b>${esc(m.from_hub ? 'Operations' : m.from)}</b> <span class="mut">to ${esc(m.to)} · ${ago(m.ts)}</span><div class="prose mut" style="max-height:120px;overflow:auto">${esc(m.text)}</div></div>`).join('') : '<div class="mut sm">No messages on this task.</div>'}</section>
      <section><h3>History</h3>${t.timeline.length ? feed(t.timeline, {noProject: 1}) : '<div class="mut sm">No events yet.</div>'}</section></div>`;
};
DRAWERS.dept = async name => {
  const r = await api('/company/departments/' + encodeURIComponent(name));
  if (!r.ok) return `<div class="c">${empty('Department not found', r.error.message)}</div>`;
  const d = r.department;
  return `<div class="h"><div class="sp"><div class="xs mut">Department</div><h1>${esc(d.name)}</h1><div style="margin-top:6px">${health(d.health)} <span class="sm mut">— ${esc(d.why)}</span></div></div><button class="quiet" data-close="1">✕</button></div>
    <div class="c"><section><h3>Mission <span class="sp"></span><a class="xs" ${act('newDept', d.name, d.mission)}>edit</a></h3><div class="prose">${esc(d.mission || 'No mission written yet.')}</div></section>
      <section><div class="stats"><div class="stat"><b>${d.members}</b><span>people</span></div><div class="stat"><b>${d.tasks.active}</b><span>active tasks</span></div><div class="stat"><b>${d.tasks.blocked}</b><span>blocked</span></div><div class="stat"><b>${d.tasks.done}</b><span>completed</span></div>
        <div class="stat"><b>${d.workload}%</b><span>average load</span></div><div class="stat"><b>${d.performance.first_pass_rate == null ? '–' : d.performance.first_pass_rate + '%'}</b><span>accepted first time</span></div></div></section>
      <section><h3>People</h3>${d.people.length ? `<div class="list">${d.people.map(p => `<a ${open('person', p.project, p.key)}>${who(p, p.role + (p.level === 'lead' ? ' · lead' : ''))}<div class="sp xs mut clip" style="text-align:right">${esc(p.activity)}</div><span style="width:70px" class="row xs mut">${bar(p.workload.pct, loadTone(p.workload.band))}</span></a>`).join('')}</div>` : `<div class="mut sm">Nobody works here yet.</div><button class="s" style="margin-top:8px" ${act('hire', '', d.name)}>Hire into ${esc(d.name)}</button>`}</section>
      <section><h3>Projects</h3><div class="row wrap">${d.projects.map(p => `<a class="tag" ${act('goProject', p)} data-close="1">${esc(p)}</a>`).join('') || '<span class="mut sm">None</span>'}</div></section>
      <section><h3>Work</h3>${d.task_list.length ? `<div class="list">${d.task_list.slice(0, 15).map(t => `<a ${open('task', t.id)}><div class="sp clip">${esc(t.title)}<div class="xs mut">${esc(t.agent)}</div></div>${tag(TASK[t.status], TONE[t.status])}</a>`).join('')}</div>` : '<div class="mut sm">No tasks yet.</div>'}</section>
      <section><h3>Recent activity</h3>${d.activity.length ? feed(d.activity) : '<div class="mut sm">Nothing yet.</div>'}</section></div>`;
};

/* ---------- modals ---------- */
const modal = html => { $('modal').innerHTML = html ? `<div class="scrim" data-mclose="1" style="z-index:30"></div><div class="modal">${html}</div>` : ''; const f = $('modal').querySelector('input,textarea,select'); if (f) f.focus(); };
const val = id => ($(id) ? $(id).value.trim() : '');
const ACT = {
  goto: d => go(d.a0), ctab: d => { S.ctab = d.a0; render(); },
  goProject: d => { state.project = d.a0; const sel = document.getElementById('proj'); if (sel) sel.value = d.a0; go(d.a1 || 'agents'); },
  zoom: d => { S.org.zoom = d.a0 === '0' ? 1 : Math.max(.4, Math.min(1.6, S.org.zoom + (d.a0 === '+' ? .1 : -.1))); render(); },
  fold: d => { S.org.closed[d.a0] = !S.org.closed[d.a0]; $('org').innerHTML = orgTree(S.orgData); },
  feedCat: d => { S.feed.category = d.a0; render(); }, knowTab: d => { S.know.tab = d.a0; render(); }, knowCat: d => { S.know.cat = d.a0; render(); },
  repKind: d => { S.rep.kind = d.a0; render(); }, 
  copyReport: () => { navigator.clipboard.writeText($('reptext').value); toast('Report copied'); },
  async approve(d) { const r = await post(`/approvals/${d.a0}/${d.a1 ? 'approve' : 'reject'}`); if (done(r, d.a1 ? 'Approved. It runs now, or as soon as the chat repeats the call.' : 'Rejected. It will not run.')) { await post('/supervisor/tick'); render(); } },
  async allowSimilar(d) { const r = await post(`/approvals/${d.a0}/allow_similar`); if (r.ok === false) return toast(r.error.message, true); toast(`Approved ${r.approved} command(s). Commands that "${r.rule}" no longer ask you in this project.`); await post('/supervisor/tick'); render(true); },
  async ruleOff(d) { const r = await post(`/approvals/${d.a0}/remove_rule`); if (r.ok === false) return toast(r.error.message, true); toast('You will be asked again for these commands.'); render(true); },
  async taskAct(d) { const note = ($('tk_note') || {}).value || ''; if (d.a1 === 'cancel' && !confirm('Cancel this task?')) return;
    const r = await post(`/company/tasks/${d.a0}/${d.a1}`, {note}); if (r.ok === false) return toast(r.error.message, true);
    toast({unblock: 'Sent. The task is back in progress.', escalate: 'The Master (or the lead) was told to solve it now.', cancel: 'Task cancelled.'}[d.a1]); await post('/supervisor/tick'); drawer('task', d.a0, '', true); render(true); },
  async reuse(d) { const o = await api('/company/overview'); const others = (o.projects || []).map(p => p.name).filter(n => n !== d.a0);
    if (!others.length) return toast('There is no other project yet. Create one first.', true);
    modal(`<h2>Use in another project</h2><p class="mut sm">The person starts there as themselves, with their skills and what they learned (tips, lessons, reusable knowledge). The memory, tasks and messages of ${esc(d.a0)} are not taken along.</p>
      <label>Project</label><select id="ru_to">${others.map(n => `<option>${esc(n)}</option>`).join('')}</select>
      <label style="display:flex;gap:8px;align-items:center;margin-top:12px"><input type="checkbox" id="ru_keep" checked style="width:auto"> Keep working in ${esc(d.a0)} as well</label>
      <div class="row" style="margin-top:14px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('reuseDo', d.a0, d.a1)}>Add to that project</button></div>`); },
  async reuseDo(d) { const r = await post(`/company/people/${encodeURIComponent(d.a0)}/${encodeURIComponent(d.a1)}/reuse`, {to: val('ru_to'), keep: $('ru_keep').checked});
    if (r.ok === false) return toast(r.error.message + (r.error.fix ? ' ' + r.error.fix : ''), true);
    modal(''); toast(`${r.name} now works in ${r.project}: ${r.memories} thing(s) learned and ${r.skills} skill(s) came along.`); render(true); },
  async needAct(d) { const r = await post(d.a0, {}); if (r.ok === false) return toast(r.error.message, true); toast('Done: the tries were reset and it was started again.'); render(true); },
  async overrule(d) { const a = prompt('Your decision (it replaces what was decided without you):'); if (!a) return; const r = await post(`/questions/${d.a0}/answer`, {answer: a}); if (r.ok === false) return toast(r.error.message, true); toast('Sent to the team: your decision replaces the earlier one.'); render(true); },
  async answer(d) { const a = d.a1 || val('q_' + d.a0); if (!a) return toast('Write your answer first', true); const r = await post(`/questions/${d.a0}/answer`, {answer: a}); if (done(r, 'Sent. The team continues with your answer; it is recorded in Knowledge.')) { await post('/supervisor/tick'); render(); } },
  async pstatus(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/status`, {status: d.a1}), d.a1 === 'active' ? 'The project is running again' : 'The project is paused')) render(); },
  async agent(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/${d.a2}`, {}), cap(d.a2) + ' done')) { render(); drawer('person', d.a0, d.a1, true); } },
  async send() { let text = val('msgtext'); const files = S.chat.files.map(f => f.file_id); if (!text && files.length) text = 'See the attached file' + (files.length > 1 ? 's' : '') + '.'; if (!text) return;
    const to = S.chat.all ? S.chat.agents : [S.chat.who]; let bad = '';
    for (const t of to) { const r = await post(`/projects/${encodeURIComponent(S.chat.project)}/messages`, {to: t, text, files}); if (!r.ok) bad = r.error.message + ' ' + (r.error.fix || ''); }
    toast(bad || (S.chat.all ? 'Sent to ' + to.length + ' agents. Their chats are woken up.' : 'Sent. The chat is woken up.'), !!bad);
    if (!bad) { Object.assign(S.chat, {files: [], draft: '', all: false, pos: null}); await post('/supervisor/tick'); render(); } },
  chatWho: d => { Object.assign(S.chat, {who: d.a0, task: '', pos: null, all: false}); render(); }, chatTask: d => { S.chat.task = d.a0; render(); },
  chatAll: () => { S.chat.all = !S.chat.all; render(); }, chatUnfile: d => { S.chat.files.splice(+d.a0, 1); render(); },
  async openChat(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/roles/${encodeURIComponent(d.a1)}/open-chat`, {}), 'The chat is being opened')) { await post('/supervisor/tick'); render(); } },
  async aiSave(d) { const r = await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}`, {ai: val('ai_p'), model: val('ai_m'), effort: val('ai_e')}); if (r.ok === false) return toast(r.error.message + ' ' + (r.error.fix || ''), true); toast('Saved. Its next chat runs on the chosen AI.'); drawer('person', d.a0, d.a1, true); },
  async aiModels() { const p = val('ai_p'); if (!p || p === 'chatgpt') return toast('Choose Claude, Gemini or Custom first', true); const r = await api('/ai/models?provider=' + p); if (!r.ok) return toast(r.error.message + ' ' + (r.error.fix || ''), true);
    $('ai_ml').innerHTML = r.models.map(m => `<option>${esc(m)}</option>`).join(''); toast(r.models.length + ' models found: click into the model field to pick one'); },
  async aiTest() { const p = val('ai_p'); if (!p || p === 'chatgpt') return toast('ChatGPT runs in your browser: there is nothing to test here', true); toast('Asking ' + p + '…'); const r = await post('/ai/test', {provider: p, model: val('ai_m')});
    toast(r.ok ? `${r.provider} / ${r.model} answered: ${r.answer}` : r.error.message + ' ' + (r.error.fix || ''), !r.ok); },
  async skillAdd(d) { const ref = d.a2 || val('sk_ref'); if (!ref) return toast('Which skill? Type owner/repo:skill', true); toast('Getting the skill…'); const r = await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/skills`, {skills: [ref]});
    if (done(r, 'Skill added. The agent reads it at the start of its next chat.')) drawer('person', d.a0, d.a1, true); },
  async skillDrop(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/skills`, {skills: [d.a2], remove: true}), 'Removed')) drawer('person', d.a0, d.a1, true); },
  async delMemory(d) { if (done(await del('/agent-memory/' + d.a0), 'Removed') && S.drawer) drawer(S.drawer.kind, ...S.drawer.args, true); },
  async delKnow(d) { if (confirm('Delete this knowledge entry?') && done(await del('/company/knowledge/' + d.a0), 'Deleted')) render(); },
  async briefing() { const b = await api('/company/briefing'); modal(`<h2>Briefing</h2><p style="margin:12px 0;font-size:15px;line-height:1.6">${esc(b.text)}</p>${b.priorities.length ? `<h3 style="margin:16px 0 6px">Running</h3>${b.priorities.map(p => `<div class="sm">${esc(p)}</div>`).join('')}` : ''}
      ${b.problems.length ? `<h3 style="margin:16px 0 6px">Problems</h3>${b.problems.map(p => `<div class="sm">${esc(p.title)}</div>`).join('')}` : ''}<div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Close</button><button class="pri" ${act('goto', 'decisions')} data-mclose="1">Open decisions</button></div>`); },
  newProject: () => modal(`<h2>Start a project</h2><p class="mut sm">Say what you want. The Master writes the plan and the architecture first, then builds the team and gives out the work.</p>
    <label>What should be built?</label><textarea id="np_goal" placeholder="A small web shop for handmade soap with a cart, checkout and an admin page…" style="min-height:110px"></textarea>
    <label>Name (optional)</label><input id="np_name" placeholder="soap-shop"><label>Folder on this PC where the work goes (optional)</label><input id="np_folder" placeholder="D:\\Projects\\soap-shop">
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('createProject')}>Start project</button></div>`),
  async createProject() { const r = await post('/projects', {goal: val('np_goal'), name: val('np_name'), folder: val('np_folder'), start: true}); if (done(r, 'Project started. The Master is being called in.')) { modal(''); state.project = r.project.name; go('agents'); } },
  newDept: d => modal(`<h2>${d.a0 ? 'Edit' : 'New'} department</h2><label>Name</label><input id="nd_name" value="${esc(d.a0 || '')}" ${d.a0 ? 'readonly' : ''} placeholder="Engineering"><label>Mission</label><textarea id="nd_mission" placeholder="Build and maintain the products.">${esc(d.a1 || '')}</textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('saveDept')}>Save</button></div>`),
  async saveDept() { if (done(await post('/company/departments', {name: val('nd_name'), mission: val('nd_mission')}), 'Department saved')) { modal(''); render(); if (S.drawer) drawer(S.drawer.kind, ...S.drawer.args, true); } },
  newKnow: async () => { const k = await api('/company/knowledge'); modal(`<h2>Add company knowledge</h2><p class="mut sm">Every chat of the company reads this before it starts working.</p><label>Category</label><select id="nk_cat">${k.categories.map(c => `<option>${esc(c.name)}</option>`).join('')}</select>
    <label>Title</label><input id="nk_title" placeholder="Always write tests first"><label>Text</label><textarea id="nk_text" style="min-height:120px" placeholder="The rule, standard or fact, written so that somebody new understands it."></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('saveKnow')}>Add</button></div>`); },
  async saveKnow() { if (done(await post('/company/knowledge', {category: val('nk_cat'), title: val('nk_title'), text: val('nk_text')}), 'Added. New chats will read it.')) { modal(''); render(); } },
  addMemory: d => modal(`<h2>Add a tip</h2><p class="mut sm">This person reads it at the start of each of their chats.</p><textarea id="am_text" style="min-height:100px" placeholder="Always run the linter before you report a task."></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('saveMemory', d.a0, d.a1)}>Add</button></div>`),
  async saveMemory(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/memory`, {kind: 'tip', text: val('am_text')}), 'Added')) { modal(''); drawer('person', d.a0, d.a1, true); } },
  talk: d => modal(`<h2>Message ${d.a1 === 'master' ? 'the Master' : esc(d.a1)}</h2><p class="mut sm">${esc(d.a0)} · the message lands in their inbox and their chat is woken up.</p><textarea id="tk_text" style="min-height:120px" placeholder="What do you want to say?"></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('sendTalk', d.a0, d.a1)}>Send</button></div>`),
  async sendTalk(d) { const r = await post(`/projects/${encodeURIComponent(d.a0)}/messages`, {to: d.a1, text: val('tk_text')}); if (done(r, 'Sent')) { modal(''); await post('/supervisor/tick'); } },
  /* assign a task, with staffing advice */
  async assign(d) {
    const o = S.orgData || await api('/company/org'), projects = o.projects.filter(p => p.status !== 'archived').map(p => p.name);
    if (!projects.length) return toast('Start a project first', true);
    S.as = {project: d.a0 || projects[0], agent: d.a1 || ''};
    modal(`<h2>Assign a task</h2><label>Project</label><select id="as_p">${projects.map(p => `<option ${p === S.as.project ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select>
      <label>Title</label><input id="as_title" placeholder="Build the login API"><label>What to do</label><textarea id="as_text" style="min-height:90px" placeholder="Context, files, constraints: everything the person needs."></textarea>
      <label>Done when (one per line)</label><textarea id="as_done" placeholder="The tests pass"></textarea>
      <label>Who <span class="dim">— ranked by fit, free capacity and record</span></label><div id="as_who" class="mut sm">Write a title to see who fits.</div>
      <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('createTask')}>Assign</button></div>`);
    const rec = async () => { S.as.project = val('as_p'); const r = await api('/company/recommend?' + qs({project: S.as.project, title: val('as_title') || 'task', text: val('as_text')}));
      const list = r.candidates || []; if (!list.length) { $('as_who').innerHTML = 'This project has no people yet. Hire someone, or let the Master build the team.'; return; }
      if (!S.as.agent || !list.some(c => c.key === S.as.agent)) S.as.agent = list[0].key;
      $('as_who').innerHTML = list.slice(0, 6).map((c, i) => `<div class="card click" style="padding:10px;margin-bottom:6px;${c.key === S.as.agent ? 'border-color:var(--acc)' : ''}" ${act('pick', c.key)}><div class="row">${av(c.name, c.status)}<div class="sp"><b>${esc(c.name)}</b> ${i === 0 ? tag('recommended', 'ok') : ''}<div class="xs mut">${esc(c.role)} · ${esc(c.why.join(' · '))}</div></div><b>${c.match}%</b><span style="width:60px">${bar(c.workload.pct, loadTone(c.workload.band))}</span></div>${c.workload.band === 'overloaded' ? `<div class="xs" style="color:var(--warn);margin-top:4px">Overloaded: ${c.workload.active} open tasks</div>` : ''}</div>`).join(''); };
    S.as.rec = rec; let h; ['as_title', 'as_text'].forEach(i => $(i).oninput = () => { clearTimeout(h); h = setTimeout(rec, 350); }); $('as_p').onchange = rec; rec();
  },
  pick: d => { S.as.agent = d.a0; S.as.rec(); },
  async createTask() { const r = await post(`/projects/${encodeURIComponent(S.as.project)}/tasks`, {agent: S.as.agent, title: val('as_title'), instructions: val('as_text'), done_when: val('as_done').split('\n').map(s => s.trim()).filter(Boolean)});
    if (done(r, 'Assigned. The person is being told.')) { modal(''); await post('/supervisor/tick'); render(); } },
  /* hire: five short steps */
  async hire(d) {
    const o = S.orgData || await api('/company/org'), projects = o.projects.filter(p => p.status !== 'archived').map(p => p.name);
    if (!projects.length) return toast('Start a project first: people are hired into a project', true);
    S.hire = {step: 0, o, v: {project: d.a0 || projects[0], department: d.a1 || '', seniority: 'mid', level: 'specialist'}};
    hireStep();
  },
  hireNext: d => { hireRead(); const v = S.hire.v, s = S.hire.step;
    if (s === 0 && !v.job_title) return toast('Give the job title, e.g. Frontend Engineer', true);
    if (s === 2 && (v.responsibilities || '').length < 80) return toast('Describe the responsibilities in a few sentences (the person works from this text)', true);
    S.hire.step = Math.max(0, Math.min(4, s + (d.a0 === 'back' ? -1 : 1))); hireStep(); },
  async hireDo() { const v = S.hire.v; const r = await post('/company/hire/' + encodeURIComponent(v.project), {...v, skills: (v.skills || '').split(',').map(s => s.trim()).filter(Boolean), start: true});
    if (done(r, `${r.ok ? r.person.name : ''} joined the company`)) { modal(''); S.orgData = null; render(); drawer('person', v.project, r.person.key); } },
};
function hireRead() { document.querySelectorAll('#modal [data-f]').forEach(el => S.hire.v[el.dataset.f] = el.value.trim()); }
function hireStep() {
  const h = S.hire, v = h.v, o = h.o, f = (k, label, ph = '', tagName = 'input') => `<label>${label}</label>${tagName === 'textarea' ? `<textarea data-f="${k}" placeholder="${esc(ph)}" style="min-height:96px">${esc(v[k] || '')}</textarea>` : `<input data-f="${k}" placeholder="${esc(ph)}" value="${esc(v[k] || '')}">`}`;
  const sel = (k, label, opts) => `<label>${label}</label><select data-f="${k}">${opts.map(([val2, text]) => `<option value="${esc(val2)}" ${v[k] === val2 ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select>`;
  const team = o.people.filter(p => p.project === v.project);
  const boss = team.find(p => p.key === v.manager);
  const steps = [
    ['Who are you hiring?', f('job_title', 'Job title', 'Frontend Engineer') + f('person_name', 'Name (optional: one is chosen if empty)', 'Maya Adel') + sel('seniority', 'Seniority', ['junior', 'mid', 'senior', 'staff', 'principal'].map(s => [s, cap(s)]))],
    ['Where will they work?', sel('project', 'Project', o.projects.filter(p => p.status !== 'archived').map(p => [p.name, p.name])) + `<label>Department</label><input data-f="department" list="hd" placeholder="Decided from the job title if empty" value="${esc(v.department || '')}"><datalist id="hd">${o.departments.map(d => `<option>${esc(d.name)}</option>`).join('')}</datalist>`
      + sel('manager', 'Reports to', [['', 'The Master'], ...team.filter(p => p.kind === 'agent').map(p => [p.key, `${p.name} — ${p.role}`])]) + sel('level', 'Level', [['lead', 'Lead (manages people, may ask you questions)'], ['specialist', 'Specialist'], ['worker', 'Worker']])],
    ['What are they responsible for?', f('responsibilities', 'Responsibilities', 'What they own, what they must not touch, and how they prove their work is done.', 'textarea') + f('skills', 'Skills (comma separated)', 'React, CSS, accessibility') + f('career', 'Background (optional)', '6 years building web front ends')],
    ['How should they work?', f('personality', 'Working style', 'Careful, tests before reporting, asks early when something is unclear.', 'textarea')],
    ['Review', `<div class="card" style="margin-top:8px"><div class="who">${av(v.person_name || v.job_title, '', 'lg')}<div><b style="font-size:16px">${esc(v.person_name || 'Name chosen on hiring')}</b><small>${esc(cap(v.seniority) + ' ' + v.job_title)}</small></div></div>
      <p style="margin-top:12px">A ${esc(v.seniority)} ${esc(v.job_title)} joining ${v.department ? 'the ' + esc(v.department) + ' department' : 'the department that fits the title'} in <b>${esc(v.project)}</b>, reporting to ${esc(boss ? boss.name : 'the Master')}.</p>
      <p class="sm mut prose" style="margin-top:8px;max-height:120px;overflow:auto">${esc(v.responsibilities || '')}</p></div><p class="xs mut" style="margin-top:8px">Their chat is opened right away and they read their profile first.</p>`]];
  modal(`<div class="xs mut">Hire · step ${h.step + 1} of 5</div><h2>${steps[h.step][0]}</h2><div class="steps">${steps.map((_, i) => `<i class="${i <= h.step ? 'on' : ''}"></i>`).join('')}</div>${steps[h.step][1]}
    <div class="row" style="margin-top:20px">${h.step ? `<button ${act('hireNext', 'back')}>Back</button>` : ''}<span class="sp"></span><button data-mclose="1">Cancel</button>${h.step < 4 ? `<button class="pri" ${act('hireNext')}>Continue</button>` : `<button class="pri" ${act('hireDo')}>Hire</button>`}</div>`);
}

/* ---------- command palette ---------- */
const COMMANDS = [['New project', () => ACT.newProject()], ['Hire someone', () => ACT.hire({})], ['Assign a task', () => ACT.assign({})], ['New department', () => ACT.newDept({})], ['Add company knowledge', () => ACT.newKnow()],
  ['Briefing: how is the company doing?', () => ACT.briefing()], ['Build a workflow', () => go('n8n')], ['View decisions', () => go('decisions')], ['Open the org chart', () => { S.ctab = 'chart'; go('company'); }], ['Task board', () => go('tasks')], ['Today\'s report', () => { S.rep.kind = 'daily'; go('reports'); }],
  ['Conversations', () => go('agents')], ['Settings', () => go('settings')], ['Switch light / dark', () => theme()]];
const P = {items: [], i: 0};
function palette(openIt) {
  if (!openIt) { $('pal').innerHTML = ''; return; }
  $('pal').innerHTML = `<div class="scrim" data-pclose="1" style="z-index:40"></div><div class="pal"><input id="palq" placeholder="Search people, projects, tasks, knowledge… or type a command" autocomplete="off"><div class="res" id="palr"></div></div>`;
  const q = $('palq'); q.focus();
  let h; q.oninput = () => { clearTimeout(h); h = setTimeout(palSearch, 140); };
  q.onkeydown = e => { if (e.key === 'ArrowDown') { P.i = Math.min(P.items.length - 1, P.i + 1); palDraw(); e.preventDefault(); } else if (e.key === 'ArrowUp') { P.i = Math.max(0, P.i - 1); palDraw(); e.preventDefault(); } else if (e.key === 'Enter') palRun(P.i); else if (e.key === 'Escape') palette(false); };
  palSearch();
}
async function palSearch() {
  const q = val('palq').toLowerCase();
  const cmds = COMMANDS.filter(c => !q || c[0].toLowerCase().includes(q)).map(c => ({type: 'command', title: c[0], run: c[1]}));
  let found = [];
  if (q.length >= 2) { const r = await api('/company/search?' + qs({q})); found = (r.results || []).map(x => ({...x, run: () => ({project: () => ACT.goProject({a0: x.title}), agent: () => drawer('person', x.project, x.key), department: () => drawer('dept', x.key), task: () => drawer('task', x.key),
    knowledge: () => { S.know = {tab: 'company', q: x.title, cat: ''}; go('knowledge'); }, message: () => ACT.goProject({a0: x.project}), artifact: () => window.open('/api/v1/files/' + x.key)}[x.type] || (() => {}))()})); }
  P.items = [...found, ...cmds]; P.i = 0; palDraw();
}
function palDraw() { const r = $('palr'); if (!r) return; r.innerHTML = P.items.map((x, i) => `<div class="it ${i === P.i ? 'on' : ''}" data-pal="${i}"><span class="ty">${x.type}</span><div class="clip"><div class="clip">${esc(x.title)}</div>${x.sub ? `<small class="clip">${esc(x.sub)}</small>` : ''}</div></div>`).join('') || '<div class="empty">Nothing found</div>'; const on = r.querySelector('.on'); if (on) on.scrollIntoView({block: 'nearest'}); }
function palRun(i) { const x = P.items[i]; if (!x) return; palette(false); x.run(); }

/* ---------- workflows: build them by drag and drop ---------- */
const W = {list: [], catalog: [], events: [], cur: null, sel: '', tab: 'build', runs: [], last: null, dirty: false, scroll: null};
const NW = 210, NH = 66;
const spec = t => W.catalog.find(c => c.type === t) || {kind: 'action', label: t, params: [], outputs: ['main']};
const WICON = {trigger: '<path d="M13 3 5 13h6l-1 8 8-10h-6z"/>', logic: '<path d="M6 3v6a6 6 0 0 0 6 6h6M18 15l-3-3M18 15l-3 3M6 3 3 6M6 3l3 3"/>', action: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>'};
const wsvg = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="15" height="15">${WICON[k]}</svg>`;
const outY = (n, out) => spec(n.type).outputs.length > 1 ? (out === 'true' ? 22 : 46) : NH / 2;
const summary = n => { const p = n.params || {}; return n.type === 'on_event' ? (p.event || '') + (p.project ? ' in ' + p.project : '') : n.type === 'schedule' ? 'every ' + p.every_minutes + ' min'
  : n.type === 'condition' ? `${p.left || ''} ${p.op || ''} ${p.right || ''}` : n.type === 'send_message' ? `to ${p.to}: ${p.text || ''}` : n.type === 'assign_task' ? `${p.agent}: ${p.title || ''}` : n.type === 'http_request' ? `${p.method} ${p.url || ''}`
  : n.type === 'powershell' ? (p.script || '') : n.type === 'run_n8n' ? (p.workflow || '') : n.type === 'add_knowledge' ? (p.title || '') : n.type === 'project_status' ? (p.status || '') : n.type === 'wait' ? p.seconds + ' s' : spec(n.type).about; };
function edgePaths() {
  const by = Object.fromEntries(W.cur.nodes.map(n => [n.id, n]));
  return W.cur.edges.map((e, i) => { const a = by[e.from], b = by[e.to]; if (!a || !b) return ''; const x1 = a.x + NW, y1 = a.y + outY(a, e.out), x2 = b.x, y2 = b.y + NH / 2, dx = Math.max(40, Math.abs(x2 - x1) / 2);
    const d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
    return `<path class="weh" data-e="${i}" d="${d}"><title>Click to remove this connection</title></path><path class="we ${e.out === 'false' ? 'no' : e.out === 'true' ? 'yes' : ''}" d="${d}"/>`; }).join('');
}
const nodeHtml = n => { const sp = spec(n.type), st = W.last && W.last.steps.find(s => s.node === n.id);
  return `<div class="wn k-${sp.kind} ${W.sel === n.id ? 'sel' : ''} ${st ? 'r-' + st.status : ''}" data-n="${esc(n.id)}" style="left:${n.x}px;top:${n.y}px" title="${st ? esc(st.output) : ''}">
    <div class="wh">${wsvg(sp.kind)}<b class="clip">${esc(n.name || sp.label)}</b>${st ? `<span class="tag ${st.status === 'ok' ? 'ok' : 'err'}">${st.status === 'ok' ? 'ran' : 'failed'}</span>` : ''}</div><div class="wb clip">${esc(summary(n))}</div>
    ${sp.kind === 'trigger' ? '' : '<i class="pin in"></i>'}${sp.outputs.map(o => `<i class="pin out ${o}" data-out="${o}" style="top:${outY(n, o) - 6}px" title="Drag to another step to connect">${o === 'main' ? '' : `<em>${o === 'true' ? 'yes' : 'no'}</em>`}</i>`).join('')}</div>`; };
const field = (n, p) => { const v = n.params[p.key] ?? '', k = p.key, common = `data-p="${k}" id="wfp_${k}"`;
  const opts = {op: ['equals', 'not_equals', 'contains', 'not_contains', 'exists', 'greater', 'less'], method: ['POST', 'GET', 'PUT', 'PATCH', 'DELETE'], status: ['active', 'paused', 'done']}[k];
  const input = opts ? `<select ${common}>${opts.map(o => `<option ${String(v) === o ? 'selected' : ''}>${o}</option>`).join('')}</select>`
    : ['text', 'instructions', 'script', 'body', 'headers', 'input'].includes(k) ? `<textarea ${common} placeholder="${esc(p.hint)}" style="min-height:${k === 'script' || k === 'instructions' || k === 'text' ? 110 : 64}px">${esc(v)}</textarea>`
    : `<input ${common} value="${esc(v)}" placeholder="${esc(p.hint)}" ${k === 'event' ? 'list="wf_events"' : k === 'project' ? 'list="wf_projects"' : ''}>`;
  return `<label>${esc(p.label)}${p.required ? '' : ' <span class="dim">(optional)</span>'}</label>${input}`; };
async function wfLoad(full) { const r = await api('/workflows'); if (!r.ok) return; W.list = r.workflows; W.catalog = r.catalog; W.events = r.events; if (!W.cur || full) W.runs = r.runs;
  if (!W.cur && W.list.length) { W.cur = JSON.parse(JSON.stringify(W.list[0])); W.runs = (await api('/workflows/' + W.cur.id)).runs || []; } }
VIEWS.workflows = async () => {
  if (!W.catalog.length || !W.dirty) await wfLoad();
  const c = W.cur, groups = [['trigger', 'Start'], ['logic', 'Logic'], ['action', 'Do']];
  const list = `<div class="wfl"><button class="pri" style="width:100%;justify-content:center;margin-bottom:10px" ${act('wfNew')}>+ New workflow</button>${W.list.map(w => `<div class="wli ${c && c.id === w.id ? 'on' : ''}" ${act('wfOpen', w.id)}><div class="row"><i class="dot" style="background:${w.enabled ? 'var(--ok)' : 'var(--dim)'}"></i><b class="clip sp">${esc(w.name)}</b>${w.last_status ? `<span class="tag ${w.last_status === 'ok' ? 'ok' : 'err'}">${w.last_status === 'ok' ? 'ok' : 'failed'}</span>` : ''}</div>
      <div class="xs mut clip" style="margin-top:4px">${esc(w.triggers.join(', '))} · ${w.nodes.length} steps · ${w.runs} runs${w.created_by === 'master' ? ' · by the Master' : ''}</div></div>`).join('') || '<div class="mut sm">No workflows yet. Create one, or ask the Master to build it.</div>'}</div>`;
  if (!c) return `<div class="wf">${list}<div class="card" style="grid-column:2/-1">${empty('Automate what repeats', 'A workflow starts on an event, on a schedule or by hand, and then does things: message the Master, assign a task, call a web address, run a script. Drag the steps onto the canvas and connect them.', `<button class="pri" ${act('wfNew')}>Create the first workflow</button>`)}</div></div>`;
  const sel = c.nodes.find(n => n.id === W.sel);
  const right = sel ? `<div class="card"><div class="row"><h3 class="sp">${esc(spec(sel.type).label)}</h3><button class="quiet s" ${act('wfDelNode')}>Delete step</button></div><div class="xs mut" style="margin:4px 0 6px">${esc(spec(sel.type).about)}</div>
      <label>Name on the canvas</label><input id="wfp__name" data-nn="1" value="${esc(sel.name || '')}">${spec(sel.type).params.map(p => field(sel, p)).join('')}
      <div class="xs mut" style="margin-top:12px">Use data from earlier steps in any text field:<br><span class="mono">{{event.payload.title}}</span> <span class="mono">{{event.project}}</span> <span class="mono">{{trigger.field}}</span> <span class="mono">{{last.field}}</span></div>
      <datalist id="wf_events">${W.events.map(e => `<option>${esc(e)}</option>`).join('')}<option>task.*</option></datalist><datalist id="wf_projects">${((state.status || {}).projects || []).map(p => `<option>${esc(p.project)}</option>`).join('')}</datalist></div>`
    : `<div class="card"><h3>Steps</h3><div class="xs mut" style="margin-bottom:8px">Drag a step onto the canvas (or click it). Then drag from the dot on its right edge to the next step.</div>${groups.map(([k, l]) => `<div class="xs dim" style="margin:10px 0 4px;text-transform:uppercase;letter-spacing:.06em">${l}</div>${W.catalog.filter(x => x.kind === k).map(x => `<div class="wpal k-${k}" draggable="true" data-type="${x.type}" title="${esc(x.about)}">${wsvg(k)}<span>${esc(x.label)}</span></div>`).join('')}`).join('')}</div>`;
  const runs = W.runs.length ? W.runs.slice(0, 8).map(r => `<div class="row sm" style="padding:6px 0;border-bottom:1px solid var(--line)"><span class="tag ${r.status === 'ok' ? 'ok' : r.status === 'running' ? '' : 'err'}">${r.status}</span><span class="mut" style="width:120px">${ago(r.started)}</span><span class="mut clip" style="width:150px">${esc(r.trigger)}</span>
      <span class="sp clip">${r.steps.map(s => `<span title="${esc(s.output)}" style="color:var(--${s.status === 'ok' ? 'ok' : 'err'})">${esc(s.name)}</span>`).join(' <span class="dim">→</span> ')}</span></div>`).join('') : '<div class="mut sm">It has not run yet.</div>';
  return `<div class="wf">${list}<div class="wfm"><div class="card" style="padding:10px 12px"><div class="row wrap"><input id="wf_name" value="${esc(c.name)}" style="max-width:260px;font-weight:600" placeholder="Workflow name"><input id="wf_desc" value="${esc(c.description)}" class="sp" style="min-width:160px" placeholder="What it does (one sentence)">
        <label class="row sm" style="margin:0;gap:6px;color:var(--fg)"><input type="checkbox" id="wf_on" ${c.enabled ? 'checked' : ''} style="width:auto"> On</label>
        <button ${act('wfRun')} title="Save, then run it now">Run now</button>${c.id ? `<button class="quiet bad" ${act('wfDelete')}>Delete</button>` : ''}<button class="pri" ${act('wfSave')}>${W.dirty ? 'Save *' : 'Save'}</button></div></div>
      <div class="wfc" id="wfc"><div class="wfi" id="wfi"><svg id="wfs">${edgePaths()}<path id="wftmp" class="we tmp"/></svg>${c.nodes.map(nodeHtml).join('')}${c.nodes.length ? '' : '<div class="wfhint">Drag a <b>Start</b> step here to begin</div>'}</div></div>
      <div class="card"><h3>Runs <span class="sp"></span>${c.id && c.nodes.some(n => n.type === 'webhook') ? `<span class="xs mono" style="text-transform:none;letter-spacing:0">POST /api/v1/workflows/${c.id}/run</span>` : ''}</h3>${runs}</div></div>
    <div class="wfr">${right}</div></div>`;
};
const wfDirty = () => { W.dirty = true; };
function wfAdd(type, x, y) { const sp = spec(type), c = W.cur; let i = c.nodes.length + 1; while (c.nodes.some(n => n.id === 'n' + i)) i++;
  const n = {id: 'n' + i, type, name: sp.label, x: Math.max(10, Math.round(x)), y: Math.max(10, Math.round(y)), params: Object.fromEntries(sp.params.map(p => [p.key, p.default]))};
  c.nodes.push(n); W.sel = n.id; W.last = null; wfDirty(); render(); }
function wfWire() {
  const c = $('wfc'), inner = $('wfi'); if (!c || !W.cur) return;
  if (W.scroll) { c.scrollLeft = W.scroll.l; c.scrollTop = W.scroll.t; }
  c.onscroll = () => { W.scroll = {l: c.scrollLeft, t: c.scrollTop}; };
  const pos = e => { const r = inner.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top}; };
  const redraw = () => { $('wfs').innerHTML = edgePaths() + '<path id="wftmp" class="we tmp"/>'; };
  document.querySelectorAll('.cx .wpal').forEach(p => { p.ondragstart = e => { e.dataTransfer.setData('text/plain', p.dataset.type); state.busy = true; }; p.ondragend = () => { state.busy = false; };
    p.onclick = () => wfAdd(p.dataset.type, 60 + c.scrollLeft + (W.cur.nodes.length % 4) * 250, 60 + c.scrollTop + Math.floor(W.cur.nodes.length / 4) * 110); });
  c.ondragover = e => e.preventDefault();
  c.ondrop = e => { e.preventDefault(); state.busy = false; const t = e.dataTransfer.getData('text/plain'); if (spec(t).type) { const p = pos(e); wfAdd(t, p.x - NW / 2, p.y - NH / 2); } };
  inner.onmousedown = e => {
    const pin = e.target.closest('.pin.out'), el = e.target.closest('.wn'), edge = e.target.closest('.weh');
    if (edge) { W.cur.edges.splice(+edge.dataset.e, 1); wfDirty(); redraw(); return; }
    if (!el) { if (W.sel) { W.sel = ''; render(); } return; }
    const n = W.cur.nodes.find(x => x.id === el.dataset.n); e.preventDefault(); state.busy = true;
    if (pin) {                                                        /* draw a connection */
      const out = pin.dataset.out, x1 = n.x + NW, y1 = n.y + outY(n, out), tmp = () => $('wftmp');
      const move = ev => { const p = pos(ev), dx = Math.max(40, Math.abs(p.x - x1) / 2); tmp().setAttribute('d', `M${x1},${y1} C${x1 + dx},${y1} ${p.x - dx},${p.y} ${p.x},${p.y}`); };
      const up = ev => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); state.busy = false;
        const target = document.elementFromPoint(ev.clientX, ev.clientY), to = target && target.closest('.wn'), m = to && W.cur.nodes.find(x => x.id === to.dataset.n);
        if (m && m.id !== n.id) { if (spec(m.type).kind === 'trigger') toast('A start step cannot have something before it', true);
          else if (!W.cur.edges.some(x => x.from === n.id && x.to === m.id && x.out === out)) { W.cur.edges.push({from: n.id, to: m.id, out}); wfDirty(); } }
        redraw(); };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up); return;
    }
    const start = pos(e), ox = n.x, oy = n.y; let moved = false;      /* move a step */
    const move = ev => { const p = pos(ev); n.x = Math.max(4, Math.round(ox + p.x - start.x)); n.y = Math.max(4, Math.round(oy + p.y - start.y)); moved = true; el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; redraw(); };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); state.busy = false; if (moved) wfDirty(); if (W.sel !== n.id || moved) { W.sel = n.id; render(); } };
    document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
  };
  const cur = () => W.cur.nodes.find(n => n.id === W.sel);
  document.querySelectorAll('.cx .wfr [data-p]').forEach(i => { i.oninput = i.onchange = () => { cur().params[i.dataset.p] = i.value; wfDirty(); const b = document.querySelector(`.wn[data-n="${W.sel}"] .wb`); if (b) b.textContent = summary(cur()); }; });
  const nn = document.querySelector('.cx .wfr [data-nn]'); if (nn) nn.oninput = () => { cur().name = nn.value; wfDirty(); const b = document.querySelector(`.wn[data-n="${W.sel}"] .wh b`); if (b) b.textContent = nn.value; };
  $('wf_name').oninput = e => { W.cur.name = e.target.value; wfDirty(); }; $('wf_desc').oninput = e => { W.cur.description = e.target.value; wfDirty(); };
  $('wf_on').onchange = e => { W.cur.enabled = e.target.checked; wfDirty(); };
}
async function wfSave(quiet) { const c = W.cur, r = await post('/workflows', {id: c.id, name: c.name, description: c.description, enabled: c.enabled, nodes: c.nodes, edges: c.edges});
  if (!r.ok) { toast(r.error.message + ' ' + (r.error.fix || ''), true); return false; }
  W.cur = r.workflow; W.dirty = false; await wfLoad(); if (!quiet) { toast('Saved. ' + (r.workflow.enabled ? 'It is switched on.' : 'It is switched off.')); render(); } return true; }
Object.assign(ACT, {
  wfNew: () => { W.cur = {id: '', name: 'New workflow', description: '', enabled: true, nodes: [], edges: [], runs: 0}; W.sel = ''; W.last = null; W.runs = []; W.dirty = true; W.scroll = null; render(); },
  async wfOpen(d) { if (W.dirty && !confirm('Leave this workflow without saving your changes?')) return; const r = await api('/workflows/' + d.a0); if (!r.ok) return toast(r.error.message, true);
    W.cur = r.workflow; W.runs = r.runs; W.sel = ''; W.last = null; W.dirty = false; W.scroll = null; render(); },
  wfSave: () => wfSave(),
  async wfRun() { if (!(await wfSave(true))) return; toast('Running…'); const r = await post(`/workflows/${W.cur.id}/run`, {}); if (!r.ok) return toast(r.error.message, true);
    W.last = r; W.runs = (await api('/workflows/' + W.cur.id)).runs || []; const bad = r.steps.find(s => s.status !== 'ok'); toast(bad ? `Failed at "${bad.name}": ${bad.output}` : `Ran ${r.steps.length} step(s) without errors`, !!bad); render(); },
  async wfDelete() { if (!confirm(`Delete the workflow "${W.cur.name}" and its run history?`)) return; if (done(await del('/workflows/' + W.cur.id), 'Deleted')) { W.cur = null; W.dirty = false; W.sel = ''; await wfLoad(true); render(); } },
  wfDelNode: () => { const c = W.cur; c.nodes = c.nodes.filter(n => n.id !== W.sel); c.edges = c.edges.filter(e => e.from !== W.sel && e.to !== W.sel); W.sel = ''; wfDirty(); render(); },
  wtab: d => { W.tab = d.a0; render(); },
});

/* ---------- skills: how-to guides from public collections, given to the agents they fit ---------- */
const SK = {repo: '', cat: null, q: '', busy: ''};
VIEWS.skills = async () => {
  const d = await api('/skills');
  if (!d.ok) return empty('Could not load skills', d.error.message);
  if (!SK.repo) SK.repo = (d.sources.find(s => s.enabled) || {}).repo || '';
  const q = SK.q.toLowerCase(), cat = SK.cat && SK.cat.repo === SK.repo ? SK.cat : null;
  const shown = cat ? cat.skills.filter(s => !q || (s.name + ' ' + s.description).toLowerCase().includes(q)) : [];
  return `<div class="grid" style="grid-template-columns:300px minmax(0,1fr) minmax(0,1fr);align-items:start">
    <div class="card"><h3>Sources</h3><div class="xs mut" style="margin-bottom:8px">Collections of skills on GitHub. Agents only get skills from sources that are switched on.</div>
      ${d.sources.map(s => `<div class="row sm" style="padding:7px 0;border-bottom:1px solid var(--line);align-items:flex-start"><input type="checkbox" ${s.enabled ? 'checked' : ''} ${act('skSrc', s.repo, s.enabled ? '' : '1')} style="width:auto;margin-top:3px" title="Use this source">
        <a class="sp" ${act('skBrowse', s.repo)} style="min-width:0"><b class="clip" style="display:block;${SK.repo === s.repo ? 'color:var(--acc)' : ''}">${esc(s.repo)}</b><span class="xs mut">${esc(s.about || '')}${s.installed ? ' · ' + s.installed + ' installed' : ''}</span></a>
        ${s.builtin ? '' : `<a class="xs dim" ${act('skSrcDel', s.repo)}>✕</a>`}</div>`).join('')}
      <div class="row" style="margin-top:10px"><input id="sk_src" placeholder="owner/repo or a skills.sh link"><button class="s" ${act('skSrcAdd')}>Add</button></div></div>
    <div class="card"><h3>${SK.repo ? esc(SK.repo) : 'Choose a source'} <span class="sp"></span>${SK.repo ? `<a class="xs" ${act('skBrowse', SK.repo, '1')}>refresh</a>` : ''}</h3>
      ${!SK.repo ? '' : !cat ? `<div class="mut sm">${SK.busy === SK.repo ? 'Reading the list from GitHub…' : `<button class="pri" ${act('skBrowse', SK.repo)}>Show its skills</button>`}</div>`
        : `<input id="sk_q" placeholder="Filter ${cat.skills.length} skills" value="${esc(SK.q)}" style="margin-bottom:10px">${shown.map(s => `<div class="row sm" style="padding:8px 0;border-bottom:1px solid var(--line);align-items:flex-start"><div class="sp" style="min-width:0"><b>${esc(s.name)}</b>${s.files ? ` <span class="dim xs">+${s.files} files</span>` : ''}<div class="mut">${esc(s.description || 'No description.')}</div></div>
            ${s.installed ? tag('installed', 'ok') : `<button class="s" ${act('skInstall', s.ref)}>${SK.busy === s.ref ? '…' : 'Install'}</button>`}</div>`).join('') || '<div class="mut sm">Nothing matches.</div>'}`}</div>
    <div class="card"><h3>Installed <span class="sp"></span><a class="xs" ${act('skWrite')}>write your own</a></h3>
      ${d.installed.length ? d.installed.map(s => `<div style="padding:9px 0;border-bottom:1px solid var(--line)"><div class="row sm"><b class="sp">${esc(s.name)}</b><span class="dim xs">${esc(s.source)} · ${Math.round(s.chars / 100) / 10}k chars</span><a class="xs dim" ${act('skRemove', s.id, s.name)}>remove</a></div>
          <div class="sm mut">${esc(s.description)}</div><div class="xs" style="margin-top:4px">${s.agents.length ? 'Used by ' + s.agents.map(a => `<a ${open('person', a.project, a.key)} style="text-decoration:underline">${esc(a.name)}</a>`).join(', ') : '<span class="dim">Nobody has it yet: open a person and add it under Skills.</span>'}</div></div>`).join('')
        : `<div class="mut sm">No skills installed yet.<br><br>A skill is a how-to guide (for example Vercel's web-design-guidelines). An agent that has it reads it at the start of every chat.<br><br>${d.auto_assign ? 'New agents get the skills that fit their job automatically: a UI designer gets web-design-guidelines, a tester gets webapp-testing.' : 'Automatic assignment is switched off (Settings › skills).'}</div>`}</div></div>`;
};
AFTER2.skills = () => { const i = $('sk_q'); if (i) i.oninput = () => { SK.q = i.value; clearTimeout(SK.t); SK.t = setTimeout(() => { render(); setTimeout(() => { const j = $('sk_q'); if (j) { j.focus(); j.setSelectionRange(j.value.length, j.value.length); } }, 60); }, 350); }; };
Object.assign(ACT, {
  async skBrowse(d) { SK.repo = d.a0; SK.q = ''; SK.busy = d.a0; render(); const r = await api('/skills/catalog?' + qs({repo: d.a0, refresh: d.a1 || ''})); SK.busy = ''; if (!r.ok) { toast(r.error.message + ' ' + (r.error.fix || ''), true); SK.cat = null; } else SK.cat = r; render(); },
  async skSrc(d) { await post('/skills/sources', {repo: d.a0, enabled: !!d.a1}); render(); },
  async skSrcDel(d) { if (confirm('Remove the source ' + d.a0 + '? Skills already installed stay.')) { await post('/skills/sources', {repo: d.a0, remove: true}); render(); } },
  async skSrcAdd() { const r = await post('/skills/sources', {add: val('sk_src')}); if (done(r, 'Source added')) render(); },
  async skInstall(d) { SK.busy = d.a0; render(); const r = await post('/skills/install', {ref: d.a0}); SK.busy = ''; if (done(r, 'Installed. Give it to an agent: open the person and add it under Skills.')) { if (SK.cat) SK.cat.skills.forEach(s => { if (s.ref === d.a0) s.installed = true; }); } render(); },
  async skRemove(d) { if (confirm(`Remove the skill "${d.a1}"? Agents that have it lose it.`) && done(await del('/skills/item/' + d.a0), 'Removed')) { if (SK.cat) SK.cat.skills.forEach(s => { if (s.ref === d.a0) s.installed = false; }); render(); } },
  skWrite: () => modal(`<h2>Write your own skill</h2><p class="mut sm">Your own way of working, as a guide agents read at the start of every chat.</p><label>Name</label><input id="ws_n" placeholder="our-code-style"><label>When it applies (one sentence)</label><input id="ws_d" placeholder="How we write and review code in this company.">
    <label>The guide</label><textarea id="ws_b" style="min-height:180px" placeholder="Write the rules and the steps, as you would explain them to a new colleague."></textarea><div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('skWriteSave')}>Save</button></div>`),
  async skWriteSave() { if (done(await post('/skills/install', {name: val('ws_n'), description: val('ws_d'), body: val('ws_b')}), 'Saved')) { modal(''); render(); } },
});

/* the Delivery System: shared tabs, not agent tabs */
const TABTONE = {DELIVERING: 'accent', NAVIGATING: 'accent', VERIFYING: 'accent', READY: 'ok', IDLE: '', RECOVERING: 'warn', FAILED: 'err', CREATING: 'warn', CLOSING: ''};
const deliveryCard = d => !d || !d.ok ? '' : `<div class="card"><div class="row wrap" style="gap:var(--s5)"><div style="min-width:230px" class="sp"><h3>Delivery System</h3>
    <div class="xs mut" style="margin-top:4px">Shared browser tabs that carry prompts to every ChatGPT agent. Agents do not own tabs: any number of agents works through these.</div></div>
    ${d.enabled ? `<div class="stats"><div class="stat"><b>${d.capacity}</b><span>tabs capacity</span></div><div class="stat"><b>${d.active}</b><span>active</span></div><div class="stat"><b>${d.available}</b><span>available</span></div>
      <div class="stat"><b style="${d.queued ? 'color:var(--warn)' : ''}">${d.queued}</b><span>queued</span></div><div class="stat"><b>${(d.stats || {}).delivered || 0}</b><span>delivered</span></div></div>
      <div class="row" style="gap:6px"><span class="xs mut">Tabs</span><button class="s" ${act('dvCap', d.capacity - 1)} ${d.capacity <= 1 ? 'disabled' : ''}>−</button><b>${d.capacity}</b><button class="s" ${act('dvCap', d.capacity + 1)} ${d.capacity >= 8 ? 'disabled' : ''}>+</button></div>` : `<span class="sm mut">${esc(d.why || 'Switched off')}</span>`}</div>
  ${d.enabled ? `<div style="margin-top:var(--s3)">${d.tabs.map(t => `<div class="row sm" style="padding:6px 0;border-top:1px solid var(--line)"><b class="mono" style="width:64px">${esc(t.name)}</b><span style="width:118px">${tag(t.state === 'IDLE' && !t.tab_id ? 'NOT OPEN' : t.state, TABTONE[t.state] || '')}</span>
      <span class="sp clip">${t.agent ? `<b>${esc(t.agent)}</b> <span class="dim mono xs">${esc((t.lease || {}).chat_id || '').slice(0, 13)}</span>` : t.chat_id ? `<span class="dim xs">last chat ${esc(t.chat_id.slice(0, 13))}</span>` : '<span class="dim xs">free</span>'}</span>
      ${t.error ? `<span class="xs" style="color:var(--err)" title="${esc(t.error)}">${esc(t.error.slice(0, 60))}</span>` : `<span class="xs dim">${t.deliveries} delivered</span>`}</div>`).join('')}
    ${d.queue.length ? `<div class="xs mut" style="margin-top:8px">Waiting for a tab: ${d.queue.slice(0, 10).map(q => `${esc(q.agent)}${q.messages > 1 ? ' ×' + q.messages : ''} <span class="dim">P${q.priority}</span>`).join(' · ')}${d.queue.length > 10 ? ' …' : ''}</div>` : ''}
    ${(d.bootstrapping || []).length ? `<div class="xs mut" style="margin-top:6px">${d.bootstrapping.length} new chat(s) starting in a temporary tab (closed when the chat has joined).</div>` : ''}
    ${d.errors.length ? `<div class="xs" style="margin-top:6px;color:var(--err)">Last delivery error: ${esc(d.errors[0].agent)} — ${esc(d.errors[0].error)}</div>` : ''}</div>` : ''}</div>`;
/* ---------- plugging the pages into the Control Center ---------- */
document.body.insertAdjacentHTML('beforeend', '<div class="cx"><div id="drawer"></div><div id="modal"></div><div id="pal"></div></div>');
const page = fn => async () => `<div class="cx">${await fn()}</div>`;
const svgOf = n => ICON[n];
const PAGES = [
  ['company', 'Company', 'Everyone who works for you: the org chart, people, departments and what happened', svgOf('org'), page(() => VIEWS.company(S.ctab)), () => AFTER2.company()],
  ['tasks', 'Tasks', 'Every work order on one board: ready, in progress, blocked, in review, done', svgOf('task'), page(() => VIEWS.tasks()), () => AFTER2.tasks()],
  ['decisions', 'Decisions', 'What needs you: commands waiting for approval and questions from the team', svgOf('dec'), page(() => VIEWS.decisions())],
  ['knowledge', 'Knowledge', 'The rules and decisions of the company, and what each person and project learned', svgOf('know'), page(() => VIEWS.knowledge()), () => AFTER2.knowledge()],
  ['skills', 'Skills', 'How-to guides from Vercel, Anthropic and other collections, given to the agents whose job they fit', '<path d="M12 3 3 7.5l9 4.5 9-4.5zM3 12l9 4.5 9-4.5M3 16.5 12 21l9-4.5"/>', page(() => VIEWS.skills()), () => AFTER2.skills()],
  ['files', 'Deliverables', 'Everything the team produced', svgOf('file'), page(() => VIEWS.files())],
  ['reports', 'Reports', 'Written from the real records at the moment you open them', svgOf('rep'), page(() => VIEWS.reports()), () => AFTER2.reports()],
];
for (const [k, label, sub, icon, fn, after] of PAGES) { TABS.push([k, label]); SUBS[k] = sub; I[k] = icon; V[k] = fn; if (after) AFTER[k] = after; }
const oldN8n = V.n8n;
V.n8n = async () => `<div class="tabs"><button class="${W.tab === 'build' ? 'on' : ''}" data-do="wtab" data-a0="build">Workflows</button><button class="${W.tab === 'n8n' ? 'on' : ''}" data-do="wtab" data-a0="n8n">n8n connection</button></div>`
  + (W.tab === 'n8n' ? await oldN8n() : `<div class="cx">${await VIEWS.workflows()}</div>`);
AFTER.n8n = () => { if (W.tab === 'build') wfWire(); };
TABS.find(t => t[0] === 'n8n')[1] = 'Workflows';
SUBS.n8n = 'Automations the hub runs by itself: build them by drag and drop, or let the Master build them';
GROUPS.length = 0;
GROUPS.push(['Work', ['dash', 'company', 'plan', 'team', 'agents', 'tasks', 'flow', 'projects', 'decisions']], ['Knowledge', ['knowledge', 'skills', 'files', 'reports']],
  ['System', ['n8n', 'conn', 'recovery', 'activity']], ['Advanced', ['logs', 'diag', 'settings', 'about']]);
SIMPLE.length = 0;
SIMPLE.push('dash', 'company', 'plan', 'team', 'agents', 'tasks', 'decisions', 'skills', 'n8n', 'conn', 'settings');
/* the dashboard also says how the company is doing, and why */
const oldDash = V.dash;
V.dash = async () => { const [html, o, dv] = await Promise.all([oldDash(), api('/company/overview'), api('/delivery')]);
  const dvHtml = dv.ok ? `<div class="cx" style="margin-bottom:14px">${deliveryCard(dv)}</div>` : '';
  if (!o.ok || !o.projects.length) return dvHtml + html;
  const h = o.health, c = o.counts;
  return `<div class="cx" style="margin-bottom:14px"><div class="card"><div class="row wrap" style="gap:var(--s5)"><div style="min-width:200px"><h3>Company health</h3><div style="font-size:17px;margin-top:4px">${health(h.level)}</div><div class="sm mut">${esc(h.headline)}</div></div>
      <div class="stats sp"><div class="stat"><b>${c.projects_active}</b><span>projects running</span></div><div class="stat"><b>${c.agents_active}<span class="mut" style="font-size:14px"> / ${c.agents}</span></b><span>people working</span></div><div class="stat"><b>${c.tasks_in_progress}</b><span>tasks open</span></div>
        <div class="stat"><b>${c.tasks_review}</b><span>in review</span></div><div class="stat"><b style="${c.tasks_blocked ? 'color:var(--warn)' : ''}">${c.tasks_blocked}</b><span>blocked</span></div><div class="stat"><b>${c.done_today}</b><span>done today</span></div></div>
      <button ${act('briefing')}>Briefing</button><button ${act('palette')} title="Search people, tasks, knowledge - or run a command">Search <kbd>Ctrl K</kbd></button></div>
      <div class="row wrap sm" style="margin-top:var(--s3);gap:var(--s4)">${h.dimensions.map(d => `<span title="${esc(d.reasons.join('; '))}">${health(d.level).replace(HEALTH[d.level], esc(d.name))} <span class="mut">${esc(d.reasons[0])}</span></span>`).join('')}</div></div></div>` + dvHtml + html
    + (o.recent.length ? `<div class="cx" style="margin-top:14px"><div class="card"><h3>What happened <span class="sp"></span><a class="xs" ${act('story')}>the whole story</a></h3>${feed(o.recent.slice(0, 8))}</div></div>` : ''); };
Object.assign(ACT, {async dvCap(d) { const r = await post('/delivery', {max_tabs: +d.a0}); if (r.ok === false) return toast(r.error.message, true); toast('Delivery tabs: ' + r.capacity + '. Agents are not affected.'); render(true); },
  palette: () => palette(true), story: () => { S.ctab = 'story'; go('company'); }});

document.addEventListener('click', e => {
  const t = e.target;
  if (t.closest('[data-pal]')) return palRun(+t.closest('[data-pal]').dataset.pal);
  if (t.closest('[data-pclose]')) return palette(false);
  const m = t.closest('[data-mclose]'), d = t.closest('[data-do]'), o = t.closest('[data-open]'), c = t.closest('[data-close]');
  if (d && ACT[d.dataset.do]) { e.preventDefault(); e.stopPropagation(); if (m) modal(''); if (c) closeDrawer(); ACT[d.dataset.do](d.dataset); return; }
  if (m) modal('');
  if (o) { e.preventDefault(); if (c) closeDrawer(); drawer(o.dataset.open, o.dataset.a0, o.dataset.a1); return; }
  if (c) closeDrawer();
});
document.addEventListener('keydown', e => {
  const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || '');
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palette(!$('pal').innerHTML); }
  else if (e.key === 'Escape') { if ($('pal').innerHTML) palette(false); else if ($('modal').innerHTML) modal(''); else if (S.drawer) closeDrawer(); }
  else if ((e.key === 'Delete' || e.key === 'Backspace') && !typing && state.tab === 'n8n' && W.tab === 'build' && W.sel) { e.preventDefault(); ACT.wfDelNode(); }
});
window.addEventListener('beforeunload', e => { if (W.dirty && W.cur && W.cur.nodes.length) { e.preventDefault(); e.returnValue = ''; } });
document.querySelector('aside .foot').insertAdjacentHTML('beforebegin', `<a href="/company" onclick="try{localStorage.setItem('hubui','company')}catch(e){}" title="The same company shown as a simpler, company-style view" style="display:block;margin:auto 4px 8px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;color:var(--fg);text-decoration:none;font-weight:600;text-align:center">Switch to company view</a>`);
document.querySelector('aside .foot').style.marginTop = '0';
const wanted = location.hash.slice(1);
if (TABS.some(t => t[0] === wanted)) state.tab = wanted;
nav(); render();
})();
