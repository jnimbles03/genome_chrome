# Form Genome - Automated Deployment Script (PowerShell Version)
# Skips interactive auth and assumes environment is ready

$ErrorActionPreference = "Stop"

# Colors
$Green = "[32m"
$Yellow = "[33m"
$Red = "[31m"
$Blue = "[34m"
$Reset = "[0m"

function Write-Color($text, $color) {
    Write-Host "$([char]27)$color$text$([char]27)$Reset"
}

Write-Color "========================================" $Blue
Write-Color "Form Genome - Automated Deployment" $Blue
Write-Color "========================================" $Blue
Write-Host ""

# Configuration
$PROJECT_ID = "formgenome"
$SERVICE_NAME = "form-genome-api"
$REGION = "us-central1"
$IMAGE_NAME = "gcr.io/$PROJECT_ID/$SERVICE_NAME"

# Ensure gcloud is in path
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    $env:Path += ";C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin"
    if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
        Write-Color "Error: gcloud not found even after path update." $Red
        exit 1
    }
}

# Ensure project is set
Write-Color "Setting project to $PROJECT_ID..." $Yellow
gcloud config set project $PROJECT_ID

# Step 1: Enable APIs
Write-Color "Step 1: Ensuring required APIs are enabled..." $Yellow
gcloud services enable cloudbuild.googleapis.com run.googleapis.com containerregistry.googleapis.com secretmanager.googleapis.com
Write-Color "✅ APIs enabled" $Green
Write-Host ""

# Step 2: Load environment variables
Write-Color "Step 2: Loading API keys from .env file..." $Yellow
if (-not (Test-Path .env)) {
    Write-Color "❌ Error: .env file not found" $Red
    exit 1
}

Get-Content .env | ForEach-Object {
    if ($_ -match "^([^#=]+)=(.*)") {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}

$GEMINI_KEY = [Environment]::GetEnvironmentVariable("GEMINI_API_KEY", "Process")
$ANTHROPIC_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "Process")

if ([string]::IsNullOrEmpty($GEMINI_KEY) -or [string]::IsNullOrEmpty($ANTHROPIC_KEY)) {
    Write-Color "❌ Error: API keys not found in .env file" $Red
    exit 1
}

Write-Color "✅ API keys loaded" $Green
Write-Host ""

# Step 3: Create/Update Secrets
Write-Color "Step 3: Updating secrets in Google Secret Manager..." $Yellow

# Helper function for secrets
function Set-Secret($name, $value) {
    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile, $value)
        
        $exists = gcloud secrets describe $name 2>&1 | Out-String
        if ($exists -match "name: projects") {
            Write-Host "Updating existing $name secret..."
            gcloud secrets versions add $name --data-file=$tmpFile
        }
        else {
            Write-Host "Creating new $name secret..."
            gcloud secrets create $name --data-file=$tmpFile
        }
    }
    finally {
        if (Test-Path $tmpFile) { Remove-Item $tmpFile }
    }
}

$DB_PASSWORD = [Environment]::GetEnvironmentVariable("DB_PASSWORD", "Process")

if ([string]::IsNullOrEmpty($DB_PASSWORD)) {
    Write-Color "❌ Error: DB_PASSWORD not found in .env file" $Red
    exit 1
}

Set-Secret "DB_PASSWORD" $DB_PASSWORD
Set-Secret "GEMINI_API_KEY" $GEMINI_KEY
Set-Secret "ANTHROPIC_API_KEY" $ANTHROPIC_KEY

Write-Color "✅ Secrets configured" $Green
Write-Host ""

# Step 4: Build and Deploy
Write-Color "Step 4: Building and deploying to Cloud Run..." $Yellow
Write-Color "Building Docker image ($IMAGE_NAME)..." $Yellow

# Submit build
gcloud builds submit --tag $IMAGE_NAME

Write-Color "✅ Image built successfully" $Green
Write-Host ""

# Deploy to Cloud Run
Write-Color "Deploying to Cloud Run..." $Yellow
gcloud run deploy $SERVICE_NAME `
    --image $IMAGE_NAME `
    --platform managed `
    --region $REGION `
    --allow-unauthenticated `
    --memory 2Gi `
    --cpu 2 `
    --timeout 300 `
    --max-instances 10 `
    --min-instances 0 `
    --port 8080 `
    --set-env-vars "FLASK_ENV=production,FLASK_DEBUG=false,DB_USER=postgres,DB_NAME=formgenome,CLOUD_SQL_CONNECTION_NAME=formgenome:us-central1:form-genome-db" `
    --add-cloudsql-instances "formgenome:us-central1:form-genome-db" `
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,DB_PASSWORD=DB_PASSWORD:latest,FORMGENOME_API_TOKENS=FORMGENOME_API_TOKENS:latest"

Write-Host ""
Write-Color "========================================" $Green
Write-Color "🎉 Deployment Complete!" $Green
Write-Color "========================================" $Green
Write-Host ""

# Get service URL
$SERVICE_URL = gcloud run services describe $SERVICE_NAME `
    --platform managed `
    --region $REGION `
    --format 'value(status.url)'

Write-Color "Your service is live at:" $Green
Write-Color "$SERVICE_URL" $Blue
Write-Host ""
