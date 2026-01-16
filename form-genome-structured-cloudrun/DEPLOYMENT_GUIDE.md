# Form Genome - Google Cloud Run Deployment Guide

## Prerequisites

- Google Cloud SDK (`gcloud`) installed
- Docker installed (optional - Cloud Build will handle it)
- Access to GCP project: `formgenome`

## Quick Deployment (Recommended)

### Step 1: Authenticate with Google Cloud

```bash
gcloud auth login
gcloud config set project formgenome
```

### Step 2: Set up API Key Secrets

```bash
# Load your API keys from .env file
source .env

# Create secrets in Google Secret Manager
echo -n "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
echo -n "$ANTHROPIC_API_KEY" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-
```

**Note**: If secrets already exist, update them instead:
```bash
echo -n "$GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
echo -n "$ANTHROPIC_API_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
```

### Step 3: Deploy to Cloud Run

```bash
./deploy.sh
```

That's it! The deploy script will:
- Enable required Google Cloud APIs
- Build your Docker image using Cloud Build
- Deploy to Cloud Run with optimized settings
- Display your service URL when complete

---

## Manual Deployment (Alternative)

If the deploy script doesn't work, you can deploy manually:

### 1. Enable APIs

```bash
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com
```

### 2. Build and Push Image

```bash
PROJECT_ID="formgenome"
SERVICE_NAME="form-genome-api"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

gcloud builds submit --tag ${IMAGE_NAME}
```

### 3. Deploy to Cloud Run

```bash
gcloud run deploy form-genome-api \
    --image ${IMAGE_NAME} \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 2 \
    --timeout 300 \
    --max-instances 10 \
    --min-instances 0 \
    --port 8080 \
    --set-env-vars "FLASK_ENV=production,FLASK_DEBUG=false" \
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"
```

### 4. Get Service URL

```bash
gcloud run services describe form-genome-api \
    --platform managed \
    --region us-central1 \
    --format 'value(status.url)'
```

---

## Configuration Details

### Cloud Run Settings

| Setting | Value | Description |
|---------|-------|-------------|
| **Memory** | 2 GiB | Handles PDF processing and LLM calls |
| **CPU** | 2 vCPU | Parallel processing support |
| **Timeout** | 300s | Allows for long-running LLM requests |
| **Max Instances** | 10 | Auto-scales based on traffic |
| **Min Instances** | 0 | Scales to zero when idle (saves cost) |
| **Port** | 8080 | Default Flask port |

### Environment Variables

- `FLASK_ENV=production` - Runs in production mode
- `FLASK_DEBUG=false` - Disables debug mode
- `GEMINI_API_KEY` - From Secret Manager
- `ANTHROPIC_API_KEY` - From Secret Manager

### Secrets (Stored in Google Secret Manager)

- `GEMINI_API_KEY` - For Tier 1 LLM (Gemini 2.5 Flash)
- `ANTHROPIC_API_KEY` - For Tier 2 LLM (Claude 3 Opus)

---

## Post-Deployment Testing

Once deployed, test these endpoints:

### 1. Health Check
```bash
SERVICE_URL="your-service-url-here"
curl ${SERVICE_URL}/health
```

Expected response:
```json
{"status": "healthy"}
```

### 2. Drag & Drop UI
Open in browser:
```
https://your-service-url/ui
```

### 3. File Upload API
```bash
curl -X POST ${SERVICE_URL}/api/analyze/upload \
  -F "file=@sample.pdf"
```

### 4. Batch API
```bash
curl ${SERVICE_URL}/api/batch/stats
```

---

## Monitoring & Logs

### View Logs
```bash
gcloud run services logs read form-genome-api \
    --platform managed \
    --region us-central1 \
    --limit 50
```

### View Metrics
```bash
# Open Cloud Console
gcloud run services describe form-genome-api \
    --platform managed \
    --region us-central1
```

Or visit: https://console.cloud.google.com/run?project=formgenome

---

## Cost Optimization

### Current Configuration
- **Tier**: Pay-per-use
- **Min instances**: 0 (scales to zero)
- **Memory**: 2 GiB
- **CPU**: 2 vCPU

### Estimated Costs
- **Idle**: $0/month (scales to zero)
- **Light use** (100 req/day): ~$5-10/month
- **Medium use** (1000 req/day): ~$20-40/month
- **Heavy use** (10000 req/day): ~$100-200/month

### To Reduce Costs
```bash
# Reduce to 1 GiB memory, 1 CPU
gcloud run services update form-genome-api \
    --memory 1Gi \
    --cpu 1 \
    --region us-central1
```

---

## Troubleshooting

### Issue: "Deployment failed"
**Solution**: Check build logs:
```bash
gcloud builds list --limit=5
gcloud builds log [BUILD_ID]
```

### Issue: "Container failed to start"
**Solution**: Check service logs:
```bash
gcloud run services logs read form-genome-api --limit=100
```

### Issue: "API keys not working"
**Solution**: Verify secrets are set:
```bash
gcloud secrets list
gcloud secrets versions access latest --secret="GEMINI_API_KEY"
```

### Issue: "Out of memory"
**Solution**: Increase memory allocation:
```bash
gcloud run services update form-genome-api --memory 4Gi
```

### Issue: "Request timeout"
**Solution**: Increase timeout (max 3600s):
```bash
gcloud run services update form-genome-api --timeout 600
```

---

## Updating the Deployment

### Update Code
```bash
# Make your code changes, then:
git add .
git commit -m "Your changes"
./deploy.sh
```

### Update Secrets
```bash
echo -n "new-api-key" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
```

### Update Configuration Only
```bash
gcloud run services update form-genome-api \
    --memory 4Gi \
    --max-instances 20
```

---

## Rollback

### List Revisions
```bash
gcloud run revisions list --service form-genome-api
```

### Rollback to Previous Revision
```bash
gcloud run services update-traffic form-genome-api \
    --to-revisions [REVISION_NAME]=100
```

---

## Security Best Practices

✅ **Current Setup:**
- API keys stored in Secret Manager (encrypted)
- HTTPS only (enforced by Cloud Run)
- Container runs as non-root user

⚠️ **Recommended Additions:**
- Enable Cloud Armor for DDoS protection
- Set up VPC for private networking
- Enable Cloud Audit Logs
- Implement rate limiting at API level

---

## Support & Documentation

- **Cloud Run Docs**: https://cloud.google.com/run/docs
- **Secret Manager**: https://cloud.google.com/secret-manager/docs
- **Cloud Build**: https://cloud.google.com/build/docs

---

## Quick Reference Commands

```bash
# View service details
gcloud run services describe form-genome-api --region us-central1

# Update service
gcloud run services update form-genome-api --memory 4Gi --region us-central1

# Delete service
gcloud run services delete form-genome-api --region us-central1

# View logs (live)
gcloud run services logs tail form-genome-api --region us-central1

# List all Cloud Run services
gcloud run services list

# Get service URL
gcloud run services describe form-genome-api \
    --format 'value(status.url)' \
    --region us-central1
```

---

**Last Updated**: 2025-11-23
**Version**: 2.0 (5,000 forms capacity + drag-and-drop UI)
