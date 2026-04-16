"""
Database models — PostgreSQL-ready via SQLAlchemy.
Stores suppliers, products, invoices, invoice line items, price history,
recipes, dishes, menus, inventories, and Fortnox OAuth tokens.
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
    fortnox_id = Column(String, unique=True, nullable=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    org_number = Column(String, nullable=True)
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
    category = Column(String, nullable=True)
    package_weight_grams = Column(Float, nullable=True)  # Weight per package for unit normalization
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier = relationship('Supplier', back_populates='products')
    price_history = relationship('PriceHistory', back_populates='product')


class Invoice(Base):
    __tablename__ = 'invoices'

    id = Column(Integer, primary_key=True)
    fortnox_id = Column(String, unique=True, nullable=True)
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
    fortnox_raw = Column(Text, nullable=True)
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
    package_weight_grams = Column(Float, nullable=True)  # Weight per package in grams

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
    """Tracks sync history so we know what has been imported."""
    __tablename__ = 'sync_logs'

    id = Column(Integer, primary_key=True)
    sync_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    records_synced = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


# ─── Recipe System ───────────────────────────────────────────────────────────


class Recipe(Base):
    """A recipe with ingredients, portions, and costing."""
    __tablename__ = 'recipes'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)  # sauce, pastry, garnish, base, etc.
    created_by = Column(String, nullable=True)  # who added the recipe
    portions = Column(Float, nullable=True)  # how many portions this recipe makes
    selling_price = Column(Float, nullable=True)  # optional: what you'd sell one portion for
    seasoning_cost_percent = Column(Float, default=0)  # e.g. 3 means 3% added for seasoning
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ingredients = relationship('RecipeIngredient', back_populates='recipe', cascade='all, delete-orphan')


class RecipeIngredient(Base):
    """A single ingredient line in a recipe."""
    __tablename__ = 'recipe_ingredients'

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey('recipes.id', ondelete='CASCADE'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)  # linked product (for auto-pricing)
    description = Column(String, nullable=False)  # ingredient name as written
    quantity = Column(Float, nullable=True)  # amount needed
    unit = Column(String, nullable=True)  # kg, g, liter, st, etc.
    unit_price = Column(Float, nullable=True)  # price per unit (auto or manual)
    is_manual_price = Column(Boolean, default=False)  # True if user entered price manually
    trimming_percent = Column(Float, default=0)  # waste %, e.g. 20 means 20% lost
    supplier_name = Column(String, nullable=True)  # which supplier this ingredient comes from

    recipe = relationship('Recipe', back_populates='ingredients')
    product = relationship('Product')

    @property
    def effective_unit_price(self):
        """Price adjusted for trimming waste."""
        price = self.unit_price or 0
        if self.trimming_percent and self.trimming_percent > 0:
            return price / (1 - self.trimming_percent / 100)
        return price

    @property
    def line_cost(self):
        """Total cost for this ingredient line."""
        return (self.quantity or 0) * self.effective_unit_price


# ─── Dish System ─────────────────────────────────────────────────────────────


class Dish(Base):
    """A dish = combination of recipes + optional extra products."""
    __tablename__ = 'dishes'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)  # starter, fish, main, snacks, dessert, mignardise, etc.
    selling_price = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    components = relationship('DishComponent', back_populates='dish', cascade='all, delete-orphan')


class DishComponent(Base):
    """A component of a dish — either a recipe or a standalone product."""
    __tablename__ = 'dish_components'

    id = Column(Integer, primary_key=True)
    dish_id = Column(Integer, ForeignKey('dishes.id', ondelete='CASCADE'), nullable=False)
    recipe_id = Column(Integer, ForeignKey('recipes.id'), nullable=True)  # if this component is a recipe
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)  # if this is a standalone product
    description = Column(String, nullable=True)  # label for this component
    quantity = Column(Float, default=1)  # how many portions of the recipe, or quantity of product
    unit = Column(String, nullable=True)
    unit_price = Column(Float, nullable=True)  # for standalone products
    is_manual_price = Column(Boolean, default=False)

    dish = relationship('Dish', back_populates='components')
    recipe = relationship('Recipe')
    product = relationship('Product')


# ─── Menu System ─────────────────────────────────────────────────────────────


class Menu(Base):
    """A menu = collection of dishes organized by courses."""
    __tablename__ = 'menus'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    menu_type = Column(String, nullable=True)  # tasting, lunch, dinner, etc.
    selling_price = Column(Float, nullable=True)  # total menu price
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship('MenuItem', back_populates='menu', cascade='all, delete-orphan')


class MenuItem(Base):
    """A dish placed within a menu, assigned to a course."""
    __tablename__ = 'menu_items'

    id = Column(Integer, primary_key=True)
    menu_id = Column(Integer, ForeignKey('menus.id', ondelete='CASCADE'), nullable=False)
    dish_id = Column(Integer, ForeignKey('dishes.id'), nullable=True)
    course = Column(String, nullable=True)  # snacks, starter, fish, main, dessert, mignardise, etc.
    position = Column(Integer, default=0)  # ordering within the course
    notes = Column(String, nullable=True)

    menu = relationship('Menu', back_populates='items')
    dish = relationship('Dish')


# ─── Inventory System ────────────────────────────────────────────────────────


class Inventory(Base):
    """A monthly inventory snapshot."""
    __tablename__ = 'inventories'

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 1-12
    status = Column(String, default='draft')  # draft, confirmed
    total_value = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship('InventoryItem', back_populates='inventory', cascade='all, delete-orphan')


class InventoryItem(Base):
    """A single product line in an inventory."""
    __tablename__ = 'inventory_items'

    id = Column(Integer, primary_key=True)
    inventory_id = Column(Integer, ForeignKey('inventories.id', ondelete='CASCADE'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)
    recipe_id = Column(Integer, ForeignKey('recipes.id'), nullable=True)  # for finished products
    description = Column(String, nullable=False)
    quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    unit_price = Column(Float, nullable=True)
    is_manual_price = Column(Boolean, default=False)
    category = Column(String, nullable=True)  # fish, meat, vegetables, dairy, wine, cleaning, prepared, etc.
    supplier_name = Column(String, nullable=True)
    total_value = Column(Float, nullable=True)  # quantity * unit_price
    trimming_pct = Column(Float, nullable=True, default=0)  # waste/trimming percentage

    inventory = relationship('Inventory', back_populates='items')
    product = relationship('Product')
    recipe = relationship('Recipe')


def init_db():
    """Create all tables and run migrations."""
    from sqlalchemy import inspect, text
    Base.metadata.create_all(engine)
    try:
        inspector = inspect(engine)
        if 'inventory_items' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('inventory_items')]
            if 'description' not in cols:
                with engine.begin() as conn:
                        conn.execute(text("DROP TABLE inventory_items CASCADE"))
                Base.metadata.create_all(engine)
    except Exception:
        pass
    return Session()
