"""Exercise rejected-image state and Instagrapi's Pillow compatibility boundary."""

import io
import struct
import zlib
from unittest.mock import patch

import pytest
from instagrapi.image_util import prepare_image, prepare_story_image_fit
from PIL import Image


def test_rejected_gif_frame_preserves_safe_canvas_dimensions() -> None:
    # GHSA-65qm-jvmh-6pqg: rejection must not leave attacker-controlled size.
    data = bytes.fromhex(
        "47494638396101000100800000000000ffffff"
        "2c0000000001000100000202440100"
        "2c00000000409c409c00"
        "2c00000000010001000002024401003b"
    )
    with Image.open(io.BytesIO(data)) as image:
        safe_size = image.size
        with pytest.raises(Image.DecompressionBombError):
            image.seek(1)
        assert image.size == safe_size


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


@pytest.mark.parametrize("disposal", [0, 1, 2])
def test_apng_rejects_oversized_canvas_before_allocating(disposal: int) -> None:
    # Scale the limit down: prove ordering without allocating a giant canvas.
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 201, 201, 8, 6, 0, 0, 0))
        + _png_chunk(b"acTL", struct.pack(">II", 2, 0))
        + _png_chunk(
            b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 10, disposal, 0)
        )
        + _png_chunk(b"IDAT", zlib.compress(b"\0" * 5))
        + _png_chunk(b"IEND", b"")
    )
    with patch.object(Image, "MAX_IMAGE_PIXELS", 10000):
        with patch.object(Image.core, "fill", wraps=Image.core.fill) as allocate:
            with pytest.raises(Image.DecompressionBombError):
                Image.open(io.BytesIO(data))
            assert not any(
                call.args[1][0] * call.args[1][1] > 20000
                for call in allocate.call_args_list
            )


@pytest.mark.parametrize(
    "mode,format", [("RGB", "JPEG"), ("RGBA", "PNG"), ("P", "PNG"), ("L", "PNG")]
)
@pytest.mark.parametrize("prepare", [prepare_image, prepare_story_image_fit])
def test_instagram_image_preparation_keeps_supported_modes(
    tmp_path, mode, format, prepare
) -> None:
    source = tmp_path / (mode + "." + format.lower())
    with Image.new(mode, (640, 480)) as image:
        image.save(source, format=format)
    output, size = prepare(str(source))
    expected_size = (640, 480) if prepare is prepare_image else (1080, 1920)
    assert size == expected_size
    with Image.open(io.BytesIO(output)) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == expected_size
