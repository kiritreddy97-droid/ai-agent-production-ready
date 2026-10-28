"""
linkedin_client.py — Minimal LinkedIn API client (OAuth + Posts API)
=====================================================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

Uses LinkedIn's official, self-serve products:
  • "Sign In with LinkedIn using OpenID Connect"  → scopes: openid profile
  • "Share on LinkedIn"                           → scope : w_member_social

Flow
----
  1. auth_url()        → send the browser to LinkedIn to approve the app
  2. exchange_code()   → trade the returned ?code= for an access token (~60 days)
  3. get_person()      → read the member id (OpenID "sub") → author URN
  4. create_post()     → publish to the member's feed via /rest/posts

Post text must be in LinkedIn's "little text" format: reserved characters are
backslash-escaped and hashtags use a template. `to_little_text()` handles it,
so callers pass ordinary text.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional
from urllib.parse import urlencode

import requests

AUTH_URL     = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL    = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL    = "https://api.linkedin.com/rest/posts"
SCOPES       = "openid profile w_member_social"
MAX_POST_CHARS = 3000
_TIMEOUT = 20


class LinkedInError(Exception):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def default_api_version(today: Optional[dt.date] = None) -> str:
    """LinkedIn versions are monthly (YYYYMM) and retired after ~1 year;
    two months back is always released and comfortably supported."""
    today = today or dt.date.today()
    y, m = today.year, today.month - 2
    if m <= 0:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": SCOPES,
    })


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    r = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise LinkedInError(f"Token exchange failed: {_err(r)}", r.status_code)
    return r.json()  # {"access_token", "expires_in", "scope", ...}


def get_person(token: str) -> dict:
    r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise LinkedInError(f"Could not read LinkedIn profile: {_err(r)}", r.status_code)
    return r.json()  # {"sub", "name", "picture", ...}


_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_HASHTAG = re.compile(r"(^|(?<=\s))\\#(\w+)")


def to_little_text(text: str) -> str:
    """Escape LinkedIn 'little text' reserved characters; keep #hashtags as hashtags.
    Unescaped reserved characters make LinkedIn silently cut the post short."""
    escaped = _RESERVED.sub(r"\\\1", text)
    return _HASHTAG.sub(lambda m: m.group(1) + "{hashtag|\\#|" + m.group(2) + "}", escaped)


def create_post(token: str, author_urn: str, text: str,
                link: Optional[str] = None, link_title: str = "", link_description: str = "",
                version: Optional[str] = None) -> str:
    """Publish a public post; returns its URN (e.g. urn:li:share:123)."""
    if len(text) > MAX_POST_CHARS:
        raise LinkedInError(f"Post is {len(text)} characters; LinkedIn allows {MAX_POST_CHARS}")
    body: dict = {
        "author": author_urn,
        "commentary": to_little_text(text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if link:
        article: dict = {"source": link, "title": (link_title or link)[:200]}
        if link_description:
            article["description"] = link_description[:250]
        body["content"] = {"article": article}
    r = requests.post(POSTS_URL, json=body, timeout=_TIMEOUT, headers={
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": version or default_api_version(),
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    })
    if r.status_code not in (200, 201):
        hint = ""
        if r.status_code == 401:
            hint = " — the LinkedIn connection expired; reconnect from the dashboard"
        elif r.status_code == 426 or "VERSION" in r.text.upper():
            hint = " — set LINKEDIN_API_VERSION in .env to a current YYYYMM version"
        raise LinkedInError(f"LinkedIn rejected the post: {_err(r)}{hint}", r.status_code)
    return r.headers.get("x-restli-id") or r.headers.get("X-RestLi-Id") or ""


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/" if urn else ""


def _err(r: requests.Response) -> str:
    try:
        j = r.json()
        return f"{r.status_code} {j.get('message') or j.get('error_description') or j.get('error') or j}"
    except ValueError:
        return f"{r.status_code} {r.text[:200]}"
