#!/bin/bash
# Load environment variables
source .env

# Create GEMINI_API_KEY secret
echo -n "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-

# Create ANTHROPIC_API_KEY secret
echo -n "$ANTHROPIC_API_KEY" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-

echo "✅ Secrets created successfully"
