"""The similar-group keeper is only a recommendation when the scores support it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "admin"))

from group_labels import similar_labels  # noqa: E402


def photo(pid: str, score):
    return {"id": pid, "score": {"composite": score} if score is not None else None}


class SimilarLabelsTest(unittest.TestCase):
    def test_unique_leader(self):
        keeper, labels, tie = similar_labels([photo("a", 3.0), photo("b", 2.0)])
        self.assertEqual(keeper, "a")
        self.assertEqual(labels, {"a": "behalten", "b": "schwächer"})
        self.assertFalse(tie)

    def test_tied_leaders_have_no_keeper(self):
        keeper, labels, tie = similar_labels([photo("a", 3.0), photo("b", 3.0)])
        self.assertIsNone(keeper)
        self.assertEqual(labels, {"a": "gleich", "b": "gleich"})
        self.assertTrue(tie)

    def test_missing_scores_have_no_keeper(self):
        keeper, labels, tie = similar_labels([photo("a", None), photo("b", None)])
        self.assertIsNone(keeper)
        self.assertEqual(labels, {"a": "keine Wertung", "b": "keine Wertung"})
        self.assertFalse(tie)

    def test_unrated_photo_is_not_called_weaker(self):
        keeper, labels, _tie = similar_labels([photo("a", 3.0), photo("b", None)])
        self.assertEqual(keeper, "a")
        self.assertEqual(labels["b"], "keine Wertung")


if __name__ == "__main__":
    unittest.main()
