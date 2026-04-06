"""
Restaurant Manager API — Flask backend deployed on Railway.
Connects to Fortnox for live invoice data, serves to Lovable frontend.
"""
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS
from datetime import datetime
import os

from config import PORT, DEBUG, SECRET_KEY
from models import (
    init_db, Session, Supplier, Product, Invoice,
    InvoiceLineItem, PriceHistory, SyncLog
)
from fortnox_client import FortnoxClient
from sync_service import SyncService
from inventory_routes import inventory_bp

# ── App Setup ──────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app)  # Allow Lovable frontend to connect
app.register_blueprint(inventory_bp)

# Create database tables on startup (MUST happen before FortnoxClient)
init_db()

# Run migrations for new columns (safe to re-run — uses IF NOT EXISTS)
from sqlalchemy import text as _text
from models import engine as _engine
_migration_sql = [
    "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS added_by TEXT",
    "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS seasoning_pct FLOAT DEFAULT 0",
    "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS photos TEXT",
    "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
    "ALTER TABLE recipe_ingredients ADD COLUMN IF NOT EXISTS trimming_pct FLOAT DEFAULT 0",
    "ALTER TABLE recipe_ingredients ADD COLUMN IF NOT EXISTS adjusted_cost FLOAT",
    "ALTER TABLE recipe_ingredients ADD COLUMN IF NOT EXISTS notes TEXT",
]
try:
    with _engine.connect() as _conn:
        for _sql in _migration_sql:
            _conn.execute(_text(_sql))
        _conn.commit()
    print("DB migration: new columns added successfully")
except Exception as _e:
    print(f"DB migration note: {_e}")

# Initialize Fortnox client
fortnox = FortnoxClient(Session)
sync = SyncService(fortnox)


# ── Health Check ───────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def health():
    return jsonify({
        'status': 'running',
        'app': 'Restaurant Manager API',
        'fortnox_connected': fortnox.is_connected(),
        'timestamp': datetime.utcnow().isoformat()
    })


# ── OAuth2 Flow (one-time setup) ──────────────────────────────────────

@app.route('/auth/fortnox', methods=['GET'])
def auth_fortnox():
    """Redirect to Fortnox OAuth2 login page."""
    auth_url = fortnox.get_auth_url()
    return redirect(auth_url)


@app.route('/auth/callback', methods=['GET'])
def auth_callback():
    """Handle Fortnox OAuth2 callback after user authorizes."""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        return jsonify({'error': error, 'description': request.args.get('error_description')}), 400

    if not code:
        return jsonify({'error': 'No authorization code received'}), 400

    try:
        tokens = fortnox.exchange_code(code)
        return jsonify({
            'status': 'connected',
            'message': 'Fortnox authorization successful! You can now sync data.',
            'expires_in': tokens.get('expires_in', 3600)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/auth/status', methods=['GET'])
def auth_status():
    """Check if Fortnox is connected."""
    return jsonify({
        'connected': fortnox.is_connected(),
        'has_access_token': bool(fortnox.access_token),
        'has_refresh_token': bool(fortnox.refresh_token),
        'token_expires_at': fortnox.expires_at.isoformat() if fortnox.expires_at else None
    })


# ── Sync Endpoints ────────────────────────────────────────────────────

@app.route('/api/sync', methods=['POST'])
def sync_all():
    """Trigger a full sync from Fortnox (suppliers + articles + invoices)."""
    if not fortnox.is_connected():
        return jsonify({'error': 'Fortnox not connected. Visit /auth/fortnox first.'}), 401
    results = sync.sync_all()
    return jsonify(results)


@app.route('/api/sync/invoices', methods=['POST'])
def sync_invoices():
    """Sync only invoices from Fortnox."""
    if not fortnox.is_connected():
        return jsonify({'error': 'Fortnox not connected'}), 401
    result = sync.sync_invoices()
    return jsonify(result)


@app.route('/api/sync/suppliers', methods=['POST'])
def sync_suppliers_endpoint():
    """Sync only suppliers from Fortnox."""
    if not fortnox.is_connected():
        return jsonify({'error': 'Fortnox not connected'}), 401
    result = sync.sync_suppliers()
    return jsonify(result)


@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    """Get latest sync logs."""
    db = Session()
    try:
        logs = db.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(20).all()
        return jsonify([{
            'id': log.id,
            'type': log.sync_type,
            'status': log.status,
            'records_synced': log.records_synced,
            'error': log.error_message,
            'started_at': log.started_at.isoformat() if log.started_at else None,
            'completed_at': log.completed_at.isoformat() if log.completed_at else None
        } for log in logs])
    finally:
        db.close()


# ── Data Endpoints (for Lovable frontend) ─────────────────────────────

@app.route('/api/invoices', methods=['GET'])
def get_invoices():
    """Get all invoices with optional filters."""
    db = Session()
    try:
        query = db.query(Invoice).order_by(Invoice.invoice_date.desc())

        # Optional filters
        supplier_id = request.args.get('supplier_id')
        if supplier_id:
            query = query.filter(Invoice.supplier_id == int(supplier_id))

        limit = request.args.get('limit', 100, type=int)
        query = query.limit(limit)

        invoices = query.all()
        return jsonify([{
            'id': inv.id,
            'fortnox_id': inv.fortnox_id,
            'supplier_name': inv.supplier_name,
            'invoice_number': inv.invoice_number,
            'invoice_date': inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else None,
            'due_date': inv.due_date.strftime('%Y-%m-%d') if inv.due_date else None,
            'total_amount': inv.total_amount,
            'vat_amount': inv.vat_amount,
            'currency': inv.currency,
            'is_paid': inv.is_paid,
            'synced_at': inv.synced_at.isoformat() if inv.synced_at else None,
            'line_items_count': len(inv.line_items)
        } for inv in invoices])
    finally:
        db.close()


@app.route('/api/invoices/<int:invoice_id>', methods=['GET'])
def get_invoice_detail(invoice_id):
    """Get single invoice with all line items."""
    db = Session()
    try:
        inv = db.query(Invoice).get(invoice_id)
        if not inv:
            return jsonify({'error': 'Invoice not found'}), 404

        return jsonify({
            'id': inv.id,
            'fortnox_id': inv.fortnox_id,
            'supplier_name': inv.supplier_name,
            'invoice_number': inv.invoice_number,
            'invoice_date': inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else None,
            'due_date': inv.due_date.strftime('%Y-%m-%d') if inv.due_date else None,
            'total_amount': inv.total_amount,
            'vat_amount': inv.vat_amount,
            'currency': inv.currency,
            'is_paid': inv.is_paid,
            'line_items': [{
                'id': li.id,
                'article_number': li.article_number,
                'description': li.description,
                'quantity': li.quantity,
                'unit': li.unit,
                'unit_price': li.unit_price,
                'total': li.total
            } for li in inv.line_items]
        })
    finally:
        db.close()


@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    """Get all suppliers."""
    db = Session()
    try:
        suppliers = db.query(Supplier).order_by(Supplier.name).all()
        return jsonify([{
            'id': sup.id,
            'fortnox_id': sup.fortnox_id,
            'name': sup.name,
            'email': sup.email,
            'phone': sup.phone,
            'city': sup.city,
            'org_number': sup.org_number,
            'invoice_count': len(sup.invoices),
            'product_count': len(sup.products)
        } for sup in suppliers])
    finally:
        db.close()


@app.route('/api/products', methods=['GET'])
def get_products():
    """Get all products with current prices."""
    db = Session()
    try:
        products = db.query(Product).order_by(Product.name).all()
        return jsonify([{
            'id': prod.id,
            'name': prod.name,
            'article_number': prod.fortnox_article_number,
            'supplier_id': prod.supplier_id,
            'supplier_name': prod.supplier.name if prod.supplier else None,
            'unit': prod.unit,
            'category': prod.category,
            'current_price': prod.current_price
        } for prod in products])
    finally:
        db.close()


@app.route('/api/price-history/<int:product_id>', methods=['GET'])
def get_price_history(product_id):
    """Get price history for a specific product."""
    db = Session()
    try:
        history = db.query(PriceHistory).filter_by(
            product_id=product_id
        ).order_by(PriceHistory.date.desc()).all()

        return jsonify([{
            'price': h.price,
            'date': h.date.strftime('%Y-%m-%d') if h.date else None,
            'invoice_number': h.invoice_number
        } for h in history])
    finally:
        db.close()


@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    """
    Get price alerts — products where price changed >= 10% between
    the two most recent invoices.
    """
    db = Session()
    try:
        products = db.query(Product).all()
        alerts = []

        for prod in products:
            history = db.query(PriceHistory).filter_by(
                product_id=prod.id
            ).order_by(PriceHistory.date.desc()).limit(2).all()

            if len(history) >= 2 and history[1].price > 0:
                change = ((history[0].price - history[1].price) / history[1].price) * 100
                if abs(change) >= 10:
                    alerts.append({
                        'product_id': prod.id,
                        'product': prod.name,
                        'supplier': prod.supplier.name if prod.supplier else None,
                        'change_percent': round(change, 1),
                        'old_price': history[1].price,
                        'new_price': history[0].price,
                        'date': history[0].date.strftime('%Y-%m-%d') if history[0].date else None
                    })

        # Sort by largest change first
        alerts.sort(key=lambda x: abs(x['change_percent']), reverse=True)
        return jsonify(alerts)
    finally:
        db.close()


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """Dashboard summary data for the Lovable frontend."""
    db = Session()
    try:
        total_invoices = db.query(Invoice).count()
        total_suppliers = db.query(Supplier).count()
        total_products = db.query(Product).count()
        unpaid_invoices = db.query(Invoice).filter_by(is_paid=False).count()

        # Total spend
        from sqlalchemy import func
        total_spend = db.query(func.sum(Invoice.total_amount)).scalar() or 0

        # Latest sync
        latest_sync = db.query(SyncLog).filter_by(
            status='success'
        ).order_by(SyncLog.completed_at.desc()).first()

        return jsonify({
            'total_invoices': total_invoices,
            'total_suppliers': total_suppliers,
            'total_products': total_products,
            'unpaid_invoices': unpaid_invoices,
            'total_spend': round(total_spend, 2),
            'currency': 'SEK',
            'last_sync': latest_sync.completed_at.isoformat() if latest_sync else None,
            'fortnox_connected': fortnox.is_connected()
        })
    finally:
        db.close()


# ── Run ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=DEBUG, host='0.0.0.0', port=PORT)
