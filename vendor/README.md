# Instagrapi Pillow 13 compatibility build

The application pins `instagrapi` 3.0.16 and Pillow 13.0.0. Instagrapi's
published wheel still limits Pillow to `<13`; Pillow 12.3.0 has unfixed image
parser vulnerabilities. This local wheel changes that requirement to
`Pillow>=13.0.0,<14` without changing any application or Instagrapi Python code.
It is not an upstream release and must not be published as one.

`instagrapi-3.0.16-1pillow13-py3-none-any.whl` uses the wheel build tag
`1pillow13`. Its public distribution version remains 3.0.16, so vulnerability
checks still identify the actual upstream library code. Only `METADATA`,
`WHEEL` and their `RECORD` hashes differ. The original license is retained.

- Upstream artifact: <https://files.pythonhosted.org/packages/71/5d/6ecf90eaa58643f0e37a88601c4a3a55e096d9a30aa8eb286181aec1ad23/instagrapi-3.0.16-py3-none-any.whl>
- Upstream SHA256: `4115ff4b4b605c4faaa7f4fc19e70665b7e629fcf31e0eda60acb1230f65b8bd`
- Local wheel SHA256: `260d6c12ff163ffb9625c38aa4a5c22b5556463ba450d57b1fc34e5e2eed4190`

Regenerate and validate the checked-in artifact:

```sh
python3 scripts/vendor_instagram_client.py --output /tmp/instagrapi-rebuilt.whl
cmp vendor/instagrapi-3.0.16-1pillow13-py3-none-any.whl /tmp/instagrapi-rebuilt.whl
uv sync --locked
uv pip check
uv run --frozen pytest -q tests/test_pillow_security.py
```

The builder verifies the upstream checksum before opening its archive and
fails if the expected dependency metadata changes. The local UV source is
explicit in `pyproject.toml`; Docker copies the same artifact before its frozen
install. Do not replace this with a resolver override that leaves an
inconsistent installed dependency set.

Compatibility checks cover Instagrapi's JPEG preparation and story-fit APIs for
RGB, RGBA, palette and grayscale images. Security regressions cover GIF
rejection state and APNG allocation ordering, including both disposal aliases.
The full application suite also exercises its download/client integration.
The application itself streams downloaded images without decoding them in
Instagrapi's upload helpers.

When a compatible upstream release removes the Pillow upper bound, test that
release, remove this local source/artifact/builder, and regenerate `uv.lock`.
Until then, review upstream Instagrapi advisories using version 3.0.16 and keep
this compatibility build in its ordinary dependency audit.
