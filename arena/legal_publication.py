"""Exact, explicitly approved legal resources. No policy wording is generated here."""
import hashlib
import json
from pathlib import Path
import re

FILES = {"terms": "TERMS.md", "privacy": "PRIVACY.md"}
PLACEHOLDER = "This page is being prepared."
ERROR = "Legal publication gate failed"


def directory():
    packaged = Path(__file__).with_name("legal")
    # A packaged pending manifest must not fall back to arbitrary source files.
    return packaged if packaged.exists() else Path(__file__).resolve().parent.parent / "docs/legal"


def validate_text(raw):
    """Accept a conservative subset of the existing renderer, without rewriting it."""
    text = raw.decode("utf-8")
    if not text.strip() or text.startswith("\ufeff") or "\r" in text or not text.endswith("\n"):
        raise ValueError(ERROR)
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError(ERROR)
    if re.search(r"\[\[|\]\]|\{\{|\}\}|\b(?:TODO|TBD)\b|Draft for legal review|\*\*REVIEW", text):
        raise ValueError(ERROR)
    if re.search(r"(?m)^\s*(?:#{4,}\s|```|~~~| {4}\S)|<[/!A-Za-z]|!\[|`", text):
        raise ValueError(ERROR)
    # Links are the only URL syntax supported by the legal renderer.
    def link(match):
        url = match[2]
        if not re.fullmatch(r"(?:https://[^\s]+|mailto:[^\s]+)", url, re.I):
            raise ValueError(ERROR)
        return match[1]
    plain = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", link, text)
    if re.search(r"\[[^\]\n]*\](?:\[|\()", plain):
        raise ValueError(ERROR)
    plain = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", plain)
    plain = re.sub(r"(?m)^\s*[-*+]\s+", "", plain)
    if "*" in plain or re.search(r"_\S.*?\S_|_\S_", plain):
        raise ValueError(ERROR)
    return text


def approved_documents(folder=None):
    """Require both exact files and an explicit owner-approval record; fail as a pair."""
    root = Path(folder) if folder is not None else directory()
    try:
        manifest_path = root / "APPROVED.json"
        if manifest_path.is_symlink():
            raise ValueError(ERROR)
        manifest = json.loads(manifest_path.read_bytes())
        if (type(manifest) is not dict or type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1
                or manifest.get("status") != "approved"
                or not isinstance(manifest.get("approval_reference"), str)
                or not manifest["approval_reference"].strip()
                or type(manifest.get("sha256")) is not dict
                or set(manifest["sha256"]) != set(FILES.values())):
            raise ValueError(ERROR)
        documents = {}
        for page, name in FILES.items():
            path = root / name
            expected = manifest["sha256"][name]
            if path.is_symlink() or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError(ERROR)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(ERROR)
            documents[page] = validate_text(raw)
        return documents
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise ValueError(ERROR) from None
