# Synthetic HEIC fixtures

Used by `apps/team/test_evidence_photos.py`. They are generated shapes, not photos of anyone:
a blue field with a red block in the top-left corner, so a test can tell which way up the
decoded picture is. The metadata is invented -- the GPS position is the Greenwich meridian.

The app reads HEIC with `pi-heif`, which cannot write it, so these are made once with
`pillow-heif` (which can) and committed. To regenerate:

```bash
uvx --python 3.14 --with pillow-heif --with pillow==12.3.0 python apps/team/test_data/evidence/make_fixtures.txt
```

(The script is a `.txt` so it is not collected, linted or imported as part of the app.)

| File | What it is |
|---|---|
| `tagged.heic` | 64x48, EXIF with camera, capture time, GPS and tags that must be dropped; Display-P3-like ICC profile; XMP naming a location |
| `rotated.heic` | 64x48 stored with EXIF Orientation 6, so pillow-heif writes a rotation into the file; decodes to 48x64 |
| `transparent.heic` | 40x30 RGBA, half-transparent green |
| `truncated.heic` | the first 200 bytes of `tagged.heic` |
| `premultiplied.heic` | 40x30, left half fully transparent, right half half-transparent green, stored with premultiplied alpha (pi-heif opens it as `RGBa`) |
| `grey10.heic` | 64x48 10-bit greyscale, grey 76 with a darker (30) top-left block; pi-heif hands it over as 16-bit |
| `display_profile.heic` | 64x48 whose ICC profile has an Apple `mmod` tag carrying `FIXTURE-DISPLAY-SERIAL`, as a Mac display profile carries its serial number |
