"""Public, bounded chat errors. Never attach upstream response bodies."""


class ChatError(ValueError):
    def __init__(
        self,
        message="请求无效。",
        status=400,
        code="CHAT_INVALID_REQUEST",
        *,
        uncertain=False,
        run_id=None,
        details=None,
        retry_after=None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.uncertain = uncertain
        self.run_id = run_id
        self.details = details or {}
        self.retry_after = retry_after
