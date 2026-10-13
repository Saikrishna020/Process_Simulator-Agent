"""Structured logging with secret redaction.

Redaction is defense-in-depth: normal code paths never log secret values, but a stray
str(exception) could leak one, so every log record is scrubbed too.
"""
from __future__ import annotations

import logging
import re

from .config import get_settings

_REDACTED = "***REDACTED***"


class SecretRedactionFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self._patterns: list[re.Pattern] = []
        self._refresh()

    def _refresh(self) -> None:
        settings = get_settings()
        secrets = []
        # Both keys are optional: Process Lab runs with no credentials at all.
        for secret in (settings.deepseek_api_key, settings.langsmith_api_key):
            if secret and secret.get_secret_value().strip():
                secrets.append(secret.get_secret_value())
        self._patterns = [re.compile(re.escape(s)) for s in secrets]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in self._patterns:
            msg = pattern.sub(_REDACTED, msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(f, SecretRedactionFilter) for h in root.handlers for f in h.filters):
        return  # already configured
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redaction_filter = SecretRedactionFilter()
    for handler in root.handlers:
        handler.addFilter(redaction_filter)
