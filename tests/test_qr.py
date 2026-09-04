from io import BytesIO

import qrcode
import pytest

from retium.qr import decode_join_code_image, join_code_svg
from retium.team import TeamCodeError, encode_join_code


def test_join_code_renders_as_local_svg_qr():
    code = encode_join_code(bytes.fromhex("00112233445566778899aabbccddeeff"))

    svg = join_code_svg(code)

    assert svg.startswith(b"<?xml")
    assert b"<svg" in svg
    assert b"<path" in svg
    assert len(svg) > 1_000


def test_generated_join_qr_decodes_back_to_the_same_code():
    code = encode_join_code(bytes.fromhex("00112233445566778899aabbccddeeff"))
    image = qrcode.make(code)
    output = BytesIO()
    image.save(output, format="PNG")

    assert decode_join_code_image(output.getvalue()) == code


def test_non_image_is_rejected():
    with pytest.raises(TeamCodeError, match="not readable"):
        decode_join_code_image(b"not an image")
