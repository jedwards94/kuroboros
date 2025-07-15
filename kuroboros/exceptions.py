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


class MutationWebhookError(Exception):
    """Exception raised for mutation errors in CRD objects."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.reason = message

    def __str__(self) -> str:
        return f"MutationWebhookError: {self.reason}"


class MultipleDefinitionsException(Exception):

    def __init__(self, cls, ctrl, vrsn) -> None:
        super().__init__(
            f"Multiple {cls.__class__.__name__} classes found in {ctrl} {vrsn}. "
            "Only one reconciler class is allowed per version."
        )
        self.reason = f"Multiple {cls} classes found in {ctrl} {vrsn}."

    def __str__(self) -> str:
        return f"MutationWebhookError: {self.reason}"
