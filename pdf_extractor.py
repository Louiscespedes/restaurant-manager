"""
PDF Invoice Extractor — uses Claude API to read supplier invoice PDFs
and extract structured product line items (article, description, qty, unit, price).
Now also extracts package_weight_grams for unit normalization.
"""
import base64
import json
import os
import logging

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
CLAUDE_MODEL = 'claude-sonnet-4-20250514'


def extract_products_from_pdf(pdf_bytes, supplier_name='', invoice_number=''):
    """
    Send a PDF to Claude API and extract product line items.
    Returns a list of dicts with article_number, description, quantity, unit,
    unit_price, total, and package_weight_grams.
    """
    if not ANTHROPIC_API_KEY:
        logger.error('ANTHROPIC_API_KEY not set')
        return []

    if not pdf_bytes:
        return []

    import requests

    # Encode PDF as base64
    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

    prompt = f"""Analyze this supplier invoice PDF and extract ALL product line items.

Supplier: {supplier_name}
Invoice number: {invoice_number}

For each product line, extract:
- article_number: the supplier's article/product number (Art nummer, Artikelnr, etc.)
- description: the product name/description (Produktbeskrivning, Benamning, etc.)
- quantity: the amount ordered (Antal, Kvantitet, Levererat, etc.) — use the DELIVERED quantity if both ordered and delivered are shown
- unit: the unit of measure (kg, st, liter, forp, etc.)
- unit_price: price per unit (A-pris, Pris, etc.) — this should be the price EXCLUDING VAT
- total: line total (Summa, Belopp, etc.) — EXCLUDING VAT
- package_weight_grams: if the product is sold per unit (st, burk, forp, pase, ask, etc.) and the description shows a weight (e.g. "100g", "500g", "1kg", "200ml"), extract that weight in GRAMS. If sold by kg or liter already, set to null. If no weight info visible, set to null. Convert: 1kg=1000g, 1l=1000ml, use ml as grams approx.

Important:
- Use decimal points (not commas) for numbers: 0.493 not 0,493
- If a line is a discount, credit, or fee (not a product), skip it
- If quantity is 0 or missing, try to calculate it from total / unit_price
- Skip summary lines, VAT lines, freight/shipping lines, and rounding lines
- Include ALL product lines, even if they span multiple pages

Return ONLY a valid JSON array. No markdown, no explanation, just the JSON array.
Example: [{{"article_number": "16004", "description": "Tonfiskbuk", "quantity": 0.493, "unit": "kg", "unit_price": 964.0, "total": 475.25, "package_weight_grams": null}}]

If you cannot extract any products, return an empty array: []"""

    try:
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': CLAUDE_MODEL,
                'max_tokens': 4096,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'document',
                            'source': {
                                'type': 'base64',
                                'media_type': 'application/pdf',
                                'data': pdf_b64
                            }
                        },
                        {
                            'type': 'text',
                            'text': prompt
                        }
                    ]
                }]
            },
            timeout=60
        )
        response.raise_for_status()
        result = response.json()

        # Extract text from Claude's response
        text = result['content'][0]['text'].strip()

        # Clean up response — remove markdown code fences if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1]  # Remove first line
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        products = json.loads(text)

        if not isinstance(products, list):
            logger.warning(f'Claude returned non-list for invoice {invoice_number}: {type(products)}')
            return []

        logger.info(f'Extracted {len(products)} products from invoice {invoice_number} ({supplier_name})')
        return products

    except json.JSONDecodeError as e:
        logger.error(f'JSON parse error for invoice {invoice_number}: {e}')
        logger.error(f'Raw response: {text[:500]}')
        return []
    except Exception as e:
        logger.error(f'Claude API error for invoice {invoice_number}: {e}')
        return []
"""
PDF Invoice Extractor — uses Claude API to read supplier invoice PDFs
and extract structured product line items (article, description, qty, unit, price).
"""
import base64
import json
import os
import logging

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
CLAUDE_MODEL = 'claude-sonnet-4-20250514'


def extract_products_from_pdf(pdf_bytes, supplier_name='', invoice_number=''):
    """
    Send a PDF to Claude API and extract product line items.
    Returns a list of dicts with article_number, description, quantity, unit, unit_price, total.
    """
    if not ANTHROPIC_API_KEY:
        logger.error('ANTHROPIC_API_KEY not set')
        return []

    if not pdf_bytes:
        return []

    import requests

    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')

    prompt = f"""Analyze this supplier invoice PDF and extract ALL product line items.

Supplier: {supplier_name}
Invoice number: {invoice_number}

For each product line, extract:
- article_number: the supplier's article/product number (Art nummer, Artikelnr, etc.)
- description: the product name/description (Produktbeskrivning, Benämning, etc.)
- quantity: the amount ordered (Antal, Kvantitet, Levererat, etc.) — use the DELIVERED quantity if both ordered and delivered are shown
- unit: the unit of measure (kg, st, liter, förp, etc.)
- unit_price: price per unit (À-pris, Pris, etc.) — this should be the price EXCLUDING VAT
- total: line total (Summa, Belopp, etc.) — EXCLUDING VAT

Important:
- Use decimal points (not commas) for numbers: 0.493 not 0,493
- If a line is a discount, credit, or fee (not a product), skip it
- If quantity is 0 or missing, try to calculate it from total / unit_price
- Skip summary lines, VAT lines, freight/shipping lines, and rounding lines
- Include ALL product lines, even if they span multiple pages

Return ONLY a valid JSON array. No markdown, no explanation, just the JSON array.
Example: [{{"article_number": "16004", "description": "Tonfiskbuk", "quantity": 0.493, "unit": "kg", "unit_price": 964.0, "total": 475.25}}]

If you cannot extract any products, return an empty array: []"""

    try:
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': CLAUDE_MODEL,
                'max_tokens': 4096,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'document',
                            'source': {
                                'type': 'base64',
                                'media_type': 'application/pdf',
                                'data': pdf_b64
                            }
                        },
                        {
                            'type': 'text',
                            'text': prompt
                        }
                    ]
                }]
            },
            timeout=60
        )
        response.raise_for_status()
        result = response.json()

        text = result['content'][0]['text'].strip()

        if text.startswith('```'):
            text = text.split('\n', 1)[1]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        products = json.loads(text)

        if not isinstance(products, list):
            logger.warning(f'Claude returned non-list for invoice {invoice_number}')
            return []

        logger.info(f'Extracted {len(products)} products from invoice {invoice_number} ({supplier_name})')
        return products

    except json.JSONDecodeError as e:
        logger.error(f'JSON parse error for invoice {invoice_number}: {e}')
        return []
    except Exception as e:
        logger.error(f'Claude API error for invoice {invoice_number}: {e}')
        return []
