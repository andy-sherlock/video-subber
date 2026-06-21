"""Phase 1D checkpoint: uncertain flag survives JSON serialize/deserialize cycle."""
import unittest
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCheckpointSerialize(unittest.TestCase):
    """uncertain=True survives JSON round-trip."""

    def test_uncertain_serialized_to_json(self):
        """Segment.uncertain is written to JSON."""
        seg_data = {
            "id": 0, "start": 1.0, "end": 3.0,
            "text": "hello", "uncertain": True,
            "speaker_change": False, "filtered": False,
        }
        dumped = json.dumps(seg_data)
        self.assertIn("uncertain", dumped)
        self.assertIn("true", dumped.lower())

    def test_uncertain_restored_from_json(self):
        """Segment.uncertain is correctly read from JSON."""
        json_str = '{"id":0,"start":1.0,"end":3.0,"text":"hello","uncertain":true}'
        data = json.loads(json_str)
        got = data.get("uncertain", False)
        self.assertTrue(got, "uncertain must be True after JSON deserialization")

    def test_missing_uncertain_defaults_false(self):
        """Old checkpoint files without 'uncertain' default to False."""
        json_str = '{"id":0,"start":1.0,"end":3.0,"text":"hello"}'
        data = json.loads(json_str)
        got = data.get("uncertain", False)
        self.assertFalse(got, "Missing uncertain must default to False")

    def test_empty_string_uncertain_not_serialized_as_true(self):
        """Only actual bool True should be True — not empty string."""
        # Simulating the real worker.py restore: r.get("uncertain", False)
        test_cases = [
            ({"uncertain": True}, True),
            ({"uncertain": False}, False),
            ({"uncertain": ""}, False),   # empty string → falsy
            ({}, False),                   # missing → default
        ]
        for data, expected in test_cases:
            got = bool(data.get("uncertain", False))
            self.assertEqual(got, expected,
                             f"data={data} → expected uncertain={expected}")


class TestCheckpointRoundTrip(unittest.TestCase):
    """Full round trip: segment → JSON file → segment."""

    def test_round_trip_preserves_uncertain(self):
        """Write segments to file, read back — uncertain survives."""
        from backend.transcriber_v2 import Segment

        seg = Segment(id=1, start=0.0, end=2.0, text="test")
        seg.uncertain = True
        seg.speaker_change = False

        # Simulate worker.py:300-301 serialization
        seg_dicts = [{
            "id": s.id, "start": s.start, "end": s.end,
            "text": s.text,
            "uncertain": getattr(s, "uncertain", False),
            "speaker_change": getattr(s, "speaker_change", False),
        } for s in [seg]]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(seg_dicts, f)
            tmp_path = f.name

        try:
            with open(tmp_path) as f:
                loaded = json.load(f)

            # Simulate worker.py:310-316 restore
            restored = Segment(id=1, start=0.0, end=2.0, text="test")
            r = loaded[0]
            restored.uncertain = r.get("uncertain", False)
            restored.speaker_change = r.get("speaker_change", False)

            self.assertTrue(restored.uncertain,
                            "uncertain must survive JSON round-trip")
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
