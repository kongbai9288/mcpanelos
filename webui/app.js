/* MC 服务器面板 前端逻辑（零框架） */
const $ = (id) => document.getElementById(id);
let STATE = {}, cur = null, curFile = null, pollTimer = null;

/* ---------------- 基础请求 ---------------- */
async function api(path, opt) {
  try {
    const r = await fetch('/api/' + path, opt);
    const j = await r.json();
    if (!j.ok && j.error) toast(j.error, 'err');
    return j;
  } catch (e) { toast('请求失败：' + e, 'err'); return { ok: false, error: String(e) }; }
}
const get = (p) => api(p, { headers: tokenHeader() });
const post = function (p, body, msg) {
  const h = Object.assign({ 'Content-Type': 'application/json' }, tokenHeader());
  return api(p, { method: 'POST', headers: h, body: JSON.stringify(body || {}) }).then(j => {
    if (j.ok && msg) toast(msg, 'ok'); if (j.ok) scheduleRefresh(); return j;
  });
};
function tokenHeader() { return TOKEN ? { 'X-Token': TOKEN } : {}; }
let TOKEN = localStorage.getItem('mcp_token') || '';

function toast(msg, kind) {
  const d = document.createElement('div');
  d.className = kind || ''; d.textContent = msg;
  $('toast').appendChild(d);
  setTimeout(() => d.remove(), kind === 'err' ? 6000 : 2600);
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function scheduleRefresh() { clearTimeout(pollTimer); pollTimer = setTimeout(refreshAll, 1200); }

/* ---------------- 导航 ---------------- */
document.querySelectorAll('#nav button').forEach(b => b.onclick = () => go(b.dataset.s));
function go(s) {
  document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('on', b.dataset.s === s));
  document.querySelectorAll('main section').forEach(x => x.classList.toggle('on', x.id === 's-' + s));
  localStorage.setItem('mcp_tab', s);
}

/* ---------------- 概览 ---------------- */
async function refreshAll() {
  const j = await get('state');
  if (!j.ok) return;
  STATE = j.data;
  $('badgeVer').textContent = 'v' + STATE.version;
  $('badgeUser').textContent = (STATE.root ? 'root' : STATE.user);
  $('badgeFw').textContent = '防火墙:' + STATE.firewall;
  $('badgeFw').className = 'badge ' + (STATE.firewall === 'none' ? 'err' : 'ok');
  $('addr').textContent = 'http://' + STATE.ip + ':' + STATE.port;
  renderHome(); renderInst(); fillSelects();
  const sys = await get('system/info');
  if (sys.ok) {
    const s = sys.data;
    $('sysGrid').innerHTML = [
      ['系统', s.os], ['内核', s.kernel], ['CPU', s.cpu + ' 核 · 负载 ' + s.load.join('/')],
      ['内存', s.mem_avail + ' 可用 / ' + s.mem_total], ['Java', STATE.java || '未安装'],
      ['运行时间', s.uptime], ['局域网 IP', s.ip], ['Python', s.python]
    ].map(([k, v]) => `<div class="card" style="margin:0"><div class="muted">${k}</div><div>${esc(v)}</div></div>`).join('');
    $('sysUptime').textContent = s.uptime;
  }
}
function renderHome() {
  $('homeInst').innerHTML = (STATE.instances || []).map(m => `
    <div class="card" style="margin:0">
      <h3>${esc(m.name)} <span class="badge ${m.running ? 'ok' : ''}">${m.running ? '运行中' : '已停止'}</span></h3>
      <div class="kv"><span>${esc(m.kind)}</span><span>${esc(m.version || '-')}</span><span>${esc(m.memory)}</span><span>端口 ${esc(m.port)}</span></div>
      <div class="row" style="margin-top:8px">
        <button class="sm ok" onclick="instAct('${esc(m.name)}','start')">启动</button>
        <button class="sm" onclick="instAct('${esc(m.name)}','stop')">停止</button>
        <button class="sm ghost" onclick="openConsole('${esc(m.name)}')">控制台</button>
        <button class="sm ghost" onclick="quickHealth('${esc(m.name)}')">体检</button>
      </div>
    </div>`).join('') || '<div class="muted">还没有实例，去「服务器」页新建一个吧。</div>';
}
async function quickHealth(name) {
  const j = await get('logs/analyze?path=' + encodeURIComponent(STATE.instances.find(x => x.name === name)?.dir + '/logs/latest.log') + '&limit=800');
  $('health').innerHTML = j.ok ? `<b>${esc(name)}</b>：${esc(j.data.verdict)}` : `<b>${esc(name)}</b>：<span class="muted">暂无日志</span>`;
}

/* ---------------- 服务器 ---------------- */
function renderInst() {
  const list = STATE.instances || [];
  $('instList').innerHTML = list.length ? `<table><tr><th>名称</th><th>类型/版本</th><th>内存</th><th>端口</th><th>状态</th><th>操作</th></tr>${
    list.map(m => `<tr>
      <td><b>${esc(m.name)}</b><div class="muted">${esc(m.dir)}</div></td>
      <td>${esc(m.kind)} ${esc(m.version || '')}</td>
      <td>${esc(m.memory)}</td><td>${esc(m.port)}</td>
      <td><span class="badge ${m.running ? 'ok' : ''}">${m.running ? '运行中' : '已停止'}</span></td>
      <td class="row">
        <button class="sm ok" onclick="instAct('${esc(m.name)}','start')">启动</button>
        <button class="sm" onclick="instAct('${esc(m.name)}','stop')">停止</button>
        <button class="sm" onclick="instAct('${esc(m.name)}','restart')">重启</button>
        <button class="sm ghost" onclick="openConsole('${esc(m.name)}')">控制台</button>
        <button class="sm ghost" onclick="instAct('${esc(m.name)}','backup')">备份</button>
        <button class="sm danger" onclick="delInst('${esc(m.name)}')">删除</button>
      </td></tr>`).join('')}</table>` : '<div class="muted">暂无实例</div>';
}
async function instAct(name, act) {
  const j = await post('instances/' + act, { name }, act === 'backup' ? '备份已创建' : '操作完成');
  if (j.ok && j.data && j.data.msg && act === 'backup') toast(j.data.msg, 'ok');
  refreshAll(); if (cur === name) openConsole(name);
}
function delInst(name) {
  if (!confirm('删除实例 ' + name + '？（只删配置，不删世界文件）')) return;
  post('instances/delete', { name, files: false }, '已删除').then(refreshAll);
}
function createInst() {
  const body = {
    name: $('niName').value.trim(), kind: $('niKind').value, version: $('niVer').value.trim(),
    memory: $('niMem').value.trim(), port: parseInt($('niPort').value || '25565'),
    java: $('niJava').value.trim(), extra_flags: $('niFlags').value.trim(),
    eula: $('niEula').checked
  };
  if (!body.name) return toast('请填写实例名', 'err');
  $('niTip').textContent = '正在下载安装服务端，可能需要几分钟…';
  post('instances/create', body, '创建成功').then(j => { $('niTip').textContent = j.ok ? '完成' : ''; refreshAll(); });
}
function openConsole(name) {
  cur = name; $('consoleCard').style.display = ''; $('consName').textContent = name; go('inst');
  tailConsole();
}
async function tailConsole() {
  if (!cur) return;
  const j = await get('instances/' + encodeURIComponent(cur) + '/console?n=300');
  if (j.ok) {
    const el = $('term');
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    el.innerHTML = (j.data.lines || []).map(colorLine).join('\n');
    if (atBottom) el.scrollTop = el.scrollHeight;
  }
  setTimeout(() => { if (cur && document.getElementById('s-inst').classList.contains('on')) tailConsole(); }, 2500);
}
function colorLine(l) {
  let cls = '';
  if (/ERROR|Exception|crash/i.test(l)) cls = 'lvl-e';
  else if (/WARN/i.test(l)) cls = 'lvl-w';
  else if (/FATAL|severe/i.test(l)) cls = 'lvl-c';
  return `<span class="${cls}">${esc(l)}</span>`;
}
function sendCmd() {
  const v = $('cmdInput').value.trim(); if (!v || !cur) return;
  post('instances/send', { name: cur, cmd: v }, '已发送').then(() => { $('cmdInput').value = ''; tailConsole(); });
}
$('cmdInput') && ($('cmdInput').onkeydown = e => { if (e.key === 'Enter') sendCmd(); });

/* ---------------- 下拉填充 ---------------- */
async function fillSelects() {
  const kinds = STATE.instances;
  const sel = $('niKind');
  if (sel && !sel.options.length) {
    const j = await get('catalog/kinds');
    if (j.ok) sel.innerHTML = j.data.items.map(k => `<option value="${k.key}">${esc(k.label)}</option>`).join('');
    $('dlKind').innerHTML = sel.innerHTML;
  }
  const opts = (kinds || []).map(m => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join('');
  ['nbtInst', 'logInst'].forEach(id => { const e = $(id); if (e) e.innerHTML = '<option value="">选择实例</option>' + opts; });
  if ($('pipeTpl') && !$('pipeTpl').options.length) loadPipes();
}
async function loadCatalog() {
  const kind = $('dlKind').value;
  $('dlTip').textContent = '获取中…';
  const j = await get('catalog/' + kind);
  if (!j.ok) { $('dlTip').textContent = j.error || '失败'; return; }
  const d = j.data;
  let arr = d.versions || (d.data && d.data.versions) || [];
  if (d.data && d.data.release) arr = d.data.release;
  if (d.latest) arr = [d.latest].concat(arr.filter(x => x !== d.latest));
  $('dlVers').innerHTML = (arr || []).slice(0, 60).map(v => `<span class="badge" style="margin:2px;cursor:pointer" onclick="pickVer('${esc(v)}')">${esc(v)}</span>`).join('')
    || (d.data && d.data.url ? `<a href="${d.data.url}">${d.data.version || '最新版下载'}</a>` : '（无列表）');
  $('dlTip').textContent = d.warn || ('共 ' + (arr || []).length + ' 个版本，点版本号填入上方');
}
function pickVer(v) { $('niVer').value = v; toast('已填入版本：' + v, 'ok'); }

/* ---------------- 下载 ---------------- */
function startDl() {
  const url = $('dlUrl').value.trim(), dest = $('dlDest').value.trim();
  if (!url || !dest) return toast('请填写 URL 和保存路径', 'err');
  post('dl/start', { url, dest }, '开始下载').then(loadDl);
}
async function loadDl() {
  const j = await get('dl/list'); if (!j.ok) return;
  $('dlTasks').innerHTML = (j.data.items || []).map(t => {
    const pct = t.total ? Math.round(t.done / t.total * 100) : 0;
    return `<div class="card" style="margin:6px 0"><div><b>${esc(t.label)}</b> <span class="badge">${esc(t.status)}</span></div>
      <div class="prog"><i style="width:${pct}%"></i></div>
      <div class="muted">${(t.done / 1048576).toFixed(1)} / ${(t.total / 1048576 || 0).toFixed(1)} MB ${esc(t.speed)} ${esc(t.error)}</div></div>`;
  }).join('') || '<div class="muted">暂无任务</div>';
  setTimeout(() => { if ($('s-dl').classList.contains('on')) loadDl(); }, 1500);
}

/* ---------------- Java ---------------- */
async function loadJava() {
  const j = await get('java/list'); if (!j.ok) return;
  $('javaList').innerHTML = `<table><tr><th>路径</th><th>版本</th><th>厂商</th></tr>${
    (j.data.items || []).map(x => `<tr><td>${esc(x.path)}</td><td>Java ${esc(x.major)}</td><td>${esc(x.vendor)}</td></tr>`).join('')
  }</table><div class="muted">当前选用：${esc(j.data.current || '无')}</div>`;
}
function ensureJava(v) { post('java/ensure', { major: v, install: true }, '处理完成').then(loadJava); }

/* ---------------- 防火墙 ---------------- */
async function loadFw() {
  const s = await get('firewall/status');
  if (s.ok) { $('fwRaw').textContent = s.data.raw; $('fwBackend').textContent = '后端：' + s.data.backend; }
  const p = await get('firewall/ports');
  if (p.ok) {
    $('fwPresets').innerHTML = Object.entries(p.data.presets).map(([k, v]) =>
      `<button class="sm ghost" onclick="fwPreset('${k}',${v.port},'${v.proto}')">放行 ${esc(k)} ${v.port}/${v.proto}</button>`).join('');
    $('fwPorts').innerHTML = `<table><tr><th>协议</th><th>地址</th><th>端口</th><th>进程</th></tr>${
      (p.data.items || []).slice(0, 40).map(x => `<tr><td>${esc(x.proto)}</td><td>${esc(x.addr)}</td><td>${esc(x.port)}</td><td>${esc(x.proc)}</td></tr>`).join('')}</table>`;
  }
}
function fwPreset(k, port, proto) { post('firewall/allow', { port, proto }, '已放行 ' + port + '/' + proto).then(loadFw); }
function fwPort(a) {
  const port = parseInt($('fwPort').value || '0'); if (!port) return toast('端口无效', 'err');
  post('firewall/' + a, { port, proto: $('fwProto').value }, '操作完成').then(loadFw);
}
function fwDel() { post('firewall/delete', { num: $('fwNum').value.trim() }, '已删除').then(loadFw); }
function fwAct(a) { post('firewall/' + a, {}, '操作完成').then(loadFw); }

/* ---------------- 自启动 ---------------- */
async function loadAuto() {
  const j = await get('autostart/env'); if (!j.ok) return;
  $('autoEnv').textContent = `systemd: ${j.data.systemd} · 当前用户: ${j.data.user} · root: ${j.data.root} · crontab: ${j.data.has_cron}`;
  const list = STATE.instances || [];
  const out = [];
  for (const m of list) {
    const s = await get('autostart/status/' + encodeURIComponent(m.name));
    out.push(`<tr><td><b>${esc(m.name)}</b></td><td>${s.ok ? esc(s.data.active) + ' / ' + esc(s.data.enabled) : '-'}</td>
      <td class="row">
        <button class="sm ok" onclick="autoInst('${esc(m.name)}')">安装自启</button>
        <button class="sm ghost" onclick="autoAct('${esc(m.name)}','start')">systemctl 启动</button>
        <button class="sm ghost" onclick="autoAct('${esc(m.name)}','stop')">停止</button>
        <button class="sm ghost" onclick="autoAct('${esc(m.name)}','restart')">重启</button>
        <button class="sm danger" onclick="autoUninst('${esc(m.name)}')">移除</button>
        <button class="sm ghost" onclick="autoCron('${esc(m.name)}')">@reboot 兜底</button>
      </td></tr>`);
  }
  $('autoList').innerHTML = list.length ? `<table><tr><th>实例</th><th>服务状态</th><th>操作</th></tr>${out.join('')}</table>` : '<div class="muted">暂无实例</div>';
}
function autoInst(n) { post('autostart/install', { name: n }, '已安装').then(loadAuto); }
function autoUninst(n) { post('autostart/uninstall', { name: n }, '已移除').then(loadAuto); }
function autoCron(n) { post('autostart/cron', { name: n }, '已加入').then(loadAuto); }
function autoAct(n, a) { post('autostart/action', { name: n, action: a }, '完成').then(loadAuto); }
function installPanel() { post('autostart/panel', { port: parseInt($('setPort').value || '8850'), exec: $('autoExec').value.trim() || 'python3 -m mcpanel' }, '已安装').then(loadAuto); }

/* ---------------- 清理 ---------------- */
async function scanClean() {
  const j = await get('cleaner/scan'); if (!j.ok) return;
  window.CLEAN = j.data.items || [];
  $('cleanList').innerHTML = (j.data.items || []).map((g, gi) => `
    <div class="card" style="margin:6px 0"><h3>${esc(g.instance)}</h3>
    ${g.groups.map((grp, ki) => `
      <div><label class="chk"><input type="checkbox" data-g="${gi}" data-k="${ki}" onchange="selGroup(this)"> <b>${esc(grp.label)}</b>（${grp.items.length} 项）</label>
      <div class="muted">${grp.items.slice(0, 6).map(i => esc(i.path.split('/').slice(-2).join('/')) + ' ' + esc(i.size_h)).join(' · ')}${grp.items.length > 6 ? ' …' : ''}</div></div>`).join('')}
    </div>`).join('') || '<div class="muted">没有可清理项</div>';
  const d = await get('cleaner/disk');
  if (d.ok) $('disk').innerHTML = `<table><tr><th>分区</th><th>容量</th><th>已用</th><th>可用</th><th>使用率</th></tr>${
    d.data.items.map(x => `<tr><td>${esc(x.mount)}</td><td>${esc(x.size)}</td><td>${esc(x.used)}</td><td>${esc(x.avail)}</td><td>${esc(x.use)}</td></tr>`).join('')}</table>`;
}
function selGroup(cb) {
  document.querySelectorAll('#cleanList input[type=checkbox]').forEach(x => { });
  const g = cb.dataset.g, k = cb.dataset.k;
  const grp = window.CLEAN[g].groups[k];
  document.querySelectorAll(`#cleanList input[data-g="${g}"][data-k="${k}"]`).forEach(() => { });
  grp._sel = cb.checked;
}
function cleanSel() {
  const paths = [];
  (window.CLEAN || []).forEach(g => g.groups.forEach(grp => { if (grp._sel) (grp.items || []).forEach(i => paths.push(i.path)); }));
  if (!paths.length) return toast('没有选中任何项', 'err');
  if (!confirm('确认删除 ' + paths.length + ' 个文件？此操作不可撤销')) return;
  post('cleaner/clean', { paths }, `已删除 ${paths.length} 个文件`).then(scanClean);
}

/* ---------------- 文件 ---------------- */
async function browse(p) {
  const j = await get('browser/list?path=' + encodeURIComponent(p));
  if (!j.ok) return;
  if (j.data && j.data.error) return toast(j.data.error, 'err');
  $('filePath').value = j.data.path;
  $('fileList').innerHTML = (j.data.items || []).map(f => `
    <div data-p="${esc(j.data.path + '/' + f.name)}" data-dir="${f.dir}">
      <span>${f.dir ? '📁' : '📄'}</span><span style="flex:1">${esc(f.name)}</span>
      <span class="muted">${f.dir ? '' : esc(f.size_h)}</span></div>`).join('');
  document.querySelectorAll('#fileList div').forEach(d => d.onclick = () => {
    if (d.dataset.dir === 'true') browse(d.dataset.p); else openFile(d.dataset.p);
  });
}
function upDir() { const p = $('filePath').value; browse(p.split('/').slice(0, -1).join('/') || '/'); }
async function openFile(p) {
  curFile = p;
  const j = await get('browser/read?path=' + encodeURIComponent(p));
  if (j.ok && j.data) $('fileText').value = j.data.text || '';
}
function saveFile() {
  if (!curFile) return toast('请先选择文件', 'err');
  post('browser/write', { path: curFile, text: $('fileText').value }, '已保存');
}
function newFile() {
  const n = prompt('新文件名'); if (!n) return;
  const p = $('filePath').value + '/' + n;
  post('browser/write', { path: p, text: '' }, '已创建').then(() => browse($('filePath').value));
}
function newDir() { const n = prompt('目录名'); if (!n) return; post('browser/mkdir', { path: $('filePath').value, name: n }, '已创建').then(() => browse($('filePath').value)); }
function renameFile() { if (!curFile) return; const n = prompt('新名称', curFile.split('/').pop()); if (!n) return; post('browser/rename', { path: curFile, newname: n }, '已重命名').then(() => browse($('filePath').value)); }
function delFile() { if (!curFile) return; if (!confirm('删除 ' + curFile + '？')) return; post('browser/delete', { path: curFile }, '已删除').then(() => browse($('filePath').value)); }
function chmodFile() { if (!curFile) return; post('browser/chmod', { path: curFile, mode: $('fileMode').value.trim() }, '已修改'); }
$('upInput') && ($('upInput').onchange = async function () {
  const f = this.files[0]; if (!f) return;
  const b64 = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result.split(',')[1]); fr.readAsDataURL(f); });
  post('browser/upload', { path: $('filePath').value, filename: f.name, b64 }, '已上传').then(() => browse($('filePath').value));
});

/* ---------------- NBT ---------------- */
async function loadLevelDat() {
  const n = $('nbtInst').value; if (!n) return;
  const j = await post('nbt/instance-level', { name: n });
  if (j.ok && j.data.items && j.data.items[0]) { $('nbtPath').value = j.data.items[0].path; nbtRead(); loadRules(); }
}
async function nbtRead() {
  const p = $('nbtPath').value.trim(); if (!p) return toast('请填路径', 'err');
  const j = await get('nbt/read?path=' + encodeURIComponent(p));
  if (j.ok) { $('nbtTree').textContent = j.data.tree; const i = await get('nbt/info?path=' + encodeURIComponent(p)); }
  else $('nbtTree').textContent = '';
  const info = await get('nbt/info?path=' + encodeURIComponent(p));
  if (info.ok) $('nbtTree').textContent = Object.entries(info.data).map(([k, v]) => k + ' = ' + v).join('\n') + '\n\n' + ($('nbtTree').textContent || '');
}
function nbtWrite() { const p = $('nbtPath').value.trim(); if (!p) return; post('nbt/write', { path: p, expr: $('nbtExpr').value.trim(), value: $('nbtVal').value }, '已写入').then(nbtRead); }
function nbtRuleSet() { post('nbt/gamerule', { path: $('nbtPath').value.trim(), rule: $('nbtRule').value, value: $('nbtRuleVal').value }, '已设置').then(nbtRead); }
function nbtSeedSet() { post('nbt/seed', { path: $('nbtPath').value.trim(), seed: $('nbtSeed').value.trim() }, '已改种子').then(nbtRead); }
async function loadRules() {
  const j = await get('nbt/gamerules?path=' + encodeURIComponent($('nbtPath').value.trim()));
  if (!j.ok) return;
  const cur = j.data.items || {};
  $('nbtRule').innerHTML = Object.keys(cur).concat(['doFireTick', 'doDaylightCycle', 'doMobSpawning', 'keepInventory', 'mobGriefing', 'randomTickSpeed'])
    .filter((v, i, a) => a.indexOf(v) === i)
    .map(k => `<option value="${esc(k)}">${esc(k)} = ${esc(cur[k])}</option>`).join('');
}

/* ---------------- 日志 ---------------- */
async function loadLogFiles() {
  const n = $('logInst').value; if (!n) return;
  const j = await get('logs/list?instance=' + encodeURIComponent(n));
  if (!j.ok) return;
  const items = j.data.items || [];
  $('logFile').innerHTML = items.map(f => `<option value="${esc(f.path)}">${esc(f.name)} (${(f.size / 1024).toFixed(0)}KB)</option>`).join('')
    || '<option value="">（暂无日志文件）</option>';
}
async function analyze() {
  const p = $('logFile').value; if (!p) return toast('请选择日志文件', 'err');
  const j = await get('logs/analyze?path=' + encodeURIComponent(p) + '&limit=3000');
  if (!j.ok) return;
  const d = j.data;
  $('logResult').innerHTML = `<div class="card" style="margin:0"><h3>${esc(d.verdict)}</h3>
    <div class="kv"><span>扫描 ${d.summary.lines} 行</span><span>致命 ${d.summary.critical}</span><span>错误 ${d.summary.error}</span><span>告警 ${d.summary.warn}</span></div>
    ${(d.findings || []).map(f => `<div class="finding ${f.level}">
      <b>${esc(f.label)}</b> × ${f.count}
      ${(f.samples || []).slice(0, 2).map(s => `<div class="muted">L${s.line}: ${esc(s.text.slice(0, 160))}</div>`).join('')}
      ${f.advice ? `<div class="muted">💡 ${esc(f.advice)}</div>` : ''}</div>`).join('')}
    ${(d.players || []).length ? `<div class="muted">玩家：${d.players.map(p => esc(p[0]) + '(' + p[1] + ')').join('，')}</div>` : ''}
  </div>`;
  $('logTail').innerHTML = (d.tail || []).map(colorLine).join('\n');
}

/* ---------------- 流水线 ---------------- */
async function loadPipes() {
  const j = await get('pipeline/list'); if (!j.ok) return;
  window.PIPES = j.data.items || [];
  window.TPL = j.data.templates || {};
  $('pipeSel').innerHTML = window.PIPES.map(p => `<option value="${esc(p.name)}">${esc(p.name)} (${esc(p.kind)})</option>`).join('') || '<option value="">（无）</option>';
  $('pipeTpl').innerHTML = '<option value="">选择模板…</option>' + Object.keys(window.TPL).map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join('');
  $('scriptText').value = j.data.simple || '';
  if (window.PIPES[0]) loadPipe();
}
function useTpl() { const k = $('pipeTpl').value; if (k && window.TPL[k]) $('pipeText').value = window.TPL[k]; }
function loadPipe() {
  const n = $('pipeSel').value; if (!n) return;
  get('pipeline/get?path=' + encodeURIComponent((window.PIPES.find(p => p.name === n) || {}).path)).then(j => { if (j.ok) $('pipeText').value = j.data.content; });
}
function savePipe() { const n = prompt('保存为（英文名）', 'pipeline'); if (!n) return; post('pipeline/save', { name: n, content: $('pipeText').value, kind: 'yaml' }, '已保存').then(loadPipes); }
function delPipe() { const n = $('pipeSel').value; if (!n) return; post('pipeline/delete', { name: n }, '已删除').then(loadPipes); }
function runPipe() {
  $('pipeOut').textContent = '执行中…';
  post('pipeline/run', { content: $('pipeText').value, kind: 'yaml', name: '网页运行' }).then(j => {
    if (j.ok) $('pipeOut').innerHTML = (j.data.logs || []).map(colorLine).join('\n');
  });
}
function runScript() {
  $('pipeOut').textContent = '执行中…';
  post('pipeline/run', { content: $('scriptText').value, kind: 'script', name: '命令脚本' }).then(j => {
    if (j.ok) $('pipeOut').innerHTML = (j.data.logs || []).map(colorLine).join('\n');
  });
}

/* ---------------- 联机 / 映射 ---------------- */
async function loadNet() {
  const j = await get('net/info');
  if (!j.ok) return;
  const d = j.data;
  $('netInfo').innerHTML = `<div class="kv">
      <span>局域网 IP：${esc(d.lan_ip)}</span><span>公网 IP：${esc(d.public_ip || '未获取')}</span>
      <span>Tailscale：${esc(d.tailscale_ip || '未安装')}</span></div>
    <table style="margin-top:8px"><tr><th>实例</th><th>协议</th><th>局域网地址</th><th>虚拟网(Tailscale)</th><th>公网(需映射)</th><th>状态</th></tr>
    ${(d.servers || []).map(s => `<tr><td><b>${esc(s.name)}</b></td><td>${esc(s.proto)} ${esc(s.port)}</td>
      <td><code>${esc(s.lan)}</code></td><td>${s.tailscale ? `<code>${esc(s.tailscale)}</code>` : '-'}</td>
      <td>${s.public ? `<code>${esc(s.public)}</code>` : '-'}</td>
      <td><span class="badge ${s.running ? 'ok' : ''}">${s.running ? '运行中' : '停止'}</span></td></tr>`).join('')}
    </table>
    <div class="muted" style="margin-top:6px">另一台电脑在游戏里「添加服务器」直接填上面的地址即可；跨网络建议用 Tailscale 那一列。</div>`;
  const r = await get('net/recommend');
  if (r.ok) $('netRec').innerHTML = r.data.items.map(x => `<div class="card" style="margin:0">
      <h3>${esc(x.title)}</h3><div class="muted">${esc(x.desc)}</div>
      <ol style="margin:6px 0 0 18px;padding:0">${x.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol></div>`).join('');
}
async function upnp(a) {
  const port = parseInt($('upPort').value || '25565');
  const j = await post('net/upnp/' + a, { port, proto: $('upProto').value, desc: $('upDesc').value || 'MC Server' });
  $('netOut').textContent = j.ok ? j.data.msg : (j.error || '失败');
}
async function upnpList() {
  const j = await get('net/upnp/list');
  $('netOut').textContent = j.ok ? (j.data.items || []).map(i => `${i.proto} ${i.external} → ${i.client}:${i.external}  ${i.desc}`).join('\n') || '（路由器不支持枚举）' : (j.error || '失败');
}
async function portCheck() {
  const j = await post('net/portcheck', {});
  const g = await get('net/portcheck?port=' + ($('upPort').value || '25565') + '&host=' + '127.0.0.1');
  $('netOut').textContent = g.ok ? (g.data.open ? '✅ 端口可连通' : '❌ 端口不通：' + (g.data.error || '')) : '检测失败';
}
async function ts(a) {
  const j = await post('net/tailscale/' + a, { ssh: false });
  $('netOut').textContent = j.ok ? j.data.msg : (j.error || '失败');
  tsStatus();
}
async function tsStatus() {
  const j = await get('net/tailscale');
  if (!j.ok) return;
  const d = j.data;
  $('netOut').textContent = d.installed
    ? `Tailscale: ${d.backend}\n本机 IP: ${(d.self || []).join(', ')}\n设备: ${(d.peers || []).map(p => p.name + ' ' + p.ip).join('\n')}`
    : '未安装 Tailscale';
}
async function frpPreview() {
  const j = await post('net/frp/preview', {
    server: $('frpServer').value.trim(), sport: +$('frpSport').value, local: +$('frpLocal').value,
    remote: +$('frpRemote').value, token: $('frpToken').value.trim()
  });
  if (j.ok) $('frpOut').textContent = j.data.config;
}
async function frpInstall() {
  const j = await post('net/frp', {
    server: $('frpServer').value.trim(), sport: +$('frpSport').value, local: +$('frpLocal').value,
    remote: +$('frpRemote').value, token: $('frpToken').value.trim()
  });
  $('frpOut').textContent = j.ok ? j.data.msg : (j.error || '失败');
}

/* ---------------- 设置 ---------------- */
async function loadSecret() {
  const j = await get('secret/status'); if (!j.ok) return;
  const s = j.data;
  $('secStatus').innerHTML = `当前用户 <b>${esc(s.user)}</b> · ${s.root ? '已为 root' : (s.nopasswd ? 'sudo 免密可用 ✅' : '需要 sudo 密码')}
    · sudoers 规则：${s.sudoers_exists ? '已写入 ✅' : '未写入'} · 已保存密码：${s.password_saved ? '是' : '否'}`;
}
function setSudo() { const p = $('sudoPw').value; if (!p) return toast('请输入密码', 'err'); post('secret/set', { password: p }, '已保存并验证').then(() => { $('sudoPw').value = ''; loadSecret(); }); }
function clearSudo() { post('secret/clear', {}, '已清除').then(loadSecret); }
function installSudoers() { post('secret/sudoers', {}, '已写入 sudoers').then(loadSecret); }
function saveSet() {
  const body = { port: parseInt($('setPort').value || '8850'), java_auto_install: $('setJava').checked };
  if ($('setPw').value) body.auth_password = $('setPw').value;
  post('settings', body, '已保存（端口改动需重启面板）');
}
function saveClean() { post('settings', { cleanup: { keep_logs_days: +$('ckLog').value, keep_crash_days: +$('ckCrash').value, keep_backups_days: +$('ckBak').value } }, '已保存'); }

/* ---------------- 启动 ---------------- */
(function init() {
  const t = localStorage.getItem('mcp_tab'); if (t) go(t);
  refreshAll().then(() => { loadJava(); loadFw(); loadAuto(); scanClean(); loadPipes(); loadSecret(); loadDl(); loadNet(); });
  browse('/srv/minecraft');
  setInterval(() => { refreshAll(); }, 15000);
  setInterval(() => { if ($('s-dl').classList.contains('on')) loadDl(); }, 2000);
})();
