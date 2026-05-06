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
        [('sepay', 'SePay')],
        string='Payment Provider',
        required=True,
        default='sepay',
        help='Payment gateway provider'
    )

    # SePay Configuration
    prefix = fields.Char(
        string='Prefix',
        required=True,
        help='Prefix for transaction identification'
    )
    sepay_host = fields.Char(
        string='SePay Host',
        required=True,
        default='https://my.sepay.vn',
        help='SePay API host URL'
    )
    sepay_qr_host = fields.Char(
        string='SePay QR Host',
        required=True,
        default='https://qr.sepay.vn',
        help='SePay QR code generation host'
    )
    sepay_acc_number = fields.Char(
        string='Account Number',
        required=True,
        help='Bank account number'
    )
    sepay_acc_bank = fields.Char(
        string='Bank Code',
        required=True,
        help='Bank code (e.g., VCB, TCB, MB, ...)'
    )
    sepay_api_token = fields.Char(
        string='API Token',
        required=True,
        help='SePay API authentication token'
    )
    sepay_prefix_transaction_id = fields.Char(
        string='Transaction ID Prefix',
        required=True,
        help='Prefix for auto-generated transaction IDs'
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
        """Compute API base URL"""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            if record.id:
                record.api_base_url = f"{base_url}/api/payment/{record.id}"
            else:
                record.api_base_url = False

    @api.depends('sepay_host', 'sepay_qr_host', 'sepay_acc_number', 'sepay_acc_bank', 'sepay_api_token', 'sepay_prefix_transaction_id')
    def _compute_is_configured(self):
        """Check if all required fields are configured"""
        for record in self:
            record.is_configured = all([
                record.sepay_host,
                record.sepay_qr_host,
                record.sepay_acc_number,
                record.sepay_acc_bank,
                record.sepay_api_token,
                record.sepay_prefix_transaction_id,
            ])

    def _compute_transaction_count(self):
        """Compute transaction counts"""
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

    @api.constrains('sepay_host', 'sepay_qr_host')
    def _check_urls(self):
        """Validate URL formats"""
        url_pattern = re.compile(r'^https?://')
        for record in self:
            if record.sepay_host and not url_pattern.match(record.sepay_host):
                raise ValidationError(_('SePay Host must start with http:// or https://'))
            if record.sepay_qr_host and not url_pattern.match(record.sepay_qr_host):
                raise ValidationError(_('SePay QR Host must start with http:// or https://'))

    @api.constrains('allowed_origins')
    def _check_allowed_origins(self):
        """Validate allowed origins format"""
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
        """Get list of allowed origins including current domain"""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        allowed_origins = [base_url]

        if self.allowed_origins:
            origins = self.allowed_origins.strip().split('\n')
            allowed_origins.extend([o.strip() for o in origins if o.strip()])

        return list(set(allowed_origins))  # Remove duplicates

    def generate_qr_url(self, transaction_id, amount):
        """Generate QR code URL for payment"""
        self.ensure_one()
        from urllib.parse import quote
        return f"{self.sepay_qr_host}/img?acc={self.sepay_acc_number}&bank={self.sepay_acc_bank}&amount={int(amount)}&des={quote(transaction_id)}"

    def action_view_api_documentation(self):
        """Open API documentation wizard"""
        self.ensure_one()
        return {
            'name': _('API Documentation'),
            'type': 'ir.actions.act_window',
            'res_model': 'isd_payment.api_documentation_wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_payment_method_id': self.id,
            },
        }

    def action_view_transactions(self):
        """View transactions for this payment method"""
        self.ensure_one()
        return {
            'name': _('Transactions'),
            'type': 'ir.actions.act_window',
            'res_model': 'isd_payment.transaction',
            'view_mode': 'list,form',
            'domain': [('payment_method_id', '=', self.id)],
            'context': {
                'default_payment_method_id': self.id,
            },
        }
