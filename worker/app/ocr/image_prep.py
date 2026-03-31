import base64
import io

from PIL import Image

TARGET_MAX_DIM = 1540


def prepare_image(image: Image.Image) -> str:
    """Resize image so longest dimension is 1540px and return base64-encoded PNG."""
    # Convert to RGB if needed (handles RGBA, palette, CMYK, etc.)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    # Resize if larger than target
    max_dim = max(image.size)
    if max_dim > TARGET_MAX_DIM:
        scale = TARGET_MAX_DIM / max_dim
        new_size = (int(image.width * scale), int(image.height * scale))
        image = image.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def load_image(filepath: str) -> Image.Image:
    """Load an image file with Pillow."""
    return Image.open(filepath)
