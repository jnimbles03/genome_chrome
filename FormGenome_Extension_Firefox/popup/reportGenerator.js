// Form Genome Batch Report Generator

// Report values come from page link text, page URLs and analysis results; escape them before
// they are written into the HTML report.
function escapeReportHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function toSafeReportUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : '#';
  } catch (e) {
    return '#';
  }
}

function generateBatchReport(analysisCache, currentPdfs) {
  // Filter analyzed PDFs
  const analyzedForms = currentPdfs
    .map(pdf => {
      const cacheKey = `analysis_${hashUrl(pdf.url)}`;
      const data = analysisCache[cacheKey];
      // RELAXED CHECK: Check for .ok OR .success (from API) OR just non-error data
      const isAnalyzed = data && !data.error && (data.ok || data.success || data.complexity_score !== undefined);

      if (isAnalyzed) {
        return { ...data, pdf_url: pdf.url, pdf_name: pdf.text, language_count: pdf.language_count };
      }
      return null;
    })
    .filter(Boolean);

  if (analyzedForms.length === 0) {
    alert('No analyzed forms found. Please analyze some PDFs first.');
    return;
  }

  // Calculate stats
  const stats = calculateStats(analyzedForms);
  console.log('[FORM GENOME] Report Stats:', stats); // Log stats for debugging

  // Generate HTML report
  const reportHTML = generateReportHTML(analyzedForms, stats);

  // Download as HTML file
  downloadReport(reportHTML, stats.entity_name);
}

function generateCSVReport(analysisCache, currentPdfs) {
  // Filter analyzed PDFs
  const analyzedForms = currentPdfs
    .map(pdf => {
      const cacheKey = `analysis_${hashUrl(pdf.url)}`;
      const data = analysisCache[cacheKey];
      // RELAXED CHECK: Check for .ok OR .success (from API) OR just non-error data
      const isAnalyzed = data && !data.error && (data.ok || data.success || data.complexity_score !== undefined);

      if (isAnalyzed) {
        return { ...data, pdf_url: pdf.url, pdf_name: pdf.text, language_count: pdf.language_count };
      }
      return null;
    })
    .filter(Boolean);

  if (analyzedForms.length === 0) {
    alert('No analyzed forms found. Please analyze some PDFs first.');
    return;
  }

  // Generate CSV content
  const csv = generateCSVContent(analyzedForms);

  // Get entity name for filename
  const bestForm = analyzedForms.find(f => (f.entity_name || f.entity) && (f.entity_name !== 'Unknown' && f.entity !== 'Unknown'));
  const entityName = bestForm?.entity_name || bestForm?.entity || analyzedForms[0]?.entity_name || analyzedForms[0]?.entity || 'Unknown';

  // Download CSV
  downloadCSV(csv, entityName);
}

function generateCSVContent(forms) {
  // Define all columns we want to export
  const headers = [
    'Form Name',
    'Entity Name',
    'PDF URL',
    'Language Count',
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
    'Estimated Processing Time'
  ];

  // Helper to escape CSV values
  const escapeCsv = (value) => {
    if (value === null || value === undefined) return '';
    const str = String(value);
    // Escape quotes and wrap in quotes if contains comma, quote, or newline
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };

  // Build CSV rows
  const rows = [headers.map(escapeCsv).join(',')];

  forms.forEach(form => {
    const row = [
      getFormDisplayName(form),
      form.entity_name || form.entity || '',
      form.pdf_url || '',
      form.language_count || 1,
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
      form.estimated_processing_time || ''
    ];

    rows.push(row.map(escapeCsv).join(','));
  });

  return rows.join('\n');
}

function generateReportHTML(forms, stats) {
  // Generate rows for the table
  const generateTableRows = (items) => {
    return items.map(form => `
      <tr>
        <td class="primary">
          <div class="form-name">${escapeReportHtml(getFormDisplayName(form))}</div>
          <div class="form-meta">
            ${escapeReportHtml(form.pages || 1)} pages • ${escapeReportHtml(form.total_field_count || form.field_count || 0)} fields
            ${form.language_count > 1 ? ` • <span class="lang-tag">+${form.language_count - 1} Langs</span>` : ''}
          </div>
        </td>
        <td>
          <div class="complexity-badge ${getComplexityClass(form.complexity_score)}">
            ${escapeReportHtml(form.complexity_score || 0)}
          </div>
        </td>
        <td>
          ${form.signature_required ? '<span class="tag tag-warn">Sign</span>' : ''}
          ${form.notarization_required ? '<span class="tag tag-warn">Notary</span>' : ''}
          ${form.payment_required ? '<span class="tag tag-warn">Pay</span>' : ''}
          ${!form.signature_required && !form.notarization_required && !form.payment_required ? 
            ((form.total_field_count || form.field_count || 0) > 0 ? '<span class="tag tag-ok" style="background:#eef2ff; color:#4338ca; border-color:#e0e7ff">Data Entry</span>' : '<span class="tag tag-ok">Info Only</span>') 
            : ''}
        </td>
        <td>
          <a href="${escapeReportHtml(toSafeReportUrl(form.pdf_url))}" target="_blank" class="action-link">View PDF &rarr;</a>
        </td>
      </tr>
    `).join('');
  };

  const getComplexityClass = (score) => {
    if (score >= 75) return 'high';
    if (score >= 40) return 'medium';
    return 'low';
  };

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Form Analysis Report — ${escapeReportHtml(stats.entity_name)}</title>
  <style>
    :root {
      --bg: #f8fafc;
      --card: #ffffff;
      --text: #0f172a;
      --sub: #64748b;
      --border: #e2e8f0;
      --primary: #4f46e5;
      --accent: #c084fc;
    }
    body {
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 40px;
      margin: 0;
    }
    .container {
      max-width: 900px;
      margin: 0 auto;
    }
    h1 { font-size: 28px; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 8px; }
    p.sub { color: var(--sub); margin-top: 0; margin-bottom: 32px; font-size: 16px; }
    
    .stats {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
      margin-bottom: 32px;
    }
    .stat-card {
      background: var(--card);
      border: 1px solid var(--border);
      padding: 24px;
      border-radius: 12px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .stat-val { font-size: 32px; font-weight: 700; color: var(--primary); letter-spacing: -0.02em; }
    .stat-label { font-size: 13px; font-weight: 600; color: var(--sub); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 4px; }

    .hero-card {
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
      color: white;
      border-radius: 16px;
      padding: 32px;
      margin-bottom: 32px;
      box-shadow: 0 10px 25px -5px rgba(79, 70, 229, 0.4);
    }
    .hero-card h2 { margin-top: 0; font-size: 20px; opacity: 0.9; }
    .hards { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 24px; }
    .hard { display: flex; gap: 12px; align-items: flex-start; background: rgba(255,255,255,0.1); padding: 16px; border-radius: 8px; }
    .hard svg { flex-shrink: 0; stroke: #fff; opacity: 0.9; }
    .hard strong { display: block; font-size: 15px; margin-bottom: 2px; }
    .hard .badge { display: inline-block; font-size: 11px; background: rgba(255,255,255,0.2); padding: 2px 6px; border-radius: 4px; margin-left: 6px; vertical-align: middle; }
    .hard .note { font-size: 13px; opacity: 0.8; margin-top: 4px; line-height: 1.4; }

    .table-wrap {
      background: var(--card);
      border-radius: 12px;
      border: 1px solid var(--border);
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
    }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; background: #f1f5f9; padding: 12px 24px; font-size: 12px; font-weight: 600; color: var(--sub); text-transform: uppercase; letter-spacing: 0.05em; }
    td { padding: 16px 24px; border-bottom: 1px solid var(--border); vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    
    .form-name { font-weight: 600; color: var(--text); }
    .form-meta { font-size: 12px; color: var(--sub); margin-top: 4px; }
    .lang-tag { background: #e0f2fe; color: #0284c7; padding: 2px 6px; border-radius: 4px; font-weight: 600; }

    .complexity-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 36px;
      height: 36px;
      border-radius: 50%;
      font-weight: 700;
      font-size: 14px;
    }
    .complexity-badge.low { background: #dcfce7; color: #166534; }
    .complexity-badge.medium { background: #fef9c3; color: #854d0e; }
    .complexity-badge.high { background: #fee2e2; color: #991b1b; }

    .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; margin-right: 4px; margin-bottom: 4px; }
    .tag-warn { background: #fff7ed; color: #c2410c; border: 1px solid #ffedd5; }
    .tag-ok { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }

    .action-link { color: var(--primary); text-decoration: none; font-size: 13px; font-weight: 500; }
    .action-link:hover { text-decoration: underline; }

    .footer { text-align: center; margin-top: 48px; color: var(--sub); font-size: 13px; }
  </style>
</head>
<body>

  <div class="container">
    <h1>Where customers feel the friction</h1>
    <p class="sub">This report spotlights the moments that make <strong>${escapeReportHtml(stats.entity_name)}</strong> forms hard to complete, increasing support costs and abandonment.</p>

    <div class="stats">
      <div class="stat-card">
        <div class="stat-val">${escapeReportHtml(stats.avg_complexity)}</div>
        <div class="stat-label">Avg. Complexity Score</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">${escapeReportHtml(stats.hardest_name)}</div>
        <div class="stat-label">Hardest Form</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">${escapeReportHtml(stats.payment_total)}</div>
        <div class="stat-label">Total Payment Requests</div>
      </div>
    </div>

    <div class="hero-card">
      <h2>What makes this hard for people</h2>
      <div class="hards">
        ${stats.friction_metrics.map(metric => `
          <div class="hard">
            ${metric.icon}
            <div>
              <strong>${metric.title}</strong><span class="badge">${metric.percentage}${metric.badge}</span>
              <div class="note">${metric.description}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>

    <h3>Detailed Form Analysis</h3>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Form Name</th>
            <th>Complexity</th>
            <th>Friction Points</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          ${generateTableRows(forms)}
        </tbody>
      </table>
    </div>

    <div class="footer">
      Generated by Form Genome AI on ${new Date().toLocaleDateString()}
    </div>
  </div>

</body>
</html>`;
}


// Helper to download content using a standard Anchor tag (bypasses browser.downloads issues)
function downloadViaAnchor(content, mimeType, filename) {
  try {
    const blob = new Blob([content], { type: mimeType });

    // Safety check for empty blob
    if (blob.size === 0) {
      alert('Error: Generated file is empty.');
      return;
    }

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.dataset.downloadurl = [mimeType, a.download, a.href].join(':'); // Optional hint
    a.download = filename;

    document.body.appendChild(a);
    a.click();

    // Clean up
    document.body.removeChild(a);
    setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 10000);

    console.log(`[FORM GENOME] Download initiated for ${filename} (${blob.size} bytes)`);
  } catch (error) {
    console.error('[FORM GENOME] Download failed:', error);
    alert('Download failed: ' + error.message);
  }
}

function downloadReport(html, entityName) {
  downloadViaAnchor(html, 'text/html', 'Form_Genome_Report_Latest.html');
}

function downloadCSV(csvContent, entityName) {
  downloadViaAnchor(csvContent, 'text/csv;charset=utf-8;', 'Form_Analysis_Latest.csv');
}

function getFormDisplayName(form) {
  // Comprehensive fallback chain for form display name
  // 1. Try API-provided titles first
  let name = form.pretty_title || form.form_title || form.form_name || form.title;

  // 2. If still empty, try to clean up the pdf_name from link text
  if (!name || name.trim() === '') {
    name = form.pdf_name || 'Untitled';
    // Clean up pdf_name: remove duplicate extensions, clean formatting
    name = name
      .replace(/\.pdf\.pdf$/i, '.pdf')  // Remove duplicate .pdf.pdf
      .replace(/\.pdf$/i, '')             // Remove .pdf extension
      .replace(/^[Gg]forms?/i, '')        // Remove "Gform" or "gform" prefix
      .trim();
  }

  return name || 'Untitled';
}

function calculateStats(forms) {
  const total = forms.length;
  const bestForm = forms.find(f => (f.entity_name || f.entity) && (f.entity_name !== 'Unknown' && f.entity !== 'Unknown'));
  const entityName = bestForm?.entity_name || bestForm?.entity || forms[0]?.entity_name || forms[0]?.entity || 'Unknown Organization';

  // Average complexity
  const avgComplexity = (forms.reduce((sum, f) => sum + (f.complexity_score || 0), 0) / total).toFixed(1);

  // Hardest form
  const hardest = forms.reduce((max, f) =>
    (f.complexity_score || 0) > (max.complexity_score || 0) ? f : max
    , forms[0]);

  // Use pretty_title first, with comprehensive fallback chain
  const hardestName = getFormDisplayName(hardest);

  // Payment total
  const paymentTotal = forms.reduce((sum, f) => sum + (parseFloat(f.payment_amount) || 0), 0);
  const paymentTotalFormatted = paymentTotal > 0
    ? `$${paymentTotal.toLocaleString()}`
    : '—';

  // Calculate all friction metrics
  const frictionMetrics = [
    {
      id: 'attachments',
      title: 'Attachment hunting',
      percentage: ((forms.filter(f => f.attachments_required).length / total) * 100).toFixed(0),
      badge: '% of forms',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><path d="M21.44 11.05l-8.49 8.49a6 6 0 11-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.9 9.9a2 2 0 11-2.83-2.83l8.49-8.49"></path></svg>',
      description: 'Chasing proofs (certificates, letters, IDs) across offices is a common derail.'
    },
    {
      id: 'notary',
      title: 'Notary bottlenecks',
      percentage: ((forms.filter(f => f.notarization_required).length / total) * 100).toFixed(0),
      badge: '% require in-person steps',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><path d="M9 7a3 3 0 116 0 3 3 0 01-6 0z"></path><path d="M5 22h14"></path><path d="M4 18h16a1 1 0 001-1v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2a1 1 0 001 1z"></path></svg>',
      description: 'Even small percentages create friction for citizens without easy access to notaries.'
    },
    {
      id: 'conditional',
      title: 'Conditional confusion',
      percentage: ((forms.filter(f => f.conditional_logic).length / total) * 100).toFixed(0),
      badge: '% include branching',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><path d="M6 3v6a4 4 0 004 4h4"></path><circle cx="6" cy="3" r="2"></circle><circle cx="14" cy="13" r="2"></circle><circle cx="18" cy="21" r="2"></circle><path d="M14 15v4a2 2 0 002 2"></path></svg>',
      description: '"If this, then that" logic raises error risk and NIGO returns when people misread paths.'
    },
    {
      id: 'pii',
      title: 'High identification burden',
      percentage: ((forms.filter(f => f.identification_required || (f._flags?.pii_hits > 0)).length / total) * 100).toFixed(0),
      badge: '% request ID/PII',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><path d="M12 2l7 4v6c0 5-3.5 9-7 10-3.5-1-7-5-7-10V6l7-4z"></path></svg>',
      description: 'Sensitive details create pause and slow completion when citizens don\'t have documents on hand.'
    },
    {
      id: 'signatures',
      title: 'Signature requirements',
      percentage: ((forms.filter(f => f.signature_required).length / total) * 100).toFixed(0),
      badge: '% require signatures',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><path d="M3 17h18M3 12h18M3 7h18"></path><path d="M8 21l4-4 4 4"></path></svg>',
      description: 'Manual signatures add friction and delay when not digital-first.'
    },
    {
      id: 'payment',
      title: 'Payment friction',
      percentage: ((forms.filter(f => f.payment_required).length / total) * 100).toFixed(0),
      badge: '% require payment',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><rect x="2" y="5" width="20" height="14" rx="2"></rect><path d="M2 10h20"></path></svg>',
      description: 'Payment requirements add complexity and abandon risk if not streamlined.'
    },
    {
      id: 'complexity',
      title: 'High field density',
      percentage: ((forms.filter(f => (f.total_field_count || f.field_count || 0) > 20).length / total) * 100).toFixed(0),
      badge: '% have 20+ fields',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect></svg>',
      description: 'Forms with many fields increase abandonment and require more time to complete.'
    },
    {
      id: 'pages',
      title: 'Multi-page complexity',
      percentage: ((forms.filter(f => (f.pages || 1) > 3).length / total) * 100).toFixed(0),
      badge: '% have 4+ pages',
      icon: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#C43D00" stroke-width="1.8"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6z"></path><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"></path></svg>',
      description: 'Lengthy forms create cognitive load and increase the likelihood of errors.'
    }
  ];

  // Sort by percentage descending and take top 4
  const topFrictions = frictionMetrics
    .sort((a, b) => parseFloat(b.percentage) - parseFloat(a.percentage))
    .slice(0, 4);

  return {
    total,
    entity_name: entityName,
    avg_complexity: avgComplexity,
    hardest_name: hardestName,
    hardest_complexity: hardest.complexity_score || 0,
    payment_total: paymentTotalFormatted,
    friction_metrics: topFrictions
  };
}

