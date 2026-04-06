"""
Inventory API endpoints — browse, create, edit, review, confirm.
"""
import json
from flask import Blueprint, jsonify, request
from datetime import datetime
from models import (
    Session, InventorySession, InventoryItem, ReviewQuestion,
    Product, Supplier, Recipe, RecipeIngredient
)
from ai_parser import parse_inventory_with_ai, generate_smart_questions, parse_recipe_text_with_ai
 
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
            # Link to selected product
            product = db.query(Product).get(int(answer))
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
            'ingredients': [{
                'id': ing.id,
                'name': ing.name,
                'quantity': ing.quantity,
                'unit': ing.unit,
                'cost': ing.cost,
                'trimming_pct': ing.trimming_pct,
                'adjusted_cost': ing.adjusted_cost,
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
 
