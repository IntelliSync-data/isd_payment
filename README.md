# ISD Payment Module for Odoo 18

Payment Gateway Integration with Auto-Generated REST APIs

## Overview

ISD Payment is an Odoo 18 module that provides seamless payment gateway integration with automatic REST API generation. Currently supports **SePay** payment gateway (Vietnam).

### Key Features

✅ **Easy Configuration** - Set up payment methods in minutes through a user-friendly interface

✅ **Auto-Generated APIs** - REST APIs are automatically created for each payment method

✅ **QR Code Payments** - Generate QR codes for instant payment

✅ **Real-time Verification** - Confirm payments via SePay API integration

✅ **CORS Support** - Flexible CORS configuration for security

✅ **Multi-Project Support** - Create multiple payment methods for different projects

✅ **Complete Documentation** - Built-in API documentation with code examples

✅ **Transaction Management** - Track all transactions with detailed status

## Installation

### 1. Install the Module

```bash
# Copy module to your Odoo addons directory
cp -r isd_payment /path/to/odoo/custom_addons/

# Restart Odoo
sudo systemctl restart odoo

# Update apps list and install
# Go to Apps → Update Apps List → Search "ISD Payment" → Install
```

### 2. Install Python Dependencies

```bash
source /path/to/odoo-venv/bin/activate
pip install requests
```

### 3. Configure Module Icons

Copy module icons from source:

```bash
# Module icon
cp /path/to/icon-isd-pay.png /path/to/isd_payment/static/description/icon.png

# SePay icon (optional, for future use)
cp /path/to/icon-sepay.png /path/to/isd_payment/image/icon-sepay.png
```

## Quick Start

### Step 1: Create Payment Method

1. Go to **ISD Payment → Payment Methods**
2. Click **Create**
3. Fill in the form:
   - **Name**: E.g., "Desktop App Payment"
   - **Provider**: SePay
   - **Prefix**: E.g., "DESK_"
   - **SePay Configuration**:
     - SePay Host: `https://my.sepay.vn`
     - SePay QR Host: `https://qr.sepay.vn`
     - Account Number: Your bank account
     - Bank Code: E.g., `VCB`, `TCB`, `MB`
     - API Token: Your SePay API token
     - Transaction ID Prefix: E.g., "PREFIX_"
4. **Save**

### Step 2: Configure CORS (Optional)

- **Disabled**: API is public (anyone can use)
- **Enabled + Empty**: Only current Odoo domain allowed
- **Enabled + URLs**: Specify allowed origins (one per line)

Example:
```
https://example.com
https://app.example.com
http://localhost:3000
```

### Step 3: View APIs

1. Click **View APIs** button
2. See all available endpoints with examples
3. Copy code samples (cURL, Python, JavaScript)

## API Endpoints

All APIs are automatically generated at:
```
https://your-odoo-domain.com/api/payment/{method_id}/
```

### 1. Create Payment

```bash
POST /api/payment/{method_id}/create

Body:
{
  "amount": 50000,
  "description": "Order #12345"
}

Response:
{
  "success": true,
  "data": {
    "transaction_id": "PREFIX_ABC123XYZ",
    "qr_url": "https://qr.sepay.vn/img?...",
    "amount": 50000,
    "bank_account": "0123456789",
    "bank_code": "VCB",
    "created_at": "2026-04-04 10:30:00"
  }
}
```

### 2. Confirm Payment

```bash
POST /api/payment/{method_id}/confirm

Body:
{
  "transaction_id": "PREFIX_ABC123XYZ",
  "amount": 50000
}

Response:
{
  "success": true,
  "status": "confirmed",
  "message": "Payment confirmed via SePay",
  "data": {...}
}
```

### 3. Get Transaction Status

```bash
GET /api/payment/{method_id}/transaction/{transaction_id}
```

### 4. List Transactions

```bash
GET /api/payment/{method_id}/transactions?limit=50&offset=0&status=confirmed
```

## Integration Examples

### Python

```python
import requests

# Create payment
response = requests.post(
    'https://your-domain.com/api/payment/1/create',
    json={'amount': 50000, 'description': 'Test payment'}
)
data = response.json()
qr_url = data['data']['qr_url']
transaction_id = data['data']['transaction_id']

# Display QR code to user...

# Check payment status (polling)
import time
while True:
    response = requests.post(
        'https://your-domain.com/api/payment/1/confirm',
        json={'transaction_id': transaction_id, 'amount': 50000}
    )
    result = response.json()

    if result['status'] == 'confirmed':
        print("Payment confirmed!")
        break
    elif result['status'] == 'failed':
        print("Payment failed!")
        break

    time.sleep(5)  # Check every 5 seconds
```

### JavaScript

```javascript
// Create payment
const createPayment = async () => {
  const response = await fetch('https://your-domain.com/api/payment/1/create', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({amount: 50000, description: 'Test'})
  });

  const data = await response.json();
  return data.data;
};

// Confirm payment
const confirmPayment = async (transactionId, amount) => {
  const response = await fetch('https://your-domain.com/api/payment/1/confirm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({transaction_id: transactionId, amount: amount})
  });

  return await response.json();
};

// Usage
const payment = await createPayment();
console.log('QR URL:', payment.qr_url);

// Poll for confirmation
const interval = setInterval(async () => {
  const result = await confirmPayment(payment.transaction_id, payment.amount);

  if (result.status === 'confirmed') {
    console.log('Payment confirmed!');
    clearInterval(interval);
  }
}, 5000);
```

### C# (.NET)

```csharp
using System.Net.Http;
using System.Text.Json;

var client = new HttpClient();

// Create payment
var createRequest = new
{
    amount = 50000,
    description = "Test payment"
};

var response = await client.PostAsJsonAsync(
    "https://your-domain.com/api/payment/1/create",
    createRequest
);

var result = await response.Content.ReadFromJsonAsync<PaymentResponse>();
var qrUrl = result.Data.QrUrl;
var transactionId = result.Data.TransactionId;

// Show QR code to user...

// Confirm payment
var confirmRequest = new
{
    transaction_id = transactionId,
    amount = 50000
};

var confirmResponse = await client.PostAsJsonAsync(
    "https://your-domain.com/api/payment/1/confirm",
    confirmRequest
);

var confirmResult = await confirmResponse.Content.ReadFromJsonAsync<ConfirmResponse>();
```

## Use Cases

### 1. Desktop Application

Create a payment method for your desktop app:
- Name: "Desktop Payment"
- CORS: Disabled (public API)
- Use the APIs in your C#/Python/Java desktop app

### 2. Mobile Application

Create a payment method for mobile:
- Name: "Mobile Payment"
- CORS: Enabled with your API backend URL
- Call APIs from your mobile app backend

### 3. Website Integration

Create a payment method for website:
- Name: "Website Payment"
- CORS: Enabled with your website URLs
- Call APIs directly from JavaScript

### 4. Multi-Environment Setup

Create separate payment methods:
- **Development**: Sandbox SePay account, CORS disabled
- **Staging**: Test SePay account, CORS with staging URLs
- **Production**: Live SePay account, CORS with production URLs

## Configuration

### Getting SePay API Token

1. Go to [SePay](https://my.sepay.vn)
2. Login to your account
3. Navigate to API Settings
4. Generate API Token
5. Copy and paste into Odoo payment method

### CORS Best Practices

**Development**:
- Disable CORS for testing

**Production**:
- Enable CORS
- Whitelist only trusted domains
- Use HTTPS URLs only

### Transaction Expiration

- Transactions expire after **1 hour** if not confirmed
- Expired transactions are automatically marked
- Cron job runs every hour to expire old transactions

## Troubleshooting

### API Returns "CORS_BLOCKED"

- Check CORS settings in payment method
- Ensure your origin is in allowed origins list
- Current domain is always allowed if CORS is enabled

### Payment Not Confirmed

- Check SePay API token is valid
- Verify bank account and bank code are correct
- Ensure payment amount matches exactly
- Check transaction hasn't expired (>1 hour)

### "Invalid SePay API credentials"

- API token is incorrect or expired
- Regenerate token in SePay dashboard
- Update in payment method configuration

### QR Code Not Working

- Verify SePay QR Host URL is correct
- Check bank account number format
- Ensure bank code is valid

## Security

- ✅ CORS protection
- ✅ Input validation
- ✅ SQL injection prevention (Odoo ORM)
- ✅ Rate limiting (recommended via nginx)
- ✅ API token authentication with SePay
- ✅ Transaction expiration
- ✅ Request origin logging

## Development

### Adding New Payment Provider

1. Add provider to selection in `models/isd_payment_method.py`
2. Add provider-specific fields
3. Update controller logic in `controllers/main.py`
4. Update documentation wizard

### Custom Fields

Add custom fields to transaction model for tracking:

```python
custom_field = fields.Char(string='Custom Field')
```

### Webhooks (Future)

Currently uses polling. Webhook support planned for v2.0.

## Changelog

### Version 1.0 (Current)

- ✅ SePay payment gateway integration
- ✅ Auto-generated REST APIs
- ✅ QR code payment
- ✅ Payment confirmation
- ✅ Transaction management
- ✅ CORS configuration
- ✅ API documentation wizard
- ✅ Multi-payment method support

### Planned Features (v2.0)

- [ ] Additional payment providers (VNPay, MoMo, ZaloPay)
- [ ] Webhook callbacks
- [ ] Payment analytics dashboard
- [ ] Auto-refund functionality
- [ ] Multi-currency support
- [ ] Payment link generation
- [ ] Email/SMS notifications

## Support

- **Documentation**: See built-in API documentation wizard
- **Issues**: Report bugs on GitHub
- **Email**: support@intellisyncdata.com

## License

LGPL-3

## Credits

**Author**: IntelliSyncData

**Website**: https://intellisyncdata.com

---

**Happy Payment Integration! 🚀**
