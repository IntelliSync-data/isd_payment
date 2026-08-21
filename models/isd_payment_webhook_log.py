# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta, datetime
import json
import logging

_logger = logging.getLogger(__name__)


class IsdPaymentWebhookLog(models.Model):
    _name = 'isd_payment.webhook_log'
    _description = 'ACB Webhook Queue'
    _order = 'create_date desc'

    payment_method_id = fields.Many2one(
        'isd_payment.method',
        string='Payment Method',
        required=True,
        ondelete='cascade',
        index=True,
    )
    request_trace = fields.Char(
        string='Request Trace',
        index=True,
    )
    request_code = fields.Char(
        string='Request Code',
        help='TRANSACTION_HISTORY or TRANSACTION_UPDATE',
    )
    raw_body = fields.Text(
        string='Raw JSON',
        required=True,
    )
    transaction_count = fields.Integer(
        string='Transaction Count',
        default=0,
    )
    status = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='Status', default='pending', index=True, required=True)
    error_message = fields.Text(string='Error')
    processed_at = fields.Datetime(string='Processed At')
    processed_count = fields.Integer(string='Processed Count', default=0)

    request_ip = fields.Char(string='IP')

    def action_retry(self):
        """Retry processing a failed log"""
        self.ensure_one()
        self.write({'status': 'pending', 'error_message': False})
        cron = self.env.ref('isd_payment.cron_process_acb_webhook_queue', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    @api.model
    def cron_process_webhook_queue(self):
        """
        Cron job to process pending webhook logs (reconciliation).
        Runs every 5 minutes.
        """
        pending_logs = self.search([
            ('status', '=', 'pending'),
        ], order='create_date asc')

        for log in pending_logs:
            try:
                log.write({'status': 'processing'})
                # Commit to mark as processing (prevent other cron from picking it up)
                self.env.cr.commit()

                log._process_reconciliation()

                log.write({
                    'status': 'done',
                    'processed_at': fields.Datetime.now(),
                })
                self.env.cr.commit()

            except Exception as e:
                self.env.cr.rollback()
                _logger.exception(f"Error processing webhook log {log.id}")
                try:
                    log.write({
                        'status': 'error',
                        'error_message': str(e)[:1000],
                    })
                    self.env.cr.commit()
                except Exception:
                    _logger.exception(f"Failed to mark webhook log {log.id} as error")

    def _process_reconciliation(self):
        """Process reconciliation for a single webhook log"""
        self.ensure_one()
        data = json.loads(self.raw_body)

        request_params = data.get('requestParameters', {})
        req = request_params.get('request', {})
        request_params_inner = req.get('requestParams', {})
        transactions = request_params_inner.get('transactions', [])

        if not transactions:
            _logger.warning(f"Webhook log {self.id}: no transactions")
            return

        payment_method = self.payment_method_id
        processed_count = 0

        # Determine the reconciliation date from the first transaction's effectiveDate
        # Only query transactions from that date range (T-1)
        first_effective = transactions[0].get('effectiveDate', '')
        if first_effective:
            recon_date = datetime.strptime(first_effective, '%Y-%m-%d').date()
        else:
            recon_date = (datetime.now() - timedelta(days=1)).date()

        recon_date_start = datetime.combine(recon_date, datetime.min.time())
        recon_date_end = datetime.combine(recon_date + timedelta(days=1), datetime.min.time())

        _logger.info(f"Webhook log {self.id}: processing {len(transactions)} transactions for date {recon_date}")

        # Filter transactions by merchant identity (custom1/custom2 must match payment method config)
        merchant_ids = set(filter(None, [
            payment_method.acb_merchant_id,
            payment_method.acb_terminal_id,
            payment_method.acb_user_id,
        ]))
        if merchant_ids:
            filtered_transactions = []
            for tx in transactions:
                tx_entity = tx.get('transactionEntityAttribute', {})
                custom1 = (tx_entity.get('custom1', '') or '').strip()
                custom2 = (tx_entity.get('custom2', '') or '').strip()
                tx_identifiers = set(filter(None, [custom1, custom2]))
                if tx_identifiers & merchant_ids:
                    filtered_transactions.append(tx)
            _logger.info(
                f"Webhook log {self.id}: filtered {len(filtered_transactions)}/{len(transactions)} "
                f"transactions matching merchant config"
            )
            transactions = filtered_transactions

        if not transactions:
            _logger.info(f"Webhook log {self.id}: no matching transactions after merchant filter")
            return

        for tx in transactions:
            tx_status = tx.get('transactionStatus', '')
            tx_amount = tx.get('amount', 0)
            tx_entity = tx.get('transactionEntityAttribute', {})
            trace_number = tx_entity.get('traceNumber', '') or ''
            virtual_account = tx_entity.get('virtualAccount', '') or ''
            reference_number = tx_entity.get('referenceNumber', '') or ''
            tx_raw = json.dumps(tx)

            _logger.info(
                f"Webhook log {self.id} tx: status={tx_status}, amount={tx_amount}, "
                f"traceNumber={trace_number}, virtualAccount={virtual_account}"
            )

            # Find transaction by traceNumber -> virtualAccount -> referenceNumber
            # Scope to the reconciliation date
            Transaction = self.env['isd_payment.transaction'].sudo()
            transaction = None

            if trace_number:
                transaction = Transaction.search([
                    ('acb_trace_number', '=', trace_number),
                    ('payment_method_id', '=', payment_method.id),
                    ('create_date', '>=', recon_date_start),
                    ('create_date', '<', recon_date_end),
                ], limit=1)
                # Fallback: search without date filter (in case transaction was created earlier)
                if not transaction:
                    transaction = Transaction.search([
                        ('acb_trace_number', '=', trace_number),
                        ('payment_method_id', '=', payment_method.id),
                    ], limit=1)

            if not transaction and virtual_account:
                transaction = Transaction.search([
                    ('acb_virtual_account', '=', virtual_account),
                    ('payment_method_id', '=', payment_method.id),
                    ('create_date', '>=', recon_date_start),
                    ('create_date', '<', recon_date_end),
                ], limit=1)

            if not transaction and reference_number:
                transaction = Transaction.search([
                    ('transaction_id', '=', reference_number),
                    ('payment_method_id', '=', payment_method.id),
                    ('create_date', '>=', recon_date_start),
                    ('create_date', '<', recon_date_end),
                ], limit=1)

            if not transaction:
                # Not found -> create as "Data Bank"
                _logger.info(f"Webhook log {self.id}: creating bank_data (traceNumber={trace_number})")
                new_status = 'confirmed' if tx_status == 'COMPLETED' else 'cancelled'
                Transaction.create({
                    'payment_method_id': payment_method.id,
                    'transaction_id': reference_number or trace_number or virtual_account,
                    'amount': tx_amount,
                    'status': new_status,
                    'confirmed_at': fields.Datetime.now() if new_status == 'confirmed' else False,
                    'acb_trace_number': trace_number,
                    'acb_virtual_account': virtual_account,
                    'reconciliation_status': 'bank_data',
                    'reconciliation_data': tx_raw,
                    'description': 'Created from ACB daily reconciliation',
                })
                processed_count += 1
                continue

            # Reconciliation logic
            recon_status = 'waiting'
            update_vals = {'reconciliation_data': tx_raw}

            if transaction.status == 'confirmed' and tx_status == 'COMPLETED':
                recon_status = 'matched'
            elif transaction.status in ('pending', 'processing') and tx_status == 'COMPLETED':
                update_vals.update({
                    'status': 'confirmed',
                    'confirmed_at': fields.Datetime.now(),
                })
                recon_status = 'updated'
            elif transaction.status in ('pending', 'processing') and tx_status != 'COMPLETED':
                update_vals['status'] = 'cancelled'
                recon_status = 'updated'
            elif transaction.status == 'confirmed' and tx_status != 'COMPLETED':
                recon_status = 'review'
            elif transaction.status == 'cancelled' and tx_status != 'ERRORCORRECTED':
                recon_status = 'review'

            update_vals['reconciliation_status'] = recon_status
            transaction.write(update_vals)
            processed_count += 1

            _logger.info(f"Webhook log {self.id}: {transaction.transaction_id} -> {recon_status}")

        self.processed_count = processed_count
        _logger.info(f"Webhook log {self.id}: done, processed {processed_count}/{len(transactions)}")
