'use strict';

// ---------------------------------------------------------------- state

const state = {
  devices: [],
  selected: null,      // the device object clicked in the sidebar
  overview: null,      // { sender, receiver }
  meta: null,
  tab: 'overview',
  timer: null,
  term: null,
  fit: null,
  ws: null,
};

const REFRESH_MS = 10000;
const $ = (s) => document.querySelector(s);

const PATHS = [
  { id: 'direct', label: 'Tailscale IP + OpenSSH',
    note: '100.x 주소(또는 MagicDNS 이름) 22번 포트 직결. 사용자 이름·비밀번호 사용. 기본값이며 실기에서 쓰는 경로입니다.' },
  { id: 'tsssh', label: 'Tailscale SSH',
    note: 'tailscaled 가 tailnet 신원으로 인가하므로 비밀번호를 쓰지 않습니다. 대상 기기에서 tailscale up --ssh 가 켜져 있어야 합니다. 아직 실기 검증 전입니다.' },
  { id: 'jump', label: '점프 호스트 경유 (릴레이)',
    note: 'tailnet 이 닿지 않을 때 접속 가능한 다른 SSH 서버를 거칩니다. 표준 ProxyJump 라 제3자 서비스를 지나지 않고, 자격 증명은 본인 소유 기기 사이에만 머뭅니다.' },
];

const QUICK = [
  'tailscale status',
  'type C:\\TempBackup\\backup.log',
  'schtasks /query /tn "TailscaleProjectBackup" /v /fo list',
  'dir C:\\TempBackup',
];

// ---------------------------------------------------------------- utils

function fmtBytes(n) {
  if (n === null || n === undefined) return '-';
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(2) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}

function taskResult(code) {
  if (code === null || code === undefined) return ['-', 'v-mute'];
  switch (code) {
    case 0:      return ['0  정상', 'v-ok'];
    case 1:      return ['1  치명적 실패', 'v-bad'];
    case 2:      return ['2  전송 실패 (pending)', 'v-warn'];
    case 267009: return ['실행 중', 'v-warn'];
    case 267011: return ['아직 실행된 적 없음', 'v-mute'];
    default:     return [String(code), 'v-warn'];
  }
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

const postJson = (path, body) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

// -------------------------------------------------------------- sidebar

function renderDevices() {
  const list = $('#device-list');
  list.textContent = '';

  for (const d of state.devices) {
    const li = el('li', 'device' + (state.selected && state.selected.host === d.host ? ' is-active' : ''));

    const head = el('div', 'device-head');
    head.append(el('span', 'device-host', d.host));
    const dot = el('span', 'device-dot' + (d.online === true ? ' on' : d.online === null ? ' unknown' : ''));
    dot.title = d.online === true ? '온라인' : d.online === null ? '상태 미상 (tailnet 조회 불가)' : '오프라인';
    head.append(dot);
    li.append(head);

    const meta = el('div', 'device-meta');
    if (d.ip) meta.append(el('span', null, d.ip));
    if (d.os) meta.append(el('span', null, d.os));
    if (!d.ip && !d.os) meta.append(el('span', null, '주소 미확인'));
    li.append(meta);

    if (d.tags && d.tags.length) {
      const tags = el('div', 'device-tags');
      d.tags.forEach((t) => tags.append(el('span', 'tag ' + t.kind, t.label)));
      li.append(tags);
    }

    li.addEventListener('click', () => selectDevice(d));
    list.append(li);
  }

  if (!state.devices.length) {
    list.append(el('li', 'empty', '기기가 없습니다.'));
  }
}

function selectDevice(d) {
  if (state.selected && state.selected.host === d.host) return;
  disconnectTerminal();
  state.selected = d;
  renderDevices();
  renderSelection();
  fillForm(d);
  renderActions();
}

function renderSelection() {
  const d = state.selected;
  $('#sel-host').textContent = d ? d.host : '-';
  $('#sel-ip').textContent = d && d.ip ? d.ip : '-';
  $('#term-target').textContent = d && d.id
    ? `${d.username || '?'}@${d.host}${d.ip ? ' · ' + d.ip : ''}`
    : (d ? `${d.host} (미등록)` : '-');
  $('#term-ws').textContent = d && d.id ? `WebSocket /api/hosts/${d.id}/terminal` : 'WebSocket -';
  const path = PATHS.find((p) => p.id === (d ? d.path : 'direct'));
  $('#term-path').textContent = path ? path.label : '-';
}

// ------------------------------------------------------------- overview

function fields(rows) {
  const dl = el('dl', 'fields');
  rows.filter(Boolean).forEach(([label, value, cls]) => {
    dl.append(el('dt', null, label));
    dl.append(el('dd', cls || null, value === null || value === undefined || value === '' ? '-' : value));
  });
  return dl;
}

function sidePanel(title, status) {
  const panel = el('section', 'panel');
  const head = el('div', 'panel-head');
  head.append(el('span', 'panel-title', title));
  if (status && status.host) head.append(el('span', 'panel-note', status.host.address));
  panel.append(head);

  const body = el('div', 'panel-body');
  if (!status) {
    body.append(el('div', 'note', '이 역할로 등록된 호스트가 없습니다. 연결 설정에서 역할을 지정하십시오.'));
    panel.append(body);
    return panel;
  }
  if (!status.reachable) {
    body.append(fields([['상태', '연결 실패', 'v-bad'], ['원인', status.error || '알 수 없음', 'v-bad']]));
    panel.append(body);
    return panel;
  }

  const rows = [];
  const s = status.sender;
  const r = status.receiver;

  if (s) {
    rows.push(
      ['대상', s.base_dir],
      ['프로젝트', s.projects != null ? `${s.projects} 개` : null],
      ['압축 수준', s.level ? `7z -mx=${s.level}` : null],
      ['전송 대상', (s.targets || []).join('\n')],
      ['작업 볼륨 여유', fmtBytes(s.free_bytes)],
      ['pending', s.pending_count ? `${s.pending_count} 개  ${fmtBytes(s.pending_bytes)}` : '없음',
        s.pending_count ? 'v-warn' : 'v-ok'],
      s.dry_run ? ['DRY_RUN', '켜짐 — 전송 안 함', 'v-warn'] : null,
    );
  }
  if (r) {
    rows.push(
      ['감시 폴더', r.watch_dir],
      ['관리 저장소', r.repo_dir],
      ['대기 중인 압축', r.waiting_count ? `${r.waiting_count} 개  ${fmtBytes(r.waiting_bytes)}` : '없음'],
      ['스냅샷', `${r.snapshots ?? 0} 개`],
      ['최근 스냅샷', r.last_snapshot],
      ['.git 크기', r.git_size],
      ['초기화까지', r.reset_due_days != null ? `${r.reset_due_days} 일  (${r.reset_due_date || '-'})` : null],
      ['7-Zip', r.seven_zip ? '있음' : '없음!', r.seven_zip ? null : 'v-bad'],
      ['보관 세대', `${r.generations ?? 0} 개`],
      ['볼륨 여유', fmtBytes(r.free_bytes)],
    );
  }

  const t = status.task;
  if (t && t.registered) {
    const [text, cls] = taskResult(t.last_result);
    rows.push(
      ['작업 스케줄러', t.enabled ? '사용' : '일시 중지됨', t.enabled ? null : 'v-warn'],
      ['마지막 실행', t.last_run],
      ['마지막 결과', text, cls],
      ['다음 실행', t.next_run],
      ['놓친 실행', String(t.missed ?? 0), t.missed ? 'v-warn' : null],
    );
  } else if (t) {
    rows.push(['작업 스케줄러', '미등록', 'v-bad']);
  }

  if (status.error) rows.push(['경고', status.error, 'v-warn']);

  body.append(fields(rows));
  panel.append(body);
  return panel;
}

function renderOverview() {
  const wrap = $('#overview-panels');
  wrap.textContent = '';
  const ov = state.overview;
  if (!ov) {
    wrap.append(el('div', 'empty', '읽는 중...'));
    return;
  }
  wrap.append(sidePanel('보내는 쪽 — ts_backup.bat', ov.sender));
  wrap.append(sidePanel('받는 쪽 — ts_receive.ps1', ov.receiver));

  // The log shown is the one belonging to the selected side, defaulting to
  // the sender - the side a run starts on.
  const side = (state.selected && state.selected.role === 'receiver') ? 'receiver' : 'sender';
  const st = ov[side];
  const name = side === 'sender' ? 'backup.log' : 'receive.log';
  const dir = st && st.host ? st.host.work_dir : '';
  $('#log-title').textContent = `최근 로그 — ${dir ? dir + '\\' : ''}${name}`;
  $('#log-note').textContent = st && st.host
    ? `GET /api/hosts/${st.host.id}/log?lines=40` : '';
  $('#overview-log').textContent = st && st.log && st.log.length
    ? st.log.join('\n') : '(로그 없음)';
}

function renderActions() {
  const target = actionTarget();
  const enabled = target && target.status && target.status.task && target.status.task.enabled;
  $('[data-action="toggle"]').textContent = enabled ? '일시 중지' : '재개';
  const has = Boolean(target);
  document.querySelectorAll('[data-action]').forEach((b) => { b.disabled = !has; });
}

function actionTarget() {
  // Act on the selected host when it has a role; otherwise on the sender,
  // which is what the overview leads with.
  const ov = state.overview;
  if (!ov) return null;
  const d = state.selected;
  if (d && d.id) {
    for (const side of ['sender', 'receiver']) {
      if (ov[side] && ov[side].host && ov[side].host.id === d.id) {
        return { id: d.id, status: ov[side] };
      }
    }
  }
  if (ov.sender && ov.sender.host) return { id: ov.sender.host.id, status: ov.sender };
  if (ov.receiver && ov.receiver.host) return { id: ov.receiver.host.id, status: ov.receiver };
  return null;
}

async function doAction(kind) {
  const target = actionTarget();
  if (!target) return;
  let action = kind;
  if (kind === 'toggle') {
    action = target.status.task && target.status.task.enabled ? 'disable' : 'enable';
  }
  const note = $('#action-note');
  note.className = 'note';
  note.textContent = `POST /api/hosts/${target.id}/action — 요청 중`;
  try {
    const res = await postJson(`/api/hosts/${target.id}/action`, { action });
    note.className = 'note ' + (res.ok ? 'ok' : 'bad');
    note.textContent = (res.output || []).join(' ').slice(0, 200) || (res.ok ? '완료' : '실패');
  } catch (err) {
    note.className = 'note bad';
    note.textContent = String(err);
  }
  setTimeout(refresh, 900);
}

// --------------------------------------------------------------- refresh

async function refresh() {
  try {
    const d = await api('/api/devices');
    state.devices = d.devices || [];
    const tn = $('#tailnet');
    tn.className = 'tailnet ' + (d.tailnet_ok ? 'ok' : 'bad');
    $('#tailnet-text').textContent = d.tailnet_ok
      ? `tailnet 연결됨 · 기기 ${d.online}/${d.total}`
      : 'tailnet 조회 불가 · 등록된 호스트만';
    if (!d.tailnet_ok && d.error) tn.title = d.error;

    if (state.selected) {
      const again = state.devices.find((x) => x.host === state.selected.host);
      state.selected = again || state.selected;
    } else if (state.devices.length) {
      state.selected = state.devices[0];
      fillForm(state.selected);
    }
    renderDevices();
    renderSelection();
  } catch (err) {
    console.error(err);
  }

  try {
    state.overview = await api('/api/overview');
  } catch (err) {
    console.error(err);
  }
  renderOverview();
  renderActions();
}

// -------------------------------------------------------------- terminal

function ensureTerm() {
  if (state.term) return true;
  if (typeof Terminal === 'undefined' || typeof FitAddon === 'undefined') {
    // Belt and braces: the files are served from /static/vendor, so this only
    // fires if they were deleted. Without the guard the failure is an
    // unhelpful "Terminal is not defined" in the console and a blank tab.
    $('#terminal').textContent =
      'xterm.js 를 불러오지 못했습니다. app/static/vendor/ 를 확인하십시오.';
    termState('불러오기 실패', 'bad');
    return false;
  }

  const term = new Terminal({
    fontFamily: '"JetBrains Mono", "D2Coding", "Cascadia Mono", Consolas, monospace',
    fontSize: 13,
    cursorBlink: true,
    theme: { background: '#000000', foreground: '#cccccc', cursor: '#61d6d6',
             selectionBackground: '#264f78' },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($('#terminal'));
  fit.fit();

  // Frame type is the protocol: binary = terminal bytes, text = JSON control.
  // See the note in main.py - a terminal emits arbitrary bytes, so anything
  // that inspects the payload to tell the two apart is a bug waiting for the
  // command that prints a brace.
  const encoder = new TextEncoder();

  term.onData((data) => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(encoder.encode(data));
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

function termState(text, kind) {
  $('#term-state').className = 'term-state' + (kind ? ' ' + kind : '');
  $('#term-state-text').textContent = text;
}

function connectTerminal() {
  const d = state.selected;
  if (!d) return;
  if (!d.id) {
    selectTab('settings');
    $('#save-note').className = 'note bad';
    $('#save-note').textContent = '이 기기는 아직 등록되지 않았습니다. 먼저 저장하십시오.';
    return;
  }
  if (state.ws) return;
  if (!ensureTerm()) return;

  state.term.reset();
  termState('연결 중...', '');

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/hosts/${d.id}/terminal`);
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
      termState(msg.backend === 'mock' ? '세션 연결됨 (MOCK)' : '세션 연결됨', 'on');
      $('#term-connect').disabled = true;
      $('#term-disconnect').disabled = false;
      try { state.fit.fit(); } catch (e) { /* pane not laid out yet */ }
      state.term.focus();   // otherwise the first thing typed goes nowhere
    } else if (msg.type === 'error') {
      state.term.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`);
      termState('연결 실패', 'bad');
    } else if (msg.type === 'closed') {
      state.term.write('\r\n\x1b[90m-- 세션이 종료되었습니다 --\x1b[0m\r\n');
    }
  };

  ws.onclose = () => {
    state.ws = null;
    termState('연결 안 됨', '');
    $('#term-connect').disabled = false;
    $('#term-disconnect').disabled = true;
  };
  ws.onerror = () => termState('연결 실패', 'bad');
}

function disconnectTerminal() {
  if (state.ws) { state.ws.close(); state.ws = null; }
}

function sendCommand(cmd) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    connectTerminal();
    return;
  }
  state.ws.send(new TextEncoder().encode(cmd + '\r'));
  state.term.focus();
}

function renderQuick() {
  const wrap = $('#quick');
  wrap.textContent = '';
  QUICK.forEach((cmd) => {
    const b = el('button', 'btn btn-sm', cmd);
    b.type = 'button';
    b.addEventListener('click', () => sendCommand(cmd));
    wrap.append(b);
  });
}

// -------------------------------------------------------------- settings

function renderPaths(selected) {
  const wrap = $('#paths');
  wrap.textContent = '';
  PATHS.forEach((p) => {
    const row = el('div', 'path' + (p.id === selected ? ' is-on' : ''));
    row.dataset.path = p.id;
    row.append(el('span', 'mark'));
    const text = el('span', 'path-text');
    text.append(el('span', 'path-label', p.label));
    text.append(el('span', 'path-note', p.note));
    row.append(text);
    row.addEventListener('click', () => {
      renderPaths(p.id);
      $('#jump-fields').hidden = p.id !== 'jump';
      $('#f-pass').disabled = p.id === 'tsssh';
      $('#f-pass').placeholder = p.id === 'tsssh'
        ? 'Tailscale SSH 는 비밀번호를 쓰지 않습니다'
        : '비워 두면 기존 값 유지';
    });
    wrap.append(row);
  });
}

function selectedPath() {
  const on = document.querySelector('.path.is-on');
  return on ? on.dataset.path : 'direct';
}

function fillForm(d) {
  $('#form-target').textContent = d ? d.host : '신규';
  $('#f-address').value = d ? (d.ip || d.host) : '';
  $('#f-user').value = d ? (d.username || '') : '';
  $('#f-port').value = d && d.port ? d.port : 22;
  $('#f-pass').value = '';
  $('#f-role').value = d ? (d.role || '') : '';
  $('#f-task').value = d ? (d.task || '') : '';
  $('#f-scripts').value = (d && d.scripts_dir) || 'C:\\Scripts';
  const path = d ? (d.path || 'direct') : 'direct';
  renderPaths(path);
  $('#jump-fields').hidden = path !== 'jump';
  $('#f-pass').disabled = path === 'tsssh';
  $('#form-note').textContent = d && d.id ? `id: ${d.id}` : '등록되지 않은 기기';
  $('#save-note').textContent = '';
}

async function saveServer(ev) {
  ev.preventDefault();
  const d = state.selected;
  const path = selectedPath();
  const body = {
    id: d && d.id ? d.id : null,
    address: $('#f-address').value.trim(),
    username: $('#f-user').value.trim(),
    port: Number($('#f-port').value) || 22,
    password: $('#f-pass').value || null,
    role: $('#f-role').value,
    task: $('#f-task').value.trim(),
    scripts_dir: $('#f-scripts').value.trim() || 'C:\\Scripts',
    path,
    label: d ? d.host : $('#f-address').value.trim(),
  };
  if (path === 'jump') {
    body.jump = {
      address: $('#j-address').value.trim(),
      port: Number($('#j-port').value) || 22,
      username: $('#j-user').value.trim(),
      password: $('#j-pass').value || null,
    };
  }

  const note = $('#save-note');
  note.className = 'note';
  note.textContent = '저장 중...';
  try {
    const res = await postJson('/api/servers', body);
    note.className = 'note ' + (res.saved_to_disk ? 'ok' : '');
    note.textContent = res.note;
    await refresh();
  } catch (err) {
    note.className = 'note bad';
    note.textContent = String(err);
  }
}

async function testConn() {
  const d = state.selected;
  const note = $('#save-note');
  if (!d || !d.id) {
    note.className = 'note bad';
    note.textContent = '먼저 저장한 뒤 시험하십시오.';
    return;
  }
  note.className = 'note';
  note.textContent = `POST /api/servers/${d.id}/test — 연결 중...`;
  try {
    const res = await postJson(`/api/servers/${d.id}/test`);
    note.className = 'note ' + (res.ok ? 'ok' : 'bad');
    note.textContent = res.detail || (res.ok ? '연결됨' : '실패');
  } catch (err) {
    note.className = 'note bad';
    note.textContent = String(err);
  }
}

// ------------------------------------------------------------------ tabs

function selectTab(name) {
  state.tab = name;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.tab === name));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('is-active', p.dataset.pane === name));
  if (name === 'terminal' && state.fit) {
    setTimeout(() => { try { state.fit.fit(); } catch (e) { /* ignore */ } }, 0);
  }
}

function startTimer() {
  clearInterval(state.timer);
  if ($('#auto-refresh').checked) state.timer = setInterval(refresh, REFRESH_MS);
}

// ------------------------------------------------------------------ init

async function init() {
  document.querySelectorAll('.tab').forEach((t) =>
    t.addEventListener('click', () => selectTab(t.dataset.tab)));
  document.querySelectorAll('[data-action]').forEach((b) =>
    b.addEventListener('click', () => doAction(b.dataset.action)));

  $('#refresh').addEventListener('click', refresh);
  $('#auto-refresh').addEventListener('change', startTimer);
  $('#open-terminal').addEventListener('click', () => { selectTab('terminal'); connectTerminal(); });
  $('#term-connect').addEventListener('click', connectTerminal);
  $('#term-disconnect').addEventListener('click', disconnectTerminal);
  $('#term-clear').addEventListener('click', () => state.term && state.term.clear());
  $('#conn-form').addEventListener('submit', saveServer);
  $('#test-conn').addEventListener('click', testConn);
  $('#add-server').addEventListener('click', () => {
    state.selected = null;
    renderDevices();
    renderSelection();
    fillForm(null);
    selectTab('settings');
    $('#f-address').focus();
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, select, textarea')) return;
    if (state.tab === 'terminal') return;   // the terminal owns every key
    if (e.key === 'f' || e.key === 'F') refresh();
  });

  renderPaths('direct');
  renderQuick();

  try {
    state.meta = await api('/api/meta');
    $('#server-info').textContent =
      `FastAPI · uvicorn ${location.host} · ${state.meta.backend === 'mock' ? 'MOCK 데이터' : 'SSH 연결'}`;
    $('#hosts-file').textContent = state.meta.hosts_file;
    if (state.meta.using_example) {
      const n = $('#notice');
      n.hidden = false;
      n.innerHTML = '<b>MOCK</b> — hosts.json 이 없어 hosts.example.json 을 쓰고 있습니다. '
        + '연결 설정에서 저장하면 hosts.json 이 만들어집니다.';
    }
  } catch (err) {
    console.error(err);
  }

  await refresh();
  startTimer();
}

init();
