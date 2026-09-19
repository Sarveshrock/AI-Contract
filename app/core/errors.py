"""Exception hierarchy. Every error carries a message safe to show to end users."""
from __future__ import annotations

from typing import Any


class ContractLensError(Exception):
    """Base class. ``user_message`` is safe to display; ``str(exc)`` may hold detail."""

    code = "error"

    def __init__(self, message: str, *, user_message: str | None = None, **context: Any) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.context = context


class ConfigurationError(ContractLensError):
    code = "configuration"


class AuthenticationError(ContractLensError):
    code = "authentication"


class AuthorizationError(ContractLensError):
    code = "authorization"


class ValidationFailure(ContractLensError):
    code = "validation"


class FileValidationError(ValidationFailure):
    code = "file_validation"


class DuplicateDocumentError(ContractLensError):
    code = "duplicate_document"

    def __init__(self, message: str, *, existing_document_id: str, existing_contract_id: str | None = None) -> None:
        super().__init__(message, existing_document_id=existing_document_id)
        self.existing_document_id = existing_document_id
        self.existing_contract_id = existing_contract_id


class DocumentProcessingError(ContractLensError):
    code = "document_processing"


class OCRUnavailableError(DocumentProcessingError):
    code = "ocr_unavailable"


class DatabaseError(ContractLensError):
    code = "database"


class StorageError(ContractLensError):
    code = "storage"


class RetrievalError(ContractLensError):
    code = "retrieval"


class IndexMismatchError(RetrievalError):
    code = "index_mismatch"


class AIUnavailableError(ContractLensError):
    """No usable LLM configured. Never replaced by fabricated output."""

    code = "ai_unavailable"


class AIResponseError(ContractLensError):
    code = "ai_response"


class TransientError(ContractLensError):
    """Retryable failure (network, rate limit, 5xx)."""

    code = "transient"


class ToolAuthorizationError(AuthorizationError):
    code = "tool_authorization"


class ConfirmationRequired(ContractLensError):
    code = "confirmation_required"


class WorkflowError(ContractLensError):
    code = "workflow"
