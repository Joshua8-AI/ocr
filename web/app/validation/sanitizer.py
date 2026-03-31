import os
import re
import unicodedata


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and other attacks."""
    # Strip any directory components
    filename = os.path.basename(filename)

    # Normalize unicode
    filename = unicodedata.normalize("NFKD", filename)

    # Remove null bytes and control characters
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)

    # Replace path separators and other dangerous chars
    filename = re.sub(r'[/\\:*?"<>|]', "_", filename)

    # Remove leading dots (hidden files)
    filename = filename.lstrip(".")

    # Limit length (preserve extension)
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[: 200 - len(ext)] + ext

    # Fallback if empty
    if not filename:
        filename = "unnamed_file"

    return filename


def validate_email(email: str) -> str:
    """Basic email format validation."""
    email = email.strip().lower()
    if len(email) > 254:
        raise ValueError("Email address too long")
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
        raise ValueError("Invalid email address format")
    return email
