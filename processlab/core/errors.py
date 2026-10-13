"""Shared exception types."""


class BusyError(ValueError):
    """A bounded worker/slot refused new work. Surfaced to HTTP as 409, not 500."""
