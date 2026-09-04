from __future__ import annotations

from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage

from .team import TeamCodeError, decode_join_code

MAX_QR_IMAGE_BYTES = 2_000_000
MAX_QR_IMAGE_PIXELS = 16_000_000


def join_code_svg(join_code: str) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(join_code)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)
    return output.getvalue()


def decode_join_code_image(raw: bytes) -> str:
    if not raw or len(raw) > MAX_QR_IMAGE_BYTES:
        raise TeamCodeError("QR image must be smaller than 2 MB")
    try:
        import zxingcpp
        from PIL import Image, UnidentifiedImageError

        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > MAX_QR_IMAGE_PIXELS:
                raise TeamCodeError("QR image dimensions are too large")
            image = source.convert("RGB")
            image.thumbnail((1280, 1280))
    except ImportError as exc:
        raise TeamCodeError("QR image decoding is unavailable on this node") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise TeamCodeError("QR image is not readable") from exc

    barcode = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.QRCode)
    if barcode is None:
        raise TeamCodeError("No QR code found")
    join_code = barcode.text.strip()
    decode_join_code(join_code)
    return join_code
