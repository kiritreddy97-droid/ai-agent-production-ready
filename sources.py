"""
sources.py — Collect your work from GitHub and Zenodo
======================================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

Turns public GitHub repositories and Zenodo publications into `WorkItem`s
that the LinkedIn campaign (campaign.py) writes posts about.

  GitHub : GET https://api.github.com/users/{user}/repos   (forks/archived skipped)
  Zenodo : GET https://zenodo.org/api/records?q=...        (by ORCID or author name)

Both APIs are public; GITHUB_TOKEN is optional and only raises the rate limit.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import requests

log = logging.getLogger("sources")

GITHUB_API = "https://api.github.com"
ZENODO_API = "https://zenodo.org/api/records"
_TIMEOUT = 15


@dataclass
class WorkItem:
    key: str                 # "github:owner/name" | "zenodo:<record id>"
    kind: str                # "github" | "zenodo"
    title: str
    url: str
    description: str
    date: str                # ISO date: last push (GitHub) / publication date (Zenodo)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _strip_html(text: str) -> str:
    text = re.sub(r"<\s*(br|/p|/li)\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


# ─── GitHub ───────────────────────────────────────────────────────────────────

def _gh_headers(token: str = "", raw: bool = False) -> dict:
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "ai-agent-production-ready"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_github(username: str, token: str = "", exclude: set[str] | None = None) -> list[WorkItem]:
    """Public, non-fork, non-archived repos of `username`, most recently pushed first."""
    exclude = {e.lower() for e in (exclude or set())}
    items: list[WorkItem] = []
    for page in range(1, 6):
        r = requests.get(f"{GITHUB_API}/users/{username}/repos",
                         params={"per_page": 100, "page": page, "type": "owner", "sort": "pushed"},
                         headers=_gh_headers(token), timeout=_TIMEOUT)
        r.raise_for_status()
        repos = r.json()
        for repo in repos:
            if repo.get("fork") or repo.get("archived") or repo.get("private"):
                continue
            if repo["name"].lower() in exclude:
                continue
            items.append(WorkItem(
                key=f"github:{repo['full_name']}",
                kind="github",
                title=repo["name"],
                url=repo["html_url"],
                description=repo.get("description") or "",
                date=(repo.get("pushed_at") or repo.get("updated_at") or "")[:19],
                details={
                    "full_name": repo["full_name"],
                    "language": repo.get("language"),
                    "topics": repo.get("topics") or [],
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "homepage": repo.get("homepage") or "",
                    "created_at": (repo.get("created_at") or "")[:10],
                },
            ))
        if len(repos) < 100:
            break
    return items


def github_readme(full_name: str, token: str = "", max_chars: int = 3500) -> str:
    try:
        r = requests.get(f"{GITHUB_API}/repos/{full_name}/readme",
                         headers=_gh_headers(token, raw=True), timeout=_TIMEOUT)
        if r.status_code != 200:
            return ""
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", r.text)          # drop images
        text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
        return text[:max_chars]
    except requests.RequestException:
        return ""


def github_commits(full_name: str, since_iso: Optional[str] = None,
                   token: str = "", limit: int = 20) -> list[str]:
    """Recent commit subject lines (merge commits skipped)."""
    params: dict = {"per_page": limit}
    if since_iso:
        params["since"] = since_iso if since_iso.endswith("Z") else since_iso + "Z"
    try:
        r = requests.get(f"{GITHUB_API}/repos/{full_name}/commits", params=params,
                         headers=_gh_headers(token), timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        msgs = [c["commit"]["message"].splitlines()[0] for c in r.json()]
        return [m for m in msgs if not m.lower().startswith("merge ")]
    except (requests.RequestException, KeyError, ValueError):
        return []


# ─── Zenodo ───────────────────────────────────────────────────────────────────

def zenodo_query(orcid: str = "", author: str = "") -> str:
    if orcid:
        return f'creators.orcid:"{orcid.strip()}"'
    words = [w for w in re.split(r"[\s,]+", author.strip()) if w]
    return "creators.name:(" + " AND ".join(words) + ")" if words else ""


def fetch_zenodo(query: str) -> list[WorkItem]:
    """Published Zenodo records matching `query`, newest first (one per concept)."""
    if not query:
        return []
    r = requests.get(ZENODO_API, params={"q": query, "size": 100, "sort": "mostrecent"},
                     headers={"Accept": "application/json"}, timeout=_TIMEOUT)
    r.raise_for_status()
    items: list[WorkItem] = []
    seen_concepts: set[str] = set()
    for hit in r.json().get("hits", {}).get("hits", []):
        concept = str(hit.get("conceptrecid") or hit.get("id"))
        if concept in seen_concepts:  # only the latest version of each work
            continue
        seen_concepts.add(concept)
        m = hit.get("metadata", {})
        doi = hit.get("doi") or m.get("doi") or ""
        url = (hit.get("links") or {}).get("self_html") or (
            f"https://doi.org/{doi}" if doi else f"https://zenodo.org/records/{hit.get('id')}")
        items.append(WorkItem(
            key=f"zenodo:{hit.get('id')}",
            kind="zenodo",
            title=m.get("title", "Untitled"),
            url=url,
            description=_strip_html(m.get("description", ""))[:4000],
            date=m.get("publication_date", "")[:10],
            details={
                "doi": doi,
                "type": (m.get("resource_type") or {}).get("title")
                        or (m.get("resource_type") or {}).get("type", ""),
                "authors": [c.get("name", "") for c in m.get("creators", [])],
                "keywords": m.get("keywords") or [],
                "journal": (m.get("journal") or {}).get("title", ""),
            },
        ))
    return items
