"""
Recipe, Dish, and Menu API routes.
Register with: app.register_blueprint(recipe_bp)
"""
import logging
from flask import Blueprint, request, jsonify
from datetime import datetime
from models import (
    Session, Recipe, RecipeIngredient, Dish, DishComponent,
    Menu, MenuItem, Product, InvoiceLineItem, PriceHistory
)

logger = logging.getLogger(__name__)

recipe_bp = Blueprint('recipes', __name__)


# ─── Helper: calculate recipe cost ──────────────────────────────────────────

def calc_recipe_cost(recipe, ingredients=None):
    """Calculate total cost, per-portion cost, and food cost % for a recipe."""
    if ingredients is None:
        ingredients = recipe.ingredients

    subtotal = 0
    for ing in ingredients:
        price = ing.unit_price or 0
        qty = ing.quantity or 0
        trim = ing.trimming_percent or 0
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
      "clarification_question": "question to ask the user if needs_clarification is true, e.g. 'Which supplier for carrots: Sorunda or Menigo?'",
      "suggested_trimming_percent": number or 0
    }}
  ],
  "notes": "all cooking instructions, method steps, and preparation notes from the text (lines starting with - or describing how to cook/prepare — everything that is NOT an ingredient line)"
}}

Be smart about matching:
- "morötter" = "carrots" = "carottes" — match across languages
- If multiple suppliers sell the same product, set needs_clarification=true and ask which supplier
- If a product seems like a finished item (ice cream, bread), note it may need a recipe
- Suggest trimming percentages for common items (20% for fish, 15% for meat, 10% for vegetables, etc.)
- Normalize all units consistently (convert tbsp to ml, cups to dl, etc.)

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

        # Enrich with product IDs and prices
        for ing in parsed.get("ingredients", []):
            matched_name = ing.get("matched_product_name")
            if matched_name:
                # Find the product in DB
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{matched_name[:30]}%")
                ).first()
                if product:
                    ing["product_id"] = product.id
                    ing["unit_price"] = product.current_price
                    ing["supplier_name"] = product.supplier.name if product.supplier else None
                else:
                    ing["product_id"] = None
                    ing["unit_price"] = None

        return jsonify(parsed)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return jsonify({"error": "AI returned invalid format. Please try again."}), 500
    except Exception as e:
        logger.error(f"Error parsing recipe: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


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
    Query param: ?q=lobster
    """
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify([])

    db = Session()
    try:
        # Step 1: Direct ILIKE search
        search_term = f"%{query}%"
        products = db.query(Product).filter(
            Product.name.ilike(search_term)
        ).order_by(Product.name).limit(20).all()

        # Step 2: If no direct match, use AI to translate and search
        if not products:
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
            results.append({
                "id": p.id,
                "name": p.name,
                "supplier_id": p.supplier_id,
                "supplier_name": p.supplier.name if p.supplier else None,
                "unit": p.unit,
                "latest_price": p.latest_price,
                "current_price": p.current_price,
                "category": p.category
            })
        return jsonify(results)
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
