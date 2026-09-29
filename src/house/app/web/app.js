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
  const tabs = [['res', 'Resumen'], ['exp', 'Expediente'], ['doc', 'Documentos'], ['img', 'Estudios']].concat(admin ? [['fam', 'Familia'], ['cfg', 'Configuración']] : [['priv', 'Privacidad']]);
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
  ({ res: renderSummary, exp: renderClinical, img: renderImaging, doc: renderDocs, fam: renderFamily, cfg: renderConfig, priv: renderPrivacy })[S.tab]();
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
  if (o.ref_low != null && o.ref_high != null) return `${fnum(o.ref_low)} a ${fnum(o.ref_high)}`;
  if (o.ref_high != null) return `hasta ${fnum(o.ref_high)}`;
  if (o.ref_low != null) return `desde ${fnum(o.ref_low)}`;
  return 'sin referencia';
}

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

  const hero = `<section class="card hero">
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
    ${methods.length > 1 ? `<div class="mixed">Ojo: estos resultados se midieron con métodos distintos (${methods.map(esc).join(', ')}). Compara la tendencia con cautela.</div>` : ''}
    <div class="chartbox" id="chart" ${S.view === 'g' ? '' : 'hidden'}></div>
    <div class="tblwrap" ${S.view === 't' ? '' : 'hidden'}><table><thead><tr><th>Fecha</th><th>Resultado</th><th>Estado</th><th>Referencia</th><th>Estudio</th><th>Método</th></tr></thead><tbody>
      ${[...pts].reverse().map(o => `<tr><td>${fd(o.collected_on)}</td><td><b>${vnum(o)}</b> ${esc(o.unit)}</td><td>${pill(o.status)}</td><td>${esc(refText(o))}</td><td>${esc(o.document_title)}</td><td>${o.entered_manually ? 'Agregado a mano' : esc(o.method || '—')}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="legend"><span><i class="lg-line"></i>Tus resultados</span>${refPt ? `<span><i class="lg-band"></i>Rango de referencia${refPt === l ? ' del último estudio' : ` (del estudio del ${fd(refPt.collected_on)}; el último no lo trae)`}: ${esc(bandText(refPt))}</span>` : ''}</div>`;
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

async function renderDocs() {
  const docs = await api(`/api/people/${S.subject}/documents`);
  view().innerHTML = `
    <section class="card cfg">
      <h2>Subir un estudio</h2>
      <label class="drop" id="drop"><b>Arrastra aquí un PDF o haz clic para elegirlo</b>
        <span class="tip">Por ahora, estudios de laboratorio en PDF digital (no escaneos). Se guardan cifrados en esta Mac.</span>
        <input type="file" id="file" accept="application/pdf,.pdf" hidden multiple></label>
      <div id="up"></div>
    </section>
    <section class="card cfg"><h2>Estudios</h2>
      ${docs.length ? `<div class="tblwrap"><table><thead><tr><th>Estudio</th><th>Fecha</th><th>Estado</th><th></th></tr></thead><tbody>
        ${docs.map(d => `<tr><td><b>${esc(d.title)}</b><small style="display:block;color:var(--muted)">${d.doc_type === 'imagen' ? 'Informe de estudio' : 'Laboratorio'}</small></td><td>${fd(d.collected_on)}</td>
          <td>${d.review_state === 'revisada' ? (d.doc_type === 'imagen' ? `Revisado · ${d.results} ${d.results === 1 ? 'informe' : 'informes'}` : `Revisado · ${d.results} resultados`) : esc(STATE[d.review_state])}</td>
          <td><div class="acts" style="display:flex;gap:6px;flex-wrap:wrap">
            ${d.review_state === 'pendiente' ? `<button class="mini" data-rev="${d.id}">Revisar</button>` : ''}
            <a class="mini" href="/api/documents/${d.id}/file" target="_blank" rel="noopener" style="text-decoration:none">Ver original</a>
            <button class="mini dn" data-del="${d.id}" data-t="${esc(d.title)}">Borrar</button></div></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="tip">Aún no hay estudios.</p>'}
    </section>`;
  const drop = document.getElementById('drop'), input = document.getElementById('file');
  input.onchange = () => uploadFiles([...input.files]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); uploadFiles([...e.dataTransfer.files]); };
  view().querySelectorAll('[data-rev]').forEach(b => b.onclick = () => openReview(Number(b.dataset.rev)));
  view().querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm(`¿Borrar «${b.dataset.t}» y todos sus resultados? No se puede deshacer.`)) return;
    await api(`/api/documents/${b.dataset.del}`, { method: 'DELETE' }); toast('Estudio borrado.'); renderDocs();
  });
}

async function uploadFiles(files) {
  const up = document.getElementById('up');
  let last = null;
  for (const f of files) {
    up.innerHTML = `<p class="tip"><span class="spin"></span>Leyendo «${esc(f.name)}»… Con Claude puede tardar hasta un minuto.</p>`;
    const form = new FormData(); form.append('file', f);
    try {
      const r = await api(`/api/people/${S.subject}/documents`, { method: 'POST', form });
      last = r.document_id;
      toast(r.kind === 'imagen' ? `Leí ${r.rows} ${r.rows === 1 ? 'informe' : 'informes'} de imagen en esta Mac.` : r.reader === 'basico' ? `Leído con el lector básico: ${r.rows} renglones.` : `Leído: ${r.rows} renglones.`);
    } catch (e) {
      if (e.status === 409 && e.detail?.document_id) { toast('Ese estudio ya estaba cargado.'); continue; }
      up.innerHTML = `<p class="err">«${esc(f.name)}»: ${esc(e.message)}</p>`;
      return;
    }
  }
  up.innerHTML = '';
  if (last && files.length === 1) openReview(last); else renderDocs();
}

const PROBLEMS = {
  no_respaldada_por_el_documento: 'No aparece tal cual en el documento',
  analito_desconocido: 'Análisis no reconocido',
  unidad_no_reconocida: 'Unidad no reconocida',
  valor_no_numerico: 'Valor no numérico',
  valor_implausible: 'Valor poco probable: revísalo',
  mismo_valor_en_otra_unidad: 'Es el mismo resultado en otra unidad: no se guardará dos veces',
  aparece_mas_de_una_vez: 'Aparece más de una vez en este estudio',
};

async function openReview(id) {
  const [d, layout] = await Promise.all([api(`/api/documents/${id}`), api(`/api/documents/${id}/layout`).catch(() => ({ pages: [], boxes: {} }))]);
  if (d.document.doc_type === 'imagen') return openImagingReview(id, d, layout);
  const rows = d.rows.map(r => ({ ...r, accept: !!r.analyte_key && !r.problems.includes('no_respaldada_por_el_documento') && !r.problems.includes('unidad_no_reconocida') && !r.problems.includes('valor_no_numerico') && !r.problems.includes('mismo_valor_en_otra_unidad') }));
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
      <div class="bar"><h2>Revisa «${esc(d.document.title)}»</h2><div style="display:flex;gap:6px"><button class="mini" id="reread" title="Útil si House mejoró o si conectaste Claude">Volver a leer</button><a class="mini" href="/api/documents/${id}/file" target="_blank" rel="noopener" style="text-decoration:none">Abrir PDF</a><button class="mini" id="back">Volver</button></div></div>
      <datalist id="catlist">${[...byName.keys()].sort().map(n => `<option value="${esc(S.catalog[byName.get(n)].name)}">`).join('')}</datalist>
      <p class="tip">Toca un resultado para ver su renglón resaltado en el original. Corrige lo necesario y desmarca lo que no quieras guardar. Si House no reconoció un análisis, dile cuál es; si falta uno, agrégalo abajo. Solo lo que confirmes entra a tu expediente.</p>
      <div class="bar"><label class="kv"><b>Fecha de toma</b><input type="date" id="date" value="${esc(d.document.collected_on || '')}"></label>
        <label><input type="checkbox" id="only" ${onlyFlags ? 'checked' : ''}> Solo lo que requiere atención</label>
        <button class="mini" id="ai">${showAi ? 'Ocultar' : 'Ver'} lo que vio la IA</button></div>
      ${showAi ? `<div class="anon">${esc(d.ai_saw)}</div><p class="tip">Datos personales quitados antes de enviar: ${Object.entries(d.redactions).map(([k, v]) => `${esc(k.toLowerCase())} (${v})`).join(', ') || 'ninguno'}.</p>` : ''}
      <div class="tblwrap"><table class="rt"><thead><tr><th></th><th>Análisis</th><th>Resultado</th><th>Referencia y estado</th></tr></thead><tbody>
        ${shown.map(r => `<tr data-row="${r.id}" class="${r.accept ? (r.needs_attention ? 'fl2' : '') : 'skip'} ${r.id === active ? 'active' : ''}">
          <td><input type="checkbox" data-acc="${r.id}" ${r.accept ? 'checked' : ''} ${(r.assigned || r.analyte_key) ? '' : 'disabled'} aria-label="Guardar"></td>
          <td><b>${esc(r.assigned ? S.catalog[r.assigned].name : (r.name || r.printed_name))}</b><small>${esc(r.printed_name)}${r.section ? ' · ' + esc(r.section) : ''}${layout.boxes[r.id] ? ` · pág. ${layout.boxes[r.id].page}` : ''}</small>
            ${r.analyte_key ? '' : `<input list="catlist" data-assign="${r.id}" placeholder="¿Qué análisis es? Escribe para buscar" value="${r.assigned ? esc(S.catalog[r.assigned].name) : ''}" style="width:100%;margin-top:4px">${r.assigned ? '' : '<span class="flag">No reconocido: dime qué análisis es o no se guardará</span>'}`}
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
      <div class="bar"><button class="btn danger" id="discard">Descartar estudio</button><button class="btn" id="ok">Confirmar ${n + manual.length} ${n + manual.length === 1 ? 'resultado' : 'resultados'}</button></div>`;
    document.getElementById('back').onclick = () => renderDocs();
    document.getElementById('reread').onclick = async () => {
      if (!confirm('¿Volver a leer este estudio? Se pierden las correcciones que no hayas confirmado.')) return;
      const b = document.getElementById('reread'); b.disabled = true; b.textContent = 'Leyendo…';
      try { const r = await api(`/api/documents/${id}/reread`, { method: 'POST' }); toast(`Leído de nuevo: ${r.rows} renglones.`); openReview(id); }
      catch (err) { document.getElementById('e').textContent = err.message; b.disabled = false; b.textContent = 'Volver a leer'; }
    };
    document.getElementById('only').onchange = e => { onlyFlags = e.target.checked; draw(); };
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
    view().querySelectorAll('[data-acc]').forEach(c => c.onchange = () => { rows.find(r => r.id == c.dataset.acc).accept = c.checked; draw(); });
    view().querySelectorAll('[data-val]').forEach(i => i.oninput = () => { rows.find(r => r.id == i.dataset.val).edited = i.value; });
    document.getElementById('discard').onclick = async () => {
      if (!confirm('¿Descartar este estudio? Se borra el original y no se guarda ningún resultado.')) return;
      await api(`/api/documents/${id}`, { method: 'DELETE' }); toast('Estudio descartado.'); renderDocs();
    };
    document.getElementById('ok').onclick = async () => {
      const e = document.getElementById('e'), date = document.getElementById('date').value;
      if (!date) return (e.textContent = 'Indica la fecha de toma del estudio.');
      const decisions = [];
      for (const r of rows) {
        const dd = { row_id: r.id, accept: r.accept };
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
        const res = await api(`/api/documents/${id}/review`, { method: 'POST', body: { collected_on: date, decisions, manual } });
        toast(`Guardé ${res.saved} resultados en tu expediente.`); S.tab = 'res'; renderShell();
      } catch (err) { e.textContent = err.message; }
    };
  };
  draw();
}

/* ---------- Expediente clínico ---------- */

const F = (name, label, type = 'text', extra = {}) => ({ name, label, type, ...extra });
const STCLS = { 'En control': 'c', 'En tratamiento': 'a', 'Seguimiento': 'a', 'Resuelta': 'r' };
const CLIN = {
  allergy: { key: 'allergies', title: 'Alergias', add: 'Agregar alergia', none: 'Sin alergias conocidas',
    fields: [F('substance', 'Sustancia', 'text', { list: 'allergy' }), F('reaction', 'Reacción (opcional)'), F('notes', 'Notas (opcional)')],
    line: a => `<b>${esc(a.substance)}</b>${a.reaction ? ' · ' + esc(a.reaction) : ''}`, sub: a => a.notes },
  problem: { key: 'problems', title: 'Problemas de salud', add: 'Agregar problema', none: 'Sin problemas de salud conocidos',
    fields: [F('name', 'Problema', 'text', { list: 'problem' }), F('status', 'Estado', 'select', { options: 'statuses' }), F('since_year', 'Desde (año)', 'text', { ph: '2021' }), F('notes', 'Notas (opcional)')],
    line: p => `<b>${esc(p.name)}</b>${p.since_year ? ` <span class="s">desde ${esc(p.since_year)}</span>` : ''}`, sub: p => p.notes,
    badge: p => `<span class="sp ${STCLS[p.status] || 'c'}">${esc(p.status)}</span>` },
  medication: { key: 'medications', title: 'Medicamentos', add: 'Agregar medicamento', none: 'Sin medicamentos actuales',
    fields: [F('name', 'Medicamento', 'text', { list: 'medication' }), F('dose', 'Dosis (por ejemplo 50 mg al día)'), F('reason', 'Para qué'), F('prescriber', 'Médico que lo indicó (opcional)'),
      F('since_year', 'Desde (año)', 'text', { ph: '2022' }), F('until_year', 'Hasta (año, si ya lo suspendiste)', 'text', { ph: '2024' }), F('active', 'Lo tomo actualmente', 'check')],
    line: m => `<b>${esc(m.name)}</b>${m.dose ? ' · ' + esc(m.dose) : ''}`,
    sub: m => [m.reason, m.prescriber && 'indicado por ' + m.prescriber, m.since_year && 'desde ' + m.since_year + (m.until_year ? ' hasta ' + m.until_year : '')].filter(Boolean).join(', ') },
  family: { key: 'family', title: 'Antecedentes familiares', add: 'Agregar antecedente', none: 'Sin antecedentes familiares relevantes',
    fields: [F('relative', 'Parentesco', 'text', { list: 'relative' }), F('condition', 'Condición', 'text', { list: 'problem' })],
    line: f => `<b>${esc(f.relative)}</b> · ${esc(f.condition)}`, sub: () => '' },
  procedure: { key: 'procedures', title: 'Cirugías', add: 'Agregar cirugía', none: 'Sin cirugías previas',
    fields: [F('name', 'Procedimiento', 'text', { list: 'procedure' }), F('year', 'Año', 'text', { ph: '2012' }), F('notes', 'Notas (opcional)')],
    line: x => `<b>${esc(x.name)}</b>${x.year ? ` <span class="s">${esc(x.year)}</span>` : ''}`, sub: x => x.notes },
  vaccine: { key: 'vaccines', title: 'Vacunas', add: 'Agregar vacuna',
    fields: [F('name', 'Vacuna', 'text', { list: 'vaccine' }), F('given_on', 'Fecha', 'date'), F('dose_label', 'Dosis (por ejemplo refuerzo)'), F('place', 'Dónde (opcional)')],
    line: v => `<b>${esc(v.name)}</b> <span class="s">${fd(v.given_on)}</span>`, sub: v => [v.dose_label, v.place].filter(Boolean).join(' · ') },
  consultation: { key: 'consultations', title: 'Consultas', add: 'Agregar consulta',
    fields: [F('occurred_on', 'Fecha', 'date'), F('reason', 'Motivo o resumen'), F('doctor', 'Médico (opcional)'), F('specialty', 'Especialidad (opcional)'), F('notes', 'Notas (opcional)')],
    line: c => `<b>${esc(c.reason)}</b> <span class="s">${fd(c.occurred_on)}</span>`, sub: c => [c.specialty, c.doctor, c.notes].filter(Boolean).join(' · ') },
};
const HAS_NONE = ['allergy', 'problem', 'medication', 'family', 'procedure'];
const EV_KIND = { consulta: 'Consulta', laboratorio: 'Laboratorio', imagen: 'Imagen', estudio: 'Otro estudio', vacuna: 'Vacuna', cirugia: 'Cirugía' };

async function renderClinical() {
  view().innerHTML = '<p class="tip"><span class="spin"></span>Cargando…</p>';
  const [c, sug] = await Promise.all([api(`/api/people/${S.subject}/clinical`), S.sug ? Promise.resolve(S.sug) : api('/api/clinical/suggestions')]);
  S.sug = sug;
  const reload = () => renderClinical();
  const itemsOf = kind => c[CLIN[kind].key];
  const row = (kind, it) => { const cfg = CLIN[kind], sub = cfg.sub(it);
    return `<li><span>${cfg.line(it)}${sub ? `<br><span class="s">${esc(sub)}</span>` : ''}${it.duplicate ? '<span class="flag">Aparece más de una vez</span>' : ''}</span>
      <span class="rowact">${cfg.badge ? cfg.badge(it) : ''}<button class="mini" data-edit="${kind}:${it.id}">Editar</button><button class="mini dn" data-del="${kind}:${it.id}">Quitar</button></span></li>`; };
  const card = kind => {
    const cfg = CLIN[kind]; let items = itemsOf(kind), past = [];
    if (kind === 'medication') { past = items.filter(m => !m.active); items = items.filter(m => m.active); }
    const confirmed = c.none.includes(kind);
    const empty = items.length ? '' : HAS_NONE.includes(kind)
      ? (confirmed ? `<div class="alg none">${esc(cfg.none)}</div><button class="link" data-none-off="${kind}">Quitar la confirmación</button>`
        : `<p class="s" style="margin:0">Sin registrar todavía.</p><button class="mini" data-none="${kind}">Confirmar: ${esc(cfg.none.toLowerCase())}</button>`)
      : '<p class="s" style="margin:0">Sin registros.</p>';
    return `<section class="card xc"><h3>${cfg.title}<button class="mini add-x" data-add="${kind}">${cfg.add}</button></h3>
      ${items.length ? `<ul class="xl">${items.map(it => row(kind, it)).join('')}</ul>` : empty}
      ${past.length ? `<details class="hist"><summary>Suspendidos (${past.length})</summary><ul class="xl">${past.map(it => row(kind, it)).join('')}</ul></details>` : ''}</section>`;
  };
  const chips = ['todo', ...Object.keys(EV_KIND)];
  const evs = c.timeline.filter(e => S.clinFilter === 'todo' || e.kind === S.clinFilter);
  const editing = S.clinEdit;
  view().innerHTML = `
    <div id="clinform"></div>
    <div class="xg">${['allergy', 'problem', 'medication', 'family', 'procedure', 'vaccine', 'consultation'].map(card).join('')}</div>
    <section class="card xc"><h3>Historial cronológico</h3>
      <div class="chips" role="group" aria-label="Filtrar por tipo">${chips.map(k => `<button class="chip" data-f="${k}" aria-pressed="${k === S.clinFilter}">${k === 'todo' ? 'Todo' : EV_KIND[k]}</button>`).join('')}</div>
      <div class="tl">${evs.length ? evs.map(e => `<div class="ev"><div class="dt">${e.approx ? esc(e.date.slice(0, 4)) : fd(e.date)}</div><div>
          <div class="tt"><span class="ty">${e.modality && e.kind !== 'imagen' ? esc(e.modality) : EV_KIND[e.kind]}</span>${esc(e.title)}${e.flag ? ' ' + flagPill(e.flag) : ''}</div>
          ${e.subtitle ? `<div class="sb">${esc(e.subtitle)}</div>` : ''}
          ${e.ref ? `<a class="mini" href="/api/documents/${e.ref.id}/file" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-top:4px">Ver original</a>` : ''}</div></div>`).join('')
        : '<p class="tip">Todavía no hay eventos. Sube estudios o agrega consultas, vacunas y cirugías.</p>'}</div></section>`;

  view().querySelectorAll('[data-f]').forEach(b => b.onclick = () => { S.clinFilter = b.dataset.f; reload(); });
  view().querySelectorAll('[data-add]').forEach(b => b.onclick = () => { S.clinEdit = { kind: b.dataset.add, item: null }; reload(); });
  view().querySelectorAll('[data-edit]').forEach(b => b.onclick = () => {
    const [kind, id] = b.dataset.edit.split(':'); S.clinEdit = { kind, item: itemsOf(kind).find(x => x.id == id) }; reload();
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
      if (f.type === 'select') return `<select name="${f.name}">${c[f.options].map(o => `<option ${o === (it[f.name] || c[f.options][0]) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
      if (f.type === 'check') return `<input type="checkbox" name="${f.name}" ${v ? 'checked' : ''}>`;
      return `<input type="${f.type === 'date' ? 'date' : 'text'}" name="${f.name}" value="${esc(v)}" ${f.list ? `list="dl_${f.list}"` : ''} ${f.ph ? `placeholder="${esc(f.ph)}"` : ''} autocomplete="off" style="width:100%">`;
    };
    document.getElementById('clinform').innerHTML = `<form class="card cfg fg2" id="cf"><h2>${editing.item ? 'Editar' : cfg.add}</h2>
      ${lists.map(l => `<datalist id="dl_${l}">${(S.sug[l] || []).map(x => `<option value="${esc(x)}">`).join('')}</datalist>`).join('')}
      ${cfg.fields.map(f => `<label>${esc(f.label)}${input(f)}</label>`).join('')}
      <p class="err" id="cfe"></p><div class="bar"><button type="button" class="mini" id="cfc">Cancelar</button><button class="btn">Guardar</button></div></form>`;
    document.getElementById('cf').elements[0].focus();
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

async function renderImaging() {
  view().innerHTML = '<p class="tip"><span class="spin"></span>Cargando…</p>';
  const list = await api(`/api/people/${S.subject}/imaging`);
  if (!list.length) {
    view().innerHTML = `<section class="card empty"><h2>Todavía no hay informes de estudios</h2>
      <p>Sube el informe en PDF, digital o escaneado (radiografía, ultrasonido, tomografía, endoscopia, biopsia, electrocardiograma…) desde Documentos. House lo lee en esta Mac, sin enviarlo a ninguna IA, y lo revisas junto al original.</p>
      <button class="btn" id="go">Subir un informe</button></section>`;
    document.getElementById('go').onclick = () => { S.tab = 'doc'; renderShell(); };
    return;
  }
  const kinds = ['Todos', ...new Set(list.map(x => x.modality || 'Otro'))];
  if (!kinds.includes(S.studyFilter)) S.studyFilter = 'Todos';
  const shown = list.filter(x => S.studyFilter === 'Todos' || (x.modality || 'Otro') === S.studyFilter);
  view().innerHTML = `<p class="tip">House guarda lo que dice cada informe; no interpreta imágenes ni sustituye al médico que lo firma. Las imágenes mismas (DICOM) llegan más adelante.</p>
    ${kinds.length > 2 ? `<div class="chips" role="group" aria-label="Filtrar por tipo">${kinds.map(k => `<button class="chip" data-sf="${esc(k)}" aria-pressed="${k === S.studyFilter}">${esc(k)}</button>`).join('')}</div>` : ''}
    ${shown.map(x => `<section class="card cfg"><div class="bar"><div><h2>${esc(x.study_name)}</h2>
        <span class="tip">${fd(x.performed_on)} · ${esc(x.modality || 'Estudio')}${x.site ? ' · ' + esc(x.site) : ''}</span></div>${flagPill(x.flag)}</div>
      ${x.conclusion ? `<p style="margin:0"><b>Conclusión:</b> ${esc(x.conclusion)}</p>` : '<p class="tip" style="margin:0">Este informe no trae una conclusión separada; lee el informe completo.</p>'}
      <details class="hist"><summary>Ver el informe completo</summary>
        <div class="kv" style="margin-top:10px">
          ${x.technique ? `<b>Técnica</b><span>${esc(x.technique)}</span>` : ''}${x.indication ? `<b>Indicación</b><span>${esc(x.indication)}</span>` : ''}
          <b>Hallazgos</b><span>${esc(x.findings || '—')}</span>${x.prior ? `<b>Estudio previo</b><span>${esc(x.prior)}</span>` : ''}
          ${x.suggestions ? `<b>Sugerencias</b><span>${esc(x.suggestions)}</span>` : ''}${x.radiologist ? `<b>Firmado por</b><span>${esc(x.radiologist)}</span>` : ''}</div>
        ${x.document_id ? `<p style="margin:10px 0 0"><a class="mini" href="/api/documents/${x.document_id}/file" target="_blank" rel="noopener" style="text-decoration:none">Ver informe original</a></p>` : ''}</details></section>`).join('')}`;
  view().querySelectorAll('[data-sf]').forEach(b => b.onclick = () => { S.studyFilter = b.dataset.sf; renderImaging(); });
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
  const reader = { claude: 'Claude (Anthropic)', basico: 'Lector básico en esta Mac, sin IA', configurado: 'Según house.toml' }[s.reader] || s.reader;
  view().innerHTML = `<section class="card cfg"><h2>Lectura de estudios</h2>
      <div class="kv"><b>Ahora lee</b><span>${esc(reader)}</span>
        <b>Gasto de este mes</b><span>${s.month_usd.toFixed(2)} USD de ${s.budget_usd} USD (${s.month_calls} lecturas)</span></div>
      ${s.reader === 'basico' ? `<p class="tip">El lector básico no envía nada fuera de tu Mac y funciona con los formatos que ya conoce (como Chopo). Para cualquier laboratorio, conecta Claude:</p>
        <ol class="tip" style="margin:0;padding-left:20px"><li>Entra a <b>console.anthropic.com</b> e inicia sesión (o crea tu cuenta y agrega un método de pago).</li>
        <li>En <b>API Keys</b>, crea una clave nueva y cópiala.</li><li>Pégala aquí. Se guarda cifrada en esta Mac.</li></ol>
        <form class="bar" id="kf"><input type="password" name="key" placeholder="sk-ant-…" style="flex:1" autocomplete="off"><button class="btn">Guardar clave</button></form>`
      : s.reader === 'claude' ? `<p class="tip">Antes de enviar un estudio, House le quita tu nombre, fecha de nacimiento y números de registro. Puedes ver exactamente lo que se envió al revisar cada estudio.</p>
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
