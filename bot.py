import os
import json
import hashlib
import html
import re
import time
import glob
import unicodedata
from io import BytesIO
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from PIL import Image, ImageDraw, ImageFont, ImageOps

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@KoraxNews11").strip()
AZURE_TRANSLATOR_KEY = os.environ.get("AZURE_TRANSLATOR_KEY", "").strip()
AZURE_TRANSLATOR_REGION = os.environ.get("AZURE_TRANSLATOR_REGION", "").strip()
MYMEMORY_EMAIL = os.environ.get("MYMEMORY_EMAIL", "").strip()
STATE_FILE = "seen_news.json"
SOURCES_FILE = "sources.json"
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_POSTS_PER_RUN = 5
MAX_AGE_HOURS = 96
MIN_IMAGE_WIDTH = 800
MIN_IMAGE_HEIGHT = 450
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 (compatible; KORAX-NEWS/2.0; +https://github.com/shotalx2-lgtm/korax-news-auto)"})

WOMEN_PATTERN = re.compile(
    r"\b(women|woman|women's|womens|female|ladies|girls?|girl's|"
    r"nwsl|wsl|uwcl|women's super league|women's world cup)\b|"
    r"سيدات|السيدات|نسائي|نسائية|نسائيه|نساء|للسيدات|منتخب السيدات|كرة القدم النسائية",
    re.IGNORECASE,
)
OTHER_SPORT_PATTERN = re.compile(
    r"كرة السلة|كرة اليد|كرة الطائرة|إسكواش|اسكواش|تنس|كريكيت|رجبي|هوكي|فورمولا|"
    r"\b(nba|wnba|nfl|mlb|nhl|cricket|rugby|tennis|volleyball|squash|"
    r"formula 1|f1|golf|badminton|handball|boxing|ufc|american football|"
    r"gridiron|college football|high school football|ncaa football|varsity football)\b",
    re.IGNORECASE,
)
FOOTBALL_PATTERN = re.compile(
    r"كرة القدم|الدوري|دوري أبطال|كأس العالم|كأس أمم|كأس آسيا|منتخب|نادي|"
    r"لاعب|مدرب|انتقالات|ميركاتو|هدف|هداف|"
    r"\b(football|soccer|premier league|la liga|serie a|bundesliga|ligue 1|"
    r"champions league|europa league|conference league|fa cup|copa del rey|"
    r"copa america|world cup|club world cup|uefa|fifa|afcon|afc|transfer|"
    r"goalkeeper|striker|midfielder|defender|footballer|matchday|"
    r"football club)\b",
    re.IGNORECASE,
)
STRICT_ASSOCIATION_PATTERN = re.compile(
    r"كرة القدم|دوري أبطال|الدوري الإنجليزي|الدوري الإسباني|الدوري الإيطالي|"
    r"الدوري الألماني|الدوري الفرنسي|الدوري المصري|الدوري السعودي|دوري روشن|"
    r"كأس العالم|كأس أمم|منتخب|نادي الأهلي|الأهلي|الزمالك|بيراميدز|الهلال|النصر|"
    r"الاتحاد|الشباب|الرجاء|الوداد|الترجي|الأفريقي|السد|الدحيل|"
    r"\b(soccer|association football|men's football|premier league|la liga|"
    r"serie a|bundesliga|ligue 1|champions league|europa league|conference league|"
    r"fa cup|copa del rey|copa america|club world cup|uefa|fifa|afcon|"
    r"afc champions|mls|j\.?league|k league|brazilian serie|argentine primera|"
    r"saudi pro league|egyptian premier|botola|qatar stars|uae pro league|"
    r"football club|manchester united|manchester city|liverpool|arsenal|chelsea|"
    r"real madrid|barcelona|atletico madrid|bayern|borussia dortmund|paris saint|"
    r"juventus|inter milan|ac milan|napoli|roma|benfica|porto|ajax|celtic|rangers|"
    r"al hilal|al nassr|al ahly|zamalek|haaland|salah|mbappe|vinicius|bellingham|"
    r"ronaldo|messi|de bruyne|yamal|kane|neymar|saka|odegaard)\b",
    re.IGNORECASE,
)


PROMO_PATTERN = re.compile(
    r"sign up for our newsletter|subscribe to our newsletter|daily newsletter|"
    r"newsletter sign|listen to our podcast|subscribe now",
    re.IGNORECASE,
)


def telegram_api(method, *, data=None, files=None, timeout=30):
    response = requests.post(f"{TG}/{method}", data=data or {}, files=files, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError(f"Telegram returned a non-JSON response for {method}")
    if not response.ok or not payload.get("ok"):
        description = payload.get("description", response.text[:300])
        raise RuntimeError(f"Telegram {method} failed ({response.status_code}): {description}")
    return payload["result"]


def check_telegram():
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN GitHub Actions secret.")
    me = telegram_api("getMe")
    print(f"Telegram bot authenticated: @{me.get('username', 'unknown')}")
    chat = telegram_api("getChat", data={"chat_id": CHAT_ID})
    print(f"Telegram destination verified: {chat.get('title') or chat.get('username') or CHAT_ID}")


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def has_arabic(value):
    return bool(re.search(r"[\u0600-\u06FF]", value or ""))


def _azure_translate(text):
    if not AZURE_TRANSLATOR_KEY:
        return None
    url = "https://api.cognitive.microsofttranslator.com/translate"
    params = {"api-version": "3.0", "to": "ar"}
    headers = {"Ocp-Apim-Subscription-Key": AZURE_TRANSLATOR_KEY, "Content-Type": "application/json"}
    if AZURE_TRANSLATOR_REGION:
        headers["Ocp-Apim-Subscription-Region"] = AZURE_TRANSLATOR_REGION
    response = requests.post(url, params=params, headers=headers, json=[{"text": text}], timeout=15)
    response.raise_for_status()
    result = response.json()[0]["translations"][0]["text"].strip()
    return result if has_arabic(result) else None


def _mymemory_translate(text):
    # MyMemory accepts up to 500 bytes per request on its free endpoint.
    encoded = text.encode("utf-8")
    if len(encoded) > 440:
        encoded = encoded[:440]
        while True:
            try:
                text = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
    params = {"q": text, "langpair": "en|ar", "mt": "1"}
    if MYMEMORY_EMAIL:
        params["de"] = MYMEMORY_EMAIL
    response = requests.get("https://api.mymemory.translated.net/get", params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    if data.get("responseStatus") != 200:
        raise RuntimeError(data.get("responseDetails", "MyMemory translation unavailable"))
    result = html.unescape(data.get("responseData", {}).get("translatedText", "")).strip()
    return result if has_arabic(result) else None


def translate_to_arabic(text):
    text = clean_text(text)
    if not text:
        return ""
    if has_arabic(text):
        return text
    for provider, func in (("Azure", _azure_translate), ("MyMemory", _mymemory_translate)):
        try:
            translated = func(text)
            if translated and has_arabic(translated):
                print(f"Translation succeeded with {provider}.")
                return translated
        except Exception as exc:
            print(f"{provider} translation unavailable:", repr(exc))
    return None


def is_mens_football(entry, source):
    title = clean_text(entry.get("title", ""))
    summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
    sample = f"{title} {summary[:500]}"
    if WOMEN_PATTERN.search(sample) or OTHER_SPORT_PATTERN.search(sample) or PROMO_PATTERN.search(sample):
        return False
    if source.get("strict_search", False):
        return bool(STRICT_ASSOCIATION_PATTERN.search(sample))
    if source.get("football_only", False):
        return True
    return bool(FOOTBALL_PATTERN.search(sample))

def parse_published(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return time.mktime(parsed)
    except (OverflowError, ValueError, TypeError):
        return None


def is_recent(entry):
    published = parse_published(entry)
    if published is None:
        return True
    return (time.time() - published) <= MAX_AGE_HOURS * 3600


def fetch_source(source):
    try:
        response = HTTP.get(source["url"], timeout=14)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if getattr(feed, "bozo", False) and not feed.entries:
            raise RuntimeError(str(feed.bozo_exception))
        print(f"RSS {source['name']}: {len(feed.entries)} entries")
        return source, feed.entries, None
    except Exception as exc:
        return source, [], exc


def image_candidates(entry):
    found = []
    for key in ("media_content", "media_thumbnail", "content"):
        for item in entry.get(key, []) or []:
            if isinstance(item, dict) and item.get("url"):
                try:
                    area = int(item.get("width", 0)) * int(item.get("height", 0))
                except (TypeError, ValueError):
                    area = 0
                found.append((area, item["url"]))
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("type", "").startswith("image/") and link.get("href"):
            found.append((0, link["href"]))
    image = entry.get("image")
    if isinstance(image, dict) and image.get("href"):
        found.append((0, image["href"]))
    markup = entry.get("summary", "") or entry.get("description", "")
    for tag in re.findall(r"<img\b[^>]*>", markup, flags=re.IGNORECASE):
        src = re.search(r"(?:src|data-src)=[\"']([^\"']+)", tag, flags=re.IGNORECASE)
        if src:
            width = re.search(r"width=[\"']?(\d+)", tag, flags=re.IGNORECASE)
            height = re.search(r"height=[\"']?(\d+)", tag, flags=re.IGNORECASE)
            area = int(width.group(1)) * int(height.group(1)) if width and height else 0
            found.append((area, src.group(1)))
    unique = {}
    for area, url in found:
        unique[url] = max(area, unique.get(url, 0))
    return sorted(((area, url) for url, area in unique.items()), reverse=True)


def article_image_candidates(article_url):
    try:
        response = HTTP.get(article_url, timeout=10, allow_redirects=True)
        response.raise_for_status()
        page_url = response.url
        page = response.text[:2_000_000]
        print(f"Article page resolved to {urlparse(page_url).netloc}.")
    except Exception as exc:
        print("Article image metadata unavailable:", repr(exc))
        return []

    scored = []
    for tag in re.findall(r"<meta\b[^>]*>", page, flags=re.IGNORECASE):
        attrs = dict(
            (key.lower(), html.unescape(value))
            for key, value in re.findall(r"([a-zA-Z_:.-]+)\s*=\s*['\"]([^'\"]*)['\"]", tag)
        )
        prop = (attrs.get("property") or attrs.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src") and attrs.get("content"):
            scored.append((1_000_000, urljoin(page_url, attrs["content"])))

    for tag in re.findall(r"<link\b[^>]*>", page, flags=re.IGNORECASE):
        attrs = dict(
            (key.lower(), html.unescape(value))
            for key, value in re.findall(r"([a-zA-Z_:.-]+)\s*=\s*['\"]([^'\"]*)['\"]", tag)
        )
        if "image_src" in (attrs.get("rel") or "").lower() and attrs.get("href"):
            scored.append((900_000, urljoin(page_url, attrs["href"])))

    # Some publishers keep the full image in srcset or lazy-loading attributes.
    for tag in re.findall(r"<(?:img|source)\b[^>]*>", page, flags=re.IGNORECASE):
        attrs = dict(
            (key.lower(), html.unescape(value))
            for key, value in re.findall(r"([a-zA-Z_:.-]+)\s*=\s*['\"]([^'\"]*)['\"]", tag)
        )
        for key in ("data-original", "data-lazy-src", "data-src", "src"):
            if attrs.get(key):
                scored.append((0, urljoin(page_url, attrs[key])))
        for key in ("srcset", "data-srcset"):
            if attrs.get(key):
                for candidate in attrs[key].split(","):
                    bits = candidate.strip().split()
                    if not bits:
                        continue
                    width = 0
                    if len(bits) > 1 and bits[-1].lower().endswith("w"):
                        try:
                            width = int(bits[-1][:-1])
                        except ValueError:
                            width = 0
                    scored.append((width * width, urljoin(page_url, bits[0])))

    # JSON-LD article objects commonly expose the publisher's original image URL.
    for match in re.findall(r'"image"\s*:\s*"([^"]+)"', page):
        scored.append((0, urljoin(page_url, match.replace("\\/", "/"))))

    unique = {}
    for score, url in scored:
        unique[url] = max(score, unique.get(url, 0))
    results = sorted(unique.items(), key=lambda item: item[1], reverse=True)
    print(f"Found {len(results)} candidate article images.")
    return [url for url, _ in results]

def _try_image_urls(candidates, limit):
    best = None
    checked = set()
    for url in candidates[:limit]:
        if not url or url in checked:
            continue
        checked.add(url)
        try:
            response = HTTP.get(url, timeout=8)
            response.raise_for_status()
            raw = response.content
            if len(raw) > 12 * 1024 * 1024:
                continue
            photo = Image.open(BytesIO(raw))
            width, height = photo.size
            if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
                print(f"Rejected low-resolution image: {width}x{height}")
                continue
            area = width * height
            if best is None or area > best[0]:
                best = (area, raw)
        except Exception as exc:
            print("Image candidate failed:", repr(exc))
    return best


def download_best_image(entry, article_url):
    rss_urls = [url for _, url in image_candidates(entry)]
    best = _try_image_urls(rss_urls, 2)
    if best:
        return best[1]

    # Google News RSS entries often include the publisher's original article URL in <source>.
    page_targets = []
    source_info = entry.get("source") or {}
    publisher_url = source_info.get("href") if isinstance(source_info, dict) else None
    if publisher_url:
        parsed = urlparse(publisher_url)
        if parsed.scheme in ("http", "https") and parsed.netloc and parsed.netloc.lower() not in ("news.google.com", "google.com"):
            page_targets.append(publisher_url)
    page_targets.append(article_url)

    for page_target in dict.fromkeys(page_targets):
        page_urls = article_image_candidates(page_target)
        page_best = _try_image_urls(page_urls, 3)
        if page_best and (best is None or page_best[0] > best[0]):
            best = page_best
        if best and best[0] >= 1_000_000:
            break

    if best:
        return best[1]
    print("No sufficiently large news photo found; using clean branded text card.")
    return None

def _font(size, bold=False):
    patterns = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in patterns:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    matches = glob.glob("/usr/share/fonts/**/*Arabic*.*tf", recursive=True)
    if matches:
        return ImageFont.truetype(matches[0], size)
    return ImageFont.load_default()


def rtl(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def wrap_rtl(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), rtl(candidate), font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1].rstrip("،؛,.") + "…"
    return lines


def category_for(title, summary):
    text = f"{title} {summary}".lower()
    categories = [
        (("champions league", "دوري أبطال أوروبا", "دوري الأبطال"), "دوري أبطال أوروبا"),
        (("premier league", "الدوري الإنجليزي"), "الدوري الإنجليزي"),
        (("la liga", "الدوري الإسباني"), "الدوري الإسباني"),
        (("serie a", "الدوري الإيطالي"), "الدوري الإيطالي"),
        (("bundesliga", "الدوري الألماني"), "الدوري الألماني"),
        (("ligue 1", "الدوري الفرنسي"), "الدوري الفرنسي"),
        (("saudi pro", "روشن", "الدوري السعودي"), "الكرة العربية"),
        (("egyptian premier", "الدوري المصري", "الأهلي", "الزمالك", "بيراميدز"), "الكرة العربية"),
        (("national team", "منتخب", "fifa world cup", "كأس العالم"), "المنتخبات"),
        (("transfer", "ميركاتو", "انتقالات"), "الانتقالات"),
    ]
    for keys, label in categories:
        if any(key in text for key in keys):
            return label
    return "كرة قدم عالمية"


def build_caption(title, summary, source_name, link, category):
    safe_title = html.escape(title)
    safe_summary = html.escape(summary[:360].strip())
    parts = [
        f"⚽ <b>{safe_title}</b>",
        "━━━━━━━━━━━━━━━━",
    ]
    if safe_summary:
        parts.append(f"📝 <b>ملخص الخبر</b>\n{safe_summary}")
    parts.extend([
        f"🏆 <b>القسم:</b> {html.escape(category)}",
        f"📰 <b>المصدر:</b> {html.escape(source_name)}",
        f'🔗 <a href="{html.escape(link, quote=True)}">اقرأ التفاصيل من المصدر</a>',
        "⚫ <b>KORAX NEWS</b>  🟢",
    ])
    caption = "\n\n".join(parts)
    if len(caption) > 1000 and safe_summary:
        allowed = max(80, len(safe_summary) - (len(caption) - 1000) - 20)
        parts[2] = f"📝 <b>ملخص الخبر</b>\n{safe_summary[:allowed]}…"
        caption = "\n\n".join(parts)
    return caption[:1024]


def make_news_card(photo_bytes, title, category, source_name):
    width, height = 1200, 675
    lime = (190, 255, 0)
    background = Image.new("RGB", (width, height), (4, 6, 6))
    if photo_bytes:
        try:
            photo = Image.open(BytesIO(photo_bytes)).convert("RGB")
            photo = ImageOps.fit(photo, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.42))
            background.paste(photo, (0, 0))
        except Exception as exc:
            print("Could not render source image:", repr(exc))

    overlay = Image.new("RGBA", (width, 300), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(300):
        alpha = int(245 * (y / 299) ** 1.15)
        overlay_draw.line((0, y, width, y), fill=(3, 6, 5, alpha))
    background.paste(overlay, (0, 375), overlay)

    draw = ImageDraw.Draw(background)
    draw.rectangle((0, 0, width, 11), fill=lime)
    draw.rounded_rectangle((48, 38, 342, 108), radius=20, fill=(4, 7, 6))
    draw.rounded_rectangle((48, 38, 342, 108), radius=20, outline=lime, width=3)
    draw.text((76, 52), "KORAX NEWS", font=_font(36, True), fill=lime)

    draw.rounded_rectangle((48, 405, 265, 452), radius=14, fill=lime)
    draw.text((156, 411), rtl(category), font=_font(22, True), fill=(5, 8, 4), anchor="mm")
    title_font = _font(43, True)
    lines = wrap_rtl(draw, title or "أخبار كرة القدم", title_font, 1080)
    y = 470
    for line in lines:
        draw.text((1148, y), rtl(line), font=title_font, fill=(250, 252, 247), anchor="ra")
        y += 53

    draw.line((48, 630, 1152, 630), fill=lime, width=2)
    draw.text((1150, 642), "KORAX • FOOTBALL", font=_font(20, True), fill=(205, 220, 190), anchor="ra")
    draw.text((50, 642), "FOOTBALL NEWS  •  KORAX", font=_font(19, True), fill=(210, 216, 205))
    output = BytesIO()
    background.save(output, format="JPEG", quality=95, optimize=True, progressive=True, subsampling=0)
    output.seek(0)
    return output


def send_news(caption, photo_bytes, title, category, source_name):
    card = make_news_card(photo_bytes, title, category, source_name)
    telegram_api(
        "sendPhoto",
        data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("korax-news.jpg", card, "image/jpeg")},
        timeout=45,
    )


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            state = json.load(file)
            return set(state.get("seen", [])), set(state.get("titles", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), set()


def title_fingerprint(title):
    normalized = unicodedata.normalize("NFKC", title).lower()
    normalized = re.sub(r"[^\w\u0600-\u06FF]+", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def main():
    check_telegram()
    with open(SOURCES_FILE, encoding="utf-8") as file:
        sources = json.load(file)
    if not sources:
        raise RuntimeError("sources.json contains no RSS sources.")

    seen_links, seen_titles = load_state()
    found = []
    healthy = 0
    total = 0
    with ThreadPoolExecutor(max_workers=min(10, len(sources))) as pool:
        futures = [pool.submit(fetch_source, source) for source in sources]
        for future in as_completed(futures):
            source, entries, error = future.result()
            if error:
                print(f"RSS source failed ({source['name']}): {error!r}")
                continue
            healthy += 1
            total += len(entries)
            for entry in entries[:60]:
                link = entry.get("link", "").strip()
                title = clean_text(entry.get("title", ""))
                if not link or not title or not is_recent(entry) or not is_mens_football(entry, source):
                    continue
                link_hash = hashlib.sha256(link.split("#")[0].encode("utf-8")).hexdigest()
                title_hash = title_fingerprint(title)
                if link_hash in seen_links or title_hash in seen_titles:
                    continue
                found.append((entry, source, link_hash, title_hash))

    if healthy == 0:
        raise RuntimeError("All RSS sources failed. Check the feed URLs and network access.")

    # Newest first keeps the channel current when several feeds overlap.
    found.sort(key=lambda item: parse_published(item[0]) or 0, reverse=True)
    print(
        f"RSS check complete: {healthy}/{len(sources)} sources healthy, "
        f"{total} entries scanned, {len(found)} new men's football stories."
    )

    published = 0
    failed_translations = 0
    for entry, source, link_hash, title_hash in found:
        if published >= MAX_POSTS_PER_RUN:
            break
        original_title = clean_text(entry.get("title", ""))
        original_summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
        title_ar = translate_to_arabic(original_title)
        if not title_ar:
            failed_translations += 1
            print(f"Skipped: Arabic translation unavailable for {original_title[:100]!r}")
            continue

        # Never let untranslated English paragraphs leak into an Arabic-only channel.
        if has_arabic(original_summary):
            summary_ar = original_summary
        elif AZURE_TRANSLATOR_KEY and original_summary:
            summary_ar = translate_to_arabic(original_summary[:900]) or ""
        else:
            summary_ar = ""

        link = entry.get("link", "").strip()
        category = category_for(original_title, original_summary)
        photo_bytes = download_best_image(entry, link)
        caption = build_caption(title_ar, summary_ar, source["name"], link, category)
        send_news(caption, photo_bytes, title_ar, category, source["name"])

        seen_links.add(link_hash)
        seen_titles.add(title_hash)
        published += 1
        print(f"Published Arabic men's football news: {original_title[:140]} ({urlparse(link).netloc})")
        time.sleep(1)

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump({
            "seen": list(seen_links)[-12000:],
            "titles": list(seen_titles)[-12000:],
        }, file, ensure_ascii=False, indent=2)

    print(
        f"Run finished: {published} posts published; "
        f"{failed_translations} skipped because Arabic translation was unavailable."
    )


if __name__ == "__main__":
    main()
