# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import timedelta, datetime
import string
import random
import json
import logging
import requests
import re
import base64
import uuid

_logger = logging.getLogger(__name__)


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

    active = fields.Boolean(string='Active', default=True)
    user_id = fields.Many2one(
        'res.users', string='Assigned User',
        default=lambda self: self.env.user,
        index=True,
        help='User responsible for this transaction'
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
    branch = fields.Char(
        string='Branch',
        help='Branch or machine identifier (e.g., Phan Thiet - May 1)'
    )

    # Status
    status = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
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

    # ACB Pay Response
    acb_trace_number = fields.Char(
        string='ACB Trace Number',
        help='Trace number from ACB QR creation'
    )
    acb_order_id = fields.Char(
        string='ACB Order ID',
        help='Order ID sent to ACB'
    )
    acb_virtual_account = fields.Char(
        string='ACB Virtual Account',
        help='Virtual account number for this transaction'
    )
    acb_qr_data = fields.Text(
        string='ACB QR Data',
        help='Raw QR data from ACB response'
    )

    # Reconciliation
    reconciliation_status = fields.Selection([
        ('waiting', 'Waiting'),
        ('matched', 'Matched'),
        ('updated', 'Updated'),
        ('review', 'Review'),
        ('bank_data', 'Bank Data'),
    ], string='Reconciliation', default='waiting', tracking=True)
    reconciliation_data = fields.Text(
        string='Reconciliation Data',
        help='Raw JSON data from ACB daily webhook (API 3)'
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

    # QR Image render
    qr_image_preview = fields.Html(
        string='QR Code',
        compute='_compute_qr_image_preview',
        store=False,
        sanitize=False,
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

    @api.depends('qr_url')
    def _compute_qr_image_preview(self):
        for record in self:
            if record.qr_url:
                record.qr_image_preview = f'''
                    <div>
                        <img src="{record.qr_url}" style="max-width: 250px; max-height: 250px; border: 1px solid #ddd; border-radius: 8px; padding: 4px;"/>
                        <br/>
                        <button class="btn btn-sm btn-outline-primary mt-2"
                            onclick="navigator.clipboard.writeText(this.closest('[name=qr_image_preview]').querySelector('img').src).then(() => {{
                                const btn = this; btn.textContent = 'Copied!';
                                setTimeout(() => btn.textContent = 'Copy URL', 1500);
                            }})">
                            Copy URL
                        </button>
                    </div>
                '''
            else:
                record.qr_image_preview = False

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

    def mark_as_confirmed_acb(self, acb_data=None):
        """Mark transaction as confirmed (ACB Pay)"""
        self.ensure_one()
        vals = {
            'status': 'confirmed',
            'confirmed_at': fields.Datetime.now(),
        }
        if acb_data:
            vals.update({
                'acb_trace_number': acb_data.get('traceNumber'),
                'acb_order_id': acb_data.get('orderId'),
                'acb_virtual_account': acb_data.get('virtualAccount'),
            })
        self.write(vals)

    def _get_acb_token(self):
        """Get ACB Bearer token via OAuth2 (reusable from transaction model)"""
        pm = self.payment_method_id
        token_url = f"{pm.provider_host}/acb/open/iam/id/v1/auth/realms/soba/protocol/openid-connect/token"
        auth_str = base64.b64encode(
            f"{pm.provider_account_id}:{pm.provider_secret}".encode()
        ).decode()

        response = requests.post(
            token_url,
            headers={
                'Authorization': f'Basic {auth_str}',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={
                'client_id': pm.provider_account_id,
                'client_secret': pm.provider_secret,
                'grant_type': 'client_credentials',
                'scope': 'soba-api',
            },
            timeout=30
        )
        if response.status_code not in (200, 201):
            raise UserError(_('Could not get ACB token: %s') % response.status_code)
        data = response.json()
        access_token = data.get('access_token', '')
        if not access_token:
            raise UserError(_('ACB token is empty'))
        return f"Bearer {access_token}"

    def _get_acb_headers(self, token):
        """Build common ACB API headers"""
        pm = self.payment_method_id
        return {
            'Authorization': token,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Client-ID': pm.provider_account_id,
            'X-Owner-Number': pm.acb_owner_number,
            'X-Request-ID': str(uuid.uuid4()),
            'X-Service': 'QRPAYMENT',
            'X-Owner-Type': 'ORG',
            'X-Provider-ID': pm.acb_provider_id,
        }

    def action_query_acb_status(self):
        """Query ACB for transaction status (API 4) and show result popup"""
        self.ensure_one()
        if self.payment_method_id.payment_provider != 'acbpay':
            raise UserError(_('This action is only available for ACB Pay transactions.'))
        if not self.acb_trace_number:
            raise UserError(_('No ACB trace number found for this transaction.'))

        try:
            token = self._get_acb_token()
            headers = self._get_acb_headers(token)
            request_id = str(uuid.uuid4())

            url = f"{self.payment_method_id.provider_host}/acb/open/payments/qr-payment/v1/retrieve"
            params = {
                'traceNumber': self.acb_trace_number,
                'fromDate': (self.create_date - timedelta(days=1)).strftime('%Y-%m-%d') if self.create_date else (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                'toDate': datetime.now().strftime('%Y-%m-%d'),
            }
            if self.acb_order_id:
                params['orderId'] = self.acb_order_id
            if self.payment_method_id.acb_merchant_id:
                params['merchantId'] = self.payment_method_id.acb_merchant_id

            _logger.info(f"ACB query API4: url={url}, params={params}")
            response = requests.get(url, headers=headers, params=params, timeout=30)
            raw_text = response.text
            _logger.info(f"ACB query API4 response: {response.status_code} {raw_text[:1000]}")

            if response.status_code != 200:
                wizard = self.env['isd_payment.acb_query_wizard'].create({
                    'transaction_id': self.id,
                    'transaction_name': self.transaction_id,
                    'query_success': False,
                    'error_message': f'ACB API error: {response.status_code} - {raw_text[:200]}',
                    'raw_response': raw_text,
                })
            else:
                data = response.json()
                resp_status = data.get('responseStatus', {})
                resp_code = resp_status.get('responseCode', '')
                resp_body = data.get('responseBody', {})

                if resp_code != '00000000':
                    wizard = self.env['isd_payment.acb_query_wizard'].create({
                        'transaction_id': self.id,
                        'transaction_name': self.transaction_id,
                        'query_success': False,
                        'error_message': f"{resp_code}: {resp_status.get('responseMessage', '')}",
                        'raw_response': json.dumps(data, indent=2, ensure_ascii=False),
                    })
                else:
                    # Parse order data - could be in responseBody directly or in orders[]
                    order = resp_body
                    if 'orders' in resp_body and resp_body['orders']:
                        order = resp_body['orders'][0]

                    wizard = self.env['isd_payment.acb_query_wizard'].create({
                        'transaction_id': self.id,
                        'transaction_name': self.transaction_id,
                        'query_success': True,
                        'acb_status': order.get('status', ''),
                        'acb_amount': order.get('amount', 0),
                        'acb_trace_number': order.get('traceNumber', ''),
                        'acb_order_id': order.get('orderId', ''),
                        'acb_virtual_account': order.get('virtualAccount', ''),
                        'acb_merchant_id': order.get('merchantId', ''),
                        'acb_terminal_id': order.get('terminalId', ''),
                        'acb_beneficiary_name': order.get('beneficiaryName', ''),
                        'raw_response': json.dumps(data, indent=2, ensure_ascii=False),
                    })

        except requests.Timeout:
            wizard = self.env['isd_payment.acb_query_wizard'].create({
                'transaction_id': self.id,
                'transaction_name': self.transaction_id,
                'query_success': False,
                'error_message': 'ACB API timeout',
                'raw_response': '',
            })
        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error querying ACB transaction")
            wizard = self.env['isd_payment.acb_query_wizard'].create({
                'transaction_id': self.id,
                'transaction_name': self.transaction_id,
                'query_success': False,
                'error_message': str(e),
                'raw_response': '',
            })

        return {
            'type': 'ir.actions.act_window',
            'name': _('ACB Transaction Status - %s') % self.transaction_id,
            'res_model': 'isd_payment.acb_query_wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'views': [(self.env.ref('isd_payment.view_acb_query_wizard_form').id, 'form')],
        }

    def action_cancel_acb_qr(self):
        """Show confirmation dialog before cancelling ACB QR (API 5)"""
        self.ensure_one()
        if self.payment_method_id.payment_provider != 'acbpay':
            raise UserError(_('This action is only available for ACB Pay transactions.'))
        if not self.acb_trace_number:
            raise UserError(_('No ACB trace number found for this transaction.'))
        if self.status == 'confirmed':
            raise UserError(_('Cannot cancel a confirmed transaction.'))
        if self.status == 'cancelled':
            raise UserError(_('Transaction is already cancelled.'))

        # Show confirmation wizard
        wizard = self.env['isd_payment.acb_cancel_confirm'].create({
            'transaction_id': self.id,
            'transaction_name': self.transaction_id,
            'amount': self.amount,
            'acb_trace_number': self.acb_trace_number,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancel QR Code - %s') % self.transaction_id,
            'res_model': 'isd_payment.acb_cancel_confirm',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'views': [(self.env.ref('isd_payment.view_acb_cancel_confirm_form').id, 'form')],
        }

    def _do_cancel_acb_qr(self):
        """Actually cancel the QR code on ACB (API 5) — called after confirmation"""
        self.ensure_one()
        try:
            token = self._get_acb_token()
            headers = self._get_acb_headers(token)
            request_id = headers['X-Request-ID']

            url = f"{self.payment_method_id.provider_host}/acb/open/payments/qr-payment/v1/cancellation"
            body = {
                'requestTrace': request_id,
                'requestDateTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S.000+0700'),
                'requestParameters': {
                    'traceNumber': self.acb_trace_number,
                    'orderId': self.acb_order_id or self.transaction_id[:13],
                    'amount': int(self.amount),
                },
            }

            _logger.info(f"ACB cancel API5: url={url}, body={json.dumps(body)}")
            response = requests.delete(url, headers=headers, json=body, timeout=30)
            raw_text = response.text
            _logger.info(f"ACB cancel API5 response: {response.status_code} {raw_text[:1000]}")

            if response.status_code != 200:
                wizard = self.env['isd_payment.acb_cancel_wizard'].create({
                    'transaction_id': self.id,
                    'transaction_name': self.transaction_id,
                    'cancel_success': False,
                    'error_message': f'ACB API error: {response.status_code} - {raw_text[:200]}',
                    'raw_response': raw_text,
                })
            else:
                data = response.json()
                resp_status = data.get('responseStatus', {})
                resp_code = resp_status.get('responseCode', '')
                resp_body = data.get('responseBody', {})

                if resp_code != '00000000':
                    wizard = self.env['isd_payment.acb_cancel_wizard'].create({
                        'transaction_id': self.id,
                        'transaction_name': self.transaction_id,
                        'cancel_success': False,
                        'error_message': f"{resp_code}: {resp_status.get('responseMessage', '')}",
                        'raw_response': json.dumps(data, indent=2, ensure_ascii=False),
                    })
                else:
                    # Success — update local transaction status
                    self.write({'status': 'cancelled'})
                    wizard = self.env['isd_payment.acb_cancel_wizard'].create({
                        'transaction_id': self.id,
                        'transaction_name': self.transaction_id,
                        'cancel_success': True,
                        'acb_status': resp_body.get('status', 'SUCCESS'),
                        'raw_response': json.dumps(data, indent=2, ensure_ascii=False),
                    })

        except requests.Timeout:
            wizard = self.env['isd_payment.acb_cancel_wizard'].create({
                'transaction_id': self.id,
                'transaction_name': self.transaction_id,
                'cancel_success': False,
                'error_message': 'ACB API timeout',
                'raw_response': '',
            })
        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error cancelling ACB QR")
            wizard = self.env['isd_payment.acb_cancel_wizard'].create({
                'transaction_id': self.id,
                'transaction_name': self.transaction_id,
                'cancel_success': False,
                'error_message': str(e),
                'raw_response': '',
            })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancel QR Result - %s') % self.transaction_id,
            'res_model': 'isd_payment.acb_cancel_wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'views': [(self.env.ref('isd_payment.view_acb_cancel_wizard_form').id, 'form')],
        }

    def action_view_reconciliation_data(self):
        """Show reconciliation JSON data in a popup"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reconciliation Data - %s') % self.transaction_id,
            'res_model': 'isd_payment.transaction',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'views': [(self.env.ref('isd_payment.view_isd_payment_transaction_recon_popup').id, 'form')],
        }

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
