#!/usr/bin/env python3
"""Copy all Render media to this computer without writing to the live service.

Use the SSH user@host from Render's Connect menu, pinned to a running instance.
Run on a trusted computer with encrypted storage and an already verified SSH host
key. Repeat for every running instance. Do not use an ephemeral shell or job.

    python3 -B scripts/backup_render_media.py \
        --ssh-target 'INSTANCE_USER@ssh.REGION.render.com' \
        --destination '/secure/local/path/tambul-media-instance-1'

The destination must not exist. A backup is confirmed only when this program
prints VERIFIED and exits zero. Failed/partial copies remain for investigation.
The source is never modified, and no Django/database command is executed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile


# Passed on SSH stdin; every operation on Render reads the current filesystem.
REMOTE_SCRIPT = r'''
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tarfile

root = Path('/opt/render/project/src/media')
if root.is_symlink() or not root.is_dir():
    raise RuntimeError('Media root is absent or a symlink; investigate before backing up.')

if MODE == 'archive':
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|', dereference=False) as archive:
        archive.add(root, arcname='media')
else:
    files = {}
    directories = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        names.sort()
        directories.append(Path(base).relative_to(root).as_posix())
        for name in dirs + names:
            path = Path(base) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError('Symlink requires separate review: ' + str(path))
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError('Special file requires separate review: ' + str(path))
            digest = hashlib.sha256()
            with path.open('rb') as source:
                opened = os.fstat(source.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise RuntimeError('File replaced during inventory: ' + str(path))
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
                after = os.fstat(source.fileno())
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if signature(info) != signature(after) or signature(after) != signature(path.stat()):
                raise RuntimeError('File changed during inventory: ' + str(path))
            files[path.relative_to(root).as_posix()] = {
                'size': info.st_size,
                'sha256': digest.hexdigest(),
                'mtime_ns': info.st_mtime_ns,
            }
    print(json.dumps({
        'root': str(root),
        'hostname': socket.gethostname(),
        'instance': os.environ.get('RENDER_INSTANCE_ID', ''),
        'commit': os.environ.get('RENDER_GIT_COMMIT', ''),
        'directories': sorted(directories),
        'files': files,
    }, sort_keys=True))
'''


def remote_copy(target, mode, destination):
    with destination.open("xb") as output:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", target, "python -B -"],
            input=("MODE = " + repr(mode) + "\n" + REMOTE_SCRIPT).encode(),
            stdout=output,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(f"SSH {mode} failed (exit {result.returncode}); copy is unverified.")


def verify_archive(archive_path, inventory):
    """Verify bytes without extracting the archive or following filesystem links."""
    expected = {"media/" + name: info for name, info in inventory["files"].items()}
    expected_dirs = {
        "media" if name == "." else "media/" + name
        for name in inventory["directories"]
    }
    seen = set()
    seen_dirs = set()
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive:
            name = member.name.rstrip("/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Unsafe archive path: " + name)
            if member.isdir():
                if name not in expected_dirs:
                    raise RuntimeError("Unexpected archive directory: " + name)
                seen_dirs.add(name)
                continue
            if not (member.isfile() or member.islnk()) or name not in expected:
                raise RuntimeError("Unexpected archive entry or link: " + name)
            if name in seen:
                raise RuntimeError("Duplicate archive entry: " + name)
            if member.islnk() and member.linkname not in expected:
                raise RuntimeError("Unverified hardlink target: " + name)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("Unreadable archive entry: " + name)
            digest = hashlib.sha256()
            size = 0
            with source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if size != expected[name]["size"] or digest.hexdigest() != expected[name]["sha256"]:
                raise RuntimeError("Archive checksum mismatch: " + name)
            seen.add(name)
    if seen != set(expected) or seen_dirs != expected_dirs:
        raise RuntimeError("Archive is incomplete; files or directories are missing.")


def archive_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup(directory):
    before = json.loads((directory / "inventory-before.json").read_text())
    after = json.loads((directory / "inventory-after.json").read_text())
    receipt = json.loads((directory / "verified.json").read_text())
    if before != after or receipt.get("status") != "VERIFIED":
        raise RuntimeError("Inventories or verification receipt are inconsistent.")
    verify_archive(directory / "media.tar", before)
    if archive_digest(directory / "media.tar") != receipt["archive_sha256"]:
        raise RuntimeError("Archive no longer matches its verification receipt.")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target")
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--verify-existing", action="store_true", help="Read and verify an existing local backup without SSH or file writes.")
    args = parser.parse_args()
    if args.verify_existing:
        try:
            print(json.dumps(verify_backup(args.destination), indent=2))
            return 0
        except Exception as error:
            print(f"UNVERIFIED: {error}", file=sys.stderr)
            return 1
    if not args.ssh_target:
        parser.error("--ssh-target is required when creating a backup.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", args.ssh_target):
        parser.error("Use only the SSH user@host from Render's Connect menu.")
    os.umask(0o077)
    args.destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    before_path = args.destination / "inventory-before.json"
    after_path = args.destination / "inventory-after.json"
    archive_path = args.destination / "media.tar"
    try:
        remote_copy(args.ssh_target, "inventory", before_path)
        remote_copy(args.ssh_target, "archive", archive_path)
        remote_copy(args.ssh_target, "inventory", after_path)
        before = json.loads(before_path.read_text())
        after = json.loads(after_path.read_text())
        if before != after:
            raise RuntimeError("Source/instance changed during backup; retain this copy and repeat to a NEW destination.")
        verify_archive(archive_path, before)
        receipt = {
            "status": "VERIFIED",
            "files": len(before["files"]),
            "bytes": sum(info["size"] for info in before["files"].values()),
            "archive_sha256": archive_digest(archive_path),
            "instance": before["instance"],
            "hostname": before["hostname"],
            "commit": before["commit"],
        }
        with (args.destination / "verified.json").open("x") as output:
            json.dump(receipt, output, indent=2)
            output.write("\n")
        print(json.dumps(receipt, indent=2))
    except Exception as error:
        print(f"UNVERIFIED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
