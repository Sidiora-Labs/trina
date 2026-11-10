"""The security layer: secret loading, redaction, residual-secret audit, artifact scan.

Requirement 3 (security.md). One pattern catalogue serves the redactor, the audit,
the artifact scanner and, through ``credential_patterns``, the hygiene gate.
"""

from trina.security.audit import (
    AuditFinding,
    AuditRecord,
    AuditReport,
    ResidualSecretError,
    audit_content,
    audit_dataset,
)
from trina.security.loader import (
    CREDENTIAL_FILES,
    SECRET_PLACEHOLDER,
    SECRETS_DIR_ENV,
    CredentialFileError,
    MissingSecretError,
    Secret,
    SecretLogFilter,
    SecretsError,
    SecretsLoader,
    install_excepthook_guard,
    install_guards,
    install_log_guard,
    resolve_secrets_dir,
)
from trina.security.patterns import KINDS, credential_patterns, placeholder
from trina.security.redaction import (
    RedactedRecord,
    RedactedText,
    RedactionError,
    empty_counts,
    redact_record,
    redact_text,
    sum_counts,
)
from trina.security.scanner import ArtifactFinding, ScanResult, scan_artifacts, scan_file

__all__ = [
    "CREDENTIAL_FILES",
    "KINDS",
    "SECRETS_DIR_ENV",
    "SECRET_PLACEHOLDER",
    "ArtifactFinding",
    "AuditFinding",
    "AuditRecord",
    "AuditReport",
    "CredentialFileError",
    "MissingSecretError",
    "RedactedRecord",
    "RedactedText",
    "RedactionError",
    "ResidualSecretError",
    "ScanResult",
    "Secret",
    "SecretLogFilter",
    "SecretsError",
    "SecretsLoader",
    "audit_content",
    "audit_dataset",
    "credential_patterns",
    "empty_counts",
    "install_excepthook_guard",
    "install_guards",
    "install_log_guard",
    "placeholder",
    "redact_record",
    "redact_text",
    "resolve_secrets_dir",
    "scan_artifacts",
    "scan_file",
    "sum_counts",
]
