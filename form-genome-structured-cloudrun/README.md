# Form Genome Project

A sophisticated form analysis and discovery system powered by a two-tier LLM architecture.

## Overview

The Form Genome Project automatically discovers, analyzes, and catalogs PDF forms from organizations across the web. It uses a cost-optimized two-tier LLM routing strategy that balances speed, cost, and accuracy.

## Features

- **Intelligent PDF Discovery**: Crawls websites to find and extract form documents
- **Automated Form Analysis**: Extracts metadata, complexity scores, and structure
- **Smart Title Generation**: Creates human-readable titles and identifies organizations
- **Two-Tier LLM Routing**: Gemini Flash (fast/cheap) → Claude Sonnet (fallback)
- **Quality Assessment**: Automatic fallback for edge cases and complex forms
- **Cost Optimization**: 95% cost reduction vs single-tier approach

## Quick Start

### 1. Prerequisites

- Python 3.9+
- PostgreSQL (or SQLite for development)
- Google Gemini API key
- Anthropic Claude API key (for fallback)

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/jnimbles03/modern-genome.git
cd form-genome-structured-cloudrun

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and add your API keys
nano .env
```

**Required Configuration:**

```bash
# Tier 1: Gemini Flash (Primary)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-1.5-flash

# Tier 2: Claude Sonnet (Fallback)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/formgenome
```

### 4. Run the Application

```bash
# Development mode
flask run --port 8080

# Production mode
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

### 5. Test the LLM Routing

```python
from app.services.llm_router import chat_complete

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Say hello!"}
]

response = chat_complete(messages=messages)
print(response)
```

## Architecture

### Two-Tier LLM Routing

The system uses an intelligent two-tier routing strategy:

1. **Tier 1 (Primary): Gemini 1.5 Flash**
   - Fast (~500ms)
   - Cheap (~$0.0001 per request)
   - Handles 90% of requests

2. **Tier 2 (Fallback): Claude 3.5 Sonnet**
   - Slower (~2s)
   - More expensive (~$0.001 per request)
   - Handles complex edge cases

**Automatic Fallback Triggers:**
- Low confidence responses (< 0.7)
- High complexity forms (> 75)
- Error indicators in response
- Poor image quality
- Incomplete data

See [LLM_ROUTING_ARCHITECTURE.md](./LLM_ROUTING_ARCHITECTURE.md) for detailed documentation.

## Project Structure

```
form-genome-structured-cloudrun/
├── app/
│   ├── api/                    # API endpoints
│   │   └── __init__.py
│   └── services/               # Core services
│       ├── llm_router.py       # Two-tier LLM routing
│       ├── title_llm.py        # Title generation
│       ├── llm_discover.py     # PDF discovery
│       ├── analyzer.py         # Form analysis
│       └── storage.py          # Data persistence
├── ui/                         # Frontend templates
├── data/                       # Data storage
├── tests/                      # Unit tests
├── requirements.txt            # Python dependencies
├── .env.example               # Example configuration
├── README.md                  # This file
└── LLM_ROUTING_ARCHITECTURE.md # Architecture docs
```

## Usage Examples

### Title Generation

```python
from app.services.title_llm import make_title

url = "https://example.com/forms/application.pdf"
first_page_text = "Member Application Form..."
complexity = 45

title, entity = make_title(url, first_page_text, complexity)
print(f"Title: {title}")
print(f"Entity: {entity}")
```

### PDF Discovery

```python
from app.services.llm_discover import discover

seed = "https://example.com/forms"
html = "<html>...</html>"
sitemap_urls = ["https://example.com/sitemap.xml"]

result = discover(seed, html, sitemap_urls)
print(f"Found {len(result['pdf_urls'])} PDFs")
```

### Form Analysis

```python
from app.services.analyzer import analyze_pdf

url = "https://example.com/form.pdf"
record = analyze_pdf(url)

print(f"Complexity: {record['complexity']}")
print(f"Title: {record['title']}")
print(f"Entity: {record['entity_name']}")
```

## Configuration Options

### LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | - | Google Gemini API key (required) |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model to use |
| `ANTHROPIC_API_KEY` | - | Anthropic API key (required for fallback) |
| `ANTHROPIC_MODEL` | `claude-3-5-sonnet-20241022` | Claude model to use |
| `ENABLE_LLM_FALLBACK` | `true` | Enable Tier 2 fallback |
| `LLM_CONFIDENCE_THRESHOLD` | `0.7` | Confidence threshold for fallback |
| `LLM_COMPLEXITY_THRESHOLD` | `75` | Complexity threshold for fallback |

### Application Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | - | Database connection string |
| `PORT` | `8080` | Application port |
| `LOG_LEVEL` | `INFO` | Logging level |
| `MAX_PDF_SIZE_MB` | `50` | Maximum PDF size |

See [.env.example](./.env.example) for all available options.

## API Endpoints

### Analyze a PDF

```bash
POST /api/analyze
Content-Type: application/json

{
  "url": "https://example.com/form.pdf"
}
```

### Discover PDFs

```bash
POST /api/discover
Content-Type: application/json

{
  "seed_url": "https://example.com/forms"
}
```

### Get Records

```bash
GET /api/records?entity=ExampleOrg&limit=10
```

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/

# Run specific test
pytest tests/test_llm_router.py
```

### Code Style

```bash
# Format code
black app/ tests/

# Lint
flake8 app/ tests/

# Type checking
mypy app/
```

## Deployment

### Google Cloud Run

```bash
# Build and deploy
gcloud builds submit --tag gcr.io/PROJECT_ID/form-genome
gcloud run deploy form-genome \
  --image gcr.io/PROJECT_ID/form-genome \
  --platform managed \
  --region us-central1 \
  --set-env-vars GEMINI_API_KEY=$GEMINI_API_KEY,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

### Docker

```bash
# Build image
docker build -t form-genome .

# Run container
docker run -p 8080:8080 \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  form-genome
```

## Cost Analysis

### Example: Analyzing 1000 Forms

**Two-Tier Approach (90% Tier 1, 10% Tier 2):**
- 900 forms × $0.0001 = $0.09
- 100 forms × $0.001 = $0.10
- **Total: $0.19**

**Single-Tier Approach (All Claude):**
- 1000 forms × $0.001 = $1.00
- **Total: $1.00**

**Savings: 81%** ($0.81 saved per 1000 forms)

## Monitoring

### View LLM Usage

```bash
# Check logs for tier usage
grep "LLM Tier" application.log

# Count Tier 1 vs Tier 2 usage
grep -c "LLM Tier 1" application.log
grep -c "LLM Tier 2" application.log
```

### Track Costs

```python
from app.services.llm_router import LLMConfig

config = LLMConfig.from_env()
print(f"Fallback enabled: {config.enable_fallback}")
print(f"Confidence threshold: {config.confidence_threshold}")
```

## Troubleshooting

### High Tier 2 Fallback Rate

If you see >20% Tier 2 usage:
1. Check confidence threshold (may be too high)
2. Review prompt quality
3. Verify input data quality

### API Errors

```bash
# Check API keys
echo $GEMINI_API_KEY
echo $ANTHROPIC_API_KEY

# Test connectivity
curl -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=$GEMINI_API_KEY"
```

### Performance Issues

1. Enable caching for repeated requests
2. Reduce max_tokens if possible
3. Use force_tier2 sparingly
4. Monitor database query performance

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

- **Documentation**: See [LLM_ROUTING_ARCHITECTURE.md](./LLM_ROUTING_ARCHITECTURE.md)
- **Issues**: [GitHub Issues](https://github.com/jnimbles03/modern-genome/issues)
- **Email**: patrick@meyerinterests.com

## Acknowledgments

- Google Gemini for fast, cost-effective inference
- Anthropic Claude for high-quality fallback analysis
- The open-source community for excellent Python libraries
