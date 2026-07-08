# -*- coding: utf-8 -*-

from odoo import http, _
from odoo.http import request, Response
import json
import logging
import requests
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
    # API Endpoint 1: Create Payment
    # ==========================================

    @http.route('/api/payment/<int:method_id>/create', type='json', auth='public', methods=['POST'], csrf=False)
    def create_payment(self, method_id, amount=None, description='', **kwargs):
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

            if payment_method.payment_provider == 'vtcpay':
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

            # Mark as processing
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
            _logger.info(f"Found {len(transactions)} transactions from SePay")

            # Remove prefix to get random code
            # transaction_id format: {prefix}{random_code}
            # Ví dụ: TEST_EO5JH16IWM -> remove 'TEST_' -> EO5JH16IWM
            transaction_code = transaction_id
            if prefix and transaction_id.startswith(prefix):
                transaction_code = transaction_id[len(prefix):]

            for tx in transactions:
                tx_content = tx.get('transaction_content', '')
                tx_amount = tx.get('amount_in', '0')

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
