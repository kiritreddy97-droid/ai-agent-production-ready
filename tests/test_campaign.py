import datetime as dt
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

import campaign as cmod
import linkedin_client as li
import sources
from campaign import Campaign, CampaignError, next_slots, parse_days
from monitor import Monitor
from sources import WorkItem

TZ = "America/New_York"
# Sunday 2026-11-01 12:00 New York
SUNDAY_NOON = dt.datetime(2026, 11, 1, 12, 0, tzinfo=ZoneInfo(TZ)).timestamp()


def repo(name, date="2026-10-20T10:00:00", desc="A tool"):
    return WorkItem(key=f"github:me/{name}", kind="github", title=name,
                    url=f"https://github.com/me/{name}", description=desc, date=date,
                    details={"full_name": f"me/{name}", "language": "Python", "topics": [],
                             "stars": 3, "created_at": "2026-01-01"})


def paper(pid, date="2026-05-01"):
    return WorkItem(key=f"zenodo:{pid}", kind="zenodo", title=f"Paper {pid}",
                    url=f"https://zenodo.org/records/{pid}", description="We study X.",
                    date=date, details={"doi": f"10.5281/zenodo.{pid}", "authors": ["Daida, K."],
                                        "keywords": ["ml"], "type": "Journal article"})


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cmod.settings, "LINKEDIN_POST_DAYS", "mon,thu")
    monkeypatch.setattr(cmod.settings, "LINKEDIN_POST_TIME", "09:00")
    monkeypatch.setattr(cmod.settings, "LINKEDIN_TIMEZONE", TZ)
    monkeypatch.setattr(cmod.settings, "LINKEDIN_DRAFT_AHEAD_HOURS", 48)
    monkeypatch.setattr(cmod.settings, "LINKEDIN_AUTO_PUBLISH", False)
    monkeypatch.setattr(cmod.settings, "LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setattr(cmod.settings, "LINKEDIN_CLIENT_SECRET", "secret")
    items = [repo("alpha", "2026-10-25T10:00:00"), repo("beta", "2026-09-01T10:00:00"), paper(7)]
    published, written = [], []

    def writer(item, style, context, author, recent):
        written.append((item["key"], style, context))
        return f"Hook about {item['title']} ({style})\n\nBody text that is long enough. #AI"

    def publisher(token, urn, text, **kw):
        published.append({"token": token, "urn": urn, "text": text, **kw})
        return f"urn:li:share:{len(published)}"

    clock = Clock(SUNDAY_NOON)
    c = Campaign(str(tmp_path / "c.db"), Monitor(), fetch_items=lambda: items, writer=writer,
                 publisher=publisher, context_for=lambda i, s, since: {"since": since}, clock=clock)
    return NS(c=c, items=items, published=published, written=written, clock=clock)


def connect(c):
    c._set(access_token="tok", expires_at=str(c.clock() + 60 * 86400),
           person_urn="urn:li:person:abc", person_name="Kirit")


# ─── Schedule ────────────────────────────────────────────────────────────────

def test_next_slots_monday_and_thursday():
    slots = next_slots(SUNDAY_NOON, parse_days("mon,thu"), "09:00", TZ, n=4)
    local = [dt.datetime.fromtimestamp(s, ZoneInfo(TZ)) for s in slots]
    assert [d.strftime("%a %d %H:%M") for d in local] == \
        ["Mon 02 09:00", "Thu 05 09:00", "Mon 09 09:00", "Thu 12 09:00"]


def test_parse_days_rejects_garbage():
    assert parse_days("Monday, THURSDAY") == [0, 3]
    with pytest.raises(CampaignError):
        parse_days("someday")


# ─── Planning & drafting ─────────────────────────────────────────────────────

def test_tick_drafts_upcoming_slots_alternating_code_and_papers(env):
    env.c.tick()  # Sunday noon: Monday 9am is within 48h, Thursday isn't
    posts = env.c.list_posts()
    assert len(posts) == 1 and posts[0]["status"] == "draft"
    assert posts[0]["item_key"] == "github:me/alpha" and posts[0]["style"] == "spotlight"

    env.clock.t += 2 * 86400  # Tuesday noon -> Thursday slot now in range
    env.c.tick()
    keys = [p["item_key"] for p in env.c.list_posts()]
    assert keys[0] == "zenodo:7"  # alternates to a paper
    assert env.c.list_posts()[0]["style"] == "paper"

    env.c.tick()  # idempotent: no duplicate drafts for the same slots
    assert len(env.c.list_posts()) == 2


def test_review_mode_publishes_only_approved(env):
    connect(env.c)
    env.c.tick()
    post = env.c.list_posts()[0]
    env.clock.t = post["slot_ts"] + 60
    env.c.tick()
    assert env.published == [] and env.c.get(post["id"])["status"] == "draft"

    env.c.approve(post["id"])
    env.c.tick()
    done = env.c.get(post["id"])
    assert done["status"] == "published" and done["post_urn"] == "urn:li:share:1"
    assert done["post_link"] == "https://www.linkedin.com/feed/update/urn:li:share:1/"
    sent = env.published[0]
    assert sent["urn"] == "urn:li:person:abc" and sent["link"] == "https://github.com/me/alpha"
    env.c.tick()
    assert len(env.published) == 1  # never posts twice


def test_auto_publish_and_grace_period(env):
    connect(env.c)
    env.c.update_settings(auto_publish=True)
    env.c.tick()
    post = env.c.list_posts()[0]
    env.clock.t = post["slot_ts"] + 2 * 86400  # server was down for two days
    env.c.tick()
    assert env.published == []  # too late to auto-post
    assert env.c.publish_now(post["id"])["status"] == "published"


def test_not_connected_keeps_post_approved(env):
    env.c.tick()
    post = env.c.list_posts()[0]
    env.c.approve(post["id"])
    env.clock.t = post["slot_ts"] + 60
    env.c.tick()
    assert env.c.get(post["id"])["status"] == "approved"
    with pytest.raises(CampaignError, match="not connected"):
        env.c.publish_now(post["id"])


def test_failed_publish_can_retry(env):
    connect(env.c)
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise li.LinkedInError("LinkedIn rejected the post: 500")
        return "urn:li:share:9"
    env.c._publisher = flaky
    post = env.c.create_draft()
    failed = env.c.publish_now(post["id"])
    assert failed["status"] == "failed" and "500" in failed["error"]
    assert env.c.publish_now(post["id"])["status"] == "published"


def test_publishing_early_frees_the_slot(env):
    connect(env.c)
    post = env.c.create_draft()
    slot = post["slot_ts"]
    done = env.c.publish_now(post["id"])
    assert done["slot_ts"] == env.clock.t < slot
    assert env.c.create_draft()["slot_ts"] == slot  # Monday still gets its post


def test_skip_picks_a_different_item_for_the_slot(env):
    env.c.tick()
    first = env.c.list_posts()[0]
    env.c.skip(first["id"])
    env.c.tick()
    active = [p for p in env.c.list_posts() if p["status"] == "draft"]
    assert len(active) == 1 and active[0]["slot_ts"] == first["slot_ts"]
    assert active[0]["item_key"] != first["item_key"]


def test_after_all_items_updates_then_deep_dives(env):
    for _ in range(3):
        env.c.create_draft()
    assert {p["style"] for p in env.c.list_posts()} == {"spotlight", "paper"}
    env.clock.t += 86400
    env.items[0].date = dt.datetime.fromtimestamp(env.clock.t - 3600, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")  # new commits
    p = env.c.create_draft()
    assert p["item_key"] == "github:me/alpha" and p["style"] == "update"
    assert env.written[-1][2]["since"] is not None  # commits looked up since last post
    p = env.c.create_draft()
    assert p["style"] in ("deep_dive", "roundup")


def test_roundup_every_sixth_post(env):
    styles = [env.c.create_draft()["style"] for _ in range(6)]
    assert styles[5] == "roundup" and "roundup" not in styles[:5]


def test_edit_regenerate_and_delete(env):
    p = env.c.create_draft()
    env.c.approve(p["id"])
    assert env.c.update_text(p["id"], "My own words #Python")["text"] == "My own words #Python"
    with pytest.raises(CampaignError):
        env.c.update_text(p["id"], "x" * 3001)
    again = env.c.regenerate(p["id"])
    assert again["status"] == "draft" and again["text"].startswith("Hook about")
    env.c.delete(p["id"])
    with pytest.raises(CampaignError):
        env.c.get(p["id"])


def test_no_sources_gives_clear_error(env):
    env.items.clear()
    with pytest.raises(CampaignError, match="GITHUB_USERNAME"):
        env.c.create_draft()


def test_disabled_campaign_does_nothing(env):
    env.c.update_settings(enabled=False)
    env.c.tick()
    assert env.c.list_posts() == []


def test_status_reports_issues(env, monkeypatch):
    monkeypatch.setattr(cmod.settings, "LINKEDIN_CLIENT_ID", "")
    s = env.c.status()
    assert any("LINKEDIN_CLIENT_ID" in i for i in s["issues"])
    assert s["schedule"]["days"] == ["mon", "thu"] and len(s["schedule"]["next_slots"]) == 4
    assert s["linkedin"]["connected"] is False


# ─── OAuth ───────────────────────────────────────────────────────────────────

def test_oauth_state_is_checked(env, monkeypatch):
    url = env.c.connect_url()
    assert "w_member_social" in url and "state=" in url
    state = url.split("state=")[1].split("&")[0]
    with pytest.raises(CampaignError, match="invalid or expired"):
        env.c.finish_connect("code", "forged")
    monkeypatch.setattr(li, "exchange_code", lambda *a: {"access_token": "T", "expires_in": 5184000})
    monkeypatch.setattr(li, "get_person", lambda t: {"sub": "xyz", "name": "Kirit Reddy Daida"})
    s = env.c.finish_connect("code", state)
    assert s["linkedin"]["connected"] and s["linkedin"]["name"] == "Kirit Reddy Daida"
    assert s["linkedin"]["days_left"] == 60
    assert env.c._get("person_urn") == "urn:li:person:xyz"
    with pytest.raises(CampaignError):  # state is single-use
        env.c.finish_connect("code", state)


# ─── Post text helpers ───────────────────────────────────────────────────────

def test_little_text_escaping_keeps_hashtags():
    out = li.to_little_text("Built (fast) [v2] @home a_b\n\n#AI #MachineLearning")
    assert r"\(fast\)" in out and r"\[v2\]" in out and r"\@home" in out and r"a\_b" in out
    assert "{hashtag|\\#|AI}" in out and "{hashtag|\\#|MachineLearning}" in out


def test_clean_post_strips_markdown_and_links():
    text = cmod.clean_post('"**Big news**\n\n## Heading\nSee https://x.y/z now\n\n\n\nEnd"')
    assert text == "Big news\n\nHeading\nSee  now\n\nEnd"


def test_prompt_is_grounded_in_source_material():
    msgs = cmod.build_messages(paper(1).to_dict(), "paper", {}, "Kirit", ["Old hook"])
    assert "Never invent" in msgs[0]["content"] and '"Old hook"' in msgs[0]["content"]
    assert "We study X." in msgs[1]["content"] and "10.5281/zenodo.1" in msgs[1]["content"]


def test_create_post_payload(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers)
        return NS(status_code=201, headers={"x-restli-id": "urn:li:share:1"}, text="")
    monkeypatch.setattr(li.requests, "post", fake_post)
    urn = li.create_post("tok", "urn:li:person:1", "Hi (there) #AI", link="https://g.com/x",
                         link_title="x", version="202609")
    assert urn == "urn:li:share:1" and captured["url"] == li.POSTS_URL
    body = captured["json"]
    assert body["author"] == "urn:li:person:1" and body["visibility"] == "PUBLIC"
    assert body["commentary"] == r"Hi \(there\) {hashtag|\#|AI}"
    assert body["content"]["article"]["source"] == "https://g.com/x"
    assert captured["headers"]["LinkedIn-Version"] == "202609"


def test_default_api_version():
    assert li.default_api_version(dt.date(2026, 1, 15)) == "202511"
    assert li.default_api_version(dt.date(2026, 10, 28)) == "202608"


# ─── Sources ─────────────────────────────────────────────────────────────────

def test_fetch_github_filters_forks_and_excludes(monkeypatch):
    repos = [
        {"name": "keep", "full_name": "me/keep", "html_url": "u1", "description": "d",
         "fork": False, "archived": False, "pushed_at": "2026-10-01T00:00:00Z", "topics": ["ai"],
         "stargazers_count": 2, "language": "Python"},
        {"name": "forked", "full_name": "me/forked", "html_url": "u2", "fork": True},
        {"name": "old", "full_name": "me/old", "html_url": "u3", "fork": False, "archived": True},
        {"name": "Secret", "full_name": "me/Secret", "html_url": "u4", "fork": False, "archived": False},
    ]
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: NS(json=lambda: repos, raise_for_status=lambda: None))
    items = sources.fetch_github("me", exclude={"secret"})
    assert [i.key for i in items] == ["github:me/keep"]
    assert items[0].details["topics"] == ["ai"] and items[0].date == "2026-10-01T00:00:00"


def test_fetch_zenodo_dedupes_versions_and_strips_html(monkeypatch):
    hits = {"hits": {"hits": [
        {"id": 2, "conceptrecid": "100", "doi": "10.5281/zenodo.2",
         "links": {"self_html": "https://zenodo.org/records/2"},
         "metadata": {"title": "Paper v2", "description": "<p>We &amp; you</p>",
                      "publication_date": "2026-03-01", "creators": [{"name": "Daida, Kirit"}],
                      "resource_type": {"title": "Preprint"}}},
        {"id": 1, "conceptrecid": "100", "metadata": {"title": "Paper v1"}},
    ]}}
    seen = {}

    def get(url, params, headers, timeout):
        seen.update(params)
        return NS(json=lambda: hits, raise_for_status=lambda: None)
    monkeypatch.setattr(sources.requests, "get", get)
    items = sources.fetch_zenodo(sources.zenodo_query(author="Kirit Reddy Daida"))
    assert seen["q"] == "creators.name:(Kirit AND Reddy AND Daida)"
    assert len(items) == 1 and items[0].description == "We & you"
    assert items[0].details["type"] == "Preprint"
    assert sources.zenodo_query(orcid="0000-0001-2345-6789") == 'creators.orcid:"0000-0001-2345-6789"'
