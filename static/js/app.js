
'use strict';

/* ── LibraryStore ─────────────────────────────────────────── */
const LibraryStore = {
  ASSETS_KEY: 'roomai_assets',
  RTAGS_KEY:  'roomai_custom_room_tags',
  OTAGS_KEY:  'roomai_custom_obj_tags',
  PRESET_ROOM: ['Living Room','Bedroom','Kitchen','Bathroom','Dining Room','Office','Kids Room'],
  PRESET_OBJ:  ['Sofa','Chair','Bed frame','Table','Lamp','Curtain','Rug','Mirror','Plant','Decor','Storage'],

  hfEnabled: false,   // set after /health check
  _syncing:  false,

  _read(k)    { try { return JSON.parse(localStorage.getItem(k) || '[]'); } catch { return []; } },
  _writeLocal(k,v) {
    try { localStorage.setItem(k, JSON.stringify(v)); }
    catch { showToast('Local storage full — assets may not persist.'); }
  },

  getAssets() { return this._read(this.ASSETS_KEY); },

  _setAssets(arr) {
    this._writeLocal(this.ASSETS_KEY, arr);
  },

  // Push full asset list to HF (debounced — max 1 request per 2s)
  _pushTimer: null,
  _pushToHF() {
    if (!this.hfEnabled) return;
    clearTimeout(this._pushTimer);
    this._pushTimer = setTimeout(async () => {
      if (this._syncing) return;
      this._syncing = true;
      setSyncStatus('saving');
      try {
        const res = await authFetch('/library', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ assets: this.getAssets() }),
        });
        if (!res.ok) throw new Error('save failed');
        setSyncStatus('saved');
      } catch {
        setSyncStatus('error');
      } finally {
        this._syncing = false;
      }
    }, 1500);
  },

  saveAsset(a) {
    const arr = this.getAssets();
    arr.push(a);
    this._setAssets(arr);
    this._pushToHF();
  },

  deleteAsset(id) {
    this._setAssets(this.getAssets().filter(a => a.id !== id));
    this._pushToHF();
  },

  getCustomRoomTags(){ return this._read(this.RTAGS_KEY); },
  getCustomObjTags() { return this._read(this.OTAGS_KEY); },
  addCustomRoomTag(t){ if(!t) return; const a=this.getCustomRoomTags(); if(!a.includes(t)){a.push(t);this._writeLocal(this.RTAGS_KEY,a);} },
  addCustomObjTag(t) { if(!t) return; const a=this.getCustomObjTags();  if(!a.includes(t)){a.push(t);this._writeLocal(this.OTAGS_KEY,a);} },
  allRoomTags(){ return [...this.PRESET_ROOM, ...this.getCustomRoomTags()]; },
  allObjTags() { return [...this.PRESET_OBJ,  ...this.getCustomObjTags()];  },

  groupByRoom(q=''){
    const lq = q.toLowerCase();
    const assets = this.getAssets().filter(a =>
      !lq || a.name.toLowerCase().includes(lq) ||
             a.roomTag.toLowerCase().includes(lq) ||
             a.objectTag.toLowerCase().includes(lq)
    );
    const map = new Map();
    for (const a of assets) {
      const k = a.roomTag || 'Untagged';
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(a);
    }
    return map;
  },

  // Load from HF on startup — merges with localStorage
  async initFromHF() {
    setSyncStatus('loading');
    try {
      const res = await authFetch('/library');
      const json = await res.json();
      this.hfEnabled = json.enabled;
      if (!json.enabled) { setSyncStatus('local'); return; }

      const remoteIds  = new Set(json.assets.map(a => a.id));
      const localOnly  = this.getAssets().filter(a => !remoteIds.has(a.id));
      const merged     = [...json.assets, ...localOnly];
      this._setAssets(merged);

      if (localOnly.length > 0) this._pushToHF();

      setSyncStatus('synced');
    } catch {
      setSyncStatus('local');
    }
  },
};

/* ── App State ────────────────────────────────────────────── */
const ORIGINAL_KEY = 'roomai_original_scene';

const S = {
  libSelectedIds:    new Set(),
  editorSelectedIds: new Set(),
  assetMode:         'upload',
  baseBlob: null, baseURL: null,
  uploadedObjBlob: null, uploadedObjURL: null,
  userEditedPrompt:  false,
  // per-asset tag config — keyed by asset id
  assetConfigs:      new Map(),  // id -> { action, styles: Set, placement }
  activeAssetId:     null,       // which chip is currently focused
  libSearchQuery:    '',
  libExpanded:       new Map(),
  elibExpanded:      new Map(),
  getLibSelected()    { return LibraryStore.getAssets().filter(a => this.libSelectedIds.has(a.id)); },
  getEditorSelected() { return LibraryStore.getAssets().filter(a => this.editorSelectedIds.has(a.id)); },
  getConfig(id) {
    if (!this.assetConfigs.has(id)) {
      this.assetConfigs.set(id, { action: 'Add', placement: '' });
    }
    return this.assetConfigs.get(id);
  },
  removeConfig(id) { this.assetConfigs.delete(id); },
};

function getOriginalScene() {
  try { return localStorage.getItem(ORIGINAL_KEY) || null; } catch { return null; }
}
function saveOriginalScene(dataUrl) {
  try { localStorage.setItem(ORIGINAL_KEY, dataUrl); } catch { /* storage full — skip silently */ }
}
function clearOriginalScene() {
  try { localStorage.removeItem(ORIGINAL_KEY); } catch {}
}

/* ── Utils ────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);

function showToast(msg, ms = 4000, type = '') {
  const el = $('errorToast');
  el.textContent = msg;
  el.className = 'error-toast visible';
  if (type) el.classList.add(type);
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('visible'), ms);
}

function uid() { return Math.random().toString(36).slice(2,9) + Date.now().toString(36); }

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function dataUrlToBlob(url) {
  const [h,d] = url.split(',');
  const mime = h.match(/:(.*?);/)[1];
  const bin  = atob(d);
  const arr  = new Uint8Array(bin.length);
  for (let i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
  return new Blob([arr],{type:mime});
}

function readFile(file) {
  return new Promise((res,rej) => {
    const r = new FileReader();
    r.onload  = e => res(e.target.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

/* ── Auth ─────────────────────────────────────────────────── */
const Auth = {
  _token: null,
  _username: null,
  _credits: 0,
  _isAdmin: false,

  get token() { return this._token; },
  get username() { return this._username; },
  get credits() { return this._credits; },
  get isAdmin() { return this._isAdmin; },

  isLoggedIn() { return !!this._token; },

  persist() {
    try {
      localStorage.setItem('roomai_token', this._token || '');
      localStorage.setItem('roomai_username', this._username || '');
      localStorage.setItem('roomai_isAdmin', this._isAdmin ? '1' : '0');
    } catch {}
  },

  restore() {
    try {
      this._token = localStorage.getItem('roomai_token') || null;
      this._username = localStorage.getItem('roomai_username') || null;
      this._isAdmin = localStorage.getItem('roomai_isAdmin') === '1';
    } catch { this._token = null; this._username = null; this._isAdmin = false; }
  },

  clear() {
    this._token = null;
    this._username = null;
    this._credits = 0;
    this._isAdmin = false;
    try { localStorage.removeItem('roomai_token'); localStorage.removeItem('roomai_username'); localStorage.removeItem('roomai_isAdmin'); } catch {}
    updateCreditDisplay();
    $('logoutBtn').style.display = 'none';
  },

  async verify() {
    try {
      const res = await fetch('/me', { headers: { Authorization: `Bearer ${this._token}` } });
      if (!res.ok) throw new Error('invalid');
      const data = await res.json();
      this._username = data.username;
      this._credits = data.credits;
      this._isAdmin = data.is_admin || false;
      updateCreditDisplay();
      $('logoutBtn').style.display = '';
      return true;
    } catch {
      this.clear();
      return false;
    }
  },

  async login(username, password) {
    const res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    this._token = data.token;
    this._username = data.username;
    this._credits = data.credits;
    this._isAdmin = data.is_admin || false;
    this.persist();
    updateCreditDisplay();
    $('logoutBtn').style.display = '';
  },

  logout() {
    this.clear();
    showAuthOverlay();
  },
};

function updateCreditDisplay() {
  const c = Auth._credits;
  $('creditCount').textContent = Auth.isLoggedIn() ? c : '--';
  const dot = $('creditDot');
  const badge = $('creditBadge');
  dot.classList.remove('low', 'zero');
  badge.classList.remove('warning', 'danger');
  if (!Auth.isLoggedIn()) return;
  if (c <= 0) { dot.classList.add('zero'); badge.classList.add('danger'); }
  else if (c <= 5) { dot.classList.add('low'); badge.classList.add('danger'); }
  else if (c <= 15) { badge.classList.add('warning'); }
}

async function authFetch(url, opts = {}) {
  if (Auth.isLoggedIn()) {
    opts.headers = opts.headers || {};
    opts.headers['Authorization'] = `Bearer ${Auth.token}`;
  }
  const res = await fetch(url, opts);
  if (res.status === 401 || res.status === 402) {
    if (res.status === 402) {
      const j = await res.json().catch(() => ({}));
      showToast(j.detail || 'Insufficient credits.');
    } else {
      Auth.clear();
      showAuthOverlay();
      showToast('Session expired. Please log in again.');
    }
  }
  return res;
}

function showAuthOverlay() {
  $('authOverlay').classList.remove('hidden');
}
function hideAuthOverlay() {
  $('authOverlay').classList.add('hidden');
}
function showAuthError(msg) {
  const el = $('authError');
  el.textContent = msg;
  el.classList.add('visible');
}
function clearAuthError() {
  $('authError').classList.remove('visible');
}

async function handleAuthSubmit() {
  const username = $('authUsername').value.trim();
  const password = $('authPassword').value.trim();
  clearAuthError();
  $('authSubmitBtn').disabled = true;
  try {
    await Auth.login(username, password);
    hideAuthOverlay();
    $('authUsername').value = '';
    $('authPassword').value = '';
    LibraryStore.initFromHF().then(() => { renderLibrary(); renderEditorLibraryGrid(); });
  } catch (err) {
    showAuthError(err.message);
  } finally {
    $('authSubmitBtn').disabled = false;
  }
}

/* ── Auth UI bindings ─────────────────────────────────────── */
$('authSubmitBtn').addEventListener('click', handleAuthSubmit);
$('authPassword').addEventListener('keydown', e => { if (e.key === 'Enter') handleAuthSubmit(); });
$('authUsername').addEventListener('keydown', e => { if (e.key === 'Enter') $('authPassword').focus(); });
$('logoutBtn').addEventListener('click', () => Auth.logout());

/* ── Screen switch ────────────────────────────────────────── */
function switchScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  $(`screen-${name}`).classList.add('active');
  document.querySelectorAll('.nav-pill').forEach(p => p.classList.toggle('active', p.dataset.screen === name));
  if (name === 'library') renderLibrary();
}
document.querySelectorAll('.nav-pill').forEach(b => b.addEventListener('click', () => switchScreen(b.dataset.screen)));

/* ── Tag Modal ────────────────────────────────────────────── */
let _pendingDataUrl = null;

function populateSelects() {
  function fill(id, tags) {
    const s = $(id);
    s.innerHTML = '<option value="">— choose or type below —</option>';
    tags.forEach(t => s.insertAdjacentHTML('beforeend', `<option value="${escHtml(t)}">${escHtml(t)}</option>`));
  }
  fill('modalRoomSelect', LibraryStore.allRoomTags());
  fill('modalObjSelect',  LibraryStore.allObjTags());
}

function openTagModal(dataUrl) {
  _pendingDataUrl = dataUrl;
  $('modalPreviewImg').src = dataUrl;
  $('modalName').value = $('modalRoomCustom').value = $('modalObjCustom').value = '';
  populateSelects();
  $('tagModal').classList.remove('hidden');
  setTimeout(() => $('modalName').focus(), 60);
}

function closeTagModal() { $('tagModal').classList.add('hidden'); _pendingDataUrl = null; }

function saveModal() {
  const name    = $('modalName').value.trim();
  const roomTag = $('modalRoomCustom').value.trim() || $('modalRoomSelect').value || 'Untagged';
  const objTag  = $('modalObjCustom').value.trim()  || $('modalObjSelect').value  || 'Item';
  if (!name) { showToast('Please enter an asset name.'); $('modalName').focus(); return; }
  if (!_pendingDataUrl) return;
  if ($('modalRoomCustom').value.trim()) LibraryStore.addCustomRoomTag(roomTag);
  if ($('modalObjCustom').value.trim())  LibraryStore.addCustomObjTag(objTag);
  LibraryStore.saveAsset({ id: uid(), name, dataUrl: _pendingDataUrl, roomTag, objectTag: objTag, createdAt: Date.now() });
  closeTagModal();
  renderLibrary();
  renderEditorLibraryGrid();
  showToast(`"${name}" saved to Library.`);
}

$('modalClose').addEventListener('click', closeTagModal);
$('modalCancel').addEventListener('click', closeTagModal);
$('modalSave').addEventListener('click', saveModal);
$('tagModal').addEventListener('click', e => { if (e.target === $('tagModal')) closeTagModal(); });
$('modalName').addEventListener('keydown', e => { if (e.key === 'Enter') saveModal(); });

$('libAddBtn').addEventListener('click', () => $('libFileInput').click());
$('libFileInput').addEventListener('change', async e => {
  const f = e.target.files[0];
  if (!f || !f.type.startsWith('image/')) return;
  openTagModal(await readFile(f));
  e.target.value = '';
});

/* ── Library Rendering ────────────────────────────────────── */
function renderLibrary() {
  const container = $('library-grid-container');
  const emptyEl   = $('library-empty');
  const grouped   = LibraryStore.groupByRoom(S.libSearchQuery);

  if (grouped.size === 0) { container.innerHTML = ''; emptyEl.style.display = ''; return; }
  emptyEl.style.display = 'none';
  container.innerHTML = '';

  for (const [roomTag, assets] of grouped) {
    const COLLAPSED = 6;
    const expanded  = S.libExpanded.get(roomTag) || false;
    const visible   = expanded ? assets : assets.slice(0, COLLAPSED);

    const sec = document.createElement('div');
    sec.className = 'lib-section';
    sec.innerHTML = `
      <div class="lib-section-header">
        <div class="lib-section-title">${escHtml(roomTag)}<span class="lib-section-count">${assets.length}</span></div>
        ${assets.length > COLLAPSED
          ? `<button class="lib-expand-btn" data-room="${escHtml(roomTag)}">${expanded ? '↑ Collapse' : `↓ Show all ${assets.length}`}</button>`
          : ''}
      </div>
      <div class="lib-thumb-grid">
        ${visible.map(a => renderLibCard(a, S.libSelectedIds.has(a.id))).join('')}
      </div>`;

    sec.querySelector('.lib-expand-btn')?.addEventListener('click', () => {
      S.libExpanded.set(roomTag, !expanded);
      renderLibrary();
    });

    sec.querySelectorAll('.lib-card').forEach(card => {
      card.addEventListener('click', e => {
        if (e.target.classList.contains('lib-del-btn')) {
          if (confirm(`Delete "${card.dataset.name}"?`)) {
            LibraryStore.deleteAsset(card.dataset.id);
            S.libSelectedIds.delete(card.dataset.id);
            S.editorSelectedIds.delete(card.dataset.id);
            renderLibrary(); renderEditorLibraryGrid(); updateFloatBar(); updateChips();
          }
          return;
        }
        toggleLibSelect(card.dataset.id);
      });
    });

    container.appendChild(sec);
  }
}

function renderLibCard(a, selected) {
  return `
    <div class="lib-card${selected?' selected':''}" data-id="${a.id}" data-name="${escHtml(a.name)}">
      <button class="lib-del-btn" title="Delete">✕</button>
      <div class="lib-check">✓</div>
      <img class="lib-card-img" src="${a.dataUrl}" alt="${escHtml(a.name)}" loading="lazy" />
      <div class="lib-card-info">
        <div class="lib-card-name">${escHtml(a.name)}</div>
        <div class="lib-card-tags">
          <span class="lib-tag room">${escHtml(a.roomTag)}</span>
          <span class="lib-tag obj">${escHtml(a.objectTag)}</span>
        </div>
      </div>
    </div>`;
}

function toggleLibSelect(id) {
  if (S.libSelectedIds.has(id)) { S.libSelectedIds.delete(id); }
  else { if (S.libSelectedIds.size >= 5) { showToast('Max 5 objects — deselect one first.'); return; } S.libSelectedIds.add(id); }
  renderLibrary();
  updateFloatBar();
}

/* Search */
$('libSearchBtn').addEventListener('click', () => { $('libSearchBar').style.display = 'flex'; $('libSearchInput').focus(); });
$('libSearchClose').addEventListener('click', () => { S.libSearchQuery=''; $('libSearchInput').value=''; $('libSearchBar').style.display='none'; renderLibrary(); });
$('libSearchInput').addEventListener('input', e => { S.libSearchQuery = e.target.value; renderLibrary(); });

/* Float bar */
function updateFloatBar() {
  const sel = S.getLibSelected();
  const bar = $('floatBar');
  if (sel.length === 0) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  $('floatCount').textContent = `${sel.length} / 5`;
  $('floatBadge').textContent = sel.length;
  $('floatChips').innerHTML = sel.slice(0,4).map(a =>
    `<div class="float-chip"><img src="${a.dataUrl}" alt="" />${escHtml(a.name)}</div>`
  ).join('') + (sel.length > 4 ? `<div class="float-chip">+${sel.length-4} more</div>` : '');
}

$('floatClear').addEventListener('click', () => { S.libSelectedIds.clear(); renderLibrary(); updateFloatBar(); });
$('libGenerateBtn').addEventListener('click', () => {
  if (S.libSelectedIds.size === 0) return;
  S.editorSelectedIds = new Set(S.libSelectedIds);
  S.userEditedPrompt  = false;
  switchScreen('editor');
  setAssetMode('library');
  renderEditorLibraryGrid();
  updateChips();
  rebuildPrompt();
});

/* ── Editor: room image ───────────────────────────────────── */
function setBase(blob, url) {
  S.baseBlob = blob; S.baseURL = url;
  if (blob) {
    $('roomZone').style.display = 'none';
    $('roomThumb').src = url;
    $('roomMini').classList.add('visible');
    $('step2').classList.remove('disabled');
    $('step3').classList.remove('disabled');
  } else {
    $('roomZone').style.display = '';
    $('roomMini').classList.remove('visible');
    $('step2').classList.add('disabled');
    $('step3').classList.add('disabled');
    setObj(null, null);
  }
  syncCanvas();
}

function setObj(blob, url) {
  S.uploadedObjBlob = blob; S.uploadedObjURL = url;
  if (blob) {
    $('objZone').style.display = 'none';
    $('objThumb').src = url;
    $('objMini').classList.add('visible');
  } else {
    $('objZone').style.display = '';
    $('objMini').classList.remove('visible');
  }
  updateChips(); rebuildPrompt(); syncCanvas();
}

$('roomInput').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  const url = await readFile(f);
  // Only save as original if none stored yet — preserves across iterations
  if (!getOriginalScene()) saveOriginalScene(url);
  setBase(f, url);
  updateOriginalBtn();
});
$('roomRemove').addEventListener('click', () => { $('roomInput').value=''; setBase(null,null); $('resultView').classList.remove('visible'); });
$('objInput').addEventListener('change', async e => {

// ── Drag-drop visual feedback ──────────────────────────────
document.querySelectorAll('.drop-zone').forEach(zone => {
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-active'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-active'));
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-active'); });
});
  const f = e.target.files[0]; if (!f) return;
  setObj(f, await readFile(f));
});
$('objRemove').addEventListener('click', () => { $('objInput').value=''; setObj(null,null); });

function syncCanvas() {
  if ($('resultView').classList.contains('visible')) return;
  if (S.baseURL) {
    $('emptyState').style.display = 'none';
    $('canvasMainImg').src = S.baseURL;
    $('viewport').classList.add('visible');
    const hasRef = S.assetMode === 'upload' ? !!S.uploadedObjURL : S.editorSelectedIds.size > 0;
    if (hasRef) {
      const src = S.assetMode === 'upload' ? S.uploadedObjURL : (S.getEditorSelected()[0]?.dataUrl || '');
      $('canvasAssetImg').src = src;
      $('viewToggles').classList.add('visible');
    } else {
      $('viewToggles').classList.remove('visible');
      showSceneView();
    }
  } else {
    $('emptyState').style.display = '';
    $('viewport').classList.remove('visible');
  }
}

function showSceneView() {
  $('btnViewScene').classList.add('active'); $('btnViewAsset').classList.remove('active');
  $('canvasMainImg').style.display = ''; $('canvasAssetImg').style.display = 'none';
}
$('btnViewScene').addEventListener('click', showSceneView);
$('btnViewAsset').addEventListener('click', () => {
  $('btnViewAsset').classList.add('active'); $('btnViewScene').classList.remove('active');
  $('canvasMainImg').style.display = 'none'; $('canvasAssetImg').style.display = '';
});

$('refineBtn').addEventListener('click', () => {
  const url = $('afterImg').src;
  setBase(dataUrlToBlob(url), url);
  setObj(null,null);
  S.editorSelectedIds.clear();
  S.assetConfigs.clear();
  S.activeAssetId = null;
  $('promptInput').value = ''; S.userEditedPrompt = false;
  $('resultView').classList.remove('visible');
  syncCanvas(); updateChips(); rebuildPrompt();
});

/* ── Asset mode toggle ────────────────────────────────────── */
function setAssetMode(mode) {
  S.assetMode = mode;
  document.querySelectorAll('.asset-toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('asset-upload-mode').style.display  = mode === 'upload'  ? '' : 'none';
  $('asset-library-mode').style.display = mode === 'library' ? '' : 'none';
  updateAssetBadge(); syncCanvas();
}
document.querySelectorAll('.asset-toggle-btn').forEach(b => b.addEventListener('click', () => {
  setAssetMode(b.dataset.mode);
  if (b.dataset.mode === 'library') renderEditorLibraryGrid();
}));

function updateAssetBadge() {
  const badge = $('assetBadge');
  const count = S.assetMode === 'library' ? S.editorSelectedIds.size : (S.uploadedObjBlob ? 1 : 0);
  if (count > 0) { badge.textContent = `${count} selected`; badge.style.display = ''; }
  else badge.style.display = 'none';
}

/* ── Editor inline library grid ──────────────────────────── */
function renderEditorLibraryGrid() {
  const container = $('editor-lib-grid');
  const grouped   = LibraryStore.groupByRoom();
  if (grouped.size === 0) {
    container.innerHTML = `<div class="elib-empty">Library is empty.<br>Go to <strong>Library</strong> tab to add assets.</div>`;
    return;
  }
  container.innerHTML = '';
  for (const [roomTag, assets] of grouped) {
    const COLLAPSED = 5;
    const expanded  = S.elibExpanded.get(roomTag) || false;
    const visible   = expanded ? assets : assets.slice(0, COLLAPSED);

    const sec = document.createElement('div');
    sec.className = 'elib-section';
    sec.innerHTML = `
      <div class="elib-section-header">
        <span>${escHtml(roomTag)}</span>
        ${assets.length > COLLAPSED
          ? `<button class="elib-expand-btn" data-room="${escHtml(roomTag)}">${expanded ? '▲' : `▼ ${assets.length - COLLAPSED} more`}</button>`
          : ''}
      </div>
      <div class="elib-grid">
        ${visible.map(a => `
          <div class="elib-thumb${S.editorSelectedIds.has(a.id)?' selected':''}" data-id="${a.id}" title="${escHtml(a.name)}">
            <img src="${a.dataUrl}" alt="${escHtml(a.name)}" loading="lazy" />
            <div class="elib-check">✓</div>
          </div>`).join('')}
      </div>`;

    sec.querySelector('.elib-expand-btn')?.addEventListener('click', () => {
      S.elibExpanded.set(roomTag, !expanded);
      renderEditorLibraryGrid();
    });
    sec.querySelectorAll('.elib-thumb').forEach(thumb => {
      thumb.addEventListener('click', () => {
        const id = thumb.dataset.id;
        if (S.editorSelectedIds.has(id)) { S.editorSelectedIds.delete(id); }
        else { if (S.editorSelectedIds.size >= 5) { showToast('Max 5 objects.'); return; } S.editorSelectedIds.add(id); }
        S.userEditedPrompt = false;
        updateAssetBadge(); updateChips(); rebuildPrompt(); syncCanvas(); renderEditorLibraryGrid();
      });
    });
    container.appendChild(sec);
  }
}

/* ── Prompt Panel ─────────────────────────────────────────── */
function getActiveAssets() {
  if (S.assetMode === 'library') return S.getEditorSelected();
  if (S.uploadedObjBlob) return [{ id:'__upload', name:'Uploaded', dataUrl: S.uploadedObjURL, objectTag:'Object' }];
  return [];
}

/* Which tag groups are relevant per action */
const ACTION_NEEDS = {
  'Add':     { placement: true  },
  'Replace': { placement: false },
  'Remove':  { placement: false },
  'Restyle': { placement: false },
  '':        { placement: true  },  // no action selected — show placement
};

function updateTagGroupVisibility(action) {
  const needs = ACTION_NEEDS[action] || ACTION_NEEDS[''];
  $('group-placement').style.display = needs.placement ? '' : 'none';
}

/* Focus a chip — load its config into the tag UI */
function focusAsset(id) {
  S.activeAssetId = id;

  document.querySelectorAll('.chip').forEach(c => c.classList.toggle('chip-active', c.dataset.id === id));

  const cfg = S.getConfig(id);

  document.querySelectorAll('#action-tags .tag').forEach(t => {
    t.classList.toggle('active', cfg.action && t.textContent === cfg.action);
  });
  document.querySelectorAll('#placement-tags .tag').forEach(t => {
    t.classList.toggle('active', t.dataset.value === cfg.placement);
  });

  updateTagGroupVisibility(cfg.action);

  const assets = getActiveAssets();
  const asset  = assets.find(a => a.id === id);
  const label  = $('directive-for-label');
  if (label && asset) label.textContent = `Editing settings for: ${asset.objectTag || asset.name}`;
}

function updateChips() {
  const row    = $('selected-chips');
  const assets = getActiveAssets();
  updateAssetBadge();

  if (assets.length === 0) {
    row.innerHTML = '<span class="chips-empty">No objects selected</span>';
    S.activeAssetId = null;
    const label = $('directive-for-label');
    if (label) label.textContent = '';
    return;
  }

  row.innerHTML = assets.map(a => `
    <div class="chip${S.activeAssetId === a.id ? ' chip-active' : ''}" data-id="${a.id}">
      <img src="${a.dataUrl}" alt="" />
      <span class="chip-label">${escHtml(a.objectTag || a.name)}</span>
      <span class="chip-x" data-id="${a.id}">✕</span>
    </div>`).join('');

  // Click chip body = focus that asset
  row.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', e => {
      if (e.target.classList.contains('chip-x')) return;
      focusAsset(chip.dataset.id);
      S.userEditedPrompt = false;
      rebuildPrompt();
    });
  });

  // Click X = remove asset
  row.querySelectorAll('.chip-x').forEach(x => x.addEventListener('click', e => {
    e.stopPropagation();
    const id = x.dataset.id;
    S.removeConfig(id);
    if (id === '__upload') { setObj(null, null); }
    else { S.editorSelectedIds.delete(id); renderEditorLibraryGrid(); }
    // Focus first remaining asset
    const remaining = getActiveAssets().filter(a => a.id !== id);
    S.activeAssetId = remaining.length ? remaining[0].id : null;
    S.userEditedPrompt = false;
    updateAssetBadge(); updateChips();
    if (S.activeAssetId) focusAsset(S.activeAssetId);
    rebuildPrompt(); syncCanvas();
  }));

  // Auto-focus first asset if none active
  if (!S.activeAssetId || !assets.find(a => a.id === S.activeAssetId)) {
    S.activeAssetId = assets[0].id;
  }
  focusAsset(S.activeAssetId);
}

function buildSentence(name, cfg) {
  const place = cfg.placement || '';

  switch (cfg.action) {
    case 'Add':
      return `Add the reference ${name} to the room${place ? ', positioned ' + place : ''}.`;

    case 'Replace':
      return `Replace the existing ${name} with the reference ${name}, keeping it in the same location.`;

    case 'Remove':
      return `Remove the ${name} from the room and inpaint the exposed background.`;

    case 'Restyle':
      return `Restyle the existing ${name} using the reference as the new finish — keep its position and size unchanged.`;

    case '':
      return `Use the reference ${name} in the room${place ? ', positioned ' + place : ''}.`;

    default:
      return `Apply the reference ${name} to the room${place ? ', placed ' + place : ''}.`;
  }
}

function buildPrompt() {
  const assets = getActiveAssets();
  if (assets.length === 0) return '';

  if (assets.length === 1) {
    const a = assets[0];
    return buildSentence(a.objectTag || a.name, S.getConfig(a.id));
  }

  const instructions = assets.map((a, i) => {
    const name = a.objectTag || a.name;
    const cfg  = S.getConfig(a.id);
    return `${i + 1}. ${buildSentence(name, cfg)}`;
  }).join('\n');

  return instructions;
}

function rebuildPrompt() { if (!S.userEditedPrompt) $('promptInput').value = buildPrompt(); }

/* Tag bindings — always apply to the currently focused asset */
document.querySelectorAll('#action-tags .tag').forEach(t => t.addEventListener('click', () => {
  if (!S.activeAssetId) return;
  const alreadyActive = t.classList.contains('active');
  document.querySelectorAll('#action-tags .tag').forEach(x => x.classList.remove('active'));
  if (!alreadyActive) {
    t.classList.add('active');
    S.getConfig(S.activeAssetId).action = t.textContent;
    updateTagGroupVisibility(t.textContent);
  } else {
    // Deselect — no action preference
    S.getConfig(S.activeAssetId).action = '';
    updateTagGroupVisibility('');
  }
  S.userEditedPrompt = false; rebuildPrompt();
}));

/* Preset prompt buttons — fill the textarea directly */
document.querySelectorAll('.btn-preset').forEach(btn => {
  btn.addEventListener('click', () => {
    $('promptInput').value = btn.dataset.prompt;
    S.userEditedPrompt = true;
  });
});

document.querySelectorAll('#placement-tags .tag').forEach(t => t.addEventListener('click', () => {
  if (!S.activeAssetId) return;
  const alreadyActive = t.classList.contains('active');
  document.querySelectorAll('#placement-tags .tag').forEach(x => x.classList.remove('active'));
  if (!alreadyActive) {
    t.classList.add('active');
    S.getConfig(S.activeAssetId).placement = t.dataset.value;
  } else {
    // Deselect — no placement preference
    S.getConfig(S.activeAssetId).placement = '';
  }
  S.userEditedPrompt = false; rebuildPrompt();
}));

$('promptInput').addEventListener('input', () => { S.userEditedPrompt = true; });

/* ── Generate ─────────────────────────────────────────────── */
$('generateBtn').addEventListener('click', async () => {
  const btn = $('generateBtn');
  if (btn.disabled) return;
  if (!S.baseBlob) return showToast('Upload a room scene first.');
  const prompt = $('promptInput').value.trim();
  if (!prompt)   return showToast('Add a directive or select tags.');

  const assets     = getActiveAssets();
  const objectTags = assets.map(a => a.objectTag || a.name);

  btn.disabled = true;
  btn.textContent = 'Processing...';
  $('loader').classList.add('visible');
  $('resultView').classList.remove('visible');
  $('viewport').classList.remove('visible');
  $('emptyState').style.display = 'none';

  const genStart = Date.now();
  const elapsedInterval = setInterval(() => {
    const secs = Math.floor((Date.now() - genStart) / 1000);
    const mins = Math.floor(secs / 60);
    const remaining = Math.max(0, 120 - secs);
    const etaText = secs < 30 ? 'Analyzing scene...' : `Generating... ${mins > 0 ? mins + 'm ' : ''}${secs % 60}s elapsed · ~${remaining}s remaining`;
    const loaderDesc = document.getElementById('loaderDesc');
    if (loaderDesc) loaderDesc.textContent = etaText;
  }, 1000);

  try {
    const fd  = new FormData();
    const ext = S.baseBlob.type === 'image/jpeg' ? 'jpg' : 'png';
    fd.append('room_image', new File([S.baseBlob], `room.${ext}`, { type: S.baseBlob.type }));
    fd.append('prompt', prompt);
    fd.append('object_tags', JSON.stringify(objectTags));

    for (let i = 0; i < Math.min(assets.length, 5); i++) {
      const a    = assets[i];
      const blob = a.id === '__upload' ? S.uploadedObjBlob : dataUrlToBlob(a.dataUrl);
      fd.append(`object_image_${i+1}`, new File([blob], `obj${i+1}.png`, { type: 'image/png' }));
    }

    const res  = await authFetch('/edit', { method: 'POST', body: fd });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || `Error ${res.status}`);
    if (json.credits !== undefined) {
      Auth._credits = json.credits;
      updateCreditDisplay();
    }

    const b64      = json.images_b64?.length ? json.images_b64[0] : json.image_b64;
    const finalUrl = `data:image/${json.format};base64,${b64}`;

    $('beforeImg').src    = S.baseURL;
    $('afterImg').src     = finalUrl;
    $('downloadBtn').href = finalUrl;
    $('resultView').classList.add('visible');
    updateOriginalBtn();
    showToast('Render complete', 3000, 'success');
  } catch (err) {
    showToast(err.message || 'Generation failed', 6000, 'error');
    syncCanvas();
  } finally {
    clearInterval(elapsedInterval);
    $('loader').classList.remove('visible');
    const loaderDesc = document.getElementById('loaderDesc');
    if (loaderDesc) loaderDesc.textContent = 'This usually takes 60-120 seconds';
    btn.disabled = false;
    btn.textContent = 'Process Render';
  }
});

/* ── Original scene ───────────────────────────────────────── */
let _showingOriginal = false;

function updateOriginalBtn() {
  const orig = getOriginalScene();
  const viewBtn  = $('viewOriginalBtn');
  const clearBtn = $('clearOriginalBtn');
  if (orig) {
    viewBtn.style.display  = '';
    clearBtn.style.display = '';
  } else {
    viewBtn.style.display  = 'none';
    clearBtn.style.display = 'none';
    hideOriginalPane();
  }
}

function showOriginalPane() {
  const orig = getOriginalScene();
  if (!orig) return;
  _showingOriginal = true;
  $('originalImg').src = orig;
  $('paneOriginal').style.display = '';
  $('comparisonGrid').classList.add('three-col');
  $('viewOriginalBtn').textContent = 'Hide Original';
}

function hideOriginalPane() {
  _showingOriginal = false;
  $('paneOriginal').style.display = 'none';
  $('comparisonGrid').classList.remove('three-col');
  $('viewOriginalBtn').textContent = 'View Original';
}

$('viewOriginalBtn').addEventListener('click', () => {
  if (_showingOriginal) hideOriginalPane();
  else showOriginalPane();
});

$('clearOriginalBtn').addEventListener('click', () => {
  if (!confirm('Remove the stored original image? This cannot be undone.')) return;
  clearOriginalScene();
  hideOriginalPane();
  updateOriginalBtn();
  showToast('Original image cleared.');
});

/* ── Sync status indicator ───────────────────────────────── */
function setSyncStatus(state) {
  const el = $('syncStatus');
  if (!el) return;
  const map = {
    loading: { dot: 'yellow', text: 'Loading library…' },
    saving:  { dot: 'yellow', text: 'Saving…'          },
    saved:   { dot: 'green',  text: 'Library saved'    },
    synced:  { dot: 'green',  text: 'Library synced'   },
    error:   { dot: 'red',    text: 'Sync failed'      },
    local:   { dot: 'grey',   text: 'Local only'       },
  };
  const s = map[state] || map.local;
  el.innerHTML = `<span class="sync-dot ${s.dot}"></span>${s.text}`;
  // Auto-clear success messages after 3s
  if (state === 'saved' || state === 'synced') {
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.innerHTML = `<span class="sync-dot green"></span>HF synced`; }, 3000);
  }
}

/* ── Init ─────────────────────────────────────────────────── */
updateChips();
rebuildPrompt();
updateOriginalBtn();

async function appInit() {
  Auth.restore();
  if (Auth.isLoggedIn()) {
    const valid = await Auth.verify();
    if (!valid) {
      showAuthOverlay();
      return;
    }
    hideAuthOverlay();
    LibraryStore.initFromHF().then(() => {
      renderLibrary();
      renderEditorLibraryGrid();
    });
  } else {
    showAuthOverlay();
  }
}
appInit();
// Cookie consent check
if (localStorage.getItem('cookie_consent')) {
  document.getElementById('cookieBanner').style.display = 'none';
}
