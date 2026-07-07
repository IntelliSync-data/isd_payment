# -*- coding: utf-8 -*-
{
    'name': 'ISD Payment',
    'version': '18.0.1.0.0',
    'category': 'ISD Modules',
    'summary': 'Payment Gateway Integration (SePay, PayPal) with Auto-Generated REST APIs',
    'description': """
ISD Payment Module
==================

This module provides payment gateway integration for Odoo 18.

Features:
---------
* Configure multiple payment methods
* Auto-generate REST APIs for external integration
* Support SePay and PayPal payment gateways
* QR code payment generation (SePay)
* PayPal order creation with redirect flow
* Payment confirmation via provider APIs
* Transaction management
* CORS configuration for security
* API documentation wizard

Supported Payment Providers:
----------------------------
* SePay (Vietnam - QR bank transfer)
* PayPal (International - OAuth2 redirect flow)

Use Cases:
----------
* Desktop application payment integration
* Mobile app payment integration
* Website payment integration
* Multi-project payment management

    """,
    'author': 'IntelliSyncData',
    'website': 'https://intellisyncdata.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/isd_payment_method_views.xml',
        'views/isd_payment_transaction_views.xml',
        'wizard/api_documentation_wizard_views.xml',
        'views/isd_payment_menu.xml',
        'data/paypal_default_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'isd_payment/static/src/css/isd_payment.css',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
