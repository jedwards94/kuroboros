from datetime import timedelta


class UnrecoverableException(Exception):
    pass

class RetriableException(Exception):
    backoff: timedelta
    
    def __init__(self, backoff: timedelta, *args: object) -> None:
        self.backoff = backoff
        super().__init__(*args)
    
    pass