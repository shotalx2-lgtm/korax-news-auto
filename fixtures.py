import os
import json
import time
import hashlib
import html
from io import BytesIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@KoraxNews11").strip()
FIXTURES_FILE = "fixtures.json"
STATE_FILE = "seen_matches.json"
CAIRO = ZoneInfo("Africa/Cairo")
LOOKAHEAD_HOURS = 24
MAX_POSTS_PER_RUN = 3
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 (compatible; KORAX-MATCH-CENTER/1.0)"})
LOGO_CACHE = {}


def get_leagues():
    with open(FIXTURES_FILE, encoding="utf-8") as file:
        leagues = json.load(file)
    if not isinstance(leagues, list) or not leagues:
        raise RuntimeError("fixtures.json must contain at least one league.")
    return leagues


def fetch_league(league, months):
    events = []
    league_name = league["name"]
    try:
        for month in months:
            response = HTTP.get(
                BASE_URL.format(slug=league["slug"]),
                params={"dates": month, "limit": 500},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("The scoreboard response was not a JSON object.")
            events.extend(payload.get("events") or [])
            league_meta = (payload.get("leagues") or [{}])[0]
            league_name = league_meta.get("name") or league_name
        return league, league_name, events, None
    except Exception as exc:
        return league, league_name, [], exc


def _parse_datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize_event(league, league_name, event):
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    kickoff = _parse_datetime(event.get("date") or competition.get("startDate"))
    if not kickoff:
        return None
    season = event.get("season") or {}
    stage = competition.get("altGameNote") or ""
    if "," in stage:
        stage = stage.split(",", 1)[1].strip()
    if not stage:
        season_type = season.get("type")
        if isinstance(season_type, dict):
            stage = season_type.get("name") or ""
    venue = competition.get("venue") or {}
    address = venue.get("address") or {}
    city = address.get("city") or ""
    country = address.get("country") or ""
    venue_name = venue.get("fullName") or ""
    venue_label = "، ".join(part for part in (venue_name, city, country) if part)
    round_info = event.get("week") or {}
    if isinstance(round_info, dict):
        round_name = round_info.get("text") or round_info.get("name") or ""
        if round_info.get("number") and not round_name:
            round_name = f"الجولة {round_info['number']}"
    else:
        round_name = str(round_info) if round_info else ""
    status = ((competition.get("status") or {}).get("type") or {}).get("state", "")
    return {
        "league_slug": league["slug"],
        "league": league.get("name") or league_name,
        "event_id": str(event.get("id") or ""),
        "kickoff": kickoff.isoformat(),
        "home": home_team.get("displayName") or home_team.get("name") or "الفريق المضيف",
        "away": away_team.get("displayName") or away_team.get("name") or "الفريق الضيف",
        "home_logo": home_team.get("logo") or "",
        "away_logo": away_team.get("logo") or "",
        "home_form": home.get("form") or "",
        "away_form": away.get("form") or "",
        "home_record": ((home.get("records") or [{}])[0]).get("summary", ""),
        "away_record": ((away.get("records") or [{}])[0]).get("summary", ""),
        "stage": stage,
        "round": round_name,
        "venue": venue_label,
        "status": status,
    }


def fixture_key(match):
    raw = f"{match['league_slug']}|{match['event_id']}|{match['kickoff']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_cairo_date(value):
    dt = value.astimezone(CAIRO)
    weekdays = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    months = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
    hour = dt.hour % 12 or 12
    period = "م" if dt.hour >= 12 else "ص"
    return {
        "date": f"{weekdays[dt.weekday()]} {dt.day} {months[dt.month - 1]} {dt.year}",
        "time": f"{hour}:{dt.minute:02d} {period}",
    }


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _rtl(value):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(value)))
    except Exception:
        return str(value)


def _download_logo(url):
    if not url:
        return None
    if url in LOGO_CACHE:
        return LOGO_CACHE[url]
    try:
        response = HTTP.get(url, timeout=9)
        response.raise_for_status()
        raw = response.content
        if len(raw) > 5 * 1024 * 1024:
            return None
        image = Image.open(BytesIO(raw)).convert("RGBA")
        image.thumbnail((184, 184), Image.Resampling.LANCZOS)
        LOGO_CACHE[url] = image
        return image
    except Exception as exc:
        print("Team badge could not be loaded:", repr(exc))
        LOGO_CACHE[url] = None
        return None


def _paste_badge(canvas, url, center, fallback, draw):
    x, y = center
    diameter = 194
    draw.ellipse((x - diameter // 2 - 8, y - diameter // 2 - 8,
                  x + diameter // 2 + 8, y + diameter // 2 + 8),
                 fill=(255, 255, 255), outline=(104, 220, 255), width=5)
    logo = _download_logo(url)
    if logo is None:
        draw.text((x, y), fallback[:3].upper(), font=_font(48, True), fill=(26, 72, 145), anchor="mm")
        return
    badge = Image.new("RGBA", (diameter, diameter), (255, 255, 255, 255))
    bx = (diameter - logo.width) // 2
    by = (diameter - logo.height) // 2
    badge.alpha_composite(logo, (bx, by))
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    canvas.paste(badge, (x - diameter // 2, y - diameter // 2), mask)


def _fit_text(draw, text, max_width, preferred=34, min_size=21):
    for size in range(preferred, min_size - 1, -1):
        font = _font(size, True)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
    return _font(min_size, True)


def make_match_card(match):
    width, height = 1200, 675
    top, bottom = (19, 48, 111), (42, 126, 198)
    canvas = Image.new("RGB", (width, height))
    pixels = canvas.load()
    for y in range(height):
        ratio = y / (height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(canvas)
    draw.ellipse((-170, 280, 360, 810), fill=(27, 83, 157))
    draw.ellipse((850, -270, 1390, 270), fill=(29, 97, 171))
    draw.rounded_rectangle((38, 30, 1162, 645), radius=38, fill=(24, 67, 135), outline=(117, 222, 255), width=3)
    draw.rounded_rectangle((72, 55, 390, 112), radius=17, fill=(81, 211, 246))
    draw.text((231, 83), "KORAX MATCH CENTER", font=_font(25, True), fill=(19, 57, 117), anchor="mm")

    league_text = _rtl(match.get("league", "بطولة كرة القدم"))
    draw.text((1125, 83), league_text, font=_fit_text(draw, league_text, 680, 32, 22), fill=(255, 255, 255), anchor="rm")

    _paste_badge(canvas, match.get("home_logo"), (345, 282), match.get("home", "HOME"), draw)
    _paste_badge(canvas, match.get("away_logo"), (855, 282), match.get("away", "AWAY"), draw)
    draw.text((600, 280), "VS", font=_font(48, True), fill=(151, 231, 255), anchor="mm")
    home_text, away_text = match.get("home", ""), match.get("away", "")
    draw.text((345, 410), home_text, font=_fit_text(draw, home_text, 420, 35), fill=(255, 255, 255), anchor="mm")
    draw.text((855, 410), away_text, font=_fit_text(draw, away_text, 420, 35), fill=(255, 255, 255), anchor="mm")

    kickoff = _parse_datetime(match["kickoff"]).astimezone(CAIRO)
    formatted = format_cairo_date(kickoff)
    draw.rounded_rectangle((105, 463, 1095, 555), radius=24, fill=(245, 251, 255), outline=(104, 220, 255), width=3)
    draw.text((600, 492), _rtl(formatted["date"]), font=_font(27, True), fill=(24, 67, 135), anchor="mm")
    draw.text((600, 531), _rtl("موعد المباراة: " + formatted["time"] + " بتوقيت مصر"), font=_font(27, True), fill=(24, 67, 135), anchor="mm")

    details = " • ".join(part for part in (match.get("stage"), match.get("round"), match.get("venue")) if part)
    if details:
        details = details[:110]
        details_rtl = _rtl(details)
        draw.text((600, 594), details_rtl, font=_fit_text(draw, details_rtl, 1020, 22, 16), fill=(239, 249, 255), anchor="mm")
    draw.text((1120, 615), "KORAX • FOOTBALL", font=_font(17, True), fill=(232, 248, 255), anchor="rm")

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=94, optimize=True, progressive=True, subsampling=0)
    output.seek(0)
    return output


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def build_caption(match):
    kickoff = _parse_datetime(match["kickoff"])
    formatted = format_cairo_date(kickoff)
    teams = f"{_escape(match['home'])} × {_escape(match['away'])}"
    parts = [
        f"⚽ <b>{teams}</b>",
        f"🏆 <b>البطولة:</b> {_escape(match['league'])}",
        f"📅 <b>تاريخ البداية:</b> {_escape(formatted['date'])}",
        f"⏰ <b>موعد المباراة:</b> {_escape(formatted['time'])} بتوقيت مصر",
    ]
    if match.get("stage"):
        parts.append(f"🏁 <b>المرحلة:</b> {_escape(match['stage'])}")
    if match.get("round"):
        parts.append(f"🔢 <b>الجولة:</b> {_escape(match['round'])}")
    if match.get("venue"):
        parts.append(f"🏟️ <b>الملعب:</b> {_escape(match['venue'])}")
    recent = []
    if match.get("home_form"):
        recent.append(f"{_escape(match['home'])}: {_escape(match['home_form'])}")
    if match.get("away_form"):
        recent.append(f"{_escape(match['away'])}: {_escape(match['away_form'])}")
    if recent:
        parts.append("📊 <b>آخر النتائج المتاحة:</b> " + " | ".join(recent))
    description = f"مباراة ضمن منافسات {_escape(match['league'])}"
    if match.get("stage"):
        description += f" في مرحلة {_escape(match['stage'])}"
    if match.get("venue"):
        description += f"، وتقام على ملعب {_escape(match['venue'])}"
    description += ". الموعد المعروض بتوقيت القاهرة، وقد يتغير موعد المباراة وفق تحديثات الجهة الناشرة."
    parts.append("📝 <b>وصف المباراة:</b> " + description)
    url = f"https://www.espn.com/soccer/match/_/gameId/{_escape(match['event_id'])}"
    parts.append(f'🔎 <a href="{url}">تفاصيل المباراة من المصدر</a>')
    parts.append("🔷 <b>KORAX MATCH CENTER</b> ◻️")
    return "\n\n".join(parts)[:1024]


def _telegram(method, data=None, files=None):
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN secret.")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, data=data or {}, files=files, timeout=45)
    payload = response.json()
    if not response.ok or not payload.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {payload.get('description', response.text[:250])}")
    return payload["result"]


def load_seen():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            payload = json.load(file)
        return set(payload.get("seen", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump({"seen": sorted(seen)[-12000:]}, file, ensure_ascii=False, indent=2)


def main():
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=LOOKAHEAD_HOURS)
    months = sorted({now.strftime("%Y%m"), end.strftime("%Y%m")})
    leagues = get_leagues()
    results = []
    healthy = 0

    with ThreadPoolExecutor(max_workers=min(10, len(leagues))) as pool:
        futures = [pool.submit(fetch_league, league, months) for league in leagues]
        for future in as_completed(futures):
            league, league_name, events, error = future.result()
            if error:
                print(f"Fixture source failed for {league['name']} ({league['slug']}): {error!r}")
                continue
            healthy += 1
            for event in events:
                match = normalize_event(league, league_name, event)
                if not match:
                    continue
                kickoff = _parse_datetime(match["kickoff"])
                if kickoff and now <= kickoff <= end and match.get("status") != "post":
                    results.append(match)

    if healthy == 0:
        raise RuntimeError("Every ESPN scoreboard request failed; no fixture data could be checked.")

    unique = {fixture_key(match): match for match in results}
    matches = sorted(unique.values(), key=lambda item: item["kickoff"])
    seen = load_seen()
    pending = [match for match in matches if fixture_key(match) not in seen]
    print(f"Fixture scan: {healthy}/{len(leagues)} competitions reachable; "
          f"{len(matches)} upcoming matches in the next {LOOKAHEAD_HOURS} hours; "
          f"{len(pending)} not published yet.")

    published = 0
    for match in pending[:MAX_POSTS_PER_RUN]:
        try:
            photo = make_match_card(match)
            _telegram(
                "sendPhoto",
                data={"chat_id": CHAT_ID, "caption": build_caption(match), "parse_mode": "HTML"},
                files={"photo": ("korax-match.jpg", photo, "image/jpeg")},
            )
            seen.add(fixture_key(match))
            save_seen(seen)
            published += 1
            print(f"Published: {match['home']} vs {match['away']} | {match['league']} | {match['kickoff']}")
            time.sleep(1)
        except Exception as exc:
            print(f"Could not publish {match.get('home')} vs {match.get('away')}: {exc!r}")

    print(f"Fixture run finished: {published} match cards published.")


if __name__ == "__main__":
    main()
