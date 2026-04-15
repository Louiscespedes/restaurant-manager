"""
Inventory API routes — input, AI processing, storage, and comparison.
Register with: app.register_blueprint(inventory_bp)
"""
import logging
import json
import os
from flask import Blueprint, request, jsonify
from datetime import datetime
from sqlalchemy import func
from models import (
    Session, Inventory, InventoryItem, Product, Recipe,
    RecipeIngredient, PriceHistory
)

logger = logging.getLogger(__name__)

inventory_bp = Blueprint('inventory', __name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

MONTH_NAMES_SV = {
    1: "Januari", 2: "Februari", 3: "Mars", 4: "April",
    5: "Maj", 6: "Juni", 7: "Juli", 8: "Augusti",
    9: "September", 10: "Oktober", 11: "November", 12: "December"
}


def inventory_to_dict(inv, include_items=True):
    """Serialize an Inventory."""
    # Group items by category
    categories = {}
    for item in inv.items:
        cat = item.category or "other"
        if cat not in categories:
            categories[cat] = {"items": [], "total_value": 0}
        categories[cat]["items"].append(item_to_dict(item))
        categories[cat]["total_value"] += item.total_value or 0

    for cat in categories:
        categories[cat]["total_value"] = round(categories[cat]["total_value"], 2)

    result = {
        "id": inv.id,
        "year": inv.year,
        "month": inv.month,
        "month_name": MONTH_NAMES_SV.get(inv.month, str(inv.month)),
        "status": inv.status,
        "total_value": round(inv.total_value or 0, 2),
        "item_count": len(inv.items),
        "categories": categories,
        "notes": inv.notes,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "updated_at": inv.updated_at.isoformat() if inv.updated_at else None
    }

    if include_items:
        result["items"] = [item_to_dict(i) for i in inv.items]

    return result


def item_to_dict(item):
    return {
        "id": item.id,
        "product_id": item.product_id,
        "recipe_id": item.recipe_id,
        "description": item.description,
        "quantity": item.quantity,
        "unit": item.unit,
        "unit_price": item.unit_price,
        "is_manual_price": item.is_manual_price,
        "category": item.category,
        "supplier_name": item.supplier_name,
        "total_value": round(item.total_value or 0, 2),
        "price_found": item.unit_price is not None and item.unit_price > 0
    }


# ─── Inventory CRUD ──────────────────────────────────────────────────────────

@inventory_bp.route("/api/inventories", methods=["GET"])
def list_inventories():
    """List all inventories, optionally filtered by year."""
    db = Session()
    try:
        year = request.args.get("year", type=int)
        query = db.query(Inventory).order_by(Inventory.year.desc(), Inventory.month.desc())
        if year:
            query = query.filter(Inventory.year == year)
        inventories = query.all()
        return jsonify([inventory_to_dict(inv, include_items=False) for inv in inventories])
    finally:
        db.close()


@inventory_bp.route("/api/inventories/<int:inv_id>", methods=["GET"])
def get_inventory(inv_id):
    """Get a single inventory with all items."""
    db = Session()
    try:
        inv = db.query(Inventory).filter_by(id=inv_id).first()
        if not inv:
            return jsonify({"error": "Inventory not found"}), 404
        return jsonify(inventory_to_dict(inv, include_items=True))
    finally:
        db.close()


@inventory_bp.route("/api/inventories/check", methods=["GET"])
def check_existing_inventory():
    """Check if an inventory already exists for a given month."""
    db = Session()
    try:
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        if not year or not month:
            return jsonify({"error": "year and month required"}), 400

        existing = db.query(Inventory).filter_by(year=year, month=month).first()
        if existing:
            return jsonify({
                "exists": True,
                "inventory": inventory_to_dict(existing, include_items=False)
            })
        return jsonify({"exists": False})
    finally:
        db.close()


@inventory_bp.route("/api/inventories", methods=["POST"])
def create_inventory():
    """Create a new inventory. If one exists for same month, returns conflict."""
    db = Session()
    try:
        data = request.get_json()
        year = data.get("year")
        month = data.get("month")
        action = data.get("action", "create")  # create, replace, add_to

        if not year or not month:
            return jsonify({"error": "year and month are required"}), 400

        existing = db.query(Inventory).filter_by(year=year, month=month).first()

        if existing and action == "create":
            return jsonify({
                "error": "inventory_exists",
                "message": f"An inventory already exists for {MONTH_NAMES_SV.get(month)} {year}",
                "existing_id": existing.id,
                "options": ["replace", "add_to", "cancel"]
            }), 409

        if existing and action == "replace":
            db.delete(existing)
            db.flush()

        if existing and action == "add_to":
            inv = existing
        else:
            inv = Inventory(year=year, month=month, status="draft")
            db.add(inv)
            db.flush()

        # Add items
        total = 0
        for item_data in data.get("items", []):
            qty = item_data.get("quantity") or 0
            price = item_data.get("unit_price") or 0
            line_total = qty * price

            item = InventoryItem(
                inventory_id=inv.id,
                product_id=item_data.get("product_id"),
                recipe_id=item_data.get("recipe_id"),
                description=item_data.get("description", ""),
                quantity=qty,
                unit=item_data.get("unit"),
                unit_price=price,
                is_manual_price=item_data.get("is_manual_price", False),
                category=item_data.get("category"),
                supplier_name=item_data.get("supplier_name"),
                total_value=line_total
            )
            db.add(item)
            total += line_total

        inv.total_value = total
        inv.updated_at = datetime.utcnow()
        db.commit()

        inv = db.query(Inventory).filter_by(id=inv.id).first()
        return jsonify(inventory_to_dict(inv)), 201

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating inventory: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@inventory_bp.route("/api/inventories/<int:inv_id>", methods=["PUT"])
def update_inventory(inv_id):
    """Update inventory status or notes."""
    db = Session()
    try:
        inv = db.query(Inventory).filter_by(id=inv_id).first()
        if not inv:
            return jsonify({"error": "Inventory not found"}), 404

        data = request.get_json()
        if "status" in data:
            inv.status = data["status"]
        if "notes" in data:
            inv.notes = data["notes"]

        # Recalculate total if items were updated
        if "items" in data:
            db.query(InventoryItem).filter_by(inventory_id=inv.id).delete()
            db.flush()

            total = 0
            for item_data in data["items"]:
                qty = item_data.get("quantity") or 0
                price = item_data.get("unit_price") or 0
                line_total = qty * price

                item = InventoryItem(
                    inventory_id=inv.id,
                    product_id=item_data.get("product_id"),
                    recipe_id=item_data.get("recipe_id"),
                    description=item_data.get("description", ""),
                    quantity=qty,
                    unit=item_data.get("unit"),
                    unit_price=price,
                    is_manual_price=item_data.get("is_manual_price", False),
                    category=item_data.get("category"),
                    supplier_name=item_data.get("supplier_name"),
                    total_value=line_total
                )
                db.add(item)
                total += line_total
            inv.total_value = total

        inv.updated_at = datetime.utcnow()
        db.commit()

        inv = db.query(Inventory).filter_by(id=inv.id).first()
        return jsonify(inventory_to_dict(inv))

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@inventory_bp.route("/api/inventories/<int:inv_id>", methods=["DELETE"])
def delete_inventory(inv_id):
    db = Session()
    try:
        inv = db.query(Inventory).filter_by(id=inv_id).first()
        if not inv:
            return jsonify({"error": "Inventory not found"}), 404
        db.delete(inv)
        db.commit()
        return jsonify({"message": f"Inventory {MONTH_NAMES_SV.get(inv.month)} {inv.year} deleted"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Inventory AI Parser ─────────────────────────────────────────────────────

@inventory_bp.route("/api/inventories/parse", methods=["POST"])
def parse_inventory_text():
    """
    Parse messy inventory text (pasted or voice-transcribed) using AI.
    Returns structured items with product matching and clarification questions.
    Supports French, English, and Swedish.
    """
    db = Session()
    try:
        data = request.get_json()
        raw_text = data.get("text", "").strip()
        if not raw_text:
            return jsonify({"error": "No inventory text provided"}), 400

        import anthropic

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
            if p.category:
                entry += f" cat:{p.category}"
            product_list.append(entry)

        products_context = "\n".join(product_list[:250])

        # Get known recipes for finished products
        recipes = db.query(Recipe).all()
        recipe_list = [f"- {r.name} (category: {r.category})" for r in recipes]
        recipe_context = "\n".join(recipe_list[:50])

        prompt = f"""You are an inventory parser for a high-end restaurant. Parse messy inventory text into structured data.

The text may be in French, English, or Swedish (or mixed). The user likely wrote this quickly on their phone.
Be smart about typos, abbreviations, and casual notation (e.g., "morot 2kg" means 2 kg of carrots).

KNOWN PRODUCTS FROM INVOICES:
{products_context}

KNOWN RECIPES (for finished products):
{recipe_context}

INVENTORY TEXT:
{raw_text}

Return JSON:
{{
  "items": [
    {{
      "description": "clean product name",
      "quantity": number,
      "unit": "kg, g, liter, st, etc.",
      "category": "fish, meat, vegetables, dairy, wine, spirits, cleaning, prepared, dry_goods, other",
      "matched_product_name": "name from known products or null",
      "matched_product_confidence": "high, medium, low, none",
      "unit_price": number or null,
      "supplier_name": "supplier name or null",
      "is_finished_product": true/false,
      "matched_recipe_name": "recipe name if this is a finished product, or null",
      "needs_clarification": true/false,
      "clarification_question": "question for user, or null"
    }}
  ]
}}

Rules:
- Match products across languages: morötter = carrots = carottes
- If unit is per-piece but price is per-kg, flag for clarification
- If multiple suppliers sell same product, ask which one
- If item looks like a finished product (ice cream, bread, sauce), set is_finished_product=true and check recipes
- Categorize everything: fish, meat, vegetables, dairy, wine, spirits, cleaning, prepared, dry_goods, other
- Normalize units (always use kg for weight >500g, g for <500g, liter for liquids, st for pieces)
- Return ONLY valid JSON, no markdown"""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = response.content[0].text.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        parsed = json.loads(result_text)

        # Enrich with product IDs
        for item in parsed.get("items", []):
            matched_name = item.get("matched_product_name")
            if matched_name:
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{matched_name[:30]}%")
                ).first()
                if product:
                    item["product_id"] = product.id
                    if not item.get("unit_price"):
                        item["unit_price"] = product.current_price
                    if not item.get("supplier_name") and product.supplier:
                        item["supplier_name"] = product.supplier.name

            # Check recipe match for finished products
            recipe_name = item.get("matched_recipe_name")
            if recipe_name:
                recipe = db.query(Recipe).filter(
                    Recipe.name.ilike(f"%{recipe_name[:30]}%")
                ).first()
                if recipe:
                    item["recipe_id"] = recipe.id

        return jsonify(parsed)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response: {e}")
        return jsonify({"error": "AI returned invalid format. Please try again."}), 500
    except Exception as e:
        logger.error(f"Error parsing inventory: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Inventory Comparison ────────────────────────────────────────────────────

@inventory_bp.route("/api/inventories/compare", methods=["GET"])
def compare_inventories():
    """Compare two inventories month-over-month."""
    db = Session()
    try:
        id1 = request.args.get("from_id", type=int)
        id2 = request.args.get("to_id", type=int)

        if not id1 or not id2:
            # Default: compare latest two months
            invs = db.query(Inventory).order_by(
                Inventory.year.desc(), Inventory.month.desc()
            ).limit(2).all()
            if len(invs) < 2:
                return jsonify({"error": "Need at least 2 inventories to compare"}), 400
            inv_to = invs[0]
            inv_from = invs[1]
        else:
            inv_from = db.query(Inventory).filter_by(id=id1).first()
            inv_to = db.query(Inventory).filter_by(id=id2).first()

        if not inv_from or not inv_to:
            return jsonify({"error": "Inventory not found"}), 404

        from_total = inv_from.total_value or 0
        to_total = inv_to.total_value or 0
        change = to_total - from_total
        change_pct = (change / from_total * 100) if from_total > 0 else 0

        # Compare by category
        from_cats = {}
        for item in inv_from.items:
            cat = item.category or "other"
            from_cats[cat] = from_cats.get(cat, 0) + (item.total_value or 0)

        to_cats = {}
        for item in inv_to.items:
            cat = item.category or "other"
            to_cats[cat] = to_cats.get(cat, 0) + (item.total_value or 0)

        all_cats = set(list(from_cats.keys()) + list(to_cats.keys()))
        category_comparison = []
        for cat in sorted(all_cats):
            f = from_cats.get(cat, 0)
            t = to_cats.get(cat, 0)
            c = t - f
            p = (c / f * 100) if f > 0 else 0
            category_comparison.append({
                "category": cat,
                "from_value": round(f, 2),
                "to_value": round(t, 2),
                "change": round(c, 2),
                "change_percent": round(p, 1)
            })

        return jsonify({
            "from": {
                "id": inv_from.id,
                "year": inv_from.year,
                "month": inv_from.month,
                "month_name": MONTH_NAMES_SV.get(inv_from.month),
                "total_value": round(from_total, 2)
            },
            "to": {
                "id": inv_to.id,
                "year": inv_to.year,
                "month": inv_to.month,
                "month_name": MONTH_NAMES_SV.get(inv_to.month),
                "total_value": round(to_total, 2)
            },
            "change": round(change, 2),
            "change_percent": round(change_pct, 1),
            "category_comparison": category_comparison
        })
    finally:
        db.close()


# ─── Inventory Years (for browse navigation) ────────────────────────────────

@inventory_bp.route("/api/inventories/years", methods=["GET"])
def inventory_years():
    """List years that have inventories."""
    db = Session()
    try:
        years = db.query(Inventory.year).distinct().order_by(Inventory.year.desc()).all()
        return jsonify([y[0] for y in years])
    finally:
        db.close()
