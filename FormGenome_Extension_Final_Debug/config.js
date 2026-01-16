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
