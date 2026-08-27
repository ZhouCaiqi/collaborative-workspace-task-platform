class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


class TaskNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            status_code=404,
            code="TASK_NOT_FOUND",
            message="Task not found"
        )


class UsernameAlreadyExistsError(AppException):
    def __init__(self):
        super().__init__(
            status_code=409,
            code="USERNAME_ALREADY_EXISTS",
            message="Username already registered"
        )


class InvalidCredentialsError(AppException):
    def __init__(self):
        super().__init__(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"}
        )


class InvalidTokenError(AppException):
    def __init__(self):
        super().__init__(
            status_code=401,
            code="INVALID_TOKEN",
            message="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )