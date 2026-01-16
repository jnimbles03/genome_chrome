#!/bin/bash

# Form Genome - Complete Setup and Deployment
# Run this script in your terminal to deploy to Google Cloud Run

set -e  # Exit on error

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Form Genome - Setup & Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Step 1: Authenticate
echo -e "${YELLOW}Step 1: Authenticating with Google Cloud...${NC}"
gcloud auth login
gcloud config set project formgenome
echo -e "${GREEN}✅ Authentication complete${NC}"
echo ""

# Step 2: Enable APIs
echo -e "${YELLOW}Step 2: Enabling required APIs...${NC}"
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com
echo -e "${GREEN}✅ APIs enabled${NC}"
echo ""

# Step 3: Load environment variables
echo -e "${YELLOW}Step 3: Loading API keys from .env file...${NC}"
if [ ! -f .env ]; then
    echo -e "${RED}❌ Error: .env file not found${NC}"
    echo "Please create a .env file with:"
    echo "  GEMINI_API_KEY=your_key_here"
    echo "  ANTHROPIC_API_KEY=your_key_here"
    exit 1
fi

source .env

if [ -z "$GEMINI_API_KEY" ] || [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${RED}❌ Error: API keys not found in .env file${NC}"
    exit 1
fi

echo -e "${GREEN}✅ API keys loaded${NC}"
echo ""

# Step 4: Create/Update Secrets
echo -e "${YELLOW}Step 4: Setting up secrets in Google Secret Manager...${NC}"

# Check if GEMINI_API_KEY secret exists
if gcloud secrets describe GEMINI_API_KEY &>/dev/null; then
    echo "Updating existing GEMINI_API_KEY secret..."
    echo -n "$GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
else
    echo "Creating new GEMINI_API_KEY secret..."
    echo -n "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
fi

# Check if ANTHROPIC_API_KEY secret exists
if gcloud secrets describe ANTHROPIC_API_KEY &>/dev/null; then
    echo "Updating existing ANTHROPIC_API_KEY secret..."
    echo -n "$ANTHROPIC_API_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
else
    echo "Creating new ANTHROPIC_API_KEY secret..."
    echo -n "$ANTHROPIC_API_KEY" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-
fi

echo -e "${GREEN}✅ Secrets configured${NC}"
echo ""

# Step 5: Build and Deploy
echo -e "${YELLOW}Step 5: Building and deploying to Cloud Run...${NC}"
echo ""

PROJECT_ID="formgenome"
SERVICE_NAME="form-genome-api"
REGION="us-central1"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Build image
echo -e "${YELLOW}Building Docker image...${NC}"
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
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"

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
echo "Test endpoints:"
echo "  🏥 Health: ${SERVICE_URL}/health"
echo "  🖱️  UI: ${SERVICE_URL}/ui"
echo "  📤 Upload API: ${SERVICE_URL}/api/analyze/upload"
echo "  📊 Batch Stats: ${SERVICE_URL}/api/batch/stats"
echo ""
echo -e "${YELLOW}Opening UI in browser...${NC}"
open "${SERVICE_URL}/ui" 2>/dev/null || echo "Visit: ${SERVICE_URL}/ui"
echo ""
