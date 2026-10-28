"""Operational errors are separate from provider outcome receipts."""


class MediaOperationError(RuntimeError):
    code = "MEDIA_OPERATION_ERROR"


class UnsafePathError(MediaOperationError):
    code = "UNSAFE_PATH"


class ArtifactMissingError(MediaOperationError):
    code = "ARTIFACT_MISSING"


class ArtifactCorruptError(MediaOperationError):
    code = "ARTIFACT_CORRUPT"


class ArtifactCollisionError(MediaOperationError):
    code = "ARTIFACT_COLLISION"


class RegistryIOError(MediaOperationError):
    code = "REGISTRY_IO_ERROR"


class JobCollisionError(MediaOperationError):
    code = "JOB_COLLISION"


class ManifestValidationError(MediaOperationError):
    code = "MANIFEST_INVALID"


class RetryUnsafeError(MediaOperationError):
    code = "RETRY_REQUIRES_RECONCILIATION"


class RunnerBusyError(MediaOperationError):
    code = "LOCAL_WORKER_BUSY"
