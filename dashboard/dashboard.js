// Form Genome Dashboard - Live view of all analyzed forms

let allForms = [];
let filteredForms = [];
let currentFilter = 'all';
let currentSort = 'recent';

// Initialize dashboard
document.addEventListener('DOMContentLoaded', async () => {
  await loadAllForms();
  updateStats();
  renderForms();
  setupEventListeners();
});

function setupEventListeners() {
  // Refresh button
  document.getElementById('refreshDashboard').addEventListener('click', async () => {
    await loadAllForms();
    updateStats();
    renderForms();
  });

  // Export CSV button
  document.getElementById('exportAllCSV').addEventListener('click', exportAllToCSV);

  // Clear all data button
  document.getElementById('clearAllData').addEventListener('click', clearAllData);

  // Search input
  document.getElementById('searchInput').addEventListener('input', (e) => {
    filterAndRenderForms();
  });

  // Filter pills
  document.querySelectorAll('.filter-pill').forEach(pill => {
    pill.addEventListener('click', (e) => {
      document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
      e.target.classList.add('active');
      currentFilter = e.target.dataset.filter;
      filterAndRenderForms();
    });
  });

  // Sort select
  document.getElementById('sortSelect').addEventListener('change', (e) => {
    currentSort = e.target.value;
    filterAndRenderForms();
  });
}

async function loadAllForms() {
  // Load all analysis results from chrome.storage.local
  const storage = await chrome.storage.local.get(null);

  allForms = Object.entries(storage)
    .filter(([key, value]) => key.startsWith('analysis_') && value && !value.error && value.ok)
    .map(([key, value]) => ({
      ...value,
      _storage_key: key,
      _cached_at: value._cached_at || Date.now()
    }));

  console.log(`Loaded ${allForms.length} forms from storage`);
}

function updateStats() {
  // Total forms
  document.getElementById('totalForms').textContent = allForms.length;

  // Average complexity
  const avgComplexity = allForms.length > 0
    ? (allForms.reduce((sum, f) => sum + (f.complexity_score || 0), 0) / allForms.length).toFixed(1)
    : '0';
  document.getElementById('avgComplexity').textContent = avgComplexity;

  // Unique entities
  const uniqueEntities = new Set(allForms.map(f => f.entity_name).filter(Boolean)).size;
  document.getElementById('uniqueEntities').textContent = uniqueEntities;

  // Last analyzed
  if (allForms.length > 0) {
    const mostRecent = Math.max(...allForms.map(f => f._cached_at || 0));
    const date = new Date(mostRecent);
    const now = Date.now();
    const diff = now - mostRecent;

    let timeAgo;
    if (diff < 60000) {
      timeAgo = 'Just now';
    } else if (diff < 3600000) {
      timeAgo = `${Math.floor(diff / 60000)}m ago`;
    } else if (diff < 86400000) {
      timeAgo = `${Math.floor(diff / 3600000)}h ago`;
    } else {
      timeAgo = date.toLocaleDateString();
    }

    document.getElementById('lastAnalyzed').textContent = timeAgo;
  } else {
    document.getElementById('lastAnalyzed').textContent = 'Never';
  }
}

function filterAndRenderForms() {
  const searchTerm = document.getElementById('searchInput').value.toLowerCase();

  // Start with all forms
  filteredForms = [...allForms];

  // Apply search filter
  if (searchTerm) {
    filteredForms = filteredForms.filter(form => {
      const formName = getFormDisplayName(form).toLowerCase();
      const entityName = (form.entity_name || '').toLowerCase();
      const url = (form.source_url || form.pdf_url || '').toLowerCase();
      return formName.includes(searchTerm) || entityName.includes(searchTerm) || url.includes(searchTerm);
    });
  }

  // Apply category filter
  if (currentFilter !== 'all') {
    filteredForms = filteredForms.filter(form => {
      switch (currentFilter) {
        case 'high-complexity':
          return (form.complexity_score || 0) >= 7;
        case 'signatures':
          return form.signature_required;
        case 'notary':
          return form.notarization_required;
        case 'attachments':
          return form.attachments_required;
        case 'payment':
          return form.payment_required;
        default:
          return true;
      }
    });
  }

  // Apply sort
  filteredForms.sort((a, b) => {
    switch (currentSort) {
      case 'recent':
        return (b._cached_at || 0) - (a._cached_at || 0);
      case 'complexity-high':
        return (b.complexity_score || 0) - (a.complexity_score || 0);
      case 'complexity-low':
        return (a.complexity_score || 0) - (b.complexity_score || 0);
      case 'name':
        return getFormDisplayName(a).localeCompare(getFormDisplayName(b));
      case 'entity':
        return (a.entity_name || '').localeCompare(b.entity_name || '');
      default:
        return 0;
    }
  });

  renderForms();
}

function renderForms() {
  const container = document.getElementById('formsList');
  const emptyState = document.getElementById('emptyState');

  if (filteredForms.length === 0) {
    container.innerHTML = '';
    emptyState.classList.remove('hidden');
    return;
  }

  emptyState.classList.add('hidden');

  container.innerHTML = filteredForms.map(form => {
    const formName = getFormDisplayName(form);
    const entityName = form.entity_name || 'Unknown Entity';
    const complexity = form.complexity_score || 0;
    const complexityClass = complexity >= 7 ? 'high' : complexity >= 4 ? 'medium' : 'low';

    const badges = [];
    if (form.signature_required) badges.push('<span class="badge signature">Signature</span>');
    if (form.notarization_required) badges.push('<span class="badge notary">Notary</span>');
    if (form.attachments_required) badges.push('<span class="badge attachment">Attachments</span>');
    if (form.payment_required) badges.push('<span class="badge payment">Payment</span>');
    if (form.conditional_logic) badges.push('<span class="badge conditional">Conditional</span>');

    const date = new Date(form._cached_at || Date.now());
    const dateStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString();

    return `
      <div class="form-card" data-key="${form._storage_key}">
        <div class="form-card-header">
          <div>
            <div class="form-title">${escapeHtml(formName)}</div>
            <div class="form-entity">${escapeHtml(entityName)}</div>
          </div>
          <div class="form-complexity ${complexityClass}">
            ${complexity}/10
          </div>
        </div>

        <div class="form-stats">
          <div class="form-stat">
            <span>📄</span>
            <span>${form.pages || 1} pages</span>
          </div>
          <div class="form-stat">
            <span>📝</span>
            <span>${form.total_field_count || form.field_count || 0} fields</span>
          </div>
          <div class="form-stat">
            <span>⚠️</span>
            <span>NIGO: ${form.nigo_score || 0}</span>
          </div>
          ${form.signature_analysis?.signature_count ? `
          <div class="form-stat">
            <span>✍️</span>
            <span>${form.signature_analysis.signature_count} signatures</span>
          </div>
          ` : ''}
        </div>

        ${badges.length > 0 ? `
        <div class="form-badges">
          ${badges.join('')}
        </div>
        ` : ''}

        <div class="form-meta">
          <div class="form-url">
            ${form.source_url || form.pdf_url ?
        `<a href="${escapeHtml(form.source_url || form.pdf_url)}" target="_blank" class="url-link" title="Open form in new tab">${escapeHtml(form.source_url || form.pdf_url)}</a>` :
        'No URL'}
          </div>
          <div class="form-date">${dateStr}</div>
        </div>
      </div>
    `;
  }).join('');
}

function getFormDisplayName(form) {
  // Same logic as reportGenerator.js
  let name = form.pretty_title || form.form_title || form.form_name || form.title;

  if (!name || name.trim() === '') {
    name = form.pdf_name || 'Untitled';
    name = name
      .replace(/\.pdf\.pdf$/i, '.pdf')
      .replace(/\.pdf$/i, '')
      .replace(/^[Gg]forms?/i, '')
      .trim();
  }

  return name || 'Untitled';
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function exportAllToCSV() {
  if (filteredForms.length === 0) {
    alert('No forms to export!');
    return;
  }

  // Generate CSV content
  const headers = [
    'Form Name',
    'Entity Name',
    'PDF URL',
    'Pages',
    'Total Fields',
    'Complexity Score',
    'NIGO Score',
    'Confidence Tier',
    'Action Type',
    'Signature Required',
    'Signature Count',
    'Notarization Required',
    'Attachments Required',
    'Attachment Count',
    'Payment Required',
    'Payment Amount',
    'Identification Required',
    'Conditional Logic',
    'Third Party Involved',
    'Witnesses Required',
    'Deadlines Present',
    'Form Purpose',
    'Industry Vertical',
    'Industry Subvertical',
    'Estimated Signer Time',
    'Estimated Processing Time',
    'Analyzed Date'
  ];

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
    const row = [
      getFormDisplayName(form),
      form.entity_name || '',
      form.source_url || form.pdf_url || '',
      form.pages || 1,
      form.total_field_count || form.field_count || 0,
      form.complexity_score || 0,
      form.nigo_score || 0,
      form.confidence_tier || '',
      form.action_type || '',
      form.signature_required ? 'Yes' : 'No',
      form.signature_analysis?.signature_count || 0,
      form.notarization_required ? 'Yes' : 'No',
      form.attachments_required ? 'Yes' : 'No',
      form.attachment_count || 0,
      form.payment_required ? 'Yes' : 'No',
      form.payment_amount || '',
      form.identification_required ? 'Yes' : 'No',
      form.conditional_logic ? 'Yes' : 'No',
      form.third_party_involved ? 'Yes' : 'No',
      form.witnesses_required ? 'Yes' : 'No',
      form.deadlines_present ? 'Yes' : 'No',
      form.form_purpose || '',
      form.industry_vertical || '',
      form.industry_subvertical || '',
      form.estimated_signer_time || '',
      form.estimated_processing_time || '',
      new Date(form._cached_at || Date.now()).toISOString()
    ];

    rows.push(row.map(escapeCsv).join(','));
  });

  const csvContent = rows.join('\n');
  const timestamp = new Date().toISOString().split('T')[0];
  const filename = `Form_Genome_All_Forms_${timestamp}.csv`;

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function clearAllData() {
  if (!confirm(`Are you sure you want to clear all ${allForms.length} analyzed forms? This cannot be undone.`)) {
    return;
  }

  // Remove all analysis entries from storage
  const keysToRemove = allForms.map(f => f._storage_key);
  await chrome.storage.local.remove(keysToRemove);

  // Reload
  await loadAllForms();
  updateStats();
  renderForms();

  alert('All analysis data has been cleared.');
}
