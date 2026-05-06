# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class ApiDocumentationWizard(models.TransientModel):
    _name = 'isd_payment.api_documentation_wizard'
    _description = 'API Documentation Wizard'

    payment_method_id = fields.Many2one(
        'isd_payment.method',
        string='Payment Method',
        required=True
    )

    # Computed fields for display
    api_base_url = fields.Char(
        string='Base URL',
        compute='_compute_api_info',
        store=False
    )
    provider_name = fields.Char(
        string='Provider',
        compute='_compute_api_info',
        store=False
    )
    cors_enabled = fields.Boolean(
        string='CORS Enabled',
        compute='_compute_api_info',
        store=False
    )
    allowed_origins_display = fields.Html(
        string='Allowed Origins',
        compute='_compute_api_info',
        store=False
    )

    # API Documentation HTML
    api_documentation = fields.Html(
        string='API Documentation',
        compute='_compute_api_documentation',
        store=False
    )

    @api.depends('payment_method_id')
    def _compute_api_info(self):
        """Compute API information"""
        for wizard in self:
            if wizard.payment_method_id:
                wizard.api_base_url = wizard.payment_method_id.api_base_url
                wizard.provider_name = dict(wizard.payment_method_id._fields['payment_provider'].selection).get(
                    wizard.payment_method_id.payment_provider
                )
                wizard.cors_enabled = wizard.payment_method_id.enable_cors

                # Build allowed origins HTML
                if wizard.payment_method_id.enable_cors:
                    origins = wizard.payment_method_id.get_allowed_origins()
                    origins_html = '<ul>'
                    for origin in origins:
                        origins_html += f'<li><code>{origin}</code></li>'
                    origins_html += '</ul>'
                    wizard.allowed_origins_display = origins_html
                else:
                    wizard.allowed_origins_display = '<p><b>Public API</b> - All origins allowed</p>'
            else:
                wizard.api_base_url = False
                wizard.provider_name = False
                wizard.cors_enabled = False
                wizard.allowed_origins_display = False

    @api.depends('payment_method_id')
    def _compute_api_documentation(self):
        """Generate API documentation HTML"""
        for wizard in self:
            if not wizard.payment_method_id:
                wizard.api_documentation = ''
                continue

            method = wizard.payment_method_id
            base_url = method.api_base_url

            html = f'''
            <div class="api-documentation">
                <style>
                    .api-documentation {{
                        font-family: 'Courier New', monospace;
                        font-size: 13px;
                    }}
                    .api-section {{
                        background: #f8f9fa;
                        border-left: 4px solid #007bff;
                        padding: 15px;
                        margin: 20px 0;
                    }}
                    .api-endpoint {{
                        background: #28a745;
                        color: white;
                        padding: 5px 10px;
                        border-radius: 3px;
                        display: inline-block;
                        margin-right: 10px;
                        font-weight: bold;
                    }}
                    .api-url {{
                        background: #e9ecef;
                        padding: 5px 10px;
                        border-radius: 3px;
                        display: inline-block;
                        font-family: monospace;
                    }}
                    .code-block {{
                        background: #2d2d2d;
                        color: #f8f8f2;
                        padding: 15px;
                        border-radius: 5px;
                        overflow-x: auto;
                        margin: 10px 0;
                    }}
                    .code-block pre {{
                        margin: 0;
                        white-space: pre-wrap;
                    }}
                    .api-description {{
                        color: #6c757d;
                        margin: 10px 0;
                    }}
                    h3 {{
                        color: #007bff;
                        border-bottom: 2px solid #007bff;
                        padding-bottom: 5px;
                    }}
                    h4 {{
                        color: #28a745;
                        margin-top: 15px;
                    }}
                </style>

                <h2>API Documentation - {method.name}</h2>

                <div style="background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <p><b>Base URL:</b> <code>{base_url}</code></p>
                    <p><b>Provider:</b> {wizard.provider_name}</p>
                    <p><b>CORS:</b> {'Enabled' if method.enable_cors else 'Disabled (Public API)'}</p>
                </div>

                <!-- API 1: Create Payment -->
                <div class="api-section">
                    <h3>1. Create Payment (Generate QR)</h3>
                    <p>
                        <span class="api-endpoint">POST</span>
                        <span class="api-url">{base_url}/create</span>
                    </p>
                    <p class="api-description">Create a payment transaction and generate QR code URL</p>

                    <h4>Request Body:</h4>
                    <div class="code-block">
                        <pre>{{
  "amount": 50000,
  "description": "Order #12345"  // Optional
}}</pre>
                    </div>

                    <h4>Response (Success):</h4>
                    <div class="code-block">
                        <pre>{{
  "success": true,
  "data": {{
    "transaction_id": "{method.sepay_prefix_transaction_id}ABC123XYZ",
    "qr_url": "https://qr.sepay.vn/img?acc=...",
    "amount": 50000,
    "bank_account": "{method.sepay_acc_number}",
    "bank_code": "{method.sepay_acc_bank}",
    "created_at": "2026-04-04 10:30:00"
  }}
}}</pre>
                    </div>

                    <h4>cURL Example:</h4>
                    <div class="code-block">
                        <pre>curl -X POST {base_url}/create \\
  -H "Content-Type: application/json" \\
  -d '{{"amount": 50000, "description": "Test"}}'</pre>
                    </div>

                    <h4>Python Example:</h4>
                    <div class="code-block">
                        <pre>import requests

response = requests.post(
    '{base_url}/create',
    json={{'amount': 50000, 'description': 'Test'}}
)
data = response.json()
print(data['data']['qr_url'])</pre>
                    </div>

                    <h4>JavaScript Example:</h4>
                    <div class="code-block">
                        <pre>fetch('{base_url}/create', {{
  method: 'POST',
  headers: {{'Content-Type': 'application/json'}},
  body: JSON.stringify({{amount: 50000, description: 'Test'}})
}})
.then(res => res.json())
.then(data => console.log(data.data.qr_url))</pre>
                    </div>
                </div>

                <!-- API 2: Confirm Payment -->
                <div class="api-section">
                    <h3>2. Confirm Payment</h3>
                    <p>
                        <span class="api-endpoint">POST</span>
                        <span class="api-url">{base_url}/confirm</span>
                    </p>
                    <p class="api-description">Check if payment has been confirmed via SePay</p>

                    <h4>Request Body:</h4>
                    <div class="code-block">
                        <pre>{{
  "transaction_id": "{method.sepay_prefix_transaction_id}ABC123XYZ",
  "amount": 50000
}}</pre>
                    </div>

                    <h4>Response (Confirmed):</h4>
                    <div class="code-block">
                        <pre>{{
  "success": true,
  "status": "confirmed",
  "message": "Payment confirmed via SePay",
  "data": {{
    "transaction_id": "{method.sepay_prefix_transaction_id}ABC123XYZ",
    "amount": 50000,
    "confirmed_at": "2026-04-04 10:35:00",
    "sepay_transaction_id": "SP123456789",
    "sepay_reference": "REF987654"
  }}
}}</pre>
                    </div>

                    <h4>Response (Processing):</h4>
                    <div class="code-block">
                        <pre>{{
  "success": true,
  "status": "processing",
  "message": "Payment is being processed"
}}</pre>
                    </div>

                    <h4>cURL Example:</h4>
                    <div class="code-block">
                        <pre>curl -X POST {base_url}/confirm \\
  -H "Content-Type: application/json" \\
  -d '{{"transaction_id": "{method.sepay_prefix_transaction_id}ABC123", "amount": 50000}}'</pre>
                    </div>
                </div>

                <!-- API 3: Get Transaction -->
                <div class="api-section">
                    <h3>3. Get Transaction Status</h3>
                    <p>
                        <span class="api-endpoint">GET</span>
                        <span class="api-url">{base_url}/transaction/{{transaction_id}}</span>
                    </p>
                    <p class="api-description">Get status and details of a specific transaction</p>

                    <h4>Response:</h4>
                    <div class="code-block">
                        <pre>{{
  "success": true,
  "data": {{
    "transaction_id": "{method.sepay_prefix_transaction_id}ABC123XYZ",
    "amount": 50000,
    "status": "confirmed",
    "qr_url": "https://qr.sepay.vn/img?...",
    "created_at": "2026-04-04 10:30:00",
    "confirmed_at": "2026-04-04 10:35:00",
    "expired_at": "2026-04-04 11:30:00"
  }}
}}</pre>
                    </div>

                    <h4>cURL Example:</h4>
                    <div class="code-block">
                        <pre>curl -X GET {base_url}/transaction/{method.sepay_prefix_transaction_id}ABC123</pre>
                    </div>
                </div>

                <!-- API 4: List Transactions -->
                <div class="api-section">
                    <h3>4. List Transactions</h3>
                    <p>
                        <span class="api-endpoint">GET</span>
                        <span class="api-url">{base_url}/transactions</span>
                    </p>
                    <p class="api-description">Get list of transactions with pagination and filters</p>

                    <h4>Query Parameters:</h4>
                    <ul>
                        <li><code>limit</code>: Number of records (default: 50, max: 100)</li>
                        <li><code>offset</code>: Offset for pagination (default: 0)</li>
                        <li><code>status</code>: Filter by status (pending, processing, confirmed, failed, expired)</li>
                        <li><code>date_from</code>: Filter from date (YYYY-MM-DD)</li>
                        <li><code>date_to</code>: Filter to date (YYYY-MM-DD)</li>
                    </ul>

                    <h4>Response:</h4>
                    <div class="code-block">
                        <pre>{{
  "success": true,
  "data": {{
    "total": 150,
    "limit": 50,
    "offset": 0,
    "transactions": [
      {{
        "transaction_id": "{method.sepay_prefix_transaction_id}ABC123",
        "amount": 50000,
        "status": "confirmed",
        "created_at": "2026-04-04 10:30:00",
        "confirmed_at": "2026-04-04 10:35:00"
      }}
    ]
  }}
}}</pre>
                    </div>

                    <h4>cURL Example:</h4>
                    <div class="code-block">
                        <pre>curl -X GET "{base_url}/transactions?limit=50&offset=0&status=confirmed"</pre>
                    </div>
                </div>

                <!-- Error Responses -->
                <div class="api-section">
                    <h3>Error Responses</h3>
                    <p class="api-description">All errors return the same format:</p>

                    <div class="code-block">
                        <pre>{{
  "success": false,
  "error": "Error message here",
  "error_code": "ERROR_CODE"
}}</pre>
                    </div>

                    <h4>Common Error Codes:</h4>
                    <ul>
                        <li><code>METHOD_NOT_FOUND</code>: Payment method not found or inactive</li>
                        <li><code>CORS_BLOCKED</code>: Origin not allowed by CORS policy</li>
                        <li><code>INVALID_AMOUNT</code>: Amount validation failed</li>
                        <li><code>TRANSACTION_NOT_FOUND</code>: Transaction doesn't exist</li>
                        <li><code>TRANSACTION_EXPIRED</code>: Transaction has expired (>1 hour)</li>
                        <li><code>INVALID_CREDENTIALS</code>: SePay API token invalid</li>
                        <li><code>INTERNAL_ERROR</code>: Server error</li>
                    </ul>
                </div>

                <!-- Integration Notes -->
                <div style="background: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h4>💡 Integration Notes</h4>
                    <ul>
                        <li>All timestamps are in format: YYYY-MM-DD HH:MM:SS</li>
                        <li>Amount is in VND (Vietnamese Dong)</li>
                        <li>Transactions expire after 1 hour if not confirmed</li>
                        <li>For polling payment status, recommended interval: 5-10 seconds</li>
                        <li>Maximum amount per transaction: 500,000,000 VND</li>
                    </ul>
                </div>
            </div>
            '''

            wizard.api_documentation = html
