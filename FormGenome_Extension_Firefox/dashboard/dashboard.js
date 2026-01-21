// Form Genome Dashboard - Live view of all analyzed forms
// Now with Data Table View

let allForms = [];
let filteredForms = [];
let currentFilter = 'all';
let currentSortKey = 'analyzed_at';
let currentSortDirection = 'desc';
let columnOrder = [];
let draggedColumn = null;

// Define all available columns (30+ attributes)
const COLUMNS = [
  { key: 'form_name', label: 'Form Name', draggable: true },
  { key: 'entity_name', label: 'Entity', draggable: true },
  { key: 'source_url', label: 'URL', draggable: true },
  { key: 'pages', label: 'Pages', draggable: true },
  { key: 'total_field_count', label: 'Fields', draggable: true },
  { key: 'complexity_score', label: 'Complexity', draggable: true },
  { key: 'nigo_score', label: 'NIGO', draggable: true },
  { key: 'confidence_tier', label: 'Confidence', draggable: true },
  { key: 'action_type', label: 'Action Type', draggable: true },

  { key: 'signature_required', label: 'Signature', draggable: true },
  { key: 'signature_count', label: 'Sig Count', draggable: true },
  { key: 'notarization_required', label: 'Notary', draggable: true },
  { key: 'attachments_required', label: 'Attachments', draggable: true },
  { key: 'attachment_count', label: 'Att Count', draggable: true },
  { key: 'payment_required', label: 'Payment', draggable: true },
  { key: 'payment_amount', label: 'Payment Amt', draggable: true },
  { key: 'identification_required', label: 'ID Required', draggable: true },
  { key: 'conditional_logic', label: 'Conditional', draggable: true },
  { key: 'third_party_involved', label: '3rd Party', draggable: true },
  { key: 'witnesses_required', label: 'Witnesses', draggable: true },
  { key: 'deadlines_present', label: 'Deadlines', draggable: true },
  { key: 'form_purpose', label: 'Purpose', draggable: true },
  { key: 'industry_vertical', label: 'Vertical', draggable: true },
  { key: 'industry_subvertical', label: 'Subvertical', draggable: true },
  { key: 'estimated_signer_time', label: 'Signer Time', draggable: true },
  { key: 'estimated_processing_time', label: 'Proc Time', draggable: true },
  { key: 'analyzed_at', label: 'Analyzed', draggable: true }
];

// Initialize dashboard
document.addEventListener('DOMContentLoaded', async () => {
  // Load column order
  const orderData = await browser.storage.local.get('dashboardColumnOrder');
  columnOrder = orderData.dashboardColumnOrder || COLUMNS.map(c => c.key);

  // Sync new columns
  COLUMNS.forEach(col => {
    if (!columnOrder.includes(col.key)) {
      columnOrder.push(col.key);
    }
  });

  await loadAllForms();
  updateStats();
  filterAndRenderForms();
  setupEventListeners();
});

function setupEventListeners() {
  document.getElementById('refreshDashboard').addEventListener('click', async () => {
    await loadAllForms();
    updateStats();
    filterAndRenderForms();
  });

  document.getElementById('exportAllCSV').addEventListener('click', exportAllToCSV);
  document.getElementById('clearAllData').addEventListener('click', clearAllData); // Be careful with this

  document.getElementById('searchInput').addEventListener('input', () => {
    filterAndRenderForms();
  });

  document.querySelectorAll('.filter-pill').forEach(pill => {
    pill.addEventListener('click', (e) => {
      document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
      e.target.classList.add('active');
      currentFilter = e.target.dataset.filter;
      filterAndRenderForms();
    });
  });

  // Sort select is less useful now with table headers, but we keep it responding to state
  const sortSelect = document.getElementById('sortSelect');
  if (sortSelect) {
    sortSelect.addEventListener('change', (e) => {
      // Map select values to keys
      const val = e.target.value;
      if (val === 'recent') { currentSortKey = 'analyzed_at'; currentSortDirection = 'desc'; }
      else if (val === 'complexity-high') { currentSortKey = 'complexity_score'; currentSortDirection = 'desc'; }
      else if (val === 'complexity-low') { currentSortKey = 'complexity_score'; currentSortDirection = 'asc'; }
      else if (val === 'name') { currentSortKey = 'form_name'; currentSortDirection = 'asc'; }
      else if (val === 'entity') { currentSortKey = 'entity_name'; currentSortDirection = 'asc'; }
      filterAndRenderForms();
    });
  }
}

async function loadAllForms() {
  const storage = await browser.storage.local.get(null);
  // Debug: Check raw storage
  const rawKeys = Object.keys(storage);
  const analysisKeys = rawKeys.filter(k => k.startsWith('analysis_'));

  if (analysisKeys.length === 0) {
    console.log('Storage is empty or has no analysis keys:', rawKeys);
    // Optional: alert('Debug: Local storage appears empty (0 forms found).');
  }

  allForms = Object.entries(storage)
    .filter(([key, value]) => key.startsWith('analysis_') && value && !value.error) // Relaxed filter completely for debugging
    .map(([key, value]) => ({
      ...value,
      _storage_key: key,
      // Normalize some fields for consistent display
      form_name: getFormDisplayName(value),
      analyzed_at: value._cached_at || Date.now()
    }));

  console.log(`Loaded ${allForms.length} forms from storage`);
}

function updateStats() {
  document.getElementById('totalForms').textContent = allForms.length;

  const avgComplexity = allForms.length > 0
    ? (allForms.reduce((sum, f) => sum + (f.complexity_score || 0), 0) / allForms.length).toFixed(1)
    : '0';
  document.getElementById('avgComplexity').textContent = avgComplexity;

  const uniqueEntities = new Set(allForms.map(f => f.entity_name || f.entity).filter(Boolean)).size;
  document.getElementById('uniqueEntities').textContent = uniqueEntities;

  if (allForms.length > 0) {
    const mostRecent = Math.max(...allForms.map(f => f.analyzed_at || 0));
    const date = new Date(mostRecent);
    const now = Date.now();
    const diff = now - mostRecent;

    let timeAgo;
    if (diff < 60000) timeAgo = 'Just now';
    else if (diff < 3600000) timeAgo = `${Math.floor(diff / 60000)}m ago`;
    else if (diff < 86400000) timeAgo = `${Math.floor(diff / 3600000)}h ago`;
    else timeAgo = date.toLocaleDateString();

    document.getElementById('lastAnalyzed').textContent = timeAgo;
  } else {
    document.getElementById('lastAnalyzed').textContent = 'Never';
  }
}

function filterAndRenderForms() {
  const searchTerm = document.getElementById('searchInput').value.toLowerCase();

  filteredForms = [...allForms];

  if (searchTerm) {
    filteredForms = filteredForms.filter(form => {
      const formName = (form.form_name || '').toLowerCase();
      const entityName = (form.entity_name || form.entity || '').toLowerCase();
      const url = (form.source_url || form.pdf_url || '').toLowerCase();
      return formName.includes(searchTerm) || entityName.includes(searchTerm) || url.includes(searchTerm);
    });
  }

  if (currentFilter !== 'all') {
    filteredForms = filteredForms.filter(form => {
      switch (currentFilter) {
        case 'high-complexity': return (form.complexity_score || 0) >= 7;
        case 'signatures': return form.signature_required;
        case 'notary': return form.notarization_required;
        case 'attachments': return form.attachments_required;
        case 'payment': return form.payment_required;
        default: return true;
      }
    });
  }

  // Sort
  filteredForms.sort((a, b) => {
    let valA = a[currentSortKey];
    let valB = b[currentSortKey];

    if (valA === undefined || valA === null) valA = '';
    if (valB === undefined || valB === null) valB = '';

    const numA = parseFloat(valA);
    const numB = parseFloat(valB);

    // Check if both are numbers (and not just empty strings behaving like 0)
    const isNum = !isNaN(numA) && !isNaN(numB) && typeof valA !== 'boolean' && typeof valB !== 'boolean' && valA !== '' && valB !== '';

    if (isNum) {
      return currentSortDirection === 'asc' ? numA - numB : numB - numA;
    }

    const strA = String(valA).toLowerCase();
    const strB = String(valB).toLowerCase();

    if (strA < strB) return currentSortDirection === 'asc' ? -1 : 1;
    if (strA > strB) return currentSortDirection === 'asc' ? 1 : -1;
    return 0;
  });

  renderTable();
}

function renderTable() {
  const table = document.getElementById('formsTable');
  const tbody = document.getElementById('formsTableBody');
  const thead = document.getElementById('tableHeaderRow');
  const emptyState = document.getElementById('emptyState');

  if (filteredForms.length === 0) {
    if (table) table.style.display = 'none';
    if (emptyState) emptyState.classList.remove('hidden');
    return;
  }

  if (table) table.style.display = 'table';
  if (emptyState) emptyState.classList.add('hidden');

  renderHeaders(thead);

  const orderedColumns = getOrderedColumns();

  tbody.innerHTML = filteredForms.map(form => {
    const cells = orderedColumns.map(col => renderCell(col.key, form)).join('');
    // Store metadata in data attribute for potential usage
    return `<tr>${cells}</tr>`;
  }).join('');
}

function getOrderedColumns() {
  return columnOrder.map(key => COLUMNS.find(c => c.key === key)).filter(Boolean);
}

function renderHeaders(thead) {
  const orderedColumns = getOrderedColumns();

  thead.innerHTML = orderedColumns.map((col, index) => {
    const draggableAttr = col.draggable ? 'draggable="true"' : '';
    const draggableClass = col.draggable ? 'draggable' : '';
    const dragIndicator = col.draggable ? '<span class="drag-indicator">⋮⋮</span>' : '';

    let sortClass = '';
    let sortIndicator = '';
    if (col.key === currentSortKey) {
      sortClass = 'sorted';
      sortIndicator = currentSortDirection === 'asc' ? ' ↑' : ' ↓';
    }

    return `
      <th class="${draggableClass} ${sortClass}"
          ${draggableAttr}
          data-column-key="${col.key}"
          onclick="handleSort('${col.key}')">
        ${dragIndicator}${col.label}${sortIndicator}
      </th>
    `;
  }).join('');

  setupColumnDragAndDrop();
}

function renderCell(key, form) {
  const val = form[key];
  let content = val;

  // Formatting Logic
  if (key === 'source_url' || key === 'pdf_url') {
    const url = val || '';
    if (url) {
      content = `<a href="${url}" target="_blank" class="url-link" title="${url}">${url}</a>`;
    } else {
      content = '';
    }
  } else if (typeof val === 'boolean') {
    content = val ? 'Yes' : 'No';
    return `<td class="${val ? 'yes' : 'no'}">${content}</td>`;
  } else if (key === 'complexity_score') {
    const num = parseInt(val) || 0;
    let cls = num >= 7 ? 'complexity-high' : num >= 4 ? 'complexity-medium' : 'complexity-low';
    return `<td class="${cls}">${num}</td>`;
  } else if (key === 'analyzed_at') {
    content = new Date(val || Date.now()).toLocaleString();
  }

  // Null/Undefined check
  if (content === null || content === undefined) content = '';

  return `<td>${content}</td>`;
}

// Global Sort Handler
window.handleSort = function (key) {
  if (currentSortKey === key) {
    currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    currentSortKey = key;
    currentSortDirection = 'desc'; // Default desc for new sorts (good for numbers/dates)
  }
  filterAndRenderForms();
};

/* Drag and Drop Logic */
function setupColumnDragAndDrop() {
  const headers = document.querySelectorAll('th.draggable');
  headers.forEach(header => {
    header.addEventListener('dragstart', handleColumnDragStart);
    header.addEventListener('dragover', handleColumnDragOver);
    header.addEventListener('drop', handleColumnDrop);
    header.addEventListener('dragend', handleColumnDragEnd);
  });
}

function handleColumnDragStart(e) {
  draggedColumn = e.currentTarget;
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', e.currentTarget.dataset.columnKey);
}

function handleColumnDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const target = e.currentTarget;
  if (draggedColumn !== target && target.classList.contains('draggable')) {
    target.classList.add('drag-over');
  }
  return false;
}

function handleColumnDrop(e) {
  e.stopPropagation();
  const target = e.currentTarget;
  if (draggedColumn !== target && draggedColumn && target.classList.contains('draggable')) {
    const draggedKey = draggedColumn.dataset.columnKey;
    const targetKey = target.dataset.columnKey;

    const draggedIndex = columnOrder.indexOf(draggedKey);
    const targetIndex = columnOrder.indexOf(targetKey);

    if (draggedIndex !== -1 && targetIndex !== -1) {
      columnOrder.splice(draggedIndex, 1);
      columnOrder.splice(targetIndex, 0, draggedKey);

      // Save order
      browser.storage.local.set({ dashboardColumnOrder: columnOrder });
      filterAndRenderForms();
    }
  }
  target.classList.remove('drag-over');
  return false;
}

function handleColumnDragEnd(e) {
  e.currentTarget.classList.remove('dragging');
  document.querySelectorAll('th').forEach(h => h.classList.remove('drag-over'));
  draggedColumn = null;
}

// Utility
function getFormDisplayName(form) {
  let name = form.pretty_title || form.form_title || form.form_name || form.title;
  if (!name || name.trim() === '') {
    name = form.pdf_name || 'Untitled';
    name = name.replace(/\.pdf\.pdf$/i, '.pdf').replace(/\.pdf$/i, '').replace(/^[Gg]forms?/i, '').trim();
  }
  return name || 'Untitled';
}

function exportAllToCSV() {
  if (filteredForms.length === 0) {
    alert('No forms to export!');
    return;
  }

  const orderedColumns = getOrderedColumns();
  const headers = orderedColumns.map(c => c.label);

  const escapeCsv = (value) => {
    if (value === null || value === undefined) return '';
    const str = String(value);
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };

  const rows = [headers.map(escapeCsv).join(',')];

  filteredForms.forEach(form => {
    const row = orderedColumns.map(col => {
      let val = form[col.key];
      if (col.key === 'analyzed_at') val = new Date(val || Date.now()).toISOString();
      else if (typeof val === 'boolean') val = val ? 'Yes' : 'No';
      return val;
    });
    rows.push(row.map(escapeCsv).join(','));
  });

  const csvContent = rows.join('\n');
  const timestamp = new Date().toISOString().split('T')[0];
  const filename = `Form_Genome_Dashboard_${timestamp}.csv`;

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

// Note: clearAllData is already in dashboard.js but I will keep this logic here as well
async function clearAllData() {
  if (!confirm(`Are you sure you want to clear all ${allForms.length} analyzed forms? This cannot be undone.`)) {
    return;
  }
  await browser.storage.local.clear();
  location.reload();
}

