/* EmaraAI company UI. Every number and status on screen comes from /api/v1/company/* (real rows); nothing is made up here. */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
let KEY = ''; try { KEY = localStorage.getItem('hubkey') || ''; } catch (e) {}
try { const t = localStorage.getItem('hubtheme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
async function api(p, opt = {}) {
  opt.headers = Object.assign({'Content-Type': 'application/json'}, KEY ? {Authorization: 'Bearer ' + KEY} : {}, opt.headers || {});
  try { const r = await fetch('/api/v1' + p, opt); const data = await r.json().catch(() => ({ok: false, error: {message: 'The server returned an unreadable response'}}));
    if (!r.ok) return {ok: false, error: data.error || {message: 'Request failed (' + r.status + ')'}}; return data;
  } catch (e) { return {ok: false, error: {message: 'Could not connect to the hub. Check the connection and retry.'}}; }
}
const post = (p, b) => api(p, {method: 'POST', body: JSON.stringify(b || {})});
const del = (p, b) => api(p, {method: 'DELETE', body: JSON.stringify(b || {})});
const qs = o => Object.entries(o).filter(([, v]) => v !== '' && v != null).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');
function toast(m, bad) { const t = $('toast'); t.textContent = m; t.className = bad ? 'bad' : ''; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', 4500); }
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
  act: '<path d="M3 12h4l3-8 4 16 3-8h4"/>', flow: '<rect x="3" y="4" width="6" height="5" rx="1"/><rect x="15" y="15" width="6" height="5" rx="1"/><path d="M9 6.5h4a3 3 0 0 1 3 3V15"/>', msg: '<path d="M21 12a8 8 0 0 1-12 7l-5 1 1-4.5A8 8 0 1 1 21 12z"/>', know: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM8 7h7M8 11h7"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5"/>', rep: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', dec: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  adv: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.3l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2.3-1.3L14 3h-4l-.3 2.5a7 7 0 0 0-2.3 1.3l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .9.1 1.3l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2.3 1.3L10 21h4l.3-2.5a7 7 0 0 0 2.3-1.3l2.3 1 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', plus: '<path d="M12 5v14M5 12h14"/>'};
const ic = n => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[n]}</svg>`;

/* ---------- state, routing, live updates ---------- */
const S = {route: [], pulse: '', decisions: 0, drawer: null, org: {zoom: 1, closed: {}, q: '', project: ''}, feed: {category: 'all'}, board: {}, chat: {}, rep: {kind: 'daily'}, know: {tab: 'company', q: '', cat: ''}};
const NAV = [['', 'Command Center', 'home'], ['company', 'Company', 'org'], ['projects', 'Projects', 'proj'], ['tasks', 'Tasks', 'task'], ['activity', 'Activity', 'act'],
  ['office', 'My assistants', 'org'], ['workflows', 'Workflows', 'flow'], ['messages', 'Communication', 'msg'], ['knowledge', 'Knowledge', 'know'], ['files', 'Deliverables', 'file'], ['reports', 'Reports', 'rep'], ['decisions', 'Decisions', 'dec']];
/* the technical pages: shown inside this interface (they keep their own logic; see /advanced?embed=1) */
const OPS = [['setup', 'Setup', 'adv'], ['ops/conn', 'Connections', 'adv'], ['ops/settings', 'Settings', 'adv'], ['pc', 'PC load', 'act'], ['tools', 'Tools & cost', 'rep'], ['quality', 'Quality', 'task'], ['api', 'API', 'flow'], ['remote', 'Phone & remote', 'adv'],
  ['ops/recovery', 'Recovery', 'adv'], ['ops/plan', 'Plan', 'task'], ['ops/n8n', 'n8n', 'flow'], ['ops/logs', 'Logs', 'file'], ['vault', 'Backups & trash', 'file'], ['ops/diag', 'Diagnostics', 'adv'], ['ops/about', 'About', 'adv']];
const OPS_TITLE = {conn: ['Connections', 'The extension, ChatGPT, the public address, the plugins'], settings: ['Settings', 'Every setting of the hub, with what it does'],
  recovery: ['Recovery', 'What broke, what was tried, and what needs you'], plan: ['Plan', 'The design of a project and its steps'], n8n: ['n8n', 'Events sent to n8n and the workflows the Master may start'],
  logs: ['Logs', 'What the hub wrote down'], team: ['Team profiles', 'Edit who a person is: name, career, way of working'], diag: ['Diagnostics', 'A full health check, on demand'], about: ['About', 'Version, folders, plugins']};
function nav() {
  const cur = S.route[0] === 'rooms' ? 'messages' : S.route[0] === 'ops' ? 'ops/' + (S.route[1] || '') : (S.route[0] || '');
  const link = ([k, label, i]) => `<a href="#/${k}" class="${cur === k ? 'on' : ''}">${ic(i)}<span>${label}</span>${k === 'decisions' && S.decisions ? `<i class="n">${S.decisions}</i>` : ''}</a>`;
  const open = S.opsOpen || cur.startsWith('ops/') || ['setup', 'pc', 'tools', 'api', 'quality', 'remote'].includes(cur);
  $('nav').innerHTML = NAV.map(link).join('') + `<div class="sep"></div><a ${act('opsToggle')} title="Connections, settings, PC load, tools, recovery, logs">${ic('adv')}<span>Operations</span><span class="sp"></span><span class="xs dim">${open ? '−' : '+'}</span></a>`
    + (open ? OPS.map(link).join('') : '');
}
const go = h => { if (location.hash === h) render(); else location.hash = h; };
async function render(keepScroll) {
  const generation = S.renderGeneration = (S.renderGeneration || 0) + 1, routeHash = location.hash;
  S.route = location.hash.replace(/^#\/?/, '').split('/').map(part => { try { return decodeURIComponent(part); } catch (e) { return part; } });
  const page = S.route[0] || 'home';
  nav();
  const view = $('view'), top = view.scrollTop;
  const fn = VIEWS[page] || VIEWS.home;
  const html = await fn(...S.route.slice(1));
  if (generation !== S.renderGeneration || location.hash !== routeHash) return;
  view.innerHTML = `<div class="page ${keepScroll ? '' : 'fresh'}">${html}</div>`;
  view.scrollTop = keepScroll ? top : 0;
  if (AFTER[page]) AFTER[page]();
}
const typing = () => { const a = document.activeElement; return a && /INPUT|TEXTAREA|SELECT/.test(a.tagName); };
async function tick() {
  const p = await api('/company/pulse');
  if (!p.ok || p.key === S.pulse) return;
  if (typing() || S.dragging || $('modal').innerHTML || $('pal').innerHTML) return;
  S.pulse = p.key;
  if (['workflows', 'ops'].includes((S.route || [])[0])) return refreshBadge();      // the canvas keeps its own state: a redraw would drop what is being edited
  if ($('actframe')) return refreshBadge();      // the events list refreshes itself (Live); redrawing the page would reload it
  refreshBadge();
  await render(true);
  if (S.drawer) drawer(S.drawer.kind, ...S.drawer.args, true);
}
async function refreshBadge() { const d = await api('/company/decisions'); if (d.ok) { S.decisions = d.questions.length + d.approvals.length; nav(); } }

/* ---------- views ---------- */
const VIEWS = {}, AFTER = {};
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
  const go2 = a.kind === 'office' ? `href="#/office"` : a.kind === 'decision' ? `href="#/decisions"` : a.kind === 'task' ? open('task', a.id) : a.who_key ? open('person', a.project, a.who_key) : `href="#/ops/recovery"`;
  const body = `<a class="need ${a.tone}" style="display:block" ${go2}><div class="b">${esc(a.title)}</div><div class="sm mut">${esc(a.who)}${a.project ? ' · ' + esc(a.project) : ''}${a.note ? ' — ' + esc(a.note) : ''}</div></a>`;
  return a.action ? `<div>${body}<div style="margin:-4px 0 10px"><button class="s pri" ${act('needAct', a.action.url, a.action.label)}>${esc(a.action.label)}</button></div></div>` : body;
};
const projCard = p => `<a class="card click" href="#/projects/${encodeURIComponent(p.name)}"><div class="row"><h2 class="clip sp">${esc(p.name)}</h2>${p.status === 'active' ? health(p.health) : tag(cap(p.status))}</div>
  <p class="sm mut" style="margin:4px 0 12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(p.goal)}</p>
  <div class="row sm"><span class="b">${p.progress}%</span>${bar(p.progress, p.progress === 100 ? 'ok' : '')}</div>
  <div class="row sm mut" style="margin-top:10px"><span>${p.team} people</span><span>${p.counts.in_progress + p.counts.pending} open</span>${p.counts.review ? `<span>${p.counts.review} in review</span>` : ''}${p.counts.blocked ? `<span style="color:var(--warn)">${p.counts.blocked} blocked</span>` : ''}<span class="sp"></span><span>${p.counts.done} done</span></div></a>`;

VIEWS.home = async () => {
  const o = await api('/company/overview');
  if (!o.ok) return empty('The hub is not answering', o.error.message);
  S.decisions = o.counts.decisions; S.company = o.company; nav(); brand(o.company);
  const c = o.counts, h = o.health;
  const [ap, dv] = await Promise.all([api('/approvals'), api('/delivery')]);
  const approvals = (ap.approvals || []).map(approvalCard).join('');
  if (!o.projects.length) return `<div class="ph"><div><h1>${esc(o.company.name)}</h1><p>${esc(o.company.tagline)}</p></div></div>
    <div class="card">${empty('Your company has no initiatives yet', 'Start a project: the Master plans it, puts a team together and runs it. You watch, decide and approve.', `<button class="pri" ${act('newProject')}>${ic('plus')} Start the first project</button>`)}</div>`;
  return `<div class="ph"><div class="sp"><h1>${esc(o.company.name)}</h1><p>${esc(o.company.tagline)} · Master ${status(o.master.status)} <span class="mut">— ${esc(o.master.activity)}</span></p></div>
      <button ${act('briefing')}>Briefing</button><button class="pri" ${act('newProject')}>${ic('plus')} New project</button></div>
    <div class="card" style="margin-bottom:var(--s4)"><div class="row wrap" style="gap:var(--s5)"><div style="min-width:210px"><h3>Company health</h3><div style="font-size:18px;margin-top:4px">${health(h.level)}</div><div class="sm mut">${esc(h.headline)}</div></div>
      <div class="stats sp"><div class="stat"><b>${c.projects_active}</b><span>projects running</span></div><div class="stat"><b>${c.agents_active}<span class="mut" style="font-size:14px"> / ${c.agents}</span></b><span>people working</span></div>
      <div class="stat"><b>${c.tasks_in_progress}</b><span>tasks open</span></div><div class="stat"><b>${c.tasks_review}</b><span>in review</span></div><div class="stat"><b style="${c.tasks_blocked ? 'color:var(--warn)' : ''}">${c.tasks_blocked}</b><span>blocked</span></div>
      <div class="stat"><b>${c.done_today}</b><span>done today</span></div><div class="stat"><b style="${c.decisions ? 'color:var(--acc)' : ''}">${c.decisions}</b><span>decisions for you</span></div></div></div>
      <div class="row wrap sm" style="margin-top:var(--s3);gap:var(--s4)">${h.dimensions.map(d => `<span title="${esc(d.reasons.join('; '))}">${health(d.level).replace(HEALTH[d.level], esc(d.name))} <span class="mut">${esc(d.reasons[0])}</span></span>`).join('')}</div></div>
    <div style="margin-bottom:var(--s4)">${deliveryCard(dv)}</div>
    <div class="hq"><div class="col left">
        <div class="card"><h3>Departments</h3><div class="list">${o.departments.map(d => `<a ${open('dept', d.name)}><div class="sp clip"><b>${esc(d.name)}</b><div class="xs mut">${d.members} people · ${d.tasks.active} active${d.tasks.blocked ? ' · ' + d.tasks.blocked + ' blocked' : ''}</div></div>${d.working ? tag(d.working + ' working', 'ok') : ''}</a>`).join('') || '<div class="mut sm">No departments yet. They appear when the Master builds a team.</div>'}</div></div>
        <div class="card"><h3>Done today</h3>${o.achievements.length ? `<div class="list">${o.achievements.map(a => `<a ${open('task', a.task_id)}><div class="clip sp">${esc(a.title)}<div class="xs mut">${esc(a.by)}</div></div></a>`).join('')}</div>` : '<div class="mut sm">Nothing was completed yet today.</div>'}</div>
      </div>
      <div class="col">
        <div class="card"><h3>Live operations <span class="sp"></span><a class="xs" href="#/company/people">everyone</a></h3>${o.live.length ? `<div class="list">${o.live.map(p => `<a ${open('person', p.project, p.key)}>${who(p, p.role + ' · ' + p.department)}<div class="sp clip sm" style="text-align:right">${esc(p.activity)}<div class="xs mut">${esc(p.project)}</div></div></a>`).join('')}</div>`
          : `<div class="mut sm">Nobody is working right now.${o.idle.length ? ' ' + o.idle.length + ' people have no assigned work.' : ''}</div>`}</div>
        <div class="card"><h3>Projects <span class="sp"></span><a class="xs" href="#/projects">all</a></h3><div class="grid g2">${o.projects.slice(0, 6).map(projCard).join('')}</div></div>
        <div class="card"><h3>Recently <span class="sp"></span><a class="xs" href="#/activity">all activity</a></h3>${o.recent.length ? feed(o.recent) : '<div class="mut sm">Nothing has happened yet. Your company is waiting for its first assignment.</div>'}</div>
      </div>
      <div class="col"><div class="card"><h3>Needs your attention</h3>${approvals}${o.attention.map(needCard).join('') || (approvals ? '' : '<div class="mut sm">Nothing needs you. The company is running on its own.</div>')}</div></div>
    </div>`;
};
const approvalCard = x => `<div class="need err"><div class="b">${x.agent ? esc(x.agent) + ' wants' : 'A chat wants'} to run a command that ${esc(x.reason)}</div>
  <div class="sm mut">${x.project ? esc(x.project) + ' · ' : ''}Nothing runs until you say yes. Only this exact command, once.</div><pre>${esc(x.command)}</pre>
  <div class="row" style="margin-top:8px"><button class="pri s" ${act('approve', x.id, '1')}>Approve and run</button><button class="s" ${act('allowSimilar', x.id)} title="Approve this one and every command of the same kind in this project, now and from now on">Allow all similar${x.similar > 1 ? ' (' + x.similar + ' waiting)' : ''}</button><button class="s" ${act('approve', x.id, '')}>Reject</button><span class="xs dim">expires ${hm(x.expires)}</span></div></div>`;

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
/* company: org chart, people, departments */
VIEWS.company = async (tab = 'chart') => {
  const o = await api('/company/org');
  if (!o.ok) return empty('Could not load the company', o.error.message);
  S.orgData = o;
  const head = `<div class="ph"><div class="sp"><h1>Company</h1><p>${o.people.filter(p => p.kind === 'agent').length} people in ${o.departments.length} departments, across ${o.projects.length} projects</p></div>
    <div class="seg">${[['chart', 'Org chart'], ['people', 'People'], ['departments', 'Departments']].map(([k, l]) => `<a href="#/company/${k}" class="${tab === k ? 'on' : ''}">${l}</a>`).join('')}</div>
    <button ${act('newDept')}>New department</button><button class="pri" ${act('hire')}>${ic('plus')} Hire</button></div>`;
  if (!o.people.length) return head + `<div class="card">${empty('Your company has no employees yet', 'Start a project and the Master hires the team it needs. You can also hire people yourself.', `<button class="pri" ${act('newProject')}>Start a project</button>`)}</div>`;
  if (tab === 'people') return head + `<div class="card" style="padding:0"><table><tr><th>Person</th><th>Department</th><th>Project</th><th>Status</th><th>Now</th><th style="width:150px">Workload</th><th>Done</th></tr>
    ${o.people.filter(p => p.kind === 'agent').map(p => `<tr class="click" ${open('person', p.project, p.key)}><td>${who(p)}</td><td>${esc(p.department)}</td><td class="mut">${esc(p.project)}</td><td>${status(p.status)}</td><td class="sm mut clip" style="max-width:260px">${esc(p.activity)}</td>
      <td><div class="row sm">${bar(p.workload.pct, loadTone(p.workload.band))}<span class="mut" style="width:34px">${p.workload.pct}%</span></div></td><td>${p.performance.done}</td></tr>`).join('')}</table></div>`;
  if (tab === 'departments') return head + `<div class="grid gauto">${o.departments.map(d => `<a class="card click" ${open('dept', d.name)}><div class="row"><h2 class="sp">${esc(d.name)}</h2>${health(d.health)}</div>
      <p class="sm mut" style="margin:4px 0 12px;min-height:38px">${esc(d.mission || 'No mission written yet.')}</p>
      <div class="sm">${d.manager ? 'Led by <b>' + esc(d.manager) + '</b>' : '<span class="mut">No lead yet</span>'}</div>
      <div class="row sm mut" style="margin-top:8px"><span>${d.members} people</span><span>${d.tasks.active} active</span><span>${d.tasks.done} done</span><span class="sp"></span><span>load ${d.workload}%</span></div></a>`).join('')}</div>`;
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
AFTER.company = () => {
  const w = $('orgwrap'); if (!w) return;
  let drag = null;
  w.onmousedown = e => { if (e.target.closest('.node')) return; drag = {x: e.clientX, y: e.clientY, l: w.scrollLeft, t: w.scrollTop}; w.classList.add('drag'); };
  window.onmouseup = () => { drag = null; w.classList.remove('drag'); };
  w.onmousemove = e => { if (drag) { w.scrollLeft = drag.l - (e.clientX - drag.x); w.scrollTop = drag.t - (e.clientY - drag.y); } };
  w.onwheel = e => { if (e.ctrlKey) { e.preventDefault(); ACT.zoom({a0: e.deltaY < 0 ? '+' : '-'}); } };
  w.onmouseover = e => { const n = e.target.closest('.node[data-id]'); w.querySelectorAll('.node.line').forEach(x => x.classList.remove('line')); let cur = n; while (cur && cur.dataset.boss) { cur = w.querySelector(`.node[data-id="${cur.dataset.boss}"]`); if (cur) cur.classList.add('line'); } };
  if (!S.org.scrolled) { w.scrollLeft = (w.scrollWidth - w.clientWidth) / 2; S.org.scrolled = 1; } else { w.scrollLeft = S.org.l || 0; w.scrollTop = S.org.t || 0; }
  w.onscroll = () => { S.org.l = w.scrollLeft; S.org.t = w.scrollTop; };
const BUSY = v => { S.dragging = v; };
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

/* projects */
VIEWS.projects = async (name, tab = 'overview') => {
  if (name) return projectWorkspace(name, tab);
  const o = await api('/company/overview');
  if (!o.ok) return empty('Could not load projects', o.error.message);
  const head = `<div class="ph"><div class="sp"><h1>Projects</h1><p>The company's initiatives</p></div><button ${act('importProject')}>Import project</button><button class="pri" ${act('newProject')}>${ic('plus')} New project</button></div>`;
  if (!o.projects.length) return head + `<div class="card">${empty('Your company has no active initiatives', 'Describe what you want built. The Master writes the plan and the architecture, hires the team and runs the work.', `<button class="pri" ${act('newProject')}>Create project</button>`)}</div>`;
  return head + `<div class="grid gauto">${o.projects.map(projCard).join('')}</div>`;
};
const PTABS = [['overview', 'Overview'], ['plan', 'Plan'], ['tasks', 'Tasks'], ['team', 'Team'], ['activity', 'Activity'], ['messages', 'Communication'], ['files', 'Deliverables'], ['knowledge', 'Knowledge'], ['report', 'Report']];
async function projectWorkspace(name, tab) {
  const o = await api('/company/org');
  const p = (o.projects || []).find(x => x.name === name);
  if (!p) return empty('This project does not exist', 'It may have been deleted.', `<button ${act('goto', '#/projects')}>All projects</button>`);
  const team = o.people.filter(x => x.project === name), e = encodeURIComponent(name);
  const head = `<div class="ph"><div class="sp"><div class="xs mut"><a href="#/projects">Projects</a> /</div><h1>${esc(p.name)}</h1><p>${tag(cap(p.status), p.status === 'active' ? 'ok' : '')} ${p.status === 'active' ? health(p.health) : ''} · ${p.progress}% · ${p.team} people · <a ${act('pfolder', name, p.folder || '')} title="Where the project's files live on this PC" style="text-decoration:underline">${p.folder ? esc(p.folder) : 'no folder set'}</a></p></div>
      ${p.status === 'active' ? `<button ${act('pstatus', name, 'paused')}>Pause</button>` : `<button ${act('pstatus', name, 'active')}>Resume</button>`}<button class="danger" ${act('del', 'project', name, '', name)} title="Remove the whole project from the hub (kept in the trash)">Delete</button><button ${act('roomOpen', name)} title="Put people of this project at a round table to decide a question together">Decision room</button><button ${act('exportProject', name)} title="Download the whole project as one file: people, tasks, messages, memory, plan, history and stored files">Export</button><button ${act('assign', name)}>Assign task</button><button class="pri" ${act('talk', name, 'master')}>Message the Master</button></div>
    <div class="seg" style="margin-bottom:var(--s4)">${PTABS.map(([k, l]) => `<a href="#/projects/${e}/${k}" class="${tab === k ? 'on' : ''}">${l}</a>`).join('')}</div>`;
  let body = '';
  if (tab === 'overview') {
    const [feedR, att, plan] = await Promise.all([api('/company/activity?' + qs({project: name, limit: 10})), api('/company/decisions'), api(`/projects/${e}/plan`)]);
    const risks = (att.attention || []).filter(a => a.project === name), qsn = (att.questions || []).filter(q => q.project === name);
    body = `<div class="grid" style="grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)"><div class="col">
        <div class="card"><h3>Objective</h3><div class="prose" style="max-height:200px;overflow:auto">${esc(p.goal)}</div></div>
        <div class="card"><h3>Strategy <span class="sp"></span><a class="xs" href="#/projects/${e}/plan">full plan</a></h3>${plan.exists ? `<div class="prose sm" style="max-height:150px;overflow:hidden">${esc(plan.overview)}</div><div class="row sm" style="margin-top:10px"><span class="b">${plan.done}/${plan.total} steps</span>${bar(plan.percent, 'ok')}</div>` : '<div class="mut sm">The Master has not written the plan yet. It does that first, before any work is given out.</div>'}</div>
        <div class="card"><h3>Work</h3><div class="row sm" style="margin-bottom:10px"><span class="b">${p.progress}%</span>${bar(p.progress)}</div><div class="stats">${[['pending', 'ready'], ['in_progress', 'in progress'], ['review', 'in review'], ['blocked', 'blocked'], ['done', 'done'], ['failed', 'failed']].map(([k, l]) => `<div class="stat"><b>${p.counts[k]}</b><span>${l}</span></div>`).join('')}</div></div>
        <div class="card"><h3>Activity <span class="sp"></span><a class="xs" href="#/projects/${e}/activity">all</a></h3>${(feedR.items || []).length ? feed(feedR.items, {noProject: 1}) : '<div class="mut sm">Nothing has happened in this project yet.</div>'}</div></div>
      <div class="col"><div class="card"><h3>Decisions for you</h3>${qsn.map(q => needCard({kind: 'decision', tone: 'accent', title: q.question, who: q.from_person || q.from, project: ''})).join('') || '<div class="mut sm">Nothing is waiting for you here.</div>'}</div>
        <div class="card"><h3>Risks and blockers</h3>${risks.map(needCard).join('') || '<div class="mut sm">No blockers.</div>'}</div>
        <div class="card"><h3>Team <span class="sp"></span><a class="xs" href="#/projects/${e}/team">all</a></h3><div class="list">${team.slice(0, 8).map(x => `<a ${open('person', name, x.key)}>${who(x)}<span class="sp"></span>${status(x.status)}</a>`).join('')}</div></div></div></div>`;
  } else if (tab === 'plan') {
    const plan = await api(`/projects/${e}/plan`);
    body = plan.exists ? `<div class="grid g2"><div class="card"><h3>Overview</h3><div class="prose">${esc(plan.overview)}</div></div><div class="card"><h3>Architecture</h3><div class="prose">${esc(plan.architecture)}</div></div></div>
      <div class="card" style="margin-top:var(--s4)"><h3>Steps <span class="sp"></span><span>${plan.done}/${plan.total} · ${plan.percent}%</span></h3><div class="list">${plan.steps.map(s => `<div><span class="mono dim" style="width:34px">${s.step}</span><div class="sp"><b>${esc(s.title)}</b><div class="sm mut">${esc(s.details)}</div></div><span class="sm mut">${esc(s.agent)}</span>${tag(cap(s.status), {done: 'ok', doing: 'accent', blocked: 'warn'}[s.status] || '')}${s.task_id ? `<a class="xs" ${open('task', s.task_id)}>task</a>` : ''}</div>`).join('')}</div>
      <div class="xs mut" style="margin-top:10px">To edit the plan or check steps by hand, use <a href="#/ops/plan" style="text-decoration:underline">Operations › Plan</a>.</div></div>`
      : `<div class="card">${empty('No plan yet', 'The Master writes the plan and the architecture in its first reply, before it gives out any work.')}</div>`;
  } else if (tab === 'tasks') { S.board.project = name; body = await boardHtml(); }
  else if (tab === 'team') body = `<div class="row" style="margin-bottom:var(--s3)"><span class="sp"></span><button class="pri" ${act('hire', name)}>${ic('plus')} Hire into this project</button></div><div class="grid gauto">${team.map(personCard).join('')}</div>`;
  else if (tab === 'activity') { const f = await api('/company/activity?' + qs({project: name, limit: 120})); body = `<div class="card">${f.items.length ? feed(f.items, {noProject: 1}) : empty('No activity yet', 'This project is waiting for its first assignment.')}</div>`; }
  else if (tab === 'messages') { S.chat.project = name; body = await chatHtml(); }
  else if (tab === 'files') body = filesHtml(await api('/company/artifacts?' + qs({project: name})));
  else if (tab === 'knowledge') { const m = await api('/company/memory?' + qs({project: name})); body = memoryHtml(m, true); }
  else if (tab === 'report') { const r = await api('/company/report?' + qs({kind: 'project', project: name})); body = reportHtml(r.report); }
  return head + body;
}
AFTER.projects = () => { if (S.route[2] === 'messages') AFTER.messages(); };
const personCard = p => `<a class="card click" ${open('person', p.project, p.key)}><div class="row">${who(p)}<span class="sp"></span>${status(p.status)}</div>
  <div class="sm mut clip" style="margin:10px 0 8px">${esc(p.activity)}</div>${p.kind === 'agent' ? `<div class="row xs mut">${bar(p.workload.pct, loadTone(p.workload.band))}<span>${p.workload.active} open · ${p.performance.done} done</span></div>` : `<div class="xs mut">Runs ${esc(p.project)}</div>`}</a>`;

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
  return `<div class="ph"><div class="sp"><h1>Tasks</h1><p>Work orders moving through the company: assigned, accepted, worked, reported, reviewed, done</p></div>
    ${sel('bp', 'All projects', (o.projects || []).map(p => p.name), S.board.projectName)}${sel('bd', 'All departments', (o.departments || []).map(d => d.name), S.board.department)}<button class="pri" ${act('assign', S.board.projectName || '')}>${ic('plus')} Assign task</button></div>` + await boardHtml();
};
AFTER.tasks = () => { $('bp').onchange = e => { S.board.projectName = e.target.value; render(); }; $('bd').onchange = e => { S.board.department = e.target.value; render(); }; };

/* activity */
const CATS = [['all', 'All'], ['company', 'Company'], ['projects', 'Projects'], ['agents', 'People'], ['tasks', 'Tasks'], ['messages', 'Messages'], ['errors', 'Problems'], ['decisions', 'Decisions'], ['deliverables', 'Deliverables']];
VIEWS.activity = async () => {
  S.feed.mode = S.feed.mode || 'events';
  const modes = `<div class="seg">${[['events', 'Events'], ['story', 'Story']].map(([k, l]) => `<a ${act('feedMode', k)} class="${S.feed.mode === k ? 'on' : ''}">${l}</a>`).join('')}</div>`;
  if (S.feed.mode === 'events')      // every event, tool call and trace, with the filters, groups and export of the Control Center's Activity page
    return `<div class="ph"><div class="sp"><h1>Activity</h1><p>Every event, tool call and recovery, newest first. Click a row for its details and full trace.</p></div>${modes}</div>
      <iframe id="actframe" src="/advanced?embed=1#activity" title="Activity" style="width:100%;height:calc(100vh - 140px);min-height:520px;border:0;border-radius:12px;background:transparent"></iframe>`;
  const f = await api('/company/activity?' + qs({category: S.feed.category, limit: 150}));
  return `<div class="ph"><div class="sp"><h1>Activity</h1><p>What the company has been doing, in order</p></div>${modes}<div class="seg">${CATS.map(([k, l]) => `<a ${act('feedCat', k)} class="${S.feed.category === k ? 'on' : ''}">${l}</a>`).join('')}</div></div>
    <div class="card">${(f.items || []).length ? feed(f.items) : empty('Nothing has happened yet', 'Your company is waiting for its first assignment.')}
    <div class="xs mut" style="margin-top:var(--s3)">Every raw event, tool call and trace is under "Events" above.</div></div>`;
};

/* my assistants: people hired outside any project, who work for the owner directly */
VIEWS.office = async () => {
  const o = await api('/office');
  if (!o.ok) return empty('Could not load your assistants', o.error.message);
  S.office = o;
  const wait = o.waiting.map(t => `<div class="card"><div class="row"><b class="sp">${esc(t.title)}</b>${tag(TASK[t.status] || t.status, TONE[t.status] || '')}<a class="xs" ${open('task', t.id)}>open</a></div>
      <div class="xs mut" style="margin:2px 0 8px">${esc(t.agent)} · ${ago(t.updated)}</div><div class="prose sm" style="white-space:pre-wrap;max-height:220px;overflow:auto">${esc(t.summary || 'No summary was written.')}</div>
      ${t.details ? `<details style="margin-top:6px"><summary class="xs mut">Details</summary><pre style="max-height:240px;overflow:auto">${esc(t.details)}</pre></details>` : ''}
      ${t.files.length ? `<div class="xs mono mut" style="margin-top:6px">${t.files.map(esc).join('<br>')}</div>` : ''}
      <textarea id="of_fb_${t.id}" rows="2" style="width:100%;margin-top:10px" placeholder="${t.status === 'review' ? 'What should change (only needed when you ask for changes)' : 'Your answer or instruction'}"></textarea>
      <div class="row" style="margin-top:8px">${t.status === 'review' ? `<button class="pri s" ${act('officeReview', t.id, 'accept')}>Accept</button><button class="s" ${act('officeReview', t.id, 'request_changes')}>Ask for changes</button>`
        : `<button class="pri s" ${act('officeUnblock', t.id)}>Answer and continue</button>`}<button class="s" ${act('officeReview', t.id, 'cancel')}>Cancel the task</button></div></div>`).join('');
  const mail = o.inbox.map(m => `<div class="card"><div class="row"><b class="sp">${esc(m.from)}</b>${tag(m.kind, KIND[m.kind] || '')}<span class="xs mut">${ago(m.ts)}</span></div>
      <div class="prose sm" style="white-space:pre-wrap;margin:6px 0;max-height:200px;overflow:auto">${esc(m.text)}</div>
      <div class="row"><input id="of_re_${m.id}" class="sp" placeholder="Your answer"><button class="pri s" ${act('officeReply', m.id, m.from_key)}>Answer</button><button class="s" ${act('officeReply', m.id, '')}>Mark as read</button></div></div>`).join('');
  return `<div class="ph"><div class="sp"><h1>My assistants</h1><p>People who work for you directly, outside any project. You give the work; their reports come back here.</p></div>
      <button ${act('officeTask')} ${o.people.length ? '' : 'disabled'}>Give work</button><button ${act('roomOpen', '@office')} ${o.people.length > 1 ? '' : 'disabled'} title="Your assistants discuss a question in turns and decide it together; you break a tie">Decision room</button><button ${act('officeBring')} title="Someone who already works in one of your projects becomes your assistant too">Bring someone from a project</button><button class="pri" ${act('officeHire')}>Hire an assistant</button></div>
    ${o.people.length ? '' : `<div class="card">${empty('Nobody works for you yet', 'Hire an assistant: a researcher, a writer, an analyst, a general helper. Then give them work in plain words.')}</div>`}
    <div class="grid" style="grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);align-items:start"><div class="col">
      <h3 style="margin:0 0 4px">Waiting for you ${o.waiting.length + o.inbox.length ? tag(String(o.waiting.length + o.inbox.length), 'accent') : ''}</h3>
      ${wait + mail || '<div class="card"><div class="mut sm">Nothing is waiting for you. Finished work and questions appear here.</div></div>'}
      <div class="card"><h3>All work</h3>${o.tasks.length ? `<div class="list">${o.tasks.map(t => `<a ${open('task', t.id)}><div class="sp clip">${esc(t.title)}<div class="xs mut">${esc(t.agent)} · ${ago(t.updated)}</div></div>${t.status === 'in_progress' ? `<b class="xs">${t.progress}%</b>` : ''}${tag(TASK[t.status] || t.status, TONE[t.status] || '')}</a>`).join('')}</div>` : '<div class="mut sm">No work has been given yet.</div>'}</div></div>
      <div class="col"><div class="card"><h3>People</h3>${o.people.length ? `<div class="list">${o.people.map(p => `<a ${open('person', o.project, p.key)}>${who(p)}<span class="sp"></span>${status(p.status)}</a>`).join('')}</div>` : '<div class="mut sm">Nobody yet.</div>'}</div></div></div>`;
};
/* communication: people on the left, the selected person's conversation in the middle, the task in focus on the right */
S.chat = {project: '', who: '*', to: 'master', task: '', files: [], all: false, open: {}, fold: {}, ctx: {}, dir: {}, side: (() => { try { return localStorage.getItem('emara.chatSide') !== '0'; } catch (e) { return true; } })(),
  nolist: (() => { try { return localStorage.getItem('emara.chatList') === '0'; } catch (e) { return false; } })()};
try { if (localStorage.getItem('emara.navmin') === '1') document.body.classList.add('navmin'); } catch (e) {}
const KIND = {report: 'ok', question: 'warn', answer: 'accent', control: 'warn'};
const fileBox = fs => (fs || []).length ? `<div class="files">${fs.map(f => f.type.startsWith('image/') ? `<a href="/api/v1/files/${f.file_id}" target="_blank"><img src="/api/v1/files/${f.file_id}" alt="${esc(f.name)}" loading="lazy"></a>` : `<a class="tag" href="/api/v1/files/${f.file_id}" target="_blank">${esc(f.name)} · ${size(f.size)}</a>`).join('')}</div>` : '';
/* above the conversation: how far the project is, what is left, and an estimate of the time it still needs */
const span = h => h < 1 ? Math.max(5, Math.round(h * 60 / 5) * 5) + ' min' : h < 36 ? (Math.round(h * 2) / 2) + ' h' : (Math.round(h / 24 * 10) / 10) + ' days';
const outlookStrip = (o, name) => { if (!o) return '';
  const c = o.counts, chip = (n, label, tone) => n ? `<span class="tag ${tone}">${n} ${label}</span>` : '';
  const e = o.eta, when = e ? new Date(e.finish_at * 1000) : null;
  const eta = o.status !== 'active' ? `<span class="mut">The project is ${esc(o.status === 'done' ? 'finished' : o.status)}.</span>`
    : !o.total && !o.plan_left.length ? '<span class="mut">No tasks have been created yet.</span>'
    : !o.left_total && !o.plan_left.length ? '<b style="color:var(--ok)">Everything is done.</b>'
    : e ? `<b>About ${span(e.hours)} left</b> <span class="mut">(likely ${span(e.low)} – ${span(e.high)}) · around ${dayOf(e.finish_at) === 'Today' ? 'today' : esc(when.toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'}))} ${hm(e.finish_at)} at the current pace of ${e.per_day} tasks a day</span>`
    : `<span class="mut">${esc(o.note)}</span>`;
  const row = t => `<a class="lrow" ${act('chatTask', t.id)}><span class="dot st-${t.status}"></span><span class="sp clip">${esc(t.title)}</span><span class="xs mut clip" style="max-width:230px">${esc(t.agent)}${t.role ? ' · ' + esc(t.role) : ''}</span>${tag(TASK[t.status] || t.status, TONE[t.status])}<span class="lbar">${bar(t.progress, t.status === 'blocked' || t.status === 'failed' ? 'warn' : '')}</span><b class="xs" style="width:34px;text-align:right">${t.progress}%</b></a>`;
  return `<div class="pstrip"><div class="prow"><b class="pct">${o.progress}%</b><div class="pmid"><div style="display:flex">${bar(o.progress, o.progress >= 100 ? 'ok' : '')}</div>
        <div class="row wrap xs" style="margin-top:6px;gap:6px"><span class="mut">${c.done} of ${o.total} tasks done</span>${chip(c.in_progress, 'in progress', 'accent')}${chip(c.review, 'in review', 'accent')}${chip(c.pending, 'not started', '')}${chip(c.blocked, 'blocked', 'warn')}${chip(c.failed, 'failed', 'err')}${chip(o.plan_left.length, 'plan steps without a task', '')}</div></div>
        <div class="peta sm">${eta}${e && o.stuck ? `<div class="xs" style="color:var(--warn);margin-top:2px">${o.stuck} blocked or failed task${o.stuck === 1 ? '' : 's'} can make it longer</div>` : ''}</div>
        ${o.left_total || o.plan_left.length ? `<button class="s ${S.chat.left ? 'pri' : ''}" ${act('chatLeft')} title="The open tasks, and plan steps nobody started">What is left · ${o.left_total + o.plan_left.length}</button>` : ''}</div>
      ${o.left_total || o.plan_left.length ? `
        ${S.chat.left ? `<div class="lleft">${o.left.map(row).join('')}${o.left_total > o.left.length ? `<div class="xs mut" style="padding:6px">… and ${o.left_total - o.left.length} more</div>` : ''}
          ${o.plan_left.map(s => `<div class="lrow"><span class="dot"></span><span class="sp clip"><span class="mono xs dim">${esc(s.step)}</span> ${esc(s.title)}</span><span class="xs mut">${esc(s.agent || 'nobody yet')}</span>${tag('no task yet', '')}</div>`).join('')}</div>` : ''}` : ''}</div>`; };
async function chatHtml() {
  const name = S.chat.project, c = await api('/company/comms?' + qs({project: name, agent: S.chat.who}));
  if (!c.ok) { if (S.chat.who !== '*') { S.chat.who = '*'; return chatHtml(); } return `<div class="card">${empty('No conversations', c.error.message)}</div>`; }
  S.chat.who = c.selected;
  const group = c.selected === '*', master = c.people.find(p => p.kind === 'master');
  const sel = group ? master : c.people.find(p => p.key === c.selected), isM = sel.kind === 'master', by = k => c.people.find(p => p.key === k);
  if (!group) S.chat.to = sel.key; else if (!c.people.some(p => p.key === S.chat.to)) S.chat.to = 'master';
  const working = c.people.filter(p => p.status === 'WORKING').length;
  const gcard = `<div class="pc ${group ? 'on' : ''}" ${act('chatWho', '*')}><div class="row"><span class="av" style="background:var(--acc)">${ic('msg')}</span><div class="sp clip"><b style="display:block">Team chat</b><span class="xs mut">Everyone in ${esc(name)}, and you</span></div></div>
      <div class="row xs dim" style="margin-top:8px"><span>${c.people.length} people · ${working} working</span><span class="sp"></span>${c.unread_total ? `<span style="color:var(--warn)">${c.unread_total} not read yet</span>` : '<span>all read</span>'}</div></div>`;
  /* the list follows the hierarchy: the Master, its leads, their people */
  const ids = new Set(c.people.map(p => p.id));
  const under = id => c.people.filter(p => p.kind === 'agent' && (p.manager_id === id || (id === master.id && !ids.has(p.manager_id))))
    .sort((a, b) => (kidsOf(b).length > 0) - (kidsOf(a).length > 0) || a.name.localeCompare(b.name));
  const kidsOf = p => c.people.filter(x => x.kind === 'agent' && x.manager_id === p.id);
  const below = p => kidsOf(p).reduce((n, k) => n + 1 + below(k), 0);
  const pcard = p => `<div class="pc ${p.key === c.selected ? 'on' : ''}" ${act('chatWho', p.key)}><div class="row">${av(p.name, p.status)}<div class="sp clip"><b class="clip" style="display:block">${esc(p.name)}${p.kind === 'master' && p.name !== 'Master' ? ' <span class="xs mut" style="font-weight:400">· Master</span>' : ''}</b><span class="xs mut clip" style="display:block">${esc(p.role)}${p.kind === 'agent' && kidsOf(p).length ? ' · leads ' + below(p) : ''}</span></div>${p.unread ? `<a class="unr" title="Messages this person has not read yet: click to see them" ${act('unreadOf', name, p.key)}>${p.unread} unread</a>` : ''}${status(p.status)}${p.kind === 'agent' && kidsOf(p).length ? `<a class="fold" ${act('chatFold', p.id)} title="${S.chat.fold[p.id] ? 'Show the team' : 'Hide the team'}">${S.chat.fold[p.id] ? '▸' : '▾'}</a>` : ''}</div>
      ${p.current_task ? `<div class="row xs" style="margin-top:8px"><span class="clip sp mut">${esc(p.current_task.title)}</span><b>${p.current_task.progress}%</b></div><div style="display:flex;margin-top:4px">${bar(p.current_task.progress)}</div>` : `<div class="xs dim" style="margin-top:8px">${esc(p.kind === 'master' ? p.activity : 'No active task')}</div>`}
      <div class="row xs dim" style="margin-top:6px"><span>${p.last_message ? 'Last: ' + ago(p.last_message) : 'No messages yet'}</span><span class="sp"></span></div></div>`;
  /* consecutive task hand-outs by the same sender become one "delegating" block */
  const rows = [];
  const q = (S.chat.q || '').trim().toLowerCase(), hit = m => !q || [m.from, m.to, m.text, m.task_title].join(' ').toLowerCase().includes(q);
  for (const m of c.messages.filter(hit)) { const last = rows[rows.length - 1]; if (m.kind === 'task' && m.task_id && last && last.del && last.from_key === m.from_key && m.ts - last.ts < 90) last.items.push(m); else rows.push(m.kind === 'task' && m.task_id && /^NEW TASK/.test(m.text) ? {del: true, from: m.from, from_key: m.from_key, ts: m.ts, items: [m]} : m); }
  /* under a message: an arrow that opens what its sender's chat itself wrote around it */
  const ctxLine = r => { const c = S.chat.ctx[r.id];
    return `<div class="ctx"><a class="ctxa" ${act('chatCtx', name, r.from_key, r.ts, r.id)} title="What this chat itself wrote in ChatGPT when it sent this message"><span class="arr">${c && c.open ? '▾' : '▸'}</span> What ${esc(r.from)} wrote in its chat</a>
      ${c && c.open ? `<div class="ctxb">${c.loading ? '<span class="mut">Reading…</span>' : c.wrote ? `${c.asked ? `<div class="xs mut" style="margin-bottom:6px"><b>It had been told:</b> ${esc(c.asked.length > 300 ? c.asked.slice(0, 300) + '…' : c.asked)}</div>` : ''}<div class="prose sm" style="white-space:pre-wrap">${esc(c.wrote)}</div><div class="xs dim" style="margin-top:6px">read from the conversation ${hm(c.read_at)} · <a ${act('chatText', name, r.from_key, r.ts)}>the whole chat text</a></div>`
          : `<span class="mut sm">The hub has not read this part of the chat yet.</span> <a class="xs" ${act('chatText', name, r.from_key, r.ts)}>Open the chat text</a>`}</div>` : ''}</div>`; };
  const whoAv = m => m.from_hub ? `<span class="av" style="background:var(--hover);color:var(--mut)">⚙</span>` : av(m.from, (by(m.from_key) || {}).status);
  const stamp = ts => `<time class="xs dim">${hm(ts)}</time>`;
  const cont = (r, i) => { const p = rows[i - 1]; return !!p && !p.del && !r.del && p.from === r.from && p.to === r.to && !!p.from_hub === !!r.from_hub && r.ts - p.ts < 300 && dayOf(p.ts) === dayOf(r.ts); };
  const job = k => { const p = by(k); return p && p.role ? `<span class="job">${esc(p.role)}</span>` : ''; };
  const daysep = (r, i) => i === 0 || dayOf(rows[i - 1].ts) !== dayOf(r.ts) ? `<div class="daysep"><span>${esc(dayOf(r.ts))}</span></div>` : '';
  const tl = rows.map((r, i) => daysep(r, i) + (r.del ? `<div class="tle">${whoAv(r)}<div class="tlb"><div class="tlh"><b>${esc(r.from)}</b>${job(r.from_key)}${stamp(r.ts)}${tag('Handing out ' + r.items.length + ' task' + (r.items.length === 1 ? '' : 's'), 'accent')}</div>
        <div class="dels">${r.items.map(m => `<div class="del st-${m.task_status || 'pending'} ${S.chat.task === m.task_id ? 'on' : ''}" ${act('chatTask', m.task_id)}>
            <div class="delt">${esc(m.task_title || m.subject)}</div>
            <div class="row" style="margin-top:8px;gap:6px">${av(m.to, (by(m.to_key) || {}).status, 'sm')}<span class="clip sp sm">${esc(m.to)}${job(m.to_key)}</span>${m.task_status ? tag(TASK[m.task_status] || m.task_status, TONE[m.task_status]) : ''}</div>
            <div class="row xs" style="margin-top:8px;gap:8px">${bar(m.task_progress, m.task_status === 'done' ? 'ok' : m.task_status === 'blocked' || m.task_status === 'failed' ? 'warn' : '')}<b>${m.task_progress}%</b><span class="mono dim">${esc(m.task_id)}</span></div>${fileBox(m.files)}</div>`).join('')}</div></div></div>`
    : `<div class="tle ${r.from_owner ? 'me' : r.from_hub ? 'hub' : ''} k-${r.kind} ${cont(r, i) ? 'cont' : ''} ${S.chat.dir[r.id] ? 'd-' + S.chat.dir[r.id] : ''}" data-mid="${esc(r.id)}"><a class="flip" ${act('flipDir', r.id)} title="Turn this message right-to-left or left-to-right">⇄</a>${cont(r, i) ? `<span class="gut">${hm(r.ts)}</span>` : whoAv(r)}<div class="tlb"><div class="tlh"><b>${esc(r.from_hub ? 'Operations' : r.from)}</b>${r.from_hub ? '' : job(r.from_key)}<span class="mut">→ ${esc(r.to)}</span>${job(r.to_key)}${stamp(r.ts)}${r.kind !== 'note' ? tag(r.kind, KIND[r.kind] || '') : ''}${r.task_id ? `<a class="tag" ${act('chatTask', r.task_id)}>${esc(r.task_title ? r.task_title.slice(0, 40) : r.task_id)}</a>` : ''}${r.needs_reply ? tag('needs reply', 'warn') : ''}<span class="sp"></span>${r.to_count ? `<span class="tag ${r.read ? 'ok' : 'warn'}" title="${esc(r.to_names.join(', '))}">read by ${r.read_count} of ${r.to_count}</span>` : r.read ? '' : tag('unread', 'warn')}</div>
        ${r.has_details && !S.chat.open[r.id]
          ? `<div class="bubble ${r.from_owner ? 'own' : r.from_hub ? 'sys' : ''}">${esc(r.plain)}${fileBox(r.files)}<a class="more techbtn" ${act('chatOpen', r.id)}>▸ Technical details</a></div>`
          : `<div class="bubble ${r.from_owner ? 'own' : r.from_hub ? 'sys' : ''}">${r.has_details ? `<div class="techfull">${esc(r.text)}</div>` : esc(r.text)}${fileBox(r.files)}${r.has_details ? `<a class="more techbtn" ${act('chatOpen', r.id)}>▾ Hide technical details</a>` : ''}</div>`}${r.from_key ? ctxLine(r) : ''}</div></div>`)).join('')
    || (q ? empty('Nothing found', 'No message here contains “' + S.chat.q + '”.') : empty(group ? 'Nothing has been said in this project yet' : 'No messages with ' + sel.name + ' yet', 'Write below to start the conversation.'));
  const agents = c.people.filter(p => p.kind === 'agent');
  S.chat.agents = agents.map(p => p.key);
  /* inspector */
  const tid = S.chat.task || c.focus_task;
  const t = tid ? (await api('/company/tasks/' + encodeURIComponent(tid))).task : null;
  const count = st => c.people.filter(p => p.status === st).length;
  const insp = group ? `<div class="card"><h2>${esc(name)}</h2><div class="sm mut" style="margin:4px 0 10px">The whole team in one conversation: what the Master hands out, what agents report and ask, and what you write.</div>
      <div class="list">${c.people.map(p => `<a ${act('chatWho', p.key)}>${av(p.name, p.status, 'sm')}<span class="sp clip sm">${esc(p.name)}<span class="job">${esc(p.role || '')}</span></span>${p.unread ? `<span class="unr" ${act('unreadOf', name, p.key)}>${p.unread} unread</span>` : ''}${status(p.status)}</a>`).join('')}</div></div>`
    : `<div class="card"><div class="row">${av(sel.name, sel.status, 'lg')}<div class="sp clip"><h2 class="clip">${esc(sel.name)}</h2><div class="sm mut clip">${esc(sel.role)} · ${esc(sel.department)}</div><div style="margin-top:4px">${status(sel.status)}</div></div></div>
      <div class="sm" style="margin-top:10px">${esc(sel.activity)}</div>${isM ? '' : `<div class="row xs mut" style="margin-top:10px">${bar(sel.workload.pct, loadTone(sel.workload.band))}<span>${sel.workload.active} open · ${sel.performance.done} done</span></div>`}
      <div class="row wrap" style="margin-top:12px;gap:6px"><button class="s" ${open('person', name, sel.key)}>Profile</button>${isM ? '' : `<button class="s" ${act('assign', name, sel.key)}>Assign task</button>`}${c.chat && c.chat.url ? `<a class="btn s" href="${esc(c.chat.url)}" target="_blank">Open chat</a>` : `<button class="s" ${act('openChat', name, sel.key)}>Open chat</button>`}</div></div>`;
  const focus = `
    <div class="card"><h3>Task in focus</h3>${t ? `<a class="b" ${open('task', t.id)}>${esc(t.title)}</a><div class="xs mono dim">${esc(t.id)}</div><div style="margin:8px 0">${tag(TASK[t.status], TONE[t.status])} <span class="sm mut">${esc(t.agent)}</span></div>
        <div class="flowline">${t.flow.map((f, i) => (i ? '<i></i>' : '') + `<span class="${f.bad ? 'bad' : f.reached ? 'on' : ''}">${f.label}</span>`).join('')}</div>
        <div class="row sm" style="margin-top:10px">${bar(t.progress, t.status === 'done' ? 'ok' : '')}<b>${t.progress}%</b></div>
        ${t.acceptance.length ? `<div class="xs mut" style="margin-top:10px">Done when</div><ul style="margin:2px 0 0;padding-left:16px">${t.acceptance.slice(0, 5).map(a => `<li class="sm">${esc(a)}</li>`).join('')}</ul>` : ''}
        ${t.result_summary ? `<div class="xs mut" style="margin-top:10px">Result</div><div class="sm prose" style="max-height:120px;overflow:auto">${esc(t.result_summary)}</div>` : ''}${t.images.slice(0, 1).map(f => `<a href="/api/v1/files/${f.file_id}" target="_blank"><img src="/api/v1/files/${f.file_id}" style="max-width:100%;border-radius:8px;border:1px solid var(--line);margin-top:8px"></a>`).join('')}`
      : '<div class="mut sm">No task yet. Click a task in the conversation to see it here.</div>'}</div>`;
  const toName = k => { const x = by(k); return x ? (x.kind === 'master' ? (x.name === 'Master' ? 'the Master' : x.name + ' (Master)') : x.name) : k; };
  const branch = p => `<div class="pt">${pcard(p)}${S.chat.fold[p.id] ? '' : `<div class="pk">${under(p.id).map(branch).join('')}</div>`}</div>`;
  return `${outlookStrip(c.outlook, name)}<div class="comm ${S.chat.side ? '' : 'nod'} ${S.chat.nolist ? 'nol' : ''} m-${S.chat.mview || 'list'}" id="commgrid"><div class="plist">${gcard}${branch(master)}</div>
    <div class="card conv"><div class="row convh" style="padding:0 0 10px;border-bottom:1px solid var(--line)"><button class="narrow quiet s back" ${act('chatBack')} aria-label="Back to the chats">‹</button><button class="wide quiet s" ${act('chatPeople')} title="Show or hide the list of people on the left">${S.chat.nolist ? 'People ▸' : '◂ People'}</button><b class="clip">${group ? 'Team chat · ' + esc(name) : isM ? 'Master · what it sent and received' : 'Conversation with ' + esc(sel.name)}</b><span class="sp"></span><span class="chatfind wide"><input id="chatq" placeholder="Search this chat" value="${esc(S.chat.q || '')}" title="Enter searches · Esc clears">${q ? `<a ${act('chatFind', '')} title="Clear the search">✕</a>` : ''}</span><span class="xs mut wide">${q ? rows.length + ' found' : c.messages.length + ' messages'}</span><button class="wide quiet s" ${act('chatSide')} title="Show or hide the panel with the people and the task in focus">${S.chat.side ? 'Close panel ▸' : '◂ Open panel'}</button><button class="narrow quiet s" ${act('chatInfo')}>Details</button></div>
      <div class="tl2" id="tl2">${tl}</div><button class="tolatest" id="tolatest" ${act('chatLatest')} style="display:none">↓ Latest</button>
      ${S.chat.files.length ? `<div class="row wrap" style="padding-top:8px">${S.chat.files.map((f, i) => `<span class="tag">${esc(f.name)} <a ${act('chatUnfile', i)}>✕</a></span>`).join('')}</div>` : ''}
      <div class="compose2"><label class="btn s" title="Attach images or files" style="margin:0;color:var(--fg)">Attach<input type="file" id="chatfile" multiple style="display:none"></label>
        ${group ? `<select id="chatto" style="width:auto;max-width:170px" title="Who receives it">${c.people.map(p => `<option value="${esc(p.key)}" ${S.chat.to === p.key ? 'selected' : ''}>To ${esc(p.kind === 'master' && p.name === 'Master' ? 'the Master' : p.name + (p.kind === 'master' ? ' (Master)' : ''))}</option>`).join('')}</select>` : ''}
        <textarea id="msgtext" placeholder="${S.chat.all ? 'Write to every agent in ' + esc(name) : 'Write to ' + esc(toName(S.chat.to))}…" title="Enter sends, Shift+Enter makes a new line"></textarea>
        ${agents.length > 1 ? `<button class="s ${S.chat.all ? 'pri' : ''}" ${act('chatAll')} title="Send this message to every agent">Everyone</button>` : ''}<button class="pri" ${act('send')}>Send</button></div></div>
    <div class="col"><div class="narrow minfo"><button ${act('chatInfo')}>‹ Back to the chat</button></div>${insp}${focus}</div></div>`;
}
function chatWire() {
  /* the three columns fill the window under the progress block, whatever its height: the conversation gets every pixel that is free */
  chatSize($('commgrid'));       // the height the owner dragged it to, else the window's
  const cq = $('chatq'); if (cq) cq.onkeydown = e => { if (e.key === 'Enter') ACT.chatFind({a0: cq.value}); if (e.key === 'Escape') ACT.chatFind({a0: ''}); };
  const m = $('tl2'); if (m) { const k = S.chat.project + '/' + S.chat.who, p = S.chat.pos; m.scrollTop = p && p.key === k && !p.bottom ? p.top : m.scrollHeight;
    const latest = $('tolatest'), far = () => { if (latest) latest.style.display = m.scrollHeight - m.scrollTop - m.clientHeight > 500 ? '' : 'none'; };
    m.onscroll = () => { S.chat.pos = {key: k, top: m.scrollTop, bottom: m.scrollHeight - m.scrollTop - m.clientHeight < 40}; far(); }; far(); }
  const t = $('msgtext'); if (t) { t.value = S.chat.draft || ''; const grow = () => { if (!t.value) { t.style.height = ''; return; } t.style.height = 'auto'; t.style.height = Math.min(220, Math.max(44, t.scrollHeight + 2)) + 'px'; };
    t.oninput = () => { S.chat.draft = t.value; grow(); }; grow(); t.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !matchMedia('(max-width:760px)').matches) { e.preventDefault(); ACT.send(); } }; }
  const f = $('chatfile'); if (f) f.onchange = async () => { for (const file of f.files) { if (file.size > 20 * 1048576) { toast(file.name + ' is larger than 20 MB', true); continue; }
    const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(file); });
    const r = await post(`/projects/${encodeURIComponent(S.chat.project)}/files`, {name: file.name, data}); if (r.ok) S.chat.files.push(r.file); else toast(r.error.message, true); } render(true); };
}
VIEWS.messages = async project => {
  const o = await api('/company/org');
  const names = (o.projects || []).map(p => p.name);
  S.chat.project = project || S.chat.project || (o.projects.find(p => p.status === 'active') || {}).name || names[0];
  if (!names.includes(S.chat.project)) S.chat.project = names[0];
  const head = `<div class="ph commhead"><div class="sp"><h1 title="Who is doing what, and what the Master and the team say to each other">Communication</h1><div class="seg" style="margin-top:10px"><a class="on" href="#/messages">Direct messages</a><a href="#/rooms">Decision room chats</a></div></div><select id="cp" style="max-width:240px">${names.map(n => `<option ${n === S.chat.project ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></div>`;
  if (!names.length) return head + `<div class="card">${empty('No conversations yet', 'Start a project to give the company something to talk about.')}</div>`;
  return head + await chatHtml();
};
AFTER.messages = () => { chatWire(); if ($('chatto')) $('chatto').onchange = e => { S.chat.to = e.target.value; };
  if ($('cp')) $('cp').onchange = e => { Object.assign(S.chat, {project: e.target.value, who: '*', to: 'master', task: '', files: [], draft: '', open: {}}); render(); }; };

/* knowledge */
const memoryHtml = (m, inProject) => `<div class="grid g2"><div class="card"><h3>What people know</h3>${m.people.length ? `<div class="list">${m.people.map(x => `<div style="align-items:flex-start">${av(x.who, '', 'sm')}<div class="sp"><div class="sm"><a class="b" ${open('person', x.project, x.who_key)}>${esc(x.who)}</a> <span class="mut">${esc(x.label)}${inProject ? '' : ' · ' + esc(x.project)}</span></div><div class="prose sm">${esc(x.text)}</div></div></div>`).join('')}</div>` : '<div class="mut sm">Nobody has written anything into their own memory yet. Agents do it as they learn; you can add tips on a person\'s profile.</div>'}</div>
  <div class="card"><h3>What the project${inProject ? '' : 's'} know${inProject ? 's' : ''}</h3>${m.projects.length ? `<div class="list">${m.projects.map(x => `<div style="align-items:flex-start"><div class="sp"><div class="sm"><b>${esc(x.title || cap(x.kind))}</b> ${tag(cap(x.kind))} ${inProject ? '' : `<span class="mut">${esc(x.project)}</span>`}</div><div class="prose sm mut" style="max-height:120px;overflow:auto">${esc(x.text)}</div></div></div>`).join('')}</div>` : '<div class="mut sm">No project memory yet.</div>'}</div></div>`;
VIEWS.knowledge = async () => {
  const head = `<div class="ph"><div class="sp"><h1>Knowledge</h1><p>What the company knows: its rules and decisions, and what each person and project learned</p></div>
    <div class="seg">${[['company', 'Company'], ['memory', 'People & projects']].map(([k, l]) => `<a ${act('knowTab', k)} class="${S.know.tab === k ? 'on' : ''}">${l}</a>`).join('')}</div><input id="kq" placeholder="Search knowledge" style="max-width:240px" value="${esc(S.know.q)}">${S.know.tab === 'company' ? `<button class="pri" ${act('newKnow')}>${ic('plus')} Add</button>` : ''}</div>`;
  if (S.know.tab === 'memory') return head + memoryHtml(await api('/company/memory?' + qs({q: S.know.q})));
  const k = await api('/company/knowledge?' + qs({q: S.know.q, category: S.know.cat}));
  return head + `<div class="grid" style="grid-template-columns:230px minmax(0,1fr)"><div class="card"><div class="list"><a ${act('knowCat', '')} class="${S.know.cat ? '' : 'b'}">Everything</a>${k.categories.map(c => `<a ${act('knowCat', c.name)} class="${S.know.cat === c.name ? 'b' : ''}"><span class="sp">${esc(c.name)}</span><span class="dim">${c.count || ''}</span></a>`).join('')}</div></div>
    <div class="col">${k.items.length ? k.items.map(x => `<div class="card"><div class="row"><h2 class="sp">${esc(x.title)}</h2>${tag(x.category)}${x.source === 'owner' ? `<button class="quiet s" ${act('delKnow', x.id)}>Delete</button>` : tag({decision: 'from a decision', report: 'project summary'}[x.source] || x.source)}</div><div class="prose sm" style="margin-top:8px">${esc(x.text)}</div><div class="xs dim" style="margin-top:8px">${x.project ? esc(x.project) + ' · ' : ''}${ago(x.updated)}</div></div>`).join('')
      : `<div class="card">${empty('No company knowledge yet', 'Write the rules and standards every team must follow. Every chat reads them first. Your decisions and finished projects are added here automatically.', `<button class="pri" ${act('newKnow')}>Add the first rule</button>`)}</div>`}</div></div>`;
};
AFTER.knowledge = () => { const i = $('kq'); i.onkeydown = e => { if (e.key === 'Enter') { S.know.q = i.value; render(); } }; };

/* deliverables */

/* Metadata-first deliverable cards; only hub-stored files have browser URLs. */
const artifactKind = f => {
  const x=(f.name||'').split('.').pop().toLowerCase();
  if ((f.type||'').startsWith('image/') || ['png','jpg','jpeg','webp','gif','svg'].includes(x)) return 'image';
  if (['js','ts','tsx','jsx','py','cjs','json','yaml','yml','sql','ps1'].includes(x)) return 'code';
  if (['pdf','doc','docx','txt','md','xlsx','csv','pptx'].includes(x)) return 'document';
  return 'other';
};
const artifactCard = f => {
  const kind=artifactKind(f), url='/api/v1/files/'+encodeURIComponent(f.id||'');
  const terms=[f.name,f.by,f.project,f.task].filter(Boolean).join(' ').toLowerCase();
  return `<article class="artifact-card" data-project="${esc(f.project||'')}" data-kind="${kind}" data-search="${esc(terms)}">
    <div class="artifact-preview">${kind==='image'&&f.stored&&f.id ? `<img src="${url}" alt="" loading="lazy">` : ic('file')}</div>
    <div class="artifact-body"><div class="artifact-name">${f.stored&&f.id ? `<a href="${url}" target="_blank" rel="noopener">${esc(f.name)}</a>` : `<b>${esc(f.name)}</b>`}</div>
      <div class="row wrap xs mut">${tag(kind)} ${f.size ? size(f.size) : ''} ${f.accepted ? tag('Accepted','ok') : ''} ${!f.stored ? tag('PC reference') : ''}</div>
      <div class="artifact-meta sm">${esc(f.project)} · ${esc(f.by||'Unknown author')} · ${ago(f.ts)}</div>
      ${f.task_id ? `<div class="xs"><a ${open('task',f.task_id)}>${esc(f.task||'View task')}</a></div>` : ''}
      ${f.path ? `<details class="artifact-path"><summary>File location</summary><code>${esc(f.path)}</code></details>` : ''}
    </div></article>`;
};
const filesHtml = a => !(a.items||[]).length ? `<div class="card">${empty('No deliverables yet','Files and screenshots from the team appear here.')}</div>`
  : `<div class="artifact-grid">${a.items.map(artifactCard).join('')}</div>`;
VIEWS.files = async () => {
  const a=await api('/company/artifacts'),items=a.items||[];
  const projects=[...new Set(items.map(f=>f.project).filter(Boolean))].sort();
  return `<div class="ph"><div class="sp"><h1>Deliverables</h1><p>Find files, previews and associated work</p></div><span class="tag" id="artifactCount">${items.length} files</span></div>
  <div class="artifact-tools"><input id="artifactSearch" aria-label="Search deliverables" placeholder="Search file, person or task">
    <select id="artifactProject" aria-label="Project filter"><option value="">All projects</option>${projects.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('')}</select>
    <select id="artifactKind" aria-label="File type filter"><option value="">All types</option><option value="image">Images</option><option value="code">Code/data</option><option value="document">Documents</option><option value="other">Other</option></select></div>
  ${filesHtml(a)}<div class="card" id="artifactNoResults" hidden>No files match these filters.</div>`;
};
AFTER.files = () => {
  const q=$('artifactSearch'), p=$('artifactProject'), k=$('artifactKind');
  if (!q||!p||!k) return;
  const cards=[...document.querySelectorAll('.artifact-card')];
  const update=()=>{
    const term=q.value.trim().toLowerCase();let count=0;
    for(const el of cards){
      const ok=(!term||el.dataset.search.includes(term))&&(!p.value||el.dataset.project===p.value)&&(!k.value||el.dataset.kind===k.value);
      el.hidden=!ok;if(ok)count++;
    }
    $('artifactCount').textContent=count+' of '+cards.length+' files';
    $('artifactNoResults').hidden=count>0||!cards.length;
  };
  q.addEventListener('input',update);p.addEventListener('change',update);k.addEventListener('change',update);
};

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
  return `<div class="ph"><div class="sp"><h1>Reports</h1><p>Written from the real records, at the moment you open them</p></div><div class="seg">${[['daily', 'Today'], ['weekly', 'This week'], ['project', 'Project'], ['department', 'Department']].map(([k, l]) => `<a ${act('repKind', k)} class="${r.kind === k ? 'on' : ''}">${l}</a>`).join('')}</div>${extra}</div>`
    + (rep.ok ? reportHtml(rep.report) : `<div class="card">${empty('No report', rep.error.message)}</div>`);
};
AFTER.reports = () => { if ($('rarg')) $('rarg').onchange = e => { S.rep[S.rep.kind] = e.target.value; render(); }; };

/* decisions */
const roomCard = r => { const open = r.status === 'open', w = (r.tally || {}).weighted || {}, h = (r.tally || {}).heads || {};
  const said = r.turns.filter(t => t.text || t.position);
  return `<div class="card room"><div class="row" style="align-items:flex-start"><div class="sp"><div class="xs mut">${esc(r.project)} · opened by ${esc(r.opened_by)} · ${ago(r.opened_at)} · ${r.how === 'vote' ? 'vote' : 'discussion'}</div>
      <div class="b" style="font-size:15px;margin-top:4px">${esc(r.question)}</div></div>${open ? tag(r.how === 'vote' ? 'voting' : 'circle ' + r.circle + ' of ' + r.max_circles, 'accent') : r.status === 'to_owner' ? tag('needs you', 'warn') : tag('decided', 'ok')}</div>
    ${r.result ? `<div style="margin:10px 0 4px"><span class="mut sm">Decision:</span> <b style="font-size:15px">${esc(r.result)}</b> <span class="xs mut">${esc(r.result_how)}${r.overruled_by ? ' · overruled by you: ' + esc(r.overrule_reason) : ''}</span></div>
      <div class="xs mut">${Object.keys(h).map(o => `${esc(o)}: ${h[o]} vote${h[o] === 1 ? '' : 's'} (${w[o]} weighted)`).join(' · ')}${(r.tally.no_position || []).length ? ' · no position: ' + r.tally.no_position.map(esc).join(', ') : ''}</div>` : ''}
    <div class="row wrap" style="gap:6px;margin:10px 0">${r.members.map(m => `<span class="tag" title="quality grade ${esc(m.grade)}">${open && r.floor === m.name ? '🎤 ' : ''}${esc(m.name)}: <b>${esc(m.position || '—')}</b></span>`).join('')}</div>
    ${open && r.floor ? `<div class="xs mut" style="margin-bottom:8px">${esc(r.floor)} has the floor.</div>` : ''}
    ${said.length ? `<details ${open ? 'open' : ''}><summary class="sm" style="cursor:pointer">${said.length} contribution${said.length === 1 ? '' : 's'}</summary><div style="margin-top:8px">${said.map(t => `<div class="tle"><span class="av" style="width:28px;height:28px;font-size:11px;${t.owner ? 'background:var(--acc)' : ''}">${esc((t.who || '?').split(' ').map(x => x[0]).join('').slice(0, 2).toUpperCase())}</span><div class="tlb"><div class="tlh"><b>${esc(t.who)}</b>${t.position ? tag(t.position, '') : ''}<span class="xs dim">circle ${t.circle}</span></div><div class="bubble ${t.owner ? 'own' : ''}">${esc(t.text || 'Keeps its position; nothing new.')}</div></div></div>`).join('')}</div></details>` : '<div class="xs mut">Nobody has spoken yet.</div>'}
    <div class="row wrap" style="margin-top:10px;gap:6px"><a class="btn s" href="#/rooms/${encodeURIComponent(r.id)}">Open as chat</a>${open ? `<button class="s" ${act('roomSay', r.id)}>Say something to the room</button><button class="s" ${act('roomClose', r.id)}>Close now and count</button>` : `<button class="s" ${act('roomOverrule', r.id, r.options.join('|'))}>Overrule</button>`}</div></div>`; };
VIEWS.decisions = async () => {
  const [d, rm] = await Promise.all([api('/company/decisions'), api('/rooms')]);
  if (!d.ok) return empty('Could not load decisions', d.error.message);
  S.roomProjects = ((rm.ok && rm.projects) || []).filter(p => p.people.length);
  const roomsHtml = `<div class="ph" style="margin-top:var(--s5)"><div class="sp"><h2 style="margin:0">Decision rooms</h2><p>The Master (or you) puts people at a round table; they discuss in turns and decide together. Votes are weighted by quality grade.</p></div><button ${act('roomOpen')}>Open a decision room</button></div>
    ${rm.ok && rm.rooms.length ? rm.rooms.map(roomCard).join('') : `<div class="card">${empty('No decision rooms yet', 'Open one when a choice affects several people or the team disagrees.')}</div>`}`;
  S.decisions = d.questions.length + d.approvals.length; nav();
  const qcard = x => { const left = Math.round((x.deadline - Date.now() / 1000) / 60);
    return `<div class="card"><div class="row"><span class="who">${av(x.from_person || x.from)}<div><b>${esc(x.from_person || x.from)}</b><small>${esc(x.from)} · ${esc(x.project)}</small></div></span><span class="sp"></span>${x.status === 'escalated' ? tag('decided without you; your answer still wins', 'warn') : tag(left > 0 ? left + ' min left' + (x.recommendation ? ', then the recommendation is applied' : '') : 'deciding now', 'accent')}</div>
      <div style="font-size:15px;margin:12px 0 6px" class="b">${esc(x.question)}</div>${x.recommendation ? `<div class="sm mut">Recommended: ${esc(x.recommendation)}</div>` : ''}
      ${x.options.length ? `<div class="row wrap" style="margin-top:10px">${x.options.map(o => `<button ${act('answer', x.id, o)}>${esc(o)}${x.recommendation && x.recommendation.toLowerCase().startsWith(o.toLowerCase()) ? ' ★' : ''}</button>`).join('')}</div>` : ''}
      <div class="row" style="margin-top:10px"><input id="q_${x.id}" placeholder="Or write your own answer"><button class="pri" ${act('answer', x.id)}>Answer</button></div></div>`; };
  const none = !d.questions.length && !d.approvals.length;
  return `<div class="ph"><div class="sp"><h1>Decisions</h1><p>Only what genuinely needs you. Approval mode for PC commands: <b>${esc(d.approval_mode)}</b> <a class="xs" href="#/ops/settings" style="text-decoration:underline">change</a></p></div></div>
    <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)"><div class="col">${none ? `<div class="card">${empty('Nothing is waiting for you', 'When the Master or a lead needs a decision that is yours, or an agent wants to run a risky command, it appears here.')}</div>` : ''}
      ${d.approvals.length ? `<div class="card"><h3>Commands waiting for your approval</h3>${d.approvals.map(approvalCard).join('')}</div>` : ''}${d.questions.map(qcard).join('')}
      ${(d.approval_rules || []).length ? `<div class="card"><h3>Always allowed</h3><div class="xs mut" style="margin-bottom:6px">Commands of these kinds run without asking you. Each one is still listed under Decided.</div><div class="list">${d.approval_rules.map(x => `<div><div class="sp sm">Commands that <b>${esc(x.reason)}</b>${x.project ? ' · ' + esc(x.project) : ''}</div><button class="s" ${act('ruleOff', x.id)}>Ask me again</button></div>`).join('')}</div></div>` : ''}
      <div class="card"><h3>Decided</h3>${d.history.length || d.approval_history.length ? `<div class="list">${d.history.map(x => `<div style="align-items:flex-start"><div class="sp"><div class="b sm">${esc(x.question)}</div><div class="sm mut prose">${esc(x.answer)}</div></div>${x.answered_by === 'client' ? tag('you', 'ok') : `<span class="row" style="gap:6px">${tag(x.answered_by === 'auto' ? 'automatic: you did not answer' : 'the Master', 'warn')}<button class="s" ${act('overrule', x.id)}>Overrule</button></span>`}</div>`).join('')}
        ${d.approval_history.map(x => `<div style="align-items:flex-start"><div class="sp"><div class="mono clip" style="max-width:520px">${esc(x.command.slice(0, 160))}</div><div class="xs mut">${esc(x.agent || x.plugin)} · ${esc(x.reason)} ${x.result ? '· ' + esc(x.result.slice(0, 80)) : ''}</div></div>${tag(cap(x.status) + (x.by_rule ? ' · by your rule' : ''), {executed: 'ok', approved: 'ok', rejected: 'err'}[x.status] || '')}</div>`).join('')}</div>` : '<div class="mut sm">No decisions have been taken yet. They are also recorded in Knowledge.</div>'}</div></div>
      <div class="col"><div class="card"><h3>Also worth a look</h3>${d.attention.map(needCard).join('') || '<div class="mut sm">No blockers, errors or overloads.</div>'}</div></div></div>` + roomsHtml;
};

/* ---------- drawers ---------- */
async function drawer(kind, a0, a1, quiet) {
  const request = S.drawer = {kind, args: [a0, a1]};
  const el = $('drawer');
  if (!quiet) el.innerHTML = `<div class="scrim" data-close="1"></div><div class="drawer"><div class="c mut">Loading…</div></div>`;
  const html = await DRAWERS[kind](a0, a1);
  if (S.drawer !== request) return;
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
      <div class="sm mut">${esc(p.department)} · reports to ${p.manager_key ? `<a ${open('person', project, p.manager_key)} style="text-decoration:underline">${esc(p.manager_name)}</a>` : esc(p.manager_name)} · <a href="#/projects/${e}" data-close="1" style="text-decoration:underline">${esc(project)}</a></div>
      <div style="margin-top:8px">${status(p.status)} <span class="sm">— ${esc(p.activity)}</span></div></div><button class="quiet" data-close="1">✕</button></div>
    <div class="c"><div class="row wrap" style="margin-bottom:var(--s5)"><button class="pri" ${act('talk', project, key)}>Message</button>${isAgent ? `<button ${act('assign', project, key)}>Assign task</button>` : ''}${p.chat && p.chat.url ? `<a class="btn" href="${esc(p.chat.url)}" target="_blank">Open chat</a>` : ''}
        ${isAgent ? (p.state === 'suspended' || p.state === 'archived' ? `<button ${act('agent', project, key, 'restore')}>Restore</button>` : `<button ${act('agent', project, key, 'suspend')}>Suspend</button>`) : ''}${isAgent && p.state !== 'archived' ? `<button class="danger" ${act('fire', project, key, p.name)} title="Remove this person from ${esc(project === 'Office' ? 'your assistants' : 'the project')} for good">Fire</button>` : ''}${isAgent ? `<button class="danger quiet" ${act('del', 'agent', key, project, p.name)} title="Erase this person and everything that is only theirs (kept in the trash)">Delete</button>` : ''}<a class="btn quiet" href="#/ops/team">Edit profile</a></div>
      ${isAgent ? `<section><h3>Workload and record</h3><div class="row sm" style="margin-bottom:10px">${bar(p.workload.pct, loadTone(p.workload.band))}<b>${p.workload.pct}%</b><span class="mut">${cap(p.workload.band)}</span></div>
        <div class="stats"><div class="stat"><b>${p.workload.active}</b><span>active</span></div><div class="stat"><b>${p.workload.waiting}</b><span>in review</span></div><div class="stat"><b>${p.workload.blocked}</b><span>blocked</span></div><div class="stat"><b>${pf.done}</b><span>completed</span></div>
        <div class="stat"><b>${pf.success_rate == null ? '–' : pf.success_rate + '%'}</b><span>success rate</span></div><div class="stat"><b>${pf.first_pass_rate == null ? '–' : pf.first_pass_rate + '%'}</b><span>accepted first time</span></div><div class="stat"><b>${pf.avg_hours == null ? '–' : pf.avg_hours < 1 ? Math.round(pf.avg_hours * 60) + ' min' : pf.avg_hours + ' h'}</b><span>average per task</span></div></div></section>` : ''}
      <section><h3>About</h3>${p.career ? `<div class="sm">${esc(p.career)}</div>` : ''}${p.responsibilities ? `<div class="prose sm mut" style="margin-top:6px;max-height:160px;overflow:auto">${esc(p.responsibilities)}</div>` : ''}
        ${p.personality ? `<div class="sm" style="margin-top:8px"><span class="mut">How they work:</span> ${esc(p.personality)}</div>` : ''}${(p.skills || []).length ? `<div class="row wrap" style="margin-top:10px;gap:6px">${p.skills.map(s => tag(s)).join('')}</div>` : ''}</section>
      <section><h3>AI</h3><div class="row wrap"><select id="ai_p" style="max-width:240px"><option value="">Default (${esc(ai.default || 'chatgpt')})</option>${providers.map(x => `<option value="${x.key}" ${p.provider === x.key ? 'selected' : ''}>${esc(x.label)}${x.ready ? '' : ' — not set up'}</option>`).join('')}</select>
          <select id="ai_mo" style="max-width:170px" title="ChatGPT: Chat or Work. Claude: Chat or Code. A Work chat is opened outside the ChatGPT project and has its own usage limit.">${[['', 'Mode: default (' + (ai.default_mode || 'chat') + ')'], ['chat', 'Mode: Chat'], ['work', 'Mode: Work (ChatGPT)'], ['code', 'Mode: Code (Claude, trial)']].map(([v, l]) => `<option value="${v}" ${(p.mode || '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <input id="ai_m" list="ai_ml" placeholder="Model (empty = that AI's default)" value="${esc(p.model || '')}" style="max-width:230px"><datalist id="ai_ml">${Object.values(ai.page_models || {}).flat().filter((x, i, a) => a.indexOf(x) === i).map(x => `<option value="${esc(x)}">`).join('')}</datalist>
          <select id="ai_e" style="max-width:170px" title="How hard it thinks before it answers. A site that has no such step uses the nearest one it has.">${[['', 'Effort: default (' + (p.effort_used || 'medium') + ')'], ...(ai.efforts || ['low', 'medium', 'high']).map(e => [e, 'Effort: ' + ({low: 'low (instant)', xhigh: 'extra high'}[e] || e)])].map(([v, l]) => `<option value="${v}" ${(p.effort || '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <button class="s" ${act('aiModels')}>Models</button><button class="s" ${act('aiTest')}>Test</button><button class="pri s" ${act('aiSave', project, key)}>Save</button></div>
        <div class="xs mut" style="margin-top:6px">Runs on <b>${esc(p.ai || 'chatgpt')}</b>${p.mode ? ' · ' + esc(p.mode) : ''}${p.model ? ' · ' + esc(p.model) : ''}${p.effort ? ' · effort ' + esc(p.effort) : ''}. A change applies from this agent's next chat. In a browser chat the hub picks the mode, the model and the effort in the site's own menus (ChatGPT Chat: instant / medium / high; Work: none … persistent; Claude: low … max). A Work chat is opened outside the ChatGPT project. Keys: Settings › ai.
          ${((ai.limits || {}).blocked || []).map(b => `<div style="color:var(--warn);margin-top:4px">${esc(b.name)} is at its usage limit until ${hm(b.until)}: agents set to it run on ${esc((((ai.limits || {}).chains || {})[b.name] || [])[0] || 'nothing else (set Settings › ai › unlimited provider)')} meanwhile.</div>`).join('')}</div></section>
      <section><h3>Skills</h3>${skills.map(s => `<div class="row sm" style="margin-bottom:8px;align-items:flex-start"><div class="sp"><b>${esc(s.name)}</b> <span class="dim xs">${esc(s.source)}</span><div class="mut">${esc((s.description || '').slice(0, 170))}</div></div><a class="xs dim" ${act('skillDrop', project, key, s.id)}>remove</a></div>`).join('') || '<div class="mut sm">No skills yet. A skill is a how-to guide the agent reads at the start of every chat.</div>'}
        <div class="row wrap" style="margin-top:8px;gap:6px">${recommended.map(x => `<button class="s" ${act('skillAdd', project, key, x)} title="${esc(x)}">+ ${esc(x.split(':')[1])}</button>`).join('')}<input id="sk_ref" placeholder="owner/repo:skill, or a skills.sh link" style="max-width:250px"><button class="s" ${act('skillAdd', project, key)}>Add</button></div></section>
      <section><h3>Current work</h3>${openTasks.length ? `<div class="list">${openTasks.map(t => `<a ${open('task', t.id)}><div class="sp clip">${esc(t.title)}</div>${t.status === 'in_progress' ? `<span class="xs mut">${t.progress}%</span>` : ''}${tag(TASK[t.status], TONE[t.status])}</a>`).join('')}</div>` : '<div class="mut sm">No assigned work.</div>'}</section>
      ${p.reports.length ? `<section><h3>${isAgent ? 'Manages' : 'Direct reports'}</h3><div class="list">${p.reports.map(x => `<a ${open('person', project, x.key)}>${who(x)}<span class="sp"></span>${status(x.status)}</a>`).join('')}</div></section>` : ''}
      ${p.kind === 'agent' ? `<section><div class="row"><button class="s" ${act('reuse', project, key)}>Use in another project</button><span class="xs mut">Arrives with name, skills and what they learned (tips, lessons). Nothing about this project goes along.</span></div></section>`
        : `<section><div class="row"><button class="s" ${act('reuse', project, key, 'master')}>Lead another project too</button><span class="xs mut">The same person becomes the Master of the other project as well (name, way of working, AI and what they learned), and stays Master here. In My assistants they work for you as a project manager.</span></div></section>`}
      <section><h3>What ${esc(p.name.split(' ')[0])} knows <span class="sp"></span><a class="xs" ${act('addMemory', project, key)}>add a tip</a></h3>${p.memory.length ? Object.entries(kinds).map(([label, items]) => `<div class="sm b" style="margin:8px 0 4px">${esc(label)}</div>${items.slice(0, 6).map(m => `<div class="row sm" style="align-items:flex-start;margin-bottom:4px"><span class="prose sp">${esc(m.text)}</span><a class="xs dim" ${act('delMemory', m.id)}>remove</a></div>`).join('')}`).join('') : '<div class="mut sm">Nothing yet. People write down facts as they work, and a lesson each time their work is sent back; your tips are read at the start of each of their chats.</div>'}</section>
      <section><h3>Recent messages</h3>${p.messages.length ? p.messages.slice(-6).map(m => `<div class="sm" style="margin-bottom:8px"><b>${esc(m.from)}</b> <span class="mut">to ${esc(m.to)} · ${ago(m.ts)}</span><div class="mut clip">${esc(m.text.slice(0, 200))}</div></div>`).join('') : '<div class="mut sm">No messages yet.</div>'}</section>
      ${p.deliverables.length ? `<section><h3>Deliverables</h3>${p.deliverables.slice(0, 8).map(f => `<div class="sm clip">${f.stored ? `<a href="/api/v1/files/${f.id}" target="_blank" style="text-decoration:underline">${esc(f.name)}</a>` : esc(f.name)} <span class="dim">${esc(f.task)}</span></div>`).join('')}</section>` : ''}
      <section><h3>Timeline</h3>${personFeed(p)}</section></div>`;
};
const personFeed = p => { const items = Array.isArray(p.timeline) ? p.timeline : []; return items.length ? feed(items, {noAgent: 1, noProject: 1}) : '<div class="mut sm">No recorded activity yet.</div>'; };
DRAWERS.task = async id => {
  const r = await api('/company/tasks/' + encodeURIComponent(id));
  if (!r.ok) return `<div class="c">${empty('Task not found', r.error.message)}</div>`;
  const t = r.task;
  return `<div class="h"><div class="sp"><div class="xs mut">${esc(t.id)} · <a href="#/projects/${encodeURIComponent(t.project)}" data-close="1">${esc(t.project)}</a>${t.plan_step ? ' · ' + esc(t.plan_step) : ''}</div><h1>${esc(t.title)}</h1>
      <div style="margin-top:6px">${tag(TASK[t.status], TONE[t.status])} ${t.priority <= 2 ? tag('Priority ' + t.priority, 'warn') : ''} ${t.visual ? tag('visual') : ''} ${t.stalled ? tag('stalled', 'warn') : ''}</div></div><button class="quiet" data-close="1">✕</button></div>
    <div class="c"><section><div class="flowline">${t.flow.map((f, i) => (i ? '<i></i>' : '') + `<span class="${f.bad ? 'bad' : f.reached ? 'on' : ''}">${f.label}</span>`).join('')}</div>
        ${t.status === 'in_progress' || t.status === 'review' ? `<div class="row sm" style="margin-top:12px">${bar(t.progress)}<b>${t.progress}%</b></div>` : ''}</section>
      <section><div class="grid g2 sm"><div><span class="mut">Assigned to</span><br>${t.agent_key ? `<a class="b" ${open('person', t.project, t.agent_key)}>${esc(t.agent)}</a> <span class="mut">${esc(t.agent_role)}</span>` : esc(t.agent)}</div><div><span class="mut">Department</span><br>${esc(t.department || '–')}</div>
        <div><span class="mut">Given by</span><br>${esc(t.by)}</div><div><span class="mut">Manager</span><br>${esc(t.manager)}</div><div><span class="mut">Started</span><br>${t.started ? ago(t.started) : 'not yet'}</div><div><span class="mut">Finished</span><br>${t.finished ? ago(t.finished) : 'not yet'}</div></div></section>
      ${t.status === 'blocked' || t.status === 'failed' ? `<section class="need ${t.status === 'failed' ? 'err' : 'warn'}" style="display:block"><h3>This task stands still</h3>
        <div class="sm">${esc(t.agent)} ${t.status === 'failed' ? 'could not do it' : 'cannot go on'}. The reason is under "Result" below. It is normally the Master's (or the lead's) job to solve it; you can also do it here.</div>
        <textarea id="tk_note" rows="3" style="width:100%;margin-top:8px" placeholder="Your answer or instruction (optional)"></textarea>
        <div class="row wrap" style="margin-top:8px"><button class="pri s" ${act('taskAct', t.id, 'unblock')}>Answer and continue</button><button class="s" ${act('taskAct', t.id, 'escalate')}>Tell the Master to solve it now</button><button class="s" ${act('taskAct', t.id, 'cancel')}>Cancel the task</button></div></section>` : ''}
      <section><div class="row"><button class="s danger quiet" ${act('del', 'task', t.id, '', t.id + ' ' + t.title.slice(0, 40))} title="Remove this task and its messages (kept in the trash)">Delete this task</button></div></section>
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
      <section><h3>Projects</h3><div class="row wrap">${d.projects.map(p => `<a class="tag" href="#/projects/${encodeURIComponent(p)}" data-close="1">${esc(p)}</a>`).join('') || '<span class="mut sm">None</span>'}</div></section>
      <section><h3>Work</h3>${d.task_list.length ? `<div class="list">${d.task_list.slice(0, 15).map(t => `<a ${open('task', t.id)}><div class="sp clip">${esc(t.title)}<div class="xs mut">${esc(t.agent)}</div></div>${tag(TASK[t.status], TONE[t.status])}</a>`).join('')}</div>` : '<div class="mut sm">No tasks yet.</div>'}</section>
      <section><h3>Recent activity</h3>${d.activity.length ? feed(d.activity) : '<div class="mut sm">Nothing yet.</div>'}</section></div>`;
};

/* ---------- modals ---------- */
const modal = html => { $('modal').innerHTML = html ? `<div class="scrim" data-mclose="1" style="z-index:30"></div><div class="modal">${html}</div>` : ''; const f = $('modal').querySelector('input,textarea,select'); if (f) f.focus(); };
const val = id => ($(id) ? $(id).value.trim() : '');
const ACT = {
  goto: d => go(d.a0), theme: () => { const r = document.documentElement, n = r.dataset.theme === 'light' ? 'dark' : 'light'; r.dataset.theme = n; try { const fr = $('actframe'); if (fr) fr.contentDocument.documentElement.dataset.theme = n; } catch (e) {} try { localStorage.setItem('hubtheme', n); } catch (e) {} },
  key: () => { const k = prompt('API key (only needed when the hub is reached through a tunnel).', KEY); if (k === null || k === KEY) return toast('API key unchanged.'); KEY = k; try { localStorage.setItem('hubkey', k); } catch (e) {} toast('API key saved for this browser.'); render(); },
  zoom: d => { S.org.zoom = d.a0 === '0' ? 1 : Math.max(.4, Math.min(1.6, S.org.zoom + (d.a0 === '+' ? .1 : -.1))); render(true); },
  fold: d => { S.org.closed[d.a0] = !S.org.closed[d.a0]; $('org').innerHTML = orgTree(S.orgData); },
  feedMode: d => { S.feed.mode = d.a0; render(); },
  feedCat: d => { S.feed.category = d.a0; render(); }, knowTab: d => { S.know.tab = d.a0; render(); }, knowCat: d => { S.know.cat = d.a0; render(); },
  repKind: d => { S.rep.kind = d.a0; render(); }, 
  copyReport: () => { navigator.clipboard.writeText($('reptext').value); toast('Report copied'); },
  async approve(d) { const r = await post(`/approvals/${d.a0}/${d.a1 ? 'approve' : 'reject'}`); if (done(r, d.a1 ? 'Approved. It runs now, or as soon as the chat repeats the call.' : 'Rejected. It will not run.')) { await post('/supervisor/tick'); render(true); refreshBadge(); } },
  async allowSimilar(d) { const r = await post(`/approvals/${d.a0}/allow_similar`); if (r.ok === false) return toast(r.error.message, true); toast(`Approved ${r.approved} command(s). Commands that "${r.rule}" no longer ask you in this project.`); await post('/supervisor/tick'); render(true); },
  async ruleOff(d) { const r = await post(`/approvals/${d.a0}/remove_rule`); if (r.ok === false) return toast(r.error.message, true); toast('You will be asked again for these commands.'); render(true); },
  async taskAct(d) { const note = ($('tk_note') || {}).value || ''; if (d.a1 === 'cancel' && !confirm('Cancel this task?')) return;
    const r = await post(`/company/tasks/${d.a0}/${d.a1}`, {note}); if (r.ok === false) return toast(r.error.message, true);
    toast({unblock: 'Sent. The task is back in progress.', escalate: 'The Master (or the lead) was told to solve it now.', cancel: 'Task cancelled.'}[d.a1]); await post('/supervisor/tick'); drawer('task', d.a0, '', true); render(true); },
  officeHire: () => modal(`<h2>Hire an assistant</h2><p class="mut sm">Works for you directly, outside any project. A chat is opened for them when you give the first work.</p>
      <label>Job</label><input id="oh_job" placeholder="Research assistant"><label>Name (optional)</label><input id="oh_name" placeholder="Sara Adel">
      <label>What they do for you</label><textarea id="oh_resp" style="min-height:90px" placeholder="Finds information on the web, compares options, writes short reports with sources."></textarea>
      <div class="row" style="margin-top:14px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('officeHireDo')}>Hire</button></div>`),
  async officeBring() { const o = await api('/company/org'); const mine = S.office || {project: 'Office', people: []};
    const have = new Set(mine.people.map(p => p.name));
    const list = (o.people || []).filter(p => p.kind === 'agent' && p.project !== mine.project && !have.has(p.name));
    if (!list.length) return toast('Nobody in your projects can be brought over (or everyone already works for you).', true);
    modal(`<h2>Bring someone from a project</h2><p class="mut sm">They become your assistant as themselves, with their skills and what they learned (tips, lessons). Nothing about the project comes along: not its memory, tasks or messages.</p>
      <label>Who</label><select id="ob_who">${list.map(p => `<option value="${esc(p.project)}|${esc(p.key)}">${esc(p.name)} — ${esc(p.role)} (${esc(p.project)})</option>`).join('')}</select>
      <label style="display:flex;gap:8px;align-items:center;margin-top:12px"><input type="checkbox" id="ob_keep" checked style="width:auto"> Keep working in the project as well</label>
      <div class="row" style="margin-top:14px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('officeBringDo')}>Make my assistant</button></div>`); },
  async officeBringDo() { const [proj, key] = val('ob_who').split('|');
    const r = await post(`/company/people/${encodeURIComponent(proj)}/${encodeURIComponent(key)}/reuse`, {to: (S.office || {}).project || 'Office', keep: $('ob_keep').checked});
    if (r.ok === false) return toast(r.error.message + (r.error.fix ? ' ' + r.error.fix : ''), true);
    modal(''); toast(`${r.name} is now your assistant: ${r.memories} thing(s) learned and ${r.skills} skill(s) came along.`); render(); },
  async officeHireDo() { if (!val('oh_job')) return toast('Write the job first', true);
    const r = await post('/office/hire', {job_title: val('oh_job'), person_name: val('oh_name'), responsibilities: val('oh_resp')}); if (done(r, 'Hired. Give them work to start.')) { modal(''); render(); } },
  officeTask: d => { const o = S.office || {people: []}; modal(`<h2>Give work</h2><label>To</label><select id="ot_to">${o.people.map(p => `<option value="${esc(p.key)}" ${d && d.a0 === p.key ? 'selected' : ''}>${esc(p.name)} — ${esc(p.role)}</option>`).join('')}</select>
      <label>What is the job (short)</label><input id="ot_title" placeholder="Compare three laptop models under 40,000 EGP">
      <label>Details</label><textarea id="ot_text" style="min-height:120px" placeholder="What you want, what matters, in what form you want the result."></textarea>
      <div class="row" style="margin-top:14px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('officeTaskDo')}>Give the work</button></div>`); },
  async officeTaskDo() { if (!val('ot_title')) return toast('Write what the job is', true);
    const r = await post('/office/tasks', {agent: val('ot_to'), title: val('ot_title'), instructions: val('ot_text') || val('ot_title')}); if (done(r, 'Given. Their report will appear here.')) { modal(''); await post('/supervisor/tick'); render(); } },
  async officeReview(d) { const fb = val('of_fb_' + d.a0); if (d.a1 === 'request_changes' && !fb) return toast('Write what should change', true); if (d.a1 === 'cancel' && !confirm('Cancel this task?')) return;
    const r = await post(`/office/tasks/${d.a0}/review`, {decision: d.a1, feedback: fb}); if (done(r, {accept: 'Accepted.', request_changes: 'Sent back with your notes.', cancel: 'Cancelled.'}[d.a1])) { await post('/supervisor/tick'); render(true); } },
  async officeUnblock(d) { const r = await post(`/company/tasks/${d.a0}/unblock`, {note: val('of_fb_' + d.a0)}); if (done(r, 'Sent. The task is back in progress.')) { await post('/supervisor/tick'); render(true); } },
  async officeReply(d) { const text = d.a1 ? val('of_re_' + d.a0) : ''; if (d.a1 && !text) return toast('Write your answer', true);
    const r = await post('/office/reply', {message_id: d.a0, to: d.a1, text}); if (done(r, text ? 'Answer sent.' : 'Marked as read.')) { await post('/supervisor/tick'); render(true); } },
  async reuse(d) { const o = await api('/company/overview'); const others = (o.projects || []).map(p => p.name).filter(n => n !== d.a0);
    if (!others.length) return toast('There is no other project yet. Create one first.', true);
    const lead = d.a2 === 'master';
    modal(`<h2>${lead ? 'Lead another project too' : 'Use in another project'}</h2><p class="mut sm">${lead ? 'The same person becomes the Master of the project you choose, with their name, way of working, AI and what they learned, and stays the Master of ' + esc(d.a0) + '. Whoever led that project until now is replaced. (In My assistants they work for you as a project manager instead.)' : 'The person starts there as themselves, with their skills and what they learned (tips, lessons, reusable knowledge).'} The memory, tasks and messages of ${esc(d.a0)} are not taken along.</p>
      <label>Project</label><select id="ru_to">${others.map(n => `<option>${esc(n)}</option>`).join('')}</select>
      <label style="display:${lead ? 'none' : 'flex'};gap:8px;align-items:center;margin-top:12px"><input type="checkbox" id="ru_keep" checked style="width:auto"> Keep working in ${esc(d.a0)} as well</label>
      <div class="row" style="margin-top:14px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('reuseDo', d.a0, d.a1)}>Add to that project</button></div>`); },
  async reuseDo(d) { const r = await post(`/company/people/${encodeURIComponent(d.a0)}/${encodeURIComponent(d.a1)}/reuse`, {to: val('ru_to'), keep: $('ru_keep').checked});
    if (r.ok === false) return toast(r.error.message + (r.error.fix ? ' ' + r.error.fix : ''), true);
    modal(''); toast(`${r.name} now works in ${r.project}: ${r.memories} thing(s) learned and ${r.skills} skill(s) came along.`); render(true); },
  async needAct(d) { const r = await post(d.a0, {}); if (r.ok === false) return toast(r.error.message, true); toast('Done: the tries were reset and it was started again.'); render(true); },
  async unreadOf(d) {
    const r = await api('/company/unread?' + qs({project: d.a0, agent: d.a1}));
    if (!r.ok) return toast(r.error.message, true);
    const list = r.messages.map(m => `<div style="margin:0 0 10px;padding:10px 12px;border-radius:10px;border:1px solid var(--line)"><div class="row xs mut" style="margin-bottom:4px"><b>${esc(m.from_hub ? 'Operations' : m.from)}</b>${tag(m.kind, KIND[m.kind] || '')}${m.needs_reply ? tag('needs reply', 'warn') : ''}${m.task_id ? `<span class="mono">${esc(m.task_id)}</span>` : ''}<span class="sp"></span>${ago(m.ts)}</div>
        <div class="prose sm" style="white-space:pre-wrap;max-height:160px;overflow:auto">${esc(m.text)}</div>
        <div class="xs dim" style="margin-top:4px">${m.handed_over ? 'Handed to the chat ' + m.handed_over + ' time(s), not confirmed yet' : m.wakes ? 'Waiting: the chat is told about it' : 'A progress note: the chat is told when it has waited 5 minutes'}</div></div>`).join('');
    modal(`<h2>Unread: ${esc(r.agent)}</h2>${r.why ? `<p class="sm" style="color:var(--warn)">${esc(r.why)}</p>` : ''}<p class="mut sm">${r.messages.length} message(s) this person has not read yet, oldest first.${r.has_chat ? ` Last prompted ${r.last_prompt ? ago(r.last_prompt) : 'never'}, last tool call ${r.last_tool ? ago(r.last_tool) : 'never'}.` : ' This person has no open chat right now; one is opened when there is work or mail.'}</p>
      <div style="max-height:56vh;overflow:auto">${list || '<div class="mut sm">Nothing is unread.</div>'}</div>
      <div class="row" style="margin-top:12px">${r.has_chat && r.messages.length ? `<button class="s pri" ${act('unreadNudge', r.session_id)}>Tell the chat now</button>` : ''}<span class="sp"></span><button class="s" data-mclose="1">Close</button></div>`);
  },
  async unreadNudge(d) { const r = await post(`/sessions/${d.a0}/retry`, {}); if (done(r, 'The chat was told to read its messages.')) modal(''); },
  async chatCtx(d) { const c = S.chat.ctx[d.a3];
    if (c && c.open) { c.open = false; return render(true); }
    S.chat.ctx[d.a3] = {open: true, loading: true}; render(true);
    const r = await api('/company/chat-text?' + qs({project: d.a0, agent: d.a1, at: d.a2}));
    S.chat.ctx[d.a3] = {open: true, ...((r.ok && r.related) || {})}; render(true); },
  async chatText(d) {
    const load = async () => { const r = await api('/company/chat-text?' + qs({project: d.a0, agent: d.a1, at: d.a2}));
      if (!r.ok) return modal(`<h2>Conversation text</h2><p class="mut">${esc(r.error.message)}</p>`);
      const turns = r.turns.map(t => `<div id="ct_${t.id}" style="margin:0 0 12px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);${t.id === r.near ? 'outline:2px solid var(--accent);' : ''}${t.who === 'user' ? 'background:var(--hover)' : ''}">
          <div class="xs mut" style="margin-bottom:4px"><b>${t.who === 'user' ? 'Typed into the chat' : esc(r.agent) + ' wrote in the chat'}</b> · read ${hm(t.captured_at)}${t.id === r.near ? ' · first reply read after this message' : ''}</div><div class="prose sm" style="white-space:pre-wrap">${esc(t.text)}</div></div>`).join('');
      modal(`<h2>Conversation text of ${esc(r.agent)}</h2><p class="mut sm">What the agent was told and what it wrote in its actual conversation${r.last_read ? '; last read ' + ago(r.last_read) : ''}.</p>
        <div class="row" style="margin-bottom:10px">${r.can_read ? `<button class="s pri" id="ct_read">Read the chat now</button>` : '<span class="xs mut">This chat cannot be read right now.</span>'}<span class="sp"></span><button class="s" data-mclose="1">Close</button></div>
        <div style="max-height:60vh;overflow:auto">${turns || '<div class="mut sm">Nothing has been read from this chat yet.</div>'}</div>`);
      const el = r.near && $('ct_' + r.near); if (el) el.scrollIntoView({block: 'center'});
      const b = $('ct_read'); if (b) b.onclick = async () => { b.disabled = true; b.textContent = 'Reading the conversation…';
        const q0 = await post('/company/chat-text?' + qs({project: d.a0}), {agent: d.a1}); if (q0.ok === false) { toast(q0.error.message, true); return load(); }
        if (q0.completed) return load();
        const before = r.last_read || 0; for (let i = 0; i < 40; i++) { await new Promise(x => setTimeout(x, 3000)); const n = await api('/company/chat-text?' + qs({project: d.a0, agent: d.a1})); if (n.ok && (n.last_read || 0) > before) break; }
        load(); };
    };
    load();
  },
  async overrule(d) { const a = prompt('Your decision (it replaces what was decided without you):'); if (!a) return; const r = await post(`/questions/${d.a0}/answer`, {answer: a}); if (r.ok === false) return toast(r.error.message, true); toast('Sent to the team: your decision replaces the earlier one.'); render(true); },
  async answer(d) { const a = d.a1 || val('q_' + d.a0); if (!a) return toast('Write your answer first', true); const r = await post(`/questions/${d.a0}/answer`, {answer: a}); if (done(r, 'Sent. The team continues with your answer; it is recorded in Knowledge.')) { await post('/supervisor/tick'); render(true); } },
  pfolder: d => modal(`<h2>Project folder</h2><p class="mut sm">Where the files of ${esc(d.a0)} live on this PC. Every chat of the project is told, and reads it first in every new chat.</p><label>Folder</label><input id="pf_path" value="${esc(d.a1 || '')}" placeholder="H:\Projects\my-project">
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('pfolderSave', d.a0)}>Save</button></div>`),
  async pfolderSave(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/folder`, {folder: val('pf_path')}), 'Saved. The team was told.')) { modal(''); S.orgData = null; render(true); } },
  async dvCap(d) { const r = await post('/delivery', {max_tabs: +d.a0}); if (r.ok === false) return toast(r.error.message, true); toast('Delivery tabs: ' + r.capacity + '. Agents are not affected.'); render(true); },
  async pstatus(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/status`, {status: d.a1}), d.a1 === 'active' ? 'The project is running again' : 'The project is paused')) render(true); },
  fire: d => { const office = d.a0 === 'Office';
    modal(`<h2>Fire ${esc(d.a2)}?</h2><p class="mut sm">${office ? 'They stop working for you: their chat is closed and their open work is cancelled.' : 'They leave ' + esc(d.a0) + ': their chat is closed and they get no more work. The Master is told at once, with the list of their open tasks to hand to someone else.'} Their history and what they produced stay; you can bring them back later with Restore.</p>
      <label>Reason ${office ? '(for your own record, may be empty)' : '(the Master reads it; may be empty)'}</label><textarea id="fire_why" style="min-height:70px" placeholder="${office ? '' : 'e.g. repeated unverified reports'}"></textarea>
      <div class="row" style="margin-top:16px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri danger" ${act('fireDo', d.a0, d.a1, d.a2)}>Fire</button></div>`); },
  async fireDo(d) { const r = await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/archive`, {reason: val('fire_why')});
    if (done(r, d.a2 + (d.a0 === 'Office' ? ' no longer works for you' : ' was removed; the Master has been told'))) { modal(''); await post('/supervisor/tick'); render(true); drawer('person', d.a0, d.a1, true); } },
  async agent(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/${d.a2}`, {}), cap(d.a2) + ' done')) { render(true); drawer('person', d.a0, d.a1, true); } },
  async send() { if (S.sending) return; let text = val('msgtext'); const files = S.chat.files.map(f => f.file_id); if (!text && files.length) text = 'See the attached file' + (files.length > 1 ? 's' : '') + '.'; if (!text) return;
    const project = S.chat.project, who = S.chat.who, all = S.chat.all, draft = S.chat.draft;
    const to = all ? [...S.chat.agents] : [S.chat.to || 'master']; let bad = ''; S.sending = true;
    const sendKey = JSON.stringify([project, to, text, files]);
    if (!S.sentRecipients || S.sentRecipients.key !== sendKey) S.sentRecipients = {key: sendKey, to: []};
    try {
      for (const t of to) { if (S.sentRecipients.to.includes(t)) continue; const r = await post(`/projects/${encodeURIComponent(project)}/messages`, {to: t, text, files}); if (!r.ok) bad = r.error.message + ' ' + (r.error.fix || ''); else S.sentRecipients.to.push(t); }
      toast(bad || (all ? 'Sent to ' + to.length + ' agents. Their chats are woken up.' : 'Sent. The chat is woken up.'), !!bad);
      if (!bad) { S.sentRecipients = null; if (S.chat.project === project && S.chat.who === who && S.chat.draft === draft) { Object.assign(S.chat, {files: [], draft: '', all: false, pos: null}); if (all) S.chat.who = '*'; } await post('/supervisor/tick'); render(true); }
    } finally { S.sending = false; } },
  chatWho: d => { Object.assign(S.chat, {who: d.a0, task: '', pos: null, all: false, mview: 'chat', q: ''}); render(true); },
  chatFind: d => { S.chat.q = (d.a0 || '').trim(); S.chat.pos = null; render(true); },
  chatBack: () => { S.chat.mview = 'list'; render(true); }, chatInfo: () => { S.chat.mview = S.chat.mview === 'info' ? 'chat' : 'info'; render(true); },
  chatLatest: () => { const m = $('tl2'); if (m) m.scrollTo({top: m.scrollHeight, behavior: 'smooth'}); }, chatOpen: d => { S.chat.open[d.a0] = !S.chat.open[d.a0]; render(true); }, chatFold: d => { S.chat.fold[d.a0] = !S.chat.fold[d.a0]; render(true); },
  chatTask: d => { S.chat.task = d.a0; if (!S.chat.side && !matchMedia('(max-width:760px)').matches) return drawer('task', d.a0);     /* details are put away: the task opens in its own panel */
    render(true); },
  chatLeft: () => { S.chat.left = !S.chat.left; render(true); },
  /* a message that mixes Arabic and English can come out mirrored (the side is taken from its first word): turn it over by hand */
  flipDir: d => { const row = document.querySelector('.tle[data-mid="' + d.a0 + '"]'); if (!row) return;
    const text = ((row.querySelector('.bubble') || {}).innerText || '').trim(), strong = text.match(/[A-Za-z\u0590-\u08FF]/);
    const now = S.chat.dir[d.a0] || (strong && /[\u0590-\u08FF]/.test(strong[0]) ? 'rtl' : 'ltr'), next = now === 'rtl' ? 'ltr' : 'rtl';
    S.chat.dir[d.a0] = next; row.classList.remove('d-rtl', 'd-ltr'); row.classList.add('d-' + next); },
  chatSide: () => { S.chat.side = !S.chat.side; S.chat.pos = null; try { localStorage.setItem('emara.chatSide', S.chat.side ? '1' : '0'); } catch (e) {} render(true); },
  chatAll: () => { S.chat.all = !S.chat.all; render(true); }, chatUnfile: d => { S.chat.files.splice(+d.a0, 1); render(true); },
  async openChat(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/roles/${encodeURIComponent(d.a1)}/open-chat`, {}), 'The chat is being opened')) { await post('/supervisor/tick'); render(true); } },
  async aiSave(d) { const r = await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}`, {ai: val('ai_p'), model: val('ai_m'), effort: val('ai_e'), mode: val('ai_mo')}); if (r.ok === false) return toast(r.error.message + ' ' + (r.error.fix || ''), true); toast('Saved. Its next chat runs on the chosen AI and mode.'); drawer('person', d.a0, d.a1, true); },
  async aiModels() { const p = val('ai_p'); if (!p || p === 'chatgpt') return toast('Choose Claude, Gemini or Custom first', true); const r = await api('/ai/models?provider=' + p); if (!r.ok) return toast(r.error.message + ' ' + (r.error.fix || ''), true);
    $('ai_ml').innerHTML = r.models.map(m => `<option>${esc(m)}</option>`).join(''); toast(r.models.length + ' models found: click into the model field to pick one'); },
  async aiTest() { const p = val('ai_p'); if (!p || p === 'chatgpt') return toast('ChatGPT runs in your browser: there is nothing to test here', true); toast('Asking ' + p + '…'); const r = await post('/ai/test', {provider: p, model: val('ai_m')});
    toast(r.ok ? `${r.provider} / ${r.model} answered: ${r.answer}` : r.error.message + ' ' + (r.error.fix || ''), !r.ok); },
  async skillAdd(d) { const ref = d.a2 || val('sk_ref'); if (!ref) return toast('Which skill? Type owner/repo:skill', true); toast('Getting the skill…'); const r = await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/skills`, {skills: [ref]});
    if (done(r, 'Skill added. The agent reads it at the start of its next chat.')) drawer('person', d.a0, d.a1, true); },
  async skillDrop(d) { if (done(await post(`/projects/${encodeURIComponent(d.a0)}/agents/${encodeURIComponent(d.a1)}/skills`, {skills: [d.a2], remove: true}), 'Removed')) drawer('person', d.a0, d.a1, true); },
  async delMemory(d) { if (done(await del('/agent-memory/' + d.a0), 'Removed') && S.drawer) drawer(S.drawer.kind, ...S.drawer.args, true); },
  async delKnow(d) { if (confirm('Delete this knowledge entry?') && done(await del('/company/knowledge/' + d.a0), 'Deleted')) render(true); },
  async briefing() { const b = await api('/company/briefing'); modal(`<h2>Briefing</h2><p style="margin:12px 0;font-size:15px;line-height:1.6">${esc(b.text)}</p>${b.priorities.length ? `<h3 style="margin:16px 0 6px">Running</h3>${b.priorities.map(p => `<div class="sm">${esc(p)}</div>`).join('')}` : ''}
      ${b.problems.length ? `<h3 style="margin:16px 0 6px">Problems</h3>${b.problems.map(p => `<div class="sm">${esc(p.title)}</div>`).join('')}` : ''}<div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Close</button><button class="pri" ${act('goto', '#/decisions')} data-mclose="1">Open decisions</button></div>`); },
  async remoteSet(d) { toast(d.a0 === 'on' ? 'Switching remote access on …' : 'Switching it off …'); const r = await post('/remote', {enabled: d.a0 === 'on'});
    if (done(r, d.a0 === 'on' ? 'Remote access is on: ' + (r.url || '') : 'Remote access is off')) render(true); },
  appAddress: () => { try { window.EmaraApp.changeAddress(); } catch (e) { toast('Only inside the Android app', true); } },
  async chatApi(d) { const r = await post('/chat-api', d.a0 === 'key' ? {new_key: true} : {enabled: d.a0 === 'on'}); if (!r.ok) return done(r, '');
    toast(r.note || (d.a0 === 'key' ? 'A new key was made; the old one no longer works' : d.a0 === 'on' ? 'The API is on' : 'The API is off'), !!r.note); render(true); },
  copyText: d => { navigator.clipboard.writeText(d.a0 || ''); toast('Copied'); },
  showKey: () => { const k = $('capi_key'); if (k) k.type = k.type === 'password' ? 'text' : 'password'; },
  async qualityOf(d) { const r = await api('/quality/person?' + qs({project: d.a0, agent: d.a1})); if (!r.ok) return done(r, ''); const s = r.score;
    modal(`<h2>${esc(r.name)}: grade ${s.grade}</h2><p class="sm">${s.last_30_days} points in 30 days · ${s.total} in total · accepted first time ${s.first_pass}, after rework ${s.after_rework} · sent back ${s.sent_back} · defects ${s.defects} · passed a defect ${s.passed_defects} · caught ${s.caught}</p>
      <div style="max-height:340px;overflow:auto">${r.ledger.map(l => `<div class="row sm" style="margin-bottom:6px;align-items:flex-start"><b style="min-width:38px;color:${l.points > 0 ? 'var(--ok)' : 'var(--err)'}">${l.points > 0 ? '+' : ''}${l.points}</b><span class="sp">${esc(l.why)}${l.task_id ? ' <span class="mono xs">' + esc(l.task_id) + '</span>' : ''}${l.note ? '<div class="xs mut">' + esc(l.note) + '</div>' : ''}</span><span class="xs mut">${ago(l.ts)}</span></div>`).join('') || '<div class="mut sm">No points yet.</div>'}</div>
      <div class="row" style="margin-top:16px"><span class="sp"></span><button data-mclose="1">Close</button></div>`); },
  givePoints: () => modal(`<h2>Give or take points</h2><label>Project</label><input id="gp_project" placeholder="MarcoStore"><label>Person (name as on the Team page)</label><input id="gp_agent" placeholder="QA Engineer A">
    <label>Points (negative to take)</label><input id="gp_points" type="number" value="5"><label>Reason</label><textarea id="gp_reason" placeholder="Found the dead sidebar links before release."></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('savePoints')}>Save</button></div>`),
  async savePoints() { if (done(await post('/quality/points', {project: val('gp_project'), agent: val('gp_agent'), points: Number(val('gp_points')), reason: val('gp_reason')}), 'Points recorded')) { modal(''); render(true); } },
  fileDefect: () => modal(`<h2>Report a defect in accepted work</h2><p class="mut sm">The task is reopened for its author. The author, whoever accepted it and whoever checked it lose points, and each is asked what they will do differently.</p>
    <label>Task id (the accepted task that delivered it)</label><input id="fd_task" placeholder="T-4KQ2M"><label>What is wrong</label><textarea id="fd_text" style="min-height:110px" placeholder="Clicking Repairs in the sidebar does nothing: the address and the page stay the same. It should open the repairs list."></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('saveDefect')}>Report</button></div>`),
  async saveDefect() { const r = await post('/quality/defect', {task_id: val('fd_task'), description: val('fd_text')}); if (!r.ok) return done(r, '');
    modal(`<h2>${esc(r.task)} was reopened</h2>${(r.charged || []).map(c => `<p class="sm">${esc(c.who)}: <b style="color:var(--err)">${c.points}</b> — ${esc(c.why)}</p>`).join('') || '<p class="sm mut">Nobody could be charged.</p>'}
      <div class="row" style="margin-top:16px"><span class="sp"></span><button class="pri" data-mclose="1">Close</button></div>`); render(true); },
  async roomOpen(d) { const rm = await api('/rooms'); const ps = S.roomProjects = ((rm.ok && rm.projects) || []).filter(p => p.people.length);
    if (!ps.length) return toast('There is no running project with people to open a room in', true);
    const want = d && d.a0 === '@office' ? ps.find(p => p.office) : d && d.a0 ? ps.find(p => p.name === d.a0) : null;
    if (d && d.a0 && !want) return toast(d.a0 === '@office' ? 'Hire at least two assistants first' : 'This project has nobody who could sit in a room (is it running, and are its people active?)', true);
    const first = (want || ps[0]).name, withWho = p => (ps.find(x => x.name === p) || {}).office ? 'Who sits in the room (at least two; you break a tie)' : 'Who sits in the room (with the Master)';
    const people = p => (ps.find(x => x.name === p) || {people: []}).people.map(x => `<label class="row sm" style="gap:6px;margin:4px 0;font-weight:400"><input type="checkbox" class="rm_p" value="${esc(x.key)}" style="width:auto"> ${esc(x.name)}</label>`).join('') || '<span class="mut sm">This project has no team members yet.</span>';
    modal(`<h2>Open a decision room</h2><p class="mut sm">The people you choose and the Master discuss the question in turns. Everyone sees what the others say and may change position. If they do not all agree, the weighted majority decides.</p>
      <label>Project</label><select id="rm_project">${ps.map(p => `<option value="${esc(p.name)}" ${p.name === first ? 'selected' : ''}>${esc(p.office ? 'My assistants' : p.name)}</option>`).join('')}</select>
      <label>Question</label><textarea id="rm_q" style="min-height:80px" placeholder="Which database for the order service? We expect 50 writes a second."></textarea>
      <label id="rm_wl">${withWho(first)}</label><div id="rm_people">${people(first)}</div>
      <label>How</label><select id="rm_how"><option value="discuss">A real discussion, in turns</option><option value="vote">A quick vote, one answer each</option>
        <option value="plan">Plan together (architecture, design): they write one plan and agree on it, with a report at the end</option></select>
      <div id="rm_choice"><label>Options (one per line, 2 to 6; may be left empty if you let them bring their own)</label><textarea id="rm_o" style="min-height:70px" placeholder="PostgreSQL&#10;SQLite"></textarea>
      <label class="row sm" style="gap:6px;margin-top:12px;font-weight:400"><input type="checkbox" id="rm_new" style="width:auto"> Let them put their own options on the table (not only the ones above)</label></div>
      <div id="rm_planbox" style="display:none"><p class="mut sm" style="margin:10px 0 0">No options: everyone proposes, the editor writes one complete plan from the proposals, the others approve it or object with the exact change, and the editor rewrites it until everyone (or the majority, after the last round) approves. You can join in at any time. It ends with a report.</p>
        <label>Who writes the plan (the editor)</label><select id="rm_ed"></select></div>
      <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('roomCreate')}>Open the room</button></div>`);
    const editors = p => { const pr = ps.find(x => x.name === p) || {people: []};
      return `<option value="">${pr.office ? 'The first person you chose' : 'The Master (default)'}</option>` + pr.people.map(x => `<option value="${esc(x.key)}">${esc(x.name)}</option>`).join(''); };
    $('rm_ed').innerHTML = editors(first);
    $('rm_how').onchange = e => { const plan = e.target.value === 'plan'; $('rm_choice').style.display = plan ? 'none' : ''; $('rm_planbox').style.display = plan ? '' : 'none';
      $('rm_q').placeholder = plan ? 'What should we plan? e.g. The architecture of the order service: we expect 50 orders a minute, one small server, the team knows Python.' : 'Which database for the order service? We expect 50 writes a second.'; };
    $('rm_project').onchange = e => { $('rm_people').innerHTML = people(e.target.value); $('rm_wl').textContent = withWho(e.target.value); $('rm_ed').innerHTML = editors(e.target.value); }; },
  async roomCreate() { const r = await post('/rooms', {project: val('rm_project'), question: val('rm_q'), options: val('rm_o').split('\n').map(x => x.trim()).filter(Boolean),
      people: [...document.querySelectorAll('.rm_p:checked')].map(x => x.value), how: val('rm_how'), allow_new: !!($('rm_new') && $('rm_new').checked),
      editor: val('rm_how') === 'plan' ? val('rm_ed') : ''});
    if (done(r, 'The room is open; the first person has the floor.')) { modal(''); go('#/rooms/' + encodeURIComponent(r.room.id)); } },
  roomSay: d => modal(`<h2>Say something to the room</h2><p class="mut sm">It goes into the discussion; whoever speaks next reads it.</p><textarea id="rs_t" style="min-height:90px"></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('roomSaySend', d.a0)}>Add it</button></div>`),
  async roomSaySend(d) { if (done(await post('/rooms/' + d.a0 + '/say', {text: val('rs_t')}), 'Added to the discussion')) { modal(''); render(true); } },
  async roomClose(d) { if (!confirm('Close the room now and count the positions as they stand?')) return; if (done(await post('/rooms/' + d.a0 + '/close', {}), 'The room is closed')) render(true); },
  roomOverrule: d => modal(`<h2>Overrule the room</h2><label>Your decision</label><select id="ro_c">${(d.a1 || '').split('|').map(o => `<option>${esc(o)}</option>`).join('')}</select>
    <label>Why</label><textarea id="ro_r" style="min-height:80px"></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('roomOverruleSend', d.a0)}>Overrule</button></div>`),
  async roomOverruleSend(d) { if (done(await post('/rooms/' + d.a0 + '/overrule', {choice: val('ro_c'), reason: val('ro_r')}), 'Your decision replaces the room\'s')) { modal(''); render(true); } },
  navToggle: () => { if (matchMedia('(max-width:760px)').matches) return document.body.classList.toggle('navopen');     /* a phone: the menu slides in */
    const off = document.body.classList.toggle('navmin'); try { localStorage.setItem('emara.navmin', off ? '1' : '0'); } catch (e) {}       /* a PC: the side bar is closed or opened, and stays that way */
    window.dispatchEvent(new Event('resize')); },
  chatPeople: () => { S.chat.nolist = !S.chat.nolist; try { localStorage.setItem('emara.chatList', S.chat.nolist ? '0' : '1'); } catch (e) {} render(true); },
  exportProject: d => { window.open('/api/v1/projects/' + encodeURIComponent(d.a0) + '/export' + (KEY ? '?key=' + encodeURIComponent(KEY) : '')); toast('The project file is being downloaded'); },
  importProject: () => modal(`<h2>Import a project</h2><p class="mut sm">Brings in a whole project: people, tasks, messages, memory, plan, history and stored files. Nothing starts by itself: its chats arrive closed, and a project that was running arrives paused.</p>
    <label>From a project file (made by Export)</label><input type="file" id="imp_file" accept=".zip">
    <label>Or from another hub on this PC (its folder)</label><div class="row"><input id="imp_folder" placeholder="H:\\EmaraAI-Hub" style="flex:1"><button ${act('importSources')}>Show its projects</button></div><div id="imp_list" class="col" style="margin-top:8px"></div>
    <label>If a project with that name is already here</label><select id="imp_mode"><option value="copy">Keep both (the new one gets the next free name)</option><option value="replace">Replace the one here</option></select>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('importFile')}>Import the file</button></div>`),
  async importSources() { const r = await api('/project-import/sources?' + qs({folder: val('imp_folder')})); if (!r.ok) return done(r, '');
    $('imp_list').innerHTML = r.projects.length ? r.projects.map(p => `<div class="row sm"><b>${esc(p.name)}</b><span class="mut">${esc(p.status)} · ${p.people} people · ${p.tasks} tasks</span><span class="sp"></span><button ${act('importFromHub', p.name)}>Import</button></div>`).join('') : '<span class="mut sm">That hub has no projects.</span>'; },
  async importFromHub(d) { toast('Importing ' + d.a0 + ' …'); ACT.imported(await post('/project-import', {hub_folder: val('imp_folder'), project: d.a0, mode: val('imp_mode')})); },
  async importFile() { const f = $('imp_file').files[0]; if (!f) return toast('Choose a project file first', true); toast('Importing …');
    ACT.imported(await api('/project-import?' + qs({mode: val('imp_mode')}), {method: 'POST', headers: {'Content-Type': 'application/zip'}, body: f})); },
  imported(r) { if (!r.ok) return done(r, ''); const i = r.imported || {};
    modal(`<h2>${esc(r.project.name)} was imported</h2><p class="sm">${i.roles || 0} people · ${i.tasks || 0} tasks · ${i.messages || 0} messages · ${i.memory || 0} memory notes · ${r.files_stored || 0} files${r.replaced ? ' · replaced the project that was here' : ''}</p>
      ${(r.notes || []).map(n => `<p class="sm mut">${esc(n)}</p>`).join('')}
      <div class="row" style="margin-top:20px"><span class="sp"></span><button class="pri" ${act('goto', '#/projects/' + encodeURIComponent(r.project.name))} data-mclose="1">Open it</button></div>`); render(true); },
  newProject: () => modal(`<h2>Start a project</h2><p class="mut sm">Say what you want. The Master writes the plan and the architecture first, then builds the team and gives out the work.</p>
    <label>What should be built?</label><textarea id="np_goal" placeholder="A small web shop for handmade soap with a cart, checkout and an admin page…" style="min-height:110px"></textarea>
    <label>Name (optional)</label><input id="np_name" placeholder="soap-shop"><label>Folder on this PC where the work goes (optional)</label><input id="np_folder" placeholder="D:\\Projects\\soap-shop">
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('createProject')}>Start project</button></div>`),
  async createProject() { const r = await post('/projects', {goal: val('np_goal'), name: val('np_name'), folder: val('np_folder'), start: true}); if (done(r, 'Project started. The Master is being called in.')) { modal(''); go('#/projects/' + encodeURIComponent(r.project.name)); } },
  newDept: d => modal(`<h2>${d.a0 ? 'Edit' : 'New'} department</h2><label>Name</label><input id="nd_name" value="${esc(d.a0 || '')}" ${d.a0 ? 'readonly' : ''} placeholder="Engineering"><label>Mission</label><textarea id="nd_mission" placeholder="Build and maintain the products.">${esc(d.a1 || '')}</textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('saveDept')}>Save</button></div>`),
  async saveDept() { if (done(await post('/company/departments', {name: val('nd_name'), mission: val('nd_mission')}), 'Department saved')) { modal(''); render(true); if (S.drawer) drawer(S.drawer.kind, ...S.drawer.args, true); } },
  newKnow: async () => { const k = await api('/company/knowledge'); modal(`<h2>Add company knowledge</h2><p class="mut sm">Every chat of the company reads this before it starts working.</p><label>Category</label><select id="nk_cat">${k.categories.map(c => `<option>${esc(c.name)}</option>`).join('')}</select>
    <label>Title</label><input id="nk_title" placeholder="Always write tests first"><label>Text</label><textarea id="nk_text" style="min-height:120px" placeholder="The rule, standard or fact, written so that somebody new understands it."></textarea>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('saveKnow')}>Add</button></div>`); },
  async saveKnow() { if (done(await post('/company/knowledge', {category: val('nk_cat'), title: val('nk_title'), text: val('nk_text')}), 'Added. New chats will read it.')) { modal(''); render(true); } },
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
    if (done(r, 'Assigned. The person is being told.')) { modal(''); await post('/supervisor/tick'); render(true); } },
  /* hire: five short steps */
  async hire(d) {
    const o = S.orgData || await api('/company/org'), projects = o.projects.filter(p => p.status !== 'archived').map(p => p.name);
    if (!projects.length) return toast('Start a project first: people are hired into a project', true);
    S.hire = {step: 0, o, v: {project: d.a0 || projects[0], department: d.a1 || '', seniority: 'mid', level: 'specialist'}};
    hireStep();
  },
  hireNext: d => { hireRead(); const v = S.hire.v, s = S.hire.step;
    if (d.a0 !== 'back' && s === 0 && !v.job_title) return toast('Give the job title, e.g. Frontend Engineer', true);
    if (d.a0 !== 'back' && s === 2 && (v.responsibilities || '').length < 80) return toast('Describe the responsibilities in a few sentences (the person works from this text)', true);
    S.hire.step = Math.max(0, Math.min(4, s + (d.a0 === 'back' ? -1 : 1))); hireStep(); },
  async hireDo() { const v = S.hire.v; const r = await post('/company/hire/' + encodeURIComponent(v.project), {...v, skills: (v.skills || '').split(',').map(s => s.trim()).filter(Boolean), start: true});
    if (done(r, `${r.ok ? r.person.name : ''} joined the company`)) { modal(''); S.orgData = null; await render(true); drawer('person', v.project, r.person.key); } },
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
  ['Briefing: how is the company doing?', () => ACT.briefing()], ['View activity', () => go('#/activity')], ['View decisions', () => go('#/decisions')], ['Open the org chart', () => go('#/company')], ['Today\'s report', () => { S.rep.kind = 'daily'; go('#/reports'); }],
  ['Switch light / dark', () => ACT.theme()], ['Settings', () => go('#/ops/settings')], ['Connections', () => go('#/ops/conn')], ['PC load', () => go('#/pc')], ['Tools & cost', () => go('#/tools')], ['Setup', () => go('#/setup')]];
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
  if (q.length >= 2) { const r = await api('/company/search?' + qs({q})); found = (r.results || []).map(x => ({...x, run: () => ({project: () => go('#/projects/' + encodeURIComponent(x.title)), agent: () => drawer('person', x.project, x.key), department: () => drawer('dept', x.key), task: () => drawer('task', x.key),
    knowledge: () => { S.know = {tab: 'company', q: x.title, cat: ''}; go('#/knowledge'); }, message: () => go('#/messages/' + encodeURIComponent(x.project)), artifact: () => window.open('/api/v1/files/' + x.key)}[x.type] || (() => {}))()})); }
  P.items = [...found, ...cmds]; P.i = 0; palDraw();
}
function palDraw() { const r = $('palr'); if (!r) return; r.innerHTML = P.items.map((x, i) => `<div class="it ${i === P.i ? 'on' : ''}" data-pal="${i}"><span class="ty">${x.type}</span><div class="clip"><div class="clip">${esc(x.title)}</div>${x.sub ? `<small class="clip">${esc(x.sub)}</small>` : ''}</div></div>`).join('') || '<div class="empty">Nothing found</div>'; const on = r.querySelector('.on'); if (on) on.scrollIntoView({block: 'nearest'}); }
function palRun(i) { const x = P.items[i]; if (!x) return; palette(false); x.run(); }

/* ---------- wiring ---------- */
function brand(c) { $('brand').innerHTML = `<span class="logo">${esc(initials(c.name))}</span><div><b>${esc(c.name)}</b><small>${esc(c.tagline)}</small></div>`; document.title = c.name; }
document.addEventListener('click', e => {
  const t = e.target;
  if (t.closest('[data-pal]')) return palRun(+t.closest('[data-pal]').dataset.pal);
  if (t.closest('[data-pclose]')) return palette(false);
  const m = t.closest('[data-mclose]'), d = t.closest('[data-do]'), o = t.closest('[data-open]'), c = t.closest('[data-close]');
  if (d) { e.preventDefault(); e.stopPropagation(); if (m) modal(''); ACT[d.dataset.do](d.dataset); return; }
  if (m) modal('');
  if (o) { e.preventDefault(); if (c) closeDrawer(); drawer(o.dataset.open, o.dataset.a0, o.dataset.a1); return; }
  if (c) closeDrawer();
});
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
const wfView = async () => {
  if (!W.catalog.length || !W.dirty) await wfLoad();
  const c = W.cur, groups = [['trigger', 'Start'], ['logic', 'Logic'], ['action', 'Do']];
  const list = `<div class="wfl"><button class="pri" style="width:100%;justify-content:center;margin-bottom:10px" ${act('wfNew')}>+ New workflow</button>${W.list.map(w => `<div class="wli ${c && c.id === w.id ? 'on' : ''}" ${act('wfOpen', w.id)}><div class="row"><i class="dot" style="background:${w.enabled ? 'var(--ok)' : 'var(--dim)'}"></i><b class="clip sp">${esc(w.name)}</b>${w.last_status ? `<span class="tag ${w.last_status === 'ok' ? 'ok' : 'err'}">${w.last_status === 'ok' ? 'ok' : 'failed'}</span>` : ''}</div>
      <div class="xs mut clip" style="margin-top:4px">${esc(w.triggers.join(', '))} · ${w.nodes.length} steps · ${w.runs} runs${w.created_by === 'master' ? ' · by the Master' : ''}</div></div>`).join('') || '<div class="mut sm">No workflows yet. Create one, or ask the Master to build it.</div>'}</div>`;
  if (!c) return `<div class="wf">${list}<div class="card" style="grid-column:2/-1">${empty('Automate what repeats', 'A workflow starts on an event, on a schedule or by hand, and then does things: message the Master, assign a task, call a web address, run a script. Drag the steps onto the canvas and connect them.', `<button class="pri" ${act('wfNew')}>Create the first workflow</button>`)}</div></div>`;
  const sel = c.nodes.find(n => n.id === W.sel);
  const right = sel ? `<div class="card"><div class="row"><h3 class="sp">${esc(spec(sel.type).label)}</h3><button class="quiet s" ${act('wfDelNode')}>Delete step</button></div><div class="xs mut" style="margin:4px 0 6px">${esc(spec(sel.type).about)}</div>
      <label>Name on the canvas</label><input id="wfp__name" data-nn="1" value="${esc(sel.name || '')}">${spec(sel.type).params.map(p => field(sel, p)).join('')}
      <div class="xs mut" style="margin-top:12px">Use data from earlier steps in any text field:<br><span class="mono">{{event.payload.title}}</span> <span class="mono">{{event.project}}</span> <span class="mono">{{trigger.field}}</span> <span class="mono">{{last.field}}</span></div>
      <datalist id="wf_events">${W.events.map(e => `<option>${esc(e)}</option>`).join('')}<option>task.*</option></datalist><datalist id="wf_projects">${(W.projects || []).map(p => `<option>${esc(p.project)}</option>`).join('')}</datalist></div>`
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
  document.querySelectorAll('.wfx .wpal').forEach(p => { p.ondragstart = e => { e.dataTransfer.setData('text/plain', p.dataset.type); S.dragging = true; }; p.ondragend = () => { S.dragging = false; };
    p.onclick = () => wfAdd(p.dataset.type, 60 + c.scrollLeft + (W.cur.nodes.length % 4) * 250, 60 + c.scrollTop + Math.floor(W.cur.nodes.length / 4) * 110); });
  c.ondragover = e => e.preventDefault();
  c.ondrop = e => { e.preventDefault(); S.dragging = false; const t = e.dataTransfer.getData('text/plain'); if (spec(t).type) { const p = pos(e); wfAdd(t, p.x - NW / 2, p.y - NH / 2); } };
  inner.onmousedown = e => {
    const pin = e.target.closest('.pin.out'), el = e.target.closest('.wn'), edge = e.target.closest('.weh');
    if (edge) { W.cur.edges.splice(+edge.dataset.e, 1); wfDirty(); redraw(); return; }
    if (!el) { if (W.sel) { W.sel = ''; render(); } return; }
    const n = W.cur.nodes.find(x => x.id === el.dataset.n); e.preventDefault(); S.dragging = true;
    if (pin) {                                                        /* draw a connection */
      const out = pin.dataset.out, x1 = n.x + NW, y1 = n.y + outY(n, out), tmp = () => $('wftmp');
      const move = ev => { const p = pos(ev), dx = Math.max(40, Math.abs(p.x - x1) / 2); tmp().setAttribute('d', `M${x1},${y1} C${x1 + dx},${y1} ${p.x - dx},${p.y} ${p.x},${p.y}`); };
      const up = ev => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); S.dragging = false;
        const target = document.elementFromPoint(ev.clientX, ev.clientY), to = target && target.closest('.wn'), m = to && W.cur.nodes.find(x => x.id === to.dataset.n);
        if (m && m.id !== n.id) { if (spec(m.type).kind === 'trigger') toast('A start step cannot have something before it', true);
          else if (!W.cur.edges.some(x => x.from === n.id && x.to === m.id && x.out === out)) { W.cur.edges.push({from: n.id, to: m.id, out}); wfDirty(); } }
        redraw(); };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up); return;
    }
    const start = pos(e), ox = n.x, oy = n.y; let moved = false;      /* move a step */
    const move = ev => { const p = pos(ev); n.x = Math.max(4, Math.round(ox + p.x - start.x)); n.y = Math.max(4, Math.round(oy + p.y - start.y)); moved = true; el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; redraw(); };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); S.dragging = false; if (moved) wfDirty(); if (W.sel !== n.id || moved) { W.sel = n.id; render(); } };
    document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
  };
  const cur = () => W.cur.nodes.find(n => n.id === W.sel);
  document.querySelectorAll('.wfx .wfr [data-p]').forEach(i => { i.oninput = i.onchange = () => { cur().params[i.dataset.p] = i.value; wfDirty(); const b = document.querySelector(`.wn[data-n="${W.sel}"] .wb`); if (b) b.textContent = summary(cur()); }; });
  const nn = document.querySelector('.wfx .wfr [data-nn]'); if (nn) nn.oninput = () => { cur().name = nn.value; wfDirty(); const b = document.querySelector(`.wn[data-n="${W.sel}"] .wh b`); if (b) b.textContent = nn.value; };
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
});

VIEWS.workflows = async () => `<div class="ph"><div class="sp"><h1>Workflows</h1><p>Automate what repeats: a workflow starts on an event, a schedule, a webhook or by hand, then runs its steps. Drag steps onto the canvas and connect them.</p></div></div><div class="wfx">${await (async () => { const o = await api('/company/overview'); W.projects = (o.projects || []).map(p => ({project: p.name, name: p.name})); return wfView(); })()}</div>`;
AFTER.workflows = () => wfWire();

/* ---------- operations: the technical pages, the PC, the tools, first-time setup ---------- */
/* the maintainer: the agent that looks after the platform itself. It proposes; you approve; every change can be reverted. */
const maintHtml = async () => { const m = await api('/maintenance'); if (!m.ok) return '';
  const who = m.maintainer, when = ts => ts ? dayOf(ts) + ' ' + hm(ts) : 'never';
  const TONE2 = {proposed: 'warn', applied: 'ok', accepted: 'ok', rejected: '', reverted: '', failed: 'err'};
  const edit = e => `<details style="margin-top:6px"><summary class="sm"><span class="mono">${esc(e.path)}</span> ${e.new_file ? tag('new file', 'accent') : e.whole_file ? tag('whole file', 'warn') : ''}</summary>
      ${e.whole_file ? `<pre class="xs" style="white-space:pre-wrap;max-height:260px;overflow:auto;background:var(--ok-bg);padding:8px;border-radius:8px">${esc(e.content)}${e.chars > 4000 ? '\n… (' + e.chars + ' characters)' : ''}</pre>`
        : `<pre class="xs" style="white-space:pre-wrap;max-height:200px;overflow:auto;background:var(--hover);padding:8px;border-radius:8px;margin-bottom:4px" title="what is there now">${esc(e.find)}</pre><pre class="xs" style="white-space:pre-wrap;max-height:200px;overflow:auto;background:var(--ok-bg);padding:8px;border-radius:8px" title="what it becomes">${esc(e.replace) || '(removed)'}</pre>`}</details>`;
  const card = p => `<div class="card" style="${p.status === 'proposed' ? 'border-color:var(--warn)' : ''}"><div class="row" style="align-items:flex-start"><div class="sp"><div class="row wrap" style="gap:6px">${tag(p.kind, p.kind === 'fix' ? 'warn' : 'accent')}${tag(p.status, TONE2[p.status] || '')}<span class="xs mono dim">${esc(p.id)}</span><span class="xs dim">${when(p.created_at)}</span></div>
        <h3 style="margin:8px 0 4px">${esc(p.title)}</h3><div class="prose sm mut" style="white-space:pre-wrap">${esc(p.why)}</div>${p.source ? `<div class="xs" style="margin-top:6px"><a href="${esc(p.source)}" target="_blank" rel="noopener">${esc(p.source)}</a></div>` : ''}
        ${p.edits.map(edit).join('') || '<div class="xs dim" style="margin-top:6px">An idea: no file would be changed.</div>'}${p.note ? `<div class="xs" style="margin-top:8px;color:var(--warn)">${esc(p.note)}</div>` : ''}</div>
      <div class="col" style="gap:6px;min-width:130px">${p.status === 'proposed' ? `<button class="pri s" ${act('maintDo', p.id, 'approve')}>${p.edits.length ? 'Approve & apply' : 'Accept'}</button><button class="s" ${act('maintDo', p.id, 'reject')}>Reject</button>` : p.status === 'applied' ? `<button class="s" ${act('maintDo', p.id, 'revert')} title="Puts every file back as it was before this change">Revert</button>` : ''}</div></div></div>`;
  return `<div class="card"><div class="row" style="align-items:flex-start"><span class="av" style="background:var(--acc)">${ic('adv')}</span><div class="sp"><h2 style="margin:0">Platform maintainer${who ? ' · ' + esc(who.name) : ''}</h2>
        <div class="sm mut" style="margin-top:4px">${who ? `An agent with its own memory (${who.memories} entries, fed from docs/MAINTAINER.md) that looks after EmaraAI itself. Every ${Math.round(m.interval_minutes)} minutes it checks logs, activity and the chats; every ${Math.round(m.research_hours)} hours it also looks at GitHub and the community. It cannot edit the platform: it proposes, you approve, and every applied change can be reverted.` : 'Not hired yet. The maintainer is an agent that checks the platform regularly and proposes fixes and improvements for you to approve.'}</div>
        <div class="row wrap xs" style="margin-top:8px;gap:14px"><span>Checks: <b>${m.enabled ? 'on' : 'off'}</b></span><span>Last check: <b>${when(m.last_check)}</b></span>${m.enabled && who ? `<span>Next: <b>${when(m.next_check)}</b></span>` : ''}<span>Waiting for you: <b style="color:${m.waiting ? 'var(--warn)' : 'inherit'}">${m.waiting}</b></span>${m.last_task ? `<span>Last report: <b>${esc(TASK[m.last_task.status] || m.last_task.status)}</b></span>` : ''}</div>
        ${m.last_task && m.last_task.result ? `<details style="margin-top:8px"><summary class="sm">What it reported last</summary><div class="prose sm mut" style="white-space:pre-wrap;margin-top:6px">${esc(m.last_task.result)}</div></details>` : ''}</div>
      <div class="col" style="gap:6px;min-width:140px">${who ? `<button class="pri s" ${act('maintSet', 'check')}>Check now</button><button class="s" ${act('openPerson', who.project, who.key)}>Its profile & memory</button>` : `<button class="pri s" ${act('maintSet', 'hire')}>Hire the maintainer</button>`}<button class="s" ${act('maintSet', m.enabled ? 'disable' : 'enable')}>${m.enabled ? 'Pause the checks' : 'Resume the checks'}</button></div></div></div>
    ${m.proposals.length ? `<h3 style="margin:var(--s4) 0 8px">Its proposals</h3>${m.proposals.map(card).join('')}` : ''}`; };
Object.assign(ACT, {
  async maintSet(d) { const r = await post('/maintenance', {action: d.a0}); if (done(r, {check: 'The check was handed to the maintainer', hire: 'The maintainer is hired', enable: 'Regular checks are on', disable: 'Regular checks are paused'}[d.a0])) render(true); },
  async maintDo(d) { let body = {};
    if (d.a1 === 'approve' && !confirm('Apply this change to the EmaraAI Hub? A copy of every file is kept, and Revert puts them back.')) return;
    if (d.a1 === 'reject') { const why = prompt('Why not? (the maintainer is told; may be empty)'); if (why === null) return; body = {reason: why}; }
    if (d.a1 === 'revert' && !confirm('Put every file back as it was before this change?')) return;
    let r = await post('/maintenance/proposals/' + d.a0 + '/' + d.a1, body);
    if (r.ok === false && d.a1 === 'revert' && /changed again/.test(r.error.message) && confirm(r.error.message + ' Revert anyway?')) r = await post('/maintenance/proposals/' + d.a0 + '/revert', {force: true});
    if (done(r, {approve: 'Applied. ' + ((((r.proposals || []).find(p => p.id === d.a0)) || {}).note || ''), reject: 'Rejected', revert: 'Reverted'}[d.a1])) render(true); },
  openPerson: d => drawer('person', d.a0, d.a1),
});
VIEWS.ops = async (tab = 'settings') => { const [title, sub] = OPS_TITLE[tab] || [cap(tab), ''];
  const top = tab === 'diag' ? await maintHtml() : '';
  return `<div class="ph"><div class="sp"><h1>${esc(title)}</h1><p>${esc(sub)}</p></div></div>${top}
    <iframe id="actframe" src="/advanced?embed=1#${encodeURIComponent(tab)}" title="${esc(title)}" style="width:100%;height:calc(100vh - 140px);min-height:520px;border:0;border-radius:12px;background:transparent"></iframe>`; };
const secs = s => s < 90 ? s + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : Math.round(s / 360) / 10 + ' h';
VIEWS.pc = async () => {
  const d = await api('/pc/load');
  if (!d.ok || !d.available) return `<div class="ph"><div class="sp"><h1>PC load</h1></div></div><div class="card">${empty('The PC bridge is switched off', 'Switch it on under Operations › Settings › PC.')}</div>`;
  const owners = {}; for (const p of d.processes) (owners[p.owner] = owners[p.owner] || {name: p.owner_name, list: [], mb: 0}).list.push(p), owners[p.owner].mb += p.memory_mb;
  const meter = (label, v) => `<div class="stat"><b style="${v >= 90 ? 'color:var(--err)' : v >= 75 ? 'color:var(--warn)' : ''}">${v}%</b><span>${label}</span></div>`;
  return `<div class="ph"><div class="sp"><h1>PC load</h1><p>What the team is running on this PC. Heavy work (installs, builds, tests, dev servers) takes turns: ${d.limit} at a time.</p></div><button ${act('reload')}>Refresh</button></div>
    <div class="card"><div class="row wrap" style="gap:var(--s5)"><div class="stats">${meter('processor', d.cpu)}${meter('memory', d.memory)}<div class="stat"><b>${d.running.length}/${d.limit}</b><span>heavy jobs</span></div>
      <div class="stat"><b style="${d.waiting.length ? 'color:var(--warn)' : ''}">${d.waiting.length}</b><span>waiting</span></div><div class="stat"><b>${d.sessions.length}</b><span>shell sessions</span></div></div>
      <div class="sp sm">${d.saturated ? `<span style="color:var(--err)">The PC is saturated (${esc(d.saturated)}): new heavy work waits. Nothing that runs is stopped.</span>` : '<span class="mut">The PC has room.</span>'}
        <div class="xs mut" style="margin-top:4px">${d.fast_shell ? 'Each agent has its own PowerShell session' : 'Every command runs in a fresh PowerShell'} · ${esc(d.powershell.split(/[\\/]/).slice(-2).join('/'))}</div></div></div></div>
    <div class="grid g2" style="align-items:start"><div class="card"><h3>Heavy work</h3>${d.running.length || d.waiting.length ? `<div class="list">${d.running.map(r => `<div><div class="sp"><div class="mono clip">${esc(r.label)}</div><div class="xs mut">${esc(r.owner_name)} · running ${secs(r.seconds)}${r.background ? ' · background job' : ''}</div></div>${tag('running', 'accent')}</div>`).join('')}
        ${d.waiting.map(r => `<div><div class="sp"><div class="mono clip">${esc(r.label)}</div><div class="xs mut">${esc(r.owner_name)} · waiting ${secs(r.seconds)}</div></div>${tag('waiting', 'warn')}</div>`).join('')}</div>` : '<div class="mut sm">Nothing heavy is running.</div>'}
      <div class="xs mut" style="margin-top:8px">Since the hub started: ${d.stats.queued} had to wait, ${d.stats.refused} were told the PC is busy, ${d.stats.stopped} processes were stopped.</div></div>
    <div class="card"><h3>Shell sessions</h3>${d.sessions.length ? `<div class="list">${d.sessions.map(s => `<div><div class="sp"><b class="sm">${esc(s.owner_name)}</b><div class="xs mut">${s.commands} commands · idle ${secs(s.idle_seconds)}</div></div>${s.busy ? tag('busy', 'accent') : tag('ready', 'ok')}</div>`).join('')}</div>` : '<div class="mut sm">No agent has a PowerShell session open.</div>'}</div></div>
    <div class="card"><h3>Processes the team started and left running</h3>${Object.keys(owners).length ? Object.entries(owners).map(([o, g]) => `<div style="margin-bottom:12px"><div class="row"><b>${esc(g.name)}</b><span class="xs mut">${g.list.length} process(es) · ${g.mb} MB</span><span class="sp"></span><button class="s bad" ${act('pcStop', o, g.name)}>Stop all of these</button></div>
        <table class="t" style="margin-top:6px"><tbody>${g.list.slice(0, 12).map(p => `<tr><td class="mono">${esc(p.name)}</td><td class="xs mut">pid ${p.pid}</td><td>${p.memory_mb} MB</td><td class="xs mut">running ${p.minutes} min</td><td class="xs mut">${p.cpu_seconds} s CPU</td></tr>`).join('')}</tbody></table></div>`).join('') : '<div class="mut sm">Nothing. Programs agents start (dev servers, watchers) appear here with who started them.</div>'}</div>`;
};
S.toolDays = 7;
VIEWS.remote = async () => {
  const d = await api('/remote');
  if (!d.ok) return empty('Could not load this page', d.error.message);
  const inApp = !!window.EmaraApp;
  return `<div class="ph"><div class="sp"><h1>Phone & remote</h1><p>Open EmaraAI from your phone or another PC. It works only through your own Tailscale network: devices signed in to your Tailscale account. It is never on the public internet.</p></div>
      ${d.you_are_remote ? '' : d.enabled ? `<button ${act('remoteSet', 'off')}>Switch off</button>` : `<button class="pri" ${act('remoteSet', 'on')}>Switch on</button>`}</div>
    ${d.you_are_remote ? `<div class="card"><div class="sm">You are connected from outside as <b>${esc(d.you_are_remote)}</b>. Remote access is switched on and off at the PC itself.</div>${inApp ? `<div style="margin-top:10px"><button ${act('appAddress')}>Change the address this app opens</button></div>` : ''}</div>` : ''}
    ${!d.enabled ? `<div class="card">${empty('Remote access is off', 'Switching it on gives this PC an address that only your own devices can open. You can switch it off again here at any time.')}</div>` : `
    <div class="grid g2" style="align-items:start"><div class="card"><h3>Address</h3>
        <div class="row"><input readonly value="${esc(d.url)}" style="flex:1"><button ${act('copyText', d.url)}>Copy</button></div>
        <div class="xs mut" style="margin-top:8px">Allowed Tailscale account${d.users.length > 1 ? 's' : ''}: ${d.users.map(esc).join(', ') || 'none'}. Anyone else gets nothing, even inside your Tailscale network.</div>
        <div class="xs mut" style="margin-top:6px">From outside you can do everything you can do here, including approving commands on this PC. Keep your phone locked.</div></div>
      <div class="card"><h3>On your Android phone</h3><ol class="sm" style="margin:0;padding-left:18px;line-height:1.7">
        <li>Install the <b>Tailscale</b> app from Google Play and sign in with <b>${esc(d.users[0] || 'the same account as this PC')}</b>. Leave it connected.</li>
        <li>${d.apk ? `Get the app: open <span class="mono">${esc(d.url)}/app/EmaraAI.apk</span> in the phone's browser, or <a href="/app/EmaraAI.apk">download it here</a> and send it to the phone (${Math.round(d.apk_size / 1024)} KB).` : 'The Android app is not built yet (scripts\\build_apk.py).'}</li>
        <li>Android asks to allow installing from this source: allow it once, then install.</li>
        <li>Open <b>EmaraAI</b>. It opens the address above; you can change it from this page inside the app.</li></ol>
        <div class="xs mut" style="margin-top:8px">Without the app, the same address works in the phone's browser.</div></div></div>`}`;
};
/* backups of everything, and what was deleted and can be put back */
VIEWS.vault = async () => { const v = await api('/vault'); if (!v.ok) return empty('Could not load the backups', v.error.message);
  const when = ts => ts ? dayOf(ts) + ' ' + hm(ts) : '';
  const KIND = {manual: 'made by you', daily: 'daily', 'before a restore': 'before a restore', 'before an update': 'before an update'};
  return `<div class="ph"><div class="sp"><h1>Backups & trash</h1><p>Go back to an earlier state of the whole hub, or put back something you deleted.</p></div><button class="pri" ${act('vaultBackup')}>Back up now</button></div>
    ${v.pending_restore ? `<div class="card" style="border-color:var(--warn)"><b>${esc(v.pending_restore)}</b> will be restored when the hub starts next. <button class="s" ${act('restartHub')}>Restart now</button></div>` : ''}
    <div class="grid g2" style="align-items:start">
      <div class="card"><h3>Backups <span class="sp"></span><span class="xs mut">${v.daily ? 'one a day by itself' : 'daily backup is off'} · ${v.keep} of each kind kept</span></h3>
        <div class="xs mut" style="margin-bottom:8px">A backup is the whole database: projects, people, tasks, messages, memory, settings in the database. Files that agents produced stay where they are. Restoring makes a copy of the present state first, then restarts the hub.</div>
        ${v.backups.length ? `<div class="list">${v.backups.map(b => `<div><div class="sp"><b>${when(b.at)}</b> ${tag(KIND[b.kind] || b.kind, b.kind === 'manual' ? 'accent' : '')}${b.broken ? tag('unreadable', 'err') : ''}
            <div class="xs mut">${b.projects ?? '?'} projects · ${b.tasks ?? '?'} tasks · ${size(b.size)}${b.note ? ' · ' + esc(b.note) : ''}</div><div class="xs mono dim">${esc(b.file)}</div></div>
            <button class="s" ${act('vaultRestore', b.file, when(b.at))} ${b.broken ? 'disabled' : ''}>Restore</button><button class="s quiet" ${act('vaultDrop', b.file)} title="Delete this backup file">✕</button></div>`).join('')}</div>` : empty('No backups yet', 'Press "Back up now".')}
        <div class="xs dim" style="margin-top:8px">Kept in ${esc(v.folder)}</div></div>
      <div class="card"><h3>Trash <span class="sp"></span><span class="xs mut">${v.trash.length} item${v.trash.length === 1 ? '' : 's'}</span></h3>
        <div class="xs mut" style="margin-bottom:8px">Whatever you delete - a project, a person, a task - is kept here in full. "Put back" restores it exactly as it was. "Delete for ever" is the only step that cannot be undone.</div>
        ${v.trash.length ? `<div class="list">${v.trash.map(t => `<div><div class="sp"><b class="clip">${esc(t.label)}</b> ${tag(t.kind, '')}<div class="xs mut">deleted ${when(t.deleted_at)} · ${t.rows} records · ${size(t.size)}</div></div>
            <button class="s pri" ${act('trashBack', t.file, t.label)}>Put back</button><button class="s danger" ${act('trashPurge', t.file, t.label)}>Delete for ever</button></div>`).join('')}</div>` : empty('The trash is empty', 'Deleted projects, people and tasks appear here.')}</div></div>`; };
Object.assign(ACT, {
  async vaultBackup() { const note = prompt('A note for this backup (may be empty)', ''); if (note === null) return; toast('Copying the database…'); if (done(await post('/vault', {action: 'backup', note}), 'Backup made')) render(true); },
  async vaultRestore(d) { if (!confirm(`Go back to the backup of ${d.a1}?\n\nEverything that happened after it is undone (the present state is saved as a backup first). The hub restarts; running chats are picked up again where that backup knew them.`)) return;
    const r = await post('/vault', {action: 'restore', file: d.a0}); if (done(r, 'Restoring: the hub restarts now')) { await post('/core/restart', {}); setTimeout(() => location.reload(), 10000); } },
  async vaultDrop(d) { if (!confirm('Delete this backup file? This cannot be undone.')) return; if (done(await post('/vault', {action: 'delete_backup', file: d.a0}), 'Backup deleted')) render(true); },
  async trashBack(d) { if (done(await post('/vault', {action: 'trash_restore', file: d.a0}), d.a1 + ' is back')) render(true); },
  async trashPurge(d) { if (!confirm(`Delete "${d.a1}" for ever? It cannot be put back after this.`)) return; if (done(await post('/vault', {action: 'trash_purge', file: d.a0}), 'Deleted for ever')) render(true); },
  async restartHub() { await post('/core/restart', {}); toast('Restarting…'); setTimeout(() => location.reload(), 9000); },
  /* delete something for good (it goes to the trash first) */
  del: d => { const [kind, ref, project, label] = [d.a0, d.a1, d.a2 || '', d.a3 || d.a1];
    const what = {project: 'the whole project: its people, tasks, messages, memory and plan', agent: 'this person with their chats, own memory and messages', task: 'this task with its messages'}[kind] || 'this';
    modal(`<h2>Delete ${esc(label)}?</h2><p class="mut sm">This removes ${what} from the hub. It is kept in the trash (Operations › Backups & trash), where "Put back" restores it exactly as it was.${kind === 'project' ? ' The chats in ChatGPT and the files on disk are not touched.' : ''}</p>
      ${kind === 'project' ? `<label>Type the project's name to confirm</label><input id="del_ok" placeholder="${esc(label)}">` : ''}
      <div class="row" style="margin-top:16px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri danger" ${act('delDo', kind, ref, project, label)}>Delete</button></div>`); },
  async delDo(d) { if (d.a0 === 'project' && val('del_ok').trim() !== d.a3) return toast('Type the name exactly to confirm', true);
    const r = await post('/vault', {action: 'delete', kind: d.a0, ref: d.a1, project: d.a2});
    if (done(r, d.a3 + ' was deleted. It is in the trash.')) { modal(''); drawer && S.drawer && (S.drawer = null); if (d.a0 === 'project') location.hash = '#/projects'; render(true); } },
});
VIEWS.quality = async () => {
  const d = await api('/quality');
  if (!d.ok) return empty('Could not load the quality page', d.error.message);
  const col = g => ({A: 'var(--ok)', B: 'var(--ok)', C: '', D: 'var(--warn)', E: 'var(--err)'}[g] || '');
  const pts = n => `<b style="color:${n > 0 ? 'var(--ok)' : n < 0 ? 'var(--err)' : ''}">${n > 0 ? '+' : ''}${n}</b>`;
  const on = k => d.gates[k] ? tag('on', 'ok') : tag('off', 'warn');
  return `<div class="ph"><div class="sp"><h1>Quality</h1><p>Who delivers work that holds, and who passes work that does not. Points come from what happens to a person's work; you can add or take points with a reason.</p></div>
      <button ${act('reload')}>Refresh</button><button ${act('givePoints')}>Give or take points</button><button class="pri" ${act('fileDefect')}>Report a defect in accepted work</button></div>
    <div class="card"><h3>Review rules the hub enforces</h3><div class="list">
      <div><div class="sp"><b>Checklist with evidence</b><div class="xs mut">A task has conditions; the report answers each with evidence and the reviewer confirms each one.</div></div>${on('checklist')}</div>
      <div><div class="sp"><b>Everything a user can use is tried</b><div class="xs mut">User-facing work lists every button, command or endpoint with what happened when it was tried. A control that does nothing must be visibly disabled.</div></div>${on('entry_points')}</div>
      <div><div class="sp"><b>Independent check</b><div class="xs mut">A QA person who is not the author checks the work with tools before it can be accepted${d.gates.verify_all ? ' (every task)' : ' (user-facing work, marked tasks, and people with a low score)'}.</div></div>${on('independent_check')}</div></div>
      <div class="xs mut" style="margin-top:8px">Points: first-time acceptance ${d.points.first_pass}, after rework ${d.points.after_rework}, sent back ${d.points.sent_back}, failed independent check ${d.points.check_failed} (checker ${'+' + d.points.check_caught}), defect after acceptance: author ${d.points.defect_author}, reviewer ${d.points.defect_reviewer}, checker ${d.points.defect_verifier}. Below ${d.low_score} in 30 days a person's work is always double-checked. All of this is under Settings › quality.</div></div>
    <div class="card"><h3>Ranking</h3>${d.ranking.length ? `<table><tr><th>Person</th><th>Project</th><th>Grade</th><th>30 days</th><th>Total</th><th>First time</th><th>Sent back</th><th>Defects</th><th>Passed a defect</th><th>Caught</th></tr>
      ${d.ranking.map(r => `<tr><td><a class="b" ${act('qualityOf', r.project, r.key)}>${esc(r.name || 'Master')}</a>${r.low ? ' ' + tag('double-checked', 'warn') : ''}</td><td class="mut">${esc(r.project)}</td>
        <td><b style="color:${col(r.grade)}">${r.grade}</b></td><td>${pts(r.last_30_days)}</td><td>${pts(r.total)}</td><td>${r.first_pass_rate == null ? '–' : r.first_pass_rate + '%'}</td><td>${r.sent_back}</td><td>${r.defects}</td><td>${r.passed_defects}</td><td>${r.caught}</td></tr>`).join('')}</table>` : '<div class="mut sm">Nobody has points yet.</div>'}</div>
    <div class="card"><h3>Latest changes</h3>${d.ledger.length ? `<table><tr><th>When</th><th>Person</th><th>Points</th><th>Why</th><th>Task</th><th>By</th></tr>
      ${d.ledger.map(l => `<tr><td>${ago(l.ts)}</td><td>${esc(l.who)}</td><td>${pts(l.points)}</td><td>${esc(l.why)}${l.note ? ` <span class="xs mut">${esc(l.note.slice(0, 120))}</span>` : ''}</td><td class="mono">${esc(l.task_id || '')}</td><td class="mut">${esc(l.by)}</td></tr>`).join('')}</table>` : '<div class="mut sm">Nothing yet. Points appear when work is accepted, sent back or found defective.</div>'}</div>`;
};
VIEWS.api = async () => {
  const d = await api('/chat-api');
  if (!d.ok) return empty('Could not load the API page', d.error.message);
  const base = d.public || d.local, key = d.key || 'YOUR_KEY';
  const sample = `curl.exe ${d.local}/chat/completions -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" -d "{\\"model\\":\\"chatgpt-fast\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"Say hello in five words\\"}]}"`;
  const st = c => c.status === 'ok' ? tag('ok', 'ok') : tag(c.status.replace(/_/g, ' '), 'warn');
  return `<div class="ph"><div class="sp"><h1>API</h1><p>ChatGPT for other programs, in OpenAI format. Every call opens a new ChatGPT chat and returns its answer. A call can only get text back: it cannot use this PC.</p></div>
      <button ${act('reload')}>Refresh</button>${d.enabled ? `<button ${act('chatApi', 'off')}>Switch off</button>` : `<button class="pri" ${act('chatApi', 'on')}>Switch on</button>`}</div>
    ${!d.enabled ? `<div class="card">${empty('The API is switched off', 'Switching it on makes a key and registers a small reply-only plugin in ChatGPT. Calls use your ChatGPT account and count against its message limits.')}</div>` : `
    <div class="grid g2" style="align-items:start"><div class="card"><h3>Connect a program</h3>
        <label>Address on this PC</label><div class="row"><input readonly value="${esc(d.local)}" style="flex:1"><button ${act('copyText', d.local)}>Copy</button></div>
        ${d.public ? `<label>Address from elsewhere</label><div class="row"><input readonly value="${esc(d.public)}" style="flex:1"><button ${act('copyText', d.public)}>Copy</button></div>` : '<div class="xs mut" style="margin-top:6px">No public address yet: publish the hub on the Connections page to call it from elsewhere.</div>'}
        <label>API key</label><div class="row"><input readonly type="password" id="capi_key" value="${esc(d.key)}" style="flex:1"><button ${act('showKey')}>Show</button><button ${act('copyText', d.key)}>Copy</button><button ${act('chatApi', 'key')}>New key</button></div>
        <div class="xs mut" style="margin-top:8px">In LobeHub or any OpenAI-compatible program: provider "OpenAI compatible", base URL = the address above, API key = this key, model = one of the names on the right.</div>
        <label>Try it</label><textarea readonly style="min-height:74px;font-family:var(--mono, monospace);font-size:12px">${esc(sample)}</textarea>
        <div class="xs" style="margin-top:8px">${d.extension ? '<span class="mut">The extension is connected.</span>' : '<span style="color:var(--err)">The Chrome extension is not connected: calls will fail until it is.</span>'}
          ${d.reply_plugin ? '<span class="mut"> The reply plugin is registered in ChatGPT.</span>' : '<span style="color:var(--warn)"> The reply plugin is not registered in ChatGPT yet: the "chatgpt" models read the answer from the page until it is.</span>'}</div></div>
      <div class="card"><h3>Models</h3><div class="list">${d.models.map(m => `<div><div class="sp"><b class="mono">${esc(m.id)}</b><div class="xs mut">${m.site === 'auto' ? 'the first way that is not at its usage limit: ChatGPT Chat, Claude, then the unlimited API model' : m.site === 'claude_web' ? 'a new claude.ai chat; the answer arrives through the reply connector (or is read from the page)' : m.mode === 'work' ? 'ChatGPT Work: a new chat outside the project; the answer arrives through the reply plugin' : m.private ? 'temporary chat: no plugins, no memory, nothing saved in ChatGPT' : 'new chat in the API project; the answer arrives through the reply plugin'}${m.effort ? ' · effort ' + (m.effort === 'low' && m.mode === 'chat' ? 'instant' : m.effort) : ''}</div></div>${m.blocked ? tag('limit until ' + hm(m.blocked), 'warn') : ''}</div>`).join('')}</div>
        <div class="xs mut" style="margin-top:8px">More names work: an effort as a suffix (<span class="mono">chatgpt-work-max</span>, <span class="mono">claude-opus-high</span>) and a model of the site's own menu after a colon (<span class="mono">chatgpt-work:gpt-6.1-sol</span>, <span class="mono">claude:sonnet</span>).</div>
        ${limitsCard(d.limits, d.fallback)}
        <div class="xs mut" style="margin-top:8px">${d.max_parallel} calls at a time (${d.running} running now). No images, no caller-defined tools, token counts are estimates.</div></div></div>
    <div class="card"><h3>Last calls</h3>${d.calls.length ? `<table><tr><th>When</th><th>Model</th><th>Seconds</th><th>Sent</th><th>Answer</th><th>Result</th><th></th></tr>${d.calls.map(c => `<tr><td>${ago(c.ts)}</td><td class="mono">${esc(c.model)}</td><td>${c.seconds}</td><td>${c.prompt_chars} ch</td><td>${c.reply_chars} ch</td><td>${st(c)}${c.error ? ` <span class="xs mut">${esc(c.error.slice(0, 90))}</span>` : ''}</td><td>${c.chat_url ? `<a href="${esc(c.chat_url)}" target="_blank">chat</a>` : ''}</td></tr>`).join('')}</table>
      <div class="xs mut" style="margin-top:8px">Only sizes and times are kept here, never the text.</div>` : '<div class="mut sm">No call yet.</div>'}</div>`}`;
};
VIEWS.tools = async () => {
  const d = await api('/tools/usage?' + qs({days: S.toolDays}));
  if (!d.ok) return empty('Could not load the tool figures', d.error.message);
  const k = n => n >= 1000 ? Math.round(n / 100) / 10 + 'k' : String(n);
  return `<div class="ph"><div class="sp"><h1>Tools & cost</h1><p>What every chat has to read before it can work, and how the tools are really used.</p></div>
      <div class="seg">${[[1, 'Today'], [7, '7 days'], [30, '30 days']].map(([n, l]) => `<a ${act('toolDays', n)} class="${S.toolDays === n ? 'on' : ''}">${l}</a>`).join('')}</div></div>
    <div class="grid g2">${d.plugins.map(p => `<div class="card"><div class="row"><h2 class="sp">${esc(p.name)}</h2>${tag(p.tools + ' tools', 'accent')}</div>
        <div class="stats" style="margin:12px 0"><div class="stat"><b>${k(p.tokens)}</b><span>tokens per chat</span></div><div class="stat"><b>${Math.round(p.bytes / 102.4) / 10} KB</b><span>tool list</span></div><div class="stat"><b>${p.inner_tools}</b><span>actions behind them</span></div></div>
        <div class="list">${p.list.map(t => `<div><b class="mono" style="width:76px">${esc(t.name)}</b><span class="sp xs mut clip">${t.actions.length ? esc(t.actions.join(', ')) : 'several calls in one request'}</span><span class="xs dim">${Math.round(t.bytes / 102.4) / 10} KB</span></div>`).join('')}</div></div>`).join('')}</div>
    <div class="card"><div class="row wrap" style="gap:var(--s5)"><div class="stats"><div class="stat"><b>${k(d.calls)}</b><span>calls</span></div><div class="stat"><b>${k(d.batches)}</b><span>batches</span></div>
        <div class="stat"><b style="${d.calls > 20 && d.batch_share < 30 ? 'color:var(--warn)' : 'color:var(--ok)'}">${d.batch_share}%</b><span>of calls went through batch</span></div></div>
      <div class="sp sm mut">A batch is one request for several calls: fewer round trips to ChatGPT, less waiting. The rules tell every chat to prefer it; a low share means the chats are not following that.</div></div></div>
    <div class="grid" style="grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);align-items:start"><div class="card"><h3>Actions</h3>${d.actions.length ? `<table class="t"><thead><tr><th>Action</th><th>Plugin</th><th>Calls</th><th>Failed</th><th>Average</th><th>In a batch</th><th>Last</th></tr></thead><tbody>
        ${d.actions.map(a => `<tr><td class="mono">${esc(a.call)}</td><td class="xs mut">${esc(a.plugin)}</td><td>${a.calls}</td><td style="${a.fail_rate >= 20 && a.calls >= 5 ? 'color:var(--err)' : ''}">${a.failed ? a.failed + ' (' + a.fail_rate + '%)' : '–'}</td><td>${a.avg_ms} ms</td><td class="xs mut">${a.in_batch || '–'}</td><td class="xs mut">${ago(a.last)}</td></tr>`).join('')}</tbody></table>` : '<div class="mut sm">No tool has been called in this period.</div>'}</div>
      <div class="card"><h3>Context per chat (estimate)</h3>${d.agents.length ? `<div class="list">${d.agents.map(a => `<div><div class="sp"><b class="sm">${esc(a.name)}</b><div class="xs mut">${esc(a.project)} · ${a.calls} calls · tool list ${k(a.tool_list_tokens)} + results ${k(a.results_tokens)} + arguments ${k(a.arguments_tokens)} tokens</div></div><span class="xs" style="${a.share_of_budget >= 80 ? 'color:var(--warn)' : ''}">${a.share_of_budget}% of a chat</span></div>`).join('')}</div>` : '<div class="mut sm">No chat is open.</div>'}
        <div class="xs mut" style="margin-top:8px">Estimated from text length (4 characters per token). ChatGPT's own count is not visible to the hub.</div></div></div>`;
};
VIEWS.setup = async () => {
  const [sy, c, o, cl, pv] = await Promise.all([api('/system'), api('/connect'), api('/company/overview'), api('/claude/connector'), api('/public')]);
  if (!sy.ok || !c.ok) return empty('Could not check the setup', (sy.error || c.error || {}).message || '');
  const comp = n => (sy.components || []).find(x => x.name === n) || {state: 'UNKNOWN', detail: ''};
  const ext = comp('extension'), pub = comp('public'), res = (c.injector || {}).results || [];
  const plugged = c.connectors.map(x => ({...x, state: res.find(r => r.name === x.name) || {}}));
  const allIn = plugged.length > 0 && plugged.every(p => p.state.installed);
  const steps = [
    {ok: ext.state === 'CONNECTED', title: 'The Chrome extension talks to this hub', why: ext.detail || 'not connected',
     how: `In Chrome open <span class="mono">chrome://extensions</span>, switch on Developer mode, press "Load unpacked" and choose the <span class="mono">extension</span> folder of this version. If the extension is already there, click its icon and set "Hub address" to <span class="mono">${esc(c.local_base)}</span>. Only one hub can use the extension at a time.`},
    {ok: !!c.published && pub.state === 'CONNECTED', title: 'ChatGPT can reach this hub', why: c.published ? ((pv.ok && pv.text) || pub.detail || pub.state) : 'no public address yet',
     how: 'Publishes only the secret plugin path through Tailscale Funnel. This Control Center stays private.' + (pv.ok && Object.keys(pv.checks || {}).length ? `<div class="row wrap xs" style="margin-top:8px;gap:6px">${[['tailscale', 'Tailscale is running'], ['route', 'the hub\'s path is published'], ['private', 'answers inside your Tailscale network'], ['public', 'answers from the internet']].map(([k, l]) => `<span class="tag ${pv.checks[k] ? 'ok' : 'err'}">${pv.checks[k] ? '✓' : '✕'} ${l}</span>`).join('')}</div>${pv.down ? '<div class="xs" style="margin-top:6px;color:var(--warn)">Prompts to ChatGPT chats are held until it answers again.</div>' : ''}${pv.last_ok ? `<div class="xs mut" style="margin-top:4px">Last reachable ${ago(pv.last_ok)}</div>` : ''}` : ''),
     btn: !c.published ? ['Publish the address', 'setupPublish'] : pv.ok && pv.verdict === 'relay_down' ? ['Reconnect Tailscale', 'publicReconnect'] : ['Check again', 'setupCheck']},
    {ok: allIn, title: 'The two plugins are in ChatGPT', why: plugged.map(p => `${esc(p.name)}: ${p.state.installed ? 'installed, ' + (p.state.tools ?? p.tools) + ' tools' : p.state.error ? esc(p.state.error) : 'not installed yet'}`).join(' · '),
     how: `Registers ${plugged.map(p => '<b>' + esc(p.name) + '</b>').join(' and ')} in your ChatGPT account and refreshes their tool lists. The first time, ChatGPT asks you to confirm each one under Plugins › Personal.`, btn: ['Register / refresh the plugins', 'setupPlugins'], needs: ext.state === 'CONNECTED' && !!c.published},
    {ok: (o.projects || []).some(p => p.name !== 'Office'), title: 'A first project', why: (o.projects || []).filter(p => p.name !== 'Office').map(p => esc(p.name)).join(', ') || 'none yet',
     how: 'Describe what you want built; the Master plans it, hires the team and hands out the work.', btn: ['New project', 'newProject'], needs: allIn},
  ];
  const cres = (cl.ok && cl.results) || [], cwant = (cl.ok && cl.wanted) || [], cin = cwant.length > 0 && cwant.every(w => cres.some(r => r.name === w.name && r.installed));
  const claudeStep = {ok: cin, optional: true, title: 'Claude can use the hub\'s tools (optional)', why: cwant.length ? cwant.map(w => { const r = cres.find(x => x.name === w.name) || {}; return `${esc(w.name)}: ${r.installed ? 'connected' : esc(r.error || r.note || 'not connected yet')}`; }).join(' · ') : 'the hub has no public address yet',
     how: `Agents that run on Claude then call the hub's tools directly, like ChatGPT's plugin, instead of typed text commands (without it they still work, only slower). On <a href="https://claude.ai/customize/connectors" target="_blank" rel="noopener">claude.ai › Customize › Connectors</a> press <b>Add › Add custom connector</b> once for each line, with this name and address:
       ${cwant.map(w => `<div class="row" style="margin-top:6px"><input readonly value="${esc(w.name)}" style="max-width:190px"><input readonly value="${esc(w.url)}" style="flex:1"><button class="s" ${act('copyText', w.url)}>Copy address</button></div>`).join('') || '<div class="mut">Publish the address first (step 2).</div>'}
       <div class="row" style="margin-top:10px"><button class="s" ${act('setupClaudeDone', cin ? '' : '1')} ${cwant.length ? '' : 'disabled'}>${cin ? 'I removed them' : 'I added them'}</button><a class="xs" ${act('setupClaude')} title="The extension opens a small claude.ai window and tries to fill in the dialog itself. claude.ai does not always let it.">or let the hub try</a></div>`};
  const next = steps.findIndex(s => !s.ok);
  steps.push(claudeStep);
  return `<div class="ph"><div class="sp"><h1>Setup</h1><p>${next < 0 ? 'Everything is connected. This page shows the same checks whenever you need them.' : 'Four steps, in order. Each one shows what is true right now.'}</p></div><button ${act('reload')}>Check again</button></div>
    ${steps.map((s, i) => `<div class="card" style="${i === next ? 'border-color:var(--acc)' : ''}"><div class="row" style="align-items:flex-start"><span class="av" style="background:${s.ok ? 'var(--ok-bg)' : 'var(--hover)'};color:${s.ok ? 'var(--ok)' : 'var(--mut)'}">${s.ok ? '✓' : i + 1}</span>
        <div class="sp"><h2>${s.title}</h2><div class="sm ${s.ok ? 'mut' : ''}" style="margin:4px 0 8px">${s.ok ? '' : '<b>Now:</b> '}${s.why}</div><div class="sm mut">${s.how}</div></div>
        ${s.btn ? `<button class="${!s.ok && i === next ? 'pri' : ''}" ${act(s.btn[1])} ${s.needs === false ? 'disabled title="Finish the step above first"' : ''}>${s.btn[0]}</button>` : s.ok ? tag('done', 'ok') : ''}</div></div>`).join('')}`;
};
/* usage limits in force, and what answers instead */
const limitsCard = (l, fallback) => !l ? '' : `<h3 style="margin-top:var(--s4)">Usage limits</h3>
  ${l.blocked.length ? `<div class="list">${l.blocked.map(b => `<div><div class="sp"><b>${esc(b.name)}</b> ${tag('until ' + hm(b.until), 'warn')}<div class="xs mut">${esc(b.text || 'reported by the site')}${b.by ? ' · seen by ' + esc(b.by) : ''}</div></div><button class="s" ${act('limitClear', b.way)} title="Use it again now; if the limit is still there the site says so again">Retry now</button></div>`).join('')}</div>` : '<div class="sm mut">No way is at its usage limit.</div>'}
  <div class="xs mut" style="margin-top:8px">${l.fallback ? Object.entries(l.chains).filter(([, v]) => v.length).map(([k, v]) => esc(k) + ' → ' + v.map(esc).join(' → ')).join(' · ') + '.' : 'The fallback is switched off (Settings › ai › fallback).'} ${l.unlimited.provider ? 'Unlimited API model: <b>' + esc(l.unlimited.provider) + (l.unlimited.model ? ' · ' + esc(l.unlimited.model) : '') + '</b>.' : 'No unlimited API model is set: the chain ends at ChatGPT Chat (Settings › ai › unlimited provider).'}${fallback === false ? ' API calls do not fall back (chat_api.fallback is off).' : ''}
    <a ${act('limitTest')} title="Marks ChatGPT Work as limited for 10 minutes, so you can watch the fallback without using up a real limit"> Try the fallback</a></div>`;
Object.assign(ACT, {
  async limitClear(d) { if (done(await post('/limits', {way: d.a0, clear: true}), 'Cleared: it is used again from the next chat')) render(true); },
  async limitTest() { if (!confirm('Mark ChatGPT Work as at its limit for 10 minutes? Agents set to Work move to ChatGPT Chat meanwhile.')) return; if (done(await post('/limits', {way: 'chatgpt/work', minutes: 10}), 'ChatGPT Work is marked as limited for 10 minutes')) render(true); },
  async setupClaudeDone(d) { if (done(await post('/claude/connector', {confirm: !!d.a0}), d.a0 ? 'Claude agents use the connector from their next chat. A chat where it does not answer goes on with text commands by itself.' : 'Claude agents use typed text commands again')) render(true); },
  async setupClaude() { toast('Trying to add the connectors in claude.ai… a small window opens (up to 2 minutes)'); const r = await post('/claude/connector', {}); if (r.ok === false) return toast(r.error.message + ' ' + (r.error.fix || ''), true);
    const bad = (r.results || []).filter(x => !x.installed); toast(bad.length ? 'Not all connectors were added: ' + bad.map(x => x.name + ' (' + (x.error || x.note || x.status) + ')').join('; ') : 'Claude is connected to the hub.', bad.length > 0); render(true); },
  opsToggle: () => { S.opsOpen = !S.opsOpen; nav(); }, reload: () => render(true), toolDays: d => { S.toolDays = +d.a0; render(); },
  async pcStop(d) { if (!confirm(`Stop every process ${d.a1} started and left running on this PC?`)) return; const r = await post('/pc/stop', {owner: d.a0}); if (done(r, `Stopped ${r.stopped} process(es).`)) render(true); },
  async setupPublish() { toast('Publishing…'); const r = await post('/publish', {}); if (done(r, 'Published. ChatGPT can reach the hub now.')) render(true); },
  async setupCheck() { await post('/connect/all', {}); await post('/public', {check: true}); render(true); },
  async publicReconnect() { if (!confirm('Reconnect Tailscale now?\n\nFor a few seconds every Tailscale address of this PC is gone: the public address, and the remote address your phone uses.')) return;
    toast('Reconnecting Tailscale…'); const r = await post('/public/reconnect', {}); if (r.ok === false) return toast(r.error.message + ' ' + (r.error.fix || ''), true);
    toast(r.verdict === 'ok' ? 'The public address answers again.' : 'Reconnected, but it still does not answer: ' + (r.text || ''), r.verdict !== 'ok'); render(true); },
  async setupPlugins() { toast('Registering the plugins in ChatGPT…'); const r = await post('/injector/run', {}); if (r.ok === false) return toast(r.error.message + ' ' + (r.error.fix || ''), true); toast('Done. If ChatGPT asks, confirm each plugin under Plugins › Personal.'); render(true); },
});

document.addEventListener('keydown', e => { const t = document.activeElement, typing = t && /INPUT|TEXTAREA|SELECT/.test(t.tagName);
  if ((e.key === 'Delete' || e.key === 'Backspace') && !typing && (S.route || [])[0] === 'workflows' && W.sel && !$('modal').innerHTML) { e.preventDefault(); ACT.wfDelNode(); } });

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palette(!$('pal').innerHTML); }
  else if (e.key === 'Escape') { if ($('pal').innerHTML) palette(false); else if ($('modal').innerHTML) modal(''); else if (S.drawer) closeDrawer(); }
});
window.addEventListener('hashchange', () => { document.body.classList.remove('navopen'); closeDrawer(); render(); });
(async () => { const c = await api('/company'); if (c.ok) { S.company = c.company; brand(c.company); } nav(); await render(); refreshBadge(); const p = await api('/company/pulse'); S.pulse = p.key || ''; setInterval(tick, 2500); })();

/* ---- turn any block of text right-to-left or left-to-right: a very small button on every card, panel section, dialog and text box.
   Arabic mixed with English is laid out from its first word, and that is often the wrong side. ---- */
(() => {
  const FLIP = S.flip = S.flip || {};
  const keyOf = el => (location.hash.split('/')[1] || 'home') + '|' + (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 70);
  const firstSide = el => { const m = (el.innerText || '').match(/[A-Za-z֐-ࣿ]/); return m && /[֐-ࣿ]/.test(m[0]) ? 'rtl' : 'ltr'; };
  const set = (el, side) => { el.classList.remove('fd-rtl', 'fd-ltr'); if (side) el.classList.add('fd-' + side); };
  const flipify = () => {
    for (const el of document.querySelectorAll('#view .card, #drawer section, #modal .modal, #modal > div > div')) {
      if (el.querySelector(':scope > .flipb') || el.classList.contains('conv') || el.classList.contains('pstrip') || el.querySelector('.tle, iframe') || (el.innerText || '').trim().length < 12) continue;
      const b = document.createElement('a');
      b.className = 'flipb'; b.textContent = '⇄'; b.title = 'Turn this text right-to-left or left-to-right';
      b.onclick = e => { e.stopPropagation(); e.preventDefault(); const k = keyOf(el), now = FLIP[k] || firstSide(el); FLIP[k] = now === 'rtl' ? 'ltr' : 'rtl'; set(el, FLIP[k]); };
      el.classList.add('flipbox'); el.appendChild(b);
      const k = keyOf(el); if (FLIP[k]) set(el, FLIP[k]);
    }
    for (const t of document.querySelectorAll('textarea:not([dir]), input[type=text]:not([dir]), input:not([type]):not([dir])')) t.setAttribute('dir', 'auto');   // a box follows what is typed into it
  };
  let due = 0;
  new MutationObserver(() => { clearTimeout(due); due = setTimeout(flipify, 120); }).observe(document.body, {childList: true, subtree: true});
  flipify();
  /* text boxes: the same small button appears at the corner of the box you are typing in */
  const fl = document.createElement('a'); fl.id = 'flipfloat'; fl.textContent = '⇄'; fl.title = 'Type right-to-left or left-to-right'; document.body.appendChild(fl);
  let box = null;
  const place = () => { if (!box || !document.contains(box)) { fl.style.display = 'none'; return; } const r = box.getBoundingClientRect(); fl.style.display = 'block'; fl.style.left = (r.right - 22) + 'px'; fl.style.top = (r.top + 3) + 'px'; };
  document.addEventListener('focusin', e => { const t = e.target; if (t.matches && t.matches('textarea, input[type=text], input:not([type])') && !t.readOnly) { box = t; place(); } });
  document.addEventListener('focusout', () => setTimeout(() => { if (document.activeElement !== box) { box = null; fl.style.display = 'none'; } }, 150));
  addEventListener('scroll', place, true); addEventListener('resize', place);
  fl.onmousedown = e => e.preventDefault();          // the box keeps the cursor
  fl.onclick = () => { if (!box) return; const now = box.getAttribute('dir') === 'rtl' || (box.getAttribute('dir') !== 'ltr' && getComputedStyle(box).direction === 'rtl') || (box.getAttribute('dir') === 'auto' && /^[^A-Za-z]*[֐-ࣿ]/.test(box.value)) ? 'rtl' : 'ltr'; box.setAttribute('dir', now === 'rtl' ? 'ltr' : 'rtl'); box.style.textAlign = 'start'; };
})();

/* ---- notifications: a bell with what needs you or was said to you; pop-ups and a sound if you want them; you choose the kinds ---- */
(() => {
  const CATS = [['decision', 'Decisions waiting for you'], ['approval', 'Approvals an agent asks for'], ['master', 'What a Master writes to you'], ['assistant', 'What your assistants write to you'],
                ['maintainer', 'The maintainer: proposals and messages'], ['problem', 'Problems: failed or blocked work, limits, lost connections'], ['project', 'A project is finished']];
  const ICON = {decision: '?', approval: '!', master: 'M', assistant: 'A', maintainer: '⚙', problem: '▲', project: '✓'};
  const load = () => { try { return JSON.parse(localStorage.getItem('emara.notif') || '{}'); } catch (e) { return {}; } };
  const N = Object.assign({cats: {}, popup: false, sound: false, away: true, seen: 0, read: 0, list: []}, load());
  CATS.forEach(([k]) => { if (N.cats[k] === undefined) N.cats[k] = true; });
  if (!N.seen) N.seen = Date.now() / 1000;                 // the first time: only what happens from now on
  const save = () => { try { localStorage.setItem('emara.notif', JSON.stringify({...N, list: N.list.slice(-80)})); } catch (e) {} };
  const unread = () => N.list.filter(n => n.ts > N.read && N.cats[n.cat]).length;
  const badge = () => { const b = $('belln'); if (b) { const u = unread(); b.textContent = u ? (u > 99 ? '99+' : u) : ''; b.style.display = u ? '' : 'none'; } };
  const beep = () => { try { const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(), g = a.createGain(); o.frequency.value = 880; g.gain.value = .05; o.connect(g); g.connect(a.destination); o.start(); o.stop(a.currentTime + .14); } catch (e) {} };
  /* push: the hub sends the notices to this device even while the Control Center is closed (services/push.py, /sw.js) */
  const pushOK = () => window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const pushWhyNot = () => window.EmaraApp ? 'Not inside the EmaraAI app: open the same address in Chrome on the phone and switch it on there.'
    : !window.isSecureContext ? 'Needs the secure address: open the Control Center through Phone & remote (https) or on this PC.' : 'This browser cannot receive notifications.';
  const keyBytes = s => { const raw = atob((s + '='.repeat((4 - s.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(raw, c => c.charCodeAt(0)); };
  const deviceName = () => { const u = navigator.userAgent;
    return (/Android/i.test(u) ? 'Android phone' : /iPhone|iPad/i.test(u) ? 'iPhone' : /Windows/i.test(u) ? 'Windows PC' : /Mac/i.test(u) ? 'Mac' : 'A device') + ' · ' +
      (/Edg\//.test(u) ? 'Edge' : /Chrome\//.test(u) ? 'Chrome' : /Firefox\//.test(u) ? 'Firefox' : /Safari\//.test(u) ? 'Safari' : 'browser'); };
  async function pushOn(quiet) {
    if (!pushOK()) throw new Error(pushWhyNot());
    if (Notification.permission !== 'granted') { if (quiet || await Notification.requestPermission() !== 'granted') throw new Error('The browser did not allow notifications for this site'); }
    const reg = await navigator.serviceWorker.register('/sw.js', {scope: '/'}); await navigator.serviceWorker.ready;
    const k = await api('/push'); if (!k.ok) throw new Error(k.error.message);
    let sub = await reg.pushManager.getSubscription();
    const same = sub && sub.options && sub.options.applicationServerKey && btoa(String.fromCharCode(...new Uint8Array(sub.options.applicationServerKey))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') === k.key;
    if (sub && !same) { await sub.unsubscribe(); sub = null; }
    sub = sub || await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: keyBytes(k.key)});
    const r = await post('/push/subscribe', {subscription: sub.toJSON(), cats: CATS.map(([c]) => c).filter(c => N.cats[c]), label: deviceName()});
    if (!r.ok) throw new Error(r.error.message);
    N.push = sub.endpoint; save();
  }
  async function pushOff() {
    try { const reg = await navigator.serviceWorker.getRegistration('/'), sub = reg && await reg.pushManager.getSubscription();
      if (sub) { await post('/push/unsubscribe', {endpoint: sub.endpoint}); await sub.unsubscribe(); } } catch (e) {}
    N.push = ''; save();
  }
  const pop = n => { if (N.push || !N.popup || !('Notification' in window) || Notification.permission !== 'granted' || (N.away && !document.hidden)) return;
    try { const x = new Notification(n.title + (n.project ? ' · ' + n.project : ''), {body: n.text, tag: n.id}); x.onclick = () => { window.focus(); location.hash = n.link; x.close(); }; } catch (e) {} };
  async function poll() {
    const r = await api('/company/notices?' + qs({since: N.seen}));
    if (!r.ok) return;
    const have = new Set(N.list.map(n => n.id)), fresh = r.notices.filter(n => !have.has(n.id));
    if (r.notices.length) N.seen = Math.max(N.seen, ...r.notices.map(n => n.ts));
    if (!fresh.length) return;
    N.list.push(...fresh); N.list = N.list.slice(-80); save(); badge();
    const mine = fresh.filter(n => N.cats[n.cat]);
    if (!mine.length) return;
    const last = mine[mine.length - 1];
    toast((mine.length > 1 ? mine.length + ' new: ' : '') + last.title + (last.text ? ' — ' + last.text.slice(0, 90) : ''));
    mine.slice(-3).forEach(pop);
    if (N.sound) beep();
    if ($('bellpanel')) panel();
  }
  const when = ts => dayOf(ts) === 'Today' ? hm(ts) : dayOf(ts) + ' ' + hm(ts);
  function panel() {
    const old = $('bellpanel'); if (old) old.remove();
    const p = document.createElement('div'); p.id = 'bellpanel';
    const rows = N.list.filter(n => N.cats[n.cat]).slice().reverse().slice(0, 40);
    const perm = 'Notification' in window ? Notification.permission : 'unsupported';
    p.innerHTML = N.settings ? `<div class="bh"><b>Which notifications</b><span class="sp"></span><a data-bn="back">‹ Back</a></div>
        <div class="bset">${CATS.map(([k, l]) => `<label><input type="checkbox" data-cat="${k}" ${N.cats[k] ? 'checked' : ''}> ${l}</label>`).join('')}
          <hr><label><input type="checkbox" data-opt="popup" ${N.popup ? 'checked' : ''} ${perm === 'unsupported' ? 'disabled' : ''}> Pop-up on this device ${perm === 'denied' ? '<span class="xs" style="color:var(--warn)">(blocked in the browser: allow notifications for this site)</span>' : perm === 'unsupported' ? '<span class="xs mut">(not available here)</span>' : ''}</label>
          <label><input type="checkbox" data-opt="away" ${N.away ? 'checked' : ''}> … only while this page is not in front</label>
          <label><input type="checkbox" data-opt="sound" ${N.sound ? 'checked' : ''}> A short sound</label>
          <hr><label><input type="checkbox" data-opt="push" ${N.push ? 'checked' : ''} ${pushOK() ? '' : 'disabled'}> <b>Also when the Control Center is closed</b> (this device, also a phone)</label>
          <div class="xs mut" style="margin:2px 0 0 22px">${pushOK() ? 'Only the title and a short text leave this PC, encrypted for this device.' + (N.push ? ' <a data-bn="ptest" style="text-decoration:underline;cursor:pointer">Send a test</a>' : '') : esc(pushWhyNot())}</div>
          <div class="xs mut" style="margin-top:8px">Kept on this device. The list and the count in the bell always work.</div></div>`
      : `<div class="bh"><b>Notifications</b><span class="sp"></span><a data-bn="read">Mark all read</a><a data-bn="set" title="Choose which ones you get">⚙</a></div>
        <div class="bl">${rows.map(n => `<a class="bn ${n.ts > N.read ? 'new' : ''}" href="${esc(n.link)}" data-bn="go"><i class="c-${n.cat}">${ICON[n.cat] || '•'}</i><span><b>${esc(n.title)}</b>${n.project ? ` <span class="xs mut">· ${esc(n.project)}</span>` : ''}<span class="bt">${esc(n.text)}</span><span class="xs dim">${when(n.ts)}</span></span></a>`).join('') || '<div class="mut sm" style="padding:18px;text-align:center">Nothing yet. Decisions, messages to you and problems appear here.</div>'}</div>`;
    document.body.appendChild(p);
    const r = $('bell').getBoundingClientRect(); p.style.top = (r.bottom + 8) + 'px'; p.style.right = Math.max(8, innerWidth - r.right - 4) + 'px';
    p.onclick = async e => { const a = e.target.closest('[data-bn]'); if (!a) return; const k = a.dataset.bn;
      if (k === 'set') { N.settings = true; return panel(); } if (k === 'back') { N.settings = false; return panel(); }
      if (k === 'read') { N.read = Date.now() / 1000; save(); badge(); return panel(); }
      if (k === 'ptest') { const r = await post('/push/test', {endpoint: N.push}); return toast(r.ok ? 'Sent: it should appear in a few seconds' : r.error.message, !r.ok); }
      if (k === 'go') { N.read = Math.max(N.read, Date.now() / 1000); save(); badge(); p.remove(); } };
    p.onchange = async e => { const t = e.target;
      if (t.dataset.cat) { N.cats[t.dataset.cat] = t.checked; if (N.push) pushOn(true).catch(() => {}); }
      if (t.dataset.opt === 'push') { t.disabled = true;
        try { if (t.checked) { await pushOn(false); toast('This device now gets notifications, also while the Control Center is closed'); } else { await pushOff(); toast('Notifications to this device are off'); } }
        catch (err) { N.push = ''; save(); toast(err.message || String(err), true); }
        return panel(); }
      if (t.dataset.opt) { N[t.dataset.opt] = t.checked;
        if (t.dataset.opt === 'popup' && t.checked && 'Notification' in window && Notification.permission !== 'granted') { const ans = await Notification.requestPermission(); if (ans !== 'granted') { N.popup = false; toast('The browser did not allow pop-ups for this site', true); } panel(); } }
      save(); badge(); };
  }
  ACT.bell = () => { if ($('bellpanel')) return $('bellpanel').remove(); N.settings = false; panel(); };
  document.addEventListener('mousedown', e => { const p = $('bellpanel'); if (p && !p.contains(e.target) && !e.target.closest('#bell')) p.remove(); });
  badge(); poll(); setInterval(poll, 15000);
  if (N.push && pushOK()) pushOn(true).catch(() => {});        // keeps the subscription fresh (the browser may renew it)
})();

/* decision rooms as chats: one conversation per room, with archive and delete */
S.rooms = {show: 'active', timer: null};
const PLAN_PHASE = r => ({propose: 'proposals', draft: 'writing the plan', review: `review ${r.circle - 1}/${r.max_circles}`, revise: 'revising the plan'})[r.phase] || 'planning';
const roomTag = r => r.status === 'open' ? tag(r.how === 'vote' ? 'voting' : r.how === 'plan' ? PLAN_PHASE(r) : 'circle ' + r.circle + '/' + r.max_circles, 'accent')
  : r.status === 'to_owner' ? tag('needs you', 'warn') : tag(r.how === 'plan' ? 'plan agreed' : 'decided', 'ok');
/* a planning room shows its plan (while they work) or its report (when it is over) next to the chat */
const planPane = room => { const rep = room.status !== 'open' && room.report;
  const text = rep ? room.report : room.draft || (room.phase === 'propose' ? 'Nobody has written the plan yet: the members are still proposing. Then ' + room.editor + ' writes it.' : room.editor + ' is writing the plan now.');
  return `<div style="padding:var(--s3) var(--s4)"><div class="row" style="gap:6px;margin-bottom:8px"><b class="sp">${rep ? 'Report' : room.draft ? `The plan · version ${room.draft_version} by ${esc(room.editor)}` : 'The plan'}</b>
    ${rep || room.draft ? `<button class="s" ${act('planCopy', room.id)}>Copy</button><button class="s" ${act('planSave', room.id)}>Download .md</button>` : ''}</div>
    ${rep && room.report_file ? `<div class="xs mut" style="margin-bottom:8px">Also saved as <span class="mono">${esc(room.report_file)}</span></div>` : ''}
    <div class="prose sm" style="white-space:pre-wrap">${esc(text)}</div></div>`; };
/* the chat area keeps the height the owner dragged it to (direct messages and room chats); a double-click on the handle fits the window again */
const CHAT_H = 'emara.chatH';
const savedChatH = () => { try { return +localStorage.getItem(CHAT_H) || 0; } catch (e) { return 0; } };
function chatSize(grid) {
  if (!grid) return;
  const fit = () => { if (matchMedia('(max-width:760px)').matches) { grid.style.height = ''; return; }
    grid.style.height = (savedChatH() || Math.max(380, innerHeight - grid.getBoundingClientRect().top - 30)) + 'px'; };
  fit(); window.onresize = fit;
  let grip = grid.nextElementSibling;
  if (!grip || !grip.classList.contains('chatgrip')) { grip = document.createElement('div'); grip.className = 'chatgrip'; grid.after(grip); }
  grip.title = 'Drag to make the chat taller or shorter (it stays that size) · double-click: fit the window';
  grip.onpointerdown = e => { e.preventDefault(); grip.setPointerCapture(e.pointerId); grip.classList.add('on');
    const y0 = e.clientY, h0 = grid.getBoundingClientRect().height;
    grip.onpointermove = ev => { grid.style.height = Math.max(320, Math.round(h0 + ev.clientY - y0)) + 'px'; };
    grip.onpointerup = () => { grip.onpointermove = grip.onpointerup = null; grip.classList.remove('on');
      try { localStorage.setItem(CHAT_H, String(Math.round(grid.getBoundingClientRect().height))); } catch (err) {} }; };
  grip.ondblclick = () => { try { localStorage.removeItem(CHAT_H); } catch (e) {} fit(); toast('The chat fits the window again'); };
}
VIEWS.rooms = async id => {
  const rm = await api('/rooms?' + qs({archived: S.rooms.show === 'archived' ? '1' : '0'}));
  if (!rm.ok) return empty('Could not load the decision rooms', rm.error.message);
  S.roomProjects = (rm.projects || []).filter(p => p.people.length);
  let room = rm.rooms.find(r => r.id === id);
  if (id && !room) { const all = await api('/rooms?archived=all'); room = all.ok && all.rooms.find(r => r.id === id); if (room && room.archived !== (S.rooms.show === 'archived')) { S.rooms.show = room.archived ? 'archived' : 'active'; return VIEWS.rooms(id); } }
  if (!room && rm.rooms.length && window.innerWidth > 760) room = rm.rooms[0];
  const head = `<div class="ph commhead"><div class="sp"><h1>Communication</h1><div class="seg" style="margin-top:10px"><a href="#/messages">Direct messages</a><a class="on" href="#/rooms">Decision room chats</a></div></div><button class="pri" ${act('roomOpen')}>${ic('plus')} Open a room</button></div>`;
  const lastTs = r => r.turns.length ? r.turns[r.turns.length - 1].at : r.opened_at;
  const kind = r => r.how === 'plan' ? 'planning' : r.how === 'vote' ? 'vote' : 'discussion';
  const faces = (ms, n = 6) => `<span class="faces">${ms.slice(0, n).map(m => `<span title="${esc(m.name + (m.job ? ' · ' + m.job : ''))}">${av(m.name, '', 'sm')}</span>`).join('')}${ms.length > n ? `<span class="xs mut" style="margin-left:10px">+${ms.length - n}</span>` : ''}</span>`;
  const rcard = r => `<div class="pc ${room && room.id === r.id ? 'on' : ''}" ${act('roomGo', r.id)}><div class="row"><span class="av" style="background:var(--acc)">${r.how === 'plan' ? '📄' : r.how === 'vote' ? '✓' : ic('msg')}</span>
      <div class="sp clip"><b class="clip" style="display:block">${esc(r.question)}</b><span class="xs mut clip" style="display:block">${esc(r.project)} · ${kind(r)}</span></div>${roomTag(r)}</div>
    <div class="row" style="margin-top:8px">${faces(r.members)}<span class="sp"></span><span class="xs dim clip" style="max-width:45%">${r.status === 'open' && r.floor ? '🎤 ' + esc(r.floor) : r.result ? esc(r.result) : ''}</span></div>
    <div class="row xs dim" style="margin-top:6px"><span>${r.turns.filter(t => t.text).length} messages · ${ago(lastTs(r))}</span></div></div>`;
  const plist = `<div class="plist"><div class="seg" style="margin-bottom:10px">${[['active', 'Active'], ['archived', 'Hidden']].map(([k, l]) => `<a ${act('roomsShow', k)} class="${S.rooms.show === k ? 'on' : ''}">${l}</a>`).join('')}</div>
    ${rm.rooms.length ? rm.rooms.map(rcard).join('') : `<div class="xs mut" style="padding:8px">${S.rooms.show === 'archived' ? 'No hidden rooms.' : 'No decision rooms yet. Open one with the button above.'}</div>`}</div>`;
  const grid = inner => `<div class="comm nod m-${id && room ? 'chat' : 'list'}" id="commgrid">${plist}${inner}</div>`;
  if (!room) return head + grid(`<div class="card conv">${empty('Choose a room', 'Pick a room on the left to read its discussion.')}</div>`);
  S.rooms.cur = room;
  const open = room.status === 'open', plan = room.how === 'plan';
  const tab = plan ? (S.rooms['tab:' + room.id] || (!open && room.report ? 'plan' : 'chat')) : 'chat';
  const where = t => plan ? (t.circle <= 1 ? 'proposals' : 'review ' + (t.circle - 1)) : room.how === 'vote' ? '' : 'circle ' + t.circle;
  const ptag = p => p ? tag(p, p === 'approve' ? 'ok' : p === 'object' ? 'warn' : 'accent') : '';
  const turns = room.turns.filter(t => t.text || t.position);
  const cont = (t, i) => { const p = turns[i - 1]; return !!p && p.who === t.who && p.position === t.position && t.at - p.at < 300 && dayOf(p.at) === dayOf(t.at); };
  const daysep = (t, i) => i === 0 || dayOf(turns[i - 1].at) !== dayOf(t.at) ? `<div class="daysep"><span>${esc(dayOf(t.at))}</span></div>` : '';
  const tl = turns.map((t, i) => daysep(t, i) + `<div class="tle ${t.owner ? 'me' : ''} ${cont(t, i) ? 'cont' : ''}">${cont(t, i) ? `<span class="gut">${hm(t.at)}</span>` : av(t.owner ? 'You' : t.who)}<div class="tlb">
      <div class="tlh"><b>${esc(t.owner ? 'You' : t.who)}</b>${t.job ? `<span class="job">${esc(t.job)}</span>` : ''}<time class="xs dim">${hm(t.at)}</time>${ptag(t.position)}<span class="sp"></span><span class="xs dim">${where(t)}</span></div>
      <div class="bubble ${t.owner ? 'own' : t.text ? '' : 'sys'}">${esc(t.text || 'Keeps its position; nothing new.')}</div></div></div>`).join('');
  const thinking = open && room.floor ? `<div class="tle hub"><div class="tlb"><div class="bubble sys">🎤 ${esc(room.floor)} has the floor and is answering…</div></div></div>` : '';
  const result = room.result || room.status === 'to_owner' ? `<div class="tle hub"><div class="tlb"><div class="bubble sys">${plan ? `${room.status === 'to_owner' ? 'Not agreed' : 'The plan is agreed'}: <b>${esc(room.result_how)}</b> · <a ${act('planTab', room.id, 'plan')} style="text-decoration:underline;cursor:pointer">read the report</a>`
    : room.result ? `Decided: <b>${esc(room.result)}</b> (${esc(room.result_how)})${room.overruled_by ? ' · overruled by you: ' + esc(room.overrule_reason) : ''}` : 'The room could not decide: it is waiting for you.'}</div></div></div>` : '';
  const optBtn = plan ? '' : `<button class="quiet s" ${act('roomOpts', room.id, room.allow_new ? '1' : '')}>Options</button>`;
  const more = optBtn + (room.how !== 'vote' ? `<button class="quiet s" ${act('roomMore', room.id, open ? 'open' : '', room.allow_new ? '1' : '')}>${open ? '+ More rounds' : 'Continue the discussion'}</button>` : '');
  const btns = open ? `${more}<button class="quiet s" ${act('roomClose', room.id)}>Close and count</button>`
    : `${more}${plan ? '' : `<button class="quiet s" ${act('roomOverrule', room.id, room.options.join('|'))}>Overrule</button>`}<button class="quiet s" ${act('roomArchive', room.id, room.archived ? 'unarchive' : 'archive')}>${room.archived ? 'Show' : 'Hide'}</button><button class="quiet s danger" ${act('roomDelete', room.id)}>Delete</button>`;
  const chips = room.members.map(m => `<span class="rchip ${open && room.floor === m.name ? 'floor' : ''}" title="${esc((m.job ? m.job + ' · ' : '') + 'quality grade ' + m.grade)}">${av(m.name, '', 'sm')}<span><b>${esc(m.name)}</b>${m.job ? `<span class="job">${esc(m.job)}</span>` : ''}</span>${m.position ? ptag(m.position) : '<span class="xs dim">—</span>'}</span>`).join('');
  const conv = `<div class="card conv">
    <div class="row convh" style="padding:0 0 10px;border-bottom:1px solid var(--line)"><a class="narrow quiet s back" href="#/rooms" aria-label="Back to the rooms">‹</a>
      <div class="sp clip"><b class="clip" style="display:block" title="${esc(room.question)}">${esc(room.question)}</b><span class="xs mut">${esc(room.project)} · opened by ${esc(room.opened_by)} · ${room.how === 'vote' ? 'vote (members do not see each other)' : plan ? 'planning · editor: ' + esc(room.editor) : 'discussion'}${room.allow_new ? ' · they may add options' : ''}</span></div>
      ${open ? '<span class="tag ok" id="roomlive" title="Refreshes by itself">● live</span>' : ''}${roomTag(room)}</div>
    <div class="rmeta"><div class="rchips">${chips}</div><div class="row wrap" style="gap:4px;margin-top:6px">${plan ? `<div class="seg"><a ${act('planTab', room.id, 'chat')} class="${tab === 'chat' ? 'on' : ''}">💬 Discussion</a><a ${act('planTab', room.id, 'plan')} class="${tab === 'plan' ? 'on' : ''}">📄 ${!open && room.report ? 'Report' : 'Plan' + (room.draft_version ? ' v' + room.draft_version : '')}</a></div>`
      : `<span class="xs mut">Options: ${room.options.length ? room.options.map(esc).join(' · ') : 'none yet - the members bring them'}</span>`}<span class="sp"></span>${btns}</div></div>
    <div class="tl2" id="roomlog" style="flex:1 1 auto;min-height:0;overflow-y:auto">${tab === 'plan' ? planPane(room) : (tl || empty('Nobody has spoken yet', open ? 'The first person has the floor.' : '')) + thinking + result}</div>
    ${open ? `<div class="compose2"><textarea id="roomtxt" placeholder="Join the discussion: whoever speaks next reads it and answers you" title="Enter sends, Shift+Enter makes a new line"></textarea><button class="pri" ${act('roomChatSend', room.id)}>Send</button></div>` : ''}</div>`;
  return head + grid(conv);
};
AFTER.rooms = () => {
  chatSize($('commgrid'));
  const log = $('roomlog'), id = (S.rooms.cur || {}).id || '';
  if (log) { const k = 'roomscroll:' + id, keep = S.rooms[k]; log.scrollTop = keep != null && !keep.bottom ? keep.top : log.scrollHeight;       // stay where the reader is; follow only at the bottom
    log.onscroll = () => { S.rooms[k] = {top: log.scrollTop, bottom: log.scrollHeight - log.scrollTop - log.clientHeight < 40}; }; }
  const t = $('roomtxt'); S.rooms.drafts = S.rooms.drafts || {};
  if (t) { t.value = S.rooms.drafts[id] || ''; const grow = () => { if (!t.value) { t.style.height = ''; return; } t.style.height = 'auto'; t.style.height = Math.min(220, Math.max(44, t.scrollHeight + 2)) + 'px'; };
    t.oninput = () => { S.rooms.drafts[id] = t.value; grow(); }; grow();
    t.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !matchMedia('(max-width:760px)').matches) { e.preventDefault(); ACT.roomChatSend({a0: id}); } }; }
  clearInterval(S.rooms.timer);
  S.rooms.timer = setInterval(() => { if (S.route[0] !== 'rooms') return clearInterval(S.rooms.timer); if (!typing() && !$('modal').innerHTML && $('roomlive')) render(true); }, 4000);
};
Object.assign(ACT, {
  roomsShow(d) { S.rooms.show = d.a0; go('#/rooms'); },
  planTab(d) { S.rooms['tab:' + d.a0] = d.a1; S.rooms['roomscroll:' + d.a0] = null; render(true); },
  planCopy() { const r = S.rooms.cur; if (!r) return; navigator.clipboard.writeText(r.status !== 'open' && r.report ? r.report : r.draft).then(() => toast('Copied'), () => toast('The browser did not allow copying', true)); },
  planSave() { const r = S.rooms.cur; if (!r) return; const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([r.status !== 'open' && r.report ? r.report : r.draft], {type: 'text/markdown'})); a.download = `plan-${r.id}.md`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); },
  async roomChatSend(d) { if (!val('roomtxt')) return; if (done(await post('/rooms/' + d.a0 + '/say', {text: val('roomtxt')}), 'Sent to the room')) { (S.rooms.drafts || {})[d.a0] = ''; S.rooms['roomscroll:' + d.a0] = null; render(true); } },
  roomGo(d) { S.rooms['roomscroll:' + d.a0] = null; go('#/rooms/' + encodeURIComponent(d.a0)); },
  async roomArchive(d) { if (done(await post('/rooms/' + d.a0 + '/' + d.a1, {}), d.a1 === 'archive' ? 'Room hidden from the chat list' : 'Room is visible again')) go('#/rooms'); },
  roomMore: d => modal(`<h2>${d.a1 ? 'More circles' : 'Continue the discussion'}</h2><p class="mut sm">${d.a1 ? 'The room gets more circles before it counts.' : 'The room opens again: everyone speaks once more, reading everything said so far, and then it is counted again.'}</p>
    <label>How many more circles</label><select id="rmc">${[1, 2, 3].map(n => `<option>${n}</option>`).join('')}</select>
    <label>Your message to them (optional)</label><textarea id="rmt" style="min-height:80px" placeholder="e.g. Think about the cost for the next two years too."></textarea>
    <label class="row sm" style="gap:6px;margin-top:12px;font-weight:400"><input type="checkbox" id="rmn" style="width:auto" ${d.a2 ? 'checked' : ''}> Let them put their own options on the table</label>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('roomMoreSend', d.a0)}>Continue</button></div>`),
  async roomMoreSend(d) { if (done(await post('/rooms/' + d.a0 + '/continue', {circles: +val('rmc') || 1, text: val('rmt'), allow_new: !!($('rmn') && $('rmn').checked)}), 'The discussion goes on')) { modal(''); S.rooms.show = 'active'; go('#/rooms/' + encodeURIComponent(d.a0)); } },
  roomOpts: d => modal(`<h2>Options</h2><p class="mut sm">Put new options on the table yourself, or let the members bring their own. If the room is discussing, they are told at once.</p>
    <label>Add options (one per line)</label><textarea id="rot" style="min-height:80px" placeholder="OpenHands&#10;OpenAI Codex"></textarea>
    <label class="row sm" style="gap:6px;margin-top:12px;font-weight:400"><input type="checkbox" id="ron" style="width:auto" ${d.a1 ? 'checked' : ''}> Let them put their own options on the table</label>
    <div class="row" style="margin-top:20px"><span class="sp"></span><button data-mclose="1">Cancel</button><button class="pri" ${act('roomOptsSend', d.a0)}>Save</button></div>`),
  async roomOptsSend(d) { if (done(await post('/rooms/' + d.a0 + '/options', {allow_new: $('ron').checked, add: val('rot').split('\n').map(x => x.trim()).filter(Boolean)}), 'Options saved')) { modal(''); render(true); } },
  async roomDelete(d) { if (!confirm('Delete this room and everything said in it? Its decision stays in the project\'s memory.')) return; if (done(await post('/rooms/' + d.a0 + '/delete', {}), 'The room is deleted')) go('#/rooms'); },
});
