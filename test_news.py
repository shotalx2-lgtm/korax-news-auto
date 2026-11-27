import unittest
from unittest.mock import patch

import bot


class NewsImageTests(unittest.TestCase):
    def test_image_fingerprint_changes_with_photo_bytes(self):
        self.assertEqual(bot.image_fingerprint(b"same-photo"), bot.image_fingerprint(b"same-photo"))
        self.assertNotEqual(bot.image_fingerprint(b"photo-one"), bot.image_fingerprint(b"photo-two"))

    def test_news_post_has_expandable_quote_without_external_link_or_source_label(self):
        rendered = bot.build_news_rich_html(
            "عنوان رياضي",
            "تفاصيل إضافية مؤكدة عن المباراة. تطورات جديدة في الفريق.",
            "الكرة العربية",
        )
        self.assertIn("<blockquote expandable>", rendered)
        self.assertIn("تفاصيل إضافية مؤكدة", rendered)
        self.assertIn('tg://photo?id=korax_news_photo', rendered)
        self.assertNotIn("<a href=", rendered)
        self.assertNotIn("المصدر:", rendered)

    def test_empty_description_does_not_invent_story_details(self):
        rendered = bot.build_news_rich_html("عنوان رياضي", "", "كرة قدم")
        self.assertIn("لم تتوفر تفاصيل إضافية موثوقة", rendered)

    @patch("bot.telegram_api")
    def test_send_news_uses_only_standard_telegram_methods_and_no_source_link(self, api):
        bot.send_news(b"photo-bytes", "عنوان الخبر", "تفاصيل موسعة داخل القناة", "الكرة العربية")
        self.assertEqual([call.args[0] for call in api.call_args_list], ["sendPhoto", "sendMessage"])
        detail_call = api.call_args_list[1]
        message = detail_call.kwargs["data"]["text"]
        self.assertIn("<blockquote expandable>", message)
        self.assertIn("تفاصيل موسعة داخل القناة", message)
        self.assertNotIn("http", message)
        self.assertNotIn("المصدر:", message)

    @patch("bot._try_image_urls", return_value=(2_000_000, b"publisher-photo"))
    @patch("bot.article_image_candidates", return_value=["https://publisher.example/og-image.jpg"])
    def test_prefers_actual_article_image_before_rss_thumbnail(self, _article_candidates, try_images):
        entry = {
            "source": {"href": "https://publisher.example"},
            "media_content": [{"url": "https://feeds.example/shared-thumbnail.jpg"}],
        }
        result = bot.download_best_image(entry, "https://publisher.example/story")
        self.assertEqual(result, b"publisher-photo")
        self.assertEqual(try_images.call_count, 1)
        self.assertEqual(try_images.call_args.args[0], ["https://publisher.example/og-image.jpg"])

    @patch("bot._try_image_urls", side_effect=[None, (1_000_000, b"rss-photo")])
    @patch("bot.article_image_candidates", return_value=[])
    @patch("bot.image_candidates", return_value=[(0, "https://feeds.example/article-photo.jpg")])
    def test_rss_photo_is_only_a_fallback(self, _rss_candidates, _article_candidates, _try_images):
        result = bot.download_best_image({"source": {}}, "https://publisher.example/story")
        self.assertEqual(result, b"rss-photo")


if __name__ == "__main__":
    unittest.main()
