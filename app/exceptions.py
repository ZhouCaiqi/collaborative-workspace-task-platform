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


class TaskPermissionDeniedError(AppException):
    def __init__(self):
        super().__init__(
            status_code=403,
            code="TASK_PERMISSION_DENIED",
            message="Insufficient task permissions",
        )


class TaskStatusTransitionConflictError(AppException):
    def __init__(self):
        super().__init__(
            status_code=409,
            code="TASK_STATUS_TRANSITION_CONFLICT",
            message="Task status transition is not allowed",
        )


class TaskAssignmentConflictError(AppException):
    def __init__(self):
        super().__init__(
            status_code=409,
            code="TASK_ASSIGNMENT_CONFLICT",
            message="Task assignment change is not allowed",
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


class WorkspaceNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            status_code=404,
            code="WORKSPACE_NOT_FOUND",
            message="Workspace not found",
        )


class WorkspacePermissionDeniedError(AppException):
    def __init__(self):
        super().__init__(
            status_code=403,
            code="WORKSPACE_PERMISSION_DENIED",
            message="Insufficient workspace permissions",
        )


class UserNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            status_code=404,
            code="USER_NOT_FOUND",
            message="User not found",
        )


class WorkspaceMemberNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            status_code=404,
            code="WORKSPACE_MEMBER_NOT_FOUND",
            message="Workspace member not found",
        )


class WorkspaceMemberAlreadyExistsError(AppException):
    def __init__(self):
        super().__init__(
            status_code=409,
            code="WORKSPACE_MEMBER_ALREADY_EXISTS",
            message="User is already a workspace member",
        )


class OwnerMembershipConflictError(AppException):
    def __init__(self):
        super().__init__(
            status_code=409,
            code="OWNER_MEMBERSHIP_CONFLICT",
            message="Workspace owner membership cannot be changed or removed",
        )


class RateLimitExceededError(AppException):
    def __init__(self, limit: int, retry_after: int):
        super().__init__(
            status_code=429,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many requests",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )


class RateLimitUnavailableError(AppException):
    def __init__(self):
        super().__init__(
            status_code=503,
            code="RATE_LIMIT_UNAVAILABLE",
            message="Rate limit service unavailable",
        )
