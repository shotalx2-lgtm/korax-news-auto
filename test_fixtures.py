import unittest
from datetime import datetime, timezone, timedelta

from fixtures import (
    normalize_event, fixture_key, format_cairo_date,
    arabic_team_name, arabic_digits, make_match_card, build_caption,
    due_reminders, reminder_key, parse_goal_fixtures
)
from PIL import Image
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo


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

    def test_24_hour_and_30_minute_reminder_windows(self):
        now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
        match = {"league_slug": "eng.1", "event_id": "chelsea-forest",
                 "kickoff": (now + timedelta(hours=24)).isoformat()}
        self.assertEqual(due_reminders(match, now), ["24h"])
        match["kickoff"] = (now + timedelta(minutes=30)).isoformat()
        self.assertEqual(due_reminders(match, now), ["30m"])
        match["kickoff"] = (now + timedelta(hours=3)).isoformat()
        self.assertEqual(due_reminders(match, now), ["schedule"])

    def test_catch_up_schedule_is_sent_for_new_matches_inside_next_day(self):
        now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
        match = {"kickoff": (now + timedelta(hours=4)).isoformat()}
        self.assertEqual(due_reminders(match, now), ["schedule"])

    def test_goal_fixtures_are_parsed_and_converted_to_cairo_correctly(self):
        html = """
        <h2>Premier League England</h2>
        <a href="/match/fixture-forest-chelsea">
          <img src="/images/forest.png"><img src="/images/chelsea.png">
          Nottingham Forest NFO Chelsea CHE 15:00
        </a>
        """
        parsed = parse_goal_fixtures(
            html,
            datetime(2026, 11, 27).date()
        )
        self.assertEqual(len(parsed), 1)
        match = parsed[0]
        self.assertEqual(match["home"], "Nottingham Forest")
        self.assertEqual(match["away"], "Chelsea")
        self.assertEqual(match["league"], "الدوري الإنجليزي الممتاز")
        self.assertEqual(match["home_logo"], "https://www.goal.com/images/forest.png")
        cairo = datetime.fromisoformat(match["kickoff"]).astimezone(ZoneInfo("Africa/Cairo"))
        self.assertEqual((cairo.hour, cairo.minute), (22, 0))

    def test_goal_parser_ignores_unrecognized_and_finished_rows(self):
        html = """
        <h3>Premier League England</h3>
        <a href="/match/one">Nottingham Forest NFO Chelsea CHE FT</a>
        <a href="/match/two">Invalid Row 15:00</a>
        """
        self.assertEqual(parse_goal_fixtures(html, datetime(2026, 11, 27).date()), [])

    def test_reminder_keys_are_separate_for_same_match(self):
        match = {"league_slug": "eng.1", "event_id": "chelsea-forest",
                 "kickoff": "2026-11-28T12:00:00+00:00"}
        self.assertNotEqual(reminder_key(match, "24h"), reminder_key(match, "30m"))

    def test_reminder_caption_explains_when_match_starts(self):
        match = {"home": "Chelsea", "away": "Nottingham Forest",
                 "league": "الدوري الإنجليزي",
                 "kickoff": "2026-11-28T12:00:00+00:00"}
        self.assertIn("موعد المباراة غدًا", build_caption(match, "24h"))
        self.assertIn("بعد نصف ساعة", build_caption(match, "30m"))

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
