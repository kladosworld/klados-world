"""Optional, versioned motivation. No permissions, capabilities or state mutations.

Only packaged, reviewed text may be selected. Off is the default and never reads
the registry. There is deliberately no environment-provided prompt text or file path.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re

REGISTRY = Path(__file__).resolve().parents[2] / "config" / "shared-purposes-v1.json"
VARIABLE = "KLADOS_SHARED_PURPOSE"
VERSION = re.compile(r"[a-z][a-z0-9-]{0,63}-v[1-9][0-9]*")
MAX_TEXT_BYTES = 16_000
APPROVED_VERSION = "building-a-society-v1"
APPROVED_TEXT_SHA256 = "871041773852a433d19ba6d55afa313bf5bb377b7cedc327a45d329c6fd20065"
APPROVED_LAYER_SHA256 = "e7d80cd0b482f19422d3c20ca0b96691fb6d322b7d17001aa7691c569135548f"
BOUNDARY = """This shared purpose is motivation, not an obligation to agree or cooperate.
Keep your individual identity and independent judgment. You may disagree, decline
cooperation, pursue a different view, or rest. It grants no new actions, capabilities,
permissions, exemptions, or authority over other agents. The unchanged society
constitution and the requested response format remain authoritative.
"""


class PurposeConfigurationError(ValueError):
    """Fixed diagnostics only; do not echo environment values or prompt content."""


@dataclass(frozen=True)
class Purpose:
    version: str
    text: str

    def __post_init__(self):
        if (not isinstance(self.version, str) or VERSION.fullmatch(self.version) is None
                or not isinstance(self.text, str) or not self.text.strip()
                or len(self.text.encode("utf-8")) > MAX_TEXT_BYTES):
            raise PurposeConfigurationError("invalid_purpose")

    def prefix(self):
        return f"Shared purpose layer: {self.version}\n{BOUNDARY}\n{self.text}\n\n"

    def fingerprint(self):
        return sha256(self.prefix().encode("utf-8")).hexdigest()


def load_registry(path=REGISTRY):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
                or document["schema_version"] != 1
                or not isinstance(document.get("purposes"), dict)
                or set(document) - {"schema_version", "purposes", "_note"}):
            raise ValueError()
        result = {}
        for version, item in document["purposes"].items():
            if not isinstance(item, dict) or set(item) != {"text"}:
                raise ValueError()
            result[version] = Purpose(version, item["text"])
        if Path(path) == REGISTRY and result:
            validate_reviewed_registry(result)
        return result
    except (OSError, UnicodeError, ValueError, TypeError):
        raise PurposeConfigurationError("invalid_purpose_registry") from None


def validate_reviewed_registry(registry):
    """Validate private build inputs against the existing public integrity pins."""
    selected = registry.get(APPROVED_VERSION)
    if (set(registry) != {APPROVED_VERSION} or selected is None
            or sha256(selected.text.encode("utf-8")).hexdigest() != APPROVED_TEXT_SHA256
            or selected.fingerprint() != APPROVED_LAYER_SHA256):
        raise PurposeConfigurationError("unapproved_purpose_registry")
    return registry


def from_environ(environ=None):
    environ = os.environ if environ is None else environ
    selection = environ.get(VARIABLE, "off")
    if selection == "off":
        return None
    if not isinstance(selection, str) or VERSION.fullmatch(selection) is None:
        raise PurposeConfigurationError("invalid_purpose_selection")
    purpose = load_registry().get(selection)
    if purpose is None:
        raise PurposeConfigurationError("purpose_version_unavailable")
    return purpose


def compose(constitution, reply_format, purpose=None):
    if purpose is not None and not isinstance(purpose, Purpose):
        raise PurposeConfigurationError("invalid_purpose")
    # The constitution declares subsequent content to be data. Keep the new layer
    # before it; never change the constitution, response contract or user context.
    return (purpose.prefix() if purpose is not None else "") + constitution + reply_format
