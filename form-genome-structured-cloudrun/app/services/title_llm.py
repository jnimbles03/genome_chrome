# app/services/title_llm.py
# LLM-based title generator with STRICT extraction rules
# Uses two-tier routing: Gemini Flash → Claude Opus fallback

import os
import re
import logging
from urllib.parse import urlparse
from app.services.llm_router import chat_complete, LLMError
from app.services.sms_alerts import send_llm_failure_alert
from app.services.domain_mappings import get_entity_from_domain, is_domain_known

logger = logging.getLogger(__name__)

# =============================================================================
# STRICT RULE #1: Form Name Normalization
# =============================================================================

def normalize_form_title(raw_title: str, url: str = "") -> str:
    """
    Normalize form title to be "pretty" and clean.

    Rules:
    - Remove file extensions (.pdf, .docx, etc.)
    - Remove underscores and replace with spaces
    - Remove version numbers (v1, v2, etc.)
    - Remove dates (YYYY-MM-DD, MM-DD-YY, etc.)
    - Remove filename artifacts (Packet, Interactive, etc.)
    - Convert to proper Title Case
    - Limit to 75 characters

    Examples:
        "BECU_Genome_10.12.pdf" → "Genome 10.12"
        "P-1250_Member_Application_v2.pdf" → "Member Application"
        "FM-AO-Account-Options.pdf" → "Account Options"

    Args:
        raw_title: Raw form title or filename
        url: Optional URL for context

    Returns:
        Clean, pretty form title
    """
    if not raw_title:
        return ""

    title = raw_title.strip()

    # Remove file extensions
    title = re.sub(r'\.(pdf|docx?|xlsx?|txt|rtf|odt)$', '', title, flags=re.IGNORECASE)

    # Remove common filename prefixes from domain (e.g., "BECU_" from becu.org)
    if url:
        domain = urlparse(url).netloc.lower()
        # Extract organization abbreviation from domain (e.g., "becu" from "becu.org")
        org_abbrev = domain.split('.')[0] if domain else ""
        if org_abbrev:
            # Remove patterns like "BECU_", "becu-", "BECU-", etc.
            title = re.sub(rf'^{org_abbrev}[-_\s]+', '', title, flags=re.IGNORECASE)

    # Replace underscores and hyphens with spaces (but preserve dashes in titles)
    title = title.replace('_', ' ').replace('-', ' ')

    # Remove version numbers
    title = re.sub(r'\b[vV]\d+(\.\d+)?\b', '', title)

    # Remove dates (various formats)
    title = re.sub(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b', '', title)  # YYYY-MM-DD
    title = re.sub(r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b', '', title)  # MM/DD/YY(YY)
    title = re.sub(r'\b\d{8}\b', '', title)  # YYYYMMDD

    # Remove common filename artifacts
    artifacts = [
        'packet', 'interactive', 'fillable', 'editable', 'final',
        'draft', 'revised', 'updated', 'new', 'form', 'document'
    ]
    for artifact in artifacts:
        title = re.sub(rf'\b{artifact}\b', '', title, flags=re.IGNORECASE)

    # Remove form codes (e.g., P-1250, FM-AO, SCU-4098)
    # But keep official government form codes (SF, OPM, IRS)
    title = re.sub(r'\b[A-Z]{1,3}-\d{2,6}\b', '', title)  # Remove codes like P-1250, FM-AO
    title = re.sub(r'\b[A-Z]\s\d{2,6}\b', '', title)  # Remove codes like P 1250

    # Clean up whitespace
    title = ' '.join(title.split())

    # Convert to Title Case (but preserve acronyms)
    words = title.split()
    title_words = []
    for word in words:
        # Keep all-caps words (acronyms) as-is
        if word.isupper() and len(word) >= 2:
            title_words.append(word)
        # Keep words with numbers
        elif any(c.isdigit() for c in word):
            title_words.append(word)
        # Title case everything else
        else:
            title_words.append(word.capitalize())

    title = ' '.join(title_words)

    # Trim to max length
    if len(title) > 75:
        title = title[:72].rsplit(' ', 1)[0] + '...'

    # Final cleanup
    title = title.strip(' .-_')

    return title


# =============================================================================
# STRICT RULE #2 & #3: Legal Entity Names with Domain Consistency
# =============================================================================

def enforce_entity_consistency(llm_entity: str, url: str) -> str:
    """
    Enforce entity consistency with LLM-first approach.

    Priority:
    1. Trust LLM extraction if it's valid and specific
    2. Use domain mapping as fallback or validation
    3. Return None if both fail

    Args:
        llm_entity: Entity name from LLM extraction
        url: Source URL

    Returns:
        Best available entity name
    """
    # PRIORITY 1: Trust LLM extraction if it's valid
    if llm_entity and llm_entity.lower() not in ["unknown", "n/a", "none", ""]:
        # Check for acronym-only names (e.g. "TCEQ", "IRS") - these are usually incomplete
        if llm_entity.isupper() and len(llm_entity) <= 5 and not any(c.isdigit() for c in llm_entity):
            logger.warning(f"Acronym-only entity from LLM: {llm_entity}. Checking domain mapping...")
            # Try domain mapping for acronyms
            domain_entity = get_entity_from_domain(url)
            if domain_entity:
                logger.info(f"Expanded acronym via domain: {llm_entity} → {domain_entity}")
                return domain_entity
        
        # LLM gave us a good entity name, use it
        logger.info(f"Using LLM-extracted entity: {llm_entity}")
        return llm_entity
    
    # PRIORITY 2: Fall back to domain mapping if LLM failed
    domain_entity = get_entity_from_domain(url)
    if domain_entity:
        logger.info(f"Using domain-mapped entity: {domain_entity} (LLM extraction failed)")
        return domain_entity

    # Both failed
    return None


# =============================================================================
# STRICT LLM PROMPT
# =============================================================================

STRICT_PROMPT = """You are a STRICT data extraction expert for form documents.

# YOUR TASK
Extract TWO pieces of information from this PDF form:
1. Form Name (pretty, normalized title)
2. Entity Name (full legal organization name)

# INPUT
- URL: {url}
- First Page Text: {fp}

# OUTPUT FORMAT (STRICT)
You MUST output EXACTLY TWO LINES with NO additional text:

Line 1: Form Name
Line 2: Entity Name

NO explanations. NO analysis. NO markdown. JUST the two lines.

# RULE #1: FORM NAME NORMALIZATION (STRICT)

The Form Name must be PRETTY and CLEAN:

✅ DO:
- Extract the main form title from the document header/title area
- Use clean, professional language
- Keep it concise (≤ 75 characters)
- Use Title Case
- Include version numbers if visible in title (e.g., "Form 10.12")

❌ DO NOT:
- Include file extensions (.pdf, .docx)
- Include underscores or filename artifacts
- Include form codes from filenames (P-1250, FM-AO)
- Include words like "Packet", "Interactive", "Fillable"
- Include organization abbreviations from filename (e.g., "BECU_")
- Extract field labels (text with colons ":")
- Extract addresses or contact info

EXAMPLES:
Filename: "BECU_Genome_10.12.pdf" → Title: "Genome 10.12"
Filename: "P-1250_Member_Application_v2.pdf" → Title: "Member Application"
Filename: "FM-AO-Account-Options.pdf" → Title: "Account Options"
Document Header: "MEMBER CHANGE OF ADDRESS" → Title: "Member Change of Address"

# RULE #2: LEGAL ENTITY NAME (STRICT)

The Entity Name must be the FULL LEGAL NAME of the organization:

✅ DO:
- Look in document header (usually top of page)
- Look in document footer (usually bottom of page)
- Extract the COMPLETE legal name
- Use proper capitalization

❌ DO NOT:
- Use abbreviations or acronyms (NO "BECU", must be "Boeing Employees Credit Union")
- Use shortened names
- Use "Unknown" unless truly no organization name visible

EXAMPLES:
✅ "Boeing Employees Credit Union" (NOT "BECU")
✅ "Charles Schwab Corporation" (NOT "Schwab")
✅ "U.S. Office of Personnel Management" (NOT "OPM")
✅ "Alaska Air Group Credit Union" (NOT "AAGCU")

# RULE #3: CONSISTENCY (CRITICAL)

Forms from the SAME domain must have the SAME entity name EVERY TIME.

Example: If URL is from becu.org:
- Entity name MUST be "Boeing Employees Credit Union"
- NEVER "BECU"
- NEVER "B.E.C.U."
- NEVER "Boeing Employees CU"
- ALWAYS the exact same full legal name

# OUTPUT EXAMPLES (STRICT FORMAT)

Example 1:
Member Application
Boeing Employees Credit Union

Example 2:
SF-2826 Federal Employees Retirement
U.S. Office of Personnel Management

Example 3:
Account Opening Authorization
Charles Schwab Corporation

# VALIDATION

Before responding, verify:
□ Line 1 is a clean, pretty form name (no filename artifacts)
□ Line 2 is the full legal entity name (no abbreviations)
□ NO additional text or explanations
□ NO markdown formatting
□ Exactly 2 lines

NOW EXTRACT THE DATA:
"""


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def make_title(url: str, first_page_text: str, complexity: int = 0) -> tuple[str, str]:
    """
    Extract form title and entity name with STRICT enforcement of three rules:

    1. Form Name Normalization: Pretty, clean titles
    2. Legal Entity Names: Full legal names from document
    3. Entity Consistency: Same domain = same entity ALWAYS

    Uses two-tier LLM routing:
    - Tier 1: Gemini 2.5 Flash (fast, cheap)
    - Tier 2: Claude 3 Opus (fallback for edge cases)

    Args:
        url: PDF URL for context and domain-based entity lookup
        first_page_text: Extracted text from first page
        complexity: Form complexity score (high complexity → Tier 2)

    Returns:
        (title, entity_name) tuple
        Returns ("", None) if extraction fails
    """
    # Truncate text for LLM
    txt = (first_page_text or "").strip()
    if len(txt) > 1500:
        txt = txt[:1500]

    # Check if we have domain-based entity (for logging)
    has_domain_entity = is_domain_known(url)
    if has_domain_entity:
        logger.info(f"Domain recognized: {url} - will enforce entity consistency")

    # Build LLM messages
    messages = [
        {
            "role": "system",
            "content": "You are a STRICT data extraction expert. Follow rules EXACTLY. Output ONLY two lines: Form Name, Entity Name. NO explanations."
        },
        {
            "role": "user",
            "content": STRICT_PROMPT.format(url=url or "", fp=txt or "")
        }
    ]

    # Build context for two-tier routing
    context = {
        "task_type": "title_generation",
        "complexity": complexity,
        "text_length": len(txt)
    }

    # Force Tier 2: ALWAYS USE CLAUDE (Gemini unreliable)
    force_tier2 = True  # Was: complexity > 85 or len(txt) < 50

    try:
        # Call LLM with two-tier routing
        response = chat_complete(
            messages=messages,
            max_tokens=150,
            temperature=0.0,  # STRICT: no creativity
            timeout=25.0,
            context=context,
            force_tier2=force_tier2
        )

        if not response or not response.strip():
            logger.warning("LLM returned empty response")
            return "", None

        # Parse response (should be exactly 2 lines)
        lines = [line.strip() for line in response.strip().split('\n') if line.strip()]

        # Filter out explanation lines (in case LLM ignored instructions)
        skip_patterns = [
            'based on', 'explanation', 'analysis', 'note:', 'reasoning',
            'here is', 'here are', 'i will', 'i have', 'the title',
            'the entity', 'according to', 'extracted', 'found'
        ]

        filtered_lines = []
        for line in lines:
            line_lower = line.lower()
            # Skip explanation lines
            if any(pattern in line_lower for pattern in skip_patterns):
                continue
            # Skip markdown
            if line.startswith('#') or line.startswith('**'):
                continue
            # Skip separators
            if line.startswith('-' * 3) or line.startswith('=' * 3):
                continue
            filtered_lines.append(line)
            if len(filtered_lines) >= 2:
                break

        if len(filtered_lines) < 2:
            logger.warning(f"LLM returned insufficient lines: {filtered_lines}")
            return "", None

        raw_title = filtered_lines[0]
        llm_entity = filtered_lines[1]

        # =====================================================================
        # RULE #1: FORM NAME NORMALIZATION
        # =====================================================================
        title = normalize_form_title(raw_title, url)

        # Additional cleanup
        title = title.strip('"\'`*_')
        title = ' '.join(title.split())

        if not title:
            logger.warning(f"Title normalization resulted in empty title from: {raw_title}")
            title = raw_title  # Fall back to raw

        # =====================================================================
        # RULE #2 & #3: LEGAL ENTITY NAME + CONSISTENCY
        # =====================================================================
        entity_name = enforce_entity_consistency(llm_entity, url)

        # Clean up entity name
        if entity_name:
            entity_name = entity_name.strip('"\'`*_')
            entity_name = ' '.join(entity_name.split())

        # Log results
        logger.info(f"✓ Title: '{title}' | Entity: '{entity_name}' | Domain: {has_domain_entity}")

        return title, entity_name

    except LLMError as e:
        logger.error(f"LLM title generation failed: {str(e)}")

        # Send SMS alert (throttled)
        alert_message = f"LLM title generation failed\n\nURL: {url[:50]}\nError: {str(e)[:100]}"
        send_llm_failure_alert(alert_message)

        return "", None

    except Exception as e:
        logger.error(f"Unexpected error in title generation: {str(e)}")
        return "", None


# =============================================================================
# VALIDATION FUNCTION
# =============================================================================

def validate_extraction(title: str, entity: str, url: str) -> dict:
    """
    Validate extraction results against strict rules.

    Args:
        title: Extracted form title
        entity: Extracted entity name
        url: Source URL

    Returns:
        Validation report dict with warnings and errors
    """
    report = {
        "valid": True,
        "warnings": [],
        "errors": []
    }

    # Validate title
    if not title:
        report["errors"].append("Title is empty")
        report["valid"] = False

    if title and any(char in title for char in ['_', '.pdf', '.docx']):
        report["warnings"].append(f"Title contains filename artifacts: {title}")

    if title and len(title) > 80:
        report["warnings"].append(f"Title too long ({len(title)} chars): {title}")

    # Validate entity
    if not entity:
        report["errors"].append("Entity is empty")
        report["valid"] = False

    # Check domain consistency
    domain_entity = get_entity_from_domain(url)
    if domain_entity and entity != domain_entity:
        report["errors"].append(
            f"Entity inconsistency: Expected '{domain_entity}' (domain), got '{entity}' (LLM)"
        )
        report["valid"] = False

    return report
