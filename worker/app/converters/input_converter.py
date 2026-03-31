import os
import subprocess
import tempfile

from PIL import Image, ImageSequence


def convert_to_images(filepath: str) -> list[Image.Image]:
    """Convert any supported file type to a list of PIL Images (one per page/frame).

    Returns a list of PIL Image objects ready for OCR.
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".pgm", ".ppm", ".pbm", ".pnm"):
        return [Image.open(filepath)]

    if ext in (".tif", ".tiff"):
        return _convert_tiff(filepath)

    if ext == ".gif":
        return _convert_gif(filepath)

    if ext in (".djvu", ".djv"):
        return _convert_djvu(filepath)

    raise ValueError(f"Unsupported file type: {ext}")


def _convert_tiff(filepath: str) -> list[Image.Image]:
    """Extract all pages from a multi-page TIFF."""
    img = Image.open(filepath)
    pages = []
    for frame in ImageSequence.Iterator(img):
        pages.append(frame.copy())
    return pages


def _convert_gif(filepath: str) -> list[Image.Image]:
    """Extract all frames from a GIF."""
    img = Image.open(filepath)
    frames = []
    for frame in ImageSequence.Iterator(img):
        frames.append(frame.copy().convert("RGB"))
    return frames


def _convert_djvu(filepath: str) -> list[Image.Image]:
    """Convert DJVU pages to images using ddjvu."""
    images = []
    # First, get page count
    result = subprocess.run(
        ["djvused", filepath, "-e", "n"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    page_count = int(result.stdout.strip()) if result.returncode == 0 else 1

    for page_num in range(1, page_count + 1):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                [
                    "ddjvu",
                    "-format=png",
                    "-quality=85",
                    f"-page={page_num}",
                    filepath,
                    tmp_path,
                ],
                capture_output=True,
                timeout=60,
                check=True,
            )
            images.append(Image.open(tmp_path).copy())
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return images
