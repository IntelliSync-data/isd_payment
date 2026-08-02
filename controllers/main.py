# -*- coding: utf-8 -*-

from odoo import http, _
from odoo.http import request, Response
import json
import logging
import requests
import re
from datetime import datetime, timedelta
from urllib.parse import quote

_logger = logging.getLogger(__name__)


class IsdPaymentController(http.Controller):

    def _get_payment_method(self, method_id):
        """Get payment method by ID"""
        try:
            method = request.env['isd_payment.method'].sudo().browse(int(method_id))
            if not method.exists() or not method.active:
                return None
            return method
        except (ValueError, TypeError):
            return None

    def _check_cors(self, payment_method):
        """Check CORS and return appropriate headers"""
        origin = request.httprequest.headers.get('Origin', '')

        # If CORS is disabled, allow all origins
        if not payment_method.enable_cors:
            return {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                'Access-Control-Max-Age': '86400'
            }, True

        # Get allowed origins
        allowed_origins = payment_method.get_allowed_origins()

        # Check if origin is allowed
        if origin in allowed_origins:
            return {
                'Access-Control-Allow-Origin': origin,
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                'Access-Control-Max-Age': '86400'
            }, True

        # Origin not allowed
        return {}, False

    def _json_response(self, data, status=200, cors_headers=None):
        """Return JSON response with CORS headers"""
        headers = {'Content-Type': 'application/json'}
        if cors_headers:
            headers.update(cors_headers)

        return Response(
            json.dumps(data, ensure_ascii=False, indent=2),
            status=status,
            headers=headers
        )

    def _error_response(self, error_message, error_code=None, status=400, cors_headers=None):
        """Return error response"""
        data = {
            'success': False,
            'error': error_message,
        }
        if error_code:
            data['error_code'] = error_code

        return self._json_response(data, status=status, cors_headers=cors_headers)

    # ==========================================
    # CORS Preflight Handler
    # ==========================================

    @http.route('/api/payment/<int:method_id>/<path:subpath>', type='http', auth='public', methods=['OPTIONS'], csrf=False)
    def payment_api_options(self, method_id, subpath, **kwargs):
        """Handle CORS preflight requests"""
        payment_method = self._get_payment_method(method_id)
        if not payment_method:
            return self._error_response('Payment method not found', 'METHOD_NOT_FOUND', status=404)

        cors_headers, allowed = self._check_cors(payment_method)
        if not allowed:
            return self._error_response('CORS policy: Origin not allowed', 'CORS_BLOCKED', status=403)

        return Response('', status=200, headers=cors_headers)

    def _create_vtcpay_payment(self, payment_method, amount, ipn_url=''):
        """Create VTC Pay payment URL"""
        try:
            import hashlib
            import urllib.parse
            import uuid

            transaction_id = str(uuid.uuid4())
            vtc_amount = int(amount) * 1000

            query_params = {
                'amount': str(vtc_amount),
                'bill_to_email': '',
                'bill_to_phone': '',
                'currency': 'VND',
                'language': 'vi',
                'payment_type': payment_method.vtc_payment_type or 'DomesticBank',
                'reference_number': transaction_id,
                'url_return': urllib.parse.unquote_plus(ipn_url) if ipn_url else '',
                'website_id': payment_method.provider_account_id,
                'country': 'VN',
            }
            # Remove empty values
            query_params = {k: v for k, v in query_params.items() if v}

            signature_pattern = 'amount|bill_to_email|bill_to_phone|country|currency|language|payment_type|reference_number|url_return|website_id'
            signature_values = []
            for k in signature_pattern.split('|'):
                if k in query_params:
                    signature_values.append(query_params[k])
            signature_values.append(payment_method.vtc_security_code)
            signature = hashlib.sha256('|'.join(signature_values).encode()).hexdigest()
            query_params['signature'] = signature

            pay_url = f"{payment_method.provider_host}/checkout.html?" + '&'.join(
                f"{k}={urllib.parse.quote_plus(v)}" for k, v in query_params.items()
            )

            return {
                'found': True,
                'transaction_id': transaction_id,
                'redirect_url': pay_url,
            }
        except Exception as e:
            _logger.exception("Error creating VTC Pay payment")
            return {'found': False, 'message': str(e)}

    def _check_vtcpay_transaction(self, payment_method, transaction_id, amount, payment_parameters=None):
        """Check VTC Pay transaction status"""
        try:
            import hashlib
            import json

            if payment_parameters:
                # Verify IPN callback signature
                params = {}
                for part in payment_parameters.split('&'):
                    k, v = part.split('=', 1)
                    params[k] = v

                signature_pattern = 'amount|message|payment_type|reference_number|status|trans_ref_no|website_id'
                signature_values = [params.get(k, '') for k in signature_pattern.split('|')]
                signature_values.append(payment_method.vtc_security_code)

                calc_signature = hashlib.sha256('|'.join(signature_values).encode()).hexdigest()
                if params.get('signature', '').lower() != calc_signature:
                    return {'found': False, 'message': 'Invalid VTC Pay signature'}

                vtc_amount = int(amount) * 1000
                if int(float(params.get('amount', 0))) != vtc_amount:
                    return {'found': False, 'message': 'Amount mismatch'}

                order_status = int(params.get('status', -1))
            else:
                # Poll VTC Pay API
                key_sign = payment_method.provider_secret or ''
                receiver_account = payment_method.vtc_receiver_account or ''
                website_id = payment_method.provider_account_id or ''
                security_code = payment_method.vtc_security_code or ''

                signature = hashlib.md5(
                    f"{key_sign}{receiver_account}{website_id}WEBSITE{security_code}".encode()
                ).hexdigest()

                url = f"{payment_method.provider_host}/api/AccountApi/VTCPayGetOrderStatus"
                payload = json.dumps({
                    'merchantType': 'WEBSITE',
                    'intergratedID': website_id,
                    'revceiverAccount': receiver_account,
                    'sign': signature,
                    'listMerchantOrderCode': [{'orderCode': transaction_id}],
                })
                response = requests.post(
                    url,
                    headers={'Content-Type': 'application/json'},
                    data=payload,
                    timeout=30
                )
                if response.status_code != 200:
                    return {'found': False, 'message': f'VTC Pay API error: {response.status_code}'}

                res_data = response.json()
                order_list = res_data.get('ListOrderStatus', [])
                if not order_list:
                    return {'found': False, 'message': 'Order not found on VTC Pay'}

                order_status = order_list[0].get('Status', -1)

            # Status 1 = success, 7 = in review, -99 = in checking
            if order_status == 1:
                return {'found': True, 'data': {'order_status': order_status}}
            elif order_status in (7, -99):
                return {'found': False, 'message': 'VTC Pay payment in review'}
            else:
                return {'found': False, 'message': f'VTC Pay payment failed (status: {order_status})'}

        except requests.Timeout:
            return {'found': False, 'message': 'VTC Pay API timeout'}
        except Exception as e:
            _logger.exception("Error checking VTC Pay transaction")
            return {'found': False, 'message': str(e)}

    def _create_paypal_payment(self, payment_method, amount):
        """Create PayPal order and return redirect URL"""
        try:
            token = self._get_paypal_token(payment_method)
            order_url = f"{payment_method.provider_host}/v2/checkout/orders"
            exchange_rate = payment_method.paypal_usd_exchange_rate or 26300.0
            amount_usd = round(amount / exchange_rate, 2)

            body = {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "amount": {
                        "currency_code": "USD",
                        "value": str(amount_usd),
                    }
                }],
                "application_context": {
                    "locale": "en-US",
                    "shipping_preference": "NO_SHIPPING",
                    "payment_method": {
                        "payer_selected": "PAYPAL",
                        "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED",
                    },
                },
            }

            response = requests.post(
                order_url,
                headers={"Content-Type": "application/json", "Authorization": token},
                json=body,
                timeout=30
            )
            if response.status_code not in (200, 201):
                _logger.error(f"PayPal create order error: {response.status_code} {response.text}")
                return {'found': False, 'message': f'PayPal API error: {response.status_code}'}

            data = response.json()
            order_id = data.get('id', '')
            redirect_url = next(
                (link['href'] for link in data.get('links', []) if link.get('rel') == 'approve'),
                None
            )
            return {
                'found': True,
                'order_id': order_id,
                'redirect_url': redirect_url,
                'amount_usd': amount_usd,
            }
        except requests.Timeout:
            return {'found': False, 'message': 'PayPal API timeout'}
        except Exception as e:
            _logger.exception("Error creating PayPal order")
            return {'found': False, 'message': str(e)}

    def _check_paypal_transaction(self, payment_method, paypal_order_id):
        """Capture a PayPal order to confirm payment"""
        try:
            token = self._get_paypal_token(payment_method)
            capture_url = f"{payment_method.provider_host}/v2/checkout/orders/{paypal_order_id}/capture"

            response = requests.post(
                capture_url,
                headers={"Content-Type": "application/json", "Authorization": token},
                timeout=30
            )
            if response.status_code not in (200, 201):
                _logger.error(f"PayPal capture error: {response.status_code} {response.text}")
                return {'found': False, 'message': f'PayPal capture error: {response.status_code}'}

            data = response.json()
            status = data.get('status', '')
            if status != 'COMPLETED':
                return {'found': False, 'message': f'PayPal order not completed, status: {status}'}

            # Extract capture details
            capture_id = None
            payer_email = None
            purchase_units = data.get('purchase_units', [])
            if purchase_units:
                captures = purchase_units[0].get('payments', {}).get('captures', [])
                if captures:
                    capture_id = captures[0].get('id')

            payer = data.get('payer', {})
            payer_email = payer.get('email_address')

            return {
                'found': True,
                'data': {
                    'order_id': paypal_order_id,
                    'capture_id': capture_id,
                    'payer_email': payer_email,
                }
            }
        except requests.Timeout:
            return {'found': False, 'message': 'PayPal API timeout'}
        except Exception as e:
            _logger.exception("Error capturing PayPal order")
            return {'found': False, 'message': str(e)}

    def _get_paypal_token(self, payment_method):
        """Get PayPal Bearer token via OAuth2"""
        token_url = f"{payment_method.provider_host}/v1/oauth2/token"
        response = requests.post(
            token_url,
            auth=(payment_method.provider_account_id, payment_method.provider_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
            timeout=30
        )
        if response.status_code not in (200, 201):
            raise Exception(f"Could not get PayPal token: {response.status_code}")
        data = response.json()
        token_type = data.get('token_type', 'Bearer')
        access_token = data.get('access_token', '')
        if not access_token:
            raise Exception("PayPal token is empty")
        return f"{token_type} {access_token}"

    # ==========================================
    # ACB Pay Methods
    # ==========================================

    def _get_acb_token(self, payment_method):
        """Get ACB Bearer token via OAuth2 client_credentials"""
        token_url = f"{payment_method.provider_host}/acb/open/iam/id/v1/auth/realms/soba/protocol/openid-connect/token"
        import base64
        auth_str = base64.b64encode(
            f"{payment_method.provider_account_id}:{payment_method.provider_secret}".encode()
        ).decode()

        response = requests.post(
            token_url,
            headers={
                'Authorization': f'Basic {auth_str}',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={
                'client_id': payment_method.provider_account_id,
                'client_secret': payment_method.provider_secret,
                'grant_type': 'client_credentials',
                'scope': 'soba-api',
            },
            timeout=30
        )
        if response.status_code not in (200, 201):
            raise Exception(f"Could not get ACB token: {response.status_code} - {response.text}")
        _logger.info(f"ACB token HTTP status: {response.status_code}")
        data = response.json()
        access_token = data.get('access_token', '')
        if not access_token:
            _logger.error(f"ACB token response (no access_token): {json.dumps(data)[:500]}")
            raise Exception("ACB token is empty")
        _logger.info(f"ACB token OK: token_type={data.get('token_type')}, expires_in={data.get('expires_in')}, scope={data.get('scope')}, token_preview={access_token[:20]}...")
        return f"Bearer {access_token}"

    def _create_acbpay_payment(self, payment_method, amount, transaction_id, description=''):
        """Create ACB Pay QR Code via API"""
        try:
            import uuid

            token = self._get_acb_token(payment_method)
            request_id = str(uuid.uuid4())
            trace_number = str(uuid.uuid4())
            # orderId max 13 characters per ACB spec
            order_id = transaction_id[:13] if len(transaction_id) > 13 else transaction_id

            url = f"{payment_method.provider_host}/acb/open/payments/qr-payment/v1/initiate"
            headers = {
                'Authorization': token,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Client-ID': payment_method.provider_account_id,
                'X-Owner-Number': payment_method.acb_owner_number,
                'X-Request-ID': request_id,
                'X-Service': 'QRPAYMENT',
                'X-Owner-Type': 'ORG',
                'X-Provider-ID': payment_method.acb_provider_id,
            }
            body = {
                'requestTrace': request_id,
                'requestDateTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S.000+0700'),
                'requestParameters': {
                    'traceNumber': trace_number,
                    'merchantId': payment_method.acb_merchant_id,
                    'terminalId': payment_method.acb_terminal_id,
                    'userId': payment_method.acb_user_id or '',
                    'orderId': order_id,
                    'virtualAccountPrefix': payment_method.acb_virtual_account_prefix,
                    'beneficiaryName': payment_method.acb_beneficiary_name,
                    'amount': int(amount),
                    'description': re.sub(r'[^a-zA-Z0-9\s]', '', description or f'Payment {order_id}'),
                    'additionalInfo': [
                        {'key': 'key', 'value': 'value'}
                    ],
                },
            }

            _logger.info(f"ACB create QR request: URL={url}, headers={json.dumps({k:v for k,v in headers.items() if k != 'Authorization'})}, body={json.dumps(body)}")
            response = requests.post(url, headers=headers, json=body, timeout=30)

            if response.status_code not in (200, 201):
                error_detail = response.text[:500] if response.text else 'No response body'
                _logger.error(f"ACB create QR error: {response.status_code} {error_detail}")
                return {'found': False, 'message': f'ACB API error: {response.status_code} - {error_detail}'}

            data = response.json()

            # ACB returns data in responseBody
            response_body = data.get('responseBody', data.get('data', {}))
            qr_url = response_body.get('qrDataUrl', '')
            virtual_account = response_body.get('virtualAccount', '')
            acb_trace = response_body.get('traceNumber', trace_number)

            return {
                'found': True,
                'trace_number': acb_trace,
                'order_id': order_id,
                'qr_url': qr_url,
                'qr_data': json.dumps(response_body) if response_body else '',
                'virtual_account': virtual_account,
            }
        except requests.Timeout:
            return {'found': False, 'message': 'ACB API timeout'}
        except Exception as e:
            _logger.exception("Error creating ACB Pay QR")
            return {'found': False, 'message': str(e)}

    def _check_acbpay_transaction(self, payment_method, transaction_id, amount):
        """Check ACB Pay transaction via query API"""
        try:
            import uuid

            token = self._get_acb_token(payment_method)
            request_id = str(uuid.uuid4())

            url = f"{payment_method.provider_host}/acb/open/payments/qr-payment/v1/transactions"
            headers = {
                'Authorization': token,
                'Accept': 'application/json',
                'X-Client-ID': payment_method.provider_account_id,
                'X-Owner-Number': payment_method.acb_owner_number,
                'X-Request-ID': request_id,
                'X-Service': 'QRPAYMENT',
                'X-Owner-Type': 'ORG',
                'X-Provider-ID': payment_method.acb_provider_id,
            }
            params = {
                'orderId': transaction_id,
                'fromDate': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                'toDate': datetime.now().strftime('%Y-%m-%d'),
                'page': 0,
                'size': 10,
            }

            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code != 200:
                error_detail = response.text[:500] if response.text else 'No response body'
                _logger.error(f"ACB query error: {response.status_code} {error_detail}")
                return {'found': False, 'message': f'ACB API error: {response.status_code} - {error_detail}'}

            data = response.json()
            transactions = data.get('data', {}).get('content', [])

            for tx in transactions:
                tx_amount = tx.get('amount', 0)
                tx_status = tx.get('status', '')
                if int(float(tx_amount)) == int(amount) and tx_status in ('SUCCESS', 'COMPLETED', 'PAID'):
                    return {
                        'found': True,
                        'data': {
                            'traceNumber': tx.get('traceNumber'),
                            'orderId': tx.get('orderId'),
                            'virtualAccount': tx.get('virtualAccount'),
                        }
                    }

            return {'found': False, 'message': 'Payment not found yet'}

        except requests.Timeout:
            return {'found': False, 'message': 'ACB API timeout'}
        except Exception as e:
            _logger.exception("Error checking ACB Pay transaction")
            return {'found': False, 'message': str(e)}

    # ==========================================
    # ACB Pay Webhook Endpoints
    # ==========================================

    @http.route('/acb_pay/webhook/realtime', type='http', auth='public', methods=['POST'], csrf=False)
    def acb_webhook_realtime(self, **kwargs):
        """
        Receive realtime transaction notification from ACB (TRANSACTION_UPDATE)

        ACB pushes transaction data when QR payment is completed.
        We respond with ACB's expected response format.
        """
        try:
            # Get API key from header (check both Authorization and x-api-key)
            auth_header = request.httprequest.headers.get('Authorization', '')
            xapi_header = request.httprequest.headers.get('x-api-key', '')
            api_key = auth_header or xapi_header
            _logger.info(f"ACB webhook auth: Authorization={'***' + auth_header[-6:] if auth_header else 'empty'}, x-api-key={'***' + xapi_header[-6:] if xapi_header else 'empty'}, using={'Authorization' if auth_header else 'x-api-key' if xapi_header else 'none'}")
            request_ip = request.httprequest.remote_addr

            # Parse raw JSON body (ACB sends raw JSON, not JSON-RPC)
            raw_body = request.httprequest.get_data(as_text=True)
            _logger.info(f"ACB webhook raw body: {raw_body}")
            data = json.loads(raw_body) if raw_body else {}
            request_trace = data.get('requestTrace', '')
            request_date_time = data.get('requestDateTime', '')

            _logger.info(f"ACB webhook realtime received: {json.dumps(data)}")

            # Helper to build ACB response format as HTTP Response
            def _acb_response(code, message, reference=''):
                resp = {
                    'requestTrace': request_trace,
                    'responseDateTime': request_date_time,
                    'responseStatus': {
                        'responseCode': code,
                        'responseMessage': message,
                    },
                    'responseBody': {
                        'index': 1,
                        'referenceCode': reference,
                    },
                }
                _logger.info(f"ACB webhook response: {json.dumps(resp)}")
                return Response(
                    json.dumps(resp),
                    content_type='application/json',
                    status=200,
                )

            # Find matching payment method by API key
            payment_method = request.env['isd_payment.method'].sudo().search([
                ('payment_provider', '=', 'acbpay'),
                ('acb_api_key', '=', api_key),
                ('active', '=', True),
            ], limit=1)

            if not payment_method:
                _logger.warning(f"ACB webhook: invalid API key from {request_ip}")
                return _acb_response('40100001', 'Invalid API key')

            # Verify source IP
            allowed_ips = [ip.strip() for ip in (payment_method.acb_webhook_ip or '').split(',') if ip.strip()]
            if allowed_ips and request_ip not in allowed_ips:
                _logger.warning(f"ACB webhook: blocked IP {request_ip}, allowed: {allowed_ips}")
                return _acb_response('40300001', 'IP not allowed')

            # Parse ACB webhook structure
            request_params = data.get('requestParameters', {})
            master_meta = request_params.get('masterMeta', {})
            client_id = master_meta.get('clientId', '')
            checksum = master_meta.get('checksum', '')

            req = request_params.get('request', {})
            request_meta = req.get('requestMeta', {})
            request_code = request_meta.get('requestCode', '')

            _logger.info(f"ACB webhook: clientId={client_id}, checksum={checksum}, requestCode={request_code}")

            # Only process TRANSACTION_UPDATE
            if request_code != 'TRANSACTION_UPDATE':
                _logger.info(f"ACB webhook: ignoring requestCode={request_code}")
                return _acb_response('00000000', 'Ignored')

            # Get transactions list
            request_params_inner = req.get('requestParams', {})
            transactions = request_params_inner.get('transactions', [])

            if not transactions:
                _logger.warning("ACB webhook: no transactions in payload")
                return _acb_response('40000001', 'No transactions')

            # Process each transaction
            confirmed_count = 0
            last_ref = ''
            for tx in transactions:
                tx_status = tx.get('transactionStatus', '')
                tx_amount = tx.get('amount', 0)
                effective_date = tx.get('effectiveDate', '')
                tx_entity = tx.get('transactionEntityAttribute', {})
                trace_number = tx_entity.get('traceNumber', '') or ''
                virtual_account = tx_entity.get('virtualAccount', '') or ''
                reference_number = tx_entity.get('referenceNumber', '') or ''
                beneficiary_name = tx_entity.get('beneficiaryName', '') or ''
                merchant_id = tx_entity.get('custom1', '') or ''
                terminal_id = tx_entity.get('custom2', '') or ''

                _logger.info(
                    f"ACB webhook tx: status={tx_status}, amount={tx_amount}, "
                    f"traceNumber={trace_number}, virtualAccount={virtual_account}, "
                    f"referenceNumber={reference_number}, beneficiaryName={beneficiary_name}, "
                    f"merchantId={merchant_id}, terminalId={terminal_id}, effectiveDate={effective_date}"
                )

                # Only confirm when status is COMPLETED
                if tx_status != 'COMPLETED':
                    _logger.info(f"ACB webhook: status={tx_status}, skipping (not COMPLETED)")
                    continue

                # Find transaction: try traceNumber -> virtualAccount -> referenceNumber
                transaction = None
                if trace_number:
                    transaction = request.env['isd_payment.transaction'].sudo().search([
                        ('acb_trace_number', '=', trace_number),
                        ('payment_method_id', '=', payment_method.id),
                    ], limit=1)
                    if transaction:
                        _logger.info(f"ACB webhook: matched by traceNumber={trace_number}")

                if not transaction and virtual_account:
                    transaction = request.env['isd_payment.transaction'].sudo().search([
                        ('acb_virtual_account', '=', virtual_account),
                        ('payment_method_id', '=', payment_method.id),
                        ('status', 'in', ('pending', 'processing')),
                    ], limit=1)
                    if transaction:
                        _logger.info(f"ACB webhook: matched by virtualAccount={virtual_account}")

                if not transaction and reference_number:
                    transaction = request.env['isd_payment.transaction'].sudo().search([
                        ('transaction_id', '=', reference_number),
                        ('payment_method_id', '=', payment_method.id),
                    ], limit=1)
                    if transaction:
                        _logger.info(f"ACB webhook: matched by referenceNumber={reference_number}")

                if not transaction:
                    _logger.warning(f"ACB webhook: transaction not found (traceNumber={trace_number}, virtualAccount={virtual_account}, referenceNumber={reference_number})")
                    continue

                # Already confirmed — skip
                if transaction.status == 'confirmed':
                    _logger.info(f"ACB webhook: transaction {transaction.transaction_id} already confirmed, skipping")
                    last_ref = transaction.transaction_id
                    continue

                # Mark as confirmed
                transaction.mark_as_confirmed_acb({
                    'traceNumber': trace_number,
                    'virtualAccount': virtual_account,
                    'amount': tx_amount,
                    'referenceNumber': reference_number,
                })
                confirmed_count += 1
                last_ref = transaction.transaction_id
                _logger.info(f"ACB webhook: confirmed transaction {transaction.transaction_id}")

            _logger.info(f"ACB webhook: processed {len(transactions)} transactions, confirmed {confirmed_count}")
            return _acb_response('00000000', 'Success', last_ref)

        except Exception as e:
            _logger.exception("Error processing ACB webhook")
            resp = {
                'requestTrace': '',
                'responseDateTime': '',
                'responseStatus': {
                    'responseCode': '50000001',
                    'responseMessage': 'Internal error',
                },
                'responseBody': {
                    'index': 1,
                    'referenceCode': '',
                },
            }
            _logger.info(f"ACB webhook response (error): {json.dumps(resp)}")
            return Response(
                json.dumps(resp),
                content_type='application/json',
                status=200,
            )

    @http.route('/acb_pay/webhook/daily', type='http', auth='public', methods=['POST'], csrf=False)
    def acb_webhook_daily(self, **kwargs):
        """
        Receive daily transaction history from ACB (T-1 reconciliation)

        ACB sends TRANSACTION_HISTORY with batch transaction data.
        Strategy: validate auth → save raw JSON to queue → response 200 immediately.
        Reconciliation is processed later by cron job.
        """
        try:
            # Get API key from header
            auth_header = request.httprequest.headers.get('Authorization', '')
            xapi_header = request.httprequest.headers.get('x-api-key', '')
            api_key = auth_header or xapi_header
            request_ip = request.httprequest.remote_addr

            # Parse raw JSON body
            raw_body = request.httprequest.get_data(as_text=True)
            data = json.loads(raw_body) if raw_body else {}
            request_trace = data.get('requestTrace', '')
            request_date_time = data.get('requestDateTime', '')

            _logger.info(f"ACB webhook daily: requestTrace={request_trace}, ip={request_ip}")

            # Helper to build ACB response format
            def _acb_response(code, message, reference=''):
                resp = {
                    'requestTrace': request_trace,
                    'responseDateTime': request_date_time,
                    'responseStatus': {
                        'responseCode': code,
                        'responseMessage': message,
                    },
                    'responseBody': {
                        'index': 1,
                        'referenceCode': reference,
                    },
                }
                _logger.info(f"ACB webhook daily response: code={code}, message={message}")
                return Response(
                    json.dumps(resp),
                    content_type='application/json',
                    status=200,
                )

            # Find payment method by API key
            payment_method = request.env['isd_payment.method'].sudo().search([
                ('payment_provider', '=', 'acbpay'),
                ('acb_api_key', '=', api_key),
                ('active', '=', True),
            ], limit=1)

            if not payment_method:
                _logger.warning(f"ACB webhook daily: invalid API key from {request_ip}")
                return _acb_response('40100001', 'Invalid API key')

            # Verify source IP
            allowed_ips = [ip.strip() for ip in (payment_method.acb_webhook_ip or '').split(',') if ip.strip()]
            if allowed_ips and request_ip not in allowed_ips:
                _logger.warning(f"ACB webhook daily: blocked IP {request_ip}")
                return _acb_response('40300001', 'IP not allowed')

            # Parse request code
            request_params = data.get('requestParameters', {})
            req = request_params.get('request', {})
            request_meta = req.get('requestMeta', {})
            request_code = request_meta.get('requestCode', '')

            if request_code != 'TRANSACTION_HISTORY':
                _logger.info(f"ACB webhook daily: ignoring requestCode={request_code}")
                return _acb_response('00000000', 'Ignored')

            # Count transactions
            request_params_inner = req.get('requestParams', {})
            transactions = request_params_inner.get('transactions', [])
            tx_count = len(transactions)

            # Save to queue — response immediately
            request.env['isd_payment.webhook_log'].sudo().create({
                'payment_method_id': payment_method.id,
                'request_trace': request_trace,
                'request_code': request_code,
                'raw_body': raw_body,
                'transaction_count': tx_count,
                'status': 'pending',
                'request_ip': request_ip,
            })

            _logger.info(f"ACB webhook daily: queued {tx_count} transactions, requestTrace={request_trace}")
            return _acb_response('00000000', 'Success')

        except Exception as e:
            _logger.exception("Error receiving ACB daily webhook")
            resp = {
                'requestTrace': '',
                'responseDateTime': '',
                'responseStatus': {
                    'responseCode': '50000001',
                    'responseMessage': 'Internal error',
                },
                'responseBody': {
                    'index': 1,
                    'referenceCode': '',
                },
            }
            return Response(
                json.dumps(resp),
                content_type='application/json',
                status=200,
            )

    # ==========================================
    # API Endpoint 1: Create Payment
    # ==========================================

    @http.route('/api/payment/<int:method_id>/create', type='json', auth='public', methods=['POST'], csrf=False)
    def create_payment(self, method_id, amount=None, description='', branch='', **kwargs):
        """
        Create payment and generate QR code

        POST /api/payment/{method_id}/create
        Body: {"amount": 50000, "description": "Optional"}
        """
        try:
            # Get payment method
            payment_method = self._get_payment_method(method_id)
            if not payment_method:
                return {
                    'success': False,
                    'error': 'Payment method not found',
                    'error_code': 'METHOD_NOT_FOUND'
                }

            # Check CORS
            cors_headers, allowed = self._check_cors(payment_method)
            if not allowed:
                return {
                    'success': False,
                    'error': 'CORS policy: Origin not allowed',
                    'error_code': 'CORS_BLOCKED'
                }

            # Validate amount
            if not amount or not isinstance(amount, (int, float)):
                return {
                    'success': False,
                    'error': 'Amount is required and must be a number',
                    'error_code': 'INVALID_AMOUNT'
                }

            if amount <= 0:
                return {
                    'success': False,
                    'error': 'Amount must be greater than 0',
                    'error_code': 'INVALID_AMOUNT'
                }

            if amount > 500000000:
                return {
                    'success': False,
                    'error': 'Amount exceeds maximum allowed (500,000,000 VND)',
                    'error_code': 'AMOUNT_TOO_LARGE'
                }

            # Get request info
            request_origin = request.httprequest.headers.get('Origin', '')
            request_ip = request.httprequest.remote_addr

            if payment_method.payment_provider == 'acbpay':
                # ACB Pay: generate QR code via ACB API
                transaction_id = request.env['isd_payment.transaction'].sudo().generate_transaction_id(
                    payment_method.prefix
                )
                acb_result = self._create_acbpay_payment(payment_method, amount, transaction_id, description)
                if not acb_result.get('found'):
                    return {
                        'success': False,
                        'error': acb_result.get('message', 'ACB Pay error'),
                        'error_code': 'ACBPAY_ERROR'
                    }

                transaction = request.env['isd_payment.transaction'].sudo().create({
                    'payment_method_id': payment_method.id,
                    'transaction_id': transaction_id,
                    'amount': amount,
                    'description': description,
                    'branch': branch,
                    'qr_url': acb_result.get('qr_url'),
                    'acb_trace_number': acb_result.get('trace_number'),
                    'acb_order_id': acb_result.get('order_id'),
                    'acb_virtual_account': acb_result.get('virtual_account'),
                    'acb_qr_data': acb_result.get('qr_data'),
                    'status': 'pending',
                    'request_origin': request_origin,
                    'request_ip': request_ip,
                })

                return {
                    'success': True,
                    'data': {
                        'transaction_id': transaction_id,
                        'qr_url': acb_result.get('qr_url'),
                        'amount': amount,
                        'created_at': transaction.create_date.strftime('%Y-%m-%d %H:%M:%S') if transaction.create_date else None,
                    }
                }

            elif payment_method.payment_provider == 'vtcpay':
                # VTC Pay: generate redirect URL
                base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
                ipn_url = f"{base_url}/isd_payment/vtcpay/ipn"
                vtc_result = self._create_vtcpay_payment(payment_method, amount, ipn_url)
                if not vtc_result.get('found'):
                    return {
                        'success': False,
                        'error': vtc_result.get('message', 'VTC Pay error'),
                        'error_code': 'VTCPAY_ERROR'
                    }

                transaction_id = vtc_result['transaction_id']
                transaction = request.env['isd_payment.transaction'].sudo().create({
                    'payment_method_id': payment_method.id,
                    'transaction_id': transaction_id,
                    'amount': amount,
                    'description': description,
                    'branch': branch,
                    'paypal_redirect_url': vtc_result.get('redirect_url'),
                    'status': 'pending',
                    'request_origin': request_origin,
                    'request_ip': request_ip,
                })

                return {
                    'success': True,
                    'data': {
                        'transaction_id': transaction_id,
                        'redirect_url': vtc_result.get('redirect_url'),
                        'amount': amount,
                        'created_at': transaction.create_date.strftime('%Y-%m-%d %H:%M:%S') if transaction.create_date else None,
                    }
                }

            elif payment_method.payment_provider == 'paypal':
                # PayPal: create order and return redirect URL
                paypal_result = self._create_paypal_payment(payment_method, amount)
                if not paypal_result.get('found'):
                    return {
                        'success': False,
                        'error': paypal_result.get('message', 'PayPal error'),
                        'error_code': 'PAYPAL_ERROR'
                    }

                order_id = paypal_result['order_id']
                transaction = request.env['isd_payment.transaction'].sudo().create({
                    'payment_method_id': payment_method.id,
                    'transaction_id': order_id,
                    'amount': amount,
                    'amount_usd': paypal_result['amount_usd'],
                    'description': description,
                    'branch': branch,
                    'paypal_order_id': order_id,
                    'paypal_redirect_url': paypal_result.get('redirect_url'),
                    'status': 'pending',
                    'request_origin': request_origin,
                    'request_ip': request_ip,
                })

                return {
                    'success': True,
                    'data': {
                        'transaction_id': order_id,
                        'redirect_url': paypal_result.get('redirect_url'),
                        'amount': amount,
                        'amount_usd': paypal_result['amount_usd'],
                        'created_at': transaction.create_date.strftime('%Y-%m-%d %H:%M:%S') if transaction.create_date else None,
                    }
                }

            else:
                # SePay: generate QR code
                transaction_id = request.env['isd_payment.transaction'].sudo().generate_transaction_id(
                    payment_method.prefix
                )

                qr_url = f"{payment_method.sepay_qr_host}/img?acc={payment_method.provider_account_id}&bank={payment_method.sepay_acc_bank}&amount={int(amount)}&des={quote(transaction_id)}"

                transaction = request.env['isd_payment.transaction'].sudo().create({
                    'payment_method_id': payment_method.id,
                    'transaction_id': transaction_id,
                    'amount': amount,
                    'description': description,
                    'branch': branch,
                    'qr_url': qr_url,
                    'bank_account': payment_method.provider_account_id,
                    'bank_code': payment_method.sepay_acc_bank,
                    'status': 'pending',
                    'request_origin': request_origin,
                    'request_ip': request_ip,
                })

                return {
                    'success': True,
                    'data': {
                        'transaction_id': transaction.transaction_id,
                        'qr_url': qr_url,
                        'amount': amount,
                        'bank_account': payment_method.provider_account_id,
                        'bank_code': payment_method.sepay_acc_bank,
                        'created_at': transaction.create_date.strftime('%Y-%m-%d %H:%M:%S') if transaction.create_date else None,
                    }
                }

        except Exception as e:
            _logger.exception("Error creating payment")
            return {
                'success': False,
                'error': str(e),
                'error_code': 'INTERNAL_ERROR'
            }

    # ==========================================
    # API Endpoint 2: Confirm Payment
    # ==========================================

    @http.route('/api/payment/<int:method_id>/confirm', type='json', auth='public', methods=['POST'], csrf=False)
    def confirm_payment(self, method_id, transaction_id=None, amount=None, **kwargs):
        """
        Confirm payment by checking SePay API

        POST /api/payment/{method_id}/confirm
        Body: {"transaction_id": "PREFIX_ABC123", "amount": 50000}
        """
        try:
            # Get payment method
            payment_method = self._get_payment_method(method_id)
            if not payment_method:
                return {
                    'success': False,
                    'error': 'Payment method not found',
                    'error_code': 'METHOD_NOT_FOUND'
                }

            # Check CORS
            cors_headers, allowed = self._check_cors(payment_method)
            if not allowed:
                return {
                    'success': False,
                    'error': 'CORS policy: Origin not allowed',
                    'error_code': 'CORS_BLOCKED'
                }

            # Validate input
            if not transaction_id:
                return {
                    'success': False,
                    'error': 'Transaction ID is required',
                    'error_code': 'MISSING_TRANSACTION_ID'
                }

            if not amount or not isinstance(amount, (int, float)):
                return {
                    'success': False,
                    'error': 'Amount is required and must be a number',
                    'error_code': 'INVALID_AMOUNT'
                }

            # Find transaction
            transaction = request.env['isd_payment.transaction'].sudo().search([
                ('transaction_id', '=', transaction_id),
                ('payment_method_id', '=', payment_method.id)
            ], limit=1)

            if not transaction:
                return {
                    'success': False,
                    'error': 'Transaction not found',
                    'error_code': 'TRANSACTION_NOT_FOUND'
                }

            # Check if already confirmed
            if transaction.status == 'confirmed':
                return {
                    'success': True,
                    'status': 'confirmed',
                    'message': 'Payment already confirmed',
                    'data': {
                        'transaction_id': transaction.transaction_id,
                        'amount': transaction.amount,
                        'confirmed_at': transaction.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.confirmed_at else None,
                        'sepay_transaction_id': transaction.sepay_transaction_id,
                        'sepay_reference': transaction.sepay_reference,
                    }
                }

            # Check if expired
            if transaction.is_expired:
                transaction.mark_as_expired()
                return {
                    'success': False,
                    'status': 'expired',
                    'message': 'Transaction has expired',
                    'error_code': 'TRANSACTION_EXPIRED'
                }

            if payment_method.payment_provider == 'acbpay':
                # ACB Pay: check DB only (payment confirmation comes via webhook, no DB update here)
                if transaction.status == 'confirmed':
                    return {
                        'success': True,
                        'status': 'confirmed',
                        'message': 'Payment confirmed via ACB Pay',
                        'data': {
                            'transaction_id': transaction.transaction_id,
                            'amount': transaction.amount,
                            'confirmed_at': transaction.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.confirmed_at else None,
                            'acb_trace_number': transaction.acb_trace_number,
                            'acb_order_id': transaction.acb_order_id,
                        }
                    }
                else:
                    return {
                        'success': True,
                        'status': 'pending',
                        'message': 'Waiting for payment confirmation from ACB',
                    }

            # Non-ACB providers: mark as processing and poll
            transaction.mark_as_processing()

            if payment_method.payment_provider == 'vtcpay':
                # VTC Pay: poll order status
                vtc_result = self._check_vtcpay_transaction(payment_method, transaction_id, amount)

                if vtc_result.get('found'):
                    transaction.mark_as_confirmed()
                    return {
                        'success': True,
                        'status': 'confirmed',
                        'message': 'Payment confirmed via VTC Pay',
                        'data': {
                            'transaction_id': transaction.transaction_id,
                            'amount': transaction.amount,
                            'confirmed_at': transaction.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.confirmed_at else None,
                        }
                    }
                else:
                    return {
                        'success': True,
                        'status': 'processing',
                        'message': vtc_result.get('message', 'VTC Pay payment is being processed')
                    }

            elif payment_method.payment_provider == 'paypal':
                # PayPal: capture the order using PayPal order ID
                paypal_order_id = transaction.paypal_order_id or transaction_id
                paypal_result = self._check_paypal_transaction(payment_method, paypal_order_id)

                if paypal_result.get('found'):
                    transaction.mark_as_confirmed_paypal(paypal_result.get('data'))
                    return {
                        'success': True,
                        'status': 'confirmed',
                        'message': 'Payment confirmed via PayPal',
                        'data': {
                            'transaction_id': transaction.transaction_id,
                            'amount': transaction.amount,
                            'amount_usd': transaction.amount_usd,
                            'confirmed_at': transaction.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.confirmed_at else None,
                            'paypal_order_id': transaction.paypal_order_id,
                            'paypal_capture_id': transaction.paypal_capture_id,
                            'paypal_payer_email': transaction.paypal_payer_email,
                        }
                    }
                else:
                    return {
                        'success': True,
                        'status': 'processing',
                        'message': paypal_result.get('message', 'PayPal payment is being processed')
                    }

            else:
                # SePay: poll SePay API
                sepay_result = self._check_sepay_transaction(
                    payment_method,
                    transaction_id,
                    int(amount),
                    prefix=payment_method.prefix
                )

                if sepay_result.get('found'):
                    transaction.mark_as_confirmed(sepay_result.get('data'))
                    return {
                        'success': True,
                        'status': 'confirmed',
                        'message': 'Payment confirmed via SePay',
                        'data': {
                            'transaction_id': transaction.transaction_id,
                            'amount': transaction.amount,
                            'confirmed_at': transaction.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.confirmed_at else None,
                            'sepay_transaction_id': transaction.sepay_transaction_id,
                            'sepay_reference': transaction.sepay_reference,
                        }
                    }
                else:
                    return {
                        'success': True,
                        'status': 'processing',
                        'message': sepay_result.get('message', 'Payment is being processed')
                    }

        except Exception as e:
            _logger.exception("Error confirming payment")
            return {
                'success': False,
                'status': 'failed',
                'error': str(e),
                'error_code': 'INTERNAL_ERROR'
            }

    def _check_sepay_transaction(self, payment_method, transaction_id, amount, prefix=''):
        """Check SePay API for transaction"""
        try:
            # Calculate date range (today - 1 day)
            on_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

            # Build API URL
            url = f"{payment_method.provider_host}/userapi/transactions/list"
            params = {
                'transaction_date_min': on_date,
                'amount_in': amount,
                'bank_brand_name': payment_method.sepay_acc_bank,
            }

            # Headers
            headers = {
                'Authorization': f'Bearer {payment_method.provider_secret}',
                'Accept': 'application/json',
            }

            # Make request
            response = requests.get(url, params=params, headers=headers, timeout=30)

            if response.status_code == 401:
                return {
                    'found': False,
                    'message': 'Invalid SePay API credentials',
                    'error_code': 'INVALID_CREDENTIALS'
                }

            if response.status_code != 200:
                _logger.error(f"SePay API error: {response.status_code}, Body: {response.text}")
                return {
                    'found': False,
                    'message': f'SePay API error: {response.status_code}'
                }

            # Parse response
            response_data = response.json()

            if response_data.get('status') != 200:
                error_msg = response_data.get('error', 'Unknown error')
                _logger.error(f"SePay response error: {error_msg}")
                return {
                    'found': False,
                    'message': f'Error from SePay: {error_msg}'
                }

            # Search for matching transaction
            transactions = response_data.get('transactions', [])
            _logger.info(f"SePay check: transaction_id={transaction_id}, amount={amount}, prefix={prefix}, found {len(transactions)} transactions")

            # Remove prefix to get random code
            # transaction_id format: {prefix}{random_code}
            # Example: TEST_EO5JH16IWM -> remove 'TEST_' -> EO5JH16IWM
            transaction_code = transaction_id
            if prefix and transaction_id.startswith(prefix):
                transaction_code = transaction_id[len(prefix):]

            _logger.info(f"SePay matching: looking for code '{transaction_code}' in transaction contents")

            for tx in transactions:
                tx_content = tx.get('transaction_content', '')
                tx_amount = tx.get('amount_in', '0')
                _logger.info(f"SePay tx: content='{tx_content}', amount_in={tx_amount}")

                # Match transaction content (case-insensitive)
                if transaction_code.upper() in tx_content.upper():
                    # Match amount
                    try:
                        if int(float(tx_amount)) == amount:
                            _logger.info(f"Payment confirmed: {transaction_id}")
                            return {
                                'found': True,
                                'data': {
                                    'id': tx.get('id'),
                                    'transaction_content': tx_content,
                                    'reference_number': tx.get('reference_number'),
                                }
                            }
                    except (ValueError, TypeError):
                        continue

            # Not found
            return {
                'found': False,
                'message': 'Payment not found yet'
            }

        except requests.Timeout:
            _logger.error("SePay API timeout")
            return {
                'found': False,
                'message': 'Request timeout - please try again'
            }
        except requests.RequestException as e:
            _logger.exception("SePay API request error")
            return {
                'found': False,
                'message': f'Network error: {str(e)}'
            }
        except Exception as e:
            _logger.exception("Error checking SePay transaction")
            return {
                'found': False,
                'message': f'Error: {str(e)}'
            }

    # ==========================================
    # API Endpoint 3: Get Transaction Status
    # ==========================================

    @http.route('/api/payment/<int:method_id>/transaction/<string:transaction_id>', type='json', auth='public', methods=['GET'], csrf=False)
    def get_transaction(self, method_id, transaction_id, **kwargs):
        """
        Get transaction status

        GET /api/payment/{method_id}/transaction/{transaction_id}
        """
        try:
            # Get payment method
            payment_method = self._get_payment_method(method_id)
            if not payment_method:
                return {
                    'success': False,
                    'error': 'Payment method not found',
                    'error_code': 'METHOD_NOT_FOUND'
                }

            # Check CORS
            cors_headers, allowed = self._check_cors(payment_method)
            if not allowed:
                return {
                    'success': False,
                    'error': 'CORS policy: Origin not allowed',
                    'error_code': 'CORS_BLOCKED'
                }

            # Find transaction
            transaction = request.env['isd_payment.transaction'].sudo().search([
                ('transaction_id', '=', transaction_id),
                ('payment_method_id', '=', payment_method.id)
            ], limit=1)

            if not transaction:
                return {
                    'success': False,
                    'error': 'Transaction not found',
                    'error_code': 'TRANSACTION_NOT_FOUND'
                }

            # Return transaction data
            return {
                'success': True,
                'data': {
                    'transaction_id': transaction.transaction_id,
                    'amount': transaction.amount,
                    'status': transaction.status,
                    'qr_url': transaction.qr_url,
                    'created_at': transaction.create_date.strftime('%Y-%m-%d %H:%M:%S') if transaction.create_date else None,
                    'confirmed_at': transaction.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.confirmed_at else None,
                    'expired_at': transaction.expired_at.strftime('%Y-%m-%d %H:%M:%S') if transaction.expired_at else None,
                }
            }

        except Exception as e:
            _logger.exception("Error getting transaction")
            return {
                'success': False,
                'error': str(e),
                'error_code': 'INTERNAL_ERROR'
            }

    # ==========================================
    # API Endpoint 4: List Transactions
    # ==========================================

    @http.route('/api/payment/<int:method_id>/transactions', type='json', auth='public', methods=['GET'], csrf=False)
    def list_transactions(self, method_id, **kwargs):
        """
        List transactions with pagination

        GET /api/payment/{method_id}/transactions?limit=50&offset=0&status=confirmed
        """
        try:
            # Get payment method
            payment_method = self._get_payment_method(method_id)
            if not payment_method:
                return {
                    'success': False,
                    'error': 'Payment method not found',
                    'error_code': 'METHOD_NOT_FOUND'
                }

            # Check CORS
            cors_headers, allowed = self._check_cors(payment_method)
            if not allowed:
                return {
                    'success': False,
                    'error': 'CORS policy: Origin not allowed',
                    'error_code': 'CORS_BLOCKED'
                }

            # Get params
            limit = min(int(kwargs.get('limit', 50)), 100)  # Max 100
            offset = int(kwargs.get('offset', 0))
            status = kwargs.get('status')
            date_from = kwargs.get('date_from')
            date_to = kwargs.get('date_to')

            # Build domain
            domain = [('payment_method_id', '=', payment_method.id)]

            if status:
                domain.append(('status', '=', status))

            if date_from:
                domain.append(('create_date', '>=', f"{date_from} 00:00:00"))

            if date_to:
                domain.append(('create_date', '<=', f"{date_to} 23:59:59"))

            # Search transactions
            Transaction = request.env['isd_payment.transaction'].sudo()
            total = Transaction.search_count(domain)
            transactions = Transaction.search(domain, limit=limit, offset=offset, order='create_date desc')

            # Build result
            transaction_list = []
            for tx in transactions:
                transaction_list.append({
                    'transaction_id': tx.transaction_id,
                    'amount': tx.amount,
                    'status': tx.status,
                    'created_at': tx.create_date.strftime('%Y-%m-%d %H:%M:%S') if tx.create_date else None,
                    'confirmed_at': tx.confirmed_at.strftime('%Y-%m-%d %H:%M:%S') if tx.confirmed_at else None,
                })

            return {
                'success': True,
                'data': {
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                    'transactions': transaction_list,
                }
            }

        except Exception as e:
            _logger.exception("Error listing transactions")
            return {
                'success': False,
                'error': str(e),
                'error_code': 'INTERNAL_ERROR'
            }
