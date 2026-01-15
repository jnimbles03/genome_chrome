// Form Genome - Analysis Results Sync Service
// Syncs analyzed forms to central backend for team collaboration

const SYNC_ENABLED = true; // Set to false to disable sync
const SYNC_INTERVAL = 60000; // Sync every 60 seconds
const SYNC_BATCH_SIZE = 10; // Sync 10 forms at a time

let syncInProgress = false;

// Initialize sync service
async function initializeSync() {
  if (!SYNC_ENABLED) {
    console.log('[SYNC] Sync disabled');
    return;
  }

  console.log('[SYNC] Starting sync service');

  // Sync immediately on startup
  await syncAnalysisResults();

  // Then sync periodically
  setInterval(syncAnalysisResults, SYNC_INTERVAL);

  // Listen for new analysis completions
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === 'local') {
      // Check if new analysis was added
      for (const [key, change] of Object.entries(changes)) {
        if (key.startsWith('analysis_') && change.newValue && !change.oldValue) {
          console.log('[SYNC] New analysis detected, triggering sync');
          syncAnalysisResults();
        }
      }
    }
  });
}

async function syncAnalysisResults() {
  if (syncInProgress) {
    console.log('[SYNC] Sync already in progress, skipping');
    return;
  }

  syncInProgress = true;

  try {
    // Get all analysis results from storage
    const storage = await chrome.storage.local.get(null);

    const unsyncedForms = Object.entries(storage)
      .filter(([key, value]) => {
        // Find analysis entries that haven't been synced yet
        return key.startsWith('analysis_') &&
               value &&
               !value.error &&
               value.ok &&
               !value._synced;
      })
      .map(([key, value]) => ({ key, data: value }));

    if (unsyncedForms.length === 0) {
      console.log('[SYNC] No unsynced forms found');
      syncInProgress = false;
      return;
    }

    console.log(`[SYNC] Found ${unsyncedForms.length} unsynced forms`);

    // Sync in batches
    for (let i = 0; i < unsyncedForms.length; i += SYNC_BATCH_SIZE) {
      const batch = unsyncedForms.slice(i, i + SYNC_BATCH_SIZE);
      await syncBatch(batch);
    }

    console.log(`[SYNC] Successfully synced ${unsyncedForms.length} forms`);
  } catch (error) {
    console.error('[SYNC] Error during sync:', error);
  } finally {
    syncInProgress = false;
  }
}

async function syncBatch(batch) {
  // Get user identifier (we'll use Chrome's sync storage for user ID)
  let userId = await getUserIdentifier();

  for (const { key, data } of batch) {
    try {
      // Prepare sync payload
      const payload = {
        user_id: userId,
        analysis_id: key,
        form_data: {
          form_name: data.form_name || data.pretty_title || 'Untitled',
          entity_name: data.entity_name || 'Unknown',
          source_url: data.source_url || data.pdf_url || '',
          complexity_score: data.complexity_score || 0,
          nigo_score: data.nigo_score || 0,
          pages: data.pages || 1,
          total_field_count: data.total_field_count || data.field_count || 0,
          confidence_tier: data.confidence_tier || '',
          action_type: data.action_type || '',
          signature_required: data.signature_required || false,
          signature_count: data.signature_analysis?.signature_count || 0,
          notarization_required: data.notarization_required || false,
          attachments_required: data.attachments_required || false,
          attachment_count: data.attachment_count || 0,
          payment_required: data.payment_required || false,
          payment_amount: data.payment_amount || null,
          identification_required: data.identification_required || false,
          conditional_logic: data.conditional_logic || false,
          third_party_involved: data.third_party_involved || false,
          witnesses_required: data.witnesses_required || false,
          deadlines_present: data.deadlines_present || false,
          form_purpose: data.form_purpose || '',
          industry_vertical: data.industry_vertical || '',
          industry_subvertical: data.industry_subvertical || '',
          estimated_signer_time: data.estimated_signer_time || null,
          estimated_processing_time: data.estimated_processing_time || null,
          analyzed_at: data._cached_at || Date.now()
        }
      };

      // Send to backend API
      const response = await fetch(`${API_BASE_URL}/api/team/sync`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      });

      if (response.ok) {
        // Mark as synced in local storage
        const updatedData = { ...data, _synced: true, _synced_at: Date.now() };
        await chrome.storage.local.set({ [key]: updatedData });
        console.log(`[SYNC] Synced ${key}`);
      } else {
        console.error(`[SYNC] Failed to sync ${key}:`, response.status, response.statusText);
      }
    } catch (error) {
      console.error(`[SYNC] Error syncing ${key}:`, error);
    }
  }
}

async function getUserIdentifier() {
  // Try to get from sync storage first
  try {
    const syncData = await chrome.storage.sync.get('userId');
    if (syncData.userId) {
      return syncData.userId;
    }
  } catch (error) {
    console.warn('[SYNC] Could not access sync storage:', error);
  }

  // Generate a unique identifier for this browser
  let localData = await chrome.storage.local.get('userId');
  if (!localData.userId) {
    // Create new user ID
    const newUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    await chrome.storage.local.set({ userId: newUserId });

    // Try to save to sync storage too
    try {
      await chrome.storage.sync.set({ userId: newUserId });
    } catch (error) {
      console.warn('[SYNC] Could not save to sync storage:', error);
    }

    return newUserId;
  }

  return localData.userId;
}

// Manual sync trigger (can be called from dashboard)
async function triggerManualSync() {
  console.log('[SYNC] Manual sync triggered');
  await syncAnalysisResults();
}

// Force sync ALL cached forms (including already synced ones)
async function forceSyncAll() {
  console.log('[SYNC] Force syncing ALL cached forms...');

  try {
    const storage = await chrome.storage.local.get(null);

    const allForms = Object.entries(storage)
      .filter(([key, value]) => {
        return key.startsWith('analysis_') &&
               value &&
               !value.error &&
               value.ok;
      })
      .map(([key, value]) => ({ key, data: value }));

    console.log(`[SYNC] Found ${allForms.length} total forms in cache`);

    if (allForms.length === 0) {
      console.log('[SYNC] No forms found in cache');
      return;
    }

    // Reset _synced flag on all forms
    for (const { key, data } of allForms) {
      await chrome.storage.local.set({
        [key]: { ...data, _synced: false }
      });
    }

    console.log('[SYNC] Reset sync status on all forms, now syncing...');

    // Now sync them all
    await syncAnalysisResults();

    console.log('[SYNC] Force sync complete!');
  } catch (error) {
    console.error('[SYNC] Error during force sync:', error);
  }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { initializeSync, triggerManualSync, forceSyncAll };
}
