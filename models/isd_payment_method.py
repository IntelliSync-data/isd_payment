# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re


class IsdPaymentMethod(models.Model):
    _name = 'isd_payment.method'
    _description = 'Payment Method Configuration'
    _order = 'name'
    _rec_name = 'name'

    # Basic Info
    name = fields.Char(
        string='Payment Method Name',
        required=True,
        help='Display name of the payment method'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Uncheck to archive this payment method'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True
    )

    # Provider
    payment_provider = fields.Selection(
        [('sepay', 'SePay'), ('paypal', 'PayPal')],
        string='Payment Provider',
        required=True,
        default='sepay',
        help='Payment gateway provider'
    )

    # Shared Configuration
    prefix = fields.Char(
        string='Prefix',
        required=True,
        help='Prefix for transaction identification'
    )
    provider_host = fields.Char(
        string='Provider Host',
        required=True,
        help='API host URL for the payment provider'
    )
    provider_account_id = fields.Char(
        string='Account ID',
        help='SePay: bank account number | PayPal: Client ID'
    )
    provider_secret = fields.Char(
        string='Secret / Token',
        help='SePay: API token | PayPal: Client Secret'
    )

    # SePay-specific Configuration
    sepay_qr_host = fields.Char(
        string='QR Host',
        default='https://qr.sepay.vn',
        help='SePay QR code generation host'
    )
    sepay_acc_bank = fields.Char(
        string='Bank Code',
        help='Bank code (e.g., VCB, TCB, MB, ...)'
    )
    # PayPal-specific Configuration
    paypal_mode = fields.Selection(
        [('sandbox', 'Sandbox'), ('live', 'Live')],
        string='Mode',
        default='sandbox',
        help='PayPal environment mode'
    )
    paypal_usd_exchange_rate = fields.Float(
        string='USD Exchange Rate (VND)',
        default=26300.0,
        help='VND to USD exchange rate for converting payment amounts'
    )

    # CORS Configuration
    enable_cors = fields.Boolean(
        string='Enable CORS',
        default=False,
        help='Enable Cross-Origin Resource Sharing restrictions'
    )
    allowed_origins = fields.Text(
        string='Allowed Origins',
        help='List of allowed URLs (one per line). Leave empty to allow only current domain.'
    )

    # Computed Fields
    api_base_url = fields.Char(
        string='API Base URL',
        compute='_compute_api_base_url',
        store=False,
        help='Base URL for this payment method APIs'
    )
    transaction_count = fields.Integer(
        string='Transactions',
        compute='_compute_transaction_count',
        help='Total number of transactions'
    )
    pending_transaction_count = fields.Integer(
        string='Pending',
        compute='_compute_transaction_count',
        help='Number of pending transactions'
    )
    confirmed_transaction_count = fields.Integer(
        string='Confirmed',
        compute='_compute_transaction_count',
        help='Number of confirmed transactions'
    )

    # Status Indicators
    is_configured = fields.Boolean(
        string='Is Configured',
        compute='_compute_is_configured',
        store=True,
        help='All required fields are filled'
    )

    def _compute_api_base_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            if record.id:
                record.api_base_url = f"{base_url}/api/payment/{record.id}"
            else:
                record.api_base_url = False

    @api.depends('payment_provider', 'provider_host', 'provider_account_id', 'provider_secret',
                 'sepay_qr_host', 'sepay_acc_bank', 'paypal_mode')
    def _compute_is_configured(self):
        for record in self:
            if record.payment_provider == 'paypal':
                record.is_configured = all([
                    record.provider_host,
                    record.provider_account_id,
                    record.provider_secret,
                ])
            else:
                record.is_configured = all([
                    record.provider_host,
                    record.provider_account_id,
                    record.provider_secret,
                    record.sepay_qr_host,
                    record.sepay_acc_bank,
                ])

    def _compute_transaction_count(self):
        for record in self:
            transactions = self.env['isd_payment.transaction'].search([
                ('payment_method_id', '=', record.id)
            ])
            record.transaction_count = len(transactions)
            record.pending_transaction_count = len(transactions.filtered(
                lambda t: t.status in ('pending', 'processing')
            ))
            record.confirmed_transaction_count = len(transactions.filtered(
                lambda t: t.status == 'confirmed'
            ))

    @api.constrains('provider_host', 'sepay_qr_host')
    def _check_urls(self):
        url_pattern = re.compile(r'^https?://')
        for record in self:
            if record.provider_host and not url_pattern.match(record.provider_host):
                raise ValidationError(_('Provider Host must start with http:// or https://'))
            if record.sepay_qr_host and not url_pattern.match(record.sepay_qr_host):
                raise ValidationError(_('QR Host must start with http:// or https://'))

    @api.constrains('allowed_origins')
    def _check_allowed_origins(self):
        url_pattern = re.compile(r'^https?://[^\s]+$')
        for record in self:
            if record.allowed_origins:
                lines = record.allowed_origins.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not url_pattern.match(line):
                        raise ValidationError(
                            _('Invalid URL format in Allowed Origins: %s\nURLs must start with http:// or https://') % line
                        )

    def get_allowed_origins(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        allowed_origins = [base_url]
        if self.allowed_origins:
            origins = self.allowed_origins.strip().split('\n')
            allowed_origins.extend([o.strip() for o in origins if o.strip()])
        return list(set(allowed_origins))

    def generate_qr_url(self, transaction_id, amount):
        """Generate SePay QR code URL"""
        self.ensure_one()
        from urllib.parse import quote
        return (
            f"{self.sepay_qr_host}/img"
            f"?acc={self.provider_account_id}"
            f"&bank={self.sepay_acc_bank}"
            f"&amount={int(amount)}"
            f"&des={quote(transaction_id)}"
        )

    def action_view_api_documentation(self):
        self.ensure_one()
        return {
            'name': _('API Documentation'),
            'type': 'ir.actions.act_window',
            'res_model': 'isd_payment.api_documentation_wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_payment_method_id': self.id},
        }

    def action_view_transactions(self):
        self.ensure_one()
        return {
            'name': _('Transactions'),
            'type': 'ir.actions.act_window',
            'res_model': 'isd_payment.transaction',
            'view_mode': 'list,form',
            'domain': [('payment_method_id', '=', self.id)],
            'context': {'default_payment_method_id': self.id},
        }
