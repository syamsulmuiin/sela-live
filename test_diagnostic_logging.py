"""The production named logger must emit aggregate diagnostics without root setup."""
import logging
import unittest

class DiagnosticLoggingTests(unittest.TestCase):
    def test_aggregate_logger_is_enabled_and_has_a_stderr_handler(self):
        from voice_app.server import logger
        self.assertTrue(logger.isEnabledFor(logging.INFO))
        self.assertTrue(any(isinstance(h, logging.StreamHandler) for h in logger.handlers))
        self.assertFalse(logger.propagate)
