import os
import json
import hashlib
import html
import re
import time
import glob
from io import BytesIO
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageFilter

import requests
import feedparser

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@KoraxNews11").strip()
STATE_FILE = "seen_news.json"
SOURCES_FILE = "sources.json"
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_POSTS_PER_RUN = 8


def telegram_api(method, *, data=None, files=None, timeout=25):
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
    try:
        me = telegram_api("getMe")
    except Exception as exc:
        raise RuntimeError(
            "Telegram bot authentication failed. Check the TELEGRAM_BOT_TOKEN secret in "
            "GitHub repository Settings > Secrets and variables > Actions. "
            "Do not paste the token into logs or chat. Original error: " + str(exc)
        ) from exc
    print(f"Telegram bot authenticated: @{me.get('username', 'unknown')}")
    try:
        chat = telegram_api("getChat", data={"chat_id": CHAT_ID})
    except Exception as exc:
        raise RuntimeError(
            f"Bot cannot access destination {CHAT_ID}. Confirm TELEGRAM_CHAT_ID and "
            "that the bot is an administrator of the channel with permission to post. "
            "Original error: " + str(exc)
        ) from exc
    print(f"Telegram destination verified: {chat.get('title') or chat.get('username') or CHAT_ID}")


def translate(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if not text:
        return ""
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "ar", "dt": "t", "q": text[:3500]},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        data = response.json()
        result = "".join(part[0] for part in data[0] if part and part[0])
        return result.strip() or text
    except Exception as exc:
        print("Translation unavailable; keeping original text:", repr(exc))
        return text


def get_image(entry):
    for key in ("media_content", "media_thumbnail"):
        values = entry.get(key) or []
        if values and values[0].get("url"):
            return values[0]["url"]
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and link.get("type", "").startswith("image/"):
            return link.get("href")
    summary = entry.get("summary", "")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)', summary, re.I)
    return match.group(1) if match else None


def build_caption(title, summary, source_name, link):
    parts = [f"⚽ <b>{html.escape(title)}</b>"]
    if summary:
        parts.append(html.escape(summary[:500]))
    parts.append(f"📰 المصدر: <b>{html.escape(source_name)}</b>")
    parts.append(f'🔗 <a href="{html.escape(link, quote=True)}">اقرأ الخبر الأصلي</a>')
    caption = "\n\n".join(parts)
    # Telegram photo captions have a 1024-character limit. Keep HTML tags intact.
    if len(caption) > 1000 and summary:
        parts[1] = html.escape(summary[:max(80, 1000 - len(caption) + len(parts[1]) - 30)])
        caption = "\n\n".join(parts)
    return caption


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


def _rtl(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _wrap_arabic(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), _rtl(candidate), font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_news_card(image_url, title, source_name):
    width, height = 1080, 1080
    lime = (190, 255, 0)
    canvas = Image.new("RGB", (width, height), (5, 7, 7))
    draw = ImageDraw.Draw(canvas)

    if image_url:
        try:
            response = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            response.raise_for_status()
            photo = Image.open(BytesIO(response.content)).convert("RGB")
            if photo.width >= 320 and photo.height >= 180:
                target_ratio = width / 610
                ratio = max(width / photo.width, 610 / photo.height)
                photo = photo.resize((int(photo.width * ratio), int(photo.height * ratio)), Image.Resampling.LANCZOS)
                left = (photo.width - width) // 2
                top = (photo.height - 610) // 2
                canvas.paste(photo.crop((left, top, left + width, top + 610)), (0, 0))
            else:
                print("Image too small; using branded fallback card.")
        except Exception as exc:
            print("Image download failed; using branded fallback card:", repr(exc))

    # Dark fade between the photograph and the information panel.
    fade = Image.new("RGBA", (width, 180), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    for y in range(180):
        alpha = int(245 * y / 179)
        fd.line((0, y, width, y), fill=(5, 7, 7, alpha))
    canvas.paste(fade, (0, 430), fade)

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, 12), fill=lime)
    draw.rounded_rectangle((52, 48, 355, 122), radius=24, fill=(5, 7, 7))
    draw.rounded_rectangle((52, 48, 355, 122), radius=24, outline=lime, width=3)
    draw.text((78, 64), "KORAX NEWS", font=_font(38, True), fill=lime)

    # Lime accent and restrained editorial layout.
    draw.rounded_rectangle((52, 660, 1028, 664), radius=2, fill=lime)
    title_font = _font(54, True)
    lines = _wrap_arabic(draw, title or "آخر أخبار كرة القدم", title_font, 940)[:4]
    y = 700
    for line in lines:
        draw.text((1018, y), _rtl(line), font=title_font, fill=(248, 250, 245), anchor="ra", direction="rtl" if not _rtl(line) == line else None)
        y += 72

    draw.rounded_rectangle((52, 990, 1028, 1050), radius=18, fill=(18, 23, 18))
    draw.ellipse((78, 1010, 98, 1030), fill=lime)
    draw.text((120, 1002), "KORAX NEWS  •  " + (source_name or "FOOTBALL"), font=_font(25, True), fill=(205, 215, 195))
    draw.text((1000, 1003), "LIVE", font=_font(25, True), fill=lime, anchor="ra")

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=94, optimize=True, progressive=True)
    output.seek(0)
    return output


def send_message(text, image=None, title=""):
    if image:
        try:
            card = make_news_card(image, title, "KORAX")
            telegram_api("sendPhoto", data={
                "chat_id": CHAT_ID,
                "caption": text,
                "parse_mode": "HTML",
            }, files={"photo": ("korax-news.jpg", card, "image/jpeg")}, timeout=45)
            return
        except Exception as exc:
            print("Branded photo send failed; trying text message:", repr(exc))
    telegram_api("sendMessage", data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })

def load_seen():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            return set(json.load(file).get("seen", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def main():
    check_telegram()

    with open(SOURCES_FILE, encoding="utf-8") as file:
        sources = json.load(file)
    if not sources:
        raise RuntimeError("sources.json contains no RSS sources.")

    seen = load_seen()
    found = []
    successful_sources = 0
    total_entries = 0

    for source in sources:
        try:
            response = requests.get(
                source["url"],
                headers={"User-Agent": "KORAX-NEWS/1.0"},
                timeout=20,
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if getattr(feed, "bozo", False) and not feed.entries:
                raise RuntimeError(str(feed.bozo_exception))
            successful_sources += 1
            total_entries += len(feed.entries)
            print(f"RSS {source['name']}: {len(feed.entries)} entries")
            for entry in feed.entries[:30]:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()
                if not link or not title:
                    continue
                key = hashlib.sha256(link.split("#")[0].encode()).hexdigest()
                if key not in seen:
                    found.append((entry, source, key))
        except Exception as exc:
            print(f"RSS source failed ({source.get('name', 'unknown')}): {exc!r}")

    if successful_sources == 0:
        raise RuntimeError("All RSS sources failed. Check the feed URLs and network access.")

    # Publish oldest first; cap each run to avoid flooding the channel.
    found.sort(key=lambda item: item[0].get("published_parsed") or time.gmtime(0))
    batch = found[:MAX_POSTS_PER_RUN]
    print(
        f"RSS check complete: {successful_sources}/{len(sources)} sources healthy, "
        f"{total_entries} entries scanned, {len(found)} new, {len(batch)} selected."
    )

    published = 0
    for entry, source, key in batch:
        original_title = entry.get("title", "").strip()
        summary = entry.get("summary", "") or entry.get("description", "")
        summary = re.sub(r"<[^>]+>", " ", html.unescape(summary))
        summary = re.sub(r"\s+", " ", summary).strip()
        title_ar = translate(original_title)
        summary_ar = translate(summary[:1200]) if summary else ""
        link = entry.get("link", "").strip()
        image = get_image(entry)
        caption = build_caption(title_ar, summary_ar, source["name"], link)

        send_message(caption, image, title_ar)
        seen.add(key)
        published += 1
        print(f"Published successfully: {original_title} ({urlparse(link).netloc})")
        time.sleep(1)

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump({"seen": list(seen)[-5000:]}, file, ensure_ascii=False, indent=2)

    print(f"Run finished: {published} news items published.")
    if found and published == 0:
        raise RuntimeError("New stories were found but none could be published.")


if __name__ == "__main__":
    main()
