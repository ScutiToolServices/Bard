"""Stable, coded failures. Messages are safe to show a user: no paths, no secrets."""


class BardError(Exception):
    code = "bard_error"


class TextRejected(BardError):
    code = "text_rejected"


class QuotaExceeded(BardError):
    code = "quota_exceeded"


class VoiceUnavailable(BardError):
    code = "voice_unavailable"


class EngineFailed(BardError):
    code = "engine_failed"


class IdentityRequired(BardError):
    code = "identity_required"
