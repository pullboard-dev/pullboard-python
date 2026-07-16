"""Error types for the Pullboard client."""


class PullboardError(Exception):
    """Raised when the Pullboard API returns a non-success response.

    Retains the HTTP status and the stable machine-readable error code so
    callers can make programmatic recovery decisions (e.g. WORK_TAKEN -> retry
    a different item).
    """

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code
