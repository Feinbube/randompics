"""Score recommendation for a similar-photo group (no Flask, easy to test)."""

from __future__ import annotations


def similar_labels(photos: list[dict]) -> tuple[str | None, dict[str, str], bool]:
    """Return (keeper id, per-photo labels, is_tie).

    A keeper exists only when exactly one photo has the best composite score.
    Ties are labelled "gleich"; photos without a score are never called weaker.
    """
    rated = [(photo, (photo.get("score") or {}).get("composite")) for photo in photos]
    rated = [(photo, score) for photo, score in rated if score is not None]
    if not rated:
        return None, {photo["id"]: "keine Wertung" for photo in photos}, False
    best = max(score for _photo, score in rated)
    winners = [photo for photo, score in rated if score == best]
    if len(winners) > 1:
        winner_ids = {photo["id"] for photo in winners}
        rated_ids = {photo["id"] for photo, _score in rated}
        labels = {photo["id"]: ("gleich" if photo["id"] in winner_ids else
                                "schwächer" if photo["id"] in rated_ids else "keine Wertung")
                  for photo in photos}
        return None, labels, True
    keeper = winners[0]
    labels = {}
    for photo in photos:
        score = (photo.get("score") or {}).get("composite")
        labels[photo["id"]] = ("behalten" if photo is keeper else
                               "schwächer" if score is not None else "keine Wertung")
    return keeper["id"], labels, False
