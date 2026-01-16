# Strict Data Extraction Rules

This document describes the three strict rules enforced for form data extraction in the Form Genome Project.

## Overview

All form processing enforces **three critical rules** to ensure data quality, consistency, and normalization:

1. **Form Name Normalization**: Clean, pretty titles
2. **Legal Entity Names**: Full legal names from documents
3. **Strict Entity Consistency**: Same domain = same entity ALWAYS

## Rule #1: Form Name Normalization

### Purpose
Convert raw filenames and form titles into clean, professional, "pretty" names suitable for display in a forms catalog.

### Requirements

**✅ DO:**
- Extract the main form title from the document header/title area
- Use clean, professional language
- Keep concise (≤ 75 characters)
- Use Title Case
- Include version numbers if visible in title (e.g., "Form 10.12")

**❌ DO NOT:**
- Include file extensions (.pdf, .docx)
- Include underscores or filename artifacts
- Include form codes from filenames (P-1250, FM-AO)
- Include words like "Packet", "Interactive", "Fillable"
- Include organization abbreviations from filename (e.g., "BECU_")
- Extract field labels (text with colons ":")
- Extract addresses or contact info

### Examples

| Input | Output |
|-------|--------|
| `BECU_Genome_10.12.pdf` | `Genome 10.12` |
| `P-1250_Member_Application_v2.pdf` | `Member Application` |
| `FM-AO-Account-Options.pdf` | `Account Options` |
| `Interactive_Fillable_Final_v3.pdf` | *(empty - removed all artifacts)* |
| Document Header: "MEMBER CHANGE OF ADDRESS" | `Member Change of Address` |

### Implementation

The `normalize_form_title()` function in `app/services/title_llm.py` handles:

1. **File Extension Removal**: `.pdf`, `.docx`, etc.
2. **Organization Prefix Removal**: Domain-based (e.g., `BECU_` from becu.org)
3. **Underscore/Hyphen Conversion**: Replace with spaces
4. **Version Number Removal**: `v1`, `v2`, `v3.2`, etc.
5. **Date Removal**: Various formats (YYYY-MM-DD, MM/DD/YY, etc.)
6. **Artifact Removal**: `packet`, `interactive`, `fillable`, `final`, etc.
7. **Form Code Removal**: `P-1250`, `FM-AO`, `SCU-4098`
8. **Title Case Conversion**: Preserve acronyms (e.g., "IRS", "TSP")
9. **Length Limiting**: Max 75 characters

---

## Rule #2: Legal Entity Names

### Purpose
Extract the **full legal name** of the organization that owns the form, not abbreviations or acronyms.

### Requirements

**✅ DO:**
- Look in document header (usually top of page)
- Look in document footer (usually bottom of page)
- Extract the COMPLETE legal name
- Use proper capitalization

**❌ DO NOT:**
- Use abbreviations or acronyms
- Use shortened names
- Use "Unknown" unless truly no organization name visible

### Examples

| ❌ Wrong | ✅ Correct |
|---------|-----------|
| BECU | Boeing Employees Credit Union |
| Schwab | Charles Schwab Corporation |
| OPM | U.S. Office of Personnel Management |
| AAGCU | Alaska Air Group Credit Union |
| Navy Fed | Navy Federal Credit Union |

### Implementation

The LLM is instructed via the `STRICT_PROMPT` to:
1. Search document header and footer
2. Extract the full legal name
3. Never use abbreviations
4. Never use shortened forms

---

## Rule #3: Strict Entity Consistency (CRITICAL)

### Purpose
**Ensure that every form from the same domain returns the EXACT same entity name every time**, regardless of how the organization appears in the document.

### The Problem

Without consistency enforcement:
- Form 1 from becu.org: "BECU" ❌
- Form 2 from becu.org: "Boeing Employees CU" ❌
- Form 3 from becu.org: "B.E.C.U." ❌
- Form 4 from becu.org: "Boeing Employees Credit Union" ✅

**This causes data quality issues and makes aggregation impossible.**

### The Solution: Domain-Based Entity Mapping

We maintain a **strict domain-to-entity mapping** in `app/services/domain_mappings.py`:

```python
DOMAIN_TO_ENTITY = {
    "becu.org": "Boeing Employees Credit Union",
    "schwab.com": "Charles Schwab Corporation",
    "opm.gov": "U.S. Office of Personnel Management",
    # ... 100+ mappings
}
```

### How It Works

1. **Extract domain** from URL (e.g., `https://www.becu.org/forms/app.pdf` → `becu.org`)
2. **Look up canonical entity** in domain mapping
3. **Override LLM output** if domain is known
4. **Use LLM output** only if domain is not in mapping (with warning)

### Example: Entity Consistency Enforcement

```python
# Input
url = "https://www.becu.org/forms/member-application.pdf"
llm_extracted_entity = "BECU"  # LLM found abbreviation in document

# Domain lookup
domain_entity = get_entity_from_domain(url)  # Returns "Boeing Employees Credit Union"

# Result
final_entity = domain_entity  # "Boeing Employees Credit Union" (OVERRIDES LLM)
```

### Consistency Guarantee

✅ **All forms from becu.org will ALWAYS return:**
```
Entity: "Boeing Employees Credit Union"
```

❌ **Never:**
- "BECU"
- "B.E.C.U."
- "Boeing Employees CU"
- "Boeing Employees' Credit Union"

### Implementation

The `enforce_entity_consistency()` function in `app/services/title_llm.py`:

1. Calls `get_entity_from_domain(url)` to check for known domain
2. If domain is known: **Returns canonical entity (ignores LLM)**
3. If domain is unknown: **Returns LLM entity with warning**

This ensures **100% consistency** for all known domains.

---

## Testing & Validation

### Automated Validation

The `validate_extraction()` function checks:

1. ✅ Title is not empty
2. ✅ Title does not contain filename artifacts (`_`, `.pdf`, `.docx`)
3. ✅ Title is not too long (≤ 80 characters)
4. ✅ Entity is not empty
5. ✅ Entity matches domain mapping (if domain is known)

### Test Suite

Run the test suite:

```bash
cd "/Users/patrickmeyer/My Drive (patrick.meyer@docusign.com)/The Form Genome Project/form-genome-structured-cloudrun"

python3 << 'EOF'
from dotenv import load_dotenv
load_dotenv()

from app.services.title_llm import make_title, validate_extraction

# Test BECU forms
test_urls = [
    'https://www.becu.org/forms/member-application.pdf',
    'https://www.becu.org/forms/account-opening.pdf',
    'https://www.becu.org/forms/loan-application.pdf',
]

for url in test_urls:
    title, entity = make_title(url, "BECU Member Services", complexity=50)
    validation = validate_extraction(title, entity, url)

    print(f"URL: {url}")
    print(f"Entity: {entity}")
    print(f"Valid: {validation['valid']}")

    assert entity == "Boeing Employees Credit Union", "Entity consistency violated!"
    print("✅ PASS\n")

print("All tests passed!")
EOF
```

---

## Domain Mappings

### Current Coverage

We have 100+ domain mappings covering:

- **Credit Unions** (BECU, Navy Federal, PenFed, etc.)
- **Banks** (Chase, Wells Fargo, Bank of America, etc.)
- **Investment Firms** (Schwab, Fidelity, Vanguard, etc.)
- **U.S. Government** (OPM, IRS, GSA, FDA, VA, etc.)
- **State Government** (California, New York, Texas, etc.)
- **Insurance** (MetLife, Prudential, USAA, etc.)
- **Healthcare** (UnitedHealth, Cigna, Kaiser, etc.)

### Adding New Domains

To add a new domain mapping:

1. Open `app/services/domain_mappings.py`
2. Add to `DOMAIN_TO_ENTITY` dict:
   ```python
   "neworg.com": "New Organization Full Legal Name"
   ```
3. Add to `DOMAIN_ALIASES` if needed (for subdomains):
   ```python
   "forms.neworg.com": "neworg.com"
   ```

### Viewing All Mappings

```python
from app.services.domain_mappings import get_all_known_entities

entities = get_all_known_entities()
print(f"Total known entities: {len(entities)}")
for entity in entities:
    print(f"  - {entity}")
```

---

## Usage Examples

### Basic Usage

```python
from app.services.title_llm import make_title

url = "https://www.becu.org/forms/BECU_Member_Application_v2.pdf"
first_page_text = """
    MEMBER APPLICATION FORM

    BECU

    Full Name: _______________
    Address: _________________
"""

title, entity = make_title(url, first_page_text, complexity=50)

print(f"Title: {title}")    # "Member Application" (normalized)
print(f"Entity: {entity}")  # "Boeing Employees Credit Union" (domain-based)
```

### With Validation

```python
from app.services.title_llm import make_title, validate_extraction

title, entity = make_title(url, first_page_text)

validation = validate_extraction(title, entity, url)

if validation['valid']:
    print("✅ Extraction passed all validation rules")
else:
    print("❌ Validation failed:")
    for error in validation['errors']:
        print(f"  - {error}")

if validation['warnings']:
    print("⚠️  Warnings:")
    for warning in validation['warnings']:
        print(f"  - {warning}")
```

### Checking Domain Coverage

```python
from app.services.domain_mappings import is_domain_known, get_entity_from_domain

url = "https://www.becu.org/forms/application.pdf"

if is_domain_known(url):
    entity = get_entity_from_domain(url)
    print(f"✅ Domain is known: {entity}")
else:
    print("⚠️  Unknown domain - will use LLM extraction")
```

---

## Benefits

### 1. Data Quality
- **No filename artifacts** in form titles
- **Consistent entity names** across all forms
- **Professional presentation** in user interfaces

### 2. Aggregation & Analytics
- Group all forms by entity reliably
- Count forms per organization accurately
- Generate reports without manual cleanup

### 3. User Experience
- Clean, readable form titles
- Consistent organization names
- No confusion from abbreviations

### 4. Scalability
- New domains easily added to mapping
- Automatic enforcement without manual review
- 100% consistency guaranteed

---

## Troubleshooting

### Issue: Entity Not Consistent

**Symptom:** Forms from same domain return different entity names

**Cause:** Domain not in mapping

**Solution:**
1. Check if domain is in `DOMAIN_TO_ENTITY` in `domain_mappings.py`
2. Add domain mapping if missing
3. Test with `get_entity_from_domain(url)`

### Issue: Title Contains Filename Artifacts

**Symptom:** Title has underscores, `.pdf`, or version numbers

**Cause:** Normalization function not catching all patterns

**Solution:**
1. Check `normalize_form_title()` in `title_llm.py`
2. Add new pattern to removal rules
3. Test with example filename

### Issue: LLM Returns Wrong Entity

**Symptom:** Entity doesn't match document

**Cause:** LLM extraction error

**Solution:**
- **If domain is known:** Entity will be overridden by domain mapping (automatic)
- **If domain is unknown:** Add domain to mapping to prevent future errors

---

## Summary

The three strict extraction rules ensure:

1. ✅ **Clean form titles** without filename artifacts
2. ✅ **Full legal entity names** from documents
3. ✅ **100% consistency** for forms from the same domain

These rules are **automatically enforced** by:
- `normalize_form_title()` - Rule #1
- LLM prompt instructions - Rule #2
- `enforce_entity_consistency()` - Rule #3

No manual intervention required. Just call `make_title()` and the rules are applied.

---

## See Also

- [LLM_ROUTING_ARCHITECTURE.md](./LLM_ROUTING_ARCHITECTURE.md) - Two-tier routing details
- [README.md](./README.md) - General usage and setup
- `app/services/domain_mappings.py` - Domain-to-entity mappings
- `app/services/title_llm.py` - Extraction implementation
