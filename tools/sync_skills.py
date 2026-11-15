#!/usr/bin/env python3
"""Sync or check the canonical Agent Skill against the Claude Code plugin mirror.

    python3 tools/sync_skills.py          sync .agents/skills/lemo-opuscar -> plugin/skills/lemo-opuscar
    python3 tools/sync_skills.py --check  exit non-zero if the mirror differs, contains symlinks, or .sh uses CRLF

Real files (never symlinks) are written so standard Git clones and downloaded
archives work identically across operating systems and coding-agent hosts.
"""
import argparse
import os
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(ROOT, '.agents', 'skills', 'lemo-opuscar')
MIRROR = os.path.join(ROOT, 'plugin', 'skills', 'lemo-opuscar')


def _rel_files(base):
    """Return sorted relative POSIX paths under `base`, rejecting symlinks."""
    if not os.path.isdir(base):
        raise SystemExit(f'✗ missing skill directory: {os.path.relpath(base, ROOT)}')
    if os.path.islink(base):
        raise SystemExit(f'✗ symlink not allowed for skill root: {os.path.relpath(base, ROOT)}')
    files = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('__pycache__'))
        for d in dirnames:
            full_d = os.path.join(dirpath, d)
            if os.path.islink(full_d):
                raise SystemExit(f'✗ symlink not allowed: {os.path.relpath(full_d, ROOT)}')
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise SystemExit(f'✗ symlink not allowed: {os.path.relpath(full, ROOT)}')
            rel = os.path.relpath(full, base).replace(os.sep, '/')
            files[rel] = full
    return files


def _read_normalized(path, rel):
    """Read raw bytes; normalize shell scripts to LF so Windows hosts stay clean."""
    with open(path, 'rb') as f:
        data = f.read()
    if rel.endswith('.sh'):
        data = data.replace(b'\r\n', b'\n')
    return data


def sync_skills():
    """Copy canonical skill files cleanly into the plugin skill mirror."""
    src_files = _rel_files(CANONICAL)
    if os.path.islink(MIRROR):
        raise SystemExit(f'✗ symlink not allowed at mirror root: {os.path.relpath(MIRROR, ROOT)}')

    # Ensure canonical .sh scripts themselves use LF line endings before mirroring.
    for rel, src_path in src_files.items():
        if rel.endswith('.sh'):
            with open(src_path, 'rb') as f:
                raw = f.read()
            fixed = raw.replace(b'\r\n', b'\n')
            if fixed != raw:
                with open(src_path, 'wb') as f:
                    f.write(fixed)

    if os.path.exists(MIRROR):
        dst_files = _rel_files(MIRROR)
        for stale in set(dst_files) - set(src_files):
            os.remove(dst_files[stale])
        # Prune empty directories left in the mirror.
        for dirpath, dirnames, filenames in os.walk(MIRROR, topdown=False):
            if dirpath != MIRROR and not dirnames and not filenames:
                os.rmdir(dirpath)

    for rel, src_path in src_files.items():
        dst_path = os.path.join(MIRROR, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        data = _read_normalized(src_path, rel)
        with open(dst_path, 'wb') as f:
            f.write(data)
        if rel.endswith('.sh'):
            for p in (src_path, dst_path):
                mode = os.stat(p).st_mode
                os.chmod(p, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f'✓ synced {len(src_files)} file(s): .agents/skills/lemo-opuscar -> plugin/skills/lemo-opuscar')


def check_skills():
    """Verify canonical and mirror skill trees are in sync and free of symlinks/CRLF .sh."""
    issues = []
    src_files = _rel_files(CANONICAL)
    dst_files = _rel_files(MIRROR)

    missing = sorted(set(src_files) - set(dst_files))
    extra = sorted(set(dst_files) - set(src_files))
    for m in missing:
        issues.append(f'missing in plugin/skills/lemo-opuscar: {m}')
    for e in extra:
        issues.append(f'unexpected file in plugin/skills/lemo-opuscar: {e}')

    for rel in sorted(set(src_files) & set(dst_files)):
        with open(src_files[rel], 'rb') as fs, open(dst_files[rel], 'rb') as fd:
            sb, db = fs.read(), fd.read()
        if rel.endswith('.sh'):
            if b'\r\n' in sb:
                issues.append(f'CRLF line ending in canonical shell script: .agents/skills/lemo-opuscar/{rel}')
            if b'\r\n' in db:
                issues.append(f'CRLF line ending in mirror shell script: plugin/skills/lemo-opuscar/{rel}')
        if sb != db:
            issues.append(f'out of sync: {rel} differs between .agents/skills/lemo-opuscar and plugin/skills/lemo-opuscar')

    if issues:
        for msg in issues:
            print(f'✗ {msg}', file=sys.stderr)
        return 1
    print(f'✓ skill sync OK ({len(src_files)} file(s) match between .agents/skills/lemo-opuscar and plugin/skills/lemo-opuscar)')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Sync or verify .agents/skills/lemo-opuscar <-> plugin/skills/lemo-opuscar')
    ap.add_argument('--check', action='store_true', help='verify parity without writing files')
    args = ap.parse_args()
    if args.check:
        return check_skills()
    sync_skills()
    return check_skills()


if __name__ == '__main__':
    sys.exit(main())
