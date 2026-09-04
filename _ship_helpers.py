"""Small helper used by build_and_release.bat.

Version numbers and CHANGELOG.md edits involve string/regex work that
batch is genuinely bad and error-prone at (multi-line strings, %
escaping, quoting) -- so that logic lives here in real Python instead,
and build_and_release.bat just calls this with simple one-line
commands and reads back a single line of output. Not meant to be run
by a person directly, though nothing stops you.

Enforces "standardized" MAJOR.MINOR.PATCH version numbers (no more
stray four-part tags like v0.1.1.2) -- bump() always produces exactly
three numbers, and `validate` rejects anything else.
"""
from __future__ import annotations

import argparse
import re
import sys
import time

INIT_PATH = "chatbot/__init__.py"
CHANGELOG_PATH = "CHANGELOG.md"
VERSION_RE = re.compile(r'__version__ = "([^"]+)"')
VERSION_FORMAT_RE = re.compile(r"^\d+\.\d+\.\d+$")


def current_version() -> str:
    text = open(INIT_PATH, encoding="utf-8").read()
    m = VERSION_RE.search(text)
    if not m:
        raise SystemExit(f"Couldn't find __version__ in {INIT_PATH}")
    return m.group(1)


def bump(version: str, kind: str) -> str:
    parts = [int(p) for p in version.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    elif kind == "patch":
        patch += 1
    else:
        raise SystemExit(f"Unknown bump kind: {kind}")
    return f"{major}.{minor}.{patch}"


def write_version(new_version: str) -> None:
    text = open(INIT_PATH, encoding="utf-8").read()
    new_text, count = VERSION_RE.subn(f'__version__ = "{new_version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"Couldn't find __version__ to replace in {INIT_PATH}")
    open(INIT_PATH, "w", encoding="utf-8").write(new_text)


def update_changelog(new_version: str) -> None:
    """Turns the "## [Unreleased]" section into a dated
    "## [new_version] - YYYY-MM-DD" section, leaving a fresh empty
    [Unreleased] above it for next time -- standard Keep a Changelog
    practice. If there's no [Unreleased] heading (shouldn't happen
    given CHANGELOG.md's own template, but don't crash a release over
    it), just prepends a new dated section at the top instead."""
    text = open(CHANGELOG_PATH, encoding="utf-8").read()
    today = time.strftime("%Y-%m-%d")
    marker = "## [Unreleased]"
    idx = text.find(marker)
    if idx == -1:
        header_end = text.find("\n\n")
        insert_at = header_end + 2 if header_end != -1 else len(text)
        new_text = text[:insert_at] + f"## [{new_version}] - {today}\n\n" + text[insert_at:]
    else:
        insert_at = idx + len(marker)
        new_text = text[:insert_at] + f"\n\n## [{new_version}] - {today}" + text[insert_at:]
    open(CHANGELOG_PATH, "w", encoding="utf-8").write(new_text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("current", help="Print the current __version__.")

    p_next = sub.add_parser("next", help="Print what a bump would produce, without changing anything.")
    p_next.add_argument("kind", choices=["major", "minor", "patch"])

    p_validate = sub.add_parser("validate", help="Exit non-zero with a message if VERSION isn't MAJOR.MINOR.PATCH.")
    p_validate.add_argument("version")

    p_apply = sub.add_parser("apply", help="Write VERSION into __init__.py and roll CHANGELOG.md's Unreleased section.")
    p_apply.add_argument("version")

    args = parser.parse_args()

    if args.cmd == "current":
        print(current_version())
    elif args.cmd == "next":
        print(bump(current_version(), args.kind))
    elif args.cmd == "validate":
        if not VERSION_FORMAT_RE.match(args.version):
            print(f"'{args.version}' isn't in MAJOR.MINOR.PATCH form (e.g. 1.2.0)", file=sys.stderr)
            sys.exit(1)
    elif args.cmd == "apply":
        if not VERSION_FORMAT_RE.match(args.version):
            print(f"'{args.version}' isn't in MAJOR.MINOR.PATCH form (e.g. 1.2.0)", file=sys.stderr)
            sys.exit(1)
        write_version(args.version)
        update_changelog(args.version)
        print(f"Applied v{args.version} to {INIT_PATH} and {CHANGELOG_PATH}")


if __name__ == "__main__":
    main()
