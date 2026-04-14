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
        """Run a full sync: suppliers → articles → invoices."""
        results = {}
        results['suppliers'] = self.sync_suppliers()
        results['articles'] = self.sync_articles()
        results['invoices'] = self.sync_invoices()
        return results

    def sync_suppliers(self):
        """Sync all suppliers from Fortnox."""
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
        """Sync all articles/products from Fortnox."""
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
            db.rollback()
            log.status = 'error'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()

    def sync_invoices(self):
        """
        Sync supplier invoices from Fortnox.
        Creates invoice records with basic metadata from the API.
        """
        db = Session()
        log = SyncLog(sync_type='invoices', status='in_progress')
        db.add(log)
        db.commit()

        try:
            fortnox_invoices = self.fortnox.get_all_pages(self.fortnox.get_supplier_invoices)
            count = 0
            new_invoices = 0

            for fi in fortnox_invoices:
                given_number = str(fi.get('GivenNumber', ''))

                # Skip if already synced
                existing = db.query(Invoice).filter_by(fortnox_id=given_number).first()
                if existing:
                    count += 1
                    continue

                # Get full invoice detail
                try:
                    detail = self.fortnox.get_supplier_invoice_detail(given_number)
                except Exception:
                    continue

                # Find supplier
                supplier_number = str(detail.get('SupplierNumber', ''))
                supplier = db.query(Supplier).filter_by(fortnox_id=supplier_number).first()

                # Parse dates
                invoice_date = None
                due_date = None
                try:
                    if detail.get('InvoiceDate'):
                        invoice_date = datetime.strptime(detail['InvoiceDate'], '%Y-%m-%d')
                    if detail.get('DueDate'):
                        due_date = datetime.strptime(detail['DueDate'], '%Y-%m-%d')
                except (ValueError, TypeError):
                    pass

                # Create invoice record
                invoice = Invoice(
                    fortnox_id=given_number,
                    supplier_id=supplier.id if supplier else None,
                    supplier_name=detail.get('SupplierName', fi.get('SupplierName', '')),
                    invoice_number=detail.get('InvoiceNumber', ''),
                    invoice_date=invoice_date,
                    due_date=due_date,
                    total_amount=detail.get('Total', 0),
                    vat_amount=detail.get('VAT', 0),
                    currency=detail.get('Currency', 'SEK'),
                    is_paid=detail.get('Balance', 1) == 0,
                    fortnox_raw=json.dumps(detail),
                    synced_at=datetime.utcnow()
                )
                db.add(invoice)
                new_invoices += 1
                count += 1

            db.commit()
            log.status = 'success'
            log.records_synced = count
            log.completed_at = datetime.utcnow()
            db.commit()
            return {
                'status': 'success',
                'invoices_total': count,
                'new_invoices': new_invoices
            }

        except Exception as e:
            db.rollback()
            log.status = 'error'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()

    def _has_real_line_items(self, db, invoice_id):
        """
        Check if an invoice has REAL extracted line items (not just accounting junk).
        Accounting entries from Fortnox have no article_number, no description, qty=0.
        """
        items = db.query(InvoiceLineItem).filter_by(invoice_id=invoice_id).all()
        if not items:
            return False
        # Check if ANY item has real product data
        for item in items:
            if item.article_number or (item.description and item.description not in ('', '[No PDF available]', '[Could not extract products from PDF]')):
                if item.quantity and item.quantity > 0:
                    return True
        return False

    def clear_junk_line_items(self):
        """
        Remove accounting-entry line items that have no real product data.
        These were synced from Fortnox API but contain no useful info.
        Returns count of invoices cleared.
        """
        db = Session()
        try:
            invoices = db.query(Invoice).all()
            cleared = 0
            for inv in invoices:
                if not self._has_real_line_items(db, inv.id):
                    junk = db.query(InvoiceLineItem).filter_by(invoice_id=inv.id).all()
                    if junk:
                        for item in junk:
                            db.delete(item)
                        cleared += 1
            db.commit()
            return {'status': 'success', 'invoices_cleared': cleared}
        except Exception as e:
            db.rollback()
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()

    def extract_invoice_products(self, invoice_id=None, limit=10):
        """
        Download PDFs from Fortnox and extract product data using Claude API.
        If invoice_id is given, extract just that one.
        Otherwise, extract from invoices that haven't been processed yet.
        Automatically clears junk accounting line items first.
        """
        db = Session()
        log = SyncLog(sync_type='pdf_extraction', status='in_progress')
        db.add(log)
        db.commit()

        try:
            if invoice_id:
                # For a specific invoice, clear its junk line items first
                if not self._has_real_line_items(db, invoice_id):
                    db.query(InvoiceLineItem).filter_by(invoice_id=invoice_id).delete()
                    db.commit()
                invoices = [db.query(Invoice).get(invoice_id)]
                invoices = [inv for inv in invoices if inv]
            else:
                # Find invoices without REAL line items
                all_invoices = db.query(Invoice).order_by(Invoice.invoice_date.desc()).all()
                invoices = []
                for inv in all_invoices:
                    if not self._has_real_line_items(db, inv.id):
                        # Clear any junk line items
                        db.query(InvoiceLineItem).filter_by(invoice_id=inv.id).delete()
                        invoices.append(inv)
                    if len(invoices) >= limit:
                        break
                db.commit()

            extracted = 0
            products_found = 0
            errors = 0

            for invoice in invoices:
                if not invoice.fortnox_id:
                    continue

                logger.info(f'Extracting products from invoice {invoice.fortnox_id} ({invoice.supplier_name})')

                # Download PDF from Fortnox
                pdf_bytes = self.fortnox.get_invoice_pdf(invoice.fortnox_id)
                if not pdf_bytes:
                    logger.warning(f'No PDF found for invoice {invoice.fortnox_id}')
                    # Create a placeholder line item so we don't retry
                    placeholder = InvoiceLineItem(
                        invoice_id=invoice.id,
                        description='[No PDF available]',
                        total=invoice.total_amount or 0
                    )
                    db.add(placeholder)
                    db.commit()
                    continue

                # Extract products using Claude API
                products = extract_products_from_pdf(
                    pdf_bytes,
                    supplier_name=invoice.supplier_name or '',
                    invoice_number=invoice.invoice_number or ''
                )

                if not products:
                    logger.warning(f'No products extracted from invoice {invoice.fortnox_id}')
                    placeholder = InvoiceLineItem(
                        invoice_id=invoice.id,
                        description='[Could not extract products from PDF]',
                        total=invoice.total_amount or 0
                    )
                    db.add(placeholder)
                    db.commit()
                    errors += 1
                    continue

                # Find supplier for linking products
                supplier = db.query(Supplier).get(invoice.supplier_id) if invoice.supplier_id else None

                # Store extracted products
                for prod_data in products:
                    article_number = str(prod_data.get('article_number', '')) or None
                    description = prod_data.get('description', '')
                    quantity = prod_data.get('quantity', 0) or 0
                    unit = prod_data.get('unit', '')
                    unit_price = prod_data.get('unit_price', 0) or 0
                    total = prod_data.get('total', 0) or 0

                    # Try to find or create a product record
                    product = None
                    if article_number:
                        # Look for existing product by article number + supplier
                        product = db.query(Product).filter_by(
                            fortnox_article_number=article_number,
                            supplier_id=supplier.id if supplier else None
                        ).first()

                        if not product:
                            # Try without supplier filter
                            product = db.query(Product).filter_by(
                                fortnox_article_number=article_number
                            ).first()

                        if not product:
                            product = Product(
                                fortnox_article_number=article_number,
                                name=description or 'Unknown',
                                unit=unit,
                                current_price=unit_price,
                                supplier_id=supplier.id if supplier else None
                            )
                            db.add(product)
                            db.flush()

                    # Create line item
                    line_item = InvoiceLineItem(
                        invoice_id=invoice.id,
                        product_id=product.id if product else None,
                        article_number=article_number,
                        description=description,
                        quantity=quantity,
                        unit=unit,
                        unit_price=unit_price,
                        total=total
                    )
                    db.add(line_item)

                    # Create price history entry
                    if product and unit_price > 0:
                        price_entry = PriceHistory(
                            product_id=product.id,
                            price=unit_price,
                            date=invoice.invoice_date or datetime.utcnow(),
                            invoice_number=invoice.invoice_number or '',
                            invoice_id=invoice.id
                        )
                        db.add(price_entry)
                        product.current_price = unit_price

                    products_found += 1

                db.commit()
                extracted += 1

                # Rate limiting — be gentle with both Fortnox and Claude APIs
                time.sleep(2)

            log.status = 'success'
            log.records_synced = extracted
            log.completed_at = datetime.utcnow()
            db.commit()

            return {
                'status': 'success',
                'invoices_processed': extracted,
                'products_extracted': products_found,
                'errors': errors
            }

        except Exception as e:
            db.rollback()
            log.status = 'error'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.commit()
            logger.error(f'PDF extraction error: {e}')
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()
