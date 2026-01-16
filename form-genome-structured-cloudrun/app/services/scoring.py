# app/services/scoring.py
# Deterministic scoring engine for form complexity

from typing import Dict, Any, Optional

# Standard weights used across the platform
STANDARD_WEIGHTS = {
    "page_weight": 5.0,
    "field_weight": 1.5,
    "signature_weight": 10.0,
    "requirement_weight": 15.0  # Applied for each complex requirement (notary, payment, etc)
}

def calculate_complexity_score(data: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Calculate form complexity score based on extracted attributes.
    This score has no upper cap (can exceed 100 for extreme forms).
    
    Args:
        data: Extracted form data (pages, fields, signatures, requirements)
        weights: Optional custom weights. Defaults to STANDARD_WEIGHTS.
        
    Returns:
        Dict containing score, method name, and weights used.
    """
    calc_weights = weights or STANDARD_WEIGHTS
    method = "Standard" if weights is None else "Custom"
    
    # 1. Page Score
    pages = float(data.get("pages", 1))
    page_score = pages * calc_weights.get("page_weight", 5.0)
    
    # 2. Field Score
    fields = float(data.get("total_field_count", 0))
    field_score = fields * calc_weights.get("field_weight", 1.5)
    
    # 3. Signature Score
    sigs = float(data.get("signature_count", 0))
    sig_score = sigs * calc_weights.get("signature_weight", 10.0)
    
    # 4. Requirements Point System
    req_score = 0.0
    req_weight = calc_weights.get("requirement_weight", 15.0)
    
    if data.get("notarization_required"): req_score += req_weight
    if data.get("payment_required"): req_score += req_weight
    if data.get("attachments_required"): req_score += req_weight
    if data.get("identification_required"): req_score += req_weight
    if data.get("conditional_logic"): req_score += (req_weight / 2.0)  # Logic is medium complex
    
    # Total
    final_score = int(page_score + field_score + sig_score + req_score)
    
    return {
        "score": final_score,
        "method": method,
        "weights": calc_weights
    }
