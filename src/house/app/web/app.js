'use strict';
/* House: interfaz local. Sin compilación ni dependencias; habla con la API en 127.0.0.1. */

const app = document.getElementById('app');
const S = { me: null, people: [], subject: null, catalog: {}, tab: 'res', sel: null, view: 'g', clinFilter: 'todo', clinEdit: null, sug: null, studyFilter: 'Todos' };

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ts = d => Date.parse(d + 'T00:00:00Z');
const fd = d => d ? new Date(ts(d)).toLocaleDateString('es-MX', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : 'sin fecha';
const fnum = v => (v == null ? '' : String(Number(Number(v).toFixed(3))));
// Valor con su signo si el laboratorio imprimió "< 0.02": es un límite del método, no una medida exacta.
const vnum = o => (o.qualifier ? o.qualifier + ' ' : '') + fnum(o.value_num);
const name = k => S.catalog[k]?.name || k;

function toast(t) {
  const d = document.createElement('div'); d.className = 'tg'; d.textContent = t;
  document.body.appendChild(d); setTimeout(() => d.remove(), 3200);
}

async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {};
  if (method !== 'GET') headers['x-house'] = '1';
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['content-type'] = 'application/json'; payload = JSON.stringify(body); }
  const r = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' });
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const data = isJson ? await r.json() : null;
  if (r.status === 401 && path !== '/api/login') {
    showLogin('Tu sesión se cerró. Vuelve a entrar con tu PIN.');
    throw Object.assign(new Error('Sesión cerrada'), { status: 401 });
  }
  if (!r.ok) {
    const d = data?.detail;
    const msg = typeof d === 'string' ? d : d?.message || (Array.isArray(d) ? 'Revisa los datos del formulario.' : 'Algo falló. Intenta de nuevo.');
    throw Object.assign(new Error(msg), { status: r.status, detail: d });
  }
  return data;
}

/* ---------- Primer uso, entrada y salida ---------- */

async function boot() {
  const s = await api('/api/status');
  if (s.needs_setup) return showSetup();
  try { S.me = await api('/api/me'); } catch { return; }
  await enter();
}

function showSetup() {
  app.innerHTML = `<div class="screen"><form class="panel fg2" id="f">
    <div class="brand">${brandIcon()}House</div>
    <h1>Bienvenido</h1>
    <p>Vas a crear tu perfil de administrador. Solo tú podrás agregar a tu familia y asignarles su PIN.</p>
    <label>Tu nombre<input type="text" name="display_name" required maxlength="60" autocomplete="off"></label>
    <label>Fecha de nacimiento<input type="date" name="birth_date" required></label>
    <label>Sexo al nacer (define los rangos de referencia)<select name="sex_at_birth"><option value="M">Masculino</option><option value="F">Femenino</option></select></label>
    <label>PIN de 4 a 8 dígitos<input type="password" name="pin" inputmode="numeric" pattern="[0-9]{4,8}" required></label>
    <label>Repite el PIN<input type="password" name="pin2" inputmode="numeric" pattern="[0-9]{4,8}" required></label>
    <p class="err" id="e"></p>
    <button class="btn">Crear mi perfil</button>
  </form></div>`;
  document.getElementById('f').onsubmit = async ev => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(ev.target));
    if (f.pin !== f.pin2) return (document.getElementById('e').textContent = 'Los PIN no coinciden.');
    try {
      await api('/api/setup', { method: 'POST', body: {
        display_name: f.display_name, birth_date: f.birth_date, sex_at_birth: f.sex_at_birth, pin: f.pin } });
      S.me = await api('/api/me'); await enter();
    } catch (e) { document.getElementById('e').textContent = e.message; }
  };
}

async function showLogin(msg) {
  S.me = null;
  const people = await api('/api/profiles');
  app.innerHTML = `<div class="screen"><div class="panel">
    <div class="brand">${brandIcon()}House</div>
    <h1>¿Quién eres?</h1>
    ${msg ? `<p>${esc(msg)}</p>` : ''}
    <div class="plist">${people.map(p => `<button data-id="${p.id}"><span class="av">${esc(p.display_name[0])}</span>${esc(p.display_name)}</button>`).join('')}</div>
  </div></div>`;
  app.querySelectorAll('.plist button').forEach(b => b.onclick = () => askPin(people.find(p => p.id == b.dataset.id)));
}

function askPin(p) {
  app.innerHTML = `<div class="screen"><form class="panel" id="f">
    <h1>Hola, ${esc(p.display_name)}</h1><p>Escribe tu PIN</p>
    <input class="pinin" type="password" name="pin" inputmode="numeric" autocomplete="off" autofocus maxlength="8">
    <p class="err" id="e"></p>
    <button class="btn">Entrar</button>
    <button type="button" class="link" id="back">Elegir otra persona</button>
  </form></div>`;
  document.getElementById('back').onclick = () => showLogin();
  document.getElementById('f').onsubmit = async ev => {
    ev.preventDefault();
    try {
      await api('/api/login', { method: 'POST', body: { person_id: p.id, pin: ev.target.pin.value } });
      S.me = await api('/api/me'); await enter();
    } catch (e) { document.getElementById('e').textContent = e.message; ev.target.pin.value = ''; }
  };
}

async function logout() { await api('/api/logout', { method: 'POST' }); showLogin(); }

/* ---------- Estructura ---------- */

async function enter() {
  if (!Object.keys(S.catalog).length) S.catalog = await api('/api/catalog');
  S.people = await api('/api/people');
  S.subject = S.subject && S.people.some(p => p.id === S.subject) ? S.subject : S.me.id;
  S.tab = 'res'; S.sel = null; S.clinEdit = null;
  renderShell();
}

function brandIcon() {
  return '<i><svg viewBox="0 0 24 24"><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/></svg></i>';
}

function renderShell() {
  const admin = S.me.is_admin, subj = S.people.find(p => p.id === S.subject);
  const tabs = [['res', 'Resumen'], ['exp', 'Expediente'], ['doc', 'Documentos'], ['img', 'Estudios'], ['ask', 'Asistente']].concat(admin ? [['fam', 'Familia'], ['cfg', 'Configuración']] : [['priv', 'Privacidad']]);
  app.innerHTML = `<div class="wrap">
    <header>
      <div class="brand">${brandIcon()}House</div>
      <div class="hdr-right">
        ${admin && S.people.length > 1 ? `<select class="psel" id="psel" aria-label="Ver perfil de">${S.people.map(p => `<option value="${p.id}" ${p.id === S.subject ? 'selected' : ''}>${esc(p.display_name)}</option>`).join('')}</select>` : ''}
        <span class="av" title="${esc(S.me.display_name)}">${esc(S.me.display_name[0])}</span>
        <button class="mini" id="out">Salir</button>
      </div>
    </header>
    <div class="who"><h1>Perfil de ${esc(subj.display_name)}</h1>
      <p>${admin ? (subj.id === S.me.id ? 'Administrador: puedes ver todos los perfiles de la familia.' : 'Estás viendo el perfil de otra persona; este acceso queda registrado y ella puede verlo.') : 'Solo ves tu propio perfil.'}</p></div>
    <div id="bkbanner"></div>
    <nav class="nav" role="tablist">${tabs.map(([k, n]) => `<button role="tab" data-t="${k}" aria-selected="${S.tab === k}">${n}</button>`).join('')}</nav>
    <div class="col" id="view"></div>
  </div>`;
  document.getElementById('out').onclick = logout;
  if (admin) backupBanner();
  const ps = document.getElementById('psel');
  if (ps) ps.onchange = () => { S.subject = Number(ps.value); S.sel = null; renderShell(); };
  app.querySelectorAll('.nav button').forEach(b => b.onclick = () => { S.tab = b.dataset.t; renderShell(); });
  ({ res: renderSummary, exp: renderClinical, img: renderImaging, ask: renderAssistant, doc: renderDocs, fam: renderFamily, cfg: renderConfig, priv: renderPrivacy })[S.tab]();
}

const view = () => document.getElementById('view');

/* ---------- Resumen ---------- */

const GROUPS = [
  ['glucosa', 'Metabolismo de la glucosa'], ['lipidos', 'Lípidos y riesgo cardiovascular'], ['higado', 'Hígado'],
  ['pancreas', 'Páncreas'], ['coagulacion', 'Coagulación'], ['rinon', 'Riñón'], ['electrolitos', 'Electrolitos y minerales'], ['sangre', 'Sangre y hierro'],
  ['diferencial', 'Glóbulos blancos'], ['tiroides', 'Tiroides'], ['vitaminas', 'Vitaminas'], ['inmunologia', 'Inmunología'],
  ['marcadores', 'Marcadores'], ['orina', 'Orina'], ['seminal', 'Espermiograma'], ['infecciosas', 'Infecciones'], ['heces', 'Heces'], ['otros', 'Otros'],
];
const STL = { ok: 'En rango', low: 'Por debajo', high: 'Por encima', abnormal: 'Fuera de referencia' };
const OUT = s => s === 'low' || s === 'high' || s === 'abnormal';

function pill(s) {
  if (!s) return '<span class="pill na">Sin referencia</span>';
  if (s === 'ok') return `<span class="pill ok"><svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6.5l2.5 2.5L10 3.5"/></svg>${STL.ok}</span>`;
  const path = s === 'high' ? 'M6 2l4.5 8h-9z' : s === 'low' ? 'M6 10L1.5 2h9z' : 'M6 1l5.5 10h-11z';
  return `<span class="pill out"><svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor"><path d="${path}"/></svg>${STL[s]}</span>`;
}
function spark(vals) {
  const w = 88, h = 32, p = 3, mn = Math.min(...vals), mx = Math.max(...vals), r = (mx - mn) || 1;
  const pts = vals.map((v, i) => [p + i * (w - 2 * p) / (vals.length - 1), h - p - ((v - mn) / r) * (h - 2 * p)]);
  const last = pts[pts.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts.map(q => q.join(',')).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/></svg>`;
}
function dist(o) {
  if (o.value_num == null) return 0;
  if (o.ref_high != null && o.value_num > o.ref_high) return (o.value_num - o.ref_high) / (o.ref_high || 1);
  if (o.ref_low != null && o.value_num < o.ref_low) return (o.ref_low - o.value_num) / (o.ref_low || 1);
  return 0;
}
function refText(o) {
  if (o.ref_printed) return o.ref_printed;
  const g = o.ref_source === 'general' ? ' (general)' : '';  // no es del laboratorio: se dice siempre
  if (o.ref_low != null && o.ref_high != null) return `${fnum(o.ref_low)} a ${fnum(o.ref_high)}${g}`;
  if (o.ref_high != null) return `hasta ${fnum(o.ref_high)}${g}`;
  if (o.ref_low != null) return `desde ${fnum(o.ref_low)}${g}`;
  return 'sin referencia';
}
const refNote = o => o.ref_note ? `<small style="display:block;color:var(--muted)">${esc(o.ref_note)}. Provisional.</small>` : '';

async function renderSummary() {
  view().innerHTML = '<p class="tip"><span class="spin"></span>Cargando…</p>';
  const { observations: obs, summary: sm } = await api(`/api/people/${S.subject}/overview`);
  if (!obs.length) {
    view().innerHTML = `<section class="card empty"><h2>Todavía no hay resultados</h2>
      <p>Sube un estudio de laboratorio en PDF. Lo revisarás junto al original y solo lo que confirmes aparecerá aquí.</p>
      <button class="btn" id="go">Subir un estudio</button></section>`;
    document.getElementById('go').onclick = () => { S.tab = 'doc'; renderShell(); };
    return;
  }
  const series = {};
  obs.forEach(o => (series[o.analyte_key] ||= []).push(o));
  Object.values(series).forEach(v => v.sort((a, b) => a.collected_on.localeCompare(b.collected_on)));
  const last = k => series[k][series[k].length - 1];
  const recent = new Set(sm.recent_keys);
  const stOf = k => sm.last_status[k]?.status ?? last(k).status;
  const yr = o => o.collected_on.slice(0, 4);
  const isNum = k => last(k).value_num != null;
  const valTxt = o => o.value_num != null ? `${vnum(o)} ${esc(o.unit)}` : esc(qlabel(o));
  S.sel = null;

  // ---- cifras del encabezado
  const nOut = sm.attention.length, nRecent = sm.counts.recent;
  const nOk = [...recent].filter(k => stOf(k) === 'ok').length, nNoRef = Math.max(0, nRecent - nOk - nOut);
  const nPersist = sm.attention.filter(a => a.kind === 'persistente').length;
  const nDocs = new Set(obs.map(o => o.document_id)).size;
  const pct = n => nRecent ? Math.round(100 * n / nRecent) : 0;

  const critBox = (sm.critical || []).length ? `<section class="alert" role="alert"><h2>${sm.critical.length === 1 ? 'Un resultado' : sm.critical.length + ' resultados'} muy alejado${sm.critical.length === 1 ? '' : 's'} de lo normal</h2>
      ${sm.critical.map(c => `<button class="fr" data-k="${esc(c.key)}"><span class="tag at">${c.side === 'high' ? 'Muy alto' : 'Muy bajo'}</span><span class="fm">${esc(name(c.key))} · ${valTxt(c.last)}</span><span class="fs">${fd(c.last.collected_on)} · límite ${fnum(c.limit)} ${esc(c.last.unit)}</span></button>`).join('')}
      <p>Un resultado así conviene comentarlo con un médico pronto. Antes, confirma en el original que el valor esté bien leído. Esto no es un diagnóstico.</p></section>` : '';
  const hero = `${critBox}<section class="card hero">
    <div class="hero-main">
      <p class="eyebrow">Al ${fd(sm.reference_date)} · ${nDocs} ${nDocs === 1 ? 'estudio' : 'estudios'}</p>
      <h2 class="hero-title ${nOut ? '' : 'good'}">${nOut ? `${nOut} ${nOut === 1 ? 'resultado fuera de rango' : 'resultados fuera de rango'}` : 'Todo en orden'}</h2>
      <p class="hero-sub">${nOut ? `de ${nRecent} análisis medidos en el último año${nPersist ? ` · ${nPersist} se ${nPersist === 1 ? 'repite' : 'repiten'} en varios estudios seguidos` : ''}` : `Nada fuera de rango en los ${nRecent} análisis del último año`}</p>
      <div class="segbar" role="img" aria-label="${nOk} en rango, ${nOut} fuera de rango, ${nNoRef} sin referencia">
        <i class="ok" style="flex:${nOk}"></i><i class="out" style="flex:${nOut}"></i><i class="na" style="flex:${nNoRef}"></i></div>
      <p class="seglegend"><span><i class="dot ok"></i>${nOk} en rango (${pct(nOk)} %)</span><span><i class="dot out"></i>${nOut} fuera de rango</span>${nNoRef ? `<span><i class="dot na"></i>${nNoRef} sin referencia</span>` : ''}</p>
    </div>
    <div class="hero-stats">
      <button class="stat" data-more="watch" ${sm.watch.length ? '' : 'disabled'}><b>${sm.watch.length}</b><span>a vigilar</span></button>
      <button class="stat" data-more="improved" ${sm.improved.length ? '' : 'disabled'}><b>${sm.improved.length}</b><span>${sm.improved.length === 1 ? 'mejoró' : 'mejoraron'}</span></button>
      <button class="stat" data-more="history" ${sm.history.length ? '' : 'disabled'}><b>${sm.history.length}</b><span>en el historial</span></button>
    </div></section>
    ${sm.profile?.minor ? '<div class="mixed">Perfil de una persona menor de 18 años: House usa solo los rangos que imprime el laboratorio (ya consideran la edad) y no aplica sus rangos generales ni sus alertas de valores críticos, que son para adultos.</div>' : ''}
    ${sm.study_is_old ? `<div class="mixed">Tu estudio más reciente es de ${fd(sm.reference_date)}, hace ${Math.round(sm.study_age_days / 365 * 10) / 10} años. Estos resultados pueden no reflejar tu estado actual.</div>` : ''}`;

  // ---- lo más importante: 4 tarjetas
  const measured = sm.attention.filter(a => !a.derived), derived = sm.attention.filter(a => a.derived);
  const ranked = [...measured, ...derived], top = ranked.slice(0, 4), rest = ranked.slice(4);
  const KIND = { persistente: 'Persistente', continua: 'Continúa', nuevo: 'Nuevo', unico: '' };
  const change = a => {
    const p = a.previous, l = a.last;
    if (!p || p.value_num == null || l.value_num == null || !a.trend || a.trend === 'estable') return '';
    return `<span class="chg ${a.trend === 'mejorando' ? 'good' : 'bad'}">${l.value_num < p.value_num ? '↘' : '↗'} ${a.trend} · antes ${fnum(p.value_num)} (${fd(p.collected_on).replace(/ \d{4}$/, '')})</span>`;
  };
  const priCard = a => {
    const l = a.last, vals = series[a.key].map(o => o.value_num).filter(v => v != null);
    const how = l.value_num != null && distOut(l) > 0 ? `${Math.round(distOut(l) * 100)} % ${l.status === 'high' ? 'sobre' : 'bajo'} el rango` : 'Fuera de lo esperado';
    return `<button class="pri" data-k="${esc(a.key)}"><span class="pri-top"><span class="pri-name">${esc(name(a.key))}</span>${KIND[a.kind] ? `<span class="tag at">${KIND[a.kind]}</span>` : ''}</span>
      <span class="pri-row"><span class="pri-val">${l.value_num != null ? vnum(l) : esc(qlabel(l))}${l.value_num != null ? `<small>${esc(l.unit)}</small>` : ''}</span>${vals.length > 1 && l.value_num != null ? spark(vals) : ''}</span>
      <span class="pri-meta">${how} · rango ${esc(refText(l))}${l.ref_from ? ` (del estudio de ${yr({ collected_on: l.ref_from })})` : ''}</span>${change(a)}</button>`;
  };
  const line = (cls, label, k, sub) => `<button class="fr" data-k="${esc(k)}"><span class="tag ${cls}">${label}</span><span class="fm">${esc(name(k))} · ${valTxt(last(k))}</span><span class="fs">${sub}</span></button>`;
  const importantes = nOut ? `<section class="sec"><div class="sec-h"><h3>Lo más importante</h3><span class="tip">Ordenado por qué tan lejos está del rango y cuánto se repite</span></div>
    <div class="pri-grid">${top.map(priCard).join('')}</div>
    ${rest.length ? `<button class="link" id="moreAtt">Ver los ${rest.length} restantes</button><div class="fl" id="restAtt" hidden>${rest.map(a => line('at', a.derived ? 'Calculado' : 'Atención', a.key, `${fd(last(a.key).collected_on)} · rango ${esc(refText(last(a.key)))}`)).join('')}</div>` : ''}</section>` : '';

  // ---- por sistema
  const sysList = GROUPS.map(([g, gname]) => {
    const keys = Object.keys(series).filter(k => (S.catalog[k]?.group || 'otros') === g);
    if (!keys.length) return null;
    const rec = keys.filter(k => recent.has(k)), out = rec.filter(k => OUT(stOf(k))), ok = rec.filter(k => stOf(k) === 'ok');
    return { g, gname, keys, rec, out, ok };
  }).filter(Boolean).sort((a, b) => (b.out.length > 0) - (a.out.length > 0) || b.out.length - a.out.length);
  const marker = k => {
    const s = series[k], l = last(k), old = !recent.has(k);
    if (!isNum(k)) return `<button class="qp ${!old && OUT(stOf(k)) ? 'out' : ''} ${old ? 'old' : ''}" data-k="${esc(k)}" title="Ver cómo ha cambiado">${esc(name(k))}: <b>${esc(qlabel(l))}</b>${old ? ` (${yr(l)})` : ''}<span class="qds">${s.slice(-6).map(o => `<i class="qd ${OUT(o.status) ? 'o' : o.status === 'ok' ? 'k' : ''}"></i>`).join('')}</span></button>`;
    const vals = s.map(o => o.value_num).filter(v => v != null);
    return `<button class="bm ${old ? 'old' : ''}" data-k="${esc(k)}"><span class="nm">${esc(name(k))}</span><div>${old ? `<span class="pill na">Último: ${yr(l)}</span>` : pill(stOf(k))}</div>
      <div class="row"><div><div class="val">${vnum(l)}<small>${esc(l.unit)}</small></div></div>${vals.length > 1 ? spark(vals) : ''}</div></button>`;
  };
  const splitMarkers = ks => `<div class="cards">${ks.filter(isNum).map(marker).join('')}</div><div class="qual">${ks.filter(k => !isNum(k)).map(marker).join('')}</div>`;
  const sysRow = sy => {
    const flagged = sy.keys.filter(k => recent.has(k) && OUT(stOf(k))), calm = sy.keys.filter(k => !flagged.includes(k));
    const names = sy.out.filter(k => !(sm.attention.find(a => a.key === k)?.derived)).slice(0, 3).map(name);
    const status = sy.out.length ? `<span class="sys-n out">${sy.out.length} fuera de rango</span>` : sy.rec.length ? '<span class="sys-n ok">todo en rango</span>' : '<span class="sys-n na">sin medición reciente</span>';
    return `<div class="sys" data-g="${sy.g}"><button class="sys-head" aria-expanded="false">
        <span class="sys-name">${sy.gname}<small>${names.length ? esc(names.join(', ')) : `${sy.rec.length} ${sy.rec.length === 1 ? 'análisis' : 'análisis'}`}</small></span>
        <span class="sys-bar"><i class="ok" style="flex:${sy.ok.length}"></i><i class="out" style="flex:${sy.out.length}"></i></span>${status}<span class="chev" aria-hidden="true">▾</span></button>
      <div class="sys-body" hidden>${flagged.length ? splitMarkers(flagged) : ''}
        ${calm.length ? `<details class="hist"><summary>${flagged.length ? `Y ${calm.length} más ${calm.length === 1 ? 'que está' : 'que están'} en rango o sin medición reciente` : `Ver los ${calm.length} análisis`}</summary>${splitMarkers(calm)}</details>` : ''}</div></div>`;
  };
  const alerts = sysList.filter(sy => sy.out.length), quiet = sysList.filter(sy => !sy.out.length);
  const sysHtml = alerts.map(sysRow).join('') + (quiet.length ? `<details class="sys quiet"><summary class="sys-head"><span class="sys-name">${quiet.length} ${quiet.length === 1 ? 'sistema' : 'sistemas'} sin alertas<small>${esc(quiet.map(q => q.gname).join(', '))}</small></span><span class="sys-n ok">todo en rango</span><span class="chev" aria-hidden="true">▾</span></summary>${quiet.map(sysRow).join('')}</details>` : '');

  const morePanel = `<section class="sec" id="morepanel" hidden></section>`;
  view().innerHTML = `${hero}${importantes}
    <section class="sec"><div class="sec-h"><h3>Por sistema</h3><span class="tip">Toca un sistema para ver sus análisis</span></div><div class="card syslist">${sysHtml}</div></section>
    ${morePanel}
    <p class="tip fine">Informativo; no sustituye una valoración médica. Los criterios de «atención» y «vigilar» son provisionales y están pendientes de revisión por un médico.</p>
    <div class="scrim" id="scrim" hidden></div><aside class="drawer" id="detail" hidden aria-label="Detalle del análisis"></aside>`;

  // ---- interacción
  view().querySelectorAll('button.sys-head').forEach(h => h.onclick = () => {
    const open = h.getAttribute('aria-expanded') === 'true';
    h.setAttribute('aria-expanded', String(!open)); h.nextElementSibling.hidden = open;
  });
  const moreAtt = document.getElementById('moreAtt');
  if (moreAtt) moreAtt.onclick = () => { const r = document.getElementById('restAtt'); r.hidden = !r.hidden; moreAtt.textContent = r.hidden ? `Ver los ${rest.length} restantes` : 'Ocultar'; };
  const panels = {
    watch: () => ({ title: 'A vigilar', note: 'Dentro de rango, pero avanzando de forma sostenida hacia un límite.', rows: sm.watch.map(w => line('vi', 'Vigilar', w.key, `Sube o baja hacia ${w.toward}: ${valTxt(w.first)} en ${yr(w.first)} a ${valTxt(w.last)} en ${yr(w.last)}`)) }),
    improved: () => ({ title: sm.improved.length === 1 ? 'Mejoró' : 'Mejoraron', note: 'Estaban fuera de rango y volvieron al rango.', rows: sm.improved.map(i => line('me', 'Mejoró', i.key, `Antes ${valTxt(i.previous)} en ${yr(i.previous)}`)) }),
    history: () => ({ title: 'Historial', note: 'Estuvieron fuera de rango más de una vez, o ya no se miden, y hoy no son motivo de alerta.', rows: sm.history.map(h => line('na', h.stale ? 'Sin medición reciente' : 'Ya en rango', h.key, `Último ${valTxt(h.last)} (${fd(h.last.collected_on)}) · fuera de rango en ${h.times_out} de ${h.results}`)) }),
  };
  view().querySelectorAll('[data-more]').forEach(b => b.onclick = () => {
    const panel = document.getElementById('morepanel'), same = S.sumMore === b.dataset.more;
    S.sumMore = same ? null : b.dataset.more;
    view().querySelectorAll('[data-more]').forEach(x => x.classList.toggle('on', x.dataset.more === S.sumMore));
    if (!S.sumMore) { panel.hidden = true; return; }
    const d = panels[S.sumMore]();
    panel.innerHTML = `<div class="sec-h"><h3>${d.title}</h3><span class="tip">${d.note}</span></div><div class="fl card" style="padding:6px 16px">${d.rows.join('')}</div>`;
    panel.hidden = false; bindOpen(panel); panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
  const bindOpen = root => root.querySelectorAll('[data-k]').forEach(el => el.onclick = () => openDetail(el.dataset.k, series));
  bindOpen(view());
  S.sumMore = null;
}

function openDetail(k, series) {
  S.sel = k;
  renderSummaryDetail(series);
  document.getElementById('scrim').hidden = false;
  document.body.classList.add('noscroll');
  document.getElementById('scrim').onclick = closeDetail;
}
function closeDetail() {
  S.sel = null;
  const d = document.getElementById('detail'), sc = document.getElementById('scrim');
  if (d) d.hidden = true; if (sc) sc.hidden = true;
  document.body.classList.remove('noscroll');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.sel) closeDetail(); });

function distOut(o) {
  if (o.value_num == null) return 0;
  if (o.ref_high != null && o.value_num > o.ref_high) return (o.value_num - o.ref_high) / (Math.abs(o.ref_high) || 1);
  if (o.ref_low != null && o.value_num < o.ref_low) return (o.ref_low - o.value_num) / (Math.abs(o.ref_low) || 1);
  return 0;
}

function markSel() {}

function normMethod(m) { return (m || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(); }

const qlabel = o => (o.value_text != null && o.value_text !== '') ? o.value_text : fnum(o.value_num);
// "Ausentes" = "ausente", "Positiva" = "positivo": una misma categoría aunque cambie la forma de escribirla.
const qkey = t => normMethod(String(t)).split(' ').map(w => w.length > 3 ? w.replace(/s$/, '').replace(/[ao]$/, '') : w).join(' ');

// Qué mide el análisis, en palabras simples (información general; no interpreta el resultado de la persona).
const aboutBox = k => S.catalog[k]?.about ? `<div class="about"><b>Qué mide</b><p>${esc(S.catalog[k].about)}</p></div>` : '';

function renderSummaryDetail(series) {
  const box = document.getElementById('detail');
  if (!S.sel) { box.hidden = true; return; }
  const all = series[S.sel];
  if (all[all.length - 1].value_num == null) return renderQualDetail(series);
  const pts = series[S.sel].filter(o => o.value_num != null), l = pts[pts.length - 1];
  // Rango del estudio más reciente que lo traiga (algunos laboratorios no lo imprimen para todo).
  const refPt = [...pts].reverse().find(o => o.ref_low != null || o.ref_high != null) || null;
  const methods = [...new Map(pts.filter(o => o.method).map(o => [normMethod(o.method), o.method])).values()];
  box.innerHTML = `<div class="dh"><div><h2>${esc(name(S.sel))}</h2>
      <p>${esc(l.unit)} · ${pts.length} ${pts.length === 1 ? 'resultado' : 'resultados'}, ${pts[0].collected_on.slice(0, 4)}${pts.length > 1 ? ' a ' + l.collected_on.slice(0, 4) : ''}</p></div>
      <div class="tabs"><button id="tg" aria-pressed="${S.view === 'g'}">Gráfica</button><button id="tt" aria-pressed="${S.view === 't'}">Tabla</button></div><button class="mini" id="dclose" aria-label="Cerrar el detalle">Cerrar ✕</button></div>
    ${aboutBox(S.sel)}
    ${methods.length > 1 ? `<div class="mixed">Ojo: estos resultados se midieron con métodos distintos (${methods.map(esc).join(', ')}). Compara la tendencia con cautela.</div>` : ''}
    <div class="chartbox" id="chart" ${S.view === 'g' ? '' : 'hidden'}></div>
    <div class="tblwrap" ${S.view === 't' ? '' : 'hidden'}><table><thead><tr><th>Fecha</th><th>Resultado</th><th>Estado</th><th>Referencia</th><th>Estudio</th><th>Método</th></tr></thead><tbody>
      ${[...pts].reverse().map(o => `<tr><td>${fd(o.collected_on)}</td><td><b>${vnum(o)}</b> ${esc(o.unit)}</td><td>${pill(o.status)}</td><td>${esc(refText(o))}${refNote(o)}</td><td>${esc(o.document_title)}</td><td>${o.entered_manually ? 'Agregado a mano' : esc(o.method || '—')}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="legend"><span><i class="lg-line"></i>Tus resultados</span>${refPt ? `<span><i class="lg-band"></i>${refPt.ref_note ? `${esc(refPt.ref_note)} (provisional)` : `Rango de referencia${refPt === l ? ' del último estudio' : ` (del estudio del ${fd(refPt.collected_on)}; el último no lo trae)`}`}: ${esc(bandText(refPt))}</span>` : ''}</div>`;
  box.hidden = false;
  document.getElementById('dclose').onclick = closeDetail;
  document.getElementById('tg').onclick = () => { S.view = 'g'; renderSummaryDetail(series); };
  document.getElementById('tt').onclick = () => { S.view = 't'; renderSummaryDetail(series); };
  if (S.view === 'g') drawChart(pts, refPt);
}


function renderQualDetail(series) {
  const box = document.getElementById('detail');
  const pts = series[S.sel], l = pts[pts.length - 1];
  const methods = [...new Map(pts.filter(o => o.method).map(o => [normMethod(o.method), o.method])).values()];
  const kinds = new Set(pts.map(o => qkey(qlabel(o)))), outs = pts.filter(o => OUT(o.status));
  const summary = pts.length === 1 ? `Un solo estudio: «${qlabel(l)}»${OUT(l.status) ? ', fuera de lo esperado' : ''}.`
    : kinds.size === 1 ? `Siempre «${qlabel(l)}» en los ${pts.length} estudios${outs.length ? ', fuera de lo esperado' : ', dentro de lo esperado'}.`
    : `Ha cambiado: ${kinds.size} resultados distintos en ${pts.length} estudios. Último: «${qlabel(l)}»${OUT(l.status) ? ' (fuera de lo esperado)' : ''}.${outs.length ? ` Fuera de lo esperado en ${outs.length} de ${pts.length}.` : ''}`;
  box.innerHTML = `<div class="dh"><div><h2>${esc(name(S.sel))}</h2>
      <p>Resultado de texto · ${pts.length} ${pts.length === 1 ? 'resultado' : 'resultados'}, ${pts[0].collected_on.slice(0, 4)}${pts.length > 1 ? ' a ' + l.collected_on.slice(0, 4) : ''}</p></div>
      <div class="tabs"><button id="tg" aria-pressed="${S.view === 'g'}">Gráfica</button><button id="tt" aria-pressed="${S.view === 't'}">Tabla</button></div><button class="mini" id="dclose" aria-label="Cerrar el detalle">Cerrar ✕</button></div>
    ${aboutBox(S.sel)}
    <p class="lead" style="font-size:16px;margin:0">${esc(summary)}</p>
    ${methods.length > 1 ? `<div class="mixed">Ojo: estos resultados se midieron con métodos distintos (${methods.map(esc).join(', ')}).</div>` : ''}
    <div class="chartbox" id="chart" ${S.view === 'g' ? '' : 'hidden'}></div>
    <div class="tblwrap" ${S.view === 't' ? '' : 'hidden'}><table><thead><tr><th>Fecha</th><th>Resultado</th><th>Estado</th><th>Lo esperado</th><th>Estudio</th><th>Método</th></tr></thead><tbody>
      ${[...pts].reverse().map(o => `<tr><td>${fd(o.collected_on)}</td><td><b>${esc(qlabel(o))}</b>${o.unit ? ' ' + esc(o.unit) : ''}</td><td>${pill(o.status)}</td><td>${esc(o.ref_printed || '—')}</td><td>${esc(o.document_title)}</td><td>${esc(o.method || '—')}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="legend"><span><i class="lg-line"></i>Tus resultados</span><span><i class="lg-band"></i>Lo esperado según el informe${l.ref_printed ? ': ' + esc(l.ref_printed) : ''}</span></div>`;
  box.hidden = false;
  document.getElementById('dclose').onclick = closeDetail;
  document.getElementById('tg').onclick = () => { S.view = 'g'; renderSummaryDetail(series); };
  document.getElementById('tt').onclick = () => { S.view = 't'; renderSummaryDetail(series); };
  if (S.view === 'g') drawQualChart(pts);
}

// Una fila por resultado distinto ("Negativo", "Positivo"…); cada estudio es un punto en su fila.
// Si siempre cae en la misma fila, la línea plana dentro de la banda verde dice "sigue igual".
function drawQualChart(pts) {
  const box = document.getElementById('chart');
  const W = Math.max(box.clientWidth, 280);
  const cats = [];
  pts.forEach(o => {
    const k = qkey(qlabel(o)); let c = cats.find(x => x.key === k);
    if (!c) cats.push(c = { key: k, label: qlabel(o), st: [] });
    c.st.push(o.status);
  });
  cats.forEach(c => { c.ok = c.st.includes('ok') && !c.st.some(OUT); });
  cats.sort((a, b) => Number(b.ok) - Number(a.ok));            // lo esperado abajo, dentro de la banda
  const m = { l: W < 520 ? 96 : 132, r: 24, t: 14, b: 42 };
  const rh = 44, ih = Math.max(1, cats.length) * rh, H = m.t + ih + m.b, iw = W - m.l - m.r;
  const rowTop = i => m.t + ih - (i + 1) * rh, Yc = i => rowTop(i) + rh / 2;
  const X = i => m.l + iw * (i + 0.5) / pts.length;
  const catIdx = o => cats.findIndex(c => c.key === qkey(qlabel(o)));
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Evolución de ${esc(name(S.sel))}">`;
  cats.forEach((c, i) => {
    if (c.ok) s += `<rect x="${m.l}" y="${rowTop(i)}" width="${iw}" height="${rh}" fill="var(--band)" stroke="var(--band-line)" stroke-dasharray="3 3"/>`;
    s += `<line x1="${m.l}" x2="${m.l + iw}" y1="${rowTop(i)}" y2="${rowTop(i)}" stroke="var(--line)" opacity=".7"/>`;
    const t = c.label.length > 18 ? c.label.slice(0, 17) + '…' : c.label;
    s += `<text class="ax" x="${m.l - 8}" y="${Yc(i) + 4}" text-anchor="end" style="font-weight:600"><title>${esc(c.label)}</title>${esc(t)}</text>`;
  });
  s += `<line x1="${m.l}" x2="${m.l + iw}" y1="${m.t + ih}" y2="${m.t + ih}" stroke="var(--line)"/>`;
  const every = Math.max(1, Math.ceil(pts.length * 64 / iw));
  let prevYear = null;
  pts.forEach((o, i) => {
    if (i % every && i !== pts.length - 1) return;
    const d = new Date(ts(o.collected_on)), yr = d.getUTCFullYear();
    s += `<text class="ax" x="${X(i)}" y="${H - 24}" text-anchor="middle">${d.toLocaleDateString('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' })}</text>`;
    if (yr !== prevYear) s += `<text class="ax" x="${X(i)}" y="${H - 8}" text-anchor="middle" style="font-weight:600">${yr}</text>`;
    prevYear = yr;
  });
  const P = pts.map((o, i) => [X(i), Yc(catIdx(o))]);
  s += `<polyline points="${P.map(q => q.join(',')).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
  P.forEach((q, i) => s += `<circle cx="${q[0]}" cy="${q[1]}" r="${i === P.length - 1 ? 6 : 5}" fill="${OUT(pts[i].status) ? 'var(--warn)' : 'var(--accent)'}" stroke="var(--surface)" stroke-width="2"/>`);
  s += `<line id="xh" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" stroke="var(--muted)" stroke-dasharray="3 3" style="display:none"/>`;
  s += `<rect id="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/></svg><div class="tip-box" id="tip" hidden></div>`;
  box.innerHTML = s;
  const svg = box.querySelector('svg'), tip = document.getElementById('tip'), xh = document.getElementById('xh');
  const show = cx => {
    const r = svg.getBoundingClientRect(), x = (cx - r.left) * (W / r.width);
    let bi = 0, bd = 1e9; P.forEach((q, i) => { const d = Math.abs(q[0] - x); if (d < bd) { bd = d; bi = i; } });
    const q = P[bi], o = pts[bi], sc = r.width / W;
    xh.setAttribute('x1', q[0]); xh.setAttribute('x2', q[0]); xh.style.display = '';
    tip.hidden = false;
    tip.innerHTML = `<span>${fd(o.collected_on)}</span><b>${esc(qlabel(o))}</b><span>${o.status ? STL[o.status] : 'Sin referencia'}</span><span>${esc(o.document_title)}</span>${o.entered_manually ? '<span>Agregado a mano</span>' : o.method ? `<span>Método: ${esc(o.method)}</span>` : ''}`;
    const tw = tip.offsetWidth; let left = q[0] * sc + 14; if (left + tw > r.width) left = q[0] * sc - tw - 14;
    tip.style.left = Math.max(0, left) + 'px'; tip.style.top = Math.max(0, q[1] * sc - 30) + 'px';
  };
  const hit = document.getElementById('hit');
  hit.addEventListener('pointermove', e => show(e.clientX));
  hit.addEventListener('pointerdown', e => show(e.clientX));
  hit.addEventListener('pointerleave', () => { tip.hidden = true; xh.style.display = 'none'; });
}

function niceStep(range, n) { const raw = range / n, m = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / m; return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * m; }

function bandText(o) {
  const f = v => fnum(v) + ' ' + o.unit, printed = o.ref_printed || '';
  const strictHigh = /<(?!\s*=)/.test(printed), strictLow = />(?!\s*=)/.test(printed);
  if (o.ref_low != null && o.ref_high != null) return `${fnum(o.ref_low)} a ${f(o.ref_high)}`;
  if (o.ref_high != null) return `${strictHigh ? 'menos de' : 'hasta'} ${f(o.ref_high)}`;
  return `${strictLow ? 'más de' : 'desde'} ${f(o.ref_low)}`;
}

function drawChart(pts, refPt) {
  const box = document.getElementById('chart');
  const W = Math.max(box.clientWidth, 280), H = W < 520 ? 250 : 320;
  const m = { l: W < 520 ? 40 : 48, r: W < 520 ? 52 : 64, t: 14, b: 42 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const l = pts[pts.length - 1], vals = pts.map(o => o.value_num);
  // Pocos resultados (lo normal): cada uno en su lugar, con espacio parejo y su fecha debajo; así
  // dos estudios del mismo mes o año no quedan encimados. Muchos: escala de tiempo con años.
  const ordinal = pts.length <= 12;
  let t0 = ts(pts[0].collected_on), t1 = ts(l.collected_on);
  const padT = Math.max((t1 - t0) * 0.04, 60 * 864e5); t0 -= padT; t1 += padT;
  const lims = refPt ? [refPt.ref_low, refPt.ref_high].filter(v => v != null) : [];
  let mn = Math.min(...vals, ...lims), mx = Math.max(...vals, ...lims);
  const pad = (mx - mn) * 0.18 || Math.abs(mx) * 0.2 || 1; mn -= pad; mx += pad;
  const step = niceStep(mx - mn, 4);
  mn = Math.floor(mn / step) * step; mx = Math.ceil(mx / step) * step; if (mn < 0 && Math.min(...vals) >= 0) mn = 0;
  const Y = v => m.t + (1 - (v - mn) / (mx - mn)) * ih;
  const X = ordinal ? i => m.l + iw * (i + 0.5) / pts.length : i => m.l + (ts(pts[i].collected_on) - t0) / (t1 - t0) * iw;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Evolución de ${esc(name(S.sel))}">`;
  if (lims.length) {
    const top = Y(refPt.ref_high ?? mx), bot = Y(refPt.ref_low ?? mn);
    s += `<rect x="${m.l}" y="${top}" width="${iw}" height="${Math.max(0, bot - top)}" fill="var(--band)" stroke="var(--band-line)" stroke-dasharray="3 3"/>`;
  }
  for (let v = mn; v <= mx + 1e-9; v += step) s += `<line x1="${m.l}" x2="${m.l + iw}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" opacity=".7"/><text class="ax" x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${Number(v.toFixed(3))}</text>`;
  if (ordinal) {
    const every = Math.max(1, Math.ceil(pts.length * 64 / iw));  // no encimar fechas en pantallas angostas
    let prevYear = null;
    pts.forEach((o, i) => {
      if (i % every && i !== pts.length - 1) return;
      const d = new Date(ts(o.collected_on)), yr = d.getUTCFullYear();
      const dm = d.toLocaleDateString('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' });
      s += `<text class="ax" x="${X(i)}" y="${H - 24}" text-anchor="middle">${dm}</text>`;
      if (yr !== prevYear) s += `<text class="ax" x="${X(i)}" y="${H - 8}" text-anchor="middle" style="font-weight:600">${yr}</text>`;
      prevYear = yr;
    });
  } else {
    const y0 = new Date(t0).getUTCFullYear() + 1, y1 = new Date(t1).getUTCFullYear(), every = Math.ceil((y1 - y0 + 1) / 8);
    for (let y = y0; y <= y1; y += every) {
      const x = m.l + (Date.UTC(y, 0, 1) - t0) / (t1 - t0) * iw;
      s += `<line x1="${x}" x2="${x}" y1="${m.t}" y2="${m.t + ih}" stroke="var(--line)" opacity=".7"/><text class="ax" x="${x}" y="${H - 16}" text-anchor="middle">${y}</text>`;
    }
  }
  const P = pts.map((o, i) => [X(i), Y(o.value_num)]);
  s += `<polyline points="${P.map(q => q.join(',')).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
  P.forEach((q, i) => s += `<circle class="pt" cx="${q[0]}" cy="${q[1]}" r="${i === P.length - 1 ? 5 : 4}" fill="${OUT(pts[i].status) ? 'var(--warn)' : 'var(--accent)'}" stroke="var(--surface)" stroke-width="2"/>`);
  const lp = P[P.length - 1];
  s += `<text x="${lp[0] + 10}" y="${lp[1] + 4}" fill="var(--ink)" style="font:600 13px var(--font)">${fnum(l.value_num)}</text>`;
  s += `<line id="xh" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" stroke="var(--muted)" stroke-dasharray="3 3" style="display:none"/>`;
  s += `<rect id="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/></svg><div class="tip-box" id="tip" hidden></div>`;
  box.innerHTML = s;
  const svg = box.querySelector('svg'), tip = document.getElementById('tip'), xh = document.getElementById('xh');
  const show = cx => {
    const r = svg.getBoundingClientRect(), x = (cx - r.left) * (W / r.width);
    let bi = 0, bd = 1e9; P.forEach((q, i) => { const d = Math.abs(q[0] - x); if (d < bd) { bd = d; bi = i; } });
    const q = P[bi], o = pts[bi], sc = r.width / W;
    xh.setAttribute('x1', q[0]); xh.setAttribute('x2', q[0]); xh.style.display = '';
    tip.hidden = false;
    tip.innerHTML = `<span>${fd(o.collected_on)}</span><b>${vnum(o)} ${esc(o.unit)}</b><span>${o.status ? STL[o.status] : 'Sin referencia'}</span><span>${esc(o.document_title)}</span>${o.entered_manually ? '<span>Agregado a mano</span>' : o.method ? `<span>Método: ${esc(o.method)}</span>` : ''}`;
    const tw = tip.offsetWidth; let left = q[0] * sc + 14; if (left + tw > r.width) left = q[0] * sc - tw - 14;
    tip.style.left = Math.max(0, left) + 'px'; tip.style.top = Math.max(0, q[1] * sc - 30) + 'px';
  };
  const hit = document.getElementById('hit');
  hit.addEventListener('pointermove', e => show(e.clientX));
  hit.addEventListener('pointerdown', e => show(e.clientX));
  hit.addEventListener('pointerleave', () => { tip.hidden = true; xh.style.display = 'none'; });
}

/* ---------- Documentos: subir y revisar ---------- */

const STATE = { pendiente: 'Pendiente de revisar', revisada: 'Revisado', descartada: 'Descartado' };

// Renglones que House leyó pero que no quedaron entre los resultados: nada se pierde en silencio.
function notSaved(d) {
  if (d.gaps?.length) return `<details class="hist"><summary><b class="warnline">${d.gaps.length} ${d.gaps.length === 1 ? 'informe incompleto' : 'informes incompletos'}</b></summary><ul class="nsl">${d.gaps.map(g => `<li><b>${esc(g.study_name)}</b><small>No encontré ${g.missing.map(esc).join(' ni ')}.</small></li>`).join('')}</ul></details>`;
  const rows = d.not_saved || [], lost = rows.filter(r => r.lost), dup = rows.length - lost.length;
  if (!rows.length) return '';
  const head = lost.length ? `<b class="warnline">${lost.length} ${lost.length === 1 ? 'renglón' : 'renglones'} ${d.review_state === 'revisada' ? 'sin guardar' : 'por resolver'}</b>` : `<span class="tip">${dup} repetido${dup === 1 ? '' : 's'} (ya guardado${dup === 1 ? '' : 's'})</span>`;
  return `<details class="hist"><summary>${head}</summary><ul class="nsl">${rows.map(r => `<li><b>${esc(r.printed_name)}</b> ${esc(r.value_printed || '')} ${esc(r.unit_printed || '')}<small>${esc(r.reason)}</small></li>`).join('')}</ul>
    ${d.review_state === 'pendiente' && lost.length ? `<button class="mini" data-reread="${d.id}" title="Vuelve a leer el original con la versión actual de House">Volver a leer</button>` : ''}
    ${d.review_state === 'revisada' && lost.length ? `<button class="mini" data-complete="${d.id}" title="Vuelve a leer el original con la versión actual de House y te deja resolver solo estos renglones">Completar estos renglones</button>` : ''}</details>`;
}

async function renderDocs() {
  const docs = await api(`/api/people/${S.subject}/documents`);
  view().innerHTML = `
    <section class="card cfg">
      <h2>Subir un estudio</h2>
      <label class="drop" id="drop"><b>Arrastra aquí un PDF o una imagen, o haz clic para elegirlos</b>
        <span class="tip">Laboratorio e informes en PDF (digitales o escaneados) e imágenes de radiografías o ultrasonidos en PNG o JPG. Se guardan cifrados en esta Mac.</span>
        <input type="file" id="file" accept="application/pdf,.pdf,image/png,image/jpeg,.png,.jpg,.jpeg" hidden multiple></label>
      <div id="up"></div>
    </section>
    <section class="card cfg">
      <h2>Subir una receta</h2>
      <label class="drop" id="rxdrop"><b>Foto o PDF de una receta</b>
        <span class="tip">Claude lee la receta y te propone los medicamentos; tú los revisas junto al original antes de guardarlos. Se envía la imagen a Claude, y ahí se ven datos personales impresos.</span>
        <input type="file" id="rxfile" accept="application/pdf,.pdf,image/png,image/jpeg,.png,.jpg,.jpeg" hidden></label>
      <div id="rxup"></div>
    </section>
    <section class="card cfg">
      <h2>Subir composición corporal (InBody)</h2>
      <label class="drop" id="bsdrop"><b>Foto o PDF del reporte de una báscula de bioimpedancia</b>
        <span class="tip">Claude lee peso, masa muscular, grasa, agua, metabolismo basal, etc. y te propone las medidas; tú las revisas y confirmas. Se envía la imagen a Claude, y ahí se ven datos personales impresos.</span>
        <input type="file" id="bsfile" accept="application/pdf,.pdf,image/png,image/jpeg,.png,.jpg,.jpeg" hidden></label>
      <div id="bsup"></div>
    </section>
    <section class="card cfg"><div class="bar"><h2>Estudios</h2></div>
      ${docs.length ? `<div class="tblwrap"><table><thead><tr><th>Estudio</th><th>Fecha</th><th>Estado</th><th></th></tr></thead><tbody>
        ${docs.map(d => `<tr><td><b>${esc(d.title)}</b>${d.filename && d.filename.replace(/\.(pdf|png|jpe?g)$/i, '') !== d.title ? `<small class="fname" style="display:block" title="Nombre del archivo que subiste">Archivo: ${esc(d.filename)}</small>` : ''}<small style="display:block;color:var(--muted)">${{ imagen: 'Informe de estudio', receta: 'Receta' }[d.doc_type] || 'Laboratorio'}</small></td><td>${fd(d.collected_on)}</td>
          <td>${d.review_state === 'revisada' ? (d.doc_type === 'imagen' ? `Revisado · ${d.results} ${d.results === 1 ? 'informe' : 'informes'}` : d.doc_type === 'receta' ? `Revisado · ${d.results} ${d.results === 1 ? 'medicamento' : 'medicamentos'}` : `Revisado · ${d.results} resultados`) : esc(STATE[d.review_state])}${notSaved(d)}</td>
          <td><div class="acts" style="display:flex;gap:6px;flex-wrap:wrap">
            ${d.review_state === 'pendiente' ? `<button class="mini" data-rev="${d.id}">Revisar</button>` : ''}
            <a class="mini" href="/api/documents/${d.id}/file" target="_blank" rel="noopener" style="text-decoration:none">Ver original</a>
            <button class="mini" data-rename-doc="${d.id}" data-t="${esc(d.title)}" title="Corregir el nombre (el del archivo original se conserva)">Renombrar</button>
            <button class="mini dn" data-del="${d.id}" data-t="${esc(d.title)}">Borrar</button></div></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="tip">Aún no hay estudios.</p>'}
    </section>`;
  const rxIn = document.getElementById('rxfile'), rxDrop = document.getElementById('rxdrop');
  rxIn.onchange = () => rxIn.files[0] && uploadPrescription(rxIn.files[0]);
  rxDrop.ondragover = e => { e.preventDefault(); rxDrop.classList.add('over'); };
  rxDrop.ondragleave = () => rxDrop.classList.remove('over');
  rxDrop.ondrop = e => { e.preventDefault(); rxDrop.classList.remove('over'); if (e.dataTransfer.files[0]) uploadPrescription(e.dataTransfer.files[0]); };
  const bsIn = document.getElementById('bsfile'), bsDrop = document.getElementById('bsdrop');
  bsIn.onchange = () => bsIn.files[0] && uploadBodyScan(bsIn.files[0]);
  bsDrop.ondragover = e => { e.preventDefault(); bsDrop.classList.add('over'); };
  bsDrop.ondragleave = () => bsDrop.classList.remove('over');
  bsDrop.ondrop = e => { e.preventDefault(); bsDrop.classList.remove('over'); if (e.dataTransfer.files[0]) uploadBodyScan(e.dataTransfer.files[0]); };
  const drop = document.getElementById('drop'), input = document.getElementById('file');
  input.onchange = () => uploadFiles([...input.files]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); uploadFiles([...e.dataTransfer.files]); };
  view().querySelectorAll('[data-rev]').forEach(b => b.onclick = () => openReview(Number(b.dataset.rev)));
  view().querySelectorAll('[data-rename-doc]').forEach(b => b.onclick = async () => {
    const name = prompt('Nombre corregido del documento (el nombre del archivo original se conserva):', b.dataset.t);
    if (name == null || name.trim() === b.dataset.t) return;
    try { await api(`/api/documents/${b.dataset.renameDoc}/title`, { method: 'PUT', body: { name } }); toast('Nombre actualizado.'); renderDocs(); } catch (e) { toast(e.message); }
  });
  view().querySelectorAll('[data-reread]').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Leyendo de nuevo…';
    try {
      const r = await api(`/api/documents/${b.dataset.reread}/reread`, { method: 'POST' });
      toast(`Leído de nuevo: ${r.rows} renglones.`); renderDocs();
    } catch (e) { b.disabled = false; b.textContent = 'Volver a leer'; toast(e.message); }
  });
  view().querySelectorAll('[data-complete]').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Leyendo de nuevo…';
    try {
      const r = await api(`/api/documents/${b.dataset.complete}/complete-read`, { method: 'POST' });
      if (!r.open) { toast('Ya no falta ningún renglón.'); renderDocs(); return; }
      openReview(Number(b.dataset.complete), { complete: true });
    } catch (e) { b.disabled = false; b.textContent = 'Completar estos renglones'; toast(e.message); }
  });
  view().querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm(`¿Borrar «${b.dataset.t}» y todos sus resultados? Si es una receta, los medicamentos que guardaste se conservan. No se puede deshacer.`)) return;
    await api(`/api/documents/${b.dataset.del}`, { method: 'DELETE' }); toast('Estudio borrado.'); renderDocs();
  });
}

async function uploadBodyScan(f) {
  const up = document.getElementById('bsup');
  if (!confirm('Se enviará la imagen del reporte a Claude para leerla. Ahí se ven datos personales impresos (nombre, ID…). ¿Continuar?')) return;
  up.innerHTML = `<p class="tip"><span class="spin"></span>Claude está leyendo «${esc(f.name)}»… (unos 15 segundos)</p>`;
  const form = new FormData(); form.append('file', f);
  try {
    const r = await api(`/api/people/${S.subject}/body-scans`, { method: 'POST', form });
    up.innerHTML = ''; toast(`Leí ${r.metrics} medidas. Revísalas antes de guardar.`); openReview(r.document_id);
  } catch (e) {
    if (e.status === 409 && e.detail?.document_id) { up.innerHTML = ''; toast('Ese reporte ya estaba cargado.'); return; }
    up.innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}

async function uploadPrescription(f) {
  const up = document.getElementById('rxup');
  if (!confirm('Se enviará la imagen de la receta a Claude para leerla. Ahí se ven datos personales impresos (nombre, médico…). ¿Continuar?')) return;
  up.innerHTML = `<p class="tip"><span class="spin"></span>Claude está leyendo «${esc(f.name)}»… (unos 15 segundos)</p>`;
  const form = new FormData(); form.append('file', f);
  try {
    const r = await api(`/api/people/${S.subject}/prescriptions`, { method: 'POST', form });
    up.innerHTML = ''; toast(`Leí ${r.medications} ${r.medications === 1 ? 'medicamento' : 'medicamentos'}. Revísalos antes de guardar.`); openReview(r.document_id);
  } catch (e) {
    if (e.status === 409 && e.detail?.document_id) { up.innerHTML = ''; toast('Esa receta ya estaba cargada.'); return; }
    up.innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}

async function uploadFiles(files) {
  const up = document.getElementById('up');
  let last = null, imgs = 0;
  for (const f of files) {
    up.innerHTML = `<p class="tip"><span class="spin"></span>Leyendo «${esc(f.name)}»… Con Claude puede tardar hasta un minuto.</p>`;
    const form = new FormData(); form.append('file', f);
    try {
      const r = await api(`/api/people/${S.subject}/documents`, { method: 'POST', form });
      if (r.kind === 'foto') { toast('Imagen guardada. La verás en Estudios, junto a su informe.'); imgs++; continue; }
      last = r.document_id;
      toast(r.kind === 'imagen' ? `Leí ${r.rows} ${r.rows === 1 ? 'informe' : 'informes'} de imagen en esta Mac.` : r.reader === 'claude' ? `El lector básico no conocía este formato; lo leyó Claude: ${r.rows} renglones.` : r.reader === 'basico' ? `Leído con el lector básico: ${r.rows} renglones.` : `Leído: ${r.rows} renglones.`);
    } catch (e) {
      if (e.status === 409) { toast('Ese archivo ya estaba cargado.'); continue; }
      up.innerHTML = `<p class="err">«${esc(f.name)}»: ${esc(e.message)}</p>`;
      return;
    }
  }
  up.innerHTML = '';
  if (imgs && !last && imgs === files.length) { S.tab = 'img'; renderShell(); return; }
  if (last && files.length === 1) openReview(last); else renderDocs();
}

const PROBLEMS = {
  valor_critico: 'Valor muy fuera de lo normal: confirma que esté bien leído',
  no_respaldada_por_el_documento: 'No aparece tal cual en el documento',
  analito_desconocido: 'Análisis no reconocido',
  unidad_no_reconocida: 'Unidad no reconocida',
  valor_no_numerico: 'Valor no numérico',
  valor_implausible: 'Valor poco probable: revísalo',
  mismo_valor_en_otra_unidad: 'Es el mismo resultado en otra unidad: no se guardará dos veces',
  aparece_mas_de_una_vez: 'Aparece más de una vez en este estudio',
};

// Renglón que House no reconoció: elegir del menú, ignorarlo (control, leyenda) o crear un análisis propio.
const titleCase = t => { const l = String(t || '').toLowerCase().trim(); return l.charAt(0).toUpperCase() + l.slice(1); };
function unknownTools(r) {
  if (r.assigned) return '';
  if (r.ignore) return `<span class="flag">Ignorado: no es un resultado</span> <button class="mini" data-unignore="${r.id}">Deshacer</button>`;
  const numeric = r.value_num != null || /^[<>]?\s*\d+([.,]\d+)?$/.test(String(r.value_printed || '').trim());
  const form = r.newOpen ? `<div class="newan"><label>Nombre<input type="text" id="nn-${r.id}" value="${esc(titleCase(r.printed_name.replace(/\(.*?\)/g, '')))}" maxlength="80"></label>
      <label>Unidad<input type="text" id="nu-${r.id}" value="${esc(r.unit_printed || '')}" maxlength="20" placeholder="Por ejemplo mg/dL"></label>
      <label>Tipo<select id="nk-${r.id}"><option value="num" ${numeric ? 'selected' : ''}>Un número</option><option value="qual" ${numeric ? '' : 'selected'}>Un texto (Negativo, Normal…)</option></select></label>
      ${r.newErr ? `<span class="err">${esc(r.newErr)}</span>` : ''}
      <span style="display:flex;gap:6px"><button class="mini" data-newsave="${r.id}">Crear y usar</button><button class="mini" data-newcancel="${r.id}">Cancelar</button></span></div>` : '';
  return `<span class="flag">No reconocido: elige qué análisis es, ignóralo o crea uno nuevo</span>
    <span class="rowact"><button class="mini" data-ignore="${r.id}">Ignorar</button><button class="mini" data-newan="${r.id}">Crear análisis nuevo</button></span>${form}`;
}

async function openReview(id, opts = {}) {
  const complete = !!opts.complete;  // estudio ya revisado: solo se muestra lo que no quedó guardado
  const [d, layout] = await Promise.all([api(`/api/documents/${id}`), api(`/api/documents/${id}/layout`).catch(() => ({ pages: [], boxes: {} }))]);
  if (d.document.doc_type === 'imagen') return openImagingReview(id, d, layout);
  if (d.document.doc_type === 'receta') return openPrescriptionReview(id, d);
  if (d.body_scan) return openBodyScanReview(id, d);
  const seenSame = new Set();  // mismo análisis, valor y unidad impresos otra vez: solo se acepta el primero
  const repeatOfEarlier = r => { const k = [r.analyte_key, r.value_printed, r.unit_printed].join('|'); const dup = seenSame.has(k); seenSame.add(k); return dup; };
  const rows = d.rows.filter(r => !complete || r.unsaved).map(r => ({ ...r, accept: !!r.analyte_key && !repeatOfEarlier(r) && !(complete && r.problems.includes('aparece_mas_de_una_vez')) && !r.problems.includes('no_respaldada_por_el_documento') && !r.problems.includes('unidad_no_reconocida') && !r.problems.includes('valor_no_numerico') && !r.problems.includes('mismo_valor_en_otra_unidad') }));
  let onlyFlags = false, showAi = false, active = null, addOpen = false;
  const manual = [];  // resultados que faltaban y la persona agregó a mano
  const byName = new Map(Object.entries(S.catalog).map(([k, a]) => [a.name.toLowerCase(), k]));
  const keyFromName = t => byName.get(String(t || '').trim().toLowerCase()) || null;
  view().innerHTML = `<div class="rv">
    <div class="pages" id="pages" aria-label="Estudio original">
      ${layout.pages.length ? layout.pages.map(pg => `<div class="pg" data-n="${pg.n}"><img src="/api/documents/${id}/pages/${pg.n}" alt="Página ${pg.n}" loading="lazy" style="aspect-ratio:${pg.width}/${pg.height}"><div class="hl" hidden></div></div>`).join('')
        : `<iframe class="pdf" src="/api/documents/${id}/file" title="Estudio original"></iframe>`}
    </div>
    <section class="card cfg" id="side"></section></div>`;
  const pagesEl = document.getElementById('pages');
  const focusRow = rid => {
    active = rid;
    pagesEl.querySelectorAll('.hl').forEach(h => h.hidden = true);
    view().querySelectorAll('tr[data-row]').forEach(tr => tr.classList.toggle('active', Number(tr.dataset.row) === rid));
    const b = layout.boxes[rid]; if (!b) return;
    const pg = layout.pages[b.page - 1], el = pagesEl.querySelector(`.pg[data-n="${b.page}"]`), hl = el.querySelector('.hl');
    const padX = 3, padY = 2;
    Object.assign(hl.style, { left: `${(b.x0 - padX) / pg.width * 100}%`, top: `${(b.top - padY) / pg.height * 100}%`,
      width: `${(b.x1 - b.x0 + 2 * padX) / pg.width * 100}%`, height: `${(b.bottom - b.top + 2 * padY) / pg.height * 100}%` });
    hl.hidden = false;
    const target = el.offsetTop + (b.top / pg.height) * el.clientHeight - pagesEl.clientHeight / 2;
    pagesEl.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  };
  const draw = () => {
    const shown = rows.filter(r => !onlyFlags || r.needs_attention || !r.analyte_key);
    const n = rows.filter(r => r.accept).length;
    document.getElementById('side').innerHTML = `
      <div class="bar"><h2>${complete ? 'Completa' : 'Revisa'} «${esc(d.document.title)}»</h2><div style="display:flex;gap:6px"><button class="mini" id="reread" ${complete ? 'hidden' : ''} title="Útil si House mejoró o si conectaste Claude">Volver a leer</button><a class="mini" href="/api/documents/${id}/file" target="_blank" rel="noopener" style="text-decoration:none">Abrir PDF</a><button class="mini" id="back">Volver</button></div></div>
      <datalist id="catlist">${[...byName.keys()].sort().map(n => `<option value="${esc(S.catalog[byName.get(n)].name)}">`).join('')}</datalist>
      ${complete ? '<p class="tip"><b>Solo aparece lo que no quedó guardado.</b> Lo que ya guardaste no se toca ni se duplica.</p>' : ''}
      <p class="tip">Toca un resultado para ver su renglón resaltado en el original. Corrige lo necesario y desmarca lo que no quieras guardar. Si House no reconoció un análisis, dile cuál es; si falta uno, agrégalo abajo. Solo lo que confirmes entra a tu expediente.</p>
      <div class="bar"><label class="kv"><b>Fecha de toma</b><input type="date" id="date" value="${esc(d.document.collected_on || '')}" ${complete ? 'disabled' : ''}></label>
        <label><input type="checkbox" id="only" ${onlyFlags ? 'checked' : ''}> Solo lo que requiere atención</label>
        <button class="mini" id="ai">${showAi ? 'Ocultar' : 'Ver'} lo que vio la IA</button></div>
      ${showAi ? `<div class="anon">${esc(d.ai_saw)}</div><p class="tip">Datos personales quitados antes de enviar: ${Object.entries(d.redactions).map(([k, v]) => `${esc(k.toLowerCase())} (${v})`).join(', ') || 'ninguno'}.</p>` : ''}
      <div class="tblwrap"><table class="rt"><thead><tr><th></th><th>Análisis</th><th>Resultado</th><th>Referencia y estado</th></tr></thead><tbody>
        ${shown.map(r => `<tr data-row="${r.id}" class="${r.accept ? (r.needs_attention ? 'fl2' : '') : 'skip'} ${r.id === active ? 'active' : ''}">
          <td><input type="checkbox" data-acc="${r.id}" ${r.accept ? 'checked' : ''} ${(r.assigned || r.analyte_key) ? '' : 'disabled'} aria-label="Guardar"></td>
          <td><b>${esc(r.assigned ? S.catalog[r.assigned].name : (r.name || r.printed_name))}</b><small>${esc(r.printed_name)}${r.section ? ' · ' + esc(r.section) : ''}${layout.boxes[r.id] ? ` · pág. ${layout.boxes[r.id].page}` : ''}</small>
            ${r.analyte_key ? '' : `<input list="catlist" data-assign="${r.id}" placeholder="¿Qué análisis es? Escribe para buscar" value="${r.assigned ? esc(S.catalog[r.assigned].name) : ''}" style="width:100%;margin-top:4px">${unknownTools(r)}`}
            ${r.problems.filter(p => p !== 'analito_desconocido').map(p => `<span class="flag">${esc(PROBLEMS[p] || p)}</span>`).join('')}
            ${r.converted ? `<span class="flag">Convertido de ${esc(r.value_printed)} ${esc(r.unit_printed || '')}</span>` : ''}</td>
          <td>${r.qualifier ? `<span title="El laboratorio imprimió ${esc(r.qualifier)} antes del valor: es un límite del método">${esc(r.qualifier)}</span> ` : ''}<input type="text" data-val="${r.id}" value="${esc(r.edited ?? (r.assigned ? r.value_printed : r.value_num != null ? fnum(r.value_num) : r.value_text ?? r.value_printed))}"> ${esc(r.assigned ? (r.unit_printed || '') : (r.unit || ''))}</td>
          <td>${esc(r.ref_printed || '—')}<div style="margin-top:4px">${pill(r.status)}</div></td></tr>`).join('')}
      </tbody></table></div>
      ${manual.length ? `<h3 style="margin:8px 0 0;font-size:15px">Agregados a mano</h3><div class="tblwrap"><table class="rt"><tbody>${manual.map((m, i) => `<tr><td><b>${esc(S.catalog[m.analyte_key].name)}</b><span class="flag">Agregado a mano</span></td><td>${esc(m.value)} ${esc(m.unit || '')}</td><td>${esc(m.ref || '—')}</td><td><button class="mini dn" data-rm="${i}">Quitar</button></td></tr>`).join('')}</tbody></table></div>` : ''}
      ${addOpen ? `<form class="fg2 card" id="mf" style="padding:14px"><b>Agregar un resultado que falta</b>
        <label>Análisis<input type="text" name="name" list="catlist" autocomplete="off" placeholder="Empieza a escribir…" required></label>
        <label>Valor<input type="text" name="value" autocomplete="off" placeholder="Por ejemplo 92 o Negativo" required></label>
        <label>Unidad<input type="text" name="unit" autocomplete="off" placeholder="(la habitual del análisis)"></label>
        <label>Rango que imprime el estudio (opcional)<input type="text" name="ref" autocomplete="off" placeholder="Por ejemplo 70 - 99"></label>
        <p class="err" id="me"></p><div class="bar"><button type="button" class="mini" id="mcancel">Cancelar</button><button class="btn">Agregar a la lista</button></div></form>` : `<div><button class="mini" id="addm">+ Agregar un resultado que falta</button></div>`}
      <p class="err" id="e"></p>
      <div class="bar">${complete ? '<span></span>' : '<button class="btn danger" id="discard">Descartar estudio</button>'}<button class="btn" id="ok">Confirmar ${n + manual.length} ${n + manual.length === 1 ? 'resultado' : 'resultados'}</button></div>`;
    document.getElementById('back').onclick = () => renderDocs();
    document.getElementById('reread').onclick = async () => {
      if (!confirm('¿Volver a leer este estudio? Se pierden las correcciones que no hayas confirmado.')) return;
      const b = document.getElementById('reread'); b.disabled = true; b.textContent = 'Leyendo…';
      try { const r = await api(`/api/documents/${id}/reread`, { method: 'POST' }); toast(`Leído de nuevo: ${r.rows} renglones.`); openReview(id); }
      catch (err) { document.getElementById('e').textContent = err.message; b.disabled = false; b.textContent = 'Volver a leer'; }
    };
    document.getElementById('only').onchange = e => { onlyFlags = e.target.checked; draw(); };
    const row = el => rows.find(x => x.id == el.dataset[Object.keys(el.dataset)[0]]);
    view().querySelectorAll('[data-ignore]').forEach(b => b.onclick = () => { const r = row(b); r.ignore = true; r.accept = false; draw(); });
    view().querySelectorAll('[data-unignore]').forEach(b => b.onclick = () => { row(b).ignore = false; draw(); });
    view().querySelectorAll('[data-newan]').forEach(b => b.onclick = () => { row(b).newOpen = true; draw(); });
    view().querySelectorAll('[data-newcancel]').forEach(b => b.onclick = () => { const r = row(b); r.newOpen = false; r.newErr = ''; draw(); });
    view().querySelectorAll('[data-newsave]').forEach(b => b.onclick = async () => {
      const r = row(b), name = document.getElementById(`nn-${r.id}`).value.trim();
      try {
        const made = await api('/api/catalog/custom', { method: 'POST', body: { name, unit: document.getElementById(`nu-${r.id}`).value.trim(), kind: document.getElementById(`nk-${r.id}`).value, alias: r.printed_name } });
        S.catalog[made.key] = made.info; byName.set(made.info.name.toLowerCase(), made.key);
        r.assigned = made.key; r.accept = true; r.newOpen = false; r.newErr = ''; toast(`Análisis «${made.info.name}» creado.`); draw();
      } catch (err) { r.newErr = err.message; draw(); }
    });
    view().querySelectorAll('[data-assign]').forEach(i => i.onchange = () => {
      const r = rows.find(x => x.id == i.dataset.assign), k = keyFromName(i.value);
      r.assigned = k; r.accept = !!k; draw();
    });
    view().querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { manual.splice(Number(b.dataset.rm), 1); draw(); });
    const addBtn = document.getElementById('addm');
    if (addBtn) addBtn.onclick = () => { addOpen = true; draw(); document.querySelector('#mf [name=name]').focus(); };
    const mf = document.getElementById('mf');
    if (mf) {
      const f = mf.elements;  // por nombre explícito: un campo "name" no debe confundirse con mf.name
      document.getElementById('mcancel').onclick = () => { addOpen = false; draw(); };
      f.name.onchange = () => { const k = keyFromName(f.name.value); if (k) { f.unit.value = S.catalog[k].unit || ''; f.value.placeholder = S.catalog[k].kind === 'num' ? 'Un número, por ejemplo 92' : 'Por ejemplo Negativo'; } };
      mf.onsubmit = ev => {
        ev.preventDefault();
        const k = keyFromName(f.name.value), err = document.getElementById('me');
        if (!k) return (err.textContent = 'Elige el análisis de la lista: escribe unas letras y selecciónalo.');
        const v = f.value.value.trim();
        if (S.catalog[k].kind === 'num' && !isFinite(Number(v.replace(',', '.')))) return (err.textContent = 'Este análisis es un número: escribe solo la cifra.');
        manual.push({ analyte_key: k, value: v, unit: f.unit.value.trim() || null, ref: f.ref.value.trim() || null });
        addOpen = false; draw();
      };
    }
    view().querySelectorAll('tr[data-row]').forEach(tr => {
      tr.onclick = e => { if (!['checkbox', 'text'].includes(e.target.type) && e.target.tagName !== 'A') focusRow(Number(tr.dataset.row)); };
      const val = tr.querySelector('[data-val]');
      if (val) val.onfocus = () => focusRow(Number(tr.dataset.row));
    });
    if (active) focusRow(active);  // al redibujar (marcar, corregir) se conserva el renglón resaltado
    view().querySelectorAll('[data-acc]').forEach(c => c.onchange = () => { rows.find(r => r.id == c.dataset.acc).accept = c.checked; draw(); });
    view().querySelectorAll('[data-val]').forEach(i => i.oninput = () => { rows.find(r => r.id == i.dataset.val).edited = i.value; });
    if (!complete) document.getElementById('discard').onclick = async () => {
      if (!confirm('¿Descartar este estudio? Se borra el original y no se guarda ningún resultado.')) return;
      await api(`/api/documents/${id}`, { method: 'DELETE' }); toast('Estudio descartado.'); renderDocs();
    };
    document.getElementById('ok').onclick = async () => {
      const e = document.getElementById('e'), date = document.getElementById('date').value;
      if (!date) return (e.textContent = 'Indica la fecha de toma del estudio.');
      const decisions = [];
      for (const r of rows) {
        const dd = { row_id: r.id, accept: r.accept };
        if (r.ignore) { dd.accept = false; dd.ignore = true; decisions.push(dd); continue; }
        if (r.accept && r.assigned) { dd.analyte_key = r.assigned; dd.printed_value = (r.edited ?? r.value_printed).trim(); decisions.push(dd); continue; }
        if (r.accept && r.edited != null) {
          if (r.value_num != null) {
            const v = Number(String(r.edited).replace(',', '.'));
            if (!isFinite(v) || String(r.edited).trim() === '') return (e.textContent = `«${r.name || r.printed_name}»: escribe un número.`);
            dd.value_num = v;
          } else dd.value_text = r.edited.trim();
        }
        decisions.push(dd);
      }
      try {
        const res = await api(`/api/documents/${id}/review`, { method: 'POST', body: { collected_on: date, decisions, manual, complete } });
        toast(`Guardé ${res.saved} ${res.saved === 1 ? 'resultado' : 'resultados'} en tu expediente.`);
        if (complete) renderDocs(); else { S.tab = 'res'; renderShell(); }
      } catch (err) { e.textContent = err.message; }
    };
  };
  draw();
}

/* ---------- Expediente clínico ---------- */

const F = (name, label, type = 'text', extra = {}) => ({ name, label, type, ...extra });
const STCLS = { 'En control': 'c', 'En tratamiento': 'a', 'Seguimiento': 'a', 'Resuelta': 'r' };
const CLIN = {
  allergy: { key: 'allergies', title: 'Alergias', pendingLabel: 'Alergias a medicamentos', add: 'Agregar alergia', none: 'Sin alergias a medicamentos conocidas',
    fields: [F('category', 'Tipo de alergia', 'select', { options: 'allergy_categories' }), F('substance', 'Sustancia', 'text', { list: 'allergy' }), F('reaction', 'Reacción (opcional)'), F('notes', 'Notas (opcional)', 'area')],
    line: a => `<b>${esc(a.substance)}</b>${a.reaction ? ' · ' + esc(a.reaction) : ''}`, sub: a => a.notes },
  problem: { key: 'problems', title: 'Problemas de salud', add: 'Agregar problema', none: 'Sin problemas de salud conocidos',
    fields: [F('name', 'Problema', 'text', { list: 'problem' }), F('status', 'Estado', 'select', { options: 'statuses' }), F('since_year', 'Desde (año)', 'text', { ph: '2021' }), F('notes', 'Notas (opcional)', 'area', { max: 12000 })],
    line: p => `<b>${esc(p.name)}</b>${p.since_year ? ` <span class="s">desde ${esc(p.since_year)}</span>` : ''}`, sub: p => p.notes,
    badge: p => `<span class="sp ${STCLS[p.status] || 'c'}">${esc(p.status)}</span>` },
  medication: { key: 'medications', title: 'Medicamentos', add: 'Agregar medicamento', none: 'Sin medicamentos actuales',
    fields: [F('brand', 'Nombre comercial (si lo conoces)', 'text', { ph: 'Por ejemplo Glucophage' }), F('active_ingredient', 'Sustancia activa', 'text', { list: 'medication', ph: 'Por ejemplo metformina' }), F('dose', 'Dosis (por ejemplo 50 mg al día)'), F('reason', 'Para qué'), F('prescriber', 'Médico que lo indicó (opcional)'),
      F('since_year', 'Desde (año)', 'text', { ph: '2022' }), F('until_year', 'Hasta (año, si ya lo suspendiste)', 'text', { ph: '2024' }), F('active', 'Lo tomo actualmente', 'check'),
      F('bad_reaction', 'Me cae mal: qué me pasa con este medicamento (opcional)', 'text', { ph: 'Por ejemplo: al día siguiente hay sangre en mis heces' })],
    badge: m => (m.bad_reaction ? `<span class="sp a" title="${esc(m.bad_reaction)}">Me cae mal</span>` : '') + (m.document_id ? `<a class="mini" href="/api/documents/${m.document_id}/file" target="_blank" rel="noopener" style="text-decoration:none">Ver receta</a>` : ''),
    line: m => `<b>${esc(m.brand || m.name)}</b>${m.dose ? ' · ' + esc(m.dose) : ''}`,
    sub: m => [m.bad_reaction && 'Me cae mal: ' + m.bad_reaction, m.brand && m.active_ingredient && 'Sustancia activa: ' + m.active_ingredient, m.reason, (m.problems || []).filter(p => p.name.toLowerCase() !== (m.reason || '').toLowerCase()).length && 'ligado a ' + m.problems.map(p => p.name).join(', '), m.prescriber && 'indicado por ' + m.prescriber, m.since_year && 'desde ' + m.since_year + (m.until_year ? ' hasta ' + m.until_year : '')].filter(Boolean).join(', ') },
  supplement: { key: 'supplements', title: 'Suplementos', add: 'Agregar suplemento',
    fields: [F('name', 'Suplemento', 'text', { list: 'supplement' }), F('dose', 'Cuánto y cuándo (por ejemplo 1 scoop al día)'), F('brand', 'Marca (opcional)'), F('reason', 'Para qué (opcional)'),
      F('since_year', 'Desde (año)', 'text', { ph: '2024' }), F('until_year', 'Hasta (año, si ya lo dejaste)', 'text', { ph: '2025' }), F('active', 'Lo tomo actualmente', 'check')],
    line: m => `<b>${esc(m.name)}</b>${m.dose ? ' · ' + esc(m.dose) : ''}`,
    sub: m => [m.brand, m.reason, m.since_year && 'desde ' + m.since_year + (m.until_year ? ' hasta ' + m.until_year : '')].filter(Boolean).join(' · ') },
  family: { key: 'family', title: 'Antecedentes familiares', add: 'Agregar antecedente', none: 'Sin antecedentes familiares relevantes',
    fields: [F('relative', 'Parentesco', 'text', { list: 'relative' }), F('condition', 'Condición', 'text', { list: 'problem' })],
    line: f => `<b>${esc(f.relative)}</b> · ${esc(f.condition)}`, sub: () => '' },
  procedure: { key: 'procedures', title: 'Cirugías', add: 'Agregar cirugía', none: 'Sin cirugías previas',
    fields: [F('name', 'Procedimiento', 'text', { list: 'procedure' }), F('year', 'Año', 'text', { ph: '2012' }), F('notes', 'Notas (opcional)', 'area')],
    line: x => `<b>${esc(x.name)}</b>${x.year ? ` <span class="s">${esc(x.year)}</span>` : ''}`, sub: x => x.notes },
  vaccine: { key: 'vaccines', title: 'Vacunas', add: 'Agregar vacuna',
    fields: [F('name', 'Vacuna', 'text', { list: 'vaccine' }), F('brand', 'Marca (opcional)', 'text', { ph: 'Por ejemplo Pfizer' }), F('given_on', 'Fecha', 'date'), F('dose_label', 'Dosis', 'select', { options: 'dose_options', optional: true }), F('lot', 'Lote (opcional)'), F('place', 'Dónde (opcional)')],
    line: v => `<b>${esc(v.name)}</b> <span class="s">${fd(v.given_on)}</span>`, sub: v => [v.dose_label, v.brand, v.place].filter(Boolean).join(' · ') },
  symptom: { key: 'symptoms', title: 'Síntomas y observaciones', add: 'Anotar algo que noté',
    fields: [F('occurred_on', 'Fecha', 'date'), F('what', 'Qué noté', 'text', { ph: 'Por ejemplo: sangre en las heces' }), F('related', 'Después de qué (medicamento, comida, actividad; opcional)', 'text', { ph: 'Por ejemplo: ibuprofeno' }), F('notes', 'Detalles (opcional): color, cantidad, cuántos días…', 'area')],
    line: s => `<b>${esc(s.what)}</b> <span class="s">${fd(s.occurred_on)}</span>`, sub: s => [s.related && 'después de ' + s.related, s.notes].filter(Boolean).join(' · ') },
  consultation: { key: 'consultations', title: 'Consultas', add: 'Agregar consulta',
    fields: [F('occurred_on', 'Fecha', 'date'), F('reason', 'Motivo o resumen'), F('doctor', 'Médico (opcional)'), F('specialty', 'Especialidad (opcional)'), F('notes', 'Resumen o notas (opcional)', 'area', { max: 20000 }), F('transcript', 'Transcripción de la cita (opcional; no se envía a la IA)', 'area', { max: 60000 })],
    line: c => `<b>${esc(c.reason)}</b> <span class="s">${fd(c.occurred_on)}</span>`, sub: c => [c.specialty, c.doctor, c.notes].filter(Boolean).join(' · ') },
};
const HAS_NONE = ['allergy', 'problem', 'medication', 'family', 'procedure'];
const EV_KIND = { consulta: 'Consulta', receta: 'Receta', cuerpo: 'Composición corporal', sintoma: 'Síntoma', laboratorio: 'Laboratorio', imagen: 'Imagen', estudio: 'Otro estudio', vacuna: 'Vacuna', cirugia: 'Cirugía' };

// Un mismo medicamento (misma sustancia activa) que aparece en varias recetas se muestra en una sola fila.
const medKey = m => String(m.active_ingredient || m.name || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const medWhen = m => m.prescribed_on || (m.since_year ? `${m.since_year}-01-01` : '');
function groupMeds(list) {
  const groups = new Map();
  list.forEach(m => (groups.get(medKey(m)) || groups.set(medKey(m), []).get(medKey(m))).push(m));
  return [...groups.values()].map(g => g.sort((a, b) => medWhen(b).localeCompare(medWhen(a)))).sort((a, b) => medWhen(b[0]).localeCompare(medWhen(a[0])));
}

async function renderClinical() {
  view().innerHTML = '<p class="tip"><span class="spin"></span>Cargando…</p>';
  const [c, sug, cand, hl] = await Promise.all([api(`/api/people/${S.subject}/clinical`), S.sug ? Promise.resolve(S.sug) : api('/api/clinical/suggestions'), api(`/api/people/${S.subject}/link-candidates`), api(`/api/people/${S.subject}/health`)]);
  S.sug = sug;
  const reload = () => renderClinical();
  const itemsOf = kind => c[CLIN[kind].key];
  // Estudios ligados a un padecimiento: fichas compactas, siempre visibles, con un menú para ligar más.
  const KL = { document: 'Laboratorio', imaging: 'Informe', analyte: 'Análisis', medication: 'Tratamiento', procedure: 'Cirugía', consultation: 'Consulta' };
  const plinks = p => {
    const have = new Set(p.links.map(l => l.kind + ':' + l.ref));
    const opts = (kind, arr) => arr.filter(x => !have.has(kind + ':' + x.ref)).map(x => `<option value="${kind}:${esc(x.ref)}">${esc(x.title)}${x.active === false ? ' (suspendido)' : ''}${x.date ? ' · ' + (x.date.length > 4 ? fd(x.date) : x.date) : ''}</option>`).join('');
    const groups = [['Tratamientos', 'medication', cand.medications], ['Consultas', 'consultation', cand.consultations || []], ['Informes de estudios', 'imaging', cand.imaging], ['Laboratorios', 'document', cand.documents], ['Análisis', 'analyte', cand.analytes], ['Cirugías', 'procedure', cand.procedures]]
      .map(([label, kind, arr]) => { const o = opts(kind, arr); return o ? `<optgroup label="${label}">${o}</optgroup>` : ''; }).join('');
    const chip = l => `<span class="lchip"><span class="lk ${l.kind}">${KL[l.kind]}</span><span class="lt">${esc(l.title)}${l.value ? ' · ' + esc(l.value) : ''}${l.extra ? ' · ' + esc(l.extra) : ''}${l.active === false ? ' · suspendido' : ''}${l.date ? ' · ' + fd(l.date) : ''}</span>
      ${l.document_id ? `<a href="/api/documents/${l.document_id}/file" target="_blank" rel="noopener" title="Ver original" aria-label="Ver el original de ${esc(l.title)}">↗</a>` : ''}
      <button data-punlink="${p.id}:${l.id}" title="Quitar" aria-label="Quitar la relación con ${esc(l.title)}">✕</button></span>`;
    const sugg = (cand.medications || []).filter(m => m.reason && plainName(m.reason) === plainName(p.name) && !have.has('medication:' + m.ref));
    const suggChips = sugg.map(m => `<button class="lchip add" data-psug="${p.id}:${m.ref}" title="Ese medicamento dice que es para este padecimiento">Sugerido: ${esc(m.title)} ＋</button>`).join('');
    return `<div class="plw">${p.links.map(chip).join('')}${suggChips}${groups ? `<label class="lchip add"><select data-plink="${p.id}" aria-label="Ligar un estudio a este padecimiento"><option value="">＋ Ligar estudio o tratamiento</option>${groups}</select></label>` : ''}</div>`;
  };
  const row = (kind, it, cfg = CLIN[kind]) => { const sub = cfg.sub(it);
    return `<li><span>${cfg.line(it)}${sub ? (sub.length > 220 || sub.includes('\n') ? `<details class="ntv"><summary>${esc(sub.split('\n')[0].replace(/[.…]+$/, '').slice(0, 110))}… <span class="s">(ver notas completas)</span></summary><div class="ntb">${esc(sub)}</div></details>` : `<br><span class="s">${esc(sub)}</span>`) : ''}${it.transcript ? `<details class="ntv"><summary>Transcripción de la cita</summary><div class="ntb">${esc(it.transcript)}</div></details>` : ''}${it.duplicate && !cfg.noDup ? '<span class="flag">Aparece más de una vez</span>' : ''}</span>
      <span class="rowact">${cfg.badge ? cfg.badge(it) : ''}<button class="mini" data-edit="${kind}:${it.id}">Editar</button><button class="mini dn" data-del="${kind}:${it.id}">Quitar</button></span>${kind === 'problem' ? plinks(it) : ''}</li>`; };
  // Vacunas agrupadas por vacuna: «COVID-19 · 2 dosis · última mayo 2022», con cada dosis al abrir.
  const plainName = t => String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
  const vaccineCard = (cfg, items) => {
    const groups = new Map();
    items.forEach(v => { const k = plainName(v.name); (groups.get(k) || groups.set(k, []).get(k)).push(v); });
    const list = [...groups.values()].map(g => g.sort((a, b) => b.given_on.localeCompare(a.given_on))).sort((a, b) => b[0].given_on.localeCompare(a[0].given_on));
    const dose = { line: v => `<b>${esc(v.dose_label || 'Dosis')}</b> <span class="s">${fd(v.given_on)}</span>`, sub: v => [v.brand, v.lot && 'lote ' + v.lot, v.place].filter(Boolean).join(' · ') };
    return `<section class="card xc"><h3>${cfg.title}<button class="mini add-x" data-add="vaccine">${cfg.add}</button></h3>
      ${list.map(g => `<details class="hist vgrp" ${g.length === 1 ? 'open' : ''}><summary><b>${esc(g[0].name)}</b> · ${g.length} ${g.length === 1 ? 'dosis' : 'dosis'} · última ${fd(g[0].given_on)}</summary>
        <ul class="xl">${g.map(v => row('vaccine', v, dose)).join('')}</ul></details>`).join('')}</section>`;
  };
  // Alergias agrupadas por tipo (medicamentos, alimentos, ambientales…).
  const allergyRows = arr => (c.allergy_categories || []).map(cat => [cat, arr.filter(a => (a.category || 'Medicamento') === cat)]).filter(([, l]) => l.length)
    .map(([cat, l]) => `<li class="grp"><span class="s"><b>${esc(cat === 'Medicamento' ? 'A medicamentos' : cat === 'Ambiental' ? 'Ambientales (polen, ácaros…)' : cat === 'Alimento' ? 'A alimentos' : 'Otras')}</b></span></li>${l.map(a => row('allergy', a)).join('')}`).join('');
  // Medicamentos: una fila por sustancia; si se repite en varias recetas, se abre para ver cada vez.
  const occ = { noDup: true, badge: CLIN.medication.badge,
    line: m => `<b>${medWhen(m) ? (m.prescribed_on ? fd(m.prescribed_on) : m.since_year) : 'Sin fecha'}</b>${m.dose ? ' · ' + esc(m.dose) : ''}`,
    sub: m => [m.brand, m.notes, m.prescriber && 'indicado por ' + m.prescriber].filter(Boolean).join(' · ') };
  const medRows = arr => groupMeds(arr).map(g => {
    if (g.length === 1) return row('medication', g[0]);
    const last = g[0], brands = [...new Set(g.map(m => m.brand).filter(Boolean))];
    const title = brands.length ? brands.join(' / ') : (last.active_ingredient || last.name);
    const dt = last.prescribed_on ? fd(last.prescribed_on) : last.since_year || '';
    return `<li class="mgrp"><details><summary><span><b>${esc(title)}</b>${last.active_ingredient && brands.length ? ` <span class="s">· ${esc(last.active_ingredient)}</span>` : ''}</span>
      <span class="s">${g.length} veces${dt ? ' · última ' + dt : ''}</span></summary><ul class="xl">${g.map(m => row('medication', m, occ)).join('')}</ul></details></li>`;
  }).join('');
  // Antecedentes familiares agrupados por familiar (madre, padre, abuelos…); una condición que se repite se marca.
  const familyCard = (cfg, items) => {
    const order = S.sug.relative || [];
    const rank = r => { const i = order.indexOf(r); return i < 0 ? order.length : i; };
    const groups = new Map();
    items.forEach(f => (groups.get(f.relative) || groups.set(f.relative, []).get(f.relative)).push(f));
    const times = new Map();
    items.forEach(f => { const k = plainName(f.condition); times.set(k, new Set([...(times.get(k) || []), f.relative])); });
    const list = [...groups.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0], 'es'));
    const repeated = [...new Set(items.filter(f => times.get(plainName(f.condition)).size > 1).map(f => f.condition))];
    const chip = f => {
      const rep = times.get(plainName(f.condition)).size > 1;
      return `<span class="lchip ${rep ? 'rep' : ''}" ${rep ? `title="Se repite en ${times.get(plainName(f.condition)).size} familiares"` : ''}><span class="lt">${esc(f.condition)}</span>
        <button data-edit="family:${f.id}" title="Editar" aria-label="Editar ${esc(f.condition)}">✎</button><button data-del="family:${f.id}" title="Quitar" aria-label="Quitar ${esc(f.condition)}">✕</button></span>`;
    };
    return `<section class="card xc"><h3>${cfg.title}<button class="mini add-x" data-add="family">${cfg.add}</button></h3>
      <div class="fam">${list.map(([rel, fs]) => `<div class="famrow"><span class="famrel">${esc(rel)}</span><div class="plw" style="margin:0">${fs.map(chip).join('')}</div></div>`).join('')}</div>
      ${repeated.length ? `<p class="tip" style="margin:8px 0 0">Se repite en más de un familiar: <b>${repeated.map(esc).join(', ')}</b>.</p>` : ''}</section>`;
  };
  const card = kind => {
    const cfg = CLIN[kind]; let items = itemsOf(kind), past = [];
    if (kind === 'medication' || kind === 'supplement') { past = items.filter(m => !m.active); items = items.filter(m => m.active); }
    const listOf = arr => kind === 'medication' ? medRows(arr) : kind === 'allergy' ? allergyRows(arr) : arr.map(it => row(kind, it)).join('');
    if (kind === 'vaccine' && items.length) return vaccineCard(cfg, items);
    if (kind === 'family' && items.length) return familyCard(cfg, items);
    const confirmed = c.none.includes(kind);
    const empty = items.length ? '' : HAS_NONE.includes(kind)
      ? (confirmed ? `<div class="alg none">${esc(cfg.none)}</div><button class="link" data-none-off="${kind}">Quitar la confirmación</button>`
        : `<p class="s" style="margin:0">Sin registrar todavía.</p><button class="mini" data-none="${kind}">Confirmar: ${esc(cfg.none.toLowerCase())}</button>`)
      : '<p class="s" style="margin:0">Sin registros.</p>';
    return `<section class="card xc"><h3>${cfg.title}<button class="mini add-x" data-add="${kind}">${cfg.add}</button></h3>
      ${items.length ? `<ul class="xl">${listOf(items)}</ul>` : empty}
      ${kind === 'allergy' && items.length && !drugAllergies && !confirmed ? `<button class="mini" data-none="allergy">Confirmar: ${esc(cfg.none.toLowerCase())}</button>` : ''}
      ${kind === 'allergy' && items.length && confirmed ? `<p class="s" style="margin:0">${esc(cfg.none)}. <button class="link" data-none-off="allergy">Quitar la confirmación</button></p>` : ''}
      ${past.length ? `<details class="hist"><summary>Suspendidos (${past.length})</summary><ul class="xl">${listOf(past)}</ul></details>` : ''}</section>`;
  };
  const chips = ['todo', ...Object.keys(EV_KIND)];
  const evs = c.timeline.filter(e => S.clinFilter === 'todo' || e.kind === S.clinFilter);
  const editing = S.clinEdit;

  // ---- Secciones: un resumen al inicio y una sección a la vez, para no ver todo junto.
  const SECTIONS = [['res', 'Resumen'], ['per', 'Perfil y medidas'], ['prob', 'Padecimientos', 'problem'], ['med', 'Medicamentos', 'medication'], ['sup', 'Suplementos', 'supplement'], ['alg', 'Alergias', 'allergy'],
    ['fam', 'Antecedentes', 'family'], ['vac', 'Vacunas', 'vaccine'], ['cir', 'Cirugías', 'procedure'], ['sin', 'Síntomas', 'symptom'], ['con', 'Consultas', 'consultation'], ['his', 'Historial']];
  const secOfKind = Object.fromEntries(SECTIONS.filter(x => x[2]).map(x => [x[2], x[0]]));
  const sec = SECTIONS.some(x => x[0] === S.expSec) ? S.expSec : 'res';
  const count = { problem: c.problems.filter(p => p.status !== 'Resuelta').length, medication: c.medications.filter(m => m.active).length, supplement: c.supplements.filter(m => m.active).length, allergy: c.allergies.length,
    family: c.family.length, vaccine: c.vaccines.length, procedure: c.procedures.length, symptom: c.symptoms.length, consultation: c.consultations.length };
  const drugAllergies = c.allergies.filter(a => !a.category || a.category === 'Medicamento').length;
  const pending = HAS_NONE.filter(k => !(k === 'allergy' ? drugAllergies : count[k]) && !c.none.includes(k));  // sin datos y sin confirmar que «no hay»
  const profileEmpty = !hl.profile.height_cm && !hl.profile.smoking && !hl.profile.alcohol && !hl.profile.exercise;
  const timelineHtml = (list, title) => `<section class="card xc"><h3>${title}</h3>
      ${title === 'Historial cronológico' ? `<div class="chips" role="group" aria-label="Filtrar por tipo">${chips.map(k => `<button class="chip" data-f="${k}" aria-pressed="${k === S.clinFilter}">${k === 'todo' ? 'Todo' : EV_KIND[k]}</button>`).join('')}</div>` : ''}
      <div class="tl">${list.length ? list.map(e => `<div class="ev"><div class="dt">${e.approx ? esc(e.date.slice(0, 4)) : fd(e.date)}</div><div>
          <div class="tt"><span class="ty">${e.modality && e.kind !== 'imagen' ? esc(e.modality) : EV_KIND[e.kind]}</span>${esc(e.title)}${e.flag ? ' ' + flagPill(e.flag) : ''}</div>
          ${e.subtitle ? `<div class="sb">${esc(e.subtitle)}</div>` : ''}
          ${e.ref ? `<a class="mini" href="/api/documents/${e.ref.id}/file" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-top:4px">Ver original</a>` : ''}</div></div>`).join('')
        : '<p class="tip">Todavía no hay eventos. Sube estudios o agrega consultas, vacunas y cirugías.</p>'}</div></section>`;
  const dash = () => {
    const act = c.problems.filter(p => p.status !== 'Resuelta'), meds = c.medications.filter(m => m.active);
    const mini = (arr, fn, target, empty) => arr.length ? `<ul class="xmini">${arr.slice(0, 4).map(fn).join('')}</ul>${arr.length > 4 ? `<button class="link" data-xs="${target}">Ver los ${arr.length}</button>` : ''}` : `<p class="tip" style="margin:0">${empty}</p>`;
    const head = (t, target) => `<h3>${t}<button class="mini" data-xs="${target}">Ver todo</button></h3>`;
    const recent = c.timeline.slice(0, 5).map(e => `<li class="xev"><span class="s">${e.approx ? esc(e.date.slice(0, 4)) : fd(e.date)}</span><span class="ty">${e.modality && e.kind !== 'imagen' ? esc(e.modality) : EV_KIND[e.kind]}</span><span class="xet">${esc(e.title)}</span></li>`).join('');
    return `${pending.length || profileEmpty ? `<section class="card xc xpend"><h3>Por completar</h3><p class="tip" style="margin:0">Estas secciones están vacías. Agrega lo que aplique o confirma que no hay nada.</p>
        ${profileEmpty ? '<div class="xprow"><span><b>Perfil de salud</b> <span class="s">talla, tabaquismo, alcohol, ejercicio… ayudan a orientarte mejor</span></span><span><button class="mini" data-xs="per">Completar</button></span></div>' : ''}
        ${pending.map(k => `<div class="xprow"><span><b>${esc(CLIN[k].pendingLabel || CLIN[k].title)}</b> <span class="s">sin registrar</span></span><span style="display:flex;gap:6px;flex-wrap:wrap"><button class="mini" data-add="${k}">Agregar</button><button class="mini" data-none="${k}">${esc(CLIN[k].none)}</button></span></div>`).join('')}</section>` : ''}
      <div class="xdash">
        <section class="card xc">${head('Perfil de salud', 'per')}${basicsHtml()}</section>
        <section class="card xc">${head('Padecimientos activos', 'prob')}${mini(act, p => `<li><b>${esc(p.name)}</b> <span class="s">${esc(p.status)}${p.links.length ? ` · ${p.links.length} ${p.links.length === 1 ? 'estudio o tratamiento ligado' : 'estudios o tratamientos ligados'}` : ''}</span></li>`, 'prob', 'Sin padecimientos activos.')}</section>
        <section class="card xc">${head('Medicamentos actuales', 'med')}${mini(groupMeds(meds).map(g => g[0]), m => `<li><b>${esc(m.brand || m.name)}</b>${m.active_ingredient && m.brand ? ` <span class="s">· ${esc(m.active_ingredient)}</span>` : ''}${m.dose ? ` <span class="s">· ${esc(m.dose)}</span>` : ''}</li>`, 'med', 'Sin medicamentos actuales.')}</section>
        <section class="card xc">${head('Suplementos actuales', 'sup')}${mini(c.supplements.filter(m => m.active), m => `<li><b>${esc(m.name)}</b>${m.dose ? ` <span class="s">· ${esc(m.dose)}</span>` : ''}</li>`, 'sup', 'Sin suplementos registrados.')}</section>
        <section class="card xc">${head('Alergias', 'alg')}${mini(c.allergies, a => `<li><b>${esc(a.substance)}</b> <span class="s">· ${esc((a.category || 'Medicamento').toLowerCase())}${a.reaction ? ' · ' + esc(a.reaction) : ''}</span></li>`, 'alg', c.none.includes('allergy') ? 'Sin alergias a medicamentos conocidas.' : 'Sin registrar.')}</section>
        <section class="card xc">${head('Síntomas y observaciones', 'sin')}${mini(c.symptoms, x => `<li><b>${esc(x.what)}</b> <span class="s">· ${fd(x.occurred_on)}${x.related ? ' · después de ' + esc(x.related) : ''}</span></li>`, 'sin', 'Nada anotado. Aquí puedes anotar lo que notes (por ejemplo, algo que te pasa después de un medicamento).')}</section>
        <section class="card xc">${head('Últimos eventos', 'his')}${recent ? `<ul class="xmini">${recent}</ul>` : '<p class="tip" style="margin:0">Todavía no hay eventos.</p>'}</section>
      </div>`;
  };
  const nav = `<nav class="xnav" role="tablist" aria-label="Secciones del expediente">${SECTIONS.map(([k, label, kind]) => `<button class="xpill" role="tab" data-xs="${k}" aria-selected="${k === sec}" aria-pressed="${k === sec}">${label}${kind ? `<span class="xn">${count[kind]}</span>` : ''}${(kind && pending.includes(kind)) || (k === 'per' && profileEmpty) ? '<i class="xdot" title="Sin registrar"></i>' : ''}</button>`).join('')}</nav>`;
  // Medicamentos sin sustancia activa: Claude la sugiere a partir del nombre y la persona confirma cada una.
  const missingIng = c.medications.filter(m => !m.active_ingredient).length;
  const medTools = () => !missingIng ? '' : `<div class="newan" style="margin-bottom:12px"><b>${missingIng} ${missingIng === 1 ? 'medicamento no tiene' : 'medicamentos no tienen'} sustancia activa</b>
    <span class="tip">Claude puede sugerirla a partir del nombre (solo se envían los nombres). Tú confirmas cada una; verifícala en la caja o en la receta.</span>
    ${S.sugIng ? (S.sugIng.length ? S.sugIng.map(x => `<div class="xprow"><span><b>${esc(x.name)}</b> → ${esc(x.active_ingredient)}</span><span style="display:flex;gap:6px"><button class="mini" data-acc-ing="${x.id}" data-ing="${esc(x.active_ingredient)}">Aceptar</button><button class="mini" data-skip-ing="${x.id}">Ignorar</button></span></div>`).join('') : '<span class="tip">Claude no está seguro de ninguna; puedes escribirlas al editar cada medicamento.</span>') : '<span><button class="mini" id="sugIng">Sugerir con Claude</button></span>'}</div>`;
  // ---- Perfil de salud y medidas
  const subj = S.people.find(p => p.id === S.subject) || S.me;
  const ageOf = b => { if (!b) return null; const d = new Date(b), t = new Date(); let a = t.getFullYear() - d.getFullYear(); if (t < new Date(t.getFullYear(), d.getMonth(), d.getDate())) a--; return a; };
  const MEAS = { weight_kg: ['Peso', 'kg'], blood_pressure: ['Presión arterial', 'mmHg'], heart_rate: ['Frecuencia cardiaca', 'lpm'], waist_cm: ['Cintura', 'cm'], body_fat_pct: ['Grasa corporal', '%'],
    skeletal_muscle_kg: ['Masa muscular esquelética', 'kg'], body_fat_kg: ['Masa grasa', 'kg'], lean_mass_kg: ['Masa magra', 'kg'], fat_free_mass_kg: ['Masa libre de grasa', 'kg'], body_water_l: ['Agua corporal total', 'L'],
    protein_kg: ['Proteínas', 'kg'], mineral_kg: ['Minerales', 'kg'], bone_mineral_kg: ['Mineral óseo', 'kg'], bmr_kcal: ['Metabolismo basal', 'kcal'], whr: ['Relación cintura-cadera', ''], fitness_score: ['Puntuación de fitness', 'puntos'] };
  const MANUAL = ['weight_kg', 'blood_pressure', 'heart_rate', 'waist_cm', 'body_fat_pct'];
  const mval = m => m.kind === 'blood_pressure' ? `${m.value}/${m.value2}` : m.value;
  const basicsHtml = () => {
    const p = hl.profile, l = hl.latest, age = ageOf(subj.birth_date);
    const bits = [`${subj.sex_at_birth === 'F' ? 'Mujer' : 'Hombre'}${age != null ? ', ' + age + ' años' : ''}`, p.height_cm && `talla ${p.height_cm} cm`, l.weight_kg && `peso ${l.weight_kg.value} kg`, hl.bmi && `IMC ${hl.bmi}`, l.blood_pressure && `presión ${mval(l.blood_pressure)}`].filter(Boolean);
    const habits = [p.smoking && 'tabaco: ' + p.smoking.toLowerCase(), p.alcohol && 'alcohol: ' + p.alcohol.toLowerCase(), p.exercise && 'ejercicio: ' + p.exercise.toLowerCase()].filter(Boolean);
    return `<p style="margin:0">${esc(bits.join(' · '))}</p>${habits.length ? `<p class="s" style="margin:4px 0 0">${esc(habits.join(' · '))}</p>` : '<p class="tip" style="margin:4px 0 0">Aún sin hábitos registrados.</p>'}`;
  };
  const bodyCard = scans => {
    const sc = scans[0], key = ['weight_kg', 'skeletal_muscle_kg', 'body_fat_kg', 'body_fat_pct', 'waist_hip_ratio', 'total_body_water_l', 'bmr_kcal', 'fitness_score'];
    const pick = sc.metrics.filter(m => key.includes(m.key)).sort((a, b) => key.indexOf(a.key) - key.indexOf(b.key));
    return `<section class="card xc" style="margin-top:12px"><h3>Composición corporal · ${fd(sc.measured_on)}<a class="mini" href="/api/documents/${sc.document_id}/file" target="_blank" rel="noopener" style="text-decoration:none">Ver original</a></h3>
      <div class="famrow" style="grid-template-columns:1fr"><div class="plw" style="margin:0">${pick.map(m => `<span class="lchip"><span class="lt"><b>${esc(m.name)}</b> ${m.value} ${esc(m.unit)}${rangeText(m) ? ` <span class="s">(normal ${rangeText(m)})</span>` : ''}</span>${EVAL_PILL(m.evaluation)}</span>`).join('')}</div></div>
      ${segTable(sc.segments)}${sc.notes ? `<p class="tip" style="margin:0">${esc(sc.notes)}</p>` : ''}
      ${scans.length > 1 ? `<p class="tip" style="margin:0">Reportes anteriores: ${scans.slice(1).map(x => `<a href="/api/documents/${x.document_id}/file" target="_blank" rel="noopener">${fd(x.measured_on)}</a>`).join(' · ')}</p>` : ''}</section>`;
  };
  const perPanel = () => {
    const p = hl.profile, ch = hl.choices, sel = (name, list) => `<select name="${name}"><option value="">Sin especificar</option>${list.map(o => `<option ${p[name] === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
    const txt = (name, ph = '') => `<input type="text" name="${name}" value="${esc(p[name] || '')}" maxlength="500" placeholder="${esc(ph)}" spellcheck="true" lang="es" style="width:100%">`;
    const grouped = MANUAL.map(k => [k, hl.measurements.filter(m => m.kind === k)]).filter(([, a]) => a.length);  // las de composición corporal salen en su tarjeta
    return `<div class="xpanel"><section class="card xc"><h3>Datos básicos y hábitos</h3>
      <p class="tip" style="margin:0">${esc(basicsHtml().replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim())}</p>
      <p class="tip" style="margin:0">Estos datos ayudan al asistente a orientarte (qué estudios o cuidados tienen sentido para tu edad y hábitos). Solo se envía a Claude lo que necesita, sin tu nombre.</p>
      <form id="hf" class="fg2" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">
        <label>Talla (cm)<input type="text" name="height_cm" inputmode="decimal" value="${esc(p.height_cm ?? '')}" placeholder="178"></label>
        <label>Tipo de sangre${sel('blood_type', ch.blood_type)}</label>
        <label>Tabaquismo${sel('smoking', ch.smoking)}</label><label>Detalle del tabaquismo${txt('smoking_detail', 'cigarros al día, años, cuándo lo dejaste')}</label>
        <label>Alcohol${sel('alcohol', ch.alcohol)}</label><label>Detalle del alcohol${txt('alcohol_detail', 'bebidas por semana')}</label>
        <label>Ejercicio${sel('exercise', ch.exercise)}</label><label>Qué haces${txt('exercise_detail', 'pesas, correr, deporte…')}</label>
        <label>Horas de sueño<input type="text" name="sleep_hours" inputmode="decimal" value="${esc(p.sleep_hours ?? '')}" placeholder="7"></label>
        <label>Alimentación${txt('diet', 'por ejemplo alta en proteína, vegetariana')}</label>
        <label>Ocupación${txt('occupation', 'trabajo de escritorio, turnos, exposición a químicos…')}</label>
        <label>Otras notas${txt('notes')}</label>
        <p class="err" id="hfe"></p><div class="bar"><span></span><button class="btn">Guardar</button></div></form></section>
      ${hl.body_scans.length ? bodyCard(hl.body_scans) : ''}
      <section class="card xc" style="margin-top:12px"><h3>Medidas con fecha</h3>
        <form id="mf2" class="fg2" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))">
          <label>Medida<select name="kind">${MANUAL.map(k => `<option value="${k}">${MEAS[k][0]}</option>`).join('')}</select></label>
          <label>Valor<input type="text" name="value" inputmode="decimal" required placeholder="82"></label>
          <label id="v2l" hidden>Diastólica<input type="text" name="value2" inputmode="decimal" placeholder="80"></label>
          <label>Fecha<input type="date" name="measured_on" value="${new Date().toISOString().slice(0, 10)}"></label>
          <div style="display:flex;align-items:end"><button class="btn">Agregar</button></div></form><p class="err" id="mfe"></p>
        ${grouped.length ? grouped.map(([k, arr]) => `<div class="famrow"><span class="famrel">${MEAS[k][0]}</span><div class="plw" style="margin:0">${arr.slice(0, 8).map(m => `<span class="lchip"><span class="lt">${mval(m)} ${MEAS[k][1]} · ${fd(m.measured_on)}</span><button data-rm-meas="${m.id}" title="Quitar" aria-label="Quitar esta medida">✕</button></span>`).join('')}</div></div>`).join('') : '<p class="tip" style="margin:0">Todavía no hay medidas. Agrega tu peso o tu presión para ver cómo cambian.</p>'}</section></div>`;
  };
  const body = sec === 'per' ? perPanel() : sec === 'res' ? dash() : sec === 'his' ? timelineHtml(evs, 'Historial cronológico') : `<div class="xpanel">${sec === 'med' ? medTools() : ''}${card(SECTIONS.find(x => x[0] === sec)[2])}</div>`;
  view().innerHTML = `
    <div id="clinform"></div>
    ${nav}
    ${body}`;

  const hf = document.getElementById('hf');
  if (hf) hf.onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(hf).entries());
    try { await api(`/api/people/${S.subject}/health/profile`, { method: 'PUT', body }); toast('Perfil guardado.'); reload(); } catch (err) { document.getElementById('hfe').textContent = err.message; }
  };
  const mf2 = document.getElementById('mf2');
  if (mf2) {
    const kindSel = mf2.elements.kind, v2 = document.getElementById('v2l');
    kindSel.onchange = () => { v2.hidden = kindSel.value !== 'blood_pressure'; mf2.elements.value.placeholder = kindSel.value === 'blood_pressure' ? '120' : '82'; };
    mf2.onsubmit = async e => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(mf2).entries());
      try { await api(`/api/people/${S.subject}/health/measurements`, { method: 'POST', body }); toast('Medida agregada.'); reload(); } catch (err) { document.getElementById('mfe').textContent = err.message; }
    };
  }
  view().querySelectorAll('[data-rm-meas]').forEach(b => b.onclick = async () => { await api(`/api/people/${S.subject}/health/measurements/${b.dataset.rmMeas}`, { method: 'DELETE' }); reload(); });
  const sg = document.getElementById('sugIng');
  if (sg) sg.onclick = async () => {
    sg.disabled = true; sg.textContent = 'Claude está pensando…';
    try { S.sugIng = (await api(`/api/people/${S.subject}/medications/suggest-ingredients`, { method: 'POST' })).suggestions; reload(); }
    catch (e) { sg.disabled = false; sg.textContent = 'Sugerir con Claude'; toast(e.message); }
  };
  view().querySelectorAll('[data-acc-ing]').forEach(b => b.onclick = async () => {
    try { await api(`/api/people/${S.subject}/medications/${b.dataset.accIng}/ingredient`, { method: 'PUT', body: { active_ingredient: b.dataset.ing } }); S.sugIng = S.sugIng.filter(x => x.id != b.dataset.accIng); toast('Sustancia activa guardada.'); reload(); }
    catch (e) { toast(e.message); }
  });
  view().querySelectorAll('[data-skip-ing]').forEach(b => b.onclick = () => { S.sugIng = S.sugIng.filter(x => x.id != b.dataset.skipIng); reload(); });
  view().querySelectorAll('[data-xs]').forEach(b => b.onclick = () => { S.expSec = b.dataset.xs; window.scrollTo({ top: 0 }); reload(); });
  view().querySelectorAll('[data-f]').forEach(b => b.onclick = () => { S.clinFilter = b.dataset.f; reload(); });
  view().querySelectorAll('[data-plink]').forEach(sel => sel.onchange = async () => {
    if (!sel.value) return;
    const [kind, ...rest] = sel.value.split(':');
    try { await api(`/api/people/${S.subject}/clinical/problem/${sel.dataset.plink}/links`, { method: 'POST', body: { kind, ref: rest.join(':') } }); toast('Estudio ligado.'); reload(); }
    catch (e) { toast(e.message); }
  });
  view().querySelectorAll('[data-psug]').forEach(b => b.onclick = async () => {
    const [pid, ref] = b.dataset.psug.split(':');
    try { await api(`/api/people/${S.subject}/clinical/problem/${pid}/links`, { method: 'POST', body: { kind: 'medication', ref } }); toast('Tratamiento ligado.'); reload(); } catch (e) { toast(e.message); }
  });
  view().querySelectorAll('[data-punlink]').forEach(b => b.onclick = async () => {
    const [pid, lid] = b.dataset.punlink.split(':');
    await api(`/api/people/${S.subject}/clinical/problem/${pid}/links/${lid}`, { method: 'DELETE' }); toast('Relación quitada.'); reload();
  });
  view().querySelectorAll('[data-add]').forEach(b => b.onclick = () => { S.clinEdit = { kind: b.dataset.add, item: null }; S.expSec = secOfKind[b.dataset.add] || sec; reload(); });
  view().querySelectorAll('[data-edit]').forEach(b => b.onclick = () => {
    const [kind, id] = b.dataset.edit.split(':'); const found = itemsOf(kind).find(x => x.id == id);
    S.clinEdit = { kind, item: kind === 'medication' && !found.brand && !found.active_ingredient ? { ...found, active_ingredient: found.name } : found }; reload();
  });
  view().querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    const [kind, id] = b.dataset.del.split(':'); const it = itemsOf(kind).find(x => x.id == id);
    if (!confirm(`¿Quitar «${CLIN[kind].line(it).replace(/<[^>]+>/g, '')}» del expediente?`)) return;
    await api(`/api/people/${S.subject}/clinical/${kind}/${id}`, { method: 'DELETE' }); toast('Quitado del expediente.'); reload();
  });
  view().querySelectorAll('[data-none]').forEach(b => b.onclick = async () => { try { await api(`/api/people/${S.subject}/clinical-none/${b.dataset.none}`, { method: 'PUT' }); reload(); } catch (e) { toast(e.message); } });
  view().querySelectorAll('[data-none-off]').forEach(b => b.onclick = async () => { await api(`/api/people/${S.subject}/clinical-none/${b.dataset.noneOff}`, { method: 'DELETE' }); reload(); });

  if (editing) {
    const cfg = CLIN[editing.kind], it = editing.item || {};
    const lists = [...new Set(cfg.fields.filter(f => f.list).map(f => f.list))];
    const input = f => {
      const v = it[f.name] ?? (f.type === 'check' ? 1 : '');
      if (f.type === 'select') {
        const cur = it[f.name] || (f.optional ? '' : c[f.options][0]);
        const opts = [...(f.optional ? [''] : []), ...c[f.options], ...(cur && !c[f.options].includes(cur) ? [cur] : [])];  // lo ya guardado nunca se pierde
        return `<select name="${f.name}">${opts.map(o => `<option value="${esc(o)}" ${o === cur ? 'selected' : ''}>${esc(o || 'Sin especificar')}</option>`).join('')}</select>`;
      }
      if (f.type === 'check') return `<input type="checkbox" name="${f.name}" ${v ? 'checked' : ''}>`;
      if (f.type === 'area') return `<textarea name="${f.name}" class="notes-area" rows="${Math.min(18, Math.max(6, String(v).split('\n').length + Math.ceil(String(v).length / 90)))}" maxlength="${f.max || 4000}" spellcheck="true" lang="es" placeholder="Escribe con libertad; puedes usar renglones y párrafos.">${esc(v)}</textarea><span class="s">Puedes agrandar el cuadro arrastrando la esquina. <span data-count>${String(v).length}</span>/${f.max || 4000}</span>`;
      return `<input type="${f.type === 'date' ? 'date' : 'text'}" name="${f.name}" value="${esc(v)}" ${f.list ? `list="dl_${f.list}"` : ''} ${f.ph ? `placeholder="${esc(f.ph)}"` : ''} autocomplete="off" ${f.type === 'date' ? '' : 'spellcheck="true" lang="es"'} style="width:100%">`;
    };
    document.getElementById('clinform').innerHTML = `<form class="card cfg fg2" id="cf"><h2>${editing.item ? 'Editar' : cfg.add}</h2>
      ${lists.map(l => `<datalist id="dl_${l}">${(S.sug[l] || []).map(x => `<option value="${esc(x)}">`).join('')}</datalist>`).join('')}
      ${cfg.fields.map(f => `<label ${f.type === 'area' ? 'class="wide"' : ''}>${esc(f.label)}${input(f)}</label>`).join('')}
      <p class="err" id="cfe"></p><div class="bar"><button type="button" class="mini" id="cfc">Cancelar</button><button class="btn">Guardar</button></div></form>`;
    document.getElementById('cf').elements[0].focus();
    document.querySelectorAll('#cf textarea.notes-area').forEach(a => a.oninput = () => { const n = a.parentElement.querySelector('[data-count]'); if (n) n.textContent = a.value.length; });
    document.getElementById('cfc').onclick = () => { S.clinEdit = null; reload(); };
    document.getElementById('cf').onsubmit = async ev => {
      ev.preventDefault();
      const body = {};
      cfg.fields.forEach(f => { const el = ev.target.elements[f.name]; body[f.name] = f.type === 'check' ? el.checked : el.value; });
      try {
        await api(editing.item ? `/api/people/${S.subject}/clinical/${editing.kind}/${editing.item.id}` : `/api/people/${S.subject}/clinical/${editing.kind}`,
          { method: editing.item ? 'PUT' : 'POST', body });
        S.clinEdit = null; toast('Guardado en el expediente.'); reload();
      } catch (e) { document.getElementById('cfe').textContent = e.message; }
    };
  }
}

/* ---------- Imagen: informes de radiografía, ultrasonido, resonancia... ---------- */

const FLAG = { normal: ['ok', 'Normal según el informe'], revisar: ['out', 'Leer la conclusión'] };
const flagPill = f => { const [c, t] = FLAG[f] || FLAG.revisar; return `<span class="pill ${c}">${t}</span>`; };

// Tablas transcritas por Claude desde la imagen del informe (a petición de la persona).
function studyTables(x) {
  if (!x.tables) return `<p style="margin:10px 0 0"><button class="mini" data-readtables="${x.id}" title="Envía las imágenes de este informe a Claude para transcribir sus tablas. Se ven datos personales impresos en ellas.">Leer las tablas con Claude</button></p>`;
  const html = x.tables.tables.map(t => `${t.caption ? `<h4 style="margin:10px 0 4px">${esc(t.caption)}</h4>` : ''}<div class="tblwrap"><table><thead><tr>${t.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${t.rows.map(r => `<tr>${r.map(c => `<td>${c == null ? '—' : esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`).join('');
  return `<div style="margin-top:10px"><span class="tip">Transcrito por Claude desde la imagen; compáralo con el original.</span>${html}
    ${x.tables.notes ? `<p class="tip">${esc(x.tables.notes)}</p>` : ''}${allergySuggestions(x)}</div>`;
}

// Pruebas cutáneas de alergia: los alérgenos con reacción (grado 2+ o más) se pueden sumar a las alergias.
function allergyRows(x) {
  if (!/cut[aá]nea|alerg|prick|hipersensibilidad/i.test(x.study_name || '') || !x.tables) return [];
  const out = [];
  for (const t of x.tables.tables) {
    const g = t.columns.findIndex(c => /grado/i.test(c));
    const n = t.columns.findIndex(c => /extracto|al[eé]rgeno|sustancia|nombre/i.test(c));
    if (g < 0 || n < 0) continue;
    for (const r of t.rows) { const m = String(r[g] || '').match(/^(\d)\+?$/); if (m && Number(m[1]) >= 2 && r[n] && !/control/i.test(r[n])) out.push({ name: r[n], grade: `${m[1]}+` }); }
  }
  return out;
}
function allergySuggestions(x) {
  const rows = allergyRows(x);
  if (!rows.length) return '';
  return `<div class="newan" style="margin-top:10px"><b>Alérgenos con reacción (grado 2+ o más, según el informe)</b>
    <span class="tip">Elige cuáles quieres sumar a tus Alergias. House no interpreta el resultado: tu médico lo hará.</span>
    ${rows.map((r, i) => `<label style="display:flex;gap:8px;align-items:center;color:var(--ink);font-size:14px"><input type="checkbox" data-allergen="${x.id}:${i}" checked> ${esc(r.name)} <span class="tag at">${esc(r.grade)}</span></label>`).join('')}
    <span><button class="mini" data-addallergies="${x.id}">Agregar a Alergias</button></span></div>`;
}

const thumbs = (imgs, label) => imgs.length ? `<div class="thumbs" aria-label="Imágenes de ${esc(label)}">${imgs.map(i => `<button class="thumb" data-img="${i.id}" title="${esc(i.title)}" aria-label="Ver imagen ${esc(i.title)}"><img src="/api/images/${i.id}/file" alt="${esc(i.title)}" loading="lazy"></button>`).join('')}</div>` : '';

// Estudios que van de la mano (conclusiones, imágenes y biopsias de un mismo procedimiento).
const related = (x, list) => {
  const rel = x.related || [], ids = new Set([x.id, ...rel.map(r => r.id)]);
  const options = list.filter(o => !ids.has(o.id));
  return `<div class="rel"><span class="tip">Relacionado con:</span>
    ${rel.map(r => `<span class="relchip"><a href="#st-${r.id}" data-goto="${r.id}">${esc(r.study_name)} · ${fd(r.performed_on)}</a>${r.document_id ? ` <a class="tip" href="/api/documents/${r.document_id}/file" target="_blank" rel="noopener">original</a>` : ''}${r.manual ? `<button class="mini" data-unlink="${x.id}:${r.id}" aria-label="Quitar relación con ${esc(r.study_name)}">✕</button>` : ''}</span>`).join('') || '<span class="tip">ningún estudio</span>'}
    ${options.length ? `<select data-link="${x.id}" aria-label="Relacionar con otro estudio"><option value="">Relacionar con…</option>${options.map(o => `<option value="${o.id}">${esc(o.study_name)} · ${fd(o.performed_on)}</option>`).join('')}</select>` : ''}</div>`;
};

async function renderImaging() {
  view().innerHTML = '<p class="tip"><span class="spin"></span>Cargando…</p>';
  const [list, loose] = await Promise.all([api(`/api/people/${S.subject}/imaging`), api(`/api/people/${S.subject}/images/loose`)]);
  if (!list.length && !loose.length) {
    view().innerHTML = `<section class="card empty"><h2>Todavía no hay informes de estudios</h2>
      <p>Sube el informe en PDF, digital o escaneado (radiografía, ultrasonido, tomografía, endoscopia, biopsia, electrocardiograma…) desde Documentos. House lo lee en esta Mac, sin enviarlo a ninguna IA, y lo revisas junto al original. Si tienes las imágenes en PNG o JPG, súbelas también y aparecerán junto a su informe.</p>
      <button class="btn" id="go">Subir un informe</button></section>`;
    document.getElementById('go').onclick = () => { S.tab = 'doc'; renderShell(); };
    return;
  }
  const kinds = ['Todos', ...new Set(list.map(x => x.modality || 'Otro'))];
  if (!kinds.includes(S.studyFilter)) S.studyFilter = 'Todos';
  const shown = list.filter(x => S.studyFilter === 'Todos' || (x.modality || 'Otro') === S.studyFilter);
  const all = new Map([...list.flatMap(x => x.images), ...loose].map(i => [i.id, i]));
  view().innerHTML = `<div class="bar"><p class="tip" style="margin:0">House guarda lo que dice cada informe y te deja ver las imágenes; no interpreta imágenes ni sustituye al médico que las firma.</p><button class="mini" id="refreshrep" title="Vuelve a leer tus informes con la versión actual de House. No cambia lo que confirmaste (nombre, fecha, tipo).">Actualizar lectura de informes</button></div>
    ${kinds.length > 2 ? `<div class="chips" role="group" aria-label="Filtrar por tipo">${kinds.map(k => `<button class="chip" data-sf="${esc(k)}" aria-pressed="${k === S.studyFilter}">${esc(k)}</button>`).join('')}</div>` : ''}
    ${shown.map(x => `<section class="card cfg" id="st-${x.id}"><div class="bar"><div><div class="ttl"><h2>${esc(x.study_name)}</h2><button class="mini" data-rename-study="${x.id}" title="Corregir el nombre" aria-label="Corregir el nombre de ${esc(x.study_name)}">✎</button></div>
        <span class="tip">${fd(x.performed_on)} · ${esc(x.modality || 'Estudio')}${x.site ? ' · ' + esc(x.site) : ''}</span>
        <span class="tip fname" title="Nombre del archivo que subiste">Archivo: ${esc(x.filename || x.document_title || '—')}</span></div>${flagPill(x.flag)}</div>
      ${thumbs(x.images, x.study_name)}
      ${related(x, list)}
      ${(x.problems || []).length ? `<p class="tip" style="margin:0">Ligado a: ${x.problems.map(p => `<span class="tag at">${esc(p.name)}</span>`).join(' ')}</p>` : ''}
      ${x.conclusion ? `<p style="margin:0"><b>Conclusión:</b> ${esc(x.conclusion)}</p>` : '<p class="tip" style="margin:0">Este informe no trae una conclusión separada; lee el informe completo.</p>'}
      <details class="hist" ${S.openStudy === x.id ? 'open' : ''}><summary>Ver el informe completo</summary>
        <div class="kv" style="margin-top:10px">
          ${x.technique ? `<b>Técnica</b><span>${esc(x.technique)}</span>` : ''}${x.indication ? `<b>Indicación</b><span>${esc(x.indication)}</span>` : ''}
          <b>Hallazgos</b><span>${esc(x.findings || '—')}</span>${x.prior ? `<b>Estudio previo</b><span>${esc(x.prior)}</span>` : ''}
          ${x.suggestions ? `<b>Sugerencias</b><span>${esc(x.suggestions)}</span>` : ''}${x.radiologist ? `<b>Firmado por</b><span>${esc(x.radiologist)}</span>` : ''}</div>
        ${x.document_id ? `<p style="margin:10px 0 0"><a class="mini" href="/api/documents/${x.document_id}/file" target="_blank" rel="noopener" style="text-decoration:none">Ver informe original</a></p>` : ''}
        ${studyTables(x)}</details></section>`).join('')}
    ${loose.length && S.studyFilter === 'Todos' ? `<section class="card cfg"><div class="bar"><div><h2>Imágenes sin informe</h2>
        <span class="tip">No encontré un informe de la misma fecha y nombre parecido. Sube el informe en PDF y se unirán solas.</span></div></div>${thumbs(loose, 'sin informe')}</section>` : ''}`;
  view().querySelectorAll('[data-sf]').forEach(b => b.onclick = () => { S.studyFilter = b.dataset.sf; renderImaging(); });
  view().querySelectorAll('[data-rename-study]').forEach(b => b.onclick = async () => {
    const x = list.find(s => s.id == b.dataset.renameStudy), name = prompt('Nombre corregido del estudio (el nombre del archivo original se conserva):', x.study_name);
    if (name == null || name.trim() === x.study_name) return;
    try { await api(`/api/imaging/${x.id}/name`, { method: 'PUT', body: { name } }); toast('Nombre actualizado.'); renderImaging(); } catch (e) { toast(e.message); }
  });
  view().querySelectorAll('[data-readtables]').forEach(b => b.onclick = async () => {
    if (!confirm('Se enviarán a Claude las imágenes de este informe para transcribir sus tablas. Ahí se ven datos personales impresos (nombre, fecha de nacimiento…). ¿Continuar?')) return;
    b.disabled = true; b.textContent = 'Claude está leyendo la imagen… (unos 15 segundos)';
    try { await api(`/api/imaging/${b.dataset.readtables}/read-tables`, { method: 'POST' }); toast('Tablas leídas. Compáralas con el original.'); S.openStudy = Number(b.dataset.readtables); renderImaging(); }
    catch (e) { b.disabled = false; b.textContent = 'Leer las tablas con Claude'; toast(e.message); }
  });
  view().querySelectorAll('[data-addallergies]').forEach(b => b.onclick = async () => {
    const x = list.find(s => s.id == b.dataset.addallergies), rows = allergyRows(x);
    const picked = [...view().querySelectorAll(`[data-allergen^="${x.id}:"]`)].filter(c => c.checked).map(c => rows[Number(c.dataset.allergen.split(':')[1])]);
    if (!picked.length) return toast('Elige al menos un alérgeno.');
    const have = new Set((await api(`/api/people/${S.subject}/clinical`)).allergies.map(a => a.substance.toLowerCase()));
    let added = 0;
    for (const r of picked) {
      if (have.has(r.name.toLowerCase())) continue;
      await api(`/api/people/${S.subject}/clinical/allergy`, { method: 'POST', body: { substance: r.name, category: 'Ambiental', reaction: `Prueba cutánea del ${fd(x.performed_on)}: grado ${r.grade}` } }); added++;
    }
    toast(added ? `Agregué ${added} ${added === 1 ? 'alergia' : 'alergias'} al expediente.` : 'Esas alergias ya estaban en tu expediente.');
  });
  const rf = document.getElementById('refreshrep');
  if (rf) rf.onclick = async () => {
    rf.disabled = true; rf.textContent = 'Leyendo de nuevo… puede tardar unos minutos';
    try {
      const r = await api(`/api/people/${S.subject}/imaging/refresh`, { method: 'POST' });
      toast(`Informes actualizados: ${r.updated}. Sin cambios: ${r.unchanged}.${r.skipped ? ` No se pudieron actualizar: ${r.skipped}.` : ''}`); renderImaging();
    } catch (e) { rf.disabled = false; rf.textContent = 'Actualizar lectura de informes'; toast(e.message); }
  };
  view().querySelectorAll('[data-goto]').forEach(a => a.onclick = e => {
    e.preventDefault();
    if (S.studyFilter !== 'Todos') { S.studyFilter = 'Todos'; renderImaging().then(() => document.getElementById('st-' + a.dataset.goto)?.scrollIntoView({ behavior: 'smooth' })); return; }
    document.getElementById('st-' + a.dataset.goto)?.scrollIntoView({ behavior: 'smooth' });
  });
  view().querySelectorAll('[data-link]').forEach(sel => sel.onchange = async () => {
    if (!sel.value) return;
    await api(`/api/imaging/${sel.dataset.link}/links`, { method: 'POST', body: { other_id: Number(sel.value) } }); toast('Estudios relacionados.'); renderImaging();
  });
  view().querySelectorAll('[data-unlink]').forEach(b => b.onclick = async () => {
    const [a, o] = b.dataset.unlink.split(':');
    await api(`/api/imaging/${a}/links/${o}`, { method: 'DELETE' }); toast('Relación quitada.'); renderImaging();
  });
  view().querySelectorAll('.thumbs').forEach(box => {
    const ids = [...box.querySelectorAll('[data-img]')].map(b => Number(b.dataset.img));
    box.querySelectorAll('[data-img]').forEach(b => b.onclick = () => openViewer(ids.map(i => all.get(i)), ids.indexOf(Number(b.dataset.img))));
  });
}

/* Visor: zoom, arrastrar, brillo, contraste e invertir (útil en radiografías). No modifica el archivo. */
function openViewer(images, start) {
  let k = start, zoom = 1, x = 0, y = 0, bright = 100, contrast = 100, inv = false;
  const back = document.createElement('div');
  back.className = 'viewer'; back.setAttribute('role', 'dialog'); back.setAttribute('aria-modal', 'true'); back.setAttribute('aria-label', 'Visor de imagen');
  document.body.appendChild(back);
  const close = () => { back.remove(); document.removeEventListener('keydown', key); };
  const key = e => {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowRight' && k < images.length - 1) { k++; reset(); draw(); }
    else if (e.key === 'ArrowLeft' && k > 0) { k--; reset(); draw(); }
    else if (e.key === '+' || e.key === '=') { zoom = Math.min(8, zoom * 1.25); apply(); }
    else if (e.key === '-') { zoom = Math.max(0.5, zoom / 1.25); apply(); }
  };
  const reset = () => { zoom = 1; x = y = 0; bright = contrast = 100; inv = false; };
  const apply = () => {
    const im = back.querySelector('#vimg'); if (!im) return;
    im.style.transform = `translate(${x}px, ${y}px) scale(${zoom})`;
    im.style.filter = `brightness(${bright}%) contrast(${contrast}%)${inv ? ' invert(1)' : ''}`;
    const z = back.querySelector('#vz'); if (z) z.textContent = Math.round(zoom * 100) + '%';
  };
  const draw = () => {
    const im = images[k];
    back.innerHTML = `<div class="vbar"><b>${esc(im.title)}</b><span class="tip" style="color:#bbb">${images.length > 1 ? `${k + 1} de ${images.length} · ` : ''}<span id="vz">100%</span></span>
        <div class="vtools">
          ${images.length > 1 ? `<button class="mini" id="vp" ${k === 0 ? 'disabled' : ''} aria-label="Anterior">‹</button><button class="mini" id="vn" ${k === images.length - 1 ? 'disabled' : ''} aria-label="Siguiente">›</button>` : ''}
          <button class="mini" id="vzo" aria-label="Alejar">−</button><button class="mini" id="vzi" aria-label="Acercar">+</button>
          <label>Brillo <input type="range" id="vb" min="40" max="220" value="${bright}"></label>
          <label>Contraste <input type="range" id="vc" min="40" max="260" value="${contrast}"></label>
          <button class="mini" id="vinv" aria-pressed="${inv}">Invertir</button><button class="mini" id="vr">Restablecer</button>
          <button class="mini dn" id="vd">Borrar</button><button class="mini" id="vx">Cerrar</button></div></div>
      <div class="vstage" id="vs"><img id="vimg" src="/api/images/${im.id}/file" alt="${esc(im.title)}" draggable="false"></div>`;
    const $ = id => back.querySelector('#' + id);
    $('vx').onclick = close;
    if ($('vp')) { $('vp').onclick = () => { k--; reset(); draw(); }; $('vn').onclick = () => { k++; reset(); draw(); }; }
    $('vzi').onclick = () => { zoom = Math.min(8, zoom * 1.25); apply(); };
    $('vzo').onclick = () => { zoom = Math.max(0.5, zoom / 1.25); apply(); };
    $('vb').oninput = e => { bright = Number(e.target.value); apply(); };
    $('vc').oninput = e => { contrast = Number(e.target.value); apply(); };
    $('vinv').onclick = () => { inv = !inv; $('vinv').setAttribute('aria-pressed', inv); apply(); };
    $('vr').onclick = () => { reset(); draw(); };
    $('vd').onclick = async () => {
      if (!confirm(`¿Borrar la imagen «${im.title}»? No se puede deshacer.`)) return;
      await api(`/api/images/${im.id}`, { method: 'DELETE' }); close(); toast('Imagen borrada.'); renderImaging();
    };
    const st = $('vs');
    st.onwheel = e => { e.preventDefault(); zoom = Math.min(8, Math.max(0.5, zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1))); apply(); };
    st.ondblclick = () => { zoom = zoom > 1 ? 1 : 2.5; if (zoom === 1) x = y = 0; apply(); };
    let drag = null;
    st.onpointerdown = e => { drag = { px: e.clientX - x, py: e.clientY - y }; st.setPointerCapture(e.pointerId); st.classList.add('grab'); };
    st.onpointermove = e => { if (drag) { x = e.clientX - drag.px; y = e.clientY - drag.py; apply(); } };
    st.onpointerup = () => { drag = null; st.classList.remove('grab'); };
    apply();
  };
  document.addEventListener('keydown', key);
  draw();
}

// Composición corporal: Claude transcribe el reporte; la persona elige qué medidas guardar.
const EVAL_PILL = e => !e ? '' : `<span class="pill ${['normal', 'fuerte'].includes(e) ? 'ok' : e === 'bajo' ? 'na' : 'out'}">${esc(e.charAt(0).toUpperCase() + e.slice(1))}</span>`;
const REGION = { brazo_izquierdo: 'Brazo izquierdo', brazo_derecho: 'Brazo derecho', tronco: 'Tronco', pierna_izquierda: 'Pierna izquierda', pierna_derecha: 'Pierna derecha' };
const rangeText = m => m.normal_low != null && m.normal_high != null ? `${m.normal_low} – ${m.normal_high}` : '';
const segTable = segs => !segs.length ? '' : `<div class="tblwrap"><table><thead><tr><th>Región</th><th>Masa magra</th><th>Grasa</th></tr></thead><tbody>${segs.map(x => `<tr><td>${REGION[x.region]}</td><td>${x.lean_kg != null ? x.lean_kg + ' kg' : '—'} ${EVAL_PILL(x.lean_evaluation)}</td><td>${x.fat_pct != null ? x.fat_pct + ' %' : ''}${x.fat_kg != null ? ' · ' + x.fat_kg + ' kg' : ''} ${EVAL_PILL(x.fat_evaluation)}</td></tr>`).join('')}</tbody></table></div>`;
function openBodyScanReview(id, d) {
  const sc = d.body_scan;
  if (sc.confirmed) { toast('Este reporte ya fue revisado.'); return renderDocs(); }
  const head = { date: sc.measured_on || '', height: true };
  const metrics = sc.metrics.map(m => ({ ...m, accept: m.key !== 'bmi' }));
  const heightDiffers = sc.height_cm && Number(d.current_height_cm) !== Number(sc.height_cm);
  view().innerHTML = `<div class="rv"><div class="pages" id="pages" aria-label="Reporte original">
      ${sc.file_kind === 'pdf' ? `<iframe class="pdf" src="/api/documents/${id}/file" title="Reporte original"></iframe>` : `<div class="pg"><img src="/api/documents/${id}/file" alt="Reporte original"></div>`}</div>
    <section class="card cfg" id="side"></section></div>`;
  const draw = () => {
    const n = metrics.filter(m => m.accept).length;
    document.getElementById('side').innerHTML = `
      <div class="bar"><h2>Revisa el reporte de composición corporal</h2><div style="display:flex;gap:6px"><a class="mini" href="/api/documents/${id}/file" target="_blank" rel="noopener" style="text-decoration:none">Abrir original</a><button class="mini" id="back">Volver</button></div></div>
      <p class="tip">Claude leyó este reporte${sc.device ? ' (' + esc(sc.device) + ')' : ''}. Compara con el original; solo lo que elijas entra a tus medidas.</p>
      ${sc.warnings.map(w => `<span class="flag">${esc(w)}</span>`).join('')}
      <div class="fg2"><label>Fecha del reporte<input type="date" id="bd" value="${esc(head.date)}"></label>
        ${sc.date_printed ? `<span class="tip" style="align-self:end">Impreso: ${esc(sc.date_printed)} (día.mes.año)</span>` : ''}</div>
      ${heightDiffers ? `<label><input type="checkbox" id="bh" ${head.height ? 'checked' : ''}> Actualizar mi talla en el perfil a <b>${sc.height_cm} cm</b>${d.current_height_cm ? ` (hoy: ${d.current_height_cm} cm)` : ''}</label>` : ''}
      <div class="tblwrap"><table class="rt"><thead><tr><th></th><th>Medida</th><th>Valor</th><th>Normal</th><th></th></tr></thead><tbody>
        ${metrics.map((m, i) => `<tr class="${m.accept ? '' : 'skip'}"><td><input type="checkbox" data-bm="${i}" ${m.accept ? 'checked' : ''} aria-label="Guardar ${esc(m.name)}"></td><td><b>${esc(m.name)}</b></td><td>${m.value} ${esc(m.unit)}</td><td>${esc(rangeText(m))}</td><td>${EVAL_PILL(m.evaluation)}</td></tr>`).join('')}
      </tbody></table></div>
      ${sc.segments.length ? `<h3 style="margin:8px 0 0;font-size:15px">Por región del cuerpo</h3>${segTable(sc.segments)}` : ''}
      ${sc.notes ? `<p class="tip"><b>Notas del reporte:</b> ${esc(sc.notes)}</p>` : ''}
      <p class="tip">El IMC del reporte no se guarda como medida: House lo calcula con tu talla y tu peso.</p>
      <p class="err" id="e"></p>
      <div class="bar"><button class="btn danger" id="discard">Descartar</button><button class="btn" id="ok" ${n ? '' : 'disabled'}>Guardar ${n} ${n === 1 ? 'medida' : 'medidas'}</button></div>`;
    document.getElementById('back').onclick = () => renderDocs();
    view().querySelectorAll('[data-bm]').forEach(c => c.onchange = () => { metrics[c.dataset.bm].accept = c.checked; draw(); });
    document.getElementById('bd').oninput = document.getElementById('bd').onchange = e => { head.date = e.target.value; };
    const bh = document.getElementById('bh'); if (bh) bh.onchange = () => { head.height = bh.checked; };
    document.getElementById('discard').onclick = async () => {
      if (!confirm('¿Descartar este reporte? Se borra el original y no se guarda nada.')) return;
      await api(`/api/documents/${id}`, { method: 'DELETE' }); toast('Reporte descartado.'); renderDocs();
    };
    document.getElementById('ok').onclick = async () => {
      try {
        const res = await api(`/api/documents/${id}/review-body-scan`, { method: 'POST', body: { measured_on: head.date || null, keys: metrics.filter(m => m.accept).map(m => m.key), update_height: !!(heightDiffers && head.height), height_cm: sc.height_cm } });
        toast(`Guardé ${res.saved} ${res.saved === 1 ? 'medida' : 'medidas'} en tu perfil.`); S.tab = 'exp'; S.expSec = 'per'; renderShell();
      } catch (err) { document.getElementById('e').textContent = err.message; }
    };
  };
  draw();
}

// Receta: Claude propone los medicamentos; la persona corrige, elige y confirma junto al original.
function openPrescriptionReview(id, d) {
  const rx = d.prescription;
  if (!rx) { toast('Esta receta ya fue revisada.'); return renderDocs(); }
  const meds = rx.medications.map((m, i) => ({ ...m, active_ingredient: m.active_ingredient || (m.brand ? '' : m.name), brand: m.brand || '', index: i, accept: true, active: true, dup: d.current_medications.some(n => n.toLowerCase() === m.name.toLowerCase()) }));
  const head = { date: rx.prescription_date || '', prescriber: rx.prescriber || '', diagnosis: rx.diagnosis || '', problem: rx.suggested_problem_id || '' };
  view().innerHTML = `<div class="rv"><div class="pages" id="pages" aria-label="Receta original">
      ${rx.file_kind === 'pdf' ? `<iframe class="pdf" src="/api/documents/${id}/file" title="Receta original"></iframe>` : `<div class="pg"><img src="/api/documents/${id}/file" alt="Receta original"></div>`}</div>
    <section class="card cfg" id="side"></section></div>`;
  const draw = () => {
    const n = meds.filter(m => m.accept).length;
    document.getElementById('side').innerHTML = `
      <div class="bar"><h2>Revisa la receta «${esc(d.document.title)}»</h2><div style="display:flex;gap:6px"><a class="mini" href="/api/documents/${id}/file" target="_blank" rel="noopener" style="text-decoration:none">Abrir original</a><button class="mini" id="back">Volver</button></div></div>
      <p class="tip">Claude leyó esta receta; compárala con el original y corrige lo que haga falta. Solo lo que confirmes entra a tus Medicamentos. House no sugiere ni interpreta medicamentos.</p>
      <div class="fg2"><label>Fecha de la receta<input type="date" id="rd" value="${esc(head.date)}"></label>
        <label>Médico<input type="text" id="rp" value="${esc(head.prescriber)}" maxlength="120"></label>
        <label>Diagnóstico o motivo<input type="text" id="rg" value="${esc(head.diagnosis)}" maxlength="200"></label>
        <label>Ligar a un padecimiento<select id="rq"><option value="">Ninguno</option>${d.problems.map(p => `<option value="${p.id}" ${String(p.id) === String(head.problem) ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label></div>
      ${meds.map(m => `<div class="prof" style="display:grid;gap:8px">
        <label><input type="checkbox" data-macc="${m.index}" ${m.accept ? 'checked' : ''}> <b>Guardar este medicamento</b></label>
        ${!m.legible ? '<span class="flag">Difícil de leer: revisa el nombre y la dosis contra el original</span>' : ''}
        ${m.dup ? `<span class="flag">Ya tienes «${esc(m.name)}» como medicamento actual</span>` : ''}
        <div class="fg2" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          <label>Nombre comercial<input type="text" data-mf="brand" data-i="${m.index}" value="${esc(m.brand || '')}" maxlength="120"></label>
          <label>Sustancia activa<input type="text" data-mf="active_ingredient" data-i="${m.index}" value="${esc(m.active_ingredient || '')}" maxlength="120"></label>
          <label>Dosis<input type="text" data-mf="dose" data-i="${m.index}" value="${esc(m.dose || '')}" maxlength="200"></label>
          <label>Frecuencia<input type="text" data-mf="frequency" data-i="${m.index}" value="${esc(m.frequency || '')}" maxlength="200"></label>
          <label>Duración<input type="text" data-mf="duration" data-i="${m.index}" value="${esc(m.duration || '')}" maxlength="200"></label>
          <label>Indicaciones<input type="text" data-mf="instructions" data-i="${m.index}" value="${esc(m.instructions || '')}" maxlength="400"></label></div>
        <label><input type="checkbox" data-mact="${m.index}" ${m.active ? 'checked' : ''}> Lo tomo actualmente</label></div>`).join('')}
      ${rx.notes ? `<p class="tip"><b>Notas de la receta:</b> ${esc(rx.notes)}</p>` : ''}
      <p class="err" id="e"></p>
      <div class="bar"><button class="btn danger" id="discard">Descartar receta</button><button class="btn" id="ok" ${n ? '' : 'disabled'}>Guardar ${n} ${n === 1 ? 'medicamento' : 'medicamentos'}</button></div>`;
    document.getElementById('back').onclick = () => renderDocs();
    view().querySelectorAll('[data-macc]').forEach(c => c.onchange = () => { meds[c.dataset.macc].accept = c.checked; draw(); });
    view().querySelectorAll('[data-mact]').forEach(c => c.onchange = () => { meds[c.dataset.mact].active = c.checked; });
    view().querySelectorAll('[data-mf]').forEach(i => i.oninput = () => { meds[i.dataset.i][i.dataset.mf] = i.value; });
    ['rd', 'rp', 'rg', 'rq'].forEach(k => { const el = document.getElementById(k); el.oninput = el.onchange = () => { head[{ rd: 'date', rp: 'prescriber', rg: 'diagnosis', rq: 'problem' }[k]] = el.value; }; });
    document.getElementById('discard').onclick = async () => {
      if (!confirm('¿Descartar esta receta? Se borra el original y no se guarda ningún medicamento.')) return;
      await api(`/api/documents/${id}`, { method: 'DELETE' }); toast('Receta descartada.'); renderDocs();
    };
    document.getElementById('ok').onclick = async () => {
      const e = document.getElementById('e');
      try {
        const res = await api(`/api/documents/${id}/review-prescription`, { method: 'POST', body: {
          prescription_date: head.date || null, prescriber: head.prescriber || null, diagnosis: head.diagnosis || null, problem_id: head.problem ? Number(head.problem) : null,
          decisions: meds.map(m => ({ index: m.index, accept: m.accept, name: m.active_ingredient || m.brand || m.name, active_ingredient: m.active_ingredient || null, brand: m.brand || null, dose: m.dose || null, frequency: m.frequency || null, duration: m.duration || null, instructions: m.instructions || null, active: m.active })) } });
        toast(`Guardé ${res.saved} ${res.saved === 1 ? 'medicamento' : 'medicamentos'} en tu expediente.`); S.tab = 'exp'; renderShell();
      } catch (err) { e.textContent = err.message; }
    };
  };
  draw();
}

async function openImagingReview(id, d, layout) {
  const reports = d.imaging.map(r => ({ ...r, accept: true }));
  view().innerHTML = `<div class="rv"><div class="pages" id="pages" aria-label="Informe original">
      ${layout.pages.length ? layout.pages.map(pg => `<div class="pg"><img src="/api/documents/${id}/pages/${pg.n}" alt="Página ${pg.n}" loading="lazy" style="aspect-ratio:${pg.width}/${pg.height}"></div>`).join('')
        : `<iframe class="pdf" src="/api/documents/${id}/file" title="Informe original"></iframe>`}</div>
    <section class="card cfg" id="side"></section></div>`;
  const draw = () => {
    const n = reports.filter(r => r.accept).length;
    document.getElementById('side').innerHTML = `
      <div class="bar"><h2>Revisa «${esc(d.document.title)}»</h2><div style="display:flex;gap:6px"><button class="mini" id="reread">Volver a leer</button><button class="mini" id="back">Volver</button></div></div>
      <p class="tip">Leí ${reports.length} ${reports.length === 1 ? 'informe' : 'informes'} en esta Mac; no se envió nada a ninguna IA. Compara con el original, corrige la fecha o el nombre si hace falta y confirma. La marca «normal» es solo una ayuda: lee siempre la conclusión.</p>
      ${reports.map((r, k) => `<div class="prof" style="display:grid;gap:8px">
        <label><input type="checkbox" data-acc="${k}" ${r.accept ? 'checked' : ''}> <b>Guardar este informe</b></label>
        <div class="fg2" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          <label>Estudio<input type="text" data-f="study_name" data-k="${k}" value="${esc(r.study_name)}" style="width:100%"></label>
          <label>Fecha<input type="date" data-f="performed_on" data-k="${k}" value="${esc(r.performed_on || '')}"></label>
          <label>Tipo<input type="text" data-f="modality" data-k="${k}" value="${esc(r.modality || '')}" style="width:100%"></label>
          <label>Marca<select data-f="flag" data-k="${k}"><option value="normal" ${r.flag === 'normal' ? 'selected' : ''}>Normal según el informe</option><option value="revisar" ${r.flag !== 'normal' ? 'selected' : ''}>Leer la conclusión</option></select></label></div>
        <div class="kv"><b>Técnica</b><span>${esc(r.technique || '—')}</span><b>Hallazgos</b><span>${esc(r.findings || '—')}</span>
          <b>Conclusión</b><span><b>${esc(r.conclusion || '—')}</b></span>${r.suggestions ? `<b>Sugerencias</b><span>${esc(r.suggestions)}</span>` : ''}</div></div>`).join('')}
      <p class="err" id="e"></p>
      <div class="bar"><button class="btn danger" id="discard">Descartar</button><button class="btn" id="ok" ${n ? '' : 'disabled'}>Confirmar ${n} ${n === 1 ? 'informe' : 'informes'}</button></div>`;
    document.getElementById('back').onclick = () => renderDocs();
    document.getElementById('reread').onclick = async () => {
      try { await api(`/api/documents/${id}/reread`, { method: 'POST' }); toast('Leído de nuevo.'); openReview(id); } catch (err) { document.getElementById('e').textContent = err.message; }
    };
    view().querySelectorAll('[data-acc]').forEach(c => c.onchange = () => { reports[c.dataset.acc].accept = c.checked; draw(); });
    view().querySelectorAll('[data-f]').forEach(i => i.oninput = i.onchange = () => { reports[i.dataset.k][i.dataset.f] = i.value; });
    document.getElementById('discard').onclick = async () => {
      if (!confirm('¿Descartar este informe? Se borra el original y no se guarda nada.')) return;
      await api(`/api/documents/${id}`, { method: 'DELETE' }); toast('Informe descartado.'); renderDocs();
    };
    document.getElementById('ok').onclick = async () => {
      const decisions = reports.map(r => ({ position: r.position, accept: r.accept, performed_on: r.performed_on || null, study_name: r.study_name, modality: r.modality, flag: r.flag }));
      try {
        const res = await api(`/api/documents/${id}/review-imaging`, { method: 'POST', body: { decisions } });
        toast(`Guardé ${res.saved} ${res.saved === 1 ? 'informe' : 'informes'} de imagen.`); S.tab = 'img'; renderShell();
      } catch (err) { document.getElementById('e').textContent = err.message; }
    };
  };
  draw();
}

/* ---------- Asistente: pregunta a tu expediente ---------- */

const ASK_IDEAS = ['¿Qué estudios o chequeos me convendría hacerme?', '¿Qué opinas de lo que está fuera de rango en mi último estudio?', '¿Hay relación entre mis estudios que deba revisar?', '¿Cómo ha cambiado el colesterol LDL?'];

// Texto de la respuesta: párrafos, viñetas y **negritas**, con [n] como enlaces a las fuentes.
function answerHtml(text, sources) {
  const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*\w])\*([^*\n]+?)\*(?![*\w])/g, '$1<i>$2</i>').replace(/\[(\d+)\]/g, (m, n) => sources.some(x => x.n == n) ? `<a class="cite" href="#src-${n}" data-cite="${n}">${n}</a>` : m);
  const out = []; let list = null, ordered = false;
  const flush = () => { if (list) { out.push(`<${ordered ? 'ol' : 'ul'}>${list.join('')}</${ordered ? 'ol' : 'ul'}>`); list = null; } };
  for (const line of text.split('\n')) {
    const h = line.match(/^\s*#{1,4}\s+(.*)/), li = line.match(/^\s*[-•*]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (h) { flush(); out.push(`<h4>${inline(h[1])}</h4>`); continue; }
    if (li || ol) { const isOl = !!ol; if (list && ordered !== isOl) flush(); ordered = isOl; (list ||= []).push(`<li>${inline((li || ol)[1])}</li>`); continue; }
    flush();
    if (line.trim() && !/^\s*-{3,}\s*$/.test(line)) out.push(`<p>${inline(line)}</p>`);
  }
  flush();
  return out.join('');
}

// Revisión integral: resumen ejecutivo en tarjetas con prioridad, secciones plegables y fuentes al final.
const PRIO = { urgente: ['Urgente', 'urg'], pronto: ['Pronto', 'pro'], rutina: ['Rutina', 'rut'], informativo: ['Informativo', 'inf'], ojo: ['Ojo', 'pro'], importante: ['Importante', 'pro'] };
const mdInline = (t, sources) => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*\w])\*([^*\n]+?)\*(?![*\w])/g, '$1<i>$2</i>').replace(/\[(\d+)\]/g, (m, n) => sources.some(x => x.n == n) ? `<a class="cite" href="#src-${n}" data-cite="${n}">${n}</a>` : m);
function execHtml(lines, sources) {
  const out = [];
  for (const line of lines) {
    const it = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (!it) { if (line.trim() && !/^\s*-{3,}\s*$/.test(line)) out.push(`<p class="exlead">${mdInline(line, sources)}</p>`); continue; }
    const m = it[1].match(/^\*\*(.+?)\*\*\s*(.*)$/);
    let chip = '', cls = 'inf', title = '', desc = it[1];
    if (m) {
      const bold = m[1].replace(/[.:]\s*$/, ''), k = bold.indexOf(':');
      const head = k > 0 ? bold.slice(0, k).trim() : '', key = head.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
      if (k > 0 && head.length <= 24) { const p = PRIO[key]; chip = p ? p[0] : head; cls = p ? p[1] : 'inf'; title = bold.slice(k + 1).trim(); } else title = bold;
      desc = m[2];
    }
    out.push(`<div class="ex ${cls}">${chip ? `<span class="exchip">${esc(chip)}</span>` : ''}<div>${title ? `<b>${mdInline(title, sources)}</b>` : ''}<p>${mdInline(desc, sources)}</p></div></div>`);
  }
  return `<div class="exec">${out.join('')}</div>`;
}
// Revisión integral: cada sección se presenta según su contenido (tarjetas, checklist, prioridades).
function rvBlocks(lines) {
  const out = []; let tbl = null;
  const endTable = () => { if (tbl) { out.push({ t: 'table', rows: tbl }); tbl = null; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (/^\s*\|/.test(line)) {
      const cells = line.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue;
      (tbl ||= []).push(cells); continue;
    }
    endTable();
    if (!line.trim() || /^\s*-{3,}\s*$/.test(line)) continue;
    const sub = line.match(/^\*\*(.+?)\*\*\s*$/), li = line.match(/^(\s*)[-•*]\s+(.*)/), ol = line.match(/^\s*(\d+)[.)]\s+(.*)/);
    if (sub) out.push({ t: 'sub', text: sub[1] });
    else if (li) out.push({ t: 'li', depth: li[1].length ? 1 : 0, text: li[2] });
    else if (ol) out.push({ t: 'ol', n: ol[1], text: ol[2] });
    else out.push({ t: 'p', text: line.trim() });
  }
  endTable();
  return out;
}
const rvSplitTitle = (t, keep) => { const m = t.match(/^\*\*(.+?)\*\*\s*(.*)$/); if (!m) return ['', t]; return [keep ? m[1] : m[1].replace(/[.:]\s*$/, ''), m[2]]; };
const rvCap = s => s ? s[0].toUpperCase() + s.slice(1) : s;
const rvPrio = t => { const s = String(t || '').replace(/\*/g, '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); return /urgente/.test(s) ? 'urg' : /pronto/.test(s) ? 'pro' : /rutina/.test(s) ? 'rut' : 'inf'; };
function rvList(items, sources) {   // viñetas con un nivel de sangría; «Mi opinión» y «Mi lectura» salen como recuadro
  const out = []; let cur = null;
  const flush = () => {
    if (cur) out.push(`<ul class="rvul">${cur.map(x => `<li>${x.text}${x.subs.length ? `<ul class="rvsub">${x.subs.map(t => `<li>${t}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ul>`);
    cur = null;
  };
  for (const b of items) {
    if (b.t === 'li' && /^\*\*(Mi opinión|Mi lectura|Mi criterio)/i.test(b.text)) { flush(); out.push(`<div class="rvopin">${mdInline(b.text, sources)}</div>`); continue; }
    if (b.t === 'li' || b.t === 'ol') {
      (cur ||= []);
      if (b.depth === 1 && cur.length) cur[cur.length - 1].subs.push(mdInline(b.text, sources));
      else cur.push({ text: mdInline(b.text, sources), subs: [] });
      continue;
    }
    flush(); out.push(b.t === 'sub' ? `<h5>${mdInline(b.text, sources)}</h5>` : `<p>${mdInline(b.text, sources)}</p>`);
  }
  flush();
  return out.join('');
}
function rvData(blocks, sources) {   // «Lo que dicen tus datos»: un bloque plegable por tema, con su importancia
  const groups = []; let cur = { title: '', body: [] };
  for (const b of blocks) { if (b.t === 'sub') { groups.push(cur); cur = { title: b.text, body: [] }; } else cur.body.push(b); }
  groups.push(cur);
  return groups.filter(g => g.title || g.body.length).map((g, i) => {
    if (!g.title) return `<div class="rvbody">${rvList(g.body, sources)}</div>`;
    const m = g.title.replace(/^\d+[.)]\s*/, '').match(/^(.*?)\s*\((.*)\)\s*$/), name = m ? m[1] : g.title.replace(/^\d+[.)]\s*/, ''), tag = m ? m[2] : '';
    const cls = /m[aá]s importante|urgente/i.test(tag) ? 'urg' : /vigilar|media/i.test(tag) ? 'pro' : 'inf';
    return `<details class="rvtopic" ${i <= 1 ? 'open' : ''}><summary><span class="rvtn">${esc(name)}</span>${tag ? `<span class="exchip ${cls}">${esc(tag[0].toUpperCase() + tag.slice(1))}</span>` : ''}</summary><div class="rvbody">${rvList(g.body, sources)}</div></details>`;
  }).join('');
}
function rvCards(blocks, sources, numbered) {   // relaciones y cuidados: una tarjeta por punto
  const cards = []; let intro = [];
  for (const b of blocks) {
    if (b.t === 'ol' || (b.t === 'li' && b.depth === 0 && !numbered)) {
      const [title, rest] = rvSplitTitle(b.text);
      cards.push({ n: b.n, title, text: rvCap(rest || (title ? '' : b.text)), extra: [] });
    } else if (b.t === 'li' && cards.length) cards[cards.length - 1].extra.push({ ...b, depth: 0 });
    else if (b.t === 'sub' || b.t === 'p') { if (!cards.length) intro.push(b); else cards[cards.length - 1].extra.push(b); }
  }
  const heads = intro.map(b => `<p class="rvlead">${mdInline(b.text, sources)}</p>`).join('');
  return heads + `<div class="rvcards">${cards.map((c, i) => `<div class="rvcard">${numbered ? `<span class="rvnum">${c.n || i + 1}</span>` : ''}<div>${c.title ? `<b class="rvct">${mdInline(c.title, sources)}</b>` : ''}${c.text ? `<p>${mdInline(c.text, sources)}</p>` : ''}${c.extra.length ? rvList(c.extra, sources) : ''}</div></div>`).join('')}</div>`;
}
function rvStudies(blocks, sources) {   // tabla de estudios → tarjetas agrupadas por prioridad; lo demás debajo
  const table = blocks.find(b => b.t === 'table'), rest = blocks.filter(b => b.t !== 'table');
  let html = '';
  if (table && table.rows.length > 1) {
    const cards = table.rows.slice(1).map(r => ({ name: r[0] || '', why: r[1] || '', guide: r[2] || '', prio: r[3] || '' }));
    const GROUPS = [['pro', 'Conviene pronto'], ['rut', 'De rutina'], ['inf', 'Según el caso']];
    const key = c => { const p = rvPrio(c.prio); return p === 'urg' ? 'pro' : p; };
    html = GROUPS.map(([k, label]) => { const list = cards.filter(c => key(c) === k); return list.length ? `<h5 class="rvgh ${k}">${label} <span class="s">(${list.length})</span></h5><div class="rvcards">${list.map(c => `<div class="rvcard st ${k}">
      <div><b class="rvct">${mdInline(c.name, sources)}</b><span class="exchip ${k}">${esc(c.prio.replace(/\*/g, ''))}</span>
      <p><span class="rvk">Por qué en tu caso</span> ${mdInline(c.why, sources)}</p>${c.guide ? `<p class="rvg"><span class="rvk">Guía o frecuencia</span> ${mdInline(c.guide, sources)}</p>` : ''}</div></div>`).join('')}</div>` : ''; }).join('');
  }
  return html + rvList(rest, sources);
}
function rvChecklist(blocks, sources, revId, kind) {   // «Qué comentar con mi médico» y «Datos que me faltan»
  const items = blocks.filter(b => b.t === 'ol' || (b.t === 'li' && b.depth === 0)), key = i => `house-rv-${revId}-${kind}-${i}`;
  const get = i => { try { return localStorage.getItem(key(i)) === '1'; } catch { return false; } };
  return `<ul class="rvcheck" data-kind="${kind}">${items.map((b, i) => { const [t, rest] = rvSplitTitle(b.text, true);
    return `<li><label><input type="checkbox" data-ck="${key(i)}" ${get(i) ? 'checked' : ''}><span>${t ? `<b>${mdInline(t, sources)}</b> ` : ''}${mdInline(rest, sources)}</span></label></li>`; }).join('')}</ul>
    ${kind === 'medico' ? '<button class="mini" id="rvcopy">Copiar esta lista</button>' : ''}`;
}
const RV_ICON = { datos: '🔎', relaciones: '🔗', estudios: '🧪', cuidados: '🌿', medico: '🩺', faltan: '❓' };
function rvKind(title) { const t = title.toLowerCase(); return /dicen|datos/.test(t) && !/faltan/.test(t) ? 'datos' : /relacion/.test(t) ? 'relaciones' : /estudios|chequeos/.test(t) ? 'estudios' : /cuidados|h[aá]bitos/.test(t) ? 'cuidados' : /comentar|m[eé]dico/.test(t) ? 'medico' : /faltan/.test(t) ? 'faltan' : ''; }
function rvSection(x, sources, revId) {
  const kind = rvKind(x.title), b = rvBlocks(x.lines);
  const body = kind === 'datos' ? rvData(b, sources) : kind === 'relaciones' ? rvCards(b, sources, true) : kind === 'estudios' ? rvStudies(b, sources)
    : kind === 'cuidados' ? rvCards(b, sources, false) : kind === 'medico' ? rvChecklist(b, sources, revId, 'medico') : kind === 'faltan' ? rvChecklist(b, sources, revId, 'faltan') : rvList(b, sources);
  const n = kind === 'datos' ? (body.match(/class="rvtopic"/g) || []).length : kind === 'estudios' ? (body.match(/class="rvcard/g) || []).length : kind ? (body.match(/class="rvcard"|<li><label>/g) || []).length : 0;
  return { kind, body, n };
}
function reviewHtml(text, sources, openAll, revId) {
  const secs = []; let cur = { title: '', lines: [] };
  for (const line of (text || '').split('\n')) {
    const h = line.match(/^\s*##\s+(.*)/);
    if (h) { secs.push(cur); cur = { title: h[1].trim(), lines: [] }; } else cur.lines.push(line);
  }
  secs.push(cur);
  const has = x => x.title || x.lines.some(l => l.trim());
  const summary = secs.find(x => /resumen/i.test(x.title)), rest = secs.filter(x => x !== summary && has(x));
  const nav = rest.filter(x => x.title).map((x, i) => `<button class="chip" data-gosec="${i}">${RV_ICON[rvKind(x.title)] || ''} ${esc(x.title)}</button>`).join('');
  return `${summary ? `<div class="rvhero"><h3>${esc(summary.title)}</h3>${execHtml(summary.lines, sources)}</div>` : ''}
    ${nav ? `<div class="rvnav"><span class="tip">Ir a:</span>${nav}<button class="mini" id="rvtoggle">${openAll ? 'Contraer todo' : 'Expandir todo'}</button></div>` : ''}
    ${rest.map((x, i) => { if (!x.title) return `<div class="rvbody">${rvList(rvBlocks(x.lines), sources)}</div>`; const s = rvSection(x, sources, revId);
      return `<details class="rvsec ${s.kind}" id="rvs-${i}" ${openAll ? 'open' : ''}><summary><span class="rvic">${RV_ICON[s.kind] || ''}</span>${esc(x.title)}${s.n ? `<span class="xn">${s.n}</span>` : ''}</summary><div class="rvsecbody">${s.body}</div></details>`; }).join('')}`;
}

const webLinks = list => list && list.length ? `<p class="tip" style="margin:8px 0 2px">Guías y páginas consultadas:</p><ul class="webs">${list.map(w => `<li><a href="${esc(w.url)}" target="_blank" rel="noopener noreferrer">${esc(w.title || w.url)}</a></li>`).join('')}</ul>` : '';

async function renderAssistant() {
  const st = await api('/api/assistant/status');
  if (!st.available) {
    view().innerHTML = `<section class="card empty"><h2>El asistente necesita Claude</h2>
      <p>Responde tus preguntas con lo que hay en tu expediente y te dice de qué documento salió cada dato. Para usarlo, conecta tu clave de Anthropic en Configuración.</p>
      ${S.me.is_admin ? '<button class="btn" id="go">Ir a Configuración</button>' : '<p class="tip">Pídele al administrador que lo conecte.</p>'}</section>`;
    const go = document.getElementById('go'); if (go) go.onclick = () => { S.tab = 'cfg'; renderShell(); };
    return;
  }
  const chat = (S.chat ||= {})[S.subject] ||= { msgs: [], busy: false, error: '' };
  const srcLink = x => x.document_id
    ? `<a href="/api/documents/${x.document_id}/file" target="_blank" rel="noopener">${esc(x.title)}${x.date ? ' · ' + fd(x.date) : ''}</a>`
    : `<button class="link" data-goexp="1">${esc(x.title)}</button>`;
  const draw = () => {
    document.getElementById('chatwrap').innerHTML = `<section class="card cfg ask">
      <div class="bar"><h2>Pregunta a tu expediente</h2>${chat.msgs.length ? '<button class="mini" id="newchat">Nueva conversación</button>' : ''}</div>
      <div class="chat" id="chat" aria-live="polite">
        ${chat.msgs.length ? '' : `<p class="tip">Respondo con lo que hay en tu expediente y con guías médicas oficiales: opino sobre tus hallazgos, sugiero estudios o cuidados y busco relaciones entre tus estudios. Cito el documento de cada dato. Soy una IA, no un médico: confirma todo con tu doctor.</p>
          <div class="chips">${ASK_IDEAS.map(q => `<button class="chip" data-idea="${esc(q)}">${esc(q)}</button>`).join('')}</div>`}
        ${chat.msgs.map(m => m.role === 'user' ? `<div class="msg me"><p>${esc(m.content)}</p></div>`
          : `<div class="msg bot">${answerHtml(m.content, m.sources || [])}
              ${m.warning ? '<p class="warnline">Esta respuesta menciona datos sin fuente: verifícalos en tus documentos.</p>' : ''}
              ${(m.sources || []).length ? `<ol class="srcs">${m.sources.map(x => `<li id="src-${x.n}" value="${x.n}">${srcLink(x)}</li>`).join('')}</ol>` : ''}${webLinks(m.web_sources)}${m.cost != null ? `<p class="tip" style="margin:6px 0 0;font-size:12px">Costo de esta respuesta: ≈ ${m.cost < 0.01 ? '<0.01' : m.cost.toFixed(2)} USD${m.fromReview ? ' · apoyada en tu última revisión' : ''}</p>` : ''}</div>`).join('')}
        ${chat.busy ? '<p class="tip"><span class="spin"></span>Buscando en tu expediente… puede tardar hasta un minuto.</p>' : ''}
        ${chat.error ? `<p class="err">${esc(chat.error)}</p>` : ''}
      </div>
      <form id="askf" class="askf"><textarea name="q" rows="2" maxlength="2000" placeholder="Escribe tu pregunta…" ${chat.busy ? 'disabled' : ''} aria-label="Tu pregunta"></textarea>
        <button class="btn" ${chat.busy ? 'disabled' : ''}>Preguntar</button></form>
      <p class="tip">Se envía a Claude lo que necesita para responder, sin tu nombre ni fecha de nacimiento; puede consultar guías médicas en internet. No reemplaza a tu médico.</p></section>`;
    const box = document.getElementById('chat'); box.scrollTop = box.scrollHeight;
    const nc = document.getElementById('newchat'); if (nc) nc.onclick = () => { chat.msgs = []; chat.error = ''; draw(); };
    view().querySelectorAll('[data-idea]').forEach(b => b.onclick = () => send(b.dataset.idea));
    view().querySelectorAll('[data-goexp]').forEach(b => b.onclick = () => { S.tab = 'exp'; renderShell(); });
    view().querySelectorAll('[data-cite]').forEach(a => a.onclick = e => { e.preventDefault(); const t = document.getElementById('src-' + a.dataset.cite); t?.scrollIntoView({ block: 'nearest' }); t?.classList.add('flash'); setTimeout(() => t?.classList.remove('flash'), 1200); });
    const f = document.getElementById('askf');
    f.onsubmit = e => { e.preventDefault(); const q = f.elements.q.value.trim(); if (q && !chat.busy) send(q); };
    f.elements.q.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); f.requestSubmit(); } };
    if (!chat.busy) f.elements.q.focus();
  };
  const send = async q => {
    chat.msgs.push({ role: 'user', content: q }); chat.busy = true; chat.error = ''; draw();
    try {
      const r = await api(`/api/people/${S.subject}/assistant`, { method: 'POST', body: { messages: chat.msgs.map(m => ({ role: m.role, content: m.content })) } });
      chat.msgs.push({ role: 'assistant', content: r.answer, sources: r.sources, warning: r.warning, web_sources: r.web_sources, cost: r.usage?.cost_usd, fromReview: r.used_review });
    } catch (e) { chat.error = e.message; chat.msgs.pop(); }
    chat.busy = false; if (S.tab === 'ask') draw();
  };
  view().innerHTML = '<div id="revcard"></div><div id="chatwrap" style="margin-top:12px"></div>';
  draw();
  paintReviews();
}

// Revisión integral: Claude analiza todo el historial con guías oficiales; corre en segundo plano.
const REV_STATE = { done: 'lista', running: 'analizando…', error: 'con error' };
async function paintReviews(forceOpen) {
  const box = document.getElementById('revcard');
  if (!box || S.tab !== 'ask') return;
  const revs = await api(`/api/people/${S.subject}/reviews`);
  const rv = ((S.rev ||= {})[S.subject] ||= { open: null });
  if (forceOpen) rv.open = forceOpen;
  if ((rv.open == null || !revs.some(r => r.id === rv.open)) && revs.length) rv.open = revs[0].id;
  const running = revs.find(r => r.status === 'running');
  const cur = rv.open ? await api(`/api/reviews/${rv.open}`).catch(() => null) : null;
  const body = !cur ? '' : cur.status === 'running' ? '<p class="tip"><span class="spin"></span>Claude está analizando todo tu historial y buscando en guías oficiales… puede tardar unos minutos. Puedes seguir usando House.</p>'
    : cur.status === 'error' ? `<p class="err">${esc(cur.error || 'La revisión falló.')}</p>`
    : `<div class="msg bot rvdoc" style="max-width:none">${reviewHtml(cur.content, cur.sources || [], rv.openAll, cur.id)}
        <details class="rvsec" id="rvsrc"><summary>Fuentes de tu expediente (${(cur.sources || []).length}) y guías consultadas (${(cur.web_sources || []).length})</summary>
        ${(cur.sources || []).length ? `<ol class="srcs">${cur.sources.map(x => `<li id="src-${x.n}" value="${x.n}">${x.document_id ? `<a href="/api/documents/${x.document_id}/file" target="_blank" rel="noopener">${esc(x.title)}${x.date ? ' · ' + fd(x.date) : ''}</a>` : esc(x.title)}</li>`).join('')}</ol>` : ''}
        ${webLinks(cur.web_sources)}${cur.web_note ? `<p class="tip">${esc(cur.web_note)}</p>` : ''}</details>
        <p class="tip" style="margin:8px 0 0">Orientación informativa generada por IA con tus datos y guías públicas; confírmala con tu médico.${cur.cost_usd ? ` Costo: ${cur.cost_usd.toFixed(2)} USD.` : ''}</p></div>
      <div class="bar"><span></span><button class="mini dn" data-delrev="${cur.id}">Borrar esta revisión</button></div>`;
  box.innerHTML = `<section class="card cfg"><div class="bar"><h2>Revisión integral</h2><button class="btn" id="newrev" ${running ? 'disabled' : ''}>${running ? 'Analizando…' : revs.length ? 'Hacer una nueva revisión' : 'Hacer mi primera revisión'}</button></div>
    <p class="tip">Claude revisa todo tu historial (laboratorios, informes, padecimientos, medicamentos, hábitos y antecedentes), lo compara con guías oficiales y te da su opinión, las relaciones entre tus estudios y los estudios o cuidados que podrías considerar. Tarda unos minutos y cuesta alrededor de 1 dólar. Confirma todo con tu médico.</p>
    ${revs.length ? `<div class="chips" role="group" aria-label="Revisiones anteriores">${revs.map(r => `<button class="chip" data-openrev="${r.id}" aria-pressed="${r.id === rv.open}">${fd(r.created_at.slice(0, 10))} · ${REV_STATE[r.status]}</button>`).join('')}</div>` : ''}
    ${body}</section>`;
  const nb = document.getElementById('newrev');
  if (nb) nb.onclick = async () => {
    if (!confirm('Se enviará tu historial completo a Claude para analizarlo (sin tu nombre) y buscará en guías médicas oficiales. Cuesta alrededor de 1 dólar y tarda unos minutos. ¿Continuar?')) return;
    nb.disabled = true;
    try { const r = await api(`/api/people/${S.subject}/reviews`, { method: 'POST' }); paintReviews(r.id); } catch (e) { nb.disabled = false; toast(e.message); }
  };
  box.querySelectorAll('[data-openrev]').forEach(b => b.onclick = () => { rv.open = Number(b.dataset.openrev); paintReviews(); });
  box.querySelectorAll('[data-delrev]').forEach(b => b.onclick = async () => {
    if (!confirm('¿Borrar esta revisión? No se puede deshacer.')) return;
    await api(`/api/reviews/${b.dataset.delrev}`, { method: 'DELETE' }); rv.open = null; paintReviews();
  });
  box.querySelectorAll('[data-cite]').forEach(a => a.onclick = e => { e.preventDefault(); const t = box.querySelector('#src-' + a.dataset.cite); if (t) { t.closest('details').open = true; t.scrollIntoView({ block: 'center' }); t.classList.add('flash'); setTimeout(() => t.classList.remove('flash'), 1400); } });
  box.querySelectorAll('[data-gosec]').forEach(b => b.onclick = () => { const d = box.querySelector('#rvs-' + b.dataset.gosec); if (d) { d.open = true; d.scrollIntoView({ block: 'start', behavior: 'smooth' }); } });
  box.querySelectorAll('input[data-ck]').forEach(i => i.onchange = () => { try { localStorage.setItem(i.dataset.ck, i.checked ? '1' : '0'); } catch { /* sin almacenamiento */ } });
  const cp = box.querySelector('#rvcopy');
  if (cp) cp.onclick = async () => { const txt = [...box.querySelectorAll('.rvcheck[data-kind="medico"] li')].map((l, n) => `${n + 1}. ${l.innerText.replace(/\s+/g, ' ').trim()}`).join('\n'); try { await navigator.clipboard.writeText(txt); toast('Copié la lista.'); } catch { toast('No pude copiar; selecciona el texto.'); } };
  const tg = box.querySelector('#rvtoggle');
  if (tg) tg.onclick = () => { rv.openAll = !rv.openAll; box.querySelectorAll('details.rvsec[id^=rvs-]').forEach(d => d.open = rv.openAll); tg.textContent = rv.openAll ? 'Contraer todo' : 'Expandir todo'; };
  clearTimeout(S.revTimer);
  if (running) S.revTimer = setTimeout(() => paintReviews(), 4000);  // solo actualiza esta tarjeta: no toca lo que escribes en el chat
}

/* ---------- Familia (solo administrador) ---------- */

async function renderFamily() {
  S.people = await api('/api/people');
  view().innerHTML = `<section class="card cfg"><h2>Familia</h2>
      ${S.people.map(p => `<div class="prof"><div><b>${esc(p.display_name)}</b>${p.is_admin ? ' · administrador' : ''}<br>
        <span class="tip">${fd(p.birth_date)} · ${p.sex_at_birth === 'F' ? 'Femenino' : 'Masculino'} · ${p.has_login ? 'Entra con su PIN' : 'Lo administras tú'}</span></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap"><button class="mini" data-see="${p.id}">Ver perfil</button>
        ${p.is_admin ? '' : `<button class="mini" data-pin="${p.id}">${p.has_login ? 'Restablecer PIN' : 'Darle acceso'}</button><button class="mini dn" data-del="${p.id}" data-n="${esc(p.display_name)}">Borrar</button>`}</div></div>`).join('')}
    </section>
    <section class="card cfg"><h2>Agregar a alguien</h2>
      <form class="fg2" id="f">
        <label>Nombre<input type="text" name="display_name" required maxlength="60"></label>
        <label>Fecha de nacimiento<input type="date" name="birth_date" required></label>
        <label>Sexo al nacer (define los rangos)<select name="sex_at_birth"><option value="F">Femenino</option><option value="M">Masculino</option></select></label>
        <label>Acceso<select name="access"><option value="pin">Entra con su propio PIN</option><option value="managed">Lo administro yo (sin acceso propio)</option></select></label>
        <label id="pinl">PIN inicial que le vas a entregar (4 a 8 dígitos)<input type="password" name="pin" inputmode="numeric" pattern="[0-9]{4,8}"></label>
        <p class="tip">Si tiene acceso propio, verá solo su perfil. Tú, como administrador, también puedes verlo; cada vez que lo hagas quedará registrado y esa persona podrá consultarlo.</p>
        <p class="err" id="e"></p><button class="btn">Agregar</button>
      </form></section>`;
  const f = document.getElementById('f');
  f.access.onchange = () => { document.getElementById('pinl').hidden = f.access.value !== 'pin'; };
  f.onsubmit = async ev => {
    ev.preventDefault();
    const v = Object.fromEntries(new FormData(f)), own = v.access === 'pin';
    try {
      await api('/api/people', { method: 'POST', body: { display_name: v.display_name, birth_date: v.birth_date, sex_at_birth: v.sex_at_birth,
        has_login: own, pin: own ? v.pin : null } });
      toast(`${v.display_name} agregado.`); renderFamily();
    } catch (e) { document.getElementById('e').textContent = e.message; }
  };
  view().querySelectorAll('[data-see]').forEach(b => b.onclick = () => { S.subject = Number(b.dataset.see); S.tab = 'res'; S.sel = null; renderShell(); });
  view().querySelectorAll('[data-pin]').forEach(b => b.onclick = async () => {
    const pin = prompt('Nuevo PIN (4 a 8 dígitos):'); if (!pin) return;
    try { await api(`/api/people/${b.dataset.pin}/pin`, { method: 'PUT', body: { pin } }); toast('PIN actualizado.'); renderFamily(); } catch (e) { toast(e.message); }
  });
  view().querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (prompt(`Esto borra el perfil de ${b.dataset.n} con todos sus estudios y resultados. Escribe su nombre para confirmar:`) !== b.dataset.n) return;
    await api(`/api/people/${b.dataset.del}`, { method: 'DELETE' }); toast('Perfil borrado.'); renderFamily();
  });
}

/* ---------- Configuración y privacidad ---------- */

async function accessLogHtml() {
  const log = await api('/api/access-log');
  return log.length ? `<div class="tblwrap"><table><thead><tr><th>Cuándo</th><th>Quién</th><th>Perfil</th><th>Qué hizo</th></tr></thead><tbody>
    ${log.slice(0, 50).map(a => `<tr><td>${esc(a.at)}</td><td>${esc(a.actor)}</td><td>${esc(a.subject)}</td><td>${esc(a.action.replaceAll('_', ' '))}</td></tr>`).join('')}</tbody></table></div>` : '<p class="tip">Nadie más ha entrado a este perfil.</p>';
}

async function renderConfig() {
  const s = await api('/api/settings');
  const reader = { claude: 'Lector básico en esta Mac; Claude (Anthropic) solo si no entiende el formato', basico: 'Lector básico en esta Mac, sin IA', configurado: 'Según house.toml' }[s.reader] || s.reader;
  view().innerHTML = `<section class="card cfg"><h2>Lectura de estudios</h2>
      <div class="kv"><b>Ahora lee</b><span>${esc(reader)}</span>
        <b>Gasto de este mes</b><span>${s.month_usd.toFixed(2)} USD de ${s.budget_usd} USD (${s.month_calls} lecturas)</span></div>
      ${s.reader === 'basico' ? `<p class="tip">El lector básico no envía nada fuera de tu Mac y funciona con los formatos que ya conoce (como Chopo). Para cualquier laboratorio, conecta Claude:</p>
        <ol class="tip" style="margin:0;padding-left:20px"><li>Entra a <b>console.anthropic.com</b> e inicia sesión (o crea tu cuenta y agrega un método de pago).</li>
        <li>En <b>API Keys</b>, crea una clave nueva y cópiala.</li><li>Pégala aquí. Se guarda cifrada en esta Mac.</li></ol>
        <form class="bar" id="kf"><input type="password" name="key" placeholder="sk-ant-…" style="flex:1" autocomplete="off"><button class="btn">Guardar clave</button></form>`
      : s.reader === 'claude' ? `<p class="tip">House lee primero en esta Mac, sin enviar nada. Solo si no entiende el formato de un estudio, se lo manda a Claude, y antes le quita tu nombre, fecha de nacimiento y números de registro. Puedes ver exactamente lo que se envió al revisar cada estudio.</p>
        <div><button class="btn danger" id="rm">Quitar la clave (volver al lector básico)</button></div>` : ''}
      <p class="err" id="e"></p></section>
    <section class="card cfg" id="bkcard"></section>
    <section class="card cfg"><h2>Quién ha visto qué</h2>${await accessLogHtml()}</section>
    <section class="card cfg"><h2>Dónde están tus datos</h2><p class="tip">En esta Mac: <b>${esc(s.data_dir)}</b>. El disco está cifrado con FileVault y los PDF, además, con una llave guardada en el llavero de macOS.</p></section>`;
  renderBackupCard(await api('/api/backup'));
  const kf = document.getElementById('kf');
  if (kf) kf.onsubmit = async ev => {
    ev.preventDefault();
    try { await api('/api/settings/anthropic-key', { method: 'PUT', body: { key: kf.key.value } }); toast('Listo: House leerá tus estudios con Claude.'); renderConfig(); }
    catch (e) { document.getElementById('e').textContent = e.message; }
  };
  const rm = document.getElementById('rm');
  if (rm) rm.onclick = async () => { if (!confirm('¿Quitar la clave de Claude?')) return; await api('/api/settings/anthropic-key', { method: 'DELETE' }); renderConfig(); };
}

/* ---------- Respaldo ---------- */

const fdt = iso => new Date(iso).toLocaleString('es-MX', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const ago = d => d == null ? '' : d < 1 ? 'hoy' : d < 2 ? 'ayer' : `hace ${Math.floor(d)} días`;

async function backupBanner() {
  const el = document.getElementById('bkbanner');
  if (!el) return;
  try {
    const b = await api('/api/backup');
    const msg = !b.configured ? 'Todavía no tienes respaldo de tus datos. Si esta Mac se daña o se pierde, se pierde todo lo que has subido.'
      : b.overdue ? `Tu último respaldo es ${b.last_at ? ago(b.age_days) : 'inexistente: nunca se ha completado uno'}. Revisa que la carpeta de destino esté disponible.` : '';
    if (!msg) return;
    el.innerHTML = `<div class="warnb bar"><span>${esc(msg)}</span><button class="mini" id="bkgo2">${b.configured ? 'Ver respaldo' : 'Activar respaldo'}</button></div>`;
    document.getElementById('bkgo2').onclick = () => { S.tab = 'cfg'; renderShell(); };
  } catch { /* el aviso es opcional */ }
}

function renderBackupCard(b, freshKey) {
  const card = document.getElementById('bkcard');
  const short = p => p.replace(/^\/Users\/[^/]+\/Library\/CloudStorage\//, '…/').replace(/^\/Users\/[^/]+\//, '~/');
  if (freshKey) {
    card.innerHTML = `<h2>Guarda tu llave de recuperación</h2>
      <div class="okb">Respaldo activado en «${esc(short(freshKey.destination))}».</div>
      <div class="anon" style="font-size:17px;letter-spacing:.06em;user-select:all;text-align:center">${esc(freshKey.recovery_key)}</div>
      <p class="tip"><b>Esta llave se muestra una sola vez y House no la guarda.</b> Sin ella nadie, ni siquiera House, puede abrir un respaldo. Anótala en papel o guárdala en tu gestor de contraseñas, en un lugar que no dependa de esta Mac.</p>
      <div class="bar"><button class="mini" id="copyk">Copiar la llave</button><label><input type="checkbox" id="saved"> Ya guardé mi llave en un lugar seguro</label></div>
      <p class="err" id="bke"></p>
      <button class="btn" id="bkgo" disabled>Continuar y hacer el primer respaldo</button>`;
    document.getElementById('copyk').onclick = async () => { try { await navigator.clipboard.writeText(freshKey.recovery_key); toast('Llave copiada.'); } catch { toast('No pude copiarla; selecciónala y cópiala a mano.'); } };
    document.getElementById('saved').onchange = e => { document.getElementById('bkgo').disabled = !e.target.checked; };
    document.getElementById('bkgo').onclick = async () => {
      const btn = document.getElementById('bkgo'); btn.disabled = true; btn.textContent = 'Respaldando…';
      try { renderBackupCard(await api('/api/backup/run', { method: 'POST' })); toast('Primer respaldo listo.'); backupBanner(); }
      catch (e) { document.getElementById('bke').textContent = e.message; btn.disabled = false; btn.textContent = 'Reintentar el primer respaldo'; }
    };
    return;
  }
  if (!b.configured) {
    card.innerHTML = `<h2>Respaldo de tus datos</h2>
      <div class="warnb">Hoy no tienes ningún respaldo. Si esta Mac se daña o se pierde, se pierde todo lo que has subido.</div>
      <p class="tip">House guarda una copia cifrada de tus resultados y de tus PDF en la carpeta que elijas. Conviene que sea en la nube o en un disco externo: una carpeta solo en esta Mac no te protege si falla. Solo se abre con una llave de recuperación que House te muestra una vez y no guarda.</p>
      <form class="fg2" id="bkf"><label>Carpeta de destino<input type="text" name="dest" placeholder="/Users/…/House respaldo" autocomplete="off" style="width:100%"></label>
        ${b.suggestions.length ? `<div class="sugs" style="display:flex;flex-wrap:wrap;gap:6px"><span class="tip">Sugerencias:</span>${b.suggestions.map(x => `<button type="button" class="mini" data-sug="${esc(x)}">${esc(short(x))}</button>`).join('')}</div>` : ''}
        <p class="err" id="bke"></p><button class="btn">Activar respaldo</button></form>`;
    card.querySelectorAll('[data-sug]').forEach(x => x.onclick = () => { card.querySelector('[name=dest]').value = x.dataset.sug; });
    document.getElementById('bkf').onsubmit = async ev => {
      ev.preventDefault();
      try { renderBackupCard(null, await api('/api/backup/setup', { method: 'POST', body: { destination: ev.target.dest.value } })); }
      catch (e) { document.getElementById('bke').textContent = e.message; }
    };
    return;
  }
  card.innerHTML = `<h2>Respaldo de tus datos</h2>
    <div class="kv"><b>Carpeta</b><span>${esc(short(b.destination))}</span>
      <b>Último respaldo</b><span>${b.last_at ? `${fdt(b.last_at)} (${ago(b.age_days)}) · ${(b.last_size / 1048576).toFixed(1)} MB` : 'todavía ninguno'}</span>
      <b>Cuándo se respalda</b><span>Solo, una vez al día mientras House esté abierto. Se conservan los últimos ${b.keep}.</span></div>
    ${b.overdue ? '<div class="warnb">El respaldo está atrasado. Revisa que la carpeta de destino esté disponible y respalda ahora.</div>' : ''}
    ${b.last_error ? `<p class="err">Último intento: ${esc(b.last_error)}</p>` : ''}
    <div style="display:flex;flex-wrap:wrap;gap:8px"><button class="btn" id="bkrun">Respaldar ahora</button><button class="btn ghost" id="bkver">Verificar el último respaldo</button>
      <button class="btn ghost" id="bkdest">Cambiar carpeta</button><button class="btn danger" id="bkoff">Desactivar</button></div>
    <div id="bkextra"></div>`;
  const extra = document.getElementById('bkextra');
  document.getElementById('bkrun').onclick = async () => {
    const btn = document.getElementById('bkrun'); btn.disabled = true; btn.textContent = 'Respaldando…';
    try { renderBackupCard(await api('/api/backup/run', { method: 'POST' })); toast('Respaldo listo.'); backupBanner(); }
    catch (e) { extra.innerHTML = `<p class="err">${esc(e.message)}</p>`; btn.disabled = false; btn.textContent = 'Respaldar ahora'; }
  };
  document.getElementById('bkver').onclick = () => {
    extra.innerHTML = `<form class="fg2" id="vf"><p class="tip">Un respaldo que nunca se probó no es una garantía. Pega tu llave de recuperación para comprobar que el último respaldo se abre y sus datos están completos. La llave no se guarda.</p>
      <input type="password" name="key" placeholder="Tu llave de recuperación" autocomplete="off" style="width:100%"><p class="err" id="vfe"></p><button class="btn">Verificar</button></form>`;
    document.getElementById('vf').onsubmit = async ev => {
      ev.preventDefault();
      const btn = ev.target.querySelector('button'); btn.disabled = true; btn.textContent = 'Verificando…';
      try {
        const r = await api('/api/backup/verify', { method: 'POST', body: { recovery_key: ev.target.key.value } });
        extra.innerHTML = `<div class="okb">Respaldo sano. Creado el ${esc(fdt(r.created_at))}: ${r.counts.person} ${r.counts.person === 1 ? 'persona' : 'personas'}, ${r.counts.observation} resultados, ${r.counts.imaging_study} informes de imagen y ${r.originals} PDF originales. Se pueden recuperar.</div>`;
      } catch (e) { document.getElementById('vfe').textContent = e.message; btn.disabled = false; btn.textContent = 'Verificar'; }
    };
  };
  document.getElementById('bkdest').onclick = () => {
    extra.innerHTML = `<form class="fg2" id="df"><label>Nueva carpeta de destino<input type="text" name="dest" value="${esc(b.destination)}" style="width:100%"></label><p class="err" id="dfe"></p><button class="btn">Guardar</button></form>`;
    document.getElementById('df').onsubmit = async ev => {
      ev.preventDefault();
      try { renderBackupCard(await api('/api/backup/destination', { method: 'PUT', body: { destination: ev.target.dest.value } })); toast('Carpeta actualizada.'); }
      catch (e) { document.getElementById('dfe').textContent = e.message; }
    };
  };
  document.getElementById('bkoff').onclick = async () => {
    if (!confirm('¿Desactivar el respaldo? Los archivos ya creados se conservan, pero ya no se harán nuevos. Al volver a activarlo se genera otra llave de recuperación.')) return;
    renderBackupCard(await api('/api/backup', { method: 'DELETE' })); backupBanner();
  };
}

async function renderPrivacy() {
  view().innerHTML = `<section class="card cfg"><h2>Quién ha visto tu perfil</h2>
    <p class="tip">El administrador puede ver tu perfil; cada vez que lo hace queda registrado aquí.</p>${await accessLogHtml()}</section>`;
}

boot().catch(e => { if (e.status !== 401) app.innerHTML = `<div class="screen"><div class="panel"><h1>No pude arrancar</h1><p>${esc(e.message)}</p></div></div>`; });
