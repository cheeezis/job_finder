"""Explicit errors that cross the review's HTTP boundary."""


class UserInputError(ValueError):
    """A refused user input with a safe, user-facing explanation."""


class NotFoundError(KeyError):
    """A requested job or document does not exist."""


class DatabaseUnavailableError(RuntimeError):
    """Runtime authentication or connection failed; diagnostics are redacted."""


class ManualImportError(RuntimeError):
    """The remote listing could not be downloaded."""


class DocumentAccessError(ValueError):
    """Stored metadata or a path must not be used for a download."""


class DocumentIntegrityError(ValueError):
    """Stored bytes no longer match the application's document checksum."""
