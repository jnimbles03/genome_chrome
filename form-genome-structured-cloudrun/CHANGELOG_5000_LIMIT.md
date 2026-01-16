# Changelog: Increased Processing Limit to 5,000 Forms

**Date**: 2025-11-23
**Change**: Updated maximum form processing capacity from ~200-1000 to **5,000 forms per batch**

## Summary

Increased the batch processing system's capacity to handle up to 5,000 PDF forms in a single discovery/processing operation. This change affects form discovery, batch creation, and LLM token limits.

## Files Modified

### 1. `app/services/llm_discover.py`

**Changes:**
- Increased PDF discovery limits from 500-1000 to **5,000**
- Increased path discovery limits from 100-200 to **500-1,000**
- Increased LLM max_tokens from 2,000-2,500 to **8,000**
- Updated prompts to reflect new capacity

**Specific Updates:**

| Line | Old Value | New Value | Purpose |
|------|-----------|-----------|---------|
| 177 | `[:500]` | `[:5000]` | Sitemap PDF harvest limit |
| 178 | `[:200]` | `[:1000]` | Sitemap path limit |
| 188 | `[:1000]` | `[:5000]` | Quick discovery return limit |
| 214 | `max_tokens=2000` | `max_tokens=8000` | Small LLM call tokens |
| 236 | `[:1000]`, `[:200]` | `[:5000]`, `[:1000]` | LLM small response limits |
| 245 | `[:100]` | `[:500]` | Fallback paths limit |
| 251 | `[:1000]`, `[:200]` | `[:5000]`, `[:1000]` | Small sufficient limits |
| 281 | `max_tokens=2500` | `max_tokens=8000` | Large LLM call tokens |
| 306 | `[:1000]`, `[:200]` | `[:5000]`, `[:1000]` | LLM large response limits |
| 312 | `[:1000]`, `[:100]` | `[:5000]`, `[:500]` | HTML crawl mode limits |
| 119 | "50-200 candidates" | "up to 5000 candidates" | Prompt guidance |
| 139 | "50–200 best candidates" | "up to 5000 best candidates" | Prompt guidance |

### 2. `BATCH_PROCESSING.md`

**Changes:**
- Updated overview to mention "5,000 PDF forms per batch"
- Added new "Processing Limits" section with detailed table

**New Section Added:**
```markdown
## Processing Limits

| Metric | Limit | Description |
|--------|-------|-------------|
| **Forms per batch** | 5,000 | Maximum PDFs per batch job |
| **Discovery paths** | 1,000 | Maximum crawl paths returned |
| **Sitemap harvest** | 5,000 | PDFs extracted from sitemaps |
| **LLM response tokens** | 8,000 | Supports large JSON responses |
| **Asset hints** | 80 | URLs extracted per page |
| **Concurrent workers** | 3-5 | Configurable parallel processing |
| **Rate limit (Gemini)** | 60/min | Tier 1 LLM requests |
| **Rate limit (Claude)** | 50/min | Tier 2 LLM requests |
```

## Impact Assessment

### Positive Impacts

1. **Increased Capacity**: Can now process 5x-10x more forms per batch
2. **Better Sitemap Support**: Large organizations with extensive sitemaps fully supported
3. **Reduced Batch Overhead**: Fewer batches needed for large-scale processing
4. **Improved LLM Responses**: Higher token limits prevent truncation of large result sets

### Considerations

1. **LLM Token Usage**: Higher max_tokens may increase API costs (but only charged for actual usage)
2. **Memory Usage**: Processing 5,000 URLs requires more memory (Python lists ~40KB for 5,000 URLs)
3. **Database Performance**: SQLite may struggle with very large batches (PostgreSQL recommended)
4. **Processing Time**: 5,000 forms at 30 req/min = ~2.8 hours per batch with rate limiting

## Testing

### Recommended Test Cases

1. **Small Batch** (10 forms)
   - Verify no regression in small batch performance
   - Test: `curl -X POST /api/batch/create -d '{"urls": ["url1.pdf", ..., "url10.pdf"]}'`

2. **Medium Batch** (100 forms)
   - Verify rate limiting works correctly
   - Test progress tracking and status updates

3. **Large Batch** (1,000 forms)
   - Verify system stability with large batches
   - Monitor memory and database performance

4. **Maximum Batch** (5,000 forms)
   - Stress test with maximum capacity
   - Verify LLM discovery can handle large result sets
   - Monitor API rate limits and fallback behavior

### Performance Benchmarks

Expected processing times (with rate limiting):

| Batch Size | Processing Time | Workers | Rate Limit |
|------------|-----------------|---------|------------|
| 10 forms | ~20 seconds | 3 | 30/min |
| 100 forms | ~3.3 minutes | 3 | 30/min |
| 1,000 forms | ~33 minutes | 3 | 30/min |
| 5,000 forms | ~2.8 hours | 3 | 30/min |

**With increased rate limits (60 req/min):**

| Batch Size | Processing Time | Workers | Rate Limit |
|------------|-----------------|---------|------------|
| 10 forms | ~10 seconds | 5 | 60/min |
| 100 forms | ~1.7 minutes | 5 | 60/min |
| 1,000 forms | ~17 minutes | 5 | 60/min |
| 5,000 forms | ~1.4 hours | 5 | 60/min |

## Migration Guide

### For Existing Deployments

No migration required. The changes are backward compatible:

1. Existing small batches will continue to work
2. No database schema changes
3. No configuration changes required
4. Existing API endpoints unchanged

### To Take Advantage of New Limits

Simply submit larger batches via the API:

```bash
# Old approach: Create multiple small batches
curl -X POST /api/batch/create -d '{"urls": [...100 URLs...]}'
curl -X POST /api/batch/create -d '{"urls": [...100 URLs...]}'
# etc.

# New approach: Create one large batch
curl -X POST /api/batch/create -d '{"urls": [...5000 URLs...]}'
```

## Configuration Recommendations

### For High-Volume Processing

If processing batches of 1,000+ forms regularly:

1. **Switch to PostgreSQL**:
   ```bash
   DATABASE_URL=postgresql://user:pass@host:5432/formgenome
   ```

2. **Increase Rate Limits** (if your API tier supports it):
   ```bash
   GEMINI_RPM=120
   ANTHROPIC_RPM=100
   ```

3. **Increase Workers**:
   ```bash
   BATCH_NUM_WORKERS=5
   ```

4. **Increase Timeouts**:
   ```bash
   BATCH_PDF_TIMEOUT=60.0
   LLM_RATE_LIMIT_TIMEOUT=120.0
   ```

## Rollback Plan

If issues arise, you can revert to previous limits:

```bash
# Edit app/services/llm_discover.py
# Change all [:5000] back to [:1000]
# Change all [:1000] back to [:200]
# Change max_tokens=8000 back to max_tokens=2000
```

Or simply limit batch sizes in your application logic:

```python
# Limit batches to 1000 forms
if len(urls) > 1000:
    # Split into multiple batches
    batches = [urls[i:i+1000] for i in range(0, len(urls), 1000)]
```

## Future Enhancements

Potential improvements for handling even larger scale:

1. **Streaming Results**: Process and store results as they arrive
2. **Batch Pagination**: Auto-split batches >5,000 into sub-batches
3. **Distributed Processing**: Multiple workers across different machines
4. **Result Caching**: Cache domain-based entity lookups
5. **Incremental Updates**: Only reprocess changed forms

## Support

For questions or issues related to this change:

1. Check test results: `python3 test_batch_system.py`
2. Monitor batch status: `curl http://localhost:8080/api/batch/stats`
3. View documentation: `BATCH_PROCESSING.md`
4. Review logs for rate limiting or timeout issues

---

**Version**: 2.0
**Previous Limit**: ~200-1,000 forms
**New Limit**: 5,000 forms
**Backward Compatible**: ✅ Yes
