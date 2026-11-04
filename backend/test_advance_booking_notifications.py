"""Tests for FilmUp's per-showtime advance-booking alert tracking."""

import unittest
from advance_booking_routes import _new_slots_by_cinema, _slot_key


class AdvanceBookingSlotTrackingTests(unittest.TestCase):
    def test_same_instant_with_different_timezone_offsets_has_same_key(self):
        first = {"showtime": "2026-11-06T19:30:00+05:00"}
        second = {"showtime": "2026-11-06T14:30:00Z"}
        self.assertEqual(_slot_key(first), _slot_key(second))

    def test_distinct_showtimes_have_distinct_keys(self):
        first = {"showtime": "2026-11-06T14:30:00Z"}
        second = {"showtime": "2026-11-06T17:30:00Z"}
        self.assertNotEqual(_slot_key(first), _slot_key(second))

    def test_existing_slot_is_not_resent_but_later_slot_is_detected(self):
        old_slot = {"cinema_name": "CUE Cinemas", "showtime": "2026-11-06T14:30:00Z"}
        new_slot = {"cinema_name": "CUE Cinemas", "showtime": "2026-11-06T17:30:00Z"}
        already_sent = {"cuecinemas": [_slot_key(old_slot)]}

        matches = _new_slots_by_cinema(
            [old_slot, new_slot],
            ["CUE Cinemas"],
            already_sent,
        )

        self.assertEqual(matches, {"CUE Cinemas": [new_slot]})

    def test_new_cinema_and_new_slot_are_both_detected(self):
        cue_slot = {"cinema_name": "CUE Cinemas", "showtime": "2026-11-06T14:30:00Z"}
        dha_slot = {"cinema_name": "DHA Cinema", "showtime": "2026-11-06T16:30:00Z"}

        matches = _new_slots_by_cinema(
            [cue_slot, dha_slot],
            ["CUE Cinemas", "DHA Cinema"],
            {"cuecinemas": [_slot_key(cue_slot)]},
        )

        self.assertEqual(matches, {"DHA Cinema": [dha_slot]})

    def test_invalid_showtime_is_ignored(self):
        row = {"cinema_name": "CUE Cinemas", "showtime": "not-a-time"}
        self.assertIsNone(_slot_key(row))
        self.assertEqual(_new_slots_by_cinema([row], ["CUE Cinemas"], {}), {})


if __name__ == "__main__":
    unittest.main()
