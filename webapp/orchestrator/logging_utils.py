"""Structured logging with secret redaction, and JSON run-manifest persistence.

Redaction is defense-in-depth: normal code paths never log secret values, but a stray
str(exception) or subprocess env dump could leak one, so every log record is scrubbed too.
"""
from __future__ import annotations

import json
import logging
import re

from .config import get_settings
from .schemas import RunManifest

_REDACTED = "***REDACTED***"


class SecretRedactionFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self._patterns: list[re.Pattern] = []
        self._refresh()

    def _refresh(self) -> None:
        settings = get_settings()
        secrets = [settings.deepseek_api_key.get_secret_value()]
        if settings.langsmith_api_key:
            secrets.append(settings.langsmith_api_key.get_secret_value())
        self._patterns = [re.compile(re.escape(s)) for s in secrets if s]

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
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    redaction_filter = SecretRedactionFilter()
    for handler in root.handlers:
        handler.addFilter(redaction_filter)


def write_manifest(manifest: RunManifest) -> str:
    settings = get_settings()
    path = settings.runs_dir / f"{manifest.run_id}.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return str(path)
