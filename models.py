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


def init_db():
    """Create all tables and return a session."""
    Base.metadata.create_all(engine)
    return Session()
