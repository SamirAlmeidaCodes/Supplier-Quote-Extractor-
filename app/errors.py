"""Errors that are safe to show in the interface.

Messages must already be free of API keys and request headers. Raise these
with ``from None`` when wrapping a library exception that might carry a
request object.
"""


class AppError(Exception):
    """A failure the user can act on."""
