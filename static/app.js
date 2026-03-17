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

const state = {
  files: { xdf: null, bin: null, bin2: null },
  tables: [],
  categories: {},
  search: '',
  hasDiff: false,
  viewMode: 'workflow',
  hideAutogen: true,
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
const btnNewFiles = $('btn-new-files');
const btnAiContext = $('btn-ai-context');
const btnCopyAiContext = $('btn-copy-ai-context');
const viewModeSelect = $('view-mode');
const toast = $('copy-toast');
const headerMeta = $('header-meta');
const debugModal = $('debug-modal');
const debugOutput = $('debug-output');
const debugSearch = $('debug-search');
const aiContextModal = $('ai-context-modal');
const aiContextText = $('ai-context-text');

let debugTables = [];

init();

function init() {
  wireDropZones();
  wireToolbar();
  wireModals();
  updateParseBtn();
  syncHideAutogenButton();
  aiContextText.textContent = AI_CONTEXT_PROMPT;
  viewModeSelect.value = state.viewMode;
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
}

function wireModals() {
  $('debug-close').addEventListener('click', () => { debugModal.style.display = 'none'; });
  $('ai-context-close').addEventListener('click', () => { aiContextModal.style.display = 'none'; });

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
  btnClear.style.display = '';
  headerMeta.textContent = `${state.files.xdf?.name || ''} · ${state.files.bin?.name || ''} · ${state.tables.length} definitions`;
  viewModeSelect.value = state.viewMode;
  renderList();
}

function renderList() {
  const visibleState = getVisibleState();
  const visible = visibleState.visible;
  countBadge.textContent = buildCountLabel(visible.length, state.tables.length, visibleState.reasons);
  tableList.innerHTML = '';

  if (!visible.length) {
    tableList.innerHTML = '<div class="no-results"><div>No tables match your filter.</div></div>';
    return;
  }

  if (state.viewMode === 'flat') {
    renderFlatView(visible);
    return;
  }

  if (state.viewMode === 'hierarchy') {
    renderHierarchyView(visible);
    return;
  }

  renderWorkflowView(visible);
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
      return table.title.toLowerCase().includes(query)
        || String(table.family_title || '').toLowerCase().includes(query)
        || String(table.variant_label || '').toLowerCase().includes(query)
        || String(table.description_short || '').toLowerCase().includes(query)
        || String(table.description_long || '').toLowerCase().includes(query)
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

function renderFlatView(tables) {
  appendFamilyGroups(tableList, tables.slice().sort(compareTables));
}

function renderWorkflowView(tables) {
  const groups = new Map();
  tables.forEach(table => {
    const bucket = table.workflow_bucket || 'Other';
    if (!groups.has(bucket)) {
      groups.set(bucket, []);
    }
    groups.get(bucket).push(table);
  });

  Array.from(groups.keys()).sort(compareBuckets).forEach(bucket => {
    const items = groups.get(bucket).slice().sort(compareTables);
    const section = buildGroupSection({
      label: bucket,
      count: items.length,
      depth: 0,
      open: bucket !== 'Low Priority',
      lowPriority: bucket === 'Low Priority',
    });
    appendFamilyDescription(section.querySelector('.group-body'), findRepresentative(items), true);
    appendFamilyGroups(section.querySelector('.group-body'), items);
    tableList.appendChild(section);
  });
}

function renderHierarchyView(tables) {
  const root = createNode('All Tables');
  tables.forEach(table => {
    const path = Array.isArray(table.primary_category_path) && table.primary_category_path.length
      ? table.primary_category_path
      : ['Uncategorised'];
    let cursor = root;
    path.forEach(segment => {
      if (!cursor.children.has(segment)) {
        cursor.children.set(segment, createNode(segment));
      }
      cursor = cursor.children.get(segment);
    });
    cursor.tables.push(table);
  });

  Array.from(root.children.values())
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach(node => tableList.appendChild(renderHierarchyNode(node, 0)));
}

function createNode(label) {
  return { label, children: new Map(), tables: [] };
}

function renderHierarchyNode(node, depth) {
  const count = countNodeTables(node);
  const lowPriority = nodeIsLowPriority(node);
  const open = depth === 0 || (!lowPriority && count <= 14);
  const section = buildGroupSection({ label: node.label, count, depth, open, lowPriority });
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
  section.className = 'family-section';
  section.innerHTML = `
    <button class="family-header" type="button">
      <svg class="family-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
      <span class="card-type-badge badge-family">family</span>
      <span class="family-title">${escapeHtml(group.familyTitle)}</span>
      <span class="family-count">${group.tables.length}</span>
    </button>
    <div class="family-body"></div>`;

  section.querySelector('.family-header').addEventListener('click', () => {
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

function buildGroupSection({ label, count, depth = 0, open = true, lowPriority = false }) {
  const section = document.createElement('section');
  section.className = `group-section${open ? ' open' : ''}${lowPriority ? ' group-section--low' : ''}`;
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
  card.className = `table-card${table.error ? ' has-error' : ''}`;

  const isScalar = table.type === 'constant' || (table.rows === 1 && table.cols === 1);
  const badgeClass = table.error ? 'badge-error' : table.type === 'constant' ? 'badge-constant' : 'badge-table';
  const badgeText = table.error ? 'error' : table.type === 'constant' ? 'scalar' : 'table';
  const dims = isScalar ? '' : `${table.rows}x${table.cols}`;
  const units = table.units ? `[${table.units}]` : '';
  const changeBadge = table.change_count ? `<span class="card-metric" title="Logged edits">${table.change_count} edits</span>` : '';
  const displayTitle = titleOverride || table.title;

  card.innerHTML = `
    <div class="card-header">
      <svg class="card-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
      <span class="card-type-badge ${badgeClass}">${badgeText}</span>
      <span class="card-title" title="${escapeHtml(table.title)}">${escapeHtml(displayTitle)}</span>
      ${table.low_priority_reason ? '<span class="badge-autogen">low priority</span>' : ''}
      ${table.is_autogen ? '<span class="badge-autogen">autogen</span>' : ''}
      ${changeBadge}
      ${units ? `<span class="card-units">${escapeHtml(units)}</span>` : ''}
      ${dims ? `<span class="card-dims">${dims}</span>` : ''}
      <button class="btn-copy card-copy" type="button" title="Copy for AI">Copy for AI</button>
    </div>
    <div class="card-body">
      ${buildDescriptionBlock(table)}
      ${buildBody(table)}
    </div>`;

  card.querySelector('.card-header').addEventListener('click', event => {
    if (event.target.closest('.card-copy')) return;
    card.classList.toggle('open');
  });

  card.querySelector('.card-copy').addEventListener('click', event => {
    event.stopPropagation();
    copyForAI(table);
  });

  return card;
}

function buildDescriptionBlock(table) {
  const shortText = table.description_short || '';
  const longText = table.description_long || '';
  if (!shortText && !longText && !table.description) {
    return '';
  }

  const parts = [];
  if (shortText) {
    parts.push(`<div class="desc-short">${escapeHtml(shortText)}</div>`);
  } else if (table.description) {
    parts.push(`<div class="desc-short">${escapeHtml(table.description)}</div>`);
  }
  if (longText) {
    parts.push(`<div class="desc-long">${escapeHtml(longText)}</div>`);
  }
  return `<div class="card-desc">${parts.join('')}</div>`;
}

function buildBody(table) {
  if (table.error) {
    return `<div class="card-error-msg">Error: ${escapeHtml(table.error)}</div>`;
  }
  if (!table.rows) {
    return '<div class="card-error-msg">No data extracted.</div>';
  }
  if (table.type === 'constant' || (table.rows === 1 && table.cols === 1)) {
    return buildScalar(table);
  }
  return buildTable(table);
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
      if (hasDiff && grid2) {
        const other = formatNumber((grid2[row] || [])[col] ?? 0);
        if (current !== other) {
          html += `<td class="diff-changed"><span class="v1">${escapeHtml(current)}</span><span class="v2">${escapeHtml(other)}</span></td>`;
        } else {
          html += `<td>${escapeHtml(current)}</td>`;
        }
      } else {
        html += `<td>${escapeHtml(current)}</td>`;
      }
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

function copyForAI(table) {
  const lines = [];
  const unitsSuffix = table.units ? ` (${table.units})` : '';
  lines.push(`[${table.title}]${unitsSuffix}`);

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
  btnClear.style.display = 'none';
  headerMeta.textContent = '';
  countBadge.textContent = '0 / 0 shown';
  tableList.innerHTML = '';
  hideError();
  syncHideAutogenButton();
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

function showToast() {
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
