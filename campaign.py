"""
campaign.py — LinkedIn autopilot: your GitHub + Zenodo work → scheduled posts
==============================================================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

What it does
------------
  • Collects your public GitHub repos and Zenodo publications (sources.py)
  • Picks what to talk about next — new work first, alternating code and papers,
    "what's new" updates when a repo changes, deep-dives and roundups later
  • Writes each post with the LLM, grounded only in the real repo/paper content
  • Drafts each post LINKEDIN_DRAFT_AHEAD_HOURS before its slot (default Mon + Thu)
  • Review mode (default): you approve/edit drafts in the dashboard, then they
    publish at their slot.  Autopilot (LINKEDIN_AUTO_PUBLISH=true): no approval.

Post lifecycle
--------------
  draft ──approve──► approved ──(slot time)──► publishing ──► published
    │                                                  └────► failed ──retry──┘
    └──skip──► skipped   (a fresh draft is written for the slot)

The scheduler runs inside the API server, so the server must be running at
posting time. A post whose slot passed more than PUBLISH_GRACE_S ago is never
published automatically (no surprise posts days late) — publish it manually.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import linkedin_client as li
import sources
from config import settings
from monitor import Monitor

log = logging.getLogger("campaign")

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
ACTIVE = ("draft", "approved", "publishing", "published", "failed")
PUBLISH_GRACE_S = 24 * 3600
DRAFT_RETRY_S = 30 * 60
ITEMS_TTL_S = 30 * 60
ROUNDUP_EVERY = 6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS li_posts (
    id          TEXT PRIMARY KEY,
    slot_ts     REAL NOT NULL,
    item_key    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    style       TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    link_desc   TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    published_at REAL,
    post_urn    TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_li_slot ON li_posts(slot_ts);
CREATE TABLE IF NOT EXISTS li_kv (key TEXT PRIMARY KEY, value TEXT);
"""


class CampaignError(Exception):
    """Invalid request (wrong state, missing config, not found)."""


# ─── Schedule ─────────────────────────────────────────────────────────────────

def parse_days(spec: str) -> list[int]:
    days = sorted({DAYS[d.strip().lower()[:3]] for d in spec.split(",")
                   if d.strip().lower()[:3] in DAYS})
    if not days:
        raise CampaignError(f"LINKEDIN_POST_DAYS has no valid days: {spec!r}")
    return days


def next_slots(now_ts: float, days: list[int], hhmm: str, tz: str, n: int = 4) -> list[float]:
    """The next `n` posting times (UTC timestamps) strictly after now."""
    zone = ZoneInfo(tz)
    h, m = (int(x) for x in hhmm.split(":"))
    local_now = dt.datetime.fromtimestamp(now_ts, zone)
    out: list[float] = []
    day = local_now.date()
    while len(out) < n:
        if day.weekday() in days:
            slot = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
            if slot.timestamp() > now_ts:
                out.append(slot.timestamp())
        day += dt.timedelta(days=1)
    return out


# ─── Post writer (LLM) ────────────────────────────────────────────────────────

STYLE_BRIEFS = {
    "spotlight": "Introduce this project for the first time: the problem it solves, what makes "
                 "the approach interesting, and who would find it useful.",
    "paper":     "Announce this publication: the research question, the key idea or finding, "
                 "and why it matters to practitioners — in plain language, no jargon walls.",
    "update":    "Share what's new in this project, based on the recent commits listed. "
                 "Frame it as building in public: what changed and why it matters.",
    "deep_dive": "Revisit this work from a fresh angle: one technical insight, design decision, "
                 "or lesson learned while building it. Do not re-announce it.",
    "roundup":   "Write a short roundup of the recent work listed, one line per item, showing "
                 "momentum and range. Pick the 3–5 most interesting.",
}

BANNED_OPENERS = ["I'm thrilled", "I am thrilled", "I'm excited to announce",
                  "I am excited to announce", "In today's fast-paced", "In the ever-evolving"]

SYSTEM_PROMPT = """You are a LinkedIn ghostwriter for {name}, an engineer and researcher \
who shares their own GitHub projects and published papers.

Write ONE LinkedIn post in first person as {name}.

Hard rules:
- Use ONLY facts present in the material provided. Never invent numbers, users, results, \
benchmarks, employers, collaborators, awards or stars. If something is unknown, leave it out.
- Plain text only: no markdown, no **bold**, no headings, no bullet symbols other than "→" or "•".
- No URLs — the link is attached to the post as a preview card.
- 120–220 words (roundups up to 260). Short paragraphs of 1–2 sentences with blank lines between.
- First line is a hook under 120 characters that makes a professional want to read on.
- Never open with: {banned}.
- Avoid repeating these recent openings: {recent}
- At most 2 emojis.
- End with a genuine question or invitation that sparks comments.
- Final line: 3–5 relevant hashtags (e.g. #MachineLearning), nothing after them.

Return only the post text."""


def build_messages(item: dict, style: str, context: dict, author: str,
                   recent_openings: list[str]) -> list[dict]:
    lines = [f"POST TYPE: {style} — {STYLE_BRIEFS[style]}", ""]
    if style == "roundup":
        lines.append("RECENT WORK:")
        for w in context.get("items", []):
            lines.append(f"- [{'paper' if w['kind'] == 'zenodo' else 'repo'}] {w['title']}: "
                         f"{w['description'][:200]}")
    elif item["kind"] == "zenodo":
        d = item["details"]
        lines += [f"PAPER TITLE: {item['title']}",
                  f"Published: {item['date']}  Type: {d.get('type', '')}  DOI: {d.get('doi', '')}",
                  f"Authors: {', '.join(d.get('authors', []))}",
                  f"Keywords: {', '.join(d.get('keywords', []))}",
                  "", "ABSTRACT:", item["description"][:3500]]
    else:
        d = item["details"]
        lines += [f"REPOSITORY: {item['title']}",
                  f"Description: {item['description']}",
                  f"Language: {d.get('language') or 'n/a'}  Topics: {', '.join(d.get('topics', []))}",
                  f"Stars: {d.get('stars', 0)}  Created: {d.get('created_at', '')}"]
        if context.get("commits"):
            lines += ["", "RECENT COMMITS:"] + [f"- {c}" for c in context["commits"][:15]]
        if context.get("readme"):
            lines += ["", "README (excerpt):", context["readme"]]
    system = SYSTEM_PROMPT.format(
        name=author or "the author",
        banned=", ".join(f'"{b}"' for b in BANNED_OPENERS),
        recent="; ".join(f'"{o}"' for o in recent_openings) or "(none yet)",
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def clean_post(text: str) -> str:
    text = text.strip().strip('"').strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)           # markdown bold
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)              # markdown headings
    text = re.sub(r"https?://\S+", "", text)                # links go in the card
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[: li.MAX_POST_CHARS]


def write_post(item: dict, style: str, context: dict, author: str,
               recent_openings: list[str]) -> str:
    from agent import get_client  # lazy: needs OPENAI_API_KEY only when writing
    resp = get_client().chat.completions.create(
        model=settings.MODEL,
        messages=build_messages(item, style, context, author, recent_openings),
        temperature=0.8,
        max_tokens=900,
    )
    text = clean_post(resp.choices[0].message.content or "")
    if len(text) < 80:
        raise CampaignError("The model returned an unusably short post")
    return text


# ─── Campaign ─────────────────────────────────────────────────────────────────

class Campaign:
    def __init__(
        self,
        db_path: str,
        monitor: Monitor,
        *,
        fetch_items: Optional[Callable[[], list[sources.WorkItem]]] = None,
        writer: Optional[Callable[..., str]] = None,
        publisher: Optional[Callable[..., str]] = None,
        context_for: Optional[Callable[[dict, str, Optional[float]], dict]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.monitor = monitor
        self._fetch_items = fetch_items or self._fetch_sources
        self._writer = writer or write_post
        self._publisher = publisher or li.create_post
        self._context_for = context_for or self._default_context
        self.clock = clock
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()        # DB access
        self._draft_lock = threading.Lock()  # one draft written at a time
        self._items: list[sources.WorkItem] = []
        self._items_at = 0.0
        self.source_errors: list[str] = []
        self._last_draft_fail = 0.0
        self._last_publish_warn = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self, interval: float = 60.0) -> None:
        if self._thread:
            return
        with self._lock:
            self._db.execute(
                "UPDATE li_posts SET status='failed', updated_at=?, error=? WHERE status='publishing'",
                (self.clock(), "Interrupted while publishing — check your LinkedIn feed before retrying"))
            self._db.commit()
        self._stop.clear()

        def loop() -> None:
            self._stop.wait(5)
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("Campaign tick failed")
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="linkedin-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
        self._thread = None

    # ── Key/value settings ────────────────────────────────────────────────────
    def _get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT value FROM li_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def _set(self, **kv: Optional[str]) -> None:
        with self._lock:
            for k, v in kv.items():
                if v is None:
                    self._db.execute("DELETE FROM li_kv WHERE key=?", (k,))
                else:
                    self._db.execute("INSERT INTO li_kv(key, value) VALUES(?, ?) "
                                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            self._db.commit()

    @property
    def enabled(self) -> bool:
        return self._get("enabled", "true") == "true"

    @property
    def auto_publish(self) -> bool:
        return self._get("auto_publish", str(settings.LINKEDIN_AUTO_PUBLISH).lower()) == "true"

    def update_settings(self, enabled: Optional[bool] = None, auto_publish: Optional[bool] = None) -> dict:
        if enabled is not None:
            self._set(enabled=str(enabled).lower())
        if auto_publish is not None:
            self._set(auto_publish=str(auto_publish).lower())
        return self.status()

    # ── LinkedIn connection ───────────────────────────────────────────────────
    def connect_url(self) -> str:
        if not (settings.LINKEDIN_CLIENT_ID and settings.LINKEDIN_CLIENT_SECRET):
            raise CampaignError("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env first")
        state = secrets.token_urlsafe(24)
        self._set(oauth_state=state, oauth_state_at=str(self.clock()))
        return li.auth_url(settings.LINKEDIN_CLIENT_ID, settings.LINKEDIN_REDIRECT_URI, state)

    def finish_connect(self, code: str, state: str) -> dict:
        expected = self._get("oauth_state")
        issued = float(self._get("oauth_state_at", "0") or 0)
        if not expected or not secrets.compare_digest(state or "", expected) \
                or self.clock() - issued > 15 * 60:
            raise CampaignError("This LinkedIn sign-in link is invalid or expired — start again from the dashboard")
        self._set(oauth_state=None, oauth_state_at=None)
        tok = li.exchange_code(code, settings.LINKEDIN_CLIENT_ID,
                               settings.LINKEDIN_CLIENT_SECRET, settings.LINKEDIN_REDIRECT_URI)
        person = li.get_person(tok["access_token"])
        self._set(
            access_token=tok["access_token"],
            expires_at=str(self.clock() + int(tok.get("expires_in", 60 * 86400))),
            person_urn=f"urn:li:person:{person['sub']}",
            person_name=person.get("name") or "",
        )
        self.monitor.emit("li_connected", None, name=person.get("name") or "")
        return self.status()

    def disconnect(self) -> None:
        self._set(access_token=None, expires_at=None, person_urn=None)

    def _connection(self) -> dict:
        token = self._get("access_token")
        expires = float(self._get("expires_at", "0") or 0)
        connected = bool(token) and expires > self.clock()
        return {
            "connected": connected,
            "name": self._get("person_name", ""),
            "expires_at": expires or None,
            "days_left": int((expires - self.clock()) // 86400) if connected else None,
            "expired": bool(token) and not connected,
        }

    # ── Sources ───────────────────────────────────────────────────────────────
    def _fetch_sources(self) -> list[sources.WorkItem]:
        items: list[sources.WorkItem] = []
        errors: list[str] = []
        if settings.GITHUB_USERNAME:
            try:
                exclude = {x.strip() for x in settings.GITHUB_EXCLUDE.split(",") if x.strip()}
                items += sources.fetch_github(settings.GITHUB_USERNAME, settings.GITHUB_TOKEN, exclude)
            except Exception as e:
                errors.append(f"GitHub: {e}")
        q = sources.zenodo_query(settings.ZENODO_ORCID, settings.ZENODO_AUTHOR)
        if q:
            try:
                items += sources.fetch_zenodo(q)
            except Exception as e:
                errors.append(f"Zenodo: {e}")
        self.source_errors = errors
        return items

    def items(self, refresh: bool = False) -> list[sources.WorkItem]:
        if refresh or not self._items or self.clock() - self._items_at > ITEMS_TTL_S:
            fetched = self._fetch_items()
            if fetched or not self._items:  # keep the last good list on a failed refresh
                self._items = fetched
            self._items_at = self.clock()
        return self._items

    def library(self, refresh: bool = False) -> list[dict]:
        items = self.items(refresh)
        with self._lock:
            rows = self._db.execute(
                "SELECT item_key, COUNT(*) n, MAX(published_at) last FROM li_posts "
                "WHERE status='published' GROUP BY item_key").fetchall()
            queued = {r["item_key"] for r in self._db.execute(
                "SELECT item_key FROM li_posts WHERE status IN ('draft','approved','failed')")}
        stats = {r["item_key"]: (r["n"], r["last"]) for r in rows}
        out = []
        for it in items:
            n, last = stats.get(it.key, (0, None))
            out.append({**it.to_dict(), "times_posted": n, "last_posted": last,
                        "queued": it.key in queued})
        return out

    def _default_context(self, item: dict, style: str, since_ts: Optional[float]) -> dict:
        if style == "roundup" or item["kind"] != "github":
            return {}
        full = item["details"]["full_name"]
        ctx = {"readme": sources.github_readme(full, settings.GITHUB_TOKEN)}
        if style == "update":
            since = dt.datetime.fromtimestamp(since_ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") \
                if since_ts else None
            ctx["commits"] = sources.github_commits(full, since, settings.GITHUB_TOKEN)
        return ctx

    # ── Planning ──────────────────────────────────────────────────────────────
    def _history(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM li_posts ORDER BY slot_ts DESC").fetchall()

    def _plan(self, items: list[sources.WorkItem], forced_key: Optional[str] = None
              ) -> tuple[dict, str, Optional[float]]:
        """Choose (item, style, last_posted_ts) for the next post."""
        history = self._history()
        active = [h for h in history if h["status"] in ACTIVE]
        skipped = {h["item_key"] for h in history if h["status"] == "skipped"}
        # When we last wrote about each item (content was gathered at created_at).
        last_ts: dict[str, float] = {}
        for h in active:
            last_ts[h["item_key"]] = max(last_ts.get(h["item_key"], 0.0), h["created_at"])
        by_key = {i.key: i for i in items}

        def style_for(it: sources.WorkItem) -> str:
            if it.key not in last_ts:
                return "paper" if it.kind == "zenodo" else "spotlight"
            if it.kind == "github" and _iso_ts(it.date) > last_ts[it.key]:
                return "update"
            return "deep_dive"

        if forced_key:
            if forced_key not in by_key:
                raise CampaignError(f"Unknown work item: {forced_key}")
            it = by_key[forced_key]
            return it.to_dict(), style_for(it), last_ts.get(it.key)

        if not items:
            raise CampaignError("No GitHub repos or Zenodo papers found — check GITHUB_USERNAME / "
                                "ZENODO_ORCID / ZENODO_AUTHOR in .env")

        # Every few posts, a roundup of recent work keeps the feed varied.
        if len(active) and (len(active) + 1) % ROUNDUP_EVERY == 0 and len(items) >= 3 \
                and not (active and active[0]["style"] == "roundup"):
            recent = sorted(items, key=lambda i: i.date, reverse=True)[:6]
            return ({"key": f"roundup:{int(self.clock())}", "kind": "roundup",
                     "title": "Recent work roundup",
                     "url": f"https://github.com/{settings.GITHUB_USERNAME}" if settings.GITHUB_USERNAME
                            else recent[0].url,
                     "description": "A roundup of my recent projects and publications.",
                     "date": recent[0].date, "details": {},
                     "_roundup": [i.to_dict() for i in recent]}, "roundup", None)

        # 1) Work never posted about: newest first, alternating papers and code.
        fresh = sorted([i for i in items if i.key not in last_ts and i.key not in skipped],
                       key=lambda i: i.date, reverse=True)
        if fresh:
            last_kind = active[0]["kind"] if active else None
            prefer = [i for i in fresh if i.kind != last_kind] or fresh
            it = prefer[0]
            return it.to_dict(), style_for(it), None

        # 2) Repos with new commits since we last posted about them.
        updated = sorted([i for i in items if i.key in last_ts and style_for(i) == "update"
                          and i.key not in skipped], key=lambda i: i.date, reverse=True)
        if updated:
            it = updated[0]
            return it.to_dict(), "update", last_ts[it.key]

        # 3) Everything covered: revisit the work posted about longest ago.
        pool = [i for i in items if i.key not in skipped] or items
        it = min(pool, key=lambda i: last_ts.get(i.key, 0.0))
        return it.to_dict(), "deep_dive", last_ts.get(it.key)

    def _free_slot(self, after_ts: Optional[float] = None) -> float:
        with self._lock:
            taken = {round(r["slot_ts"]) for r in self._db.execute(
                "SELECT slot_ts FROM li_posts WHERE status IN ('draft','approved','publishing','published','failed')")}
        for s in next_slots(after_ts or self.clock(), parse_days(settings.LINKEDIN_POST_DAYS),
                            settings.LINKEDIN_POST_TIME, settings.LINKEDIN_TIMEZONE, n=60):
            if round(s) not in taken:
                return s
        raise CampaignError("No free posting slot in the next 60 slots")

    # ── Drafts ────────────────────────────────────────────────────────────────
    def create_draft(self, item_key: Optional[str] = None, slot_ts: Optional[float] = None) -> dict:
        with self._draft_lock:
            slot = slot_ts or self._free_slot()
            item, style, since = self._plan(self.items(), item_key)
            context = {"items": item.pop("_roundup")} if style == "roundup" \
                else self._context_for(item, style, since)
            text = self._writer(item, style, context, self._author(), self._recent_openings())
            pid = uuid.uuid4().hex[:10]
            now = self.clock()
            with self._lock:
                self._db.execute(
                    "INSERT INTO li_posts (id, slot_ts, item_key, kind, style, title, url, link_desc, "
                    "text, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, slot, item["key"], item["kind"], style, item["title"], item["url"],
                     item["description"][:250], text, "draft", now, now))
                self._db.commit()
        self.monitor.emit("li_draft_created", None, post=pid, title=item["title"], style=style,
                          slot=slot)
        return self.get(pid)

    def _author(self) -> str:
        return self._get("person_name") or settings.ZENODO_AUTHOR or ""

    def _recent_openings(self) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT text FROM li_posts WHERE status IN "
                                    "('draft','approved','published') ORDER BY slot_ts DESC LIMIT 6")
            return [r["text"].splitlines()[0][:100] for r in rows if r["text"]]

    # ── Post operations ───────────────────────────────────────────────────────
    def get(self, pid: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM li_posts WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise CampaignError(f"Post '{pid}' not found")
        d = dict(row)
        d["post_link"] = li.post_url(d["post_urn"] or "")
        d["chars"] = len(d["text"])
        return d

    def list_posts(self, limit: int = 60) -> list[dict]:
        with self._lock:
            ids = [r["id"] for r in self._db.execute(
                "SELECT id FROM li_posts ORDER BY slot_ts DESC, created_at DESC LIMIT ?", (limit,))]
        return [self.get(i) for i in ids]

    def _move(self, pid: str, to: str, from_: tuple[str, ...], **fields: Any) -> bool:
        self.get(pid)
        cols = ", ".join(f"{k}=?" for k in ["status", "updated_at", *fields])
        with self._lock:
            n = self._db.execute(
                f"UPDATE li_posts SET {cols} WHERE id=? AND status IN ({','.join('?' * len(from_))})",
                (to, self.clock(), *fields.values(), pid, *from_)).rowcount
            self._db.commit()
        return n == 1

    def update_text(self, pid: str, text: str) -> dict:
        text = text.strip()
        if not text:
            raise CampaignError("Post text cannot be empty")
        if len(text) > li.MAX_POST_CHARS:
            raise CampaignError(f"LinkedIn allows {li.MAX_POST_CHARS} characters; this is {len(text)}")
        if not self._move(pid, self.get(pid)["status"], ("draft", "approved", "failed"), text=text):
            raise CampaignError("Only unpublished posts can be edited")
        return self.get(pid)

    def approve(self, pid: str) -> dict:
        if not self._move(pid, "approved", ("draft",)):
            raise CampaignError("Only drafts can be approved")
        self.monitor.emit("li_approved", None, post=pid, title=self.get(pid)["title"])
        return self.get(pid)

    def unapprove(self, pid: str) -> dict:
        if not self._move(pid, "draft", ("approved", "failed")):
            raise CampaignError("Only approved or failed posts can go back to draft")
        return self.get(pid)

    def skip(self, pid: str) -> dict:
        if not self._move(pid, "skipped", ("draft", "approved", "failed")):
            raise CampaignError("Published posts cannot be skipped")
        return self.get(pid)

    def delete(self, pid: str) -> None:
        if self.get(pid)["status"] in ("publishing", "published"):
            raise CampaignError("Published posts stay in the history")
        with self._lock:
            self._db.execute("DELETE FROM li_posts WHERE id=?", (pid,))
            self._db.commit()

    def regenerate(self, pid: str) -> dict:
        post = self.get(pid)
        if post["status"] not in ("draft", "approved", "failed"):
            raise CampaignError("Only unpublished posts can be rewritten")
        items = {i.key: i for i in self.items()}
        if post["style"] == "roundup":
            recent = sorted(items.values(), key=lambda i: i.date, reverse=True)[:6]
            item = {"key": post["item_key"], "kind": "roundup", "title": post["title"],
                    "url": post["url"], "description": post["link_desc"], "details": {}}
            context = {"items": [i.to_dict() for i in recent]}
        else:
            if post["item_key"] not in items:
                raise CampaignError("The source repo/paper is no longer available")
            item = items[post["item_key"]].to_dict()
            prev = [h["created_at"] for h in self._history()
                    if h["item_key"] == post["item_key"] and h["id"] != pid and h["status"] in ACTIVE]
            context = self._context_for(item, post["style"], max(prev) if prev else None)
        text = self._writer(item, post["style"], context, self._author(),
                            [o for o in self._recent_openings() if o != post["text"].splitlines()[0][:100]])
        self._move(pid, "draft", ("draft", "approved", "failed"), text=text, error=None)
        return self.get(pid)

    def publish_now(self, pid: str) -> dict:
        return self._publish(pid, from_=("draft", "approved", "failed"))

    def _publish(self, pid: str, from_: tuple[str, ...]) -> dict:
        conn = self._connection()
        if not conn["connected"]:
            msg = ("LinkedIn connection expired — reconnect from the dashboard" if conn["expired"]
                   else "LinkedIn is not connected — click Connect LinkedIn in the dashboard")
            raise CampaignError(msg)
        if not self._move(pid, "publishing", from_, error=None):  # atomic claim: never double-post
            raise CampaignError(f"Post is '{self.get(pid)['status']}' and cannot be published")
        post = self.get(pid)
        try:
            urn = self._publisher(self._get("access_token"), self._get("person_urn"), post["text"],
                                  link=post["url"] or None, link_title=post["title"],
                                  link_description=post["link_desc"],
                                  version=settings.LINKEDIN_API_VERSION or None)
        except Exception as e:
            self._move(pid, "failed", ("publishing",), error=str(e))
            self.monitor.emit("li_publish_failed", None, post=pid, title=post["title"], error=str(e))
            return self.get(pid)
        # Published early (manual "Publish now"): record it at the real time so its
        # future slot opens up for the next post instead of staying empty.
        now = self.clock()
        self._move(pid, "published", ("publishing",), published_at=now, post_urn=urn,
                   slot_ts=min(post["slot_ts"], now))
        self.monitor.emit("li_published", None, post=pid, title=post["title"], urn=urn)
        return self.get(pid)

    # ── Scheduler ─────────────────────────────────────────────────────────────
    def tick(self) -> None:
        if not self.enabled:
            return
        now = self.clock()
        # 1) Publish posts whose slot has arrived.
        due = ("approved", "draft") if self.auto_publish else ("approved",)
        with self._lock:
            rows = self._db.execute(
                f"SELECT id, slot_ts FROM li_posts WHERE slot_ts<=? AND status IN ({','.join('?' * len(due))}) "
                "ORDER BY slot_ts", (now, *due)).fetchall()
        for r in rows:
            if now - r["slot_ts"] > PUBLISH_GRACE_S:
                continue  # too late — left for a manual decision
            try:
                self._publish(r["id"], from_=due)
            except CampaignError as e:
                # Not connected: warn at most hourly instead of every tick.
                if now - self._last_publish_warn > 3600:
                    self._last_publish_warn = now
                    self.monitor.emit("li_publish_failed", None, post=r["id"], error=str(e))
                break

        # 2) Draft upcoming slots ahead of time.
        if now - self._last_draft_fail < DRAFT_RETRY_S:
            return
        ahead = settings.LINKEDIN_DRAFT_AHEAD_HOURS * 3600
        slots = next_slots(now, parse_days(settings.LINKEDIN_POST_DAYS),
                           settings.LINKEDIN_POST_TIME, settings.LINKEDIN_TIMEZONE, n=4)
        with self._lock:
            taken = {round(r["slot_ts"]) for r in self._db.execute(
                "SELECT slot_ts FROM li_posts WHERE status IN ('draft','approved','publishing','published','failed')")}
        for s in slots:
            if s - now > ahead or round(s) in taken:
                continue
            try:
                self.create_draft(slot_ts=s)
            except Exception as e:
                self._last_draft_fail = now
                log.warning("Could not draft LinkedIn post: %s", e)
                self.monitor.emit("li_draft_failed", None, error=str(e))
                return

    # ── Status ────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        issues = []
        if not settings.OPENAI_API_KEY:
            issues.append("OPENAI_API_KEY is not set — the agent can't write posts.")
        if not settings.GITHUB_USERNAME and not (settings.ZENODO_ORCID or settings.ZENODO_AUTHOR):
            issues.append("Set GITHUB_USERNAME and/or ZENODO_ORCID / ZENODO_AUTHOR so the agent knows your work.")
        if not (settings.LINKEDIN_CLIENT_ID and settings.LINKEDIN_CLIENT_SECRET):
            issues.append("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET (LinkedIn developer app) to publish.")
        try:
            days = parse_days(settings.LINKEDIN_POST_DAYS)
            ZoneInfo(settings.LINKEDIN_TIMEZONE)
            upcoming = next_slots(self.clock(), days, settings.LINKEDIN_POST_TIME,
                                  settings.LINKEDIN_TIMEZONE, n=4)
        except Exception as e:
            issues.append(f"Schedule settings are invalid: {e}")
            days, upcoming = [], []
        conn = self._connection()
        if conn["expired"]:
            issues.append("The LinkedIn connection expired (tokens last ~60 days) — reconnect.")
        elif conn["connected"] and conn["days_left"] is not None and conn["days_left"] < 7:
            issues.append(f"The LinkedIn connection expires in {conn['days_left']} days — reconnect soon.")
        with self._lock:
            counts = {r["status"]: r["n"] for r in self._db.execute(
                "SELECT status, COUNT(*) n FROM li_posts GROUP BY status")}
        return {
            "enabled": self.enabled,
            "auto_publish": self.auto_publish,
            "linkedin": conn,
            "linkedin_app_configured": bool(settings.LINKEDIN_CLIENT_ID and settings.LINKEDIN_CLIENT_SECRET),
            "schedule": {
                "days": [k for k, v in DAYS.items() if v in days],
                "time": settings.LINKEDIN_POST_TIME,
                "timezone": settings.LINKEDIN_TIMEZONE,
                "draft_ahead_hours": settings.LINKEDIN_DRAFT_AHEAD_HOURS,
                "next_slots": upcoming,
            },
            "sources": {
                "github": settings.GITHUB_USERNAME,
                "zenodo": sources.zenodo_query(settings.ZENODO_ORCID, settings.ZENODO_AUTHOR),
                "items": len(self._items),
                "errors": self.source_errors,
            },
            "counts": {s: counts.get(s, 0) for s in
                       ("draft", "approved", "publishing", "published", "failed", "skipped")},
            "issues": issues,
        }


def _iso_ts(s: str) -> float:
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", ""))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return 0.0

