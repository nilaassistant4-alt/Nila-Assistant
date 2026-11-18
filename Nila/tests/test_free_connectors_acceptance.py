"""Acceptance tests: stage 5 - the free connector pack and `brain start`.

Weather (Open-Meteo), Wikipedia, YouTube search, YouTube transcripts and RSS are
the four-and-a-half connectors that need no login. Each one is a plain GET to a
named host, so each one is parsed here against the real shapes those services
return, with the network stubbed out - the sandbox has no route to them, and a
test that silently skipped would be worse than no test at all.

Run:  python -m pytest tests/test_free_connectors_acceptance.py -q
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from brain.agent import web


# --------------------------------------------------------------------------- #
# a tiny fake urlopen, so each shape can be exercised exactly
# --------------------------------------------------------------------------- #
class FakeResp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, url: str = ""):
        super().__init__(body)
        self.status = status
        self.url = url
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def net(monkeypatch):
    """Answer by URL prefix; anything unexpected is a test failure, not a skip."""
    routes = {}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for prefix, answer in routes.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                body, status = answer if isinstance(answer, tuple) else (answer, 200)
                return FakeResp(body if isinstance(body, bytes) else body.encode(),
                                status=status, url=url)
        raise AssertionError(f"unexpected network call: {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return routes


# --------------------------------------------------------------------------- #
# weather
# --------------------------------------------------------------------------- #
def test_weather_with_no_key(net):
    net["https://geocoding-api.open-meteo.com/"] = json.dumps({
        "results": [{"name": "Chennai", "country": "India", "latitude": 13.08,
                     "longitude": 80.27, "timezone": "Asia/Kolkata"}]})
    net["https://api.open-meteo.com/"] = json.dumps({
        "current": {"temperature_2m": 31.4, "relative_humidity_2m": 68,
                    "apparent_temperature": 36.1, "weather_code": 2,
                    "wind_speed_10m": 12.6},
        "daily": {"time": ["2026-11-04", "2026-11-05"],
                  "weather_code": [2, 61],
                  "temperature_2m_max": [32, 30],
                  "temperature_2m_min": [25, 24],
                  "precipitation_probability_max": [10, 70]}})
    out = web.weather("Chennai", days=2)
    assert out["ok"] and out["place"]["name"] == "Chennai"
    assert "31.4°C" in out["rendered"] and "partly cloudy" in out["rendered"]
    assert "70%" in out["rendered"]
    assert "no key" in out["source"]


def test_weather_in_tamil_answers_in_tamil(net):
    net["https://geocoding-api.open-meteo.com/"] = json.dumps({
        "results": [{"name": "Madurai", "latitude": 9.9, "longitude": 78.1}]})
    net["https://api.open-meteo.com/"] = json.dumps({
        "current": {"temperature_2m": 30, "relative_humidity_2m": 70,
                    "apparent_temperature": 34, "weather_code": 61,
                    "wind_speed_10m": 8},
        "daily": {"time": [], "weather_code": [], "temperature_2m_max": [],
                  "temperature_2m_min": [], "precipitation_probability_max": []}})
    out = web.weather("Madurai", days=1, tamil=True)
    assert "மழை" in out["rendered"]


def test_an_unknown_place_is_named_not_guessed(net):
    net["https://geocoding-api.open-meteo.com/"] = json.dumps({"results": []})
    with pytest.raises(web.ConnectorError) as exc:
        web.weather("Atlantis")
    assert "Atlantis" in str(exc.value)


# --------------------------------------------------------------------------- #
# wikipedia
# --------------------------------------------------------------------------- #
def test_wikipedia_returns_a_summary_and_its_source(net):
    net["https://en.wikipedia.org/"] = json.dumps({
        "title": "Drip irrigation", "extract": "Drip irrigation is a type of micro-irrigation.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Drip_irrigation"}}})
    out = web.wikipedia("drip irrigation")
    assert out["ok"] and out["url"].endswith("Drip_irrigation")
    assert "micro-irrigation" in out["rendered"]


def test_wikipedia_can_answer_in_tamil(net):
    net["https://ta.wikipedia.org/"] = json.dumps({
        "title": "தமிழ்", "extract": "தமிழ் ஒரு மொழி.",
        "content_urls": {"desktop": {"page": "https://ta.wikipedia.org/wiki/தமிழ்"}}})
    out = web.wikipedia("தமிழ்", lang="ta")
    assert out["language"] == "ta" and "மொழி" in out["summary"]


def test_a_missing_article_is_said_plainly(net):
    err = urllib.error.HTTPError("u", 404, "not found", {}, None)
    net["https://en.wikipedia.org/"] = err
    with pytest.raises(web.ConnectorError) as exc:
        web.wikipedia("qwertyuiopasdf")
    assert "no en Wikipedia article" in str(exc.value)


# --------------------------------------------------------------------------- #
# youtube
# --------------------------------------------------------------------------- #
def test_youtube_search_gives_links(net):
    net["https://www.googleapis.com/"] = json.dumps({"items": [
        {"id": {"videoId": "abc12345678"},
         "snippet": {"title": "Drip irrigation explained", "channelTitle": "Farm TV",
                     "publishedAt": "2026-01-02T10:00:00Z"}}]})
    out = web.youtube_search("drip irrigation", "KEY")
    assert out["ok"] and out["videos"][0]["url"] == "https://www.youtube.com/watch?v=abc12345678"
    assert "Farm TV" in out["rendered"]


def test_youtube_search_without_a_key_says_where_to_get_one():
    with pytest.raises(web.ConnectorError) as exc:
        web.youtube_search("anything", "")
    assert "api key" in str(exc.value).lower() and "YouTube Data API v3" in str(exc.value)


@pytest.mark.parametrize("given,expect", [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=3", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("not a link", ""),
])
def test_every_youtube_link_shape_is_understood(given, expect):
    assert web.video_id(given) == expect


def test_a_transcript_is_read_from_the_page(net):
    page = ('<html>{"title":"Test video",'
            '"captionTracks":[{"baseUrl":"https://www.youtube.com/api/timedtext?x=1",'
            '"languageCode":"ta"}]}</html>')
    net["https://www.youtube.com/watch"] = page
    net["https://www.youtube.com/api/timedtext"] = (
        '<transcript><text start="0">வணக்கம்</text>'
        '<text start="1">இது ஒரு சோதனை</text></transcript>')
    out = web.youtube_transcript("https://youtu.be/dQw4w9WgXcQ")
    assert "வணக்கம்" in out["text"] and out["language"] == "ta"
    assert out["title"] == "Test video"


def test_a_video_without_captions_is_reported_honestly(net):
    net["https://www.youtube.com/watch"] = "<html>no tracks here</html>"
    with pytest.raises(web.ConnectorError) as exc:
        web.youtube_transcript("dQw4w9WgXcQ")
    assert "no captions" in str(exc.value)


def test_srv3_and_old_caption_formats_both_parse():
    assert web._timedtext_to_text(
        '<timedtext><body><p t="0">hi <s>there</s></p></body></timedtext>') == "hi there"
    assert web._timedtext_to_text(
        '<transcript><text start="0">hello</text></transcript>') == "hello"


# --------------------------------------------------------------------------- #
# rss
# --------------------------------------------------------------------------- #
def test_rss_headlines(net):
    net["https://example.com/feed.xml"] = """<?xml version="1.0"?>
      <rss><channel>
        <item><title>First story</title><link>https://example.com/1</link>
              <pubDate>Mon, 03 Nov 2026 10:00:00 GMT</pubDate></item>
        <item><title>Second &amp; last</title><link>https://example.com/2</link></item>
      </channel></rss>"""
    out = web.rss("https://example.com/feed.xml", limit=2)
    assert out["items"][0]["title"] == "First story"
    assert out["items"][1]["title"] == "Second & last"
    assert "https://example.com/1" in out["rendered"]


def test_an_atom_feed_works_too(net):
    net["https://example.com/atom"] = """<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Atom story</title><link href="https://example.com/a"/></entry>
      </feed>"""
    out = web.rss("https://example.com/atom", limit=1)
    assert out["items"][0]["title"] == "Atom story"
    assert out["items"][0]["url"] == "https://example.com/a"


def test_a_feed_that_is_not_xml_is_refused(net):
    net["https://example.com/x"] = "<html>not a feed</html>"
    with pytest.raises(web.ConnectorError):
        web.rss("https://example.com/x")


def test_a_feed_address_must_be_a_full_url():
    with pytest.raises(web.ConnectorError):
        web.rss("example.com/feed")


# --------------------------------------------------------------------------- #
# the pack description
# --------------------------------------------------------------------------- #
def test_the_pack_says_what_each_connector_needs():
    rows = {r["name"]: r for r in web.describe_pack()}
    assert set(rows) >= {"weather", "wikipedia", "youtube_search",
                         "youtube_transcript", "rss"}
    assert "nothing" in rows["weather"]["needs"]
    assert "key" in rows["youtube_search"]["needs"]
    assert all(r["why"] for r in rows.values())


def test_the_connectors_are_network_gated_by_the_broker(config, vault, memory):
    """Each connector tool must declare the net capability, or it would bypass
    the one-approval-one-call rule that already protects integrations."""
    from brain.agent.agent import Brain
    from brain.security.permission import CAP_NET

    brain = Brain(config, vault, memory)
    for name in ("web_weather", "web_wikipedia", "web_youtube_search",
                 "web_youtube_transcript", "web_rss"):
        spec = brain.registry.get(name)
        assert spec is not None, name
        assert spec.capability == CAP_NET, name


def test_a_connector_call_without_permission_asks_instead_of_calling(config, vault, memory,
                                                                     monkeypatch):
    from brain.agent.agent import Brain

    def boom(*a, **k):
        raise AssertionError("nothing may go out before the owner affirms")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    brain = Brain(config, vault, memory)
    res = brain.registry.call("web_weather", {"place": "Chennai"}, brain.tool_ctx)
    assert res["ok"] is False and res["error"] == "permission required"
    assert res["capability"] == "net.host"


# --------------------------------------------------------------------------- #
# brain start
# --------------------------------------------------------------------------- #
def test_start_is_registered_and_needs_auth():
    from brain.cli.main import build_parser

    args = build_parser().parse_args(["start"])
    assert args.func.__name__ == "cmd_start"
    assert hasattr(args, "passphrase_env") and args.port == 8765
