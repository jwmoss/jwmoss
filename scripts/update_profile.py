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

USERNAME = "jwmoss"
README = Path(__file__).resolve().parent.parent / "readme.md"
PROJECTS_HEADER = "Some of my projects and repos, most recently updated first:"
OSS_HEADER = "Open source I'm contributing to:"
EMOJI = "🆕"
LOOKBACK_DAYS = 45
MAX_PRS_PER_REPO = 5
SEARCH_PAGE_LIMIT = 10
REPO_PAGE_LIMIT = 10
PER_PAGE = 100
EXCLUDED_REPOS = frozenset(
    {
        "536tech/terraform-provider-daytona",
        "jwmoss/terraform-provider-daytona",
    }
)
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
    repo_description: str | None = None


def gh(path: str) -> dict | list:
    """Call gh api and return parsed JSON."""
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


def gh_graphql(query: str, fields: dict[str, str]) -> dict:
    """Call gh graphql and return parsed JSON."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in fields.items():
        args.extend(["-f", f"{key}={value}"])
    out = subprocess.run(args, check=True, capture_output=True, text=True)
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


def search_merged_pr_nodes(query: str) -> list[dict]:
    """Return PullRequest GraphQL nodes for a GitHub search query."""
    graphql = """
    query($searchQuery: String!, $after: String) {
      search(query: $searchQuery, type: ISSUE, first: 100, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          ... on PullRequest {
            number
            title
            url
            mergedAt
            repository {
              nameWithOwner
              description
            }
          }
        }
      }
    }
    """
    nodes: list[dict] = []
    after = ""
    for _ in range(SEARCH_PAGE_LIMIT):
        fields = {"searchQuery": query}
        if after:
            fields["after"] = after
        data = gh_graphql(graphql, fields)
        search = data.get("data", {}).get("search", {})
        page_nodes = [node for node in search.get("nodes", []) if node]
        nodes.extend(page_nodes)
        page_info = search.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor") or ""
        if not after:
            break
    return nodes


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


def phrase_list(phrases: list[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"


def split_recent_clause(bullet: str) -> tuple[str, str]:
    base = bullet.rstrip()
    if base.endswith("."):
        base = base[:-1]
    if RECENT_PRS_PREFIX not in base:
        return base, ""
    base, recent = base.split(RECENT_PRS_PREFIX, 1)
    base = base.rstrip()
    if base.endswith("."):
        base = base[:-1]
    return base, recent.strip()


def refresh_recent_clause(bullet: str, recent_prs: list[PullRequest]) -> str:
    base, _ = split_recent_clause(bullet)
    if not recent_prs:
        return f"{base}."
    phrases = [contribution_phrase(pr) for pr in recent_prs]
    return f"{base}.{RECENT_PRS_PREFIX}{phrase_list(phrases)}."


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
        bullets.append(f"- {EMOJI} [{r['name']}](https://github.com/{r['full_name']}) - {desc}.")
    return bullets


def merged_external_prs(cutoff: datetime) -> dict[str, list[PullRequest]]:
    """Merged external PRs by USERNAME since cutoff, grouped by repo."""
    since = cutoff.date().isoformat()
    query = f"is:pr author:{USERNAME} is:merged -user:{USERNAME} merged:>={since}"
    by_repo: dict[str, list[PullRequest]] = {}
    for node in search_merged_pr_nodes(query):
        repo = node.get("repository", {}).get("nameWithOwner")
        merged_at = node.get("mergedAt")
        if not repo or not merged_at or repo in EXCLUDED_REPOS:
            continue
        description = node.get("repository", {}).get("description")
        by_repo.setdefault(repo, []).append(
            PullRequest(
                repo=repo,
                number=int(node["number"]),
                title=node["title"],
                merged_at=datetime.fromisoformat(merged_at.replace("Z", "+00:00")),
                repo_description=description,
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
        base, _ = split_recent_clause(bullet)
        base_prs = prs_in_bullet(base, repo)
        recent_prs = [
            pr
            for pr in prs_by_repo[repo]
            if pr.number not in base_prs
        ][:MAX_PRS_PER_REPO]
        refreshed = refresh_recent_clause(bullet, recent_prs)
        if refreshed != bullet:
            print(f"~ oss: {repo} ({len(recent_prs)} recent merged PRs)")
            bullets[i] = refreshed

    for repo, prs in prs_by_repo.items():
        if repo in referenced:
            continue
        print(f"+ oss: {repo} ({len(prs)} merged PRs)")
        desc = (prs[0].repo_description or "").rstrip(".") or repo
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
