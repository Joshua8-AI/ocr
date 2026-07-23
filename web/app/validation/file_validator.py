import magic

ALLOWED_TYPES: dict[str, list[str]] = {
    "application/pdf": [".pdf"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png": [".png"],
    "image/tiff": [".tif", ".tiff"],
    "image/bmp": [".bmp"],
    "image/gif": [".gif"],
    "image/webp": [".webp"],
    "image/vnd.djvu": [".djvu", ".djv"],
    "image/x-portable-anymap": [".pnm"],
    "image/x-portable-bitmap": [".pbm"],
    "image/x-portable-graymap": [".pgm"],
    "image/x-portable-pixmap": [".ppm"],
}

# Flatten to a set of allowed extensions
ALLOWED_EXTENSIONS: set[str] = set()
for exts in ALLOWED_TYPES.values():
    ALLOWED_EXTENSIONS.update(exts)

# Magic byte signatures for quick validation
MAGIC_SIGNATURES: dict[bytes, str] = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
    b"BM": "image/bmp",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"AT&T": "image/vnd.djvu",
}


class FileValidationError(Exception):
    pass


def validate_file_extension(filename: str) -> str:
    """Validate file extension and return normalized extension."""
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        raise FileValidationError(f"File '{filename}' has no extension")
    ext = filename[dot_idx:].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )
    return ext


def validate_magic_bytes(content: bytes, filename: str) -> str:
    """Validate file content via magic bytes. Returns detected MIME type."""
    detected = magic.from_buffer(content[:2048], mime=True)

    # Normalize some libmagic quirks
    mime_aliases = {
        "image/x-ms-bmp": "image/bmp",
        "image/x-bmp": "image/bmp",
        "image/x-djvu": "image/vnd.djvu",
    }
    detected = mime_aliases.get(detected, detected)

    if detected not in ALLOWED_TYPES:
        raise FileValidationError(
            f"File '{filename}' content detected as '{detected}', which is not allowed"
        )

    # Verify extension matches detected type
    ext = filename[filename.rfind(".") :].lower()
    allowed_exts = ALLOWED_TYPES.get(detected, [])
    # PNM family types share extensions loosely, so be lenient
    pnm_exts = {".pnm", ".pbm", ".pgm", ".ppm"}
    if ext not in allowed_exts and ext not in pnm_exts:
        raise FileValidationError(
            f"File '{filename}' extension '{ext}' does not match detected type '{detected}'"
        )

    return detected
