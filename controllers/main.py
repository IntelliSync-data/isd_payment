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

            # Generate transaction ID
            transaction_id = request.env['isd_payment.transaction'].sudo().generate_transaction_id(
                payment_method.sepay_prefix_transaction_id
            )

            # Build QR URL
            qr_url = f"{payment_method.sepay_qr_host}/img?acc={payment_method.sepay_acc_number}&bank={payment_method.sepay_acc_bank}&amount={int(amount)}&des={quote(transaction_id)}"

            # Get request info
            request_origin = request.httprequest.headers.get('Origin', '')
            request_ip = request.httprequest.remote_addr

            # Create transaction record
            transaction = request.env['isd_payment.transaction'].sudo().create({
                'payment_method_id': payment_method.id,
                'transaction_id': transaction_id,
                'amount': amount,
                'description': description,
                'qr_url': qr_url,
                'bank_account': payment_method.sepay_acc_number,
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
                    'bank_account': payment_method.sepay_acc_number,
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

            # Call SePay API
            sepay_result = self._check_sepay_transaction(
                payment_method,
                transaction_id,
                int(amount),
                prefix=payment_method.sepay_prefix_transaction_id
            )

            if sepay_result.get('found'):
                # Payment confirmed
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
                # Still processing
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
            url = f"{payment_method.sepay_host}/userapi/transactions/list"
            params = {
                'transaction_date_min': on_date,
                'amount_in': amount,
                'bank_brand_name': payment_method.sepay_acc_bank,
            }

            # Headers
            headers = {
                'Authorization': f'Bearer {payment_method.sepay_api_token}',
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
