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
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

GOAL_FIXTURES_URL = "https://www.goal.com/en-gb/fixtures/{date}"
GOAL_TIMEZONE = ZoneInfo("America/New_York")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@KoraxNews11").strip()
FIXTURES_FILE = "fixtures.json"
STATE_FILE = "seen_matches.json"
CAIRO = ZoneInfo("Africa/Cairo")
LOOKAHEAD_HOURS = 26
MAX_REMINDERS_PER_RUN = 6
REMINDER_WINDOWS = {
    "24h": (timedelta(hours=23, minutes=45), timedelta(hours=24, minutes=15)),
    "30m": (timedelta(minutes=20), timedelta(minutes=35)),
}
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
    "al ain":"العين","al wasl":"الوصل","al ahly":"الأهلي","zamalek":"الزمالك","pyramids":"بيراميدز",
    "al kholood":"الخلود","al khuluud":"الخلود","al khaleej":"الخليج","al kholood club":"الخلود",
    "al qadsiah":"القادسية","al-qadsiah":"القادسية","al fayha":"الفيحاء","al-fayha":"الفيحاء",
    "al ittihad":"الاتحاد","al-ittihad":"الاتحاد","neom":"نيوم","neom sc":"نيوم",
    "werder bremen":"فيردر بريمن","borussia moenchengladbach":"بوروسيا مونشنجلادباخ",
    "borussia mönchengladbach":"بوروسيا مونشنجلادباخ","monchengladbach":"مونشنجلادباخ",
    "venezia":"فينيسيا","bologna":"بولونيا","stade brestois 29":"بريست","brest":"بريست",
    "paris fc":"باريس إف سي","levante":"ليفانتي","racing santander":"راسينج سانتاندير",
    "amal tiznit":"أمل تزنيت","us amal tiznit":"أمل تزنيت","moghreb tetouan":"المغرب التطواني",
    "maghreb tetouan":"المغرب التطواني","azzam united":"عزام يونايتد","azam fc":"عزام يونايتد",
    "al ahly tripoli":"أهلي طرابلس","al ahli tripoli":"أهلي طرابلس",
    "ghaz al-shamal sc":"غاز الشمال","ghaz al shamal sc":"غاز الشمال",
    "ghaz al-shamal":"غاز الشمال","ghaz al shamal":"غاز الشمال",
    "al-karma":"الكرمة","al karma":"الكرمة",
    "zakho":"زاخو","karbalaa":"كربلاء","karbala":"كربلاء",
    "al-gharraf":"الغراف","al gharraf":"الغراف","gharraf":"الغراف",
    "al-mosul":"الموصل","al mosul":"الموصل","mosul":"الموصل",
    "al talaba":"الطلبة","al-talaba":"الطلبة","al minaa basra":"الميناء",
    "naft maysan":"نفط ميسان","naft":"النفط","al quwa al jawiya":"القوة الجوية",
    "al-jolan sc":"الجولان","al jolan sc":"الجولان","newroz":"نوروز",
    "al kahrabaa":"الكهرباء","al karkh":"الكرخ","al zawraa":"الزوراء",
    "erbil":"أربيل","duhok":"دهوك","diyala":"ديالى",
    "dinamo batumi":"دينامو باتومي","dila gori":"ديلا جوري"
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


class _GoalAnchorParser(HTMLParser):
    """Collect fixture anchors together with the closest preceding competition context."""
    VOID_TAGS = {"img", "br", "input", "source", "meta", "link", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.stack = []
        self.current_competition = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            image = (attrs.get("src") or attrs.get("data-src") or
                     attrs.get("data-lazy-src") or attrs.get("data-original") or "")
            if image.startswith(("http://", "https://", "/")):
                record = {
                    "url": image,
                    "alt": attrs.get("alt") or "",
                    "title": attrs.get("title") or "",
                    "class": attrs.get("class") or "",
                }
                for node in reversed(self.stack):
                    if node["tag"] == "a":
                        node["images"].append(image)
                        node["image_meta"].append(record)
                        break
            return
        if tag in self.VOID_TAGS:
            return

        node = {
            "tag": tag,
            "attrs": attrs,
            "text_parts": [],
            "images": [],
            "image_meta": [],
        }
        if tag == "a":
            node["href"] = attrs.get("href") or ""
            node["competition"] = self.current_competition
            # GOAL sometimes renders competition headings inside divs, not h-tags.
            # Resolve the most recent recognized heading in the nearest parent container.
            for ancestor in reversed(self.stack):
                context = " ".join(ancestor["text_parts"])
                detected, _ = _goal_competition_name(context)
                if detected:
                    node["competition"] = context[-800:]
                    break
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        for node in self.stack:
            node["text_parts"].append(data)

    def handle_endtag(self, tag):
        while self.stack and self.stack[-1]["tag"] != tag:
            self.stack.pop()
        if not self.stack:
            return
        node = self.stack.pop()
        if node["tag"] in ("h1", "h2", "h3", "h4"):
            heading = " ".join(node["text_parts"])
            if _goal_competition_name(heading)[0]:
                self.current_competition = heading
        if node["tag"] == "a":
            node["text"] = " ".join(node["text_parts"])
            node["images"] = list(dict.fromkeys(node["images"]))
            self.items.append(node)


_GOAL_ROW_PATTERN = re.compile(
    r"^(?:(?P<home_prefix>FK|FC|SK|AC|AS|SC|CD|CA|UD|SD|TSV|CSD|CS)\s+)?"
    r"(?P<home>.+?)\s+(?:(?P<home_suffix>SC|FC|AFC|CF|FK|AC|AS|CD|CA|UD|SD|SK|SV|TSV|CSD|CS)\s+)?"
    r"(?P<home_code>[A-Z0-9]{2,4})\s+"
    r"(?P<away>.+?)\s+(?P<away_code>[A-Z0-9]{2,4})\s+"
    r"(?P<time>\d{1,2}:\d{2})$"
)


def _goal_competition_name(raw):
    value = re.sub(r"\s+", " ", str(raw or "")).strip().lower()
    if not value or any(token in value for token in ("women", "femenil", "u18", "academy", "youth")):
        return "", ""

    mappings = (
        (("premier league", "england"), "الدوري الإنجليزي الممتاز", "eng.1"),
        (("premier league", "iraq"), "الدوري العراقي", "irq.1"),
        (("laliga",), "الدوري الإسباني", "esp.1"),
        (("la liga",), "الدوري الإسباني", "esp.1"),
        (("2. bundesliga",), "دوري الدرجة الثانية الألماني", "ger.2"),
        (("bundesliga", "germany"), "الدوري الألماني", "ger.1"),
        (("3. liga", "germany"), "دوري الدرجة الثالثة الألماني", "ger.3"),
        (("serie a", "italy"), "الدوري الإيطالي", "ita.1"),
        (("serie a", "brazil"), "الدوري البرازيلي", "bra.1"),
        (("serie c", "italy"), "دوري الدرجة الثالثة الإيطالي", "ita.3"),
        (("ligue 1",), "الدوري الفرنسي", "fra.1"),
        (("pro league", "saudi"), "دوري روشن السعودي", "ksa.1"),
        (("major league soccer",), "الدوري الأمريكي", "usa.1"),
        (("mls",), "الدوري الأمريكي", "usa.1"),
        (("championship", "england"), "دوري البطولة الإنجليزية", "eng.2"),
        (("national league", "england"), "دوري الدرجة الخامسة الإنجليزي", "eng.nl"),
        (("botola pro",), "الدوري المغربي", "mar.1"),
        (("confederation cup",), "كأس الكونفدرالية الإفريقية", "caf.confed"),
        (("caf champions",), "دوري أبطال إفريقيا", "caf.champions"),
        (("eredivisie",), "الدوري الهولندي", "ned.1"),
        (("eerste divisie",), "دوري الدرجة الثانية الهولندي", "ned.2"),
        (("liga portugal",), "الدوري البرتغالي", "por.1"),
        (("world cup qualification", "concacaf"), "تصفيات كأس العالم - الكونكاكاف", "fifa.worldq.concacaf"),
        (("primera a", "colombia"), "الدوري الكولومبي", "col.1"),
        (("liga super", "malaysia"), "الدوري الماليزي", "mys.1"),
        (("erovnuli liga",), "الدوري الجورجي", "geo.1"),
        (("adnoc pro league",), "الدوري الإماراتي", "uae.1"),
        (("iraqi stars",), "الدوري العراقي", "irq.1"),
        (("first league", "czechia"), "الدوري التشيكي", "cze.1"),
        (("fnl", "czechia"), "دوري الدرجة الثانية التشيكي", "cze.2"),
        (("super lig", "turkiye"), "الدوري التركي", "tur.1"),
        (("super lig", "türkiye"), "الدوري التركي", "tur.1"),
        (("prva liga", "serbia"), "الدوري الصربي", "srb.1"),
        (("prva liga", "slovenia"), "الدوري السلوفيني", "svn.1"),
        (("super liga", "serbia"), "الدوري الصربي", "srb.1"),
        (("super lig", "turkey"), "الدوري التركي", "tur.1"),
        (("süper lig",), "الدوري التركي", "tur.1"),
        (("ekstraklasa",), "الدوري البولندي", "pol.1"),
        (("greek super league",), "الدوري اليوناني", "gre.1"),
        (("liga i", "romania"), "الدوري الروماني", "rou.1"),
        (("hnl", "croatia"), "الدوري الكرواتي", "cro.1"),
    )
    matches = []
    for priority, (tokens, name, slug) in enumerate(mappings):
        positions = [value.rfind(token) for token in tokens]
        if all(position >= 0 for position in positions):
            # The page parser can see several preceding group headings in the same
            # parent container; choose the latest competition label in document order.
            group_start = min(positions)
            matches.append((group_start, -priority, name, slug))
    if matches:
        _, _, name, slug = max(matches)
        return name, slug
    return "", ""

def _goal_team_logo_records(item, home, away):
    """Map GOAL images by alt/title when possible; reject obvious generic placeholders."""
    records = item.get("image_meta") or []
    cleaned = []
    for record in records:
        url = str(record.get("url") or "")
        label = " ".join((record.get("alt", ""), record.get("title", ""))).lower()
        haystack = (url + " " + label + " " + str(record.get("class", ""))).lower()
        if any(word in haystack for word in ("placeholder", "generic", "default-logo", "no-logo", "unknown-team", "blank-crest")):
            continue
        if url and url not in [entry["url"] for entry in cleaned]:
            cleaned.append({"url": url, "label": label})

    def score(record, team):
        team_key = re.sub(r"[^a-z0-9]+", " ", str(team).lower()).strip()
        label_key = re.sub(r"[^a-z0-9]+", " ", record["label"]).strip()
        if not label_key:
            return 0
        team_words = {word for word in team_key.split() if len(word) > 2}
        label_words = set(label_key.split())
        if team_key and team_key in label_key:
            return 100
        return len(team_words & label_words)

    home_ranked = sorted(((score(record, home), record) for record in cleaned), key=lambda x: x[0], reverse=True)
    away_ranked = sorted(((score(record, away), record) for record in cleaned), key=lambda x: x[0], reverse=True)
    home_record = home_ranked[0][1] if home_ranked and home_ranked[0][0] > 0 else None
    away_record = away_ranked[0][1] if away_ranked and away_ranked[0][0] > 0 else None
    if home_record and away_record and home_record["url"] == away_record["url"]:
        away_record = None

    # GOAL commonly emits exactly two club images in home-then-away order.
    # Use positional fallback only when both images are distinct and not labelled as generic.
    if len(cleaned) == 2:
        home_record = home_record or cleaned[0]
        away_record = away_record or cleaned[1]
    return (
        urljoin("https://www.goal.com", home_record["url"]) if home_record else "",
        urljoin("https://www.goal.com", away_record["url"]) if away_record else "",
    )


def _team_initials_ar(value):
    words = [word for word in str(value or "").split() if word not in {"ال", "نادي", "ن"}]
    if len(words) >= 2:
        return words[0][0] + words[1][0]
    return (words[0][:2] if words else "؟")


def parse_goal_fixtures(page_html, match_date, page_timezone=GOAL_TIMEZONE):
    """Parse scheduled match rows from the official GOAL fixtures page."""
    parser = _GoalAnchorParser()
    parser.feed(page_html or "")
    matches = []
    raw_competitions = sorted({re.sub(r"\s+", " ", item.get("competition", "")).strip()
                               for item in parser.items if item.get("competition")})
    print("GOAL competition labels sample:", raw_competitions[:20])
    for item in parser.items:
        text = re.sub(r"\s+", " ", item.get("text", "")).strip()
        result = _GOAL_ROW_PATTERN.match(text)
        if not result:
            continue
        home = result.group("home").strip()
        if result.groupdict().get("home_prefix"):
            home = result.group("home_prefix").strip() + " " + home
        if result.groupdict().get("home_suffix"):
            home += " " + result.group("home_suffix").strip()
        away = result.group("away").strip()
        if re.search(r"\b(women|femenil|u18|academy|youth)\b", f"{home} {away}", re.I):
            continue
        league_name, league_slug = _goal_competition_name(item.get("competition"))
        if not league_name:
            continue
        hour, minute = map(int, result.group("time").split(":"))
        try:
            kickoff_local = datetime(
                match_date.year, match_date.month, match_date.day,
                hour, minute, tzinfo=page_timezone
            )
        except ValueError:
            continue
        kickoff = kickoff_local.astimezone(timezone.utc)
        href = item.get("href", "")
        event_id = urlparse(href).path.rstrip("/").split("/")[-1]
        if not event_id or event_id in ("fixtures", "live-scores"):
            raw_id = f"{league_slug}|{home}|{away}|{kickoff.isoformat()}"
            event_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:20]
        home_logo, away_logo = _goal_team_logo_records(item, home, away)
        matches.append({
            "league_slug": league_slug,
            "league": league_name,
            "league_logo": "",
            "event_id": event_id,
            "kickoff": kickoff.isoformat(),
            "home": home,
            "away": away,
            "home_logo": home_logo,
            "away_logo": away_logo,
            "home_form": "",
            "away_form": "",
            "home_record": "",
            "away_record": "",
            "stage": "",
            "round": "",
            "venue": "",
            "status": "pre",
        })
    unique = {fixture_key(match): match for match in matches}
    return sorted(unique.values(), key=lambda item: item["kickoff"])


def fetch_goal_matches(now, end):
    """Fetch GOAL's dated fixtures pages and convert their displayed times to Cairo."""
    all_matches = {}
    dates = {now.astimezone(GOAL_TIMEZONE).date(), end.astimezone(GOAL_TIMEZONE).date()}
    healthy_pages = 0
    for fixture_date in sorted(dates):
        url = GOAL_FIXTURES_URL.format(date=fixture_date.isoformat())
        response = HTTP.get(url, timeout=20, allow_redirects=True)
        response.raise_for_status()
        healthy_pages += 1
        parsed = parse_goal_fixtures(response.text, fixture_date)
        print(f"GOAL fixtures {fixture_date}: HTTP {response.status_code}; "
              f"{len(response.content)} bytes; {len(parsed)} scheduled matches parsed.")
        for match in parsed:
            kickoff = _parse_datetime(match["kickoff"])
            if kickoff and now < kickoff <= end:
                all_matches[fixture_key(match)] = match
    if not healthy_pages or not all_matches:
        raise RuntimeError("GOAL fixtures returned no verifiable upcoming matches; refusing to fall back to another source.")
    return sorted(all_matches.values(), key=lambda item: item["kickoff"])



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


_RAQM_AVAILABLE = bool(getattr(getattr(ImageFont, "core", None), "HAVE_RAQM", False))


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    layout = ImageFont.Layout.RAQM if _RAQM_AVAILABLE else ImageFont.Layout.BASIC
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, layout_engine=layout)
            except Exception:
                continue
    return ImageFont.load_default()


def _rtl(value):
    text = str(value)
    # Pillow's RAQM engine already shapes Arabic and applies the bidi algorithm.
    # Pre-shaping it again reverses word order, which caused the garbled card text.
    if _RAQM_AVAILABLE:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


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
        visible = [pixel[:3] for pixel in image.getdata() if pixel[3] > 40]
        unique = set(visible)
        if visible:
            grayscale_ratio = sum(1 for red, green, blue in visible
                                  if max(red, green, blue) - min(red, green, blue) <= 12) / len(visible)
            if len(unique) <= 14 and grayscale_ratio >= 0.94:
                print("Rejected generic monochrome club placeholder:", urlparse(url).path[-70:])
                LOGO_CACHE[url] = None
                return None
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
        initials = _rtl(_team_initials_ar(fallback))
        font = _fit_text(draw, initials, diameter - 36, 54, 30)
        draw.text((x, y), initials, font=font, fill=(26, 78, 18), anchor="mm")
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
    month_names=["يناير","فبراير","مارس","أبريل","مايو","يونيو","يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"]
    draw.rounded_rectangle((52,55,365,164),radius=55,fill=(5,35,0),outline=(70,125,0),width=3)
    date_text=_rtl(f"{arabic_digits(kickoff.day)} {month_names[kickoff.month-1]}")
    time_text=_rtl(arabic_digits(formatted["time"]))
    draw.text((208,88),date_text,font=_fit_text(draw,date_text,270,28,23),
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


def build_caption(match, reminder_type=None):
    kickoff=_parse_datetime(match["kickoff"])
    formatted=format_cairo_date(kickoff)
    home_ar=arabic_team_name(match.get("home"))
    away_ar=arabic_team_name(match.get("away"))
    reminder_labels = {
        "schedule": "📅 <b>موعد مباراة قادمة</b>",
        "24h": "⏳ <b>موعد المباراة غدًا</b>",
        "30m": "🚨 <b>المباراة هتبدأ بعد نصف ساعة</b>",
    }
    parts=[]
    if reminder_type in reminder_labels:
        parts.append(reminder_labels[reminder_type])
    parts.extend([
        f"⚽ <b>{_escape(home_ar)} ضد {_escape(away_ar)}</b>",
        f"🏆 <b>البطولة:</b> {_escape(match.get('league') or 'بطولة كرة القدم')}",
        f"📅 <b>التاريخ:</b> {_escape(arabic_digits(formatted['date']))}",
        f"⏰ <b>الموعد:</b> {_escape(arabic_digits(formatted['time']))} بتوقيت القاهرة",
    ])
    if match.get("round"):
        round_name = str(match["round"])
        round_number = re.search(r"\d+", round_name)
        if round_number:
            round_name = f"الجولة {arabic_digits(round_number.group())}"
        elif not re.search(r"[\u0600-\u06FF]", round_name):
            round_name = ""
        if round_name:
            parts.append(f"🔢 <b>الجولة:</b> {_escape(round_name)}")
    parts.append("🔷 <b>كوراكس</b>")
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
        json.dump({"seen": sorted(seen)[-24000:]}, file, ensure_ascii=False, indent=2)


def due_reminders(match, now):
    kickoff = _parse_datetime(match.get("kickoff"))
    if not kickoff:
        return []
    remaining = kickoff - now
    due = [kind for kind, (earliest, latest) in REMINDER_WINDOWS.items()
           if earliest <= remaining <= latest]
    if due:
        return due
    # On first activation, catch matches already inside the next 24 hours
    # instead of waiting for a reminder window that has already passed.
    if timedelta(minutes=35) < remaining < timedelta(hours=12):
        return ["schedule"]
    return []


def reminder_key(match, reminder_type):
    return f"{reminder_type}:{fixture_key(match)}"


def main():
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=LOOKAHEAD_HOURS)
    matches = fetch_goal_matches(now, end)
    seen = load_seen()
    due = []
    for match in matches:
        for reminder_type in due_reminders(match, now):
            key = reminder_key(match, reminder_type)
            if key not in seen:
                due.append((match, reminder_type, key))

    print(f"GOAL scan: {len(matches)} upcoming matches in the next {LOOKAHEAD_HOURS} hours; "
          f"{len(due)} due reminders (schedule / 24h / 30m).")

    published = 0
    for match, reminder_type, key in due[:MAX_REMINDERS_PER_RUN]:
        try:
            photo = make_match_card(match)
            _telegram(
                "sendPhoto",
                data={"chat_id": CHAT_ID, "caption": build_caption(match, reminder_type), "parse_mode": "HTML"},
                files={"photo": ("korax-match.jpg", photo, "image/jpeg")},
            )
            seen.add(key)
            save_seen(seen)
            published += 1
            labels = {
                "schedule": "upcoming schedule",
                "24h": "24-hour reminder",
                "30m": "30-minute reminder",
            }
            print(f"Published {labels.get(reminder_type, reminder_type)}: "
                  f"{match['home']} vs {match['away']} | {match['league']} | {match['kickoff']}")
            time.sleep(1)
        except Exception as exc:
            print(f"Could not publish {reminder_type} reminder for {match.get('home')} vs {match.get('away')}: {exc!r}")

    print(f"GOAL fixture run finished: {published} reminders published.")


if __name__ == "__main__":
    main()
