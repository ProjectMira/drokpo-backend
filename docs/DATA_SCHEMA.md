# Firestore Data Schema

Cloud Firestore is document/collection-based, not tables-and-rows — there's no single CREATE TABLE source of truth, so this document *is* the schema reference. It reflects exactly what the code in `backend/app/services/` writes and what [firestore.rules](../firestore.rules) enforces, not an aspirational future version.

## Conventions used throughout

- **Document ID = the natural key**, wherever one exists, instead of an auto-generated ID plus a foreign-key field. `users/{uid}` is keyed by the Firebase Auth uid; `matches/{matchId}` is keyed by a deterministic `sorted(uidA, uidB)` join. This makes existence checks and idempotent writes a single `get()`/`set()` instead of a query.
- **`createdAt` / `updatedAt`** are real Firestore `Timestamp` values, written server-side with `firestore.SERVER_TIMESTAMP` — never a client-supplied date — so they can't be spoofed and sort correctly regardless of device clocks.
- **No relational joins.** Data that's read together is denormalized onto the same document (e.g. `matches.lastMessage` is a copy of the newest message, not a lookup) — see [TECH_STACK.md](TECH_STACK.md) for why Firestore was chosen over a relational option for this app.
- **Admin SDK vs. client SDK.** Every collection below notes who is actually allowed to write it. "Backend only" means the security rule is `allow write: if false` and the *only* writer is FastAPI/Cloud Functions through the Admin SDK, which bypasses rules entirely. "Client direct" means the mobile/web app writes it straight to Firestore under rule enforcement.

## Collection map

```
users/{uid}
  ├── swipes/{targetUid}
  └── memberships/{cid}

matches/{matchId}
  └── messages/{messageId}

reports/{reportId}

blocks/{uid}
  └── blockedUsers/{blockedUid}

ads/{adId}

communities/{uid}
  └── members/{memberUid}

communityPosts/{postId}
  ├── votes/{uid}
  └── rsvps/{uid}

news/{newsId}

mail/{autoId}
```

There is no top-level `chats` or `likes` collection — swipes live under the swiping user, and messages live under the match they belong to, since both are always queried in that scoped context.

---

## `users/{uid}`

The profile document. One per Firebase Auth account, created by `POST /onboarding` ([users.py](../backend/app/services/users.py) `create_profile`) and mutated by `PATCH /profile/me` and the photo endpoints.

| Field | Type | Written by | Notes |
|---|---|---|---|
| `displayName` | string | onboarding, profile update | |
| `dob` | string | onboarding, profile update | Plain ISO date string (`"1998-04-12"`), not a Firestore Timestamp |
| `gender` | string \| null | onboarding, profile update | Optional profile info — not used to filter the feed |
| `interests` | array\<string\> | onboarding, profile update | Free-text tags (e.g. "momo cooking", "gorshey", "hiking") — drives the feed's shared-interest ranking in `get_candidates` |
| `socials` | map `{ instagram: string, youtube?, tiktok?, facebook?, x?, wechat? }` | onboarding, profile update | `instagram` is **required** at onboarding and can be changed but never cleared (validated in [user.py](../backend/app/models/user.py)); other platforms are optional. Partial updates merge via dotted paths (`socials.youtube`) so omitted platforms are untouched |
| `bio` | string | onboarding, profile update | |
| `occupation` | string | onboarding, profile update | Current job / profession |
| `education` | string | onboarding, profile update | Education level (e.g. "Bachelor's", "Monastic education") |
| `answers` | map\<string, string\> | onboarding, profile update | Profile Q&A prompts ("Chai or butter tea?", "Places I've travelled to", favourite movies/music, …). Keys are stable question ids the app defines; capped at 30 entries × 500 chars, validated in [user.py](../backend/app/models/user.py) `clean_answers`. Replaced wholesale on update; empty values are dropped |
| `region` | string | onboarding, profile update | Free text, e.g. "U-Tsang", "Kham", "Amdo", or a diaspora city |
| `languages` | array\<string\> | onboarding, profile update | |
| `location` | map `{ lat: number, lng: number, geohash: string }` | onboarding, profile update | `geohash` is always recomputed server-side ([geo.py](../backend/app/services/geo.py)) — a client sends only lat/lng and can never desync the geohash |
| `preferences` | map `{ ageMin: number, ageMax: number, distanceKm: number }` | onboarding, profile update | `ageMin`/`ageMax` drive the feed's age filter. Partial updates merge via dotted field paths (`preferences.ageMin`), so sending only `distanceKm` does not reset the others |
| `photos` | array of maps `{ storagePath: string, order: number, url: string }` | backend, via `add_photo`/`remove_photo` | Capped at 6 (`MAX_PHOTOS` in [users.py](../backend/app/services/users.py)); `add_photo` rejects further attaches with a 400. `url` is a stable token download URL minted server-side at attach time ([storage.py](../backend/app/services/storage.py) `ensure_download_url`, which also stamps `Cache-Control` on the blob) — the client renders it directly instead of paying a Storage-SDK `getDownloadURL()` round-trip per photo. Photos attached before `url` existed are covered by `backend/scripts/backfill_photo_urls.py`; clients should still fall back to deriving from `storagePath` if `url` is absent |
| `fcmTokens` | array\<string\> | backend, via `POST`/`DELETE /profile/me/fcm-tokens` | Device tokens the notification Cloud Functions ([functions/main.py](../functions/main.py)) fan out to; the client registers its token after sign-in |
| `status` | string: `"active"` \| `"paused"` \| `"banned"` | backend, set to `"active"` at creation | No endpoint currently transitions it to `"paused"` or `"banned"` — this is a placeholder for a future moderation/self-pause feature |
| `onboardingComplete` | boolean | backend, flipped `true` by `POST /onboarding/complete` | Gates access to `GET /feed` |
| `createdAt` / `updatedAt` | Timestamp | backend | |

**Access rule:** owner-only **read**; all client writes are blocked (`allow write: if false`). Every write goes through FastAPI's Admin SDK, so the backend's validation — onboarding gating, server-computed geohash, photo-path ownership, the 6-photo cap — cannot be bypassed by a client writing its own document directly.

### `users/{uid}/swipes/{targetUid}`

One document per swipe the user has made, keyed by the target's uid for O(1) "have I swiped on this person" lookups from the feed query and the match-detection transaction.

| Field | Type | Notes |
|---|---|---|
| `action` | string: `"like"` \| `"pass"` \| `"superlike"` | |
| `createdAt` | Timestamp | |

**Access rule:** `allow read, write: if false` — entirely backend-only at the Firestore layer. A client can never read or forge a swipe directly; every swipe is created by [matching.py](../backend/app/services/matching.py) as part of the swipe transaction. Users can list their *own* swipe history through the API instead: `GET /swipes` (optionally `?action=like` for likes sent).

---

## `matches/{matchId}`

Created only when both sides have liked each other, inside a Firestore transaction ([matching.py](../backend/app/services/matching.py)) that reads the reverse swipe before writing anything — Firestore requires all transaction reads to precede all writes, which also naturally prevents duplicate matches from two simultaneous "like"s.

**Document ID:** `"_".join(sorted([uidA, uidB]))` — deterministic, not auto-generated, so re-running the match check is idempotent (`match_ref.get(transaction=...)` inside the same transaction checks it doesn't already exist).

| Field | Type | Written by | Notes |
|---|---|---|---|
| `users` | array\<string\> (length 2) | backend, at creation | `sorted([uidA, uidB])` |
| `status` | string: `"active"` \| `"unmatched"` | backend | Set to `"unmatched"` by `POST /matches/{matchId}/unmatch` ([matches.py](../backend/app/services/matches.py)). An unmatched doc is never resurrected: a later re-like reports no match, and the message-create rule requires `status == "active"` |
| `createdAt` | Timestamp | backend | |
| `lastMessage` | map `{ text, senderId, createdAt }` or `null` | Cloud Function (`on_message_created`) | Denormalized copy of the newest message, kept in sync as messages arrive — lets the match-list screen render previews without reading every thread |
| `unreadCount` | map `{ [uid]: number }` | Cloud Function (`on_message_created`) increments; `POST /matches/{matchId}/read` resets the caller's counter to 0 | |

**Access rule:** clients can `read` a match only if their uid is in its `users` array; `write` is `false` — only the backend creates/updates it.

### `matches/{matchId}/messages/{messageId}`

Messages can be written two ways — **directly by clients** under rule enforcement (the real-time chat path, see [CHAT_SYSTEM.md](CHAT_SYSTEM.md)), or through the REST endpoints (`POST /matches/{matchId}/messages`, backed by [messages.py](../backend/app/services/messages.py)), which perform the same participant/active checks server-side. Reads are available both as real-time client listeners and via `GET /matches/{matchId}/messages` (paginated) and `GET /messages/sent` (collection-group query over the caller's sent messages). Document ID is Firestore's auto-generated ID.

| Field | Type | Enforced by rules? | Notes |
|---|---|---|---|
| `senderId` | string | **Yes** — must equal `request.auth.uid` | Prevents impersonating the other participant |
| `text` | string | No | Rules don't currently check length or content — worth adding a max-length check if abuse becomes a concern |
| `imageUrl` | string \| null | No | Reserved field; no upload path wires into it yet |
| `createdAt` | Timestamp | No | Convention is a client-set `serverTimestamp()`, but rules don't enforce its type or presence |
| `readAt` | Timestamp \| null | No | Reserved field; nothing writes it yet (see Known Gaps) |

**Access rule:** `read` requires the caller's uid to be in the parent match's `users` array (checked via `get()` on the parent doc); `create` additionally requires the parent match's `status` to be `"active"`, so messaging stops the moment either side unmatches (history stays readable). `update`/`delete` are `false` — messages are immutable once created.

**Worked example.** Two users match, then exchange three messages:

```jsonc
// matches/uidA_uidB
{
  "users": ["uidA", "uidB"],
  "status": "active",
  "createdAt": "2026-07-01T09:12:03Z",
  "lastMessage": {
    "text": "Tashi delek! Nice to match with you.",
    "senderId": "uidB",
    "createdAt": "2026-07-01T09:15:41Z"
  },
  "unreadCount": { "uidA": 1, "uidB": 0 }
}

// matches/uidA_uidB/messages/msg1  (auto-generated ID)
{ "senderId": "uidA", "text": "Hi!", "imageUrl": null, "createdAt": "2026-07-01T09:14:02Z", "readAt": "2026-07-01T09:14:30Z" }

// matches/uidA_uidB/messages/msg2  (auto-generated ID)
{ "senderId": "uidB", "text": "Tashi delek! Nice to match with you.", "imageUrl": null, "createdAt": "2026-07-01T09:15:41Z", "readAt": null }
```

Note that `msg1`'s `readAt` is populated in this example to show the *intended* shape — as flagged in Known Gaps below, nothing in the current codebase actually writes `readAt` yet, so in practice every message today stays `null` forever.

**Query pattern the client uses** (there is no `GET /messages` endpoint — this is a direct Firestore SDK query, not an API call):

```
matches/{matchId}/messages
  .orderBy("createdAt", "desc")
  .limit(30)
  // scroll-back pagination: .startAfter(lastVisibleDoc) on the next page
```

Firestore's auto-generated document ID is not chronologically sortable, which is why every read of this subcollection must explicitly `orderBy("createdAt")` rather than relying on default document order. A single-field index on `createdAt` is created automatically by Firestore (only *composite* indexes need the explicit entries in [firestore.indexes.json](../firestore.indexes.json)), so no extra index configuration is needed for this query.

---

## `reports/{reportId}`

Created by `POST /reports` ([reports.py](../backend/app/services/reports.py)). Auto-generated document ID.

| Field | Type |
|---|---|
| `reporterUid` | string |
| `reportedUid` | string |
| `reason` | string |
| `note` | string |
| `status` | string, always `"open"` at creation |
| `createdAt` | Timestamp |

**Access rule:** `allow read, write: if false` — write-only from the backend's perspective, and not even backend-readable through the client SDK (there's no client-facing read path; moderation would query this via the Admin SDK/console, not through the app).

## `blocks/{uid}/blockedUsers/{blockedUid}` and `blocks/{uid}/blockedBy/{blockerUid}`

Marker documents — existence is the signal, there's no meaningful payload beyond a timestamp. Blocks are written **bidirectionally** in one batch ([reports.py](../backend/app/services/reports.py)): when A blocks B, both `blocks/A/blockedUsers/B` and `blocks/B/blockedBy/A` are created, so *both* users' feed queries can exclude the other with a cheap read of their own subcollections instead of a collection-group scan.

| Field | Type |
|---|---|
| `createdAt` | Timestamp |

**Where blocks are enforced:** `get_candidates` excludes the union of the caller's `blockedUsers` and `blockedBy` sets from the feed, and `record_swipe` refuses (HTTP 403) any swipe where either direction of block exists — so a blocked pair can never match.

**Access rule:** the owning uid can `read` their own `blockedUsers` list; `blockedBy` is deliberately unreadable even by its owner (deny-by-default) so users can't discover who blocked them. All writes are backend-only (`POST`/`DELETE /blocks/{target_uid}`).

---

## `ads/{adId}`

Sponsored cards interleaved into the Discover deck (one after every 3 real profiles, client-side). Authored **by hand in the Firebase console** — there is no admin API. Served by `GET /api/feed` (`ads` key, via [ads.py](../backend/app/services/ads.py) `list_active`); impressions/clicks are bumped by `POST /api/ads/{adId}/events`. See [ADS.md](ADS.md) for the how-to.

| Field | Type | Notes |
|---|---|---|
| `active` | boolean | **Required.** Only `active == true` ads are served; flip to `false` to pull an ad instantly |
| `title` | string | **Required.** Card headline |
| `linkUrl` | string | **Required.** Opened in the in-app browser when a member likes the ad. Ads missing `title` or `linkUrl` are skipped |
| `body` | string | Optional description shown on the card |
| `ctaLabel` | string | Optional call-to-action label (defaults to "Learn more" in the app) |
| `imageUrl` | string | Optional public https image URL |
| `photos` | array of maps `{ storagePath?: string, url?: string }` | Optional, same shape as profile photos; `storagePath` should live under `ads/` in Storage (readable by any signed-in user per [storage.rules](../storage.rules)). `imageUrl` is a convenience shorthand for a single-image ad |
| `order` | number | Sort key, lowest first; defaults to 0 |
| `impressions` / `clicks` | number | Counters incremented by the events endpoint — never sent to clients |

**Access rule:** no client access (deny-by-default; the collection has no rules entry). Everything flows through the API.

---

## `communities/{uid}` — doc ID = the community's Firebase Auth uid

A community/organization account — an alternative to `users/{uid}` for the same Firebase Auth uid; a given uid is registered as exactly one or the other, enforced by a 409 on whichever onboarding endpoint (`POST /api/onboarding` vs `POST /api/communities/onboarding`) runs second. Created by `POST /api/communities/onboarding` ([communities.py](../backend/app/services/communities.py) `create_community`).

| Field | Type | Notes |
|---|---|---|
| `name` | string | 2–80 chars |
| `description` | string | ≤2000 chars |
| `website` | string \| null | https URL only |
| `phone` / `email` | string \| null | |
| `contactPerson` | map `{name, role?, phone?, email?}` | `name` required |
| `address` | map `{line1?, city, state?, country, postalCode?}` | `city`/`country` required |
| `socials` | map, same shape as a person's `socials` | all optional — no required handle here |
| `photos` | array of maps `{storagePath, order, url}` | capped at 6, same minted-URL flow as user photos; paths live under `communities/{uid}/photos/` in Storage |
| `verification` | string: `"pending"` \| `"verified"` \| `"rejected"` | starts `"pending"`; flipped by hand in the console — see [COMMUNITIES.md](COMMUNITIES.md) |
| `memberCount` | number | maintained with `firestore.Increment` inside the join/leave batch — never recomputed by scanning `members` |
| `createdAt` / `updatedAt` | Timestamp | backend |

**Access rule:** no client access (deny-by-default, same as `ads`) — everything flows through the API.

### `communities/{cid}/members/{uid}` and `users/{uid}/memberships/{cid}`

Bidirectional marker docs, written in one batch by `join_community`/`leave_community` ([communities.py](../backend/app/services/communities.py)) — same pattern as `blocks`. `memberships/{cid}` denormalizes `communityName` so "my communities" can render before hydrating full community docs; `members/{uid}` has no payload beyond `createdAt`.

Member *lists* (`GET /api/communities/{cid}/members`) are members-only — the caller must either be a member of `{cid}` or be `{cid}` itself (`is_member_or_self`); everyone else only ever sees the aggregate `memberCount`. This is a deliberate choice, not an oversight: this app hosts organizing communities (protests, cultural events) for a diaspora audience, where a publicly enumerable member roster is a real safety concern. The member list itself returns slim profiles only (`uid`, `displayName`, first photo, `region`) — never the full dating-card view (bio, socials, prompts).

**Access rule:** no client access.

---

## `communityPosts/{postId}` — top-level, auto-generated ID

A community's post, in one of four kinds. Top-level rather than a subcollection of `communities/{cid}` so the Discover feed can query across every community's posts without a collection-group join. Created by `POST /api/communities/me/posts` ([communityposts.py](../backend/app/services/communityposts.py) `create_post`) — refused with 403 unless the calling community's `verification` is `"verified"`.

| Field | Type | Notes |
|---|---|---|
| `communityId` | string | the owning community's uid |
| `communityName` / `communityLogoUrl` | string \| null | denormalized at creation time from the community doc, so the feed/list views never join back to `communities` per post |
| `kind` | string: `"announcement"` \| `"link"` \| `"poll"` \| `"event"` | |
| `title` | string | ≤160 chars; the poll question for `kind == "poll"`, the event name for `kind == "event"` |
| `body` | string | ≤2000 chars, optional |
| `imageUrl` | string \| null | resolved from an uploaded community photo (`photoStoragePath`, must be under the community's own `communities/{cid}/photos/` prefix) or a plain https URL — same dual shape as ads |
| `linkUrl` / `ctaLabel` | string \| null | `linkUrl` required for `kind == "link"`; optional on `kind == "event"` (e.g. a registration page); opened in the in-app browser on swipe-right, mirroring ads |
| `poll` | map `{options: [{id, label}], counts: {[id]: number}}` \| null | required for `kind == "poll"`, 2–4 options; `id`s (`"opt1"`, `"opt2"`, …) are assigned server-side and are immutable once created — votes reference them |
| `eventAt` | string \| null | required for `kind == "event"` — ISO 8601 datetime with a UTC offset (validated in [community_post.py](../backend/app/models/community_post.py), must be in the future at creation) |
| `location` | string \| null | free text, ≤200 chars, `kind == "event"` only |
| `attendeeCount` | number \| null | `kind == "event"` only; maintained transactionally alongside `rsvps` (never recomputed by scanning the subcollection), same pattern as `poll.counts` |
| `active` | boolean | `true` at creation; a community can flip it `false` to unpublish, but posts are otherwise immutable (no field lets a poll's options or an event's date change post-creation) |
| `impressions` / `clicks` | number | same convention as ads — bumped by `POST /api/posts/{postId}/events`, never sent to clients |
| `createdAt` / `updatedAt` | Timestamp | backend |

**Access rule:** no client access — everything flows through the API. `GET /api/communities/{cid}/posts` serves only `active == true` posts to everyone *except* the community itself, which also sees its own unpublished posts (so its post-manager screen can show and let it re-publish them) — see `list_posts` in [communityposts.py](../backend/app/services/communityposts.py). The Discover feed additionally drops posts from any community whose `verification` isn't `"verified"`, and any event whose `eventAt` has already passed, at read time (so un-verifying a community, or an event simply passing, both pull the post from the feed without touching the doc itself).

### `communityPosts/{postId}/votes/{uid}`

One doc per voter. `{optionId, createdAt, updatedAt}`. Written inside a Firestore transaction ([communityposts.py](../backend/app/services/communityposts.py) `_vote_transaction`) that also updates the parent post's `poll.counts` — reads both the post and the caller's existing vote before any write (Firestore's read-before-write rule), so a changed vote atomically moves the count from the old option to the new one. Re-voting for the same option is a no-op (no write at all).

**Access rule:** no client access.

### `communityPosts/{postId}/rsvps/{uid}`

One doc per attendee, `{createdAt}`. Same transactional shape as `votes` (`_rsvp_transaction` in [communityposts.py](../backend/app/services/communityposts.py)): reads the post and the caller's existing RSVP before any write, then sets or deletes the marker and moves `attendeeCount` by exactly one. RSVPing when already going, or un-RSVPing when not, is a no-op. `POST /api/posts/{postId}/rsvp` (going) and `DELETE /api/posts/{postId}/rsvp` (not going) — persons only.

**Access rule:** no client access.

---

## `news/{newsId}` — doc ID = `sha1(canonical sourceUrl)[:20]`

A summarized news card for the Discover feed, authored entirely by the `news-digest` Claude skill (`.claude/skills/news-digest/`), never by the backend or the app. The backend only reads active docs and bumps impression/click counters ([news.py](../backend/app/services/news.py)) — treat the skill's `scripts/news_admin.py` as the authoritative writer for this collection's field shapes.

| Field | Type | Notes |
|---|---|---|
| `active` | boolean | flipped `false` by the skill's prune step (default: articles older than 10 days, or beyond the 40 most-recent active) |
| `title` | string | ≤160 chars |
| `gist` | string | ≤240 chars — the only text shown on the Discover card itself |
| `summary` | string | ≤5000 chars — shown in the tap-through detail view; an original summary the skill writes, not a scrape of the source |
| `sourceUrl` | string | the original article; opened in the in-app browser on swipe-right, never the summary text itself |
| `sourceName` | string | e.g. `"Phayul"` — shown as attribution |
| `imageUrl` | string \| null | the article's share image, extracted from its page metadata |
| `publishedAt` | string \| null | ISO date/datetime from the source article |
| `order` | number | **the negative of the published epoch-seconds** — the same "lowest `order` serves first" convention as `ads`, which happens to sort newest-first here since a more recent article has a larger (less negative) epoch |
| `impressions` / `clicks` | number | bumped by `POST /api/news/{newsId}/events`, never sent to clients |
| `createdAt` / `updatedAt` | Timestamp | set by the skill at upsert time |

**Access rule:** no client access — everything flows through `GET /api/feed` (`news` key) and the events endpoint.

---

## `mail/{autoId}` — auto-generated ID

Not app data — the queue the [Trigger Email extension](https://extensions.dev/extensions/firebase/firestore-send-email) watches to actually send email. Written only by two Cloud Functions ([functions/main.py](../functions/main.py)): `on_community_created` (registration notice to the admin) and `on_community_verification_changed` (approval/rejection notice to the community) — see [COMMUNITIES.md](COMMUNITIES.md) for the full loop and the extension's one-time setup.

| Field | Type | Notes |
|---|---|---|
| `to` | array\<string\> | recipient email address(es) |
| `message` | map `{subject: string, html: string}` | `html` fields interpolate community-supplied text, so it's always HTML-escaped before interpolation (`_esc` in functions/main.py) |

**Access rule:** no client access — Cloud Functions write it, the extension reads and deletes/updates it (adds its own `delivery` status fields, which this app never reads back).

---

## Composite indexes

Firestore requires an explicit composite index whenever a query combines an equality filter with a range filter, or `array-contains` with another filter. Declared in [firestore.indexes.json](../firestore.indexes.json):

| Collection | Fields | Powers |
|---|---|---|
| `users` | `status` (==) + `location.geohash` (range) | The feed candidate query in `get_candidates` |
| `matches` | `users` (array-contains) + `status` (==) | `GET /matches` listing a user's active matches |
| `messages` (collection-group scope) | `senderId` (==) + `createdAt` (desc) | `GET /messages/sent` — all messages the caller has sent, across every match |
| `swipes` (collection-group scope) | `toUid` (==) + `action` (==) | `GET /swipes/received?action=like` — the action filter runs in the query so `limit` counts likes, not likes-plus-passes |
| `communities` | `verification` (==) + `memberCount` (desc) | `GET /communities` — the directory, verified communities biggest-first |
| `communityPosts` | `communityId` (==) + `active` (==) + `createdAt` (desc) | `GET /communities/{cid}/posts` for anyone but the community itself, **and** `GET /communities/feed`'s per-chunk `communityId in [...]` query in `list_feed_for_member` — Firestore serves `in` queries from the same composite index as the `==` case, just fanned out into one range scan per value |
| `communityPosts` | `communityId` (==) + `createdAt` (desc) | Same endpoint when the community views its own posts (includes unpublished ones, so no `active` filter) |
| `communityPosts` | `active` (==) + `createdAt` (desc) | The Discover feed's community-post query in `list_active_for_feed` |

`news` needs no composite index — `list_active` filters `active == true` in the query and sorts by `order` in memory (same as `ads.list_active`), since the active set is small (capped at ~40 by the skill's prune step).

---

## Known gaps in the current schema

These are things the schema *implies* but the code doesn't fully implement yet — flagging them here rather than in the code, since they're schema-level decisions as much as bugs:

1. **`messages.readAt` is reserved but unreachable** — the rules block all updates to a message, so no per-message read-receipt mechanism exists. Thread-level read state *is* handled now (`POST /matches/{matchId}/read` zeroes the caller's `unreadCount`), which covers the badge-count use case; per-message receipts would need a narrowly-scoped update rule (recipient-only, single-field).
2. **`preferences.distanceKm` is stored but the geohash cell size is fixed** at ~4.9km regardless of the chosen radius — the feed neither tightens nor widens with it (see the geohashing note in [TECH_STACK.md](TECH_STACK.md)).

Previously listed gaps now closed: `fcmTokens` is writable via `POST /profile/me/fcm-tokens`; blocks are enforced in both the feed and the swipe path; messaging locks when a match is unmatched; photos are capped at 6; `unreadCount` is resettable via `POST /matches/{matchId}/read`; every profile field (`gender`, `dob`, `location`, `socials`, …) is editable via `PATCH /profile/me`.
