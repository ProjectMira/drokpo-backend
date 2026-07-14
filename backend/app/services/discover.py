"""Pure composition helpers for the typed Discover / communities-home feeds.

Each item is a discriminated union {"type": ..., "data": ...} so clients can
render heterogeneous cards from one ordered list. Clients must skip unknown
`type` values (forward compatibility for future card kinds).
"""

PROFILES_PER_CONTENT = 3
CONTENT_ROTATION = ("ad", "news", "communityPost")
# The Discover deck must never feel empty: when profiles run low the page is
# topped up with content cards so there is always something to swipe. The
# client re-requests as the deck runs low; with no new people, each request
# serves the content queues again — news and ads repeat rather than the deck
# going dark.
MIN_ITEMS_PER_PAGE = 12
POSTS_PER_AD = 4


def build_items(candidates: list[dict], ads: list[dict], news: list[dict], posts: list[dict]) -> list[dict]:
    """Discover page: one content card after every PROFILES_PER_CONTENT
    person cards, rotating ad -> news -> communityPost; then topped up with
    remaining content until MIN_ITEMS_PER_PAGE so few/zero candidates still
    yield a swipeable page."""
    queues = {"ad": list(ads), "news": list(news), "communityPost": list(posts)}
    cursors = dict.fromkeys(CONTENT_ROTATION, 0)
    rotation = 0

    def next_content() -> dict | None:
        nonlocal rotation
        for _ in range(len(CONTENT_ROTATION)):
            kind = CONTENT_ROTATION[rotation % len(CONTENT_ROTATION)]
            rotation += 1
            queue, cursor = queues[kind], cursors[kind]
            if cursor < len(queue):
                cursors[kind] = cursor + 1
                return {"type": kind, "data": queue[cursor]}
        return None

    items: list[dict] = []
    since_content = 0
    for candidate in candidates:
        items.append({"type": "person", "data": candidate})
        since_content += 1
        if since_content >= PROFILES_PER_CONTENT:
            if content := next_content():
                items.append(content)
                since_content = 0
    while len(items) < MIN_ITEMS_PER_PAGE:
        content = next_content()
        if content is None:
            break
        items.append(content)
    return items


def interleave_posts_with_ads(posts: list[dict], ads: list[dict]) -> list[dict]:
    """Communities home feed: one sponsored card after every POSTS_PER_AD
    joined-community posts. Ads never appear without posts around them — an
    empty joined feed stays empty (the screen shows a join prompt instead)."""
    items: list[dict] = []
    ad_cursor = 0
    since_ad = 0
    for post in posts:
        items.append({"type": "communityPost", "data": post})
        since_ad += 1
        if since_ad >= POSTS_PER_AD and ad_cursor < len(ads):
            items.append({"type": "ad", "data": ads[ad_cursor]})
            ad_cursor += 1
            since_ad = 0
    return items
