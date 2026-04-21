"""
Price Estimator — uses Claude to estimate wholesale prices for products
not found in invoice data. Provides approximate Swedish wholesale prices
as a fallback when no invoice history exists.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')


def estimate_product_price(product_name, unit='kg', category=None):
    """
    Estimate the wholesale price of a product in Sweden.
    Returns a dict with estimated_price, unit, confidence, and source.
    Uses Claude Haiku for fast, cheap estimation.
    """
    if not ANTHROPIC_API_KEY:
        logger.error('ANTHROPIC_API_KEY not set for price estimation')
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    category_hint = f" (category: {category})" if category else ""

    prompt = f"""You are a wholesale food pricing expert for restaurants in Sweden.
Estimate the current approximate WHOLESALE price (what a restaurant pays from a supplier like Menigo, Martin & Servera, or Sorunda) for:

Product: {product_name}{category_hint}
Unit: per {unit}

Consider:
- Swedish wholesale prices (not retail/consumer prices)
- Prices in SEK (Swedish kronor)
- Typical restaurant supplier pricing (Menigo, Martin & Servera, Sorunda level)
- Current market conditions (2025-2026)

Return ONLY a JSON object, no markdown:
{{
    "estimated_price": number (price per unit in SEK),
    "unit": "{unit}",
    "confidence": "high" or "medium" or "low",
    "reasoning": "brief explanation of estimate basis (max 20 words)",
    "price_range_low": number,
    "price_range_high": number
}}

If you truly cannot estimate (exotic/unknown product), return:
{{"estimated_price": null, "unit": "{unit}", "confidence": "none", "reasoning": "Cannot estimate price for this product"}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = response.content[0].text.strip()
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            result_text = result_text.strip()

        estimate = json.loads(result_text)
        estimate['product_name'] = product_name
        estimate['is_estimate'] = True

        logger.info(
            f"Price estimate for '{product_name}': "
            f"{estimate.get('estimated_price')} SEK/{unit} "
            f"(confidence: {estimate.get('confidence')})"
        )
        return estimate

    except Exception as e:
        logger.error(f"Price estimation failed for '{product_name}': {e}")
        return None


def estimate_prices_batch(items):
    """
    Estimate prices for multiple items that have no invoice price.
    Returns a dict mapping item description to price estimate.
    Only estimates for items where unit_price is None or 0.
    """
    estimates = {}
    for item in items:
        desc = item.get('description', '')
        price = item.get('unit_price')
        if not desc or (price and price > 0):
            continue

        unit = item.get('unit', 'kg')
        category = item.get('category')
        estimate = estimate_product_price(desc, unit=unit, category=category)
        if estimate and estimate.get('estimated_price'):
            estimates[desc] = estimate

    logger.info(f"Batch estimation: {len(estimates)} prices estimated out of {len(items)} items")
    return estimates
