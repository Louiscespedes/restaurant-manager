"""
AI-powered inventory text parser using Claude API.
Takes messy text/voice input and returns structured inventory items.
"""
import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY
from models import Session, Product, Recipe

client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


def parse_inventory_with_ai(raw_text, db_session):
    """
    Send messy inventory text to Claude, get back structured items.
    Also pulls known products/recipes from DB for context.
    """
    if not client:
        return None  # Fall back to regex parser if no API key

    # Get known products from database for context
    products = db_session.query(Product).all()
    product_list = []
    for p in products:
        entry = f"- {p.name}"
        if p.supplier:
            entry += f" (supplier: {p.supplier.name})"
        if p.unit:
            entry += f" [{p.unit}]"
        if p.current_price:
            entry += f" {p.current_price} SEK/{p.unit or 'unit'}"
        if p.category:
            entry += f" category: {p.category}"
        product_list.append(entry)

    products_context = "\n".join(product_list) if product_list else "No products in database yet."

    # Get recipes for context
    recipes = db_session.query(Recipe).all()
    recipe_list = []
    for r in recipes:
        recipe_list.append(f"- {r.name}: yields {r.total_yield} {r.yield_unit}, cost {r.cost_per_unit} SEK/{r.yield_unit}")

    recipes_context = "\n".join(recipe_list) if recipe_list else "No recipes in database yet."

    prompt = f"""You are an AI assistant for a Swedish restaurant's inventory system.
Parse the following messy inventory notes (could be from handwriting, voice dictation, or quick typing) into structured data.

KNOWN PRODUCTS IN DATABASE:
{products_context}

KNOWN RECIPES:
{recipes_context}

CATEGORIES — you MUST assign one to every item based on what the product is:
- Meat: beef, pork, chicken, lamb, duck, wagyu, entrecôte, fläsk, kyckling, lamm, anka, nötkött, färs, korv, bacon, skinka
- Fish & Seafood: salmon, tuna, cod, shrimp, lobster, lax, torsk, räkor, hummer, skaldjur, musslor, bläckfisk, krabba
- Dairy: milk, cream, butter, cheese, yogurt, mjölk, grädde, smör, ost, crème fraiche, mascarpone, parmesan
- Produce / Vegetables: carrot, onion, potato, tomato, zucchini, lettuce, morot, lök, potatis, tomat, sallad, gurka, paprika, svamp, vitlök, purjolök, selleri, broccoli, spenat, ruccola
- Fruit: banana, apple, lemon, lime, orange, berry, banan, äpple, citron, apelsin, bär, hallon, jordgubbar, mango, avocado
- Dry Goods: flour, rice, pasta, sugar, salt, mjöl, ris, socker, linser, bönor, couscous, quinoa, nötter
- Oils & Condiments: olive oil, vinegar, soy sauce, olivolja, vinäger, soja, senap, ketchup, majonnäs, rapsolja, sesam, truffle oil, balsamico, kryddor, peppar
- Beverages: wine, beer, juice, water, coffee, vin, öl, juice, vatten, kaffe, te, läsk, mineralvatten, tonic
- Frozen: anything frozen/fryst, glass (ice cream), frysta bär, frysta grönsaker
- Bakery: bread, bröd, bullar, croissant, deg, jäst
- Finished Products: sauces, stocks, ice cream, prepared foods you make in-house — sås, buljong, glass, fond, aioli
- Other: cleaning supplies, packaging, disposables, folie, diskmedel, servetter

RULES:
1. Extract each item with: name, quantity, unit (kg/g/st/liter), price_per_unit (if mentioned), category, supplier (if mentioned)
2. ALWAYS assign a category — use your knowledge of food and cooking. Every ingredient belongs somewhere. Never leave category empty or null.
3. Match items to known products when possible — use the exact product name from the database
4. Understand Swedish AND English — this is a Swedish restaurant so input may be in either language or mixed
5. Recognize trimming keywords: "rensad", "filead", "putsad", "trimmed", "skuren" — flag these with needs_trimming_review: true
6. Recognize finished/recipe products: "färdig", "hemlagad", "homemade", "prepared" — flag with needs_recipe_review: true
7. If a product name could match multiple known products, flag with needs_disambiguation: true and list the possible matches
8. If price is missing but the product exists in the database, use the database price
9. Handle messy formats: "10kg lax 450kr", "salmon 10 kilo", "3st olivolja flaskor", etc.
10. Clean up product names to be readable — "lax" becomes "Lax (Salmon)", "morötter" becomes "Morötter"

RAW INVENTORY TEXT:
{raw_text}

Return ONLY valid JSON in this exact format:
{{
  "items": [
    {{
      "name": "product name (cleaned up)",
      "raw_input": "original text for this item",
      "quantity": 10.0,
      "unit": "kg",
      "price_per_unit": 450.0,
      "category": "Fish & Seafood",
      "supplier_name": "supplier if mentioned or matched",
      "matched_product_id": null,
      "matched_product_name": "exact name from database if matched",
      "needs_disambiguation": false,
      "disambiguation_options": [],
      "needs_trimming_review": false,
      "needs_recipe_review": false,
      "notes": "any extra context"
    }}
  ]
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        # Extract JSON from response
        response_text = response.content[0].text

        # Try to parse JSON (handle case where Claude wraps in markdown code block)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        result = json.loads(response_text.strip())
        return result

    except Exception as e:
        print(f"AI parsing error: {e}")
        return None


def generate_smart_questions(items, known_products, db_session):
    """
    Use Claude to generate intelligent review questions based on parsed items.
    """
    if not client:
        return None

    items_json = json.dumps(items, ensure_ascii=False, indent=2)

    prompt = f"""You are reviewing a restaurant's inventory parsing results. Generate smart clarifying questions.

PARSED ITEMS:
{items_json}

For each item that needs review, generate a question. Question types:
1. "product_match" — when an item could match multiple products, ask which one
2. "trimming_loss" — when trimming was mentioned, ask the percentage (common: 10%, 15%, 20%, 25%, 30%)
3. "recipe_cost" — when a finished product is detected, confirm recipe costing
4. "missing_price" — when no price was found and none in database
5. "category_confirm" — when category is uncertain

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "item_index": 0,
      "question_type": "trimming_loss",
      "question_text": "You mentioned 'rensad lax'. What trimming loss percentage should I use?",
      "options": [
        {{"value": "15", "label": "15% (light trim)"}},
        {{"value": "20", "label": "20% (standard)"}},
        {{"value": "25", "label": "25% (heavy trim)"}}
      ]
    }}
  ]
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        return json.loads(response_text.strip())

    except Exception as e:
        print(f"AI question generation error: {e}")
        return None
