"""
Inventory API endpoints — browse, create, edit, review, confirm.
"""
import json
from flask import Blueprint, jsonify, request
from datetime import datetime
from models import (
    Session, InventorySession, InventoryItem, ReviewQuestion,
    Product, Supplier, Recipe, RecipeIngredient, RecipeReviewQuestion,
    Dish, DishRecipe, DishIngredient,
    Menu, MenuSection, MenuSectionItem
)
from ai_parser import parse_inventory_with_ai, generate_smart_questions, parse_recipe_text_with_ai, match_recipe_prices_with_ai

inventory_bp = Blueprint('inventory', __name__)


# ── Browse Inventories ─────────────────────────────────────────────────

@inventory_bp.route('/api/inventory/years', methods=['GET'])
def get_inventory_years():
    """Get all years that have inventory data."""
    db = Session()
    try:
        years = db.query(InventorySession.year).filter_by(
            status='confirmed'
        ).distinct().order_by(InventorySession.year.desc()).all()
        return jsonify([y[0] for y in years])
    finally:
        db.close()


@inventory_bp.route('/api/inventory/<int:year>', methods=['GET'])
def get_inventory_months(year):
    """Get all months with inventory for a given year."""
    db = Session()
    try:
        sessions = db.query(InventorySession).filter_by(
            year=year, status='confirmed'
        ).order_by(InventorySession.month).all()
        return jsonify([{
            'month': s.month,
            'total_value': s.total_value,
            'item_count': len(s.items),
            'confirmed_at': s.confirmed_at.isoformat() if s.confirmed_at else None
        } for s in sessions])
    finally:
        db.close()


@inventory_bp.route('/api/inventory/<int:year>/<int:month>', methods=['GET'])
def get_inventory(year, month):
    """Get full inventory for a specific month."""
    db = Session()
    try:
        session = db.query(InventorySession).filter_by(
            year=year, month=month, status='confirmed'
        ).first()

        if not session:
            return jsonify({'error': 'No inventory found for this month', 'items': []}), 404

        # Optional filters
        category = request.args.get('category')
        supplier = request.args.get('supplier')
        search = request.args.get('search')

        items_query = db.query(InventoryItem).filter_by(session_id=session.id)

        if category:
            items_query = items_query.filter(InventoryItem.category == category)
        if supplier:
            items_query = items_query.filter(InventoryItem.supplier_name.ilike(f'%{supplier}%'))
        if search:
            items_query = items_query.filter(InventoryItem.name.ilike(f'%{search}%'))

        items = items_query.order_by(InventoryItem.category, InventoryItem.name).all()

        return jsonify({
            'session_id': session.id,
            'year': session.year,
            'month': session.month,
            'status': session.status,
            'total_value': session.total_value,
            'confirmed_at': session.confirmed_at.isoformat() if session.confirmed_at else None,
            'items': [{
                'id': item.id,
                'name': item.name,
                'category': item.category,
                'supplier_name': item.supplier_name,
                'quantity': item.quantity,
                'unit': item.unit,
                'price_per_unit': item.price_per_unit,
                'trimming_loss_pct': item.trimming_loss_pct,
                'adjusted_price': item.adjusted_price,
                'is_recipe_product': item.is_recipe_product,
                'value': item.value,
                'notes': item.notes
            } for item in items]
        })
    finally:
        db.close()


@inventory_bp.route('/api/inventory/categories', methods=['GET'])
def get_categories():
    """Get all product categories."""
    return jsonify([
        'Meat', 'Fish & Seafood', 'Dairy', 'Produce / Vegetables',
        'Fruit', 'Dry Goods', 'Oils & Condiments', 'Beverages',
        'Frozen', 'Bakery', 'Finished Products', 'Other'
    ])


@inventory_bp.route('/api/inventory/session/<int:session_id>', methods=['GET'])
def get_inventory_session(session_id):
    """Get a session with all its items — used by confirm page."""
    db = Session()
    try:
        session = db.query(InventorySession).get(session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404

        return jsonify({
            'session_id': session.id,
            'year': session.year,
            'month': session.month,
            'status': session.status,
            'total_value': session.total_value,
            'raw_input': session.raw_input,
            'items': [{
                'id': item.id,
                'name': item.name,
                'category': item.category,
                'supplier_name': item.supplier_name,
                'quantity': item.quantity,
                'unit': item.unit,
                'price_per_unit': item.price_per_unit,
                'trimming_loss_pct': item.trimming_loss_pct,
                'adjusted_price': item.adjusted_price,
                'is_recipe_product': item.is_recipe_product,
                'value': item.value,
                'notes': item.notes
            } for item in session.items]
        })
    finally:
        db.close()


# ── Edit Inventory Item ────────────────────────────────────────────────

@inventory_bp.route('/api/inventory/item/<int:item_id>', methods=['PUT'])
def update_inventory_item(item_id):
    """Edit a single inventory item."""
    db = Session()
    try:
        item = db.query(InventoryItem).get(item_id)
        if not item:
            return jsonify({'error': 'Item not found'}), 404

        data = request.json
        if 'name' in data:
            item.name = data['name']
        if 'category' in data:
            item.category = data['category']
        if 'supplier_name' in data:
            item.supplier_name = data['supplier_name']
        if 'quantity' in data:
            item.quantity = data['quantity']
        if 'unit' in data:
            item.unit = data['unit']
        if 'price_per_unit' in data:
            item.price_per_unit = data['price_per_unit']
        if 'trimming_loss_pct' in data:
            item.trimming_loss_pct = data['trimming_loss_pct']
        if 'notes' in data:
            item.notes = data['notes']

        # Recalculate adjusted price and value
        if item.price_per_unit and item.trimming_loss_pct:
            item.adjusted_price = item.price_per_unit / (1 - item.trimming_loss_pct / 100)
        else:
            item.adjusted_price = item.price_per_unit

        price = item.adjusted_price or item.price_per_unit or 0
        item.value = price * (item.quantity or 0)

        db.commit()

        # Recalculate session total
        session = db.query(InventorySession).get(item.session_id)
        if session:
            total = sum(i.value or 0 for i in session.items)
            session.total_value = round(total, 2)
            db.commit()

        return jsonify({'status': 'updated', 'item_id': item.id, 'new_value': item.value})
    finally:
        db.close()


# ── Process Text / Voice Input ─────────────────────────────────────────

@inventory_bp.route('/api/inventory/process-text', methods=['POST'])
def process_inventory_text():
    """
    Receive raw messy text (from paste or voice transcript).
    Parse it into structured inventory items.
    Returns a session_id for the review process.
    """
    db = Session()
    try:
        data = request.json
        raw_text = data.get('text', '')
        year = data.get('year', datetime.utcnow().year)
        month = data.get('month', datetime.utcnow().month)

        if not raw_text.strip():
            return jsonify({'error': 'No text provided'}), 400

        # Create inventory session
        session = InventorySession(
            year=year,
            month=month,
            status='draft',
            raw_input=raw_text
        )
        db.add(session)
        db.flush()

        # Try AI parsing first, fall back to regex
        ai_result = parse_inventory_with_ai(raw_text, db)

        if ai_result and 'items' in ai_result:
            # AI parsed successfully — convert to InventoryItem objects
            items = _create_items_from_ai(ai_result['items'], session.id, db)
        else:
            # Fallback to regex parser
            items = _parse_inventory_text(raw_text, session.id, db)

        for item in items:
            db.add(item)
        db.flush()  # Get item IDs before generating questions

        # Generate review questions (AI-powered if available)
        if ai_result and 'items' in ai_result:
            ai_questions = generate_smart_questions(ai_result['items'], None, db)
            if ai_questions and 'questions' in ai_questions:
                questions = _create_questions_from_ai(ai_questions['questions'], items, session.id)
            else:
                questions = _generate_review_questions(items, session.id, db)
        else:
            questions = _generate_review_questions(items, session.id, db)

        for q in questions:
            db.add(q)

        session.status = 'reviewing'
        db.commit()

        return jsonify({
            'session_id': session.id,
            'items_parsed': len(items),
            'questions_count': len(questions),
            'status': 'reviewing'
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


def _create_items_from_ai(ai_items, session_id, db):
    """Convert AI-parsed items into InventoryItem objects."""
    items = []
    for ai_item in ai_items:
        item = InventoryItem(
            session_id=session_id,
            raw_name=ai_item.get('raw_input', ''),
            name=ai_item.get('name', ai_item.get('raw_input', 'Unknown')),
            quantity=ai_item.get('quantity'),
            unit=ai_item.get('unit'),
            price_per_unit=ai_item.get('price_per_unit'),
            category=ai_item.get('category'),
            supplier_name=ai_item.get('supplier_name'),
            notes=ai_item.get('notes')
        )

        # Link to matched product if AI found one
        matched_id = ai_item.get('matched_product_id')
        if matched_id:
            product = db.query(Product).get(matched_id)
            if product:
                item.product_id = product.id
                if not item.price_per_unit and product.current_price:
                    item.price_per_unit = product.current_price

        # Calculate value
        if item.price_per_unit and item.quantity:
            item.value = round(item.price_per_unit * item.quantity, 2)

        items.append(item)
    return items


def _create_questions_from_ai(ai_questions, items, session_id):
    """Convert AI-generated questions into ReviewQuestion objects."""
    questions = []
    for i, aq in enumerate(ai_questions):
        item_index = aq.get('item_index', 0)
        item = items[item_index] if item_index < len(items) else None

        q = ReviewQuestion(
            session_id=session_id,
            item_id=item.id if item else None,
            question_type=aq.get('question_type', 'missing_price'),
            question_text=aq.get('question_text', ''),
            options=json.dumps(aq.get('options', [])),
            order=i
        )
        questions.append(q)
    return questions


def _parse_inventory_text(raw_text, session_id, db):
    """
    Parse messy inventory text into structured items.
    Handles formats like:
    - "10kg salmon 450kr/kg"
    - "carrots 25kg menigo"
    - "3 bottles olive oil"
    - "wagyu 2.5kg"
    """
    import re
    items = []
    lines = raw_text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 2:
            continue

        # Clean up common separators
        line = line.replace(',', '\n').replace(';', '\n')
        sub_items = line.split('\n')

        for sub in sub_items:
            sub = sub.strip()
            if not sub or len(sub) < 2:
                continue

            item = InventoryItem(
                session_id=session_id,
                raw_name=sub,
                name=sub,  # Will be refined during review
                quantity=None,
                unit=None,
                price_per_unit=None
            )

            # Try to extract quantity + unit (e.g. "10kg", "3 bottles", "25 kg")
            qty_match = re.search(r'(\d+[.,]?\d*)\s*(kg|g|st|liter|l|flaskor?|bottles?|burkar?|paket|pkt|förp)', sub, re.IGNORECASE)
            if qty_match:
                item.quantity = float(qty_match.group(1).replace(',', '.'))
                unit_raw = qty_match.group(2).lower()
                unit_map = {
                    'kg': 'kg', 'g': 'g', 'st': 'st', 'liter': 'liter', 'l': 'liter',
                    'flaska': 'st', 'flaskor': 'st', 'bottle': 'st', 'bottles': 'st',
                    'burk': 'st', 'burkar': 'st', 'paket': 'st', 'pkt': 'st', 'förp': 'st'
                }
                item.unit = unit_map.get(unit_raw, unit_raw)

            # Try to extract price (e.g. "450kr/kg", "200kr", "150 kr")
            price_match = re.search(r'(\d+[.,]?\d*)\s*kr', sub, re.IGNORECASE)
            if price_match:
                item.price_per_unit = float(price_match.group(1).replace(',', '.'))

            # Try to match to existing products
            # Clean the name for matching (remove qty, price, etc.)
            clean_name = re.sub(r'\d+[.,]?\d*\s*(kg|g|st|liter|l|kr|sek|flaskor?|bottles?)\S*', '', sub, flags=re.IGNORECASE).strip()
            clean_name = re.sub(r'\s+', ' ', clean_name).strip(' ,-/')
            if clean_name:
                item.name = clean_name

            # Try matching to known products
            matches = db.query(Product).filter(
                Product.name.ilike(f'%{clean_name}%')
            ).all()
            if len(matches) == 1:
                item.product_id = matches[0].id
                item.category = matches[0].category
                item.supplier_name = matches[0].supplier.name if matches[0].supplier else None
                if not item.price_per_unit and matches[0].current_price:
                    item.price_per_unit = matches[0].current_price

            # Calculate value if we have price and quantity
            if item.price_per_unit and item.quantity:
                item.value = round(item.price_per_unit * item.quantity, 2)

            items.append(item)

    return items


def _generate_review_questions(items, session_id, db):
    """Generate AI review questions for ambiguous items."""
    questions = []
    order = 0

    for item in items:
        # 1. Product disambiguation — multiple matches
        if item.name:
            matches = db.query(Product).filter(
                Product.name.ilike(f'%{item.name}%')
            ).all()

            if len(matches) > 1:
                options = []
                for m in matches:
                    label = m.name
                    if m.supplier:
                        label += f' — {m.supplier.name}'
                    options.append({'id': m.id, 'label': label})

                q = ReviewQuestion(
                    session_id=session_id,
                    item_id=item.id if item.id else None,
                    question_type='product_match',
                    question_text=f'I found multiple products matching "{item.name}". Which one did you mean?',
                    options=json.dumps(options),
                    order=order
                )
                questions.append(q)
                order += 1

        # 2. Trimming loss — keywords
        raw = (item.raw_name or '').lower()
        if any(word in raw for word in ['trimmed', 'rensad', 'filead', 'putsad', 'skuren']):
            q = ReviewQuestion(
                session_id=session_id,
                item_id=item.id if item.id else None,
                question_type='trimming_loss',
                question_text=f'You noted "{item.raw_name}" which sounds trimmed. What\'s the trimming loss percentage?',
                options=json.dumps([
                    {'value': 10, 'label': '10%'},
                    {'value': 15, 'label': '15%'},
                    {'value': 20, 'label': '20%'},
                    {'value': 25, 'label': '25%'},
                    {'value': 30, 'label': '30%'},
                ]),
                order=order
            )
            questions.append(q)
            order += 1

        # 3. Recipe product — keywords
        if any(word in raw for word in ['finished', 'färdig', 'prepared', 'homemade', 'hemlagad']):
            # Check if we have a matching recipe
            recipe_matches = db.query(Recipe).filter(
                Recipe.name.ilike(f'%{item.name}%')
            ).all()

            if recipe_matches:
                recipe = recipe_matches[0]
                q = ReviewQuestion(
                    session_id=session_id,
                    item_id=item.id if item.id else None,
                    question_type='recipe_cost',
                    question_text=f'"{item.name}" looks like a finished product. I found recipe "{recipe.name}" '
                                  f'(total cost {recipe.total_cost} SEK for {recipe.total_yield} {recipe.yield_unit}). '
                                  f'Should I use this recipe for costing?',
                    options=json.dumps([
                        {'value': 'yes', 'label': f'Yes, use recipe ({recipe.cost_per_unit} SEK/{recipe.yield_unit})', 'recipe_id': recipe.id},
                        {'value': 'no', 'label': 'No, use manual price'},
                    ]),
                    order=order
                )
                questions.append(q)
                order += 1

        # 4. Missing price
        if not item.price_per_unit and item.quantity:
            q = ReviewQuestion(
                session_id=session_id,
                item_id=item.id if item.id else None,
                question_type='missing_price',
                question_text=f'I couldn\'t find a price for "{item.name}". What\'s the price per {item.unit or "unit"}?',
                options=json.dumps([]),  # Free text input
                order=order
            )
            questions.append(q)
            order += 1

    return questions


# ── Review Questions ───────────────────────────────────────────────────

@inventory_bp.route('/api/inventory/review/<int:session_id>', methods=['GET'])
def get_review_questions(session_id):
    """Get all review questions for a session."""
    db = Session()
    try:
        session = db.query(InventorySession).get(session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404

        questions = db.query(ReviewQuestion).filter_by(
            session_id=session_id
        ).order_by(ReviewQuestion.order).all()

        answered = sum(1 for q in questions if q.is_answered)

        return jsonify({
            'session_id': session_id,
            'total_questions': len(questions),
            'answered': answered,
            'questions': [{
                'id': q.id,
                'type': q.question_type,
                'question': q.question_text,
                'options': json.loads(q.options) if q.options else [],
                'answer': q.answer,
                'is_answered': q.is_answered,
                'order': q.order
            } for q in questions]
        })
    finally:
        db.close()


@inventory_bp.route('/api/inventory/review/<int:session_id>/answer', methods=['POST'])
def answer_review_question(session_id):
    """Submit an answer to a review question."""
    db = Session()
    try:
        data = request.json
        question_id = data.get('question_id')
        answer = data.get('answer')

        question = db.query(ReviewQuestion).get(question_id)
        if not question or question.session_id != session_id:
            return jsonify({'error': 'Question not found'}), 404

        question.answer = json.dumps(answer) if isinstance(answer, dict) else str(answer)
        question.is_answered = True

        # Apply the answer to the inventory item
        item = db.query(InventoryItem).get(question.item_id) if question.item_id else None

        if item and question.question_type == 'product_match':
            # Link to selected product — answer can be product id OR label text
            product = None
            try:
                product = db.query(Product).get(int(answer))
            except (ValueError, TypeError):
                # Answer is a label string — look it up from the question options
                try:
                    opts = json.loads(question.options) if question.options else []
                    for opt in opts:
                        if str(opt.get('label', '')).lower() == str(answer).lower() or str(opt.get('id', '')) == str(answer):
                            product = db.query(Product).get(opt['id'])
                            break
                    if not product:
                        # Fallback: search by name
                        product = db.query(Product).filter(Product.name.ilike(f'%{answer}%')).first()
                except Exception:
                    pass
            if product:
                item.product_id = product.id
                item.name = product.name
                item.category = product.category
                item.supplier_name = product.supplier.name if product.supplier else None
                if not item.price_per_unit and product.current_price:
                    item.price_per_unit = product.current_price

        elif item and question.question_type == 'trimming_loss':
            item.trimming_loss_pct = float(answer)
            if item.price_per_unit:
                item.adjusted_price = round(item.price_per_unit / (1 - float(answer) / 100), 2)
                item.value = round(item.adjusted_price * (item.quantity or 0), 2)

        elif item and question.question_type == 'recipe_cost':
            if isinstance(answer, str) and answer == 'yes' or (isinstance(answer, dict) and answer.get('value') == 'yes'):
                item.is_recipe_product = True
                # Find recipe and apply costing
                options = json.loads(question.options)
                for opt in options:
                    if opt.get('recipe_id'):
                        recipe = db.query(Recipe).get(opt['recipe_id'])
                        if recipe:
                            item.recipe_id = recipe.id
                            item.price_per_unit = recipe.cost_per_unit
                            item.value = round(recipe.cost_per_unit * (item.quantity or 0), 2)
                        break

        elif item and question.question_type == 'missing_price':
            item.price_per_unit = float(answer)
            item.value = round(float(answer) * (item.quantity or 0), 2)

        db.commit()

        # Check if all questions answered
        remaining = db.query(ReviewQuestion).filter_by(
            session_id=session_id, is_answered=False
        ).count()

        return jsonify({
            'status': 'answered',
            'remaining_questions': remaining,
            'all_answered': remaining == 0
        })
    finally:
        db.close()


# ── Confirm Inventory ──────────────────────────────────────────────────

@inventory_bp.route('/api/inventory/confirm/<int:session_id>', methods=['POST'])
def confirm_inventory(session_id):
    """Confirm and finalize an inventory session."""
    db = Session()
    try:
        session = db.query(InventorySession).get(session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404

        # Calculate final values for all items
        for item in session.items:
            if item.price_per_unit and item.quantity:
                if item.trimming_loss_pct and item.trimming_loss_pct > 0:
                    item.adjusted_price = round(item.price_per_unit / (1 - item.trimming_loss_pct / 100), 2)
                    item.value = round(item.adjusted_price * item.quantity, 2)
                else:
                    item.adjusted_price = item.price_per_unit
                    item.value = round(item.price_per_unit * item.quantity, 2)

        # Calculate total
        total = sum(item.value or 0 for item in session.items)
        session.total_value = round(total, 2)
        session.status = 'confirmed'
        session.confirmed_at = datetime.utcnow()

        db.commit()

        return jsonify({
            'status': 'confirmed',
            'session_id': session.id,
            'total_value': session.total_value,
            'items_count': len(session.items),
            'confirmed_at': session.confirmed_at.isoformat()
        })
    finally:
        db.close()


# ── Recipe Text Parsing ────────────────────────────────────────────────

@inventory_bp.route('/api/recipes/parse-text', methods=['POST'])
def parse_recipe_text():
    """
    Receive messy recipe text from chef, parse with AI into structured recipe.
    Separates ingredients from cooking instructions.
    """
    db = Session()
    try:
        data = request.json
        raw_text = data.get('text', '')

        if not raw_text.strip():
            return jsonify({'error': 'No text provided'}), 400

        result = parse_recipe_text_with_ai(raw_text, db)

        if not result:
            return jsonify({'error': 'Failed to parse recipe text'}), 500

        # Try to match ingredients to known products and get prices
        for ing in result.get('ingredients', []):
            if ing.get('name'):
                matches = db.query(Product).filter(
                    Product.name.ilike(f'%{ing["name"]}%')
                ).all()
                if len(matches) == 1:
                    ing['matched_product_id'] = matches[0].id
                    if matches[0].current_price:
                        ing['cost_per_unit'] = matches[0].current_price
                    if matches[0].unit:
                        ing['product_unit'] = matches[0].unit

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# ── Recipes ────────────────────────────────────────────────────────────

@inventory_bp.route('/api/recipes', methods=['GET'])
def get_recipes():
    """Get all recipes."""
    db = Session()
    try:
        recipes = db.query(Recipe).order_by(Recipe.name).all()
        return jsonify([{
            'id': r.id,
            'name': r.name,
            'added_by': r.added_by,
            'total_yield': r.total_yield,
            'yield_unit': r.yield_unit,
            'total_cost': r.total_cost,
            'cost_per_unit': r.cost_per_unit,
            'seasoning_pct': r.seasoning_pct,
            'selling_price': r.selling_price,
            'food_cost_pct': r.food_cost_pct,
            'price_review_status': r.price_review_status,
            'photos': json.loads(r.photos) if r.photos else [],
            'ingredient_count': len(r.ingredients),
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None
        } for r in recipes])
    finally:
        db.close()


@inventory_bp.route('/api/recipes/<int:recipe_id>', methods=['GET'])
def get_recipe_detail(recipe_id):
    """Get recipe with all ingredients."""
    db = Session()
    try:
        recipe = db.query(Recipe).get(recipe_id)
        if not recipe:
            return jsonify({'error': 'Recipe not found'}), 404

        return jsonify({
            'id': recipe.id,
            'name': recipe.name,
            'added_by': recipe.added_by,
            'total_yield': recipe.total_yield,
            'yield_unit': recipe.yield_unit,
            'total_cost': recipe.total_cost,
            'cost_per_unit': recipe.cost_per_unit,
            'seasoning_pct': recipe.seasoning_pct,
            'notes': recipe.notes,
            'photos': json.loads(recipe.photos) if recipe.photos else [],
            'created_at': recipe.created_at.isoformat() if recipe.created_at else None,
            'updated_at': recipe.updated_at.isoformat() if recipe.updated_at else None,
            'selling_price': recipe.selling_price,
            'food_cost_pct': recipe.food_cost_pct,
            'price_review_status': recipe.price_review_status,
            'ingredients': [{
                'id': ing.id,
                'name': ing.name,
                'quantity': ing.quantity,
                'unit': ing.unit,
                'price_per_unit': ing.price_per_unit,
                'price_unit': ing.price_unit,
                'price_source': ing.price_source,
                'cost': ing.cost,
                'trimming_pct': ing.trimming_pct,
                'adjusted_cost': ing.adjusted_cost,
                'needs_review': ing.needs_review,
                'notes': ing.notes
            } for ing in recipe.ingredients]
        })
    finally:
        db.close()


@inventory_bp.route('/api/recipes', methods=['POST'])
def create_recipe():
    """Create a new recipe with ingredients."""
    db = Session()
    try:
        data = request.json
        photos = data.get('photos', [])
        recipe = Recipe(
            name=data.get('name', ''),
            added_by=data.get('added_by'),
            total_yield=data.get('total_yield'),
            yield_unit=data.get('yield_unit', 'portions'),
            seasoning_pct=data.get('seasoning_pct', 0) or 0,
            notes=data.get('notes'),
            photos=json.dumps(photos) if photos else None
        )
        db.add(recipe)
        db.flush()

        ingredients_total = 0
        for ing_data in data.get('ingredients', []):
            cost = ing_data.get('cost', 0) or 0
            trimming_pct = ing_data.get('trimming_pct', 0) or 0

            # Calculate adjusted cost with trimming
            if trimming_pct > 0 and cost > 0:
                adjusted_cost = round(cost / (1 - trimming_pct / 100), 2)
            else:
                adjusted_cost = cost

            ing = RecipeIngredient(
                recipe_id=recipe.id,
                product_id=ing_data.get('product_id'),
                name=ing_data.get('name', ''),
                quantity=ing_data.get('quantity'),
                unit=ing_data.get('unit'),
                cost=cost,
                trimming_pct=trimming_pct,
                adjusted_cost=adjusted_cost,
                notes=ing_data.get('notes')
            )
            db.add(ing)
            ingredients_total += adjusted_cost

        # Apply seasoning cost
        seasoning_cost = round(ingredients_total * (recipe.seasoning_pct / 100), 2)
        recipe.total_cost = round(ingredients_total + seasoning_cost, 2)
        if recipe.total_yield and recipe.total_yield > 0:
            recipe.cost_per_unit = round(recipe.total_cost / recipe.total_yield, 2)

        db.commit()
        return jsonify({
            'id': recipe.id,
            'name': recipe.name,
            'total_cost': recipe.total_cost,
            'cost_per_unit': recipe.cost_per_unit
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/recipes/<int:recipe_id>', methods=['PUT'])
def update_recipe(recipe_id):
    """Update a recipe and its ingredients."""
    db = Session()
    try:
        recipe = db.query(Recipe).get(recipe_id)
        if not recipe:
            return jsonify({'error': 'Recipe not found'}), 404

        data = request.json
        if 'name' in data:
            recipe.name = data['name']
        if 'added_by' in data:
            recipe.added_by = data['added_by']
        if 'total_yield' in data:
            recipe.total_yield = data['total_yield']
        if 'yield_unit' in data:
            recipe.yield_unit = data['yield_unit']
        if 'seasoning_pct' in data:
            recipe.seasoning_pct = data['seasoning_pct']
        if 'notes' in data:
            recipe.notes = data['notes']
        if 'photos' in data:
            recipe.photos = json.dumps(data['photos']) if data['photos'] else None

        # Replace ingredients if provided
        if 'ingredients' in data:
            # Remove old ingredients
            for old_ing in recipe.ingredients:
                db.delete(old_ing)

            ingredients_total = 0
            for ing_data in data['ingredients']:
                cost = ing_data.get('cost', 0) or 0
                trimming_pct = ing_data.get('trimming_pct', 0) or 0

                if trimming_pct > 0 and cost > 0:
                    adjusted_cost = round(cost / (1 - trimming_pct / 100), 2)
                else:
                    adjusted_cost = cost

                ing = RecipeIngredient(
                    recipe_id=recipe.id,
                    product_id=ing_data.get('product_id'),
                    name=ing_data.get('name', ''),
                    quantity=ing_data.get('quantity'),
                    unit=ing_data.get('unit'),
                    cost=cost,
                    trimming_pct=trimming_pct,
                    adjusted_cost=adjusted_cost,
                    notes=ing_data.get('notes')
                )
                db.add(ing)
                ingredients_total += adjusted_cost

            seasoning_pct = recipe.seasoning_pct or 0
            seasoning_cost = round(ingredients_total * (seasoning_pct / 100), 2)
            recipe.total_cost = round(ingredients_total + seasoning_cost, 2)
            if recipe.total_yield and recipe.total_yield > 0:
                recipe.cost_per_unit = round(recipe.total_cost / recipe.total_yield, 2)

        db.commit()
        return jsonify({'status': 'updated', 'id': recipe.id})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/recipes/<int:recipe_id>', methods=['DELETE'])
def delete_recipe(recipe_id):
    """Delete a recipe."""
    db = Session()
    try:
        recipe = db.query(Recipe).get(recipe_id)
        if not recipe:
            return jsonify({'error': 'Recipe not found'}), 404
        db.delete(recipe)
        db.commit()
        return jsonify({'status': 'deleted'})
    finally:
        db.close()


# ── Recipe Price Review ────────────────────────────────────────────────

@inventory_bp.route('/api/recipes/<int:recipe_id>/match-prices', methods=['POST'])
def match_recipe_prices(recipe_id):
    """
    After a recipe is created/parsed, run AI price matching on its ingredients.
    Cross-references with Products table, generates review questions for ambiguous cases.
    """
    db = Session()
    try:
        recipe = db.query(Recipe).get(recipe_id)
        if not recipe:
            return jsonify({'error': 'Recipe not found'}), 404

        # Build ingredient list for AI matching
        ingredients_for_ai = []
        for ing in recipe.ingredients:
            ingredients_for_ai.append({
                'name': ing.name,
                'quantity': ing.quantity,
                'unit': ing.unit,
                'notes': ing.notes,
            })

        if not ingredients_for_ai:
            return jsonify({'error': 'Recipe has no ingredients'}), 400

        # Run AI price matching
        result = match_recipe_prices_with_ai(ingredients_for_ai, db)
        if not result:
            return jsonify({'error': 'Price matching failed'}), 500

        # Clear old review questions for this recipe
        db.query(RecipeReviewQuestion).filter_by(recipe_id=recipe_id).delete()

        # Process matches — auto-assign confident ones, flag the rest
        ingredients_list = list(recipe.ingredients)
        matched_count = 0
        question_count = 0

        for match in result.get('matches', []):
            idx = match.get('ingredient_index', 0)
            if idx >= len(ingredients_list):
                continue
            ing = ingredients_list[idx]

            if match.get('status') == 'matched' and match.get('product_id'):
                # Auto-assign: confident match found
                ing.product_id = match['product_id']
                ing.price_per_unit = match.get('price_per_unit')
                ing.price_unit = match.get('price_unit')
                ing.price_source = 'invoice'
                ing.needs_review = False

                # Calculate cost with unit conversion
                calc_cost = match.get('calculated_cost')
                if calc_cost:
                    ing.cost = round(calc_cost, 2)
                elif ing.price_per_unit and ing.quantity:
                    ing.cost = round(ing.price_per_unit * ing.quantity, 2)

                # Apply trimming
                if ing.trimming_pct and ing.trimming_pct > 0 and ing.cost:
                    ing.adjusted_cost = round(ing.cost / (1 - ing.trimming_pct / 100), 2)
                else:
                    ing.adjusted_cost = ing.cost

                matched_count += 1
            else:
                # Needs review: multiple matches, no match, or unit mismatch
                ing.needs_review = True

        # Create review questions
        for q_data in result.get('questions', []):
            idx = q_data.get('ingredient_index', 0)
            ing_id = ingredients_list[idx].id if idx < len(ingredients_list) else None

            q = RecipeReviewQuestion(
                recipe_id=recipe_id,
                ingredient_id=ing_id,
                question_type=q_data.get('question_type', 'missing_price'),
                question_text=q_data.get('question_text', ''),
                options=json.dumps(q_data.get('options', []), ensure_ascii=False),
                order=question_count,
            )
            db.add(q)
            question_count += 1

        # Update recipe status
        recipe.price_review_status = 'reviewing' if question_count > 0 else 'completed'

        # Recalculate recipe total cost from matched ingredients
        _recalc_recipe_cost(recipe)

        db.commit()

        return jsonify({
            'recipe_id': recipe.id,
            'matched': matched_count,
            'needs_review': question_count,
            'total_ingredients': len(ingredients_list),
            'review_status': recipe.price_review_status,
            'total_cost': recipe.total_cost,
            'cost_per_unit': recipe.cost_per_unit,
        })

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/recipes/<int:recipe_id>/review', methods=['GET'])
def get_recipe_review(recipe_id):
    """Get all review questions for a recipe's pricing."""
    db = Session()
    try:
        recipe = db.query(Recipe).get(recipe_id)
        if not recipe:
            return jsonify({'error': 'Recipe not found'}), 404

        questions = db.query(RecipeReviewQuestion).filter_by(
            recipe_id=recipe_id
        ).order_by(RecipeReviewQuestion.order).all()

        # Also return ingredients with their current pricing status
        ingredients_status = []
        for ing in recipe.ingredients:
            ingredients_status.append({
                'id': ing.id,
                'name': ing.name,
                'quantity': ing.quantity,
                'unit': ing.unit,
                'product_id': ing.product_id,
                'price_per_unit': ing.price_per_unit,
                'price_unit': ing.price_unit,
                'price_source': ing.price_source,
                'cost': ing.cost,
                'adjusted_cost': ing.adjusted_cost,
                'needs_review': ing.needs_review,
                'trimming_pct': ing.trimming_pct,
            })

        return jsonify({
            'recipe_id': recipe.id,
            'recipe_name': recipe.name,
            'review_status': recipe.price_review_status,
            'total_cost': recipe.total_cost,
            'cost_per_unit': recipe.cost_per_unit,
            'ingredients': ingredients_status,
            'questions': [{
                'id': q.id,
                'ingredient_id': q.ingredient_id,
                'question_type': q.question_type,
                'question_text': q.question_text,
                'options': json.loads(q.options) if q.options else [],
                'answer': q.answer,
                'is_answered': q.is_answered,
            } for q in questions],
        })
    finally:
        db.close()


@inventory_bp.route('/api/recipes/<int:recipe_id>/review/answer', methods=['POST'])
def answer_recipe_review(recipe_id):
    """
    Answer a recipe review question.
    Body: { "question_id": 5, "answer": "12" }
    For price_match: answer is product_id or "manual"
    For missing_price: answer is "manual" (then use /ingredient/:id/price to set it)
    For unit_mismatch: answer is pieces_per_kg value or "manual"
    """
    db = Session()
    try:
        data = request.json
        q_id = data.get('question_id')
        answer = data.get('answer')

        question = db.query(RecipeReviewQuestion).get(q_id)
        if not question or question.recipe_id != recipe_id:
            return jsonify({'error': 'Question not found'}), 404

        question.answer = str(answer)
        question.is_answered = True

        # Apply the answer to the ingredient
        if question.ingredient_id:
            ing = db.query(RecipeIngredient).get(question.ingredient_id)
            if ing:
                if question.question_type == 'price_match' and answer != 'manual':
                    # User selected a product
                    try:
                        product = db.query(Product).get(int(answer))
                        if product:
                            ing.product_id = product.id
                            ing.price_per_unit = product.current_price
                            ing.price_unit = product.unit
                            ing.price_source = 'invoice'
                            ing.needs_review = False
                            # Calculate cost
                            _calc_ingredient_cost(ing)
                    except (ValueError, TypeError):
                        pass

                elif question.question_type == 'unit_mismatch' and answer != 'manual':
                    # User provided pieces_per_kg
                    try:
                        pieces_per_kg = float(answer)
                        if ing.price_per_unit and ing.quantity and pieces_per_kg > 0:
                            # price is per kg, convert to per piece
                            price_per_piece = ing.price_per_unit / pieces_per_kg
                            ing.cost = round(price_per_piece * ing.quantity, 2)
                            ing.price_source = 'invoice'
                            ing.needs_review = False
                            if ing.trimming_pct and ing.trimming_pct > 0:
                                ing.adjusted_cost = round(ing.cost / (1 - ing.trimming_pct / 100), 2)
                            else:
                                ing.adjusted_cost = ing.cost
                    except (ValueError, TypeError):
                        pass

                elif answer == 'manual':
                    # User will manually enter the price
                    ing.needs_review = True
                    ing.price_source = None  # Will be set to 'manual' when they enter it

        # Check if all questions are answered
        recipe = db.query(Recipe).get(recipe_id)
        remaining = db.query(RecipeReviewQuestion).filter_by(
            recipe_id=recipe_id, is_answered=False
        ).count()

        if remaining == 0:
            # Check if any ingredients still need manual pricing
            needs_manual = db.query(RecipeIngredient).filter_by(
                recipe_id=recipe_id, needs_review=True
            ).count()
            if needs_manual == 0:
                recipe.price_review_status = 'completed'

        # Recalculate recipe cost
        _recalc_recipe_cost(recipe)
        db.commit()

        return jsonify({
            'status': 'answered',
            'remaining_questions': remaining,
            'review_status': recipe.price_review_status,
            'total_cost': recipe.total_cost,
            'cost_per_unit': recipe.cost_per_unit,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/recipes/ingredient/<int:ingredient_id>/price', methods=['PUT'])
def set_ingredient_price(ingredient_id):
    """
    Manually set or override the price on a recipe ingredient.
    Body: {
      "price_per_unit": 285.0,
      "price_unit": "kg",         # optional, defaults to ingredient's unit
      "trimming_pct": 15,         # optional
      "product_id": null           # optional, link to product
    }
    """
    db = Session()
    try:
        ing = db.query(RecipeIngredient).get(ingredient_id)
        if not ing:
            return jsonify({'error': 'Ingredient not found'}), 404

        data = request.json
        ing.price_per_unit = data.get('price_per_unit', ing.price_per_unit)
        ing.price_unit = data.get('price_unit', ing.price_unit or ing.unit)
        ing.price_source = 'manual'
        ing.needs_review = False

        if 'trimming_pct' in data:
            ing.trimming_pct = data['trimming_pct'] or 0
        if 'product_id' in data:
            ing.product_id = data['product_id']

        # Calculate cost
        _calc_ingredient_cost(ing)

        # Recalculate recipe total
        recipe = db.query(Recipe).get(ing.recipe_id)
        if recipe:
            # Check if all ingredients now have prices
            needs_review = db.query(RecipeIngredient).filter_by(
                recipe_id=recipe.id, needs_review=True
            ).count()
            if needs_review == 0:
                remaining_q = db.query(RecipeReviewQuestion).filter_by(
                    recipe_id=recipe.id, is_answered=False
                ).count()
                if remaining_q == 0:
                    recipe.price_review_status = 'completed'
            _recalc_recipe_cost(recipe)

        db.commit()

        return jsonify({
            'ingredient_id': ing.id,
            'name': ing.name,
            'price_per_unit': ing.price_per_unit,
            'price_unit': ing.price_unit,
            'price_source': ing.price_source,
            'cost': ing.cost,
            'adjusted_cost': ing.adjusted_cost,
            'recipe_total_cost': recipe.total_cost if recipe else None,
            'recipe_cost_per_unit': recipe.cost_per_unit if recipe else None,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


def _calc_ingredient_cost(ing):
    """Calculate ingredient cost with unit conversion and trimming."""
    if not ing.price_per_unit or not ing.quantity:
        return

    price = ing.price_per_unit
    qty = ing.quantity
    recipe_unit = (ing.unit or '').lower()
    price_unit = (ing.price_unit or recipe_unit).lower()

    # Unit conversion: recipe_unit → price_unit
    converted_qty = qty
    if recipe_unit != price_unit:
        # Weight conversions
        weight_to_kg = {'kg': 1, 'g': 0.001, 'hg': 0.1}
        # Volume conversions
        vol_to_l = {'l': 1, 'liter': 1, 'dl': 0.1, 'cl': 0.01, 'ml': 0.001, 'msk': 0.015, 'tsk': 0.005}

        if recipe_unit in weight_to_kg and price_unit in weight_to_kg:
            converted_qty = qty * weight_to_kg[recipe_unit] / weight_to_kg[price_unit]
        elif recipe_unit in vol_to_l and price_unit in vol_to_l:
            converted_qty = qty * vol_to_l[recipe_unit] / vol_to_l[price_unit]
        elif recipe_unit in weight_to_kg and price_unit in vol_to_l:
            # Rough approximation: 1 kg ≈ 1 liter (for water-based)
            converted_qty = qty * weight_to_kg[recipe_unit] / vol_to_l[price_unit]
        elif recipe_unit in vol_to_l and price_unit in weight_to_kg:
            converted_qty = qty * vol_to_l[recipe_unit] / weight_to_kg[price_unit]

    ing.cost = round(price * converted_qty, 2)

    # Apply trimming
    if ing.trimming_pct and ing.trimming_pct > 0:
        ing.adjusted_cost = round(ing.cost / (1 - ing.trimming_pct / 100), 2)
    else:
        ing.adjusted_cost = ing.cost


def _recalc_recipe_cost(recipe):
    """Recalculate recipe total cost from all ingredients."""
    ingredients_total = 0
    for ing in recipe.ingredients:
        ingredients_total += (ing.adjusted_cost or ing.cost or 0)

    seasoning_pct = recipe.seasoning_pct or 0
    seasoning_cost = round(ingredients_total * (seasoning_pct / 100), 2)
    recipe.total_cost = round(ingredients_total + seasoning_cost, 2)

    if recipe.total_yield and recipe.total_yield > 0:
        recipe.cost_per_unit = round(recipe.total_cost / recipe.total_yield, 2)

    # Update food cost % if selling price is set
    if recipe.selling_price and recipe.selling_price > 0 and recipe.cost_per_unit:
        recipe.food_cost_pct = round((recipe.cost_per_unit / recipe.selling_price) * 100, 1)


# ── Export ─────────────────────────────────────────────────────────────

@inventory_bp.route('/api/inventory/export/<int:session_id>', methods=['GET'])
def export_inventory(session_id):
    """Export inventory as JSON (frontend converts to PDF/Excel)."""
    db = Session()
    try:
        session = db.query(InventorySession).get(session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404

        # Group items by category
        categories = {}
        for item in session.items:
            cat = item.category or 'Uncategorized'
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({
                'name': item.name,
                'quantity': item.quantity,
                'unit': item.unit,
                'price_per_unit': item.price_per_unit,
                'trimming_loss_pct': item.trimming_loss_pct,
                'adjusted_price': item.adjusted_price,
                'value': item.value,
                'supplier_name': item.supplier_name
            })

        return jsonify({
            'session_id': session.id,
            'year': session.year,
            'month': session.month,
            'total_value': session.total_value,
            'confirmed_at': session.confirmed_at.isoformat() if session.confirmed_at else None,
            'item_count': len(session.items),
            'categories': categories
        })
    finally:
        db.close()


# ── Dashboard / Trends ─────────────────────────────────────────────────

@inventory_bp.route('/api/inventory/trends', methods=['GET'])
def get_inventory_trends():
    """Get inventory value trends over time for dashboard."""
    db = Session()
    try:
        sessions = db.query(InventorySession).filter_by(
            status='confirmed'
        ).order_by(InventorySession.year, InventorySession.month).all()

        trends = [{
            'year': s.year,
            'month': s.month,
            'label': f'{s.year}-{s.month:02d}',
            'total_value': s.total_value or 0,
            'item_count': len(s.items)
        } for s in sessions]

        # Category breakdown for latest inventory
        category_breakdown = {}
        if sessions:
            latest = sessions[-1]
            for item in latest.items:
                cat = item.category or 'Uncategorized'
                if cat not in category_breakdown:
                    category_breakdown[cat] = {'count': 0, 'value': 0}
                category_breakdown[cat]['count'] += 1
                category_breakdown[cat]['value'] += item.value or 0

        # Month-over-month change
        mom_change = None
        if len(sessions) >= 2:
            current = sessions[-1].total_value or 0
            previous = sessions[-2].total_value or 0
            if previous > 0:
                mom_change = round(((current - previous) / previous) * 100, 1)

        return jsonify({
            'trends': trends,
            'category_breakdown': category_breakdown,
            'month_over_month_change': mom_change,
            'total_inventories': len(sessions)
        })
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
#  DISHES — composed of recipes + standalone ingredients
# ══════════════════════════════════════════════════════════════════════

def _recalc_dish_cost(dish, db):
    """Recalculate total_cost, cost_per_serving, food_cost_pct, and margin for a dish."""
    total = 0.0
    for dr in dish.dish_recipes:
        recipe = db.query(Recipe).get(dr.recipe_id)
        if recipe and recipe.cost_per_unit:
            dr.cost = round(recipe.cost_per_unit * (dr.portions or 1), 2)
        total += dr.cost or 0
    for di in dish.dish_ingredients:
        if di.adjusted_cost:
            total += di.adjusted_cost
        elif di.cost:
            total += di.cost
    dish.total_cost = round(total, 2)
    dish.cost_per_serving = round(total / (dish.servings or 1), 2)
    # Food cost % and margin
    if dish.selling_price and dish.selling_price > 0:
        dish.food_cost_pct = round((dish.cost_per_serving / dish.selling_price) * 100, 1)
        dish.margin = round(dish.selling_price - dish.cost_per_serving, 2)
    else:
        dish.food_cost_pct = None
        dish.margin = None


def _calc_ingredient_cost(ing, db):
    """Calculate cost for a standalone dish ingredient, handling unit conversion."""
    if ing.product_id:
        product = db.query(Product).get(ing.product_id)
        if product and product.current_price:
            ing.cost_per_unit = product.current_price
            product_unit = (product.unit or '').lower()
            ing_unit = (ing.unit or '').lower()
            # Unit conversion: piece vs kg
            if ing_unit in ('piece', 'pieces', 'st', 'pcs') and product_unit == 'kg':
                if ing.pieces_per_kg and ing.pieces_per_kg > 0:
                    cost_per_piece = product.current_price / ing.pieces_per_kg
                    ing.cost = round(cost_per_piece * (ing.quantity or 1), 2)
                else:
                    ing.cost = None  # Need AI to ask
                    return
            else:
                ing.cost = round(product.current_price * (ing.quantity or 0), 2)
    elif ing.cost_per_unit and ing.quantity:
        ing.cost = round(ing.cost_per_unit * ing.quantity, 2)
    # Apply trimming
    if ing.cost and ing.trimming_pct and ing.trimming_pct > 0:
        ing.adjusted_cost = round(ing.cost / (1 - ing.trimming_pct / 100), 2)
    else:
        ing.adjusted_cost = ing.cost


@inventory_bp.route('/api/dishes', methods=['GET'])
def get_dishes():
    """List all dishes."""
    db = Session()
    try:
        dishes = db.query(Dish).order_by(Dish.updated_at.desc()).all()
        return jsonify([{
            'id': d.id,
            'name': d.name,
            'description': d.description,
            'added_by': d.added_by,
            'servings': d.servings,
            'total_cost': d.total_cost,
            'cost_per_serving': d.cost_per_serving,
            'selling_price': d.selling_price,
            'food_cost_pct': d.food_cost_pct,
            'margin': d.margin,
            'photos': json.loads(d.photos) if d.photos else [],
            'recipe_count': len(d.dish_recipes),
            'ingredient_count': len(d.dish_ingredients),
            'created_at': d.created_at.isoformat() if d.created_at else None,
            'updated_at': d.updated_at.isoformat() if d.updated_at else None,
        } for d in dishes])
    finally:
        db.close()


@inventory_bp.route('/api/dishes/<int:dish_id>', methods=['GET'])
def get_dish(dish_id):
    """Get a single dish with all recipes and ingredients."""
    db = Session()
    try:
        d = db.query(Dish).get(dish_id)
        if not d:
            return jsonify({'error': 'Dish not found'}), 404
        return jsonify({
            'id': d.id,
            'name': d.name,
            'description': d.description,
            'added_by': d.added_by,
            'servings': d.servings,
            'total_cost': d.total_cost,
            'cost_per_serving': d.cost_per_serving,
            'selling_price': d.selling_price,
            'food_cost_pct': d.food_cost_pct,
            'margin': d.margin,
            'photos': json.loads(d.photos) if d.photos else [],
            'created_at': d.created_at.isoformat() if d.created_at else None,
            'updated_at': d.updated_at.isoformat() if d.updated_at else None,
            'recipes': [{
                'id': dr.id,
                'recipe_id': dr.recipe_id,
                'recipe_name': dr.recipe.name if dr.recipe else None,
                'portions': dr.portions,
                'cost': dr.cost,
                'order': dr.order,
            } for dr in sorted(d.dish_recipes, key=lambda x: x.order)],
            'ingredients': [{
                'id': di.id,
                'product_id': di.product_id,
                'name': di.name,
                'quantity': di.quantity,
                'unit': di.unit,
                'cost_per_unit': di.cost_per_unit,
                'cost': di.cost,
                'trimming_pct': di.trimming_pct,
                'adjusted_cost': di.adjusted_cost,
                'pieces_per_kg': di.pieces_per_kg,
                'notes': di.notes,
                'order': di.order,
                'needs_conversion': di.cost is None and di.product_id is not None,
            } for di in sorted(d.dish_ingredients, key=lambda x: x.order)],
        })
    finally:
        db.close()


@inventory_bp.route('/api/dishes', methods=['POST'])
def create_dish():
    """Create a new dish."""
    db = Session()
    try:
        data = request.json
        dish = Dish(
            name=data.get('name', 'Untitled Dish'),
            description=data.get('description'),
            added_by=data.get('added_by'),
            servings=data.get('servings', 1),
            selling_price=data.get('selling_price'),
            photos=json.dumps(data.get('photos', [])),
        )
        db.add(dish)
        db.flush()

        # Add recipes
        for i, r in enumerate(data.get('recipes', [])):
            dr = DishRecipe(
                dish_id=dish.id,
                recipe_id=r['recipe_id'],
                portions=r.get('portions', 1),
                order=i,
            )
            db.add(dr)

        # Add standalone ingredients
        for i, ing in enumerate(data.get('ingredients', [])):
            di = DishIngredient(
                dish_id=dish.id,
                product_id=ing.get('product_id'),
                name=ing.get('name', 'Unknown'),
                quantity=ing.get('quantity'),
                unit=ing.get('unit'),
                cost_per_unit=ing.get('cost_per_unit'),
                trimming_pct=ing.get('trimming_pct', 0),
                pieces_per_kg=ing.get('pieces_per_kg'),
                notes=ing.get('notes'),
                order=i,
            )
            _calc_ingredient_cost(di, db)
            db.add(di)

        db.flush()
        _recalc_dish_cost(dish, db)
        dish.updated_at = datetime.utcnow()
        db.commit()

        return jsonify({'id': dish.id, 'total_cost': dish.total_cost, 'cost_per_serving': dish.cost_per_serving}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/dishes/<int:dish_id>', methods=['PUT'])
def update_dish(dish_id):
    """Update an existing dish."""
    db = Session()
    try:
        dish = db.query(Dish).get(dish_id)
        if not dish:
            return jsonify({'error': 'Dish not found'}), 404

        data = request.json
        dish.name = data.get('name', dish.name)
        dish.description = data.get('description', dish.description)
        dish.added_by = data.get('added_by', dish.added_by)
        dish.servings = data.get('servings', dish.servings)
        if 'selling_price' in data:
            dish.selling_price = data.get('selling_price')
        if 'photos' in data:
            dish.photos = json.dumps(data['photos'])

        # Replace recipes
        if 'recipes' in data:
            for dr in dish.dish_recipes:
                db.delete(dr)
            db.flush()
            for i, r in enumerate(data['recipes']):
                dr = DishRecipe(
                    dish_id=dish.id,
                    recipe_id=r['recipe_id'],
                    portions=r.get('portions', 1),
                    order=i,
                )
                db.add(dr)

        # Replace ingredients
        if 'ingredients' in data:
            for di in dish.dish_ingredients:
                db.delete(di)
            db.flush()
            for i, ing in enumerate(data['ingredients']):
                di = DishIngredient(
                    dish_id=dish.id,
                    product_id=ing.get('product_id'),
                    name=ing.get('name', 'Unknown'),
                    quantity=ing.get('quantity'),
                    unit=ing.get('unit'),
                    cost_per_unit=ing.get('cost_per_unit'),
                    trimming_pct=ing.get('trimming_pct', 0),
                    pieces_per_kg=ing.get('pieces_per_kg'),
                    notes=ing.get('notes'),
                    order=i,
                )
                _calc_ingredient_cost(di, db)
                db.add(di)

        db.flush()
        _recalc_dish_cost(dish, db)
        dish.updated_at = datetime.utcnow()
        db.commit()

        return jsonify({'id': dish.id, 'total_cost': dish.total_cost, 'cost_per_serving': dish.cost_per_serving})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/dishes/<int:dish_id>', methods=['DELETE'])
def delete_dish(dish_id):
    """Delete a dish."""
    db = Session()
    try:
        dish = db.query(Dish).get(dish_id)
        if not dish:
            return jsonify({'error': 'Dish not found'}), 404
        db.delete(dish)
        db.commit()
        return jsonify({'status': 'deleted'})
    finally:
        db.close()


@inventory_bp.route('/api/dishes/<int:dish_id>/convert-unit', methods=['POST'])
def convert_dish_ingredient_unit(dish_id):
    """AI unit conversion — user tells us pieces_per_kg for an ingredient."""
    db = Session()
    try:
        data = request.json
        ingredient_id = data.get('ingredient_id')
        pieces_per_kg = data.get('pieces_per_kg')

        di = db.query(DishIngredient).get(ingredient_id)
        if not di or di.dish_id != dish_id:
            return jsonify({'error': 'Ingredient not found'}), 404

        di.pieces_per_kg = float(pieces_per_kg)
        _calc_ingredient_cost(di, db)

        dish = db.query(Dish).get(dish_id)
        _recalc_dish_cost(dish, db)
        dish.updated_at = datetime.utcnow()
        db.commit()

        return jsonify({
            'ingredient_id': di.id,
            'cost': di.cost,
            'adjusted_cost': di.adjusted_cost,
            'pieces_per_kg': di.pieces_per_kg,
            'dish_total_cost': dish.total_cost,
            'dish_cost_per_serving': dish.cost_per_serving,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
#  MENUS — lunch, tasting, personalized
# ══════════════════════════════════════════════════════════════════════

def _recalc_menu_cost(menu, db):
    """Recalculate total cost for a menu (1 menu served)."""
    total = 0.0
    for section in menu.sections:
        for item in section.items:
            if item.dish_id:
                dish = db.query(Dish).get(item.dish_id)
                if dish and dish.cost_per_serving:
                    item.cost = round(dish.cost_per_serving * (item.portions or 1), 2)
            total += item.cost or 0
    menu.total_cost = round(total, 2)
    menu.cost_per_menu = round(total, 2)  # Already per 1 menu
    # Food cost % and margin
    if menu.selling_price and menu.selling_price > 0:
        menu.food_cost_pct = round((menu.cost_per_menu / menu.selling_price) * 100, 1)
        menu.margin = round(menu.selling_price - menu.cost_per_menu, 2)
    else:
        menu.food_cost_pct = None
        menu.margin = None


@inventory_bp.route('/api/menus', methods=['GET'])
def get_menus():
    """List all menus."""
    db = Session()
    try:
        menus = db.query(Menu).order_by(Menu.updated_at.desc()).all()
        return jsonify([{
            'id': m.id,
            'name': m.name,
            'menu_type': m.menu_type,
            'description': m.description,
            'added_by': m.added_by,
            'total_cost': m.total_cost,
            'cost_per_menu': m.cost_per_menu,
            'selling_price': m.selling_price,
            'food_cost_pct': m.food_cost_pct,
            'margin': m.margin,
            'section_count': len(m.sections),
            'photos': json.loads(m.photos) if m.photos else [],
            'created_at': m.created_at.isoformat() if m.created_at else None,
            'updated_at': m.updated_at.isoformat() if m.updated_at else None,
        } for m in menus])
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>', methods=['GET'])
def get_menu(menu_id):
    """Get a single menu with all sections and items."""
    db = Session()
    try:
        m = db.query(Menu).get(menu_id)
        if not m:
            return jsonify({'error': 'Menu not found'}), 404
        return jsonify({
            'id': m.id,
            'name': m.name,
            'menu_type': m.menu_type,
            'description': m.description,
            'added_by': m.added_by,
            'total_cost': m.total_cost,
            'cost_per_menu': m.cost_per_menu,
            'selling_price': m.selling_price,
            'food_cost_pct': m.food_cost_pct,
            'margin': m.margin,
            'photos': json.loads(m.photos) if m.photos else [],
            'created_at': m.created_at.isoformat() if m.created_at else None,
            'updated_at': m.updated_at.isoformat() if m.updated_at else None,
            'sections': [{
                'id': s.id,
                'name': s.name,
                'description': s.description,
                'order': s.order,
                'items': [{
                    'id': item.id,
                    'dish_id': item.dish_id,
                    'dish_name': item.dish.name if item.dish else item.name,
                    'name': item.name,
                    'portions': item.portions,
                    'cost': item.cost,
                    'order': item.order,
                    'dish_cost_per_serving': item.dish.cost_per_serving if item.dish else None,
                } for item in sorted(s.items, key=lambda x: x.order)]
            } for s in sorted(m.sections, key=lambda x: x.order)]
        })
    finally:
        db.close()


@inventory_bp.route('/api/menus', methods=['POST'])
def create_menu():
    """Create a new menu with default sections based on type."""
    db = Session()
    try:
        data = request.json
        menu_type = data.get('menu_type', 'personalized')

        menu = Menu(
            name=data.get('name', f'New {menu_type.title()} Menu'),
            menu_type=menu_type,
            description=data.get('description'),
            added_by=data.get('added_by'),
            selling_price=data.get('selling_price'),
            photos=json.dumps(data.get('photos', [])),
        )
        db.add(menu)
        db.flush()

        # Create default sections based on menu type
        if 'sections' in data:
            # User provided custom sections
            for i, s in enumerate(data['sections']):
                section = MenuSection(
                    menu_id=menu.id,
                    name=s.get('name', f'Course {i+1}'),
                    description=s.get('description'),
                    order=i,
                )
                db.add(section)
                db.flush()
                for j, item in enumerate(s.get('items', [])):
                    si = MenuSectionItem(
                        section_id=section.id,
                        dish_id=item.get('dish_id'),
                        name=item.get('name'),
                        portions=item.get('portions', 1),
                        order=j,
                    )
                    db.add(si)
        else:
            # Auto-create default sections
            defaults = {
                'lunch': [
                    {'name': 'Starter', 'order': 0},
                    {'name': 'Main Course', 'order': 1},
                    {'name': 'Dessert', 'order': 2},
                ],
                'tasting': [
                    {'name': 'Snacks', 'order': 0},
                    {'name': 'Dishes', 'order': 1},
                    {'name': 'Mignardises', 'order': 2},
                ],
                'personalized': [
                    {'name': 'Course 1', 'order': 0},
                ],
            }
            for s in defaults.get(menu_type, defaults['personalized']):
                section = MenuSection(
                    menu_id=menu.id,
                    name=s['name'],
                    order=s['order'],
                )
                db.add(section)

        db.flush()
        _recalc_menu_cost(menu, db)
        menu.updated_at = datetime.utcnow()
        db.commit()

        return jsonify({'id': menu.id, 'total_cost': menu.total_cost}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>', methods=['PUT'])
def update_menu(menu_id):
    """Update a menu — name, sections, items."""
    db = Session()
    try:
        menu = db.query(Menu).get(menu_id)
        if not menu:
            return jsonify({'error': 'Menu not found'}), 404

        data = request.json
        menu.name = data.get('name', menu.name)
        menu.description = data.get('description', menu.description)
        menu.added_by = data.get('added_by', menu.added_by)
        if 'selling_price' in data:
            menu.selling_price = data.get('selling_price')
        if 'photos' in data:
            menu.photos = json.dumps(data['photos'])

        # Replace sections if provided
        if 'sections' in data:
            for s in menu.sections:
                for item in s.items:
                    db.delete(item)
                db.delete(s)
            db.flush()

            for i, s in enumerate(data['sections']):
                section = MenuSection(
                    menu_id=menu.id,
                    name=s.get('name', f'Course {i+1}'),
                    description=s.get('description'),
                    order=i,
                )
                db.add(section)
                db.flush()
                for j, item in enumerate(s.get('items', [])):
                    si = MenuSectionItem(
                        section_id=section.id,
                        dish_id=item.get('dish_id'),
                        name=item.get('name'),
                        portions=item.get('portions', 1),
                        order=j,
                    )
                    db.add(si)

        db.flush()
        _recalc_menu_cost(menu, db)
        menu.updated_at = datetime.utcnow()
        db.commit()

        return jsonify({'id': menu.id, 'total_cost': menu.total_cost, 'cost_per_menu': menu.cost_per_menu})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>', methods=['DELETE'])
def delete_menu(menu_id):
    """Delete a menu."""
    db = Session()
    try:
        menu = db.query(Menu).get(menu_id)
        if not menu:
            return jsonify({'error': 'Menu not found'}), 404
        db.delete(menu)
        db.commit()
        return jsonify({'status': 'deleted'})
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>/sections', methods=['POST'])
def add_menu_section(menu_id):
    """Add a new section to a menu."""
    db = Session()
    try:
        menu = db.query(Menu).get(menu_id)
        if not menu:
            return jsonify({'error': 'Menu not found'}), 404

        data = request.json
        max_order = max([s.order for s in menu.sections], default=-1)
        section = MenuSection(
            menu_id=menu.id,
            name=data.get('name', f'Course {max_order + 2}'),
            description=data.get('description'),
            order=max_order + 1,
        )
        db.add(section)
        db.commit()

        return jsonify({'id': section.id, 'name': section.name, 'order': section.order}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>/sections/<int:section_id>', methods=['DELETE'])
def delete_menu_section(menu_id, section_id):
    """Remove a section from a menu."""
    db = Session()
    try:
        section = db.query(MenuSection).get(section_id)
        if not section or section.menu_id != menu_id:
            return jsonify({'error': 'Section not found'}), 404
        db.delete(section)
        menu = db.query(Menu).get(menu_id)
        db.flush()
        _recalc_menu_cost(menu, db)
        db.commit()
        return jsonify({'status': 'deleted'})
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>/sections/<int:section_id>/items', methods=['POST'])
def add_section_item(menu_id, section_id):
    """Add a dish to a menu section."""
    db = Session()
    try:
        section = db.query(MenuSection).get(section_id)
        if not section or section.menu_id != menu_id:
            return jsonify({'error': 'Section not found'}), 404

        data = request.json
        max_order = max([i.order for i in section.items], default=-1)

        item = MenuSectionItem(
            section_id=section.id,
            dish_id=data.get('dish_id'),
            name=data.get('name'),
            portions=data.get('portions', 1),
            order=max_order + 1,
        )

        # Calculate cost from dish
        if item.dish_id:
            dish = db.query(Dish).get(item.dish_id)
            if dish and dish.cost_per_serving:
                item.cost = round(dish.cost_per_serving * (item.portions or 1), 2)

        db.add(item)
        db.flush()
        menu = db.query(Menu).get(menu_id)
        _recalc_menu_cost(menu, db)
        db.commit()

        return jsonify({
            'id': item.id,
            'dish_id': item.dish_id,
            'name': item.name,
            'cost': item.cost,
            'menu_total_cost': menu.total_cost,
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/menus/<int:menu_id>/sections/<int:section_id>/items/<int:item_id>', methods=['DELETE'])
def delete_section_item(menu_id, section_id, item_id):
    """Remove a dish from a menu section."""
    db = Session()
    try:
        item = db.query(MenuSectionItem).get(item_id)
        if not item or item.section_id != section_id:
            return jsonify({'error': 'Item not found'}), 404
        section = db.query(MenuSection).get(section_id)
        if not section or section.menu_id != menu_id:
            return jsonify({'error': 'Section not found'}), 404
        db.delete(item)
        menu = db.query(Menu).get(menu_id)
        db.flush()
        _recalc_menu_cost(menu, db)
        db.commit()
        return jsonify({'status': 'deleted', 'menu_total_cost': menu.total_cost})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
#  FOOD COST & MARGIN — pricing calculator + economy overview
# ══════════════════════════════════════════════════════════════════════

@inventory_bp.route('/api/pricing/calculate', methods=['POST'])
def calculate_pricing():
    """
    Two-way pricing calculator.
    Mode 1: Given selling_price → returns food_cost_pct and margin
    Mode 2: Given target_food_cost_pct → returns required selling_price and margin

    Body: {
      "cost": 85.0,           # cost per serving (required)
      "selling_price": 350,   # option 1: you set the price
      "target_food_cost_pct": 30  # option 2: you set the target %
    }
    """
    data = request.json
    cost = data.get('cost')
    if not cost or cost <= 0:
        return jsonify({'error': 'Cost is required and must be > 0'}), 400

    selling_price = data.get('selling_price')
    target_pct = data.get('target_food_cost_pct')

    if selling_price and selling_price > 0:
        # Mode 1: price → food cost %
        food_cost_pct = round((cost / selling_price) * 100, 1)
        margin = round(selling_price - cost, 2)
        return jsonify({
            'mode': 'from_selling_price',
            'cost': round(cost, 2),
            'selling_price': round(selling_price, 2),
            'food_cost_pct': food_cost_pct,
            'margin': margin,
            'margin_pct': round((margin / selling_price) * 100, 1),
            'healthy': food_cost_pct <= 35,
        })
    elif target_pct and target_pct > 0:
        # Mode 2: target % → required selling price
        selling_price = round(cost / (target_pct / 100), 2)
        margin = round(selling_price - cost, 2)
        return jsonify({
            'mode': 'from_target_food_cost',
            'cost': round(cost, 2),
            'selling_price': selling_price,
            'food_cost_pct': target_pct,
            'margin': margin,
            'margin_pct': round((margin / selling_price) * 100, 1),
            'healthy': target_pct <= 35,
        })
    else:
        return jsonify({'error': 'Provide either selling_price or target_food_cost_pct'}), 400


@inventory_bp.route('/api/pricing/set', methods=['POST'])
def set_pricing():
    """
    Set selling price OR target food cost % on a dish or menu.
    Body: {
      "type": "dish" or "menu",
      "id": 5,
      "selling_price": 350,        # option 1
      "target_food_cost_pct": 30   # option 2 — calculates selling price for you
    }
    """
    db = Session()
    try:
        data = request.json
        item_type = data.get('type')
        item_id = data.get('id')

        if item_type == 'dish':
            item = db.query(Dish).get(int(item_id))
            cost = item.cost_per_serving if item else None
        elif item_type == 'menu':
            item = db.query(Menu).get(int(item_id))
            cost = item.cost_per_menu if item else None
        else:
            return jsonify({'error': 'type must be "dish" or "menu"'}), 400

        if not item:
            return jsonify({'error': f'{item_type} not found'}), 404
        if not cost or cost <= 0:
            return jsonify({'error': f'{item_type} has no cost calculated yet'}), 400

        selling_price = data.get('selling_price')
        target_pct = data.get('target_food_cost_pct')

        if selling_price and selling_price > 0:
            item.selling_price = round(float(selling_price), 2)
        elif target_pct and target_pct > 0:
            item.selling_price = round(cost / (float(target_pct) / 100), 2)
        else:
            return jsonify({'error': 'Provide selling_price or target_food_cost_pct'}), 400

        # Recalculate food cost % and margin
        item.food_cost_pct = round((cost / item.selling_price) * 100, 1)
        item.margin = round(item.selling_price - cost, 2)
        item.updated_at = datetime.utcnow()
        db.commit()

        return jsonify({
            'id': item.id,
            'type': item_type,
            'cost': round(cost, 2),
            'selling_price': item.selling_price,
            'food_cost_pct': item.food_cost_pct,
            'margin': item.margin,
            'margin_pct': round((item.margin / item.selling_price) * 100, 1),
            'healthy': item.food_cost_pct <= 35,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@inventory_bp.route('/api/economy/margins', methods=['GET'])
def economy_margins():
    """
    Economy dashboard — margin overview for all dishes and menus.
    Returns everything sorted by food cost % (worst first).
    """
    db = Session()
    try:
        dishes = db.query(Dish).all()
        menus = db.query(Menu).all()

        items = []
        total_healthy = 0
        total_warning = 0
        total_danger = 0
        total_no_price = 0

        for d in dishes:
            entry = {
                'type': 'dish',
                'id': d.id,
                'name': d.name,
                'cost': d.cost_per_serving,
                'selling_price': d.selling_price,
                'food_cost_pct': d.food_cost_pct,
                'margin': d.margin,
                'status': 'no_price',
            }
            if d.food_cost_pct is not None:
                if d.food_cost_pct <= 30:
                    entry['status'] = 'healthy'
                    total_healthy += 1
                elif d.food_cost_pct <= 35:
                    entry['status'] = 'warning'
                    total_warning += 1
                else:
                    entry['status'] = 'danger'
                    total_danger += 1
            else:
                total_no_price += 1
            items.append(entry)

        for m in menus:
            entry = {
                'type': 'menu',
                'id': m.id,
                'name': m.name,
                'menu_type': m.menu_type,
                'cost': m.cost_per_menu,
                'selling_price': m.selling_price,
                'food_cost_pct': m.food_cost_pct,
                'margin': m.margin,
                'status': 'no_price',
            }
            if m.food_cost_pct is not None:
                if m.food_cost_pct <= 30:
                    entry['status'] = 'healthy'
                    total_healthy += 1
                elif m.food_cost_pct <= 35:
                    entry['status'] = 'warning'
                    total_warning += 1
                else:
                    entry['status'] = 'danger'
                    total_danger += 1
            else:
                total_no_price += 1
            items.append(entry)

        # Sort: danger first, then warning, then healthy, then no_price
        status_order = {'danger': 0, 'warning': 1, 'healthy': 2, 'no_price': 3}
        items.sort(key=lambda x: (status_order.get(x['status'], 9), -(x['food_cost_pct'] or 0)))

        return jsonify({
            'items': items,
            'summary': {
                'total': len(items),
                'healthy': total_healthy,
                'warning': total_warning,
                'danger': total_danger,
                'no_price': total_no_price,
            },
            'thresholds': {
                'healthy_max': 30,
                'warning_max': 35,
            }
        })
    finally:
        db.close()
