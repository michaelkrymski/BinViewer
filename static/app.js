'use strict';

const WORKFLOW_BUCKET_ORDER = [
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
const EDITOR_PREFS_STORAGE_KEY = 'tunerpro-viewer.editor-prefs.v1';
const DRAFT_SESSION_STORAGE_KEY = 'tunerpro-viewer.saved-draft-session.v1';

function loadEditorPrefs() {
  try {
    const raw = window.localStorage.getItem(EDITOR_PREFS_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== 'object') {
      return { operation: 'add_constant', value: 1 };
    }
    return {
      operation: parsed.operation || 'add_constant',
      value: Number.isFinite(Number(parsed.value)) ? parsed.value : 1,
    };
  } catch (error) {
    return { operation: 'add_constant', value: 1 };
  }
}

function saveEditorPrefs() {
  window.localStorage.setItem(EDITOR_PREFS_STORAGE_KEY, JSON.stringify(state.lastEditorPreset));
}

const state = {
  files: { xdf: null, bin: null, log: null },
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
  preferences: { favorites: { tables: [], families: [] }, folders: [], assignments: {}, log: { axis_mappings: {}, graph: { enabled_columns: [] }, playback_speed: 1 } },
  editingFolderId: null,
  quickAddFolderId: null,
  quickAddAssignments: new Set(),
  quickAddQuery: '',
  selectedFolderId: null,
  metadataOverrides: loadMetadataOverrides(),
  editingMetadataTableId: null,
  openState: { cards: new Set(), groups: new Set(), families: new Set() },
  lastEditorPreset: loadEditorPrefs(),
  selectionAnchors: {},
  importTableId: null,
  importPreview: null,
  project: null,
  projectBins: [],
  tableClipboard: null,
  log: null,
  logOverlay: null,
  logPlayback: {
    sampleIndex: 0,
    rangeStart: 0,
    rangeEnd: 0,
    speed: 1,
    playing: false,
    timer: null,
  },
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

const AI_TABLE_IMPORT_TEMPLATE = `Fill or revise the ECU calibration table below and return only a TunerPro XDF Viewer import block.

RULES
- Keep the same table shape unless I explicitly ask for different axes.
- If changing only cell values, use Mode: values_only.
- If changing cell values and X/Y breakpoint labels, use Mode: values_and_axes.
- Return numeric values only in the Values section. Do not include explanations inside the import block.
- Preserve row and column order exactly.
- For scalars, return one value.

OUTPUT FORMAT
[TABLE IMPORT]
Title: <exact table title>
Mode: values_only

X Axis:
<comma-separated X labels if present>

Y Axis:
<comma-separated Y labels if present>

Values:
<comma-separated row 1>
<comma-separated row 2>
<continue for every row>

TABLE TO FILL
Title: <table title>
Units: <units or blank>
Rows: <row count>
Columns: <column count>
X Axis: <current X labels if present>
Y Axis: <current Y labels if present>
Current Values:
<paste copied table values here>`;

const $ = id => document.getElementById(id);
const on = (node, eventName, handler) => {
  if (node) {
    node.addEventListener(eventName, handler);
  }
};
const resultsArea = $('results-area');
const tableList = $('table-list');
const mapTabs = $('map-tabs');
const categoryNav = $('category-nav');
const categoryTabs = $('category-tabs');
const workspaceOverview = $('workspace-overview');
const workspaceEmpty = $('workspace-empty');
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
const btnParseLog = $('btn-parse-log');
const btnDebug = $('btn-debug');
const btnHideAutogen = $('btn-hide-autogen');
const btnCollapseAll = $('btn-collapse-all');
const btnExportBinTop = $('btn-export-bin-top');
const btnNewFiles = $('btn-new-files');
const btnAiContext = $('btn-ai-context');
const btnCopyAiContext = $('btn-copy-ai-context');
const btnToggleFavorite = $('btn-toggle-favorite');
const btnCreateFolder = $('btn-create-folder');
const btnClearFolderFilter = $('btn-clear-folder-filter');
const viewModeSelect = $('view-mode');
const editorToolbar = $('editor-toolbar');
const draftBadge = $('draft-badge');
const draftMeta = $('draft-meta');
const btnUndoDraft = $('btn-undo-draft');
const btnResetDraft = $('btn-reset-draft');
const btnExportDraft = $('btn-export-draft');
const btnSaveDraftSession = $('btn-save-draft-session');
const btnRestoreDraftSession = $('btn-restore-draft-session');
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
const folderList = $('folder-list');
const folderModal = $('folder-modal');
const folderNameInput = $('folder-name');
const btnFolderSave = $('folder-save');
const btnFolderDelete = $('folder-delete');
const quickAddModal = $('folder-quick-add-modal');
const quickAddTitle = $('folder-quick-add-title');
const quickAddSearch = $('folder-quick-add-search');
const quickAddList = $('folder-quick-add-list');
const btnQuickAddClose = $('folder-quick-add-close');
const btnQuickAddCancel = $('folder-quick-add-cancel');
const btnQuickAddClear = $('folder-quick-add-clear');
const btnQuickAddSave = $('folder-quick-add-save');
const importModal = $('import-modal');
const importText = $('import-text');
const importMode = $('import-mode');
const importError = $('import-error');
const importSummary = $('import-summary');
const importCompare = $('import-compare');
const importMirrorPanel = $('import-mirror-panel');
const importMirrorToggle = $('import-mirror-toggle');
const importMirrorList = $('import-mirror-list');
const btnImportClear = $('import-clear');
const btnImportPreview = $('import-preview-btn');
const btnImportApply = $('import-apply-btn');
const btnImportCopyTemplate = $('import-copy-template');
const projectStatus = $('project-status');
const projectMeta = $('project-meta');
const projectBinSelect = $('project-bin-select');
const projectSaveMode = $('project-save-mode');
const btnProjectLoad = $('btn-project-load');
const btnProjectRefresh = $('btn-project-refresh');
const btnProjectSaveDraft = $('btn-project-save-draft');
const btnProjectRestoreDraft = $('btn-project-restore-draft');
const btnProjectExport = $('btn-project-export');
const clipboardModal = $('clipboard-modal');
const clipboardText = $('clipboard-text');
const btnClipboardClose = $('clipboard-close');
const btnClipboardCopy = $('clipboard-copy');
const btnClipboardSelect = $('clipboard-select');
const workspaceLogbar = $('workspace-logbar');

let debugTables = [];
let dragSelection = null;

document.addEventListener('click', event => {
  if (!event.target.closest('#import-copy-template')) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  copyImportTemplate();
});

init();

function init() {
  wireDropZones();
  wireToolbar();
  wireModals();
  loadPreferences();
  loadProjectStatus();
  updateParseBtn();
  syncHideAutogenButton();
  updateDraftToolbar();
  aiContextText.textContent = AI_CONTEXT_PROMPT;
  viewModeSelect.value = state.viewMode;
  window.addEventListener('mouseup', () => { dragSelection = null; });
  document.addEventListener('copy', handleTableCopy);
  document.addEventListener('paste', handleTablePaste);
  renderResults();
}

function wireDropZones() {
  ['xdf', 'bin', 'log'].forEach(key => {
    const zone = $(`drop-${key}`);
    const input = $(`input-${key}`);
    if (!zone || !input) {
      return;
    }

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
  if (btnParseLog) {
    btnParseLog.addEventListener('click', parseLogFile);
  }
  btnDebug.addEventListener('click', doDebug);
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
  btnToggleFavorite.addEventListener('click', toggleSelectedFavorite);
  btnCreateFolder.addEventListener('click', () => openFolderModal());
  btnClearFolderFilter.addEventListener('click', clearFolderFilter);

  btnCopyAiContext.addEventListener('click', () => copyText(AI_CONTEXT_PROMPT));
  btnExportBinTop.addEventListener('click', exportDraft);

  btnSaveDraftSession.addEventListener('click', saveDraftSession);
  btnRestoreDraftSession.addEventListener('click', restoreDraftSession);
  btnUndoDraft.addEventListener('click', undoDraft);
  btnResetDraft.addEventListener('click', resetDraft);
  btnExportDraft.addEventListener('click', exportDraft);
  btnProjectLoad.addEventListener('click', parseProjectBin);
  btnProjectRefresh.addEventListener('click', loadProjectBins);
  btnProjectSaveDraft.addEventListener('click', saveProjectDraft);
  btnProjectRestoreDraft.addEventListener('click', restoreProjectDraft);
  btnProjectExport.addEventListener('click', exportProjectDraft);
  projectSaveMode.addEventListener('change', updateProjectSaveMode);
}

function wireModals() {
  on($('debug-close'), 'click', () => { debugModal.style.display = 'none'; });
  on($('ai-context-close'), 'click', () => { aiContextModal.style.display = 'none'; });
  on($('metadata-close'), 'click', closeMetadataModal);
  on($('folder-close'), 'click', closeFolderModal);
  on($('import-close'), 'click', closeImportModal);
  on(btnClipboardClose, 'click', closeClipboardModal);
  on(btnClipboardCopy, 'click', copyClipboardModalText);
  on(btnClipboardSelect, 'click', selectClipboardFallbackText);
  on(btnQuickAddClose, 'click', closeQuickAddModal);
  on(btnQuickAddCancel, 'click', closeQuickAddModal);
  on(btnImportClear, 'click', clearImportModal);
  on(btnImportPreview, 'click', previewImportedTable);
  on(btnImportApply, 'click', applyImportedTable);
  on(importMirrorToggle, 'change', renderImportMirrorTargets);
  on(btnQuickAddClear, 'click', () => {
    quickAddSearch.value = '';
    state.quickAddQuery = '';
    renderQuickAddList();
  });
  on(btnQuickAddSave, 'click', saveQuickAddAssignments);

  on(debugModal, 'click', event => {
    if (event.target === debugModal) {
      debugModal.style.display = 'none';
    }
  });

  on(aiContextModal, 'click', event => {
    if (event.target === aiContextModal) {
      aiContextModal.style.display = 'none';
    }
  });

  on(metadataModal, 'click', event => {
    if (event.target === metadataModal) {
      closeMetadataModal();
    }
  });
  on(folderModal, 'click', event => {
    if (event.target === folderModal) {
      closeFolderModal();
    }
  });
  on(quickAddModal, 'click', event => {
    if (event.target === quickAddModal) {
      closeQuickAddModal();
    }
  });
  on(importModal, 'click', event => {
    if (event.target === importModal) {
      closeImportModal();
    }
  });
  on(clipboardModal, 'click', event => {
    if (event.target === clipboardModal) {
      closeClipboardModal();
    }
  });

  on(btnMetadataSave, 'click', saveMetadataOverride);
  on(btnMetadataReset, 'click', clearMetadataOverride);
  on(btnFolderSave, 'click', saveFolder);
  on(btnFolderDelete, 'click', deleteFolder);
  on(quickAddSearch, 'input', () => {
    state.quickAddQuery = quickAddSearch.value.trim().toLowerCase();
    renderQuickAddList();
  });
  on(importText, 'input', () => {
    hideImportError();
    btnImportApply.disabled = true;
  });

  on(debugSearch, 'input', () => renderDebugOutput(debugTables));
}

function setFile(key, file) {
  state.files[key] = file;
  $(`status-${key}`).textContent = `${file.name} (${formatSize(file.size)})`;
  $(`drop-${key}`).classList.add('has-file');
  hideError();
  updateParseBtn();
  if (key === 'log' && state.session?.session_id) {
    parseLogFile();
  }
}

function updateParseBtn() {
  btnParse.disabled = !(state.files.xdf && state.files.bin);
  if (btnParseLog) {
    btnParseLog.disabled = !(state.files.log && state.session?.session_id);
  }
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

    applySessionPayload(data);
    if (state.files.log) {
      await parseLogFile({ silent: true });
    }

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

function applySessionPayload(data) {
  state.tables = data.tables || [];
  state.categories = data.categories || {};
  state.hasDiff = state.tables.some(table => table.has_diff);
  state.session = data.session || null;
  state.selections = {};
  state.previews = {};
  state.editorForms = {};
  state.axisDrafts = {};
  state.selectedTableId = null;
  state.log = data.log || null;
  state.logOverlay = null;
  stopLogPlayback();
  if (data.preferences) {
    state.preferences = normalizePreferences(data.preferences);
  }
  if (data.project) {
    state.project = data.project;
    renderProjectPanel();
  }
  syncLogPlaybackPrefs();
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

async function loadProjectStatus() {
  try {
    const response = await fetch('/api/project/status');
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Project status failed.');
    }
    state.project = data.project || null;
    renderProjectPanel();
    if (state.project?.active_project) {
      loadProjectBins();
    }
  } catch (error) {
    projectStatus.textContent = 'Tray project unavailable';
    projectMeta.textContent = error.message;
    syncProjectButtons();
  }
}

async function loadProjectBins() {
  if (!state.project?.active_project) {
    renderProjectPanel();
    return;
  }
  try {
    const response = await fetch('/api/project/bins');
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Project BIN list failed.');
    }
    state.projectBins = data.bins || [];
    if (data.project) {
      state.project = data.project;
    }
    renderProjectPanel();
  } catch (error) {
    showToast(error.message);
    syncProjectButtons();
  }
}

function renderProjectPanel() {
  const project = state.project?.active_project;
  if (!project) {
    projectStatus.textContent = 'No active tray project';
    projectMeta.textContent = 'Use the Windows tray app to create or select a project.';
    projectBinSelect.innerHTML = '<option value="">Use last project BIN</option>';
    projectSaveMode.value = 'new_revision';
    syncProjectButtons();
    return;
  }
  projectStatus.textContent = project.name || 'Active project';
  const binName = project.last_bin_name || 'no BIN selected';
  const xdfName = project.xdf_name || 'no XDF selected';
  projectMeta.textContent = `${xdfName} · ${binName} · ${project.remote_save_mode === 'small_edit' ? 'small edit saves' : 'new revision saves'}`;
  projectSaveMode.value = project.remote_save_mode || 'new_revision';
  const currentValue = projectBinSelect.value;
  const options = ['<option value="">Use last project BIN</option>'];
  state.projectBins.forEach(item => {
    options.push(`<option value="${escapeHtml(item.path)}"${item.is_last ? ' selected' : ''}>${escapeHtml(item.name)}</option>`);
  });
  projectBinSelect.innerHTML = options.join('');
  if (currentValue && state.projectBins.some(item => item.path === currentValue)) {
    projectBinSelect.value = currentValue;
  }
  syncProjectButtons();
}

function syncProjectButtons() {
  const hasProject = Boolean(state.project?.active_project);
  const hasSession = Boolean(state.session?.session_id);
  btnProjectLoad.disabled = !hasProject;
  btnProjectRefresh.disabled = !hasProject;
  btnProjectRestoreDraft.disabled = !hasProject;
  btnProjectSaveDraft.disabled = !hasProject || !hasSession;
  btnProjectExport.disabled = !hasProject || !hasSession || !(state.session?.export_ready || state.session?.pending_change_count);
  projectSaveMode.disabled = !hasProject;
  projectBinSelect.disabled = !hasProject;
}

async function parseProjectBin() {
  hideError();
  showProgress(true);
  try {
    const response = await fetch('/api/project/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bin_path: projectBinSelect.value || undefined }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Project load failed.');
    }
    applySessionPayload(data);
    if (state.files.log) {
      await parseLogFile({ silent: true });
    }
    setProjectFileStatus();
    showProgress(false);
    renderResults();
    showToast('Project BIN loaded.');
  } catch (error) {
    showProgress(false);
    showError(error.message);
  }
}

async function saveProjectDraft() {
  if (!state.session?.session_id) {
    showToast('No active draft to save.');
    return;
  }
  const response = await fetch('/api/project/draft/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: state.session.session_id }),
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Project draft save failed.');
    return;
  }
  if (data.project) {
    state.project = data.project;
    renderProjectPanel();
  }
  showToast(`Project draft saved to ${data.draft_path}`);
}

async function restoreProjectDraft() {
  const response = await fetch('/api/project/draft/restore', { method: 'POST' });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Project draft restore failed.');
    return;
  }
  applySessionPayload(data);
  if (state.files.log) {
    await parseLogFile({ silent: true });
  }
  setProjectFileStatus();
  renderResults();
  showToast('Project draft restored.');
}

async function updateProjectSaveMode() {
  const response = await fetch('/api/project/save-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ remote_save_mode: projectSaveMode.value }),
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Save mode update failed.');
    renderProjectPanel();
    return;
  }
  state.project = data.project || state.project;
  renderProjectPanel();
  showToast(projectSaveMode.value === 'small_edit' ? 'Small edit saves enabled.' : 'New revision saves enabled.');
}

async function exportProjectDraft() {
  if (!state.session?.session_id) {
    showToast('No active draft to export.');
    return;
  }
  const project = state.project?.active_project || {};
  let description;
  if ((projectSaveMode.value || project.remote_save_mode) === 'new_revision') {
    const defaultDescription = getDescriptionFromFilename(project.last_bin_name || state.session?.source_bin_name || '');
    const entered = window.prompt('Revision description for the exported BIN:', defaultDescription);
    if (entered === null) {
      showToast('Project export cancelled.');
      return;
    }
    description = entered.trim();
  }
  const shouldDownload = !isLocalHost();
  const response = await fetch('/api/project/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: state.session.session_id,
      description,
      download: shouldDownload,
    }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    showToast(data.error || 'Project export failed.');
    return;
  }
  if (!shouldDownload) {
    const data = await response.json();
    if (data.project) {
      state.project = data.project;
      renderProjectPanel();
    } else {
      await loadProjectStatus();
    }
    showToast(data.log_path ? `Project BIN saved to ${data.export_path}. Log saved to ${data.log_path}` : 'Project BIN saved.');
    return;
  }
  const blob = await response.blob();
  const disposition = response.headers.get('content-disposition') || '';
  const exportPath = response.headers.get('X-Export-Path') || '';
  const exportLogPath = response.headers.get('X-Export-Log-Path') || '';
  const match = disposition.match(/filename=\"?([^\";]+)\"?/i);
  const filename = match ? match[1] : 'edited-bin.bin';
  downloadBlob(blob, filename);
  await loadProjectStatus();
  showToast(exportLogPath ? `Project BIN saved to ${exportPath}. Log saved to ${exportLogPath}` : 'Project BIN exported.');
}

function isLocalHost() {
  const host = window.location.hostname;
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
}

function getDescriptionFromFilename(filename) {
  const match = String(filename || '').match(/^[^.]*?(?:v|mk)\d+\(([^)]*)\)/i);
  return match ? match[1] : '';
}

function setProjectFileStatus() {
  if (!state.session) {
    return;
  }
  $('status-xdf').textContent = `Project XDF: ${state.session.source_xdf_name || 'loaded'}`;
  $('status-bin').textContent = `Project BIN: ${state.session.source_bin_name || 'loaded'}`;
  $('drop-xdf').classList.add('has-file');
  $('drop-bin').classList.add('has-file');
}

function renderResults() {
  resultsArea.style.display = '';
  const loaded = Boolean(state.tables.length && (state.session || (state.files.xdf && state.files.bin)));
  headerMeta.textContent = loaded
    ? `${state.session?.source_xdf_name || state.files.xdf?.name || ''} · ${state.session?.source_bin_name || state.files.bin?.name || ''}${state.log?.filename ? ` · ${state.log.filename}` : ''} · ${state.tables.length} definitions`
    : 'No files loaded';
  viewModeSelect.value = state.viewMode;
  if (!state.selectedCategory) {
    state.selectedCategory = WORKFLOW_BUCKET_ORDER[0] || 'Boost / WGDC';
  }
  updateDraftToolbar();
  renderFolderList();
  renderLogWorkspaceBar();
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
  workspaceEmpty.style.display = state.tables.length ? 'none' : '';
  btnToggleFavorite.disabled = !state.selectedTableId;
  renderFolderList();

  if (!state.tables.length) {
    renderLogWorkspaceBar();
    renderInspector();
    return;
  }

  if (!visible.length) {
    tableList.innerHTML = '<div class="no-results"><div>No tables match your filter.</div></div>';
    renderLogWorkspaceBar();
    renderInspector();
    return;
  }

  if (state.viewMode === 'flat') {
    renderFlatView(visible);
    ensureSelectedTable(visible);
    renderLogWorkspaceBar();
    renderInspector(visible);
    return;
  }

  if (state.viewMode === 'hierarchy') {
    renderHierarchyView(visible);
    ensureSelectedTable(visible);
    renderLogWorkspaceBar();
    renderInspector(visible);
    return;
  }

  renderWorkflowView(visible);
  renderLogWorkspaceBar();
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

  if (state.selectedFolderId) {
    const before = visible.length;
    visible = visible.filter(table => tableMatchesFolder(table, state.selectedFolderId));
    if (visible.length !== before) {
      const folder = (state.preferences.folders || []).find(item => item.id === state.selectedFolderId);
      reasons.push(`folder: ${(folder && folder.name) || 'saved set'}`);
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
      switchToMap(mapLabel, tables);
    });
    mapTabs.appendChild(button);
  });
}

function switchToMap(mapLabel, tables = state.tables) {
  const currentTable = state.tables.find(item => item.table_id === state.selectedTableId) || null;
  state.selectedMap = mapLabel;
  let target = null;
  if (currentTable) {
    target = findSiblingTableForMap(currentTable, mapLabel, tables);
  }
  if (target) {
    state.selectedCategory = target.workflow_bucket || state.selectedCategory;
    state.selectedTableId = target.table_id;
  } else {
    state.selectedCategory = null;
  }
  renderList();
  if (hasActiveLog()) {
    refreshLogOverlay().then(() => renderResults());
  }
}

function findSiblingTableForMap(table, mapLabel, tables = state.tables) {
  const familyKey = table.family_key || table.family_title || table.title;
  const variantLabel = table.variant_label || '';
  const exactTitle = table.title;
  const candidates = (tables || []).filter(item => tableBelongsToMap(item, mapLabel));
  const exactTitleMatch = candidates.find(item => item.title === exactTitle);
  if (exactTitleMatch) {
    return exactTitleMatch;
  }
  const exactVariantMatch = candidates.find(item =>
    (item.family_key || item.family_title || item.title) === familyKey
      && String(item.variant_label || '') === String(variantLabel || ''),
  );
  if (exactVariantMatch) {
    return exactVariantMatch;
  }
  const familyMatch = candidates.find(item => (item.family_key || item.family_title || item.title) === familyKey);
  if (familyMatch) {
    return familyMatch;
  }
  return null;
}

function getMirrorSiblingTables(tableId, tables = state.tables) {
  const source = (tables || []).find(item => item.table_id === tableId);
  if (!source || isGlobalTable(source)) {
    return [];
  }
  const familyKey = source.family_key || source.family_title || source.title;
  const variantLabel = String(source.variant_label || '');
  return (tables || []).filter(item => {
    if (item.table_id === tableId) return false;
    if (isGlobalTable(item)) return false;
    if ((item.family_key || item.family_title || item.title) !== familyKey) return false;
    return String(item.variant_label || '') === variantLabel;
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
  const favoriteTables = getFavoriteTables(mapTables).slice(0, 8);
  const folderCount = (state.preferences.folders || []).length;

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
      <div class="overview-card__eyebrow">Favorites</div>
      <div class="overview-card__title">Starred tables and custom folders</div>
      <div class="overview-summary">
        <div class="overview-stat">
          <span class="overview-stat__value">${favoriteTables.length}</span>
          <span class="overview-stat__label">Starred Rows</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat__value">${folderCount}</span>
          <span class="overview-stat__label">Custom Folders</span>
        </div>
      </div>
      <div class="overview-pills overview-pills--favorites"></div>
    </div>`;

  const generalWrap = workspaceOverview.querySelector('.overview-pills--general');
  const favoriteWrap = workspaceOverview.querySelector('.overview-pills--favorites');

  generalTables.forEach(table => {
    generalWrap.appendChild(buildOverviewPill(table));
  });
  favoriteTables.forEach(table => favoriteWrap.appendChild(buildOverviewPill(table)));
  if (!favoriteTables.length) {
    favoriteWrap.innerHTML = '<div class="overview-empty">Star tables or families to keep them within easy reach across sessions.</div>';
  }
}

function buildOverviewPill(table) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'overview-pill';
  button.innerHTML = `${getOverviewMapBadge(table)}<span>${escapeHtml(getDisplayTitle(table))}</span>`;
  button.addEventListener('click', () => {
    state.selectedCategory = table.workflow_bucket || state.selectedCategory || WORKFLOW_BUCKET_ORDER[0];
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
  if (text.includes('commanded wgdc')) {
    return false;
  }
  if ((table.workflow_bucket || '') === 'Map Switching / Multi-map') {
    return true;
  }
  return ['ethanol', 'switch', 'slot', 'map', 'flex', 'fuel scalar', 'static ethanol'].some(keyword => text.includes(keyword));
}

function shortBucketLabel(bucket) {
  return String(bucket)
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
  btnToggleFavorite.disabled = !state.selectedTableId;
}

function selectTable(tableId) {
  state.selectedTableId = tableId;
  const table = state.tables.find(item => item.table_id === tableId);
  if (table && getTableMapVariant(table) && !isGlobalTable(table)) {
    state.selectedMap = getTableMapVariant(table);
  }
  if (table?.workflow_bucket) {
    state.selectedCategory = table.workflow_bucket;
  }
  if (table && isScalarTable(table) && !state.selections[tableId]) {
    state.selections[tableId] = normalizeSelection({
      row_start: 0,
      row_end: Math.max(table.rows - 1, 0),
      col_start: 0,
      col_end: Math.max(table.cols - 1, 0),
    });
  }
  btnToggleFavorite.disabled = !state.selectedTableId;
  renderList();
  if (hasActiveLog()) {
    refreshLogOverlay().then(() => renderResults());
  }
}

function updateDraftToolbar() {
  const session = state.session;
  const pending = Number(session?.pending_change_count || 0);
  const dirty = Number(session?.dirty_table_count || 0);
  const exportReady = Boolean(session?.export_ready);

  if (!session) {
    draftBadge.textContent = 'Draft Unloaded';
    draftMeta.textContent = '0 pending edits · 0 dirty tables';
    btnSaveDraftSession.disabled = true;
    btnUndoDraft.disabled = true;
    btnResetDraft.disabled = true;
    btnExportDraft.disabled = true;
    btnExportBinTop.disabled = true;
    syncProjectButtons();
    return;
  }

  draftBadge.textContent = exportReady ? 'Draft Dirty' : 'Draft Clean';
  draftBadge.classList.toggle('is-dirty', exportReady);
  draftMeta.textContent = `${pending} pending edits · ${dirty} dirty tables`;
  btnSaveDraftSession.disabled = false;
  btnUndoDraft.disabled = pending === 0;
  btnResetDraft.disabled = pending === 0;
  btnExportDraft.disabled = !exportReady;
  btnExportBinTop.disabled = !exportReady;
  syncProjectButtons();
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
  const mapVariants = Array.from(new Map(
    group.tables
      .filter(table => getTableMapVariant(table))
      .map(table => [getTableMapVariant(table), table]),
  ).entries());
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
      ${mapVariants.length ? `<span class="family-map-switcher">${mapVariants.map(([mapLabel]) => `<button class="family-map-chip${mapLabel === state.selectedMap ? ' is-active' : ''}" type="button" data-family-map="${escapeHtml(mapLabel)}">${escapeHtml(mapLabel.replace('Map ', 'M'))}</button>`).join('')}</span>` : ''}
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

  section.querySelectorAll('[data-family-map]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      const mapLabel = button.dataset.familyMap;
      const target = group.tables.find(table => getTableMapVariant(table) === mapLabel) || group.representative;
      state.selectedMap = mapLabel;
      state.selectedCategory = target.workflow_bucket || state.selectedCategory;
      selectTable(target.table_id);
    });
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

function hasActiveLog() {
  return Boolean(state.log && state.session?.session_id);
}

function getLogPrefs() {
  return state.preferences.log || { axis_mappings: {}, graph: { enabled_columns: [] }, playback_speed: 1 };
}

function syncLogPlaybackPrefs() {
  const prefs = getLogPrefs();
  const speed = Number(prefs.playback_speed);
  state.logPlayback.speed = Number.isFinite(speed) && speed > 0 ? speed : 1;
}

function stopLogPlayback() {
  if (state.logPlayback.timer) {
    window.clearInterval(state.logPlayback.timer);
    state.logPlayback.timer = null;
  }
  state.logPlayback.playing = false;
}

function renderLogWorkspaceBar() {
  if (!workspaceLogbar) {
    return;
  }
  if (!hasActiveLog()) {
    workspaceLogbar.style.display = 'none';
    workspaceLogbar.innerHTML = '';
    return;
  }
  const selected = state.tables.find(item => item.table_id === state.selectedTableId);
  const activeIndex = clampLogSampleIndex(state.logPlayback.sampleIndex);
  const maxIndex = Math.max((state.log?.sample_count || 1) - 1, 0);
  const overlaySummary = state.logOverlay?.summary
    ? `${state.logOverlay.mapped_sample_count} mapped · ${state.logOverlay.unmapped_sample_count} unmapped`
    : `${state.log?.sample_count || 0} samples`;
  workspaceLogbar.style.display = '';
  workspaceLogbar.innerHTML = `
    <div class="logbar">
      <div class="logbar__meta">
        <div class="logbar__title">Log Overlay</div>
        <div class="logbar__copy">${escapeHtml(state.log.filename || 'CSV log')} · ${overlaySummary}</div>
      </div>
      <div class="logbar__controls">
        <button class="btn btn-ghost btn-sm" id="log-step-back" ${activeIndex <= 0 ? 'disabled' : ''}>Prev</button>
        <button class="btn btn-primary btn-sm" id="log-play-toggle">${state.logPlayback.playing ? 'Pause' : 'Play'}</button>
        <button class="btn btn-ghost btn-sm" id="log-step-forward" ${activeIndex >= maxIndex ? 'disabled' : ''}>Next</button>
        <input id="log-scrubber" class="logbar__scrubber" type="range" min="0" max="${maxIndex}" step="1" value="${activeIndex}" />
        <label class="logbar__speed">
          <span>Speed</span>
          <select id="log-speed">
            ${[0.25, 0.5, 1, 2, 4, 8].map(value => `<option value="${value}"${Number(value) === Number(state.logPlayback.speed) ? ' selected' : ''}>${value}x</option>`).join('')}
          </select>
        </label>
        <span class="logbar__time">${formatLogTime(state.logOverlay?.current_sample_time ?? state.log?.time_range?.start ?? 0)}</span>
      </div>
      <div class="logbar__table">${selected ? escapeHtml(getDisplayTitle(selected)) : 'Select a table to overlay the log'}</div>
    </div>`;

  $('log-play-toggle')?.addEventListener('click', toggleLogPlayback);
  $('log-step-back')?.addEventListener('click', () => stepLogSample(-1));
  $('log-step-forward')?.addEventListener('click', () => stepLogSample(1));
  $('log-scrubber')?.addEventListener('input', async event => {
    state.logPlayback.sampleIndex = clampLogSampleIndex(Number(event.target.value));
    await refreshLogOverlay();
    renderResults();
  });
  $('log-speed')?.addEventListener('change', async event => {
    state.logPlayback.speed = Number(event.target.value) || 1;
    if (!state.preferences.log) {
      state.preferences.log = getLogPrefs();
    }
    state.preferences.log.playback_speed = state.logPlayback.speed;
    await savePreferences();
    if (state.logPlayback.playing) {
      stopLogPlayback();
      toggleLogPlayback();
    }
  });
}

function clampLogSampleIndex(value) {
  const maxIndex = Math.max((state.log?.sample_count || 1) - 1, 0);
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.max(0, Math.min(maxIndex, Math.round(numeric)));
}

function formatLogTime(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(3)}s` : '0.000s';
}

async function toggleLogPlayback() {
  if (!hasActiveLog()) {
    return;
  }
  if (state.logPlayback.playing) {
    stopLogPlayback();
    renderResults();
    return;
  }
  const delay = Math.max(30, Math.round(160 / Math.max(state.logPlayback.speed, 0.25)));
  state.logPlayback.playing = true;
  state.logPlayback.timer = window.setInterval(async () => {
    if (state.logPlayback.sampleIndex >= Math.max((state.log?.sample_count || 1) - 1, 0)) {
      stopLogPlayback();
      renderResults();
      return;
    }
    state.logPlayback.sampleIndex = clampLogSampleIndex(state.logPlayback.sampleIndex + 1);
    await refreshLogOverlay();
    renderResults();
  }, delay);
  renderResults();
}

async function stepLogSample(delta) {
  state.logPlayback.sampleIndex = clampLogSampleIndex((state.logPlayback.sampleIndex || 0) + delta);
  await refreshLogOverlay();
  renderResults();
}

async function parseLogFile(options = {}) {
  const silent = Boolean(options.silent);
  if (!state.files.log || !state.session?.session_id) {
    return;
  }
  const formData = new FormData();
  formData.append('session_id', state.session.session_id);
  formData.append('log', state.files.log);
  try {
    const response = await fetch('/api/log/parse', { method: 'POST', body: formData });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Could not parse CSV log.');
    }
    state.log = data.log || null;
    state.logOverlay = null;
    state.logPlayback.sampleIndex = 0;
    state.logPlayback.rangeStart = 0;
    state.logPlayback.rangeEnd = Math.max((state.log?.sample_count || 1) - 1, 0);
    syncLogPlaybackPrefs();
    const enabled = getEnabledGraphColumns();
    if (!enabled.length && state.log?.initial_traces?.length) {
      state.preferences.log = getLogPrefs();
      state.preferences.log.graph.enabled_columns = [...state.log.initial_traces];
      await savePreferences();
    }
    await refreshLogOverlay();
    renderResults();
    if (!silent) {
      showToast(`Loaded log CSV: ${state.log.filename}`);
    }
  } catch (error) {
    if (!silent) {
      showToast(error.message || 'Could not parse CSV log.');
    }
  }
}

function getEnabledGraphColumns() {
  return [...(((state.preferences.log || {}).graph || {}).enabled_columns || [])];
}

function resolveLogMapping(table) {
  const prefs = getLogPrefs();
  const saved = (prefs.axis_mappings || {})[getFamilyPreferenceKey(table)] || {};
  const mapping = {
    x_column: saved.x_column || '',
    y_column: saved.y_column || '',
    map_column: saved.map_column || '',
  };
  if (!mapping.x_column || !mapping.y_column) {
    const inferred = inferLogMapping(table);
    mapping.x_column = mapping.x_column || inferred.x_column;
    mapping.y_column = mapping.y_column || inferred.y_column;
    mapping.map_column = mapping.map_column || inferred.map_column;
  }
  return mapping;
}

function inferLogMapping(table) {
  const columns = state.log?.header || [];
  const title = String(table.title || '').toLowerCase();
  const family = String(table.family_title || '').toLowerCase();
  const bucket = String(table.workflow_bucket || '').toLowerCase();
  const xStats = getAxisLabelStats(table.x_labels || []);
  const yStats = getAxisLabelStats(table.y_labels || []);
  const find = candidates => {
    const normalized = columns.map(column => ({ original: column, normalized: normalizeText(column) }));
    for (const candidate of candidates) {
      const direct = normalized.find(entry => entry.normalized === normalizeText(candidate));
      if (direct) return direct.original;
    }
    for (const candidate of candidates) {
      const partial = normalized.find(entry => entry.normalized.includes(normalizeText(candidate)));
      if (partial) return partial.original;
    }
    return '';
  };
  let xColumn = '';
  let yColumn = '';
  if (looksLikeGearAxis(xStats) && looksLikeRpmAxis(yStats)) {
    xColumn = find(['Gear (-)', 'Gear']);
    yColumn = find(['RPM (rpm)', 'RPM']);
  } else if (looksLikeRpmAxis(xStats) && looksLikeLoadAxis(yStats)) {
    xColumn = find(['RPM (rpm)', 'RPM']);
    yColumn = find(['Load actual RAM', 'Load req. (%)', 'Load req.']);
  } else if (looksLikeRpmAxis(yStats) && looksLikeLoadAxis(xStats)) {
    xColumn = find(['Load actual RAM', 'Load req. (%)', 'Load req.']);
    yColumn = find(['RPM (rpm)', 'RPM']);
  } else if (title.includes('gear')) {
    xColumn = find(['Gear (-)', 'Gear']);
    yColumn = find(['RPM (rpm)', 'RPM']);
  } else {
    xColumn = find(['RPM (rpm)', 'RPM']);
    if (title.includes('ethanol') || title.includes('interpolation')) {
      xColumn = find(['Fuel Interpolation (-)', 'Fuel Scalar Interpolation (-)', 'Ethanol Content']);
    }
    if (title.includes('gear x rpm')) {
      xColumn = find(['Gear (-)', 'Gear']);
      yColumn = find(['RPM (rpm)', 'RPM']);
    }
  }
  if (!yColumn) {
    if (title.includes('gear x rpm')) {
      yColumn = find(['RPM (rpm)', 'RPM']);
    } else if (title.includes('gear')) {
      yColumn = find(['RPM (rpm)', 'RPM']);
    } else if (title.includes('ethanol')) {
      yColumn = find(['Load actual RAM', 'Load req. (%)']);
    } else if (bucket.includes('fuel') || bucket.includes('ignition') || bucket.includes('boost') || family.includes('fuel') || family.includes('timing') || family.includes('wgdc')) {
      yColumn = find(['Load actual RAM', 'Load req. (%)', 'Load req.']);
    }
  }
  const mapColumn = find(['Current Map (-)', 'Current Map']);
  return { x_column: xColumn, y_column: yColumn, map_column: mapColumn };
}

function getAxisLabelStats(labels) {
  const values = (labels || []).map(Number).filter(Number.isFinite);
  if (!values.length) {
    return { min: null, max: null };
  }
  return { min: Math.min(...values), max: Math.max(...values) };
}

function looksLikeRpmAxis(stats) {
  return Number.isFinite(stats?.max) && Number(stats.max) >= 400;
}

function looksLikeLoadAxis(stats) {
  return Number.isFinite(stats?.max) && Number(stats.max) >= 5 && Number(stats.max) <= 350;
}

function looksLikeGearAxis(stats) {
  return Number.isFinite(stats?.max) && Number(stats.max) <= 12;
}

async function updateLogMapping(table, field, value) {
  state.preferences.log = getLogPrefs();
  const familyKey = getFamilyPreferenceKey(table);
  if (!state.preferences.log.axis_mappings[familyKey]) {
    state.preferences.log.axis_mappings[familyKey] = {};
  }
  state.preferences.log.axis_mappings[familyKey][field] = value;
  await savePreferences();
  await refreshLogOverlay();
  renderResults();
}

async function toggleGraphColumn(column, enabled) {
  state.preferences.log = getLogPrefs();
  const current = new Set(getEnabledGraphColumns());
  if (enabled) {
    current.add(column);
  } else {
    current.delete(column);
  }
  state.preferences.log.graph.enabled_columns = Array.from(current);
  await savePreferences();
  await refreshLogOverlay();
  renderResults();
}

async function refreshLogOverlay() {
  if (!hasActiveLog() || !state.selectedTableId) {
    state.logOverlay = null;
    return;
  }
  const table = state.tables.find(item => item.table_id === state.selectedTableId);
  if (!table || !table.rows || !table.cols) {
    state.logOverlay = null;
    return;
  }
  const mapping = resolveLogMapping(table);
  const needsX = Number(table.cols || 0) > 1;
  const needsY = Number(table.rows || 0) > 1;
  if ((needsX && !mapping.x_column) || (needsY && !mapping.y_column)) {
    state.logOverlay = { error: 'Select the required log axis columns for this table.', mapping };
    return;
  }
  const response = await fetch('/api/log/overlay', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: state.session.session_id,
      table_id: table.table_id,
      sample_index: clampLogSampleIndex(state.logPlayback.sampleIndex),
      range_start: state.logPlayback.rangeStart,
      range_end: state.logPlayback.rangeEnd,
      mapping,
      trace_columns: getEnabledGraphColumns(),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    state.logOverlay = { error: data.error || 'Could not build log overlay.', mapping };
    return;
  }
  state.logOverlay = data.overlay;
  state.logOverlay.mapping = data.mapping;
}

function buildCard(table, { titleOverride = null } = {}) {
  const card = document.createElement('article');
  const shouldBeOpen = shouldOpen('cards', table.table_id, false);
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
      <button class="card-import" type="button" title="Import AI-generated table text">Import AI</button>
      <button class="btn-copy card-copy" type="button" title="Copy for AI">Copy for AI</button>
    </div>
    <div class="card-body">
      ${buildBody(table, { includeAxisEditors: false })}
    </div>`;

  card.querySelector('.card-header').addEventListener('click', event => {
    if (event.target.closest('.card-expander, .card-copy, .card-edit-meta, .card-import')) {
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

  card.querySelector('.card-import').addEventListener('click', event => {
    event.stopPropagation();
    openImportModal(table.table_id);
  });

  card.querySelectorAll('[data-log-mapping]').forEach(select => {
    select.addEventListener('change', async event => {
      event.stopPropagation();
      await updateLogMapping(table, event.target.dataset.logMapping, event.target.value);
    });
  });

  if (table.editable && !table.error) {
    wireEditableCard(card, table);
  }

  return card;
}

function renderInspector(visibleTables = []) {
  const selected = visibleTables.find(table => table.table_id === state.selectedTableId) || null;

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
  const favoriteLabel = isFavoriteTable(selected) ? 'Unstar' : 'Star';

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
      <button class="btn btn-ghost btn-sm inspector-favorite" type="button">${favoriteLabel}</button>
    </div>`;

  const descriptionMarkup = buildDescriptionBlock(selected) || '<div class="desc-short">No description available yet for this table.</div>';
  const axisMarkup = buildAxisEditors(selected) || '<div class="desc-short">No editable embedded breakpoints are available for this table.</div>';
  const familyMeta = selected.family_title && selected.family_title !== selected.title
    ? `<div class="desc-short">Family: ${escapeHtml(selected.family_title)}</div>`
    : '';
  const categoryPath = (selected.primary_category_path || []).length
    ? `<div class="desc-long">${escapeHtml((selected.primary_category_path || []).join(' > '))}</div>`
    : '';
  const favoriteFolders = getAssignedFoldersMarkup(selected);
  const graphMarkup = buildLogGraphSection(selected);

  inspectorBody.innerHTML = `
    ${graphMarkup ? `<section class="inspector-section inspector-section--graph"><div class="inspector-section__title">Log Graph</div>${graphMarkup}</section>` : ''}
    <section class="inspector-section">
      <div class="inspector-section__title">Description</div>
      ${descriptionMarkup}
    </section>
    <section class="inspector-section">
      <div class="inspector-section__title">Saved Organization</div>
      ${favoriteFolders}
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
  inspectorHeader.querySelector('.inspector-favorite').addEventListener('click', () => toggleFavoriteForTable(selected));
  inspectorBody.querySelectorAll('[data-graph-column]').forEach(input => {
    input.addEventListener('change', async event => {
      await toggleGraphColumn(event.target.dataset.graphColumn, event.target.checked);
    });
  });
  wireEditableScope(inspectorPanel, selected, { hasCellGrid: false });
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
  const formState = state.editorForms[table.table_id] || state.lastEditorPreset || { operation: 'add_constant', value: 1 };
  const showValue = !String(formState.operation).startsWith('interpolate');
  const previewSummary = preview
    ? `<div class="editor-preview-copy">${preview.summary.changed_cell_count} cells previewed · delta ${formatNumber(preview.summary.min_delta)} to ${formatNumber(preview.summary.max_delta)}</div>`
    : '<div class="editor-preview-copy">Select a region, then preview or apply an operation.</div>';

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
  if (isScalarTable(table)) {
    return `${buildInlineEditorBar(table)}${buildLogOverlayBar(table)}${buildScalar(table)}`;
  }
  return `${buildInlineEditorBar(table)}${buildLogOverlayBar(table)}${buildTable(table)}`;
}

function buildLogGraphSection(table) {
  if (!hasActiveLog()) {
    return '';
  }
  if (state.logOverlay?.error) {
    return `<div class="desc-short">${escapeHtml(state.logOverlay.error)}</div>`;
  }
  const graph = state.logOverlay?.graph;
  if (!graph || !Array.isArray(graph.series) || !graph.series.length) {
    return '<div class="desc-short">Pick graph traces to see the loaded CSV log plotted here.</div>';
  }
  const toggles = buildGraphToggleMarkup();
  const chart = buildLogChartSvg(graph);
  return `
    <div class="log-graph">
      <div class="log-graph__toggles">${toggles}</div>
      <div class="log-graph__chart">${chart}</div>
    </div>`;
}

function buildGraphToggleMarkup() {
  const columns = state.log?.header || [];
  const enabled = new Set(getEnabledGraphColumns());
  const preferred = new Set([...(state.log?.initial_traces || []), ...(enabled || [])]);
  const options = columns.filter(column => preferred.has(column)).concat(columns.filter(column => !preferred.has(column))).slice(0, 14);
  return options.map(column => `
    <label class="graph-toggle">
      <input type="checkbox" data-graph-column="${escapeHtml(column)}" ${enabled.has(column) ? 'checked' : ''} />
      <span>${escapeHtml(column)}</span>
    </label>
  `).join('');
}

function buildLogChartSvg(graph) {
  const width = 300;
  const height = 180;
  const padding = { top: 10, right: 10, bottom: 18, left: 10 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const timeValues = graph.times || [];
  if (!timeValues.length) {
    return '<div class="desc-short">No plottable log samples in the current range.</div>';
  }
  const timeMin = Math.min(...timeValues);
  const timeMax = Math.max(...timeValues);
  const colors = ['#7dd3fc', '#a78bfa', '#f59e0b', '#34d399', '#f87171', '#f472b6', '#facc15'];
  const lines = graph.series.map((series, index) => {
    const numericValues = series.values.filter(value => Number.isFinite(value));
    if (!numericValues.length || series.min === null || series.max === null) {
      return '';
    }
    const range = series.max - series.min || 1;
    const points = [];
    series.values.forEach((value, pointIndex) => {
      if (!Number.isFinite(value)) {
        return;
      }
      const x = padding.left + (((timeValues[pointIndex] - timeMin) / Math.max(timeMax - timeMin, 0.0001)) * plotWidth);
      const y = padding.top + (plotHeight - (((value - series.min) / range) * plotHeight));
      points.push(`${x.toFixed(2)},${y.toFixed(2)}`);
    });
    if (points.length < 2) {
      return '';
    }
    return `<polyline fill="none" stroke="${colors[index % colors.length]}" stroke-width="2" points="${points.join(' ')}" />`;
  }).join('');

  let cursorLine = '';
  const overlayTime = Number(state.logOverlay?.current_sample_time);
  if (Number.isFinite(overlayTime)) {
    const cursorX = padding.left + (((overlayTime - timeMin) / Math.max(timeMax - timeMin, 0.0001)) * plotWidth);
    cursorLine = `<line x1="${cursorX.toFixed(2)}" x2="${cursorX.toFixed(2)}" y1="${padding.top}" y2="${padding.top + plotHeight}" stroke="rgba(248,250,252,0.8)" stroke-dasharray="4 4" />`;
  }

  return `
    <svg class="log-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Log chart">
      <rect x="${padding.left}" y="${padding.top}" width="${plotWidth}" height="${plotHeight}" rx="10" fill="rgba(15,23,42,0.6)" stroke="rgba(148,163,184,0.14)" />
      ${cursorLine}
      ${lines}
    </svg>`;
}

function buildInlineEditorBar(table) {
  if (!table.editable || table.error) {
    return '';
  }
  return buildEditorPanel(table);
}

function buildScalar(table) {
  const previewLookup = buildPreviewLookup(table.table_id);
  const previewCell = previewLookup.get('0:0');
  const scalarValue = previewCell ? previewCell.after : (table.grid?.[0]?.[0] ?? 0);
  const first = formatNumber(scalarValue);
  const second = table.has_diff ? formatNumber(table.grid2?.[0]?.[0] ?? 0) : null;
  const selected = selectionContains(state.selections[table.table_id], 0, 0);
  const scalarClasses = ['scalar-display', 'scalar-display--editable'];
  if (isActiveLogCell(table, 0, 0)) scalarClasses.push('is-log-active');
  if (selected) scalarClasses.push('is-selected');
  if (previewCell) scalarClasses.push('is-preview');
  if (second === null || first === second) {
    return `<div class="${scalarClasses.join(' ')}" data-row="0" data-col="0"><span class="scalar-value">${first}</span>${table.units ? `<span class="scalar-units">${escapeHtml(table.units)}</span>` : ''}</div>`;
  }
  return `<div class="${scalarClasses.join(' ')}" data-row="0" data-col="0"><span class="scalar-value">${first}</span>${table.units ? `<span class="scalar-units">${escapeHtml(table.units)}</span>` : ''}<span class="scalar-diff"><span class="arrow">-></span><span class="new-val">${second}</span></span></div>`;
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
      const previewChange = previewLookup.get(`${row}:${col}`);
      const displayValue = previewChange ? previewChange.after : ((grid[row] || [])[col] ?? 0);
      const current = formatNumber(displayValue);
      const cellClasses = ['table-cell'];
      const heatmapStyle = buildHeatmapStyle(table, row, col);
      if (isActiveLogCell(table, row, col)) {
        cellClasses.push('is-log-active');
      }
      const dwellRatio = getLogDwellRatio(table, row, col);
      if (dwellRatio > 0) {
        cellClasses.push('has-log-dwell');
      }
      if (selectionContains(selection, row, col)) {
        cellClasses.push('is-selected');
        cellClasses.push(...selectionEdgeClasses(selection, row, col));
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
  const activeAxis = state.axisDrafts[table.table_id]?.activeAxis || (xEditor ? 'x' : 'y');
  return `
    <div class="axis-editor-wrap" data-table-id="${escapeHtml(table.table_id)}">
      <div class="axis-toggle">
        ${xEditor ? `<button class="axis-toggle__btn${activeAxis === 'x' ? ' is-active' : ''}" type="button" data-axis-tab="x">X Axis</button>` : ''}
        ${yEditor ? `<button class="axis-toggle__btn${activeAxis === 'y' ? ' is-active' : ''}" type="button" data-axis-tab="y">Y Axis</button>` : ''}
      </div>
      ${xEditor ? `<div class="axis-editor-panel${activeAxis === 'x' ? ' is-active' : ''}" data-axis-panel="x">${xEditor}</div>` : ''}
      ${yEditor ? `<div class="axis-editor-panel${activeAxis === 'y' ? ' is-active' : ''}" data-axis-panel="y">${yEditor}</div>` : ''}
    </div>`;
}

function buildLogOverlayBar(table) {
  if (!hasActiveLog() || state.selectedTableId !== table.table_id) {
    return '';
  }
  const mapping = (state.logOverlay && state.logOverlay.mapping) || resolveLogMapping(table);
  const columns = state.log?.header || [];
  const overlay = state.logOverlay;
  const needsX = Number(table.cols || 0) > 1;
  const needsY = Number(table.rows || 0) > 1;
  const activeCell = overlay?.active_cell;
  const activeCellLabel = activeCell ? `R${activeCell.row + 1} · C${activeCell.col + 1}` : 'No active cell';
  const currentX = overlay?.current_values?.x_value;
  const currentY = overlay?.current_values?.y_value;
  const currentMap = overlay?.current_values?.map_value;
  const mappingWarning = ((needsX && !mapping.x_column) || (needsY && !mapping.y_column))
    ? `<div class="log-overlay-bar__warning">Choose ${needsX && needsY ? 'X and Y' : (needsX ? 'an X' : 'a Y')} log column${needsX && needsY ? 's' : ''} to enable the overlay for this table.</div>`
    : '';
  return `
    <div class="log-overlay-bar${mappingWarning ? ' has-warning' : ''}" data-log-table-id="${escapeHtml(table.table_id)}">
      <div class="log-overlay-bar__row">
        <div class="log-overlay-bar__summary">
          <span class="log-chip">${escapeHtml(activeCellLabel)}</span>
          <span class="log-chip">X ${escapeHtml(formatMaybeNumber(currentX))}</span>
          <span class="log-chip">Y ${escapeHtml(formatMaybeNumber(currentY))}</span>
          ${currentMap !== undefined ? `<span class="log-chip">Map ${escapeHtml(formatMaybeNumber(currentMap))}</span>` : ''}
          <span class="log-chip">${escapeHtml(`${overlay?.mapped_sample_count || 0} mapped / ${overlay?.unmapped_sample_count || 0} unmapped`)}</span>
        </div>
      </div>
      <div class="log-overlay-bar__row">
        ${needsX ? `<label class="log-mapping-field">
          <span>X Axis Source</span>
          <select data-log-mapping="x_column">
            ${buildLogColumnOptions(columns, mapping.x_column)}
          </select>
        </label>` : ''}
        ${needsY ? `<label class="log-mapping-field">
          <span>Y Axis Source</span>
          <select data-log-mapping="y_column">
            ${buildLogColumnOptions(columns, mapping.y_column)}
          </select>
        </label>` : ''}
        <label class="log-mapping-field">
          <span>Map Filter</span>
          <select data-log-mapping="map_column">
            <option value="">No map filter</option>
            ${buildLogColumnOptions(columns, mapping.map_column)}
          </select>
        </label>
      </div>
      ${mappingWarning}
    </div>`;
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
      <div class="axis-editor__inputs axis-editor__inputs--vertical">
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
  if (Array.isArray(selection.cells) && selection.cells.length) {
    const count = selection.cells.length;
    if (!selection.is_rectangular) {
      return `Selection: ${count} cells across ${table.rows}x${table.cols}`;
    }
  }
  return `Selection: R${selection.row_start + 1}-${selection.row_end + 1} · C${selection.col_start + 1}-${selection.col_end + 1} of ${table.rows}x${table.cols}`;
}

function buildPreviewLookup(tableId) {
  const preview = state.previews[tableId];
  const lookup = new Map();
  if (!preview) {
    return lookup;
  }
  (preview.changes || []).forEach(change => lookup.set(`${change.row}:${change.col}`, change));
  return lookup;
}

function makeRectSelection(anchorRow, anchorCol, row, col) {
  return {
    row_start: Math.min(anchorRow, row),
    row_end: Math.max(anchorRow, row),
    col_start: Math.min(anchorCol, col),
    col_end: Math.max(anchorCol, col),
    is_rectangular: true,
  };
}

function normalizeSelection(selection) {
  if (!selection) {
    return null;
  }
  if (Array.isArray(selection.cells) && selection.cells.length) {
    const unique = [];
    const seen = new Set();
    selection.cells.forEach(cell => {
      const row = Number(cell.row);
      const col = Number(cell.col);
      const key = `${row}:${col}`;
      if (!Number.isFinite(row) || !Number.isFinite(col) || seen.has(key)) {
        return;
      }
      seen.add(key);
      unique.push({ row, col });
    });
    if (!unique.length) {
      return null;
    }
    const rows = unique.map(cell => cell.row);
    const cols = unique.map(cell => cell.col);
    const rowStart = Math.min(...rows);
    const rowEnd = Math.max(...rows);
    const colStart = Math.min(...cols);
    const colEnd = Math.max(...cols);
    const area = (rowEnd - rowStart + 1) * (colEnd - colStart + 1);
    return {
      row_start: rowStart,
      row_end: rowEnd,
      col_start: colStart,
      col_end: colEnd,
      cells: unique,
      is_rectangular: unique.length === area,
    };
  }
  return {
    row_start: Number(selection.row_start),
    row_end: Number(selection.row_end),
    col_start: Number(selection.col_start),
    col_end: Number(selection.col_end),
    is_rectangular: true,
  };
}

function toggleCellSelection(selection, row, col) {
  const current = selectionToCells(selection);
  const key = `${row}:${col}`;
  if (current.has(key)) {
    current.delete(key);
  } else {
    current.set(key, { row, col });
  }
  return normalizeSelection({ cells: Array.from(current.values()) });
}

function selectionToCells(selection) {
  const cells = new Map();
  if (!selection) {
    return cells;
  }
  if (Array.isArray(selection.cells) && selection.cells.length) {
    selection.cells.forEach(cell => {
      cells.set(`${cell.row}:${cell.col}`, { row: Number(cell.row), col: Number(cell.col) });
    });
    return cells;
  }
  for (let row = selection.row_start; row <= selection.row_end; row += 1) {
    for (let col = selection.col_start; col <= selection.col_end; col += 1) {
      cells.set(`${row}:${col}`, { row, col });
    }
  }
  return cells;
}

function selectionEdgeClasses(selection, row, col) {
  if (!selectionContains(selection, row, col)) {
    return [];
  }
  if (!selection?.is_rectangular) {
    return ['is-selected-cell'];
  }
  const classes = [];
  if (row === selection.row_start) classes.push('is-selection-top');
  if (row === selection.row_end) classes.push('is-selection-bottom');
  if (col === selection.col_start) classes.push('is-selection-left');
  if (col === selection.col_end) classes.push('is-selection-right');
  return classes;
}

function buildHeatmapStyle(table, row, col) {
  const stats = getGridStats(table.grid || []);
  const layers = [];
  if (stats && stats.max > stats.min) {
    const value = Number((table.grid[row] || [])[col]);
    if (Number.isFinite(value)) {
      const ratio = Math.max(0, Math.min(1, (value - stats.min) / (stats.max - stats.min)));
      const hue = 120 - (ratio * 120);
      const alpha = 0.2 + (ratio * 0.18);
      layers.push(`linear-gradient(180deg, hsla(${hue}, 72%, 45%, ${alpha}) 0%, hsla(${hue}, 70%, 32%, ${alpha * 0.95}) 100%)`);
    }
  }
  const dwellRatio = getLogDwellRatio(table, row, col);
  if (dwellRatio > 0) {
    const dwellAlpha = 0.12 + (dwellRatio * 0.28);
    layers.push(`linear-gradient(180deg, rgba(125, 211, 252, ${dwellAlpha}) 0%, rgba(59, 130, 246, ${dwellAlpha * 0.92}) 100%)`);
  }
  return layers.length ? `background: ${layers.join(', ')};` : '';
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

function getLogDwellRatio(table, row, col) {
  if (!state.logOverlay || state.selectedTableId !== table.table_id || !Array.isArray(state.logOverlay.dwell_matrix)) {
    return 0;
  }
  const dwell = Number((state.logOverlay.dwell_matrix[row] || [])[col] || 0);
  const maxDwell = Number(state.logOverlay.summary?.max_dwell || 0);
  if (!Number.isFinite(dwell) || !Number.isFinite(maxDwell) || maxDwell <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(1, dwell / maxDwell));
}

function isActiveLogCell(table, row, col) {
  const active = state.logOverlay?.active_cell;
  return Boolean(active && state.selectedTableId === table.table_id && active.row === row && active.col === col);
}

function buildLogColumnOptions(columns, selected) {
  return (columns || []).map(column => (
    `<option value="${escapeHtml(column)}"${column === selected ? ' selected' : ''}>${escapeHtml(column)}</option>`
  )).join('');
}

function formatMaybeNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatNumber(numeric) : 'n/a';
}

function selectionContains(selection, row, col) {
  if (!selection) {
    return false;
  }
  if (Array.isArray(selection.cells) && selection.cells.length) {
    return selection.cells.some(cell => Number(cell.row) === row && Number(cell.col) === col);
  }
  return row >= selection.row_start
    && row <= selection.row_end
    && col >= selection.col_start
    && col <= selection.col_end;
}

function isTextEditingTarget(target) {
  const element = target instanceof Element ? target : null;
  return Boolean(element?.closest('input, textarea, select, [contenteditable="true"], .inline-cell-input'));
}

function getSelectedEditableTable() {
  if (!state.selectedTableId) {
    return null;
  }
  const table = state.tables.find(item => item.table_id === state.selectedTableId);
  if (!table || !table.editable || table.error || table.has_diff) {
    return null;
  }
  const selection = normalizeSelection(state.selections[table.table_id]);
  if (!selection) {
    return null;
  }
  return { table, selection };
}

function getSelectionValueBlock(table, selection) {
  if (!selection?.is_rectangular) {
    return null;
  }
  const rows = [];
  for (let row = selection.row_start; row <= selection.row_end; row += 1) {
    const values = [];
    for (let col = selection.col_start; col <= selection.col_end; col += 1) {
      values.push(Number((table.grid[row] || [])[col]));
    }
    rows.push(values);
  }
  return rows;
}

function serializeValueBlock(values) {
  return values.map(row => row.map(value => String(value)).join('\t')).join('\n');
}

function parseValueBlock(text) {
  const rows = String(text || '')
    .trim()
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => line.split(/\t|,/).map(value => Number(value.trim())));
  if (!rows.length || rows.some(row => !row.length || row.some(value => !Number.isFinite(value)))) {
    return null;
  }
  return rows;
}

function handleTableCopy(event) {
  if (isTextEditingTarget(event.target)) {
    return;
  }
  const selected = getSelectedEditableTable();
  if (!selected) {
    return;
  }
  const values = getSelectionValueBlock(selected.table, selected.selection);
  if (!values) {
    showToast('Copy needs one rectangular selection.');
    return;
  }
  const text = serializeValueBlock(values);
  state.tableClipboard = {
    tableId: selected.table.table_id,
    values,
    text,
  };
  event.clipboardData.setData('text/plain', text);
  event.preventDefault();
  showToast(values.length === 1 ? 'Copied row.' : 'Copied cells.');
}

async function handleTablePaste(event) {
  if (isTextEditingTarget(event.target)) {
    return;
  }
  const selected = getSelectedEditableTable();
  if (!selected) {
    return;
  }
  const text = event.clipboardData?.getData('text/plain') || '';
  const values = parseValueBlock(text) || state.tableClipboard?.values;
  if (!values || !values.length) {
    return;
  }
  event.preventDefault();
  await pasteValueBlock(selected.table, selected.selection, values);
}

async function pasteValueBlock(table, selection, values) {
  if (!state.session?.session_id) {
    showToast('No active editing session.');
    return;
  }
  const startRow = selection.row_start;
  const startCol = selection.col_start;
  const response = await fetch(`/api/session/${state.session.session_id}/paste`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      table_id: table.table_id,
      start_row: startRow,
      start_col: startCol,
      values,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Paste failed.');
    return;
  }
  state.session = data.session || state.session;
  state.previews[table.table_id] = data.preview || null;
  state.tables = state.tables.map(item => (item.table_id === table.table_id ? data.table : item));
  if (data.preview?.selection) {
    state.selections[table.table_id] = normalizeSelection(data.preview.selection);
    state.selectionAnchors[table.table_id] = {
      row: data.preview.selection.row_start,
      col: data.preview.selection.col_start,
    };
  }
  updateDraftToolbar();
  renderList();
  showToast('Pasted values into draft.');
}

function wireEditableCard(card, table) {
  const tableId = table.table_id;
  wireEditableScope(card, table, { hasCellGrid: true });

  const interactiveCells = [...card.querySelectorAll('.table-cell, .scalar-display--editable')];
  interactiveCells.forEach(cell => {
    cell.addEventListener('mousedown', event => {
      if (event.button !== 0) {
        return;
      }
      const row = Number(cell.dataset.row);
      const col = Number(cell.dataset.col);
      const anchor = state.selectionAnchors[tableId] || { row, col };
      state.selectedTableId = tableId;

      if (event.ctrlKey || event.metaKey) {
        dragSelection = null;
        state.selections[tableId] = toggleCellSelection(state.selections[tableId], row, col);
      } else if (event.shiftKey) {
        dragSelection = null;
        state.selections[tableId] = normalizeSelection(makeRectSelection(anchor.row, anchor.col, row, col));
      } else {
        dragSelection = { tableId, anchorRow: row, anchorCol: col };
        state.selections[tableId] = normalizeSelection(makeRectSelection(row, col, row, col));
        state.selectionAnchors[tableId] = { row, col };
      }
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
      state.selections[tableId] = normalizeSelection(makeRectSelection(dragSelection.anchorRow, dragSelection.anchorCol, row, col));
      delete state.previews[tableId];
      refreshEditableCard(card, table);
    });

    cell.addEventListener('dblclick', event => {
      event.preventDefault();
      event.stopPropagation();
      beginInlineCellEdit(cell, table);
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
    state.lastEditorPreset = {
      operation: operationSelect.value,
      value,
    };
      saveEditorPrefs();
      valueWrap.style.display = operationSelect.value.startsWith('interpolate') ? 'none' : '';
    };

    syncFormState();
    operationSelect.addEventListener('change', syncFormState);
    if (valueInput) {
      valueInput.addEventListener('input', syncFormState);
    }

    panel.querySelector('[data-editor-action="select-all"]').addEventListener('click', event => {
      event.stopPropagation();
      state.selections[tableId] = normalizeSelection({
        row_start: 0,
        row_end: Math.max(table.rows - 1, 0),
        col_start: 0,
        col_end: Math.max(table.cols - 1, 0),
      });
      state.selectionAnchors[tableId] = { row: 0, col: 0 };
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
      delete state.selectionAnchors[tableId];
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

  container.querySelectorAll('[data-axis-tab]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      if (!state.axisDrafts[tableId]) {
        state.axisDrafts[tableId] = {};
      }
      state.axisDrafts[tableId].activeAxis = button.dataset.axisTab;
      renderList();
    });
  });

  container.querySelectorAll('.axis-input').forEach(input => {
    input.addEventListener('input', event => {
      const axisName = event.target.dataset.axisName;
      const index = Number(event.target.dataset.axisIndex);
      const values = [...getAxisDraft(table, axisName)];
      values[index] = Number(event.target.value);
      state.axisDrafts[tableId][axisName] = values;
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        input.blur();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        const axisName = input.dataset.axisName;
        const index = Number(input.dataset.axisIndex);
        input.value = getAxisDraft(table, axisName)[index];
        input.blur();
      }
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
  card.querySelectorAll('.table-cell, .scalar-display--editable').forEach(cell => {
    const row = Number(cell.dataset.row);
    const col = Number(cell.dataset.col);
    const selected = selectionContains(selection, row, col);
    cell.classList.toggle('is-selected', selected);
    cell.classList.toggle('is-preview', previewLookup.has(`${row}:${col}`));
    ['is-selection-top', 'is-selection-bottom', 'is-selection-left', 'is-selection-right', 'is-selected-cell']
      .forEach(name => cell.classList.remove(name));
    if (selected) {
      selectionEdgeClasses(selection, row, col).forEach(name => cell.classList.add(name));
    }
    if (cell.classList.contains('table-cell')) {
      const previewChange = previewLookup.get(`${row}:${col}`);
      cell.textContent = formatNumber(previewChange ? previewChange.after : ((table.grid[row] || [])[col] ?? 0));
    } else if (cell.classList.contains('scalar-display--editable')) {
      const previewChange = previewLookup.get('0:0');
      const scalarValue = previewChange ? previewChange.after : (table.grid?.[0]?.[0] ?? 0);
      const valueNode = cell.querySelector('.scalar-value');
      if (valueNode) {
        valueNode.textContent = formatNumber(scalarValue);
      }
    }
  });
}

function beginInlineCellEdit(cell, table) {
  if (cell.querySelector('.inline-cell-input') || table.has_diff) {
    return;
  }
  const row = Number(cell.dataset.row);
  const col = Number(cell.dataset.col);
  const current = (table.grid[row] || [])[col];
  const original = Number.isFinite(Number(current)) ? String(current) : '';
  cell.dataset.originalValue = cell.innerHTML;
  cell.classList.add('is-editing');
  cell.innerHTML = `<input class="inline-cell-input" type="number" step="any" value="${escapeHtml(original)}" />`;
  const input = cell.querySelector('.inline-cell-input');
  input.focus();
  input.select();

  const cancel = () => {
    cell.classList.remove('is-editing');
    cell.innerHTML = cell.dataset.originalValue || escapeHtml(formatNumber(current));
  };

  input.addEventListener('keydown', async event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      const value = Number(input.value);
      if (!Number.isFinite(value)) {
        showToast('Enter a valid numeric value.');
        return;
      }
      state.selections[table.table_id] = normalizeSelection({ row_start: row, row_end: row, col_start: col, col_end: col });
      state.selectionAnchors[table.table_id] = { row, col };
      state.editorForms[table.table_id] = { operation: 'set_cells', value };
      state.lastEditorPreset = { operation: 'set_cells', value };
      saveEditorPrefs();
      await runEditorAction(table.table_id, false);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancel();
    }
  });

  input.addEventListener('blur', () => {
    if (cell.classList.contains('is-editing')) {
      cancel();
    }
  });
}

async function runEditorAction(tableId, previewOnly) {
  if (!state.session?.session_id) {
    showToast('No active editing session.');
    return;
  }
  const selection = normalizeSelection(state.selections[tableId]);
  if (!selection) {
    showToast('Select a table region first.');
    return;
  }

  const formState = state.editorForms[tableId] || state.lastEditorPreset || { operation: 'add_constant', value: 1 };
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
    const currentCard = document.querySelector(`.table-card[data-table-id="${CSS.escape(tableId)}"]`);
    const currentTable = state.tables.find(item => item.table_id === tableId);
    if (currentCard && currentTable) {
      refreshEditableCard(currentCard, currentTable);
    } else {
      renderList();
    }
    showToast('Preview ready.');
    return;
  }

  state.session = data.session || state.session;
  state.previews[tableId] = data.preview || null;
  state.tables = state.tables.map(table => (table.table_id === tableId ? data.table : table));
  if (state.selectedTableId === tableId && hasActiveLog()) {
    await refreshLogOverlay();
  }
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
  if (state.selectedTableId === tableId && hasActiveLog()) {
    await refreshLogOverlay();
  }
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
  if (hasActiveLog()) {
    await refreshLogOverlay();
  }
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
  if (hasActiveLog()) {
    await refreshLogOverlay();
  }
  updateDraftToolbar();
  renderList();
  showToast('Draft reset to the original BIN.');
}

async function exportDraft() {
  if (!state.session?.session_id) {
    return;
  }
  const response = await fetch(`/api/session/${state.session.session_id}/export-bin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stem: stripExtension(state.files.bin?.name || state.session?.source_bin_name || 'edited-bin') }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    showToast(data.error || 'Export failed.');
    return;
  }

  const blob = await response.blob();
  const disposition = response.headers.get('content-disposition') || '';
  const exportLogPath = response.headers.get('X-Export-Log-Path');
  const match = disposition.match(/filename=\"?([^\";]+)\"?/i);
  const filename = match ? match[1] : 'edited-bin.bin';
  downloadBlob(blob, filename);
  showToast(exportLogPath ? `BIN exported. Log saved to ${exportLogPath}` : 'BIN exported.');
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

async function saveDraftSession() {
  if (!state.session?.session_id) {
    showToast('No active draft to save.');
    return;
  }
  const response = await fetch(`/api/session/${state.session.session_id}/snapshot`);
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Failed to save draft session.');
    return;
  }
  const wrapper = {
    saved_at: new Date().toISOString(),
    payload: data.payload,
    file_names: {
      xdf: state.files.xdf?.name || state.session?.source_xdf_name || '',
      bin: state.files.bin?.name || state.session?.source_bin_name || '',
    },
  };
  window.localStorage.setItem(DRAFT_SESSION_STORAGE_KEY, JSON.stringify(wrapper));
  showToast('Draft session saved.');
}

async function restoreDraftSession() {
  const raw = window.localStorage.getItem(DRAFT_SESSION_STORAGE_KEY);
  if (!raw) {
    showToast('No saved draft session found.');
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    showToast('Saved draft is corrupted.');
    return;
  }
  const response = await fetch('/api/session/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload: parsed.payload }),
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Failed to restore draft.');
    return;
  }
  state.tables = data.tables || [];
  state.categories = data.categories || {};
  state.session = data.session || null;
  state.preferences = data.preferences || state.preferences;
  state.files = {
    xdf: state.files.xdf,
    bin: state.files.bin,
    log: state.files.log,
  };
  const fileNames = parsed.file_names || {};
  $('status-xdf').textContent = fileNames.xdf || state.session?.source_xdf_name || 'Restored draft XDF';
  $('status-bin').textContent = fileNames.bin || state.session?.source_bin_name || 'Restored draft BIN';
  state.selectedTableId = state.selectedTableId && state.tables.some(table => table.table_id === state.selectedTableId)
    ? state.selectedTableId
    : state.tables[0]?.table_id || null;
  if (state.files.log) {
    await parseLogFile({ silent: true });
  }
  updateDraftToolbar();
  renderList();
  showToast('Draft session restored.');
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

async function loadPreferences() {
  try {
    const response = await fetch('/api/preferences');
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    state.preferences = normalizePreferences(data);
    syncLogPlaybackPrefs();
    renderFolderList();
  } catch (_error) {
    // Keep defaults if preferences are unavailable.
  }
}

function normalizePreferences(payload) {
  const favorites = payload?.favorites || {};
  const log = payload?.log || {};
  return {
    favorites: {
      tables: Array.isArray(favorites.tables) ? favorites.tables : [],
      families: Array.isArray(favorites.families) ? favorites.families : [],
    },
    folders: Array.isArray(payload?.folders) ? payload.folders : [],
    assignments: payload?.assignments && typeof payload.assignments === 'object' ? payload.assignments : {},
    log: {
      axis_mappings: log.axis_mappings && typeof log.axis_mappings === 'object' ? log.axis_mappings : {},
      graph: {
        enabled_columns: Array.isArray(log.graph?.enabled_columns) ? log.graph.enabled_columns : [],
      },
      playback_speed: Number.isFinite(Number(log.playback_speed)) ? Number(log.playback_speed) : 1,
    },
  };
}

async function savePreferences() {
  const response = await fetch('/api/preferences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.preferences),
  });
  const data = await response.json().catch(() => state.preferences);
  if (response.ok) {
    state.preferences = normalizePreferences(data);
    renderFolderList();
    renderList();
  } else {
    showToast(data.error || 'Could not save preferences.');
  }
}

function getTablePreferenceKey(table) {
  return table.preference_key || normalizeText(table.title || table.table_id);
}

function getFamilyPreferenceKey(table) {
  return table.family_key || normalizeText(table.family_title || table.title || table.table_id);
}

function isFavoriteTable(table) {
  return state.preferences.favorites.tables.includes(getTablePreferenceKey(table))
    || state.preferences.favorites.families.includes(getFamilyPreferenceKey(table));
}

function getFavoriteTables(tables) {
  return [...tables].filter(isFavoriteTable).sort(compareTables);
}

function toggleSelectedFavorite() {
  const table = state.tables.find(item => item.table_id === state.selectedTableId);
  if (table) {
    toggleFavoriteForTable(table);
  }
}

function toggleFavoriteForTable(table) {
  const key = getTablePreferenceKey(table);
  const items = new Set(state.preferences.favorites.tables);
  if (items.has(key)) {
    items.delete(key);
    showToast('Removed from favorites.');
  } else {
    items.add(key);
    showToast('Added to favorites.');
  }
  state.preferences.favorites.tables = [...items];
  savePreferences();
}

function renderFolderList() {
  if (!folderList) {
    return;
  }
  const selected = state.tables.find(item => item.table_id === state.selectedTableId);
  const selectedKey = selected ? getTablePreferenceKey(selected) : null;
  const assignments = state.preferences.assignments || {};
  btnClearFolderFilter.style.display = state.selectedFolderId ? '' : 'none';
  folderList.innerHTML = '';
  if (!(state.preferences.folders || []).length) {
    folderList.innerHTML = '<div class="folder-empty">No folders yet. Create one to group your favorite tables for new-map workflows.</div>';
    return;
  }
  state.preferences.folders.forEach(folder => {
    const row = document.createElement('div');
    row.className = 'folder-row';
    const selectedFamilyKey = selected ? getFamilyPreferenceKey(selected) : null;
    const assignedKeys = Array.isArray(assignments[folder.id]) ? assignments[folder.id] : [];
    const assigned = selectedKey && (assignedKeys.includes(selectedKey) || (selectedFamilyKey && assignedKeys.includes(selectedFamilyKey)));
    row.innerHTML = `
      <div class="folder-row__info">
        <button class="folder-row__name${state.selectedFolderId === folder.id ? ' is-active' : ''}" type="button">${escapeHtml(folder.name)}</button>
        <span class="folder-row__count">${assignedKeys.length}</span>
      </div>
      <div class="folder-row__actions">
        <button class="editor-mini-btn" type="button" data-folder-assign="${escapeHtml(folder.id)}" ${selectedKey ? '' : 'disabled'}>${assigned ? 'Remove' : 'Add'}</button>
        <button class="editor-mini-btn" type="button" data-folder-quick="${escapeHtml(folder.id)}">Quick Add</button>
      </div>`;
    row.querySelector('.folder-row__name').addEventListener('click', () => {
      state.selectedFolderId = state.selectedFolderId === folder.id ? null : folder.id;
      renderList();
    });
    row.querySelector('.folder-row__name').addEventListener('contextmenu', event => {
      event.preventDefault();
      openFolderModal(folder.id);
    });
    row.querySelector('[data-folder-assign]').addEventListener('click', () => toggleFolderAssignment(folder.id));
    row.querySelector('[data-folder-quick]').addEventListener('click', () => openQuickAddModal(folder.id));
    folderList.appendChild(row);
  });
}

function clearFolderFilter() {
  state.selectedFolderId = null;
  renderList();
}

function toggleFolderAssignment(folderId) {
  const selected = state.tables.find(item => item.table_id === state.selectedTableId);
  if (!selected) {
    showToast('Select a table first.');
    return;
  }
  const tableKey = getTablePreferenceKey(selected);
  const familyKey = getFamilyPreferenceKey(selected);
  const current = new Set(state.preferences.assignments[folderId] || []);
  if (current.has(tableKey)) {
    current.delete(tableKey);
  } else if (current.has(familyKey)) {
    current.delete(familyKey);
  } else {
    current.add(tableKey);
  }
  state.preferences.assignments[folderId] = [...current];
  savePreferences();
}

function openFolderModal(folderId = null) {
  state.editingFolderId = folderId;
  const folder = (state.preferences.folders || []).find(item => item.id === folderId);
  folderNameInput.value = folder?.name || '';
  btnFolderDelete.style.display = folder ? '' : 'none';
  folderModal.style.display = 'flex';
}

function closeFolderModal() {
  folderModal.style.display = 'none';
  state.editingFolderId = null;
  folderNameInput.value = '';
}

function saveFolder() {
  const name = folderNameInput.value.trim();
  if (!name) {
    showToast('Enter a folder name.');
    return;
  }
  const folders = [...(state.preferences.folders || [])];
  if (state.editingFolderId) {
    const folder = folders.find(item => item.id === state.editingFolderId);
    if (folder) {
      folder.name = name;
    }
  } else {
    folders.push({ id: `folder-${Date.now()}`, name });
  }
  state.preferences.folders = folders;
  closeFolderModal();
  savePreferences();
}

function deleteFolder() {
  if (!state.editingFolderId) {
    return;
  }
  state.preferences.folders = (state.preferences.folders || []).filter(item => item.id !== state.editingFolderId);
  delete state.preferences.assignments[state.editingFolderId];
  closeFolderModal();
  savePreferences();
}

function openQuickAddModal(folderId) {
  const folder = (state.preferences.folders || []).find(item => item.id === folderId);
  if (!folder) {
    showToast('Could not find that folder.');
    return;
  }
  state.quickAddFolderId = folderId;
  state.quickAddAssignments = new Set(state.preferences.assignments[folderId] || []);
  state.quickAddQuery = '';
  quickAddSearch.value = '';
  quickAddTitle.textContent = `Quick Add to ${folder.name}`;
  quickAddModal.style.display = 'flex';
  renderQuickAddList();
  quickAddSearch.focus();
}

function closeQuickAddModal() {
  quickAddModal.style.display = 'none';
  state.quickAddFolderId = null;
  state.quickAddAssignments = new Set();
  state.quickAddQuery = '';
  if (quickAddSearch) {
    quickAddSearch.value = '';
  }
}

function renderQuickAddList() {
  if (!quickAddList) {
    return;
  }
  const items = getQuickAddItems();
  const query = state.quickAddQuery;
  const visible = query
    ? items.filter(item => `${item.label} ${item.subtitle || ''} ${item.kind}`.toLowerCase().includes(query))
    : items;

  if (!visible.length) {
    quickAddList.innerHTML = '<div class="folder-empty">No tables or families match that search.</div>';
    return;
  }

  quickAddList.innerHTML = visible.map(item => `
    <label class="quick-add-item">
      <input type="checkbox" data-quick-key="${escapeHtml(item.key)}" ${state.quickAddAssignments.has(item.key) ? 'checked' : ''} />
      <div class="quick-add-item__copy">
        <div class="quick-add-item__title">${escapeHtml(item.label)}</div>
        <div class="quick-add-item__meta">${escapeHtml(item.kind)}${item.subtitle ? ` · ${escapeHtml(item.subtitle)}` : ''}</div>
      </div>
    </label>
  `).join('');

  quickAddList.querySelectorAll('[data-quick-key]').forEach(input => {
    input.addEventListener('change', event => {
      const key = event.target.dataset.quickKey;
      if (event.target.checked) {
        state.quickAddAssignments.add(key);
      } else {
        state.quickAddAssignments.delete(key);
      }
    });
  });
}

function getQuickAddItems() {
  const familyMap = new Map();
  const tableItems = [];
  state.tables.forEach(table => {
    const tableKey = getTablePreferenceKey(table);
    tableItems.push({
      key: tableKey,
      kind: 'Table',
      label: getDisplayTitle(table),
      subtitle: [table.workflow_bucket || 'Other', getTableMapVariant(table) || '', table.units || ''].filter(Boolean).join(' · '),
    });

    const familyKey = getFamilyPreferenceKey(table);
    if (!familyMap.has(familyKey)) {
      familyMap.set(familyKey, {
        key: familyKey,
        kind: 'Family',
        label: table.family_title || getDisplayTitle(table),
        subtitle: table.workflow_bucket || 'Other',
      });
    }
  });

  const familyItems = Array.from(familyMap.values());
  return [...familyItems, ...tableItems].sort((a, b) => {
    if (a.kind !== b.kind) {
      return a.kind.localeCompare(b.kind);
    }
    return a.label.localeCompare(b.label);
  });
}

function saveQuickAddAssignments() {
  if (!state.quickAddFolderId) {
    closeQuickAddModal();
    return;
  }
  state.preferences.assignments[state.quickAddFolderId] = Array.from(state.quickAddAssignments);
  closeQuickAddModal();
  savePreferences();
}

function getAssignedFoldersMarkup(table) {
  const key = getTablePreferenceKey(table);
  const familyKey = getFamilyPreferenceKey(table);
  const assigned = (state.preferences.folders || []).filter(folder => {
    const keys = state.preferences.assignments[folder.id] || [];
    return keys.includes(key) || keys.includes(familyKey);
  });
  if (!assigned.length) {
    return '<div class="desc-short">Not saved in a custom folder yet.</div>';
  }
  return `<div class="folder-chip-list">${assigned.map(folder => `<span class="folder-chip">${escapeHtml(folder.name)}</span>`).join('')}</div>`;
}

function tableMatchesFolder(table, folderId) {
  const keys = state.preferences.assignments[folderId] || [];
  const tableKey = getTablePreferenceKey(table);
  const familyKey = getFamilyPreferenceKey(table);
  return keys.includes(tableKey) || keys.includes(familyKey);
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

function openImportModal(tableId) {
  state.importTableId = tableId;
  state.importPreview = null;
  importText.value = '';
  importMode.value = 'values_only';
  importSummary.innerHTML = '';
  importCompare.innerHTML = '';
  hideImportError();
  btnImportApply.disabled = true;
  if (importMirrorToggle) {
    importMirrorToggle.checked = false;
  }
  renderImportMirrorTargets();
  importModal.style.display = 'flex';
  importText.focus();
}

function closeImportModal() {
  importModal.style.display = 'none';
  state.importTableId = null;
  state.importPreview = null;
  importSummary.innerHTML = '';
  importCompare.innerHTML = '';
  hideImportError();
  btnImportApply.disabled = true;
}

function clearImportModal() {
  importText.value = '';
  state.importPreview = null;
  importSummary.innerHTML = '';
  importCompare.innerHTML = '';
  hideImportError();
  btnImportApply.disabled = true;
  renderImportMirrorTargets();
  importText.focus();
}

function getImportMirrorTargets() {
  return getMirrorSiblingTables(state.importTableId)
    .slice()
    .sort((a, b) => extractVariantNumber(getTableMapVariant(a)) - extractVariantNumber(getTableMapVariant(b)));
}

function renderImportMirrorTargets() {
  if (!importMirrorPanel || !importMirrorToggle || !importMirrorList) {
    return;
  }
  const table = state.tables.find(item => item.table_id === state.importTableId);
  const targets = getImportMirrorTargets();
  if (!table || !targets.length) {
    importMirrorPanel.style.display = 'none';
    importMirrorToggle.checked = false;
    importMirrorList.innerHTML = '';
    return;
  }

  importMirrorPanel.style.display = '';
  const sourceMap = getTableMapVariant(table) || 'Current map';
  const targetList = targets.map(target => {
    const mapLabel = getTableMapVariant(target) || 'Sibling map';
    return `<li>${escapeHtml(mapLabel)}: ${escapeHtml(getDisplayTitle(target))}</li>`;
  }).join('');
  const verb = importMirrorToggle.checked ? 'Will also change' : 'Available sibling maps';
  importMirrorList.innerHTML = `
    <div>${escapeHtml(verb)} from ${escapeHtml(sourceMap)}:</div>
    <ul>${targetList}</ul>
  `;
}

function copyImportTemplate() {
  const table = state.tables.find(item => item.table_id === state.importTableId);
  let templateText = AI_TABLE_IMPORT_TEMPLATE;
  if (!table) {
    showImportTemplateText(templateText);
    return false;
  }
  const lines = [
    'Fill or revise this ECU calibration table and return only a TunerPro XDF Viewer import block.',
    '',
    'RULES',
    '- Keep the same table shape unless I explicitly ask for different axes.',
    '- Use Mode: values_only for normal value changes.',
    '- Use Mode: values_and_axes only if you also change X/Y breakpoint labels.',
    '- Return numeric values only in the Values section. Do not include explanations inside the import block.',
    '- Preserve row and column order exactly.',
    '',
    'OUTPUT FORMAT',
    '[TABLE IMPORT]',
    `Title: ${getDisplayTitle(table)}`,
    `Mode: ${importMode.value || 'values_only'}`,
    '',
  ];
  if (Array.isArray(table.x_labels) && table.x_labels.length) {
    lines.push('X Axis:');
    lines.push(table.x_labels.map(formatNumber).join(','));
    lines.push('');
  }
  if (Array.isArray(table.y_labels) && table.y_labels.length) {
    lines.push('Y Axis:');
    lines.push(table.y_labels.map(formatNumber).join(','));
    lines.push('');
  }
  lines.push('Values:');
  buildImportValueRows(table).forEach(row => lines.push(row));
  lines.push('');
  lines.push('REQUEST');
  lines.push('Replace the values above with your recommended filled table, keeping the same import format.');
  templateText = lines.join('\n');
  showImportTemplateText(templateText);
  return false;
}

function showImportTemplateText(templateText) {
  importText.value = templateText;
  btnImportApply.disabled = true;
  importSummary.innerHTML = '<div class="import-summary__ok">AI template inserted below. Press Ctrl+C to copy the selected text.</div>';
  importCompare.innerHTML = '';
  hideImportError();
  window.setTimeout(selectImportText, 0);
  showToast('Template inserted. Press Ctrl+C to copy it.');
}

function selectImportText() {
  importText.focus();
  importText.select();
  importText.setSelectionRange(0, importText.value.length);
}

function buildImportValueRows(table) {
  const grid = table.grid || [];
  if (table.type === 'constant' || (table.rows === 1 && table.cols === 1)) {
    return [formatNumber(grid?.[0]?.[0] ?? 0)];
  }
  return grid.map(row => (row || []).map(formatNumber).join(','));
}

function showImportError(message) {
  importError.textContent = message;
  importError.style.display = 'block';
}

function hideImportError() {
  importError.style.display = 'none';
}

async function previewImportedTable() {
  if (!state.session?.session_id || !state.importTableId) {
    showImportError('Load a session and select a table before importing.');
    return;
  }
  hideImportError();
  const response = await fetch(`/api/session/${state.session.session_id}/import-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      table_id: state.importTableId,
      import_text: importText.value,
      mode: importMode.value,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    showImportError(data.error || 'Import preview failed.');
    btnImportApply.disabled = true;
    return;
  }
  state.importPreview = data.preview;
  renderImportPreview();
  btnImportApply.disabled = false;
}

async function applyImportedTable() {
  if (!state.session?.session_id || !state.importTableId) {
    return;
  }
  hideImportError();
  const response = await fetch(`/api/session/${state.session.session_id}/import-apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      table_id: state.importTableId,
      import_text: importText.value,
      mode: importMode.value,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    showImportError(data.error || 'Import apply failed.');
    return;
  }
  state.session = data.session || state.session;
  delete state.axisDrafts[state.importTableId];
  delete state.previews[state.importTableId];
  state.tables = state.tables.map(table => (table.table_id === state.importTableId ? data.table : table));
  let mirroredCount = 0;
  if (importMirrorToggle?.checked) {
    const mirrorTargets = getImportMirrorTargets();
    for (const sibling of mirrorTargets) {
      const siblingResponse = await fetch(`/api/session/${state.session.session_id}/import-apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          table_id: sibling.table_id,
          import_text: importText.value,
          mode: importMode.value,
        }),
      });
      const siblingData = await siblingResponse.json();
      if (!siblingResponse.ok) {
        showImportError(siblingData.error || `Mirror failed for ${sibling.title}.`);
        continue;
      }
      state.session = siblingData.session || state.session;
      delete state.axisDrafts[sibling.table_id];
      delete state.previews[sibling.table_id];
      state.tables = state.tables.map(table => (table.table_id === sibling.table_id ? siblingData.table : table));
      mirroredCount += 1;
    }
  }
  if (state.selectedTableId === state.importTableId && hasActiveLog()) {
    await refreshLogOverlay();
  }
  updateDraftToolbar();
  renderList();
  closeImportModal();
  showToast(mirroredCount ? `Imported table applied to draft and ${mirroredCount} sibling map${mirroredCount === 1 ? '' : 's'}.` : 'Imported table applied to draft.');
}

function renderImportPreview() {
  const preview = state.importPreview;
  const table = state.tables.find(item => item.table_id === state.importTableId);
  if (!preview || !table) {
    importSummary.innerHTML = '';
    importCompare.innerHTML = '';
    return;
  }
  const axisSummary = preview.axis_summary || { x_changed_count: 0, y_changed_count: 0 };
  const validation = preview.validation || {};
  importSummary.innerHTML = `
    <div class="overview-summary">
      <div class="overview-stat">
        <span class="overview-stat__value">${preview.summary.changed_cell_count}</span>
        <span class="overview-stat__label">Changed Cells</span>
      </div>
      <div class="overview-stat">
        <span class="overview-stat__value">${axisSummary.x_changed_count}</span>
        <span class="overview-stat__label">X Axis Changes</span>
      </div>
      <div class="overview-stat">
        <span class="overview-stat__value">${axisSummary.y_changed_count}</span>
        <span class="overview-stat__label">Y Axis Changes</span>
      </div>
      <div class="overview-stat">
        <span class="overview-stat__value">${validation.rows}x${validation.cols}</span>
        <span class="overview-stat__label">Imported Shape</span>
      </div>
    </div>
    <div class="desc-short">
      Mode: ${escapeHtml(preview.mode)} · Delta ${formatNumber(preview.summary.min_delta)} to ${formatNumber(preview.summary.max_delta)}
    </div>
  `;
  importCompare.innerHTML = `
    <div class="import-compare__column">
      <div class="import-compare__title">Current Table</div>
      ${buildImportCompareTable(
        table,
        table.grid,
        table.x_labels,
        table.y_labels,
        preview.import_grid || table.grid,
        preview.import_x_labels || table.x_labels,
        preview.import_y_labels || table.y_labels,
        false,
      )}
    </div>
    <div class="import-compare__column">
      <div class="import-compare__title">Imported Preview</div>
      ${buildImportCompareTable(
        table,
        preview.import_grid || table.grid,
        preview.import_x_labels || table.x_labels,
        preview.import_y_labels || table.y_labels,
        table.grid,
        table.x_labels,
        table.y_labels,
        true,
      )}
    </div>
  `;
}

function buildImportCompareTable(table, grid, xLabels, yLabels, compareGrid, compareXLabels, compareYLabels, importedSide) {
  if (table.rows === 1 && table.cols === 1) {
    const current = formatNumber(grid?.[0]?.[0] ?? 0);
    const other = formatNumber(compareGrid?.[0]?.[0] ?? 0);
    const changed = current !== other;
    return `<div class="import-scalar${importedSide ? ' is-imported' : ''}${changed ? ' is-preview' : ''}">${current}${table.units ? ` <span class="scalar-units">[${escapeHtml(table.units)}]</span>` : ''}</div>`;
  }
  let html = '<div class="table-scroll"><table class="data-table import-compare-table"><thead><tr>';
  html += `<th class="row-header">${yLabels?.length ? 'Y / X' : ''}</th>`;
  for (let col = 0; col < table.cols; col += 1) {
    const label = xLabels && xLabels[col] !== undefined ? formatNumber(xLabels[col]) : `C${col}`;
    const otherLabel = compareXLabels && compareXLabels[col] !== undefined ? formatNumber(compareXLabels[col]) : `C${col}`;
    const changedAxis = label !== otherLabel;
    html += `<th class="${changedAxis ? (importedSide ? 'is-preview' : 'is-selected-cell') : ''}">${escapeHtml(label)}</th>`;
  }
  html += '</tr></thead><tbody>';
  for (let row = 0; row < table.rows; row += 1) {
    const rowLabel = yLabels && yLabels[row] !== undefined ? formatNumber(yLabels[row]) : `R${row}`;
    const otherRowLabel = compareYLabels && compareYLabels[row] !== undefined ? formatNumber(compareYLabels[row]) : `R${row}`;
    const changedRowAxis = rowLabel !== otherRowLabel;
    html += `<tr><td class="row-label ${changedRowAxis ? (importedSide ? 'is-preview' : 'is-selected-cell') : ''}">${escapeHtml(rowLabel)}</td>`;
    for (let col = 0; col < table.cols; col += 1) {
      const currentValue = formatNumber((grid[row] || [])[col] ?? 0);
      const otherValue = formatNumber((compareGrid[row] || [])[col] ?? 0);
      const changed = currentValue !== otherValue;
      html += `<td class="table-cell${changed ? (importedSide ? ' is-preview' : ' is-selected-cell') : ''}">${escapeHtml(currentValue)}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  return html;
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

  const selection = normalizeSelection(state.selections[table.table_id]);
  if (selection) {
    lines.push(`Selection: ${formatSelectionLabel(selection, table).replace(/^Selection:\s*/, '')}`);
    const selectedCells = Array.isArray(selection.cells) && selection.cells.length
      ? selection.cells
      : (() => {
          const cells = [];
          for (let row = selection.row_start; row <= selection.row_end; row += 1) {
            for (let col = selection.col_start; col <= selection.col_end; col += 1) {
              cells.push({ row, col });
            }
          }
          return cells;
        })();
    const rowIndexes = [...new Set(selectedCells.map(cell => Number(cell.row)))].sort((a, b) => a - b);
    const colIndexes = [...new Set(selectedCells.map(cell => Number(cell.col)))].sort((a, b) => a - b);
    const rectangular = selection.is_rectangular !== false
      && selectedCells.length === rowIndexes.length * colIndexes.length;

    if (!rectangular) {
      lines.push('');
      lines.push('Selected Cells:');
      selectedCells
        .slice()
        .sort((a, b) => (a.row - b.row) || (a.col - b.col))
        .forEach(cell => {
          const value = formatNumber((table.grid[cell.row] || [])[cell.col] ?? 0);
          const yLabel = table.y_labels?.[cell.row] !== undefined ? formatNumber(table.y_labels[cell.row]) : `R${cell.row + 1}`;
          const xLabel = table.x_labels?.[cell.col] !== undefined ? formatNumber(table.x_labels[cell.col]) : `C${cell.col + 1}`;
          lines.push(`- Y=${yLabel}, X=${xLabel}: ${value}`);
        });
      return copyText(lines.join('\n'));
    }

    const widths = new Array(colIndexes.length + 1).fill(3);
    widths[0] = Math.max(3, ...rowIndexes.map(row => formatNumber(table.y_labels?.[row] ?? `R${row + 1}`).length));
    colIndexes.forEach((col, idx) => {
      let width = formatNumber(table.x_labels?.[col] ?? `C${col + 1}`).length;
      rowIndexes.forEach(row => {
        const cellText = formatNumber((table.grid[row] || [])[col] ?? 0);
        width = Math.max(width, cellText.length);
      });
      widths[idx + 1] = width;
    });

    lines.push('');
    if (colIndexes.length) {
      lines.push(`X Axis: ${colIndexes.map(col => formatNumber(table.x_labels?.[col] ?? `C${col + 1}`)).join(', ')}`);
    }
    if (rowIndexes.length) {
      lines.push(`Y Axis: ${rowIndexes.map(row => formatNumber(table.y_labels?.[row] ?? `R${row + 1}`)).join(', ')}`);
    }
    lines.push('');
    const headers = [pad('', widths[0])];
    colIndexes.forEach((col, idx) => headers.push(pad(formatNumber(table.x_labels?.[col] ?? `C${col + 1}`), widths[idx + 1])));
    lines.push(headers.join(' | '));
    lines.push(headers.map((_, index) => '-'.repeat(widths[index])).join('-+-'));
    rowIndexes.forEach(row => {
      const cells = [pad(formatNumber(table.y_labels?.[row] ?? `R${row + 1}`), widths[0])];
      colIndexes.forEach((col, idx) => {
        cells.push(pad(formatNumber((table.grid[row] || [])[col] ?? 0), widths[idx + 1]));
      });
      lines.push(cells.join(' | '));
    });
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

async function copyText(text, options = {}) {
  const value = String(text || '');
  if (!value) {
    showToast('Nothing to copy.');
    return false;
  }
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      if (options.showModal) {
        openClipboardModal(value);
        showToast('Copied. Text is also open below.');
      } else {
        showToast('Copied to this device.');
      }
      return true;
    }
  } catch (error) {
    // Fall through to the legacy path and final manual-copy modal.
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const copied = document.execCommand('copy');
    textarea.remove();
    if (copied) {
      if (options.showModal) {
        openClipboardModal(value);
        showToast('Copied. Text is also open below.');
      } else {
        showToast('Copied to this device.');
      }
      return true;
    }
  } catch (error) {
    // Show the modal below.
  }

  openClipboardModal(value);
  showToast('Clipboard blocked. Select the text to copy.');
  return false;
}

function copyClipboardModalText() {
  copyText(clipboardText.value);
}

function openClipboardModal(text) {
  clipboardText.value = String(text || '');
  clipboardModal.style.display = 'flex';
  selectClipboardFallbackText();
}

function closeClipboardModal() {
  clipboardModal.style.display = 'none';
}

function selectClipboardFallbackText() {
  clipboardText.focus();
  clipboardText.select();
  clipboardText.setSelectionRange(0, clipboardText.value.length);
}

function resetAll() {
  state.files = { xdf: null, bin: null, log: null };
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
  state.selectedTableId = null;
  state.selectedCategory = null;
  state.log = null;
  state.logOverlay = null;
  stopLogPlayback();
  searchInput.value = '';
  searchClear.style.display = 'none';
  viewModeSelect.value = state.viewMode;

  ['xdf', 'bin', 'log'].forEach(key => {
    $(`input-${key}`).value = '';
    $(`status-${key}`).textContent = key === 'xdf'
      ? 'No XDF selected'
      : (key === 'bin' ? 'No BIN selected' : 'No CSV log selected');
    $(`drop-${key}`).classList.remove('has-file');
  });

  headerMeta.textContent = '';
  countBadge.textContent = '0 / 0 shown';
  tableList.innerHTML = '';
  workspaceOverview.innerHTML = '';
  folderList.innerHTML = '';
  hideError();
  syncHideAutogenButton();
  updateDraftToolbar();
  updateParseBtn();
  renderResults();
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

function normalizeText(value) {
  return String(value || '').trim().replace(/\s+/g, ' ').toLowerCase();
}










