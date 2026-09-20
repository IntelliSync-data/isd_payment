import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Methods that already exist were set up against the real providers, so mark them
    Live. PayPal keeps whatever its own Mode field says."""
    cr.execute("""
        UPDATE isd_payment_method
        SET environment = 'live'
        WHERE environment IS NULL OR environment = 'test'
    """)
    _logger.info("isd_payment: marked %s payment methods as live", cr.rowcount)

    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'isd_payment_method' AND column_name = 'paypal_mode'
    """)
    if not cr.fetchone():
        return

    cr.execute("""
        UPDATE isd_payment_method
        SET environment = 'test'
        WHERE payment_provider = 'paypal' AND paypal_mode = 'sandbox'
    """)
    _logger.info("isd_payment: marked %s PayPal sandbox methods as test", cr.rowcount)
