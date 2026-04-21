"""
Recipe, Dish, and Menu API routes.
Register with: app.register_blueprint(recipe_bp)
"""
import logging
import uuid
import time
import threading
from flask import Blueprint, request, jsonify
from datetime import datetime
from sqlalchemy import func, or_
from food_dictionary import search_food_terms
from models import (
    Session, Recipe, RecipeIngredient, Dish, DishComponent,
    Menu, MenuItem, Product, InvoiceLineItem, PriceHistory
)

logger = logging.getLogger(__name__)

recipe_bp = Blueprint('recipes', __name__)

# ─── Recipe Review Session Storage (in-memory) ─────────────────────────────
_recipe_review_sessions = {}
_recipe_sessions_lock = threading.Lock()
RECIPE_SESSION_TTL = 7200  # 2 hours

def _cleanup_recipe_sessions():
    now = time.time()
    with _recipe_sessions_lock:
        expired = [sid for sid, s in _recipe_review_sessions.items()
                   if now - s["created_at"] > RECIPE_SESSION_TTL]
        for sid in expired:
            del _recipe_review_sessions[sid]

def _create_recipe_review_session(parsed_data, ingredients):
    session_id = str(uuid.uuid4())[:8]
    questions = []
    for idx, ing in enumerate(ingredients):
        if ing.get("needs_clarification") and ing.get("clarification_question"):
            q_type = ing.get("clarification_type", "product_match")
            options = ing.get("clarification_options") or []
            if "Skip" not in options:
                options.append("Skip")
            item_name = ing.get("description") or "Unknown"
            question_text = ing["clarification_question"]
            if item_name.lower() not in question_text.lower():
                question_text = f"[{item_name}] {question_text}"
            questions.append({
                "id": len(questions) + 1,
                "item_index": idx,
                "type": q_type,
                "item_description": item_name,
                "question": question_text,
                "options": options,
                "answer": None,
                "is_answered": False
            })
    with _recipe_sessions_lock:
        _recipe_review_sessions[session_id] = {
            "id": session_id,
            "parsed": parsed_data,
            "ingredients": ingredients,
            "questions": questions,
            "status": "reviewing" if questions else "ready",
            "created_at": time.time()
        }
    logger.info(f"Recipe review session {session_id}: {len(ingredients)} ingredients, {len(questions)} questions")
    return session_id

def _get_recipe_session(session_id):
    _cleanup_recipe_sessions()
    with _recipe_sessions_lock:
        return _recipe_review_sessions.get(session_id)

def _apply_recipe_answer(session, question_id, answer):
    question = None
    for q in session["questions"]:
        if q["id"] == question_id:
            question = q
            break
    if not question:
        return False, "Question not found"
    question["answer"] = answer
    question["is_answered"] = True
    item_idx = question["item_index"]
    ing = session["ingredients"][item_idx]
    q_type = question["type"]
    if answer.lower() == "skip":
        ing["needs_clarification"] = False
        return True, "Skipped"
    if q_type in ("product_match", "cut_variant"):
        clean_name = answer.split(" - ")[0].split(" (")[0].strip()
        ing["matched_product_name"] = clean_name
        ing["needs_clarification"] = False
        # Re-lookup product to get price
        db = Session()
        try:
            product = db.query(Product).filter(
                func.lower(Product.name) == clean_name.lower().strip()
            ).first()
            if not product:
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{clean_name[:30]}%")
                ).first()
            if product:
                ing["product_id"] = product.id
                raw_price = _adjust_price_for_package_size(product.name, product.current_price or 0)
                ing_unit = ing.get("unit", "kg")
                ing["unit_price"] = round(_convert_price_to_ingredient_unit(raw_price, ing_unit), 4)
                ing["raw_price_per_base_unit"] = raw_price
                ing["supplier_name"] = product.supplier.name if product.supplier else None
        finally:
            db.close()
    elif q_type == "supplier_choice":
        ing["supplier_name"] = answer
        ing["needs_clarification"] = False
        # Re-lookup with supplier
        db = Session()
        try:
            from models import Supplier
            supplier = db.query(Supplier).filter(Supplier.name.ilike(f"%{answer[:30]}%")).first()
            if supplier:
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{ing.get('matched_product_name', '')[:30]}%"),
                    Product.supplier_id == supplier.id
                ).first()
                if product:
                    ing["product_id"] = product.id
                    raw_price = _adjust_price_for_package_size(product.name, product.current_price or 0)
                    ing_unit = ing.get("unit", "kg")
                    ing["unit_price"] = round(_convert_price_to_ingredient_unit(raw_price, ing_unit), 4)
                    ing["raw_price_per_base_unit"] = raw_price
        finally:
            db.close()
    elif q_type == "no_invoice_match":
        # AI recognized the ingredient but it's not in invoices
        if answer.startswith("Yes, use") or answer.startswith("Yes"):
            ing["needs_clarification"] = False
            ing["matched_product_confidence"] = "none"
        elif answer.strip().lower() == "skip":
            ing["needs_clarification"] = False
        else:
            custom_name = answer.strip()
            if custom_name and custom_name.lower() != "enter custom name":
                ing["description"] = custom_name
            ing["needs_clarification"] = False
            # Try to find in DB
            db = Session()
            try:
                search_name = custom_name if custom_name and custom_name.lower() != "enter custom name" else ing.get("description", "")
                matches = db.query(Product).filter(
                    Product.name.ilike(f"%{search_name[:30]}%")
                ).limit(5).all()
                if len(matches) == 1:
                    product = matches[0]
                    ing["matched_product_name"] = product.name
                    ing["product_id"] = product.id
                    if product.current_price:
                        ing["unit_price"] = product.current_price
                    if product.supplier:
                        ing["supplier_name"] = product.supplier.name
                    ing["matched_product_confidence"] = "high"
                elif len(matches) > 1:
                    options = []
                    for p in matches:
                        price_str = f"{p.current_price:.2f} SEK" if p.current_price else "no price"
                        supplier_str = p.supplier.name if p.supplier else "unknown"
                        options.append(f"{p.name} - {price_str} ({supplier_str})")
                    options.append("Skip")
                    new_q = {
                        "id": max(q["id"] for q in session["questions"]) + 1,
                        "item_index": item_idx,
                        "type": "product_match",
                        "item_description": search_name,
                        "question": f"Found {len(matches)} products matching '{search_name}'. Which one?",
                        "options": options,
                        "answer": None,
                        "is_answered": False
                    }
                    current_pos = session["questions"].index(question)
                    session["questions"].insert(current_pos + 1, new_q)
            finally:
                db.close()

    elif q_type == "unknown_product":
        user_product_name = answer.strip()
        ing["description"] = user_product_name
        ing["needs_clarification"] = False
        db = Session()
        try:
            # Step 1: Direct ILIKE search
            matches = db.query(Product).filter(
                Product.name.ilike(f"%{user_product_name[:30]}%")
            ).limit(10).all()
            # Step 2: If no direct match, try cross-language AI translation
            if not matches:
                try:
                    import anthropic as anth_unknown
                    client_unknown = anth_unknown.Anthropic()
                    translate_resp = client_unknown.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=200,
                        messages=[{"role": "user", "content": f"""Translate this food/ingredient term to Swedish, English, and French. Return ONLY a JSON array of search terms (lowercase, no explanations). Example: ["potatis", "potato", "pomme de terre"]\n\nTerm: {user_product_name}"""}]
                    )
                    import json as json_unknown
                    terms_text = translate_resp.content[0].text.strip()
                    if terms_text.startswith("["):
                        search_terms = json_unknown.loads(terms_text)
                    else:
                        search_terms = [user_product_name]
                    from sqlalchemy import or_ as or_unknown
                    conditions = [Product.name.ilike(f"%{term}%") for term in search_terms]
                    matches = db.query(Product).filter(
                        or_unknown(*conditions)
                    ).limit(10).all()
                except Exception as e:
                    logger.error(f"Cross-language search failed in unknown_product: {e}")
            if len(matches) == 1:
                product = matches[0]
                ing["product_id"] = product.id
                raw_price = _adjust_price_for_package_size(product.name, product.current_price or 0)
                ing_unit = ing.get("unit", "kg")
                ing["unit_price"] = round(_convert_price_to_ingredient_unit(raw_price, ing_unit), 4)
                ing["raw_price_per_base_unit"] = raw_price
                ing["supplier_name"] = product.supplier.name if product.supplier else None
            elif len(matches) > 1:
                options = []
                for p in matches:
                    price_str = f"{p.current_price:.2f} SEK" if p.current_price else "no price"
                    supplier_str = p.supplier.name if p.supplier else "unknown"
                    options.append(f"{p.name} - {price_str} ({supplier_str})")
                options.append("Skip")
                new_q = {
                    "id": max(q["id"] for q in session["questions"]) + 1,
                    "item_index": item_idx,
                    "type": "product_match",
                    "item_description": user_product_name,
                    "question": f"Found {len(matches)} products matching '{user_product_name}'. Which one?",
                    "options": options,
                    "answer": None,
                    "is_answered": False
                }
                current_pos = session["questions"].index(question)
                session["questions"].insert(current_pos + 1, new_q)
        finally:
            db.close()
    else:
        ing["needs_clarification"] = False
    all_answered = all(q["is_answered"] for q in session["questions"])
    if all_answered:
        session["status"] = "ready"
    return True, "Answer applied"



# ─── Helper: calculate recipe cost ──────────────────────────────────────────

def _adjust_price_for_package_size(product_name, price):
    """Detect package sizes in product names and return true per-unit price.
    E.g., 'Salt fint med jod Jozo 1x10 kg DK' at 205 kr -> 20.50 kr/kg
    'Rapsolja Zeta 1x1 L' at 35.90 kr -> 35.90 kr/L (no change)
    """
    import re
    if not product_name or not price:
        return price or 0
    name_lower = product_name.lower()
    # Pattern: NxM unit (e.g., "1x10 kg", "6x1 l", "2x5 kg")
    match = re.search(r'(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(?:kg|l|liter|litre)\b', name_lower)
    if match:
        packs = int(match.group(1))
        size = float(match.group(2).replace(',', '.'))
        total_units = packs * size
        if total_units > 1:
            return round(price / total_units, 4)
        return price
    # Pattern: standalone large size like "10 kg", "5L" (but NOT "3%" or unit descriptions)
    match = re.search(r'(?:^|\s)(\d+(?:[.,]\d+)?)\s*(?:kg|l|liter|litre)\b', name_lower)
    if match:
        size = float(match.group(1).replace(',', '.'))
        # Only adjust if size > 1 (avoids "1 kg" = no adjustment)
        if size > 1:
            return round(price / size, 4)
    return price


def _convert_price_to_ingredient_unit(price_per_base, ingredient_unit, product_unit=None):
    """Convert price from invoice unit (kg/liter) to ingredient unit (g/ml/cl/etc).
    Invoice prices are always per kg or per liter. If the recipe uses g, ml, etc.,
    we need to convert so that qty * price gives the correct line cost.
    Returns price per ingredient unit."""
    if not price_per_base or not ingredient_unit:
        return price_per_base or 0
    iu = ingredient_unit.lower().strip()
    # If ingredient is in g but price is per kg -> divide by 1000
    if iu == 'g':
        return price_per_base / 1000
    # If ingredient is in ml -> price per liter / 1000
    elif iu == 'ml':
        return price_per_base / 1000
    # If ingredient is in cl -> price per liter / 100
    elif iu == 'cl':
        return price_per_base / 100
    # kg, liter, st -> price is already correct
    return price_per_base


def calc_recipe_cost(recipe, ingredients=None):
    """Calculate total cost, per-portion cost, and food cost % for a recipe."""
    if ingredients is None:
        ingredients = recipe.ingredients

    subtotal = 0
    for ing in ingredients:
        price = ing.unit_price or 0
        qty = ing.quantity or 0
        trim = ing.trimming_percent or 0
        # Prices are stored pre-converted to ingredient unit at parse/save time
        effective_price = price / (1 - trim / 100) if trim < 100 else price
        subtotal += qty * effective_price

    seasoning = subtotal * (recipe.seasoning_cost_percent or 0) / 100
    total_cost = subtotal + seasoning
    per_portion = total_cost / recipe.portions if recipe.portions and recipe.portions > 0 else total_cost
    food_cost_pct = (per_portion / recipe.selling_price * 100) if recipe.selling_price and recipe.selling_price > 0 else None

    return {
        "subtotal": round(subtotal, 2),
        "seasoning_cost": round(seasoning, 2),
        "total_cost": round(total_cost, 2),
        "per_portion": round(per_portion, 2),
        "food_cost_percent": round(food_cost_pct, 1) if food_cost_pct is not None else None
    }


def ingredient_to_dict(ing):
    """Serialize a RecipeIngredient."""
    price = ing.unit_price or 0
    qty = ing.quantity or 0
    trim = ing.trimming_percent or 0
    effective_price = price / (1 - trim / 100) if trim > 0 and trim < 100 else price

    return {
        "id": ing.id,
        "product_id": ing.product_id,
        "description": ing.description,
        "quantity": ing.quantity,
        "unit": ing.unit,
        "unit_price": ing.unit_price,
        "is_manual_price": ing.is_manual_price,
        "trimming_percent": ing.trimming_percent,
        "effective_unit_price": round(effective_price, 2),
        "line_cost": round(qty * effective_price, 2),
        "supplier_name": ing.supplier_name,
        "price_found": ing.unit_price is not None and ing.unit_price > 0
    }


def recipe_to_dict(recipe, include_ingredients=True):
    """Serialize a Recipe."""
    result = {
        "id": recipe.id,
        "name": recipe.name,
        "category": recipe.category,
        "created_by": recipe.created_by,
        "portions": recipe.portions,
        "selling_price": recipe.selling_price,
        "seasoning_cost_percent": recipe.seasoning_cost_percent,
        "notes": recipe.notes,
        "ingredient_count": len(recipe.ingredients),
        "created_at": recipe.created_at.isoformat() if recipe.created_at else None,
        "updated_at": recipe.updated_at.isoformat() if recipe.updated_at else None
    }

    if include_ingredients:
        result["ingredients"] = [ingredient_to_dict(ing) for ing in recipe.ingredients]
        result["cost"] = calc_recipe_cost(recipe)

    else:
        # Still include cost summary for list views
        result["cost"] = calc_recipe_cost(recipe)

    return result


# ─── Recipes CRUD ────────────────────────────────────────────────────────────

@recipe_bp.route("/api/recipes", methods=["GET"])
def list_recipes():
    """List all recipes with cost summaries."""
    db = Session()
    try:
        category = request.args.get("category")
        query = db.query(Recipe).order_by(Recipe.name)
        if category:
            query = query.filter(Recipe.category == category)
        recipes = query.all()
        return jsonify([recipe_to_dict(r, include_ingredients=False) for r in recipes])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/recipes/<int:recipe_id>", methods=["GET"])
def get_recipe(recipe_id):
    """Get a single recipe with all ingredients and cost breakdown."""
    db = Session()
    try:
        recipe = db.query(Recipe).filter_by(id=recipe_id).first()
        if not recipe:
            return jsonify({"error": "Recipe not found"}), 404
        return jsonify(recipe_to_dict(recipe, include_ingredients=True))
    finally:
        db.close()


@recipe_bp.route("/api/recipes", methods=["POST"])
def create_recipe():
    """Create a new recipe with ingredients."""
    db = Session()
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"error": "Recipe name is required"}), 400

        recipe = Recipe(
            name=data["name"],
            category=data.get("category"),
            created_by=data.get("created_by"),
            portions=data.get("portions"),
            selling_price=data.get("selling_price"),
            seasoning_cost_percent=data.get("seasoning_cost_percent", 0),
            notes=data.get("notes")
        )
        db.add(recipe)
        db.flush()

        # Add ingredients
        for ing_data in data.get("ingredients", []):
            ing = RecipeIngredient(
                recipe_id=recipe.id,
                product_id=ing_data.get("product_id"),
                description=ing_data.get("description", ""),
                quantity=ing_data.get("quantity"),
                unit=ing_data.get("unit"),
                unit_price=ing_data.get("unit_price"),
                is_manual_price=ing_data.get("is_manual_price", False),
                trimming_percent=ing_data.get("trimming_percent", 0),
                supplier_name=ing_data.get("supplier_name")
            )
            db.add(ing)

        db.commit()
        # Re-fetch to get relationships
        recipe = db.query(Recipe).filter_by(id=recipe.id).first()
        return jsonify(recipe_to_dict(recipe)), 201

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating recipe: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/recipes/<int:recipe_id>", methods=["PUT"])
def update_recipe(recipe_id):
    """Update a recipe and its ingredients."""
    db = Session()
    try:
        recipe = db.query(Recipe).filter_by(id=recipe_id).first()
        if not recipe:
            return jsonify({"error": "Recipe not found"}), 404

        data = request.get_json()

        # Update recipe fields
        if "name" in data:
            recipe.name = data["name"]
        if "category" in data:
            recipe.category = data["category"]
        if "created_by" in data:
            recipe.created_by = data["created_by"]
        if "portions" in data:
            recipe.portions = data["portions"]
        if "selling_price" in data:
            recipe.selling_price = data["selling_price"]
        if "seasoning_cost_percent" in data:
            recipe.seasoning_cost_percent = data["seasoning_cost_percent"]
        if "notes" in data:
            recipe.notes = data["notes"]
        recipe.updated_at = datetime.utcnow()

        # Replace ingredients if provided
        if "ingredients" in data:
            # Delete old ingredients
            db.query(RecipeIngredient).filter_by(recipe_id=recipe.id).delete()
            db.flush()

            for ing_data in data["ingredients"]:
                ing = RecipeIngredient(
                    recipe_id=recipe.id,
                    product_id=ing_data.get("product_id"),
                    description=ing_data.get("description", ""),
                    quantity=ing_data.get("quantity"),
                    unit=ing_data.get("unit"),
                    unit_price=ing_data.get("unit_price"),
                    is_manual_price=ing_data.get("is_manual_price", False),
                    trimming_percent=ing_data.get("trimming_percent", 0),
                    supplier_name=ing_data.get("supplier_name")
                )
                db.add(ing)

        db.commit()
        recipe = db.query(Recipe).filter_by(id=recipe.id).first()
        return jsonify(recipe_to_dict(recipe))

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating recipe: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
def delete_recipe(recipe_id):
    """Delete a recipe."""
    db = Session()
    try:
        recipe = db.query(Recipe).filter_by(id=recipe_id).first()
        if not recipe:
            return jsonify({"error": "Recipe not found"}), 404
        db.delete(recipe)
        db.commit()
        return jsonify({"message": f"Recipe '{recipe.name}' deleted"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Recipe AI Parser ────────────────────────────────────────────────────────

@recipe_bp.route("/api/recipes/parse", methods=["POST"])
def parse_recipe_text():
    """
    Parse messy recipe text using AI. Returns structured recipe data
    with ingredient matching against known products from invoices.
    The frontend should then let the user review and confirm.
    """
    db = Session()
    try:
        data = request.get_json()
        raw_text = data.get("text", "").strip()
        if not raw_text:
            return jsonify({"error": "No recipe text provided"}), 400

        import anthropic
        import json
        import os

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        # Get known products for matching
        products = db.query(Product).all()
        product_list = []
        for p in products:
            entry = f"- {p.name}"
            if p.supplier:
                entry += f" (supplier: {p.supplier.name})"
            if p.unit:
                entry += f" [{p.unit}]"
            if p.current_price:
                entry += f" {p.current_price} SEK"
            product_list.append(entry)

        products_context = "\n".join(product_list[:200])  # Limit context size

        prompt = f"""You are a recipe parser for a restaurant. Parse the following messy recipe text into structured data.

The text may be in French, English, or Swedish. Normalize everything to a clean format.

KNOWN PRODUCTS FROM INVOICES (match ingredients to these when possible):
{products_context}

RECIPE TEXT:
{raw_text}

Return a JSON object with this structure:
{{
  "name": "recipe name (inferred from the text)",
  "category": "one of: sauce, pastry, garnish, base, main, dessert, other",
  "portions": number or null,
  "ingredients": [
    {{
      "description": "clean ingredient name",
      "quantity": number,
      "unit": "kg, g, liter, cl, ml, st, etc. (normalized)",
      "matched_product_name": "name of matched product from invoice list, or null if no match",
      "matched_product_confidence": "high, medium, low, or none",
      "needs_clarification": true/false,
      "clarification_type": "product_match, supplier_choice, unknown_product, no_invoice_match, or null",
      "clarification_question": "question to ask the user if needs_clarification is true",
      "clarification_options": ["option1", "option2"] or null,
      "suggested_trimming_percent": number or 0
    }}
  ],
  "notes": "all cooking instructions, method steps, and preparation notes from the text (lines starting with - or describing how to cook/prepare — everything that is NOT an ingredient line)"
}}

Be smart about matching:
- "morötter" = "carrots" = "carottes" — match across languages
- If multiple suppliers sell the same product, set needs_clarification=true and ask which supplier
- If a product seems like a finished item (ice cream, bread), note it may need a recipe
- Suggest trimming percentages for common items
- BE AGGRESSIVE about flagging clarifications — better to ask than guess wrong
- If multiple suppliers sell the same product, set needs_clarification=true, type="supplier_choice", and list supplier names as options
- If a product name matches multiple items, set type="product_match" and list the options
- Suggest trimming percentages for common items (20% for fish, 15% for meat, 10% for vegetables, etc.)
- IMPORTANT: Distinguish between TWO cases when a product is not in KNOWN PRODUCTS:
  1. You KNOW what the ingredient is (e.g. baking powder, vanilla, corn starch) but it's not in invoices: set clarification_type="no_invoice_match", question="I recognize '[name]' but couldn't find it in your invoices. Use this name or enter your own?", options=["Yes, use '[name]'", "Enter custom name"]
  2. You genuinely CANNOT determine what the product is: set clarification_type="unknown_product", question="I don't recognize '[name]'. What product is this?", options=["Type the product name"]. LAST RESORT only.
  Common ingredients (flour, sugar, salt, baking powder, spices, oils, vinegar, etc.) are ALWAYS case 1, never case 2.
- Normalize all units consistently (convert tbsp to ml, cups to dl, etc.)

PRICE SANITY CHECK — VERY IMPORTANT:
- Products with unit [FRP], [ST], [KRT] are often sold in BULK packages (cartons, cases, crates), NOT individual pieces
- Example: "Ägg M frigående [FRP] 389 SEK" = a CARTON of ~120 eggs at 389 kr, NOT 389 kr per egg
- When you match a recipe ingredient (e.g. "12 eggs") to a product sold per FRP/carton, set needs_clarification=true with type="price_check" and ask: "This product is sold per carton (FRP) at X kr. How many pieces per carton?" with options like ["120", "90", "60", "30", "Other"]
- Similarly, if a matched price seems unreasonably high per unit (e.g. >50 kr for a single egg, >20 kr for 1g of sugar), flag it for clarification
- Common sense: eggs ~2-5 kr each, butter ~100-150 kr/kg, milk ~15-25 kr/L, cream ~30-50 kr/L, sugar ~15-30 kr/kg

- IMPORTANT: Extract ALL cooking instructions, method steps, and preparation notes into the "notes" field. Lines starting with "-" or describing cooking methods/techniques are NOT ingredients — they go into notes. Include the full process/method.

Return ONLY valid JSON, no markdown formatting."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = response.content[0].text.strip()
        # Try to parse JSON from response
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        parsed = json.loads(result_text)

        # Enrich with product IDs and prices (with cross-language fallback)
        for ing in parsed.get("ingredients", []):
            matched_name = ing.get("matched_product_name")
            product = None

            if matched_name:
                # Step 1: Try direct ILIKE on the AI's suggested match
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{matched_name[:30]}%")
                ).first()

            if not product:
                # Step 2: Cross-language fallback using food dictionary
                search_term = matched_name or ing.get("description", "")
                if search_term:
                    bilingual_terms = search_food_terms(search_term)
                    if len(bilingual_terms) > 1:
                        conditions = [
                            Product.name.ilike(f"%{term}%")
                            for term in bilingual_terms
                        ]
                        product = db.query(Product).filter(
                            or_(*conditions)
                        ).first()

            if product:
                ing["product_id"] = product.id
                ing["matched_product_name"] = product.name
                ing["supplier_name"] = product.supplier.name if product.supplier else None

                # Calculate unit price — handle bulk packages (FRP/KRT with package_quantity)
                raw_price = product.current_price or 0
                product_unit = (product.unit or "").lower()
                ing_unit = (ing.get("unit") or "st").lower()

                if raw_price <= 0:
                    ing["unit_price"] = 0
                elif product.package_quantity and product.package_quantity > 0 and product_unit in ("frp", "krt", "kartong", "back", "lda"):
                    # Bulk package: price is for the whole carton, divide by piece count
                    price_per_piece = raw_price / product.package_quantity
                    ing["unit_price"] = round(price_per_piece, 4)
                    ing["package_quantity"] = product.package_quantity
                else:
                    # Use existing helper functions for kg/L package adjustments
                    adjusted_price = _adjust_price_for_package_size(product.name, raw_price)
                    ing["unit_price"] = round(_convert_price_to_ingredient_unit(adjusted_price, ing_unit), 4)
                    ing["raw_price_per_base_unit"] = adjusted_price

                ing["price_unit"] = ing_unit
                ing["needs_clarification"] = False
                ing["matched_product_confidence"] = ing.get("matched_product_confidence") or "medium"
            else:
                ing["product_id"] = None
                ing["unit_price"] = None


        # Smart enrichment: for clarification items, search products to provide real options
        for ing in parsed.get("ingredients", []):
            if not ing.get("needs_clarification"):
                continue
            options = ing.get("clarification_options") or []
            # Remove "Skip" to check if there are real options
            real_options = [o for o in options if o.lower() != "skip"]
            if real_options:
                continue  # AI already provided options, keep them

            # No real options — search products using description
            desc = ing.get("description", "")
            if not desc:
                continue

            # Try direct DB search first
            search_term = f"%{desc[:30]}%"
            matches = db.query(Product).filter(
                Product.name.ilike(search_term)
            ).limit(10).all()

            # If no direct match, try cross-language search with AI
            if not matches:
                try:
                    import anthropic as anth_search
                    search_client = anth_search.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
                    translate_resp = search_client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=200,
                        messages=[{"role": "user", "content": f"Translate this food/ingredient term into Swedish, French, and English. Return ONLY a JSON array of search terms. Term: {desc}"}]
                    )
                    terms_text = translate_resp.content[0].text.strip()
                    if terms_text.startswith("["):
                        search_terms = json.loads(terms_text)
                        conditions = [Product.name.ilike(f"%{t}%") for t in search_terms]
                        matches = db.query(Product).filter(or_(*conditions)).limit(10).all()
                except Exception as e:
                    logger.error(f"Cross-language search failed for '{desc}': {e}")

            if matches:
                # Build options with price and supplier info
                new_options = []
                for p in matches:
                    price_str = f"{p.current_price:.2f} SEK/{p.unit or 'kg'}" if p.current_price else "no price"
                    supplier_str = p.supplier.name if p.supplier else "unknown"
                    new_options.append(f"{p.name} - {price_str} ({supplier_str})")
                new_options.append("Skip")
                ing["clarification_options"] = new_options
                ing["clarification_type"] = "product_match"
                ing["clarification_question"] = f"Which product matches '{desc}'?"
            else:
                # Truly unknown — change to unknown_product type so frontend shows text input
                ing["clarification_type"] = "unknown_product"
                ing["clarification_question"] = f"I couldn't find '{desc}' in your invoices. What product is this?"
                ing["clarification_options"] = ["Type the product name"]

        # Create a review session
        ingredients = parsed.get("ingredients", [])
        session_id = _create_recipe_review_session(parsed, ingredients)
        session = _get_recipe_session(session_id)
        total_questions = len(session["questions"])
        unanswered = sum(1 for q in session["questions"] if not q["is_answered"])

        return jsonify({
            "session_id": session_id,
            "name": parsed.get("name"),
            "category": parsed.get("category"),
            "portions": parsed.get("portions"),
            "notes": parsed.get("notes"),
            "ingredients": ingredients,
            "has_questions": total_questions > 0,
            "total_questions": total_questions,
            "unanswered_questions": unanswered,
            "status": session["status"]
        })

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return jsonify({"error": "AI returned invalid format. Please try again."}), 500
    except Exception as e:
        logger.error(f"Error parsing recipe: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()




# ─── Recipe Review Session Endpoints ───────────────────────────────────────

@recipe_bp.route("/api/recipes/review/<session_id>", methods=["GET"])
def get_recipe_review_status(session_id):
    session = _get_recipe_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404
    questions = session["questions"]
    total = len(questions)
    answered = sum(1 for q in questions if q["is_answered"])
    next_question = None
    for q in questions:
        if not q["is_answered"]:
            next_question = q
            break
    result = {
        "session_id": session_id,
        "status": session["status"],
        "total_questions": total,
        "answered_questions": answered,
        "remaining_questions": total - answered,
        "progress_pct": round(answered / total * 100) if total > 0 else 100,
        "current_question": next_question,
        "ingredient_count": len(session["ingredients"])
    }
    if session["status"] == "ready":
        result["parsed"] = session["parsed"]
        result["ingredients"] = session["ingredients"]
    return jsonify(result)

@recipe_bp.route("/api/recipes/review/<session_id>/answer", methods=["POST"])
def submit_recipe_review_answer(session_id):
    session = _get_recipe_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404
    data = request.get_json()
    question_id = data.get("question_id")
    answer = data.get("answer")
    if not question_id or not answer:
        return jsonify({"error": "question_id and answer are required"}), 400
    success, message = _apply_recipe_answer(session, question_id, answer)
    if not success:
        return jsonify({"error": message}), 400
    questions = session["questions"]
    total = len(questions)
    answered = sum(1 for q in questions if q["is_answered"])
    next_question = None
    for q in questions:
        if not q["is_answered"]:
            next_question = q
            break
    return jsonify({
        "success": True,
        "message": message,
        "session_id": session_id,
        "status": session["status"],
        "total_questions": total,
        "answered_questions": answered,
        "remaining_questions": total - answered,
        "progress_pct": round(answered / total * 100) if total > 0 else 100,
        "current_question": next_question
    })

@recipe_bp.route("/api/recipes/review/<session_id>/go-back", methods=["POST"])
def recipe_review_go_back(session_id):
    session = _get_recipe_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404
    data = request.get_json() or {}
    current_question_id = data.get("current_question_id")
    questions = session["questions"]
    previous_question = None
    if current_question_id:
        current_idx = None
        for i, q in enumerate(questions):
            if q["id"] == current_question_id:
                current_idx = i
                break
        if current_idx is not None and current_idx > 0:
            for i in range(current_idx - 1, -1, -1):
                if questions[i]["is_answered"]:
                    previous_question = questions[i]
                    previous_question["is_answered"] = False
                    previous_question["answer"] = None
                    ing = session["ingredients"][previous_question["item_index"]]
                    ing["needs_clarification"] = True
                    break
    if not previous_question:
        return jsonify({"error": "No previous question to go back to"}), 400
    total = len(questions)
    answered = sum(1 for q in questions if q["is_answered"])
    session["status"] = "reviewing"
    return jsonify({
        "success": True,
        "session_id": session_id,
        "status": "reviewing",
        "total_questions": total,
        "answered_questions": answered,
        "remaining_questions": total - answered,
        "progress_pct": round(answered / total * 100) if total > 0 else 0,
        "current_question": previous_question
    })

@recipe_bp.route("/api/recipes/review/<session_id>/skip-all", methods=["POST"])
def skip_all_recipe_questions(session_id):
    session = _get_recipe_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404
    for q in session["questions"]:
        if not q["is_answered"]:
            q["answer"] = "Skip"
            q["is_answered"] = True
            session["ingredients"][q["item_index"]]["needs_clarification"] = False
    session["status"] = "ready"
    return jsonify({
        "success": True,
        "session_id": session_id,
        "status": "ready",
        "parsed": session["parsed"],
        "ingredients": session["ingredients"]
    })

@recipe_bp.route("/api/recipes/review/<session_id>/ingredients", methods=["GET"])
def get_recipe_session_ingredients(session_id):
    session = _get_recipe_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404
    return jsonify({
        "session_id": session_id,
        "status": session["status"],
        "parsed": session["parsed"],
        "ingredients": session["ingredients"],
        "ingredient_count": len(session["ingredients"])
    })

# ─── Dishes CRUD ─────────────────────────────────────────────────────────────

def calc_dish_cost(dish, db):
    """Calculate total dish cost from its recipe and product components."""
    total = 0
    for comp in dish.components:
        if comp.recipe_id and comp.recipe:
            recipe_cost = calc_recipe_cost(comp.recipe)
            per_portion = recipe_cost["per_portion"]
            total += per_portion * (comp.quantity or 1)
        elif comp.unit_price:
            total += comp.unit_price * (comp.quantity or 1)
        elif comp.product_id and comp.product:
            total += (comp.product.current_price or 0) * (comp.quantity or 1)
    return round(total, 2)


def dish_to_dict(dish, db):
    """Serialize a Dish."""
    total_cost = calc_dish_cost(dish, db)
    food_cost_pct = (total_cost / dish.selling_price * 100) if dish.selling_price and dish.selling_price > 0 else None

    return {
        "id": dish.id,
        "name": dish.name,
        "category": dish.category,
        "selling_price": dish.selling_price,
        "notes": dish.notes,
        "total_cost": total_cost,
        "food_cost_percent": round(food_cost_pct, 1) if food_cost_pct else None,
        "component_count": len(dish.components),
        "components": [{
            "id": c.id,
            "recipe_id": c.recipe_id,
            "recipe_name": c.recipe.name if c.recipe else None,
            "product_id": c.product_id,
            "product_name": c.product.name if c.product else None,
            "description": c.description,
            "quantity": c.quantity,
            "unit": c.unit,
            "unit_price": c.unit_price,
            "is_manual_price": c.is_manual_price
        } for c in dish.components],
        "created_at": dish.created_at.isoformat() if dish.created_at else None,
        "updated_at": dish.updated_at.isoformat() if dish.updated_at else None
    }


@recipe_bp.route("/api/dishes", methods=["GET"])
def list_dishes():
    db = Session()
    try:
        category = request.args.get("category")
        query = db.query(Dish).order_by(Dish.name)
        if category:
            query = query.filter(Dish.category == category)
        dishes = query.all()
        return jsonify([dish_to_dict(d, db) for d in dishes])
    finally:
        db.close()


@recipe_bp.route("/api/dishes/<int:dish_id>", methods=["GET"])
def get_dish(dish_id):
    db = Session()
    try:
        dish = db.query(Dish).filter_by(id=dish_id).first()
        if not dish:
            return jsonify({"error": "Dish not found"}), 404
        return jsonify(dish_to_dict(dish, db))
    finally:
        db.close()


@recipe_bp.route("/api/dishes", methods=["POST"])
def create_dish():
    db = Session()
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"error": "Dish name is required"}), 400

        dish = Dish(
            name=data["name"],
            category=data.get("category"),
            selling_price=data.get("selling_price"),
            notes=data.get("notes")
        )
        db.add(dish)
        db.flush()

        for comp_data in data.get("components", []):
            comp = DishComponent(
                dish_id=dish.id,
                recipe_id=comp_data.get("recipe_id"),
                product_id=comp_data.get("product_id"),
                description=comp_data.get("description"),
                quantity=comp_data.get("quantity", 1),
                unit=comp_data.get("unit"),
                unit_price=comp_data.get("unit_price"),
                is_manual_price=comp_data.get("is_manual_price", False)
            )
            db.add(comp)

        db.commit()
        dish = db.query(Dish).filter_by(id=dish.id).first()
        return jsonify(dish_to_dict(dish, db)), 201

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating dish: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/dishes/<int:dish_id>", methods=["PUT"])
def update_dish(dish_id):
    db = Session()
    try:
        dish = db.query(Dish).filter_by(id=dish_id).first()
        if not dish:
            return jsonify({"error": "Dish not found"}), 404

        data = request.get_json()
        if "name" in data:
            dish.name = data["name"]
        if "category" in data:
            dish.category = data["category"]
        if "selling_price" in data:
            dish.selling_price = data["selling_price"]
        if "notes" in data:
            dish.notes = data["notes"]
        dish.updated_at = datetime.utcnow()

        if "components" in data:
            db.query(DishComponent).filter_by(dish_id=dish.id).delete()
            db.flush()
            for comp_data in data["components"]:
                comp = DishComponent(
                    dish_id=dish.id,
                    recipe_id=comp_data.get("recipe_id"),
                    product_id=comp_data.get("product_id"),
                    description=comp_data.get("description"),
                    quantity=comp_data.get("quantity", 1),
                    unit=comp_data.get("unit"),
                    unit_price=comp_data.get("unit_price"),
                    is_manual_price=comp_data.get("is_manual_price", False)
                )
                db.add(comp)

        db.commit()
        dish = db.query(Dish).filter_by(id=dish.id).first()
        return jsonify(dish_to_dict(dish, db))

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/dishes/<int:dish_id>", methods=["DELETE"])
def delete_dish(dish_id):
    db = Session()
    try:
        dish = db.query(Dish).filter_by(id=dish_id).first()
        if not dish:
            return jsonify({"error": "Dish not found"}), 404
        db.delete(dish)
        db.commit()
        return jsonify({"message": f"Dish '{dish.name}' deleted"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Menus CRUD ──────────────────────────────────────────────────────────────

def menu_to_dict(menu, db):
    """Serialize a Menu with cost calculations."""
    total_cost = 0
    courses = {}

    for item in menu.items:
        course = item.course or "other"
        if course not in courses:
            courses[course] = []

        dish_cost = 0
        if item.dish:
            dish_cost = calc_dish_cost(item.dish, db)
            total_cost += dish_cost

        courses[course].append({
            "id": item.id,
            "dish_id": item.dish_id,
            "dish_name": item.dish.name if item.dish else None,
            "dish_category": item.dish.category if item.dish else None,
            "course": item.course,
            "position": item.position,
            "notes": item.notes,
            "dish_cost": dish_cost
        })

    # Sort each course by position
    for course in courses:
        courses[course].sort(key=lambda x: x["position"])

    food_cost_pct = (total_cost / menu.selling_price * 100) if menu.selling_price and menu.selling_price > 0 else None

    return {
        "id": menu.id,
        "name": menu.name,
        "menu_type": menu.menu_type,
        "selling_price": menu.selling_price,
        "notes": menu.notes,
        "total_cost": round(total_cost, 2),
        "food_cost_percent": round(food_cost_pct, 1) if food_cost_pct else None,
        "dish_count": len(menu.items),
        "courses": courses,
        "items": [{
            "id": item.id,
            "dish_id": item.dish_id,
            "dish_name": item.dish.name if item.dish else None,
            "course": item.course,
            "position": item.position,
            "notes": item.notes
        } for item in sorted(menu.items, key=lambda x: (x.course or "", x.position))],
        "created_at": menu.created_at.isoformat() if menu.created_at else None,
        "updated_at": menu.updated_at.isoformat() if menu.updated_at else None
    }


@recipe_bp.route("/api/menus", methods=["GET"])
def list_menus():
    db = Session()
    try:
        menus = db.query(Menu).order_by(Menu.name).all()
        return jsonify([menu_to_dict(m, db) for m in menus])
    finally:
        db.close()


@recipe_bp.route("/api/menus/<int:menu_id>", methods=["GET"])
def get_menu(menu_id):
    db = Session()
    try:
        menu = db.query(Menu).filter_by(id=menu_id).first()
        if not menu:
            return jsonify({"error": "Menu not found"}), 404
        return jsonify(menu_to_dict(menu, db))
    finally:
        db.close()


@recipe_bp.route("/api/menus", methods=["POST"])
def create_menu():
    db = Session()
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"error": "Menu name is required"}), 400

        menu = Menu(
            name=data["name"],
            menu_type=data.get("menu_type"),
            selling_price=data.get("selling_price"),
            notes=data.get("notes")
        )
        db.add(menu)
        db.flush()

        for item_data in data.get("items", []):
            item = MenuItem(
                menu_id=menu.id,
                dish_id=item_data.get("dish_id"),
                course=item_data.get("course"),
                position=item_data.get("position", 0),
                notes=item_data.get("notes")
            )
            db.add(item)

        db.commit()
        menu = db.query(Menu).filter_by(id=menu.id).first()
        return jsonify(menu_to_dict(menu, db)), 201

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating menu: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/menus/<int:menu_id>", methods=["PUT"])
def update_menu(menu_id):
    db = Session()
    try:
        menu = db.query(Menu).filter_by(id=menu_id).first()
        if not menu:
            return jsonify({"error": "Menu not found"}), 404

        data = request.get_json()
        if "name" in data:
            menu.name = data["name"]
        if "menu_type" in data:
            menu.menu_type = data["menu_type"]
        if "selling_price" in data:
            menu.selling_price = data["selling_price"]
        if "notes" in data:
            menu.notes = data["notes"]
        menu.updated_at = datetime.utcnow()

        if "items" in data:
            db.query(MenuItem).filter_by(menu_id=menu.id).delete()
            db.flush()
            for item_data in data["items"]:
                item = MenuItem(
                    menu_id=menu.id,
                    dish_id=item_data.get("dish_id"),
                    course=item_data.get("course"),
                    position=item_data.get("position", 0),
                    notes=item_data.get("notes")
                )
                db.add(item)

        db.commit()
        menu = db.query(Menu).filter_by(id=menu.id).first()
        return jsonify(menu_to_dict(menu, db))

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@recipe_bp.route("/api/menus/<int:menu_id>", methods=["DELETE"])
def delete_menu(menu_id):
    db = Session()
    try:
        menu = db.query(Menu).filter_by(id=menu_id).first()
        if not menu:
            return jsonify({"error": "Menu not found"}), 404
        db.delete(menu)
        db.commit()
        return jsonify({"message": f"Menu '{menu.name}' deleted"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Recipe Categories ───────────────────────────────────────────────────────

@recipe_bp.route("/api/recipe-categories", methods=["GET"])
def recipe_categories():
    """Return list of used recipe categories."""
    db = Session()
    try:
        cats = db.query(Recipe.category).distinct().filter(Recipe.category.isnot(None)).all()
        return jsonify([c[0] for c in cats])
    finally:
        db.close()


@recipe_bp.route("/api/dish-categories", methods=["GET"])
def dish_categories():
    """Return list of used dish categories."""
    db = Session()
    try:
        cats = db.query(Dish.category).distinct().filter(Dish.category.isnot(None)).all()
        return jsonify([c[0] for c in cats])
    finally:
        db.close()


# ─── Smart Search (cross-language) ─────────────────────────────────

@recipe_bp.route("/api/search/products", methods=["GET"])
def search_products_smart():
    """Search products with cross-language support (EN/SV/FR).
    First tries direct DB match, then uses AI translation if no results.
    Query params: ?q=lobster&fast=1 (fast skips AI translation)
    """
    query = request.args.get("q", "").strip()
    fast_mode = request.args.get("fast", "0") == "1"
    if not query or len(query) < 2:
        return jsonify([])

    db = Session()
    try:
        # Step 1: Direct ILIKE search
        search_term = f"%{query}%"
        products = db.query(Product).filter(
            Product.name.ilike(search_term)
        ).order_by(Product.name).limit(20).all()

        # Step 2: If no direct match and not fast mode, use AI to translate and search
        if not products and not fast_mode:
            try:
                client = anthropic.Anthropic()
                translate_response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    messages=[{"role": "user", "content": f"""Translate this food/ingredient term into Swedish, French, and English.
Return ONLY a JSON array of search terms (strings), including the original.
Example: ["lobster", "hummer", "homard"]
Term: {query}"""}]
                )
                import json as json_mod
                terms_text = translate_response.content[0].text.strip()
                if terms_text.startswith("["):
                    search_terms = json_mod.loads(terms_text)
                else:
                    search_terms = [query]

                # Search with all translated terms
                from sqlalchemy import or_
                conditions = [Product.name.ilike(f"%{term}%") for term in search_terms]
                products = db.query(Product).filter(
                    or_(*conditions)
                ).order_by(Product.name).limit(20).all()
            except Exception as e:
                logger.error(f"AI translation search failed: {e}")
                products = []

        results = []
        for p in products:
            pkg_price = _adjust_price_for_package_size(p.name, p.current_price or 0)
            results.append({
                "id": p.id,
                "name": p.name,
                "supplier_id": p.supplier_id,
                "supplier_name": p.supplier.name if p.supplier else None,
                "unit": p.unit,
                "current_price": pkg_price,
                "raw_invoice_price": p.current_price,
                "category": p.category
            })
        return jsonify(results)
    except Exception as e:
        import traceback
        logger.error(f"Search products error: {traceback.format_exc()}")
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500
    finally:
        db.close()


@recipe_bp.route("/api/search/recipes", methods=["GET"])
def search_recipes_smart():
    """Search recipes by name with cross-language support.
    Query param: ?q=ice cream
    """
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify([])

    db = Session()
    try:
        # Direct ILIKE search
        search_term = f"%{query}%"
        recipes = db.query(Recipe).filter(
            Recipe.name.ilike(search_term)
        ).order_by(Recipe.name).limit(20).all()

        # If no match, try AI translation
        if not recipes:
            try:
                client = anthropic.Anthropic()
                translate_response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    messages=[{"role": "user", "content": f"""Translate this food/dish/recipe term into Swedish, French, and English.
Return ONLY a JSON array of search terms (strings), including the original.
Example: ["ice cream", "glass", "glace"]
Term: {query}"""}]
                )
                import json as json_mod
                terms_text = translate_response.content[0].text.strip()
                if terms_text.startswith("["):
                    search_terms = json_mod.loads(terms_text)
                else:
                    search_terms = [query]

                from sqlalchemy import or_
                conditions = [Recipe.name.ilike(f"%{term}%") for term in search_terms]
                recipes = db.query(Recipe).filter(
                    or_(*conditions)
                ).order_by(Recipe.name).limit(20).all()
            except Exception as e:
                logger.error(f"AI recipe search failed: {e}")
                recipes = []

        results = []
        for r in recipes:
            cost_info = calc_recipe_cost(r) if r.ingredients else {"per_portion": 0, "total_cost": 0}
            results.append({
                "id": r.id,
                "name": r.name,
                "category": r.category,
                "portions": r.portions,
                "cost_per_portion": cost_info["per_portion"],
                "total_cost": cost_info["total_cost"],
                "ingredient_count": len(r.ingredients) if r.ingredients else 0
            })
        return jsonify(results)
    finally:
        db.close()
