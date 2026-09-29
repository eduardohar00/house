'use strict';
/* House: interfaz local. Sin compilación ni dependencias; habla con la API en 127.0.0.1. */

const app = document.getElementById('app');
const S = { me: null, people: [], subject: null, catalog: {}, tab: 'res', sel: null, view: 'g' };

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ts = d => Date.parse(d + 'T00:00:00Z');
const fd = d => d ? new Date(ts(d)).toLocaleDateString('es-MX', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : 'sin fecha';
const fnum = v => (v == null ? '' : String(Number(Number(v).toFixed(3))));
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
  S.tab = 'res'; S.sel = null;
  renderShell();
}

function brandIcon() {
  return '<i><svg viewBox="0 0 24 24"><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/></svg></i>';
}

function renderShell() {
  const admin = S.me.is_admin, subj = S.people.find(p => p.id === S.subject);
  const tabs = [['res', 'Resumen'], ['doc', 'Documentos']].concat(admin ? [['fam', 'Familia'], ['cfg', 'Configuración']] : [['priv', 'Privacidad']]);
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
    <nav class="nav" role="tablist">${tabs.map(([k, n]) => `<button role="tab" data-t="${k}" aria-selected="${S.tab === k}">${n}</button>`).join('')}</nav>
    <div class="col" id="view"></div>
  </div>`;
  document.getElementById('out').onclick = logout;
  const ps = document.getElementById('psel');
  if (ps) ps.onchange = () => { S.subject = Number(ps.value); S.sel = null; renderShell(); };
  app.querySelectorAll('.nav button').forEach(b => b.onclick = () => { S.tab = b.dataset.t; renderShell(); });
  ({ res: renderSummary, doc: renderDocs, fam: renderFamily, cfg: renderConfig, priv: renderPrivacy })[S.tab]();
}

const view = () => document.getElementById('view');

/* ---------- Resumen ---------- */

const GROUPS = [
  ['glucosa', 'Metabolismo de la glucosa'], ['lipidos', 'Lípidos y riesgo cardiovascular'], ['higado', 'Hígado'],
  ['pancreas', 'Páncreas'], ['rinon', 'Riñón'], ['electrolitos', 'Electrolitos y minerales'], ['sangre', 'Sangre y hierro'],
  ['diferencial', 'Glóbulos blancos'], ['tiroides', 'Tiroides'], ['vitaminas', 'Vitaminas'], ['inmunologia', 'Inmunología'],
  ['marcadores', 'Marcadores'], ['orina', 'Orina'], ['seminal', 'Espermiograma'], ['infecciosas', 'Infecciones'], ['otros', 'Otros'],
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
  const obs = await api(`/api/people/${S.subject}/observations`);
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
  const numeric = Object.keys(series).filter(k => last(k).value_num != null);
  const latestDate = obs.map(o => o.collected_on).sort().pop();
  const nDocs = new Set(obs.map(o => o.document_id)).size;

  const att = Object.keys(series).filter(k => OUT(last(k).status))
    .sort((a, b) => last(b).collected_on.localeCompare(last(a).collected_on) || dist(last(b)) - dist(last(a)));
  const better = Object.keys(series).filter(k => series[k].length > 1 && last(k).status === 'ok' && OUT(series[k][series[k].length - 2].status));
  if (!S.sel || !series[S.sel] || last(S.sel).value_num == null) S.sel = att.find(k => numeric.includes(k)) || numeric[0];

  const ic = {
    at: '<svg width="10" height="10" viewBox="0 0 12 12" fill="currentColor"><path d="M6 1l5.5 10h-11z"/></svg>',
    me: '<svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6.5l2.5 2.5L10 3.5"/></svg>',
  };
  const trend = k => {
    const s = series[k]; if (s.length < 2 || last(k).value_num == null) return '';
    const f = s[0], l = last(k), df = dist(f), dl = dist(l);
    const t = dl < df * 0.8 ? 'Mejorando' : dl > df * 1.2 || df === 0 ? 'Empeorando' : 'Estable';
    return ` ${t}: ${fnum(f.value_num)} en ${f.collected_on.slice(0, 4)} a ${fnum(l.value_num)} en ${l.collected_on.slice(0, 4)}.`;
  };
  const row = (cls, label, k, sub) => {
    const l = last(k);
    const v = l.value_num != null ? `${fnum(l.value_num)} ${esc(l.unit)}` : esc(l.value_text);
    return `<button class="fr" data-k="${esc(k)}"><span class="tag ${cls}">${ic[cls]}${label}</span><span class="fm">${esc(name(k))} · ${v}</span><span class="fs">${sub}</span></button>`;
  };
  const rows = att.slice(0, 8).map(k => {
    const l = last(k);
    const how = l.value_num != null ? `${Math.round(dist(l) * 100)} % ${l.status === 'high' ? 'por encima' : 'por debajo'} del rango (${esc(refText(l))}).` : `Referencia: ${esc(refText(l))}.`;
    return row('at', 'Atención', k, `${fd(l.collected_on)}. ${how}${trend(k)}`);
  });
  if (att.length > 8) rows.push(`<div class="fq">Y ${att.length - 8} más fuera de rango; están marcados abajo, por sistema.</div>`);
  better.slice(0, 3).forEach(k => rows.push(row('me', 'Mejoró', k, `Ya está en rango (${esc(refText(last(k)))}).`)));

  const groups = GROUPS.map(([g, gname]) => {
    const keys = Object.keys(series).filter(k => (S.catalog[k]?.group || 'otros') === g);
    if (!keys.length) return '';
    const nums = keys.filter(k => numeric.includes(k)), texts = keys.filter(k => !numeric.includes(k));
    const withRef = keys.filter(k => last(k).status), ok = withRef.filter(k => last(k).status === 'ok').length;
    const cards = nums.map(k => {
      const s = series[k], l = last(k), vals = s.map(o => o.value_num).filter(v => v != null);
      const f = s[0], delta = s.length < 2 ? `${fd(l.collected_on)}` :
        (l.value_num === f.value_num ? 'sin cambio' : (l.value_num < f.value_num ? '▼ ' : '▲ ') + Math.round(Math.abs((l.value_num - f.value_num) / (f.value_num || 1)) * 100) + ' % desde ' + f.collected_on.slice(0, 4));
      return `<button class="bm" data-k="${esc(k)}" aria-pressed="${k === S.sel}">
        <span class="nm">${esc(name(k))}</span><div>${pill(l.status)}</div>
        <div class="row"><div><div class="val">${fnum(l.value_num)}<small>${esc(l.unit)}</small></div><div class="delta">${delta}</div></div>${vals.length > 1 ? spark(vals) : ''}</div></button>`;
    }).join('');
    const quals = texts.map(k => { const l = last(k); return `<span class="${OUT(l.status) ? 'out' : ''}">${esc(name(k))}: <b>${esc(l.value_text ?? fnum(l.value_num))}</b></span>`; }).join('');
    return `<section class="grp"><div class="gh"><h3>${gname}</h3>${withRef.length ? `<span class="gc ${ok === withRef.length ? 'ok' : 'out'}">${ok} de ${withRef.length} en rango</span>` : ''}</div>
      ${cards ? `<div class="cards">${cards}</div>` : ''}${quals ? `<div class="qual">${quals}</div>` : ''}</section>`;
  }).join('');

  view().innerHTML = `
    <section class="card insight"><h2>Resumen</h2>
      <p class="lead">${att.length ? `${att.length} ${att.length === 1 ? 'resultado fuera de rango' : 'resultados fuera de rango'}` : 'Todo dentro de rango'} en el último valor de cada análisis. Estudio más reciente: ${fd(latestDate)}; ${nDocs} ${nDocs === 1 ? 'estudio' : 'estudios'} en total.</p>
      <div class="fl">${rows.join('')}</div>
      <p class="tip">Para comentar con tu médico. Es informativo y no sustituye una valoración médica.</p></section>
    <section class="card detail" id="detail"></section>
    <div class="grid">${groups}</div>`;
  view().querySelectorAll('[data-k]').forEach(el => el.onclick = () => {
    if (series[el.dataset.k] && last(el.dataset.k).value_num != null) { S.sel = el.dataset.k; renderSummaryDetail(series); markSel(); document.getElementById('detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  });
  renderSummaryDetail(series);
}

function markSel() { view().querySelectorAll('.bm').forEach(b => b.setAttribute('aria-pressed', b.dataset.k === S.sel)); }

function normMethod(m) { return (m || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(); }

function renderSummaryDetail(series) {
  const box = document.getElementById('detail');
  if (!S.sel) { box.hidden = true; return; }
  const pts = series[S.sel].filter(o => o.value_num != null), l = pts[pts.length - 1];
  const methods = [...new Map(pts.filter(o => o.method).map(o => [normMethod(o.method), o.method])).values()];
  box.innerHTML = `<div class="dh"><div><h2>${esc(name(S.sel))}</h2>
      <p>${esc(l.unit)} · ${pts.length} ${pts.length === 1 ? 'resultado' : 'resultados'}, ${pts[0].collected_on.slice(0, 4)}${pts.length > 1 ? ' a ' + l.collected_on.slice(0, 4) : ''}</p></div>
      <div class="tabs"><button id="tg" aria-pressed="${S.view === 'g'}">Gráfica</button><button id="tt" aria-pressed="${S.view === 't'}">Tabla</button></div></div>
    ${methods.length > 1 ? `<div class="mixed">Ojo: estos resultados se midieron con métodos distintos (${methods.map(esc).join(', ')}). Compara la tendencia con cautela.</div>` : ''}
    <div class="chartbox" id="chart" ${S.view === 'g' ? '' : 'hidden'}></div>
    <div class="tblwrap" ${S.view === 't' ? '' : 'hidden'}><table><thead><tr><th>Fecha</th><th>Resultado</th><th>Estado</th><th>Referencia</th><th>Estudio</th><th>Método</th></tr></thead><tbody>
      ${[...pts].reverse().map(o => `<tr><td>${fd(o.collected_on)}</td><td><b>${fnum(o.value_num)}</b> ${esc(o.unit)}</td><td>${pill(o.status)}</td><td>${esc(refText(o))}</td><td>${esc(o.document_title)}</td><td>${esc(o.method || '—')}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="legend"><span><i class="lg-line"></i>Tus resultados</span>${l.ref_low != null || l.ref_high != null ? `<span><i class="lg-band"></i>Rango de referencia del último estudio: ${esc(refText(l))}</span>` : ''}</div>`;
  box.hidden = false;
  document.getElementById('tg').onclick = () => { S.view = 'g'; renderSummaryDetail(series); };
  document.getElementById('tt').onclick = () => { S.view = 't'; renderSummaryDetail(series); };
  if (S.view === 'g') drawChart(pts);
}

function niceStep(range, n) { const raw = range / n, m = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / m; return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * m; }

function drawChart(pts) {
  const box = document.getElementById('chart');
  const W = Math.max(box.clientWidth, 280), H = W < 520 ? 250 : 320;
  const m = { l: W < 520 ? 40 : 48, r: W < 520 ? 52 : 64, t: 14, b: 42 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const l = pts[pts.length - 1], vals = pts.map(o => o.value_num);
  // Pocos resultados (lo normal): cada uno en su lugar, con espacio parejo y su fecha debajo; así
  // dos estudios del mismo mes o año no quedan encimados. Muchos: escala de tiempo con años.
  const ordinal = pts.length <= 12;
  let t0 = ts(pts[0].collected_on), t1 = ts(l.collected_on);
  const padT = Math.max((t1 - t0) * 0.04, 60 * 864e5); t0 -= padT; t1 += padT;
  const lims = [l.ref_low, l.ref_high].filter(v => v != null);
  let mn = Math.min(...vals, ...lims), mx = Math.max(...vals, ...lims);
  const pad = (mx - mn) * 0.18 || Math.abs(mx) * 0.2 || 1; mn -= pad; mx += pad;
  const step = niceStep(mx - mn, 4);
  mn = Math.floor(mn / step) * step; mx = Math.ceil(mx / step) * step; if (mn < 0 && Math.min(...vals) >= 0) mn = 0;
  const Y = v => m.t + (1 - (v - mn) / (mx - mn)) * ih;
  const X = ordinal ? i => m.l + iw * (i + 0.5) / pts.length : i => m.l + (ts(pts[i].collected_on) - t0) / (t1 - t0) * iw;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Evolución de ${esc(name(S.sel))}">`;
  if (lims.length) {
    const top = Y(l.ref_high ?? mx), bot = Y(l.ref_low ?? mn);
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
    tip.innerHTML = `<span>${fd(o.collected_on)}</span><b>${fnum(o.value_num)} ${esc(o.unit)}</b><span>${o.status ? STL[o.status] : 'Sin referencia'}</span><span>${esc(o.document_title)}</span>${o.method ? `<span>Método: ${esc(o.method)}</span>` : ''}`;
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
        ${docs.map(d => `<tr><td><b>${esc(d.title)}</b></td><td>${fd(d.collected_on)}</td>
          <td>${d.review_state === 'revisada' ? `Revisado · ${d.results} resultados` : esc(STATE[d.review_state])}</td>
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
      toast(r.reader === 'basico' ? `Leído con el lector básico: ${r.rows} renglones.` : `Leído: ${r.rows} renglones.`);
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
  const rows = d.rows.map(r => ({ ...r, accept: !!r.analyte_key && !r.problems.includes('no_respaldada_por_el_documento') && !r.problems.includes('unidad_no_reconocida') && !r.problems.includes('valor_no_numerico') && !r.problems.includes('mismo_valor_en_otra_unidad') }));
  let onlyFlags = false, showAi = false, active = null;
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
      <p class="tip">Toca un resultado para ver su renglón resaltado en el original. Corrige lo necesario y desmarca lo que no quieras guardar. Solo lo que confirmes entra a tu expediente.</p>
      <div class="bar"><label class="kv"><b>Fecha de toma</b><input type="date" id="date" value="${esc(d.document.collected_on || '')}"></label>
        <label><input type="checkbox" id="only" ${onlyFlags ? 'checked' : ''}> Solo lo que requiere atención</label>
        <button class="mini" id="ai">${showAi ? 'Ocultar' : 'Ver'} lo que vio la IA</button></div>
      ${showAi ? `<div class="anon">${esc(d.ai_saw)}</div><p class="tip">Datos personales quitados antes de enviar: ${Object.entries(d.redactions).map(([k, v]) => `${esc(k.toLowerCase())} (${v})`).join(', ') || 'ninguno'}.</p>` : ''}
      <div class="tblwrap"><table class="rt"><thead><tr><th></th><th>Análisis</th><th>Resultado</th><th>Referencia</th><th>Estado</th></tr></thead><tbody>
        ${shown.map(r => `<tr data-row="${r.id}" class="${r.accept ? (r.needs_attention ? 'fl2' : '') : 'skip'} ${r.id === active ? 'active' : ''}">
          <td><input type="checkbox" data-acc="${r.id}" ${r.accept ? 'checked' : ''} ${r.analyte_key ? '' : 'disabled'} aria-label="Guardar"></td>
          <td><b>${esc(r.name || r.printed_name)}</b><small>${esc(r.printed_name)}${r.section ? ' · ' + esc(r.section) : ''}${layout.boxes[r.id] ? ` · pág. ${layout.boxes[r.id].page}` : ''}</small>
            ${r.analyte_key ? '' : '<span class="flag">No reconocido: no se guardará</span>'}
            ${r.problems.filter(p => p !== 'analito_desconocido').map(p => `<span class="flag">${esc(PROBLEMS[p] || p)}</span>`).join('')}
            ${r.converted ? `<span class="flag">Convertido de ${esc(r.value_printed)} ${esc(r.unit_printed || '')}</span>` : ''}</td>
          <td><input type="text" data-val="${r.id}" value="${esc(r.edited ?? (r.value_num != null ? fnum(r.value_num) : r.value_text ?? r.value_printed))}"> ${esc(r.unit || '')}</td>
          <td>${esc(r.ref_printed || '—')}</td><td>${pill(r.status)}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="err" id="e"></p>
      <div class="bar"><button class="btn danger" id="discard">Descartar estudio</button><button class="btn" id="ok">Confirmar ${n} ${n === 1 ? 'resultado' : 'resultados'}</button></div>`;
    document.getElementById('back').onclick = () => renderDocs();
    document.getElementById('reread').onclick = async () => {
      if (!confirm('¿Volver a leer este estudio? Se pierden las correcciones que no hayas confirmado.')) return;
      const b = document.getElementById('reread'); b.disabled = true; b.textContent = 'Leyendo…';
      try { const r = await api(`/api/documents/${id}/reread`, { method: 'POST' }); toast(`Leído de nuevo: ${r.rows} renglones.`); openReview(id); }
      catch (err) { document.getElementById('e').textContent = err.message; b.disabled = false; b.textContent = 'Volver a leer'; }
    };
    document.getElementById('only').onchange = e => { onlyFlags = e.target.checked; draw(); };
    document.getElementById('ai').onclick = () => { showAi = !showAi; draw(); };
    view().querySelectorAll('tr[data-row]').forEach(tr => {
      tr.onclick = e => { if (e.target.type !== 'checkbox') focusRow(Number(tr.dataset.row)); };
      tr.querySelector('[data-val]').onfocus = () => focusRow(Number(tr.dataset.row));
    });
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
        const res = await api(`/api/documents/${id}/review`, { method: 'POST', body: { collected_on: date, decisions } });
        toast(`Guardé ${res.saved} resultados en tu expediente.`); S.tab = 'res'; renderShell();
      } catch (err) { e.textContent = err.message; }
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
    <section class="card cfg"><h2>Quién ha visto qué</h2>${await accessLogHtml()}</section>
    <section class="card cfg"><h2>Dónde están tus datos</h2><p class="tip">En esta Mac: <b>${esc(s.data_dir)}</b>. El disco está cifrado con FileVault y los PDF, además, con una llave guardada en el llavero de macOS.</p></section>`;
  const kf = document.getElementById('kf');
  if (kf) kf.onsubmit = async ev => {
    ev.preventDefault();
    try { await api('/api/settings/anthropic-key', { method: 'PUT', body: { key: kf.key.value } }); toast('Listo: House leerá tus estudios con Claude.'); renderConfig(); }
    catch (e) { document.getElementById('e').textContent = e.message; }
  };
  const rm = document.getElementById('rm');
  if (rm) rm.onclick = async () => { if (!confirm('¿Quitar la clave de Claude?')) return; await api('/api/settings/anthropic-key', { method: 'DELETE' }); renderConfig(); };
}

async function renderPrivacy() {
  view().innerHTML = `<section class="card cfg"><h2>Quién ha visto tu perfil</h2>
    <p class="tip">El administrador puede ver tu perfil; cada vez que lo hace queda registrado aquí.</p>${await accessLogHtml()}</section>`;
}

boot().catch(e => { if (e.status !== 401) app.innerHTML = `<div class="screen"><div class="panel"><h1>No pude arrancar</h1><p>${esc(e.message)}</p></div></div>`; });
