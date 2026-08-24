# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Refresh the profile readme with the newest jwmoss repositories.

Existing bullets are preserved as-is (hand-curated emoji and descriptions).
New repositories get a generic 🆕 line. The projects are sorted by last push.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

USERNAME = "jwmoss"
README = Path(__file__).resolve().parent.parent / "readme.md"
PROJECTS_HEADER = "## Projects"
EMOJI = "🆕"
LOOKBACK_DAYS = 45
REPO_PAGE_LIMIT = 10
PER_PAGE = 100
EXCLUDED_REPOS = frozenset(
    {
        "536tech/terraform-provider-daytona",
        "jwmoss/terraform-provider-daytona",
    }
)
GITHUB_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")


def gh(path: str) -> dict | list:
    """Call gh api and return parsed JSON."""
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


def gh_pages(path: str, *, page_limit: int = REPO_PAGE_LIMIT) -> list[dict]:
    """Return REST items from a paged list endpoint."""
    items: list[dict] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, page_limit + 1):
        data = gh(f"{path}{separator}per_page={PER_PAGE}&page={page}")
        assert isinstance(data, list)
        items.extend(data)
        if len(data) < PER_PAGE:
            break
    return items


def find_section(lines: list[str], header: str) -> tuple[int, int]:
    """Return (start, end) line indices of the bullet block under header."""
    for i, line in enumerate(lines):
        if line.strip() != header:
            continue
        start = i + 1
        while start < len(lines) and not lines[start].strip():
            start += 1
        end = start
        while end < len(lines) and lines[end].startswith("- "):
            end += 1
        return start, end
    raise SystemExit(f"header not found in readme: {header!r}")


def get_bullets(text: str, header: str) -> list[str]:
    lines = text.splitlines()
    start, end = find_section(lines, header)
    return lines[start:end]


def set_bullets(text: str, header: str, bullets: list[str]) -> str:
    lines = text.splitlines()
    start, end = find_section(lines, header)
    lines[start:end] = bullets
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def repos_in(text: str) -> set[str]:
    """Return 'owner/repo' strings already referenced anywhere in the readme."""
    return {f"{o}/{r.rstrip(').,;:')}" for o, r in GITHUB_URL_RE.findall(text)}


def last_push(bullet: str) -> datetime:
    """Sort key: the repo's last-push time, or epoch if it can't be read."""
    floor = datetime.min.replace(tzinfo=timezone.utc)
    match = GITHUB_URL_RE.search(bullet)
    if not match:
        return floor
    full = f"{match.group(1)}/{match.group(2).rstrip(').,;:')}"
    try:
        meta = gh(f"repos/{full}")
    except subprocess.CalledProcessError:
        print(f"! could not fetch {full}, sinking to bottom")
        return floor
    assert isinstance(meta, dict)
    stamp = meta.get("pushed_at")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else floor


def new_personal_bullets(seen: set[str], cutoff: datetime) -> list[str]:
    """Bullets for public jwmoss repos created since cutoff and not yet listed."""
    bullets = []
    for r in gh_pages(f"users/{USERNAME}/repos?sort=created&direction=desc"):
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if created < cutoff:
            break
        if r["fork"] or r["archived"] or r["private"] or not r.get("description"):
            continue
        if r["full_name"] in seen or r["full_name"] in EXCLUDED_REPOS:
            continue
        print(f"+ personal: {r['full_name']}")
        desc = r["description"].rstrip(".")
        bullets.append(
            f"- {EMOJI} [{r['name']}](https://github.com/{r['full_name']}) - {desc}."
        )
    return bullets


def main() -> int:
    text = README.read_text()
    seen = repos_in(text)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    personal = new_personal_bullets(seen, cutoff) + get_bullets(text, PROJECTS_HEADER)
    personal.sort(key=last_push, reverse=True)
    text = set_bullets(text, PROJECTS_HEADER, personal)

    README.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
