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
            'scope': 'supplierinvoice supplier
