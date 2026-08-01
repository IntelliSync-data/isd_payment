# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import json
import logging

_logger = logging.getLogger(__name__)


class AcbTransactionQueryWizard(models.TransientModel):
    _name = 'isd_payment.acb_query_wizard'
    _description = 'ACB Transaction Query Result'

    transaction_id = fields.Many2one(
        'isd_payment.transaction',
        string='Transaction',
        readonly=True,
    )
    transaction_name = fields.Char(
        string='Transaction ID',
        readonly=True,
    )

    # Result display
    acb_status = fields.Char(string='ACB Status', readonly=True)
    acb_amount = fields.Float(string='Amount', readonly=True)
    acb_trace_number = fields.Char(string='Trace Number', readonly=True)
    acb_order_id = fields.Char(string='Order ID', readonly=True)
    acb_virtual_account = fields.Char(string='Virtual Account', readonly=True)
    acb_merchant_id = fields.Char(string='Merchant ID', readonly=True)
    acb_terminal_id = fields.Char(string='Terminal ID', readonly=True)
    acb_beneficiary_name = fields.Char(string='Beneficiary Name', readonly=True)

    # Status indicator
    query_success = fields.Boolean(string='Query Success', readonly=True)
    error_message = fields.Char(string='Error', readonly=True)
    status_display = fields.Char(string='Result', compute='_compute_status_display')

    # Raw JSON
    raw_response = fields.Text(string='Raw JSON', readonly=True)

    @api.depends('query_success', 'acb_status', 'error_message')
    def _compute_status_display(self):
        for rec in self:
            if not rec.query_success:
                rec.status_display = f'Error: {rec.error_message}'
            elif rec.acb_status in ('COMPLETED', 'SUCCESS'):
                rec.status_display = f'Payment Completed ({rec.acb_status})'
            elif rec.acb_status in ('INIT', 'PENDING'):
                rec.status_display = f'Payment Pending ({rec.acb_status})'
            elif rec.acb_status in ('CANCELLED', 'ERRORCORRECTED'):
                rec.status_display = f'Cancelled ({rec.acb_status})'
            else:
                rec.status_display = rec.acb_status or 'Unknown'


class AcbCancelConfirmWizard(models.TransientModel):
    _name = 'isd_payment.acb_cancel_confirm'
    _description = 'ACB Cancel QR Confirmation'

    transaction_id = fields.Many2one(
        'isd_payment.transaction',
        string='Transaction',
        required=True,
    )
    transaction_name = fields.Char(string='Transaction ID', readonly=True)
    amount = fields.Float(string='Amount', readonly=True)
    acb_trace_number = fields.Char(string='Trace Number', readonly=True)

    def action_confirm_cancel(self):
        """User confirmed — proceed with ACB QR cancellation"""
        self.ensure_one()
        return self.transaction_id._do_cancel_acb_qr()


class AcbCancelQrWizard(models.TransientModel):
    _name = 'isd_payment.acb_cancel_wizard'
    _description = 'ACB Cancel QR Result'

    transaction_id = fields.Many2one(
        'isd_payment.transaction',
        string='Transaction',
        readonly=True,
    )
    transaction_name = fields.Char(
        string='Transaction ID',
        readonly=True,
    )

    # Result
    cancel_success = fields.Boolean(string='Cancel Success', readonly=True)
    acb_status = fields.Char(string='ACB Status', readonly=True)
    error_message = fields.Char(string='Error', readonly=True)
    status_display = fields.Char(string='Result', compute='_compute_status_display')

    # Raw JSON
    raw_response = fields.Text(string='Raw JSON', readonly=True)

    @api.depends('cancel_success', 'acb_status', 'error_message')
    def _compute_status_display(self):
        for rec in self:
            if rec.cancel_success:
                rec.status_display = f'QR Code cancelled successfully ({rec.acb_status})'
            else:
                rec.status_display = f'Error: {rec.error_message}'
