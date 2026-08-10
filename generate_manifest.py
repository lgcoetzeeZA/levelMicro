#!/usr/bin/env python3
"""
Generates manifest.json for LevelUp OTA updates.

Usage:
    python3 generate_manifest.py --folder ./device_files --repo-base https://raw.githubusercontent.com/lgcoetzeeZA/levelup-firmware/main --version 1.0.1

This scans the given folder for .py files, computes a sha256 hash for
each, and writes a manifest.json ready to commit and push to your GitHub
repo alongside the files themselves. Devices will compare their currently
installed version against manifest["version"] and update if different.
"""
import argparse
import hashlib
import json
import os

EXCLUDE_FILES = {"generate_manifest.py"}


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Generate manifest.json for LevelUp OTA updates")
    parser.add_argument("--folder", required=True, help="Folder containing the .py files to release")
    parser.add_argument("--repo-base", required=True, help="Base raw GitHub URL, e.g. https://raw.githubusercontent.com/lgcoetzeeZA/levelup-firmware/main")
    parser.add_argument("--version", required=True, help="Version string for this release, e.g. 1.0.1")
    parser.add_argument("--output", default="manifest.json", help="Output path (default: manifest.json)")
    args = parser.parse_args()

    repo_base = args.repo_base.rstrip("/")
    files = {}

    for name in sorted(os.listdir(args.folder)):
        if not name.endswith(".py"):
            continue
        if name in EXCLUDE_FILES:
            continue

        full_path = os.path.join(args.folder, name)
        if not os.path.isfile(full_path):
            continue

        digest = sha256_of_file(full_path)
        files[name] = {
            "url": "{}/{}".format(repo_base, name),
            "sha256": digest,
        }
        print("  {} -> {}".format(name, digest))

    manifest = {
        "version": args.version,
        "files": files,
    }

    with open(args.output, "w") as f:
        json.dump(manifest, f, indent=2)

    print("\nWrote {} with {} file(s) at version {}".format(args.output, len(files), args.version))
    print("Next: commit and push both {} and the {} files to your GitHub repo.".format(args.output, len(files)))


if __name__ == "__main__":
    main()
