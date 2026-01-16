# Deployment Expectations - What to Expect

## When You Run Quick Deploy

### Step 1: Source Environment Variables
```bash
source .env
```
**Expected Output**: (no output, just loads variables)

---

### Step 2: Update Secrets
```bash
echo -n "$GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
echo -n "$ANTHROPIC_API_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
```

**Expected Output**:
```
Created version [2] of the secret [GEMINI_API_KEY].
Created version [2] of the secret [ANTHROPIC_API_KEY].
```

---

### Step 3: Build Docker Image
```bash
gcloud builds submit --tag gcr.io/formgenome/form-genome --timeout=600
```

**Expected Output**:
```
Creating temporary tarball archive of 28 file(s) totalling 231.3 KiB before compression.
Uploading tarball of [.] to [gs://formgenome_cloudbuild/source/...]
Created [https://cloudbuild.googleapis.com/v1/projects/formgenome/locations/global/builds/BUILD_ID].
Logs are available at [https://console.cloud.google.com/cloud-build/builds/BUILD_ID?project=483910792736].

----------------------------- REMOTE BUILD OUTPUT ------------------------------
starting build "BUILD_ID"

FETCHSOURCE
...downloading source...

BUILD
Step 1/13 : FROM python:3.11-slim
Step 2/13 : WORKDIR /app
Step 3/13 : RUN apt-get update && apt-get install -y gcc g++ curl...
...
Step 13/13 : CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 "main:create_app()"
Successfully built IMAGE_SHA
Successfully tagged gcr.io/formgenome/form-genome:latest

PUSH
Pushing gcr.io/formgenome/form-genome
...
latest: digest: sha256:IMAGE_DIGEST size: 4321

DONE
```

**Duration**: ~3-5 minutes

---

### Step 4: Deploy to Cloud Run
```bash
gcloud run deploy form-genome \
    --image gcr.io/formgenome/form-genome \
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

**Expected Output**:
```
Deploying container to Cloud Run service [form-genome] in project [formgenome] region [us-central1]
✓ Deploying new service... Done.
  ✓ Creating Revision...
  ✓ Routing traffic...
  ✓ Setting IAM Policy...
Done.
Service [form-genome] revision [form-genome-00294-xyz] has been deployed and is serving 0 percent of traffic.

Service URL: https://form-genome-nxqo233ggq-uc.a.run.app
```

**Duration**: ~1-2 minutes

**IMPORTANT NOTE**: Notice it says "serving 0 percent of traffic" - this is because the new revision is deployed but NOT receiving traffic yet. You need to route traffic in the next step.

---

### Step 5: Route Traffic to New Revision
```bash
# Get the new revision name from the previous output (e.g., form-genome-00294-xyz)
gcloud run services update-traffic form-genome \
    --to-revisions form-genome-00294-xyz=100 \
    --region us-central1
```

**Expected Output**:
```
URL: https://form-genome-nxqo233ggq-uc.a.run.app
Traffic:
  100% form-genome-00294-xyz
Updating traffic...
Routing traffic...done
Done.
```

**Duration**: ~30 seconds

---

## After Deployment Complete

### What Should Work Immediately

#### 1. Health Check
```bash
curl https://form-genome-nxqo233ggq-uc.a.run.app/health
```
**Expected Response**:
```json
{"status":"healthy"}
```

#### 2. API Status (Root URL)
```bash
curl https://form-genome-nxqo233ggq-uc.a.run.app/
```
**Expected Response**:
```json
{
  "service": "Form Genome Batch Processing API",
  "status": "running",
  "endpoints": {
    "upload_analyze": "POST /api/analyze/upload",
    "create_batch": "POST /api/batch/create",
    "get_status": "GET /api/batch/status/<batch_id>",
    "list_batches": "GET /api/batch/list",
    "queue_stats": "GET /api/batch/stats",
    "batch_health": "GET /api/batch/health",
    "analyze_health": "GET /api/analyze/health"
  }
}
```

#### 3. Genetics Dashboard
```bash
curl https://form-genome-nxqo233ggq-uc.a.run.app/genetics | head -50
```
**Expected Response**: HTML content starting with:
```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Global Genetics Dashboard</title>
...
```

**In Browser**: Visit https://form-genome-nxqo233ggq-uc.a.run.app/genetics
- Should see the genetics dashboard UI
- Same look and feel as before
- All charts and tables working

#### 4. Batch API Stats
```bash
curl https://form-genome-nxqo233ggq-uc.a.run.app/api/batch/stats
```
**Expected Response**:
```json
{
  "stats": {
    "total": 0
  },
  "success": true
}
```

---

### How to Verify Backend Changes Are Working

#### Test 1: Verify 5,000 Form Capacity
The capacity increase is internal - you won't see it unless you run a large batch:

```bash
# This would previously fail or cap at ~1,000 forms
# Now it should accept up to 5,000
curl -X POST https://form-genome-nxqo233ggq-uc.a.run.app/api/batch/create \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "max_forms": 5000,
    "name": "Large Test Batch"
  }'
```

**Expected Response**:
```json
{
  "batch_id": "batch_abc123",
  "status": "created",
  "max_forms": 5000
}
```

#### Test 2: Verify Two-Tier LLM Routing
Check logs to see routing in action:

```bash
gcloud run services logs read form-genome \
  --region us-central1 \
  --limit 100 | grep -E "(Tier|Gemini|Claude)"
```

**Expected Log Entries**:
```
2025-11-23 ... INFO: Using Tier 1 (Gemini 2.5 Flash)
2025-11-23 ... INFO: Tier 1 confidence: 0.87 - accepting result
2025-11-23 ... INFO: Using Tier 1 (Gemini 2.5 Flash)
2025-11-23 ... INFO: Tier 1 confidence: 0.52 - falling back to Tier 2
2025-11-23 ... INFO: Using Tier 2 (Claude 3 Opus)
2025-11-23 ... INFO: Tier 2 response accepted
```

You should see **mostly Tier 1** (Gemini) with **occasional Tier 2** (Claude) fallbacks.

#### Test 3: Verify Strict Data Extraction
Upload a test PDF and check the extracted title/entity:

```bash
# Upload a test file
curl -X POST https://form-genome-nxqo233ggq-uc.a.run.app/api/analyze/upload \
  -F "file=@test_form.pdf"
```

**Expected Response**:
```json
{
  "title": "Member Application",  // ✅ Clean, no version numbers or extensions
  "entity": "Boeing Employees Credit Union",  // ✅ Full legal name, no abbreviations
  "file_type": "pdf",
  "analysis": {
    "complexity": 45,
    "validation": {
      "valid": true,
      "errors": [],
      "warnings": []
    }
  }
}
```

**What to Check**:
- ✅ Title is clean (no `.pdf`, `_v2`, `BECU_`, etc.)
- ✅ Entity is full legal name (not "BECU" but "Boeing Employees Credit Union")
- ✅ Validation passes with no errors

---

## Visual Comparison: Before vs After

### Front-End (User-Facing)
| URL | Before | After | Change |
|-----|--------|-------|--------|
| `/` | API status JSON | API status JSON | ✅ Same |
| `/health` | `{"status":"healthy"}` | `{"status":"healthy"}` | ✅ Same |
| `/genetics` | Genetics dashboard | Genetics dashboard | ✅ Same |
| `/ui` | Drag-drop interface | Drag-drop interface | ✅ Same |

**Visual Result**: **NO CHANGE** - everything looks exactly the same to users

---

### Back-End (Under the Hood)
| Feature | Before | After | Change |
|---------|--------|-------|--------|
| **Batch Capacity** | ~200-1,000 forms | 5,000 forms | ⬆️ 5x increase |
| **LLM Provider** | Claude 3 Opus (100%) | Gemini Flash (95%) + Claude (5%) | 💰 95% cost savings |
| **Title Extraction** | May include junk (v2, .pdf, etc.) | Clean, normalized | ✨ Better quality |
| **Entity Extraction** | May use abbreviations (BECU, GSA) | Full legal names | ✨ Better quality |
| **Entity Consistency** | May vary for same domain | 100% consistent per domain | ✨ Better quality |

**Functional Result**: **MUCH BETTER** - same UI, but faster, cheaper, and higher quality

---

## What You Should NOT See

❌ **Should NOT happen**:
- Root URL showing genetics dashboard (we reverted this)
- 404 errors on any existing endpoints
- Login redirects (old deployment had auth, but it should work)
- Broken UI elements
- Empty API responses

If you see any of these, **rollback immediately**:
```bash
gcloud run services update-traffic form-genome \
    --to-revisions form-genome-00287-h7r=100 \
    --region us-central1
```

---

## Monitoring After Deployment

### 1. Watch Logs in Real-Time
```bash
gcloud run services logs tail form-genome --region us-central1
```

**What to look for**:
- ✅ Successful requests (200 status codes)
- ✅ LLM routing messages (Tier 1/Tier 2)
- ❌ Error messages (500 status codes)
- ❌ API key errors

### 2. Check Metrics
Visit: https://console.cloud.google.com/run/detail/us-central1/form-genome/metrics

**What to monitor**:
- Request count (should be steady)
- Request latency (should be similar to before)
- Error rate (should be near 0%)
- CPU utilization (should be < 50%)
- Memory utilization (should be < 1.5 GiB)

### 3. Test All Critical Paths
```bash
# Test batch creation
curl -X POST https://form-genome-nxqo233ggq-uc.a.run.app/api/batch/create \
  -H "Content-Type: application/json" \
  -d '{"domain": "test.com", "max_forms": 100}'

# Test file upload
curl -X POST https://form-genome-nxqo233ggq-uc.a.run.app/api/analyze/upload \
  -F "file=@test.pdf"

# Test genetics dashboard (in browser)
open https://form-genome-nxqo233ggq-uc.a.run.app/genetics
```

---

## Cost Impact (After 24-48 Hours)

### Before (100% Claude 3 Opus)
- LLM costs: ~$500-1,000/month
- Cloud Run: ~$20-40/month
- **Total**: ~$520-1,040/month

### After (95% Gemini Flash + 5% Claude Opus)
- LLM costs: ~$25-50/month (95% savings)
- Cloud Run: ~$20-40/month (same)
- **Total**: ~$45-90/month

**Expected savings**: ~$475-950/month (90% reduction)

You can verify this in Google Cloud Console after a few days:
- Visit: https://console.cloud.google.com/billing
- Check "Cost breakdown" → Filter by "Cloud Run" and "API usage"

---

## Success Criteria

✅ **Deployment is successful if**:
1. All health checks pass (`/health` returns `{"status":"healthy"}`)
2. Genetics dashboard loads at `/genetics`
3. Batch API accepts jobs up to 5,000 forms
4. Logs show Tier 1 (Gemini) being used for most requests
5. No error spike in Cloud Run metrics
6. All existing functionality still works

❌ **Rollback if**:
1. Any endpoint returns 500 errors consistently
2. Genetics dashboard doesn't load
3. API key errors in logs
4. Error rate > 5% in Cloud Run metrics
5. Any critical functionality is broken

---

## Timeline

From running Quick Deploy to fully operational:

1. **Build Docker image**: 3-5 minutes
2. **Deploy to Cloud Run**: 1-2 minutes
3. **Route traffic**: 30 seconds
4. **Verify deployment**: 2-3 minutes
5. **Monitor for issues**: 30 minutes (recommended)

**Total**: ~10 minutes active work + 30 minutes monitoring

---

## Rollback Procedure (If Needed)

If anything goes wrong:

```bash
# Immediate rollback to stable revision
gcloud run services update-traffic form-genome \
    --to-revisions form-genome-00287-h7r=100 \
    --region us-central1
```

**Rollback time**: 30 seconds

Service will instantly revert to the old stable deployment (Nov 11 version).

---

## Summary: What Changes?

### User Experience
- **Nothing changes** - same UI, same endpoints, same functionality

### Developer Experience
- Can now process 5x more forms per batch
- LLM calls are 95% cheaper
- Data quality is higher (clean titles, full entity names, consistency)

### Operations
- Same infrastructure
- Same deployment process
- Better cost efficiency
- Better data quality

**Bottom line**: Users see no difference, but the system is faster, cheaper, and produces higher quality results.
