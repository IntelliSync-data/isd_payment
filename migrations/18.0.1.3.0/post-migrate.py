import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Say what every existing Cash method already was: paid in person.

    cash_type only defaults on rows created from now on, so without this the
    radio opens blank on methods that have been collecting cash for months.
    """
    cr.execute("""
        UPDATE isd_payment_method
           SET cash_type = 'cod'
         WHERE payment_provider = 'cash'
           AND cash_type IS NULL
    """)
    _logger.info("isd_payment: marked %s cash methods as collect-in-person", cr.rowcount)
