#!/usr/bin/env python3
"""Download the six New England Geofabrik PBFs, optionally build one .navmap."""

import argparse
from pathlib import Path
import subprocess
import sys
import urllib.request


BASE_URL = "https://download.geofabrik.de/north-america/us"
STATES = (
    "connecticut",
    "maine",
    "massachusetts",
    "new-hampshire",
    "rhode-island",
    "vermont",
)
USER_AGENT = "NAV-COM-2006 offline-map downloader"


def download(url, destination, force=False):
    if destination.exists() and not force:
        print(f"Exists, skipping: {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    if temp.exists():
        temp.unlink()

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    print(f"Downloading {destination.name} ...")

    with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        copied = 0
        next_report = 10

        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            copied += len(chunk)

            if total:
                percent = int(copied * 100 / total)
                if percent >= next_report:
                    print(f"  {percent}%")
                    next_report += 10

    temp.replace(destination)


def main():
    parser = argparse.ArgumentParser(
        description="Download New England OSM extracts for NAV-COM."
    )
    parser.add_argument(
        "--directory",
        default="downloads/new-england",
        help="Directory for downloaded .osm.pbf files.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build offline_maps/new-england.navmap after downloading.",
    )
    parser.add_argument(
        "--output",
        default="offline_maps/new-england.navmap",
        help="Output path used with --build.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download existing files and replace an existing navmap.",
    )
    args = parser.parse_args()

    directory = Path(args.directory)
    for state in STATES:
        filename = f"{state}-latest.osm.pbf"
        download(
            f"{BASE_URL}/{filename}",
            directory / filename,
            force=args.force,
        )

    if args.build:
        builder = Path(__file__).with_name("build_navmap.py")
        command = [
            sys.executable,
            str(builder),
            "--name",
            "New England",
            "--output",
            args.output,
        ]
        if args.force:
            command.append("--overwrite")
        command.append(str(directory))

        print()
        print("Building New England navmap ...")
        subprocess.check_call(command)


if __name__ == "__main__":
    main()
