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
from dataclasses import dataclass
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
SEARCH_PAGE_LIMIT = 3
GITHUB_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")
PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
RECENT_PRS_PREFIX = " Recent merged PRs: "
CONVENTIONAL_TITLE_RE = re.compile(
    r"^(?:build|chore|ci|docs|feat|fix|perf|refactor|style|test)"
    r"(?:\([^)]+\))?:\s+",
    re.IGNORECASE,
)
LEADING_TITLE_VERB_RE = re.compile(
    r"^(?:add|document|fix|implement|introduce|support|update)\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    merged_at: datetime


def gh(path: str) -> dict | list:
    """Call gh api and return parsed JSON."""
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


def search_issue_items(query: str) -> list[dict]:
    """Return issue-search items for a query, newest updates first."""
    items: list[dict] = []
    for page in range(1, SEARCH_PAGE_LIMIT + 1):
        data = gh(
            "search/issues?"
            f"q={quote(query)}&sort=updated&order=desc&per_page=100&page={page}"
        )
        assert isinstance(data, dict)
        page_items = data.get("items", [])
        items.extend(page_items)
        if len(page_items) < 100:
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


def repo_from_bullet(bullet: str) -> str | None:
    match = GITHUB_URL_RE.search(bullet)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2).rstrip(').,;:')}"


def prs_in_bullet(bullet: str, repo: str) -> set[int]:
    numbers = set()
    for owner, name, number in PR_URL_RE.findall(bullet):
        if f"{owner}/{name}" == repo:
            numbers.add(int(number))
    return numbers


def pr_label(pr: PullRequest) -> str:
    title = CONVENTIONAL_TITLE_RE.sub("", pr.title).strip()
    title = LEADING_TITLE_VERB_RE.sub("", title).strip()
    title = title.rstrip(".")
    if not title:
        title = f"PR #{pr.number}"
    return title[0].upper() + title[1:]


def pr_ref(repo: str, number: int) -> str:
    return f"[#{number}](https://github.com/{repo}/pull/{number})"


def contribution_phrase(pr: PullRequest) -> str:
    return f"{pr_label(pr)} ({pr_ref(pr.repo, pr.number)})"


def append_contributions(bullet: str, additions: list[PullRequest]) -> str:
    if not additions:
        return bullet
    base = bullet.rstrip()
    if base.endswith("."):
        base = base[:-1]

    phrases = [contribution_phrase(pr) for pr in additions]
    if len(phrases) == 1:
        new_text = phrases[0]
    elif len(phrases) == 2:
        new_text = f"{phrases[0]} and {phrases[1]}"
    else:
        new_text = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"

    if RECENT_PRS_PREFIX in base:
        return f"{base}, and {new_text}."
    return f"{base}.{RECENT_PRS_PREFIX}{new_text}."


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


def merged_external_prs(cutoff: datetime) -> dict[str, list[PullRequest]]:
    """Merged external PRs by USERNAME since cutoff, grouped by repo."""
    since = cutoff.date().isoformat()
    query = f"is:pr author:{USERNAME} is:merged -user:{USERNAME} merged:>={since}"
    by_repo: dict[str, list[PullRequest]] = {}
    for item in search_issue_items(query):
        match = PR_URL_RE.match(item["html_url"])
        if not match:
            continue
        repo = f"{match.group(1)}/{match.group(2)}"
        merged_at = item.get("closed_at")
        if not merged_at:
            continue
        by_repo.setdefault(repo, []).append(
            PullRequest(
                repo=repo,
                number=int(match.group(3)),
                title=item["title"],
                merged_at=datetime.fromisoformat(merged_at.replace("Z", "+00:00")),
            )
        )

    for prs in by_repo.values():
        prs.sort(key=lambda pr: pr.merged_at, reverse=True)
    return by_repo


def refresh_oss_bullets(existing: list[str], prs_by_repo: dict[str, list[PullRequest]]) -> list[str]:
    """Add newly merged PRs to existing OSS bullets and create bullets for new repos."""
    bullets = existing[:]
    referenced = {repo for bullet in bullets if (repo := repo_from_bullet(bullet))}

    for i, bullet in enumerate(bullets):
        repo = repo_from_bullet(bullet)
        if not repo or repo not in prs_by_repo:
            continue
        existing_prs = prs_in_bullet(bullet, repo)
        additions = [
            pr
            for pr in prs_by_repo[repo]
            if pr.number not in existing_prs
        ][:MAX_PRS_PER_REPO]
        if additions:
            print(f"~ oss: {repo} (+{len(additions)} merged PRs)")
            bullets[i] = append_contributions(bullet, additions)

    for repo, prs in prs_by_repo.items():
        if repo in referenced:
            continue
        print(f"+ oss: {repo} ({len(prs)} merged PRs)")
        meta = gh(f"repos/{repo}")
        assert isinstance(meta, dict)
        desc = (meta.get("description") or "").rstrip(".") or repo
        refs = ", ".join(
            pr_ref(repo, pr.number)
            for pr in prs[:MAX_PRS_PER_REPO]
        )
        bullets.append(f"- {EMOJI} [{repo}](https://github.com/{repo}) - {desc} ({refs}).")

    return bullets


def main() -> int:
    text = README.read_text()
    seen = repos_in(text)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    oss = refresh_oss_bullets(get_bullets(text, OSS_HEADER), merged_external_prs(cutoff))
    text = set_bullets(text, OSS_HEADER, oss)

    personal = new_personal_bullets(seen, cutoff) + get_bullets(text, PROJECTS_HEADER)
    personal.sort(key=last_push, reverse=True)
    text = set_bullets(text, PROJECTS_HEADER, personal)

    README.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
