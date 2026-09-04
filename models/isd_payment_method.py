# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re
import secrets


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
    image = fields.Image(
        string='Image',
        max_width=512,
        max_height=512,
        help='Logo or icon for this payment method (jpg/png, max 512x512)'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Uncheck to archive this payment method'
    )
    user_ids = fields.Many2many(
        'res.users', 'isd_payment_method_user_rel', 'method_id', 'user_id',
        string='Assigned Users',
        help='Users who can see this payment method and its transactions'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True
    )

    # Provider
    payment_provider = fields.Selection(
        [('sepay', 'SePay'), ('paypal', 'PayPal'), ('vtcpay', 'VTC Pay'), ('acbpay', 'ACB Pay')],
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
    # VTC Pay-specific Configuration
    vtc_security_code = fields.Char(
        string='Security Code',
        help='VTC Pay security code for callback signature verification'
    )
    vtc_payment_type = fields.Char(
        string='Payment Type',
        default='DomesticBank',
        help='VTC Pay payment type (e.g., DomesticBank, InternationalCard)'
    )
    vtc_receiver_account = fields.Char(
        string='Receiver Account',
        help='VTC Pay receiver account number'
    )

    # ACB Pay-specific Configuration
    acb_token_url = fields.Char(
        string='Token URL',
        help='ACB OAuth2 token endpoint URL',
    )
    acb_api_key = fields.Char(
        string='API Key',
        help='ACB webhook x-api-key for verifying callbacks'
    )
    acb_webhook_ip = fields.Char(
        string='Webhook IP Whitelist',
        default='124.197.28.244',
        help='Comma-separated IPs allowed to call webhook (ACB server IPs)'
    )
    acb_owner_number = fields.Char(
        string='Owner Number',
        help='X-Owner-Number header value (from ACB data test)'
    )
    acb_provider_id = fields.Char(
        string='Provider ID',
        help='X-Provider-ID header value (from ACB data test)'
    )
    acb_virtual_account_prefix = fields.Char(
        string='Virtual Account Prefix',
        help='Prefix for virtual account (from ACB data test)'
    )
    acb_beneficiary_name = fields.Char(
        string='Beneficiary Name',
        help='Account holder name displayed on QR code'
    )
    acb_account_number = fields.Char(
        string='Account Number',
        help='ACB bank account number (e.g. 5798589)'
    )
    acb_merchant_id = fields.Char(
        string='Merchant ID',
        help='Merchant ID from ACB, max 30 chars'
    )
    acb_terminal_id = fields.Char(
        string='Terminal ID',
        help='Terminal ID from ACB, max 30 chars'
    )
    acb_user_id = fields.Char(
        string='User ID',
        help='ACB user ID (userId) for QR payment API'
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
                 'sepay_qr_host', 'sepay_acc_bank',
                 'vtc_security_code', 'vtc_receiver_account',
                 'acb_owner_number', 'acb_provider_id', 'acb_virtual_account_prefix', 'acb_beneficiary_name',
                 'acb_account_number', 'acb_merchant_id', 'acb_terminal_id')
    def _compute_is_configured(self):
        for record in self:
            if record.payment_provider == 'paypal':
                record.is_configured = all([
                    record.provider_host,
                    record.provider_account_id,
                    record.provider_secret,
                ])
            elif record.payment_provider == 'vtcpay':
                record.is_configured = all([
                    record.provider_host,
                    record.provider_account_id,
                    record.vtc_security_code,
                    record.vtc_receiver_account,
                ])
            elif record.payment_provider == 'acbpay':
                record.is_configured = all([
                    record.provider_host,
                    record.provider_account_id,
                    record.provider_secret,
                    record.acb_owner_number,
                    record.acb_provider_id,
                    record.acb_virtual_account_prefix,
                    record.acb_beneficiary_name,
                    record.acb_account_number,
                    record.acb_merchant_id,
                    record.acb_terminal_id,
                ])
            else:  # sepay
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

    def action_generate_acb_api_key(self):
        self.ensure_one()
        self.acb_api_key = secrets.token_urlsafe(32)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('API Key generated successfully!'),
                'type': 'success',
                'sticky': False,
            },
        }

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
            'context': {
                'default_payment_method_id': self.id,
                'search_default_status_confirmed': 1,
                'search_default_group_branch': 1,
            },
        }
