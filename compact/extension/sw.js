// EmaraAI Hub Connector — service worker.
// Long-polls the local hub for commands and runs them in chatgpt.com tabs of THIS Chrome profile.
// It talks only to the hub on 127.0.0.1 and to chatgpt.com. No data is sent anywhere else.

let HUB = "http://127.0.0.1:8797";
let looping = false;
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------- functions injected into the ChatGPT page
async function pageTypeSend(text, plugin, sel, files) {
  const valid = () => { if (sel.execution_deadline && Date.now() >= sel.execution_deadline) throw new Error("delivery expired before page action"); };
  valid();
  // Chrome slows timers in a hidden tab (down to one per minute after 5 minutes). A message channel is not a timer,
  // so waiting through it keeps the hub's typing fast in background tabs.
  const sleep = ms => !document.hidden ? new Promise(r => setTimeout(r, ms)) : new Promise(r => { const end = performance.now() + ms, ch = new MessageChannel();
    ch.port1.onmessage = () => performance.now() >= end ? r() : ch.port2.postMessage(0); ch.port2.postMessage(0); });
  const q = s => { valid(); try { return document.querySelector(s); } catch (e) { return null; } };
  // the Work composer has no [data-chatgpt-composer]: its attachments are looked for in the form
  const REMOVE = '[data-chatgpt-composer] button[aria-label^="Remove"], form button[aria-label^="Remove"]';
  const T = e => ((e && (e.innerText || e.getAttribute("aria-label"))) || "").replace(/[\ue000-\uf8ff]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const PO = {bubbles: true, cancelable: true, pointerType: "mouse", button: 0};
  const press = e => { valid(); for (const [C, t] of [[PointerEvent, "pointerdown"], [MouseEvent, "mousedown"], [PointerEvent, "pointerup"], [MouseEvent, "mouseup"]]) e.dispatchEvent(new C(t, PO)); e.click(); };
  const hover = e => { for (const t of ["pointerenter", "pointermove"]) e.dispatchEvent(new PointerEvent(t, PO)); e.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", code: "ArrowRight", bubbles: true})); };
  const menus = () => [...document.querySelectorAll('[role="menu"]')];
  const items = r => menus().flatMap(m => [...m.querySelectorAll(r)]);
  const closeMenus = async () => {
    for (let k = 0; k < 4 && menus().length; k++) {
      const m = menus().pop();
      m.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", bubbles: true, cancelable: true})); await sleep(300);
    }
    if (menus().length) { document.body.dispatchEvent(new PointerEvent("pointerdown", PO)); await sleep(300); }
  };
  const T0 = performance.now(), took = {}, lap = k => { took[k] = Math.round(performance.now() - T0); };
  let mode = null;
  if (sel.execution_surface === "claude_code") {
    if (location.hostname !== "claude.ai" || !/^\/code(?:\/|$)/.test(location.pathname)) return {ok:false,error:"Claude Code page was not opened"};
    mode = "code";
  }
  if (sel.mode) {
    const want = String((sel.mode_labels || {})[sel.mode] || sel.mode).toLowerCase();
    const sw = () => { try { return [...document.querySelectorAll(sel.mode_switch || '[role="group"][aria-label="Composer mode"] button, [role="radiogroup"][aria-label="Mode"] [role="radio"]')]; } catch (e) { return []; } };
    const on = b => !!b && (b.getAttribute("aria-pressed") || b.getAttribute("aria-checked")) === "true";
    const mine = () => sw().find(b => T(b) === want);
    // the switch is drawn late and starts disabled. A page that has none (a project page, a temporary chat) is not waited for long
    // when Chat is wanted: Chat is what such a page is.
    for (let i = 0; i < 14 && (!mine() || mine().disabled); i++) { if (!sw().length && !sel.mode_required && i >= 3) break; await sleep(500); }
    if (!mine()) mode = "absent";
    else {
      for (let tries = 0; tries < 2 && !on(mine()); tries++) { mine().click(); for (let i = 0; i < 12 && mine() && !on(mine()); i++) await sleep(250); }
      mode = on(mine()) ? sel.mode : "failed";
      await sleep(900);
    }
    if (mode !== sel.mode && sel.mode_required) return {ok: false, error: "the '" + sel.mode + "' mode could not be selected on this page (switch " + mode + ")", mode};
  }
  lap("mode");
  let box; for (let i = 0; i < 80 && !(box = q(sel.composer)); i++) await sleep(500);
  if (!box) return {ok: false, error: "composer not found (is ChatGPT signed in?)"};
  if (sel.execution_surface === "claude_code" && sel.code_bind_repository) {
    const pickers = () => [...document.querySelectorAll('button[role="combobox"]')].filter(e => !e.getAttribute('aria-label') && e.offsetParent);
    const pick = async (index, name) => {
      const button = pickers()[index];
      if (!button || button.disabled) return false;
      press(button);
      let option;
      for (let i=0;i<30 && !option;i++) { await sleep(200); option = [...document.querySelectorAll('[role="option"]')].find(e => e.offsetParent && T(e) === name.toLowerCase()); }
      if (!option) return false;
      press(option); await sleep(500);
      return true;
    };
    if (!sel.code_repository || !await pick(0, sel.code_repository)) return {ok:false,error:"The requested Claude Code repository could not be selected"};
    if (!await pick(1, sel.code_branch || "main")) return {ok:false,error:"The requested Claude Code branch could not be selected"};
  }
  lap("composer");
  // While ChatGPT is answering there is no Send button, only Stop. Typing now would leave the text in the box: wait for the
  // answer to end (up to 30 s), and if it is still running say so - the hub tries again later, nothing is lost.
  const busy = () => !!(sel.stop_button && q(sel.stop_button));
  for (let i = 0; i < 60 && busy(); i++) await sleep(500);
  if (busy()) return {ok: false, busy: true, error: "the chat is still answering"};
  box.focus();
  const ins = t => document.execCommand("insertText", false, t);
  if ((box.innerText || "").trim()) {     // text left by an attempt that did not finish must not be sent twice
    document.execCommand("selectAll"); document.execCommand("delete"); await sleep(200); box.focus();
  }
  // A picture or file that is already sitting in the message box was not put there by the hub (ChatGPT keeps an unsent
  // attachment as a draft, for a chat or for the "new chat" page). It must not travel with the hub's prompt: take it out.
  let cleared = 0;
  for (let k = 0; k < 8; k++) {
    const rm = document.querySelector(REMOVE);
    if (!rm) break;
    rm.click(); cleared++; await sleep(300);
  }
  if (cleared) { box = q(sel.composer) || box; box.focus(); }
  let mentioned = false, connector = null;
  if (sel.connector) {
    const plus = q(sel.attach_button || '[data-testid="chat-input-attach"]');
    if (plus) {
      press(plus);
      let c = null; for (let i = 0; i < 10 && !(c = items('[role^="menuitem"]').find(e => T(e).startsWith("connectors"))); i++) await sleep(250);
      if (c) {
        press(c); hover(c);
        let it = null; const name = String(sel.connector).toLowerCase();
        for (let i = 0; i < 10 && !it; i++) { await sleep(250); it = items('[role="menuitemcheckbox"]').find(e => T(e) === name); }
        if (it && it.getAttribute("aria-checked") !== "true") { press(it); await sleep(600); }
        connector = !!it;
      }
      await closeMenus();
      box = q(sel.composer) || box; box.focus();
    }
  }
  const pluginsMenu = plugin ? document.querySelector('button[aria-label="Plugins"][aria-haspopup="menu"]') : null;
  if (pluginsMenu) {
    press(pluginsMenu);
    let it = null;
    for (let i = 0; i < 14 && !it; i++) { await sleep(300); it = items('[role="menuitemcheckbox"]').find(e => T(e) === plugin.toLowerCase()); }
    if (it) { if (it.getAttribute("aria-checked") !== "true") { press(it); await sleep(600); } mentioned = true; }
    await closeMenus();
    box = q(sel.composer) || box; box.focus();
  } else if (plugin) {
    const kw = plugin.split(" ").pop();          // "EmaraAI Agent" -> "@Agent" (typing "@EmaraAI" could match another plugin)
    ins("@" + kw);
    let opt = null;
    for (let i = 0; i < 16 && !opt; i++) {
      await sleep(400);
      const c = [...document.querySelectorAll("[role=option],[role=menuitem],[cmdk-item],li,button")]
        .filter(e => (e.innerText || "").trim().startsWith(plugin) && e.offsetParent && !box.contains(e));
      if (c.length) opt = c.sort((a, b) => a.innerText.length - b.innerText.length)[0];
    }
    if (opt) { opt.click(); mentioned = true; await sleep(500); box.focus(); ins(" "); }
    else { for (let i = 0; i < kw.length + 1; i++) document.execCommand("delete"); }
  }
  lap("plugin");
  let attached = 0;         // files of the message (a UI picture, a document) go into the chat so the model sees them
  if (files && files.length) {
    let inputs = [...document.querySelectorAll("[data-chatgpt-composer] input[type=file]")];
    if (!inputs.length) inputs = [...document.querySelectorAll("form input[type=file]")];
    const inp = inputs.find(i => !i.accept) || inputs[0];
    if (inp) {
      const dt = new DataTransfer();
      for (const f of files) dt.items.add(new File([Uint8Array.from(atob(f.data), c => c.charCodeAt(0))], f.name, {type: f.type || "application/octet-stream"}));
      inp.files = dt.files; inp.dispatchEvent(new Event("change", {bubbles: true}));
      const shown = () => document.querySelectorAll(REMOVE).length;
      for (let i = 0; i < 60 && shown() < files.length; i++) await sleep(500);
      attached = shown(); await sleep(1500); box.focus();
    }
  }
  ins(text);
  await sleep(sel.settle_ms || 1200);
  let thinking = null, model = null;
  // The composer's model menu holds the models and the effort (ChatGPT: a slider - Instant / Medium / High in Chat, None ... Persistent
  // in Work; claude.ai: an Effort submenu). Set both to what this agent should use.
  const WANT = String(sel.effort_label || {low: "instant", medium: "medium", high: "high"}[sel.effort || ""] || "").toLowerCase();
  const MODEL = String(sel.model || "").toLowerCase();
  const picker = () => q(sel.model_picker || 'button[aria-label="Select ChatGPT model"], [data-testid="model-selector-dropdown"]');
  if ((WANT || MODEL) && picker()) {
    const level = () => { const s = items('[role="status"]')[0]; return s ? (s.textContent || "").split(",")[0].trim().toLowerCase() : ""; };
    const open = async () => {
      if (menus().length) return true;
      picker().dispatchEvent(new PointerEvent("pointerdown", PO)); picker().dispatchEvent(new PointerEvent("pointerup", PO));
      for (let i = 0; i < 8 && !menus().length; i++) await sleep(150);
      if (!menus().length) { picker().click(); for (let i = 0; i < 8 && !menus().length; i++) await sleep(150); }
      return menus().length > 0;
    };
    if (!MODEL && WANT && T(picker()) === WANT) thinking = WANT;
    else if (await open()) {
      if (MODEL) {
        const radios = items('[role="menuitemradio"]');
        const r = radios.find(e => T(e) === MODEL) || radios.find(e => T(e).startsWith(MODEL));
        if (r) { if (r.getAttribute("aria-checked") !== "true") { press(r); await sleep(800); } model = T(r).slice(0, 40); }
        else model = "not offered: " + radios.map(e => T(e).slice(0, 24)).join(", ").slice(0, 200);
      }
      if (WANT && await open()) {
        const power = items('[role="menuitem"][aria-keyshortcuts*="ArrowLeft"]')[0];
        const sub = items('[role="menuitem"][aria-haspopup="menu"]').find(e => T(e).startsWith("effort"));
        if (power && level()) {
          const order = (sel.effort_order || ["instant", "medium", "high"]).map(x => String(x).toLowerCase());
          const rank = x => order.indexOf(x);
          for (let i = 0; i < 12 && level() !== WANT && rank(level()) >= 0 && rank(WANT) >= 0; i++) {
            const before = level(), right = rank(WANT) > rank(before), k = right ? "ArrowRight" : "ArrowLeft";
            power.focus(); power.dispatchEvent(new KeyboardEvent("keydown", {key: k, code: k, bubbles: true, cancelable: true}));
            await sleep(350);
            const now = level();
            if (now === before) break;                                              // the end of this model's slider
            if (right ? rank(now) > rank(WANT) : rank(now) < rank(WANT)) break;      // this model has no such step: the nearest one stays
          }
          thinking = level() || null;
        } else if (sub) {
          press(sub); hover(sub);
          let it = null;
          for (let i = 0; i < 8 && !it; i++) { await sleep(250); it = items('[role^="menuitem"]').find(e => e !== sub && !e.getAttribute("aria-haspopup") && (T(e) === WANT || T(e).startsWith(WANT + " "))); }
          if (it) { press(it); await sleep(600); thinking = WANT; }
        }
      }
      await closeMenus();
      const again = q(sel.composer); if (again) again.focus();
    }
  } else if (sel.think && sel.think.length) {      // older ChatGPT: a "Think" toggle in the composer
    // the toggle shows its label only when there is room (a selected plugin or an attachment hides it), so fall back
    // to "the one toggle button of the composer"
    const label = b => ((b.innerText || "") + " " + (b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || "")).trim().toLowerCase();
    const find = () => {      // looked up again every time: ChatGPT replaces the button when it re-renders the composer
      const toggles = [...document.querySelectorAll("[data-chatgpt-composer] button[aria-pressed]")];
      return toggles.find(b => sel.think.some(l => label(b).includes(l.toLowerCase()))) || (toggles.length === 1 ? toggles[0] : null);
    };
    const on = () => { const b = find(); return b ? b.getAttribute("aria-pressed") === "true" : null; };
    const locked = () => { const b = find(); return !!b && (b.disabled || b.getAttribute("aria-disabled") === "true"); };
    for (let tries = 0; tries < 2 && on() === false && !locked(); tries++) { find().click(); for (let i = 0; i < 8 && on() === false; i++) await sleep(250); }
    thinking = on() === false && locked() ? "unavailable" : on();      // ChatGPT itself greys the toggle out in some chats
  }
  lap("effort");
  // ChatGPT redraws the composer while a page is still settling (and after its menus close): the text must be in the box
  // that is on screen NOW, or there is nothing to send
  const probe = text.replace(/\s+/g, " ").trim().slice(0, 24);
  for (let k = 0; k < 3 && probe; k++) {
    const live = q(sel.composer);
    if (!live) { await sleep(1500); continue; }
    if ((live.innerText || "").replace(/\s+/g, " ").includes(probe)) { box = live; break; }
    box = live; box.focus();
    if ((box.innerText || "").trim()) { document.execCommand("selectAll"); document.execCommand("delete"); await sleep(200); box.focus(); }
    ins(text); await sleep(sel.settle_ms || 1200);
  }
  let btn; for (let i = 0; i < (attached ? 300 : 40) && !(btn = q(sel.send_button)); i++) await sleep(300);   // uploads keep Send disabled
  box = q(sel.composer);
  if (!box || !box.isConnected || !(box.innerText || "").replace(/\s+/g, " ").includes(text.replace(/\s+/g, " ").trim()))
    return {ok: false, error: "message was not present in the live composer before submission", submission_attempted: false};
  // Send, then make sure the text really left the box. No button (ChatGPT changes it now and then) or a click that did
  // nothing: press Enter in the composer instead. Text that still stays is removed, so a later try cannot send it twice.
  const empty = () => !(box.innerText || "").trim();
  const enter = () => { box.focus(); for (const t of ["keydown", "keypress", "keyup"]) box.dispatchEvent(new KeyboardEvent(t, {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true})); };
  // a page in a background tab reacts slowly (some sites only update when they repaint): give it much longer there
  const gone = async () => { for (let i = 0; i < (document.hidden ? 150 : 20); i++) { if (empty() || busy()) return true; await sleep(300); } return false; };
  let via = btn ? "button" : "enter";
  if (btn) btn.click(); else enter();
  let sent = await gone();
  if (!sent && btn) { via = "enter"; enter(); sent = await gone(); }
  if (!sent) {
    box.focus(); document.execCommand("selectAll"); document.execCommand("delete");
    return {ok: false, error: btn ? "the message stayed in the box after Send" : "send button not found", mentioned, thinking, submission_attempted: true};
  }
  lap("sent");
  return {ok: true, mentioned, thinking, attached, via, cleared, mode, model, connector, took};
}

function pageObserve(c) {
  const q = s => { try { return document.querySelector(s); } catch (e) { return null; } };
  const qa = s => { try { return Array.from(document.querySelectorAll(s)); } catch (e) { return []; } };
  let approved = "";
  if (c.approve) {
    const btns = qa("button");
    for (const label of c.approve.labels) {
      const b = btns.find(x => (x.innerText || "").trim().toLowerCase().startsWith(label.toLowerCase()) && !x.disabled);
      if (!b) continue;
      let box = b, text = "";
      for (let i = 0; i < 6 && box; i++) { box = box.parentElement; text = box ? (box.innerText || "") : ""; if (text.length > 60) break; }
      if (c.approve.scope.some(s => text.includes(s))) { b.click(); approved = label; break; }
    }
  }
  // ChatGPT's newer conversation markup: one [data-turn-key] block per exchange, holding the user's bubble and the reply
  const split = () => Array.from(document.querySelectorAll("[data-turn-key]")).map(el => {
    const ub = el.querySelector("[data-user-message-bubble]");
    const u = ub ? (ub.innerText || "").trim() : "", full = (el.innerText || "").trim();
    return {u, a: (u && full.includes(u) ? full.replace(u, "") : full).trim()};
  });
  let aText = qa(c.assistant_turn).map(el => el.innerText || ""), uText = qa(c.user_turn).map(el => el.innerText || "");
  if (!aText.length && !uText.length) { const t = split(); aText = t.map(x => x.a).filter(Boolean); uText = t.map(x => x.u).filter(Boolean); }
  const assistants = aText, users = uText;
  const chars = aText.concat(uText).reduce((n, t) => n + t.length, 0);
  const tail = aText.length ? aText[aText.length - 1] : "";
  const err = qa(c.error_box).map(e => e.innerText || "").join(" | ").slice(0, 400);
  const scan = (err + " " + tail.slice(-600)).toLowerCase();
  const lastUser = users.length ? users[users.length - 1].replace(/\s+/g, " ").trim() : "";
  // "You've hit your limit ... resets at 3 PM": shown as an alert, next to the message box, or as the whole (short) last reply.
  // The conversation itself is not searched: agents talk about limits too.
  let usage_text = "";
  const up = c.usage_patterns || [];
  if (up.length) {
    let f = q(c.composer), near = "";
    for (let i = 0; i < 8 && f && f.parentElement && !f.parentElement.querySelector(c.assistant_turn + ", [data-turn-key]"); i++) f = f.parentElement;
    if (f) near = (f.innerText || "").slice(0, 1500);
    const hay = (err + " | " + near + " | " + (tail.length < 500 ? tail : "")).replace(/[\u2018\u2019]/g, "'").replace(/\s+/g, " "), low = hay.toLowerCase();
    const p = up.find(x => low.includes(x));
    if (p) { const i = low.indexOf(p); usage_text = hay.slice(Math.max(0, i - 80), i + 240).trim(); }
  }
  return {usage_hit: !!usage_text, usage_text, generating: !!q(c.stop_button) || !!(c.generating_selector && q(c.generating_selector)), composer: !!q(c.composer), assistant_turns: assistants.length, user_turns: users.length, chars, last_user: lastUser,
          error_text: err, limit_hit: c.limit_patterns.some(p => scan.includes(p)), error_hit: c.error_patterns.some(p => scan.includes(p)),
          tail: tail.slice(-300), url: location.href, approved};
}

// claude.ai: add the hub's MCP servers as custom connectors, through the page's own dialog (+ > Connectors > Add connector > Add custom connector)
async function pageClaudeConnector(want) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const T = e => ((e && (e.innerText || e.getAttribute("aria-label"))) || "").replace(/[\ue000-\uf8ff]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const PO = {bubbles: true, cancelable: true, pointerType: "mouse", button: 0};
  const press = e => { for (const [C, t] of [[PointerEvent, "pointerdown"], [MouseEvent, "mousedown"], [PointerEvent, "pointerup"], [MouseEvent, "mouseup"]]) e.dispatchEvent(new C(t, PO)); e.click(); };
  const hover = e => { for (const t of ["pointerenter", "pointermove"]) e.dispatchEvent(new PointerEvent(t, PO)); e.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", code: "ArrowRight", bubbles: true})); };
  const menus = () => [...document.querySelectorAll('[role="menu"]')];
  const item = t => [...document.querySelectorAll('[role^="menuitem"]')].find(e => T(e).startsWith(t));
  const until = async (fn, n = 12, ms = 250) => { let v; for (let i = 0; i < n && !(v = fn()); i++) await sleep(ms); return v; };
  const closeMenus = async () => {
    for (let k = 0; k < 4 && menus().length; k++) { menus().pop().dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", bubbles: true, cancelable: true})); await sleep(300); }
    if (menus().length) { document.body.dispatchEvent(new PointerEvent("pointerdown", PO)); await sleep(300); }
  };
  const openConnectors = async () => {
    if (item("add connector")) return true;
    await closeMenus();
    const plus = await until(() => document.querySelector('[data-testid="chat-input-attach"]'), 40, 500);
    if (!plus) return false;
    press(plus);
    const c = await until(() => item("connectors"));
    if (!c) return false;
    press(c); hover(c);
    return !!(await until(() => item("add connector")));
  };
  const listed = () => [...document.querySelectorAll('[role="menuitemcheckbox"]')].map(T);
  const dialog = () => document.querySelector('[role="dialog"]');
  const fill = async (el, v) => {
    el.focus(); el.select && el.select(); document.execCommand("insertText", false, v); await sleep(200);
    if (el.value !== v) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v); el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true})); }
  };
  if (!(await openConnectors())) return {ok: false, error: "The Connectors menu of claude.ai was not found (is claude.ai signed in in this Chrome profile?)", results: []};
  const results = [];
  for (const w of want) {
    const out = {name: w.name}, name = w.name.toLowerCase();
    try {
      if (!(await openConnectors())) throw new Error("the Connectors menu did not open");
      if (listed().includes(name)) { out.status = "ready"; out.installed = true; results.push(out); continue; }
      const add = item("add connector"); press(add); hover(add);
      const cust = await until(() => item("add custom connector"));
      if (!cust) throw new Error("'Add custom connector' is not offered (your Claude plan may not allow custom connectors)");
      press(cust);
      const d = await until(dialog, 20);
      if (!d) throw new Error("the 'Add custom connector' dialog did not open");
      const nameBox = d.querySelector('input[placeholder="Name"]') || d.querySelector('input:not([type=radio]):not([type=url])'), urlBox = d.querySelector('input[type="url"]');
      if (!nameBox || !urlBox) throw new Error("the dialog has no Name / MCP server URL fields");
      await fill(nameBox, w.name); await fill(urlBox, w.url);
      const GO = ["continue", "add", "add connector", "connect", "save", "done", "confirm", "create"];
      for (let step = 0; step < 4 && dialog(); step++) {
        const b = await until(() => [...dialog().querySelectorAll("button")].find(x => GO.includes(T(x)) && !x.disabled && x.getAttribute("aria-disabled") !== "true"), 12);
        if (!b) break;
        b.click(); await sleep(2200);
      }
      if (dialog()) {            // a step the hub does not know: say what the page shows, and leave nothing half-done on screen
        out.note = T(dialog()).slice(0, 300);
        const no = [...dialog().querySelectorAll("button")].find(x => ["cancel", "close"].includes(T(x)));
        if (no) no.click(); await sleep(600);
      }
      await sleep(1200);
      out.installed = (await openConnectors()) && listed().includes(name);
      out.status = out.installed ? "created" : "failed";
      if (!out.installed && !out.note) out.note = "the connector is not in the Connectors list after adding it";
    } catch (e) { out.status = "failed"; out.error = String(e && e.message || e); }
    results.push(out);
  }
  await closeMenus();
  return {ok: true, results};
}

function pageStop(selector) { const b = document.querySelector(selector); if (b) b.click(); return !!b; }
function pageHas(text) { return (document.body.innerText || "").includes(text); }

async function pageInject(CFG) {
  const C = "/backend-api/aip/connectors";
  const session = await fetch("/api/auth/session", {credentials: "include"}).then(r => r.json()).catch(() => ({}));
  if (!session || !session.accessToken) return {error: "Not signed in to ChatGPT in this Chrome profile.", results: []};
  const api = async (method, path, body) => {
    const p = path.split("?")[0];
    const res = await fetch(path, {method, credentials: "include", body: body === undefined ? undefined : JSON.stringify(body),
      headers: {"authorization": "Bearer " + session.accessToken, "content-type": "application/json", "x-openai-target-path": p, "x-openai-target-route": p}});
    const text = await res.text(); let data; try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
    if (res.status >= 400) throw new Error(method + " " + p + " -> " + res.status + " " + (typeof data === "string" ? data : JSON.stringify(data)).slice(0, 300));
    return data;
  };
  const list = () => api("POST", C + "/list_accessible?skip_actions=true&external_logos=true&skip_directory=true", {principals: []}).then(r => (r && r.connectors) || []);
  const links = () => api("POST", C + "/links/list_accessible", {principals: [], link_refresh_strategy: "NONE"}).then(r => (r && r.links) || []);
  const results = [];
  const existing = await list();
  for (const want of CFG.connectors) {
    const out = {name: want.name};
    try {
      let conn = existing.find(c => c && c.base_url === want.url);
      if (!conn) {
        const stale = existing.find(c => c && c.connector_type === "MCP" && c.name === want.name && c.base_url !== want.url);
        if (stale) { await api("DELETE", C + "/" + stale.id); out.replaced_old_address = true; }
        conn = await api("POST", C + "/mcp", {name: want.name, mcp_url: want.url, description: "", logo_url: null,
                                              auth_request: {supported_auth: [{type: "NONE"}]}}).then(r => r.connector || r);
        out.status = "created";
      } else { out.status = "ready"; }
      out.connector_id = conn.id;
      const l = (await links()).find(x => x.connector_id === conn.id);
      out.installed = !!l;
      if (l) { try { out.tools = (await api("POST", C + "/mcp/refresh_actions", {link_id: l.id}).then(r => (r && r.actions) || [])).length; } catch (e) { out.note = String(e.message).slice(0, 160); } }
    } catch (e) { out.status = "failed"; out.error = String(e && e.message || e); }
    results.push(out);
  }
  return {results};
}

// Presses "+" next to a plugin on ChatGPT → Plugins → Personal, and the confirm button of ChatGPT's own dialog for that plugin.
async function pageInstall(names) {
  // Chrome slows timers in a hidden tab (down to one per minute after 5 minutes). A message channel is not a timer,
  // so waiting through it keeps the hub's typing fast in background tabs.
  const sleep = ms => !document.hidden ? new Promise(r => setTimeout(r, ms)) : new Promise(r => { const end = performance.now() + ms, ch = new MessageChannel();
    ch.port1.onmessage = () => performance.now() >= end ? r() : ch.port2.postMessage(0); ch.port2.postMessage(0); });
  const out = {};
  for (let i = 0; i < 30 && !(document.body.innerText || "").includes("Created by you"); i++) await sleep(500);
  for (const name of names) {
    const label = [...document.querySelectorAll("div,span,p,h3,h4")].find(e => e.children.length === 0 && (e.innerText || "").trim() === name);
    if (!label) { out[name] = "not listed"; continue; }
    let row = label, btn = null;
    for (let i = 0; i < 6 && row && !btn; i++) { row = row.parentElement; const bs = row ? [...row.querySelectorAll("button")] : []; if (bs.length === 1) btn = bs[0]; else if (bs.length > 1) break; }
    if (!btn) { out[name] = "no button"; continue; }
    btn.click(); await sleep(2500);
    const confirm = [...document.querySelectorAll("button")].find(b => (b.innerText || "").trim() === "Connect " + name);
    if (confirm) { confirm.click(); await sleep(2500); }
    out[name] = "clicked";
  }
  return out;
}

// ---------------------------------------------------------------- helpers
const run = async (tabId, func, args, world = "ISOLATED") => {
  const [r] = await chrome.scripting.executeScript({target: {tabId}, func, args, world});
  return r ? r.result : null;
};
const waitLoaded = async (tabId, ms = 45000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { const t = await chrome.tabs.get(tabId).catch(() => null); if (!t) throw new Error("tab closed"); if (t.status === "complete") return t; await sleep(400); }
  return chrome.tabs.get(tabId);
};
// a brand-new chat first has a temporary address ("local-chatgpt:..."); it is not an identity, so it counts as "unknown"
const providerURL = (value, host) => { try { const u = new URL(value); return u.protocol === 'https:' && u.hostname === host && !u.username && !u.password; } catch (e) { return false; } };
const chatIdOf = url => { if (!providerURL(url, 'chatgpt.com')) return ''; const id = (new URL(url).pathname.match(/\/c\/([0-9a-zA-Z%:-]{8,})/) || [])[1] || ""; return /^local-chatgpt/i.test(id) ? "" : id; };
async function findTab(ref) {
  // a chat is identified by the id in its address; the tab id is only a shortcut. A tab that was navigated
  // somewhere else (or a chat that moved into a ChatGPT Project and got a new address) must not be mistaken for it.
  const want = chatIdOf(ref && ref.url);
  if (ref && ref.tab_id) {
    const t = await chrome.tabs.get(Number(ref.tab_id)).catch(() => null);
    if (t && providerURL(t.url, "chatgpt.com") && (!want || chatIdOf(t.url) === want)) return t;
  }
  if (want) {
    const t = (await chrome.tabs.query({url: "https://chatgpt.com/*"})).find(x => chatIdOf(x.url) === want);
    if (t) return t;
  }
  return null;
}
async function chatgptTab(url) {
  const tabs = await chrome.tabs.query({url: "https://chatgpt.com/*"});
  if (!url && tabs.length) return tabs[0];
  const t = await chrome.tabs.create({url: url || "https://chatgpt.com/", active: false});
  await waitLoaded(t.id); await sleep(1500);
  return t;
}

// ---------------------------------------------------------------- ChatGPT Projects, chat names, tab groups
async function pageProject(name, memory) {
  // Chrome slows timers in a hidden tab (down to one per minute after 5 minutes). A message channel is not a timer,
  // so waiting through it keeps the hub's typing fast in background tabs.
  const sleep = ms => !document.hidden ? new Promise(r => setTimeout(r, ms)) : new Promise(r => { const end = performance.now() + ms, ch = new MessageChannel();
    ch.port1.onmessage = () => performance.now() >= end ? r() : ch.port2.postMessage(0); ch.port2.postMessage(0); });
  const row = () => [...document.querySelectorAll("[data-app-action-sidebar-project-row]")].find(e => e.getAttribute("data-app-action-sidebar-project-label") === name);
  const add = () => document.querySelector('button[aria-label="Add new project"]');
  for (let i = 0; i < 40 && !add() && !row(); i++) await sleep(500);
  let created = false;
  if (!row()) {
    if (!add()) return {ok: false, error: "the Projects section was not found in the ChatGPT sidebar (open the sidebar once)"};
    const fire = (el, t) => el.dispatchEvent(new PointerEvent(t, {bubbles: true, cancelable: true, pointerType: "mouse", button: 0}));
    const box = () => document.querySelector('[role=dialog] input[name="project-name"]');
    add().click();
    let inp; for (let i = 0; i < 24 && !(inp = box()); i++) await sleep(250);
    if (!inp) { fire(add(), "pointerdown"); fire(add(), "pointerup"); add().click(); for (let i = 0; i < 24 && !(inp = box()); i++) await sleep(250); }
    if (!inp) return {ok: false, error: "the Create project box did not open"};
    inp.focus(); document.execCommand("insertText", false, name); await sleep(400);
    if (memory === "project_only") {
      // ChatGPT asks once, here, whether the project shares memory with the rest of the account. It cannot be changed later,
      // so the project is only created when "Project-only memory" is really selected.
      const dialog = inp.closest("[role=dialog]");
      const pick = () => [...dialog.querySelectorAll("button[aria-haspopup=menu]")].find(b => /memory/i.test(b.innerText || ""));
      const only = () => /project.only/i.test((pick() || {}).innerText || "");
      if (!pick()) return {ok: false, error: "the memory choice was not found in the Create project box"};
      for (let tries = 0; tries < 3 && !only(); tries++) {
        fire(pick(), "pointerdown"); fire(pick(), "pointerup");
        let item; for (let i = 0; i < 12 && !(item = [...document.querySelectorAll("[role=menuitemradio],[role=menuitem]")].find(x => /project.only/i.test(x.innerText || ""))); i++) await sleep(250);
        if (!item) { pick().click(); await sleep(500); item = [...document.querySelectorAll("[role=menuitemradio],[role=menuitem]")].find(x => /project.only/i.test(x.innerText || "")); }
        if (item) { item.click(); await sleep(600); }
      }
      if (!only()) {
        const close = [...dialog.querySelectorAll("button")].find(b => /close/i.test(b.innerText || b.getAttribute("aria-label") || "")); if (close) close.click();
        return {ok: false, error: "Project-only memory could not be selected, so the project was not created"};
      }
    }
    const btn = [...inp.closest("[role=dialog]").querySelectorAll("button")].find(b => b.type === "submit");
    if (!btn || btn.disabled) return {ok: false, error: "the Create project button is not available"};
    btn.click(); created = true;
    for (let i = 0; i < 40 && !row(); i++) await sleep(500);
  }
  const id = row() ? row().getAttribute("data-app-action-sidebar-project-id") : (location.pathname.match(/g-p-[a-z0-9]+/) || [])[0];
  return id ? {ok: true, id, created, memory: created ? (memory === "project_only" ? "project_only" : "default") : "unchanged"} : {ok: false, error: "the project did not appear in the sidebar"};
}

async function pageOrganize(chatId, title, project) {
  // Chrome slows timers in a hidden tab (down to one per minute after 5 minutes). A message channel is not a timer,
  // so waiting through it keeps the hub's typing fast in background tabs.
  const sleep = ms => !document.hidden ? new Promise(r => setTimeout(r, ms)) : new Promise(r => { const end = performance.now() + ms, ch = new MessageChannel();
    ch.port1.onmessage = () => performance.now() >= end ? r() : ch.port2.postMessage(0); ch.port2.postMessage(0); });
  const press = el => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new (t.startsWith("pointer") ? PointerEvent : MouseEvent)(t, {bubbles: true, cancelable: true, button: 0, pointerType: "mouse"})); };
  const link = () => document.querySelector('nav a[href$="/c/' + chatId + '"]');
  const items = () => [...document.querySelectorAll("[role=menuitem]")];
  const item = text => items().find(e => (e.innerText || "").trim() === text);
  const menu = async text => {
    const row = link() && link().closest("div.group"), btn = row && row.querySelector('button[aria-label="Chat actions"]');
    if (!btn) return null;
    press(btn);
    let it; for (let i = 0; i < 12 && !(it = item(text)); i++) await sleep(250);
    return it;
  };
  for (let i = 0; i < 30 && !link(); i++) await sleep(500);
  if (!link()) return {ok: false, error: "the chat is not listed in the ChatGPT sidebar yet"};
  const out = {ok: true, renamed: !!title && (link().innerText || "").trim() === title, moved: false};
  if (title && (link().innerText || "").trim() !== title) {
    const it = await menu("Rename");
    if (!it) return {ok: false, error: "Rename was not found in the chat menu"};
    press(it);
    let inp; for (let i = 0; i < 12 && !(inp = document.querySelector('input[aria-label="Chat title"]')); i++) await sleep(250);
    if (!inp) return {ok: false, error: "the rename box did not open"};
    const form = inp.closest("form,[role=dialog]"), save = form && [...form.querySelectorAll("button")].find(b => b.type === "submit");
    inp.focus(); inp.select(); document.execCommand("insertText", false, title); await sleep(300);
    if (!save || save.disabled) return {ok: false, error: "the Save button of the rename box is not available"};
    save.click();
    for (let i = 0; i < 12 && document.querySelector('input[aria-label="Chat title"]'); i++) await sleep(250);
    await sleep(500);
    out.renamed = !!link() && (link().innerText || "").trim() === title;
  }
  if (project && link() && !(link().getAttribute("href") || "").includes("/g/g-p-")) {
    const mv = await menu("Move to project");
    if (mv) {
      mv.dispatchEvent(new PointerEvent("pointermove", {bubbles: true, pointerType: "mouse"})); press(mv); await sleep(800);
      if (!item(project)) { mv.focus(); mv.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", code: "ArrowRight", keyCode: 39, bubbles: true})); await sleep(800); }
      const target = item(project);
      if (target) { press(target); for (let i = 0; i < 16 && !(link() && (link().getAttribute("href") || "").includes("/g/g-p-")); i++) await sleep(250); }
      else document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
      out.moved = !!link() && (link().getAttribute("href") || "").includes("/g/g-p-");
    }
  }
  out.title = link() ? (link().innerText || "").trim() : "";
  out.in_project = !!link() && (link().getAttribute("href") || "").includes("/g/g-p-");
  return out;
}

// every tab of a hub project sits in one Chrome tab group named after it
const groupIds = {};
async function groupTab(tabId, name) {
  // two groups, never mistaken for yours: the chats the hub talks to, and the pages agents open to do their work
  name = name === "EmaraAI agents" ? name : "EmaraAI delivery";
  if (!chrome.tabGroups) return;
  try {
    const tab = await chrome.tabs.get(Number(tabId));
    // A group belongs to ONE window. Putting a tab into a group of another window - or into a new group without naming the
    // window - makes Chrome MOVE the tab to that window. So only a group in the tab's own window is used.
    const key = name + ":" + tab.windowId;
    let gid = groupIds[key];
    const known = gid != null ? await chrome.tabGroups.get(gid).catch(() => null) : null;
    if (!known || known.title !== name || known.windowId !== tab.windowId) {
      const found = await chrome.tabGroups.query({title: name, windowId: tab.windowId});
      gid = found.length ? found[0].id : null;
    }
    if (gid != null && tab.groupId === gid) { groupIds[key] = gid; return; }
    gid = await chrome.tabs.group(gid != null ? {tabIds: [tab.id], groupId: gid} : {tabIds: [tab.id], createProperties: {windowId: tab.windowId}});
    await chrome.tabGroups.update(gid, {title: name, color: name === "EmaraAI agents" ? "green" : "blue"});
    groupIds[key] = gid;
  } catch (e) { /* grouping is cosmetic: never fail a command because of it */ }
}

// ---------------------------------------------------------------- commands from the hub
// ---------------------------------------------------------------- other chat sites (Claude, Gemini)
function pageTranscript(c, n, max) {
  // the last n turns of the conversation as plain text, oldest first: what the chat was told and what it wrote
  let els = []; try { els = Array.from(document.querySelectorAll(c.assistant_turn + ", " + c.user_turn)); } catch (e) {}
  if (els.length) return els.slice(-n).map(el => ({who: el.matches(c.user_turn) ? "user" : "assistant", text: (el.innerText || "").trim().slice(0, max)})).filter(t => t.text);
  // ChatGPT's newer conversation markup: one [data-turn-key] block per exchange, holding the user's bubble and the reply
  const split = () => Array.from(document.querySelectorAll("[data-turn-key]")).map(el => {
    const ub = el.querySelector("[data-user-message-bubble]");
    const u = ub ? (ub.innerText || "").trim() : "", full = (el.innerText || "").trim();
    return {u, a: (u && full.includes(u) ? full.replace(u, "") : full).trim()};
  });
  return split().slice(-Math.ceil(n / 2)).flatMap(x => [{who: "user", text: x.u.slice(0, max)}, {who: "assistant", text: x.a.slice(0, max)}]).filter(t => t.text);
}
function pageLastReply(selector, max) {
  let els = []; try { els = Array.from(document.querySelectorAll(selector)); } catch (e) {}
  const last = els[els.length - 1];
  return last ? (last.innerText || "").slice(-max) : "";
}
async function webTab(a) {
  // the tab the hub opened for this chat; when it was closed, the chat is opened again by its address (never a "new chat" page)
  const pattern = a.chat_pattern ? new RegExp(a.chat_pattern) : null;
  if (a.tab_id) {
    const t = await chrome.tabs.get(Number(a.tab_id)).catch(() => null);
    if (t && providerURL(t.url, a.host) && (!pattern || pattern.test(t.url))) {
      if (a.own_window && (await chrome.tabs.query({windowId: t.windowId})).length > 1)       // it sits among other tabs: give it its own window
        await withoutStealingFocus(() => chrome.windows.create({tabId: t.id, focused: false, width: 560, height: 760}).catch(() => {}));
      return chrome.tabs.get(t.id);
    }
  }
  if (a.url && pattern && providerURL(a.url, a.host) && pattern.test(a.url)) {
    const t = a.own_window ? (await withoutStealingFocus(() => chrome.windows.create({url: a.url, focused: false, width: 560, height: 760}))).tabs[0]
                           : await chrome.tabs.create({url: a.url, active: false});
    await waitLoaded(t.id); await sleep(3000);
    return chrome.tabs.get(t.id);
  }
  const e = new Error("chat tab not found"); e.missing = true; throw e;
}

// ---------------------------------------------------------------- delivery pool: shared tabs that visit one chat after another
function pageLoadFailed(click, note) {
  // ChatGPT's own error page ("Could not load this ChatGPT conversation" + Retry). With click=true its Retry is pressed.
  // `note` is shown on the page so the owner sees that the hub noticed and what it is doing.
  const text = ((document.querySelector("main") || document.body).innerText || "").slice(0, 3000) + " " + (document.body.innerText || "").slice(-1500);
  const failed = /could not load this (chatgpt )?conversation|unable to load conversation|conversation not found|something went wrong/i.test(text)
    && !document.querySelector('#prompt-textarea, [data-chatgpt-composer] [contenteditable="true"]');
  let tag = document.getElementById("emaraai-load-note");
  if (failed && note) {
    if (!tag) {
      tag = document.createElement("div"); tag.id = "emaraai-load-note";
      tag.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#1d4ed8;color:#fff;font:600 13px system-ui;padding:8px 14px;border-radius:999px;box-shadow:0 4px 16px rgba(0,0,0,.25);pointer-events:none";
      document.documentElement.appendChild(tag);
    }
    tag.textContent = note;
  } else if (tag && !failed) tag.remove();
  let pressed = false;
  if (failed && click) {
    // The Retry that belongs to the error message - not some other control that also carries the word (a narrow window
    // shows icon-only buttons with a hidden "Retry" label). Start at the error text and widen the search step by step.
    const words = /^(retry|try again|reload)$/i;
    const label = x => (x.innerText || "").trim();
    const msg = [...document.querySelectorAll("main *, body *")].find(x => !x.children.length && !/^(SCRIPT|STYLE|NOSCRIPT)$/.test(x.tagName) && (x.textContent || "").length < 160 && /could not load this|unable to load conversation|something went wrong/i.test(x.textContent || ""))
      || [...document.querySelectorAll("h1, h2, h3, p, div, span")].reverse().find(x => /could not load this|unable to load conversation/i.test(label(x)) && label(x).length < 120);
    let b = null;
    for (let box = msg ? msg.parentElement : null, up = 0; box && !b && up < 6; box = box.parentElement, up++)
      b = [...box.querySelectorAll('button, a, [role="button"]')].find(x => words.test(label(x)));
    if (!b) b = [...document.querySelectorAll('button, a, [role="button"]')].find(x => words.test(label(x)) && x.getClientRects().length);
    if (b) {
      try { b.scrollIntoView({block: "center", inline: "center"}); } catch (e) {}
      const r = b.getBoundingClientRect();
      const o = {bubbles: true, cancelable: true, pointerType: "mouse", button: 0, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2};
      b.dispatchEvent(new PointerEvent("pointerdown", o)); b.dispatchEvent(new MouseEvent("mousedown", o));
      b.dispatchEvent(new PointerEvent("pointerup", o)); b.dispatchEvent(new MouseEvent("mouseup", o));
      b.click(); pressed = true;
      window.__emaraRetry = `${b.tagName.toLowerCase()}${b.getAttribute("role") ? "[role=" + b.getAttribute("role") + "]" : ""} disabled=${!!b.disabled} visible=${b.offsetParent !== null} html=${b.outerHTML.replace(/class="[^"]*"/g, "").slice(0, 160)}`;
    } else {
      const m = document.querySelector("main") || document.body;
      window.__emaraRetry = "no control found; main html=" + m.innerHTML.replace(/class="[^"]*"/g, "").replace(/<svg[\s\S]*?<\/svg>/g, "").slice(0, 400);
    }
  }
  return failed ? (click ? (pressed ? "pressed" : "no_button") : true) : false;
}
function pageRetryDirect() {
  // The ordinary press did not make ChatGPT try again (the page ignores clicks that do not come from a real mouse).
  // Call the Retry control's own handler - the function ChatGPT attached to it - directly.
  // The Retry that belongs to the error message - not some other control that also carries the word (a narrow window
  // shows icon-only buttons with a hidden "Retry" label). Start at the error text and widen the search step by step.
  const words = /^(retry|try again|reload)$/i;
  const label = x => (x.innerText || "").trim();
  const msg = [...document.querySelectorAll("main *, body *")].find(x => !x.children.length && !/^(SCRIPT|STYLE|NOSCRIPT)$/.test(x.tagName) && (x.textContent || "").length < 160 && /could not load this|unable to load conversation|something went wrong/i.test(x.textContent || ""))
    || [...document.querySelectorAll("h1, h2, h3, p, div, span")].reverse().find(x => /could not load this|unable to load conversation/i.test(label(x)) && label(x).length < 120);
  let b = null;
  for (let box = msg ? msg.parentElement : null, up = 0; box && !b && up < 6; box = box.parentElement, up++)
    b = [...box.querySelectorAll('button, a, [role="button"]')].find(x => words.test(label(x)));
  if (!b) b = [...document.querySelectorAll('button, a, [role="button"]')].find(x => words.test(label(x)) && x.getClientRects().length);
  if (!b) return "no control";
  for (let el = b, depth = 0; el && depth < 4; el = el.parentElement, depth++) {
    const key = Object.keys(el).find(k => k.startsWith("__reactProps$"));
    const props = key ? el[key] : null;
    const fn = props && (props.onClick || props.onPointerUp || props.onMouseUp || props.onPointerDown || props.onMouseDown);
    if (typeof fn === "function") {
      try {
        fn({type: "click", button: 0, target: b, currentTarget: el, nativeEvent: {button: 0}, isTrusted: true, defaultPrevented: false,
            preventDefault() {}, stopPropagation() {}, persist() {}, isDefaultPrevented: () => false, isPropagationStopped: () => false});
        return "handler called on " + el.tagName.toLowerCase() + " (level " + depth + ")";
      } catch (e) { return "handler failed: " + String(e).slice(0, 80); }
    }
  }
  b.focus();
  for (const type of ["keydown", "keyup"]) b.dispatchEvent(new KeyboardEvent(type, {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true}));
  return "no handler found; Enter sent";
}
function pageState() {
  // what the page looked like when something could not be done: goes into the error so the cause can be read later
  const has = s => { try { return !!document.querySelector(s); } catch (e) { return false; } };
  const box = document.querySelector('#prompt-textarea, [data-chatgpt-composer] [contenteditable="true"]');
  return `[page: box=${!!box} text_in_box=${box ? (box.innerText || "").trim().length : 0} send=${has('button[data-testid="send-button"], button[aria-label="Send"], button[aria-label="Send prompt"]')}`
    + ` stop=${has('button[data-testid="stop-button"], button[aria-label^="Stop"]')} menu=${has('[role="menu"]')} dialog=${has('[role="dialog"]')} hidden=${document.hidden}`
    + ` says="${((document.querySelector("main") || document.body).innerText || "").replace(/\s+/g, " ").trim().slice(-140)}"]`;
}
function pageChatLoaded() {
  // ChatGPT draws the message box first and the conversation afterwards - and it is only then that "Could not load this
  // conversation" can still replace everything. 2 = box and messages are there, 1 = only the box so far, 0 = nothing.
  const box = !!document.querySelector('#prompt-textarea, [data-chatgpt-composer] [contenteditable="true"]');
  return !box ? 0 : document.querySelector("[data-message-author-role], [data-turn-key]") ? 2 : 1;
}
function pageHasComposer() { return !!document.querySelector('#prompt-textarea, [data-chatgpt-composer] [contenteditable="true"], form [contenteditable="true"][role="textbox"]'); }
// A delivery tab must stay alive and DRAWN: Chrome puts a background tab to sleep (timers slowed, nothing rendered, the tab
// even discarded to save memory), and ChatGPT then does not load its conversation or take the prompt reliably. So each
// delivery tab lives in a small window of its own, where it is the visible tab, and Chrome may never discard it.
const WIN = {width: 1000, height: 760};
// Whatever the hub does with its windows, YOUR window and YOUR tab stay where they were: the focused window and its active
// tab are remembered before, and put back if Chrome moved the focus (opening or restoring a window can do that).
async function withoutStealingFocus(fn) {
  const before = await chrome.windows.getLastFocused({populate: true}).catch(() => null);
  const tabBefore = before && (before.tabs || []).find(x => x.active);
  try { return await fn(); }
  finally {
    await sleep(150);
    const now = await chrome.windows.getLastFocused().catch(() => null);
    if (before && before.focused && now && now.id !== before.id) await chrome.windows.update(before.id, {focused: true}).catch(() => {});
    if (tabBefore) { const t = await chrome.tabs.get(tabBefore.id).catch(() => null); if (t && !t.active && t.windowId === before.id) await chrome.tabs.update(t.id, {active: true}).catch(() => {}); }
  }
}
// ---- ONE window for all delivery tabs. The tab being worked in is made the active tab of THAT window, so Chrome draws it;
// your own window and the tab you are looking at are never touched. Only one delivery tab is worked in at a time (inTurn).
let deliveryWin = null;
async function deliveryWindow() {
  if (deliveryWin == null) deliveryWin = ((await chrome.storage.session.get("deliveryWin").catch(() => ({}))) || {}).deliveryWin ?? null;
  if (deliveryWin != null) {
    const w = await chrome.windows.get(deliveryWin).catch(() => null);
    if (w && w.type === "normal") return w;
    deliveryWin = null;
  }
  // Session storage is cleared on extension reload. A delivery group still identifies
  // its window even while bootstrap tabs (in another hub group) share that window.
  const saved = ((await chrome.storage.local.get("deliveryWin").catch(() => ({}))) || {}).deliveryWin;
  const candidates = [];
  if (chrome.tabGroups) {
    for (const g of (await chrome.tabGroups.query({}).catch(() => [])).filter(g => g.title === "EmaraAI delivery" || g.title === "EmaraAI")) {
      const w = await chrome.windows.get(g.windowId).catch(() => null);
      const tabs = await chrome.tabs.query({windowId: g.windowId}).catch(() => []);
      const count = tabs.filter(t => t.groupId === g.id && (g.title !== "EmaraAI" || /^https:\/\/chatgpt\.com\//.test(t.url || t.pendingUrl || ""))).length;
      if (w && w.type === "normal" && count) candidates.push({w, count});
    }
  }
  candidates.sort((a, b) => Number(b.w.id === saved) - Number(a.w.id === saved) || b.count - a.count || a.w.id - b.w.id);
  if (candidates.length) { await rememberDeliveryWindow(candidates[0].w.id); return candidates[0].w; }
  return null;
}
async function rememberDeliveryWindow(id) {
  deliveryWin = id;
  await chrome.storage.session.set({deliveryWin: id}).catch(() => {});
  await chrome.storage.local.set({deliveryWin: id}).catch(() => {});
}
async function intoDeliveryWindow(t, activate = true) {
  let w = await deliveryWindow();
  if (!w) {                                               // the first delivery tab takes the window with it
    w = await withoutStealingFocus(() => chrome.windows.create({tabId: t.id, focused: false, ...WIN}));
    await rememberDeliveryWindow(w.id);
    await groupTab(t.id, "EmaraAI delivery");
  } else if (t.windowId !== w.id) {
    await chrome.tabs.move(t.id, {windowId: w.id, index: -1});
    await groupTab(t.id, "EmaraAI delivery");            // a tab loses its group when it changes window
  }
  if (w.state === "minimized")                            // a minimized window is not drawn: show it again, behind what you are doing
    await withoutStealingFocus(() => chrome.windows.update(w.id, {state: "normal", focused: false}).catch(() => {}));
  const cur = await chrome.tabs.get(t.id);
  if (cur.windowId !== w.id) throw new Error("Delivery tab could not join the shared window; sending was stopped.");
  if (activate && !cur.active) await chrome.tabs.update(t.id, {active: true}).catch(() => {});       // active in ITS window only: nothing of yours changes
  return chrome.tabs.get(t.id);
}
// A NEW chat's tab. Chrome hardly loads a background tab (it can sit on "loading" for minutes when the PC is busy or the window
// is covered), so when the delivery window exists the tab is opened THERE as its active tab and loads like a tab you look at.
// Your own window is not touched. The steps that need the tab drawn (loading, typing) take their turn with the deliveries.
async function newChatTab(url, group, steps) {
  const w = await deliveryWindow();
  if (!w) {
    const t = await chrome.tabs.create({url, active: false});
    await groupTab(t.id, group);
    return {t, out: await steps(t)};
  }
  return inTurn(async () => {
    if (w.state === "minimized") await withoutStealingFocus(() => chrome.windows.update(w.id, {state: "normal", focused: false}).catch(() => {}));
    const t = await chrome.tabs.create({windowId: w.id, url, active: true});
    await groupTab(t.id, group || "EmaraAI delivery");
    return {t, out: await steps(t)};
  });
}
let turn = Promise.resolve();
// A timeout stops waiting, but cannot cancel a browser operation. Keep ownership until the actual operation ends.
const inTurn = fn => {
  const operation = turn.then(fn, fn);
  turn = operation.catch(() => {});
  let timer;
  return Promise.race([operation, new Promise((_, no) => { timer = setTimeout(() => no(new Error("delivery timed out; the browser operation still owns its turn")), 150000); })]).finally(() => clearTimeout(timer));
};
const winMode = a => a.window || (a.own_window === false ? "none" : "own");

const aloneInWindow = async t => (await chrome.tabs.query({windowId: t.windowId}).catch(() => [])).length === 1;
async function keepDrawn(t, mode) {
  if (t.autoDiscardable !== false) await chrome.tabs.update(t.id, {autoDiscardable: false}).catch(() => {});
  if (mode === "shared") return intoDeliveryWindow(t);
  if (mode !== "own") return t;
  if (!(await aloneInWindow(t))) {                         // it sits among other tabs (opened before this setting): give it its own window
    await withoutStealingFocus(() => chrome.windows.create({tabId: t.id, focused: false, ...WIN}).catch(() => {}));
    await groupTab(t.id, "EmaraAI");
    t = await chrome.tabs.get(t.id);
  }
  if (!(await aloneInWindow(t))) return t;                 // it could not be moved: it stays a background tab. A tab in YOUR window is never switched to.
  const w = await chrome.windows.get(t.windowId).catch(() => null);
  if (w && w.state === "minimized")                        // a minimized window is not drawn: show it again, behind what you are doing
    await withoutStealingFocus(() => chrome.windows.update(w.id, {state: "normal", focused: false}).catch(() => {}));
  if (!t.active) await chrome.tabs.update(t.id, {active: true}).catch(() => {});
  return chrome.tabs.get(t.id);
}
function pageHidden() { return {hidden: document.hidden, focused: document.hasFocus()}; }
// claude.ai builds its menus only while the page is really drawn. A covered or background window counts as hidden, and then
// the model, effort and connector menus stay empty. For the few seconds such a menu is needed the tab's window is brought
// to the front, and the window you were in gets the focus back right after. Nothing is fronted when the page is drawn anyway.
async function whileShown(tabId, allow, fn) {
  const hidden = await run(tabId, pageHidden, [], "MAIN").then(r => !!(r && r.hidden)).catch(() => false);
  if (!hidden || allow === false) return fn();
  const t = await chrome.tabs.get(tabId), before = await chrome.windows.getLastFocused().catch(() => null);
  await chrome.tabs.update(tabId, {active: true}).catch(() => {});
  await chrome.windows.update(t.windowId, {focused: true, state: "normal"}).catch(() => {});
  await sleep(700);
  try { return await fn(); }
  finally { if (before && before.id !== t.windowId) await chrome.windows.update(before.id, {focused: true}).catch(() => {}); }
}
async function poolTab(a) {
  const t = a.tab_id ? await chrome.tabs.get(Number(a.tab_id)).catch(() => null) : null;
  if (!t) { const e = new Error("delivery tab not found"); e.missing = true; throw e; }
  return keepDrawn(t, winMode(a));
}

const bornAt = {};       // tab id -> when it appeared (a tab that was just opened is never swept away)
chrome.tabs.onCreated.addListener(t => { bornAt[t.id] = Date.now(); });
const touched = {};      // tab id -> the last time something happened in it (navigation, loading, a new title)
chrome.tabs.onUpdated.addListener((id, info) => { if (info.url || info.status || info.title) touched[id] = Date.now(); });
chrome.tabs.onRemoved.addListener(id => { delete bornAt[id]; delete touched[id]; });

const OPS = {
  async pool_open(a) {
    let t;
    const mode = winMode(a);
    if (mode === "shared") {
      const w = await deliveryWindow();
      if (w) t = await chrome.tabs.create({windowId: w.id, url: "https://chatgpt.com/", active: false});
      else {
        const made = await withoutStealingFocus(() => chrome.windows.create({url: "https://chatgpt.com/", focused: false, ...WIN}));
        await rememberDeliveryWindow(made.id); t = made.tabs[0];
      }
    } else if (mode === "own") {
      const n = (await chrome.windows.getAll().catch(() => [])).length;       // cascade the windows a little so each stays reachable
      t = (await withoutStealingFocus(() => chrome.windows.create({url: "https://chatgpt.com/", focused: false, ...WIN, left: 60 + (n % 6) * 34, top: 50 + (n % 6) * 30}))).tabs[0];
    } else t = await chrome.tabs.create({url: "https://chatgpt.com/", active: false});
    await chrome.tabs.update(t.id, {autoDiscardable: false}).catch(() => {});
    await groupTab(t.id, "EmaraAI delivery");
    await waitLoaded(t.id); await sleep(1200);
    return {tab_id: String(t.id), own_window: mode === "own", window: mode};
  },
  async pool_sweep(a) {
    // delivery tabs the hub no longer knows (left by a restart, or opened after the hub stopped waiting) are closed: the pool
    // never has more tabs than its setting. Only ChatGPT tabs inside the hub's own tab group are touched, never one just opened.
    const keep = new Set((a.keep || []).map(String)), closed = [];
    if (a.window === "shared") {
      for (const id of (a.delivery_tabs || [])) {
        const tab = await chrome.tabs.get(Number(id)).catch(() => null);
        if (tab) await intoDeliveryWindow(tab, false);
      }
    }
    if (!chrome.tabGroups) return {closed};
    const groups = (await chrome.tabGroups.query({})).filter(g => g.title === "EmaraAI" || g.title === "EmaraAI delivery" || g.title === "EmaraAI agents").map(g => g.id);
    for (const t of await chrome.tabs.query({})) {
      if (!groups.includes(t.groupId) || keep.has(String(t.id))) continue;
      const url = t.url || t.pendingUrl || "", age = Date.now() - (bornAt[t.id] || 0);
      if (url.startsWith("https://chatgpt.com/")) {            // a delivery tab the hub does not know
        if (age < 180000) continue;
      } else {
        // any other tab the hub opened (a tool tab, a page it showed): closed when nothing happened in it for idle_ms.
        // Never the tab you are looking at, and never one that is still loading.
        const quiet = Date.now() - Math.max(bornAt[t.id] || 0, touched[t.id] || 0, t.lastAccessed || 0);
        if (!a.idle_ms || quiet < a.idle_ms || t.active || t.status === "loading") continue;
      }
      await chrome.tabs.remove(t.id).catch(() => {}); closed.push(String(t.id));
    }
    return {closed};
  },
  async pool_nav(a) {
    let t = await poolTab(a);
    if ((t.url || "").split("#")[0] !== String(a.url).split("#")[0]) {
      await chrome.tabs.update(t.id, {url: a.url}); await sleep(500);
      await waitLoaded(t.id); await sleep(1500);
    }
    // loaded = the messages are there, or the box has stayed for 8 seconds without the error page taking its place
    // (a tab in the background does not always draw the messages)
    let boxFor = 0;
    const composer = async () => {
      const v = await run(t.id, pageChatLoaded, [], "MAIN").catch(() => 0);
      boxFor = v ? boxFor + 1 : 0;
      return v === 2 || boxFor >= 16;
    };
    const failedPage = (click, note) => run(t.id, pageLoadFailed, [!!click, note || ""], "MAIN").catch(() => false);
    // wait for the message box (a long conversation needs half a minute to draw it); stop early when ChatGPT shows its error page
    const ready = async n => {
      for (let i = 0; i < n; i++) {
        if (await composer()) return true;
        if (i > 3 && await failedPage(false)) return false;
        await sleep(500);
      }
      return false;
    };
    let ok = await ready(70), retries = 0, reloaded = false, noButton = 0, reacted = 0, direct = "", pressedWhat = "";
    if (!ok && await failedPage(false)) {
      // "Could not load this ChatGPT conversation": press its Retry, wait, look again - in this same tab, on this same chat
      const max = Math.max(0, a.retries == null ? 10 : a.retries), gap = Math.max(1, a.retry_seconds || 5) * 1000;
      while (!ok && retries < max) {
        retries++;
        const how = await failedPage(true, `EmaraAI: this conversation did not load \u2014 pressing Retry ${retries}/${max}`);
        if (how === "no_button") noButton++;
        if (how === false) { ok = await ready(30); break; }           // the error page is gone: wait for the chat
        let moved = false;
        for (let k = 0; k < 8 && !moved; k++) { await sleep(150); moved = !(await failedPage(false)); }   // did the page react to the press?
        if (!moved && how === "pressed") {
          direct = await run(t.id, pageRetryDirect, [], "MAIN").catch(e => "error " + e);
          for (let k = 0; k < 8 && !moved; k++) { await sleep(150); moved = !(await failedPage(false)); }
        }
        if (moved) reacted++;
        if (!pressedWhat) pressedWhat = await run(t.id, () => window.__emaraRetry || "", [], "MAIN").catch(() => "");
        await sleep(gap);
        ok = await composer() || (!(await failedPage(false)) && await ready(30));
        if (noButton >= 2) break;                                       // there is no Retry to press: go straight to the reload
      }
    }
    if (!ok) {      // still nothing: load the address again in this same tab
      await failedPage(false, "EmaraAI: Retry did not help \u2014 reloading this tab");
      await chrome.tabs.update(t.id, {url: a.url}); reloaded = true; await sleep(600); await waitLoaded(t.id); await sleep(1500);
      ok = await ready(70);
      if (!ok) await failedPage(false, "EmaraAI: ChatGPT still cannot load this conversation \u2014 trying again shortly");
    }
    t = await chrome.tabs.get(t.id);
    return {tab_id: String(t.id), url: t.url || "", composer: ok, retries, reloaded, no_retry_button: noButton, reacted, pressed_what: pressedWhat, direct, load_error: !ok && !!(await failedPage(false))};
  },
  async pool_verify(a) {
    const t = await poolTab(a);
    const seen = await run(t.id, pageObserve, [a.cfg], "MAIN");
    const turns = a.transcript ? await run(t.id, pageTranscript, [a.cfg, a.transcript, 200000], "MAIN").catch(() => []) : undefined;
    const drawn = await run(t.id, pageHidden, [], "MAIN").catch(() => ({}));
    return {...seen, turns, hidden: !!drawn.hidden, url: t.url || "", tab_id: String(t.id)};
  },
  async pool_send(a) {
    // the last look before typing happens here, next to the keyboard: a tab that is not on the expected chat gets nothing
    let t;
    try { t = await poolTab(a); }
    catch (e) { e.beforeSend = true; throw e; }
    if (chatIdOf(t.url) !== a.expect_chat_id) { const e = new Error("wrong chat: the tab shows '" + (chatIdOf(t.url) || "no chat") + "', not '" + a.expect_chat_id + "'"); e.beforeSend = true; throw e; }
    if (a.stop_selector) { await run(t.id, pageStop, [a.stop_selector], "MAIN").catch(() => {}); await sleep(900); }
    const r = await run(t.id, pageTypeSend, [a.text, a.plugin || "", a.sel, a.files || []], "MAIN");
    if (!r || !r.ok) { const e = new Error(((r && r.error) || "could not send") + " " + (await run(t.id, pageState, [], "MAIN").catch(() => ""))); e.beforeSend = !!r && !r.submission_attempted; throw e; }
    await sleep(1000);
    const t2 = await chrome.tabs.get(t.id);
    const after = await run(t.id, pageObserve, [a.cfg], "MAIN").catch(() => ({}));
    return {via: r.via, effort: r.thinking, thinking: r.thinking, model: r.model, attached: r.attached, cleared: r.cleared || 0, after: {...after, url: t2.url || ""}};
  },
  async pool_close(a) { const t = a.tab_id ? await chrome.tabs.get(Number(a.tab_id)).catch(() => null) : null; if (t) await withoutStealingFocus(() => chrome.tabs.remove(t.id).catch(() => {})); return {closed: !!t}; },
  async wopen(a) {
    // some sites only draw their replies in a tab that is on screen: such a chat gets a small window of its own
    let t, r;
    const type = async tab => {
      // Code credits can allow submission despite the weekly plan-limit banner.
      // Let the filled composer and actual submission establish availability.
      return run(tab.id, pageTypeSend, [a.text, "", a.sel, []], "MAIN");
    };
    if (a.own_window) {
      t = (await withoutStealingFocus(() => chrome.windows.create({url: a.url, focused: false, width: 560, height: 760}))).tabs[0];
      await groupTab(t.id, a.group);
      await waitLoaded(t.id); await sleep(2500);
      r = await type(t);
    } else {
      // claude.ai builds its model / effort menu and its mode switch only while the page is drawn: for a chat that needs them the
      // window is shown for the few seconds the message is typed (a.front; the focus goes back right after)
      const menus = !!(a.sel && (a.sel.model || a.sel.effort_label || a.sel.mode_required));
      ({t, out: r} = await newChatTab(a.url, a.group, async tab => {
        await waitLoaded(tab.id); await sleep(2500);
        const submit = () => type(tab);
        return menus ? whileShown(tab.id, a.front, submit) : submit();
      }));
    }
    if (!r || !r.ok) throw new Error((r && r.error) || "could not send");
    const pattern = a.chat_pattern ? new RegExp(a.chat_pattern) : null;
    let url = a.url;
    for (let i = 0; i < 40; i++) { url = (await chrome.tabs.get(t.id)).url; if (pattern && pattern.test(url)) break; await sleep(500); }   // some sites give the chat its address late
    return {tab_id: String(t.id), url, via: r.via, mode: r.mode, model: r.model, effort: r.thinking, connector: r.connector};
  },
  async claude_connector(a) {      // add the hub's connectors to claude.ai (asked for by the owner in Settings)
    const t = (await withoutStealingFocus(() => chrome.windows.create({url: "https://claude.ai/new", focused: false, width: 900, height: 760}))).tabs[0];
    try { await waitLoaded(t.id); await sleep(3500); return await whileShown(t.id, true, () => run(t.id, pageClaudeConnector, [a.connectors || []], "MAIN")); }
    finally { await chrome.tabs.remove(t.id).catch(() => {}); }
  },
  async wsend(a) {
    const t = await webTab(a);
    const r = await run(t.id, pageTypeSend, [a.text, "", a.sel, []], "MAIN");
    if (!r || !r.ok) throw new Error((r && r.error) || "could not send");
    return {tab_id: String(t.id), url: (await chrome.tabs.get(t.id)).url, via: r.via};
  },
  async wobserve(a) {
    const t = await webTab(a);
    const r = await run(t.id, pageObserve, [a.cfg], "MAIN");
    const last = await run(t.id, pageLastReply, [a.cfg.assistant_turn, 60000], "MAIN");
    const turns = a.transcript ? await run(t.id, pageTranscript, [a.cfg, a.transcript, 200000], "MAIN") : undefined;
    return {...r, last_text: last || "", turns, tab_id: String(t.id), tab_url: t.url || ""};
  },
  async wfront(a) {
    // a site that only draws while it is on screen: show its window for a moment, then give the focus back
    const t = await webTab(a);
    const before = await chrome.windows.getLastFocused().catch(() => null);
    await chrome.tabs.update(t.id, {active: true});
    await chrome.windows.update(t.windowId, {focused: true, state: "normal"}).catch(() => {});
    await sleep(a.ms || 1800);
    if (before && before.id !== t.windowId) await chrome.windows.update(before.id, {focused: true}).catch(() => {});
    return {shown: true};
  },
  async wstop(a) { const t = await webTab(a).catch(() => null); return {stopped: t ? await run(t.id, pageStop, [a.selector], "MAIN") : false}; },
  async wclose(a) { const t = a.tab_id ? await chrome.tabs.get(Number(a.tab_id)).catch(() => null) : null; if (t) await chrome.tabs.remove(t.id); return {closed: !!t}; },
  async debug_windows() {      // what Chrome's windows and tabs look like right now (diagnostics)
    const ws = await chrome.windows.getAll({populate: true});
    const groups = await chrome.tabGroups.query({}).catch(() => []);
    return {delivery_window: deliveryWin, groups: groups.map(g => ({id: g.id, title: g.title, window_id: g.windowId})), windows: ws.map(w => ({id: w.id, state: w.state, focused: w.focused, type: w.type, left: w.left, top: w.top, width: w.width, height: w.height,
      tabs: (w.tabs || []).map(t => ({id: t.id, active: t.active, status: t.status, discarded: t.discarded, frozen: t.frozen, group: t.groupId, url: (t.url || t.pendingUrl || "").slice(0, 70)}))}))};
  },
  async ping() { return {profile_tabs: (await chrome.tabs.query({url: "https://chatgpt.com/*"})).length}; },
  async open(a) {
    const t0 = Date.now();
    let loaded = 0;
    const {t, out: r} = await newChatTab(a.url, a.group, async tab => {
      await waitLoaded(tab.id); await sleep(1500);
      loaded = Date.now() - t0;
      const res = await run(tab.id, pageTypeSend, [a.text, a.plugin || "", a.sel, a.files || []], "MAIN");
      if (!res || !res.ok) await chrome.tabs.remove(tab.id).catch(() => {});       // a chat that was not started leaves no tab behind
      return res;
    });
    if (!r || !r.ok) throw new Error((r && r.error) || "could not send");
    const typed = Date.now() - t0;
    let url = a.url;
    // wait for the real address (a temporary chat never gets one: no_address)
    for (let i = 0; i < (a.no_address ? 0 : 90); i++) { const cur = await chrome.tabs.get(t.id); url = cur.url; if (chatIdOf(url)) break; await sleep(500); }
    return {tab_id: String(t.id), url, mentioned: r.mentioned, thinking: r.thinking, attached: r.attached, mode: r.mode, model: r.model,
            took: {loaded, typed, address: Date.now() - t0, page: r.took}};
  },
  async send(a) {
    let t = await findTab(a.ref);
    const known = chatIdOf(a.ref && a.ref.url);     // only a real chat id can be reopened (a temporary address would open an EMPTY chat)
    if (!t && known) {
      t = await chrome.tabs.create({url: a.ref.url, active: false}); await waitLoaded(t.id); await sleep(2500);
      t = await chrome.tabs.get(t.id);
      // ChatGPT sends an address it does not know to its start page. Typing there would begin a NEW chat: never do that.
      if (chatIdOf(t.url) !== known) { await chrome.tabs.remove(t.id).catch(() => {}); t = null; }
    }
    if (!t) { const e = new Error("chat tab not found"); e.missing = true; throw e; }
    await groupTab(t.id, a.group);
    const r = await run(t.id, pageTypeSend, [a.text, a.plugin || "", a.sel, a.files || []], "MAIN");
    if (!r || !r.ok) throw new Error((r && r.error) || "could not send");
    return {tab_id: String(t.id), url: t.url, mentioned: r.mentioned, thinking: r.thinking, attached: r.attached};
  },
  async observe(a) {
    const t = await findTab(a.ref);
    if (!t) { const e = new Error("chat tab not found"); e.missing = true; throw e; }
    await groupTab(t.id, a.group);
    return {...(await run(t.id, pageObserve, [a.cfg], "MAIN")), tab_id: String(t.id), tab_url: t.url || ""};
  },
  async stop(a) { const t = await findTab(a.ref); return {stopped: t ? await run(t.id, pageStop, [a.selector], "MAIN") : false}; },
  async close(a) { const t = await findTab(a.ref); if (t) await chrome.tabs.remove(t.id); return {closed: !!t}; },
  async locate(a) {
    const out = [];
    for (const t of await chrome.tabs.query({url: "https://chatgpt.com/c/*"})) {
      try { if (await run(t.id, pageHas, [a.text])) out.push({tab_id: String(t.id), url: t.url}); } catch (e) { /* tab not scriptable */ }
    }
    return {tabs: out};
  },
  async project(a) {            // find or create the ChatGPT Project with this name
    let made = null;
    try {
      const {t, out: r} = await newChatTab("https://chatgpt.com/", "", async tab => { made = tab; await waitLoaded(tab.id); await sleep(2000); return run(tab.id, pageProject, [a.name, a.memory || "project_only"], "MAIN"); });
      made = t;
      if (!r || !r.ok) throw new Error((r && r.error) || "could not create the ChatGPT project");
      return {id: r.id, created: r.created, memory: r.memory, url: "https://chatgpt.com/g/" + r.id + "/project"};
    } finally { if (made) await chrome.tabs.remove(made.id).catch(() => {}); }
  },
  async organize(a) {           // name the chat (master-01, backend-02 …) and put it into its ChatGPT Project
    const t = await findTab(a.ref);
    if (!t) { const e = new Error("chat tab not found"); e.missing = true; throw e; }
    await groupTab(t.id, a.group);
    const m = (t.url || "").match(/\/c\/([0-9a-f-]{20,})/);
    if (!m) throw new Error("the chat has no address yet");
    const r = await run(t.id, pageOrganize, [m[1], a.title || "", a.project || ""], "MAIN");
    if (!r || !r.ok) throw new Error((r && r.error) || "could not rename the chat");
    let url = t.url;           // after a move ChatGPT navigates the tab: wait until it shows this chat again
    for (let i = 0; i < 20; i++) { await sleep(400); url = (await chrome.tabs.get(t.id)).url; if (chatIdOf(url) === m[1]) break; }
    return {...r, tab_id: String(t.id), url: chatIdOf(url) === m[1] ? url : ""};
  },
  async reload() { setTimeout(() => chrome.runtime.reload(), 300); return {reloading: true}; },
  async inject(a) {
    const t = await chatgptTab();
    let res = await run(t.id, pageInject, [a.cfg], "MAIN");
    const missing = (res.results || []).filter(r => r.installed === false).map(r => r.name);
    if (a.install && missing.length) {
      const p = await chrome.tabs.create({url: "https://chatgpt.com/plugins?directoryTab=personal", active: false});
      await waitLoaded(p.id); await sleep(2500);
      const clicks = await run(p.id, pageInstall, [missing], "MAIN").catch(e => ({error: String(e)}));
      await sleep(1500); await chrome.tabs.remove(p.id).catch(() => {});
      res = await run(t.id, pageInject, [a.cfg], "MAIN");
      res.install_clicks = clicks;
    }
    return res;
  },
};


// ---------------------------------------------------------------- browser tools (any tab of this profile)
function pageEl(a) {
  let el = null;
  if (a.element_ref) el = document.querySelector('[data-emara-ref="' + a.element_ref + '"]');
  if (!el && a.selector) { try { el = document.querySelector(a.selector); } catch (e) { return {error: "bad selector: " + a.selector}; } }
  return el ? {el} : {error: "element not found" + (a.selector ? ": " + a.selector : "")};
}
function pageControlled(a) {
  // Who is controlling this tab: a thin frame in the agent's colour (the page stays fully readable), a label with the agent's
  // name, and the agent's own pointer that glides to what is about to be clicked or typed into, with a ring where it lands.
  // It removes itself a few seconds after the last action. Your own mouse pointer is not changed.
  const ID = "emara-ctl", color = (a && a.color) || "#5b8cff", who = String((a && a.who) || "EmaraAI").slice(0, 60);
  let root = document.getElementById(ID);
  if (!root) {
    root = document.createElement("div"); root.id = ID;
    root.innerHTML = `<style>
      #${ID}{position:fixed;inset:0;pointer-events:none;z-index:2147483646;font:600 12px/1 system-ui,Segoe UI,Arial,sans-serif;--c:#5b8cff}
      #${ID} .f{position:absolute;inset:0;border:2px solid var(--c);box-shadow:inset 0 0 12px 0 color-mix(in srgb,var(--c) 40%,transparent)}
      #${ID} .b{position:absolute;top:8px;left:50%;transform:translateX(-50%);display:flex;gap:8px;align-items:center;background:rgba(22,25,34,.9);color:#fff;padding:5px 12px 5px 6px;border-radius:99px;border:1px solid var(--c);box-shadow:0 4px 14px rgba(0,0,0,.28);white-space:nowrap}
      #${ID} .b i{width:20px;height:20px;border-radius:50%;background:var(--c);display:grid;place-items:center;font:700 10px/1 system-ui,Arial,sans-serif;font-style:normal}
      #${ID} .b u{text-decoration:none;font-weight:500;opacity:.72}
      #${ID} .m{position:absolute;left:0;top:0;transition:transform .5s cubic-bezier(.22,.8,.24,1);will-change:transform}
      #${ID} .m svg{display:block;filter:drop-shadow(0 2px 3px rgba(0,0,0,.4))}
      #${ID} .m span{position:absolute;left:15px;top:20px;background:var(--c);color:#fff;padding:3px 7px;border-radius:8px;font-size:11px;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.25)}
      #${ID} .r{position:absolute;width:14px;height:14px;margin:-7px 0 0 -7px;border-radius:50%;border:2px solid var(--c);opacity:0}
      #${ID} .r.go{animation:emara-ctl-r .6s ease-out}
      @keyframes emara-ctl-r{0%{opacity:.95;transform:scale(1)}100%{opacity:0;transform:scale(4.5)}}
    </style><div class="f"></div><div class="b"><i></i><span></span><u>is controlling this tab</u></div>
    <div class="m"><svg width="22" height="26" viewBox="0 0 22 26"><path d="M2 1l17 12-8 1.5-4 8.5z" fill="var(--c)" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg><span></span></div><div class="r"></div>`;
    (document.documentElement || document.body).appendChild(root);
    const start = window.__emaraCtlPos || {x: innerWidth / 2, y: innerHeight / 2};
    root.querySelector(".m").style.transform = "translate(" + start.x + "px," + start.y + "px)";
    window.__emaraCtlPos = start;
    root.getBoundingClientRect();            // the pointer is drawn at its start before it is asked to move
  }
  root.style.setProperty("--c", color);
  root.querySelector(".b i").textContent = who.slice(0, 1).toUpperCase();
  root.querySelector(".b span").textContent = who;
  root.querySelector(".m span").textContent = who.split(" ")[0];
  let el = null;
  try { el = a && a.element_ref ? document.querySelector('[data-emara-ref="' + a.element_ref + '"]') : (a && a.selector ? document.querySelector(a.selector) : null); } catch (e) {}
  if (el) {
    let r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) { try { el.scrollIntoView({block: "center", inline: "center"}); } catch (e) {} r = el.getBoundingClientRect(); }
    const x = Math.max(4, Math.min(innerWidth - 8, r.left + r.width / 2)), y = Math.max(4, Math.min(innerHeight - 8, r.top + r.height / 2));
    root.querySelector(".m").style.transform = "translate(" + x + "px," + y + "px)";
    window.__emaraCtlPos = {x, y};
    const ring = root.querySelector(".r");
    clearTimeout(window.__emaraCtlRing);
    window.__emaraCtlRing = setTimeout(() => { ring.style.left = x + "px"; ring.style.top = y + "px"; ring.classList.remove("go"); ring.getBoundingClientRect(); ring.classList.add("go"); }, 470);
  }
  clearTimeout(window.__emaraCtlTimer);
  window.__emaraCtlTimer = setTimeout(() => { const x = document.getElementById(ID); if (x) x.remove(); }, 6000);
  return true;
}
function pageAct(a) {
  const q = (() => {
    let el = null;
    if (a.element_ref) el = document.querySelector('[data-emara-ref="' + a.element_ref + '"]');
    if (!el && a.selector) { try { el = document.querySelector(a.selector); } catch (e) { return {error: "bad selector: " + a.selector}; } }
    return el ? {el} : {error: "element not found" + (a.selector ? ": " + a.selector : "")};
  });
  const flash = el => { if (!a.visual_feedback) return; const o = el.style.outline; el.style.outline = "3px solid #6d8dff"; setTimeout(() => { el.style.outline = o; }, a.feedback_ms || 700); };
  const text = el => (el.innerText || el.value || el.getAttribute("aria-label") || "").trim();
  switch (a.action) {
    case "inspect": return {url: location.href, title: document.title, text: (document.body.innerText || "").slice(0, a.max_chars || 8000)};
    case "query": {
      let list; try { list = Array.from(document.querySelectorAll(a.selector)); } catch (e) { return {error: "bad selector: " + a.selector}; }
      const out = [];
      for (const el of list) {
        if (!a.include_hidden && !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) continue;
        let ref = el.getAttribute("data-emara-ref");
        if (!ref) { ref = "e" + Math.random().toString(36).slice(2, 9); el.setAttribute("data-emara-ref", ref); }
        const item = {element_ref: ref, tag: el.tagName.toLowerCase(), text: text(el).slice(0, 120)};
        for (const k of ["id", "name", "type", "href", "placeholder", "aria-label", "role", "value"]) { const v = el.getAttribute(k); if (v) item[k] = v.slice(0, 160); }
        out.push(item);
        if (out.length >= (a.limit || 20)) break;
      }
      return {elements: out, total_matching: list.length};
    }
    case "click": { const r = q(); if (r.error) return r; r.el.scrollIntoView({block: "center"}); flash(r.el); r.el.click(); return {clicked: text(r.el).slice(0, 80) || r.el.tagName.toLowerCase()}; }
    case "hover": { const r = q(); if (r.error) return r; r.el.scrollIntoView({block: "center"}); flash(r.el);
      for (const t of ["mouseover", "mouseenter", "mousemove"]) r.el.dispatchEvent(new MouseEvent(t, {bubbles: true})); return {hovered: true}; }
    case "fill": { const r = q(); if (r.error) return r; const el = r.el; el.scrollIntoView({block: "center"}); flash(el); el.focus();
      if (el.isContentEditable) { document.execCommand("selectAll"); document.execCommand("insertText", false, a.value); }
      else { const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const set = Object.getOwnPropertyDescriptor(proto, "value"); set && set.set ? set.set.call(el, a.value) : (el.value = a.value);
        el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true})); }
      return {filled: true, chars: String(a.value).length}; }
    case "select": { const r = q(); if (r.error) return r; const el = r.el;
      const opt = Array.from(el.options || []).find(o => o.value === a.value || o.text.trim() === a.value);
      if (!opt) return {error: "option not found: " + a.value + ". Options: " + Array.from(el.options || []).map(o => o.text.trim()).slice(0, 15).join(", ")};
      el.value = opt.value; el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true})); return {selected: opt.text.trim()}; }
    case "press": { const el = document.activeElement || document.body; const init = {key: a.key, code: a.key, bubbles: true, cancelable: true};
      el.dispatchEvent(new KeyboardEvent("keydown", init)); el.dispatchEvent(new KeyboardEvent("keypress", init)); el.dispatchEvent(new KeyboardEvent("keyup", init));
      if (a.key === "Enter" && el.form) { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); }
      return {pressed: a.key}; }
    case "scroll": window.scrollBy(a.delta_x || 0, a.delta_y || 0); return {scroll_y: Math.round(window.scrollY)};
    case "read_text": { const r = q(); if (r.error) return r; return {text: text(r.el).slice(0, a.max_chars || 4000)}; }
    case "exists": { const r = q(); return {found: !r.error}; }
  }
  return {error: "unknown page action " + a.action};
}

function hostAllowed(url, allowed) {
  if (!allowed || !allowed.length) return true;
  let h = "", hp = ""; try { const u = new URL(url); h = u.hostname.toLowerCase(); hp = u.host.toLowerCase(); } catch (e) { return false; }
  if (allowed.some(a => a[0] === "!" && (hp === a.slice(1) || h === a.slice(1)))) return false;      // "!host:port" = never (the hub's own pages)
  const yes = allowed.filter(a => a[0] !== "!");
  return !yes.length || yes.some(a => h === a || h.endsWith("." + a));
}
async function tabFor(a) {
  const t = await chrome.tabs.get(Number(a.tab_id)).catch(() => null);
  if (!t) { const e = new Error("Unknown tab " + a.tab_id); e.missing = true; throw e; }
  if (!hostAllowed(t.url || "", a.allowed_hosts)) throw new Error("This site is not in the allowed list (Settings → PC bridge → browser allowed hosts).");
  return t;
}
async function pageAudit(limit) {
  // Press every visible control once and compare the page before and after. A control that changes nothing is "dead".
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && s.pointerEvents !== "none"; };
  const label = el => ((el.innerText || "").trim() || el.getAttribute("aria-label") || el.title || el.value || el.id || el.tagName).replace(/\s+/g, " ").slice(0, 70);
  const hash = s => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; };
  const snap = el => ({href: location.href, text: hash(document.body.innerText || ""), len: (document.body.innerText || "").length,
    dialogs: document.querySelectorAll("[role=dialog],[role=alertdialog],dialog[open],[aria-modal=true],[role=menu],[role=listbox]").length,
    reqs: performance.getEntriesByType("resource").length, self: el && el.isConnected ? hash(el.outerHTML) : -1,
    root: hash(document.documentElement.className + "|" + document.body.className + "|" + (document.documentElement.getAttribute("data-theme") || "") + "|" + document.documentElement.dir)});
  // href="#" only adds a # to the address, and "#name" without such a place on the page leads nowhere: neither is a result
  const hashOnly = (x, y) => { const [bx, hx = ""] = x.split("#"), [by, hy = ""] = y.split("#"); if (bx !== by) return false; return !hy || !(document.getElementById(hy) || document.getElementsByName(hy).length) || hx === hy; };
  const danger = /(delete|remove|log ?out|sign ?out|pay|purchase|checkout|confirm|submit|send|reset|drop|destroy|uninstall|حذف|خروج|دفع|إرسال|ارسال|تأكيد)/i;
  const sel = "a, button, [role=button], [role=link], [role=tab], [role=menuitem], [role=switch], input[type=button], input[type=submit], summary, [onclick], [tabindex]:not([tabindex='-1'])";
  const seen = new Set(), items = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el) || el.closest("[aria-hidden=true]")) continue;
    const key = label(el) + "|" + el.tagName;
    if (seen.has(key)) continue;
    seen.add(key); items.push(el);
    if (items.length >= limit) break;
  }
  const out = [], start = location.href;
  for (const el of items) {
    const name = label(el), tag = el.tagName.toLowerCase();
    const row = {label: name, tag, outcome: "", detail: ""};
    out.push(row);
    if (!el.isConnected || !vis(el)) { row.outcome = "gone"; row.detail = "disappeared after an earlier click"; continue; }
    if (el.disabled || el.getAttribute("aria-disabled") === "true") { row.outcome = "disabled"; continue; }
    const href = tag === "a" ? (el.getAttribute("href") || "") : "";
    if (danger.test(name)) { row.outcome = "skipped"; row.detail = "looks like it changes or sends something: try it yourself"; continue; }
    if (el.type === "submit" && el.form) { row.outcome = "skipped"; row.detail = "submits a form: try it yourself"; continue; }
    if (href && !/^(#|javascript:|$)/i.test(href.trim()) && el.target === "_blank") { row.outcome = "navigates"; row.detail = "opens " + href.slice(0, 100); continue; }
    const before = snap(el);
    // A plain link would load another page and end this check. The click is let through to the page's own handlers first
    // (a router handles it there); only if nobody handled it is the browser's own navigation stopped and the address noted.
    let nav = "", left = false;
    const guard = e => { const a = e.target && e.target.closest && e.target.closest("a[href]"); if (a && !e.defaultPrevented && !/^(#|javascript:)/i.test(a.getAttribute("href") || "#")) { e.preventDefault(); nav = a.href; } };
    const onLeave = () => { left = true; };
    addEventListener("click", guard, false); addEventListener("beforeunload", onLeave);
    try { el.scrollIntoView({block: "center"}); el.click(); } catch (e) { removeEventListener("click", guard, false); row.outcome = "error"; row.detail = String(e).slice(0, 100); continue; }
    await sleep(700);
    removeEventListener("click", guard, false); removeEventListener("beforeunload", onLeave);
    if (left) { row.outcome = "navigates"; row.detail = "loads another page"; break; }
    const after = snap(el);
    if (nav && after.href === before.href && after.text === before.text) {
      let status = 0;
      if (nav.startsWith(location.origin)) { try { status = (await fetch(nav, {method: "GET", credentials: "include"})).status; } catch (e) { status = -1; } }
      if (status >= 400 || status === -1) { row.outcome = "dead"; row.detail = "a link to " + nav.replace(location.origin, "").slice(0, 80) + " which answers " + (status === -1 ? "nothing" : status); }
      else { row.outcome = "navigates"; row.detail = "a link to " + nav.replace(location.origin, "").slice(0, 100) + (status ? " (answers " + status + ")" : ""); }
    }
    else if (after.href !== before.href && !hashOnly(before.href, after.href)) { row.outcome = "address"; row.detail = after.href.replace(location.origin, "").slice(0, 100); }
    else if (after.dialogs > before.dialogs) { row.outcome = "dialog"; row.detail = "something opened"; }
    else if (after.text !== before.text) { row.outcome = "content"; row.detail = "the page text changed (" + (after.len - before.len) + " characters)"; }
    else if (after.reqs > before.reqs) { row.outcome = "request"; row.detail = (after.reqs - before.reqs) + " request(s) sent"; }
    else if (after.root !== before.root || after.self !== before.self) { row.outcome = "state"; row.detail = "its own look or the page's mode changed"; }
    else { row.outcome = "dead"; row.detail = "nothing changed: address, content, dialogs, requests, state"; }
    if (after.dialogs > before.dialogs) { document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true})); await sleep(250); }
    if (location.href !== start) { history.back(); await sleep(700); }
  }
  const dead = out.filter(r => r.outcome === "dead");
  return {url: start, total: out.length, controls: out, dead: dead.map(r => r.label),
          summary: out.length + " control(s) tried, " + dead.length + " dead" + (dead.length ? ": " + dead.map(r => r.label).slice(0, 12).join(", ") : "")};
}

let browserCaptureTail = Promise.resolve();
function pageUpload(a) {
  const input = document.querySelector(a.selector);
  if (!input || input.tagName !== "INPUT" || input.type !== "file" || input.disabled)
    throw new Error("Upload selector must identify an enabled file input.");
  if (!input.multiple && a.files.length !== 1) throw new Error("This input accepts one file.");
  const transfer = new DataTransfer();
  for (const file of a.files) {
    const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
    transfer.items.add(new File([bytes], file.name, {type: file.type}));
  }
  input.files = transfer.files;
  input.dispatchEvent(new Event("input", {bubbles: true}));
  input.dispatchEvent(new Event("change", {bubbles: true}));
  return {files: Array.from(transfer.files, f => ({name: f.name, bytes: f.size})), summary: "Files selected; verify the page's result."};
}
const captureInTurn = fn => {
  const pending = browserCaptureTail.then(async () => { await sleep(600); return fn(); });
  browserCaptureTail = pending.catch(() => {});
  return pending;
};
OPS.browser = async function (a) {
  const brief = t => ({tab_id: String(t.id), title: (t.title || "").slice(0, 120), url: t.url || "", active: !!t.active});
  switch (a.action) {
    case "state": case "tabs": return {tabs: (await chrome.tabs.query({})).filter(t => /^https?:/.test(t.url || "") && hostAllowed(t.url, a.allowed_hosts)).map(brief)};   // only tabs of sites the caller may use
    case "new_tab": {
      if (a.url && !hostAllowed(a.url, a.allowed_hosts)) throw new Error("This site is not in the allowed list (Settings → PC bridge → browser allowed hosts).");
      const t = await chrome.tabs.create({url: a.url || "about:blank", active: !a.background}); await groupTab(t.id, "EmaraAI agents"); const done = await waitLoaded(t.id, a.timeout_ms || 30000);
      await run(t.id, pageControlled, [{who: a.who, color: a.color}]).catch(() => {}); return {tab: brief(done), tab_id: String(t.id)};
    }
    case "navigate": { const t = await tabFor(a); if (!hostAllowed(a.url, a.allowed_hosts)) throw new Error("This site is not in the allowed list.");
      await chrome.tabs.update(t.id, {url: a.url}); await sleep(300); return {tab: brief(await waitLoaded(t.id, a.timeout_ms || 30000))}; }
    case "back": case "forward": case "reload": { const t = await tabFor(a);
      try {
        a.action === "back" ? await chrome.tabs.goBack(t.id) : a.action === "forward" ? await chrome.tabs.goForward(t.id) : await chrome.tabs.reload(t.id);
      } catch (e) {
        if (a.action === "reload" || !/Cannot find a next page in history/i.test(String(e.message || e))) throw e;
        const fallback = await run(t.id, (direction) => {
          if (history.length <= 1) return {requested: false};
          history.go(direction === "back" ? -1 : 1);
          return {requested: true};
        }, [a.action], "MAIN");
        if (!fallback || !fallback.requested) throw e;
      }
      await sleep(300); const done = await waitLoaded(t.id, 30000);
      return {tab: brief(done), url_changed: done.url !== t.url}; }
    case "screenshot": return captureInTurn(async () => {
      const t = await tabFor(a);
      if (a.sel && a.sel.execution_deadline && Date.now() >= a.sel.execution_deadline) throw new Error("Screenshot command expired before capture.");
      const previous = (await chrome.tabs.query({windowId: t.windowId, active: true}))[0];
      try {
        await chrome.tabs.update(t.id, {active: true});
        await sleep(200);
        const active = (await chrome.tabs.query({windowId: t.windowId, active: true}))[0];
        if (!active || active.id !== t.id) throw new Error("Screenshot cancelled: another tab became active.");
        const data_url = await chrome.tabs.captureVisibleTab(t.windowId, {format: "png"});
        const after = (await chrome.tabs.query({windowId: t.windowId, active: true}))[0];
        if (!after || after.id !== t.id) throw new Error("Screenshot cancelled: tab changed during capture.");
        return {tab_id: String(t.id), url: t.url, data_url};
      } finally {
        if (previous && previous.id !== t.id) {
          const current = (await chrome.tabs.query({windowId: t.windowId, active: true}))[0];
          if (current && current.id === t.id) await chrome.tabs.update(previous.id, {active: true}).catch(() => {});
        }
      }
    });
    case "audit_clicks": { const t = await tabFor(a); const r = await run(t.id, pageAudit, [a.limit || 60], "MAIN"); if (!r) throw new Error("the page could not be checked"); return r; }
    case "upload": { const t = await tabFor(a); const r = await run(t.id, pageUpload, [a], "MAIN"); if (!r) throw new Error("the page did not accept file selection"); return r; }
    case "activate_tab": { const t = await tabFor(a); await chrome.tabs.update(t.id, {active: true}); await chrome.windows.update(t.windowId, {focused: true}); return {activated: String(t.id)}; }
    case "close_tab": { const t = await tabFor(a); await chrome.tabs.remove(t.id); return {closed: String(t.id)}; }
    case "wait": { const t = await tabFor(a); const until = Date.now() + (a.timeout_ms || 20000);
      while (Date.now() < until) { const r = await run(t.id, pageAct, [{action: "exists", selector: a.selector}]).catch(() => null); if (r && r.found) return {met: true}; await sleep(400); }
      return {met: false, summary: "the element did not appear in time"}; }
    default: { const t = await tabFor(a);
      const LOOKING = ["query", "read_text", "read", "exists", "wait", "wait_for", "extract", "get_text", "find", "state"];      // looking is not controlling
      if (a.show_control !== false && !LOOKING.includes(a.action)) { await run(t.id, pageControlled, [{selector: a.selector, element_ref: a.element_ref, who: a.who, color: a.color}]).catch(() => {}); if (a.selector || a.element_ref) await sleep(560); }
      const r = await run(t.id, pageAct, [a]); if (!r) throw new Error("the page did not answer (still loading, or a browser-internal page)"); if (r.error) throw new Error(r.error); return r; }
  }
};

// ---------------------------------------------------------------- connection: handshake -> heartbeat (long-poll) -> reconnect with backoff
let TOKEN = "";
let telemetry = {signed_in: false, tabs: 0, checked: 0};
// every request: session token + fresh nonce + timestamp (the hub rejects replays and stale requests)
const post = (path, body) => fetch(HUB + "/api/v1" + path, {method: "POST", headers: {"content-type": "application/json"},
  body: JSON.stringify({...body, token: TOKEN, nonce: crypto.randomUUID(), timestamp: Date.now()})});

async function handle(cmd) {
  let body;
  // request {id, action, timestamp, session, nonce, payload}  ->  response {id, success, timestamp, error, data}
  const action = cmd.action || cmd.op, payload = cmd.payload || cmd.args || {};
  try {
    if (!OPS[action]) throw new Error("unknown action " + action);
    // in the shared delivery window only one tab can be the drawn one: its delivery steps take turns
    const turns = payload && payload.window === "shared" && ["pool_open", "pool_nav", "pool_verify", "pool_send", "pool_sweep"].includes(action);
    const execute = async () => {
      if (cmd.expires_at && Date.now() >= cmd.expires_at) throw new Error("command expired before execution");
      if (cmd.expires_at) payload.sel = {...(payload.sel || {}), execution_deadline: cmd.expires_at};
      if (action !== "pool_send" || !payload.delivery_id) return OPS[action](payload);
      const key = "deliveryReceipt:" + payload.delivery_id;
      const saved = (await chrome.storage.local.get(key))[key];
      if (saved && saved.state === "done") return saved.result;
      if (saved) throw new Error("delivery outcome is uncertain; automatic resend was prevented");
      await chrome.storage.local.set({[key]: {state: "running", at: Date.now()}});
      let result;
      try { result = await OPS[action](payload); }
      catch (e) { if (e.beforeSend || /still answering/i.test(String(e.message || e))) await chrome.storage.local.remove(key); throw e; }
      const normalize = x => String(x || "").replace(/\s+/g, " ").trim();
      if (normalize((result.after || {}).last_user) === normalize(payload.text)) {
        await chrome.storage.local.set({[key]: {state: "done", at: Date.now(), result}});
      }
      return result;
    };
    body = {id: cmd.id, success: true, data: turns ? await inTurn(execute) : await execute()};
  } catch (e) { body = {id: cmd.id, success: false, error: String(e && e.message || e), missing: !!(e && e.missing)}; }
  await post("/ext/result", body).catch(() => {});
}

async function refreshTelemetry() {
  const tabs = await chrome.tabs.query({url: "https://chatgpt.com/*"});
  telemetry.tabs = tabs.length;
  const ua = (navigator.userAgent.match(/(Edg|Chrome)\/(\d+)/) || []);
  telemetry.browser = ua.length ? (ua[1] === "Edg" ? "Edge " : "Chrome ") + ua[2] : "Browser";
  if (tabs.length && Date.now() - telemetry.checked > 60000) {
    telemetry.checked = Date.now();
    try {
      telemetry.signed_in = !!(await run(tabs[0].id, () => fetch("/api/auth/session", {credentials: "include"}).then(r => r.json()).then(s => !!(s && s.accessToken)).catch(() => false), [], "MAIN"));
    } catch (e) { /* tab not ready */ }
  }
  return {signed_in: telemetry.signed_in, tabs: telemetry.tabs, browser: telemetry.browser};
}

async function handshake() {
  const st = await chrome.storage.local.get({hub: HUB, instance: ""});
  HUB = st.hub;
  const instance = st.instance || (crypto.randomUUID());
  if (!st.instance) await chrome.storage.local.set({instance});
  const r = await fetch(HUB + "/api/v1/ext/hello", {method: "POST", headers: {"content-type": "application/json"},
                                                   body: JSON.stringify({instance, version: chrome.runtime.getManifest().version})});
  const j = await r.json();
  if (!j.ok) throw new Error("handshake refused");
  TOKEN = j.token;
  chrome.action.setBadgeText({text: ""});
}

async function loop() {
  if (looping) return;
  looping = true;
  let backoff = 2000;
  while (true) {
    try {
      if (!TOKEN) await handshake();
      const currentTelemetry = () => ({signed_in: telemetry.signed_in, tabs: telemetry.tabs, browser: telemetry.browser});
      const freshTelemetry = await Promise.race([refreshTelemetry(), sleep(5000).then(currentTelemetry)]);
      const r = await post("/ext/poll", {version: chrome.runtime.getManifest().version, telemetry: freshTelemetry});
      if (r.status === 403) { TOKEN = ""; continue; }          // hub restarted: handshake again, then restore
      const j = await r.json();
      backoff = 2000;
      for (const c of (j.commands || [])) handle(c);           // not awaited: commands for different chats run side by side
    } catch (e) {
      TOKEN = "";
      chrome.action.setBadgeText({text: "!"}); chrome.action.setBadgeBackgroundColor({color: "#d33"});
      await sleep(backoff);
      backoff = Math.min(backoff * 2, 5000);                   // short cap: after a hub restart the extension is back within seconds, not half a minute
    }
  }
}

// telemetry: tell the hub when a ChatGPT tab changes page
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.url && (tab.url || "").startsWith("https://chatgpt.com/") && TOKEN)
    post("/ext/event", {event: "browser.page_changed", data: {url: info.url.split("?")[0], tab_id: String(tabId)}}).catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create("keepalive", {periodInMinutes: 0.5}); loop(); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create("keepalive", {periodInMinutes: 0.5}); loop(); });
chrome.alarms.onAlarm.addListener(() => loop());
loop();
