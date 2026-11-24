#!/bin/bash

# Form Genome - Google Cloud Run Deployment Script
# This script deploys the Form Genome application to Google Cloud Run

set -e  # Exit on error

# Configuration
PROJECT_ID="formgenome"
SERVICE_NAME="form-genome-api"
REGION="us-central1"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Form Genome - Cloud Run Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed${NC}"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Set project
echo -e "${YELLOW}Setting GCP project to ${PROJECT_ID}...${NC}"
gcloud config set project ${PROJECT_ID}

# Enable required APIs
echo -e "${YELLOW}Enabling required Google Cloud APIs...${NC}"
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com

# Build and push Docker image
echo ""
echo -e "${YELLOW}Building Docker image...${NC}"
gcloud builds submit --tag ${IMAGE_NAME}

# Check if secrets exist (optional - for environment variables)
echo ""
echo -e "${YELLOW}Checking for secrets...${NC}"

# You can set secrets like this:
# gcloud secrets create GEMINI_API_KEY --data-file=<(echo -n "your-key-here")
# gcloud secrets create ANTHROPIC_API_KEY --data-file=<(echo -n "your-key-here")

# Deploy to Cloud Run
echo ""
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
    --set-env-vars "FLASK_ENV=production" \
    --set-env-vars "FLASK_DEBUG=false" \
    --set-secrets "SECRET_KEY=FORM_GENOME_SECRET_KEY:latest"

# Note: For secrets, add these flags:
# --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest" \
# --set-secrets "ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"

# Get the service URL
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} \
    --platform managed \
    --region ${REGION} \
    --format 'value(status.url)')

echo -e "${GREEN}Service URL: ${SERVICE_URL}${NC}"
echo ""
echo "Test endpoints:"
echo "  Health: ${SERVICE_URL}/health"
echo "  UI: ${SERVICE_URL}/ui"
echo "  API: ${SERVICE_URL}/api/analyze/upload"
echo ""
echo -e "${YELLOW}Note: Remember to set your API keys as secrets:${NC}"
echo "  gcloud secrets create GEMINI_API_KEY --data-file=<(echo -n 'your-key-here')"
echo "  gcloud secrets create ANTHROPIC_API_KEY --data-file=<(echo -n 'your-key-here')"
echo ""
