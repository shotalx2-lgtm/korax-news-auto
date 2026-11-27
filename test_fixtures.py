import unittest
from datetime import datetime, timezone

from fixtures import (
    normalize_event, fixture_key, format_cairo_date,
    arabic_team_name, arabic_digits, make_match_card, build_caption
)
from PIL import Image
from io import BytesIO
from unittest.mock import patch


class FixtureParsingTests(unittest.TestCase):
    def sample_event(self):
        return {
            "id": "sample-123",
            "date": "2026-11-27T20:00:00Z",
            "season": {"year": 2026, "type": {"name": "League Phase"}},
            "competitions": [{
                "altGameNote": "UEFA Champions League, League Phase",
                "status": {"type": {"state": "pre"}},
                "venue": {"fullName": "Example Stadium", "address": {"city": "Cairo", "country": "Egypt"}},
                "competitors": [
                    {"homeAway": "home", "form": "WWDLW", "team": {"displayName": "Home FC", "logo": "https://example.com/home.png"}},
                    {"homeAway": "away", "form": "LWWDW", "team": {"displayName": "Away FC", "logo": "https://example.com/away.png"}}
                ]
            }]
        }

    def test_normalize_event_preserves_badges_and_teams(self):
        normalized = normalize_event({"slug": "uefa.champions", "name": "دوري أبطال أوروبا"},
                                     "UEFA Champions League", self.sample_event())
        self.assertEqual(normalized["home"], "Home FC")
        self.assertEqual(normalized["away"], "Away FC")
        self.assertTrue(normalized["home_logo"].endswith("home.png"))
        self.assertEqual(normalized["stage"], "League Phase")
        self.assertIn("Example Stadium", normalized["venue"])

    def test_logo_array_fallback_and_final_status(self):
        event = self.sample_event()
        event["competitions"][0]["competitors"][0]["team"].pop("logo")
        event["competitions"][0]["competitors"][0]["team"]["logos"] = [
            {"href": "https://example.com/home-badge.png"}
        ]
        event["competitions"][0]["status"]["type"] = {
            "state": "post", "name": "STATUS_FINAL"
        }
        normalized = normalize_event(
            {"slug": "uefa.champions", "name": "دوري أبطال أوروبا"},
            "UEFA Champions League", event
        )
        self.assertEqual(normalized["home_logo"], "https://example.com/home-badge.png")
        self.assertEqual(normalized["status"], "post")

    def test_reschedule_changes_deduplication_key(self):
        league = {"slug": "eng.1", "name": "الدوري الإنجليزي"}
        first = normalize_event(league, "English Premier League", self.sample_event())
        changed = self.sample_event()
        changed["date"] = "2026-11-28T20:00:00Z"
        second = normalize_event(league, "English Premier League", changed)
        self.assertNotEqual(fixture_key(first), fixture_key(second))

    def test_cairo_time_conversion(self):
        result = format_cairo_date(datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc))
        self.assertIn("27 نوفمبر 2026", result["date"])
        self.assertIn("10:00 م", result["time"])

    def test_known_club_names_are_arabic(self):
        self.assertEqual(arabic_team_name("Chelsea"), "تشيلسي")
        self.assertEqual(arabic_team_name("Nottingham Forest"), "نوتينجهام فورست")
        self.assertEqual(arabic_team_name("Real Madrid"), "ريال مدريد")

    def test_unknown_latin_club_name_is_transliterated(self):
        name = arabic_team_name("Example United")
        self.assertTrue(name)
        self.assertNotRegex(name, r"[A-Za-z]")

    def test_match_card_is_landscape_and_uses_arabic_names(self):
        match = {
            "home": "Chelsea",
            "away": "Nottingham Forest",
            "home_logo": "https://example.com/home.png",
            "away_logo": "https://example.com/away.png",
            "league": "الدوري الإنجليزي الممتاز",
            "league_logo": "https://example.com/league.png",
            "kickoff": "2026-11-27T20:00:00Z",
        }
        fake_badge = Image.new("RGBA", (160, 160), (25, 80, 20, 255))
        with patch("fixtures._download_logo", return_value=fake_badge):
            image = Image.open(BytesIO(make_match_card(match).getvalue()))
        self.assertEqual(image.size, (1200, 675))
        match["round"] = "Matchday 12"
        caption = build_caption(match)
        self.assertIn("تشيلسي ضد نوتينجهام فورست", caption)
        self.assertIn("١٠:٠٠ م", caption)
        self.assertIn("الجولة ١٢", caption)
        self.assertIn("كوراكس", caption)
        self.assertNotIn("Chelsea", caption)
        self.assertNotIn("KORAX", caption)


if __name__ == "__main__":
    unittest.main()
