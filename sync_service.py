"""
Sync service — pulls data from Fortnox and saves it to PostgreSQL.
Handles suppliers, articles, and supplier invoices (with line items + price tracking).
"""
import json
from datetime import datetime
from models import (
    Session, Supplier, Product, Invoice, InvoiceLineItem,
    PriceHistory, SyncLog
)


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
        For each invoice, also fetch line items and create price history entries.
        """
        db = Session()
        log = SyncLog(sync_type='invoices', status='in_progress')
        db.add(log)
        db.commit()

        try:
            fortnox_invoices = self.fortnox.get_all_pages(self.fortnox.get_supplier_invoices)
            count = 0
            new_prices = 0

            for fi in fortnox_invoices:
                given_number = str(fi.get('GivenNumber', ''))

                # Skip if already synced
                existing = db.query(Invoice).filter_by(fortnox_id=given_number).first()
                if existing:
                    count += 1
                    continue

                # Get full invoice detail (includes line items)
                try:
                    detail = self.fortnox.get_supplier_invoice_detail(given_number)
                except Exception:
                    continue

                # Find or match supplier
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
                db.flush()  # Get invoice.id

                # Process line items (rows)
                rows = detail.get('SupplierInvoiceRows', [])
                for row in rows:
                    article_number = row.get('ArticleNumber', '')
                    product = None
                    if article_number:
                        product = db.query(Product).filter_by(
                            fortnox_article_number=str(article_number)
                        ).first()

                    # If no product exists but there's an article number, create one
                    if not product and article_number:
                        product = Product(
                            fortnox_article_number=str(article_number),
                            name=row.get('ItemDescription', 'Unknown'),
                            unit=row.get('Unit', ''),
                            supplier_id=supplier.id if supplier else None
                        )
                        db.add(product)
                        db.flush()

                    line_item = InvoiceLineItem(
                        invoice_id=invoice.id,
                        product_id=product.id if product else None,
                        article_number=str(article_number) if article_number else None,
                        description=row.get('ItemDescription', ''),
                        quantity=row.get('Quantity', 0),
                        unit=row.get('Unit', ''),
                        unit_price=row.get('Price', 0),
                        total=row.get('Total', 0),
                        vat_percent=row.get('VAT', 0)
                    )
                    db.add(line_item)

                    # Create price history entry if we have a product and a price
                    if product and row.get('Price') and row['Price'] > 0:
                        price_entry = PriceHistory(
                            product_id=product.id,
                            price=row['Price'],
                            date=invoice_date or datetime.utcnow(),
                            invoice_number=detail.get('InvoiceNumber', ''),
                            invoice_id=invoice.id
                        )
                        db.add(price_entry)

                        # Update product's current price
                        product.current_price = row['Price']
                        new_prices += 1

                count += 1

            db.commit()
            log.status = 'success'
            log.records_synced = count
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'success', 'invoices_synced': count, 'price_updates': new_prices}

        except Exception as e:
            db.rollback()
            log.status = 'error'
            log.error_message = str(e)
            log.completed_at = datetime.utcnow()
            db.commit()
            return {'status': 'error', 'message': str(e)}
        finally:
            db.close()
