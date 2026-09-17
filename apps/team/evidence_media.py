"""Files riders upload as Race Verified evidence: which kinds are taken, and how photos are stored.

Every photo is decoded and written out again before it is stored. That is what lets the site
take an iPhone's HEIC photos, which Chrome, Firefox and Edge cannot draw, and store them as
JPEGs. It is also what keeps a photo's location out of storage: a scale is usually photographed
at home, and the file says where. Only the metadata a reviewer can use survives -- when the
photo was taken and on what camera -- plus the colour profile, cut down to its colour tags
(which drops, for one, the display serial number macOS puts in a screenshot's profile).
GPS, embedded thumbnails, XMP, comments and maker notes are left behind.

A rebuild rather than an in-place metadata edit, because location can sit in EXIF, in XMP, in a
PNG text chunk or in the second image an iPhone JPEG carries, and only a rebuild is sure to
leave all of them out. JPEGs are written back with their own quantisation tables and chroma
subsampling, so the picture is not visibly compressed a second time.

A photo is decoded inside the web request, so the limits here are about memory and time as
much as correctness: a small, flat file can decode to hundreds of megabytes, and a GIF can hold
thousands of frames or grow its canvas part way through -- so a GIF's frames are measured from
its bytes before Pillow opens it.

HEIC is read with pi-heif, the decode-only build of pillow-heif (no GPL encoder), called
directly rather than registered as a Pillow plugin: registering it would let every ImageField
on the site -- logos, kit icons -- accept HEIC files most browsers cannot show.

Videos are stored as uploaded, but with the Content-Type their extension implies rather than
the one the browser sent, so an upload cannot be served from the bucket as, say, text/html.
A video's own location metadata is not removed: that takes a remux, which this module does not do.
"""

from __future__ import annotations

import io
import struct
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, Literal

import logfire
import pi_heif
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import ExifTags, Image, ImageChops, ImageSequence, JpegImagePlugin, PngImagePlugin

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

# Stored after a rebuild in their own format.
_REBUILT_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif")
# Stored as JPEG. ".heif" is the generic spelling some cameras and apps use for the same thing.
CONVERTED_PHOTO_EXTENSIONS = (".heic", ".heif")
PHOTO_EXTENSIONS = _REBUILT_PHOTO_EXTENSIONS + CONVERTED_PHOTO_EXTENSIONS

# The Content-Type each video is stored with, whatever the browser claimed.
VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
}
VIDEO_EXTENSIONS = tuple(VIDEO_CONTENT_TYPES)

# Refused with advice rather than the generic "not allowed": an iPhone with ProRAW on sends
# these straight from the photo library, and no browser can show one.
RAW_PHOTO_EXTENSIONS = (".dng",)

HEIC_JPEG_QUALITY = 90

# Above every phone camera's usual output (iPhone 48 MP, Pixel and Samsung 50 MP, the 64 MP a
# few others use) and below Pillow's own 89 MP ceiling.
EVIDENCE_MAX_PIXELS = 65_000_000
# HEIC takes about ten megabytes per megapixel to decode, JPEG about four, so HEIC stops lower:
# still above an iPhone's 48 MP and the 50 MP of a Samsung or Pixel saving HEIF.
HEIF_MAX_PIXELS = 50_000_000
# Photos that cost two to three times as much memory to decode -- a HEIC with transparency or
# high-bit greyscale, a progressive JPEG -- and that phone cameras do not write get a lower ceiling.
COSTLY_DECODE_MAX_PIXELS = 24_000_000
# An animation's frames are all held decoded until the file is written, and Pillow keeps its own
# canvas besides, so a GIF costs several times a still per pixel. This is the budget for all
# frames together -- 100 frames of 480x360 fit four times over. No phone camera makes these.
MAX_ANIMATION_PIXELS = 24_000_000
# Frames are decoded one by one in Python; this bounds how long that can take.
MAX_ANIMATION_FRAMES = 1000
# The longest single frame delay, in whole milliseconds, each animated format can store -- GIF in
# hundredths of a second, APNG as a fraction whose numerator Pillow keeps under 65536. Pillow
# merges identical consecutive frames by adding their delays, which can go past it.
_LONGEST_FRAME_MS = {"GIF": 655_350, "PNG": 65_535}

# What a rebuilt photo keeps. Anything not listed is dropped, so a tag added to a future
# phone's output is left out by default rather than carried through.
_KEPT_IMAGE_TAGS = (
    ExifTags.Base.Make,
    ExifTags.Base.Model,
    # Kept so a JPEG shot sideways still displays upright; the pixels are not rotated.
    # HEIC arrives already rotated, so it is dropped there.
    ExifTags.Base.Orientation,
    ExifTags.Base.DateTime,
)
_KEPT_EXIF_TAGS = (
    ExifTags.Base.DateTimeOriginal,
    ExifTags.Base.DateTimeDigitized,
    ExifTags.Base.OffsetTime,
    ExifTags.Base.OffsetTimeOriginal,
    ExifTags.Base.OffsetTimeDigitized,
    ExifTags.Base.SubsecTimeOriginal,
    ExifTags.Base.SubsecTimeDigitized,
    ExifTags.Base.ColorSpace,
)

# ICC tags that describe colour. Anything else -- Apple's "mmod" carries a display's serial
# number, and macOS tags screenshots with the display's profile -- is taken out.
_ICC_COLOUR_TAGS = frozenset({
    b"desc", b"dscm", b"cprt", b"wtpt", b"bkpt", b"chad", b"chrm", b"lumi", b"meas", b"tech",
    b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"kTRC",
    b"A2B0", b"A2B1", b"A2B2", b"B2A0", b"B2A1", b"B2A2",
    b"D2B0", b"D2B1", b"D2B2", b"D2B3", b"B2D0", b"B2D1", b"B2D2", b"B2D3",
    b"pre0", b"pre1", b"pre2", b"gamt", b"cicp", b"view", b"vued", b"ndin",
})  # fmt: skip
_ICC_HEADER_BYTES = 128
_ICC_MAX_BYTES = 4 * 1024 * 1024  # print (CMYK) profiles run to a few hundred kilobytes

# Pillow formats a non-HEIC photo may turn out to be. A JPEG that carries a second image, which
# is what iPhones write for an HDR photo, opens as MPO.
_PILLOW_FORMATS = ("JPEG", "PNG", "GIF")
_OUTPUT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}
_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif"}

EvidenceKind = Literal["photo", "video"]


class PhotoRejectedError(ValueError):
    """A photo that cannot be stored, with a message written for the rider."""

    def __init__(self, reason: str, message: str) -> None:
        """Keep a short reason code for logging beside the rider-facing message.

        Args:
            reason: Short code for logs, e.g. ``unreadable``.
            message: What to tell the rider.

        """
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class _Rebuilt:
    data: bytes
    output_format: str


def extension(name: str) -> str:
    """Return a file name's extension, lower-cased, with its dot ("" when there is none).

    The page's script has a copy of this rule (``extensionOf`` in race_ready_form.html).

    Args:
        name: File name as uploaded.

    Returns:
        E.g. ``".heic"`` for ``IMG_1234.HEIC``.

    """
    return PurePath(name or "").suffix.lower()


def evidence_kind(name: str) -> EvidenceKind | None:
    """Say whether an uploaded file is a photo or a video, going by its extension.

    Args:
        name: File name as uploaded.

    Returns:
        ``"photo"``, ``"video"``, or None for anything the form does not take.

    """
    ext = extension(name)
    if ext in PHOTO_EXTENSIONS:
        return "photo"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def _listed(extensions: tuple[str, ...]) -> str:
    """Join extensions the way a sentence lists them: ".a, .b or .c".

    Args:
        extensions: Extensions to list.

    Returns:
        The list as prose.

    """
    return f"{', '.join(extensions[:-1])} or {extensions[-1]}" if len(extensions) > 1 else extensions[0]


# One wording each, shared by the server check and the page's pre-upload check.
WRONG_KIND_MESSAGES: dict[EvidenceKind, str] = {
    "photo": f"Photo evidence needs a photo file: {_listed(PHOTO_EXTENSIONS)}.",
    "video": f"Video evidence needs a video file: {_listed(VIDEO_EXTENSIONS)}.",
}
RAW_PHOTO_MESSAGE = (
    "That photo is in RAW format (.dng), which reviewers can't open. Turn RAW off in your "
    "camera app and take the photo again, or upload a screenshot of it instead."
)
UNREADABLE_PHOTO_MESSAGE = (
    "That photo couldn't be read. It may be damaged, or not the kind of file its name says. "
    "Try taking it again, or upload a screenshot of it instead."
)
TOO_MANY_FRAMES_MESSAGE = (
    f"That animation has more than {MAX_ANIMATION_FRAMES} frames, which is too many to process. "
    "Please upload a still photo or a video instead."
)


def video_content_type(name: str) -> str:
    """Return the Content-Type a video is stored with, from its extension alone.

    Args:
        name: File name as uploaded; must have a video extension.

    Returns:
        E.g. ``"video/quicktime"`` for ``IMG_1234.MOV``.

    """
    return VIDEO_CONTENT_TYPES[extension(name)]


def _source_exif(image: Image.Image) -> Image.Exif:
    """Read the photo's own EXIF block, and nothing Pillow would infer in its place.

    ``Image.getexif()`` also fills in an Orientation from XMP and parses the EXIF text chunk
    ImageMagick writes into PNGs. Browsers ignore both, so writing either back as real EXIF
    would turn the stored picture from how the rider's upload displayed.

    Args:
        image: The opened photo.

    Returns:
        The parsed EXIF, empty when the file has none.

    """
    image.load()  # a PNG's eXIf chunk may follow the pixel data, and is only read with it
    exif = Image.Exif()
    if raw := image.info.get("exif"):
        exif.load(raw)
    return exif


def _kept_exif(image: Image.Image, *, keep_orientation: bool = True) -> bytes | None:
    """Build the EXIF block a rebuilt photo carries: capture time and camera, nothing else.

    Args:
        image: The opened photo.
        keep_orientation: False when the pixels are already upright.

    Returns:
        Serialised EXIF, or None when there is nothing worth keeping -- or when the photo's
        EXIF cannot be parsed. Browsers show such a photo, so it is stored without its EXIF
        rather than refused; dropping it all is also the safe way round for its location.

    """
    try:
        source = _source_exif(image)
        kept = Image.Exif()
        for tag in _KEPT_IMAGE_TAGS:
            if tag in source and (keep_orientation or tag != ExifTags.Base.Orientation):
                kept[tag] = source[tag]
        source_exif_ifd = source.get_ifd(ExifTags.IFD.Exif)
        exif_ifd = {tag: source_exif_ifd[tag] for tag in _KEPT_EXIF_TAGS if tag in source_exif_ifd}
        if exif_ifd:
            kept.get_ifd(ExifTags.IFD.Exif).update(exif_ifd)
        if not len(kept) and not exif_ifd:
            return None
        return kept.tobytes()
    except Exception as exc:
        logfire.warning("Verification photo EXIF unreadable; stored without it", error_type=type(exc).__name__)
        return None


def _had_gps(image: Image.Image) -> bool:
    """Whether the photo's EXIF carried a location. For the log line only.

    Args:
        image: The opened photo.

    Returns:
        True if the GPS block had any entries.

    """
    try:
        return bool(_source_exif(image).get_ifd(ExifTags.IFD.GPSInfo))
    except Exception:  # a malformed GPS block still counts as having had one
        return True


def _clean_icc(icc: bytes | None) -> bytes | None:
    """Return a colour profile holding only its colour tags, or None if it is not a usable profile.

    Only what a colour tag points at survives, with the header and the tag table: bytes past the
    profile's declared size, gaps between tags, the header's reserved bytes and the data of any
    dropped tag are gone or zeroed. Nothing moves, so the kept offsets stay valid. A profile
    that was already just that -- an iPhone's Display P3 -- comes back byte for byte. Otherwise
    the profile ID, an MD5 of the profile, is zeroed, which the ICC spec reads as "not computed".
    A kept description tag still says whatever its maker wrote; that is the profile's name.

    Args:
        icc: The profile bytes as the photo carried them.

    Returns:
        The profile to store, or None.

    """
    if not icc or len(icc) > _ICC_MAX_BYTES or icc[36:40] != b"acsp":
        return None
    declared = int.from_bytes(icc[0:4], "big")
    if not _ICC_HEADER_BYTES + 4 <= declared <= len(icc):
        return None
    source = icc[:declared]
    count = int.from_bytes(source[128:132], "big")
    table_end = 132 + 12 * count
    if not count or table_end > declared:
        return None
    entries = []
    for start in range(132, table_end, 12):
        signature = source[start : start + 4]
        offset = int.from_bytes(source[start + 4 : start + 8], "big")
        size = int.from_bytes(source[start + 8 : start + 12], "big")
        if offset < table_end or offset + size > declared:
            return None
        entries.append((signature, offset, size))
    kept = [entry for entry in entries if entry[0] in _ICC_COLOUR_TAGS]
    if not kept:
        return None

    cleaned = bytearray(declared)
    cleaned[:100] = source[:100]  # the header, less its reserved bytes (100-127)
    table = b"".join(sig + off.to_bytes(4, "big") + size.to_bytes(4, "big") for sig, off, size in kept)
    cleaned[128:table_end] = len(kept).to_bytes(4, "big") + table.ljust(table_end - 132, b"\0")
    for _, offset, size in kept:
        cleaned[offset : offset + size] = source[offset : offset + size]
    if cleaned == icc:
        return icc
    cleaned[84:100] = bytes(16)
    return bytes(cleaned)


def _too_many_pixels_message(limit: int | None = None) -> str:
    """Word the refusal for a photo over a pixel ceiling.

    Args:
        limit: The ceiling that was passed; ``EVIDENCE_MAX_PIXELS`` by default.

    Returns:
        The rider-facing message, with the ceiling in megapixels.

    """
    megapixels = (EVIDENCE_MAX_PIXELS if limit is None else limit) / 1_000_000
    return (
        f"That photo has too many pixels to process (the limit is {megapixels:.0f} megapixels). "
        "Please resize it and try again."
    )


def _check_pixels(width: int, height: int, limit: int | None = None) -> None:
    """Refuse a picture too big to decode safely inside a web request.

    Args:
        width: Width in pixels.
        height: Height in pixels.
        limit: The ceiling; ``EVIDENCE_MAX_PIXELS`` by default.

    Raises:
        PhotoRejectedError: Over the ceiling.

    """
    ceiling = EVIDENCE_MAX_PIXELS if limit is None else limit
    if width * height > ceiling:
        raise PhotoRejectedError("too_many_pixels", _too_many_pixels_message(ceiling))


def _flatten_for_jpeg(image: Image.Image) -> Image.Image:
    """Return something JPEG can store: transparency laid on white, other modes as RGB.

    The photo itself is returned, not a copy, when it needs nothing -- a copy would double the
    memory a large photo takes.

    Args:
        image: The decoded photo.

    Returns:
        An RGB, L or CMYK image.

    """
    if image.has_transparency_data:
        # Converting to RGBA also undoes premultiplied alpha ("RGBa"), which a plain RGB
        # conversion would leave as black.
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba)  # an RGBA mask is read from its alpha band
        return background
    if image.mode in {"RGB", "L", "CMYK"}:
        image.load()
        return image
    return image.convert("RGB")


def _png_colour_chunks(info: dict) -> PngImagePlugin.PngInfo | None:
    """Carry a PNG's gamma, chromaticity and sRGB chunks, which Pillow only writes when asked.

    Browsers apply them to a PNG without an ICC profile, so dropping them changes how it looks.

    Args:
        info: The source PNG's ``info``.

    Returns:
        The chunks to write, or None when there are none.

    """
    chunks = PngImagePlugin.PngInfo()
    if "srgb" in info:
        chunks.add(b"sRGB", bytes([int(info["srgb"])]))
    if "gamma" in info:
        chunks.add(b"gAMA", struct.pack(">I", round(info["gamma"] * 100_000)))
    if "chromaticity" in info:
        chunks.add(b"cHRM", struct.pack(">8I", *(round(value * 100_000) for value in info["chromaticity"])))
    return chunks if chunks.chunks else None


def _high_bit_grey_to_l(image: Image.Image, bit_depth: int) -> Image.Image:
    """Bring 10- or 12-bit greyscale down to 8 bits, white to white.

    pi-heif converts high-bit colour to 8 bits but hands greyscale over at 16, each sample
    shifted up (10-bit white is 1023 << 6). A plain 8-bit conversion clips all of it to white.
    The scaling is done in 16-bit mode, which Pillow can, so no 32-bit copy is made.

    Args:
        image: A 16-bit greyscale picture from pi-heif.
        bit_depth: The file's own bit depth.

    Returns:
        The picture in mode L.

    """
    top = ((1 << bit_depth) - 1) << (16 - bit_depth) if 8 < bit_depth < 16 else 65535
    scale = 255 / top
    return image.point(lambda value: value * scale + 0.5).convert("L")


def _rebuild_heif(data: bytes) -> tuple[_Rebuilt, bool]:
    """Decode a HEIC/HEIF photo and write it out as a JPEG.

    Args:
        data: The whole upload.

    Returns:
        The JPEG, and whether the original carried a GPS block.

    """
    heif = pi_heif.open_heif(io.BytesIO(data))
    # The mode is read from the header, before anything is decoded.
    _check_pixels(*heif.size, limit=HEIF_MAX_PIXELS if heif.mode in {"RGB", "L"} else COSTLY_DECODE_MAX_PIXELS)
    info = dict(heif.info)
    # Not to_pillow(), which copies the decoded picture twice. Pillow maps some modes (RGBA, L,
    # 16-bit grey) straight onto pi-heif's buffer and copies the rest, RGB among them. pi-heif's
    # buffer is a memoryview with no owner, freed with `heif` itself: so `heif` is let go only
    # when Pillow copied, and otherwise lives until the JPEG is written. libheif has already
    # applied the file's rotation and mirroring, so the picture is upright and Orientation is not
    # kept (the EXIF still carries it).
    image = Image.frombuffer(heif.mode, heif.size, heif.data, "raw", heif.mode, heif.stride, 1)
    if not image.readonly:
        del heif
    image.info = info
    exif = _kept_exif(image, keep_orientation=False)
    icc = _clean_icc(info.get("icc_profile"))
    had_gps = _had_gps(image)
    if image.mode.startswith("I;16"):
        image = _high_bit_grey_to_l(image, int(info.get("bit_depth") or 16))
    elif image.mode == "RGBa":
        image = image.convert("RGBA")  # undoes the premultiplication; the RGBa copy is let go
    pixels = _flatten_for_jpeg(image)
    del image
    pixels.info.clear()
    out = io.BytesIO()
    pixels.save(out, "JPEG", quality=HEIC_JPEG_QUALITY, exif=exif or b"", icc_profile=icc)
    return _Rebuilt(out.getvalue(), "JPEG"), had_gps


def _rebuild_jpeg(image: JpegImagePlugin.JpegImageFile) -> _Rebuilt:
    """Write a JPEG out again with its own quantisation tables and chroma subsampling.

    Only the primary picture is kept: an MPO's second image (an iPhone's HDR gain map, a
    stereo pair) goes, along with any metadata it carried.

    Args:
        image: The opened JPEG or MPO, on its first frame.

    Returns:
        The rewritten JPEG.

    """
    exif, icc = _kept_exif(image), _clean_icc(image.info.get("icc_profile"))
    params: dict = {"exif": exif or b"", "icc_profile": icc}
    tables = getattr(image, "quantization", None)
    if tables:
        params["qtables"] = tables
        sampling = JpegImagePlugin.get_sampling(image)
        if sampling != -1:
            params["subsampling"] = sampling
    else:
        params["quality"] = 95
    pixels = _flatten_for_jpeg(image)
    pixels.info.clear()  # read from above; Pillow would otherwise write the comment back
    out = io.BytesIO()
    pixels.save(out, "JPEG", **params)
    return _Rebuilt(out.getvalue(), "JPEG")


def _rebuild_png(image: Image.Image) -> _Rebuilt:
    """Write a still PNG out again, losslessly, with only the kept metadata.

    Args:
        image: The opened PNG.

    Returns:
        The rewritten PNG.

    """
    exif, icc = _kept_exif(image), _clean_icc(image.info.get("icc_profile"))
    params: dict = {"icc_profile": icc}
    if exif:
        params["exif"] = exif
    # Part of how a palette image looks, not metadata, so it has to come across.
    if "transparency" in image.info:
        params["transparency"] = image.info["transparency"]
    if not icc and (colour := _png_colour_chunks(image.info)):
        params["pnginfo"] = colour
    image.load()
    image.info.clear()
    out = io.BytesIO()
    image.save(out, "PNG", **params)
    return _Rebuilt(out.getvalue(), "PNG")


def _gif_canvas_sizes(data: bytes) -> list[tuple[int, int]]:
    """Read, without decoding anything, how big Pillow's canvas will be for each GIF frame.

    Pillow grows the canvas when a frame is placed past its edge, and allocates for it while
    seeking, before a caller can look at the frame -- so a 100-byte GIF can ask for gigabytes.
    Walking the block structure first lets the limits refuse such a file before Pillow opens it.
    The walk follows Pillow's own reader: unknown bytes are skipped one at a time, and it stops
    at the trailer or the end of the data. It stops early once the frame limit is passed.

    Args:
        data: The whole GIF.

    Returns:
        The canvas size after each frame, in order.

    Raises:
        PhotoRejectedError: More frames than ``MAX_ANIMATION_FRAMES``.

    """
    width, height = struct.unpack_from("<HH", data, 6)
    position = 13
    if data[10] & 0x80:
        position += 3 << ((data[10] & 7) + 1)
    sizes: list[tuple[int, int]] = []

    def skip_sub_blocks(at: int) -> int:
        while at < len(data) and data[at]:
            at += data[at] + 1
        return at + 1

    while position < len(data):
        block = data[position]
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension: label, then data sub-blocks
            position = skip_sub_blocks(position + 2)
        elif block == 0x2C:  # image descriptor
            if position + 10 > len(data):
                break
            x, y, frame_width, frame_height, flags = struct.unpack_from("<HHHHB", data, position + 1)
            width, height = max(width, x + frame_width), max(height, y + frame_height)
            sizes.append((width, height))
            if len(sizes) > MAX_ANIMATION_FRAMES:
                raise PhotoRejectedError("too_many_frames", TOO_MANY_FRAMES_MESSAGE)
            position += 10
            if flags & 0x80:
                position += 3 << ((flags & 7) + 1)
            position = skip_sub_blocks(position + 1)  # past the LZW code size, then the pixels
        else:
            position += 1
    return sizes


def _check_animation_budget(sizes: list[tuple[int, int]]) -> None:
    """Refuse an animation whose frames, all decoded, would pass the budget.

    Args:
        sizes: Each frame's canvas size.

    Raises:
        PhotoRejectedError: Too many frames, or too many pixels across them.

    """
    if len(sizes) > MAX_ANIMATION_FRAMES:
        raise PhotoRejectedError("too_many_frames", TOO_MANY_FRAMES_MESSAGE)
    if sum(width * height for width, height in sizes) > MAX_ANIMATION_PIXELS:
        raise PhotoRejectedError("too_many_pixels", _too_many_pixels_message(MAX_ANIMATION_PIXELS))


def _animation_frames(image: Image.Image, screen: tuple[int, int]) -> tuple[list[Image.Image], list[int]]:
    """Decode an animated GIF or PNG frame by frame, as a browser shows it, within the limits.

    Each frame is checked again as it comes, before it is decoded, and cut to the file's own
    screen size: browsers clip a frame drawn past the edge, where Pillow grows the canvas.

    Args:
        image: The opened GIF or PNG.
        screen: The size the file declares for itself.

    Returns:
        The frames as RGBA, all ``screen`` sized, and each one's delay in whole milliseconds.

    Raises:
        PhotoRejectedError: Too many frames, or too many pixels across them.

    """
    # An APNG may carry a fallback picture outside the animation, which browsers never show.
    skip_fallback = bool(image.info.get("default_image"))
    frames: list[Image.Image] = []
    durations: list[int] = []
    pixels = 0
    for index, frame in enumerate(ImageSequence.Iterator(image)):
        if index == 0 and skip_fallback:
            continue
        if len(frames) >= MAX_ANIMATION_FRAMES:
            raise PhotoRejectedError("too_many_frames", TOO_MANY_FRAMES_MESSAGE)
        pixels += frame.width * frame.height
        if pixels > MAX_ANIMATION_PIXELS:
            raise PhotoRejectedError("too_many_pixels", _too_many_pixels_message(MAX_ANIMATION_PIXELS))
        durations.append(round(frame.info.get("duration", 0)))
        rgba = frame.convert("RGBA")
        if rgba.size != screen:
            rgba = rgba.crop((0, 0, *screen))
        rgba.info.clear()  # a GIF comment is re-written from info otherwise
        frames.append(rgba)
    return frames, durations


def _gif_disposals(frames: list[Image.Image]) -> list[int]:
    """Choose, frame by frame, whether a GIF frame is cleared before the next is drawn.

    Left in place (1), the next frame need only be written where it differs, which keeps a
    screen-recording GIF small. But a pixel the next frame leaves see-through would then show
    this frame through it, so a frame is cleared (2) when the next one turns any of its opaque
    pixels see-through. Deciding this for the whole file let one transparent pixel make every
    frame be written in full.

    Args:
        frames: The frames, as RGBA.

    Returns:
        One disposal method per frame.

    """
    alphas = [frame.getchannel("A") for frame in frames]
    disposals = [1] * len(frames)
    for index in range(len(frames) - 1):
        if ImageChops.subtract(alphas[index], alphas[index + 1]).getbbox():
            disposals[index] = 2
    return disposals


def _merge_repeats(frames: list[Image.Image], durations: list[int], longest: int) -> tuple[list, list[int]]:
    """Fold each frame that repeats the one before into it, adding up the delays.

    Pillow does the same while writing, but its sum can pass the longest delay the format
    stores, and a file it folds down to one frame no longer takes per-frame settings. Doing it
    here, with each delay capped, avoids both.

    Args:
        frames: The frames, as RGBA.
        durations: Each frame's delay in milliseconds.
        longest: The longest delay the output format can store.

    Returns:
        The frames and delays with repeats folded in.

    """
    kept_frames: list[Image.Image] = []
    kept_durations: list[int] = []
    for frame, delay in zip(frames, durations, strict=True):
        # alpha_only=False: for RGBA, getbbox() otherwise looks at transparency alone.
        if kept_frames and not ImageChops.difference(kept_frames[-1], frame).getbbox(alpha_only=False):
            kept_durations[-1] = min(kept_durations[-1] + delay, longest)
            continue
        kept_frames.append(frame)
        kept_durations.append(min(delay, longest))
    return kept_frames, kept_durations


def _rebuild_animation(image: Image.Image, output_format: str, screen: tuple[int, int]) -> _Rebuilt:
    """Write a GIF, or an animated PNG, out again frame by frame with its timing and looping.

    Args:
        image: The opened GIF or APNG.
        output_format: ``"GIF"`` or ``"PNG"``.
        screen: The size the file declares for itself.

    Returns:
        The rewritten file.

    """
    params: dict = {}
    if output_format == "PNG":
        exif, icc = _kept_exif(image), _clean_icc(image.info.get("icc_profile"))
        params["icc_profile"] = icc
        if exif:
            params["exif"] = exif
        if not icc and (colour := _png_colour_chunks(image.info)):
            params["pnginfo"] = colour
    loop = image.info.get("loop")
    longest = _LONGEST_FRAME_MS[output_format]
    frames, durations = _merge_repeats(*_animation_frames(image, screen), longest)
    disposals = _gif_disposals(frames) if output_format == "GIF" else None
    if len(frames) > 1:
        params.update(save_all=True, append_images=frames[1:])
        if loop is not None:
            params["loop"] = loop

    def write(delays: list[int], disposal: list[int] | int | None) -> _Rebuilt:
        if len(frames) > 1:
            params["duration"] = delays
        if disposal is not None:
            params["disposal"] = disposal if len(frames) > 1 else disposal[0]
        out = io.BytesIO()
        frames[0].save(out, output_format, **params)
        return _Rebuilt(out.getvalue(), output_format)

    try:
        return write(durations, disposals)
    except struct.error, ValueError, TypeError:
        # Pillow can still fold frames that differ only in colours its palette merges: the sum
        # may overflow (GIF's field; APNG's fraction), and a file folded to one frame rejects
        # per-frame settings. Delays short enough that no sum overflows, and one disposal for
        # the whole file, keep such an animation from being called damaged. A second failure
        # is a real one.
        capped = [min(delay, longest // len(frames)) for delay in durations]
        single = None if disposals is None else (2 if 2 in disposals else 1)
        return write(capped, single)


def _rebuild_unchecked(data: bytes) -> tuple[_Rebuilt, str, bool]:
    """Decode a photo by its content, whatever its name says, and rebuild it.

    Args:
        data: The whole upload.

    Returns:
        The rebuilt photo, the format it arrived in, and whether it carried a GPS block.

    """
    if pi_heif.is_supported(data):
        rebuilt, had_gps = _rebuild_heif(data)
        return rebuilt, "HEIF", had_gps
    screen = None
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 13:
        screen = struct.unpack_from("<HH", data, 6)
        _check_animation_budget(_gif_canvas_sizes(data))
    with Image.open(io.BytesIO(data), formats=_PILLOW_FORMATS) as image:
        _check_pixels(*image.size)
        source_format = image.format or ""
        if source_format in {"JPEG", "MPO"} and image.info.get("progressive"):
            _check_pixels(*image.size, limit=COSTLY_DECODE_MAX_PIXELS)
        had_gps = _had_gps(image)  # decodes the picture
        if source_format in {"JPEG", "MPO"}:
            image.seek(0)
            return _rebuild_jpeg(image), source_format, had_gps
        if source_format == "PNG":
            # n_frames is read from the APNG header, so this costs nothing. An APNG's frames
            # cannot leave its canvas, so its size is the budget's measure.
            frame_count = getattr(image, "n_frames", 1)
            if frame_count > 1:
                _check_animation_budget([image.size] * frame_count)
                return _rebuild_animation(image, "PNG", image.size), "APNG", had_gps
            return _rebuild_png(image), source_format, had_gps
        return _rebuild_animation(image, "GIF", screen or image.size), source_format, had_gps


def _rebuild(data: bytes) -> tuple[_Rebuilt, str, bool]:
    """Rebuild a photo, reporting Pillow's own size guard as the rider-facing refusal.

    Pillow raises above twice its ceiling, and warns (without stopping) between one and two
    times it; ``_check_pixels`` refuses everything above the lower ``EVIDENCE_MAX_PIXELS``. No
    warnings filter is set here: ``warnings.catch_warnings`` changes process-wide state, which
    concurrent requests would trample.

    Args:
        data: The whole upload.

    Returns:
        As ``_rebuild_unchecked``.

    Raises:
        PhotoRejectedError: For anything that is not a photo this module can read and store.

    """
    try:
        return _rebuild_unchecked(data)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise PhotoRejectedError("too_many_pixels", _too_many_pixels_message()) from exc


def _check_stored_size(size: int, max_bytes: int | None) -> None:
    """Hold the rebuilt file to the same ceiling as the upload.

    A rebuild is usually no bigger than the upload, but a converted HEIC or a re-encoded
    animation can be, and the limit is about what is stored as much as what is sent.

    Args:
        size: Rebuilt size in bytes.
        max_bytes: The upload ceiling, or None for no check.

    Raises:
        PhotoRejectedError: Over ``max_bytes``.

    """
    if max_bytes is not None and size > max_bytes:
        raise PhotoRejectedError(
            "too_large_after_rebuild",
            f"That photo is {size / 1024 / 1024:.0f} MB once prepared for reviewers, and the limit is "
            f"{max_bytes / 1024 / 1024:.0f} MB. Please resize it and try again.",
        )


def prepare_photo(upload: UploadedFile, *, max_bytes: int | None = None) -> UploadedFile:
    """Turn an uploaded photo into the file that is stored: HEIC as JPEG, location removed.

    The format comes from the content, not the name, so a PNG called ``.jpg`` is stored as a
    PNG and a file that only claims to be a photo is refused.

    Args:
        upload: The rider's upload, already past the size and extension checks.
        max_bytes: The most the stored file may be -- the caller's upload limit, read by the
            caller so that a settings failure is not reported to the rider as a bad photo.

    Returns:
        A new upload object named for its real format, with a matching Content-Type.

    Raises:
        PhotoRejectedError: With a rider-facing message, for a photo that cannot be stored.

    """
    started = time.monotonic()
    source_extension = extension(upload.name)
    upload.seek(0)
    data = upload.read()
    try:
        rebuilt, source_format, had_gps = _rebuild(data)
        _check_stored_size(len(rebuilt.data), max_bytes)
    except PhotoRejectedError as exc:
        logfire.warning(
            "Verification photo refused",
            reason=exc.reason,
            source_extension=source_extension,
            size_bytes=len(data),
        )
        raise
    except Exception as exc:  # decoders raise OSError, ValueError, SyntaxError and more
        logfire.warning(
            "Verification photo refused",
            reason="unreadable",
            source_extension=source_extension,
            size_bytes=len(data),
            error_type=type(exc).__name__,
        )
        raise PhotoRejectedError("unreadable", UNREADABLE_PHOTO_MESSAGE) from exc

    stem = PurePath(upload.name).stem or "photo"
    output_extension = _OUTPUT_EXTENSIONS[rebuilt.output_format]
    if rebuilt.output_format == "JPEG" and source_extension in {".jpg", ".jpeg"}:
        output_extension = source_extension  # keep the rider's own spelling
    logfire.info(
        "Verification photo prepared",
        source_format=source_format,
        source_extension=source_extension,
        output_format=rebuilt.output_format,
        size_bytes_in=len(data),
        size_bytes_out=len(rebuilt.data),
        had_gps=had_gps,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return SimpleUploadedFile(
        f"{stem}{output_extension}",
        rebuilt.data,
        content_type=_CONTENT_TYPES[rebuilt.output_format],
    )
