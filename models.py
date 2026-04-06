"""
Database models — PostgreSQL-ready via SQLAlchemy.
Stores suppliers, products, invoices, invoice line items, price history,
and Fortnox OAuth tokens.
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from config import DATABASE_URL

Base = declarative_base()
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


class Supplier(Base):
    __tablename__ = 'suppliers'

    id = Column(Integer, primary_key=True)
    fortnox_id = Column(String, unique=True, nullable=True)  # Fortnox SupplierNumber
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    org_number = Column(String, nullable=True)  # Swedish org number
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    products = relationship('Product', back_populates='supplier')
    invoices = relationship('Invoice', back_populates='supplier')


class Product(Base):
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True)
    fortnox_article_number = Column(String, unique=True, nullable=True)
    name = Column(String, nullable=False)
    supplier_id = Column(Integer, ForeignKey('suppliers.id'), nullable=True)
    unit = Column(String, nullable=True)  # kg, st, liter, etc.
    current_price = Column(Float, nullable=True)
    category = Column(String, nullable=True)  # meat, dairy, produce, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier = relationship('Supplier', back_populates='products')
    price_history = relationship('PriceHistory', back_populates='product')


class Invoice(Base):
    __tablename__ = 'invoices'

    id = Column(Integer, primary_key=True)
    fortnox_id = Column(String, unique=True, nullable=True)  # Fortnox GivenNumber
    supplier_id = Column(Integer, ForeignKey('suppliers.id'), nullable=True)
    supplier_name = Column(String, nullable=True)
    invoice_number = Column(String, nullable=True)
    invoice_date = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    total_amount = Column(Float, nullable=True)
    vat_amount = Column(Float, nullable=True)
    currency = Column(String, default='SEK')
    is_paid = Column(Boolean, default=False)
    payment_date = Column(DateTime, nullable=True)
    fortnox_raw = Column(Text, nullable=True)  # Store raw Fortnox JSON for debugging
    synced_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    supplier = relationship('Supplier', back_populates='invoices')
    line_items = relationship('InvoiceLineItem', back_populates='invoice')


class InvoiceLineItem(Base):
    """Individual line items from each invoice — this is where price tracking happens."""
    __tablename__ = 'invoice_line_items'

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey('invoices.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)
    article_number = Column(String, nullable=True)
    description = Column(String, nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    unit_price = Column(Float, nullable=True)
    total = Column(Float, nullable=True)
    vat_percent = Column(Float, nullable=True)

    invoice = relationship('Invoice', back_populates='line_items')
    product = relationship('Product')


class PriceHistory(Base):
    """Tracks price changes per product over time — built from invoice line items."""
    __tablename__ = 'price_history'

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    price = Column(Float, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    invoice_number = Column(String, nullable=True)
    invoice_id = Column(Integer, ForeignKey('invoices.id'), nullable=True)

    product = relationship('Product', back_populates='price_history')


class FortnoxToken(Base):
    """Stores OAuth tokens so they persist across restarts."""
    __tablename__ = 'fortnox_tokens'

    id = Column(Integer, primary_key=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SyncLog(Base):
    """Tracks sync history so we know what's been imported."""
    __tablename__ = 'sync_logs'

    id = Column(Integer, primary_key=True)
    sync_type = Column(String, nullable=False)  # 'invoices', 'suppliers', 'articles'
    status = Column(String, nullable=False)  # 'success', 'error'
    records_synced = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class InventorySession(Base):
    """A monthly inventory session — one per month."""
    __tablename__ = 'inventory_sessions'

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    status = Column(String, default='draft')  # draft, reviewing, confirmed
    raw_input = Column(Text, nullable=True)  # Original messy text or voice transcript
    total_value = Column(Float, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship('InventoryItem', back_populates='session', cascade='all, delete-orphan')
    questions = relationship('ReviewQuestion', back_populates='session', cascade='all, delete-orphan')


class InventoryItem(Base):
    """A single item in an inventory — product + quantity + value."""
    __tablename__ = 'inventory_items'

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('inventory_sessions.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)
    raw_name = Column(String, nullable=True)  # Original name from user input
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    supplier_name = Column(String, nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)  # kg, st, liter, etc.
    price_per_unit = Column(Float, nullable=True)
    trimming_loss_pct = Column(Float, default=0)  # e.g. 20 for 20%
    adjusted_price = Column(Float, nullable=True)  # price after trimming loss
    is_recipe_product = Column(Boolean, default=False)
    recipe_id = Column(Integer, ForeignKey('recipes.id'), nullable=True)
    value = Column(Float, nullable=True)  # final calculated value
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship('InventorySession', back_populates='items')
    product = relationship('Product')
    recipe = relationship('Recipe')


class ReviewQuestion(Base):
    """AI-generated review questions for inventory disambiguation."""
    __tablename__ = 'review_questions'

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('inventory_sessions.id'), nullable=False)
    item_id = Column(Integer, ForeignKey('inventory_items.id'), nullable=True)
    question_type = Column(String, nullable=False)  # product_match, cut_variant, trimming_loss, recipe_cost
    question_text = Column(String, nullable=False)
    options = Column(Text, nullable=True)  # JSON array of options
    answer = Column(String, nullable=True)
    is_answered = Column(Boolean, default=False)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship('InventorySession', back_populates='questions')
    item = relationship('InventoryItem')


class Recipe(Base):
    """Restaurant recipes — used for costing finished products in inventory."""
    __tablename__ = 'recipes'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    added_by = Column(String, nullable=True)  # Chef name
    total_yield = Column(Float, nullable=True)  # e.g. 2 (liters)
    yield_unit = Column(String, nullable=True)  # e.g. 'liter', 'kg', 'portions'
    total_cost = Column(Float, nullable=True)  # sum of ingredient costs
    cost_per_unit = Column(Float, nullable=True)  # total_cost / total_yield
    selling_price = Column(Float, nullable=True)  # If sold standalone (e.g. sauce, bread)
    food_cost_pct = Column(Float, nullable=True)  # (cost_per_unit / selling_price) * 100
    seasoning_pct = Column(Float, default=0)  # seasoning cost as % of ingredients total
    price_review_status = Column(String, default='pending')  # pending, reviewing, completed
    notes = Column(Text, nullable=True)
    photos = Column(Text, nullable=True)  # JSON array of photo URLs
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ingredients = relationship('RecipeIngredient', back_populates='recipe', cascade='all, delete-orphan')
    review_questions = relationship('RecipeReviewQuestion', cascade='all, delete-orphan')


class RecipeIngredient(Base):
    """Ingredients in a recipe — links to products for price lookup."""
    __tablename__ = 'recipe_ingredients'

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey('recipes.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)
    name = Column(String, nullable=False)
    quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)  # kg, g, st, liter, dl, ml, msk, tsk, etc.
    price_per_unit = Column(Float, nullable=True)  # Price per kg/liter/st from invoice or manual
    price_unit = Column(String, nullable=True)  # The unit the price is in (e.g. 'kg' even if recipe uses 'g')
    price_source = Column(String, nullable=True)  # 'invoice', 'manual', 'estimated'
    cost = Column(Float, nullable=True)  # Calculated: quantity * price (with unit conversion)
    trimming_pct = Column(Float, default=0)  # trimming loss percentage
    adjusted_cost = Column(Float, nullable=True)  # cost after trimming: cost / (1 - trim%)
    needs_review = Column(Boolean, default=False)  # True if AI couldn't find a confident price match
    notes = Column(String, nullable=True)  # e.g. "finely diced", "to taste"

    recipe = relationship('Recipe', back_populates='ingredients')
    product = relationship('Product')


class RecipeReviewQuestion(Base):
    """AI-generated review questions for recipe ingredient pricing."""
    __tablename__ = 'recipe_review_questions'

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey('recipes.id'), nullable=False)
    ingredient_id = Column(Integer, ForeignKey('recipe_ingredients.id'), nullable=True)
    question_type = Column(String, nullable=False)
    # Types: 'price_match' (multiple products match), 'missing_price' (no product found),
    #        'unit_mismatch' (recipe uses g but price is per kg), 'quantity_check' (seems too high/low),
    #        'confirm_price' (auto-matched, just confirm)
    question_text = Column(String, nullable=False)
    options = Column(Text, nullable=True)  # JSON array of options
    answer = Column(String, nullable=True)
    is_answered = Column(Boolean, default=False)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    recipe = relationship('Recipe')
    ingredient = relationship('RecipeIngredient')


# ── Dish & Menu Models ─────────────────────────────────────────────────

class Dish(Base):
    """A composed dish — made of recipes + standalone ingredients."""
    __tablename__ = 'dishes'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    added_by = Column(String, nullable=True)  # Chef name
    servings = Column(Float, default=1)  # How many portions this dish makes
    total_cost = Column(Float, nullable=True)  # Sum of all recipe costs + ingredient costs
    cost_per_serving = Column(Float, nullable=True)  # total_cost / servings
    selling_price = Column(Float, nullable=True)  # What you charge the customer
    food_cost_pct = Column(Float, nullable=True)  # (cost_per_serving / selling_price) * 100
    margin = Column(Float, nullable=True)  # selling_price - cost_per_serving
    photos = Column(Text, nullable=True)  # JSON array of photo URLs
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    dish_recipes = relationship('DishRecipe', back_populates='dish', cascade='all, delete-orphan')
    dish_ingredients = relationship('DishIngredient', back_populates='dish', cascade='all, delete-orphan')


class DishRecipe(Base):
    """Links a recipe into a dish — with portion scaling."""
    __tablename__ = 'dish_recipes'

    id = Column(Integer, primary_key=True)
    dish_id = Column(Integer, ForeignKey('dishes.id'), nullable=False)
    recipe_id = Column(Integer, ForeignKey('recipes.id'), nullable=False)
    portions = Column(Float, default=1)  # How many portions of this recipe go into the dish
    cost = Column(Float, nullable=True)  # recipe.cost_per_unit * portions
    order = Column(Integer, default=0)

    dish = relationship('Dish', back_populates='dish_recipes')
    recipe = relationship('Recipe')


class DishIngredient(Base):
    """Standalone ingredients added directly to a dish (not from a recipe)."""
    __tablename__ = 'dish_ingredients'

    id = Column(Integer, primary_key=True)
    dish_id = Column(Integer, ForeignKey('dishes.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)
    name = Column(String, nullable=False)
    quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)  # kg, st, piece, liter
    cost_per_unit = Column(Float, nullable=True)
    cost = Column(Float, nullable=True)  # quantity * cost_per_unit (after conversion)
    trimming_pct = Column(Float, default=0)
    adjusted_cost = Column(Float, nullable=True)
    pieces_per_kg = Column(Float, nullable=True)  # For AI unit conversion (e.g. 7 langoustines/kg)
    notes = Column(String, nullable=True)
    order = Column(Integer, default=0)

    dish = relationship('Dish', back_populates='dish_ingredients')
    product = relationship('Product')


class Menu(Base):
    """A menu — lunch, tasting, or personalized."""
    __tablename__ = 'menus'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    menu_type = Column(String, nullable=False)  # lunch, tasting, personalized
    description = Column(Text, nullable=True)
    added_by = Column(String, nullable=True)
    total_cost = Column(Float, nullable=True)  # Sum of all section costs
    cost_per_menu = Column(Float, nullable=True)  # Total cost for 1 menu served
    selling_price = Column(Float, nullable=True)  # Menu price charged to customer
    food_cost_pct = Column(Float, nullable=True)  # (cost_per_menu / selling_price) * 100
    margin = Column(Float, nullable=True)  # selling_price - cost_per_menu
    photos = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sections = relationship('MenuSection', back_populates='menu', cascade='all, delete-orphan')


class MenuSection(Base):
    """A section within a menu — e.g. Starter, Main Course, Dessert."""
    __tablename__ = 'menu_sections'

    id = Column(Integer, primary_key=True)
    menu_id = Column(Integer, ForeignKey('menus.id'), nullable=False)
    name = Column(String, nullable=False)  # "Starter", "Main Course", "Snacks", etc.
    description = Column(Text, nullable=True)
    order = Column(Integer, default=0)

    menu = relationship('Menu', back_populates='sections')
    items = relationship('MenuSectionItem', back_populates='section', cascade='all, delete-orphan')


class MenuSectionItem(Base):
    """An item in a menu section — points to a dish."""
    __tablename__ = 'menu_section_items'

    id = Column(Integer, primary_key=True)
    section_id = Column(Integer, ForeignKey('menu_sections.id'), nullable=False)
    dish_id = Column(Integer, ForeignKey('dishes.id'), nullable=True)  # Link to existing dish
    name = Column(String, nullable=True)  # Display name override
    portions = Column(Float, default=1)  # How many portions of this dish per menu
    cost = Column(Float, nullable=True)  # dish.cost_per_serving * portions
    order = Column(Integer, default=0)

    section = relationship('MenuSection', back_populates='items')
    dish = relationship('Dish')


def init_db():
    """Create all tables and return a session."""
    Base.metadata.create_all(engine)
    return Session()
