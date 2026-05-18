# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Refresh the profile readme with newest jwmoss repos and OSS contributions.

Additive only: never edits existing bullets. Inserts a generic 🆕 line for
each new repo or external repo I have merged PRs in, so the diff is a small
nudge to come back and hand-curate the emoji and description.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

USERNAME = "jwmoss"
README = Path(__file__).resolve().parent.parent / "readme.md"
PROJECTS_HEADER = "Some of my projects and repos, newest first:"
OSS_HEADER = "Open source I'm contributing to:"
EMOJI = "🆕"
GITHUB_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "45"))
MAX_PRS_PER_REPO = 5


def gh(path: str) -> dict | list:
    """Call gh api and return parsed JSON."""
    result = subprocess.run(
        ["gh", "api", path],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def existing_repos(text: str) -> set[str]:
    """Return set of 'owner/repo' strings already referenced in the readme."""
    found: set[str] = set()
    for match in GITHUB_URL_RE.finditer(text):
        owner = match.group(1)
        repo = match.group(2).rstrip(").,;:")
        if not owner or not repo:
            continue
        found.add(f"{owner}/{repo}")
    return found


def fetch_new_personal_repos(seen: set[str], cutoff: datetime) -> list[dict]:
    """Return public jwmoss repos created since cutoff that aren't already listed."""
    repos = gh(f"users/{USERNAME}/repos?sort=created&direction=desc&per_page=30")
    assert isinstance(repos, list)
    new: list[dict] = []
    for repo in repos:
        if repo["fork"] or repo["archived"] or repo["private"]:
            continue
        if not repo.get("description"):
            continue
        if repo["full_name"] in seen:
            continue
        created = datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00"))
        if created < cutoff:
            continue
        new.append(
            {
                "full_name": repo["full_name"],
                "name": repo["name"],
                "description": repo["description"],
            }
        )
    return new


def fetch_new_oss_repos(seen: set[str], cutoff: datetime) -> list[dict]:
    """Return external repos with merged PRs by USERNAME since cutoff that aren't listed."""
    since = cutoff.date().isoformat()
    query = f"is:pr author:{USERNAME} is:merged -user:{USERNAME} created:>={since}"
    data = gh(f"search/issues?q={quote(query)}&sort=created&order=desc&per_page=50")
    assert isinstance(data, dict)

    by_repo: dict[str, list[dict]] = {}
    pr_re = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
    for item in data.get("items", []):
        match = pr_re.match(item["html_url"])
        if not match:
            continue
        full = f"{match.group(1)}/{match.group(2)}"
        if full in seen:
            continue
        by_repo.setdefault(full, []).append(
            {"number": int(match.group(3)), "url": item["html_url"]}
        )

    out: list[dict] = []
    for full, prs in by_repo.items():
        meta = gh(f"repos/{full}")
        assert isinstance(meta, dict)
        recent = sorted(prs, key=lambda p: p["number"], reverse=True)[:MAX_PRS_PER_REPO]
        out.append(
            {
                "full_name": full,
                "description": meta.get("description") or "",
                "prs": sorted(recent, key=lambda p: p["number"]),
            }
        )
    return out


def render_personal_bullet(repo: dict) -> str:
    description = repo["description"].rstrip(".")
    return f"- {EMOJI} [{repo['name']}](https://github.com/{repo['full_name']}) - {description}."


def render_oss_bullet(repo: dict) -> str:
    pr_refs = ", ".join(f"[#{pr['number']}]({pr['url']})" for pr in repo["prs"])
    description = repo["description"].rstrip(".") or repo["full_name"]
    return (
        f"- {EMOJI} [{repo['full_name']}](https://github.com/{repo['full_name']}) - "
        f"{description} ({pr_refs})."
    )


def insert_after_header(text: str, header: str, bullets: list[str]) -> str:
    if not bullets:
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        insert_at = index + 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        for offset, bullet in enumerate(bullets):
            lines.insert(insert_at + offset, bullet)
        suffix = "\n" if text.endswith("\n") else ""
        return "\n".join(lines) + suffix
    raise SystemExit(f"header not found in readme: {header!r}")


def main() -> int:
    text = README.read_text()
    seen = existing_repos(text)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    new_personal = fetch_new_personal_repos(seen, cutoff)
    new_oss = fetch_new_oss_repos(seen, cutoff)

    if not new_personal and not new_oss:
        print("nothing to add")
        return 0

    for repo in new_personal:
        print(f"+ personal: {repo['full_name']}")
    for repo in new_oss:
        print(f"+ oss: {repo['full_name']} ({len(repo['prs'])} merged PRs)")

    personal_bullets = [render_personal_bullet(repo) for repo in new_personal]
    oss_bullets = [render_oss_bullet(repo) for repo in new_oss]

    text = insert_after_header(text, OSS_HEADER, oss_bullets)
    text = insert_after_header(text, PROJECTS_HEADER, personal_bullets)
    README.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
