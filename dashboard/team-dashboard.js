// Team Form Genome Dashboard - Table View with Inline Editing

let allForms = [];
let filteredForms = [];
let myUserId = null;
let showOnlyMine = false;
let editingRowId = null;
let columnOrder = []; // Global column order (only you can edit)
let draggedColumn = null;
let currentSortKey = 'analyzed_at';
let currentSortDirection = 'desc';

// Define all available columns
const COLUMNS = [
  { key: 'actions', label: 'Actions', draggable: false },
  { key: 'form_name', label: 'Form Name', draggable: true },
  { key: 'entity_name', label: 'Entity', draggable: true },
  { key: 'llm_model', label: 'LLM Model', draggable: true },
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
  myUserId = await getMyUserId();

  // Load column order from storage (shared for all viewers)
  const orderData = await chrome.storage.local.get('columnOrder');
  columnOrder = orderData.columnOrder || COLUMNS.map(c => c.key);

  // Ensure new columns are in the order if not present
  COLUMNS.forEach(col => {
    if (!columnOrder.includes(col.key)) {
      // Insert after entity_name if possible, else append
      const idx = columnOrder.indexOf('entity_name');
      if (idx !== -1) columnOrder.splice(idx + 1, 0, col.key);
      else columnOrder.push(col.key);
    }
  });

  await loadAllTeamForms();
  setupEventListeners();
});

function setupEventListeners() {
  // Refresh button
  document.getElementById('refreshDashboard').addEventListener('click', async () => {
    await loadAllTeamForms();
  });

  // View my forms toggle
  document.getElementById('viewMyForms').addEventListener('click', () => {
    showOnlyMine = !showOnlyMine;
    const btn = document.getElementById('viewMyForms');
    btn.textContent = showOnlyMine ? 'View All Forms' : 'My Forms Only';
    btn.classList.toggle('active', showOnlyMine);
    filterAndRenderForms();
  });

  // Export CSV button
  document.getElementById('exportAllCSV').addEventListener('click', exportAllToCSV);

  // Search input
  document.getElementById('searchInput').addEventListener('input', () => {
    filterAndRenderForms();
  });

  // Filter select
  document.getElementById('filterSelect').addEventListener('change', (e) => {
    filterAndRenderForms();
  });
}

async function getMyUserId() {
  try {
    const localData = await chrome.storage.local.get('userId');
    return localData.userId || 'unknown';
  } catch (error) {
    return 'unknown';
  }
}

async function loadAllTeamForms() {
  showLoading(true);

  try {
    // Fetch all team forms
    const formsResponse = await fetch(`${API_BASE_URL}/api/team/forms?limit=1000`);
    const formsData = await formsResponse.json();

    if (formsData.ok) {
      allForms = formsData.forms;
      console.log(`Loaded ${allForms.length} team forms from API`);
      filterAndRenderForms();
    } else {
      showError('Failed to load team forms');
    }
  } catch (error) {
    console.error('Error loading team forms:', error);
    showError('Unable to connect to backend API. Please check your connection.');
  } finally {
    showLoading(false);
  }
}

function filterAndRenderForms() {
  const searchTerm = document.getElementById('searchInput').value.toLowerCase();
  const filterValue = document.getElementById('filterSelect').value;

  // Start with all forms
  filteredForms = [...allForms];

  // Filter by my forms if enabled
  if (showOnlyMine) {
    filteredForms = filteredForms.filter(form => form.user_id === myUserId);
  }

  // Apply search filter
  if (searchTerm) {
    filteredForms = filteredForms.filter(form => {
      const formName = (form.form_name || '').toLowerCase();
      const entityName = (form.entity_name || '').toLowerCase();
      const url = (form.source_url || '').toLowerCase();
      return formName.includes(searchTerm) || entityName.includes(searchTerm) || url.includes(searchTerm);
    });
  }

  // Apply category filter
  if (filterValue !== 'all') {
    filteredForms = filteredForms.filter(form => {
      switch (filterValue) {
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

  // Sort
  filteredForms.sort((a, b) => {
    let valA = a[currentSortKey];
    let valB = b[currentSortKey];

    // Handle nulls/undefined (always last)
    if (valA === undefined || valA === null) valA = '';
    if (valB === undefined || valB === null) valB = '';

    // Numeric sort check
    const numA = parseFloat(valA);
    const numB = parseFloat(valB);
    if (!isNaN(numA) && !isNaN(numB) && typeof valA !== 'boolean' && typeof valB !== 'boolean') {
      return currentSortDirection === 'asc' ? numA - numB : numB - numA;
    }

    // String sort (case insensitive)
    const strA = String(valA).toLowerCase();
    const strB = String(valB).toLowerCase();

    if (strA < strB) return currentSortDirection === 'asc' ? -1 : 1;
    if (strA > strB) return currentSortDirection === 'asc' ? 1 : -1;
    return 0;
  });

  renderTable();
}

function renderTable() {
  const tbody = document.getElementById('formsTableBody');
  const thead = document.getElementById('tableHeaderRow');
  const table = document.getElementById('formsTable');
  const emptyState = document.getElementById('emptyState');
  const formCount = document.getElementById('formCount');

  formCount.textContent = `${filteredForms.length} forms`;

  if (filteredForms.length === 0) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    return;
  }

  table.style.display = 'table';
  emptyState.style.display = 'none';

  // Render headers in custom order
  renderHeaders(thead);

  // Get ordered columns
  const orderedColumns = getOrderedColumns();

  // Render table body
  tbody.innerHTML = filteredForms.map(form => {
    const isMyForm = form.user_id === myUserId;
    const rowId = `${form.user_id}_${form.analysis_id}`;

    const cells = orderedColumns.map(col => {
      return renderCell(col.key, form, isMyForm, rowId);
    }).join('');

    return `
      <tr class="${isMyForm ? 'editable' : ''}"
          data-row-id="${escapeHtml(rowId)}"
          data-analysis-id="${escapeHtml(form.analysis_id)}"
          data-form="${escapeHtml(JSON.stringify(form))}">
        ${cells}
      </tr>
    `;
  }).join('');

  // Add edit button event listeners
  document.querySelectorAll('.edit-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const rowId = e.target.dataset.editRow;
      startEdit(rowId);
    });
  });
}

function getOrderedColumns() {
  // Return columns in the saved order
  return columnOrder.map(key => COLUMNS.find(c => c.key === key)).filter(Boolean);
}

function renderHeaders(thead) {
  const orderedColumns = getOrderedColumns();

  thead.innerHTML = orderedColumns.map((col, index) => {
    const draggableAttr = col.draggable ? 'draggable="true"' : '';
    const draggableClass = col.draggable ? 'draggable' : '';
    const dragIndicator = col.draggable ? '<span class="drag-indicator">⋮⋮</span>' : '';

    // Sort indicator
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
          data-column-index="${index}"
          style="cursor: ${col.key === 'actions' ? 'default' : 'pointer'}">
        <div style="display: flex; align-items: center;">
            ${dragIndicator}
            <span class="header-text" onclick="handleSort('${col.key}')">${col.label}${sortIndicator}</span>
        </div>
      </th>
    `;
  }).join('');

  // Setup drag and drop for columns
  setupColumnDragAndDrop();
}

// Function exposed to global scope for onclick handler
window.handleSort = function (key) {
  if (key === 'actions') return;

  if (currentSortKey === key) {
    currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    currentSortKey = key;
    currentSortDirection = 'desc'; // Default new sort to desc
  }
  filterAndRenderForms();
};

function renderCell(columnKey, form, isMyForm, rowId) {
  const complexity = form.complexity_score || 0;
  const complexityClass = complexity >= 7 ? 'complexity-high' : complexity >= 4 ? 'complexity-medium' : 'complexity-low';

  switch (columnKey) {
    case 'actions':
      return `<td>${isMyForm ? `<button class="edit-btn" data-edit-row="${escapeHtml(rowId)}">Edit</button>` : ''}</td>`;

    case 'form_name':
      return `<td class="form-name">${escapeHtml(form.form_name || 'Untitled')}</td>`;

    case 'entity_name':
      return `<td>${escapeHtml(form.entity_name || '')}</td>`;

    case 'llm_model':
      const model = form.llm_model || 'Unknown';
      const badgeClass = model.includes('Gemini') ? 'badge-gemini' : 'badge-claude';
      const style = model.includes('Gemini') ? 'background: #e6f4ea; color: #137333; padding: 2px 6px; border-radius: 4px; font-size: 11px;'
        : 'background: #fce8e6; color: #c5221f; padding: 2px 6px; border-radius: 4px; font-size: 11px;';
      return `<td><span style="${style}">${escapeHtml(model)}</span></td>`;

    case 'source_url':
      return `<td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(form.source_url || '')}</td>`;

    case 'pages':
      return `<td>${escapeHtml(form.pages || 1)}</td>`;

    case 'total_field_count':
      return `<td>${escapeHtml(form.total_field_count || 0)}</td>`;

    case 'complexity_score':
      return `<td class="${complexityClass}">${escapeHtml(complexity)}</td>`;

    case 'nigo_score':
      return `<td>${escapeHtml(form.nigo_score || 0)}</td>`;

    case 'confidence_tier':
      return `<td>${escapeHtml(form.confidence_tier || 'High')}</td>`;

    case 'action_type':
      return `<td>${escapeHtml(form.action_type || '')}</td>`;

    case 'signature_required':
      return `<td class="${form.signature_required ? 'yes' : 'no'}">${form.signature_required ? 'Yes' : 'No'}</td>`;

    case 'signature_count':
      return `<td>${escapeHtml(form.signature_count || 0)}</td>`;

    case 'notarization_required':
      return `<td class="${form.notarization_required ? 'yes' : 'no'}">${form.notarization_required ? 'Yes' : 'No'}</td>`;

    case 'attachments_required':
      return `<td class="${form.attachments_required ? 'yes' : 'no'}">${form.attachments_required ? 'Yes' : 'No'}</td>`;

    case 'attachment_count':
      return `<td>${escapeHtml(form.attachment_count || 0)}</td>`;

    case 'payment_required':
      return `<td class="${form.payment_required ? 'yes' : 'no'}">${form.payment_required ? 'Yes' : 'No'}</td>`;

    case 'payment_amount':
      return `<td>${escapeHtml(form.payment_amount || '')}</td>`;

    case 'identification_required':
      return `<td class="${form.identification_required ? 'yes' : 'no'}">${form.identification_required ? 'Yes' : 'No'}</td>`;

    case 'conditional_logic':
      return `<td class="${form.conditional_logic ? 'yes' : 'no'}">${form.conditional_logic ? 'Yes' : 'No'}</td>`;

    case 'third_party_involved':
      return `<td class="${form.third_party_involved ? 'yes' : 'no'}">${form.third_party_involved ? 'Yes' : 'No'}</td>`;

    case 'witnesses_required':
      return `<td class="${form.witnesses_required ? 'yes' : 'no'}">${form.witnesses_required ? 'Yes' : 'No'}</td>`;

    case 'deadlines_present':
      return `<td class="${form.deadlines_present ? 'yes' : 'no'}">${form.deadlines_present ? 'Yes' : 'No'}</td>`;

    case 'form_purpose':
      return `<td style="max-width: 150px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(form.form_purpose || '')}</td>`;

    case 'industry_vertical':
      return `<td>${escapeHtml(form.industry_vertical || '')}</td>`;

    case 'industry_subvertical':
      return `<td>${escapeHtml(form.industry_subvertical || '')}</td>`;

    case 'estimated_signer_time':
      return `<td>${escapeHtml(form.estimated_signer_time || '')}</td>`;

    case 'estimated_processing_time':
      return `<td>${escapeHtml(form.estimated_processing_time || '')}</td>`;

    case 'analyzed_at':
      return `<td>${new Date(form.analyzed_at || Date.now()).toLocaleDateString()}</td>`;

    default:
      return `<td></td>`;
  }
}

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
  if (e.preventDefault) {
    e.preventDefault();
  }
  e.dataTransfer.dropEffect = 'move';

  const target = e.currentTarget;
  if (draggedColumn !== target && target.classList.contains('draggable')) {
    target.classList.add('drag-over');
  }

  return false;
}

function handleColumnDrop(e) {
  if (e.stopPropagation) {
    e.stopPropagation();
  }

  const target = e.currentTarget;
  if (draggedColumn !== target && draggedColumn && target.classList.contains('draggable')) {
    const draggedKey = draggedColumn.dataset.columnKey;
    const targetKey = target.dataset.columnKey;

    // Swap column positions in the columnOrder array
    const draggedIndex = columnOrder.indexOf(draggedKey);
    const targetIndex = columnOrder.indexOf(targetKey);

    if (draggedIndex !== -1 && targetIndex !== -1) {
      // Remove dragged column from its position
      columnOrder.splice(draggedIndex, 1);

      // Insert it at the target position
      const newTargetIndex = columnOrder.indexOf(targetKey);
      columnOrder.splice(newTargetIndex, 0, draggedKey);

      // Save and re-render
      saveColumnOrder();
      renderTable();
    }
  }

  target.classList.remove('drag-over');
  return false;
}

function handleColumnDragEnd(e) {
  e.currentTarget.classList.remove('dragging');

  // Remove drag-over class from all headers
  document.querySelectorAll('th').forEach(header => {
    header.classList.remove('drag-over');
  });

  draggedColumn = null;
}

async function saveColumnOrder() {
  // Persist column order to Chrome storage
  await chrome.storage.local.set({ columnOrder: columnOrder });
  console.log('[DASHBOARD] Column order saved:', columnOrder);
}

function startEdit(rowId) {
  if (editingRowId) {
    alert('Please save or cancel the current edit first');
    return;
  }

  const row = document.querySelector(`tr[data-row-id="${CSS.escape(rowId)}"]`);
  const form = JSON.parse(row.dataset.form);
  editingRowId = rowId;

  row.classList.add('editing');

  // Get ordered columns
  const orderedColumns = getOrderedColumns();

  // Replace row with editable fields
  const editCells = orderedColumns.map(col => {
    return renderEditCell(col.key, form);
  }).join('');

  row.innerHTML = editCells;

  // Add event listeners for save and cancel buttons
  row.querySelector('.save-btn')?.addEventListener('click', (e) => {
    const rowId = e.target.dataset.saveRow;
    saveEdit(rowId);
  });

  row.querySelector('.cancel-btn')?.addEventListener('click', (e) => {
    const rowId = e.target.dataset.cancelRow;
    cancelEdit(rowId);
  });
}

function renderEditCell(columnKey, form) {
  const rowId = `${form.user_id}_${form.analysis_id}`;

  switch (columnKey) {
    case 'actions':
      return `<td>
        <button class="save-btn" data-save-row="${escapeHtml(rowId)}">Save</button>
        <button class="cancel-btn" data-cancel-row="${escapeHtml(rowId)}">Cancel</button>
      </td>`;

    case 'form_name':
      return `<td><input class="edit-field" name="form_name" value="${escapeHtml(form.form_name || '')}" /></td>`;

    case 'entity_name':
      return `<td><input class="edit-field" name="entity_name" value="${escapeHtml(form.entity_name || '')}" /></td>`;

    case 'llm_model':
      // LLM Model is not editable
      return `<td>${escapeHtml(form.llm_model || '')}</td>`;

    case 'source_url':
      return `<td><input class="edit-field" name="source_url" value="${escapeHtml(form.source_url || '')}" /></td>`;

    case 'pages':
      return `<td>${renderNumberInput('pages', form.pages || 1)}</td>`;

    case 'total_field_count':
      return `<td>${renderNumberInput('total_field_count', form.total_field_count || 0)}</td>`;

    case 'complexity_score':
      return `<td>${renderNumberInput('complexity_score', form.complexity_score || 0, 'min="0" max="10"')}</td>`;

    case 'nigo_score':
      return `<td>${renderNumberInput('nigo_score', form.nigo_score || 0, 'min="0" max="10"')}</td>`;

    case 'confidence_tier':
      return `<td>
        <select class="edit-field" name="confidence_tier">
          <option value="High" ${form.confidence_tier === 'High' ? 'selected' : ''}>High</option>
          <option value="Medium" ${form.confidence_tier === 'Medium' ? 'selected' : ''}>Medium</option>
          <option value="Low" ${form.confidence_tier === 'Low' ? 'selected' : ''}>Low</option>
        </select>
      </td>`;

    case 'action_type':
      return `<td><input class="edit-field" name="action_type" value="${escapeHtml(form.action_type || '')}" /></td>`;

    case 'signature_required':
      return `<td>
        <select class="edit-field" name="signature_required">
          <option value="true" ${form.signature_required ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.signature_required ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'signature_count':
      return `<td>${renderNumberInput('signature_count', form.signature_count || 0)}</td>`;

    case 'notarization_required':
      return `<td>
        <select class="edit-field" name="notarization_required">
          <option value="true" ${form.notarization_required ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.notarization_required ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'attachments_required':
      return `<td>
        <select class="edit-field" name="attachments_required">
          <option value="true" ${form.attachments_required ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.attachments_required ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'attachment_count':
      return `<td>${renderNumberInput('attachment_count', form.attachment_count || 0)}</td>`;

    case 'payment_required':
      return `<td>
        <select class="edit-field" name="payment_required">
          <option value="true" ${form.payment_required ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.payment_required ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'payment_amount':
      return `<td><input class="edit-field" name="payment_amount" value="${escapeHtml(form.payment_amount || '')}" /></td>`;

    case 'identification_required':
      return `<td>
        <select class="edit-field" name="identification_required">
          <option value="true" ${form.identification_required ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.identification_required ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'conditional_logic':
      return `<td>
        <select class="edit-field" name="conditional_logic">
          <option value="true" ${form.conditional_logic ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.conditional_logic ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'third_party_involved':
      return `<td>
        <select class="edit-field" name="third_party_involved">
          <option value="true" ${form.third_party_involved ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.third_party_involved ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'witnesses_required':
      return `<td>
        <select class="edit-field" name="witnesses_required">
          <option value="true" ${form.witnesses_required ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.witnesses_required ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'deadlines_present':
      return `<td>
        <select class="edit-field" name="deadlines_present">
          <option value="true" ${form.deadlines_present ? 'selected' : ''}>Yes</option>
          <option value="false" ${!form.deadlines_present ? 'selected' : ''}>No</option>
        </select>
      </td>`;

    case 'form_purpose':
      return `<td><input class="edit-field" name="form_purpose" value="${escapeHtml(form.form_purpose || '')}" /></td>`;

    case 'industry_vertical':
      return `<td><input class="edit-field" name="industry_vertical" value="${escapeHtml(form.industry_vertical || '')}" /></td>`;

    case 'industry_subvertical':
      return `<td><input class="edit-field" name="industry_subvertical" value="${escapeHtml(form.industry_subvertical || '')}" /></td>`;

    case 'estimated_signer_time':
      return `<td><input class="edit-field" name="estimated_signer_time" value="${escapeHtml(form.estimated_signer_time || '')}" /></td>`;

    case 'estimated_processing_time':
      return `<td><input class="edit-field" name="estimated_processing_time" value="${escapeHtml(form.estimated_processing_time || '')}" /></td>`;

    case 'analyzed_at':
      return `<td>${new Date(form.analyzed_at || Date.now()).toLocaleDateString()}</td>`;

    default:
      return `<td></td>`;
  }
}

async function saveEdit(rowId) {
  const row = document.querySelector(`tr[data-row-id="${CSS.escape(rowId)}"]`);
  const form = JSON.parse(row.dataset.form);

  // Collect edited values
  const inputs = row.querySelectorAll('.edit-field');
  const updatedData = { ...form };

  inputs.forEach(input => {
    const name = input.name;
    let value = input.value;

    // Convert boolean strings to actual booleans
    if (value === 'true') value = true;
    else if (value === 'false') value = false;
    // Convert numeric strings to numbers
    else if (input.type === 'number') value = parseFloat(value) || 0;

    updatedData[name] = value;
  });

  try {
    // Send update to backend
    const response = await fetch(`${API_BASE_URL}/api/team/update`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        user_id: form.user_id,
        analysis_id: form.analysis_id,
        form_data: updatedData
      })
    });

    if (response.ok) {
      // Update local data
      const index = allForms.findIndex(f => f.user_id === form.user_id && f.analysis_id === form.analysis_id);
      if (index !== -1) {
        allForms[index] = updatedData;
      }

      editingRowId = null;
      filterAndRenderForms();
      alert('Form updated successfully!');
    } else {
      throw new Error('Failed to update form');
    }
  } catch (error) {
    console.error('Error updating form:', error);
    alert('Failed to update form. Please try again.');
  }
}

function cancelEdit(rowId) {
  editingRowId = null;
  filterAndRenderForms();
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// A number input silently blanks any value that is not a valid floating-point
// number, so non-numeric stored values are edited as text instead of being lost.
function renderNumberInput(name, value, extraAttrs = '') {
  const isNumeric = /^-?(\d+(\.\d+)?|\.\d+)([eE][-+]?\d+)?$/.test(String(value));
  const typeAttr = isNumeric ? `type="number" ${extraAttrs}` : 'type="text"';
  return `<input class="edit-field" ${typeAttr} name="${escapeHtml(name)}" value="${escapeHtml(value)}" />`;
}

function showLoading(show) {
  const loadingState = document.getElementById('loadingState');
  const table = document.getElementById('formsTable');

  if (show) {
    loadingState.style.display = 'block';
    table.style.display = 'none';
  } else {
    loadingState.style.display = 'none';
  }
}

function showError(message) {
  const table = document.getElementById('formsTable');
  const tbody = document.getElementById('formsTableBody');

  table.style.display = 'table';
  tbody.innerHTML = `
    <tr>
      <td colspan="28" style="text-align: center; padding: 40px; color: #ef4444;">
        <strong>Error:</strong> ${escapeHtml(message)}
      </td>
    </tr>
  `;
}

function exportAllToCSV() {
  if (filteredForms.length === 0) {
    alert('No forms to export!');
    return;
  }

  const headers = [
    'Form Name', 'Entity Name', 'LLM Model', 'PDF URL', 'Pages', 'Total Fields',
    'Complexity Score', 'NIGO Score', 'Confidence Tier', 'Action Type',
    'Signature Required', 'Signature Count', 'Notarization Required',
    'Attachments Required', 'Attachment Count', 'Payment Required',
    'Payment Amount', 'Identification Required', 'Conditional Logic',
    'Third Party Involved', 'Witnesses Required', 'Deadlines Present',
    'Form Purpose', 'Industry Vertical', 'Industry Subvertical',
    'Estimated Signer Time', 'Estimated Processing Time', 'Analyzed Date', 'User ID'
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
      form.form_name || '',
      form.entity_name || '',
      form.llm_model || 'Unknown',
      form.source_url || '',
      form.pages || 1,
      form.total_field_count || 0,
      form.complexity_score || 0,
      form.nigo_score || 0,
      form.confidence_tier || '',
      form.action_type || '',
      form.signature_required ? 'Yes' : 'No',
      form.signature_count || 0,
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
      new Date(form.analyzed_at || Date.now()).toISOString(),
      form.user_id || ''
    ];

    rows.push(row.map(escapeCsv).join(','));
  });

  const csvContent = rows.join('\n');
  const timestamp = new Date().toISOString().split('T')[0];
  const filename = `Team_Forms_${timestamp}.csv`;

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}
