# app/services/domain_mappings.py
# Strict domain-to-entity mappings for consistency
# Rule: Same domain = Same entity name ALWAYS

import re
from typing import Optional
from urllib.parse import urlparse

# ============================================================================
# STRICT DOMAIN-TO-ENTITY MAPPINGS
# ============================================================================
# This ensures every form from the same domain gets the EXACT same entity name
# Key = domain (lowercase), Value = official legal entity name

DOMAIN_TO_ENTITY = {
    # Credit Unions
    "becu.org": "Boeing Employees Credit Union",
    "aagcu.org": "Alaska Air Group Credit Union",
    "gtfcu.org": "Greater Texas Federal Credit Union",
    "texasbaycu.org": "Texas Bay Area Credit Union",
    "navyfederal.org": "Navy Federal Credit Union",
    "penfed.org": "Pentagon Federal Credit Union",
    "alliantcreditunion.org": "Alliant Credit Union",
    "schemas.org": "SchoolsFirst Federal Credit Union",
    "securityservicefcu.org": "Security Service Federal Credit Union",
    "communityadvantage.com": "Community Advantage",

    # Banks & Financial Institutions
    "schwab.com": "Charles Schwab Corporation",
    "fidelity.com": "Fidelity Investments",
    "chase.com": "JPMorgan Chase Bank",
    "wellsfargo.com": "Wells Fargo Bank",
    "bankofamerica.com": "Bank of America",
    "citi.com": "Citibank",
    "usbank.com": "U.S. Bank",
    "pnc.com": "PNC Bank",
    "capitalone.com": "Capital One",
    "tdbank.com": "TD Bank",
    "ally.com": "Ally Bank",
    "citizensbank.com": "Citizens Bank",
    "regionsbank.com": "Regions Bank",
    "suntrust.com": "Truist Bank",
    "bbt.com": "Truist Bank",
    "keybank.com": "KeyBank",
    "fifththird.com": "Fifth Third Bank",
    "huntington.com": "Huntington Bank",

    # Investment & Brokerage
    "vanguard.com": "The Vanguard Group",
    "troweprice.com": "T. Rowe Price",
    "blackrock.com": "BlackRock",
    "franklintempleton.com": "Franklin Templeton",
    "invesco.com": "Invesco",
    "statestreet.com": "State Street Corporation",
    "morganstanley.com": "Morgan Stanley",
    "goldmansachs.com": "Goldman Sachs",
    "ml.com": "Bank of America Merrill Lynch",
    "ameritrade.com": "Charles Schwab (TD Ameritrade)",
    "etrade.com": "Morgan Stanley E*TRADE",
    "robinhood.com": "Robinhood Markets",

    # U.S. Government Agencies
    "opm.gov": "U.S. Office of Personnel Management",
    "irs.gov": "Internal Revenue Service",
    "gsa.gov": "U.S. General Services Administration",
    "fda.gov": "U.S. Food and Drug Administration",
    "dod.gov": "U.S. Department of Defense",
    "va.gov": "U.S. Department of Veterans Affairs",
    "ssa.gov": "U.S. Social Security Administration",
    "epa.gov": "U.S. Environmental Protection Agency",
    "usda.gov": "U.S. Department of Agriculture",
    "hhs.gov": "U.S. Department of Health and Human Services",
    "cms.gov": "Centers for Medicare & Medicaid Services",
    "treasury.gov": "U.S. Department of the Treasury",
    "justice.gov": "U.S. Department of Justice",
    "state.gov": "U.S. Department of State",
    "ed.gov": "U.S. Department of Education",
    "dhs.gov": "U.S. Department of Homeland Security",
    "dot.gov": "U.S. Department of Transportation",
    "energy.gov": "U.S. Department of Energy",
    "labor.gov": "U.S. Department of Labor",
    "hud.gov": "U.S. Department of Housing and Urban Development",

    # State Government (examples)
    "ca.gov": "State of California",
    "ny.gov": "State of New York",
    "texas.gov": "State of Texas",
    "florida.gov": "State of Florida",
    "illinois.gov": "State of Illinois",
    "illinoiscourts.gov": "Judicial Branch of Illinois",
    "azdor.gov": "Arizona Department of Revenue",
    "pennsylvania.gov": "Commonwealth of Pennsylvania",
    "ohio.gov": "State of Ohio",
    "georgia.gov": "State of Georgia",
    "michigan.gov": "State of Michigan",
    "virginia.gov": "Commonwealth of Virginia",

    # Insurance Companies
    "metlife.com": "MetLife",
    "prudential.com": "Prudential Financial",
    "aig.com": "American International Group",
    "allstate.com": "Allstate Insurance Company",
    "statefarm.com": "State Farm Insurance",
    "geico.com": "GEICO",
    "progressive.com": "Progressive Insurance",
    "nationwide.com": "Nationwide Insurance",
    "usaa.com": "USAA",
    "libertymutual.com": "Liberty Mutual Insurance",

    # Healthcare
    "unitedhealthcare.com": "UnitedHealth Group",
    "anthem.com": "Anthem Inc.",
    "cigna.com": "Cigna Corporation",
    "aetna.com": "Aetna (CVS Health)",
    "humana.com": "Humana Inc.",
    "bluecrossma.com": "Blue Cross Blue Shield of Massachusetts",
    "bcbs.com": "Blue Cross Blue Shield Association",
    "kaiser.com": "Kaiser Permanente",

    # Utilities & Services
    "pge.com": "Pacific Gas and Electric Company",
    "sce.com": "Southern California Edison",
    "duke-energy.com": "Duke Energy",
    "constellation.com": "Constellation Energy",
    "exeloncorp.com": "Exelon Corporation",

    # Education
    "collegeboard.org": "College Board",
    "act.org": "ACT",
    "commonapp.org": "The Common Application",
    "fafsa.gov": "Federal Student Aid",

    # Telecommunications
    "att.com": "AT&T",
    "verizon.com": "Verizon Communications",
    "t-mobile.com": "T-Mobile",
    "sprint.com": "T-Mobile (Sprint)",
    "comcast.com": "Comcast Corporation",
    "spectrum.com": "Charter Communications (Spectrum)",
    "cox.com": "Cox Communications",

    # More State Agencies (Texas Focus)
    "tceq.texas.gov": "Texas Commission on Environmental Quality",
    "glo.texas.gov": "Texas General Land Office",
    "tpwd.texas.gov": "Texas Parks and Wildlife Department",
    "dshs.texas.gov": "Texas Department of State Health Services",
    "twc.texas.gov": "Texas Workforce Commission",
    "dot.state.tx.us": "Texas Department of Transportation",
    "txdot.gov": "Texas Department of Transportation",
    "tdi.texas.gov": "Texas Department of Insurance",
    "dfps.texas.gov": "Texas Department of Family and Protective Services",
    
    # Generic state variations
    "state.tx.us": "State of Texas",
}

# ============================================================================
# DOMAIN ALIASES (for variations and subdomains)
# ============================================================================
# Maps variations to canonical domain
DOMAIN_ALIASES = {
    # Schwab variations
    "client.schwab.com": "schwab.com",
    "content.schwab.com": "schwab.com",
    "workplace.schwab.com": "schwab.com",

    # Fidelity variations
    "workplaceservices.fidelity.com": "fidelity.com",
    "nb.fidelity.com": "fidelity.com",
    "institutional.fidelity.com": "fidelity.com",

    # Government variations
    "www.opm.gov": "opm.gov",
    "apps.opm.gov": "opm.gov",
    "www.gsa.gov": "gsa.gov",
    "www.irs.gov": "irs.gov",

    # BECU variations
    "www.becu.org": "becu.org",
    "online.becu.org": "becu.org",
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def normalize_domain(url: str) -> Optional[str]:
    """
    Extract and normalize domain from URL.

    Args:
        url: Full URL or domain

    Returns:
        Normalized domain (lowercase) or None
    """
    try:
        if not url:
            return None

        # Parse URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Remove port if present
        if ":" in domain:
            domain = domain.split(":")[0]

        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return None


def get_entity_from_domain(url: str) -> Optional[str]:
    """
    Get official entity name from URL using strict domain mapping.

    This ensures STRICT CONSISTENCY: same domain = same entity name ALWAYS.

    Args:
        url: Full URL or domain

    Returns:
        Official legal entity name or None
    """
    domain = normalize_domain(url)
    if not domain:
        return None

    # Check domain aliases first
    if domain in DOMAIN_ALIASES:
        domain = DOMAIN_ALIASES[domain]

    # Check exact match
    if domain in DOMAIN_TO_ENTITY:
        return DOMAIN_TO_ENTITY[domain]

    # Check if it's a subdomain of a known domain
    # e.g., "forms.becu.org" -> "becu.org"
    parts = domain.split(".")
    if len(parts) >= 2:
        # Try last 2 parts (e.g., "becu.org")
        parent_domain = ".".join(parts[-2:])
        if parent_domain in DOMAIN_TO_ENTITY:
            return DOMAIN_TO_ENTITY[parent_domain]

    # No mapping found
    return None


def add_domain_mapping(domain: str, entity_name: str) -> None:
    """
    Add a new domain-to-entity mapping.

    Args:
        domain: Domain (e.g., "example.com")
        entity_name: Official legal entity name
    """
    domain = domain.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]

    DOMAIN_TO_ENTITY[domain] = entity_name


def get_all_known_entities() -> list[str]:
    """
    Get list of all known entity names.

    Returns:
        Sorted list of unique entity names
    """
    return sorted(set(DOMAIN_TO_ENTITY.values()))


def is_domain_known(url: str) -> bool:
    """
    Check if domain has a known entity mapping.

    Args:
        url: Full URL or domain

    Returns:
        True if domain is known, False otherwise
    """
    return get_entity_from_domain(url) is not None


# ============================================================================
# TESTING
# ============================================================================

if __name__ == "__main__":
    # Test cases
    test_urls = [
        "https://www.becu.org/forms/application.pdf",
        "https://content.schwab.com/forms/SF-2826.pdf",
        "https://www.opm.gov/forms/pdf_fill/sf50.pdf",
        "https://forms.becu.org/test.pdf",
        "https://client.schwab.com/public/file/P-1234.pdf",
    ]

    print("Domain-to-Entity Mapping Tests:")
    print("=" * 60)

    for url in test_urls:
        entity = get_entity_from_domain(url)
        domain = normalize_domain(url)
        print(f"\nURL: {url}")
        print(f"Domain: {domain}")
        print(f"Entity: {entity}")

    print("\n" + "=" * 60)
    print(f"Total known entities: {len(get_all_known_entities())}")
