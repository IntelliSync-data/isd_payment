# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta
import string
import random


class IsdPaymentTransaction(models.Model):
    _name = 'isd_payment.transaction'
    _description = 'Payment Transaction'
    _order = 'create_date desc'
    _rec_name = 'transaction_id'

    # Relation
    payment_method_id = fields.Many2one(
        'isd_payment.method',
        string='Payment Method',
        required=True,
        ondelete='cascade',
        index=True
    )

    # Transaction Info
    transaction_id = fields.Char(
        string='Transaction ID',
        required=True,
        index=True,
        copy=False,
        readonly=True,
        help='Unique transaction identifier'
    )
    amount = fields.Float(
        string='Amount',
        required=True,
        digits=(16, 2),
        help='Transaction amount in VND'
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.VND'),
        help='Currency (VND)'
    )
    description = fields.Char(
        string='Description',
        help='Optional description for this transaction'
    )

    # Status
    status = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
        ('expired', 'Expired'),
    ], string='Status', required=True, default='pending', index=True, tracking=True)

    # SePay Info
    qr_url = fields.Char(
        string='QR URL',
        help='QR code URL for payment'
    )
    bank_account = fields.Char(
        string='Bank Account',
        help='Bank account number'
    )
    bank_code = fields.Char(
        string='Bank Code',
        help='Bank code'
    )

    # SePay Response
    sepay_transaction_id = fields.Char(
        string='SePay Transaction ID',
        help='Transaction ID from SePay'
    )
    sepay_reference = fields.Char(
        string='SePay Reference',
        help='Reference number from SePay'
    )
    sepay_transaction_content = fields.Char(
        string='Transaction Content',
        help='Transaction content from SePay'
    )

    # PayPal Response
    paypal_order_id = fields.Char(
        string='PayPal Order ID',
        help='Order ID from PayPal'
    )
    paypal_capture_id = fields.Char(
        string='PayPal Capture ID',
        help='Capture ID from PayPal after payment completion'
    )
    paypal_payer_email = fields.Char(
        string='Payer Email',
        help='Email of PayPal payer'
    )
    paypal_redirect_url = fields.Char(
        string='PayPal Redirect URL',
        help='URL to redirect user to PayPal approval page'
    )
    amount_usd = fields.Float(
        string='Amount (USD)',
        digits=(16, 4),
        help='Transaction amount in USD (for PayPal)'
    )

    # Dates
    confirmed_at = fields.Datetime(
        string='Confirmed At',
        readonly=True,
        help='When payment was confirmed'
    )
    expired_at = fields.Datetime(
        string='Expired At',
        compute='_compute_expired_at',
        store=True,
        help='Transaction expiration time (1 hour after creation)'
    )

    # API Request Info (for logging)
    request_origin = fields.Char(
        string='Request Origin',
        help='Origin URL of the API request'
    )
    request_ip = fields.Char(
        string='Request IP',
        help='IP address of the API request'
    )

    # Computed
    is_expired = fields.Boolean(
        string='Is Expired',
        compute='_compute_is_expired',
        store=False,
        help='Whether transaction has expired'
    )

    _sql_constraints = [
        ('transaction_id_unique', 'unique(transaction_id)', 'Transaction ID must be unique!'),
    ]

    @api.depends('create_date')
    def _compute_expired_at(self):
        """Compute expiration time (1 hour after creation)"""
        for record in self:
            if record.create_date:
                record.expired_at = record.create_date + timedelta(hours=1)
            else:
                record.expired_at = False

    @api.depends('expired_at', 'status')
    def _compute_is_expired(self):
        """Check if transaction is expired"""
        now = fields.Datetime.now()
        for record in self:
            if record.status in ('pending', 'processing') and record.expired_at:
                record.is_expired = now > record.expired_at
            else:
                record.is_expired = False

    @api.constrains('amount')
    def _check_amount(self):
        """Validate amount"""
        for record in self:
            if record.amount <= 0:
                raise ValidationError(_('Amount must be greater than 0'))
            if record.amount > 500000000:  # Max 500 million VND
                raise ValidationError(_('Amount exceeds maximum allowed (500,000,000 VND)'))

    @api.model
    def generate_transaction_id(self, prefix):
        """
        Generate unique transaction ID
        Format: {prefix}{random_10_chars}
        """
        chars = string.ascii_uppercase + string.digits
        random_part = ''.join(random.choices(chars, k=10))
        transaction_id = f"{prefix}{random_part}"

        # Ensure uniqueness
        exists = self.search([('transaction_id', '=', transaction_id)], limit=1)
        if exists:
            # Retry if collision (very unlikely)
            return self.generate_transaction_id(prefix)

        return transaction_id

    def mark_as_confirmed(self, sepay_data=None):
        """Mark transaction as confirmed (SePay)"""
        self.ensure_one()
        vals = {
            'status': 'confirmed',
            'confirmed_at': fields.Datetime.now(),
        }
        if sepay_data:
            vals.update({
                'sepay_transaction_id': sepay_data.get('id'),
                'sepay_reference': sepay_data.get('reference_number'),
                'sepay_transaction_content': sepay_data.get('transaction_content'),
            })
        self.write(vals)

    def mark_as_confirmed_paypal(self, paypal_data=None):
        """Mark transaction as confirmed (PayPal)"""
        self.ensure_one()
        vals = {
            'status': 'confirmed',
            'confirmed_at': fields.Datetime.now(),
        }
        if paypal_data:
            vals.update({
                'paypal_order_id': paypal_data.get('order_id'),
                'paypal_capture_id': paypal_data.get('capture_id'),
                'paypal_payer_email': paypal_data.get('payer_email'),
            })
        self.write(vals)

    def mark_as_failed(self):
        """Mark transaction as failed"""
        self.ensure_one()
        self.write({'status': 'failed'})

    def mark_as_processing(self):
        """Mark transaction as processing"""
        self.ensure_one()
        self.write({'status': 'processing'})

    def mark_as_expired(self):
        """Mark transaction as expired"""
        self.ensure_one()
        self.write({'status': 'expired'})

    @api.model
    def cron_expire_old_transactions(self):
        """
        Cron job to mark expired transactions
        Run every 1 hour
        """
        now = fields.Datetime.now()
        expired_transactions = self.search([
            ('status', 'in', ('pending', 'processing')),
            ('expired_at', '<', now),
        ])
        expired_transactions.write({'status': 'expired'})
        return True
