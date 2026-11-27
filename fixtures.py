import os
import json
import time
import hashlib
import html
import re
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

TEAM_NAMES_AR = {
    "chelsea":"تشيلسي","nottingham forest":"نوتينجهام فورست","nottm forest":"نوتينجهام فورست",
    "arsenal":"أرسنال","liverpool":"ليفربول","manchester city":"مانشستر سيتي","man city":"مانشستر سيتي",
    "manchester united":"مانشستر يونايتد","man united":"مانشستر يونايتد","tottenham hotspur":"توتنهام","tottenham":"توتنهام",
    "newcastle united":"نيوكاسل يونايتد","aston villa":"أستون فيلا","west ham united":"وست هام يونايتد","west ham":"وست هام",
    "brighton and hove albion":"برايتون","brighton":"برايتون","crystal palace":"كريستال بالاس","brentford":"برينتفورد",
    "everton":"إيفرتون","fulham":"فولهام","bournemouth":"بورنموث","wolves":"وولفرهامبتون",
    "wolverhampton wanderers":"وولفرهامبتون","burnley":"بيرنلي","sunderland":"سندرلاند","leeds united":"ليدز يونايتد",
    "real madrid":"ريال مدريد","barcelona":"برشلونة","atletico madrid":"أتلتيكو مدريد","athletic club":"أتلتيك بلباو",
    "real sociedad":"ريال سوسيداد","villarreal":"فياريال","real betis":"ريال بيتيس","betis":"ريال بيتيس",
    "sevilla":"إشبيلية","valencia":"فالنسيا","girona":"جيرونا","osasuna":"أوساسونا","getafe":"خيتافي",
    "celta vigo":"سيلتا فيجو","juventus":"يوفنتوس","inter":"إنتر ميلان","internazionale":"إنتر ميلان",
    "inter milan":"إنتر ميلان","ac milan":"ميلان","milan":"ميلان","napoli":"نابولي","roma":"روما",
    "lazio":"لاتسيو","fiorentina":"فيورنتينا","atalanta":"أتالانتا","bologna":"بولونيا","torino":"تورينو",
    "bayern munich":"بايرن ميونخ","bayern":"بايرن ميونخ","borussia dortmund":"بوروسيا دورتموند",
    "dortmund":"بوروسيا دورتموند","bayer leverkusen":"باير ليفركوزن","rb leipzig":"لايبزيج","leipzig":"لايبزيج",
    "eintracht frankfurt":"آينتراخت فرانكفورت","stuttgart":"شتوتجارت","wolfsburg":"فولفسبورج",
    "paris saint-germain":"باريس سان جيرمان","paris saint germain":"باريس سان جيرمان","psg":"باريس سان جيرمان",
    "marseille":"مارسيليا","monaco":"موناكو","lyon":"ليون","al hilal":"الهلال","al-hilal":"الهلال",
    "al nassr":"النصر","al-nassr":"النصر","al ahli":"الأهلي","al-ahli":"الأهلي","al ittihad":"الاتحاد",
    "al-ittihad":"الاتحاد","al shabab":"الشباب","al-shabab":"الشباب","al ettifaq":"الاتفاق",
    "al fateh":"الفتح","al taawoun":"التعاون","al wehda":"الوحدة","al raed":"الرائد","al khaleej":"الخليج",
    "al hazem":"الحزم","al fayha":"الفيحاء","al qadsiah":"القادسية","al sadd":"السد","al duhail":"الدحيل",
    "al ain":"العين","al wasl":"الوصل","al ahly":"الأهلي","zamalek":"الزمالك","pyramids":"بيراميدز"
}
_LATIN_DIGRAPHS_AR = (("tch","تش"),("sch","ش"),("sh","ش"),("ch","تش"),("th","ث"),("gh","غ"),("kh","خ"),
    ("ph","ف"),("ck","ك"),("qu","كو"),("oo","و"),("ee","ي"),("ea","ي"),("ou","و"),("ow","او"),
    ("ai","اي"),("ay","اي"),("tion","شيون"))
_LATIN_LETTERS_AR = {"a":"ا","b":"ب","c":"ك","d":"د","e":"ي","f":"ف","g":"ج","h":"ه","i":"ي",
    "j":"ج","k":"ك","l":"ل","m":"م","n":"ن","o":"و","p":"ب","q":"ق","r":"ر","s":"س","t":"ت",
    "u":"و","v":"ڤ","w":"و","x":"كس","y":"ي","z":"ز"}

def arabic_team_name(value):
    """Use an Arabic club name; transliterate other Latin names as a fallback."""
    original = str(value or "").strip()
    if not original:
        return "الفريق"
    if re.search(r"[\u0600-\u06FF]", original):
        return original
    key = re.sub(r"[^a-z0-9]+", " ", original.lower()).strip()
    if key in TEAM_NAMES_AR:
        return TEAM_NAMES_AR[key]
    out, index = [], 0
    while index < len(key):
        for latin, arabic in _LATIN_DIGRAPHS_AR:
            if key.startswith(latin, index):
                out.append(arabic)
                index += len(latin)
                break
        else:
            char = key[index]
            out.append(" " if char == " " else char if char.isdigit() else _LATIN_LETTERS_AR.get(char, ""))
            index += 1
    result = re.sub(r"\s+", " ", "".join(out)).strip()
    return result or "الفريق"

def arabic_digits(value):
    return str(value).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))

def _league_logo_url(league_meta):
    if not isinstance(league_meta, dict):
        return ""
    candidates = [league_meta.get("logo")]
    for item in league_meta.get("logos") or []:
        if isinstance(item, dict):
            candidates.append(item.get("href") or item.get("url"))
    return next((u for u in candidates if isinstance(u, str) and u.startswith(("https://","http://"))), "")



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
            league_logo = _league_logo_url(league_meta)
            if league_logo:
                league = dict(league)
                league["logo"] = league_logo
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


def _team_logo(team):
    """Return the best real badge URL exposed by ESPN, without inventing one."""
    candidates = [team.get("logo")]
    logos = team.get("logos") or []
    if isinstance(logos, list):
        candidates.extend(item.get("href") for item in logos if isinstance(item, dict))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("https://", "http://")):
            return candidate
    return ""


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
    status_type = ((competition.get("status") or {}).get("type") or {})
    status = (status_type.get("state") or "").lower()
    status_name = (status_type.get("name") or "").lower()
    if status in {"post", "final"} or "final" in status_name or "full time" in status_name:
        status = "post"
    return {
        "league_slug": league["slug"],
        "league": league.get("name") or league_name,
        "league_logo": league.get("logo") or "",
        "event_id": str(event.get("id") or ""),
        "kickoff": kickoff.isoformat(),
        "home": home_team.get("displayName") or home_team.get("name") or "الفريق المضيف",
        "away": away_team.get("displayName") or away_team.get("name") or "الفريق الضيف",
        "home_logo": _team_logo(home_team),
        "away_logo": _team_logo(away_team),
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
        image.thumbnail((160, 160), Image.Resampling.LANCZOS)
        LOGO_CACHE[url] = image
        return image
    except Exception as exc:
        print("Team badge could not be loaded:", repr(exc))
        LOGO_CACHE[url] = None
        return None


def _paste_badge(canvas, url, center, fallback, draw, diameter=190):
    x, y = center
    draw.ellipse((x-diameter//2-9,y-diameter//2-9,x+diameter//2+9,y+diameter//2+9),
                 fill=(255,255,255), outline=(234,255,0), width=5)
    logo = _download_logo(url)
    if logo is None:
        draw.text((x,y), _rtl("الشعار غير متاح"), font=_font(20,True),
                  fill=(45,70,15), anchor="mm")
        return
    logo = logo.copy()
    logo.thumbnail((diameter-24, diameter-24), Image.Resampling.LANCZOS)
    badge = Image.new("RGBA",(diameter,diameter),(255,255,255,255))
    badge.alpha_composite(logo,((diameter-logo.width)//2,(diameter-logo.height)//2))
    mask = Image.new("L",(diameter,diameter),0)
    ImageDraw.Draw(mask).ellipse((0,0,diameter-1,diameter-1),fill=255)
    canvas.paste(badge,(x-diameter//2,y-diameter//2),mask)


def _fit_text(draw, text, max_width, preferred=34, min_size=21):
    for size in range(preferred, min_size - 1, -1):
        font = _font(size, True)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
    return _font(min_size, True)


def _draw_arabic_capsule(draw, bounds, text, font_size=42):
    draw.rounded_rectangle(bounds, radius=48, fill=(3,24,0), outline=(105,220,0), width=4)
    x=(bounds[0]+bounds[2])//2
    y=(bounds[1]+bounds[3])//2
    shaped=_rtl(text)
    font=_fit_text(draw,shaped,bounds[2]-bounds[0]-36,font_size,21)
    draw.text((x,y),shaped,font=font,fill=(255,255,255),anchor="mm")


def make_match_card(match):
    """Render the Arabic bright-green match poster using real club and league badges."""
    width,height=1200,675
    canvas=Image.new("RGB",(width,height),(2,5,1))
    card_box=(15,15,1185,660)
    cw,ch=card_box[2]-card_box[0],card_box[3]-card_box[1]
    gradient=Image.new("RGB",(cw,ch))
    pixels=gradient.load()
    top_right=(214,255,18)
    bottom_left=(10,62,2)
    for y in range(ch):
        for x in range(cw):
            blend=min(1.0,max(0.0,0.55*x/max(1,cw-1)+0.45*y/max(1,ch-1)))
            pixels[x,y]=tuple(int(top_right[k]*(1-blend)+bottom_left[k]*blend) for k in range(3))
    mask=Image.new("L",(cw,ch),0)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,cw-1,ch-1),radius=72,fill=255)
    canvas.paste(gradient,(card_box[0],card_box[1]),mask)
    draw=ImageDraw.Draw(canvas)
    draw.polygon([(15,510),(420,15),(610,15),(95,660),(15,660)],fill=(57,145,0))
    draw.polygon([(770,660),(1185,170),(1185,300),(900,660)],fill=(22,104,2))
    draw.rounded_rectangle(card_box,radius=72,outline=(133,235,0),width=6)

    league=_rtl(str(match.get("league") or "بطولة كرة القدم"))
    league_logo=match.get("league_logo")
    if league_logo:
        _paste_badge(canvas,league_logo,(1110,112),"",draw,diameter=82)
        league_bounds=(430,62,1030,150)
    else:
        league_bounds=(500,62,1110,150)
    league_font=_fit_text(draw,league,league_bounds[2]-league_bounds[0],44,24)
    draw.text(((league_bounds[0]+league_bounds[2])//2,(league_bounds[1]+league_bounds[3])//2),
              league,font=league_font,fill=(0,12,0),anchor="mm")

    kickoff=_parse_datetime(match["kickoff"]).astimezone(CAIRO)
    formatted=format_cairo_date(kickoff)
    draw.rounded_rectangle((52,55,365,164),radius=55,fill=(5,35,0),outline=(70,125,0),width=3)
    date_text=_rtl(arabic_digits(formatted["date"]))
    time_text=_rtl(arabic_digits(formatted["time"]))
    draw.text((208,88),date_text,font=_fit_text(draw,date_text,270,22,15),
              fill=(232,255,210),anchor="mm")
    draw.text((208,132),time_text,font=_font(38,True),fill=(255,255,255),anchor="mm")

    home_ar=arabic_team_name(match.get("home"))
    away_ar=arabic_team_name(match.get("away"))
    _paste_badge(canvas,match.get("home_logo"),(290,345),home_ar,draw)
    _paste_badge(canvas,match.get("away_logo"),(910,345),away_ar,draw)
    draw.text((600,345),_rtl("ضد"),font=_font(92,True),fill=(0,12,0),anchor="mm")
    _draw_arabic_capsule(draw,(105,510,475,594),home_ar,44)
    _draw_arabic_capsule(draw,(725,510,1095,594),away_ar,44)

    output=BytesIO()
    canvas.save(output,format="JPEG",quality=95,optimize=True,progressive=True,subsampling=0)
    output.seek(0)
    return output


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def build_caption(match):
    kickoff=_parse_datetime(match["kickoff"])
    formatted=format_cairo_date(kickoff)
    home_ar=arabic_team_name(match.get("home"))
    away_ar=arabic_team_name(match.get("away"))
    parts=[
        f"⚽ <b>{_escape(home_ar)} ضد {_escape(away_ar)}</b>",
        f"🏆 <b>البطولة:</b> {_escape(match.get('league') or 'بطولة كرة القدم')}",
        f"📅 <b>التاريخ:</b> {_escape(arabic_digits(formatted['date']))}",
        f"⏰ <b>الموعد:</b> {_escape(arabic_digits(formatted['time']))} بتوقيت القاهرة",
    ]
    if match.get("round"):
        round_name = str(match["round"])
        round_number = re.search(r"\\d+", round_name)
        if round_number:
            round_name = f"الجولة {arabic_digits(round_number.group())}"
        elif not re.search(r"[\\u0600-\\u06FF]", round_name):
            round_name = ""
        if round_name:
            parts.append(f"🔢 <b>الجولة:</b> {_escape(round_name)}")
    parts.append("🔷 <b>KORAX NEWS</b>")
    return "\n\n".join(parts)


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
                if kickoff and now < kickoff <= end and match.get("status") == "pre":
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
