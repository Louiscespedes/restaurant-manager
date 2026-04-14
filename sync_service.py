"""
Sync service — pulls data from Fortnox and saves it to PostgreSQL.
Handles suppliers, articles, and supplier invoices.
For invoices: downloads attached PDFs and uses Claude API to extract product line items.
"""
import json
import logging
import time
from datetime import datetime
from models import (
    Session, Supplier, Product, Invoice, InvoiceLineItem,
    PriceHistory, SyncLog
)
from pdf_extractor import extract_products_from_pdf

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, fortnox_client):
        self.fortnox = fortnox_client

    def sync_all(self):
        results = {}
        results['suppliers'] = self.sync_suppliers()
        results['articles'] = self.sync_articles()
        results['invoices'] = self.sync_invoices()
        return results

    def sync_suppliers(self):
        db = Session()
        log = SyncLog(sync_type='suppliers', status='in_progress')
        db.add(log)
        db.commit()

        try:
            fortnox_suppliers = self.fortnox.get_all_pages(self.fortnox.get_suppliers)
            count = 0

            for fs in fortnox_suppliers:
                supplier_number = str(fs.get('SupplierNumber', ''))
                existing = db.query(Supplier).filter_by(fortnox_id=supplier_number).first()

                if existing:
                    existing.name = fs.get('Name', existing.name)
                    existing.email = fs.get('Email', existing.email)
                    existing.phone = fs.get('Phone1', existing.phone)
                    existing.address = fs.get('Address1', existing.address)
                    existing.city = fs.get('City', existing.city)
                    existing.zip_code = fs.get('ZipCode', existing.zip_code)
                    existing.org_number = fs.get('OrganisationNumber', existing.org_number)
                else:
                    supplier = Supplier(
                        fortnox_id=supplier_number,
                        name=fs.get('Name', 'Unknown'),
                        email=fs.get('Email'),
                        phone=fs.get('Phone1'),
                        address=fs.get('Address1'),
                        city=fs.get('City'),
                        zip_code=fs.get('ZipCode'),
                        org_number=fs.get('OrganisationNumber')
                    )
                    db.add(supplier)
                count += 1

            db.commit()
            log.status = 'success'
            log.records_synced = count
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'success', 'synced': count}

        except Exception as e:
            db.rollback()
            log.status = 'error'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()

    def sync_articles(self):
        db = Session()
        log = SyncLog(sync_type='articles', status='in_progress')
        db.add(log)
        db.commit()

        try:
            fortnox_articles = self.fortnox.get_all_pages(self.fortnox.get_articles)
            count = 0

            for fa in fortnox_articles:
                article_number = str(fa.get('ArticleNumber', ''))
                existing = db.query(Product).filter_by(fortnox_article_number=article_number).first()

                if existing:
                    existing.name = fa.get('Description', existing.name)
                    existing.unit = fa.get('Unit', existing.unit)
                else:
                    product = Product(
                        fortnox_article_number=article_number,
                        name=fa.get('Description', 'Unknown'),
                        unit=fa.get('Unit')
                    )
                    db.add(product)
                count += 1

            db.commit()
            log.status = 'success'
            log.records_synced = count
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'success', 'synced': count}

        except Exception as e:
            db.rollback
