#!/bin/bash

# Form Genome - Automated Deployment Script (Agent Version)
# Skips interactive auth and assumes environment is ready

set -e  # Exit on error

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Form Genome - Automated Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Configuration
PROJECT_ID="formgenome"
SERVICE_NAME="form-genome-api"
REGION="us-central1"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Ensure project is set
echo -e "${YELLOW}Setting project to ${PROJECT_ID}...${NC}"
gcloud config set project ${PROJECT_ID}

# Step 1: Enable APIs (idempotent)
echo -e "${YELLOW}Step 1: Ensuring required APIs are enabled...${NC}"
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com
echo -e "${GREEN}✅ APIs enabled${NC}"
echo ""

# Step 2: Load environment variables
echo -e "${YELLOW}Step 2: Loading API keys from .env file...${NC}"
if [ ! -f .env ]; then
    echo -e "${RED}❌ Error: .env file not found${NC}"
    exit 1
fi

source .env

if [ -z "$GEMINI_API_KEY" ] || [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${RED}❌ Error: API keys not found in .env file${NC}"
    exit 1
fi

if [ -z "$FORMGENOME_API_TOKENS" ]; then
    echo -e "${RED}❌ Error: FORMGENOME_API_TOKENS (comma-separated client tokens) not found in .env file${NC}"
    exit 1
fi

echo -e "${GREEN}✅ API keys loaded${NC}"
echo ""

# Step 3: Create/Update Secrets
echo -e "${YELLOW}Step 3: Updating secrets in Google Secret Manager...${NC}"

# Update GEMINI_API_KEY
if gcloud secrets describe GEMINI_API_KEY &>/dev/null; then
    echo "Updating existing GEMINI_API_KEY secret..."
    echo -n "$GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
else
    echo "Creating new GEMINI_API_KEY secret..."
    echo -n "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
fi

# Update ANTHROPIC_API_KEY
if gcloud secrets describe ANTHROPIC_API_KEY &>/dev/null; then
    echo "Updating existing ANTHROPIC_API_KEY secret..."
    echo -n "$ANTHROPIC_API_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
else
    echo "Creating new ANTHROPIC_API_KEY secret..."
    echo -n "$ANTHROPIC_API_KEY" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-
fi

# Update FORMGENOME_API_TOKENS
if gcloud secrets describe FORMGENOME_API_TOKENS &>/dev/null; then
    echo "Updating existing FORMGENOME_API_TOKENS secret..."
    echo -n "$FORMGENOME_API_TOKENS" | gcloud secrets versions add FORMGENOME_API_TOKENS --data-file=-
else
    echo "Creating new FORMGENOME_API_TOKENS secret..."
    echo -n "$FORMGENOME_API_TOKENS" | gcloud secrets create FORMGENOME_API_TOKENS --data-file=-
fi

echo -e "${GREEN}✅ Secrets configured${NC}"
echo ""

# Step 4: Build and Deploy
echo -e "${YELLOW}Step 4: Building and deploying to Cloud Run...${NC}"
echo -e "${YELLOW}Building Docker image (${IMAGE_NAME})...${NC}"

# Submit build
gcloud builds submit --tag ${IMAGE_NAME}

echo -e "${GREEN}✅ Image built successfully${NC}"
echo ""

# Deploy to Cloud Run
echo -e "${YELLOW}Deploying to Cloud Run...${NC}"
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_NAME} \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 2 \
    --timeout 300 \
    --max-instances 10 \
    --min-instances 0 \
    --port 8080 \
    --set-env-vars "FLASK_ENV=production,FLASK_DEBUG=false" \
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,FORMGENOME_API_TOKENS=FORMGENOME_API_TOKENS:latest"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}🎉 Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Get service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} \
    --platform managed \
    --region ${REGION} \
    --format 'value(status.url)')

echo -e "${GREEN}Your service is live at:${NC}"
echo -e "${BLUE}${SERVICE_URL}${NC}"
echo ""
