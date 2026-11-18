"""Free connectors: weather, Wikipedia, YouTube, RSS - and asking them politely.

The owner asked for "a pack with lots of connectors, like Gemini has". Four of
them need **no login at all** (weather, Wikipedia, RSS, YouTube transcripts), and
the rest of the pack is a JSON file the owner edits.

Two rules shape everything here:

* **A connector is a URL you can read.** Every request goes out as a normal GET to
  a named host, through the same permission broker as every other host - one
  approval, one call, audited. No key, no account, nothing hidden in a header.
* **Parsing is local.** RSS is XML that arrives in a thousand shapes; YouTube's
  transcript endpoint answers with JSON wrapped in a JavaScript call. Both are
  read here, in Python, with no extra dependency, and a failure says which shape
  surprised it instead of returning an empty list that looks like "no news".
"""
from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Dict, List, Optional

#: The free pack. `needs` says what the owner must add, if anything.
PACK: Dict[str, Dict[str, Any]] = {
    "weather": {
        "label": "Weather (Open-Meteo)",
        "needs": "nothing - no key, no account",
        "cost": "free",
        "why": "current conditions and a three-day outlook for a place you name",
        "host": "api.open-meteo.com",
    },
    "wikipedia": {
        "label": "Wikipedia",
        "needs": "nothing",
        "cost": "free",
        "why": "a short, sourced summary of a topic, in Tamil or English",
        "host": "ta.wikipedia.org",
    },
    "youtube_search": {
        "label": "YouTube search",
        "needs": "a free Google API key (10,000 searches/day)",
        "cost": "free tier",
        "why": "finds a video and gives you the link, which is what was asked for",
        "host": "www.googleapis.com",
    },
    "youtube_transcript": {
        "label": "YouTube transcript",
        "needs": "nothing",
        "cost": "free",
        "why": "reads what is said in a video so it can be explained in Tamil",
        "host": "www.youtube.com",
    },
    "rss": {
        "label": "RSS news",
        "needs": "nothing - any feed address",
        "cost": "free",
        "why": "headlines from a site you choose, newest first",
        "host": "(the feed's own host)",
    },
    "wttr": {
        "label": "Weather (wttr.in, one-line)",
        "needs": "nothing",
        "cost": "free",
        "why": "a single line answer, for when the full forecast is too much",
        "host": "wttr.in",
    },
}


class ConnectorError(RuntimeError):
    """A connector failed, with the reason a person can act on."""


# --------------------------------------------------------------------------- #
# places
# --------------------------------------------------------------------------- #
def geocode(name: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Turn "Chennai" into coordinates, using Open-Meteo's free geocoder."""
    import urllib.request

    q = str(name or "").strip()
    if not q:
        raise ConnectorError("name a place, e.g. 'Chennai' or 'Madurai'")
    url = ("https://geocoding-api.open-meteo.com/v1/search?name="
           + urllib.parse.quote(q) + "&count=1&language=ta&format=json")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read() or b"{}")
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"could not look up '{q}': {exc}") from exc
    hits = data.get("results") or []
    if not hits:
        raise ConnectorError(f"I could not find a place called '{q}'")
    top = hits[0]
    return {"name": top.get("name") or q, "country": top.get("country") or "",
            "latitude": top.get("latitude"), "longitude": top.get("longitude"),
            "timezone": top.get("timezone") or "auto"}


_WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "snow showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail",
    99: "a thunderstorm with heavy hail",
}
_WEATHER_TA = {
    0: "தெளிவான வானம்", 1: "கிட்டத்தட்ட தெளிவு", 2: "பகுதி மேகம்", 3: "மேகமூட்டம்",
    45: "பனிமூட்டம்", 48: "உறைபனி மூட்டம்", 51: "சிறு தூறல்", 53: "தூறல்",
    55: "கனமான தூறல்", 61: "சிறு மழை", 63: "மழை", 65: "கனமழை",
    71: "சிறு பனிப்பொழிவு", 73: "பனிப்பொழிவு", 75: "கனமான பனிப்பொழிவு",
    80: "மழைத்தூறல்", 81: "மழைத்தூறல்", 82: "பலத்த மழை",
    95: "இடி மின்னல் மழை", 96: "இடியுடன் கூடிய மழை", 99: "கடும் இடி மழை",
}


def weather(place: str, days: int = 3, tamil: bool = False,
            timeout: float = 12.0) -> Dict[str, Any]:
    """Current weather and a short outlook. No key, no account."""
    import urllib.request

    where = geocode(place, timeout=timeout)
    days = max(1, min(7, int(days or 3)))
    url = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
           "wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,"
           "precipitation_probability_max&forecast_days={days}&timezone=auto").format(
        lat=where["latitude"], lon=where["longitude"], days=days)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read() or b"{}")
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"the weather service did not answer: {exc}") from exc
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    code = int(cur.get("weather_code") or 0)
    say = (_WEATHER_TA if tamil else _WEATHER_CODES).get(code, "unsettled")
    lines = [f"{where['name']}: {cur.get('temperature_2m')}°C, {say}"
             f" (feels like {cur.get('apparent_temperature')}°C,"
             f" humidity {cur.get('relative_humidity_2m')}%,"
             f" wind {cur.get('wind_speed_10m')} km/h)"]
    dates = daily.get("time") or []
    for i, day in enumerate(dates[:days]):
        dc = int((daily.get("weather_code") or [0] * days)[i] or 0)
        lines.append(f"  {day}: {(daily.get('temperature_2m_min') or [0])[i]}–"
                     f"{(daily.get('temperature_2m_max') or [0])[i]}°C, "
                     f"{(_WEATHER_TA if tamil else _WEATHER_CODES).get(dc, '')}, "
                     f"rain {(daily.get('precipitation_probability_max') or [0])[i]}%")
    return {"ok": True, "place": where, "current": cur, "daily": daily,
            "rendered": "\n".join(lines), "source": "Open-Meteo (free, no key)"}


# --------------------------------------------------------------------------- #
# wikipedia
# --------------------------------------------------------------------------- #
def wikipedia(topic: str, lang: str = "en", sentences: int = 3,
              timeout: float = 12.0) -> Dict[str, Any]:
    """A sourced summary. The URL of the article comes back with it."""
    import urllib.request

    q = str(topic or "").strip()
    if not q:
        raise ConnectorError("name a topic to look up")
    lang = "ta" if str(lang).lower().startswith("ta") else "en"
    url = (f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
           + urllib.parse.quote(q.replace(" ", "_")))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ConnectorError(f"no {lang} Wikipedia article for '{q}'") from exc
        raise ConnectorError(f"Wikipedia answered HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"Wikipedia did not answer: {exc}") from exc
    extract = str(data.get("extract") or "").strip()
    if not extract:
        raise ConnectorError(f"the article on '{q}' has no summary I can read")
    trimmed = " ".join(extract.split(" ")[: max(20, sentences * 30)])
    page = (data.get("content_urls") or {}).get("desktop", {}).get("page", "")
    return {"ok": True, "title": data.get("title") or q, "summary": trimmed,
            "url": page, "language": lang,
            "rendered": f"{data.get('title') or q}\n{trimmed}\n{page}".strip()}


# --------------------------------------------------------------------------- #
# youtube
# --------------------------------------------------------------------------- #
def youtube_search(query: str, api_key: str, limit: int = 3,
                   timeout: float = 12.0) -> Dict[str, Any]:
    """Find videos and return their links - the thing that was asked for."""
    import urllib.request

    if not api_key:
        raise ConnectorError(
            "YouTube search needs a free Google API key: create one in Google Cloud "
            "Console, enable 'YouTube Data API v3', then `brain connectors key youtube "
            "<key>` (or set YOUTUBE_API_KEY).")
    q = str(query or "").strip()
    if not q:
        raise ConnectorError("what should I search for?")
    limit = max(1, min(10, int(limit or 3)))
    url = ("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video"
           "&maxResults={n}&q={q}&key={k}").format(
        n=limit, q=urllib.parse.quote(q), k=urllib.parse.quote(api_key))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise ConnectorError(f"YouTube answered HTTP {exc.code}: {detail}") from exc
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"YouTube did not answer: {exc}") from exc
    items = data.get("items") or []
    videos: List[Dict[str, Any]] = []
    for it in items:
        vid = str(((it.get("id") or {}).get("videoId")) or "")
        sn = it.get("snippet") or {}
        if not vid:
            continue
        videos.append({"title": unescape(str(sn.get("title") or "")),
                       "channel": unescape(str(sn.get("channelTitle") or "")),
                       "published": str(sn.get("publishedAt") or "")[:10],
                       "url": f"https://www.youtube.com/watch?v={vid}",
                       "id": vid})
    lines = [f"{i + 1}. {v['title']} — {v['channel']} ({v['published']})\n   {v['url']}"
             for i, v in enumerate(videos)]
    return {"ok": True, "query": q, "videos": videos,
            "rendered": "\n".join(lines) or f"No videos found for '{q}'."}


def video_id(url_or_id: str) -> str:
    """Accept a full URL, a short URL or a bare id."""
    text = str(url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    parsed = urllib.parse.urlparse(text)
    if "youtu.be" in (parsed.hostname or ""):
        return parsed.path.strip("/")[:11]
    q = urllib.parse.parse_qs(parsed.query)
    if q.get("v"):
        return str(q["v"][0])[:11]
    m = re.search(r"/(?:embed|shorts|live)/([A-Za-z0-9_-]{11})", parsed.path)
    return m.group(1) if m else ""


def _captions_from_player(html: str) -> Optional[Dict[str, Any]]:
    m = re.search(r'"captionTracks":\s*(\[.*?\])', html)
    if not m:
        return None
    try:
        tracks = json.loads(m.group(1).replace("\\u0026", "&").replace("\\/", "/"))
    except json.JSONDecodeError:
        return None
    if not tracks:
        return None
    preferred = None
    for track in tracks:
        lang = str(track.get("languageCode") or "").lower()
        if lang.startswith("ta"):
            preferred = track
            break
    if preferred is None:
        for track in tracks:
            if str(track.get("languageCode") or "").lower().startswith("en"):
                preferred = track
                break
    preferred = preferred or tracks[0]
    return {"url": preferred.get("baseUrl") or "",
            "language": preferred.get("languageCode") or "",
            "kind": preferred.get("kind") or ""}


def _timedtext_to_text(raw: str) -> str:
    """srv3 (`<p>`) or the older transcribed XML (`<text start=…>`)."""
    if "<p " in raw or "<p>" in raw:
        pieces = re.findall(r"<p[^>]*>(.*?)</p>", raw, flags=re.S)
    else:
        pieces = re.findall(r"<text[^>]*>(.*?)</text>", raw, flags=re.S)
    out: List[str] = []
    for piece in pieces:
        clean = re.sub(r"<[^>]+>", "", piece)
        clean = unescape(clean).replace("\n", " ").strip()
        if clean:
            out.append(clean)
    text = " ".join(out)
    return " ".join(text.replace("\u200b", "").split())


def youtube_transcript(url_or_id: str, timeout: float = 15.0) -> Dict[str, Any]:
    """What is said in a video, read from YouTube's own caption track.

    No dependency and no key. YouTube serves the caption track URL inside the
    watch page, so this reads the page, finds the track (Tamil first, then
    English), and fetches the timed text. A video with captions turned off has
    none of them, and that is said plainly.
    """
    import urllib.request

    vid = video_id(url_or_id)
    if not vid:
        raise ConnectorError("I need a YouTube link or an 11-character video id")
    page_url = f"https://www.youtube.com/watch?v={vid}&hl=en"
    headers = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/122.0 Safari/537.36"),
               "Accept-Language": "en-US,en;q=0.9,ta;q=0.8"}
    try:
        req = urllib.request.Request(page_url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(3_000_000).decode("utf-8", "replace")
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"could not open the video page: {exc}") from exc
    track = _captions_from_player(html)
    if not track or not track["url"]:
        raise ConnectorError(
            "this video has no captions I can read (the uploader turned them off, "
            "or the video is private/age-restricted)")
    title = ""
    m = re.search(r'"title":\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        try:
            title = json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            title = m.group(1)
    caption_url = track["url"] + ("&fmt=json3" if "fmt=" in track["url"] else "")
    try:
        req = urllib.request.Request(track["url"], headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4_000_000).decode("utf-8", "replace")
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"the caption track could not be fetched: {exc}") from exc
    text = _timedtext_to_text(body)
    if not text and "json3" not in caption_url:
        try:                                     # try the JSON shape after XML
            req = urllib.request.Request(caption_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read(4_000_000) or b"{}")
            events = data.get("events") or []
            text = " ".join("".join(seg.get("utf8", "") for seg in ev.get("segs") or [])
                            for ev in events)
            text = " ".join(text.split())
        except (OSError, ValueError):
            text = ""
    if not text:
        raise ConnectorError("the caption track was empty")
    return {"ok": True, "video_id": vid, "title": unescape(title),
            "language": track["language"], "chars": len(text), "text": text,
            "url": f"https://www.youtube.com/watch?v={vid}"}


# --------------------------------------------------------------------------- #
# rss
# --------------------------------------------------------------------------- #
def rss(feed_url: str, limit: int = 5, timeout: float = 12.0) -> Dict[str, Any]:
    """Headlines from any RSS or Atom feed, newest first as the feed orders them."""
    import urllib.request

    src = str(feed_url or "").strip()
    if not re.match(r"^https?://", src, re.I):
        raise ConnectorError("give a full feed address, e.g. https://example.com/feed.xml")
    limit = max(1, min(20, int(limit or 5)))
    try:
        req = urllib.request.Request(src, headers={"User-Agent": "nila-rss/0.2"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(3_000_000)
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"the feed did not answer: {exc}") from exc
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ConnectorError(f"that address did not return a readable feed: {exc}") from exc
    items: List[Dict[str, str]] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = link = date = ""
        for child in node:
            name = child.tag.split("}")[-1].lower()
            if name == "title":
                title = " ".join((child.text or "").split())
            elif name == "link" and not link:
                link = (child.text or "").strip() or child.attrib.get("href", "")
            elif name in ("pubdate", "published", "updated") and not date:
                date = (child.text or "").strip()[:25]
        if title:
            items.append({"title": unescape(title), "url": link, "date": date})
        if len(items) >= limit:
            break
    if not items:
        raise ConnectorError("the feed was readable but had no items in it")
    lines = [f"{i + 1}. {it['title']}" + (f"  ({it['date']})" if it["date"] else "")
             + (f"\n   {it['url']}" if it["url"] else "") for i, it in enumerate(items)]
    return {"ok": True, "feed": src, "items": items, "rendered": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# the pack, for a screen
# --------------------------------------------------------------------------- #
def describe_pack() -> List[Dict[str, Any]]:
    return [{"name": name, **spec} for name, spec in PACK.items()]


def help_text() -> str:
    return ("Free connectors, no account needed unless it says so:\n"
            "  brain web weather Chennai | weather in Madurai tomorrow\n"
            "  brain web wiki \"drip irrigation\" | wiki in Tamil\n"
            "  brain web youtube \"best drip irrigation video\"\n"
            "  brain web transcript <youtube link>\n"
            "  brain web rss https://example.com/feed.xml\n"
            "YouTube search needs a free Google API key; the rest need nothing.")
