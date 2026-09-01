'use strict';

// ---------------------------------------------------------------- state

const state = {
  hosts: [],
  activeId: null,
  status: {},          // host id -> last status payload
  backend: null,
  tab: 'dashboard',
  timer: null,
  term: null,
  fit: null,
  ws: null,
};

const $ = (sel) => document.querySelector(sel);
const REFRESH_MS = 10000;

// ---------------------------------------------------------------- utils

function fmtBytes(n) {
  if (n === null || n === undefined) return '-';
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(2) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}

function taskResultText(code) {
  if (code === null || code === undefined) return ['-', ''];
  switch (code) {
    case 0:      return ['0  정상', 'ok'];
    case 1:      return ['1  치명적 실패', 'bad'];
    case 2:      return ['2  전송 실패 (pending)', 'warn'];
    case 267009: return ['실행 중', 'warn'];
    case 267011: return ['아직 실행된 적 없음', ''];
    default:     return [String(code), 'warn'];
  }
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function field(label, value, tone) {
  const row = el('div', 'field');
  row.append(el('span', 'field-label', label));
  row.append(el('span', 'field-value' + (tone ? ' ' + tone : ''), value ?? '-'));
  return row;
}

function card(title, rows) {
  const c = el('section', 'card');
  c.append(el('h2', null, title));
  rows.filter(Boolean).forEach((r) => c.append(r));
  return c;
}

// ---------------------------------------------------------------- api

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

const postJson = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

// ---------------------------------------------------------------- hosts

function renderHostList() {
  const list = $('#host-list');
  list.textContent = '';

  for (const host of state.hosts) {
    const item = el('li', 'host-item' + (host.id === state.activeId ? ' is-active' : ''));
    const status = state.status[host.id];
    let dotCls = 'host-dot';
    if (status) dotCls += status.reachable ? ' ok' : ' bad';

    const text = el('div', 'host-text');
    text.append(el('div', 'host-name', host.label));
    text.append(el('div', 'host-addr', `${host.username}@${host.address}`));

    item.append(el('span', dotCls));
    item.append(text);
    item.addEventListener('click', () => selectHost(host.id));
    list.append(item);
  }
}

function activeHost() {
  return state.hosts.find((h) => h.id === state.activeId) || null;
}

async function selectHost(id) {
  if (state.activeId === id) return;
  disconnectTerminal();
  state.activeId = id;
  renderHostList();
  renderDashboard();
  await refresh();
  if (state.tab === 'log') loadFullLog();
}

// ------------------------------------------------------------ dashboard

function renderDashboard() {
  const host = activeHost();
  const dash = $('#dash');
  const empty = $('#dash-empty');

  if (!host) {
    dash.hidden = true;
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  dash.hidden = false;

  const status = state.status[host.id];
  const cards = $('#cards');
  cards.textContent = '';

  if (!status) {
    cards.append(card('상태', [field('', '읽는 중...')]));
    return;
  }

  if (!status.reachable) {
    cards.append(card('연결', [
      field('상태', '연결 실패', 'bad'),
      field('원인', status.error || '알 수 없음', 'bad'),
    ]));
    $('#dash-log').textContent = '';
    return;
  }

  // 작업 스케줄러 — 양쪽 공통
  const t = status.task;
  if (t) {
    const [resultText, resultTone] = taskResultText(t.last_result);
    cards.append(card('작업 스케줄러', t.registered ? [
      field('상태', t.enabled ? '사용' : '일시 중지됨', t.enabled ? 'ok' : 'warn'),
      field('마지막 실행', t.last_run),
      field('마지막 결과', resultText, resultTone),
      field('다음 실행', t.next_run),
      field('놓친 실행', String(t.missed ?? '-'), t.missed ? 'warn' : ''),
    ] : [field('상태', '미등록', 'bad'), field('작업 이름', host.task)]));
  }

  const s = status.sender;
  if (s) {
    cards.append(card('백업 대상', [
      field('경로', s.base_dir),
      field('프로젝트', `${s.projects} 개`),
      field('압축 수준', `7z -mx=${s.level}`),
      s.dry_run ? field('DRY_RUN', '켜짐 — 전송 안 함', 'warn') : null,
    ]));
    cards.append(card('전송', [
      field('대상 순서', (s.targets || []).join('\n')),
      field('작업 볼륨 여유', fmtBytes(s.free_bytes)),
      field('pending', s.pending_count
        ? `${s.pending_count} 개  ${fmtBytes(s.pending_bytes)}`
        : '없음', s.pending_count ? 'warn' : 'ok'),
    ]));
  }

  const r = status.receiver;
  if (r) {
    cards.append(card('수신', [
      field('감시 폴더', r.watch_dir),
      field('대기 중인 압축', r.waiting_count
        ? `${r.waiting_count} 개  ${fmtBytes(r.waiting_bytes)}`
        : '없음'),
      field('7-Zip', r.seven_zip ? '있음' : '없음!', r.seven_zip ? 'ok' : 'bad'),
      field('볼륨 여유', fmtBytes(r.free_bytes)),
    ]));
    cards.append(card('관리 저장소', [
      field('경로', r.repo_dir),
      field('스냅샷', `${r.snapshots} 개`),
      field('최근 스냅샷', r.last_snapshot),
      field('.git 크기', r.git_size),
      field('초기화 횟수', String(r.reset_count ?? 0)),
      field('초기화까지', r.reset_due_days != null
        ? `${r.reset_due_days} 일  (${r.reset_due_date || '-'})`
        : '-'),
      field('보관 세대', `${r.generations ?? 0} 개`),
    ]));
  }

  $('#dash-log').textContent = (status.log || []).join('\n') || '(로그 없음)';
}

async function refresh() {
  const host = activeHost();
  if (!host) return;
  try {
    const status = await api(`/api/hosts/${host.id}/status`);
    state.status[host.id] = status;
  } catch (err) {
    state.status[host.id] = { reachable: false, error: String(err), log: [] };
  }
  renderHostList();
  renderDashboard();
}

// --------------------------------------------------------------- actions

async function doAction(kind) {
  const host = activeHost();
  if (!host) return;
  const status = state.status[host.id];
  let action = kind;
  if (kind === 'toggle') {
    action = status?.task?.enabled ? 'disable' : 'enable';
  }
  const out = $('#action-out');
  out.textContent = '요청 중...';
  try {
    const res = await postJson(`/api/hosts/${host.id}/action`, { action });
    out.textContent = (res.output || []).join(' ').slice(0, 300) || (res.ok ? '완료' : '실패');
  } catch (err) {
    out.textContent = String(err);
  }
  setTimeout(refresh, 800);
}

// ------------------------------------------------------------- full log

async function loadFullLog() {
  const host = activeHost();
  const pre = $('#full-log');
  if (!host) { pre.textContent = '호스트를 고르십시오.'; return; }
  pre.textContent = '읽는 중...';
  const lines = $('#log-lines').value;
  try {
    const res = await api(`/api/hosts/${host.id}/log?lines=${lines}`);
    pre.textContent = res.error
      ? res.error
      : (res.lines || []).join('\n') || '(로그 없음)';
  } catch (err) {
    pre.textContent = String(err);
  }
}

// -------------------------------------------------------------- terminal

function ensureTerm() {
  if (state.term) return true;
  if (typeof Terminal === 'undefined' || typeof FitAddon === 'undefined') {
    // Belt and braces: the files are served from /static/vendor, so this only
    // fires if they were deleted. Without the guard the failure is an
    // unhelpful "Terminal is not defined" in the console and a blank tab.
    $('#terminal').textContent =
      'xterm.js 를 불러오지 못했습니다. app/static/vendor/ 안에 xterm.js 와 '
      + 'xterm-addon-fit.js 가 있는지 확인하십시오.';
    termState('불러오기 실패', 'badge-bad');
    return false;
  }
  const term = new Terminal({
    fontFamily: 'ui-monospace, "Cascadia Mono", "D2Coding", Consolas, monospace',
    fontSize: 13,
    cursorBlink: true,
    convertEol: false,
    theme: { background: '#000000', foreground: '#d7dce5', cursor: '#4c8dff' },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($('#terminal'));
  fit.fit();

  // Frame type is the protocol: binary = terminal bytes, text = JSON control.
  // See the note in main.py — a terminal emits arbitrary bytes, so anything
  // that inspects the payload to tell the two apart is a bug waiting for the
  // command that prints a brace.
  const encoder = new TextEncoder();

  term.onData((data) => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(encoder.encode(data));
    }
  });

  term.onResize(({ cols, rows }) => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'resize', cols, rows }));
    }
  });

  state.term = term;
  state.fit = fit;
  new ResizeObserver(() => { try { fit.fit(); } catch (e) { /* hidden pane */ } })
    .observe($('#terminal'));
  return true;
}

function termState(text, cls) {
  const badge = $('#term-state');
  badge.textContent = text;
  badge.className = 'badge ' + (cls || 'badge-muted');
}

function connectTerminal() {
  const host = activeHost();
  if (!host) { alert('호스트를 먼저 고르십시오.'); return; }
  if (state.ws) return;

  if (!ensureTerm()) return;
  state.term.reset();
  termState('연결 중...', 'badge-muted');

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/hosts/${host.id}/terminal`);
  ws.binaryType = 'arraybuffer';
  state.ws = ws;

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      state.term.write(new Uint8Array(ev.data));   // xterm decodes UTF-8 itself
      return;
    }
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'ready') {
      const mock = msg.backend === 'mock';
      termState(mock ? '연결됨 (MOCK)' : '연결됨', mock ? 'badge-mock' : 'badge-ssh');
      $('#term-connect').disabled = true;
      $('#term-disconnect').disabled = false;
      try { state.fit.fit(); } catch (e) { /* pane not laid out yet */ }
      state.term.focus();   // otherwise the first thing typed goes nowhere
    } else if (msg.type === 'error') {
      state.term.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`);
      termState('오류', 'badge-bad');
    } else if (msg.type === 'closed') {
      state.term.write('\r\n\x1b[90m-- 세션이 종료되었습니다 --\x1b[0m\r\n');
    }
  };

  ws.onclose = () => {
    state.ws = null;
    termState('연결 안 됨', 'badge-muted');
    $('#term-connect').disabled = false;
    $('#term-disconnect').disabled = true;
  };

  ws.onerror = () => termState('연결 실패', 'badge-bad');
}

function disconnectTerminal() {
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
}

// ------------------------------------------------------------------ tabs

function selectTab(name) {
  state.tab = name;
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('is-active', t.dataset.tab === name));
  document.querySelectorAll('.pane').forEach((p) =>
    p.classList.toggle('is-active', p.dataset.pane === name));

  if (name === 'terminal' && state.fit) {
    setTimeout(() => { try { state.fit.fit(); } catch (e) { /* ignore */ } }, 0);
  }
  if (name === 'log') loadFullLog();
}

// ------------------------------------------------------------------ init

function startTimer() {
  clearInterval(state.timer);
  if ($('#auto-refresh').checked) {
    state.timer = setInterval(refresh, REFRESH_MS);
  }
}

async function init() {
  document.querySelectorAll('.tab').forEach((t) =>
    t.addEventListener('click', () => selectTab(t.dataset.tab)));

  $('#refresh').addEventListener('click', refresh);
  $('#auto-refresh').addEventListener('change', startTimer);
  $('#log-reload').addEventListener('click', loadFullLog);
  $('#log-lines').addEventListener('change', loadFullLog);
  $('#term-connect').addEventListener('click', connectTerminal);
  $('#term-disconnect').addEventListener('click', disconnectTerminal);
  document.querySelectorAll('[data-action]').forEach((b) =>
    b.addEventListener('click', () => doAction(b.dataset.action)));

  try {
    const meta = await api('/api/meta');
    state.backend = meta.backend;
    const badge = $('#backend-badge');
    badge.textContent = meta.backend === 'mock' ? 'MOCK 데이터' : 'SSH 연결';
    badge.className = 'badge ' + (meta.backend === 'mock' ? 'badge-mock' : 'badge-ssh');
    $('#hosts-file').textContent = meta.hosts_file;

    if (meta.using_example) {
      const notice = $('#notice');
      notice.hidden = false;
      notice.textContent =
        'hosts.json 이 없어 hosts.example.json 을 쓰고 있습니다. ' +
        '실제 기기에 붙이려면 webadmin/hosts.json 을 만들어 주소와 계정을 채우십시오.';
    }
  } catch (err) {
    console.error(err);
  }

  state.hosts = await api('/api/hosts');
  renderHostList();
  if (state.hosts.length) await selectHost(state.hosts[0].id);
  startTimer();
}

init();
