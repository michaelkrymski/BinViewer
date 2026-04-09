'use strict';

const WORKFLOW_BUCKET_ORDER = [
  'Quick Start',
  'Boost / WGDC',
  'Fueling / Ethanol',
  'Ignition',
  'Torque / Load / Limits',
  'Map Switching / Multi-map',
  'Rail / HPFP / Fuel System',
  'Diagnostics / Display / Utility',
  'Other',
  'Low Priority',
];

const METADATA_STORAGE_KEY = 'tunerpro-viewer.metadata-overrides.v1';

const state = {
  files: { xdf: null, bin: null, bin2: null },
  tables: [],
  categories: {},
  search: '',
  hasDiff: false,
  viewMode: 'workflow',
  hideAutogen: true,
  selectedMap: 'Map 1',
  selectedCategory: null,
  selectedTableId: null,
  session: null,
  selections: {},
  previews: {},
  editorForms: {},
  axisDrafts: {},
  metadataOverrides: loadMetadataOverrides(),
  editingMetadataTableId: null,
  openState: { cards: new Set(), groups: new Set(), families: new Set() },
};

const AI_CONTEXT_PROMPT = `You are an expert automotive ECU tuning assistant. I will share ECU calibration data copied from TunerPro XDF Viewer.

DATA FORMAT
- Each block starts with [Table Name] followed by units if present.
- 2D tables are shown as a grid where rows are the Y axis and columns are the X axis.
- 1D tables are shown as a single row with axis labels.
- Scalars are shown as a single Value line.
- In diff mode, values like old->new indicate a change between two BIN files.

HOW TO INTERPRET IT
- Values are already scaled using the XDF math equation.
- Axis labels are the lookup inputs.
- If I paste multiple tables, compare them together and infer likely tuning intent.
- Flag anything suspicious such as flatlined values, obviously wrong scaling, or unusually aggressive timing, boost, or fueling.
- When diff values are present, focus on what changed and why it might matter.

When I paste a table, respond like a tuning-aware assistant that understands ECU maps, ignition, fueling, boost control, torque modeling, and limiters.`;

const $ = id => document.getElementById(id);
const uploadPanel = $('upload-panel');
const resultsArea = $('results-area');
const tableList = $('table-list');
const mapTabs = $('map-tabs');
const categoryNav = $('category-nav');
const categoryTabs = $('category-tabs');
const workspaceOverview = $('workspace-overview');
const inspectorEmpty = $('inspector-empty');
const inspectorPanel = $('inspector-panel');
const inspectorHeader = $('inspector-header');
const inspectorBody = $('inspector-body');
const mapPanel = $('map-panel');
const categoryPanel = $('category-panel');
const searchInput = $('search-input');
const searchClear = $('search-clear');
const countBadge = $('count-badge');
const progressBar = $('progress-bar');
const progressFill = $('progress-fill');
const uploadError = $('upload-error');
const btnParse = $('btn-parse');
const btnClear = $('btn-clear');
const btnDebug = $('btn-debug');
const btnHideAutogen = $('btn-hide-autogen');
const btnCollapseAll = $('btn-collapse-all');
const btnExportBinTop = $('btn-export-bin-top');
const btnNewFiles = $('btn-new-files');
const btnAiContext = $('btn-ai-context');
const btnCopyAiContext = $('btn-copy-ai-context');
const viewModeSelect = $('view-mode');
const editorToolbar = $('editor-toolbar');
const draftBadge = $('draft-badge');
const draftMeta = $('draft-meta');
const btnUndoDraft = $('btn-undo-draft');
const btnResetDraft = $('btn-reset-draft');
const btnExportDraft = $('btn-export-draft');
const toast = $('copy-toast');
const headerMeta = $('header-meta');
const debugModal = $('debug-modal');
const debugOutput = $('debug-output');
const debugSearch = $('debug-search');
const aiContextModal = $('ai-context-modal');
const aiContextText = $('ai-context-text');
const metadataModal = $('metadata-modal');
const metadataTitleInput = $('metadata-title');
const metadataShortInput = $('metadata-short');
const metadataLongInput = $('metadata-long');
const btnMetadataSave = $('metadata-save');
const btnMetadataReset = $('metadata-reset');

let debugTables = [];
let dragSelection = null;

init();

function init() {
  wireDropZones();
  wireToolbar();
  wireModals();
  updateParseBtn();
  syncHideAutogenButton();
  updateDraftToolbar();
  aiContextText.textContent = AI_CONTEXT_PROMPT;
  viewModeSelect.value = state.viewMode;
  window.addEventListener('mouseup', () => { dragSelection = null; });
}

function wireDropZones() {
  ['xdf', 'bin', 'bin2'].forEach(key => {
    const zone = $(`drop-${key}`);
    const input = $(`input-${key}`);

    zone.addEventListener('click', event => {
      if (!event.target.closest('label')) {
        input.click();
      }
    });

    input.addEventListener('change', () => {
      if (input.files && input.files[0]) {
        setFile(key, input.files[0]);
      }
    });

    zone.addEventListener('dragover', event => {
      event.preventDefault();
      zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', event => {
      event.preventDefault();
      zone.classList.remove('drag-over');
      const file = event.dataTransfer.files[0];
      if (file) {
        setFile(key, file);
      }
    });
  });
}

function wireToolbar() {
  btnParse.addEventListener('click', doParse);
  btnDebug.addEventListener('click', doDebug);
  btnClear.addEventListener('click', resetAll);
  btnNewFiles.addEventListener('click', resetAll);

  searchInput.addEventListener('input', () => {
    state.search = searchInput.value.trim();
    searchClear.style.display = state.search ? '' : 'none';
    renderList();
  });

  searchClear.addEventListener('click', () => {
    searchInput.value = '';
    state.search = '';
    searchClear.style.display = 'none';
    renderList();
  });

  viewModeSelect.addEventListener('change', () => {
    state.viewMode = viewModeSelect.value;
    renderList();
  });

  btnHideAutogen.addEventListener('click', () => {
    state.hideAutogen = !state.hideAutogen;
    syncHideAutogenButton();
    renderList();
  });

  btnCollapseAll.addEventListener('click', () => {
    document.querySelectorAll('.table-card.open').forEach(node => node.classList.remove('open'));
    document.querySelectorAll('.group-section.open, .family-section.open').forEach(node => node.classList.remove('open'));
  });

  btnAiContext.addEventListener('click', () => {
    aiContextModal.style.display = 'flex';
  });

  btnCopyAiContext.addEventListener('click', () => copyText(AI_CONTEXT_PROMPT));
  btnExportBinTop.addEventListener('click', exportDraft);

  btnUndoDraft.addEventListener('click', undoDraft);
  btnResetDraft.addEventListener('click', resetDraft);
  btnExportDraft.addEventListener('click', exportDraft);
}

function wireModals() {
  $('debug-close').addEventListener('click', () => { debugModal.style.display = 'none'; });
  $('ai-context-close').addEventListener('click', () => { aiContextModal.style.display = 'none'; });
  $('metadata-close').addEventListener('click', closeMetadataModal);

  debugModal.addEventListener('click', event => {
    if (event.target === debugModal) {
      debugModal.style.display = 'none';
    }
  });

  aiContextModal.addEventListener('click', event => {
    if (event.target === aiContextModal) {
      aiContextModal.style.display = 'none';
    }
  });

  metadataModal.addEventListener('click', event => {
    if (event.target === metadataModal) {
      closeMetadataModal();
    }
  });

  btnMetadataSave.addEventListener('click', saveMetadataOverride);
  btnMetadataReset.addEventListener('click', clearMetadataOverride);

  debugSearch.addEventListener('input', () => renderDebugOutput(debugTables));
}

function setFile(key, file) {
  state.files[key] = file;
  $(`status-${key}`).textContent = `${file.name} (${formatSize(file.size)})`;
  $(`drop-${key}`).classList.add('has-file');
  hideError();
  updateParseBtn();
}

function updateParseBtn() {
  btnParse.disabled = !(state.files.xdf && state.files.bin);
  btnDebug.disabled = !state.files.xdf;
}

function syncHideAutogenButton() {
  btnHideAutogen.textContent = state.hideAutogen ? 'Autogen Hidden' : 'Autogen Visible';
  btnHideAutogen.classList.toggle('active', state.hideAutogen);
}

async function doParse() {
  hideError();
  showProgress(true);
  btnParse.disabled = true;

  const formData = new FormData();
  formData.append('xdf', state.files.xdf);
  formData.append('bin', state.files.bin);
  if (state.files.bin2) {
    formData.append('bin2', state.files.bin2);
  }

  try {
    let pct = 10;
    const tick = setInterval(() => {
      pct = Math.min(pct + 6, 90);
      setProgress(pct);
    }, 160);

    const response = await fetch('/api/parse', { method: 'POST', body: formData });
    clearInterval(tick);
    setProgress(100);

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Server error');
    }

    state.tables = data.tables || [];
    state.categories = data.categories || {};
    state.hasDiff = state.tables.some(table => table.has_diff);
    state.session = data.session || null;
    state.selections = {};
    state.previews = {};
    state.editorForms = {};
    state.axisDrafts = {};

    setTimeout(() => {
      showProgress(false);
      renderResults();
      updateParseBtn();
    }, 180);
  } catch (error) {
    showProgress(false);
    showError(error.message);
    updateParseBtn();
  }
}

async function doDebug() {
  const formData = new FormData();
  formData.append('xdf', state.files.xdf);
  if (state.files.bin) {
    formData.append('bin', state.files.bin);
  }

  try {
    debugOutput.textContent = 'Loading...';
    debugModal.style.display = 'flex';
    const response = await fetch('/api/debug', { method: 'POST', body: formData });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Debug failed');
    }
    debugTables = data.tables || [];
    renderDebugOutput(debugTables);
  } catch (error) {
    debugOutput.textContent = `Error: ${error.message}`;
  }
}

function renderDebugOutput(tables) {
  const query = debugSearch.value.trim().toLowerCase();
  const visible = query ? tables.filter(table => table.title.toLowerCase().includes(query)) : tables;
  const lines = [`${visible.length} / ${tables.length} tables shown`];

  visible.forEach((table, index) => {
    const outOfRange = String(table.sample_hex || '').includes('OUT OF RANGE');
    lines.push('');
    lines.push('-'.repeat(60));
    lines.push(`[${index}] ${table.title} (${table.type})`);
    lines.push(`family=${table.family_title || 'n/a'} variant=${table.variant_label || 'n/a'}`);
    lines.push(`workflow_bucket=${table.workflow_bucket || 'n/a'}`);
    lines.push(`category_path=${(table.primary_category_path || []).join(' > ') || 'Uncategorised'}`);
    lines.push(`base_offset=${table.base_offset} base_subtract=${table.base_subtract}`);
    lines.push(`z_address=${table.z_address_hex} -> file_offset=${table.z_file_addr_hex}${outOfRange ? ' OUT OF RANGE' : ''}`);
    lines.push(`z_rows=${table.z_rows} z_cols=${table.z_cols} z_bits=${table.z_bits} type_flags=${table.z_type_flags}`);
    lines.push(`major_stride=${table.z_major_stride} minor_stride=${table.z_minor_stride}`);
    lines.push(`math=${table.z_math}`);
    lines.push(`sample_hex=${table.sample_hex || 'n/a'}`);
  });

  debugOutput.textContent = lines.join('\n');
}

function renderResults() {
  uploadPanel.style.display = 'none';
  resultsArea.style.display = '';
  editorToolbar.style.display = '';
  btnClear.style.display = '';
  headerMeta.textContent = `${state.files.xdf?.name || ''} � ${state.files.bin?.name || ''} � ${state.tables.length} definitions`;
  viewModeSelect.value = state.viewMode;
  if (!state.selectedCategory) {
    state.selectedCategory = WORKFLOW_BUCKET_ORDER[0];
  }
  updateDraftToolbar();
  renderList();
}

function renderList() {
  state.openState = snapshotOpenState();
  const visibleState = getVisibleState();
  const visible = visibleState.visible;
  countBadge.textContent = buildCountLabel(visible.length, state.tables.length, visibleState.reasons);
  tableList.innerHTML = '';
  workspaceOverview.innerHTML = '';
  categoryTabs.innerHTML = '';
  mapTabs.innerHTML = '';
  categoryNav.innerHTML = '';
  mapPanel.style.display = state.viewMode === 'workflow' ? '' : 'none';
  categoryPanel.style.display = state.viewMode === 'workflow' ? '' : 'none';

  if (!visible.length) {
    tableList.innerHTML = '<div class="no-results"><div>No tables match your filter.</div></div>';
    renderInspector();
    return;
  }

  if (state.viewMode === 'flat') {
    renderFlatView(visible);
    ensureSelectedTable(visible);
    renderInspector(visible);
    return;
  }

  if (state.viewMode === 'hierarchy') {
    renderHierarchyView(visible);
    ensureSelectedTable(visible);
    renderInspector(visible);
    return;
  }

  renderWorkflowView(visible);
  renderInspector(visible);
}

function getVisibleState() {
  let visible = [...state.tables];
  const reasons = [];

  if (state.hideAutogen) {
    const before = visible.length;
    visible = visible.filter(table => !table.is_autogen);
    if (visible.length !== before) {
      reasons.push('autogen hidden');
    }
  }

  if (state.search) {
    const before = visible.length;
    const query = state.search.toLowerCase();
    visible = visible.filter(table => {
      return getDisplayTitle(table).toLowerCase().includes(query)
        || String(table.family_title || '').toLowerCase().includes(query)
        || String(table.variant_label || '').toLowerCase().includes(query)
        || String(getDisplayShortDescription(table) || '').toLowerCase().includes(query)
        || String(getDisplayLongDescription(table) || '').toLowerCase().includes(query)
        || String(table.workflow_bucket || '').toLowerCase().includes(query)
        || (table.primary_category_path || []).join(' ').toLowerCase().includes(query);
    });
    if (visible.length !== before) {
      reasons.push('search active');
    }
  }

  return { visible, reasons };
}

function buildCountLabel(visibleCount, totalCount, reasons) {
  const base = `${visibleCount} / ${totalCount} shown`;
  return reasons.length ? `${base} - ${reasons.join(' + ')}` : base;
}

function renderWorkflowView(tables) {
  const maps = getWorkflowMaps(tables);
  if (!maps.includes(state.selectedMap)) {
    state.selectedMap = maps[0] || 'Map 1';
  }

  renderMapTabs(maps, tables);

  const mapTables = tables.filter(table => tableBelongsToMap(table, state.selectedMap));
  const categories = getWorkflowCategories(mapTables);
  if (!categories.some(category => category.bucket === state.selectedCategory)) {
    state.selectedCategory = categories[0]?.bucket || WORKFLOW_BUCKET_ORDER[0];
  }

  renderCategoryNav(categories);
  renderCategoryTabs(categories);
  renderWorkflowOverview(mapTables, categories);

  const activeTables = mapTables
    .filter(table => (table.workflow_bucket || 'Other') === state.selectedCategory)
    .sort(compareTables);

  ensureSelectedTable(activeTables.length ? activeTables : mapTables);

  if (!activeTables.length) {
    tableList.innerHTML = '<div class="no-results"><div>No tables in this category for the selected map.</div></div>';
    return;
  }

  appendFamilyGroups(tableList, activeTables);
}

function getWorkflowMaps(tables) {
  const numbers = new Set(
    tables
      .map(table => extractVariantNumber(getTableMapVariant(table)))
      .filter(value => Number.isFinite(value) && value > 0 && value <= 4),
  );

  if (!numbers.size) {
    return ['Map 1'];
  }

  return [1, 2, 3, 4].map(number => `Map ${number}`);
}

function renderMapTabs(maps, tables) {
  mapTabs.innerHTML = '';
  maps.forEach(mapLabel => {
    const mapTables = tables.filter(table => tableBelongsToMap(table, mapLabel));
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `map-tab${mapLabel === state.selectedMap ? ' is-active' : ''}`;
    button.innerHTML = `
      <span>${escapeHtml(mapLabel)}</span>
      <span class="map-tab__meta">${mapTables.length} entries</span>`;
    button.addEventListener('click', () => {
      state.selectedMap = mapLabel;
      state.selectedCategory = null;
      renderList();
    });
    mapTabs.appendChild(button);
  });
}

function getWorkflowCategories(tables) {
  const grouped = new Map();
  tables.forEach(table => {
    const bucket = table.workflow_bucket || 'Other';
    if (!grouped.has(bucket)) {
      grouped.set(bucket, []);
    }
    grouped.get(bucket).push(table);
  });

  return Array.from(grouped.entries())
    .map(([bucket, bucketTables]) => ({
      bucket,
      tables: bucketTables.slice().sort(compareTables),
      familyCount: groupByFamily(bucketTables).length,
    }))
    .sort((a, b) => compareBuckets(a.bucket, b.bucket));
}

function renderCategoryNav(categories) {
  categoryNav.innerHTML = '';
  categories.forEach(category => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `category-nav__item${category.bucket === state.selectedCategory ? ' is-active' : ''}`;
    button.innerHTML = `
      <span>${escapeHtml(category.bucket)}</span>
      <span class="category-nav__meta">${category.tables.length}</span>`;
    button.addEventListener('click', () => {
      state.selectedCategory = category.bucket;
      renderList();
    });
    categoryNav.appendChild(button);
  });
}

function renderCategoryTabs(categories) {
  categoryTabs.innerHTML = '';
  categories.forEach(category => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `category-tab${category.bucket === state.selectedCategory ? ' is-active' : ''}`;
    button.innerHTML = `
      <span>${escapeHtml(shortBucketLabel(category.bucket))}</span>
      <span class="category-tab__count">${category.tables.length}</span>`;
    button.addEventListener('click', () => {
      state.selectedCategory = category.bucket;
      renderList();
    });
    categoryTabs.appendChild(button);
  });
}

function renderWorkflowOverview(mapTables, categories) {
  const familyCount = groupByFamily(mapTables).length;
  const globalCount = mapTables.filter(isGlobalTable).length;
  const dirtyCount = mapTables.filter(table => table.is_dirty).length;
  const generalTables = mapTables
    .filter(table => isGeneralMapSetting(table))
    .sort(compareTables)
    .slice(0, 5);
  const hotTables = mapTables
    .filter(table => !table.low_priority_reason && (table.workflow_bucket || 'Other') !== 'Quick Start')
    .sort(compareTables)
    .slice(0, 6);
  const quickStartTables = mapTables
    .filter(table => (table.workflow_bucket || 'Other') === 'Quick Start')
    .sort(compareTables)
    .slice(0, 6);

  workspaceOverview.innerHTML = `
    <div class="overview-card">
      <div class="overview-card__eyebrow">${escapeHtml(state.selectedMap)}</div>
      <div class="overview-card__title">General map settings</div>
      <div class="overview-summary">
        <div class="overview-stat">
          <span class="overview-stat__value">${mapTables.length}</span>
          <span class="overview-stat__label">Visible Tables</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${familyCount}</span>
          <span class="overview-stat__label">Families</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${globalCount}</span>
          <span class="overview-stat__label">Global Rows</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${dirtyCount}</span>
          <span class="overview-stat__label">Dirty Tables</span>
        </div>
      </div>
      <div class="overview-pills overview-pills--general"></div>
    </div>
    <div class="overview-card">
      <div class="overview-card__eyebrow">Quick Start</div>
      <div class="overview-card__title">Starter tables for a fresh map revision</div>
      <div class="overview-summary">
        <div class="overview-stat">
          <span class="overview-stat__value">${quickStartTables.length}</span>
          <span class="overview-stat__label">Pinned Tables</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${categories.find(category => category.bucket === 'Quick Start')?.tables.length || 0}</span>
          <span class="overview-stat__label">Quick Start Rows</span>
        </div>
      </div>
      <div class="overview-pills overview-pills--quick-start"></div>
    </div>
    <div class="overview-card">
      <div class="overview-card__eyebrow">Hot Tables</div>
      <div class="overview-card__title">Most useful for this map right now</div>
      <div class="overview-summary">
        ${categories.slice(0, 4).map(category => `
          <div class="overview-stat">
            <span class="overview-stat__value">${category.familyCount}</span>
            <span class="overview-stat__label">${escapeHtml(shortBucketLabel(category.bucket))}</span>
          </div>`).join('')}
      </div>
      <div class="overview-pills overview-pills--hot"></div>
    </div>`;

  const generalWrap = workspaceOverview.querySelector('.overview-pills--general');
  const quickStartWrap = workspaceOverview.querySelector('.overview-pills--quick-start');
  const hotWrap = workspaceOverview.querySelector('.overview-pills--hot');

  (generalTables.length ? generalTables : hotTables.slice(0, 4)).forEach(table => {
    generalWrap.appendChild(buildOverviewPill(table));
  });
  quickStartTables.forEach(table => {
    quickStartWrap.appendChild(buildOverviewPill(table));
  });
  hotTables.forEach(table => {
    hotWrap.appendChild(buildOverviewPill(table));
  });
}

function buildOverviewPill(table) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'overview-pill';
  button.innerHTML = `${getOverviewMapBadge(table)}<span>${escapeHtml(getDisplayTitle(table))}</span>`;
  button.addEventListener('click', () => {
    state.selectedCategory = table.workflow_bucket || state.selectedCategory;
    selectTable(table.table_id);
  });
  return button;
}

function tableBelongsToMap(table, mapLabel) {
  const mapNumber = extractVariantNumber(mapLabel);
  const tableMapNumber = extractVariantNumber(getTableMapVariant(table));
  return tableMapNumber === mapNumber || isGlobalTable(table);
}

function isGlobalTable(table) {
  return Boolean(table.is_shared_global);
}

function isInferredMapTable(table) {
  return Boolean(table.map_inferred && getTableMapVariant(table));
}

function getMapStatusBadge(table) {
  if (isGlobalTable(table)) {
    return '<span class="badge-global">global</span>';
  }
  if (isInferredMapTable(table)) {
    return `<span class="badge-inferred">${escapeHtml(getTableMapVariant(table))} (Inferred)</span>`;
  }
  return '';
}

function getOverviewMapBadge(table) {
  if (isGlobalTable(table)) {
    return '<span class="overview-pill__badge">Global</span>';
  }
  if (isInferredMapTable(table)) {
    return `<span class="overview-pill__badge overview-pill__badge--inferred">${escapeHtml(getTableMapVariant(table))} (Inferred)</span>`;
  }
  return '';
}

function isGeneralMapSetting(table) {
  const text = `${table.workflow_bucket || ''} ${table.title || ''} ${table.family_title || ''}`.toLowerCase();
  if (isGlobalTable(table) || (table.workflow_bucket || '') === 'Map Switching / Multi-map') {
    return true;
  }
  return ['ethanol', 'switch', 'slot', 'map', 'flex', 'global'].some(keyword => text.includes(keyword));
}

function shortBucketLabel(bucket) {
  return String(bucket)
    .replace('Quick Start', 'Quick Start')
    .replace(' / WGDC', '')
    .replace(' / Ethanol', '')
    .replace(' / Limits', '')
    .replace(' / Fuel System', '');
}

function getTableMapVariant(table) {
  return table.effective_map_variant || table.map_variant || null;
}

function ensureSelectedTable(tables) {
  const available = new Set((tables || []).map(table => table.table_id));
  if (state.selectedTableId && available.has(state.selectedTableId)) {
    return;
  }
  state.selectedTableId = tables[0]?.table_id || null;
  const selected = tables.find(table => table.table_id === state.selectedTableId);
  if (selected && isScalarTable(selected) && !state.selections[selected.table_id]) {
    state.selections[selected.table_id] = {
      row_start: 0,
      row_end: Math.max(selected.rows - 1, 0),
      col_start: 0,
      col_end: Math.max(selected.cols - 1, 0),
    };
  }
}

function selectTable(tableId) {
  state.selectedTableId = tableId;
  const table = state.tables.find(item => item.table_id === tableId);
  if (table && isScalarTable(table) && !state.selections[tableId]) {
    state.selections[tableId] = {
      row_start: 0,
      row_end: Math.max(table.rows - 1, 0),
      col_start: 0,
      col_end: Math.max(table.cols - 1, 0),
    };
  }
  renderList();
}

function updateDraftToolbar() {
  const session = state.session;
  const pending = Number(session?.pending_change_count || 0);
  const dirty = Number(session?.dirty_table_count || 0);
  const exportReady = Boolean(session?.export_ready);

  if (!session) {
    draftBadge.textContent = 'Draft Unloaded';
    draftMeta.textContent = '0 pending edits � 0 dirty tables';
    btnUndoDraft.disabled = true;
    btnResetDraft.disabled = true;
    btnExportDraft.disabled = true;
    btnExportBinTop.disabled = true;
    return;
  }

  draftBadge.textContent = exportReady ? 'Draft Dirty' : 'Draft Clean';
  draftBadge.classList.toggle('is-dirty', exportReady);
  draftMeta.textContent = `${pending} pending edits · ${dirty} dirty tables`;
  btnUndoDraft.disabled = pending === 0;
  btnResetDraft.disabled = pending === 0;
  btnExportDraft.disabled = !exportReady;
  btnExportBinTop.disabled = !exportReady;
}

function snapshotOpenState() {
  return {
    cards: new Set(Array.from(document.querySelectorAll('.table-card.open')).map(node => node.dataset.tableId)),
    groups: new Set(Array.from(document.querySelectorAll('.group-section.open')).map(node => node.dataset.groupKey)),
    families: new Set(Array.from(document.querySelectorAll('.family-section.open')).map(node => node.dataset.familyKey)),
  };
}

function shouldOpen(kind, key, defaultOpen) {
  const bucket = state.openState?.[kind];
  if (bucket && bucket.has(String(key))) {
    return true;
  }
  return defaultOpen;
}

function getMetadataKey(table) {
  return `${state.session?.source_xdf_name || state.files.xdf?.name || 'xdf'}::${table.table_id}`;
}

function loadMetadataOverrides() {
  try {
    const raw = window.localStorage.getItem(METADATA_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    return {};
  }
}

function saveMetadataOverrides() {
  window.localStorage.setItem(METADATA_STORAGE_KEY, JSON.stringify(state.metadataOverrides));
}

function getTableOverride(table) {
  return state.metadataOverrides[getMetadataKey(table)] || {};
}

function getDisplayTitle(table) {
  const override = getTableOverride(table);
  return Object.prototype.hasOwnProperty.call(override, 'title') ? override.title : table.title;
}

function getDisplayShortDescription(table) {
  const override = getTableOverride(table);
  if (Object.prototype.hasOwnProperty.call(override, 'short')) {
    return override.short;
  }
  return table.description_short || table.description || '';
}

function getDisplayLongDescription(table) {
  const override = getTableOverride(table);
  if (Object.prototype.hasOwnProperty.call(override, 'long')) {
    return override.long;
  }
  return table.description_long || '';
}

function renderFlatView(tables) {
  renderAlternateWorkspaceChrome('Flat List', tables, 'Priority-ranked families and tables without map segmentation.');
  appendFamilyGroups(tableList, tables.slice().sort(compareTables));
}

function renderHierarchyView(tables) {
  renderAlternateWorkspaceChrome('TunerPro Hierarchy', tables, 'Raw XDF folder and subfolder organization with the new cockpit shell.');
  const root = createNode('All Tables');
  tables.forEach(table => {
    const path = Array.isArray(table.primary_category_path) && table.primary_category_path.length
      ? table.primary_category_path
      : ['Uncategorised'];
    let cursor = root;
    path.forEach(segment => {
      if (!cursor.children.has(segment)) {
        const child = createNode(segment);
        child.path = [...cursor.path, segment];
        cursor.children.set(segment, child);
      }
      cursor = cursor.children.get(segment);
    });
    cursor.tables.push(table);
  });

  Array.from(root.children.values())
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach(node => tableList.appendChild(renderHierarchyNode(node, 0)));
}

function renderAlternateWorkspaceChrome(title, tables, description) {
  workspaceOverview.innerHTML = `
    <div class="overview-card">
      <div class="overview-card__eyebrow">${escapeHtml(title)}</div>
      <div class="overview-card__title">Alternate browsing mode</div>
      <div class="overview-summary">
        <div class="overview-stat">
          <span class="overview-stat__value">${tables.length}</span>
          <span class="overview-stat__label">Visible Tables</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${groupByFamily(tables).length}</span>
          <span class="overview-stat__label">Families</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${tables.filter(table => table.is_dirty).length}</span>
          <span class="overview-stat__label">Dirty Tables</span>
        </div>
      </div>
      <div class="desc-short">${escapeHtml(description)}</div>
    </div>`;
}

function createNode(label) {
  return { label, children: new Map(), tables: [], path: [] };
}

function renderHierarchyNode(node, depth) {
  const count = countNodeTables(node);
  const lowPriority = nodeIsLowPriority(node);
  const open = depth === 0 || (!lowPriority && count <= 14);
  const section = buildGroupSection({
    key: `hierarchy:${node.path.join('/') || node.label}`,
    label: node.label,
    count,
    depth,
    open,
    lowPriority,
  });
  const body = section.querySelector('.group-body');

  Array.from(node.children.values())
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach(child => body.appendChild(renderHierarchyNode(child, depth + 1)));

  appendFamilyGroups(body, node.tables.slice().sort(compareTables));
  return section;
}

function appendFamilyGroups(container, tables) {
  const families = groupByFamily(tables);
  families.forEach(group => {
    if (shouldRenderAsFamily(group)) {
      container.appendChild(buildFamilySection(group));
    } else {
      container.appendChild(buildCard(group.tables[0], { titleOverride: group.tables[0].title }));
    }
  });
}

function groupByFamily(tables) {
  const grouped = new Map();
  tables.forEach(table => {
    const key = table.family_title || table.title;
    if (!grouped.has(key)) {
      grouped.set(key, []);
    }
    grouped.get(key).push(table);
  });

  return Array.from(grouped.entries())
    .map(([familyTitle, members]) => {
      const sorted = members.slice().sort(compareTablesWithinFamily);
      return {
        familyTitle,
        tables: sorted,
        representative: findRepresentative(sorted),
      };
    })
    .sort((a, b) => compareTables(a.representative, b.representative) || a.familyTitle.localeCompare(b.familyTitle));
}

function shouldRenderAsFamily(group) {
  return group.tables.length > 1 || Boolean(group.tables[0].variant_label);
}

function buildFamilySection(group) {
  const section = document.createElement('section');
  const familyKey = `family:${group.familyTitle}`;
  const isSelected = state.selectedTableId === group.representative.table_id;
  section.className = `family-section${shouldOpen('families', familyKey, false) ? ' open' : ''}${isSelected ? ' is-selected' : ''}`;
  section.dataset.familyKey = familyKey;
  section.innerHTML = `
    <div class="family-header" role="button" tabindex="0">
      <button class="family-expander" type="button" aria-label="Toggle family">
        <svg class="family-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
      <span class="card-type-badge badge-family">family</span>
      <span class="family-title">${escapeHtml(group.familyTitle)}</span>
      ${group.tables.some(isGlobalTable) ? '<span class="badge-global">global</span>' : group.tables.some(isInferredMapTable) ? `<span class="badge-inferred">${escapeHtml(getTableMapVariant(group.tables.find(isInferredMapTable)))} (Inferred)</span>` : ''}
      <span class="family-count">${group.tables.length}</span>
    </div>
    <div class="family-body"></div>`;

  section.querySelector('.family-header').addEventListener('click', event => {
    if (event.target.closest('.family-expander')) {
      return;
    }
    selectTable(group.representative.table_id);
  });

  section.querySelector('.family-expander').addEventListener('click', event => {
    event.stopPropagation();
    section.classList.toggle('open');
  });

  const body = section.querySelector('.family-body');
  appendFamilyDescription(body, group.representative, false);
  group.tables.forEach(table => {
    body.appendChild(buildCard(table, { titleOverride: table.variant_label || table.title }));
  });
  return section;
}

function appendFamilyDescription(container, representative, workflowHeader) {
  const shortText = representative.family_description_short || '';
  const longText = representative.family_description_long || '';
  if (!shortText && !longText) {
    return;
  }

  const block = document.createElement('div');
  block.className = workflowHeader ? 'group-description' : 'family-description';
  const pieces = [];
  if (shortText) {
    pieces.push(`<div class="desc-short">${escapeHtml(shortText)}</div>`);
  }
  if (longText) {
    pieces.push(`<div class="desc-long">${escapeHtml(longText)}</div>`);
  }
  block.innerHTML = pieces.join('');
  container.appendChild(block);
}

function buildGroupSection({ key, label, count, depth = 0, open = true, lowPriority = false }) {
  const section = document.createElement('section');
  section.className = `group-section${shouldOpen('groups', key, open) ? ' open' : ''}${lowPriority ? ' group-section--low' : ''}`;
  section.dataset.groupKey = key;
  section.innerHTML = `
    <button class="group-header" type="button" style="--group-depth:${depth}">
      <span class="group-chevron">></span>
      <span class="group-title">${escapeHtml(label)}</span>
      <span class="group-count">${count}</span>
    </button>
    <div class="group-body"></div>`;

  section.querySelector('.group-header').addEventListener('click', () => {
    section.classList.toggle('open');
  });

  return section;
}

function countNodeTables(node) {
  let count = node.tables.length;
  node.children.forEach(child => {
    count += countNodeTables(child);
  });
  return count;
}

function nodeIsLowPriority(node) {
  const ownTablesLow = node.tables.length > 0 && node.tables.every(table => Boolean(table.low_priority_reason));
  if (node.children.size === 0) {
    return ownTablesLow;
  }
  let childLow = true;
  node.children.forEach(child => {
    childLow = childLow && nodeIsLowPriority(child);
  });
  return ownTablesLow || childLow;
}

function compareBuckets(a, b) {
  const safeA = WORKFLOW_BUCKET_ORDER.includes(a) ? WORKFLOW_BUCKET_ORDER.indexOf(a) : WORKFLOW_BUCKET_ORDER.length;
  const safeB = WORKFLOW_BUCKET_ORDER.includes(b) ? WORKFLOW_BUCKET_ORDER.indexOf(b) : WORKFLOW_BUCKET_ORDER.length;
  return safeA - safeB || a.localeCompare(b);
}

function compareTables(a, b) {
  const scoreDiff = (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0);
  if (scoreDiff) return scoreDiff;
  const familyDiff = String(a.family_title || a.title).localeCompare(String(b.family_title || b.title));
  if (familyDiff) return familyDiff;
  const variantDiff = String(a.variant_label || a.title).localeCompare(String(b.variant_label || b.title));
  if (variantDiff) return variantDiff;
  return a.title.localeCompare(b.title);
}

function compareTablesWithinFamily(a, b) {
  const mapDiff = extractVariantNumber(a.map_variant) - extractVariantNumber(b.map_variant);
  if (mapDiff) return mapDiff;
  const pathDiff = extractVariantNumber(a.path_variant) - extractVariantNumber(b.path_variant);
  if (pathDiff) return pathDiff;
  const modeDiff = extractVariantNumber(a.mode_variant) - extractVariantNumber(b.mode_variant);
  if (modeDiff) return modeDiff;
  return String(a.variant_label || a.title).localeCompare(String(b.variant_label || b.title));
}

function extractVariantNumber(value) {
  if (!value) return 0;
  const match = String(value).match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

function findRepresentative(tables) {
  return tables.slice().sort(compareTables)[0];
}

function buildCard(table, { titleOverride = null } = {}) {
  const card = document.createElement('article');
  const shouldBeOpen = shouldOpen('cards', table.table_id, Boolean(state.previews[table.table_id]));
  const isSelected = state.selectedTableId === table.table_id;
  card.className = `table-card${table.error ? ' has-error' : ''}${shouldBeOpen ? ' open' : ''}${isSelected ? ' is-selected' : ''}`;
  card.dataset.tableId = table.table_id;

  const badgeClass = table.error ? 'badge-error' : table.type === 'constant' ? 'badge-constant' : 'badge-table';
  const badgeText = table.error ? 'error' : table.type === 'constant' ? 'scalar' : 'table';
  const dims = isScalarTable(table) ? '' : `${table.rows}x${table.cols}`;
  const units = table.units ? `[${table.units}]` : '';
  const changeBadge = table.change_count ? `<span class="card-metric" title="Logged edits">${table.change_count} edits</span>` : '';
  const customTitle = getDisplayTitle(table);
  const displayTitle = customTitle !== table.title ? customTitle : (titleOverride || customTitle);
  const dirtyBadge = table.is_dirty ? '<span class="badge-draft">draft</span>' : '';
  const mapStatusBadge = getMapStatusBadge(table);

  card.innerHTML = `
    <div class="card-header">
      <button class="card-expander" type="button" aria-label="Toggle table">
        <svg class="card-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
      <span class="card-type-badge ${badgeClass}">${badgeText}</span>
      <span class="card-title" title="${escapeHtml(customTitle)}">${escapeHtml(displayTitle)}</span>
      ${mapStatusBadge}
      ${table.low_priority_reason ? '<span class="badge-autogen">low priority</span>' : ''}
      ${table.is_autogen ? '<span class="badge-autogen">autogen</span>' : ''}
      ${dirtyBadge}
      ${changeBadge}
      ${units ? `<span class="card-units">${escapeHtml(units)}</span>` : ''}
      ${dims ? `<span class="card-dims">${dims}</span>` : ''}
      <button class="card-edit-meta" type="button" title="Edit table name and descriptions">edit</button>
      <button class="btn-copy card-copy" type="button" title="Copy for AI">Copy for AI</button>
    </div>
    <div class="card-body">
      ${buildBody(table, { includeAxisEditors: false })}
    </div>`;

  card.querySelector('.card-header').addEventListener('click', event => {
    if (event.target.closest('.card-expander, .card-copy, .card-edit-meta')) {
      return;
    }
    selectTable(table.table_id);
  });

  card.querySelector('.card-expander').addEventListener('click', event => {
    event.stopPropagation();
    card.classList.toggle('open');
  });

  card.querySelector('.card-copy').addEventListener('click', event => {
    event.stopPropagation();
    copyForAI(table);
  });

  card.querySelector('.card-edit-meta').addEventListener('click', event => {
    event.stopPropagation();
    openMetadataModal(table.table_id);
  });

  if (table.editable && !table.error) {
    wireEditableCard(card, table);
  }

  return card;
}

function renderInspector(visibleTables = []) {
  const selected = visibleTables.find(table => table.table_id === state.selectedTableId)
    || state.tables.find(table => table.table_id === state.selectedTableId)
    || null;

  if (!selected) {
    inspectorEmpty.style.display = '';
    inspectorPanel.style.display = 'none';
    inspectorHeader.innerHTML = '';
    inspectorBody.innerHTML = '';
    return;
  }

  inspectorEmpty.style.display = 'none';
  inspectorPanel.style.display = '';

  const badgeClass = selected.error ? 'badge-error' : selected.type === 'constant' ? 'badge-constant' : 'badge-table';
  const badgeText = selected.error ? 'error' : selected.type === 'constant' ? 'scalar' : 'table';
  const units = selected.units ? `[${selected.units}]` : '';
  const dims = isScalarTable(selected) ? 'Scalar' : `${selected.rows}x${selected.cols}`;
  const mapStatusBadge = getMapStatusBadge(selected);
  const dirtyBadge = selected.is_dirty ? '<span class="badge-draft">draft</span>' : '';
  const variantMeta = selected.variant_label ? `<span>${escapeHtml(selected.variant_label)}</span>` : '';
  const categoryMeta = selected.workflow_bucket ? `<span>${escapeHtml(selected.workflow_bucket)}</span>` : '';
  const changeMeta = selected.change_count ? `<span>${selected.change_count} edits</span>` : '';

  inspectorHeader.innerHTML = `
    <div class="inspector-panel__title">
      <span class="card-type-badge ${badgeClass}">${badgeText}</span>
      <h3>${escapeHtml(getDisplayTitle(selected))}</h3>
      ${mapStatusBadge}
      ${dirtyBadge}
    </div>
    <div class="inspector-panel__meta">
      ${units ? `<span>${escapeHtml(units)}</span>` : ''}
      <span>${dims}</span>
      ${variantMeta}
      ${categoryMeta}
      ${changeMeta}
    </div>
    <div class="inspector-panel__actions">
      <button class="btn btn-ghost btn-sm inspector-copy" type="button">Copy for AI</button>
      <button class="btn btn-ghost btn-sm inspector-meta" type="button">Edit Metadata</button>
      <button class="btn btn-ghost btn-sm inspector-ai" type="button">AI Context</button>
    </div>`;

  const descriptionMarkup = buildDescriptionBlock(selected) || '<div class="desc-short">No description available yet for this table.</div>';
  const editMarkup = selected.editable && !selected.error
    ? buildEditorPanel(selected)
    : '<div class="desc-short">This table is currently read-only.</div>';
  const axisMarkup = buildAxisEditors(selected) || '<div class="desc-short">No editable embedded breakpoints are available for this table.</div>';
  const familyMeta = selected.family_title && selected.family_title !== selected.title
    ? `<div class="desc-short">Family: ${escapeHtml(selected.family_title)}</div>`
    : '';
  const categoryPath = (selected.primary_category_path || []).length
    ? `<div class="desc-long">${escapeHtml((selected.primary_category_path || []).join(' � '))}</div>`
    : '';

  inspectorBody.innerHTML = `
    <section class="inspector-section">
      <div class="inspector-section__title">Description</div>
      ${descriptionMarkup}
    </section>
    <section class="inspector-section">
      <div class="inspector-section__title">Editing</div>
      ${editMarkup}
    </section>
    <section class="inspector-section">
      <div class="inspector-section__title">Axis Tools</div>
      ${axisMarkup}
    </section>
    <section class="inspector-section">
      <div class="inspector-section__title">Context</div>
      ${familyMeta}
      <div class="desc-short">Bucket: ${escapeHtml(selected.workflow_bucket || 'Other')}</div>
      ${categoryPath}
    </section>`;

  inspectorHeader.querySelector('.inspector-copy').addEventListener('click', () => copyForAI(selected));
  inspectorHeader.querySelector('.inspector-meta').addEventListener('click', () => openMetadataModal(selected.table_id));
  inspectorHeader.querySelector('.inspector-ai').addEventListener('click', () => {
    aiContextModal.style.display = 'flex';
  });

  if (selected.editable && !selected.error) {
    wireEditableScope(inspectorPanel, selected, { hasCellGrid: false });
  }
}

function isScalarTable(table) {
  return table.type === 'constant' || (table.rows === 1 && table.cols === 1);
}

function buildDescriptionBlock(table) {
  const shortText = getDisplayShortDescription(table);
  const longText = getDisplayLongDescription(table);
  if (!shortText && !longText) {
    return '';
  }

  const parts = [];
  if (shortText) {
    parts.push(`<div class="desc-short">${escapeHtml(shortText)}</div>`);
  }
  if (longText) {
    parts.push(`<div class="desc-long">${escapeHtml(longText)}</div>`);
  }
  return `<div class="card-desc">${parts.join('')}</div>`;
}

function buildEditorPanel(table) {
  if (!table.editable || table.error) {
    return '';
  }

  const selection = state.selections[table.table_id];
  const preview = state.previews[table.table_id];
  const formState = state.editorForms[table.table_id] || { operation: 'add_constant', value: 1 };
  const showValue = !String(formState.operation).startsWith('interpolate');
  const previewSummary = preview ? `
    <div class="editor-preview-copy">
      ${preview.summary.changed_cell_count} cells � delta ${formatNumber(preview.summary.min_delta)} to ${formatNumber(preview.summary.max_delta)}
    </div>
    ${(preview.changes || []).slice(0, 6).map(change => `
      <div class="editor-preview-line">
        R${change.row + 1} C${change.col + 1}: ${formatNumber(change.before)} -> ${formatNumber(change.after)}
      </div>`).join('')}
    ${preview.changes_truncated ? '<div class="editor-preview-line">…more cells omitted</div>' : ''}
  ` : '<div class="editor-preview-copy">Select a region, then preview or apply an operation.</div>';

  return `
    <div class="editor-panel" data-table-id="${escapeHtml(table.table_id)}">
      <div class="editor-row">
        <span class="selection-chip">${escapeHtml(formatSelectionLabel(selection, table))}</span>
        <button class="editor-mini-btn" type="button" data-editor-action="select-all">Select All</button>
        <button class="editor-mini-btn" type="button" data-editor-action="clear-selection">Clear</button>
      </div>
      <div class="editor-row">
        <label class="editor-control">
          <span>Operation</span>
          <select class="editor-operation">
            ${buildOperationOptions(formState.operation)}
          </select>
        </label>
        <label class="editor-control editor-value-wrap"${showValue ? '' : ' style="display:none"'}>
          <span>Value</span>
          <input class="editor-value" type="number" step="any" value="${escapeHtml(String(formState.value ?? 1))}" />
        </label>
        <button class="btn btn-ghost btn-sm editor-preview-btn" type="button">Preview</button>
        <button class="btn btn-primary btn-sm editor-apply-btn" type="button">Apply to Draft</button>
      </div>
      <div class="editor-preview">${previewSummary}</div>
    </div>`;
}

function buildBody(table, { includeAxisEditors = true } = {}) {
  if (table.error) {
    return `<div class="card-error-msg">Error: ${escapeHtml(table.error)}</div>`;
  }
  if (!table.rows) {
    return '<div class="card-error-msg">No data extracted.</div>';
  }
  const axisEditors = includeAxisEditors ? buildAxisEditors(table) : '';
  if (isScalarTable(table)) {
    return `${axisEditors}${buildScalar(table)}`;
  }
  return `${axisEditors}${buildTable(table)}`;
}

function buildScalar(table) {
  const first = formatNumber(table.grid?.[0]?.[0] ?? 0);
  const second = table.has_diff ? formatNumber(table.grid2?.[0]?.[0] ?? 0) : null;
  if (second === null || first === second) {
    return `<div class="scalar-display"><span class="scalar-value">${first}</span>${table.units ? `<span class="scalar-units">${escapeHtml(table.units)}</span>` : ''}</div>`;
  }
  return `<div class="scalar-display"><span class="scalar-value">${first}</span>${table.units ? `<span class="scalar-units">${escapeHtml(table.units)}</span>` : ''}<span class="scalar-diff"><span class="arrow">-></span><span class="new-val">${second}</span></span></div>`;
}

function buildTable(table) {
  const { grid, grid2, x_labels: xLabels, y_labels: yLabels, rows, cols, has_diff: hasDiff } = table;
  const hasX = Array.isArray(xLabels) && xLabels.length > 0;
  const hasY = Array.isArray(yLabels) && yLabels.length > 0;
  const selection = state.selections[table.table_id];
  const previewLookup = buildPreviewLookup(table.table_id);
  let html = '<div class="table-scroll"><table class="data-table"><thead><tr>';
  html += `<th class="row-header">${hasY ? 'Y / X' : ''}</th>`;

  for (let col = 0; col < cols; col += 1) {
    const label = hasX && xLabels[col] !== undefined ? formatNumber(xLabels[col]) : `C${col}`;
    html += `<th>${escapeHtml(label)}</th>`;
  }

  html += '</tr></thead><tbody>';

  for (let row = 0; row < rows; row += 1) {
    const rowLabel = hasY && yLabels[row] !== undefined ? formatNumber(yLabels[row]) : `R${row}`;
    html += `<tr><td class="row-label">${escapeHtml(rowLabel)}</td>`;
    for (let col = 0; col < cols; col += 1) {
      const current = formatNumber((grid[row] || [])[col] ?? 0);
      const cellClasses = ['table-cell'];
      const heatmapStyle = buildHeatmapStyle(table, row, col);
      if (selectionContains(selection, row, col)) {
        cellClasses.push('is-selected');
      }
      if (previewLookup.has(`${row}:${col}`)) {
        cellClasses.push('is-preview');
      }
      if (hasDiff && grid2) {
        const other = formatNumber((grid2[row] || [])[col] ?? 0);
        if (current !== other) {
          cellClasses.push('diff-changed');
          html += `<td class="${cellClasses.join(' ')}" data-row="${row}" data-col="${col}" style="${heatmapStyle}"><span class="v1">${escapeHtml(current)}</span><span class="v2">${escapeHtml(other)}</span></td>`;
        } else {
          html += `<td class="${cellClasses.join(' ')}" data-row="${row}" data-col="${col}" style="${heatmapStyle}">${escapeHtml(current)}</td>`;
        }
      } else {
        html += `<td class="${cellClasses.join(' ')}" data-row="${row}" data-col="${col}" style="${heatmapStyle}">${escapeHtml(current)}</td>`;
      }
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

function buildOperationOptions(selectedOperation) {
  const options = [
    ['set_cells', 'Set Cells'],
    ['add_constant', 'Add Constant'],
    ['multiply', 'Multiply'],
    ['interpolate_x', 'Interpolate X'],
    ['interpolate_y', 'Interpolate Y'],
    ['interpolate_xy', 'Interpolate XY'],
  ];
  return options.map(([value, label]) => (
    `<option value="${value}"${value === selectedOperation ? ' selected' : ''}>${label}</option>`
  )).join('');
}

function buildAxisEditors(table) {
  const xEditor = buildAxisEditor(table, 'x');
  const yEditor = buildAxisEditor(table, 'y');
  if (!xEditor && !yEditor) {
    return '';
  }
  return `<div class="axis-editor-wrap">${xEditor}${yEditor}</div>`;
}

function buildAxisEditor(table, axisName) {
  const editable = axisName === 'x' ? table.x_axis_editable : table.y_axis_editable;
  const values = axisName === 'x' ? (table.x_labels || []) : (table.y_labels || []);
  if (!editable || !values.length) {
    return '';
  }
  const draftValues = getAxisDraft(table, axisName);
  const axisLabel = axisName.toUpperCase();
  return `
    <div class="axis-editor" data-axis="${axisName}">
      <div class="axis-editor__header">
        <span class="axis-editor__title">${axisLabel} Axis Breakpoints</span>
        <button class="editor-mini-btn" type="button" data-axis-action="reset" data-axis-name="${axisName}">Reset ${axisLabel}</button>
        <button class="btn btn-ghost btn-sm" type="button" data-axis-action="apply" data-axis-name="${axisName}">Apply ${axisLabel} Axis</button>
      </div>
      <div class="axis-editor__inputs">
        ${draftValues.map((value, index) => `
          <label class="axis-editor__item">
            <span>${axisLabel}${index + 1}</span>
            <input type="number" step="any" class="axis-input" data-axis-name="${axisName}" data-axis-index="${index}" value="${escapeHtml(String(value))}" />
          </label>
        `).join('')}
      </div>
    </div>`;
}

function getAxisDraft(table, axisName) {
  if (!state.axisDrafts[table.table_id]) {
    state.axisDrafts[table.table_id] = {};
  }
  if (!state.axisDrafts[table.table_id][axisName]) {
    state.axisDrafts[table.table_id][axisName] = [...(axisName === 'x' ? (table.x_labels || []) : (table.y_labels || []))];
  }
  return state.axisDrafts[table.table_id][axisName];
}

function formatSelectionLabel(selection, table) {
  if (!selection) {
    return 'Selection: none';
  }
  return `Selection: R${selection.row_start + 1}-${selection.row_end + 1} � C${selection.col_start + 1}-${selection.col_end + 1} of ${table.rows}x${table.cols}`;
}

function buildPreviewLookup(tableId) {
  const preview = state.previews[tableId];
  const lookup = new Set();
  if (!preview) {
    return lookup;
  }
  (preview.changes || []).forEach(change => lookup.add(`${change.row}:${change.col}`));
  return lookup;
}

function buildHeatmapStyle(table, row, col) {
  const stats = getGridStats(table.grid || []);
  if (!stats || stats.max <= stats.min) {
    return '';
  }
  const value = Number((table.grid[row] || [])[col]);
  if (!Number.isFinite(value)) {
    return '';
  }
  const ratio = Math.max(0, Math.min(1, (value - stats.min) / (stats.max - stats.min)));
  const hue = 120 - (ratio * 120);
  const alpha = 0.2 + (ratio * 0.18);
  return `background: linear-gradient(180deg, hsla(${hue}, 72%, 45%, ${alpha}) 0%, hsla(${hue}, 70%, 32%, ${alpha * 0.95}) 100%);`;
}

function getGridStats(grid) {
  const values = [];
  (grid || []).forEach(row => {
    (row || []).forEach(value => {
      if (Number.isFinite(Number(value))) {
        values.push(Number(value));
      }
    });
  });
  if (!values.length) {
    return null;
  }
  return {
    min: Math.min(...values),
    max: Math.max(...values),
  };
}

function selectionContains(selection, row, col) {
  if (!selection) {
    return false;
  }
  return row >= selection.row_start
    && row <= selection.row_end
    && col >= selection.col_start
    && col <= selection.col_end;
}

function wireEditableCard(card, table) {
  const tableId = table.table_id;
  wireEditableScope(card, table, { hasCellGrid: true });

  card.querySelectorAll('.table-cell').forEach(cell => {
    cell.addEventListener('mousedown', event => {
      if (event.button !== 0) {
        return;
      }
      const row = Number(cell.dataset.row);
      const col = Number(cell.dataset.col);
      dragSelection = { tableId, anchorRow: row, anchorCol: col };
      state.selections[tableId] = { row_start: row, row_end: row, col_start: col, col_end: col };
      state.selectedTableId = tableId;
      delete state.previews[tableId];
      refreshEditableCard(card, table);
      event.preventDefault();
    });

    cell.addEventListener('mouseenter', () => {
      if (!dragSelection || dragSelection.tableId !== tableId) {
        return;
      }
      const row = Number(cell.dataset.row);
      const col = Number(cell.dataset.col);
      state.selections[tableId] = {
        row_start: Math.min(dragSelection.anchorRow, row),
        row_end: Math.max(dragSelection.anchorRow, row),
        col_start: Math.min(dragSelection.anchorCol, col),
        col_end: Math.max(dragSelection.anchorCol, col),
      };
      delete state.previews[tableId];
      refreshEditableCard(card, table);
    });
  });
}

function wireEditableScope(container, table, { hasCellGrid = false } = {}) {
  const tableId = table.table_id;
  const panel = container.querySelector('.editor-panel');

  if (panel) {
    const operationSelect = panel.querySelector('.editor-operation');
    const valueWrap = panel.querySelector('.editor-value-wrap');
    const valueInput = panel.querySelector('.editor-value');

    const syncFormState = () => {
      const value = valueInput ? valueInput.value : '';
      state.editorForms[tableId] = {
        operation: operationSelect.value,
        value,
      };
      valueWrap.style.display = operationSelect.value.startsWith('interpolate') ? 'none' : '';
    };

    syncFormState();
    operationSelect.addEventListener('change', syncFormState);
    if (valueInput) {
      valueInput.addEventListener('input', syncFormState);
    }

    panel.querySelector('[data-editor-action="select-all"]').addEventListener('click', event => {
      event.stopPropagation();
      state.selections[tableId] = {
        row_start: 0,
        row_end: Math.max(table.rows - 1, 0),
        col_start: 0,
        col_end: Math.max(table.cols - 1, 0),
      };
      delete state.previews[tableId];
      if (hasCellGrid) {
        refreshEditableCard(container, table);
      } else {
        renderList();
      }
    });

    panel.querySelector('[data-editor-action="clear-selection"]').addEventListener('click', event => {
      event.stopPropagation();
      delete state.selections[tableId];
      delete state.previews[tableId];
      if (hasCellGrid) {
        refreshEditableCard(container, table);
      } else {
        renderList();
      }
    });

    panel.querySelector('.editor-preview-btn').addEventListener('click', async event => {
      event.stopPropagation();
      await runEditorAction(tableId, true);
    });

    panel.querySelector('.editor-apply-btn').addEventListener('click', async event => {
      event.stopPropagation();
      await runEditorAction(tableId, false);
    });
  }

  container.querySelectorAll('.axis-input').forEach(input => {
    input.addEventListener('input', event => {
      const axisName = event.target.dataset.axisName;
      const index = Number(event.target.dataset.axisIndex);
      const values = [...getAxisDraft(table, axisName)];
      values[index] = Number(event.target.value);
      state.axisDrafts[tableId][axisName] = values;
    });
  });

  container.querySelectorAll('[data-axis-action="reset"]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      const axisName = button.dataset.axisName;
      state.axisDrafts[tableId][axisName] = [...(axisName === 'x' ? (table.x_labels || []) : (table.y_labels || []))];
      renderList();
    });
  });

  container.querySelectorAll('[data-axis-action="apply"]').forEach(button => {
    button.addEventListener('click', async event => {
      event.stopPropagation();
      await applyAxisDraft(tableId, button.dataset.axisName);
    });
  });
}

function refreshEditableCard(card, table) {
  const selection = state.selections[table.table_id];
  const previewLookup = buildPreviewLookup(table.table_id);
  const selectionChip = card.querySelector('.selection-chip');
  if (selectionChip) {
    selectionChip.textContent = formatSelectionLabel(selection, table);
  }
  const previewBox = card.querySelector('.editor-preview');
  if (previewBox && !state.previews[table.table_id]) {
    previewBox.innerHTML = '<div class="editor-preview-copy">Select a region, then preview or apply an operation.</div>';
  }
  card.querySelectorAll('.table-cell').forEach(cell => {
    const row = Number(cell.dataset.row);
    const col = Number(cell.dataset.col);
    cell.classList.toggle('is-selected', selectionContains(selection, row, col));
    cell.classList.toggle('is-preview', previewLookup.has(`${row}:${col}`));
  });
}

async function runEditorAction(tableId, previewOnly) {
  if (!state.session?.session_id) {
    showToast('No active editing session.');
    return;
  }
  const selection = state.selections[tableId];
  if (!selection) {
    showToast('Select a table region first.');
    return;
  }

  const formState = state.editorForms[tableId] || { operation: 'add_constant', value: 1 };
  const payload = {
    table_id: tableId,
    operation: formState.operation,
    selection,
    parameters: {},
  };
  if (!String(formState.operation).startsWith('interpolate')) {
    payload.parameters.value = Number(formState.value);
  }

  const endpoint = previewOnly ? 'preview' : 'apply';
  const response = await fetch(`/api/session/${state.session.session_id}/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Edit request failed.');
    return;
  }

  if (previewOnly) {
    state.previews[tableId] = data.preview;
    renderList();
    showToast('Preview ready.');
    return;
  }

  state.session = data.session || state.session;
  state.previews[tableId] = data.preview || null;
  state.tables = state.tables.map(table => (table.table_id === tableId ? data.table : table));
  updateDraftToolbar();
  renderList();
  showToast('Draft updated.');
}

async function applyAxisDraft(tableId, axisName) {
  if (!state.session?.session_id) {
    return;
  }
  const table = state.tables.find(item => item.table_id === tableId);
  if (!table) {
    showToast('Could not find the selected table.');
    return;
  }
  const values = getAxisDraft(table, axisName).map(value => Number(value));
  const response = await fetch(`/api/session/${state.session.session_id}/axis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ table_id: tableId, axis: axisName, values }),
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || `Failed to apply ${String(axisName).toUpperCase()} axis.`);
    return;
  }

  state.session = data.session || state.session;
  state.tables = state.tables.map(table => (table.table_id === tableId ? data.table : table));
  if (!state.axisDrafts[tableId]) {
    state.axisDrafts[tableId] = {};
  }
  state.axisDrafts[tableId][axisName] = [...(axisName === 'x' ? (data.table.x_labels || []) : (data.table.y_labels || []))];
  updateDraftToolbar();
  renderList();
  showToast(`${String(axisName).toUpperCase()} axis updated.`);
}

async function undoDraft() {
  if (!state.session?.session_id) {
    return;
  }
  const response = await fetch(`/api/session/${state.session.session_id}/undo`, { method: 'POST' });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Undo failed.');
    return;
  }
  state.tables = data.tables || [];
  state.categories = data.categories || state.categories;
  state.session = data.session || null;
  state.previews = {};
  updateDraftToolbar();
  renderList();
  showToast('Undid the last draft edit.');
}

async function resetDraft() {
  if (!state.session?.session_id) {
    return;
  }
  const response = await fetch(`/api/session/${state.session.session_id}/reset`, { method: 'POST' });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Reset failed.');
    return;
  }
  state.tables = data.tables || [];
  state.categories = data.categories || state.categories;
  state.session = data.session || null;
  state.previews = {};
  state.selections = {};
  updateDraftToolbar();
  renderList();
  showToast('Draft reset to the original BIN.');
}

async function exportDraft() {
  if (!state.session?.session_id) {
    return;
  }
  const response = await fetch(`/api/session/${state.session.session_id}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stem: stripExtension(state.files.bin?.name || 'edited-bin') }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    showToast(data.error || 'Export failed.');
    return;
  }

  const blob = await response.blob();
  const disposition = response.headers.get('content-disposition') || '';
  const match = disposition.match(/filename=\"?([^\";]+)\"?/i);
  const filename = match ? match[1] : 'edited-bin.zip';
  downloadBlob(blob, filename);
  showToast('Export ready.');
}

function stripExtension(filename) {
  return String(filename || 'edited-bin').replace(/\.[^.]+$/, '');
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function openMetadataModal(tableId) {
  const table = state.tables.find(item => item.table_id === tableId);
  if (!table) {
    return;
  }
  const override = getTableOverride(table);
  state.editingMetadataTableId = tableId;
  metadataTitleInput.value = Object.prototype.hasOwnProperty.call(override, 'title') ? override.title : table.title;
  metadataShortInput.value = Object.prototype.hasOwnProperty.call(override, 'short')
    ? override.short
    : (table.description_short || table.description || '');
  metadataLongInput.value = Object.prototype.hasOwnProperty.call(override, 'long')
    ? override.long
    : (table.description_long || '');
  metadataModal.style.display = 'flex';
}

function closeMetadataModal() {
  metadataModal.style.display = 'none';
  state.editingMetadataTableId = null;
}

function saveMetadataOverride() {
  const table = state.tables.find(item => item.table_id === state.editingMetadataTableId);
  if (!table) {
    closeMetadataModal();
    return;
  }
  state.metadataOverrides[getMetadataKey(table)] = {
    title: metadataTitleInput.value.trim(),
    short: metadataShortInput.value.trim(),
    long: metadataLongInput.value.trim(),
  };
  saveMetadataOverrides();
  closeMetadataModal();
  renderList();
  showToast('Local metadata override saved.');
}

function clearMetadataOverride() {
  const table = state.tables.find(item => item.table_id === state.editingMetadataTableId);
  if (!table) {
    closeMetadataModal();
    return;
  }
  delete state.metadataOverrides[getMetadataKey(table)];
  saveMetadataOverrides();
  closeMetadataModal();
  renderList();
  showToast('Local metadata override cleared.');
}

function copyForAI(table) {
  const lines = [];
  const unitsSuffix = table.units ? ` (${table.units})` : '';
  lines.push(`[${getDisplayTitle(table)}]${unitsSuffix}`);

  if (table.error) {
    lines.push(`ERROR: ${table.error}`);
    return copyText(lines.join('\n'));
  }

  if (table.type === 'constant' || (table.rows === 1 && table.cols === 1)) {
    const first = formatNumber(table.grid?.[0]?.[0] ?? 0);
    if (table.has_diff) {
      const second = formatNumber(table.grid2?.[0]?.[0] ?? 0);
      lines.push(`Value: ${first} -> ${second}${unitsSuffix}`);
    } else {
      lines.push(`Value: ${first}${unitsSuffix}`);
    }
    return copyText(lines.join('\n'));
  }

  const hasX = Array.isArray(table.x_labels) && table.x_labels.length > 0;
  const hasY = Array.isArray(table.y_labels) && table.y_labels.length > 0;
  const widths = new Array(table.cols + 1).fill(3);
  widths[0] = hasY ? Math.max(3, ...table.y_labels.map(value => formatNumber(value).length)) : 3;

  for (let col = 0; col < table.cols; col += 1) {
    let width = (hasX && table.x_labels[col] !== undefined ? formatNumber(table.x_labels[col]) : `C${col}`).length;
    for (let row = 0; row < table.rows; row += 1) {
      let cell = formatNumber((table.grid[row] || [])[col] ?? 0);
      if (table.has_diff && table.grid2) {
        const other = formatNumber((table.grid2[row] || [])[col] ?? 0);
        if (cell !== other) {
          cell = `${cell}->${other}`;
        }
      }
      width = Math.max(width, cell.length);
    }
    widths[col + 1] = width;
  }

  const headers = [pad('', widths[0])];
  for (let col = 0; col < table.cols; col += 1) {
    const header = hasX && table.x_labels[col] !== undefined ? formatNumber(table.x_labels[col]) : `C${col}`;
    headers.push(pad(header, widths[col + 1]));
  }
  lines.push(headers.join(' | '));
  lines.push(headers.map((_, index) => '-'.repeat(widths[index])).join('-+-'));

  for (let row = 0; row < table.rows; row += 1) {
    const rowLabel = hasY && table.y_labels[row] !== undefined ? formatNumber(table.y_labels[row]) : `R${row}`;
    const cells = [pad(rowLabel, widths[0])];
    for (let col = 0; col < table.cols; col += 1) {
      let cell = formatNumber((table.grid[row] || [])[col] ?? 0);
      if (table.has_diff && table.grid2) {
        const other = formatNumber((table.grid2[row] || [])[col] ?? 0);
        if (cell !== other) {
          cell = `${cell}->${other}`;
        }
      }
      cells.push(pad(cell, widths[col + 1]));
    }
    lines.push(cells.join(' | '));
  }

  copyText(lines.join('\n'));
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(showToast).catch(() => {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    textarea.remove();
    showToast();
  });
}

function resetAll() {
  state.files = { xdf: null, bin: null, bin2: null };
  state.tables = [];
  state.categories = {};
  state.search = '';
  state.viewMode = 'workflow';
  state.hideAutogen = true;
  state.session = null;
  state.selections = {};
  state.previews = {};
  state.editorForms = {};
  state.axisDrafts = {};
  searchInput.value = '';
  searchClear.style.display = 'none';
  viewModeSelect.value = state.viewMode;

  ['xdf', 'bin', 'bin2'].forEach(key => {
    $(`input-${key}`).value = '';
    $(`status-${key}`).textContent = 'No file selected';
    $(`drop-${key}`).classList.remove('has-file');
  });

  uploadPanel.style.display = '';
  resultsArea.style.display = 'none';
  editorToolbar.style.display = 'none';
  btnClear.style.display = 'none';
  headerMeta.textContent = '';
  countBadge.textContent = '0 / 0 shown';
  tableList.innerHTML = '';
  hideError();
  syncHideAutogenButton();
  updateDraftToolbar();
  updateParseBtn();
}

function showProgress(show) {
  progressBar.style.display = show ? 'block' : 'none';
  if (show) setProgress(0);
}

function setProgress(percent) {
  progressFill.style.width = `${percent}%`;
}

function showError(message) {
  uploadError.textContent = message;
  uploadError.style.display = 'block';
}

function hideError() {
  uploadError.style.display = 'none';
}

function showToast(message = 'Copied to clipboard!') {
  toast.textContent = message;
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 1800);
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatNumber(value) {
  if (typeof value !== 'number') return String(value);
  if (!Number.isFinite(value)) return '-';
  if (Number.isInteger(value)) return String(value);

  const abs = Math.abs(value);
  let decimals = 2;
  if (abs >= 1000) decimals = 0;
  else if (abs >= 100) decimals = 1;
  else if (abs >= 1) decimals = 2;
  else if (abs >= 0.1) decimals = 3;
  else decimals = 4;
  return String(parseFloat(value.toFixed(decimals)));
}

function pad(value, width) {
  const stringValue = String(value);
  return stringValue.length >= width ? stringValue : stringValue + ' '.repeat(width - stringValue.length);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}










