import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Say out loud what the code used to assume: PayPal charges in USD and
    every other gateway in VND. Stating it per method keeps today's behaviour
    while letting a second USD gateway exist tomorrow."""
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'isd_payment_method' AND column_name = 'currency'
    """)
    if not cr.fetchone():
        return

    cr.execute("""
        UPDATE isd_payment_method
        SET currency = CASE WHEN payment_provider = 'paypal' THEN 'usd' ELSE 'vnd' END
        WHERE currency IS NULL OR currency = 'vnd'
    """)
    _logger.info("isd_payment: set the charge currency on %s methods", cr.rowcount)
