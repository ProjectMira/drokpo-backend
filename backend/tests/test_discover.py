"""Pure tests for the typed feed composition in app/services/discover.py."""

from app.services import discover


def _person(i):
    return {"uid": f"u{i}"}


def _ad(i):
    return {"adId": f"ad{i}", "title": f"Ad {i}", "linkUrl": "https://x.example"}


def _news(i):
    return {"newsId": f"n{i}", "title": f"News {i}", "gist": "g", "sourceUrl": "https://s.example"}


def _post(i):
    return {"postId": f"p{i}", "kind": "announcement", "title": f"Post {i}"}


def test_content_card_after_every_three_profiles_rotating():
    items = discover.build_items(
        [_person(i) for i in range(9)], [_ad(1)], [_news(1)], [_post(1)]
    )
    types = [item["type"] for item in items]
    # positions 3, 7, 11 (0-indexed) are content, rotating ad -> news -> post
    assert types[3] == "ad" and types[7] == "news" and types[11] == "communityPost"
    assert types.count("person") == 9


def test_empty_queues_are_skipped_in_rotation():
    items = discover.build_items([_person(i) for i in range(6)], [], [_news(1), _news(2)], [])
    content = [item for item in items if item["type"] != "person"]
    assert [c["type"] for c in content] == ["news", "news"]


def test_no_candidates_still_yields_a_swipeable_page():
    items = discover.build_items(
        [], [_ad(i) for i in range(3)], [_news(i) for i in range(20)], [_post(i) for i in range(2)]
    )
    assert len(items) == discover.MIN_ITEMS_PER_PAGE
    assert all(item["type"] in ("ad", "news", "communityPost") for item in items)
    # rotation still applies while queues last
    assert [item["type"] for item in items[:3]] == ["ad", "news", "communityPost"]


def test_few_candidates_topped_up_with_content():
    items = discover.build_items([_person(1), _person(2)], [], [_news(i) for i in range(30)], [])
    assert len(items) == discover.MIN_ITEMS_PER_PAGE
    assert items[0]["type"] == "person" and items[1]["type"] == "person"
    assert all(item["type"] == "news" for item in items[2:])


def test_everything_empty_yields_empty_page():
    assert discover.build_items([], [], [], []) == []


def test_no_duplicate_content_within_a_page():
    items = discover.build_items([], [_ad(1)], [_news(1)], [_post(1)])
    ids = [item["data"].get("adId") or item["data"].get("newsId") or item["data"].get("postId") for item in items]
    assert len(ids) == len(set(ids)) == 3


def test_posts_with_ads_interleave():
    items = discover.interleave_posts_with_ads(
        [_post(i) for i in range(9)], [_ad(1), _ad(2), _ad(3)]
    )
    types = [item["type"] for item in items]
    # one ad after every 4 posts; the third ad never fires (only 1 post follows)
    assert types == [
        "communityPost", "communityPost", "communityPost", "communityPost", "ad",
        "communityPost", "communityPost", "communityPost", "communityPost", "ad",
        "communityPost",
    ]


def test_no_posts_means_no_ads():
    assert discover.interleave_posts_with_ads([], [_ad(1)]) == []
