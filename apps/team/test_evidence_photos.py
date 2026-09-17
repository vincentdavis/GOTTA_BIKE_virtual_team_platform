"""Photos riders upload as verification evidence are rebuilt before they are stored.

Two reasons, both from riders on iPhones. An iPhone can send HEIC, which Chrome, Firefox and
Edge cannot draw, so a reviewer on any of them saw a broken image -- HEIC is now stored as JPEG.
And a photo of a scale is usually taken at home, and the file says where: every photo is now
stored with its capture time and camera and nothing else.

A photo is decoded inside the web request, so the limits are tested as hard as the output:
a file of a few hundred bytes can ask for gigabytes.

The HEIC files are committed, synthetic fixtures (see test_data/evidence/README.md): the app's
decoder cannot write HEIC. JPEG, PNG and GIF are made here with Pillow.
"""

import io
import shutil
import struct
import subprocess  # noqa: S404 -- runs node on a page this test rendered
import time
from pathlib import Path

import pytest
from constance.test import override_config
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.html import escape
from PIL import ExifTags, Image, ImageChops, ImageCms, ImageSequence, JpegImagePlugin, PngImagePlugin

from apps.team import evidence_media
from apps.team.evidence_media import PhotoRejectedError, prepare_photo
from apps.team.forms import RaceReadyRecordForm
from apps.team.models import RaceReadyRecord

TEST_DATA = Path(__file__).parent / "test_data"
FIXTURES = TEST_DATA / "evidence"
RED = (255, 0, 0)
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

# What a rebuilt file must never contain, whatever it started as. The GPS values and the
# description are invented (see the fixtures README), but they stand in for a rider's home.
LEAKS = (b"Example Road", b"FIXTURE-SERIAL", b"exif:GPS", b"GPSLatitude", b"FIXTURE-DISPLAY-SERIAL")


def _heic(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _exif(*, orientation: int | None = None, gps: bool = True) -> bytes:
    """Build EXIF like a phone writes: camera, capture time, and extras that must not survive.

    Returns:
        Serialised EXIF.

    """
    tags = Image.Exif()
    tags[ExifTags.Base.Make] = "Apple"
    tags[ExifTags.Base.Model] = "iPhone Fixture"
    tags[ExifTags.Base.Software] = "26.6.1"
    tags[ExifTags.Base.ImageDescription] = "Kitchen scale at 1 Example Road"
    tags[ExifTags.Base.DateTime] = "2026:09:01 07:31:00"
    if orientation:
        tags[ExifTags.Base.Orientation] = orientation
    sub = tags.get_ifd(ExifTags.IFD.Exif)
    sub[ExifTags.Base.DateTimeOriginal] = "2026:09:01 07:30:00"
    sub[ExifTags.Base.OffsetTimeOriginal] = "+01:00"
    sub[ExifTags.Base.BodySerialNumber] = "FIXTURE-SERIAL-0001"
    if gps:
        location = tags.get_ifd(ExifTags.IFD.GPSInfo)
        location[ExifTags.GPS.GPSLatitudeRef] = "N"
        location[ExifTags.GPS.GPSLatitude] = (51.0, 28.0, 38.0)
    return tags.tobytes()


def _with_display_serial(icc: bytes, marker: bytes = b"FIXTURE-DISPLAY-SERIAL") -> bytes:
    """Add an Apple ``mmod`` tag, where a Mac display profile keeps its serial number.

    Returns:
        The profile with one more tag.

    """
    count = struct.unpack(">I", icc[128:132])[0]
    table_end = 132 + 12 * count
    entries = []
    for i in range(count):
        signature, offset, size = struct.unpack(">4sII", icc[132 + 12 * i : 144 + 12 * i])
        entries.append((signature, offset + 12, size))  # the table grows by one entry
    body = icc[table_end:]
    body += bytes(-len(body) % 4)
    data = b"mmod" + bytes(4) + marker.ljust(32, b"\0")
    entries.append((b"mmod", table_end + 12 + len(body), len(data)))
    table = struct.pack(">I", count + 1) + b"".join(struct.pack(">4sII", *entry) for entry in entries)
    profile = icc[:128] + table + body + data
    return struct.pack(">I", len(profile)) + profile[4:]


def _icc_tags(icc: bytes) -> list[bytes]:
    count = struct.unpack(">I", icc[128:132])[0]
    return [icc[132 + 12 * i : 136 + 12 * i] for i in range(count)]


def _picture(width: int = 64, height: int = 48, mode: str = "RGB") -> Image.Image:
    """Draw blue with a red top-left block, so a test can tell which way up a picture is.

    Returns:
        The picture.

    """
    image = Image.new("RGB", (width, height), (30, 90, 200))
    image.paste(RED, (0, 0, width // 4, height // 4))
    return image if mode == "RGB" else image.convert(mode)


def _jpeg(**save_params) -> bytes:
    out = io.BytesIO()
    _picture().save(out, "JPEG", **{"quality": 80, **save_params})
    return out.getvalue()


def _png(image: Image.Image | None = None, **save_params) -> bytes:
    out = io.BytesIO()
    (image or _picture()).save(out, "PNG", **save_params)
    return out.getvalue()


def _gif(frames: list[Image.Image], **save_params) -> bytes:
    out = io.BytesIO()
    if len(frames) > 1:
        save_params = {"save_all": True, "append_images": frames[1:], **save_params}
    frames[0].save(out, "GIF", **save_params)
    return out.getvalue()


def _iphone_hdr_jpeg(**save_params) -> bytes:
    """Build a JPEG carrying a second picture (MPO), as an iPhone writes one for an HDR photo.

    The location is only in the SECOND picture, which no viewer shows -- so only a rebuild
    that drops that picture removes it.

    Returns:
        The file.

    """
    gain_map = Image.new("L", (32, 24), 128)
    gain_map.encoderinfo = {"exif": _exif()}
    out = io.BytesIO()
    _picture().save(out, "MPO", save_all=True, append_images=[gain_map], exif=_exif(gps=False), **save_params)
    return out.getvalue()


def _raw_gif(frames: list[dict]) -> bytes:
    """Write a 1x1-screen GIF by hand, one frame per dict.

    Each dict has ``x`` and ``y``, and optionally ``delay`` (hundredths of a second), ``colour``
    (the frame's own one-colour palette; otherwise the screen's black), ``size`` (the extent the
    frame declares; its pixel data is always one pixel) and ``disposal``.

    By hand because Pillow's writer would not produce these. A frame placed past the screen's
    edge makes Pillow grow the canvas when it is decoded -- one at (9000, 9000) turns a 1x1
    picture into an 81-megapixel one.

    Returns:
        The file.

    """
    screen = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
    loop = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    body = b""
    for frame in frames:
        packed = frame.get("disposal", 0) << 2
        body += b"\x21\xf9\x04" + struct.pack("<BHB", packed, frame.get("delay", 10), 0) + b"\x00"
        colour = frame.get("colour")
        width, height = frame.get("size", (1, 1))
        body += b"\x2c" + struct.pack("<HHHHB", frame["x"], frame["y"], width, height, 0x80 if colour else 0)
        if colour:
            body += bytes(colour) + b"\x00\x00\x00"
        body += b"\x02\x02\x44\x01\x00"
    return screen + loop + body + b";"


def _at(x: int, y: int, **extra) -> dict:
    return {"x": x, "y": y, **extra}


def _upload(name: str, data: bytes, content_type: str = "application/octet-stream") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


def _open(upload) -> Image.Image:
    upload.seek(0)
    return Image.open(io.BytesIO(upload.read()))


def _stored_bytes(upload) -> bytes:
    upload.seek(0)
    return upload.read()


def _red_corner(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    corners = {
        "top-left": (0, 0),
        "top-right": (rgb.width - 1, 0),
        "bottom-left": (0, rgb.height - 1),
        "bottom-right": (rgb.width - 1, rgb.height - 1),
    }
    return max(corners, key=lambda c: rgb.getpixel(corners[c])[0] - rgb.getpixel(corners[c])[2])


def _refusal(upload) -> PhotoRejectedError:
    with pytest.raises(PhotoRejectedError) as caught:
        prepare_photo(upload)
    return caught.value


@pytest.fixture
def logged(monkeypatch):
    lines = []
    for level in ("info", "warning"):
        monkeypatch.setattr(
            evidence_media.logfire, level, lambda message, _level=level, **kw: lines.append((_level, message, kw))
        )
    return lines


# --- HEIC is stored as JPEG ------------------------------------------------------------------


def test_a_heic_photo_is_stored_as_a_jpeg():
    stored = prepare_photo(_upload("IMG_0001.HEIC", _heic("tagged.heic"), "image/heic"))
    image = _open(stored)

    assert stored.name == "IMG_0001.jpg"
    assert stored.content_type == "image/jpeg"
    assert image.format == "JPEG"
    assert image.size == (64, 48)
    assert _red_corner(image) == "top-left"


def test_a_heif_spelling_is_converted_too():
    stored = prepare_photo(_upload("scale.heif", _heic("tagged.heic")))

    assert stored.name == "scale.jpg"
    assert _open(stored).format == "JPEG"


def test_heic_content_is_converted_whatever_the_name_says():
    """Some apps save HEIC under .jpg. The content decides, so it still reaches reviewers as JPEG."""
    stored = prepare_photo(_upload("scale.jpg", _heic("tagged.heic"), "image/jpeg"))

    assert _open(stored).format == "JPEG"
    assert stored.name == "scale.jpg"


def test_a_rotated_heic_is_turned_exactly_once():
    """The file stores the rotation and the decoder applies it, so no Orientation is kept.

    Keeping an Orientation of 6 as well would make every browser turn the picture again.
    """
    image = _open(prepare_photo(_upload("IMG_0002.HEIC", _heic("rotated.heic"))))

    assert image.size == (48, 64)
    assert _red_corner(image) == "top-right"  # 90 degrees clockwise from top-left
    assert image.getexif().get(ExifTags.Base.Orientation, 1) == 1


def test_a_transparent_heic_is_laid_on_white():
    """JPEG has no transparency; dropping the alpha channel would leave whatever was under it."""
    image = _open(prepare_photo(_upload("clip.heic", _heic("transparent.heic"))))

    assert image.mode == "RGB"
    red, green, blue = image.getpixel((20, 15))
    assert abs(red - 128) <= 4
    assert abs(green - 228) <= 4
    assert abs(blue - 128) <= 4


def test_a_premultiplied_transparent_heic_is_laid_on_white_too():
    """Premultiplied alpha opens as "RGBa"; a plain RGB conversion left its clear areas black."""
    image = _open(prepare_photo(_upload("clip.heic", _heic("premultiplied.heic"))))

    assert image.mode == "RGB"
    assert all(channel >= 250 for channel in image.getpixel((5, 15)))  # the fully clear half
    red, green, blue = image.getpixel((30, 15))
    assert green > red + 60
    assert green > blue + 60


def test_a_10_bit_grey_heic_keeps_its_greys():
    """The decoder leaves high-bit greyscale at 16 bits; converted naively it all clips to white."""
    image = _open(prepare_photo(_upload("grey.heic", _heic("grey10.heic"))))

    assert image.mode == "L"
    assert abs(image.getpixel((40, 30)) - 76) <= 4
    assert abs(image.getpixel((6, 5)) - 30) <= 6  # inside the dark block, clear of its edge ringing


@pytest.mark.parametrize(
    ("bit_depth", "samples", "expected"),
    [
        (10, [0, 512 << 6, 1023 << 6], [0, 128, 255]),
        (12, [0, 2048 << 4, 4095 << 4], [0, 128, 255]),
        (16, [0, 32768, 65535], [0, 128, 255]),
    ],
)
def test_high_bit_grey_is_scaled_white_to_white(bit_depth, samples, expected):
    """pi-heif shifts samples up, so white is short of 65535; plain division left it at 254."""
    grey = Image.new("I;16", (len(samples), 1))
    grey.putdata(samples)

    converted = evidence_media._high_bit_grey_to_l(grey, bit_depth)

    assert converted.mode == "L"
    assert list(converted.tobytes()) == expected


class _ScrubbedWhenDropped:
    """Stand in for pi-heif's decoded file, overwriting its pixels the moment it is let go.

    pi-heif's buffer is a memoryview with no owner, freed with the object: a picture Pillow
    mapped onto it reads whatever the memory holds next. That is not reliably visible in a
    test, so this makes it certain -- the pixels turn white when the object is dropped.
    """

    def __init__(self, real):
        self.mode, self.size, self.stride, self.info = real.mode, real.size, real.stride, dict(real.info)
        self._pixels = bytearray(real.data)
        self.data = memoryview(self._pixels)

    def __del__(self):
        self._pixels[:] = b"\xff" * len(self._pixels)


@pytest.mark.parametrize("fixture", ["grey10.heic", "transparent.heic", "tagged.heic"])
def test_the_decoded_heic_outlives_every_picture_that_reads_it(monkeypatch, fixture):
    """Dropping the decoder early let a 16-bit grey photo be stored from freed memory."""
    real_open = evidence_media.pi_heif.open_heif
    expected = _stored_bytes(prepare_photo(_upload("x.heic", _heic(fixture))))
    monkeypatch.setattr(evidence_media.pi_heif, "open_heif", lambda fp, **kw: _ScrubbedWhenDropped(real_open(fp, **kw)))

    assert _stored_bytes(prepare_photo(_upload("x.heic", _heic(fixture)))) == expected


def test_the_heic_colour_profile_is_kept():
    """Keep the profile: iPhone photos are Display P3, and without it a browser shows them washed out."""
    import pi_heif

    source_profile = pi_heif.open_heif(FIXTURES / "tagged.heic").to_pillow().info["icc_profile"]

    image = _open(prepare_photo(_upload("IMG_0001.HEIC", _heic("tagged.heic"))))

    assert image.info.get("icc_profile") == source_profile


def test_a_damaged_heic_is_refused_in_plain_words():
    refusal = _refusal(_upload("IMG_0003.HEIC", _heic("truncated.heic")))

    assert refusal.reason == "unreadable"
    assert refusal.message == evidence_media.UNREADABLE_PHOTO_MESSAGE
    assert "IMG_0003" not in refusal.message


def test_the_heic_decoder_is_not_plugged_into_pillow():
    """Registered, it would let the logo and kit-icon uploads take HEIC, which most browsers can't show."""
    assert "HEIF" not in Image.OPEN

    with pytest.raises(forms.ValidationError):
        forms.ImageField().clean(_upload("logo.heic", _heic("tagged.heic"), "image/heic"))


# --- only capture time and camera survive ------------------------------------------------------

SOURCES_WITH_A_LOCATION = {
    "heic": lambda: _upload("IMG_0001.HEIC", _heic("tagged.heic")),
    "jpeg": lambda: _upload("scale.jpg", _jpeg(exif=_exif())),
    "png": lambda: _upload("scale.png", _png(exif=_exif())),
}


@pytest.mark.parametrize("source", sorted(SOURCES_WITH_A_LOCATION))
def test_the_location_is_removed(source):
    stored = prepare_photo(SOURCES_WITH_A_LOCATION[source]())
    exif = _open(stored).getexif()

    assert ExifTags.IFD.GPSInfo not in exif
    assert not exif.get_ifd(ExifTags.IFD.GPSInfo)
    data = _stored_bytes(stored)
    for leak in LEAKS:
        assert leak not in data, leak


@pytest.mark.parametrize("source", sorted(SOURCES_WITH_A_LOCATION))
def test_capture_time_and_camera_are_kept(source):
    """What a reviewer can use to judge the evidence -- and all a rider is told is kept."""
    exif = _open(prepare_photo(SOURCES_WITH_A_LOCATION[source]())).getexif()
    capture = exif.get_ifd(ExifTags.IFD.Exif)

    assert exif[ExifTags.Base.Make] == "Apple"
    assert exif[ExifTags.Base.Model] == "iPhone Fixture"
    assert exif[ExifTags.Base.DateTime] == "2026:09:01 07:31:00"
    assert capture[ExifTags.Base.DateTimeOriginal] == "2026:09:01 07:30:00"
    assert capture[ExifTags.Base.OffsetTimeOriginal] == "+01:00"


@pytest.mark.parametrize("source", sorted(SOURCES_WITH_A_LOCATION))
def test_everything_else_in_the_exif_is_dropped(source):
    """An allowlist: a tag a future phone adds is left out rather than carried through."""
    exif = _open(prepare_photo(SOURCES_WITH_A_LOCATION[source]())).getexif()

    assert ExifTags.Base.Software not in exif
    assert ExifTags.Base.ImageDescription not in exif
    assert ExifTags.Base.BodySerialNumber not in exif.get_ifd(ExifTags.IFD.Exif)


@pytest.mark.parametrize("make", [_jpeg, _png], ids=["jpeg", "png"])
def test_exactly_the_allowlisted_tags_survive(make):
    """Written out by hand, not read from the module: dropping an entry there must fail here."""
    tags = Image.Exif()
    tags[ExifTags.Base.Make] = "Apple"
    tags[ExifTags.Base.Model] = "iPhone Fixture"
    tags[ExifTags.Base.Orientation] = 1
    tags[ExifTags.Base.DateTime] = "2026:09:01 07:31:00"
    tags[ExifTags.Base.Software] = "26.6.1"
    capture = tags.get_ifd(ExifTags.IFD.Exif)
    capture[ExifTags.Base.DateTimeOriginal] = "2026:09:01 07:30:00"
    capture[ExifTags.Base.DateTimeDigitized] = "2026:09:01 07:30:00"
    capture[ExifTags.Base.OffsetTime] = "+01:00"
    capture[ExifTags.Base.OffsetTimeOriginal] = "+01:00"
    capture[ExifTags.Base.OffsetTimeDigitized] = "+01:00"
    capture[ExifTags.Base.SubsecTimeOriginal] = "123"
    capture[ExifTags.Base.SubsecTimeDigitized] = "123"
    capture[ExifTags.Base.ColorSpace] = 1
    capture[ExifTags.Base.BodySerialNumber] = "FIXTURE-SERIAL-0001"

    exif = _open(prepare_photo(_upload("scale", make(exif=tags.tobytes())))).getexif()

    assert {ExifTags.TAGS[tag] for tag in exif} == {"Make", "Model", "Orientation", "DateTime", "ExifOffset"}
    assert {ExifTags.TAGS[tag] for tag in exif.get_ifd(ExifTags.IFD.Exif)} == {
        "DateTimeOriginal",
        "DateTimeDigitized",
        "OffsetTime",
        "OffsetTimeOriginal",
        "OffsetTimeDigitized",
        "SubsecTimeOriginal",
        "SubsecTimeDigitized",
        "ColorSpace",
    }


def test_a_heic_xmp_location_is_dropped():
    """The fixture also names the place in XMP, where no EXIF edit would reach."""
    assert b"exif:GPSLatitude" in _heic("tagged.heic")

    stored = prepare_photo(_upload("IMG_0001.HEIC", _heic("tagged.heic")))

    assert "xmp" not in _open(stored).info
    assert b"GPSLatitude" not in _stored_bytes(stored)


def test_an_iphone_hdr_jpeg_keeps_only_its_main_picture():
    """The second picture is an HDR gain map no reviewer sees -- and it carried the location."""
    source = _iphone_hdr_jpeg(icc_profile=SRGB)
    assert Image.open(io.BytesIO(source)).n_frames == 2
    assert source.count(ExifTags.IFD.GPSInfo.to_bytes(2, "big")) == 1

    stored = prepare_photo(_upload("IMG_0004.jpg", source, "image/jpeg"))
    image = _open(stored)

    assert image.format == "JPEG"
    assert getattr(image, "n_frames", 1) == 1
    assert image.size == (64, 48)
    assert image.info.get("icc_profile") == SRGB
    assert ExifTags.IFD.GPSInfo.to_bytes(2, "big") not in _stored_bytes(stored)
    assert b"Exif" in _stored_bytes(stored)  # the main picture's own EXIF is still there


def test_a_jpeg_keeps_its_orientation_and_is_not_rotated():
    """A sideways JPEG displays upright through its tag, as it did before; the pixels stay put."""
    image = _open(prepare_photo(_upload("scale.jpg", _jpeg(exif=_exif(orientation=6)))))

    assert image.getexif()[ExifTags.Base.Orientation] == 6
    assert image.size == (64, 48)
    assert _red_corner(image) == "top-left"


XMP_ORIENTATION = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/></rdf:RDF></x:xmpmeta>'
)


def _png_with_xmp_orientation() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_itxt("XML:com.adobe.xmp", XMP_ORIENTATION.decode())
    return _png(pnginfo=info)


def _png_with_imagemagick_exif() -> bytes:
    """ImageMagick keeps EXIF in a text chunk as hex, which Pillow parses and browsers ignore.

    Returns:
        The file.

    """
    raw = Image.Exif()
    raw[ExifTags.Base.Orientation] = 6
    payload = raw.tobytes()
    body = b"Exif\x00\x00" + payload
    info = PngImagePlugin.PngInfo()
    info.add_text("Raw profile type exif", f"\nexif\n{len(body):8d}\n{body.hex()}\n", zip=True)
    return _png(pnginfo=info)


@pytest.mark.parametrize(
    ("name", "make"),
    [
        pytest.param("scale.jpg", lambda: _jpeg(xmp=XMP_ORIENTATION), id="jpeg-xmp"),
        pytest.param("scale.png", _png_with_xmp_orientation, id="png-xmp"),
        pytest.param("scale.png", _png_with_imagemagick_exif, id="png-imagemagick-text"),
    ],
)
def test_an_orientation_browsers_ignore_is_not_made_real(name, make):
    """Pillow infers Orientation from these; writing it back as EXIF would turn the stored picture."""
    source = Image.open(io.BytesIO(make()))
    assert source.getexif().get(ExifTags.Base.Orientation) == 6  # what Pillow would have kept

    image = _open(prepare_photo(_upload(name, make())))

    assert image.getexif().get(ExifTags.Base.Orientation, 1) == 1


@pytest.mark.parametrize("subsampling", [0, 1, 2], ids=["4:4:4", "4:2:2", "4:2:0"])
def test_a_jpeg_is_written_back_with_its_own_tables_and_subsampling(subsampling):
    """So the picture is not visibly compressed a second time."""
    data = _jpeg(exif=_exif(), subsampling=subsampling)
    source = Image.open(io.BytesIO(data))

    image = _open(prepare_photo(_upload("scale.jpg", data)))

    assert image.quantization == source.quantization
    assert JpegImagePlugin.get_sampling(image) == JpegImagePlugin.get_sampling(source) == subsampling


@pytest.mark.parametrize(("mode", "colour"), [("L", 128), ("CMYK", (0, 255, 255, 0))])
def test_grey_and_cmyk_jpegs_keep_their_mode(mode, colour):
    """Converting these to RGB would needlessly change the file's colours and size."""
    out = io.BytesIO()
    Image.new(mode, (16, 16), colour).save(out, "JPEG", quality=95)

    image = _open(prepare_photo(_upload("scale.jpg", out.getvalue(), "image/jpeg")))

    assert image.mode == mode
    pixel = image.getpixel((5, 5))
    if mode == "L":
        assert abs(pixel - colour) <= 2
    else:
        assert all(abs(got - want) <= 3 for got, want in zip(pixel, colour, strict=True))


def test_a_jpeg_comment_and_xmp_are_dropped():
    source = _jpeg(comment=b"1 Example Road", xmp=b"<x>exif:GPSLatitude</x>")

    stored = prepare_photo(_upload("scale.jpg", source))
    image = _open(stored)

    assert "comment" not in image.info
    assert "xmp" not in image.info
    for leak in LEAKS:
        assert leak not in _stored_bytes(stored), leak


def test_a_png_is_rebuilt_without_losing_a_pixel():
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "1 Example Road")
    info.add_itxt("XML:com.adobe.xmp", "<x>exif:GPSLatitude</x>")
    source = _png(pnginfo=info, exif=_exif())

    stored = prepare_photo(_upload("screenshot.png", source, "image/png"))
    image = _open(stored)

    assert stored.name == "screenshot.png"
    assert stored.content_type == "image/png"
    assert ImageChops.difference(image.convert("RGB"), _picture()).getbbox() is None
    assert "Comment" not in image.info
    assert "xmp" not in image.info
    for leak in LEAKS:
        assert leak not in _stored_bytes(stored), leak


def test_a_png_without_a_profile_keeps_its_gamma_and_chromaticity():
    """Browsers apply these to a PNG with no ICC profile; without them it displays darker."""
    info = PngImagePlugin.PngInfo()
    info.add(b"gAMA", struct.pack(">I", 100_000))
    info.add(b"cHRM", struct.pack(">8I", 31_270, 32_900, 64_000, 33_000, 30_000, 60_000, 15_000, 6_000))
    source = Image.open(io.BytesIO(_png(pnginfo=info)))

    image = _open(prepare_photo(_upload("screenshot.png", _png(pnginfo=info))))
    image.load()

    assert image.info["gamma"] == source.info["gamma"]
    assert source.info["gamma"] == pytest.approx(1.0)
    assert image.info["chromaticity"] == source.info["chromaticity"]


def test_a_palette_png_keeps_its_transparency():
    """Transparency is how the picture looks, not metadata."""
    source = Image.new("P", (4, 1))
    source.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0])
    source.putdata([0, 1, 2, 1])
    data = _png(source, transparency=1)  # the red pixels are see-through
    before = Image.open(io.BytesIO(data)).convert("RGBA")
    assert before.getpixel((1, 0))[3] == 0

    image = _open(prepare_photo(_upload("icon.png", data)))

    assert image.convert("RGBA").tobytes() == before.tobytes()


def test_a_photo_with_nothing_worth_keeping_carries_no_exif():
    stored = prepare_photo(_upload("scale.jpg", _jpeg()))

    assert not _open(stored).getexif()
    assert b"Exif" not in _stored_bytes(stored)


def _jpeg_with_app1(payload: bytes) -> bytes:
    """Splice a raw APP1 segment in after the JPEG's start marker.

    Returns:
        The file.

    """
    plain = _jpeg()
    return plain[:2] + b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload + plain[2:]


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data")
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"Exif\x00\x00" + b"not-a-tiff-header" * 4, id="bad-header"),
        pytest.param(b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x05\x01\x0f\x00\x02", id="truncated-directory"),
        pytest.param(
            b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x01\x88\x25\x00\x04\x00\x00\x00\x01\x00\x00\xff\xff"
            b"\x00\x00\x00\x00",
            id="gps-pointing-nowhere",
        ),
    ],
)
def test_a_photo_with_broken_exif_is_still_taken(payload):
    """Browsers show these photos, so refusing them would be the site's fault, not the rider's."""
    stored = prepare_photo(_upload("scale.jpg", _jpeg_with_app1(payload)))

    assert _open(stored).size == (64, 48)
    assert not _open(stored).getexif().get_ifd(ExifTags.IFD.GPSInfo)


def test_exif_that_cannot_be_parsed_at_all_is_dropped_not_fatal(monkeypatch, logged):
    def unreadable(self, data):
        raise SyntaxError("not a TIFF file")

    monkeypatch.setattr(Image.Exif, "load", unreadable)

    stored = prepare_photo(_upload("scale.jpg", _jpeg(exif=_exif())))

    assert b"Exif" not in _stored_bytes(stored)
    assert ("warning", "Verification photo EXIF unreadable; stored without it") in [
        (level, message) for level, message, _ in logged
    ]


# --- colour profiles: kept, without device identifiers ------------------------------------------


@pytest.mark.parametrize(("name", "make"), [("scale.jpg", _jpeg), ("scale.png", _png)], ids=["jpeg", "png"])
def test_a_jpeg_or_png_colour_profile_is_kept(name, make):
    """An iPhone 'Most Compatible' JPEG is Display P3 too; without the profile it looks washed out."""
    image = _open(prepare_photo(_upload(name, make(icc_profile=SRGB, exif=_exif()))))

    assert image.info.get("icc_profile") == SRGB


SOURCES_WITH_A_DISPLAY_PROFILE = {
    "heic": lambda: _upload("Screenshot.heic", _heic("display_profile.heic")),
    "jpeg": lambda: _upload("Screenshot.jpg", _jpeg(icc_profile=_with_display_serial(SRGB))),
    "png": lambda: _upload("Screenshot.png", _png(icc_profile=_with_display_serial(SRGB))),
}


@pytest.mark.parametrize("source", sorted(SOURCES_WITH_A_DISPLAY_PROFILE))
def test_a_display_serial_is_taken_out_of_the_colour_profile(source):
    """MacOS tags screenshots with the display's profile, serial included; the RAW advice says 'screenshot'."""
    stored = prepare_photo(SOURCES_WITH_A_DISPLAY_PROFILE[source]())
    profile = _open(stored).info["icc_profile"]

    assert b"FIXTURE-DISPLAY-SERIAL" not in profile
    assert b"FIXTURE-DISPLAY-SERIAL" not in _stored_bytes(stored)
    assert b"mmod" not in _icc_tags(profile)
    assert set(_icc_tags(profile)) == set(_icc_tags(SRGB))
    # Still a working profile, describing the same colours.
    cleaned = ImageCms.ImageCmsProfile(io.BytesIO(profile))
    original = ImageCms.ImageCmsProfile(io.BytesIO(SRGB))
    assert ImageCms.getProfileDescription(cleaned) == ImageCms.getProfileDescription(original)
    ImageCms.profileToProfile(_picture(), cleaned, ImageCms.createProfile("sRGB"))


def test_a_colour_only_profile_is_kept_byte_for_byte():
    assert evidence_media._clean_icc(SRGB) is SRGB


def _with_room_for_text(icc: bytes, text: bytes) -> bytes:
    """Put text inside the profile's declared size, where no tag points.

    Returns:
        The profile, its size field grown to cover the text.

    """
    profile = icc + text
    return struct.pack(">I", len(profile)) + profile[4:]


@pytest.mark.parametrize(
    "profile",
    [
        pytest.param(SRGB + b"1 Example Road", id="after-the-declared-size"),
        pytest.param(_with_room_for_text(SRGB, b"1 Example Road"), id="inside-it-between-tags"),
        pytest.param(SRGB[:100] + b"1 Example Road".ljust(28, b"\0") + SRGB[128:], id="in-the-reserved-header"),
    ],
)
def test_text_hidden_in_a_valid_profile_is_removed(profile):
    """Every tag is a colour tag, so the profile used to come back as it was sent."""
    cleaned = evidence_media._clean_icc(profile)

    assert b"Example Road" not in cleaned
    assert set(_icc_tags(cleaned)) == set(_icc_tags(SRGB))
    assert cleaned[84:100] == bytes(16)  # the ID no longer matches, so it is cleared
    ImageCms.ImageCmsProfile(io.BytesIO(cleaned))

    stored = prepare_photo(_upload("scale.jpg", _jpeg(icc_profile=profile)))

    assert b"Example Road" not in _stored_bytes(stored)


@pytest.mark.parametrize(
    "profile",
    [
        pytest.param(b"not a profile, but 1 Example Road " * 10, id="text"),
        pytest.param(SRGB[:100], id="truncated-header"),
        pytest.param(SRGB[:128] + struct.pack(">I", 500) + SRGB[132:], id="table-past-the-end"),
    ],
)
def test_something_that_is_not_a_profile_is_dropped(profile):
    """The profile is a free-form blob, so an arbitrary one could carry anything."""
    assert evidence_media._clean_icc(profile) is None

    stored = prepare_photo(_upload("scale.jpg", _jpeg(icc_profile=profile)))

    assert "icc_profile" not in _open(stored).info
    assert b"Example Road" not in _stored_bytes(stored)


# --- animations ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("frame_count", "loop"), [(2, 0), (3, 0), (3, 3), (3, None)])
def test_an_animated_gif_keeps_its_frames_timing_and_looping_but_not_its_comment(frame_count, loop):
    frames = [Image.new("RGB", (8, 8), (i * 80, 0, 0)) for i in range(frame_count)]
    params = {"duration": [100 * (i + 1) for i in range(frame_count)], "comment": b"1 Example Road"}
    if loop is not None:
        params["loop"] = loop

    stored = prepare_photo(_upload("scale.gif", _gif(frames, **params), "image/gif"))
    image = _open(stored)

    assert stored.content_type == "image/gif"
    assert image.n_frames == frame_count
    assert [frame.info["duration"] for frame in ImageSequence.Iterator(image)] == params["duration"]
    if loop is None:
        assert "loop" not in image.info  # a play-once GIF must not start looping
    else:
        assert image.info["loop"] == loop
    assert b"Example Road" not in _stored_bytes(stored)


def test_an_opaque_gif_is_written_as_differences_and_stays_small():
    """Clearing every frame to the background made screen-recording GIFs 10-100 times bigger."""
    base = Image.effect_noise((120, 90), 60).convert("RGB")
    frames = []
    for i in range(20):
        frame = base.copy()
        frame.paste((255, 0, 0), (i * 4, 40, i * 4 + 8, 48))
        frames.append(frame)
    source = _gif(frames, duration=50, loop=0)

    stored = prepare_photo(_upload("recording.gif", source))
    image = _open(stored)

    assert len(_stored_bytes(stored)) < len(source) * 2
    assert {frame.disposal_method for frame in ImageSequence.Iterator(image)} == {1}
    # Same frames, give or take the few levels Pillow's GIF palette choice moves a colour.
    before = [frame.convert("RGB") for frame in ImageSequence.Iterator(Image.open(io.BytesIO(source)))]
    after = [frame.convert("RGB") for frame in ImageSequence.Iterator(image)]
    assert len(after) == len(before)
    for old_frame, new_frame in zip(before, after, strict=True):
        assert max(high for _, high in ImageChops.difference(old_frame, new_frame).getextrema()) <= 16


def test_a_see_through_gif_still_clears_between_frames():
    """Without clearing, a transparent animation smears each frame over the last in a browser."""
    frames = [Image.new("RGBA", (8, 8), (0, 0, 0, 0)) for _ in range(2)]
    frames[0].paste((255, 0, 0, 255), (0, 0, 4, 4))
    frames[1].paste((255, 0, 0, 255), (4, 4, 8, 8))

    source = _gif(frames, duration=100, loop=0, disposal=2)

    image = _open(prepare_photo(_upload("sprite.gif", source)))

    # Only the first frame needs clearing: the second turns its opaque corner see-through.
    assert [frame.disposal_method for frame in ImageSequence.Iterator(image)] == [2, 1]
    before = [frame.convert("RGBA") for frame in ImageSequence.Iterator(Image.open(io.BytesIO(source)))]
    after = [frame.convert("RGBA") for frame in ImageSequence.Iterator(image)]
    for old_frame, new_frame in zip(before, after, strict=True):
        assert old_frame.getchannel("A").tobytes() == new_frame.getchannel("A").tobytes()


def test_one_see_through_pixel_does_not_make_a_gif_balloon():
    """The disposal is chosen per frame; chosen for the file, one clear pixel made it 150 times bigger."""
    base = Image.effect_noise((120, 90), 60).convert("RGB").quantize(64)
    frames = []
    for i in range(20):
        frame = base.copy()
        frame.paste(1, (i * 4, 40, i * 4 + 8, 48))
        frames.append(frame)
    source = _gif(frames, duration=50, loop=0, transparency=0)  # palette entry 0 is see-through

    stored = prepare_photo(_upload("recording.gif", source))

    assert len(_stored_bytes(stored)) < len(source) * 3
    before = [frame.convert("RGBA") for frame in ImageSequence.Iterator(Image.open(io.BytesIO(source)))]
    after = [frame.convert("RGBA") for frame in ImageSequence.Iterator(_open(stored))]
    assert len(after) == len(before)
    for old_frame, new_frame in zip(before, after, strict=True):
        assert old_frame.getchannel("A").tobytes() == new_frame.getchannel("A").tobytes()


def test_a_long_still_gif_is_not_called_damaged():
    """Identical frames are merged and their delays added, past the most a GIF frame can hold."""
    source = _raw_gif([_at(0, 0, delay=65_535), _at(0, 0, delay=65_535), _at(0, 0, colour=(0, 0, 255))])
    assert [f.info["duration"] for f in ImageSequence.Iterator(Image.open(io.BytesIO(source)))] == [
        655_350,
        655_350,
        100,
    ]

    image = _open(prepare_photo(_upload("still.gif", source)))

    assert image.n_frames == 2
    assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 0)


def test_a_long_still_animated_png_is_not_called_damaged():
    """APNG merges identical frames too, and refuses a delay past 65.535 s with a different error."""
    red, blue = Image.new("RGBA", (8, 8), (255, 0, 0, 255)), Image.new("RGBA", (8, 8), (0, 0, 255, 255))
    # Kept apart in the source only by their blend operations, as another encoder might write them.
    source = _png(red, save_all=True, append_images=[red, blue], duration=[33_333, 33_334, 100], blend=[0, 1, 0])
    assert Image.open(io.BytesIO(source)).n_frames == 3

    image = _open(prepare_photo(_upload("still.png", source)))

    assert image.n_frames == 2
    assert [frame.convert("RGB").getpixel((0, 0)) for frame in ImageSequence.Iterator(image)] == [
        (255, 0, 0),
        (0, 0, 255),
    ]


def test_an_animated_png_keeps_its_frames():
    frames = [Image.new("RGBA", (8, 8), colour) for colour in ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))]
    source = _png(frames[0], save_all=True, append_images=frames[1:], duration=200, loop=0)

    stored = prepare_photo(_upload("clip.png", source, "image/png"))
    image = _open(stored)

    assert stored.name == "clip.png"
    assert image.n_frames == 3
    assert [frame.convert("RGB").getpixel((0, 0)) for frame in ImageSequence.Iterator(image)] == [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
    ]


def test_an_animated_png_drops_the_fallback_picture_browsers_never_show():
    fallback = Image.new("RGBA", (8, 8), (255, 255, 255, 255))
    frames = [Image.new("RGBA", (8, 8), colour) for colour in ((255, 0, 0, 255), (0, 0, 255, 255))]
    source = _png(fallback, save_all=True, append_images=frames, duration=200, loop=0, default_image=True)
    assert Image.open(io.BytesIO(source)).convert("RGB").getpixel((0, 0)) == (255, 255, 255)

    image = _open(prepare_photo(_upload("clip.png", source)))

    assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


# --- oversized pictures are refused before they are decoded ---------------------------------


@pytest.mark.parametrize(
    ("name", "make"),
    [
        pytest.param("scale.jpg", _jpeg, id="jpeg"),
        pytest.param("scale.png", _png, id="png"),
        pytest.param("scale.gif", lambda: _gif([_picture()]), id="gif"),
    ],
)
def test_a_picture_with_too_many_pixels_is_refused(monkeypatch, name, make):
    """64x48 is 3,072 pixels."""
    monkeypatch.setattr(evidence_media, "EVIDENCE_MAX_PIXELS", 3071)

    refusal = _refusal(_upload(name, make()))

    assert refusal.reason == "too_many_pixels"
    assert "megapixels" in refusal.message


@pytest.mark.parametrize(
    ("name", "make", "costly"),
    [
        pytest.param("clip.heic", lambda: _heic("transparent.heic"), True, id="transparent-heic"),
        pytest.param("clip.heic", lambda: _heic("premultiplied.heic"), True, id="premultiplied-heic"),
        pytest.param("grey.heic", lambda: _heic("grey10.heic"), True, id="high-bit-grey-heic"),
        pytest.param("IMG_0001.HEIC", lambda: _heic("tagged.heic"), False, id="ordinary-heic"),
        pytest.param("scale.jpg", lambda: _jpeg(progressive=True), True, id="progressive-jpeg"),
        pytest.param("scale.jpg", _jpeg, False, id="baseline-jpeg"),
    ],
)
def test_photos_that_are_costly_to_decode_get_the_lower_ceiling(monkeypatch, name, make, costly):
    """No phone camera writes these, and each takes two to three times the memory to decode."""
    monkeypatch.setattr(evidence_media, "COSTLY_DECODE_MAX_PIXELS", 1000)  # every fixture is bigger

    if costly:
        assert _refusal(_upload(name, make())).reason == "too_many_pixels"
    else:
        assert prepare_photo(_upload(name, make()))


def test_the_costly_ceiling_still_takes_an_ordinary_screenshot():
    assert 2560 * 1600 * 4 <= evidence_media.COSTLY_DECODE_MAX_PIXELS < evidence_media.EVIDENCE_MAX_PIXELS


def test_a_picture_at_the_ceiling_is_taken(monkeypatch):
    monkeypatch.setattr(evidence_media, "EVIDENCE_MAX_PIXELS", 3072)

    assert prepare_photo(_upload("scale.jpg", _jpeg())).name == "scale.jpg"


def test_the_ceilings_leave_room_for_every_phone_camera():
    """Leave room for an iPhone's 48 MP and the 50 MP Pixel and Samsung sensors, in either format."""
    for ceiling in (evidence_media.EVIDENCE_MAX_PIXELS, evidence_media.HEIF_MAX_PIXELS):
        assert ceiling >= 8064 * 6048
        assert ceiling >= 8160 * 6120
    assert evidence_media.HEIF_MAX_PIXELS <= evidence_media.EVIDENCE_MAX_PIXELS < Image.MAX_IMAGE_PIXELS


def test_an_ordinary_heic_has_its_own_ceiling(monkeypatch):
    monkeypatch.setattr(evidence_media, "HEIF_MAX_PIXELS", 3071)  # the fixture is 3,072 pixels

    assert _refusal(_upload("IMG_0001.HEIC", _heic("tagged.heic"))).reason == "too_many_pixels"
    assert prepare_photo(_upload("scale.jpg", _jpeg()))  # a JPEG the same size is still taken


def test_pillows_own_refusal_is_reported_as_too_many_pixels(monkeypatch):
    """Pillow raises above twice its own ceiling, before this module's check can run."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)

    assert _refusal(_upload("scale.jpg", _jpeg())).reason == "too_many_pixels"


def test_no_warnings_filter_is_left_behind(recwarn):
    """catch_warnings() swaps process-wide state that concurrent requests would trample."""
    import warnings

    before = list(warnings.filters)

    prepare_photo(_upload("scale.jpg", _jpeg()))

    assert warnings.filters == before


@pytest.mark.parametrize("walked_first", [True, False], ids=["from-the-bytes", "while-decoding"])
def test_an_animation_is_counted_frame_by_frame(monkeypatch, walked_first):
    """Each frame is under the ceiling; all of them decoded at once are not.

    Counted twice: from the file's bytes before Pillow opens it, and again as each frame is
    decoded, in case the two readings ever disagree.
    """
    frames = [Image.new("RGB", (20, 20), (i * 10, 0, 0)) for i in range(4)]
    monkeypatch.setattr(evidence_media, "MAX_ANIMATION_PIXELS", 1000)  # 400 a frame, 1,600 in all
    if not walked_first:
        monkeypatch.setattr(evidence_media, "_gif_canvas_sizes", lambda data: [])

    assert _refusal(_upload("scale.gif", _gif(frames))).reason == "too_many_pixels"


def test_an_animated_png_is_counted_before_it_is_decoded(monkeypatch):
    frames = [Image.new("RGBA", (20, 20), (i * 60, 0, 0, 255)) for i in range(4)]
    source = _png(frames[0], save_all=True, append_images=frames[1:], duration=100)
    monkeypatch.setattr(evidence_media, "MAX_ANIMATION_PIXELS", 1000)
    monkeypatch.setattr(evidence_media, "_animation_frames", lambda *a: pytest.fail("decoded anyway"))

    assert _refusal(_upload("clip.png", source)).reason == "too_many_pixels"


def test_the_animation_budget_is_well_under_a_stills():
    """A decoded GIF costs several times a still per pixel; 100 frames of 480x360 must still fit."""
    assert 100 * 480 * 360 <= evidence_media.MAX_ANIMATION_PIXELS < evidence_media.EVIDENCE_MAX_PIXELS


@pytest.mark.parametrize(
    "frames",
    [
        pytest.param([_at(0, 0), _at(5000, 5000)], id="canvas-grown-past-the-budget"),
        pytest.param([_at(0, 0), _at(0, 0, size=(13370, 13370), disposal=2)], id="frame-extent-past-the-budget"),
        pytest.param([_at(0, 0, size=(9000, 9000))], id="first-frame-past-the-budget"),
    ],
)
def test_a_gif_that_would_grow_is_refused_before_pillow_opens_it(monkeypatch, frames):
    """Pillow allocates for a grown canvas, or a frame's disposal area, while seeking to it."""
    opened = []
    real_open = Image.open
    monkeypatch.setattr(evidence_media.Image, "open", lambda *a, **kw: opened.append(a) or real_open(*a, **kw))

    refusal = _refusal(_upload("tiny.gif", _raw_gif(frames)))

    assert refusal.reason == "too_many_pixels"
    assert opened == []


def test_a_gif_frame_past_the_screen_edge_is_clipped_as_a_browser_clips_it():
    """Pillow grows the canvas to fit; browsers draw only the declared screen."""
    source = _raw_gif([_at(0, 0), _at(2, 2, colour=(0, 0, 255))])
    assert Image.open(io.BytesIO(source)).size == (1, 1)

    image = _open(prepare_photo(_upload("clip.gif", source)))

    assert image.size == (1, 1)
    assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 0)


def test_the_gif_walk_stops_at_the_frame_limit():
    """Counting stops as soon as the limit is passed, however many frames the file holds."""
    source = _raw_gif([_at(0, 0)] * 50_000)

    with pytest.raises(PhotoRejectedError) as caught:
        evidence_media._gif_canvas_sizes(source)

    assert caught.value.reason == "too_many_frames"
    assert len(evidence_media._gif_canvas_sizes(_raw_gif([_at(0, 0)] * 3))) == 3


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
@pytest.mark.parametrize("offset", [9000, 11000, 14000], ids=["under-pillows-ceiling", "pillow-warns", "pillow-raises"])
def test_a_gif_whose_canvas_grows_is_refused_before_it_is_decoded(offset):
    """A GIF of under 200 bytes grew to 81 MP a frame and took 3 GB; the first frame is only 1x1."""
    source = _raw_gif([_at(0, 0)] + [_at(offset, offset)] * 5)
    assert len(source) < 200
    started = time.monotonic()

    refusal = _refusal(_upload("tiny.gif", source))

    assert refusal.reason == "too_many_pixels"
    assert time.monotonic() - started < 2


def test_a_gif_with_too_many_frames_is_refused_quickly():
    """400,000 one-pixel frames took a minute and a gigabyte, under any pixel ceiling."""
    frames = [
        Image.new("RGB", (1, 1), RED if i % 2 else (0, 0, 255)) for i in range(evidence_media.MAX_ANIMATION_FRAMES + 1)
    ]
    source = _gif(frames, duration=20)
    assert Image.open(io.BytesIO(source)).n_frames == evidence_media.MAX_ANIMATION_FRAMES + 1
    started = time.monotonic()

    refusal = _refusal(_upload("many.gif", source))

    assert refusal.reason == "too_many_frames"
    assert str(evidence_media.MAX_ANIMATION_FRAMES) in refusal.message
    assert time.monotonic() - started < 5


def test_a_gif_at_the_frame_limit_is_taken():
    frames = [
        Image.new("RGB", (1, 1), RED if i % 2 else (0, 0, 255)) for i in range(evidence_media.MAX_ANIMATION_FRAMES)
    ]

    image = _open(prepare_photo(_upload("many.gif", _gif(frames, duration=20))))

    assert image.n_frames == evidence_media.MAX_ANIMATION_FRAMES


def test_a_rebuild_bigger_than_the_upload_limit_is_refused():
    """The limit is on what is stored, too; a rebuild can come out bigger than what was sent."""
    source = _jpeg()
    rebuilt_size = len(_stored_bytes(prepare_photo(_upload("scale.jpg", source))))

    with pytest.raises(PhotoRejectedError) as caught:
        prepare_photo(_upload("scale.jpg", source), max_bytes=rebuilt_size - 1)

    assert caught.value.reason == "too_large_after_rebuild"
    assert prepare_photo(_upload("scale.jpg", source), max_bytes=rebuilt_size).name == "scale.jpg"


@pytest.mark.django_db
def test_the_form_holds_the_rebuild_to_the_upload_limit(monkeypatch):
    seen = []
    real = evidence_media.prepare_photo
    monkeypatch.setattr(evidence_media, "prepare_photo", lambda upload, **kw: seen.append(kw) or real(upload, **kw))

    with override_config(MAX_MEDIA_UPLOAD_MB=7):
        assert _form("weight_light", "photo", _upload("scale.jpg", _jpeg())).is_valid()

    assert seen == [{"max_bytes": 7 * 1024 * 1024}]


# --- the content decides the format ------------------------------------------------------------


def test_a_png_named_jpg_is_stored_as_a_png():
    stored = prepare_photo(_upload("scale.jpg", _png(), "image/jpeg"))

    assert stored.name == "scale.png"
    assert stored.content_type == "image/png"
    assert _open(stored).format == "PNG"


def test_a_jpeg_keeps_the_riders_spelling_of_its_extension():
    assert prepare_photo(_upload("scale.JPEG", _jpeg())).name == "scale.jpeg"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"<html><script>alert(1)</script></html>", id="html"),
        pytest.param(b"", id="empty"),
        pytest.param(b"\xff\xd8\xff\xe0garbage", id="broken-jpeg"),
    ],
)
def test_something_that_only_claims_to_be_a_photo_is_refused(data):
    """Its bytes were stored as sent before; an HTML page named .jpg could open from the bucket."""
    assert _refusal(_upload("scale.jpg", data, "text/html")).reason == "unreadable"


@pytest.mark.parametrize("pillow_format", ["BMP", "TIFF", "WEBP"])
def test_a_format_outside_the_list_is_refused_even_though_pillow_reads_it(pillow_format):
    out = io.BytesIO()
    _picture().save(out, pillow_format)

    assert _refusal(_upload("scale.png", out.getvalue())).reason == "unreadable"


def test_a_truncated_jpeg_is_refused():
    """Pillow would otherwise pad the missing rows with grey and store that."""
    assert _refusal(_upload("scale.jpg", _jpeg()[:-200])).reason == "unreadable"


def test_the_wrong_kind_messages_list_the_right_extensions():
    """Pinned as text: they are what a rider reads, on the page and from the server."""
    assert evidence_media.WRONG_KIND_MESSAGES == {
        "photo": "Photo evidence needs a photo file: .jpg, .jpeg, .png, .gif, .heic or .heif.",
        "video": "Video evidence needs a video file: .mp4, .mov, .avi or .webm.",
    }


# --- what reaches the logs ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "source_format", "had_gps"),
    [
        pytest.param(lambda: _upload("Jane_Rider_home.HEIC", _heic("tagged.heic")), "HEIF", True, id="located"),
        pytest.param(
            lambda: _upload("Jane_Rider_home.jpg", _jpeg(exif=_exif(gps=False))), "JPEG", False, id="no-location"
        ),
    ],
)
def test_the_log_line_says_what_happened_without_naming_the_file(logged, make, source_format, had_gps):
    prepare_photo(make())

    assert len(logged) == 1
    _, message, fields = logged[0]
    assert message == "Verification photo prepared"
    assert fields["source_format"] == source_format
    assert fields["output_format"] == "JPEG"
    assert fields["had_gps"] is had_gps
    assert "Jane" not in repr(fields)


def test_an_unreadable_photo_is_logged_without_naming_the_file(logged):
    with pytest.raises(PhotoRejectedError):
        prepare_photo(_upload("Jane_Rider_home.jpg", b"not a photo"))

    level, message, fields = logged[0]
    assert (level, message, fields["reason"]) == ("warning", "Verification photo refused", "unreadable")
    assert fields["error_type"]
    assert "Jane" not in repr(fields)


def test_an_oversized_photo_is_logged_without_naming_the_file(logged, monkeypatch):
    monkeypatch.setattr(evidence_media, "EVIDENCE_MAX_PIXELS", 1000)

    with pytest.raises(PhotoRejectedError):
        prepare_photo(_upload("Jane_Rider_home.jpg", _jpeg()))

    assert logged
    assert logged[-1][2]["reason"] == "too_many_pixels"
    assert "Jane" not in repr(logged)


# --- the submission form -----------------------------------------------------------------------


def _form(verify_type: str, media_type: str, upload, **data) -> RaceReadyRecordForm:
    fields = {"verify_type": verify_type, "media_type": media_type, "record_date": "2026-09-01", **data}
    if verify_type in ("weight_full", "weight_light"):
        fields.setdefault("weight", "72.5")
    if verify_type == "height":
        fields.setdefault("height", "178")
    return RaceReadyRecordForm(data=fields, files={"media_file": upload})


@pytest.fixture
def photo_spy(monkeypatch):
    calls = []
    real = evidence_media.prepare_photo

    def spy(upload, **kwargs):
        calls.append(upload.name)
        return real(upload, **kwargs)

    monkeypatch.setattr(evidence_media, "prepare_photo", spy)
    return calls


@pytest.fixture
def form_logged(monkeypatch):
    from apps.team import forms as team_forms

    lines = []
    monkeypatch.setattr(team_forms.logfire, "warning", lambda message, **kw: lines.append((message, kw)))
    return lines


@pytest.mark.django_db
def test_the_form_stores_an_iphone_photo_as_jpeg():
    form = _form("weight_light", "photo", _upload("IMG_0001.HEIC", _heic("tagged.heic"), "image/heic"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["media_file"].name == "IMG_0001.jpg"
    assert form.cleaned_data["media_file"].content_type == "image/jpeg"


REFUSED_UPLOADS = {
    "unreadable": (
        ("weight_light", "photo", "IMG_0003.HEIC", _heic("truncated.heic")),
        evidence_media.UNREADABLE_PHOTO_MESSAGE,
    ),
    "wrong-kind": (
        ("height", "video", "scale.jpg", _jpeg()),
        "Video evidence needs a video file: .mp4, .mov, .avi or .webm.",
    ),
    "raw": (("weight_light", "photo", "IMG_0005.DNG", b"II*\x00" + b"x" * 64), evidence_media.RAW_PHOTO_MESSAGE),
}


@pytest.mark.django_db
@pytest.mark.parametrize("case", sorted(REFUSED_UPLOADS))
def test_a_refused_file_gets_one_message_not_two(case):
    """The model used to add "provide a file or a URL" beside the refusal explaining why there wasn't one."""
    (verify_type, media_type, name, data), message = REFUSED_UPLOADS[case]
    form = _form(verify_type, media_type, _upload(name, data))

    assert not form.is_valid()
    assert form.errors["media_file"] == [message]
    assert not form.non_field_errors()


@pytest.mark.django_db
def test_no_evidence_at_all_still_gets_the_no_evidence_message():
    form = RaceReadyRecordForm(
        data={"verify_type": "height", "media_type": "video", "record_date": "2026-09-01", "height": "178"}
    )

    assert not form.is_valid()
    assert form.non_field_errors() == ["You must provide either a file upload or a URL (or both)."]


@pytest.mark.django_db
@pytest.mark.parametrize("verify_type", ["weight_full", "height", "power"])
def test_video_evidence_refuses_a_photo(verify_type, photo_spy):
    """The review page plays Video evidence in a video player, where a photo shows nothing."""
    form = _form(verify_type, "video", _upload("scale.jpg", _jpeg(), "image/jpeg"))

    assert not form.is_valid()
    assert form.errors["media_file"] == ["Video evidence needs a video file: .mp4, .mov, .avi or .webm."]
    assert photo_spy == []  # refused before any time is spent decoding it


@pytest.mark.django_db
def test_photo_evidence_refuses_a_video():
    form = _form("weight_light", "photo", _upload("clip.mp4", b"x" * 64, "video/mp4"))

    assert not form.is_valid()
    assert form.errors["media_file"] == ["Photo evidence needs a photo file: .jpg, .jpeg, .png, .gif, .heic or .heif."]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "data"),
    [("scale.jpg", _jpeg(exif=_exif())), ("clip.mov", b"x" * 64)],
)
def test_link_evidence_takes_either_kind(name, data):
    """Unchanged: a link record may carry a file of either kind, shown as a download."""
    form = _form("height", "link", _upload(name, data), url="https://example.test/evidence")

    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_a_photo_on_a_link_record_is_still_rebuilt(photo_spy):
    form = _form("height", "link", _upload("scale.jpg", _jpeg(exif=_exif())), url="https://example.test/e")

    assert form.is_valid(), form.errors
    assert photo_spy == ["scale.jpg"]
    assert b"GPSLatitude" not in _stored_bytes(form.cleaned_data["media_file"])


@pytest.mark.django_db
def test_a_file_on_other_evidence_is_refused_without_being_decoded(photo_spy):
    form = _form("height", "other", _upload("scale.jpg", _jpeg()), notes="Weighed at the club.")

    assert not form.is_valid()
    assert "media_file" in form.errors
    assert photo_spy == []


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["IMG_0005.DNG", "IMG_0005.dng"])
def test_a_raw_photo_gets_advice_rather_than_a_list(name, photo_spy):
    """What rider 516's iPhone sent four times. A list of extensions did not tell them what to do."""
    form = _form("weight_light", "photo", _upload(name, b"II*\x00" + b"x" * 64, "image/x-adobe-dng"))

    assert not form.is_valid()
    assert form.errors["media_file"] == [
        "That photo is in RAW format (.dng), which reviewers can't open. Turn RAW off in your camera app and "
        "take the photo again, or upload a screenshot of it instead."
    ]
    assert photo_spy == []


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["video", "link"])
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG_0006.MOV", "video/quicktime"),
        ("clip.mp4", "video/mp4"),
        ("clip.webm", "video/webm"),
        ("clip.avi", "video/x-msvideo"),
    ],
)
def test_a_video_is_stored_with_the_type_its_extension_implies(media_type, name, expected):
    """Not the browser's claim: that is what the bucket serves it as."""
    extra = {"url": "https://example.test/e"} if media_type == "link" else {}
    form = _form("height", media_type, _upload(name, b"x" * 64, "text/html"), **extra)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["media_file"].content_type == expected


@pytest.mark.parametrize(
    ("upload", "expected"),
    [
        pytest.param(
            lambda: prepare_photo(_upload("IMG_0001.HEIC", _heic("tagged.heic"), "image/heic")),
            "image/jpeg",
            id="converted-photo",
        ),
        pytest.param(lambda: _upload("clip.mov", b"x", "text/html"), "text/html", id="control-unchecked-video"),
    ],
)
def test_the_bucket_takes_the_type_from_the_upload_object(upload, expected):
    """django-storages prefers the upload's own content_type over the name, so setting it matters.

    The control shows why the form overrides a video's: left alone, the browser's claim is stored.
    """
    from storages.backends.s3 import S3Storage

    storage = S3Storage(bucket_name="test-bucket", access_key="AKIAEXAMPLE", secret_key="topsecret")  # noqa: S106

    assert storage._get_write_parameters("race_ready/2026/09/x", upload())["ContentType"] == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("verify_type", "media_type", "name", "data", "reason"),
    [
        ("weight_light", "photo", "Jane_Rider_home.DNG", b"II*\x00", "raw_photo"),
        ("weight_light", "photo", "Jane_Rider_home.mp4", b"x", "wrong_file_kind"),
        ("weight_full", "video", "Jane_Rider_home.jpg", b"x", "wrong_file_kind"),
        ("weight_full", "video", "Jane_Rider_home.pdf", b"x", "invalid_file_type"),
    ],
)
def test_the_forms_refusals_are_logged_without_the_file_name(form_logged, verify_type, media_type, name, data, reason):
    assert not _form(verify_type, media_type, _upload(name, data)).is_valid()

    assert [kw.get("error_reason") for _, kw in form_logged] == [reason]
    assert "Jane" not in repr(form_logged)


@pytest.mark.django_db
def test_an_oversized_file_is_logged_without_its_name(form_logged):
    with override_config(MAX_MEDIA_UPLOAD_MB=0):
        assert not _form("height", "video", _upload("Jane_Rider_home.mov", b"x" * 64)).is_valid()

    assert [kw.get("error_reason") for _, kw in form_logged] == ["file_too_large"]
    assert "Jane" not in repr(form_logged)


@pytest.mark.django_db
def test_the_widget_carries_the_validators_own_messages():
    """The page shows these before an upload; a swap would tell a rider the opposite of the server."""
    attrs = RaceReadyRecordForm().fields["media_file"].widget.attrs

    assert attrs["data-wrong-kind-photo"] == evidence_media.WRONG_KIND_MESSAGES["photo"]
    assert attrs["data-wrong-kind-video"] == evidence_media.WRONG_KIND_MESSAGES["video"]
    assert attrs["data-raw-message"] == evidence_media.RAW_PHOTO_MESSAGE
    assert attrs["data-accept-photo"] == ".jpg,.jpeg,.png,.gif,.heic,.heif"
    assert attrs["data-accept-video"] == ".mp4,.mov,.avi,.webm"
    assert attrs["data-raw-extensions"] == ".dng"


# --- end to end ------------------------------------------------------------------------------


@pytest.fixture
def media_dir(settings, tmp_path):
    from django.core.files.storage import default_storage
    from django.utils.functional import empty

    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": tmp_path}},
    }
    default_storage._wrapped = empty
    yield tmp_path
    default_storage._wrapped = empty


@pytest.fixture
def rider(user_model):
    return user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        gender="male",
        zwid=6164399,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )


def _submit(client, name, data):
    return client.post(
        reverse("accounts:submit_race_ready"),
        {
            "verify_type": "weight_light",
            "media_type": "photo",
            "record_date": "2026-09-01",
            "weight": "72.5",
            "media_file": _upload(name, data),
        },
    )


@pytest.mark.django_db
def test_an_iphone_submission_is_stored_as_a_jpeg_without_its_location(client, rider, media_dir):
    client.force_login(rider)

    response = _submit(client, "IMG_0001.HEIC", _heic("tagged.heic"))

    assert response.status_code == 302
    record = RaceReadyRecord.objects.get(user=rider)
    assert record.media_file.name.startswith("race_ready/")
    assert record.media_file.name.endswith(".jpg")
    stored = (media_dir / record.media_file.name).read_bytes()
    image = Image.open(io.BytesIO(stored))
    assert image.format == "JPEG"
    assert not image.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    for leak in LEAKS:
        assert leak not in stored, leak


@pytest.mark.django_db
def test_a_refused_photo_leaves_nothing_behind(client, rider, media_dir):
    client.force_login(rider)

    _submit(client, "IMG_0005.DNG", b"II*\x00" + b"x" * 64)

    assert not RaceReadyRecord.objects.filter(user=rider).exists()
    assert not any(media_dir.rglob("*.*"))


@pytest.mark.django_db
def test_the_views_failure_log_carries_codes_not_messages(client, rider, media_dir, monkeypatch):
    """A message can quote the file name; the view logged every message until now."""
    from apps.accounts import views as account_views

    lines = []
    monkeypatch.setattr(account_views.logfire, "warning", lambda message, **kw: lines.append((message, kw)))
    client.force_login(rider)

    response = client.post(
        reverse("accounts:submit_race_ready"),
        {
            "verify_type": "weight_light",
            "media_type": "photo",
            "record_date": "2026-09-01",
            "weight": "72.5",
            "media_file": _upload("Jane_Rider_12_Example_Road.pdf", b"x"),
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert "Jane_Rider_12_Example_Road.pdf is not an allowed file type" in response.content.decode()
    failure = next(kw for message, kw in lines if message == "Race ready form validation failed")
    assert failure["form_errors"] == {"media_file": ["invalid_file_type"]}
    assert "Jane" not in repr(lines)


@pytest.mark.django_db
def test_the_page_gives_the_picker_what_it_needs(client, rider):
    """The script narrows the picker and checks a chosen file from these; the wording is the server's."""
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert 'data-accept-photo=".jpg,.jpeg,.png,.gif,.heic,.heif"' in body
    assert 'data-accept-video=".mp4,.mov,.avi,.webm"' in body
    assert 'data-raw-extensions=".dng"' in body
    assert f'data-raw-message="{escape(evidence_media.RAW_PHOTO_MESSAGE)}"' in body
    assert f'data-wrong-kind-photo="{escape(evidence_media.WRONG_KIND_MESSAGES["photo"])}"' in body
    assert f'data-wrong-kind-video="{escape(evidence_media.WRONG_KIND_MESSAGES["video"])}"' in body
    assert 'aria-describedby="media-file-help"' in body
    assert 'id="media-file-help"' in body


@pytest.mark.django_db
def test_the_page_tells_riders_what_happens_to_their_photos(client, rider):
    """Said where the file is chosen, not only in a policy page."""
    client.force_login(rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert "Photos are saved without their location" in body
    assert "iPhone (HEIC) photos are saved as JPEG" in body


@pytest.mark.django_db
@pytest.mark.skipif(shutil.which("node") is None, reason="needs node, as the Tailwind build does")
def test_the_page_script_narrows_the_picker_and_checks_a_file_before_upload(client, rider, tmp_path):
    """Runs the page's own script against a stub DOM -- see test_data/picker_check.js."""
    client.force_login(rider)
    page = tmp_path / "verification.html"
    page.write_text(client.get(reverse("accounts:verification")).content.decode())

    result = subprocess.run(  # noqa: S603 -- fixed arguments, a page this test rendered
        [shutil.which("node"), str(TEST_DATA / "picker_check.js"), str(page)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("ok ") >= 14
