#!/usr/bin/env python3
"""Rebuild Instagrapi's pinned wheel with tested Pillow 13 metadata only."""

import argparse
import base64
import copy
import csv
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

UPSTREAM_URL = (
    "https://files.pythonhosted.org/packages/71/5d/"
    "6ecf90eaa58643f0e37a88601c4a3a55e096d9a30aa8eb286181aec1ad23/"
    "instagrapi-3.0.16-py3-none-any.whl"
)
UPSTREAM_SHA256 = "4115ff4b4b605c4faaa7f4fc19e70665b7e629fcf31e0eda60acb1230f65b8bd"
OUTPUT_NAME = "instagrapi-3.0.16-1pillow13-py3-none-any.whl"
DIST_INFO = "instagrapi-3.0.16.dist-info/"


def rebuild(source: bytes, output: Path) -> None:
    if hashlib.sha256(source).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Instagrapi upstream wheel SHA256 mismatch")
    with zipfile.ZipFile(io.BytesIO(source)) as upstream:
        members = upstream.infolist()
        contents = {member.filename: upstream.read(member) for member in members}
    metadata = DIST_INFO + "METADATA"
    wheel = DIST_INFO + "WHEEL"
    record = DIST_INFO + "RECORD"
    old_requirement = b"Requires-Dist: Pillow<13,>=12.2.0\n"
    if contents[metadata].count(old_requirement) != 1:
        raise ValueError("Unexpected upstream Pillow requirement")
    if b"Build:" in contents[wheel] or any(
        name.endswith(("/RECORD.jws", "/RECORD.p7s")) for name in contents
    ):
        raise ValueError("Unexpected upstream wheel build or signature metadata")
    contents[metadata] = contents[metadata].replace(
        old_requirement, b"Requires-Dist: Pillow<14,>=13.0.0\n"
    )
    contents[wheel] = contents[wheel].rstrip(b"\n") + b"\nBuild: 1pillow13\n"
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for member in members:
        name = member.filename
        if name == record:
            continue
        data = contents[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        writer.writerow((name, "sha256=" + digest.decode("ascii"), len(data)))
    writer.writerow((record, "", ""))
    contents[record] = rows.getvalue().encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as rebuilt:
        for member in members:
            info = copy.copy(member)
            info.compress_type = zipfile.ZIP_DEFLATED
            rebuilt.writestr(info, contents[member.filename], compresslevel=9)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-wheel", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "vendor" / OUTPUT_NAME,
    )
    args = parser.parse_args()
    if args.upstream_wheel:
        source = args.upstream_wheel.read_bytes()
    else:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=30) as response:
            source = response.read()
    rebuild(source, args.output)
    print(hashlib.sha256(args.output.read_bytes()).hexdigest(), args.output)


if __name__ == "__main__":
    main()
