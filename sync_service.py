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
            db.rollback()
            log.status = 'error'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()

    def sync_invoices(self):
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

                existing = db.query(Invoice).filter_by(fortnox_id=given_number).first()
                if existing:
                    count += 1
                    continue

                try:
                    detail = self.fortnox.get_supplier_invoice_detail(given_number)
                except Exception:
                    continue

                supplier_number = str(detail.get('SupplierNumber', ''))
                supplier = db.query(Supplier).filter_by(fortnox_id=supplier_number).first()

                invoice_date = None
                due_date = None
                try:
                    if detail.get('InvoiceDate'):
                        invoice_date = datetime.strptime(detail['InvoiceDate'], '%Y-%m-%d')
                    if detail.get('DueDate'):
                        due_date = datetime.strptime(detail['DueDate'], '%Y-%m-%d')
                except (ValueError, TypeError):
                    pass

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

    def extract_invoice_products(self, invoice_id=None, limit=10):
        """
        Download PDFs from Fortnox and extract product data using Claude API.
        If invoice_id is given, extract just that one.
        Otherwise, extract from invoices that haven't been processed yet.
        """
        db = Session()
        log = SyncLog(sync_type='pdf_extraction', status='in_progress')
        db.add(log)
        db.commit()

        try:
            if invoice_id:
                invoices = [db.query(Invoice).get(invoice_id)]
                invoices = [inv for inv in invoices if inv]
            else:
                # Find invoices with no line items (not yet extracted)
                invoices = db.query(Invoice).filter(
                    ~Invoice.id.in_(
                        db.query(InvoiceLineItem.invoice_id).distinct()
                    )
                ).order_by(Invoice.invoice_date.desc()).limit(limit).all()

            extracted = 0
            products_found = 0
            errors = 0

            for invoice in invoices:
                if not invoice.fortnox_id:
                    continue

                logger.info(f'Extracting from invoice {invoice.fortnox_id} ({invoice.supplier_name})')

                # Download PDF from Fortnox
                pdf_bytes = self.fortnox.get_invoice_pdf(invoice.fortnox_id)
                if not pdf_bytes:
                    logger.warning(f'No PDF for invoice {invoice.fortnox_id}')
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
                    logger.warning(f'No products from invoice {invoice.fortnox_id}')
                    placeholder = InvoiceLineItem(
                        invoice_id=invoice.id,
                        description='[Could not extract products from PDF]',
                        total=invoice.total_amount or 0
                    )
                    db.add(placeholder)
                    db.commit()
                    errors += 1
                    continue

                supplier = db.query(Supplier).get(invoice.supplier_id) if invoice.supplier_id else None

                for prod_data in products:
                    article_number = str(prod_data.get('article_number', '')) or None
                    description = prod_data.get('description', '')
                    quantity = prod_data.get('quantity', 0) or 0
                    unit = prod_data.get('unit', '')
                    unit_price = prod_data.get('unit_price', 0) or 0
                    total = prod_data.get('total', 0) or 0

                    # Find or create product
                    product = None
                    if article_number:
                        product = db.query(Product).filter_by(
                            fortnox_article_number=article_number,
                            supplier_id=supplier.id if supplier else None
                        ).first()

                        if not product:
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

                # Rate limiting
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
