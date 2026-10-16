# -*- coding: utf-8 -*-
from odoo import models, fields


class IsdPaymentTransferLine(models.Model):
    _name = 'isd_payment.transfer.line'
    _description = 'Payment Transfer Detail'
    _order = 'sequence, id'

    method_id = fields.Many2one(
        'isd_payment.method', string='Payment Method',
        required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string='Sequence', default=10)
    # What a bank asks for differs by country and by bank, so the rows are
    # typed in rather than fixed here: IBAN in one place, routing number in
    # another, and a reference line nobody else uses.
    label = fields.Char(
        string='Label', required=True, translate=True,
        help='What this line is called on the checkout page, e.g. SWIFT/BIC code')
    value = fields.Char(
        string='Value', required=True,
        help='What the customer copies. Not translated: an account number '
             'reads the same in every language')
