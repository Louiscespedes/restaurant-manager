"""
Fortnox API client — handles OAuth2 auth, token refresh, and API calls.
Endpoints: Supplier Invoices, Suppliers, Articles.
"""
import requests
import json
from datetime import datetime, timedelta
from config import (
    FORTNOX_CLIENT_ID, FORTNOX_CLIENT_SECRET, FORTNOX_REDIRECT_URI,
    FORTNOX_API_BASE, FORTNOX_AUTH_URL, FORTNOX_TOKEN_URL
)


class FortnoxClient:
    def __init__(self, session_factory):
        """
        session_factory: a callable that returns a SQLAlchemy session (e.g., models.Session)
        """
        self.session_factory = session_factory
        self.access_token = None
        self.refresh_token = None
        self.expires_at = None
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens from database."""
        from models import FortnoxToken
        db = self.session_factory()
        try:
            token = db.query(FortnoxToken).order_by(FortnoxToken.updated_at.desc()).first()
            if token:
                self.access_token = token.access_token
                self.refresh_token = token.refresh_token
                self.expires_at = token.expires_at
        finally:
            db.close()

    def _save_tokens(self, access_token, refresh_token, expires_in=3600):
        """Save tokens to database."""
        from models import FortnoxToken
        db = self.session_factory()
        try:
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

            # Update existing or create new
            token = db.query(FortnoxToken).first()
            if token:
                token.access_token = access_token
                token.refresh_token = refresh_token
                token.expires_at = self.expires_at
                token.updated_at = datetime.utcnow()
            else:
                token = FortnoxToken(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=self.expires_at
                )
                db.add(token)
            db.commit()
        finally:
            db.close()

    def get_auth_url(self):
        """Generate the OAuth2 authorization URL — user visits this in browser."""
        params = {
            'client_id': FORTNOX_CLIENT_ID,
            'redirect_uri': FORTNOX_REDIRECT_URI,
            'scope': 'supplierinvoice supplier article',
            'state': 'restaurant-manager',
            'access_type': 'offline',
            'response_type': 'code',
        }
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'{FORTNOX_AUTH_URL}?{query}'

    def exchange_code(self, auth_code):
        """Exchange authorization code for access + refresh tokens."""
        response = requests.post(
            FORTNOX_TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'code': auth_code,
                'redirect_uri': FORTNOX_REDIRECT_URI,
            },
            auth=(FORTNOX_CLIENT_ID, FORTNOX_CLIENT_SECRET),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        response.raise_for_status()
        data = response.json()

        self._save_tokens(
            data['access_token'],
            data['refresh_token'],
            data.get('expires_in', 3600)
        )
        return data

    def _refresh_access_token(self):
        """Use refresh token to get a new access token."""
        if not self.refresh_token:
            raise Exception('No refresh token available. Run OAuth authorization first.')

        response = requests.post(
            FORTNOX_TOKEN_URL,
            data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
            },
            auth=(FORTNOX_CLIENT_ID, FORTNOX_CLIENT_SECRET),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        response.raise_for_status()
        data = response.json()

        self._save_tokens(
            data['access_token'],
            data['refresh_token'],
            data.get('expires_in', 3600)
        )

    def _is_token_expired(self):
        """Check if access token is expired or about to expire."""
        if not self.expires_at:
            return True
        # Refresh 5 minutes early to be safe
        return datetime.utcnow() >= (self.expires_at - timedelta(minutes=5))

    def _get_headers(self):
        """Get auth headers, refreshing token if needed."""
        if self._is_token_expired() and self.refresh_token:
            self._refresh_access_token()

        if not self.access_token:
            raise Exception('No access token. Complete OAuth authorization first.')

        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def _get(self, endpoint, params=None):
        """Make authenticated GET request to Fortnox API."""
        headers = self._get_headers()
        url = f'{FORTNOX_API_BASE}/{endpoint}'
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def is_connected(self):
        """Check if we have valid Fortnox credentials."""
        return bool(self.access_token or self.refresh_token)

    # ── Supplier Invoices ──────────────────────────────────────────────

    def get_supplier_invoices(self, page=1, limit=100, from_date=None):
        """
        Fetch supplier invoices (leverantörsfakturor).
        from_date: only get invoices from this date onwards (YYYY-MM-DD)
        """
        params = {'page': page, 'limit': limit}
        if from_date:
            params['lastmodified'] = from_date

        data = self._get('supplierinvoices', params)
        return data.get('SupplierInvoices', [])

    def get_supplier_invoice_detail(self, given_number):
        """Get full details of a single supplier invoice including line items."""
        data = self._get(f'supplierinvoices/{given_number}')
        return data.get('SupplierInvoice', {})

    # ── Suppliers ──────────────────────────────────────────────────────

    def get_suppliers(self, page=1, limit=100):
        """Fetch all suppliers."""
        params = {'page': page, 'limit': limit}
        data = self._get('suppliers', params)
        return data.get('Suppliers', [])

    def get_supplier_detail(self, supplier_number):
        """Get full details of a single supplier."""
        data = self._get(f'suppliers/{supplier_number}')
        return data.get('Supplier', {})

    # ── Articles (Products) ────────────────────────────────────────────

    def get_articles(self, page=1, limit=100):
        """Fetch all articles/products."""
        params = {'page': page, 'limit': limit}
        data = self._get('articles', params)
        return data.get('Articles', [])

    # ── Pagination Helper ──────────────────────────────────────────────

    def get_all_pages(self, fetch_func, **kwargs):
        """Auto-paginate through all pages of a Fortnox endpoint."""
        all_results = []
        page = 1
        while True:
            results = fetch_func(page=page, **kwargs)
            if not results:
                break
            all_results.extend(results)
            if len(results) < 100:  # Last page
                break
            page += 1
        return all_results
