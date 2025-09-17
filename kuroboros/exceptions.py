class RetriableException(Exception):
    """
    An retriable exception that will cause the reonciliation loop
    to try agains in a defined backoof
    """
    backoff: float

    def __init__(self, backoff: float, *args: object) -> None:
        self.backoff = backoff
        super().__init__(*args)


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
    """
    Multiples types of a resource is defined under the same controller
    """

    def __init__(self, cls, ctrl, vrsn) -> None:
        super().__init__(
            f"Multiple {cls.__class__.__name__} classes found in {ctrl} {vrsn}. "
            f"Only one {cls.__class__.__name__} class is allowed per version."
        )
        self.reason = f"Multiple {cls} classes found in {ctrl} {vrsn}."

    def __str__(self) -> str:
        return f"MultipleDefinitionsException: {self.reason}"
