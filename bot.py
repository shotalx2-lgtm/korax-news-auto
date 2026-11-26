import os, json, hashlib, html, re, time
from urllib.parse import urlparse
import requests
import feedparser

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@KoraxNews11")
STATE_FILE = "seen_news.json"
SOURCES_FILE = "sources.json"
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

def translate(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\\s+", " ", text)).strip()
    if not text:
        return ""
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client":"gtx","sl":"auto","tl":"ar","dt":"t","q":text[:3500]},
            timeout=15,
            headers={"User-Agent":"Mozilla/5.0"}
        )
        r.raise_for_status()
        data = r.json()
        result = "".join(part[0] for part in data[0] if part and part[0])
        return result.strip() or text
    except Exception:
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

def send_message(text, image=None):
    if image:
        r = requests.post(f"{TG}/sendPhoto", data={
            "chat_id": CHAT_ID, "photo": image, "caption": text,
            "parse_mode": "HTML"
        }, timeout=30)
        if r.ok:
            return
        print("Image send failed; sending text:", r.text[:300])
    r = requests.post(f"{TG}/sendMessage", data={
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": False
    }, timeout=30)
    r.raise_for_status()

def main():
    with open(SOURCES_FILE, encoding="utf-8") as f:
        sources = json.load(f)
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            seen = set(json.load(f).get("seen", []))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = set()

    found = []
    for source in sources:
        try:
            feed = feedparser.parse(source["url"], request_headers={"User-Agent":"KORAX-NEWS/1.0"})
            if getattr(feed, "bozo", False) and not feed.entries:
                print("Feed error:", source["name"], feed.bozo_exception)
                continue
            for entry in feed.entries[:30]:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()
                if not link or not title:
                    continue
                key = hashlib.sha256(link.split("#")[0].encode()).hexdigest()
                if key in seen:
                    continue
                found.append((entry, source, key))
        except Exception as e:
            print("Source error:", source["name"], repr(e))

    # Oldest first so a burst of new stories appears in chronological order.
    found.sort(key=lambda item: item[0].get("published_parsed") or time.gmtime(0))

    for entry, source, key in found:
        original_title = entry.get("title", "").strip()
        summary = entry.get("summary", "") or entry.get("description", "")
        summary = re.sub(r"<[^>]+>", " ", html.unescape(summary))
        summary = re.sub(r"\\s+", " ", summary).strip()
        title_ar = translate(original_title)
        summary_ar = translate(summary[:1200]) if summary else ""
        link = entry.get("link", "").strip()
        image = get_image(entry)
        host = urlparse(link).netloc.replace("www.", "")
        caption = (
            f"⚽ <b>{html.escape(title_ar)}</b>\n\n"
            + (html.escape(summary_ar[:700]) + "\n\n" if summary_ar else "")
            + f"📰 المصدر: <b>{html.escape(source['name'])}</b>\n"
            + f"🔗 <a href=\"{html.escape(link, quote=True)}\">اقرأ الخبر الأصلي</a>"
        )
        if len(caption) > 1000 and image:
            caption = caption[:990] + "…"
        try:
            send_message(caption, image)
            seen.add(key)
            print("Published:", original_title, host)
            time.sleep(1)
        except Exception as e:
            print("Publish failed:", original_title, repr(e))
            # Keep failed items unseen so the next run can retry.

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": list(seen)[-5000:]}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
