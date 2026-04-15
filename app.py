"""
Restaurant Manager API — Flask backend for price tracking, supplier management,
and smart food search. Connects to Fortnox for invoice/supplier data.
"""
import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from sqlalchemy import func, or_, extract

from models import (
    Session, Base, engine, Supplier, Product, Invoice,
    InvoiceLineItem, PriceHistory, SyncLog, FortnoxToken, init_db,
    Recipe, RecipeIngredient, Dish, DishComponent, Menu, MenuItem,
    Inventory, InventoryItem
)
from config import FORTNOX_CLIENT_ID, FORTNOX_CLIENT_SECRET, FORTNOX_REDIRECT_URI
from food_dictionary import search_food_terms

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Initialize database
init_db()

# Run lightweight migrations — add columns that may be missing from older schemas
def run_migrations():
    """Add new columns to existing tables if they don't exist yet."""
    from sqlalchemy import text
    db = Session()
    try:
        # Drop recipe-related tables so create_all() rebuilds them with correct schema
        drop_tables = [
            "DROP TABLE IF EXISTS menu_items CASCADE",
            "DROP TABLE IF EXISTS menus CASCADE",
            "DROP TABLE IF EXISTS dish_components CASCADE",
            "DROP TABLE IF EXISTS dishes CASCADE",
            "DROP TABLE IF EXISTS recipe_ingredients CASCADE",
            "DROP TABLE IF EXISTS recipes CASCADE",
        ]
        for sql in drop_tables:
            try:
                db.execute(text(sql))
            except Exception as e:
                logger.warning(f"Drop table skipped: {e}")
        db.commit()
        logger.info("Dropped recipe tables for schema rebuild")

        # Re-create all tables with correct schema
        Base.metadata.create_all(engine)
        logger.info("Tables recreated with correct schema")

        migrations = [
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS package_weight_grams FLOAT",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category VARCHAR",
            "ALTER TABLE invoice_line_items ADD COLUMN IF NOT EXISTS package_weight_grams FLOAT",
        ]
        for sql in migrations:
            try:
                db.execute(text(sql))
            except Exception as e:
                logger.warning(f"Migration skipped: {e}")
        db.commit()
        logger.info("Database migrations complete")
    except Exception as e:
        db.rollback()
        logger.error(f"Migration error: {e}")
    finally:
        db.close()

run_migrations()

# Register recipe/dish/menu routes
from recipe_routes import recipe_bp
app.register_blueprint(recipe_bp)

# Register inventory routes
from inventory_routes import inventory_bp
app.register_blueprint(inventory_bp)

# Import sync service after DB init
from sync_service import sync_all, sync_status, start_auto_sync, re_extract_all_invoices, extract_invoice_products


# --- Health ---

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# --- Fortnox OAuth ---

@app.route("/api/fortnox/auth", methods=["GET"])
def fortnox_auth():
    """Redirect user to Fortnox OAuth authorization page."""
    auth_url = (
        f"https://apps.fortnox.se/oauth-v1/auth"
        f"?client_id={FORTNOX_CLIENT_ID}"
        f"&redirect_uri={FORTNOX_REDIRECT_URI}"
        f"&scope=supplierinvoice%20supplier%20article"
        f"&state=restaurant_manager"
        f"&response_type=code"
    )
    return jsonify({"auth_url": auth_url})


@app.route("/api/fortnox/callback", methods=["GET"])
def fortnox_callback():
    """Handle OAuth callback from Fortnox — exchange code for tokens."""
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "No authorization code received"}), 400

    import requests as http_requests
    try:
        response = http_requests.post(
            "https://apps.fortnox.se/oauth-v1/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": FORTNOX_REDIRECT_URI
            },
            auth=(FORTNOX_CLIENT_ID, FORTNOX_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30
        )
        response.raise_for_status()
        token_data = response.json()

        # Store tokens
        db = Session()
        token = FortnoxToken(
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        )
        db.add(token)
        db.commit()
        db.close()

        logger.info("Fortnox OAuth tokens stored successfully")

        # Redirect to frontend
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
        return redirect(f"{frontend_url}/settings?fortnox=connected")

    except Exception as e:
        logger.error(f"Fortnox OAuth error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/fortnox/status", methods=["GET"])
def fortnox_status():
    """Check if Fortnox is connected (has valid tokens)."""
    db = Session()
    try:
        token = db.query(FortnoxToken).order_by(FortnoxToken.id.desc()).first()
        if token:
            return jsonify({
                "connected": True,
                "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                "updated_at": token.updated_at.isoformat() if token.updated_at else None
            })
        return jsonify({"connected": False})
    finally:
        db.close()


# --- Suppliers ---

@app.route("/api/suppliers", methods=["GET"])
def get_suppliers():
    db = Session()
    try:
        suppliers = db.query(Supplier).order_by(Supplier.name).all()
        return jsonify([{
            "id": s.id,
            "fortnox_id": s.fortnox_id,
            "name": s.name,
            "email": s.email,
            "phone": s.phone,
            "city": s.city,
            "org_number": s.org_number,
            "product_count": db.query(Product).filter_by(supplier_id=s.id).count(),
            "invoice_count": db.query(Invoice).filter_by(supplier_id=s.id).count()
        } for s in suppliers])
    finally:
        db.close()


@app.route("/api/suppliers/<int:supplier_id>", methods=["GET"])
def get_supplier(supplier_id):
    db = Session()
    try:
        s = db.query(Supplier).filter_by(id=supplier_id).first()
        if not s:
            return jsonify({"error": "Supplier not found"}), 404

        products = db.query(Product).filter_by(supplier_id=s.id).all()
        invoices = db.query(Invoice).filter_by(supplier_id=s.id).order_by(Invoice.invoice_date.desc()).limit(20).all()

        return jsonify({
            "id": s.id,
            "fortnox_id": s.fortnox_id,
            "name": s.name,
            "email": s.email,
            "phone": s.phone,
            "address": s.address,
            "city": s.city,
            "zip_code": s.zip_code,
            "org_number": s.org_number,
            "products": [{
                "id": p.id,
                "name": p.name,
                "unit": p.unit,
                "current_price": p.current_price,
                "category": p.category
            } for p in products],
            "invoices": [{
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "total_amount": inv.total_amount,
                "is_paid": inv.is_paid
            } for inv in invoices]
        })
    finally:
        db.close()


# --- Products ---

@app.route("/api/products", methods=["GET"])
def get_products():
    db = Session()
    try:
        products = db.query(Product).order_by(Product.name).all()
        results = []
        for p in products:
            prices = db.query(PriceHistory).filter_by(
                product_id=p.id
            ).order_by(PriceHistory.date.desc()).limit(2).all()

            price_change = None
            if len(prices) >= 2 and prices[1].price > 0:
                price_change = round(
                    ((prices[0].price - prices[1].price) / prices[1].price) * 100, 1
                )

            results.append({
                "id": p.id,
                "name": p.name,
                "article_number": p.fortnox_article_number,
                "supplier_id": p.supplier_id,
                "supplier_name": p.supplier.name if p.supplier else None,
                "unit": p.unit,
                "current_price": p.current_price,
                "category": p.category,
                "package_weight_grams": p.package_weight_grams,
                "price_change_percent": price_change
            })
        return jsonify(results)
    finally:
        db.close()


@app.route("/api/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    db = Session()
    try:
        p = db.query(Product).filter_by(id=product_id).first()
        if not p:
            return jsonify({"error": "Product not found"}), 404

        price_history = db.query(PriceHistory).filter_by(
            product_id=p.id
        ).order_by(PriceHistory.date.desc()).all()

        return jsonify({
            "id": p.id,
            "name": p.name,
            "article_number": p.fortnox_article_number,
            "supplier_id": p.supplier_id,
            "supplier_name": p.supplier.name if p.supplier else None,
            "unit": p.unit,
            "current_price": p.current_price,
            "category": p.category,
            "package_weight_grams": p.package_weight_grams,
            "price_history": [{
                "price": ph.price,
                "date": ph.date.isoformat() if ph.date else None,
                "invoice_number": ph.invoice_number
            } for ph in price_history]
        })
    finally:
        db.close()


@app.route("/api/products/<int:product_id>/price-history", methods=["GET"])
def get_price_history(product_id):
    db = Session()
    try:
        history = db.query(PriceHistory).filter_by(
            product_id=product_id
        ).order_by(PriceHistory.date.asc()).all()

        return jsonify([{
            "price": h.price,
            "date": h.date.isoformat() if h.date else None,
            "invoice_number": h.invoice_number
        } for h in history])
    finally:
        db.close()


# --- Smart Search (Bilingual) ---

@app.route("/api/search", methods=["GET"])
def search_products():
    """
    Smart bilingual search — search in English or Swedish, find products in both.
    ?q=carrot finds morrot products and vice versa.
    """
    db = Session()
    try:
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify([])

        search_terms = search_food_terms(q)

        conditions = []
        for term in search_terms:
            like_term = f"%{term}%"
            conditions.append(Product.name.ilike(like_term))
            conditions.append(Product.category.ilike(like_term))
            conditions.append(Product.fortnox_article_number.ilike(like_term))

        q_like = f"%{q}%"
        matching_suppliers = db.query(Supplier.id).filter(
            Supplier.name.ilike(q_like)
        ).all()
        supplier_ids = [s.id for s in matching_suppliers]
        if supplier_ids:
            conditions.append(Product.supplier_id.in_(supplier_ids))

        if not conditions:
            return jsonify([])

        products = db.query(Product).filter(
            or_(*conditions)
        ).order_by(Product.name).limit(50).all()

        results = []
        for prod in products:
            latest_prices = db.query(PriceHistory).filter_by(
                product_id=prod.id
            ).order_by(PriceHistory.date.desc()).limit(2).all()

            price_change = None
            if len(latest_prices) >= 2 and latest_prices[1].price > 0:
                price_change = round(
                    ((latest_prices[0].price - latest_prices[1].price) / latest_prices[1].price) * 100, 1
                )

            normalized_price_per_kg = None
            if prod.current_price and prod.current_price > 0:
                unit_lower = (prod.unit or "").lower()
                if unit_lower == "kg":
                    normalized_price_per_kg = prod.current_price
                elif unit_lower in ("g", "gram"):
                    normalized_price_per_kg = prod.current_price * 1000
                elif prod.package_weight_grams and prod.package_weight_grams > 0:
                    normalized_price_per_kg = round(
                        prod.current_price / (prod.package_weight_grams / 1000), 2
                    )

            results.append({
                "id": prod.id,
                "name": prod.name,
                "article_number": prod.fortnox_article_number,
                "supplier_id": prod.supplier_id,
                "supplier_name": prod.supplier.name if prod.supplier else None,
                "unit": prod.unit,
                "category": prod.category,
                "current_price": prod.current_price,
                "package_weight_grams": prod.package_weight_grams,
                "normalized_price_per_kg": normalized_price_per_kg,
                "price_change_percent": price_change,
                "matched_terms": list(search_terms)[:5]
            })

        return jsonify(results)
    finally:
        db.close()


# --- Invoices ---

@app.route("/api/invoices", methods=["GET"])
def get_invoices():
    db = Session()
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        supplier_id = request.args.get("supplier_id", type=int)

        query = db.query(Invoice).order_by(Invoice.invoice_date.desc())

        if supplier_id:
            query = query.filter_by(supplier_id=supplier_id)

        total = query.count()
        invoices = query.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            "invoices": [{
                "id": inv.id,
                "fortnox_id": inv.fortnox_id,
                "supplier_name": inv.supplier_name,
                "supplier_id": inv.supplier_id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "total_amount": inv.total_amount,
                "is_paid": inv.is_paid,
                "line_item_count": db.query(InvoiceLineItem).filter_by(invoice_id=inv.id).count()
            } for inv in invoices],
            "total": total,
            "page": page,
            "per_page": per_page
        })
    finally:
        db.close()


@app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
def get_invoice(invoice_id):
    db = Session()
    try:
        inv = db.query(Invoice).filter_by(id=invoice_id).first()
        if not inv:
            return jsonify({"error": "Invoice not found"}), 404

        line_items = db.query(InvoiceLineItem).filter_by(invoice_id=inv.id).all()

        return jsonify({
            "id": inv.id,
            "fortnox_id": inv.fortnox_id,
            "supplier_name": inv.supplier_name,
            "supplier_id": inv.supplier_id,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "total_amount": inv.total_amount,
            "vat_amount": inv.vat_amount,
            "currency": inv.currency,
            "is_paid": inv.is_paid,
            "line_items": [{
                "id": li.id,
                "article_number": li.article_number,
                "description": li.description,
                "quantity": li.quantity,
                "unit": li.unit,
                "unit_price": li.unit_price,
                "total": li.total,
                "package_weight_grams": li.package_weight_grams,
                "product_id": li.product_id
            } for li in line_items]
        })
    finally:
        db.close()


# --- Spend Analytics ---

@app.route("/api/spend/by-month", methods=["GET"])
def spend_by_month():
    """Monthly spend totals — for the spend chart on the dashboard."""
    db = Session()
    try:
        months = request.args.get("months", 12, type=int)
        cutoff = datetime.utcnow() - timedelta(days=months * 31)

        results = db.query(
            extract("year", Invoice.invoice_date).label("year"),
            extract("month", Invoice.invoice_date).label("month"),
            func.sum(Invoice.total_amount).label("total"),
            func.count(Invoice.id).label("invoice_count")
        ).filter(
            Invoice.invoice_date >= cutoff,
            Invoice.total_amount.isnot(None)
        ).group_by("year", "month").order_by("year", "month").all()

        return jsonify([{
            "year": int(r.year),
            "month": int(r.month),
            "total": round(float(r.total), 2),
            "invoice_count": r.invoice_count
        } for r in results])
    finally:
        db.close()


@app.route("/api/spend/by-supplier", methods=["GET"])
def spend_by_supplier():
    """Spend broken down by supplier — for the dashboard pie chart."""
    db = Session()
    try:
        months = request.args.get("months", 12, type=int)
        cutoff = datetime.utcnow() - timedelta(days=months * 31)

        results = db.query(
            Invoice.supplier_name,
            Invoice.supplier_id,
            func.sum(Invoice.total_amount).label("total"),
            func.count(Invoice.id).label("invoice_count")
        ).filter(
            Invoice.invoice_date >= cutoff,
            Invoice.total_amount.isnot(None)
        ).group_by(
            Invoice.supplier_name, Invoice.supplier_id
        ).order_by(func.sum(Invoice.total_amount).desc()).all()

        return jsonify([{
            "supplier_name": r.supplier_name,
            "supplier_id": r.supplier_id,
            "total": round(float(r.total), 2),
            "invoice_count": r.invoice_count
        } for r in results])
    finally:
        db.close()


# --- Price Alerts ---

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    """
    Smart price alerts — detects significant price changes.
    Supports unit normalization (compares per-kg prices when package_weight is known).
    """
    db = Session()
    try:
        threshold = request.args.get("threshold", 5.0, type=float)
        days = request.args.get("days", 90, type=int)
        cutoff = datetime.utcnow() - timedelta(days=days)

        products = db.query(Product).all()
        alerts = []

        for product in products:
            prices = db.query(PriceHistory).filter(
                PriceHistory.product_id == product.id,
                PriceHistory.date >= cutoff
            ).order_by(PriceHistory.date.desc()).limit(10).all()

            if len(prices) < 2:
                continue

            current = prices[0]
            previous = prices[1]

            if previous.price <= 0:
                continue

            change_pct = ((current.price - previous.price) / previous.price) * 100

            if abs(change_pct) >= threshold:
                normalized_current = None
                normalized_previous = None
                unit_lower = (product.unit or "").lower()

                if unit_lower == "kg":
                    normalized_current = current.price
                    normalized_previous = previous.price
                elif unit_lower in ("g", "gram"):
                    normalized_current = current.price * 1000
                    normalized_previous = previous.price * 1000
                elif product.package_weight_grams and product.package_weight_grams > 0:
                    kg_factor = product.package_weight_grams / 1000
                    normalized_current = round(current.price / kg_factor, 2)
                    normalized_previous = round(previous.price / kg_factor, 2)

                alerts.append({
                    "product_id": product.id,
                    "product_name": product.name,
                    "supplier_name": product.supplier.name if product.supplier else None,
                    "unit": product.unit,
                    "current_price": current.price,
                    "previous_price": previous.price,
                    "change_percent": round(change_pct, 1),
                    "direction": "up" if change_pct > 0 else "down",
                    "date": current.date.isoformat() if current.date else None,
                    "previous_date": previous.date.isoformat() if previous.date else None,
                    "invoice_number": current.invoice_number,
                    "package_weight_grams": product.package_weight_grams,
                    "normalized_price_per_kg": normalized_current,
                    "previous_normalized_per_kg": normalized_previous
                })

        alerts.sort(key=lambda a: abs(a["change_percent"]), reverse=True)

        return jsonify(alerts)
    finally:
        db.close()


# --- Dashboard Stats ---

@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    db = Session()
    try:
        supplier_count = db.query(Supplier).count()
        product_count = db.query(Product).count()
        invoice_count = db.query(Invoice).count()

        total_spend = db.query(func.sum(Invoice.total_amount)).scalar() or 0

        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_spend = db.query(func.sum(Invoice.total_amount)).filter(
            Invoice.invoice_date >= month_start
        ).scalar() or 0

        price_alerts = 0
        products = db.query(Product).all()
        for p in products:
            prices = db.query(PriceHistory).filter_by(
                product_id=p.id
            ).order_by(PriceHistory.date.desc()).limit(2).all()
            if len(prices) >= 2 and prices[1].price > 0:
                change = ((prices[0].price - prices[1].price) / prices[1].price) * 100
                if change > 5:
                    price_alerts += 1

        last_sync_log = db.query(SyncLog).filter_by(
            status="completed"
        ).order_by(SyncLog.completed_at.desc()).first()

        return jsonify({
            "suppliers": supplier_count,
            "products": product_count,
            "invoices": invoice_count,
            "total_spend": round(float(total_spend), 2),
            "month_spend": round(float(month_spend), 2),
            "price_alerts": price_alerts,
            "last_sync": last_sync_log.completed_at.isoformat() if last_sync_log and last_sync_log.completed_at else None,
            "sync_status": sync_status
        })
    finally:
        db.close()


# --- Sync Controls ---

@app.route("/api/sync", methods=["POST"])
def trigger_sync():
    """Trigger a manual full sync."""
    import threading
    if sync_status["is_syncing"]:
        return jsonify({"message": "Sync already in progress", "status": sync_status}), 409

    thread = threading.Thread(target=sync_all, daemon=True)
    thread.start()
    return jsonify({"message": "Sync started", "status": sync_status})


@app.route("/api/sync/status", methods=["GET"])
def get_sync_status():
    return jsonify(sync_status)


@app.route("/api/sync/history", methods=["GET"])
def sync_history():
    db = Session()
    try:
        logs = db.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(20).all()
        return jsonify([{
            "id": log.id,
            "sync_type": log.sync_type,
            "status": log.status,
            "records_synced": log.records_synced,
            "error_message": log.error_message,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None
        } for log in logs])
    finally:
        db.close()


# --- Re-extract (backfill package weights) ---

@app.route("/api/re-extract", methods=["POST"])
def trigger_re_extract():
    """
    Re-extract all invoice PDFs to backfill package_weight_grams.
    This clears existing line items and re-processes every invoice.
    Use with caution — takes a long time and costs API credits.
    """
    import threading
    if sync_status["is_syncing"]:
        return jsonify({"message": "Sync/extraction already in progress", "status": sync_status}), 409

    invoice_id = request.json.get("invoice_id") if request.is_json else None

    def _run():
        sync_status["is_syncing"] = True
        sync_status["progress"] = "Re-extracting invoices..."
        try:
            if invoice_id:
                count = extract_invoice_products(invoice_id=invoice_id, force=True)
            else:
                count = re_extract_all_invoices()
            sync_status["products_extracted"] = count
            sync_status["progress"] = f"Re-extraction complete! {count} products extracted."
        except Exception as e:
            sync_status["last_error"] = str(e)
            sync_status["progress"] = f"Re-extraction failed: {e}"
        finally:
            sync_status["is_syncing"] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    msg = f"Re-extracting invoice {invoice_id}" if invoice_id else "Re-extracting ALL invoices"
    return jsonify({"message": msg, "status": sync_status})


# --- Start ---

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Restaurant Manager API on port {port}")
    start_auto_sync()
    app.run(host="0.0.0.0", port=port, debug=False)
