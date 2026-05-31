# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Refresh the profile readme with newest jwmoss repos and OSS contributions.

Existing bullets are preserved as-is (hand-curated emoji and descriptions).
New repos and external repos with merged PRs get a generic 🆕 line appended.
The personal projects section is re-sorted by each repo's last push, so the
repos I most recently worked on float to the top.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

USERNAME = "jwmoss"
README = Path(__file__).resolve().parent.parent / "readme.md"
PROJECTS_HEADER = "Some of my projects and repos, most recently updated first:"
OSS_HEADER = "Open source I'm contributing to:"
EMOJI = "🆕"
LOOKBACK_DAYS = 45
MAX_PRS_PER_REPO = 5
GITHUB_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")


def gh(path: str) -> dict | list:
    """Call gh api and return parsed JSON."""
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


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
    repos = gh(f"users/{USERNAME}/repos?sort=created&direction=desc&per_page=30")
    assert isinstance(repos, list)
    bullets = []
    for r in repos:
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if r["fork"] or r["archived"] or r["private"] or not r.get("description"):
            continue
        if r["full_name"] in seen or created < cutoff:
            continue
        print(f"+ personal: {r['full_name']}")
        desc = r["description"].rstrip(".")
        bullets.append(f"- {EMOJI} [{r['name']}](https://github.com/{r['full_name']}) - {desc}.")
    return bullets


def new_oss_bullets(seen: set[str], cutoff: datetime) -> list[str]:
    """Bullets for external repos with merged PRs by USERNAME since cutoff."""
    since = cutoff.date().isoformat()
    query = f"is:pr author:{USERNAME} is:merged -user:{USERNAME} created:>={since}"
    data = gh(f"search/issues?q={quote(query)}&sort=created&order=desc&per_page=50")
    assert isinstance(data, dict)

    by_repo: dict[str, list[int]] = {}
    pr_re = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
    for item in data.get("items", []):
        match = pr_re.match(item["html_url"])
        if match and (full := f"{match.group(1)}/{match.group(2)}") not in seen:
            by_repo.setdefault(full, []).append(int(match.group(3)))

    bullets = []
    for full, numbers in by_repo.items():
        print(f"+ oss: {full} ({len(numbers)} merged PRs)")
        meta = gh(f"repos/{full}")
        assert isinstance(meta, dict)
        desc = (meta.get("description") or "").rstrip(".") or full
        refs = ", ".join(
            f"[#{n}](https://github.com/{full}/pull/{n})"
            for n in sorted(numbers, reverse=True)[:MAX_PRS_PER_REPO]
        )
        bullets.append(f"- {EMOJI} [{full}](https://github.com/{full}) - {desc} ({refs}).")
    return bullets


def main() -> int:
    text = README.read_text()
    seen = repos_in(text)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    oss = new_oss_bullets(seen, cutoff) + get_bullets(text, OSS_HEADER)
    text = set_bullets(text, OSS_HEADER, oss)

    personal = new_personal_bullets(seen, cutoff) + get_bullets(text, PROJECTS_HEADER)
    personal.sort(key=last_push, reverse=True)
    text = set_bullets(text, PROJECTS_HEADER, personal)

    README.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
