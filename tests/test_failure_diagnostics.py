"""Software-only aggregation regression; never a Teacher success example."""

import json

from scripts.analyze_failure_retry import recommendation_counts


def test_counts_every_artist_once_per_unique_song_across_repeated_pages():
    a = {"id": 1, "name": "synthetic_A"}
    b = {"id": 2, "name": "synthetic_B"}
    first = {"song_id": 11, "artists": [a, b, b]}
    second = {"song_id": 12, "artists": [b]}
    messages = [
        {
            "role": "tool",
            "name": "spotify__show_recommendations",
            "content": json.dumps(page),
        }
        for page in [[first], [first, second], []]
    ]
    seen, counts, pages, names, multi = recommendation_counts(messages)
    assert seen == {11, 12}
    assert dict(counts) == {1: 1, 2: 2}
    assert sum(counts.values()) == 3
    assert pages == [1, 2, 0] and multi == 1
    assert names == {1: "synthetic_A", 2: "synthetic_B"}
