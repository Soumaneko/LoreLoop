class ClientValidationError(ValueError):
    """A deliberate client-input or business-validation error.

    The message is safe to surface to the client verbatim. Every other
    exception type is logged server-side and replaced with a generic
    ``internal_error`` before it reaches the HTTP/SSE boundary.
    """
