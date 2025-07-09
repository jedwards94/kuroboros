from datetime import timedelta


class UnrecoverableException(Exception):
    pass

class RetriableException(Exception):
    backoff: timedelta
    
    def __init__(self, backoff: timedelta, *args: object) -> None:
        self.backoff = backoff
        super().__init__(*args)
    
    pass

class ValidationWebhookError(Exception):
    """Exception raised for validation errors in CRD objects."""
    
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.reason = message
    
    def __str__(self) -> str:
        return f"ValidationWebhookError: {self.reason}"