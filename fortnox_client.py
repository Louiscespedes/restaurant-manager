"""
Fortnox API client — handles OAuth2 auth, token refresh, and API calls.
Endpoints: Supplier Invoices, Suppliers, Articles, Invoice PDFs.
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
        self.session_factory = session_factory
        self.access_token = None
        self.refresh_token = None
        self.expires_at = None
        self._load_tokens()

    def _load_tokens(self):
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
        from models import FortnoxToken
        db = self.session_factory()
        try:
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

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
        params = {
            'client_id': FORTNOX_CLIENT_ID,
            'redirect_uri': FORTNOX_REDIRECT_URI,
            'scope': 'supplierinvoice supplier article connectfile',
            'state': 'restaurant-manager',
            'access_type': 'offline',
            'response_type': 'code',
        }
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'{FORTNOX_AUTH_URL}?{query}'

    def exchange_code(self, auth_code):
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
        self._save_tokens(data['access_token'], data['refresh_token'], data.get('expires_in', 3600))
        return data

    def _refresh_access_token(self):
        if not self.refresh_token:
            raise Exception('No refresh token available. Run OAuth authorization first.')
        response = requests.post(
            FORTNOX_TOKEN_URL,
            data={'grant_type': 'refresh_token', 'refresh_token': self.refresh_token},
            auth=(FORTNOX_CLIENT_ID, FORTNOX_CLIENT_SECRET),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        response.raise_for_status()
        data = response.json()
        self._save_tokens(data['access_token'], data['refresh_token'], data.get('expires_in', 3600))

    def _is_token_expired(self):
        if not self.expires_at:
            return True
        return datetime.utcnow() >= (self.expires_at - timedelta(minutes=5))

    def _get_headers(self):
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
        headers = self._get_headers()
        url = f'{FORTNOX_API_BASE}/{endpoint}'
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def is_connected(self):
        return bool(self.access_token or self.refresh_token)

    # ── Supplier Invoices ──────────────────────────────────────────────

    def get_supplier_invoices(self, page=1, limit=100, from_date=None):
        params = {'page': page, 'limit': limit}
        if from_date:
            params['lastmodified'] = from_date
        data = self._get('supplierinvoices', params)
        return data.get('SupplierInvoices', [])

    def get_supplier_invoice_detail(self, given_number):
        data = self._get(f'supplierinvoices/{given_number}')
        return data.get('SupplierInvoice', {})

    # ── Suppliers ──────────────────────────────────────────────────────

    def get_suppliers(self, page=1, limit=100):
        params = {'page': page, 'limit': limit}
        data = self._get('suppliers', params)
        return data.get('Suppliers', [])

    def get_supplier_detail(self, supplier_number):
        data = self._get(f'suppliers/{supplier_number}')
        return data.get('Supplier', {})

    # ── Articles (Products) ────────────────────────────────────────────

    def get_articles(self, page=1, limit=100):
        params = {'page': page, 'limit': limit}
        data = self._get('articles', params)
        return data.get('Articles', [])

    # ── Invoice PDF Files ────────────────────────────────────────────────

    def get_invoice_file_connections(self, given_number):
        """Get file attachments linked to a supplier invoice."""
        try:
            data = self._get('supplierinvoicefileconnections',
                           params={'supplierinvoicenumber': given_number})
            return data.get('SupplierInvoiceFileConnections', [])
        except Exception:
            return []

    def download_file(self, file_id):
        """Download a file from Fortnox archive. Returns raw PDF bytes."""
        headers = self._get_headers()
        headers['Accept'] = 'application/octet-stream'
        url = f'{FORTNOX_API_BASE}/archive/{file_id}'
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.content

    def get_invoice_pdf(self, given_number):
        """Get the PDF for a supplier invoice. Returns PDF bytes or None."""
        connections = self.get_invoice_file_connections(given_number)
        if not connections:
            return None
        file_id = connections[0].get('FileId')
        if not file_id:
            return None
        try:
            return self.download_file(file_id)
        except Exception as e:
            print(f'Error downloading PDF for invoice {given_number}: {e}')
            return None

    # ── Pagination Helper ──────────────────────────────────────────────

    def get_all_pages(self, fetch_func, **kwargs):
        all_results = []
        page = 1
        while True:
            results = fetch_func(page=page, **kwargs)
            if not results:
                break
            all_results.extend(results)
            if len(results) < 100:
                break
            page += 1
        return all_results
