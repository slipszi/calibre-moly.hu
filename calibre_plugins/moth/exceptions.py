"""Exceptions thrown by the plugin."""


class Aborted(RuntimeError):
    """Exception indicating that a request was aborted."""


class JsonError(RuntimeError):
    """Exception indicating invalid JSON values."""
