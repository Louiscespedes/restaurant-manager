"""
Inventory API routes — input, AI processing, review sessions, storage, and comparison.
Register with: app.register_blueprint(inventory_bp)

BATCH PROCESSING: The parse endpoint splits large inventories into
batches of ~30 items and processes them in parallel for speed.

REVIEW SESSIONS: After parsing, items needing clarification go through
an in-memory review flow (one question at a time) before final save.
"""
import logging
import json
import os
import uuid
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify
from datetime import datetime
from sqlalchemy import func
from models import (
    Session, Inventory, InventoryItem,
    Product, Recipe, RecipeIngredient, PriceHistory
)

logger = logging.getLogger(__name__)
inventory_bp = Blueprint('inventory', __name__)

# --- Helpers -----------------------------------------------------------------

MONTH_NAMES_SV = {
    1: "Januari", 2: "Februari", 3: "Mars", 4: "April",
    5: "Maj", 6: "Juni", 7: "Juli", 8: "Augusti",
    9: "September", 10: "Oktober", 11: "November", 12: "December"
}

def inventory_to_dict(inv, include_items=True):
    """Serialize an Inventory."""
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

# --- Batch parsing helpers ---------------------------------------------------

BATCH_SIZE = 30
MAX_PARALLEL = 5

def split_text_into_batches(raw_text, batch_size=BATCH_SIZE):
    """Split inventory text into batches of ~batch_size lines."""
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    if not lines:
        return [raw_text]
    batches = []
    for i in range(0, len(lines), batch_size):
        batch = '\n'.join(lines[i:i + batch_size])
        batches.append(batch)
    logger.info(f"Split {len(lines)} lines into {len(batches)} batches of ~{batch_size}")
    return batches

def parse_single_batch(batch_text, products_context, recipe_context, batch_num, total_batches):
    """Parse a single batch of inventory text using Claude."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""You are an inventory parser for a high-end restaurant.
Parse messy inventory text into structured data.
The text may be in French, English, or Swedish (or mixed).
The user likely wrote this quickly on their phone.
Be smart about typos, abbreviations, and casual notation (e.g., "morot 2kg" means 2 kg of carrots).

KNOWN PRODUCTS FROM INVOICES:
{products_context}

KNOWN RECIPES (for finished products):
{recipe_context}

INVENTORY TEXT (batch {batch_num}/{total_batches}):
{batch_text}

Return JSON:
{{
  "items": [
    {{
      "description": "product name in ENGLISH (translate Swedish names to English)",
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
      "clarification_type": "product_match, cut_variant, trimming_loss, recipe_cost, supplier_choice, unit_mismatch, or null",
      "clarification_question": "question for user, or null",
      "clarification_options": ["option1", "option2", "option3"] or null
    }}
  ]
}}

Rules:
- Match products across languages: morottor = carrots = carottes
- If unit is per-piece but price is per-kg, flag for clarification with type "unit_mismatch"
- If multiple suppliers sell same product, flag with type "supplier_choice" and list supplier names as options
- If a product name matches multiple items in the database, flag with type "product_match" and list the options
- If meat/fish terms are ambiguous (e.g. "beef" could be multiple cuts), flag with type "cut_variant"
- If user writes "trimmed" or indicates waste, flag with type "trimming_loss" and options ["10%", "15%", "20%", "25%", "Custom"]
- If item looks like a finished product (ice cream, bread, sauce, stock, dressing), set is_finished_product=true, check recipes, flag with type "recipe_cost"
- Categorize everything: fish, meat, vegetables, dairy, wine, spirits, cleaning, prepared, dry_goods, other
- Normalize units (always use kg for weight >500g, g for <500g, liter for liquids, st for pieces)
- BE AGGRESSIVE about flagging clarifications -- it's better to ask too many questions than too few
- If a product name PARTIALLY matches multiple known products, ALWAYS flag as product_match
- If the user wrote a generic term like "oil", "flour", "fish", "mushroom" etc, flag for clarification
- Prices from suppliers are ALWAYS per kg (weight) or per liter (volume) or per piece (st) -- never per gram or ml
- When setting unit_price, use the per-kg or per-liter price -- the server normalizes quantities automatically
- ALWAYS output the "description" field in ENGLISH (translate Swedish/other languages to English). Example: "Tomat grön" -> "Green tomato", "Morötter" -> "Carrots", "Lax" -> "Salmon". Keep matched_product_name in the ORIGINAL language (Swedish) for database matching.
- Return ONLY valid JSON, no markdown"""

    logger.info(f"Processing batch {batch_num}/{total_batches} ...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )

    result_text = response.content[0].text.strip()
    if result_text.startswith("```"):
        result_text = result_text.split("```")[1]
        if result_text.startswith("json"):
            result_text = result_text[4:]
        result_text = result_text.strip()

    parsed = json.loads(result_text)
    items = parsed.get("items", [])
    logger.info(f"Batch {batch_num}/{total_batches} returned {len(items)} items")
    return items


def _normalize_item_unit(item):
    """Normalize inventory item units to match how prices work.
    
    Prices from Fortnox are ALWAYS per kg for weight items, per liter for
    liquids, and per piece (st) for countable items. Never per gram.
    
    So if the AI parser returns 500g, we convert to 0.5 kg so that
    quantity * price_per_kg gives the correct value.
    """
    qty = item.get("quantity") or 0
    unit = (item.get("unit") or "").lower().strip()
    
    # Weight: always normalize to kg
    if unit == "g":
        item["quantity"] = round(qty / 1000, 4)
        item["unit"] = "kg"
    
    # Volume: always normalize to liter
    if unit == "ml":
        item["quantity"] = round(qty / 1000, 4)
        item["unit"] = "liter"
    if unit == "cl":
        item["quantity"] = round(qty / 100, 4)
        item["unit"] = "liter"
    
    # dl -> liter
    if unit == "dl":
        item["quantity"] = round(qty / 10, 4)
        item["unit"] = "liter"
    
    return item


# --- Review Session Storage (in-memory) --------------------------------------
# Sessions are transient -- they live between parse and confirm.
# Cleaned up after 2 hours automatically.

_review_sessions = {}
_sessions_lock = threading.Lock()
SESSION_TTL = 7200  # 2 hours

def _cleanup_old_sessions():
    """Remove sessions older than TTL."""
    now = time.time()
    with _sessions_lock:
        expired = [sid for sid, s in _review_sessions.items()
                   if now - s["created_at"] > SESSION_TTL]
        for sid in expired:
            del _review_sessions[sid]
            logger.info(f"Cleaned up expired review session {sid}")

def _create_review_session(items, year=None, month=None):
    """Create a review session from parsed items. Returns session_id."""
    session_id = str(uuid.uuid4())[:8]

    # Build questions from items that need clarification
    questions = []
    for idx, item in enumerate(items):
        if item.get("needs_clarification") and item.get("clarification_question"):
            q_type = item.get("clarification_type", "product_match")
            options = item.get("clarification_options") or []

            # Always add a skip option
            if "Skip" not in options and "skip" not in [o.lower() for o in options]:
                options.append("Skip")

            # Include item name in question for context
            item_name = item.get("description") or item.get("matched_product_name") or "Unknown item"
            question_text = item["clarification_question"]
            # Prefix with item name if not already mentioned
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

    with _sessions_lock:
        _review_sessions[session_id] = {
            "id": session_id,
            "items": items,
            "questions": questions,
            "year": year,
            "month": month,
            "status": "reviewing" if questions else "ready",
            "created_at": time.time()
        }

    logger.info(f"Created review session {session_id}: {len(items)} items, {len(questions)} questions")
    return session_id

def _get_session(session_id):
    """Get a session by ID. Returns None if not found."""
    _cleanup_old_sessions()
    with _sessions_lock:
        return _review_sessions.get(session_id)

def _apply_answer(session, question_id, answer):
    """Apply a user's answer to the corresponding item."""
    question = None
    for q in session["questions"]:
        if q["id"] == question_id:
            question = q
            break

    if not question:
        return False, "Question not found"

    question["answer"] = answer
    question["is_answered"] = True

    # Apply the answer to the item
    item_idx = question["item_index"]
    item = session["items"][item_idx]
    q_type = question["type"]

    if answer.lower() == "skip":
        item["needs_clarification"] = False
        return True, "Skipped"

    if q_type in ("product_match", "cut_variant"):
        # Clean answer: strip price/supplier info that may be in the option text
        # e.g. "Tomat grön - 100.29 SEK/kg (MENIGO FOODSERVICE AB)" -> "Tomat grön"
        clean_name = answer.split(" - ")[0].split(" (")[0].strip()
        # Set matched_product_name for DB lookup; keep AI's English description if available
        item["matched_product_name"] = clean_name
        if not item.get("description") or item["description"].lower() == clean_name.lower():
            item["description"] = clean_name  # No AI description, use product name
        item["needs_clarification"] = False

    elif q_type == "supplier_choice":
        item["supplier_name"] = answer
        item["needs_clarification"] = False

    elif q_type == "trimming_loss":
        try:
            pct_str = answer.replace("%", "").strip()
            pct = float(pct_str)
            if item.get("unit_price") and pct > 0 and pct < 100:
                item["trimming_loss_pct"] = pct
                item["original_price"] = item["unit_price"]
                item["unit_price"] = round(item["unit_price"] / (1 - pct / 100), 2)
        except (ValueError, TypeError):
            pass
        item["needs_clarification"] = False

    elif q_type == "recipe_cost":
        if answer.lower() in ("yes", "correct", "yes, correct"):
            item["needs_clarification"] = False
        else:
            try:
                new_val = float(answer.replace("kr", "").replace("SEK", "").strip())
                item["unit_price"] = new_val
            except (ValueError, TypeError):
                pass
            item["needs_clarification"] = False

    elif q_type == "unit_mismatch":
        item["needs_clarification"] = False

    else:
        item["needs_clarification"] = False

    # Check if all questions are answered
    all_answered = all(q["is_answered"] for q in session["questions"])
    if all_answered:
        session["status"] = "ready"

    return True, "Answer applied"


# --- Inventory CRUD ----------------------------------------------------------

@inventory_bp.route("/api/inventories", methods=["GET"])
def list_inventories():
    """List all inventories, optionally filtered by year and/or month.
    When month is specified, returns full item details for display."""
    db = Session()
    try:
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        query = db.query(Inventory).order_by(Inventory.year.desc(), Inventory.month.desc())
        if year:
            query = query.filter(Inventory.year == year)
        if month:
            query = query.filter(Inventory.month == month)
        inventories = query.all()
        include_items = month is not None
        return jsonify([inventory_to_dict(inv, include_items=include_items) for inv in inventories])
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
        action = data.get("action", "create")

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


# --- Inventory AI Parser (BATCH PROCESSING) ----------------------------------

@inventory_bp.route("/api/inventories/parse", methods=["POST"])
def parse_inventory_text():
    """
    Parse messy inventory text using AI with BATCH PROCESSING.
    Now returns a review session with clarification questions.
    """
    db = Session()
    try:
        data = request.get_json()
        raw_text = data.get("text", "").strip()
        year = data.get("year")
        month = data.get("month")

        if not raw_text:
            return jsonify({"error": "No inventory text provided"}), 400

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

        # Split into batches
        batches = split_text_into_batches(raw_text, BATCH_SIZE)
        total_batches = len(batches)
        logger.info(f"Processing inventory: {total_batches} batch(es)")

        all_items = []
        errors = []

        if total_batches == 1:
            try:
                items = parse_single_batch(
                    batches[0], products_context, recipe_context, 1, 1
                )
                all_items.extend(items)
            except Exception as e:
                logger.error(f"Error in single batch: {e}")
                errors.append(str(e))
        else:
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
                future_to_batch = {}
                for i, batch_text in enumerate(batches):
                    future = executor.submit(
                        parse_single_batch,
                        batch_text, products_context, recipe_context,
                        i + 1, total_batches
                    )
                    future_to_batch[future] = i

                batch_results = [None] * total_batches
                for future in as_completed(future_to_batch):
                    batch_idx = future_to_batch[future]
                    try:
                        items = future.result(timeout=120)
                        batch_results[batch_idx] = items
                    except Exception as e:
                        logger.error(f"Batch {batch_idx + 1} failed: {e}")
                        errors.append(f"Batch {batch_idx + 1}: {str(e)}")
                        batch_results[batch_idx] = []

                for items in batch_results:
                    if items:
                        all_items.extend(items)

        logger.info(f"Total parsed items: {len(all_items)}, errors: {len(errors)}")

        # Normalize units: g->kg, ml->liter (prices are always per kg/liter)
        for item in all_items:
            _normalize_item_unit(item)

        # Enrich with product IDs and prices (skip items needing clarification — they get re-enriched at confirm)
        for item in all_items:
            if item.get("needs_clarification"):
                continue  # Don't set prices for ambiguous items — wait for user to pick
            matched_name = item.get("matched_product_name") or item.get("description")
            if matched_name:
                # Try exact match first, then partial
                product = db.query(Product).filter(
                    func.lower(Product.name) == matched_name.lower().strip()
                ).first()
                if not product:
                    product = db.query(Product).filter(
                        Product.name.ilike(f"%{matched_name[:30]}%")
                    ).first()
                if product:
                    item["product_id"] = product.id
                    if product.current_price:
                        item["unit_price"] = product.current_price
                        item["price_found"] = True
                    if not item.get("supplier_name") and product.supplier:
                        item["supplier_name"] = product.supplier.name

            recipe_name = item.get("matched_recipe_name")
            if recipe_name:
                recipe = db.query(Recipe).filter(
                    Recipe.name.ilike(f"%{recipe_name[:30]}%")
                ).first()
                if recipe:
                    item["recipe_id"] = recipe.id

        # Create a review session
        session_id = _create_review_session(all_items, year=year, month=month)
        session = _get_session(session_id)

        total_questions = len(session["questions"])
        unanswered = sum(1 for q in session["questions"] if not q["is_answered"])

        result = {
            "session_id": session_id,
            "item_count": len(all_items),
            "items": all_items,
            "has_questions": total_questions > 0,
            "total_questions": total_questions,
            "unanswered_questions": unanswered,
            "status": session["status"]
        }
        if errors:
            result["warnings"] = errors
            result["message"] = f"Parsed {len(all_items)} items with {len(errors)} batch error(s)"

        return jsonify(result)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response: {e}")
        return jsonify({"error": "AI returned invalid format. Please try again."}), 500
    except Exception as e:
        logger.error(f"Error parsing inventory: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# --- Review Session Endpoints ------------------------------------------------

@inventory_bp.route("/api/inventory/review/<session_id>", methods=["GET"])
def get_review_status(session_id):
    """Get review session status and next unanswered question."""
    session = _get_session(session_id)
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
        "item_count": len(session["items"])
    }

    if session["status"] == "ready":
        result["items"] = session["items"]

    return jsonify(result)


@inventory_bp.route("/api/inventory/review/<session_id>/answer", methods=["POST"])
def submit_review_answer(session_id):
    """Submit an answer to a review question."""
    session = _get_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404

    data = request.get_json()
    question_id = data.get("question_id")
    answer = data.get("answer")

    if not question_id or not answer:
        return jsonify({"error": "question_id and answer are required"}), 400

    success, message = _apply_answer(session, question_id, answer)
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


@inventory_bp.route("/api/inventory/review/<session_id>/skip-all", methods=["POST"])
def skip_all_questions(session_id):
    """Skip all remaining unanswered questions."""
    session = _get_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404

    for q in session["questions"]:
        if not q["is_answered"]:
            q["answer"] = "Skip"
            q["is_answered"] = True
            item_idx = q["item_index"]
            session["items"][item_idx]["needs_clarification"] = False

    session["status"] = "ready"

    return jsonify({
        "success": True,
        "session_id": session_id,
        "status": "ready",
        "items": session["items"]
    })


@inventory_bp.route("/api/inventory/review/<session_id>/items", methods=["GET"])
def get_session_items(session_id):
    """Get current items in a review session (for the confirm/edit step)."""
    session = _get_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404

    return jsonify({
        "session_id": session_id,
        "status": session["status"],
        "items": session["items"],
        "item_count": len(session["items"]),
        "year": session.get("year"),
        "month": session.get("month")
    })


@inventory_bp.route("/api/inventory/review/<session_id>/items", methods=["PUT"])
def update_session_items(session_id):
    """Update items in a review session (user edits before confirming)."""
    session = _get_session(session_id)
    if not session:
        return jsonify({"error": "Review session not found or expired"}), 404

    data = request.get_json()
    updated_items = data.get("items")
    if updated_items is not None:
        session["items"] = updated_items

    return jsonify({
        "success": True,
        "session_id": session_id,
        "item_count": len(session["items"])
    })


@inventory_bp.route("/api/inventory/confirm/<session_id>", methods=["POST"])
def confirm_review_session(session_id):
    """
    Confirm a review session and save to database.
    Creates the Inventory + InventoryItems from the session data.
    """
    session_data = _get_session(session_id)
    if not session_data:
        return jsonify({"error": "Review session not found or expired"}), 404

    data = request.get_json() or {}
    year = data.get("year") or session_data.get("year") or datetime.utcnow().year
    month = data.get("month") or session_data.get("month") or datetime.utcnow().month
    action = data.get("action", "create")

    db = Session()
    try:
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

        # Re-enrich ALL items: aggressively look up products and prices
        for item_data in session_data["items"]:
            _normalize_item_unit(item_data)
            # Try matched_product_name first, then fall back to description
            search_name = item_data.get("matched_product_name") or item_data.get("description")
            if not search_name:
                continue
            # Try exact match first
            product = db.query(Product).filter(
                func.lower(Product.name) == search_name.lower().strip()
            ).first()
            # Fall back to ilike partial match
            if not product:
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{search_name[:30]}%")
                ).first()
            if product:
                item_data["product_id"] = product.id
                # Always set the price from the database (override AI-guessed prices)
                if product.current_price:
                    item_data["unit_price"] = product.current_price
                    item_data["price_found"] = True
                if product.supplier and not item_data.get("supplier_name"):
                    item_data["supplier_name"] = product.supplier.name

        total = 0
        for item_data in session_data["items"]:
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

        # Clean up session
        with _sessions_lock:
            if session_id in _review_sessions:
                del _review_sessions[session_id]

        inv = db.query(Inventory).filter_by(id=inv.id).first()
        return jsonify(inventory_to_dict(inv)), 201

    except Exception as e:
        db.rollback()
        logger.error(f"Error confirming inventory: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# --- Inventory Comparison ----------------------------------------------------

@inventory_bp.route("/api/inventories/compare", methods=["GET"])
def compare_inventories():
    """Compare two inventories month-over-month."""
    db = Session()
    try:
        id1 = request.args.get("from_id", type=int)
        id2 = request.args.get("to_id", type=int)

        if not id1 or not id2:
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
                "id": inv_from.id, "year": inv_from.year, "month": inv_from.month,
                "month_name": MONTH_NAMES_SV.get(inv_from.month),
                "total_value": round(from_total, 2)
            },
            "to": {
                "id": inv_to.id, "year": inv_to.year, "month": inv_to.month,
                "month_name": MONTH_NAMES_SV.get(inv_to.month),
                "total_value": round(to_total, 2)
            },
            "change": round(change, 2),
            "change_percent": round(change_pct, 1),
            "category_comparison": category_comparison
        })
    finally:
        db.close()


# --- Inventory Years (for browse navigation) ---------------------------------

@inventory_bp.route("/api/inventories/years", methods=["GET"])
def inventory_years():
    """List years that have inventories."""
    db = Session()
    try:
        years = db.query(Inventory.year).distinct().order_by(Inventory.year.desc()).all()
        return jsonify([y[0] for y in years])
    finally:
        db.close()
