// Configuration for Form Genome Chrome Extension

// Team sync API base URL (Cloud Run deployment)
const API_BASE_URL = 'https://form-genome-483910792736.us-central1.run.app';

const CONFIG = {
  // Production API URL
  API_URL: 'https://form-genome-nxqo233ggq-uc.a.run.app',

  // Local development API URL (uncomment to test locally)
  // API_URL: 'http://localhost:8080',

  // API endpoint for PDF analysis
  ANALYZE_ENDPOINT: '/api/analyze/upload'
};

// Team API bearer token, issued by the backend admin with
// form-genome-structured-cloudrun/mint_team_token.py. Stored locally only.
const TEAM_TOKEN_STORAGE_KEY = 'teamApiToken';

async function getTeamApiToken() {
  const stored = await chrome.storage.local.get(TEAM_TOKEN_STORAGE_KEY);
  return stored[TEAM_TOKEN_STORAGE_KEY] || null;
}

async function setTeamApiToken(token) {
  await chrome.storage.local.set({ [TEAM_TOKEN_STORAGE_KEY]: token });
}

async function getTeamAuthHeaders() {
  const token = await getTeamApiToken();
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// The server records forms under the token's `sub` claim.
function getTeamTokenSubject(token) {
  try {
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(payload)).sub || null;
  } catch (error) {
    return null;
  }
}
