#!/bin/bash
# Load environment variables
source .env

# Create GEMINI_API_KEY secret
echo -n "$GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-

# Create ANTHROPIC_API_KEY secret
echo -n "$ANTHROPIC_API_KEY" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-

# Create FORMGENOME_API_TOKENS secret (token:identity pairs for the analysis API)
echo -n "$FORMGENOME_API_TOKENS" | gcloud secrets create FORMGENOME_API_TOKENS --data-file=-

echo "✅ Secrets created successfully"
